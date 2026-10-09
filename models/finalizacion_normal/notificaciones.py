# Encola avisos individuales, añade resúmenes diarios y refleja resultados de correo en cada FC.
import logging
from datetime import datetime, time, timedelta

import pytz
from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import email_normalize

from .reglas import requiere_resumen

_logger = logging.getLogger(__name__)


class AvisoFinalizacionNormal(models.Model):
    _name = 'fuv.finalizacion.aviso'
    _description = 'Registro de correo de finalización normal'
    _order = 'id desc'

    clave = fields.Char(required=True, index=True)
    contract_id = fields.Many2one('contrataciones_fuv', ondelete='cascade', index=True)
    contract_ids = fields.Many2many(
        'contrataciones_fuv', 'fn_aviso_contrato_rel', 'aviso_id', 'contract_id',
        string='Contratos incluidos en resumen',
    )
    destinatario_id = fields.Many2one('res.users', required=True, ondelete='restrict', index=True)
    fecha = fields.Date(required=True, index=True)
    fecha_vigencia = fields.Date()
    tipo = fields.Selection([
        ('recordatorio', 'Aviso de vencimiento'), ('finalizacion', 'Finalización normal'),
        ('resumen', 'Resumen adicional'),
    ], required=True, index=True)
    mail_id = fields.Many2one('mail.mail', ondelete='restrict', readonly=True, index=True)
    resultado = fields.Selection([
        ('outgoing', 'En cola'), ('sent', 'Enviado'), ('exception', 'Fallido'), ('cancel', 'Cancelado'),
    ], default='outgoing', readonly=True)
    detalle_fallo = fields.Text(readonly=True)
    cantidad_individuales = fields.Integer(readonly=True)
    _sql_constraints = [('fn_aviso_clave_unica', 'unique(clave)', 'Este aviso ya existe.')]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('Los avisos de finalización los crea únicamente el proceso interno.'))
        contexto = {k: v for k, v in self.env.context.items() if not k.startswith('default_')}
        return super(AvisoFinalizacionNormal, self.with_context(contexto)).create(vals_list)

    def _fn_contratos(self):
        return self.contract_id | self.contract_ids

    def _fn_traza(self, contratos=None):
        self.ensure_one()
        nombres = dict(self._fields['tipo'].selection)
        # "sent" confirma aceptación del servidor SMTP, no entrega ni lectura.
        resultados = {
            'outgoing': _('Pendiente de procesamiento'),
            'sent': _('Aceptado por el servidor de correo'),
            'exception': _('Fallido'),
            'cancel': _('Cancelado'),
        }
        cuerpo = Markup(
            '<p><b>🔔 %s: %s</b></p><p>Destinatario: %s (%s). Fecha: %s.</p>'
        ) % (escape(nombres[self.tipo]), escape(resultados[self.resultado]),
             escape(self.destinatario_id.name), escape(self.mail_id.email_to), escape(str(self.fecha)))
        if self.detalle_fallo:
            cuerpo += Markup('<p>Detalle: %s</p>') % escape(self.detalle_fallo)
        for contrato in contratos if contratos is not None else self._fn_contratos():
            contrato.sudo().message_post(body=cuerpo, subtype_xmlid='mail.mt_note')

    @api.model
    def _fn_correo_valores(self, usuario, asunto, cuerpo, **extra):
        remitente = self.env.company.email or self.env.user.email
        return dict({
            'subject': asunto, 'body_html': str(cuerpo),
            'email_to': email_normalize(usuario.email),
            'email_from': remitente,
            'state': 'outgoing', 'email_cc': False,
            'recipient_ids': [(5, 0, 0)], 'attachment_ids': [(5, 0, 0)],
            'headers': False, 'message_type': 'email',
            # Estos avisos conservan mail.mail para auditoría y resultados posteriores.
            'auto_delete': False,
        }, **extra)

    @api.model
    def _fn_cron_notificaciones(self):
        if not self.env['contrataciones_fuv']._fn_esta_habilitado():
            return False
        # Repara resultados de correo si una traza no pudo escribirse durante SMTP.
        avisos = self.sudo().search([
            '|', '&', ('resultado', '=', 'outgoing'), ('mail_id.state', '!=', 'outgoing'),
            '&', ('resultado', '=', 'exception'), ('mail_id.state', '!=', 'exception'),
        ], order='id', limit=200)
        avisos.mapped('mail_id')._fn_sincronizar_resultado()
        self.sudo()._fn_preparar_resumenes()

    @api.model
    def _fn_preparar_resumenes(self):
        if not self.env['contrataciones_fuv']._fn_esta_habilitado():
            return False
        # Evita carreras entre finalización manual y el cron; un error de resumen
        # nunca revierte contratos que el lote ya finalizó correctamente.
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(162601, 2)')
        if not self.env.cr.fetchone()[0]:
            return False
        try:
            with self.env.cr.savepoint():
                self._fn_preparar_resumenes_impl()
        except Exception:
            _logger.exception('[FC-FINALIZACION-NORMAL] Error al preparar resumen adicional')
            return False
        return True

    @api.model
    def _fn_preparar_resumenes_impl(self):
        """Siempre individuales. Un resumen adicional por persona y día >20.

        Se programa al cierre del día y se actualiza mientras siga en cola,
        incorporando los contratos encontrados en los lotes siguientes.
        """
        contrato_model = self.env['contrataciones_fuv']
        hoy = contrato_model._fn_hoy()
        grupos = self.read_group([
            ('tipo', '!=', 'resumen'), ('fecha', '>=', hoy - timedelta(days=7)),
            ('resultado', 'in', ['outgoing', 'sent', 'exception']),
        ], ['destinatario_id', 'fecha'], ['destinatario_id', 'fecha:day'], lazy=False)
        parametros = self.env['ir.config_parameter'].sudo()
        zona = pytz.timezone(parametros.get_param('Matriz.fn_zona_horaria', 'America/Bogota'))
        for grupo in grupos:
            if not requiere_resumen(grupo['__count']):
                continue
            primero = self.search(grupo['__domain'], limit=1)
            if not primero:
                continue
            usuario = primero.destinatario_id
            fecha = primero.fecha
            clave = 'resumen:%s:%s' % (usuario.id, fecha)
            resumen = self.search([('clave', '=', clave)], limit=1)
            if resumen and resumen.resultado != 'outgoing':
                continue
            if resumen and resumen.cantidad_individuales == grupo['__count']:
                continue
            if not email_normalize(usuario.email):
                _logger.warning('[FC-FINALIZACION-NORMAL] No se puede resumir: correo inválido usuario_id=%s', usuario.id)
                continue
            individuales = self.search(grupo['__domain'], order='contract_id, id')
            contratos = individuales.mapped('contract_id')
            # El resumen nunca sustituye los avisos individuales ni sus trazas.
            filas = Markup('').join(
                Markup('<li><a href="%s">%s</a>: %s. Vigencia: %s.</li>') % (
                    escape(aviso.contract_id._fn_url()), escape(aviso.contract_id.display_name),
                    escape(dict(self._fields['tipo'].selection)[aviso.tipo]),
                    escape(str(aviso.fecha_vigencia)),
                ) for aviso in individuales
            )
            cuerpo = Markup(
                '<h2>🔔 Resumen diario de contratos</h2><p>Hola, %s:</p>'
                '<p>Registramos %s avisos individuales el %s. Este resumen los complementa.</p><ul>%s</ul>'
            ) % (escape(usuario.name), len(individuales), escape(str(fecha)), filas)
            if resumen:
                nuevos = contratos - resumen.contract_ids
                resumen.write({'contract_ids': [(6, 0, contratos.ids)], 'cantidad_individuales': len(individuales)})
                resumen.mail_id.write({'body_html': str(cuerpo)})
                resumen._fn_traza(nuevos)
            else:
                # Bogotá: 23:55. UTC se entrega a mail.mail sin forzar send().
                programado = zona.localize(datetime.combine(fecha, time(23, 55))).astimezone(pytz.UTC).replace(tzinfo=None)
                correo = self._fn_modelo_correo().create(self._fn_correo_valores(
                    usuario, _('🔔 Resumen adicional de contratos — %s') % fecha, cuerpo,
                    scheduled_date=programado,
                ))
                resumen = self.create({
                    'clave': clave, 'destinatario_id': usuario.id, 'fecha': fecha,
                    'tipo': 'resumen', 'mail_id': correo.id,
                    'cantidad_individuales': len(individuales),
                    'contract_ids': [(6, 0, contratos.ids)],
                })
                resumen._fn_traza()
                _logger.info('[FC-FINALIZACION-NORMAL] resumen usuario_id=%s fecha=%s avisos=%s mail_id=%s',
                             usuario.id, fecha, len(individuales), correo.id)

    @api.model
    def _fn_modelo_correo(self):
        # Ningún default enviado por un cliente puede agregar destinatarios,
        # adjuntos o un falso estado Enviado al correo técnico creado con sudo.
        contexto = {k: v for k, v in self.env.context.items() if not k.startswith('default_')}
        return self.env['mail.mail'].sudo().with_context(contexto)


class ContratoAvisosFinalizacion(models.Model):
    _inherit = 'contrataciones_fuv'

    def _fn_url(self):
        self.ensure_one()
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        return '%s/web#id=%s&model=contrataciones_fuv&view_type=form' % (base, self.id)

    def _fn_encolar_avisos(self, tipo, usuarios, origen=False, observaciones=False):
        self.ensure_one()
        if not self._fn_esta_habilitado():
            return False
        avisos = self.env['fuv.finalizacion.aviso'].sudo()
        hoy = self._fn_hoy()
        for usuario in usuarios:
            if not email_normalize(usuario.email):
                raise ValueError('Correo inválido del coordinador %s' % usuario.id)
            clave = '%s:%s:%s:%s' % (tipo, self.id, self.fn_fecha_vigente, usuario.id)
            if tipo == 'recordatorio':
                # Identifica por separado los hitos de 15, 5 y 0 días.
                clave += ':%s-dias' % (self.fn_fecha_vigente - hoy).days
            if tipo == 'finalizacion':
                clave += ':%s' % self.fn_finalizado_el
            if avisos.search_count([('clave', '=', clave)]):
                _logger.info('[FC-FINALIZACION-NORMAL] Aviso omitido: ya registrado fc_id=%s usuario_id=%s clave=%s',
                             self.id, usuario.id, clave)
                continue
            titulo = _('🔔 Contrato próximo a vencer') if tipo == 'recordatorio' else _('🔔 Contrato finalizado normalmente')
            dias_restantes = (self.fn_fecha_vigente - hoy).days
            if tipo == 'recordatorio' and dias_restantes == 0:
                descripcion = _(
                    'La vigencia termina hoy, %s. Revisa inmediatamente si corresponde gestionar un otrosí.'
                ) % self.fn_fecha_vigente
            elif tipo == 'recordatorio':
                descripcion = _(
                    'La vigencia termina el %s; faltan %s días. Revisa si corresponde gestionar un otrosí antes de esa fecha.'
                ) % (self.fn_fecha_vigente, dias_restantes)
            else:
                descripcion = _('El contrato terminó su vigencia el %s y pasó a Finalizado.') % self.fn_fecha_vigente
            if tipo == 'finalizacion':
                zona_nombre = self.env['ir.config_parameter'].sudo().get_param(
                    'Matriz.fn_zona_horaria', 'America/Bogota'
                )
                zona = pytz.timezone(zona_nombre)
                finalizado_utc = fields.Datetime.to_datetime(self.fn_finalizado_el)
                finalizado_local = pytz.UTC.localize(finalizado_utc).astimezone(zona)
                fecha_finalizacion = finalizado_local.strftime('%Y-%m-%d %H:%M')
                contratista = self.contratista_proveedor_relacionado.display_name or self.display_name
                origen_texto = _('Acción manual') if origen == 'manual' else _('Proceso automático')
                detalle = Markup(
                    '<table role="presentation" width="100%%" cellpadding="10" cellspacing="0" '
                    'style="background:#f2f7fc;border-left:4px solid #083580">'
                    '<tr><td><b>Contrato:</b> %s<br/><b>Proyecto:</b> %s<br/>'
                    '<b>Contratista:</b> %s<br/><b>Fecha de terminación original:</b> %s<br/>'
                    '<b>Vigencia efectiva utilizada:</b> %s<br/>'
                    '<b>Fecha de finalización en SGO:</b> %s (%s)<br/>'
                    '<b>Tipo de finalización:</b> Normal<br/>'
                    '<b>Ejecutada mediante:</b> %s%s</td></tr></table>'
                ) % (
                    escape(self.display_name), escape(self.proyecto_relacionado.display_name),
                    escape(contratista), escape(str(self.fecha_terminacion or '')),
                    escape(str(self.fn_fecha_vigente)), escape(fecha_finalizacion),
                    escape(zona_nombre), escape(origen_texto),
                    Markup('<br/><b>Observaciones:</b> %s') % escape(observaciones)
                    if observaciones else Markup(''),
                )
                explicacion = Markup(
                    '<p style="margin:0 0 18px">Esta finalización corresponde al '
                    'cumplimiento normal del plazo contractual. No genera acta de '
                    'liquidación, no libera ni reintegra recursos y no modifica los '
                    'valores del contrato.</p>%s'
                    '<p style="margin:18px 0">Si posteriormente se requiere un otrosí, '
                    'deberá tramitarse mediante Soporte y contar con autorización de la '
                    'Dirección FUV.</p>'
                ) % detalle
            else:
                explicacion = Markup(
                    '<table role="presentation" width="100%%" cellpadding="12" cellspacing="0" '
                    'style="background:#f2f7fc;border-left:4px solid #083580">'
                    '<tr><td><b>Contrato:</b> %s<br/><b>Proyecto:</b> %s</td></tr></table>'
                ) % (escape(self.display_name), escape(self.proyecto_relacionado.display_name))
            cuerpo = Markup(
                # Tabla y estilos inline para compatibilidad con los clientes de correo.
                '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
                'style="font-family:Arial,sans-serif;border:1px solid #dddddd;'
                'border-collapse:separate;border-radius:4px;color:#163452;background:#ffffff">'
                '<tr><td style="background:#083580;color:#ffffff;padding:18px 20px;'
                'text-align:center;font-size:21px;font-weight:bold">%s</td></tr>'
                '<tr><td style="padding:24px 20px;font-size:14px;line-height:1.6">'
                '<p style="margin:0 0 16px">Hola, <b>%s</b>:</p>'
                '<p style="margin:0 0 18px">%s</p>'
                '%s'
                '<p style="margin:24px 0;text-align:center"><a href="%s" '
                'target="_blank" style="display:inline-block;background:#083580;color:#ffffff;'
                'padding:12px 22px;border-radius:4px;text-decoration:none;font-weight:bold">'
                'Consultar contrato en SGO</a></p>'
                '<p style="border-top:1px solid #dddddd;padding-top:14px;margin:0;'
                'font-size:12px;color:#777777">Gestión Operativa SGO · '
                'Este es un mensaje automático, por favor no responder.</p>'
                '</td></tr></table>'
            ) % (escape(titulo), escape(usuario.name), escape(descripcion),
                 explicacion, escape(self._fn_url()))
            correo = avisos._fn_modelo_correo().create(avisos._fn_correo_valores(
                usuario, '%s — %s' % (titulo, self.display_name), cuerpo,
            ))
            aviso = avisos.create({
                'clave': clave, 'contract_id': self.id, 'destinatario_id': usuario.id,
                'fecha': hoy, 'fecha_vigencia': self.fn_fecha_vigente, 'tipo': tipo,
                'mail_id': correo.id,
            })
            # Mismo patrón de envío directo del FC: sin actividades ni commits manuales.
            # La traza aparece solo con el resultado final; no publica el estado "En cola".
            correo.sudo().send(auto_commit=False)
            correo._fn_sincronizar_resultado()
            _logger.info('[FC-FINALIZACION-NORMAL] individual fc_id=%s usuario_id=%s tipo=%s mail_id=%s',
                         self.id, usuario.id, tipo, correo.id)


class CorreoFinalizacionNormal(models.Model):
    _inherit = 'mail.mail'

    def write(self, vals):
        resultado = super().write(vals)
        if vals.get('state') in ('outgoing', 'cancel'):
            self._fn_sincronizar_resultado()
        return resultado

    def _postprocess_sent_message(self, success_pids, failure_reason=False, failure_type=None):
        resultado = super()._postprocess_sent_message(success_pids, failure_reason, failure_type)
        # Odoo 16 escribe una excepción PROVISIONAL antes de SMTP. Solo aquí el
        # resultado es definitivo: evita una falsa traza de fallo por cada envío.
        self.exists()._fn_sincronizar_resultado()
        return resultado

    def _fn_sincronizar_resultado(self):
        avisos = self.env['fuv.finalizacion.aviso'].sudo().search([('mail_id', 'in', self.ids)])
        for aviso in avisos:
            try:
                with self.env.cr.savepoint():
                    estado = aviso.mail_id.state
                    if estado not in ('outgoing', 'sent', 'exception', 'cancel') or aviso.resultado == estado:
                        continue
                    aviso.write({
                        'resultado': estado,
                        'detalle_fallo': aviso.mail_id.failure_reason if estado == 'exception' else False,
                    })
                    aviso._fn_traza()
            except Exception:
                # Un error de chatter no debe provocar reenviar un correo ya despachado.
                _logger.exception('[FC-FINALIZACION-NORMAL] No se pudo actualizar traza aviso_id=%s', aviso.id)

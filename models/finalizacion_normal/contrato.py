# Hereda el FC para finalizar por vigencia en lotes, sin tocar valores ni cierres con acta.
import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import email_normalize

from .reglas import ESTADOS_OTROSI_FORMALIZADOS, bloqueo_finalizacion, en_ventana_aviso, fecha_vigente, vencio

_logger = logging.getLogger(__name__)

# INTERRUPTOR GENERAL: False pausa avisos, resúmenes y finalización normal.
# Cambiar a True y reiniciar Odoo para activar; los cierres con acta no se afectan.
FINALIZACION_NORMAL_ACTIVA = True

# Corte exclusivo de finalización automática. Los avisos revisan todas las vigencias.
# None permite finalizar automáticamente cualquier vigencia; no cambia fechas del FC.

FINALIZACION_NORMAL_FECHA_INICIO = '2027-11-01'


class ContratoFinalizacionNormal(models.Model):
    _inherit = 'contrataciones_fuv'

    fn_fecha_vigente = fields.Date(
        'Vigencia efectiva', compute='_compute_fn_fecha_vigente', store=True, index=True,
        help='Fecha del FC, incluyendo ampliaciones de plazo formalizadas.',
    )
    fn_revision_fecha = fields.Date('Última revisión automática', readonly=True, copy=False, index=True)
    fn_finalizado_el = fields.Datetime('Finalizado por vigencia el', readonly=True, copy=False)
    fn_fecha_fin_finalizada = fields.Date('Vigencia al finalizar', readonly=True, copy=False)
    fn_incidencia = fields.Char('Última incidencia de vigencia', readonly=True, copy=False)
    fn_reapertura_pendiente = fields.Boolean('Excepción pendiente de otrosí', readonly=True, copy=False)
    fn_otrosi_excepcion_id = fields.Many2one('otro_si', readonly=True, copy=False, ondelete='restrict')
    fn_autorizacion_activa_id = fields.Many2one('fuv.finalizacion.autorizacion', readonly=True, copy=False, ondelete='restrict')
    fn_aviso_ids = fields.One2many('fuv.finalizacion.aviso', 'contract_id', readonly=True)

    @api.depends('fecha_iniciacion', 'fecha_terminacion')
    def _compute_duracion(self):
        """Conserva la duración visual sin enviar las alertas antiguas 30/15/5.

        El método original enviaba correo desde un campo calculado y comparaba la
        duración total del contrato, no los días restantes. Los avisos quedan a
        cargo exclusivo del cron idempotente de 15/5/0 días.
        """
        for contrato in self:
            if not contrato.fecha_iniciacion or not contrato.fecha_terminacion:
                contrato.duracion = ''
                continue
            fin_inclusivo = contrato.fecha_terminacion + timedelta(days=1)
            diferencia = relativedelta(fin_inclusivo, contrato.fecha_iniciacion)
            partes = []
            if diferencia.years:
                partes.append('%s años' % diferencia.years)
            if diferencia.months:
                partes.append('%s meses' % diferencia.months)
            if diferencia.days:
                partes.append('%s días' % diferencia.days)
            contrato.duracion = ' '.join(partes)

    @api.depends(
        'fecha_terminacion', 'otro_si_ids.estado_contratacion',
        'otro_si_ids.fecha_terminacion', 'otro_si_ids.prorroga',
        'otro_si_ids.adicion_prorroga', 'otro_si_ids.reinicio',
        'otro_si_ids.otro_si_modificatorio', 'otro_si_ids.tipo_modificacion_modificatorio',
    )
    def _compute_fn_fecha_vigente(self):
        for contrato in self:
            ampliaciones = contrato.otro_si_ids.filtered(lambda otro: (
                otro.estado_contratacion in ESTADOS_OTROSI_FORMALIZADOS
                and (otro.prorroga or otro.adicion_prorroga or otro.reinicio or (
                    otro.otro_si_modificatorio
                    and otro.tipo_modificacion_modificatorio in ('tiempo', 'ambos')
                ))
            ))
            contrato.fn_fecha_vigente = fecha_vigente(
                contrato.fecha_terminacion, ampliaciones.mapped('fecha_terminacion')
            )

    def _fn_bloquear_fila(self):
        """Comparte bloqueo con la creación de otrosí para evitar carreras."""
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute(
            'SELECT id FROM contrataciones_fuv WHERE id = %s FOR UPDATE', [self.id]
        )
        self.invalidate_recordset()
        self.otro_si_ids.invalidate_recordset()

    def _fn_coordinadores(self):
        self.ensure_one()
        grupo = self.env.ref('Matriz.group_contratacion_coordinador')
        usuarios = self.proyecto_relacionado.sudo().users_with_access | self.create_uid
        return usuarios.sudo().filtered(
            lambda usuario: usuario.active and not usuario.share and grupo in usuario.groups_id
        )

    def _fn_motivo_bloqueo(self):
        self.ensure_one()
        pendientes = self.otro_si_ids.filtered(
            lambda otro: otro.estado_contratacion not in ESTADOS_OTROSI_FORMALIZADOS
        )
        # En el sistema actual la suspensión puede conservar el FC en Contratado.
        movimientos = self.otro_si_ids.filtered(lambda otro: (
            otro.estado_contratacion in ESTADOS_OTROSI_FORMALIZADOS
            and (otro.suspension or otro.reinicio or otro.acta_de_prorroga_de_la_suspension)
        )).sorted('id', reverse=True)
        suspendido = bool(movimientos and (
            movimientos[0].suspension or movimientos[0].acta_de_prorroga_de_la_suspension
        ))
        bloqueo = bloqueo_finalizacion(
            self.estado_contratacion, self.fn_fecha_vigente, self.fn_reapertura_pendiente,
            # Cualquier acta/proceso creado excluye la finalización normal, sin importar
            # si corresponde a anulación, mutuo acuerdo o liquidación unilateral.
            bool(self.cierre_proceso_ids), bool(pendientes), suspendido,
        )
        return {
            'estado': _('El contrato no está en Contratado.'),
            'fecha': _('No hay fecha de vigencia.'),
            'excepcion': _('Soporte tiene una excepción de otrosí pendiente de formalizar.'),
            'cierre': _('El contrato ya tiene un acta o proceso de cierre contractual creado.'),
            'pendientes': _('Existen otrosíes pendientes de formalizar.'),
            'suspension': _('El último movimiento formalizado mantiene una suspensión.'),
        }.get(bloqueo, False)

    def _fn_registrar_incidencia(self, motivo):
        self.ensure_one()
        if self.fn_incidencia != motivo:
            self.sudo().write({'fn_incidencia': motivo})
            self.sudo().message_post(
                body=Markup('<p><b>⚠ Finalización normal pendiente</b></p><p>%s</p>') % escape(motivo),
                subtype_xmlid='mail.mt_note',
            )
            _logger.warning('[FC-FINALIZACION-NORMAL] fc_id=%s incidencia=%s', self.id, motivo)

    def _fn_finalizar(self, manual=False, observaciones=False):
        """Única entrada de finalización normal. No modifica fondos/documentos."""
        self.ensure_one()
        # El apagado bloquea la entrada automática; la acción manual sigue disponible.
        if not manual and not self._fn_esta_habilitado():
            raise UserError(_('La finalización normal está desactivada en el código Python.'))
        self._fn_bloquear_fila()
        hoy = self._fn_hoy()
        motivo = self._fn_motivo_bloqueo()
        if motivo or not vencio(self.fn_fecha_vigente, hoy):
            raise UserError(motivo or _('El contrato continúa vigente durante su último día.'))
        coordinadores = self._fn_coordinadores()
        if not coordinadores or any(not email_normalize(usuario.email) for usuario in coordinadores):
            raise UserError(_('Falta un coordinador válido o su correo. Corrige la información antes de finalizar.'))
        # sudo se limita a estos campos técnicos. Los writes originales siguen en la cadena.
        self.sudo().write({
            'estado_contratacion': 'finalizado',
            'fn_finalizado_el': fields.Datetime.now(),
            'fn_fecha_fin_finalizada': self.fn_fecha_vigente,
            'fn_incidencia': False,
        })
        if self.estado_contratacion != 'finalizado' or not self.fn_finalizado_el:
            raise UserError(_('La seguridad existente no permitió la escritura. Soporte debe revisar los roles del usuario.'))
        self.sudo().message_post(
            body=Markup('<p><b>✓ Contrato finalizado normalmente</b></p>'
                        '<p>Terminó la vigencia del %s. No se modificaron valores ni se reintegraron recursos.</p>%s')
                 % (
                     escape(fields.Date.to_string(self.fn_fecha_vigente)),
                     Markup('<p><b>Observaciones:</b> %s</p>') % escape(observaciones)
                     if observaciones else Markup(''),
                 ),
            subtype_xmlid='mail.mt_note',
        )
        self._fn_encolar_avisos(
            'finalizacion', coordinadores,
            origen='manual' if manual else 'automatico',
            observaciones=observaciones,
        )
        _logger.info('[FC-FINALIZACION-NORMAL] finalizado fc_id=%s vigencia=%s', self.id, self.fn_fecha_vigente)
        return True

    @api.model
    def _fn_hoy(self):
        zona = self.env['ir.config_parameter'].sudo().get_param('Matriz.fn_zona_horaria', 'America/Bogota')
        return fields.Date.context_today(self.with_context(tz=zona))

    @api.model
    def cron_verificar_alertas_fechas(self):
        # El cron histórico se redirige al proceso acotado, sin el barrido anterior.
        if not (self.env.su or self.env.user.has_group('Matriz.group_soporte')):
            return False
        return self._fn_cron_vigencia()

    @api.model
    def _fn_esta_habilitado(self):
        return FINALIZACION_NORMAL_ACTIVA

    @api.model
    def _fn_cron_vigencia(self):
        parametros = self.env['ir.config_parameter'].sudo()
        if not self._fn_esta_habilitado():
            _logger.info('[FC-FINALIZACION-NORMAL] Cron omitido: interruptor Python apagado.')
            return False
        # Una sola ejecución simultánea. No se usan commits manuales.
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(162601, 1)')
        if not self.env.cr.fetchone()[0]:
            return False
        hoy = self._fn_hoy()
        try:
            inicio = (fields.Date.to_date(FINALIZACION_NORMAL_FECHA_INICIO)
                      if FINALIZACION_NORMAL_FECHA_INICIO else None)
            lote = max(1, min(int(parametros.get_param('Matriz.fn_lote', '50')), 200))
        except (ValueError, TypeError):
            _logger.error('[FC-FINALIZACION-NORMAL] Fecha de corte Python o tamaño de lote inválido.')
            return False
        dominio = [
            ('estado_contratacion', '=', 'contratado'),
            ('fn_fecha_vigente', '!=', False),
            ('fn_fecha_vigente', '<=', hoy + timedelta(days=15)),
            '|', ('fn_revision_fecha', '=', False), ('fn_revision_fecha', '<', hoy),
        ]
        contratos = self.sudo().search(dominio, order='fn_fecha_vigente, id', limit=lote)
        _logger.info('[FC-FINALIZACION-NORMAL] hoy=%s corte_finalizacion=%s lote=%s seleccionados=%s',
                     hoy, inicio, lote, len(contratos))
        for contrato in contratos:
            try:
                with self.env.cr.savepoint():
                    contrato._fn_bloquear_fila()
                    # Revalidar después del bloqueo por cambios concurrentes.
                    if contrato.estado_contratacion != 'contratado' or contrato.fn_revision_fecha == hoy:
                        continue
                    if (not contrato.fn_fecha_vigente
                            or contrato.fn_fecha_vigente > hoy + timedelta(days=15)):
                        continue
                    contrato.write({'fn_revision_fecha': hoy})
                    motivo = contrato._fn_motivo_bloqueo()
                    coordinadores = contrato._fn_coordinadores()
                    dias = (contrato.fn_fecha_vigente - hoy).days
                    for otro in contrato.otro_si_ids.filtered(lambda registro: registro.prorroga or registro.adicion_prorroga):
                        formalizado = otro.estado_contratacion in ESTADOS_OTROSI_FORMALIZADOS
                        _logger.info(
                            '[FC-FINALIZACION-NORMAL][PRORROGA] fc_id=%s otrosi_id=%s tipo=%s '
                            'estado=%s formalizado=%s fecha_fc=%s fecha_otrosi=%s vigencia_efectiva=%s aporta_fecha_efectiva=%s',
                            contrato.id, otro.id,
                            'adicion/prorroga' if otro.adicion_prorroga else 'prorroga',
                            otro.estado_contratacion, formalizado, contrato.fecha_terminacion,
                            otro.fecha_terminacion, contrato.fn_fecha_vigente,
                            bool(formalizado and otro.fecha_terminacion
                                 and otro.fecha_terminacion == contrato.fn_fecha_vigente
                                 and (not contrato.fecha_terminacion
                                      or otro.fecha_terminacion > contrato.fecha_terminacion)),
                        )
                    _logger.info('[FC-FINALIZACION-NORMAL] revision fc_id=%s vigencia=%s dias=%s hito=%s coordinadores=%s',
                                 contrato.id, contrato.fn_fecha_vigente, dias,
                                 en_ventana_aviso(contrato.fn_fecha_vigente, hoy), coordinadores.ids)
                    if not coordinadores or any(not email_normalize(usuario.email) for usuario in coordinadores):
                        motivo = _('Falta un coordinador habilitado o su correo electrónico.')
                    elif en_ventana_aviso(contrato.fn_fecha_vigente, hoy):
                        # Avisar no depende de que el contrato ya esté listo para finalizar.
                        contrato._fn_encolar_avisos('recordatorio', coordinadores)
                    if motivo:
                        contrato._fn_registrar_incidencia(motivo)
                        continue
                    if contrato.fn_incidencia:
                        contrato.write({'fn_incidencia': False})
                    if (vencio(contrato.fn_fecha_vigente, hoy)
                            and (not inicio or contrato.fn_fecha_vigente >= inicio)):
                        contrato._fn_finalizar()
            except Exception:
                # Aísla un registro inválido, sin cancelar ni bloquear todo el lote.
                _logger.exception('[FC-FINALIZACION-NORMAL] Error fc_id=%s', contrato.id)
                contrato.invalidate_recordset()
                contrato.write({'fn_revision_fecha': hoy})
                contrato._fn_registrar_incidencia(_('Error técnico. Soporte debe revisar el log de finalización normal.'))
        self.env['fuv.finalizacion.aviso'].sudo()._fn_preparar_resumenes()
        return True

    def write(self, vals):
        # readonly en XML no es seguridad. Bloquea alteraciones técnicas vía RPC.
        campos_tecnicos = {nombre for nombre in vals if nombre.startswith('fn_')}
        if campos_tecnicos and not self.env.su:
            raise AccessError(_('Los campos automáticos de finalización no se editan directamente.'))
        for contrato in self:
            if (vals.get('estado_contratacion') == 'finalizado'
                    and contrato.estado_contratacion == 'contratado' and not self.env.su):
                raise AccessError(_('La finalización normal debe ejecutarse desde su botón, con validación de vigencia.'))
            if (contrato.fn_finalizado_el and contrato.estado_contratacion == 'finalizado'
                    and vals.get('estado_contratacion', 'finalizado') != 'finalizado'
                    and not (self.env.su and self.env.context.get('fn_reapertura_autorizada'))):
                raise AccessError(_('Reabre desde la excepción de Soporte con autorización de la Dirección de FUV.'))
        resultado = super().write(vals)
        if {'fecha_terminacion', 'estado_contratacion', 'proyecto_relacionado'} & set(vals):
            for contrato in self.filtered(lambda registro: registro.fn_revision_fecha):
                # Una corrección del plazo/estado permite revisar otra vez hoy,
                # sin esperar al día siguiente. El write técnico no entra aquí.
                contrato.sudo().write({'fn_revision_fecha': False})
        return resultado

    @api.model
    def create(self, vals):
        if any(nombre.startswith('fn_') for nombre in vals) and not self.env.su:
            raise AccessError(_('No se pueden precargar campos automáticos de finalización.'))
        contexto = {k: v for k, v in self.env.context.items() if not k.startswith('default_fn_')}
        return super(ContratoFinalizacionNormal, self.with_context(contexto)).create(vals)

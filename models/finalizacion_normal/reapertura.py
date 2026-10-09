# Conserva la autorización real de Dirección FUV y permite a Soporte una única excepción posterior.
import base64

from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class AutorizacionOtrosiPosterior(models.Model):
    _name = 'fuv.finalizacion.autorizacion'
    _description = 'Autorización de otrosí posterior a finalización normal'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name')
    contract_id = fields.Many2one(
        'contrataciones_fuv', string='Contrato', required=True, readonly=True,
        ondelete='restrict',
    )
    motivo = fields.Text(
        string='Motivo de la solicitud', required=True,
        help='Explica por qué se necesita crear un otrosí después de la finalización normal.',
    )
    director_id = fields.Many2one(
        'res.users', string='Director(a) de FUV que autoriza', required=True,
        domain=lambda self: [('active', '=', True), ('groups_id', 'in', self.env.ref('Matriz.group_contratacion_director_fuv').id)],
        help='Selecciona un usuario activo que tenga asignado el rol Dirección de FUV.',
    )
    documento_autorizacion = fields.Binary(
        string='Documento de autorización', attachment=True,
        help='Archivo opcional que respalda la solicitud o la autorización.',
    )
    documento_autorizacion_nombre = fields.Char(string='Nombre del documento')
    estado = fields.Selection([
        ('solicitada', 'Solicitada'), ('autorizada', 'Autorizada'),
        ('utilizada', 'Utilizada'), ('cancelada', 'Cancelada'),
    ], default='solicitada', required=True, readonly=True, tracking=True)
    autorizado_el = fields.Datetime(string='Fecha de autorización', readonly=True)
    autorizado_por_id = fields.Many2one(
        'res.users', string='Autorizado por', readonly=True,
        help='Se completa automáticamente cuando Dirección FUV autoriza.',
    )
    ejecutado_por_id = fields.Many2one(
        'res.users', string='Otrosí iniciado por', readonly=True,
        help='Se completa automáticamente cuando Soporte inicia el otrosí autorizado.',
    )
    ejecutado_el = fields.Datetime(string='Fecha de inicio del otrosí', readonly=True)
    otrosi_id = fields.Many2one(
        'otro_si', string='Otrosí creado', readonly=True, ondelete='restrict',
        help='Otrosí generado con esta autorización.',
    )

    @api.depends('contract_id')
    def _compute_name(self):
        for registro in self:
            registro.name = _('Otrosí posterior: %s') % registro.contract_id.display_name

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede solicitar esta excepción.'))
        for vals in vals_list:
            if set(vals) - {
                    'contract_id', 'motivo', 'director_id',
                    'documento_autorizacion', 'documento_autorizacion_nombre'}:
                raise AccessError(_('La autorización no puede venir aprobada desde el cliente.'))
            contrato = self.env['contrataciones_fuv'].browse(vals.get('contract_id')).exists()
            contrato.check_access_rights('read')
            contrato.check_access_rule('read')
            if not contrato or contrato.estado_contratacion != 'finalizado' or not contrato.fn_finalizado_el:
                raise UserError(_('Esta excepción corresponde solo a contratos finalizados normalmente.'))
            director = self.env['res.users'].sudo().browse(vals.get('director_id')).exists()
            if not director or not director.active or not director.has_group('Matriz.group_contratacion_director_fuv'):
                raise UserError(_('Selecciona una persona con el rol Dirección de FUV.'))
            if not (vals.get('motivo') or '').strip():
                raise UserError(_('Explica el motivo del otrosí posterior.'))
        # Los defaults del contexto tampoco pueden simular una aprobación.
        campos_automaticos = {'estado': 'solicitada', 'autorizado_el': False,
                             'autorizado_por_id': False, 'ejecutado_por_id': False,
                             'ejecutado_el': False, 'otrosi_id': False}
        vals_list = [dict(vals, **campos_automaticos) for vals in vals_list]
        registros = super().create(vals_list)
        for registro in registros:
            valores_mensaje = {
                'body': Markup('<p><b>⚠ Solicitud de otrosí posterior a finalización normal</b></p>'
                               '<p>Soporte: %s. Dirección de FUV: %s. Motivo: %s. Solicitud: %s.</p>')
                % (escape(self.env.user.name), escape(registro.director_id.name),
                   escape(registro.motivo), registro.id),
                'subtype_xmlid': 'mail.mt_note',
            }
            if registro.documento_autorizacion and registro.documento_autorizacion_nombre:
                valores_mensaje['attachments'] = [(
                    registro.documento_autorizacion_nombre,
                    base64.b64decode(registro.documento_autorizacion),
                )]
            registro.contract_id.sudo().message_post(**valores_mensaje)
        return registros

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_('Usa los botones de autorizar, cancelar o iniciar otrosí. El registro no se altera directamente.'))
        return super().write(vals)

    def unlink(self):
        raise AccessError(_('La autorización se conserva para trazabilidad. Puedes cancelarla.'))

    def action_autorizar(self):
        self.ensure_one()
        self.check_access_rule('read')
        if (not self.env.user.has_group('Matriz.group_contratacion_director_fuv')
                or self.director_id != self.env.user):
            raise AccessError(_('Solo la persona seleccionada de Dirección de FUV puede autorizar.'))
        self._fn_bloquear()
        if self.estado != 'solicitada':
            raise UserError(_('La solicitud ya fue atendida.'))
        self.sudo().write({
            'estado': 'autorizada', 'autorizado_por_id': self.env.uid,
            'autorizado_el': fields.Datetime.now(),
        })
        self.contract_id.sudo().message_post(
            body=Markup('<p><b>Dirección de FUV autorizó el otrosí posterior.</b></p><p>%s. Solicitud: %s.</p>')
            % (escape(self.env.user.name), self.id), subtype_xmlid='mail.mt_note',
        )
        return True

    def _fn_bloquear(self):
        self.ensure_one()
        # Siempre contrato antes de autorización: mismo orden que create de otrosí.
        self.contract_id._fn_bloquear_fila()
        self.env.cr.execute('SELECT id FROM fuv_finalizacion_autorizacion WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    def action_cancelar(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede cancelar la solicitud.'))
        self.check_access_rule('read')
        self._fn_bloquear()
        if self.estado == 'utilizada':
            raise UserError(_('La autorización ya tiene un otrosí y no puede cancelarse.'))
        contrato = self.contract_id
        contrato._fn_bloquear_fila()
        self.sudo().write({'estado': 'cancelada'})
        if (contrato.fn_reapertura_pendiente and not contrato.fn_otrosi_excepcion_id
                and contrato.fn_autorizacion_activa_id == self):
            contrato.sudo().with_context(fn_reapertura_autorizada=True).write({
                'estado_contratacion': 'finalizado', 'fn_reapertura_pendiente': False,
                'fn_autorizacion_activa_id': False,
                'fn_incidencia': False,
            })
        contrato.sudo().message_post(
            body=Markup('<p>Soporte canceló la excepción de otrosí posterior. Solicitud: %s.</p>') % self.id,
            subtype_xmlid='mail.mt_note',
        )
        return True

    def action_iniciar_otrosi(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede iniciar el otrosí autorizado.'))
        self.check_access_rule('read')
        self._fn_bloquear()
        if self.estado != 'autorizada' or not self.autorizado_por_id:
            raise UserError(_('Primero requiere autorización de la Dirección de FUV.'))
        contrato = self.contract_id
        contrato._fn_bloquear_fila()
        if self.otrosi_id or contrato.fn_otrosi_excepcion_id or contrato.cierre_proceso_activo_id:
            raise UserError(_('Existe un trámite relacionado. Revisa la trazabilidad.'))
        retomando = contrato.fn_reapertura_pendiente and contrato.fn_autorizacion_activa_id == self
        if (contrato.estado_contratacion != 'finalizado' and not retomando) or not contrato.fn_finalizado_el:
            raise UserError(_('El contrato debe estar finalizado normalmente.'))
        # Reabre solo el estado. No concede permisos sobre otros documentos o montos.
        if not retomando:
            contrato.sudo().with_context(fn_reapertura_autorizada=True).write({
                'estado_contratacion': 'contratado', 'fn_reapertura_pendiente': True,
                'fn_revision_fecha': False, 'fn_autorizacion_activa_id': self.id,
            })
            if not contrato.fn_reapertura_pendiente or contrato.estado_contratacion != 'contratado':
                raise UserError(_('La seguridad existente no permitió reabrir. Revisa los roles de Soporte.'))
            contrato.sudo().message_post(
                body=Markup('<p><b>⚠ Soporte habilitó un otrosí posterior a finalización.</b></p>'
                            '<p>Ejecutado por: %s. Autorizó: %s. Solicitud: %s. Motivo: %s.</p>')
                % (escape(self.env.user.name), escape(self.autorizado_por_id.name), self.id, escape(self.motivo)),
                subtype_xmlid='mail.mt_note',
            )
        return {
            'type': 'ir.actions.act_window', 'name': _('Otrosí autorizado posterior a finalización'),
            'res_model': 'otro_si', 'view_mode': 'form', 'target': 'current',
            'context': {'default_contratacion_fuv_en_id': contrato.id, 'fn_autorizacion_id': self.id},
        }


class ContratoExcepcionFinalizacion(models.Model):
    _inherit = 'contrataciones_fuv'

    def action_fn_solicitar_otrosi(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede solicitar un otrosí posterior.'))
        if self.estado_contratacion != 'finalizado' or not self.fn_finalizado_el:
            raise UserError(_('El contrato no está finalizado normalmente.'))
        return {
            'type': 'ir.actions.act_window', 'name': _('Autorización'),
            'res_model': 'fuv.finalizacion.autorizacion', 'view_mode': 'form', 'target': 'new',
            'context': {'default_contract_id': self.id},
        }

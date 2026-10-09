from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

from .constantes import ESTADO_SOLICITUD_POLIZA_SELECTION, EstadoSolicitudPoliza


class SolicitudPolizaSoporteEstado(models.Model):
    _inherit = 'contrataciones_fuv.solicitud.poliza'

    def action_soporte_cambiar_estado(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_('Solo Soporte puede cambiar manualmente el estado de pólizas.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Soporte: cambiar estado de pólizas'),
            'res_model': 'contrataciones_fuv.poliza.estado.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_poliza_soporte_estado_wizard').id,
            'target': 'new',
            'context': {
                'default_solicitud_id': self.id,
                'default_estado_capturado': self.estado,
            },
        }


class PolizaSoporteEstadoWizard(models.TransientModel):
    _name = 'contrataciones_fuv.poliza.estado.wizard'
    _description = 'Cambio administrativo del estado de pólizas'

    solicitud_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Solicitud de pólizas',
        required=True,
        readonly=True,
    )
    estado_capturado = fields.Selection(
        ESTADO_SOLICITUD_POLIZA_SELECTION,
        string='Estado al abrir',
        required=True,
        readonly=True,
    )
    estado_nuevo = fields.Selection(
        ESTADO_SOLICITUD_POLIZA_SELECTION,
        string='Nuevo estado',
        required=True,
    )
    motivo = fields.Text(string='Motivo del cambio', required=True)

    def action_aplicar(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_('Solo Soporte puede cambiar manualmente el estado de pólizas.'))
        solicitud = self.solicitud_id.exists()
        if not solicitud:
            raise UserError(_('La solicitud de pólizas ya no existe.'))
        motivo = (self.motivo or '').strip()
        if not motivo:
            raise UserError(_('Indique el motivo del cambio de estado.'))
        etiquetas = dict(ESTADO_SOLICITUD_POLIZA_SELECTION)
        if self.estado_nuevo not in etiquetas:
            raise UserError(_('Seleccione un estado válido de pólizas.'))

        self.env.cr.execute(
            'SELECT id FROM "%s" WHERE id = %%s FOR UPDATE' % solicitud._table,
            (solicitud.id,),
        )
        if not self.env.cr.fetchone():
            raise UserError(_('La solicitud de pólizas ya no existe.'))
        solicitud.invalidate_recordset(['estado'])
        if solicitud.estado != self.estado_capturado:
            raise UserError(_(
                'El estado cambió mientras el asistente estaba abierto. '
                'Ciérrelo y vuelva a abrirlo.'
            ))
        if solicitud.estado == self.estado_nuevo:
            raise UserError(_('La solicitud ya se encuentra en ese estado.'))

        estado_anterior = solicitud.estado
        valores = {'estado': self.estado_nuevo}
        if self.estado_nuevo == EstadoSolicitudPoliza.FINALIZADA.value:
            # Un cierre administrativo no debe dejar vigente un enlace público.
            valores.update({'portal_token': False, 'portal_token_expira': False})
        solicitud.sudo().write(valores)
        solicitud.sudo().message_post(
            body=Markup(
                '<p><strong>Estado de pólizas cambiado manualmente por Soporte.</strong></p>'
                '<p><strong>Anterior:</strong> %s<br/>'
                '<strong>Nuevo:</strong> %s<br/>'
                '<strong>Responsable:</strong> %s<br/>'
                '<strong>Motivo:</strong> %s</p>'
            ) % (
                escape(etiquetas.get(estado_anterior, estado_anterior or _('Sin estado'))),
                escape(etiquetas[self.estado_nuevo]),
                escape(self.env.user.name),
                escape(motivo),
            ),
            subtype_xmlid='mail.mt_note',
            author_id=self.env.user.partner_id.id,
        )
        return {'type': 'ir.actions.act_window_close'}

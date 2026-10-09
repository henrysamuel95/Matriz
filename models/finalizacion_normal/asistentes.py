# Integra la finalización normal en el asistente existente de cierres contractuales.
from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class CierreWizardFinalizacionNormal(models.TransientModel):
    _inherit = 'contrataciones_fuv.close.wizard'

    tipo_acta = fields.Selection(
        selection_add=[('finalizar', 'Finalizar normalmente')],
        ondelete={'finalizar': 'cascade'},
    )
    # Las razones pertenecen a solicitudes con acta, no a la finalización normal.
    razones_solicitud = fields.Text(required=False)
    observaciones_finalizacion = fields.Text(
        string='Observaciones de finalización',
        help='Comentario opcional que quedará en la trazabilidad del contrato.',
    )

    def action_confirmar_cierre(self):
        self.ensure_one()
        if self.tipo_acta != 'finalizar':
            if not (self.razones_solicitud or '').strip():
                raise UserError(_('Debes registrar las razones de la solicitud de cierre.'))
            return super().action_confirmar_cierre()
        if not (self.env.user.has_group('Matriz.group_contratacion_coordinador')
                or self.env.user.has_group('Matriz.group_soporte')):
            raise AccessError(_('Solo Coordinación o Soporte pueden finalizar normalmente el contrato.'))
        contrato = self.contract_id
        contrato.check_access_rights('write')
        contrato.check_access_rule('write')
        # Reutiliza las validaciones de estado, vigencia, otrosíes y actas.
        contrato._fn_finalizar(
            manual=True,
            observaciones=(self.observaciones_finalizacion or '').strip(),
        )
        self.env['fuv.finalizacion.aviso'].sudo()._fn_preparar_resumenes()
        return {
            'type': 'ir.actions.act_window', 'name': _('Contrato finalizado'),
            'res_model': 'contrataciones_fuv', 'view_mode': 'form',
            'res_id': contrato.id, 'target': 'current',
        }


class ContratoSolicitudFinalizacion(models.Model):
    _inherit = 'contrataciones_fuv'

    def action_close_contract(self):
        # El formulario original contiene las tres actas y la finalización normal.
        return super().action_close_contract()

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html_escape


ESTADOS_ANTERIORES = [
    ('coordinador', 'Coordinador'),
    ('financiera', 'Financiera'),
    ('juridico', 'Jurídico'),
    ('recursos_humanos', 'Recursos humanos'),
    ('contratado', 'Contratado'),
    ('suspendido', 'Suspendido'),
    ('finalizado', 'Finalizado'),
    ('liquidado', 'Liquidado'),
    ('anulado', 'Anulado'),
]

ESTADOS_NUEVOS = [
    ('coordinador', 'Coordinador'),
    ('ventanilla', 'Ventanilla'),
    ('presupuesto', 'Presupuesto'),
    ('director_juridico', 'Dir. Jurídico'),
    ('juridico_1', 'Jurídico 1 - Revisión'),
    ('recursos_humanos', 'Recursos humanos'),
    ('juridico_2', 'Jurídico 2 - Contrato'),
    ('firma', 'Coord. - Subir contrato firmado'),
    ('polizas', 'Pólizas (Eventual)'),
    ('contratado', 'Contratado'),
    ('suspendido', 'Suspendido'),
    ('finalizado', 'Finalizado'),
    ('liquidado', 'Liquidado'),
    ('anulado', 'Anulado'),
]

ESTADOS_NUEVOS_JURIDICA = [
    item for item in ESTADOS_NUEVOS
    if item[0] not in ('recursos_humanos', 'juridico_2')
]


class ContratacionesFuvSoporteEstado(models.Model):
    _inherit = 'contrataciones_fuv'

    def action_soporte_cambiar_estado(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede cambiar manualmente el estado.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Soporte - Cambiar estado'),
            'res_model': 'contrataciones_fuv.soporte.estado.wizard',
            'view_mode': 'form',
            'views': [(self.env.ref('Matriz.view_contrataciones_fuv_soporte_estado_wizard_form').id, 'form')],
            'target': 'new',
            'context': {'default_contratacion_id': self.id},
        }


class ContratacionesFuvSoporteEstadoWizard(models.TransientModel):
    _name = 'contrataciones_fuv.soporte.estado.wizard'
    _description = 'Cambio administrativo de estado de contratación FUV'

    contratacion_id = fields.Many2one(
        'contrataciones_fuv', string='Contratación', required=True, readonly=True,
        ondelete='cascade',
    )
    flujo_nuevo = fields.Boolean(related='contratacion_id.usa_flujo_contratacion_nuevo')
    persona_juridica = fields.Boolean(compute='_compute_persona_juridica')
    estado_anterior = fields.Selection(ESTADOS_ANTERIORES, string='Nuevo estado (flujo anterior)')
    estado_nuevo_natural = fields.Selection(ESTADOS_NUEVOS, string='Nuevo estado (persona natural)')
    estado_nuevo_juridica = fields.Selection(ESTADOS_NUEVOS_JURIDICA, string='Nuevo estado (persona jurídica)')

    @api.depends('contratacion_id.select_persona')
    def _compute_persona_juridica(self):
        for wizard in self:
            wizard.persona_juridica = wizard.contratacion_id.select_persona == 'persona_juridica'

    def action_aplicar(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede cambiar manualmente el estado.'))

        contratacion = self.contratacion_id.exists()
        if not contratacion:
            raise UserError(_('La contratación ya no existe.'))

        if contratacion.usa_flujo_contratacion_nuevo:
            opciones = ESTADOS_NUEVOS_JURIDICA if contratacion.select_persona == 'persona_juridica' else ESTADOS_NUEVOS
            estado_nuevo = self.estado_nuevo_juridica if contratacion.select_persona == 'persona_juridica' else self.estado_nuevo_natural
        else:
            opciones = ESTADOS_ANTERIORES
            estado_nuevo = self.estado_anterior

        if estado_nuevo not in dict(opciones):
            raise UserError(_('Seleccione un estado válido para este flujo de contratación.'))
        estado_actual = contratacion.estado_contratacion
        if estado_actual == estado_nuevo:
            raise UserError(_('La contratación ya se encuentra en el estado seleccionado.'))

        etiquetas = dict(contratacion._fields['estado_contratacion']._description_selection(self.env))
        contratacion.with_context(soporte_cambio_estado_fc=True).write({
            'estado_contratacion': estado_nuevo,
        })
        contratacion.message_post(
            body=(
                '<p><strong>Cambio administrativo de estado - Soporte</strong></p>'
                '<p><strong>%s</strong> cambió manualmente la contratación de '
                '<strong>%s</strong> a <strong>%s</strong>.</p>'
            ) % (
                html_escape(self.env.user.name),
                html_escape(etiquetas.get(estado_actual, estado_actual or '')),
                html_escape(etiquetas.get(estado_nuevo, estado_nuevo)),
            ),
            subtype_xmlid='mail.mt_note',
        )
        return {'type': 'ir.actions.act_window_close'}

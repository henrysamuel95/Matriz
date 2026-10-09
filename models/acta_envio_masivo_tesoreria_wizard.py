from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class ActaEnvioMasivoTesoreriaWizard(models.TransientModel):
    _name = 'acta.envio.masivo.tesoreria.wizard'
    _description = 'Enviar masivamente cuentas de cobro a Tesorería'

    acta_ids = fields.Many2many(
        'acta_entrega_documentos',
        'acta_envio_tesoreria_rel',
        'wizard_id',
        'acta_id',
        string='Cuentas de cobro',
        required=True,
    )
    cantidad = fields.Integer(
        string='Registros seleccionados',
        compute='_compute_cantidad',
    )
    line_ids = fields.One2many(
        'acta.envio.masivo.tesoreria.wizard.line',
        'wizard_id',
        string='Cuentas seleccionadas',
    )
    usuarios_tesoreria_global = fields.Many2many(
        'res.users',
        'acta_envio_masivo_tesoreria_global_rel',
        'wizard_id',
        'user_id',
        string='Asignar a todas',
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_tesoreria').id])],
    )
    observaciones_tesoreria = fields.Text(string='Observaciones generales')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        acta_ids = self.env.context.get('active_ids') or []
        if not acta_ids:
            default_acta_ids = self.env.context.get('default_acta_ids') or []
            for command in default_acta_ids:
                if isinstance(command, (list, tuple)) and len(command) >= 3 and command[0] == 6:
                    acta_ids = command[2]
                    break

        if acta_ids:
            actas = self.env['acta_entrega_documentos'].browse(acta_ids).exists()
            res['acta_ids'] = [(6, 0, actas.ids)]
            res['line_ids'] = [
                (0, 0, self._prepare_line_vals(acta))
                for acta in actas
            ]
        return res

    def _prepare_line_vals(self, acta):
        return {
            'acta_id': acta.id,
            'seleccionado': True,
            'usuarios_tesoreria': [(6, 0, acta.usuarios_tesoreria.ids)],
        }

    @api.depends('line_ids')
    def _compute_cantidad(self):
        for wizard in self:
            wizard.cantidad = len(wizard.line_ids)

    def action_aplicar_tesoreria_a_todas(self):
        self.ensure_one()
        if not self.usuarios_tesoreria_global:
            raise UserError(_("Seleccione primero el usuario de Tesorería que desea aplicar."))
        for line in self.line_ids:
            line.usuarios_tesoreria = [(6, 0, self.usuarios_tesoreria_global.ids)]
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _reconstruir_lineas_si_perdieron_acta(self):
        self.ensure_one()
        if self.line_ids.filtered('acta_id') or not self.acta_ids:
            return
        self.line_ids.unlink()
        for acta in self.acta_ids.exists():
            self.env['acta.envio.masivo.tesoreria.wizard.line'].create(dict(
                self._prepare_line_vals(acta),
                wizard_id=self.id,
            ))

    def action_enviar(self):
        self.ensure_one()

        if not self.env.user.has_group('Matriz.group_tesoreria_auxiliar') and not self.env.su:
            raise AccessError(_("Solo el Auxiliar de Tesorería puede enviar cuentas masivamente a Tesorería."))

        self._reconstruir_lineas_si_perdieron_acta()

        lineas = self.line_ids.filtered(lambda line: line.seleccionado and line.acta_id)
        if not lineas:
            raise UserError(_("Seleccione al menos una cuenta de cobro para enviar."))

        actas = lineas.mapped('acta_id').exists()
        registros_invalidos = actas.filtered(lambda record: record.estado != 'au_tesoreria')
        if registros_invalidos:
            nombres = ', '.join((registros_invalidos.mapped('display_name') or registros_invalidos.mapped('name'))[:5])
            raise UserError(_(
                "Solo puede enviar a Tesorería cuentas que estén en Auxiliar de Tesorería. "
                "Revise estos registros: %s"
            ) % nombres)

        if self.usuarios_tesoreria_global:
            for line in lineas:
                line.usuarios_tesoreria = [(6, 0, self.usuarios_tesoreria_global.ids)]

        lineas_sin_tesoreria = lineas.filtered(lambda line: not line.usuarios_tesoreria)
        if lineas_sin_tesoreria:
            nombres = ', '.join(lineas_sin_tesoreria.mapped('acta_id.display_name')[:5])
            raise UserError(_("Debe asignar Tesorería en cada cuenta seleccionada. Revise: %s") % nombres)

        observaciones = (self.observaciones_tesoreria or '').strip()
        for line in lineas:
            valores = {
                'usuarios_tesoreria': [(6, 0, line.usuarios_tesoreria.ids)],
            }
            if observaciones:
                valores['observaciones_tesoreria'] = observaciones
            line.acta_id.write(valores)
            line.acta_id.enviar_a_tesoreria()

        return {'type': 'ir.actions.act_window_close'}


class ActaEnvioMasivoTesoreriaWizardLine(models.TransientModel):
    _name = 'acta.envio.masivo.tesoreria.wizard.line'
    _description = 'Línea de envío masivo a Tesorería'

    wizard_id = fields.Many2one(
        'acta.envio.masivo.tesoreria.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    seleccionado = fields.Boolean(string='Enviar', default=True)
    acta_id = fields.Many2one(
        'acta_entrega_documentos',
        string='Cuenta de cobro',
        readonly=True,
    )
    nombre_contratista = fields.Char(
        string='Contratista',
        related='acta_id.nombre_contratista',
        readonly=True,
    )
    proyecto_relacionado = fields.Many2one(
        'contratacion',
        string='Proyecto',
        related='acta_id.proyecto_relacionado',
        readonly=True,
    )
    pago = fields.Integer(
        string='Pago N°',
        related='acta_id.pago',
        readonly=True,
    )
    estado = fields.Selection(
        string='Estado',
        related='acta_id.estado',
        readonly=True,
    )
    usuarios_tesoreria = fields.Many2many(
        'res.users',
        'acta_envio_masivo_tesoreria_line_user_rel',
        'line_id',
        'user_id',
        string='Asignar Tesorería',
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_tesoreria').id])],
    )

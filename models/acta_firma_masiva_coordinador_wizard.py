from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class ActaFirmaMasivaCoordinadorWizard(models.TransientModel):
    _name = 'acta.firma.masiva.coordinador.wizard'
    _description = 'Firma masiva del Coordinador en cuentas de cobro'

    acta_ids = fields.Many2many(
        'acta_entrega_documentos',
        'acta_firma_coord_rel',
        'wizard_id',
        'acta_id',
        string='Cuentas de cobro',
        required=True,
    )
    cantidad = fields.Integer(
        string='Registros seleccionados',
        compute='_compute_cantidad',
    )
    coordinador_nombre_id = fields.Many2one(
        'res.users',
        string='Nombre del firmante',
        required=True,
        default=lambda self: self.env.user,
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_contratacion_coordinador').id])],
    )
    cargo_global_id = fields.Many2one(
        'cargo_tag',
        string='Cargo a aplicar',
    )
    usuarios_supervision_global = fields.Many2many(
        'res.users',
        'acta_firma_coord_vent_global_rel',
        'wizard_id',
        'user_id',
        string='Ventanilla a aplicar',
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_contratacion_ventanilla_unica').id])],
    )
    fecha_firma = fields.Date(
        string='Fecha de firma',
        default=fields.Date.context_today,
        required=True,
    )
    observaciones_ventanilla = fields.Text(
        string='Observaciones generales para Ventanilla',
    )
    line_ids = fields.One2many(
        'acta.firma.masiva.coordinador.wizard.line',
        'wizard_id',
        string='Cuentas seleccionadas',
    )

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

    def _get_certificacion(self, acta):
        certificacion = acta.relcion_cer_conjunto_ids[:1]
        if certificacion:
            return certificacion
        domain = [('relcion', '=', acta.id)]
        if acta.cuenta_id:
            domain = ['|'] + domain + [('relcion_cobros', '=', acta.cuenta_id.id)]
        certificacion = self.env['certificacion_cumplimiento'].search(domain, order='id desc', limit=1)
        if certificacion and certificacion.relcion != acta:
            certificacion.sudo().with_context(
                skip_auto_certificacion_conjunta=True,
                skip_certificacion_guardado=True,
            ).write({'relcion': acta.id})
        return certificacion

    def _prepare_line_vals(self, acta):
        certificacion = self._get_certificacion(acta)
        falta_certificacion = not (certificacion and certificacion.firmado)
        falta_vobo = acta.clase_persona == 'persona_natural' and not acta.informe_vobo_coordinador
        datos_ok = bool(
            certificacion
            and (certificacion.actividades_especificas or '').strip()
            and certificacion.porcentaje_objetivos
        )
        return {
            'acta_id': acta.id,
            'seleccionado': bool(falta_certificacion or falta_vobo),
            'cargo_id': certificacion.cargo2.id if certificacion and certificacion.cargo2 else False,
            'firma_certificacion': bool(falta_certificacion),
            'firma_vobo': bool(falta_vobo),
            'datos_certificacion_ok': datos_ok,
            'usuarios_supervision': [(6, 0, acta.usuarios_supervision.ids)],
        }

    @api.depends('line_ids')
    def _compute_cantidad(self):
        for wizard in self:
            wizard.cantidad = len(wizard.line_ids)

    def _reconstruir_lineas_si_perdieron_acta(self):
        self.ensure_one()
        if self.line_ids.filtered('acta_id') or not self.acta_ids:
            return
        self.line_ids.unlink()
        for acta in self.acta_ids.exists():
            self.env['acta.firma.masiva.coordinador.wizard.line'].create(dict(
                self._prepare_line_vals(acta),
                wizard_id=self.id,
            ))

    def action_aplicar_cargo_a_todas(self):
        self.ensure_one()
        if not self.cargo_global_id:
            raise UserError(_("Seleccione primero el cargo que desea aplicar."))
        for line in self.line_ids.filtered('acta_id'):
            line.cargo_id = self.cargo_global_id
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_aplicar_ventanilla_a_todas(self):
        self.ensure_one()
        if not self.usuarios_supervision_global:
            raise UserError(_("Seleccione primero el usuario de Ventanilla que desea aplicar."))
        for line in self.line_ids.filtered('acta_id'):
            line.usuarios_supervision = [(6, 0, self.usuarios_supervision_global.ids)]
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_firmar(self):
        self.ensure_one()
        usuario = self.env.user
        firmante = self.coordinador_nombre_id

        if not usuario.has_group('Matriz.group_contratacion_coordinador') and not self.env.su:
            raise AccessError(_("Solo el Coordinador puede firmar masivamente estas cuentas."))
        if not firmante:
            raise UserError(_("Seleccione el nombre del Coordinador que firma."))

        self._reconstruir_lineas_si_perdieron_acta()

        lineas = self.line_ids.filtered(lambda line: line.seleccionado and line.acta_id)
        if not lineas:
            raise UserError(_("Seleccione al menos una cuenta para firmar."))

        invalidas = lineas.filtered(
            lambda line: (
                line.acta_id.estado != 'coordinador'
                or (not self.env.su and usuario not in line.acta_id.sudo().usuarios_contratacion)
            )
        )
        if invalidas:
            nombres = ', '.join(invalidas.mapped('acta_id.display_name')[:5])
            raise UserError(_(
                "Solo puede firmar cuentas en estado Coordinador y asignadas a su usuario. "
                "Revise: %s"
            ) % nombres)

        for line in lineas:
            if self.cargo_global_id:
                line.cargo_id = self.cargo_global_id
            if self.usuarios_supervision_global:
                line.usuarios_supervision = [(6, 0, self.usuarios_supervision_global.ids)]

            certificacion = self._get_certificacion(line.acta_id)
            line.datos_certificacion_ok = bool(
                certificacion
                and (certificacion.actividades_especificas or '').strip()
                and certificacion.porcentaje_objetivos
            )

        sin_datos = lineas.filtered(lambda line: line.firma_certificacion and not line.datos_certificacion_ok)
        if sin_datos:
            nombres = ', '.join(sin_datos.mapped('acta_id.display_name')[:5])
            raise UserError(_(
                "Estas cuentas aún no tienen completa la certificación conjunta "
                "(actividad a cargo y porcentaje). Revise: %s"
            ) % nombres)

        sin_cargo = lineas.filtered(lambda line: line.firma_certificacion and not line.cargo_id)
        if sin_cargo:
            nombres = ', '.join(sin_cargo.mapped('acta_id.display_name')[:5])
            raise UserError(_("Debe indicar cargo para firmar la certificación. Revise: %s") % nombres)

        sin_ventanilla = lineas.filtered(lambda line: not line.usuarios_supervision)
        if sin_ventanilla:
            nombres = ', '.join(sin_ventanilla.mapped('acta_id.display_name')[:5])
            raise UserError(_("Debe asignar Ventanilla en cada cuenta seleccionada antes de enviar. Revise: %s") % nombres)

        observaciones_ventanilla = (self.observaciones_ventanilla or '').strip()
        for line in lineas:
            acta = line.acta_id
            certificacion = self._get_certificacion(acta)
            if not certificacion:
                raise UserError(_("La cuenta %s no tiene certificación conjunta creada.") % acta.display_name)

            if line.firma_certificacion and not certificacion.firmado:
                certificacion.write({
                    'coordinador_nombre_id': firmante.id,
                    'cargo2': line.cargo_id.id,
                    'fecha_coordinador': self.fecha_firma,
                    'firmado': True,
                })

            if line.firma_vobo and acta.clase_persona == 'persona_natural' and not acta.informe_vobo_coordinador:
                acta.write({'informe_vobo_coordinador': True})

            valores_acta = {
                'usuarios_supervision': [(6, 0, line.usuarios_supervision.ids)],
            }
            if observaciones_ventanilla:
                valores_acta['observaciones_ventanilla'] = observaciones_ventanilla
            acta.write(valores_acta)
            acta.enviar_a_ventanilla()

        return {'type': 'ir.actions.act_window_close'}


class ActaFirmaMasivaCoordinadorWizardLine(models.TransientModel):
    _name = 'acta.firma.masiva.coordinador.wizard.line'
    _description = 'Línea de firma masiva del Coordinador'

    wizard_id = fields.Many2one(
        'acta.firma.masiva.coordinador.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    seleccionado = fields.Boolean(string='Firmar', default=True)
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
    firma_certificacion = fields.Boolean(string='Firmar certificación')
    firma_vobo = fields.Boolean(string='Firmar Vo.Bo.')
    datos_certificacion_ok = fields.Boolean(string='Certificación completa', readonly=True)
    cargo_id = fields.Many2one('cargo_tag', string='Cargo')
    usuarios_supervision = fields.Many2many(
        'res.users',
        'acta_firma_coord_line_vent_rel',
        'line_id',
        'user_id',
        string='Asignar Ventanilla',
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_contratacion_ventanilla_unica').id])],
    )

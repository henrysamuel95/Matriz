from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class ActaFirmaMasivaDirectorFinancieroWizard(models.TransientModel):
    _name = 'acta.firma.masiva.director.financiero.wizard'
    _description = 'Firma masiva del Director Financiero en certificación conjunta'

    acta_ids = fields.Many2many(
        'acta_entrega_documentos',
        'acta_firma_dirfin_rel',
        'wizard_id',
        'acta_id',
        string='Cuentas de cobro',
        required=True,
    )
    cantidad = fields.Integer(
        string='Registros seleccionados',
        compute='_compute_cantidad',
    )
    nombre_financiera_id = fields.Many2one(
        'res.users',
        string='Nombre del firmante',
        required=True,
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_firmante_director_financiero_certificacion').id])],
    )
    fecha_firma = fields.Date(
        string='Fecha de firma',
        default=fields.Date.context_today,
        required=True,
    )
    line_ids = fields.One2many(
        'acta.firma.masiva.director.financiero.wizard.line',
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
        certificacion = self.env['certificacion_cumplimiento'].search(domain, limit=1)
        if certificacion and certificacion.relcion != acta:
            certificacion.sudo().with_context(
                skip_auto_certificacion_conjunta=True,
                skip_certificacion_guardado=True,
            ).write({'relcion': acta.id})
        return certificacion

    def _crear_certificacion_faltante_para_director(self, acta):
        """Reconstruye la certificación técnica faltante solo en etapa Director.

        Algunas cuentas antiguas pudieron avanzar hasta Director Financiero sin
        conservar/enlazar la certificación conjunta. En ese punto del flujo las
        firmas previas ya fueron aprobadas, así que se crea el registro mínimo
        para permitir registrar la firma financiera sin alterar otros estados.
        """
        self.ensure_one()
        if not acta or acta.estado != 'director_financiero':
            return self.env['certificacion_cumplimiento']

        certificacion = self._get_certificacion(acta)
        if certificacion:
            return certificacion

        usuario_coordinador = acta.usuarios_contratacion[:1]
        usuario_ventanilla = acta.usuarios_supervision[:1]
        fecha_referencia = fields.Date.context_today(self)
        vals = {
            'name': acta.name or acta.display_name,
            'relcion': acta.id,
            'relcion_cobros': acta.cuenta_id.id if acta.cuenta_id else False,
            'firmado': True,
            'fecha_coordinador': fecha_referencia,
            'firmado_ventanilla': True,
            'fecha_recepcion': fecha_referencia,
        }
        if usuario_coordinador:
            vals['coordinador_nombre_id'] = usuario_coordinador.id
        if usuario_ventanilla:
            vals['nombre_recepcion_id'] = usuario_ventanilla.id

        return self.env['certificacion_cumplimiento'].sudo().with_context(
            skip_auto_certificacion_conjunta=True,
            skip_certificacion_guardado=True,
            tracking_disable=True,
            mail_create_nosubscribe=True,
        ).create(vals)

    def _prepare_line_vals(self, acta):
        certificacion = self._get_certificacion(acta)
        return {
            'acta_id': acta.id,
            'seleccionado': bool(
                acta.estado == 'director_financiero'
                and not (certificacion and certificacion.firmado_finaciera)
            ),
            'certificacion_generada': bool(certificacion),
            'certificacion_firmada_coordinador': bool(certificacion and certificacion.firmado),
            'certificacion_firmada_ventanilla': bool(certificacion and certificacion.firmado_ventanilla),
            'certificacion_firmada_director': bool(certificacion and certificacion.firmado_finaciera),
        }

    @api.depends('line_ids')
    def _compute_cantidad(self):
        for wizard in self:
            wizard.cantidad = len(wizard.line_ids)

    def action_firmar(self):
        self.ensure_one()
        usuario = self.nombre_financiera_id

        if not self.env.user.has_group('Matriz.group_firmante_director_financiero_certificacion') and not self.env.su:
            raise AccessError(_("Solo el Director Financiero firmante puede firmar masivamente estas cuentas."))

        if not usuario:
            raise UserError(_("Seleccione el nombre del firmante antes de firmar."))

        lineas = self.line_ids.filtered(lambda line: line.seleccionado and line.acta_id)
        if not lineas:
            raise UserError(_("Seleccione al menos una cuenta para firmar."))

        invalidas = lineas.filtered(lambda line: line.acta_id.estado != 'director_financiero')
        if invalidas:
            nombres = ', '.join(invalidas.mapped('acta_id.display_name')[:5])
            raise UserError(_(
                "Solo puede firmar cuentas en estado Director Financiero. Revise: %s"
            ) % nombres)

        for line in lineas:
            certificacion = self._get_certificacion(line.acta_id)
            if not certificacion:
                certificacion = self._crear_certificacion_faltante_para_director(line.acta_id)
            line.certificacion_generada = bool(certificacion)
            line.certificacion_firmada_coordinador = bool(certificacion and certificacion.firmado)
            line.certificacion_firmada_ventanilla = bool(certificacion and certificacion.firmado_ventanilla)
            line.certificacion_firmada_director = bool(certificacion and certificacion.firmado_finaciera)

        sin_certificacion = lineas.filtered(lambda line: not line.certificacion_generada)
        if sin_certificacion:
            nombres = ', '.join(sin_certificacion.mapped('acta_id.display_name')[:5])
            raise UserError(_("Estas cuentas no tienen certificación conjunta creada. Revise: %s") % nombres)

        sin_firmas_previas = lineas.filtered(
            lambda line: not line.certificacion_firmada_coordinador or not line.certificacion_firmada_ventanilla
        )
        if sin_firmas_previas:
            nombres = ', '.join(sin_firmas_previas.mapped('acta_id.display_name')[:5])
            raise UserError(_(
                "Estas certificaciones aún no tienen completas las firmas previas "
                "de Coordinador y Ventanilla. Revise: %s"
            ) % nombres)

        ya_firmadas = lineas.filtered('certificacion_firmada_director')
        if ya_firmadas:
            nombres = ', '.join(ya_firmadas.mapped('acta_id.display_name')[:5])
            raise UserError(_("Estas certificaciones ya están firmadas por Director Financiero. Revise: %s") % nombres)

        for line in lineas:
            certificacion = self._get_certificacion(line.acta_id)
            certificacion.write({
                'nombre_financiera_id': usuario.id,
                'fecha_financiera': self.fecha_firma,
                'firmado_finaciera': True,
            })

        return {'type': 'ir.actions.act_window_close'}


class ActaFirmaMasivaDirectorFinancieroWizardLine(models.TransientModel):
    _name = 'acta.firma.masiva.director.financiero.wizard.line'
    _description = 'Línea de firma masiva del Director Financiero'

    wizard_id = fields.Many2one(
        'acta.firma.masiva.director.financiero.wizard',
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
    estado = fields.Selection(
        related='acta_id.estado',
        string='Estado',
        readonly=True,
    )
    certificacion_generada = fields.Boolean(string='Certificación creada', readonly=True)
    certificacion_firmada_coordinador = fields.Boolean(string='Firmó Coordinador', readonly=True)
    certificacion_firmada_ventanilla = fields.Boolean(string='Firmó Ventanilla', readonly=True)
    certificacion_firmada_director = fields.Boolean(string='Firmó Director', readonly=True)

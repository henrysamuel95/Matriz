from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .correo_poliza import plantilla_correo_polizas


class SolicitudPolizaAsignacion(models.Model):
    """Responsable jurídico del trámite de pólizas.

    El responsable se selecciona del equipo Jurídico global. No requiere estar
    previamente asociado al proyecto; la asignación le concede el acceso al
    contrato y a la carpeta de documentos que necesita para revisar.
    """

    _inherit = 'contrataciones_fuv.solicitud.poliza'

    juridico_responsable_id = fields.Many2one(
        'res.users',
        string='Responsable jurídico',
        readonly=True,
        copy=False,
        tracking=True,
    )
    juridicos_asignables_ids = fields.Many2many(
        'res.users',
        string='Jurídicos disponibles para asignación',
        compute='_compute_juridicos_asignables',
    )
    puede_revisar_polizas = fields.Boolean(
        string='Puede revisar pólizas',
        compute='_compute_permisos_revision',
    )

    @api.depends(
        'contract_id',
        'contract_id.usuarios_juridico_ids',
        'contract_id.proyecto_relacionado.users_with_access',
        'sgr_contract_id',
        'sgr_contract_id.proyecto_relacionado.users_with_access_juridico',
    )
    def _compute_juridicos_asignables(self):
        usuarios_vacios = self.env['res.users']
        for solicitud in self:
            solicitud.juridicos_asignables_ids = (
                solicitud._usuarios_juridicos_asignables_poliza()
                if solicitud._contrato_poliza()
                else usuarios_vacios
            )

    @api.depends('juridico_responsable_id')
    @api.depends_context('uid')
    def _compute_permisos_revision(self):
        usuario = self.env.user
        es_director = usuario.has_group(
            'Matriz.group_contratacion_director_juridico'
        )
        es_juridico = usuario.has_group('Matriz.group_contratacion_juridico')
        for solicitud in self:
            solicitud.puede_revisar_polizas = bool(
                es_director
                or (
                    es_juridico
                    and solicitud.juridico_responsable_id == usuario
                )
            )

    def _usuarios_juridicos_asignables_poliza(self):
        self.ensure_one()
        grupo_juridico = self.env.ref('Matriz.group_contratacion_juridico')
        return self.env['res.users'].sudo().search([
            ('groups_id', 'in', grupo_juridico.id),
            ('active', '=', True),
            ('email', '!=', False),
        ])

    def _usuario_puede_revisar_polizas(self):
        self.ensure_one()
        return self.puede_revisar_polizas

    def _usuarios_notificacion_revision_polizas(self):
        """Incluye los responsables del proyecto y el Jurídico asignado."""
        destinatarios = super()._usuarios_notificacion_revision_polizas()
        asistentes = self.env['res.users']
        contrato = self._contrato_poliza()
        if contrato.proyecto_relacionado:
            grupo_asistente = self.env.ref(
                'Matriz.group_contratacion_asis_coordinador'
            )
            asistentes = contrato.proyecto_relacionado.users_with_access.filtered(
                lambda usuario: grupo_asistente in usuario.groups_id
            )
        destinatarios |= asistentes
        if self.juridico_responsable_id:
            destinatarios |= self.juridico_responsable_id
        return destinatarios.filtered(
            lambda usuario: usuario.active and bool(usuario.email)
        )

    def _grupos_notificacion_revision_polizas(self):
        """Separa acción, seguimiento e información sin duplicar buzones."""
        self.ensure_one()
        contrato = self._contrato_poliza()
        usuarios_proyecto = (
            contrato.proyecto_relacionado.users_with_access
            if contrato.proyecto_relacionado
            else self.env['res.users']
        )
        if self.sgr_contract_id:
            usuarios_proyecto |= contrato.proyecto_relacionado.users_with_access_juridico
        usuarios_proyecto = usuarios_proyecto.filtered(
            lambda usuario: usuario.active and bool(usuario.email)
        )
        grupo_director = self.env.ref(
            'Matriz.group_contratacion_director_juridico'
        )
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asistente = self.env.ref(
            'Matriz.group_contratacion_asis_coordinador'
        )
        directores = usuarios_proyecto.filtered(
            lambda usuario: grupo_director in usuario.groups_id
        )
        coordinacion = usuarios_proyecto.filtered(
            lambda usuario: (
                grupo_coordinador in usuario.groups_id
                or grupo_asistente in usuario.groups_id
            )
        )
        responsable = self.juridico_responsable_id.filtered(
            lambda usuario: usuario.active and bool(usuario.email)
        )
        # Si aún no existe asignación, Dirección Jurídica conserva la acción
        # para que el trámite no quede sin revisor.
        revisores = responsable or directores
        grupos = []
        if revisores:
            grupos.append({
                'clave': 'revision',
                'usuarios': revisores,
                'prefijo_asunto': _('Revisión'),
                'titulo': _('Pólizas cargadas'),
                'instruccion': _(
                    'Tiene pólizas asignadas para revisión. Ingrese al SGO y '
                    'decida si se aprueban o se devuelven para corrección.'
                ),
                'texto_boton': _('Revisar pólizas'),
            })
        if directores:
            grupos.append({
                'clave': 'seguimiento',
                'usuarios': directores,
                'prefijo_asunto': _('Seguimiento'),
                'titulo': _('Pólizas cargadas'),
                'instruccion': _(
                    'Las pólizas fueron enviadas al responsable jurídico. '
                    'Puede ingresar al SGO para realizar seguimiento.'
                ),
                'texto_boton': _('Ver seguimiento'),
            })
        if coordinacion:
            grupos.append({
                'clave': 'informacion',
                'usuarios': coordinacion,
                'prefijo_asunto': _('Información'),
                'titulo': _('Pólizas cargadas'),
                'instruccion': _(
                    'El cargue fue recibido y quedó disponible para revisión '
                    'jurídica. Puede consultar el avance en el SGO.'
                ),
                'texto_boton': _('Consultar trámite'),
            })
        return grupos

    def action_abrir_asignar_juridico_poliza(self):
        self.ensure_one()
        if not self.env.user.has_group(
            'Matriz.group_contratacion_director_juridico'
        ):
            raise UserError(_(
                'Solo Dirección Jurídica puede asignar al responsable de pólizas.'
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Asignar responsable jurídico'),
            'res_model': 'contrataciones_fuv.solicitud.poliza.asignar.juridico.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref(
                'Matriz.view_asignar_juridico_poliza_wizard'
            ).id,
            'target': 'new',
            'context': {
                'default_solicitud_id': self.id,
                'default_juridicos_seleccionados_ids': [
                    (6, 0, self.juridico_responsable_id.ids),
                ],
            },
        }

    def _asignar_juridico_responsable(self, juridico):
        self.ensure_one()
        if not self.env.user.has_group(
            'Matriz.group_contratacion_director_juridico'
        ):
            raise UserError(_(
                'Solo Dirección Jurídica puede asignar al responsable de pólizas.'
            ))
        if juridico.id not in self._usuarios_juridicos_asignables_poliza().ids:
            raise UserError(_(
                'La persona seleccionada debe pertenecer a Jurídica, estar activa '
                'y tener un correo electrónico configurado.'
            ))

        # La asignación también concede acceso de lectura a los documentos
        # del contrato, usando la carpeta y relación propias de cada flujo.
        contrato = self._contrato_poliza()
        if self.sgr_contract_id:
            contrato._agregar_juridico_poliza_sgr(juridico)
        elif juridico not in contrato.usuarios_juridico_ids:
            contrato.sudo().with_context(
                gestion_asignacion_juridica_polizas=True,
                skip_flujo_nuevo_juridico_1=True,
                notificar_acceso_juridico=False,
            ).write({
                'usuarios_juridico_ids': [(4, juridico.id)],
            })

        self.sudo().write({'juridico_responsable_id': juridico.id})
        enlace = self._url_formulario_revision()
        asunto = _('Asignación jurídica de pólizas - %s') % contrato.display_name
        contenido = Markup(
            '<p style="margin:0 0 18px;line-height:1.55">Fue asignado como responsable '
            'jurídico para revisar las pólizas del siguiente contrato.</p>'
            '<table role="presentation" width="100%%" cellspacing="0" cellpadding="0" '
            'style="width:100%%;background:#f4f7fb;border-left:4px solid #0b5cab;'
            'border-radius:6px"><tr><td style="padding:14px 16px;line-height:1.65">'
            '<strong style="color:#043484">Contrato:</strong> %s<br/>'
            '<strong style="color:#043484">Responsable jurídico:</strong> %s'
            '</td></tr></table>'
            '<p style="margin:20px 0 0;line-height:1.55">Ingrese al formulario de pólizas '
            'para consultar los documentos y gestionar su revisión. También dispone '
            'de acceso de lectura a los documentos del contrato en Drive.</p>'
            '<table role="presentation" cellspacing="0" cellpadding="0" '
            'style="margin:26px auto 8px"><tr><td bgcolor="#043484" '
            'style="border-radius:6px"><a href="%s" '
            'style="display:inline-block;padding:13px 24px;color:#ffffff;'
            'text-decoration:none;font-weight:700">Ver solicitud de pólizas</a>'
            '</td></tr></table>'
        ) % (
            escape(contrato.display_name),
            escape(juridico.name),
            escape(enlace),
        )
        cuerpo = plantilla_correo_polizas(_('Pólizas asignadas'), contenido)
        enviado = self._enviar_correo_sistema({
            'subject': asunto,
            'email_to': juridico.email,
            'body_html': cuerpo,
        })
        self.message_post(
            body=Markup(
                '<p><strong>Responsable jurídico asignado.</strong></p>'
                '<p><strong>Usuario:</strong> %s (%s)</p>'
            ) % (escape(juridico.name), escape(juridico.email)),
            subtype_xmlid='mail.mt_note',
        )
        if enviado:
            self._registrar_notificacion_correo(
                _('Asignación de responsable jurídico'),
                [{
                    'rol': _('Jurídica responsable'),
                    'nombre': juridico.name,
                    'correo': juridico.email,
                }],
            )
        return True


class SolicitudPolizaLineaAsignacion(models.Model):
    _inherit = 'contrataciones_fuv.solicitud.poliza.linea'

    puede_revisar_polizas = fields.Boolean(
        compute='_compute_puede_revisar_polizas',
        string='Puede revisar pólizas',
    )

    @api.depends('solicitud_id.puede_revisar_polizas')
    @api.depends_context('uid')
    def _compute_puede_revisar_polizas(self):
        for linea in self:
            linea.puede_revisar_polizas = bool(
                linea.solicitud_id.puede_revisar_polizas
            )


class AsignarJuridicoPolizaWizard(models.TransientModel):
    _name = 'contrataciones_fuv.solicitud.poliza.asignar.juridico.wizard'
    _description = 'Asignar responsable jurídico de pólizas'

    solicitud_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Solicitud de pólizas',
        required=True,
        readonly=True,
    )
    juridicos_asignables_ids = fields.Many2many(
        'res.users',
        compute='_compute_juridicos_asignables',
        string='Jurídicos disponibles',
    )
    juridicos_seleccionados_ids = fields.Many2many(
        'res.users',
        relation='poliza_asignar_juridico_wiz_user_rel',
        column1='wizard_id',
        column2='user_id',
        string='Responsable jurídico',
    )

    @api.depends('solicitud_id', 'solicitud_id.juridicos_asignables_ids')
    def _compute_juridicos_asignables(self):
        for wizard in self:
            wizard.juridicos_asignables_ids = (
                wizard.solicitud_id.juridicos_asignables_ids
            )

    def action_confirmar_asignacion(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_contratacion_director_juridico'):
            raise UserError(_('Solo Dirección Jurídica puede gestionar esta asignación.'))
        solicitud = self.solicitud_id.exists()
        if not solicitud:
            raise UserError(_('No se encontró la solicitud de pólizas.'))
        if len(self.juridicos_seleccionados_ids) > 1:
            raise UserError(_(
                'Este trámite admite un responsable jurídico. Retire el tag '
                'actual antes de seleccionar su reemplazo.'
            ))
        seleccionado = self.juridicos_seleccionados_ids
        anterior = solicitud.juridico_responsable_id
        if seleccionado:
            if seleccionado != anterior:
                solicitud._asignar_juridico_responsable(seleccionado)
        elif anterior:
            solicitud.sudo().write({'juridico_responsable_id': False})
            solicitud.sudo().message_post(
                body=Markup(
                    '<p><strong>Responsable jurídico retirado de pólizas:</strong> '
                    '%s (%s).</p>'
                ) % (escape(anterior.name), escape(anterior.email or '')),
                subtype_xmlid='mail.mt_note',
                author_id=self.env.user.partner_id.id,
            )
        return {'type': 'ir.actions.act_window_close'}

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..drive import GoogleDriveAuth
from ..contrataciones_sgr.estados import EstadoContratacionSgr
from .constantes import OrigenSolicitudPoliza


class ContratacionesSgrSolicitudPoliza(models.Model):
    _inherit = 'contrataciones_sgr'

    solicitud_poliza_ids = fields.One2many(
        'contrataciones_fuv.solicitud.poliza',
        'sgr_contract_id',
        string='Solicitudes de pólizas',
    )
    solicitud_poliza_ultima_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Solicitud de pólizas',
        compute='_compute_solicitud_poliza_ultima_sgr',
    )
    sgr_polizas_cargadas = fields.Integer(
        string='Pólizas cargadas',
        compute='_compute_sgr_polizas_cargadas',
    )
    registro_guardado_para_poliza = fields.Boolean(
        compute='_compute_registro_guardado_para_poliza_sgr',
    )
    puede_solicitar_poliza_guardada = fields.Boolean(
        compute='_compute_puede_solicitar_poliza_guardada_sgr',
    )

    @api.depends('solicitud_poliza_ids', 'solicitud_poliza_ids.fecha_solicitud')
    def _compute_solicitud_poliza_ultima_sgr(self):
        for record in self:
            record.solicitud_poliza_ultima_id = record.solicitud_poliza_ids.sorted(
                key=lambda solicitud: (solicitud.fecha_solicitud, solicitud.id),
                reverse=True,
            )[:1]

    @api.depends('solicitud_poliza_ids', 'solicitud_poliza_ids.linea_ids.documento_id')
    def _compute_sgr_polizas_cargadas(self):
        for record in self:
            record.sgr_polizas_cargadas = sum(
                bool(linea.documento_id)
                for solicitud in record.solicitud_poliza_ids
                for linea in solicitud.linea_ids
            )

    def _compute_registro_guardado_para_poliza_sgr(self):
        for record in self:
            record.registro_guardado_para_poliza = bool(record._origin.id)

    def _usuario_puede_solicitar_poliza(self):
        return any(self.env.user.has_group(grupo) for grupo in (
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_juridico',
            'Matriz.group_contratacion_director_juridico',
        ))

    @api.depends('estado_contratacion')
    @api.depends_context('uid')
    def _compute_puede_solicitar_poliza_guardada_sgr(self):
        puede_solicitar = self._usuario_puede_solicitar_poliza()
        for record in self:
            record.puede_solicitar_poliza_guardada = bool(
                puede_solicitar
                and record.estado_contratacion != EstadoContratacionSgr.CONTRATADO.value
            )

    def _usuarios_notificacion_revision_polizas(self):
        self.ensure_one()
        proyecto = self.proyecto_relacionado.sudo()
        usuarios = proyecto.users_with_access | proyecto.users_with_access_juridico
        return usuarios.filtered(
            lambda usuario: usuario.active and bool(usuario.email) and any(
                usuario.has_group(grupo) for grupo in (
                    'Matriz.group_contratacion_coordinador',
                    'Matriz.group_contratacion_director_juridico',
                )
            )
        )

    def _agregar_juridico_poliza_sgr(self, juridico):
        self.ensure_one()
        carpeta = self.drive_file_id
        if not carpeta or not carpeta.folder_id2:
            raise UserError(_('El contrato SGR no tiene carpeta individual en Drive.'))
        drive = GoogleDriveAuth.get_instance().get_drive()
        if not drive:
            raise UserError(_('No se pudo conectar con Google Drive.'))
        carpeta_google = drive.CreateFile({'id': carpeta.folder_id2})
        permisos = carpeta_google.GetPermissions()
        if not any(
            (permiso.get('emailAddress') or '').casefold() == juridico.email.casefold()
            for permiso in permisos
        ):
            carpeta_google.InsertPermission({
                'type': 'user', 'value': juridico.email, 'role': 'reader',
            })
        proyecto = self.proyecto_relacionado
        if juridico not in proyecto.users_with_access_juridico:
            proyecto.sudo().write({
                'users_with_access_juridico': [(4, juridico.id)],
            })

    def _validar_solicitud_polizas_antes_de_contratar(self):
        for record in self:
            solicitud = record.solicitud_poliza_ids.filtered(
                lambda item: item.origen == OrigenSolicitudPoliza.CONTRATO.value
            )[:1]
            if not solicitud:
                continue
            if not solicitud.linea_ids:
                raise UserError(_('La solicitud de pólizas no contiene garantías seleccionadas.'))
            correo = solicitud._correo_contratista()
            if not solicitud._todas_polizas_recibidas() and (not correo or '@' not in correo):
                raise UserError(_(
                    'El contratista no tiene un correo electrónico válido para enviar la solicitud de pólizas.'
                ))

    def _enviar_solicitud_polizas_al_contratar(self):
        for record in self.filtered(
            lambda contrato: contrato.estado_contratacion == EstadoContratacionSgr.CONTRATADO.value
        ):
            solicitud = record.solicitud_poliza_ids.filtered(
                lambda item: item.origen == OrigenSolicitudPoliza.CONTRATO.value
            )[:1]
            if solicitud and not solicitud.fecha_envio and not solicitud._todas_polizas_recibidas():
                solicitud.sudo()._enviar_solicitud_contratista(automatico=True)

    def action_aprobar_sgr(self):
        self.ensure_one()
        if self.estado_contratacion == EstadoContratacionSgr.COORDINADOR_CONFIRMACION.value:
            self._validar_solicitud_polizas_antes_de_contratar()
        resultado = super().action_aprobar_sgr()
        self._enviar_solicitud_polizas_al_contratar()
        return resultado

    def action_solicitar_poliza(self):
        self.ensure_one()
        if not self._origin.id:
            raise UserError(_('Debes guardar el contrato antes de solicitar pólizas.'))
        if not self._usuario_puede_solicitar_poliza():
            raise UserError(_('Solo Coordinación o Jurídica pueden solicitar pólizas.'))
        if self.estado_contratacion == EstadoContratacionSgr.CONTRATADO.value:
            raise UserError(_('El contrato ya está contratado.'))
        if self.solicitud_poliza_ids.filtered(
            lambda item: item.origen == OrigenSolicitudPoliza.CONTRATO.value
        ):
            raise UserError(_('Este contrato ya tiene una solicitud de pólizas guardada.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Guardar solicitud de pólizas'),
            'res_model': 'contrataciones_fuv.solicitar.poliza.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_solicitar_poliza_wizard').id,
            'target': 'new',
            'context': {'default_sgr_contract_id': self.id},
        }

    def action_view_solicitud_poliza(self):
        self.ensure_one()
        solicitud = self.solicitud_poliza_ultima_id
        if not solicitud:
            raise UserError(_('Este contrato no tiene una solicitud de pólizas guardada.'))
        return solicitud._action_abrir_formulario()

    def action_solicitar_poliza_soporte(self):
        self.ensure_one()
        if not self._origin.id:
            raise UserError(_('Debes guardar el contrato antes de solicitar pólizas.'))
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_('Solo Soporte puede crear una solicitud extraordinaria de pólizas.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Solicitud extraordinaria de pólizas'),
            'res_model': 'contrataciones_fuv.solicitar.poliza.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_solicitar_poliza_wizard').id,
            'target': 'new',
            'context': {
                'default_sgr_contract_id': self.id,
                'default_modo_soporte': True,
            },
        }

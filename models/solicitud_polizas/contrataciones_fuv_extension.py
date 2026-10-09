from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..contrataciones_fuv_aprobaciones import EstadoContratacion
from .constantes import OrigenSolicitudPoliza


ESTADOS_SIN_SOLICITUD_POLIZA_ORDINARIA = frozenset({
    EstadoContratacion.CONTRATADO.value,
    EstadoContratacion.LIQUIDADO.value,
    EstadoContratacion.ANULADO.value,
    EstadoContratacion.FINALIZADO.value,
})


class ContratacionesFuvSolicitudPoliza(models.Model):
    _inherit = 'contrataciones_fuv'

    solicitud_poliza_ids = fields.One2many(
        'contrataciones_fuv.solicitud.poliza',
        'contract_id',
        string='Solicitudes de pólizas',
    )
    solicitud_poliza_ultima_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Solicitud de pólizas',
        compute='_compute_solicitud_poliza_ultima',
    )
    registro_guardado_para_poliza = fields.Boolean(
        string='Contrato guardado',
        compute='_compute_registro_guardado_para_poliza',
    )
    puede_solicitar_poliza_guardada = fields.Boolean(
        string='Puede solicitar pólizas',
        compute='_compute_puede_solicitar_poliza_guardada',
    )

    @api.depends('solicitud_poliza_ids', 'solicitud_poliza_ids.fecha_solicitud')
    def _compute_solicitud_poliza_ultima(self):
        for record in self:
            record.solicitud_poliza_ultima_id = record.solicitud_poliza_ids.sorted(
                key=lambda solicitud: (solicitud.fecha_solicitud, solicitud.id),
                reverse=True,
            )[:1]

    def _compute_registro_guardado_para_poliza(self):
        for record in self:
            record.registro_guardado_para_poliza = bool(record._origin.id)

    def _usuario_puede_solicitar_poliza(self):
        return (
            self.env.user.has_group('Matriz.group_contratacion_director_juridico')
            or self.env.user.has_group('Matriz.group_contratacion_juridico')
            or self.env.user.has_group('Matriz.group_contratacion_coordinador')
        )

    def _usuarios_notificacion_revision_polizas(self):
        """Obtiene Coordinación y Dirección Jurídica asignadas al proyecto."""
        self.ensure_one()
        usuarios_proyecto = (
            self.proyecto_relacionado.users_with_access
            if self.proyecto_relacionado
            else self.env['res.users']
        )
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_director = self.env.ref(
            'Matriz.group_contratacion_director_juridico'
        )
        destinatarios = usuarios_proyecto.filtered(
            lambda usuario: (
                grupo_coordinador in usuario.groups_id
                or grupo_director in usuario.groups_id
            )
        )
        return destinatarios.filtered(
            lambda usuario: usuario.active and bool(usuario.email)
        )

    def _validar_solicitud_polizas_antes_de_contratar(self):
        """Valida una solicitud de pólizas solo cuando el contrato la tiene.

        Las pólizas no aplican a todos los contratos. Por tanto, la ausencia de
        una solicitud no puede bloquear la contratación; cuando Coordinación o
        Jurídica ya guardaron un checklist, sí se valida su contenido y el
        correo del contratista antes del envío automático.
        """
        for record in self:
            solicitud = record.solicitud_poliza_ids.filtered(
                lambda item: item.origen == OrigenSolicitudPoliza.CONTRATO.value
            )[:1]
            if not solicitud:
                continue
            if not solicitud.linea_ids:
                raise UserError(_(
                    'La solicitud de pólizas no contiene garantías seleccionadas.'
                ))
            correo = solicitud._correo_contratista()
            if not correo or '@' not in correo:
                raise UserError(_(
                    'El contratista no tiene un correo electrónico válido para '
                    'enviar la solicitud de pólizas.'
                ))

    def _enviar_solicitud_polizas_al_contratar(self):
        """Envía una sola vez la solicitud cuando el contrato queda contratado."""
        for record in self.filtered(
            lambda contrato: contrato.estado_contratacion == 'contratado'
        ):
            solicitud = record.solicitud_poliza_ids.filtered(
                lambda item: item.origen == OrigenSolicitudPoliza.CONTRATO.value
            )[:1]
            if solicitud and not solicitud.fecha_envio:
                solicitud.sudo()._enviar_solicitud_contratista(automatico=True)

    def contratado2(self):
        self._validar_solicitud_polizas_antes_de_contratar()
        resultado = super().contratado2()
        self._enviar_solicitud_polizas_al_contratar()
        return resultado

    def contratado(self):
        self._validar_solicitud_polizas_antes_de_contratar()
        resultado = super().contratado()
        self._enviar_solicitud_polizas_al_contratar()
        return resultado

    def action_set_contratado(self):
        """Incluye el envío también en la herramienta de normalización de Soporte."""
        self._validar_solicitud_polizas_antes_de_contratar()
        resultado = super().action_set_contratado()
        self._enviar_solicitud_polizas_al_contratar()
        return resultado

    @api.depends('estado_contratacion')
    @api.depends_context('uid')
    def _compute_puede_solicitar_poliza_guardada(self):
        puede_solicitar = bool(
            self._usuario_puede_solicitar_poliza()
            and self.env.user.has_group('Matriz.group_ver_cierre_contractual')
        )
        for record in self:
            record.puede_solicitar_poliza_guardada = bool(
                puede_solicitar
                and record.estado_contratacion
                not in ESTADOS_SIN_SOLICITUD_POLIZA_ORDINARIA
            )

    def action_solicitar_poliza(self):
        self.ensure_one()
        if not self._origin.id:
            raise UserError(_('Debes guardar el contrato antes de solicitar pólizas.'))
        if not self.env.user.has_group('Matriz.group_ver_cierre_contractual'):
            raise UserError(_(
                'Debe pertenecer al grupo Cierre contractual y pólizas '
                'para crear la solicitud inicial de pólizas.'
            ))
        if not self._usuario_puede_solicitar_poliza():
            raise UserError(_(
                'Solo Dirección Jurídica, Jurídica o Coordinación pueden solicitar pólizas.'
            ))
        if self.estado_contratacion in ESTADOS_SIN_SOLICITUD_POLIZA_ORDINARIA:
            raise UserError(_(
                'En contratos contratados, liquidados, anulados o finalizados, '
                'solo Soporte puede crear una solicitud extraordinaria de pólizas.'
            ))
        if self.solicitud_poliza_ids.filtered(
            lambda item: item.origen == OrigenSolicitudPoliza.CONTRATO.value
        ):
            raise UserError(_('Este contrato ya tiene una solicitud de pólizas guardada.'))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Guardar solicitud de pólizas',
            'res_model': 'contrataciones_fuv.solicitar.poliza.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_solicitar_poliza_wizard').id,
            'target': 'new',
            'context': {'default_contract_id': self.id},
        }

    def action_solicitar_poliza_soporte(self):
        """Crea un ciclo extraordinario aun con trámites anteriores finalizados."""
        self.ensure_one()
        if not self._origin.id:
            raise UserError(_('Debes guardar el contrato antes de solicitar pólizas.'))
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_(
                'Solo Soporte puede crear una solicitud extraordinaria de pólizas.'
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Solicitud extraordinaria de pólizas'),
            'res_model': 'contrataciones_fuv.solicitar.poliza.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_solicitar_poliza_wizard').id,
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'default_modo_soporte': True,
            },
        }

    def action_view_solicitud_poliza(self):
        self.ensure_one()
        solicitud = self.solicitud_poliza_ultima_id
        if not solicitud:
            raise UserError(_('Este contrato no tiene una solicitud de pólizas guardada.'))
        return solicitud._action_abrir_formulario()

from odoo import api, fields, models
from odoo.exceptions import UserError
from markupsafe import Markup, escape

from .estados import ESTADOS_CONTRATACION_SGR, EstadoContratacionSgr


FLUJO_SGR = [estado for estado, _nombre in ESTADOS_CONTRATACION_SGR]
GRUPOS_POR_ESTADO = {
    EstadoContratacionSgr.COORDINADOR_1.value: (
        'Matriz.group_contratacion_coordinador', 'Matriz.group_contratacion_asis_coordinador',
    ),
    EstadoContratacionSgr.JURIDICO_1.value: (
        'Matriz.group_contratacion_juridico', 'Matriz.group_contratacion_director_juridico',
    ),
    EstadoContratacionSgr.COORDINADOR_2.value: (
        'Matriz.group_contratacion_coordinador', 'Matriz.group_contratacion_asis_coordinador',
    ),
    EstadoContratacionSgr.JURIDICO_2.value: (
        'Matriz.group_contratacion_juridico', 'Matriz.group_contratacion_director_juridico',
    ),
    EstadoContratacionSgr.JURIDICO_SECOP.value: ('Matriz.group_contratacion_sgr_juridico_secop',),
    EstadoContratacionSgr.FINANCIERO.value: (
        'Matriz.group_contratacion_financiero', 'Matriz.group_contratacion_director_financiero',
    ),
    EstadoContratacionSgr.JURIDICO_ACTA_INICIO.value: (
        'Matriz.group_contratacion_juridico', 'Matriz.group_contratacion_director_juridico',
    ),
    EstadoContratacionSgr.COORDINADOR_POLIZAS_FIRMA.value: (
        'Matriz.group_contratacion_coordinador', 'Matriz.group_contratacion_asis_coordinador',
    ),
    EstadoContratacionSgr.JURIDICO_ACTA_APROBACION.value: (
        'Matriz.group_contratacion_juridico', 'Matriz.group_contratacion_director_juridico',
    ),
    EstadoContratacionSgr.COORDINADOR_CONFIRMACION.value: (
        'Matriz.group_contratacion_coordinador', 'Matriz.group_contratacion_asis_coordinador',
    ),
}


class ContratacionesSgrFlujo(models.Model):
    _inherit = 'contrataciones_sgr'

    sgr_ultimo_estado_devolucion = fields.Selection(
        ESTADOS_CONTRATACION_SGR, string='Último estado que devolvió', copy=False, readonly=True,
        tracking=True,
    )
    sgr_ultimo_usuario_devolucion_id = fields.Many2one(
        'res.users', string='Último usuario que devolvió', copy=False, readonly=True,
        tracking=True,
    )
    sgr_estado_retorno_devolucion = fields.Selection(
        ESTADOS_CONTRATACION_SGR, string='Estado al que debe retornar', copy=False, readonly=True,
        tracking=True,
    )
    sgr_usuario_asignado_id = fields.Many2one(
        'res.users', string='Usuario asignado en SGR', copy=False, readonly=True,
        tracking=True,
    )
    sgr_puede_actuar = fields.Boolean(compute='_compute_sgr_puede_actuar')

    @api.depends('sgr_usuario_asignado_id')
    @api.depends_context('uid')
    def _compute_sgr_puede_actuar(self):
        for record in self:
            record.sgr_puede_actuar = (
                not record.sgr_usuario_asignado_id
                or record.sgr_usuario_asignado_id.id == self.env.uid
            )

    def _validar_rol_flujo_sgr(self):
        self.ensure_one()
        grupos = GRUPOS_POR_ESTADO.get(self.estado_contratacion)
        if not grupos or not any(self.env.user.has_group(grupo) for grupo in grupos):
            raise UserError('No tienes permiso para actuar en este estado de la contratación SGR.')
        if self.sgr_usuario_asignado_id and self.sgr_usuario_asignado_id != self.env.user:
            raise UserError('Esta contratación fue retornada al usuario que solicitó los ajustes.')

    def _usuarios_destino_sgr(self, estado):
        self.ensure_one()
        grupos = self.env['res.groups'].sudo().browse([
            self.env.ref(xml_id).id for xml_id in GRUPOS_POR_ESTADO[estado]
        ])
        proyecto = self.proyecto_relacionado.sudo()
        usuarios = (
            proyecto.users_with_access |
            proyecto.users_with_access_juridico |
            proyecto.users_with_access_financiero
        )
        return usuarios.filtered(
            lambda user: user.active and not user.share and bool(user.groups_id & grupos)
        )

    @staticmethod
    def _campo_observaciones_flujo_sgr(estado):
        if estado == EstadoContratacionSgr.FINANCIERO.value:
            return 'observaciones_finacie'
        if estado and estado.startswith('juridico'):
            return 'observaciones_juri'
        return 'observaciones'

    def _registrar_accion_flujo_sgr(self, accion, origen, destino, observaciones):
        body = Markup('<b>%s:</b> %s → %s.') % (
            escape(accion), escape(self._nombre_estado_sgr(origen)),
            escape(self._nombre_estado_sgr(destino)),
        )
        if observaciones:
            texto = Markup('<br/>').join(escape(line) for line in observaciones.splitlines())
            body += Markup('<br/><b>Observaciones:</b><br/>%s') % texto
        self.message_post(body=body, subtype_xmlid='mail.mt_note')

    def _validar_documentos_para_contratar_sgr(self):
        self.ensure_one()
        cargados = {
            documento.codigo for documento in self.documentos_soporte_ids
            if documento.drive_id
        }
        faltantes = [
            nombre for codigo, nombre in (
                ('contrato', 'Contrato'),
                ('firmado', 'Contrato firmado'),
            ) if codigo not in cargados
        ]
        if faltantes:
            raise UserError(
                'Para contratar, cargue en Drive los siguientes soportes: %s.'
                % ', '.join(faltantes)
            )

    def action_aprobar_sgr(self):
        self.ensure_one()
        if self.estado_contratacion not in GRUPOS_POR_ESTADO:
            raise UserError('Este estado no permite aprobar la contratación SGR.')
        self._validar_rol_flujo_sgr()
        if self.sgr_estado_retorno_devolucion:
            raise UserError('Hay ajustes pendientes. Usa Retornar ajustes para volver al estado que devolvió.')
        origen = self.estado_contratacion
        siguiente = FLUJO_SGR[FLUJO_SGR.index(origen) + 1]
        if siguiente == EstadoContratacionSgr.CONTRATADO.value:
            self._validar_documentos_para_contratar_sgr()
            creador = self.proyecto_relacionado.create_uid.filtered(
                lambda user: user.active and not user.share and user.email
            )
            destinatarios = self.env.user | creador
            titulo = 'Contratación SGR contratada'
            area_destino = 'Contratado'
        else:
            destinatarios = self._usuarios_destino_sgr(siguiente)
            titulo = 'Aprobación de %s' % self._nombre_estado_sgr(origen)
            area_destino = self._nombre_estado_sgr(siguiente)
        destinatarios = self._destinatarios_correo_sgr(destinatarios)
        observaciones = self._observaciones_estado_sgr(origen)
        mensaje = (
            '%s contrató a %s.' % (self.env.user.name, self.name)
            if siguiente == EstadoContratacionSgr.CONTRATADO.value else
            '%s aprobó la contratación de %s y la envió a %s.'
            % (self.env.user.name, self.name, area_destino)
        )
        self.write({
            'estado_contratacion': siguiente,
            'sgr_usuario_asignado_id': False,
            self._campo_observaciones_flujo_sgr(origen): False,
        })
        self._registrar_accion_flujo_sgr(
            'Contratado' if siguiente == EstadoContratacionSgr.CONTRATADO.value else 'Aprobado',
            origen, siguiente, observaciones,
        )
        self._enviar_correo_flujo_sgr(
            destinatarios, titulo, area_destino, mensaje, observaciones,
            'Ver contratación SGR',
        )
        return True

    def action_devolver_sgr(self):
        self.ensure_one()
        if self.estado_contratacion not in GRUPOS_POR_ESTADO or self.estado_contratacion == FLUJO_SGR[0]:
            raise UserError('Este estado no permite devolver la contratación SGR.')
        self._validar_rol_flujo_sgr()
        if self.sgr_estado_retorno_devolucion:
            raise UserError('Primero hay que retornar los ajustes de la devolución pendiente.')
        origen = self.estado_contratacion
        observaciones = (self._observaciones_estado_sgr(origen) or '').strip()
        if not observaciones:
            raise UserError('Para devolver, escribe una observación en el campo de tu área.')
        destino = FLUJO_SGR[FLUJO_SGR.index(origen) - 1]
        destinatarios = self._destinatarios_correo_sgr(self._usuarios_destino_sgr(destino))
        area_destino = self._nombre_estado_sgr(destino)
        self.write({
            'sgr_ultimo_estado_devolucion': origen,
            'sgr_ultimo_usuario_devolucion_id': self.env.user.id,
            'sgr_estado_retorno_devolucion': origen,
            'sgr_usuario_asignado_id': False,
            'estado_contratacion': destino,
            self._campo_observaciones_flujo_sgr(origen): False,
        })
        self._registrar_accion_flujo_sgr('Devuelto', origen, destino, observaciones)
        self._enviar_correo_flujo_sgr(
            destinatarios,
            'Devolución de %s' % self._nombre_estado_sgr(origen),
            area_destino,
            '%s devolvió la contratación de %s para ajustes.' % (self.env.user.name, self.name),
            observaciones,
            'Revisar devolución',
            es_devolucion=True,
        )
        return True

    def action_retornar_ajustes_sgr(self):
        self.ensure_one()
        destino = self.sgr_estado_retorno_devolucion
        if not destino or destino not in GRUPOS_POR_ESTADO:
            raise UserError('No hay una devolución pendiente de retorno.')
        if FLUJO_SGR.index(destino) - 1 != FLUJO_SGR.index(self.estado_contratacion):
            raise UserError('El estado actual ya no corresponde a la devolución pendiente.')
        self._validar_rol_flujo_sgr()
        usuario = self.sgr_ultimo_usuario_devolucion_id.sudo()
        grupos = self.env['res.groups'].sudo().browse([
            self.env.ref(xml_id).id for xml_id in GRUPOS_POR_ESTADO[destino]
        ])
        if not usuario or not usuario.active or usuario.share or not (usuario.groups_id & grupos):
            raise UserError('El usuario que devolvió ya no tiene el rol necesario para recibir los ajustes.')
        destinatario = self._destinatarios_correo_sgr(usuario)
        origen = self.estado_contratacion
        observaciones = self._observaciones_estado_sgr(origen)
        self.write({
            'estado_contratacion': destino,
            'sgr_estado_retorno_devolucion': False,
            'sgr_usuario_asignado_id': usuario.id,
            self._campo_observaciones_flujo_sgr(origen): False,
        })
        self._registrar_accion_flujo_sgr('Ajustes retornados', origen, destino, observaciones)
        self._enviar_correo_flujo_sgr(
            destinatario,
            'Ajustes de contratación SGR',
            self._nombre_estado_sgr(destino),
            '%s completó los ajustes de la contratación de %s y la retornó a tu revisión.'
            % (self.env.user.name, self.name),
            observaciones,
            'Continuar revisión',
        )
        return True

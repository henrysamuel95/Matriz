from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from .estados import ESTADOS_CONTRATACION_SGR
from .flujo import FLUJO_SGR, GRUPOS_POR_ESTADO


def _verificar_soporte(env):
    if not env.user.has_group('Matriz.group_soporte'):
        raise AccessError(_('Solo Soporte puede reparar el flujo SGR.'))


def _retorno_consistente(contratacion):
    destino = contratacion.sgr_estado_retorno_devolucion
    actual = contratacion.estado_contratacion
    return bool(
        destino in GRUPOS_POR_ESTADO
        and actual in FLUJO_SGR
        and FLUJO_SGR.index(destino) - 1 == FLUJO_SGR.index(actual)
    )


class ContratacionesSgrSoporte(models.Model):
    _inherit = 'contrataciones_sgr'

    sgr_soporte_evento_ids = fields.One2many(
        'contrataciones_sgr.soporte.evento', 'contratacion_id',
        string='Historial de Soporte', readonly=True,
    )

    def _abrir_asistente_soporte_sgr(self, modelo, vista, titulo):
        self.ensure_one()
        _verificar_soporte(self.env)
        return {
            'type': 'ir.actions.act_window',
            'name': titulo,
            'res_model': modelo,
            'view_mode': 'form',
            'views': [(self.env.ref(vista).id, 'form')],
            'target': 'new',
            'context': {
                'default_contratacion_id': self.id,
                'default_estado_capturado': self.estado_contratacion,
                'default_retorno_capturado': self.sgr_estado_retorno_devolucion or False,
                'default_asignado_capturado_id': self.sgr_usuario_asignado_id.id or False,
            },
        }

    def action_soporte_diagnosticar_flujo_sgr(self):
        return self._abrir_asistente_soporte_sgr(
            'contrataciones_sgr.soporte.flujo.wizard',
            'Matriz.view_contrataciones_sgr_soporte_flujo_wizard_form',
            _('Soporte - Diagnosticar flujo SGR'),
        )

    def action_soporte_cambiar_estado_sgr(self):
        return self._abrir_asistente_soporte_sgr(
            'contrataciones_sgr.soporte.estado.wizard',
            'Matriz.view_contrataciones_sgr_soporte_estado_wizard_form',
            _('Soporte - Cambiar estado SGR'),
        )


class ContratacionesSgrSoporteEvento(models.Model):
    _name = 'contrataciones_sgr.soporte.evento'
    _description = 'Historial de reparaciones de contratación SGR'
    _order = 'create_date desc, id desc'

    contratacion_id = fields.Many2one('contrataciones_sgr', required=True, readonly=True, ondelete='cascade')
    usuario_id = fields.Many2one('res.users', required=True, readonly=True)
    accion = fields.Char(required=True, readonly=True)
    estado_anterior = fields.Char(readonly=True)
    estado_nuevo = fields.Char(readonly=True)
    asignado_anterior_id = fields.Many2one('res.users', readonly=True)
    asignado_nuevo_id = fields.Many2one('res.users', readonly=True)
    retorno_anterior = fields.Char(readonly=True)
    retorno_nuevo = fields.Char(readonly=True)
    motivo = fields.Text(required=True, readonly=True)


class ContratacionesSgrSoporteFlujoWizard(models.TransientModel):
    _name = 'contrataciones_sgr.soporte.flujo.wizard'
    _description = 'Diagnóstico y reparación del flujo SGR'

    contratacion_id = fields.Many2one('contrataciones_sgr', required=True, readonly=True)
    estado_capturado = fields.Char(readonly=True)
    retorno_capturado = fields.Char(readonly=True)
    asignado_capturado_id = fields.Many2one('res.users', readonly=True)
    estado_actual = fields.Selection(related='contratacion_id.estado_contratacion', readonly=True)
    estado_retorno = fields.Selection(related='contratacion_id.sgr_estado_retorno_devolucion', readonly=True)
    ultimo_usuario_id = fields.Many2one(related='contratacion_id.sgr_ultimo_usuario_devolucion_id', readonly=True)
    asignado_id = fields.Many2one(related='contratacion_id.sgr_usuario_asignado_id', readonly=True)
    diagnostico = fields.Text(compute='_compute_diagnostico')
    historial_ids = fields.One2many(related='contratacion_id.sgr_soporte_evento_ids', readonly=True)
    usuarios_elegibles_ids = fields.Many2many('res.users', compute='_compute_usuarios_elegibles')
    usuario_reasignado_id = fields.Many2one('res.users', string='Nuevo revisor')
    motivo = fields.Text(string='Motivo de la reparación', required=True)

    @api.depends('contratacion_id', 'contratacion_id.estado_contratacion',
                 'contratacion_id.sgr_estado_retorno_devolucion')
    def _compute_usuarios_elegibles(self):
        for wizard in self:
            contrato = wizard.contratacion_id
            destino = contrato.sgr_estado_retorno_devolucion or contrato.estado_contratacion
            wizard.usuarios_elegibles_ids = (
                contrato._usuarios_destino_sgr(destino)
                if contrato and destino in GRUPOS_POR_ESTADO else self.env['res.users']
            )

    @api.depends('contratacion_id', 'contratacion_id.estado_contratacion',
                 'contratacion_id.sgr_estado_retorno_devolucion',
                 'contratacion_id.sgr_usuario_asignado_id',
                 'contratacion_id.sgr_ultimo_usuario_devolucion_id')
    def _compute_diagnostico(self):
        for wizard in self:
            contrato = wizard.contratacion_id
            if not contrato:
                wizard.diagnostico = ''
            elif contrato.sgr_estado_retorno_devolucion:
                wizard.diagnostico = (
                    _('Devolución pendiente. Seleccione un revisor habilitado para recibir el retorno.')
                    if _retorno_consistente(contrato) else
                    _('Los estados de la devolución no coinciden. Use Cambiar estado para limpiar la devolución pendiente.')
                )
            elif contrato.sgr_usuario_asignado_id:
                wizard.diagnostico = _(
                    'El trámite está asignado a una persona. Seleccione otro revisor o deje el campo vacío para liberar la asignación.'
                )
            else:
                wizard.diagnostico = _('No hay devolución pendiente ni asignación que reparar.')

    def action_reparar(self):
        self.ensure_one()
        _verificar_soporte(self.env)
        contrato = self.contratacion_id.exists()
        if not contrato:
            raise UserError(_('La contratación ya no existe.'))
        if (self.estado_capturado != contrato.estado_contratacion
                or (self.retorno_capturado or False) != (contrato.sgr_estado_retorno_devolucion or False)
                or getattr(self.asignado_capturado_id, 'id', False) != getattr(contrato.sgr_usuario_asignado_id, 'id', False)):
            raise UserError(_('El flujo cambió mientras el asistente estaba abierto. Ciérrelo y vuelva a abrirlo.'))
        motivo = (self.motivo or '').strip()
        if not motivo:
            raise UserError(_('Indique el motivo de la reparación.'))

        pending = bool(contrato.sgr_estado_retorno_devolucion)
        if pending and not _retorno_consistente(contrato):
            raise UserError(_('Los estados de la devolución no coinciden. Corríjalos con Cambiar estado.'))
        if not pending and not contrato.sgr_usuario_asignado_id:
            raise UserError(_('No hay asignación ni devolución pendiente que reparar.'))

        usuario = self.usuario_reasignado_id
        if pending and not usuario:
            raise UserError(_('Seleccione al revisor que recibirá el retorno.'))
        destino = contrato.sgr_estado_retorno_devolucion or contrato.estado_contratacion
        if usuario and usuario not in contrato._usuarios_destino_sgr(destino):
            raise UserError(_('El revisor debe estar activo, pertenecer al proyecto y tener el rol del estado destino.'))

        anterior = contrato.sgr_usuario_asignado_id or contrato.sgr_ultimo_usuario_devolucion_id
        if usuario and usuario == anterior:
            raise UserError(_('Seleccione un revisor diferente al actual.'))
        valores = (
            {'sgr_ultimo_usuario_devolucion_id': usuario.id,
             'sgr_ultimo_estado_devolucion': destino}
            if pending else {'sgr_usuario_asignado_id': usuario.id if usuario else False}
        )
        contrato.sudo().write(valores)
        self.env['contrataciones_sgr.soporte.evento'].sudo().create({
            'contratacion_id': contrato.id,
            'usuario_id': self.env.uid,
            'accion': 'Reasignar devolución' if pending else 'Corregir asignación',
            'estado_anterior': contrato.estado_contratacion,
            'estado_nuevo': contrato.estado_contratacion,
            'asignado_anterior_id': anterior.id if anterior else False,
            'asignado_nuevo_id': usuario.id if usuario else False,
            'retorno_anterior': contrato.sgr_estado_retorno_devolucion or False,
            'retorno_nuevo': contrato.sgr_estado_retorno_devolucion or False,
            'motivo': motivo,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class ContratacionesSgrSoporteEstadoWizard(models.TransientModel):
    _name = 'contrataciones_sgr.soporte.estado.wizard'
    _description = 'Cambio administrativo de estado SGR'

    contratacion_id = fields.Many2one('contrataciones_sgr', required=True, readonly=True)
    estado_capturado = fields.Char(readonly=True)
    retorno_capturado = fields.Char(readonly=True)
    asignado_capturado_id = fields.Many2one('res.users', readonly=True)
    estado_actual = fields.Selection(related='contratacion_id.estado_contratacion', readonly=True)
    estado_nuevo = fields.Selection(ESTADOS_CONTRATACION_SGR, string='Nuevo estado', required=True)
    motivo = fields.Text(string='Motivo del cambio', required=True)

    def action_aplicar(self):
        self.ensure_one()
        _verificar_soporte(self.env)
        contrato = self.contratacion_id.exists()
        if not contrato:
            raise UserError(_('La contratación ya no existe.'))
        if (self.estado_capturado != contrato.estado_contratacion
                or (self.retorno_capturado or False) != (contrato.sgr_estado_retorno_devolucion or False)
                or getattr(self.asignado_capturado_id, 'id', False) != getattr(contrato.sgr_usuario_asignado_id, 'id', False)):
            raise UserError(_('El flujo cambió mientras el asistente estaba abierto. Ciérrelo y vuelva a abrirlo.'))
        if self.estado_nuevo not in dict(ESTADOS_CONTRATACION_SGR):
            raise UserError(_('Seleccione un estado válido de SGR.'))
        if contrato.estado_contratacion == self.estado_nuevo:
            raise UserError(_('La contratación ya se encuentra en ese estado.'))
        motivo = (self.motivo or '').strip()
        if not motivo:
            raise UserError(_('Indique el motivo del cambio.'))

        anterior = contrato.estado_contratacion
        retorno = contrato.sgr_estado_retorno_devolucion
        asignado = contrato.sgr_usuario_asignado_id
        contrato.sudo().write({
            'estado_contratacion': self.estado_nuevo,
            'sgr_estado_retorno_devolucion': False,
            'sgr_ultimo_estado_devolucion': False,
            'sgr_ultimo_usuario_devolucion_id': False,
            'sgr_usuario_asignado_id': False,
        })
        self.env['contrataciones_sgr.soporte.evento'].sudo().create({
            'contratacion_id': contrato.id,
            'usuario_id': self.env.uid,
            'accion': 'Cambiar estado',
            'estado_anterior': anterior or False,
            'estado_nuevo': self.estado_nuevo,
            'asignado_anterior_id': asignado.id if asignado else False,
            'retorno_anterior': retorno or False,
            'motivo': motivo,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

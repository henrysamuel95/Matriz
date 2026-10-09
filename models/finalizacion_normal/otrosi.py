# Restringe la creación posterior a Soporte autorizado y respeta las aprobaciones existentes del otrosí.
from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .reglas import ESTADOS_OTROSI_FORMALIZADOS


class OtrosiPosteriorFinalizacion(models.Model):
    _inherit = 'otro_si'

    fn_autorizacion_id = fields.Many2one('fuv.finalizacion.autorizacion', readonly=True, copy=False, ondelete='restrict')
    fn_es_posterior = fields.Boolean(compute='_compute_fn_es_posterior')

    @api.depends('fn_autorizacion_id', 'contratacion_fuv_en_id.fn_reapertura_pendiente')
    def _compute_fn_es_posterior(self):
        for registro in self:
            registro.fn_es_posterior = bool(
                registro.fn_autorizacion_id or registro.contratacion_fuv_en_id.fn_reapertura_pendiente
            )

    def _fn_puede_crear_excepcion(self, vals):
        """Hook mínimo del create original. Nunca autoriza por un contexto solo."""
        if not self.env.user.has_group('Matriz.group_soporte'):
            return False
        contrato = self.env['contrataciones_fuv'].browse(vals.get('contratacion_fuv_en_id')).exists()
        autorizacion = self.env['fuv.finalizacion.autorizacion'].sudo().browse(
            self.env.context.get('fn_autorizacion_id')
        ).exists()
        return bool(
            contrato and contrato.fn_reapertura_pendiente
            and autorizacion and autorizacion.estado == 'autorizada'
            and contrato.fn_autorizacion_activa_id == autorizacion
            and autorizacion.contract_id == contrato and autorizacion.autorizado_por_id
            and not autorizacion.otrosi_id
        )

    @api.model
    def create(self, vals):
        if 'fn_autorizacion_id' in vals:
            raise AccessError(_('La autorización se relaciona desde el servidor.'))
        contrato = self.env['contrataciones_fuv'].browse(vals.get('contratacion_fuv_en_id')).exists()
        excepcion = False
        if contrato:
            contrato.check_access_rights('read')
            contrato.check_access_rule('read')
            contrato._fn_bloquear_fila()
            if contrato.estado_contratacion == 'finalizado':
                raise AccessError(_('Soporte debe solicitar autorización de Dirección FUV antes de un otrosí posterior.'))
            if contrato.fn_reapertura_pendiente:
                if contrato.fn_otrosi_excepcion_id or not self._fn_puede_crear_excepcion(vals):
                    raise AccessError(_('Solo Soporte puede crear el único otrosí autorizado para esta reapertura.'))
                excepcion = self.env['fuv.finalizacion.autorizacion'].sudo().browse(self.env.context['fn_autorizacion_id'])
        contexto = {k: v for k, v in self.env.context.items() if not k.startswith('default_fn_')}
        registro = super(OtrosiPosteriorFinalizacion, self.with_context(contexto)).create(vals)
        if contrato and contrato.fn_revision_fecha:
            contrato.sudo().write({'fn_revision_fecha': False})
        if excepcion:
            # Actor original preservado. No cambia de usuario ni elimina validaciones existentes.
            registro.sudo().write({'fn_autorizacion_id': excepcion.id})
            excepcion.write({
                'estado': 'utilizada', 'otrosi_id': registro.id,
                'ejecutado_por_id': self.env.uid, 'ejecutado_el': fields.Datetime.now(),
            })
            contrato.sudo().write({'fn_otrosi_excepcion_id': registro.id})
            contrato.sudo().message_post(
                body=Markup('<p><b>Otrosí posterior creado por Soporte</b></p>'
                            '<p>Registro: %s (ID %s). Autorización: %s. Ejecutado por: %s.</p>')
                % (escape(registro.display_name), registro.id, excepcion.id, escape(self.env.user.name)),
                subtype_xmlid='mail.mt_note',
            )
        return registro

    def write(self, vals):
        if 'fn_autorizacion_id' in vals and not self.env.su:
            raise AccessError(_('La relación de autorización no se edita.'))
        contratos_revisados = self.mapped('contratacion_fuv_en_id')
        requiere_revision = bool({'estado_contratacion', 'fecha_terminacion', 'contratacion_fuv_en_id'} & set(vals))
        if requiere_revision:
            # Las formalizaciones y cambios de plazo comparten el mismo bloqueo
            # que el cron. Nunca decide sobre una fecha que está cambiando a la vez.
            destino = self.env['contrataciones_fuv'].browse(vals.get('contratacion_fuv_en_id')).exists()
            for contrato in (contratos_revisados | destino).sorted('id'):
                contrato._fn_bloquear_fila()
        if 'contratacion_fuv_en_id' in vals:
            for registro in self:
                if registro.fn_autorizacion_id and vals['contratacion_fuv_en_id'] != registro.contratacion_fuv_en_id.id:
                    raise AccessError(_('Un otrosí autorizado no puede trasladarse a otro contrato.'))
            destino = self.env['contrataciones_fuv'].browse(vals['contratacion_fuv_en_id']).exists()
            if destino:
                destino._fn_bloquear_fila()
                if destino.estado_contratacion == 'finalizado' or destino.fn_reapertura_pendiente:
                    raise AccessError(_('No puedes trasladar un otrosí a un contrato finalizado o en excepción.'))
        resultado = super().write(vals)
        if requiere_revision:
            for contrato in contratos_revisados | self.mapped('contratacion_fuv_en_id'):
                if contrato.fn_revision_fecha:
                    contrato.sudo().write({'fn_revision_fecha': False})
        if 'estado_contratacion' in vals:
            for registro in self.filtered('fn_autorizacion_id'):
                contrato = registro.contratacion_fuv_en_id
                if contrato.fn_reapertura_pendiente and registro.estado_contratacion in ESTADOS_OTROSI_FORMALIZADOS:
                    contrato.sudo().write({
                        'fn_reapertura_pendiente': False, 'fn_revision_fecha': False,
                        'fn_otrosi_excepcion_id': False,
                        'fn_autorizacion_activa_id': False,
                    })
                    contrato.sudo().message_post(
                        body=Markup('<p>Otrosí posterior formalizado (ID %s). La revisión de vigencia retoma su control.</p>') % registro.id,
                        subtype_xmlid='mail.mt_note',
                    )
        return resultado

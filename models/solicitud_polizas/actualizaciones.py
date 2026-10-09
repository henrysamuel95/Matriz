from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import OrigenSolicitudPoliza


class SolicitudPolizaActualizaciones(models.Model):
    """Crea ciclos independientes sin sobrescribir las pólizas históricas."""

    _inherit = 'contrataciones_fuv.solicitud.poliza'

    @api.model
    def _crear_actualizacion_desde_origen(
        self,
        contrato,
        origen,
        otro_si=False,
        cierre=False,
    ):
        contrato = contrato.exists()
        if not contrato:
            raise UserError(_('No se encontró el contrato de la actualización.'))

        registro_origen = otro_si or cierre
        if registro_origen:
            self.env.cr.execute(
                'SELECT id FROM "%s" WHERE id = %%s FOR UPDATE'
                % registro_origen._table,
                (registro_origen.id,),
            )

        dominio_existente = []
        if origen == OrigenSolicitudPoliza.OTRO_SI.value:
            dominio_existente = [('otro_si_id', '=', otro_si.id)]
        elif origen == OrigenSolicitudPoliza.LIQUIDACION.value:
            dominio_existente = [('cierre_proceso_id', '=', cierre.id)]
        existente = self.search(dominio_existente, limit=1) if dominio_existente else self
        if existente:
            return existente

        anteriores = contrato.solicitud_poliza_ids.filtered(
            lambda item: item.linea_ids
        ).sorted(key=lambda item: (item.fecha_solicitud, item.id), reverse=True)
        if not anteriores:
            return self

        anterior = anteriores[0]
        valores = {
            'contract_id': contrato.id,
            'origen': origen,
            'linea_ids': [(0, 0, {'tipo_poliza': linea.tipo_poliza}) for linea in anterior.linea_ids],
        }
        if otro_si:
            valores['otro_si_id'] = otro_si.id
        if cierre:
            valores['cierre_proceso_id'] = cierre.id
        if 'juridico_responsable_id' in self._fields and anterior.juridico_responsable_id:
            valores['juridico_responsable_id'] = anterior.juridico_responsable_id.id

        actor = self.env.user
        valores['solicitado_por_id'] = actor.id
        solicitud = self.sudo().create(valores).with_context(
            solicitud_poliza_actor_id=actor.id,
        )
        solicitud._crear_carpeta_poliza_solicitada()
        etiquetas = ', '.join(solicitud.linea_ids.mapped('tipo_poliza_nombre'))
        detalle_origen = otro_si.display_name if otro_si else cierre.display_name
        solicitud.message_post(
            body=Markup(
                '<p><strong>Actualización de pólizas creada.</strong></p>'
                '<p><strong>Origen:</strong> %s</p>'
                '<p><strong>Garantías:</strong> %s</p>'
            ) % (escape(detalle_origen), escape(etiquetas)),
            subtype_xmlid='mail.mt_note',
        )
        contrato.message_post(
            body=Markup(
                '<p><strong>Nuevo ciclo de pólizas:</strong> %s</p>'
                '<p><strong>Origen:</strong> %s</p>'
            ) % (escape(solicitud.display_name), escape(detalle_origen)),
            subtype_xmlid='mail.mt_note',
        )
        solicitud._enviar_solicitud_contratista(automatico=False)
        return solicitud


class OtroSiActualizacionPolizas(models.Model):
    _inherit = 'otro_si'

    solicitud_actualizacion_poliza_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Actualización de pólizas',
        compute='_compute_solicitud_actualizacion_poliza',
    )
    aplica_actualizacion_polizas = fields.Boolean(
        string='Requiere actualización de pólizas',
        compute='_compute_solicitud_actualizacion_poliza',
    )
    puede_solicitar_actualizacion_polizas = fields.Boolean(
        compute='_compute_solicitud_actualizacion_poliza',
    )

    @api.depends(
        'contratacion_fuv_en_id.solicitud_poliza_ids',
        'adicion', 'prorroga', 'adicion_prorroga', 'reinicio',
        'estado_contratacion', 'contratado_fin', 'contratado_fin_j',
    )
    def _compute_solicitud_actualizacion_poliza(self):
        modelo = self.env['contrataciones_fuv.solicitud.poliza']
        for record in self:
            record_id = record._origin.id
            record.solicitud_actualizacion_poliza_id = modelo.search([
                ('otro_si_id', '=', record_id),
            ], limit=1) if record_id else False
            tiene_antecedente = bool(
                record.contratacion_fuv_en_id.solicitud_poliza_ids.filtered(
                    lambda solicitud: solicitud.otro_si_id != record
                )
            )
            record.aplica_actualizacion_polizas = bool(
                tiene_antecedente
                and (
                    record.reinicio
                    or record.prorroga
                    or record.adicion
                    or record.adicion_prorroga
                    or (record.adicion and record.prorroga)
                )
            )
            record.puede_solicitar_actualizacion_polizas = bool(
                record.aplica_actualizacion_polizas
                and not record.solicitud_actualizacion_poliza_id
                and record._estado_permite_actualizar_polizas()
            )

    def _estado_permite_actualizar_polizas(self):
        self.ensure_one()
        return self._otrosi_estado_final_bloqueado()

    def _crear_actualizacion_polizas_si_aplica(self):
        for record in self:
            if not record.aplica_actualizacion_polizas:
                continue
            if record.solicitud_actualizacion_poliza_id:
                continue
            if not record._estado_permite_actualizar_polizas():
                continue
            solicitud = self.env['contrataciones_fuv.solicitud.poliza']._crear_actualizacion_desde_origen(
                record.contratacion_fuv_en_id,
                OrigenSolicitudPoliza.OTRO_SI.value,
                otro_si=record,
            )
            if solicitud:
                record.invalidate_recordset([
                    'solicitud_actualizacion_poliza_id',
                    'puede_solicitar_actualizacion_polizas',
                ])
                record.message_post(
                    body=Markup(
                        '<p><strong>Actualización de pólizas solicitada.</strong></p>'
                        '<p>Se conservaron las mismas garantías del trámite anterior.</p>'
                    ),
                    subtype_xmlid='mail.mt_note',
                )
        return True

    def action_solicitar_actualizacion_polizas(self):
        self.ensure_one()
        if not self.contratacion_fuv_en_id._usuario_puede_solicitar_poliza():
            raise UserError(_('No tiene permisos para solicitar la actualización de pólizas.'))
        if not self.aplica_actualizacion_polizas:
            raise UserError(_(
                'Este otrosí no requiere actualización: el contrato no tenía pólizas '
                'previas o el evento no es reinicio, prórroga, adición o adición y prórroga.'
            ))
        if not self._estado_permite_actualizar_polizas():
            raise UserError(_('La actualización se solicita cuando el otrosí queda formalizado.'))
        self._crear_actualizacion_polizas_si_aplica()
        return self.action_view_actualizacion_polizas()

    def action_view_actualizacion_polizas(self):
        self.ensure_one()
        solicitud = self.solicitud_actualizacion_poliza_id
        if not solicitud:
            raise UserError(_('Este otrosí todavía no tiene actualización de pólizas.'))
        return solicitud._action_abrir_formulario()

    def finalizar_otrosi_flujo_nuevo(self):
        resultado = super().finalizar_otrosi_flujo_nuevo()
        self._crear_actualizacion_polizas_si_aplica()
        return resultado

    def action_set_estado_final_otrosi_nuevo(self):
        resultado = super().action_set_estado_final_otrosi_nuevo()
        self._crear_actualizacion_polizas_si_aplica()
        return resultado

    def contratado(self):
        resultado = super().contratado()
        self._crear_actualizacion_polizas_si_aplica()
        return resultado

    def contratado2(self):
        resultado = super().contratado2()
        self._crear_actualizacion_polizas_si_aplica()
        return resultado

    def action_set_contratado(self):
        resultado = super().action_set_contratado()
        self._crear_actualizacion_polizas_si_aplica()
        return resultado


class CierreActualizacionPolizas(models.Model):
    _inherit = 'contrataciones_fuv.cierre.proceso'

    solicitud_actualizacion_poliza_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Pólizas para liquidación',
        compute='_compute_actualizacion_polizas_liquidacion',
    )
    puede_solicitar_polizas_liquidacion = fields.Boolean(
        compute='_compute_actualizacion_polizas_liquidacion',
    )

    @api.depends('contract_id.solicitud_poliza_ids', 'tipo_acta')
    def _compute_actualizacion_polizas_liquidacion(self):
        modelo = self.env['contrataciones_fuv.solicitud.poliza']
        for record in self:
            record_id = record._origin.id
            record.solicitud_actualizacion_poliza_id = modelo.search([
                ('cierre_proceso_id', '=', record_id),
            ], limit=1) if record_id else False
            record.puede_solicitar_polizas_liquidacion = bool(
                record.tipo_acta in ('normal', 'incumplimiento')
                and record.contract_id.solicitud_poliza_ids.filtered(
                    lambda solicitud: solicitud.cierre_proceso_id != record
                )
            )

    def action_solicitar_polizas_liquidacion(self):
        self.ensure_one()
        if not self.contract_id._usuario_puede_solicitar_poliza():
            raise UserError(_('No tiene permisos para solicitar pólizas en la liquidación.'))
        if not self.puede_solicitar_polizas_liquidacion:
            raise UserError(_('El contrato nunca tuvo pólizas; no hay garantías que actualizar.'))
        solicitud = self.env['contrataciones_fuv.solicitud.poliza']._crear_actualizacion_desde_origen(
            self.contract_id,
            OrigenSolicitudPoliza.LIQUIDACION.value,
            cierre=self,
        )
        self.message_post(
            body=Markup(
                '<p><strong>Actualización opcional de pólizas solicitada para la liquidación.</strong></p>'
            ),
            subtype_xmlid='mail.mt_note',
        )
        return solicitud._action_abrir_formulario()

    def action_view_polizas_liquidacion(self):
        self.ensure_one()
        if not self.solicitud_actualizacion_poliza_id:
            raise UserError(_('La liquidación no tiene una solicitud de pólizas.'))
        return self.solicitud_actualizacion_poliza_id._action_abrir_formulario()

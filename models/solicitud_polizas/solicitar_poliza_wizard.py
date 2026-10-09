from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

from .constantes import POLIZA_SELECTION, OrigenSolicitudPoliza


class SolicitarPolizaWizard(models.TransientModel):
    _name = 'contrataciones_fuv.solicitar.poliza.wizard'
    _description = 'Asistente para solicitar pólizas del contrato'
    _inherit = 'contrataciones_fuv.solicitud.poliza.campos'

    contract_id = fields.Many2one(
        'contrataciones_fuv',
        string='Contrato FUV',
        readonly=True,
    )
    sgr_contract_id = fields.Many2one(
        'contrataciones_sgr',
        string='Contrato SGR',
        readonly=True,
    )
    modo_soporte = fields.Boolean(
        string='Solicitud extraordinaria de Soporte',
        readonly=True,
    )

    def action_guardar_solicitud_poliza(self):
        self.ensure_one()
        if bool(self.contract_id) == bool(self.sgr_contract_id):
            raise UserError(_('Selecciona un solo contrato FUV o SGR.'))
        contrato = (self.sgr_contract_id or self.contract_id).exists()
        if not contrato or not contrato.id:
            raise UserError(_('Debes guardar el contrato antes de solicitar pólizas.'))
        es_soporte = bool(
            self.modo_soporte
            and self.env.user.has_group('Matriz.group_soporte')
        )
        if self.modo_soporte and not es_soporte:
            raise UserError(_(
                'Solo Soporte puede crear una solicitud extraordinaria de pólizas.'
            ))
        if not es_soporte and not contrato._usuario_puede_solicitar_poliza():
            raise UserError(_(
                'Solo Dirección Jurídica, Jurídica o Coordinación pueden solicitar pólizas.'
            ))
        if self.sgr_contract_id and not es_soporte and contrato.estado_contratacion == 'contratado':
            raise UserError(_('El contrato SGR ya está contratado.'))
        if not es_soporte and contrato.solicitud_poliza_ids.filtered(
            lambda item: item.origen == OrigenSolicitudPoliza.CONTRATO.value
        ):
            raise UserError(_('Este contrato ya tiene una solicitud de pólizas guardada.'))

        seleccionadas = self._polizas_seleccionadas()
        if not seleccionadas:
            raise UserError(_('Debes seleccionar al menos una póliza.'))

        valores_solicitud = {
            'sgr_contract_id' if self.sgr_contract_id else 'contract_id': contrato.id,
            'solicitado_por_id': self.env.user.id,
            'origen': (
                OrigenSolicitudPoliza.SOPORTE.value
                if es_soporte
                else OrigenSolicitudPoliza.CONTRATO.value
            ),
            'linea_ids': [
                (0, 0, {'tipo_poliza': field_name})
                for field_name in seleccionadas
            ],
        }
        modelo_solicitud = self.env['contrataciones_fuv.solicitud.poliza']
        if es_soporte:
            # Soporte no recibe permisos generales de creación: esta elevación
            # queda limitada a la acción extraordinaria ya validada.
            solicitud = modelo_solicitud.sudo().with_context(
                solicitud_poliza_actor_id=self.env.user.id,
            ).create(valores_solicitud)
        else:
            solicitud = modelo_solicitud.create(valores_solicitud)

        etiquetas_por_tipo = dict(POLIZA_SELECTION)
        etiquetas = [etiquetas_por_tipo[field_name] for field_name in seleccionadas]
        mensaje = Markup(
            '<p><strong>%s</strong></p>'
            '<p><strong>Solicitado por:</strong> %s</p>'
            '<p><strong>Pólizas:</strong> %s</p>'
        ) % (
            escape(
                _('Solicitud extraordinaria de pólizas creada por Soporte.')
                if es_soporte
                else _('Solicitud de pólizas guardada.')
            ),
            escape(self.env.user.name),
            escape(', '.join(etiquetas)),
        )
        if self.contract_id:
            contrato.message_post(body=mensaje, subtype_xmlid='mail.mt_note')
        solicitud.message_post(
            body=mensaje,
            subtype_xmlid='mail.mt_note',
            author_id=self.env.user.partner_id.id,
        )
        solicitud._crear_carpeta_poliza_solicitada()
        if es_soporte:
            # La excepción de Soporte funciona sin depender del estado actual
            # del contrato: cerrado, contratado o histórico.
            solicitud._enviar_solicitud_contratista(automatico=False)
        else:
            # También cubre contratos históricos que ya estaban contratados cuando
            # se creó por primera vez el checklist.
            contrato._enviar_solicitud_polizas_al_contratar()
        return solicitud._action_abrir_formulario()

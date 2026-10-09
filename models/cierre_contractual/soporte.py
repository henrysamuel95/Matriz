# Proporciona herramientas auditadas de recuperación para el equipo de Soporte en cierres contractuales.
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import ESTADOS_CIERRE_CONTRATO, EstadoCierreContrato


class ContratacionesFuvCierreSoporte(models.Model):
    _inherit = 'contrataciones_fuv.cierre.proceso'

    soporte_motivo = fields.Text(
        string='Motivo de la corrección',
        copy=False,
        help='Explica el incidente antes de ejecutar una acción de soporte.',
    )
    soporte_diagnostico = fields.Html(
        string='Diagnóstico de soporte',
        compute='_compute_soporte_diagnostico',
        sanitize=True,
    )

    @api.depends(
        'estado',
        'tipo_acta',
        'contract_id.estado_contratacion',
        'estado_retorno_devolucion',
        'cierre_drive_folder_id',
        'cierre_drive_folder_id.parent_folder_id',
        'contract_id.drive_file_id',
        'objeto_cierre',
        'otrosi_id',
        'otrosi_id.contratacion_fuv_en_id',
        'contratista_notificado',
        'fecha_notificacion_contratista',
        'fecha_liberacion_rfc',
        'liberado_por_id',
        'certificado_financiero_id',
        'acta_borrador_id',
        'acta_firmada_id',
        'acta_unilateral_firmada_id',
    )
    def _compute_soporte_diagnostico(self):
        """Señala inconsistencias frecuentes sin modificar información."""
        for record in self:
            alertas = []
            carpeta_contratista = record.contract_id.drive_file_id
            carpeta_cierre = record.cierre_drive_folder_id
            if not carpeta_contratista:
                alertas.append(_('El FC no tiene carpeta de contratista relacionada.'))
            if not carpeta_cierre:
                alertas.append(_('El cierre no tiene relacionada la carpeta Cierre contractual.'))
            elif carpeta_contratista and carpeta_cierre.parent_folder_id != carpeta_contratista:
                alertas.append(_('La carpeta del cierre no está dentro de la carpeta del contratista.'))
            if record.objeto_cierre == 'otrosi' and not record.otrosi_id:
                alertas.append(_('El cierre indica Otrosí, pero no conserva el registro relacionado.'))
            elif (
                record.otrosi_id
                and record.otrosi_id.contratacion_fuv_en_id != record.contract_id
            ):
                alertas.append(_('El otrosí relacionado pertenece a otro contrato.'))
            if (
                record.estado_retorno_devolucion
                and record.estado != EstadoCierreContrato.SOLICITUD.value
            ):
                alertas.append(_(
                    'Existe una etapa de retorno pendiente, pero el proceso no está en Solicitud.'
                ))
            if bool(record.contratista_notificado) != bool(
                record.fecha_notificacion_contratista
            ):
                alertas.append(_(
                    'La marca y la fecha de notificación al contratista no coinciden.'
                ))
            if bool(record.fecha_liberacion_rfc) != bool(record.liberado_por_id):
                alertas.append(_(
                    'La fecha y el usuario de liberación del RFC no coinciden.'
                ))
            estados_operativos = {
                EstadoCierreContrato.SOLICITUD.value,
                EstadoCierreContrato.FINANCIERA.value,
                EstadoCierreContrato.JURIDICA.value,
                EstadoCierreContrato.FIRMA_COORDINADOR.value,
                EstadoCierreContrato.LIBERACION_RFC.value,
            }
            if (
                record.estado in estados_operativos
                and record.contract_id.estado_contratacion != 'contratado'
            ):
                alertas.append(_(
                    'El cierre está activo, pero el FC padre no está en estado Contratado.'
                ))
            if (
                record.estado in {
                    EstadoCierreContrato.JURIDICA.value,
                    EstadoCierreContrato.FIRMA_COORDINADOR.value,
                    EstadoCierreContrato.LIBERACION_RFC.value,
                }
                and not record.certificado_financiero_id
            ):
                alertas.append(_(
                    'La etapa actual es posterior a Financiera y falta el certificado financiero.'
                ))
            if (
                record.estado == EstadoCierreContrato.FIRMA_COORDINADOR.value
                and not record.acta_borrador_id
            ):
                alertas.append(_(
                    'El proceso está en firma de Coordinación y no tiene acta preparada por Jurídica.'
                ))
            if (
                record.estado == EstadoCierreContrato.LIBERACION_RFC.value
                and not (
                    record.acta_unilateral_firmada_id
                    if record.tipo_acta == 'incumplimiento'
                    else record.acta_firmada_id
                )
            ):
                alertas.append(_(
                    'El proceso está en liberación y no tiene acta firmada relacionada.'
                ))
            if (
                record.tipo_acta == 'incumplimiento'
                and record.estado == EstadoCierreContrato.LIBERACION_RFC.value
                and not record.contratista_notificado
            ):
                alertas.append(_(
                    'La liquidación unilateral llegó a liberación sin marcar la notificación al contratista.'
                ))

            if alertas:
                items = ''.join('<li>%s</li>' % escape(alerta) for alerta in alertas)
                record.soporte_diagnostico = Markup(
                    '<div class="fc-cierre-support-warning">'
                    '<strong>Revisar:</strong><ul>%s</ul></div>'
                ) % Markup(items)
            else:
                record.soporte_diagnostico = Markup(
                    '<div class="fc-cierre-support-ok">'
                    '✓ No se detectaron inconsistencias técnicas básicas.'
                    '</div>'
                )

    def _validar_accion_soporte_cierre(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_('Solo el grupo Soporte puede ejecutar esta acción.'))
        if not (self.soporte_motivo or '').strip():
            raise UserError(_(
                'Debes escribir el motivo de la corrección antes de ejecutar soporte.'
            ))

    def _registrar_accion_soporte_cierre(self, accion, detalle=False):
        """Registra la corrección tanto en el cierre como en su FC padre."""
        self.ensure_one()
        mensaje = Markup(
            '<div style="border-left:4px solid #f59e0b;padding-left:10px;">'
            '<p><strong>🛠 Corrección de Soporte: %s</strong></p>'
            '%s'
            '<p><strong>Motivo:</strong> %s</p>'
            '<p><strong>Ejecutado por:</strong> %s</p>'
            '</div>'
        ) % (
            escape(accion),
            Markup('<p>%s</p>') % escape(detalle) if detalle else Markup(''),
            escape((self.soporte_motivo or '').strip()),
            escape(self.env.user.name),
        )
        self.message_post(body=mensaje, subtype_xmlid='mail.mt_note')
        if self.contract_id:
            self.contract_id.message_post(body=mensaje, subtype_xmlid='mail.mt_note')

    def action_soporte_cambiar_estado_cierre(self):
        """Mueve una etapa activa sin ejecutar correos ni efectos de aprobación."""
        self.ensure_one()
        self._validar_accion_soporte_cierre()
        estado_destino = self.env.context.get('estado_soporte_destino')
        estados_operativos = {
            EstadoCierreContrato.SOLICITUD.value,
            EstadoCierreContrato.FINANCIERA.value,
            EstadoCierreContrato.JURIDICA.value,
            EstadoCierreContrato.FIRMA_COORDINADOR.value,
            EstadoCierreContrato.LIBERACION_RFC.value,
        }
        if estado_destino not in estados_operativos:
            raise UserError(_(
                'Soporte solo puede recuperar etapas operativas. Finalizar o cancelar '
                'debe hacerse con las acciones normales del flujo.'
            ))
        if self.estado in (
            EstadoCierreContrato.FINALIZADO.value,
            EstadoCierreContrato.CANCELADO.value,
        ):
            raise UserError(_(
                'Un cierre finalizado o cancelado no puede reabrirse desde esta zona '
                'porque ya pudo producir efectos contractuales y financieros.'
            ))

        estado_anterior = self.estado
        etiquetas = dict(ESTADOS_CIERRE_CONTRATO)
        detalle = _('%(anterior)s → %(nuevo)s') % {
            'anterior': etiquetas.get(estado_anterior, estado_anterior),
            'nuevo': etiquetas.get(estado_destino, estado_destino),
        }
        self._registrar_accion_soporte_cierre(_('Cambio manual de etapa'), detalle)
        valores = {
            'estado': estado_destino,
            # Un cambio manual no debe conservar una ruta de devolución vieja.
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
            'soporte_motivo': False,
        }
        if (
            estado_destino == EstadoCierreContrato.LIBERACION_RFC.value
            and self.tipo_acta == 'anulacion'
        ):
            valores['valor_liberado_rfc'] = (
                self._valor_referencia_documento_cierre()
            )
        self.with_context(skip_validacion_cierre_contractual=True).write(valores)
        return True

    def action_soporte_reparar_carpeta_cierre(self):
        """Recupera la relación de Drive sin borrar ni mover carpetas existentes."""
        self.ensure_one()
        self._validar_accion_soporte_cierre()
        carpeta_anterior = self.cierre_drive_folder_id
        carpeta_contratista = self.contract_id.drive_file_id
        if (
            carpeta_anterior
            and carpeta_contratista
            and carpeta_anterior.parent_folder_id != carpeta_contratista
        ):
            # Solo retira el enlace incorrecto. No elimina la carpeta antigua.
            self.with_context(skip_validacion_cierre_contractual=True).write({
                'cierre_drive_folder_id': False,
            })
        carpeta = self._crear_carpeta_cierre_contractual()
        detalle = _('Carpeta relacionada: %s') % (
            carpeta.folder_name or carpeta.display_name
        )
        self._registrar_accion_soporte_cierre(_('Recuperación de carpeta Drive'), detalle)
        self.with_context(skip_validacion_cierre_contractual=True).write({
            'soporte_motivo': False,
        })
        return True

    def action_soporte_limpiar_retorno_cierre(self):
        """Elimina una ruta de devolución obsoleta que dejó bloqueado el flujo."""
        self.ensure_one()
        self._validar_accion_soporte_cierre()
        retorno = dict(ESTADOS_CIERRE_CONTRATO).get(
            self.estado_retorno_devolucion,
            self.estado_retorno_devolucion or _('Ninguno'),
        )
        self._registrar_accion_soporte_cierre(
            _('Limpieza de retorno de devolución'),
            _('Retorno anterior: %s') % retorno,
        )
        self.with_context(skip_validacion_cierre_contractual=True).write({
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
            'soporte_motivo': False,
        })
        return True

    def action_soporte_abrir_fc_cierre(self):
        """Abre el FC padre para contrastar estado, fondo y carpeta Drive."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_('Solo el grupo Soporte puede ejecutar esta acción.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Contratación FUV relacionada'),
            'res_model': 'contrataciones_fuv',
            'view_mode': 'form',
            'res_id': self.contract_id.id,
            'target': 'current',
        }

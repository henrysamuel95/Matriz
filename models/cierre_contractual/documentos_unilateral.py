# Gestiona exclusivamente los documentos, permisos y requisitos de la liquidación unilateral.
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import EstadoCierreContrato


class ContratacionesFuvCierreDocumentosUnilateral(models.Model):
    _inherit = 'contrataciones_fuv.cierre.proceso'

    soporte_solicitud_unilateral_id = fields.Many2one(
        'drive',
        string='Soporte de la solicitud unilateral',
        copy=False,
        tracking=True,
    )
    acta_unilateral_firmada_id = fields.Many2one(
        'drive',
        string='Acta unilateral firmada por Dirección Ejecutiva',
        copy=False,
        tracking=True,
    )
    soporte_liberacion_rfc_unilateral_id = fields.Many2one(
        'drive',
        string='Soporte de liberación del RFC unilateral',
        copy=False,
        tracking=True,
    )
    puede_cargar_certificado_unilateral = fields.Boolean(
        compute='_compute_permisos_documentos_unilateral',
    )
    puede_cargar_soporte_solicitud_unilateral = fields.Boolean(
        compute='_compute_permisos_documentos_unilateral',
    )
    puede_cargar_acta_unilateral = fields.Boolean(
        compute='_compute_permisos_documentos_unilateral',
    )
    puede_cargar_soporte_rfc_unilateral = fields.Boolean(
        compute='_compute_permisos_documentos_unilateral',
    )

    @api.depends('estado', 'juridico_responsable_id', 'tipo_acta')
    @api.depends_context('uid')
    def _compute_permisos_documentos_unilateral(self):
        """Aplica los responsables definidos en el flujo unilateral."""
        user = self.env.user
        es_soporte = self.env.su or user.has_group('Matriz.group_soporte')
        es_coordinacion = (
            user.has_group('Matriz.group_contratacion_coordinador')
            or user.has_group('Matriz.group_contratacion_asis_coordinador')
        )
        es_financiera = user.has_group('Matriz.group_financiera_cierre_contrato')
        es_director_juridico = user.has_group('Matriz.group_contratacion_director_juridico')
        es_juridico = user.has_group('Matriz.group_contratacion_juridico')
        es_presupuesto = user.has_group('Matriz.group_presupuestos')

        for record in self:
            es_unilateral = record.tipo_acta == 'incumplimiento'
            # Este soporte es opcional: Coordinación puede adjuntarlo únicamente
            # mientras prepara la solicitud, pero no se valida para poder enviarla.
            record.puede_cargar_soporte_solicitud_unilateral = bool(
                es_unilateral
                and record.estado == EstadoCierreContrato.SOLICITUD.value
                and (es_coordinacion or es_soporte)
            )
            record.puede_cargar_certificado_unilateral = bool(
                es_unilateral
                and record.estado == EstadoCierreContrato.FINANCIERA.value
                and (es_financiera or es_soporte)
            )
            record.puede_cargar_acta_unilateral = bool(
                es_unilateral
                and record.estado == EstadoCierreContrato.JURIDICA.value
                and (
                    es_soporte
                    or es_director_juridico
                    or (
                        es_juridico
                        and record.juridico_responsable_id == user
                    )
                )
            )
            record.puede_cargar_soporte_rfc_unilateral = bool(
                es_unilateral
                and record.estado == EstadoCierreContrato.LIBERACION_RFC.value
                and (es_financiera or es_presupuesto or es_soporte)
            )

    def _configuracion_documentos_unilateral(self):
        return {
            'soporte_solicitud_unilateral': {
                'campo': 'soporte_solicitud_unilateral_id',
                'nombre': 'Soporte de la solicitud unilateral',
                'estado': EstadoCierreContrato.SOLICITUD.value,
                'permiso': 'puede_cargar_soporte_solicitud_unilateral',
            },
            'certificado_financiero_unilateral': {
                'campo': 'certificado_financiero_id',
                'nombre': 'Certificado financiero con porcentaje de ejecución',
                'estado': EstadoCierreContrato.FINANCIERA.value,
                'permiso': 'puede_cargar_certificado_unilateral',
            },
            'acta_unilateral_firmada': {
                'campo': 'acta_unilateral_firmada_id',
                'nombre': 'Acta de liquidación unilateral firmada por Dirección Ejecutiva',
                'estado': EstadoCierreContrato.JURIDICA.value,
                'permiso': 'puede_cargar_acta_unilateral',
            },
            'soporte_liberacion_rfc_unilateral': {
                'campo': 'soporte_liberacion_rfc_unilateral_id',
                'nombre': 'Soporte de liberación del RFC',
                'estado': EstadoCierreContrato.LIBERACION_RFC.value,
                'permiso': 'puede_cargar_soporte_rfc_unilateral',
            },
        }

    def _action_cargar_documento_unilateral(self, tipo_documento):
        self.ensure_one()
        configuracion = self._configuracion_documentos_unilateral().get(tipo_documento)
        if not configuracion:
            raise UserError(_('El documento unilateral solicitado no está configurado.'))
        if self.tipo_acta != 'incumplimiento':
            raise UserError(_('Estos documentos solo aplican a la liquidación unilateral.'))
        if self.estado != configuracion['estado']:
            raise UserError(_(
                'El documento "%s" no se puede cargar en la etapa actual.'
            ) % configuracion['nombre'])
        if not self[configuracion['permiso']]:
            raise UserError(_(
                'No tienes permisos para cargar "%s" en esta etapa.'
            ) % configuracion['nombre'])

        carpeta = self.cierre_drive_folder_id
        if not carpeta or not carpeta.folder_id2:
            raise UserError(_(
                'El proceso no tiene vinculada su carpeta "Cierre contractual" en Drive.'
            ))
        return {
            'name': _('Cargar %s') % configuracion['nombre'],
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_drive_upload_popup_form').id,
            'target': 'new',
            'context': {
                # No crea carpetas: carga directamente en el cierre del contratista.
                'default_folder_id': carpeta.folder_id2,
                'default_stage2_id': carpeta.id,
                'cierre_contractual_proceso_id': self.id,
                'tipo_documento_cierre': tipo_documento,
                'tipo_documento_upload': tipo_documento,
            },
        }

    def _action_ver_documento_unilateral(self, tipo_documento):
        self.ensure_one()
        configuracion = self._configuracion_documentos_unilateral().get(tipo_documento)
        documento = self[configuracion['campo']] if configuracion else False
        if not documento:
            raise UserError(_('Todavía no hay un documento cargado.'))
        return {
            'type': 'ir.actions.act_window',
            'name': configuracion['nombre'],
            'res_model': 'drive',
            'view_mode': 'form',
            'res_id': documento.id,
            'target': 'new',
            'context': {'default_invisible_todo': True},
        }

    def _validar_documento_unilateral(self, campo, nombre, solo_si_hay_saldo=False):
        self.ensure_one()
        if self.tipo_acta != 'incumplimiento':
            return
        if solo_si_hay_saldo and self.valor_liberado_rfc <= 0:
            return
        if not self[campo]:
            raise UserError(_('Antes de continuar debes cargar: %s.') % nombre)

    def action_enviar_revision_juridica(self):
        self.ensure_one()
        if self.tipo_acta == 'incumplimiento':
            if not 0 <= self.porcentaje_ejecucion_financiera <= 100:
                raise UserError(_(
                    'El porcentaje de ejecución financiera debe estar entre 0 y 100.'
                ))
            if self.saldo_por_liberar < 0:
                raise UserError(_('El saldo certificado por liberar no puede ser negativo.'))
            if self.saldo_por_liberar > self._valor_referencia_documento_cierre():
                raise UserError(_(
                    'El saldo certificado por liberar no puede superar el valor '
                    'total del contrato con sus otrosí.'
                ))
        self._validar_documento_unilateral(
            'certificado_financiero_id',
            'Certificado financiero con porcentaje de ejecución',
        )
        return super().action_enviar_revision_juridica()

    def action_enviar_liberacion_rfc_unilateral(self):
        self.ensure_one()
        self._validar_documento_unilateral(
            'acta_unilateral_firmada_id',
            'Acta de liquidación unilateral firmada por Dirección Ejecutiva',
        )
        return super().action_enviar_liberacion_rfc_unilateral()

    def action_finalizar_cierre_contractual(self):
        self.ensure_one()
        if (
            self.tipo_acta == 'incumplimiento'
            and self.valor_liberado_rfc > self.saldo_por_liberar
        ):
            raise UserError(_(
                'El valor liberado no puede superar el saldo certificado por Financiera.'
            ))
        self._validar_documento_unilateral(
            'soporte_liberacion_rfc_unilateral_id',
            'Soporte de liberación del RFC',
            solo_si_hay_saldo=True,
        )
        return super().action_finalizar_cierre_contractual()

    def action_cargar_certificado_financiero_unilateral(self):
        return self._action_cargar_documento_unilateral('certificado_financiero_unilateral')

    def action_cargar_soporte_solicitud_unilateral(self):
        return self._action_cargar_documento_unilateral('soporte_solicitud_unilateral')

    def action_ver_soporte_solicitud_unilateral(self):
        return self._action_ver_documento_unilateral('soporte_solicitud_unilateral')

    def action_ver_certificado_financiero_unilateral(self):
        return self._action_ver_documento_unilateral('certificado_financiero_unilateral')

    def action_cargar_acta_unilateral_firmada(self):
        return self._action_cargar_documento_unilateral('acta_unilateral_firmada')

    def action_ver_acta_unilateral_firmada(self):
        return self._action_ver_documento_unilateral('acta_unilateral_firmada')

    def action_cargar_soporte_liberacion_rfc_unilateral(self):
        return self._action_cargar_documento_unilateral('soporte_liberacion_rfc_unilateral')

    def action_ver_soporte_liberacion_rfc_unilateral(self):
        return self._action_ver_documento_unilateral('soporte_liberacion_rfc_unilateral')


class DriveDocumentosCierreUnilateral(models.Model):
    _inherit = 'drive'

    def action_save_and_close(self):
        resultado = super().action_save_and_close()
        proceso_id = self.env.context.get('cierre_contractual_proceso_id')
        tipo_documento = self.env.context.get('tipo_documento_cierre')
        if not proceso_id or not tipo_documento:
            return resultado

        proceso = self.env['contrataciones_fuv.cierre.proceso'].browse(proceso_id).exists()
        if not proceso:
            raise UserError(_('No se encontró el proceso de cierre contractual relacionado.'))
        configuracion = proceso._configuracion_documentos_unilateral().get(tipo_documento)
        if not configuracion:
            return resultado
        if proceso.tipo_acta != 'incumplimiento':
            raise UserError(_('El documento no corresponde al tipo de cierre del proceso.'))
        if proceso.estado != configuracion['estado']:
            raise UserError(_('La etapa del proceso cambió antes de guardar el documento.'))
        if not proceso[configuracion['permiso']]:
            raise UserError(_('Ya no tienes permiso para guardar este documento.'))

        proceso.with_context(skip_validacion_cierre_contractual=True).write({
            configuracion['campo']: self.id,
        })
        if configuracion['campo'] == 'certificado_financiero_id':
            proceso._registrar_responsable_financiero_actual()
        proceso.message_post(
            body=Markup(
                '<p><strong>Documento cargado:</strong> %s</p>'
                '<p><strong>Archivo:</strong> %s</p>'
                '<p><strong>Cargado por:</strong> %s</p>'
            ) % (
                escape(configuracion['nombre']),
                escape(self.file_name or self.name or 'Sin nombre'),
                escape(self.env.user.name),
            ),
            subtype_xmlid='mail.mt_note',
        )
        if configuracion['campo'] == 'acta_unilateral_firmada_id':
            proceso._notificar_acta_firmada_financiera(configuracion['nombre'])
        return resultado

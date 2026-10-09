# Gestiona los documentos del acta de anulación, sus permisos, cargas en Drive y requisitos por etapa.
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import EstadoCierreContrato


class ContratacionesFuvCierreDocumentosAnulacion(models.Model):
    _inherit = 'contrataciones_fuv.cierre.proceso'

    solicitud_anulacion_id = fields.Many2one(
        'drive',
        string='Solicitud de anulación',
        copy=False,
        tracking=True,
    )
    soporte_liberacion_rfc_id = fields.Many2one(
        'drive',
        string='ARC',
        copy=False,
        tracking=True,
    )
    puede_editar_solicitud = fields.Boolean(
        compute='_compute_permisos_documentos_anulacion',
    )
    puede_cargar_certificado_anulacion = fields.Boolean(
        compute='_compute_permisos_documentos_anulacion',
    )
    puede_cargar_borrador_anulacion = fields.Boolean(
        compute='_compute_permisos_documentos_anulacion',
    )
    puede_cargar_acta_firmada_anulacion = fields.Boolean(
        compute='_compute_permisos_documentos_anulacion',
    )
    puede_cargar_soporte_rfc_anulacion = fields.Boolean(
        compute='_compute_permisos_documentos_anulacion',
    )

    @api.depends('estado', 'juridico_responsable_id', 'tipo_acta')
    @api.depends_context('uid')
    def _compute_permisos_documentos_anulacion(self):
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
            es_anulacion = record.tipo_acta == 'anulacion'
            record.puede_editar_solicitud = bool(
                es_anulacion
                and (es_coordinacion or es_soporte)
                and record.estado == EstadoCierreContrato.SOLICITUD.value
            )
            record.puede_cargar_certificado_anulacion = bool(
                es_anulacion
                and (es_financiera or es_soporte)
                and record.estado == EstadoCierreContrato.FINANCIERA.value
            )
            record.puede_cargar_borrador_anulacion = bool(
                es_anulacion
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
            record.puede_cargar_acta_firmada_anulacion = bool(
                es_anulacion
                and (es_coordinacion or es_soporte)
                and record.estado == EstadoCierreContrato.FIRMA_COORDINADOR.value
            )
            record.puede_cargar_soporte_rfc_anulacion = bool(
                es_anulacion
                and (es_financiera or es_presupuesto or es_soporte)
                and record.estado == EstadoCierreContrato.LIBERACION_RFC.value
            )

    def _configuracion_documentos_anulacion(self):
        return {
            'solicitud_anulacion': {
                'campo': 'solicitud_anulacion_id',
                'nombre': 'Solicitud de anulación',
                'estado': EstadoCierreContrato.SOLICITUD.value,
                'permiso': 'puede_editar_solicitud',
            },
            'certificado_financiero_anulacion': {
                'campo': 'certificado_financiero_id',
                'nombre': 'Certificado financiero 0%',
                'estado': EstadoCierreContrato.FINANCIERA.value,
                'permiso': 'puede_cargar_certificado_anulacion',
            },
            'borrador_acta_anulacion': {
                'campo': 'acta_borrador_id',
                'nombre': 'Borrador del acta de anulación',
                'estado': EstadoCierreContrato.JURIDICA.value,
                'permiso': 'puede_cargar_borrador_anulacion',
            },
            'acta_anulacion_firmada': {
                'campo': 'acta_firmada_id',
                'nombre': 'Acta de anulación firmada',
                'estado': EstadoCierreContrato.FIRMA_COORDINADOR.value,
                'permiso': 'puede_cargar_acta_firmada_anulacion',
            },
            'soporte_liberacion_rfc_anulacion': {
                'campo': 'soporte_liberacion_rfc_id',
                'nombre': 'ARC',
                'estado': EstadoCierreContrato.LIBERACION_RFC.value,
                'permiso': 'puede_cargar_soporte_rfc_anulacion',
            },
        }

    def _action_cargar_documento_anulacion(self, tipo_documento):
        self.ensure_one()
        configuracion = self._configuracion_documentos_anulacion().get(tipo_documento)
        if not configuracion:
            raise UserError(_('El tipo de documento solicitado no está configurado.'))
        if self.tipo_acta != 'anulacion':
            raise UserError(_('Estos documentos solo aplican al acta de anulación.'))
        if self.estado != configuracion['estado']:
            raise UserError(_(
                'El documento "%s" no se puede cargar en la etapa actual.'
            ) % configuracion['nombre'])
        if (
            tipo_documento == 'certificado_financiero_anulacion'
            and not self.env.user.has_group('Matriz.group_financiera_cierre_contrato')
            and not self.env.user.has_group('Matriz.group_soporte')
            and not self.env.su
        ):
            raise UserError(_(
                'Solo un usuario con el rol Financiera cierre contrato puede cargar el certificado financiero.'
            ))
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
                'default_folder_id': carpeta.folder_id2,
                'default_stage2_id': carpeta.id,
                'cierre_contractual_proceso_id': self.id,
                'tipo_documento_cierre': tipo_documento,
                'tipo_documento_upload': tipo_documento,
            },
        }

    def _action_ver_documento_anulacion(self, tipo_documento):
        self.ensure_one()
        configuracion = self._configuracion_documentos_anulacion().get(tipo_documento)
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

    def _validar_documento_anulacion(self, campo, nombre):
        self.ensure_one()
        if self.tipo_acta == 'anulacion' and not self[campo]:
            raise UserError(_(
                'Antes de continuar debes cargar: %s.'
            ) % nombre)

    def action_enviar_revision_juridica(self):
        self._validar_documento_anulacion(
            'certificado_financiero_id',
            'Certificado financiero 0%',
        )
        return super().action_enviar_revision_juridica()

    def action_enviar_firma_coordinador(self):
        self._validar_documento_anulacion(
            'acta_borrador_id',
            'Borrador del acta de anulación',
        )
        return super().action_enviar_firma_coordinador()

    def action_enviar_liberacion_rfc(self):
        self._validar_documento_anulacion(
            'acta_firmada_id',
            'Acta de anulación firmada',
        )
        return super().action_enviar_liberacion_rfc()

    def action_finalizar_cierre_contractual(self):
        self._validar_documento_anulacion(
            'soporte_liberacion_rfc_id',
            'ARC',
        )
        return super().action_finalizar_cierre_contractual()

    def action_cargar_solicitud_anulacion(self):
        return self._action_cargar_documento_anulacion('solicitud_anulacion')

    def action_ver_solicitud_anulacion(self):
        return self._action_ver_documento_anulacion('solicitud_anulacion')

    def action_cargar_certificado_financiero_anulacion(self):
        return self._action_cargar_documento_anulacion('certificado_financiero_anulacion')

    def action_ver_certificado_financiero_anulacion(self):
        return self._action_ver_documento_anulacion('certificado_financiero_anulacion')

    def action_cargar_borrador_acta_anulacion(self):
        return self._action_cargar_documento_anulacion('borrador_acta_anulacion')

    def action_ver_borrador_acta_anulacion(self):
        return self._action_ver_documento_anulacion('borrador_acta_anulacion')

    def action_cargar_acta_anulacion_firmada(self):
        return self._action_cargar_documento_anulacion('acta_anulacion_firmada')

    def action_ver_acta_anulacion_firmada(self):
        return self._action_ver_documento_anulacion('acta_anulacion_firmada')

    def action_cargar_soporte_liberacion_rfc_anulacion(self):
        return self._action_cargar_documento_anulacion('soporte_liberacion_rfc_anulacion')

    def action_ver_soporte_liberacion_rfc_anulacion(self):
        return self._action_ver_documento_anulacion('soporte_liberacion_rfc_anulacion')


class DriveDocumentosCierreAnulacion(models.Model):
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

        configuracion = proceso._configuracion_documentos_anulacion().get(tipo_documento)
        if not configuracion:
            # El contexto también lo utilizan los demás tipos de cierre. Si no
            # es un documento de anulación, deja que la siguiente extensión de
            # Drive procese su propia configuración.
            return resultado
        if proceso.tipo_acta != 'anulacion':
            raise UserError(_('El documento no corresponde al tipo de cierre del proceso.'))
        if proceso.estado != configuracion['estado']:
            raise UserError(_('La etapa del proceso cambió antes de guardar el documento.'))
        if not proceso[configuracion['permiso']]:
            raise UserError(_('Ya no tienes permiso para guardar este documento.'))

        # La validación específica anterior autoriza la carga documento por
        # documento. Esto permite que Financiera aporte el soporte RFC sin
        # concederle la edición general ni la finalización de Presupuesto.
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
        if configuracion['campo'] == 'acta_firmada_id':
            proceso._notificar_acta_firmada_financiera(configuracion['nombre'])
        return resultado

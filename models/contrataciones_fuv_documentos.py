#!/usr/bin/python
from odoo import models
from odoo.exceptions import UserError


class ContratacionesFuvDocumentos(models.Model):
    _inherit = 'contrataciones_fuv'

    def _es_usuario_consulta_documentos(self):
        return self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')

    _DRIVE_UPDATE_CONFIG = {
        'actualizar_correo_Tarjeta_profesional_revisor_fiscal_en_drive': (
            'Tarjeta_profesional_revisor_fiscal_doc_id',
            'actualizar_visible',
            'cedula_revisor_fiscal_actualizar_visible',
            {
                'nombre_contratista': 'name',
            },
        ),
        'actualizar_correo_Tarjeta_profesional_contador_en_drive': (
            'Tarjeta_profesional_contador_doc_id',
            'actualizar_visible',
            'cedula_revisor_fiscal_actualizar_visible',
            {
                'nombre_contratista': 'name',
            },
        ),
        'actualizar_correo_Documento_de_identidad_contador_en_drive': (
            'Documento_de_identidad_contador_doc_id',
            'actualizar_visible',
            'cedula_revisor_fiscal_actualizar_visible',
            {
                'nombre_contratista': 'name',
            },
        ),
        'actualizar_correo_formato_fuv_en_drive': (
            'formato_fuv_doc_id',
            'actualizar_visible',
            'formato_fuv_actualizar_visible',
            None,
        ),
        'actualizar_correo_tarjeta_profesional_en_drive': (
            'tarjeta_profesional_doc_id',
            'actualizar_visible',
            'tarjeta_profesional_actualizar_visible',
            None,
        ),
        'actualizar_correo_formato_fuv_juri_en_drive': (
            'formato_fuv_ju_doc_id',
            'actualizar_visible',
            'formato_fuv_juri_actualizar_visible',
            None,
        ),
        'actualizar_correo_poliza_en_drive': (
            'poliza_id',
            'actualizar_visible',
            'poliza_actualizar_visible',
            None,
        ),
        'actualizar_correo_poliza_pn_en_drive': (
            'poliza_pn_id',
            'actualizar_visible',
            'poliza_pn_actualizar_visible',
            None,
        ),
        'actualizar_correo_otra_certificado_arl_en_drive': (
            'otra_certificado_arl_id',
            'actualizar_visible',
            'otra_certificado_arl_actualizar_visible',
            None,
        ),
        'actualizar_correo_documento_identidad_en_drive': (
            'documento_identidad_id',
            'actualizar_visible',
            'documento_identidad_actualizar_visible',
            None,
        ),
        'actualizar_correo_rut_en_drive': (
            'rut_id',
            'actualizar_visible',
            'rut_actualizar_visible',
            None,
        ),
        'actualizar_correo_hoja_vida_en_drive': (
            'hoja_vida_id',
            'actualizar_visible',
            'hoja_vida_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificados_estudio_en_drive': (
            'certificados_estudio_id',
            'actualizar_visible',
            'certificados_estudio_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificados_laborales_en_drive': (
            'certificados_laborales_id',
            'actualizar_visible',
            'certificados_laborales_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificado_arl_en_drive': (
            'certificado_arl_id',
            'actualizar_visible',
            'certificado_arl_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_salud_en_drive': (
            'certificacion_salud_id',
            'actualizar_visible',
            'certificacion_salud_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_pension_en_drive': (
            'certificacion_pension_id',
            'actualizar_visible',
            'certificacion_pension_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_examen_preocupacional_en_drive': (
            'certificacion_examen_preocupacional_id',
            'actualizar_visible',
            'certificacion_examen_preocupacional_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_contraloria_en_drive': (
            'certificacion_contraloria_id',
            'actualizar_visible',
            'certificacion_contraloria_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_procuraduria_en_drive': (
            'certificacion_procuraduria_id',
            'actualizar_visible',
            'certificacion_procuraduria_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_policia_judicial_en_drive': (
            'certificacion_policia_judicial_id',
            'actualizar_visible',
            'certificacion_policia_judicial_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_medidas_cautelares_en_drive': (
            'certificacion_medidas_cautelares_id',
            'actualizar_visible',
            'certificacion_medidas_cautelares_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_inhabilidades_en_drive': (
            'certificacion_inhabilidades_id',
            'actualizar_visible',
            'certificacion_inhabilidades_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_libreta_militar_en_drive': (
            'certificacion_libreta_militar_id',
            'actualizar_visible',
            'certificacion_libreta_militar_actualizar_visible',
            None,
        ),
        'actualizar_correo_resolucion_pensionado_en_drive': (
            'resolucion_pensionado_id',
            'actualizar_visible',
            'resolucion_pensionado_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificacion_bancaria_en_drive': (
            'certificacion_bancaria_id',
            'actualizar_visible',
            'certificacion_bancaria_actualizar_visible',
            None,
        ),
        'actualizar_correo_anexo_tributario_en_drive': (
            'anexo_tributario_id',
            'actualizar_visible',
            'anexo_tributario_actualizar_visible',
            None,
        ),
        'actualizar_correo_contratos_en_drive': (
            'contratos_id',
            'actualizar_visible',
            'contratos_actualizar_visible',
            None,
        ),
        'actualizar_correo_contratos_firmado_en_drive': (
            'contratos_firmado_id',
            'actualizar_visible',
            'contratos_firmado_actualizar_visible',
            None,
        ),
        'actualizar_correo_su_cdp_en_drive': (
            'su_cdp_id',
            'actualizar_visible',
            'su_cdp_actualizar_visible',
            None,
        ),
        'actualizar_correo_declaracion_juramentada_en_drive': (
            'declaracion_juramentada_id',
            'actualizar_visible',
            'declaracion_juramentada_actualizar_visible',
            None,
        ),
        'actualizar_propuesta_pj_en_drive': (
            'propuesta_pj_doc_id',
            'actualizar_visible',
            'propuesta_doc_actualizar_visible',
            None,
        ),
        'actualizar_correo_camara_comercio_en_drive': (
            'camara_comercio_doc_id',
            'actualizar_visible',
            'camara_comercio_actualizar_visible',
            None,
        ),
        'actualizar_correo_rut_pj_en_drive': (
            'rut_pj_doc_id',
            'actualizar_visible',
            'rut_pj_actualizar_visible',
            None,
        ),
        'actualizar_correo_rup_en_drive': (
            'rup_doc_id',
            'actualizar_visible',
            'rup_actualizar_visible',
            None,
        ),
        'actualizar_correo_hoja_vida_juridica_en_drive': (
            'hoja_vida_juridica_doc_id',
            'actualizar_visible',
            'hoja_vida_juridica_actualizar_visible',
            None,
        ),
        'actualizar_correo_cert_contraloria_en_drive': (
            'cert_contraloria_doc_id',
            'actualizar_visible',
            'cert_contraloria_actualizar_visible',
            None,
        ),
        'actualizar_correo_cert_procuraduria_en_drive': (
            'cert_procuraduria_doc_id',
            'actualizar_visible',
            'cert_procuraduria_actualizar_visible',
            None,
        ),
        'actualizar_correo_cedula_rep_legal_en_drive': (
            'cedula_rep_legal_doc_id',
            'actualizar_visible',
            'cedula_rep_legal_actualizar_visible',
            None,
        ),
        'actualizar_correo_hoja_vida_rep_legal_en_drive': (
            'hoja_vida_rep_legal_doc_id',
            'actualizar_visible',
            'hoja_vida_rep_legal_actualizar_visible',
            None,
        ),
        'actualizar_correo_cert_contraloria_rep_legal_en_drive': (
            'cert_contraloria_rep_legal_doc_id',
            'actualizar_visible',
            'cert_contraloria_rep_legal_actualizar_visible',
            None,
        ),
        'actualizar_correo_cert_procuraduria_rep_legal_en_drive': (
            'cert_procuraduria_rep_legal_doc_id',
            'actualizar_visible',
            'cert_procuraduria_rep_legal_actualizar_visible',
            None,
        ),
        'actualizar_correo_cert_policia_judicial_en_drive': (
            'cert_policia_judicial_doc_id',
            'actualizar_visible',
            'cert_policia_judicial_actualizar_visible',
            None,
        ),
        'actualizar_correo_contrato_j_en_drive': (
            'aprueba_j_h_contrato_doc_id',
            'actualizar_visible',
            'contrato_j_actualizar_visible',
            None,
        ),
        'actualizar_correo_bdd_j_en_drive': (
            'aprueba_j_h_bdd_doc_id',
            'actualizar_visible',
            'bdd_j_actualizar_visible',
            None,
        ),
        'actualizar_correo_contrato_firmado_j_en_drive': (
            'aprueba_j_h_contrato_firmado_doc_id',
            'actualizar_visible',
            'contrato_firmado_j_actualizar_visible',
            None,
        ),
        'actualizar_correo_cert_inhabilidades_en_drive': (
            'cert_inhabilidades_doc_id',
            'actualizar_visible',
            'cert_inhabilidades_actualizar_visible',
            None,
        ),
        'actualizar_correo_resolucion_facturacion_en_drive': (
            'resolucion_facturacion_doc_id',
            'actualizar_visible',
            'resolucion_facturacion_actualizar_visible',
            None,
        ),
        'actualizar_correo_resolucion_facturacion_ju_en_drive': (
            'resolucion_facturacion_ju_id',
            'actualizar_visible',
            'resolucion_facturacion_ju_actualizar_visible',
            None,
        ),
        'actualizar_correo_bdd_ju_en_drive': (
            'bdd_ju_id',
            'visible',
            'bdd_ju_actualizar_visible',
            None,
        ),
        'actualizar_correo_experiencia_laboral_en_drive': (
            'experiencia_laboral_doc_id',
            'actualizar_visible',
            'experiencia_laboral_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificado_pago_parafiscales_en_drive': (
            'certificado_pago_parafiscales_doc_id',
            'actualizar_visible',
            'certificado_pago_parafiscales_actualizar_visible',
            None,
        ),
        'actualizar_correo_planilla_seguridad_social_en_drive': (
            'planilla_seguridad_social_doc_id',
            'actualizar_visible',
            'planilla_seguridad_social_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificado_junta_contadores_en_drive': (
            'certificado_junta_contadores_doc_id',
            'actualizar_visible',
            'certificado_junta_contadores_actualizar_visible',
            None,
        ),
        'actualizar_correo_cedula_revisor_fiscal_en_drive': (
            'cedula_revisor_fiscal_doc_id',
            'actualizar_visible',
            'cedula_revisor_fiscal_actualizar_visible',
            None,
        ),
        'actualizar_correo_certificado_bancario_en_drive': (
            'certificado_bancario_doc_id',
            'actualizar_visible',
            'certificado_bancario_actualizar_visible',
            None,
        ),
        'actualizar_correo_anexo_tributario_juridico_en_drive': (
            'anexo_tributario_juridico_doc_id',
            'actualizar_visible',
            'anexo_tributario_juridico_actualizar_visible',
            None,
        ),
        'actualizar_correo_cdp_juridico_en_drive': (
            'cdp_juridico_doc_id',
            'actualizar_visible',
            'cdp_juridico_actualizar_visible',
            None,
        ),
        'actualizar_correo_estados_financieros_juridico_en_drive': (
            'estados_financieros_juridico_doc_id',
            'actualizar_visible',
            'estados_financieros_juridico_actualizar_visible',
            None,
        ),
    }

    def _actualizar_documento_drive_generico(self, config_key):
        (
            documento_field,
            campo_visible_drive,
            campo_visible_fuv,
            campos_extra,
        ) = self._DRIVE_UPDATE_CONFIG[config_key]
        documento = getattr(self, documento_field, False)
        if not documento:
            return False

        vals = {
            'correo_electrinico': self.correo,
            'nombre_contratista': self.name,
        }
        if campos_extra:
            vals.update({
                campo_drive: getattr(self, campo_fuv)
                for campo_drive, campo_fuv in campos_extra.items()
            })
        if campo_visible_drive and campo_visible_fuv:
            vals[campo_visible_drive] = getattr(self, campo_visible_fuv)
        vals['file_data_visible'] = self.file_data_visible

        documento.write(vals)
        return True

    _ABRIR_DOCUMENTO_CONFIG = {
        'abrir_documento_Tarjeta_profesional_revisor_fiscal_pdf': {
            'drive_field': 'Tarjeta_profesional_revisor_fiscal_doc_id',
            'filename_field': 'Tarjeta_profesional_revisor_fiscal_doc_fname',
            'update_method': 'actualizar_correo_Tarjeta_profesional_revisor_fiscal_en_drive',
            'direct_title': 'Tarjeta profesional revisor fiscal',
            'found_title': 'Cédula Revisor Fiscal',
            'cache_name': '_cache_documentos_Tarjeta_profesional_revisor_fiscal',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Cédula de Revisor Fiscal relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_Tarjeta_profesional_contador_pdf': {
            'drive_field': 'Tarjeta_profesional_contador_doc_id',
            'filename_field': 'Tarjeta_profesional_contador_fname',
            'update_method': 'actualizar_correo_Tarjeta_profesional_contador_en_drive',
            'direct_title': 'Tarjeta Profesional Contador',
            'found_title': 'Cédula Revisor Fiscal',
            'cache_name': '_cache_documentos_Tarjeta_profesional_contador',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Cédula de Revisor Fiscal relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_Documento_de_identidad_contador_pdf': {
            'drive_field': 'Documento_de_identidad_contador_doc_id',
            'filename_field': 'Documento_de_identidad_contador_doc_fname',
            'update_method': 'actualizar_correo_Documento_de_identidad_contador_en_drive',
            'direct_title': 'Documento de identidad Contador',
            'found_title': 'Cédula Revisor Fiscal',
            'cache_name': '_cache_documentos_Documento_de_identidad_contador',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Cédula de Revisor Fiscal relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_formato_fuv_pdf': {
            'drive_field': 'formato_fuv_doc_id',
            'filename_field': 'formato_fuv_doc_fname',
            'update_method': 'actualizar_correo_formato_fuv_en_drive',
            'direct_title': 'Formato FUV',
            'found_title': 'Formato FUV',
            'cache_name': '_cache_documentos_formato_fuv',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Formato FUV relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': True,
        },
        'abrir_documento_tarjeta_profesional_pdf': {
            'drive_field': 'tarjeta_profesional_doc_id',
            'filename_field': 'tarjeta_profesional_doc_fname',
            'update_method': 'actualizar_correo_tarjeta_profesional_en_drive',
            'direct_title': 'Tarjeta Profesional',
            'found_title': 'Tarjeta Profesional',
            'cache_name': '_cache_documentos_tarjeta_profesional',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Tarjeta Profesional relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_formato_fuv_juri_pdf': {
            'drive_field': 'formato_fuv_ju_doc_id',
            'filename_field': 'formato_fuv_ju_doc_fname',
            'update_method': 'actualizar_correo_formato_fuv_juri_en_drive',
            'direct_title': 'Formato FUV',
            'found_title': 'Formato FUV',
            'cache_name': '_cache_documentos_formato_fuv_ju',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Formato FUV relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_poliza_pdf': {
            'drive_field': 'poliza_id',
            'filename_field': 'polizas_fname',
            'update_method': 'actualizar_correo_poliza_en_drive',
            'direct_title': 'Póliza',
            'found_title': 'Póliza',
            'cache_name': '_cache_documentos_poliza',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Póliza relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_poliza_pn_pdf': {
            'drive_field': 'poliza_pn_id',
            'filename_field': 'polizas__pn_fname',
            'update_method': 'actualizar_correo_poliza_pn_en_drive',
            'direct_title': 'Póliza',
            'found_title': 'Póliza',
            'cache_name': '_cache_documentos_poliza_pn',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Póliza relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': True,
        },
        'abrir_documento_otra_certificado_arl_pdf': {
            'drive_field': 'otra_certificado_arl_id',
            'filename_field': 'otra_certificado_arl_doc_fname',
            'update_method': 'actualizar_correo_otra_certificado_arl_en_drive',
            'direct_title': 'Novedad ARL',
            'found_title': 'Novedad ARL',
            'cache_name': '_cache_documentos_otra_certificado_arl',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado estudio relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': True,
        },
        'abrir_documento_pdf': {
            'drive_field': 'documento_identidad_id',
            'filename_field': 'documento_identidad_pn_doc_fname',
            'update_method': 'actualizar_correo_documento_identidad_en_drive',
            'direct_title': 'Documento de identidad',
            'found_title': 'Documento de identidad',
            'cache_name': '_cache_documentos_identidad',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Documento de Identidad relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_rut_pdf': {
            'drive_field': 'rut_id',
            'filename_field': 'rut_pn_doc_fname',
            'update_method': 'actualizar_correo_rut_en_drive',
            'direct_title': 'RUT (Actualizado)',
            'found_title': 'RUT (Actualizado)',
            'cache_name': '_cache_documentos_rut',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Documento de Identidad relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_hoja_vida_pdf': {
            'drive_field': 'hoja_vida_id',
            'filename_field': 'hoja_vida_pn_doc_fname',
            'update_method': 'actualizar_correo_hoja_vida_en_drive',
            'direct_title': 'Hoja de vida',
            'found_title': 'Hoja de vida',
            'cache_name': '_cache_documentos_hoja_vida',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Hoja de vida relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificados_estudio_pdf': {
            'drive_field': 'certificados_estudio_id',
            'filename_field': 'certificados_estudio_doc_fname',
            'update_method': 'actualizar_correo_certificados_estudio_en_drive',
            'direct_title': 'Certificado estudio',
            'found_title': 'Certificado estudio',
            'cache_name': '_cache_documentos_certificados_estudio',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado estudio relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificados_laborales_pdf': {
            'drive_field': 'certificados_laborales_id',
            'filename_field': 'certificados_laborales_doc_fname',
            'update_method': 'actualizar_correo_certificados_laborales_en_drive',
            'direct_title': 'Certificado laborales',
            'found_title': 'Certificado laborales',
            'cache_name': '_cache_documentos_certificados_laborales',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado laborales relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificado_arl_pdf': {
            'drive_field': 'certificado_arl_id',
            'filename_field': 'certificado_arl_doc_fname',
            'update_method': 'actualizar_correo_certificado_arl_en_drive',
            'direct_title': 'Cert.ARL(no mayor a 30 días) o formato ARL FUV',
            'found_title': 'Cert.ARL(no mayor a 30 días) o formato ARL FUV',
            'cache_name': '_cache_documentos_certificado_arl',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado estudio relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_salud_pdf': {
            'drive_field': 'certificacion_salud_id',
            'filename_field': 'certificacion_salud_doc_fname',
            'update_method': 'actualizar_correo_certificacion_salud_en_drive',
            'direct_title': 'Cert.afiliación salud(no mayor a 30 días)',
            'found_title': 'Cert.afiliación salud(no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_salud',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación salud con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_pension_pdf': {
            'drive_field': 'certificacion_pension_id',
            'filename_field': 'certificacion_pension_doc_fname',
            'update_method': 'actualizar_correo_certificacion_pension_en_drive',
            'direct_title': 'Cert.afiliación pensión(no mayor a 30 días)',
            'found_title': 'Cert.afiliación pensión(no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_pension',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación pensión con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_examen_preocupacional_pdf': {
            'drive_field': 'certificacion_examen_preocupacional_id',
            'filename_field': 'certificacion_examen_preocupacional_doc_fname',
            'update_method': 'actualizar_correo_certificacion_examen_preocupacional_en_drive',
            'direct_title': 'Cert.afiliación examen preocupacional(no mayor a 30 días)',
            'found_title': 'Cert.afiliación examen preocupacional(no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_examen_preocupacional',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación examen preocupacional con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_contraloria_pdf': {
            'drive_field': 'certificacion_contraloria_id',
            'filename_field': 'certificacion_contraloria_doc_fname',
            'update_method': 'actualizar_correo_certificacion_contraloria_en_drive',
            'direct_title': 'Cert.afiliación contraloria(no mayor a 30 días)',
            'found_title': 'Cert.afiliación contraloria(no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_contraloria',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación contraloria con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_procuraduria_pdf': {
            'drive_field': 'certificacion_procuraduria_id',
            'filename_field': 'certificacion_procuraduria_doc_fname',
            'update_method': 'actualizar_correo_certificacion_procuraduria_en_drive',
            'direct_title': 'Cert.afiliación procuraduria(no mayor a 30 días)',
            'found_title': 'Cert.afiliación procuraduria(no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_procuraduria',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación procuraduria con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_policia_judicial_pdf': {
            'drive_field': 'certificacion_policia_judicial_id',
            'filename_field': 'certificacion_policia_judicial_doc_fname',
            'update_method': 'actualizar_correo_certificacion_policia_judicial_en_drive',
            'direct_title': 'Cert.afiliación policía judicial (no mayor a 30 días)',
            'found_title': 'Cert.afiliación policía judicial (no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_policia_judicial',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación policía judicial con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_medidas_cautelares_pdf': {
            'drive_field': 'certificacion_medidas_cautelares_id',
            'filename_field': 'certificacion_medidas_cautelares_doc_fname',
            'update_method': 'actualizar_correo_certificacion_medidas_cautelares_en_drive',
            'direct_title': 'Medidas correctivas(no mayor a 30 días)',
            'found_title': 'Medidas correctivas (no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_medidas_cautelares',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.medidas cautelares relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_inhabilidades_pdf': {
            'drive_field': 'certificacion_inhabilidades_id',
            'filename_field': 'certificacion_inhabilidades_doc_fname',
            'update_method': 'actualizar_correo_certificacion_inhabilidades_en_drive',
            'direct_title': 'Cert.inhabilidades (no mayor a 30 días)',
            'found_title': 'Cert.inhabilidades (no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_inhabilidades',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.inhabilidades relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_libreta_militar_pdf': {
            'drive_field': 'certificacion_libreta_militar_id',
            'filename_field': 'certificacion_libreta_militar_doc_fname',
            'update_method': 'actualizar_correo_certificacion_libreta_militar_en_drive',
            'direct_title': 'Cert.libreta militar (no mayor a 30 días)',
            'found_title': 'Cert.libreta militar (no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_libreta_militar',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.libreta militar relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_resolucion_pensionado_pdf': {
            'drive_field': 'resolucion_pensionado_id',
            'filename_field': 'resolucion_pensionado_doc_fname',
            'update_method': 'actualizar_correo_resolucion_pensionado_en_drive',
            'direct_title': 'Cert.resolución pensionado (no mayor a 30 días)',
            'found_title': 'Cert.resolución pensionado (no mayor a 30 días)',
            'cache_name': '_cache_documentos_resolucion_pensionado',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.resolución pensionado relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificacion_bancaria_pdf': {
            'drive_field': 'certificacion_bancaria_id',
            'filename_field': 'certificacion_bancaria_doc_fname',
            'update_method': 'actualizar_correo_certificacion_bancaria_en_drive',
            'direct_title': 'Certificación bancaria (no mayor a 30 días)',
            'found_title': 'Certificación bancaria (no mayor a 30 días)',
            'cache_name': '_cache_documentos_certificacion_bancaria',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Certificación bancaria con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_anexo_tributario_pdf': {
            'drive_field': 'anexo_tributario_id',
            'filename_field': 'anexo_tributario_doc_fname',
            'update_method': 'actualizar_correo_anexo_tributario_en_drive',
            'direct_title': 'Anexo tributario',
            'found_title': 'Anexo tributario',
            'cache_name': '_cache_documentos_anexo_tributario',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Anexo tributario relacionado con este registro.',
            'direct_context': True,
            'found_context': True,
            'sync_file_id': True,
        },
        'abrir_documento_contratos_pdf': {
            'drive_field': 'contratos_id',
            'filename_field': 'contratos_doc_fname',
            'update_method': 'actualizar_correo_contratos_en_drive',
            'direct_title': 'Contrato en Drive',
            'found_title': 'Contrato en Drive',
            'cache_name': '_cache_documentos_contratos',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay documento relacionado ni nombre de archivo disponible.',
            'direct_context': True,
            'found_context': True,
            'sync_file_id': True,
        },
        'abrir_documento_contratos_firmado_pdf': {
            'drive_field': 'contratos_firmado_id',
            'filename_field': 'contratos_firmado_doc_fname',
            'update_method': 'actualizar_correo_contratos_firmado_en_drive',
            'direct_title': 'Contrato firmado',
            'found_title': 'Contrato firmado',
            'cache_name': '_cache_documentos_contratos_firmado',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún contrato firmado relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': True,
        },
        'abrir_documento_su_cdp_pdf': {
            'drive_field': 'su_cdp_id',
            'filename_field': 'su_cdp_doc_fname',
            'update_method': 'actualizar_correo_su_cdp_en_drive',
            'direct_title': 'RPC',
            'found_title': 'RPC',
            'cache_name': '_cache_documentos_su_cdp',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación examen preocupacional con este registro.',
            'direct_context': True,
            'found_context': True,
            'sync_file_id': True,
        },
        'abrir_documento_declaracion_juramentada_pdf': {
            'drive_field': 'declaracion_juramentada_id',
            'filename_field': 'declaracion_juramentada_doc_fname',
            'update_method': 'actualizar_correo_declaracion_juramentada_en_drive',
            'direct_title': 'Declaración juramentada',
            'found_title': 'Declaración juramentada',
            'cache_name': '_cache_documentos_declaracion_juramentada',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cert.afiliación examen preocupacional con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_propuesta_pj_pdf': {
            'drive_field': 'propuesta_pj_doc_id',
            'filename_field': 'propuesta_pj_doc_fname',
            'update_method': 'actualizar_propuesta_pj_en_drive',
            'direct_title': 'Propuesta',
            'found_title': 'Propuesta',
            'cache_name': '_cache_documentos_propuesta_pj',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No se encontró un nombre de archivo para buscar en el modelo Drive.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_camara_comercio_pdf': {
            'drive_field': 'camara_comercio_doc_id',
            'filename_field': 'camara_comercio_doc_fname',
            'update_method': 'actualizar_correo_camara_comercio_en_drive',
            'direct_title': 'Cámara de comercio (actualizada)',
            'found_title': 'Cámara de comercio (actualizada)',
            'cache_name': '_cache_documentos_camara_comercio',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cámara de comercio (actualizada) relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_rut_pj_pdf': {
            'drive_field': 'rut_pj_doc_id',
            'filename_field': 'rut_pj_doc_fname',
            'update_method': 'actualizar_correo_rut_pj_en_drive',
            'direct_title': 'RUT',
            'found_title': 'RUT',
            'cache_name': '_cache_documentos_rut_pj',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún RUT relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_rup_pdf': {
            'drive_field': 'rup_doc_id',
            'filename_field': 'rup_doc_fname',
            'update_method': 'actualizar_correo_rup_en_drive',
            'direct_title': 'RUP',
            'found_title': 'RUP',
            'cache_name': '_cache_documentos_rup',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún RUP relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_hoja_vida_juridica_pdf': {
            'drive_field': 'hoja_vida_juridica_doc_id',
            'filename_field': 'hoja_vida_juridica_doc_fname',
            'update_method': 'actualizar_correo_hoja_vida_juridica_en_drive',
            'direct_title': 'Hoja de Vida Jurídica',
            'found_title': 'Hoja de Vida Jurídica',
            'cache_name': '_cache_documentos_hoja_vida_juridica',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Hoja de Vida Jurídica relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cert_contraloria_pdf': {
            'drive_field': 'cert_contraloria_doc_id',
            'filename_field': 'cert_contraloria_doc_fname',
            'update_method': 'actualizar_correo_cert_contraloria_en_drive',
            'direct_title': 'Certificado Contraloría',
            'found_title': 'Certificado Contraloría',
            'cache_name': '_cache_documentos_cert_contraloria',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Contraloría relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cert_procuraduria_pdf': {
            'drive_field': 'cert_procuraduria_doc_id',
            'filename_field': 'cert_procuraduria_doc_fname',
            'update_method': 'actualizar_correo_cert_procuraduria_en_drive',
            'direct_title': 'Certificado Procuraduría',
            'found_title': 'Certificado Procuraduría',
            'cache_name': '_cache_documentos_cert_procuraduria',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Procuraduría relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cedula_rep_legal_pdf': {
            'drive_field': 'cedula_rep_legal_doc_id',
            'filename_field': 'cedula_rep_legal_doc_fname',
            'update_method': 'actualizar_correo_cedula_rep_legal_en_drive',
            'direct_title': 'Cédula Representante Legal',
            'found_title': 'Cédula Representante Legal',
            'cache_name': '_cache_documentos_cedula_rep_legal',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Cédula Representante Legal relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_hoja_vida_rep_legal_pdf': {
            'drive_field': 'hoja_vida_rep_legal_doc_id',
            'filename_field': 'hoja_vida_rep_legal_doc_fname',
            'update_method': 'actualizar_correo_hoja_vida_rep_legal_en_drive',
            'direct_title': 'Hoja de Vida Representante Legal',
            'found_title': 'Hoja de Vida Representante Legal',
            'cache_name': '_cache_documentos_hoja_vida_rep_legal',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Hoja de Vida Representante Legal relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cert_contraloria_rep_legal_pdf': {
            'drive_field': 'cert_contraloria_rep_legal_doc_id',
            'filename_field': 'cert_contraloria_rep_legal_doc_fname',
            'update_method': 'actualizar_correo_cert_contraloria_rep_legal_en_drive',
            'direct_title': 'Certificado Contraloría Representante Legal',
            'found_title': 'Certificado Contraloría Representante Legal',
            'cache_name': '_cache_documentos_cert_contraloria_rep_legal',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Contraloría Representante Legal relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cert_procuraduria_rep_legal_pdf': {
            'drive_field': 'cert_procuraduria_rep_legal_doc_id',
            'filename_field': 'aprueba_j_h_contraloria_rep_lega_doc_fname',
            'update_method': 'actualizar_correo_cert_procuraduria_rep_legal_en_drive',
            'direct_title': 'Certificado Procuraduría Representante Legal',
            'found_title': 'Certificado Procuraduría Representante Legal',
            'cache_name': '_cache_documentos_cert_procuraduria_rep_legal',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Procuraduría Representante Legal relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cert_policia_judicial_pdf': {
            'drive_field': 'cert_policia_judicial_doc_id',
            'filename_field': 'cert_policia_judicial_doc_fname',
            'update_method': 'actualizar_correo_cert_policia_judicial_en_drive',
            'direct_title': 'Certificado Policía Judicial',
            'found_title': 'Certificado Policía Judicial',
            'cache_name': '_cache_documentos_cert_policia_judicial',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Policía Judicial relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_contrato_j_pdf': {
            'drive_field': 'aprueba_j_h_contrato_doc_id',
            'filename_field': 'aprueba_j_h_contrato_doc_fname',
            'update_method': 'actualizar_correo_contrato_j_en_drive',
            'direct_title': 'Contrato Jurídico',
            'found_title': 'Contrato Jurídico',
            'cache_name': '_cache_documentos_contrato_j',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Contrato Jurídico relacionado con este registro.',
            'direct_context': True,
            'found_context': True,
            'sync_file_id': False,
        },
        'abrir_documento_bdd_j_pdf': {
            'drive_field': 'aprueba_j_h_bdd_doc_id',
            'filename_field': 'aprueba_j_h_bdd_doc_fname',
            'update_method': 'actualizar_correo_bdd_j_en_drive',
            'direct_title': 'BDD',
            'found_title': 'BDD',
            'cache_name': '_cache_documentos_bdd_j',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún BDD relacionado con este registro.',
            'direct_context': True,
            'found_context': True,
            'sync_file_id': True,
        },
        'abrir_documento_contrato_firmado_j_pdf': {
            'drive_field': 'aprueba_j_h_contrato_firmado_doc_id',
            'filename_field': 'aprueba_j_h_contrato_firmado_doc_fname',
            'update_method': 'actualizar_correo_contrato_firmado_j_en_drive',
            'direct_title': 'Contrato firmado',
            'found_title': 'Contrato firmado',
            'cache_name': '_cache_documentos_contrato_firmado_j',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Contrato Firmado Jurídico relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cert_inhabilidades_pdf': {
            'drive_field': 'cert_inhabilidades_doc_id',
            'filename_field': 'cert_inhabilidades_doc_fname',
            'update_method': 'actualizar_correo_cert_inhabilidades_en_drive',
            'direct_title': 'Certificado Inhabilidades',
            'found_title': 'Certificado Inhabilidades',
            'cache_name': '_cache_documentos_cert_inhabilidades',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Inhabilidades relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_resolucion_facturacion_pdf': {
            'drive_field': 'resolucion_facturacion_doc_id',
            'filename_field': 'resolucion_facturacion_doc_fname',
            'update_method': 'actualizar_correo_resolucion_facturacion_en_drive',
            'direct_title': 'Resolución de Facturación',
            'found_title': 'Resolución de Facturación',
            'cache_name': '_cache_documentos_resolucion_facturacion',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Resolución de Facturación relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_resolucion_facturacion_ju_pdf': {
            'drive_field': 'resolucion_facturacion_ju_id',
            'filename_field': 'resolucion_facturacion_ju_doc_fname',
            'update_method': 'actualizar_correo_resolucion_facturacion_ju_en_drive',
            'direct_title': 'Resolución de facturación electrónica',
            'found_title': 'Resolución de facturación electrónica',
            'cache_name': '_cache_documentos_resolucion_facturacion_ju',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Resolución de facturación electrónica relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_bdd_ju_pdf': {
            'drive_field': 'bdd_ju_id',
            'filename_field': 'bdd_ju_doc_fname',
            'update_method': 'actualizar_correo_resolucion_facturacion_ju_en_drive',
            'direct_title': 'BDD',
            'found_title': 'BDD',
            'cache_name': '_cache_documentos_bdd_ju',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún BDD relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_experiencia_laboral_pdf': {
            'drive_field': 'experiencia_laboral_doc_id',
            'filename_field': 'experiencia_laboral_doc_fname',
            'update_method': 'actualizar_correo_experiencia_laboral_en_drive',
            'direct_title': 'Experiencia Laboral',
            'found_title': 'Experiencia Laboral',
            'cache_name': '_cache_documentos_experiencia_laboral',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún documento de Experiencia Laboral relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificado_pago_parafiscales_pdf': {
            'drive_field': 'certificado_pago_parafiscales_doc_id',
            'filename_field': 'certificado_pago_parafiscales_doc_fname',
            'update_method': 'actualizar_correo_certificado_pago_parafiscales_en_drive',
            'direct_title': 'Certificado Pago Parafiscales',
            'found_title': 'Certificado Pago Parafiscales',
            'cache_name': '_cache_documentos_certificado_pago_parafiscales',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Pago Parafiscales relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_planilla_seguridad_social_pdf': {
            'drive_field': 'planilla_seguridad_social_doc_id',
            'filename_field': 'planilla_seguridad_social_doc_fname',
            'update_method': 'actualizar_correo_planilla_seguridad_social_en_drive',
            'direct_title': 'Planilla Seguridad Social',
            'found_title': 'Planilla Seguridad Social',
            'cache_name': '_cache_documentos_planilla_seguridad_social',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Planilla de Seguridad Social relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificado_junta_contadores_pdf': {
            'drive_field': 'certificado_junta_contadores_doc_id',
            'filename_field': 'certificado_junta_contadores_doc_fname',
            'update_method': 'actualizar_correo_certificado_junta_contadores_en_drive',
            'direct_title': 'Certificado Junta Contadores',
            'found_title': 'Certificado Junta Contadores',
            'cache_name': '_cache_documentos_certificado_junta_contadores',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Junta Contadores relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_cedula_revisor_fiscal_pdf': {
            'drive_field': 'cedula_revisor_fiscal_doc_id',
            'filename_field': 'cedula_revisor_fiscal_doc_fname',
            'update_method': 'actualizar_correo_cedula_revisor_fiscal_en_drive',
            'direct_title': 'Cédula Revisor Fiscal',
            'found_title': 'Cédula Revisor Fiscal',
            'cache_name': '_cache_documentos_cedula_revisor_fiscal',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna Cédula de Revisor Fiscal relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_certificado_bancario_pdf': {
            'drive_field': 'certificado_bancario_doc_id',
            'filename_field': 'certificado_bancario_doc_fname',
            'update_method': 'actualizar_correo_certificado_bancario_en_drive',
            'direct_title': 'Certificado Bancario',
            'found_title': 'Certificado Bancario',
            'cache_name': '_cache_documentos_certificado_bancario',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Certificado Bancario relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_anexo_tributario_juridico_pdf': {
            'drive_field': 'anexo_tributario_juridico_doc_id',
            'filename_field': 'anexo_tributario_juridico_doc_fname',
            'update_method': 'actualizar_correo_anexo_tributario_juridico_en_drive',
            'direct_title': 'Anexo Tributario Jurídico',
            'found_title': 'Anexo Tributario Jurídico',
            'cache_name': '_cache_documentos_anexo_tributario_juridico',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Anexo Tributario Jurídico relacionado con este registro.',
            'direct_context': True,
            'found_context': True,
            'sync_file_id': False,
        },
        'abrir_documento_cdp_juridico_pdf': {
            'drive_field': 'cdp_juridico_doc_id',
            'filename_field': 'cdp_doc_fname',
            'update_method': 'actualizar_correo_cdp_juridico_en_drive',
            'direct_title': 'RCP',
            'found_title': 'RCP',
            'cache_name': '_cache_documentos_cdp_juridico',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún RCP relacionado con este registro.',
            'direct_context': True,
            'found_context': True,
            'sync_file_id': True,
        },
        'abrir_documento_estados_financieros_juridico_pdf': {
            'drive_field': 'estados_financieros_juridico_doc_id',
            'filename_field': 'estados_financieros_doc_fname',
            'update_method': 'actualizar_correo_estados_financieros_juridico_en_drive',
            'direct_title': 'Estados financieros',
            'found_title': 'Estados financieros',
            'cache_name': '_cache_documentos_estados_financieros_juridico',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ningún Estados financieros relacionado con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
        'abrir_documento_archivo_acta_pdf': {
            'drive_field': 'archivo_acta_id',
            'filename_field': 'archivo_acta_nombre',
            'update_method': None,
            'direct_title': 'Archivo del acta',
            'found_title': 'Archivo del acta',
            'cache_name': '_cache_documentos_archivo_acta',
            'not_found_message': 'Documento no encontrado en Drive para {file_name}',
            'no_file_message': 'No hay ninguna archivo del acta relacionada con este registro.',
            'direct_context': False,
            'found_context': False,
            'sync_file_id': False,
        },
    }

    def _abrir_documento_drive_generico(self, config_key):
        self.ensure_one()
        config = self._ABRIR_DOCUMENTO_CONFIG[config_key]
        es_usuario_consulta = self._es_usuario_consulta_documentos()

        update_method = config.get('update_method')
        if update_method and not es_usuario_consulta:
            getattr(self, update_method)()

        def _build_action(title, drive_record, include_context):
            if (
                config.get('sync_file_id')
                and not es_usuario_consulta
                and not drive_record.file_id
                and drive_record.drive_file_id
            ):
                drive_record.write({'file_id': drive_record.drive_file_id})

            action = {
                'type': 'ir.actions.act_window',
                'name': title,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_record.id,
                'target': 'new',
            }
            if include_context:
                action['context'] = {
                    'default_contratacion_fuv_documentos_id': self.id,
                }
            return action

        drive_document = self[config['drive_field']]
        if drive_document:
            return _build_action(
                config['direct_title'],
                drive_document,
                config.get('direct_context'),
            )

        filename_field = config.get('filename_field')
        file_name = self[filename_field] if filename_field else False
        if file_name:
            file_name_to_search = file_name.strip().replace(" ", "_")
            cache_name = config.get('cache_name')
            cache = getattr(self, cache_name, {}) if cache_name else {}
            drive_document = cache.get(file_name_to_search)

            if not drive_document:
                drive_document = self.env['drive'].search(
                    [('file_name', 'ilike', file_name_to_search)],
                    limit=1,
                )
                if drive_document and cache_name:
                    cache[file_name_to_search] = drive_document
                    setattr(self, cache_name, cache)

            if drive_document:
                if not es_usuario_consulta:
                    self.write({config['drive_field']: drive_document.id})
                return _build_action(
                    config['found_title'],
                    drive_document,
                    config.get('found_context'),
                )

            raise UserError(
                config['not_found_message'].format(file_name=file_name_to_search)
            )

        raise UserError(config['no_file_message'])

    def abrir_documento_Tarjeta_profesional_revisor_fiscal_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_Tarjeta_profesional_revisor_fiscal_pdf')

    def abrir_documento_Tarjeta_profesional_contador_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_Tarjeta_profesional_contador_pdf')

    def abrir_documento_Documento_de_identidad_contador_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_Documento_de_identidad_contador_pdf')

    def abrir_documento_formato_fuv_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_formato_fuv_pdf')

    def abrir_documento_tarjeta_profesional_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_tarjeta_profesional_pdf')

    def abrir_documento_formato_fuv_juri_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_formato_fuv_juri_pdf')

    def abrir_documento_poliza_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_poliza_pdf')

    def abrir_documento_poliza_pn_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_poliza_pn_pdf')

    def abrir_documento_otra_certificado_arl_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_otra_certificado_arl_pdf')

    def abrir_documento_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_pdf')

    def abrir_documento_rut_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_rut_pdf')

    def abrir_documento_hoja_vida_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_hoja_vida_pdf')

    def abrir_documento_certificados_estudio_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificados_estudio_pdf')

    def abrir_documento_certificados_laborales_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificados_laborales_pdf')

    def abrir_documento_certificado_arl_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificado_arl_pdf')

    def abrir_documento_certificacion_salud_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_salud_pdf')

    def abrir_documento_certificacion_pension_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_pension_pdf')

    def abrir_documento_certificacion_examen_preocupacional_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_examen_preocupacional_pdf')

    def abrir_documento_certificacion_contraloria_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_contraloria_pdf')

    def abrir_documento_certificacion_procuraduria_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_procuraduria_pdf')

    def abrir_documento_certificacion_policia_judicial_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_policia_judicial_pdf')

    def abrir_documento_certificacion_medidas_cautelares_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_medidas_cautelares_pdf')

    def abrir_documento_certificacion_inhabilidades_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_inhabilidades_pdf')

    def abrir_documento_certificacion_libreta_militar_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_libreta_militar_pdf')

    def abrir_documento_resolucion_pensionado_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_resolucion_pensionado_pdf')

    def abrir_documento_certificacion_bancaria_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificacion_bancaria_pdf')

    def abrir_documento_anexo_tributario_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_anexo_tributario_pdf')

    def abrir_documento_contratos_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_contratos_pdf')

    def abrir_documento_contratos_firmado_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_contratos_firmado_pdf')

    def abrir_documento_su_cdp_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_su_cdp_pdf')

    def abrir_documento_declaracion_juramentada_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_declaracion_juramentada_pdf')

    def abrir_documento_propuesta_pj_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_propuesta_pj_pdf')

    def abrir_documento_camara_comercio_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_camara_comercio_pdf')

    def abrir_documento_rut_pj_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_rut_pj_pdf')

    def abrir_documento_rup_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_rup_pdf')

    def abrir_documento_hoja_vida_juridica_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_hoja_vida_juridica_pdf')

    def abrir_documento_cert_contraloria_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cert_contraloria_pdf')

    def abrir_documento_cert_procuraduria_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cert_procuraduria_pdf')

    def abrir_documento_cedula_rep_legal_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cedula_rep_legal_pdf')

    def abrir_documento_hoja_vida_rep_legal_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_hoja_vida_rep_legal_pdf')

    def abrir_documento_cert_contraloria_rep_legal_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cert_contraloria_rep_legal_pdf')

    def abrir_documento_cert_procuraduria_rep_legal_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cert_procuraduria_rep_legal_pdf')

    def abrir_documento_cert_policia_judicial_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cert_policia_judicial_pdf')

    def abrir_documento_contrato_j_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_contrato_j_pdf')

    def abrir_documento_bdd_j_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_bdd_j_pdf')

    def abrir_documento_contrato_firmado_j_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_contrato_firmado_j_pdf')

    def abrir_documento_cert_inhabilidades_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cert_inhabilidades_pdf')

    def abrir_documento_resolucion_facturacion_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_resolucion_facturacion_pdf')

    def abrir_documento_resolucion_facturacion_ju_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_resolucion_facturacion_ju_pdf')

    def abrir_documento_bdd_ju_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_bdd_ju_pdf')

    def abrir_documento_experiencia_laboral_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_experiencia_laboral_pdf')

    def abrir_documento_certificado_pago_parafiscales_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificado_pago_parafiscales_pdf')

    def abrir_documento_planilla_seguridad_social_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_planilla_seguridad_social_pdf')

    def abrir_documento_certificado_junta_contadores_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificado_junta_contadores_pdf')

    def abrir_documento_cedula_revisor_fiscal_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cedula_revisor_fiscal_pdf')

    def abrir_documento_certificado_bancario_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_certificado_bancario_pdf')

    def abrir_documento_anexo_tributario_juridico_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_anexo_tributario_juridico_pdf')

    def abrir_documento_cdp_juridico_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_cdp_juridico_pdf')

    def abrir_documento_estados_financieros_juridico_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_estados_financieros_juridico_pdf')

    def abrir_documento_archivo_acta_pdf(self):
        return self._abrir_documento_drive_generico('abrir_documento_archivo_acta_pdf')

    def actualizar_correo_Tarjeta_profesional_revisor_fiscal_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_Tarjeta_profesional_revisor_fiscal_en_drive')

    def actualizar_correo_Tarjeta_profesional_contador_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_Tarjeta_profesional_contador_en_drive')

    def actualizar_correo_Documento_de_identidad_contador_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_Documento_de_identidad_contador_en_drive')

    def actualizar_correo_formato_fuv_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_formato_fuv_en_drive')

    def actualizar_correo_tarjeta_profesional_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_tarjeta_profesional_en_drive')

    def actualizar_correo_formato_fuv_juri_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_formato_fuv_juri_en_drive')

    def actualizar_correo_poliza_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_poliza_en_drive')

    def actualizar_correo_poliza_pn_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_poliza_pn_en_drive')

    def actualizar_correo_otra_certificado_arl_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_otra_certificado_arl_en_drive')

    def actualizar_correo_documento_identidad_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_documento_identidad_en_drive')

    def actualizar_correo_rut_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_rut_en_drive')

    def actualizar_correo_hoja_vida_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_hoja_vida_en_drive')

    def actualizar_correo_certificados_estudio_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificados_estudio_en_drive')

    def actualizar_correo_certificados_laborales_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificados_laborales_en_drive')

    def actualizar_correo_certificado_arl_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificado_arl_en_drive')

    def actualizar_correo_certificacion_salud_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_salud_en_drive')

    def actualizar_correo_certificacion_pension_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_pension_en_drive')

    def actualizar_correo_certificacion_examen_preocupacional_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_examen_preocupacional_en_drive')

    def actualizar_correo_certificacion_contraloria_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_contraloria_en_drive')

    def actualizar_correo_certificacion_procuraduria_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_procuraduria_en_drive')

    def actualizar_correo_certificacion_policia_judicial_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_policia_judicial_en_drive')

    def actualizar_correo_certificacion_medidas_cautelares_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_medidas_cautelares_en_drive')

    def actualizar_correo_certificacion_inhabilidades_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_inhabilidades_en_drive')

    def actualizar_correo_certificacion_libreta_militar_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_libreta_militar_en_drive')

    def actualizar_correo_resolucion_pensionado_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_resolucion_pensionado_en_drive')

    def actualizar_correo_certificacion_bancaria_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificacion_bancaria_en_drive')

    def actualizar_correo_anexo_tributario_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_anexo_tributario_en_drive')

    def actualizar_correo_contratos_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_contratos_en_drive')

    def actualizar_correo_contratos_firmado_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_contratos_firmado_en_drive')

    def actualizar_correo_su_cdp_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_su_cdp_en_drive')

    def actualizar_correo_declaracion_juramentada_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_declaracion_juramentada_en_drive')

    def actualizar_propuesta_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_propuesta_pj_en_drive')

    def actualizar_correo_camara_comercio_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_camara_comercio_en_drive')

    def actualizar_correo_rut_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_rut_pj_en_drive')

    def actualizar_correo_rup_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_rup_en_drive')

    def actualizar_correo_hoja_vida_juridica_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_hoja_vida_juridica_en_drive')

    def actualizar_correo_cert_contraloria_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cert_contraloria_en_drive')

    def actualizar_correo_cert_procuraduria_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cert_procuraduria_en_drive')

    def actualizar_correo_cedula_rep_legal_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cedula_rep_legal_en_drive')

    def actualizar_correo_hoja_vida_rep_legal_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_hoja_vida_rep_legal_en_drive')

    def actualizar_correo_cert_contraloria_rep_legal_en_drive(self):
       # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cert_contraloria_rep_legal_en_drive')

    def actualizar_correo_cert_procuraduria_rep_legal_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cert_procuraduria_rep_legal_en_drive')

    def actualizar_correo_cert_policia_judicial_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cert_policia_judicial_en_drive')

    def actualizar_correo_contrato_j_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_contrato_j_en_drive')

    def actualizar_correo_bdd_j_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_bdd_j_en_drive')

    def actualizar_correo_contrato_firmado_j_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_contrato_firmado_j_en_drive')

    def actualizar_correo_cert_inhabilidades_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cert_inhabilidades_en_drive')

    def actualizar_correo_resolucion_facturacion_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_resolucion_facturacion_en_drive')

    def actualizar_correo_resolucion_facturacion_ju_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_resolucion_facturacion_ju_en_drive')

    def actualizar_correo_bdd_ju_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_bdd_ju_en_drive')

    def actualizar_correo_experiencia_laboral_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_experiencia_laboral_en_drive')

    def actualizar_correo_certificado_pago_parafiscales_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificado_pago_parafiscales_en_drive')

    def actualizar_correo_planilla_seguridad_social_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_planilla_seguridad_social_en_drive')

    def actualizar_correo_certificado_junta_contadores_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificado_junta_contadores_en_drive')

    def actualizar_correo_cedula_revisor_fiscal_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cedula_revisor_fiscal_en_drive')

    def actualizar_correo_certificado_bancario_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_certificado_bancario_en_drive')

    def actualizar_correo_anexo_tributario_juridico_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_anexo_tributario_juridico_en_drive')

    def actualizar_correo_cdp_juridico_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_cdp_juridico_en_drive')

    def actualizar_correo_estados_financieros_juridico_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        # Verificar si hay una relaci?n con el modelo Drive
        return self._actualizar_documento_drive_generico('actualizar_correo_estados_financieros_juridico_en_drive')

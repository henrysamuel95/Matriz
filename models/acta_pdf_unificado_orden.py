# -*- coding: utf-8 -*-
import json
import os
import re

from odoo import fields, models, _


class ActaEntregaDocumentosPdfOrden(models.Model):
    _inherit = 'acta_entrega_documentos'

    pdf_unificado_orden_manual = fields.Text(
        string='Orden manual PDF unificado',
        copy=False,
    )
    pdf_unificado_usar_orden_manual = fields.Boolean(
        string='Usar orden manual PDF unificado',
        default=False,
        copy=False,
    )

    def _pdf_unificado_nombre_normalizado(self, drive_record):
        nombre = (drive_record.file_name or drive_record.display_name or '').lower()
        nombre = os.path.splitext(nombre)[0]
        nombre = re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27,}', '', nombre)
        nombre = re.sub(r'_acta_\d+_cuenta_\d+', '', nombre)
        nombre = re.sub(r'[_\-\s]+', ' ', nombre).strip()
        return nombre

    def _pdf_unificado_es_generado(self, drive_record):
        nombre = self._pdf_unificado_nombre_normalizado(drive_record)
        return (
            'cuenta de cobro unificada' in nombre
            or 'cuenta cobro unificada' in nombre
            or 'pdf unificado' in nombre
        )

    def _pdf_unificado_es_core_en_adicionales(self, drive_record):
        nombre = self._pdf_unificado_nombre_normalizado(drive_record)
        patrones = (
            'certificacion conjunta', 'certificación conjunta',
            'documento equivalente',
            'factura electronica', 'factura electrónica',
            'informe de actividades', 'informe actividades',
        )
        return any(patron in nombre for patron in patrones)

    def _pdf_unificado_buscar_drive_por_nombres(self, patrones):
        self.ensure_one()
        stage = self.drive_stage_provedor_id
        if not stage:
            return self.env['drive']

        domain = [('stage4_id', '=', stage.id)]
        if stage.folder_id:
            domain = ['|', ('stage4_id', '=', stage.id), ('folder_id', '=', str(stage.folder_id))]

        candidatos = self.env['drive'].sudo().search(domain, order='id desc')
        patrones_norm = tuple((patron or '').lower() for patron in patrones)
        for registro in candidatos:
            if self._pdf_unificado_es_generado(registro):
                continue
            nombre = self._pdf_unificado_nombre_normalizado(registro)
            if any(patron in nombre for patron in patrones_norm):
                if self._resolve_google_drive_file_id(registro):
                    return registro
        return self.env['drive']

    def _pdf_unificado_preparar_documentos_generados(self):
        self.ensure_one()
        cuenta = self.cuenta_id
        factura_natural = bool(
            cuenta
            and (
                cuenta.factura_si_o_no
                or cuenta.factura_id
                or (
                    cuenta.numero_factura
                    and cuenta.numero_factura.strip()
                    and cuenta.numero_factura.strip().upper() not in ('N/A', 'NA')
                )
            )
        )

        if cuenta and self.clase_persona == 'persona_natural' and not factura_natural:
            self._refresh_documento_equivalente_drive_from_template()

        try:
            self._refresh_informe_de_actividades_drive_from_template()
        except Exception:
            pass

        self.invalidate_cache()

    def _documentos_para_pdf_unificado_base(self):
        self.ensure_one()
        documentos = []
        ids_odoo = set()
        ids_drive = set()
        nombres_core = set()
        categorias_core = set()

        def agregar(etiqueta, registros, categoria_core=False, omitir_core_en_adicionales=False):
            if not registros:
                return
            for registro in registros:
                if not registro:
                    continue
                if self._pdf_unificado_es_generado(registro):
                    continue
                file_id = self._resolve_google_drive_file_id(registro)
                if not file_id:
                    continue
                if registro.id in ids_odoo or file_id in ids_drive:
                    continue

                if omitir_core_en_adicionales and self._pdf_unificado_es_core_en_adicionales(registro):
                    continue

                nombre_normalizado = self._pdf_unificado_nombre_normalizado(registro)
                if categoria_core:
                    if categoria_core in categorias_core:
                        continue
                    if nombre_normalizado and nombre_normalizado in nombres_core:
                        continue

                documentos.append((etiqueta, registro))
                ids_odoo.add(registro.id)
                ids_drive.add(file_id)
                if categoria_core:
                    categorias_core.add(categoria_core)
                    if nombre_normalizado:
                        nombres_core.add(nombre_normalizado)

        agregar('Comprobante de pago contable (Tesorería)', self.comprobante_de_pago_id)
        agregar('Comprobante de pago banco (Tesorería)', self.comprobante_bancario_id)
        agregar('Orden de pago contable (Auxiliar tesorería)', self.orden_de_pago_id)
        agregar('Causación contable (Contabilidad)', self.causacion_id)
        agregar('Documento de retenciones Art. 383 (Contabilidad)', self.retencion_id)
        agregar('Documento de retenciones Art. 383 (Contabilidad)', self.documento_liquidacion_id)
        agregar('Otros documentos de Contabilidad', self.documento_cruce_id)
        agregar('Otros documentos de Contabilidad', self.articulos_id)
        agregar('Otros documentos de Contabilidad', self.reporte_liquidacion_id)
        agregar('Otros documentos de Contabilidad', self.documentos_adicionales_contabilidad_id)
        agregar(
            'Otros documentos de Contabilidad',
            self.documentos_adicionales_contabilidad_extra_ids.sorted('id'),
        )

        if self.clase_persona == 'persona_juridica':
            certificacion = (
                self.certificacion_conjunta_juridica_id
                or self._pdf_unificado_buscar_drive_por_nombres((
                    'certificación conjunta', 'certificacion conjunta',
                ))
            )
            factura_equivalente = (
                self.factura_electronica_juridica_id
                or self._pdf_unificado_buscar_drive_por_nombres((
                    'factura electrónica', 'factura electronica', 'factura',
                ))
            )
            informe = (
                self.informe_de_actividades_id
                or self._pdf_unificado_buscar_drive_por_nombres((
                    'informe de actividades', 'informe actividades',
                ))
            )

            agregar('Certificación conjunta', certificacion, categoria_core='certificacion_conjunta')
            agregar('Factura o documento equivalente', factura_equivalente, categoria_core='factura_documento_equivalente')
            agregar('Informe de actividades', informe, categoria_core='informe_actividades')
            agregar('Seguridad social', self.planilla_de_aportes_a_seguridad_social_ju_id, categoria_core='seguridad_social')
            agregar('RUT', self.rut_actualizado_juridica_id, categoria_core='rut')
            campos_adicionales = (
                'registro_proveedores_juridica_id',
                'resolucion_facturacion_electronica_juridica_id',
                'camara_comercio_juridica_id',
                'certificado_pago_parafiscales_juridica_id',
                'tarjeta_profesional_revisor_fiscal_juridica_id',
                'cedula_revisor_fiscal_juridica_id',
                'cedula_representante_legal_juridica_id',
                'cedula_representante_legal_de_contador_juridica_id',
                'Tarjeta_profesional_contador_juridica_id',
                'certificado_junta_contadores_juridica_id',
                'rut_representante_legal_juridica_id',
                'certificacion_bancaria_juridica_id',
                'anexo_tributario_juridica_id',
                'copia_contrato_juridica_id',
                'cedula_de_contador_juridica_id',
            )
        else:
            certificacion = (
                self.certificacion_conjunta_id
                or self._pdf_unificado_buscar_drive_por_nombres((
                    'certificación conjunta', 'certificacion conjunta',
                ))
            )
            factura_equivalente = (
                self.factura_id
                or self.documento_equivalente_id
                or self._pdf_unificado_buscar_drive_por_nombres((
                    'documento equivalente', 'factura',
                ))
            )
            informe = (
                self.informe_de_actividades_id
                or self._pdf_unificado_buscar_drive_por_nombres((
                    'informe de actividades', 'informe actividades',
                ))
            )

            agregar('Certificación conjunta', certificacion, categoria_core='certificacion_conjunta')
            agregar('Factura o documento equivalente', factura_equivalente, categoria_core='factura_documento_equivalente')
            agregar('Informe de actividades', informe, categoria_core='informe_actividades')
            agregar('Seguridad social', self.planilla_de_aportes_a_seguridad_social_id, categoria_core='seguridad_social')
            agregar('RUT', self.rut_actualizado_id, categoria_core='rut')
            campos_adicionales = (
                'declaracion_juramentada_y_soportes_id',
                'certificado_de_expensa_id',
                'copia_contrato_id',
                'copia_cedula_id',
                'certificacion_bancaria_id',
                'registro_de_proveedores_id',
                'resolucion_facturacion_electronica_id',
                'anexo_tributario_id',
            )

        for nombre_campo in campos_adicionales:
            if nombre_campo in self._fields:
                agregar(
                    'Demás documentos cargados por el contratista',
                    self[nombre_campo],
                    omitir_core_en_adicionales=True,
                )

        if self.origen_cuenta == 'regalias':
            agregar('Demás documentos cargados por el contratista', self.regalias_copia_cedula_id)
            agregar('Demás documentos cargados por el contratista', self.regalias_copia_contrato_id)

        agregar(
            'Demás documentos cargados por el contratista',
            self._get_portal_acta_document_records(
                'informes_contratista', 'informes_contratista_id'
            ).sorted('id'),
            omitir_core_en_adicionales=True,
        )
        agregar(
            'Demás documentos cargados por el contratista',
            self._get_portal_acta_document_records(
                'otro_documentos_de_informacion', 'otro_documentos_de_inf_id'
            ).sorted('id'),
            omitir_core_en_adicionales=True,
        )
        return documentos

    def _documentos_para_pdf_unificado(self):
        self.ensure_one()
        documentos = self._documentos_para_pdf_unificado_base()
        if not self.pdf_unificado_usar_orden_manual or not self.pdf_unificado_orden_manual:
            return documentos

        try:
            orden_ids = [int(valor) for valor in json.loads(self.pdf_unificado_orden_manual)]
        except Exception:
            return documentos

        por_id = {registro.id: (etiqueta, registro) for etiqueta, registro in documentos}
        ordenados = [por_id.pop(drive_id) for drive_id in orden_ids if drive_id in por_id]
        ordenados.extend(
            (etiqueta, registro)
            for etiqueta, registro in documentos
            if registro.id in por_id
        )
        return ordenados

    def action_ordenar_pdf_unificado(self):
        self.ensure_one()
        self._pdf_unificado_preparar_documentos_generados()
        documentos = self._documentos_para_pdf_unificado_base()
        if not documentos:
            return self.action_generar_pdf_unificado()

        wizard = self.env['acta.pdf.unificado.orden.wizard'].create({'acta_id': self.id})
        self.env['acta.pdf.unificado.orden.line'].create([
            {
                'wizard_id': wizard.id,
                'sequence': indice * 10,
                'etiqueta': etiqueta,
                'drive_id': registro.id,
                'incluir': True,
            }
            for indice, (etiqueta, registro) in enumerate(documentos, 1)
        ])
        return {
            'type': 'ir.actions.act_window',
            'name': 'Ordenar PDF unificado',
            'res_model': 'acta.pdf.unificado.orden.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ActaPdfUnificadoOrdenWizard(models.TransientModel):
    _name = 'acta.pdf.unificado.orden.wizard'
    _description = 'Orden manual del PDF unificado'

    acta_id = fields.Many2one('acta_entrega_documentos', required=True, readonly=True)
    line_ids = fields.One2many('acta.pdf.unificado.orden.line', 'wizard_id', string='Documentos')

    def action_aplicar_y_generar(self):
        self.ensure_one()
        lineas = self.line_ids.filtered('incluir').sorted(lambda linea: (linea.sequence, linea.id))
        if not lineas:
            return {'type': 'ir.actions.act_window_close'}
        self.acta_id.sudo().write({
            'pdf_unificado_orden_manual': json.dumps(lineas.mapped('drive_id').ids),
            'pdf_unificado_usar_orden_manual': True,
        })
        return self.acta_id.action_generar_pdf_unificado()

    def action_usar_orden_base(self):
        self.ensure_one()
        self.acta_id.sudo().write({
            'pdf_unificado_orden_manual': False,
            'pdf_unificado_usar_orden_manual': False,
        })
        return self.acta_id.action_generar_pdf_unificado()


class ActaPdfUnificadoOrdenLine(models.TransientModel):
    _name = 'acta.pdf.unificado.orden.line'
    _description = 'Documento del orden manual del PDF unificado'
    _order = 'sequence, id'

    wizard_id = fields.Many2one('acta.pdf.unificado.orden.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    incluir = fields.Boolean(default=True)
    etiqueta = fields.Char(readonly=True)
    drive_id = fields.Many2one('drive', string='Archivo', readonly=True)

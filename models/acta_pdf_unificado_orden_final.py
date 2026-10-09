# -*- coding: utf-8 -*-
from odoo import models


class ActaEntregaDocumentosPdfOrdenFinal(models.Model):
    _inherit = 'acta_entrega_documentos'

    _PDF_ORDEN_BASE_FINAL = {
        'Comprobante de pago contable (Tesorería)': 10,
        'Comprobante de pago banco (Tesorería)': 20,
        'Orden de pago contable (Auxiliar tesorería)': 30,
        'Causación contable (Contabilidad)': 40,
        'Documento de retenciones Art. 383 (Contabilidad)': 50,
        'Otros documentos de Contabilidad': 60,
        'Certificación conjunta': 70,
        'Factura o documento equivalente': 80,
        'Documento equivalente y declaración juramentada': 81,
        'Informe de actividades': 90,
        'Seguridad social': 100,
        'RUT': 110,
        'Demás documentos cargados por el contratista': 120,
    }

    def _pdf_unificado_documento_principal_factura_equivalente(self):
        """Devuelve (etiqueta, drive) para la factura/documento principal."""
        self.ensure_one()

        if self.clase_persona == 'persona_juridica':
            registro = self.factura_electronica_juridica_id if 'factura_electronica_juridica_id' in self._fields else False
            if registro and self._resolve_google_drive_file_id(registro):
                return 'Factura o documento equivalente', registro
            registro = self._pdf_unificado_buscar_drive_por_nombres((
                'factura electrónica', 'factura electronica',
            ))
            return ('Factura o documento equivalente', registro) if registro else (False, False)

        cuenta = self.cuenta_id
        tiene_factura = bool(cuenta and getattr(cuenta, 'factura_si_o_no', False))

        if tiene_factura:
            registro = self.factura_id if 'factura_id' in self._fields else False
            if registro and self._resolve_google_drive_file_id(registro):
                return 'Factura o documento equivalente', registro
            registro = self._pdf_unificado_buscar_drive_por_nombres(('factura',))
            return ('Factura o documento equivalente', registro) if registro else (False, False)

        if self.selecionar in ('servicios', 'bienes'):
            campos = ('declaracion_juramentada_y_soportes_id',)
        else:
            campos = ('documento_equivalente_id',)

        for field_name in campos:
            if field_name not in self._fields:
                continue
            registro = self[field_name]
            if registro and self._resolve_google_drive_file_id(registro):
                return 'Documento equivalente y declaración juramentada', registro

        registro = self._pdf_unificado_buscar_drive_por_nombres((
            'declaracion juramentada',
            'declaración juramentada',
            'documento equivalente',
        ))
        return (
            ('Documento equivalente y declaración juramentada', registro)
            if registro else (False, False)
        )

    def _pdf_unificado_documento_equivalente_adicional(self):
        """En persona natural con factura, incluir también el documento equivalente.

        factura_id es la factura. documento_equivalente_id es otro documento y no
        debe desaparecer cuando la factura esté activada.
        """
        self.ensure_one()
        if self.clase_persona != 'persona_natural':
            return False

        cuenta = self.cuenta_id
        tiene_factura = bool(cuenta and getattr(cuenta, 'factura_si_o_no', False))
        if not tiene_factura:
            return False

        if 'documento_equivalente_id' in self._fields:
            registro = self.documento_equivalente_id
            if registro and self._resolve_google_drive_file_id(registro):
                return registro

        return self._pdf_unificado_buscar_drive_por_nombres((
            'declaracion juramentada',
            'declaración juramentada',
            'documento equivalente',
        ))

    def _pdf_unificado_certificacion_conjunta_oficial(self):
        """Usa la Certificación conjunta del formulario, no otra variante."""
        self.ensure_one()
        campos = (
            ('certificacion_conjunta_juridica_id',)
            if self.clase_persona == 'persona_juridica'
            else ('certificacion_conjunta_id',)
        )
        for field_name in campos:
            if field_name in self._fields:
                registro = self[field_name]
                if registro and self._resolve_google_drive_file_id(registro):
                    return registro

        return self._pdf_unificado_buscar_drive_por_nombres((
            'certificación conjunta',
            'certificacion conjunta',
        ))

    def _documentos_para_pdf_unificado_base(self):
        self.ensure_one()
        documentos = super()._documentos_para_pdf_unificado_base()

        etiquetas_certificacion = {
            'Certificación conjunta',
            'Certificación conjunta firmada',
            'Certificación conjunta firmada (Auxiliar tesorería)',
        }
        etiquetas_factura_equivalente = {
            'Factura o documento equivalente',
            'Documento equivalente y declaración juramentada',
        }

        certificacion = self._pdf_unificado_certificacion_conjunta_oficial()
        etiqueta_principal, documento_principal = self._pdf_unificado_documento_principal_factura_equivalente()
        documento_equivalente_adicional = self._pdf_unificado_documento_equivalente_adicional()

        principales = [
            registro
            for registro in (certificacion, documento_principal, documento_equivalente_adicional)
            if registro
        ]
        ids_principales = {registro.id for registro in principales}
        file_ids_principales = {
            self._resolve_google_drive_file_id(registro)
            for registro in principales
            if self._resolve_google_drive_file_id(registro)
        }

        filtrados = []
        for etiqueta, registro in documentos:
            if etiqueta in etiquetas_certificacion or etiqueta in etiquetas_factura_equivalente:
                continue
            if registro and registro.id in ids_principales:
                continue
            if registro:
                file_id = self._resolve_google_drive_file_id(registro)
                if file_id and file_id in file_ids_principales:
                    continue
            filtrados.append((etiqueta, registro))
        documentos = filtrados

        if certificacion:
            documentos.append(('Certificación conjunta', certificacion))
        if documento_principal:
            documentos.append((etiqueta_principal, documento_principal))
        if documento_equivalente_adicional:
            documentos.append((
                'Documento equivalente y declaración juramentada',
                documento_equivalente_adicional,
            ))

        resultado = []
        ids_odoo = set()
        ids_drive = set()
        categorias_unicas = set()
        unicas = {
            'Certificación conjunta',
            'Factura o documento equivalente',
            'Documento equivalente y declaración juramentada',
            'Informe de actividades',
            'Seguridad social',
            'RUT',
        }

        for indice, (etiqueta, registro) in enumerate(documentos):
            if etiqueta in etiquetas_certificacion:
                etiqueta = 'Certificación conjunta'
            if not registro:
                continue

            file_id = self._resolve_google_drive_file_id(registro)
            if not file_id:
                continue
            if registro.id in ids_odoo or file_id in ids_drive:
                continue
            if etiqueta in unicas and etiqueta in categorias_unicas:
                continue

            resultado.append((
                self._PDF_ORDEN_BASE_FINAL.get(etiqueta, 120),
                indice,
                etiqueta,
                registro,
            ))
            ids_odoo.add(registro.id)
            ids_drive.add(file_id)
            if etiqueta in unicas:
                categorias_unicas.add(etiqueta)

        resultado.sort(key=lambda item: (item[0], item[1]))
        return [(etiqueta, registro) for _, _, etiqueta, registro in resultado]

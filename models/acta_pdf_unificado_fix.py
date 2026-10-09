# -*- coding: utf-8 -*-
import logging

from odoo import models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ActaEntregaDocumentosPdfFix(models.Model):
    _inherit = 'acta_entrega_documentos'

    _PDF_ORDEN_BASE = {
        'Comprobante de pago contable (Tesorería)': 10,
        'Comprobante de pago banco (Tesorería)': 20,
        'Orden de pago contable (Auxiliar tesorería)': 30,
        'Causación contable (Contabilidad)': 40,
        'Documento de retenciones Art. 383 (Contabilidad)': 50,
        'Otros documentos de Contabilidad': 60,
        'Certificación conjunta firmada (Auxiliar tesorería)': 70,
        'Factura o documento equivalente': 80,
        'Informe de actividades': 90,
        'Seguridad social': 100,
        'RUT': 110,
        'Demás documentos cargados por el contratista': 120,
    }

    def _pdf_unificado_buscar_drive_por_nombres(self, patrones):
        """Busca también por el acta para recuperar vínculos históricos incompletos."""
        self.ensure_one()
        drive_model = self.env['drive'].sudo()
        condiciones = []
        stage = self.drive_stage_provedor_id

        if stage:
            if 'stage4_id' in drive_model._fields:
                condiciones.append(('stage4_id', '=', stage.id))
            if stage.folder_id and 'folder_id' in drive_model._fields:
                condiciones.append(('folder_id', '=', str(stage.folder_id)))

        if 'contratistas_provedores' in drive_model._fields:
            condiciones.append(('contratistas_provedores', '=', self.id))

        if not condiciones:
            return drive_model.browse()

        if len(condiciones) == 1:
            domain = condiciones
        else:
            domain = (['|'] * (len(condiciones) - 1)) + condiciones

        candidatos = drive_model.search(domain, order='id desc')
        patrones_norm = tuple((patron or '').lower() for patron in patrones)
        for registro in candidatos:
            if self._pdf_unificado_es_generado(registro):
                continue
            nombre = self._pdf_unificado_nombre_normalizado(registro)
            if any(patron in nombre for patron in patrones_norm):
                if self._resolve_google_drive_file_id(registro):
                    return registro
        return drive_model.browse()

    def _pdf_unificado_primer_drive_valido(self, campos, patrones):
        """Primero usa el campo oficial y luego recupera el archivo desde Drive."""
        self.ensure_one()
        for field_name in campos:
            if field_name not in self._fields:
                continue
            registro = self[field_name]
            if registro and self._resolve_google_drive_file_id(registro):
                return registro
        return self._pdf_unificado_buscar_drive_por_nombres(patrones)

    def _pdf_unificado_es_certificacion_firmada(self, drive_record):
        self.ensure_one()
        if not drive_record:
            return False

        campos_certificacion_final = []
        for field_name in (
            'certificacion_conjunta_firmado_id',
            'certificacion_conjunta_firmado_juridica_id',
            'certificacion_conjunta_id',
            'certificacion_conjunta_juridica_id',
        ):
            if field_name in self._fields:
                registro = self[field_name]
                if registro:
                    campos_certificacion_final.append(registro.id)

        if drive_record.id in campos_certificacion_final:
            return True

        nombre = self._pdf_unificado_nombre_normalizado(drive_record)
        return (
            'certificacion conjunta firmad' in nombre
            or 'certificación conjunta firmad' in nombre
            or 'conjunta firmada' in nombre
            or 'certificacion conjunta' in nombre
            or 'certificación conjunta' in nombre
        )

    def _documentos_para_pdf_unificado_base(self):
        self.ensure_one()
        documentos = super()._documentos_para_pdf_unificado_base()

        if self.clase_persona == 'persona_juridica':
            certificacion = self._pdf_unificado_primer_drive_valido(
                ('certificacion_conjunta_firmado_juridica_id', 'certificacion_conjunta_juridica_id'),
                ('certificación conjunta firmada', 'certificacion conjunta firmada', 'conjunta firmada', 'certificación conjunta', 'certificacion conjunta'),
            )
            factura_equivalente = self._pdf_unificado_primer_drive_valido(
                ('factura_electronica_juridica_id',),
                ('factura electrónica', 'factura electronica', '1 factura electronica', 'factura'),
            )
        else:
            certificacion = self._pdf_unificado_primer_drive_valido(
                ('certificacion_conjunta_firmado_id', 'certificacion_conjunta_id'),
                ('certificación conjunta firmada', 'certificacion conjunta firmada', 'conjunta firmada', 'certificación conjunta', 'certificacion conjunta'),
            )
            factura_equivalente = self._pdf_unificado_primer_drive_valido(
                ('factura_id', 'documento_equivalente_id'),
                ('documento equivalente', 'documento_equivalente', 'factura'),
            )

        informe = self._pdf_unificado_primer_drive_valido(
            ('informe_de_actividades_id',),
            ('informe de actividades', 'informe actividades', 'informe_actividades'),
        )

        def reemplazar_categoria(etiqueta, registro):
            nonlocal documentos
            documentos = [(e, r) for e, r in documentos if e != etiqueta]
            if registro:
                documentos.append((etiqueta, registro))

        reemplazar_categoria('Certificación conjunta firmada (Auxiliar tesorería)', certificacion)
        reemplazar_categoria('Factura o documento equivalente', factura_equivalente)
        reemplazar_categoria('Informe de actividades', informe)

        resultado = []
        categorias_unicas = set()
        ids_drive = set()
        ids_odoo = set()
        categorias_solo_una = {
            'Documento de retenciones Art. 383 (Contabilidad)',
            'Certificación conjunta firmada (Auxiliar tesorería)',
            'Factura o documento equivalente',
            'Informe de actividades',
            'Seguridad social',
            'RUT',
        }

        for indice, (etiqueta, registro) in enumerate(documentos):
            if not registro:
                continue
            if etiqueta == 'Certificación conjunta firmada (Auxiliar tesorería)' and not self._pdf_unificado_es_certificacion_firmada(registro):
                continue
            file_id = self._resolve_google_drive_file_id(registro)
            if not file_id or registro.id in ids_odoo or file_id in ids_drive:
                continue
            if etiqueta in categorias_solo_una and etiqueta in categorias_unicas:
                continue
            resultado.append((self._PDF_ORDEN_BASE.get(etiqueta, 999), indice, etiqueta, registro))
            ids_odoo.add(registro.id)
            ids_drive.add(file_id)
            if etiqueta in categorias_solo_una:
                categorias_unicas.add(etiqueta)

        resultado.sort(key=lambda item: (item[0], item[1]))
        return [(etiqueta, registro) for _, _, etiqueta, registro in resultado]

    def action_ordenar_pdf_unificado(self):
        self.ensure_one()
        self.invalidate_cache(['pdf_unificado_estado', 'pdf_unificado_progreso', 'pdf_unificado_mensaje'])
        if self.pdf_unificado_estado == 'procesando':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PDF unificado'),
                    'message': _('Hay una generación en proceso. Espere a que termine antes de ordenar los documentos.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }
        documentos = self._documentos_para_pdf_unificado_base()
        if not documentos:
            raise UserError(_('La cuenta no tiene documentos disponibles para ordenar.'))
        wizard = self.env['acta.pdf.unificado.orden.wizard'].create({'acta_id': self.id})
        self.env['acta.pdf.unificado.orden.line'].create([
            {'wizard_id': wizard.id, 'sequence': indice * 10, 'etiqueta': etiqueta, 'drive_id': registro.id, 'incluir': True}
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

    def _refresh_documento_equivalente_drive_from_template(self):
        self.ensure_one()
        if self.estado == 'finalizado' and self.env.context.get('pdf_unificado_background'):
            return self.documento_equivalente_id
        return super()._refresh_documento_equivalente_drive_from_template()

    def _refresh_informe_de_actividades_drive_from_template(self):
        self.ensure_one()
        if self.estado == 'finalizado' and self.env.context.get('pdf_unificado_background'):
            return self.informe_de_actividades_id
        return super()._refresh_informe_de_actividades_drive_from_template()

    def _generar_pdf_unificado(self):
        """No permite que la limpieza del orden manual tape el error real."""
        self.ensure_one()
        usar_manual = bool(self.pdf_unificado_usar_orden_manual and self.pdf_unificado_orden_manual)
        error_generacion = None
        try:
            return super()._generar_pdf_unificado()
        except Exception as error:
            error_generacion = error
            raise
        finally:
            if usar_manual:
                if error_generacion and self.env.context.get('pdf_unificado_background'):
                    try:
                        self.env.cr.rollback()
                        self.invalidate_cache()
                    except Exception:
                        _logger.exception('No fue posible recuperar la transacción del PDF antes de limpiar el orden manual.')

                try:
                    self.sudo().with_context(
                        skip_drive_sync=True,
                        skip_integrity=True,
                        mail_notrack=True,
                        tracking_disable=True,
                    ).write({
                        'pdf_unificado_orden_manual': False,
                        'pdf_unificado_usar_orden_manual': False,
                    })
                    if self.env.context.get('pdf_unificado_background'):
                        self.env.cr.commit()
                except Exception:
                    if error_generacion:
                        _logger.exception('Falló la limpieza del orden manual; se conserva la excepción original.')
                    else:
                        raise

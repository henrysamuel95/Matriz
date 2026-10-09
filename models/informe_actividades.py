import os
import base64
import requests
from io import BytesIO
from openpyxl import load_workbook
from odoo import models, fields, api, _
from odoo.http import request
import tempfile
import uuid
from odoo.exceptions import UserError
from odoo.osv import expression
from docxtpl import DocxTemplate  # type: ignore
from datetime import datetime
from datetime import date, timedelta
import hashlib
from .decorador_firmas import seguimiento_firmas
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.merge import MergedCell
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import CellRange
from copy import copy
import textwrap
import locale
from docx import Document  # type: ignore
from docx.api import Document as DocumentApi  # type: ignore
from googleapiclient.errors import HttpError  # Asegúrate de tener google-api-python-client
from .drive import GoogleDriveAuth
import logging

_logger = logging.getLogger(__name__)


class InformeActividades(models.Model):
    _name = 'informe_actividades'
    _description = 'Informe de Actividades'
    _inherit = ['signature.mixin', 'mail.thread', 'mail.activity.mixin']

    def message_post(self, **kwargs):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        is_public_request = bool(
            request and hasattr(request, 'httprequest') and request.httprequest.path.startswith('/public/')
        )
        if not portal_login_id and is_public_request and len(self) == 1 and self.cuenta_cobro_id and self.cuenta_cobro_id.login_id:
            portal_login_id = self.cuenta_cobro_id.login_id.id
        if portal_login_id and not kwargs.get('author_id'):
            login = self.env['login'].sudo().browse(portal_login_id)
            if login.exists():
                partner = login._get_portal_chatter_partner()
                if partner:
                    kwargs['author_id'] = partner.id
        return super(InformeActividades, self).message_post(**kwargs)

    def _reassign_portal_tracking_author(self, since):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        is_public_request = bool(
            request and hasattr(request, 'httprequest') and request.httprequest.path.startswith('/public/')
        )
        if not portal_login_id and is_public_request and len(self) == 1 and self.cuenta_cobro_id and self.cuenta_cobro_id.login_id:
            portal_login_id = self.cuenta_cobro_id.login_id.id
        if not portal_login_id:
            return

        login = self.env['login'].sudo().browse(portal_login_id)
        if not login.exists():
            return
        partner = login._get_portal_chatter_partner()
        if not partner:
            return

        messages = self.env['mail.message'].sudo().search([
            ('model', '=', self._name),
            ('res_id', 'in', self.ids),
            ('date', '>=', since - timedelta(seconds=3)),
            ('author_id', '!=', partner.id),
        ])
        if messages:
            messages.write({'author_id': partner.id})

    _SIGNATURE_FIELDS = {
        'firma_contratista_img',
        'firma_contratista_check',
        'firma_contratista_hash',
        'contratista_log_id',
        'firma_coordinador_img',
        'firma_coordinador_check',
        'firma_coordinador_hash',
        'coordinador_log_id',
    }
    _GENERATED_DOCUMENT_FIELDS = {
        'informe_de_actividades_doc',
        'informe_de_actividades_doc_name',
    }

    relacion_id = fields.Many2one('acta_entrega_documentos', string='Relación')
    cuenta_cobro_id = fields.Many2one('cuentas_de_cobros', string='Cuenta de Cobro Vinculada')

    drive_stage_provedor_id = fields.Many2one('cuenta_de_cobros', string='Drive cuenta de cobros', related='relacion_id.drive_stage_provedor_id')


    # --- CAMPO DINÁMICO ---
    
    # Campo para el número de informe, se maneja con la secuencia (INF0001, etc.)
    name = fields.Char(string='Nombre del Contratista')
    consecutivo = fields.Char(string='Consecutivo')

    
    # --- FECHA DE ACTIVIDAD (Son los campos que el usuario llena) ---
    actividad_dia = fields.Char(string='Día de Actividad')
    actividad_mes = fields.Char(string='Mes de Actividad')
    actividad_anio = fields.Char(string='Año de Actividad')

    
    # --- INFORMACIÓN GENERAL ---
    nombre_proyecto = fields.Many2one('contratacion', string='Nombre del Proyecto', related="relacion_id.proyecto_relacionado")
    no_contrato = fields.Char(string='No. Contrato', related="relacion_id.id_del_contrato")
    no_contrato2 = fields.Char(string='No. Contrato')
    es_cuenta_regalias = fields.Boolean(
        string='Es cuenta de Regalías',
        compute='_compute_numero_contrato_regalias',
    )
    no_contrato_regalias = fields.Char(
        string='No. Contrato Regalías',
        compute='_compute_numero_contrato_regalias',
    )

    @api.depends(
        'cuenta_cobro_id.origen_cuenta',
        'cuenta_cobro_id.contrato_regalias_id.numero_de_contrato',
        'relacion_id.cuenta_id.origen_cuenta',
        'relacion_id.cuenta_id.contrato_regalias_id.numero_de_contrato',
        'relacion_id.contrato_regalias_id.numero_de_contrato',
    )
    def _compute_numero_contrato_regalias(self):
        for record in self:
            cuenta = record.cuenta_cobro_id or record.relacion_id.cuenta_id
            es_regalias = bool(cuenta and cuenta.origen_cuenta == 'regalias')
            contrato_regalias = False
            if es_regalias:
                contrato_regalias = (
                    cuenta.contrato_regalias_id
                    or record.relacion_id.contrato_regalias_id
                )
            record.es_cuenta_regalias = es_regalias
            record.no_contrato_regalias = (
                contrato_regalias.numero_de_contrato if contrato_regalias else False
            )

    selecionar = fields.Selection([
        ('servicios', 'Orden de prestación de servicios'),
        ('bienes', 'Orden de compra de bienes'),
        ('contrato', 'Contrato'),
        ('otrosi', 'Otrosí'),
    ], string='Tipo', related='relacion_id.selecionar',)

    objeto_contrato_manual = fields.Text(string='Objeto del Contrato Manual')

    # Campo Objeto del Contrato:
    # - Si el contratista lo escribe en el portal, se guarda en objeto_contrato_manual.
    # - Si no escribe nada, se toma automáticamente desde la cuenta de cobro o el acta.
    objeto_contrato = fields.Text(
        string='Objeto del Contrato', 
        compute='_compute_objeto_contrato', 
        inverse='_inverse_objeto_contrato',
        readonly=False
    )

    @api.depends(
        'objeto_contrato_manual',
        'relacion_id',
        'cuenta_cobro_id',
        'cuenta_cobro_id.objeto_contrato',
        'relacion_id.objeto_contrato'
    )
    def _compute_objeto_contrato(self):
        for record in self:
            if record.objeto_contrato_manual:
                record.objeto_contrato = record.objeto_contrato_manual
                continue

            # Inicializamos vacío
            valor_encontrado = ""

            # 1. Intentar traer de la Cuenta de Cobro (Prioridad)
            if record.cuenta_cobro_id and record.cuenta_cobro_id.objeto_contrato:
                valor_encontrado = record.cuenta_cobro_id.objeto_contrato
            
            # 2. Si sigue vacío, intentar traer del Acta
            elif record.relacion_id and record.relacion_id.objeto_contrato:
                valor_encontrado = record.relacion_id.objeto_contrato
            
            record.objeto_contrato = valor_encontrado

    def _inverse_objeto_contrato(self):
        for record in self:
            record.objeto_contrato_manual = record.objeto_contrato or False
    
    # --- DESCRIPCIÓN DE ACTIVIDADES ---
    descripcion_actividades = fields.Text(string='Descripción de las Actividades')
    
    
    # --- OBSERVACIONES ---
    observaciones = fields.Text(string='Observaciones')


    # ==========================================
    #   MÉTODO LÓGICO DE REEMPLAZO (TEMPLATE)
    # ==========================================
    def _split_text_in_lines(self, text, chars_per_line=120):
        lines = []
        for paragraph in (text or "").split('\n'):
            if paragraph.strip():
                wrapped = textwrap.wrap(paragraph.strip(), width=chars_per_line)
                lines.extend(wrapped)
            else:
                lines.append("")
        return lines or [""]

    def _cell_range_intersects(self, existing_range, start_row, end_row, start_col, end_col):
        return not (
            existing_range.max_row < start_row
            or existing_range.min_row > end_row
            or existing_range.max_col < start_col
            or existing_range.min_col > end_col
        )

    def _merge_text_row(self, sheet, row_idx, start_col, end_col):
        if not end_col or end_col <= start_col:
            return

        range_text = "%s%s:%s%s" % (
            get_column_letter(start_col),
            row_idx,
            get_column_letter(end_col),
            row_idx,
        )
        for merged_range in sheet.merged_cells.ranges:
            intersects = self._cell_range_intersects(
                merged_range,
                row_idx,
                row_idx,
                start_col,
                end_col,
            )
            if intersects and str(merged_range) != range_text:
                return
            if str(merged_range) == range_text:
                return

        sheet.merge_cells(range_text)

    def _write_text_in_rows(self, sheet, start_row, col_idx, text, chars_per_line=120, merge_until_col=None):
        """Escribe texto largo distribuyéndolo en filas hacia abajo"""
        lines = self._split_text_in_lines(text, chars_per_line=chars_per_line)
        for i, line in enumerate(lines):
            row_idx = start_row + i
            if merge_until_col:
                self._merge_text_row(sheet, row_idx, col_idx, merge_until_col)
            target_cell = sheet.cell(row=row_idx, column=col_idx)
            # ✅ Saltar celdas fusionadas secundarias (son read-only)
            if isinstance(target_cell, MergedCell):
                continue
            target_cell.value = line
            target_cell.alignment = Alignment(vertical='top', horizontal='left', wrap_text=True)
            target_cell.font = Font(bold=False)
            sheet.row_dimensions[start_row + i].height = 20

    def _numero_contrato_para_plantilla(self):
        """En Regalías usa el contrato oficial de su base de datos."""
        self.ensure_one()
        cuenta = self.cuenta_cobro_id or self.relacion_id.cuenta_id
        if cuenta and cuenta.origen_cuenta == 'regalias':
            contrato_regalias = (
                cuenta.contrato_regalias_id
                or self.relacion_id.contrato_regalias_id
            )
            if contrato_regalias and contrato_regalias.numero_de_contrato:
                return contrato_regalias.numero_de_contrato
        return self.no_contrato2 or self.no_contrato or ""

    def _format_wrapped_header_field(self, sheet, cell, value, end_col=10, chars_per_line=85):
        """Mantiene los textos variables del encabezado dentro de la tabla."""
        self._merge_text_row(sheet, cell.row, cell.column, end_col)
        cell.value = str(value or "")
        cell.alignment = copy(cell.alignment)
        cell.alignment = Alignment(
            horizontal=cell.alignment.horizontal or 'left',
            vertical='center',
            wrap_text=True,
        )
        line_count = len(self._split_text_in_lines(cell.value, chars_per_line=chars_per_line))
        current_height = sheet.row_dimensions[cell.row].height or 15
        sheet.row_dimensions[cell.row].height = max(current_height, 15 * max(line_count, 1))

    def _copy_row_style(self, sheet, source_row, target_row):
        for col_idx in range(1, sheet.max_column + 1):
            source_cell = sheet.cell(row=source_row, column=col_idx)
            target_cell = sheet.cell(row=target_row, column=col_idx)
            if source_cell.has_style:
                target_cell._style = copy(source_cell._style)
            if source_cell.number_format:
                target_cell.number_format = source_cell.number_format
            if source_cell.protection:
                target_cell.protection = copy(source_cell.protection)
            if source_cell.alignment:
                target_cell.alignment = copy(source_cell.alignment)
        sheet.row_dimensions[target_row].height = sheet.row_dimensions[source_row].height

    def _insert_rows_preserving_merges(self, sheet, row_idx, amount):
        merged_ranges = list(sheet.merged_cells.ranges)
        shifted_ranges = []

        for merged_range in merged_ranges:
            range_text = str(merged_range)
            if merged_range.min_row >= row_idx:
                sheet.unmerge_cells(range_text)
                shifted = CellRange(range_text)
                shifted.shift(row_shift=amount, col_shift=0)
                shifted_ranges.append(str(shifted))

        sheet.insert_rows(row_idx, amount=amount)

        for range_text in shifted_ranges:
            sheet.merge_cells(range_text)

    def _ensure_activity_rows_fit_before_row(self, sheet, activity_row, limit_row, lines_needed):
        if not activity_row or not limit_row:
            return 0
        available_rows = max(limit_row - activity_row, 1)
        extra_rows = lines_needed - available_rows
        if extra_rows <= 0:
            return 0

        style_source_row = limit_row - 1
        self._insert_rows_preserving_merges(sheet, limit_row, extra_rows)
        for row_idx in range(limit_row, limit_row + extra_rows):
            self._copy_row_style(sheet, style_source_row, row_idx)
        return extra_rows

    def render_informe_template(self):
        if not load_workbook:
            raise UserError("La librería openpyxl no está instalada.")

        module_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        templates_dir = os.path.join(module_dir, "templates")
        template_path = os.path.join(templates_dir, "informe_de_actividades_adicion.xlsx")
        
        workbook = load_workbook(template_path)
        sheet = workbook.active

        placeholders = {
            "{{actividad_anio}}": str(self.actividad_anio or ""),
            "{{actividad_mes}}": str(self.actividad_mes or ""),
            "{{actividad_dia}}": str(self.actividad_dia or ""),
            "{{name}}": str(self.name or ""),
            "{{nombre_proyecto}}": str(self.nombre_proyecto.display_name if self.nombre_proyecto else ""),
            "{{no_contrato}}": str(self._numero_contrato_para_plantilla()),
            "{{firma_coordinador_hash}}": str(self.firma_coordinador_hash or ""),
            "{{firma_coordinador_nombre}}": self._texto_nombre_usuario_firma(self.coordinador_log_id),
            "{{firma_coordinador_documento}}": self._texto_documento_usuario_firma(self.coordinador_log_id),
            "{{firma_coordinador_fecha}}": self._texto_fecha_firma(self.coordinador_log_id),
            "{{firma_contratista_hash}}": str(self.firma_contratista_hash or ""),
            "{{firma_contratista_nombre}}": str(self.name or ""),
            "{{firma_contratista_documento}}": self._texto_documento_acta_firma(),
            "{{firma_contratista_fecha}}": self._texto_fecha_firma(self.contratista_log_id),
            "{{consecutivo}}": str(self.consecutivo or ""),
        }

        lista_actividades = [
            a.strip() for a in (self.descripcion_actividades or "").split('\n')
            if a.strip()
        ]
        objeto_chars_per_line = 95
        actividad_chars_per_line = 105
        observaciones_chars_per_line = 100
        object_row = None
        activity_row = None
        observations_row = None
        signature_row = None
        object_lines_needed = 0
        activity_lines_needed = 0
        observation_lines_needed = 0

        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell) or not isinstance(cell.value, str):
                    continue
                if "{{dobjeto_contratos}}" in cell.value:
                    object_row = cell.row
                    object_lines_needed = len(
                        self._split_text_in_lines(
                            self.objeto_contrato or "",
                            chars_per_line=objeto_chars_per_line,
                        )
                    ) or 1
                if "{{descripcion_actividades}}" in cell.value:
                    activity_row = cell.row
                    for actividad in lista_actividades:
                        activity_lines_needed += len(self._split_text_in_lines(actividad, chars_per_line=actividad_chars_per_line))
                    activity_lines_needed = activity_lines_needed or 1
                if "{{observaciones}}" in cell.value:
                    observations_row = cell.row
                    observation_lines_needed = len(
                        self._split_text_in_lines(
                            self.observaciones or "",
                            chars_per_line=observaciones_chars_per_line,
                        )
                    )
                if "{{firma_contratista_hash}}" in cell.value or "{{firma_coordinador_hash}}" in cell.value:
                    signature_row = min(signature_row or cell.row, cell.row)

        object_limit_row = activity_row - 1 if activity_row else observations_row or signature_row
        object_extra_rows = self._ensure_activity_rows_fit_before_row(
            sheet,
            object_row,
            object_limit_row,
            object_lines_needed,
        )
        if object_extra_rows:
            if activity_row:
                activity_row += object_extra_rows
            if observations_row:
                observations_row += object_extra_rows
            if signature_row:
                signature_row += object_extra_rows

        activity_extra_rows = self._ensure_activity_rows_fit_before_row(
            sheet,
            activity_row,
            observations_row or signature_row,
            activity_lines_needed,
        )
        if activity_extra_rows:
            if observations_row:
                observations_row += activity_extra_rows
            if signature_row:
                signature_row += activity_extra_rows

        if observations_row and signature_row:
            observation_extra_rows = self._ensure_activity_rows_fit_before_row(
                sheet,
                observations_row,
                signature_row,
                observation_lines_needed or 1,
            )
            # Las firmas se desplazan cuando las observaciones necesitan más
            # filas. Mantener la posición anterior hacía que el área de
            # impresión terminara antes de la firma y cortara la parte baja.
            signature_row += observation_extra_rows

        for row_idx, row in enumerate(sheet.iter_rows(), start=1):
            for cell in row:
                # ✅ Saltar MergedCell en el loop principal también
                if isinstance(cell, MergedCell):
                    continue

                if cell.value and isinstance(cell.value, str):
                    original_value = cell.value
                    # Reemplazos normales
                    for placeholder, value in placeholders.items():
                        if placeholder in cell.value:
                            cell.value = cell.value.replace(placeholder, str(value))

                    # Estos valores ocupan desde la columna D hasta la J. Al
                    # combinarlos y envolverlos se evita que un nombre largo
                    # invada o desaparezca fuera del borde derecho del formato.
                    if "{{name}}" in original_value:
                        self._format_wrapped_header_field(
                            sheet, cell, placeholders["{{name}}"], chars_per_line=85,
                        )
                    elif "{{nombre_proyecto}}" in original_value:
                        self._format_wrapped_header_field(
                            sheet, cell, placeholders["{{nombre_proyecto}}"], chars_per_line=85,
                        )
                    elif "{{no_contrato}}" in original_value:
                        self._format_wrapped_header_field(
                            sheet, cell, placeholders["{{no_contrato}}"], chars_per_line=85,
                        )

                    if "{{firma_contratista_hash}}" in original_value:
                        self._aplicar_formato_bloque_firma(
                            cell,
                            placeholders["{{firma_contratista_hash}}"],
                            placeholders["{{firma_contratista_nombre}}"],
                            placeholders["{{firma_contratista_documento}}"],
                            placeholders["{{firma_contratista_fecha}}"],
                        )
                    elif "{{firma_coordinador_hash}}" in original_value:
                        self._aplicar_formato_bloque_firma(
                            cell,
                            placeholders["{{firma_coordinador_hash}}"],
                            placeholders["{{firma_coordinador_nombre}}"],
                            placeholders["{{firma_coordinador_documento}}"],
                            placeholders["{{firma_coordinador_fecha}}"],
                        )

                    # Objeto del contrato
                    if isinstance(cell.value, str) and "{{dobjeto_contratos}}" in cell.value:
                        col_idx = cell.column
                        cell.value = ""
                        objeto_text = str(self.objeto_contrato or "")
                        self._write_text_in_rows(
                            sheet,
                            row_idx,
                            col_idx,
                            objeto_text,
                            chars_per_line=objeto_chars_per_line,
                            merge_until_col=10,
                        )

                    # Observaciones
                    if isinstance(cell.value, str) and "{{observaciones}}" in cell.value:
                        col_idx = cell.column
                        cell.value = ""
                        obs_text = str(self.observaciones or "")
                        self._write_text_in_rows(
                            sheet,
                            row_idx,
                            col_idx,
                            obs_text,
                            chars_per_line=observaciones_chars_per_line,
                            merge_until_col=10,
                        )

                    # Actividades
                    if isinstance(cell.value, str) and "{{descripcion_actividades}}" in cell.value:
                        col_idx = cell.column
                        cell.value = ""
                        current_row = row_idx

                        for actividad in lista_actividades:
                            self._write_text_in_rows(
                                sheet,
                                current_row,
                                col_idx,
                                actividad,
                                chars_per_line=actividad_chars_per_line,
                                merge_until_col=10,
                            )
                            lines_used = len(self._split_text_in_lines(actividad, chars_per_line=actividad_chars_per_line)) or 1
                            current_row += lines_used

        # Evita que al convertir el informe a PDF se incluyan filas sobrantes,
        # notas internas o páginas en blanco de la plantilla. Si el objeto,
        # actividades u observaciones insertaron filas, signature_row ya fue
        # desplazado arriba y el área de impresión conserva todo lo útil.
        # El borde que cierra la plantilla está cuatro filas después del
        # inicio de las firmas. Terminar en ``signature_row + 2`` ocultaba ese
        # borde y hacía que la parte inferior de la página pareciera cortada.
        ultima_fila_util = max(signature_row + 4 if signature_row else 32, 32)
        sheet.print_area = f"A1:J{ultima_fila_util}"
        for row_number in range(ultima_fila_util + 1, sheet.max_row + 1):
            sheet.row_dimensions[row_number].hidden = True
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        # La plantilla fue diseñada en vertical y debe conservarse completa en
        # una sola página; el ajuste de alto evita separar firmas o notas.
        sheet.page_setup.orientation = sheet.ORIENTATION_PORTRAIT
        sheet.page_setup.paperSize = sheet.PAPERSIZE_LETTER
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        sheet.page_margins.left = 0.25
        sheet.page_margins.right = 0.25
        sheet.page_margins.top = 0.35
        sheet.page_margins.bottom = 0.35
        sheet.print_options.horizontalCentered = True

        output_filename = f"temp_informe_actividades{self.id}.xlsx"
        output_path = os.path.join(templates_dir, output_filename)
        workbook.save(output_path)
        return output_path

    def _texto_fecha_firma(self, log_firma):
        if not log_firma or not log_firma.signature_date:
            return ""
        fecha = fields.Datetime.context_timestamp(self, log_firma.signature_date)
        meses = {
            1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
            5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
            9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
        }
        return f"{meses.get(fecha.month, '')} {fecha.day} de {fecha.year} a las {fecha.strftime('%I:%M %p').lower()}"

    def _texto_documento_acta_firma(self):
        if not self.relacion_id or not self.relacion_id.documento_de_identificacion:
            return ""
        return f"Documento de identidad: {self.relacion_id.documento_de_identificacion}"

    def _texto_nombre_usuario_firma(self, log_firma):
        usuario = log_firma.signature_user_id if log_firma else False
        return usuario.name if usuario else ""

    def _texto_documento_usuario_firma(self, log_firma):
        usuario = log_firma.signature_user_id if log_firma else False
        documento = usuario.partner_id.vat if usuario and usuario.partner_id else ""
        return f"Documento de identidad: {documento}" if documento else ""

    def _aplicar_formato_bloque_firma(self, cell, firma_hash, nombre, documento, fecha):
        hash_lineas = textwrap.wrap(str(firma_hash or ""), width=44)
        texto_hash = "\n".join(
            ["SHA/FHA Hash: " + hash_lineas[0], *hash_lineas[1:]]
            if hash_lineas
            else []
        )
        # Los rótulos de cada firmante ya están impresos en la plantilla
        # (fila 30). Incluirlos también aquí duplicaba "Contratista" y
        # "Vo.Bo. Coordinador..." dentro del bloque dinámico de la firma.
        texto = "\n".join(linea for linea in [texto_hash, nombre, documento, fecha] if linea)
        sheet = cell.parent
        sheet.row_dimensions[cell.row].height = max(sheet.row_dimensions[cell.row].height or 0, 34)
        sheet.row_dimensions[cell.row + 1].height = max(sheet.row_dimensions[cell.row + 1].height or 0, 34)
        try:
            from openpyxl.cell.rich_text import CellRichText, TextBlock
            from openpyxl.cell.text import InlineFont
        except Exception:
            cell.value = texto
            return

        bloques = []
        if texto_hash:
            bloques.append(TextBlock(InlineFont(rFont="Tahoma", sz=6, b=True, color="000000"), texto_hash))
            bloques.append("\n")
        if nombre:
            bloques.append(TextBlock(InlineFont(rFont="Tahoma", sz=9, b=True, color="000000"), nombre))
            bloques.append("\n")
        if documento:
            bloques.append(TextBlock(InlineFont(rFont="Tahoma", sz=8, b=True, color="000000"), documento))
            bloques.append("\n")
        if fecha:
            bloques.append(TextBlock(InlineFont(rFont="Tahoma", sz=8, b=True, color="000000"), fecha))
            bloques.append("\n")
        cell.value = CellRichText(bloques)

    def generar_informe_xlsx(self):
        self.ensure_one()
        _logger.info(f"--- INICIO GENERAR INFORME PARA: {self.name} ---")
        
        try:
            # 1. Renderizar y crear binario
            template_rendered = self.render_informe_template()
            
            with open(template_rendered, 'rb') as f:
                file_data = base64.b64encode(f.read())

            filename = f"informe_actividades_{self.name.replace(' ', '_')}.xlsx"
            
            self.write({
                'informe_de_actividades_doc': file_data,
                'informe_de_actividades_doc_name': filename,
            })
            _logger.info("--- PASO 1: Archivo generado en informe_actividades ---")

            # 2. Obtener el Acta directamente
            # Como relacion_id es un Many2one a 'acta_entrega_documentos', ¡esto YA ES EL ACTA!
            acta_record = self.relacion_id.with_context(
                silent_informe_vobo_update=self.env.context.get('silent_informe_vobo_update'),
                mail_create_nosubscribe=self.env.context.get('mail_create_nosubscribe'),
                mail_notrack=self.env.context.get('mail_notrack'),
                tracking_disable=self.env.context.get('tracking_disable'),
                skip_vobo_reset=True,  # <--- ESTA LÍNEA EVITA EL RESETEO
            )
            
            if acta_record:
                _logger.info(f"--- PASO 2: Acta ID {acta_record.id} ENCONTRADA directamente ---")
                
                # 3. Escribir archivo en el Acta
                acta_record.write({
                    'informe_de_actividades_documento': file_data,
                    'informe_de_actividades_name': filename,
                })
                _logger.info("--- PASO 3: Archivo escrito exitosamente en el Acta ---")
                
                # 4. Preparar los cambios sin romper la transacción HTTP. Drive
                # recibe el binario directamente; no necesita un commit previo.
                self.env.flush_all()
                _logger.info("--- PASO 4: Cambios preparados en la transacción ---")

                # 5. Subida al Drive
                if acta_record.informe_de_actividades_documento:
                    _logger.info("--- PASO 5: Subiendo a Drive desde el Acta... ---")
                    try:
                        upload_ok = acta_record._upload_informe_de_actividades_to_drive()
                        if not upload_ok:
                            raise UserError(
                                "No fue posible actualizar el informe de actividades en Drive. "
                                "Inténtelo nuevamente."
                            )
                        _logger.info("--- ÉXITO TOTAL: Subida Finalizada ---")
                    except Exception as e_drive:
                        if getattr(e_drive, 'pgcode', None) in ('40001', '40P01', '55P03'):
                            raise
                        _logger.exception("--- ERROR AL SUBIR A DRIVE: %s ---", e_drive)
                        if self.env.context.get('raise_document_generation_error'):
                            if isinstance(e_drive, UserError):
                                raise
                            raise UserError(
                                "No fue posible actualizar el informe de actividades en Drive. "
                                "Inténtelo nuevamente."
                            ) from e_drive
                else:
                    _logger.warning("--- ADVERTENCIA: No hay documento binario para subir a Drive ---")
            else:
                _logger.error("--- ERROR CRÍTICO: Este informe no tiene un relacion_id vinculado (El Acta está vacía) ---")

            # 6. Limpieza del archivo temporal local
            if os.path.exists(template_rendered):
                os.remove(template_rendered)

            return True

        except Exception as e:
            if getattr(e, 'pgcode', None) in ('40001', '40P01', '55P03'):
                raise
            _logger.exception("--- ERROR GENERAL EN GENERAR EXCEL: %s ---", e)
            if self.env.context.get('raise_document_generation_error'):
                if isinstance(e, UserError):
                    raise
                raise UserError(
                    "No fue posible generar o subir el informe de actividades. "
                    "Inténtelo nuevamente."
                ) from e
            return False
        
    informe_de_actividades_doc = fields.Binary(string='Informe de actividades')
    informe_de_actividades_doc_name = fields.Char(string='Informe de actividades')

    # ==========================================
    #       VARIABLES PARA LAS FIRMAS
    # ==========================================
    
    contratista_log_id = fields.Many2one('signature.mixin')
    coordinador_log_id = fields.Many2one('signature.mixin')

    firma_contratista_img = fields.Binary(string='Firma Contratista')
    firma_contratista_check = fields.Boolean(string='Aceptación')
    firma_contratista_hash = fields.Char(readonly=True)

    firma_coordinador_img = fields.Binary(string='Firma Coordinador')
    firma_coordinador_check = fields.Boolean(string='Vo.Bo.')
    firma_coordinador_hash = fields.Char(readonly=True)

    def write(self, vals):
        vals = dict(vals)
        since = fields.Datetime.now()
        result = self._write_con_seguimiento_firmas(vals)
        self._reassign_portal_tracking_author(since)
        return result

    # Trazabilidad de firmas (ver seguimiento_firmas en decorador_firmas.py):
    # 2 slots — Contratista y Coordinador. Este helper es llamado desde
    # write() (arriba) DESPUÉS de _preparar_refirma_si_cambio_contenido,
    # que puede resetear check/log/hash a False si el contenido cambió tras
    # una firma previa (ver esa función más abajo).
    @seguimiento_firmas([
        {
            'name': 'Contratista',
            'img': 'firma_contratista_img',
            'check': 'firma_contratista_check',
            'hash': 'firma_contratista_hash',
            'log': 'contratista_log_id'
        },
        {
            'name': 'Coordinador',
            'img': 'firma_coordinador_img',
            'check': 'firma_coordinador_check',
            'hash': 'firma_coordinador_hash',
            'log': 'coordinador_log_id'
        }
    ])
    def _write_con_seguimiento_firmas(self, vals):
        return super(InformeActividades, self).write(vals)

    def _preparar_refirma_si_cambio_contenido(self, vals):
        if self.env.context.get('skip_refirma_por_cambio'):
            return
        if not self._tiene_firma_activa_previa():
            return
        campos_contenido = set(vals) - self._SIGNATURE_FIELDS - self._GENERATED_DOCUMENT_FIELDS
        campos_contenido = {campo for campo in campos_contenido if campo in self._fields}
        if not campos_contenido or not self._hay_cambio_real_en_campos(vals, campos_contenido):
            return

        self._agregar_reset_firmas_a_vals(vals)

    def _tiene_firma_activa_previa(self):
        for record in self:
            if (
                record.firma_contratista_check
                or record.contratista_log_id
                or record.firma_contratista_hash
                or record.firma_coordinador_check
                or record.coordinador_log_id
                or record.firma_coordinador_hash
            ):
                return True
        return False

    def _hay_cambio_real_en_campos(self, vals, campos):
        for record in self:
            for campo in campos:
                nuevo = vals.get(campo)
                actual = record[campo]
                field = self._fields[campo]
                if field.type == 'many2one':
                    actual = actual.id or False
                elif field.type in ('one2many', 'many2many'):
                    return True
                if (nuevo or False) != (actual or False):
                    return True
        return False

    def _agregar_reset_firmas_a_vals(self, vals):
        pares = [
            ('firma_contratista_check', 'contratista_log_id', 'firma_contratista_hash'),
        ]
        for check, log, hash_field in pares:
            vals.setdefault(check, False)
            vals[log] = False
            vals[hash_field] = False
        self.message_post(
            body=_(
                "⚠️ Se modificó información del informe. Las firmas anteriores quedan sin efecto y se requiere firmar nuevamente."
            ),
            subtype_xmlid="mail.mt_note",
        )

    def action_requerir_nueva_firma_por_correccion(self):
        for record in self:
            vals = {}
            record._agregar_reset_firmas_a_vals(vals)
            record.with_context(skip_refirma_por_cambio=True)._write_con_seguimiento_firmas(vals)

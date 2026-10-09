#!/usr/bin/python
import time

import PyPDF2
from dateutil.relativedelta import relativedelta
from google.auth import default
from num2words import num2words
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError
import uuid
from datetime import datetime, timedelta
import os
import re
from odoo.modules.module import get_module_resource
from .drive import GoogleDriveAuth
import tempfile
from odoo.exceptions import AccessError  # type: ignore
from attr import exceptions
from odoo.exceptions import UserError  # type: ignore
from odoo.tools import html2plaintext, html_escape
import base64
from io import BytesIO
from docx import Document  # type: ignore
from docx.api import Document as DocumentApi  # type: ignore
from docxtpl import DocxTemplate  # type: ignore
from odoo import exceptions
import threading
import queue
import logging

_logger = logging.getLogger(__name__)


class TablaFormaPagoOtroSi(models.Model):
    _name = 'tabla_forma_pago_otrosi'
    _description = 'Tabla de forma de pago del otrosí'
    _order = 'numero_de_pagos, id'

    otrosi_tabla_id = fields.Many2one(
        'otro_si',
        string='Otrosí',
        required=True,
        ondelete='cascade',
    )
    numero_de_pagos = fields.Integer(string='# PAGOS', readonly=True, copy=False)
    cantidad_de_dinero = fields.Float(string='CANTIDAD DE DINERO')
    pago = fields.Float(string='% DE PAGO', compute='_compute_porcentaje_pago')
    porcentaje_acumulado = fields.Float(
        string='% ACUMULADO',
        compute='_compute_porcentaje_acumulado',
    )
    tecnico = fields.Float(string='% TÉCNICO')
    condicion = fields.Char(string='CONDICIÓN')
    seleccionado = fields.Boolean(string='Seleccionado')

    @api.depends('cantidad_de_dinero', 'otrosi_tabla_id.valor_total_contrato')
    def _compute_porcentaje_pago(self):
        for linea in self:
            total = linea.otrosi_tabla_id.valor_total_contrato or 0.0
            linea.pago = (linea.cantidad_de_dinero / total * 100.0) if total else 0.0

    @api.depends(
        'cantidad_de_dinero',
        'numero_de_pagos',
        'otrosi_tabla_id.valor_total_contrato',
        'otrosi_tabla_id.tabla_de_forma_de_pago.cantidad_de_dinero',
    )
    def _compute_porcentaje_acumulado(self):
        for linea in self:
            total = linea.otrosi_tabla_id.valor_total_contrato or 0.0
            acumulado = 0.0
            if total:
                lineas = linea.otrosi_tabla_id.tabla_de_forma_de_pago
                for item in lineas:
                    acumulado += (item.cantidad_de_dinero or 0.0) / total * 100.0
                    if item == linea:
                        break
            linea.porcentaje_acumulado = acumulado

    def _renumerar_lineas(self):
        padres = self.mapped('otrosi_tabla_id')
        for padre in padres:
            for numero, linea in enumerate(
                padre.tabla_de_forma_de_pago.sorted(key=lambda item: item.id),
                start=1,
            ):
                if linea.numero_de_pagos != numero:
                    linea.numero_de_pagos = numero

    @api.model_create_multi
    def create(self, vals_list):
        lineas = super().create(vals_list)
        lineas._renumerar_lineas()
        return lineas

    def unlink(self):
        padres = self.mapped('otrosi_tabla_id')
        resultado = super().unlink()
        padres.mapped('tabla_de_forma_de_pago')._renumerar_lineas()
        return resultado


class Departamentos_y_municipios_2(models.Model):
    _name = 'departamento_y_municipio2'
    contrataciones_fuv_id = fields.Many2one('otro_si', string='Tabla')
    departamentos = fields.Many2one(comodel_name='departamento', string="DEPARTAMENTOS",
                                    default=lambda self: self._default_departamento())
    municipios2 = fields.Many2one(comodel_name='municipio', string="MUNICIPIOS",
                                  default=lambda self: self._default_municipio())

    @api.model
    def _default_departamento(self):
        departamento = self.env['departamento'].search([('name', '=', 'Valle del Cauca')], limit=1)
        return departamento.id if departamento else False

    @api.model
    def _default_municipio(self):
        municipio = self.env['municipio'].search([('name', '=', 'Cali')], limit=1)
        return municipio.id if municipio else False


class VisorDocumentoPDF(models.Model):
    _name = 'visor_documento_pdf'

    hiden = fields.Char(string='hiden')

    name = fields.Char(string="Nombre", related="documento_nombre")
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')
    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              track_visibility='always')
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())
    documento_nombre = fields.Char(string="Nombre del Documento")
    documento_pdf_html = fields.Html(compute='_compute_documento_pdf_html')

    def _compute_documento_pdf_html(self):
        for record in self:
            if record.documento_pdf:
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                url = "{}/web/pdf_viewer?file=/web/content/{}".format(base_url, record.id)
                record.documento_pdf_html = '<iframe style="width: 100%; height: 600px;" src="{}"></iframe>'.format(url)
            else:
                record.documento_pdf_html = '<p>No hay documento para mostrar.</p>'

    def download_pdf(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model={}&id={}&field=documento_pdf&download=true'.format(self._name, self.id),
            'target': 'self',
        }

    def descargar_documento_word(self):
        nombre_archivo = "Documento_word.docx"  # Nombre del archivo Word
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=visor_documento_pdf&id=%s&field=documento_word&download=true&filename=%s' % (
                self.id, self.name),
            'target': 'self',
        }

    def descargar_documento_excel(self):
        nombre_archivo = "Documento_excel.xlsx"  # Nombre del archivo Excel
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=visor_documento_pdf&id=%s&field=documento_excel&download=true&filename=%s' % (
                self.id, self.name),
            'target': 'self',
        }

    def open_url(self):
        # Open the URL in a new tab/window
        return {
            'type': 'ir.actions.act_url',
            'url': self.url,
            'target': 'new',
        }

    documento_pdf = fields.Binary(string="Documento PDF", attachment=True, tracking=True)

    @api.constrains('documento_pdf')
    def _check_file_extension(self):
        for record in self:
            if record.documento_pdf:
                filename = record.name
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    url = fields.Char(string='URL')

    carpeta = fields.Char(string='Nombre de la carpeta')
    documento_word = fields.Binary(string="Documento", attachment=True, tracking=True)

    @api.constrains('documento_word')
    def _check_document_type(self):
        for record in self:
            if record.documento_word:
                filename = record.name or "Document"
                if not filename.endswith(('.docx', '.doc')):
                    raise ValidationError("Only Word documents are allowed.")

    documento_excel = fields.Binary(string="Documento", attachment=True, tracking=True)

    @api.constrains('documento_excel')
    def _check_document_type(self):
        for record in self:
            if record.documento_excel:
                filename = record.name or "Document"
                if not filename.endswith(('.xlsx', '.xls')):
                    raise ValidationError("Only Excel documents are allowed.")

    tipo = fields.Selection([
        ('archivo', 'PDF'),
        ('URL', 'URL'),
        ('carpeta', 'Carpeta'),
        ('word', 'Word'),
        ('excel', 'Excel'),
    ], string='Tipo', default='archivo')

    documentos_ids = fields.One2many('documentos', 'documentos_en_id', string="Documentos")
    description = fields.Text()


class SubirDocumento(models.Model):
    _name = 'subir_documento'
    _description = 'Subir documentos'

    name = fields.Char(string="Nombre", readonly=False)

    documento_nombre = fields.Char(string="Nombre del Documento", related="name")

    tipo = fields.Selection([
        ('archivo', 'PDF'),
        ('URL', 'URL'),
        ('carpeta', 'Carpeta'),
        ('word', 'Word'),
        ('excel', 'Excel'),
    ], string='Tipo', default='archivo')

    datas = fields.Binary(string='Archivo')
    url = fields.Char(string='URL')
    carpeta = fields.Char(string='Carpeta', readonly=False, related="name")
    documento_word = fields.Binary(string="Documento word", attachment=True, tracking=True)
    documento_excel = fields.Binary(string="Documento excel", attachment=True, tracking=True)

    def subir(self):
        if self.tipo == 'word':
            file_name, file_extension = os.path.splitext(self.name)
            allowed_extensions = ['.docx', '.doc']
            if file_extension.lower() not in allowed_extensions:
                return {'warning': {
                    'title': "Tipo de archivo incorrecto",
                    'message': "Solo se permiten archivos .docx y .doc para documentos de Word."
                }}

        if self.tipo == 'excel':
            file_name, file_extension = os.path.splitext(self.name)
            allowed_extensions = ['.xlsx', '.xls']
            if file_extension.lower() not in allowed_extensions:
                return {'warning': {
                    'title': "Tipo de archivo incorrecto",
                    'message': "Solo se permiten archivos .xlsx y .xls para documentos de Excel."
                }}

        if self.tipo == 'archivo':
            file_name, file_extension = os.path.splitext(self.name)
            allowed_extensions = ['.pdf']
            if file_extension.lower() not in allowed_extensions:
                return {'warning': {
                    'title': "Tipo de archivo incorrecto",
                    'message': "Solo se permiten archivos .pdf para el tipo de documento PDF."
                }}

        # Crear proyecto en contratación
        visor_obj = self.env['visor_documento_pdf']
        new_visor = visor_obj.create({
            'documento_nombre': self.name,
            'carpeta': self.carpeta,
            'url': self.url,
            'documento_pdf': self.datas,
            'tipo': self.tipo,
            'documento_word': self.documento_word,
            'documento_excel': self.documento_excel,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }


class Documentos(models.Model):
    _name = 'documentos'
    _description = "Documentos"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    
    documentos_en_id = fields.Many2one('visor_documento_pdf', string='Documentos')

    name = fields.Char(string="Nombre", readonly=False)
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              track_visibility='always')
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())
    documento_nombre = fields.Char(string="Nombre del Documento", related="name")

    documento_pdf = fields.Binary(string="Documento PDF", attachment=True, tracking=True)

    @api.depends('name')
    def _compute_documento_nombre(self):
        for record in self:
            record.documento_nombre = record.name

    @api.constrains('documento_pdf')
    def _check_file_extension_pdf(self):
        for record in self:
            if record.documento_pdf:
                if not record.name.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.constrains('documento_word')
    def _check_file_extension_word(self):
        for record in self:
            if record.documento_word:
                if not record.name.lower().endswith(('.docx', '.doc')):
                    raise ValidationError("Solo se permiten documentos Word.")

    @api.constrains('documento_excel')
    def _check_file_extension_excel(self):
        for record in self:
            if record.documento_excel:
                if not record.name.lower().endswith(('.xlsx', '.xls')):
                    raise ValidationError("Solo se permiten documentos Excel.")

    @api.constrains('documento_pdf')
    def _check_file_extension(self):
        for record in self:
            if record.documento_pdf:
                # Guardar el archivo temporalmente
                temp_file_path = False
                try:
                    temp_file_path = tempfile.mkstemp(suffix='.pdf')[1]
                    with open(temp_file_path, 'wb') as temp_file:
                        temp_file.write(record.documento_pdf)
                    # Intentar abrir el archivo PDF
                    pdf_reader = PyPDF2.PdfFileReader(temp_file_path)
                    # Verificar si el archivo PDF tiene al menos una página
                    if pdf_reader.numPages < 1:
                        raise ValidationError("El archivo no es un PDF válido.")
                except PyPDF2.utils.PdfReadError:
                    raise ValidationError("El archivo no es un PDF válido.")
                finally:
                    # Eliminar el archivo temporal
                    if temp_file_path and os.path.exists(temp_file_path):
                        os.unlink(temp_file_path)

    url = fields.Char(string='URL')
    carpeta = fields.Char(string='Carpeta', readonly=False, related="name")
    documento_word = fields.Binary(string="Documento word", attachment=True, tracking=True)

    description = fields.Text()
    documento_excel = fields.Binary(string="Documento excel", attachment=True, tracking=True)

    tipo = fields.Selection([
        ('archivo', 'PDF'),
        ('URL', 'URL'),
        ('carpeta', 'Carpeta'),
        ('word', 'Word'),
        ('excel', 'Excel'),
    ], string='Tipo', default='archivo')

    def open_current_record_form(self):
        """ Método para abrir el formulario del registro actual """
        return {
            'name': 'Editar Documento',
            'view_mode': 'form',
            'res_model': 'documentos',
            'res_id': self.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def download_pdf(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model={}&id={}&field=documento_pdf&download=true'.format(self._name, self.id),
            'target': 'self',
        }

    def descargar_documento_word(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=documentos&id={}&field=documento_word&download=true&filename={}'.format(self.id,
                                                                                                                self.name),
            'target': 'self',
        }

    def descargar_documento_excel(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=documentos&id={}&field=documento_excel&download=true&filename={}'.format(
                self.id, self.name),
            'target': 'self',
        }

    def open_url(self):
        # Open the URL in a new tab/window
        return {
            'type': 'ir.actions.act_url',
            'url': self.url,
            'target': 'new',
        }

    documentos_2_ids = fields.One2many('documentos_2', 'documentos_en_2_id', string="Documentos")


class Documentos_2(models.Model):
    _name = 'documentos_2'

    documentos_en_2_id = fields.Many2one('documentos', string='Documentos')

    name = fields.Char(string="Nombre", readonly=False)
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              track_visibility='always')
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())
    documento_nombre = fields.Char(string="Nombre del Documento", related="name")

    documento_pdf = fields.Binary(string="Documento PDF", attachment=True, tracking=True)

    @api.constrains('documento_pdf')
    def _check_file_extension(self):
        for record in self:
            if record.documento_pdf:
                # Guardar el archivo temporalmente
                temp_file_path = False
                try:
                    temp_file_path = tempfile.mkstemp(suffix='.pdf')[1]
                    with open(temp_file_path, 'wb') as temp_file:
                        temp_file.write(record.documento_pdf)
                    # Intentar abrir el archivo PDF
                    pdf_reader = PyPDF2.PdfFileReader(temp_file_path)
                    # Verificar si el archivo PDF tiene al menos una página
                    if pdf_reader.numPages < 1:
                        raise ValidationError("El archivo no es un PDF válido.")
                except PyPDF2.utils.PdfReadError:
                    raise ValidationError("El archivo no es un PDF válido.")
                finally:
                    # Eliminar el archivo temporal
                    if temp_file_path and os.path.exists(temp_file_path):
                        os.unlink(temp_file_path)

    url = fields.Char(string='URL')
    documento_word = fields.Binary(string="Documento word", attachment=True, tracking=True)
    documento_excel = fields.Binary(string="Documento excel", attachment=True, tracking=True)

    @api.depends('name')
    def _compute_documento_nombre(self):
        for record in self:
            record.documento_nombre = record.name

    @api.constrains('documento_pdf')
    def _check_file_extension_pdf(self):
        for record in self:
            if record.documento_pdf:
                if not record.name.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.constrains('documento_word')
    def _check_file_extension_word(self):
        for record in self:
            if record.documento_word:
                if not record.name.lower().endswith(('.docx', '.doc')):
                    raise ValidationError("Solo se permiten documentos Word.")

    @api.constrains('documento_excel')
    def _check_file_extension_excel(self):
        for record in self:
            if record.documento_excel:
                if not record.name.lower().endswith(('.xlsx', '.xls')):
                    raise ValidationError("Solo se permiten documentos Excel.")

    tipo = fields.Selection([
        ('archivo', 'PDF'),
        ('URL', 'URL'),
        ('word', 'Word'),
        ('excel', 'Excel'),
    ], string='Tipo', default='archivo')

    def open_current_record_form(self):
        """ Método para abrir el formulario del registro actual """
        return {
            'name': 'Documento',
            'view_mode': 'form',
            'res_model': 'documentos_2',
            'res_id': self.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def download_pdf(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model={}&id={}&field=documento_pdf&download=true'.format(self._name, self.id),
            'target': 'self',
        }

    def descargar_documento_word(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=documentos_2&id={}&field=documento_word&download=true&filename={}'.format(
                self.id, self.name),
            'target': 'self',
        }

    def descargar_documento_excel(self):
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=documentos_2&id={}&field=documento_excel&download=true&filename={}'.format(
                self.id, self.name),
            'target': 'self',
        }

    def open_url(self):
        # Open the URL in a new tab/window
        return {
            'type': 'ir.actions.act_url',
            'url': self.url,
            'target': 'new',
        }

class _SafeDocsProxy:
    """Wrapper de registro Odoo para docxtpl: convierte False→'' en campos char/text/selection
    para evitar que Jinja2 imprima literalmente 'False' en documentos generados."""
    __slots__ = ('_r', '_overrides')

    def __init__(self, rec, **overrides):
        object.__setattr__(self, '_r', rec)
        object.__setattr__(self, '_overrides', overrides)

    def __getattr__(self, name):
        overrides = object.__getattribute__(self, '_overrides')
        if name in overrides:
            return overrides[name]
        rec = object.__getattribute__(self, '_r')
        val = getattr(rec, name)
        if val is False:
            f = rec._fields.get(name)
            if f and f.type in ('char', 'text', 'html', 'selection'):
                return ''
        return val

    def __bool__(self):
        return bool(object.__getattribute__(self, '_r'))

    def __str__(self):
        return str(object.__getattribute__(self, '_r'))


class OtroSi(models.Model):
    _name = "otro_si"
    _description = "OtroSí"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    def _crear_adjunto_temporal_drive_para_correo(self, drive_record, nombre_default="documento.pdf"):
        """Descarga una copia desde Google Drive solo para adjuntarla al correo.

        El adjunto queda marcado como temporal y se elimina después del envío,
        para que Gmail muestre el archivo sin acumular documentos permanentes
        en Ajustes > Técnico > Adjuntos.
        """
        self.ensure_one()
        if not drive_record:
            return False

        target_id = drive_record.file_id or drive_record.drive_file_id
        if not target_id:
            return False

        temp_file_path = False
        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()
            gdrive_file = drive.CreateFile({"id": target_id})
            gdrive_file.FetchMetadata()

            mime_type = gdrive_file.get('mimeType') or 'application/octet-stream'
            original_title = gdrive_file.get('title') or nombre_default
            export_mime_type = None
            extension = os.path.splitext(original_title)[1]

            if mime_type == 'application/vnd.google-apps.spreadsheet':
                export_mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                extension = ".xlsx"
            elif mime_type == 'application/vnd.google-apps.document':
                export_mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                extension = ".docx"
            elif mime_type == 'application/vnd.google-apps.presentation':
                export_mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                extension = ".pptx"
            elif not extension:
                extension = ".pdf" if mime_type == "application/pdf" else ".bin"

            final_name = original_title if original_title.lower().endswith(extension.lower()) else f"{original_title}{extension}"
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
            temp_file_path = temp_file.name
            temp_file.close()

            if export_mime_type:
                gdrive_file.GetContentFile(temp_file_path, mimetype=export_mime_type)
            else:
                gdrive_file.GetContentFile(temp_file_path)

            with open(temp_file_path, "rb") as f:
                file_base64 = base64.b64encode(f.read()).decode("utf-8")

            attachment = self.env["ir.attachment"]._drive_create_temp_download({
                "name": final_name,
                "type": "binary",
                "datas": file_base64,
                "mimetype": export_mime_type or mime_type,
                "res_model": self._name,
                "res_id": self.id,
            }, hours=1)
            return attachment.id
        except Exception as e:
            _logger.warning(">>> No se pudo crear adjunto temporal desde Drive: %s", str(e))
            return False
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception:
                    pass
    
    
    #MODIFICACIONES SAMUEL---ESTADOS ENUM
    
    estado_contratacion = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('juridico', 'Jurídico'),
        ('recursos_humanos', 'Recursos humanos'),
        ('contratado', 'Contratado'),
    ], string='Estado Contratación', default='coordinador', tracking=True)

    # estado_contratacion = fields.Selection(
    #     selection_add=ESTADOS_CONTRATACION_NUEVOS,
    #     ondelete={
    #         EstadoContratacion.VENTANILLA.value: 'set default',
    #         EstadoContratacion.PRESUPUESTO.value: 'set default',
    #         EstadoContratacion.DIRECTOR_JURIDICO.value: 'set default',
    #         EstadoContratacion.JURIDICO_1.value: 'set default',
    #         EstadoContratacion.JURIDICO_2.value: 'set default',
    #         EstadoContratacion.FIRMA.value: 'set default',
    #         EstadoContratacion.POLIZAS.value: 'set default',
    #         EstadoContratacion.SUSPENDIDO.value: 'set default',
    #         EstadoContratacion.FINALIZADO.value: 'set default',
    #         EstadoContratacion.ANULADO.value: 'set default',
    #     },
    # )

    
    
    contratado_fin_visible = fields.Boolean('Contratado', tracking=True, default=False)
    contratacion_fuv_en_id = fields.Many2one('contrataciones_fuv', string='Contratación FUV')
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado',
                                           related="contratacion_fuv_en_id.proyecto_relacionado")
    

    # Documentos arrastrador  P/N
    anexo_tributario_id = fields.Many2one('drive', string='Anexo Tributario', readonly=True, related='contratacion_fuv_en_id.anexo_tributario_id')
    anexo_tributario_doc_visible = fields.Boolean(default=True, related='contratacion_fuv_en_id.anexo_tributario_doc_visible')

    documento_identidad_pn_doc_visible = fields.Boolean(default=True, related='contratacion_fuv_en_id.documento_identidad_pn_doc_visible')
    documento_identidad_id = fields.Many2one('drive', string='Documento de Identidad', readonly=True, related='contratacion_fuv_en_id.documento_identidad_id')

    certificacion_bancaria_id = fields.Many2one('drive', string='Certificación Bancaria', readonly=True, related='contratacion_fuv_en_id.certificacion_bancaria_id')
    certificacion_bancaria_doc_visible = fields.Boolean(default=True, related='contratacion_fuv_en_id.certificacion_bancaria_doc_visible')

    resolucion_facturacion_ju_id = fields.Many2one('drive', string='Resolución Facturación', readonly=True, related='contratacion_fuv_en_id.resolucion_facturacion_ju_id')
    resolucion_facturacion_ju_doc_visible = fields.Boolean(default=True, related='contratacion_fuv_en_id.resolucion_facturacion_ju_doc_visible')

    # Documentos arrastrador  P/J
    anexo_tributario_juridico_doc_visible = fields.Boolean(default=True, related='contratacion_fuv_en_id.anexo_tributario_juridico_doc_visible')
    anexo_tributario_juridico_doc_id = fields.Many2one('drive', string='Anexo Tributario Jurídico', related='contratacion_fuv_en_id.anexo_tributario_juridico_doc_id')

    certificado_bancario_doc_id = fields.Many2one('drive', string='Certificado Bancario', related='contratacion_fuv_en_id.certificado_bancario_doc_id')
    certificado_bancario_doc_visible = fields.Boolean(default=True, related='contratacion_fuv_en_id.certificado_bancario_doc_visible')

    resolucion_facturacion_doc_id = fields.Many2one('drive', string='Resolución Facturación', related='contratacion_fuv_en_id.resolucion_facturacion_doc_id')
    resolucion_facturacion_doc_visible = fields.Boolean(default=True, related='contratacion_fuv_en_id.resolucion_facturacion_doc_visible')


    relacion_ids = fields.One2many('otrosi_bdd', 'fuente_id', string='Relación de otrosíes')
    nuemor_de_cuotas = fields.Integer(
        string="Digitar número de cuotas",
        compute='_compute_nuemor_de_cuotas',
        inverse='_inverse_nuemor_de_cuotas',
    )
    tabla_de_forma_de_pago = fields.One2many(
        'tabla_forma_pago_otrosi',
        'otrosi_tabla_id',
        string='Tabla para formas de pagos',
    )
    total_de_cantidad_de_dinero = fields.Float(
        string="Monto total",
        compute='_compute_total_dinero_otrosi',
    )
    total_pago_porcentaje = fields.Float(
        string="Total % de pago",
        compute='_compute_total_pago_porcentaje_otrosi',
    )
    total_porcentaje_tecnico = fields.Float(
        string="Total porcentaje técnico",
        compute='_compute_total_porcentaje_tecnico_otrosi',
    )

    @api.depends('tabla_de_forma_de_pago')
    def _compute_nuemor_de_cuotas(self):
        for record in self:
            record.nuemor_de_cuotas = len(record.tabla_de_forma_de_pago)

    def _inverse_nuemor_de_cuotas(self):
        for record in self:
            cuotas = max(record.nuemor_de_cuotas or 0, 0)
            lineas_actuales = len(record.tabla_de_forma_de_pago)
            if cuotas == lineas_actuales:
                continue
            if cuotas == 0:
                record.tabla_de_forma_de_pago = [(5, 0, 0)]
            elif cuotas > lineas_actuales:
                record.tabla_de_forma_de_pago = [
                    (0, 0, {})
                    for _ in range(cuotas - lineas_actuales)
                ]
            else:
                record.tabla_de_forma_de_pago = record.tabla_de_forma_de_pago[:cuotas]

    @api.depends('tabla_de_forma_de_pago.cantidad_de_dinero')
    def _compute_total_dinero_otrosi(self):
        for record in self:
            record.total_de_cantidad_de_dinero = sum(
                line.cantidad_de_dinero for line in record.tabla_de_forma_de_pago
            )

    @api.depends('tabla_de_forma_de_pago.pago')
    def _compute_total_pago_porcentaje_otrosi(self):
        for record in self:
            record.total_pago_porcentaje = sum(
                line.pago for line in record.tabla_de_forma_de_pago
            )

    @api.depends('tabla_de_forma_de_pago.tecnico')
    def _compute_total_porcentaje_tecnico_otrosi(self):
        for record in self:
            record.total_porcentaje_tecnico = sum(
                line.tecnico for line in record.tabla_de_forma_de_pago
            )

    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################

    select_fondos = fields.Selection([
        ('es_produccion', 'Producción'),
        ('es_personal_fuv', 'Costos FUV'),
        ('es_vco', 'Contratos VCO')
    ], string='Seleccionar fondo para contratar')

    # 1. Campo Monetary relacionado (con 'related')
    suma_costos_fuv = fields.Monetary(related="proyecto_relacionado.suma_costos_fuv", string='Costos FUV')

    # Saldo disponible para Costos FUV
    saldo_disponible_personal_fuv = fields.Monetary(
        related ='contratacion_fuv_en_id.saldo_disponible_personal_fuv',
        string='Saldo Disponible costos FUV',
        tracking=True,
    )

    saldo_disponible_vco = fields.Monetary(
        related ='contratacion_fuv_en_id.saldo_disponible_vco',
        string='Saldo contratos VCO',
        tracking=True,
    )
        
    # 2. Este es el que hace la resta en tiempo real
    saldo_disponible_produccion = fields.Monetary(
        related='contratacion_fuv_en_id.saldo_disponible_produccion',
        string='Saldo Disponible Producción',
        tracking=True,
    )

    # --- VALIDACIÓN (CONSTRAINS) ---
    @api.onchange('valor_total_contrato', 'select_fondos')
    def _check_disponibilidad_presupuesto(self):
        """
        Valida que el fondo seleccionado tenga saldo suficiente para el contrato.
        """
        for record in self:
            if not record.select_fondos or record.valor_total_contrato <= 0:
                continue

            # 1. Caso: Producción
            if record.select_fondos == 'es_produccion':
                if record.valor_total_contrato > record.saldo_disponible_produccion:
                    faltante = record.saldo_disponible_produccion - record.valor_total_contrato
                    raise ValidationError(
                        "🛑 ERROR DE SALDO: PRODUCCIÓN\n\n"
                        f"No puedes guardar esta orden porque el saldo de producción "
                        f"quedaría en negativo (${faltante:,.2f}).\n"
                        "Por favor, reduzca el valor o cambie la fuente de fondos."
                    )

            # 2. Caso: Personal FUV
            elif record.select_fondos == 'es_personal_fuv':
                if record.valor_total_contrato > record.saldo_disponible_personal_fuv:
                    faltante = record.saldo_disponible_personal_fuv - record.valor_total_contrato
                    raise ValidationError(
                        "🛑 ERROR DE SALDO: PERSONAL FUV\n\n"
                        f"No puedes guardar esta orden porque el saldo de personal FUV "
                        f"quedaría en negativo (${faltante:,.2f}).\n"
                        "Por favor, reduzca el valor o cambie la fuente de fondos."
                    )

            # 3. Caso: Contratos VCO (EL NUEVO)
            elif record.select_fondos == 'es_vco':
                if record.valor_total_contrato > record.saldo_disponible_vco:
                    faltante = record.saldo_disponible_vco - record.valor_total_contrato
                    raise ValidationError(
                        "🛑 ERROR DE SALDO: CONTRATOS VCO\n\n"
                        f"No puedes guardar esta orden porque el saldo de contratos VCO "
                        f"quedaría en negativo (${faltante:,.2f}).\n"
                        "Por favor, reduzca el valor o cambie la fuente de fondos."
                    )

    



    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################

        # 2. El campo Monetary (Sin 'related', usamos compute)
    valor_a_contratar = fields.Monetary(
        string='Valor a contratar',
        currency_field='currency_id', # Vincula con el campo de arriba
        compute='_compute_valor_a_contratar',
        store=True # Para que se guarde en BD
    )

    # 3. La función que "traduce" de Float a Monetary
    @api.depends('proyecto_relacionado.new_total_costos_produccion_N_r')
    def _compute_valor_a_contratar(self):
        for record in self:
            # Obtenemos el valor float
            valor_origen = record.proyecto_relacionado.new_total_costos_produccion_N_r
            
            # Lo asignamos. Si es False/None, ponemos 0.0
            record.valor_a_contratar = valor_origen if valor_origen else 0.0

    suma_valor_total_contrato = fields.Monetary(compute='_compute_suma_valor_total_contrato', string='Suma del valor total del contrato')

    @api.depends('total', 'proyecto_relacionado')
    def _compute_suma_valor_total_contrato(self):
        for record in self:
            # Obtener todos los registros en el modelo 'otro_si' relacionados con el mismo proyecto
            registros_otro_si = self.env['otro_si'].search([('proyecto_relacionado', '=',
                                                                                   record.proyecto_relacionado.id)])

            # Calcular la suma de los valores de 'valor_total_contrato' para el proyecto actual
            suma_valor_total_contrato = sum(registro.total for registro in registros_otro_si)   
            # Actualizar el campo 'suma_valor_total_contrato' con la suma calculada
            record.suma_valor_total_contrato = suma_valor_total_contrato

    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################
          
    #Poliza total y disponible a contratar póliza total
    # Definición de campos (manteniendo tu snippet original)
    poliza_total = fields.Monetary(related="proyecto_relacionado.poliza_total", string='Valor póliza total')
    saldo_disponible_poliza_total = fields.Monetary(related="contratacion_fuv_en_id.saldo_disponible_poliza_total",
                                                string='Saldo Disponible a Contratar (póliza)', tracking=True)
    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################

    total_compras = fields.Monetary('Valor a contratar', related="proyecto_relacionado.total_compras")
    total_bienes = fields.Monetary('Valor a contratar', related="proyecto_relacionado.total_bienes")
    
    saldo_disponible_contratar = fields.Monetary(related='contratacion_fuv_en_id.saldo_disponible_contratar',
                                                 string='Saldo Disponible a Contratar')
    
    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################
###########################################Nuevos campos P/J ########################################################
###################################################################################################################

    @api.model
    def search(self, args=None, offset=0, limit=None, order=None, count=False):
        args = list(args or [])
        usuario = self.env.user

        if (
            self.env.su
            or usuario.has_group('Matriz.group_soporte')
            or usuario.has_group('Matriz.group_consulta_coordinacion_financiera')
            or usuario.has_group('Matriz.group_contratacion_coordinador')
            or usuario.has_group('Matriz.group_contratacion_asis_coordinador')
            or usuario.has_group('Matriz.group_contratacion_director_fuv')
            or usuario.has_group('Matriz.group_contratacion_director_financiero')
            or usuario.has_group('Matriz.group_contratacion_financiero')
            or usuario.has_group('Matriz.group_contratacion_ventanilla_unica')
            or usuario.has_group('Matriz.group_presupuestos')
            or usuario.has_group('Matriz.group_contratacion_director_juridico')
            or usuario.has_group('Matriz.group_contratacion_juridico')
            or usuario.has_group('Matriz.group_contratacion_recursos')
        ):
            # Los roles del flujo pueden ver el Otrosí en cualquier estado.
            # La edición se controla en write/create según el estado actual.
            pass
        else:
            args.append(('id', '=', False))

        return super().search(args, offset=offset, limit=limit, order=order, count=count)

    ############################################juridico##################################################################

    #Sección para agregar juridico a la lista de permitidos por director juridico
    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto:',
                                         related='proyecto_relacionado.users_with_access')
    users_with_access_financiero = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto:',
                                         related='proyecto_relacionado.users_with_access_financiero')

    usuarios_juridico_ids = fields.Many2many(
        'res.users',
        string='Usuarios con acceso jurídico al Otrosí',
        help='Usuarios con acceso a los registros jurídicos.',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_juridico').id)],
        tracking=True,
    )
    show_aprueba_financiera = fields.Boolean(
        string='Mostrar botón de aprueba_financiera',
        compute='_compute_show_aprueba_financiera',
    )

    drive_file_id = fields.Many2one('otrosi_drive', string='Archivo de Drive')  # Campo para vincular con otrosi_drive

    @api.depends('aprueba_f_a_su_cdp')
    def _compute_show_aprueba_financiera(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_f_a_su_cdp,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_financiera = all_approved

    def _share_file_with_new_users_juridico(self, previous_user_ids=None, notificar=True):
        """Comparte la carpeta de Google Drive con los usuarios jurídicos y envía notificaciones por correo."""
        if not self.drive_file_id or not self.drive_file_id.folder_id:
            _logger.warning(
                "No se ha configurado una carpeta de Google Drive o el ID de la carpeta no está disponible.")
            return

        # Obtener autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        if not drive:
            _logger.error("No se pudo autenticar en Google Drive.")
            return

        # Obtener el ID de la carpeta
        folder_id = self.drive_file_id.folder_id
        folder = drive.CreateFile({'id': folder_id})

        # Obtener permisos actuales de la carpeta
        try:
            existing_permissions = folder.GetPermissions()
            existing_emails = {
                perm.get('emailAddress'): perm
                for perm in existing_permissions if 'emailAddress' in perm
            }
            owner_email = next(
                (perm.get('emailAddress') for perm in existing_permissions if perm.get('role') == 'owner'),
                None
            )
            _logger.info(f"Propietario de la carpeta: {owner_email}")
        except Exception as e:
            _logger.error(f"Error al obtener permisos de la carpeta: {e}")
            return

        # Usuarios anteriores y actuales para acceso jurídico
        previous_users = set(previous_user_ids if previous_user_ids is not None else (self._origin.usuarios_juridico_ids.ids if self._origin else []))
        current_users = set(self.usuarios_juridico_ids.ids)

        # Identificar usuarios eliminados y agregados
        removed_users_ids = previous_users - current_users
        added_users_ids = current_users - previous_users

        # Revocar permisos de usuarios eliminados
        for user_id in removed_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if not email:
                _logger.warning(f"Usuario sin correo electrónico (ID: {user_id}).")
                continue
            if email == owner_email:
                _logger.info(f"El propietario {email} no puede ser eliminado.")
                continue
            try:
                permission_id = existing_emails[email]['id']
                folder.DeletePermission(permission_id)
                _logger.info(f"Permiso revocado para: {email}")
            except Exception as e:
                _logger.error(f"Error al revocar permiso para {email}: {e}")

        # Agregar permisos de usuarios nuevos y enviar notificaciones
        for user_id in added_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if not email:
                _logger.warning(f"Usuario sin correo electrónico (ID: {user_id}).")
                continue
            try:
                if email in existing_emails:
                    _logger.info(f"El usuario {email} ya tiene acceso, se le enviará la notificación de todas formas.")
                else:
                    folder.InsertPermission({
                        'type': 'user',
                        'value': email,
                        'role': 'reader',
                    })
                    _logger.info(f"Carpeta compartida con: {email}")

                # Crear enlaces
                folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

                # Extraer el ID sin el prefijo 'NewId_'
                form_id = str(self.id).replace("NewId_", "")

                # Aquí, ajustamos el formato del enlace con los parámetros adicionales requeridos
                formulario_url = f"/web#id={form_id}&cids=1&menu_id=144&active_id=16&model=otro_si&view_type=form"
                formulario_url = base_url + formulario_url


                proyecto_nombre = (
                    self.proyecto_relacionado.new_name
                    or self.proyecto_relacionado.name
                    or 'Proyecto'
                )
                body_html = self._render_email_sgo(
                    titulo="Revisión pendiente - Jurídico",
                    icono="&#9878;",
                    nombre_destinatario=user.name,
                    mensaje_cuerpo=(
                        f"Se te ha compartido el acceso a {self._get_labels_correo_documento_otrosi()['revision_documento']} "
                        ", por favor revisar."
                    ),
                    formulario_url=formulario_url,
                    observaciones=None,
                )

                if notificar:
                    self.env['mail.mail'].sudo().create({
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{proyecto_nombre} - Revisión de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': self.env.user.email_formatted,
                        'email_to': email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }).send()

                    _logger.info(f"Notificación enviada a: {email}")

            except Exception as e:
                _logger.error(f"Error al compartir carpeta o enviar notificación a {email}: {e}")

    def agregar_usuario_juridico(self, user_id):
        """
        Agregar un usuario al grupo de usuarios con acceso jurídico.
        Solo permitido para usuarios del grupo `group_contratacion_director_juridico`.
        """
        if not self.env.user.has_group('Matriz.group_contratacion_director_juridico'):
            raise exceptions.AccessError("No tienes permisos para agregar usuarios jurídicos.")

        user = self.env['res.users'].browse(user_id)
        if not user:
            raise exceptions.ValidationError("El usuario no existe.")

        self.write({'usuarios_juridico_ids': [(4, user.id)]})


    @api.onchange('usuarios_juridico_ids')
    def _onchange_users_with_access(self):
        """No ejecutar Drive mientras se selecciona: el valor se procesa al guardar."""
        return

    def action_link_drive_file(self):
        """Buscar y vincular el archivo de Drive basado en un identificador único."""
        for record in self:
            if record.new_name:  # Reemplaza `new_name` con el campo que identifique el archivo
                drive_file = self.env['drive.file'].search([('folder_name', '=', record.new_name)], limit=1)
                record.drive_file_id = drive_file.id if drive_file else False

    def _get_labels_correo_documento_otrosi(self):
        """Textos de correo segun el tipo de documento del Otrosi."""
        es_documento_especial = bool(
            self.suspension
            or self.acta_de_prorroga_de_la_suspension
            or self.reinicio
        )
        if self.suspension:
            nombre = "acta de suspensión"
        elif self.acta_de_prorroga_de_la_suspension:
            nombre = "acta de prórroga de la suspensión"
        elif self.reinicio:
            nombre = "acta de reinicio"
        else:
            numero_otrosi = (
                self.otrosi_number
                or self._get_numero_otrosi_global_para_registro(self)
                or 1
            )
            nombre = f"otrosí No. {numero_otrosi}"

        nombre_cap = nombre[:1].upper() + nombre[1:]
        no_aprobado = "no aprobada" if nombre.startswith("acta") else "no aprobado"
        nombre_proyecto = (
            self.proyecto_relacionado.new_name
            or self.proyecto_relacionado.name
            or 'el proyecto'
        ) if self.proyecto_relacionado else 'el proyecto'
        if es_documento_especial:
            subido_asunto = f"El {nombre} ha sido subido en {self.name}"
            firmado_asunto = f"Copia del {nombre} firmado: {self.name}"
            revision_documento = f"{nombre} {self.name}"
            no_aprobado_asunto = f"{nombre_cap} {no_aprobado} de {self.name}"
            no_aprobado_documento = f"{nombre_cap} de {self.name}"
            nombre_documento = self.name
        else:
            subido_asunto = f"El {nombre_cap} ha sido subido en {nombre_proyecto}"
            firmado_asunto = f"Copia del {nombre} firmado: {nombre_proyecto}"
            revision_documento = nombre_cap
            no_aprobado_asunto = f"{nombre_cap} {no_aprobado}"
            no_aprobado_documento = nombre_cap
            nombre_documento = nombre_cap
        return {
            'nombre': nombre,
            'nombre_cap': nombre_cap,
            'nombre_documento': nombre_documento,
            'subido_titulo': f"{nombre_cap} subido al sistema",
            'subido_asunto': subido_asunto,
            'subido_mensaje': f"Se ha subido el {nombre} al sistema, por favor, hacer firmar:",
            'nombre_label': f"Nombre del {nombre}:",
            'boton_ver': f"Ver {nombre}",
            'firmado_asunto': firmado_asunto,
            'firmado_titulo': f"Copia del {nombre} firmado disponible",
            'firmado_intro': f"El {nombre} firmado para el siguiente proyecto ya está disponible:",
            'firmado_adjunto': f"Encontrarán la copia del {nombre} firmado disponible en Google Drive.",
            'firmado_disponible': f"El {nombre} firmado para este proyecto ya está disponible en el sistema.",
            'revision_documento': revision_documento,
            'no_aprobado_titulo': f"{nombre_cap} {no_aprobado}",
            'no_aprobado_asunto': no_aprobado_asunto,
            'no_aprobado_documento': no_aprobado_documento,
            'no_aprobado_estado': no_aprobado,
        }

    def _registrar_envio_coordinador_para_firma(self):
        self.ensure_one()
        labels_doc = self._get_labels_correo_documento_otrosi()
        mensaje = (
            f"{labels_doc['nombre_cap']} subido correctamente. "
            "El estado pasó a Coordinador para contratar y se envió al coordinador para firma."
        )
        self.message_post(
            body=(
                f"✅ <strong>{labels_doc['nombre_cap']} subido por jurídico</strong><br/>"
                "El estado pasó a <strong>Coordinador para contratar</strong> y se envió al coordinador para firma."
            )
        )
        return mensaje

    def _omite_recursos_humanos_otrosi(self):
        self.ensure_one()
        return bool(
            self.suspension
            or self.reinicio
            or self.acta_de_prorroga_de_la_suspension
        )

    def _requiere_recursos_humanos_otrosi(self):
        self.ensure_one()
        if self.select_persona not in ['persona_natural', 'contrato_laboral'] or self._omite_recursos_humanos_otrosi():
            return False
        return bool(
            self.adicion
            or self.prorroga
            or self.adicion_prorroga
            or (
                self.otro_si_modificatorio
                and self.tipo_modificacion_modificatorio in ('tiempo', 'ambos')
            )
        )





    is_editable = fields.Boolean(string='Es editable', related="contratacion_fuv_en_id.is_editable", default=False)

    opciones_otrosi_bloqueadas = fields.Boolean(
        string='Opciones de otrosí bloqueadas',
        compute='_compute_opciones_otrosi_bloqueadas',
        store=False,
    )

    def _compute_opciones_otrosi_bloqueadas(self):
        for record in self:
            record.opciones_otrosi_bloqueadas = bool(record.id)

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass


    ####################################################################################################################
    id_proyecto3 = fields.Char(string='Id del proyecto', related="contratacion_fuv_en_id.id_proyecto3")
    mcb_centro_costo = fields.Char(
        string='Centro de costo',
        related='contratacion_fuv_en_id.mcb_centro_costo',
        tracking=True,
    )
    otrosi_conteo = fields.Integer(string="Numero de otrosí")
    ciudad_residencia = fields.Many2one(comodel_name='municipio',
                                        string="Ciudad de Residencia",
                                        store=True,
                                        related="contratacion_fuv_en_id.ciudad_residencia")
    total = fields.Monetary(
        string="Valor de contrato con otrosí",
        compute="_compute_total_otrosi_form",
        store=False,
    )
    fch_impresion_cc = fields.Date('Fecha de Impresión Cámara de Comercio', tracking=True)

    fecha_impresion_cc = fields.Date('Fecha de Impresión Camara de Comercio', tracking=True,
                                     related='contratacion_fuv_en_id.fecha_impresion_cc')
    ciudad_oficina_principal = fields.Many2one(comodel_name='municipio',
                                               string="Ciudad de Oficina Principal",
                                               store=True,
                                               related="contratacion_fuv_en_id.ciudad_oficina_principal")

    ####################################################################################################################

    proyecto = fields.Many2one('otro_si', string="Contrato", invisible=True, tracking=True)
    valor_total_contrato = fields.Monetary('Valor otrosí del contrato', default=0.0, tracking=True)
    tipo_modificacion_modificatorio = fields.Selection([
        ('tiempo', 'Tiempo / Objeto / Actividades / Forma de pago'),
        ('valor', 'Ajuste al valor del contrato / Objeto / Actividades / Forma de pago'),
        ('ambos', 'Ambos (valor y tiempo)'),
    ], string='Tipo de modificación', tracking=True)

    objeto_suspension = fields.Text(string='Objeto adicional suspensión', tracking=True, readonly=False)
    objeto_reinicio = fields.Text(string='Objeto adicional reinicio', readonly=False)
    prorroga_texto = fields.Text(string='Texto de Prórroga', compute='_compute_prorroga_texto', tracking=True,
                                 readonly=False, store=True)

    @api.depends('contratacion_fuv_en_id', 'contratacion_fuv_en_id.valor_total_contrato', 'contratacion_fuv_en_id.otro_si_ids.valor_total_contrato')
    def _compute_total_otrosi_form(self):
        for record in self:
            contrato = record.contratacion_fuv_en_id
            if not contrato:
                record.total = 0.0
                continue

            otrosi_ids = contrato.otro_si_ids
            if record._origin.id:
                otrosi_ids = otrosi_ids.filtered(lambda otrosi: otrosi.id != record._origin.id)

            record.total = (contrato.valor_total_contrato or 0.0) + sum(otrosi_ids.mapped('valor_total_contrato'))

    @api.depends('fecha_iniciacion_texto', 'fecha_terminacion_texto')
    def _compute_prorroga_texto(self):
        for record in self:
            texto_prorroga = ""

            # Verificar que las fechas sean válidas (no "Fecha no asignada")
            fechas_validas = (
                    record.fecha_iniciacion_texto and record.fecha_terminacion_texto and
                    record.fecha_iniciacion_texto != "Fecha no asignada" and
                    record.fecha_terminacion_texto != "Fecha no asignada"
            )

            # Manejo de fechas para prórroga
            if fechas_validas:
                texto_prorroga = f"Prórroga desde el {record.fecha_iniciacion_texto} hasta el {record.fecha_terminacion_texto}"

            record.prorroga_texto = texto_prorroga

    adicion_texto = fields.Text(string='Texto de Adición', compute='_compute_adicion_texto', tracking=True,
                                readonly=False, store=True)
    @api.depends('valor_total_contrato', 'valor_en_letras')
    def _compute_adicion_texto(self):
        for record in self:
            texto_adicion = ""

            if record.valor_total_contrato and record.valor_en_letras:
                valor_total_formateado = f"${record.valor_total_contrato:,.2f}"
                texto_adicion = f"Se adiciona el valor inicial en la suma de {record.valor_en_letras} M/CTE. ({valor_total_formateado})"

            record.adicion_texto = texto_adicion

    objeto = fields.Text(string='Objeto adicional', compute='_compute_objeto', tracking=True, readonly=False,
                         store=True)

    @api.depends('valor_total_contrato', 'valor_en_letras', 'fecha_iniciacion_texto', 'fecha_terminacion_texto')
    def _compute_objeto(self):
        for record in self:
            texto_completo = ""

            # Verificar que las fechas sean válidas (no "Fecha no asignada")
            fechas_validas = (
                    record.fecha_iniciacion_texto and record.fecha_terminacion_texto and
                    record.fecha_iniciacion_texto != "Fecha no asignada" and
                    record.fecha_terminacion_texto != "Fecha no asignada"
            )

            # Manejo de fechas
            if fechas_validas:
                texto_completo = f"Se prorroga el plazo de ejecución del {record.fecha_iniciacion_texto} al {record.fecha_terminacion_texto}"

            # Manejo del valor
            if record.valor_total_contrato and record.valor_en_letras:
                valor_total_formateado = f"${record.valor_total_contrato:,.2f}"
                if texto_completo:
                    texto_completo += f" y se adicionó al valor del contrato la suma de {record.valor_en_letras} M/CTE. ({valor_total_formateado})"
                else:
                    texto_completo = f"Se adicionó al valor del contrato la suma de {record.valor_en_letras} M/CTE. ({valor_total_formateado})"

            record.objeto = texto_completo

    gestion_seleccionada = fields.Boolean(
        string='Gestión seleccionada',
        compute='_compute_gestion_seleccionada',
        store=True,
        readonly=False
    )

    @api.depends('proyecto', 'prorroga', 'otro_si', 'suspension', 'adicion', 'reinicio')
    def _compute_gestion_seleccionada(self):
        for record in self:
            record.gestion_seleccionada = bool(record.proyecto) or any(
                [record.prorroga, record.otro_si, record.suspension, record.adicion, record.reinicio])

    @api.depends('adicion', 'prorroga')
    def _compute_adicion_prorroga(self):
        for record in self:
            record.adicion_prorroga = record.prorroga and record.adicion

    otro_si = fields.Boolean('Otrosí', readonly=False, default=True)

    @api.onchange('reinicio', 'suspension', 'acta_de_prorroga_de_la_suspension', 'otro_si_modificatorio')
    def _onchange_reinicio_suspension_desmarcar_otrosi(self):
        for record in self:
            # Si cualquiera de los dos (reinicio o suspension) está en True, otro_si pasa a False
            if record.reinicio or record.suspension or record.acta_de_prorroga_de_la_suspension or record.otro_si_modificatorio:
                record.otro_si = False

    adicion_prorroga = fields.Boolean('Adición y Prórroga', compute="_compute_adicion_prorroga", store=True)
    prorroga = fields.Boolean('Prórroga', readonly=True, tracking=True)
    adicion = fields.Boolean('Adición', readonly=True, tracking=True)
    reinicio = fields.Boolean('Reinicio', readonly=False, tracking=True)
    suspension = fields.Boolean('Suspensión', readonly=False, tracking=True)
    otro_si_modificatorio = fields.Boolean('Otrosí modificatorio', readonly=False, tracking=True)
    acta_de_prorroga_de_la_suspension = fields.Boolean('Acta de prórroga de la suspensión', readonly=False, tracking=True)

    @api.onchange('otro_si_modificatorio')
    def _onchange_verificar_modificatorio(self):
        pass

    # Método onchange para actualizar estado_contratacion
    @api.onchange(
        'reinicio', 'suspension', 'adicion', 'prorroga', 
        'adicion_prorroga', 'otro_si_modificatorio', 'acta_de_prorroga_de_la_suspension'
    )
    def _onchange_estado_contratacion(self):
        for record in self:
            # 1. Reinicio ahora pasa por Jurídico (igual que acta de prórroga de la suspensión)
            if record.reinicio:
                record.estado_contratacion = 'juridico'

            # 2. Acta de prórroga de la suspensión va directo a Jurídico
            elif record.acta_de_prorroga_de_la_suspension:
                record.estado_contratacion = 'juridico'

            # 3. Suspensión pasa por flujo de aprobación -> inicia en 'coordinador'
            elif record.suspension:
                record.estado_contratacion = 'coordinador'

            # 3. Si marcan cualquiera de los demás -> pasa a 'coordinador'
            elif (record.adicion or record.prorroga or record.adicion_prorroga or
                  record.otro_si_modificatorio):
                record.estado_contratacion = 'coordinador'
                
            # 3. Si no hay nada marcado, puedes dejarlo vacío o en su estado inicial
            else:
                record.estado_contratacion = False

    ####################################################################################################################

    

    ###################################################################################################################
    create_uid = fields.Many2one('res.users', string='Creador del proyecto: ', readonly=True,
                                 related='proyecto_relacionado.create_uid')

    creator_user_id = fields.Many2one('res.users', string='Creador del proyecto',
                                      related='proyecto_relacionado.creator_user_id', store=True)


    name2 = fields.Char(string='Para: ', related='contratacion_fuv_en_id.name')
    nit2 = fields.Char(string="Nit", related='contratacion_fuv_en_id.nit')
    objeto_contrato2 = fields.Text(string='Objeto del contrato', related='contratacion_fuv_en_id.objeto_contrato')
    valor_en_letras2 = fields.Text('Valor en letras', related='contratacion_fuv_en_id.valor_en_letras')
    fecha_iniciacion2 = fields.Date('Fecha de iniciación', related='contratacion_fuv_en_id.fecha_iniciacion')
    fecha_terminacion2 = fields.Date('Fecha de terminación', related='contratacion_fuv_en_id.fecha_terminacion')
    valor_total_contrato2 = fields.Monetary('Valor total contrato',
                                            related='contratacion_fuv_en_id.valor_total_contrato')

    relacion_ids = fields.One2many('otrosi_bdd', 'fuente_id', string='Relación de otrosíes')


    def unlink(self):
        # Iterar por cada registro que se va a eliminar
        for record in self:
            # Eliminar los registros relacionados en 'relacion_ids'
            if record.relacion_ids:
                record.relacion_ids.unlink()
            
            # Verificar si existe un archivo de Drive vinculado
            if record.drive_file_id:
                try:
                    # Intentar eliminar el archivo vinculado en Google Drive
                    record.drive_file_id.unlink()
                except Exception as e:
                    raise UserError(f"No se pudo eliminar la carpeta de Google Drive: {str(e)}")
        
        # Llamar al método original para eliminar el registro de Odoo
        return super(OtroSi, self).unlink()


    @api.onchange('name')
    def _onchange_new_name(self):
        """Cuando se cambia el new_name, actualiza el folder_name en drive.file y Google Drive."""
        if self.name and self.drive_file_id:
            # Actualiza el folder_name en el modelo drive.file
            self.drive_file_id.folder_name = self.name

            # Actualiza el nombre en Google Drive
            self._update_folder_name_in_drive(self.drive_file_id.folder_id, self.name)

    def _get_nombre_contratista_correo(self):
        """Obtiene el nombre base del contratista sin el tipo ni el consecutivo del otrosí."""
        self.ensure_one()
        contratacion = self.contratacion_fuv_en_id
        if contratacion:
            return contratacion.nombre_contratista or contratacion.new_name or 'No definido'
        if self.contratista_proveedor_relacionado:
            return (
                self.contratista_proveedor_relacionado.razon_social
                or self.contratista_proveedor_relacionado.name
                or 'No definido'
            )
        return 'No definido'

    def _get_asunto_correo_con_contratista(self, asunto):
        """Agrega el nombre limpio del contratista al asunto existente."""
        self.ensure_one()
        return f"{asunto} - {self._get_nombre_contratista_correo()}"

    def _get_asunto_correo_no_aprobado(self, nombre_proyecto):
        """Deja el resultado de la revisión al final del asunto."""
        self.ensure_one()
        labels = self._get_labels_correo_documento_otrosi()
        asunto = self._get_asunto_correo_con_contratista(
            f"{nombre_proyecto} - {labels['no_aprobado_documento']}"
        )
        return f"{asunto} - {labels['no_aprobado_estado']}"

    def _render_email_sgo(self, titulo, icono, nombre_destinatario, mensaje_cuerpo, formulario_url, observaciones=None):
        COLOR = "#043484"
        observaciones = self._texto_observacion_flujo_viejo(observaciones)
        nombre_contratista = self._get_nombre_contratista_correo()
        nombre_proyecto = (
            self.proyecto_relacionado.new_name
            or self.proyecto_relacionado.name
            or ''
        ) if self.proyecto_relacionado else (self.new_name or '')
        obs_html = f'<div style="margin: 10px 0; padding: 10px; background-color: #fff8e1; border-left: 4px solid #f9a825;"><strong>Observaciones:</strong> {observaciones}</div>' if observaciones else ''
        return f"""
        <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
            <div style="background-color: {COLOR}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                <h3 style="margin: 0; font-size: 18px;">{icono} {titulo}</h3>
            </div>
            <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                <p>Cordial saludo, <strong>{nombre_destinatario}</strong>:</p>
                <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR};">
                    <div style="margin-bottom: 6px;"><strong>Contratista:</strong> {nombre_contratista}</div>
                    <strong>Proyecto:</strong> {nombre_proyecto}
                </div>
                <p>{mensaje_cuerpo}</p>
                {obs_html}
                <p style="text-align: center; margin-top: 30px;">
                    <a href="{formulario_url}"
                       style="background-color: {COLOR}; color: white; padding: 10px 20px;
                              text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                        &#10145; Abrir en SGO
                    </a>
                </p>
                <p style="margin-top: 20px; font-size: 12px; color: #999;">
                    Este es un mensaje automático de SGO.
                </p>
            </div>
        </div>"""

    def _texto_observacion_flujo_viejo(self, observacion):
        """Convierte observaciones HTML del flujo viejo en texto limpio para correos y chatter."""
        return (html2plaintext(observacion or '') or '').strip()

    def _post_observacion_flujo_viejo(self, rol, observaciones):
        """Publica en chatter las observaciones del flujo viejo una sola vez por accion."""
        observaciones = self._texto_observacion_flujo_viejo(observaciones)
        if not observaciones:
            return
        observaciones_html = html_escape(observaciones).replace('\n', '<br/>')
        self.message_post(
            body=(
                f"<p><strong>Observaciones del flujo anterior - {rol}</strong></p>"
                f"<p>{observaciones_html}</p>"
            ),
            subtype_xmlid='mail.mt_note',
        )

    def _destinatarios_juridico_flujo_viejo(self, usuarios_base=None):
        """Retorna director jurídico del proyecto y jurídicos asignados al otrosí para el flujo viejo."""
        grupo_director = self.env.ref('Matriz.group_contratacion_director_juridico')
        grupo_juridico = self.env.ref('Matriz.group_contratacion_juridico')
        usuarios_base = usuarios_base or self.proyecto_relacionado.users_with_access
        directores = usuarios_base.filtered(lambda user: grupo_director in user.groups_id)
        juridicos_asignados = self.usuarios_juridico_ids.filtered(
            lambda user: grupo_juridico in user.groups_id or grupo_director in user.groups_id
        )
        return directores | juridicos_asignados

    def _update_folder_name_in_drive(self, folder_id, name):
        """Actualiza el nombre de la carpeta en Google Drive."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            folder = drive.CreateFile({'id': folder_id})
            folder['title'] = name
            folder.Upload()
            _logger.info(f"Nombre de la carpeta en Google Drive actualizado a {name}")
        except Exception as e:
            _logger.error(f"Error al actualizar el nombre de la carpeta en Google Drive: {str(e)}")
            raise UserError("No se pudo actualizar el nombre en Google Drive.")

    # Samuel ###########################################################################################################
    fecha_iniciacion_texto = fields.Char('Fecha de iniciación (texto)', compute='_compute_fecha_iniciacion_texto',
                                         store=True)
    fecha_terminacion_texto = fields.Char('Fecha de terminación (texto)', compute='_compute_fecha_terminacion_texto',
                                          store=True)




    @staticmethod
    def convertir_fecha_a_texto(fecha):
        """ Convierte una fecha en texto con formato '04 de Octubre del 2024'. """
        if fecha:
            # Diccionario de meses en español
            meses = [
                'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
            ]
            # La fecha ya es un objeto datetime.date, así que podemos usarlo directamente
            fecha_dt = fecha  # No necesitas convertir
            return f"{fecha_dt.day} de {meses[fecha_dt.month - 1]} del {fecha_dt.year}"
        return 'Fecha no asignada'

    # Función para convertir y almacenar la fecha de iniciación en texto
    @api.depends('fecha_iniciacion')
    def _compute_fecha_iniciacion_texto(self):
        for record in self:
            record.fecha_iniciacion_texto = self.convertir_fecha_a_texto(record.fecha_iniciacion)

    # Función para convertir y almacenar la fecha de terminación en texto
    @api.depends('fecha_terminacion')
    def _compute_fecha_terminacion_texto(self):
        for record in self:
            record.fecha_terminacion_texto = self.convertir_fecha_a_texto(record.fecha_terminacion)

    ####################################################################################################################
    generar_informe_visible = fields.Boolean(string='Visible para Generar Informe', default=False)


    # contratado Persona natural

    def _marcar_contratacion_padre_como_suspendida(self):
        for record in self.filtered('suspension'):
            contratacion = record.contratacion_fuv_en_id
            if not contratacion:
                raise ValidationError(
                    _("La suspensión no tiene una contratación FUV relacionada.")
                )
            if contratacion.estado_contratacion != 'contratado':
                raise ValidationError(
                    _("La contratación FUV debe estar en estado Contratado para aplicar la suspensión.")
                )

            contratacion.message_post(
                body=(
                    "<p><b>Suspensión formalizada mediante otrosí.</b></p>"
                    f"<p>La suspensión fue formalizada desde el registro "
                    f"<b>{record.display_name}</b>. El estado de la contratación FUV "
                    "permanece en <b>Contratado</b>; la suspensión se controla desde el otrosí.</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )

    # def _marcar_contratacion_padre_como_reiniciada(self):
    #     for record in self.filtered('reinicio'):
    #         contratacion = record.contratacion_fuv_en_id
            
    #         if not contratacion:
    #             raise ValidationError(
    #                 _("El reinicio no tiene una contratación FUV relacionada.")
    #             )
            
    #         # Usando tu Enum en lugar del string 'contratado'
    #         if contratacion.estado_contratacion == EstadoContratacion.CONTRATADO.value:
    #             continue
                
    #         # Usando tu Enum en lugar del string 'suspendido'
    #         if contratacion.estado_contratacion != EstadoContratacion.SUSPENDIDO.value:
    #             raise ValidationError(
    #                 _("La contratación FUV debe estar en estado 'Suspendido' para poder aplicar el reinicio.")
    #             )

    #         # Escribiendo el estado usando el Enum
    #         contratacion.write({'estado_contratacion': EstadoContratacion.CONTRATADO.value})
            
    #         contratacion.message_post(
    #             body=(
    #                 "<p><b>Contrato reiniciado mediante otrosí.</b></p>"
    #                 f"<p>El reinicio fue formalizado desde el registro "
    #                 f"<b>{record.display_name}</b>. La contratación pasó de "
    #                 "<b>Suspendido</b> a <b>Contratado</b>.</p>"
    #             ),
    #             subtype_xmlid='mail.mt_note',
    #         )
            
    def contratado2(self):
        self.write({'estado_contratacion': 'contratado'})
        self.notificar_contratado_fc()
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':

            # Marcar la acción como realizada solo si el estado es "coordinador"
            if self.estado_contratacion == 'juridico':
                self.write({'aprobacion_juridico_realizada': True})

            return

    generar_v = fields.Boolean(string='Visible para Generar Informe', default=False)

    def aprueba_juridico(self):
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de juridico ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "juridico"
        if self.estado_contratacion == 'juridico':
            self.write({'aprobacion_juridico_realizada': True})

        if self._omite_recursos_humanos_otrosi():
            self.write({'estado_contratacion': 'coordinador'})
            self.message_post(
                body=(
                    "✅ <strong>Aprobación jurídica registrada</strong><br/>"
                    "Este tipo de solicitud no pasa por Recursos Humanos; "
                    "el estado pasó a <strong>Coordinador para contratar</strong>."
                )
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Enviado a coordinador',
                    'sticky': False,
                    'message': 'Aprobación jurídica registrada. El estado pasó a Coordinador para contratar.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 6,
                }
            }

        # Obtener los usuarios del grupo de recursos humanos desde los campos relacionados
        recursos_humanos = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_recursos') in user.groups_id
        )

        # Obtener el usuario jurídico actual
        juridico = self.env.user
        observaciones_juri = self._texto_observacion_flujo_viejo(self.observaciones_juri)
        self._post_observacion_flujo_viejo('Jurídico', observaciones_juri)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de recursos humanos
        if recursos_humanos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, departamento de recursos humanos, el departamento jurídico ha aprobado la contratación del contratista <strong>{self.new_name}</strong>, por favor revisar."""


            for recursos_humano in recursos_humanos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Enviar correo electrónico al departamento de recursos humanos
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Recursos Humanos",
                        icono="&#128101;",
                        nombre_destinatario=recursos_humano.name,
                        mensaje_cuerpo=f"El departamento jurídico ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}, por favor revisar.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_juri if observaciones_juri else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Aprobación de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': juridico.email,
                        'email_to': recursos_humano.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, recursos_humano.partner_id.id), (4, juridico.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {recursos_humano.name}',
                    })
                    chat_message = f"<b>Aprobación de jurídico</b>\n\n{mensaje_con_enlace}"
                    chat_channel.sudo().message_post(
                        body=chat_message,
                        author_id=self.env.user.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {recursos_humano.name}: {str(e)}")

            # Actualizar el estado de la contratación
            self.write({'estado_contratacion': 'recursos_humanos'})

            # Notificación de éxito
            return False
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo recursos humanos no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                    }
                }



    #Jurídico no aprueba PN

    def juridico_no_Aprobado(self):
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de juridico ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'juridico':
            self.write({'aprobacion_juridico_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles financieros
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        juridico = self.env.user

        # Obtener las observaciones
        observaciones_juri = self._texto_observacion_flujo_viejo(self.observaciones_juri)
        self._post_observacion_flujo_viejo('Jurídico', observaciones_juri)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, coordinador, el departamento jurídico <b>no ha aprobado</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

            if observaciones_juri:
                mensaje += f"\nObservaciones: {observaciones_juri}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML
                    body_html = self._render_email_sgo(
                        titulo=self._get_labels_correo_documento_otrosi()['no_aprobado_titulo'],
                        icono="&#10060;",
                        nombre_destinatario=coordinador.name,
                        mensaje_cuerpo=f"El departamento jurídico no ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}, por favor revisar.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_juri if observaciones_juri else None
                    )

                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': self._get_asunto_correo_no_aprobado(nombre_proyecto),
                        'email_from': juridico.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                    # Actualizar el estado de la contratación
                    self.write({
                        'estado_contratacion': 'coordinador',
                        'estado_contratacion_nun': '2',
                        'aprobacion_coordinador_realizada': False,
                        'aprobacion_juridico_realizada': False,
                    })

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Notificación de éxito
            return False
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")


    ##################################################################################################################

    @api.depends(
        'aprueba_j_h_su_arl',
        'suspension',
        'reinicio',
        'acta_de_prorroga_de_la_suspension',
    )
    def _compute_show_aprueba_juridico(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_j_h_su_arl,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_juridico = all_approved and not record._omite_recursos_humanos_otrosi()

    show_aprueba_juridico = fields.Boolean(
        string='Mostrar botón de aprueba_juridico',
        compute='_compute_show_aprueba_juridico',
    )

    #############################################################################################
    estado_contratacion_nun_juridica = fields.Selection([
        ('1', 'Coordinador'),
        ('2', 'Juridico'),
    ], string='Estado Contratación', default='1', tracking=True)

    estado_contratacion_nun = fields.Selection([
        ('1', 'Coordinador'),
        ('2', 'Juridico'),
        ('3', 'Recursos humanos'),
    ], string='Estado Contratación', default='1', tracking=True)
    # Botones del footer para aprobación

    def aprueba_coordinador(self, documentos=None):
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})


        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.proyecto_relacionado.users_with_access | self.proyecto_relacionado.users_with_access_financiero)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        users_with_financial_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id
            )
        )

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_viejo(self.observaciones)
        self._post_observacion_flujo_viejo('Coordinador', observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios financieros relacionados con el proyecto
        if users_with_financial_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, financiero, el coordinador ha aprobado el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for financiero in users_with_financial_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Financiera",
                        icono="&#128179;",
                        nombre_destinatario=financiero.name,
                        mensaje_cuerpo=f"El coordinador ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}. Por favor, revisarlo.",
                        formulario_url=formulario_url,
                        observaciones=observaciones if observaciones else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Aprobación de documentos de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': coordinador.email,
                        'email_to': financiero.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            self.write({'estado_contratacion': 'financiera'})

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return False
        else:
            raise UserError("No hay usuarios con el rol financiero asociados al proyecto.")


    #Nueva función para enviar a juridica sin prorroga está activo
    def aprueba_c_con_prorroga(self, documentos=None):
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})


        users_with_juridicos_role = self._destinatarios_juridico_flujo_viejo(self.users_with_access)

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_viejo(self.observaciones)
        self._post_observacion_flujo_viejo('Coordinador', observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios Jurídico relacionados con el proyecto
        if users_with_juridicos_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, departamento jurídico, El coordinador ha aprobado el documento <strong>{self.name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for juridicos in users_with_juridicos_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Jurídico",
                        icono="&#9878;",
                        nombre_destinatario=juridicos.name,
                        mensaje_cuerpo=f"El coordinador ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}. Por favor, revisarlo.",
                        formulario_url=formulario_url,
                        observaciones=observaciones if observaciones else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Aprobación de documentos de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': coordinador.email,
                        'email_to': juridicos.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridicos.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.notificar_financiero()
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            self.write({'estado_contratacion': 'juridico'})

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return False
        else:
            raise UserError("No hay usuarios con el rol juridico asociados al proyecto.")

    # Notificar financiero
    def notificar_financiero(self, documentos=None):

        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Obtener usuarios financieros del proyecto
        usuarios_combinados = (
                self.proyecto_relacionado.users_with_access | self.proyecto_relacionado.users_with_access_financiero
        )
        users_with_financial_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id
            )
        )

        # Obtener datos adicionales
        coordinador = self.env.user
        observaciones = self._texto_observacion_flujo_viejo(self.observaciones)
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombre_proyecto = ', '.join(filter(None, nombres_proyecto))  # Filtra nombres vacíos

        # Verificar si hay usuarios financieros
        if users_with_financial_role:
            for financiero in users_with_financial_role:
                try:
                    # URL del formulario en Odoo
                    formulario_url = f"/web#id={self.id}&view_type=form&model=otro_si"
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    # Cuerpo del correo
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Financiera",
                        icono="&#128179;",
                        nombre_destinatario=financiero.name,
                        mensaje_cuerpo=f"El coordinador ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}.",
                        formulario_url=formulario_url,
                        observaciones=observaciones if observaciones else None
                    )

                    # Enviar el correo
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Aprobación de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': coordinador.email,
                        'email_to': financiero.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }
                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

            # Trazabilidad en el chatter
            mensaje_trazabilidad = f"""
            📤 <strong>Notificación enviada al área financiera</strong><br/>
            <strong>Proyecto:</strong> {nombre_proyecto}<br/>
            <strong>Contratista:</strong> {self.name}<br/>
            <strong>Coordinador:</strong> {coordinador.name}<br/>
            {f'<strong>Observaciones:</strong> {observaciones}<br/>' if observaciones else ''}
            """
            self.message_post(body=mensaje_trazabilidad)


            return False
        else:
            raise UserError("No hay usuarios con el rol financiero asociados al proyecto.")

    def aprueba_coordinador_juridico(self, documentos=None):
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})


        users_with_juridicos_role = self._destinatarios_juridico_flujo_viejo(self.users_with_access)

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_viejo(self.observaciones)
        self._post_observacion_flujo_viejo('Coordinador', observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios Jurídico relacionados con el proyecto
        if users_with_juridicos_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, departamento jurídico, El coordinador ha aprobado y corregido el documento <strong>{self.name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for juridicos in users_with_juridicos_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Jurídico",
                        icono="&#9878;",
                        nombre_destinatario=juridicos.name,
                        mensaje_cuerpo=f"El coordinador ha aprobado y corregido el {self._get_labels_correo_documento_otrosi()['revision_documento']}. Por favor, revisarlo.",
                        formulario_url=formulario_url,
                        observaciones=observaciones if observaciones else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Aprobación de documentos de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': coordinador.email,
                        'email_to': juridicos.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridicos.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            self.write({'estado_contratacion': 'juridico'})

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return False
        else:
            raise UserError("No hay usuarios con el rol juridico asociados al proyecto.")

    def aprueba_coordinador_rrhh(self, documentos=None):
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})

        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.users_with_access)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        users_with_recursos_humanos_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_recursos') in user.groups_id
            )
        )

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_viejo(self.observaciones)
        self._post_observacion_flujo_viejo('Coordinador', observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios financieros relacionados con el proyecto
        if users_with_recursos_humanos_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
             Hola, departamento recursos humanos, El coordinador ha aprobado y corregido el documento <strong>{self.name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for recursos_humanos in users_with_recursos_humanos_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Recursos Humanos",
                        icono="&#128101;",
                        nombre_destinatario=recursos_humanos.name,
                        mensaje_cuerpo=f"El coordinador ha aprobado y corregido el {self._get_labels_correo_documento_otrosi()['revision_documento']}. Por favor, revisarlo.",
                        formulario_url=formulario_url,
                        observaciones=observaciones if observaciones else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Aprobación de documentos de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': coordinador.email,
                        'email_to': recursos_humanos.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {recursos_humanos.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            self.write({'estado_contratacion': 'recursos_humanos'})

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return False
        else:
            raise UserError("No hay usuarios con el rol juridico asociados al proyecto.")




    #############################################################################################################

    def aprueba_coordinador_j_2(self, documentos=None):
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }


        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})

        # Obtener el usuario coordinador actual
        coordinador = self.env.user


        users_with_juridicos_role = self._destinatarios_juridico_flujo_viejo()


        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_viejo(self.observaciones)
        self._post_observacion_flujo_viejo('Coordinador', observaciones)

        # Verificar si hay usuarios Jurídico relacionados con el proyecto
        if users_with_juridicos_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, Jurídico, el coordinador ha aprobado y corregido el documento <strong>{self.name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for juridicos in users_with_juridicos_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Jurídico",
                        icono="&#9878;",
                        nombre_destinatario=juridicos.name,
                        mensaje_cuerpo=f"El coordinador ha aprobado y corregido el {self._get_labels_correo_documento_otrosi()['revision_documento']}. Por favor, revisarlo.",
                        formulario_url=formulario_url,
                        observaciones=observaciones if observaciones else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Aprobación de documentos de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': coordinador.email,
                        'email_to': juridicos.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat pero no enviar correo electrónico
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridicos.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridicos.name}',
                    })
                    chat_channel.sudo().message_post(
                        body=mensaje_con_enlace,
                        subject='Aprobación de contratación',
                        author_id=coordinador.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                        partner_ids=[coordinador.id]
                    )

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridicos.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            self.write({'estado_contratacion': 'juridico'})

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return False
        else:
            raise UserError("No hay usuarios con el rol Jurídico asociados al proyecto.")



    ############################################################################################################

    @api.depends('su_arl')
    def _compute_show_aprueba_coordinador(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.su_arl,

            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_coordinador = all_approved

    show_aprueba_coordinador = fields.Boolean(
        string='Mostrar botón de aprueba_coordinador',
        compute='_compute_show_aprueba_coordinador',
    )

    ######################################################################
    @api.depends(
        'aprueba_h_h_su_arl',
        'select_persona',
        'adicion',
        'prorroga',
        'adicion_prorroga',
        'otro_si_modificatorio',
        'tipo_modificacion_modificatorio',
        'suspension',
        'reinicio',
        'acta_de_prorroga_de_la_suspension',
    )
    def _compute_show_aprueba_recursos_humanos(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_h_h_su_arl,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_recursos_humanos = all_approved and record._requiere_recursos_humanos_otrosi()

    show_aprueba_recursos_humanos = fields.Boolean(
        string='Mostrar botón de aprueba_recursos_humanos',
        compute='_compute_show_aprueba_recursos_humanos',
    )

    aprobacion_recursos_humanos_realizada = fields.Boolean(string='Aprobación de recursos_humanos Realizada',
                                                           default=False)


    @api.onchange('aprobacion_recursos_humanos_realizada')
    def confirmar_aprobacion_juridico(self):
        if self.aprobacion_recursos_humanos_realizada and self.estado_contratacion == 'estado_contratacion':
            self.boolean_contratado = True

    boolean_contratado = fields.Boolean(string='Contratado', default=False)

    #############################################################################################
    def aprueba_recursos_humanos(self):
        self.contratado_fin_visible = True
        if self.aprobacion_recursos_humanos_realizada and self.estado_contratacion != 'recursos_humanos':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de recursos humanos ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "recursos_humanos"
        if self.estado_contratacion == 'recursos_humanos':
            self.write({'aprobacion_recursos_humanos_realizada': True})

            # Después de realizar las acciones necesarias, actualiza el campo generar_informe_visible
        if self.select_persona in ['persona_natural', 'contrato_laboral']:
            self.write({'generar_informe_visible': True})
        else:
            self.write({'generar_informe_visible': False})

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Recursos humanos devuelve el trámite al jurídico asignado por el director.
        juridicos = self.usuarios_juridico_ids.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_juridico') in user.groups_id
            )
        )


        # Obtener el usuario de recursos humanos actual
        recursos_humanos = self.env.user
        observaciones_recursos = self._texto_observacion_flujo_viejo(self.observaciones_recursos)
        self._post_observacion_flujo_viejo('Recursos Humanos', observaciones_recursos)

        # Verificar si hay usuarios en el grupo jurídico
        if juridicos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, jurídico, el departamento de recursos humanos informa que todo está listo para generar/revisar el documento <strong>{self.name}</strong>."""

            for juridico in juridicos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Jurídico",
                        icono="&#9878;",
                        nombre_destinatario=juridico.name,
                        mensaje_cuerpo=f"El departamento de recursos humanos informa que todo está listo para generar/revisar el {self._get_labels_correo_documento_otrosi()['revision_documento']}.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_recursos if observaciones_recursos else None
                    )

                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Listo para generar {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': recursos_humanos.email,
                        'email_to': juridico.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridico.partner_id.id), (4, recursos_humanos.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridico.name}',
                    })
                    chat_channel.sudo().message_post(
                        body=mensaje_con_enlace,
                        author_id=recursos_humanos.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )


                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridico.name}: {str(e)}")

            # Actualizar el estado de la contratación
            self.write({'estado_contratacion': 'juridico'})
            self.write({'estado_contratacion_2': 'juridico_2'})

            # Notificación de éxito
            return False
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo jurídico no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                    }
                }



    def humanos_no_Aprobado(self):
        if self.aprobacion_recursos_humanos_realizada and self.estado_contratacion != 'recursos_humanos':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de recursos_humanos ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'recursos_humanos':
            self.write({'aprobacion_recursos_humanos_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles financieros
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        recursos_humanos = self.env.user

        # Obtener las observaciones
        observaciones_recursos = self._texto_observacion_flujo_viejo(self.observaciones_recursos)
        self._post_observacion_flujo_viejo('Recursos Humanos', observaciones_recursos)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>"\
                        Hola, coordinador. El departamento de recursos humanos <b>no ha aprobado</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

            if observaciones_recursos:
                mensaje += f"\nObservaciones: {observaciones_recursos}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML
                    body_html = self._render_email_sgo(
                        titulo="Documento devuelto",
                        icono="&#10060;",
                        nombre_destinatario=coordinador.name,
                        mensaje_cuerpo=f"El departamento de recursos humanos no ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}, por favor revisar.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_recursos if observaciones_recursos else None
                    )
                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': self._get_asunto_correo_no_aprobado(nombre_proyecto),
                        'email_from': recursos_humanos.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Actualizar el estado de la contratación
                    self.write({
                        'estado_contratacion': 'coordinador',
                        'estado_contratacion_nun': '3',
                        'aprobacion_coordinador_realizada': False,
                        'aprobacion_recursos_humanos_realizada': False,
                    })

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Notificación de éxito
            return False
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")


    #############################################################################################################

    def aprueba_financiera(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "financiera"
        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})

        juridicos = self._destinatarios_juridico_flujo_viejo()

        # Obtener el usuario financiero actual
        financiero = self.env.user
        observaciones_finacie = self._texto_observacion_flujo_viejo(self.observaciones_finacie)
        self._post_observacion_flujo_viejo('Financiera', observaciones_finacie)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo jurídico
        if juridicos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                         Hola, departamento jurídico. El departamento financiero le informa que ya puedes revisar <strong>{self._get_labels_correo_documento_otrosi()['revision_documento']}</strong>."""

            for juridico in juridicos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Jurídico",
                        icono="&#9878;",
                        nombre_destinatario=juridico.name,
                        mensaje_cuerpo=f"El departamento financiero le informa que ya puedes revisar {self._get_labels_correo_documento_otrosi()['revision_documento']}.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_finacie if observaciones_finacie else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Revisión de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': financiero.email,
                        'email_to': juridico.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridico.partner_id.id), (4, financiero.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridico.name} y {financiero.name}',
                    })
                    chat_message = f"<b>Aprobación de financiación</b>\n\n{mensaje_con_enlace}"
                    chat_channel.sudo().message_post(
                        body=chat_message,
                        author_id=self.env.user.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridico.name}: {str(e)}")

            # Actualizar el estado de la contratación
            self.write({'estado_contratacion': 'juridico'})

            # Notificación de éxito
            return False
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo jurídico no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                    }
                }




    def financiera_no_Aprobado(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles financieros
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        financieros = self.env.user

        # Obtener las observaciones
        observaciones_finacie = self._texto_observacion_flujo_viejo(self.observaciones_finacie)
        self._post_observacion_flujo_viejo('Financiera', observaciones_finacie)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>"\
                        Hola, coordinador. El departamento financiero <strong>no aprobó</strong> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

            if observaciones_finacie:
                mensaje += f"\nObservaciones: {observaciones_finacie}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML con observaciones incluidas
                    body_html = self._render_email_sgo(
                        titulo="Documento devuelto",
                        icono="&#10060;",
                        nombre_destinatario=coordinador.name,
                        mensaje_cuerpo=f"El departamento Financiero no aprobó el {self._get_labels_correo_documento_otrosi()['revision_documento']}, por favor revisar.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_finacie if observaciones_finacie else None
                    )

                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': self._get_asunto_correo_no_aprobado(nombre_proyecto),
                        'email_from': financieros.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }
                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Actualizar el estado de la contratación
                    self.write({
                        'estado_contratacion': 'coordinador',
                        'estado_contratacion_nun': '1',
                        'aprobacion_coordinador_realizada': False,
                        'aprobacion_financiera_realizada': False,
                    })

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Notificación de éxito
            return False
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")


    estado_contratacion_2 = fields.Selection([
        ('visible', 'Visible'),
        ('juridico_2', 'Jurídico'),
    ], string='Estado Contratación', default='visible')

    @api.depends()
    def _compute_show_aprueba_coordinador_j(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([


            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_coordinador_j = all_approved

    show_aprueba_coordinador_j = fields.Boolean(
        string='Mostrar botón de aprueba_coordinador',
        compute='_compute_show_aprueba_coordinador_j',
    )

    #####################################################################
    @api.depends('aprueba_f_cdp_juridico')

    def _compute_show_aprueba_financiera_j(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_f_cdp_juridico
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_financiera_j = all_approved

    show_aprueba_financiera_j = fields.Boolean(
        string='Mostrar botón de aprueba_financiera',
        compute='_compute_show_aprueba_financiera_j',
    )

    aprobacion_financiera_realizada = fields.Boolean(string='Aprobación de financiera Realizada', default=False)
    generar_informe_visible_j = fields.Boolean(string='Visible para Generar Informe', default=False)

    def aprueba_financiera_j(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "financiera"
        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})

            # Después de realizar las acciones necesarias, actualiza el campo generar_informe_visible_j
            if self.select_persona == 'persona_juridica':
                self.write({'generar_informe_visible_j': True})
            else:
                self.write({'generar_informe_visible_j': False})

        # Obtener los usuarios del grupo jurídico desde los campos relacionados

        juridicos = self._destinatarios_juridico_flujo_viejo()

        # Obtener el usuario financiero actual
        financiero = self.env.user
        observaciones_finacie = self._texto_observacion_flujo_viejo(self.observaciones_finacie)
        self._post_observacion_flujo_viejo('Financiera', observaciones_finacie)


        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo jurídico
        if juridicos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                         Hola, departamento jurídico. El departamento financiero le informa que ya puedes revisar <strong>{self._get_labels_correo_documento_otrosi()['revision_documento']}</strong>."""

            for juridico in juridicos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML
                    body_html = self._render_email_sgo(
                        titulo="Revisión pendiente - Jurídico",
                        icono="&#9878;",
                        nombre_destinatario=juridico.name,
                        mensaje_cuerpo=f"El departamento financiero le informa que ya puedes revisar {self._get_labels_correo_documento_otrosi()['revision_documento']}.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_finacie if observaciones_finacie else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_con_contratista(
                            f"{nombre_proyecto} - Revisión de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                        ),
                        'email_from': financiero.email,
                        'email_to': juridico.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridico.partner_id.id), (4, financiero.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridico.name} y {financiero.name}',
                    })
                    chat_message = f"<b>Aprobación de financiación</b>\n\n{mensaje_con_enlace}"
                    chat_channel.sudo().message_post(
                        body=chat_message,
                        author_id=self.env.user.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )

                except Exception as e:
                    raise UserError(f"No se pudo enviar el mensaje a {juridico.name}: {str(e)}")

            # Actualizar el estado de la contratación
            self.write({'estado_contratacion': 'juridico'})

            # Notificación de éxito
            return False
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo jurídico no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                    }
                }

    ####################################################################################################################

    def financiera_no_Aprobado_j(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})


        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles financieros
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asis_coordinador in user.groups_id
        )

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener el usuario jurídico actual
        financieros = self.env.user

        # Obtener las observaciones
        observaciones_finacie = self._texto_observacion_flujo_viejo(self.observaciones_finacie)
        self._post_observacion_flujo_viejo('Financiera', observaciones_finacie)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>"\
                        Hola, coordinador. El departamento financiero <strong>no aprobó</strong> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""


            if observaciones_finacie:
                mensaje += f"\nObservaciones: {observaciones_finacie}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Enviar correo electrónico al coordinador
                    body_html = self._render_email_sgo(
                        titulo="Documento devuelto",
                        icono="&#10060;",
                        nombre_destinatario=coordinador.name,
                        mensaje_cuerpo=f"El departamento financiero no aprobó el {self._get_labels_correo_documento_otrosi()['revision_documento']}, por favor revisar.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_finacie if observaciones_finacie else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_no_aprobado(nombre_proyecto),
                        'email_from': financieros.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    self.write({
                        'estado_contratacion': 'coordinador',
                        'aprobacion_coordinador_realizada': False,
                        'aprobacion_financiera_realizada': False,
                    })

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Notificación de éxito
            return False
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")

    ####################################################################################################################

    @api.depends('aprueba_j_h_su_arl' )
    def _compute_show_aprueba_juridico_j(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_j_h_su_arl,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_juridico_j = all_approved

    show_aprueba_juridico_j = fields.Boolean(
        string='Mostrar botón de aprueba_juridico',
        compute='_compute_show_aprueba_juridico_j',
    )
    aprobacion_juridico_realizada = fields.Boolean(string='Aprobación de juridico Realizada', default=False)
    generar_vi = fields.Boolean(string='Visible para Generar Informe', default=False)

    def juridico_no_Aprobado_j(self):
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de juridico ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'juridico':
            self.write({'aprobacion_juridico_realizada': True})
        # Obtener los usuarios del grupo de coordinadores
        coordinadores = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)
        ])

        # Obtener el usuario jurídico actual
        juridico = self.env.user

        # Obtener las observaciones
        observaciones_juri = self._texto_observacion_flujo_viejo(self.observaciones_juri)
        self._post_observacion_flujo_viejo('Jurídico', observaciones_juri)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"Hola, coordinador, el departamento jurídico <b>no ha aprobado</b> el {self._get_labels_correo_documento_otrosi()['revision_documento']}, por favor revisar."

            if observaciones_juri:
                mensaje += f"\nObservaciones: {observaciones_juri}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Enviar correo electrónico al coordinador
                    body_html = self._render_email_sgo(
                        titulo=self._get_labels_correo_documento_otrosi()['no_aprobado_titulo'],
                        icono="&#10060;",
                        nombre_destinatario=coordinador.name,
                        mensaje_cuerpo=f"El departamento jurídico no ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}, por favor revisar.",
                        formulario_url=formulario_url,
                        observaciones=observaciones_juri if observaciones_juri else None
                    )
                    mail_values = {
                        'subject': self._get_asunto_correo_no_aprobado(nombre_proyecto),
                        'email_from': juridico.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                    # Actualizar el estado de la contratación
                    self.write({
                        'estado_contratacion': 'coordinador',
                        'estado_contratacion_nun_juridica': '2',
                        'aprobacion_coordinador_realizada': False,
                        'aprobacion_juridico_realizada': False,
                    })

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Notificación de éxito
            return False
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")

    # contratado Persona Juridica

    def contratado(self):
        self.write({'estado_contratacion': 'contratado'})
        self.notificar_contratado_fc()
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':

            # Marcar la acción como realizada solo si el estado es "coordinador"
            if self.estado_contratacion == 'juridico':
                self.write({'aprobacion_juridico_realizada': True})

            return

    ################################################################################################################
    contratado_fin = fields.Boolean('Contratado', tracking=True, default=False)
    contratado_fin_j = fields.Boolean('Contratado', tracking=True, default=False)

    def notificar_contratado_fc(self):
        print('notificar_contratado_fc')
        self.contratado_fin = True
        self.contratado_fin_j = True
        # Obtener el nombre del proyecto (filtrando valores vacíos)
        nombres_proyecto = filter(None, [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name])
        nombre_proyecto = ', '.join(nombres_proyecto)

        # Obtener coordinadores, asistentes y director jurídico del proyecto
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id or
                         self.env.ref('Matriz.group_contratacion_asis_coordinador') in user.groups_id or
                         self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id
        )

        if not coordinadores:
            raise UserError("No hay coordinadores asignados para recibir la notificación.")

        # Construir la URL del formulario
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=otro_si'

        if self.suspension or self.acta_de_prorroga_de_la_suspension or self.reinicio or self.otro_si_modificatorio:
            titulo_estado_final = "Proceso finalizado"
            texto_estado_final = (
                f"El proceso de {self._get_labels_correo_documento_otrosi()['revision_documento']} "
                "ha finalizado correctamente."
            )
            asunto_estado_final = (
                f"Proceso finalizado de {self._get_labels_correo_documento_otrosi()['revision_documento']} "
                f"del proyecto {nombre_proyecto}"
            )
        else:
            titulo_estado_final = "Contratado"
            texto_estado_final = (
                f"El {self._get_labels_correo_documento_otrosi()['revision_documento']} "
                "ha alcanzado su estado final: "
                "<strong>Contratado</strong>."
            )
            asunto_estado_final = (
                f"{self._get_labels_correo_documento_otrosi()['revision_documento']} "
                f"del proyecto {nombre_proyecto} ha sido contratado"
            )

        # Enviar correo a cada coordinador
        for coordinador in coordinadores:
            try:
                mensaje_base = self._render_email_sgo(
                    titulo=titulo_estado_final,
                    icono="&#9989;",
                    nombre_destinatario=coordinador.name,
                    mensaje_cuerpo=texto_estado_final,
                    formulario_url=formulario_url,
                    observaciones=None,
                )
                mail_values = {
                    'subject': self._get_asunto_correo_con_contratista(asunto_estado_final),
                    'email_from': self.env.user.email,
                    'email_to': coordinador.email,
                    'body_html': mensaje_base,
                    'auto_delete': True,
                }
                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"Error al enviar correo a {coordinador.name}: {str(e)}")

        return False
    ######################################################################

    aprobacion_coordinador_realizada = fields.Boolean(string='Aprobación de Coordinador Realizada', default=False)

    def aprueba_coordinador_j(self):
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})

        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (
                    self.contratacion_fuv_en_id.users_with_access | self.contratacion_fuv_en_id.users_with_access_financiero)

        users_with_financial_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id
            )
        )

        # Verificar si hay usuarios financieros antes de continuar
        if not users_with_financial_role:
            raise UserError(f"No hay usuarios con el rol financiero asociados al proyecto. "
                            f"Usuarios encontrados: {[user.name for user in usuarios_combinados]}")

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.new_name, self.proyecto_relacionado.name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_viejo(self.observaciones)
        self._post_observacion_flujo_viejo('Coordinador', observaciones)

        # Construir el mensaje base
        mensaje = f"""Proyecto: {nombre_proyecto}
        Hola, financiero, el coordinador ha aprobado el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

        if observaciones:
            mensaje += f"\nObservaciones: {observaciones}"

        for financiero in users_with_financial_role:
            try:
                # Construir la URL con la base de Odoo
                formulario_url = f'/web#id={self.id}&view_type=form&model=otro_si'
                formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url
                mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                # Crear el cuerpo del correo HTML
                body_html = self._render_email_sgo(
                    titulo="Revisión pendiente - Financiera",
                    icono="&#128179;",
                    nombre_destinatario=financiero.name,
                    mensaje_cuerpo=f"El coordinador ha aprobado el {self._get_labels_correo_documento_otrosi()['revision_documento']}. Por favor, revisarlo.",
                    formulario_url=formulario_url,
                    observaciones=observaciones if observaciones else None
                )
                mail_values = {
                    'subject': self._get_asunto_correo_con_contratista(
                        f"{nombre_proyecto} - Aprobación de documentos de {self._get_labels_correo_documento_otrosi()['revision_documento']}"
                    ),
                    'email_from': coordinador.email,
                    'email_to': financiero.email,
                    'body_html': body_html,
                    'auto_delete': True,
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()

            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

        # Actualizar el estado del coordinador
        self.write({'estado_contratacion': 'financiera'})

        return False
    ####################################################################################################################

    # Relatar monucipios y departamentos
    departamentos_y_municipios_ids2 = fields.One2many(related="contratacion_fuv_en_id.departamentos_y_municipios_ids")

    departamentos_y_municipios_ids = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id',
                                                     string='Tabla')


    currency_id = fields.Many2one('res.currency', string='Moneda')
    select_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('contrato_laboral', 'Contrato laboral')], tracking=True, related="contratacion_fuv_en_id.select_persona", string='Tipo de persona')

    # variables
    dependencia_area_solicitante = fields.Char(string='Dependencia - Área solicitante', tracking=True)
    nombre_solicitante = fields.Char(string='Nombre de quien solicita', tracking=True)


    nombre_solicitante4 = fields.Many2one(
        'res.users',
        string='Nombre de quien solicita',
        help='Seleccione al Nombre de quien solicita de la revisión',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)],
        related="contratacion_fuv_en_id.nombre_solicitante3",
    )



    dependencia_area_solicitante2 = fields.Selection(string='Dependencia - Área solicitante', tracking=True,
                                               related='contratacion_fuv_en_id.dependencia_area_solicitante')



    objeto_contrato = fields.Text(string='Objeto del contrato', tracking=True,
                                  related='contratacion_fuv_en_id.objeto_contrato')
    

    justificacion_solicitud = fields.Text('Justificación de la solicitud', tracking=True)
    fecha_solicitud = fields.Date('Fecha de solicitud', tracking=True)
    nombre_proyecto = fields.Char(string='Nombre del proyecto', readonly=False, tracking=True,
                                  related='contratacion_fuv_en_id.nombre_proyecto')

    # Información del contrato
    new_name_value = fields.Char(string='Valor de new_name', related='contratacion_fuv_en_id.new_name', readonly=False)
    new_name = fields.Char(string='Valor de new_name', related='contratacion_fuv_en_id.new_name', readonly=False)
    # Campos name que tomará el valor de new_name con el texto dinámico
    name = fields.Char(string="Nombre", compute='_compute_name', readonly=False)

    otrosi_number = fields.Integer(string='Número de Otrosí', compute='_compute_otrosi_number', store=True)
    reinicio_number = fields.Integer(string='Número de Reinicio', compute='_compute_reinicio_number', store=True)
    suspension_number = fields.Integer(string='Número de Suspensión', compute='_compute_suspension_number', store=False)
    prorroga_number = fields.Integer(string='Número de prorroga', store=True, compute='_compute_prorroga_number')
    adicion_number = fields.Integer(string='Número de adición', store=True, compute='_compute_adicion_number')
    adicion_prorroga_number = fields.Integer(string='Número de adición con prorroga', store=True, compute='_compute_adicion_prorroga_number')
    otro_si_modificatorio_number = fields.Integer(string='Número de Otrosí modificatorio', store=True, compute='_compute_otro_si_modificatorio_number')
    acta_de_prorroga_de_la_suspension_number = fields.Integer(string='Número de acta de prorroga de la suspensión', store=True, compute='_compute_acta_de_prorroga_de_la_suspension_number')


    @api.depends('contratacion_fuv_en_id', 'acta_de_prorroga_de_la_suspension')
    def _compute_acta_de_prorroga_de_la_suspension_number(self):
        for record in self:
            if record.acta_de_prorroga_de_la_suspension and record.contratacion_fuv_en_id:
                # 1. Buscamos TODAS las actas de este contrato específico
                registros = self.search([
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('acta_de_prorroga_de_la_suspension', '=', True)
                ], order='id asc')
                
                # 2. Si el registro ya existe en BD, sacamos su posición real
                if record._origin.id in registros.ids:
                    record.acta_de_prorroga_de_la_suspension_number = list(registros.ids).index(record._origin.id) + 1
                else:
                    # 3. Si es nuevo (aún no guardado), es la cantidad total + 1
                    record.acta_de_prorroga_de_la_suspension_number = len(registros) + 1
            else:
                # Ojo: Aquí debe ser 0 para que no sume números si el check está apagado
                record.acta_de_prorroga_de_la_suspension_number = 0
   
    
    @api.depends('contratacion_fuv_en_id', 'otro_si_modificatorio')
    def _compute_otro_si_modificatorio_number(self):
        for record in self:
            if record.otro_si_modificatorio and record.contratacion_fuv_en_id:
                registros = self._get_secuencia_global(record.contratacion_fuv_en_id.id)
                if record._origin.id in registros.ids:
                    record.otro_si_modificatorio_number = list(registros.ids).index(record._origin.id) + 1
                else:
                    record.otro_si_modificatorio_number = len(registros) + 1
            else:
                record.otro_si_modificatorio_number = 0



    def _get_secuencia_global(self, contratacion_fuv_en_id):
        """Posición del registro en la secuencia compartida de adición, prórroga, adición+prórroga y modificatorio."""
        return self.search([
            ('contratacion_fuv_en_id', '=', contratacion_fuv_en_id),
            '|', '|', '|',
            ('adicion', '=', True),
            ('prorroga', '=', True),
            ('adicion_prorroga', '=', True),
            ('otro_si_modificatorio', '=', True),
        ], order='id asc')

    def _get_numero_otrosi_global_para_registro(self, record):
        if not record.contratacion_fuv_en_id:
            return 0
        registros = self._get_secuencia_global(record.contratacion_fuv_en_id.id)
        record_id = record._origin.id or record.id
        if record_id in registros.ids:
            return list(registros.ids).index(record_id) + 1
        return len(registros) + 1

    @api.depends('contratacion_fuv_en_id', 'adicion_prorroga')
    def _compute_adicion_prorroga_number(self):
        for record in self:
            if record.adicion_prorroga and record.contratacion_fuv_en_id:
                registros = self._get_secuencia_global(record.contratacion_fuv_en_id.id)
                if record._origin.id in registros.ids:
                    record.adicion_prorroga_number = list(registros.ids).index(record._origin.id) + 1
                else:
                    record.adicion_prorroga_number = len(registros) + 1
            else:
                record.adicion_prorroga_number = 0

    @api.depends('contratacion_fuv_en_id', 'prorroga')
    def _compute_prorroga_number(self):
        for record in self:
            if record.prorroga and record.contratacion_fuv_en_id:
                registros = self._get_secuencia_global(record.contratacion_fuv_en_id.id)
                if record._origin.id in registros.ids:
                    record.prorroga_number = list(registros.ids).index(record._origin.id) + 1
                else:
                    record.prorroga_number = len(registros) + 1
            else:
                record.prorroga_number = 0

    @api.depends('contratacion_fuv_en_id', 'adicion')
    def _compute_adicion_number(self):
        for record in self:
            if record.adicion and record.contratacion_fuv_en_id:
                registros = self._get_secuencia_global(record.contratacion_fuv_en_id.id)
                if record._origin.id in registros.ids:
                    record.adicion_number = list(registros.ids).index(record._origin.id) + 1
                else:
                    record.adicion_number = len(registros) + 1
            else:
                record.adicion_number = 0

    @api.depends('contratacion_fuv_en_id', 'otro_si', 'adicion', 'prorroga', 'adicion_prorroga', 'otro_si_modificatorio')
    def _compute_otrosi_number(self):
        for record in self:
            if record.contratacion_fuv_en_id and (
                record.otro_si
                or record.adicion
                or record.prorroga
                or record.adicion_prorroga
                or record.otro_si_modificatorio
            ):
                record.otrosi_number = record._get_numero_otrosi_global_para_registro(record)
            else:
                record.otrosi_number = 0

    @api.depends('contratacion_fuv_en_id', 'reinicio')
    def _compute_reinicio_number(self):
        for record in self:
            if record.reinicio and record.contratacion_fuv_en_id:
                reinicios = self.search([
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('reinicio', '=', True)
                ], order='id asc')
                if record._origin.id in reinicios.ids:
                    record.reinicio_number = list(reinicios.ids).index(record._origin.id) + 1
                else:
                    record.reinicio_number = len(reinicios) + 1
            else:
                record.reinicio_number = 0

    @api.depends('contratacion_fuv_en_id', 'suspension', 'acta_de_prorroga_de_la_suspension')
    def _compute_suspension_number(self):
        for record in self:
            if record.contratacion_fuv_en_id and (record.suspension or record.acta_de_prorroga_de_la_suspension):
                suspensiones = self.search([
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('suspension', '=', True)
                ], order='id asc')
                if record.suspension and record._origin.id in suspensiones.ids:
                    record.suspension_number = list(suspensiones.ids).index(record._origin.id) + 1
                elif record.suspension:
                    record.suspension_number = len(suspensiones) + 1
                else:
                    current_id = record._origin.id or record.id
                    suspension_base = suspensiones.filtered(lambda item: not current_id or item.id < current_id)[-1:] or suspensiones[-1:]
                    record.suspension_number = (
                        list(suspensiones.ids).index(suspension_base.id) + 1
                        if suspension_base
                        else 0
                    )
            else:
                record.suspension_number = 0

    def action_recompute_otrosi_numbers(self):
        """Recomputa los números de todos los OtroSi. Ejecutar como acción de servidor tras actualizar el módulo."""
        all_records = self.search([])
        all_records._compute_prorroga_number()
        all_records._compute_adicion_number()
        all_records._compute_otrosi_number()
        all_records._compute_reinicio_number()
        all_records._compute_suspension_number()
        all_records._compute_adicion_prorroga_number()
        all_records._compute_otro_si_modificatorio_number()
        all_records._compute_acta_de_prorroga_de_la_suspension_number()

    # Campos calculados para el texto dinámico con contador
    identificador_tipo = fields.Char(string='Tipo de Documento', compute='_compute_identificador_tipo', store=False)
    identificador_tipo2 = fields.Char(string='Tipo de Documento', compute='_compute_identificador_tipo2', store=False)
    identificador_tipo3 = fields.Char(string='Tipo de Documento', compute='_compute_identificador_tipo3')
    identificador_tipo4 = fields.Char(string='Tipo de Documento', compute='_compute_identificador_tipo4')
    indentificador_tipo5 = fields.Char(string='Tipo de Documento', compute='_compute_identificador_tipo5')
    indentificador_tipo6 = fields.Char(string='Tipo de Documento', compute='_compute_identificador_tipo6')

    @api.depends('acta_de_prorroga_de_la_suspension', 'contratacion_fuv_en_id')
    def _compute_identificador_tipo6(self):
        for record in self:
            if record.acta_de_prorroga_de_la_suspension and record.contratacion_fuv_en_id:
                domain = [
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('acta_de_prorroga_de_la_suspension', '=', True)
                ]
                acta_de_prorroga_de_la_suspension = self.search(domain, order='id asc')
                
                if record._origin.id in acta_de_prorroga_de_la_suspension.ids:
                    numero = list(acta_de_prorroga_de_la_suspension.ids).index(record._origin.id) + 1
                else:
                    numero = len(acta_de_prorroga_de_la_suspension) + 1
                
                # CORRECCIÓN: Le quitamos el _6 para que coincida con _compute_name
                record.acta_de_prorroga_de_la_suspension_number = numero
                record.indentificador_tipo6 = f"PRÓRROGA No. {numero}" 
            else:
                record.acta_de_prorroga_de_la_suspension_number = 0
                record.indentificador_tipo6 = ""


    @api.depends('otro_si_modificatorio', 'contratacion_fuv_en_id', 'otro_si_modificatorio_number')
    def _compute_identificador_tipo5(self):
        for record in self:
            if record.otro_si_modificatorio and record.contratacion_fuv_en_id:
                record.indentificador_tipo5 = f"OTROSÍ No. {record._get_numero_otrosi_global_para_registro(record)}"
            else:
                record.indentificador_tipo5 = ""

    @api.depends('adicion', 'contratacion_fuv_en_id')
    def _compute_identificador_tipo4(self):
        for record in self:
            if record.adicion and record.contratacion_fuv_en_id:
                # 1. Filtramos para traer SOLO los de ESTE contrato
                domain = [
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('adicion', '=', True)
                ]
                
                # 2. Buscamos y ordenamos del más antiguo al más nuevo
                adicion = self.search(domain, order='id asc')
                
                # 3. Determinamos la posición exacta
                if record._origin.id in adicion.ids:
                    numero = list(adicion.ids).index(record._origin.id) + 1
                else:
                    numero = len(adicion) + 1
                
                # 4. Asignamos los valores
                record.adicion_number_4 = numero
                
                # ¡AQUÍ ESTABA EL ERROR! Cambiamos tipo2 por tipo4
                record.identificador_tipo4 = f"ADICIÓN No. {numero}" 
            else:
                record.adicion_number_4 = 0
                record.identificador_tipo4 = ""



    # Contadores para cada tipo de Otrosí, Reinicio y Suspensión
    otrosi_number_1 = fields.Integer(string='Número de Otrosí', store=True)
    reinicio_number_2 = fields.Integer(string='Número de Reinicio', store=True)
    suspension_number_3 = fields.Integer(string='Número de Suspensión', store=True)
    adicion_number_4 = fields.Integer(string='Número de Adición', store=True)
    otro_si_modificatorio_number_5 = fields.Integer(string='Número de Otrosí modificatorio', store=True)
    acta_de_prorroga_de_la_suspension_number_6 = fields.Integer(string='Número de Otrosí modificatorio', store=True)
    


    @api.depends('otro_si', 'contratacion_fuv_en_id')
    def _compute_identificador_tipo(self):
        for record in self:
            if record.otro_si and record.contratacion_fuv_en_id:
                # 1. Filtramos para traer SOLO los de ESTE contrato
                domain = [
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('otro_si', '=', True)
                ]
                
                # 2. Buscamos y ordenamos del más antiguo al más nuevo
                otros_sis = self.search(domain, order='id asc')
                
                # 3. Determinamos la posición exacta
                if record._origin.id in otros_sis.ids:
                    numero = list(otros_sis.ids).index(record._origin.id) + 1
                else:
                    numero = len(otros_sis) + 1
                
                # 4. Asignamos los valores
                record.otrosi_number_1 = numero
                record.identificador_tipo = f"OTROSÍ No. {numero}"
            else:
                record.otrosi_number_1 = 0
                record.identificador_tipo = ""

    @api.depends('reinicio', 'contratacion_fuv_en_id')
    def _compute_identificador_tipo2(self):
        for record in self:
            if record.reinicio and record.contratacion_fuv_en_id:
                # 1. Filtramos para traer SOLO los de ESTE contrato
                domain = [
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('reinicio', '=', True)
                ]
                
                # 2. Buscamos y ordenamos del más antiguo al más nuevo
                reinicios = self.search(domain, order='id asc')
                
                # 3. Determinamos la posición exacta
                if record._origin.id in reinicios.ids:
                    numero = list(reinicios.ids).index(record._origin.id) + 1
                else:
                    numero = len(reinicios) + 1
                
                # 4. Asignamos los valores
                record.reinicio_number_2 = numero
                record.identificador_tipo2 = f"REINICIO No. {numero}"
            else:
                record.reinicio_number_2 = 0
                record.identificador_tipo2 = ""

    @api.depends('suspension', 'contratacion_fuv_en_id')
    def _compute_identificador_tipo3(self):
        for record in self:
            if record.suspension and record.contratacion_fuv_en_id:
                # 1. Buscamos TODAS las suspensiones de este contrato, ordenadas por ID (de la más vieja a la más nueva)
                domain = [
                    ('contratacion_fuv_en_id', '=', record.contratacion_fuv_en_id.id),
                    ('suspension', '=', True)
                ]
                
                # En lugar de search_count, usamos search para traer los registros reales y ordenados
                suspensiones = self.search(domain, order='id asc')
                
                # 2. Determinamos la posición exacta de ESTE registro
                # record._origin.id asegura que busquemos el ID real, incluso si el registro se está editando
                if record._origin.id in suspensiones.ids:
                    # Si el registro ya está en la base de datos, buscamos su posición (index empieza en 0, le sumamos 1)
                    numero = list(suspensiones.ids).index(record._origin.id) + 1
                else:
                    # Si es un registro NUEVO que apenas estás creando y aún no has guardado,
                    # le toca el número total de los que ya existen + 1
                    numero = len(suspensiones) + 1
                
                # 3. Asignamos el número consecutivo correcto
                record.suspension_number_3 = numero
                record.identificador_tipo3 = f"SUSPENSIÓN No. {numero}"
            else:
                record.suspension_number_3 = 0
                record.identificador_tipo3 = ""



    # CORRECCIÓN: Se agregaron los 4 campos nuevos al @api.depends
    @api.depends(
        'reinicio', 'suspension', 'prorroga', 'adicion', 'new_name_value', 'adicion_prorroga',
        'adicion_number', 'prorroga_number', 'reinicio_number', 'suspension_number', 'adicion_prorroga_number',
        'otro_si_modificatorio', 'otro_si_modificatorio_number',
        'acta_de_prorroga_de_la_suspension', 'acta_de_prorroga_de_la_suspension_number'
    )
    def _compute_name(self):
        for record in self:
            #_logger.info("=== INICIO _compute_name ===")
            #_logger.info(f"Registro ID: {record.id} | Origin ID: {record._origin.id}")
            # (Tus otros loggers...)

            # 1. Condición combinada: Adición Y Prórroga
            if record.adicion_prorroga:
                record.name = f"{record.new_name_value} - Adición y Prórroga {record.adicion_prorroga_number}"
                #_logger.info(f"-> ENTRO EN: Adición Y Prórroga. Valor asignado: {record.name}")

            # 2. Condiciones individuales
            elif record.adicion:
                record.name = f"{record.new_name_value} - Adición {record.adicion_number}"
                #_logger.info(f"-> ENTRO EN: Adición. Valor asignado: {record.name}")

            elif record.reinicio:
                record.name = f"{record.new_name_value} - Reinicio {record.reinicio_number}"
                #_logger.info(f"-> ENTRO EN: Reinicio. Valor asignado: {record.name}")

            elif record.suspension:
                record.name = f"{record.new_name_value} - Suspensión {record.suspension_number}"
                #_logger.info(f"-> ENTRO EN: Suspensión. Valor asignado: {record.name}")

            elif record.prorroga:
                record.name = f"{record.new_name_value} - Prórroga {record.prorroga_number}"
                #_logger.info(f"-> ENTRO EN: Prórroga. Valor asignado: {record.name}")

            # AHORA ESTOS DOS SÍ VAN A FUNCIONAR PERFECTO
            elif record.otro_si_modificatorio:
                record.name = f"{record.new_name_value} - Otrosí modificatorio {record.otro_si_modificatorio_number}"
                #_logger.info(f"-> ENTRO EN: Otrosí modificatorio. Valor asignado: {record.name}")

            elif record.acta_de_prorroga_de_la_suspension:
                record.name = f"{record.new_name_value} - Acta de prorroga de la suspensión {record.acta_de_prorroga_de_la_suspension_number}"
                #_logger.info(f"-> ENTRO EN: Acta de prorroga de la suspensión. Valor asignado: {record.name}")

            # 3. Valor por defecto
            else:
                record.name = record.new_name_value
                #_logger.info(f"-> ENTRO EN: Por defecto. Valor asignado: {record.name}")
            
            #_logger.info("=== FIN _compute_name ===\n")

    def _get_nombre_archivo_otrosi_docx(self):
        """Devuelve un nombre de descarga estable, conservando tildes y ñ."""
        self.ensure_one()
        base_name = str(self.name or self.new_name or 'Informe_Contrataciones_FUV')
        clean_name = re.sub(r'[\\/:*?"<>|#%&{}$!`@+=]', ' ', base_name)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip(' ._-')
        return clean_name or 'Informe_Contrataciones_FUV'

    cedula_representante_legal = fields.Char(string="Cédula del representante legal", tracking=True,
                                             related='contratacion_fuv_en_id.cedula_representante_legal')
    r_legal = fields.Char(string="Representante legal", tracking=True, related='contratacion_fuv_en_id.r_legal')
    id_del_contrato = fields.Char(string='ID del contrato', related='contratacion_fuv_en_id.id_del_contrato')
    direccion_contratista = fields.Char('Dirección', tracking=True,
                                        related='contratacion_fuv_en_id.direccion_contratista')
    correo = fields.Char('Correo electrónico', tracking=True, related='contratacion_fuv_en_id.correo')
    tipo_contrato = fields.Selection([
        ('orden_servicios', 'Orden de servicios / Orden de compra'),
        ('prestacion_servicios', 'Prestación de servicios - adquisición de bienes'),
        ('laboral', 'Laboral')],
        string='Tipo de contrato', tracking=True, related='contratacion_fuv_en_id.tipo_contrato')

    tipo_contrato_id = fields.Many2one(
        'tipo.contrato',
        string='Tipo de contrato',
        tracking=True,
        related='contratacion_fuv_en_id.tipo_contrato_id'
    )
    salario_basico = fields.Float('Salario básico (Contrato Laboral)', tracking=True)

    @api.onchange('valor_total_contrato')
    def _onchange_valor_total_contrato(self):
        if not self.contratacion_fuv_en_id:
            return
        # Sumar todos los demás otro_si (desde DB) excepto el actual (que aún no está guardado)
        origin_id = self._origin.id
        otros = sum(
            r.valor_total_contrato
            for r in self.contratacion_fuv_en_id.otro_si_ids
            if r.id != origin_id
        )
        self.contratacion_fuv_en_id.valor_del_otrosi = otros + (self.valor_total_contrato or 0.0)

    documento_identidad = fields.Char('Documento de identificación', tracking=True,
                                      related='contratacion_fuv_en_id.documento_identidad')
    nit = fields.Char(string="Nit", tracking=True, related='contratacion_fuv_en_id.nit')
    fecha_ciudad_expedicion_contra = fields.Date(
        'Fecha de expedición del documento',
        help='Fecha en que se expidió el documento de identidad del contratista', tracking=True,
        related='contratacion_fuv_en_id.fecha_ciudad_expedicion_contra')

    ciudad_contratista = fields.Char('Ciudad de expedición', tracking=True,
                                     related='contratacion_fuv_en_id.ciudad_contratista')
    telefono_contratista = fields.Char('Teléfono/Celular', tracking=True,
                                       related='contratacion_fuv_en_id.telefono_contratista')
    expedicion_contra_N = fields.Date(
        'Fecha expedición cedula', tracking=True, store=True, related="contratacion_fuv_en_id.expedicion_contra_N")

    fecha_iniciacion = fields.Date('Fecha de iniciación', tracking=True)
    fecha_terminacion = fields.Date('Fecha de terminación', tracking=True)
    # Campos para almacenar las fechas formateadas
    fecha_iniciacion_letras2 = fields.Char(
        'Fecha de iniciación en letras',
        compute='_compute_fecha_iniciacion_letras',
        store=True
    )
    fecha_terminacion_letras2 = fields.Char(
        'Fecha de terminación en letras',
        compute='_compute_fecha_terminacion_letras',
        store=True
    )

    def _get_mes_espanol(self, mes_numero):
        """Convierte el número de mes a nombre en español."""
        meses = {
            1: 'enero', 2: 'febrero', 3: 'marzo', 4: 'abril',
            5: 'mayo', 6: 'junio', 7: 'julio', 8: 'agosto',
            9: 'septiembre', 10: 'octubre', 11: 'noviembre', 12: 'diciembre'
        }
        return meses.get(mes_numero, '')

    def _formatear_fecha_simple(self, fecha):
        """Formatea una fecha al formato 'DD de mes del YYYY'."""
        if not fecha:
            return ''

        dia = str(fecha.day).zfill(2)  # Agrega un cero al inicio si es necesario
        mes = self._get_mes_espanol(fecha.month)
        anio = fecha.year

        return f"{dia} de {mes} del {anio}"

    @api.depends('fecha_iniciacion')
    def _compute_fecha_iniciacion_letras(self):
        for record in self:
            record.fecha_iniciacion_letras2 = self._formatear_fecha_simple(record.fecha_iniciacion)
            print(f"📅 Fecha inicio en letras: {record.fecha_iniciacion} -> {record.fecha_iniciacion_letras2}")

    @api.depends('fecha_terminacion')
    def _compute_fecha_terminacion_letras(self):
        for record in self:
            record.fecha_terminacion_letras2 = self._formatear_fecha_simple(record.fecha_terminacion)
            print(f"📅 Fecha terminación en letras: {record.fecha_terminacion} -> {record.fecha_terminacion_letras2}")

    duracion = fields.Char('Duración', compute='_compute_duracion', store=True, tracking=True)

    @api.depends('fecha_iniciacion', 'fecha_terminacion')
    def _compute_duracion(self):
        for record in self:
            if record.fecha_iniciacion and record.fecha_terminacion:
                fecha_iniciacion = record.fecha_iniciacion
                fecha_terminacion_inclusiva = record.fecha_terminacion + timedelta(days=1)
                dias_totales = (fecha_terminacion_inclusiva - fecha_iniciacion).days
                if 0 < dias_totales <= 30:
                    record.duracion = f"{dias_totales} días"
                    continue
                diferencia = relativedelta(fecha_terminacion_inclusiva, fecha_iniciacion)

                años = diferencia.years
                meses = diferencia.months
                dias = diferencia.days

                duracion_str = ''

                if años > 0:
                    duracion_str += f"{años} años "

                if meses > 0:
                    duracion_str += f"{meses} meses "

                if dias > 0:
                    duracion_str += f"{dias} días"

                record.duracion = duracion_str.strip()

            else:
                record.duracion = ''

    valor_en_letras = fields.Text('Valor en letras', compute='_compute_valor_en_letras', tracking=True)

    @api.depends('valor_total_contrato')
    def _compute_valor_en_letras(self):
        for record in self:
            valor_en_letras = num2words(record.valor_total_contrato, lang='es')
            record.valor_en_letras = valor_en_letras.upper()

    # Debajo de cuadro información des contrato
    forma_pago = fields.Text('Forma de pago', tracking=True)
    actividades_especificas = fields.Text('Actividades específicas descritas en el FC', tracking=True, related="contratacion_fuv_en_id.actividades_especificas")
    actividades_especificas_natu = fields.Text('Actividades específicas descritas en el FC', tracking=True, related="contratacion_fuv_en_id.actividades_especificas_natu")
    #Nuevas para otrosí
    actividades_especificas_otrosi = fields.Text('Actividades específicas otrosí', tracking=True)
    actividades_especificas_otrosi_natu = fields.Text('Actividades específicas otrosí', tracking=True)
    # 4 campos nuevos
    codigo_ciiu = fields.Char('CIIU', tracking=True, related='contratacion_fuv_en_id.codigo_ciiu')

    @api.depends(
        'compromiso_presupuestal_adicion',
        'contratacion_fuv_en_id.compromiso_presupuestal',
    )
    def _compute_compromiso_presupuestal(self):
        for record in self:
            rpc_otrosi = record.compromiso_presupuestal_adicion or False
            rpc_contrato_inicial = record.contratacion_fuv_en_id.compromiso_presupuestal or False
            record.compromiso_presupuestal = rpc_otrosi or rpc_contrato_inicial

    compromiso_presupuestal = fields.Char(
        'RPC',
        compute='_compute_compromiso_presupuestal',
        tracking=True,
    )
    compromiso_presupuestal_contrato_inicial = fields.Char(
        'RPC contrato inicial',
        tracking=True,
        related='contratacion_fuv_en_id.compromiso_presupuestal',
    )
    compromiso_presupuestal_adicion = fields.Char('RPC otrosí adición', tracking=True)
    
    f_electronica = fields.Boolean('Facturación electronica', tracking=True, related='contratacion_fuv_en_id.f_electronica')
    r_iva = fields.Boolean('Responsable de IVA', tracking=True, related='contratacion_fuv_en_id.r_iva')

    no_r_iva = fields.Boolean('No rsponsable de IVA', tracking=True, related='contratacion_fuv_en_id.no_r_iva')
    no_disponibilidad_presupuestal = fields.Char('N° Disponibilidad presupuestal', tracking=True,
                                                 related='contratacion_fuv_en_id.no_disponibilidad_presupuestal')
    fecha_cdp_d = fields.Date('Fecha disponibilidad presupuestal', tracking=True)
    proyecto_convenio = fields.Text('Proyecto o convenio', tracking=True,
                                    related='contratacion_fuv_en_id.proyecto_convenio')

    observaciones = fields.Html('Observaciones', tracking=True)
    observaciones_finacie = fields.Html('Observaciones', tracking=True)
    observaciones_juri = fields.Html('Observaciones', tracking=True)
    observaciones_recursos = fields.Html('Observaciones', tracking=True)

    def action_set_contratado(self):
        """ Cambia el estado de contratación a 'contratado' """
        self.ensure_one()  # Asegura que solo hay un registro seleccionado
        self.estado_contratacion = 'contratado'
        _logger.info(f"El estado de contratación se ha cambiado a 'contratado' para el registro ID {self.id}.")

    def _log_diagnostico_adicion_prorroga_viejo(self, origen, vals=None):
        """Log temporal para diagnosticar visibilidad del botón inicial en Adición + Prórroga."""
        vals = vals or {}
        for record in self:
            try:
                es_adicion_prorroga = bool(
                    record.adicion_prorroga or (record.adicion and record.prorroga)
                )
                if not es_adicion_prorroga or record.usa_flujo_otrosi_nuevo:
                    continue

                _logger.warning(
                    "[DIAGNOSTICO-OTROSI-ADICION-PRORROGA] origen=%s "
                    "id=%s name=%s persona=%s estado=%s nun=%s nun_juridica=%s "
                    "adicion=%s prorroga=%s adicion_prorroga=%s flujo_nuevo=%s "
                    "contrato_firmado_natural_visible=%s contrato_otrosi_visible=%s "
                    "contrato_firmado_juridico_visible=%s contrato_otrosi_juridico_visible=%s "
                    "show_coord=%s show_coord_j=%s aprob_coord=%s aprob_fin=%s aprob_juri=%s aprob_rrhh=%s "
                    "vals_keys=%s context=%s",
                    origen,
                    record.id,
                    record.name,
                    record.select_persona,
                    record.estado_contratacion,
                    record.estado_contratacion_nun,
                    record.estado_contratacion_nun_juridica,
                    record.adicion,
                    record.prorroga,
                    record.adicion_prorroga,
                    record.usa_flujo_otrosi_nuevo,
                    record.contratos_firmado_natu_doc_visible,
                    record.contratos_otro_doc_visible,
                    record.contratos_firmado_juridico_doc_visible,
                    record.contratos_otro_juridico_doc_visible,
                    record.show_aprueba_coordinador,
                    record.show_aprueba_coordinador_j,
                    record.aprobacion_coordinador_realizada,
                    record.aprobacion_financiera_realizada,
                    record.aprobacion_juridico_realizada,
                    record.aprobacion_recursos_humanos_realizada,
                    sorted(vals.keys()),
                    dict(self.env.context),
                )
            except Exception:
                _logger.exception(
                    "[DIAGNOSTICO-OTROSI-ADICION-PRORROGA] No se pudo generar el log de diagnóstico."
                )

    @api.model
    def create(self, vals):
        # 0. PERMISO DE CREACIÓN
        user = self.env.user
        puede_crear = (
            user.has_group('Matriz.group_contratacion_coordinador') or
            user.has_group('Matriz.group_contratacion_asis_coordinador') or
            self._fn_puede_crear_excepcion(vals)  # Solo Soporte con autorización persistida de Dirección FUV.
        )
        if not puede_crear:
            raise UserError(
                "Solo el Coordinador y el Asistente de Coordinación pueden crear registros de Otrosí."
            )

        # 1. VALIDACIÓN PREVIA
        contratacion_fuv_id = vals.get('contratacion_fuv_en_id')
        if contratacion_fuv_id:
            if vals.get('suspension'):
                contratacion = self.env['contrataciones_fuv'].browse(
                    contratacion_fuv_id
                ).exists()
                if not contratacion or contratacion.estado_contratacion != 'contratado':
                    raise ValidationError(
                        _("Solo se puede crear una suspensión cuando la contratación FUV está en estado Contratado.")
                    )

            # Movimientos que continúan una suspensión: el último Otrosí debe ser
            # una Suspensión o un Acta de prórroga de la suspensión.
            if vals.get('acta_de_prorroga_de_la_suspension') or vals.get('reinicio'):
                last_record = self.search(
                    [('contratacion_fuv_en_id', '=', contratacion_fuv_id)],
                    order='id desc', limit=1
                )
                if not last_record or not (
                    last_record.suspension or last_record.acta_de_prorroga_de_la_suspension
                ):
                    raise ValidationError(
                        _("La solicitud solo se puede crear cuando el último movimiento "
                          "del contrato es una Suspensión o un Acta de prórroga de la suspensión.")
                    )

            # VALIDACIÓN IMPORTANTE:
            # Se revisa siempre el último Otrosí antes de crear uno nuevo.
            # Solo bloquean los anteriores de tipo Adición, Prórroga o
            # Adición + Prórroga que todavía no estén formalizados.
            self._check_previous_record(contratacion_fuv_id)

        # 2. CREAR EL REGISTRO
        record = super(OtroSi, self).create(vals)

        # Asegura que el bloque embebido de Otrosí sea visible en el FC padre
        # incluso cuando el registro hijo se crea desde un flujo o enlace directo.
        if record.contratacion_fuv_en_id and not record.contratacion_fuv_en_id.otro_si:
            record.contratacion_fuv_en_id.write({'otro_si': True})

        # 3. BUSCAR/CREAR CARPETA
        record.invalidate_recordset()
        record.buscar_carpeta()

        # 4. El flujo anterior conserva el envío automático a Jurídico.
        # En el flujo nuevo todos los tipos comienzan en Coordinador.
        if vals.get('reinicio') or vals.get('acta_de_prorroga_de_la_suspension'):
            if record.usa_flujo_otrosi_nuevo:
                record.write({'estado_contratacion': 'coordinador'})
            else:
                record.aprueba_coordinador_juridico()

        # 5. INTEGRACIÓN BDD
        self.env['otrosi_bdd'].action_cargar_individual_otro_si(record.id)

        #record._log_diagnostico_adicion_prorroga_viejo('create', vals)

        _logger.info(f"Registro creado con éxito: ID {record.id} Nombre: {record.name}")
        return record

    def _check_previous_record(self, contratacion_fuv_id):
        """
        Impide crear un nuevo Otrosí cuando el anterior es una Adición,
        Prórroga o Adición + Prórroga que aún no llegó a su estado final.
        """
        last_record = self.search(
            [('contratacion_fuv_en_id', '=', contratacion_fuv_id)],
            order='id desc',
            limit=1,
        )
        if not last_record:
            return

        # La combinación puede estar indicada por el campo calculado
        # adicion_prorroga o porque ambos indicadores estén activos.
        es_adicion_prorroga = (
            last_record.adicion_prorroga
            or (last_record.adicion and last_record.prorroga)
        )

        # Cada tipo debe alcanzar específicamente su estado final.
        if es_adicion_prorroga:
            estado_final = 'adicion_prorroga_formalizada'
            tipo_anterior = _('Adición + Prórroga')
        elif last_record.adicion:
            estado_final = 'adicion_formalizada'
            tipo_anterior = _('Adición')
        elif last_record.prorroga:
            estado_final = 'prorroga_formalizada'
            tipo_anterior = _('Prórroga')
        else:
            # Suspensión, reinicio, modificatorio y los demás tipos no
            # bloquean la creación mediante esta validación.
            return

        estado_anterior_valido = (
            # Compatibilidad con el flujo anterior, cuyo estado final
            # para estos tipos era 'contratado'.
            last_record.estado_contratacion == 'contratado'
            or (
                # En el flujo nuevo se exige el estado final específico
                # asignado arriba según el tipo del Otrosí anterior.
                last_record.usa_flujo_otrosi_nuevo
                and last_record.estado_contratacion == estado_final
            )
        )
        if not estado_anterior_valido:
            raise ValidationError(
                _(
                    "No se puede crear un nuevo registro hasta que el otrosí "
                    "anterior de tipo %s esté formalizado."
                ) % tipo_anterior
            )



    polizas_bo = fields.Boolean(default=True)
    polizas = fields.Binary(string='Pólizas', tracking=True)
    polizas_fname = fields.Char(string='Nombre del archivo', tracking=True)
    poliza_id = fields.Many2one('drive', string='Pólizas', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    polizas_visible = fields.Boolean(default=True)

    def action_upload_polizas_otrosi_doc(self):
        """
        Abre la ventana emergente para subir el documento Pólizas (Otrosí).
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir pólizas', # Nombre de la ventana
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculamos al registro de OTRO SI
                'default_otrosi_documentos_id': self.id,
                
                # ID de carpeta Odoo
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de carpeta Google Drive
                'default_folder_id': google_folder_id, 
                
                'default_otrosi_bolean': True,
                
                # CLAVE ÚNICA para este documento
                'tipo_documento_upload': 'polizas_otrosi' 
                
            },
        }

    

    def open_drive_filtered(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otras pólizas',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('otrosi_id', '=', self.id)],  # Solo archivos de este contrato
            'context': {
                'default_stage5_id': self.drive_file_id.id,  # Auto-llenar en nuevos archivos
                'default_otrosi_id': self.id,  # Relacionar con el contrato actual
            },
            'target': 'current',
        }

    def subir_polizas_a_drive(self):
        """Sube el documento 'Pólizas' a Google Drive y lo registra en el modelo drive."""
        if self.polizas and self.polizas_fname:
            _logger.info(f"Subiendo documento '{self.polizas_fname}' a Google Drive...")

            result = self._upload_polizas_to_drive_pn()

            if not result:
                raise UserError("❌ Error al subir la Pólizas a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ Pólizas subida correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'Pólizas' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_polizas_to_drive_pn(self):
        """Sube el archivo binario de póliza a Google Drive y lo registra en el modelo 'drive'."""
        self.ensure_one()

        if not self.polizas or not self.polizas_fname:
            _logger.error("No hay archivo de póliza para subir a Google Drive.")
            raise UserError("Debe adjuntar el archivo de póliza antes de subir.")

        if isinstance(self.drive_file_id, str):
            _logger.error(f"El campo drive_file_id tiene un valor inválido (tipo string): {self.drive_file_id}")
            raise UserError("Referencia a archivo Drive inválida. Verifique la configuración.")

        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("Archivo de Drive no encontrado. No se puede continuar con la subida.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró carpeta Drive para el archivo ID {self.drive_file_id.id}.")
            raise UserError("No hay carpeta asociada en Drive. Verifique el archivo de Drive.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        temp_file_path = None  # Para limpieza en finally

        try:
            file_content = base64.b64decode(self.polizas)
            original_extension = os.path.splitext(self.polizas_fname)[1] or ".pdf"
            fixed_file_name = f"Póliza_{uuid.uuid4()}{original_extension}"

            # Validación de duplicados en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido con éxito. ID: {file_id}, URL: {file_link}")

            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.polizas,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(f"Registro creado en 'drive' con ID {drive_record.id}")

            if not self.poliza_id:
                self.poliza_id = drive_record.id
                _logger.info(f"Campo poliza_id actualizado con ID {drive_record.id}")

            self.polizas_visible = False

            _logger.info("Archivo de póliza eliminado del formulario tras la subida.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo de póliza a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal de póliza eliminado.")



    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_poliza = {}

    def abrir_documento_poliza_pdf(self):
        self.polizas = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_poliza_en_drive()

        # primero, verificar si 'cdp_juridico_doc_id' está presente
        if self.poliza_id:
            # si esta relcionado, abrir el registro en ek modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Póliza',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.poliza_id.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }

        # si 'poliza_id' no está presente, buscar el documento en el modelo 'drive'
        if self.polizas_fname:
            file_name_to_search = self.polizas_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el caché
            if file_name_to_search in self._cache_documentos_poliza:
                drive_document = self._cache_documentos_poliza[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que contengan el nombre base
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el caché
                    self._cache_documentos_poliza[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'poliza_id' con el ID del documento encontrado
                self.write({'poliza_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Póliza',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(
                    f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún RCP relacionado con este registro.')

    poliza_actualizar_visible = fields.Boolean('Actualizar visible', default=False, related='poliza_id.actualizar_visible', store=True, readonly=False)
    file_data_visible = fields.Boolean(default=False)

    def actualizar_correo_poliza_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.poliza_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.poliza_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.poliza_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False


    #Relatados para contrato firmado persona natural
    contratos_firmado = fields.Boolean('Contrato', tracking=True, related="contratacion_fuv_en_id.contratos_firmado")
    contratos_firmado_doc = fields.Binary(tracking=True, related="contratacion_fuv_en_id.contratos_firmado_doc")
    contratos_firmado_doc_visible = fields.Boolean(related="contratacion_fuv_en_id.contratos_firmado_doc_visible")
    contratos_firmado_doc_fname = fields.Char(tracking=True, related="contratacion_fuv_en_id.contratos_firmado_doc_fname")
    contratos_firmado_doc_id = fields.Many2one('drive', string='Contrato Firmado', tracking=True, related="contratacion_fuv_en_id.contratos_firmado_id")

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_contratos_firmado = {}
    
    def abrir_documento_contratos_firmado_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()


        # Primero, verificamos si 'contratos_firmado_doc_id' está configurado
        if self.contratos_firmado_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Contrato inicial firmado',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.contratos_firmado_doc_id.id,
                'target': 'new',
            }

        # Si 'contratos_firmado_doc_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.contratos_firmado_doc_fname:
            file_name_to_search = self.contratos_firmado_doc_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_contratos_firmado:
                drive_document = self._cache_documentos_contratos_firmado[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_contratos_firmado[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'contratos_firmado_doc_id' con el ID del documento encontrado
                self.write({'contratos_firmado_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Contrato inicial firmado',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')



    #Relatados para contrato firmado persona juridica
    contrato_firmado_j = fields.Boolean('Contrato', tracking=True, related="contratacion_fuv_en_id.contrato_firmado_j")
    aprueba_j_h_contrato_firmado_doc_visible = fields.Boolean(default=True, related="contratacion_fuv_en_id.aprueba_j_h_contrato_firmado_doc_visible")
    aprueba_j_h_contrato_firmado_doc = fields.Binary(tracking=True, related="contratacion_fuv_en_id.aprueba_j_h_contrato_firmado_doc")
    aprueba_j_h_contrato_firmado_doc_fname = fields.Char(tracking=True, related="contratacion_fuv_en_id.aprueba_j_h_contrato_firmado_doc_fname")
    aprueba_j_h_contrato_firmado_doc_id = fields.Many2one('drive', string='Contrato Firmado', tracking=True, related="contratacion_fuv_en_id.aprueba_j_h_contrato_firmado_doc_id")
    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_contrato_firmado_j = {}

    def abrir_documento_contrato_firmado_j_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'aprueba_j_h_contrato_firmado_doc_id' está configurado
        if self.aprueba_j_h_contrato_firmado_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Contrato firmado',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.aprueba_j_h_contrato_firmado_doc_id.id,
                'target': 'new',
            }

        # Si 'aprueba_j_h_contrato_firmado_doc_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.aprueba_j_h_contrato_firmado_doc_fname:
            file_name_to_search = self.aprueba_j_h_contrato_firmado_doc_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_contrato_firmado_j:
                drive_document = self._cache_documentos_contrato_firmado_j[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_contrato_firmado_j[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'aprueba_j_h_contrato_firmado_doc_id' con el ID del documento encontrado
                self.write({'aprueba_j_h_contrato_firmado_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Contrato firmado',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Contrato Firmado Jurídico relacionado con este registro.')

    ###################################################################################################
    contrato_juridico = fields.Boolean('Contrato', tracking=True)
    aprueba_f_contrato_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_contrato_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    contrato_doc_fname = fields.Char(tracking=True)
    contrato_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('contrato_juridico_doc')
    def _check_file_extension44(self):
        for record in self:
            if record.contrato_juridico_doc:
                filename = record.contrato_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('contrato_juridico_doc')
    def _onchange_contrato_juridico_doc(self):
        if self.contrato_juridico_doc:
            self.aprueba_j_h_contrato_j = True

    @api.onchange('contrato_juridico_doc')
    def _onchange_delete_contrato_juridico_doc(self):
        if not self.contrato_juridico_doc:
            self.aprueba_j_h_contrato_j = False

    @api.model
    def cargar_documento_61(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'contrato_juridico_doc': file_content,
            'contrato_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_contrato_juridico = {}

    def abrir_documento_contrato_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.contrato_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_contrato_juridico:
            drive_document = self._cache_documentos_contrato_juridico[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_contrato_juridico[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.contrato_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ###################################################################################################
    cdp_juridico = fields.Boolean('RPC', tracking=True)
    aprueba_f_cdp_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_cdp_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.onchange('cdp_juridico_doc')
    def _onchange_cdp_juridico_doc(self):
        if self.cdp_juridico_doc:
            self.aprueba_f_cdp_juridico = True

    @api.onchange('cdp_juridico_doc')
    def _onchange_delete_cdp_juridico_doc(self):
        if not self.cdp_juridico_doc:
            self.aprueba_f_cdp_juridico = False

    @api.model
    def cargar_documento_61(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cdp_juridico_doc': file_content,
            'cdp_doc_juridico_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cdp_juridico = {}

    def abrir_documento_cdp_juridico_pdf(self):
        self.cdp_juridico_doc = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_cdp_juridico_en_drive()

        # primero, verificar si 'cdp_juridico_doc_id' está presente
        if self.cdp_juridico_doc_id:
            # si esta relcionado, abrir el registro en ek modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'RCP',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.cdp_juridico_doc_id.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }

        # si 'cdp_juridico_doc_id' no está presente, buscar el documento en el modelo 'drive'
        if self.cdp_doc_juridico_fname:
            file_name_to_search = self.cdp_doc_juridico_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el caché
            if file_name_to_search in self._cache_documentos_cdp_juridico:
                drive_document = self._cache_documentos_cdp_juridico[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que contengan el nombre base
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el caché
                    self._cache_documentos_cdp_juridico[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'cdp_juridico_doc_id' con el ID del documento encontrado
                self.write({'cdp_juridico_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'RCP',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(
                    f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún RCP relacionado con este registro.')

    cdp_juridico_actualizar_visible = fields.Boolean('Actualizar visible', default=False, related='cdp_juridico_doc_id.actualizar_visible')

    def actualizar_correo_cdp_juridico_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.cdp_juridico_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.cdp_juridico_doc_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.cdp_juridico_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False



    camara_de_comercio_juridico = fields.Boolean('Cámara de comercio', tracking=True)
    aprueba_f_camara_de_comercio_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_camara_de_comercio_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    camara_de_comercio_doc_fname = fields.Char(tracking=True)
    camara_de_comercio_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('camara_de_comercio_juridico_doc')
    def _check_file_extension56(self):
        for record in self:
            if record.camara_de_comercio_juridico_doc:
                filename = record.camara_de_comercio_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('camara_de_comercio_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.camara_de_comercio_juridico_doc:
            self.camara_de_comercio_juridico = True

    @api.onchange('camara_de_comercio_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.camara_de_comercio_juridico_doc:
            self.camara_de_comercio_juridico = False

    @api.model
    def cargar_documento_70(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'camara_de_comercio_juridico_doc': file_content,
            'camara_de_comercio_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_camara_de_comercio_juridico = {}

    def abrir_documento_camara_de_comercio_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.camara_de_comercio_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_camara_de_comercio_juridico:
            drive_document = self._cache_documentos_camara_de_comercio_juridico[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_camara_de_comercio_juridico[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.camara_de_comercio_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    rut_juridico = fields.Boolean('RUT', tracking=True)
    aprueba_f_rut_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_rut_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    rut_doc_fname = fields.Char(tracking=True)
    rut_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('rut_juridico_doc')
    def _check_file_extension96(self):
        for record in self:
            if record.rut_juridico_doc:
                filename = record.rut_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('rut_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.rut_juridico_doc:
            self.rut_juridico = True

    @api.onchange('rut_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.rut_juridico_doc:
            self.rut_juridico = False

    @api.model
    def cargar_documento_71(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'rut_juridico_doc': file_content,
            'rut_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_rut_juridico = {}

    def abrir_documento_rut_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.rut_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_rut_juridico:
            drive_document = self._cache_documentos_rut_juridico[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_rut_juridico[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.rut_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    cert_contraloria_juridico = fields.Boolean('Cert. Contraloría', tracking=True)
    aprueba_f_cert_contraloria_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_cert_contraloria_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    cert_contraloria_doc_fname = fields.Char(tracking=True)
    cert_contraloria_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('cert_contraloria_juridico_doc')
    def _check_file_extension89(self):
        for record in self:
            if record.cert_contraloria_juridico_doc:
                filename = record.cert_contraloria_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('cert_contraloria_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.cert_contraloria_juridico_doc:
            self.cert_contraloria_juridico = True

    @api.onchange('cert_contraloria_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.cert_contraloria_juridico_doc:
            self.cert_contraloria_juridico = False

    @api.model
    def cargar_documento_67(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cert_contraloria_juridico_doc': file_content,
            'cert_contraloria_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_contraloria_juridico = {}

    def abrir_documento_cert_contraloria_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.cert_contraloria_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_cert_contraloria_juridico:
            drive_document = self._cache_documentos_cert_contraloria_juridico[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_cert_contraloria_juridico[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.cert_contraloria_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    cert_procuraduria_juridico = fields.Boolean('Cert. Procuraduría', tracking=True)
    aprueba_f_cert_procuraduria_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_cert_procuraduria_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    cert_procuraduria_doc_fname = fields.Char(tracking=True)
    cert_procuraduria_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('cert_procuraduria_juridico_doc')
    def _check_file_extension96(self):
        for record in self:
            if record.cert_procuraduria_juridico_doc:
                filename = record.cert_procuraduria_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('cert_procuraduria_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.cert_procuraduria_juridico_doc:
            self.cert_procuraduria_juridico = True

    @api.onchange('cert_procuraduria_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.cert_procuraduria_juridico_doc:
            self.cert_procuraduria_juridico = False

    @api.model
    def cargar_documento_69(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cert_procuraduria_juridico_doc': file_content,
            'cert_procuraduria_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_procuraduria_juridico = {}

    def abrir_documento_cert_procuraduria_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.cert_procuraduria_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_cert_procuraduria_juridico:
            drive_document = self._cache_documentos_cert_procuraduria_juridico[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_cert_procuraduria_juridico[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.cert_procuraduria_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    certificados_de_contraloria_rep_legal_juridico = fields.Boolean('Certificados de Contraloría Rep. Legal',
                                                                    tracking=True)
    aprueba_f_certificados_de_contraloria_rep_legal_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_certificados_de_contraloria_rep_legal_j = fields.Boolean('Aprueba - Aprueba área jurídica',
                                                                         tracking=True)

    certificados_de_contraloria_rep_legal_doc_fname = fields.Char(tracking=True)
    certificados_de_contraloria_rep_legal_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('certificados_de_contraloria_rep_legal_juridico_doc')
    def _check_file_extension26(self):
        for record in self:
            if record.certificados_de_contraloria_rep_legal_juridico_doc:
                filename = record.certificados_de_contraloria_rep_legal_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('certificados_de_contraloria_rep_legal_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.certificados_de_contraloria_rep_legal_juridico_doc:
            self.certificados_de_contraloria_rep_legal_juridico = True

    @api.onchange('certificados_de_contraloria_rep_legal_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.certificados_de_contraloria_rep_legal_juridico_doc:
            self.certificados_de_contraloria_rep_legal_juridico = False

    @api.model
    def cargar_documento_68(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificados_de_contraloria_rep_legal_juridico_doc': file_content,
            'certificados_de_contraloria_rep_legal_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificados_de_contraloria_rep_legal = {}

    def abrir_documento_certificados_de_contraloria_rep_legal_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.certificados_de_contraloria_rep_legal_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_certificados_de_contraloria_rep_legal:
            drive_document = self._cache_documentos_certificados_de_contraloria_rep_legal[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_certificados_de_contraloria_rep_legal[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.certificados_de_contraloria_rep_legal_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    certificados_de_Procuraduria_rep_legal_juridico = fields.Boolean('Certificados de Procuraduría Rep. Legal',
                                                                     tracking=True)
    aprueba_f_certificados_de_Procuraduria_rep_legal_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_certificados_de_Procuraduria_rep_legal_j = fields.Boolean('Aprueba - Aprueba área jurídica',
                                                                          tracking=True)

    certificados_de_Procuraduria_rep_legal_doc_fname = fields.Char(tracking=True)
    certificados_de_Procuraduria_rep_legal_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('certificados_de_Procuraduria_rep_legal_juridico_doc')
    def _check_file_extension16(self):
        for record in self:
            if record.certificados_de_Procuraduria_rep_legal_juridico_doc:
                filename = record.certificados_de_Procuraduria_rep_legal_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('certificados_de_Procuraduria_rep_legal_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.certificados_de_Procuraduria_rep_legal_juridico_doc:
            self.certificados_de_Procuraduria_rep_legal_juridico = True

    @api.onchange('certificados_de_Procuraduria_rep_legal_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.certificados_de_Procuraduria_rep_legal_juridico_doc:
            self.certificados_de_Procuraduria_rep_legal_juridico = False

    @api.model
    def cargar_documento_63(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificados_de_Procuraduria_rep_legal_juridico_doc': file_content,
            'certificados_de_Procuraduria_rep_legal_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificados_de_procuraduria_rep_legal = {}

    def abrir_documento_certificados_de_Procuraduria_rep_legal_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.certificados_de_Procuraduria_rep_legal_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_certificados_de_procuraduria_rep_legal:
            drive_document = self._cache_documentos_certificados_de_procuraduria_rep_legal[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_certificados_de_procuraduria_rep_legal[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.certificados_de_Procuraduria_rep_legal_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    certificados_de_policia_judicial_juridico = fields.Boolean(
        'Cert. de Policía Judicial, Medidas Cautelares Rep.Legal', tracking=True)
    aprueba_f_certificados_de_policia_judicial_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_certificados_de_policia_judicial_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    certificados_de_policia_judicial_doc_fname = fields.Char(tracking=True)
    certificados_de_policia_judicial_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('certificados_de_policia_judicial_juridico_doc')
    def _check_file_extension86(self):
        for record in self:
            if record.certificados_de_policia_judicial_juridico_doc:
                filename = record.certificados_de_policia_judicial_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('certificados_de_policia_judicial_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.certificados_de_policia_judicial_juridico_doc:
            self.certificados_de_policia_judicial_juridico = True

    @api.onchange('certificados_de_policia_judicial_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.certificados_de_policia_judicial_juridico_doc:
            self.certificados_de_policia_judicial_juridico = False

    @api.model
    def cargar_documento_65(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificados_de_policia_judicial_juridico_doc': file_content,
            'certificados_de_policia_judicial_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificados_de_policia_judicial = {}

    def abrir_documento_certificados_de_policia_judicial_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.certificados_de_policia_judicial_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_certificados_de_policia_judicial:
            drive_document = self._cache_documentos_certificados_de_policia_judicial[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_certificados_de_policia_judicial[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.certificados_de_policia_judicial_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    certificado_de_inhabilidades_rep_juridico = fields.Boolean('Certificado de Inhabilidades Rep. Legal', tracking=True)
    aprueba_f_certificado_de_inhabilidades_rep_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_certificado_de_inhabilidades_rep_j = fields.Boolean('Aprueba - Aprueba área jurídica',
                                                                    tracking=True)

    certificado_de_inhabilidades_rep_doc_fname = fields.Char(tracking=True)
    certificado_de_inhabilidades_rep_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('certificado_de_inhabilidades_rep_juridico_doc')
    def _check_file_extension86(self):
        for record in self:
            if record.certificado_de_inhabilidades_rep_juridico_doc:
                filename = record.certificado_de_inhabilidades_rep_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('certificado_de_inhabilidades_rep_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.certificado_de_inhabilidades_rep_juridico_doc:
            self.certificado_de_inhabilidades_rep_juridico = True

    @api.onchange('certificado_de_inhabilidades_rep_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.certificado_de_inhabilidades_rep_juridico_doc:
            self.certificado_de_inhabilidades_rep_juridico = False

    @api.model
    def cargar_documento_65(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificado_de_inhabilidades_rep_juridico_doc': file_content,
            'certificado_de_inhabilidades_rep_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_de_inhabilidades_rep = {}

    def abrir_documento_certificado_de_inhabilidades_rep_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.certificado_de_inhabilidades_rep_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_certificado_de_inhabilidades_rep:
            drive_document = self._cache_documentos_certificado_de_inhabilidades_rep[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_certificado_de_inhabilidades_rep[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.certificado_de_inhabilidades_rep_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    certificado_de_pago_parafiscales_juridico = fields.Boolean('Certificado de Pago Parafiscales', tracking=True)
    aprueba_f_certificado_de_pago_parafiscales_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_certificado_de_pago_parafiscales_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    certificado_de_pago_parafiscales_doc_fname = fields.Char(tracking=True)
    certificado_de_pago_parafiscales_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('certificado_de_pago_parafiscales_juridico_doc')
    def _check_file_extension86(self):
        for record in self:
            if record.certificado_de_pago_parafiscales_juridico_doc:
                filename = record.certificado_de_pago_parafiscales_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('certificado_de_pago_parafiscales_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.certificado_de_pago_parafiscales_juridico_doc:
            self.certificado_de_pago_parafiscales_juridico = True

    @api.onchange('certificado_de_pago_parafiscales_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.certificado_de_pago_parafiscales_juridico_doc:
            self.certificado_de_pago_parafiscales_juridico = False

    @api.model
    def cargar_documento_65(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificado_de_pago_parafiscales_juridico_doc': file_content,
            'certificado_de_pago_parafiscales_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_de_pago_parafiscales = {}

    def abrir_documento_certificado_de_pago_parafiscales_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.certificado_de_pago_parafiscales_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_certificado_de_pago_parafiscales:
            drive_document = self._cache_documentos_certificado_de_pago_parafiscales[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_certificado_de_pago_parafiscales[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.certificado_de_pago_parafiscales_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    planilla_de_seguridad_social_juridico = fields.Boolean('Planilla de Seguridad Social', tracking=True)
    aprueba_f_planilla_de_seguridad_social_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_planilla_de_seguridad_social_j = fields.Boolean('Aprueba - Aprueba área jurídica',
                                                                tracking=True)

    planilla_de_seguridad_social_doc_fname = fields.Char(tracking=True)
    planilla_de_seguridad_social_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('planilla_de_seguridad_social_juridico_doc')
    def _check_file_extension86(self):
        for record in self:
            if record.planilla_de_seguridad_social_juridico_doc:
                filename = record.planilla_de_seguridad_social_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('planilla_de_seguridad_social_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.planilla_de_seguridad_social_juridico_doc:
            self.planilla_de_seguridad_social_juridico = True

    @api.onchange('planilla_de_seguridad_social_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.planilla_de_seguridad_social_juridico_doc:
            self.planilla_de_seguridad_social_juridico = False

    @api.model
    def cargar_documento_65(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'planilla_de_seguridad_social_juridico_doc': file_content,
            'planilla_de_seguridad_social_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_planilla_seguridad_social = {}

    def abrir_documento_planilla_de_seguridad_social_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.planilla_de_seguridad_social_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_planilla_seguridad_social:
            drive_document = self._cache_documentos_planilla_seguridad_social[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_planilla_seguridad_social[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.planilla_de_seguridad_social_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    certif_de_junta_de_contadores_juridico = fields.Boolean('Certif. de Junta de Contadores', tracking=True)
    aprueba_f_certif_de_junta_de_contadores_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_certif_de_junta_de_contadores_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    certif_de_junta_de_contadores_doc_fname = fields.Char(tracking=True)
    certif_de_junta_de_contadores_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('certif_de_junta_de_contadores_juridico_doc')
    def _check_file_extension86(self):
        for record in self:
            if record.certif_de_junta_de_contadores_juridico_doc:
                filename = record.certif_de_junta_de_contadores_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('certif_de_junta_de_contadores_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.certif_de_junta_de_contadores_juridico_doc:
            self.certif_de_junta_de_contadores_juridico = True

    @api.onchange('certif_de_junta_de_contadores_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.certif_de_junta_de_contadores_juridico_doc:
            self.certif_de_junta_de_contadores_juridico = False

    @api.model
    def cargar_documento_65(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certif_de_junta_de_contadores_juridico_doc': file_content,
            'certif_de_junta_de_contadores_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_junta_contadores = {}

    def abrir_documento_certif_de_junta_de_contadores_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.certif_de_junta_de_contadores_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_junta_contadores:
            drive_document = self._cache_documentos_junta_contadores[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_junta_contadores[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.certif_de_junta_de_contadores_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    cedula_de_revisor_juridico = fields.Boolean('Cédula de Revisor Fiscal y/o Contador', tracking=True)
    aprueba_f_cedula_de_revisor_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_cedula_de_revisor_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    cedula_de_revisor_doc_fname = fields.Char(tracking=True)
    cedula_de_revisor_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('cedula_de_revisor_juridico_doc')
    def _check_file_extension86(self):
        for record in self:
            if record.cedula_de_revisor_juridico_doc:
                filename = record.cedula_de_revisor_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('cedula_de_revisor_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.cedula_de_revisor_juridico_doc:
            self.cedula_de_revisor_juridico = True

    @api.onchange('cedula_de_revisor_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.cedula_de_revisor_juridico_doc:
            self.cedula_de_revisor_juridico = False

    @api.model
    def cargar_documento_65(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cedula_de_revisor_juridico_doc': file_content,
            'cedula_de_revisor_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_revisor = {}

    def abrir_documento_cedula_de_revisor_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.cedula_de_revisor_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_revisor:
            drive_document = self._cache_documentos_revisor[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_revisor[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.cedula_de_revisor_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ##################################################################################################################################

    tarjeta_juridico = fields.Boolean('Tarjeta Prof. de Revisor Fiscal y/o Contador', tracking=True)
    aprueba_f_tarjeta_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_tarjeta_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    tarjeta_doc_fname = fields.Char(tracking=True)
    tarjeta_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('tarjeta_juridico_doc')
    def _check_file_extension86(self):
        for record in self:
            if record.tarjeta_juridico_doc:
                filename = record.tarjeta_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('tarjeta_juridico_doc')
    def _onchange_propuesta_juridico_doc(self):
        if self.tarjeta_juridico_doc:
            self.tarjeta_juridico = True

    @api.onchange('tarjeta_juridico_doc')
    def _onchange_delete_propuesta_juridico_doc(self):
        if not self.tarjeta_juridico_doc:
            self.tarjeta_juridico = False

    @api.model
    def cargar_documento_65(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'tarjeta_juridico_doc': file_content,
            'tarjeta_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_tarjeta = {}

    def abrir_documento_tarjeta_juridico_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.tarjeta_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_tarjeta:
            drive_document = self._cache_documentos_tarjeta[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_tarjeta[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.tarjeta_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ###################################################################################################
    otro_j = fields.Boolean('Otro', tracking=True)
    aprueba_f_otro = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_otro = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    aprueba_j_h_otro_doc = fields.Binary(tracking=True)
    aprueba_j_h_otro_doc_fname = fields.Char(tracking=True)

    @api.constrains('aprueba_j_h_otro_doc')
    def _check_file_extension45(self):
        for record in self:
            if record.aprueba_j_h_otro_doc:
                filename = record.aprueba_j_h_otro_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    @api.onchange('aprueba_j_h_otro_doc')
    def _onchange_aprueba_j_h_otro_doc(self):
        if self.aprueba_j_h_otro_doc:
            self.aprueba_f_otro = True

    @api.onchange('aprueba_j_h_otro_doc')
    def _onchange_delete_aprueba_j_h_otro_doc(self):
        if not self.aprueba_j_h_otro_doc:
            self.aprueba_f_otro = False

    @api.model
    def cargar_documento_60(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'aprueba_j_h_otro_doc': file_content,
            'aprueba_j_h_otro_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_otro_j = {}

    def abrir_documento_otro_j_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.aprueba_j_h_otro_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_otro_j:
            drive_document = self._cache_documentos_otro_j[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_otro_j[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.aprueba_j_h_otro_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    ###################################PERSONA NATURAL#######################################################
    su_cdp = fields.Boolean('CDP', tracking=True)


    @api.constrains('su_cdp_doc')
    def _check_file_extension46(self):
        for record in self:
            if record.su_cdp_doc:
                filename = record.su_cdp_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_su_cdp = {}

    def abrir_documento_su_cdp_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_su_cdp_en_drive()
        # Primero, verificamos si 'su_cdp_id' está configurado
        if self.su_cdp_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'RPC',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.su_cdp_id.id,
                'target': 'new',
            }

        # Si 'su_cdp_doc_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.su_cdp_doc_fname:
            file_name_to_search = self.su_cdp_doc_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_su_cdp:
                drive_document = self._cache_documentos_su_cdp[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_su_cdp[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'su_cdp_id' con el ID del documento encontrado
                self.write({'su_cdp_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'RPC',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
                }

            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Cert.afiliación examen preocupacional con este registro.')

    su_cdp_actualizar_visible = fields.Boolean(default=False, related='su_cdp_id.actualizar_visible', readonly=False)

    def actualizar_correo_su_cdp_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.su_cdp_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.su_cdp_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.su_cdp_actualizar_visible,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False



    @api.onchange('su_cdp_doc')
    def _onchange_su_cdp_doc(self):
        if self.su_cdp_doc:
            self.aprueba_f_a_su_cdp = True

    @api.onchange('su_cdp_doc')
    def _onchange_delete_su_cdp_doc(self):
        if not self.su_cdp_doc:
            self.aprueba_f_a_su_cdp = False

    @api.model
    def cargar_documento_56(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'su_cdp_doc': file_content,
            'su_cdp_doc_fname': file_name,
        })

    aprueba_j_h_su_cdp = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_a_su_cdp = fields.Boolean('Aprueba-F', tracking=True)
    ##########################################################################################
    su_contrato_natu = fields.Boolean('Contrato', tracking=True)
    su_contrato_natu_doc = fields.Binary(tracking=True)

    @api.constrains('su_contrato_natu_doc')
    def _check_file_extension46(self):
        for record in self:
            if record.su_contrato_natu_doc:
                filename = record.su_contrato_natu_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_su_contrato_natu = {}

    def abrir_documento_su_contrato_natu_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.su_contrato_natu_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_su_contrato_natu:
            drive_document = self._cache_documentos_su_contrato_natu[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_su_contrato_natu[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.su_contrato_natu_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    su_contrato_natu_doc_fname = fields.Char(tracking=True)

    @api.onchange('su_contrato_natu_doc')
    def _onchange_su_contrato_natu_doc(self):
        if self.su_contrato_natu_doc:
            self.aprueba_f_a_su_contrato_natu = True

    @api.onchange('su_contrato_natu_doc')
    def _onchange_delete_su_contrato_natu_doc(self):
        if not self.su_contrato_natu_doc:
            self.aprueba_f_a_su_contrato_natu = False

    @api.model
    def cargar_documento_56(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'su_contrato_natu_doc': file_content,
            'su_contrato_natu_doc_fname': file_name,
        })

    aprueba_j_h_su_contrato_natu = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_a_su_contrato_natu = fields.Boolean('Aprueba-F', tracking=True)
    ##########################################################################################
    su_rut = fields.Boolean('RUT', tracking=True)
    su_rut_doc = fields.Binary(tracking=True)

    aprueba_j_h_su_rut = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_a_su_rut = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('su_rut_doc')
    def _check_file_extension46(self):
        for record in self:
            if record.su_rut_doc:
                filename = record.su_rut_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_su_rut = {}

    def abrir_documento_su_rut_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.su_rut_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_su_rut:
            drive_document = self._cache_documentos_su_rut[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_su_rut[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.su_rut_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    su_rut_doc_fname = fields.Char(tracking=True)

    @api.onchange('su_rut_doc')
    def _onchange_su_rut_doc(self):
        if self.su_rut_doc:
            self.su_rut = True

    @api.onchange('su_rut_doc')
    def _onchange_delete_su_rut_doc(self):
        if not self.su_rut_doc:
            self.su_rut = False

    @api.model
    def cargar_documento_100(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'su_rut_doc': file_content,
            'su_rut_doc_fname': file_name,
        })

    ##########################################################################################
    su_salud = fields.Boolean('Certificado Salud', tracking=True)
    su_salud_doc = fields.Binary(tracking=True)

    aprueba_j_h_su_salud = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_h_h_su_salud = fields.Boolean('Aprueba - Aprueba área Recursos humanos', tracking=True)
    aprueba_f_a_su_salud = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('su_salud_doc')
    def _check_file_extension46(self):
        for record in self:
            if record.su_salud_doc:
                filename = record.su_salud_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_su_salud = {}

    def abrir_documento_su_salud_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.su_salud_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_su_salud:
            drive_document = self._cache_documentos_su_salud[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_su_salud[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.su_salud_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    su_salud_doc_fname = fields.Char(tracking=True)

    @api.onchange('su_salud_doc')
    def _onchange_su_salud_doc(self):
        if self.su_salud_doc:
            self.su_salud = True

    @api.onchange('su_salud_doc')
    def _onchange_delete_su_salud_doc(self):
        if not self.su_salud_doc:
            self.su_salud = False

    @api.model
    def cargar_documento_100(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'su_salud_doc': file_content,
            'su_salud_doc_fname': file_name,
        })

    ##########################################################################################
    su_pension = fields.Boolean('Certificado de pensión', tracking=True)
    su_pension_doc = fields.Binary(tracking=True)

    aprueba_j_h_su_pension = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_h_h_su_pension = fields.Boolean('Aprueba - Aprueba área Recursos humanos', tracking=True)
    aprueba_f_a_su_pension = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('su_pension_doc')
    def _check_file_extension46(self):
        for record in self:
            if record.su_pension_doc:
                filename = record.su_pension_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_su_pension = {}

    def abrir_documento_su_pension_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.su_pension_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_su_pension:
            drive_document = self._cache_documentos_su_pension[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_su_pension[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.su_pension_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    su_pension_doc_fname = fields.Char(tracking=True)

    @api.onchange('su_pension_doc')
    def _onchange_su_pension_doc(self):
        if self.su_pension_doc:
            self.su_pension = True

    @api.onchange('su_pension_doc')
    def _onchange_delete_su_pension_doc(self):
        if not self.su_pension_doc:
            self.su_pension = False

    @api.model
    def cargar_documento_100(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'su_pension_doc': file_content,
            'su_pension_doc_fname': file_name,
        })

    ##########################################################################################

    selecionar_todos_p_juri = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_p_juri')
    def _onchange_selecionar_todos_p_juri(self):
        if self.selecionar_todos_p_juri:
            self.contratos_juridico_otro = True

        else:
            self.contratos_juridico_otro = False


    # chulear todos Persona juridica F

    selecionar_todos_p_juri_f = fields.Boolean('Seleccionar todos', tracking=True)


    @api.onchange('selecionar_todos_p_juri_f')
    def _onchange_selecionar_todos_p_juri_f(self):
        if self.selecionar_todos_p_juri_f:
            self.aprueba_f_l_contratos_otro_juridico = True
            self.aprueba_f_camara_de_comercio_juridico = True
            self.aprueba_f_rut_juridico = True
            self.aprueba_f_cert_contraloria_juridico = True
            self.aprueba_f_cert_procuraduria_juridico = True
            self.aprueba_f_certificados_de_contraloria_rep_legal_juridico = True
            self.aprueba_f_certificados_de_Procuraduria_rep_legal_juridico = True
            self.aprueba_f_cdp_juridico = True
            self.aprueba_f_contrato_juridico = True
            self.aprueba_f_certificados_de_policia_judicial_juridico = True
            self.aprueba_f_certificado_de_inhabilidades_rep_juridico = True
            self.aprueba_f_certificado_de_pago_parafiscales_juridico = True
            self.aprueba_f_planilla_de_seguridad_social_juridico = True
            self.aprueba_f_certif_de_junta_de_contadores_juridico = True
            self.aprueba_f_cedula_de_revisor_juridico = True
            self.aprueba_f_tarjeta_juridico = True
            self.aprueba_f_otro = True
        else:
            self.aprueba_f_l_contratos_otro_juridico = False
            self.aprueba_f_camara_de_comercio_juridico = False
            self.aprueba_f_rut_juridico = False
            self.aprueba_f_cert_contraloria_juridico = False
            self.aprueba_f_cert_procuraduria_juridico = False
            self.aprueba_f_certificados_de_contraloria_rep_legal_juridico = False
            self.aprueba_f_certificados_de_Procuraduria_rep_legal_juridico = False
            self.aprueba_f_cdp_juridico = False
            self.aprueba_f_contrato_juridico = False
            self.aprueba_f_certificados_de_policia_judicial_juridico = False
            self.aprueba_f_certificado_de_inhabilidades_rep_juridico = False
            self.aprueba_f_certificado_de_pago_parafiscales_juridico = False
            self.aprueba_f_planilla_de_seguridad_social_juridico = False
            self.aprueba_f_certif_de_junta_de_contadores_juridico = False
            self.aprueba_f_cedula_de_revisor_juridico = False
            self.aprueba_f_tarjeta_juridico = False
            self.aprueba_f_otro = False


    # chulear todos Persona juridica F

    selecionar_todos_p_juri_j = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_p_juri_j')
    def _onchange_selecionar_todos_p_juri_j(self):
        if self.selecionar_todos_p_juri_j:
            self.aprueba_j_h_contratos_otro_juridico = True


        else:
            self.aprueba_j_h_contratos_otro_juridico = False




    selecionar_todos_p_rh = fields.Boolean('Seleccionar todos', tracking=True)



    # chulear todos

    selecionar_todos = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos')
    def _onchange_selecionar_todos(self):
        if self.selecionar_todos:
            self.su_rut = True
            self.su_salud = True
            self.su_pension = True
            self.su_arl = True
            self.su_cdp = True
            self.su_contrato_natu = True
            self.contraloria_otro = True
            self.procuraduria_otro = True
            self.policia_otro = True
            self.cautelares_otro = True
            self.inhabilidades_otro = True
            self.contratos_otro = True
        else:
            self.su_rut = False
            self.su_salud = False
            self.su_pension = False
            self.su_arl = False
            self.su_cdp = False
            self.su_contrato_natu = False
            self.contraloria_otro = False
            self.procuraduria_otro = False
            self.policia_otro = False
            self.cautelares_otro = False
            self.inhabilidades_otro = False
            self.contratos_otro = False

    # chulear todos Financiera

    selecionar_todos_f = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_f')
    def _onchange_selecionar_todos_f(self):
        if self.selecionar_todos_f:
            self.aprueba_f_a_su_rut = True
            self.aprueba_f_a_su_salud = True
            self.aprueba_f_a_su_pension = True
            self.aprueba_f_a_su_arl = True
            self.aprueba_f_a_su_cdp = True
            self.aprueba_f_a_su_contrato_natu = True
            self.aprueba_f_l_contraloria_otro = True
            self.aprueba_f_l_procuraduria_otro = True
            self.aprueba_f_l_policia_otro = True
            self.aprueba_f_l_cautelares_otro = True
            self.aprueba_f_l_inhabilidades_otro = True
            self.aprueba_f_l_contratos_otro = True
        else:
            self.aprueba_f_a_su_rut = False
            self.aprueba_f_a_su_salud = False
            self.aprueba_f_a_su_pension = False
            self.aprueba_f_a_su_arl = False
            self.aprueba_f_a_su_cdp = False
            self.aprueba_f_a_su_contrato_natu = False
            self.aprueba_f_l_contraloria_otro = False
            self.aprueba_f_l_procuraduria_otro = False
            self.aprueba_f_l_policia_otro = False
            self.aprueba_f_l_cautelares_otro = False
            self.aprueba_f_l_inhabilidades_otro = False
            self.aprueba_f_l_contratos_otro = False


    # chulear todos Jurídico

    selecionar_todos_j = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_j')
    def _onchange_selecionar_todos_j(self):
        if self.selecionar_todos_j:
            self.aprueba_j_h_su_rut = True
            self.aprueba_j_h_su_salud = True
            self.aprueba_j_h_su_pension = True
            self.aprueba_j_h_su_arl = True
            self.aprueba_j_h_su_cdp = True
            self.aprueba_j_h_su_contrato_natu = True
            self.aprueba_j_h_contraloria_otro = True
            self.aprueba_j_h_procuraduria_otro = True
            self.aprueba_j_h_policia_otro = True
            self.aprueba_j_h_cautelares_otro = True
            self.aprueba_j_h_inhabilidades_otro = True
            self.aprueba_j_h_contratos_otro = True
        else:
            self.aprueba_j_h_su_rut = False
            self.aprueba_j_h_su_salud = False
            self.aprueba_j_h_su_pension = False
            self.aprueba_j_h_su_arl = False
            self.aprueba_j_h_su_cdp = False
            self.aprueba_j_h_su_contrato_natu = False
            self.aprueba_j_h_contraloria_otro = False
            self.aprueba_j_h_procuraduria_otro = False
            self.aprueba_j_h_policia_otro = False
            self.aprueba_j_h_cautelares_otro = False
            self.aprueba_j_h_inhabilidades_otro = False
            self.aprueba_j_h_contratos_otro = False

    @api.onchange('selecionar_todos_p_rh')
    def _onchange_selecionar_todos_p_rh(self):
        if self.selecionar_todos_p_rh:
            self.aprueba_h_h_su_arl = True
            self.aprueba_h_h_su_salud = True
            self.aprueba_h_h_su_pension = True

        else:
            self.aprueba_h_h_su_arl = False
            self.aprueba_h_h_su_salud = False
            self.aprueba_h_h_su_pension = False


    su_arl = fields.Boolean('Certificado de ARL', tracking=True)
    su_arl_doc = fields.Binary(tracking=True)

    aprueba_j_h_su_arl = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_h_h_su_arl = fields.Boolean('Aprueba - Aprueba área Recursos humanos', tracking=True)
    aprueba_f_a_su_arl = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('su_arl_doc')
    def _check_file_extension46(self):
        for record in self:
            if record.su_arl_doc:
                filename = record.su_arl_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_su_arl = {}

    def abrir_documento_su_arl_pdf(self):
        self.su_arl_doc = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_su_arl_en_drive()

        # Primero, verificamos si 'su_arl_doc_id' está configurado
        if self.su_arl_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'ARL',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.su_arl_doc_id.id,
                'target': 'new',
            }

        # Si 'propuesta_pj_doc_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.su_arl_doc_fname:
            file_name_to_search = self.su_arl_doc_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_su_arl:
                drive_document = self._cache_documentos_su_arl[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_su_arl[file_name_to_search] = drive_document

                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'su_arl_doc_id' con el ID del documento encontrado
                self.write({'su_arl_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'ARL',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        raise UserError("No se encontró un nombre de archivo para buscar en el modelo Drive.")

    su_arl_doc_actualizar_visible = fields.Boolean(default=False, related='su_arl_doc_id.actualizar_visible', readonly=False, store=True)

    file_data_visible = fields.Boolean(default=False)

    def actualizar_su_arl_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.su_arl_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.su_arl_doc_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.su_arl_doc_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    su_arl_doc_fname = fields.Char(tracking=True)

    @api.onchange('su_arl_doc')
    def _onchange_su_arl_doc(self):
        if self.su_arl_doc:
            self.su_arl = True

    @api.onchange('su_arl_doc')
    def _onchange_delete_su_arl_doc(self):
        if not self.su_arl_doc:
            self.su_arl = False

    @api.model
    def cargar_documento_100(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'su_arl_doc': file_content,
            'su_arl_doc_fname': file_name,
        })

    #############################################################################################
    propuesta = fields.Boolean('Propuesta', tracking=True)
    propuesta_doc = fields.Binary(tracking=True)
    propuesta_doc_fname = fields.Char(tracking=True)
    propuesta_doc_id = fields.Many2one('drive', string='Propuesta', tracking=True)
    propuesta_doc_visible = fields.Boolean(default=True)

    @api.onchange('propuesta_doc')
    def _onchange_propuesta_doc(self):
        if self.propuesta_doc:
            self.propuesta = True

    @api.onchange('propuesta_doc')
    def _onchange_delete_propuesta_doc(self):
        if not self.propuesta_doc:
            self.propuesta = False

    def action_upload_propuesta_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir propuesta',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_otrosi_documentos_id': self.id,
                'default_stage5_id': self.drive_file_id.id if self.drive_file_id else False,
                'default_folder_id': google_folder_id,
                'default_otrosi_bolean': True,
                'tipo_documento_upload': 'propuesta_otrosi',
            },
        }

    def abrir_documento_propuesta_pdf(self):
        self.propuesta_doc = False
        self.ensure_one()

        if self.propuesta_doc_id:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Propuesta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.propuesta_doc_id.id,
                'target': 'new',
            }

        if self.propuesta_doc_fname:
            file_name_to_search = self.propuesta_doc_fname.strip().replace(" ", "_")
            drive_document = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)
            if drive_document:
                self.write({'propuesta_doc_id': drive_document.id})
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Propuesta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        raise UserError('No hay ninguna propuesta relacionada con este registro.')

    def subir_propuesta_doc_drive(self):
        if self.propuesta_doc and self.propuesta_doc_fname:
            _logger.info("Subiendo propuesta '%s' a Google Drive...", self.propuesta_doc_fname)
            result = self._upload_propuesta_doc_drive_pn()
            if not result:
                raise UserError("❌ Error al subir la propuesta a Google Drive. Intente nuevamente más tarde.")
            self.message_post(body="✅ Propuesta subida correctamente a Google Drive.")
        else:
            _logger.info("No hay archivo de propuesta para subir. No se realiza ninguna acción.")

    def _upload_propuesta_doc_drive_pn(self):
        self.ensure_one()

        if not self.propuesta_doc or not self.propuesta_doc_fname:
            raise UserError("No hay archivo para subir a Google Drive.")

        if not self.drive_file_id:
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        carpeta_drive = drive_file_record.folder_id if drive_file_record else False
        if not carpeta_drive:
            raise UserError("El archivo de Drive no tiene una carpeta válida.")

        temp_file_path = None
        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            file_content = base64.b64decode(self.propuesta_doc)
            original_extension = os.path.splitext(self.propuesta_doc_fname)[1] or ".pdf"
            fixed_file_name = f"Propuesta_{uuid.uuid4()}{original_extension}"

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            file_drive = drive.CreateFile({
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}],
            })
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                return False

            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': f"https://drive.google.com/file/d/{file_id}/view",
                'file_data': self.propuesta_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id,
            })

            if not self.propuesta_doc_id:
                self.propuesta_doc_id = drive_record.id

            self.propuesta_doc_visible = False
            self.propuesta_doc = False
            return True

        except Exception as e:
            _logger.error("Error al subir propuesta a Google Drive: %s", e)
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    #############################################################################################
    contratos_otro = fields.Boolean('Contrato', tracking=True)

    aprueba_j_h_contratos_otro = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_contratos_otro = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('contratos_otro_doc')
    def _check_file_extension47(self):
        for record in self:
            if record.contratos_otro_doc:
                filename = record.contratos_otro_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_contratos_otro = {}

    def abrir_documento_contratos_otro_pdf(self):
        self.contratos_otro_doc = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_contratos_otro_en_drive()
        # Primero, verificamos si 'contratos_otro_id' está configurado
        if self.contratos_otro_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Contrato otrosí',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.contratos_otro_id.id,
                'target': 'new',
            }

        # Si 'contratos_otro_doc_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.contratos_otro_doc_fname:
            file_name_to_search = self.contratos_otro_doc_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_contratos_otro:
                drive_document = self._cache_documentos_contratos_otro[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_contratos_otro[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'contratos_otro_id' con el ID del documento encontrado
                self.write({'contratos_otro_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Contrato otrosí',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
                }

            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Cert.afiliación examen preocupacional con este registro.')

    contratos_otro_actualizar_visible = fields.Boolean(default=False, related='contratos_otro_id.actualizar_visible', readonly=False)

    def actualizar_correo_contratos_otro_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.contratos_otro_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.contratos_otro_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.contratos_otro_actualizar_visible,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False



    @api.onchange('contratos_otro_doc')
    def _onchange_contratos_otro_doc(self):
        if self.contratos_otro_doc:
            self.contratos_otro = True

    @api.onchange('contratos_otro_doc')
    def _onchange_delete_contratos_otro_doc(self):
        if not self.contratos_otro_doc:
            self.contratos_otro = False

    @api.model
    def cargar_documento_50(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'contratos_otro_doc': file_content,
            'contratos_otro_doc_fname': file_name,
        })

    #############################################################################################
    contraloria_otro = fields.Boolean('Cert. Contraloría', tracking=True)
    contraloria_otro_doc = fields.Binary(tracking=True)
    aprueba_j_h_contraloria_otro = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_contraloria_otro = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('contraloria_otro_doc')
    def _check_file_extension47(self):
        for record in self:
            if record.contraloria_otro_doc:
                filename = record.contraloria_otro_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_contraloria_otro = {}

    def abrir_documento_contraloria_otro_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.contraloria_otro_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_contraloria_otro:
            drive_document = self._cache_documentos_contraloria_otro[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_contraloria_otro[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.contraloria_otro_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    contraloria_otro_doc_fname = fields.Char(tracking=True)

    @api.onchange('contraloria_otro_doc')
    def _onchange_contraloria_otro_doc(self):
        if self.contraloria_otro_doc:
            self.contraloria_otro = True

    @api.onchange('contraloria_otro_doc')
    def _onchange_delete_contraloria_otro_doc(self):
        if not self.contraloria_otro_doc:
            self.contraloria_otro = False

    @api.model
    def cargar_documento_50(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'contraloria_otro_doc': file_content,
            'contraloria_otro_doc_fname': file_name,
        })

    #############################################################################################
    procuraduria_otro = fields.Boolean('Cert. Procuraduría', tracking=True)
    procuraduria_otro_doc = fields.Binary(tracking=True)
    aprueba_j_h_procuraduria_otro = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_procuraduria_otro = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('procuraduria_otro_doc')
    def _check_file_extension47(self):
        for record in self:
            if record.procuraduria_otro_doc:
                filename = record.procuraduria_otro_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_procuraduria_otro = {}

    def abrir_documento_procuraduria_otro_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.procuraduria_otro_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_procuraduria_otro:
            drive_document = self._cache_documentos_procuraduria_otro[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_procuraduria_otro[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.procuraduria_otro_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    procuraduria_otro_doc_fname = fields.Char(tracking=True)

    @api.onchange('procuraduria_otro_doc')
    def _onchange_procuraduria_otro_doc(self):
        if self.procuraduria_otro_doc:
            self.procuraduria_otro = True

    @api.onchange('procuraduria_otro_doc')
    def _onchange_delete_procuraduria_otro_doc(self):
        if not self.procuraduria_otro_doc:
            self.procuraduria_otro = False

    @api.model
    def cargar_documento_50(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'procuraduria_otro_doc': file_content,
            'procuraduria_otro_doc_fname': file_name,
        })

    #############################################################################################
    policia_otro = fields.Boolean('Certif. de Policía Judicial', tracking=True)
    policia_otro_doc = fields.Binary(tracking=True)
    aprueba_j_h_policia_otro = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_policia_otro = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('policia_otro_doc')
    def _check_file_extension47(self):
        for record in self:
            if record.policia_otro_doc:
                filename = record.policia_otro_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_policia_otro = {}

    def abrir_documento_policia_otro_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.policia_otro_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_policia_otro:
            drive_document = self._cache_documentos_policia_otro[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_policia_otro[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.policia_otro_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    policia_otro_doc_fname = fields.Char(tracking=True)

    @api.onchange('policia_otro_doc')
    def _onchange_policia_otro_doc(self):
        if self.policia_otro_doc:
            self.policia_otro = True

    @api.onchange('policia_otro_doc')
    def _onchange_delete_policia_otro_doc(self):
        if not self.policia_otro_doc:
            self.policia_otro = False

    @api.model
    def cargar_documento_50(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'policia_otro_doc': file_content,
            'policia_otro_doc_fname': file_name,
        })

    #############################################################################################
    cautelares_otro = fields.Boolean('Cert. Medidas cautelares', tracking=True)
    cautelares_otro_doc = fields.Binary(tracking=True)
    aprueba_j_h_cautelares_otro = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_cautelares_otro = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('cautelares_otro_doc')
    def _check_file_extension47(self):
        for record in self:
            if record.cautelares_otro_doc:
                filename = record.cautelares_otro_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cautelares_otro = {}

    def abrir_documento_cautelares_otro_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.cautelares_otro_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_cautelares_otro:
            drive_document = self._cache_documentos_cautelares_otro[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_cautelares_otro[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.cautelares_otro_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError('Documento no encontrado en el modelo Drive.')

    cautelares_otro_doc_fname = fields.Char(tracking=True)

    @api.onchange('cautelares_otro_doc')
    def _onchange_cautelares_otro_doc(self):
        if self.cautelares_otro_doc:
            self.cautelares_otro = True

    @api.onchange('cautelares_otro_doc')
    def _onchange_delete_cautelares_otro_doc(self):
        if not self.cautelares_otro_doc:
            self.cautelares_otro = False

    @api.model
    def cargar_documento_50(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cautelares_otro_doc': file_content,
            'cautelares_otro_doc_fname': file_name,
        })

    #############################################################################################
    inhabilidades_otro = fields.Boolean('Certif. de Inhabilidades', tracking=True)
    inhabilidades_otro_doc = fields.Binary(tracking=True)
    aprueba_j_h_inhabilidades_otro = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_inhabilidades_otro = fields.Boolean('Aprueba-F', tracking=True)

    @api.constrains('inhabilidades_otro_doc')
    def _check_file_extension47(self):
        for record in self:
            if record.inhabilidades_otro_doc:
                filename = record.inhabilidades_otro_doc_fname
                if not filename.lower().endswith('.pdf'):
                    raise ValidationError("Solo se permiten archivos PDF.")

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_inhabilidades_otro = {}

    def abrir_documento_inhabilidades_otro_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener el nombre base del archivo sin la fecha y hora
        base_file_name = self.inhabilidades_otro_doc_fname.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', base_file_name)

        # Comprobar si el documento ya está en el caché
        if base_file_name in self._cache_documentos_inhabilidades_otro:
            drive_document = self._cache_documentos_inhabilidades_otro[base_file_name]
        else:
            # Buscar documentos en el modelo 'drive' que contengan el nombre base
            drive_documents = self.env['drive'].search([('file_name', 'ilike', base_file_name)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el caché
                self._cache_documentos_inhabilidades_otro[base_file_name] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.inhabilidades_otro_doc_fname,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error y registrar el intento
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', base_file_name)
            raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {base_file_name}')

    inhabilidades_otro_doc_fname = fields.Char(tracking=True)

    @api.onchange('inhabilidades_otro_doc')
    def _onchange_inhabilidades_otro_doc(self):
        if self.inhabilidades_otro_doc:
            self.inhabilidades_otro = True

    @api.onchange('inhabilidades_otro_doc')
    def _onchange_delete_inhabilidades_otro_doc(self):
        if not self.inhabilidades_otro_doc:
            self.inhabilidades_otro = False

    @api.model
    def cargar_documento_50(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'inhabilidades_otro_doc': file_content,
            'inhabilidades_otro_doc_fname': file_name,
        })

    #################################################################################################################################

    def buscar_carpeta(self):
        # Si el padre no tiene carpeta Drive aún, crearla primero automáticamente
        if not self.contratacion_fuv_en_id.drive_file_id:
            _logger.info(
                f"Contratación padre '{self.contratacion_fuv_en_id.name}' no tiene carpeta Drive. Creándola automáticamente."
            )
            self.contratacion_fuv_en_id.buscar_carpeta()

        # Si después de intentar crear la carpeta del padre sigue sin tenerla, no se puede continuar
        if not self.contratacion_fuv_en_id.drive_file_id:
            _logger.warning(
                f"No se pudo crear la carpeta del contrato padre '{self.contratacion_fuv_en_id.name}'. "
                f"Otrosí ID {self.id} queda con carpeta pendiente."
            )
            self.write({'folder_pending_creation': True})
            self.message_post(
                body="⚠️ No se pudo crear la carpeta en Drive: la contratación padre no pudo crear su carpeta en Drive. "
                     "Verifique la configuración de Drive del contrato padre."
            )
            return

        # Si el nombre aún no fue calculado (computed sin store), forzarlo ahora
        if not self.name:
            self._compute_name()
        if not self.name:
            _logger.warning(f"El registro ID {self.id} no tiene nombre calculado. Verifique que el contrato padre tenga 'new_name' configurado.")
            self.write({'folder_pending_creation': True})
            self.message_post(body="⚠️ No se pudo crear la carpeta en Drive: el registro no tiene nombre calculado. Verifique el campo 'new_name' del contrato padre.")
            return

        drive_file = self.contratacion_fuv_en_id.drive_file_id

        if not drive_file.folder_id2:
            _logger.warning(f"La carpeta padre en Drive no tiene folder_id2 configurado. Otrosí ID {self.id} queda pendiente.")
            self.write({'folder_pending_creation': True})
            self.message_post(
                body="⚠️ No se pudo crear la carpeta en Drive: la carpeta del contrato padre no tiene ID de Drive (folder_id2) configurado."
            )
            return

        # Bug 2 fix: buscar por el otrosi_drive ya vinculado a este registro, no por nombre
        if self.drive_file_id:
            _logger.info(f"Otrosí ID {self.id} ya tiene drive_file_id={self.drive_file_id.id}. No se crea otra carpeta.")
            return

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            unique_id = str(uuid.uuid4())[:8]
            clean_name = self.name.replace('/', '_').replace('\\', '_')
            drive_folder_name = f"{clean_name}_{unique_id}"

            folder_metadata = {
                'title': drive_folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': drive_file.folder_id2}]
            }

            folder = drive.CreateFile(folder_metadata)
            folder.Upload()

            new_counter_value = self.env['otrosi_drive'].search_count([]) + 1

            otrosi_drive = self.env['otrosi_drive'].create({
                'folder_name': drive_folder_name,
                'drive_id': drive_file.id,
                'folder_guardar_id': folder['id'],
                'folder_id': folder['id'],
                'registro_counter': new_counter_value,
                'folder_pending_creation': False
            })

            self.write({'drive_file_id': otrosi_drive.id, 'folder_pending_creation': False})
            _logger.info(f"Carpeta creada en Drive para Otrosí ID {self.id}: {drive_folder_name}")

        except Exception as e:
            # Bug 3 fix: no relanzar como UserError para no revertir el registro de Odoo.
            # El registro queda guardado y marcado como pendiente para reintento manual.
            _logger.error(f"Error al crear carpeta en Drive para Otrosí ID {self.id}: {e}")
            self.write({'folder_pending_creation': True})
            self.message_post(
                body=f"⚠️ El registro fue creado en Odoo pero no se pudo crear la carpeta en Google Drive: {e}. "
                     f"Use el botón 'Crear carpeta' cuando Drive esté disponible."
            )

    folder_pending_creation = fields.Boolean(string="Creación de carpeta pendiente", default=False)

    def action_guardar_y_cerrar(self):
        self.ensure_one()
        if not self.drive_file_id:
            self.buscar_carpeta()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'otro_si',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    cdp_doc_juridico_fname = fields.Char(tracking=True)
    cdp_juridico_doc = fields.Binary(tracking=True)

    cdp_juridico_doc_id = fields.Many2one('drive', string='RPC  ', tracking=True)
    cdp_juridico_doc_visible = fields.Boolean(default=True)

    def action_upload_cdp_juridico_doc(self):
        """
        Abre la ventana emergente para subir el documento RPC (cdp_juridico).
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC', # Nombre de la ventana
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculamos al registro de OTRO SI
                'default_otrosi_documentos_id': self.id,
                
                # ID de carpeta Odoo
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de carpeta Google Drive
                'default_folder_id': google_folder_id, 
                'default_otrosi_bolean': True,
                
                # CLAVE ÚNICA para este documento en la lista de Otrosi
                'tipo_documento_upload': 'cdp_juridico_otrosi' 
            },
        }

    def subir_cdp_juridico_doc_a_drive(self):
        """Sube el documento 'RPC' a Google Drive y lo registra en el modelo drive."""
        if self.cdp_juridico_doc and self.cdp_doc_juridico_fname:
            _logger.info(f"Subiendo documento '{self.cdp_doc_juridico_fname}' a Google Drive...")

            result = self._upload_cdp_juridico_doc_doc_to_drive_pn()

            if not result:
                raise UserError("❌ Error al subir el RPC a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ RPC subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'RPC' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_cdp_juridico_doc_doc_to_drive_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        self.ensure_one()

        if not self.cdp_juridico_doc or not self.cdp_doc_juridico_fname:
            _logger.error("No hay archivo para subir a Google Drive.")
            raise UserError("No hay archivo para subir a Google Drive.")

        if not self.drive_file_id:
            _logger.error("No se encontró un drive_file_id. No se puede subir el archivo.")
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("No se encontró el registro en Drive. Verifique el archivo de Drive.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta válida en Drive para el archivo ID {self.drive_file_id.id}.")
            raise UserError("El archivo de Drive no tiene una carpeta válida.")

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            file_content = base64.b64decode(self.cdp_juridico_doc)
            original_extension = os.path.splitext(self.cdp_doc_juridico_fname)[1] or ".pdf"
            fixed_file_name = f"RPC_{uuid.uuid4()}{original_extension}"

            # Validación de duplicado en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            # Guardar archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a la carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida del archivo a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido correctamente con ID: {file_id} y URL: {file_link}")

            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.cdp_juridico_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(f"Registro en drive creado con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            if not self.cdp_juridico_doc_id:
                self.cdp_juridico_doc_id = drive_record.id
                _logger.info(f"Campo cdp_juridico_doc_id actualizado con ID {drive_record.id}")

            self.cdp_juridico_doc_visible = False
            self.cdp_juridico_doc = False
            _logger.info("Campos cdp_juridico_doc_visible y cdp_juridico_doc limpiados.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal eliminado.")


    contratos_firmado_juridico_doc_fname = fields.Char(tracking=True)
    
    contratos_firmado_juridico = fields.Boolean('Contrato', tracking=True)
    contratos_firmado_juridico_doc = fields.Binary(tracking=True)
    contratos_firmado_juridico_id = fields.Many2one('drive', string='Contrato Firmado', readonly=True)
    contratos_firmado_juridico_doc_visible = fields.Boolean(default=True)

    def action_upload_contratos_firmado_juridico_doc(self):
        """
        Abre la ventana emergente para subir el documento Contrato Firmado (Jurídico).
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contrato otrosí firmado', # Nombre de la ventana
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculamos al registro de OTRO SI
                'default_otrosi_documentos_id': self.id,
                
                # ID de carpeta Odoo
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de carpeta Google Drive
                'default_folder_id': google_folder_id, 
                'default_otrosi_bolean': True,
                
                # CLAVE ÚNICA para este documento
                'tipo_documento_upload': 'contratos_firmado_juridico' 
            },
        }

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_contratos_juridico_firmado = {}

    def abrir_documento_contratos_firmado_juridico_pdf(self):
        self.contratos_firmado_juridico_doc = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_contratos_firmado_juridico2_en_drive()

        # Primero, verificamos si 'contratos_firmado_id' está configurado
        if self.contratos_firmado_juridico_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Otrosí firmado',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.contratos_firmado_juridico_id.id,
                'target': 'new',
            }

        # Si 'contratos_firmado_doc_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.contratos_firmado_juridico_doc_fname:
            file_name_to_search = self.contratos_firmado_juridico_doc_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_contratos_juridico_firmado:
                drive_document = self._cache_documentos_contratos_juridico_firmado[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_contratos_juridico_firmado[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'contratos_firmado_juridico_id' con el ID del documento encontrado
                self.write({'contratos_firmado_juridico_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Otrosí firmado',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún contrato firmado relacionado con este registro.')

    actualizar_correo_contratos_firmado_juridico_en_drive = fields.Boolean(default=False, related='contratos_firmado_juridico_id.actualizar_visible')

    def actualizar_correo_contratos_firmado_juridico2_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.contratos_firmado_juridico_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.contratos_firmado_juridico_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.actualizar_correo_contratos_firmado_juridico_en_drive,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def subir_contratos_firmado_juridico_doc_drive(self):
        """Sube el documento 'Contratos firmado' a Google Drive y lo registra en el modelo drive."""
        if self.contratos_firmado_juridico_doc and self.contratos_firmado_juridico_doc_fname:
            _logger.info(f"Subiendo documento '{self.contratos_firmado_juridico_doc_fname}' a Google Drive...")

            result = self._upload_contratos_firmado_juridico_doc_drive_pn()

            if not result:
                raise UserError("❌ Error al subir el Contratos firmado a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ Contratos firmado subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'Contratos firmado' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_contratos_firmado_juridico_doc_drive_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        self.ensure_one()

        if not self.contratos_firmado_juridico_doc or not self.contratos_firmado_juridico_doc_fname:
            _logger.error("No hay archivo para subir a Google Drive.")
            raise UserError("No hay archivo para subir a Google Drive.")

        if not self.drive_file_id:
            _logger.error("No se encontró un drive_file_id. No se puede subir el archivo.")
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("No se encontró el registro en Drive. Verifique el archivo de Drive.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta válida en Drive para el archivo ID {self.drive_file_id.id}.")
            raise UserError("El archivo de Drive no tiene una carpeta válida.")

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            file_content = base64.b64decode(self.contratos_firmado_juridico_doc)
            original_extension = os.path.splitext(self.contratos_firmado_juridico_doc_fname)[1] or ".pdf"
            fixed_file_name = f"Contrato_Firmado_de_{self.name}_{uuid.uuid4()}{original_extension}"

            # Validación de duplicados en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a la carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida del archivo a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido correctamente con ID: {file_id} y URL: {file_link}")

            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.contratos_firmado_juridico_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(f"Registro creado en 'drive' con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            if not self.contratos_firmado_juridico_id:
                self.contratos_firmado_juridico_id = drive_record.id
                _logger.info(f"Campo contratos_firmado_juridico_id actualizado con ID {drive_record.id}")

            self.env.cr.commit()
            _logger.info(f"Registro guardado con file_url: {file_link}")

            self.notificacion_contrato_firmado_juridico()
            _logger.info(f"Notificación enviada para contratos_firmado_juridico_id: {drive_record.id}")

            self.contratos_firmado_juridico_doc_visible = False

            _logger.info("Campos de archivo limpiados correctamente tras la subida.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal eliminado.")

    def notificacion_contrato_firmado_juridico(self):
        _logger.info(">>> Iniciando envío de notificación de contrato jurídico firmado (ID: %s)", self.id)

        # Preparar enlace directo de Google Drive para el correo.
        # No se descarga ni se adjunta copia local: la copia vive en Google Drive.
        attachment_ids = []
        drive_link = ""
        if self.contratos_firmado_juridico_id:
            try:
                drive_record = self.contratos_firmado_juridico_id
                target_id = drive_record.file_id or drive_record.drive_file_id
                if target_id:
                    drive_link = f"https://drive.google.com/file/d/{target_id}/view"
                    attachment_id = self._crear_adjunto_temporal_drive_para_correo(
                        drive_record,
                        nombre_default="Otrosi firmado.pdf",
                    )
                    if attachment_id:
                        attachment_ids.append(attachment_id)
                    _logger.info(">>> Enlace de Drive preparado para correo: %s", drive_link)
                else:
                    _logger.warning(">>> No se encontró file_id en el registro de Drive (ID: %s)", self.contratos_firmado_juridico_id.id)
            except Exception as e:
                _logger.warning(">>> No se pudo preparar el enlace del documento en Drive: %s", str(e))

        # Lista de destinatarios
        destinatarios = []

        if self.contratista_proveedor_relacionado and self.contratista_proveedor_relacionado.correo_facturacion:
            destinatarios.append(self.contratista_proveedor_relacionado.correo_facturacion)
            _logger.info(">>> Destinatario (Contratista): %s", self.contratista_proveedor_relacionado.correo_facturacion)

        COLOR_PRINCIPAL = "#043484"
        labels_doc = self._get_labels_correo_documento_otrosi()
        nombre_contratista = self._get_nombre_contratista_correo()
        subject = self._get_asunto_correo_con_contratista(labels_doc['firmado_asunto'])
        proyecto_nombre = self.proyecto_relacionado.new_name if self.proyecto_relacionado else 'N/A'
        adjunto_info = (
            f"<p>Encontrarán la copia del {labels_doc['nombre']} firmado adjunta a este correo.</p>"
            if attachment_ids
            else f'<p>{labels_doc["firmado_disponible"]} <a href="{drive_link}">Ver documento</a></p>'
            if drive_link
            else f"<p>{labels_doc['firmado_disponible']}</p>"
        )
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📄 {labels_doc['firmado_titulo']}</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Cordial saludo,</p>

                    <p>{labels_doc['firmado_intro']}</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <p style="margin: 4px 0;"><strong>Contratista:</strong> {nombre_contratista}</p>
                        <p style="margin: 4px 0;"><strong>Proyecto:</strong> {proyecto_nombre}</p>
                    </div>

                    {adjunto_info}

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático del Repositorio, por favor no responder.
                    </p>
                </div>
            </div>
        """

        mail_values = {
            'subject': subject,
            'body_html': body,
            'email_to': ','.join(destinatarios),
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
            'attachment_ids': [(6, 0, attachment_ids)],
        }

        try:
            _logger.info(">>> Enviando notificación a %s destinatarios...", len(destinatarios))
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.sudo().send()
            if attachment_ids:
                self.env["ir.attachment"].sudo().browse(attachment_ids).unlink()
            _logger.info(">>> ✅ NOTIFICACIÓN ENVIADA CON ÉXITO")
        except Exception as e:
            _logger.error(">>> ❌ ERROR enviando notificación: %s", str(e))

    aprueba_f_l_contratos_otro_juridico = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_contratos_otro_juridico = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    contratos_juridico_otro = fields.Boolean(default=True)
    
    contratos_otro_jurdico_doc = fields.Binary(tracking=True)
    contratos_otro_doc_juridico_fname = fields.Char(tracking=True)


    contratos_otro_juridico_id = fields.Many2one('drive', string='Contratos Otro', tracking=True, domain="[('stage5_id', '=', drive_file_id)]")

    contratos_otro_juridico_doc_visible = fields.Boolean(default=True)

    def action_upload_contratos_otro_juridico_doc(self):
        """
        Abre la ventana emergente para subir el documento Contratos Otro (Jurídico).
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contratos Otrosí', # Nombre de la ventana
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculamos al registro de OTRO SI
                'default_otrosi_documentos_id': self.id,
                
                # ID de carpeta Odoo
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de carpeta Google Drive
                'default_folder_id': google_folder_id, 
                'default_otrosi_bolean': True,
                
                # CLAVE ÚNICA para este documento
                'tipo_documento_upload': 'contratos_otro_juridico' 
            },
        }

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_contratos_otro_juridico = {}

    def abrir_documento_contratos_otro_juridico_pdf(self):
        self.contratos_otro_jurdico_doc = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_contratos_otro_juridico_en_drive()
        # Primero, verificamos si 'contratos_otro_juridico_id' está configurado
        if self.contratos_otro_juridico_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Contrato otrosí',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.contratos_otro_juridico_id.id,
                'target': 'new',
                
            }

        # Si 'contratos_otro_doc_juridico_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.contratos_otro_doc_juridico_fname:
            file_name_to_search = self.contratos_otro_doc_juridico_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_contratos_otro_juridico:
                drive_document = self._cache_documentos_contratos_otro_juridico[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_contratos_otro_juridico[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'contratos_otro_juridico_id' con el ID del documento encontrado
                self.write({'contratos_otro_juridico_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Contrato otrosí',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
                }

            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Cert.afiliación examen preocupacional con este registro.')

    contratos_otro_juridico_actualizar_visible = fields.Boolean(default=False, related='contratos_otro_juridico_id.actualizar_visible', readonly=False)

    def actualizar_correo_contratos_otro_juridico_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.contratos_otro_juridico_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.contratos_otro_juridico_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.contratos_otro_juridico_actualizar_visible,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def subir_contratos_otro_juridico_doc_drive(self):
        """Sube el documento 'Otrosí' a Google Drive y lo registra en el modelo drive."""
        if self.contratos_otro_jurdico_doc and self.contratos_otro_doc_juridico_fname:
            _logger.info(f"Subiendo documento '{self.contratos_otro_doc_juridico_fname}' a Google Drive...")

            result = self._upload_contratos_otro_juridico_doc_drive_pn()

            if not result:
                raise UserError("❌ Error al subir el Otrosí a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ Otrosí subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'Otrosí' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_contratos_otro_juridico_doc_drive_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""

        self.ensure_one()

        if not self.contratos_otro_jurdico_doc or not self.contratos_otro_doc_juridico_fname:
            _logger.error("No hay archivo para subir a Google Drive.")
            raise UserError("No hay archivo para subir a Google Drive.")

        if not self.drive_file_id:
            _logger.error("No se encontró un drive_file_id. No se puede subir el archivo.")
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        # Buscar carpeta asociada
        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("No se encontró el registro en Drive. Verifique el archivo de Drive.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta válida en Drive para el archivo ID {self.drive_file_id.id}.")
            raise UserError("El archivo de Drive no tiene una carpeta válida.")

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            file_content = base64.b64decode(self.contratos_otro_jurdico_doc)
            original_extension = os.path.splitext(self.contratos_otro_doc_juridico_fname)[1] or ".pdf"
            fixed_file_name = f"Contrato_de_{self.name}_{uuid.uuid4()}{original_extension}"

            # Validar duplicado en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            # Guardar en archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a la carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida del archivo a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido correctamente con ID: {file_id} y URL: {file_link}")

            # Crear registro en el modelo drive
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.contratos_otro_jurdico_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(f"Registro en drive creado con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            if not self.contratos_otro_juridico_id:
                self.contratos_otro_juridico_id = drive_record.id
                _logger.info(f"Campo contratos_otro_juridico_id actualizado con ID {drive_record.id}")

            self.notificacion_contratos_juridico_doc()

            self.contratos_otro_juridico_doc_visible = False

            _logger.info("Campo contratos_otro_jurdico_doc ocultado y limpiado.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal eliminado.")

    def notificacion_contratos_juridico_doc(self):
        _logger.info(">>> DISPARANDO NOTIFICACIÓN JURÍDICO DOC: Se subió un contrato (ID: %s)", self.id)

        if self.usa_flujo_otrosi_nuevo:
            self.message_post(
                body=(
                    "<p><strong>Documento jurídico del otrosí cargado.</strong> "
                    "Jurídico puede continuar con el envío al estado Firma.</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )
            return False

        # Para modificatorio PJ: solo actuar si el archivo fue realmente subido
        if self.otro_si_modificatorio and not self.contratos_otro_juridico_id:
            _logger.warning(">>> Modificatorio PJ sin archivo subido, se omite la notificación.")
            return

        # 1. Verificar que el proyecto relacionado y su creador existan
        if not self.proyecto_relacionado or not self.proyecto_relacionado.create_uid:
            _logger.warning(">>> No se envió correo: Falta proyecto relacionado o creador.")
            raise UserError("No se ha definido un proyecto relacionado o el proyecto no tiene un creador asignado.")

        # 2. Obtener la URL base del sistema y configurar enlace
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        record_id = self._origin.id if self._origin else self.id
        record_url = f"{base_url}/web#id={record_id}&view_type=form&model={self._name}"

        # 3. Validar destinatarios (Creador + Asistentes de la lista con acceso)
        emails = []
        
        # Correo del Creador
        if self.proyecto_relacionado.create_uid.email:
            emails.append(self.proyecto_relacionado.create_uid.email)


        # Buscar los IDs de los grupos (Asistente y Coordinador)
        group_asis = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
        group_coord = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)

        # Recorrer la lista users_with_access y filtrar por cualquiera de los dos grupos
        for usuario in self.proyecto_relacionado.users_with_access:
            if usuario.email:
                # Comprobar si el usuario está en el grupo asistente o en el coordinador
                is_asistente = group_asis and group_asis in usuario.groups_id
                is_coordinador = group_coord and group_coord in usuario.groups_id
                
                if is_asistente or is_coordinador:
                    if usuario.email not in emails:  # Evitar duplicados
                        emails.append(usuario.email)

        if not emails:
            _logger.warning(">>> No se envió correo: No se encontraron destinatarios con email.")
            raise UserError("No se encontraron correos electrónicos válidos (Creador, Coordinadores o Asistentes con acceso).")

        email_to = ','.join(emails)

        # 4. Construir Correo
        COLOR_PRINCIPAL = "#043484"
        labels_doc = self._get_labels_correo_documento_otrosi()
        nombre_contratista = self._get_nombre_contratista_correo()
        subject = self._get_asunto_correo_con_contratista(labels_doc['subido_asunto'])
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📋 {labels_doc['subido_titulo']}</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Estimados,</p>

                    <p>{labels_doc['subido_mensaje']}</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <p style="margin: 4px 0;"><strong>{labels_doc['nombre_label']}</strong> {labels_doc['nombre_documento']}</p>
                        <p style="margin: 4px 0;"><strong>Contratista:</strong> {nombre_contratista}</p>
                        <p style="margin: 4px 0;"><strong>Proyecto:</strong> {self.proyecto_relacionado.new_name}</p>
                    </div>

                    <p style="text-align: center; margin-top: 30px;">
                        <a href="{record_url}"
                           style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                            ➡ {labels_doc['boton_ver']}
                        </a>
                    </p>

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático del Repositorio, por favor no responder.
                    </p>
                </div>
            </div>
        """

        # 5. Definir valores y enviar
        mail_values = {
            'subject': subject,
            'body_html': body,
            'email_to': email_to,
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
        }

        _logger.info(">>> Creando correo para: %s", email_to)
        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()
        _logger.info(">>> ¡NOTIFICACIÓN JURÍDICO DOC ENVIADA EXITOSAMENTE!")

        if self.reinicio or self.suspension or self.acta_de_prorroga_de_la_suspension:
            self.write({'estado_contratacion': 'coordinador'})
            return self._registrar_envio_coordinador_para_firma()
        elif self.otro_si_modificatorio or self.adicion or self.prorroga or self.adicion_prorroga:
            self.write({'estado_contratacion': 'coordinador'})
            return self._registrar_envio_coordinador_para_firma()


    contrato_juridico_doc_visible = fields.Boolean(default=True)

    propuesta_juridico_doc_visible = fields.Boolean(default=True)


    camara_de_comercio_juridico_doc_visible = fields.Boolean(default=True)

    rut_juridico_doc_visible = fields.Boolean(default=True)


    cert_contraloria_juridico_doc_visible = fields.Boolean(default=True)

    cert_procuraduria_juridico_doc_visible = fields.Boolean(default=True)



    certificados_de_contraloria_rep_legal_juridico_doc_visible = fields.Boolean(default=True)


    certificados_de_Procuraduria_rep_legal_juridico_doc_visible = fields.Boolean(default=True)


    certificados_de_policia_judicial_juridico_doc_visible = fields.Boolean(default=True)


    certificado_de_inhabilidades_rep_juridico_doc_visible = fields.Boolean(default=True)


    certificado_de_pago_parafiscales_juridico_doc_visible = fields.Boolean(default=True)


    planilla_de_seguridad_social_juridico_doc_visible = fields.Boolean(default=True)



    certif_de_junta_de_contadores_juridico_doc_visible = fields.Boolean(default=True)



    cedula_de_revisor_juridico_doc_visible = fields.Boolean(default=True)


    tarjeta_juridico_doc_visible = fields.Boolean(default=True)



    aprueba_j_h_otro_doc_visible = fields.Boolean(default=True)


    su_contrato_natu_doc_visible = fields.Boolean(default=True)


    su_rut_doc_visible = fields.Boolean(default=True)


    su_salud_doc_visible = fields.Boolean(default=True)


    su_pension_doc_visible = fields.Boolean(default=True)



#########################################################################################################

    def _otrosi_estado_final_bloqueado(self):
        """Indica si el otrosí ya llegó a un estado final y no debe retroceder."""
        self.ensure_one()
        estados_finales = {
            'contratado',
            'suspendido',
            'adicion_formalizada',
            'prorroga_formalizada',
            'adicion_prorroga_formalizada',
            'modificatorio_formalizado',
            'reinicio_formalizado',
            'suspension_prorrogada',
        }
        return (
            self.estado_contratacion in estados_finales
            or self.contratado_fin
            or self.contratado_fin_j
        )

    def _validar_no_editar_otrosi_finalizado(self, vals):
        """Evita que acciones de flujo muevan un otrosí que ya finalizó."""
        if self.env.context.get('skip_validacion_estado_otrosi_nuevo'):
            return

        nuevo_estado = vals.get('estado_contratacion')
        if not nuevo_estado:
            return

        for record in self:
            if not record._otrosi_estado_final_bloqueado():
                continue
            if nuevo_estado == record.estado_contratacion:
                continue
            raise UserError(
                "Este otrosí ya está finalizado y no puede cambiar de estado."
            )

    def write(self, vals):
        """Sobrescribe el método 'write' para actualizar los campos relacionados con Google Drive."""
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            return True

        self._validar_no_editar_otrosi_finalizado(vals)

        helper_validacion = getattr(
            self, '_requiere_validacion_edicion_otrosi_nuevo', None
        )
        validar_edicion = (
            helper_validacion(vals) if helper_validacion else True
        )

        campos_tipo_otrosi = {
            'otro_si_modificatorio',
            'adicion',
            'prorroga',
            'adicion_prorroga',
            'reinicio',
            'suspension',
            'acta_de_prorroga_de_la_suspension',
            'tipo_modificacion_modificatorio',
        }
        campos_bloqueados = (
            campos_tipo_otrosi.intersection(vals) if validar_edicion else set()
        )
        if campos_bloqueados:
            for record in self:
                for campo in campos_bloqueados:
                    if record[campo] != vals.get(campo):
                        raise UserError(
                            "No se pueden cambiar las opciones del otrosí después de crear la solicitud."
                        )

        previous_juridico_users_by_record = {}
        if 'usuarios_juridico_ids' in vals:
            previous_juridico_users_by_record = {
                record.id: set(record.usuarios_juridico_ids.ids)
                for record in self
            }

        res = super(OtroSi, self).write(vals)

        #self._log_diagnostico_adicion_prorroga_viejo('write', vals)

        if vals.get('estado_contratacion') == 'contratado':
            self._marcar_contratacion_padre_como_suspendida()
            #self._marcar_contratacion_padre_como_reiniciada()
            
        if 'usuarios_juridico_ids' in vals:
            for record in self:
                if not record.drive_file_id:
                    record.action_link_drive_file()
                if record.drive_file_id:
                    record._share_file_with_new_users_juridico(
                        previous_user_ids=previous_juridico_users_by_record.get(record.id, set()),
                        notificar=not record.usa_flujo_otrosi_nuevo,
                    )
                else:
                    _logger.warning("No se estableció un archivo relacionado; no se compartirán usuarios jurídicos.")

        if 'valor_total_contrato' in vals:
            for record in self:
                if record.contratacion_fuv_en_id:
                    record.contratacion_fuv_en_id.update_valor_otrosi()
        if self.select_persona in ['persona_natural', 'contrato_laboral']:
            if 'su_arl_doc' in vals:
                self.subir_su_arl_a_drive()
            elif 'propuesta_doc' in vals:
                self.subir_propuesta_doc_drive()
            elif 'su_cdp_doc' in vals:
                self.subir_su_cdp_doc_a_drive()
            elif 'contratos_otro_doc' in vals:
                self.subir_contratos_otro_doc_drive()
            elif 'contratos_firmado_natu_doc' in vals:
                self.subir_contratos_firmado_natu_doc_drive()
        # -------------------Sección para documentos P/J-------------------------
        elif self.select_persona == 'persona_juridica':
             if 'cdp_juridico_doc' in vals:
                self.subir_cdp_juridico_doc_a_drive()
             elif 'propuesta_doc' in vals:
                self.subir_propuesta_doc_drive()
             elif 'contratos_firmado_juridico_doc' in vals:
                self.subir_contratos_firmado_juridico_doc_drive()
             elif 'contratos_otro_jurdico_doc' in vals:
                self.subir_contratos_otro_juridico_doc_drive()
             elif 'polizas' in vals:
                self.subir_polizas_a_drive()
        return res


############################################################################################################

    
    su_cdp_doc_fname = fields.Char(tracking=True)
    cdp_doc_fname = fields.Char(string="CDP Document Name")
    su_cdp_doc = fields.Binary(tracking=True)
    su_cdp_id = fields.Many2one('drive', string='RPC', readonly=True)
    su_cdp_doc_visible = fields.Boolean(default=True)

    def action_upload_su_cdp_doc(self):
        """
        Abre la ventana emergente para subir el documento RPC (su_cdp).
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC', # Nombre de la ventana
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculamos al registro de OTRO SI
                'default_otrosi_documentos_id': self.id,
                
                # ID de carpeta Odoo
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de carpeta Google Drive
                'default_folder_id': google_folder_id, 
                'default_otrosi_bolean': True,
                
                # CLAVE ÚNICA para este documento
                'tipo_documento_upload': 'su_cdp' 
            },
        }

    def subir_su_cdp_doc_a_drive(self):
        """Sube el documento 'RPC' a Google Drive y lo registra en el modelo drive."""
        if self.su_cdp_doc and self.su_cdp_doc_fname:
            _logger.info(f"Subiendo documento '{self.su_cdp_doc_fname}' a Google Drive...")

            result = self._upload_su_cdp_doc_to_drive_pn()

            if not result:
                raise UserError("❌ Error al subir el RPC a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ RPC subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'RPC' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_su_cdp_doc_to_drive_pn(self):
        """Sube el archivo binario su_cdp_doc a Google Drive y lo registra en el modelo 'drive'."""
        self.ensure_one()

        if not self.su_cdp_doc or not self.su_cdp_doc_fname:
            _logger.error("No hay archivo para subir a Google Drive.")
            raise UserError("Debe adjuntar el archivo antes de subirlo a Google Drive.")

        if not self.drive_file_id or isinstance(self.drive_file_id, str):
            _logger.error(f"drive_file_id inválido o ausente: {self.drive_file_id}")
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("Archivo de Drive no encontrado. No se puede continuar con la subida.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró carpeta válida en Drive para el archivo {self.drive_file_id.id}.")
            raise UserError("El archivo de Drive no tiene una carpeta válida asociada.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        temp_file_path = None

        try:
            file_content = base64.b64decode(self.su_cdp_doc)
            original_extension = os.path.splitext(self.su_cdp_doc_fname)[1] or ".pdf"
            fixed_file_name = f"RPC_{uuid.uuid4()}{original_extension}"

            # Validación de duplicados en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido exitosamente. ID: {file_id}, URL: {file_link}")

            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.su_cdp_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en 'drive' con ID {drive_record.id} para archivo '{fixed_file_name}'")

            if not self.su_cdp_id:
                self.su_cdp_id = drive_record.id
                _logger.info(f"Campo su_cdp_id actualizado con ID {drive_record.id}")

            self.su_cdp_doc_visible = False
            self.su_cdp_doc = False
            _logger.info("Campo su_cdp_doc eliminado y ocultado del formulario.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo su_cdp_doc a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal de su_cdp_doc eliminado correctamente.")


    su_arl_doc_visible = fields.Boolean(default=True)
    su_arl_doc_id = fields.Many2one('drive', string='certificado de ARL', tracking=True)

    def action_upload_su_arl_doc(self):
        """
        Abre la ventana emergente para subir el documento ARL.
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Se utiliza la misma lógica del ejemplo para determinar la carpeta de destino.
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            # Cambiamos el nombre para que refleje el tipo de documento
            'name': 'Subir documento certificado de ARL', 
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculación del documento subido al registro actual (self.id)
                'default_otrosi_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo (stage5_id)
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de Google Drive de la carpeta de destino
                'default_folder_id': google_folder_id, 
                'default_otrosi_bolean': True,
                
                # CLAVE DE IDENTIFICACIÓN: Especifica que el archivo a subir es el documento ARL.
                # Este valor será usado por el modelo 'drive' para actualizar el campo su_arl_doc_id
                # en el registro principal (self).
                'tipo_documento_upload': 'documento_arl' 
            },
        }






    def subir_su_arl_a_drive(self):
        """Sube el documento 'ARL' a Google Drive y lo registra en el modelo drive."""
        if self.su_arl_doc and self.su_arl_doc_fname:
            _logger.info(f"Subiendo documento '{self.su_arl_doc_fname}' a Google Drive...")

            result = self._upload_su_arl_to_drive_pn()

            if not result:
                raise UserError("❌ Error al subir el ARL a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ ARL subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'ARL' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_su_arl_to_drive_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        self.ensure_one()

        if not self.su_arl_doc or not self.su_arl_doc_fname:
            _logger.error("No hay archivo para subir a Google Drive.")
            raise UserError("No hay archivo para subir a Google Drive.")

        if not self.drive_file_id:
            _logger.error("No se encontró un drive_file_id. No se puede subir el archivo.")
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("No se encontró el registro en Drive. Verifique el archivo de Drive.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta válida en Drive para el archivo ID {self.drive_file_id.id}.")
            raise UserError("El archivo de Drive no tiene una carpeta válida.")

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            file_content = base64.b64decode(self.su_arl_doc)
            original_extension = os.path.splitext(self.su_arl_doc_fname)[1] or ".pdf"
            fixed_file_name = f"Certificado de ARL_{uuid.uuid4()}{original_extension}"

            # Validación de duplicados en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a la carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida del archivo a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido correctamente con ID: {file_id} y URL: {file_link}")

            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.su_arl_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(f"Registro en drive creado con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            if not self.su_arl_doc_id:
                self.su_arl_doc_id = drive_record.id
                _logger.info(f"Campo su_arl_doc_id actualizado con ID {drive_record.id}")

            self.su_arl_doc_visible = False

            _logger.info("Campos su_arl_doc_visible y su_arl_doc limpiados.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal eliminado.")

    
    contratos_otro_doc = fields.Binary(tracking=True)
    contratos_otro_doc_fname = fields.Char(tracking=True)

    contratos_otro_id = fields.Many2one('drive', string='Contratos OtroSí', tracking=True)
    contratos_otro_doc_visible = fields.Boolean(default=True)

    def action_upload_contratos_otro_doc(self):
        """
        Abre la ventana emergente para subir el documento Contratos Otro.
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contrato otrosí', # Nombre de la ventana
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculamos al registro de OTRO SI
                'default_otrosi_documentos_id': self.id,
                
                # ID de carpeta Odoo
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de carpeta Google Drive
                'default_folder_id': google_folder_id, 
                'default_otrosi_bolean': True,
                
                # CLAVE ÚNICA para este documento
                'tipo_documento_upload': 'contratos_otro' 
            },
        }


    def subir_contratos_otro_doc_drive(self):
        """Sube el documento 'Otrosí' a Google Drive y lo registra en el modelo drive."""
        if self.contratos_otro_doc and self.contratos_otro_doc_fname:
            _logger.info(f"Subiendo documento '{self.contratos_otro_doc_fname}' a Google Drive...")

            result = self._upload_contratos_otro_doc_drive_pn()

            if not result:
                raise UserError("❌ Error al subir el Otrosí a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ Otrosí subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'Otrosí' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_contratos_otro_doc_drive_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""

        self.ensure_one()

        if not self.contratos_otro_doc or not self.contratos_otro_doc_fname:
            _logger.error("No hay archivo para subir a Google Drive.")
            raise UserError("No hay archivo para subir a Google Drive.")

        if not self.drive_file_id:
            _logger.error("No se encontró un drive_file_id. No se puede subir el archivo.")
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        # Buscar carpeta asociada
        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("No se encontró el registro en Drive. Verifique el archivo de Drive.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta válida en Drive para el archivo ID {self.drive_file_id.id}.")
            raise UserError("El archivo de Drive no tiene una carpeta válida.")

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            file_content = base64.b64decode(self.contratos_otro_doc)
            original_extension = os.path.splitext(self.contratos_otro_doc_fname)[1] or ".pdf"
            fixed_file_name = f"Contrato_de_{self.name}_{uuid.uuid4()}{original_extension}"

            # Validación de duplicado en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            # Guardar en archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a la carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida del archivo a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido correctamente con ID: {file_id} y URL: {file_link}")

            # Crear registro en el modelo drive
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.contratos_otro_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(f"Registro en drive creado con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            if not self.contratos_otro_id:
                self.contratos_otro_id = drive_record.id
                _logger.info(f"Campo contratos_otro_id actualizado con ID {drive_record.id}")

            self.notificacion_contratos_doc()

            self.contratos_otro_doc_visible = False

            _logger.info("Campos contratos_otro_doc_visible y contratos_otro_doc limpiados.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal eliminado.")

    def notificacion_contratos_doc(self):
        _logger.info(">>> DISPARANDO NOTIFICACIÓN CONTRATOS DOC: Se subió un contrato (ID: %s)", self.id)

        if self.usa_flujo_otrosi_nuevo:
            self.message_post(
                body=(
                    "<p><strong>Documento jurídico del otrosí cargado.</strong> "
                    "Jurídico puede continuar con el envío al estado Firma.</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )
            return False

        # Para modificatorio: solo actuar si el archivo fue realmente subido
        if self.otro_si_modificatorio and not self.contratos_otro_id:
            _logger.warning(">>> Modificatorio sin archivo subido, se omite la notificación.")
            return

        # 1. Verificar que el proyecto relacionado y su creador existan
        if not self.proyecto_relacionado or not self.proyecto_relacionado.create_uid:
            _logger.warning(">>> No se envió correo: Falta proyecto relacionado o creador.")
            raise UserError("No se ha definido un proyecto relacionado o el proyecto no tiene un creador asignado.")

        # 2. Configurar enlace del registro
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        record_id = self._origin.id if self._origin else self.id
        record_url = f"{base_url}/web#id={record_id}&view_type=form&model={self._name}"

        # 3. Recolectar destinatarios (Creador + Asistentes de la lista users_with_access)
        emails = []
        
        # Agregar al Creador del proyecto
        if self.proyecto_relacionado.create_uid.email:
            emails.append(self.proyecto_relacionado.create_uid.email)


        # Buscar los IDs de los grupos (Asistente y Coordinador)
        group_asis = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
        group_coord = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)

        # Recorrer la lista users_with_access y filtrar por cualquiera de los dos grupos
        for usuario in self.proyecto_relacionado.users_with_access:
            if usuario.email:
                # Comprobar si el usuario está en el grupo asistente o en el coordinador
                is_asistente = group_asis and group_asis in usuario.groups_id
                is_coordinador = group_coord and group_coord in usuario.groups_id
                
                if is_asistente or is_coordinador:
                    if usuario.email not in emails:  # Evitar duplicados
                        emails.append(usuario.email)

        if not emails:
            _logger.warning(">>> No se envió correo: No se encontraron destinatarios con email.")
            raise UserError("No se encontraron correos electrónicos válidos (Creador, Coordinadores o Asistentes con acceso).")

        email_to = ','.join(emails)
        
        # 4. Construir cuerpo del correo
        COLOR_PRINCIPAL = "#043484"
        labels_doc = self._get_labels_correo_documento_otrosi()
        nombre_contratista = self._get_nombre_contratista_correo()
        subject = self._get_asunto_correo_con_contratista(labels_doc['subido_asunto'])
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📋 {labels_doc['subido_titulo']}</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Estimados,</p>

                    <p>{labels_doc['subido_mensaje']}</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <p style="margin: 4px 0;"><strong>{labels_doc['nombre_label']}</strong> {labels_doc['nombre_documento']}</p>
                        <p style="margin: 4px 0;"><strong>Contratista:</strong> {nombre_contratista}</p>
                        <p style="margin: 4px 0;"><strong>Proyecto:</strong> {self.proyecto_relacionado.new_name}</p>
                    </div>

                    <p style="text-align: center; margin-top: 30px;">
                        <a href="{record_url}"
                           style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                            ➡ Ir a ver {labels_doc['nombre']}
                        </a>
                    </p>

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático del Repositorio, por favor no responder.
                    </p>
                </div>
            </div>
        """

        # 5. Definir valores y enviar correo
        mail_values = {
            'subject': subject,
            'body_html': body,
            'email_to': email_to,
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
        }

        _logger.info(">>> Enviando notificación a: %s", email_to)
        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()
        _logger.info(">>> ¡NOTIFICACIÓN CONTRATOS DOC ENVIADA EXITOSAMENTE!")

        if self.reinicio or self.suspension or self.acta_de_prorroga_de_la_suspension:
            self.write({'estado_contratacion': 'coordinador'})
            return self._registrar_envio_coordinador_para_firma()
        elif self.otro_si_modificatorio or self.adicion or self.prorroga or self.adicion_prorroga:
            self.write({'estado_contratacion': 'coordinador'})
            return self._registrar_envio_coordinador_para_firma()

    contratos_firmado_natu_doc_fname = fields.Char(tracking=True)
    
    contratos_firmado_natu = fields.Boolean('Contrato', tracking=True)
    contratos_firmado_natu_doc = fields.Binary(tracking=True)
    contratos_firmado_id = fields.Many2one('drive', string='Contrato Firmado', readonly=True)
    contratos_firmado_natu_doc_visible = fields.Boolean(default=True)

    def action_upload_contratos_firmado_natu_doc(self):
        """
        Abre la ventana emergente para subir el documento Contrato Firmado.
        """
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive
        google_folder_id = self.drive_file_id.folder_id if self.drive_file_id and self.drive_file_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contrato otrosí firmado', # Nombre de la ventana
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                # Vinculamos al registro de OTRO SI
                'default_otrosi_documentos_id': self.id,
                
                # ID de carpeta Odoo
                'default_stage5_id': self.drive_file_id.id, 
                
                # ID de carpeta Google Drive
                'default_folder_id': google_folder_id, 
                'default_otrosi_bolean': True,
                
                # CLAVE ÚNICA para este documento
                'tipo_documento_upload': 'contratos_firmado_natu' 
            },
        }

    def subir_contratos_firmado_natu_doc_drive(self):
        """Sube el documento 'Contratos firmado' a Google Drive y lo registra en el modelo drive."""
        if self.contratos_firmado_natu_doc and self.contratos_firmado_natu_doc_fname:
            _logger.info(f"Subiendo documento '{self.contratos_firmado_natu_doc_fname}' a Google Drive...")

            result = self._upload_contratos_firmado_natu_doc_drive_pn()

            if not result:
                raise UserError("❌ Error al subir la Contratos firmado a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ Contratos firmado subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'Contratos firmado' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_contratos_firmado_natu_doc_drive_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        self.ensure_one()

        if not self.contratos_firmado_natu_doc or not self.contratos_firmado_natu_doc_fname:
            _logger.error("No hay archivo para subir a Google Drive.")
            raise UserError("No hay archivo para subir a Google Drive.")

        if not self.drive_file_id:
            _logger.error("No se encontró un drive_file_id. No se puede subir el archivo.")
            raise UserError("Debe seleccionar un archivo de Drive antes de subir.")

        drive_file_record = self.env['otrosi_drive'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en otrosi_drive con ID {self.drive_file_id.id}.")
            raise UserError("No se encontró el registro en Drive. Verifique el archivo de Drive.")

        carpeta_drive = drive_file_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta válida en Drive para el archivo ID {self.drive_file_id.id}.")
            raise UserError("El archivo de Drive no tiene una carpeta válida.")

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            file_content = base64.b64decode(self.contratos_firmado_natu_doc)
            original_extension = os.path.splitext(self.contratos_firmado_natu_doc_fname)[1] or ".pdf"
            fixed_file_name = f"Contrato_firmado_de_{self.name}_{uuid.uuid4()}{original_extension}"

            # Validación de duplicados en Google Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a la carpeta '{carpeta_drive}' en Google Drive...")

            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive.get('id')
            if not file_id:
                _logger.error("La subida del archivo a Google Drive no devolvió un ID válido.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido correctamente con ID: {file_id} y URL: {file_link}")

            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.contratos_firmado_natu_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage5_id': self.drive_file_id.id
            })

            _logger.info(f"Registro creado en 'drive' con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            if not self.contratos_firmado_id:
                self.contratos_firmado_id = drive_record.id
                _logger.info(f"Campo contratos_firmado_id actualizado con ID {drive_record.id}")

            self.env.cr.commit()
            _logger.info(f"Registro en Odoo guardado con file_url: {file_link}")

            self.notificacion_contrato_firmado()
            _logger.info(f"Notificación enviada para contratos_firmado_id: {drive_record.id}")

            self.contratos_firmado_natu_doc_visible = False

            _logger.info("Campos de archivo limpiados correctamente tras la subida.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            raise UserError(f"Ocurrió un error al subir el archivo a Google Drive: {e}")

        finally:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal eliminado.")

    contratista_proveedor_relacionado = fields.Many2one(
        comodel_name='contratista_proveedor',
        string='Ingresar nombre del contratista para buscar',
        context={'show_search_more': True}, related="contratacion_fuv_en_id.contratista_proveedor_relacionado"
    )


    def notificacion_contrato_firmado(self):
        _logger.info(">>> Iniciando envío de notificación de contrato firmado (ID: %s)", self.id)

        # Preparar enlace directo de Google Drive para el correo.
        # No se descarga ni se adjunta copia local: la copia vive en Google Drive.
        attachment_ids = []
        drive_link = ""
        if self.contratos_firmado_id:
            try:
                drive_record = self.contratos_firmado_id
                target_id = drive_record.file_id or drive_record.drive_file_id
                if target_id:
                    drive_link = f"https://drive.google.com/file/d/{target_id}/view"
                    attachment_id = self._crear_adjunto_temporal_drive_para_correo(
                        drive_record,
                        nombre_default="Otrosi firmado.pdf",
                    )
                    if attachment_id:
                        attachment_ids.append(attachment_id)
                    _logger.info(">>> Enlace de Drive preparado para correo: %s", drive_link)
                else:
                    _logger.warning(">>> No se encontró file_id en el registro de Drive (ID: %s)", self.contratos_firmado_id.id)
            except Exception as e:
                _logger.warning(">>> No se pudo preparar el enlace del documento en Drive: %s", str(e))

        # Lista de destinatarios
        destinatarios = []

        if self.contratista_proveedor_relacionado and self.contratista_proveedor_relacionado.correo_facturacion:
            destinatarios.append(self.contratista_proveedor_relacionado.correo_facturacion)
            _logger.info(">>> Destinatario (Contratista): %s", self.contratista_proveedor_relacionado.correo_facturacion)


        COLOR_PRINCIPAL = "#043484"
        labels_doc = self._get_labels_correo_documento_otrosi()
        nombre_contratista = self._get_nombre_contratista_correo()
        subject = self._get_asunto_correo_con_contratista(labels_doc['firmado_asunto'])
        proyecto_nombre = self.proyecto_relacionado.new_name if self.proyecto_relacionado else 'N/A'
        adjunto_info = (
            f"<p>Encontrarán la copia del {labels_doc['nombre']} firmado adjunta a este correo.</p>"
            if attachment_ids
            else f'<p>{labels_doc["firmado_disponible"]} <a href="{drive_link}">Ver documento</a></p>'
            if drive_link
            else f"<p>{labels_doc['firmado_disponible']}</p>"
        )
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📄 {labels_doc['firmado_titulo']}</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Cordial saludo,</p>

                    <p>{labels_doc['firmado_intro']}</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <p style="margin: 4px 0;"><strong>Contratista:</strong> {nombre_contratista}</p>
                        <p style="margin: 4px 0;"><strong>Proyecto:</strong> {proyecto_nombre}</p>
                    </div>

                    {adjunto_info}

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático del Repositorio, por favor no responder.
                    </p>
                </div>
            </div>
        """

        mail_values = {
            'subject': subject,
            'body_html': body,
            'email_to': ','.join(destinatarios),
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
            'attachment_ids': [(6, 0, attachment_ids)],
        }

        try:
            _logger.info(">>> Enviando notificación a %s destinatarios...", len(destinatarios))
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.sudo().send()
            if attachment_ids:
                self.env["ir.attachment"].sudo().browse(attachment_ids).unlink()
            _logger.info(">>> ✅ NOTIFICACIÓN ENVIADA CON ÉXITO")
        except Exception as e:
            _logger.error(">>> ❌ ERROR enviando notificación: %s", str(e))

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_contratos_natu_firmado = {}

    def abrir_documento_contratos_firmado_natu_pdf(self):
        self.contratos_firmado_natu_doc = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_contratos_firmado_natu_en_drive()

        # Primero, verificamos si 'contratos_firmado_id' está configurado
        if self.contratos_firmado_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Contrato firmado',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.contratos_firmado_id.id,
                'target': 'new',
            }

        # Si 'contratos_firmado_doc_fname' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.contratos_firmado_natu_doc_fname:
            file_name_to_search = self.contratos_firmado_natu_doc_fname.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_contratos_natu_firmado:
                drive_document = self._cache_documentos_contratos_natu_firmado[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_contratos_natu_firmado[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'contratos_firmado_id' con el ID del documento encontrado
                self.write({'contratos_firmado_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Contrato firmado',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún contrato firmado relacionado con este registro.')

    contratos_firmado_natu2_actualizar_visible = fields.Boolean(default=False, related='contratos_firmado_id.actualizar_visible')

    def actualizar_correo_contratos_firmado_natu_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.contratos_firmado_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.contratos_firmado_id.write({
                'correo_electrinico': self.correo,
                'actualizar_visible': self.contratos_firmado_natu2_actualizar_visible,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False


    def cambiar_vista(self):
        # Devolver la acción para abrir el formulario del registro actual
        action = {
            'name': self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'otro_si',
            'view_mode': 'form',
            'res_id': self.id,  # ID del registro actual
            'target': 'current',
        }

        return action


    contraloria_otro_doc_visible = fields.Boolean(default=True)
    procuraduria_otro_doc_visible = fields.Boolean(default=True)
    policia_otro_doc_visible = fields.Boolean(default=True)
    cautelares_otro_doc_visible = fields.Boolean(default=True)
    inhabilidades_otro_doc_visible = fields.Boolean(default=True)

    fecha_actualizada_siempre2 = fields.Date(
        string='Fecha actualizada',
        compute='_compute_fecha_actualizada',
        store=False,
        tracking=True
    )

    fecha_con_formato = fields.Char(
        string='Fecha con formato',
        compute='_compute_fecha_con_formato',
        store=True
    )

    @api.depends('fecha_actualizada_siempre2', 'fecha_iniciacion')
    def _compute_fecha_actualizada(self):
        for record in self:
            record.fecha_actualizada_siempre2 = fields.Date.today()

    def numero_a_palabras_es(self, numero):
        """Convierte un número a palabras en español."""
        unidades = (
            '', 'uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve'
        )
        decenas = (
            '', '', 'veinte', 'treinta', 'cuarenta', 'cincuenta', 'sesenta', 'setenta', 'ochenta', 'noventa'
        )
        especiales = {
            10: 'diez', 11: 'once', 12: 'doce', 13: 'trece', 14: 'catorce', 15: 'quince',
            16: 'dieciséis', 17: 'diecisiete', 18: 'dieciocho', 19: 'diecinueve'
        }

        if numero < 10:
            return unidades[numero]
        elif 10 <= numero < 20:
            return especiales[numero]
        elif 20 <= numero < 100:
            dec = decenas[numero // 10]
            uni = unidades[numero % 10]
            return f"{dec} y {uni}" if uni else dec
        elif numero == 100:
            return "cien"
        else:
            return str(numero)

    def _get_mes_espanol(self, mes_numero):
        """Convierte el número de mes a nombre en español."""
        meses = {
            1: 'enero', 2: 'febrero', 3: 'marzo', 4: 'abril',
            5: 'mayo', 6: 'junio', 7: 'julio', 8: 'agosto',
            9: 'septiembre', 10: 'octubre', 11: 'noviembre', 12: 'diciembre'
        }
        return meses.get(mes_numero, '')

    @api.depends('fecha_actualizada_siempre2', 'fecha_iniciacion')
    def _compute_fecha_con_formato(self):
        for record in self:
            if record.fecha_actualizada_siempre2:
                fecha = record.fecha_actualizada_siempre2

                # Extraer partes de la fecha
                dia = fecha.day
                mes_numero = fecha.month
                anio = fecha.year

                # Obtener el nombre del mes en español
                mes = self._get_mes_espanol(mes_numero)

                # Convertir día y año a texto
                dia_en_letras = self.numero_a_palabras_es(dia).capitalize()
                anio_en_letras = "dos mil veinticinco" if anio == 2025 else self.numero_a_palabras_es(anio)

                # Formatear el texto
                record.fecha_con_formato = (
                    f"a los {dia_en_letras} ({dia}) días del mes de {mes} "
                    f"del año {anio_en_letras}."
                )
            else:
                record.fecha_con_formato = ''

    ###################################################################################################################

    ########################Adición y prorroga######################################################################


    def render_informe_template_natu_adicion_prorroga(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_adicion_prorroga.docx")
        template = DocxTemplate(template_path)


        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.

            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }

        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_otrosi_adicion_prorroga.docx")
        template.save(output_path)
        return output_path
    
    objeto_otrosi_modificatortio = fields.Text(string='Objeto adicional otrosí modificatorio', tracking=True, readonly=False)
    objeto_acta_de_prorroga_de_la_suspension = fields.Text(string='Objeto adicional acta de prórroga de la suspensión', tracking=True, readonly=False)

    def generar_informe_natu_adicion_prorroga_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_natu_adicion_prorroga()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_adicion_prorroga_natu(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_natu_adicion_prorroga()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_natu_adicion_prorroga(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_natu_adicion_prorroga(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado


    ########################Prórroga######################################################################


    def render_informe_template_natu_prorroga(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_prorroga.docx")
        template = DocxTemplate(template_path)

        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_otrosi_prorroga.docx")
        template.save(output_path)
        return output_path
    
    objeto_otrosi_modificatortio = fields.Text(string='Objeto adicional otrosí modificatorio', tracking=True, readonly=False)
    objeto_acta_de_prorroga_de_la_suspension = fields.Text(string='Objeto adicional acta de prórroga de la suspensión', tracking=True, readonly=False)

    def generar_informe_natu_prorroga_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_natu_prorroga()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_prorroga_natu(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_natu_prorroga()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_natu_prorroga(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_natu_prorroga(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado

    objeto_otrosi_modificatortio = fields.Text(string='Objeto adicional otrosí modificatorio', tracking=True, readonly=False)
    objeto_acta_de_prorroga_de_la_suspension = fields.Text(string='Objeto adicional acta de prórroga de la suspensión', tracking=True, readonly=False)
        
########################Adición######################################################################


    def render_informe_template_natu_adicion(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_adicion.docx")
        template = DocxTemplate(template_path)


        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }

        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_otrosi_adicion.docx")
        template.save(output_path)
        return output_path
    


    def generar_informe_natu_adicion_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_natu_suspencion()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_adicion_natu(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_natu_adicion()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_natu(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_natu(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado

    ########################Otrosí modificatorio pj######################################################################


    def render_informe_template_pj_otro_si_modificatorio(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_modificatorio_pj.docx")
        template = DocxTemplate(template_path)


        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        
        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_otrosi_modificatorio_pj.docx")
        template.save(output_path)
        return output_path


    def generar_informe_pj_otro_si_modificatorion_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_pj_otro_si_modificatorio()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_otro_si_modificatorio_pj(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_pj_otro_si_modificatorio()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_pj_otro_si_modificatorio(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_pj_otro_si_modificatorio(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado



    
    ########################Otrosí modificatorio######################################################################


    def render_informe_template_natu_otro_si_modificatorio(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_modificatorio.docx")
        template = DocxTemplate(template_path)


        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        
        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_otrosi_modificatorio.docx")
        template.save(output_path)
        return output_path


    def generar_informe_natu_otro_si_modificatorion_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_natu_otro_si_modificatorio()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_otro_si_modificatorio_natu(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_natu_otro_si_modificatorio()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_natu_otro_si_modificatorio(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_natu_otro_si_modificatorio(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado


 ########################Acta de prórroga de la suspensión######################################################################


    def render_informe_template_pj_acta_de_prorroga_de_la_suspension(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "acta_de_prorroga_de_la_suspension_pj.docx")
        template = DocxTemplate(template_path)


        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        
        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_acta_de_prorroga_de_la_suspension_pj.docx")
        template.save(output_path)
        return output_path

    def generar_informe_pj_acta_de_prorroga_de_la_suspension_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_pj_acta_de_prorroga_de_la_suspension()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_acta_de_prorroga_de_la_suspension_pj(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_pj_acta_de_prorroga_de_la_suspension()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_pj_acta_de_prorroga_de_la_suspension(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_pj_acta_de_prorroga_de_la_suspension(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado

    
########################Acta de prórroga de la suspensión######################################################################


    def render_informe_template_natu_acta_de_prorroga_de_la_suspension(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "acta_de_prorroga_de_la_suspension.docx")
        template = DocxTemplate(template_path)


        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        
        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_acta_de_prorroga_de_la_suspension.docx")
        template.save(output_path)
        return output_path



    def generar_informe_natu_acta_de_prorroga_de_la_suspension_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_natu_acta_de_prorroga_de_la_suspension()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_acta_de_prorroga_de_la_suspension_natu(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_natu_acta_de_prorroga_de_la_suspension()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_natu_acta_de_prorroga_de_la_suspension(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_natu_acta_de_prorroga_de_la_suspension(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado


        ########################Suspencion######################################################################


    def render_informe_template_natu_suspencion(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_suspencion.docx")
        template = DocxTemplate(template_path)


        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if self not in registros_a_procesar:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            identificador_suspension = item.identificador_tipo3
            if not identificador_suspension and item.suspension:
                identificador_suspension = f"SUSPENSIÓN No. {item._get_numero_otrosi_global_para_registro(item)}"

            if identificador_suspension:
                historial_para_word.append({
                    'col_izquierda': identificador_suspension,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        identificador_suspension_actual = self.identificador_tipo3
        if not identificador_suspension_actual and self.suspension:
            identificador_suspension_actual = f"SUSPENSIÓN No. {self._get_numero_otrosi_global_para_registro(self)}"

        context = {
            'docs': _SafeDocsProxy(self, identificador_tipo3=identificador_suspension_actual),
            'historial_tabla': historial_para_word,
        }


        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_acta_de_suspension.docx")
        template.save(output_path)
        return output_path
    
    objeto_otrosi_modificatortio = fields.Text(string='Objeto adicional otrosí modificatorio', tracking=True, readonly=False)
    objeto_acta_de_prorroga_de_la_suspension = fields.Text(string='Objeto adicional acta de prórroga de la suspensión', tracking=True, readonly=False)

    def generar_informe_natu_suspencion_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_natu_suspencion()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_suspencion_natu(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_natu_suspencion()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_natu_suspencion(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_natu_suspencion(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado

    
    ########################Reinicio######################################################################


    def render_informe_template_natu_reinicio(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_reinicio.docx")
        template = DocxTemplate(template_path)

        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_acta_de_reinicio.docx")
        template.save(output_path)
        return output_path

    def generar_informe_natu_reinicio_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_natu_reinicio()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_reinicio_natu(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(
                        f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(
                        f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizar folder_id2 en lugar de folder_id
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_natu_reinicio()

            # Subir el archivo a Google Drive
            drive_record = self.subir_a_google_drive_natu_reinicio(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_natu_reinicio(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado


    
    ########################prórroga y adición pj######################################################################


    def render_informe_template_juridica_prorroga_y_adicion(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_prorroga_y_adicion_juridica.docx")
        template = DocxTemplate(template_path)

        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_juridica_otrosi_prorroga_y_adicion.docx")
        template.save(output_path)
        return output_path
    


    def generar_informe_persona_juridica_prorroga_y_adicion_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_juridica_prorroga_y_adicion()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_prorroga_y_adicion(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_juridica_prorroga_y_adicion()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_prorroga_y_adicion(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_prorroga_y_adicion(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado

    ########################Prorroga pj######################################################################


    def render_informe_template_juridica_prorroga(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_prorroga_juridica.docx")
        template = DocxTemplate(template_path)

        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }


        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_juridica_otrosi_prorroga.docx")
        template.save(output_path)
        return output_path
    
    


    def generar_informe_persona_juridica_prorroga_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_juridica_prorroga()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_prorroga(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_juridica_prorroga()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_prorroga(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_prorroga(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado
    

    ########################Adición pj######################################################################


    def render_informe_template_juridica_adicion(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_adicion_juridica.docx")
        template = DocxTemplate(template_path)

        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }

        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_juridica_otrosi_adicion.docx")
        template.save(output_path)
        return output_path
    


    def generar_informe_persona_juridica_adicion_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_juridica_adicion()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_adicion(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_juridica_adicion()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_adicion(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_adicion(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado
    
   ########################Suspencion pj######################################################################


    def render_informe_template_pj_suspencion(self):
                # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_suspencion_pj.docx")
        template = DocxTemplate(template_path)

        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if self not in registros_a_procesar:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            identificador_suspension = item.identificador_tipo3
            if not identificador_suspension and item.suspension:
                identificador_suspension = f"SUSPENSIÓN No. {item._get_numero_otrosi_global_para_registro(item)}"

            if identificador_suspension:
                historial_para_word.append({
                    'col_izquierda': identificador_suspension,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        identificador_suspension_actual = self.identificador_tipo3
        if not identificador_suspension_actual and self.suspension:
            identificador_suspension_actual = f"SUSPENSIÓN No. {self._get_numero_otrosi_global_para_registro(self)}"

        context = {
            'docs': _SafeDocsProxy(self, identificador_tipo3=identificador_suspension_actual),
            'historial_tabla': historial_para_word,
        }

        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_acta_de_suspensión_pj.docx")
        template.save(output_path)
        return output_path



    def generar_informe_pj_suspencion_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_pj_suspencion()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_suspencion_pj(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name ("Brayan Esteban juridico - Suspensión 1")
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada con éxito: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizamos la carpeta padre
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_pj_suspencion()

            # Subir el archivo a Google Drive (enviamos a Plantilla Otrosí)
            drive_record = self.subir_a_google_drive_pj_suspencion(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_pj_suspencion(self, file_path, folder_id):
        # Autenticación y obtención de la instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre del archivo a partir de su ruta
        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())

        # Intentar encontrar un archivo existente con el mismo nombre en la carpeta
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # Si el archivo existe, actualiza su contenido en lugar de crear uno nuevo
            gfile = file_list[0]  # Tomar el primer archivo que coincida con el nombre
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")

            # Configuramos para subir el nuevo contenido sin crear una nueva versión
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo sin crear una nueva versión
        else:
            # Si no existe, crea un nuevo archivo en la carpeta
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})  # Subir el archivo nuevo

        # Configurar permisos: 'writer' (editor) y 'reader' (lector)
        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})  # Permiso de editor
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})  # Permiso de lector

        # Obtener el registro de la carpeta en `otrosi_drive`
        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        # Leer los datos del archivo y codificarlos en base64
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Crear o actualizar el registro del archivo en el modelo Drive
        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            # Si el archivo ya existe en el modelo, actualiza el registro
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            # Si no existe, crea un nuevo registro
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record  # Retorna el registro creado o actualizado
    


        ########################Reinicio pj######################################################################


    def render_informe_template_pj_reinicio(self):
        # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "otrosi_reinicio_pj.docx")
        template = DocxTemplate(template_path)

        # 1. Buscamos el historial
        if self._origin.id:
            domain = [
                ('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id),
                ('id', '<=', self._origin.id)
            ]
        else:
            domain = [('contratacion_fuv_en_id', '=', self.contratacion_fuv_en_id.id)]
            
        historial = self.search(domain, order='id asc')
        registros_a_procesar = list(historial)
        if not self._origin.id:
            registros_a_procesar.append(self)

        # 2. CREAMOS LA LISTA LIMPIA PARA WORD (ESTRICTAMENTE SEPARADOS)
        historial_para_word = []
        
        for item in registros_a_procesar:
            # Evaluamos uno por uno. Si existe, crea una fila nueva e independiente.
            
            if item.adicion or item.prorroga or item.adicion_prorroga:
                historial_para_word.append({
                    'col_izquierda': f"OTROSÍ No. {self._get_numero_otrosi_global_para_registro(item)}",
                    'col_derecha': item.objeto if item.objeto else ''
                })

            elif item.identificador_tipo:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo,
                    'col_derecha': item.objeto if item.objeto else ''
                })
            
            if item.identificador_tipo2:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo2,
                    'col_derecha': item.objeto_reinicio if item.objeto_reinicio else ''
                })
            
            if item.identificador_tipo3:
                historial_para_word.append({
                    'col_izquierda': item.identificador_tipo3,
                    'col_derecha': item.objeto_suspension if item.objeto_suspension else ''
                })
            
                
            if getattr(item, 'indentificador_tipo5', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo5,
                    'col_derecha': item.objeto_otrosi_modificatortio if item.objeto_otrosi_modificatortio else ''
                })
                
            if getattr(item, 'indentificador_tipo6', False):
                historial_para_word.append({
                    'col_izquierda': item.indentificador_tipo6,
                    'col_derecha': item.objeto_acta_de_prorroga_de_la_suspension if item.objeto_acta_de_prorroga_de_la_suspension else ''
                })

        # 3. Pasamos la lista 'historial_para_word' al contexto de Word
        context = {
            'docs': _SafeDocsProxy(self),
            'historial_tabla': historial_para_word,
        }

        template.render(context)
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_acta_de_reinicio_pj.docx")
        template.save(output_path)
        return output_path


    def generar_informe_pj_reinicio_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto

        template_rendered = self.render_informe_template_pj_reinicio()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        filename_base = self._get_nombre_archivo_otrosi_docx()
        return {
            'name': '{}.docx'.format(filename_base),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, filename_base),
            'target': 'self',
        }

    def vista_previa_informe_reinicio_pj(self):
        # 1. BÚSQUEDA ROBUSTA DE LA CARPETA
        # Primero intentamos buscar por el ID relacional
        drive_file = self.env['drive.file'].search([
            ('id', '=', self.drive_file_id.id)
        ], limit=1)

        # Si el ID está vacío (como pasa en los registros nuevos), lo buscamos POR NOMBRE.
        # Usamos 'ilike' con self.name
        if not drive_file:
            _logger.info(f"El campo drive_file_id está vacío. Buscando carpeta por nombre: {self.name}")
            drive_file = self.env['drive.file'].search([
                ('folder_name', 'ilike', self.name)
            ], limit=1)

        # Si por alguna razón sigue sin encontrarla, usamos la carpeta del contrato principal como salvavidas
        if not drive_file and self.contratacion_fuv_en_id:
            drive_file = self.contratacion_fuv_en_id.drive_file_id

        # 2. CONTINUAMOS CON LA LÓGICA DE PLANTILLA OTROSÍ
        if drive_file:
            _logger.info(f"Carpeta encontrada: {drive_file.folder_name} (ID: {drive_file.id})")

            # Buscar si ya existe la carpeta 'Plantilla Otrosí' en Odoo
            otrosi_drive = self.env['otrosi_drive'].search([
                ('folder_name', '=', "Plantilla Otrosí")
            ], limit=1)

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            folder_exists = False
            if otrosi_drive and otrosi_drive.folder_id:
                try:
                    # Verificamos si la carpeta aún existe en Google Drive
                    folder_check = drive.CreateFile({'id': otrosi_drive.folder_id})
                    folder_check.FetchMetadata()
                    folder_exists = True
                    _logger.info(
                        f"La carpeta 'Plantilla Otrosí' ya existe en Google Drive con ID {otrosi_drive.folder_id}")
                except Exception as e:
                    _logger.warning(
                        f"La carpeta con ID {otrosi_drive.folder_id} no existe en Drive o no se puede acceder: {e}")
                    folder_exists = False

            # Si no existe en Odoo o fue eliminada en Drive, creamos una nueva
            if not otrosi_drive or not folder_exists:
                _logger.info("Creando la carpeta 'Plantilla Otrosí' en Google Drive y sincronizando con Odoo...")

                folder_metadata = {
                    'title': "Plantilla Otrosí",
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_file.folder_id2}]  # Utilizar folder_id2 en lugar de folder_id
                }

                try:
                    # Crear la carpeta en Google Drive
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()

                    if otrosi_drive:
                        # Si el registro existe en Odoo, actualizamos el folder_id
                        otrosi_drive.write({
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Actualizado registro 'otrosi_drive' en Odoo con ID {otrosi_drive.id}")
                    else:
                        # Si no existe en Odoo, lo creamos
                        otrosi_drive = self.env['otrosi_drive'].create({
                            'folder_name': "Plantilla Otrosí",
                            'drive_id': drive_file.id,
                            'folder_guardar_id': folder['id'],
                            'folder_id': folder['id']
                        })
                        _logger.info(f"Creado nuevo registro 'otrosi_drive' con ID {otrosi_drive.id}")

                except Exception as e:
                    _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                    raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")

            else:
                _logger.info("La carpeta 'Plantilla Otrosí' ya existe en Odoo y en Google Drive.")

            # Subir el archivo renderizado a la carpeta de Google Drive
            template_rendered = self.render_informe_template_pj_reinicio()

            # Subir el archivo a Google Drive
            drive_record = self.subir_a_google_drive_pj_reinicio(template_rendered, otrosi_drive.folder_id)

            # Retornar una acción que abra el registro en la vista de Drive
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,  # ID del nuevo registro en Drive
                'view_mode': 'form',
                'target': 'new',  # Abrir en la misma pestaña
            }

        else:
            _logger.warning(f"No se encontró la carpeta en 'drive.file' con el nombre '{self.name}'.")
            raise exceptions.UserError(f"No se encontró la carpeta para el proyecto {self.name}.")

    def subir_a_google_drive_pj_reinicio(self, file_path, folder_id):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        file_name = '{}.docx'.format(self._get_nombre_archivo_otrosi_docx())
        query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            gfile = file_list[0]
            _logger.info(f"Actualizando archivo existente en Google Drive con ID {gfile['id']}")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})
        else:
            gfile = drive.CreateFile({
                'title': file_name,
                'parents': [{'id': folder_id}]
            })
            _logger.info("Subiendo un nuevo archivo a Google Drive")
            gfile.SetContentFile(file_path)
            gfile.Upload(param={'supportsAllDrives': True})

        gfile.InsertPermission({'type': 'anyone', 'role': 'writer'})
        gfile.InsertPermission({'type': 'anyone', 'role': 'reader'})

        otrosi_drive_record = self.env['otrosi_drive'].search([('folder_id', '=', folder_id)], limit=1)

        with open(file_path, 'rb') as f:
            file_data = f.read()

        drive_record = self.env['drive'].search([('drive_file_id', '=', gfile['id'])], limit=1)
        if drive_record:
            drive_record.write({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })
        else:
            drive_record = self.env['drive'].create({
                'file_name': gfile['title'],
                'file_data': base64.b64encode(file_data),
                'file_url': gfile['alternateLink'],
                'drive_file_id': gfile['id'],
                'edit_url': gfile['alternateLink'],
                'folder_id': folder_id,
                'stage5_id': otrosi_drive_record.id if otrosi_drive_record else False,
            })

        return drive_record

    def guardar_documento_adjunto(self, base64_documento):
        # Decodificar la cadena Base64 a datos binarios
        documento_binario = base64.b64decode(base64_documento)

        # Guardar el documento como un adjunto en el registro actual
        self.write({'informe_adjunto': base64_documento})

        return True

    informe_adjunto = fields.Binary(string='Adjunto del informe', readonly=True, tracking=True)

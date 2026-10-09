#!/usr/bin/python
import logging
import re
import time
import uuid
from re import search
from odoo.modules.module import get_module_resource
from attr import exceptions
from googleapiclient.errors import HttpError
from matplotlib.units import registry
# import inflect as inflect
from babel.dates import format_date
from odoo import models, fields, api  # type: ignore
from jinja2 import Environment, FileSystemLoader
from docx import Document  # type: ignore
from docx.api import Document as DocumentApi  # type: ignore
from docxtpl import DocxTemplate  # type: ignore
import os
import base64
from io import BytesIO
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from dateutil.relativedelta import relativedelta
from odoo.modules.registry import Registry
import inflect
import locale
from babel.dates import format_date
from odoo.exceptions import ValidationError  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from num2words import num2words
from werkzeug.urls import url_encode
from werkzeug.utils import secure_filename
import mimetypes
import io
import tempfile
import zipfile
import json
import random
from odoo.exceptions import AccessError  # type: ignore
from .drive import GoogleDriveAuth
import threading
import queue
import logging

_logger = logging.getLogger(__name__)




class NivelAcademico(models.Model):
    _name = 'academico2'
    _description = 'Nivel Académico'

    name = fields.Char(string='Título')


# Documentos que se pueden enviar a firmar electrónicamente (el botón
# "Firma electrónica" solo aparece en estos). Para sumar otro documento
# firmable, agrega aquí el nombre de su campo.
_CAMPOS_FIRMABLES = {
    'contratos_id','contratos_firmado_id'
}


class Contrataciones_Fuv(models.Model):
    _name = 'contrataciones_fuv'
    _description = 'Contrataciones FUV'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    nivel_academico2 = fields.Selection([
        ('bachiller', 'Bachiller'),
        ('tecnico', 'Técnico'),
        ('tecnologo', 'Tecnólogo'),
        ('profesional', 'Profesional'),
        ('especializacion', 'Especialización'),
        ('maestria', 'Maestría'),
        ('doctorado', 'Doctorado'),
    ], string='Nivel académico', tracking=True)

    titulo_id = fields.Many2one('academico2', string='Título académico')
    
    
    def _es_consulta_coordinacion_financiera(self):
        return self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')

    def _marcar_apertura_si_permitido(self):
        # Evita escrituras automáticas cuando el usuario solo tiene rol de consulta.
        if not self._es_consulta_coordinacion_financiera():
            self.write({'write_date': fields.Datetime.now()})

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

    create_uid = fields.Many2one('res.users', string='Creador del proyecto: ', readonly=True, related='proyecto_relacionado.create_uid')
    vistas_ids = fields.One2many(
        'vistas_sql', 
        'contratista_id', 
        string='Consultas Realizadas'
    )
    create_fc = fields.Many2one(
            'res.users', 
            string='Creador del contrato:', 
            default=lambda self: self.env.user,
            readonly=True,
            tracking=True
        )
    #Sección Brayan-Otrosí
    valor_contrato_otrosi = fields.Monetary(
        string='Valor otrosí',
        compute='_compute_valor_contrato_otrosi',
        currency_field='currency_id',
        store=True,
    )

    @api.depends('otrosi_ids.valor_total_contrato')
    def _compute_valor_contrato_otrosi(self):
        """
        SOLUCIÓN AL ACUMULADO DE OTROSÍ

        Antes este campo era un ``related='otrosi_ids.valor_total_contrato'``.
        Como ``otrosi_ids`` es One2many, el related podía devolver solamente uno
        de los valores y no la suma completa. Esto hacía que un contrato con
        varios otrosí mostrara y descontara un acumulado incorrecto.

        La solución es recorrer todos los otrosí relacionados y sumar
        explícitamente ``valor_total_contrato``. Con cero o un otrosí el
        resultado se conserva; con varios, devuelve el acumulado correcto.
        """
        for record in self:
            record.valor_contrato_otrosi = sum(
                record.otrosi_ids.mapped('valor_total_contrato')
            )
    
    
    
    
    
    #No cambiar de tipo de contrato luego de que se haya terminado y este en su fase "Contratado"
    ############################################SAMUEL##################################################################
    no_editable = fields.Boolean(string='Es editable', default=False)

    @api.onchange('no_editable')
    def _onchange_no_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass

    @api.onchange('estado_contratacion')
    def onchange_estado_contratacion(self):
        if self.estado_contratacion == 'contratado':
            self.no_editable = True
        else:
            self.no_editable = False
    ##################################################################nuevo#################################################################################
    Tarjeta_profesional_revisor_fiscal = fields.Boolean('Tarjeta profesional revisor fiscal (Si aplica)', tracking=True)
    Tarjeta_profesional_revisor_fiscal_doc_visible = fields.Boolean(default=True)
    Tarjeta_profesional_revisor_fiscal_doc = fields.Binary(tracking=True, store=True)
    Tarjeta_profesional_revisor_fiscal_doc_fname = fields.Char(store=True)
    Tarjeta_profesional_revisor_fiscal_doc_id = fields.Many2one('drive', string='Tarjeta Profesional Revisor Fiscal (Si aplica)', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_tarjeta_profesional_rf(self):
        self.Tarjeta_profesional_revisor_fiscal = True  
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir tarjeta profesional revisor fiscal',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'tarjeta_profesional_rf' 
            },
        }


    def subir_Tarjeta_profesional_revisor_fiscal_a_drive(self):
        """Sube el documento 'Tarjeta profesional revisor fiscal' a Google Drive y lo registra en el modelo drive."""
        if self.Tarjeta_profesional_revisor_fiscal_doc and self.Tarjeta_profesional_revisor_fiscal_doc_fname:
            _logger.info(f"Subiendo documento '{self.Tarjeta_profesional_revisor_fiscal_doc_fname}' a Google Drive...")
            self._upload_Tarjeta_profesional_revisor_fiscal_to_drive_pn()
        else:
            _logger.warning(
                "No se encontró el documento 'Tarjeta profesional revisor fiscal (Si aplica)' para subir.")

    def _upload_Tarjeta_profesional_revisor_fiscal_to_drive_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Validar que self.drive_file_id es un objeto Many2one y no un string
        if isinstance(self.drive_file_id, str):
            _logger.error(f"El campo drive_file_id tiene un valor de tipo string: {self.drive_file_id}")
            return False

        # Buscar el registro en drive.file basado en el ID del archivo
        drive_file_record = self.env['drive.file'].search([('id', '=', self.drive_file_id.id)], limit=1)

        if not drive_file_record:
            _logger.error(
                f"No se encontró un registro en drive.file con el ID {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        carpeta_drive = drive_file_record.folder_id2  # Usamos 'folder_id2' aquí

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.Tarjeta_profesional_revisor_fiscal_doc)

            original_extension = os.path.splitext(self.Tarjeta_profesional_revisor_fiscal_doc_fname)[1]

            fixed_file_name = f"Tarjeta profesional revisor fiscal_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.Tarjeta_profesional_revisor_fiscal_doc_fname} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',  # Tipo genérico para binarios
                'parents': [{'id': carpeta_drive}]  # Aseguramos que se suba a la carpeta correcta
            }

            file_drive = drive.CreateFile(file_metadata)

            file_drive.SetContentFile(temp_file_path)  # Subir el archivo correctamente
            file_drive.Upload()

            file_id = file_drive['id']

            file_link = f"https://drive.google.com/file/d/{file_id}/view"

            _logger.info(f"Archivo subido correctamente a Google Drive con ID: {file_id} y URL: {file_link}")

            # Crear el registro en el modelo 'drive' y asignar correctamente 'drive_file_id' y 'stage2_id'
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.Tarjeta_profesional_revisor_fiscal_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo Tarjeta_profesional_revisor_fiscal_doc_id
            if not self.Tarjeta_profesional_revisor_fiscal_doc_id:
                self.Tarjeta_profesional_revisor_fiscal_doc_id = drive_record.id

            _logger.info(
                f"Campo Tarjeta_profesional_revisor_fiscal_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar Tarjeta_profesional_revisor_fiscal_doc_visible a False después de subir el archivo
            self.Tarjeta_profesional_revisor_fiscal_doc_visible = False

            _logger.info(f"Campo Tarjeta_profesional_revisor_fiscal_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    @api.constrains('Tarjeta_profesional_revisor_fiscal_doc')
    def _check_file_extension200(self):
        for record in self:
            if record.Tarjeta_profesional_revisor_fiscal_doc:
                filename = record.Tarjeta_profesional_revisor_fiscal_doc_fname

    @api.onchange('Tarjeta_profesional_revisor_fiscal_doc')
    def _onchange_Tarjeta_profesional_revisor_fiscal_doc(self):
        if self.Tarjeta_profesional_revisor_fiscal_doc:
            self.cedula_revisor_fiscal = True

    @api.onchange('Tarjeta_profesional_revisor_fiscal_doc')
    def _onchange_delete_Tarjeta_profesional_revisor_fiscal_doc(self):
        if not self.Tarjeta_profesional_revisor_fiscal_doc:
            self.Tarjeta_profesional_revisor_fiscal = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_Tarjeta_profesional_revisor_fiscal = {}


    Tarjeta_profesional_revisor_fiscal_actualizar_visible = fields.Boolean(default=False,
                                                              related='Tarjeta_profesional_revisor_fiscal_doc_id.actualizar_visible',
                                                              store=True, readonly=False)









    @api.model
    def cargar_documento_26(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'Tarjeta_profesional_revisor_fiscal_doc': file_content,
            'Tarjeta_profesional_revisor_fiscal_doc_fname': file_name,
        })

    aprueba_f_Tarjeta_profesional_revisor_fiscal = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_Tarjeta_profesional_revisor_fiscal = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)





#############################################################nuevo##########################################################################################

    Tarjeta_profesional_contador = fields.Boolean('Tarjeta profesional contador (Si aplica)', tracking=True)
    Tarjeta_profesional_contador_doc_visible = fields.Boolean(default=True)
    Tarjeta_profesional_contador_doc = fields.Binary(tracking=True, store=True)
    Tarjeta_profesional_contador_fname = fields.Char(store=True)
    Tarjeta_profesional_contador_doc_id = fields.Many2one('drive',
                                                                string='Tarjeta Profesional Contador (Si aplica)',
                                                                domain="[('stage2_id', '=', drive_file_id)]")
        
    def action_upload_tarjeta_profesional_contador(self):
        self.Tarjeta_profesional_contador = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir tarjeta profesional contador',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'tarjeta_profesional_contador' 
            },
        }


    @api.constrains('Tarjeta_profesional_contador_doc')
    def _check_file_extension200(self):
        for record in self:
            if record.Tarjeta_profesional_contador_doc:
                filename = record.Tarjeta_profesional_contador_fname

    @api.onchange('Tarjeta_profesional_contador_doc')
    def _onchange_Tarjeta_profesional_contador_doc(self):
        if self.Tarjeta_profesional_contador_doc:
            self.cedula_revisor_fiscal = True

    @api.onchange('Tarjeta_profesional_contador_doc')
    def _onchange_delete_Tarjeta_profesional_contador_doc(self):
        if not self.Tarjeta_profesional_contador_doc:
            self.Tarjeta_profesional_contador = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_Tarjeta_profesional_contador = {}


    Tarjeta_profesional_contador_actualizar_visible = fields.Boolean(default=False,
                                                                           related='Tarjeta_profesional_contador_doc_id.actualizar_visible',
                                                                           store=True, readonly=False)


    @api.model
    def cargar_documento_26(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'Tarjeta_profesional_contador_doc': file_content,
            'Tarjeta_profesional_contador_fname': file_name,
        })

    aprueba_f_Tarjeta_profesional_contador = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_Tarjeta_profesional_contador = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

#############################################nuevo###########################################################################################################
    Documento_de_identidad_contador = fields.Boolean('Documento de identidad contador (Si aplica)', tracking=True)
    Documento_de_identidad_contador_doc_visible = fields.Boolean(default=True)
    Documento_de_identidad_contador_doc = fields.Binary(tracking=True, store=True)
    Documento_de_identidad_contador_doc_fname = fields.Char(tracking=True, store=True)
    Documento_de_identidad_contador_doc_id = fields.Many2one('drive', string='Documento de identidad Contador (Si aplica)', domain="[('stage2_id', '=', drive_file_id)]")

    
    def action_upload_documento_identidad_contador(self):
        self.Documento_de_identidad_contador = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir documento de identidad contador',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'documento_identidad_contador' 
            },
        }

    @api.constrains('Documento_de_identidad_contador_doc')
    def _check_file_extension201(self):
        for record in self:
            if record.Documento_de_identidad_contador_doc:
                filename = record.Documento_de_identidad_contador_doc_fname

    @api.onchange('Documento_de_identidad_contador_doc')
    def _onchange_Documento_de_identidad_contador_doc(self):
        if self.Documento_de_identidad_contador_doc:
            self.cedula_revisor_fiscal = True

    @api.onchange('Documento_de_identidad_contador_doc')
    def _onchange_delete_Documento_de_identidad_contador_doc(self):
        if not self.Documento_de_identidad_contador_doc:
            self.Documento_de_identidad_contador = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_Documento_de_identidad_contador = {}


    Documento_de_identidad_contador_actualizar_visible = fields.Boolean(default=False,
                                                              related='Documento_de_identidad_contador_doc_id.actualizar_visible',
                                                              store=True, readonly=False)


    @api.model
    def cargar_documento_26(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'Documento_de_identidad_contador_doc': file_content,
            'Documento_de_identidad_contador_doc_fname': file_name,
        })

    aprueba_f_Documento_de_identidad_contador = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_Documento_de_identidad_contador = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    ################################Nuevo canpo############################
    relacion_id = fields.Many2one('contrataciones_fuv_relacion', string='Relación de Contrataciones', ondelete='cascade')


    experiencia_garantizada = fields.Boolean(
        string='El coordinador garantiza que el contratista tiene la experiencia requerida',
        tracking=True,
        help='Marcar si el coordinador garantiza que el contratista tiene la experiencia requerida.'
    )
 ###########################################Nuevos campos P/N ########################################################
    formato_fuv = fields.Boolean(string='Formato de inhabilitadades e incompatibilidades', tracking=True)
    formato_fuv_doc = fields.Binary(tracking=True, string='Formato de inhabilitadades e incompatibilidades')
    formato_fuv_doc_fname = fields.Char(tracking=True)
    formato_fuv_doc_visible = fields.Boolean(default=True)
    formato_fuv_doc_id = fields.Many2one('drive', string='Formato de inhabilitadades e incompatibilidades', tracking=True,
                                         domain="[('stage2_id', '=', drive_file_id)]")

    
    def action_upload_formato_fuv(self):
        self.formato_fuv = True  # Marcar el campo como True al iniciar la carga
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir formato de inhabilitadades e incompatibilidades',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'formato_fuv' 
            },
        }


    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_formato_fuv = {}


    formato_fuv_actualizar_visible = fields.Boolean(default=False, related='formato_fuv_doc_id.actualizar_visible',
                                                    store=True, readonly=False)


#################################################################################################################################
    tarjeta_profesional = fields.Boolean('Tarjeta Profesional', tracking=True)
    tarjeta_profesional_doc = fields.Binary(tracking=True)
    tarjeta_profesional_doc_fname = fields.Char()
    tarjeta_profesional_doc_visible = fields.Boolean(default=True)
    tarjeta_profesional_doc_id = fields.Many2one('drive', string='Tarjeta Profesional',
                                                 domain="[('stage2_id', '=', drive_file_id)]")

    
    def action_upload_tarjeta_profesional(self):
        self.tarjeta_profesional = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir tarjeta profesional',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'tarjeta_profesional' 
            },
        }


    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_tarjeta_profesional = {}


    tarjeta_profesional_actualizar_visible = fields.Boolean(default=False,
                                                            related='tarjeta_profesional_doc_id.actualizar_visible',
                                                            store=True, readonly=False)


    ###########################################Nuevos campos P/J ########################################################

    formato_fuv_ju = fields.Boolean('Formato FUV', tracking=True)
    formato_fuv_ju_doc = fields.Binary(tracking=True)
    formato_fuv_ju_doc_fname = fields.Char(tracking=True)
    formato_fuv_ju_doc_visible = fields.Boolean(default=True)
    formato_fuv_ju_doc_id = fields.Many2one('drive', string='Formato FUV', tracking=True,
                                            domain="[('stage2_id', '=', drive_file_id)]")


    def action_upload_formato_fuv_ju(self):
        self.formato_fuv_ju = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir formato de inhabilitadades e incompatibilidades',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'formato_fuv_ju' 
            },
        }


    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_formato_fuv_ju = {}


    formato_fuv_juri_actualizar_visible = fields.Boolean(default=False,
                                                         related='formato_fuv_ju_doc_id.actualizar_visible', store=True,
                                                         readonly=False)


   ################################################## #nuevo #############################################################################################
    polizas_bo = fields.Boolean(default=False)
    polizas = fields.Binary(string='Pólizas', tracking=True)
    polizas_fname = fields.Char(string='Nombre del archivo', tracking=True)
    poliza_id = fields.Many2one('drive', string='Pólizas', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    polizas_visible = fields.Boolean(default=True)

    
    def action_upload_polizas(self):
        self.polizas_bo = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir pólizas',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'polizas' 
            },
        }

    solicitar_poliza = fields.Selection([
        ('si', 'solicitar póliza'),
        ('no', 'Seleccionar')
    ], string='Seleccione', default='no')


    #nuevo #    
    def notificacion_poliza_coordinador(self):
        """Notifica solo al coordinador si se solicitó póliza."""
        if self.solicitar_poliza != 'si':
            _logger.info("No se solicita póliza. No se envía notificación al coordinador.")
            return False

        _logger.info("Iniciando notificación al coordinador por solicitud de póliza...")

        # URL base y enlace al contrato en Odoo
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        record_url = f"{base_url}/web#id={self.id}&view_type=form&model={self._name}"

        # Coordinador = creador del proyecto
        if not (self.proyecto_relacionado and self.proyecto_relacionado.create_uid.email):
            raise UserError("No se encontró un coordinador con correo para enviar la notificación.")

        destinatario = self.proyecto_relacionado.create_uid.email
        _logger.info(f"📩 Enviando notificación solo al coordinador: {destinatario}")

        # Armar correo
        subject = f"Solicitud de póliza para el contrato {self.new_name or self.name}"
        body = f"""
            <p>Cordial saludo,</p>
            <p>Se ha solicitado la póliza correspondiente al contrato <b>{self.new_name or self.name}</b>.</p>
            <p>Puedes revisarla directamente en la plataforma SGO:</p>
            <p><a href="{record_url}" target="_blank">🔗 Ir al contrato</a></p>
            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
            <p style="font-size: 12px; color: #888;">Este es un mensaje automático, por favor no responder.</p>
        """

        mail_values = {
            'subject': subject,
            'body_html': body,
            'email_to': destinatario,
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
        }

        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()
        _logger.info("✅ Notificación enviada al coordinador.")
        self.message_post(body="✅ Póliza solicitada.")

        return True

    def open_drive_filtered(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otras pólizas',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('contratacion_fuv_id', '=', self.id)],  # Solo archivos de este contrato
            'context': {
                'default_stage2_id': self.drive_file_id.id,  # Auto-llenar en nuevos archivos
                'default_contratacion_fuv_id': self.id,  # Relacionar con el contrato actual
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
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Validar que self.drive_file_id es un objeto Many2one y no un string
        if isinstance(self.drive_file_id, str):
            _logger.error(f"El campo drive_file_id tiene un valor de tipo string: {self.drive_file_id}")
            return False

        # Buscar el registro en drive.file basado en el ID del archivo
        drive_file_record = self.env['drive.file'].search([('id', '=', self.drive_file_id.id)], limit=1)

        if not drive_file_record:
            _logger.error(
                f"No se encontró un registro en drive.file con el ID {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        carpeta_drive = drive_file_record.folder_id2  # Usamos 'folder_id2' aquí

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.polizas)

            original_extension = os.path.splitext(self.polizas_fname)[1]

            fixed_file_name = f"Póliza_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.polizas_fname} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',  # Tipo genérico para binarios
                'parents': [{'id': carpeta_drive}]  # Aseguramos que se suba a la carpeta correcta
            }

            file_drive = drive.CreateFile(file_metadata)

            file_drive.SetContentFile(temp_file_path)  # Subir el archivo correctamente
            file_drive.Upload()

            file_id = file_drive['id']

            file_link = f"https://drive.google.com/file/d/{file_id}/view"

            _logger.info(f"Archivo subido correctamente a Google Drive con ID: {file_id} y URL: {file_link}")

            # Crear el registro en el modelo 'drive' y asignar correctamente 'drive_file_id' y 'stage2_id'
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.polizas,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo poliza_id
            if not self.poliza_id:
                self.poliza_id = drive_record.id

            _logger.info(f"Campo poliza_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar polizas_visible a False después de subir el archivo
            self.polizas_visible = False


            _logger.info(f"Campo polizas_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_poliza = {}


    poliza_actualizar_visible = fields.Boolean('Actualizar visible', default=False,
                                               related='poliza_id.actualizar_visible', store=True, readonly=False)


    ##############################################Nuevo################################################################################################
    polizas_pn = fields.Boolean(default=False)
    polizas2_pn = fields.Binary(string='Pólizas', tracking=True)
    polizas__pn_fname = fields.Char(string='Nombre del archivo', tracking=True)
    poliza_pn_id = fields.Many2one('drive', string='Pólizas', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    polizas_pn_visible = fields.Boolean(default=True)

    
    def action_upload_poliza_pn(self):
        self.polizas_pn = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir pólizas',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'poliza_pn' 
            },
        }



    def open_drive_filtered_pn(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otras pólizas',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('contratacion_fuv_id', '=', self.id)],  # Solo archivos de este contrato
            'context': {
                'default_stage2_id': self.drive_file_id.id,  # Auto-llenar en nuevos archivos
                'default_contratacion_fuv_id': self.id,  # Relacionar con el contrato actual
            },
            'target': 'current',
        }


    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_poliza_pn = {}


    poliza_pn_actualizar_visible = fields.Boolean('Actualizar visible', default=False,
                                               related='poliza_pn_id.actualizar_visible', store=True, readonly=False)


    ###################################################################################################

    mcb_centro_costo = fields.Char(related='proyecto_relacionado.mcb_centro_costo', string="Centro de costo", tracking=True)
    estado_proyecto = fields.Selection(
        related='proyecto_relacionado.estado',
        string='Estado del proyecto',
        readonly=True,
    )

    @api.model
    def copy(self, default=None):
        raise UserError("No se permite duplicar este registro.")


    #Nuevo campo Samuel
    total_departamentos = fields.Integer(
        string='Total',
        compute='_compute_total_departamentos',
        store=True
    )

    # Nueva variable Fecha impresión Cámara de Comercio
    fecha_impresion_cc = fields.Date(
        'Fecha impresión Cámara de Comercio',
        tracking=True,
        store=True,
        related="contratista_proveedor_relacionado.fecha_impresion_ccm",
    )
    ####################################################################################################################
    salario_basico_total = fields.Float(string="Salario básico (total)", default=0.0)
    seguridad_social = fields.Float(string='Seguridad Social (Costo Total)', default=0.00)
    prestaciones_sociales = fields.Float(string='Prestaciones Sociales (Costo Total)', default=0.00)
    valor_total_contrato_a_b_c = fields.Float(string='Valor Total Contrato (a + b + c)', compute='_compute_valor_total_contrato', store=True)

    @api.depends('salario_basico_total', 'seguridad_social', 'prestaciones_sociales')
    def _compute_valor_total_contrato(self):
        for record in self:
            record.valor_total_contrato_a_b_c = record.salario_basico_total + record.seguridad_social + record.prestaciones_sociales

    valor_letras = fields.Text('Valor en letras', compute='_compute_valor_letras', tracking=True)

    @api.depends('valor_total_contrato_a_b_c')
    def _compute_valor_letras(self):
        for record in self:
            valor_letras = num2words(record.valor_total_contrato_a_b_c, lang='es')
            record.valor_letras = valor_letras.upper()

    id_proyecto3 = fields.Char(string='Id del proyecto', related="proyecto_relacionado.id_proyecto2")

    @api.depends('departamentos_y_municipios_ids')
    def _compute_total_departamentos(self):
        for record in self:
            unique_departments = set()
            for line in record.departamentos_y_municipios_ids:
                if line.departamentos:
                    unique_departments.add(line.departamentos.id)
            record.total_departamentos = len(unique_departments)


    concepto_22 = fields.Many2one(comodel_name='concepto', string="Concepto", related="gestion_id_3.concepto_2", store=True)
    ##########################

    #Variables relatadas para obtener datos del form de contratistas y proveedores
    select_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('contrato_laboral', 'Contrato laboral')], string='Seleccione persona', tracking=True)

    @api.onchange('select_persona')
    def _onchange_select_persona(self):
        if self.select_persona in ['persona_natural', 'contrato_laboral']:
            return {'domain': {'contratista_proveedor_relacionado': [('clase_persona', '=', 'persona_natural')]}}
        elif self.select_persona == 'persona_juridica':
            return {'domain': {'contratista_proveedor_relacionado': [('clase_persona', '=', 'persona_juridica')]}}
        else:
            return {'domain': {'contratista_proveedor_relacionado': []}}  # Sin filtro para otros casos



    ####################################################################################################################

    #Campos combinados para la forma de pago
    campos_combinados = fields.Char(string="Campos Combinados", compute='_compute_campos_combinados')

    @api.depends(
        'dependencia_area_solicitante',
        'fecha_iniciacion',
        'fecha_terminacion'
    )
    def _compute_campos_combinados(self):
        for record in self:
            # Verificamos si todos los campos tienen valor
            if all([record.dependencia_area_solicitante,
                    record.fecha_iniciacion,
                    record.fecha_terminacion]):
                # Si todos los campos tienen valor, concatenamos el texto
                record.campos_combinados = f"{record.fecha_iniciacion} | {record.fecha_terminacion} | {record.dependencia_area_solicitante}"
            else:
                # Si alguno de los campos está vacío, dejamos el campo de texto vacío
                record.campos_combinados = ''

    bdd_rpc_related_ids = fields.One2many('bdd_rpc_related', 'contratista1', string='Pagos')

    ciudad_residencia = fields.Many2one(
        comodel_name='municipio',
        string="Ciudad de Residencia", store=True, related="contratista_proveedor_relacionado.municipios2"
    )



    new_name = fields.Char(string='Nombre', tracking=True, readonly=False, store=True)

    contratista_proveedor_relacionado = fields.Many2one(
        comodel_name='contratista_proveedor',
        string='Seleccionar contratista',
        context={'show_search_more': True}
    )

    def _validar_contratista_requerido_para_no_soporte(self, vals=None):
        """Soporte puede guardar casos incompletos; los demas deben tener contratista."""
        if self.env.user.has_group('Matriz.group_soporte'):
            return

        vals = vals or {}
        if not self:
            if not vals.get('contratista_proveedor_relacionado'):
                raise ValidationError("Debe seleccionar un contratista.")
            return

        if 'contratista_proveedor_relacionado' in vals and not vals.get('contratista_proveedor_relacionado'):
            raise ValidationError("Debe seleccionar un contratista.")

    nit = fields.Char(string="Nit", tracking=True, compute='_compute_nit', store=True,
                      related="contratista_proveedor_relacionado.nit")
    direccion_contratista = fields.Char('Dirección', tracking=True, store=True,
                                        related="contratista_proveedor_relacionado.direccion_fisica")
    correo = fields.Char('Correo electrónico', tracking=True, store=True,
                         related="contratista_proveedor_relacionado.correo_facturacion")

    r_legal = fields.Char(string="Representante legal", tracking=True, store=True,
                          related="contratista_proveedor_relacionado.nombre_r_legal")
    cedula_representante_legal = fields.Char(
        string="Cédula del representante legal",
        compute="_compute_cedula_representante_legal",
        store=True,
        tracking=True,
    )


    ciudad_contratista = fields.Char(
        'Ciudad de expedición',
        tracking=True,
        store=True,
        compute='_compute_ciudad_contratista'
    )

    @api.depends(
        'select_persona',
        'contratista_proveedor_relacionado.ciudad_exp_natural',
        'contratista_proveedor_relacionado.ciudad_exp_representante'
    )
    def _compute_ciudad_contratista(self):
        for record in self:
            if record.select_persona in ['persona_natural', 'contrato_laboral']:
                # Usar ciudad_exp_natural para persona natural y contrato laboral
                ciudad_natural = record.contratista_proveedor_relacionado.ciudad_exp_natural
                record.ciudad_contratista = ciudad_natural.name if ciudad_natural else ''

            elif record.select_persona == 'persona_juridica':
                # Usar ciudad_exp_representante para persona jurídica
                ciudades_juridicas = record.contratista_proveedor_relacionado.mapped('ciudad_exp_representante.name')
                record.ciudad_contratista = ', '.join(ciudades_juridicas) if ciudades_juridicas else ''
            else:
                # Si no hay selección, dejar el campo vacío
                record.ciudad_contratista = ''

    telefono_contratista = fields.Char('Teléfono/Celular', tracking=True, store=True,
                                     related="contratista_proveedor_relacionado.numero_telefono")
    fecha_ciudad_expedicion_contra = fields.Date(
        'Fecha expedición cedula del representante', tracking=True, store=True,
                                     related="contratista_proveedor_relacionado.expedicion_contra_j")

    expedicion_contra_N = fields.Date(
        'Fecha expedición cedula', tracking=True, store=True,
                                     related="contratista_proveedor_relacionado.expedicion_contra_N")

    ciudad_oficina_principal = fields.Many2one(
        comodel_name='municipio', store=True,
        string="Ciudad de oficina principal", related="contratista_proveedor_relacionado.ciudad_oficina_principal"
    )
    
    documento_identidad = fields.Char(
        'Documento de identificación', tracking=True, compute='_compute_documento_identidad', store=True)

    nombre_contratista = fields.Char('Nombre del contratista', tracking=True)


    @api.depends('contratista_proveedor_relacionado.cedula_representante')
    def _compute_cedula_representante_legal(self):
        for record in self:
            # Verifica y transforma el tipo de dato del campo relacionado
            cedula = record.contratista_proveedor_relacionado.cedula_representante
            if cedula:
                record.cedula_representante_legal = str(cedula)  # Convierte a string si no lo es
            else:
                record.cedula_representante_legal = ''

    @api.depends('contratista_proveedor_relacionado.cedula')
    def _compute_documento_identidad(self):
        for record in self:
            # Verifica y transforma el tipo de dato del campo relacionado
            cedula = record.contratista_proveedor_relacionado.cedula
            if cedula:
                record.documento_identidad = str(cedula)  # Convierte a string si no lo es
            else:
                record.documento_identidad = ''



    #Campos Hans
    # Relación con 'otro_si'
    otro_si = fields.Boolean('Otrosí', readonly=False, tracking=True)
    otrosi_ids = fields.One2many('otro_si', 'contratacion_fuv_en_id', string="Otrosí Relacionados")
    tipo_otrosi_relevante = fields.Char(
        string='Tipo otrosí',
        compute='_compute_fechas_otrosi_relevante',
    )
    fecha_iniciacion_otrosi_relevante = fields.Date(
        string='Fecha inicio otrosí',
        compute='_compute_fechas_otrosi_relevante',
    )
    fecha_terminacion_otrosi_relevante = fields.Date(
        string='Fecha fin otrosí',
        compute='_compute_fechas_otrosi_relevante',
    )
    fecha_fin_con_otrosi = fields.Date(
        string='Fecha fin contractual',
        compute='_compute_fechas_otrosi_relevante',
    )

    @api.depends(
        'fecha_terminacion',
        'otrosi_ids',
        'otrosi_ids.fecha_iniciacion',
        'otrosi_ids.fecha_terminacion',
        'otrosi_ids.adicion',
        'otrosi_ids.prorroga',
        'otrosi_ids.adicion_prorroga',
        'otrosi_ids.otro_si_modificatorio',
        'otrosi_ids.acta_de_prorroga_de_la_suspension',
        'otrosi_ids.tipo_modificacion_modificatorio',
        'otrosi_ids.adicion_number',
        'otrosi_ids.prorroga_number',
        'otrosi_ids.adicion_prorroga_number',
        'otrosi_ids.otro_si_modificatorio_number',
        'otrosi_ids.acta_de_prorroga_de_la_suspension_number',
    )
    def _compute_fechas_otrosi_relevante(self):
        for record in self:
            candidatos = record.otrosi_ids.filtered(
                lambda otrosi: otrosi.fecha_terminacion
                and record._otrosi_aplica_para_fecha_fin_vigente(otrosi)
            )
            relevante = candidatos.sorted(
                key=lambda otrosi: (
                    otrosi.fecha_terminacion,
                    otrosi.fecha_iniciacion or otrosi.fecha_terminacion,
                    otrosi.id,
                ),
                reverse=True,
            )[:1]
            record.tipo_otrosi_relevante = (
                record._nombre_tipo_otrosi_relevante(relevante)
                if relevante else False
            )
            record.fecha_iniciacion_otrosi_relevante = (
                relevante.fecha_iniciacion if relevante else False
            )
            record.fecha_terminacion_otrosi_relevante = (
                relevante.fecha_terminacion if relevante else False
            )
            fechas_fin = list(filter(None, (
                record.fecha_terminacion,
                record.fecha_terminacion_otrosi_relevante,
            )))
            record.fecha_fin_con_otrosi = max(fechas_fin) if fechas_fin else False

    def _otrosi_aplica_para_fecha_fin_vigente(self, otrosi):
        self.ensure_one()
        if otrosi.prorroga or otrosi.adicion_prorroga or otrosi.acta_de_prorroga_de_la_suspension:
            return True
        if otrosi.otro_si_modificatorio:
            return otrosi.tipo_modificacion_modificatorio in ('tiempo', 'ambos')
        return bool(
            otrosi.fecha_terminacion
            and self.fecha_terminacion
            and otrosi.fecha_terminacion > self.fecha_terminacion
        )

    def _nombre_tipo_otrosi_relevante(self, otrosi):
        self.ensure_one()
        if otrosi.adicion_prorroga or (otrosi.adicion and otrosi.prorroga):
            return 'Adición y prórroga %s' % (otrosi.adicion_prorroga_number or otrosi.id)
        if otrosi.prorroga:
            return 'Prórroga %s' % (otrosi.prorroga_number or otrosi.id)
        if otrosi.acta_de_prorroga_de_la_suspension:
            return 'Acta prórroga suspensión %s' % (
                otrosi.acta_de_prorroga_de_la_suspension_number or otrosi.id
            )
        if otrosi.adicion:
            return 'Adición %s' % (otrosi.adicion_number or otrosi.id)
        if otrosi.otro_si_modificatorio:
            tipo = dict(
                otrosi._fields['tipo_modificacion_modificatorio'].selection
            ).get(otrosi.tipo_modificacion_modificatorio)
            return 'Modificatorio%s %s' % (
                ' %s' % tipo.lower() if tipo else '',
                otrosi.otro_si_modificatorio_number or otrosi.id,
            )
        return 'Otrosí %s' % otrosi.id

    @api.onchange('otro_si')
    def _onchange_otro_si(self):
        for record in self:
            if not record.otro_si and record.otrosi_ids:
                record.otro_si = True  # Revertimos el cambio
                raise UserError("No puedes desactivar 'Otrosí' porque ya existen registros relacionados.")

    # Campos para almacenar las cantidades
    count_prorroga = fields.Integer('Cantidad de Prórrogas', compute='_compute_counts', store=True)
    count_adicion = fields.Integer('Cantidad de Adiciones', compute='_compute_counts', store=True)
    count_otro_si = fields.Integer('Cantidad de Otrosí', compute='_compute_counts', store=True)
    count_reinicio = fields.Integer('Cantidad de Reinicios', compute='_compute_counts', store=True)
    count_suspension = fields.Integer('Cantidad de Suspensiones', compute='_compute_counts', store=True)
    count_modificatorio = fields.Integer('Cantidad de Modificatorios', compute='_compute_counts', store=False)
    count_acta_prorroga_suspension = fields.Integer('Cantidad de Actas de Prórroga de Suspensión', compute='_compute_counts', store=False)

    @api.depends('otrosi_ids', 'otrosi_ids.prorroga', 'otrosi_ids.adicion', 'otrosi_ids.otro_si',
                 'otrosi_ids.reinicio', 'otrosi_ids.suspension',
                 'otrosi_ids.otro_si_modificatorio', 'otrosi_ids.acta_de_prorroga_de_la_suspension')
    def _compute_counts(self):
        for record in self:
            record.count_prorroga = sum(record.otrosi_ids.mapped('prorroga'))
            record.count_adicion = sum(record.otrosi_ids.mapped('adicion'))
            record.count_otro_si = sum(record.otrosi_ids.mapped('otro_si'))
            record.count_reinicio = sum(record.otrosi_ids.mapped('reinicio'))
            record.count_suspension = sum(record.otrosi_ids.mapped('suspension'))
            record.count_modificatorio = sum(record.otrosi_ids.mapped('otro_si_modificatorio'))
            record.count_acta_prorroga_suspension = sum(record.otrosi_ids.mapped('acta_de_prorroga_de_la_suspension'))

    # Campos de chulear - Tabla izquierda
    ####################################Sección para documentos P/N#####################################################
    documento_identidad_pn = fields.Boolean('Documento de identidad', tracking=True, store=True,)

    documento_identidad_pn_doc_visible = fields.Boolean(default=True)
    documento_identidad_id = fields.Many2one('drive', string='Documento de Identidad', readonly=True, store=True, domain="[('stage2_id', '=', drive_file_id)]")
    documento_identidad_pn_doc_fname = fields.Char(string="Nombre del Documento Identidad PN", store=True, related="contratista_proveedor_relacionado.documento_identidad_name")
    documento_identidad_pn_doc = fields.Binary(string="Documento Identidad PN", attachment=True, tracking=True)

    
    def action_upload_documento_identidad_pn(self):
        self.documento_identidad_pn = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir documento de identidad',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'documento_identidad_pn' 

            },
        }


    rut_pn = fields.Boolean('RUT (actualizado)', tracking=True)

    rut_id = fields.Many2one('drive', string='RUT', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    rut_pn_doc_visible = fields.Boolean(default=True)
    rut_pn_doc = fields.Binary('RUT (actualizado)', tracking=True)
    rut_pn_doc_fname = fields.Char(string="RUT (actualizado)", store=True, related="contratista_proveedor_relacionado.rut_actualizado_name")
    
    def action_upload_rut_pn(self):
        self.rut_pn = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RUT',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'rut_pn' 
            },
        }


    hoja_vida_pn = fields.Boolean('Hoja de vida', tracking=True)

    hoja_vida_pn_doc_visible = fields.Boolean(default=True)
    hoja_vida_pn_doc = fields.Binary('Hoja de vida', tracking=True)
    hoja_vida_pn_doc_fname = fields.Char(string="Hoja de vida", store=True, related="contratista_proveedor_relacionado.hoja_vida_name")
    hoja_vida_id = fields.Many2one('drive', string='Hoja de vida', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_hoja_vida_pn(self):
        self.hoja_vida_pn = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir hoja de vida',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'hoja_vida_pn' 
            },
        }



    certificados_estudio = fields.Boolean('Certificados de estudio', tracking=True)

    certificados_estudio_doc_visible = fields.Boolean(default=True)
    certificados_estudio_doc = fields.Binary('Certificados de estudio', tracking=True)
    certificados_estudio_doc_fname = fields.Char(string="Certificados de estudio", store=True, related="contratista_proveedor_relacionado.certificado_estudio_name")
    certificados_estudio_id = fields.Many2one('drive', string='Certificados de estudio', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
        
    def action_upload_certificados_estudio(self):
        self.certificados_estudio = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificados de estudio',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificados_estudio' 
            },
        }

    certificados_laborales = fields.Boolean('Certificados Laborales', tracking=True)

    certificados_laborales_doc_visible = fields.Boolean(default=True)
    certificados_laborales_doc_fname = fields.Char(string="Certificados Laborales", store=True, related="contratista_proveedor_relacionado.certificado_laborales_name")
    certificados_laborales_doc = fields.Binary('Certificados Laborales', tracking=True)
    certificados_laborales_id = fields.Many2one('drive', string='Certificados Laborales', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificados_laborales(self):
        self.certificados_laborales = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificados laborales',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificados_laborales' 
            },
        }


    certificado_arl_doc_visible = fields.Boolean(default=True)
    certificado_arl_doc = fields.Binary('Certificado de afiliación a ARL (no mayor a 30 días) o formato ARL FUV', tracking=True)
    certificado_arl_doc_fname = fields.Char(string="Certificado de afiliación a ARL (no mayor a 30 días) o formato ARL FUV", store=True, related="contratista_proveedor_relacionado.cert_arl_name")
    certificado_arl_id = fields.Many2one('drive', string='Certificado de afiliación a ARL (no mayor a 30 días)', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificado_arl(self):
        self.certificado_arl = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado ARL',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificado_arl' 
            },
        }


    certificacion_salud = fields.Boolean('Certificación Afiliación Salud (no mayor a 30 días)', tracking=True)

########################################################Nueva arl######################################################################################################
    otra_certificado_arl_doc_visible = fields.Boolean(default=True)
    otra_certificado_arl_doc = fields.Binary('Novedad ARL', tracking=True)
    otra_certificado_arl_doc_fname = fields.Char(string="Novedad ARL", store=True,
                                                 related="contratista_proveedor_relacionado.cert_arl_name")
    otra_certificado_arl_id = fields.Many2one('drive', string='Novedad ARL', readonly=True,
                                              domain="[('stage2_id', '=', drive_file_id)]")
    
        
    def action_upload_otra_certificado_arl(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir novedad ARL',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'otra_certificado_arl' 
            },
        }


    otra_certificado_arl = fields.Boolean('Novedad ARL',
                                          tracking=True)

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_otra_certificado_arl = {}


    otra_certificado_arl_actualizar_visible = fields.Boolean(default=False,
                                                             related='otra_certificado_arl_id.actualizar_visible',
                                                             readonly=False, store=False)


    @api.constrains('certificado_arl_doc')
    def _check_file_extension999(self):
        for record in self:
            if record.otra_certificado_arl_doc:
                filename = record.otra_certificado_arl_doc_fname

    @api.onchange('otra_certificado_arl_doc')
    def _onchange_otra_certificado_arl_doc(self):
        if self.otra_certificado_arl_doc:
            self.otra_certificado_arl = True

    @api.onchange('otra_certificado_arl_doc')
    def _onchange_delete_otra_certificado_arl_doc(self):
        if not self.otra_certificado_arl_doc:
            self.otra_certificado_arl = False

    @api.model
    def cargar_documento_999(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'otra_certificado_arl_doc': file_content,
            'otra_certificado_arl_doc_fname': file_name,
        })

    aprueba_j_h_otra_arl = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_otra_arl_ = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')
    aprueba_r_otra_arl_ = fields.Boolean('Aprueba-F', tracking=True)

    #################################################################################################################################################

    certificacion_salud_doc_visible = fields.Boolean(default=True)
    certificacion_salud_doc = fields.Binary('Certificación Afiliación Salud (no mayor a 30 días)', tracking=True)
    certificacion_salud_doc_fname = fields.Char(string="Certificación Afiliación Salud (no mayor a 30 días)", store=True, related="contratista_proveedor_relacionado.cert_afiliacion_salud_name")
    certificacion_salud_id = fields.Many2one('drive', string='Certificación Afiliación Salud (no mayor a 30 días)', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificacion_salud(self):
        self.certificacion_salud = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación de salud',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_salud' 
            },
        }


    certificacion_pension = fields.Boolean('Certificación afiliación a pensión (no mayor a 30 días)', tracking=True)

    certificacion_pension_doc_visible = fields.Boolean(default=True)
    certificacion_pension_doc = fields.Binary(tracking=True, store=True)
    certificacion_pension_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_afiliacion_pension_name")
    certificacion_pension_id = fields.Many2one('drive', string="Cert.afiliación a pensión(no mayor a 30 días)", readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    
    def action_upload_certificacion_pension(self):
        self.certificacion_pension = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación de pensión',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_pension' 
            },
        }

    certificacion_examen_preocupacional = fields.Boolean('Certificación examen preocupacional (no mayor a 30 días)', tracking=True)

    certificacion_examen_preocupacional_doc_visible = fields.Boolean(default=True)
    certificacion_examen_preocupacional_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_examen_preocupacional_name")
    certificacion_examen_preocupacional_doc = fields.Binary(tracking=True)
    certificacion_examen_preocupacional_id = fields.Many2one('drive', string="Certificación examen preocupacional", readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificacion_examen_preocupacional(self):
        self.certificacion_examen_preocupacional = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación examen preocupacional',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_examen_preocupacional' 
            },
        }

    resolucion_facturacion_ju = fields.Boolean('Resolución de facturación electrónica', tracking=True)

    resolucion_facturacion_ju_doc = fields.Binary(tracking=True)
    resolucion_facturacion_ju_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.resolucion_facturacion_electronica_name")
    resolucion_facturacion_ju_id = fields.Many2one('drive', string='Resolución Facturación', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    resolucion_facturacion_ju_doc_visible = fields.Boolean(default=True)
    
    def action_upload_resolucion_facturacion_ju(self):
        self.resolucion_facturacion_ju = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir resolución de facturación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'resolucion_facturacion_ju' 
            },
        }

    def subir_documento_resolucion_facturacion_ju_a_drive(self):
        """Sube el documento de resolución de facturación a Google Drive y lo registra en el modelo drive."""
        if self.resolucion_facturacion_ju_doc and self.resolucion_facturacion_ju_doc_fname:
            _logger.info(f"Subiendo documento '{self.resolucion_facturacion_ju_doc_fname}' a Google Drive...")
            self._upload_document_to_drive_resolucion_facturacion_ju_pn()
        else:
            _logger.warning("No se encontró el documento de resolución de facturación para subir.")

    def _upload_document_to_drive_resolucion_facturacion_ju_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Validar que self.drive_file_id es un objeto Many2one y no un string
        if isinstance(self.drive_file_id, str):
            _logger.error(f"El campo drive_file_id tiene un valor de tipo string: {self.drive_file_id}")
            return False

        # Buscar el registro en drive.file basado en el ID del archivo
        drive_file_record = self.env['drive.file'].search([('id', '=', self.drive_file_id.id)], limit=1)

        if not drive_file_record:
            _logger.error(f"No se encontró un registro en drive.file con el ID {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        carpeta_drive = drive_file_record.folder_id2  # Usamos 'folder_id2' aquí

        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.resolucion_facturacion_ju_doc)

            original_extension = self.resolucion_facturacion_ju_doc_fname.split('.')[-1]
            fixed_file_name = f"11_Resolución Facturación_{uuid.uuid4()}.{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{original_extension}") as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {fixed_file_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google Drive dentro de la carpeta correcta
            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',  # Tipo genérico para binarios
                'parents': [{'id': carpeta_drive}]  # Aseguramos que se suba a la carpeta correcta
            }
            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)  # Subir el archivo correctamente
            file_drive.Upload()

            file_id = file_drive['id']
            file_link = f"https://drive.google.com/file/d/{file_id}/view"

            _logger.info(f"Archivo subido correctamente a Google Drive con ID: {file_id} y URL: {file_link}")

            # Crear el registro en el modelo 'drive'
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.resolucion_facturacion_ju_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Actualizar el campo resolucion_facturacion_ju_id si está vacío
            if not self.resolucion_facturacion_ju_id:
                self.resolucion_facturacion_ju_id = drive_record.id

            _logger.info(f"Campo resolucion_facturacion_ju_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Ocultar el campo después de subir el archivo
            self.resolucion_facturacion_ju_doc_visible = False
            _logger.info("Campo resolucion_facturacion_ju_doc_visible actualizado a False.")

            # Asegurar que solo un registro esté siendo guardado
            self.ensure_one()

            # Eliminar el archivo temporal después de la subida
            os.unlink(temp_file_path)

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True


    certificacion_contraloria = fields.Boolean('Certificado contraloría (no mayor a 30 días)', tracking=True)

    certificacion_contraloria_doc_visible = fields.Boolean(default=True)
    certificacion_contraloria_doc = fields.Binary(tracking=True)
    certificacion_contraloria_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_contraloria_name")

    certificacion_contraloria_id = fields.Many2one('drive', string='Certificación Contraloría', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
        
    def action_upload_certificacion_contraloria(self):
        self.certificacion_contraloria = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación contraloría',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_contraloria' 
            },
        }


    certificacion_procuraduria = fields.Boolean('Certificado de procuraduría (no mayor a 30 días)', tracking=True)

    certificacion_procuraduria_doc_visible = fields.Boolean(default=True)
    certificacion_procuraduria_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_procuraduria_name")
    certificacion_procuraduria_doc = fields.Binary(tracking=True)
    certificacion_procuraduria_id = fields.Many2one('drive', string='Certificación Procuraduría', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificacion_procuraduria(self):
        self.certificacion_procuraduria = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación procuraduría',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_procuraduria' 
            },
        }

    certificacion_policia_judicial_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_policia_judicial_name")
    certificacion_policia_judicial_doc = fields.Binary(tracking=True)
    certificacion_policia_judicial_id = fields.Many2one('drive', string='Certificación Policía Judicial', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    certificacion_policia_judicial_doc_visible = fields.Boolean(default=True)
    
    def action_upload_certificacion_policia_judicial(self):
        self.certificacion_policia_judicial = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación policía judicial',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_policia_judicial' 
            },
        }


    certificacion_medidas_cautelares_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_medidas_cautelares_name")
    certificacion_medidas_cautelares_doc = fields.Binary(tracking=True)
    certificacion_medidas_cautelares_id = fields.Many2one('drive', string='Medidas correctivas (no mayor a 30 días)', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    certificacion_medidas_cautelares_doc_visible = fields.Boolean(default=True)

    
    def action_upload_certificacion_medidas_cautelares(self):
        self.certificacion_medidas_cautelares = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir medidas correctivas',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_medidas_cautelares' 
            },
        }



    certificacion_inhabilidades = fields.Boolean('Certificado de inhabilidades (no mayor a 30 días)', tracking=True)

    certificacion_inhabilidades_doc_visible = fields.Boolean(default=True)
    certificacion_inhabilidades_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_inhabilidades_name")
    certificacion_inhabilidades_doc = fields.Binary(tracking=True)
    certificacion_inhabilidades_id = fields.Many2one('drive', string='Certificación Inhabilidades', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificacion_inhabilidades(self):
        self.certificacion_inhabilidades = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación inhabilidades',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_inhabilidades' 
            },
        }

    certificacion_libreta_militar = fields.Boolean('Certificado libreta militar (< 50 años) (si aplica)', tracking=True)

    certificacion_libreta_militar_doc_visible = fields.Boolean(default=True)
    certificacion_libreta_militar_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_libreta_militar_name")
    certificacion_libreta_militar_doc = fields.Binary(tracking=True)
    certificacion_libreta_militar_id = fields.Many2one('drive', string='Certificación Libreta Militar', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificacion_libreta_militar(self):
        self.certificacion_libreta_militar = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación libreta militar',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_libreta_militar' 
            },
        }
 


    resolucion_pensionado = fields.Boolean('Resolución de pensionado vigente (si aplica)', tracking=True)

    resolucion_pensionado_doc_visible = fields.Boolean(default=True)
    resolucion_pensionado_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.resolucion_pensionado_vigente_name")
    resolucion_pensionado_doc = fields.Binary(tracking=True)
    resolucion_pensionado_id = fields.Many2one('drive', string='Resolución Pensionado', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_resolucion_pensionado(self):
        self.resolucion_pensionado = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir resolución pensionado',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'resolucion_pensionado' 
            },
        }

    certificacion_bancaria = fields.Boolean('Certificación bancaria (vigente)', tracking=True)

    certificacion_bancaria_doc_visible = fields.Boolean(default=True)
    certificacion_bancaria_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.certificado_bancario_vigente_name")
    certificacion_bancaria_doc = fields.Binary(tracking=True)
    certificacion_bancaria_id = fields.Many2one('drive', string='Certificación Bancaria', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificacion_bancaria(self):
        self.certificacion_bancaria = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificación bancaria',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'certificacion_bancaria' 
            },
        }


    declaracion_juramentada = fields.Boolean('Declaración juramentada', tracking=True)
    declaracion_juramentada_doc_fname = fields.Char()
    declaracion_juramentada_doc = fields.Binary(tracking=True)
    declaracion_juramentada_doc_visible = fields.Boolean(default=True)
    declaracion_juramentada_id = fields.Many2one('drive', string='Declaracion juramentada', readonly=True, domain="[('stage2_id', '=', drive_file_id)]")
        
    def action_upload_declaracion_juramentada(self):
        self.declaracion_juramentada = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir declaración juramentada',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'declaracion_juramentada' 
            },
        }




    ####################################Sección para documentos P/J#####################################################
    propuesta_pj = fields.Boolean('Propuesta', tracking=True)
    propuesta_pj_doc = fields.Binary(tracking=True, store=True)
    propuesta_pj_doc_visible = fields.Boolean(default=True)
    propuesta_pj_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.propuesta_j_name")

    propuesta_pj_doc_id = fields.Many2one('drive', string='Propuesta PJ', domain="[('stage2_id', '=', drive_file_id)]")
    
    # Función para el botón de la PROPUESTA
    def action_upload_propuesta(self):
        self.propuesta_pj = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir propuesta',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                'default_stage2_id': self.drive_file_id.id, 
                'tipo_documento_upload': 'propuesta_pj'  # <--- SEÑAL
            },
        }



    camara_comercio_doc_visible = fields.Boolean(default=True)
    camara_comercio_doc = fields.Binary(tracking=True, store=True)
    camara_comercio_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.camara_c_actualizada_j_name")

    camara_comercio_doc_id = fields.Many2one('drive', string='Cámara de Comercio', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_camara_comercio(self):
        self.camara_comercio = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir cámara de comercio',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente al campo 'folder_id'
                'default_folder_id': google_folder_id, 
                'default_stage2_id': self.drive_file_id.id, 
                
                'tipo_documento_upload': 'camara_comercio' 
            },
        }



    rut_pj_doc_visible = fields.Boolean(default=True)
    rut_pj_doc = fields.Binary(tracking=True, store=True)
    rut_pj_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.rut_actualizado_j_name")

    rut_pj_doc_id = fields.Many2one('drive', string='RUT PJ', domain="[('stage2_id', '=', drive_file_id)]")

    
    def action_upload_rut_pj(self):
        self.rut_pj = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RUT',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente al campo 'folder_id'
                'default_folder_id': google_folder_id, 
                # La ID de la carpeta Odoo (stage2_id) ya no es necesaria para la subida
                'default_stage2_id': self.drive_file_id.id, 
                'tipo_documento_upload': 'rut_pj' 
            },
        }

    rup = fields.Boolean('RUP (Si aplica)', tracking=True)
    rup_doc_visible = fields.Boolean(default=True)
    rup_doc = fields.Binary(tracking=True, store=True)
    rup_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.rup_j_name")

    rup_doc_id = fields.Many2one('drive', string='RUP', domain="[('stage2_id', '=', drive_file_id)]")


    def action_upload_rup(self):
        self.rup = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RUP',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (Necesario para el M2O en la vista)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'rup' 
            },
        }


    hoja_vida_juridica = fields.Boolean('Hoja de vida jurídica (actualizada)', tracking=True)

    
    hoja_vida_juridica_doc = fields.Binary(tracking=True, store=True)
    hoja_vida_juridica_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.hoja_vida_j_name")

    hoja_vida_juridica_doc_id = fields.Many2one('drive', string='Hoja de Vida Jurídica', domain="[('stage2_id', '=', drive_file_id)]")
    hoja_vida_juridica_doc_visible = fields.Boolean(default=True)

    def action_upload_hoja_vida_juridica(self):
        self.hoja_vida_juridica = True  
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir hoja de vida jurídica',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,  
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'hoja_vida_juridica'
            },
        }



    cert_contraloria = fields.Boolean('Cert. Contraloría', tracking=True)

    cert_contraloria_doc_visible = fields.Boolean(default=True)
    cert_contraloria_doc = fields.Binary(tracking=True, store=True)
    cert_contraloria_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_contraloria_j_name")
    cert_contraloria_doc_id = fields.Many2one('drive', string='Certificado Contraloría', domain="[('stage2_id', '=', drive_file_id)]")

    def action_upload_cert_contraloria(self):
        self.cert_contraloria = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado contraloría',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cert_contraloria' 
            },
        }


    cert_procuraduria = fields.Boolean('Cert. Procuraduría', tracking=True)

    cert_procuraduria_doc_visible = fields.Boolean(default=True)
    cert_procuraduria_doc = fields.Binary(tracking=True, store=True)
    cert_procuraduria_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_procuraduria_j_name")

    cert_procuraduria_doc_id = fields.Many2one('drive', string='Certificado Procuraduría', domain="[('stage2_id', '=', drive_file_id)]")
    

    def action_upload_cert_procuraduria(self):
        self.cert_procuraduria = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado procuraduría',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cert_procuraduria' 
            },
        }



    estados_financieros_juridico_doc_visible = fields.Boolean(default=True)
    estados_financieros_juridico_doc = fields.Binary(tracking=True, store=True)
    estados_financieros_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.estados_financieros2_name")
    estados_financieros_juridico_doc_id = fields.Many2one('drive', string='Estados Financieros Jurídico', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_estados_financieros_juridico(self):
        self.estados_financieros_juridico = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir estados financieros jurídico',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'estados_financieros_juridico' 
            },
        }



    cert_policia_judicial = fields.Boolean('Certificados de policía judicial, medidas cautelares', tracking=True)

    cert_policia_judicial_doc_visible = fields.Boolean(default=True)
    cert_policia_judicial_doc = fields.Binary(tracking=True, store=True)
    cert_policia_judicial_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_medidas_cautelares_j_name")
    cert_policia_judicial_doc_id = fields.Many2one('drive', string='Certificado Policía Judicial', domain="[('stage2_id', '=', drive_file_id)]" )
    
    def action_upload_cert_policia_judicial(self):
        self.cert_policia_judicial = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado policía judicial',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cert_policia_judicial' 
            },
        }

    cert_inhabilidades = fields.Boolean('Certificado de inhabilidades', tracking=True)

    cert_inhabilidades_doc_visible = fields.Boolean(default=True)
    cert_inhabilidades_doc = fields.Binary(tracking=True, store=True)
    cert_inhabilidades_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_inhabilidades_j_name")
    cert_inhabilidades_doc_id = fields.Many2one('drive', string='Certificado Inhabilidades', domain="[('stage2_id', '=', drive_file_id)]")
        
    def action_upload_cert_inhabilidades(self):
        self.cert_inhabilidades = True
        # Asegúrate de poner el nombre correcto de tu módulo
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # --- ¡CORRECCIÓN CLAVE AQUÍ! ---
        # 1. Obtenemos el ID de Google Drive real del campo 'folder_id2' del registro 'drive_file_id'.
        # 2. Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado inhabilidades',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                # Pasamos la ID STRING de Google Drive directamente al campo 'folder_id'
                'default_folder_id': google_folder_id, 
                
                # Nota: El 'default_stage2_id' ya no es necesario para la carpeta, 
                # pero lo dejo comentado si necesitas esa relación por otras razones:
                'default_stage2_id': self.drive_file_id.id, 
                
                'tipo_documento_upload': 'cert_inhabilidades'   
            },
        }

    resolucion_facturacion = fields.Boolean('Resolución de facturación electrónica', tracking=True)

    resolucion_facturacion_doc_visible = fields.Boolean(default=True)
    resolucion_facturacion_doc = fields.Binary(tracking=True, store=True)
    resolucion_facturacion_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.resolucion_fact_elect_j_name")
    resolucion_facturacion_doc_id = fields.Many2one('drive', string='Resolución Facturación', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_resolucion_facturacion(self):
        self.resolucion_facturacion = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir resolución facturación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'resolucion_facturacion' 
            },
        }


    experiencia_laboral = fields.Boolean('Experiencia laboral', tracking=True)

    
    experiencia_laboral_doc = fields.Binary(tracking=True, store=True)
    experiencia_laboral_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.experiencia_laboral_j_name")
    experiencia_laboral_doc_id = fields.Many2one('drive', string='Experiencia Laboral', domain="[('stage2_id', '=', drive_file_id)]")
    experiencia_laboral_doc_visible = fields.Boolean(default=True)

    def action_upload_experiencia_laboral(self):
        self.experiencia_laboral = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir experiencia laboral',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'experiencia_laboral'
            },
        }


    certificado_pago_parafiscales = fields.Boolean('Certificado de pago parafiscales', tracking=True)

    certificado_pago_parafiscales_doc_visible = fields.Boolean(default=True)
    certificado_pago_parafiscales_doc = fields.Binary(tracking=True, store=True)
    certificado_pago_parafiscales_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_pago_paraf_j_name")
    certificado_pago_parafiscales_doc_id = fields.Many2one('drive', string='Certificado Pago Parafiscales', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_cert_pago_parafiscales(self):
        self.certificado_pago_parafiscales = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado pago parafiscales',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cert_pago_parafiscales' 
            },
        }


    planilla_seguridad_social = fields.Boolean('Planilla de seguridad social', tracking=True)

    planilla_seguridad_social_doc_visible = fields.Boolean(default=True)
    planilla_seguridad_social_doc = fields.Binary(tracking=True, store=True)
    planilla_seguridad_social_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.planilla_seguridad_social_j_name")
    planilla_seguridad_social_doc_id = fields.Many2one('drive', string='Planilla Seguridad Social', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_planilla_seguridad_social(self):
        self.planilla_seguridad_social = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir planilla seguridad social',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'planilla_seguridad_social' 
            },
        }

    certificado_junta_contadores = fields.Boolean('Certificado de junta de contadores (si aplica)', tracking=True)

    certificado_junta_contadores_doc_visible = fields.Boolean(default=True)
    certificado_junta_contadores_doc = fields.Binary(tracking=True, store=True)
    certificado_junta_contadores_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_junta_contadores_j_name")
    certificado_junta_contadores_doc_id = fields.Many2one('drive', string='Certificado Junta Contadores', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_cert_junta_contadores(self):
        self.certificado_junta_contadores = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado junta contadores',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cert_junta_contadores' 
            },
        }


    cedula_revisor_fiscal = fields.Boolean('Documento de identidad revisor fiscal (Si aplica)', tracking=True)

    cedula_revisor_fiscal_doc_visible = fields.Boolean(default=True)
    cedula_revisor_fiscal_doc = fields.Binary(tracking=True, store=True)
    cedula_revisor_fiscal_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cedula_revisor_fiscal_contador_j_name")
    cedula_revisor_fiscal_doc_id = fields.Many2one('drive', string='Documento de identidad revisor fiscal (Si aplica)', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_cedula_revisor_fiscal(self):
        self.cedula_revisor_fiscal = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir documento de identidad revisor fiscal',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cedula_revisor_fiscal' 
            },
        }


    certificado_bancario = fields.Boolean('Certificado bancario (vigente)', tracking=True)

    certificado_bancario_doc_visible = fields.Boolean(default=True)
    certificado_bancario_doc = fields.Binary(tracking=True, store=True)
    certificado_bancario_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.certificado_bancario_vigente_j_name")
    certificado_bancario_doc_id = fields.Many2one('drive', string='Certificado Bancario', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_certificado_bancario(self):
        self.certificado_bancario = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado bancario',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'certificado_bancario' 
            },
        }

    cedula_rep_legal = fields.Boolean('Cédula del Rep. Legal', tracking=True)

    cedula_rep_legal_doc_visible = fields.Boolean(default=True)
    cedula_rep_legal_doc = fields.Binary(tracking=True, store=True)
    cedula_rep_legal_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cedula_representante_legal_name")
    cedula_rep_legal_doc_id = fields.Many2one('drive', string='Cédula Rep. Legal', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_cedula_rep_legal(self):
        self.cedula_rep_legal = True  # Marcar el campo como True al iniciar la acción de subida
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir cédula rep. legal',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cedula_rep_legal' 
            },
        }



    hoja_vida_rep_legal = fields.Boolean('Hoja de vida Rep. Legal', tracking=True)

    
    hoja_vida_rep_legal_doc = fields.Binary(tracking=True, store=True)
    hoja_vida_rep_legal_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.hoja_v_representante_legal_name")
    hoja_vida_rep_legal_doc_id = fields.Many2one('drive', string='Hoja de Vida Rep. Legal', domain="[('stage2_id', '=', drive_file_id)]")
    hoja_vida_rep_legal_doc_visible = fields.Boolean(default=True)

    def action_upload_hoja_vida_rep_legal(self):
        self.hoja_vida_rep_legal = True  # Marcar el campo como True al iniciar la acción de subida
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir hoja de vida rep. legal',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'hoja_vida_rep_legal'
            },
        }
    

    cert_contraloria_rep_legal = fields.Boolean('Certificados de contraloría Rep. Legal', tracking=True)

    cert_contraloria_rep_legal_doc_visible = fields.Boolean(default=True)
    cert_contraloria_rep_legal_doc = fields.Binary(tracking=True, store=True)
    cert_contraloria_rep_legal_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_contraloria_repre_legal_name")
    cert_contraloria_rep_legal_doc_id = fields.Many2one('drive', string='Certificado Contraloría Rep. Legal', domain="[('stage2_id', '=', drive_file_id)]" )
    
    def action_upload_cert_contraloria_rep_legal(self):
        self.cert_contraloria_rep_legal = True  # Marcar el campo como True al iniciar la acción de subida
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado Contraloría rep. legal',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cert_contraloria_rep_legal' 
            },
        }

    #procuraduria Persona Jurídica
    aprueba_j_h_contraloria_rep_lega = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    aprueba_j_h_contraloria_rep_lega_doc_visible = fields.Boolean(default=True)
    aprueba_j_h_contraloria_rep_lega_doc = fields.Binary(tracking=True, store=True)
    aprueba_j_h_contraloria_rep_lega_doc_fname = fields.Char(store=True, related="contratista_proveedor_relacionado.cert_procuraduria_repre_legal_name")
    cert_procuraduria_rep_legal_doc_id = fields.Many2one('drive', string='Cert. procuraduría representante legal', domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_cert_procuraduria_rep_legal(self):
        self.cert_procuraduria_rep_legal = True  # Marcar el campo como True al iniciar la acción de subida
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir cert. procuraduría rep. legal',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cert_procuraduria_rep_legal' 
            },
        }


    rut_pj = fields.Boolean('RUT (Actualizado)', tracking=True)
    aprueba_j_h_exm_prec = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_c_preocupacional = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')
    aprueba_j_h_doc = fields.Boolean('Aprueba - Aprueba área jurídica.', tracking=True)
    aprueba_rr_hh_doc = fields.Boolean('Aprueba - Aprueba recursos humanos.', tracking=True)
    aprueba_f_docc = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')
    aprueba_j_h_rut = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_rutt = fields.Boolean('Aprueba-F', tracking=True)
    compromiso_presupuestal = fields.Char('RPC', tracking=True)
    salario_basico = fields.Float('Salario básico', tracking=True, required=True)

    iva = fields.Float('IVA (19%)', store=True, tracking=True)
    salario_basico_iva = fields.Float('Salario básico + IVA', compute='_compute_salario_basico_iva', store=True,
                                      tracking=True)

    @api.depends('salario_basico', 'iva')
    def _compute_salario_basico_iva(self):
        """Suma el salario básico y el IVA."""
        for record in self:
            record.salario_basico_iva = record.salario_basico + record.iva




    # @api.depends('contratista_proveedor_relacionado.nit')
    # def _compute_nit(self):
    #     for record in self:
    #         record.nit = str(record.contratista_proveedor_relacionado.nit) if record.contratista_proveedor_relacionado.nit else ''
    #
    # @api.depends('contratista_proveedor_relacionado.cedula')
    # def _compute_cedula(self):
    #     for record in self:
    #         record.documento_identidad = str(record.contratista_proveedor_relacionado.cedula) if record.contratista_proveedor_relacionado.cedula else ''
    #
    # @api.depends('contratista_proveedor_relacionado.cedula_representante')
    # def _compute_cedula_representante_legal(self):
    #     for record in self:
    #         if record.contratista_proveedor_relacionado.cedula_representante:
    #             record.cedula_representante_legal = str(record.contratista_proveedor_relacionado.cedula_representante)
    #         else:
    #             record.cedula_representante_legal = ''




    def action_agregar_contratistas(self):
        """Llama al método 'agregar_todos_contratistas' del modelo 'programapagos'."""
        for record in self:
            # Verifica si el registro tiene un proyecto relacionado
            if record.proyecto_relacionado:
                # Obtener el objeto del programa de pagos relacionado
                programa_pagos = self.env['programapagos'].search([('proyecto_relacionado', '=', record.proyecto_relacionado.id)], limit=1)
                if programa_pagos:
                    # Llamar al método 'agregar_todos_contratistas' del modelo 'programapagos'
                    try:
                        programa_pagos.agregar_todos_contratistas()
                        _logger.info(f"Se ha iniciado la agregación de contratistas para el proyecto {record.proyecto_relacionado.name}.")
                    except Exception as e:
                        _logger.error(f"Error al intentar agregar contratistas: {str(e)}")
                        raise UserError(f"Error al ejecutar el proceso: {str(e)}")
                else:
                    _logger.warning(f"No se encontró el programa de pagos para el proyecto {record.proyecto_relacionado.name}.")
                    raise UserError("No se encontró el programa de pagos para este proyecto.")
            else:
                _logger.warning("No hay proyecto relacionado para esta contratación.")
                raise UserError("Este registro no tiene un proyecto relacionado.")



    # Campo booleano para controlar la visibilidad del botón
    mostrar_boton = fields.Boolean(compute='_compute_mostrar_boton', string="Mostrar Botón")

    @api.depends('otrosi_ids.estado_contratacion')
    def _compute_mostrar_boton(self):
        for record in self:
            # Verificar si algún registro relacionado en otro_si tiene el estado deseado
            record.mostrar_boton = any(
                otrosi.estado_contratacion == 'juridico' for otrosi in record.otrosi_ids
            )

    # 18-11-2024 SAMUEL
    ############################################SAMUEL##################################################################


    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')
    
    # 1. Campo auxiliar para guardar el estado manual del botón
    manual_edit_mode = fields.Boolean(default=False, string="Modo edición manual")

    # 2. El campo is_editable ahora es computado
    is_editable = fields.Boolean(
        string='Es editable', 
        compute='_compute_is_editable', 
        store=True  # Ponlo en True si necesitas buscar/filtrar por este campo, si solo es visual false
    )

    # 3. La lógica que evalúa las dos condiciones
    @api.depends('proyecto_relacionado.is_editable', 'manual_edit_mode')
    def _compute_is_editable(self):
        for record in self:
            # Lógica OR: Es True si el proyecto lo permite O si activamos el modo manual
            parent_allowed = record.proyecto_relacionado.is_editable
            manual_allowed = record.manual_edit_mode
            
            record.is_editable = parent_allowed or manual_allowed

    # 4. El botón ahora cambia el campo auxiliar 'manual_edit_mode'
    def toggle_edit(self):
        for record in self:
            # Invertimos el modo manual
            record.manual_edit_mode = not record.manual_edit_mode
            
        # Refrescamos la vista para que el usuario vea el cambio inmediato
        return {
            'type': 'ir.actions.act_window',
            'name': 'Contrataciones FUV',
            'res_model': 'contrataciones_fuv',
            'res_id': self.id, # Usamos self.id directamente si es un solo registro
            'view_mode': 'form',
            'view_type': 'form',
            'flags': {'form': {'action_buttons': True}},
        }
    ############################################SAMUEL##################################################################

    ############################################SAMUEL##################################################################

    otro_si_ids = fields.One2many('otro_si', 'contratacion_fuv_en_id', string="Otrosí")

  

    ####################################################################################################################################

    fecha_iniciacion_texto = fields.Char('Fecha de iniciación (texto)', compute='_compute_fecha_iniciacion_texto',
                                         store=True)
    fecha_terminacion_texto = fields.Char('Fecha de terminación (texto)', compute='_compute_fecha_terminacion_texto',
                                          store=True)

    def abrir_otro_si(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otros Si',
            'res_model': 'otro_si',
            'view_mode': 'tree,form',
        }


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

  


    estado_contratacion_2 = fields.Selection([
        ('visible', 'Visible'),
        ('juridico_2', 'Jurídico'),
    ], string='Estado Contratación', default='visible')

    # Variable para agregar usuarios

    estado_contratacion = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('juridico', 'Jurídico'),
        ('recursos_humanos', 'Recursos humanos'),
        ('contratado', 'Contratado'),
    ], string='Estado Contratación', default='coordinador', tracking=True)


    # Variable para agregar usuarios

    usuarios_juridico_ids = fields.Many2many(
        'res.users',
        string='Usuarios con acceso jurídico al FC',
        help='Usuarios con acceso a los registros jurídicos.',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_juridico').id)],
        tracking=True,
    )

    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive')  # Campo para vincular con drive.file

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

    def _registrar_trazabilidad_usuarios_juridico(self, previous_user_ids=None):
        previous_users = set(previous_user_ids or [])
        current_users = set(self.usuarios_juridico_ids.ids)
        removed_users_ids = previous_users - current_users
        added_users_ids = current_users - previous_users
        mensajes = []

        for user in self.env['res.users'].browse(list(added_users_ids)):
            email = user.email or 'sin correo'
            mensajes.append(f"<li><b>Agregado:</b> {user.name} ({email})</li>")

        for user in self.env['res.users'].browse(list(removed_users_ids)):
            email = user.email or 'sin correo'
            mensajes.append(f"<li><b>Retirado:</b> {user.name} ({email})</li>")

        if mensajes:
            self.message_post(
                body="<p><b>Cambios en usuarios jurídicos asignados:</b></p><ul>" + "".join(mensajes) + "</ul>",
                subtype_xmlid='mail.mt_note',
            )




    def _share_file_with_new_users_juridico(self, previous_user_ids=None):
        """Comparte la carpeta y, salvo asignación desde pólizas, notifica."""
        if not self.drive_file_id or not self.drive_file_id.folder_id2:
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
        folder_id = self.drive_file_id.folder_id2
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
        previous_users = set(
            previous_user_ids
            if previous_user_ids is not None
            else (self._origin.usuarios_juridico_ids.ids if self._origin else [])
        )
        current_users = set(self.usuarios_juridico_ids.ids)

        # Identificar usuarios eliminados y agregados
        removed_users_ids = previous_users - current_users
        added_users_ids = current_users - previous_users
        notificar_acceso = not (
            self.env.context.get('gestion_asignacion_juridica_polizas')
            and self.env.context.get('notificar_acceso_juridico') is False
        )

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
                if email in existing_emails:
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
                # Compartir carpeta
                if email in existing_emails:
                    _logger.info("El usuario %s ya tiene acceso a la carpeta.", email)
                else:
                    permiso = {
                        'type': 'user',
                        'value': email,
                        'role': 'reader',
                    }
                    if notificar_acceso:
                        folder.InsertPermission(permiso)
                    else:
                        # Pólizas envía su propio correo con plantilla. Google
                        # concede el acceso sin emitir otro aviso de Drive.
                        folder.InsertPermission(
                            permiso, param={'sendNotificationEmails': False}
                        )
                    _logger.info(f"Carpeta compartida con: {email}")

                if not notificar_acceso:
                    # El correo de pólizas informa tanto la asignación como el
                    # acceso documental; no duplicar el aviso del contrato.
                    continue

                # Crear enlaces
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

                # Extraer el ID sin el prefijo 'NewId_'
                form_id = str(self.id).replace("NewId_", "")

                # Aquí, ajustamos el formato del enlace con los parámetros adicionales requeridos
                formulario_url = f"/web#id={form_id}&cids=1&menu_id=144&active_id=16&model=contrataciones_fuv&view_type=form"
                formulario_url = base_url + formulario_url


                proyecto_nombre = self.proyecto_relacionado.name or self.proyecto_relacionado.new_name or 'Proyecto'
                contrato_nombre = self.new_name or 'Contrato'
                remitente = self.env.user
                body_html = f"""
                    <div style="margin: 0; padding: 24px; background-color: #f4f6f8; font-family: Arial, Helvetica, sans-serif; color: #1f2933;">
                        <div style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e5e7eb;">
                            <div style="background-color: #003b7a; padding: 18px 24px; color: #ffffff;">
                                <h2 style="margin: 0; font-size: 18px; font-weight: 700;">Acceso jurídico compartido</h2>
                                <p style="margin: 6px 0 0; font-size: 13px;">Gestión Operativa SGO</p>
                            </div>
                            <div style="padding: 24px;">
                                <p style="margin: 0 0 14px;">Hola <strong>{user.name}</strong>,</p>
                                <p style="margin: 0 0 14px;">
                                    Se te ha compartido el acceso jurídico al contrato
                                    <strong>{contrato_nombre}</strong>.
                                </p>
                                <p style="margin: 0 0 18px;">
                                    Proyecto relacionado:
                                    <strong>{proyecto_nombre}</strong>
                                </p>
                                <div style="margin: 18px 0; padding: 12px 14px; background-color: #eef6ff; border-left: 4px solid #003b7a; border-radius: 4px;">
                                    <strong>Acceso a documentos habilitado.</strong><br/>
                                    Ya puedes consultar la carpeta documental asociada a este contrato desde el formulario en SGO.
                                </div>
                                <div style="margin: 22px 0;">
                                    <a href="{formulario_url}"
                                       style="display: inline-block; padding: 11px 18px; background-color: #003b7a; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 700; margin-right: 8px;">
                                        Ver formulario en SGO
                                    </a>
                                </div>
                                <p style="margin: 18px 0 0; color: #52616b; font-size: 13px;">
                                    Este mensaje confirma que SGO registró tu asignación jurídica para continuar la revisión del FC.
                                </p>
                                <p style="margin: 22px 0 0;">Cordialmente,<br/><strong>SGO</strong></p>
                            </div>
                        </div>
                    </div>
                """

                self.env['mail.mail'].sudo().create({
                    'subject': f'Acceso jurídico compartido: {contrato_nombre}',
                    'email_from': remitente.email_formatted or remitente.email,
                    'email_to': email,
                    'body_html': body_html,
                    'auto_delete': True,
                }).send()

                _logger.info(f"Notificación enviada a: {email}")

            except Exception as e:
                _logger.error(f"Error al compartir carpeta o enviar notificación a {email}: {e}")

    @api.model
    def search(self, args=None, offset=0, limit=None, order=None, count=False):
        """Combina restricciones de acceso por grupo de usuarios y ordenamiento basado en filtros seleccionados"""

        # # 1️⃣ 🔹 Copia de los argumentos para evitar modificar el original
        # if args is None:
        #     args = []
        # else:
        #     args = args.copy()

        # # 2️⃣ 🔹 Aplicar restricciones de acceso según el grupo del usuario
        
        # # --- LÓGICA DE PRIORIDAD PARA PRESUPUESTOS (Ve todo) ---
        # if self.env.user.has_group('Matriz.group_presupuestos'):
        #     pass  # Este grupo (y si se combina con otro rol como Financiero) ve todos los registros, sin restricciones.
        # # --------------------------------------------------------
        
        # elif self.env.user.has_group('Matriz.group_contratacion_financiero'):
        #     # Lógica anterior para Financiero (solo se aplica si NO es Presupuestos)
        #     args.append(('estado_contratacion', 'in', ['financiera', 'contratado']))
            
        # elif self.env.user.has_group('Matriz.group_contratacion_director_juridico'):
        #     args.append(('estado_contratacion', 'in', ['juridico', 'contratado']))
            
        # elif self.env.user.has_group('Matriz.group_contratacion_juridico'):
        #     args.extend([
        #         '&',
        #         ('estado_contratacion', 'in', ['juridico', 'contratado']),
        #         '|',
        #         ('estado_contratacion', '=', 'contratado'),
        #         ('usuarios_juridico_ids', 'in', [self.env.user.id])
        #     ])
            
        # elif self.env.user.has_group('Matriz.group_contratacion_recursos'):
        #     args.append(('estado_contratacion', 'in', ['recursos_humanos', 'contratado']))
            
        # elif self.env.user.has_group('Matriz.group_contratacion_coordinador') or \
        #         self.env.user.has_group('Matriz.group_contratacion_asis_coordinador') or \
        #         self.env.user.has_group('Matriz.group_contratacion_director_fuv'):
        #     pass  # Estos grupos pueden ver todos los registros, sin restricciones adicionales
            
        # else:
        #     args.append(('id', '=', False)) # Usuarios sin acceso no pueden ver nada

        # 3️⃣ 🔹 Aplicar ordenamiento basado en los filtros seleccionados
        if self.env.context.get('order_create_date_desc'):
            order = 'fch_creacion desc'
        elif self.env.context.get('order_create_date_asc'):
            order = 'fch_creacion asc'
        elif self.env.context.get('order_name'):
            order = 'new_name asc'
        elif self.env.context.get('order_name_desc'):
            order = 'new_name desc'

        # 4️⃣ 🔹 Ejecutar búsqueda con restricciones y orden combinado
        return super().search(args, offset=offset, limit=limit, order=order, count=count)



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



    #Campos y funciones especificadas para el modelo reportes
    fecha_de_pago = fields.Date(string='FECHA DE PAGO', related="contrato_ids.fecha_de_pago")
    reportes_ids = fields.One2many('reportes', 'contratista_reportes', string='Reportes contratista')

    nit2 = fields.Char(string="Nit2", tracking=True, store=True)
    d_verificacion = fields.Integer()

    def asignar_nit2_a_todos(self):
        """
        Recorre todos los registros y asigna el valor de 'nit2' basado en 'nit'.
        """
        registros = self.search([])
        for registro in registros:
            if registro.nit:
                registro.nit2 = self._clean_nit_before_dash(registro.nit)
        return True

    @api.onchange('nit')
    def _onchange_nit(self):
        if self.nit:
            # Limpiar y asignar valor a nit2, eliminando el número después del guion
            self.nit2 = self._clean_nit_before_dash(self.nit)

            # Limpiar el campo de caracteres no necesarios para extraer d_verificacion
            nit_cleaned = self.nit.replace('.', '').strip()
            # Buscar el guion o signo menos
            match = re.search(r'[-−](\d+)$', nit_cleaned)
            if match:
                # Extraer el número después del guion o signo menos
                d_verificacion_str = match.group(1)
                try:
                    # Convertir a entero y asignar al campo d_verificacion
                    self.d_verificacion = int(d_verificacion_str)
                except ValueError:
                    # Manejar casos en que la conversión falle
                    self.d_verificacion = False
            else:
                self.d_verificacion = False

    def _clean_nit_before_dash(self, nit):
        """
        Limpia el valor de 'nit' eliminando caracteres no numéricos y todo lo que sigue después del guion o signo menos.
        """
        if nit:
            # Eliminar todo lo que sigue después del guion
            nit_part = re.split(r'[-−]', nit)[0]  # Tomar solo la parte antes del guion
            nit_cleaned = re.sub(r'\D', '', nit_part)  # Eliminar caracteres no numéricos
            return nit_cleaned
        return ''



    #########################################################################################################


    generar_visible_j = fields.Boolean(string='Visible para Generar Informe', default=False)
    generar_visible = fields.Boolean(string='Visible para Generar Informe', default=False)

    gestion_id_3 = fields.One2many('proceso_gestion_tributaria', 'contratacion_fuv_en_id3', string="Otro si")

    def Anexo_tributario(self):
        self.ensure_one()
        contexto_at_posterior = {}
        if self.usa_flujo_contratacion_nuevo:
            # Particularidad autorizada para el flujo nuevo: Presupuesto puede
            # abrir, crear y completar el AT aunque el FC esté en otra etapa.
            # La excepción de seguridad solo admite la escritura técnica de
            # write_date y nunca habilita la edición general del contrato.
            self._validar_gestion_at_posterior_presupuesto()
            contexto_at_posterior = {
                'gestion_at_posterior_presupuesto': True,
            }
            _logger.info(
                '[AT-PRESUPUESTO-FLUJO-NUEVO] Apertura del AT. '
                'fc_id=%s estado=%s usuario_id=%s usuario=%s',
                self.id, self.estado_contratacion,
                self.env.user.id, self.env.user.name,
            )
            self.with_context(**contexto_at_posterior)._marcar_apertura_si_permitido()
        else:
            # El flujo anterior conserva sin cambios su comportamiento actual.
            self._marcar_apertura_si_permitido()

        existing_records = self.env['proceso_gestion_tributaria'].search(
            [('contratacion_fuv_en_id3', '=', self.id)])

        if existing_records:
            return {
                'name': 'Anexo tributario',
                'view_mode': 'tree,form',
                'res_model': 'proceso_gestion_tributaria',
                'type': 'ir.actions.act_window',
                'target': 'current',
                'domain': [('contratacion_fuv_en_id3', '=', self.id)],
                'context': contexto_at_posterior,
            }
        else:
            if self._es_consulta_coordinacion_financiera():
                return {
                    'name': 'Anexo tributario',
                    'view_mode': 'tree,form',
                    'res_model': 'proceso_gestion_tributaria',
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                    'domain': [('contratacion_fuv_en_id3', '=', self.id)],
                    'context': {'default_contratacion_fuv_en_id3': self.id},
                }
            # Obtener valores necesarios
            centro = self.mcb_centro_costo
            fecha = fields.Date.today()
            anio = str(fecha.year)

            # Contar registros anteriores con ese centro y año
            count = self.env['proceso_gestion_tributaria'].search_count([
                ('mcb_centro_costo', '=', centro),
                ('anio_ejecucion', '=', anio)
            ]) + 1

            consecutivo = f"{centro}-{str(count).zfill(3)}-{anio}"

            # Crear el nuevo registro con el consecutivo
            new_record = self.env['proceso_gestion_tributaria'].create({
                'contratacion_fuv_en_id3': self.id,
                'fecha': fecha,
                'consecutivo_unido': consecutivo,
            })

            return {
                'name': 'Anexo tributario',
                'view_mode': 'form',
                'res_model': 'proceso_gestion_tributaria',
                'type': 'ir.actions.act_window',
                'target': 'current',
                'res_id': new_record.id,
                'context': contexto_at_posterior,
            }

    departamentos_y_municipios_ids3 = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id',
                                                     string='Tabla')



    porcentaje_pago2 = fields.Float(related="contrato_ids.porcentaje_pago")
    porcentaje_pago_final = fields.Float(related="contrato_ids.porcentaje_pago_final")
    acomulado_total = fields.Monetary(related="contrato_ids.acomulado_total")
    valor_total_contrato3 = fields.Monetary(related="contrato_ids.valor_total_contrato3")
    acomulado_1 = fields.Monetary(related="contrato_ids.acomulado_1")
    acumulado_por_contratista = fields.Monetary(related="contrato_ids.acumulado_por_contratista")
    contrato_ids = fields.One2many('contrato', 'contratista1', string="Contratos")
    valor_pago_2suma = fields.Float(related="contrato_ids.valor_pago_2suma")
    acumulado_por_contratista_contrato = (fields.Monetary
                                          (string="Suma acumulada por contratista",
                                           related='contrato_ids.acumulado_por_contratista'))




    pago = fields.Float(related="tabla_de_forma_de_pago.pago")



    # Variables enviadas a pagos de proyecto

    nuemor_de_cuotas = fields.Integer(string="Seleccionar número de cuotas", compute='_compute_nuemor_de_cuotas',
                                      inverse='_inverse_nuemor_de_cuotas')
    tabla_de_forma_de_pago = fields.One2many('tabla_forma_de_pago', 'contrataciones_fuv_tabla_id', string='Tabla')
    numero_de_pagos = fields.Integer('tabla_de_forma_de_pago.numero_de_pagos')


    @api.depends('tabla_de_forma_de_pago')
    def _compute_nuemor_de_cuotas(self):
        for contrato in self:
            contrato.nuemor_de_cuotas = len(contrato.tabla_de_forma_de_pago)

    def _inverse_nuemor_de_cuotas(self):
        for contrato in self:
            if contrato.nuemor_de_cuotas >= 0:
                if contrato.nuemor_de_cuotas == 0:
                    # If the new value is 0, remove all lines
                    contrato.tabla_de_forma_de_pago = [(5, 0, 0)] * len(contrato.tabla_de_forma_de_pago)
                else:
                    current_lines = len(contrato.tabla_de_forma_de_pago)
                    new_lines = contrato.nuemor_de_cuotas - current_lines
                    if new_lines > 0:
                        values = [{'contrataciones_fuv_tabla_id': contrato.id} for _ in range(new_lines)]
                        contrato.tabla_de_forma_de_pago = [(0, 0, vals) for vals in values]
                    elif new_lines < 0:
                        # If the new value is less than the current value,
                        # keep the existing lines and remove the extras
                        contrato.tabla_de_forma_de_pago = contrato.tabla_de_forma_de_pago[:contrato.nuemor_de_cuotas]


    cantidad_de_dinero2 = fields.Float(related='tabla_de_forma_de_pago.cantidad_de_dinero')
    total_de_cantidad_de_dinero = fields.Float(string="Monto total", compute='_compute_total_dinero')

    @api.depends('tabla_de_forma_de_pago.cantidad_de_dinero')
    def _compute_total_dinero(self):
        for contrato in self:
            contrato.total_de_cantidad_de_dinero = sum(
                line.cantidad_de_dinero for line in contrato.tabla_de_forma_de_pago)

    total_pago_porcentaje = fields.Float(string="Total % de Pago", compute='_compute_total_pago_porcentaje')
    # 1. Modificamos el campo para que sea tipo "compute"
    warning_message = fields.Char(
        string='Warning Message', 
        compute='_compute_warning_message'
    )

    # 2. Creamos un compute exclusivo para el mensaje
    @api.depends('tabla_de_forma_de_pago.pago')
    def _compute_warning_message(self):
        for contrato in self:
            total_porcentaje = sum(line.pago for line in contrato.tabla_de_forma_de_pago)
            if total_porcentaje > 100:
                contrato.warning_message = "⚠️ El total del porcentaje de pago no puede exceder el 100%."
            else:
                contrato.warning_message = False

    # 3. Tu compute original se queda solo para el número (limpio y sin textos)
    @api.depends('tabla_de_forma_de_pago.pago')
    def _compute_total_pago_porcentaje(self):
        for contrato in self:
            contrato.total_pago_porcentaje = sum(line.pago for line in contrato.tabla_de_forma_de_pago)



    total_porcentaje_tecnico = fields.Float(string="Total porcentaje técnico",
                                            compute='_compute_total_porcentaje_tecnico')

    @api.depends('tabla_de_forma_de_pago.tecnico')
    def _compute_total_porcentaje_tecnico(self):
        for contrato in self:
            contrato.total_porcentaje_tecnico = sum(line.tecnico for line in contrato.tabla_de_forma_de_pago)

    ####################################################################################################



    currency_id = fields.Many2one('res.currency', string='Moneda', tracking=True)

    valor_total_contrato = fields.Monetary('Valor total contrato (PS)', default=0.0, tracking=True)


    total = fields.Monetary('Valor de contrato con otrosí', compute='_compute_total')

    valor_del_otrosi = fields.Monetary('Valor del otrosí', compute='_compute_valor_del_otrosi')

    @api.depends('otro_si_ids.valor_total_contrato')
    def _compute_valor_del_otrosi(self):
        for contrato in self:
            contrato.valor_del_otrosi = sum(otrosi.valor_total_contrato for otrosi in contrato.otro_si_ids)

    def update_valor_otrosi(self):
        self.valor_del_otrosi = sum(otrosi.valor_total_contrato for otrosi in self.otro_si_ids)


    @api.depends('valor_del_otrosi', 'valor_total_contrato')
    def _compute_total(self):
        for contrato in self:
            contrato.total = contrato.valor_del_otrosi + contrato.valor_total_contrato

    id_del_contrato = fields.Char(string='ID del contrato', tracking=True,)


    # #########################################################################Sam reparación del valor mcb --> contratos
    # 1. Campo para la moneda (OBLIGATORIO para campos Monetary)
    # Usamos la moneda de la compañía por defecto
    currency_id = fields.Many2one(
        'res.currency', 
        string="Moneda", 
        default=lambda self: self.env.company.currency_id
    )

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

    saldo_disponible_contratar = fields.Monetary(compute='_compute_saldo_disponible_contratar',
                                                 string='Saldo Disponible a Contratar')

    def action_soporte_recalcular_fondos(self):
        """
        Recalcula los fondos de Contrataciones FUV para todo el proyecto.

        Este botón es una herramienta de reparación para registros antiguos cuyas
        dependencias almacenadas pudieron quedar desactualizadas. No modifica
        valores digitados por el usuario ni recalcula la MCB; únicamente vuelve a
        ejecutar los campos de Contrataciones FUV.
        """
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError("Solo el grupo Soporte puede recalcular los fondos.")
        if not self.proyecto_relacionado:
            raise UserError("La contratación FUV no tiene un proyecto relacionado.")

        proyecto = self.proyecto_relacionado
        # Asegura que los related del proyecto se lean desde los valores actuales
        # de la MCB, sin intentar modificarla desde este botón.
        proyecto.invalidate_recordset([
            'new_total_costos_produccion_N_r',
            'suma_costos_fuv',
            'contratos_vco_resumen',
        ])

        # Recalcular todos los contratos del proyecto, no solo el abierto,
        # porque los saldos son globales y deben coincidir en cada formulario.
        contratos = self.search([
            ('proyecto_relacionado', '=', proyecto.id),
        ])
        contratos._compute_valor_contrato_otrosi()
        contratos._compute_valor_a_contratar()
        contratos._compute_valor_contrato_vco()
        contratos.invalidate_recordset([
            'saldo_disponible_produccion',
            'saldo_disponible_personal_fuv',
            'saldo_disponible_vco',
            'total_gastado_produccion_v2',
        ])
        contratos._compute_saldo_disponible_produccion()
        contratos._compute_saldo_disponible_personal_fuv()
        contratos._compute_saldo_disponible_vco()
        contratos._compute_total_gastado_produccion_v2()

        _logger.info(
            "[SOPORTE-FONDOS] Recalculadas Contrataciones FUV del proyecto_id=%s "
            "por user_id=%s | contratos_fuv_ids=%s",
            proyecto.id,
            self.env.user.id,
            contratos.ids,
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fondos recalculados',
                'message': (
                    'Se actualizaron los saldos de Producción, Costos FUV y '
                    'Contratos VCO del proyecto. La MCB no fue modificada.'
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_soporte_normalizar_flujo(self):
        """
        Normaliza contratos marcados como flujo nuevo que quedaron con estados
        del flujo anterior o en una etapa incompatible. No ejecuta aprobaciones,
        no envia correos y no modifica documentos; solo corrige el estado visible
        para que el responsable continue el flujo desde el punto correcto.
        """
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError("Solo el grupo Soporte puede normalizar el flujo.")

        if not self.usa_flujo_contratacion_nuevo:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sin cambios',
                    'message': 'Este contrato pertenece al flujo anterior. No se normalizo el estado.',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        estado_anterior = self.estado_contratacion
        es_juridica = self.select_persona == 'persona_juridica'
        es_natural = self.select_persona in ['persona_natural', 'contrato_laboral']
        nuevo_estado = False
        motivo = False

        def documento_cargado(campo_id, campo_visible):
            documento = getattr(self, campo_id, False)
            visible = getattr(self, campo_visible, True)
            return bool(documento) or visible is False

        tiene_rpc = documento_cargado('su_cdp_id', 'su_cdp_doc_visible')
        tiene_anexo_presupuesto = (
            documento_cargado('anexo_tributario_id', 'anexo_tributario_doc_visible')
            or documento_cargado(
                'anexo_tributario_juridico_doc_id',
                'anexo_tributario_juridico_doc_visible',
            )
        )
        tiene_gestion_presupuesto = tiene_rpc or tiene_anexo_presupuesto
        tiene_contrato_sin_firmar = (
            documento_cargado('contratos_id', 'contratos_doc_visible')
            or documento_cargado(
                'aprueba_j_h_contrato_doc_id',
                'aprueba_j_h_contrato_doc_visible',
            )
        )
        tiene_contrato_firmado = (
            documento_cargado('contratos_firmado_id', 'contratos_firmado_doc_visible')
            or documento_cargado(
                'aprueba_j_h_contrato_firmado_doc_id',
                'aprueba_j_h_contrato_firmado_doc_visible',
            )
        )

        if estado_anterior == 'financiera':
            if tiene_gestion_presupuesto:
                nuevo_estado = 'presupuesto'
                motivo = (
                    "El contrato estaba en Financiera del flujo anterior, pero "
                    "ya tiene evidencia de RPC o anexo tributario. Se ubica en "
                    "Presupuesto para continuar sin perder lo cargado."
                )
            else:
                nuevo_estado = 'ventanilla'
                motivo = (
                    "El contrato estaba en Financiera del flujo anterior y no "
                    "tiene evidencia presupuestal cargada. En el flujo nuevo "
                    "debe iniciar esa etapa en Ventanilla."
                )
        elif estado_anterior == 'juridico':
            if tiene_contrato_firmado:
                nuevo_estado = 'firma'
                motivo = (
                    "El contrato ya tiene archivo firmado cargado. Se deja en "
                    "Firma para que Coordinacion pueda cerrar con el boton "
                    "Contratar si corresponde."
                )
            elif tiene_contrato_sin_firmar:
                nuevo_estado = 'firma'
                motivo = (
                    "El contrato ya tiene contrato sin firmar cargado. Se deja "
                    "en Firma para que Coordinacion gestione firmas y cargue el "
                    "contrato firmado."
                )
            elif self.usuarios_juridico_ids:
                nuevo_estado = 'juridico_1'
                motivo = (
                    "El contrato fue detectado como flujo nuevo, estaba en "
                    "Juridico del flujo anterior y ya tenia juridico asignado. "
                    "Se normaliza a Juridico para revision."
                )
            else:
                nuevo_estado = 'director_juridico'
                motivo = (
                    "El contrato fue detectado como flujo nuevo, pero estaba en "
                    "Juridico del flujo anterior sin juridico asignado. En el "
                    "flujo nuevo debe pasar primero por Director Juridico."
                )
        elif estado_anterior == 'recursos_humanos':
            if es_juridica:
                nuevo_estado = 'firma' if tiene_contrato_sin_firmar else 'juridico_1'
                motivo = (
                    "La persona juridica no pasa por Recursos Humanos en el "
                    "flujo nuevo. Se normaliza al punto juridico correspondiente."
                )
            elif es_natural and self.aprobacion_recursos_humanos_realizada:
                nuevo_estado = 'juridico_2'
                motivo = (
                    "Recursos Humanos ya figura aprobado. Se normaliza a "
                    "Juridico 2 para continuar con el contrato."
                )
        elif estado_anterior == 'juridico_2' and es_juridica:
            nuevo_estado = 'firma' if tiene_contrato_sin_firmar else 'juridico_1'
            motivo = (
                "La persona juridica no usa Juridico 2 en el flujo nuevo. "
                "Se normaliza al punto juridico correcto."
            )
        elif estado_anterior == 'director_juridico' and self.usuarios_juridico_ids:
            nuevo_estado = 'juridico_1'
            motivo = (
                "El Director Juridico ya asigno usuario juridico. Se sincroniza "
                "el estado a Juridico para que aparezcan los botones correctos."
            )

        if not nuevo_estado:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sin cambios',
                    'message': 'El estado actual no requiere normalizacion para el flujo nuevo.',
                    'type': 'info',
                    'sticky': False,
                }
            }

        vals_normalizacion = {'estado_contratacion': nuevo_estado}
        if nuevo_estado == 'juridico_2':
            vals_normalizacion['estado_contratacion_2'] = 'juridico_2'
        elif nuevo_estado != 'juridico_2':
            vals_normalizacion['estado_contratacion_2'] = False
        if nuevo_estado == 'juridico_1' and es_juridica:
            vals_normalizacion['generar_informe_visible_j'] = True
        elif nuevo_estado == 'juridico_2':
            vals_normalizacion['generar_informe_visible'] = True
        elif nuevo_estado == 'firma':
            if es_juridica:
                vals_normalizacion['generar_informe_visible_j'] = True
            else:
                vals_normalizacion['generar_informe_visible'] = True

        self.with_context(skip_validacion_estado_flujo_nuevo=True).write(vals_normalizacion)
        self.with_context(
            mail_notify_force_send=False,
            mail_create_nosubscribe=True,
            mail_auto_subscribe_no_notify=True,
            mail_notify_noemail=True,
        ).message_post(
            body=(
                "Registro normalizado por Soporte para flujo nuevo: "
                f"{estado_anterior or 'Sin estado'} -> {nuevo_estado}.<br/>"
                f"<strong>Motivo:</strong> {motivo}"
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Flujo normalizado',
                'message': f'Estado actualizado: {estado_anterior} -> {nuevo_estado}.',
                'type': 'success',
                'sticky': False,
            }
        }
    
    
    
    
    
    
    # 2. El campo Monetary (Sin 'related', usamos compute)
    disponible_contrato_vco = fields.Monetary(
        string='Presupuesto VCO',
        currency_field='currency_id',
        compute='_compute_valor_contrato_vco',
        store=True
    )

    @api.depends('proyecto_relacionado.contratos_vco_resumen')
    def _compute_valor_contrato_vco(self):
        for record in self:
            # Usamos el nombre técnico exacto de tu proyecto relacionado
            valor_origen2 = record.proyecto_relacionado.contratos_vco_resumen
            record.disponible_contrato_vco = valor_origen2 if valor_origen2 else 0.0






    # Saldo disponible para VCO desde otrosí
    # 2. Campo de Saldo (El que resta lo que vas gastando)
    saldo_disponible_vco = fields.Monetary(
        compute='_compute_saldo_disponible_vco',
        string='Saldo Disponible VCO',
        tracking=True
    )

    valor_utilizado_vco = fields.Monetary(
        compute='_compute_valor_utilizado_fondos_tablero',
        string='Valor utilizado VCO',
        currency_field='currency_id',
        store=False,
    )

    valor_utilizado_costos_fuv = fields.Monetary(
        compute='_compute_valor_utilizado_fondos_tablero',
        string='Valor utilizado Costos FUV',
        currency_field='currency_id',
        store=False,
    )

    @api.depends(
        'disponible_contrato_vco',
        'saldo_disponible_vco',
        'suma_costos_fuv',
        'saldo_disponible_personal_fuv',
    )
    def _compute_valor_utilizado_fondos_tablero(self):
        for record in self:
            record.valor_utilizado_vco = (
                (record.disponible_contrato_vco or 0.0)
                - (record.saldo_disponible_vco or 0.0)
            )
            record.valor_utilizado_costos_fuv = (
                (record.suma_costos_fuv or 0.0)
                - (record.saldo_disponible_personal_fuv or 0.0)
            )

    @api.depends('disponible_contrato_vco', 'valor_total_contrato', 'valor_contrato_otrosi',
                'select_fondos', 'proyecto_relacionado', 'otro_si_ids.valor_total_contrato', 'otro_si_ids.select_fondos')
    def _compute_saldo_disponible_vco(self):
        for record in self:
            presupuesto_vco = record.disponible_contrato_vco or 0.0

            if not record.proyecto_relacionado:
                record.saldo_disponible_vco = presupuesto_vco
                _logger.debug(
                    "[AUDIT-FONDOS][VCO] fuv_id=%s sin proyecto | presupuesto=%s | saldo=%s",
                    record.id, presupuesto_vco, record.saldo_disponible_vco,
                )
                continue

            # --- 1. CONTRATOS FUV ---
            todos_contratos_vco = self.search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'contratos_vco'),
            ])
            gasto_contratos_vco = sum(todos_contratos_vco.mapped('valor_total_contrato') or [0.0])
            
            # _logger.info("🔍 [VCO] Buscando Contratos: Encontrados %s registros.", len(todos_contratos_vco))
            # _logger.info("💰 [VCO] Subtotal Contratos FUV: %s", gasto_contratos_vco)

            # --- 2. OTROSÍ ---
            todos_otrosi_vco = self.env['otro_si'].search([
                ('contratacion_fuv_en_id.proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_vco')
            ])
            gasto_otrosi_vco = sum(todos_otrosi_vco.mapped('valor_total_contrato') or [0.0])
            
            # _logger.info("🔍 [VCO] Buscando Otrosí: Encontrados %s registros.", len(todos_otrosi_vco))
            # _logger.info("💸 [VCO] Subtotal Otrosí VCO: %s", gasto_otrosi_vco)

            # --- 3. OPERACIÓN FINAL ---
            gasto_total = gasto_contratos_vco + gasto_otrosi_vco
            saldo_final = presupuesto_vco - gasto_total

            # Reintegro certificado por Presupuesto en cierres ya finalizados.
            # Se suma al resultado, sin modificar el presupuesto ni borrar el
            # consumo histórico de los contratos.
            liberados = record._obtener_recursos_liberados_por_fondo()
            saldo_final += liberados['contratos_vco']
            
            record.saldo_disponible_vco = saldo_final

            _logger.debug(
                "[AUDIT-FONDOS][VCO] proyecto_id=%s fuv_id=%s | presupuesto=%s | "
                "contratos_ids=%s contratos_valores=%s contratos_total=%s | "
                "otrosi_ids=%s otrosi_valores=%s otrosi_total=%s | gasto_total=%s | saldo=%s",
                record.proyecto_relacionado.id,
                record.id,
                presupuesto_vco,
                todos_contratos_vco.ids,
                todos_contratos_vco.mapped('valor_total_contrato'),
                gasto_contratos_vco,
                todos_otrosi_vco.ids,
                todos_otrosi_vco.mapped('valor_total_contrato'),
                gasto_otrosi_vco,
                gasto_total,
                saldo_final,
            )
                
                
    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################

    select_fondos = fields.Selection([
        ('es_produccion', 'Producción'),
        ('es_personal_fuv', 'Costos FUV'),
        ('contratos_vco', 'Contratos VCO'),
    ], string='Seleccionar fondo para contratar')

    # Campo para simular si estamos en un contexto donde la póliza está permitida, 
    # pero usaremos el nombre del modelo para la validación directa.
    @api.constrains('saldo_disponible_produccion', 'saldo_disponible_personal_fuv', 
                    'saldo_disponible_vco', 'select_fondos', 'valor_total_contrato')
    def _check_saldo_disponible_suficiente(self):
        for record in self:
            fondo = record.select_fondos
            saldo = 0.0
            nombre = ""
            
            if fondo == 'es_produccion':
                saldo = record.saldo_disponible_produccion
                nombre = "Producción"
            elif fondo == 'es_personal_fuv':
                saldo = record.saldo_disponible_personal_fuv
                nombre = "Personal FUV"
            elif fondo == 'contratos_vco':
                saldo = record.saldo_disponible_vco
                nombre = "Contratos VCO"
            elif fondo == 'es_poliza':
                saldo = record.saldo_disponible_poliza_total
                nombre = "Póliza"
            
            # Bloqueo si el saldo es negativo
            if fondo and saldo < 0:
                raise ValidationError(
                    f"¡Saldo Insuficiente! En el fondo '{nombre}' no hay dinero suficiente. "
                    f"El saldo quedaría en: ${saldo:,.2f}. Por favor reduzca el valor."
                )
            
    @api.onchange('select_fondos')
    def _check_poliza_allowed_onchange(self):
        """
        Detecta si el usuario intenta seleccionar 'Póliza' en el modelo 'contrataciones_fuv'.
        Si detecta la selección, la revierte y muestra un pop-up de advertencia.
        """
        # 1. Detectar el contexto: ¿Estamos en el modelo donde la Póliza NO está permitida?
        # En este caso, el modelo actual es 'contrataciones_fuv'.
        
        if self.select_fondos == 'es_poliza' and self._name == 'contrataciones_fuv':
            # Mostrar pop-up de advertencia (UserWarning)
            warning_msg = "AVISO: La selección de 'Póliza' solo aplica y está permitida en el entorno de Órdenes de Servicio. Por favor, seleccione otro fondo."
            
            # Limpiar el campo seleccionado para evitar que se guarde el valor no permitido
            self.select_fondos = False
            
            return {
                'warning': {
                    'title': "Restricción de Selección de Fondo",
                    'message': warning_msg,
                }
            }

        # Si el usuario selecciona otro valor, o si estamos en otro modelo, no hace nada.


    suma_costos_fuv = fields.Monetary(related="proyecto_relacionado.suma_costos_fuv", string='Personal FUV')


    # Saldo disponible para Personal FUV
    saldo_disponible_personal_fuv = fields.Monetary(
        compute='_compute_saldo_disponible_personal_fuv',
        string='Saldo Disponible Personal FUV',

        tracking=True
    )


    @api.depends(
        'suma_costos_fuv',
        'valor_total_contrato',
        'select_fondos',
        'proyecto_relacionado',
        'otro_si_ids.valor_total_contrato',
        'otro_si_ids.select_fondos',
    )
    def _compute_saldo_disponible_personal_fuv(self):
        for record in self:
            presupuesto_fuv = record.suma_costos_fuv or 0.0

            # Sin proyecto no existen consumos relacionados que descontar.
            if not record.proyecto_relacionado:
                record.saldo_disponible_personal_fuv = presupuesto_fuv
                continue

            # SOLUCIÓN GLOBAL:
            # El fondo de cada otrosí es independiente del fondo de su contrato
            # padre. Por eso se buscan todos los contratos y todos los otrosí
            # del proyecto que consumen Costos FUV. La versión anterior ignoraba
            # un otrosí FUV cuando su contrato padre estaba en Producción.
            contratos_fuv = self.search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_personal_fuv'),
            ])
            otrosi_fuv = self.env['otro_si'].search([
                ('contratacion_fuv_en_id.proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_personal_fuv'),
            ])

            gasto_contratos_fuv = sum(contratos_fuv.mapped('valor_total_contrato'))
            gasto_otrosi_fuv = sum(otrosi_fuv.mapped('valor_total_contrato'))
            gasto_total_fuv = gasto_contratos_fuv + gasto_otrosi_fuv

            saldo_actual_fuv = presupuesto_fuv - gasto_total_fuv
            # Retorna únicamente recursos cuyo contrato inicial usó Costos FUV.
            liberados = record._obtener_recursos_liberados_por_fondo()
            record.saldo_disponible_personal_fuv = (
                saldo_actual_fuv + liberados['es_personal_fuv']
            )

            _logger.debug(
                "[AUDIT-FONDOS][COSTOS-FUV] proyecto_id=%s fuv_id=%s | presupuesto=%s | "
                "contratos_ids=%s contratos_valores=%s contratos_total=%s | "
                "otrosi_ids=%s otrosi_valores=%s otrosi_total=%s | "
                "gasto_total=%s | saldo=%s",
                record.proyecto_relacionado.id,
                record.id,
                presupuesto_fuv,
                contratos_fuv.ids,
                contratos_fuv.mapped('valor_total_contrato'),
                gasto_contratos_fuv,
                otrosi_fuv.ids,
                otrosi_fuv.mapped('valor_total_contrato'),
                gasto_otrosi_fuv,
                gasto_total_fuv,
                record.saldo_disponible_personal_fuv,
            )









    # Saldo disponible para Producción
    total_gastado_produccion_v2 = fields.Monetary(
        string='Total Gastado en Producción (Auditado)',
        compute='_compute_total_gastado_produccion_v2',
        store=False,
        help="Suma real de Bienes, Servicios, Órdenes especiales y Contratos FUV para este proyecto."
    )

            
            
    @api.depends(
        'valor_total_contrato',
        'select_fondos',
        'proyecto_relacionado',
        'otro_si_ids.valor_total_contrato',
        'otro_si_ids.select_fondos',
    )
    def _compute_total_gastado_produccion_v2(self):
        """
        Calcula el valor global utilizado en Producción para el tablero.

        La versión anterior utilizaba ``valor_contrato_otrosi`` para los
        contratos hermanos y podía conservar solamente el primer otrosí en
        caché. Por eso el tablero quedaba, por ejemplo, en 120 millones aunque
        el contrato y sus tres otrosí sumaran 160 millones.

        Esta versión suma directamente todos los otrosí de Producción tanto
        para el registro actual como para cada contrato hermano.
        """
        for record in self:
            if not record.proyecto_relacionado:
                record.total_gastado_produccion_v2 = 0.0
                continue


            bienes = self.env['orden_compra_bienes'].search([
                ('proyecto_relacionado_c', '=', record.proyecto_relacionado.id)
            ])
            total_bienes = sum(bienes.mapped('total'))


            servicios = self.env['orden_compra_servicios'].search([
                ('proyecto_relacionado_s', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion')
            ])
            total_servicios = sum(servicios.mapped(
                lambda s: (s.subtotal or 0.0) + (s.iva or 0.0) + (s.gastos_expedicion or 0.0)
            ))

            ordenes_especiales = self.env['ordenes_especiales'].search([
                ('proyecto_relacionado_s', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion')
            ])
            total_ordenes_especiales = sum(ordenes_especiales.mapped(
                lambda orden: (
                    (orden.subtotal or 0.0)
                    + (orden.iva or 0.0)
                    + (orden.gastos_expedicion or 0.0)
                )
            ))


            hermanos = self.search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion'),
                ('id', '!=', record._origin.id if record._origin else False)
            ])
            # Contratos hermanos: contrato base + TODOS sus otrosí de Producción.
            gasto_fuv_otros = sum(
                (contrato.valor_total_contrato or 0.0)
                + sum(
                    contrato.otro_si_ids.filtered(
                        lambda otrosi: otrosi.select_fondos == 'es_produccion'
                    ).mapped('valor_total_contrato')
                )
                for contrato in hermanos
            )
            

            gasto_fuv_actual = 0.0
            # Contrato abierto: TODOS sus otrosí asignados a Producción.
            otrosi_actual_produccion = record.otro_si_ids.filtered(
                lambda otrosi: otrosi.select_fondos == 'es_produccion'
            )
            if record.select_fondos == 'es_produccion':
                gasto_fuv_actual = (
                    (record.valor_total_contrato or 0.0)
                    + sum(otrosi_actual_produccion.mapped('valor_total_contrato'))
                )

            record.total_gastado_produccion_v2 = (
                total_bienes
                + total_servicios
                + total_ordenes_especiales
                + gasto_fuv_otros
                + gasto_fuv_actual
            )

            _logger.debug(
                "[AUDIT-FONDOS][PRODUCCION-UTILIZADO] proyecto_id=%s fuv_id=%s fondo_actual=%s | "
                "bienes_ids=%s bienes_total=%s | servicios_ids=%s servicios_total=%s | "
                "ordenes_especiales_ids=%s ordenes_especiales_total=%s | "
                "hermanos_ids=%s hermanos_contrato=%s hermanos_otrosi_related=%s "
                "hermanos_total=%s | contrato_actual=%s otrosi_actual_related=%s "
                "actual_total=%s | utilizado_total=%s",
                record.proyecto_relacionado.id,
                record.id,
                record.select_fondos,
                bienes.ids,
                total_bienes,
                servicios.ids,
                total_servicios,
                ordenes_especiales.ids,
                total_ordenes_especiales,
                hermanos.ids,
                hermanos.mapped('valor_total_contrato'),
                [
                    sum(
                        contrato.otro_si_ids.filtered(
                            lambda otrosi: otrosi.select_fondos == 'es_produccion'
                        ).mapped('valor_total_contrato')
                    )
                    for contrato in hermanos
                ],
                gasto_fuv_otros,
                record.valor_total_contrato or 0.0,
                sum(otrosi_actual_produccion.mapped('valor_total_contrato')),
                gasto_fuv_actual,
                record.total_gastado_produccion_v2,
            )
            
            
            
            
            
            
    # --- CAMPOS PARA TRAER LA INFORMACIÓN DE VIÁTICOS ---
    valor_final_presupuesto = fields.Float(compute='obtener_valor_final_presupuesto', string='Ajuste Neto')
    impacto_positivo_viaticos = fields.Float(compute='obtener_valor_final_presupuesto', string='Reembolsos (+)')
    impacto_negativo_viaticos = fields.Float(compute='obtener_valor_final_presupuesto', string='Reintegros (-)')
    
    # NUEVO CAMPO: La Deducción (Gasto Real)
    total_utilizado_viaticos = fields.Float(compute='obtener_valor_final_presupuesto', string='Gasto Real Ejecutado')

    def obtener_valor_final_presupuesto(self):
        for record in self:
            viaticos_ids = self.env['viaticos'].search([
                ('proyecto_relacionado_viaticos', '=', record.proyecto_relacionado.id),
                ('state_viaticos', '=', 'completado')
            ])
            
            if viaticos_ids:
                # 1. Totales de Viáticos
                neto = sum(viaticos_ids.mapped('valor_final_presupuesto'))
                positivo = sum(viaticos_ids.mapped('impacto_positivo_fuv'))
                negativo = sum(viaticos_ids.mapped('impacto_negativo_fuv'))
                
                record.valor_final_presupuesto = neto
                record.impacto_positivo_viaticos = positivo
                record.impacto_negativo_viaticos = negativo
                
                # 2. CÁLCULO DE LA DEDUCCIÓN (Gasto Real)
                # Formula: Anticipos Girados - Ajuste Neto
                # (Si el ajuste es negativo, la matemática hace -- = +, sumando el extra)
                anticipos = record.gasto_anticipos or 0.0
                record.total_utilizado_viaticos = anticipos - neto
            else:
                record.valor_final_presupuesto = 0.0
                record.impacto_positivo_viaticos = 0.0
                record.impacto_negativo_viaticos = 0.0
                record.total_utilizado_viaticos = record.gasto_anticipos or 0.0 # Si no hay viáticos, el gasto real es lo que se giró en anticipos (si es que se giró algo)
        
    @api.depends('proyecto_relacionado')
    def obtener_anticipos(self):
        for record in self:
            # Inicializamos en 0.0 por seguridad si no hay proyecto
            record.gasto_anticipos = 0.0
            
            if record.proyecto_relacionado:
                anticipos_ids = self.env['anticipos'].search([
                    ('proyecto_id', '=', record.proyecto_relacionado.id),
                    ('state', '=', 'completado')
                ])
                #_logger.info("🔍 [Anticipos] Buscando anticipos para proyecto ID %s: Encontrados %s registros.", record.proyecto_relacionado.id, len(anticipos_ids))
                
                # Asignamos el valor directamente, SIN 'return'
                if anticipos_ids:
                    record.gasto_anticipos = sum(anticipos_ids.mapped('valor_anticipo'))
                else:
                    record.gasto_anticipos = 0.0

    # Definición del campo
    gasto_anticipos = fields.Float(
        string='Total Anticipos', 
        compute='obtener_anticipos')
     
            
    saldo_disponible_produccion = fields.Monetary(
        compute='_compute_saldo_disponible_produccion',
        string='Saldo Disponible Producción',
        tracking=True)
    
    @api.depends('saldo_final_disponible', 'valor_a_contratar', 'valor_total_contrato', 'total_bienes', 'total_compras', 'select_fondos', 'proyecto_relacionado', 'otro_si_ids.valor_total_contrato', 'otro_si_ids.select_fondos')
    def _compute_saldo_disponible_produccion(self):
        """
        Calcula el saldo global de Producción.

        Todos los contratos del mismo proyecto deben obtener el mismo saldo.
        Para lograrlo, los otrosí de los contratos hermanos se suman
        directamente y se filtran por ``es_produccion``. Esto evita que el
        resultado cambie según cuál contrato sea el registro actual.
        """
        for record in self:
            if not record.proyecto_relacionado:
                record.saldo_disponible_produccion = record.valor_a_contratar or 0.0
                continue
            saldo_final_disponible = record.saldo_final_disponible or 0.0
            # --- 1. GASTOS FIJOS (Bienes, Servicios, Anticipos y FUVs) ---

            bienes_ids = self.env['orden_compra_bienes'].search([
                ('proyecto_relacionado_c', '=', record.proyecto_relacionado.id)
            ])
            gasto_bienes = sum(bienes_ids.mapped('total'))
            
            servicios_ids = self.env['orden_compra_servicios'].search([
                ('proyecto_relacionado_s', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion')
            ])
            gasto_servicios = sum(servicios_ids.mapped(
                lambda s: (s.subtotal or 0.0) + (s.iva or 0.0) + (s.gastos_expedicion or 0.0)
            ))

            ordenes_especiales_ids = self.env['ordenes_especiales'].search([
                ('proyecto_relacionado_s', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion')
            ])
            gasto_ordenes_especiales = sum(ordenes_especiales_ids.mapped(
                lambda orden: (
                    (orden.subtotal or 0.0)
                    + (orden.iva or 0.0)
                    + (orden.gastos_expedicion or 0.0)
                )
            ))

            # Anticipos (Esto ya descuenta el dinero que salió inicialmente)
            anticipos_ids = self.env['anticipos'].search([
                ('proyecto_id', '=', record.proyecto_relacionado.id),
                ('state', '=', 'completado')
            ])
            gasto_anticipos = sum(anticipos_ids.mapped('valor_anticipo'))
            #_logger.info("🔄 [Producción] Ajuste por Viáticos (Reintegros Negativos): %s", gasto_anticipos)
            
            hermanos_ids = self.search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion'),
                ('id', '!=', record._origin.id if record._origin else False)
            ])
            # Contratos hermanos: valor base + suma completa de sus otrosí
            # asignados al fondo Producción.
            gasto_fuv_historico = sum(
                (contrato.valor_total_contrato or 0.0)
                + sum(
                    contrato.otro_si_ids.filtered(
                        lambda otrosi: otrosi.select_fondos == 'es_produccion'
                    ).mapped('valor_total_contrato')
                )
                for contrato in hermanos_ids
            )

            # Contrato actual: solo consume este fondo cuando está clasificado
            # como Producción; sus otrosí se validan individualmente por fondo.
            valor_padre_prod = record.valor_total_contrato if record.select_fondos == 'es_produccion' else 0.0
            valor_otrosi_prod = sum(record.otro_si_ids.filtered(lambda x: x.select_fondos == 'es_produccion').mapped('valor_total_contrato'))
            gasto_fuv_actual = valor_padre_prod + valor_otrosi_prod

            # --- 2. LA DIFERENCIA DE VIÁTICOS (Reembolso o Reintegro) ---
            viaticos_ids = self.env['viaticos'].search([
                ('proyecto_relacionado_viaticos', '=', record.proyecto_relacionado.id),
                ('state_viaticos', '=', 'completado')
            ])
            # Esta es la diferencia (Si es positiva = Reembolso/Devolución, si es negativa = Reintegro/Gasto extra)
            viaticos_negativo = sum(viaticos_ids.mapped('impacto_negativo_fuv'))
            viaticos_positivo = sum(viaticos_ids.mapped('impacto_positivo_fuv'))


            # --- 3. CÁLCULO FINAL ---
            presupuesto_inicial = record.valor_a_contratar or 0.0
            
            # Sumamos todos los gastos que REALMENTE restan (sin la diferencia de viáticos todavía)
            gastos_totales = (
                gasto_bienes
                + gasto_servicios
                + gasto_ordenes_especiales
                + gasto_fuv_historico
                + gasto_fuv_actual
                + gasto_anticipos
                + saldo_final_disponible
                + viaticos_negativo
            )
            
            # APLICAMOS LA MAGIA: 
            # Saldo = Presupuesto - Gastos + Ajuste
            # Si ajuste_viaticos es POSITIVO (Reembolso), el saldo SUBE.
            # Si ajuste_viaticos es NEGATIVO (Reintegro/Extra), el saldo BAJA.
            if record.select_fondos == 'es_produccion':
                saldo_actual_produccion = (presupuesto_inicial - gastos_totales) + viaticos_positivo
            else:
                saldo_actual_produccion = (
                    presupuesto_inicial
                    - (
                        saldo_final_disponible
                        + gasto_bienes
                        + gasto_servicios
                        + gasto_ordenes_especiales
                        + gasto_fuv_historico
                        + gasto_anticipos
                    )
                    + viaticos_positivo
                )

            # Los cierres conservan el gasto histórico y agregan como ajuste
            # positivo solamente el valor liberado del fondo Producción.
            liberados = record._obtener_recursos_liberados_por_fondo()
            record.saldo_disponible_produccion = (
                saldo_actual_produccion + liberados['es_produccion']
            )

            _logger.debug(
                "[AUDIT-FONDOS][PRODUCCION-SALDO] proyecto_id=%s fuv_id=%s fondo_actual=%s | "
                "presupuesto=%s | bienes=%s servicios=%s ordenes_especiales=%s anticipos=%s | "
                "hermanos_ids=%s hermanos_contrato=%s hermanos_otrosi_related=%s "
                "historico=%s | contrato_actual=%s otrosi_prod_ids=%s "
                "otrosi_prod_valores=%s otrosi_prod_total=%s actual_total=%s | "
                "saldo_poliza=%s viaticos_negativo=%s viaticos_positivo=%s | "
                "gastos_totales=%s saldo_final=%s",
                record.proyecto_relacionado.id,
                record.id,
                record.select_fondos,
                presupuesto_inicial,
                gasto_bienes,
                gasto_servicios,
                gasto_ordenes_especiales,
                gasto_anticipos,
                hermanos_ids.ids,
                hermanos_ids.mapped('valor_total_contrato'),
                [
                    sum(
                        contrato.otro_si_ids.filtered(
                            lambda otrosi: otrosi.select_fondos == 'es_produccion'
                        ).mapped('valor_total_contrato')
                    )
                    for contrato in hermanos_ids
                ],
                gasto_fuv_historico,
                valor_padre_prod,
                record.otro_si_ids.filtered(lambda x: x.select_fondos == 'es_produccion').ids,
                record.otro_si_ids.filtered(lambda x: x.select_fondos == 'es_produccion').mapped('valor_total_contrato'),
                valor_otrosi_prod,
                gasto_fuv_actual,
                saldo_final_disponible,
                viaticos_negativo,
                viaticos_positivo,
                gastos_totales,
                record.saldo_disponible_produccion,
            )
                

    def action_debug_produccion_print(self):
        """ Función disparada por el botón con Log oficial """
        # Mensaje de control inicial
        _logger.info("🚀 BOTÓN PRESIONADO: Iniciando Debug de Producción")
        
        if not self:
            _logger.warning("⚠️ No hay registros en 'self' para procesar.")

        for record in self:
            # Re-calculamos para el log
            otros = self.search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion'),
                ('id', '!=', record._origin.id if record._origin else False)
            ])
            
            hist = sum(otros.mapped(lambda r: (r.valor_total_contrato or 0.0) + (r.total_bienes or 0.0) + (r.total_compras or 0.0) + (r.valor_contrato_otrosi or 0.0)))
            act = (record.valor_total_contrato or 0.0) + (record.total_bienes or 0.0) + (record.total_compras or 0.0) + (record.valor_contrato_otrosi or 0.0)
            
            # --- LOG FORMATEADO ---
            # _logger.info("\n" + "🔍" * 10)
            # _logger.info(f"ID Registro: {record.id}")
            # _logger.info(f"Proyecto: {record.proyecto_relacionado.name}")
            # _logger.info(f"Valor a Contratar: {record.valor_a_contratar}")
            # _logger.info(f"Gasto Histórico: {hist}")
            # _logger.info(f"Gasto Actual: {act}")
            # _logger.info(f"Saldo Calc: {record.saldo_disponible_produccion}")
            # _logger.info("🔍" * 10 + "\n")
            
        return True
    
    
    # Campo para ver el acumulado real de gastos
    gastos_produccion_total = fields.Monetary(
        compute='_compute_gastos_produccion_total',
        string='Total Gastado en Producción',
        help="Suma histórica de todos los contratos de producción de este proyecto."
    )

    @api.depends('valor_total_contrato', 'total_bienes', 'total_compras', 'select_fondos', 'proyecto_relacionado', 'valor_contrato_otrosi')
    def _compute_gastos_produccion_total(self):
        for record in self:
            # 1. Buscamos los otros registros de producción del mismo proyecto
            otros_contratos = self.search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_produccion'),
                ('id', '!=', record._origin.id)
            ])
            
            # 2. Sumamos lo que ya está guardado en la base de datos (Histórico)
            gastado_historico = sum(otros_contratos.mapped(
                lambda r: (r.valor_total_contrato or 0.0) + (r.total_bienes or 0.0) + 
                          (r.total_compras or 0.0) + (r.valor_contrato_otrosi or 0.0)
            ))
            
            # 3. Calculamos lo que se está ingresando en el registro actual
            gastado_actual = (record.valor_total_contrato or 0.0) + \
                             (record.total_bienes or 0.0) + \
                             (record.total_compras or 0.0) + \
                             (record.valor_contrato_otrosi or 0.0)
            
            # 4. Lógica de suma final
            if record.select_fondos == 'es_produccion':
                # Si este registro es producción, el total es: lo de atrás + lo de ahora
                record.gastos_produccion_total = gastado_historico + gastado_actual
            else:
                # Si no, el total gastado en el proyecto sigue siendo solo lo histórico
                record.gastos_produccion_total = gastado_historico
    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################

    ##################################################################################
    ##################################################################################
    ##################################################################################
    ##################################################################################
          


    # Definición del campo (mejor dejarlo store=False para que siempre consulte lo último)
    gastos_poliza_externo = fields.Monetary(
        compute='_compute_gastos_poliza_externo', 
        string='Gastos Póliza (Desde Órdenes)'
    )

    @api.depends('proyecto_relacionado') # Este es el campo 'puente' en tu modelo actual
    def _compute_gastos_poliza_externo(self):
        for record in self:
            # Verificamos que tengamos un proyecto seleccionado para buscar
            if record.proyecto_relacionado:
                # 🔍 Entramos al modelo 'orden_compra_servicios'
                ordenes_externas = self.env['orden_compra_servicios'].search([
                    ('proyecto_relacionado_s', '=', record.proyecto_relacionado.id),
                    ('select_fondos', '=', 'es_poliza')
                ])
                
                # Sumamos el total acumulado usando la fórmula: 
                # $Total = \sum (Subtotal + IVA + Gastos\ Expedicion)$
                total_acumulado = sum(ordenes_externas.mapped(
                    lambda o: (o.subtotal or 0.0) + (o.iva or 0.0) + (o.gastos_expedicion or 0.0)
                ))
                
                record.gastos_poliza_externo = total_acumulado
            else:
                record.gastos_poliza_externo = 0.0




    #Poliza total y disponible a contratar póliza total
    # Definición de campos (manteniendo tu snippet original)
    poliza_total = fields.Monetary(related="proyecto_relacionado.poliza_total", string='Valor póliza total')
    saldo_disponible_poliza_total = fields.Monetary(compute='_compute_saldo_disponible_contratar_poliza_total',
                                                string='Saldo Disponible a contratar (póliza)', store=False, tracking=True)


    @api.depends('poliza_total', 'valor_total_contrato', 'total_bienes', 'total_compras', 'select_fondos', 'proyecto_relacionado', 'valor_contrato_otrosi')
    def _compute_saldo_disponible_contratar_poliza_total(self):
        for record in self:
            
            otros_contratos = self.search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('select_fondos', '=', 'es_poliza'),
                ('id', '!=', record._origin.id)
            ])
            
            gastado_historico = sum(otros_contratos.mapped(lambda r: r.valor_total_contrato + r.total_bienes + r.total_compras + r.valor_contrato_otrosi))
            gastado_actual = record.valor_total_contrato + record.total_bienes + record.total_compras + record.valor_contrato_otrosi
            
            if record.select_fondos == 'es_poliza':
                record.saldo_disponible_poliza_total = record.poliza_total - (gastado_historico + gastado_actual)
            else:
                record.saldo_disponible_poliza_total = record.poliza_total - gastado_historico

    ##################################################################################
    ##################################################################################
    # 1. Definición del campo de Saldo Final
    saldo_final_disponible = fields.Monetary(
        string='Saldo Final Disponible',
        compute='_compute_saldo_final_disponible',
        store=False, # Al depender de un valor externo (env), es mejor que sea volátil
        help="Muestra el valor de la póliza menos el total de órdenes de servicio."
    )

    @api.depends('poliza_total', 'gastos_poliza_externo')
    def _compute_saldo_final_disponible(self):
        """
        Calcula la resta simple:
        Saldo = Póliza Total - Gastos Acumulados
        """
        for record in self:
            # Aseguramos que los valores sean numéricos (evitamos errores si están vacíos)
            total_poliza = record.poliza_total or 0.0
            gastos_acumulados = record.gastos_poliza_externo or 0.0
            
            # Realizamos la resta
            record.saldo_final_disponible = total_poliza - gastos_acumulados
    ##################################################################################
    ##################################################################################
    total_compras = fields.Monetary('Valor a contratar', related="proyecto_relacionado.total_compras")
    total_bienes = fields.Monetary('Valor a contratar', related="proyecto_relacionado.total_bienes")
    @api.depends('valor_a_contratar', 'suma_valor_total_contrato', 'total_compras', 'total_bienes')
    def _compute_saldo_disponible_contratar(self):
        for record in self:
            record.saldo_disponible_contratar = (
                    record.valor_a_contratar - (
                    record.suma_valor_total_contrato + record.total_bienes + record.total_compras))

    programapagos_ids = fields.One2many('programapagos', 'contratista1', string='Pagos')


    suma_valor_total_contrato = fields.Monetary(compute='_compute_suma_valor_total_contrato', string='Suma del valor total del contrato')
    
    @api.depends('total', 'proyecto_relacionado')
    def _compute_suma_valor_total_contrato(self):
        for record in self:
            # Obtener todos los registros en el modelo 'contrataciones_fuv' relacionados con el mismo proyecto
            registros_contrataciones_fuv = self.env['contrataciones_fuv'].search([('proyecto_relacionado', '=',
                                                                                   record.proyecto_relacionado.id)])

            # Calcular la suma de los valores de 'valor_total_contrato' para el proyecto actual
            suma_valor_total_contrato = sum(registro.total for registro in registros_contrataciones_fuv)

            # Actualizar el campo 'suma_valor_total_contrato' con la suma calculada
            record.suma_valor_total_contrato = suma_valor_total_contrato

    contratacion_ids = fields.One2many('formulario_cdp', 'proyecto_relacionado_cdp', string="RPC")

    hiden = fields.Char(string='hiden')
    contratacion2_ids = fields.One2many('proceso_gestion_tributaria', 'proyecto_relacionado_anexo', string="RPC")
    anexo_tributario1 = fields.Binary(string='Anexo tributario', widget='attachment', tracking=True)
    cdp = fields.Binary(string='RPC', widget='attachment', tracking=True)



    relacion_ids = fields.One2many('contrataciones_fuv_relacion', 'fuente_id', string='Relación de Contrataciones')


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
        return super(Contrataciones_Fuv, self).unlink()



    asignar_riesgo = fields.Selection(
        [('0', '0'),
         ('1', '1'),
         ('2', '2'),
         ('3', '3'),
         ('4', '4'),
         ('5', '5')],
        string="Asignar Riesgo",
        default='0', tracking=True
    )

    def Formulario_cdp_new(self):
        self._marcar_apertura_si_permitido()

        # Verificar si ya hay registros para este proyecto
        existing_records = self.env['formulario_cdp'].search(
            [('proyecto_relacionado_cdp', '=', self.id)])

        if existing_records:
            # Si hay registros, abrir la vista de lista (tree view) para este proyecto
            return {
                'name': 'Registros formularios CDP.',
                'view_mode': 'tree,form',
                'res_model': 'formulario_cdp',
                'type': 'ir.actions.act_window',
                'target': 'current',
                'domain': [('proyecto_relacionado_cdp', '=', self.id)],
            }
        else:
            if self._es_consulta_coordinacion_financiera():
                return {
                    'name': 'Registros formularios CDP.',
                    'view_mode': 'tree,form',
                    'res_model': 'formulario_cdp',
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                    'domain': [('proyecto_relacionado_cdp', '=', self.id)],
                    'context': {'default_proyecto_relacionado_cdp': self.id},
                }
            # Si no hay registros, abrir el formulario para crear uno nuevo
            return {
                'name': 'Formulario CDP',
                'view_mode': 'form',
                'res_model': 'formulario_cdp',
                'type': 'ir.actions.act_window',
                'target': 'current',
                'context': {'default_proyecto_relacionado_cdp': self.id},
            }

        ############################################################################################################

    @api.depends('documento_identidad_pn', 'rut_pn', 'hoja_vida_pn', 'certificados_estudio',
                 'certificados_laborales', 'certificado_arl', 'certificacion_salud', 'certificacion_pension',
                 'certificacion_examen_preocupacional', 'certificacion_contraloria', 'certificacion_procuraduria',
                 'certificacion_policia_judicial', 'certificacion_medidas_cautelares', 'certificacion_inhabilidades',
                 'certificacion_bancaria',
                 'declaracion_juramentada')
    def _compute_show_aprueba_coordinador(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.documento_identidad_pn, record.rut_pn, record.hoja_vida_pn, record.certificados_estudio,
                record.certificados_laborales, record.certificado_arl, record.certificacion_salud,
                record.certificacion_pension,
                record.certificacion_examen_preocupacional, record.certificacion_contraloria,
                record.certificacion_procuraduria,
                record.certificacion_policia_judicial, record.certificacion_medidas_cautelares,
                record.certificacion_inhabilidades,
                record.certificacion_bancaria,
                record.declaracion_juramentada
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_coordinador = all_approved

    show_aprueba_coordinador = fields.Boolean(
        string='Mostrar botón de aprueba_coordinador',
        compute='_compute_show_aprueba_coordinador',
    )

    ############################################################################################################

    @api.depends('documento_identidad_pn', 'rut_pn', 'hoja_vida_pn', 'certificados_estudio',
                 'certificados_laborales', 'certificado_arl', 'certificacion_salud', 'certificacion_pension',
                 'certificacion_examen_preocupacional', 'certificacion_contraloria', 'certificacion_procuraduria',
                 'certificacion_policia_judicial', 'certificacion_medidas_cautelares', 'certificacion_inhabilidades',
                 'certificacion_bancaria',
                 'declaracion_juramentada')
    def _compute_show_aprueba_coordinador(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.documento_identidad_pn, record.rut_pn, record.hoja_vida_pn, record.certificados_estudio,
                record.certificados_laborales, record.certificado_arl, record.certificacion_salud,
                record.certificacion_pension,
                record.certificacion_examen_preocupacional, record.certificacion_contraloria,
                record.certificacion_procuraduria,
                record.certificacion_policia_judicial, record.certificacion_medidas_cautelares,
                record.certificacion_inhabilidades,
                record.certificacion_bancaria,
                record.declaracion_juramentada
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_coordinador = all_approved

    ###########################################################################

    @api.depends('cert_policia_judicial', 'cert_inhabilidades', 'resolucion_facturacion', 'resolucion_facturacion_ju','resolucion_facturacion_ju',
                 'experiencia_laboral',
                 'certificado_pago_parafiscales', 'planilla_seguridad_social',
                 'certificacion_examen_preocupacional', 'certificacion_contraloria', 'certificacion_procuraduria',
                 'certificado_bancario', 'camara_comercio', 'anexo_tributario_juridico',
                 'propuesta_pj', 'rut_pj', 'rup', 'hoja_vida_juridica', 'cert_contraloria', 'cert_procuraduria',
                 'cedula_rep_legal',
                 'hoja_vida_rep_legal', 'cert_contraloria_rep_legal', 'cert_procuraduria_rep_legal')
    def _compute_show_aprueba_coordinador_j(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.cert_policia_judicial, record.cert_inhabilidades, record.resolucion_facturacion,
                record.resolucion_facturacion_ju,
                record.experiencia_laboral,
                record.certificado_pago_parafiscales, record.planilla_seguridad_social,
                record.certificado_bancario, record.camara_comercio, record.anexo_tributario_juridico,
                record.propuesta_pj, record.rut_pj, record.rup,
                record.hoja_vida_juridica, record.cert_contraloria, record.cert_procuraduria,
                record.cedula_rep_legal, record.hoja_vida_rep_legal, record.cert_contraloria_rep_legal,
                record.cert_procuraduria_rep_legal,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_coordinador_j = all_approved

    show_aprueba_coordinador_j = fields.Boolean(
        string='Mostrar botón de aprueba_coordinador',
        compute='_compute_show_aprueba_coordinador_j',
    )

    #####################################################################
    @api.depends('aprueba_f_rutt', 'aprueba_f_resolucion_facturacion_ju', 'aprueba_f_l_juramentada',
                 'aprueba_f_l_bancaria',)
    def _compute_show_aprueba_financiera(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_f_resolucion_facturacion_ju, record.aprueba_f_rutt, record.aprueba_f_l_bancaria, record.aprueba_f_l_juramentada,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_financiera = all_approved

    show_aprueba_financiera = fields.Boolean(
        string='Mostrar botón de aprueba_financiera',
        compute='_compute_show_aprueba_financiera',
    )

    ############################################################################
    @api.depends('aprueba_f_c_bancario',
                 'aprueba_f_rut','aprueba_f_resolucion_facturacion',)
    def _compute_show_aprueba_financiera_j(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_f_c_bancario,
                record.aprueba_f_rut,record.aprueba_f_resolucion_facturacion,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_financiera_j = all_approved

    show_aprueba_financiera_j = fields.Boolean(
        string='Mostrar botón de aprueba_financiera',
        compute='_compute_show_aprueba_financiera_j',
    )

    ######################################################################
    @api.depends('aprueba_j_h_doc', 'aprueba_j_h_hv', 'aprueba_j_h_est',
                 'aprueba_j_h_lab', 'aprueba_j_h_arl', 'aprueba_j_h_salud', 'aprueba_j_h_pension',
                 'aprueba_j_h_exm_prec', 'aprueba_j_h_contraloria', 'aprueba_j_h_procuraduria',
                 'aprueba_j_h_policia_judicial', 'aprueba_j_h_medidas_cautelares', 'aprueba_j_h_inhabilidades',
                 'aprueba_j_h_tributario')
    def _compute_show_aprueba_juridico(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_j_h_doc, record.aprueba_j_h_hv,
                record.aprueba_j_h_est, record.aprueba_j_h_lab, record.aprueba_j_h_arl, record.aprueba_j_h_salud,
                record.aprueba_j_h_pension, record.aprueba_j_h_exm_prec, record.aprueba_j_h_contraloria,
                record.aprueba_j_h_procuraduria, record.aprueba_j_h_policia_judicial,
                record.aprueba_j_h_medidas_cautelares,
                record.aprueba_j_h_inhabilidades,
                record.aprueba_j_h_tributario,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_juridico = all_approved

    show_aprueba_juridico = fields.Boolean(
        string='Mostrar botón de aprueba_juridico',
        compute='_compute_show_aprueba_juridico',
    )

    ##########################################################################
    @api.depends('aprueba_j_h_policia_judicial_', 'aprueba_j_h_inhabilidades_',
                 'aprueba_j_h_exp_laboral_',
                 'aprueba_j_h_para_fiscales', 'aprueba_j_h_seguridad_social_',
                 'aprueba_j_h_c_comercio_', 'aprueba_j_h_anexo_tributario_j',
                 'aprueba_j_h_propuesta_', 'aprueba_j_h_rup_',
                 'aprueba_j_h_hjv_juridica_', 'aprueba_j_h_c_contraloriaa_', 'aprueba_j_h_cprocuraduria_',
                 'aprueba_j_h_cedula_r_', 'aprueba_j_h_hjv_r', 'aprueba_j_h_contraloria_rep_lega',
                 'aprueba_j_h_procuraduria_rep_legal','aprueba_j_h_contrato', )
    def _compute_show_aprueba_juridico_j(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_j_h_policia_judicial_, record.aprueba_j_h_inhabilidades_,
                record.aprueba_j_h_exp_laboral_, record.aprueba_j_h_para_fiscales, record.aprueba_j_h_seguridad_social_,
                record.aprueba_j_h_c_comercio_,record.aprueba_j_h_contrato,
                record.aprueba_j_h_anexo_tributario_j, record.aprueba_j_h_propuesta_,
                record.aprueba_j_h_rup_, record.aprueba_j_h_hjv_juridica_, record.aprueba_j_h_c_contraloriaa_,
                record.aprueba_j_h_cprocuraduria_, record.aprueba_j_h_cedula_r_, record.aprueba_j_h_hjv_r,
                record.aprueba_j_h_contraloria_rep_lega, record.aprueba_j_h_procuraduria_rep_legal,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_juridico_j = all_approved

    show_aprueba_juridico_j = fields.Boolean(
        string='Mostrar botón de aprueba_juridico',
        compute='_compute_show_aprueba_juridico_j',
    )

    ######################################################################
    @api.depends('aprueba_r_arl_', 'aprueba_r_salud_', 'aprueba_r_pension_', )
    def _compute_show_aprueba_recursos_humanos(self):
        for record in self:
            # Verifica si todas las variables están marcadas como aprobadas
            all_approved = all([
                record.aprueba_r_arl_, record.aprueba_r_salud_, record.aprueba_r_pension_,
            ])

            # Establece el valor del campo que controla la visibilidad del botón
            record.show_aprueba_recursos_humanos = all_approved

    show_aprueba_recursos_humanos = fields.Boolean(
        string='Mostrar botón de aprueba_recursos_humanos',
        compute='_compute_show_aprueba_recursos_humanos',
    )
    ######################################################################


    aprobacion_coordinador_realizada = fields.Boolean(string='Aprobación de Coordinador Realizada', default=False)

##############################################################################################

    users_with_access_financiero = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto:',
                                         related='proyecto_relacionado.users_with_access_financiero')
    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto:', related='proyecto_relacionado.users_with_access')
    creator_user_id = fields.Many2one('res.users', string='Creador del proyecto',
                                      related='proyecto_relacionado.creator_user_id', store=True)
#############################################################################################

    # Botones del footer para aprobación



    estado_contratacion_nun = fields.Selection([
        ('1', 'Coordinador'),
        ('2', 'Juridico'),
        ('3', 'Recursos humanos'),
    ], string='Estado Contratación', default='1', tracking=True)






    #################################################################################################################

    generar_informe_visible = fields.Boolean(string='Visible para Generar Informe', default=False)


    # Botones del footer para aprobación




    estado_contratacion_nun_juridica = fields.Selection([
        ('1', 'Coordinador'),
        ('2', 'Juridico'),
    ], string='Estado Contratación', default='1', tracking=True)



    ###############################################################################################################

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
                    f"del año {anio_en_letras} ({anio})."
                )
            else:
                record.fecha_con_formato = ''




    nombre_proyecto = fields.Char(string='Nombre del proyecto', related='proyecto_relacionado.name', readonly=False,
                                  tracking=True)

    objeto_contrato = fields.Text(string='Objeto del contrato', tracking=True)
    Justificacion_de_la_solicitud = fields.Text(string='Justificación de la solicitud', tracking=True)

    nombre_solicitante3 = fields.Many2one(
        'res.users',
        string='Nombre de quien solicita',
        help='Seleccione al Nombre de quien solicita de la revisión',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)],
    )


    dependencia_area_solicitante = fields.Selection(
        selection=[
            ('productividad', 'Productividad'),
            ('regalias', 'Regalías'),
            ('tecnologia', 'Tecnología'),
            ('estructuracion', 'Estructuración'),
            ('financiera', 'Financiera'),
            ('juridica', 'Jurídica'),
            ('administrativa', 'Administrativa'),
        ],
        string='Dependencia - Área Solicitante',
        tracking=True,
        required=True
    )




    def guardar_documento_adjunto(self, base64_documento):
        # Decodificar la cadena Base64 a datos binarios
        documento_binario = base64.b64decode(base64_documento)

        # Guardar el documento como un adjunto en el registro actual
        self.write({'informe_adjunto': base64_documento})

        return True

    def _nombre_archivo_contrato_generado(self, nombre_base=None):
        partes = []
        if self.id_del_contrato:
            partes.append(self.id_del_contrato.strip())
        partes.append((nombre_base or self.new_name or self.name or 'Informe_Contrataciones_FUV').strip())
        nombre_limpio = ' '.join(partes).replace('&', 'y').strip()
        return f"{nombre_limpio}.docx"


    def render_informe_template(self):
        # Obtener la ruta del directorio actual
        current_directory = get_module_resource('Matriz', 'static/src/docx')

        # Cargar la plantilla desde el archivo .docx utilizando python-docx-template
        template_path = os.path.join(current_directory, "plantilla_contrato.docx")
        template = DocxTemplate(template_path)

        # Obtener el contexto de Odoo
        context = {'docs': self}

        # Renderizar la plantilla con los datos actuales del registro
        template.render(context)

        # Guardar el resultado en un nuevo archivo .docx
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_plantilla_contrato.docx")
        template.save(output_path)

        return output_path

    def render_informe_template_laboral(self):
        # Obtener la ruta del directorio actual
        current_directory = get_module_resource('Matriz', 'static/src/docx')

        # Cargar la plantilla desde el archivo .docx utilizando python-docx-template
        template_path = os.path.join(current_directory, "plantilla_contrato_laboral.docx")
        template = DocxTemplate(template_path)

        # Obtener el contexto de Odoo
        context = {'docs': self}

        # Renderizar la plantilla con los datos actuales del registro
        template.render(context)

        # Guardar el resultado en un nuevo archivo .docx
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_plantilla_laboral.docx")
        template.save(output_path)

        return output_path


    def generar_informe_docx(self):
        record_id = self.id  # ID del registro en contexto

        # Verificar la selección y elegir la plantilla correcta
        if self.select_persona == "persona_natural":
            template_rendered = self.render_informe_template()
        elif self.select_persona == "contrato_laboral":
            template_rendered = self.render_informe_template_laboral()
        else:
            template_rendered = self.render_informe_template()

        # Crear instancia de documento vacío
        documento = Document()
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el docx renderizado como adjunto
        with open(template_rendered, 'rb') as template_file:
            self.write({
                'informe_adjunto': base64.b64encode(template_file.read()),
                'generar_visible_j': True,
                'generar_v': True
            })

        nombre_final = self._nombre_archivo_contrato_generado()

        return {
            'name': nombre_final,
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}'.format(
                record_id, nombre_final
            ),
            'target': 'self',
        }



    # General contrato persona juridica
    def render_informe_template_juridica(self):
        # Obtener la ruta del directorio actual
        current_directory = get_module_resource('Matriz', 'static/src/docx')

        # Cargar la plantilla desde el archivo .docx utilizando python-docx-template
        template_path = os.path.join(current_directory, "plantilla_contrato_juridica.docx")
        template = DocxTemplate(template_path)

        # Obtener el contexto de Odoo
        context = {'docs': self}

        # Renderizar la plantilla con los datos actuales del registro
        template.render(context)

        # Guardar el resultado en un nuevo archivo .docx
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_plantilla_contrato_juridica.docx")
        template.save(output_path)

        return output_path

    #Pendiente por ajustar el self.write({'generar_vi': True}) para recargar solo sin cargar pagina
    def generar_informe_persona_juridica_docx(self):
        record_id = self.id
        # El renderizado usa el nombre original, así que dentro sale con & (si usaste | e)
        template_rendered = self.render_informe_template_juridica()

        # Crear instancia de documento (Buffer)
        documento = Document()
        buffer = BytesIO()
        documento.save(buffer)

        # 1. Guardar el archivo renderizado y actualizar banderas en un solo paso
        with open(template_rendered, 'rb') as template_file:
            self.write({
                'informe_adjunto': base64.b64encode(template_file.read()),
                'generar_visible': True,
                'generar_vi': True
            })

        nombre_final = self._nombre_archivo_contrato_generado()

        return {
            'name': nombre_final,
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=contrataciones_fuv&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}'.format(
                record_id, nombre_final
            ),
            'target': 'self',
        }

    informe_adjunto = fields.Binary(string='Adjunto del informe', readonly=True, tracking=True)

    fch_creacion = fields.Datetime(string='Fecha creacion', copy=False, default=lambda self: fields.Datetime.now(),
                                   tracking=True)
    name = fields.Char(string='Nombre', related="new_name", readonly=False)


###############################################################################

    ###############################################################################



    def Guardar(self):
        self.ensure_one()
        self.write({'name': self.new_name})
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    proyecto = fields.Many2one('contrataciones_fuv', string="Contrato", invisible=True, tracking=True)
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

    # Columna izquierda
    contrato_nuevo = fields.Boolean('Contrato nuevo', tracking=True)
    prorroga = fields.Boolean('Prórroga', readonly=True, tracking=True)

    suspension = fields.Boolean('Suspensión', readonly=True, tracking=True)
    adicion = fields.Boolean('Adición', readonly=True, tracking=True)
    reinicio = fields.Boolean('Reinicio', readonly=True, tracking=True)

    justificacion_solicitud = fields.Text('Justificación de la solicitud', tracking=True)

    # Fecha va en la columna derecha
    fecha_solicitud = fields.Date('Fecha de solicitud', tracking=True)


    tipo_contrato = fields.Selection([
        ('orden_servicios', 'Orden de servicios / Orden de compra'),
        ('prestacion_servicios', 'Prestación de servicios - adquisición de bienes'),
        ('laboral', 'Laboral')],
        string='Tipo de contrato', tracking=True)


    tipo_contrato_id = fields.Many2one(
        'tipo.contrato',
        string='Tipo de contrato',
        tracking=True,
    )


    consecutivo_id = fields.One2many('consecutivo', 'contratatos_id', string='Relacionada')
    valor_en_letras = fields.Text('Valor en letras', compute='_compute_valor_en_letras', tracking=True)

    @api.depends('valor_total_contrato')
    def _compute_valor_en_letras(self):
        for record in self:
            valor_en_letras = num2words(record.valor_total_contrato, lang='es')
            record.valor_en_letras = valor_en_letras.upper()

    # Columna izquierda

    
    # Campos de fecha originales
    fecha_iniciacion = fields.Date(
        'Fecha de iniciación',
        tracking=True,
        required=True
    )
    fecha_terminacion = fields.Date(
        'Fecha de terminación',
        tracking=True,
        required=True
    )

    # Campos para almacenar las fechas formateadas
    fecha_iniciacion_letras = fields.Char(
        'Fecha de iniciación en letras',
        compute='_compute_fecha_iniciacion_letras',
        store=True
    )
    fecha_terminacion_letras = fields.Char(
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
            record.fecha_iniciacion_letras = self._formatear_fecha_simple(record.fecha_iniciacion)

    @api.depends('fecha_terminacion')
    def _compute_fecha_terminacion_letras(self):
        for record in self:
            record.fecha_terminacion_letras = self._formatear_fecha_simple(record.fecha_terminacion)


    alerta_enviada = fields.Boolean('Alerta 15 días Enviada', default=False)
    alerta_30_dias_enviada = fields.Boolean('Alerta 30 días Enviada', default=False)
    creador_id = fields.Many2one('res.users', string='Responsable', default=lambda self: self.env.user)


    @api.model
    def cron_verificar_alertas_fechas(self):
        contratos = self.search([('fecha_terminacion', '!=', False)])
        hoy = fields.Date.today()

        for contrato in contratos:
            dias_restantes = (contrato.fecha_terminacion - hoy).days
            _logger.info(
                f"Contrato: {contrato.name} | Fecha fin: {contrato.fecha_terminacion} | Días restantes: {dias_restantes}")

            if dias_restantes == 30:
                contrato.alerta_enviada = 'Faltan 30 días para la finalización del contrato.'
            elif dias_restantes == 15:
                contrato.alerta_enviada = 'Faltan 15 días para la finalización del contrato.'
            elif dias_restantes == 5:
                contrato.alerta_enviada = 'Faltan 5 días para la finalización del contrato.'
            else:
                contrato.alerta_enviada = ''


    duracion = fields.Char('Duración', compute='_compute_duracion', store=True, tracking=True)
    
    @api.depends('fecha_iniciacion', 'fecha_terminacion')
    def _compute_duracion(self):
        for record in self:
            if record.fecha_iniciacion and record.fecha_terminacion:
                fecha_iniciacion = record.fecha_iniciacion
                fecha_terminacion_inclusiva = record.fecha_terminacion + timedelta(days=1)
                diferencia = relativedelta(fecha_terminacion_inclusiva, fecha_iniciacion)

                # Construir la duración en formato legible
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

                # Calcular la diferencia total en días
                diferencia_dias = (fecha_terminacion_inclusiva -record.fecha_iniciacion).days

                # Enviar alerta a los 30 días
                if diferencia_dias == 30 and not record.alerta_30_dias_enviada and record.id:
                    record._enviar_alerta(30)
                    record.alerta_30_dias_enviada = True

                # Enviar alerta a los 15 días
                if diferencia_dias == 15 and not record.alerta_enviada and record.id:
                    record._enviar_alerta(15)
                    record.alerta_enviada = True

                if diferencia_dias == 5 and not record.alerta_enviada and record.id:
                    record._enviar_alerta(5)
                    record.alerta_enviada = True

            else:
                record.duracion = ''


    def _enviar_alerta(self, dias):
        self._onchange_fecha()
        self._send_alert_email(dias)

    def _onchange_fecha(self):
        if self.fecha_iniciacion and self.fecha_terminacion:
            activity_type = self.env['mail.activity.type'].search([('name', '=', 'Correo electrónico')], limit=1)

            if activity_type:
                contrataciones = self.env['contrataciones_fuv'].search([])
                for contratacion in contrataciones:
                    if not contratacion.activity_type_id:
                        contratacion.with_context(skip_fc_activity_type_security=True).activity_type_id = activity_type.id

                self.message_notify(
                    body="Se ha creado un Correo electrónico para las fechas proporcionadas en todos los registros.",
                    partner_ids=[self.env.user.partner_id.id],
                )


    name_final = fields.Char(string="Nombre final")

    def _send_alert_email(self, dias):
        for record in self:
            # Obtener nombre final
            if record.new_name:
                record.name_final = record.new_name
            elif record.contratista_proveedor_relacionado and record.contratista_proveedor_relacionado.name:
                record.name_final = record.contratista_proveedor_relacionado.name
            else:
                record.name_final = 'Nombre no disponible'

            coordinadores = record.proyecto_relacionado.users_with_access.filtered(
                lambda user: record.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id
            )

            if not coordinadores:
                raise UserError("No hay coordinadores en el grupo 'Contratación Coordinador' con acceso al proyecto.")

            subject = f'⚠️ Alerta: El contrato {record.name_final} del proyecto {record.proyecto_relacionado.new_name} está próximo a expirar en {dias} días'

            base_url = record.env['ir.config_parameter'].sudo().get_param('web.base.url')
            id_sin_prefijo = str(record.id).replace('NewId_', '')
            formulario_url = f'/web#id={id_sin_prefijo}&cids=1&menu_id=144&active_id={id_sin_prefijo}&model=contrataciones_fuv&view_type=form'
            confirm_url = base_url + formulario_url

            body_html = f"""
                <p>Estimado/a,</p>
                <p>El contrato <strong>{record.name_final}</strong> está próximo a expirar. La fecha de finalización es en {dias} días.</p>
                <p>Le recomendamos que tome las acciones necesarias antes de la fecha de expiración.</p>
                <p>Para revisar los detalles del contrato, por favor haga clic en el siguiente botón:</p>
                <br/>
                <p>
                    <a href="{confirm_url}" style="background-color: #4CAF50; color: white; padding: 8px 16px; text-decoration: none; border-radius: 5px; margin-right: 10px;">Ir al contrato</a>
                </p>
                <br/>
                <p>Atentamente,</p>
                <p>Equipo de Sistema de Gestión Operativa</p>
            """

            for user in coordinadores:
                recipient_email = user.email
                if recipient_email:
                    email_values = {
                        'subject': subject,
                        'body_html': body_html,
                        'email_from': 'go@fundacionunivalle.org',
                        'email_to': recipient_email,
                    }
                    mail = record.env['mail.mail'].create(email_values)
                    mail.send()

        return True

    ultima_actividad_creada = fields.Date(string="Última Actividad Creada", readonly=True)

    def crear_actividad(self):
        # Verificar si ya se ha creado una actividad hoy
        hoy = fields.Date.today()
        if self.ultima_actividad_creada == hoy:
            return  # No crear una actividad si ya se ha creado hoy

        for record in self:
            if not record.id:
                raise ValueError("El registro debe estar guardado antes de crear la actividad.")

            # Verificar que el tipo de actividad esté seleccionado
            if not record.activity_type_id:
                raise ValueError("Por favor, selecciona un tipo de actividad.")

            # Detalles de la actividad
            summary = f"Contrato próximo a vencer"  # Usa el contador en el resumen
            note = (
                "Estimado/a Coordinador/a,\n\n <br/>"
                "<br/>"
                "Asunto: Vencimiento próximo de contrato - Posible necesidad de otrosí\n\n <br/>"
                "<br/>"
                "Por medio de la presente, me permito informarle que el contrato está próximo a vencer en 15 días calendario.\n\n <br/>"
                "<br/>"
                "Considerando la cercanía de la fecha de vencimiento, es importante evaluar si se requiere realizar alguna modificación o adición al contrato mediante un otrosí.\n\n"
                "Le solicito amablemente revisar las condiciones actuales del contrato y determinar si es necesario tomar alguna acción al respecto.\n\n <br/>"
                "<br/>"
                "En caso de que se requiera un otrosí, le agradecería iniciar el proceso correspondiente a la brevedad posible para asegurar su tramitación antes de la fecha de vencimiento.\n\n <br/>"
                "<br/>"
                "Si necesita información adicional o apoyo en este proceso, no dude en contactarme.\n\n <br/>"
                "<br/>"
                "Agradezco su pronta atención a este asunto."
            )
            date_deadline = datetime.now() + timedelta(days=15)

            # Obtener el grupo de usuarios 'Coordinador'
            coordinador_group = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)

            if not coordinador_group or not coordinador_group.users:
                raise ValueError("No hay usuarios en el grupo 'Contratación Coordinador'.")

            # Filtrar solo los usuarios que no son el Administrador
            coordinador_users = coordinador_group.users.filtered(lambda u: u.id != self.env.ref('base.user_admin').id)

            if not coordinador_users:
                raise ValueError(
                    "No hay usuarios disponibles en el grupo 'Contratación Coordinador' que no sean Administradores.")

            # Seleccionar el primer usuario del grupo que no sea el Administrador
            user_id = coordinador_users[0]

            # Crear la actividad
            actividad = self.env['mail.activity'].create({
                'activity_type_id': record.activity_type_id.id,
                'summary': summary,
                'note': note,
                'date_deadline': date_deadline,
                'user_id': user_id.id,
                'res_model_id': self.env['ir.model']._get_id('contrataciones_fuv'),
                'res_id': record.id,
            })

            # Actualizar la fecha de la última actividad creada
            record.ultima_actividad_creada = hoy
            record.activity_id = actividad.id

        return True  # Retornar True después de crear la actividad


    activity_id = fields.Many2one(
        'mail.activity',
        string="Tarea",
        domain="[('res_model', '=', 'contrataciones_fuv'), ('res_id', '=', id)]" # Filtro para mostrar solo actividades relacionadas con este proyecto
    )


    activity_type_id = fields.Many2one('mail.activity.type', string="Tarea", store=True)
    res_model = fields.Char(string='Modelo')

    # Debajo de cuadro información des contrato
    forma_pago = fields.Text('Forma de pago', tracking=True)
    actividades_especificas = fields.Text('Actividades específicas', tracking=True)
    actividades_especificas_natu = fields.Text('Actividades específicas', tracking=True)
    # 4 campos nuevos
    codigo_ciiu = fields.Char('CIIU', tracking=True)
    f_electronica = fields.Boolean('Facturación electronica', tracking=True)
    r_iva = fields.Boolean('Responsable de IVA', tracking=True)
    no_r_iva = fields.Boolean('No responsable de IVA', tracking=True)
    


    @api.depends('f_electronica')
    def compute_facturador_electronico(self):
        if self.f_electronica == True:
            self.f_electronica = 'si'
        else: self.f_electronica = 'no'

    declaracion_renta = fields.Selection([('si', 'Sí'), ('no', 'No')], default='no')
    regimen_simple_tributacion = fields.Selection([('si', 'Sí'), ('no', 'No')], string='Régimen simple de tributación', default='no')
    regimen_especial = fields.Selection([('si', 'Sí'), ('no', 'No')], string='Régimen especial', default='no')
    regimen_ordinario = fields.Selection([('si', 'Sí'), ('no', 'No')], string='Régimen ordinario', default='no')
    
    
    municipios = fields.Selection([
        ('opcion1', 'ABEJORRAL'),

        # Agrega más opciones según sea necesario
    ], string='Municipio', tracking=True)

    # chulear todos

    selecionar_todos = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos')
    def _onchange_selecionar_todos(self):
        if self.selecionar_todos:
            self.documento_identidad_pn = True
            self.rut_pn = True
            self.hoja_vida_pn = True
            self.certificados_estudio = True
            self.certificados_laborales = True
            self.certificado_arl = True
            self.certificacion_salud = True
            self.certificacion_pension = True
            self.certificacion_examen_preocupacional = True
            self.certificacion_contraloria = True
            self.certificacion_procuraduria = True
            self.certificacion_policia_judicial = True
            self.certificacion_medidas_cautelares = True
            self.certificacion_inhabilidades = True
            self.certificacion_libreta_militar = True
            self.resolucion_pensionado = True
            self.certificacion_bancaria = True
            self.anexo_tributario = True
            self.declaracion_juramentada = True
            self.su_cdp = True
            self.contratos = True
        else:
            self.documento_identidad_pn = False
            self.rut_pn = False
            self.hoja_vida_pn = False
            self.certificados_estudio = False
            self.certificados_laborales = False
            self.certificado_arl = False
            self.certificacion_salud = False
            self.certificacion_pension = False
            self.certificacion_examen_preocupacional = False
            self.certificacion_contraloria = False
            self.certificacion_procuraduria = False
            self.certificacion_policia_judicial = False
            self.certificacion_medidas_cautelares = False
            self.certificacion_inhabilidades = False
            self.certificacion_libreta_militar = False
            self.resolucion_pensionado = False
            self.certificacion_bancaria = False
            self.anexo_tributario = False
            self.declaracion_juramentada = False
            self.su_cdp = False
            self.contratos = False

    # chulear todos Financiera

    selecionar_todos_f = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_f')
    def _onchange_selecionar_todos_f(self):
        if self.selecionar_todos_f:
            self.aprueba_f_docc = True
            self.aprueba_f_rutt = True
            self.aprueba_f_hjv = True
            self.aprueba_f_estudio = True
            self.aprueba_f_laborales = True
            self.aprueba_f_arl_ = True
            self.aprueba_f_salud = True
            self.aprueba_f_c_pension = True
            self.aprueba_f_c_preocupacional = True
            self.aprueba_f_c_contraloria = True
            self.aprueba_f_c_procuraduria = True
            self.aprueba_f_c_policia = True
            self.aprueba_f_medidas_cautelares = True
            self.aprueba_f_c_inhabilidades = True
            self.aprueba_f_l_militar = True
            self.aprueba_f_l_pensionado = True
            self.aprueba_f_l_bancaria = True
            self.aprueba_f_a_tributario = True
            self.aprueba_f_l_juramentada = True
            self.aprueba_f_a_su_cdp = True
            self.aprueba_f_l_contratos = True
            self.aprueba_f_resolucion_facturacion_ju = True
            self.aprueba_f_bdd_ju = True
        else:
            self.aprueba_f_docc = False
            self.aprueba_f_rutt = False
            self.aprueba_f_resolucion_facturacion_ju = False
            self.aprueba_f_bdd_ju = False
            self.aprueba_f_hjv = False
            self.aprueba_f_estudio = False
            self.aprueba_f_laborales = False
            self.aprueba_f_arl_ = False
            self.aprueba_f_salud = False
            self.aprueba_f_c_pension = False
            self.aprueba_f_c_preocupacional = False
            self.aprueba_f_c_contraloria = False
            self.aprueba_f_c_procuraduria = False
            self.aprueba_f_c_policia = False
            self.aprueba_f_medidas_cautelares = False
            self.aprueba_f_c_inhabilidades = False
            self.aprueba_f_l_militar = False
            self.aprueba_f_l_pensionado = False
            self.aprueba_f_l_bancaria = False
            self.aprueba_f_a_tributario = False
            self.aprueba_f_l_juramentada = False
            self.aprueba_f_a_su_cdp = False
            self.aprueba_f_l_contratos = False

    # chulear todos Jurídico

    selecionar_todos_j = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_j')
    def _onchange_selecionar_todos_j(self):
        if self.selecionar_todos_j:
            self.aprueba_j_h_doc = True
            self.aprueba_j_h_rut = True
            self.aprueba_j_h_hv = True
            self.aprueba_j_h_est = True
            self.aprueba_j_h_lab = True
            self.aprueba_j_h_arl = True
            self.aprueba_f_salud = True
            self.aprueba_j_h_salud = True
            self.aprueba_j_h_pension = True
            self.aprueba_j_h_contraloria = True
            self.aprueba_j_h_procuraduria = True
            self.aprueba_j_h_policia_judicial = True
            self.aprueba_j_h_medidas_cautelares = True
            self.aprueba_j_h_inhabilidades = True
            self.aprueba_j_h_l_militar = True
            self.aprueba_j_h_pensionado = True
            self.aprueba_j_h_bancaria = True
            self.aprueba_j_h_tributario = True
            self.aprueba_j_h_juramentada = True
            self.aprueba_j_h_su_cdp = True
            self.aprueba_j_h_contratos = True
            self.aprueba_j_h_exm_prec = True
        else:
            self.aprueba_j_h_doc = False
            self.aprueba_j_h_rut = False
            self.aprueba_j_h_hv = False
            self.aprueba_j_h_est = False
            self.aprueba_j_h_lab = False
            self.aprueba_j_h_arl = False
            self.aprueba_f_salud = False
            self.aprueba_j_h_salud = False
            self.aprueba_j_h_pension = False
            self.aprueba_j_h_contraloria = False
            self.aprueba_j_h_procuraduria = False
            self.aprueba_j_h_policia_judicial = False
            self.aprueba_j_h_medidas_cautelares = False
            self.aprueba_j_h_inhabilidades = False
            self.aprueba_j_h_l_militar = False
            self.aprueba_j_h_pensionado = False
            self.aprueba_j_h_bancaria = False
            self.aprueba_j_h_tributario = False
            self.aprueba_j_h_juramentada = False
            self.aprueba_j_h_su_cdp = False
            self.aprueba_j_h_contratos = False
            self.aprueba_j_h_exm_prec = False

    # Información del Contrato - Documentación Anexa
    persona_natural = fields.Boolean('Persona natural', tracking=True)
    persona_juridica = fields.Boolean('Persona jurídica', tracking=True)


    @api.constrains('documento_identidad_pn_doc')
    def _check_file_extension1(self):
        for record in self:
            if record.documento_identidad_pn_doc:
                filename = record.documento_identidad_pn_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_identidad = {}


    documento_identidad_actualizar_visible = fields.Boolean(default=False, related='documento_identidad_id.actualizar_visible', store=True, readonly=False)

    file_data_visible = fields.Boolean(default=False)




    # @api.onchange('documento_identidad_pn_doc')
    # def _onchange_documento_identidad_pn_doc(self):
    #     if self.documento_identidad_pn_doc:
    #         self.documento_identidad_pn = True

    @api.onchange('documento_identidad_pn_doc')
    def _onchange_delete_documento_identidad_pn_doc(self):
        if not self.documento_identidad_pn_doc:
            self.documento_identidad_pn = False

    @api.model
    def cargar_documento(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'documento_identidad_pn_doc': file_content,
            'documento_identidad_pn_doc_fname': file_name,
        })

    def _compute_true_natural(self):
        self.aprueba_f_docc = True
        self.aprueba_f_estudio = True
        self.aprueba_f_laborales = True
        self.aprueba_f_arl_ = True
        self.aprueba_f_otra_arl_ = True
        self.aprueba_f_salud = True
        self.aprueba_f_c_pension = True
        self.aprueba_f_c_preocupacional = True
        self.aprueba_f_c_contraloria = True
        self.aprueba_f_c_procuraduria = True
        self.aprueba_f_c_policia = True
        self.aprueba_f_medidas_cautelares = True
        self.aprueba_f_c_inhabilidades = True
        self.aprueba_f_l_militar = True
        self.aprueba_f_l_pensionado = True
        self.aprueba_f_a_tributario = True
        self.aprueba_f_a_su_cdp = True
        self.aprueba_f_l_contratos = True
        self.aprueba_j_h_contratos = True
        self.aprueba_j_h_tributario = True


    @api.constrains('rut_pn_doc')
    def _check_file_extension2(self):
        for record in self:
            if record.rut_pn_doc:
                filename = record.rut_pn_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_rut = {}



    rut_actualizar_visible = fields.Boolean(default=False, related='rut_id.actualizar_visible', store=True, readonly=False)
    


    @api.onchange('rut_pn_doc')
    def _onchange_rut_pn_doc(self):
        if self.rut_pn_doc:
            self.rut_pn = True

    @api.onchange('rut_pn_doc')
    def _onchange_delete_rut_pn_doc(self):
        if not self.rut_pn_doc:
            self.rut_pn = False

    @api.model
    def cargar_documento_2(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'rut_pn_doc': file_content,
            'rut_pn_doc_fname': file_name,
        })



    @api.constrains('hoja_vida_pn_doc')
    def _check_file_extension3(self):
        for record in self:
            if record.hoja_vida_pn_doc:
                filename = record.hoja_vida_pn_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_hoja_vida = {}


    hoja_vida_actualizar_visible = fields.Boolean(default=False, related='hoja_vida_id.actualizar_visible', store=True, readonly=False)



    @api.onchange('hoja_vida_pn_doc')
    def _onchange_hoja_vida_pn_doc(self):
        if self.hoja_vida_pn_doc:
            self.hoja_vida_pn = True

    @api.onchange('hoja_vida_pn_doc')
    def _onchange_delete_hoja_vida_pn_doc(self):
        if not self.hoja_vida_pn_doc:
            self.hoja_vida_pn = False

    @api.model
    def cargar_documento_3(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'hoja_vida_pn_doc': file_content,
            'hoja_vida_pn_doc_fname': file_name,
        })

    aprueba_j_h_hv = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_hjv = fields.Boolean('Aprueba-F', tracking=True)



    @api.constrains('certificados_estudio_doc')
    def _check_file_extension4(self):
        for record in self:
            if record.certificados_estudio_doc:
                filename = record.certificados_estudio_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificados_estudio = {}


    certificados_estudio_actualizar_visible = fields.Boolean(default=False, related='certificados_estudio_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('certificados_estudio_doc')
    def _onchange_certificados_estudio_doc(self):
        if self.certificados_estudio_doc:
            self.certificados_estudio = True

    @api.onchange('certificados_estudio_doc')
    def _onchange_delete_certificados_estudio_doc(self):
        if not self.certificados_estudio_doc:
            self.certificados_estudio = False

    @api.model
    def cargar_documento_4(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificados_estudio_doc': file_content,
            'certificados_estudio_doc_fname': file_name,
        })

    aprueba_j_h_est = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_estudio = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')



    @api.constrains('certificados_laborales_doc')
    def _check_file_extension5(self):
        for record in self:
            if record.certificados_laborales_doc:
                filename = record.certificados_laborales_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificados_laborales = {}



    certificados_laborales_actualizar_visible = fields.Boolean(default=False, related='certificados_laborales_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('certificados_laborales_doc')
    def _onchange_certificados_laborales_doc(self):
        if self.certificados_laborales_doc:
            self.certificados_laborales = True

    @api.onchange('certificados_laborales_doc')
    def _onchange_delete_certificados_laborales_doc(self):
        if not self.certificados_laborales_doc:
            self.certificados_laborales = False

    @api.model
    def cargar_documento_5(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificados_estudio_doc': file_content,
            'certificados_estudio_doc_fname': file_name,
        })

    aprueba_j_h_lab = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_laborales = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')

    certificado_arl = fields.Boolean('Certificado de afiliación a ARL (no mayor a 30 días) o formato ARL FUV',
                                     tracking=True)

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificado_arl = {}


    certificado_arl_actualizar_visible = fields.Boolean(default=False, related='certificado_arl_id.actualizar_visible', readonly=False, store=True)



    @api.constrains('certificado_arl_doc')
    def _check_file_extension6(self):
        for record in self:
            if record.certificado_arl_doc:
                filename = record.certificado_arl_doc_fname

    @api.onchange('certificado_arl_doc')
    def _onchange_certificado_arl_doc(self):
        if self.certificado_arl_doc:
            self.certificado_arl = True

    @api.onchange('certificado_arl_doc')
    def _onchange_delete_certificado_arl_doc(self):
        if not self.certificado_arl_doc:
            self.certificado_arl = False

    @api.model
    def cargar_documento_6(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificado_arl_doc': file_content,
            'certificado_arl_doc_fname': file_name,
        })

    aprueba_j_h_arl = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_arl_ = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')
    aprueba_r_arl_ = fields.Boolean('Aprueba-F', tracking=True)



    @api.constrains('certificacion_salud_doc')
    def _check_file_extension7(self):
        for record in self:
            if record.certificacion_salud_doc:
                filename = record.certificacion_salud_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_salud = {}


    certificacion_salud_actualizar_visible = fields.Boolean(default=False, related='certificacion_salud_id.actualizar_visible', readonly=False, store=True)




    @api.onchange('certificacion_salud_doc')
    def _onchange_certificacion_salud_doc(self):
        if self.certificacion_salud_doc:
            self.certificacion_salud = True

    @api.onchange('certificacion_salud_doc')
    def _onchange_delete_certificacion_salud_doc(self):
        if not self.certificacion_salud_doc:
            self.certificacion_salud = False

    @api.model
    def cargar_documento_7(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_salud_doc': file_content,
            'certificacion_salud_doc_fname': file_name,
        })

    aprueba_j_h_salud = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_salud = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')
    aprueba_r_salud_ = fields.Boolean('Aprueba-F', tracking=True)



    @api.constrains('certificacion_pension_doc')
    def _check_file_extension8(self):
        for record in self:
            if record.certificacion_pension_doc:
                filename = record.certificacion_pension_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_pension = {}


    certificacion_pension_actualizar_visible = fields.Boolean(default=False, related='certificacion_pension_id.actualizar_visible', readonly=False, store=True)




    @api.onchange('certificacion_pension_doc')
    def _onchange_certificacion_pension_doc(self):
        if self.certificacion_pension_doc:
            self.certificacion_pension = True

    @api.onchange('certificacion_pension_doc')
    def _onchange_delete_certificacion_pension_doc(self):
        if not self.certificacion_pension_doc:
            self.certificacion_pension = False

    @api.model
    def cargar_documento_8(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_pension_doc': file_content,
            'certificacion_pension_doc_fname': file_name,
        })

    aprueba_j_h_pension = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_c_pension = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')
    aprueba_r_pension_ = fields.Boolean('Aprueba-F', tracking=True)



    @api.constrains('certificacion_examen_preocupacional_doc')
    def _check_file_extension9(self):
        for record in self:
            if record.certificacion_examen_preocupacional_doc:
                filename = record.certificacion_examen_preocupacional_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_examen_preocupacional = {}


    certificacion_examen_preocupacional_actualizar_visible = fields.Boolean(default=False, related='certificacion_examen_preocupacional_id.actualizar_visible', readonly=False, store=True)


    @api.onchange('certificacion_examen_preocupacional_doc')
    def _onchange_certificacion_examen_preocupacional_doc(self):
        if self.certificacion_examen_preocupacional_doc:
            self.certificacion_examen_preocupacional = True

    @api.onchange('certificacion_examen_preocupacional_doc')
    def _onchange_delete_certificacion_examen_preocupacional_doc(self):
        if not self.certificacion_examen_preocupacional_doc:
            self.certificacion_examen_preocupacional = False

    @api.model
    def cargar_documento_8(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_examen_preocupacional_doc': file_content,
            'certificacion_examen_preocupacional_doc_fname': file_name,
        })



    @api.constrains('certificacion_contraloria_doc')
    def _check_file_extension10(self):
        for record in self:
            if record.certificacion_contraloria_doc:
                filename = record.certificacion_contraloria_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_contraloria = {}


    certificacion_contraloria_actualizar_visible = fields.Boolean(default=False, related='certificacion_contraloria_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('certificacion_contraloria_doc')
    def _onchange_certificacion_contraloria_doc(self):
        if self.certificacion_contraloria_doc:
            self.certificacion_contraloria = True

    @api.onchange('certificacion_contraloria_doc')
    def _onchange_delete_certificacion_contraloria_doc(self):
        if not self.certificacion_contraloria_doc:
            self.certificacion_contraloria = False

    @api.model
    def cargar_documento_9(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_contraloria_doc': file_content,
            'certificacion_contraloria_doc_fname': file_name,
        })

    aprueba_j_h_contraloria = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_c_contraloria = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')



    @api.constrains('certificacion_procuraduria_doc')
    def _check_file_extension11(self):
        for record in self:
            if record.certificacion_procuraduria_doc:
                filename = record.certificacion_procuraduria_doc_fname

    _cache_documentos_certificacion_procuraduria = {}


    certificacion_procuraduria_actualizar_visible = fields.Boolean(default=False, related='certificacion_procuraduria_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('certificacion_procuraduria_doc')
    def _onchange_certificacion_procuraduria_doc(self):
        if self.certificacion_procuraduria_doc:
            self.certificacion_procuraduria = True

    @api.onchange('certificacion_procuraduria_doc')
    def _onchange_delete_certificacion_procuraduria_doc(self):
        if not self.certificacion_procuraduria_doc:
            self.certificacion_procuraduria = False

    @api.model
    def cargar_documento_10(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_procuraduria_doc': file_content,
            'certificacion_procuraduria_doc_fname': file_name,
        })

    aprueba_j_h_procuraduria = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_c_procuraduria = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')

    certificacion_policia_judicial = fields.Boolean('Certificado de policía judicial (no mayor a 30 días)',
                                                    tracking=True)


    @api.constrains('certificacion_policia_judicial_doc')
    def _check_file_extension12(self):
        for record in self:
            if record.certificacion_policia_judicial_doc:
                filename = record.certificacion_policia_judicial_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_policia_judicial = {}



    certificacion_policia_judicial_actualizar_visible = fields.Boolean(default=False, related='certificacion_policia_judicial_id.actualizar_visible', readonly=False, store=True)




    @api.onchange('certificacion_policia_judicial_doc')
    def _onchange_certificacion_policia_judicial_doc(self):
        if self.certificacion_policia_judicial_doc:
            self.certificacion_policia_judicial = True

    @api.onchange('certificacion_policia_judicial_doc')
    def _onchange_delete_certificacion_policia_judicial_doc(self):
        if not self.certificacion_policia_judicial_doc:
            self.certificacion_policia_judicial = False

    @api.model
    def cargar_documento_11(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_policia_judicial_doc': file_content,
            'certificacion_policia_judicial_doc_fname': file_name,
        })

    aprueba_j_h_policia_judicial = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_c_policia = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')

    certificacion_medidas_cautelares = fields.Boolean('Certificado medidas cautelares (no mayor a 30 días)',
                                                      tracking=True)


    @api.constrains('certificacion_medidas_cautelares_doc')
    def _check_file_extension13(self):
        for record in self:
            if record.certificacion_medidas_cautelares_doc:
                filename = record.certificacion_medidas_cautelares_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_medidas_cautelares = {}


    certificacion_medidas_cautelares_actualizar_visible = fields.Boolean(default=False, related='certificacion_medidas_cautelares_id.actualizar_visible', readonly=False, store=True)


    @api.onchange('certificacion_medidas_cautelares_doc')
    def _onchange_certificacion_medidas_cautelares_doc(self):
        if self.certificacion_medidas_cautelares_doc:
            self.certificacion_medidas_cautelares = True

    @api.onchange('certificacion_medidas_cautelares_doc')
    def _onchange_delete_certificacion_medidas_cautelares_doc(self):
        if not self.certificacion_medidas_cautelares_doc:
            self.certificacion_medidas_cautelares = False

    @api.model
    def cargar_documento_12(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_medidas_cautelares_doc': file_content,
            'certificacion_medidas_cautelares_doc_fname': file_name,
        })

    aprueba_j_h_medidas_cautelares = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_medidas_cautelares = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')



    @api.constrains('certificacion_inhabilidades_doc')
    def _check_file_extension14(self):
        for record in self:
            if record.certificacion_inhabilidades_doc:
                filename = record.certificacion_inhabilidades_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_inhabilidades = {}


    certificacion_inhabilidades_actualizar_visible = fields.Boolean(default=False, related='certificacion_inhabilidades_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('certificacion_inhabilidades_doc')
    def _onchange_certificacion_inhabilidades_doc(self):
        if self.certificacion_inhabilidades_doc:
            self.certificacion_inhabilidades = True

    @api.onchange('certificacion_inhabilidades_doc')
    def _onchange_delete_certificacion_inhabilidades_doc(self):
        if not self.certificacion_inhabilidades_doc:
            self.certificacion_inhabilidades = False

    @api.model
    def cargar_documento_13(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_inhabilidades_doc': file_content,
            'certificacion_inhabilidades_doc_fname': file_name,
        })

    aprueba_j_h_inhabilidades = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_c_inhabilidades = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')



    @api.constrains('certificacion_libreta_militar_doc')
    def _check_file_extension15(self):
        for record in self:
            if record.certificacion_libreta_militar_doc:
                filename = record.certificacion_libreta_militar_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_libreta_militar = {}


    certificacion_libreta_militar_actualizar_visible = fields.Boolean(default=False, related='certificacion_libreta_militar_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('certificacion_libreta_militar_doc')
    def _onchange_certificacion_libreta_militar_doc(self):
        if self.certificacion_libreta_militar_doc:
            self.certificacion_libreta_militar = True

    @api.onchange('certificacion_libreta_militar_doc')
    def _onchange_delete_certificacion_libreta_militar_doc(self):
        if not self.certificacion_libreta_militar_doc:
            self.certificacion_libreta_militar = False

    @api.model
    def cargar_documento_14(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_libreta_militar_doc': file_content,
            'certificacion_libreta_militar_doc_fname': file_name,
        })

    aprueba_j_h_l_militar = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_militar = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')



    @api.constrains('resolucion_pensionado_doc')
    def _check_file_extension16(self):
        for record in self:
            if record.resolucion_pensionado_doc:
                filename = record.resolucion_pensionado_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_resolucion_pensionado = {}


    resolucion_pensionado_actualizar_visible = fields.Boolean(default=False, related='resolucion_pensionado_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('resolucion_pensionado_doc')
    def _onchange_resolucion_pensionado_doc(self):
        if self.resolucion_pensionado_doc:
            self.resolucion_pensionado = True

    @api.onchange('resolucion_pensionado_doc')
    def _onchange_delete_resolucion_pensionado_doc(self):
        if not self.resolucion_pensionado_doc:
            self.resolucion_pensionado = False

    @api.model
    def cargar_documento_15(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'resolucion_pensionado_doc': file_content,
            'resolucion_pensionado_doc_fname': file_name,
        })

    aprueba_j_h_pensionado = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_pensionado = fields.Boolean('Aprueba-F', compute='_compute_true_natural')



    @api.constrains('certificacion_bancaria_doc')
    def _check_file_extension17(self):
        for record in self:
            if record.certificacion_bancaria_doc:
                filename = record.certificacion_bancaria_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_certificacion_bancaria = {}


    certificacion_bancaria_actualizar_visible = fields.Boolean(default=False, related='certificacion_bancaria_id.actualizar_visible', readonly=False, store=True)


    @api.onchange('certificacion_bancaria_doc')
    def _onchange_certificacion_bancaria_doc(self):
        if self.certificacion_bancaria_doc:
            self.certificacion_bancaria = True

    @api.onchange('certificacion_bancaria_doc')
    def _onchange_delete_certificacion_bancaria_doc(self):
        if not self.certificacion_bancaria_doc:
            self.certificacion_bancaria = False

    @api.model
    def cargar_documento_16(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificacion_bancaria_doc': file_content,
            'certificacion_bancaria_doc_fname': file_name,
        })

    aprueba_j_h_bancaria = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_bancaria = fields.Boolean('Aprueba-F', tracking=True)

    anexo_tributario = fields.Boolean('Anexo tributario', tracking=True)
    anexo_tributario_doc = fields.Binary(tracking=True)

    @api.constrains('anexo_tributario_doc')
    def _check_file_extension18(self):
        for record in self:
            if record.anexo_tributario_doc:
                filename = record.anexo_tributario_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_anexo_tributario = {}




    anexo_tributario_actualizar_visible = fields.Boolean(default=False, related='anexo_tributario_id.actualizar_visible', readonly=False, store=True)


    anexo_tributario_doc_fname = fields.Char(tracking=True)
    anexo_tributario_doc_visible = fields.Boolean(default=True)
    anexo_tributario_id = fields.Many2one('drive', string='Anexo Tributario', readonly=True, tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_anexo_tributario(self):
        self.ensure_one()
        contexto_at_posterior = {}
        if self.usa_flujo_contratacion_nuevo:
            self._validar_gestion_at_posterior_presupuesto()
            contexto_at_posterior = {
                'gestion_at_posterior_presupuesto': True,
            }
            _logger.info(
                '[AT-PRESUPUESTO-FLUJO-NUEVO] Apertura de carga del AT PN. '
                'fc_id=%s estado=%s usuario_id=%s usuario=%s',
                self.id, self.estado_contratacion,
                self.env.user.id, self.env.user.name,
            )
            self.with_context(**contexto_at_posterior).write({
                'anexo_tributario': True,
            })
        else:
            self.anexo_tributario = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir anexo tributario',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'anexo_tributario',
                **contexto_at_posterior,
            },
        }


    def subir_documento_anexo_tributario_a_drive(self):
        """Sube el documento 'Anexo Tributario' a Google Drive y lo registra en el modelo drive."""
        if self.anexo_tributario_doc and self.anexo_tributario_doc_fname:
            _logger.info(f"Subiendo documento '{self.anexo_tributario_doc_fname}' a Google Drive...")

            result = self._upload_document_to_drive_anexo_tributario_pn()

            if not result:
                raise UserError("❌ Error al subir el Anexo tributario a Google Drive. Intente nuevamente más tarde.")

            self.message_post(body="✅ Anexo tributario subido correctamente a Google Drive.")

        else:
            _logger.info("No hay archivo de 'Anexo tributario' para subir. No se realiza ninguna acción.")
            # No se lanza error aquí, simplemente se ignora

    def _upload_document_to_drive_anexo_tributario_pn(self):
        """Sube el archivo binario 'anexo_tributario_doc' a Google Drive y lo registra en el modelo 'drive'."""

        self.ensure_one()
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Validar que drive_file_id es Many2one y no string
        if isinstance(self.drive_file_id, str):
            _logger.error(f"El campo drive_file_id tiene un valor inválido (string): {self.drive_file_id}")
            return False

        # Obtener el registro relacionado en drive.file
        drive_file_record = self.env['drive.file'].browse(self.drive_file_id.id)
        if not drive_file_record:
            _logger.error(f"No se encontró un registro en drive.file con el ID {self.drive_file_id.id}.")
            return False

        carpeta_drive = drive_file_record.folder_id2
        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta asociada en el campo folder_id2 del registro drive.file con ID {self.drive_file_id.id}.")
            return False

        try:
            file_content = base64.b64decode(self.anexo_tributario_doc)
            original_extension = os.path.splitext(self.anexo_tributario_doc_fname)[1] or '.bin'
            fixed_file_name = f"21_Anexo Tributario_{uuid.uuid4()}{original_extension}"

            # Verificar si ya existe un archivo con ese nombre en la carpeta de Drive
            query = f"'{carpeta_drive}' in parents and trashed=false and title='{fixed_file_name}'"
            file_list = drive.ListFile({'q': query}).GetList()
            if file_list:
                _logger.warning(
                    f"Ya existe un archivo con el nombre '{fixed_file_name}' en la carpeta {carpeta_drive}. No se subirá.")
                # raise UserError(f"Ya existe un archivo con el nombre '{fixed_file_name}' en Google Drive.")
                return False

            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo '{fixed_file_name}' a la carpeta ID {carpeta_drive} en Google Drive...")

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
                _logger.error("Falló la subida del archivo. No se recibió un ID válido desde Google Drive.")
                return False

            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo subido correctamente a Google Drive con ID: {file_id} y URL: {file_link}")

            # Crear el registro en Odoo
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.anexo_tributario_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(f"Registro creado en el modelo 'drive' con ID {drive_record.id}")

            # Actualizar el campo anexo_tributario_id si está vacío
            if not self.anexo_tributario_id:
                self.anexo_tributario_id = drive_record.id
                _logger.info(
                    f"Campo anexo_tributario_id actualizado con el ID del nuevo registro en drive: {drive_record.id}")

            # Ocultar el campo binario
            self.anexo_tributario_doc_visible = False

            _logger.info("Campo anexo_tributario_doc ocultado y limpiado tras la subida.")

            return True

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        finally:
            # Eliminar archivo temporal
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                _logger.info("Archivo temporal eliminado correctamente.")

    @api.onchange('anexo_tributario_doc')
    def _onchange_anexo_tributario_doc(self):
        if self.anexo_tributario_doc:
            self.anexo_tributario = True

    @api.onchange('anexo_tributario_doc')
    def _onchange_delete_anexo_tributario_doc(self):
        if not self.anexo_tributario_doc:
            self.anexo_tributario = False

    @api.model
    def cargar_documento_18(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'anexo_tributario_doc': file_content,
            'anexo_tributario_doc_fname': file_name,
        })

    ####################################################################################################################
    contratos = fields.Boolean('Contrato', tracking=True)
    contratos_doc = fields.Binary(tracking=True)

    @api.constrains('contratos_doc')
    def _check_file_extension19(self):
        for record in self:
            if record.contratos_doc:
                filename = record.contratos_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_contratos = {}



    contratos_actualizar_visible = fields.Boolean(default=False, related='contratos_id.actualizar_visible', readonly=False, store=True)


    contratos_doc_fname = fields.Char(tracking=True)
    contratos_doc_visible = fields.Boolean(default=True)
    contratos_id = fields.Many2one('drive', string='Contrato', readonly=True, tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_contratos(self):
        self.contratos = True  # Asegurarse de que el campo booleano esté marcado como True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contrato',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'contratos' 
            },
        }


    def notificacion_contratos_doc(self):
        if self.usa_flujo_contratacion_nuevo:
            self._marcar_contrato_listo_para_firmas('')
            return

        _logger.info(">>> DISPARANDO NOTIFICACIÓN: Se subió un contrato (ID: %s)", self.id)
        
        # 1. Verificar que el proyecto relacionado exista
        if not self.proyecto_relacionado:
            _logger.warning(">>> No se envió correo: Falta proyecto relacionado.")
            raise UserError("No se ha definido un proyecto relacionado.")

        # 2. Configurar URLs
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        record_id = self._origin.id if self._origin else self.id
        record_url = f"{base_url}/web#id={record_id}&view_type=form&model={self._name}"

        # 3. Validar destinatarios
        emails = []
        
        # Correo del creador, si existe.
        if self.proyecto_relacionado.create_uid.email:
            emails.append(self.proyecto_relacionado.create_uid.email)

        # Respaldo: coordinador responsable del proyecto.
        responsable = self.proyecto_relacionado.asignar_coordinador
        if responsable and responsable.email and responsable.email not in emails:
            emails.append(responsable.email)

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
        subject = f"El contrato ha sido subido en {self.new_name or self.name}"
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📋 Contrato subido al sistema</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Estimados,</p>

                    <p>Se ha subido el contrato al sistema, por favor, hacer firmar:</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <p style="margin: 4px 0;"><strong>Nombre del contrato:</strong> {self.new_name or self.name}</p>
                        <p style="margin: 4px 0;"><strong>Proyecto:</strong> {self.proyecto_relacionado.new_name}</p>
                    </div>

                    <p style="text-align: center; margin-top: 30px;">
                        <a href="{record_url}"
                           style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                            ➡ Ver contrato
                        </a>
                    </p>

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático del Repositorio, por favor no responder.
                    </p>
                </div>
            </div>
        """

        mail_values = {
            'subject': subject,
            'body_html': body,
            'email_to': email_to,
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
        }

        # 5. Envío
        _logger.info(">>> Creando correo para: %s", email_to)
        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()
        _logger.info(">>> ¡CORREO ENVIADO EXITOSAMENTE!")
        self._marcar_contrato_listo_para_firmas(email_to)

    def _marcar_contrato_listo_para_firmas(self, email_to):
        if self.usa_flujo_contratacion_nuevo:
            self.message_post(
                body=(
                    "<p><b>Contrato cargado por Jurídico.</b></p>"
                    "<p>El estado permanece en <b>Jurídico 2 - Contrato</b> para que "
                    "el jurídico pueda revisar el archivo cargado y, cuando esté correcto, "
                    "use el botón de aprobación para enviarlo a firma.</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )
            return

        estado_destino = 'firma' if self.usa_flujo_contratacion_nuevo else 'coordinador'
        estado_visible = 'Firma' if estado_destino == 'firma' else 'Coordinador'
        if self.estado_contratacion != estado_destino:
            self.write({'estado_contratacion': estado_destino})
        self.message_post(
            body=(
                "<p><b>Contrato cargado para firma.</b></p>"
                "<p>Se notificó exitosamente al coordinador/asistente para que proceda "
                "con las firmas correspondientes y el cargue del contrato firmado.</p>"
                f"<p><b>Estado actual:</b> {estado_visible}</p>"
                f"<p><b>Destinatarios:</b> {email_to}</p>"
            ),
            subtype_xmlid='mail.mt_note',
        )

    @api.onchange('contratos_doc')
    def _onchange_contratos_doc(self):
        if self.contratos_doc:
            self.contratos = True

    @api.onchange('contratos_doc')
    def _onchange_delete_contratos_doc(self):
        if not self.contratos_doc:
            self.contratos = False


######################################################################################################################

    contratos_firmado = fields.Boolean('Contrato', tracking=True)
    contratos_firmado_doc = fields.Binary(tracking=True)

    @api.constrains('contratos_firmado_doc')
    def _check_file_extension19(self):
        for record in self:
            if record.contratos_firmado_doc:
                filename = record.contratos_firmado_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_contratos_firmado = {}


    contratos_firmado_actualizar_visible = fields.Boolean(default=False, related='contratos_firmado_id.actualizar_visible', readonly=False, store=True)


    contratos_firmado_doc_fname = fields.Char(tracking=True)
    contratos_firmado_doc_visible = fields.Boolean(default=True)

    contratos_firmado_id = fields.Many2one('drive', string='Contrato firmado', readonly=True, tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_contratos_firmado(self):
        self.contratos_firmado = True  # Asegurarse de que el campo booleano esté marcado como True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Contrato firmado',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'contratos_firmado' 
            },
        }



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
                        nombre_default="Contrato firmado.pdf",
                    )
                    if attachment_id:
                        attachment_ids.append(attachment_id)
                    _logger.info(">>> Enlace de Drive preparado para correo: %s", drive_link)
                else:
                    _logger.warning(">>> No se encontró file_id en el registro de Drive (ID: %s)", drive_record.id)
            except Exception as e:
                _logger.warning(">>> No se pudo preparar el enlace del documento en Drive: %s", str(e))

        # Lista de destinatarios
        destinatarios = []

        # 1. Correo del contratista
        if self.contratista_proveedor_relacionado and self.contratista_proveedor_relacionado.correo_facturacion:
            destinatarios.append(self.contratista_proveedor_relacionado.correo_facturacion)
            _logger.info(">>> Destinatario (Contratista): %s", self.contratista_proveedor_relacionado.correo_facturacion)


        # Construir Correo
        COLOR_PRINCIPAL = "#043484"
        subject = f"Copia del contrato firmado: {self.new_name or self.name}"
        proyecto_nombre = self.proyecto_relacionado.new_name if self.proyecto_relacionado else 'N/A'
        adjunto_info = (
            "<p>Encontrarán la copia del contrato firmado adjunta a este correo.</p>"
            if attachment_ids
            else
            f'<p>El contrato firmado está disponible en Google Drive: '
            f'<a href="{drive_link}" style="color:{COLOR_PRINCIPAL}; font-weight:bold;">Ver documento</a></p>'
            if drive_link
            else "<p>El contrato firmado para este proyecto ya está disponible en el sistema.</p>"
        )
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📄 Copia del contrato firmado disponible</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Cordial saludo,</p>

                    <p>El contrato firmado para el siguiente proyecto ya está disponible:</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <strong>Proyecto:</strong> {proyecto_nombre}
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
            _logger.info(">>> Enviando notificación de contrato firmado a %s destinatarios...", len(destinatarios))
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.sudo().send()
            if attachment_ids:
                self.env["ir.attachment"].sudo().browse(attachment_ids).unlink()
            _logger.info(">>> ✅ NOTIFICACIÓN ENVIADA CON ÉXITO")
        except Exception as e:
            _logger.error(">>> ❌ ERROR enviando notificación firmada: %s", str(e))

    @api.onchange('contratos_firmado_doc')
    def _onchange_contratos_doc(self):
        if self.contratos_firmado_doc:
            self.contratos = True

    @api.onchange('contratos_firmado_doc')
    def _onchange_delete_contratos_doc(self):
        if not self.contratos_firmado_doc:
            self.contratos_firmado = False
########################################################################################################################

    @api.model
    def cargar_documento_50(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'contratos_doc': file_content,
            'contratos_doc_fname': file_name,
        })


    @api.model
    def cargar_documento_100(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'contratosfirmado_doc': file_content,
            'contratosfirmado_doc_fname': file_name,
        })

    aprueba_j_h_contratos = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True, compute='_compute_true_natural')
    aprueba_f_l_contratos = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')

    ###################################################################################################

    su_cdp = fields.Boolean('RPC', tracking=True)
    su_cdp_doc = fields.Binary(tracking=True)

    @api.constrains('su_cdp_doc')
    def _check_file_extension20(self):
        for record in self:
            if record.su_cdp_doc:
                filename = record.su_cdp_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_su_cdp = {}


    su_cdp_actualizar_visible = fields.Boolean(default=False, related='su_cdp_id.actualizar_visible', readonly=False, store=True)


    su_cdp_doc_fname = fields.Char(tracking=True)
    su_cdp_id = fields.Many2one('drive', string='RPC', readonly=True, tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    su_cdp_doc_visible = fields.Boolean(default=True)

    
    def action_upload_su_cdp(self):
        self.su_cdp = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'su_cdp' 
            },
        }

    @api.onchange('su_cdp_doc')
    def _onchange_su_cdp_doc(self):
        if self.su_cdp_doc:
            self.su_cdp = True

    @api.onchange('su_cdp_doc')
    def _onchange_delete_su_cdp_doc(self):
        if not self.su_cdp_doc:
            self.su_cdp = False

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
    aprueba_f_a_su_cdp = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')

    ###################################################################################################

    aprueba_j_h_tributario = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True, compute='_compute_true_natural')
    aprueba_f_a_tributario = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true_natural')



    @api.constrains('declaracion_juramentada_doc')
    def _check_file_extension21(self):
        for record in self:
            if record.declaracion_juramentada_doc:
                filename = record.declaracion_juramentada_doc_fname

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_declaracion_juramentada = {}


    declaracion_juramentada_actualizar_visible = fields.Boolean(default=False, related='declaracion_juramentada_id.actualizar_visible', readonly=False, store=True)



    @api.onchange('declaracion_juramentada_doc')
    def _onchange_declaracion_juramentada_doc(self):
        if self.declaracion_juramentada_doc:
            self.declaracion_juramentada = True

    @api.onchange('declaracion_juramentada_doc')
    def _onchange_delete_declaracion_juramentada_doc(self):
        if not self.declaracion_juramentada_doc:
            self.declaracion_juramentada = False

    @api.model
    def cargar_documento_17(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'declaracion_juramentada_doc': file_content,
            'declaracion_juramentada_doc_fname': file_name,
        })

    aprueba_j_h_juramentada = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)
    aprueba_f_l_juramentada = fields.Boolean('Aprueba-F', tracking=True)

    # Campos de chulear - Tabla derecha


    ##################################################################################################################
    def crear_consecutivos(self):
        """
        Método para llamar a create_from_contrataciones en el modelo consecutivo.
        """
        try:
            # Llama al método create_from_contrataciones del modelo consecutivo
            self.env['consecutivo'].crear_consecutivos_para_contrataciones()
        except Exception as e:
            # Maneja cualquier error que ocurra al ejecutar el método
            _logger.error('Error al crear consecutivos desde contrataciones_fuv: %s', e)

    def crear_reportes(self, record):
        # Intenta crear reportes a partir de los contratistas
        try:
            # Llama a la función create_from_contratistas en el modelo reportes
            self.env['reportes'].create_from_contratistas()
        except Exception as e:
            # Maneja cualquier error que ocurra durante la creación
            _logger.error('Error al crear reportes desde contratistas: %s', e)
            return

        # Busca los registros de reportes relacionados con el contratista actual
        reportes_records = self.env['reportes'].search([('contratista_reportes', '=', record.id)])

        # Itera sobre los reportes encontrados y obtiene los datos de pago
        for reporte in reportes_records:
            try:
                reporte.obtener_datos_pago()
            except Exception as e:
                # Maneja cualquier error que ocurra al obtener datos de pago
                _logger.error('Error al obtener datos de pago para el reporte %s: %s', reporte.id, e)

    def _marcar_proyecto_en_progreso_desde_fc(self):
        for record in self:
            proyecto = record.proyecto_relacionado
            if proyecto and proyecto.coordinador_g == 'red':
                proyecto.write({'coordinador_g': 'yellow'})

    @api.model
    def create(self, vals):
        self._validar_contratista_requerido_para_no_soporte(vals)
        current_date = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = super(Contrataciones_Fuv, self).create(vals)

        if not record.new_name:
            self.new_name = f"Folder_{current_date}"
            self.save()

        self.crear_reportes(record)
        self.crear_consecutivos()
        record.buscar_carpeta()

        # 🔽 Aquí llamas la función del otro modelo
        self.env['contrataciones_fuv_relacion'].action_cargar_individual(record.id)
        record._marcar_proyecto_en_progreso_desde_fc()

        return record
        
    proceso_en_ejecucion = fields.Boolean(default=False)

    estado_sincronizacion_drive = fields.Selection([
        ('borrador', 'No sincronizado'),
        ('pendiente', 'En proceso'),
        ('completado', 'Completado')
    ], string="Estado Drive", default='borrador', tracking=True)

    def buscar_carpeta(self):
        """Busca o crea la carpeta relacionada en Odoo y en Google Drive."""
        # --- [CORRECCIÓN 1] EVITAR DUPLICADOS DE CARPETAS ---
        # Si este contrato ya tiene una carpeta asignada, NO HACEMOS NADA.
        if self.drive_file_id:
            return 
        # ----------------------------------------------------

        if self.proyecto_relacionado:
            if self.proceso_en_ejecucion:
                return

            self.proceso_en_ejecucion = True

            try:
                _logger.info(f"Iniciando creación de carpeta para: {self.new_name}")

                # 1. Buscar carpeta PADRE (CONTRATOS PS)
                drive_stage = self.env['drive_stage'].search([
                    ('name', '=', '3. CONTRATOS PS'),
                    ('proyecto_relacionado', '=', self.proyecto_relacionado.id),
                ], limit=1)

                if not drive_stage:
                    drive_stage = self.env['drive_stage'].create({
                        'name': '3. CONTRATOS PS',
                        'proyecto_relacionado': self.proyecto_relacionado.id,
                        'folder_id': 'some_default_folder_id', # Ajusta esto según tu lógica
                    })

                # 2. Definir nombre
                if self.contratista_proveedor_relacionado:
                    self.new_name = self.contratista_proveedor_relacionado.name

                unique_folder_id = str(uuid.uuid4())[:8]
                drive_folder_name = f"{self.new_name}_{unique_folder_id}"

                # 3. Crear en Google Drive
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()

                folder_metadata = {
                    'title': drive_folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': drive_stage.folder_id}]
                }

                folder = drive.CreateFile(folder_metadata)
                folder.Upload()

                # 4. Guardar en Odoo
                drive_file = self.env['drive.file'].create({
                    'folder_name': drive_folder_name,
                    'folder_id2': folder['id'],
                    'drive_id': drive_stage.id,
                    'select_persona': self.select_persona or False,
                })

                # 5. Asignar al registro
                self.write({'drive_file_id': drive_file.id})
                _logger.info(f"Carpeta creada exitosamente con ID: {folder['id']}")

                # 6. Crear subcarpeta Informes dentro de la carpeta del contratista
                drive_file._crear_carpeta_informes()

            except Exception as e:
                _logger.error(f"Error creando carpeta Drive: {e}")
            finally:
                self.proceso_en_ejecucion = False

    def assign(self):
        if self.select_persona in ['persona_natural', 'contrato_laboral']:
            self._onchange_copy_contractor_folder()
        elif self.select_persona == 'persona_juridica':
            self._onchange_copy_contractor_folder()

    drive_stage_provedor_id = fields.Many2one('drive_stage_provedor', string='Drive Stage Provedor',
                                              related='contratista_proveedor_relacionado.drive_stage_provedor_id',
                                              store=True)

    # -------------------------------------------------------------------------
    # 2. NUEVA FUNCIÓN: Solo obtiene la lista (Para que JS sepa qué copiar)
    # -------------------------------------------------------------------------
    @api.model
    def js_get_files_list(self, record_id):
        """Devuelve SOLO los archivos faltantes. Limpia TODOS los códigos UUID y extensiones para evitar duplicados."""
        record = self.browse(record_id)
        # Carga el registro del contrato en memoria y verifica que tenga un "contratista" asignado y una "carpeta de destino" (drive_file_id)
        
        if not record.contratista_proveedor_relacionado or not record.drive_file_id:
            return []

        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()


            
            #Se conecta a Google Drive usando tus credenciales. luego, busca cuál es la carpeta personal de ese contratista 
            # (de donde sacaremos los archivos originales). 
            # Si el contratista no tiene carpeta en Drive, aborta devolviendo []
            contratista_folder = record.contratista_proveedor_relacionado.drive_stage_provedor_id
            if not contratista_folder or not contratista_folder.folder_id:
                return []

            # =====================================================================
            # FUNCIÓN MAESTRA DE LIMPIEZA DE NOMBRES (MULTIFORMATO Y SIN CÓDIGOS)
            # =====================================================================
            # Odoo y Google Drive a veces agregan códigos raros o la gente sube copias. 
            # Esta función limpia todo eso para poder comparar los archivos de forma precisa, 
            # sin que importen esos textos basura
            def extraer_concepto(filename):
                name, _ = os.path.splitext(filename) # Ignora si es .pdf o .jpg
                
                # 1. Quitamos "(1)", "(2)", "Copia de" primero para despejar el final
                name = re.sub(r'\s*\(\d+\)', '', name) 
                name = re.sub(r'(?i)^Copia\s+de\s+', '', name)
                name = re.sub(r'(?i)\s*-\s*copia.*', '', name)
                name = name.strip()

                # 2. Quitamos el código final de 8 caracteres si existe (ej: _32272f7b)
                name = re.sub(r'_[a-fA-F0-9]{8}$', '', name)
                
                # 3. Quitamos el UUID de 36 caracteres si quedó al final (ej: _d1a56f3b-...)
                name = re.sub(r'_[a-fA-F0-9\-]{36}$', '', name)
                
                return name.strip().lower()

            # =====================================================================
            # 1. SABER QUÉ CONCEPTOS EXACTOS YA EXISTEN EN ODOO
            # =====================================================================
            # Busca en la base de datos de Odoo todos los documentos que ya están metidos en la carpeta de este contrato. luego, 
            # los pasa por la "lavadora" (extraer_concepto) y los guarda en una lista (conceptos_en_odoo).
            existing_docs = self.env['drive'].sudo().search([
                ('stage2_id', '=', record.drive_file_id.id),
                ('file_name', '!=', False)
            ])
            
            conceptos_en_odoo = set()
            for doc in existing_docs:
                concepto = extraer_concepto(doc.file_name)
                conceptos_en_odoo.add(concepto)

            # =====================================================================
            # 2. CONSULTAR DRIVE Y FILTRAR
            # =====================================================================

            # Le pide a Google Drive todos los archivos que están adentro de la carpeta del contratista y que no estén en la papelera. 
            # luego, los ordena del más nuevo al más viejo.
            query = f"'{contratista_folder.folder_id}' in parents and trashed=false"
            raw_file_list = drive.ListFile({'q': query}).GetList()
            # Ordenamos por fecha (el más nuevo arriba)
            raw_file_list.sort(key=lambda x: x.get('modifiedDate', ''), reverse=True)

            # Prepara un diccionario vacío (archivos_unicos_drive = {}) que será la "caja final" 
            # donde guardaremos los archivos que pasen las pruebas. 
            # luego, empieza a revisar los archivos de Drive uno por uno (for f in raw_file_list)
            archivos_unicos_drive = {}
            for f in raw_file_list:
                titulo_original = f['title']
                concepto_drive = extraer_concepto(titulo_original)
                
                # --- A. EL CANDADO PRINCIPAL (FOTO FIJA) ---
                # Pregunta: ¿Este concepto ("rut") ya existe en la carpeta de Odoo?
                if concepto_drive in conceptos_en_odoo:
                    continue
                
                # --- B. CONGELAMIENTO DE TIEMPO ---
                drive_created_str = f.get('createdDate') 
                if drive_created_str:
                    try:
                        # datetime.strptime(...): Drive entrega la fecha en un formato de texto feo como "2026-03-05T10:30:00.000Z". 
                        # esta línea corta los milisegundos ([:19]) y convierte
                        # ese texto en una fecha matemática real que Python puede entender y comparar.
                        drive_created_dt = datetime.strptime(drive_created_str[:19], '%Y-%m-%dT%H:%M:%S')
                        # Pregunta: ¿este archivo de Drive se creó después de que se creara el contrato en Odoo + 5 minutos de gracia?
                        if drive_created_dt > (record.create_date + timedelta(minutes=5)):
                            continue
                    except Exception:
                        pass

                # --- C. AGRUPACIÓN MULTI-FORMATO ---
                # Pregunta: ¿ya metí un "rut" en mi caja final (archivos_unicos_drive) durante este bucle? Si no lo he metido, lo guardo.
                if concepto_drive not in archivos_unicos_drive:
                    archivos_unicos_drive[concepto_drive] = f

            # =====================================================================
            # 3. EMPAQUETAR LOS QUE REALMENTE FALTAN
            # =====================================================================
            # Toma todos los archivos sobrevivientes a los filtros y arma "cajas" (diccionarios) con toda la información que el javaScript necesita para copiar el archivo 
            # (ID de origen, a qué carpeta va, y a qué proyecto pertenece). 
            # y finalmente se los devuelve al JS.
            files_to_sync = []
            for file in archivos_unicos_drive.values():
                files_to_sync.append({
                    'id': file['id'],
                    'title': file['title'],
                    'mimeType': file['mimeType'],
                    'dest_folder_id': record.drive_file_id.folder_id2,
                    'dest_folder_odoo_id': record.drive_file_id.id,
                    'contratista_id': record.contratista_proveedor_relacionado.id,
                    'proyecto_id': record.proyecto_relacionado.id
                })
            
            return files_to_sync

        except Exception as e:
            _logger.error(f"Error js_get_files_list: {e}")
            return []

    @api.model
    def js_get_all_files(self, record_id):
        """Devuelve TODOS los archivos del contratista. Reutilizamos la lógica blindada."""
        return self.js_get_files_list(record_id)

    @api.model
    def js_get_all_files(self, record_id):
        """Devuelve TODOS los archivos del contratista. Reutilizamos la lógica blindada."""
        return self.js_get_files_list(record_id)
    # -------------------------------------------------------------------------
    # 3. NUEVA FUNCIÓN: Copia 1 Solo Archivo (Tu lógica exacta de copia)
    # -------------------------------------------------------------------------
    @api.model
    def js_copy_single_file(self, file_data):
        """El JS llama a esto repetidamente para copiar uno por uno."""
        try:
            # Primero, se conecta a Google Drive. Luego, toma el nombre del archivo (ejemplo: "rut.pdf") y lo parte en dos: el nombre ("rut") 
            # y la extensión (".pdf"). finalmente, genera un código único de 8 letras y números al azar (el unique_id) y lo pega al final.
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()
            google_service = drive.auth.service 
            
            base_name, ext = os.path.splitext(file_data['title'])
            unique_id = str(uuid.uuid4())[:8]
            new_file_name = f"{base_name}_{unique_id}{ext}"
            # Crea un diccionario (la caja del paquete) con las instrucciones para Google Drive.
            metadata_body = {
                'title': new_file_name, # 'title': El nuevo nombre con el código único.
                'parents': [{'id': file_data['dest_folder_id']}], # 'parents': Le dice a Drive exactamente en qué carpeta de Odoo debe soltar la copia.
                'properties': { # 'properties': Le pega unas "etiquetas ocultas" al archivo en Drive. Aunque el usuario no las vea, el archivo sabrá a qué proyecto y a qué contratista pertenece.                            
                    'proyecto_id': str(file_data['proyecto_id']),
                    'contratista_id': str(file_data['contratista_id'])
                }
            }

            # Aquí ocurre la magia real en internet. Le dice a Google: "Toma el archivo original (fileId), hazle una copia usando mi etiqueta (body), y cuando termines, 
            # devuélveme solo estos 4 datos: el ID nuevo, el link para verlo, el link alternativo y el link para incrustarlo (fields)".
            request = google_service.files().copy(
                fileId=file_data['id'],
                body=metadata_body,
                fields='id, webViewLink, alternateLink, embedLink' 
            )
            copied_file = request.execute()

            # --- VALIDACIÓN CORREGIDA ---
            # Verifica que Google realmente nos haya devuelto un ID nuevo. Si no hay ID, 
            # lanza un error que el javaScript detectará para intentar copiarlo de nuevo (los famosos 3 reintentos).
            file_id = copied_file.get('id')
            
            if not file_id:
                raise Exception("Drive no retornó un ID. Posible fallo de red.")
            # ---------------------------

            # Ahora que el archivo ya existe físicamente en Google Drive, Odoo crea un registro en su propia tabla (self.env['drive']). 
            # aquí es donde guarda cómo se llama, quién lo subió y guarda todos los links. Al final, le devuelve al JavaScript un mensaje de "éxito".

            self.env['drive'].sudo().create({
                'file_name': new_file_name,
                'stage2_id': file_data['dest_folder_odoo_id'],
                'proyecto_relacionado': int(file_data['proyecto_id']),
                'file_id': file_id,
                'original_file_id': file_data['id'],
                'file_url': copied_file.get('alternateLink', ''),
                'preview_url': copied_file.get('webViewLink', ''),
                'edit_url': copied_file.get('alternateLink', ''),
                'preview_html': f'<iframe src="{copied_file.get("embedLink", "")}" width="100%" height="600"></iframe>',
                'edit_html': f'<iframe src="{copied_file.get("alternateLink", "")}" width="100%" height="600"></iframe>',
                'tag_ids': [(6, 0, [])],
                'show_on_dashboard': False,
                'user_id': self.env.user.id,
                'fch_creacion': fields.Datetime.now(),
            })
            
            return {'status': 'success', 'file': new_file_name}

        except Exception as e:
            _logger.error(f"Error copiando archivo individual: {e}")
            return {'status': 'error', 'message': str(e)}


    # -------------------------------------------------------------------------
    # 1. EL BOTÓN (AHORA SOLO DISPARA EL JS, NO BLOQUEA ODOO)
    # -------------------------------------------------------------------------
    def update_missing_documents(self):
        """ Este botón ya no hace el trabajo pesado. Solo le avisa al JS que inicie. """
        if not self.contratista_proveedor_relacionado:
            raise UserError("No hay un contratista relacionado.")
            
        return {
            'type': 'ir.actions.client',
            'tag': 'matriz.trigger_update_toast',
            'params': {
                'record_id': self.id
            }
        }

    # -------------------------------------------------------------------------
    # 2. MINI-FUNCIONES PARA QUE EL JS TRABAJE ARCHIVO POR ARCHIVO
    # -------------------------------------------------------------------------
    @api.model
    def js_get_all_files(self, record_id):
        """Devuelve TODOS los archivos del contratista (solo la versión más reciente) para que JS los actualice."""
        record = self.browse(record_id)
        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()
            contratista_folder = record.contratista_proveedor_relacionado.drive_stage_provedor_id
            
            if not contratista_folder or not contratista_folder.folder_id:
                return []
            
           # Le pide a Drive que le entregue absolutamente todos los archivos de esa carpeta que no estén en la basura (trashed=false).

            query = f"'{contratista_folder.folder_id}' in parents and trashed=false"
            raw_file_list = drive.ListFile({'q': query}).GetList()
            
            # ==================================================================
            # CAMBIO 1: AGRUPAR IGNORANDO EXTENSIÓN PARA EVITAR DUPLICADOS
            # ==================================================================
            # La línea raw_file_list.sort(...) toma esa montaña de archivos y los acomoda en fila, del más recién modificado al más antiguo. 
            # esto es vital para el siguiente paso.
            raw_file_list.sort(key=lambda x: x.get('modifiedDate', ''), reverse=True)

            # Aquí aplicamos de nuevo el súper poder de re.sub. imagina que el contratista tiene estos 3 archivos en Drive
            archivos_unicos = {}
            for file in raw_file_list:
                titulo = file['title']
                
                # Quitamos .pdf, .jpg y los códigos UUID al final para agruparlos bien
                nombre_base = re.sub(r'\.[a-zA-Z0-9]+$', '', titulo) 
                nombre_base = re.sub(r'_[a-fA-F0-9\-]+$', '', nombre_base) 
                nombre_base = re.sub(r'\s*\(\d+\)', '', nombre_base).strip().lower()

                if nombre_base not in archivos_unicos:
                    archivos_unicos[nombre_base] = file
            
            file_list = list(archivos_unicos.values())
            # ==================================================================
            # Toma la lista final (donde solo sobrevivió la versión más reciente de cada archivo)
            #  y arma los "paquetes" de datos con los IDs y destinos para que el javaScript pueda hacer su trabajo de copia/actualización.
            files_to_sync = []
            for file in file_list:
                files_to_sync.append({
                    'id': file['id'],
                    'title': file['title'],
                    'dest_folder_id': record.drive_file_id.folder_id2,
                    'dest_folder_odoo_id': record.drive_file_id.id,
                    'contratista_id': record.contratista_proveedor_relacionado.id,
                    'proyecto_id': record.proyecto_relacionado.id
                })
            return files_to_sync
        except Exception as e:
            _logger.error(f"Error js_get_all_files: {e}")
            return []

    @api.model
    def js_update_single_file(self, file_data):
        """Elimina el viejo, hace copia nativa y actualiza en Odoo. Llamado por JS."""
        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()
            DriveModel = self.env['drive'].sudo()
            

            base_name, ext = os.path.splitext(file_data['title'])
            unique_id = str(uuid.uuid4())[:8]
            new_file_name = f"{base_name}_{unique_id}{ext}"
            
            # ==================================================================
            # CAMBIO 2: BUSCAR EL VIEJO POR SU NOMBRE BASE EN ODOO, NO POR ID
            # Si el contratista subió un archivo nuevo, el ID de Drive cambia.
            # Al buscarlo por nombre, lo encontramos siempre para reemplazarlo.
            # ==================================================================
            nombre_buscar = re.sub(r'_[a-fA-F0-9\-]+$', '', base_name)
            nombre_buscar = re.sub(r'\s*\(\d+\)', '', nombre_buscar).strip()

            existing_record = DriveModel.search([
                ('file_name', '=ilike', f"{nombre_buscar}_%"), 
                ('stage2_id', '=', file_data['dest_folder_odoo_id']),
                ('proyecto_relacionado', '=', file_data['proyecto_id']),
            ], limit=1)
            # ==================================================================

            # Si encontró un archivo viejo en el paso anterior, le dice a Google Drive: "busca este archivo por su ID y elimínalo (mándalo a la papelera)".
            if existing_record and existing_record.file_id:
                try:
                    old_file = drive.CreateFile({'id': existing_record.file_id})
                    old_file.Delete() 
                except Exception:
                    pass

            # 2. Copia nativa
            # Hace la copia del archivo nuevo. Ojo aquí: A diferencia de la función js_copy_single_file que usaba la API nativa de Google v3, esta función usa PyDrive (.CreateFile(), .Copy(), .Upload()). 
            # es una forma más antigua pero efectiva de hacerlo. Le pone el nuevo nombre con el código y lo sube.
            orig_file = drive.CreateFile({'id': file_data['id']})
            new_file = orig_file.Copy()
            new_file['title'] = new_file_name
            new_file['parents'] = [{'id': file_data['dest_folder_id']}]
            new_file['properties'] = {
                 'proyecto_id': str(file_data['proyecto_id']),
                 'contratista_id': str(file_data['contratista_id'])
            }
            new_file.Upload()
            
            file_id = new_file['id']
            if not file_id:
                raise Exception("Drive no retornó ID válido. Posible micro-corte.")

            # 3. Guardar en Odoo
            record_vals = {
                'file_name': new_file_name,
                'file_id': file_id, 
                'file_url': new_file.get('alternateLink', ''),
                'preview_url': new_file.get('webViewLink', ''),
                'edit_url': new_file.get('alternateLink', ''),
                'preview_html': f'<iframe src="{new_file.get("embedLink", "")}" width="100%" height="600"></iframe>',
                'edit_html': f'<iframe src="{new_file.get("alternateLink", "")}" width="100%" height="600"></iframe>',
                'fch_creacion': fields.Datetime.now(),
            }
            
            if existing_record:
                existing_record.write(record_vals)
            else:
                record_vals.update({
                    'stage2_id': file_data['dest_folder_odoo_id'],
                    'proyecto_relacionado': file_data['proyecto_id'],
                    'original_file_id': file_data['id'], 
                    'tag_ids': [(6, 0, [])],
                    'show_on_dashboard': False,
                    'user_id': self.env.user.id,
                })
                DriveModel.create(record_vals)
                
            return {'status': 'success', 'file': new_file_name}

        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    @api.model
    def js_finish_update(self, record_id):
        """Llamado al final de la barra progresiva para limpiar y trazar."""
        record = self.browse(record_id)
        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()
            record.limpiar_duplicados(drive, record.drive_file_id.id, record.proyecto_relacionado.id)
            record.assign_docs_all() # Llama a tu función optimizada que no hace spam
        except Exception as e:
            _logger.error(f"Error al finalizar actualización: {e}")
    # ------------------------------------------------------------------
    # 3. ASIGNACIÓN CON TRAZABILIDAD INTELIGENTE (NO SPAM)
    # ------------------------------------------------------------------

    def assign_docs_all(self):
        if self.select_persona in ['persona_natural', 'contrato_laboral']:
            self.assign_documents_part1()
            self.assign_documents_part2()
            self.assign_documents_part3()
        elif self.select_persona == 'persona_juridica':
            self.assign_documents_persona_juridica_part1()
            self.assign_documents_persona_juridica_part2()
            self.assign_documents_persona_juridica_part3()


    # ---- FUNCIONES PERSONA JURÍDICA ----

    def assign_documents_persona_juridica_part3(self):
        doc_mapping = {
            '13_Estados financieros': ('estados_financieros_juridico_doc_id', 'estados_financieros_juridico_doc_visible'),
            'Documento de identidad contador': ('Documento_de_identidad_contador_doc_id', 'Documento_de_identidad_contador_doc_visible'),
            'Tarjeta profesional revisor fiscal': ('Tarjeta_profesional_revisor_fiscal_doc_id', 'Tarjeta_profesional_revisor_fiscal_doc_visible'),
            'Tarjeta profesional contador': ('Tarjeta_profesional_contador_doc_id', 'Tarjeta_profesional_contador_doc_visible'),
        }
        self._procesar_asignacion_y_trazabilidad(doc_mapping)

    def assign_documents_persona_juridica_part1(self):
        doc_mapping = {
            '1_Propuesta': ('propuesta_pj_doc_id', 'propuesta_pj_doc_visible'),
            '2_Cámara de comercio': ('camara_comercio_doc_id', 'camara_comercio_doc_visible'),
            '3_RUT': ('rut_pj_doc_id', 'rut_pj_doc_visible'),
            '4_RUP': ('rup_doc_id', 'rup_doc_visible'),
            '5_Hoja de vida jurídica_': ('hoja_vida_juridica_doc_id', 'hoja_vida_juridica_doc_visible'),
            '6_Certificado contraloría': ('cert_contraloria_doc_id', 'cert_contraloria_doc_visible'),
            '7_Certificado procuraduría': ('cert_procuraduria_doc_id', 'cert_procuraduria_doc_visible'),
            '8_Cédula representante legal': ('cedula_rep_legal_doc_id', 'cedula_rep_legal_doc_visible'),
            '9_Hoja de vida representante legal_': ('hoja_vida_rep_legal_doc_id', 'hoja_vida_rep_legal_doc_visible'),
            '10_Cert_contraloría representante legal': ('cert_contraloria_rep_legal_doc_id', 'cert_contraloria_rep_legal_doc_visible'),
        }
        self._procesar_asignacion_y_trazabilidad(doc_mapping)

    def assign_documents_persona_juridica_part2(self):
        doc_mapping = {
            '14_Cert_policía judicial_medidas cautelares': ('cert_policia_judicial_doc_id', 'cert_policia_judicial_doc_visible'),
            '11_Cert_procuraduría representante legal': ('cert_procuraduria_rep_legal_doc_id', 'aprueba_j_h_contraloria_rep_lega_doc_visible'),
            '15_Certificado inhabilidades': ('cert_inhabilidades_doc_id', 'cert_inhabilidades_doc_visible'),
            '16_Resolución de facturación electrónica': ('resolucion_facturacion_doc_id', 'resolucion_facturacion_doc_visible'),
            '17_Experiencia laboral': ('experiencia_laboral_doc_id', 'experiencia_laboral_doc_visible'),
            '18_Certificado pago parafiscales': ('certificado_pago_parafiscales_doc_id', 'certificado_pago_parafiscales_doc_visible'),
            '19_Planilla de seguridad social': ('planilla_seguridad_social_doc_id', 'planilla_seguridad_social_doc_visible'),
            '20_Certificado junta de contadores': ('certificado_junta_contadores_doc_id', 'certificado_junta_contadores_doc_visible'),
            '21_Documento de identidad revisor fiscal': ('cedula_revisor_fiscal_doc_id', 'cedula_revisor_fiscal_doc_visible'),
            '22_Certificado bancario': ('certificado_bancario_doc_id', 'certificado_bancario_doc_visible'),
        }
        self._procesar_asignacion_y_trazabilidad(doc_mapping)


    # ---- FUNCIONES PERSONA NATURAL ----

    def assign_documents_part1(self):
        doc_mapping = {
            '1_Documento de identidad': ('documento_identidad_id', 'documento_identidad_pn_doc_visible'),
            '2_RUT': ('rut_id', 'rut_pn_doc_visible'),
            '3_Hoja de vida': ('hoja_vida_id', 'hoja_vida_pn_doc_visible'),
            '4_Certificado estudio': ('certificados_estudio_id', 'certificados_estudio_doc_visible'),
            '5_Certificado laborales': ('certificados_laborales_id', 'certificados_laborales_doc_visible'),
            '6_Cert_ARL o formato ARL FUV': ('certificado_arl_id', 'certificado_arl_doc_visible'),
            '7_Cert_afiliación salud': ('certificacion_salud_id', 'certificacion_salud_doc_visible'),
            '8_Cert_afiliación a pensión': ('certificacion_pension_id', 'certificacion_pension_doc_visible'),
        }
        self._procesar_asignacion_y_trazabilidad(doc_mapping)

    def assign_documents_part2(self):
        doc_mapping = {
            '11_Resolución de facturación electrónica': ('resolucion_facturacion_ju_id', 'resolucion_facturacion_ju_doc_visible'),
            '13_Cert_contraloría': ('certificacion_contraloria_id', 'certificacion_contraloria_doc_visible'),
            '14_Cert_procuraduría': ('certificacion_procuraduria_id', 'certificacion_procuraduria_doc_visible'),
            '15_Cert_policía judicial': ('certificacion_policia_judicial_id', 'certificacion_policia_judicial_doc_visible'),
            '16_Cert_medidas correctivas': ('certificacion_medidas_cautelares_id', 'certificacion_medidas_cautelares_doc_visible'),
            '17_Cert_inhabilidades': ('certificacion_inhabilidades_id', 'certificacion_inhabilidades_doc_visible'),
            '18_Cert_libreta militar': ('certificacion_libreta_militar_id', 'certificacion_libreta_militar_doc_visible'),
            '19_Resolución de pensionado vigente': ('resolucion_pensionado_id', 'resolucion_pensionado_doc_visible'),
            '20_Certificado bancario': ('certificacion_bancaria_id', 'certificacion_bancaria_doc_visible'),
        }
        self._procesar_asignacion_y_trazabilidad(doc_mapping)

    def assign_documents_part3(self):
        doc_mapping = {
            '22_Declaración juramentada': ('declaracion_juramentada_id', 'declaracion_juramentada_doc_visible'),
            '21_Tarjeta profesional': ('tarjeta_profesional_doc_id', 'tarjeta_profesional_doc_visible'),
            '9_Cert_examen preocupacional': ('certificacion_examen_preocupacional_id', 'certificacion_examen_preocupacional_doc_visible'),
        }
        self._procesar_asignacion_y_trazabilidad(doc_mapping)


    # ------------------------------------------------------------------
    # NUEVA FUNCIÓN OPTIMIZADA (Para no repetir código en cada part)
    # ------------------------------------------------------------------
    def _procesar_asignacion_y_trazabilidad(self, doc_mapping):
        """
        Función centralizada que verifica si el documento ya estaba asignado.
        Solo publica en el chatter si el documento es nuevo o fue actualizado.
        """
        documentos = self.env['drive'].search_read(
            [('stage2_id', '=', self.drive_file_id.id)], ['id', 'file_name']
        )

        for doc in documentos:
            for key, (field_id, field_visible) in doc_mapping.items():
                if key in doc['file_name']:
                    # Obtenemos el registro que actualmente está asignado
                    valor_actual = getattr(self, field_id)
                    
                    # Verificamos: ¿Está vacío? ¿O el ID es distinto al que queremos asignar?
                    if not valor_actual or valor_actual.id != doc['id']:
                        setattr(self, field_id, doc['id'])
                        setattr(self, field_visible, False)
                        
                        # --- TRAZABILIDAD (SOLO SE EJECUTA UNA VEZ POR DOC) ---
                        nombre_limpio = key.split('_', 1)[-1] if '_' in key else key
                        mensaje = (
                            f"✅ <b>{nombre_limpio}</b>: Documento traído de la base de datos de contratistas y proveedores "
                            f"y verificado en Google Drive exitosamente. (Archivo: {doc['file_name']})"
                        )
                        self.message_post(body=mensaje)
                        # --------------------------------------------------------
                        _logger.info(f"{key} asignado exitosamente: {doc['file_name']}")
                    else:
                        pass # Ya estaba asignado, no hacemos spam en el chatter

                    break # Salimos del bucle interno ya que encontramos coincidencia

    @api.onchange('contratista_proveedor_relacionado')
    def _onchange_contratista_proveedor_relacionado(self):
        """
        Actualiza campos inmediatamente cuando se selecciona un contratista/proveedor.
        También sincroniza los documentos de identidad y su visibilidad.
        """
        if self.contratista_proveedor_relacionado:
            if self.select_persona in ['persona_natural', 'contrato_laboral']:
                self.new_name = self.contratista_proveedor_relacionado.name
            elif self.select_persona == 'persona_juridica':
                self.new_name = self.contratista_proveedor_relacionado.name
        else:
            self.new_name = ''
            
    def write(self, vals):
        """Sobrescribe el método 'write' para restringir edición y generar trazabilidad."""
        self._validar_contratista_requerido_para_no_soporte(vals)

        if self.env.context.get('soporte_cambio_estado_fc') and set(vals) == {'estado_contratacion'}:
            if not self.env.user.has_group('Matriz.group_soporte'):
                raise AccessError('Solo Soporte puede cambiar manualmente el estado.')
            result = super().write(vals)
            self._marcar_proyecto_en_progreso_desde_fc()
            return result

        if self._es_consulta_coordinacion_financiera():
            return True

        # Un anticipo completado actualiza saldos calculados de forma interna.
        # No es una edición contractual del usuario de Tesorería y solo puede
        # entrar aquí desde código con superusuario.
        if (self.env.su and
                self.env.context.get('recalculo_anticipo_completado')):
            return super().write(vals)

        # Permite solo la actualizacion tecnica del tipo de actividad que se
        # dispara desde _onchange_fecha al crear contratos nuevos.
        if self.env.context.get('skip_fc_activity_type_security') and set(vals.keys()) <= {'activity_type_id'}:
            return super().write(vals)

        user = self.env.user
        
        # 1. Validaciones de Seguridad (Pre-escritura)
        # La gestión posterior del AT solo exceptúa la actualización técnica
        # de write_date. Cualquier otro campo conserva las reglas por estado.
        apertura_at_posterior = self._es_escritura_at_posterior_presupuesto(vals)

        # La asignación de pólizas solo comparte acceso jurídico: no edita
        # datos contractuales ni debe quedar bloqueada por la etapa del contrato.
        asignacion_juridica_polizas = bool(
            self.env.su
            and self.env.context.get('gestion_asignacion_juridica_polizas')
            and set(vals) == {'usuarios_juridico_ids'}
            and user.has_group('Matriz.group_contratacion_director_juridico')
            and all(
                isinstance(comando, (tuple, list))
                and len(comando) >= 2
                and comando[0] == 4
                for comando in vals['usuarios_juridico_ids']
            )
        )

        for record in self.filtered(
            lambda item: not apertura_at_posterior and not asignacion_juridica_polizas
        ):
            # Compatibilidad solicitada para el flujo anterior: el equipo
            # financiero conserva edición en cualquier estado. Esta excepción
            # no aplica al flujo nuevo, que mantiene su matriz estricta en
            # contrataciones_fuv_aprobaciones.py.
            es_equipo_financiero_flujo_anterior = (
                not record.usa_flujo_contratacion_nuevo
                and (
                    user.has_group('Matriz.group_contratacion_financiero')
                    or user.has_group('Matriz.group_contratacion_director_financiero')
                    or user.has_group('Matriz.group_presupuestos')
                )
            )
            if es_equipo_financiero_flujo_anterior:
                continue

            if record.estado_contratacion == 'coordinador':
                if not (user.has_group('Matriz.group_contratacion_coordinador') or
                        user.has_group('Matriz.group_contratacion_asis_coordinador')):
                    raise UserError("⛔ Solo el Coordinador o su Asistente pueden editar en este estado.")

            elif record.estado_contratacion == 'financiera':
                if not (user.has_group('Matriz.group_contratacion_financiero') or 
                        user.has_group('Matriz.group_contratacion_director_financiero') or 
                        user.has_group('Matriz.group_presupuestos')):
                    raise UserError("⛔ Solo el equipo Financiero o Presupuestos pueden editar en este estado.")

            elif record.estado_contratacion == 'juridico':
                if not (user.has_group('Matriz.group_contratacion_juridico') or 
                        user.has_group('Matriz.group_contratacion_director_juridico')):
                    valores_resumidos = {}
                    for campo, valor in vals.items():
                        if isinstance(valor, str) and len(valor) > 300:
                            valores_resumidos[campo] = valor[:300] + '...<truncado>'
                        elif isinstance(valor, (bytes, bytearray)):
                            valores_resumidos[campo] = '<binario>'
                        elif isinstance(valor, list) and len(valor) > 10:
                            valores_resumidos[campo] = valor[:10] + ['...<truncado>']
                        else:
                            valores_resumidos[campo] = valor

                    grupos_clave = {
                        'coordinador': user.has_group('Matriz.group_contratacion_coordinador'),
                        'asistente_coordinador': user.has_group('Matriz.group_contratacion_asis_coordinador'),
                        'financiero': user.has_group('Matriz.group_contratacion_financiero'),
                        'director_financiero': user.has_group('Matriz.group_contratacion_director_financiero'),
                        'presupuesto': user.has_group('Matriz.group_presupuestos'),
                        'director_juridico': user.has_group('Matriz.group_contratacion_director_juridico'),
                        'juridico': user.has_group('Matriz.group_contratacion_juridico'),
                        'recursos_humanos': user.has_group('Matriz.group_contratacion_recursos'),
                        'soporte': user.has_group('Matriz.group_soporte'),
                        'base_group_system': user.has_group('base.group_system'),
                    }
                    try:
                        grupos_usuario = list(user.groups_id.get_external_id().values())
                    except Exception:
                        grupos_usuario = user.groups_id.mapped('display_name')

                    _logger.warning(
                        "[DIAGNOSTICO-FC-PERMISO-JURIDICO] Bloqueo en write de contrataciones_fuv. "
                        "Usuario: id=%s login=%s nombre=%s | Grupos clave=%s | Grupos usuario=%s | "
                        "Registro: id=%s name=%s estado=%s flujo_nuevo=%s select_persona=%s proyecto_id=%s proyecto=%s "
                        "contratista_id=%s contratista=%s creador_id=%s creador=%s | "
                        "Vals keys=%s | Vals resumidos=%s | Contexto=%s",
                        user.id,
                        user.login,
                        user.name,
                        grupos_clave,
                        grupos_usuario,
                        record.id,
                        record.display_name,
                        record.estado_contratacion,
                        record.usa_flujo_contratacion_nuevo,
                        record.select_persona,
                        record.proyecto_relacionado.id if record.proyecto_relacionado else False,
                        record.proyecto_relacionado.display_name if record.proyecto_relacionado else False,
                        record.contratista_proveedor_relacionado.id if record.contratista_proveedor_relacionado else False,
                        record.contratista_proveedor_relacionado.display_name if record.contratista_proveedor_relacionado else False,
                        record.create_uid.id if record.create_uid else False,
                        record.create_uid.name if record.create_uid else False,
                        list(vals.keys()),
                        valores_resumidos,
                        dict(self.env.context),
                    )
                    raise UserError("⛔ Solo el equipo Jurídico puede editar en este estado.")

            elif record.estado_contratacion == 'recursos_humanos':
                if not user.has_group('Matriz.group_contratacion_recursos'):
                    
                    raise UserError("⛔ Solo Recursos Humanos puede editar en este estado.")

            # === Lógica para CONTRATADO ===
        #Es vital que esto ocurra antes del super para que el UserError detenga el proceso.

        # 2. Preparar Trazabilidad (Antes de que el super borre datos)
        trazabilidad_por_registro = {}
        if 'departamentos_y_municipios_ids' in vals:
            for record in self:
                mensajes = []
                for command in vals.get('departamentos_y_municipios_ids'):
                    # COMANDO 0: NUEVO
                    if command[0] == 0:
                        data = command[2]
                        dep = self.env['departamento'].browse(data.get('departamentos')).name or 'N/A'
                        mun = self.env['municipio'].browse(data.get('municipios2')).name or 'N/A'
                        mensajes.append(f"<li>🟢 <b>Agregado:</b> {dep} - {mun}</li>")
                    
                    # COMANDO 1: EDITAR
                    elif command[0] == 1:
                        linea = self.env['departamento_y_municipio'].browse(command[1])
                        nombre_dep = linea.departamentos.name or 'N/A'
                        nombre_mun = linea.municipios2.name or 'N/A'
                        mensajes.append(f"<li>🟡 <b>Modificado:</b> {nombre_dep} - {nombre_mun}</li>")
                    
                    # COMANDO 2: ELIMINAR (Aquí es donde era crítico hacerlo antes del super)
                    elif command[0] in [2, 3]:
                        linea = self.env['departamento_y_municipio'].browse(command[1])
                        nombre_dep = linea.departamentos.name or 'N/A'
                        nombre_mun = linea.municipios2.name or 'N/A'
                        mensajes.append(f"<li>🔴 <b>Eliminado:</b> {nombre_dep} - {nombre_mun}</li>")
                
                if mensajes:
                    trazabilidad_por_registro[record.id] = mensajes

        previous_juridico_users_by_record = {}
        if 'usuarios_juridico_ids' in vals:
            previous_juridico_users_by_record = {
                record.id: set(record.usuarios_juridico_ids.ids)
                for record in self
            }

        # 3. Ejecución de la escritura original
        res = super(Contrataciones_Fuv, self).write(vals)

        # 4. Validaciones Post-escritura y Publicación de Mensajes
        for record in self:
            # Validaciones de campos obligatorios (compromiso_presupuestal, etc.)
            if any(f in vals for f in ['compromiso_presupuestal', 'selecionar_todos_f']):
                es_presupuesto = user.has_group("Matriz.group_presupuestos")
                if es_presupuesto and not record.compromiso_presupuestal:
                    raise ValidationError("⚠️ El campo 'RPC' es obligatorio.")

            # Publicar trazabilidad si existe para este registro
            if record.id in trazabilidad_por_registro:
                body = "<p><b>Cambios en Departamentos y Municipios:</b></p><ul>" + "".join(trazabilidad_por_registro[record.id]) + "</ul>"
                record.message_post(body=body)

            if 'usuarios_juridico_ids' in vals:
                record._registrar_trazabilidad_usuarios_juridico(
                    previous_user_ids=previous_juridico_users_by_record.get(record.id, set())
                )
                if not record.drive_file_id:
                    record.action_link_drive_file()
                if record.drive_file_id:
                    record._share_file_with_new_users_juridico(
                        previous_user_ids=previous_juridico_users_by_record.get(record.id, set())
                    )
                else:
                    _logger.warning("No se estableció un archivo relacionado; no se compartirán usuarios jurídicos.")

        if any(f in vals for f in ['proyecto_relacionado', 'estado_contratacion']):
            self._marcar_proyecto_en_progreso_desde_fc()

        return res

    #######################################################################################################################################


    @api.constrains('propuesta_pj_doc')
    def _check_file_extension22(self):
        for record in self:
            if record.propuesta_pj_doc:
                filename = record.propuesta_pj_doc_fname

    @api.onchange('propuesta_pj_doc')
    def _onchange_propuesta_pj_doc(self):
        if self.propuesta_pj_doc:
            self.propuesta_pj = True

    @api.onchange('propuesta_pj_doc')
    def _onchange_delete_propuesta_pj_doc(self):
        if not self.propuesta_pj_doc:
            self.propuesta_pj = False

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_propuesta_pj = {}


    propuesta_doc_actualizar_visible = fields.Boolean(
        default=False,
        related='propuesta_pj_doc_id.actualizar_visible',
        store=True,
        readonly=False
    )



    @api.model
    def cargar_documento_30(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'propuesta_pj_doc': file_content,
            'propuesta_pj_doc_fname': file_name,
        })

    aprueba_f_propuesta = fields.Boolean('Aprueba-F', default=True, tracking=True, compute='_compute_true')

    aprueba_j_h_propuesta_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    camara_comercio = fields.Boolean('Cámara de comercio (Actualizada)', tracking=True)


    @api.constrains('camara_comercio_doc')
    def _check_file_extension23(self):
        for record in self:
            if record.camara_comercio_doc:
                filename = record.camara_comercio_doc_fname

    @api.onchange('camara_comercio_doc')
    def _onchange_camara_comercio_doc(self):
        if self.camara_comercio_doc:
            self.camara_comercio = True

    def _compute_true(self):
        self.aprueba_f_propuesta = True,
        self.aprueba_f_comercio = True,
        self.aprueba_f_rup = True,
        self.aprueba_f_contraloria = True,
        self.aprueba_f_procuraduria = True,
        self.aprueba_f_cedula_r = True,
        self.aprueba_f_contraloria_rep_legal = True,
        self.aprueba_f_cdp_juridico = True,
        self.aprueba_f_policia_judicial = True,
        self.aprueba_f_inhabilidades = True,
        self.aprueba_f_experiencia_laboral = True,
        self.aprueba_f_pago_parafiscales = True,
        self.aprueba_f_seguridad_social = True,
        self.aprueba_f_junta_contadores = True,
        self.aprueba_f_revisor_fiscal = True,
        self.aprueba_f_tributario_juridico = True,
        self.aprueba_f_contrato = True,
        self.aprueba_f_procuraduria_rep_legal = True,
        self.aprueba_f_bdd = True,
        self.aprueba_f_estados_financieros_juridico = True,

    @api.onchange('camara_comercio_doc')
    def _onchange_delete_camara_comercio_doc(self):
        if not self.camara_comercio_doc:
            self.camara_comercio = False

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_camara_comercio = {}


    camara_comercio_actualizar_visible = fields.Boolean(default=False, related='camara_comercio_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_28(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'camara_comercio_doc': file_content,
            'camara_comercio_doc_fname': file_name,
        })

    aprueba_f_comercio = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_c_comercio_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)


    @api.constrains('rut_pj_doc')
    def _check_file_extension24(self):
        for record in self:
            if record.rut_pj_doc:
                record.rut_pj_doc_fname

    @api.onchange('rut_pj_doc')
    def _onchange_rut_pj_doc(self):
        if self.rut_pj_doc:
            self.rut_pj = True

    @api.onchange('rut_pj_doc')
    def _onchange_delete_rut_pj_doc(self):
        if not self.rut_pj_doc:
            self.rut_pj = False

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_rut_pj = {}


    rut_pj_actualizar_visible = fields.Boolean(default=False, related='rut_pj_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_31(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'rut_pj_doc': file_content,
            'rut_pj_doc_fname': file_name,
        })

    aprueba_f_rut = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_rut_pj_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('rup_doc')
    def _check_file_extension25(self):
        for record in self:
            if record.rup_doc:
                filename = record.rup_doc_fname

    @api.onchange('rup_doc')
    def _onchange_rup_doc(self):
        if self.rup_doc:
            self.rup = True

    @api.onchange('rup_doc')
    def _onchange_delete_rup_doc(self):
        if not self.rup_doc:
            self.rup = False

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_rup = {}


    rup_actualizar_visible = fields.Boolean(default=False, related='rup_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_32(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'rup_doc': file_content,
            'rup_doc_fname': file_name,
        })

    aprueba_f_rup = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')

    aprueba_j_h_rup_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)


    @api.constrains('hoja_vida_juridica_doc')
    def _check_file_extension26(self):
        for record in self:
            if record.hoja_vida_juridica_doc:
                filename = record.hoja_vida_juridica_doc_fname

    @api.onchange('hoja_vida_juridica_doc')
    def _onchange_hoja_vida_juridica_doc(self):
        if self.hoja_vida_juridica_doc:
            self.hoja_vida_juridica = True

    @api.onchange('hoja_vida_juridica_doc')
    def _onchange_delete_hoja_vida_juridica_doc(self):
        if not self.hoja_vida_juridica_doc:
            self.hoja_vida_juridica = False

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_hoja_vida_juridica = {}


    hoja_vida_juridica_actualizar_visible = fields.Boolean(default=False, related='hoja_vida_juridica_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_33(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'hoja_vida_juridica_doc': file_content,
            'hoja_vida_juridica_doc_fname': file_name,
        })

    aprueba_f_juridica = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_hjv_juridica_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('cert_contraloria_doc')
    def _check_file_extension27(self):
        for record in self:
            if record.cert_contraloria_doc:
                filename = record.cert_contraloria_doc_fname

    @api.onchange('cert_contraloria_doc')
    def _onchange_cert_contraloria_doc(self):
        if self.cert_contraloria_doc:
            self.cert_contraloria = True

    @api.onchange('cert_contraloria_doc')
    def _onchange_delete_cert_contraloria_doc(self):
        if not self.cert_contraloria_doc:
            self.cert_contraloria = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_contraloria = {}


    cert_contraloria_actualizar_visible = fields.Boolean(default=False, related='cert_contraloria_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_34(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cert_contraloria_doc': file_content,
            'cert_contraloria_doc_fname': file_name,
        })

    aprueba_f_contraloria = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_c_contraloriaa_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('cert_procuraduria_doc')
    def _check_file_extension28(self):
        for record in self:
            if record.cert_procuraduria_doc:
                filename = record.cert_procuraduria_doc_fname

    @api.onchange('cert_procuraduria_doc')
    def _onchange_cert_procuraduria_doc(self):
        if self.cert_procuraduria_doc:
            self.cert_procuraduria = True

    @api.onchange('cert_procuraduria_doc')
    def _onchange_delete_cert_procuraduria_doc(self):
        if not self.cert_procuraduria_doc:
            self.cert_procuraduria = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_procuraduria = {}



    cert_procuraduria_actualizar_visible = fields.Boolean(default=False, related='cert_procuraduria_doc_id.actualizar_visible', store=True, readonly=False)




    @api.model
    def cargar_documento_35(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cert_procuraduria_doc': file_content,
            'cert_procuraduria_doc_fname': file_name,
        })

    aprueba_f_procuraduria = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_cprocuraduria_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('cedula_rep_legal_doc')
    def _check_file_extension29(self):
        for record in self:
            if record.cedula_rep_legal_doc:
                filename = record.cedula_rep_legal_doc_fname

    @api.onchange('cedula_rep_legal_doc')
    def _onchange_cedula_rep_legal_doc(self):
        if self.cedula_rep_legal_doc:
            self.cedula_rep_legal = True

    @api.onchange('cedula_rep_legal_doc')
    def _onchange_delete_cedula_rep_legal_doc(self):
        if not self.cedula_rep_legal_doc:
            self.cedula_rep_legal = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cedula_rep_legal = {}


    cedula_rep_legal_actualizar_visible = fields.Boolean(default=False, related='cedula_rep_legal_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_36(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cedula_rep_legal_doc': file_content,
            'cedula_rep_legal_doc_fname': file_name,
        })

    aprueba_f_cedula_r = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_cedula_r_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('hoja_vida_rep_legal_doc')
    def _check_file_extension30(self):
        for record in self:
            if record.hoja_vida_rep_legal_doc:
                filename = record.hoja_vida_rep_legal_doc_fname

    @api.onchange('hoja_vida_rep_legal_doc')
    def _onchange_hoja_vida_rep_legal_doc(self):
        if self.hoja_vida_rep_legal_doc:
            self.hoja_vida_rep_legal = True

    @api.onchange('hoja_vida_rep_legal_doc')
    def _onchange_delete_hoja_vida_rep_legal_doc(self):
        if not self.hoja_vida_rep_legal_doc:
            self.hoja_vida_rep_legal = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_hoja_vida_rep_legal = {}


    hoja_vida_rep_legal_actualizar_visible = fields.Boolean(default=False, related='hoja_vida_rep_legal_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_37(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'hoja_vida_rep_legal_doc': file_content,
            'hoja_vida_rep_legal_doc_fname': file_name,
        })

    aprueba_f_hjv_r = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_hjv_r = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('cert_contraloria_rep_legal_doc')
    def _check_file_extension31(self):
        for record in self:
            if record.cert_contraloria_rep_legal_doc:
                filename = record.cert_contraloria_rep_legal_doc_fname

    @api.onchange('cert_contraloria_rep_legal_doc')
    def _onchange_cert_contraloria_rep_legal_doc(self):
        if self.cert_contraloria_rep_legal_doc:
            self.cert_contraloria_rep_legal = True

    @api.onchange('cert_contraloria_rep_legal_doc')
    def _onchange_delete_cert_contraloria_rep_legal_doc(self):
        if not self.cert_contraloria_rep_legal_doc:
            self.cert_contraloria_rep_legal = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_contraloria_rep_legal = {}


    cert_contraloria_rep_legal_actualizar_visible = fields.Boolean(default=False, related='cert_contraloria_rep_legal_doc_id.actualizar_visible', store=True, readonly=False)




    @api.model
    def cargar_documento_38(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cert_contraloria_rep_legal_doc': file_content,
            'cert_contraloria_rep_legal_doc_fname': file_name,
        })

    aprueba_f_contraloria_rep_legal = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')

    @api.constrains('aprueba_j_h_contraloria_rep_lega_doc')
    def _check_file_extension32(self):
        for record in self:
            if record.aprueba_j_h_contraloria_rep_lega_doc:
                filename = record.aprueba_j_h_contraloria_rep_lega_doc_fname

    @api.onchange('aprueba_j_h_contraloria_rep_lega_doc')
    def _onchange_aprueba_j_h_contraloria_rep_lega_doc(self):
        if self.aprueba_j_h_contraloria_rep_lega_doc:
            self.cert_procuraduria_rep_legal = True

    @api.onchange('aprueba_j_h_contraloria_rep_lega_doc')
    def _onchange_delete_aprueba_j_h_contraloria_rep_lega_doc(self):
        if not self.aprueba_j_h_contraloria_rep_lega_doc:
            self.cert_procuraduria_rep_legal = False

    @api.model
    def cargar_documento_39(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'aprueba_j_h_contraloria_rep_lega_doc': file_content,
            'aprueba_j_h_contraloria_rep_lega_doc_fname': file_name,
        })

    cert_procuraduria_rep_legal = fields.Boolean('C-Procuraduría Rep.Legal', tracking=True)
    aprueba_f_procuraduria_rep_legal = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_procuraduria_rep_legal = {}


    cert_procuraduria_rep_legal_actualizar_visible = fields.Boolean(default=False, related='cert_procuraduria_rep_legal_doc_id.actualizar_visible', store=True, readonly=False)


    # chulear todos Persona juridica

    selecionar_todos_p_juri = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_p_juri')
    def _onchange_selecionar_todos_p_juri(self):
        if self.selecionar_todos_p_juri:
            self.cert_policia_judicial = True
            self.cert_inhabilidades = True
            self.resolucion_facturacion = True
            self.resolucion_facturacion_ju = True
            self.experiencia_laboral = True
            self.certificado_pago_parafiscales = True
            self.planilla_seguridad_social = True
            self.certificado_junta_contadores = True
            self.cedula_revisor_fiscal = True
            self.certificado_bancario = True
            self.camara_comercio = True
            self.anexo_tributario_juridico = True
            self.propuesta_pj = True
            self.rut_pj = True
            self.rup = True
            self.hoja_vida_juridica = True
            self.cert_contraloria = True
            self.cert_procuraduria = True
            self.cedula_rep_legal = True
            self.hoja_vida_rep_legal = True
            self.cert_contraloria_rep_legal = True
            self.cert_procuraduria_rep_legal = True
            self.cdp_juridico = True
            self.contrato_j = True
        else:
            self.cert_policia_judicial = False
            self.cert_inhabilidades = False
            self.resolucion_facturacion = False
            self.resolucion_facturacion_ju = False
            self.experiencia_laboral = False
            self.certificado_pago_parafiscales = False
            self.planilla_seguridad_social = False
            self.certificado_junta_contadores = False
            self.cedula_revisor_fiscal = False
            self.certificado_bancario = False
            self.camara_comercio = False
            self.anexo_tributario_juridico = False
            self.propuesta_pj = False
            self.rut_pj = False
            self.rup = False
            self.hoja_vida_juridica = False
            self.cert_contraloria = False
            self.cert_procuraduria = False
            self.cedula_rep_legal = False
            self.hoja_vida_rep_legal = False
            self.cert_contraloria_rep_legal = False
            self.cert_procuraduria_rep_legal = False
            self.cdp_juridico = False
            self.contrato_j = False

    # chulear todos Persona juridica F

    selecionar_todos_p_juri_f = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_p_juri_f')
    def _onchange_selecionar_todos_p_juri_f(self):
        if self.selecionar_todos_p_juri_f:
            self.aprueba_f_policia_judicial = True
            self.aprueba_f_inhabilidades = True
            self.aprueba_f_resolucion_facturacion = True
            self.aprueba_f_resolucion_facturacion_ju = True
            self.aprueba_f_experiencia_laboral = True
            self.aprueba_f_pago_parafiscales = True
            self.aprueba_f_seguridad_social = True
            self.aprueba_f_junta_contadores = True
            self.aprueba_f_revisor_fiscal = True
            self.aprueba_f_c_bancario = True
            self.aprueba_f_comercio = True
            self.aprueba_f_tributario_juridico = True
            self.aprueba_f_propuesta = True
            self.aprueba_f_rut = True
            self.aprueba_f_rup = True
            self.aprueba_f_juridica = True
            self.aprueba_f_contraloria = True
            self.aprueba_f_procuraduria = True
            self.aprueba_f_cedula_r = True
            self.aprueba_f_hjv_r = True
            self.aprueba_f_contraloria_rep_legal = True
            self.aprueba_f_procuraduria_rep_legal = True
            self.aprueba_f_cdp_juridico = True
            self.aprueba_f_contrato = True
        else:
            self.aprueba_f_policia_judicial = False
            self.aprueba_f_inhabilidades = False
            self.aprueba_f_resolucion_facturacion = False
            self.aprueba_f_resolucion_facturacion_ju = False
            self.aprueba_f_experiencia_laboral = False
            self.aprueba_f_pago_parafiscales = False
            self.aprueba_f_seguridad_social = False
            self.aprueba_f_junta_contadores = False
            self.aprueba_f_revisor_fiscal = False
            self.aprueba_f_c_bancario = False
            self.aprueba_f_comercio = False
            self.aprueba_f_tributario_juridico = False
            self.aprueba_f_propuesta = False
            self.aprueba_f_rut = False
            self.aprueba_f_rup = False
            self.aprueba_f_juridica = False
            self.aprueba_f_contraloria = False
            self.aprueba_f_procuraduria = False
            self.aprueba_f_cedula_r = False
            self.aprueba_f_hjv_r = False
            self.aprueba_f_contraloria_rep_legal = False
            self.aprueba_f_procuraduria_rep_legal = False
            self.aprueba_f_cdp_juridico = False
            self.aprueba_f_contrato = False

    # chulear todos Persona juridica F

    selecionar_todos_p_juri_j = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_p_juri_j')
    def _onchange_selecionar_todos_p_juri_j(self):
        if self.selecionar_todos_p_juri_j:
            self.aprueba_j_h_policia_judicial_ = True
            self.aprueba_j_h_inhabilidades_ = True
            self.aprueba_j_h_re_facturacion = True
            self.aprueba_j_h_exp_laboral_ = True
            self.aprueba_j_h_para_fiscales = True
            self.aprueba_j_h_seguridad_social_ = True
            self.aprueba_j_h_juntacontadores = True
            self.aprueba_j_h_r_fiscal = True
            self.aprueba_j_h_certificado_bancario = True
            self.aprueba_j_h_c_comercio_ = True
            self.aprueba_j_h_anexo_tributario_j = True
            self.aprueba_j_h_propuesta_ = True
            self.aprueba_j_h_rut_pj_ = True
            self.aprueba_j_h_rup_ = True
            self.aprueba_j_h_hjv_juridica_ = True
            self.aprueba_j_h_c_contraloriaa_ = True
            self.aprueba_j_h_cprocuraduria_ = True
            self.aprueba_j_h_cedula_r_ = True
            self.aprueba_j_h_hjv_r = True
            self.aprueba_j_h_contraloria_rep_lega = True
            self.aprueba_j_h_procuraduria_rep_legal = True
            self.aprueba_j_h_cdp_j = True
            self.aprueba_j_h_contrato = True
        else:
            self.aprueba_j_h_policia_judicial_ = False
            self.aprueba_j_h_inhabilidades_ = False
            self.aprueba_j_h_re_facturacion = False
            self.aprueba_j_h_exp_laboral_ = False
            self.aprueba_j_h_para_fiscales = False
            self.aprueba_j_h_seguridad_social_ = False
            self.aprueba_j_h_juntacontadores = False
            self.aprueba_j_h_r_fiscal = False
            self.aprueba_j_h_certificado_bancario = False
            self.aprueba_j_h_c_comercio_ = False
            self.aprueba_j_h_anexo_tributario_j = False
            self.aprueba_j_h_propuesta_ = False
            self.aprueba_j_h_rut_pj_ = False
            self.aprueba_j_h_rup_ = False
            self.aprueba_j_h_hjv_juridica_ = False
            self.aprueba_j_h_c_contraloriaa_ = False
            self.aprueba_j_h_cprocuraduria_ = False
            self.aprueba_j_h_cedula_r_ = False
            self.aprueba_j_h_hjv_r = False
            self.aprueba_j_h_contraloria_rep_lega = False
            self.aprueba_j_h_procuraduria_rep_legal = False
            self.aprueba_j_h_cdp_j = False
            self.aprueba_j_h_contrato = False

    selecionar_todos_p_rh = fields.Boolean('Seleccionar todos', tracking=True)

    @api.onchange('selecionar_todos_p_rh')
    def _onchange_selecionar_todos_p_rh(self):
        if self.selecionar_todos_p_rh:
            self.aprueba_r_arl_ = True
            self.aprueba_r_salud_ = True
            self.aprueba_r_pension_ = True

        else:
            self.aprueba_r_arl_ = False
            self.aprueba_r_salud_ = False
            self.aprueba_r_pension_ = False


    aprueba_j_h_procuraduria_rep_legal = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('cert_policia_judicial_doc')
    def _check_file_extension(self):
        for record in self:
            if record.cert_policia_judicial_doc:
                filename = record.cert_policia_judicial_doc_fname

    @api.onchange('cert_policia_judicial_doc')
    def _onchange_cert_policia_judicial_doc(self):
        if self.cert_policia_judicial_doc:
            self.cert_policia_judicial = True

    @api.onchange('cert_policia_judicial_doc')
    def _onchange_delete_cert_policia_judicial_doc(self):
        if not self.cert_policia_judicial_doc:
            self.cert_policia_judicial = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_policia_judicial = {}


    cert_policia_judicial_actualizar_visible = fields.Boolean(default=False, related='cert_policia_judicial_doc_id.actualizar_visible', store=True, readonly=False)




    @api.model
    def cargar_documento_19(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cert_policia_judicial_doc': file_content,
            'cert_policia_judicial_doc_fname': file_name,
        })

    aprueba_f_policia_judicial = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_policia_judicial_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    ############################################################################################################
    contrato_j = fields.Boolean('Contrato', tracking=True)
    aprueba_f_contrato = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_contrato = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    aprueba_j_h_contrato_doc_visible = fields.Boolean(default=True)
    aprueba_j_h_contrato_doc = fields.Binary(tracking=True)
    aprueba_j_h_contrato_doc_fname = fields.Char(tracking=True)
    aprueba_j_h_contrato_doc_id = fields.Many2one('drive', string='Aprueba J&H Contrato', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_aprueba_j_h(self):
        self.contrato_j = True  # Marcar el contrato como presente antes de la carga
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        ## 1. Obtener el ID de Google Drive real (string)
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contrato',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'aprueba_j_h_contrato_doc' 
            },
        }


    def notificacion_contratos_ju_doc(self):
        if self.usa_flujo_contratacion_nuevo:
            self._marcar_contrato_listo_para_firmas('')
            return

        _logger.info(">>> DISPARANDO NOTIFICACIÓN JU: Se subió un contrato (ID: %s)", self.id)
        
        # 1. Verificar que el proyecto relacionado exista
        if not self.proyecto_relacionado:
            _logger.warning(">>> No se envió correo: Falta proyecto relacionado.")
            raise UserError("No se ha definido un proyecto relacionado.")

        # 2. Configurar URLs
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        record_id = self._origin.id if self._origin else self.id
        record_url = f"{base_url}/web#id={record_id}&view_type=form&model={self._name}"

        # 3. Validar destinatarios
        emails = []
        
        # Correo del creador, si existe.
        if self.proyecto_relacionado.create_uid.email:
            emails.append(self.proyecto_relacionado.create_uid.email)

        # Respaldo: coordinador responsable del proyecto.
        responsable = self.proyecto_relacionado.asignar_coordinador
        if responsable and responsable.email and responsable.email not in emails:
            emails.append(responsable.email)

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
        subject = f"El contrato ha sido subido en {self.new_name or self.name}"
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📋 Contrato subido al sistema</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Estimados,</p>

                    <p>Se ha subido el contrato al sistema, por favor, hacer firmar:</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <p style="margin: 4px 0;"><strong>Nombre del contrato:</strong> {self.new_name or self.name}</p>
                        <p style="margin: 4px 0;"><strong>Proyecto:</strong> {self.proyecto_relacionado.new_name}</p>
                    </div>

                    <p style="text-align: center; margin-top: 30px;">
                        <a href="{record_url}"
                           style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                            ➡ Ver contrato
                        </a>
                    </p>

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático del Repositorio, por favor no responder.
                    </p>
                </div>
            </div>
        """

        # 5. Definir valores del correo
        mail_values = {
            'subject': subject,
            'body_html': body,
            'email_to': email_to,
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
        }

        # 6. Enviar
        _logger.info(">>> Creando correo para: %s", email_to)
        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()
        _logger.info(">>> ¡NOTIFICACIÓN ENVIADA EXITOSAMENTE!")
        self._marcar_contrato_listo_para_firmas(email_to)


    @api.constrains('aprueba_j_h_contrato_doc')
    def _check_file_extension33(self):
        for record in self:
            if record.aprueba_j_h_contrato_doc:
                filename = record.aprueba_j_h_contrato_doc_fname

    @api.onchange('aprueba_j_h_contrato_doc')
    def _onchange_aprueba_j_h_contrato_doc(self):
        if self.aprueba_j_h_contrato_doc:
            self.contrato_j = True

    @api.onchange('aprueba_j_h_contrato_doc')
    def _onchange_delete_aprueba_j_h_contrato_doc(self):
        if not self.aprueba_j_h_contrato_doc:
            self.contrato_j = False

    @api.model
    def cargar_documento_60(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'aprueba_j_h_contrato_doc': file_content,
            'aprueba_j_h_contrato_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_contrato_j = {}


    contrato_j_actualizar_visible = fields.Boolean(default=False, related='aprueba_j_h_contrato_doc_id.actualizar_visible', store=True, readonly=False)


    ####################################BDD######################################################


    bdd_j = fields.Boolean('BDD ', tracking=True)
    aprueba_f_bdd = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_bdd = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    aprueba_j_h_bdd_doc_visible = fields.Boolean(default=True)
    aprueba_j_h_bdd_doc = fields.Binary(tracking=True)
    aprueba_j_h_bdd_doc_fname = fields.Char(tracking=True)
    aprueba_j_h_bdd_doc_id = fields.Many2one('drive', string='Aprueba J&H BDD', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_aprueba_j_h_bdd(self):
        self.bdd_j = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir BDD',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'aprueba_j_h_bdd' 
            },
        }


    @api.constrains('aprueba_j_h_bdd_doc')
    def _check_file_extension130(self):
        for record in self:
            if record.aprueba_j_h_bdd_doc:
                filename = record.aprueba_j_h_bdd_doc_fname

    @api.onchange('aprueba_j_h_bdd_doc')
    def _onchange_aprueba_j_h_bdd_doc(self):
        if self.aprueba_j_h_bdd_doc:
            self.bdd_j = True

    @api.onchange('aprueba_j_h_bdd_doc')
    def _onchange_delete_aprueba_j_h_contrato_doc(self):
        if not self.aprueba_j_h_bdd_doc:
            self.bdd_j = False

    @api.model
    def cargar_documento_160(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'aprueba_j_h_bdd_doc': file_content,
            'aprueba_j_h_bdd_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_bdd_j = {}


    bdd_j_actualizar_visible = fields.Boolean(default=False, related='aprueba_j_h_bdd_doc_id.actualizar_visible', store=True, readonly=False)


    ##########################################################################################



    contrato_firmado_j = fields.Boolean('Contrato', tracking=True)
    aprueba_f_contrato_firmado = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_contrato_firmado = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    aprueba_j_h_contrato_firmado_doc_visible = fields.Boolean(default=True)
    aprueba_j_h_contrato_firmado_doc = fields.Binary(tracking=True)
    aprueba_j_h_contrato_firmado_doc_fname = fields.Char(tracking=True)
    aprueba_j_h_contrato_firmado_doc_id = fields.Many2one('drive', string='Aprueba J&H Contrato Firmado', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_aprueba_j_h_firmado(self):
        self.contrato_firmado_j = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contrato firmado',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'aprueba_j_h_firmado' 
            },
        }


    def notificacion_contrato_juridico_firmado(self):
        _logger.info(">>> Iniciando envío de notificación de contrato jurídico firmado (ID: %s)", self.id)

        # Preparar enlace directo de Google Drive para el correo.
        # No se descarga ni se adjunta copia local: la copia vive en Google Drive.
        attachment_ids = []
        drive_link = ""
        file_id = False
        if self.aprueba_j_h_contrato_firmado_doc_id:
            drive_record = self.aprueba_j_h_contrato_firmado_doc_id
            file_id = drive_record.file_id or drive_record.drive_file_id or False
            try:
                target_id = file_id
                if target_id:
                    drive_link = f"https://drive.google.com/file/d/{target_id}/view"
                    attachment_id = self._crear_adjunto_temporal_drive_para_correo(
                        drive_record,
                        nombre_default="Contrato firmado.pdf",
                    )
                    if attachment_id:
                        attachment_ids.append(attachment_id)
                    _logger.info(">>> Enlace de Drive preparado para correo: %s", drive_link)
                else:
                    _logger.warning(">>> No se encontró file_id en el registro de Drive (ID: %s)", drive_record.id)
            except Exception as e:
                _logger.warning(">>> No se pudo preparar el enlace del documento en Drive: %s", str(e))

        destinatarios = []

        if self.contratista_proveedor_relacionado and self.contratista_proveedor_relacionado.correo_facturacion:
            destinatarios.append(self.contratista_proveedor_relacionado.correo_facturacion)
            _logger.info(">>> Destinatario (Contratista): %s", self.contratista_proveedor_relacionado.correo_facturacion)


        COLOR_PRINCIPAL = "#043484"
        subject = f"Copia del contrato firmado: {self.new_name or self.name}"
        proyecto_nombre = self.proyecto_relacionado.new_name if self.proyecto_relacionado else 'N/A'
        adjunto_info = (
            "<p>Encontrarán la copia del contrato firmado adjunta a este correo.</p>"
            if attachment_ids
            else
            f'<p>El contrato firmado está disponible en Google Drive: '
            f'<a href="{drive_link}" style="color:{COLOR_PRINCIPAL}; font-weight:bold;">Ver documento</a></p>'
            if drive_link
            else "<p>El contrato firmado para este proyecto ya está disponible en el sistema.</p>"
        )
        body = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📄 Copia del contrato firmado disponible</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Cordial saludo,</p>

                    <p>El contrato firmado para el siguiente proyecto ya está disponible:</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <strong>Proyecto:</strong> {proyecto_nombre}
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
            _logger.info(">>> Enviando notificación de contrato jurídico firmado a %s destinatarios...", len(destinatarios))
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail.sudo().send()
            if attachment_ids:
                self.env["ir.attachment"].sudo().browse(attachment_ids).unlink()
            _logger.info(">>> ✅ NOTIFICACIÓN ENVIADA CON ÉXITO")
        except Exception as e:
            _logger.error(">>> ❌ ERROR enviando notificación jurídica firmada: %s", str(e))

    @api.constrains('aprueba_j_h_contrato_firmado_doc')
    def _check_file_extension102(self):
        for record in self:
            if record.aprueba_j_h_contrato_firmado_doc:
                filename = record.aprueba_j_h_contrato_firmado_doc_fname

    @api.onchange('aprueba_j_h_contrato_firmado_doc')
    def _onchange_aprueba_j_h_contrato_firmado_doc(self):
        if self.aprueba_j_h_contrato_firmado_doc:
            self.contrato_firmado_j = True

    @api.onchange('aprueba_j_h_contrato_firmado_doc')
    def _onchange_delete_aprueba_j_h_contrato_firmado_doc(self):
        if not self.aprueba_j_h_contrato_firmado_doc:
            self.contrato_firmado_j = False

    @api.model
    def cargar_documento_120(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'aprueba_j_h_contrato_firmado_doc': file_content,
            'aprueba_j_h_contrato_firmado_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_contrato_firmado_j = {}


    contrato_firmado_j_actualizar_visible = fields.Boolean(default=False, related='aprueba_j_h_contrato_firmado_doc_id.actualizar_visible', store=True, readonly=False)


    ##########################################################################################



    @api.constrains('cert_inhabilidades_doc')
    def _check_file_extension34(self):
        for record in self:
            if record.cert_inhabilidades_doc:
                filename = record.cert_inhabilidades_doc_fname

    @api.onchange('cert_inhabilidades_doc')
    def _onchange_cert_inhabilidades_doc(self):
        if self.cert_inhabilidades_doc:
            self.cert_inhabilidades = True

    @api.onchange('cert_inhabilidades_doc')
    def _onchange_delete_cert_inhabilidades_doc(self):
        if not self.cert_inhabilidades_doc:
            self.cert_inhabilidades = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cert_inhabilidades = {}


    cert_inhabilidades_actualizar_visible = fields.Boolean(default=False, related='cert_inhabilidades_doc_id.actualizar_visible', store=True, readonly=False)




    @api.model
    def cargar_documento_20(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cert_inhabilidades_doc': file_content,
            'cert_inhabilidades_doc_fname': file_name,
        })

    aprueba_f_inhabilidades = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_inhabilidades_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('resolucion_facturacion_doc')
    def _check_file_extension35(self):
        for record in self:
            if record.resolucion_facturacion_doc:
                filename = record.resolucion_facturacion_doc_fname

    @api.onchange('resolucion_facturacion_doc')
    def _onchange_resolucion_facturacion_doc(self):
        if self.resolucion_facturacion_doc:
            self.resolucion_facturacion = True

    @api.onchange('resolucion_facturacion_doc')
    def _onchange_delete_resolucion_facturacion_doc(self):
        if not self.resolucion_facturacion_doc:
            self.resolucion_facturacion = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_resolucion_facturacion = {}



    resolucion_facturacion_actualizar_visible = fields.Boolean('Actualizar visible', tracking=True, related='resolucion_facturacion_doc_id.actualizar_visible', default=False, store=True, readonly=False)


    @api.model
    def cargar_documento_21(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'resolucion_facturacion_doc': file_content,
            'resolucion_facturacion_doc_fname': file_name,
        })

    aprueba_f_resolucion_facturacion = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_re_facturacion = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    ######################################################################################################
    @api.constrains('resolucion_facturacion_ju_doc')
    def _check_file_extension35(self):
        for record in self:
            if record.resolucion_facturacion_ju_doc:
                filename = record.resolucion_facturacion_ju_doc_fname

    @api.onchange('resolucion_facturacion_ju_doc')
    def _onchange_resolucion_facturacion_ju_doc(self):
        if self.resolucion_facturacion_ju_doc:
            self.resolucion_facturacion_ju = True

    @api.onchange('resolucion_facturacion_ju_doc')
    def _onchange_delete_resolucion_facturacion_ju_doc(self):
        if not self.resolucion_facturacion_ju_doc:
            self.resolucion_facturacion_ju = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_resolucion_facturacion_ju = {}


    resolucion_facturacion_ju_actualizar_visible = fields.Boolean('Actualizar visible', tracking=True, default=False, related='resolucion_facturacion_ju_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_21(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'resolucion_facturacion_ju_doc': file_content,
            'resolucion_facturacion_ju_doc_fname': file_name,
        })

    aprueba_f_resolucion_facturacion_ju = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_re_facturacion_ju = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    ####################################################################################################


    bdd_ju = fields.Boolean('Resolución de facturación electrónica', tracking=True)
    bdd_ju_doc = fields.Binary(tracking=True)

    @api.constrains('bdd_ju_doc')
    def _check_file_extension35(self):
        for record in self:
            if record.bdd_ju_doc:
                filename = record.bdd_ju_doc_fname

    @api.onchange('bdd_ju_doc')
    def _onchange_bdd_ju_doc(self):
        if self.bdd_ju_doc:
            self.bdd_ju = True

    @api.onchange('bdd_ju_doc')
    def _onchange_delete_bdd_ju_doc(self):
        if not self.bdd_ju_doc:
            self.bdd_ju = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_bdd_ju = {}


    bdd_ju_actualizar_visible = fields.Boolean('Actualizar visible', tracking=True, default=False, related='bdd_ju_id.actualizar_visible', store=True, readonly=False)


    bdd_ju_doc_fname = fields.Char(tracking=True)
    bdd_ju_doc_visible = fields.Boolean(default=True)
    bdd_ju_id = fields.Many2one('drive', string='BDD', readonly=True, tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
        
    def action_upload_bdd_ju(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # Obtenemos el ID de Google Drive real (string)
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir BDD',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # ID del registro de carpeta de Odoo
                'default_stage2_id': self.drive_file_id.id, 
                
                # ID de Google Drive
                'default_folder_id': google_folder_id, 
                
                # Señal para el mapeo
                'tipo_documento_upload': 'bdd_ju' 
            },
        }

    def subir_documento_bdd_ju_a_drive(self):
        """Sube el documento de BDD a Google Drive y lo registra en el modelo drive."""
        if self.bdd_ju_doc and self.bdd_ju_doc_fname:
            _logger.info(f"Subiendo documento '{self.bdd_ju_doc_fname}' a Google Drive...")
            self._upload_document_to_drive_bdd_ju_pn()
        else:
            _logger.warning("No se encontró el documento de BDD para subir.")

    def _upload_document_to_drive_bdd_ju_pn(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Validar que self.drive_file_id es un objeto Many2one y no un string
        if isinstance(self.drive_file_id, str):
            _logger.error(f"El campo drive_file_id tiene un valor de tipo string: {self.drive_file_id}")
            return False

        # Buscar el registro en drive.file basado en el ID del archivo
        drive_file_record = self.env['drive.file'].search([('id', '=', self.drive_file_id.id)], limit=1)

        if not drive_file_record:
            _logger.error(f"No se encontró un registro en drive.file con el ID {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        carpeta_drive = drive_file_record.folder_id2  # Usamos 'folder_id2' aquí

        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.bdd_ju_doc)

            original_extension = self.bdd_ju_doc_fname.split('.')[-1]
            fixed_file_name = f"12_BDD_{uuid.uuid4()}.{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{original_extension}") as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {fixed_file_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google Drive dentro de la carpeta correcta
            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',  # Tipo genérico para binarios
                'parents': [{'id': carpeta_drive}]  # Aseguramos que se suba a la carpeta correcta
            }
            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)  # Subir el archivo correctamente
            file_drive.Upload()

            file_id = file_drive['id']
            file_link = f"https://drive.google.com/file/d/{file_id}/view"

            _logger.info(f"Archivo subido correctamente a Google Drive con ID: {file_id} y URL: {file_link}")

            # Crear el registro en el modelo 'drive'
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.bdd_ju_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Actualizar el campo bdd_ju_id si está vacío
            if not self.bdd_ju_id:
                self.bdd_ju_id = drive_record.id

            _logger.info(f"Campo bdd_ju_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Ocultar el campo después de subir el archivo
            self.bdd_ju_doc_visible = False
            self.bdd_ju_doc = False
            _logger.info("Campo bdd_ju_doc_visible actualizado a False.")

            # Asegurar que solo un registro esté siendo guardado
            self.ensure_one()

            # Eliminar el archivo temporal después de la subida
            os.unlink(temp_file_path)

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True


    @api.model
    def cargar_documento_126(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'bdd_ju_doc': file_content,
            'bdd_ju_doc_fname': file_name,
        })

    aprueba_f_bdd_ju = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_re_bdd_ju = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

####################################################################################################



    @api.constrains('experiencia_laboral_doc')
    def _check_file_extension36(self):
        for record in self:
            if record.experiencia_laboral_doc:
                filename = record.experiencia_laboral_doc_fname

    @api.onchange('experiencia_laboral_doc')
    def _onchange_experiencia_laboral_doc(self):
        if self.experiencia_laboral_doc:
            self.experiencia_laboral = True

    @api.onchange('experiencia_laboral_doc')
    def _onchange_delete_experiencia_laboral_doc(self):
        if not self.experiencia_laboral_doc:
            self.experiencia_laboral = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_experiencia_laboral = {}


    experiencia_laboral_actualizar_visible = fields.Boolean(default=False, related='experiencia_laboral_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_22(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'experiencia_laboral_doc': file_content,
            'experiencia_laboral_doc_fname': file_name,
        })

    aprueba_f_experiencia_laboral = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_exp_laboral_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('certificado_pago_parafiscales_doc')
    def _check_file_extension37(self):
        for record in self:
            if record.certificado_pago_parafiscales_doc:
                filename = record.certificado_pago_parafiscales_doc_fname

    @api.onchange('certificado_pago_parafiscales_doc')
    def _onchange_certificado_pago_parafiscales_doc(self):
        if self.certificado_pago_parafiscales_doc:
            self.certificado_pago_parafiscales = True

    @api.onchange('certificado_pago_parafiscales_doc')
    def _onchange_delete_certificado_pago_parafiscales_doc(self):
        if not self.certificado_pago_parafiscales_doc:
            self.certificado_pago_parafiscales = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_pago_parafiscales = {}


    certificado_pago_parafiscales_actualizar_visible = fields.Boolean(default=False, related='certificado_pago_parafiscales_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_23(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificado_pago_parafiscales_doc': file_content,
            'certificado_pago_parafiscales_doc_fname': file_name,
        })

    aprueba_f_pago_parafiscales = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_para_fiscales = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('planilla_seguridad_social_doc')
    def _check_file_extension38(self):
        for record in self:
            if record.planilla_seguridad_social_doc:
                filename = record.planilla_seguridad_social_doc_fname

    @api.onchange('planilla_seguridad_social_doc')
    def _onchange_planilla_seguridad_social_doc(self):
        if self.planilla_seguridad_social_doc:
            self.planilla_seguridad_social = True

    @api.onchange('planilla_seguridad_social_doc')
    def _onchange_delete_planilla_seguridad_social_doc(self):
        if not self.planilla_seguridad_social_doc:
            self.planilla_seguridad_social = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_planilla_seguridad_social = {}


    planilla_seguridad_social_actualizar_visible = fields.Boolean(default=False, related='planilla_seguridad_social_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_24(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'planilla_seguridad_social_doc': file_content,
            'planilla_seguridad_social_doc_fname': file_name,
        })

    aprueba_f_seguridad_social = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_seguridad_social_ = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('certificado_junta_contadores_doc')
    def _check_file_extension39(self):
        for record in self:
            if record.certificado_junta_contadores_doc:
                filename = record.certificado_junta_contadores_doc_fname

    @api.onchange('certificado_junta_contadores_doc')
    def _onchange_certificado_junta_contadores_doc(self):
        if self.certificado_junta_contadores_doc:
            self.certificado_junta_contadores = True

    @api.onchange('certificado_junta_contadores_doc')
    def _onchange_delete_certificado_junta_contadores_doc(self):
        if not self.certificado_junta_contadores_doc:
            self.certificado_junta_contadores = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_junta_contadores = {}


    certificado_junta_contadores_actualizar_visible = fields.Boolean(default=False, related='certificado_junta_contadores_doc_id.actualizar_visible', store=True, readonly=False)



    @api.model
    def cargar_documento_25(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificado_junta_contadores_doc': file_content,
            'certificado_junta_contadores_doc_fname': file_name,
        })

    aprueba_f_junta_contadores = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_juntacontadores = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('cedula_revisor_fiscal_doc')
    def _check_file_extension40(self):
        for record in self:
            if record.cedula_revisor_fiscal_doc:
                filename = record.cedula_revisor_fiscal_doc_fname

    @api.onchange('cedula_revisor_fiscal_doc')
    def _onchange_cedula_revisor_fiscal_doc(self):
        if self.cedula_revisor_fiscal_doc:
            self.cedula_revisor_fiscal = True

    @api.onchange('cedula_revisor_fiscal_doc')
    def _onchange_delete_cedula_revisor_fiscal_doc(self):
        if not self.cedula_revisor_fiscal_doc:
            self.cedula_revisor_fiscal = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cedula_revisor_fiscal = {}



    cedula_revisor_fiscal_actualizar_visible = fields.Boolean(default=False, related='cedula_revisor_fiscal_doc_id.actualizar_visible', store=True, readonly=False)


    @api.model
    def cargar_documento_26(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cedula_revisor_fiscal_doc': file_content,
            'cedula_revisor_fiscal_doc_fname': file_name,
        })

    aprueba_f_revisor_fiscal = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_r_fiscal = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('certificado_bancario_doc')
    def _check_file_extension41(self):
        for record in self:
            if record.certificado_bancario_doc:
                filename = record.certificado_bancario_doc_fname

    @api.onchange('certificado_bancario_doc')
    def _onchange_certificado_bancario_doc(self):
        if self.certificado_bancario_doc:
            self.certificado_bancario = True

    @api.onchange('certificado_bancario_doc')
    def _onchange_delete_certificado_bancario_doc(self):
        if not self.certificado_bancario_doc:
            self.certificado_bancario = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_bancario = {}


    certificado_bancario_actualizar_visible = fields.Boolean(default=False, related='certificado_bancario_doc_id.actualizar_visible', store=True, readonly=False)


    @api.model
    def cargar_documento_27(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'certificado_bancario_doc': file_content,
            'certificado_bancario_doc_fname': file_name,
        })

    aprueba_f_c_bancario = fields.Boolean('Aprueba-F', tracking=True)
    aprueba_j_h_certificado_bancario = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    anexo_tributario_juridico = fields.Boolean('Anexo tributario', tracking=True)
    anexo_tributario_juridico_doc = fields.Binary(tracking=True)

    @api.constrains('anexo_tributario_juridico_doc')
    def _check_file_extension42(self):
        for record in self:
            if record.anexo_tributario_juridico_doc:
                filename = record.anexo_tributario_juridico_doc_fname

    @api.onchange('anexo_tributario_juridico_doc')
    def _onchange_anexo_tributario_juridico_doc(self):
        if self.anexo_tributario_juridico_doc:
            self.anexo_tributario_juridico = True

    @api.onchange('anexo_tributario_juridico_doc')
    def _onchange_delete_anexo_tributario_juridico_doc(self):
        if not self.anexo_tributario_juridico_doc:
            self.anexo_tributario_juridico = False

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_anexo_tributario_juridico = {}


    anexo_tributario_juridico_actualizar_visible = fields.Boolean(default=False, related='anexo_tributario_juridico_doc_id.actualizar_visible', store=True, readonly=False)


    anexo_tributario_juridico_doc_fname = fields.Char(tracking=True)
    anexo_tributario_juridico_doc_visible = fields.Boolean(default=True)
    anexo_tributario_juridico_doc_id = fields.Many2one('drive', string='Anexo Tributario Jurídico', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    
    def action_upload_anexo_tributario_juridico(self):
        self.ensure_one()
        contexto_at_posterior = {}
        if self.usa_flujo_contratacion_nuevo:
            self._validar_gestion_at_posterior_presupuesto()
            contexto_at_posterior = {
                'gestion_at_posterior_presupuesto': True,
            }
            _logger.info(
                '[AT-PRESUPUESTO-FLUJO-NUEVO] Apertura de carga del AT PJ. '
                'fc_id=%s estado=%s usuario_id=%s usuario=%s',
                self.id, self.estado_contratacion,
                self.env.user.id, self.env.user.name,
            )
            self.with_context(**contexto_at_posterior).write({
                'anexo_tributario_juridico': True,
            })
        else:
            self.anexo_tributario_juridico = True
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir anexo tributario',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'anexo_tributario_juridico',
                **contexto_at_posterior,
            },
        }

    @api.model
    def cargar_documento_29(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'anexo_tributario_juridico_doc': file_content,
            'anexo_tributario_juridico_doc_fname': file_name,
        })

    aprueba_f_tributario_juridico = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_anexo_tributario_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    ###################################################################################################
    cdp_juridico = fields.Boolean('RPC', tracking=True)
    aprueba_f_cdp_juridico = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_cdp_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)

    cdp_juridico_doc_visible = fields.Boolean(default=True)
    cdp_doc_fname = fields.Char(tracking=True)
    cdp_juridico_doc = fields.Binary(tracking=True)
    cdp_juridico_doc_id = fields.Many2one('drive', string='RPC  ', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_cdp_juridico(self):
        self.cdp_juridico = True    
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratacion_fuv_documentos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cdp_juridico' 
            },
        }



    @api.constrains('cdp_juridico_doc')
    def _check_file_extension43(self):
        for record in self:
            if record.cdp_juridico_doc:
                filename = record.cdp_doc_fname

    @api.onchange('cdp_juridico_doc')
    def _onchange_cdp_juridico_doc(self):
        if self.cdp_juridico_doc:
            self.cdp_juridico = True

    @api.onchange('cdp_juridico_doc')
    def _onchange_delete_cdp_juridico_doc(self):
        if not self.cdp_juridico_doc:
            self.cdp_juridico = False

    @api.model
    def cargar_documento_61(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'cdp_juridico_doc': file_content,
            'cdp_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cdp_juridico = {}


    cdp_juridico_actualizar_visible = fields.Boolean('Actualizar visible', default=False, related='cdp_juridico_doc_id.actualizar_visible', store=True, readonly=False)


    ###################################################################################################


    ###################################################################################################
    estados_financieros_juridico = fields.Boolean('Estados financieros', tracking=True)
    aprueba_f_estados_financieros_juridico = fields.Boolean('Aprueba-F', tracking=True, compute='_compute_true')
    aprueba_j_h_estados_financieros_j = fields.Boolean('Aprueba - Aprueba área jurídica', tracking=True)



    @api.constrains('estados_financieros_juridico_doc')
    def _check_file_extension143(self):
        for record in self:
            if record.estados_financieros_juridico_doc:
                filename = record.estados_financieros_doc_fname

    @api.onchange('estados_financieros_juridico_doc')
    def _onchange_estados_financieros_juridico_doc(self):
        if self.estados_financieros_juridico_doc:
            self.estados_financieros_juridico = True

    @api.onchange('estados_financieros_juridico_doc')
    def _onchange_delete_estados_financieros_juridico_doc(self):
        if not self.estados_financieros_juridico_doc:
            self.estados_financieros_juridico = False

    @api.model
    def cargar_documento_161(self, record_id, file_content, file_name):
        # Encuentra el registro
        record = self.browse(record_id)

        # Actualiza el campo Binary con el contenido del archivo y el nombre
        record.write({
            'estados_financieros_juridico_doc': file_content,
            'estados_financieros_doc_fname': file_name,
        })

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_estados_financieros_juridico = {}


    estados_financieros_juridico_actualizar_visible = fields.Boolean('Actualizar visible', default=False, related='estados_financieros_juridico_doc_id.actualizar_visible', store=True, readonly=False)


    ###################################################################################################

    # Fin de campos booleanos
    no_disponibilidad_presupuestal = fields.Char('N° Disponibilidad presupuestal', tracking=True)
    fecha_cdp_d = fields.Date('Fecha disponibilidad presupuestal', tracking=True)
    proyecto_convenio = fields.Text('Proyecto o convenio', tracking=True)
    observaciones = fields.Text('Observaciones', tracking=True)
    observaciones_finacie = fields.Text('Observaciones', tracking=True)
    observaciones_juri = fields.Text('Observaciones', tracking=True)
    observaciones_recursos = fields.Text('Observaciones', tracking=True)

    aprobacion_financiera_realizada = fields.Boolean(string='Aprobación de financiera Realizada', default=False)

    # Aprueba financiera a persona natural


    ###############################################################################################################
    #Aprueba financiera a persona juidica


    ##############################################################################################################

    aprobacion_juridico_realizada = fields.Boolean(string='Aprobación de juridico Realizada', default=False)


    def action_set_contratado(self):
        """ Cambia el estado de contratación a 'contratado' """
        self.ensure_one()  # Asegura que solo hay un registro seleccionado
        self.estado_contratacion = 'contratado'
        self.no_editable = True
        _logger.info(f"El estado de contratación se ha cambiado a 'contratado' para el registro ID {self.id}.")
    #contratado Persona natural

    def contratado2(self):
        self.notificar_contratado_fc()
        self.write({'estado_contratacion': 'contratado'})
        self.no_editable = True
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':

            # Marcar la acción como realizada solo si el estado es "coordinador"
            if self.estado_contratacion == 'juridico':
                self.write({'aprobacion_juridico_realizada': True})
            return


    #contratado Persona Juridica

    def contratado(self):
        self.notificar_contratado_fc()
        self.write({'estado_contratacion': 'contratado'})
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':

            # Marcar la acción como realizada solo si el estado es "coordinador"
            if self.estado_contratacion == 'juridico':
                self.write({'aprobacion_juridico_realizada': True})

            return


    contratado_fin = fields.Boolean('Contratado', tracking=True, default=False)
    contratado_fin_j = fields.Boolean('Contratado', tracking=True, default=False)

    def notificar_contratado_fc(self):
        print('notificar_contratado_fc')
        self.contratado_fin = True
        self.contratado_fin_j = True

        # Verificar que el proyecto tenga un creador
        if not self.proyecto_relacionado or not self.proyecto_relacionado.create_uid:
            raise UserError("El proyecto relacionado no tiene un creador asignado.")

        # Obtener el director jurídico relacionado (si existe)
        director_juridico = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id
        )

        # Construir lista de destinatarios (creador + director jurídico)
        destinatarios = [self.proyecto_relacionado.create_uid.email]
        if director_juridico:
            destinatarios.append(director_juridico[0].email)  # Solo tomamos el primer director jurídico encontrado

        # Filtrar destinatarios vacíos
        destinatarios = [email for email in destinatarios if email]

        if not destinatarios:
            raise UserError("No hay destinatarios válidos para la notificación.")

        # Mostrar en consola a quién se enviará el correo
        print(f"Correos enviados a: {', '.join(destinatarios)}")

        # Obtener el nombre del proyecto
        nombres_proyecto = filter(None, [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name])
        nombre_proyecto = ', '.join(nombres_proyecto)

        # Construir la URL del formulario
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=contrataciones_fuv'

        # Construir mensaje
        mensaje_base = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #2c3e50;">Proyecto: {nombre_proyecto}</h2>
            <p>El formulario de contratación para {self.new_name} ha alcanzado su estado final: <strong>Contratado</strong>.</p>
            <div style="margin-top: 20px; text-align: center;">
                <a href="{formulario_url}" style="
                    background-color: #007bff;
                    color: white;
                    padding: 10px 20px;
                    text-decoration: none;
                    border-radius: 5px;
                    display: inline-block;">
                    Ver formulario
                </a>
            </div>
        </div>
        """

        # Enviar correo a los destinatarios
        mail_values = {
            'subject': f'Contratista {self.new_name} del proyecto {nombre_proyecto} ha sido contratado',
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
            'email_to': ','.join(destinatarios),  # Enviar a todos los destinatarios
            'body_html': mensaje_base,
            'auto_delete': True,
        }

        mail_id = self.env['mail.mail'].sudo().create(mail_values)
        mail_id.send()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Notificación enviada',
                'message': 'Se ha notificado al creador del proyecto y al director jurídico.',
                'type': 'success',
                'sticky': False,
                'message_duration': 7,
            }
        }

    generar_v = fields.Boolean(string='Visible para Generar Informe', default=False)
    generar_vi = fields.Boolean(string='Visible para Generar Informe', default=False)

    ###################################################################################################################

    aprobacion_recursos_humanos_realizada = fields.Boolean(string='Aprobación de recursos_humanos Realizada',
                                                           default=False)
    generar_informe_visible_j = fields.Boolean(string='Visible para Generar Informe', default=False)


    #Jurídico no aprueba PN


    #Jurídico no aprueba PJ


    ########################################################################################################


    #################################################################################################################


    ##################################################################################

    departamentos_y_municipios_ids = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id',
                                                     string='Tabla')


    tarifa_bd = fields.Char(related="departamentos_y_municipios_ids.tarifa_bd", store=True, string='Tarifa')



    #################################################################################################################
       #Nuevo Teylor 4/12/2025
        # campos para acta
    acta_liquidacion = fields.Binary("Acta de liquidación")
    acta_liquidacion_nombre = fields.Char("Nombre del acta")
    archivo_acta_id = fields.Many2one('drive', string='Archivo del Acta')
    archivo_acta_visible = fields.Boolean(string="Archivo Acta Visible", default=True)
    tipo_acta_liquidacion = fields.Selection([
        ('no', 'No requiere acta'),
        ('normal', 'Acta de liquidación de mutuo acuerdo'),
        ('incumplimiento', 'Acta de liquidación unilateral'),
        ('anulacion', 'Acta de anulación'),
    ], string="Tipo de acta")

    _cache_documentos_archivo_acta = {}




    # botón que invoca el XML
    def action_close_contract(self):
        self.ensure_one()
        self.is_editable = True
        try:
            # solo postea en chatter / hace log para abrir wizard (no envía correo aún)
            self.notify_coordinator(send_email=False)
        except Exception as e:
            _logger.exception("Error notificando coordinador al abrir wizard: %s", e)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Cerrar contrato',
            'res_model': 'contrataciones_fuv.close.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contract_id': self.id},
        }

    def notify_coordinator(self, send_email=True):
        self.ensure_one()

        # Validaciones
        project = self.proyecto_relacionado
        if not project:
            raise UserError("No hay proyecto asociado a este contrato.")

        coordinator = project.create_uid
        if not coordinator or not coordinator.email:
            raise UserError("El coordinador asociado no tiene correo configurado.")

        # Datos
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        record_url = f"{base_url}/web#id={self.id}&view_type=form&model={self._name}"

        COLOR_PRINCIPAL = "#043484"  # Azul corporativo
        contrato_nombre = self.name

        # ---------------------
        # Solo chatter
        # ---------------------
        if not send_email:
            body = f"""
                <p>Cordial saludo {coordinator.name},</p>
                <p>Se ha iniciado el proceso de cierre del contrato <b>{contrato_nombre}</b>.</p>
                <p><a href="{record_url}" target="_blank">Ir al contrato</a></p>
            """
            self.message_post(body=body)
            return True

        # ---------------------
        # Plantilla HTML completa
        # ---------------------
        body_html = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📄 Inicio de proceso de cierre</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                    
                    <p>Hola, <strong>{coordinator.name}</strong>:</p>

                    <p>Se ha iniciado el proceso de cierre del contrato:</p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <strong>Contrato:</strong> {contrato_nombre}<br/>
                        <strong>Proyecto asociado:</strong> {project.display_name}
                    </div>

                    <p style="text-align: center; margin-top: 30px;">
                        <a href='{record_url}' 
                            style="background-color: {COLOR_PRINCIPAL}; 
                            color: white; 
                            padding: 10px 20px; 
                            text-decoration: none; 
                            border-radius: 5px; 
                            font-weight: bold; 
                            display: inline-block;">
                            ➡️ Ir al contrato
                        </a>
                    </p>

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático de SGO.
                    </p>
                </div>
            </div>
        """

        # Enviar correo
        mail_values = {
            'subject': f"Proceso de cierre iniciado - Contrato {contrato_nombre}",
            'email_from': 'go@fundacionunivalle.org',
            'email_to': coordinator.email,
            'body_html': body_html,
            'auto_delete': True,
        }

        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()

        self.message_post(body=f"Correo enviado a {coordinator.email}.")
        return True
#####################################################################################################################








#####################################################################################################################


class FormularioCDP(models.Model):
    _name = 'formulario_cdp'
    _description = 'Formulario CDP'
    proyecto_relacionado_cdp = fields.Many2one('contrataciones_fuv', string='Selecionar contratista')

    lineas_formulario_cdp = fields.One2many('formulario_cdp_linea', 'formulario_cdp_id', string='Líneas')

    ####################################################################################################################
    def guardar_documento_adjunto(self, base64_documento):
        # Decodificar la cadena Base64 a datos binarios
        documento_binario = base64.b64decode(base64_documento)

        # Guardar el documento como un adjunto en el registro actual
        self.write({'informe_adjunto': base64_documento})

        return True

    def generar_datos_tabla_cdp(self):
        # Obtener los datos existentes de la tabla
        datos_existente = self.lineas_formulario_cdp

        # Si hay datos existentes, simplemente devolverlos
        if datos_existente:
            return datos_existente
        else:
            # Si no hay datos existentes, generar una lista vacía
            return []

    def render_informe_template_cdp(self):
        # Obtener la ruta del directorio actual
        current_directory = get_module_resource('Matriz', 'static/src/docx')

        # Cargar la plantilla desde el archivo .docx utilizando python-docx-template
        template_path = os.path.join(current_directory, "plantilla_cdp.docx")
        template = DocxTemplate(template_path)

        # Obtener el contexto de Odoo
        context = {'docs': self}

        # Renderizar la plantilla con los datos actuales del registro
        context['datos_tabla_cdp'] = self.generar_datos_tabla_cdp()  # Agregar los datos de la tabla al contexto
        template.render(context)

        # Guardar el resultado en un nuevo archivo .docx
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_plantilla_rendered_cdp.docx")
        template.save(output_path)

        return output_path

    def generar_informe_docx(self):
        record_id = self.id  # ID del registro en contexto

        template_rendered = self.render_informe_template_cdp()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        # --- LIMPIEZA SIMPLE ---
        # Usamos self.name que es el campo que traía originalmente este método
        nombre_sucio = self.name or 'Informe_formulario_cdp'
        nombre_limpio = nombre_sucio.replace('&', 'y').strip()

        return {
            'name': '{}.docx'.format(nombre_limpio),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=formulario_cdp&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, nombre_limpio),
            'target': 'self',
        }

    informe_adjunto = fields.Binary(string='Adjunto del informe', readonly=True, tracking=True)

    ##########################################################################################################################

    def default_get(self, fields):
        # Obtener los datos existentes de la tabla
        datos_existente = self.lineas_formulario_cdp

        # Si ya existen datos en la tabla, no hacer nada
        if datos_existente:
            return super(FormularioCDP, self).default_get(fields)
        else:
            # Si no hay datos existentes, agregar las líneas predeterminadas
            res = super(FormularioCDP, self).default_get(fields)

            # Descripciones para la columna 'detalles'
            descripciones = ['Inicial', 'Otrosi1', 'Otrosi2', 'Otrosi3', 'Otrosi4']

            # Crear cinco líneas predeterminadas con descripciones
            lineas_defaults = []
            for i in range(5):
                descripcion = descripciones[i] if i < len(descripciones) else ''
                lineas_defaults.append((0, 0, {'detalles': descripcion, 'valor_mensual': 0.0, 'valor_inicial': 0.0,
                                               'adiciones': 0.0, 'disminuciones': 0.0, 'total': 0.0}))

            # Asignar las líneas predeterminadas al formulario
            res['lineas_formulario_cdp'] = lineas_defaults

            return res

    # Campos adicionales
    name = fields.Char(string='Para: ', related='proyecto_relacionado_cdp.new_name', readonly=False)
    de_para = fields.Char(string='De: ')
    fecha_actual = fields.Date(string='Fecha actual')
    fecha_inicial = fields.Date(string='Fecha inicial')
    estado = fields.Char(string='Estado')
    vigencia = fields.Char(string='Vigencia')
    objeto = fields.Text(string='Objeto', related='proyecto_relacionado_cdp.objeto_contrato', readonly=False)
    contratista = fields.Char(string='Contratista', related='proyecto_relacionado_cdp.name', readonly=False)

    # Campos de contenido que se agregarán en la vista como hoja para ir agregando líneas hacia abajo
    detalles = fields.Char(string='Detalles', unique=True)
    inicial = fields.Char(string='Inicial')
    otrosi1 = fields.Char(string='Otrosi1')
    otrosi2 = fields.Char(string='Otrosi2')
    otrosi3 = fields.Char(string='Otrosi3')
    otrosi4 = fields.Char(string='Otrosi4')
    valor_mensual = fields.Float(string='Valor mensual')
    valor_inicial = fields.Float(string='Valor inicial')
    adiciones = fields.Float(string='Adiciones')
    disminuciones = fields.Float(string='Disminuciones')

    total = fields.Float(string='Total', compute='_compute_total', store=True)
    total_todos = fields.Float(string='Total Todos')

    # Campos fuera de esa vista
    valor_en_letras = fields.Char(string='Valor en letras')
    plazo = fields.Char(string='Plazo')
    plazo_inicial = fields.Date(string='Plazo inicial')
    plazo_final = fields.Date(string='Plazo final')
    proyecto = fields.Char(related='proyecto_relacionado_cdp.nombre_proyecto', readonly=False)
    signature_field = fields.Binary(string="Firma", attachment=True, options={'width': 100, 'height': 50})
    text_observaciones = fields.Text()
    eleborado_por = fields.Char(default=lambda self: self.env.user.name, readonly=True)
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())

    # Campos calculados para sumas verticales
    total_adiciones = fields.Float(string='Total adiciones', compute='_compute_total_general_adiciones', store=True)
    total_disminuciones = fields.Float(string='Total disminuciones', compute='_compute_total_general_disminucion',
                                       store=True)
    total_general = fields.Float(string='Total General', compute='_compute_total_general_total', store=True)
    total_valor_inicial = fields.Float(string="Total valor inicial", compute='_compute_total_general', store=True)

    @api.depends('lineas_formulario_cdp.total')
    def _compute_total_general_total(self):
        for actividad in self:
            actividad.total_general = sum(actividad.lineas_formulario_cdp.mapped('total'))

    @api.depends('lineas_formulario_cdp.disminuciones')
    def _compute_total_general_disminucion(self):
        for actividad in self:
            actividad.total_disminuciones = sum(actividad.lineas_formulario_cdp.mapped('disminuciones'))

    @api.depends('lineas_formulario_cdp.adiciones')
    def _compute_total_general_adiciones(self):
        for actividad in self:
            actividad.total_adiciones = sum(actividad.lineas_formulario_cdp.mapped('adiciones'))

    @api.depends('lineas_formulario_cdp.valor_inicial')
    def _compute_total_general(self):
        for actividad in self:
            actividad.total_valor_inicial = sum(actividad.lineas_formulario_cdp.mapped('valor_inicial'))

    @api.depends('valor_inicial', 'adiciones', 'disminuciones')
    def _compute_total(self):
        for record in self:
            total = record.valor_inicial + record.adiciones - record.disminuciones
            record.total = max(total, 0)  # Evitar números negativos

    detalles = fields.Char(string='Detalles', related='lineas_formulario_cdp.detalles')
    inicial = fields.Char(string='Inicial')
    otrosi1 = fields.Char(string='Otrosi1')
    otrosi2 = fields.Char(string='Otrosi2')
    otrosi3 = fields.Char(string='Otrosi3')
    otrosi4 = fields.Char(string='Otrosi4')
    valor_mensual = fields.Float(string='Valor mensual', related='lineas_formulario_cdp.valor_mensual')
    valor_inicial = fields.Float(string='Valor inicial', related='lineas_formulario_cdp.valor_inicial')
    adiciones = fields.Float(string='Adiciones', related='lineas_formulario_cdp.adiciones')
    disminuciones = fields.Float(string='Disminuciones', related='lineas_formulario_cdp.disminuciones')
    total = fields.Float(string='Total', compute='_compute_total', store=True, related='lineas_formulario_cdp.total')


class LineaFormularioCDP(models.Model):
    _name = 'formulario_cdp_linea'
    _description = 'Línea del formulario CDP'

    formulario_cdp_id = fields.Many2one('formulario_cdp', string='Formulario CDP')
    detalles = fields.Char(string='Detalles')
    inicial = fields.Char(string='Inicial')
    otrosi1 = fields.Char(string='Otrosi1')
    otrosi2 = fields.Char(string='Otrosi2')
    otrosi3 = fields.Char(string='Otrosi3')
    otrosi4 = fields.Char(string='Otrosi4')
    valor_mensual = fields.Float(string='Valor mensual')
    valor_inicial = fields.Float(string='Valor inicial')
    adiciones = fields.Float(string='Adiciones')
    disminuciones = fields.Float(string='Disminuciones')
    total = fields.Float(string='Total', compute='_compute_total', store=True)

    # Campos calculados para sumas verticales

    total_adiciones = fields.Float()
    total_disminuciones = fields.Float()
    total_general = fields.Float()
    total_valor_inicial = fields.Float()

    @api.depends('valor_inicial', 'adiciones', 'disminuciones')
    def _compute_total(self):
        for record in self:
            total = record.valor_inicial + record.adiciones - record.disminuciones
            record.total = max(total, 0)  # Evitar números negativos
























class Concepto(models.Model):
    _name = 'concepto'
    _description = 'Concepto'

    name = fields.Char(string='Concepto')
    base = fields.Char(string='Base')
    retencion = fields.Float(string='Retención')
    retencion2 = fields.Char(string='Retención')

    def abrir_concepto(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Concepto',
            'res_model': 'concepto',
            'view_mode': 'tree,form',
            'target': 'current',
        }


class Tabla2(models.Model):
    _name = 'tabla2'

    actividad_id2 = fields.Many2one('proceso_gestion_tributaria', string='Tabla')
    #Nuevo campo
    concepto_2 = fields.Many2one(comodel_name='concepto', string="Concepto")

    base = fields.Float(string='Base')
    retencion_fuente_porcentaje = fields.Float(string='% Retención en la fuente')


class Tabla(models.Model):
    _name = 'tabla'
    departamentos_y_municipios_ids = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id',
                                                     string='Tabla')
    actividad_id = fields.Many2one('proceso_gestion_tributaria', string='Tabla')
    # Campos del modelo
    municipio = fields.Many2one(comodel_name='municipio', string='Municipio')
    departamento = fields.Many2one(comodel_name='departamento', string='Departamento')




class Pais(models.Model):
    _name = 'pais'
    _description = 'Países'

    name = fields.Char(string='País')

    def abrir_Pais(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Países',
            'res_model': 'pais',
            'view_mode': 'tree',
            'target': 'current',
        }

class Tarifa(models.Model):
    _name = 'tarifa2'
    _description = 'Tarifa'

    name = fields.Char(string='Tarifa')

    def abrir_tarifa2(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarifa',
            'res_model': 'tarifa2',
            'view_mode': 'tree,form',
            'target': 'current',
        }





class ProcesoGestionTributaria(models.Model):
    _name = 'proceso_gestion_tributaria'
    _description = 'Proceso de gestión tributaria'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    def _validar_gestion_at_flujo_nuevo(self, contratacion=False):
        """Protege la gestión posterior del AT únicamente en el flujo nuevo."""
        contrataciones = contratacion or self.mapped('contratacion_fuv_en_id3')
        if not contrataciones.filtered('usa_flujo_contratacion_nuevo'):
            return
        contrataciones._validar_gestion_at_posterior_presupuesto()
        _logger.info(
            '[AT-PRESUPUESTO-FLUJO-NUEVO] Escritura autorizada en proceso '
            'tributario. at_ids=%s fc_ids=%s usuario_id=%s usuario=%s',
            self.ids, contrataciones.ids,
            self.env.user.id, self.env.user.name,
        )

    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################


    fecha = fields.Date(string='Fecha', related="contratacion_fuv_en_id3.fecha_solicitud", store=True)
    anio_ejecucion = fields.Char(string="Año de ejecución", compute='compute_anio_fecha', store=True)

    @api.depends('fecha')
    def compute_anio_fecha(self):
        """
        Calcula el año basado en la fecha de terminación.
        """
        for record in self:
            if record.fecha:
                # Extraer el año de la fecha
                record.anio_ejecucion = record.fecha.year
            else:
                record.anio_ejecucion = " "

    contratacion_fuv_en_id3 = fields.Many2one('contrataciones_fuv', string='Contratación FUV')

    mcb_centro_costo = fields.Char(
        related='contratacion_fuv_en_id3.mcb_centro_costo',
        string="Centro de costo", tracking=True)

    consecutivo_unido = fields.Char(string="Consecutivo", compute="generar_datos_tributarios")

    def generar_datos_tributarios(self):
        """Genera anio_ejecucion y consecutivo_unido una sola vez por registro"""
        for record in self:
            # Forzar el cálculo del año para el registro actual
            record.compute_anio_fecha()

            if record.consecutivo_unido:
                continue  # Ya tiene consecutivo, no recalcula

            contratacion = record.contratacion_fuv_en_id3
            if not contratacion:
                raise UserError(("Debe seleccionar una contratación FUV."))

            centro_costo = contratacion.mcb_centro_costo or "CC"
            anio = record.anio_ejecucion  # Usa el valor del registro actual

            count = record.id
            consecutivo = f"AT-{count:03d}-{centro_costo}-{anio}"
            print(f"✅ Consecutivo generado: {consecutivo}")

            record.consecutivo_unido = consecutivo

    #Nuevo campo
    def abrir_municipios_nuevos(self):
        """Abre la vista tree de municipios_nuevos permitiendo la creación de nuevos registros."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Municipios nuevos',
            'res_model': 'municipios_nuevos',
            'view_mode': 'tree,form',  # Habilita la vista de formulario para crear registros
            'target': 'current',
            'context': {'create': True}  # Permite la creación de nuevos registros
        }

    concepto_2 = fields.Many2one(comodel_name='concepto', string="CONCEPTO")
    tarifa_bd = fields.Char(string="TARIFA", related="contratacion_fuv_en_id3.tarifa_bd", store=True)
    tarifa_bd3 = fields.Char(string="TARIFA", related="municipios_combinados_id2.tarifa_bd", store=True)
    municipios_combinados_id2 = fields.One2many('municipios_combinados', 'proceso_id',
                                                string="Municipio combinado")
    municipios3 = fields.Many2one(related="municipios_combinados_id2.municipios3", string="MUNICIPIO")

    def repartir_dedicacion(self):
        for proceso in self:
            lineas = proceso.municipios_combinados_id2
            total_lineas = len(lineas)
            if total_lineas > 0:
                porcentaje = round(100 / total_lineas, 2)
                for linea in lineas:
                    linea.dedicacion_comibinados = porcentaje
        print("Se ejecutó action_repartir_dedicacion")

    def action_repartir_dedicacion(self):
        self.repartir_dedicacion()
        
    def transferir_municipios_seleccionados(self):
        """
        Limpia el anexo y sincroniza municipios únicos agregando Base Servicio y Base Bienes.
        """
        for record in self:
            _logger.info("🔄 Iniciando sincronización con bases nuevas para AT: %s", record.id)

            # 1. LIMPIEZA TOTAL
            self.env['municipios_combinados'].search([
                ('proceso_id', '=', record.id)
            ]).unlink()
            
            record.municipios_combinados_id2 = [(5, 0, 0)]

            if not record.concepto_2:
                _logger.warning("⚠️ No se puede procesar sin un Concepto definido.")
                continue

            municipios_dict = {}

            for municipio_rel in record.departamentos_y_municipios_ids2:
                if not municipio_rel.municipios2:
                    continue
                
                for municipio in municipio_rel.municipios2:
                    # Buscamos en el maestro de tarifas
                    municipio_maestro = self.env['municipios_nuevos'].search([
                        ('name', '=', municipio.name)
                    ], limit=1)

                    if municipio_maestro:
                        # Buscamos el registro específico del concepto para traer bases y tarifas
                        tarifa_record = self.env['municipios_nuevos'].search([
                            ('name', '=', municipio.name),
                            ('conceptos_bd3', '=', record.concepto_2.name)
                        ], limit=1)

                        # --- MODIFICACIÓN AQUÍ: Extraemos los valores Char ---
                        tarifa = tarifa_record.tarifa_bd3 if tarifa_record else "Tarifa no definida"
                        b_servicio = tarifa_record.base_servicio if tarifa_record else ""
                        b_bienes = tarifa_record.base_bienes if tarifa_record else ""

                        # Guardamos (o sobrescribimos) en el diccionario
                        municipios_dict[municipio.name] = (0, 0, {
                            'municipios3': municipio_maestro.id,
                            'conceptos_bd': record.concepto_2.name,
                            'tarifa_bd': tarifa,
                            'base_servicio': b_servicio,  # <--- NUEVO CAMPO
                            'base_bienes': b_bienes,      # <--- NUEVO CAMPO
                            'dedicacion_comibinados': 0.0
                        })
                    else:
                        _logger.error("❌ No existe en municipios_nuevos: %s", municipio.name)

            # 3. ESCRITURA
            if municipios_dict:
                record.write({'municipios_combinados_id2': list(municipios_dict.values())})
                _logger.info("✅ Sincronización exitosa con bases incluidas.")

            # 4. REPARTIR DEDICACIÓN
            record.action_repartir_dedicacion()

        return True


    def actualizar_tarifas_desde_proceso(self):
        """Llama al método en Departamentos_y_municipios para actualizar tarifas."""
        municipios = self.mapped('departamentos_y_municipios_ids2')
        if municipios:
            municipios.actualizar_todas_las_tarifas()


    base2 = fields.Char(related="concepto_2.base")
    retencion2 = fields.Float(related="concepto_2.retencion")
    retencion3 = fields.Char(related="concepto_2.retencion2")
    retencion2_visible = fields.Boolean(compute="_compute_retencion2_visible", store=True)


    @api.depends('concepto_2','municipios2')  # Esta función se ejecuta automáticamente cuando cambian 'concepto_2' o 'municipios2'
    def _compute_tarifa_bd(self):
        """
        Calcula el valor de 'tarifa_bd' basado en el campo 'concepto_2'.
        Si 'concepto_2' tiene un valor en 'base', lo asigna a 'tarifa_bd'.
        Si 'concepto_2' está vacío, asigna una cadena vacía.
        """
        for record in self:
            # Asigna el valor de 'base' de 'concepto_2' a 'tarifa_bd', si existe
            record.tarifa_bd = record.concepto_2.base if record.concepto_2 else ''



    @api.depends("retencion2")
    def _compute_retencion2_visible(self):
        for record in self:
            record.retencion2_visible = record.retencion2 == 0
        #print("Se ejecuto actualizar_tarifas_desde_proceso3")
        #print("Se ejecuto transferir_municipios_seleccionados")
        self.actualizar_tarifas_desde_proceso()
        self.transferir_municipios_seleccionados()
        self.action_repartir_dedicacion()



    retencion_fuente_porcentaje2 = fields.Float(string="% Retención")
    departamentos_y_municipios_ids2 = fields.One2many(
        'departamento_y_municipio',
        related="contratacion_fuv_en_id3.departamentos_y_municipios_ids",
        readonly=False
    )

    datos_adicionales = fields.One2many('tabla', 'actividad_id', string='Rentencion')
    datos_otros = fields.One2many('tabla2', 'actividad_id2', string='Rentencion2')

    informe_adjunto = fields.Binary(string='Adjunto del informe', readonly=True, tracking=True)

    codigo_ciiu_2 = fields.Char(
        string='Código CIIU',
        related='contratacion_fuv_en_id3.codigo_ciiu',
        readonly=False,
        store=True
    )

    total_departamentos = fields.Integer(
        string='Total',
        related='contratacion_fuv_en_id3.total_departamentos',
        store=True
    )



    tarifa = fields.Selection([
        ('1', '1 * 1000'),
        ('2', '2 * 1000'),
        ('3', '3 * 1000'),
        ('4', '4 * 1000'),
        ('5', '5 * 1000'),
        ('6', '6 * 1000'),
        ('7', '7 * 1000'),
        ('8', '8 * 1000'),
        ('9', '9 * 1000'),
        ('10', '10 * 1000')
    ], string='Utilizar una tarifa igual para todos', store=True)

    dedicacion = fields.Float(string='% Dedicación', store=True)

    @api.onchange('tarifa', 'dedicacion')
    def _onchange_campos_relacionados(self):
        """Actualiza los campos en el registro relacionado"""
        if self.contratacion_fuv_en_id3 and self.departamentos_y_municipios_ids2:
            for linea in self.departamentos_y_municipios_ids2:
                if self.tarifa:
                    linea.tarifa = self.tarifa
                if self.dedicacion:
                    linea.dedicacion = self.dedicacion

    @api.model
    def create(self, vals):
        """Sobrescribe create para manejar la creación de registros"""
        contratacion = self.env['contrataciones_fuv'].browse(
            vals.get('contratacion_fuv_en_id3')
        ).exists()
        self._validar_gestion_at_flujo_nuevo(contratacion=contratacion)
        record = super(ProcesoGestionTributaria, self).create(vals)
        record._sincronizar_campos_relacionados()
        return record

    def write(self, vals):
        """Sobrescribe write para manejar la actualización de registros"""
        self._validar_gestion_at_flujo_nuevo()
        result = super(ProcesoGestionTributaria, self).write(vals)
        self._sincronizar_campos_relacionados()
        return result

    def _sincronizar_campos_relacionados(self):
        """Método auxiliar para sincronizar campos relacionados"""
        for record in self:
            if record.contratacion_fuv_en_id3 and record.departamentos_y_municipios_ids2:
                for linea in record.departamentos_y_municipios_ids2:
                    valores = {}
                    if record.tarifa:
                        valores['tarifa'] = record.tarifa
                    if record.dedicacion:
                        valores['dedicacion'] = record.dedicacion
                    if valores:
                        linea.write(valores)
    ###################################################################################################################
    #No es la relación
    proyecto_relacionado_anexo = fields.Many2one('contrataciones_fuv', string='Selecionar contratista')
    #No es la relación
    eleborado_por = fields.Char(default=lambda self: self.env.user.name, readonly=True)

    version = fields.Text(string='Versión')
    pagina = fields.Char(string='Página')
    subtitulo = fields.Text()


    de = fields.Char(string='De:')
    name = fields.Char(string='Para:', related='contratacion_fuv_en_id3.name', readonly=True)
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())
    asunto = fields.Text(string='Asunto', related='contratacion_fuv_en_id3.proyecto_convenio', readonly=True)

    nombre_del_proyecto = fields.Many2one(related='contratacion_fuv_en_id3.proyecto_relacionado')


    # Subtítulo 1
    codigo_ciiu = fields.Char(string='Código CIIU', related='contratacion_fuv_en_id3.codigo_ciiu', readonly=True)
    objeto_contractual = fields.Text(string='Objeto contractual', related='contratacion_fuv_en_id3.objeto_contrato',
                                     readonly=True)

    # Subtítulo 2
    currency_id = fields.Many2one('res.currency', string='Moneda')
    valor_contrato = fields.Monetary(string='Valor del contrato',
                                     related='contratacion_fuv_en_id3.valor_total_contrato', readonly=True)
    forma_pago2 = fields.Text(string='Forma de pago', related='contratacion_fuv_en_id3.forma_pago', readonly=True)

    responsable = fields.Boolean(string='Responsable', related="contratacion_fuv_en_id3.r_iva")
    no_responsable = fields.Boolean(string='No responsable', related="contratacion_fuv_en_id3.no_r_iva")

    excluido = fields.Boolean(string='Excluido')
    exento = fields.Boolean(string='Exento')
    retencion_iva = fields.Boolean(string='Retención IVA', compute='_compute_retencion_iva', store=True, readonly=False)

    @api.depends('responsable')
    def _compute_retencion_iva(self):
        for record in self:
            record.retencion_iva = record.responsable

    @api.onchange('responsable')
    def _onchange_responsable(self):
        if self.responsable:
            self.no_responsable = False
            self.excluido = False
            self.exento = False

    @api.onchange('no_responsable')
    def _onchange_no_responsable(self):
        if self.no_responsable:
            self.responsable = False
            self.excluido = False
            self.exento = False

    @api.onchange('excluido')
    def _onchange_excluido(self):
        if self.excluido:
            self.responsable = False
            self.no_responsable = False
            self.exento = False

    @api.onchange('exento')
    def _onchange_exento(self):
        if self.exento:
            self.responsable = False
            self.no_responsable = False
            self.excluido = False

    # Nota
    nota = fields.Text(string='Nota')

    # Subtítulo 5
    retencion_fuente = fields.Boolean(string='Retención en la fuente')

    # Otros
    facturador_electronico = fields.Selection([('si', 'Sí'), ('no', 'No')], default='no', compute='compute_facturador_electronico')
    f_electronica = fields.Boolean(string='Facturación electronica', related='contratacion_fuv_en_id3.f_electronica')

    @api.depends('f_electronica')
    def compute_facturador_electronico(self):
        if self.f_electronica == True:
            self.facturador_electronico = 'si'
        else: self.facturador_electronico = 'no'

    declaracion_renta = fields.Selection([('si', 'Sí'), ('no', 'No')], default='no', related='contratacion_fuv_en_id3.declaracion_renta')
    regimen_simple_tributacion = fields.Selection([('si', 'Sí'), ('no', 'No')], string='Régimen simple de tributación', default='no', related='contratacion_fuv_en_id3.regimen_simple_tributacion')
    regimen_especial = fields.Selection([('si', 'Sí'), ('no', 'No')], string='Régimen especial', default='no', related='contratacion_fuv_en_id3.regimen_especial')
    regimen_ordinario = fields.Selection([('si', 'Sí'), ('no', 'No')], string='Régimen ordinario', default='no', related='contratacion_fuv_en_id3.regimen_ordinario')
    signature_field = fields.Binary(string="Firma", attachment=True, )
    signature_field2 = fields.Binary(string="Firma", attachment=True, )
    signature_field3 = fields.Binary(string="Firma", attachment=True, )
    signature_field4 = fields.Binary(string="Firma", attachment=True, )



    # Subtítulo 5 - Tabla YA NO SE USARA
    base = fields.Float(string='Base', related='datos_otros.base')
    retencion_fuente_porcentaje = fields.Float(string='% Retención en la fuente',
                                               related='datos_otros.retencion_fuente_porcentaje')

    ######GENERAR_WORD##############################################################################################

    def guardar_documento_adjunto(self, base64_documento):
        # Decodificar la cadena Base64 a datos binarios
        documento_binario = base64.b64decode(base64_documento)
        self.write({'informe_adjunto': base64_documento})
        return True

    def generar_datos_tabla(self):
        # Obtener los datos existentes de la tabla
        datos_existente = self.datos_adicionales

        # Si hay datos existentes, simplemente devolverlos
        if datos_existente:
            return datos_existente
        else:
            # Si no hay datos existentes, generamos datos aleatorios como antes
            num_filas = 10
            datos_tabla = []
            for _ in range(num_filas):
                # Obtener un departamento y municipio aleatorio
                departamento = self.env['departamento'].search([], limit=1)
                municipio = self.env['municipio'].search([], limit=1)

                # Verificar si se encontraron departamentos y municipios
                if departamento and municipio:
                    datos_tabla.append({
                        'departamento': departamento.name,  # Acceder al campo 'name' del departamento
                        'municipio': municipio.name,  # Acceder al campo 'name' del municipio
                        'codigo_ciiu_2': random.choice([True, False]),  # True o False al azar
                        'tarifa': round(random.uniform(1, 10), 2),
                        'base_servicio': "Sin base definida", 
                        'base_bienes': "Sin base definida",
                        # Número aleatorio entre 1 y 10, redondeado a 2 decimales
                        'dedicacion': random.randint(1, 100)  # Número aleatorio entre 1 y 100

                    })

            return []

    def generar_datos_tabla2(self):
        # Obtener los datos existentes de la tabla
        datos_existente = self.datos_otros

        # Si hay datos existentes, simplemente devolverlos
        if datos_existente:
            return datos_existente
        else:
            # Si no hay datos existentes, generamos datos aleatorios como antes
            num_filas = 10
            datos_tabla2 = []
            for _ in range(num_filas):
                base = round(random.uniform(1, 1000), 2)  # Generar un valor aleatorio para 'base'
                retencion_fuente_porcentaje = round(random.uniform(1, 10), 2)
                concepto = random.randint(1, 100)  # Número aleatorio entre 1 y 100

                datos_tabla2.append({
                    'base': base,
                    'retencion_fuente_porcentaje': retencion_fuente_porcentaje,
                    'concepto': concepto
                })

            return []

    def render_informe_template_anexo(self):
        # Obtener la ruta del directorio actual
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        # Cargar la plantilla desde el archivo .docx utilizando python-docx-template
        template_path = os.path.join(current_directory, "plantilla_anexo_tributario.docx")
        template = DocxTemplate(template_path)

        # Generar los datos para la tabla
        datos_tabla = self.generar_datos_tabla()
        datos_tabla2 = self.generar_datos_tabla2()

        # Obtener el contexto de Odoo
        context = {'docs': self, 'tabladedatos': datos_tabla, 'tabladedatos2': datos_tabla2}
        # Renderizar la plantilla con los datos actuales del registro
        template.render(context)
        # Guardar el resultado en un nuevo archivo .docx
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_plantilla_rendered_anexo.docx")
        template.save(output_path)
        return output_path

    def generar_informe_docx(self):
        record_id = self.id
        template_rendered = self.render_informe_template_anexo()

        with open(template_rendered, 'rb') as template_file:
            contenido_base64 = base64.b64encode(template_file.read())
            self.write({'informe_adjunto': contenido_base64})

        # --- LIMPIEZA SIMPLE ---
        # Cambiamos '&' por 'y' para que el archivo en Drive y la referencia no den problemas
        nombre_sucio = self.name or 'Informe_proceso_gestion_tributaria'
        nombre_limpio = nombre_sucio.replace('&', 'y').strip()
        
        # Nombre del archivo final
        nombre_archivo = "AT {}.docx".format(nombre_limpio)

        # Subir a Google Drive con el nombre ya limpio
        drive_record = self.subir_documento_informe_a_drive_anexo(nombre_archivo, contenido_base64)

        # Limpiar el archivo temporal
        os.remove(template_rendered)

        if drive_record:
            drive_record.view_mode = 'edit'
            return {
                'name': 'Documento en Google Drive',
                'type': 'ir.actions.act_window',
                'res_model': 'drive',
                'res_id': drive_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

        raise UserError("Error al subir el archivo a Google Drive.")
    
    

    def subir_documento_informe_a_drive_anexo(self, nombre_archivo, contenido_base64):
        """Sube informe_adjunto a Google Drive y crea un registro en el modelo drive."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        folder_id = self.drive_file_id.folder_id2
        if not folder_id:
            _logger.error("No se encontró la carpeta de destino en Drive.")
            return False

        try:
            # Crear archivo temporal
            file_content = base64.b64decode(contenido_base64)
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            file_metadata = {
                'title': nombre_archivo,
                'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'parents': [{'id': folder_id}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive['id']
            file_url = f"https://drive.google.com/file/d/{file_id}/view"

            _logger.info(f"Archivo subido a Drive. ID: {file_id}")

            # Excepción exclusiva del flujo nuevo de AT: el proyecto mantiene
            # sus carpetas en modo lector, pero el usuario de Presupuesto que
            # generó este archivo recibe edición sobre este file_id concreto.
            fc = self.contratacion_fuv_en_id3
            if fc.usa_flujo_contratacion_nuevo:
                proyecto = fc.proyecto_relacionado
                if not proyecto:
                    raise UserError(
                        "El FC relacionado con el AT no tiene proyecto asociado."
                    )
                proyecto.otorgar_permiso_edicion_archivo_at(
                    drive_file_id=file_id,
                    modelo_origen=self._name,
                    usuario=self.env.user,
                )

            # Crear registro en drive
            drive_record = self.env['drive'].create({
                'file_name': nombre_archivo,
                'file_id': file_id,
                'file_url': file_url,
                'file_data': contenido_base64,
                'folder_id': folder_id,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id,
            })

            return drive_record

        except Exception as e:
            _logger.error(f"Error al subir el informe a Google Drive: {e}")
            return False

    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive',
                                    related='contratacion_fuv_en_id3.drive_file_id')  # Campo para vincular con drive.file

    ##############################################################################################################


# noinspection PyPackageRequirements
class TablaDeFormaDePago(models.Model):
    _name = 'tabla_forma_de_pago'
    _description = 'Tabla de forma de pago'
    _rec_name = "pago"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    campos_combinados = fields.Char(string='Campos Combinados', related="contrataciones_fuv_tabla_id.campos_combinados",
                                    tracking=True)
    #19-11-2024 SAMUEL
    ############################################SAMUEL##################################################################
    is_editable = fields.Boolean(string='Es editable', related="contrataciones_fuv_tabla_id.is_editable", default=False)
    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################

    cantidad_de_dinero = fields.Float(string="CANTIDAD DE DINERO", tracking=True,)
    contratista_reportes = fields.Many2one(comodel_name='contrataciones_fuv', string="Contratista",
                                           tracking=True, store=True)
    contrataciones_fuv_tabla_id = fields.Many2one('contrataciones_fuv', string='Tabla')
    currency_id = fields.Many2one('res.currency', string='Moneda', tracking=True)
    total = fields.Monetary('Valor de contrato con otrosí', related='contrataciones_fuv_tabla_id.total', tracking=True,)

    #VARIABLES
    condicion = fields.Char(string="CONDICIÓN", tracking=True,)
    tecnico = fields.Float(string="% TÉCNICO", tracking=True,)
    pago = fields.Float(string="% DE PAGO", compute="_compute_porcentaje_pago", tracking=True,)

    # Nuevo campo para indicar si ha sido seleccionado
    seleccionado = fields.Boolean(string='Seleccionado', tracking=True,)
    numero_de_pagos = fields.Integer(string='# PAGOS', compute='_compute_numero_de_pagos', store=True, tracking=True,)
    porcentaje_acumulado = fields.Float(string='% ACUMULADO', compute='_compute_porcentaje_acumulado', store=True)
    id_del_contrato = fields.Char(string='ID del contrato', related="contrataciones_fuv_tabla_id.id_del_contrato",
                                  tracking=True, )

    @api.depends('pago', 'cantidad_de_dinero', 'contrataciones_fuv_tabla_id.total')
    def _compute_porcentaje_pago(self):
        for pago1 in self:
            if pago1.cantidad_de_dinero:
                pago1.pago = (pago1.cantidad_de_dinero / pago1.total * 100.0)
            else:
                pago1.pago = 0.0


    @api.depends('contrataciones_fuv_tabla_id')
    def _compute_numero_de_pagos(self):
        for record in self:
            if record.contrataciones_fuv_tabla_id:
                # Fetch all records related to the same contrataciones_fuv_tabla_id
                related_records = self.search([('contrataciones_fuv_tabla_id', '=', record.contrataciones_fuv_tabla_id.id)], order='id')
                for index, related_record in enumerate(related_records, start=1):
                    # Use update method to avoid triggering write method
                    related_record.update({'numero_de_pagos': index})

    @api.depends('cantidad_de_dinero')
    def _compute_porcentaje_acumulado(self):
        self.obtener_cantidad_de_dinero()
        for record in self:
            if record.contrataciones_fuv_tabla_id:
                # Fetch all records related to the same contrataciones_fuv_tabla_id, ordered by numero_de_pagos
                related_records = self.search([('contrataciones_fuv_tabla_id', '=', record.contrataciones_fuv_tabla_id.id)], order='numero_de_pagos')
                acumulado = 0.0
                for related_record in related_records:
                    acumulado += related_record.pago
                    related_record.porcentaje_acumulado = acumulado


    @api.model
    def create(self, vals):
        record = super().create(vals)
        record._compute_numero_de_pagos()
        record._compute_porcentaje_acumulado()
        record._post_trazabilidad()  # ← una sola vez
        return record

    def write(self, vals):
        res = super().write(vals)
        campos_a_monitorear = {'condicion', 'tecnico', 'cantidad_de_dinero'}
        campos_modificados = campos_a_monitorear.intersection(vals.keys())
        if campos_modificados:
            # Solo postear una vez usando el primer registro
            self[0]._post_trazabilidad()
        return res


    def _post_trazabilidad(self):
        self.ensure_one()

        def formato(val):
            # Maneja casos donde el valor pueda ser None para evitar errores
            val = val or 0.0
            return "{:,.2f}".format(val).replace(",", "X").replace(".", ",").replace("X", ".")

        # Iniciamos el mensaje con el título
        lineas = ["<b>Registro actualizado:</b>"]

        # Agregamos cada campo con su etiqueta en negrita
        lineas.append(f"<b>Número de pago:</b> {self.numero_de_pagos}")
        lineas.append(f"<b>% Acumulado:</b> {formato(self.porcentaje_acumulado)}")
        lineas.append(f"<b>Cantidad de dinero:</b> {formato(self.cantidad_de_dinero)}")
        lineas.append(f"<b>% de pago:</b> {formato(self.pago)}")

        # Condición lógica: solo si existe (no es False o None)
        if self.condicion:
            lineas.append(f"<b>Condición:</b> {self.condicion}")

        lineas.append(f"<b>% Técnico:</b> {formato(self.tecnico)}")

        # Unimos todas las líneas con un salto de línea HTML
        mensaje = "<br/>".join(lineas)

        self.contrataciones_fuv_tabla_id.message_post(body=mensaje)
        
    chart_data = fields.Char(string="Datos del Gráfico", compute="_compute_chart_data")

    @api.model
    def obtener_cantidad_de_dinero(self):
        registros = self.search([])
        cantidad_de_dinero = registros.mapped('cantidad_de_dinero')
        lista_cantidad_de_dinero = list(cantidad_de_dinero)
        print(lista_cantidad_de_dinero)
        return lista_cantidad_de_dinero

    @api.depends('cantidad_de_dinero')
    def _compute_chart_data(self):
        for record in self:
            lista_cantidad_de_dinero = self.obtener_cantidad_de_dinero()
            chart_data = {
                'labels': [f"Registro {i + 1}" for i in range(len(lista_cantidad_de_dinero))],
                'datasets': [{
                    'label': 'Cantidad de Dinero',
                    'data': lista_cantidad_de_dinero,
                    'backgroundColor': ['#4bc0c0', '#9966ff', '#ffa64d', '#ff6384', '#36a2eb', '#ffcc99', '#ff6666',
                                        '#339966', '#cc99ff', '#ff9933', '#6699ff', '#ffcc00']
                }]
            }
            record.chart_data = json.dumps(chart_data)

class EPS(models.Model):
    _name = 'eps'
    _description = 'Eps'

    name = fields.Char(string='Nombre de la EPS')

    def abrir_Eps(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Eps',
            'res_model': 'eps',
            'view_mode': 'tree,form',
            'target': 'current',
        }

class ARL(models.Model):
    _name = 'arl'
    _description = 'arl'

    name = fields.Char(string='Nombres de ARLs')

    def abrir_Arl(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Arl',
            'res_model': 'arl',
            'view_mode': 'tree,form',
            'target': 'current',
        }

class FondoPension(models.Model):
    _name = 'fondopension'
    _description = 'fondo de pension'

    name = fields.Char(string='Nombres de Fondo de pensiones')

    def abrir_FondoPension(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'FondoPension',
            'res_model': 'fondopension',
            'view_mode': 'tree,form',
            'target': 'current',
        }

class CajasCompensacion(models.Model):
    _name = 'cajascompensacion'
    _description = 'Cajas de Compensacion'

    name = fields.Char(string='Nombres de Cajas de Compensación')

    def abrir_CajasCompesancion(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'CajasCompesancion',
            'res_model': 'cajascompensacion',
            'view_mode': 'tree,form',
            'target': 'current',
        }
        
        
        
        
        
        
class CloseContractWizard(models.TransientModel):
    _name = 'contrataciones_fuv.close.wizard'
    _description = 'Cerrar contrato (wizard)'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive', related="contract_id.drive_file_id") 

    contract_id = fields.Many2one('contrataciones_fuv', string='Contrato')

    confirmar_cierre = fields.Boolean(string='¿Desea cerrar el contrato?', required=True)

    tipo_acta = fields.Selection([
        ('no', 'No requiere acta'),
        ('normal', 'Acta de liquidación de mutuo acuerdo'),
        ('incumplimiento', 'Acta de liquidación unilateral'),
        ('anulacion', 'Acta de anulación'),
    ], string='Tipo de acta', required=True)

    archivo_acta = fields.Binary("Acta")
    archivo_acta_nombre = fields.Char("Nombre del archivo")
    archivo_acta_visible = fields.Boolean(string="Archivo Acta Visible", default=True)
    archivo_acta_id = fields.Many2one('drive', string='Archivo del acta')

    def write(self, vals):
        # Ejecutamos el guardado y Drive primero
        res = super(CloseContractWizard, self).write(vals)
        
        # --- LÓGICA DRIVE EXISTENTE (Sin cambios) ---
        if 'archivo_acta' in vals: self.subir_archivo_acta_a_drive()
        return res

    
    def subir_archivo_acta_a_drive(self):
        """Sube el Archivo del acta a Google Drive y lo registra en el modelo drive."""
        if self.archivo_acta and self.archivo_acta_nombre:
            _logger.info(f"Subiendo documento '{self.archivo_acta_nombre}' a Google Drive...")
            self._upload_archivo_acta_to_drive()
        else:
            _logger.warning("No se encontró el Anexo tributario para subir.")

    def _upload_archivo_acta_to_drive(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Validar que self.drive_file_id es un objeto Many2one y no un string
        if isinstance(self.drive_file_id, str):
            _logger.error(f"El campo drive_file_id tiene un valor de tipo string: {self.drive_file_id}")
            return False

        # Buscar el registro en drive.file basado en el ID del archivo
        drive_file_record = self.env['drive.file'].search([('id', '=', self.drive_file_id.id)], limit=1)

        if not drive_file_record:
            _logger.error(
                f"No se encontró un registro en drive.file con el ID {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        carpeta_drive = drive_file_record.folder_id2  # Usamos 'folder_id2' aquí
        if not carpeta_drive:
            _logger.error("No se encontró la carpeta de Google Drive para subir el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.archivo_acta)
            original_extension = os.path.splitext(self.archivo_acta_nombre)[1]
            # === NUEVA LÓGICA PARA EL NOMBRE DEL ARCHIVO ===
            tipo_nombre = "Acta"
            if self.tipo_acta == 'normal':
                tipo_nombre = "Acta_de_liquidacion_mutuo_acuerdo"
            elif self.tipo_acta == 'incumplimiento':
                tipo_nombre = "Acta_de_liquidacion_unilateral"
            elif self.tipo_acta == 'anulacion':
                tipo_nombre = "Acta_de_anulacion"
            
            original_extension = os.path.splitext(self.archivo_acta_nombre)[1]
            # Ahora el nombre lleva el tipo elegido + un ID único para evitar duplicados
            fixed_file_name = f"{tipo_nombre}_{uuid.uuid4().hex[:8]}{original_extension}"
            # ===============================================

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.archivo_acta_nombre} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google Drive
            file_metadata = {
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': carpeta_drive}]
            }

            file_drive = drive.CreateFile(file_metadata)
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()

            file_id = file_drive['id']
            file_link = f"https://drive.google.com/file/d/{file_id}/view"

            _logger.info(f"Archivo subido correctamente a Google Drive con ID: {file_id} y URL: {file_link}")

            # Crear el registro en el modelo 'drive' y asignar correctamente 'drive_file_id' y 'stage2_id'
            drive_record = self.env['drive'].create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': self.archivo_acta,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Solo actualizar el campo archivo_acta_id
            if not self.archivo_acta_id:
                self.archivo_acta_id = drive_record.id

            _logger.info(
                f"Campo Anexo_tributario_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar archivo_acta_visible a False después de subir el archivo
            self.archivo_acta_visible = False

            _logger.info(f"Campo archivo_acta_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    _cache_documentos_archivo_acta = {}

    def abrir_documento_archivo_acta_pdf(self):
        self.archivo_acta = False
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'archivo_acta_id' está configurado
        if self.archivo_acta_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Archivo del acta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.archivo_acta_id.id,
                'target': 'new',
            }

        # Si 'archivo_acta_nombre' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.archivo_acta_nombre:
            file_name_to_search = self.archivo_acta_nombre.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_archivo_acta:
                drive_document = self._cache_documentos_archivo_acta[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_archivo_acta[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'archivo_acta_id' con el ID del documento encontrado
                self.write({'archivo_acta_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Archivo del acta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna archivo del acta relacionada con este registro.')

    @api.onchange('tipo_acta')
    def _onchange_tipo_acta(self):
        if self.tipo_acta == 'no':
            self.archivo_acta = False
            self.archivo_acta_nombre = False
            self.archivo_acta_id = False  # Limpia el Many2one
            self.archivo_acta_visible = True # Reset por defecto si no hay acta

    def _asegurar_archivo_acta_id(self):
        """Garantiza que el acta cargada en el wizard tenga registro Drive."""
        self.ensure_one()
        if self.archivo_acta_id or self.tipo_acta not in ['normal', 'incumplimiento', 'anulacion']:
            return bool(self.archivo_acta_id)

        if self.archivo_acta and self.archivo_acta_nombre:
            self.subir_archivo_acta_a_drive()

        return bool(self.archivo_acta_id)

    def action_confirmar_cierre(self):
        self.ensure_one()

        if not self.confirmar_cierre:
            raise UserError("Debe confirmar si desea cerrar el contrato.")

        # Validación según tu lógica: si requiere acta, el Many2one no puede estar vacío
        if self.tipo_acta in ['normal', 'incumplimiento', 'anulacion'] and not self._asegurar_archivo_acta_id():
            raise UserError("Debe subir o seleccionar el archivo del acta.")

        contrato = self.contract_id

        # Preparamos los valores para actualizar el contrato
        vals = {
            'tipo_acta_liquidacion': self.tipo_acta,
            'is_editable': True,
        }

        # Solo enviamos el ID y la visibilidad si el usuario seleccionó un archivo
        if self.archivo_acta_id:
            vals.update({
                'archivo_acta_id': self.archivo_acta_id.id,
                'archivo_acta_visible': self.archivo_acta_visible,
            })
        else:
            # Si no hay acta, nos aseguramos de limpiar los campos en el contrato
            vals.update({
                'archivo_acta_id': False,
                'archivo_acta_visible': True,
            })

        # Ejecutamos la actualización en el modelo contrataciones_fuv
        contrato.write(vals)

        # Notificación al coordinador
        contrato.notify_coordinator(send_email=True)

        return {'type': 'ir.actions.act_window_close'}

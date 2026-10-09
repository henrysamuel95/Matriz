#!/usr/bin/python
from datetime import datetime, timedelta

from odoo import fields, models, api
from odoo.exceptions import ValidationError  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from .drive import GoogleDriveAuth
import requests
import json
import re  # Asegúrate de importar el módulo de expresiones regulares
import logging

_logger = logging.getLogger(__name__)


class OTROSI(models.Model):
    _name = "otrosi"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "Gestor de documentos"

    name = fields.Char(string='Nombre', required=True)
    workspace = fields.Many2many('drive.file', string='Carpeta')
    custom_tags_ids = fields.One2many('documents.tag', 'buscador', string='Etiquetas')
    etiquetas = fields.Many2many('documents.tag', string='Etiquetas')
    tags_html = fields.Char('Etiquetas HTML')
    workspace_cuenta_de_cobros = fields.Many2many('cuenta_de_cobros', string='Carpeta')
    workspace_otrosi_drive = fields.Many2many('otrosi_drive', string='Carpeta')
    detalles = fields.Boolean(string="Destalles", default="False")

    def set_detalles_to_false(self):
        for record in self:
            # Establecer 'detalles' en False en el registro actual
            record.detalles = False

            # Buscar todos los registros relacionados en el modelo 'drive'
            drive_records = self.env['drive'].search([('buscador', '=', record.id)])

            if drive_records:
                # Actualizar 'ejecutar_toggle' a False en todos los registros encontrados
                drive_records.write({'ejecutar_toggle': False})

    def toggle_show_documents(self):
        # Limpiar la selección de los campos `workspace`, `workspace_cuenta_de_cobros`, `workspace_otrosi_drive`, y `etiquetas`
        for record in self:
            record.workspace = [(5, 0, 0)]  # Limpiar selección de `workspace`
            record.workspace_cuenta_de_cobros = [(5, 0, 0)]  # Limpiar selección de `workspace_cuenta_de_cobros`
            record.workspace_otrosi_drive = [(5, 0, 0)]  # Limpiar selección de `workspace_otrosi_drive`
            record.etiquetas = [(5, 0, 0)]  # Limpiar selección de `etiquetas``

    file_name = fields.Char(string='File Name')
    user_id = fields.Many2one('res.users', string='User')
    carpeta_id = fields.Char(string='Carpeta')

    # Campo de búsqueda
    # Campo computado que devolverá solo los documentos filtrados
    documentos_filtrados = fields.Many2many('drive', compute='_compute_documentos_filtrados',
                                            string='Documentos Filtrados')

    search_keyword = fields.Char('Buscar Palabra Clave')
    search_filter = fields.Selection([
        ('name', 'Buscar por Nombre'),
        ('tags_html', 'Buscar por Etiquetas HTML'),
        ('api', 'Buscar por API'),
    ], string='Filtro de Búsqueda', default='name')

    @api.depends('search_keyword', 'search_filter', 'workspace', 'workspace_cuenta_de_cobros', 'workspace_otrosi_drive',
                 'etiquetas')
    def _compute_documentos_filtrados(self):
        for record in self:
            domain = []
            api_matching_ids = []  # IDs obtenidos desde la API

            # Verificar el filtro seleccionado
            if record.search_filter == 'name' and record.search_keyword:
                # Si el filtro es 'name', buscar por nombre
                domain.append(('name', 'ilike', record.search_keyword))

            elif record.search_filter == 'tags_html' and record.search_keyword:
                # Si el filtro es 'tags_html', buscar por etiquetas HTML
                domain.append(('tags_html', 'ilike', record.search_keyword))

            elif record.search_filter == 'api' and record.search_keyword:
                # Si el filtro es 'api', buscar en la API
                search_keywords = [keyword.strip().lower() for keyword in record.search_keyword.split(',')]
                url = 'https://03489969833a49108391a9c27b47ccf1.api.mockbin.io/'
                response = requests.get(url)

                if response.status_code == 200:
                    try:
                        documents = json.loads(response.text)

                        if isinstance(documents, list):
                            for document in documents:
                                content = document.get('contenido', '').lower()

                                # Verificar si alguna de las palabras clave está en el contenido
                                matching_keywords = [keyword for keyword in search_keywords if keyword in content]

                                # Si alguna palabra clave coincide, agregar el ID
                                if matching_keywords:
                                    api_matching_ids.append(document.get('ID', ''))

                    except ValueError as e:
                        record.documentos_filtrados = 'Error al parsear la respuesta de la API'
                        continue

            # Filtro por workspace
            if record.workspace:
                domain.append(('stage2_id', 'in', record.workspace.ids))

            # Filtro por workspace_cuenta_de_cobros
            if record.workspace_cuenta_de_cobros:
                domain.append(('stage4_id', 'in', record.workspace_cuenta_de_cobros.ids))

            # Filtro por workspace_otrosi_drive
            if record.workspace_otrosi_drive:
                domain.append(('stage5_id', 'in', record.workspace_otrosi_drive.ids))

            # Filtro por etiquetas
            if record.etiquetas and record.etiquetas.ids:
                domain.append(('tag_ids', 'in', record.etiquetas.ids))

            # Agregar filtro por IDs obtenidos desde la API
            if api_matching_ids:
                domain.append(('file_id', 'in', api_matching_ids))

            # Si no hay filtros específicos, mostrar todos los documentos relacionados
            if not domain:
                domain = [('buscador', '=', record.id)]

            # Realizar la búsqueda con el dominio
            documentos = self.env['drive'].search(domain)

            # Asignar los documentos encontrados al campo documentos_filtrados
            record.documentos_filtrados = documentos

    def cog(self):
        return

    _cache_documentos_file_name = {}

    def Ver(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Obtener y limpiar el nombre del archivo
        file_name_to_search = self.file_name.strip().replace(" ", "_")

        # Imprimir el nombre que se está buscando para depuración
        _logger.info('Buscando documento con nombre: %s', file_name_to_search)

        # Comprobar si el documento ya está en el cache
        if file_name_to_search in self._cache_documentos_file_name:
            drive_document = self._cache_documentos_file_name[file_name_to_search]
        else:
            # Buscar documentos en el modelo 'drive' que coincidan insensiblemente
            drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

            if drive_documents:
                # Si se encuentra el documento, usar el primer resultado
                drive_document = drive_documents[0]

                # Almacenar el documento en el cache
                self._cache_documentos_file_name[file_name_to_search] = drive_document
            else:
                drive_document = None

        if drive_document:
            # Abrir el registro en la vista de formulario
            return {
                'type': 'ir.actions.act_window',
                'name': self.file_name,
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': drive_document.id,
                'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
            }
        else:
            # Si no se encuentra el documento, lanzar un error
            _logger.warning('Documento no encontrado en el modelo Drive. Nombre buscado: %s', file_name_to_search)
            raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    def Enviar(self):
        return

    def Eliminar(self):
        return


class DocumentsTag(models.Model):
    _name = 'documents.tag'
    _description = 'Etiqueta para Documentos'

    name = fields.Char(string='Nombre', required=True)
    buscador = fields.Many2one(
        'otrosi',
        string='Proyecto relacionado',
        default=lambda self: self.env['otrosi'].search([], limit=1).id or False)


class CoinloreAPI(models.Model):
    _name = 'coinlore.api'
    _description = 'Coinlore API Integration'

    search_term = fields.Char(string='Buscar Documento',
                              help="Ingresa palabras clave separadas por comas para buscar en el contenido.")
    document_ids = fields.Char(string='IDs de Documentos', help="IDs de documentos encontrados en la búsqueda.")
    document_names = fields.Text(string='Nombres de Documentos',
                                  help="Nombres de documentos encontrados en la búsqueda.")

    def search_documents(self):
        if not self.search_term:
            self.document_ids = 'Debe ingresar palabras clave para buscar.'
            self.document_names = 'Debe ingresar palabras clave para buscar.'
            return False

        # Separar las palabras clave
        search_keywords = [keyword.strip().lower() for keyword in self.search_term.split(',')]

        # URL de la API
        url = 'https://03489969833a49108391a9c27b47ccf1.api.mockbin.io/'
        response = requests.get(url)

        if response.status_code == 200:
            try:
                documents = json.loads(response.text)

                if isinstance(documents, list):
                    print("Respuesta JSON es una lista de documentos")
                else:
                    print("Respuesta JSON no es una lista de documentos")
                    self.document_ids = 'Error: Respuesta JSON no es una lista'
                    self.document_names = 'Error: Respuesta JSON no es una lista'
                    return False

            except ValueError as e:
                self.document_ids = f'Error al parsear JSON: {e}'
                self.document_names = f'Error al parsear JSON: {e}'
                return False

            matching_ids = []

            # Recorre todos los documentos y busca las palabras clave
            for document in documents:
                content = document.get('contenido', '').lower()

                # Verificar si alguna de las palabras clave está en el contenido
                matching_keywords = [keyword for keyword in search_keywords if keyword in content]

                # Si se encuentra solo una palabra clave
                if len(matching_keywords) == 1:
                    matching_ids.append(document.get('ID', ''))

                # Verificar si todas las palabras clave están en el contenido
                elif len(matching_keywords) == len(search_keywords):
                    matching_ids.append(document.get('ID', ''))

            # Eliminar duplicados
            matching_ids = list(set(matching_ids))

            if matching_ids:
                self.document_ids = ', '.join(matching_ids)  # Asignar IDs encontrados
            else:
                self.document_ids = 'No se encontraron documentos que coincidan'

            # Consultar los registros del modelo `drive` usando los IDs encontrados
            if matching_ids:
                drive_records = self.env['drive'].search([('file_id', 'in', matching_ids)])
                document_names = [record.name for record in drive_records]

                if document_names:
                    self.document_names = ', '.join(document_names)  # Asignar los nombres de los registros
                else:
                    self.document_names = 'No se encontraron registros en el modelo Drive'
            else:
                self.document_names = 'No se encontraron registros en el modelo Drive'

            return True
        else:
            self.document_ids = 'Error al consultar la API'
            self.document_names = 'Error al consultar la API'
            return False

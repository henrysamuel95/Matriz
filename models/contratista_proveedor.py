# models.py
import base64
import os
import re
import tempfile
import threading
from email.policy import default
from tokenize import String
import time
from odoo import models, fields, api, _
from datetime import datetime
import mimetypes
from odoo.exceptions import UserError  # type: ignore
from odoo.exceptions import ValidationError
import uuid
from .drive import GoogleDriveAuth
import logging

_logger = logging.getLogger(__name__)



class ContratistaProveedor(models.Model):
    _name = 'contratista_proveedor'
    _description = 'Contratistas y proveedores'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'  # Campo usado por defecto para las búsquedas y visualización

        # 🔹 Estado de conexión
    estado_usuario = fields.Selection([
        ('no_conectado', 'No conectado hasta ahora'),
        ('confirmado', 'Confirmado')
    ], string="Estado", default="no_conectado", tracking=True, related='contratistas_ids.estado')

    name = fields.Char(string='Nombre o Razón Social', required=False, tracking=True)
    info_ids = fields.One2many('info_contrataciones', 'info_id',
                               string='Tabla')
    user_id = fields.Many2one(
        'res.users',
        string='Usuario relacionado',
        default=lambda self: self.env.user
    )

    email_contratante = fields.Char(string='Correo del Contratista')
    
    asistentes_coordinador_ids = fields.Many2many('res.users', string='Asistentes Coordinador')   
    correos_asistentes = fields.Text(string="Correos de Asistentes Coordinador", help="Lista JSON con los correos de los asistentes.")
    
    contratistas_ids = fields.One2many('login', 'contratistas', string='Contratistas Relacionados')

    @api.constrains('cedula', 'clase_persona')
    def _check_cedula(self):
        for record in self:
            if record.clase_persona == 'persona_natural' and record.cedula == 0:
                raise ValidationError("La cédula no puede ser 0 cuando el tipo de persona es 'Persona natural'.")

    # Nuevo campo de texto para búsqueda
    cedula_texto = fields.Char(string='Cédula (Texto)', compute='_compute_cedula_texto', store=True, index=True)

    @api.depends('cedula')
    def _compute_cedula_texto(self):
        for record in self:
            record.cedula_texto = str(record.cedula) if record.cedula else ''

    clase_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('seleccion_manual', 'Seleccione'),  # Asegúrate de que este valor esté correctamente definido
    ], string='Tipo', required=False, tracking=True)

    identificaciones_existentes = fields.Text(string="Identificaciones Existentes", readonly=True)

    def obtener_identificaciones(self):
        """Obtiene todas las cédulas y NIT en una lista y las almacena en un campo de texto."""
        registros = self.env['contratista_proveedor'].search([])
        lista_identificaciones = [
            str(r.cedula) if r.cedula else str(r.nit) for r in registros if r.nit or r.cedula
        ]
        self.identificaciones_existentes = ", ".join(lista_identificaciones)


    @api.onchange('cedula', 'nit')
    def _check_unique_identification(self):
        """Verifica si la cédula o el NIT ya existen en la lista almacenada"""
        registros = self.env['contratista_proveedor'].search([])

        for record in self:
            record_id = record._origin.id if record._origin and record._origin.id else record.id
            cedula_str = self._normalize_document_for_match(record.cedula)
            nit_str = self._normalize_document_for_match(record.nit)

            cedula_existente = registros.filtered(
                lambda r: self._normalize_document_for_match(r.cedula) == cedula_str
                and r.id != record_id
            )[:1]
            if cedula_str and cedula_existente:
                raise ValidationError(f"El contratista con cédula {record.cedula} ya existe.")

            nit_existente = registros.filtered(
                lambda r: self._normalize_document_for_match(r.nit) == nit_str
                and r.id != record_id
            )[:1]
            if nit_str and nit_existente:
                raise ValidationError(f"El contratista con NIT {record.nit} ya existe.")

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        args = args or []
        domain = []

        context = self._context
        if name:
            if context.get('select_persona') == 'persona_natural':
                domain = ['|',
                          ('name', operator, name),
                          ('cedula', operator, name)
                          ]
            elif context.get('select_persona') == 'persona_juridica':
                domain = ['|',
                          ('name', operator, name),
                          ('nit', operator, name)
                          ]
            else:
                domain = ['|', '|',
                          ('name', operator, name),
                          ('nit', operator, name),
                          ('cedula', operator, name)
                          ]

        records = self.search(domain + args, limit=limit)
        normalized_name = self._normalize_document_for_match(name)
        if normalized_name and len(records) < limit:
            remaining = limit - len(records)
            extra_records = self.search(args).filtered(
                lambda r: (
                    self._normalize_document_for_match(r.cedula) == normalized_name
                    or self._normalize_document_for_match(r.nit) == normalized_name
                )
            )
            records |= extra_records.filtered(lambda r: r.id not in records.ids)[:remaining]
        return records.name_get()

    def name_get(self):
        result = []
        for record in self:
            display_name = record.name or ''
            if self._context.get('select_persona') == 'persona_juridica' and record.nit:
                display_name += f' [NIT: {record.nit}]'
            elif self._context.get('select_persona') == 'persona_natural' and record.cedula:
                display_name += f' [DI: {record.cedula}]'
            elif not self._context.get('select_persona'):
                if record.nit:
                    display_name += f' [NIT: {record.nit}]'
                if record.cedula:
                    display_name += f' [DI: {record.cedula}]'

            result.append((record.id, display_name or "Sin Identificación"))
        return result

    contratacion_fuv_ids = fields.One2many(
        'contrataciones_fuv',
        'contratista_proveedor_relacionado',
        string='Contrataciones Relacionadas'
    )

    drive_stage_provedor_id = fields.Many2one('drive_stage_provedor', string='Drive Stage Provedor')

    def action_create_drive_folder(self):
        """
        Función para crear la carpeta en Google Drive manualmente con un botón.
        """
        self.ensure_one()  # Asegura que solo se llama en un único registro

        if self.drive_stage_provedor_id:
            raise UserError(_("Ya existe una carpeta en Google Drive para este contratista."))

        drive_stage_model = self.env['drive_stage_provedor']

        # Obtener autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Verificar si existe la carpeta "Contratistas"
        contratistas_folder_id = None
        try:
            query = "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and title='Contratistas'"
            folders = drive.ListFile({'q': query}).GetList()
            if folders:
                contratistas_folder_id = folders[0]['id']
            else:
                # Crear la carpeta "Contratistas" si no existe
                contratistas_folder_metadata = {
                    'title': 'Contratistas',
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': 'root'}]
                }
                contratistas_folder = drive.CreateFile(contratistas_folder_metadata)
                contratistas_folder.Upload()
                contratistas_folder_id = contratistas_folder['id']
        except Exception as e:
            _logger.error(f"Error al verificar o crear la carpeta 'Contratistas': {e}")
            raise UserError(_("No se pudo verificar o crear la carpeta 'Contratistas' en Google Drive."))

        # Crear la carpeta específica del contratista dentro de "Contratistas"
        folder_metadata = {
            'title': f"{self.name}",
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': contratistas_folder_id}]
        }

        try:
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()

            # Crear el registro en 'drive_stage_provedor'
            drive_stage_record = drive_stage_model.create({
                'contratistas_provedores': self.id,
                'folder_name': f"{self.name}",
                'folder_id': folder['id'],
                'folder_guardar_id': contratistas_folder_id
            })

            # Asignar la relación Many2one en contratista_proveedor
            self.drive_stage_provedor_id = drive_stage_record.id

            _logger.info(f"Se ha creado la carpeta en Google Drive para el contratista {self.name}")
            return {
                'effect': {
                    'fadeout': 'slow',
                    'message': _("Se creó la carpeta en Google Drive."),
                    'type': 'rainbow_man',
                }
            }
        except Exception as e:
            _logger.error(f"Error creando la carpeta en Google Drive para {self.name}: {e}")
            raise UserError(_("No se pudo crear la carpeta en Google Drive para el contratista."))

    nit = fields.Char(string='NIT', required=False, tracking=True)

    
    cedula_old = fields.Integer(string='DI', required=False, tracking=True, default='')
    cedula = fields.Char(string='DI', required=False, tracking=True, default='')


    @api.onchange('cedula')
    def _onchange_cedula(self):
        if self.cedula:
            cedula_normalizada = self._normalize_document_for_match(self.cedula)
            if not cedula_normalizada:
                return
            registros = self.env['contratista_proveedor'].search([])
            if self.id and isinstance(self.id, int):
                registros = registros.filtered(lambda r: r.id != self.id)

            existente = registros.filtered(
                lambda r: self._normalize_document_for_match(r.cedula) == cedula_normalizada
            )[:1]
            if existente:
                self.cedula = False
                return {
                    'warning': {
                        'title': 'Cédula duplicada',
                        'message': 'Ya existe un registro con esta cédula.'
                    }
                }

    @api.model
    def create(self, vals):
        if (
            not self.env.context.get('creacion_automatica_contratista_externo')
            and vals.get('clase_persona') not in ('persona_natural', 'persona_juridica')
        ):
            raise ValidationError("Debe seleccionar Persona natural o Persona jurídica.")

        record = super(ContratistaProveedor, self).create(vals)

        try:
            self.env['login'].create({
                'name': record.razon_social or record.name,
                'contratistas': record.id,
                'coreo': record.correo_facturacion,
                'documento_de_identidad': record.nit or record.cedula,
                'clase_persona': record.clase_persona,

            })
            # Sincronizar el nuevo registro con drive_stage_provedor
            self._sync_with_drive_stage_provedor(record)
        except Exception as e:
            _logger.error(f"Error al crear login: {e}")
            raise

        return record


    @api.model
    def _sync_with_drive_stage_provedor(self, contratista):
        """
        Crea un registro en drive_stage_provedor y una carpeta en Google Drive
        para el contratista proporcionado, si no existe.
        """
        drive_stage_model = self.env['drive_stage_provedor']

        # Obtener autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Verificar si existe la carpeta "Contratistas"
        contratistas_folder_id = None
        try:
            query = "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and title='Contratistas'"
            folders = drive.ListFile({'q': query}).GetList()
            if folders:
                contratistas_folder_id = folders[0]['id']
            else:
                # Crear la carpeta "Contratistas" si no existe
                contratistas_folder_metadata = {
                    'title': 'Contratistas',
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': 'root'}]
                }
                contratistas_folder = drive.CreateFile(contratistas_folder_metadata)
                contratistas_folder.Upload()
                contratistas_folder_id = contratistas_folder['id']
        except Exception as e:
            _logger.error(f"Error al verificar o crear la carpeta 'Contratistas': {e}")
            raise UserError(_("No se pudo verificar o crear la carpeta 'Contratistas' en Google Drive."))

        # Crear la carpeta específica del contratista dentro de "Contratistas"
        folder_metadata = {
            'title': f"{contratista.name}",  # Asigna un nombre único para la carpeta
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': contratistas_folder_id}]
        }

        try:
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()

            # Crear el registro en 'drive_stage_provedor'
            drive_stage_record = drive_stage_model.create({
                'contratistas_provedores': contratista.id,
                'folder_name': f"{contratista.name}",  # Nombre de la carpeta
                'folder_id': folder['id'],  # ID de la carpeta en Google Drive
                'folder_guardar_id': contratistas_folder_id  # ID de la carpeta "Contratistas"
            })

            # Asignar la relación Many2one en contratista_proveedor
            contratista.drive_stage_provedor_id = drive_stage_record.id

            _logger.info(f"Se ha creado la carpeta en Google Drive para el contratista {contratista.name}")
        except Exception as e:
            _logger.error(f"Error creando la carpeta en Google Drive para {contratista.name}: {e}")
            raise UserError(_("No se pudo crear la carpeta en Google Drive para el contratista."))

    fecha_impresion_rut = fields.Date("Fecha de impresión")
    fecha_impresion_ccm = fields.Date("Fecha de impresión")

    description = fields.Text(string="Descripción")

    access_token = fields.Char(string="Access Token", readonly=True)
    #token_used = fields.Boolean(string="Token Used", default=False)

    email_destino = fields.Char(string="Correo Destinatario", help="Correo al que se enviará el formulario", related='correo_facturacion')
    ciudad_oficina_principal = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",)

    def obtener_todos_los_ids(self):
        """Retorna una lista con todos los IDs del modelo contratista_proveedor."""
        ids = self.search([]).ids
        print(ids)
        return ids

    def _get_share_url(self):
        """Genera un enlace público para el registro"""
        if not self.access_token:
            self.access_token = str(uuid.uuid4())

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}/contratista/view/{self.id}?access_token={self.access_token}"

    def _get_html_template(self, email, name):
        """Construye la plantilla HTML del correo"""
        name_part = f" {name}" if name else ""
        return f"""
        <html>
            <body>
                <p>Estimado Proveedor y/o Cliente{name_part},</p>
                <p>Le enviamos el siguiente enlace al formulario: <a href="{self._get_share_url()}">Formulario del Contratista</a></p>
                <p>Para que se sirva completarlo lo más pronto posible con el fin de tener los datos y documentos actualizados como proveedor/cliente de la FUV.</p>
            </body>
        </html>
        """

    def action_send_email(self):
        """Método para enviar el correo con el enlace"""
        self.ensure_one()

        # Verificar que el campo de correo no esté vacío
        if not self.email_destino:
            raise UserError("Por favor, ingrese un correo electrónico en el campo 'Correo Destinatario'.")

        # Crear el contenido HTML del correo
        email_body = self._get_html_template(self.email_destino, self.name)

        # Crear un correo en Odoo
        mail_values = {
            'subject': 'Enlace al formulario del Contratista',
            'body_html': email_body,
            'email_to': self.email_destino,  # Usamos el correo ingresado en el campo
            'email_from': self.env.user.email,  # El correo del usuario actual, o puedes poner uno estático
        }

        # Enviar el correo
        mail_id = self.env['mail.mail'].sudo().create(mail_values)
        mail_id.send()
        return True

    ################# Funciones nuevas #################

    def _get_share_url_update(self):
        """Genera un enlace para actualizar el contratista"""
        if not self.access_token:
            self.access_token = str(uuid.uuid4())

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}/contratista/view_update/{self.id}?access_token={self.access_token}"

    def _get_html_template_update(self, email, name):
        """Construye la plantilla HTML del correo"""
        name_part = f" {name}" if name else ""
        return f"""
        <html>
            <body>
                <p>Estimado Proveedor y/o Cliente{name_part},</p>
                <p>Le enviamos el siguiente enlace al formulario para hacer actualizacion de los datos: <a href="{self._get_share_url_update()}">Formulario del Contratista</a></p>
                <p>Para que se sirva completarlo lo más pronto posible con el fin de tener los datos y documentos actualizados como proveedor/cliente de la FUV.</p>
            </body>
        </html>
        """

    def action_send_email_update(self):
        """Método para enviar el correo con el enlace"""
        self.ensure_one()

        # Verificar que el campo de correo no esté vacío
        if not self.email_destino:
            raise UserError("Por favor, ingrese un correo electrónico en el campo 'Correo Destinatario'.")

        # Crear el contenido HTML del correo
        email_body = self._get_html_template_update(self.email_destino, self.name)

        # Crear un correo en Odoo
        mail_values = {
            'subject': 'Enlace al formulario del Contratista para Actualizacion de datos',
            'body_html': email_body,
            'email_to': self.email_destino,  # Usamos el correo ingresado en el campo
            'email_from': self.env.user.email,  # El correo del usuario actual, o puedes poner uno estático
        }

        # Enviar el correo
        mail_id = self.env['mail.mail'].sudo().create(mail_values)
        mail_id.send()
        return True

    def crear_form_contrataciones(self):
        """
        Crea registros de contratista_proveedor a partir de contrataciones_fuv,
        limpiando y transformando los campos nit y cedula.
        """
        contrataciones = self.env['contrataciones_fuv'].search([], order='create_date asc')
        _logger.info(f"Contrataciones encontradas: {len(contrataciones)}")

        for contratacion in contrataciones:
            try:
                _logger.info(f"Procesando contratación: {contratacion.name}")
                registros_existente = self.search([('contratacion_fuv_ids', '=', contratacion.id)], limit=1)

                if registros_existente:
                    _logger.info(f"Omitiendo creación, registro ya existe para {contratacion.name}")
                    continue

                clase_persona = contratacion.select_persona
                nit_limpio = self._clean_numeric_field(contratacion.nit)
                cedula_limpia = self._clean_numeric_field(contratacion.documento_identidad)

                if not nit_limpio and not cedula_limpia:
                    _logger.warning(f"NIT y Cédula están vacíos para {contratacion.name}, omitiendo registro.")
                    continue

                correo_facturacion = contratacion.correo  # Obteniendo el correo desde contrataciones_fuv

                nuevo_contratista = self.create({
                    'name': contratacion.new_name or contratacion.name,
                    'nit': nit_limpio,
                    'cedula': cedula_limpia,
                    'contratacion_fuv_ids': [(4, contratacion.id)],
                    'clase_persona': clase_persona,
                    'correo_facturacion': correo_facturacion,  # Asignando el correo al nuevo registro
                })

                _logger.info(f"Registro creado: {nuevo_contratista.name} para contratación {contratacion.name}")
                print("Función de crear contratistas y proveedores llamada con éxito")
            except Exception as e:
                _logger.error(f"Error al procesar {contratacion.name}: {str(e)}")
                raise e

    @staticmethod
    def _clean_numeric_field(value):
        """
        Limpia un campo eliminando caracteres no numéricos y lo convierte a entero.
        """
        if not value:
            return None
        try:
            value_limpio = re.sub(r'\D', '', str(value))
            return int(value_limpio) if value_limpio else None
        except ValueError:
            _logger.error(f"Error al convertir el valor '{value}' a entero.")
            return None

    @staticmethod
    def _normalize_document_for_match(value):
        """Normaliza cédulas/NIT solo para comparar; no modifica el dato guardado."""
        return re.sub(r'\D+', '', str(value or ''))

    informacion_tributaria_ids = fields.One2many(
        comodel_name='informacion_tributaria',
        inverse_name='informacion_tributaria_relacionado',
        string='Información Tributaria',
        tracking=True
    )

    departamentos2 = fields.Many2one(
        comodel_name='departamento',
        string="DEPARTAMENTOS",
    )
    municipios2 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",
    )

    #Columna 1
    nombre_municipio = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",
    )

    cdg_activida_ciiu = fields.Char(string="Código actividad CIIU")
    tarifa = fields.Char(string="Tarifa")
    #Columna 2
    nombre_municipio2 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu2 = fields.Char(string="Código actividad CIIU")
    tarifa2 = fields.Char(string="Tarifa")
    #Columna 3
    nombre_municipio3 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu3 = fields.Char(string="Código actividad CIIU")
    tarifa3 = fields.Char(string="Tarifa")
    #Columna 4
    nombre_municipio4 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu4 = fields.Char(string="Código actividad CIIU")
    tarifa4 = fields.Char(string="Tarifa")
    #Columna 5
    nombre_municipio5 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu5 = fields.Char(string="Código actividad CIIU")
    tarifa5 = fields.Char(string="Tarifa")
    #Columna 6
    nombre_municipio6 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu6 = fields.Char(string="Código actividad CIIU")
    tarifa6 = fields.Char(string="Tarifa")
    #Columna 7
    nombre_municipio7 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu7 = fields.Char(string="Código actividad CIIU")
    tarifa7 = fields.Char(string="Tarifa")
    #Columna 8
    nombre_municipio8 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu8 = fields.Char(string="Código actividad CIIU")
    tarifa8 = fields.Char(string="Tarifa")
    #Columna 9
    nombre_municipio9 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu9 = fields.Char(string="Código actividad CIIU")
    tarifa9 = fields.Char(string="Tarifa")
    #Columna 10
    nombre_municipio10 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu10 = fields.Char(string="Código actividad CIIU")
    tarifa10 = fields.Char(string="Tarifa")
    #Columna 11
    nombre_municipio11 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu11 = fields.Char(string="Código actividad CIIU")
    tarifa11 = fields.Char(string="Tarifa")
    #Columna 12
    nombre_municipio12 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu12 = fields.Char(string="Código actividad CIIU")
    tarifa12 = fields.Char(string="Tarifa")
    #Columna 13
    nombre_municipio13 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu13 = fields.Char(string="Código actividad CIIU")
    tarifa13 = fields.Char(string="Tarifa")
    #Columna 14
    nombre_municipio14 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu14 = fields.Char(string="Código actividad CIIU")
    tarifa14 = fields.Char(string="Tarifa")
    #Columna 15
    nombre_municipio15 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",

    )
    cdg_activida_ciiu15 = fields.Char(string="Código actividad CIIU")
    tarifa15 = fields.Char(string="Tarifa")




    razon_social = fields.Char(string='Razón social', required=False, tracking=True)


    nombre_r_legal = fields.Char(string="Representante legal", tracking=True)

    cedula_representante = fields.Integer(string='Cédula del representante legal', tracking=True)
    ciudad_exp_representante = fields.Many2one(
        comodel_name='municipio',
        string="Ciudad de expedición")
    expedicion_contra_j = fields.Date(string='Fecha de expedición', tracking=True)

    expedicion_contra_N = fields.Date(string='Fecha de expedición', tracking=True)


    ciudad_exp_natural = fields.Many2one(
        comodel_name='municipio',
        string="Ciudad de expedición")


    ####################################Sección para documentos P/N#####################################################

    def write(self, vals):
        res = super(ContratistaProveedor, self).write(vals)
        
        # Actualizar el nombre en el modelo relacionado 'login' si el campo 'name' cambia
        if 'name' in vals:
            for record in self:
                if record.contratistas_ids:
                    record.contratistas_ids.write({'name': vals['name']})

        # Actualizar el documento de identidad en el modelo relacionado 'login' si los campos 'cedula' o 'nit' cambian
        if 'cedula' in vals or 'nit' in vals:
            for record in self:
                if record.contratistas_ids:
                    # Determinar cuál campo usar para actualizar 'documento_de_identidad'
                    documento = vals.get('cedula') or vals.get('nit') or record.cedula or record.nit
                    record.contratistas_ids.write({'documento_de_identidad': documento})

        # Actualizar el correo en el modelo relacionado 'login' si el campo 'correo_facturacion' cambia
        if 'correo_facturacion' in vals:
            for record in self:
                if record.contratistas_ids:
                    record.contratistas_ids.write({'coreo': vals['correo_facturacion']})
        
        if 'documento_identidad' in vals:
            self._onchange_documento_identidad()
        if 'rut_actualizado' in vals:
            self._onchange_rut_actualizado()
        if 'hoja_vida' in vals:
            self._onchange_hoja_vida()
        if 'certificado_estudio' in vals:
            self._onchange_certificado_estudio()
        if 'certificado_laborales' in vals:
            self._onchange_certificado_laborales()
        if 'cert_arl' in vals:
            self._onchange_cert_arl()
        if 'cert_afiliacion_salud' in vals:
            self._onchange_cert_afiliacion_salud()
        if 'cert_afiliacion_pension' in vals:
            self._onchange_cert_afiliacion_pension()
        if 'cert_examen_preocupacional' in vals:
            self._onchange_cert_examen_preocupacional()
        if 'resolucion_facturacion_electronica' in vals:
            self._onchange_resolucion_facturacion_electronica()
        if 'cert_contraloria' in vals:
            self._onchange_cert_contraloria()
        if 'cert_procuraduria' in vals:
            self._onchange_cert_procuraduria()
        if 'cert_policia_judicial' in vals:
            self._onchange_cert_policia_judicial()
        if 'cert_medidas_cautelares' in vals:
            self._upload_document_cert_medidas_cautelares_to_drive()
        if 'cert_inhabilidades' in vals:
            self._onchange_cert_inhabilidades()
        if 'cert_libreta_militar' in vals:
            self._onchange_cert_libreta_militar()
        if 'resolucion_pensionado_vigente' in vals:
            self._onchange_resolucion_pensionado_vigente()
        if 'certificado_bancario_vigente' in vals:
            self._onchange_certificado_bancario_vigente()
        if 'tarjeta_profesional' in vals:
            self._onchange_tarjeta_profesional()
        if 'declaracion_juramentada' in vals:
            self._onchange_declaracion_juramentada()
        # -------------------Sección para documentos P/J-------------------------
        if 'propuesta_j' in vals:
            self._onchange_propuesta_j()
        if 'camara_c_actualizada_j' in vals:
            self._onchange_camara_c_actualizada_j()
        if 'rut_actualizado_j' in vals:
            self._onchange_rut_actualizado_j()
        if 'rup_j' in vals:
            self._onchange_rup_j()
        if 'hoja_vida_j' in vals:
            self._onchange_hoja_vida_j()
        if 'cert_contraloria_j' in vals:
            self._onchange_cert_contraloria_j()
        if 'cert_procuraduria_j' in vals:
            self._onchange_cert_procuraduria_j()
        if 'cert_medidas_cautelares_j' in vals:
            self._onchange_cert_medidas_cautelares_j()
        if 'cert_inhabilidades_j' in vals:
            self._onchange_cert_inhabilidades_j()
        if 'resolucion_fact_elect_j' in vals:
            self._onchange_resolucion_fact_elect_j()
        if 'experiencia_laboral_j' in vals:
            self._onchange_experiencia_laboral_j()
        if 'cert_pago_paraf_j' in vals:
            self._onchange_cert_pago_paraf_j()
        if 'planilla_seguridad_social_j' in vals:
            self._onchange_planilla_seguridad_social_j()
        if 'cert_junta_contadores_j' in vals:
            self._onchange_cert_junta_contadores_j()
        if 'cedula_revisor_fiscal_contador_j' in vals:
            self._onchange_cedula_revisor_fiscal_contador_j()
        if 'documento_de_identidad_contador_j' in vals:
            self._onchange_documento_de_identidad_contador_j()
        if 'Tarjeta_profesional_revisor_fiscal_j' in vals:
            self._onchange_Tarjeta_profesional_revisor_fiscal_j()
        if 'Tarjeta_profesional_contador_j' in vals:
            self._onchange_Tarjeta_profesional_contador_j()
        if 'certificado_bancario_vigente_j' in vals:
            self._onchange_certificado_bancario_vigente_j()
        if 'cedula_representante_legal' in vals:
            self._onchange_cedula_representante_legal()
        if 'hoja_v_representante_legal' in vals:
            self._onchange_hoja_v_representante_legal()
        if 'cert_contraloria_repre_legal' in vals:
            self._onchange_cert_contraloria_repre_legal()
        if 'cert_procuraduria_repre_legal' in vals:
            self._onchange_cert_procuraduria_repre_legal()
        if 'estados_financieros2' in vals:
            self._onchange_estados_financieros2()
        return res

    def unlink(self):
        # Iterar por cada registro que se va a eliminar
        for record in self:
            # Verificar si existe un archivo de Drive vinculado
            if record.drive_stage_provedor_id:
                try:
                    # Intentar eliminar el archivo vinculado en Google Drive
                    record.drive_stage_provedor_id.unlink()
                except Exception as e:
                    raise UserError(f"No se pudo eliminar la carpeta de Google Drive: {str(e)}")
        # Llamar al método original para eliminar el registro de Odoo
        return super(ContratistaProveedor, self).unlink()

    @api.onchange('name')
    def _onchange_name(self):
        """Cuando se cambia el name, actualiza el folder_name en drive.file y Google Drive."""
        if self.name and self.drive_stage_provedor_id:
            # Actualiza el folder_name en el modelo drive.file
            self.drive_stage_provedor_id.write({'folder_name': self.name})

            # Actualiza el nombre en Google Drive
            self._update_folder_name_in_drive(self.drive_stage_provedor_id.folder_id, self.name)

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

    def buscar_carpeta_en_segundo_plano(self):
        logging.info("Iniciando en segundo plano.")
        thread = threading.Thread(target=self._thread_buscar_carpeta)
        thread.daemon = True
        thread.start()

    def _thread_buscar_carpeta(self):
        _logger.info("Hilo iniciado.")
        new_cr = None
        try:
            new_cr = self.env.registry.cursor()  # Crear un nuevo cursor
            env = api.Environment(new_cr, self.env.uid, self.env.context)
            self_with_new_env = self.with_env(env)

            # Medir el tiempo de búsqueda
            start_time = time.time()
            self_with_new_env._onchange_subir_todos()  # Ejecutar la búsqueda de carpeta
            _logger.info(f"finalizada en {time.time() - start_time:.2f} segundos.")

            new_cr.commit()  # Confirmar los cambios en la base de datos
        except Exception as e:
            _logger.error(f"Error en el hilo de búsqueda de carpetas: {str(e)}")
            if new_cr:
                new_cr.rollback()  # Rollback en caso de error
        finally:
            if new_cr:  # Verifica si el cursor fue creado antes de intentar cerrarlo
                new_cr.close()  # Cerrar el cursor solo si fue creado
                print('Cerro el hilo')

    def _onchange_subir_todos(self):
        if self.clase_persona == 'persona_natural':
            self._onchange_documento_identidad()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_rut_actualizado()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_hoja_vida()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_certificado_estudio()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_certificado_laborales()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_arl()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_afiliacion_salud()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_afiliacion_pension()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_examen_preocupacional()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_resolucion_facturacion_electronica()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_contraloria()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_procuraduria()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_policia_judicial()
            self.env.cr.commit()
            time.sleep(0.5)

            self._upload_document_cert_medidas_cautelares_to_drive()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_inhabilidades()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_libreta_militar()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_resolucion_pensionado_vigente()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_certificado_bancario_vigente()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_declaracion_juramentada()
            self.env.cr.commit()

        elif self.clase_persona == 'persona_juridica':
            self._onchange_propuesta_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_camara_c_actualizada_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_rut_actualizado_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_rup_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_hoja_vida_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_contraloria_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_procuraduria_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_medidas_cautelares_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_inhabilidades_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_resolucion_fact_elect_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_experiencia_laboral_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_pago_paraf_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_planilla_seguridad_social_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_Tarjeta_profesional_contador_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_Tarjeta_profesional_revisor_fiscal_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_junta_contadores_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cedula_revisor_fiscal_contador_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_certificado_bancario_vigente_j()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cedula_representante_legal()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_hoja_v_representante_legal()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_contraloria_repre_legal()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_cert_procuraduria_repre_legal()
            self.env.cr.commit()
            time.sleep(0.5)

            self._onchange_estados_financieros2()
            self.env.cr.commit()
            



##################################################################################################################################
    def assign_docs_all(self):
        if self.clase_persona == 'persona_natural':
            self.assign_documents_part1()
            self.assign_documents_part2()
            self.assign_documents_part3()
        elif self.clase_persona == 'persona_juridica':
            self.assign_documents_persona_juridica_part1()
            self.assign_documents_persona_juridica_part2()
            self.assign_documents_persona_juridica_part3()

    def assign_documents_persona_juridica_part3(self):
        _logger.info(f"Asignando documentos para drive_stage_provedor_id: {self.drive_stage_provedor_id.id}")

        # Mapeo de nombres de documentos a atributos del modelo
        doc_mapping = {
            '13_Estados financieros': (
                'estados_financieros2_id', 'estados_financieros2_visible'),
        }

        # Buscar documentos en la base de datos
        documentos = self.env['drive'].search_read(
            [('stage6_id', '=', self.drive_stage_provedor_id.id)], ['id', 'file_name']
        )

        for doc in documentos:
            for key, (field_id, field_visible) in doc_mapping.items():
                if key in doc['file_name']:
                    setattr(self, field_id, doc['id'])
                    setattr(self, field_visible, False)
                    _logger.info(f"{key} asignado: {doc['file_name']}")
                    break  # Salir del bucle una vez encontrado el documento correcto

    def assign_documents_persona_juridica_part1(self):
        _logger.info(f"Asignando documentos para drive_stage_provedor_id: {self.drive_stage_provedor_id.id}")

        # Definir el mapeo de nombres a atributos del modelo
        doc_mapping = {
            '1_Propuesta_': ('propuesta_j_id', 'propuesta_j_visible'),
            '2_Cámara de comercio': ('camara_c_actualizada_j_id', 'camara_c_actualizada_j_visible'),
            '3_RUT': ('rut_actualizado_j_id', 'rut_actualizado_j_visible'),
            '4_RUP': ('rup_j_id', 'rup_j_visible'),
            '5_Hoja de vida jurídica': ('hoja_vida_j_id', 'hoja_vida_j_visible'),
            '6_Certificado contraloría': ('cert_contraloria_j_id', 'cert_contraloria_j_visible'),
            '7_Certificado procuraduría': ('cert_procuraduria_j_id', 'cert_procuraduria_j_visible'),
            '8_Cédula representante legal': ('cedula_representante_legal_id', 'cedula_representante_legal_visible'),
            '9_Hoja de vida representante legal': ('hoja_v_representante_legal_id', 'hoja_v_representante_legal_visible'),
            '10_Cert_contraloría representante legal': (
                'cert_contraloria_repre_legal_id', 'cert_contraloria_repre_legal_visible'),
        }

        # Buscar documentos en la base de datos
        documentos = self.env['drive'].search_read(
            [('stage6_id', '=', self.drive_stage_provedor_id.id)], ['id', 'file_name']
        )

        for doc in documentos:
            for key, (field_id, field_visible) in doc_mapping.items():
                if key in doc['file_name']:
                    setattr(self, field_id, doc['id'])
                    setattr(self, field_visible, False)
                    _logger.info(f"{key} asignado: {doc['file_name']}")
                    break  # Salir del bucle una vez encontrado el documento correcto

    def assign_documents_persona_juridica_part2(self):
        _logger.info(f"Asignando documentos para drive_stage_provedor_id: {self.drive_stage_provedor_id.id}")

        # Mapeo de nombres de documentos a atributos del modelo
        doc_mapping = {
            '14_Cert_policía judicial_medidas cautelares': (
                'cert_medidas_cautelares_j_id', 'cert_medidas_cautelares_j_visible'),
            '11_Cert_procuraduría representante legal': (
                'cert_procuraduria_repre_legal_id', 'cert_procuraduria_repre_legal_visible'),
            '15_Certificado inhabilidades': ('cert_inhabilidades_j_id', 'cert_inhabilidades_j_visible'),
            '16_Resolución de facturación electrónica': (
                'resolucion_fact_elect_j_id', 'resolucion_fact_elect_j_visible'),
            '17_Experiencia laboral': ('experiencia_laboral_j_id', 'experiencia_laboral_j_visible'),
            '18_Certificado pago parafiscales': (
                'cert_pago_paraf_j_id', 'cert_pago_paraf_j_visible'),
            '19_Planilla de seguridad social': (
                'planilla_seguridad_social_j_id', 'planilla_seguridad_social_j_visible'),
            '20_Certificado junta de contadores': (
                'cert_junta_contadores_j_id', 'cert_junta_contadores_j_visible'),
            '21_Cédula revisor fiscal_y_o_contador': (
                'cedula_revisor_fiscal_contador_j_id', 'cedula_revisor_fiscal_contador_j_visible'),
            '22_Certificado bancario': ('certificado_bancario_vigente_j_id', 'certificado_bancario_vigente_j_visible'),
        }

        # Buscar documentos en la base de datos
        documentos = self.env['drive'].search_read(
            [('stage6_id', '=', self.drive_stage_provedor_id.id)], ['id', 'file_name']
        )

        for doc in documentos:
            for key, (field_id, field_visible) in doc_mapping.items():
                if key in doc['file_name']:
                    setattr(self, field_id, doc['id'])
                    setattr(self, field_visible, False)
                    _logger.info(f"{key} asignado: {doc['file_name']}")
                    break  # Salir del bucle una vez encontrado el documento correcto

    def assign_documents_part3(self):
        documentos = self.env['drive'].search_read(
            [('stage6_id', '=', self.drive_stage_provedor_id.id)], ['id', 'file_name']
        )

        document_map = {
            '22_Declaración juramentada': ('declaracion_juramentada_id', 'declaracion_juramentada_visible'),
            '9_Cert_examen preocupacional': (
                'cert_examen_preocupacional_id', 'cert_examen_preocupacional_visible'),
        }

        for doc in documentos:
            for key, (field_id, field_visible) in document_map.items():
                if key in doc['file_name']:
                    setattr(self, field_id, doc['id'])
                    setattr(self, field_visible, False)
                    _logger.info(f"{key} asignado: {doc['file_name']}")
                    break

    def assign_documents_part1(self):
        _logger.info(f"Asignando documentos para drive_stage_provedor_id: {self.drive_stage_provedor_id.id}")

        # Mapeo de nombres de documentos a atributos del modelo
        doc_mapping = {
            '1_Documento de identidad': ('documento_identidad_c_id', 'documento_identidad_visible'),
            '2_RUT': ('rut_actualizado_id', 'rut_actualizado_visible'),
            '3_Hoja de vida': ('hoja_vida_id', 'hoja_vida_visible'),
            '4_Certificado estudio': ('certificado_estudio_id', 'certificado_estudio_visible'),
            '5_Certificado laborales': ('certificado_laborales_id', 'certificado_laborales_visible'),
            '6_Cert_ARL o formato ARL FUV': ('cert_arl_id', 'cert_arl_visible'),
            '7_Cert_afiliación salud': ('cert_afiliacion_salud_id', 'cert_afiliacion_salud_visible'),
            '8_Cert_afiliación a pensión': ('cert_afiliacion_pension_id', 'cert_afiliacion_pension_visible'),
        }

        # Buscar documentos en la base de datos
        documentos = self.env['drive'].search_read(
            [('stage6_id', '=', self.drive_stage_provedor_id.id)], ['id', 'file_name']
        )

        for doc in documentos:
            for key, (field_id, field_visible) in doc_mapping.items():
                if key in doc['file_name']:
                    setattr(self, field_id, doc['id'])
                    setattr(self, field_visible, False)
                    _logger.info(f"{key} asignado: {doc['file_name']}")
                    break  # Salir del bucle una vez encontrado el documento correcto

    def assign_documents_part2(self):
        documentos = self.env['drive'].search_read(
            [('stage6_id', '=', self.drive_stage_provedor_id.id)], ['id', 'file_name']
        )

        document_map = {
            '11_Resolución de facturación electrónica': (
                'resolucion_facturacion_electronica_id', 'resolucion_facturacion_electronica_visible'),
            '13_Cert_contraloría': ('cert_contraloria_id', 'cert_contraloria_visible'),
            '14_Cert_procuraduría': ('cert_procuraduria_id', 'cert_procuraduria_visible'),
            '15_Cert_policía judicial': (
                'cert_policia_judicial_id', 'cert_policia_judicial_visible'),
            '16_Cert_medidas correctivas': (
                'cert_medidas_cautelares_id', 'cert_medidas_cautelares_visible'),
            '17_Cert_inhabilidades': ('cert_inhabilidades_id', 'cert_inhabilidades_visible'),
            '18_Cert_libreta militar': (
                'cert_libreta_militar_id', 'cert_libreta_militar_visible'),
            '19_Resolución de pensionado vigente': ('resolucion_pensionado_vigente_id', 'resolucion_pensionado_vigente_visible'),
            '20_Certificado bancario': ('certificado_bancario_vigente_id', 'certificado_bancario_vigente_visible'),
        }

        for doc in documentos:
            for key, (field_id, field_visible) in document_map.items():
                if key in doc['file_name']:
                    setattr(self, field_id, doc['id'])
                    setattr(self, field_visible, False)
                    _logger.info(f"{key} asignado: {doc['file_name']}")
                    break


####################################################################################################################################

    contrataciones_ids = fields.One2many('drive_stage_provedor', 'contratistas_provedores', string='Contrataciones')

    documento_identidad = fields.Binary(string="Documento de identidad")
    documento_identidad_name = fields.Char(string="Documento de identidad")
    documento_identidad_c_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    documento_identidad_visible = fields.Boolean(string="Documento de identidad visible", default=True, store=True)


    def action_open_documento_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.documento_identidad_c_id.id,
            'target': 'new',
        }

    def _onchange_documento_identidad(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.documento_identidad and self.documento_identidad_name:
            self._upload_document_to_drive()

    def _upload_document_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.documento_identidad_name)[-1] or ".bin"
            fixed_file_name = f"1_Documento de identidad_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.documento_identidad)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.documento_identidad_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '1_Documento de identidad_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'documento_identidad_c_id': drive_record.id,
                'documento_identidad_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    rut_actualizado = fields.Binary(string="RUT (Actualizado)")
    rut_actualizado_name = fields.Char(string="RUT (Actualizado)")

    rut_actualizado_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    rut_actualizado_visible = fields.Boolean(string="Documento de identidad visible", default=True, store=True)

    def action_open_rut_actualizado_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT (Actualizado)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_actualizado_id.id,
            'target': 'new',
        }

    def _onchange_rut_actualizado(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.rut_actualizado and self.rut_actualizado_name:
            self._upload_document_rut_to_drive()

    def _upload_document_rut_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.rut_actualizado_name)[-1] or ".bin"
            fixed_file_name = f"2_RUT_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.rut_actualizado)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.rut_actualizado_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '2_RUT_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'rut_actualizado_id': drive_record.id,
                'rut_actualizado_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True



    hoja_vida = fields.Binary(string="Hoja de vida")
    hoja_vida_name = fields.Char(string="Hoja de vida")

    hoja_vida_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    hoja_vida_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_hoja_vida_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Hoja de vida',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.hoja_vida_id.id,
            'target': 'new',
        }

    def _onchange_hoja_vida(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.hoja_vida and self.hoja_vida_name:
            self._upload_document_hoja_vida_to_drive()

    def _upload_document_hoja_vida_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.hoja_vida_name)[-1] or ".bin"
            fixed_file_name = f"3_Hoja de vida_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.hoja_vida)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.hoja_vida_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '3_Hoja de vida_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'hoja_vida_id': drive_record.id,
                'hoja_vida_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    

    certificado_estudio = fields.Binary(string="Certificado de estudio")
    certificado_estudio_name = fields.Char(string="Certificado de estudio")

    certificado_estudio_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    certificado_estudio_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_certificado_estudio_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado de estudio',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_estudio_id.id,
            'target': 'new',
        }

    def _onchange_certificado_estudio(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.certificado_estudio and self.certificado_estudio_name:
            self._upload_document_certificado_estudio_to_drive()

    def _upload_document_certificado_estudio_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.certificado_estudio_name)[-1] or ".bin"
            fixed_file_name = f"4_Certificado estudio_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.certificado_estudio)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.certificado_estudio_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '4_Certificado estudio_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'certificado_estudio_id': drive_record.id,
                'certificado_estudio_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    certificado_laborales = fields.Binary(string="Certificado laborales")
    certificado_laborales_name = fields.Char(string="Certificado laborales")

    certificado_laborales_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    certificado_laborales_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_certificado_laborales_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado laborales',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_laborales_id.id,
            'target': 'new',
        }

    def _onchange_certificado_laborales(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.certificado_laborales and self.certificado_laborales_name:
            self._upload_document_certificado_laborales_to_drive()

    def _upload_document_certificado_laborales_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.certificado_laborales_name)[-1] or ".bin"
            fixed_file_name = f"5_Certificado laborales_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.certificado_laborales)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.certificado_laborales_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '5_Certificado laborales_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'certificado_laborales_id': drive_record.id,
                'certificado_laborales_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True



    cert_arl = fields.Binary(string="Certificado ARL (no mayor a 30 días)")
    cert_arl_name = fields.Char(string="Certificado ARL (no mayor a 30 días)")

    cert_arl_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_arl_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_arl_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado ARL',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_arl_id.id,
            'target': 'new',
        }

    def _onchange_cert_arl(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_arl and self.cert_arl_name:
            self._upload_document_cert_arl_to_drive()
            
    def _upload_document_cert_arl_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_arl_name)[-1] or ".bin"
            fixed_file_name = f"6_Cert_ARL o formato ARL FUV_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_arl)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_arl_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '6_Cert_ARL o formato ARL FUV_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_arl_id': drive_record.id,
                'cert_arl_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True
    

    cert_afiliacion_salud = fields.Binary(string="Certificado afiliación salud (no mayor a 30 días)")
    cert_afiliacion_salud_name = fields.Char(string="Certificado afiliación salud (no mayor a 30 días)")

    cert_afiliacion_salud_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_afiliacion_salud_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_afiliacion_salud_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado afiliación salud',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_afiliacion_salud_id.id,
            'target': 'new',
        }

    def _onchange_cert_afiliacion_salud(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_afiliacion_salud and self.cert_afiliacion_salud_name:
            self._upload_document_cert_afiliacion_salud_to_drive()

    def _upload_document_cert_afiliacion_salud_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_afiliacion_salud_name)[-1] or ".bin"
            fixed_file_name = f"7_Cert_afiliación salud_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_afiliacion_salud)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_afiliacion_salud_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '7_Cert_afiliación salud_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_afiliacion_salud_id': drive_record.id,
                'cert_afiliacion_salud_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    cert_afiliacion_pension = fields.Binary(string="Certificado afiliación pensión (no mayor a 30 días)")
    cert_afiliacion_pension_name = fields.Char(string="Certificado afiliación pensión (no mayor a 30 días)")

    cert_afiliacion_pension_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_afiliacion_pension_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_afiliacion_pension_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado afiliación pensión',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_afiliacion_pension_id.id,
            'target': 'new',
        }

    def _onchange_cert_afiliacion_pension(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_afiliacion_pension and self.cert_afiliacion_pension_name:
            self._upload_document_cert_afiliacion_pension_to_drive()

    def _upload_document_cert_afiliacion_pension_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_afiliacion_pension_name)[-1] or ".bin"
            fixed_file_name = f"8_Cert_afiliación a pensión_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_afiliacion_pension)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_afiliacion_pension_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '8_Cert_afiliación a pensión_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_afiliacion_pension_id': drive_record.id,
                'cert_afiliacion_pension_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    cert_examen_preocupacional = fields.Binary(string="Certificado examen preocupacional (no mayor a 30 días)")
    cert_examen_preocupacional_name = fields.Char(string="Certificado examen preocupacional (no mayor a 30 días)")

    cert_examen_preocupacional_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_examen_preocupacional_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_examen_preocupacional_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado examen preocupacional',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_examen_preocupacional_id.id,
            'target': 'new',
        }

    def _onchange_cert_examen_preocupacional(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_examen_preocupacional and self.cert_examen_preocupacional_name:
            self._upload_document_cert_examen_preocupacional_to_drive()
    
    def _upload_document_cert_examen_preocupacional_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_examen_preocupacional_name)[-1] or ".bin"
            fixed_file_name = f"9_Cert_examen preocupacional_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_examen_preocupacional)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_examen_preocupacional_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '9_Cert_examen preocupacional_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_examen_preocupacional_id': drive_record.id,
                'cert_examen_preocupacional_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    resolucion_facturacion_electronica = fields.Binary(string="Resolución de facturación electrónica")
    resolucion_facturacion_electronica_name = fields.Char(string="Resolución de facturación electrónica")

    resolucion_facturacion_electronica_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    resolucion_facturacion_electronica_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_resolucion_facturacion_electronica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolución de facturación electrónica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.resolucion_facturacion_electronica_id.id,
            'target': 'new',
        }

    def _onchange_resolucion_facturacion_electronica(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.resolucion_facturacion_electronica and self.resolucion_facturacion_electronica_name:
            self._upload_document_resolucion_facturacion_electronica_to_drive()
    
        
    def _upload_document_resolucion_facturacion_electronica_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.resolucion_facturacion_electronica_name)[-1] or ".bin"
            fixed_file_name = f"11_Resolución de facturación electrónica_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.resolucion_facturacion_electronica)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.resolucion_facturacion_electronica_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '11_Resolución de facturación electrónica_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'resolucion_facturacion_electronica_id': drive_record.id,
                'resolucion_facturacion_electronica_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

   
    cert_contraloria = fields.Binary(string="Certificado contraloría (no mayor a 30 días)")
    cert_contraloria_name = fields.Char(string="Certificado contraloría (no mayor a 30 días)")

    cert_contraloria_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_contraloria_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_contraloria_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado contraloría',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_contraloria_id.id,
            'target': 'new',
        }

    def _onchange_cert_contraloria(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_contraloria and self.cert_contraloria_name:
            self._upload_document_cert_contraloria_to_drive()

    def _upload_document_cert_contraloria_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_contraloria_name)[-1] or ".bin"
            fixed_file_name = f"13_Cert_contraloría_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_contraloria)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_contraloria_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '13_Cert_contraloría_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_contraloria_id': drive_record.id,
                'cert_contraloria_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    cert_procuraduria = fields.Binary(string="Certificado procuraduría (no mayor a 30 días)")
    cert_procuraduria_name = fields.Char(string="Certificado procuraduría (no mayor a 30 días)")

    cert_procuraduria_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_procuraduria_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_procuraduria_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado procuraduría',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_procuraduria_id.id,
            'target': 'new',
        }

    def _onchange_cert_procuraduria(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_procuraduria and self.cert_procuraduria_name:
            self._upload_document_cert_procuraduria_to_drive()
    
    def _upload_document_cert_procuraduria_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_procuraduria_name)[-1] or ".bin"
            fixed_file_name = f"14_Cert_procuraduría_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_procuraduria)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_procuraduria_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '14_Cert_procuraduría_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_procuraduria_id': drive_record.id,
                'cert_procuraduria_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True



    cert_policia_judicial = fields.Binary(string="Certificado policía judicial (no mayor a 30 días)")
    cert_policia_judicial_name = fields.Char(string="Certificado policía judicial (no mayor a 30 días)")

    cert_policia_judicial_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_policia_judicial_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_policia_judicial_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado policía judicial',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_policia_judicial_id.id,
            'target': 'new',
        }

    def _onchange_cert_policia_judicial(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_policia_judicial and self.cert_policia_judicial_name:
            self._upload_document_cert_policia_judicial_to_drive()

    def _upload_document_cert_policia_judicial_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_policia_judicial_name)[-1] or ".bin"
            fixed_file_name = f"15_Cert_policía judicial_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_policia_judicial)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_policia_judicial_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '15_Cert_policía judicial_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_policia_judicial_id': drive_record.id,
                'cert_policia_judicial_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True



    cert_medidas_cautelares = fields.Binary(string="Certificado medidas cautelares (no mayor a 30 días)")
    cert_medidas_cautelares_name = fields.Char(string="Certificado medidas cautelares (no mayor a 30 días)")

    cert_medidas_cautelares_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_medidas_cautelares_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_medidas_cautelares_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado medidas cautelares',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_medidas_cautelares_id.id,
            'target': 'new',
        }

    def _onchange_cert_medidas_cautelares(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_medidas_cautelares and self.cert_medidas_cautelares_name:
            self._upload_document_cert_medidas_cautelares_to_drive()

    def _upload_document_cert_medidas_cautelares_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_medidas_cautelares_name)[-1] or ".bin"
            fixed_file_name = f"16_Cert_medidas correctivas_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_medidas_cautelares)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_medidas_cautelares_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '16_Cert_medidas correctivas_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_medidas_cautelares_id': drive_record.id,
                'cert_medidas_cautelares_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    cert_inhabilidades = fields.Binary(string="Certificado inhabilidades (no mayor a 30 días)")
    cert_inhabilidades_name = fields.Char(string="Certificado inhabilidades (no mayor a 30 días)")

    cert_inhabilidades_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_inhabilidades_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_inhabilidades_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado inhabilidades',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_inhabilidades_id.id,
            'target': 'new',
        }

    def _onchange_cert_inhabilidades(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_inhabilidades and self.cert_inhabilidades_name:
            self._upload_document_cert_inhabilidades_to_drive()

    def _upload_document_cert_inhabilidades_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_inhabilidades_name)[-1] or ".bin"
            fixed_file_name = f"17_Cert_inhabilidades_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_inhabilidades)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_inhabilidades_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '17_Cert_inhabilidades_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_inhabilidades_id': drive_record.id,
                'cert_inhabilidades_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True



    cert_libreta_militar = fields.Binary(string="Certificado libreta militar (< 50 años) (si aplica)")
    cert_libreta_militar_name = fields.Char(string="Certificado libreta militar (< 50 años) (si aplica)")

    cert_libreta_militar_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_libreta_militar_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_libreta_militar_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado libreta militar',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_libreta_militar_id.id,
            'target': 'new',
        }

    def _onchange_cert_libreta_militar(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_libreta_militar and self.cert_libreta_militar_name:
            self._upload_document_cert_libreta_militar_to_drive()
    
    def _upload_document_cert_libreta_militar_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_libreta_militar_name)[-1] or ".bin"
            fixed_file_name = f"18_Cert_libreta militar_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_libreta_militar)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_libreta_militar_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '18_Cert_libreta militar_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_libreta_militar_id': drive_record.id,
                'cert_libreta_militar_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    resolucion_pensionado_vigente = fields.Binary(string="Resolución pensionado (si aplica)")
    resolucion_pensionado_vigente_name = fields.Char(string="Resolución pensionado (si aplica)")

    resolucion_pensionado_vigente_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    resolucion_pensionado_vigente_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_resolucion_pensionado_vigente_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolución pensionado',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.resolucion_pensionado_vigente_id.id,
            'target': 'new',
        }

    def _onchange_resolucion_pensionado_vigente(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.resolucion_pensionado_vigente and self.resolucion_pensionado_vigente_name:
            self._upload_document_resolucion_pensionado_vigente_to_drive()
    
    def _upload_document_resolucion_pensionado_vigente_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.resolucion_pensionado_vigente_name)[-1] or ".bin"
            fixed_file_name = f"19_Resolución de pensionado vigente_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.resolucion_pensionado_vigente)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.resolucion_pensionado_vigente_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '19_Resolución de pensionado vigente_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'resolucion_pensionado_vigente_id': drive_record.id,
                'resolucion_pensionado_vigente_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    certificado_bancario_vigente = fields.Binary(string="Certificado bancario (vigente)")
    certificado_bancario_vigente_name = fields.Char(string="Certificado bancario (vigente)")

    certificado_bancario_vigente_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    certificado_bancario_vigente_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_certificado_bancario_vigente_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado bancario',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_bancario_vigente_id.id,
            'target': 'new',
        }

    def _onchange_certificado_bancario_vigente(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.certificado_bancario_vigente and self.certificado_bancario_vigente_name:
            self._upload_document_certificado_bancario_vigente_to_drive()

    def _upload_document_certificado_bancario_vigente_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.certificado_bancario_vigente_name)[-1] or ".bin"
            fixed_file_name = f"20_Certificado bancario_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.certificado_bancario_vigente)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.certificado_bancario_vigente_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '20_Certificado bancario_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'certificado_bancario_vigente_id': drive_record.id,
                'certificado_bancario_vigente_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    tarjeta_profesional = fields.Binary(string="Tarjeta profesional (si aplica)")
    tarjeta_profesional_name = fields.Char(string="Tarjeta profesional (si aplica)")    
    tarjeta_profesional_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    tarjeta_profesional_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_tarjeta_profesional_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarjeta profesional',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.tarjeta_profesional_id.id,
            'target': 'new',
        }
    
    def _onchange_tarjeta_profesional(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.tarjeta_profesional and self.tarjeta_profesional_name:
            self._upload_document_tarjeta_profesional_to_drive()

    def _upload_document_tarjeta_profesional_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.tarjeta_profesional_name)[-1] or ".bin"
            fixed_file_name = f"21_Tarjeta profesional_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.tarjeta_profesional)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.tarjeta_profesional_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '21_Tarjeta profesional_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'tarjeta_profesional_id': drive_record.id,
                'tarjeta_profesional_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True
        

    declaracion_juramentada = fields.Binary(string="Declaración juramentada")
    declaracion_juramentada_name = fields.Char(string="Declaración juramentada")

    declaracion_juramentada_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    declaracion_juramentada_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_declaracion_juramentada_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Declaración juramentada',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.declaracion_juramentada_id.id,
            'target': 'new',
        }

    def _onchange_declaracion_juramentada(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.declaracion_juramentada and self.declaracion_juramentada_name:
            self._upload_document_declaracion_juramentada_to_drive()

    def _upload_document_declaracion_juramentada_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.declaracion_juramentada_name)[-1] or ".bin"
            fixed_file_name = f"22_Declaración juramentada_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.declaracion_juramentada)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.declaracion_juramentada_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '22_Declaración juramentada_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'declaracion_juramentada_id': drive_record.id,
                'declaracion_juramentada_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    ####################################Sección para documentos P/J#####################################################
    propuesta_j = fields.Binary(string="Propuesta")
    propuesta_j_name = fields.Char(string="Propuesta")

    propuesta_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    propuesta_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_propuesta_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Propuesta',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.propuesta_j_id.id,
            'target': 'new',
        }

    def _onchange_propuesta_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.propuesta_j and self.propuesta_j_name:
            self._upload_document_propuesta_j_to_drive()

    def _upload_document_propuesta_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.propuesta_j_name)[-1] or ".bin"
            fixed_file_name = f"1_Propuesta_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.propuesta_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.propuesta_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '1_Propuesta_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'propuesta_j_id': drive_record.id,
                'propuesta_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    camara_c_actualizada_j = fields.Binary(string="Cámara de Comercio (Actualizada)")
    camara_c_actualizada_j_name = fields.Char(string="Cámara de Comercio (Actualizada)")

    camara_c_actualizada_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    camara_c_actualizada_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_camara_c_actualizada_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cámara de Comercio (Actualizada)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.camara_c_actualizada_j_id.id,
            'target': 'new',
        }

    def _onchange_camara_c_actualizada_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.camara_c_actualizada_j and self.camara_c_actualizada_j_name:
            self._upload_document_camara_c_actualizada_j_to_drive()

    def _upload_document_camara_c_actualizada_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.camara_c_actualizada_j_name)[-1] or ".bin"
            fixed_file_name = f"2_Cámara de comercio_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.camara_c_actualizada_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.camara_c_actualizada_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '2_Cámara de comercio_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'camara_c_actualizada_j_id': drive_record.id,
                'camara_c_actualizada_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    rut_actualizado_j = fields.Binary(string="RUT (Actualizado)")
    rut_actualizado_j_name = fields.Char(string="RUT (Actualizado)")

    rut_actualizado_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    rut_actualizado_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_rut_actualizado_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT (Actualizado)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_actualizado_j_id.id,
            'target': 'new',
        }

    def _onchange_rut_actualizado_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.rut_actualizado_j and self.rut_actualizado_j_name:
            self._upload_document_rut_actualizado_j_to_drive()

    def _upload_document_rut_actualizado_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.rut_actualizado_j_name)[-1] or ".bin"
            fixed_file_name = f"3_RUT_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.rut_actualizado_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.rut_actualizado_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '3_RUT_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'rut_actualizado_j_id': drive_record.id,
                'rut_actualizado_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    rup_j = fields.Binary(string="RUP (Si aplica)")
    rup_j_name = fields.Char(string="RUP (Si aplica)")

    rup_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    rup_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_rup_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUP',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rup_j_id.id,
            'target': 'new',
        }

    def _onchange_rup_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.rup_j and self.rup_j_name:
            self._upload_document_rup_j_to_drive()

    def _upload_document_rup_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.rup_j_name)[-1] or ".bin"
            fixed_file_name = f"4_RUP_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.rup_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.rup_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '4_RUP_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'rup_j_id': drive_record.id,
                'rup_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    hoja_vida_j = fields.Binary(string="Hoja de Vida Jurídica (Actualizada)")
    hoja_vida_j_name = fields.Char(string="Hoja de Vida Jurídica (Actualizada)")

    hoja_vida_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    hoja_vida_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_hoja_vida_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Hoja de Vida',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.hoja_vida_j_id.id,
            'target': 'new',
        }

    def _onchange_hoja_vida_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.hoja_vida_j and self.hoja_vida_j_name:
            self._upload_document_hoja_vida_j_to_drive()
    
    def _upload_document_hoja_vida_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.hoja_vida_j_name)[-1] or ".bin"
            fixed_file_name = f"5_Hoja de vida jurídica_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.hoja_vida_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.hoja_vida_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '5_Hoja de vida jurídica_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'hoja_vida_j_id': drive_record.id,
                'hoja_vida_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    cert_contraloria_j = fields.Binary(string="Certificado Contraloría")
    cert_contraloria_j_name = fields.Char(string="Certificado Contraloría")

    cert_contraloria_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_contraloria_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_contraloria_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado Contraloría',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_contraloria_j_id.id,
            'target': 'new',
        }

    def _onchange_cert_contraloria_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_contraloria_j and self.cert_contraloria_j_name:
            self._upload_document_cert_contraloria_j_to_drive()
    
    def _upload_document_cert_contraloria_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_contraloria_j_name)[-1] or ".bin"
            fixed_file_name = f"6_Certificado contraloría_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_contraloria_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_contraloria_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '6_Certificado contraloría_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_contraloria_j_id': drive_record.id,
                'cert_contraloria_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    cert_procuraduria_j = fields.Binary(string="Certificado Procuraduría")
    cert_procuraduria_j_name = fields.Char(string="Certificado Procuraduría")

    cert_procuraduria_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_procuraduria_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_procuraduria_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado Procuraduría',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_procuraduria_j_id.id,
            'target': 'new',
        }

    def _onchange_cert_procuraduria_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_procuraduria_j and self.cert_procuraduria_j_name:
            self._upload_document_cert_procuraduria_j_to_drive()
    
    def _upload_document_cert_procuraduria_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_procuraduria_j_name)[-1] or ".bin"
            fixed_file_name = f"7_Certificado procuraduría_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_procuraduria_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_procuraduria_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '7_Certificado procuraduría_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_procuraduria_j_id': drive_record.id,
                'cert_procuraduria_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    # Estatos financieros faltante

    cert_medidas_cautelares_j = fields.Binary(string="Cert. Policía Judicial, Medidas Cautelares")
    cert_medidas_cautelares_j_name = fields.Char(string="Cert. Policía Judicial, Medidas Cautelares")

    cert_medidas_cautelares_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_medidas_cautelares_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_medidas_cautelares_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cert. Policía Judicial, Medidas Cautelares',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_medidas_cautelares_j_id.id,
            'target': 'new',
        }

    def _onchange_cert_medidas_cautelares_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_medidas_cautelares_j and self.cert_medidas_cautelares_j_name:
            self._upload_document_cert_medidas_cautelares_j_to_drive()

    def _upload_document_cert_medidas_cautelares_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_medidas_cautelares_j_name)[-1] or ".bin"
            fixed_file_name = f"14_Cert_policía judicial_medidas cautelares_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_medidas_cautelares_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_medidas_cautelares_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '14_Cert_policía judicial_medidas cautelares_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_medidas_cautelares_j_id': drive_record.id,
                'cert_medidas_cautelares_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    cert_inhabilidades_j = fields.Binary(string="Certificado Inhabilidades")
    cert_inhabilidades_j_name = fields.Char(string="Certificado Inhabilidades")

    cert_inhabilidades_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_inhabilidades_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_inhabilidades_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado Inhabilidades',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_inhabilidades_j_id.id,
            'target': 'new',
        }

    def _onchange_cert_inhabilidades_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_inhabilidades_j and self.cert_inhabilidades_j_name:
            self._upload_document_cert_inhabilidades_j_to_drive()

    def _upload_document_cert_inhabilidades_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_inhabilidades_j_name)[-1] or ".bin"
            fixed_file_name = f"15_Certificado inhabilidades_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_medidas_cautelares_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_inhabilidades_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '15_Certificado inhabilidades_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_inhabilidades_j_id': drive_record.id,
                'cert_inhabilidades_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True



    resolucion_fact_elect_j = fields.Binary(string="Resolución de Facturación Electrónica")
    resolucion_fact_elect_j_name = fields.Char(string="Resolución de Facturación Electrónica")

    resolucion_fact_elect_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    resolucion_fact_elect_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_resolucion_fact_elect_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolución de Facturación Electrónica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.resolucion_fact_elect_j_id.id,
            'target': 'new',
        }

    def _onchange_resolucion_fact_elect_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.resolucion_fact_elect_j and self.resolucion_fact_elect_j_name:
            self._upload_document_resolucion_fact_elect_j_to_drive()

    def _upload_document_resolucion_fact_elect_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.resolucion_fact_elect_j_name)[-1] or ".bin"
            fixed_file_name = f"16_Resolución de facturación electrónica_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.resolucion_fact_elect_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.resolucion_fact_elect_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '16_Resolución de facturación electrónica_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'resolucion_fact_elect_j_id': drive_record.id,
                'resolucion_fact_elect_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    experiencia_laboral_j = fields.Binary(string="Experiencia Laboral")
    experiencia_laboral_j_name = fields.Char(string="Experiencia Laboral")

    experiencia_laboral_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    experiencia_laboral_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_experiencia_laboral_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Experiencia Laboral',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.experiencia_laboral_j_id.id,
            'target': 'new',
        }

    def _onchange_experiencia_laboral_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.experiencia_laboral_j and self.experiencia_laboral_j_name:
            self._upload_document_experiencia_laboral_j_to_drive()

    def _upload_document_experiencia_laboral_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.experiencia_laboral_j_name)[-1] or ".bin"
            fixed_file_name = f"17_Experiencia laboral_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.resolucion_fact_elect_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.experiencia_laboral_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '17_Experiencia laboral_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'experiencia_laboral_j_id': drive_record.id,
                'experiencia_laboral_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    cert_pago_paraf_j = fields.Binary(string="Certificado Pago Parafiscales")
    cert_pago_paraf_j_name = fields.Char(string="Certificado Pago Parafiscales")

    cert_pago_paraf_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_pago_paraf_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_pago_paraf_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado Pago Parafiscales',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_pago_paraf_j_id.id,
            'target': 'new',
        }

    def _onchange_cert_pago_paraf_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_pago_paraf_j and self.cert_pago_paraf_j_name:
            self._upload_document_cert_pago_paraf_j_to_drive()

    def _upload_document_cert_pago_paraf_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_pago_paraf_j_name)[-1] or ".bin"
            fixed_file_name = f"18_Certificado pago parafiscales_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_pago_paraf_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_pago_paraf_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '18_Certificado pago parafiscales_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_pago_paraf_j_id': drive_record.id,
                'cert_pago_paraf_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    planilla_seguridad_social_j = fields.Binary(string="Planilla de Seguridad Social")
    planilla_seguridad_social_j_name = fields.Char(string="Planilla de Seguridad Social")

    planilla_seguridad_social_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    planilla_seguridad_social_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_planilla_seguridad_social_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planilla de Seguridad Social',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.planilla_seguridad_social_j_id.id,
            'target': 'new',
        }

    def _onchange_planilla_seguridad_social_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.planilla_seguridad_social_j and self.planilla_seguridad_social_j_name:
            self._upload_document_planilla_seguridad_social_j_to_drive()
    
    def _upload_document_planilla_seguridad_social_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.planilla_seguridad_social_j_name)[-1] or ".bin"
            fixed_file_name = f"19_Planilla de seguridad social_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.planilla_seguridad_social_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.planilla_seguridad_social_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '19_Planilla de seguridad social_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'planilla_seguridad_social_j_id': drive_record.id,
                'planilla_seguridad_social_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    cert_junta_contadores_j = fields.Binary(string="Cert. Junta de Contadores (Si aplica)")
    cert_junta_contadores_j_name = fields.Char(string="Cert. Junta de Contadores (Si aplica)")

    cert_junta_contadores_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_junta_contadores_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_junta_contadores_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cert. Junta de Contadores (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_junta_contadores_j_id.id,
            'target': 'new',
        }

    def _onchange_cert_junta_contadores_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_junta_contadores_j and self.cert_junta_contadores_j_name:
            self._upload_document_cert_junta_contadores_j_to_drive()

    def _upload_document_cert_junta_contadores_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_junta_contadores_j_name)[-1] or ".bin"
            fixed_file_name = f"20_Certificado junta de contadores_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_junta_contadores_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_junta_contadores_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '20_Certificado junta de contadores_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_junta_contadores_j_id': drive_record.id,
                'cert_junta_contadores_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    cedula_revisor_fiscal_contador_j = fields.Binary(string="Documento de identidad revisor fiscal (Si aplica)")
    cedula_revisor_fiscal_contador_j_name = fields.Char(string="Documento de identidad revisor fiscal (Si aplica)")

    cedula_revisor_fiscal_contador_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cedula_revisor_fiscal_contador_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cedula_revisor_fiscal_contador_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad revisor fiscal (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_revisor_fiscal_contador_j_id.id,
            'target': 'new',
        }

    def _onchange_cedula_revisor_fiscal_contador_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cedula_revisor_fiscal_contador_j and self.cedula_revisor_fiscal_contador_j_name:
            self._upload_document_cedula_revisor_fiscal_contador_j_to_drive()

    def _upload_document_cedula_revisor_fiscal_contador_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cedula_revisor_fiscal_contador_j_name)[-1] or ".bin"
            fixed_file_name = f"21_Documento de identidad revisor fiscal_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cedula_revisor_fiscal_contador_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cedula_revisor_fiscal_contador_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '21_Documento de identidad revisor fiscal_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cedula_revisor_fiscal_contador_j_id': drive_record.id,
                'cedula_revisor_fiscal_contador_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


####################################################################################################################################################
    documento_de_identidad_contador_j = fields.Binary(string="Documento de identidad contador (Si aplica)")
    documento_de_identidad_contador_j_name = fields.Char(string="Documento de identidad contador (Si aplica)")

    documento_de_identidad_contador_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    documento_de_identidad_contador_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_documento_de_identidad_contador_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad contador (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.documento_de_identidad_contador_j_id.id,
            'target': 'new',
        }

    def _onchange_documento_de_identidad_contador_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.documento_de_identidad_contador_j and self.documento_de_identidad_contador_j_name:
            self._upload_document_documento_de_identidad_contador_j_to_drive()

    def _upload_document_documento_de_identidad_contador_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.documento_de_identidad_contador_j_name)[-1] or ".bin"
            fixed_file_name = f"Documento de identidad contador_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.documento_de_identidad_contador_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.documento_de_identidad_contador_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains 'Documento de identidad contador_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'documento_de_identidad_contador_j_id': drive_record.id,
                'documento_de_identidad_contador_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


####################################################################################################################################################
    Tarjeta_profesional_revisor_fiscal_j = fields.Binary(string="Tarjeta profesional revisor fiscal (Si aplica)")
    Tarjeta_profesional_revisor_fiscal_j_name = fields.Char(string="Tarjeta profesional revisor fiscal (Si aplica)")

    Tarjeta_profesional_revisor_fiscal_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    Tarjeta_profesional_revisor_fiscal_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_Tarjeta_profesional_revisor_fiscal_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarjeta profesional revisor fiscal (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.Tarjeta_profesional_revisor_fiscal_j_id.id,
            'target': 'new',
        }

    def _onchange_Tarjeta_profesional_revisor_fiscal_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.Tarjeta_profesional_revisor_fiscal_j and self.Tarjeta_profesional_revisor_fiscal_j_name:
            self._upload_document_Tarjeta_profesional_revisor_fiscal_j_to_drive()
    
    def _upload_document_Tarjeta_profesional_revisor_fiscal_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.Tarjeta_profesional_revisor_fiscal_j_name)[-1] or ".bin"
            fixed_file_name = f"Tarjeta profesional revisor fiscal_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.Tarjeta_profesional_revisor_fiscal_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.Tarjeta_profesional_revisor_fiscal_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains 'Tarjeta profesional revisor fiscal_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'Tarjeta_profesional_revisor_fiscal_j_id': drive_record.id,
                'Tarjeta_profesional_revisor_fiscal_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


####################################################################################################################################################
    Tarjeta_profesional_contador_j = fields.Binary(string="Tarjeta profesional contador (Si aplica)")
    Tarjeta_profesional_contador_j_name = fields.Char(string="Tarjeta profesional contador (Si aplica)")

    Tarjeta_profesional_contador_j_id = fields.Many2one('drive', string="Documento en Drive", store=True,
                                                          domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    Tarjeta_profesional_contador_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_Tarjeta_profesional_contador_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarjeta profesional contador (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.Tarjeta_profesional_contador_j_id.id,
            'target': 'new',
        }

    def _onchange_Tarjeta_profesional_contador_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.Tarjeta_profesional_contador_j and self.Tarjeta_profesional_contador_j_name:
            self._upload_document_Tarjeta_profesional_contador_j_to_drive()

    def _upload_document_Tarjeta_profesional_contador_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.Tarjeta_profesional_contador_j_name)[-1] or ".bin"
            fixed_file_name = f"Tarjeta profesional contador_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.Tarjeta_profesional_contador_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.Tarjeta_profesional_contador_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains 'Tarjeta profesional contador_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'Tarjeta_profesional_contador_j_id': drive_record.id,
                'Tarjeta_profesional_contador_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


###################################################################################################################################################
    certificado_bancario_vigente_j = fields.Binary(string="Certificado Bancario (Vigente)")
    certificado_bancario_vigente_j_name = fields.Char(string="Certificado Bancario (Vigente)")

    certificado_bancario_vigente_j_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    certificado_bancario_vigente_j_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_certificado_bancario_vigente_j_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado Bancario (Vigente)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_bancario_vigente_j_id.id,
            'target': 'new',
        }

    def _onchange_certificado_bancario_vigente_j(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.certificado_bancario_vigente_j and self.certificado_bancario_vigente_j_name:
            self._upload_document_certificado_bancario_vigente_j_to_drive()

    def _upload_document_certificado_bancario_vigente_j_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.certificado_bancario_vigente_j_name)[-1] or ".bin"
            fixed_file_name = f"22_Certificado bancario vigente_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.certificado_bancario_vigente_j)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.certificado_bancario_vigente_j_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '22_Certificado bancario vigente_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'certificado_bancario_vigente_j_id': drive_record.id,
                'certificado_bancario_vigente_j_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    cedula_representante_legal = fields.Binary(string="Cédula del Representante Legal")
    cedula_representante_legal_name = fields.Char(string="Cédula del Representante Legal")

    cedula_representante_legal_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cedula_representante_legal_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cedula_representante_legal_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cédula del Representante Legal',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_representante_legal_id.id,
            'target': 'new',
        }

    def _onchange_cedula_representante_legal(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cedula_representante_legal and self.cedula_representante_legal_name:
            self._upload_document_cedula_representante_legal_to_drive()
            print('ejecuto')
    
    def _upload_document_cedula_representante_legal_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cedula_representante_legal_name)[-1] or ".bin"
            fixed_file_name = f"8_Cédula representante legal_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cedula_representante_legal)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cedula_representante_legal_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '8_Cédula representante legal_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cedula_representante_legal_id': drive_record.id,
                'cedula_representante_legal_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    hoja_v_representante_legal = fields.Binary(string="Hoja de Vida Representante Legal")
    hoja_v_representante_legal_name = fields.Char(string="Hoja de Vida Representante Legal")

    hoja_v_representante_legal_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    hoja_v_representante_legal_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_hoja_v_representante_legal_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Hoja de Vida Representante Legal',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.hoja_v_representante_legal_id.id,
            'target': 'new',
        }

    def _onchange_hoja_v_representante_legal(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.hoja_v_representante_legal and self.hoja_v_representante_legal_name:
            self._upload_document_hoja_v_representante_legal_to_drive()

    def _upload_document_hoja_v_representante_legal_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.hoja_v_representante_legal_name)[-1] or ".bin"
            fixed_file_name = f"9_Hoja de vida representante legal_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.hoja_v_representante_legal)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.hoja_v_representante_legal_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '9_Hoja de vida representante legal_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'hoja_v_representante_legal_id': drive_record.id,
                'hoja_v_representante_legal_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True

    cert_contraloria_repre_legal = fields.Binary(string="Cert. Contraloría Representante Legal")
    cert_contraloria_repre_legal_name = fields.Char(string="Cert. Contraloría Representante Legal")

    cert_contraloria_repre_legal_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_contraloria_repre_legal_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_contraloria_repre_legal_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cert. Contraloría Representante Legal',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_contraloria_repre_legal_id.id,
            'target': 'new',
        }

    def _onchange_cert_contraloria_repre_legal(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_contraloria_repre_legal and self.cert_contraloria_repre_legal_name:
            self._upload_document_cert_contraloria_repre_legal_to_drive()

    def _upload_document_cert_contraloria_repre_legal_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_contraloria_repre_legal_name)[-1] or ".bin"
            fixed_file_name = f"10_Cert_contraloría representante legal_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_contraloria_repre_legal)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_contraloria_repre_legal_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '10_Cert_contraloría representante legal_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_contraloria_repre_legal_id': drive_record.id,
                'cert_contraloria_repre_legal_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    cert_procuraduria_repre_legal = fields.Binary(string="Cert. Procuraduría Representante Legal")
    cert_procuraduria_repre_legal_name = fields.Char(string="Cert. Procuraduría Representante Legal")

    cert_procuraduria_repre_legal_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    cert_procuraduria_repre_legal_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_cert_procuraduria_repre_legal_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cert. Procuraduría Representante Legal',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cert_procuraduria_repre_legal_id.id,
            'target': 'new',
        }

    def _onchange_cert_procuraduria_repre_legal(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.cert_procuraduria_repre_legal and self.cert_procuraduria_repre_legal_name:
            self._upload_document_cert_procuraduria_repre_legal_to_drive()

    def _upload_document_cert_procuraduria_repre_legal_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.cert_procuraduria_repre_legal_name)[-1] or ".bin"
            fixed_file_name = f"11_Cert_procuraduría representante legal_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.cert_procuraduria_repre_legal)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.cert_procuraduria_repre_legal_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '11_Cert_procuraduría representante legal_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'cert_procuraduria_repre_legal_id': drive_record.id,
                'cert_procuraduria_repre_legal_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    estados_financieros2 = fields.Binary(string="Estados financieros (si aplica)")
    estados_financieros2_name = fields.Char(string="Estados financieros (si aplica)")

    estados_financieros2_id = fields.Many2one('drive', string="Documento en Drive", store=True, domain="[('stage6_id', '=', drive_stage_provedor_id)]")
    estados_financieros2_visible = fields.Boolean(string="Documento visible", default=True, store=True)

    def action_open_estados_financieros2_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Estados financieros (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.estados_financieros2_id.id,
            'target': 'new',
        }

    def _onchange_estados_financieros2(self):
        """Sube el documento a Google Drive cuando se adjunta y lo registra en el modelo drive."""
        if self.estados_financieros2 and self.estados_financieros2_name:
            self._upload_document_estados_financieros2_to_drive()

    def _upload_document_estados_financieros2_to_drive(self):
        """Sube el archivo a Drive. Busca directamente en la nube para limpiar duplicados viejos o huérfanos."""
        # =====================================================================
        # CANDADO ANTI-DUPLICADOS (ESPECIAL PARA PRODUCCIÓN)
        # Si llega una petición simultánea (por doble clic o reintento del servidor),
        # chocará contra este candado y se cancelará sin duplicar el archivo.
        # =====================================================================
        try:
            self.env.cr.execute("SELECT id FROM {} WHERE id = %s FOR UPDATE NOWAIT".format(self._table), [self.id])
        except Exception:
            _logger.warning(f"Se bloqueó una petición duplicada simultánea para el registro {self.id}")
            return False
        # =====================================================================
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No hay registro stage6 para {self.name}.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No hay carpeta Drive asignada para {self.name}.")
            return False

        try:
            import mimetypes

            # 1. Preparamos el nuevo nombre y archivo temporal
            original_extension = os.path.splitext(self.estados_financieros2_name)[-1] or ".bin"
            fixed_file_name = f"13_Estados financieros_{uuid.uuid4()}{original_extension}"
            
            file_content = base64.b64decode(self.estados_financieros2)
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            mime_type, _ = mimetypes.guess_type(self.estados_financieros2_name)
            if not mime_type:
                mime_type = 'application/octet-stream'

            # ---------------------------------------------------------------------
            # CORRECCIÓN: CONSULTA DIRECTA A GOOGLE DRIVE (PyDrive)
            # Preguntamos a Drive: "¿Tienes archivos con este nombre en esta carpeta?"
            # Esto encuentra el PDF viejo aunque Odoo no sepa que existe.
            # ---------------------------------------------------------------------
            query = f"'{carpeta_drive}' in parents and title contains '13_Estados financieros_' and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            file_id = False
            file_link = False

            if file_list:
                # --- CASO: YA EXISTEN ARCHIVOS (incluso los viejos/dañados) ---
                
                # Tomamos el primero de la lista para RECICLARLO
                file_to_update = file_list[0]
                file_id = file_to_update['id']
                
                _logger.info(f"Encontrado archivo viejo en Drive (ID: {file_id}). Se sobrescribirá.")

                # Sobrescribimos contenido, nombre y tipo (convierte PDF a Imagen o viceversa)
                file_to_update['title'] = fixed_file_name
                file_to_update['mimeType'] = mime_type
                file_to_update.SetContentFile(temp_file_path)
                file_to_update.Upload()
                
                file_link = file_to_update['alternateLink'] # O usa f"https://drive.google.com..."

                # --- LIMPIEZA DE DUPLICADOS ---
                # Si la función vieja dejó 2 o 3 archivos, borramos los sobrantes ahora mismo.
                if len(file_list) > 1:
                    for duplicate in file_list[1:]:
                        try:
                            _logger.info(f"Borrando duplicado viejo encontrado en Drive: {duplicate['title']} (ID: {duplicate['id']})")
                            duplicate.Delete()
                        except Exception as e:
                            _logger.warning(f"No se pudo borrar el duplicado {duplicate['id']}: {e}")

            else:
                # --- CASO: NO HAY NADA, CREAMOS DESDE CERO ---
                _logger.info("No se encontraron archivos previos en Drive. Creando nuevo.")
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': mime_type,
                    'parents': [{'id': carpeta_drive}]
                }
                new_file = drive.CreateFile(file_metadata)
                new_file.SetContentFile(temp_file_path)
                new_file.Upload()
                
                file_id = new_file['id']
                file_link = new_file['alternateLink']

            # ---------------------------------------------------------------------
            # SINCRONIZACIÓN CON ODOO
            # Ahora que Drive está limpio y actualizado, nos aseguramos que Odoo apunte bien.
            # ---------------------------------------------------------------------
            
            # Buscamos si ya existe ese ID de archivo en Odoo
            drive_record = self.env['drive'].search([('file_id', '=', file_id)], limit=1)

            vals = {
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_url': file_link,
                'file_data': False,
                'folder_id': carpeta_drive,
                'stage6_id': stage6_record.id
            }

            if drive_record:
                drive_record.write(vals)
            else:
                drive_record = self.env['drive'].create(vals)

            # Vinculamos al modelo actual
            self.write({
                'estados_financieros2_id': drive_record.id,
                'estados_financieros2_visible': False
            })

            os.unlink(temp_file_path)
            _logger.info("Proceso de subida y limpieza completado con éxito.")

        except Exception as e:
            _logger.error(f"Error crítico en subida a Drive: {e}")
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return False

        return True


    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')
    contratista1 = fields.Many2one(comodel_name='contrataciones_fuv', string="Contratista", tracking=True,
                                   domain="[('proyecto_relacionado', '=', proyecto_relacionado)]")

    tipo = fields.Selection([
        ('contratista', 'Contratista'),
        ('proveedor', 'Proveedor')
    ], string='Tipo', required=False, tracking=True)

    identificacion = fields.Char(string='NIT o Identificación', required=False, tracking=True)
    # telefono = fields.Char(string='Teléfono', tracking=True)
    # correo = fields.Char(string='Correo Electrónico')
    # direccion = fields.Char(string='Dirección')
    estado = fields.Selection(
        [('activo', 'Activo'), ('inactivo', 'Inactivo'), ('pendiente_aprobacion', 'Pendiente de Aprobación')],
        string='Estado', default='activo', tracking=True)
    pagos_realizados = fields.Float(string='Pagos Realizados')
    deuda_total = fields.Float(string='Deuda Total')
    notas = fields.Text(string='Notas Internas')

    ###################################    VARIABLES DEL FORMULAIO    ###################################



    nit_rut = fields.Char(string='NIT o RUT', required=False, tracking=True)
    ###********** VARIABLES DE DATOS DE UBICACIÓN
    pais2 = fields.Many2one(comodel_name='pais', string="País", tracking=True)
    pais = fields.Char(string="País", tracking=True)
    departamento = fields.Char(string="Departamento", tracking=True)
    ciudad = fields.Char(string="Ciudad", tracking=True)
    direccion_fisica = fields.Char(string="Dirección", tracking=True)
    correo_facturacion = fields.Char(string="Correo electrónico para facturación electrónica", tracking=True)

    @api.constrains('correo_facturacion')
    def _check_correo_facturacion(self):
        for record in self:
            if record.correo_facturacion and not re.match(r"[^@]+@[^@]+\.[^@]+", record.correo_facturacion):
                raise ValidationError("El correo electrónico de facturación no es válido.")

    def is_valid_email(email):
        """Check if the email is a valid format."""
        regex = r'^[a-z0-9]+[\._]?[a-z0-9]+[@]\w+[.]\w+$'
        return re.match(regex, email) is not None

    # Test the function
    emails = ["test@example.com", "invalid-email.com", "another.test@example.co.uk"]
    for email in emails:
        if is_valid_email(email):
            print(f"'{email}' es una dirección de correo electrónico válida.")
        else:
            print(f"'{email}' es una dirección de correo electrónico no válida.")

    codigo_postal = fields.Char(string="Código Postal", tracking=True)
    # El indicativo suele venir de la relación con el país
    numero_telefono = fields.Char(string="Número")
    extension = fields.Char(string="Ext.")
    country2_id = fields.Many2one('res.country', string="País")
    indicativo2 = fields.Char(compute="_compute_telefono_data2", store=True)
    bandera2_html = fields.Html(compute="_compute_telefono_data2") 

    @api.depends('country2_id')
    def _compute_telefono_data2(self):
        for record in self:
            if record.country2_id:
                record.indicativo2 = f"+{record.country2_id.phone_code}" if record.country2_id.phone_code else ""
                if record.country2_id.image_url:
                    # Ajustamos el estilo aquí para que la imagen no rompa el diseño
                    record.bandera2_html = f'<img src="{record.country2_id.image_url}" style="height: 15px; width: auto; vertical-align: middle; margin-right: 5px;"/>'
                else:
                    record.bandera2_html = False
            else:
                record.indicativo2 = ""
                record.bandera2_html = False



    fax = fields.Char(string="Nro. de fax", tracking=True)
    pagina_web = fields.Char(string="Página WEB", tracking=True)


    @api.constrains('pagina_web')
    def _check_pagina_web(self):
        for record in self:
            if record.pagina_web and not record.pagina_web.startswith(('http://', 'https://', 'www.')):
                raise ValidationError("El campo página web debe comenzar con 'http://' o 'https://' o 'www.'.")
            

    ###********** VARIABLES DE DATOS DE CONTACTO COMERCIAL
    nombre_contacto_comercial = fields.Char(string="Nombre", tracking=True)
    telefono_contacto_comercial = fields.Char(string="Teléfono móvil", tracking=True)
    country_id = fields.Many2one('res.country', string="País")
    indicativo = fields.Char(compute="_compute_telefono_data", store=True)
    bandera_html = fields.Html(compute="_compute_telefono_data") 

    @api.depends('country_id')
    def _compute_telefono_data(self):
        for record in self:
            if record.country_id:
                record.indicativo = f"+{record.country_id.phone_code}" if record.country_id.phone_code else ""
                if record.country_id.image_url:
                    # Ajustamos el estilo aquí para que la imagen no rompa el diseño
                    record.bandera_html = f'<img src="{record.country_id.image_url}" style="height: 15px; width: auto; vertical-align: middle; margin-right: 5px;"/>'
                else:
                    record.bandera_html = False
            else:
                record.indicativo = ""
                record.bandera_html = False

    correo_contacto_comercial = fields.Char(string="Correo electrónico", tracking=True)

    @api.constrains('correo_contacto_comercial')
    def _check_correo_contacto_comercial(self):
        for record in self:
            if record.correo_contacto_comercial and not re.match(r"[^@]+@[^@]+\.[^@]+", record.correo_contacto_comercial):
                raise ValidationError("El correo electrónico de contacto no es válido.")

    correo_info_contacto_comercial = fields.Char(string="Correo para recepción órdenes de compra o información adicional", tracking=True)

    @api.constrains('correo_contacto_comercial')
    def _check_correo_contacto_comercial(self):
        for record in self:
            if record.correo_contacto_comercial and not re.match(r"[^@]+@[^@]+\.[^@]+", record.correo_contacto_comercial):
                raise ValidationError("El correo electrónico de contacto no es válido.")

    ###********** VARIABLES DE CONDICIONES DE PAGO/DATOS BANCARIOS PROVEEDOR
    # Condiciones de Pago
    tiempo_pago_dias = fields.Char(string="Tiempo plazo de pago (en días)", tracking=True)
    forma_pago = fields.Char(string="Forma de Pago", tracking=True)
    moneda_venta = fields.Selection(
        selection=[('COP', 'COP (Pesos Colombianos)'), ('USD', 'USD (Dólares Americanos)')],
        string="Moneda de Venta",
        default='COP',
        required=False,
        tracking=True
    )
    tiempo_entrega_dias = fields.Char(
        string="Tiempo plazo de entrega (en días)",
        default='N/A',
        tracking=True
    )
    acepta_devoluciones = fields.Selection(
        selection=[('SI', 'Sí'), ('NO', 'No'), ('selecione', 'Seleccione')],
        string="¿Acepta devoluciones?",
        default='selecione',
        tracking=True
    )
    # Datos Bancarios
    nombre_banco = fields.Selection(
        selection=[
            ('abn_amro', 'ABN AMRO BANK'),
            ('bancamia', 'BANCAMIA'),
            ('banco_agrario', 'BANCO AGRARIO'),
            ('banco_aliadas', 'BANCO ALIADAS'),
            ('banco_caja_social', 'BANCO CAJA SOCIAL'),
            ('banco_central_hipotecario', 'BANCO CENTRAL HIPOTECARIO'),
            ('banco_bogota', 'BANCO DE BOGOTÁ'),
            ('banco_credito', 'BANCO DE CRÉDITO'),
            ('banco_occidente', 'BANCO DE OCCIDENTE'),
            ('banco_falabella', 'BANCO FALABELLA'),
            ('banco_pichincha', 'BANCO PICHINCHA'),
            ('banco_popular', 'BANCO POPULAR'),
            ('bancolombia', 'BANCOLOMBIA'),
            ('banco_santander', 'BANCO SANTANDER'),
            ('bbva', 'BBVA'),
            ('boston', 'BOSTON'),
            ('brinks', 'BRINKS'),
            ('citibank', 'CITIBANK'),
            ('coltefinanciera', 'COLTEFINANCIERA S.A.'),
            ('coomeva_financiera', 'COOMEVA FINANCIERA'),
            ('colmena', 'COOP.SOC.DE AHORRO Y VIVIENDA COLMENA'),
            ('corpbanca', 'CORPBANCA COLOMBIA S.A.'),
            ('davivienda', 'DAVIVIENDA'),
            ('helm_bank', 'HELM BANK'),
            ('finandina', 'FINANDINA'),
            ('hsbc', 'HSBC'),
            ('interbanco', 'INTERBANCO'),
            ('av_villas', 'AV VILLAS'),
            ('mercantil', 'MERCANTIL DE COLOMBIA'),
            ('multibanca_colpatria', 'MULTIBANCA COLPATRIA'),
            ('plan_separe', 'PLAN SEPARE'),
            ('procredit', 'PROCREDIT DE COLOMBIA'),
            ('standard_chartered', 'STANDART CHARTERED'),
            ('sudameris', 'SUDAMERIS DE COLOMBIA'),
            ('nubank', 'Nubank')
        ],
        string="Nombre del Banco",
        required=False,
        tracking=True
    )

    sucursal_ciudad = fields.Char(string="Sucursal o Ciudad", tracking=True)
    tipo_cuenta2 = fields.Char(
        string="Tipo de Cuenta Bancaria",
        tracking=True
    )
    numero_cuenta = fields.Char(string="Número de Cuenta Bancaria", required=False, tracking=True)
    titular_cuenta = fields.Char(string="Titular de la Cuenta Bancaria", required=False, tracking=True)

    # Autorización
    autorizacion_pago = fields.Text(
        string="Autorización",
        default="Autorizamos a FUNDACIÓN UNIVERSIDAD DEL VALLE para que consigne en nuestra cuenta mencionada los "
                "valores correspondientes al pago de las facturas, a nuestro nombre.",
        tracking=True
    )

    ###********** VARIABLES DE INFORMACIÓN TRIBUTARIA
    ############# IMPUESTO SOBRE LA RENTA Y SUS COMPLEMENTARIOS
    actividad_realizar = fields.Selection(selection=[
        ('contratos de obra', 'Contratos de obra'),
        ('arrendamientos', 'Arrendamientos'),
        ('compra Activos fijos', 'Compra Activos fijos'),
        ('compra de bienes en general', 'Compra de bienes en general'),
        ('servicios personas jurídicas', 'Servicios personas jurídicas'),
        ('servicios personas naturales', 'Servicios personas naturales'),
        ('honorarios personas jurídicas', 'Sonorarios personas jurídicas'),
        ('honorarios personas naturales', 'Honorarios personas naturales'),
        ('restaurante, hotel y hospedaje ', 'Restaurante, hotel y hospedaje'),
        ('servicios de aseo y vigilancia ', 'Servicios de aseo y vigilancia'),
        ('servicios transp nal carga terr., Ae., Mar. ', 'Servicios transp nal carga terr., Ae., Mar. '),
        ('interventoria persona juridica, Ae., Mar. ', 'Interventoria persona juridica, Ae., Mar. '),
        ('otros', 'Otros'),
        ('selecione', 'Seleccione')
    ], string='Tipo', required=False, tracking=True, default='selecione')
    descripcion_actividad = fields.Text(string="Descripción", tracking=True)
    cdg_actividad_economica = fields.Char(string="Codigo de actividad economica renta", tracking=True)
    sujeto_retencion = fields.Selection(
        selection=[('SI', 'Sí'), ('NO', 'No'), ('seleccione', 'Seleccione')],
        string="¿Acepta devoluciones?",
        default='seleccione',
        tracking=True
    )
    otro_sujeto_retencion = fields.Selection(selection=[
        ('autorretenedor', 'Autorretenedor'),
        ('bolsa Agrepecuaria', 'Bolsa Agrepecuaria'),
        ('ley 1425', 'Ley 1425'),
        ('ley 550', 'Ley 550'),
        ('no Contribuyente Art. 22 E.T.', 'No Contribuyente Art. 22 E.T.'),
        ('regimen Especial Art. 19 E.T.', 'Regimen Especial Art. 19 E.T.'),
        ('otro', 'Otro')
    ], string='Tipo', required=False, tracking=True)
    otra_resolucion = fields.Text(string="Si es otro, diga cuál/o resolución", tracking=True)

    ############# IMPUESTO A LAS VENTAS
    sujeto_retencion_iva = fields.Selection(
        selection=[('SI', 'Sí'), ('NO', 'No'), ('seleccione', 'Seleccione')],
        string="Sujeto a retención de IVA",
        default='seleccione',
        tracking=True
    )
    gran_contribuyente = fields.Selection(
        selection=[('SI', 'Sí'), ('NO', 'No'), ('seleccione', 'Seleccione')],
        string="Gran Contribuyente",
        default='seleccione',
        tracking=True
    )
    numero_resolucion = fields.Char(string="N° de resolución", tracking=True)
    fecha = fields.Date(string="Fecha", tracking=True)

    regimen_iva = fields.Selection(
        selection=[
            ('responsable de IVA', 'Responsable de IVA'),
            ('no responsable del IVA', 'No responsable del IVA'),
            ('seleccione', 'Seleccione')
        ],
        string="Régimen de IVA",
        default='seleccione',
        tracking=True
    )

    ############# IMPUESTOS MUNICIPALES
    act_economica_main = fields.Selection(
        selection=[('seleccione', 'Seleccione'), ('comercial', 'Comercial'), ('financiero', 'Financiero'),
                   ('industrial', 'Industrial'), ('servicos', 'Servicos')],
        string="Actividad económica principal",
        default='seleccione',
        tracking=True
    )
    sjt_retencion_ica = fields.Selection(
        selection=[('SI', 'Sí'), ('NO', 'No')],
        string="Sujeto a retención de ICA",
        default='NO',
        tracking=True
    )

    ############# IMPUESTOS MUNICIPALES

    ###********** FIRMA DEL PROVEEDOR
    firma_proveedor_name = fields.Char(string="Firma", tracking=True)
    firma_proveedor = fields.Binary(string="Firma del Proveedor")

    nombre_proveedor = fields.Char(string="Nombre", tracking=True)
    cargo_proveedor = fields.Char(string="Cargo", tracking=True)

    ###********** DATOS A COMPLETAR POR FUNDACION UNIVERSIDAD DEL VALLE
    tipo_proveedor = fields.Selection(selection=[
        ('seleccione', 'Seleccione'),
        ('prestador de servicios ', 'Prestador de servicios '),
        ('interventoria ', 'Interventoria '),
        ('proveedores comercial ', 'proveedores comercial '),
        ('contratos de Obra', 'Contratos de Obra'),
        ('honorarios ', 'Honorarios '),
        ('compra de Activos fijos ', 'Compra de Activos fijos ')
    ], string='Tipo proveedor', default='seleccione', required=False, tracking=True)

    tipo_cliente = fields.Selection(selection=[
        ('seleccione', 'Seleccione'),
        ('proveedores Servicios', 'Proveedores Servicios'),
        ('proveedores Servicios', 'Proveedores Servicios'),
        ('proveedores de Bienes', 'Proveedores de Bienes'),
        ('obras', 'Obras'),
        ('interventoria', 'Interventoria')
    ], string="Tipo cliente", default='seleccione', tracking=True)

    sociedad = fields.Selection(selection=[
        ('seleccione', 'Seleccione'),
        ('FUNDACION UNIVERSIDAD DEL VALLE 800.187.151-9', 'FUNDACION UNIVERSIDAD DEL VALLE 800.187.151-9')
    ], string="Sociedad", default='seleccione', tracking=True)

    unidad_compras = fields.Selection(selection=[
        ('seleccione', 'Seleccione'),
        ('001- Cali', '001- Cali'),
        ('002- Otros municipios y ciudades', '002- Otros municipios y ciudades')
    ], string="Unidad de compras", default='seleccione', tracking=True)

    unidad_ventas = fields.Selection(selection=[
        ('seleccione', 'Seleccione'),
        ('001- Sistema General de regalias', '001- Sistema General de regalias'),
        ('002- Adm Fundacion Univeridad del Valle', '002- Adm Fundacion Univeridad del Valle'),
        ('003-Proyectos', '003-Proyectos'),
        ('004-Ingresos para terceros', '004-Ingresos para terceros')
    ], string="Unidad de ventas", default='seleccione', tracking=True)

    grupuo_tesoreria = fields.Selection(selection=[
        ('seleccione', 'Seleccione'),
        ('REGALIAS - SPGR', 'REGALIAS - SPGR'),
        ('REGALIAS - APORTE FUV', 'REGALIAS - APORTE FUV'),
        ('REGALIAS - APORTE MUNICIPIO', 'REGALIAS - APORTE MUNICIPIO'),
        ('REGALIAS - SPGR Y APORTE FUV', 'REGALIAS - SPGR Y APORTE FUV'),
        ('REGALIAS - SPGR Y APORTE MUNICIPIO', 'REGALIAS - SPGR Y APORTE MUNICIPIO'),
        ('REGALIAS - APORTE FUV Y APORTE MUNICIPIOS', 'REGALIAS - APORTE FUV Y APORTE MUNICIPIOS'),
        ('RECURSOS INGRESOS PARA TERCEROS', 'RECURSOS INGRESOS PARA TERCEROS'),
        ('RECURSOS INGRESOS PARA TERCEROS', 'RECURSOS INGRESOS PARA TERCEROS'),
        ('CARTAS DE INSTRUCCIÓN', 'CARTAS DE INSTRUCCIÓN'),
        ('PROYECTOS PROPIOS', 'PROYECTOS PROPIOS'),
    ], string="Grupo de tesoreria", default='seleccione', tracking=True)

    ##******************** APROBADORES - FINANCIERA

    ###### VENTANILLA UNICA
    firma_ventanilla_unica = fields.Char(string="Firma", tracking=True)
    nombre_ventanilla_unica = fields.Char(string="Nombre completo", tracking=True)
    fecha_ventanilla_unica = fields.Date(string="Fecha aprobacion", tracking=True)

    ###### ASISTENTE CONTABLE
    firma_asistente_contable = fields.Char(string="Firma", tracking=True)
    nombre_asistente_contable = fields.Char(string="Nombre completo", tracking=True)
    fecha_asistente_contable = fields.Date(string="Fecha aprobacion", tracking=True)

    ###### DIRECCION FINANCIERA
    firma_direccion_financiera = fields.Char(string="Firma", tracking=True)
    nombre_direccion_financiera = fields.Char(string="Nombre completo", tracking=True)
    fecha_direccion_financiera = fields.Date(string="Fecha aprobacion", tracking=True)




class InformacionTributaria(models.Model):
    _name = 'informacion_tributaria'
    _description = 'Información tributaria de los municipios donde es contribuyente'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    informacion_tributaria_relacionado = fields.Many2one(
        comodel_name='contratista_proveedor',
        string='Relacionado'
    )
    municipios2 = fields.Many2one(comodel_name='municipio', string="Municipio",
                                  )
    @api.model
    def _default_municipio(self):
        municipio = self.env['municipio'].search([('name', '=', 'Cali')], limit=1)
        return municipio.id if municipio else False

    #Columna 1
    nombre_municipio = fields.Char(string="Municipio")
    cdg_activida_ciiu = fields.Char(string="Código actividad CIIU")
    tarifa = fields.Float(string="Tarifa")



class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def create(self, vals):
        # Crear el usuario normalmente
        user = super(ResUsers, self).create(vals)

        # Verificar si el usuario pertenece al grupo 'Usuario Externo'
        external_group = self.env.ref('Matriz.group_contratacion_externo', raise_if_not_found=False)
        if external_group and external_group.id in user.groups_id.ids:
            # Verificar si ya existe un registro en contratista_proveedor para este usuario
            existing_contratista = self.env['contratista_proveedor'].search([('user_id', '=', user.id)], limit=1)
            if not existing_contratista:
                # Crear el registro en contratista_proveedor si no existe
                self.env['contratista_proveedor'].with_context(
                    creacion_automatica_contratista_externo=True
                ).create({
                    'name': user.name,  # Usar el nombre del usuario externo
                    'user_id': user.id,  # Relacionar con el usuario
                    'clase_persona': 'seleccion_manual',
                })

        return user

    roles_groups = fields.Many2many(
        'res.groups',
        compute='_compute_roles_groups',
        string="Rol"
    )

    def _compute_roles_groups(self):
        xmlids = [
            'Matriz.cat_sgo_admin_control',
            'Matriz.cat_sgo_direccion_coord',
            'Matriz.cat_sgo_financiero_tesoreria',
            'Matriz.cat_sgo_juridico_estruc',
            'Matriz.cat_sgo_apoyo_externo',
            'Matriz.module_category_gestion_documental',
        ]
        category_ids = set()
        for xmlid in xmlids:
            rec = self.env.ref(xmlid, raise_if_not_found=False)
            if rec:
                category_ids.add(rec.id)
        for user in self:
            user.roles_groups = user.groups_id.filtered(
                lambda g: g.category_id.id in category_ids
            )


class InfoContrataciones(models.Model):
    _name = 'info_contrataciones'
    _description = 'Información Académica y Profesional'

    info_id = fields.Many2one('contratista_proveedor', string='Tabla')

    nombre = fields.Char(string="Nombre")
    valor = fields.Char(string="Valor")
    cedula = fields.Char(string="Cédula")

    tipo_estudio = fields.Selection([
        ('pregrado', 'Pregrado'),
        ('postgrado', 'Postgrado'),
        ('otro', 'Otros estudios'),
    ], string="Tipo de estudio", required=True, default='pregrado')

    profesion = fields.Char(string="Profesión")
    anio_profesion = fields.Date(string="Año de graduación")

    postgrado = fields.Char(string="Postgrado")
    anio_postgrado = fields.Date(string="Año de graduación")

    tarjeta_profesional = fields.Char(string="Tarjeta profesional")
    anio_tarjeta = fields.Date(string="Año de expedición")

    otro_estudio = fields.Char(string="Título")
    anio_otro_estudio = fields.Date(string="Año de grado")

    titulo_estudio = fields.Char(
        string="Profesión / título",
        compute='_compute_datos_estudio',
        inverse='_inverse_datos_estudio',
        store=True,
    )
    anio_grado = fields.Date(
        string="Año de grado",
        compute='_compute_datos_estudio',
        inverse='_inverse_datos_estudio',
        store=True,
    )

    @api.depends(
        'tipo_estudio', 'profesion', 'anio_profesion', 'postgrado',
        'anio_postgrado', 'otro_estudio', 'anio_otro_estudio'
    )
    def _compute_datos_estudio(self):
        for record in self:
            if record.tipo_estudio == 'postgrado':
                record.titulo_estudio = record.postgrado
                record.anio_grado = record.anio_postgrado
            elif record.tipo_estudio == 'otro':
                record.titulo_estudio = record.otro_estudio
                record.anio_grado = record.anio_otro_estudio
            else:
                record.titulo_estudio = record.profesion
                record.anio_grado = record.anio_profesion

    def _inverse_datos_estudio(self):
        for record in self:
            if record.tipo_estudio == 'postgrado':
                record.postgrado = record.titulo_estudio
                record.anio_postgrado = record.anio_grado
            elif record.tipo_estudio == 'otro':
                record.otro_estudio = record.titulo_estudio
                record.anio_otro_estudio = record.anio_grado
            else:
                record.profesion = record.titulo_estudio
                record.anio_profesion = record.anio_grado

    @api.constrains('anio_profesion', 'anio_postgrado', 'anio_tarjeta', 'anio_otro_estudio')
    def _check_valid_dates(self):
        for record in self:
            for field_name in ['anio_profesion', 'anio_postgrado', 'anio_tarjeta', 'anio_otro_estudio']:
                fecha = record[field_name]
                if fecha and fecha.year < 1900:
                    campo_legible = self._fields[field_name].string or field_name.replace('_', ' ').capitalize()
                    raise ValidationError(_(
                        "⚠️ El campo **%s** contiene una fecha inválida.\n\n"
                        "La fecha debe ser posterior al año 1900.\n\n"
                        "Ejemplo válido: 1995-06-15"
                    ) % campo_legible)

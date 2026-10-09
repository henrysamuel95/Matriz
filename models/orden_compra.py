# models.py
#!/usr/bin/python
from email.policy import default

from odoo import models, fields, api
from datetime import datetime, timedelta
from .drive import GoogleDriveAuth
import threading
import uuid
import time
from odoo.exceptions import UserError
from odoo.exceptions import AccessError  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore
import tempfile
from attr import exceptions
from odoo.modules.module import get_module_resource
import os
import base64
from docx import Document  # type: ignore
from docx.api import Document as DocumentApi  # type: ignore
from docxtpl import DocxTemplate  # type: ignore
from io import BytesIO
from markupsafe import escape as html_escape

import logging

_logger = logging.getLogger(__name__)


def _orden_compra_email_body(titulo, saludo, cuerpo_html, formulario_url, observaciones=None):
    color_principal = "#043484"
    observaciones_html = ""
    if observaciones:
        observaciones_html = f"""
            <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid {color_principal};">
                <strong>Observaciones:</strong><br/>
                {html_escape(observaciones)}
            </div>
        """

    return f"""
        <div style="font-family: Arial, sans-serif; font-size: 14px; color: #1f2937; background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 4px; overflow: hidden;">
            <div style="background-color: {color_principal}; color: white; padding: 12px 20px; text-align: center; font-size: 18px; font-weight: bold;">
                {html_escape(titulo)}
            </div>
            <div style="padding: 24px 16px;">
                <p>Cordial saludo, <strong>{html_escape(saludo)}</strong>:</p>
                {cuerpo_html}
                {observaciones_html}
                <p style="text-align: center; margin: 28px 0 18px;">
                    <a href="{html_escape(formulario_url)}"
                       style="background-color: {color_principal}; color: white; padding: 11px 22px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                        Ir al Formulario
                    </a>
                </p>
                <p style="margin-top: 20px; color: #8792a2; font-size: 12px;">Este es un mensaje automático de SGO.</p>
            </div>
        </div>
    """

class OrdenCompraBienes(models.Model):
    _name = 'orden_compra_bienes'
    _description = 'Orden de Compra de Bienes'
    _inherit = ['mail.thread', 'mail.activity.mixin'] # Habilita el seguimiento en el chatter



    saldo_disponible_contratar = fields.Monetary(related="proyecto_relacionado_c.saldo_disponible_produccion", string='Saldo disponible')



    ####################################################################################################################
    # Nuevos campos para hacer efectivo el AT.
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    anexo_id = fields.Many2one('anexo', string="Anexo")
    # Relación hacia departamento_y_municipio
    departamento_municipio_ids = fields.One2many(
        'departamento_y_municipio',
        'orden_compra_bienes_id',
        string="Departamentos y Municipios"
    )
    municipios2 = fields.Many2one(related="departamento_municipio_ids.municipios2")
    departamentos = fields.Many2one(related="departamento_municipio_ids.departamentos")

    def action_ver_anexos(self):
        self.ensure_one()

        # Buscar si ya hay un anexo ligado a esta orden
        anexo = self.env['anexo'].search([
            ('orden_compra_bienes_id', '=', self.id)
        ], limit=1)

        if not anexo:
            # Si no existe, lo crea
            anexo = self.env['anexo'].create({
                'name': self.name or 'Anexo de Orden %s' % self.id,
                'proceso_gestion_id': getattr(self, 'proceso_gestion_id', False) or False,
                'orden_compra_bienes_id': self.id
            })

        return {
            'type': 'ir.actions.act_window',
            'name': 'Anexo tributario',
            'res_model': 'anexo',
            'view_mode': 'form',
            'res_id': anexo.id,
            'target': 'current',
        }

    objeto_contractual = fields.Text(string="Objeto contractual")



    codigo_ciiu = fields.Char(string="Código CIIU")
    id_proyecto3 = fields.Char(string='Id del proyecto')
    aprueba_f_rutt = fields.Boolean('Aprueba-F', tracking=True)
    compromiso_presupuestal = fields.Char('RPC', tracking=True)
    f_electronica = fields.Boolean('Facturación electronica', tracking=True)
    r_iva = fields.Boolean('Responsable de IVA', tracking=True)
    no_r_iva = fields.Boolean('No responsable de IVA', tracking=True)

    @api.onchange('r_iva')
    def _onchange_r_iva(self):
        if self.r_iva:
            self.no_r_iva = False

    @api.onchange('no_r_iva')
    def _onchange_no_r_iva(self):
        if self.no_r_iva:
            self.r_iva = False


    # Sección Impuesto a la venta (IVA)
    responsable = fields.Boolean(string="Responsable IVA")
    no_responsable = fields.Boolean(string="No Responsable IVA")

    excluido = fields.Boolean(string="Excluido IVA")
    exento = fields.Boolean(string="Exento IVA")
    retencion_iva = fields.Float(string="Retención IVA")

    # Sección Otros
    facturador_electronico = fields.Boolean(string="Facturador Electrónico")
    declaracion_renta = fields.Boolean(string="Declaración Renta")
    regimen_simple_tributacion = fields.Boolean(string="Régimen Simple de Tributación")
    regimen_especial = fields.Boolean(string="Régimen Especial")
    regimen_ordinario = fields.Boolean(string="Régimen Ordinario")
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################

    relacion_orden_compra_bienes_fuv_ids = fields.One2many(
        'cuentas_de_cobros', 
        'relacion_orden_compra_bienes_fuv_id', 
        string='Contrataciones'
    )

    # Este decorador ejecuta la validación justo antes de intentar borrar
    @api.ondelete(at_uninstall=False)
    def _check_cuentas_de_cobro(self):
        for record in self:
            if record.relacion_orden_compra_bienes_fuv_ids:
                # Estructuramos el mensaje con saltos de línea y un icono de advertencia
                mensaje = (
                    "⚠️  RESTRICCIÓN DE SEGURIDAD  ⚠️\n\n"
                    "No se puede eliminar esta orden de bien porque ya tiene una cuenta de cobro creada.\n\n"
                    "Por favor, solicite que se elimine la cuenta de cobro primero."
                )
                raise UserError(mensaje)





    #Crear informe en word para descargar
    informe_adjunto = fields.Binary(
        string='Informe Adjunto',
        attachment=True,
        readonly=True,
    )

    def render_informe_template_bienes(self):
        # Obtener la ruta del directorio actual
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        # Cargar la plantilla desde el archivo .docx utilizando python-docx-template
        template_path = os.path.join(current_directory, "plantilla_orden_bienes2024.docx")
        template = DocxTemplate(template_path)
        context = {'docs': self}
        # Generar los datos para la tabla
        #datos_tabla = self.generar_datos_tabla()
        #datos_tabla2 = self.generar_datos_tabla2()

        # Obtener el contexto de Odoo
        #context = {'docs': self, 'tabladedatos': datos_tabla, 'tabladedatos2': datos_tabla2}
        # Renderizar la plantilla con los datos actuales del registro
        template.render(context)
        # Guardar el resultado en un nuevo archivo .docx
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_plantilla_orden_bienes2024.docx")
        template.save(output_path)
        return output_path

    def generar_informe_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto
        template_rendered = self.render_informe_template_bienes()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        # Formatear el nombre con "AT " antes del nombre real
        nombre_archivo = "ORDEN DE COMPRA DE BIENES {}".format(self.proveedor or 'Informe_orden_compra_bienes')

        return {
            'name': '{}.docx'.format(nombre_archivo),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=orden_compra_bienes&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, nombre_archivo),
            'target': 'self',
        }


    ##### Relacion con el modelo "contratacion" #####
    currency_id = fields.Many2one('res.currency', string='Moneda')
    proyecto_relacionado_c = fields.Many2one('contratacion', string='Proyecto relacionado')

    # Nuevas variables AGREGADAS 0325
    cedula_proveedor = fields.Char(string='Cedula del proveedor', tracking=True)
    nombre_proyecto_relacionado = fields.Char(related='proyecto_relacionado_c.new_name',
                                              string='Nombre del proyecto relacionado')
    centro_costo = fields.Char(related='proyecto_relacionado_c.mcb_centro_costo', string="Centro de costo", store=True)

    eps_ids = fields.Many2one(comodel_name='eps', string="Nombres de EPS")

    name = fields.Char(string='Nombre', related="proveedor", tracking=True)

    fecha = fields.Date(string='Fecha')
    fecha_inicio = fields.Date(string="Fecha inicio")
    fecha_fin = fields.Date(string="Fecha fin")

    fecha_solicitud = fields.Date(string="Fecha de Solicitud")
    fecha_solicitud_texto = fields.Char(string="Fecha Solicitud en Texto", compute="_compute_fecha_solicitud_texto", store=True)

    @staticmethod
    def convertir_fecha_a_texto(fecha):
        """Convierte una fecha en texto con formato '04 de Octubre del 2024'."""
        if fecha:
            meses = [
                'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
            ]
            return f"{fecha.day:02d} de {meses[fecha.month - 1]} del {fecha.year}"
        return 'Fecha no asignada'

    @api.depends('fecha_solicitud')
    def _compute_fecha_solicitud_texto(self):
        for record in self:
            record.fecha_solicitud_texto = self.convertir_fecha_a_texto(record.fecha_solicitud)

    proveedor = fields.Char(string='Proveedor', tracking=True)
    direccion = fields.Char(string='Dirección', tracking=True)
    nit = fields.Char(string='NIT', tracking=True)
    telefono = fields.Char(string='Teléfono', tracking=True)
    cel = fields.Char(string='Celular', tracking=True)
    email = fields.Char(string='Email', tracking=True)
    lugar_venta = fields.Char(string='Lugar de Venta', tracking=True)

    ciudad = fields.Many2one(
        comodel_name='municipio',
        string="Ciudad", tracking=True)
    
    def name_get(self):
        result = []
        for record in self:
            display_name = record.name or ''
            if self._context.get('seleccion_persona') == 'persona_juridica' and record.nit:
                display_name += f' [NIT: {record.nit}]'
            elif self._context.get('seleccion_persona') == 'persona_natural' and record.cedula_proveedor:
                display_name += f' [DI: {record.cedula_proveedor}]'
            elif not self._context.get('seleccion_persona'):
                if record.nit:
                    display_name += f' [NIT: {record.nit}]'
                if record.cedula_proveedor:
                    display_name += f' [DI: {record.cedula_proveedor}]'

            result.append((record.id, display_name or "Sin Identificación"))
        return result
    

    descripcion = fields.Char(string='Descripción', tracking=True)
    solicitante = fields.Char(string='Solicitante', tracking=True)
    forma_pago = fields.Selection([
        ('contado', 'De Contado'),
        ('credito', 'Crédito')
    ], string='Forma de Pago', tracking=True)

    forma_pago1 = fields.Char(string='Forma de Pago', tracking=True)

    autoriza = fields.Binary("Firma Autorización", widget="signature", tracking=True)

    tiempo_entrega = fields.Char(string='Tiempo de Entrega', tracking=True)
    lineas_ids = fields.One2many('orden.compra.linea', 'orden_id', string='Líneas')
    descripcion_del_servicio = fields.Char(string='Descripción', related='lineas_ids.descripcion')
    subtotal = fields.Float(string='Sub-Total', compute='_compute_subtotal', store=True, tracking=True)
    iva = fields.Float(string='IVA', store=True, tracking=True)

    total = fields.Float(string='TOTAL', compute='_compute_total', store=True, tracking=True)
    @api.depends('subtotal', 'iva')
    def _compute_total(self):
        for record in self:
            record.total = record.subtotal + record.iva

    ############################################################################
    ############################################################################
    ############################################################################
    # Nuevo campo donde se guardará la suma de todos los "total"
    total_bienes = fields.Float(
        string='Total de todos los Bienes',
        compute='_compute_total_bienes_global',
        store=False
    )

    @api.depends('total', 'proyecto_relacionado_c', 'iva', 'subtotal')
    def _compute_total_bienes_global(self):
        for record in self:
            # 1. Verificamos que exista un proyecto relacionado para buscar
            if record.proyecto_relacionado_c:
                # 🔍 Buscamos solo los registros del mismo proyecto
                # Excluimos el ID actual (_origin) para que no se sume doble en la base de datos
                bienes_del_proyecto = self.search([
                    ('proyecto_relacionado_c', '=', record.proyecto_relacionado_c.id),
                    ('id', '!=', record._origin.id)
                ])
                
                # 2. Sumamos lo que ya está guardado en otros registros
                suma_historica = sum(bienes_del_proyecto.mapped('total'))
                
                # 3. Sumamos lo que el usuario tiene en pantalla en este momento
                suma_actual = record.total or 0.0
                
                record.total_bienes = suma_historica + suma_actual
                
                # Debug para consola
                print(f"\n>>> [PROYECTO: {record.proyecto_relacionado_c.name}]")
                print(f">>> Histórico: {suma_historica} | Actual: {suma_actual}")
                print(f">>> Total Bienes Proyecto: {record.total_bienes}")
            else:
                # Si no hay proyecto, el total es solo lo de este registro
                record.total_bienes = record.total or 0.0
    ############################################################################
    ############################################################################
    ############################################################################

    estado_compra_bienes = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('contratado', 'Contratado'),
    ], string='Estado orden de compra de bienes', default='coordinador', tracking=True)

    ################### Drive ##########################
    def unlink(self):
        # Iterar por cada registro que se va a eliminar
        for record in self:
            # Verificar si existe un archivo de Drive vinculado
            if record.drive_file_id:
                try:
                    # Intentar eliminar el archivo vinculado en Google Drive
                    record.drive_file_id.unlink()
                except Exception as e:
                    raise UserError(f"No se pudo eliminar la carpeta de Google Drive: {str(e)}")
        # Llamar al método original para eliminar el registro de Odoo
        return super(OrdenCompraBienes, self).unlink()

    # 3. WRITE CON CASCADA (Mantiene Drive intacto)
    def write(self, vals):
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            return True

        # Ejecutamos el guardado y Drive primero
        res = super(OrdenCompraBienes, self).write(vals)
        
        # --- LÓGICA DRIVE EXISTENTE (Sin cambios) ---
        if 'rut_pj_doc' in vals: self._upload_rut_pj_to_drive_pj()
        if 'junta_contadores_doc' in vals: self._upload_certificado_junta_contadores_to_drive_pj()
        if 'camara_comercio_doc' in vals: self._upload_camara_comercio_to_drive_pj()
        if 'planilla_ss_doc' in vals: self._upload_planilla_ss_to_drive_pj()
        if 'cert_parafiscales_doc' in vals: self._upload_certificado_parafiscales_to_drive_pj()
        if 'certificado_bancario_pj' in vals: self._upload_certificado_bancario_pj_to_drive_pj()
        if 'tp_contador_doc' in vals: self._upload_tp_contador_to_drive_pj()
        if 'resolucion_facturacion_doc' in vals: self._upload_resolucion_facturacion_to_drive_pj()
        if 'documento_rpc_pj' in vals: self._upload_documento_rpc_pj_to_drive_pj()
        if 'factura_cuenta_pj' in vals: self._upload_factura_cuenta_pj_to_drive_pj()
        if 'Anexo_tributario' in vals: self._upload_anexo_tributario_to_drive()
        if 'rut_pn' in vals: self._upload_rut_pn_to_drive()
        if 'documento_identidad' in vals: self._upload_documento_identidad_to_drive()
        if 'planilla' in vals: self._upload_planilla_to_drive()
        if 'certificado_bancario' in vals: self._upload_certificado_bancario_to_drive()
        if 'documento_rpc' in vals: self._upload_documento_rpc_to_drive()
        if 'factura_cuenta' in vals: self._upload_factura_cuenta_to_drive()
        if 'Anexo_tributario_pn' in vals: self._upload_anexo_tributario_pn_to_drive()

        # --- 🚀 NUEVO: EFECTO CASCADA PARA TOTAL BIENES ---
        if any(campo in vals for campo in ['total', 'proyecto_relacionado_c']):
            for record in self:
                if record.proyecto_relacionado_c:
                    hermanos = self.search([('proyecto_relacionado_c', '=', record.proyecto_relacionado_c.id)])
                    hermanos._compute_total_bienes_global()
                    # También avisamos al proyecto para que actualice su saldo disponible
        
        return res

    @api.model
    def create(self, vals):
        current_date = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = super(OrdenCompraBienes, self).create(vals)

        if not record.name:
            self.name = f"Folder_{current_date}"
            self.save()

        record.buscar_carpeta()

        return record

    proceso_en_ejecucion = fields.Boolean(default=False)

    def buscar_carpeta(self):
        """Busca o crea la carpeta relacionada en Odoo y en Google Drive."""
        if self.proyecto_relacionado_c:
            if self.proceso_en_ejecucion:
                _logger.info("Proceso de búsqueda de carpeta ya en ejecución, esperando a que termine.")
                return

            self.proceso_en_ejecucion = True

            try:
                _logger.info(f"Buscando carpeta para el proyecto relacionado: {self.proyecto_relacionado_c.name}")

                # Buscar o crear carpeta en Odoo
                drive_stage = self.env['drive_stage'].search([
                    ('name', '=', '3. CONTRATOS PS'),
                    ('proyecto_relacionado', '=', self.proyecto_relacionado_c.id),
                ], limit=1)

                if not drive_stage:
                    _logger.warning("No se encontró la carpeta 'CONTRATOS PS' en Odoo. Creando...")
                    drive_stage = self.env['drive_stage'].create({
                        'name': '3. CONTRATOS PS',
                        'proyecto_relacionado': self.proyecto_relacionado_c.id,
                        'folder_id': 'some_default_folder_id',
                    })

                _logger.info(f"Carpeta en Odoo encontrada o creada: {drive_stage.name} (ID: {drive_stage.id})")

                # Asignar solo el nombre sin identificador a `new_name`
                if self.name:
                    self.name = self.name

                # Generar nombre con identificador solo para Google Drive
                unique_folder_id = str(uuid.uuid4())[:8]
                drive_folder_name = f"{self.name}_(bien)_{unique_folder_id}"

                _logger.info(f"Intentando buscar o crear la carpeta con el nombre: {drive_folder_name}")

                # Buscar si ya existe en 'drive.file'
                drive_file = self.env['drive.file'].search([
                    ('folder_name', '=', drive_folder_name),
                    ('drive_id', '=', drive_stage.id)
                ], limit=1)

                if not drive_file:
                    _logger.info(f"Carpeta '{drive_folder_name}' no encontrada en 'drive.file'. Creando...")

                    with threading.Lock():
                        drive_file = self.env['drive.file'].search([
                            ('folder_name', '=', drive_folder_name),
                            ('drive_id', '=', drive_stage.id)
                        ], limit=1)
                        if not drive_file:
                            drive_auth = GoogleDriveAuth.get_instance()
                            drive = drive_auth.get_drive()

                            folder_metadata = {
                                'title': drive_folder_name,  # Usa la versión con identificador solo en Drive
                                'mimeType': 'application/vnd.google-apps.folder',
                                'parents': [{'id': drive_stage.folder_id}]
                            }

                            try:
                                folder = drive.CreateFile(folder_metadata)
                                folder.Upload()

                                drive_file = self.env['drive.file'].create({
                                    'folder_name': drive_folder_name,
                                    'folder_id2': folder['id'],
                                    'drive_id': drive_stage.id,

                                })
                                drive_file.write({'select_persona': 'bienes_y_sevicios'})

                                _logger.info(
                                    f"Carpeta '{drive_folder_name}' creada en 'drive.file' con ID {folder['id']}")

                                self.write({'drive_file_id': drive_file.id})
                                _logger.info(f"Campo 'drive_file_id' actualizado con ID {drive_file.id}")
                                time.sleep(3)


                            except Exception as e:
                                _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                                raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")
                        else:
                            _logger.info(f"Carpeta '{drive_folder_name}' ya existe en 'drive.file'.")
                            self.write({'drive_file_id': drive_file.id})
                            _logger.info(f"Campo 'drive_file_id' actualizado con ID {drive_file.id}")

            finally:
                self.proceso_en_ejecucion = False  # Corrige el flag de proceso en ejecución

    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive')  # Campo para vincular con drive.file

    ######################### DOCUMENTOS PARA PERSONA JURIDICA #################################
    anexo_tributario_pj = fields.Boolean(string='Anexo tributario', tracking=True)
    Anexo_tributario = fields.Binary(string="Anexo tributario")
    Anexo_tributario_name = fields.Char(string="Nombre del documento Anexo tributario", tracking=True)
    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_anexo_tributario = {}

    def abrir_documento_anexo_tributario_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_anexo_tributario_en_drive()

        # Primero, verificamos si 'Anexo_tributario_doc_id' está configurado
        if self.Anexo_tributario_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Anexo tributario',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.Anexo_tributario_doc_id.id,
                'target': 'new',
            }

        # Si 'Anexo_tributario_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.Anexo_tributario_name:
            file_name_to_search = self.Anexo_tributario_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_anexo_tributario:
                drive_document = self._cache_documentos_anexo_tributario[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_anexo_tributario[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'Anexo_tributario_doc_id' con el ID del documento encontrado
                self.write({'Anexo_tributario_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Anexo tributario',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')
        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Anexo tributario relacionado con este registro.')

    Anexo_tributario_visible = fields.Boolean(default=False, related='Anexo_tributario_doc_id.actualizar_visible',
                                              store=True,
                                              readonly=False)

    def actualizar_correo_anexo_tributario_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.Anexo_tributario_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.Anexo_tributario_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.Anexo_tributario_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    Anexo_tributario_doc_id = fields.Many2one('drive', string='Anexo tributario', tracking=True,
                                              domain="[('stage2_id', '=', drive_file_id)]")
    Anexo_tributario_doc_visible = fields.Boolean(default=True)

    def action_upload_Anexo_tributario_pj(self):
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
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'Anexo_tributario_pj_documento', 
            },
        }
    

    def subir_anexo_tributario_a_drive(self):
        """Sube el Anexo tributario a Google Drive y lo registra en el modelo drive."""
        if self.Anexo_tributario and self.Anexo_tributario_name:
            _logger.info(f"Subiendo documento '{self.Anexo_tributario_name}' a Google Drive...")
            self._upload_anexo_tributario_to_drive()
        else:
            _logger.warning("No se encontró el Anexo tributario para subir.")

    def _upload_anexo_tributario_to_drive(self):
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
            file_content = base64.b64decode(self.Anexo_tributario)
            original_extension = os.path.splitext(self.Anexo_tributario_name)[1]
            fixed_file_name = f"Anexo_tributario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.Anexo_tributario_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.Anexo_tributario,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Solo actualizar el campo Anexo_tributario_doc_id
            if not self.Anexo_tributario_doc_id:
                self.Anexo_tributario_doc_id = drive_record.id

            _logger.info(
                f"Campo Anexo_tributario_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar Anexo_tributario_doc_visible a False después de subir el archivo
            self.Anexo_tributario_doc_visible = False

            _logger.info(f"Campo Anexo_tributario_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    anexo_tributario_pj_aprueba_f = fields.Boolean(string="Anexo tributario aprobado por Coordinador", default=False)
    anexo_tributario_pj_aprueba_c = fields.Boolean(string="Anexo tributario aprobado por Financiero", default=False)

    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_rut_pj = {}

    def abrir_documento_rut_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_rut_pj_en_drive()

        # Primero, verificamos si 'rut_pj_doc_id' está configurado
        if self.rut_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'RUT',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.rut_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'rut_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rut_pj_name:
            file_name_to_search = self.rut_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rut_pj:
                drive_document = self._cache_documentos_rut_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_rut_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rut_pj_doc_id' con el ID del documento encontrado
                self.write({'rut_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'RUT',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún RUT relacionado con este registro.')

    rut_pj_actualizar_visible = fields.Boolean(default=False, related='rut_pj_doc_id.actualizar_visible', store=True,
                                               readonly=False)
    file_data_visible = fields.Boolean(default=False)

    def actualizar_correo_rut_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.rut_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.rut_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.rut_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    rut_pj_doc = fields.Binary(string="RUT Persona Juridica")
    rut_pj_name = fields.Char(string="Nombre del documento RUT PJ", tracking=True)

    rut_pj_doc_visible = fields.Boolean(default=True)
    rut_pj_doc_id = fields.Many2one('drive', string='RUT PJ', tracking=True,
                                    domain="[('stage2_id', '=', drive_file_id)]")

    def action_upload_rut_pj(self):
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
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'rut_pj_documento', 
            },
        }
    

    def subir_rut_pj_a_drive(self):
        """Sube el RUT PJ a Google Drive y lo registra en el modelo drive."""
        if self.rut_pj_doc and self.rut_pj_name:
            _logger.info(f"Subiendo documento '{self.rut_pj_name}' a Google Drive...")
            self._upload_rut_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el RUT PJ para subir.")

    def _upload_rut_pj_to_drive_pj(self):
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
            file_content = base64.b64decode(self.rut_pj_doc)

            original_extension = os.path.splitext(self.rut_pj_name)[1]

            fixed_file_name = f"1_RUT_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.rut_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.rut_pj_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo rut_pj_doc_id
            if not self.rut_pj_doc_id:
                self.rut_pj_doc_id = drive_record.id

            _logger.info(f"Campo rut_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar rut_pj_doc_visible a False después de subir el archivo
            self.rut_pj_doc_visible = False

            _logger.info(f"Campo rut_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    rut_pj_aprueba_f = fields.Boolean(tracking=True, store=True)
    rut_pj_aprueba_c = fields.Boolean(tracking=True, default=True)

    camara_comercio_doc = fields.Binary(string="Camara de comercio")
    camara_comercio_name = fields.Char(string="Nombre del documento Camara de Comercio", tracking=True)
    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_camara_comercio = {}

    def abrir_documento_camara_comercio_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_camara_comercio_en_drive()

        # Primero, verificamos si 'camara_comercio_doc_id' está configurado
        if self.camara_comercio_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Camara de comercio',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.camara_comercio_doc_id.id,
                'target': 'new',
            }

        # Si 'camara_comercio_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.camara_comercio_name:
            file_name_to_search = self.camara_comercio_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_camara_comercio:
                drive_document = self._cache_documentos_camara_comercio[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_camara_comercio[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'camara_comercio_doc_id' con el ID del documento encontrado
                self.write({'camara_comercio_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Camara de comercio',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado de Cámara de Comercio relacionado con este registro.')

    camara_comercio_actualizar_visible = fields.Boolean(default=False,
                                                        related='camara_comercio_doc_id.actualizar_visible', store=True,
                                                        readonly=False)

    def actualizar_correo_camara_comercio_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.camara_comercio_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.camara_comercio_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.camara_comercio_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    camara_comercio_doc_id = fields.Many2one('drive', string='Camara de comercio', tracking=True,
                                             domain="[('stage2_id', '=', drive_file_id)]")
    camara_comercio_doc_visible = fields.Boolean(default=True)

    def action_upload_camara_comercio_pj(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir camara de comercio',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'camara_comercio_pj_documento', 
            },
        }
    



    def subir_camara_comercio_a_drive(self):
        """Sube el documento 'Camara de comercio' a Google Drive y lo registra en el modelo drive."""
        if self.camara_comercio_doc and self.camara_comercio_name:
            _logger.info(f"Subiendo documento '{self.camara_comercio_name}' a Google Drive...")
            self._upload_camara_comercio_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Camara de comercio' para subir.")

    def _upload_camara_comercio_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.camara_comercio_doc)

            original_extension = os.path.splitext(self.camara_comercio_name)[1]

            fixed_file_name = f"2_Camara de Comercio_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.camara_comercio_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.camara_comercio_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo camara_comercio_doc_id
            if not self.camara_comercio_doc_id:
                self.camara_comercio_doc_id = drive_record.id

            _logger.info(f"Campo camara_comercio_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar camara_comercio_doc_visible a False después de subir el archivo
            self.camara_comercio_doc_visible = False

            _logger.info(f"Campo camara_comercio_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    camara_comercio_doc_aprueba_f = fields.Boolean('Aprueba financiera Camara de comercio', tracking=True, store=True)
    camara_comercio_doc_aprueba_c = fields.Boolean('Aprueba Coordinador Camara de comercio', tracking=True,
                                                   default=True)

    planilla_ss_doc = fields.Binary(string="Planilla de seguridad social")
    planilla_ss_name = fields.Char(string="Nombre del documento planilla de seguridad social", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_planilla_ss = {}

    def abrir_documento_planilla_ss_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_planilla_ss_en_drive()

        # Primero, verificamos si 'planilla_ss_doc_id' está configurado
        if self.planilla_ss_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Planilla de seguridad social',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.planilla_ss_doc_id.id,
                'target': 'new',
            }

        # Si 'planilla_ss_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.planilla_ss_name:
            file_name_to_search = self.planilla_ss_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_planilla_ss:
                drive_document = self._cache_documentos_planilla_ss[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_planilla_ss[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'planilla_ss_doc_id' con el ID del documento encontrado
                self.write({'planilla_ss_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Planilla de seguridad social',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Planilla de Seguridad Social relacionada con este registro.')

    planilla_ss_actualizar_visible = fields.Boolean(default=False, related='planilla_ss_doc_id.actualizar_visible',
                                                    store=True, readonly=False)

    def actualizar_correo_planilla_ss_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.planilla_ss_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.planilla_ss_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.planilla_ss_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    planilla_ss_doc_id = fields.Many2one('drive', string='Planilla de seguridad social', tracking=True,
                                         domain="[('stage2_id', '=', drive_file_id)]")
    planilla_ss_doc_visible = fields.Boolean(default=True)

    def action_upload_planilla_ss_pj(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir planilla de seguridad social',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'planilla_ss_pj_documento', 
            },
        }
 
    def subir_planilla_ss_a_drive(self):
        """Sube el documento 'Planilla de seguridad social' a Google Drive y lo registra en el modelo drive."""
        if self.planilla_ss_doc and self.planilla_ss_name:
            _logger.info(f"Subiendo documento '{self.planilla_ss_name}' a Google Drive...")
            self._upload_planilla_ss_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Planilla de seguridad social' para subir.")

    def _upload_planilla_ss_to_drive_pj(self):
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
            file_content = base64.b64decode(self.planilla_ss_doc)

            original_extension = os.path.splitext(self.planilla_ss_name)[1]

            fixed_file_name = f"3_Planilla seguridad social_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.planilla_ss_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.planilla_ss_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo planilla_ss_doc_id
            if not self.planilla_ss_doc_id:
                self.planilla_ss_doc_id = drive_record.id

            _logger.info(f"Campo planilla_ss_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar planilla_ss_doc_visible a False después de subir el archivo
            self.planilla_ss_doc_visible = False

            _logger.info(f"Campo planilla_ss_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    planilla_ss_doc_aprueba_f = fields.Boolean('Aprueba financiera Seguridad social', tracking=True, store=True)
    planilla_ss_doc_aprueba_c = fields.Boolean('Aprueba Coordinador Seguridad social', tracking=True, default=True)

    cert_parafiscales_doc = fields.Binary(string="Certificado Parafiscales")
    cert_parafiscales_name = fields.Char(string="Nombre del documento Cert Parafiscales", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_parafiscales = {}

    def abrir_documento_certificado_parafiscales_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_parafiscales_en_drive()

        # Primero, verificamos si 'cert_parafiscales_doc_id' está configurado
        if self.cert_parafiscales_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Certificado Parafiscales',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.cert_parafiscales_doc_id.id,
                'target': 'new',
            }

        # Si 'cert_parafiscales_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.cert_parafiscales_name:
            file_name_to_search = self.cert_parafiscales_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_parafiscales:
                drive_document = self._cache_documentos_certificado_parafiscales[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_parafiscales[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'cert_parafiscales_doc_id' con el ID del documento encontrado
                self.write({'cert_parafiscales_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Certificado Parafiscales',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Parafiscales relacionado con este registro.')

    cert_parafiscales_actualizar_visible = fields.Boolean(default=False,
                                                          related='cert_parafiscales_doc_id.actualizar_visible',
                                                          store=True, readonly=False)

    def actualizar_correo_certificado_parafiscales_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.cert_parafiscales_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.cert_parafiscales_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.cert_parafiscales_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    cert_parafiscales_doc_id = fields.Many2one('drive', string='Certificado Parafiscales', tracking=True,
                                               domain="[('stage2_id', '=', drive_file_id)]")

    cert_parafiscales_doc_visible = fields.Boolean(default=True)

    def action_upload_certificado_parafiscales_pj(self):
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
                'default_bienes_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'certificado_parafiscales_pj_documento',
            },
        }


    def subir_certificado_parafiscales_a_drive(self):
        """Sube el documento 'Certificado Parafiscales' a Google Drive y lo registra en el modelo drive."""
        if self.cert_parafiscales_doc and self.cert_parafiscales_name:
            _logger.info(f"Subiendo documento '{self.cert_parafiscales_name}' a Google Drive...")
            self._upload_certificado_parafiscales_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Certificado Parafiscales' para subir.")

    def _upload_certificado_parafiscales_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.cert_parafiscales_doc)

            original_extension = os.path.splitext(self.cert_parafiscales_name)[1]

            fixed_file_name = f"4_Certificado parafiscales_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.cert_parafiscales_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.cert_parafiscales_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo cert_parafiscales_doc_id
            if not self.cert_parafiscales_doc_id:
                self.cert_parafiscales_doc_id = drive_record.id

            _logger.info(
                f"Campo cert_parafiscales_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar cert_parafiscales_doc_visible a False después de subir el archivo
            self.cert_parafiscales_doc_visible = False

            _logger.info(f"Campo cert_parafiscales_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    cert_parafiscales_doc_aprueba_f = fields.Boolean('Aprueba financiera Certificado parafiscals', tracking=True,
                                                     store=True)
    cert_parafiscales_doc_aprueba_c = fields.Boolean('Aprueba Coordinador Certificado parafiscales', tracking=True,
                                                     default=True)

    junta_contadores_doc = fields.Binary(string="Junta de contadores")
    junta_contadores_name = fields.Char(string="Nombre del documento Junta de Contadores", tracking=True)
    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_junta_contadores = {}

    def abrir_documento_certificado_junta_contadores_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_junta_contadores_en_drive()

        # Primero, verificamos si 'certificado_junta_contadores_doc_id' está configurado
        if self.certificado_junta_contadores_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Junta contadores',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.certificado_junta_contadores_doc_id.id,
                'target': 'new',
            }

        # Si 'junta_contadores_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.junta_contadores_name:
            file_name_to_search = self.junta_contadores_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_junta_contadores:
                drive_document = self._cache_documentos_certificado_junta_contadores[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_junta_contadores[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'certificado_junta_contadores_doc_id' con el ID del documento encontrado
                self.write({'certificado_junta_contadores_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Junta contadores',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Junta Contadores relacionado con este registro.')

    certificado_junta_contadores_actualizar_visible = fields.Boolean(default=False,
                                                                     related='certificado_junta_contadores_doc_id.actualizar_visible',
                                                                     store=True, readonly=False)

    def actualizar_correo_certificado_junta_contadores_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_junta_contadores_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_junta_contadores_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.certificado_junta_contadores_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    certificado_junta_contadores_doc_id = fields.Many2one('drive', string='Certificado Junta Contadores', tracking=True,
                                                          domain="[('stage2_id', '=', drive_file_id)]")
    certificado_junta_contadores_doc_visible = fields.Boolean(default=True)

    def action_upload_certificado_junta_contadores_pj(self):
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
                'default_bienes_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'certificado_junta_contadores_pj_documento',
            },
        }

    def subir_certificado_junta_contadores_a_drive(self):
        """Sube el documento 'Certificado Junta Contadores' a Google Drive y lo registra en el modelo drive."""
        if self.junta_contadores_doc and self.junta_contadores_name:
            _logger.info(f"Subiendo documento '{self.junta_contadores_name}' a Google Drive...")
            self._upload_certificado_junta_contadores_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Certificado Junta Contadores' para subir.")

    def _upload_certificado_junta_contadores_to_drive_pj(self):
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
            file_content = base64.b64decode(self.junta_contadores_doc)

            original_extension = os.path.splitext(self.junta_contadores_name)[1]

            fixed_file_name = f"6_Junta Contadores_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.junta_contadores_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.junta_contadores_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo certificado_junta_contadores_doc_id
            if not self.certificado_junta_contadores_doc_id:
                self.certificado_junta_contadores_doc_id = drive_record.id

            _logger.info(
                f"Campo certificado_junta_contadores_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar certificado_junta_contadores_doc_visible a False después de subir el archivo
            self.certificado_junta_contadores_doc_visible = False

            _logger.info(f"Campo certificado_junta_contadores_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    junta_contadores_doc_aprueba_f = fields.Boolean(tracking=True, store=True)
    junta_contadores_doc_aprueba_c = fields.Boolean(tracking=True, default=True)

    tp_contador_doc = fields.Binary(string="TP Contador")
    tp_contador_name = fields.Char(string="Nombre del documento Junta de Contadores", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_tp_contador = {}

    def abrir_documento_tp_contador_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_tp_contador_en_drive()

        # Primero, verificamos si 'tp_contador_doc_id' está configurado
        if self.tp_contador_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'TP Contador',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.tp_contador_doc_id.id,
                'target': 'new',
            }

        # Si 'tp_contador_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.tp_contador_name:
            file_name_to_search = self.tp_contador_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_tp_contador:
                drive_document = self._cache_documentos_tp_contador[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_tp_contador[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'tp_contador_doc_id' con el ID del documento encontrado
                self.write({'tp_contador_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'TP Contador',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún TP Contador relacionado con este registro.')

    tp_contador_actualizar_visible = fields.Boolean(default=False, related='tp_contador_doc_id.actualizar_visible',
                                                    store=True, readonly=False)

    def actualizar_correo_tp_contador_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.tp_contador_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.tp_contador_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.tp_contador_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    tp_contador_doc_id = fields.Many2one('drive', string='TP Contador', tracking=True,
                                         domain="[('stage2_id', '=', drive_file_id)]")

    tp_contador_doc_visible = fields.Boolean(default=True)

    def action_upload_tp_contador_pj(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir TP Contador',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'tp_contador_pj_documento',
            },
        }

    def subir_tp_contador_a_drive(self):
        """Sube el documento 'TP Contador' a Google Drive y lo registra en el modelo drive."""
        if self.tp_contador_doc and self.tp_contador_name:
            _logger.info(f"Subiendo documento '{self.tp_contador_name}' a Google Drive...")
            self._upload_tp_contador_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'TP Contador' para subir.")

    def _upload_tp_contador_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.tp_contador_doc)

            original_extension = os.path.splitext(self.tp_contador_name)[1]

            fixed_file_name = f"7_TP Contador_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.tp_contador_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.tp_contador_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo tp_contador_doc_id
            if not self.tp_contador_doc_id:
                self.tp_contador_doc_id = drive_record.id

            _logger.info(f"Campo tp_contador_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar tp_contador_doc_visible a False después de subir el archivo
            self.tp_contador_doc_visible = False

            _logger.info(f"Campo tp_contador_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    tp_contador_doc_aprueba_f = fields.Boolean('Aprueba financiera TP Contador', tracking=True, store=True)
    tp_contador_doc_aprueba_c = fields.Boolean('Aprueba Coordinador TP Contador', tracking=True, default=True)

    certificado_bancario_pj = fields.Binary(string="Certficado Bancario PJ")
    certificado_bancario_pj_name = fields.Char(string="Nombre del documento Cert Bancario PJ", tracking=True)
    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_bancario_pj = {}

    def abrir_documento_certificado_bancario_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_bancario_pj_en_drive()

        # Primero, verificamos si 'certificado_bancario_pj_doc_id' está configurado
        if self.certificado_bancario_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Certificado Bancario PJ',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.certificado_bancario_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'certificado_bancario_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.certificado_bancario_pj_name:
            file_name_to_search = self.certificado_bancario_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_bancario_pj:
                drive_document = self._cache_documentos_certificado_bancario_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_bancario_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'certificado_bancario_pj_doc_id' con el ID del documento encontrado
                self.write({'certificado_bancario_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Certificado Bancario PJ',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Bancario PJ relacionado con este registro.')

    certificado_bancario_pj_actualizar_visible = fields.Boolean(default=False,
                                                                related='certificado_bancario_pj_doc_id.actualizar_visible',
                                                                store=True, readonly=False)

    def actualizar_correo_certificado_bancario_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_bancario_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_bancario_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.certificado_bancario_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    certificado_bancario_pj_doc_id = fields.Many2one('drive', string='Certificado Bancario PJ', tracking=True,
                                                     domain="[('stage2_id', '=', drive_file_id)]")

    certificado_bancario_pj_doc_visible = fields.Boolean(default=True)

    def action_upload_certificado_bancario_pj(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado bancario',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'certificado_bancario_pj_documento',
            },
        }

    def subir_certificado_bancario_pj_a_drive(self):
        """Sube el documento 'Certificado Bancario PJ' a Google Drive y lo registra en el modelo drive."""
        if self.certificado_bancario_pj and self.certificado_bancario_pj_name:
            _logger.info(f"Subiendo documento '{self.certificado_bancario_pj_name}' a Google Drive...")
            self._upload_certificado_bancario_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Certificado Bancario PJ' para subir.")

    def _upload_certificado_bancario_pj_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.certificado_bancario_pj)

            original_extension = os.path.splitext(self.certificado_bancario_pj_name)[1]

            fixed_file_name = f"8_Certificado Bancario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.certificado_bancario_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.certificado_bancario_pj,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo certificado_bancario_pj_doc_id
            if not self.certificado_bancario_pj_doc_id:
                self.certificado_bancario_pj_doc_id = drive_record.id

            _logger.info(
                f"Campo certificado_bancario_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar certificado_bancario_pj_doc_visible a False después de subir el archivo
            self.certificado_bancario_pj_doc_visible = False

            _logger.info(f"Campo certificado_bancario_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    certificado_bancario_pj_aprueba_f = fields.Boolean('Aprueba financiera Certificado financiero PJ', tracking=True,
                                                       store=True)
    certificado_bancario_pj_aprueba_c = fields.Boolean('Aprueba Coordinador Certificado financiero PJ', tracking=True,
                                                       default=True)

    resolucion_facturacion_doc = fields.Binary(string="Resolucion Facturacion")
    resolucion_facturacion_name = fields.Char(string="Nombre del documento resolucion facturacion", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_resolucion_facturacion = {}

    def abrir_documento_resolucion_facturacion_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_resolucion_facturacion_en_drive()

        # Primero, verificamos si 'resolucion_facturacion_doc_id' está configurado
        if self.resolucion_facturacion_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Resolucion Facturacion',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.resolucion_facturacion_doc_id.id,
                'target': 'new',
            }

        # Si 'resolucion_facturacion_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.resolucion_facturacion_name:
            file_name_to_search = self.resolucion_facturacion_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_resolucion_facturacion:
                drive_document = self._cache_documentos_resolucion_facturacion[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_resolucion_facturacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'resolucion_facturacion_doc_id' con el ID del documento encontrado
                self.write({'resolucion_facturacion_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Resolucion Facturacion',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Resolucion Facturacion relacionada con este registro.')

    resolucion_facturacion_actualizar_visible = fields.Boolean(default=False,
                                                               related='resolucion_facturacion_doc_id.actualizar_visible',
                                                               store=True, readonly=False)

    def actualizar_correo_resolucion_facturacion_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.resolucion_facturacion_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.resolucion_facturacion_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.resolucion_facturacion_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    resolucion_facturacion_doc_id = fields.Many2one('drive', string='Resolucion Facturacion', tracking=True,
                                                    domain="[('stage2_id', '=', drive_file_id)]")

    resolucion_facturacion_doc_visible = fields.Boolean(default=True)

    def action_upload_resolucion_facturacion_pj(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir resolucion facturacion',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'resolucion_facturacion_pj_documento',
            },
        }


    def subir_resolucion_facturacion_a_drive(self):
        """Sube el documento 'Resolucion Facturacion' a Google Drive y lo registra en el modelo drive."""
        if self.resolucion_facturacion_doc and self.resolucion_facturacion_name:
            _logger.info(f"Subiendo documento '{self.resolucion_facturacion_name}' a Google Drive...")
            self._upload_resolucion_facturacion_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Resolucion Facturacion' para subir.")

    def _upload_resolucion_facturacion_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.resolucion_facturacion_doc)

            original_extension = os.path.splitext(self.resolucion_facturacion_name)[1]

            fixed_file_name = f"9_Resolucion Facturacion_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.resolucion_facturacion_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.resolucion_facturacion_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo resolucion_facturacion_doc_id
            if not self.resolucion_facturacion_doc_id:
                self.resolucion_facturacion_doc_id = drive_record.id

            _logger.info(
                f"Campo resolucion_facturacion_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar resolucion_facturacion_doc_visible a False después de subir el archivo
            self.resolucion_facturacion_doc_visible = False

            _logger.info(f"Campo resolucion_facturacion_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    resolucion_facturacion_doc_aprueba_f = fields.Boolean('Aprueba financiera resolucion facturacion', tracking=True,
                                                          store=True)
    resolucion_facturacion_doc_aprueba_c = fields.Boolean('Aprueba Coordinador resolucion facturacion', tracking=True,
                                                          default=True)

    documento_rpc_pj = fields.Binary(string="Documento RPC")
    documento_rpc_pj_name = fields.Char(string="Nombre del documento RPC", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_documento_rpc_pj = {}

    def abrir_documento_documento_rpc_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_documento_rpc_pj_en_drive()

        # Primero, verificamos si 'documento_rpc_pj_doc_id' está configurado
        if self.documento_rpc_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Documento RPC',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.documento_rpc_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'documento_rpc_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_rpc_pj_name:
            file_name_to_search = self.documento_rpc_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_rpc_pj:
                drive_document = self._cache_documentos_documento_rpc_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_rpc_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_rpc_pj_doc_id' con el ID del documento encontrado
                self.write({'documento_rpc_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Documento RPC',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Documento RPC PJ relacionado con este registro.')

    documento_rpc_pj_actualizar_visible = fields.Boolean(default=False,
                                                         related='documento_rpc_pj_doc_id.actualizar_visible',
                                                         store=True, readonly=False)

    def actualizar_correo_documento_rpc_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.documento_rpc_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.documento_rpc_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.documento_rpc_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    documento_rpc_pj_doc_id = fields.Many2one('drive', string='Documento RPC PJ', tracking=True,
                                              domain="[('stage2_id', '=', drive_file_id)]")

    documento_rpc_pj_doc_visible = fields.Boolean(default=True)

    def action_upload_documento_rpc_pj(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'documento_rpc_pj_documento',
            },
        }

    def subir_documento_rpc_pj_a_drive(self):
        """Sube el documento 'Documento RPC PJ' a Google Drive y lo registra en el modelo drive."""
        if self.documento_rpc_pj and self.documento_rpc_pj_name:
            _logger.info(f"Subiendo documento '{self.documento_rpc_pj_name}' a Google Drive...")
            self._upload_documento_rpc_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Documento RPC PJ' para subir.")

    def _upload_documento_rpc_pj_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.documento_rpc_pj)

            original_extension = os.path.splitext(self.documento_rpc_pj_name)[1]

            fixed_file_name = f"10_RPC_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.documento_rpc_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.documento_rpc_pj,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo documento_rpc_pj_doc_id
            if not self.documento_rpc_pj_doc_id:
                self.documento_rpc_pj_doc_id = drive_record.id

            _logger.info(
                f"Campo documento_rpc_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar documento_rpc_pj_doc_visible a False después de subir el archivo
            self.documento_rpc_pj_doc_visible = False

            _logger.info(f"Campo documento_rpc_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    documento_rpc_pj_aprueba_f = fields.Boolean('Aprueba financiera RPC', tracking=True, store=True)
    documento_rpc_pj_aprueba_c = fields.Boolean('Aprueba Coordinador RPC', tracking=True, default=False)

    factura_cuenta_pj = fields.Binary(string="Factura o Cuenta")
    factura_cuenta_pj_name = fields.Char(string="Nombre del documento Factura o cuenta", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_factura_cuenta_pj = {}

    def abrir_documento_factura_cuenta_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_factura_cuenta_pj_en_drive()

        # Primero, verificamos si 'factura_cuenta_pj_doc_id' está configurado
        if self.factura_cuenta_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Factura o Cuenta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.factura_cuenta_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.factura_cuenta_pj_name:
            file_name_to_search = self.factura_cuenta_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_factura_cuenta_pj:
                drive_document = self._cache_documentos_factura_cuenta_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_factura_cuenta_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'factura_cuenta_pj_doc_id' con el ID del documento encontrado
                self.write({'factura_cuenta_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Factura o Cuenta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Factura o Cuenta PJ relacionada con este registro.')

    factura_cuenta_pj_actualizar_visible = fields.Boolean(default=False,
                                                          related='factura_cuenta_pj_doc_id.actualizar_visible',
                                                          store=True, readonly=False)

    def actualizar_correo_factura_cuenta_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.factura_cuenta_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.factura_cuenta_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.factura_cuenta_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    factura_cuenta_pj_doc_id = fields.Many2one('drive', string='Factura o Cuenta PJ', tracking=True,
                                               domain="[('stage2_id', '=', drive_file_id)]")

    factura_cuenta_pj_doc_visible = fields.Boolean(default=True)

    def action_upload_factura_cuenta_pj(self):
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
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'factura_cuenta_pj_documento', 
            },
        }
    

    def subir_factura_cuenta_pj_a_drive(self):
        """Sube el documento 'Factura o Cuenta PJ' a Google Drive y lo registra en el modelo drive."""
        if self.factura_cuenta_pj and self.factura_cuenta_pj_name:
            _logger.info(f"Subiendo documento '{self.factura_cuenta_pj_name}' a Google Drive...")
            self._upload_factura_cuenta_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Factura o Cuenta PJ' para subir.")

    def _upload_factura_cuenta_pj_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.factura_cuenta_pj)

            original_extension = os.path.splitext(self.factura_cuenta_pj_name)[1]

            fixed_file_name = f"5_Factura o Cuenta_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.factura_cuenta_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.factura_cuenta_pj,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo factura_cuenta_pj_doc_id
            if not self.factura_cuenta_pj_doc_id:
                self.factura_cuenta_pj_doc_id = drive_record.id

            _logger.info(
                f"Campo factura_cuenta_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar factura_cuenta_pj_doc_visible a False después de subir el archivo
            self.factura_cuenta_pj_doc_visible = False

            _logger.info(f"Campo factura_cuenta_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    factura_cuenta_pj_aprueba_f = fields.Boolean('Aprueba financiera Factura o cuenta', tracking=True, store=True)
    factura_cuenta_pj_aprueba_c = fields.Boolean('Aprueba Coordinador Factura o cuenta', tracking=True, default=False)

    ######################### DOCUMENTOS PARA PERSONA NATURAL #################################
    anexo_tributario_pn = fields.Boolean(string='Anexo tributario', tracking=True)
    Anexo_tributario_pn = fields.Binary(string="Anexo tributario")
    Anexo_tributario_name_pn = fields.Char(string="Nombre del documento Anexo tributario", tracking=True)
    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_anexo_tributario_pn = {}

    def abrir_documento_anexo_tributario_pn_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_anexo_tributario_pn_en_drive()

        # Primero, verificamos si 'Anexo_tributario_doc_id' está configurado
        if self.Anexo_tributario_doc_pn_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Anexo tributario',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.Anexo_tributario_doc_pn_id.id,
                'target': 'new',
            }

        # Si 'Anexo_tributario_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.Anexo_tributario_name_pn:
            file_name_to_search = self.Anexo_tributario_name_pn.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_anexo_tributario_pn:
                drive_document = self._cache_documentos_anexo_tributario_pn[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_anexo_tributario_pn[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'Anexo_tributario_doc_id' con el ID del documento encontrado
                self.write({'Anexo_tributario_doc_pn_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Anexo tributario',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')
        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Anexo tributario relacionado con este registro.')

    Anexo_tributario_pn_visible = fields.Boolean(default=False, related='Anexo_tributario_doc_pn_id.actualizar_visible',
                                                 store=True,
                                                 readonly=False)

    def actualizar_correo_anexo_tributario_pn_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.Anexo_tributario_doc_pn_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.Anexo_tributario_doc_pn_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.Anexo_tributario_pn_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    Anexo_tributario_doc_pn_id = fields.Many2one('drive', string='Anexo tributario', tracking=True,
                                                 domain="[('stage2_id', '=', drive_file_id)]")
    Anexo_tributario_doc_pn_visible = fields.Boolean(default=True)

    def action_upload_Anexo_tributario(self):
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
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'Anexo_tributario_pn', 
            },
        }


    def subir_anexo_tributario_pn_a_drive(self):
        """Sube el Anexo tributario a Google Drive y lo registra en el modelo drive."""
        if self.Anexo_tributario_pn and self.Anexo_tributario_name_pn:
            _logger.info(f"Subiendo documento '{self.Anexo_tributario_name_pn}' a Google Drive...")
            self._upload_anexo_tributario_pn_to_drive()
        else:
            _logger.warning("No se encontró el Anexo tributario para subir.")

    def _upload_anexo_tributario_pn_to_drive(self):
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
            file_content = base64.b64decode(self.Anexo_tributario_pn)
            original_extension = os.path.splitext(self.Anexo_tributario_name_pn)[1]
            fixed_file_name = f"Anexo_tributario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.Anexo_tributario_name_pn} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.Anexo_tributario_pn,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Solo actualizar el campo Anexo_tributario_doc_id
            if not self.Anexo_tributario_doc_pn_id:
                self.Anexo_tributario_doc_pn_id = drive_record.id

            _logger.info(
                f"Campo Anexo_tributario_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar Anexo_tributario_doc_pn_visible a False después de subir el archivo
            self.Anexo_tributario_doc_pn_visible = False

            _logger.info(f"Campo Anexo_tributario_doc_pn_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    anexo_tributario_pn_aprueba_f = fields.Boolean(string="Anexo tributario aprobado por Coordinador", default=False)
    anexo_tributario_pn_aprueba_c = fields.Boolean(string="Anexo tributario aprobado por Financiero", default=False)

    rut_pn = fields.Binary(string="RUT Persona Natural")
    rut_pn_name = fields.Char(string="Nombre del documento RUT PN", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_rut_pn = {}

    def abrir_documento_rut_pn_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_rut_pn_en_drive()

        # Primero, verificamos si 'rut_pn_doc_id' está configurado
        if self.rut_pn_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'RUT',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.rut_pn_doc_id.id,
                'target': 'new',
            }

        # Si 'rut_pn_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rut_pn_name:
            file_name_to_search = self.rut_pn_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rut_pn:
                drive_document = self._cache_documentos_rut_pn[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_rut_pn[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rut_pn_doc_id' con el ID del documento encontrado
                self.write({'rut_pn_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'RUT',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún RUT Persona Natural relacionado con este registro.')

    rut_pn_actualizar_visible = fields.Boolean(default=False, related='rut_pn_doc_id.actualizar_visible', store=True,
                                               readonly=False)

    def actualizar_correo_rut_pn_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.rut_pn_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.rut_pn_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.rut_pn_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    rut_pn_doc_id = fields.Many2one('drive', string='RUT Persona Natural', tracking=True,
                                    domain="[('stage2_id', '=', drive_file_id)]")

    rut_pn_doc_visible = fields.Boolean(default=True)

    def action_upload_rut(self):
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
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'rut_natural', 
            },
        }

    def subir_rut_pn_a_drive(self):
        """Sube el documento 'RUT Persona Natural' a Google Drive y lo registra en el modelo drive."""
        if self.rut_pn and self.rut_pn_name:
            _logger.info(f"Subiendo documento '{self.rut_pn_name}' a Google Drive...")
            self._upload_rut_pn_to_drive()
        else:
            _logger.warning("No se encontró el documento 'RUT Persona Natural' para subir.")

    def _upload_rut_pn_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.rut_pn)

            original_extension = os.path.splitext(self.rut_pn_name)[1]

            fixed_file_name = f"1_RUT_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.rut_pn_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.rut_pn,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo rut_pn_doc_id
            if not self.rut_pn_doc_id:
                self.rut_pn_doc_id = drive_record.id

            _logger.info(f"Campo rut_pn_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar rut_pn_doc_visible a False después de subir el archivo
            self.rut_pn_doc_visible = False

            _logger.info(f"Campo rut_pn_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    rut_pn_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', tracking=True, default=True)
    rut_pn_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)

    documento_identidad = fields.Binary(string="Documento Identidad")
    documento_identidad_name = fields.Char(string="Nombre del documento CEDULA", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_documento_identidad = {}

    def abrir_documento_documento_identidad_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_documento_identidad_en_drive()

        # Primero, verificamos si 'documento_identidad_doc_id' está configurado
        if self.documento_identidad_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Documento Identidad',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.documento_identidad_doc_id.id,
                'target': 'new',
            }

        # Si 'documento_identidad_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_identidad_name:
            file_name_to_search = self.documento_identidad_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_identidad:
                drive_document = self._cache_documentos_documento_identidad[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_identidad[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_identidad_doc_id' con el ID del documento encontrado
                self.write({'documento_identidad_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Documento Identidad',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Documento Identidad relacionado con este registro.')

    documento_identidad_actualizar_visible = fields.Boolean(default=False,
                                                            related='documento_identidad_doc_id.actualizar_visible',
                                                            store=True, readonly=False)

    def actualizar_correo_documento_identidad_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.documento_identidad_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.documento_identidad_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.documento_identidad_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    documento_identidad_doc_id = fields.Many2one('drive', string='Documento Identidad', tracking=True,
                                                 domain="[('stage2_id', '=', drive_file_id)]")

    documento_identidad_doc_visible = fields.Boolean(default=True)

    def action_upload_documento_identidad(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir documento identidad',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'documento_identidad_pn', 
            },
        }

    def subir_documento_identidad_a_drive(self):
        """Sube el documento 'Documento Identidad' a Google Drive y lo registra en el modelo drive."""
        if self.documento_identidad and self.documento_identidad_name:
            _logger.info(f"Subiendo documento '{self.documento_identidad_name}' a Google Drive...")
            self._upload_documento_identidad_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Documento Identidad' para subir.")

    def _upload_documento_identidad_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.documento_identidad)

            original_extension = os.path.splitext(self.documento_identidad_name)[1]

            fixed_file_name = f"4_Documento Identidad_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.documento_identidad_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.documento_identidad,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo documento_identidad_doc_id
            if not self.documento_identidad_doc_id:
                self.documento_identidad_doc_id = drive_record.id

            _logger.info(
                f"Campo documento_identidad_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar documento_identidad_doc_visible a False después de subir el archivo
            self.documento_identidad_doc_visible = False

            _logger.info(f"Campo documento_identidad_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    documento_identidad_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', tracking=True,
                                                       default=True)
    documento_identidad_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True,
                                                       store=True)

    planilla = fields.Binary(string="Planilla")
    planilla_name = fields.Char(string="Nombre del documento Planilla", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_planilla = {}

    def abrir_documento_planilla_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_planilla_en_drive()

        # Primero, verificamos si 'planilla_doc_id' está configurado
        if self.planilla_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Planilla',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.planilla_doc_id.id,
                'target': 'new',
            }

        # Si 'planilla_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.planilla_name:
            file_name_to_search = self.planilla_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_planilla:
                drive_document = self._cache_documentos_planilla[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_planilla[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'planilla_doc_id' con el ID del documento encontrado
                self.write({'planilla_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Planilla',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Planilla relacionada con este registro.')

    planilla_actualizar_visible = fields.Boolean(default=False, related='planilla_doc_id.actualizar_visible',
                                                 store=True, readonly=False)

    def actualizar_correo_planilla_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.planilla_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.planilla_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.planilla_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    planilla_doc_id = fields.Many2one('drive', string='Planilla', tracking=True,
                                      domain="[('stage2_id', '=', drive_file_id)]")

    planilla_doc_visible = fields.Boolean(default=True)

    def action_upload_planilla(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir planilla',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'planilla_natural', 
            },
        }

    def subir_planilla_a_drive(self):
        """Sube el documento 'Planilla' a Google Drive y lo registra en el modelo drive."""
        if self.planilla and self.planilla_name:
            _logger.info(f"Subiendo documento '{self.planilla_name}' a Google Drive...")
            self._upload_planilla_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Planilla' para subir.")

    def _upload_planilla_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.planilla)

            original_extension = os.path.splitext(self.planilla_name)[1]

            fixed_file_name = f"2_Planilla_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.planilla_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.planilla,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo planilla_doc_id
            if not self.planilla_doc_id:
                self.planilla_doc_id = drive_record.id

            _logger.info(f"Campo planilla_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar planilla_doc_visible a False después de subir el archivo
            self.planilla_doc_visible = False

            _logger.info(f"Campo planilla_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    planilla_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', tracking=True, default=True)
    planilla_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)

    certificado_bancario = fields.Binary(string="Certficado Bancario")
    certificado_bancario_name = fields.Char(string="Nombre del documento Cert Bancario", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_bancario = {}

    def abrir_documento_certificado_bancario_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_bancario_en_drive()

        # Primero, verificamos si 'certificado_bancario_doc_id' está configurado
        if self.certificado_bancario_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Certificado Bancario',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.certificado_bancario_doc_id.id,
                'target': 'new',
            }

        # Si 'certificado_bancario_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.certificado_bancario_name:
            file_name_to_search = self.certificado_bancario_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_bancario:
                drive_document = self._cache_documentos_certificado_bancario[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_bancario[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'certificado_bancario_doc_id' con el ID del documento encontrado
                self.write({'certificado_bancario_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Certificado Bancario',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Bancario relacionado con este registro.')

    certificado_bancario_actualizar_visible = fields.Boolean(default=False,
                                                             related='certificado_bancario_doc_id.actualizar_visible',
                                                             store=True, readonly=False)

    def actualizar_correo_certificado_bancario_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_bancario_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_bancario_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.certificado_bancario_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    certificado_bancario_doc_id = fields.Many2one('drive', string='Certificado Bancario', tracking=True,
                                                  domain="[('stage2_id', '=', drive_file_id)]")

    certificado_bancario_doc_visible = fields.Boolean(default=True)

    def action_upload_certificado_bancario(self):
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
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'certificado_bancario_pn', 
            },
        }


    def subir_certificado_bancario_a_drive(self):
        """Sube el documento 'Certificado Bancario' a Google Drive y lo registra en el modelo drive."""
        if self.certificado_bancario and self.certificado_bancario_name:
            _logger.info(f"Subiendo documento '{self.certificado_bancario_name}' a Google Drive...")
            self._upload_certificado_bancario_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Certificado Bancario' para subir.")

    def _upload_certificado_bancario_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.certificado_bancario)

            original_extension = os.path.splitext(self.certificado_bancario_name)[1]

            fixed_file_name = f"5_Certificado Bancario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.certificado_bancario_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.certificado_bancario,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo certificado_bancario_doc_id
            if not self.certificado_bancario_doc_id:
                self.certificado_bancario_doc_id = drive_record.id

            _logger.info(
                f"Campo certificado_bancario_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar certificado_bancario_doc_visible a False después de subir el archivo
            self.certificado_bancario_doc_visible = False

            _logger.info(f"Campo certificado_bancario_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    certificado_bancario_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', tracking=True,
                                                        default=True)
    certificado_bancario_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True,
                                                        store=True)

    documento_rpc = fields.Binary(string="Documento RPC")
    documento_rpc_name = fields.Char(string="Nombre del documento RPC", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_documento_rpc = {}

    def abrir_documento_documento_rpc_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_documento_rpc_en_drive()

        # Primero, verificamos si 'documento_rpc_doc_id' está configurado
        if self.documento_rpc_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Documento RPC',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.documento_rpc_doc_id.id,
                'target': 'new',
            }

        # Si 'documento_rpc_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_rpc_name:
            file_name_to_search = self.documento_rpc_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_rpc:
                drive_document = self._cache_documentos_documento_rpc[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_rpc[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_rpc_doc_id' con el ID del documento encontrado
                self.write({'documento_rpc_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Documento RPC',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error

        raise UserError('No hay ningún Documento RPC relacionado con este registro.')

    documento_rpc_actualizar_visible = fields.Boolean(default=False, related='documento_rpc_doc_id.actualizar_visible',
                                                      store=True, readonly=False)

    def actualizar_correo_documento_rpc_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.documento_rpc_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.documento_rpc_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.documento_rpc_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    documento_rpc_doc_id = fields.Many2one('drive', string='Documento RPC', tracking=True,
                                           domain="[('stage2_id', '=', drive_file_id)]")

    documento_rpc_doc_visible = fields.Boolean(default=True)

    def action_upload_documento_rpc(self):
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
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'documento_rpc_natural', 
            },
        }

    def subir_documento_rpc_a_drive(self):
        """Sube el documento 'Documento RPC' a Google Drive y lo registra en el modelo drive."""
        if self.documento_rpc and self.documento_rpc_name:
            _logger.info(f"Subiendo documento '{self.documento_rpc_name}' a Google Drive...")
            self._upload_documento_rpc_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Documento RPC' para subir.")

    def _upload_documento_rpc_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.documento_rpc)

            original_extension = os.path.splitext(self.documento_rpc_name)[1]

            fixed_file_name = f"6_RPC_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.documento_rpc_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.documento_rpc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo documento_rpc_doc_id
            if not self.documento_rpc_doc_id:
                self.documento_rpc_doc_id = drive_record.id

            _logger.info(f"Campo documento_rpc_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar documento_rpc_doc_visible a False después de subir el archivo
            self.documento_rpc_doc_visible = False

            _logger.info(f"Campo documento_rpc_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True
    
        # Valores booleanos para APROBACION
    doc_rpc_aprueba_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', tracking=True, default=False)
    doc_rpc_aprueba_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)

    factura_cuenta = fields.Binary(string="Factura o Cuenta")
    factura_cuenta_name = fields.Char(string="Nombre del documento Factura o cuenta", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda 
    _cache_documentos_factura_cuenta = {}

    def abrir_documento_factura_cuenta_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_factura_cuenta_en_drive()

        # Primero, verificamos si 'factura_cuenta_doc_id' está configurado
        if self.factura_cuenta_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Factura o Cuenta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.factura_cuenta_doc_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.factura_cuenta_name:
            file_name_to_search = self.factura_cuenta_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_factura_cuenta:
                drive_document = self._cache_documentos_factura_cuenta[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_factura_cuenta[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'factura_cuenta_doc_id' con el ID del documento encontrado
                self.write({'factura_cuenta_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Factura o Cuenta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error

        raise UserError('No hay ninguna Factura o Cuenta relacionada con este registro.')

    factura_cuenta_actualizar_visible = fields.Boolean(default=False,
                                                       related='factura_cuenta_doc_id.actualizar_visible', store=True,
                                                       readonly=False)

    def actualizar_correo_factura_cuenta_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.factura_cuenta_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.factura_cuenta_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.factura_cuenta_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    factura_cuenta_doc_id = fields.Many2one('drive', string='Factura o Cuenta', tracking=True,
                                            domain="[('stage2_id', '=', drive_file_id)]")

    factura_cuenta_doc_visible = fields.Boolean(default=True)

    def action_upload_factura_cuenta(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir factura o cuenta',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_bienes_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'factura_o_cuenta', 
            },
        }

    def subir_factura_cuenta_a_drive(self):
        """Sube el documento 'Factura o Cuenta' a Google Drive y lo registra en el modelo drive."""
        if self.factura_cuenta and self.factura_cuenta_name:
            _logger.info(f"Subiendo documento '{self.factura_cuenta_name}' a Google Drive...")
            self._upload_factura_cuenta_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Factura o Cuenta' para subir.")

    def _upload_factura_cuenta_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.factura_cuenta)

            original_extension = os.path.splitext(self.factura_cuenta_name)[1]

            fixed_file_name = f"3_Factura o Cuenta_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.factura_cuenta_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.factura_cuenta,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo factura_cuenta_doc_id
            if not self.factura_cuenta_doc_id:
                self.factura_cuenta_doc_id = drive_record.id

            _logger.info(f"Campo factura_cuenta_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar factura_cuenta_doc_visible a False después de subir el archivo
            self.factura_cuenta_doc_visible = False

            _logger.info(f"Campo factura_cuenta_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True
    
    # Valores booleanos para APROBACION
    factura_cuenta_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', tracking=True, default=False)
    factura_cuenta_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)


    archivo_adjunto = fields.Binary(string="Archivo Adjunto", attachment=True)
    archivo_adjunto_name = fields.Char(string="Archivo Adjunto", tracking=True)


    archivo_prueba = fields.Binary(string="Archivo prueba")
    archivo_prueba_name = fields.Char(string="Archivo pruba name")

    ################### Seleccionador de Todos para Coordinacion PJ ##########################
    selecionar_todos_coord = fields.Boolean('Seleccionar todos PJ', tracking=True)

    @api.onchange('selecionar_todos_coord')
    def _onchange_selecionar_todos_coord(self):
        if self.selecionar_todos_coord:

            self.rut_pj_aprueba_c = True
            self.camara_comercio_doc_aprueba_c = True
            self.planilla_ss_doc_aprueba_c = True
            self.cert_parafiscales_doc_aprueba_c = True
            self.junta_contadores_doc_aprueba_c = True
            self.tp_contador_doc_aprueba_c = True
            self.certificado_bancario_pj_aprueba_c = True
            self.resolucion_facturacion_doc_aprueba_c = True
            self.documento_rpc_pj_aprueba_c = True
            self.factura_cuenta_pj_aprueba_c = True
        else:
            self.rut_pj_aprueba_c = False
            self.camara_comercio_doc_aprueba_c = False
            self.planilla_ss_doc_aprueba_c = False
            self.cert_parafiscales_doc_aprueba_c = False
            self.junta_contadores_doc_aprueba_c = False
            self.tp_contador_doc_aprueba_c = False
            self.certificado_bancario_pj_aprueba_c = False
            self.resolucion_facturacion_doc_aprueba_c = False
            self.documento_rpc_pj_aprueba_c = False
            self.factura_cuenta_pj_aprueba_c = False

    ################### Seleccionador de Todos para Financiera PJ ##########################

    selecionar_todos_fin = fields.Boolean('Seleccionar todos PJ', tracking=True)

    @api.onchange('selecionar_todos_fin')
    def _onchange_selecionar_todos_fin(self):
        if self.selecionar_todos_fin:

            self.rut_pj_aprueba_f = True
            self.camara_comercio_doc_aprueba_f = True
            self.planilla_ss_doc_aprueba_f = True
            self.cert_parafiscales_doc_aprueba_f = True
            self.junta_contadores_doc_aprueba_f = True
            self.tp_contador_doc_aprueba_f = True
            self.certificado_bancario_pj_aprueba_f = True
            self.resolucion_facturacion_doc_aprueba_f = True
            self.documento_rpc_pj_aprueba_f = True
            self.factura_cuenta_pj_aprueba_f = True
        else:
            self.rut_pj_aprueba_f = False
            self.camara_comercio_doc_aprueba_f = False
            self.planilla_ss_doc_aprueba_f = False
            self.cert_parafiscales_doc_aprueba_f = False
            self.junta_contadores_doc_aprueba_f = False
            self.tp_contador_doc_aprueba_f = False
            self.certificado_bancario_pj_aprueba_f = False
            self.resolucion_facturacion_doc_aprueba_f = False
            self.documento_rpc_pj_aprueba_f = False
            self.factura_cuenta_pj_aprueba_f = False

    ################### Seleccionador de Todos para Coordinacion PN ##########################
    selecionar_todos_coord_pn = fields.Boolean('Seleccionar todos PN', tracking=True)

    @api.onchange('selecionar_todos_coord_pn')
    def _onchange_selecionar_todos_coord_pn(self):
        if self.selecionar_todos_coord_pn:

            self.rut_pn_aprueba_doc_c = True
            self.documento_identidad_aprueba_doc_c = True
            self.planilla_aprueba_doc_c = True
            self.certificado_bancario_aprueba_doc_c = True
            self.doc_rpc_aprueba_c = True
            self.factura_cuenta_aprueba_doc_c = True

        else:
            self.rut_pn_aprueba_doc_c = False
            self.documento_identidad_aprueba_doc_c = False
            self.planilla_aprueba_doc_c = False
            self.certificado_bancario_aprueba_doc_c = False
            self.doc_rpc_aprueba_c = False
            self.factura_cuenta_aprueba_doc_c = False

    ################### Seleccionador de Todos para Financiero PN ##########################
    seleccionar_todos_finan_pn = fields.Boolean('Seleccionar todos PN', tracking=True)

    @api.onchange('seleccionar_todos_finan_pn')
    def _onchange_seleccionar_todos_finan_pn(self):
        if self.seleccionar_todos_finan_pn:

            self.rut_pn_aprueba_doc_f = False
            self.documento_identidad_aprueba_doc_f = False
            self.planilla_aprueba_doc_f = False
            self.certificado_bancario_aprueba_doc_f = False
            self.doc_rpc_aprueba_f = False
            self.factura_cuenta_aprueba_doc_f = False
        else:
            self.rut_pn_aprueba_doc_f = False
            self.documento_identidad_aprueba_doc_f = False
            self.planilla_aprueba_doc_f = False
            self.certificado_bancario_aprueba_doc_f = False
            self.doc_rpc_aprueba_f = False
            self.factura_cuenta_aprueba_doc_f = False

    ################### Seleccionador de Todos para Coordinacion ##########################
    seleccion_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica')
    ],tracking=True)

    ################### Observacion del Coordinador ##########################
    observacion_coordinador = fields.Text('Observaciones: ', tracking=True)

    ################### Observacion del Financiero ##########################
    observacion_financiera = fields.Text('Observaciones: ', tracking=True)

    ################### Campo para rastrear la aprobación del financiero
    financiera_aprobado = fields.Boolean(string="Aprobado por Financiera", default=False)

    #################### BOTONES DE APROBACION ####################
    def no_aprobado_financiera(self):
        # Cambiar el estado a "Coordinador" y marcar como no aprobado
        self.write({
            'estado_compra_bienes': 'coordinador',
            'financiera_aprobado': False,  # Marcar como no aprobado
        })

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_c

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo "group_contratacion_coordinador" que están relacionados con el proyecto
        usuarios_coordinadores = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id or self.env.ref('Matriz.group_contratacion_asis_coordinador') in user.groups_id
        )
        # Verificar si hay usuarios coordinadores relacionados con el proyecto
        if not usuarios_coordinadores:
            raise UserError("No hay usuarios coordinadores asociados al proyecto.")

        # Obtener el usuario financiero actual
        financiero = self.env.user

        # Obtener las observaciones (si las hay)
        observacion_financiera = self.observacion_financiera or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_c.name, self.proyecto_relacionado_c.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # # Obtener el nombre del registro
        nombre_registro = self.name # Usa el campo que almacena el nombre del registro

        # Construir el mensaje del correo
        mensaje = f"""
            <p><strong>{nombre_proyecto}</strong></p>
            <p>Hola, coordinador:</p>
            <p>El financiero <strong>no aprobó</strong> el documento orden de compra de bienes. Proveedor <strong>{nombre_registro}</strong>. Por favor, revisarlo.</p>
            {f'<p><strong>Observaciones:</strong> {observacion_financiera}</p>' if observacion_financiera else ''}
        """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_bienes'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario coordinador
        for coordinador in usuarios_coordinadores:
            try:
                body_html = _orden_compra_email_body(
                    "Documento orden de compra de bienes no aprobado",
                    "coordinador",
                    f"""
                        <p>El financiero <strong>no aprobó</strong> el documento orden de compra de bienes. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Por favor, revisarlo.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_financiera,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Documento orden de compra de bienes no fue aprobado. Proveedor {nombre_registro}",
                    'email_from': financiero.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': coordinador.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }

    def aprobar_orden_financiera(self):
        # Cambiar el estado a "Financiera"
        self.write({'estado_compra_bienes': 'financiera'})

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_c

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo DESEADOS que están relacionados con el proyecto
        usuarios_financieros = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id or
                         self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id
        )

        # Verificar si hay usuarios financieros relacionados con el proyecto
        if not usuarios_financieros:
            raise UserError("No hay usuarios financieros asociados al proyecto.")

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones del Coordinadorl (si las hay)
        observacion_coordinador = self.observacion_coordinador or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_c.name, self.proyecto_relacionado_c.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener el nombre del registro
        nombre_registro = self.name  # Usa el campo que almacena el nombre del registro


        # Construir el mensaje del correo
        mensaje = f"""  
            <p><strong>{nombre_proyecto}</strong></p>
            <p>Hola, financiero:</p>
            <p>El coordinador ha <strong>aprobado</strong> el documento orden de compra de bienes. Proveedor <strong>{nombre_registro}</strong>. Por favor, revisarlo.</p>
            {f'<p><strong>Observaciones:</strong> {observacion_coordinador}</p>' if observacion_coordinador else ''}
        """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_bienes'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario financiero
        for financiero in usuarios_financieros:
            try:
                body_html = _orden_compra_email_body(
                    "Documento orden de compra de bienes aprobado",
                    "financiero",
                    f"""
                        <p>El coordinador ha <strong>aprobado</strong> el documento orden de compra de bienes. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Por favor, revisarlo.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_coordinador,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Documento orden de compra de bienes fue aprobado. Proveedor {nombre_registro}",
                    'email_from': coordinador.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': financiero.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }


    def aprobar_orden_coordinador(self):
        # Cambiar el estado a "Coordinador"
        self.write({
            'estado_compra_bienes': 'coordinador',
            'financiera_aprobado': True,  # Marcar como aprobado
        })

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_c

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo "group_contratacion_coordinador" que están relacionados con el proyecto
        usuarios_coordinadores = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id or self.env.ref('Matriz.group_contratacion_asis_coordinador') in user.groups_id
        )
        # Verificar si hay usuarios coordinadores relacionados con el proyecto
        if not usuarios_coordinadores:
            raise UserError("No hay usuarios coordinadores asociados al proyecto.")

        # Obtener el usuario financiero actual
        financiero = self.env.user

        # Obtener las observaciones de Financiera (si las hay)
        observacion_financiera = self.observacion_financiera or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_c.name, self.proyecto_relacionado_c.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener el nombre del registro
        nombre_registro = self.name or "Sin nombre"  # Usa el campo que almacena el nombre del registro

        # Construir el mensaje del correo
        mensaje = f"""
            <p><strong>{nombre_proyecto}</strong></p>
            <p>Hola, coordinador:</p>
            <p>El financiero ha <strong>aprobado</strong> el documento orden de compra de bienes. Proveedor <strong>{nombre_registro}</strong>. Queda a su disposición para los fines pertinentes.</p>

            {f'<p><strong>Observaciones:</strong> {observacion_financiera}</p>' if observacion_financiera else ''}
        """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_bienes'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario coordinador
        for coordinador in usuarios_coordinadores:
            try:
                body_html = _orden_compra_email_body(
                    "Aprobación de documento orden de compra de bienes",
                    "coordinador",
                    f"""
                        <p>El financiero ha <strong>aprobado</strong> el documento orden de compra de bienes. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Queda a su disposición para los fines pertinentes.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_financiera,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Aprobación de documento orden de compra de bienes. Proveedor {nombre_registro}",
                    'email_from': financiero.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': coordinador.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }

    def no_aprobado_coordinador(self):
        # Cambiar el estado a "financiera" (para el grupo coordinador)
        self.write({'estado_compra_bienes': 'financiera'})

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_c

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo DESEADOS que están relacionados con el proyecto
        usuarios_financieros = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id or
                         self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id
        )

        # Verificar si hay usuarios financieros relacionados con el proyecto
        if not usuarios_financieros:
            raise UserError("No hay usuarios financieros asociados al proyecto.")

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones (si las hay)
        observacion_coordinador = self.observacion_coordinador or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_c.name, self.proyecto_relacionado_c.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # # Obtener el nombre del registro
        nombre_registro = self.name  # Usa el campo que almacena el nombre del registro

        # Construir el mensaje del correo
        mensaje = f"""
                    <p><strong>{nombre_proyecto}</strong></p>
                    <p>Hola, coordinador:</p>
                    <p>El financiero ha <strong>aprobado</strong> el documento orden de compra de bienes. Proveedor <strong>{nombre_registro}</strong>. Por favor, revisarlo.</p>

                    {f'<p><strong>Observaciones:</strong> {observacion_coordinador}</p>' if observacion_coordinador else ''}
                """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_bienes'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario financiero
        for financiero in usuarios_financieros:
            try:
                body_html = _orden_compra_email_body(
                    "Documento orden de compra de bienes no aprobado",
                    "financiero",
                    f"""
                        <p>El coordinador <strong>no aprobó</strong> el documento orden de compra de bienes. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Por favor, revisarlo.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_coordinador,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Documento orden de compra de bienes no fue aprobado. Proveedor {nombre_registro}",
                    'email_from': coordinador.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': financiero.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }


    def finalizar_contratado(self):
        # Cambiar el estado a "Contratado" (para el grupo coordinador)
        self.write({'estado_compra_bienes': 'contratado'})

    hiden = fields.Char(string='hiden')

    ################# FUNCIONES PARA EL CALCULO EN DETALLES DE LA ORDEN #############
    @api.depends('lineas_ids.valor_total')
    def _compute_subtotal(self):
        for record in self:
            record.subtotal = sum(line.valor_total for line in record.lineas_ids)

    # @api.depends('subtotal')
    # def _compute_iva(self):
    #     for record in self:
    #         record.iva = record.subtotal * 0.19



class OrdenCompraLinea(models.Model):
    _name = 'orden.compra.linea'
    _description = 'Línea de Orden de Compra'

    orden_id = fields.Many2one('orden_compra_bienes', string='Orden de Compra de Bienes')
    descripcion = fields.Char(string='Descripción', required=True, tracking=True)



    cantidad = fields.Integer(string='Cantidad', tracking=True)
    valor_unitario = fields.Float(string='Valor Unitario', tracking=True)
    valor_total = fields.Float(string='Valor Total', compute='_compute_valor_total', store=True, tracking=True)

    @api.depends('cantidad', 'valor_unitario')
    def _compute_valor_total(self):
        for record in self:
            record.valor_total = record.cantidad * record.valor_unitario




class OrdenCompraServicios(models.Model):
    _name = 'orden_compra_servicios'
    _description = 'Orden de Compra de Servicios'
    _inherit = ['mail.thread', 'mail.activity.mixin']  # Habilita el seguimiento en el chatter
    anexo_id = fields.Many2one('anexo', string="Anexo", ondelete="cascade")
    name = fields.Char(string='Nombre', related="proveedor", tracking=True)
    descripcion = fields.Text(string='Descripción')


    def unlink(self):
        # Guardamos los proyectos relacionados ANTES de borrar
        proyectos_afectados = self.mapped('proyecto_relacionado_s').filtered(lambda p: p)
        
        # Ejecutamos el borrado normal
        result = super(OrdenCompraServicios, self).unlink()
        
        # FORZAMOS el recálculo en los proyectos afectados
        if proyectos_afectados:
            proyectos_afectados._compute_saldo_disponible_contratar_poliza_total()
        
        return result


    proyecto_relacionado_s = fields.Many2one('contratacion', string='Proyecto relacionado')
    # Campos calculados
    subtotal = fields.Float(string='Subtotal', compute='_compute_subtotal', store=True, tracking=True)
    iva = fields.Float(string='IVA', store=True, tracking=True)

    select_fondos = fields.Selection([
        ('es_produccion', 'Producción'),
        ('es_poliza', 'Póliza'),
    ], string='Seleccionar fondo')


    gastos_expedicion = fields.Float(string='Gastos expedición', tracking=True)

    # 1. Campo para Producción
    total = fields.Float(
        string='Total Compra (Producción)', 
        compute='_compute_totales_separados', 
        store=True, 
        tracking=True
    )

    # 2. Nuevo campo para Póliza
    total_poliza = fields.Float(
        string='Total Póliza',
        compute='_compute_totales_separados',
        store=True,
        tracking=True
    )

    @api.depends('subtotal', 'iva', 'select_fondos', 'gastos_expedicion')
    def _compute_totales_separados(self):
        """
        Asigna el valor (Subtotal + IVA) al campo correspondiente.
        Si es póliza, suma también los gastos de expedición.
        """
        for record in self:
            # Valores base con tratamiento de nulos
            subtotal = record.subtotal or 0.0
            iva = record.iva or 0.0
            gastos = record.gastos_expedicion or 0.0
            
            valor_base = subtotal + iva

            if record.select_fondos == 'es_produccion':
                record.total = valor_base
                record.total_poliza = 0.0
                
            elif record.select_fondos == 'es_poliza':
                record.total = 0.0
                # Sumamos los gastos de expedición solo aquí
                record.total_poliza = valor_base + gastos
                
            else:
                record.total = 0.0
                record.total_poliza = 0.0

    # 2. Campo Gastos Póliza (Acumulativo e independiente)
    gastos_poliza = fields.Float(
        string='Gastos Póliza', 
        compute='_compute_gastos_poliza', 
        tracking=True,
        store=False,
    )

    @api.depends('subtotal', 'iva', 'select_fondos', 'proyecto_relacionado_s', 'gastos_expedicion')
    def _compute_gastos_poliza(self):
        """
        Lógica Corregida:
        Suma (Subtotal + IVA + Gastos Expedición) de todo el histórico.
        """
        for record in self:
            # 1. Definimos cuánto aporta ESTE registro si es póliza
            valor_actual = (record.subtotal or 0.0) + (record.iva or 0.0) + (record.gastos_expedicion or 0.0)
            
            gastado_historico = 0.0
            if record.proyecto_relacionado_s:
                ordenes_previas = self.search([
                    ('proyecto_relacionado_s', '=', record.proyecto_relacionado_s.id),
                    ('select_fondos', '=', 'es_poliza'),
                    ('id', '!=', record._origin.id)
                ])
                
                # 🚀 CORRECCIÓN: Sumamos los 3 campos de las órdenes anteriores
                gastado_historico = sum(ordenes_previas.mapped(
                    lambda r: (r.subtotal or 0.0) + (r.iva or 0.0) + (r.gastos_expedicion or 0.0)
                ))

            # 2. Asignación del resultado
            if record.select_fondos == 'es_poliza':
                record.gastos_poliza = gastado_historico + valor_actual
            else:
                # Si esta orden no es póliza, el acumulado es solo lo histórico
                record.gastos_poliza = gastado_historico



    
    saldo_disponible_contratar = fields.Monetary(related="proyecto_relacionado_s.saldo_disponible_produccion", string='Saldo disponible')
    saldo_disponible_poliza = fields.Monetary(related="proyecto_relacionado_s.saldo_disponible_poliza_total", string='Saldo disponible (Póliza):')

    # Nuevo campo para el saldo global de la póliza
    poliza_global = fields.Float(
        string='Saldo Global Póliza',
        compute='_compute_poliza_global',
        store=False,
        tracking=True
    )


    @api.depends('proyecto_relacionado_s', 'gastos_poliza', 'subtotal', 'iva', 'select_fondos', 'gastos_expedicion')
    def _compute_poliza_global(self):
        for record in self:
            # 1. Si no hay proyecto o no hay valor de póliza, el saldo es 0
            if not record.proyecto_relacionado_s:
                record.poliza_global = 0.0
                continue

            # 2. Obtenemos el valor total (techo) de la póliza desde el proyecto
            valor_total_poliza = record.proyecto_relacionado_s.poliza_total or 0.0

            # 3. RESTA DIRECTA
            # Como record.gastos_poliza YA ES el acumulado (Histórico + Actual),
            # simplemente lo restamos del total.
            record.poliza_global = valor_total_poliza - record.gastos_poliza



    @api.constrains('total', 'total_poliza', 'select_fondos', 'proyecto_relacionado_s')
    def _check_saldo_negativo_total(self):
        """
        Bloqueo de seguridad: Impide guardar si el saldo final del proyecto 
        o de la póliza queda en negativo.
        """
        for record in self:
            if not record.proyecto_relacionado_s or not record.select_fondos:
                continue

            # 1. Bloqueo para Producción
            if record.select_fondos == 'es_produccion':
                # Revisamos el saldo directo en el proyecto
                if record.proyecto_relacionado_s.saldo_disponible_produccion < 0:
                    raise ValidationError(
                        "🛑 ERROR DE SALDO: PRODUCCIÓN\n\n"
                        f"No puedes guardar esta orden porque el saldo de producción "
                        f"quedaría en negativo (${record.proyecto_relacionado_s.saldo_disponible_produccion:,.2f}).\n"
                        "Por favor, reduzca el valor o cambie la fuente de fondos."
                    )

            # 2. Bloqueo para Póliza
            elif record.select_fondos == 'es_poliza':
                # Revisamos el campo poliza_global calculado
                if record.poliza_global < 0:
                    raise ValidationError(
                        "🛑 ERROR DE SALDO: PÓLIZA\n\n"
                        f"No puedes guardar esta orden porque el cupo de la póliza "
                        f"quedaría en negativo (${record.poliza_global:,.2f}).\n"
                        "Por favor, reduzca el valor para poder continuar."
                    )

    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################

    relacion_orden_compra_servicios_fuv_ids = fields.One2many(
        'cuentas_de_cobros', 
        'relacion_orden_compra_servicios_fuv_id', 
        string='Contrataciones'
    )

    # Este decorador ejecuta la validación justo antes de intentar borrar
    @api.ondelete(at_uninstall=False)
    def _check_cuentas_de_cobro(self):
        for record in self:
            if record.relacion_orden_compra_servicios_fuv_ids:
                # Estructuramos el mensaje con saltos de línea y un icono de advertencia
                mensaje = (
                    "⚠️  RESTRICCIÓN DE SEGURIDAD  ⚠️\n\n"
                    "No se puede eliminar esta orden de servicio porque ya tiene una cuenta de cobro creada.\n\n"
                    "Por favor, solicite que se elimine la cuenta de cobro primero."
                )
                raise UserError(mensaje)

    # Relación hacia departamento_y_municipio
    departamento_municipio_ids = fields.One2many(
        'departamento_y_municipio',
        'orden_compra_servicios_id',
        string="Departamentos y Municipios"
    )
    municipios2 = fields.Many2one(related="departamento_municipio_ids.municipios2")
    departamentos = fields.Many2one(related="departamento_municipio_ids.departamentos")
    objeto_contractual = fields.Text(string="Objeto contractual")
    codigo_ciiu = fields.Char(string="Código CIIU")
    id_proyecto3 = fields.Char(string='Id del proyecto')
    aprueba_f_rutt = fields.Boolean('Aprueba-F', tracking=True)
    compromiso_presupuestal = fields.Char('RPC', tracking=True)
    f_electronica = fields.Boolean('Facturación electronica', tracking=True)
    
    r_iva = fields.Boolean('Responsable de IVA', tracking=True)
    no_r_iva = fields.Boolean('No responsable de IVA', tracking=True)

    @api.onchange('r_iva')
    def _onchange_r_iva(self):
        if self.r_iva:
            self.no_r_iva = False

    @api.onchange('no_r_iva')
    def _onchange_no_r_iva(self):
        if self.no_r_iva:
            self.r_iva = False


    # Sección Impuesto a la venta (IVA)
    responsable = fields.Boolean(string="Responsable IVA")
    no_responsable = fields.Boolean(string="No Responsable IVA")

    excluido = fields.Boolean(string="Excluido IVA")
    exento = fields.Boolean(string="Exento IVA")
    retencion_iva = fields.Float(string="Retención IVA")

    # Sección Otros
    facturador_electronico = fields.Boolean(string="Facturador Electrónico")
    declaracion_renta = fields.Boolean(string="Declaración Renta")
    regimen_simple_tributacion = fields.Boolean(string="Régimen Simple de Tributación")
    regimen_especial = fields.Boolean(string="Régimen Especial")
    regimen_ordinario = fields.Boolean(string="Régimen Ordinario")


    def action_ver_anexos(self):
        self.ensure_one()

        # Buscar si ya hay un anexo ligado a esta orden
        anexo = self.env['anexo'].search([
            ('orden_compra_servicios_id', '=', self.id)
        ], limit=1)

        if not anexo:
            # Si no existe, lo crea
            anexo = self.env['anexo'].create({
                'name': self.name or 'Anexo de Orden %s' % self.id,
                'proceso_gestion_id': getattr(self, 'proceso_gestion_id', False) or False,
                'orden_compra_servicios_id': self.id
            })

        return {
            'type': 'ir.actions.act_window',
            'name': 'Anexo tributario',
            'res_model': 'anexo',
            'view_mode': 'form',
            'res_id': anexo.id,
            'target': 'current',
        }

    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    #Crear informe en word para descargar
    informe_adjunto = fields.Binary(
        string='Informe Adjunto',
        attachment=True,
        readonly=True,
    )

    def render_informe_template_servicios(self):
        # Obtener la ruta del directorio actual
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        # Cargar la plantilla desde el archivo .docx utilizando python-docx-template
        template_path = os.path.join(current_directory, "plantilla_orden_servicios2024.docx")
        template = DocxTemplate(template_path)
        context = {'docs': self}
        # Generar los datos para la tabla
        #datos_tabla = self.generar_datos_tabla()
        #datos_tabla2 = self.generar_datos_tabla2()

        # Obtener el contexto de Odoo
        #context = {'docs': self, 'tabladedatos': datos_tabla, 'tabladedatos2': datos_tabla2}
        # Renderizar la plantilla con los datos actuales del registro
        template.render(context)
        # Guardar el resultado en un nuevo archivo .docx
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_plantilla_orden_servicios2024.docx")
        template.save(output_path)
        return output_path

    def generar_informe_docx(self):
        record_id = self.id  # Asegúrate de tener el ID disponible en tu contexto
        template_rendered = self.render_informe_template_servicios()

        # Crear una instancia del documento
        documento = Document()

        # Guardar el documento en un buffer BytesIO
        buffer = BytesIO()
        documento.save(buffer)

        # Guardar el documento como un adjunto en el registro actual
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})

        # Formatear el nombre con "AT " antes del nombre real
        nombre_archivo = "ORDEN DE COMPRA DE SERVICIOS {}".format(self.proveedor or 'Informe_orden_compra_servicios')

        return {
            'name': '{}.docx'.format(nombre_archivo),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=orden_compra_servicios&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, nombre_archivo),
            'target': 'self',
        }


    # Nuevas variables AGREGADAS 0325
    cedula_proveedor = fields.Char(string='Cedula del proveedor', tracking=True)
    nombre_proyecto_relacionado = fields.Char(related='proyecto_relacionado_s.new_name',
                                              string='Nombre del proyecto relacionado')
    centro_costo = fields.Char(related='proyecto_relacionado_s.mcb_centro_costo', string="Centro de Costo", store=True)



    currency_id = fields.Many2one('res.currency', string='Moneda')

    ##### Relacion con el modelo "contratacion" #####

    solicitante = fields.Char(string='Solicitante', tracking=True)
    forma_pago = fields.Selection([
        ('contado', 'De Contado'),
        ('credito', 'Crédito')
    ], string='Forma de Pago', tracking=True)
    forma_pago1 = fields.Char(string='Forma de pago', tracking=True)

    fecha = fields.Date(string='Fecha')
    fecha_inicio = fields.Date(string="Fecha inicio")
    fecha_fin = fields.Date(string="Fecha fin")
    fecha_solicitud = fields.Date(string="Fecha de Solicitud")
    fecha_solicitud_texto = fields.Char(string="Fecha Solicitud en Texto", compute="_compute_fecha_solicitud_texto", store=True)

    @staticmethod
    def convertir_fecha_a_texto(fecha):
        """Convierte una fecha en texto con formato '04 de Octubre del 2024'."""
        if fecha:
            meses = [
                'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
            ]
            return f"{fecha.day:02d} de {meses[fecha.month - 1]} del {fecha.year}"
        return 'Fecha no asignada'

    @api.depends('fecha_solicitud')
    def _compute_fecha_solicitud_texto(self):
        for record in self:
            record.fecha_solicitud_texto = self.convertir_fecha_a_texto(record.fecha_solicitud)

    proveedor = fields.Char(string='Proveedor', tracking=True)
    direccion = fields.Char(string='Dirección', tracking=True)
    nit = fields.Char(string='NIT', tracking=True)
    telefono = fields.Char(string='Teléfono', tracking=True)
    cel = fields.Char(string='Celular', tracking=True)
    email = fields.Char(string='Email', tracking=True)
    lugar_prestacio_servicio = fields.Char(string='Lugar de Venta', tracking=True)

    ciudad = fields.Many2one(
        comodel_name='municipio',
        string="Ciudad", tracking=True)
    
    def name_get(self):
        result = []
        for record in self:
            display_name = record.name or ''
            if self._context.get('seleccion_persona') == 'persona_juridica' and record.nit:
                display_name += f' [NIT: {record.nit}]'
            elif self._context.get('seleccion_persona') == 'persona_natural' and record.cedula_proveedor:
                display_name += f' [DI: {record.cedula_proveedor}]'
            elif not self._context.get('seleccion_persona'):
                if record.nit:
                    display_name += f' [NIT: {record.nit}]'
                if record.cedula_proveedor:
                    display_name += f' [DI: {record.cedula_proveedor}]'

            result.append((record.id, display_name or "Sin Identificación"))
        return result

    # Campos para las líneas de la orden
    lineas_ids = fields.One2many('orden_compra_servicios.linea', 'orden_id', string='Líneas de Servicios')
    descripcion_del_servicio = fields.Char(string='Descripción', related='lineas_ids.descripcion')




    justificacion = fields.Text(string='Justificación', tracking=True)
    autoriza = fields.Binary("Firma Autorización", attachment=True, tracking=True)

    tiempo_entrega = fields.Char(string='Tiempo de Entrega', tracking=True)

    estado_compra_servicios = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('contratado', 'Contratado'),
    ], string='Estado orden de compra de bienes', default='coordinador', tracking=True)

    hiden = fields.Char(string='hiden')

    ################### Seleccionador de Todos para Coordinacion ##########################
    seleccion_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica')
    ], tracking=True)

    ################### Seleccionador de Todos para Coordinacion PJ ##########################
    selecionar_todos_coord = fields.Boolean('Seleccionar todos PJ', tracking=True)

    @api.onchange('selecionar_todos_coord')
    def _onchange_selecionar_todos_coord(self):
        if self.selecionar_todos_coord:

            self.rut_pj_aprueba_c = True
            self.camara_comercio_doc_aprueba_c = True
            self.planilla_ss_doc_aprueba_c = True
            self.cert_parafiscales_doc_aprueba_c = True
            self.junta_contadores_doc_aprueba_c = True
            self.tp_contador_doc_aprueba_c = True
            self.certificado_bancario_pj_aprueba_c = True
            self.resolucion_facturacion_doc_aprueba_c = True
            self.documento_rpc_pj_aprueba_c = True
            self.factura_cuenta_pj_aprueba_c = True
        else:
            self.rut_pj_aprueba_c = False
            self.camara_comercio_doc_aprueba_c = False
            self.planilla_ss_doc_aprueba_c = False
            self.cert_parafiscales_doc_aprueba_c = False
            self.junta_contadores_doc_aprueba_c = False
            self.tp_contador_doc_aprueba_c = False
            self.certificado_bancario_pj_aprueba_c = False
            self.resolucion_facturacion_doc_aprueba_c = False
            self.documento_rpc_pj_aprueba_c = False
            self.factura_cuenta_pj_aprueba_c = False

    ################### Seleccionador de Todos para Financiera PJ ##########################

    selecionar_todos_fin = fields.Boolean('Seleccionar todos PJ', tracking=True)

    @api.onchange('selecionar_todos_fin')
    def _onchange_selecionar_todos_fin(self):
        if self.selecionar_todos_fin:

            self.rut_pj_aprueba_f = True
            self.camara_comercio_doc_aprueba_f = True
            self.planilla_ss_doc_aprueba_f = True
            self.cert_parafiscales_doc_aprueba_f = True
            self.junta_contadores_doc_aprueba_f = True
            self.tp_contador_doc_aprueba_f = True
            self.certificado_bancario_pj_aprueba_f = True
            self.resolucion_facturacion_doc_aprueba_f = True
            self.documento_rpc_pj_aprueba_f = True
            self.factura_cuenta_pj_aprueba_f = True
        else:
            self.rut_pj_aprueba_f = False
            self.camara_comercio_doc_aprueba_f = False
            self.planilla_ss_doc_aprueba_f = False
            self.cert_parafiscales_doc_aprueba_f = False
            self.junta_contadores_doc_aprueba_f = False
            self.tp_contador_doc_aprueba_f = False
            self.certificado_bancario_pj_aprueba_f = False
            self.resolucion_facturacion_doc_aprueba_f = False
            self.documento_rpc_pj_aprueba_f = False
            self.factura_cuenta_pj_aprueba_f = False

    ################### Seleccionador de Todos para Coordinacion PN ##########################
    selecionar_todos_coord_pn = fields.Boolean('Seleccionar todos PN', tracking=True)

    @api.onchange('selecionar_todos_coord_pn')
    def _onchange_selecionar_todos_coord_pn(self):
        if self.selecionar_todos_coord_pn:

            self.rut_pn_aprueba_doc_c = True
            self.documento_identidad_aprueba_doc_c = True
            self.planilla_aprueba_doc_c = True
            self.certificado_bancario_aprueba_doc_c = True
            self.doc_rpc_aprueba_c = True
            self.factura_cuenta_aprueba_doc_c = True

        else:
            self.rut_pn_aprueba_doc_c = False
            self.documento_identidad_aprueba_doc_c = False
            self.planilla_aprueba_doc_c = False
            self.certificado_bancario_aprueba_doc_c = False
            self.doc_rpc_aprueba_c = False
            self.factura_cuenta_aprueba_doc_c = False

    ################### Seleccionador de Todos para Financiero PN ##########################
    seleccionar_todos_finan_pn = fields.Boolean('Seleccionar todos PN', tracking=True)

    @api.onchange('seleccionar_todos_finan_pn')
    def _onchange_seleccionar_todos_finan_pn(self):
        if self.seleccionar_todos_finan_pn:

            self.rut_pn_aprueba_doc_f = False
            self.documento_identidad_aprueba_doc_f = False
            self.planilla_aprueba_doc_f = False
            self.certificado_bancario_aprueba_doc_f = False
            self.doc_rpc_aprueba_f = False
            self.factura_cuenta_aprueba_doc_f = False
        else:
            self.rut_pn_aprueba_doc_f = False
            self.documento_identidad_aprueba_doc_f = False
            self.planilla_aprueba_doc_f = False
            self.certificado_bancario_aprueba_doc_f = False
            self.doc_rpc_aprueba_f = False
            self.factura_cuenta_aprueba_doc_f = False

        ################### Drive ##########################

    def unlink(self):
        # Iterar por cada registro que se va a eliminar
        for record in self:
            # Verificar si existe un archivo de Drive vinculado
            if record.drive_file_id:
                try:
                    # Intentar eliminar el archivo vinculado en Google Drive
                    record.drive_file_id.unlink()
                except Exception as e:
                    raise UserError(f"No se pudo eliminar la carpeta de Google Drive: {str(e)}")
        # Llamar al método original para eliminar el registro de Odoo
        return super(OrdenCompraServicios, self).unlink()

    def write(self, vals):
        """Sobrescribe el método 'write' para actualizar Google Drive y sincronizar cálculos."""
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            return True

        # 1. Ejecutamos el guardado normal primero
        res = super(OrdenCompraServicios, self).write(vals)

        # --- SECCIÓN DE DOCUMENTOS GOOGLE DRIVE (Tu lógica actual) ---
        if 'rut_pj_doc' in vals: self._upload_rut_pj_to_drive_pj()
        if 'junta_contadores_doc' in vals: self._upload_certificado_junta_contadores_to_drive_pj()
        if 'camara_comercio_doc' in vals: self._upload_camara_comercio_to_drive_pj()
        if 'planilla_ss_doc' in vals: self._upload_planilla_ss_to_drive_pj()
        if 'cert_parafiscales_doc' in vals: self._upload_certificado_parafiscales_to_drive_pj()
        if 'certificado_bancario_pj' in vals: self._upload_certificado_bancario_pj_to_drive_pj()
        if 'tp_contador_doc' in vals: self._upload_tp_contador_to_drive_pj()
        if 'resolucion_facturacion_doc' in vals: self._upload_resolucion_facturacion_to_drive_pj()
        if 'documento_rpc_pj' in vals: self._upload_documento_rpc_pj_to_drive_pj()
        if 'factura_cuenta_pj' in vals: self._upload_factura_cuenta_pj_to_drive_pj()
        if 'Anexo_tributario' in vals: self._upload_anexo_tributario_to_drive()
        if 'rut_pn' in vals: self._upload_rut_pn_to_drive()
        if 'documento_identidad' in vals: self._upload_documento_identidad_to_drive()
        if 'planilla' in vals: self._upload_planilla_to_drive()
        if 'certificado_bancario' in vals: self._upload_certificado_bancario_to_drive()
        if 'documento_rpc' in vals: self._upload_documento_rpc_to_drive()
        if 'factura_cuenta' in vals: self._upload_factura_cuenta_to_drive()
        if 'Anexo_tributario_pn' in vals: self._upload_anexo_tributario_pn_to_drive()

        # --- 🚀 NUEVA LÓGICA: EFECTO CASCADA PARA GASTOS PÓLIZA ---
        # Si se modifica el subtotal, iva o el proyecto, recalculamos a TODOS los hermanos
        campos_criticos = ['subtotal', 'iva', 'select_fondos', 'proyecto_relacionado_s']
        if any(campo in vals for campo in campos_criticos):
            for record in self:
                if record.proyecto_relacionado_s:
                    # Buscamos a todos los registros que pertenecen al mismo proyecto
                    hermanos = self.search([
                        ('proyecto_relacionado_s', '=', record.proyecto_relacionado_s.id)
                    ])
                    # Forzamos la ejecución de la función de cómputo para todos ellos
                    hermanos._compute_gastos_poliza()
        
        return res

    proceso_en_ejecucion = fields.Boolean(default=False)

    @api.model
    def create(self, vals):
        current_date = datetime.now().strftime("%Y%m%d_%H%M%S")
        record = super(OrdenCompraServicios, self).create(vals)

        if not record.name:
            self.name = f"Folder_{current_date}"
            self.save()

        record.buscar_carpeta()

        return record

    def buscar_carpeta(self):
        """Busca o crea la carpeta relacionada en Odoo y en Google Drive."""
        if self.proyecto_relacionado_s:
            if self.proceso_en_ejecucion:
                _logger.info("Proceso de búsqueda de carpeta ya en ejecución, esperando a que termine.")
                return

            self.proceso_en_ejecucion = True

            try:
                _logger.info(f"Buscando carpeta para el proyecto relacionado: {self.proyecto_relacionado_s.name}")

                # Buscar o crear carpeta en Odoo
                drive_stage = self.env['drive_stage'].search([
                    ('name', '=', '3. CONTRATOS PS'),
                    ('proyecto_relacionado', '=', self.proyecto_relacionado_s.id),
                ], limit=1)

                if not drive_stage:
                    _logger.warning("No se encontró la carpeta 'CONTRATOS PS' en Odoo. Creando...")
                    drive_stage = self.env['drive_stage'].create({
                        'name': '3. CONTRATOS PS',
                        'proyecto_relacionado': self.proyecto_relacionado_s.id,
                        'folder_id': 'some_default_folder_id',
                    })

                _logger.info(f"Carpeta en Odoo encontrada o creada: {drive_stage.name} (ID: {drive_stage.id})")

                # Asignar solo el nombre sin identificador a `new_name`
                if self.name:
                    self.name = self.name

                # Generar nombre con identificador solo para Google Drive
                unique_folder_id = str(uuid.uuid4())[:8]
                drive_folder_name = f"{self.name}_(servicio)_{unique_folder_id}"

                _logger.info(f"Intentando buscar o crear la carpeta con el nombre: {drive_folder_name}")

                # Buscar si ya existe en 'drive.file'
                drive_file = self.env['drive.file'].search([
                    ('folder_name', '=', drive_folder_name),
                    ('drive_id', '=', drive_stage.id)
                ], limit=1)

                if not drive_file:
                    _logger.info(f"Carpeta '{drive_folder_name}' no encontrada en 'drive.file'. Creando...")

                    with threading.Lock():
                        drive_file = self.env['drive.file'].search([
                            ('folder_name', '=', drive_folder_name),
                            ('drive_id', '=', drive_stage.id)
                        ], limit=1)
                        if not drive_file:
                            drive_auth = GoogleDriveAuth.get_instance()
                            drive = drive_auth.get_drive()

                            folder_metadata = {
                                'title': drive_folder_name,  # Usa la versión con identificador solo en Drive
                                'mimeType': 'application/vnd.google-apps.folder',
                                'parents': [{'id': drive_stage.folder_id}]
                            }

                            try:
                                folder = drive.CreateFile(folder_metadata)
                                folder.Upload()

                                drive_file = self.env['drive.file'].create({
                                    'folder_name': drive_folder_name,
                                    'folder_id2': folder['id'],
                                    'drive_id': drive_stage.id,

                                })
                                drive_file.write({'select_persona': 'bienes_y_sevicios'})

                                _logger.info(
                                    f"Carpeta '{drive_folder_name}' creada en 'drive.file' con ID {folder['id']}")

                                self.write({'drive_file_id': drive_file.id})
                                _logger.info(f"Campo 'drive_file_id' actualizado con ID {drive_file.id}")
                                time.sleep(3)


                            except Exception as e:
                                _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                                raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")
                        else:
                            _logger.info(f"Carpeta '{drive_folder_name}' ya existe en 'drive.file'.")
                            self.write({'drive_file_id': drive_file.id})
                            _logger.info(f"Campo 'drive_file_id' actualizado con ID {drive_file.id}")

            finally:
                self.proceso_en_ejecucion = False  # Corrige el flag de proceso en ejecución

    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive')  # Campo para vincular con drive.file

    ######################### DOCUMENTOS PARA PERSONA JURIDICA #################################
    anexo_tributario_pj = fields.Boolean(string='Anexo tributario', tracking=True)
    Anexo_tributario = fields.Binary(string="Anexo tributario")
    Anexo_tributario_name = fields.Char(string="Nombre del documento Anexo tributario", tracking=True)
    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_anexo_tributario = {}

    def abrir_documento_anexo_tributario_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_anexo_tributario_en_drive()

        # Primero, verificamos si 'Anexo_tributario_doc_id' está configurado
        if self.Anexo_tributario_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Anexo tributario',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.Anexo_tributario_doc_id.id,
                'target': 'new',
            }

        # Si 'Anexo_tributario_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.Anexo_tributario_name:
            file_name_to_search = self.Anexo_tributario_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_anexo_tributario:
                drive_document = self._cache_documentos_anexo_tributario[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_anexo_tributario[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'Anexo_tributario_doc_id' con el ID del documento encontrado
                self.write({'Anexo_tributario_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Anexo tributario',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')
        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Anexo tributario relacionado con este registro.')

    Anexo_tributario_visible = fields.Boolean(default=False, related='Anexo_tributario_doc_id.actualizar_visible', store=True,
                                               readonly=False)

    def actualizar_correo_anexo_tributario_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.Anexo_tributario_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.Anexo_tributario_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.Anexo_tributario_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    Anexo_tributario_doc_id = fields.Many2one('drive', string='Anexo tributario', tracking=True,
                                                domain="[('stage2_id', '=', drive_file_id)]")
    Anexo_tributario_doc_visible = fields.Boolean(default=True)

    def action_upload_Anexo_tributario_pj_doc(self):
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
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'Anexo_tributario_pj_documento_doc', 
            },
        }

    def subir_anexo_tributario_a_drive(self):
        """Sube el Anexo tributario a Google Drive y lo registra en el modelo drive."""
        if self.Anexo_tributario and self.Anexo_tributario_name:
            _logger.info(f"Subiendo documento '{self.Anexo_tributario_name}' a Google Drive...")
            self._upload_anexo_tributario_to_drive()
        else:
            _logger.warning("No se encontró el Anexo tributario para subir.")

    def _upload_anexo_tributario_to_drive(self):
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
            file_content = base64.b64decode(self.Anexo_tributario)
            original_extension = os.path.splitext(self.Anexo_tributario_name)[1]
            fixed_file_name = f"Anexo_tributario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.Anexo_tributario_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.Anexo_tributario,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Solo actualizar el campo Anexo_tributario_doc_id
            if not self.Anexo_tributario_doc_id:
                self.Anexo_tributario_doc_id = drive_record.id

            _logger.info(f"Campo Anexo_tributario_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar Anexo_tributario_doc_visible a False después de subir el archivo
            self.Anexo_tributario_doc_visible = False

            _logger.info(f"Campo Anexo_tributario_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    anexo_tributario_pj_aprueba_f = fields.Boolean(string="Anexo tributario aprobado por Coordinador", default=False)
    anexo_tributario_pj_aprueba_c = fields.Boolean(string="Anexo tributario aprobado por Financiero", default=False)





    rut_pj_doc = fields.Binary(string="RUT Persona Juridica")
    rut_pj_name = fields.Char(string="Nombre del documento RUT PJ", tracking=True)
    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_rut_pj = {}

    def abrir_documento_rut_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_rut_pj_en_drive()

        # Primero, verificamos si 'rut_pj_doc_id' está configurado
        if self.rut_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'RUT',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.rut_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'rut_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rut_pj_name:
            file_name_to_search = self.rut_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rut_pj:
                drive_document = self._cache_documentos_rut_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_rut_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rut_pj_doc_id' con el ID del documento encontrado
                self.write({'rut_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'RUT',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún RUT relacionado con este registro.')

    rut_pj_actualizar_visible = fields.Boolean(default=False, related='rut_pj_doc_id.actualizar_visible', store=True,
                                               readonly=False)
    file_data_visible = fields.Boolean(default=False)

    def actualizar_correo_rut_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.rut_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.rut_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.rut_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    rut_pj_doc_visible = fields.Boolean(default=True)
    rut_pj_doc_id = fields.Many2one('drive', string='RUT PJ', tracking=True,
                                    domain="[('stage2_id', '=', drive_file_id)]")
    
    def action_upload_rut_pj_doc(self):
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
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'rut_pj_documento_doc', 
            },
        }

    def subir_rut_pj_a_drive(self):
        """Sube el RUT PJ a Google Drive y lo registra en el modelo drive."""
        if self.rut_pj_doc and self.rut_pj_name:
            _logger.info(f"Subiendo documento '{self.rut_pj_name}' a Google Drive...")
            self._upload_rut_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el RUT PJ para subir.")

    def _upload_rut_pj_to_drive_pj(self):
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
            file_content = base64.b64decode(self.rut_pj_doc)

            original_extension = os.path.splitext(self.rut_pj_name)[1]

            fixed_file_name = f"1_RUT_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.rut_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.rut_pj_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo rut_pj_doc_id
            if not self.rut_pj_doc_id:
                self.rut_pj_doc_id = drive_record.id

            _logger.info(f"Campo rut_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar rut_pj_doc_visible a False después de subir el archivo
            self.rut_pj_doc_visible = False

            _logger.info(f"Campo rut_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    rut_pj_aprueba_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)
    rut_pj_aprueba_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', tracking=True, default=True)

    camara_comercio_doc = fields.Binary(string="Camara de comercio")
    camara_comercio_name = fields.Char(string="Nombre del documento Camara de Comercio", tracking=True)
    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_camara_comercio = {}

    def abrir_documento_camara_comercio_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_camara_comercio_en_drive()

        # Primero, verificamos si 'camara_comercio_doc_id' está configurado
        if self.camara_comercio_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Camara de comercio',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.camara_comercio_doc_id.id,
                'target': 'new',
            }

        # Si 'camara_comercio_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.camara_comercio_name:
            file_name_to_search = self.camara_comercio_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_camara_comercio:
                drive_document = self._cache_documentos_camara_comercio[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_camara_comercio[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'camara_comercio_doc_id' con el ID del documento encontrado
                self.write({'camara_comercio_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Camara de comercio',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado de Cámara de Comercio relacionado con este registro.')

    camara_comercio_actualizar_visible = fields.Boolean(default=False,
                                                        related='camara_comercio_doc_id.actualizar_visible', store=True,
                                                        readonly=False)

    def actualizar_correo_camara_comercio_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.camara_comercio_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.camara_comercio_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.camara_comercio_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    camara_comercio_doc_id = fields.Many2one('drive', string='Camara de comercio', tracking=True,
                                             domain="[('stage2_id', '=', drive_file_id)]")
    camara_comercio_doc_visible = fields.Boolean(default=True)


    def action_upload_camara_comercio_pj_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir camara de comercio',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'camara_comercio_pj_documento_doc', 
            },
        }
    

    def subir_camara_comercio_a_drive(self):
        """Sube el documento 'Camara de comercio' a Google Drive y lo registra en el modelo drive."""
        if self.camara_comercio_doc and self.camara_comercio_name:
            _logger.info(f"Subiendo documento '{self.camara_comercio_name}' a Google Drive...")
            self._upload_camara_comercio_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Camara de comercio' para subir.")

    def _upload_camara_comercio_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.camara_comercio_doc)

            original_extension = os.path.splitext(self.camara_comercio_name)[1]

            fixed_file_name = f"2_Camara de Comercio_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.camara_comercio_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.camara_comercio_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo camara_comercio_doc_id
            if not self.camara_comercio_doc_id:
                self.camara_comercio_doc_id = drive_record.id

            _logger.info(f"Campo camara_comercio_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar camara_comercio_doc_visible a False después de subir el archivo
            self.camara_comercio_doc_visible = False

            _logger.info(f"Campo camara_comercio_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    camara_comercio_doc_aprueba_f = fields.Boolean('Aprueba financiera Camara de comercio', tracking=True, store=True)
    camara_comercio_doc_aprueba_c = fields.Boolean('Aprueba Coordinador Camara de comercio', tracking=True,
                                                   default=True)

    planilla_ss_doc = fields.Binary(string="Planilla de seguridad social")
    planilla_ss_name = fields.Char(string="Nombre del documento planilla de seguridad social", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_planilla_ss = {}

    def abrir_documento_planilla_ss_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_planilla_ss_en_drive()

        # Primero, verificamos si 'planilla_ss_doc_id' está configurado
        if self.planilla_ss_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Planilla de seguridad social',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.planilla_ss_doc_id.id,
                'target': 'new',
            }

        # Si 'planilla_ss_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.planilla_ss_name:
            file_name_to_search = self.planilla_ss_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_planilla_ss:
                drive_document = self._cache_documentos_planilla_ss[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_planilla_ss[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'planilla_ss_doc_id' con el ID del documento encontrado
                self.write({'planilla_ss_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Planilla de seguridad social',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Planilla de Seguridad Social relacionada con este registro.')

    planilla_ss_actualizar_visible = fields.Boolean(default=False, related='planilla_ss_doc_id.actualizar_visible',
                                                    store=True, readonly=False)

    def actualizar_correo_planilla_ss_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.planilla_ss_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.planilla_ss_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.planilla_ss_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    planilla_ss_doc_id = fields.Many2one('drive', string='Planilla de seguridad social', tracking=True,
                                         domain="[('stage2_id', '=', drive_file_id)]")
    planilla_ss_doc_visible = fields.Boolean(default=True)

    def action_upload_planilla_ss_pj_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir planilla de seguridad social',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'planilla_ss_pj_documento_doc', 
            },
        }

    def subir_planilla_ss_a_drive(self):
        """Sube el documento 'Planilla de seguridad social' a Google Drive y lo registra en el modelo drive."""
        if self.planilla_ss_doc and self.planilla_ss_name:
            _logger.info(f"Subiendo documento '{self.planilla_ss_name}' a Google Drive...")
            self._upload_planilla_ss_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Planilla de seguridad social' para subir.")

    def _upload_planilla_ss_to_drive_pj(self):
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
            file_content = base64.b64decode(self.planilla_ss_doc)

            original_extension = os.path.splitext(self.planilla_ss_name)[1]

            fixed_file_name = f"3_Planilla seguridad social_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.planilla_ss_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.planilla_ss_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo planilla_ss_doc_id
            if not self.planilla_ss_doc_id:
                self.planilla_ss_doc_id = drive_record.id

            _logger.info(f"Campo planilla_ss_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar planilla_ss_doc_visible a False después de subir el archivo
            self.planilla_ss_doc_visible = False

            _logger.info(f"Campo planilla_ss_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    planilla_ss_doc_aprueba_f = fields.Boolean('Aprueba financiera Seguridad social', tracking=True, store=True)
    planilla_ss_doc_aprueba_c = fields.Boolean('Aprueba Coordinador Seguridad social', tracking=True, default=True)

    cert_parafiscales_doc = fields.Binary(string="Certificado Parafiscales")
    cert_parafiscales_name = fields.Char(string="Nombre del documento Cert Parafiscales", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_parafiscales = {}

    def abrir_documento_certificado_parafiscales_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_parafiscales_en_drive()

        # Primero, verificamos si 'cert_parafiscales_doc_id' está configurado
        if self.cert_parafiscales_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Certificado Parafiscales',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.cert_parafiscales_doc_id.id,
                'target': 'new',
            }

        # Si 'cert_parafiscales_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.cert_parafiscales_name:
            file_name_to_search = self.cert_parafiscales_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_parafiscales:
                drive_document = self._cache_documentos_certificado_parafiscales[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_parafiscales[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'cert_parafiscales_doc_id' con el ID del documento encontrado
                self.write({'cert_parafiscales_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Certificado Parafiscales',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Parafiscales relacionado con este registro.')

    cert_parafiscales_actualizar_visible = fields.Boolean(default=False,
                                                          related='cert_parafiscales_doc_id.actualizar_visible',
                                                          store=True, readonly=False)

    def actualizar_correo_certificado_parafiscales_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.cert_parafiscales_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.cert_parafiscales_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.cert_parafiscales_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    cert_parafiscales_doc_id = fields.Many2one('drive', string='Certificado Parafiscales', tracking=True,
                                               domain="[('stage2_id', '=', drive_file_id)]")

    cert_parafiscales_doc_visible = fields.Boolean(default=True)

    def action_upload_certificado_parafiscales_pj_doc(self):
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
                'default_servicios_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'certificado_parafiscales_pj_documento_doc',
            },
        }

    def subir_certificado_parafiscales_a_drive(self):
        """Sube el documento 'Certificado Parafiscales' a Google Drive y lo registra en el modelo drive."""
        if self.cert_parafiscales_doc and self.cert_parafiscales_name:
            _logger.info(f"Subiendo documento '{self.cert_parafiscales_name}' a Google Drive...")
            self._upload_certificado_parafiscales_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Certificado Parafiscales' para subir.")

    def _upload_certificado_parafiscales_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.cert_parafiscales_doc)

            original_extension = os.path.splitext(self.cert_parafiscales_name)[1]

            fixed_file_name = f"4_Certificado parafiscales_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.cert_parafiscales_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.cert_parafiscales_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo cert_parafiscales_doc_id
            if not self.cert_parafiscales_doc_id:
                self.cert_parafiscales_doc_id = drive_record.id

            _logger.info(
                f"Campo cert_parafiscales_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar cert_parafiscales_doc_visible a False después de subir el archivo
            self.cert_parafiscales_doc_visible = False

            _logger.info(f"Campo cert_parafiscales_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    cert_parafiscales_doc_aprueba_f = fields.Boolean('Aprueba financiera Certificado parafiscals', tracking=True,
                                                     store=True)
    cert_parafiscales_doc_aprueba_c = fields.Boolean('Aprueba Coordinador Certificado parafiscales', tracking=True,
                                                     default=True)

    junta_contadores_doc = fields.Binary(string="Junta de contadores")
    junta_contadores_name = fields.Char(string="Nombre del documento Junta de Contadores", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_junta_contadores = {}

    def abrir_documento_certificado_junta_contadores_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_junta_contadores_en_drive()

        # Primero, verificamos si 'certificado_junta_contadores_doc_id' está configurado
        if self.certificado_junta_contadores_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Junta contadores',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.certificado_junta_contadores_doc_id.id,
                'target': 'new',
            }

        # Si 'junta_contadores_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.junta_contadores_name:
            file_name_to_search = self.junta_contadores_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_junta_contadores:
                drive_document = self._cache_documentos_certificado_junta_contadores[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_junta_contadores[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'certificado_junta_contadores_doc_id' con el ID del documento encontrado
                self.write({'certificado_junta_contadores_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Junta contadores',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Junta Contadores relacionado con este registro.')

    certificado_junta_contadores_actualizar_visible = fields.Boolean(default=False,
                                                                     related='certificado_junta_contadores_doc_id.actualizar_visible',
                                                                     store=True, readonly=False)

    def actualizar_correo_certificado_junta_contadores_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_junta_contadores_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_junta_contadores_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.certificado_junta_contadores_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    certificado_junta_contadores_doc_id = fields.Many2one('drive', string='Certificado Junta Contadores', tracking=True,
                                                          domain="[('stage2_id', '=', drive_file_id)]")
    certificado_junta_contadores_doc_visible = fields.Boolean(default=True)

    def action_upload_certificado_junta_contadores_pj_doc(self):
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
                'default_servicios_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'certificado_junta_contadores_pj_documento_doc',
            },
        }

    def subir_certificado_junta_contadores_a_drive(self):
        """Sube el documento 'Certificado Junta Contadores' a Google Drive y lo registra en el modelo drive."""
        if self.junta_contadores_doc and self.junta_contadores_name:
            _logger.info(f"Subiendo documento '{self.junta_contadores_name}' a Google Drive...")
            self._upload_certificado_junta_contadores_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Certificado Junta Contadores' para subir.")

    def _upload_certificado_junta_contadores_to_drive_pj(self):
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
            file_content = base64.b64decode(self.junta_contadores_doc)

            original_extension = os.path.splitext(self.junta_contadores_name)[1]

            fixed_file_name = f"6_Junta Contadores_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.junta_contadores_name} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.junta_contadores_doc,  # Guardar el archivo binario en Odoo
                'folder_id': carpeta_drive,  # Guardar la carpeta donde se subió
                'drive_file_id': self.drive_file_id.id,  # Relacionar con drive.file correctamente
                'stage2_id': self.drive_file_id.id  # Asignar la ubicación (stage2_id) como la misma de 'drive_file_id'
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo certificado_junta_contadores_doc_id
            if not self.certificado_junta_contadores_doc_id:
                self.certificado_junta_contadores_doc_id = drive_record.id

            _logger.info(
                f"Campo certificado_junta_contadores_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar certificado_junta_contadores_doc_visible a False después de subir el archivo
            self.certificado_junta_contadores_doc_visible = False

            _logger.info(f"Campo certificado_junta_contadores_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    junta_contadores_doc_aprueba_f = fields.Boolean('Aprueba financiera Junta de contadores', tracking=True, store=True)
    junta_contadores_doc_aprueba_c = fields.Boolean('Aprueba Coordinador Junta de contadores', tracking=True,
                                                    default=True)

    tp_contador_doc = fields.Binary(string="TP Contador")
    tp_contador_name = fields.Char(string="Nombre del documento Junta de Contadores", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_tp_contador = {}

    def abrir_documento_tp_contador_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_tp_contador_en_drive()

        # Primero, verificamos si 'tp_contador_doc_id' está configurado
        if self.tp_contador_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'TP Contador',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.tp_contador_doc_id.id,
                'target': 'new',
            }

        # Si 'tp_contador_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.tp_contador_name:
            file_name_to_search = self.tp_contador_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_tp_contador:
                drive_document = self._cache_documentos_tp_contador[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_tp_contador[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'tp_contador_doc_id' con el ID del documento encontrado
                self.write({'tp_contador_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'TP Contador',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún TP Contador relacionado con este registro.')

    tp_contador_actualizar_visible = fields.Boolean(default=False, related='tp_contador_doc_id.actualizar_visible',
                                                    store=True, readonly=False)

    def actualizar_correo_tp_contador_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.tp_contador_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.tp_contador_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.tp_contador_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    tp_contador_doc_id = fields.Many2one('drive', string='TP Contador', tracking=True,
                                         domain="[('stage2_id', '=', drive_file_id)]")

    tp_contador_doc_visible = fields.Boolean(default=True)

    def action_upload_tp_contador_pj_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir TP Contador',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'tp_contador_pj_documento_doc',
            },
        }

    def subir_tp_contador_a_drive(self):
        """Sube el documento 'TP Contador' a Google Drive y lo registra en el modelo drive."""
        if self.tp_contador_doc and self.tp_contador_name:
            _logger.info(f"Subiendo documento '{self.tp_contador_name}' a Google Drive...")
            self._upload_tp_contador_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'TP Contador' para subir.")

    def _upload_tp_contador_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.tp_contador_doc)

            original_extension = os.path.splitext(self.tp_contador_name)[1]

            fixed_file_name = f"7_TP Contador_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.tp_contador_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.tp_contador_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo tp_contador_doc_id
            if not self.tp_contador_doc_id:
                self.tp_contador_doc_id = drive_record.id

            _logger.info(f"Campo tp_contador_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar tp_contador_doc_visible a False después de subir el archivo
            self.tp_contador_doc_visible = False

            _logger.info(f"Campo tp_contador_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    tp_contador_doc_aprueba_f = fields.Boolean('Aprueba financiera TP Contador', tracking=True, store=True)
    tp_contador_doc_aprueba_c = fields.Boolean('Aprueba Coordinador TP Contador', tracking=True, default=True)

    certificado_bancario_pj = fields.Binary(string="Certficado Bancario PJ")
    certificado_bancario_pj_name = fields.Char(string="Nombre del documento Cert Bancario PJ", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_bancario_pj = {}

    def abrir_documento_certificado_bancario_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_bancario_pj_en_drive()

        # Primero, verificamos si 'certificado_bancario_pj_doc_id' está configurado
        if self.certificado_bancario_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Certificado Bancario PJ',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.certificado_bancario_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'certificado_bancario_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.certificado_bancario_pj_name:
            file_name_to_search = self.certificado_bancario_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_bancario_pj:
                drive_document = self._cache_documentos_certificado_bancario_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_bancario_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'certificado_bancario_pj_doc_id' con el ID del documento encontrado
                self.write({'certificado_bancario_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Certificado Bancario PJ',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Bancario PJ relacionado con este registro.')

    certificado_bancario_pj_actualizar_visible = fields.Boolean(default=False,
                                                                related='certificado_bancario_pj_doc_id.actualizar_visible',
                                                                store=True, readonly=False)

    def actualizar_correo_certificado_bancario_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_bancario_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_bancario_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.certificado_bancario_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    certificado_bancario_pj_doc_id = fields.Many2one('drive', string='Certificado Bancario PJ', tracking=True,
                                                     domain="[('stage2_id', '=', drive_file_id)]")

    certificado_bancario_pj_doc_visible = fields.Boolean(default=True)

    def action_upload_certificado_bancario_pj_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado bancario',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'certificado_bancario_pj_documento_doc',
            },
        }

    def subir_certificado_bancario_pj_a_drive(self):
        """Sube el documento 'Certificado Bancario PJ' a Google Drive y lo registra en el modelo drive."""
        if self.certificado_bancario_pj and self.certificado_bancario_pj_name:
            _logger.info(f"Subiendo documento '{self.certificado_bancario_pj_name}' a Google Drive...")
            self._upload_certificado_bancario_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Certificado Bancario PJ' para subir.")

    def _upload_certificado_bancario_pj_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.certificado_bancario_pj)

            original_extension = os.path.splitext(self.certificado_bancario_pj_name)[1]

            fixed_file_name = f"8_Certificado Bancario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.certificado_bancario_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.certificado_bancario_pj,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo certificado_bancario_pj_doc_id
            if not self.certificado_bancario_pj_doc_id:
                self.certificado_bancario_pj_doc_id = drive_record.id

            _logger.info(
                f"Campo certificado_bancario_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar certificado_bancario_pj_doc_visible a False después de subir el archivo
            self.certificado_bancario_pj_doc_visible = False

            _logger.info(f"Campo certificado_bancario_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    certificado_bancario_pj_aprueba_f = fields.Boolean('Aprueba financiera Certificado financiero PJ', tracking=True,
                                                       store=True)
    certificado_bancario_pj_aprueba_c = fields.Boolean('Aprueba Coordinador Certificado financiero PJ', tracking=True,
                                                       default=True)

    resolucion_facturacion_doc = fields.Binary(string="Resolucion Facturacion")
    resolucion_facturacion_name = fields.Char(string="Nombre del documento resolucion facturacion", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_resolucion_facturacion = {}

    def abrir_documento_resolucion_facturacion_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_resolucion_facturacion_en_drive()

        # Primero, verificamos si 'resolucion_facturacion_doc_id' está configurado
        if self.resolucion_facturacion_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Resolucion Facturacion',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.resolucion_facturacion_doc_id.id,
                'target': 'new',
            }

        # Si 'resolucion_facturacion_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.resolucion_facturacion_name:
            file_name_to_search = self.resolucion_facturacion_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_resolucion_facturacion:
                drive_document = self._cache_documentos_resolucion_facturacion[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_resolucion_facturacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'resolucion_facturacion_doc_id' con el ID del documento encontrado
                self.write({'resolucion_facturacion_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Resolucion Facturacion',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Resolucion Facturacion relacionada con este registro.')

    resolucion_facturacion_actualizar_visible = fields.Boolean(default=False,
                                                               related='resolucion_facturacion_doc_id.actualizar_visible',
                                                               store=True, readonly=False)

    def actualizar_correo_resolucion_facturacion_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.resolucion_facturacion_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.resolucion_facturacion_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.resolucion_facturacion_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    resolucion_facturacion_doc_id = fields.Many2one('drive', string='Resolucion Facturacion', tracking=True,
                                                    domain="[('stage2_id', '=', drive_file_id)]")

    resolucion_facturacion_doc_visible = fields.Boolean(default=True)

    def action_upload_resolucion_facturacion_pj_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir resolucion facturacion',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'resolucion_facturacion_pj_documento_doc',
            },
        }

    def subir_resolucion_facturacion_a_drive(self):
        """Sube el documento 'Resolucion Facturacion' a Google Drive y lo registra en el modelo drive."""
        if self.resolucion_facturacion_doc and self.resolucion_facturacion_name:
            _logger.info(f"Subiendo documento '{self.resolucion_facturacion_name}' a Google Drive...")
            self._upload_resolucion_facturacion_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Resolucion Facturacion' para subir.")

    def _upload_resolucion_facturacion_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.resolucion_facturacion_doc)

            original_extension = os.path.splitext(self.resolucion_facturacion_name)[1]

            fixed_file_name = f"9_Resolucion Facturacion_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.resolucion_facturacion_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.resolucion_facturacion_doc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo resolucion_facturacion_doc_id
            if not self.resolucion_facturacion_doc_id:
                self.resolucion_facturacion_doc_id = drive_record.id

            _logger.info(
                f"Campo resolucion_facturacion_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar resolucion_facturacion_doc_visible a False después de subir el archivo
            self.resolucion_facturacion_doc_visible = False

            _logger.info(f"Campo resolucion_facturacion_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    resolucion_facturacion_doc_aprueba_f = fields.Boolean('Aprueba financiera resolucion facturacion', tracking=True,
                                                          store=True)
    resolucion_facturacion_doc_aprueba_c = fields.Boolean('Aprueba Coordinador resolucion facturacion', tracking=True,
                                                          default=True)

    documento_rpc_pj = fields.Binary(string="Documento RPC")
    documento_rpc_pj_name = fields.Char(string="Nombre del documento RPC", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_documento_rpc_pj = {}

    def abrir_documento_documento_rpc_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_documento_rpc_pj_en_drive()

        # Primero, verificamos si 'documento_rpc_pj_doc_id' está configurado
        if self.documento_rpc_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Documento RPC',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.documento_rpc_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'documento_rpc_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_rpc_pj_name:
            file_name_to_search = self.documento_rpc_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_rpc_pj:
                drive_document = self._cache_documentos_documento_rpc_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_rpc_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_rpc_pj_doc_id' con el ID del documento encontrado
                self.write({'documento_rpc_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Documento RPC',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Documento RPC PJ relacionado con este registro.')

    documento_rpc_pj_actualizar_visible = fields.Boolean(default=False,
                                                         related='documento_rpc_pj_doc_id.actualizar_visible',
                                                         store=True, readonly=False)

    def actualizar_correo_documento_rpc_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.documento_rpc_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.documento_rpc_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.documento_rpc_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    documento_rpc_pj_doc_id = fields.Many2one('drive', string='Documento RPC PJ', tracking=True,
                                              domain="[('stage2_id', '=', drive_file_id)]")

    documento_rpc_pj_doc_visible = fields.Boolean(default=True)

    def action_upload_documento_rpc_pj_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id,
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'documento_rpc_pj_documento_doc',
            },
        }

    def subir_documento_rpc_pj_a_drive(self):
        """Sube el documento 'Documento RPC PJ' a Google Drive y lo registra en el modelo drive."""
        if self.documento_rpc_pj and self.documento_rpc_pj_name:
            _logger.info(f"Subiendo documento '{self.documento_rpc_pj_name}' a Google Drive...")
            self._upload_documento_rpc_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Documento RPC PJ' para subir.")

    def _upload_documento_rpc_pj_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.documento_rpc_pj)

            original_extension = os.path.splitext(self.documento_rpc_pj_name)[1]

            fixed_file_name = f"10_RPC_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.documento_rpc_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.documento_rpc_pj,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo documento_rpc_pj_doc_id
            if not self.documento_rpc_pj_doc_id:
                self.documento_rpc_pj_doc_id = drive_record.id

            _logger.info(
                f"Campo documento_rpc_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar documento_rpc_pj_doc_visible a False después de subir el archivo
            self.documento_rpc_pj_doc_visible = False

            _logger.info(f"Campo documento_rpc_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    documento_rpc_pj_aprueba_f = fields.Boolean('Aprueba financiera RPC', tracking=True, store=True)
    documento_rpc_pj_aprueba_c = fields.Boolean('Aprueba Coordinador RPC', tracking=True, default=False)

    factura_cuenta_pj = fields.Binary(string="Factura o Cuenta")
    factura_cuenta_pj_name = fields.Char(string="Nombre del documento Factura o cuenta", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_factura_cuenta_pj = {}

    def abrir_documento_factura_cuenta_pj_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_factura_cuenta_pj_en_drive()

        # Primero, verificamos si 'factura_cuenta_pj_doc_id' está configurado
        if self.factura_cuenta_pj_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Factura o Cuenta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.factura_cuenta_pj_doc_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_pj_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.factura_cuenta_pj_name:
            file_name_to_search = self.factura_cuenta_pj_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_factura_cuenta_pj:
                drive_document = self._cache_documentos_factura_cuenta_pj[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_factura_cuenta_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'factura_cuenta_pj_doc_id' con el ID del documento encontrado
                self.write({'factura_cuenta_pj_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Factura o Cuenta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Factura o Cuenta PJ relacionada con este registro.')

    factura_cuenta_pj_actualizar_visible = fields.Boolean(default=False,
                                                          related='factura_cuenta_pj_doc_id.actualizar_visible',
                                                          store=True, readonly=False)

    def actualizar_correo_factura_cuenta_pj_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.factura_cuenta_pj_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.factura_cuenta_pj_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.factura_cuenta_pj_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    factura_cuenta_pj_doc_id = fields.Many2one('drive', string='Factura o Cuenta PJ', tracking=True,
                                               domain="[('stage2_id', '=', drive_file_id)]")

    factura_cuenta_pj_doc_visible = fields.Boolean(default=True)

    def action_upload_factura_cuenta_pj_doc(self):
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
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'factura_cuenta_pj_documento_doc', 
            },
        }
    

    def subir_factura_cuenta_pj_a_drive(self):
        """Sube el documento 'Factura o Cuenta PJ' a Google Drive y lo registra en el modelo drive."""
        if self.factura_cuenta_pj and self.factura_cuenta_pj_name:
            _logger.info(f"Subiendo documento '{self.factura_cuenta_pj_name}' a Google Drive...")
            self._upload_factura_cuenta_pj_to_drive_pj()
        else:
            _logger.warning("No se encontró el documento 'Factura o Cuenta PJ' para subir.")

    def _upload_factura_cuenta_pj_to_drive_pj(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.factura_cuenta_pj)

            original_extension = os.path.splitext(self.factura_cuenta_pj_name)[1]

            fixed_file_name = f"5_Factura o Cuenta_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.factura_cuenta_pj_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.factura_cuenta_pj,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo factura_cuenta_pj_doc_id
            if not self.factura_cuenta_pj_doc_id:
                self.factura_cuenta_pj_doc_id = drive_record.id

            _logger.info(
                f"Campo factura_cuenta_pj_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar factura_cuenta_pj_doc_visible a False después de subir el archivo
            self.factura_cuenta_pj_doc_visible = False

            _logger.info(f"Campo factura_cuenta_pj_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    factura_cuenta_pj_aprueba_f = fields.Boolean('Aprueba financiera Factura o cuenta', tracking=True, store=True)
    factura_cuenta_pj_aprueba_c = fields.Boolean('Aprueba Coordinador Factura o cuenta', tracking=True, default=False)

    ######################### DOCUMENTOS PARA PERSONA NATURAL #################################

    anexo_tributario_pn = fields.Boolean(string='Anexo tributario', tracking=True)
    Anexo_tributario_pn = fields.Binary(string="Anexo tributario")
    Anexo_tributario_name_pn = fields.Char(string="Nombre del documento Anexo tributario", tracking=True)
    # Diccionario de cache para almacenar resultados de búsqueda
    _cache_documentos_anexo_tributario_pn = {}

    def abrir_documento_anexo_tributario_pn_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_anexo_tributario_pn_en_drive()

        # Primero, verificamos si 'Anexo_tributario_doc_id' está configurado
        if self.Anexo_tributario_doc_pn_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Anexo tributario',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.Anexo_tributario_doc_pn_id.id,
                'target': 'new',
            }

        # Si 'Anexo_tributario_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.Anexo_tributario_name_pn:
            file_name_to_search = self.Anexo_tributario_name_pn.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_anexo_tributario_pn:
                drive_document = self._cache_documentos_anexo_tributario_pn[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_anexo_tributario_pn[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'Anexo_tributario_doc_id' con el ID del documento encontrado
                self.write({'Anexo_tributario_doc_pn_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Anexo tributario',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')
        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Anexo tributario relacionado con este registro.')

    Anexo_tributario_pn_visible = fields.Boolean(default=False, related='Anexo_tributario_doc_pn_id.actualizar_visible', store=True,
                                               readonly=False)

    def actualizar_correo_anexo_tributario_pn_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.Anexo_tributario_doc_pn_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.Anexo_tributario_doc_pn_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.Anexo_tributario_pn_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    Anexo_tributario_doc_pn_id = fields.Many2one('drive', string='Anexo tributario', tracking=True,
                                                domain="[('stage2_id', '=', drive_file_id)]")
    Anexo_tributario_doc_pn_visible = fields.Boolean(default=True)

    def action_upload_Anexo_tributario_doc(self):
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
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'Anexo_tributario_doc', 
            },
        }

    def subir_anexo_tributario_pn_a_drive(self):
        """Sube el Anexo tributario a Google Drive y lo registra en el modelo drive."""
        if self.Anexo_tributario_pn and self.Anexo_tributario_name_pn:
            _logger.info(f"Subiendo documento '{self.Anexo_tributario_name_pn}' a Google Drive...")
            self._upload_anexo_tributario_pn_to_drive()
        else:
            _logger.warning("No se encontró el Anexo tributario para subir.")

    def _upload_anexo_tributario_pn_to_drive(self):
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
            file_content = base64.b64decode(self.Anexo_tributario_pn)
            original_extension = os.path.splitext(self.Anexo_tributario_name_pn)[1]
            fixed_file_name = f"Anexo_tributario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.Anexo_tributario_name_pn} a la carpeta {carpeta_drive} en Google Drive.")

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
                'file_data': self.Anexo_tributario_pn,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Solo actualizar el campo Anexo_tributario_doc_id
            if not self.Anexo_tributario_doc_pn_id:
                self.Anexo_tributario_doc_pn_id = drive_record.id

            _logger.info(f"Campo Anexo_tributario_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar Anexo_tributario_doc_pn_visible a False después de subir el archivo
            self.Anexo_tributario_doc_pn_visible = False

            _logger.info(f"Campo Anexo_tributario_doc_pn_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    anexo_tributario_pn_aprueba_f = fields.Boolean(string="Anexo tributario aprobado por Coordinador", default=False)
    anexo_tributario_pn_aprueba_c = fields.Boolean(string="Anexo tributario aprobado por Financiero", default=False)




    rut_pn = fields.Binary(string="RUT Persona Natural")
    rut_pn_name = fields.Char(string="Nombre del documento RUT PN", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_rut_pn = {}

    def abrir_documento_rut_pn_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_rut_pn_en_drive()

        # Primero, verificamos si 'rut_pn_doc_id' está configurado
        if self.rut_pn_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'RUT',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.rut_pn_doc_id.id,
                'target': 'new',
            }

        # Si 'rut_pn_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rut_pn_name:
            file_name_to_search = self.rut_pn_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rut_pn:
                drive_document = self._cache_documentos_rut_pn[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_rut_pn[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rut_pn_doc_id' con el ID del documento encontrado
                self.write({'rut_pn_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'RUT',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún RUT Persona Natural relacionado con este registro.')

    rut_pn_actualizar_visible = fields.Boolean(default=False, related='rut_pn_doc_id.actualizar_visible', store=True,
                                               readonly=False)

    def actualizar_correo_rut_pn_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.rut_pn_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.rut_pn_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.rut_pn_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    rut_pn_doc_id = fields.Many2one('drive', string='RUT Persona Natural', tracking=True,
                                    domain="[('stage2_id', '=', drive_file_id)]")

    rut_pn_doc_visible = fields.Boolean(default=True)

    def action_upload_rut_pn(self):
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
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'rut_natural_doc', 
            },
        }

    def subir_rut_pn_a_drive(self):
        """Sube el documento 'RUT Persona Natural' a Google Drive y lo registra en el modelo drive."""
        if self.rut_pn and self.rut_pn_name:
            _logger.info(f"Subiendo documento '{self.rut_pn_name}' a Google Drive...")
            self._upload_rut_pn_to_drive()
        else:
            _logger.warning("No se encontró el documento 'RUT Persona Natural' para subir.")

    def _upload_rut_pn_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.rut_pn)

            original_extension = os.path.splitext(self.rut_pn_name)[1]

            fixed_file_name = f"1_RUT_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.rut_pn_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.rut_pn,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo rut_pn_doc_id
            if not self.rut_pn_doc_id:
                self.rut_pn_doc_id = drive_record.id

            _logger.info(f"Campo rut_pn_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar rut_pn_doc_visible a False después de subir el archivo
            self.rut_pn_doc_visible = False

            _logger.info(f"Campo rut_pn_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    rut_pn_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', default=True)
    rut_pn_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)

    documento_identidad = fields.Binary(string="Documento Identidad")
    documento_identidad_name = fields.Char(string="Nombre del documento CEDULA", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_documento_identidad = {}

    def abrir_documento_documento_identidad_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_documento_identidad_en_drive()

        # Primero, verificamos si 'documento_identidad_doc_id' está configurado
        if self.documento_identidad_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Documento Identidad',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.documento_identidad_doc_id.id,
                'target': 'new',
            }

        # Si 'documento_identidad_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_identidad_name:
            file_name_to_search = self.documento_identidad_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_identidad:
                drive_document = self._cache_documentos_documento_identidad[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_identidad[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_identidad_doc_id' con el ID del documento encontrado
                self.write({'documento_identidad_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Documento Identidad',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Documento Identidad relacionado con este registro.')

    documento_identidad_actualizar_visible = fields.Boolean(default=False,
                                                            related='documento_identidad_doc_id.actualizar_visible',
                                                            store=True, readonly=False)

    def actualizar_correo_documento_identidad_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.documento_identidad_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.documento_identidad_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.documento_identidad_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    documento_identidad_doc_id = fields.Many2one('drive', string='Documento Identidad', tracking=True,
                                                 domain="[('stage2_id', '=', drive_file_id)]")

    documento_identidad_doc_visible = fields.Boolean(default=True)

    def action_upload_documento_identidad_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir documento identidad',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'documento_identidad_doc', 
            },
        }

    def subir_documento_identidad_a_drive(self):
        """Sube el documento 'Documento Identidad' a Google Drive y lo registra en el modelo drive."""
        if self.documento_identidad and self.documento_identidad_name:
            _logger.info(f"Subiendo documento '{self.documento_identidad_name}' a Google Drive...")
            self._upload_documento_identidad_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Documento Identidad' para subir.")

    def _upload_documento_identidad_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.documento_identidad)

            original_extension = os.path.splitext(self.documento_identidad_name)[1]

            fixed_file_name = f"4_Documento Identidad_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.documento_identidad_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.documento_identidad,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo documento_identidad_doc_id
            if not self.documento_identidad_doc_id:
                self.documento_identidad_doc_id = drive_record.id

            _logger.info(
                f"Campo documento_identidad_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar documento_identidad_doc_visible a False después de subir el archivo
            self.documento_identidad_doc_visible = False

            _logger.info(f"Campo documento_identidad_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    documento_identidad_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', default=True)
    documento_identidad_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True,
                                                       store=True)

    planilla = fields.Binary(string="Planilla")
    planilla_name = fields.Char(string="Nombre del documento Planilla", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_planilla = {}

    def abrir_documento_planilla_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_planilla_en_drive()

        # Primero, verificamos si 'planilla_doc_id' está configurado
        if self.planilla_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Planilla',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.planilla_doc_id.id,
                'target': 'new',
            }

        # Si 'planilla_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.planilla_name:
            file_name_to_search = self.planilla_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_planilla:
                drive_document = self._cache_documentos_planilla[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_planilla[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'planilla_doc_id' con el ID del documento encontrado
                self.write({'planilla_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Planilla',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ninguna Planilla relacionada con este registro.')

    planilla_actualizar_visible = fields.Boolean(default=False, related='planilla_doc_id.actualizar_visible',
                                                 store=True, readonly=False)

    def actualizar_correo_planilla_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.planilla_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.planilla_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.planilla_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    planilla_doc_id = fields.Many2one('drive', string='Planilla', tracking=True,
                                      domain="[('stage2_id', '=', drive_file_id)]")

    planilla_doc_visible = fields.Boolean(default=True)

    def action_upload_planilla_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir planilla',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'planilla_natural_doc', 
            },
        }

    def subir_planilla_a_drive(self):
        """Sube el documento 'Planilla' a Google Drive y lo registra en el modelo drive."""
        if self.planilla and self.planilla_name:
            _logger.info(f"Subiendo documento '{self.planilla_name}' a Google Drive...")
            self._upload_planilla_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Planilla' para subir.")

    def _upload_planilla_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.planilla)

            original_extension = os.path.splitext(self.planilla_name)[1]

            fixed_file_name = f"2_Planilla_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.planilla_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.planilla,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo planilla_doc_id
            if not self.planilla_doc_id:
                self.planilla_doc_id = drive_record.id

            _logger.info(f"Campo planilla_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar planilla_doc_visible a False después de subir el archivo
            self.planilla_doc_visible = False

            _logger.info(f"Campo planilla_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    planilla_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', default=True)
    planilla_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)

    certificado_bancario = fields.Binary(string="Certficado Bancario")
    certificado_bancario_name = fields.Char(string="Nombre del documento Cert Bancario", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_certificado_bancario = {}

    def abrir_documento_certificado_bancario_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_certificado_bancario_en_drive()

        # Primero, verificamos si 'certificado_bancario_doc_id' está configurado
        if self.certificado_bancario_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Certificado Bancario',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.certificado_bancario_doc_id.id,
                'target': 'new',
            }

        # Si 'certificado_bancario_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.certificado_bancario_name:
            file_name_to_search = self.certificado_bancario_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_bancario:
                drive_document = self._cache_documentos_certificado_bancario[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_bancario[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'certificado_bancario_doc_id' con el ID del documento encontrado
                self.write({'certificado_bancario_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Certificado Bancario',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error
        raise UserError('No hay ningún Certificado Bancario relacionado con este registro.')

    certificado_bancario_actualizar_visible = fields.Boolean(default=False,
                                                             related='certificado_bancario_doc_id.actualizar_visible',
                                                             store=True, readonly=False)

    def actualizar_correo_certificado_bancario_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_bancario_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_bancario_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.certificado_bancario_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    certificado_bancario_doc_id = fields.Many2one('drive', string='Certificado Bancario', tracking=True,
                                                  domain="[('stage2_id', '=', drive_file_id)]")

    certificado_bancario_doc_visible = fields.Boolean(default=True)
    
    def action_upload_certificado_bancario_doc(self):
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
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'certificado_bancario_doc', 
            },
        }



    def subir_certificado_bancario_a_drive(self):
        """Sube el documento 'Certificado Bancario' a Google Drive y lo registra en el modelo drive."""
        if self.certificado_bancario and self.certificado_bancario_name:
            _logger.info(f"Subiendo documento '{self.certificado_bancario_name}' a Google Drive...")
            self._upload_certificado_bancario_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Certificado Bancario' para subir.")

    def _upload_certificado_bancario_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.certificado_bancario)

            original_extension = os.path.splitext(self.certificado_bancario_name)[1]

            fixed_file_name = f"5_Certificado Bancario_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(
                f"Subiendo archivo {self.certificado_bancario_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.certificado_bancario,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo certificado_bancario_doc_id
            if not self.certificado_bancario_doc_id:
                self.certificado_bancario_doc_id = drive_record.id

            _logger.info(
                f"Campo certificado_bancario_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar certificado_bancario_doc_visible a False después de subir el archivo
            self.certificado_bancario_doc_visible = False

            _logger.info(f"Campo certificado_bancario_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    certificado_bancario_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', default=True)
    certificado_bancario_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True,
                                                        store=True)

    documento_rpc = fields.Binary(string="Documento RPC")
    documento_rpc_name = fields.Char(string="Nombre del documento RPC", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_documento_rpc = {}

    def abrir_documento_documento_rpc_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_documento_rpc_en_drive()

        # Primero, verificamos si 'documento_rpc_doc_id' está configurado
        if self.documento_rpc_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Documento RPC',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.documento_rpc_doc_id.id,
                'target': 'new',
            }

        # Si 'documento_rpc_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_rpc_name:
            file_name_to_search = self.documento_rpc_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_rpc:
                drive_document = self._cache_documentos_documento_rpc[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_rpc[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_rpc_doc_id' con el ID del documento encontrado
                self.write({'documento_rpc_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Documento RPC',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error

        raise UserError('No hay ningún Documento RPC relacionado con este registro.')

    documento_rpc_actualizar_visible = fields.Boolean(default=False, related='documento_rpc_doc_id.actualizar_visible',
                                                      store=True, readonly=False)

    def actualizar_correo_documento_rpc_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.documento_rpc_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.documento_rpc_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.documento_rpc_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    documento_rpc_doc_id = fields.Many2one('drive', string='Documento RPC', tracking=True,
                                           domain="[('stage2_id', '=', drive_file_id)]")

    documento_rpc_doc_visible = fields.Boolean(default=True)

    def action_upload_documento_rpc_doc(self):
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
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'documento_rpc_natural_doc', 
            },
        }


    def subir_documento_rpc_a_drive(self):
        """Sube el documento 'Documento RPC' a Google Drive y lo registra en el modelo drive."""
        if self.documento_rpc and self.documento_rpc_name:
            _logger.info(f"Subiendo documento '{self.documento_rpc_name}' a Google Drive...")
            self._upload_documento_rpc_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Documento RPC' para subir.")

    def _upload_documento_rpc_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.documento_rpc)

            original_extension = os.path.splitext(self.documento_rpc_name)[1]

            fixed_file_name = f"6_Documento RPC_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.documento_rpc_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.documento_rpc,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo documento_rpc_doc_id
            if not self.documento_rpc_doc_id:
                self.documento_rpc_doc_id = drive_record.id

            _logger.info(f"Campo documento_rpc_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar documento_rpc_doc_visible a False después de subir el archivo
            self.documento_rpc_doc_visible = False

            _logger.info(f"Campo documento_rpc_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True

    # Valores booleanos para APROBACION
    doc_rpc_aprueba_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', default=False)
    doc_rpc_aprueba_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)

    factura_cuenta = fields.Binary(string="Factura o Cuenta")
    factura_cuenta_name = fields.Char(string="Nombre del documento Factura o cuenta", tracking=True)

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_factura_cuenta = {}

    def abrir_documento_factura_cuenta_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        self.actualizar_correo_factura_cuenta_en_drive()

        # Primero, verificamos si 'factura_cuenta_doc_id' está configurado
        if self.factura_cuenta_doc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Factura o Cuenta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.factura_cuenta_doc_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.factura_cuenta_name:
            file_name_to_search = self.factura_cuenta_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_factura_cuenta:
                drive_document = self._cache_documentos_factura_cuenta[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_factura_cuenta[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'factura_cuenta_doc_id' con el ID del documento encontrado
                self.write({'factura_cuenta_doc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Factura o Cuenta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        # Si no hay ni relación ni nombre de archivo, lanzar un error

        raise UserError('No hay ninguna Factura o Cuenta relacionada con este registro.')

    factura_cuenta_actualizar_visible = fields.Boolean(default=False,
                                                       related='factura_cuenta_doc_id.actualizar_visible', store=True,
                                                       readonly=False)

    def actualizar_correo_factura_cuenta_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.factura_cuenta_doc_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.factura_cuenta_doc_id.write({
                'correo_electrinico': self.email,
                'actualizar_visible': self.factura_cuenta_actualizar_visible,
                'file_data_visible': self.file_data_visible,
            })
            return True
        else:
            return False

    factura_cuenta_doc_id = fields.Many2one('drive', string='Factura o Cuenta', tracking=True,
                                            domain="[('stage2_id', '=', drive_file_id)]")

    factura_cuenta_doc_visible = fields.Boolean(default=True)

    def action_upload_factura_cuenta_doc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir factura o cuenta',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_servicios_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'factura_o_cuenta_doc', 
            },
        }



    def subir_factura_cuenta_a_drive(self):
        """Sube el documento 'Factura o Cuenta' a Google Drive y lo registra en el modelo drive."""
        if self.factura_cuenta and self.factura_cuenta_name:
            _logger.info(f"Subiendo documento '{self.factura_cuenta_name}' a Google Drive...")
            self._upload_factura_cuenta_to_drive()
        else:
            _logger.warning("No se encontró el documento 'Factura o Cuenta' para subir.")

    def _upload_factura_cuenta_to_drive(self):
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

        carpeta_drive = drive_file_record.folder_id2

        if not carpeta_drive:
            _logger.error(
                f"No se encontró una carpeta en Drive para el archivo {self.drive_file_id.id}. No se subirá el archivo.")
            return False

        try:
            # Decodificar el archivo binario
            file_content = base64.b64decode(self.factura_cuenta)

            original_extension = os.path.splitext(self.factura_cuenta_name)[1]

            fixed_file_name = f"3_Factura o Cuenta_{uuid.uuid4()}{original_extension}"

            # Crear archivo temporal para subir a Drive
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            _logger.info(f"Subiendo archivo {self.factura_cuenta_name} a la carpeta {carpeta_drive} en Google Drive.")

            # Crear el archivo en Google
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
                'file_data': self.factura_cuenta,
                'folder_id': carpeta_drive,
                'drive_file_id': self.drive_file_id.id,
                'stage2_id': self.drive_file_id.id
            })

            _logger.info(
                f"Registro en Odoo creado en el modelo drive con ID {drive_record.id} y drive_file_id {self.drive_file_id.id}")

            # Mantener las relaciones previas (por ejemplo, 'rut_id' y otros)
            # Solo actualizar el campo factura_cuenta_doc_id
            if not self.factura_cuenta_doc_id:
                self.factura_cuenta_doc_id = drive_record.id

            _logger.info(f"Campo factura_cuenta_doc_id actualizado con el ID del registro en drive: {drive_record.id}")

            # Actualizar factura_cuenta_doc_visible a False después de subir el archivo
            self.factura_cuenta_doc_visible = False

            _logger.info(f"Campo factura_cuenta_doc_visible actualizado a False.")

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True
    # Valores booleanos para APROBACION
    factura_cuenta_aprueba_doc_c = fields.Boolean('Aprueba Coordinador RUT Persona Natural', default=False)
    factura_cuenta_aprueba_doc_f = fields.Boolean('Aprueba financiera RUT Persona Natural', tracking=True, store=True)

    ################### Campo para rastrear la aprobación del financiero
    financiera_aprobado = fields.Boolean(string="Aprobado por Financiera", default=False, tracking=True)

    ################### Observacion del Coordinador ##########################
    observacion_coordinador = fields.Text('Observaciones: ', tracking=True)

    ################### Observacion del Financiero ##########################
    observacion_financiera = fields.Text('Observaciones: ', tracking=True)

    ############################ FUNCIONES (DEF) PARA EL CAMBIO DE ESTADO CON SU RESPECTIVO NOTIFICACION DE CORREO ############################
    def aprobar_orden_financiera(self):
        # Cambiar el estado a "Financiera"
        self.write({'estado_compra_servicios': 'financiera'})

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_s

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo DESEADOS que están relacionados con el proyecto
        usuarios_financieros = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id or
                         self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id
        )

        # Verificar si hay usuarios financieros relacionados con el proyecto
        if not usuarios_financieros:
            raise UserError("No hay usuarios financieros asociados al proyecto.")

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones del Coordinadorl (si las hay)
        observacion_coordinador = self.observacion_coordinador or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_s.name, self.proyecto_relacionado_s.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener el nombre del registro
        nombre_registro = self.name  # Usa el campo que almacena el nombre del registro

        # Construir el mensaje del correo
        mensaje = f"""
                    <p><strong>{nombre_proyecto}</strong></p>
                    <p>Hola, financiero:</p>
                    <p>El coordinador ha <strong>aprobado</strong> el documento orden de compra de servicios. Proveedor <strong>{nombre_registro}</strong>. Por favor, revisarlo.</p>
                    {f'<p><strong>Observaciones:</strong> {observacion_coordinador}</p>' if observacion_coordinador else ''}
                """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_servicios'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario financiero
        for financiero in usuarios_financieros:
            try:
                body_html = _orden_compra_email_body(
                    "Documento orden de compra de servicios aprobado",
                    "financiero",
                    f"""
                        <p>El coordinador ha <strong>aprobado</strong> el documento orden de compra de servicios. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Por favor, revisarlo.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_coordinador,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Documento orden de compra de servicios fue aprobado. Proveedor {nombre_registro}",
                    'email_from': coordinador.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': financiero.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }

    def no_aprobado_coordinador(self):
        # Cambiar el estado a "financiera" (para el grupo coordinador)
        self.write({'estado_compra_servicios': 'financiera'})

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_s

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo DESEADOS que están relacionados con el proyecto
        usuarios_financieros = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id or
                         self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id
        )

        # Verificar si hay usuarios financieros relacionados con el proyecto
        if not usuarios_financieros:
            raise UserError("No hay usuarios financieros asociados al proyecto.")

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones (si las hay)
        observacion_coordinador = self.observacion_coordinador or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_s.name, self.proyecto_relacionado_s.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # # Obtener el nombre del registro
        nombre_registro = self.name  # Usa el campo que almacena el nombre del registro

        # Construir el mensaje del correo
        mensaje = f"""
                            <p><strong>{nombre_proyecto}</strong></p>
                            <p>Hola, coordinador:</p>
                            <p>Hola, financiero:</p>
                            <p>El coordinador <strong>no aprobó</strong> el documento orden de compra de servicios. Proveedor <strong>{nombre_registro}</strong>. Por favor, revisarlo.</p>

                            {f'<p><strong>Observaciones:</strong> {observacion_coordinador}</p>' if observacion_coordinador else ''}
                        """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_servicios'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario financiero
        for financiero in usuarios_financieros:
            try:
                body_html = _orden_compra_email_body(
                    "Documento orden de compra de servicios no aprobado",
                    "financiero",
                    f"""
                        <p>El coordinador <strong>no aprobó</strong> el documento orden de compra de servicios. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Por favor, revisarlo.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_coordinador,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Documento orden de compra de servicios no fue aprobado. Proveedor {nombre_registro}",
                    'email_from': coordinador.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': financiero.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }

    def finalizar_contratado(self):
        # Cambiar el estado a "Contratado" (para el grupo coordinador)
        self.write({'estado_compra_servicios': 'contratado'})

    def aprobar_orden_coordinador(self):
        # Cambiar el estado a "Coordinador"
        self.write({
            'estado_compra_servicios': 'coordinador',
            'financiera_aprobado': True,  # Marcar como aprobado
        })

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_s

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo "group_contratacion_coordinador" que están relacionados con el proyecto
        usuarios_coordinadores = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id or self.env.ref('Matriz.group_contratacion_asis_coordinador') in user.groups_id
        )
        # Verificar si hay usuarios coordinadores relacionados con el proyecto
        if not usuarios_coordinadores:
            raise UserError("No hay usuarios coordinadores asociados al proyecto.")

        # Obtener el usuario financiero actual
        financiero = self.env.user

        # Obtener las observaciones de Financiera (si las hay)
        observacion_financiera = self.observacion_financiera or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_s.name, self.proyecto_relacionado_s.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener el nombre del registro
        nombre_registro = self.name or "Sin nombre"  # Usa el campo que almacena el nombre del registro

        # Construir el mensaje del correo
        mensaje = f"""
                    <p><strong>{nombre_proyecto}</strong></p>
                    <p>Hola, coordinador:</p>
                    <p>El financiero ha <strong>aprobado</strong> el documento orden de compra de servicios. Proveedor <strong>{nombre_registro}</strong>. Queda a su disposición para los fines pertinentes.</p>
                    {f'<p><strong>Observaciones:</strong> {observacion_financiera}</p>' if observacion_financiera else ''}
                """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_servicios'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario coordinador
        for coordinador in usuarios_coordinadores:
            try:
                body_html = _orden_compra_email_body(
                    "Aprobación de documento orden de compra de servicios",
                    "coordinador",
                    f"""
                        <p>El financiero ha <strong>aprobado</strong> el documento orden de compra de servicios. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Queda a su disposición para los fines pertinentes.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_financiera,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Aprobación de documento orden de compra de servicios. Proveedor {nombre_registro}",
                    'email_from': financiero.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': coordinador.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }

    def no_aprobado_financiera(self):
        # Cambiar el estado a "Coordinador" y marcar como no aprobado
        self.write({
            'estado_compra_servicios': 'coordinador',
            'financiera_aprobado': False,  # Marcar como no aprobado
        })

        # Obtener el proyecto relacionado
        proyecto = self.proyecto_relacionado_s

        if not proyecto:
            raise UserError("No hay un proyecto relacionado para enviar la notificación.")

        # Obtener los usuarios del grupo "group_contratacion_coordinador" que están relacionados con el proyecto
        usuarios_coordinadores = proyecto.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id or self.env.ref('Matriz.group_contratacion_asis_coordinador') in user.groups_id
        )

        # Verificar si hay usuarios coordinadores relacionados con el proyecto
        if not usuarios_coordinadores:
            raise UserError("No hay usuarios coordinadores asociados al proyecto.")

        # Obtener el usuario financiero actual
        financiero = self.env.user

        # Obtener las observaciones (si las hay)
        observacion_financiera = self.observacion_financiera or ''

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado_s.name, self.proyecto_relacionado_s.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # # Obtener el nombre del registro
        nombre_registro = self.name  # Usa el campo que almacena el nombre del registro

        # Construir el mensaje del correo
        mensaje = f"""
                    <p><strong>{nombre_proyecto}</strong></p>
                    <p>Hola, coordinador:</p>
                    <p>El financiero <strong>no aprobó</strong> el documento orden de compra de servicios. Proveedor <strong>{nombre_registro}</strong>. Por favor, revisarlo.</p>
                    {f'<p><strong>Observaciones:</strong> {observacion_financiera}</p>' if observacion_financiera else ''}
                """

        # Construir la URL del formulario
        formulario_url = f'/web#id={self.id}&view_type=form&model=orden_compra_servicios'
        formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

        # Enviar correo electrónico a cada usuario coordinador
        for coordinador in usuarios_coordinadores:
            try:
                body_html = _orden_compra_email_body(
                    "Documento orden de compra de servicios no aprobado",
                    "coordinador",
                    f"""
                        <p>El financiero <strong>no aprobó</strong> el documento orden de compra de servicios. Proveedor <strong>{html_escape(nombre_registro)}</strong>. Por favor, revisarlo.</p>
                        <div style="margin: 16px 0; padding: 12px 14px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                            <strong>Proyecto:</strong> {html_escape(nombre_proyecto)}
                        </div>
                    """,
                    formulario_url,
                    observacion_financiera,
                )
                mail_values = {
                    'subject': f"{nombre_proyecto} - Documento orden de compra de servicios no fue aprobado. Proveedor {nombre_registro}",
                    'email_from': financiero.email or 'no-reply@tuempresa.com',  # Correo del remitente
                    'email_to': coordinador.email,  # Correo del destinatario
                    'body_html': body_html,  # Cuerpo del correo en HTML
                    'auto_delete': True,  # Eliminar el correo después de enviarlo
                }

                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()
            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

        # Mostrar notificación de éxito
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'sticky': False,
                'message': 'El mensaje ha sido enviado correctamente.',
                'type': 'success',
                'message_sticky': False,
                'message_duration': 9,
            }
        }

    # Método para calcular el subtotal
    @api.depends('lineas_ids.valor_unitario')
    def _compute_subtotal(self):
        for record in self:
            record.subtotal = sum(line.valor_unitario for line in record.lineas_ids)

    # Método para calcular el IVA (19% del subtotal)
    # @api.depends('subtotal')
    # def _compute_iva(self):
    #     for record in self:
    #         record.iva = record.subtotal * 0.19

    # Método para calcular el total (subtotal + IVA)

class OrdenCompraServiciosLinea(models.Model):
    _name = 'orden_compra_servicios.linea'
    _description = 'Línea de Orden de Compra de Servicios'

    # Campos para la línea de la orden
    descripcion = fields.Char(string='Descripción', required=True, tracking=True)
    valor_unitario = fields.Float(string='Valor Unitario', required=True, tracking=True)

    # Relación Many2one con la orden de compra de servicios
    orden_id = fields.Many2one('orden_compra_servicios', string='Orden de Compra', ondelete='cascade')

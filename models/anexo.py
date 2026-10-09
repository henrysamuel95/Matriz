#!/usr/bin/python
from time import sleep
from odoo import models, fields, api
from datetime import datetime, timedelta
from .drive import GoogleDriveAuth
import threading
import uuid
import time
from odoo.modules.module import get_module_resource
from odoo.exceptions import UserError
from odoo.exceptions import AccessError  # type: ignore
from odoo.exceptions import ValidationError  # type: ignore
from datetime import date
from attr import exceptions
import os
import tempfile
import base64
from docx import Document  # type: ignore
from docx.api import Document as DocumentApi  # type: ignore
from docxtpl import DocxTemplate  # type: ignore
from io import BytesIO
import logging

_logger = logging.getLogger(__name__) 


class Anexo(models.Model):
    _name = 'anexo'
    _description = 'Anexo'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string="Para", required=True)
    name_servicios = fields.Char(string="Para", related="orden_compra_servicios_id.name")

    def name_get(self):
        result = []
        for record in self:
            display_name = "Anexo - {}".format(record.name or "")
            result.append((record.id, display_name))
        return result


    # Relación inversa
    orden_compra_bienes_id = fields.Many2one(
        'orden_compra_bienes',
        string="Orden de bienes"
    )

    orden_compra_servicios_id = fields.Many2one(
        'orden_compra_servicios',
        string="Orden de servicios"
    )
    ####################################################################################################################
    # Campos solicitados
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    informe_adjunto = fields.Binary(
        string='Informe Adjunto',
        attachment=True,
        readonly=True,
    )

    nombre_del_proyecto = fields.Char(
        string="Información tributaria del convenio y/o contrato",
        compute="_compute_campos_orden",
        store=True
    )
    objeto_contractual = fields.Text(
        string="Objeto Contractual",
        compute="_compute_campos_orden",
        store=True
    )
    codigo_ciiu = fields.Char(
        string="Código CIIU 2",
        compute="_compute_campos_orden",
        store=True
    )
    valor_contrato = fields.Float(
        string="Valor Contrato",
        compute="_compute_campos_orden",
        store=True
    )
    forma_pago2 = fields.Char(
        string="Forma de Pago 2",
        compute="_compute_campos_orden",
        store=True
    )
    f_electronica = fields.Boolean(
        string="Factura Electrónica",
        compute="_compute_campos_orden",
        store=True
    )
    responsable = fields.Boolean(
        string='Responsable',
        compute="_compute_campos_orden",
        store=True
    )
    no_responsable = fields.Boolean(
        string='No responsable',
        compute="_compute_campos_orden",
        store=True
    )
    centro_costo = fields.Char(
        string="Centro de costo",
        compute="_compute_campos_orden",
        store=True
    )
    declaracion_renta = fields.Boolean(string="Declaración Renta", compute="_compute_campos_orden")
    
    regimen_simple_tributacion = fields.Boolean(string="Régimen simple de tributación", compute="_compute_campos_orden")
    regimen_especial = fields.Boolean(string="Régimen Especial", compute="_compute_campos_orden")
    regimen_ordinario = fields.Boolean(string="Régimen Ordinario", compute="_compute_campos_orden")
    
    
    
    @api.depends(
        "orden_compra_bienes_id", 
        "orden_compra_bienes_id.nombre_proyecto_relacionado",
        "orden_compra_bienes_id.objeto_contractual", 
        "orden_compra_bienes_id.codigo_ciiu",
        "orden_compra_bienes_id.total", 
        "orden_compra_bienes_id.forma_pago1",
        "orden_compra_bienes_id.f_electronica", 
        "orden_compra_bienes_id.r_iva",
        "orden_compra_bienes_id.declaracion_renta", 
        "orden_compra_bienes_id.regimen_simple_tributacion",
        "orden_compra_bienes_id.regimen_especial", 
        "orden_compra_bienes_id.regimen_ordinario",
        "orden_compra_bienes_id.no_r_iva", 
        "orden_compra_bienes_id.centro_costo",
        "orden_compra_servicios_id", 
        "orden_compra_servicios_id.nombre_proyecto_relacionado",
        "orden_compra_servicios_id.objeto_contractual", 
        "orden_compra_servicios_id.codigo_ciiu",
        "orden_compra_servicios_id.total", 
        "orden_compra_servicios_id.forma_pago1",
        "orden_compra_servicios_id.f_electronica", 
        "orden_compra_servicios_id.r_iva",
        "orden_compra_servicios_id.no_r_iva", 
        "orden_compra_servicios_id.centro_costo",
        # --- NUEVOS CAMPOS AÑADIDOS PARA SERVICIOS ---
        "orden_compra_servicios_id.declaracion_renta", 
        "orden_compra_servicios_id.regimen_simple_tributacion",
        "orden_compra_servicios_id.regimen_especial", 
        "orden_compra_servicios_id.regimen_ordinario",
    )
    def _compute_campos_orden(self):
        for record in self:
            # Alias para facilitar la lectura
            b = record.orden_compra_bienes_id
            s = record.orden_compra_servicios_id

            # Usamos "or" para tomar el valor de donde exista información
            record.nombre_del_proyecto = b.nombre_proyecto_relacionado or s.nombre_proyecto_relacionado or False
            record.objeto_contractual = b.objeto_contractual or s.objeto_contractual or False
            record.codigo_ciiu = b.codigo_ciiu or s.codigo_ciiu or False
            record.valor_contrato = b.total or s.total or 0.0
            record.forma_pago2 = b.forma_pago1 or s.forma_pago1 or False
            record.f_electronica = b.f_electronica or s.f_electronica or False
            
            # Campos tributarios
            record.declaracion_renta = b.declaracion_renta or s.declaracion_renta or False
            record.regimen_simple_tributacion = b.regimen_simple_tributacion or s.regimen_simple_tributacion or False
            record.regimen_especial = b.regimen_especial or s.regimen_especial or False
            record.regimen_ordinario = b.regimen_ordinario or s.regimen_ordinario or False
            
            record.responsable = b.r_iva or s.r_iva or False
            record.no_responsable = b.no_r_iva or s.no_r_iva or False
            record.centro_costo = b.centro_costo or s.centro_costo or False

    fecha = fields.Date(string="Fecha", compute="_compute_fecha", store=True)

    @api.depends("orden_compra_bienes_id.fecha_solicitud", "orden_compra_servicios_id.fecha_solicitud")
    def _compute_fecha(self):
        for record in self:
            if record.orden_compra_bienes_id and record.orden_compra_bienes_id.fecha_solicitud:
                record.fecha = record.orden_compra_bienes_id.fecha_solicitud
            elif record.orden_compra_servicios_id and record.orden_compra_servicios_id.fecha_solicitud:
                record.fecha = record.orden_compra_servicios_id.fecha_solicitud
            else:
                record.fecha = False

    anio_ejecucion2 = fields.Integer(string="Año Ejecución", compute='compute_anio_fecha', store=True)


    def compute_anio_fecha(self):
        """
        Calcula el año basado en la fecha de terminación.
        """
        for record in self:
            if record.fecha:
                # Extraer el año de la fecha (record.fecha es tipo date/datetime)
                record.anio_ejecucion2 = record.fecha.year
                print("Se puso el año {anio_ejecucion2}")
            else:
                # Si no hay fecha, dejar vacío (NULL en BD)
                record.anio_ejecucion2 = False



    consecutivo_unido = fields.Char(string="Consecutivo", compute="generar_datos_tributarios")

    def generar_datos_tributarios(self):
        """Genera anio_ejecucion2 y consecutivo_unido una sola vez por registro"""
        for record in self:
            self.compute_anio_fecha()
            if record.consecutivo_unido:
                continue  # Ya tiene consecutivo, no recalcula

            centro_costo = self.centro_costo
            anio = str(record.anio_ejecucion2)  # Usa el valor del registro actual y lo convierte a texto

            count = record.id
            consecutivo = f"AT-{count:03d}-{centro_costo}-{anio}"
            print(f"✅ Consecutivo generado: {consecutivo}")

            record.consecutivo_unido = consecutivo

    def render_informe_template_bienes(self):
         # 1. Odoo encuentra la ruta de tu módulo "Matriz" automáticamente
        current_directory = get_module_resource('Matriz', 'static/src/docx')
        
        # Fíjate que aquí puse el nombre del archivo de Word que me pasaste en tu ruta
        template_path = os.path.join(current_directory, "plantilla_anexo_tributario_ordenes.docx")
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
        output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}_rendered_plantilla_anexo_tributario_ordenes.docx")
        template.save(output_path)
        return output_path

    def generar_informe_docx(self):
        template_rendered = self.render_informe_template_bienes()

        nombre_base = self.name or self.name_servicios or 'Informe_anexo_orden_compra'
        nombre_archivo = "Anexo orden de compra de bienes o servicios {}".format(nombre_base)

        drive_record = self._subir_plantilla_a_drive(template_rendered, nombre_archivo)

        if  drive_record:
            drive_record.view_mode = 'edit'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Drive',
                'res_model': 'drive',
                'res_id': drive_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

        # Fallback si no hay carpeta Drive asociada
        record_id = self.id
        documento = Document()
        buffer = BytesIO()
        documento.save(buffer)
        with open(template_rendered, 'rb') as template_file:
            self.write({'informe_adjunto': base64.b64encode(template_file.read())})
        return {
            'name': '{}.docx'.format(nombre_archivo),
            'type': 'ir.actions.act_url',
            'url': '/web/content/?model=anexo&id={}&filename_field=informe_adjunto&field=informe_adjunto&download=true&filename={}.docx'.format(
                record_id, nombre_archivo),
            'target': 'self',
        }

    def _subir_plantilla_a_drive(self, file_path, nombre_archivo):
        orden = self.orden_compra_bienes_id or self.orden_compra_servicios_id
        if not orden or not orden.drive_file_id or not orden.drive_file_id.folder_id2:
            _logger.warning("No hay carpeta de Drive asociada al registro. No se sube la plantilla.")
            return None
        try:
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()
            folder_id = orden.drive_file_id.folder_id2
            file_name = "{}.docx".format(nombre_archivo)

            query = f"title='{file_name}' and '{folder_id}' in parents and trashed=false"
            file_list = drive.ListFile({'q': query}).GetList()

            if file_list:
                gfile = file_list[0]
                gfile.SetContentFile(file_path)
                gfile.Upload(param={'supportsAllDrives': True})
                _logger.info(f"Archivo '{file_name}' actualizado en Google Drive.")
            else:
                gfile = drive.CreateFile({
                    'title': file_name,
                    'parents': [{'id': folder_id}]
                })
                gfile.SetContentFile(file_path)
                gfile.Upload(param={'supportsAllDrives': True})
                _logger.info(f"Archivo '{file_name}' subido a Google Drive en carpeta {folder_id}.")

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
                    'stage2_id': orden.drive_file_id.id,
                })
            else:
                drive_record = self.env['drive'].create({
                    'file_name': gfile['title'],
                    'file_data': base64.b64encode(file_data),
                    'file_url': gfile['alternateLink'],
                    'drive_file_id': gfile['id'],
                    'edit_url': gfile['alternateLink'],
                    'folder_id': folder_id,
                    'stage2_id': orden.drive_file_id.id,
                })
            return drive_record
        except Exception as e:
            _logger.error(f"Error al subir la plantilla a Google Drive: {e}")
            return None


    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################

    departamentos = fields.Char(string="Departamentos")
    municipios2 = fields.Char(string="Municipios 2")




    tarifa_bd = fields.Float(string="Tarifa BD")
    total_departamentos = fields.Float(string="Total Departamentos")



    excluido = fields.Boolean(string='Excluido')
    exento = fields.Boolean(string='Exento')
    retencion_iva2 = fields.Boolean(string='Retención IVA', compute='_compute_retencion_iva2', store=True, readonly=False)

    @api.depends('responsable')
    def _compute_retencion_iva2(self):
        for record in self:
            record.retencion_iva2 = record.responsable

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

    # Sección Otros




    
    # Variables de retención
    retencion2_visible = fields.Boolean(string="Retención 2 Visible")
    concepto_2 = fields.Many2one(comodel_name='concepto', string="CONCEPTO")
    base = fields.Char(string="Base 2")
    retencion2 = fields.Char(string="Retención 2")

    @api.onchange('concepto_2')
    def _onchange_concepto_2(self):
        if self.concepto_2:
            self.base = self.concepto_2.base       # campo en modelo concepto
            self.retencion2 = self.concepto_2.retencion2   # campo en modelo concepto
        else:
            self.base = False
            self.retencion2 = False

    dedicacion_comibinados = fields.Float(string='% Dedicación', compute="compute_repartir_dedicacion", store=True)

    # Método para repartir dedicación en las líneas
    @api.depends('concepto_2', 'municipios_combinados_id2')
    def compute_repartir_dedicacion(self):
        for record in self:
            lineas = record.municipios_combinados_id2
            total_lineas = len(lineas)
            if total_lineas > 0:
                porcentaje = round(100 / total_lineas, 2)
                for linea in lineas:
                    linea.dedicacion_comibinados = porcentaje


    municipios_combinados_id2 = fields.One2many('municipios_combinados', 'proceso_id_anexo',
                                                string="Municipio combinado")

    municipios3 = fields.Many2one(related="municipios_combinados_id2.municipios3", string="MUNICIPIO")

    
    #Nuevo campo
    def abrir_municipios_nuevos(self):
        """Abre la vista tree de municipios_nuevos permitiendo la creación de nuevos registros."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Municipios Nuevos',
            'res_model': 'municipios_nuevos',
            'view_mode': 'tree,form',  # Habilita la vista de formulario para crear registros
            'target': 'current',
            'context': {'create': True}  # Permite la creación de nuevos registros
        }

    def borrar_registros_relacionados(self):
        for record in self:
            self.env['municipios_combinados'].search([
                ('proceso_id_anexo', '=', record.id)
            ]).unlink()






    @api.onchange("concepto_2")
    def compute_retencion2_visible(self):
        """Solo cálculos y validaciones aquí"""
        for record in self:
            record.retencion2_visible = record.retencion2 == 0

    def write(self, vals):
        """Override write para capturar cambios en concepto_2"""
        result = super().write(vals)

        # Si se modificó concepto_2, procesar municipios
        if 'concepto_2' in vals:
            print("Se ejecutó transferir_municipios_seleccionados en write")
            self.transferir_municipios_seleccionados()

        return result

    # Variables de departamentos y municipios (ICA)
    # Mostrar departamentos y municipios seleccionados en las órdenes de compra de bienes asociadas
    departamento_municipio_ids = fields.One2many(
        'departamento_y_municipio',
        compute='_compute_departamento_municipio_ids',
        string='Departamentos y Municipios',
        store=False
    )

    @api.depends('orden_compra_bienes_id.departamento_municipio_ids',
                 'orden_compra_servicios_id.departamento_municipio_ids')
    def _compute_departamento_municipio_ids(self):
        for record in self:
            res = self.env['departamento_y_municipio']  # recordset vacío del modelo
            if record.orden_compra_bienes_id:
                res |= record.orden_compra_bienes_id.mapped('departamento_municipio_ids')
            if record.orden_compra_servicios_id:
                res |= record.orden_compra_servicios_id.mapped('departamento_municipio_ids')
            record.departamento_municipio_ids = res

    def transferir_municipios_seleccionados(self):
        """Ahora SÍ funciona porque se ejecuta en write()"""
        for record in self:
            # Eliminar registros existentes
            self.env['municipios_combinados'].search([
                ('proceso_id_anexo', '=', record.id)
            ]).unlink()

            # Insertar nuevamente los municipios seleccionados
            for municipio_rel in record.departamento_municipio_ids:
                # Asegurarse que municipios2 es un recordset de municipios
                for municipio in municipio_rel.municipios2:
                    if not municipio:
                        continue

                    print(f"🔵 Municipio detectado: {municipio.name}")

                    municipio_nuevo = self.env['municipios_nuevos'].search([
                        ('name', '=', municipio.name)
                    ], limit=1)

                    if municipio_nuevo:
                        tarifa_record = self.env['municipios_nuevos'].search([
                            ('name', '=', municipio.name),
                            ('conceptos_bd3', '=', record.concepto_2.name)
                        ], limit=1)

                        tarifa = tarifa_record.tarifa_bd3 if tarifa_record else "Tarifa no definida"
                        b_servicio = tarifa_record.base_servicio if tarifa_record else ""
                        b_bienes = tarifa_record.base_bienes if tarifa_record else ""

                        self.env['municipios_combinados'].create({
                            'proceso_id_anexo': record.id,
                            'municipios3': municipio_nuevo.id,
                            'conceptos_bd': record.concepto_2.name,
                            'base_servicio': b_servicio,  # <--- NUEVO CAMPO
                            'base_bienes': b_bienes,      # <--- NUEVO CAMPO
                            'tarifa_bd': tarifa
                        })

                        print(
                            f"✅ Municipio creado: {municipio.name}, Concepto: {record.concepto_2.name}, Tarifa: {tarifa}")
                    else:
                        print(f"❌ Municipio no encontrado en municipios_nuevos: {municipio.name}")

        return True

    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################


    proceso_gestion_id = fields.Many2one('proceso_gestion', string="Proceso de Gestión")
    orden_compra_servicios_ids = fields.One2many('orden_compra_servicios', 'anexo_id', string="Órdenes de Servicios")

    # Variables extraídas del XML de Odoo - Proceso de Gestión Tributaria
    datos_adicionales = fields.Text(string="Datos Adicionales")
    de = fields.Char(string="De")
    conceptos_bd = fields.Char(string="Conceptos BD")
    # Variable de nota
    nota = fields.Text(string="Nota")
    # Variables de elaboración
    eleborado_por = fields.Char(string='Elaborado por', default=lambda self: self.env.user.name, readonly=True)
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())

    # Variables del modelo tarifa2
    tarifa2_name = fields.Char(string="Nombre Tarifa2")

    # Variables del sistema Odoo (campos automáticos)
    message_follower_ids = fields.One2many('mail.followers', 'res_id', string="Seguidores", domain=[('res_model', '=', 'anexo')])
    activity_ids = fields.One2many('mail.activity', 'res_id', string="Actividades", domain=[('res_model', '=', 'anexo')])
    message_ids = fields.One2many('mail.message', 'res_id', string="Mensajes", domain=[('model', '=', 'anexo')])

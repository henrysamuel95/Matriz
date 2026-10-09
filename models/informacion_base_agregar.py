#!/usr/bin/python

from odoo import models, fields, api
from odoo.exceptions import UserError
from .drive import GoogleDriveAuth
import threading
import logging

_logger = logging.getLogger(__name__)



class SecopOption(models.Model):
    _name = 'secop_option'
    _description = 'Opciones de SECOP'
    rec_name = "apodo"
    name = fields.Char(string='Nombre')


class InformacionBase(models.Model):
    _name = 'informacion_base_agregar'
    _description = 'Información base'
    _rec_name = "apodo"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    
    area_solicitante2  = fields.Selection(
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
        tracking=True)

    bpin = fields.Char(string='BPIN', tracking=True)
        
    secop_option_id = fields.Many2one(
        'secop_option',
        string='Opción SECOP',
        help="Selecciona una opción de SECOP o crea una nueva."
    )

    drive_folder_id = fields.Many2one('drive_folder', string='Carpeta de Google Drive', related='contratacion_id.drive_folder_id')
    nombre_proceso_proyecto = fields.Char(string='Nombre del proceso/proyecto', tracking=True)

    def write(self, vals):
        if 'area_solicitante2' in vals:
            for record in self:
                if record.area_solicitante2 == vals['area_solicitante2']:
                    continue
                proyectos = record.contratacion_id | record.informacion_base_agregar_ids
                if not proyectos:
                    continue
                modelo = 'contrataciones_fuv' if vals['area_solicitante2'] == 'regalias' else 'contrataciones_sgr'
                if self.env[modelo].search_count([('proyecto_relacionado', 'in', proyectos.ids)]):
                    raise UserError(
                        'No se puede cambiar la dependencia: el proyecto ya tiene contrataciones '
                        'del flujo actual.'
                    )

        # 1. Primero guardamos los cambios en Odoo
        res = super(InformacionBase, self).write(vals)

        # 2. Si el nombre del proceso cambió, sincronizamos con Google
        if 'nombre_proceso_proyecto' in vals:
            nuevo_nombre = vals.get('nombre_proceso_proyecto')
            
            for record in self:
                # Accedemos a la carpeta a través de la relación related
                carpeta = record.drive_folder_id
                
                # Verificamos que exista la carpeta y que tenga el ID de Google correcto
                if carpeta and carpeta.folder_guardar_id:
                    try:
                        _logger.info(f"Sincronizando Google Drive. ID: {carpeta.folder_guardar_id}")
                        
                        # --- LÓGICA PYDRIVE ---
                        drive_auth = GoogleDriveAuth.get_instance()
                        drive = drive_auth.get_drive()
                        
                        # Instanciamos la carpeta en Google usando folder_guardar_id
                        folder_remote = drive.CreateFile({'id': carpeta.folder_guardar_id})
                        
                        # Cargamos metadatos para asegurar la conexión
                        folder_remote.FetchMetadata()
                        
                        # Cambiamos el nombre (en PyDrive es 'title')
                        folder_remote['title'] = nuevo_nombre
                        
                        # Subimos el cambio a Google Drive
                        folder_remote.Upload()
                        
                        # 3. Actualizamos el nombre local en el modelo drive_folder
                        # Tu modelo usa 'folder_name' para el nombre técnico
                        carpeta.write({'folder_name': nuevo_nombre})
                        
                        _logger.info(f"SINCRO EXITOSA: Carpeta renombrada en Google Drive a: {nuevo_nombre}")

                    except Exception as e:
                        _logger.error(f"ERROR GOOGLE DRIVE: No se pudo renombrar. Motivo: {str(e)}")
                else:
                    _logger.warning(f"SINCRO FALLIDA: El registro drive_folder ID {carpeta.id if carpeta else 'N/A'} no tiene folder_guardar_id.")
        
        return res
    
    apodo = fields.Char(default="Editar información del proyecto")

    contratacion_id = fields.Many2one('contratacion', string='Contratación',)

    users_with_access = fields.Many2many('res.users',
                                         string='Permitir acceso de usuarios al proyecto',
                                         related='contratacion_id.users_with_access')
    avances_ids = fields.One2many('tablero_avances', 'informacion_base_agregar_id2', string='Tablero avances')


    informacion_base_agregar_ids = fields.One2many('contratacion', 'informacion_base_id',
                                                   string='Información Base')

    Titulo_proyecto = fields.Char()

    

    new_name = fields.Char(string='New Name')
    name = fields.Char(string='Name', related='apodo')

    informacion_base_id = fields.Many2one('informacion_base_agregar', string='Información base')

    is_editable = fields.Boolean(string='Es editable', default=False, related="informacion_base_agregar_ids.is_editable")

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass

    modalidad_contratacion = fields.Selection([
        ('CI', 'Carta de Instrucción (CI)'),
        ('CC', 'Contrato de Consultoria (CC)'),
        ('CO', 'Contrato de Obra (CO)'),
        ('CIA', 'Contrato Interadministrativo (CIA)'),
        ('CLP', 'Contrato de Licitaciones Publicas (CLP)'),
        ('CPS', 'Contrato de Prestacion de Servicios (CPS)'),
        ('OC', 'Orden de Compra (OC)'),
        ('OS', 'Orden de Servicios (OS)'),
        ('CS', 'Contrato de Suministro (CS)'),
        ('CA', 'Convenio de Asociacion (CA)'),
        ('CCF', 'Convenio de Cofinanciacion (CCF)'),
        ('CEC', 'Convenio Especial de Cooperacion (CEC)'),
        ('CIA', 'Convenio Interadministrativo (CIA)'),
    ], string='Modalidad de contratación')

    modalidad_id = fields.Many2one(
        'modalidad_contratacion',
        string='Modalidad de contratación',
        tracking=True
    )

    tag_municipios_ids = fields.Many2many('municipio', 'municipio_ejecucion', string='Municipio beneficiado')
    tag_municipios2_ids = fields.Many2many('municipio', 'municipio_ejecucion2', string='Municipio de Ejecución')

    objeto_proyecto = fields.Char(string='Objeto del proyecto', tracking=True)
    # Nuevo campo tipo Text para soportar saltos de línea automáticos
    objeto_proyecto_texto = fields.Text(
        string='Objeto del proyecto', 
        compute='_compute_objeto_proyecto_texto', 
        inverse='_inverse_objeto_proyecto_texto',
        store=True
    )

    @api.depends('objeto_proyecto')
    def _compute_objeto_proyecto_texto(self):
        for record in self:
            record.objeto_proyecto_texto = record.objeto_proyecto

    def _inverse_objeto_proyecto_texto(self):
        for record in self:
            record.objeto_proyecto = record.objeto_proyecto_texto
            
        
        
    primera_entidad_relacionada = fields.Char(string='Primera entidad relacionada', tracking=True)
    nit_primera_entidad = fields.Char(string='NIT, primera entidad', tracking=True)

    segunda_entidad_relacionada = fields.Char(string='Segunda entidad relacionada', tracking=True)
    nit_segunda_entidad = fields.Char(string='NIT, segunda entidad', tracking=True)

    tercera_entidad_relacionada = fields.Char(string='Tercera entidad relacionada', tracking=True)
    nit_tercera_entidad = fields.Char(string='NIT, tercera entidad', tracking=True)
    eje_trabajo = fields.Selection([
        ('cambio_climatico', 'Cambio climático'),
        ('bienes_publicos', 'Generación de bienes públicos'),
        ('desarrollo_social', 'Desarrollo social'),
        ('desarrollo_economico', 'Desarrollo económico'),
        ('ciencia_tecnologia_innovacion', 'Ciencia tecnología e innovación CTI'),
    ], string='Eje de trabajo', tracking=True)

    descripcion_beneficiarios = fields.Char(string='Descripción de beneficiarios', tracking=True)
    total_beneficiarios = fields.Integer(string='Total de beneficiarios', tracking=True)

    municipio_ejecucion2 = fields.Many2one(comodel_name='municipio', string='Municipio de ejecución', tracking=True)
    aporte = fields.Float(string='Aporte', tracking=True)

    def mi_funcion_del_boton(self):
        # Código que deseas ejecutar cuando se presione el botón
        # Por ejemplo, puedes hacer algún cálculo o acción aquí
        pass

    otro_municipio_beneficiado2 = fields.Many2one(comodel_name='municipio', string='Municipio beneficiado', tracking=True)
    aporte_otro_municipio = fields.Float(string='Aporte', tracking=True)
    asignar_coordinador = fields.Many2one(
        'res.users',
        string='Asignar coordinador responsable',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)],
        tracking=True,
    )
    id_proyecto = fields.Char(string='Unidad de negocio (Centro de costos)', tracking=True)
    numero_contrato = fields.Char(string='Número de contrato', tracking=True)
    fecha_acta_inicio = fields.Date(string='Fecha del acta de inicio', tracking=True, autocomplete="off")
    fecha_acta_fin = fields.Date(string='Fecha fin', tracking=True, autocomplete="off")
    valor_total_neto = fields.Float(string='Valor total neto', readonly="1", related='mcb_ids.valor_total2_final')
    valor_sin_contrapartida = fields.Float(string='Valor sin contrapartida', readonly="1",
                                           related='mcb_ids.new_mcb_valor_total2_final')

    new_mcb_valor_total2_final = fields.Float(string='Valor sin contrapartida', readonly="1",
                                           related='mcb_ids.new_mcb_valor_total2_final')

    currency_id = fields.Many2one('res.currency', string='Moneda')
    valor_contrapartida_fuv = fields.Monetary(string='Valor contrapartida FUV', readonly="1",
                                              related='mcb_ids.contrapartida')
    costos_tributarios = fields.Monetary(string='Costos tributarios', readonly="1",
                                         related='mcb_ids.suma_costos_tributarios_total_valor')
    valor_poliza = fields.Monetary(string='Valor póliza', readonly="1", related='mcb_ids.suma_polizas')
    valor_administracion_costo_operativo = fields.Monetary(string='Valor de administración y costo operativo',
                                                        readonly="1", related='mcb_ids.suma_Admon_12')
    valor_operacion_para_contratar = fields.Monetary(string='Valor de operación para contratar', readonly="1",
                                                  related='mcb_ids.suma_total_costos_produccion_N')
    secop = fields.Boolean(string='SECOP II')

    actividad3_ids = fields.One2many('vistas_director', 'ejecucion3_id', string='Tablero control')
    cantidad_contrataciones = fields.Integer(compute='_compute_cantidad_contrataciones',
                                             string="Cantidad total de contrataciones")
    mcb_ids = fields.One2many('mcb', 'informacion_base_agregar_id', string='MCB')


    @api.depends('cantidad_contrataciones')
    def _compute_cantidad_contrataciones(self):
        contratacion_obj = self.env['contratacion']
        cantidad_registros = contratacion_obj.search_count([])
        print("Cantidad de registros en 'contratacion':", cantidad_registros)
        self.cantidad_contrataciones = cantidad_registros

    def action_ingresar_datos(self):
        self.ensure_one()

        if not self.nombre_proceso_proyecto:
            raise UserError("Debe establecer el nombre del proceso/proyecto antes de continuar.")

        if not self.env['vistas_director'].search([('name', '=', 'Global')], limit=1):
            self.env['vistas_director'].create({'name': 'Global'})

        self.new_name = self.nombre_proceso_proyecto
        self._compute_cantidad_contrataciones()

        new_contratacion = self.env['contratacion'].create({
            'new_name': self.nombre_proceso_proyecto,
            'informacion_base_id': self.id,
        })
        self.contratacion_id = new_contratacion.id
        if hasattr(new_contratacion, 'Guardar'):
            new_contratacion.Guardar()

        new_mcb = self.env['mcb'].create({
            'new_name': self.nombre_proceso_proyecto,
            'informacion_base_agregar_id': self.id,
            'contrataciones_id': new_contratacion.id,
        })
        if hasattr(new_mcb, 'Guardar'):
            new_mcb.Guardar()

        new_folder = self.env['drive_folder'].create({
            'folder_name': self.nombre_proceso_proyecto,
            'proyecto_relacionado': new_contratacion.id,
        })
        folder_id = new_folder.create_drive_carpeta()
        if folder_id:
            _logger.info(f"Carpeta creada en Google Drive con ID: {folder_id}")
            self.env.cr.commit()
            thread = threading.Thread(
                target=self.with_context(thread_safe=True).create_subcarpetas_background,
                args=(new_folder.id, new_contratacion.id)
            )
            thread.start()
        else:
            _logger.error("Error al crear la carpeta en Google Drive")

        # Crear tablero antes de ejecución
        # nuevo_tablero = self.env['tablero_avances'].create({
        #     'nombre_proyecto_relacionado': self.nombre_proceso_proyecto,
        #     'informacion_base_agregar_id2': self.id,                         No activar versión pasada
        # })

        # Ahora sí crear ejecución
        try:
            nueva_objetivo = self.env['ejecucion'].create({
                'new_name': 'ACTIVIDADES',
                'name': self.nombre_proceso_proyecto,
                'informacion_base_id': self.id,
                'proyecto_relacionado2': new_contratacion.id,
                'proyecto_relacionado': new_contratacion.id,
                # 'tablero_id': nuevo_tablero.id,                               No activar versión pasada
            })
            print(nueva_objetivo)

            # Relacionar ejecución con tablero
            #nuevo_tablero.write({'ejecucion_J_id': nueva_objetivo.id})       No activar versión pasada

        except Exception as e:
            _logger.exception("Error creando objetivo y tablero: %s", e)

        new_ficharesumen = self.env['ficharesumen'].create({
            'new_name': self.nombre_proceso_proyecto,
            'informacion_base_id': self.id,
            'contratacio2_id': new_contratacion.id,
        })
        if hasattr(new_ficharesumen, 'Guardar'):
            new_ficharesumen.Guardar()

        self.env['avances_proyectos'].create({
            'new_name': self.nombre_proceso_proyecto,
            'informacion_base_agregar_id': self.id,
            'contrataciones_id': new_contratacion.id,
            'nombre_proyecto': new_contratacion.id,
        })

        reporte_proyectos_obj = self.env['reporte_proyectos']
        proyectos_contratacion = self.env['contratacion'].search([])
        for proyecto in proyectos_contratacion:
            if not reporte_proyectos_obj.search([('proyecto_relacionado4', '=', proyecto.id)], limit=1):
                reporte_proyectos_obj.create({
                    'proyecto_relacionado4': proyecto.id,
                    'descripcion': f'Reporte para el proyecto: {proyecto.new_name or proyecto.name}',
                })
            else:
                _logger.info(f"Ya existe reporte para proyecto: {proyecto.new_name or proyecto.name}")

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def create_subcarpetas_background(self, drive_folder_id, proyecto_id):
        new_cr = self.pool.cursor()
        env = api.Environment(new_cr, self.env.uid, self.env.context)

        try:
            _logger.info("Iniciando creación de subcarpetas en segundo plano")

            drive_folder = env['drive_folder'].browse(drive_folder_id)
            if not drive_folder.exists():
                _logger.error(f"No se encontró la carpeta principal con ID {drive_folder_id}")
                return

            # Mapeo nombre numerado → nombres legacy
            _SUBCARPETAS_MAP = [
                ("1. CARPETA PRINCIPAL", ["CARPETA PRINCIPAL"]),
                ("2. COMUNICACIONES",    ["COMUNICACIONES"]),
                ("3. CONTRATOS PS",      ["CONTRATOS PS"]),
                ("4. INFORMES",          ["INFORMES"]),
            ]
            # ACTAS ya no se crea

            drive_stage_obj = env['drive_stage']

            # Renombrar carpetas legacy que ya existen
            for nuevo, viejos in _SUBCARPETAS_MAP:
                for viejo in viejos:
                    legacy = drive_stage_obj.search([
                        ('folder_name', '=', viejo),
                        ('proyecto_relacionado', '=', proyecto_id),
                        ('drive_id', '=', drive_folder.id),
                        ('folder_guardar_id', '=', drive_folder.folder_guardar_id)
                    ], limit=1)
                    if legacy:
                        legacy.write({'folder_name': nuevo})

            subcarpetas = [n for n, _ in _SUBCARPETAS_MAP]
            for subcarpeta in subcarpetas:
                try:
                    existing_subcarpeta = drive_stage_obj.search([
                        ('folder_name', '=', subcarpeta),
                        ('proyecto_relacionado', '=', proyecto_id),
                        ('drive_id', '=', drive_folder.id),
                        ('folder_guardar_id', '=', drive_folder.folder_guardar_id)
                    ], limit=1)

                    if not existing_subcarpeta:
                        new_subcarpeta = drive_stage_obj.create({
                            'folder_name': subcarpeta,
                            'proyecto_relacionado': proyecto_id,
                            'drive_id': drive_folder.id,
                            'folder_guardar_id': drive_folder.folder_guardar_id,
                        })

                        new_subcarpeta.with_context(timeout=30).create_drive_folder()
                        folder_gd_id = new_subcarpeta.folder_id  # escrito por create_drive_folder
                        if folder_gd_id:
                            _logger.info(
                                f"Subcarpeta '{subcarpeta}' creada en Google Drive con ID: {folder_gd_id}")
                            new_subcarpeta.write({'google_drive_id': folder_gd_id})
                        else:
                            _logger.warning(f"No se pudo crear la subcarpeta '{subcarpeta}' en Google Drive")
                    else:
                        _logger.info(f"La subcarpeta '{subcarpeta}' ya existe en Google Drive")
                except Exception as e:
                    _logger.error(f"Error al crear la subcarpeta '{subcarpeta}': {str(e)}")
                    new_cr.rollback()
                else:
                    new_cr.commit()

        except Exception as e:
            _logger.error(f"Error en el proceso de creación de subcarpetas: {str(e)}")
            new_cr.rollback()
        finally:
            new_cr.close()


    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')

    def action_discard(self):
        # Lógica para descartar los cambios
        return True

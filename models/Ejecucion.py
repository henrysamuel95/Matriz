# models.py
import locale
from datetime import datetime, timedelta, date

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime
from markupsafe import escape as html_escape
import xlwt
from odoo.exceptions import UserError
from io import BytesIO
import logging
from collections import defaultdict
_logger = logging.getLogger(__name__)

class Ejecucion(models.Model):
    _name = 'ejecucion'
    _description = 'actividades'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string='Nombre', tracking=True)
    new_name = fields.Char(string='Nombre', tracking=True)
    tablero_id = fields.Many2one('tablero_avances', ondelete='cascade')

    def _es_usuario_consulta(self):
        return self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')
    
    ##Teylor##
    def create_informes_record(self):
            self.ensure_one()
            # Actualiza la fecha de modificación
            if not self._es_usuario_consulta():
                self.write({'write_date': fields.Datetime.now()})

            # --- CORRECCIÓN ---
            # Usamos el ID del proyecto relacionado (proyecto_relacionado2), NO el ID de esta ejecución (self.id).
            project_id = self.proyecto_relacionado2.id
            
            if not project_id:
                raise UserError("Esta ejecución no está asociada a ningún proyecto.")
            # ------------------

            # Busca si ya existe un informe relacionado con el PROYECTO (usando project_id)
            ejecucion_existente = self.env['informes_drive'].search([
                ('proyecto_relacionado', '=', project_id),
            ], limit=1)

            if ejecucion_existente:
                ejecucion_existente._ensure_root_workspace()
                return {
                    'name': '4. Cierre',
                    'view_mode': 'form',
                    'res_model': 'informes_drive',
                    'res_id': ejecucion_existente.id,
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                }
            elif self._es_usuario_consulta():
                raise UserError("Aun no existe un registro de informes para este proyecto.")
            else:
                # Si no existe, crear uno nuevo usando el ID del proyecto correcto
                nueva_ejecucion = self.env['informes_drive'].create({
                    'name': 'Informes',
                    'proyecto_relacionado': project_id, # <--- Aquí estaba el error, ahora usa el ID real del proyecto
                })
                nueva_ejecucion._ensure_root_workspace()

                return {
                    'name': 'Informes',
                    'view_mode': 'form',
                    'res_model': 'informes_drive',
                    'res_id': nueva_ejecucion.id,
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                }
            
    def create_comunicaciones_record(self):
            self.ensure_one()
            # Actualiza la fecha de modificación
            if not self._es_usuario_consulta():
                self.write({'write_date': fields.Datetime.now()})

            # --- CORRECCIÓN ---
            # Usamos el ID del proyecto relacionado (proyecto_relacionado2), NO el ID de esta ejecución (self.id).
            project_id = self.proyecto_relacionado2.id
            
            if not project_id:
                raise UserError("Esta ejecución no está asociada a ningún proyecto.")
            # ------------------

            # Busca si ya existe un registro de comunicaciones relacionado con el PROYECTO
            ejecucion_existente = self.env['comunicaciones_drive'].search([
                ('proyecto_relacionado', '=', project_id), # Corregido
            ], limit=1)

            if ejecucion_existente:
                ejecucion_existente._ensure_root_workspace()
                return {
                    'name': 'Comunicaciones',
                    'view_mode': 'form',
                    'res_model': 'comunicaciones_drive',
                    'res_id': ejecucion_existente.id,
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                }
            elif self._es_usuario_consulta():
                raise UserError("Aun no existe un registro de comunicaciones para este proyecto.")
            else:
                # Si no existe, crear uno nuevo vinculado al PROYECTO
                nueva_ejecucion = self.env['comunicaciones_drive'].create({
                    'name': 'Comunicaciones',
                    'proyecto_relacionado': project_id, # Corregido
                })
                nueva_ejecucion._ensure_root_workspace()

                return {
                    'name': 'Comunicaciones',
                    'view_mode': 'form',
                    'res_model': 'comunicaciones_drive',
                    'res_id': nueva_ejecucion.id,
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                }

    def relacionar_tablero_con_ejecucion(self):
        for record in self:
            tablero = self.env['tablero_avances'].search([
                ('name', '=', record.name)
            ], limit=1)

            if tablero:
                if record.tablero_id != tablero:
                    record.write({'tablero_id': tablero.id})
                if tablero.ejecucion_J_id != record:
                    tablero.write({'ejecucion_J_id': record.id})
            else:
                _logger.warning("No se encontró tablero para: %s", record.name)

    # def unlink(self):
    #     for rec in self:
    #         rec.action_borrar_informaciones_base()  # Llama la función que ya elimina la info base
    #     return super(Ejecucion, self).unlink()

    def action_borrar_informaciones_base(self):
        nombres = []
        for rec in self:
            info = rec.informacion_base_id
            if info:
                nombres.append(info.display_name)  # usa el nombre visible del registro
                rec.informacion_base_id = False
                info.unlink()

        if not nombres:
            raise UserError("No hay registros en 'Información Base' para borrar.")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Registros eliminados",
                'message': f"Se eliminaron: {', '.join(nombres)}",
                'type': 'success',
                'sticky': False,
            }
        }

    informacion_base_id = fields.Many2one('informacion_base_agregar', string='Información Base', ondelete='cascade')
    entregable_ids = fields.One2many('entregable', 'ejecucion_en_id', string="actividad")
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado', ondelete='cascade')
    proyecto_relacionado2 = fields.Many2one('contratacion', string='Proyecto relacionado', ondelete='cascade')
    ###Samuel
    tabla_director_ids = fields.One2many('tablero_avances', 'ejecucion_J_id')

    # 28-11-2024 SAMUEL
    ############################################SAMUEL##################################################################
    is_editable = fields.Boolean(string='Es editable', related="proyecto_relacionado2.is_editable", default=False)

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################


    ##################09-04-2021########################################################################################
    total_final = fields.Float(related="entregable_ids.total_final")
    actividades_pendiente_count = fields.Integer(related="entregable_ids.actividades_pendiente_count")
    actividades_completas_count = fields.Integer(related="entregable_ids.actividades_completas_count")
    actividades_en_progreso_count = fields.Integer(related="entregable_ids.actividades_en_progreso_count")
    actividades_retrasadas_count = fields.Integer(related="entregable_ids.actividades_retrasadas_count")
    ##################09-04-2021########################################################################################
    resultado_tecnica_representada_dinero = fields.Monetary(
        related='tabla_director_ids.resultado_tecnica_representada_dinero')


    faltante_por_cobrar = fields.Monetary(related="id_proyecto.faltante_por_cobrar")
    cantidad_lineas_tabla_pagos = fields.Integer(related="id_proyecto.cantidad_lineas_tabla_pagos")

    #####################################################################
    # Pagos
    def action_ingresar_pagos_proyecto(self):
        self.ensure_one()
        proyecto_relacionado = self.proyecto_relacionado or self.proyecto_relacionado2
        domain = [('ejecucion2_id', '=', self.id)]
        if proyecto_relacionado:
            domain = [
                '|',
                ('ejecucion2_id', '=', self.id),
                ('proyecto_relacionado', '=', proyecto_relacionado.id),
            ]
        # Buscar el componente existente basado en la ejecución
        componente_existente = self.env['action_ingresar_pagos_proyecto'].search(domain, limit=1)

        # Si ya existe el componente, actualizarlo
        if componente_existente:
            if not self._es_usuario_consulta():
                componente_existente.write({
                    'informacion_base_id': self.informacion_base_id.id,
                    'proyecto_relacionado': proyecto_relacionado.id if proyecto_relacionado else False,
                    # Asignar directamente el proyecto relacionado del mismo modelo
                })
                componente_existente.write({'new_name': 'Cobros del proyecto'})

            # Devolver la acción para mostrar el formulario de la ejecución existente
            return {
                'name': 'Cobros del proyecto',
                'view_mode': 'form',
                'res_model': 'action_ingresar_pagos_proyecto',
                'res_id': componente_existente.id,  # Abrir el formulario del registro existente
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        elif self._es_usuario_consulta():
            raise UserError("Aun no existe un registro de cobros para este proyecto.")
        else:
            # Si no existe, crear una nueva ejecución relacionada con el proyecto actual
            nueva_componente = self.env['action_ingresar_pagos_proyecto'].create({
                'new_name': 'Cobros del proyecto',
                'ejecucion2_id': self.id,
                'informacion_base_id': self.informacion_base_id.id,
                'proyecto_relacionado': proyecto_relacionado.id if proyecto_relacionado else False,
                # Asignar directamente el proyecto relacionado del mismo modelo
            })

            # Devolver la acción para mostrar el formulario de la nueva ejecución creada
            return {
                'name': 'Cobros del proyecto',
                'view_mode': 'form',
                'res_model': 'action_ingresar_pagos_proyecto',
                'res_id': nueva_componente.id,  # Abrir el formulario del nuevo registro creado
                'type': 'ir.actions.act_window',
                'target': 'current',
            }

    # En el modelo 'ejecucion' esta es la relación correcta entre action_ingresar_pagos_proyecto y ejecucion
    pagos_ids = fields.One2many('action_ingresar_pagos_proyecto', 'ejecucion2_id', string='Pagos')
    valor_cobrado = fields.Monetary(string='Valor Cobrado', related='pagos_ids.acomulado_cobro_sumado')
    valor_por_facturar = fields.Monetary(string='Valor por facturar', currency_field='currency_id',
                                         related='pagos_ids.valor_por_facturar')

    #  tiene una relación que envía datos al modelo "contratacion" y luego al modelo "reporte_proyectos" no eliminar
    id_proyecto = fields.Many2one('action_ingresar_pagos_proyecto', string='Proyecto', ondelete='cascade')

    currency_id = fields.Many2one('res.currency', string='Moneda')
    otros_accion = fields.Many2one(comodel_name='rubros', string="ACCION", tracking=True,
                                   related="entregable_ids.otros_accion")
    verificador = fields.Char(string='VERIFICADOR', tracking=True, related="entregable_ids.verificador")
    unidad_medida = fields.Char(string='UNIDAD DE MEDIDA', tracking=True,
                                related="entregable_ids.unidad_medida")

    porcentaje = fields.Float(string='PESO PONDERADO', tracking=True, related="entregable_ids.porcentaje")
    duracion_periodos = fields.Float(string='DURACIÓN (PERÍODOS)', tracking=True,
                                     related="entregable_ids.duracion_periodos")
    fecha_inicio = fields.Date(string='INICIO', tracking=True, related="entregable_ids.fecha_inicio")
    fecha_fin = fields.Date(string='FINAL', tracking=True, related="entregable_ids.fecha_fin")

    estado = fields.Selection([
        ('pendiente', 'NO INICIADO'),
        ('en_progreso', 'EJECUTANDO'),
        ('en_atraso', 'ATRASADO'),
        ('completado', 'FINALIZADO'),
    ], string='ESTADO', tracking=True, readonly=False, default='pendiente', related="entregable_ids.estado")

    @api.depends('new_name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.new_name or record.name

    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)

    @api.depends('ejecutado1')
    def _compute_ejecutado1(self):
        for ejecucion in self:
            # Actualizar automáticamente el campo correspondiente en el modelo TableroAvances
            ejecucion.tabla_director_ids.ejecutado1 = ejecucion.ejecutado1

    campos_ocultos = fields.Boolean(string='Campos ocultos')
    actividad_ids = fields.One2many('actividad', 'ejecucion_id', string="Componentes")

    suma_total = fields.Float(string='Suma Total', related="ejecucion_ids.suma_total")

    ejecucion_ids = fields.One2many('praneacion', 'ejecucion_en_id', string="Planeación Presupuestal")

    proyecto_id = fields.Many2one('subactividad', string='Sub actividad', ondelete='cascade')
    # Campo Many2one para la relación con VistasDirector
    vistas_director3_id = fields.Many2one('vistas_director', string='Vistas Director', required=False,
                                          default=lambda self: self.env['vistas_director'].search([], limit=1))

    valor_ejecutado_total = fields.Float(string='Valor Ejecutado Total', compute='_compute_valor_ejecutado_total')
    fch_creacion = fields.Datetime(string='Fecha creación', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())

    # mini tabla
    presupuesto = fields.Float(string="Presupuesto", related='actividad_ids.total_general1_global',
                               tracking=True)

    ejecutado1 = fields.Float(string="Ejecutado", related='actividad_ids.total_general2_global', store=True,
                              tracking=True)
    disponible = fields.Float(string="Disponible", related='actividad_ids.total_general3_global',
                              tracking=True)
    resultado_division = fields.Float(string="Disponible", related='actividad_ids.resultado_division',
                                      tracking=True)
    #####################################################################
    item = fields.Char(string='Item', tracking=True, related="actividad_ids.item")

    #variables tracking
    # cantidad = fields.Integer(string='CANTIDAD', tracking=True, related="entregable_ids.cantidad")
    cantidad = fields.Integer(string='Cantidad', tracking=True, related="actividad_ids.cantidad")
    nombre = fields.Char(string='# ACCION', related="entregable_ids.name")
    #nombre = fields.Char(string='Nombre', related="actividad_ids.name")

    @api.depends('actividad_ids.rubro')
    def _compute_rubro(self):
        for ejecucion in self:
            # Obtener una lista de rubros de todas las actividades asociadas
            rubros = [actividad.rubro for actividad in ejecucion.actividad_ids if actividad.rubro]

            # Concatenar los rubros en una cadena separada por comas
            ejecucion.rubro = ', '.join(rubros)

    rubro = fields.Char(string='Rubro', compute='_compute_rubro', store=True)
    # rubro = fields.Selection([
    #     ('personalcin', 'PERSONAL CIENTÍFICO'),
    #     ('tecnico', 'SERVICIOS TÉCNICOS'),
    #     ('adquisicion', 'ADQUISICIÓN DE EQUIPOS'),
    #     ('materiales', 'MATERIALES E INSUMOS'),
    #     ('adecuacion', 'ADECUACIÓN DE INFRAESTRUCTURA'),
    #     ('software', 'SOFTWARE'),
    #     ('salida', 'SALIDAS DE CAMPO'),
    #     ('difusion', 'DIFUSIÓN DE RESULTADOS'),
    #     ('Viajes', 'VIAJES'),
    #     ('gastos', 'GASTOS DE PROPIEDAD INTELECTUAL'),
    #     ('biblo', 'BIBLIOGRAFÍA'),
    #     ('adminis', 'GASTOS DE ADMINISTRACIÓN'),
    #     ('ctei', 'DIVULGACIÓN Y APROPIACIÓN DE CTEI'),
    #     ('personal', 'PERSONAL DE APOYO'),
    #     ('capacitaciones', 'CAPACITACIONES'),
    #     ('convenio', 'CONVENIO ESPECIAL DE COOPERACIÓN TÉC'),
    #     ('certificaion', 'CERTIFICACIONES'),
    #     ('consultoria', 'CONSULTORÍA ESPECIALIZADA'),
    #     ('serviciosTec', 'SERVICIOS TÉCNOLÓGICOS'),
    # ], string='Rubro', tracking=True, related="actividad_ids.rubro")

    tipo_requerimiento = fields.Selection([
        ('servicio', 'Servicio '),
        ('bien', 'Bien'),
    ], string='Tipo Requerimiento', tracking=True, related="actividad_ids.tipo_requerimiento")

    descripcion_gasto = fields.Char(string='Descripción del Gasto', tracking=True,
                                    related="actividad_ids.descripcion_gasto")
    fecha_solicitud = fields.Date(string='Fecha de Solicitud', tracking=True,
                                  related="actividad_ids.fecha_solicitud")
    fecha_requerida = fields.Date(string='Fecha Requerida', related="actividad_ids.fecha_requerida")
    lugar_entrega = fields.Char(string='Lugar y/o Forma de Entrega', tracking=True,
                                related="actividad_ids.lugar_entrega")

    tiempo_ejecucion_dias = fields.Integer(string='Tiempo de Ejecución (Días)', tracking=True,
                                           related="actividad_ids.tiempo_ejecucion_dias")
    proveedor = fields.Char(string='Proveedor', tracking=True, related="actividad_ids.proveedor")
    nit = fields.Char(string='NIT', tracking=True, related="actividad_ids.nit")
    tipo_contractual = fields.Char(string='Tipo Contractual', tracking=True,
                                   related="actividad_ids.tipo_contractual")
    valor_estimado = fields.Float(string='Valor Estimado (Requerimiento)', tracking=True,
                                  related="actividad_ids.valor_estimado")
    valor_ejecutado = fields.Float(string='Valor Ejecutado', tracking=True,
                                   related="actividad_ids.valor_ejecutado")
    impuesto_asumido_proyecto = fields.Float(string='Impuesto Asumido por el Proyecto', tracking=True,
                                             related="actividad_ids.impuesto_asumido_proyecto")
    total = fields.Float(string='Total', compute='_compute_total', store=True, tracking=True,
                         related="actividad_ids.total")

    porcentaje_ejecucion = fields.Float(
        string="% EJECUCION",
        compute='_compute_porcentaje_ejecucion'
        , related="actividad_ids.porcentaje_ejecucion"
    )
    #####################################################################
    total_valor_ejecutado = fields.Float(
        string="Total Valor Ejecutado",
        compute='_compute_total_valor_ejecutado',
        store=False  # Puedes optar por almacenar el valor calculado si es necesario.
    )

    @api.depends('ejecutado1')
    def _compute_total_valor_ejecutado(self):
        for record in self:
            record.total_valor_ejecutado = record.ejecutado1

    total_ejecucion_global = fields.Float(
        string="Total Ejecución Global",
        compute='_compute_total_ejecucion_global',
        store=False
    )

    @api.depends('total_valor_ejecutado')
    def _compute_total_ejecucion_global(self):
        all_records = self.search([])
        total = sum(all_records.mapped('total_valor_ejecutado'))

        for record in self:
            record.total_ejecucion_global = total

    ########################################################################

    # Relación con vistas_director
    vistas_director_id = fields.Many2one('vistas_director', string='Vistas del Director',
                                         default=lambda self: self.env['vistas_director'].search([], limit=1).id)


    @api.depends('actividad_ids.line_ids.valor_ejecutado')
    def _compute_valor_ejecutado_total(self):
        for ejecucion in self:
            total_valor_ejecutado = sum(ejecucion.actividad_ids.mapped('line_ids.valor_ejecutado'))
            ejecucion.valor_ejecutado_total = total_valor_ejecutado

    valor_total = fields.Float(string='Valor Ejecutado Total', compute='_compute_valor_total')

    @api.depends('actividad_ids.line_ids.total_general')
    def _compute_valor_total(self):
        for ejecucion in self:
            valor_total = sum(ejecucion.actividad_ids.mapped('line_ids.total_general'))
            ejecucion.valor_total = valor_total

    def action_abrir_menu_agregar_actividad(self):
        # Crear una nueva actividad y vincularla a esta ejecución
        nueva_actividad = self.env['actividad'].create({'name': 'Nueva Actividad', 'ejecucion_id': self.id})

        # Abrir la vista Kanban para las actividades
        return {
            'type': 'ir.actions.act_window',
            'name': 'Actividades',
            'view_mode': 'kanban,form',
            'res_model': 'actividad',
            'domain': [('ejecucion_id', '=', self.id)],
            'context': {'default_ejecucion_id': self.id},
        }

    def action_ver_actividades(self):
        # Lógica para abrir la vista de árbol de actividades
        return {
            'name': 'Base de Datos',
            'type': 'ir.actions.act_window',
            'res_model': 'actividad_line',
            'view_mode': 'tree,graph,pivot,search,form',
            # Opcional: establecer un dominio predeterminado para filtrar las actividades
            # 'domain': [('ejecucion_id', '=', self.id)],
        }

    #####################################################################################################
    # Programa de pagos


    suma_total_costos_produccion_N2 = fields.Monetary('Valor a contratar',
                                                      related="proyecto_relacionado2.valor_a_contratar")
    programa_pagos_id = fields.One2many('programapagos', 'mcb_id3', string="Programa de Pagos relacionado")
    mcb_id4 = fields.Many2one('ejecucion', string="ejecucion relacionado", related="programa_pagos_id.mcb_id3")
    def action_ingresar_programa_pagos(self):
        self.ensure_one()
        # Busca si ya existe un programa de pagos asociado
        programa_pagos = self.programa_pagos_id

        # Determinar el valor de proyecto_relacionado en el modelo Ejecucion
        proyecto_relacionado_id = self.proyecto_relacionado.id if self.proyecto_relacionado else False

        # Si no existe, crear uno nuevo
        if not programa_pagos:
            if self._es_usuario_consulta():
                raise UserError("Aun no existe un registro de pagos para este proyecto.")

            nuevo_programa = self.env['programapagos'].create({
                'new_name': 'Pagos del proyecto',
                'proyecto_relacionado': proyecto_relacionado_id
            })
            self.programa_pagos_id = nuevo_programa

            # Abrir la ventana del programa recién creado
            return {
                'name': 'Pagos del proyecto',
                'view_mode': 'form',
                'res_model': 'programapagos',
                'type': 'ir.actions.act_window',
                'res_id': nuevo_programa.id,
                'target': 'current',
            }

        # Si existe, actualizar el campo suma_total_costos_produccion_N2
        else:
            if not self._es_usuario_consulta():
                programa_pagos.write({
                    'new_name': 'Pagos del proyecto',
                })

            # Abrir la ventana del programa existente
            return {
                'name': 'Pagos del proyecto',
                'view_mode': 'form',
                'res_model': 'programapagos',
                'type': 'ir.actions.act_window',
                'res_id': programa_pagos.id,
                'target': 'current',
            }

    id_programa = fields.Many2one('programapagos', string='Programa', ondelete='cascade')
    pago_efectuado2_sumado = fields.Float(string='Valor pagado', related='id_programa.pago_efectuado2_sumado')

    valor_pago_2suma2 = fields.Float(string='Valor contratado', related='programa_pagos_id.valor_pago_2suma')
    saldo_disponible = fields.Monetary(related='programa_pagos_id.saldo_disponible_2')


class objetivo_especifico(models.Model):
    _name = 'objetivo'
    _description = 'Objetivos específicos'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', related='proyecto_relacionado3.name', tracking=True)
    new_name = fields.Char(string='Nombre', tracking=True)

    componente_ids = fields.One2many('abrir_componente', 'objetivo_en_id', string="Objetivo específico",
                                     tracking=True)
    proyecto_relacionado3 = fields.Many2one('contratacion', string='Selecciona un Proyecto', tracking=True, ondelete='cascade')

    @api.depends('new_name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.new_name or record.name

    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)

    ###Samuel
    tabla_director_ids = fields.One2many('tablero_avances', 'ejecucion_O_id', string='Tablero de Avances')

    #####################################################################
    # Pagos
    def action_ingresar_pagos_proyecto(self):
        # Verifica si ya hay un proyecto asociado
        if not self.id_proyecto:
            if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
                raise UserError("Aun no existe un registro de cobros para este proyecto.")
            # Crea un nuevo registro para el proyecto actual
            nuevo_proyecto = self.env['action_ingresar_pagos_proyecto'].create({})
            self.id_proyecto = nuevo_proyecto.id

        # Abrir la ventana del proyecto recién establecido
        return {
            'name': 'Cobros del proyecto',
            'view_mode': 'form',
            'res_model': 'action_ingresar_pagos_proyecto',
            'type': 'ir.actions.act_window',
            'res_id': self.id_proyecto.id,  # Establecer el ID del proyecto recién establecido
            'target': 'current',
        }


    currency_id = fields.Many2one('res.currency', string='Moneda')

    id_proyecto = fields.Many2one('action_ingresar_pagos_proyecto', string='Proyecto', ondelete='cascade')
    valor_cobrado = fields.Float(string='Valor Cobrado', related='id_proyecto.valor_cobrado')

    valor_por_facturar = fields.Monetary(string='Valor por facturar', currency_field='currency_id',
                                         related='id_proyecto.valor_por_facturar')

    #####################################################################################################
    # Programa de pagos

    def action_ingresar_programa_pagos(self):
        # Verifica si ya hay un programa asociado
        if not self.id_programa:
            # Crea un nuevo programa para el proyecto actual
            nuevo_programa = self.env['programapagos'].create({})
            self.id_programa = nuevo_programa.id

        # Abrir la ventana del programa recién establecido
        return {
            'name': 'PAGOS DEL PROYECTO',
            'view_mode': 'form',
            'res_model': 'programapagos',
            'type': 'ir.actions.act_window',
            'res_id': self.id_programa.id,  # Establecer el ID del programa recién establecido
            'target': 'current',
        }

    id_programa = fields.Many2one('programapagos', string='Programa', ondelete='cascade')
    pago_efectuado2_sumado = fields.Float(string='Valor pagado', related='id_programa.pago_efectuado2_sumado')
    valor_pago_2suma = fields.Float(string='Valor contratado', related='id_programa.valor_pago_2suma')


class abrir_componentes(models.Model):
    _name = 'abrir_componente'
    _description = 'Componentes'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', tracking=True)

    objetivo_en_id = fields.Many2one('objetivo', string='Objetivo específico', tracking=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)

    def action_view_Matriz_de_componente(self):
        # Busca si ya existe una ejecución relacionada con el proyecto actual
        componente_existente = self.env['componente'].search([
            ('proyec_id', '=', self.id),
        ], limit=1)

        if componente_existente:
            # Si ya existe, devolver la acción para mostrar el formulario de la ejecución existente
            return {
                'name': 'componente',
                'view_mode': 'form',
                'res_model': 'componente',
                'res_id': componente_existente.id,  # Abrir el formulario del registro existente
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            # Si no existe, crear una nueva ejecución relacionada con el proyecto actual
            nueva_componente = self.env['componente'].create({
                'name': 'COMPONENTES',
                # Conserva el nombre actual si existe, de lo contrario usa "Actividad"
                'proyec_id': self.id,
            })

            # Devolver la acción para mostrar el formulario de la nueva ejecución creada
            return {
                'name': 'componente',
                'view_mode': 'form',
                'res_model': 'componente',
                'res_id': nueva_componente.id,  # Abrir el formulario del nuevo registro creado
                'type': 'ir.actions.act_window',
                'target': 'current',
            }

    componente_ids = fields.One2many('componente', 'proyec_id', string='componentes', tracking=True)


class componentes(models.Model):
    _name = 'componente'
    _description = 'Componentes'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', tracking=True)

    proyec_id = fields.Many2one('abrir_componente', string='COMPONENTES', tracking=True, ondelete='cascade')

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)

    actividades_ids = fields.One2many('abrir_actividades', 'componente_en_id', string="componente",
                                      tracking=True)


class Actividades(models.Model):
    _name = 'actividades'
    _description = 'Actividades'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', tracking=True)

    proyec_id = fields.Many2one('abrir_actividades', string='ACTIVIDADES', tracking=True, ondelete='cascade')

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)
    entregables3_ids = fields.One2many('abrir_entregables3', 'actividades_en_id', string="actividad",
                                       tracking=True)


class Abrir_actividades(models.Model):
    _name = 'abrir_actividades'
    _description = 'Actividades'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', tracking=True)

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)
    componente_en_id = fields.Many2one('componente', string='componente', tracking=True, ondelete='cascade')
    componente_ids = fields.One2many('actividades', 'proyec_id', string='actividades', tracking=True)

    def action_view_Matriz_de_actividades(self):
        # Busca si ya existe una ejecución relacionada con el proyecto actual
        actividades_existente = self.env['actividades'].search([
            ('proyec_id', '=', self.id),
        ], limit=1)

        if actividades_existente:
            # Si ya existe, devolver la acción para mostrar el formulario de la ejecución existente
            return {
                'name': 'actividades',
                'view_mode': 'form',
                'res_model': 'actividades',
                'res_id': actividades_existente.id,  # Abrir el formulario del registro existente
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            # Si no existe, crear una nueva ejecución relacionada con el proyecto actual
            nueva_actividades = self.env['actividades'].create({
                'name': 'ACTIVIDADES',
                # Conserva el nombre actual si existe, de lo contrario usa "Actividad"
                'proyec_id': self.id,
            })

            # Devolver la acción para mostrar el formulario de la nueva ejecución creada
            return {
                'name': 'actividades',
                'view_mode': 'form',
                'res_model': 'actividades',
                'res_id': nueva_actividades.id,  # Abrir el formulario del nuevo registro creado
                'type': 'ir.actions.act_window',
                'target': 'current',
            }


class Entregables3(models.Model):
    _name = 'entregables3'
    _description = 'entregables'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', tracking=True)

    proyec_id = fields.Many2one('abrir_entregables3', string='ENTREGABLE', ondelete='cascade')
    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)

    entregable_ids = fields.One2many('subactividad', 'ejecuciones_en_id', string="entregable")


class Subactividad(models.Model):
    _name = 'subactividad'
    _description = 'Sub actividades'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', tracking=True)

    ejecuciones_en_id = fields.Many2one('entregables3', string='Ejecución', ondelete='cascade')
    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)
    entregables_ids = fields.One2many('ejecucion', 'proyecto_id', string='subactividad')

    def Ejecución(self):
        # Busca si ya existe una ejecución relacionada con el proyecto actual
        ejecucion_existente = self.env['ejecucion'].search([
            ('proyecto_id', '=', self.id),
        ], limit=1)

        if ejecucion_existente:
            # Si ya existe, actualizar el nombre y devolver la acción para mostrar el formulario de la ejecución existente
            ejecucion_existente.write({'new_name': 'SUB ACTIVIDADES'})

            return {
                'name': 'Ejecución',
                'view_mode': 'form',
                'res_model': 'ejecucion',
                'res_id': ejecucion_existente.id,  # Abrir el formulario del registro existente
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            # Si no existe, crear una nueva ejecución relacionada con el proyecto actual
            nueva_ejecucion = self.env['ejecucion'].create({
                'name': 'SUB ACTIVIDADES',
                # Conserva el nombre actual si existe, de lo contrario usa "Actividad"
                'proyecto_id': self.id,
            })

            # Devolver la acción para mostrar el formulario de la nueva ejecución creada
            return {
                'name': 'Ejecución',
                'view_mode': 'form',
                'res_model': 'ejecucion',
                'res_id': nueva_ejecucion.id,  # Abrir el formulario del nuevo registro creado
                'type': 'ir.actions.act_window',
                'target': 'current',
            }


class abrir_entregables3(models.Model):
    _name = 'abrir_entregables3'
    _description = 'entregables'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre')

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)
    actividades_en_id = fields.Many2one('actividades', string='actividad', ondelete='cascade')
    entregables3_ids = fields.One2many('entregables3', 'proyec_id', string='entregable')

    def action_view_Matriz_de_entregables(self):
        # Busca si ya existe una ejecución relacionada con el proyecto actual
        entregables3_existente = self.env['entregables3'].search([
            ('proyec_id', '=', self.id),
        ], limit=1)

        if entregables3_existente:
            # Si ya existe, devolver la acción para mostrar el formulario de la ejecución existente
            return {
                'name': 'Entregable',
                'view_mode': 'form',
                'res_model': 'entregables3',
                'res_id': entregables3_existente.id,  # Abrir el formulario del registro existente
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            # Si no existe, crear una nueva ejecución relacionada con el proyecto actual
            nueva_entregables3 = self.env['entregables3'].create({
                'name': 'ENTREGABLES',
                # Conserva el nombre actual si existe, de lo contrario usa "Actividad"
                'proyec_id': self.id,
            })

            # Devolver la acción para mostrar el formulario de la nueva ejecución creada
            return {
                'name': 'Entregable',
                'view_mode': 'form',
                'res_model': 'entregables3',
                'res_id': nueva_entregables3.id,  # Abrir el formulario del nuevo registro creado
                'type': 'ir.actions.act_window',
                'target': 'current',
            }


class Entregable(models.Model):
    _name = 'entregable'
    _description = 'ACCIONES'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado',
                                           related="ejecucion_en_id.proyecto_relacionado")
    # 28-11-2024 SAMUEL
    ############################################SAMUEL##################################################################
    is_editable = fields.Boolean(string='Es editable', related="ejecucion_en_id.is_editable", default=False)

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################
    avances_id = fields.Many2one('tablero_avances', string='tablero avances', related="ejecucion_en_id.tablero_id")
    hiden = fields.Char(string='hiden')

    ejecucion_en_id = fields.Many2one('ejecucion', string='Ejecución', ondelete='cascade')
    proyec2_id = fields.Many2one('praneacion', string='Planificación Presupuestal', ondelete='cascade')

    # variables tracking
    nombre = fields.Char(string='# ACCION', related="entregable2_ids.name")

    otros_accion = fields.Many2one(comodel_name='rubros', string="ACCION", tracking=True,
                                   related="entregable2_ids.otros_accion")
    verificador = fields.Char(string='VERIFICADOR', tracking=True, related="entregable2_ids.verificador")
    unidad_medida = fields.Char(string='UNIDAD DE MEDIDA', tracking=True,
                                related="entregable2_ids.unidad_medida")
    cantidad = fields.Integer(string='CANTIDAD', tracking=True, related="entregable2_ids.cantidad")
    porcentaje = fields.Float(string='PESO PONDERADO', tracking=True, related="entregable2_ids.porcentaje")
    duracion_periodos = fields.Float(string='DURACIÓN (PERÍODOS)', tracking=True,
                                     related="entregable2_ids.duracion_periodos")
    fecha_inicio = fields.Date(string='INICIO', tracking=True, related="entregable2_ids.fecha_inicio")
    fecha_fin = fields.Date(string='FINAL', tracking=True, related="entregable2_ids.fecha_fin")

    estado = fields.Selection([
        ('pendiente', 'NO INICIADO'),
        ('en_progreso', 'EJECUTANDO'),
        ('en_atraso', 'ATRASADO'),
        ('completado', 'FINALIZADO'),
    ], string='ESTADO', tracking=True, readonly=False, default='pendiente')

    def open_traceability_view(self):
        return {
            'name': 'Trazabilidad',
            'type': 'ir.actions.act_window',
            'res_model': 'traceability.record2',
            'view_mode': 'tree,form',
            'target': 'current',
        }

    def update_praneacion_tabla(self):
        # Buscar todos los registros de praneacion
        praneaciones = self.env['praneacion'].search([])

        # Iterar sobre los registros de praneacion
        for praneacion in praneaciones:
            # Obtener el ejecucion_en_id de la praneacion actual
            ejecucion_id = praneacion.ejecucion_en_id.id

            # Buscar todos los registros de Entregable relacionados con el mismo ejecucion_en_id
            entregables = self.env['entregable'].search([('ejecucion_en_id', '=', ejecucion_id)])

            # Iterar sobre los registros de Entregable encontrados
            for entregable in entregables:
                # Verificar si ya existe una línea en praneacion_tabla para la praneacion actual y el entregable actual
                existing_line = self.env['praneacion_tabla'].search(
                    [('praneacion_tabla_id', '=', praneacion.id), ('accion_descripcion', '=', entregable.id)])

                # Si no existe la línea, crearla
                if not existing_line:
                    praneacion_tabla_obj = self.env['praneacion_tabla'].create({
                        'praneacion_tabla_id': praneacion.id,
                        'accion_descripcion': entregable.id,  # Establecer el ID del nuevo registro de Entregable
                        # Puedes incluir más campos según lo necesites
                    })

        return True

    def unlink(self):
        # Obtener los registros de praneacion_tabla asociados a los registros de Entregable que se eliminarán
        praneacion_tabla_objs = self.env['praneacion_tabla'].search([('accion_descripcion', 'in', self.ids)])

        # Eliminar los registros de praneacion_tabla asociados
        praneacion_tabla_objs.unlink()

        # Llamar al método unlink del super para eliminar los registros de Entregable
        return super(Entregable, self).unlink()

    # variables
    objetivo_especifico = fields.Text(string='Objetivo Específico', tracking=True)
    nu_componente = fields.Integer(string='Número de Componente', tracking=True)
    componente = fields.Text(string='Componente')
    nu_actividad = fields.Integer(string='Número de Actividad', tracking=True)
    actividad = fields.Text(string='Actividad', tracking=True)
    nu_entregable = fields.Char(string='Un Entregable', tracking=True)
    entregable = fields.Text(string='Entregable', tracking=True)
    valor_entregable = fields.Float(string='Valor Entregable', tracking=True)

    def action_ver_planificacion_presupuestal(self):
        self.update_praneacion_tabla()
        # Obtener el registro de ejecución actual
        ejecucion_actual = self.ejecucion_en_id

        # Verificar si ya existe un registro de praneacion relacionado con el registro actual
        praneacion_actual = self.env['praneacion'].search([('ejecucion_en_id', '=', ejecucion_actual.id)], limit=1)

        if not praneacion_actual:
            # Si no existe, crear un nuevo registro en 'praneacion' relacionado con la ejecución actual
            praneacion_vals = {
                'name': 'Planificación Presupuestal',
                'ejecucion_en_id': ejecucion_actual.id,
                # Puedes agregar más campos y valores aquí según sea necesario
            }
            praneacion_actual = self.env['praneacion'].create(praneacion_vals)

        # Llamar al método para agregar registros a praneacion_tabla
        self.agregar_a_praneacion_tabla(praneacion_actual)

        # Devolver una acción para ver el registro de 'praneacion' relacionado con el registro actual
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planificación Presupuestal',
            'res_model': 'praneacion',
            'view_mode': 'form',
            'res_id': praneacion_actual.id,
            'target': 'current',
        }

    def agregar_a_praneacion_tabla(self, praneacion_actual):
        # Buscar todos los Entregables relacionados con la misma ejecucion_en_id
        entregables = self.env['entregable'].search([('ejecucion_en_id', '=', self.ejecucion_en_id.id)])
        # Buscar todos los registros de praneacion relacionados con la misma ejecucion_en_id
        praneaciones = self.env['praneacion'].search([('ejecucion_en_id', '=', self.ejecucion_en_id.id)])

        # Crear todas las combinaciones de Entregables y praneacion
        for entregable in entregables:
            for praneacion in praneaciones:
                # Verificar si ya existe una línea en praneacion_tabla para el entregable actual y la praneacion actual
                existing_line = self.env['praneacion_tabla'].search(
                    [('praneacion_tabla_id', '=', praneacion.id), ('accion_descripcion', '=', entregable.id)])

                # Si no existe la línea, crearla
                if not existing_line:
                    self.env['praneacion_tabla'].create({
                        'praneacion_tabla_id': praneacion.id,
                        'accion_descripcion': entregable.id,
                        # Puedes incluir más campos según lo necesites
                    })

    @api.model
    def create(self, values):
        entregable_obj = super(Entregable, self).create(values)
        # No es necesario llamar al método desde aquí, ya que se llamará desde action_ver_planificacion_presupuestal
        return entregable_obj

    name = fields.Char(string='Nombre', tracking=True)

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)

    def action_view_Matriz_de_entregables(self):
        # Verificar si ya existe un registro en 'entregables_2' para esta subactividad
        existing_record = self.env['entregables_2'].search([('proyec_id', '=', self.id)], limit=1)

        if not existing_record:
            # Si no existe, crear un nuevo registro en 'entregables_2' con los datos de la subactividad actual
            entregables_2_vals = {
                'proyec_id': self.id  # Asociar el registro a la subactividad actual
                # Puedes agregar más campos y valores aquí según sea necesario
            }
            existing_record = self.env['entregables_2'].create(entregables_2_vals)

        # Devolver una acción para ver el registro correspondiente a la subactividad actual en formato formulario y árbol
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'entregables_2',
            'view_mode': 'tree,form,graph,pivot,calendar,activity,kanban',
            'target': 'current',
            'domain': [('proyec_id', '=', self.id)],
            # Mostrar solo los registros relacionados con la subactividad actual
            'context': {'default_proyec_id': self.id},  # Establecer la subactividad actual como valor predeterminado
            # Abrir la vista del registro correspondiente a la subactividad actual en formato formulario y árbol
        }

    entregable2_ids = fields.One2many('entregables_2', 'proyec_id', string='entregables')
    total_final = fields.Float(string='Total Final', related="entregable2_ids.total_final", tracking=True)

    Periodo1 = fields.Float(string="P 1", related="entregable2_ids.Periodo1", tracking=True)
    Periodo2 = fields.Float(string="P 2", related="entregable2_ids.Periodo2", tracking=True)
    Periodo3 = fields.Float(string="P 3", related="entregable2_ids.Periodo3", tracking=True)
    Periodo4 = fields.Float(string="P 4", related="entregable2_ids.Periodo4", tracking=True)
    Periodo5 = fields.Float(string="P 5", related="entregable2_ids.Periodo5", tracking=True)
    Periodo6 = fields.Float(string="P 6", related="entregable2_ids.Periodo6", tracking=True)
    Periodo7 = fields.Float(string="P 7", related="entregable2_ids.Periodo7", tracking=True)
    Periodo8 = fields.Float(string="P 8", related="entregable2_ids.Periodo8", tracking=True)
    Periodo9 = fields.Float(string="P 9", related="entregable2_ids.Periodo9", tracking=True)
    Periodo10 = fields.Float(string="P 10", related="entregable2_ids.Periodo10", tracking=True)
    Periodo11 = fields.Float(string="P 11", related="entregable2_ids.Periodo11", tracking=True)
    Periodo12 = fields.Float(string="P 12", related="entregable2_ids.Periodo12", tracking=True)
    Periodo13 = fields.Float(string="P 13", related="entregable2_ids.Periodo13", tracking=True)
    Periodo14 = fields.Float(string="P 14", related="entregable2_ids.Periodo14", tracking=True)
    Periodo15 = fields.Float(string="P 15", related="entregable2_ids.Periodo15", tracking=True)
    Periodo16 = fields.Float(string="P 16", related="entregable2_ids.Periodo16", tracking=True)
    Periodo17 = fields.Float(string="P 17", related="entregable2_ids.Periodo17", tracking=True)
    Periodo18 = fields.Float(string="P 18", related="entregable2_ids.Periodo18", tracking=True)
    Periodo19 = fields.Float(string="P 19", related="entregable2_ids.Periodo19", tracking=True)
    Periodo20 = fields.Float(string="P 20", related="entregable2_ids.Periodo20", tracking=True)
    Periodo21 = fields.Float(string="P 21", related="entregable2_ids.Periodo21", tracking=True)
    Periodo22 = fields.Float(string="P 22", related="entregable2_ids.Periodo22", tracking=True)
    Periodo23 = fields.Float(string="P 23", related="entregable2_ids.Periodo23", tracking=True)
    Periodo24 = fields.Float(string="P 24", related="entregable2_ids.Periodo24", tracking=True)
    Periodo25 = fields.Float(string="P 25", related="entregable2_ids.Periodo25", tracking=True)
    Periodo26 = fields.Float(string="P 26", related="entregable2_ids.Periodo26", tracking=True)
    Periodo27 = fields.Float(string="P 27", related="entregable2_ids.Periodo27", tracking=True)
    Periodo28 = fields.Float(string="P 28", related="entregable2_ids.Periodo28", tracking=True)
    Periodo29 = fields.Float(string="P 29", related="entregable2_ids.Periodo29", tracking=True)
    Periodo30 = fields.Float(string="P 30", related="entregable2_ids.Periodo30", tracking=True)

    actividades_completas_count = fields.Integer(string='Actividades completas',
                                                 related="entregable2_ids.actividades_completas_count",
                                                 tracking=True)
    actividades_en_progreso_count = fields.Integer(string='Actividades EJECUTADO',
                                                   related="entregable2_ids.actividades_en_progreso_count",
                                                   tracking=True)
    actividades_retrasadas_count = fields.Integer(string='Actividades retrasadas',
                                                  related="entregable2_ids.actividades_retrasadas_count",
                                                  tracking=True)
    actividades_pendiente_count = fields.Integer(string='Actividades pendiente',
                                                 related="entregable2_ids.actividades_pendiente_count",
                                                 tracking=True)


class Matrizdeentregables(models.Model):
    _name = 'entregables_2'
    _description = 'ACCIONES'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='# ACCION')
    hiden = fields.Char(string='hiden')
    proyec_id = fields.Many2one('entregable', string='SUBACTIVIDAD', ondelete='cascade')
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado',
                                           related="proyec_id.proyecto_relacionado")
    # 28-11-2024 SAMUEL
    ############################################SAMUEL##################################################################
    is_editable = fields.Boolean(string='Es editable', related="proyec_id.is_editable", default=False)

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################

    def action_ingresar_rubros(self):
        related_id = self.otros_accion.id  # Obtenemos el ID seleccionado en otros_accion
        if related_id:
            rubros_record = self.env['rubros'].browse(related_id)
            rubros_record.write({'entregable_id': self.id})  # Set the entregable_id field of the Rubros record
            return {
                'name': 'Tareas',
                'view_mode': 'form',
                'res_model': 'rubros',
                'res_id': related_id,  # Pasamos el ID seleccionado como res_id
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            return False

    otros_accion = fields.Many2one(comodel_name='rubros', string="ACCION", tracking=True,
                                   domain="[('entregable_id', '=', id)]")
    verificador = fields.Char(string='VERIFICADOR', tracking=True)
    unidad_medida = fields.Char(string='UNIDAD DE MEDIDA', tracking=True)
    cantidad = fields.Integer(string='CANTIDAD', tracking=True)
    porcentaje = fields.Float(string='PESO PONDERADO', tracking=True)
    duracion_periodos = fields.Float(string='DURACIÓN (PERÍODOS)', tracking=True)

    actividades_completas_count = fields.Integer(string='Actividades completas',
                                                 compute='_compute_actividades_completas_count',
                                                 tracking=True)
    actividades_en_progreso_count = fields.Integer(string='Actividades EJECUTADO',
                                                   compute='_compute_actividades_en_progreso_count',
                                                   tracking=True)
    actividades_retrasadas_count = fields.Integer(string='Actividades retrasadas',
                                                  compute='_compute_actividades_retrasadas_count',
                                                  tracking=True)

    actividades_pendiente_count = fields.Integer(string='Actividades pendientes',
                                                 compute='_compute_actividades_pendiente_count',
                                                 tracking=True)

    @api.model
    def create(self, vals):
        record = super().create(vals)
        self.env['traceability.record2'].track_changes(self._name, record.id, vals)
        return record

    def write(self, vals):
        self.env['traceability.record2'].track_changes(self._name, self.id, vals)
        result = super().write(vals)
        return result

    @api.depends('proyec_id')
    def _compute_actividades_completas_count(self):
        for record in self:
            record.actividades_completas_count = self.env['entregables_2'].search_count(
                [('proyec_id', '=', record.proyec_id.id), ('estado', '=', 'completado')])

    @api.depends('proyec_id')
    def _compute_actividades_en_progreso_count(self):
        for record in self:
            record.actividades_en_progreso_count = self.env['entregables_2'].search_count(
                [('proyec_id', '=', record.proyec_id.id), ('estado', '=', 'en_ejectando')])

    @api.depends('proyec_id')
    def _compute_actividades_retrasadas_count(self):
        for record in self:
            record.actividades_retrasadas_count = self.env['entregables_2'].search_count(
                [('proyec_id', '=', record.proyec_id.id), ('estado', '=', 'en_atraso')])

    @api.depends('proyec_id')
    def _compute_actividades_pendiente_count(self):
        for record in self:
            record.actividades_pendiente_count = self.env['entregables_2'].search_count(
                [('proyec_id', '=', record.proyec_id.id), ('estado', '=', 'pendiente')])



    @api.depends('porcentaje', 'proyec_id')
    def _compute_total_porcentaje(self):
        for record in self:
            same_proyec_records = self.filtered(lambda r: r.proyec_id == record.proyec_id)
            total_porcentaje = sum(same_proyec_records.mapped('porcentaje'))
            record.total_porcentaje = total_porcentaje

    total_porcentaje = fields.Float(string='Total Porcentaje', compute='_compute_total_porcentaje', store=True,
                                    tracking=True)

    #######################################################################################################################
    def open_popup(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana'].search([('matriz_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 1',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana'].create({
                'matriz_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 1',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p2(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana2'].search([('matriz2_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 2',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana2',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana2'].create({
                'matriz2_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 2',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana2',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p3(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana3'].search([('matriz3_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 3',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana3',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana3'].create({
                'matriz3_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 3',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana3',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p4(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana4'].search([('matriz4_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 4',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana4',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana4'].create({
                'matriz4_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 4',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana4',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p5(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana5'].search([('matriz5_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 5',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana5',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana5'].create({
                'matriz5_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 5',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana5',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p6(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana6'].search([('matriz6_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 6',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana6',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana6'].create({
                'matriz6_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 6',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana6',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p7(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana7'].search([('matriz7_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 7',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana7',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana7'].create({
                'matriz7_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 7',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana7',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p8(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana8'].search([('matriz8_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 8',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana8',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana8'].create({
                'matriz8_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 8',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana8',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p9(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana9'].search([('matriz9_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 9',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana9',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana9'].create({
                'matriz9_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 9',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana9',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p10(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana10'].search([('matriz10_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 10',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana10',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana10'].create({
                'matriz10_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 10',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana10',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p11(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana11'].search([('matriz11_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 11',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana11',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana11'].create({
                'matriz11_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 11',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana11',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p12(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana12'].search([('matriz12_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 12',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana12',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana12'].create({
                'matriz12_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 12',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana12',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p13(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana13'].search([('matriz13_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 13',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana13',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana13'].create({
                'matriz13_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 13',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana13',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p14(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana14'].search([('matriz14_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 14',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana14',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana14'].create({
                'matriz14_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 14',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana14',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p15(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana15'].search([('matriz15_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 15',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana15',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana15'].create({
                'matriz15_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 15',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana15',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p16(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana16'].search([('matriz16_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 16',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana16',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana16'].create({
                'matriz16_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 16',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana16',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p17(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana17'].search([('matriz17_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 17',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana17',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana17'].create({
                'matriz17_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 17',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana17',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p18(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana18'].search([('matriz18_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 18',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana18',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana18'].create({
                'matriz18_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 18',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana18',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p19(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana19'].search([('matriz19_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 19',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana19',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana19'].create({
                'matriz19_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 19',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana19',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p20(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana20'].search([('matriz20_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 20',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana20',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana20'].create({
                'matriz20_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 20',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana20',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p21(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana21'].search([('matriz21_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 21',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana21',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana21'].create({
                'matriz21_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 21',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana21',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p22(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana22'].search([('matriz22_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 22',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana22',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana22'].create({
                'matriz22_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 22',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana22',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p23(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana23'].search([('matriz23_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 23',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana23',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana23'].create({
                'matriz23_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 23',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana23',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p24(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana24'].search([('matriz24_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 24',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana24',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana24'].create({
                'matriz24_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 24',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana24',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p25(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana25'].search([('matriz25_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 25',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana25',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana25'].create({
                'matriz25_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 25',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana25',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p26(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana26'].search([('matriz26_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 26',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana26',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana26'].create({
                'matriz26_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 26',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana26',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p27(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana27'].search([('matriz27_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 27',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana27',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana27'].create({
                'matriz27_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 27',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana27',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p28(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana28'].search([('matriz28_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 28',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana28',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana28'].create({
                'matriz28_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 28',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana28',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p29(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana29'].search([('matriz29_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 29',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana29',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana29'].create({
                'matriz29_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 29',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana29',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def open_popup_p30(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['ventana30'].search([('matriz30_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'Ventana emergente P 30',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana30',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['ventana30'].create({
                'matriz30_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'Ventana emergente P 30',
                'type': 'ir.actions.act_window',
                'res_model': 'ventana30',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    Periodo1 = fields.Float(string="P 1", tracking=True, readonly=True)
    ventana_id = fields.One2many('ventana', 'matriz_id', string="Matriz de entregables")

    # Define the HTML field for clickable Periodo1
    periodo1_html = fields.Html(
        string="P 1",
        compute='_compute_periodo1_html',
        sanitize=False
    )

    @api.depends('Periodo1', 'ventana_id', 'is_editable')
    def _compute_periodo1_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo1_html = f"""
                    <div>{html_escape(f"{record.Periodo1:.2f}")}</div>
                """
            else:
                ventana_record = record.ventana_id
                ventana_id = ventana_record.id if ventana_record else False

                # Configurar el contexto
                context = {
                    'default_matriz_id': record.id
                }

                # Generar HTML con la funcionalidad de abrir ventana
                record.periodo1_html = f"""
                    <div class="o_form_uri" style="cursor: pointer;"
                         onclick="(function() {{
                             var action = {{
                                 type: 'ir.actions.act_window',
                                 name: 'Ventana emergente P 1',
                                 res_model: 'ventana',
                                 view_mode: 'form',
                                 res_id: {ventana_id if ventana_id else 'false'},
                                 target: 'new',
                                 views: [[false, 'form']],
                                 context: {context}
                             }};
                             odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                         }})()">
                        {html_escape(f"{record.Periodo1:.2f}")}
                    </div>
                """

    Periodo2 = fields.Float(string="P 2", tracking=True, readonly=True)

    # Define the HTML field for clickable Periodo2
    periodo2_html = fields.Html(
        string="P 2",
        compute='_compute_periodo2_html',
        sanitize=False
    )

    @api.depends('Periodo2', 'ventana2_id', 'is_editable')
    def _compute_periodo2_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo2_html = f"""
                    <div>{html_escape(f"{record.Periodo2:.2f}")}</div>
                """
            else:
                ventana2_record = record.ventana2_id and record.ventana2_id[
                    0]  # Taking the first associated 'ventana' record
                ventana2_id = ventana2_record.id if ventana2_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz2_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo2_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 2',
                                         res_model: 'ventana2',
                                         view_mode: 'form',
                                         res_id: {ventana2_id if ventana2_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo2:.2f}")}
                            </div>
                        """

    Periodo3 = fields.Float(string="P 3", tracking=True, readonly=True)

    periodo3_html = fields.Html(
        string="P 3",
        compute='_compute_periodo3_html',
        sanitize=False
    )

    @api.depends('Periodo3', 'ventana3_id', 'is_editable')
    def _compute_periodo3_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo3_html = f"""
                    <div>{html_escape(f"{record.Periodo3:.2f}")}</div>
                """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana3_record = record.ventana3_id and record.ventana3_id[
                    0]  # Taking the first associated 'ventana' record
                ventana3_id = ventana3_record.id if ventana3_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz3_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo3_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 3',
                                         res_model: 'ventana3',
                                         view_mode: 'form',
                                         res_id: {ventana3_id if ventana3_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo3:.2f}")}
                            </div>
                        """

    Periodo4 = fields.Float(string="P 4", tracking=True, readonly=True)
    periodo4_html = fields.Html(
        string="P 4",
        compute='_compute_periodo4_html',
        sanitize=False
    )

    @api.depends('Periodo4', 'ventana4_id', 'is_editable')
    def _compute_periodo4_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo4_html = f"""
                    <div>{html_escape(f"{record.Periodo4:.2f}")}</div>
                """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana4_record = record.ventana4_id and record.ventana4_id[
                    0]  # Taking the first associated 'ventana' record
                ventana4_id = ventana4_record.id if ventana4_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz4_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo4_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 4',
                                         res_model: 'ventana4',
                                         view_mode: 'form',
                                         res_id: {ventana4_id if ventana4_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo4:.2f}")}
                            </div>
                        """

    Periodo5 = fields.Float(string="P 5", tracking=True, readonly=True)
    periodo5_html = fields.Html(
        string="P 5",
        compute='_compute_periodo5_html',
        sanitize=False
    )

    @api.depends('Periodo5', 'ventana5_id', 'is_editable')
    def _compute_periodo5_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo5_html = f"""
                    <div>{html_escape(f"{record.Periodo5:.2f}")}</div>
                """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana5_record = record.ventana5_id and record.ventana5_id[
                    0]  # Taking the first associated 'ventana' record
                ventana5_id = ventana5_record.id if ventana5_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz5_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo5_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 5',
                                         res_model: 'ventana5',
                                         view_mode: 'form',
                                         res_id: {ventana5_id if ventana5_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo5:.2f}")}
                            </div>
                        """

    Periodo6 = fields.Float(string="P 6", tracking=True, readonly=True)
    periodo6_html = fields.Html(
        string="P 6",
        compute='_compute_periodo6_html',
        sanitize=False
    )

    @api.depends('Periodo6', 'ventana6_id', 'is_editable')
    def _compute_periodo6_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo6_html = f"""
                    <div>{html_escape(f"{record.Periodo6:.2f}")}</div>
                """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana6_record = record.ventana6_id and record.ventana6_id[
                    0]  # Taking the first associated 'ventana' record
                ventana6_id = ventana6_record.id if ventana6_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz6_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo6_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 6',
                                         res_model: 'ventana6',
                                         view_mode: 'form',
                                         res_id: {ventana6_id if ventana6_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo6:.2f}")}
                            </div>
                        """

    Periodo7 = fields.Float(string="P 7", tracking=True, readonly=True)
    periodo7_html = fields.Html(
        string="P 7",
        compute='_compute_periodo7_html',
        sanitize=False
    )

    @api.depends('Periodo7', 'ventana7_id', 'is_editable')
    def _compute_periodo7_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo7_html = f"""
                    <div>{html_escape(f"{record.Periodo7:.2f}")}</div>
                """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana7_record = record.ventana7_id and record.ventana7_id[
                    0]  # Taking the first associated 'ventana' record
                ventana7_id = ventana7_record.id if ventana7_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz7_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo7_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 7',
                                         res_model: 'ventana7',
                                         view_mode: 'form',
                                         res_id: {ventana7_id if ventana7_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo7:.2f}")}
                            </div>
                        """

    Periodo8 = fields.Float(string="P 8", tracking=True, readonly=True)

    periodo8_html = fields.Html(
        string="P 8",
        compute='_compute_periodo8_html',
        sanitize=False
    )

    @api.depends('Periodo8', 'ventana8_id', 'is_editable')
    def _compute_periodo8_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo8_html = f"""
                    <div>{html_escape(f"{record.Periodo8:.2f}")}</div>
                """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana8_record = record.ventana8_id and record.ventana8_id[
                    0]  # Taking the first associated 'ventana' record
                ventana8_id = ventana8_record.id if ventana8_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz8_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo8_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 8',
                                         res_model: 'ventana8',
                                         view_mode: 'form',
                                         res_id: {ventana8_id if ventana8_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo8:.2f}")}
                            </div>
                        """

    Periodo9 = fields.Float(string="P 9", tracking=True, readonly=True)

    periodo9_html = fields.Html(
        string="P 9",
        compute='_compute_periodo9_html',
        sanitize=False
    )

    @api.depends('Periodo9', 'ventana9_id', 'is_editable')
    def _compute_periodo9_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo9_html = f"""
                    <div>{html_escape(f"{record.Periodo9:.2f}")}</div>
                """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana9_record = record.ventana9_id and record.ventana9_id[
                    0]  # Taking the first associated 'ventana' record
                ventana9_id = ventana9_record.id if ventana9_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz9_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo9_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 9',
                                         res_model: 'ventana9',
                                         view_mode: 'form',
                                         res_id: {ventana9_id if ventana9_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo9:.2f}")}
                            </div>
                        """

    Periodo10 = fields.Float(string="P 10", tracking=True, readonly=True)
    periodo10_html = fields.Html(
        string="P 10",
        compute='_compute_periodo10_html',
        sanitize=False
    )

    @api.depends('Periodo10', 'ventana10_id', 'is_editable')
    def _compute_periodo10_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo10_html = f"""
                    <div>{html_escape(f"{record.Periodo10:.2f}")}</div>
                """
            else:
                ventana10_record = record.ventana10_id and record.ventana10_id[
                    0]  # Taking the first associated 'ventana' record
                ventana10_id = ventana10_record.id if ventana10_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz10_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo10_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 10',
                                         res_model: 'ventana10',
                                         view_mode: 'form',
                                         res_id: {ventana10_id if ventana10_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo10:.2f}")}
                            </div>
                        """

    Periodo11 = fields.Float(string="P 11", tracking=True, readonly=True)
    periodo11_html = fields.Html(
        string="P 11",
        compute='_compute_periodo11_html',
        sanitize=False
    )

    @api.depends('Periodo11', 'ventana11_id', 'is_editable')
    def _compute_periodo11_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo11_html = f"""
                        <div>{html_escape(f"{record.Periodo11:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana11_record = record.ventana11_id and record.ventana11_id[
                    0]  # Taking the first associated 'ventana' record
                ventana11_id = ventana11_record.id if ventana11_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz11_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo11_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 11',
                                         res_model: 'ventana11',
                                         view_mode: 'form',
                                         res_id: {ventana11_id if ventana11_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo11:.2f}")}
                            </div>
                        """

    Periodo12 = fields.Float(string="P 12", tracking=True, readonly=True)
    periodo12_html = fields.Html(
        string="P 12",
        compute='_compute_periodo12_html',
        sanitize=False
    )

    @api.depends('Periodo12', 'ventana12_id', 'is_editable')
    def _compute_periodo12_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo12_html = f"""
                        <div>{html_escape(f"{record.Periodo12:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana12_record = record.ventana12_id and record.ventana12_id[
                    0]  # Taking the first associated 'ventana' record
                ventana12_id = ventana12_record.id if ventana12_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz12_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo12_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 12',
                                         res_model: 'ventana12',
                                         view_mode: 'form',
                                         res_id: {ventana12_id if ventana12_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo12:.2f}")}
                            </div>
                        """

    Periodo13 = fields.Float(string="P 13", tracking=True, readonly=True)
    periodo13_html = fields.Html(
        string="P 13",
        compute='_compute_periodo13_html',
        sanitize=False
    )

    @api.depends('Periodo13', 'ventana13_id', 'is_editable')
    def _compute_periodo13_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo13_html = f"""
                        <div>{html_escape(f"{record.Periodo13:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana13_record = record.ventana13_id and record.ventana13_id[
                    0]  # Taking the first associated 'ventana' record
                ventana13_id = ventana13_record.id if ventana13_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz13_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo13_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 13',
                                         res_model: 'ventana13',
                                         view_mode: 'form',
                                         res_id: {ventana13_id if ventana13_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo13:.2f}")}
                            </div>
                        """

    Periodo14 = fields.Float(string="P 14", tracking=True, readonly=True)
    periodo14_html = fields.Html(
        string="P 14",
        compute='_compute_periodo14_html',
        sanitize=False
    )

    @api.depends('Periodo14', 'ventana14_id', 'is_editable')
    def _compute_periodo14_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo14_html = f"""
                        <div>{html_escape(f"{record.Periodo14:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana14_record = record.ventana14_id and record.ventana14_id[
                    0]  # Taking the first associated 'ventana' record
                ventana14_id = ventana14_record.id if ventana14_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz14_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo14_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 14',
                                         res_model: 'ventana14',
                                         view_mode: 'form',
                                         res_id: {ventana14_id if ventana14_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo14:.2f}")}
                            </div>
                        """

    Periodo15 = fields.Float(string="P 15", tracking=True, readonly=True)
    periodo15_html = fields.Html(
        string="P 15",
        compute='_compute_periodo15_html',
        sanitize=False
    )

    @api.depends('Periodo15', 'ventana15_id', 'is_editable')
    def _compute_periodo15_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo15_html = f"""
                        <div>{html_escape(f"{record.Periodo15:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana15_record = record.ventana15_id and record.ventana15_id[
                    0]  # Taking the first associated 'ventana' record
                ventana15_id = ventana15_record.id if ventana15_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz15_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo15_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 15',
                                         res_model: 'ventana15',
                                         view_mode: 'form',
                                         res_id: {ventana15_id if ventana15_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo15:.2f}")}
                            </div>
                        """

    Periodo16 = fields.Float(string="P 16", tracking=True, readonly=True)
    periodo16_html = fields.Html(
        string="P 16",
        compute='_compute_periodo16_html',
        sanitize=False
    )

    @api.depends('Periodo16', 'ventana16_id', 'is_editable')
    def _compute_periodo16_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo16_html = f"""
                        <div>{html_escape(f"{record.Periodo16:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana16_record = record.ventana16_id and record.ventana16_id[
                    0]  # Taking the first associated 'ventana' record
                ventana16_id = ventana16_record.id if ventana16_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz16_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo16_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 16',
                                         res_model: 'ventana16',
                                         view_mode: 'form',
                                         res_id: {ventana16_id if ventana16_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo16:.2f}")}
                            </div>
                        """

    Periodo17 = fields.Float(string="P 17", tracking=True, readonly=True)
    periodo17_html = fields.Html(
        string="P 17",
        compute='_compute_periodo17_html',
        sanitize=False
    )

    @api.depends('Periodo17', 'ventana17_id', 'is_editable')
    def _compute_periodo17_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo17_html = f"""
                        <div>{html_escape(f"{record.Periodo17:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana17_record = record.ventana17_id and record.ventana17_id[
                    0]  # Taking the first associated 'ventana' record
                ventana17_id = ventana17_record.id if ventana17_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz17_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo17_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 17',
                                         res_model: 'ventana17',
                                         view_mode: 'form',
                                         res_id: {ventana17_id if ventana17_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo17:.2f}")}
                            </div>
                        """

    Periodo18 = fields.Float(string="P 18", tracking=True, readonly=True)
    periodo18_html = fields.Html(
        string="P 18",
        compute='_compute_periodo18_html',
        sanitize=False
    )

    @api.depends('Periodo18', 'ventana18_id', 'is_editable')
    def _compute_periodo18_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo18_html = f"""
                        <div>{html_escape(f"{record.Periodo18:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana18_record = record.ventana18_id and record.ventana18_id[
                    0]  # Taking the first associated 'ventana' record
                ventana18_id = ventana18_record.id if ventana18_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz18_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo18_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 18',
                                         res_model: 'ventana18',
                                         view_mode: 'form',
                                         res_id: {ventana18_id if ventana18_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo18:.2f}")}
                            </div>
                        """

    Periodo19 = fields.Float(string="P 19", tracking=True, readonly=True)
    periodo19_html = fields.Html(
        string="P 19",
        compute='_compute_periodo19_html',
        sanitize=False
    )

    @api.depends('Periodo19', 'ventana19_id', 'is_editable')
    def _compute_periodo19_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo19_html = f"""
                        <div>{html_escape(f"{record.Periodo19:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana19_record = record.ventana19_id and record.ventana19_id[
                    0]  # Taking the first associated 'ventana' record
                ventana19_id = ventana19_record.id if ventana19_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz19_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo19_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 19',
                                         res_model: 'ventana19',
                                         view_mode: 'form',
                                         res_id: {ventana19_id if ventana19_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo19:.2f}")}
                            </div>
                        """

    Periodo20 = fields.Float(string="P 20", tracking=True, readonly=True)
    periodo20_html = fields.Html(
        string="P 20",
        compute='_compute_periodo20_html',
        sanitize=False
    )

    @api.depends('Periodo20', 'ventana20_id', 'is_editable')
    def _compute_periodo20_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo20_html = f"""
                        <div>{html_escape(f"{record.Periodo20:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana20_record = record.ventana20_id and record.ventana20_id[
                    0]  # Taking the first associated 'ventana' record
                ventana20_id = ventana20_record.id if ventana20_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz20_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo20_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 20',
                                         res_model: 'ventana20',
                                         view_mode: 'form',
                                         res_id: {ventana20_id if ventana20_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo20:.2f}")}
                            </div>
                        """

    Periodo21 = fields.Float(string="P 21", tracking=True, readonly=True)
    periodo21_html = fields.Html(
        string="P 21",
        compute='_compute_periodo21_html',
        sanitize=False
    )

    @api.depends('Periodo21', 'ventana21_id', 'is_editable')
    def _compute_periodo21_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo21_html = f"""
                        <div>{html_escape(f"{record.Periodo21:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana21_record = record.ventana21_id and record.ventana21_id[
                    0]  # Taking the first associated 'ventana' record
                ventana21_id = ventana21_record.id if ventana21_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz21_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo21_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 21',
                                         res_model: 'ventana21',
                                         view_mode: 'form',
                                         res_id: {ventana21_id if ventana21_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo21:.2f}")}
                            </div>
                        """

    Periodo22 = fields.Float(string="P 22", tracking=True, readonly=True)
    periodo22_html = fields.Html(
        string="P 22",
        compute='_compute_periodo22_html',
        sanitize=False
    )

    @api.depends('Periodo22', 'ventana22_id', 'is_editable')
    def _compute_periodo22_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo22_html = f"""
                        <div>{html_escape(f"{record.Periodo22:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana22_record = record.ventana22_id and record.ventana22_id[
                    0]  # Taking the first associated 'ventana' record
                ventana22_id = ventana22_record.id if ventana22_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz22_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo22_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 22',
                                         res_model: 'ventana22',
                                         view_mode: 'form',
                                         res_id: {ventana22_id if ventana22_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo22:.2f}")}
                            </div>
                        """

    Periodo23 = fields.Float(string="P 23", tracking=True, readonly=True)
    periodo23_html = fields.Html(
        string="P 23",
        compute='_compute_periodo23_html',
        sanitize=False
    )

    @api.depends('Periodo23', 'ventana23_id', 'is_editable')
    def _compute_periodo23_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo23_html = f"""
                        <div>{html_escape(f"{record.Periodo23:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana23_record = record.ventana23_id and record.ventana23_id[
                    0]  # Taking the first associated 'ventana' record
                ventana23_id = ventana23_record.id if ventana23_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz23_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo23_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 23',
                                         res_model: 'ventana23',
                                         view_mode: 'form',
                                         res_id: {ventana23_id if ventana23_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo23:.2f}")}
                            </div>
                        """

    Periodo24 = fields.Float(string="P 24", tracking=True, readonly=True)
    periodo24_html = fields.Html(
        string="P 24",
        compute='_compute_periodo24_html',
        sanitize=False
    )

    @api.depends('Periodo24', 'ventana24_id', 'is_editable')
    def _compute_periodo24_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo24_html = f"""
                        <div>{html_escape(f"{record.Periodo24:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana24_record = record.ventana24_id and record.ventana24_id[
                    0]  # Taking the first associated 'ventana' record
                ventana24_id = ventana24_record.id if ventana24_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz24_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo24_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 24',
                                         res_model: 'ventana24',
                                         view_mode: 'form',
                                         res_id: {ventana24_id if ventana24_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo24:.2f}")}
                            </div>
                        """

    Periodo25 = fields.Float(string="P 25", tracking=True, readonly=True)
    periodo25_html = fields.Html(
        string="P 25",
        compute='_compute_periodo25_html',
        sanitize=False
    )

    @api.depends('Periodo25', 'ventana25_id', 'is_editable')
    def _compute_periodo25_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo25_html = f"""
                        <div>{html_escape(f"{record.Periodo25:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana25_record = record.ventana25_id and record.ventana25_id[
                    0]  # Taking the first associated 'ventana' record
                ventana25_id = ventana25_record.id if ventana25_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz25_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo25_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 25',
                                         res_model: 'ventana25',
                                         view_mode: 'form',
                                         res_id: {ventana25_id if ventana25_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo25:.2f}")}
                            </div>
                        """

    Periodo26 = fields.Float(string="P 26", tracking=True, readonly=True)
    periodo26_html = fields.Html(
        string="P 26",
        compute='_compute_periodo26_html',
        sanitize=False
    )

    @api.depends('Periodo26', 'ventana26_id', 'is_editable')
    def _compute_periodo26_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo26_html = f"""
                        <div>{html_escape(f"{record.Periodo26:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana26_record = record.ventana26_id and record.ventana26_id[
                    0]  # Taking the first associated 'ventana' record
                ventana26_id = ventana26_record.id if ventana26_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz26_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo26_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 26',
                                         res_model: 'ventana26',
                                         view_mode: 'form',
                                         res_id: {ventana26_id if ventana26_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo26:.2f}")}
                            </div>
                        """

    Periodo27 = fields.Float(string="P 27", tracking=True, readonly=True)
    periodo27_html = fields.Html(
        string="P 27",
        compute='_compute_periodo27_html',
        sanitize=False
    )

    @api.depends('Periodo27', 'ventana27_id', 'is_editable')
    def _compute_periodo27_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo27_html = f"""
                        <div>{html_escape(f"{record.Periodo27:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana27_record = record.ventana27_id and record.ventana27_id[
                    0]  # Taking the first associated 'ventana' record
                ventana27_id = ventana27_record.id if ventana27_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz27_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo27_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 27',
                                         res_model: 'ventana27',
                                         view_mode: 'form',
                                         res_id: {ventana27_id if ventana27_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo27:.2f}")}
                            </div>
                        """

    Periodo28 = fields.Float(string="P28", tracking=True, readonly=True)
    periodo28_html = fields.Html(
        string="P 28",
        compute='_compute_periodo28_html',
        sanitize=False
    )

    @api.depends('Periodo28', 'ventana28_id', 'is_editable')
    def _compute_periodo28_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo28_html = f"""
                        <div>{html_escape(f"{record.Periodo28:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana28_record = record.ventana28_id and record.ventana28_id[
                    0]  # Taking the first associated 'ventana' record
                ventana28_id = ventana28_record.id if ventana28_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz28_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo28_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 28',
                                         res_model: 'ventana28',
                                         view_mode: 'form',
                                         res_id: {ventana28_id if ventana28_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo28:.2f}")}
                            </div>
                        """

    Periodo29 = fields.Float(string="P 29", tracking=True, readonly=True)
    periodo29_html = fields.Html(
        string="P 29",
        compute='_compute_periodo29_html',
        sanitize=False
    )

    @api.depends('Periodo29', 'ventana29_id', 'is_editable')
    def _compute_periodo29_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo29_html = f"""
                        <div>{html_escape(f"{record.Periodo29:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana29_record = record.ventana29_id and record.ventana29_id[
                    0]  # Taking the first associated 'ventana' record
                ventana29_id = ventana29_record.id if ventana29_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz29_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo29_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 29',
                                         res_model: 'ventana29',
                                         view_mode: 'form',
                                         res_id: {ventana29_id if ventana29_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo29:.2f}")}
                            </div>
                        """

    Periodo30 = fields.Float(string="P 30", tracking=True, readonly=True)
    periodo30_html = fields.Html(
        string="P 30",
        compute='_compute_periodo30_html',
        sanitize=False
    )

    @api.depends('Periodo30', 'ventana30_id', 'is_editable')
    def _compute_periodo30_html(self):
        for record in self:
            if record.is_editable:
                # Mostrar el valor como texto simple si no es editable
                record.periodo30_html = f"""
                        <div>{html_escape(f"{record.Periodo30:.2f}")}</div>
                    """
            else:
                # Ensure that we fetch the first 'ventana' record associated with the current 'entregables_2' record
                ventana30_record = record.ventana30_id and record.ventana30_id[
                    0]  # Taking the first associated 'ventana' record
                ventana30_id = ventana30_record.id if ventana30_record else False

                # Set up the context to open the same 'ventana' record
                context = {
                    'default_matriz30_id': record.id
                    # Pass the current 'entregables_2' record ID to maintain the same context
                }

                # Generate the HTML with JavaScript to open the existing 'ventana' record
                record.periodo30_html = f"""
                            <div class="o_form_uri" style="cursor: pointer;"
                                 onclick="(function() {{
                                     var action = {{
                                         type: 'ir.actions.act_window',
                                         name: 'Ventana emergente P 30',
                                         res_model: 'ventana30',
                                         view_mode: 'form',
                                         res_id: {ventana30_id if ventana30_id else 'false'},
                                         target: 'new',
                                         views: [[false, 'form']],
                                         context: {context}  <!-- Ensure context is passed to avoid creating a new record -->
                                     }};
                                     odoo.__WOWL_DEBUG__.root.env.services.action.doAction(action);
                                 }})()">
                                {html_escape(f"{record.Periodo30:.2f}")}
                            </div>
                        """

    ###################################################################################################################

    def calcular_total(self):
        for record in self:
            total_periodos = sum([
                getattr(record, f'Periodo{i}') for i in range(1, 31)
            ])
            total = total_periodos * record.porcentaje / 100
            record.total = total

    total = fields.Float(string='Porcentaje total', compute='calcular_total', tracking=True)

    @api.depends('total')
    def calcular_total_final(self):
        for record in self:
            registros_mismo_proyecto = self.filtered(lambda r: r.proyec_id == record.proyec_id)
            total_final = sum(registro.total for registro in registros_mismo_proyecto)
            record.total_final = total_final

    total_final = fields.Float(string='Porcentaje final', compute='calcular_total_final', tracking=True)
    ####################################################################################################################

    user_id = fields.Many2one('res.users', string='Project Manager', default=lambda self: self.env.user,
                              tracking=True)

    fecha_fin = fields.Date(string='FINAL', tracking=True)
    fecha_actual = fields.Date(string='Fecha Actual', compute='_compute_fecha_actual')

    cron_job_id = fields.Many2one('ir.cron', string="Cron Job")

    @api.depends('fecha_fin')
    def _compute_fecha_actual(self):
        for record in self:
            record.fecha_actual = fields.Date.today()

    estado = fields.Selection([
        ('pendiente', 'NO INICIADO'),
        ('en_ejectando', 'EJECUTANDO'),
        ('en_atraso', 'ATRASADO'),
        ('completado', 'FINALIZADO'),
    ], string='ESTADO', tracking=True, readonly=False, default='pendiente', copy=False)

    @api.onchange('estado')
    def _onchange_estado(self):
        for record in self:
            if record.estado == 'completado':
                record._remove_cron_job()
                # Actualizar el estado a 'finalizado' después de eliminar el cron job
                record.estado = 'completado'

    def _create_or_update_cron_job(self):
        if not self.cron_job_id:
            cron_job = self.env['ir.cron'].sudo().create({
                'name': f"alerta {self.id}",
                'model_id': self.env['ir.model'].search([('model', '=', self._name)]).id,
                'state': 'code',
                'code': f"model.email_actividades()",
                'interval_number': 1,
                'interval_type': 'days',
                'numbercall': -1,
                'doall': False,
                'active': True,
                'user_id': self.env.user.id,
                'nextcall': fields.Datetime.from_string(f"{datetime.now().date()} 12:00:00")
            })
            self.cron_job_id = cron_job.id
        else:
            self.cron_job_id.write({
                'nextcall': fields.Datetime.from_string(f"{datetime.now().date()} 12:00:00"),
            })
        print("Cron job creado o actualizado.")

    def _remove_cron_job(self):
        if self.cron_job_id:
            self.cron_job_id.unlink()
            self.cron_job_id = False
            print("Cron job eliminado.")

    def email_actividades(self, after_days=5):
        self.obtener_correos_coordinadores()
        # Obtener la plantilla de correo electrónico por su referencia
        email_template = self.env.ref('Matriz.email_actividades_vencidas', raise_if_not_found=False)

        if not email_template:
            raise UserError("¡Plantilla de correo electrónico no encontrada!")

        # Obtener el usuario actual
        user = self.env.user

        # Obtener la dirección de correo electrónico del usuario
        recipient_email = user.email

        # Generar el correo electrónico a partir de la plantilla y los detalles del registro
        email_values = {
            'subject': email_template.subject,
            'body_html': email_template.body_html,
            'email_from': 'go@fundacionunivalle.org',
            # Reemplaza con la dirección de correo que deseas utilizar como remitente
            'email_to': recipient_email,
        }

        # Crear el registro del correo electrónico y enviarlo
        mail = self.env['mail.mail'].create(email_values)
        mail.send()

        # Verificar si el correo se envió correctamente
        if mail.state == 'sent':
            print("¡El correo se envió correctamente!")
        else:
            print("¡Error al enviar el correo!")

        return True

    ########################################################################################################################

    fecha_inicio = fields.Date(string='INICIO', tracking=True)

    diferencia_dias = fields.Integer(string="Diferencia de días", compute='_compute_diferencia_dias')
    cron_job_created = fields.Boolean(string="Cron Job Creado")

    ultima_fecha_correo_enviado = fields.Date(string="Última Fecha de Correo Enviado")

    @api.depends('fecha_actual', 'fecha_fin')
    def _compute_diferencia_dias(self):
        for record in self:
            if record.fecha_actual and record.fecha_fin:
                fecha_actual = fields.Date.from_string(record.fecha_actual)
                fecha_fin = fields.Date.from_string(record.fecha_fin)
                diferencia = fecha_fin - fecha_actual
                record.diferencia_dias = diferencia.days

                # Verificar si la diferencia de días es igual a -1, 0, o está entre 1 y 8
                if record.diferencia_dias == -1:
                    # Verificar si el trabajo cron ya ha sido creado
                    if not record.cron_job_created:
                        record._create_or_update_cron_job()
                        record.estado = 'en_atraso'
                        # Marcar que el trabajo cron ha sido creado
                        record.cron_job_created = True
                elif 0 < record.diferencia_dias <= 8:
                    # Verificar si el correo electrónico no se ha enviado hoy
                    hoy = fields.Date.today()
                    if record.ultima_fecha_correo_enviado != hoy:
                        record.email_actividades_por_dia()
                        # Actualizar la última fecha de envío del correo electrónico
                        record.ultima_fecha_correo_enviado = hoy
            else:
                record.diferencia_dias = 0

    def email_actividades_por_dia(self, after_days=5):
        # Obtener la plantilla de correo electrónico por su referencia
        email_template = self.env.ref('Matriz.email_actividades_vencidas_antes_de_ocho_dias', raise_if_not_found=False)

        if not email_template:
            raise UserError("¡Plantilla de correo electrónico no encontrada!")

        # Obtener el usuario seleccionado en el campo user_id
        project_manager = self.user_id

        # Obtener la dirección de correo electrónico del usuario
        recipient_email = project_manager.email

        # Generar el correo electrónico a partir de la plantilla y los detalles del registro
        email_values = {
            'subject': email_template.subject,
            'body_html': email_template.body_html,
            'email_from': 'go@fundacionunivalle.org',
            # Reemplaza con la dirección de correo que deseas utilizar como remitente
            'email_to': recipient_email,
        }

        # Crear el registro del correo electrónico y enviarlo
        mail = self.env['mail.mail'].create(email_values)
        mail.send()

        # Verificar si el correo se envió correctamente
        if mail.state == 'sent':
            print("¡El correo se envió correctamente!")
        else:
            print("¡Error al enviar el correo!")

        return True

    ######################################################################################################################

    def obtener_correos_coordinadores(self):
        # Buscar el ID del grupo de rol "Coordinador"
        group_coordinador_id = self.env.ref('Matriz.group_contratacion_coordinador').id

        if not group_coordinador_id:
            raise UserError("¡El grupo de rol de Coordinador no fue encontrado!")

        # Buscar todos los usuarios que tienen el rol de Coordinador
        usuarios_coordinadores = self.env['res.users'].search([('groups_id', 'in', [group_coordinador_id])])

        if not usuarios_coordinadores:
            print("No se encontraron usuarios con el rol de Coordinador.")
        else:
            print("Correos de los usuarios con el rol de Coordinador:")
            for usuario in usuarios_coordinadores:
                print(usuario.email)

    ################################################################################################
    estados = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados = 'fecha_cercana_cierre'

    ventana2_id = fields.One2many('ventana2', 'matriz2_id', string="Matriz de entregables")

    estados2 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado2', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado2(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados2 = 'fecha_cercana_cierre'

    ventana3_id = fields.One2many('ventana3', 'matriz3_id', string="Matriz de entregables")

    estados3 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado3', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado3(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados3 = 'fecha_cercana_cierre'

    ventana4_id = fields.One2many('ventana4', 'matriz4_id', string="Matriz de entregables")

    estados4 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado4', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado4(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados4 = 'fecha_cercana_cierre'

    ventana5_id = fields.One2many('ventana5', 'matriz5_id', string="Matriz de entregables")

    estados5 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado5', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado5(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados5 = 'fecha_cercana_cierre'

    ventana6_id = fields.One2many('ventana6', 'matriz6_id', string="Matriz de entregables")

    estados6 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado6', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado6(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados6 = 'fecha_cercana_cierre'

    ventana7_id = fields.One2many('ventana7', 'matriz7_id', string="Matriz de entregables")

    estados7 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado7', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado7(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados7 = 'fecha_cercana_cierre'

    ventana8_id = fields.One2many('ventana8', 'matriz8_id', string="Matriz de entregables")

    estados8 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado8', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado8(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados8 = 'fecha_cercana_cierre'

    ventana9_id = fields.One2many('ventana9', 'matriz9_id', string="Matriz de entregables")

    estados9 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado9', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado9(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados9 = 'fecha_cercana_cierre'

    ventana10_id = fields.One2many('ventana10', 'matriz10_id', string="Matriz de entregables")

    estados10 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado10(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados10 = 'fecha_cercana_cierre'

    ventana11_id = fields.One2many('ventana11', 'matriz11_id', string="Matriz de entregables")

    estados11 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado11', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado11(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados11 = 'fecha_cercana_cierre'

    ventana12_id = fields.One2many('ventana12', 'matriz12_id', string="Matriz de entregables")

    estados12 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado12(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados12 = 'fecha_cercana_cierre'

    ventana13_id = fields.One2many('ventana13', 'matriz13_id', string="Matriz de entregables")

    estados13 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado13(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados13 = 'fecha_cercana_cierre'

    ventana14_id = fields.One2many('ventana14', 'matriz14_id', string="Matriz de entregables")

    estados14 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado14(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados14 = 'fecha_cercana_cierre'

    ventana15_id = fields.One2many('ventana15', 'matriz15_id', string="Matriz de entregables")

    estados15 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado15(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados15 = 'fecha_cercana_cierre'

    ventana16_id = fields.One2many('ventana16', 'matriz16_id', string="Matriz de entregables")

    estados16 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado16(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados16 = 'fecha_cercana_cierre'

    ventana17_id = fields.One2many('ventana17', 'matriz17_id', string="Matriz de entregables")

    estados17 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado17(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados17 = 'fecha_cercana_cierre'

    ventana18_id = fields.One2many('ventana18', 'matriz18_id', string="Matriz de entregables")

    estados18 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado18(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados18 = 'fecha_cercana_cierre'

    ventana19_id = fields.One2many('ventana19', 'matriz19_id', string="Matriz de entregables")

    estados19 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado19(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados19 = 'fecha_cercana_cierre'

    ventana20_id = fields.One2many('ventana20', 'matriz20_id', string="Matriz de entregables")

    estados20 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado20(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados20 = 'fecha_cercana_cierre'

    ventana21_id = fields.One2many('ventana21', 'matriz21_id', string="Matriz de entregables")

    estados21 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado21(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados21 = 'fecha_cercana_cierre'

    ventana22_id = fields.One2many('ventana22', 'matriz22_id', string="Matriz de entregables")

    estados22 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado22(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados22 = 'fecha_cercana_cierre'

    ventana23_id = fields.One2many('ventana23', 'matriz23_id', string="Matriz de entregables")

    estados23 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado23(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados23 = 'fecha_cercana_cierre'

    ventana24_id = fields.One2many('ventana24', 'matriz24_id', string="Matriz de entregables")

    estados24 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado24(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados24 = 'fecha_cercana_cierre'

    ventana25_id = fields.One2many('ventana25', 'matriz25_id', string="Matriz de entregables")

    estados25 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado25(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados25 = 'fecha_cercana_cierre'

    ventana26_id = fields.One2many('ventana26', 'matriz26_id', string="Matriz de entregables")

    estados26 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado26(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados26 = 'fecha_cercana_cierre'

    ventana27_id = fields.One2many('ventana27', 'matriz27_id', string="Matriz de entregables")

    estados27 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado27(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados27 = 'fecha_cercana_cierre'

    ventana28_id = fields.One2many('ventana28', 'matriz28_id', string="Matriz de entregables")

    estados28 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado28(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados28 = 'fecha_cercana_cierre'

    ventana29_id = fields.One2many('ventana29', 'matriz29_id', string="Matriz de entregables")

    estados29 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado29(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados29 = 'fecha_cercana_cierre'

    ventana30_id = fields.One2many('ventana30', 'matriz30_id', string="Matriz de entregables")

    estados30 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado10', store=True)

    @api.depends('fecha_inicio', 'fecha_fin')
    def _compute_estado30(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin:
                diferencia = record.fecha_fin - record.fecha_inicio
                if diferencia == timedelta(days=8):
                    record.estados30 = 'fecha_cercana_cierre'


################################################################################################
class Ventana(models.Model):
    _name = 'ventana'
    _description = 'Ventana 1'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz_id.estados = record.estados

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo1': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo1': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo1_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo1_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


##############################################################################################################
class Ventana2(models.Model):
    _name = 'ventana2'
    _description = 'Ventana 2'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados2 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados2 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados2 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados2 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados2 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz2_id.estados2 = record.estados2

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz2_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz2_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo2': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo2': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo2_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo2_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


###################################################################################################################
class Ventana3(models.Model):
    _name = 'ventana3'
    _description = 'Ventana 3'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados3 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados3 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados3 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados3 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados3 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz3_id.estados3 = record.estados3

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz3_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz3_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo3': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo3': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo3_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo3_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana4(models.Model):
    _name = 'ventana4'
    _description = 'Ventana 4'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados4 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados4 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados4 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados4 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados4 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz4_id.estados4 = record.estados4

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz4_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz4_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo4': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo4': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo4_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo4_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana5(models.Model):
    _name = 'ventana5'
    _description = 'Ventana 5'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados5 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados5 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados5 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados5 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados5 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz5_id.estados5 = record.estados5

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz5_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz5_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo5': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo5': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo5_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo5_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana6(models.Model):
    _name = 'ventana6'
    _description = 'Ventana 6'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados6 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados6 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados6 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados6 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados6 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz6_id.estados6 = record.estados6

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz6_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz6_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo6': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo6': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo6_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo6_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana7(models.Model):
    _name = 'ventana7'
    _description = 'Ventana 7'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados7 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados7 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados7 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados7 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados7 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz7_id.estados7 = record.estados7

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz7_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz7_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo7': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo7': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo7_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo7_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana8(models.Model):
    _name = 'ventana8'
    _description = 'Ventana 8'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados8 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados8 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados8 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados8 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados8 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz8_id.estados8 = record.estados8

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz8_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz8_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo8': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo8': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo8_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo8_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana9(models.Model):
    _name = 'ventana9'
    _description = 'Ventana 9'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados9 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados9 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados9 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados9 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados9 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz9_id.estados9 = record.estados9

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz9_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz9_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo9': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo9': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo9_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo9_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana10(models.Model):
    _name = 'ventana10'
    _description = 'Ventana 10'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados10 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados10 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados10 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados10 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados10 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz10_id.estados10 = record.estados10

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz10_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz10_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo10': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo10': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo10_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo10_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana11(models.Model):
    _name = 'ventana11'
    _description = 'Ventana 11'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados11 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados11 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados11 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados11 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados11 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz11_id.estados11 = record.estados11

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz11_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz11_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo11': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo11': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo11_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo11_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana12(models.Model):
    _name = 'ventana12'
    _description = 'Ventana 12'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados12 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados12 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados12 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados12 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados12 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz12_id.estados12 = record.estados12

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz12_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz12_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo12': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo12': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo12_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo12_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana13(models.Model):
    _name = 'ventana13'
    _description = 'Ventana 13'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados13 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados13 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados13 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados13 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados13 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz13_id.estados13 = record.estados13

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz13_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz13_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo13': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo13': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo13_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo13_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana14(models.Model):
    _name = 'ventana14'
    _description = 'Ventana 14'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados14 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados14 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados14 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados14 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados14 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz14_id.estados14 = record.estados14

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz14_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz14_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo14': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo14': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo14_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo14_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana15(models.Model):
    _name = 'ventana15'
    _description = 'Ventana 15'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados15 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados15 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados15 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados15 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados15 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz15_id.estados15 = record.estados15

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz15_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz15_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo15': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo15': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo15_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo15_html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana16(models.Model):
    _name = 'ventana16'
    _description = 'Ventana 16'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados16 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados16 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados16 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados16 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados16 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz16_id.estados16 = record.estados16

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz16_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz16_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo16': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo16': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo16_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo16html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana17(models.Model):
    _name = 'ventana17'
    _description = 'Ventana 17'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados17 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados17 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados17 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados17 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados17 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz17_id.estados17 = record.estados17

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz17_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz17_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo17': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo17': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo17_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo17html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################


class Ventana18(models.Model):
    _name = 'ventana18'
    _description = 'Ventana 18'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados18 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados18 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados18 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados18 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados18 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz18_id.estados18 = record.estados18

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz18_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz18_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo18': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo18': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo18_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo18html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana19(models.Model):
    _name = 'ventana19'
    _description = 'Ventana 19'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados19 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados19 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados19 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados19 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados19 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz19_id.estados19 = record.estados19

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz19_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz19_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo19': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo19': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo19_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo19html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana20(models.Model):
    _name = 'ventana20'
    _description = 'Ventana 20'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados20 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados20 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados20 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados20 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados20 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz20_id.estados20 = record.estados20

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz20_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz20_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo20': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo20': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo20_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo20html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana21(models.Model):
    _name = 'ventana21'
    _description = 'Ventana 21'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados21 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados21 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados21 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados21 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados21 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz21_id.estados21 = record.estados21

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz21_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz21_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo21': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo21': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo21_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo21html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana22(models.Model):
    _name = 'ventana22'
    _description = 'Ventana 22'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados22 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados22 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados22 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados22 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados22 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz22_id.estados22 = record.estados22

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz22_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz22_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo22': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo22': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo22_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo22html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana23(models.Model):
    _name = 'ventana23'
    _description = 'Ventana 23'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados23 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados23 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados23 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados23 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados23 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz23_id.estados23 = record.estados23

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz23_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz23_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo23': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo23': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo23_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo23html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana24(models.Model):
    _name = 'ventana24'
    _description = 'Ventana 24'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados24 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados24 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados24 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados24 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados24 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz24_id.estados24 = record.estados24

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz24_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz24_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo24': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo24': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo24_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo24html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana25(models.Model):
    _name = 'ventana25'
    _description = 'Ventana 25'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados25 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados25 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados25 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados25 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados25 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz25_id.estados25 = record.estados25

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz25_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz25_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo25': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo25': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo25_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo25html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana26(models.Model):
    _name = 'ventana26'
    _description = 'Ventana 26'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados26 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados26 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados26 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados26 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados26 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz26_id.estados26 = record.estados26

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz26_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz26_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo26': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo26': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo26_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo26html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana27(models.Model):
    _name = 'ventana27'
    _description = 'Ventana 27'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados27 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados27 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados27 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados27 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados27 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz27_id.estados27 = record.estados27

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz27_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz27_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo27': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo27': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo27_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo27html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana28(models.Model):
    _name = 'ventana28'
    _description = 'Ventana 28'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados28 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados28 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados28 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados28 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados28 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz28_id.estados28 = record.estados28

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz28_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz28_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo28': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo28': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo28_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo28html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana29(models.Model):
    _name = 'ventana29'
    _description = 'Ventana 29'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados29 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados29 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados29 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados29 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados29 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz29_id.estados29 = record.estados29

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz29_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz29_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo29': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo29': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo29_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo29html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


######################################################################################################################

class Ventana30(models.Model):
    _name = 'ventana30'
    _description = 'Ventana 30'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    estados30 = fields.Selection([
        ('retraso_menor', 'Retraso menor'),
        ('retraso_significativo', 'Retraso significativo menor al 90% del programado'),
        ('al_dia', 'Al día o con holgura'),
        ('fecha_cercana_cierre', 'Fecha cercana a cierre de actividad')
    ], string='Estado', compute='_compute_estado')

    def _compute_estado(self):
        for record in self:
            if record.division_porcentajes >= 100:
                record.estados30 = 'al_dia'
            elif record.division_porcentajes == 0:
                record.estados30 = 'retraso_significativo'
            elif record.division_porcentajes < 100 and record.division_porcentajes >= 90:
                record.estados30 = 'retraso_menor'
            elif record.division_porcentajes < 90:
                record.estados30 = 'retraso_significativo'

            # Actualizar el campo 'estado' en el modelo Matrizdeentregables
            record.matriz30_id.estados30 = record.estados30

    porcentaje_programado = fields.Float(string="Porcentaje programado")
    porcentaje_aprobado = fields.Float(string="Porcentaje aprobado")
    retraso_holgura = fields.Float(string="Retraso/Holgura", compute='_compute_resta_porcentajes')
    division_porcentajes = fields.Float(compute='_compute_division_porcentajes', string="División de porcentajes")
    matriz30_id = fields.Many2one('entregables_2', string="Matriz de entregables", inverse_name="ventana2_id")

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_resta_porcentajes(self):
        for record in self:
            record.retraso_holgura = record.porcentaje_aprobado - record.porcentaje_programado

    @api.depends('porcentaje_aprobado', 'porcentaje_programado')
    def _compute_division_porcentajes(self):
        for record in self:
            if record.porcentaje_programado != 0:
                record.division_porcentajes = round(record.porcentaje_aprobado / record.porcentaje_programado * 100, 2)
            else:
                record.division_porcentajes = 0.0

    def Guardar(self):
        # Obtener el valor de porcentaje_programado y porcentaje_aprobado
        porcentaje_programado = self.porcentaje_programado
        porcentaje_aprobado = self.porcentaje_aprobado

        # Buscar el registro en el modelo Matrizdeentregables asociado con este registro de Ventana
        entregables = self.matriz30_id

        # Actualizar cada registro encontrado en Matrizdeentregables
        for entregable in entregables:
            # Si ambos valores están presentes, se debe asignar porcentaje_aprobado
            if porcentaje_programado and porcentaje_aprobado:
                entregable.write({
                    'Periodo30': porcentaje_aprobado,  # Dar preferencia a porcentaje_aprobado
                })
            else:
                # Si solo porcentaje_programado tiene valor, se asigna ese
                entregable.write({
                    'Periodo30': porcentaje_programado,
                })

            # Forzar el recálculo de 'periodo1_html' tras la actualización de 'Periodo1'
            entregable._compute_periodo30_html()

        # Retornar una acción de cliente que indicará al frontend que debe actualizar los campos sin recargar la página
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'update_fields': ['periodo30html'],  # Solo actualizamos el campo HTML
            }
        }

    def action_discard(self):
        # Lógica para descartar los cambios
        return True


    ####################################################################################################################

class Rubros(models.Model):
    _name = 'rubros'
    _description = 'ACCIONES'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="ACCION", tracking=True)

    entregable_id = fields.Many2one('entregables_2', ondelete='cascade')

    tareas_ids = fields.One2many('tareas', 'tarea_id', string='Tareas')

    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado',
                                           related="entregable_id.proyecto_relacionado")

    is_editable = fields.Boolean(string='Es editable', related="entregable_id.is_editable", default=False)
    # 18-11-2024 SAMUEL
    ############################################SAMUEL##################################################################

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################

    def datos(self):
        return {
            'name': 'Tareas',
            'view_mode': 'tree,graph,pivot,activity',
            'res_model': 'tareas',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('tarea_id', '=', self.id)],  # Filtrar por el ID del registro actual de Rubros
        }


class Tareas(models.Model):
    _name = 'tareas'
    _description = 'Tareas'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='TAREA', tracking=True)
    hiden = fields.Char(string='hiden')
    tarea_id = fields.Many2one('rubros', string='Tarea', ondelete='cascade')
    is_editable = fields.Boolean(string='Es editable', related="tarea_id.is_editable", default=False)
    # 18-11-2024 SAMUEL
    ############################################SAMUEL##################################################################

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################

    estado = fields.Selection([
        ('pendiente', 'NO INICIADO'),
        ('en_progreso', 'EJECUTADO'),
        ('en_atraso', 'ATRASADO'),
        ('completado', 'FINALIZADO'),
    ], string='ESTADO', tracking=True, readonly=False, default='pendiente')

    n_tareas = fields.Char(string='# TAREA', tracking=True)
    otros_tareas2 = fields.Many2one(
        comodel_name='contrataciones_fuv',
        string="TAREA",
        tracking=True,
        domain="[('proyecto_relacionado', '=', proyecto_relacionado), ('estado_contratacion', '=', 'contratado')]",
        ondelete='restrict'  # O 'cascade' si prefieres eliminar registros relacionados
    )

    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado',
                                           related="tarea_id.proyecto_relacionado")

    def action_ingresar_tareas(self):
        related_id = self.otros_tareas2.id  # Obtenemos el ID seleccionado en otros_accion
        if related_id:
            tareas_record = self.env['subtareas'].browse(related_id)
            tareas_record.write({'subtareas_id': self.id})  # Set the entregable_id field of the Rubros record
            return {
                'name': 'Tareas',
                'view_mode': 'form',
                'res_model': 'subtareas',
                'res_id': related_id,  # Pasamos el ID seleccionado como res_id
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            return False  # En caso de que no se haya seleccionado ningún registro en otros_accion

    verificador = fields.Char(string='VERIFICADOR', tracking=True)
    unidad_medida = fields.Char(string='UNIDAD DE MEDIDA', tracking=True)
    cantidad = fields.Integer(string='CANTIDAD', tracking=True)
    porcentaje = fields.Float(string='PESO PONDERADO', tracking=True)
    duracion_periodos = fields.Float(string='DURACIÓN (PERÍODOS)', tracking=True)
    fecha_inicio = fields.Date(string='INICIO', tracking=True)
    fecha_fin = fields.Date(string='FINAL', tracking=True)

    Periodo1 = fields.Float(string="P 1", tracking=True)
    Periodo2 = fields.Float(string="P 2", tracking=True)
    Periodo3 = fields.Float(string="P 3", tracking=True)
    Periodo4 = fields.Float(string="P 4", tracking=True)
    Periodo5 = fields.Float(string="P 5", tracking=True)
    Periodo6 = fields.Float(string="P 6", tracking=True)
    Periodo7 = fields.Float(string="P 7", tracking=True)
    Periodo8 = fields.Float(string="P 8", tracking=True)
    Periodo9 = fields.Float(string="P 9", tracking=True)
    Periodo10 = fields.Float(string="P 10", tracking=True)
    Periodo11 = fields.Float(string="P 11", tracking=True)
    Periodo12 = fields.Float(string="P 12", tracking=True)
    Periodo13 = fields.Float(string="P 13", tracking=True)
    Periodo14 = fields.Float(string="P 14", tracking=True)
    Periodo15 = fields.Float(string="P 15", tracking=True)
    Periodo16 = fields.Float(string="P 16", tracking=True)
    Periodo17 = fields.Float(string="P 17", tracking=True)
    Periodo18 = fields.Float(string="P 18", tracking=True)
    Periodo19 = fields.Float(string="P 19", tracking=True)
    Periodo20 = fields.Float(string="P 20", tracking=True)
    Periodo21 = fields.Float(string="P 21", tracking=True)
    Periodo22 = fields.Float(string="P 22", tracking=True)
    Periodo23 = fields.Float(string="P 23", tracking=True)
    Periodo24 = fields.Float(string="P 24", tracking=True)
    Periodo25 = fields.Float(string="P 25", tracking=True)
    Periodo26 = fields.Float(string="P 26", tracking=True)
    Periodo27 = fields.Float(string="P 27", tracking=True)
    Periodo28 = fields.Float(string="P28", tracking=True)
    Periodo29 = fields.Float(string="P 29", tracking=True)
    Periodo30 = fields.Float(string="P 30", tracking=True)




class SubTareas(models.Model):
    _name = 'subtareas'
    _description = 'Sub tareas'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Tareas", tracking=True)

    subtareas_id = fields.Many2one('tareas', ondelete='cascade')

    tareas3_ids = fields.One2many('subtareas2', 'tarea2_id', string='Tareas')

    def datos(self):
        return {
            'name': 'Sub tareas',
            'view_mode': 'tree,graph,pivot,activity',
            'res_model': 'subtareas2',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('tarea2_id', '=', self.id)],  # Filtrar por el ID del registro actual de Rubros
        }


class SubTareas2(models.Model):
    _name = 'subtareas2'
    _description = 'Sub tareas'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Subtareas", tracking=True)

    estado = fields.Selection([
        ('pendiente', 'NO INICIADO'),
        ('en_progreso', 'EJECUTADO'),
        ('en_atraso', 'ATRASADO'),
        ('completado', 'FINALIZADO'),
    ], string='ESTADO', tracking=True, readonly=False, default='pendiente')

    n_tareas = fields.Char(string='# SUBTAREA', tracking=True)
    otros_Subtareas = fields.Many2one(comodel_name='subtareas3', string="SUBTAREA", tracking=True,
                                      domain="[('subtareas3_id', '=', id)]")

    hiden = fields.Char(string='hiden')

    def action_ingresar_Subtareas(self):
        related_id = self.otros_Subtareas.id  # Obtenemos el ID seleccionado en otros_accion
        if related_id:
            tareas_record = self.env['subtareas3'].browse(related_id)
            tareas_record.write({'subtareas3_id': self.id})  # Set the entregable_id field of the Rubros record
            return {
                'name': 'Sub tareas',
                'view_mode': 'form',
                'res_model': 'subtareas3',
                'res_id': related_id,  # Pasamos el ID seleccionado como res_id
                'type': 'ir.actions.act_window',
                'target': 'new',
            }
        else:
            return False  # En caso de que no se haya seleccionado ningún registro en otros_accion

    verificador = fields.Char(string='VERIFICADOR', tracking=True)
    unidad_medida = fields.Char(string='UNIDAD DE MEDIDA', tracking=True)
    cantidad = fields.Integer(string='CANTIDAD', tracking=True)
    porcentaje = fields.Float(string='PESO PONDERADO', tracking=True)
    duracion_periodos = fields.Float(string='DURACIÓN (PERÍODOS)', tracking=True)
    fecha_inicio = fields.Date(string='INICIO', tracking=True)
    fecha_fin = fields.Date(string='FINAL', tracking=True)

    Periodo1 = fields.Float(string="P 1", tracking=True)
    Periodo2 = fields.Float(string="P 2", tracking=True)
    Periodo3 = fields.Float(string="P 3", tracking=True)
    Periodo4 = fields.Float(string="P 4", tracking=True)
    Periodo5 = fields.Float(string="P 5", tracking=True)
    Periodo6 = fields.Float(string="P 6", tracking=True)
    Periodo7 = fields.Float(string="P 7", tracking=True)
    Periodo8 = fields.Float(string="P 8", tracking=True)
    Periodo9 = fields.Float(string="P 9", tracking=True)
    Periodo10 = fields.Float(string="P 10", tracking=True)
    Periodo11 = fields.Float(string="P 11", tracking=True)
    Periodo12 = fields.Float(string="P 12", tracking=True)
    Periodo13 = fields.Float(string="P 13", tracking=True)
    Periodo14 = fields.Float(string="P 14", tracking=True)
    Periodo15 = fields.Float(string="P 15", tracking=True)
    Periodo16 = fields.Float(string="P 16", tracking=True)
    Periodo17 = fields.Float(string="P 17", tracking=True)
    Periodo18 = fields.Float(string="P 18", tracking=True)
    Periodo19 = fields.Float(string="P 19", tracking=True)
    Periodo20 = fields.Float(string="P 20", tracking=True)
    Periodo21 = fields.Float(string="P 21", tracking=True)
    Periodo22 = fields.Float(string="P 22", tracking=True)
    Periodo23 = fields.Float(string="P 23", tracking=True)
    Periodo24 = fields.Float(string="P 24", tracking=True)
    Periodo25 = fields.Float(string="P 25", tracking=True)
    Periodo26 = fields.Float(string="P 26", tracking=True)
    Periodo27 = fields.Float(string="P 27", tracking=True)
    Periodo28 = fields.Float(string="P28", tracking=True)
    Periodo29 = fields.Float(string="P 29", tracking=True)
    Periodo30 = fields.Float(string="P 30", tracking=True)

    tarea2_id = fields.Many2one('subtareas', string='Subtareas', ondelete='cascade')


class SubTareas_base(models.Model):
    _name = 'subtareas3'
    _description = 'Sub tareas'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="subtareas", tracking=True)
    subtareas3_id = fields.Many2one('subtareas2', ondelete='cascade')


class praneacion_presupuestal(models.Model):
    _name = 'praneacion'
    _description = 'Planeación'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Nombre", tracking=True)
    valor_entregable = fields.Float(string='VALOR PROYECTÓ', tracking=True)

    praneacion_ids = fields.One2many('praneacion_tabla', 'praneacion_tabla_id', string='Planificación presupuestal')
    praneacion_total_ids = fields.One2many('praneacion_tabla_total', 'praneacion_tabla_total_id', string='Totales')

    proyec2_id = fields.Many2one('entregable', string='SUBACTIVIDAD', ondelete='cascade')

    ejecucion_en_id = fields.Many2one('ejecucion', string='Ejecución', ondelete='cascade')
    is_editable = fields.Boolean(string='Es editable', related="ejecucion_en_id.is_editable", default=False)
    # 18-11-2024 SAMUEL
    ############################################SAMUEL##################################################################

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################

    accion2 = fields.Char(string='# ACCIÓN', related="praneacion_ids.accion2", tracking=True)
    porcentaje = fields.Float(string='PESO PONDERADO', related="praneacion_ids.porcentaje", tracking=True)
    valor_ejecutado = fields.Float(string='VALOR EJECUTADO', related="praneacion_ids.valor_ejecutado",
                                   tracking=True)
    valor_por_accion = fields.Float(string='VALOR POR ACCIÓN', related="praneacion_ids.valor_por_accion",
                                    tracking=True)

    suma_total = fields.Float(string='Suma Total', related="praneacion_ids.suma_total")

    @api.onchange('praneacion_ids')
    def add_praneacion_total_line(self):
        if self.praneacion_ids and not self.praneacion_total_ids:
            self.praneacion_total_ids = [(0, 0, {})]

    def datos(self):
        return {
            'name': 'Planificación Presupuestal',
            'view_mode': 'tree,form,pivot',
            'res_model': 'praneacion_tabla',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('praneacion_tabla_id', '=', self.id)],  # Filtrar por el ID del registro actual de Rubros
        }

    porcentaje_total = fields.Float(string="PESO PONDERADO", related="praneacion_ids.porcentaje_total",
                                    tracking=True)

    Periodo1_total = fields.Float(string="P 1", related="praneacion_ids.Periodo1_total", tracking=True)
    Periodo2_total = fields.Float(string="P 2", related="praneacion_ids.Periodo2_total", tracking=True)
    Periodo3_total = fields.Float(string="P 3", related="praneacion_ids.Periodo3_total", tracking=True)
    Periodo4_total = fields.Float(string="P 4", related="praneacion_ids.Periodo4_total", tracking=True)
    Periodo5_total = fields.Float(string="P 5", related="praneacion_ids.Periodo5_total", tracking=True)
    Periodo6_total = fields.Float(string="P 6", related="praneacion_ids.Periodo6_total", tracking=True)
    Periodo7_total = fields.Float(string="P 7", related="praneacion_ids.Periodo7_total", tracking=True)
    Periodo8_total = fields.Float(string="P 8", related="praneacion_ids.Periodo8_total", tracking=True)
    Periodo9_total = fields.Float(string="P 9", related="praneacion_ids.Periodo9_total", tracking=True)
    Periodo10_total = fields.Float(string="P 10", related="praneacion_ids.Periodo10_total", tracking=True)
    Periodo11_total = fields.Float(string="P 11", related="praneacion_ids.Periodo11_total", tracking=True)
    Periodo12_total = fields.Float(string="P 12", related="praneacion_ids.Periodo12_total", tracking=True)
    Periodo13_total = fields.Float(string="P 13", related="praneacion_ids.Periodo13_total", tracking=True)
    Periodo14_total = fields.Float(string="P 14", related="praneacion_ids.Periodo14_total", tracking=True)
    Periodo15_total = fields.Float(string="P 15", related="praneacion_ids.Periodo15_total", tracking=True)
    Periodo16_total = fields.Float(string="P 6", related="praneacion_ids.Periodo16_total", tracking=True)
    Periodo17_total = fields.Float(string="P 7", related="praneacion_ids.Periodo17_total", tracking=True)
    Periodo18_total = fields.Float(string="P 8", related="praneacion_ids.Periodo18_total", tracking=True)
    Periodo19_total = fields.Float(string="P 9", related="praneacion_ids.Periodo19_total", tracking=True)
    Periodo20_total = fields.Float(string="P 10", related="praneacion_ids.Periodo20_total", tracking=True)
    Periodo21_total = fields.Float(string="P 11", related="praneacion_ids.Periodo21_total", tracking=True)
    Periodo22_total = fields.Float(string="P 12", related="praneacion_ids.Periodo22_total", tracking=True)
    Periodo23_total = fields.Float(string="P 13", related="praneacion_ids.Periodo23_total", tracking=True)
    Periodo24_total = fields.Float(string="P 14", related="praneacion_ids.Periodo24_total", tracking=True)
    Periodo25_total = fields.Float(string="P 15", related="praneacion_ids.Periodo25_total", tracking=True)
    Periodo26_total = fields.Float(string="P 26", related="praneacion_ids.Periodo26_total", tracking=True)
    Periodo27_total = fields.Float(string="P 27", related="praneacion_ids.Periodo27_total", tracking=True)
    Periodo28_total = fields.Float(string="P 28", related="praneacion_ids.Periodo28_total", tracking=True)
    Periodo29_total = fields.Float(string="P 29", related="praneacion_ids.Periodo29_total", tracking=True)
    Periodo30_total = fields.Float(string="P 30", related="praneacion_ids.Periodo30_total", tracking=True)


class praneacion_presupuestal_tabla(models.Model):
    _name = 'praneacion_tabla'
    _description = 'Planeación'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Nombre")

    praneacion_tabla_id = fields.Many2one('praneacion', string='Planificación presupuestal', ondelete='cascade')
    is_editable = fields.Boolean(string='Es editable', related="praneacion_tabla_id.is_editable", default=False)
    # 18-11-2024 SAMUEL
    ############################################SAMUEL##################################################################

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################

    # Definir las variables relacionadas con la acción
    accion2 = fields.Char(string='# ACCIÓN', tracking=True)
    accion_descripcion = fields.Many2one(comodel_name='entregable', string='ACCIÓN', tracking=True)
    porcentaje = fields.Float(string='PESO PONDERADO', compute='_compute_porcentaje', tracking=True)
    total_final = fields.Float(string='Porcentaje final', related="accion_descripcion.total_final",
                               tracking=True)

    total_multiplicacion = fields.Float(string='Total Multiplicación', compute='_compute_total_multiplicacion',
                                        tracking=True)

    @api.depends('porcentaje', 'total_final')
    def _compute_total_multiplicacion(self):
        for record in self:
            record.total_multiplicacion = (record.porcentaje / 100) * record.total_final

    @api.depends('total_multiplicacion', 'praneacion_tabla_id')
    def _compute_suma_total(self):
        for record in self:
            total_multiplicacion_proyecto = sum(
                linea.total_multiplicacion for linea in
                record.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)]))
            record.suma_total = total_multiplicacion_proyecto

    suma_total = fields.Float(string='Suma Total', compute='_compute_suma_total')

    porcentaje_total = fields.Float(string="PORCENTAJE", compute='_compute_porcentaje_total', tracking=True)

    @api.depends('porcentaje')
    def _compute_porcentaje_total(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna porcentaje para todos los registros
            suma_porcentaje = sum(record.porcentaje for record in records)
            # Asignar la suma a porcentaje_total
            record.porcentaje_total = suma_porcentaje

    valor_ejecutado = fields.Float(string='VALOR EJECUTADO', compute='_compute_valor_ejecutado',
                                   tracking=True)

    @api.depends('Periodo1', 'Periodo2', 'Periodo3', 'Periodo4', 'Periodo5',
                 'Periodo6', 'Periodo7', 'Periodo8', 'Periodo9', 'Periodo10',
                 'Periodo11', 'Periodo12', 'Periodo13', 'Periodo14', 'Periodo15',
                 'Periodo16', 'Periodo17', 'Periodo18', 'Periodo19', 'Periodo20',
                 'Periodo21', 'Periodo22', 'Periodo23', 'Periodo24', 'Periodo25',
                 'Periodo26', 'Periodo27', 'Periodo28', 'Periodo29', 'Periodo30', )
    def _compute_valor_ejecutado(self):
        for record in self:
            periodos = [getattr(record, f'Periodo{i}') for i in range(1, 31)]
            record.valor_ejecutado = sum(periodos)

    @api.depends('valor_por_accion', 'valor_entregable')
    def _compute_porcentaje(self):
        for record in self:
            if record.valor_entregable != 0:
                record.porcentaje = (record.valor_por_accion / record.valor_entregable) * 100
            else:
                record.porcentaje = 0.0

    valor_por_accion = fields.Float(string='VALOR POR ACCIÓN', tracking=True)
    valor_entregable = fields.Float(string='VALOR PROYECTÓ', related="accion_descripcion.valor_entregable",
                                    tracking=True)
    Periodo1 = fields.Float(string="P 1", compute='_compute_periodo1', tracking=True)
    Periodo2 = fields.Float(string="P 2", compute='_compute_periodo2', tracking=True)
    Periodo3 = fields.Float(string="P 3", compute='_compute_periodo3', tracking=True)
    Periodo4 = fields.Float(string="P 4", compute='_compute_periodo4', tracking=True)
    Periodo5 = fields.Float(string="P 5", compute='_compute_periodo5', tracking=True)
    Periodo6 = fields.Float(string="P 6", compute='_compute_periodo6', tracking=True)
    Periodo7 = fields.Float(string="P 7", compute='_compute_periodo7', tracking=True)
    Periodo8 = fields.Float(string="P 8", compute='_compute_periodo8', tracking=True)
    Periodo9 = fields.Float(string="P 9", compute='_compute_periodo9', tracking=True)
    Periodo10 = fields.Float(string="P 10", compute='_compute_periodo10', tracking=True)
    Periodo11 = fields.Float(string="P 11", compute='_compute_periodo11', tracking=True)
    Periodo12 = fields.Float(string="P 12", compute='_compute_periodo12', tracking=True)
    Periodo13 = fields.Float(string="P 13", compute='_compute_periodo13', tracking=True)
    Periodo14 = fields.Float(string="P 14", compute='_compute_periodo14', tracking=True)
    Periodo15 = fields.Float(string="P 15", compute='_compute_periodo15', tracking=True)
    Periodo16 = fields.Float(string="P 16", compute='_compute_periodo16', tracking=True)
    Periodo17 = fields.Float(string="P 17", compute='_compute_periodo17', tracking=True)
    Periodo18 = fields.Float(string="P 18", compute='_compute_periodo18', tracking=True)
    Periodo19 = fields.Float(string="P 19", compute='_compute_periodo19', tracking=True)
    Periodo20 = fields.Float(string="P 20", compute='_compute_periodo20', tracking=True)
    Periodo21 = fields.Float(string="P 21", compute='_compute_periodo21', tracking=True)
    Periodo22 = fields.Float(string="P 22", compute='_compute_periodo22', tracking=True)
    Periodo23 = fields.Float(string="P 23", compute='_compute_periodo23', tracking=True)
    Periodo24 = fields.Float(string="P 24", compute='_compute_periodo24', tracking=True)
    Periodo25 = fields.Float(string="P 25", compute='_compute_periodo25', tracking=True)
    Periodo26 = fields.Float(string="P 26", compute='_compute_periodo26', tracking=True)
    Periodo27 = fields.Float(string="P 27", compute='_compute_periodo27', tracking=True)
    Periodo28 = fields.Float(string="P 28", compute='_compute_periodo28', tracking=True)
    Periodo29 = fields.Float(string="P 29", compute='_compute_periodo29', tracking=True)
    Periodo30 = fields.Float(string="P 30", compute='_compute_periodo30', tracking=True)

    # relaciones
    Periodo1_r = fields.Float(string="Periodo 1", related="accion_descripcion.Periodo1")

    @api.depends('valor_por_accion', 'Periodo1_r')
    def _compute_periodo1(self):
        for record in self:
            record.Periodo1 = round((record.valor_por_accion * record.Periodo1_r) / 100, 2)

    Periodo2_r = fields.Float(string="Periodo 2", related="accion_descripcion.Periodo2")

    @api.depends('valor_por_accion', 'Periodo2_r')
    def _compute_periodo2(self):
        for record in self:
            record.Periodo2 = round((record.valor_por_accion * record.Periodo2_r) / 100, 2)

    Periodo3_r = fields.Float(string="Periodo 3", related="accion_descripcion.Periodo3")

    @api.depends('valor_por_accion', 'Periodo3_r')
    def _compute_periodo3(self):
        for record in self:
            record.Periodo3 = round((record.valor_por_accion * record.Periodo3_r) / 100, 2)

    Periodo4_r = fields.Float(string="Periodo 4", related="accion_descripcion.Periodo4")

    @api.depends('valor_por_accion', 'Periodo4_r')
    def _compute_periodo4(self):
        for record in self:
            record.Periodo4 = round((record.valor_por_accion * record.Periodo4_r) / 100, 2)

    Periodo5_r = fields.Float(string="Periodo 5", related="accion_descripcion.Periodo5")

    @api.depends('valor_por_accion', 'Periodo5_r')
    def _compute_periodo5(self):
        for record in self:
            record.Periodo5 = round((record.valor_por_accion * record.Periodo5_r) / 100, 2)

    Periodo6_r = fields.Float(string="Periodo 6", related="accion_descripcion.Periodo6")

    @api.depends('valor_por_accion', 'Periodo6_r')
    def _compute_periodo6(self):
        for record in self:
            record.Periodo6 = round((record.valor_por_accion * record.Periodo6_r) / 100, 2)

    Periodo7_r = fields.Float(string="Periodo 7", related="accion_descripcion.Periodo7")

    @api.depends('valor_por_accion', 'Periodo6_r')
    def _compute_periodo7(self):
        for record in self:
            record.Periodo7 = round((record.valor_por_accion * record.Periodo7_r) / 100, 2)

    Periodo8_r = fields.Float(string="Periodo 8", related="accion_descripcion.Periodo8")

    @api.depends('valor_por_accion', 'Periodo8_r')
    def _compute_periodo8(self):
        for record in self:
            record.Periodo8 = round((record.valor_por_accion * record.Periodo8_r) / 100, 2)

    Periodo9_r = fields.Float(string="Periodo 9", related="accion_descripcion.Periodo9")

    @api.depends('valor_por_accion', 'Periodo9_r')
    def _compute_periodo9(self):
        for record in self:
            record.Periodo9 = round((record.valor_por_accion * record.Periodo9_r) / 100, 2)

    Periodo10_r = fields.Float(string="Periodo 10", related="accion_descripcion.Periodo10")

    @api.depends('valor_por_accion', 'Periodo10_r')
    def _compute_periodo10(self):
        for record in self:
            record.Periodo10 = round((record.valor_por_accion * record.Periodo10_r) / 100, 2)

    Periodo11_r = fields.Float(string="Periodo 11", related="accion_descripcion.Periodo11")

    @api.depends('valor_por_accion', 'Periodo11_r')
    def _compute_periodo11(self):
        for record in self:
            record.Periodo11 = round((record.valor_por_accion * record.Periodo11_r) / 100, 2)

    Periodo12_r = fields.Float(string="Periodo 12", related="accion_descripcion.Periodo12")

    @api.depends('valor_por_accion', 'Periodo12_r')
    def _compute_periodo12(self):
        for record in self:
            record.Periodo12 = round((record.valor_por_accion * record.Periodo12_r) / 100, 2)

    Periodo13_r = fields.Float(string="Periodo 13", related="accion_descripcion.Periodo13")

    @api.depends('valor_por_accion', 'Periodo13_r')
    def _compute_periodo13(self):
        for record in self:
            record.Periodo13 = round((record.valor_por_accion * record.Periodo13_r) / 100, 2)

    Periodo14_r = fields.Float(string="Periodo 14", related="accion_descripcion.Periodo14")

    @api.depends('valor_por_accion', 'Periodo14_r')
    def _compute_periodo14(self):
        for record in self:
            record.Periodo14 = round((record.valor_por_accion * record.Periodo14_r) / 100, 2)

    Periodo15_r = fields.Float(string="Periodo 15", related="accion_descripcion.Periodo15")

    @api.depends('valor_por_accion', 'Periodo15_r')
    def _compute_periodo15(self):
        for record in self:
            record.Periodo15 = round((record.valor_por_accion * record.Periodo15_r) / 100, 2)

    Periodo16_r = fields.Float(string="Periodo 16", related="accion_descripcion.Periodo16")

    @api.depends('valor_por_accion', 'Periodo16_r')
    def _compute_periodo16(self):
        for record in self:
            record.Periodo16 = round((record.valor_por_accion * record.Periodo16_r) / 100, 2)

    Periodo17_r = fields.Float(string="Periodo 17", related="accion_descripcion.Periodo17")

    @api.depends('valor_por_accion', 'Periodo17_r')
    def _compute_periodo17(self):
        for record in self:
            record.Periodo17 = round((record.valor_por_accion * record.Periodo17_r) / 100, 2)

    Periodo18_r = fields.Float(string="Periodo 18", related="accion_descripcion.Periodo18")

    @api.depends('valor_por_accion', 'Periodo18_r')
    def _compute_periodo18(self):
        for record in self:
            record.Periodo18 = round((record.valor_por_accion * record.Periodo18_r) / 100, 2)

    Periodo19_r = fields.Float(string="Periodo 19", related="accion_descripcion.Periodo19")

    @api.depends('valor_por_accion', 'Periodo19_r')
    def _compute_periodo19(self):
        for record in self:
            record.Periodo19 = round((record.valor_por_accion * record.Periodo19_r) / 100, 2)

    Periodo20_r = fields.Float(string="Periodo 20", related="accion_descripcion.Periodo20")

    @api.depends('valor_por_accion', 'Periodo20_r')
    def _compute_periodo20(self):
        for record in self:
            record.Periodo20 = round((record.valor_por_accion * record.Periodo20_r) / 100, 2)

    Periodo21_r = fields.Float(string="Periodo 21", related="accion_descripcion.Periodo21")

    @api.depends('valor_por_accion', 'Periodo21_r')
    def _compute_periodo21(self):
        for record in self:
            record.Periodo21 = round((record.valor_por_accion * record.Periodo21_r) / 100, 2)

    Periodo22_r = fields.Float(string="Periodo 22", related="accion_descripcion.Periodo22")

    @api.depends('valor_por_accion', 'Periodo22_r')
    def _compute_periodo22(self):
        for record in self:
            record.Periodo22 = round((record.valor_por_accion * record.Periodo22_r) / 100, 2)

    Periodo23_r = fields.Float(string="Periodo 23", related="accion_descripcion.Periodo23")

    @api.depends('valor_por_accion', 'Periodo23_r')
    def _compute_periodo23(self):
        for record in self:
            record.Periodo23 = round((record.valor_por_accion * record.Periodo23_r) / 100, 2)

    Periodo24_r = fields.Float(string="Periodo 24", related="accion_descripcion.Periodo24")

    @api.depends('valor_por_accion', 'Periodo24_r')
    def _compute_periodo24(self):
        for record in self:
            record.Periodo24 = round((record.valor_por_accion * record.Periodo24_r) / 100, 2)

    Periodo25_r = fields.Float(string="Periodo 25", related="accion_descripcion.Periodo25")

    @api.depends('valor_por_accion', 'Periodo25_r')
    def _compute_periodo25(self):
        for record in self:
            record.Periodo25 = round((record.valor_por_accion * record.Periodo25_r) / 100, 2)

    Periodo26_r = fields.Float(string="Periodo 26", related="accion_descripcion.Periodo26")

    @api.depends('valor_por_accion', 'Periodo26_r')
    def _compute_periodo26(self):
        for record in self:
            record.Periodo26 = round((record.valor_por_accion * record.Periodo26_r) / 100, 2)

    Periodo27_r = fields.Float(string="Periodo 27", related="accion_descripcion.Periodo27")

    @api.depends('valor_por_accion', 'Periodo27_r')
    def _compute_periodo27(self):
        for record in self:
            record.Periodo27 = round((record.valor_por_accion * record.Periodo27_r) / 100, 2)

    Periodo28_r = fields.Float(string="Periodo 28", related="accion_descripcion.Periodo28")

    @api.depends('valor_por_accion', 'Periodo28_r')
    def _compute_periodo28(self):
        for record in self:
            record.Periodo28 = round((record.valor_por_accion * record.Periodo28_r) / 100, 2)

    Periodo29_r = fields.Float(string="Periodo 29", related="accion_descripcion.Periodo29")

    @api.depends('valor_por_accion', 'Periodo29_r')
    def _compute_periodo29(self):
        for record in self:
            record.Periodo29 = round((record.valor_por_accion * record.Periodo29_r) / 100, 2)

    Periodo30_r = fields.Float(string="Periodo 30", related="accion_descripcion.Periodo30")

    @api.depends('valor_por_accion', 'Periodo30_r')
    def _compute_periodo30(self):
        for record in self:
            record.Periodo30 = round((record.valor_por_accion * record.Periodo30_r) / 100, 2)

    # Formulas
    Periodo1_total = fields.Float(string="Periodo 1", compute='_compute_periodo_total1')

    @api.depends('Periodo1', 'praneacion_tabla_id')
    def _compute_periodo_total1(self):
        for record in self:
            # Buscar todos los registros que tengan el mismo praneacion_tabla_id que el actual
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para los registros encontrados
            suma_periodo1 = sum(rec.Periodo1 for rec in records)
            # Asignar la suma a Periodo1_total
            record.Periodo1_total = suma_periodo1

    Periodo2_total = fields.Float(string="Periodo 2", compute='_compute_periodo_total2')

    @api.depends('Periodo2', 'praneacion_tabla_id')
    def _compute_periodo_total2(self):
        for record in self:
            # Buscar todos los registros que tengan el mismo praneacion_tabla_id que el actual
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo2 para los registros encontrados
            suma_periodo2 = sum(rec.Periodo2 for rec in records)
            # Asignar la suma a Periodo2_total
            record.Periodo2_total = suma_periodo2

    Periodo3_total = fields.Float(string="Periodo 3", compute='_compute_periodo_total3')

    @api.depends('Periodo3')
    def _compute_periodo_total3(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para todos los registros
            suma_periodo3 = sum(record.Periodo3 for record in records)
            # Asignar la suma a Periodo1_total
            record.Periodo3_total = suma_periodo3

    Periodo4_total = fields.Float(string="Periodo 4", compute='_compute_periodo_total4')

    @api.depends('Periodo4')
    def _compute_periodo_total4(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para todos los registros
            suma_periodo4 = sum(record.Periodo4 for record in records)
            # Asignar la suma a Periodo1_total
            record.Periodo4_total = suma_periodo4

    Periodo5_total = fields.Float(string="Periodo 5", compute='_compute_periodo_total5')

    @api.depends('Periodo5')
    def _compute_periodo_total5(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para todos los registros
            suma_periodo5 = sum(record.Periodo5 for record in records)
            # Asignar la suma a Periodo1_total
            record.Periodo5_total = suma_periodo5

    Periodo6_total = fields.Float(string="Periodo 6", compute='_compute_periodo_total6')

    @api.depends('Periodo6')
    def _compute_periodo_total6(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para todos los registros
            suma_periodo6 = sum(record.Periodo6 for record in records)
            # Asignar la suma a Periodo1_total
            record.Periodo6_total = suma_periodo6

    Periodo7_total = fields.Float(string="Periodo 7", compute='_compute_periodo_total7')

    @api.depends('Periodo7')
    def _compute_periodo_total7(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para todos los registros
            suma_periodo7 = sum(record.Periodo7 for record in records)
            # Asignar la suma a Periodo1_total
            record.Periodo7_total = suma_periodo7

    Periodo8_total = fields.Float(string="Periodo 8", compute='_compute_periodo_total8')

    @api.depends('Periodo8')
    def _compute_periodo_total8(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para todos los registros
            suma_periodo8 = sum(record.Periodo8 for record in records)
            # Asignar la suma a Periodo1_total
            record.Periodo8_total = suma_periodo8

    Periodo9_total = fields.Float(string="Periodo 9", compute='_compute_periodo_total9')

    @api.depends('Periodo9')
    def _compute_periodo_total9(self):
        for record in self:
            # Obtener todos los registros en el modelo
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            # Sumar los valores de la columna Periodo1 para todos los registros
            suma_periodo9 = sum(record.Periodo9 for record in records)
            # Asignar la suma a Periodo1_total
            record.Periodo9_total = suma_periodo9

    Periodo10_total = fields.Float(string="Periodo 10", compute='_compute_periodo_total10')

    @api.depends('Periodo10')
    def _compute_periodo_total10(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo10 = sum(record.Periodo10 for record in records)
            record.Periodo10_total = suma_periodo10

    Periodo11_total = fields.Float(string="Periodo 11", compute='_compute_periodo_total11')

    @api.depends('Periodo11')
    def _compute_periodo_total11(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo11 = sum(record.Periodo11 for record in records)
            record.Periodo11_total = suma_periodo11

    Periodo12_total = fields.Float(string="Periodo 12", compute='_compute_periodo_total12')

    @api.depends('Periodo12')
    def _compute_periodo_total12(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo12 = sum(record.Periodo12 for record in records)
            record.Periodo12_total = suma_periodo12

    Periodo13_total = fields.Float(string="Periodo 13", compute='_compute_periodo_total13')

    @api.depends('Periodo13')
    def _compute_periodo_total13(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo13 = sum(record.Periodo13 for record in records)
            record.Periodo13_total = suma_periodo13

    Periodo14_total = fields.Float(string="Periodo 14", compute='_compute_periodo_total14')

    @api.depends('Periodo14')
    def _compute_periodo_total14(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo14 = sum(record.Periodo14 for record in records)
            record.Periodo14_total = suma_periodo14

    Periodo15_total = fields.Float(string="Periodo 15", compute='_compute_periodo_total15')

    @api.depends('Periodo15')
    def _compute_periodo_total15(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo15 = sum(record.Periodo15 for record in records)
            record.Periodo15_total = suma_periodo15

    Periodo16_total = fields.Float(string="Periodo 16", compute='_compute_periodo_total16')

    @api.depends('Periodo16')
    def _compute_periodo_total16(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo16 = sum(record.Periodo16 for record in records)
            record.Periodo16_total = suma_periodo16

    Periodo17_total = fields.Float(string="Periodo 17", compute='_compute_periodo_total17')

    @api.depends('Periodo17')
    def _compute_periodo_total17(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo17 = sum(record.Periodo17 for record in records)
            record.Periodo17_total = suma_periodo17

    Periodo18_total = fields.Float(string="Periodo 18", compute='_compute_periodo_total18')

    @api.depends('Periodo18')
    def _compute_periodo_total18(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo18 = sum(record.Periodo18 for record in records)
            record.Periodo18_total = suma_periodo18

    Periodo19_total = fields.Float(string="Periodo 19", compute='_compute_periodo_total19')

    @api.depends('Periodo19')
    def _compute_periodo_total19(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo19 = sum(record.Periodo19 for record in records)
            record.Periodo19_total = suma_periodo19

    Periodo20_total = fields.Float(string="Periodo 20", compute='_compute_periodo_total20')

    @api.depends('Periodo20')
    def _compute_periodo_total20(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo20 = sum(record.Periodo20 for record in records)
            record.Periodo20_total = suma_periodo20

    Periodo21_total = fields.Float(string="Periodo 21", compute='_compute_periodo_total21')

    @api.depends('Periodo21')
    def _compute_periodo_total21(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo21 = sum(record.Periodo21 for record in records)
            record.Periodo21_total = suma_periodo21

    Periodo22_total = fields.Float(string="Periodo 22", compute='_compute_periodo_total22')

    @api.depends('Periodo22')
    def _compute_periodo_total22(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo22 = sum(record.Periodo22 for record in records)
            record.Periodo22_total = suma_periodo22

    Periodo23_total = fields.Float(string="Periodo 23", compute='_compute_periodo_total23')

    @api.depends('Periodo23')
    def _compute_periodo_total23(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo23 = sum(record.Periodo23 for record in records)
            record.Periodo23_total = suma_periodo23

    Periodo24_total = fields.Float(string="Periodo 24", compute='_compute_periodo_total24')

    @api.depends('Periodo24')
    def _compute_periodo_total24(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo24 = sum(record.Periodo24 for record in records)
            record.Periodo24_total = suma_periodo24

    Periodo25_total = fields.Float(string="Periodo 25", compute='_compute_periodo_total25')

    @api.depends('Periodo25')
    def _compute_periodo_total25(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo25 = sum(record.Periodo25 for record in records)
            record.Periodo25_total = suma_periodo25

    Periodo26_total = fields.Float(string="Periodo 26", compute='_compute_periodo_total26')

    @api.depends('Periodo26')
    def _compute_periodo_total26(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo26 = sum(record.Periodo26 for record in records)
            record.Periodo26_total = suma_periodo26

    Periodo27_total = fields.Float(string="Periodo 27", compute='_compute_periodo_total27')

    @api.depends('Periodo27')
    def _compute_periodo_total27(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo27 = sum(record.Periodo27 for record in records)
            record.Periodo27_total = suma_periodo27

    Periodo28_total = fields.Float(string="Periodo 28", compute='_compute_periodo_total28')

    @api.depends('Periodo28')
    def _compute_periodo_total28(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo28 = sum(record.Periodo28 for record in records)
            record.Periodo28_total = suma_periodo28

    Periodo29_total = fields.Float(string="Periodo 29", compute='_compute_periodo_total29')

    @api.depends('Periodo29')
    def _compute_periodo_total29(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo29 = sum(record.Periodo29 for record in records)
            record.Periodo29_total = suma_periodo29

    Periodo30_total = fields.Float(string="Periodo 30", compute='_compute_periodo_total30')

    @api.depends('Periodo30')
    def _compute_periodo_total30(self):
        for record in self:
            records = self.search([('praneacion_tabla_id', '=', record.praneacion_tabla_id.id)])
            suma_periodo30 = sum(record.Periodo30 for record in records)
            record.Periodo30_total = suma_periodo30


class praneacion_presupuestal_tabla_total(models.Model):
    _name = 'praneacion_tabla_total'
    _description = 'Planeación'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Nombre")

    praneacion_tabla_total_id = fields.Many2one('praneacion', string='Totales', ondelete='cascade')
    is_editable = fields.Boolean(string='Es editable', related="praneacion_tabla_total_id.is_editable", default=False)

    # 18-11-2024 SAMUEL
    ############################################SAMUEL##################################################################

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    ############################################SAMUEL##################################################################

    # Definir las variables relacionadas con la acción
    porcentaje_total = fields.Float(string='PESO PONDERADO', related="praneacion_tabla_total_id.porcentaje_total",
                                    tracking=True)
    Periodo1_total = fields.Float(string="P 1", related="praneacion_tabla_total_id.Periodo1_total",
                                  tracking=True)
    Periodo2_total = fields.Float(string="P 2", related="praneacion_tabla_total_id.Periodo2_total",
                                  tracking=True)
    Periodo3_total = fields.Float(string="P 3", related="praneacion_tabla_total_id.Periodo3_total",
                                  tracking=True)
    Periodo4_total = fields.Float(string="P 4", related="praneacion_tabla_total_id.Periodo4_total",
                                  tracking=True)
    Periodo5_total = fields.Float(string="P 5", related="praneacion_tabla_total_id.Periodo5_total",
                                  tracking=True)
    Periodo6_total = fields.Float(string="P 6", related="praneacion_tabla_total_id.Periodo6_total",
                                  tracking=True)
    Periodo7_total = fields.Float(string="P 7", related="praneacion_tabla_total_id.Periodo7_total",
                                  tracking=True)
    Periodo8_total = fields.Float(string="P 8", related="praneacion_tabla_total_id.Periodo8_total",
                                  tracking=True)
    Periodo9_total = fields.Float(string="P 9", related="praneacion_tabla_total_id.Periodo9_total",
                                  tracking=True)
    Periodo10_total = fields.Float(string="P 10", related="praneacion_tabla_total_id.Periodo10_total",
                                   tracking=True)
    Periodo11_total = fields.Float(string="P 11", related="praneacion_tabla_total_id.Periodo11_total",
                                   tracking=True)
    Periodo12_total = fields.Float(string="P 12", related="praneacion_tabla_total_id.Periodo12_total",
                                   tracking=True)
    Periodo13_total = fields.Float(string="P 13", related="praneacion_tabla_total_id.Periodo13_total",
                                   tracking=True)
    Periodo14_total = fields.Float(string="P 14", related="praneacion_tabla_total_id.Periodo14_total",
                                   tracking=True)
    Periodo15_total = fields.Float(string="P 15", related="praneacion_tabla_total_id.Periodo15_total",
                                   tracking=True)
    Periodo16_total = fields.Float(string="P 16", related="praneacion_tabla_total_id.Periodo16_total",
                                   tracking=True)
    Periodo17_total = fields.Float(string="P 17", related="praneacion_tabla_total_id.Periodo17_total",
                                   tracking=True)
    Periodo18_total = fields.Float(string="P 18", related="praneacion_tabla_total_id.Periodo18_total",
                                   tracking=True)
    Periodo19_total = fields.Float(string="P 19", related="praneacion_tabla_total_id.Periodo19_total",
                                   tracking=True)
    Periodo20_total = fields.Float(string="P 20", related="praneacion_tabla_total_id.Periodo20_total",
                                   tracking=True)
    Periodo21_total = fields.Float(string="P 21", related="praneacion_tabla_total_id.Periodo21_total",
                                   tracking=True)
    Periodo22_total = fields.Float(string="Pe 22", related="praneacion_tabla_total_id.Periodo22_total",
                                   tracking=True)
    Periodo23_total = fields.Float(string="P 23", related="praneacion_tabla_total_id.Periodo23_total",
                                   tracking=True)
    Periodo24_total = fields.Float(string="P 24", related="praneacion_tabla_total_id.Periodo24_total",
                                   tracking=True)
    Periodo25_total = fields.Float(string="P 25", related="praneacion_tabla_total_id.Periodo25_total",
                                   tracking=True)
    Periodo26_total = fields.Float(string="P 26", related="praneacion_tabla_total_id.Periodo26_total",
                                   tracking=True)
    Periodo27_total = fields.Float(string="P 27", related="praneacion_tabla_total_id.Periodo27_total",
                                   tracking=True)
    Periodo28_total = fields.Float(string="P 28", related="praneacion_tabla_total_id.Periodo28_total",
                                   tracking=True)
    Periodo29_total = fields.Float(string="P 29", related="praneacion_tabla_total_id.Periodo29_total",
                                   tracking=True)
    Periodo30_total = fields.Float(string="P 30", related="praneacion_tabla_total_id.Periodo30_total",
                                   tracking=True)


class TraceabilityRecord2(models.Model):
    _name = 'traceability.record2'
    _description = 'Trazabilidad'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Variables
    model_name = fields.Char(string='Nombre del modelo', tracking=True)
    field_name = fields.Char(string='Nombre del campo', tracking=True)
    new_value = fields.Char(string='Valor', tracking=True)
    user_id = fields.Many2one('res.users', string='Cambiado por', default=lambda self: self.env.user, tracking=True)
    change_date = fields.Datetime(string='Modificado', default=fields.Datetime.now, tracking=True)

    # Funciónes
    @api.model
    def create_traceability_record(self, model_name, record_id, field_name, old_value, new_value):
        """Create a new traceability record."""
        model_description = self.env[model_name]._description
        # Retrieve the human-readable description of the model
        self.create({
            'model_name': model_description,
            'field_name': field_name,
            'new_value': new_value,
            'user_id': self.env.user.id,
        })

    @api.model
    def track_changes(self, model_name, record_id, vals):
        """Track changes in the specified record."""
        record = self.env[model_name].browse(record_id)
        model_fields = self.env[model_name].fields_get()
        for field_name, new_value in vals.items():
            old_value = record[field_name]
            if old_value != new_value:
                if model_fields[field_name]['type'] == 'many2one':
                    comodel_name = model_fields[field_name].get('comodel_name')
                    if comodel_name:
                        # Obtener el nombre legible del registro relacionado
                        new_value_readable = self.env[comodel_name].browse(new_value).name or False
                        old_value_readable = self.env[comodel_name].browse(old_value).name or False
                    else:
                        # Si comodel_name no está definido, dejar new_value y old_value como están
                        new_value_readable = new_value
                        old_value_readable = old_value
                else:
                    # Si el campo no es many2one, dejar new_value y old_value como están
                    new_value_readable = new_value
                    old_value_readable = old_value

                field_label = model_fields[field_name]['string']
                self.create_traceability_record(model_name, record_id, field_label, old_value_readable,
                                                new_value_readable)

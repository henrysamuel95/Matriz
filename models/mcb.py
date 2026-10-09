#!/usr/bin/python
from email.policy import default

# noinspection PyPackageRequirements
from odoo import fields, models, api, exceptions
from attr import exceptions
from googleapiclient.errors import HttpError
from odoo.exceptions import ValidationError  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from odoo.exceptions import AccessError  # type: ignore
import os
import json
import re
from datetime import datetime
import base64
import tempfile
from .drive import GoogleDriveAuth
import mimetypes
import threading
import queue
import logging
_logger = logging.getLogger(__name__)

class MCB(models.Model):
    _name = "mcb"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'MCB'

    def _es_consulta_coordinacion_financiera(self):
        return self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')

    def _marcar_apertura_si_permitido(self):
        # Evita escrituras automáticas cuando el usuario solo tiene rol de consulta.
        if not self._es_consulta_coordinacion_financiera():
            self.write({'write_date': fields.Datetime.now()})

    mcb_ids = fields.One2many('mcb2', 'mcb_id', string="Agregar otrosí")
    contratos_vco_resumen = fields.Float(related="new_mcb_ids.contratos_vco_resumen")

    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    new_total_costos_produccion_N_r = fields.Float(
        compute='_compute_new_total_costos_produccion_N_r',
        store=True
    )

    def _compute_new_total_costos_produccion_N_r(self):
        for rec in self:
            hijo = rec.new_mcb_ids[:1]   # <- tomamos el primer hijo (o el correcto si luego lo filtramos)
            rec.new_total_costos_produccion_N_r = hijo.new_total_costos_produccion_N_r if hijo else 0

    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################
    ####################################################################################################################

    new_total_costos_produccion_sin_pol_fuv = fields.Float(
        compute='_compute_new_total_costos_produccionin_pol_fuv',
        store=True
    )

    def _compute_new_total_costos_produccionin_pol_fuv(self):
        for rec in self:
            hijo = rec.new_mcb_ids[:1]   # <- tomamos el primer hijo (o el correcto si luego lo filtramos)
            rec.new_total_costos_produccion_sin_pol_fuv = hijo.new_total_costos_produccion_sin_pol_fuv if hijo else 0


    costos_total_valor2R = fields.Float(related="new_mcb_ids.costos_total_valor2R")
    poliza_total_r = fields.Monetary(related="new_mcb_ids.poliza_total_r")
    suma_costos_fuv = fields.Monetary(related="new_mcb_ids.suma_costos_fuv")
    suma_adicion_otro_si2 = fields.Monetary(related="new_mcb_ids.suma_adicion_otro_si2")
    valor_total2_final = fields.Float(
        related='new_mcb_ids.valor_total2_final', # Si new_mcb_ids es el campo One2many
        string='Valor total con otrosí', 
        store=True,
        readonly=True
    )                                                             


    informacion_base_agregar_id = fields.Many2one('informacion_base_agregar', string='Informacion base')
    new_name = fields.Char(string='Nuevo nombre', tracking=True, related="informacion_base_agregar_id.nombre_proceso_proyecto")

    new_mcb_valor_total2_final = fields.Float(related="new_mcb_ids.valor_total2_final")

    # lo que crea un modelo en el tablero de control
    @api.model
    def create(self, values):
        mcb_record = super(MCB, self).create(values)
        mcb_record._create_tablero_avances()
        return mcb_record

    def _create_tablero_avances(self):
        # Crear el tablero
        tablero_avances_values = {
            'ejecucion_id': self.id,
            'name': self.new_name,
            'informacion_base_agregar_id2': self.informacion_base_agregar_id.id if self.informacion_base_agregar_id else False,
        }
        nuevo_tablero = self.env['tablero_avances'].create(tablero_avances_values)

        # Buscar ejecución creada previamente con el mismo nombre
        ejecucion = self.env['ejecucion'].search([
            ('name', '=', self.new_name),
            ('informacion_base_id', '=', self.informacion_base_agregar_id.id)
        ], limit=1)

        if ejecucion:
            ejecucion.write({'tablero_id': nuevo_tablero.id})
            nuevo_tablero.write({'ejecucion_J_id': ejecucion.id})
            _logger.info(f"✔ Tablero vinculado a ejecución: {ejecucion.name}")
        else:
            _logger.warning(f"⚠ No se encontró ejecución para: {self.new_name}")

        _logger.info(
            f"✅ Se creó tablero_avances: ID={nuevo_tablero.id}, Name={nuevo_tablero.name}, Ejecutado desde MCB: {self.new_name}")

    def name_get(self):
        result = []
        for record in self:
            name = record.new_name  # Reemplaza con el campo correcto de informacion_base
            result.append((record.id, name))
        return result

    actividad_ids = fields.One2many('tablero_avances', 'ejecucion_id', string='Tablero control')


    new_mcb_ids = fields.One2many('new_mcb', 'mcb_id', string="Proyectos Nuevos")

    new_total_costos_boolean = fields.Boolean(related="new_mcb_ids.new_total_costos_boolean", store=True)

    def ir_a_NUEVA_MCB(self):
        self.ensure_one()

        # Buscar si ya existe una new_mcb relacionada a esta MCB
        existing_record = self.env['new_mcb'].search([('mcb_id', '=', self.id)], limit=1)

        # Si no existe, se crea automáticamente
        if not existing_record:
            if self._es_consulta_coordinacion_financiera():
                return {
                    'name': 'Nueva MCB',
                    'type': 'ir.actions.act_window',
                    'res_model': 'new_mcb',
                    'view_mode': 'tree,form',
                    'target': 'current',
                    'domain': [('mcb_id', '=', self.id)],
                    'context': {'default_mcb_id': self.id},
                }
            existing_record = self.env['new_mcb'].create({
                'name': self.name,
                'mcb_id': self.id,
            })

            # Relacionar tablero si existe
            tablero = self.env['tablero_avances'].search([('ejecucion_id', '=', self.id)], limit=1)
            if tablero:
                tablero.new_ejecucion_id = existing_record.id

        # Crear otrosís si hacen falta
        if not self._es_consulta_coordinacion_financiera():
            self.env['new_mcb_otrosi'].crear_otrosis_faltantes()

        # Abrir formulario de la new_mcb correspondiente
        return {
            'name': 'Nueva MCB',
            'type': 'ir.actions.act_window',
            'res_model': 'new_mcb',
            'view_mode': 'form',
            'res_id': existing_record.id,
            'target': 'current',
            'context': {
                'default_mcb_id': self.id,
            },
        }

    solicitante = fields.Many2one('res.users', string='Creador del proyecto', readonly=True, related="contrataciones_id.create_uid")
    ficharesumen_id = fields.Many2one('ficharesumen', string='Ficha resumen')
    carpeta_principal_id = fields.Many2one('carpeta_principal', string='Carpeta Principal')


    # Nueva sección para ué el Director asigne el sobrecosto aprobado manualmente
    aprueba_director = fields.Boolean(string='Aprueba sobrecosto', tracking=True)
    cantidad_aprobada = fields.Float(string='Asignar sobrecosto', tracking=True)

    total_costos_produccion_N = fields.Monetary(compute='_compute_calculo_costo_producccion')

    @api.depends('Admon_12', 'costos_financieros_total', 'costos_total_valor', 'valor_proyecto_convenio3',
                 'cantidad_aprobada')
    def _compute_calculo_costo_producccion(self):
        for record in self:
            record.total_costos_produccion_N = (record.valor_proyecto_convenio3 - (record.costos_total_valor + record.Admon_12 + record.costos_financieros_total) + record.cantidad_aprobada)



    is_editable_mcb = fields.Boolean(string='Es editable', related="contrataciones_id.is_editable", default=False)

    @api.onchange('is_editable_mcb')
    def _onchange_is_editable_mcb(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass

        # Relación inversa con 'new_mcb'


    
    def ir_a_tablero_carpeta_p(self):
        from odoo import fields

        # 1. Definimos el nombre
        project_name = self.new_name or self.name
        nombre_carpeta = f"Carpeta principal del proyecto: {project_name}"

        # 2. Actualizamos la fecha de modificación del registro actual
        self._marcar_apertura_si_permitido()

        # Obtenemos el ID numérico
        contratacion_id = self.contrataciones_id.id

        # 3. Busca si ya existe
        ejecucion_existente = self.env['carpeta_principal'].search([
            ('contrataciones_carpeta_id', '=', contratacion_id),
        ], limit=1)

        carpeta_principal_record = None

        if ejecucion_existente:
            # CASO 1: Ya existe
            carpeta_principal_record = ejecucion_existente
            # ---> ACTUALIZACIÓN FORZADA AQUÍ <---
            # Actualizamos el new_name aunque el registro ya exista
            if not self._es_consulta_coordinacion_financiera():
                carpeta_principal_record.write({'new_name': nombre_carpeta})
        else:
            if self._es_consulta_coordinacion_financiera():
                return {
                    'name': nombre_carpeta,
                    'view_mode': 'tree,form',
                    'res_model': 'carpeta_principal',
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                    'domain': [('contrataciones_carpeta_id', '=', contratacion_id)],
                    'context': {'default_contrataciones_carpeta_id': contratacion_id},
                }
            # CASO 2: No existe, crear uno nuevo
            values = {
                'name': project_name,
                'new_name': nombre_carpeta, # Aquí ya se asigna al crear
                'contrataciones_carpeta_id': contratacion_id,
            }
            carpeta_principal_record = self.env['carpeta_principal'].create(values)

        # 4. Solo si el campo de la carpeta (stage3_id) está vacío, la buscamos.
        if carpeta_principal_record and not carpeta_principal_record.stage3_id and not self._es_consulta_coordinacion_financiera():
            drive_stage = carpeta_principal_record._buscar_carpeta()
            
            if drive_stage:
                carpeta_principal_record.write({'stage3_id': drive_stage.id})

        # 5. Devolver la acción
        return {
            'name': nombre_carpeta,
            'view_mode': 'form',
            'res_model': 'carpeta_principal',
            'res_id': carpeta_principal_record.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
        }
        

    actividad_ids11 = fields.One2many('vistas_director', 'ejecucion_id11', string='Tablero de control')



    informacion_base_id = fields.Many2one('informacion_base', string='Informacion base')
    contrataciones_id = fields.Many2one('contratacion', string='Contratacion')

    #Sam 20-03-2024 y 10/05/2024

    suma_valor_total_contrato3 = fields.Monetary(related='contrataciones_id.suma_valor_total_contrato2')
    apodo = fields.Char(related='informacion_base_agregar_id.nombre_proceso_proyecto')


    def Abrir_mcb(self):
        # Establecer el valor de apodo a 'MCB'
        if not self._es_consulta_coordinacion_financiera():
            self.new_name = 'MCB'

        # Devolver la acción para abrir el formulario del registro actual
        action = {
            'name': 'MCB',
            'type': 'ir.actions.act_window',
            'res_model': 'mcb',
            'view_mode': 'form',
            'res_id': self.id,  # ID del registro actual
            'target': 'current',
        }

        return action

    valor_aportes = fields.Monetary(string="Valor total aportes", compute="_compute_valor_aportes")


    @api.depends('suma_adicion_otro_si2', 'valor_proyecto_convenio2', 'contrapartida')
    def _compute_valor_aportes(self):
        for record in self:
            record.valor_aportes = (record.suma_adicion_otro_si2 + record.valor_proyecto_convenio2 + record.contrapartida)


    valor_total2 = fields.Float(
        string="Valor total1",
        currency='COP',
        ##compute='_compute_valor_total2'
    )

    valor_total2_final = fields.Float(
        string="Valor total2",
        currency='COP',
        compute='_compute_valor_total2_final'
    )

    @api.depends('suma_adicion_otro_si2', 'valor_proyecto_convenio2')
    def _compute_valor_total2_final(self):
        for record in self:
            record.valor_total2_final = (record.suma_adicion_otro_si2 + record.valor_proyecto_convenio2)

    valor_total2_restado_contrapartida = fields.Float(compute='_compute_resta_contrapartida')

    @api.depends('valor_total2_final', 'contrapartida')
    def _compute_resta_contrapartida(self):
        for record in self:
            record.valor_total2_restado_contrapartida = (record.valor_total2_final - record.contrapartida)

    ##Global
    valor_total2_final2 = fields.Float(
        string="Valor total3",
        currency='COP',
        compute='_compute_todos_los_valores_total22'
    )

    @api.depends('valor_total2_final')
    def _compute_todos_los_valores_total22(self):
        # Obtener todos los registros en el modelo 'mcb'
        registros_mcb = self.env['mcb'].search([])

        # Calcular la suma de los valores de 'valor_total2_final'
        suma_valores_total2_final = sum(registro_mcb.valor_total2_final for registro_mcb in registros_mcb)

        # Actualizar el campo calculado con asignación directa, sin disparar permisos de escritura.
        for record in self:
            record.valor_total2_final2 = suma_valores_total2_final

    contrapartida4 = fields.Monetary(string="Contrapartida FUV", tracking=True)
    valor_proyecto_convenio3 = fields.Float(string="Valor proyecto")

    valor_proyecto_convenio3_boolean = fields.Boolean(compute='_compute_valor_proyecto_convenio3_boolean', store=True)
    @api.depends('valor_proyecto_convenio3')
    def _compute_valor_proyecto_convenio3_boolean(self):
        for record in self:
            record.valor_proyecto_convenio3_boolean = bool(record.valor_proyecto_convenio3)




    contrapartida = fields.Monetary(string="Contrapartida FUV", tracking=True, compute="_compute_suma_contrapartidas")

    contrapartida_otrosi = fields.Monetary(string="Contrapartida FUV", related='mcb_ids.contrapartida22')

    @api.depends('contrapartida4', 'contrapartida_otrosi')
    def _compute_suma_contrapartidas(self):
        for record in self:
            record.contrapartida = record.contrapartida_otrosi + record.contrapartida4





    todos_los_valores_total2 = fields.Float(
        string='',
        compute='_compute_todos_los_valores_total2',
    )

    @api.depends('valor_total2')
    def _compute_todos_los_valores_total2(self):
        # Obtener todos los registros en el modelo 'mcb'
        registros_mcb = self.env['mcb'].search([])

        # Calcular la suma de los valores de 'Valor total 2'
        suma_valores_total2 = sum(registro_mcb.valor_total2 for registro_mcb in registros_mcb)

        # Actualizar el campo calculado con asignación directa, sin disparar permisos de escritura.
        for record in self:
            record.todos_los_valores_total2 = suma_valores_total2

    @api.model
    def calcular_y_mostrar_valores_total2(self):
        # Esta función puede seguir siendo utilizada desde otros lugares si es necesario
        registros_mcb = self.env['mcb'].search([])
        suma_valores_total2 = sum(registro_mcb.valor_total2 for registro_mcb in registros_mcb)
        print("Suma de todos los valores de 'Valor total':", suma_valores_total2)




    fch_creacion = fields.Datetime(string='Fecha creación', copy=False, default=lambda self: fields.Datetime.now(),
                                   tracking=True)
    write_date = fields.Datetime(string='Fecha de Modificación', readonly=True, tracking=True)
    gestion_seleccionada = fields.Boolean(string='Gestión Seleccionada', compute='_compute_gestion_seleccionada',
                                          store=True)

    @api.depends('informacion_base_id')
    def _compute_gestion_seleccionada(self):
        for record in self:
            record.gestion_seleccionada = bool(record.informacion_base_id)

    def Guardar(self):
        self.ensure_one()
        self.write({'name': self.new_name})
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    name = fields.Char(string="Nombre", readonly=True, related='apodo')
    objecto_proyecto = fields.Char(string="Objeto del proyecto", tracking=True)
    entidad_contratante = fields.Char(string="Entidad contratante", tracking=True)
    centro_costo = fields.Char(string="Centro de costo", tracking=True)

    objeto_proyecto = fields.Char(string="Objeto del proyecto", related='informacion_base_agregar_id.objeto_proyecto')

    numero_contrato = fields.Char(string="Número de contrato", related='informacion_base_agregar_id.numero_contrato',
                                  tracking=True)
    numero_de_poliza = fields.Char(string="Número de póliza", tracking=True, readonly=False)

    ##VALORES DE RESUMEN
    entidad_contratanteR = fields.Char(string="Entidad contratante relacionada", related="entidad_contratante",
                                       store=False)
    centro_costoR = fields.Char(string="Centro de costo relacionado", related="centro_costo", store=False)
    numero_de_polizaR = fields.Char(string="Número de póliza", related="numero_de_poliza", store=False)

    fecha_inicio = fields.Date(string="Fecha de inicio", related='informacion_base_agregar_id.fecha_acta_inicio',
                               tracking=True)

    fecha_finalizacion2 = fields.Date(string="Fecha de finalización", related='informacion_base_agregar_id.fecha_acta_fin')

    fecha_finalizacion = fields.Date(string="Fecha de finalización", compute='_compute_fecha_finalizacion', store=True)

    fecha_finalizacion_new = fields.Date(string="Fecha de finalización", related='new_mcb_ids.fecha_finalizacion_otrosi', store=True)
    # Dependencias: Escuchamos cambios en la lista de hijos, y en las fechas de los nietos
    @api.depends('new_mcb_ids.otrosi_ids_new.fecha_finalizacion', 'mcb_ids.fecha_finalizacion')
    def _compute_fecha_finalizacion(self):
        for mcb in self:
            # ------------------------------------------------------------------
            # ESTRATEGIA: Buscar directamente en los NIETOS (new_mcb_otrosi)
            # saltándonos el campo 'related' intermedio que suele fallar.
            # ------------------------------------------------------------------
            
            # 1. Recolectar fechas de todos los 'otrosi_ids_new' de todos los 'new_mcb_ids'
            # Usamos mapped para ir: MCB -> NewMCB -> NewMCBOtrosi -> fecha
            fechas_nuevas = mcb.new_mcb_ids.mapped('otrosi_ids_new.fecha_finalizacion')
            
            # Limpiamos (quitamos False/None)
            fechas_nuevas_validas = [f for f in fechas_nuevas if f]

            print(f"DEBUG MCB {mcb.id}: Fechas encontradas en nietos: {fechas_nuevas_validas}")

            # CASO 1: Hay fechas en los nuevos otrosís
            if fechas_nuevas_validas:
                mcb.fecha_finalizacion = max(fechas_nuevas_validas)
                continue 

            # CASO 2: No hay nuevos, miramos los históricos (mcb_ids)
            fechas_viejas = mcb.mcb_ids.mapped('fecha_finalizacion')
            fechas_viejas_validas = [f for f in fechas_viejas if f]

            if fechas_viejas_validas:
                mcb.fecha_finalizacion = max(fechas_viejas_validas)
            
            # CASO 3: Nada de nada, fecha original
            else:
                mcb.fecha_finalizacion = mcb.fecha_finalizacion2
                

    MODALIDAD = fields.Selection([
        ('CI', 'Carta de instrucción (CI)'),
        ('CC', 'Contrato de consultoría (CC)'),
        ('CO', 'Contrato de obra (CO)'),
        ('CIA', 'Contrato interadministrativo (CIA)'),
        ('CLP', 'Contrato de licitaciones públicas (CLP)'),
        ('CPS', 'Contrato de prestación de servicios (CPS)'),
        ('OC', 'Orden de compra (OC)'),
        ('OS', 'Orden de servicios (OS)'),
        ('CS', 'Contrato de suministros (CS)'),
        ('CA', 'Convenio de asociación (CA)'),
        ('CCF', 'Convenio de cofinanciación (CCF)'),
        ('CEC', 'Convenio especial de cooperación (CEC)'),
        ('CIA', 'Convenio interadministrativo (CIA)')
    ], string="Modalidad", related='informacion_base_agregar_id.modalidad_contratacion', tracking=True)

    modalidad_id = fields.Many2one(
       'modalidad_contratacion',
        string='Modalidad de Contratación',
        tracking=True,
        related='informacion_base_agregar_id.modalidad_id'
    )

    title = fields.Char(string="MATRIZ GENERAL DE COSTO BENEFICIO")
    subtitle = fields.Char(string="F. Emisión: 2012-09-10  Versión: 6\n\nFU-F-GPP-019")

    def Guardar1(self):
        self.ensure_one()
        self.write({'name': self.new_name})
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    # Columna 1 izquierda bloque 3
    costos_tributarios = fields.Char(string="Costos tributarios", tracking=True)
    impuesto = fields.Char(string="Impuestos", tracking=True)
    tasa = fields.Char(string="Tasa %", tracking=True)
    valor = fields.Char(string="Valor", tracking=True)
    # Columna 1 izquierda bloque 4
    participacion = fields.Char(string="Participacion", tracking=True)

    # Columna 1 izquierda bloque 5
    facturacion = fields.Char(string="Programaciòn de facturaciòn", tracking=True)
    fecha_estimada = fields.Char(string="Fecha estimada", tracking=True)
    porcentaje_ejecucion = fields.Char(string="% Ejecuciòn", tracking=True)
    valor_facturar = fields.Char(string="Valor a facturar ", tracking=True)

    campo_1F = fields.Date(string="Campo 1", tracking=True)
    campo_1E = fields.Char(string="Campo 1", tracking=True)
    campo_2F = fields.Date(string="Campo 2", tracking=True)
    campo_2E = fields.Char(string="Campo 2", tracking=True)
    campo_3F = fields.Date(string="Campo 3", tracking=True)
    campo_3E = fields.Char(string="Campo 3", tracking=True)
    campo_4f = fields.Date(string="Campo 4", tracking=True)
    campo_4E = fields.Char(string="Campo 4", tracking=True)
    campo_5F = fields.Date(string="Campo 5", tracking=True)
    campo_5E = fields.Char(string="Campo 5", tracking=True)

    # Columna 2 derecha bloque1
    b_costos_administracion = fields.Char(string="B. Costos administración", tracking=True)
    Admon_C_12 = fields.Char(string="Admon.12%", tracking=True)
    c_costos_financieros = fields.Char(string="C. Costos financieros", tracking=True)

    # Columna 2 derecha bloque3
    valor_proyecto_convenio = fields.Char(string="VALOR PROYECTO / CONVENIO", tracking=True)

    valor_proyecto_convenio2 = fields.Float(string="Valor de contrato/Convenio resumen ", tracking=True,
                                            related="valor_proyecto_convenio3")

    # texto
    Revisado = fields.Char(string="Revisado direccion unidad de proyectos y/o", tracking=True)
    Lider = fields.Char(string="Lider gestor - Proyectos de ingeniería e infraestructura", tracking=True)
    # notas
    notas = fields.Char(string="Notas para el diligenciamiento:", tracking=True)
    mensaje_1 = fields.Text(
        default="Para la estimación de la administración, tener en cuenta el porcentaje definido conforme a los "
                "indicadores de gestión del año vigente. Este valor será neto a negociar fuera de impuestos y "
                "gastos asociados.")
    mensaje_2 = fields.Text(
        default="Tener en cuenta para el cálculo del IVA la procedencia del proyecto (Entidad Contratante), el tipo de "
                "contrato y su modalidad de contratación. La articulación con el área financiera para resolver "
                "particularidades.")
    mensaje_3 = fields.Text(
        default="El descuento por estampilla depende del tipo de contrato y del ente territorial. Sí es por Carta de "
                "Instrucción o directamente con el sector público, consultar o solicitar la ficha técnica del proyecto "
                "ante la entidad contratante y entregar a financiera.")
    mensaje_4 = fields.Char(string="Observaciones Financiera", tracking=True)
    mensaje_4_R = fields.Char(string="Observaciones Financiera", related='mensaje_4')

    # Bloque de costos tributarios
    IVA_ADMON = fields.Char(string="IVA ADMON", tracking=True)
    RETEICA = fields.Char(string="RETEICA", tracking=True)
    Descuentos_Estampillas = fields.Char(string="Descuentos en estampillas", tracking=True)
    cuatro_X1000 = fields.Char(string="4X1000", tracking=True)
    ICA_AVISOS_Y_TABLEROS = fields.Char(string="ICA AVISOS Y TABLEROS", tracking=True)
    costos_tributarios_total = fields.Char(string="Costos tributarios total", tracking=True)
    campo_6 = fields.Char(string="Campo 6")
    campo_6_valor = fields.Char(string="Campo 6")
    currency_id = fields.Many2one('res.currency', string='Moneda')

    # Columna 1 izquierda bloque 1##############################################################################
    # Columna 1 izquierda bloque 1 antigua
    a_costos_produccion = fields.Char(string="A. Costos de producción")
    produccion_1 = fields.Char(string="1")
    produccion_1n = fields.Monetary(string="1")
    produccion_2 = fields.Char(string="2")
    produccion_2n = fields.Char(string="2")
    produccion_3 = fields.Char(string="3")
    produccion_3n = fields.Char(string="3")
    produccion_4 = fields.Char(string="4")
    produccion_4n = fields.Char(string="4")
    produccion_5 = fields.Char(string="5")
    produccion_5n = fields.Char(string="5")
    produccion_6 = fields.Char(string="6")
    produccion_6n = fields.Char(string="6")
    produccion_7 = fields.Char(string="7")
    produccion_7n = fields.Char(string="7")
    produccion_8 = fields.Char(string="8")
    produccion_8n = fields.Char(string="8")
    produccion_9 = fields.Char(string="9")
    produccion_9n = fields.Char(string="9")
    total_costos_produccion = fields.Float(string="Total costos producción")

    # Columna 1 izquierda bloque 1 NUEVA
    a_costos_produccion_N = fields.Char(string="A. Costos de producción", tracking=True)
    # Definición del campo con un valor fijo
    produccion_1_N = fields.Char(string="Producción", compute='_compute_produccion_1_N', store=True, readonly=True)

    @api.depends()
    def _compute_produccion_1_N(self):
        for record in self:
            record.produccion_1_N = "1"

    produccion_2_N = fields.Char(string="2", tracking=True)
    produccion_3_N = fields.Char(string="3", tracking=True)
    produccion_4_N = fields.Char(string="4", tracking=True)
    produccion_5_N = fields.Char(string="5", tracking=True)
    produccion_6_N = fields.Char(string="6", tracking=True)
    produccion_7_N = fields.Char(string="7", tracking=True)
    produccion_8_N = fields.Char(string="8", tracking=True)
    produccion_9_N = fields.Char(string="9", tracking=True)
    ##Resumen
    produccion_1_NR = fields.Char(related="produccion_1_N")
    produccion_2_NR = fields.Char(related="produccion_2_N")
    produccion_3_NR = fields.Char(related="produccion_3_N")
    produccion_4_NR = fields.Char(related="produccion_4_N")
    produccion_5_NR = fields.Char(related="produccion_5_N")
    produccion_6_NR = fields.Char(related="produccion_6_N")
    produccion_7_NR = fields.Char(related="produccion_7_N")
    produccion_8_NR = fields.Char(related="produccion_8_N")
    produccion_9_NR = fields.Char(related="produccion_9_N")

    ##Funciones para recibir valor de otrsi############################################################################
    costos_financieros_total = fields.Monetary(string="Costos financieros total", tracking=True,
                                               compute='_compute_suma_costos_financiero_total')

    @api.depends('cheques', 'transferencias', 'intereses', 'fiducia', 'polizas', 'otros_costos_financieros')
    def _compute_suma_costos_financiero_total(self):
        for record in self:
            total_suma = sum([
                record.cheques,
                record.transferencias,
                record.intereses,
                record.fiducia,
                record.polizas,
                record.otros_costos_financieros
            ])
            record.costos_financieros_total = total_suma


    cheques = fields.Monetary(string="Cheques", tracking=True)
    cheques2 = fields.Monetary(string="Cheques", tracking=True, related='mcb_ids.cheques2')
    suma_cheques = fields.Monetary(string="Suma de cheques", compute='_compute_suma_cheques')

    transferencias = fields.Monetary(string="Transferencias", tracking=True)
    transferencias2 = fields.Monetary(string="Transferencias", tracking=True, related='mcb_ids.transferencias2')
    suma_transferencias = fields.Monetary(string="Suma de Contrapartidas", compute='_compute_suma_transferencias')

    intereses = fields.Monetary(string="Intereses", tracking=True)
    intereses2 = fields.Monetary(string="Intereses", tracking=True, related='mcb_ids.intereses2')
    suma_intereses = fields.Monetary(string="Suma de Contrapartidas", compute='_compute_suma_intereses')

    fiducia = fields.Monetary(string="Fiducia", tracking=True)
    fiducia2 = fields.Monetary(string="Fiducia", tracking=True, related='mcb_ids.fiducia2')
    suma_fiducia = fields.Monetary(string="Suma de Contrapartidas", compute='_compute_suma_fiducia')

    polizas = fields.Monetary(string="Pólizas + Póliza sup", readonly=False, tracking=True)
    polizas2 = fields.Monetary(string="Pólizas + Póliza sup", readonly=False, tracking=True,
                               related='mcb_ids.polizas2')
    suma_polizas = fields.Monetary(string="Suma de Contrapartidas", compute='_compute_suma_polizas')

    otros_costos_financieros = fields.Monetary(string="Otros C. financieros", tracking=True)
    otros_costos_financieros2 = fields.Monetary(string="Otros C. financieros", tracking=True,
                                                related='mcb_ids.otros_costos_financieros2')
    suma_otros_costos_financieros = fields.Monetary(string="Suma de Contrapartidas",
                                                    compute='_compute_suma_otros_costos_financieros')

    costos_financieros_total2 = fields.Monetary(string="Costos financieros total", tracking=True,
                                                related='mcb_ids.costos_financieros_total2')
    suma_costos_financieros_total = fields.Monetary(string="Suma de Contrapartidas",
                                                    compute='_compute_suma_costos_financieros_total')
    ##

    suma_contrapartidas = fields.Monetary(string="Suma de Contrapartidas", compute='_compute_suma_contrapartidas')

    adicion_otro_si = fields.Monetary(string="Adicion (OTRO SI)", readonly=False, tracking=True, invisible=False)
    adicion_otro_si2 = fields.Monetary(related='mcb_ids.adicion_otro_si2')
    suma_adicion_otro_si2 = fields.Monetary(string="Suma de Adicion Otro Si 2",
                                            compute='_compute_suma_adicion_otro_si2')
    #########################################################################################

    IVA_ADMON_vaor2 = fields.Monetary(tracking=True, related='mcb_ids.IVA_ADMON_vaor2')
    suma_IVA_ADMON_vaor = fields.Monetary(compute='_compute_suma_IVA_ADMON_vaor')

    RETEICA_valor2 = fields.Monetary(tracking=True, related='mcb_ids.RETEICA_valor2')
    suma_RETEICA_valor = fields.Monetary(compute='_compute_suma_RETEICA_valor')
    Descuentos_Estampillas_valor2 = fields.Monetary(tracking=True,
                                                    related='mcb_ids.Descuentos_Estampillas_valor2')
    suma_Descuentos_Estampillas_valor = fields.Monetary(compute='_compute_suma_Descuentos_Estampillas_valor')

    cuatro_X1000_valor2 = fields.Monetary(tracking=True, related='mcb_ids.cuatro_X1000_valor2')
    suma_cuatro_X1000_valor = fields.Monetary(compute='_compute_suma_cuatro_X1000_valor')


    ICA_AVISOS_Y_TABLEROS_valor2 = fields.Monetary(tracking=True,
                                                   related='mcb_ids.ICA_AVISOS_Y_TABLEROS_valor2')
    suma_ICA_AVISOS_Y_TABLEROS_valor = fields.Monetary(compute='_compute_suma_ICA_AVISOS_Y_TABLEROS_valor')



    RETEICA_valor = fields.Monetary(string="RETEICA", tracking=True)
    IVA_ADMON_vaor = fields.Monetary(string="IVA ADMON", tracking=True)
    cuatro_X1000_valor = fields.Monetary(string="4X1000", tracking=True)
    Descuentos_Estampillas_valor = fields.Monetary(string="Descuentos en estampillas", tracking=True)
    ICA_AVISOS_Y_TABLEROS_valor = fields.Monetary(string="ICA AVISOS Y TABLEROS", tracking=True)


    suma_costos_tributarios_total_valor = fields.Monetary(compute='_compute_suma_costos_tributarios_total_valor')
    ###costos_tributarios_total_valor
    costos_tributarios_total_valor2 = fields.Monetary(tracking=True,
                                                      related='mcb_ids.costos_tributarios_total_valor2')

    @api.depends('costos_total_valor', 'costos_tributarios_total_valor2')
    def _compute_suma_costos_tributarios_total_valor(self):
        for record in self:
            record.suma_costos_tributarios_total_valor = (
                    record.costos_total_valor + record.costos_tributarios_total_valor2)

    costos_tributarios_total_valor = fields.Monetary(related="mcb.costos_total_valor")
    costos_total_valor = fields.Monetary(compute='_compute_suma_costos_tributarios_total1')

    @api.depends('RETEICA_valor', 'IVA_ADMON_vaor', 'cuatro_X1000_valor', 'Descuentos_Estampillas_valor',
                 'ICA_AVISOS_Y_TABLEROS_valor')
    def _compute_suma_costos_tributarios_total1(self):
        for record in self:
            total_suma = sum([
                record.RETEICA_valor,
                record.IVA_ADMON_vaor,
                record.cuatro_X1000_valor,
                record.Descuentos_Estampillas_valor,
                record.ICA_AVISOS_Y_TABLEROS_valor
            ])
            record.costos_total_valor = total_suma






    Participación_UV = fields.Monetary(string="Participación UV/FAC, ESC", tracking=True)
    Participación_UV2 = fields.Monetary(string="Participación UV/FAC, ESC", tracking=True,
                                        related='mcb_ids.Participación_UV2')

    suma_Participación_UV = fields.Monetary(compute='_compute_suma_Participación_UV')

    fuv_programas = fields.Monetary(string="FUV - Programas sociales", tracking=True)
    fuv_programas2 = fields.Monetary(string="Participación UV/FAC, ESC", tracking=True,
                                     related='mcb_ids.fuv_programas2')

    suma_fuv_programas = fields.Monetary(compute='_compute_suma_fuv_programas')

    costo_total_2 = fields.Float(string="Costo total 2", tracking=True, compute='_compute_suma_abc')

    @api.depends('total_costos_produccion_N', 'Admon_12', 'costos_financieros_total')
    def _compute_suma_abc(self):
        for record in self:
            total_suma = sum([
                record.total_costos_produccion_N,
                record.Admon_12,
                record.costos_financieros_total,
            ])
            record.costo_total_2 = total_suma

    costo_total_22 = fields.Float(string="Costo total 2", tracking=True, related='mcb_ids.costo_total_22')
    suma_costo_total_2 = fields.Float(string="Costo total 2", tracking=True, compute='_compute_suma_costo_total_2')

    costo_tributario2 = fields.Monetary(string="Costo tributario 2", tracking=True)
    costo_tributario22 = fields.Monetary(string="Costo tributario 2", tracking=True, related='mcb_ids.costo_tributario22')
    suma_costo_tributario2 = fields.Monetary(string="Costo tributario 2", tracking=True, compute='_compute_suma_costo_tributario2')

    total_a_facturar = fields.Float(string="Total a facturar", tracking=True)
    total_a_facturar2 = fields.Float(string="Total a facturar", tracking=True, related='mcb_ids.total_a_facturar2')
    suma_total_a_facturar = fields.Float(string="Total a facturar", tracking=True, compute='_compute_suma_total_a_facturar')

    excedente_1 = fields.Monetary(string="Excedente", tracking=True)
    excedente_12 = fields.Monetary(string="Excedente", tracking=True, related='mcb_ids.excedente_12')
    suma_excedente_1 = fields.Monetary(string="Excedente", tracking=True, compute='_compute_suma_excedente_1')

    campo_1V = fields.Monetary(string="Campo 1", tracking=True)
    campo_1V2 = fields.Monetary(string="Campo 1", tracking=True, related='mcb_ids.campo_1V2')
    suma_campo_1V = fields.Monetary(string="Campo 1", tracking=True, compute='_compute_suma_campo_1V')

    campo_2V = fields.Monetary(string="Campo 2", tracking=True)
    campo_2V2 = fields.Monetary(string="Campo 2", tracking=True, related='mcb_ids.campo_2V2')
    suma_campo_2V = fields.Monetary(string="Campo 1", tracking=True, compute='_compute_suma_campo_2V')

    campo_3V = fields.Monetary(string="Campo 3", tracking=True)
    campo_3V2 = fields.Monetary(string="Campo 3", tracking=True, related='mcb_ids.campo_3V2')
    suma_campo_3V = fields.Monetary(string="Campo 1", tracking=True, compute='_compute_suma_campo_3V')

    campo_4V = fields.Monetary(string="Campo 4", tracking=True)
    campo_4V2 = fields.Monetary(string="Campo 4", tracking=True, related='mcb_ids.campo_4V2')
    suma_campo_4V = fields.Monetary(string="Campo 4", tracking=True, compute='_compute_suma_campo_4V')

    campo_5V = fields.Float(string="Campo 5V", currency_field='currency_id', currency='COP', tracking=True)
    campo_5V2 = fields.Float(string="Campo 5V", currency_field='currency_id', related='mcb_ids.campo_5V2')
    suma_campo_5V = fields.Float(string="Campo 5V", tracking=True, compute='_compute_suma_campo_5V')

    Admon_122 = fields.Monetary(related='mcb_ids.Admon_122')
    suma_Admon_12 = fields.Monetary(tracking=True, compute='_compute_suma_Admon_12')

    produccion_1n_N2 = fields.Monetary(related='mcb_ids.produccion_1n_N42')
    suma_produccion_1n_ = fields.Monetary(compute='_compute_suma_produccion_1n_N')

    produccion_2n_N = fields.Monetary(string="2", tracking=True)
    produccion_2n_N2 = fields.Monetary(related='mcb_ids.produccion_2n_N42')
    suma_produccion_2n_N = fields.Monetary(compute='_compute_suma_produccion_2n_N')

    produccion_3n_N = fields.Monetary(string="3", tracking=True)
    produccion_3n_N2 = fields.Monetary(related='mcb_ids.produccion_3n_N42')
    suma_produccion_3n_N = fields.Monetary(compute='_compute_suma_produccion_3n_N')

    produccion_4n_N = fields.Monetary(string="4", tracking=True)
    produccion_4n_N2 = fields.Monetary(related='mcb_ids.produccion_4n_N42')
    suma_produccion_4n_N = fields.Monetary(compute='_compute_suma_produccion_4n_N')

    produccion_5n_N = fields.Monetary(string="5", tracking=True)
    produccion_5n_N2 = fields.Monetary(related='mcb_ids.produccion_5n_N42')
    suma_produccion_5n_N = fields.Monetary(compute='_compute_suma_produccion_5n_N')

    produccion_6n_N = fields.Monetary(string="6", tracking=True)
    produccion_6n_N2 = fields.Monetary(related='mcb_ids.produccion_6n_N42')
    suma_produccion_6n_N = fields.Monetary(compute='_compute_suma_produccion_6n_N')

    produccion_7n_N = fields.Monetary(string="7", tracking=True)
    produccion_7n_N2 = fields.Monetary(related='mcb_ids.produccion_7n_N42')
    suma_produccion_7n_N = fields.Monetary(compute='_compute_suma_produccion_7n_N')

    produccion_8n_N = fields.Monetary(string="8", tracking=True)
    produccion_8n_N2 = fields.Monetary(related='mcb_ids.produccion_8n_N42')
    suma_produccion_8n_N = fields.Monetary(compute='_compute_suma_produccion_8n_N')

    produccion_9n_N = fields.Monetary(string="9", tracking=True)
    produccion_9n_N2 = fields.Monetary(related='mcb_ids.produccion_9n_N42')
    suma_produccion_9n_N = fields.Monetary(compute='_compute_suma_produccion_9n_N')

    # Funciones de cálculo
    @api.depends('total_costos_produccion_N', 'produccion_suma_2_9')
    def _compute_resta_produccion(self):
        for record in self:
            record.produccion_1n_N = record.total_costos_produccion_N - record.produccion_suma_2_9

    @api.depends('produccion_2n_N', 'produccion_3n_N', 'produccion_4n_N', 'produccion_5n_N', 'produccion_6n_N',
                 'produccion_7n_N', 'produccion_8n_N', 'produccion_9n_N')
    def _compute_suma_produccion(self):
        for record in self:
            total_suma = sum([
                record.produccion_2n_N,
                record.produccion_3n_N,
                record.produccion_4n_N,
                record.produccion_5n_N,
                record.produccion_6n_N,
                record.produccion_7n_N,
                record.produccion_8n_N,
                record.produccion_9n_N
            ])
            record.produccion_suma_2_9 = total_suma

    # Luego, definimos los campos dependientes de estas funciones de cálculo
    produccion_suma_2_9 = fields.Monetary(compute='_compute_suma_produccion')

    produccion_1n_N = fields.Monetary(string="1", tracking=True, compute='_compute_resta_produccion')


    # Nuevo calculo programado en la reunión del 26 de abril
    Admon_12 = fields.Float(string="Admon.12%")

    costos_tributarios_total_valor = fields.Monetary(string="Costos tributarios total", tracking=True)





    ###total_costos_produccion_N
    valor_sobre_costo = fields.Monetary(string="Sobre costo")
    suma_total_costos_produccion_N = fields.Monetary(compute='_compute_suma_total_costos_produccion_N')
    total_costos_produccion_N2 = fields.Monetary(related='mcb_ids.total_costos_produccion_N2')

    @api.depends('total_costos_produccion_N', 'total_costos_produccion_N2', 'valor_sobre_costo')
    def _compute_suma_total_costos_produccion_N(self):
        for record in self:
            record.suma_total_costos_produccion_N = ((record.total_costos_produccion_N2 + record.valor_sobre_costo)
                                                     + record.total_costos_produccion_N)


    valor_proyecto_convenio22 = fields.Float(related='mcb_ids.valor_proyecto_convenio22')

    ###produccion_1n_

    @api.depends('produccion_1n_N', 'produccion_1n_N2')
    def _compute_suma_produccion_1n_N(self):
        for record in self:
            record.suma_produccion_1n_ = record.produccion_1n_N + record.produccion_1n_N2

    ###produccion_2n_N

    @api.depends('produccion_2n_N', 'produccion_2n_N2')
    def _compute_suma_produccion_2n_N(self):
        for record in self:
            record.suma_produccion_2n_N = record.produccion_2n_N + record.produccion_2n_N2

    ###produccion_3n_N
    @api.depends('produccion_3n_N', 'produccion_3n_N2')
    def _compute_suma_produccion_3n_N(self):
        for record in self:
            record.suma_produccion_3n_N = record.produccion_3n_N + record.produccion_3n_N2

    ###produccion_4n_N
    @api.depends('produccion_4n_N', 'produccion_4n_N2')
    def _compute_suma_produccion_4n_N(self):
        for record in self:
            record.suma_produccion_4n_N = record.produccion_4n_N + record.produccion_4n_N2

    ###produccion_5n_N
    @api.depends('produccion_5n_N', 'produccion_5n_N2')
    def _compute_suma_produccion_5n_N(self):
        for record in self:
            record.suma_produccion_5n_N = record.produccion_5n_N + record.produccion_5n_N2

    ###produccion_6n_N
    @api.depends('produccion_6n_N', 'produccion_6n_N2')
    def _compute_suma_produccion_6n_N(self):
        for record in self:
            record.suma_produccion_6n_N = record.produccion_6n_N + record.produccion_6n_N2

    ###produccion_7n_N
    @api.depends('produccion_7n_N', 'produccion_7n_N2')
    def _compute_suma_produccion_7n_N(self):
        for record in self:
            record.suma_produccion_7n_N = record.produccion_7n_N + record.produccion_7n_N2

    ###produccion_8n_N
    @api.depends('produccion_8n_N', 'produccion_8n_N2')
    def _compute_suma_produccion_8n_N(self):
        for record in self:
            record.suma_produccion_8n_N = record.produccion_8n_N + record.produccion_8n_N2

    ###produccion_9n_N
    @api.depends('produccion_9n_N', 'produccion_9n_N2')
    def _compute_suma_produccion_9n_N(self):
        for record in self:
            record.suma_produccion_9n_N = record.produccion_9n_N + record.produccion_9n_N2

    ############################################################################################


    ###Admon_12
    @api.depends('Admon_12', 'Admon_122')
    def _compute_suma_Admon_12(self):
        for record in self:
            record.suma_Admon_12 = record.Admon_12 + record.Admon_122

    ###campo_1V
    @api.depends('campo_1V', 'campo_1V2')
    def _compute_suma_campo_1V(self):
        for record in self:
            record.suma_campo_1V = record.campo_1V + record.campo_1V2

    ###campo_2V
    @api.depends('campo_2V', 'campo_2V2')
    def _compute_suma_campo_2V(self):
        for record in self:
            record.suma_campo_2V = record.campo_2V + record.campo_2V2

    ###campo_3V
    @api.depends('campo_3V', 'campo_3V2')
    def _compute_suma_campo_3V(self):
        for record in self:
            record.suma_campo_3V = record.campo_3V + record.campo_3V2

    ###campo_4V
    @api.depends('campo_4V', 'campo_4V2')
    def _compute_suma_campo_4V(self):
        for record in self:
            record.suma_campo_4V = record.campo_4V + record.campo_4V2

    ###campo_5V
    @api.depends('campo_5V', 'campo_5V2')
    def _compute_suma_campo_5V(self):
        for record in self:
            record.suma_campo_5V = record.campo_5V + record.campo_5V2

    #######################################
    ###adicion_otro_si
    @api.depends('adicion_otro_si', 'adicion_otro_si2')
    def _compute_suma_adicion_otro_si2(self):
        for record in self:
            record.suma_adicion_otro_si2 = record.adicion_otro_si + record.adicion_otro_si2

    ###costos_financieros_total
    @api.depends('costos_financieros_total', 'costos_financieros_total2')
    def _compute_suma_costos_financieros_total(self):
        for record in self:
            record.suma_costos_financieros_total = record.costos_financieros_total + record.costos_financieros_total2

    ###otros_costos_financieros
    @api.depends('otros_costos_financieros', 'otros_costos_financieros2')
    def _compute_suma_otros_costos_financieros(self):
        for record in self:
            record.suma_otros_costos_financieros = record.otros_costos_financieros + record.otros_costos_financieros2

    ###polizas
    @api.depends('polizas', 'polizas2')
    def _compute_suma_polizas(self):
        for record in self:
            record.suma_polizas = record.polizas + record.polizas2

    ###fiducia
    @api.depends('fiducia', 'fiducia2')
    def _compute_suma_fiducia(self):
        for record in self:
            record.suma_fiducia = record.fiducia + record.fiducia2

    ###intereses
    @api.depends('intereses', 'intereses2')
    def _compute_suma_intereses(self):
        for record in self:
            record.suma_intereses = record.intereses + record.intereses2

    ###transferencias
    @api.depends('transferencias', 'transferencias2')
    def _compute_suma_transferencias(self):
        for record in self:
            record.suma_transferencias = record.transferencias + record.transferencias2

    ###cheques
    @api.depends('cheques', 'cheques2')
    def _compute_suma_cheques(self):
        for record in self:
            record.suma_cheques = record.cheques + record.cheques2

    ###IVA_ADMON_vaor
    @api.depends('IVA_ADMON_vaor', 'IVA_ADMON_vaor2')
    def _compute_suma_IVA_ADMON_vaor(self):
        for record in self:
            record.suma_IVA_ADMON_vaor = record.IVA_ADMON_vaor + record.IVA_ADMON_vaor2

    ###RETEICA_valor
    @api.depends('RETEICA_valor', 'RETEICA_valor2')  #viene de resumen = suma_RETEICA_valor. este viene de la normal = RETEICA_valor
    def _compute_suma_RETEICA_valor(self):
        for record in self:
            record.suma_RETEICA_valor = record.RETEICA_valor + record.RETEICA_valor2

    ###Descuentos_Estampillas_valor
    @api.depends('Descuentos_Estampillas_valor', 'Descuentos_Estampillas_valor2')
    def _compute_suma_Descuentos_Estampillas_valor(self):
        for record in self:
            record.suma_Descuentos_Estampillas_valor = (
                    record.Descuentos_Estampillas_valor + record.Descuentos_Estampillas_valor2)

    ###cuatro_X1000_valor
    @api.depends('cuatro_X1000_valor', 'cuatro_X1000_valor2')
    def _compute_suma_cuatro_X1000_valor(self):
        for record in self:
            record.suma_cuatro_X1000_valor = record.cuatro_X1000_valor + record.cuatro_X1000_valor2

    ###ICA_AVISOS_Y_TABLEROS_valor
    @api.depends('ICA_AVISOS_Y_TABLEROS_valor', 'ICA_AVISOS_Y_TABLEROS_valor2')
    def _compute_suma_ICA_AVISOS_Y_TABLEROS_valor(self):
        for record in self:
            record.suma_ICA_AVISOS_Y_TABLEROS_valor = (
                    record.ICA_AVISOS_Y_TABLEROS_valor + record.ICA_AVISOS_Y_TABLEROS_valor2)


    ###Participación_UV
    @api.depends('Participación_UV', 'Participación_UV2')
    def _compute_suma_Participación_UV(self):
        for record in self:
            record.suma_Participación_UV = (
                    record.Participación_UV + record.Participación_UV2)

    ###fuv_programas
    @api.depends('fuv_programas', 'fuv_programas2')
    def _compute_suma_fuv_programas(self):
        for record in self:
            record.suma_fuv_programas = (
                    record.fuv_programas + record.fuv_programas2)

    ###costo_total_2
    @api.depends('costo_total_2', 'costo_total_22')
    def _compute_suma_costo_total_2(self):
        for record in self:
            record.suma_costo_total_2 = (
                    record.costo_total_2 + record.costo_total_22)

    ###total_a_facturar
    @api.depends('total_a_facturar', 'total_a_facturar2')
    def _compute_suma_total_a_facturar(self):
        for record in self:
            record.suma_total_a_facturar = (
                    record.total_a_facturar + record.total_a_facturar2)

    ###costo_tributario2
    @api.depends('costo_tributario2', 'costo_tributario22')
    def _compute_suma_costo_tributario2(self):
        for record in self:
            record.suma_costo_tributario2 = (
                    record.costo_tributario2 + record.costo_tributario22)

    ###excedente_1
    @api.depends('excedente_1', 'excedente_12')
    def _compute_suma_excedente_1(self):
        for record in self:
            record.suma_excedente_1 = (
                    record.excedente_1 + record.excedente_12)

    ###################################################################################################################


class MCB2(models.Model):
    _name = "mcb2"
    mcb_id = fields.Many2one('mcb', string='Formulario mcb')
    is_editable_mcb2 = fields.Boolean(string='Es editable', related="mcb_id.is_editable_mcb", default=False)
    valor_total2 = fields.Float(string="Valor total", currency='COP', invisible=False)
    @api.onchange('is_editable_mcb2')
    def _onchange_is_editable_mcb2(self):
        pass

    otrosi_ids = fields.One2many('new_mcb_otrosi', 'mcb2_id', string="Otrosí relacionados")

    hiden = fields.Char(string='hiden')

    # Bloque de costos tributarios
    IVA_ADMON = fields.Char(string="IVA ADMON")
    RETEICA = fields.Char(string="RETEICA")
    Descuentos_Estampillas = fields.Char(string="Descuentos en estampillas")
    cuatro_X1000 = fields.Char(string="4X1000")
    ICA_AVISOS_Y_TABLEROS = fields.Char(string="ICA AVISOS Y TABLEROS")
    costos_tributarios_total = fields.Char(string="Costos tributarios total")
    campo_6 = fields.Char()
    campo_6_valor = fields.Char()
    currency_id = fields.Many2one('res.currency', string='Moneda')

    # Bloque Costo admin
    cheques = fields.Monetary(string="Cheques")
    cheques2 = fields.Monetary(string="Cheques", compute='_compute_suma_contrapartida')

    transferencias = fields.Monetary(string="Transferencias")
    transferencias2 = fields.Monetary(string="Transferencias", compute='_compute_suma_contrapartida')

    intereses = fields.Monetary(string="Intereses")
    intereses2 = fields.Monetary(string="Intereses", compute='_compute_suma_contrapartida')

    fiducia = fields.Monetary(string="Fiducia")
    fiducia2 = fields.Monetary(string="Fiducia", compute='_compute_suma_contrapartida')

    polizas = fields.Monetary(readonly=False)
    polizas2 = fields.Monetary(readonly=False, compute='_compute_suma_contrapartida')
    otros_costos_financieros = fields.Monetary(string="Otros C. financieros", )
    otros_costos_financieros2 = fields.Monetary(compute='_compute_suma_contrapartida')

    costos_financieros_total = fields.Monetary(string="Costos financieros total", tracking=True,
                                               compute='_compute_suma_costos_financiero_total')

    @api.depends('cheques', 'transferencias', 'intereses', 'fiducia', 'polizas', 'otros_costos_financieros')
    def _compute_suma_costos_financiero_total(self):
        for record in self:
            total_suma = sum([
                record.cheques,
                record.transferencias,
                record.intereses,
                record.fiducia,
                record.polizas,
                record.otros_costos_financieros
            ])
            record.costos_financieros_total = total_suma


    costos_financieros_total2 = fields.Monetary(compute='_compute_suma_contrapartida')

    IVA_ADMON_vaor = fields.Monetary(string="IVA ADMON")
    IVA_ADMON_vaor2 = fields.Monetary(string="IVA ADMON", compute='_compute_suma_contrapartida')
    RETEICA_valor = fields.Monetary(string="RETEICA")
    RETEICA_valor2 = fields.Monetary(string="RETEICA", compute='_compute_suma_contrapartida')
    Descuentos_Estampillas_valor = fields.Monetary(string="Descuentos en estampillas")
    Descuentos_Estampillas_valor2 = fields.Monetary(string="Descuentos en estampillas",
                                                    compute='_compute_suma_contrapartida')
    cuatro_X1000_valor = fields.Monetary(string="4X1000")
    cuatro_X1000_valor2 = fields.Monetary(string="4X1000", compute='_compute_suma_contrapartida')
    ICA_AVISOS_Y_TABLEROS_valor = fields.Monetary(string="ICA AVISOS Y TABLEROS")
    ICA_AVISOS_Y_TABLEROS_valor2 = fields.Monetary(compute='_compute_suma_contrapartida')
    costos_tributarios_total_valor = fields.Monetary(string="Costos tributarios total",
                                                     compute='_compute_suma_costos_tributarios_total_mcb')

    @api.depends('IVA_ADMON_vaor', 'RETEICA_valor', 'Descuentos_Estampillas_valor', 'cuatro_X1000_valor',
                 'ICA_AVISOS_Y_TABLEROS_valor')
    def _compute_suma_costos_tributarios_total_mcb(self):
        for record in self:
            total_suma = sum([
                record.IVA_ADMON_vaor,
                record.RETEICA_valor,
                record.Descuentos_Estampillas_valor,
                record.cuatro_X1000_valor,
                record.ICA_AVISOS_Y_TABLEROS_valor
            ])
            record.costos_tributarios_total_valor = total_suma

    costos_tributarios_total_valor2 = fields.Monetary(compute='_compute_suma_contrapartida')
    Participación_UV = fields.Monetary(string="Participación UV/FAC, ESC")
    Participación_UV2 = fields.Monetary(compute='_compute_suma_contrapartida')
    fuv_programas = fields.Monetary(string="FUV - Programas sociales")
    fuv_programas2 = fields.Monetary(compute='_compute_suma_contrapartida')


    @api.depends('costo_total_2', 'total_costos_produccion_N3', 'Admon_12', 'costos_financieros_total')
    def _compute_suma_costo_total_abc(self):
        for record in self:
            total_suma = sum([
                record.total_costos_produccion_N3,
                record.Admon_12,
                record.costos_financieros_total
            ])
            record.costo_total_2 = total_suma

    costo_total_2 = fields.Float(string="Costo total 2", currency_field='currency_id',
                                 currency='COP', compute="_compute_suma_costo_total_abc")
    costo_total_22 = fields.Float(string="Costo total 2", currency_field='currency_id', currency='COP',
                                  compute='_compute_suma_contrapartida')
    costo_tributario2 = fields.Monetary(string="Costo tributario 2", currency_field='currency_id', currency='COP', )
    costo_tributario22 = fields.Monetary(string="Costo tributario 2", currency_field='currency_id', currency='COP',
                                         compute='_compute_suma_contrapartida')
    total_a_facturar = fields.Float(string="Total a facturar", currency_field='currency_id', currency='COP', )
    total_a_facturar2 = fields.Float(string="Total a facturar", currency_field='currency_id', currency='COP',
                                     compute='_compute_suma_contrapartida')
    excedente_1 = fields.Monetary(string="Excedente")
    excedente_12 = fields.Monetary(string="Excedente", compute='_compute_suma_contrapartida')
    campo_1V = fields.Monetary(string="Campo 1")
    campo_1V2 = fields.Monetary(string="Campo 1", compute='_compute_suma_contrapartida')
    campo_2V = fields.Monetary(string="Campo 2")
    campo_2V2 = fields.Monetary(string="Campo 2", compute='_compute_suma_contrapartida')
    campo_3V = fields.Monetary(string="Campo 3")
    campo_3V2 = fields.Monetary(string="Campo 3", compute='_compute_suma_contrapartida')
    campo_4V = fields.Monetary(string="Campo 4")
    campo_4V2 = fields.Monetary(string="Campo 4", compute='_compute_suma_contrapartida')
    campo_5V = fields.Float(string="Campo 5V", currency_field='currency_id', currency='COP')
    campo_5V2 = fields.Float(string="Campo 5V", currency_field='currency_id', currency='COP',
                             compute='_compute_suma_contrapartida')
    Admon_12 = fields.Monetary(string='Administración')
    Admon_122 = fields.Monetary(string="Admon.12%", compute='_compute_suma_contrapartida')

    total_costos_produccion_N2 = fields.Monetary(compute='_compute_suma_contrapartida')

    produccion_1n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_2n_N4 = fields.Monetary(string="2")
    produccion_2n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_3n_N4 = fields.Monetary(string="3")
    produccion_3n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_4n_N4 = fields.Monetary(string="4")
    produccion_4n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_5n_N4 = fields.Monetary(string="5")
    produccion_5n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_6n_N4 = fields.Monetary(string="6")
    produccion_6n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_7n_N4 = fields.Monetary(string="7")
    produccion_7n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_8n_N4 = fields.Monetary(string="8")
    produccion_8n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')
    produccion_9n_N4 = fields.Monetary(string="9")
    produccion_9n_N42 = fields.Monetary(compute='_compute_suma_contrapartida')

    # Columna 2 derecha bloque3
    valor_proyecto_convenio = fields.Char(string="VALOR PROYECTO / CONVENIO")
    valor_proyecto_convenio2si = fields.Float()
    valor_proyecto_convenio22 = fields.Float(compute='_compute_suma_contrapartida')

    adicion_otro_si = fields.Monetary(string="Adicion (OTRO SI)", readonly=True, compute='_compute_otrosi_convenio2')
    adicion_otro_si2 = fields.Monetary(string="Adicion (OTRO SI)", readonly=False,
                                       compute='_compute_suma_contrapartida')


    contrapartida2 = fields.Monetary(string="Contrapartida FUV")
    contrapartida22 = fields.Monetary(compute='_compute_suma_contrapartida')

    @api.depends('valor_proyecto_convenio2si')
    def _compute_otrosi_convenio2(self):
        for record in self:
            record.adicion_otro_si = record.valor_proyecto_convenio2si

    @api.depends('adicion_otro_si', 'costos_financieros_total', 'otros_costos_financieros',
                 'polizas', 'fiducia', 'intereses', 'transferencias', 'cheques', 'mcb_id', 'IVA_ADMON_vaor',
                 'RETEICA_valor', 'Descuentos_Estampillas_valor', 'cuatro_X1000_valor', 'Descuentos_Estampillas_valor',
                 'ICA_AVISOS_Y_TABLEROS_valor', 'costos_tributarios_total_valor', 'Participación_UV', 'fuv_programas',
                 'costo_total_2', 'costo_tributario2', 'total_a_facturar', 'excedente_1', 'campo_1V', 'campo_2V',
                 'campo_3V', 'campo_4V', 'campo_5V', 'Admon_12', 'total_costos_produccion_N3',
                 'produccion_1n_N4', 'produccion_2n_N4', 'produccion_3n_N4', 'produccion_4n_N4',
                 'produccion_5n_N4', 'produccion_6n_N4', 'produccion_7n_N4', 'produccion_8n_N4', 'produccion_9n_N4',
                 'contrapartida2')
    def _compute_suma_contrapartida(self):
        for record in self:
            registros_mcb = self.env['mcb2'].search([('mcb_id', '=', record.mcb_id.id)])

            adicion_otro_si2 = sum(registro_mcb2.adicion_otro_si for registro_mcb2 in registros_mcb)
            costos_financieros_total2 = sum(registro_mcb2.costos_financieros_total for registro_mcb2 in registros_mcb)
            otros_costos_financieros2 = sum(registro_mcb2.otros_costos_financieros for registro_mcb2 in registros_mcb)
            polizas2 = sum(registro_mcb2.polizas for registro_mcb2 in registros_mcb)
            fiducia2 = sum(registro_mcb2.fiducia for registro_mcb2 in registros_mcb)
            intereses2 = sum(registro_mcb2.intereses for registro_mcb2 in registros_mcb)
            transferencias2 = sum(registro_mcb2.transferencias for registro_mcb2 in registros_mcb)
            cheques2 = sum(registro_mcb2.cheques for registro_mcb2 in registros_mcb)
            IVA_ADMON_vaor2 = sum(registro_mcb2.IVA_ADMON_vaor for registro_mcb2 in registros_mcb)
            RETEICA_valor2 = sum(registro_mcb2.RETEICA_valor for registro_mcb2 in registros_mcb)
            Descuentos_Estampillas_valor2 = sum(registro_mcb2.Descuentos_Estampillas_valor for registro_mcb2 in
                                                registros_mcb)
            cuatro_X1000_valor2 = sum(registro_mcb2.cuatro_X1000_valor for registro_mcb2 in registros_mcb)
            ICA_AVISOS_Y_TABLEROS_valor2 = sum(registro_mcb2.ICA_AVISOS_Y_TABLEROS_valor for registro_mcb2 in
                                               registros_mcb)
            costos_tributarios_total_valor2 = sum(registro_mcb2.costos_tributarios_total_valor for registro_mcb2 in
                                                  registros_mcb)
            Participación_UV2 = sum(registro_mcb2.Participación_UV for registro_mcb2 in registros_mcb)
            fuv_programas2 = sum(registro_mcb2.fuv_programas for registro_mcb2 in registros_mcb)
            costo_total_22 = sum(registro_mcb2.costo_total_2 for registro_mcb2 in registros_mcb)
            costo_tributario22 = sum(registro_mcb2.costo_tributario2 for registro_mcb2 in registros_mcb)
            total_a_facturar2 = sum(registro_mcb2.total_a_facturar for registro_mcb2 in registros_mcb)
            excedente_12 = sum(registro_mcb2.excedente_1 for registro_mcb2 in registros_mcb)
            campo_1V2 = sum(registro_mcb2.campo_1V for registro_mcb2 in registros_mcb)
            campo_2V2 = sum(registro_mcb2.campo_2V for registro_mcb2 in registros_mcb)
            campo_3V2 = sum(registro_mcb2.campo_3V for registro_mcb2 in registros_mcb)
            campo_4V2 = sum(registro_mcb2.campo_4V for registro_mcb2 in registros_mcb)
            campo_5V2 = sum(registro_mcb2.campo_5V for registro_mcb2 in registros_mcb)
            Admon_122 = sum(registro_mcb2.Admon_12 for registro_mcb2 in registros_mcb)
            total_costos_produccion_N2 = sum(registro_mcb2.total_costos_produccion_N3 for
                                             registro_mcb2 in registros_mcb)
            produccion_1n_N42 = sum(registro_mcb2.produccion_1n_N4 for registro_mcb2 in registros_mcb)
            produccion_2n_N42 = sum(registro_mcb2.produccion_2n_N4 for registro_mcb2 in registros_mcb)
            produccion_3n_N42 = sum(registro_mcb2.produccion_3n_N4 for registro_mcb2 in registros_mcb)
            produccion_4n_N42 = sum(registro_mcb2.produccion_4n_N4 for registro_mcb2 in registros_mcb)
            produccion_5n_N42 = sum(registro_mcb2.produccion_5n_N4 for registro_mcb2 in registros_mcb)
            produccion_6n_N42 = sum(registro_mcb2.produccion_6n_N4 for registro_mcb2 in registros_mcb)
            produccion_7n_N42 = sum(registro_mcb2.produccion_7n_N4 for registro_mcb2 in registros_mcb)
            produccion_8n_N42 = sum(registro_mcb2.produccion_8n_N4 for registro_mcb2 in registros_mcb)
            produccion_9n_N42 = sum(registro_mcb2.produccion_9n_N4 for registro_mcb2 in registros_mcb)
            valor_proyecto_convenio22 = sum(registro_mcb2.valor_proyecto_convenio2si for registro_mcb2 in registros_mcb)
            contrapartida22 = sum(registro_mcb2.contrapartida2 for registro_mcb2 in registros_mcb)

            record.contrapartida22 = contrapartida22
            record.adicion_otro_si2 = adicion_otro_si2
            record.costos_financieros_total2 = costos_financieros_total2
            record.otros_costos_financieros2 = otros_costos_financieros2
            record.polizas2 = polizas2
            record.fiducia2 = fiducia2
            record.intereses2 = intereses2
            record.transferencias2 = transferencias2
            record.cheques2 = cheques2
            record.IVA_ADMON_vaor2 = IVA_ADMON_vaor2
            record.RETEICA_valor2 = RETEICA_valor2
            record.Descuentos_Estampillas_valor2 = Descuentos_Estampillas_valor2
            record.cuatro_X1000_valor2 = cuatro_X1000_valor2
            record.ICA_AVISOS_Y_TABLEROS_valor2 = ICA_AVISOS_Y_TABLEROS_valor2
            record.costos_tributarios_total_valor2 = costos_tributarios_total_valor2
            record.Participación_UV2 = Participación_UV2
            record.fuv_programas2 = fuv_programas2
            record.costo_total_22 = costo_total_22
            record.costo_tributario22 = costo_tributario22
            record.total_a_facturar2 = total_a_facturar2
            record.excedente_12 = excedente_12
            record.campo_1V2 = campo_1V2
            record.campo_2V2 = campo_2V2
            record.campo_3V2 = campo_3V2
            record.campo_4V2 = campo_4V2
            record.campo_5V2 = campo_5V2
            record.Admon_122 = Admon_122
            record.total_costos_produccion_N2 = total_costos_produccion_N2
            record.produccion_1n_N42 = produccion_1n_N42
            record.produccion_2n_N42 = produccion_2n_N42
            record.produccion_3n_N42 = produccion_3n_N42
            record.produccion_4n_N42 = produccion_4n_N42
            record.produccion_5n_N42 = produccion_5n_N42
            record.produccion_6n_N42 = produccion_6n_N42
            record.produccion_7n_N42 = produccion_7n_N42
            record.produccion_8n_N42 = produccion_8n_N42
            record.produccion_9n_N42 = produccion_9n_N42
            record.valor_proyecto_convenio22 = valor_proyecto_convenio22

    # Columna 1 izquierda bloque 1##############################################################################
    # Columna 1 izquierda bloque 1 antigua
    a_costos_produccion = fields.Char(string="A. Costos de producción")
    produccion_1 = fields.Char(string="1")
    produccion_1n = fields.Monetary(string="1")
    produccion_2 = fields.Char(string="2")
    produccion_2n = fields.Char(string="2")
    produccion_3 = fields.Char(string="3")
    produccion_3n = fields.Char(string="3")
    produccion_4 = fields.Char(string="4")
    produccion_4n = fields.Char(string="4")
    produccion_5 = fields.Char(string="5")
    produccion_5n = fields.Char(string="5")
    produccion_6 = fields.Char(string="6")
    produccion_6n = fields.Char(string="6")
    produccion_7 = fields.Char(string="7")
    produccion_7n = fields.Char(string="7")
    produccion_8 = fields.Char(string="8")
    produccion_8n = fields.Char(string="8")
    produccion_9 = fields.Char(string="9")
    produccion_9n = fields.Char(string="9")
    total_costos_produccion = fields.Float(string="Total costos producción", currency_field='currency_id',
                                           currency='COP')
    # Columna 1 izquierda bloque 1 NUEVA
    a_costos_produccion_N = fields.Char(string="A. Costos de producción")
    produccion_1_N = fields.Char(string="1")
    produccion_2_N = fields.Char(string="2")
    produccion_3_N = fields.Char(string="3")
    produccion_4_N = fields.Char(string="4")
    produccion_5_N = fields.Char(string="5")
    produccion_6_N = fields.Char(string="6")
    produccion_7_N = fields.Char(string="7")
    produccion_8_N = fields.Char(string="8")
    produccion_9_N = fields.Char(string="9")

    total_costos_produccion_N3 = fields.Monetary(compute='_compute_calculo_costo_producccion_mcb2')

    @api.depends('Admon_12', 'costos_financieros_total', 'costos_tributarios_total_valor', 'valor_proyecto_convenio2si')
    def _compute_calculo_costo_producccion_mcb2(self):
        for record in self:
            record.total_costos_produccion_N3 = (record.valor_proyecto_convenio2si -
                                                 (record.costos_tributarios_total_valor + record.Admon_12 +
                                                  record.costos_financieros_total))

    # Funciones de cálculo
    produccion_1n_N4 = fields.Monetary(compute='_compute_resta_produccionmcb2')

    @api.depends('total_costos_produccion_N3', 'produccion_mcb2_suma_2_9')
    def _compute_resta_produccionmcb2(self):
        for record in self:
            record.produccion_1n_N4 = record.total_costos_produccion_N3 - record.produccion_mcb2_suma_2_9

    produccion_mcb2_suma_2_9 = fields.Monetary(compute='_compute_suma_produccion_mcb2')

    @api.depends('produccion_2n_N4', 'produccion_3n_N4', 'produccion_4n_N4', 'produccion_5n_N4', 'produccion_6n_N4',
                 'produccion_7n_N4', 'produccion_8n_N4', 'produccion_9n_N4')
    def _compute_suma_produccion_mcb2(self):
        for record in self:
            total_suma = sum([
                record.produccion_2n_N4,
                record.produccion_3n_N4,
                record.produccion_4n_N4,
                record.produccion_5n_N4,
                record.produccion_6n_N4,
                record.produccion_7n_N4,
                record.produccion_8n_N4,
                record.produccion_9n_N4
            ])
            record.produccion_mcb2_suma_2_9 = total_suma

    informacion_base_agregar_id = fields.Many2one('informacion_base_agregar', string='Informacion base')


    new_name = fields.Char(string='Nuevo nombre')
    fch_creacion = fields.Datetime(string='Fecha creación', copy=False, default=lambda self: fields.Datetime.now())
    write_date = fields.Datetime(string='Fecha de Modificación', readonly=True)
    gestion_seleccionada = fields.Boolean(compute='_compute_gestion_seleccionada', store=True)

    name = fields.Char(string="Nombre", readonly=False)
    objecto_proyecto = fields.Char(string="Objeto del proyecto")
    entidad_contratante2 = fields.Char(string="Entidad contratante", related='mcb_id.entidad_contratante')
    centro_costo2 = fields.Char(string="Centro de costo", related='mcb_id.centro_costo')
    numero_contrato2 = fields.Char(string="Número de contrato", related='mcb_id.numero_contrato')
    numero_de_poliza2 = fields.Char(string="Número de póliza", related='mcb_id.numero_de_poliza')
    fecha_inicio = fields.Date(string="Fecha de inicio", related='mcb_id.fecha_inicio')
    fecha_finalizacion = fields.Date(string="Fecha de finalización")
    MODALIDAD2 = fields.Selection([
        ('CI', 'Carta de instrucción (CI)'),
        ('CC', 'Contrato de consultoría (CC)'),
        ('CO', 'Contrato de obra (CO)'),
        ('CIA', 'Contrato interadministrativo (CIA)'),
        ('CLP', 'Contrato de licitaciones públicas (CLP)'),
        ('CPS', 'Contrato de prestación de servicios (CPS)'),
        ('OC', 'Orden de compra (OC)'),
        ('OS', 'Orden de servicios (OS)'),
        ('CS', 'Contrato de suministros (CS)'),
        ('CA', 'Convenio de asociación (CA)'),
        ('CCF', 'Convenio de cofinanciación (CCF)'),
        ('CEC', 'Convenio especial de cooperación (CEC)'),
        ('CIA', 'Convenio interadministrativo (CIA)')
    ], string="Modalidad", related='mcb_id.MODALIDAD')

    modalidad2_id = fields.Many2one(
       'modalidad_contratacion',
        string='Modalidad de Contratación',
        tracking=True,
        related='mcb_id.modalidad_id'
    )

    title = fields.Char(string="MATRIZ GENERAL DE COSTO BENEFICIO")
    subtitle = fields.Char(string="F. Emisión: 2012-09-10  Versión: 6\n\nFU-F-GPP-019")
    # Columna 1 izquierda bloque 3
    costos_tributarios = fields.Char(string="Costos tributarios")
    impuesto = fields.Char(string="Impuestos")
    tasa = fields.Char(string="Tasa %")
    valor = fields.Char(string="Valor")
    # Columna 1 izquierda bloque 4
    participacion = fields.Char(string="Participacion")
    # Columna 1 izquierda bloque 5
    facturacion = fields.Char(string="Programaciòn de facturaciòn")
    fecha_estimada = fields.Char(string="Fecha estimada")
    porcentaje_ejecucion = fields.Char(string="% Ejecuciòn")
    valor_facturar = fields.Char(string="Valor a facturar ")

    campo_1F = fields.Date(string="Campo 1")
    campo_1E = fields.Char(string="Campo 1")
    campo_2F = fields.Date(string="Campo 2")
    campo_2E = fields.Char(string="Campo 2")
    campo_3F = fields.Date(string="Campo 3")
    campo_3E = fields.Char(string="Campo 3")
    campo_4f = fields.Date(string="Campo 4")
    campo_4E = fields.Char(string="Campo 4")
    campo_5F = fields.Date(string="Campo 5")
    campo_5E = fields.Char(string="Campo 5")
    # Columna 2 derecha bloque1
    b_costos_administracion = fields.Char(string="B. Costos administración")
    Admon_C_12 = fields.Char(string="Admon.12%")
    c_costos_financieros = fields.Char(string="C. Costos financieros")

    # texto
    Revisado = fields.Char(string="Revisado direccion unidad de proyectos y/o")
    Lider = fields.Char(string="Lider gestor - Proyectos de ingeniería e infraestructura")
    # notas
    notas = fields.Char(string="Notas para el diligenciamiento:")
    mensaje_1 = fields.Text(
        default="Para la estimación de la administración, "
                "tener en cuenta el porcentaje definido conforme a los indicadores de "
                "gestión del año vigente. Este valor será neto a negociar fuera de impuestos y gastos asociados.")
    mensaje_2 = fields.Text(
        default="Tener en cuenta para el cálculo del IVA la "
                "procedencia del proyecto (Entidad Contratante), el tipo de contrato y su modalidad de contratación. "
                "La articulación con el área financiera para resolver particularidades.")
    mensaje_3 = fields.Text(
        default="El descuento por estampilla depende del tipo de contrato y del ente territorial. "
                "Sí es por Carta de Instrucción o directamente con el sector público, consultar o solicitar "
                "la ficha técnica del proyecto ante la entidad contratante y entregar a financiera.")
    mensaje_4 = fields.Char(string="Observaciones Financiera")





_CP_REQUIRED_VISIBLE = {
    'carta_invitacion_visible',
    'propuesta_visible',
    'polizas_visible',
    'aceptacion_polizas_visible',
    'acta_inicio_visible',
}

_CP_REQUIRED_LABELS = {
    'carta_invitacion_visible':    'Carta de Invitación o Print Secop2',
    'propuesta_visible':           'Propuesta',
    'polizas_visible':             'Pólizas',
    'aceptacion_polizas_visible':  'Aceptación de pólizas',
    'acta_inicio_visible':         'Acta de inicio / print Secop 2',
}


class CarpetaPrincipal(models.Model):
    _name = 'carpeta_principal'
    _description = 'Carpeta Principal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'new_name'

    def write(self, vals):
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            return True

        # Solo validar si viene de una subida de documento (contexto explícito)
        if self.env.context.get('_cp_check_required'):
            non_required_upload = any(
                k.endswith('_visible') and k not in _CP_REQUIRED_VISIBLE and vals.get(k) is False
                for k in vals
            )
            if non_required_upload:
                for rec in self:
                    missing = [
                        label for field, label in _CP_REQUIRED_LABELS.items()
                        if getattr(rec, field, True)
                    ]
                    if missing:
                        raise ValidationError(
                            'Debe subir primero los documentos obligatorios (*):\n\n- '
                            + '\n- '.join(missing)
                        )
        return super().write(vals)

    is_editable_cp = fields.Boolean(string='Es editable', related="proyecto_relacionado.is_editable", default=False)

    @api.onchange('is_editable_cp')
    def _onchange_is_editable_cp(self):
        # Este método se llama cuando cambia el valor de is_editable_cp
        # Puedes agregar lógica adicional aquí si es necesario
        pass
    

    name = fields.Char(string='Nombre', tracking=True)
    new_name = fields.Char(string='Nuevo nombre')
    description = fields.Text(string='Descripción', tracking=True)
    fch_creacion = fields.Date(string='Fecha de Creación', default=fields.Date.today, tracking=True)
    write_date = fields.Datetime(string='Última Modificación', default=fields.Datetime.now, tracking=True)
    mcb_ids = fields.One2many('mcb', 'carpeta_principal_id', string='MCB', tracking=True)
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')
    contrataciones_carpeta_id = fields.Many2one('contratacion', string='Contratación', tracking=True)

    # Nuevos campo

    matriz_de_riesgos = fields.Binary(string='Matriz de riesgos', tracking=True)
    matriz_de_riesgos_char = fields.Char(string='Matriz de riesgos', tracking=True)
    matriz_de_riesgos_visible = fields.Boolean(default=True)
    matriz_de_riesgos_id = fields.Many2one('drive', string='Matriz de riesgos (Drive)', tracking=True)

    def action_upload_matriz_de_riesgos(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir matriz de riesgos',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'matriz_de_riesgos' 
            },
        }

    _cache_documentos_matriz_de_riesgos = {}

    def abrir_documento_matriz_de_riesgos(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'matriz_de_riesgos_id' está configurado
        if self.matriz_de_riesgos_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Matriz de riesgos',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.matriz_de_riesgos_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.matriz_de_riesgos_char:
            file_name_to_search = self.matriz_de_riesgos_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_matriz_de_riesgos:
                drive_document = self._cache_documentos_matriz_de_riesgos[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_matriz_de_riesgos[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'matriz_de_riesgos_id' con el ID del documento encontrado
                self.write({'matriz_de_riesgos_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Matriz de riesgos',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

        

 



    informe_de_invertentoria = fields.Binary(string='Informe de interventoría', tracking=True)
    informe_de_invertentoria_char = fields.Char(string='Informe de interventoría (char)', tracking=True)
    informe_de_invertentoria_visible = fields.Boolean(default=True)
    informe_de_invertentoria_id = fields.Many2one('drive', string='Informe de interventoría (Drive)', tracking=True)

    def action_upload_informe_de_invertentoria(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir informe de interventoría',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'informe_de_invertentoria_documento' 
            },
        }

    _cache_documentos_informe_de_invertentoria = {}

    def abrir_documento_informe_de_invertentoria(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'informe_de_invertentoria_id' está configurado
        if self.informe_de_invertentoria_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Informe de interventoría',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.informe_de_invertentoria_id.id,
                'target': 'new',
            }

        # Si 'informe_de_invertentoria_char' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.informe_de_invertentoria_char:
            file_name_to_search = self.informe_de_invertentoria_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_informe_de_invertentoria:
                drive_document = self._cache_documentos_informe_de_invertentoria[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_informe_de_invertentoria[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'informe_de_invertentoria_id' con el ID del documento encontrado
                self.write({'informe_de_invertentoria_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Informe de interventoría',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    m_g_a = fields.Binary(string='Matriz de riesgos', tracking=True)
    m_g_a_char = fields.Char(string='Matriz de riesgos', tracking=True)
    m_g_a_visible = fields.Boolean(default=True)
    m_g_a_id = fields.Many2one('drive', string='Matriz de riesgos (Drive)', tracking=True)

    def action_upload_m_g_a(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir M.G.A',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'm_g_a_documento' 
            },
        }

    _cache_documentos_m_g_a = {}

    def abrir_documento_m_g_a(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'm_g_a_id' está configurado
        if self.m_g_a_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'M.G.A',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.m_g_a_id.id,
                'target': 'new',
            }

        # Si 'm_g_a_char' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.m_g_a_char:
            file_name_to_search = self.m_g_a_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_m_g_a:
                drive_document = self._cache_documentos_m_g_a[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_m_g_a[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'm_g_a_id' con el ID del documento encontrado
                self.write({'m_g_a_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'M.G.A',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')


    # DOCUMENTOS CARPETA PRINCIPAL
    
    # Campo para almacenar la carpeta principal encontrada (en modelo 'carpeta_principal')
    stage3_id = fields.Many2one('drive_stage', string='Ubicación', tracking=True)

    @api.onchange('proyecto_relacionado')
    def _onchange_proyecto_relacionado(self):
        """
        Se ejecuta al cambiar 'proyecto_relacionado' y asigna el resultado a stage3_id.
        """
        if self.proyecto_relacionado:
            _logger.info(f"Onchange: Buscando carpeta para proyecto {self.proyecto_relacionado.name}")
            
            # 1. Llamamos a la función de búsqueda
            drive_stage = self._buscar_carpeta()
            
            if drive_stage:
                _logger.info(f"Carpeta encontrada: {drive_stage.name}")
                # 2. ASIGNACIÓN CLAVE: Esto coloca el valor en la interfaz
                self.stage3_id = drive_stage.id
            else:
                _logger.warning("No se encontró la carpeta principal.")
                self.stage3_id = False
        else:
            # Si borran el proyecto, limpiamos el stage3_id
            self.stage3_id = False

    # 1. Lógica de búsqueda centralizada (puede ser llamado como estático o de instancia)
    # Cambiamos el nombre para que sea más claro lo que busca (drive_stage)
    def _buscar_carpeta(self):
        domain = [('name', 'in', ['1. CARPETA PRINCIPAL', 'CARPETA PRINCIPAL'])]
        
        # 1. Prioridad: proyecto_relacionado
        if self.proyecto_relacionado:
            _logger.info(f"Usando proyecto_relacionado: {self.proyecto_relacionado.name}")
            # Buscamos específicamente en la columna 'proyecto_relacionado'
            domain.append(('proyecto_relacionado', '=', self.proyecto_relacionado.id))
            
        # 2. Alternativa: contrataciones_carpeta_id (solo si no existe el anterior)
        elif self.contrataciones_carpeta_id:
            _logger.info(f"Usando contrataciones_carpeta_id: {self.contrataciones_carpeta_id.name}")
            # Buscamos específicamente en la columna 'contrataciones_carpeta_id'
            domain.append(('contrataciones_carpeta_id', '=', self.contrataciones_carpeta_id.id))
            
        else:
            # Si no hay ninguno de los dos, no hay nada que buscar
            return None

        # Ejecutamos la búsqueda con el dominio que construimos arriba
        drive_stage = self.env['drive_stage'].search(domain, limit=1)

        if drive_stage:
            _logger.info(f"Carpeta encontrada: {drive_stage.name} (ID: {drive_stage.id})")
            return drive_stage
        
        _logger.warning("Se intentó buscar pero no se halló la 'CARPETA PRINCIPAL'.")
        return None

    carta_invitacion = fields.Binary(string='Carta de Invitación o Print Secop2', tracking=True)
    carta_invitacion_char = fields.Char(string='Carta de Invitación o Print Secop2 (Char)', tracking=True)
    carta_invitacion_visible = fields.Boolean(default=True)
    carta_invitacion_id = fields.Many2one('drive', string='Documento carta de invitación', tracking=True)

    def action_upload_carta_invitacion(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir carta de invitación o print secop2',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'carta_invitacion' 
            },
        }

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_carta_invitacion = {}

    def abrir_documento_carta_invitacion(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'carta_invitacion_id' está configurado
        if self.carta_invitacion_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'carta de invitación o print secop2',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.carta_invitacion_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.carta_invitacion_char:
            file_name_to_search = self.carta_invitacion_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_carta_invitacion:
                drive_document = self._cache_documentos_carta_invitacion[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_carta_invitacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'carta_invitacion_id' con el ID del documento encontrado
                self.write({'carta_invitacion_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'carta de invitación o print secop2',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')


    cdp = fields.Binary(string='CDP', tracking=True)
    cdp_char = fields.Char(string='CDP (Char)', tracking=True)
    cdp_visible = fields.Boolean(default=True)
    cdp_id = fields.Many2one('drive', string='Documento CDP', tracking=True)

    def action_upload_cdp(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir CDP',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'cdp_documento' 
            },
        }

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_cdp = {}

    def abrir_documento_cdp(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'cdp_id' está configurado
        if self.cdp_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'CDP',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.cdp_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.cdp_char:
            file_name_to_search = self.cdp_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_cdp:
                drive_document = self._cache_documentos_cdp[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_cdp[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'cdp_id' con el ID del documento encontrado
                self.write({'cdp_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'CDP',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    
    def abrir_otros_archivos_nueva_relacion_cdp(self):
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else False
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otras documentos',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('carpeta_principal_otros_id', '=', self.id)],
            'context': {
                'default_stage3_id': self.stage3_id.id,
                'default_folder_id': google_folder_id,
                'default_carpeta_principal_otros_id': self.id,
            },
            'target': 'current',
        }


    




    estudios_previos = fields.Binary(string='Estudios Previos', tracking=True)
    estudios_previos_char = fields.Char(string='Estudios Previos (Char)', tracking=True)

    estudios_previos_visible = fields.Boolean(default=True)
    estudios_previos_id = fields.Many2one('drive', string='Documento Estudios Previos', tracking=True)

    def action_upload_estudios_previos(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir estudios previos',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'estudios_previos' 
            },
        }

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_estudios_previos = {}

    def abrir_documento_estudios_previos(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'estudios_previos_id' está configurado
        if self.estudios_previos_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'estudios previos',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.estudios_previos_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.estudios_previos_char:
            file_name_to_search = self.estudios_previos_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_estudios_previos:
                drive_document = self._cache_documentos_estudios_previos[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_estudios_previos[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'estudios_previos_id' con el ID del documento encontrado
                self.write({'estudios_previos_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'estudios previos',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')


    propuesta = fields.Binary(string='Propuesta', tracking=True)
    propuesta_char = fields.Char(string='Propuesta (Char)', tracking=True)

    
    propuesta_visible = fields.Boolean(default=True)
    propuesta_id = fields.Many2one('drive', string='Documento Propuesta', tracking=True)

    def action_upload_propuesta(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir propuesta',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'propuesta_documento' 
            },
        }

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_propuesta = {}

    def abrir_documento_propuesta(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'propuesta_id' está configurado
        if self.propuesta_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Propuesta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.propuesta_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.propuesta_char:
            file_name_to_search = self.propuesta_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_propuesta:
                drive_document = self._cache_documentos_propuesta[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_propuesta[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'propuesta_id' con el ID del documento encontrado
                self.write({'propuesta_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Propuesta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')



    oficio_remisorio_propuesta = fields.Binary(string='Oficio Remisorio Propuesta (si aplica)', tracking=True)
    oficio_remisorio_propuesta_char = fields.Char(string='Oficio Remisorio Propuesta (Char)', tracking=True)

    oficio_remisorio_propuesta_visible = fields.Boolean(default=True)
    oficio_remisorio_propuesta_id = fields.Many2one('drive', string='Documento Oficio Remisorio Propuesta', tracking=True)

    def action_upload_oficio_remisorio_propuesta(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir oficio remisorio propuesta',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'oficio_remisorio_propuesta_documento' 
            },
        }

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_oficio_remisorio_propuesta = {}

    def abrir_documento_oficio_remisorio_propuesta(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'oficio_remisorio_propuesta_id' está configurado
        if self.oficio_remisorio_propuesta_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Oficio remisorio propuesta',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.oficio_remisorio_propuesta_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.oficio_remisorio_propuesta_char:
            file_name_to_search = self.oficio_remisorio_propuesta_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_oficio_remisorio_propuesta:
                drive_document = self._cache_documentos_oficio_remisorio_propuesta[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_oficio_remisorio_propuesta[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'oficio_remisorio_propuesta_id' con el ID del documento encontrado
                self.write({'oficio_remisorio_propuesta_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Oficio remisorio propuesta',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')



    resolucion_contratacion = fields.Binary(string='Resolución de Contratación (si aplica)', tracking=True)
    resolucion_contratacion_char = fields.Char(string='Resolución de Contratación (Char)', tracking=True)

    resolucion_contratacion_visible = fields.Boolean(default=True)
    resolucion_contratacion_id = fields.Many2one('drive', string='Documento Resolución de Contratación', tracking=True)

    def action_upload_resolucion_contratacion(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir resolución de contratación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'resolucion_contratacion_documento' 
            },
        }

    # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_resolucion_contratacion = {}

    def abrir_documento_resolucion_contratacion(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'resolucion_contratacion_id' está configurado
        if self.resolucion_contratacion_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Resolución de contratación',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.resolucion_contratacion_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.resolucion_contratacion_char:
            file_name_to_search = self.resolucion_contratacion_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_resolucion_contratacion:
                drive_document = self._cache_documentos_resolucion_contratacion[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_resolucion_contratacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'resolucion_contratacion_id' con el ID del documento encontrado
                self.write({'resolucion_contratacion_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Resolución de contratación',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')



    contrato_convenio_componente = fields.Binary(string='Contrato/Convenio/Componente', tracking=True)
    contrato_convenio_componente_char = fields.Char(string='Contrato/Convenio/Componente (Char)', tracking=True)
    contrato_convenio_componente_visible = fields.Boolean(default=True)
    contrato_convenio_componente_id = fields.Many2one('drive', string='Documento Contrato/Convenio/Componente', tracking=True)

    def action_upload_contrato_convenio_componente(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir contrato/convenio/componente',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'contrato_convenio_componente_documento' 
            },
        }

    _cache_documentos_contrato_convenio_componente = {}

    def abrir_documento_contrato_convenio_componente(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'contrato_convenio_componente_id' está configurado
        if self.contrato_convenio_componente_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Contrato/Convenio/Componente',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.contrato_convenio_componente_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.contrato_convenio_componente_char:
            file_name_to_search = self.contrato_convenio_componente_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_contrato_convenio_componente:
                drive_document = self._cache_documentos_contrato_convenio_componente[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_contrato_convenio_componente[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'contrato_convenio_componente_id' con el ID del documento encontrado
                self.write({'contrato_convenio_componente_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Contrato/Convenio/Componente',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')


    polizas = fields.Binary(string='Pólizas', tracking=True)
    polizas_char = fields.Char(string='Pólizas (Char)', tracking=True)
    polizas_visible = fields.Boolean(default=True)
    polizas_id = fields.Many2one('drive', string='Documento Pólizas', tracking=True)

    def action_upload_polizas(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir pólizas',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'polizas_documento' 
            },
        }

    _cache_documentos_polizas = {}

    def abrir_documento_polizas(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'polizas_id' está configurado
        if self.polizas_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Pólizas',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.polizas_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.polizas_char:
            file_name_to_search = self.polizas_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_polizas:
                drive_document = self._cache_documentos_polizas[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_polizas[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'polizas_id' con el ID del documento encontrado
                self.write({'polizas_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Pólizas',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')



    aceptacion_polizas = fields.Binary(string='Aceptación de Pólizas (si aplica)', tracking=True)
    aceptacion_polizas_char = fields.Char(string='Aceptación de Pólizas (Char)', tracking=True)
    aceptacion_polizas_visible = fields.Boolean(default=True)
    aceptacion_polizas_id = fields.Many2one('drive', string='Documento Aceptación de Pólizas', tracking=True)

    def action_upload_aceptacion_polizas(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir aceptación de pólizas',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'aceptacion_polizas_documento' 
            },
        }

    _cache_documentos_aceptacion_polizas = {}

    def abrir_documento_aceptacion_polizas(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'aceptacion_polizas_id' está configurado
        if self.aceptacion_polizas_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Aceptación de pólizas',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.aceptacion_polizas_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.aceptacion_polizas_char:
            file_name_to_search = self.aceptacion_polizas_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_aceptacion_polizas:
                drive_document = self._cache_documentos_aceptacion_polizas[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_aceptacion_polizas[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'aceptacion_polizas_id' con el ID del documento encontrado
                self.write({'aceptacion_polizas_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Aceptación de pólizas',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    
    acta_inicio = fields.Binary(string='Acta de Inicio/Print Secop 2', tracking=True)
    acta_inicio_char = fields.Char(string='Acta de Inicio/Print Secop 2 (Char)', tracking=True)
    acta_inicio_visible = fields.Boolean(default=True)
    acta_inicio_id = fields.Many2one('drive', string='Documento Acta de Inicio/Print Secop 2', tracking=True)

    def action_upload_acta_inicio(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir acta de inicio/print Secop 2',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'acta_inicio_documento' 
            },
        }

    _cache_documentos_acta_inicio = {}

    def abrir_documento_acta_inicio(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'acta_inicio_id' está configurado
        if self.acta_inicio_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Acta de Inicio/Print Secop 2',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.acta_inicio_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.acta_inicio_char:
            file_name_to_search = self.acta_inicio_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_acta_inicio:
                drive_document = self._cache_documentos_acta_inicio[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_acta_inicio[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'acta_inicio_id' con el ID del documento encontrado
                self.write({'acta_inicio_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Acta de Inicio/Print Secop 2',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

       
    rpc = fields.Binary(string='RPC', tracking=True)
    rpc_char = fields.Char(string='RPC (Char)', tracking=True)
    rpc_visible = fields.Boolean(default=True)
    rpc_id = fields.Many2one('drive', string='Documento RPC', tracking=True)

    def action_upload_rpc(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'rpc_documento' 
            },
        }
 
    _cache_documentos_rpc = {}

    def abrir_documento_rpc(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'rpc_id' está configurado
        if self.rpc_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'RPC',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.rpc_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rpc_char:
            file_name_to_search = self.rpc_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rpc:
                drive_document = self._cache_documentos_rpc[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_rpc[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rpc_id' con el ID del documento encontrado
                self.write({'rpc_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'RPC',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')
    

    otrosi_modificatorios = fields.Binary(string='Otrosí (si aplica)/Modificatorios, Prórrogas', tracking=True)
    otrosi_modificatorios_char = fields.Char(string='Otrosí (si aplica)/Modificatorios, Prórrogas (Char)',
                                             tracking=True)
    otrosi_modificatorios_visible = fields.Boolean(default=True)
    otrosi_modificatorios_id = fields.Many2one('drive', string='Documento Otrosí/Modificatorios', tracking=True)

    def action_upload_otrosi_modificatorios(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir otrosí/modificatorios',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'otrosi_modificatorios_documento' 
            },
        }

    _cache_documentos_otrosi = {}

    def abrir_documento_otrosi_modificatorios(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'otrosi_modificatorios_id' está configurado
        if self.otrosi_modificatorios_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Otrosí/Modificatorios',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.otrosi_modificatorios_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.otrosi_modificatorios_char:
            file_name_to_search = self.otrosi_modificatorios_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_otrosi:
                drive_document = self._cache_documentos_otrosi[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_otrosi[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'otrosi_modificatorios_id' con el ID del documento encontrado
                self.write({'otrosi_modificatorios_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Otrosí/Modificatorios',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    otros_archivos_ids = fields.One2many('otros_archivos', 'modelo_principal_id', string='Archivos Relacionados')
    otros_archivos_poliza_ids = fields.One2many('otros_archivos', 'modelo_principal_poliza_id', string='Archivos Relacionados')
    otros_archivos_cdp_ids = fields.One2many('otros_archivos', 'modelo_principal_cdp_id',
                                                string='Archivos Relacionados')


    
    def abrir_otros_archivos(self):
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else False
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otros documentos',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('carpetas_principales_otra_relacion_id', '=', self.id)],
            'context': {
                'default_stage3_id': self.stage3_id.id,
                'default_folder_id': google_folder_id,
                'default_carpetas_principales_otra_relacion_id': self.id,
            },
            'target': 'current',
        }


    


    def abrir_otros_archivos_nueva_relacion(self):
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else False
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otros documentos',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('carpeta_principal_nueva_relacion_id', '=', self.id)],
            'context': {
                'default_stage3_id': self.stage3_id.id,
                'default_folder_id': google_folder_id,
                'default_carpeta_principal_nueva_relacion_id': self.id,
            },
            'target': 'current',
        }




    polizas_modificatorios = fields.Binary(string='Pólizas de los Modificatorios', tracking=True)
    polizas_modificatorios_char = fields.Char(string='Pólizas de los Modificatorios (Char)', tracking=True)
    polizas_modificatorios_visible = fields.Boolean(default=True)
    polizas_modificatorios_id = fields.Many2one('drive', string='Documento Pólizas de los Modificatorios', tracking=True)

    def action_upload_polizas_modificatorios(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir pólizas de los modificatorios',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'polizas_modificatorios_documento' 
            },
        }

    _cache_documentos_polizas_modificatorios = {}
    
    def abrir_documento_polizas_modificatorios(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'polizas_modificatorios_id' está configurado
        if self.polizas_modificatorios_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Pólizas de los Modificatorios',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.polizas_modificatorios_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.polizas_modificatorios_char:
            file_name_to_search = self.polizas_modificatorios_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_polizas_modificatorios:
                drive_document = self._cache_documentos_polizas_modificatorios[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_polizas_modificatorios[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'polizas_modificatorios_id' con el ID del documento encontrado
                self.write({'polizas_modificatorios_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Pólizas de los Modificatorios',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')
   
    designacion_supervisor = fields.Binary(string='Designación Supervisor', tracking=True)
    designacion_supervisor_char = fields.Char(string='Designación Supervisor (Char)', tracking=True)
    designacion_supervisor_visible = fields.Boolean(default=True)
    designacion_supervisor_id = fields.Many2one('drive', string='Documento Designación Supervisor', tracking=True)

    def action_upload_designacion_supervisor(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir designación supervisor',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'designacion_supervisor_documento' 
            },
        }

    _cache_documentos_designacion_supervisor = {}

    def abrir_documento_designacion_supervisor(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'designacion_supervisor_id' está configurado
        if self.designacion_supervisor_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Designación Supervisor',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.designacion_supervisor_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.designacion_supervisor_char:
            file_name_to_search = self.designacion_supervisor_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_designacion_supervisor:
                drive_document = self._cache_documentos_designacion_supervisor[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_designacion_supervisor[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'designacion_supervisor_id' con el ID del documento encontrado
                self.write({'designacion_supervisor_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Designación Supervisor',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    facturacion = fields.Binary(string='Facturación', tracking=True)
    facturacion_char = fields.Char(string='Facturación (Char)', tracking=True)
    facturacion_visible = fields.Boolean(default=True)
    facturacion_id = fields.Many2one('drive', string='Documento Facturación', tracking=True)

    def abrir_otros_archivos_facturacion(self):
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else False
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otras documentos',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('carpeta_principal_otros_facturacion_id', '=', self.id)],
            'context': {
                'default_stage3_id': self.stage3_id.id,
                'default_folder_id': google_folder_id,
                'default_carpeta_principal_otros_facturacion_id': self.id,
            },
            'target': 'current',
        }
    
    def action_upload_facturacion(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir facturación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'facturacion_documento' 
            },
        }
    
    _cache_documentos_facturacion = {}

    def abrir_documento_facturacion(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'facturacion_id' está configurado
        if self.facturacion_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Facturación',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.facturacion_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.facturacion_char:
            file_name_to_search = self.facturacion_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_facturacion:
                drive_document = self._cache_documentos_facturacion[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_facturacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'facturacion_id' con el ID del documento encontrado
                self.write({'facturacion_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Facturación',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    actas_seguimiento = fields.Binary(string='Actas de Seguimiento (si aplica)', tracking=True)
    actas_seguimiento_char = fields.Char(string='Actas de Seguimiento (Char)', tracking=True)
    actas_seguimiento_visible = fields.Boolean(default=True)
    actas_seguimiento_id = fields.Many2one('drive', string='Documento Actas de Seguimiento', tracking=True)

    def action_upload_actas_seguimiento(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir actas de seguimiento',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'actas_seguimiento_documento' 
            },
        }

    _cache_documentos_actas_seguimiento = {}

    def abrir_documento_actas_seguimiento(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'actas_seguimiento_id' está configurado
        if self.actas_seguimiento_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Actas de Seguimiento',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.actas_seguimiento_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.actas_seguimiento_char:
            file_name_to_search = self.actas_seguimiento_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_actas_seguimiento:
                drive_document = self._cache_documentos_actas_seguimiento[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_actas_seguimiento[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'actas_seguimiento_id' con el ID del documento encontrado
                self.write({'actas_seguimiento_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Actas de Seguimiento',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    informes_avance = fields.Binary(string='Informes de Avance/Parcial', tracking=True)
    informes_avance_char = fields.Char(string='Informes de Avance/Parcial (Char)', tracking=True)
    informes_avance_visible = fields.Boolean(default=True)
    informes_avance_id = fields.Many2one('drive', string='Documento Informes de Avance', tracking=True)

    def action_upload_informes_avance(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir informes de avance/parcial',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'informes_avance_documento' 
            },
        }

     
    _cache_documentos_informes_avance = {}

    def abrir_documento_informes_avance(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'informes_avance_id' está configurado
        if self.informes_avance_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Informes de Avance/Parcial',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.informes_avance_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.informes_avance_char:
            file_name_to_search = self.informes_avance_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_informes_avance:
                drive_document = self._cache_documentos_informes_avance[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_informes_avance[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'informes_avance_id' con el ID del documento encontrado
                self.write({'informes_avance_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Informes de Avance/Parcial',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    actas_supervisor_parciales = fields.Binary(string='Actas de Supervisor Parciales', tracking=True)
    actas_supervisor_parciales_char = fields.Char(string='Actas de Supervisor Parciales (Char)', tracking=True)
    actas_supervisor_parciales_visible = fields.Boolean(default=True)
    actas_supervisor_parciales_id = fields.Many2one('drive', string='Documento Actas de Supervisor Parciales', tracking=True)

    def action_upload_actas_supervisor_parciales(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir actas de supervisor parciales',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'actas_supervisor_parciales_documento' 
            },
        }

    _cache_documentos_actas_supervisor_parciales = {}

    def abrir_documento_actas_supervisor_parciales(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'actas_supervisor_parciales_id' está configurado
        if self.actas_supervisor_parciales_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Actas de Supervisor Parciales',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.actas_supervisor_parciales_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.actas_supervisor_parciales_char:
            file_name_to_search = self.actas_supervisor_parciales_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_actas_supervisor_parciales:
                drive_document = self._cache_documentos_actas_supervisor_parciales[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_actas_supervisor_parciales[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'actas_supervisor_parciales_id' con el ID del documento encontrado
                self.write({'actas_supervisor_parciales_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Actas de Supervisor Parciales',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    informe_final = fields.Binary(string='Informe Final', tracking=True)
    informe_final_char = fields.Char(string='Informe Final (Char)', tracking=True)
    informe_final_visible = fields.Boolean(default=True)
    informe_final_id = fields.Many2one('drive', string='Documento Informe Final', tracking=True)

    def action_upload_informe_final(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir informe final',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'informe_final_documento' 
            },
        }

    _cache_documentos_informe_final = {}

    def abrir_documento_informe_final(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'informe_final_id' está configurado
        if self.informe_final_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Informe Final',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.informe_final_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.informe_final_char:
            file_name_to_search = self.informe_final_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_informe_final:
                drive_document = self._cache_documentos_informe_final[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_informe_final[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'informe_final_id' con el ID del documento encontrado
                self.write({'informe_final_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Informe Final',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    acta_final_supervisor = fields.Binary(string='Acta Final de Supervisor', tracking=True)
    acta_final_supervisor_char = fields.Char(string='Acta Final de Supervisor (Char)', tracking=True)
    acta_final_supervisor_visible = fields.Boolean(default=True)
    acta_final_supervisor_id = fields.Many2one('drive', string='Documento Acta Final de Supervisor', tracking=True)

    def action_upload_acta_final_supervisor(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir acta final de supervisor',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'acta_final_supervisor_documento' 
            },
        }
    
    _cache_documentos_acta_final_supervisor = {}

    def abrir_documento_acta_final_supervisor(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'acta_final_supervisor_id' está configurado
        if self.acta_final_supervisor_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Acta Final de Supervisor',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.acta_final_supervisor_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.acta_final_supervisor_char:
            file_name_to_search = self.acta_final_supervisor_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_acta_final_supervisor:
                drive_document = self._cache_documentos_acta_final_supervisor[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_acta_final_supervisor[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'acta_final_supervisor_id' con el ID del documento encontrado
                self.write({'acta_final_supervisor_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Acta Final de Supervisor',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')
     

    acta_terminacion = fields.Binary(string='Acta de Terminación o Recibo', tracking=True)
    acta_terminacion_char = fields.Char(string='Acta de Terminación o Recibo (Char)', tracking=True)
    acta_terminacion_visible = fields.Boolean(default=True)
    acta_terminacion_id = fields.Many2one('drive', string='Documento Acta de Terminación o Recibo', tracking=True)

    def action_upload_acta_terminacion(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir acta de terminación o recibo',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'acta_terminacion_documento' 
            },
        }
    
    _cache_documentos_declaracion_juramentada_pdf = {}

    def abrir_documento_declaracion_juramentada_pdf(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'acta_terminacion_id' está configurado
        if self.acta_terminacion_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Acta de Terminación o Recibo',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.acta_terminacion_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.acta_terminacion_char:
            file_name_to_search = self.acta_terminacion_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_declaracion_juramentada_pdf:
                drive_document = self._cache_documentos_declaracion_juramentada_pdf[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_declaracion_juramentada_pdf[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'acta_terminacion_id' con el ID del documento encontrado
                self.write({'acta_terminacion_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Acta de Terminación o Recibo',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    acta_final_pago = fields.Binary(string='Acta Final de Pago', tracking=True)
    acta_final_pago_char = fields.Char(string='Acta Final de Pago (Char)', tracking=True)
    acta_final_pago_visible = fields.Boolean(default=True)
    acta_final_pago_id = fields.Many2one('drive', string='Documento Acta Final de Pago', tracking=True)

    def action_upload_acta_final_pago(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir acta final de pago',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'acta_final_pago_documento' 
            },
        }

    _cache_documentos_acta_final_pago = {}

    def abrir_documento_acta_final_pago(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'acta_final_pago_id' está configurado
        if self.acta_final_pago_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Acta Final de Pago',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.acta_final_pago_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.acta_final_pago_char:
            file_name_to_search = self.acta_final_pago_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_acta_final_pago:
                drive_document = self._cache_documentos_acta_final_pago[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_acta_final_pago[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'acta_final_pago_id' con el ID del documento encontrado
                self.write({'acta_final_pago_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Acta Final de Pago',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    acta_liquidacion = fields.Binary(string='Acta de Liquidación', tracking=True)
    acta_liquidacion_char = fields.Char(string='Acta de Liquidación (Char)', tracking=True)
    acta_liquidacion_visible = fields.Boolean(default=True)
    acta_liquidacion_id = fields.Many2one('drive', string='Documento Acta de Liquidación', tracking=True)

    def action_upload_acta_liquidacion(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir acta de liquidación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'acta_liquidacion_documento' 
            },
        }

    _cache_documentos_acta_liquidacion = {}

    def abrir_documento_acta_liquidacion(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'acta_liquidacion_id' está configurado
        if self.acta_liquidacion_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Acta de Liquidación',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.acta_liquidacion_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.acta_liquidacion_char:
            file_name_to_search = self.acta_liquidacion_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_acta_liquidacion:
                drive_document = self._cache_documentos_acta_liquidacion[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_acta_liquidacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'acta_liquidacion_id' con el ID del documento encontrado
                self.write({'acta_liquidacion_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Acta de Liquidación',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    certificado_cumplimiento = fields.Binary(string='Certificado de Cumplimiento', tracking=True)
    certificado_cumplimiento_char = fields.Char(string='Certificado de Cumplimiento (Char)', tracking=True)
    certificado_cumplimiento_visible = fields.Boolean(default=True)
    certificado_cumplimiento_id = fields.Many2one('drive', string='Documento Certificado de Cumplimiento', tracking=True)

    def action_upload_certificado_cumplimiento(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir certificado de cumplimiento',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'certificado_cumplimiento_documento' 
            },
        }

    _cache_documentos_certificado_cumplimiento = {}

    def abrir_documento_certificado_cumplimiento(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'certificado_cumplimiento_id' está configurado
        if self.certificado_cumplimiento_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Certificado de Cumplimiento',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.certificado_cumplimiento_id.id,
                'target': 'new',
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.certificado_cumplimiento_char:
            file_name_to_search = self.certificado_cumplimiento_char.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_certificado_cumplimiento:
                drive_document = self._cache_documentos_certificado_cumplimiento[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_certificado_cumplimiento[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'certificado_cumplimiento_id' con el ID del documento encontrado
                self.write({'certificado_cumplimiento_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Certificado de Cumplimiento',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')

    
    # Campo de selección para los tipos de proyectos o convenios
    tipo_proyecto = fields.Selection([
        ('convenio_interadministrativo', 'Convenio Interadministrativo'),
        ('contrato_interadministrativo', 'Contrato Interadministrativo'),
        ('anexos_carta_mandato', 'Anexos a Carta de Mandato (Cartas de Instrucción)'),
        ('contrato', 'Contrato'),
    ], string='Tipo de Proyecto/Convenio', required=False, tracking=True)


class OtrosArchivos(models.Model):
    _name = 'otros_archivos'
    _description = 'Otros documentos'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name_char = fields.Char(string='Nombre del Archivo', tracking=True)
    archivo_binario = fields.Binary(string='Archivo', tracking=True)
    modelo_principal_id = fields.Many2one('carpeta_principal', string='Modelo Principal')
    modelo_principal_poliza_id = fields.Many2one('carpeta_principal', string='Modelo Principal')
    modelo_principal_cdp_id = fields.Many2one('carpeta_principal', string='Modelo Principal')
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado')

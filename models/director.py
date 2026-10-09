#!/usr/bin/python

from odoo import fields, models, api
from odoo.exceptions import UserError
import io
import logging
import math
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
import json
from collections import defaultdict


CHART_COLORS = ['#0E7C6B', '#4C5FD5', '#E0A100', '#D64541', '#1F9D6B', '#8B5CF6',
                '#0EA5E9', '#F97316', '#64748B', '#DB2777', '#65A30D', '#0891B2']
CHART_HOVER_COLORS = ['#14958A', '#6373DE', '#F0B22B', '#E1665C', '#27B87F', '#A78BFA',
                      '#38BDF8', '#FB923C', '#7C8896', '#EC4899', '#84CC16', '#22B8D6']

# Paleta unificada del Dashboard Director (alineada con el mockup de Panel de Dirección)
DASH_TEAL = '#0E7C6B'
DASH_INDIGO = '#4C5FD5'
DASH_POS = '#1F9D6B'
DASH_WARN = '#E0A100'
DASH_NEG = '#D64541'
DASH_INK = '#0F1B2D'
DASH_MUTED = '#94A3B8'
MONTH_NAMES = {
    '01': 'Enero', '02': 'Febrero', '03': 'Marzo', '04': 'Abril',
    '05': 'Mayo', '06': 'Junio', '07': 'Julio', '08': 'Agosto',
    '09': 'Septiembre', '10': 'Octubre', '11': 'Noviembre', '12': 'Diciembre'
}

_logger = logging.getLogger(__name__)


class TABLA_DIRECTOR(models.Model):
    # ==========================================================
    # MODELO: TABLERO DE AVANCES (PROYECTOS)
    # ==========================================================

    _name = "tablero_avances"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string="Vista proyectos", related="nombre_proyecto_relacionado")
    hiden = fields.Char(string='hiden')

    # Nuevos campos desde la nueva MCB
    new_ejecucion_id = fields.Many2one('new_mcb', string='Relación con NUEVA MCB')
    valor_proyecto_convenio3 = fields.Float(related='new_ejecucion_id.valor_proyecto_convenio3', readonly=False)
    suma_adicion_otro_si2 = fields.Monetary(string="Entidad contratante", related="new_ejecucion_id.suma_adicion_otro_si2")
    valor_total2_final = fields.Float(string="Valor total2", currency='COP', related="new_ejecucion_id.valor_total2_final")
    suma_contrapartida4 = fields.Monetary(string="Contrapartida", related="new_ejecucion_id.suma_contrapartida4")
    valor_aportes2 = fields.Monetary(string="Valor total aportes", related="new_ejecucion_id.valor_aportes")
    new_total_costos_produccion_N_r = fields.Float(related="new_ejecucion_id.new_total_costos_produccion_N_r")


    costo_operativo1 = fields.Float(string='Costo operativo', related="new_ejecucion_id.costo_operativo1")

    # Campos antiguos
    ejecucion_id = fields.Many2one('mcb', string='Relación con MCB', ondelete='cascade')
    valor_por_proyecto_mcb = fields.Float(string="Valor total proyecto", related="new_ejecucion_id.valor_total2_final")
    adicion_otrosi = fields.Monetary(related='new_ejecucion_id.suma_adicion_otro_si2')
    contrapartida_avances = fields.Monetary(tracking=True, related='new_ejecucion_id.suma_contrapartida4')

    valor_aportes = fields.Monetary(string="Valor total proyecto", related='new_ejecucion_id.valor_aportes')
    valor_proyecto_convenio2 = fields.Float(related='new_ejecucion_id.new_total_costos_produccion_N_r')

    valor_cobrado = fields.Monetary(string='Valor Cobrado', compute='_compute_valor_cobrado_siesa', store=True)

    @api.depends('mcb_centro_costo')
    def _compute_valor_cobrado_siesa(self):
        for record in self:
            if record.mcb_centro_costo:
                registros = self.env['cobros.siesa.line'].sudo().search([('id_un', '=', record.mcb_centro_costo)])
                record.valor_cobrado = sum(registros.mapped('recaudado'))
            else:
                record.valor_cobrado = 0.0

    resultado_vp_vc_barra = fields.Float(string='Valor Cobrado', compute='_compute_valores_barra')

    #Se actualiza la division es con el valor total: valor cobrado / (la suma del proyecto + otrosi) * 100
    @api.depends('valor_cobrado', 'valor_total2_final')
    def _compute_valores_barra(self):
        for record in self:
            if record.valor_cobrado and record.valor_total2_final:
                porcentaje = (record.valor_cobrado / record.valor_total2_final) * 100
                # Verificar si el porcentaje calculado es mayor que 100
                if porcentaje > 100:
                    record.resultado_vp_vc_barra = 100.0  # Establecer el porcentaje en 100 si es mayor que 100
                else:
                    record.resultado_vp_vc_barra = porcentaje
            else:
                record.resultado_vp_vc_barra = 0.0



    pago_efectuado2_sumado = fields.Float(compute='_compute_pago_efectuado_siesa', string="Valor pagado", store=True)

    @api.depends('valor_pagado_dashboard')
    def _compute_pago_efectuado_siesa(self):
        for record in self:
            record.pago_efectuado2_sumado = record.valor_pagado_dashboard

    resultado_vpagado_barra = fields.Float(string='Valor Cobrado', compute='_compute_valores_pagado')

    # OJO: antes dependía de `pago_efectuado2_sumado` (store=True, línea 96), que solo se recalcula
    # cuando Odoo detecta un cambio en `valor_pagado_dashboard` a través del grafo de dependencias --
    # como los pagos de SIESA se cargan por cron/botón (no vía escritura ORM normal sobre este
    # registro), ese grafo nunca se dispara y el campo queda congelado en 0 para la mayoría de
    # proyectos existentes (92 de 127 verificados en vivo). Mismo patrón de bug ya encontrado y
    # corregido varias veces en vistas_director (ver _pagado_siesa_sum) -- aquí se usa
    # `valor_pagado_dashboard` directo. También se agrega el mismo fallback de presupuesto que ya usa
    # el cálculo de CPI/SPI (`valor_proyecto_convenio2 or valor_por_proyecto_mcb`), para los 4
    # proyectos donde convenio2 está en 0.
    @api.depends('valor_pagado_dashboard', 'valor_proyecto_convenio2', 'valor_por_proyecto_mcb')
    def _compute_valores_pagado(self):
        for record in self:
            presupuesto = record.valor_proyecto_convenio2 or record.valor_por_proyecto_mcb
            if record.valor_pagado_dashboard and presupuesto:
                porcentaje = (record.valor_pagado_dashboard / presupuesto) * 100
                record.resultado_vpagado_barra = min(porcentaje, 100.0)
            else:
                record.resultado_vpagado_barra = 0.0


    valor_por_proyecto_mcb_global = fields.Float(string="Valor global", related='new_ejecucion_id.valor_total2_final')
    valor_por_proyecto_mcb_global_sumado = fields.Float(string="Valor global", compute='_compute_suma_mcb_total')
    @api.depends('valor_por_proyecto_mcb_global')
    def _compute_suma_mcb_total(self):
        todos_los_proyectos = self.env['tablero_avances'].search([])
        suma_total = sum(todos_los_proyectos.mapped('valor_por_proyecto_mcb_global'))
        for record in self:
            record.valor_por_proyecto_mcb_global_sumado = suma_total
    resultado_peso_proyectos = fields.Float(compute="_compute_peso_proyectos")
    @api.depends('valor_por_proyecto_mcb_global_sumado', 'valor_por_proyecto_mcb')
    def _compute_peso_proyectos(self):
        for record in self:
            if record.valor_por_proyecto_mcb_global and record.valor_por_proyecto_mcb:
                porcentaje = (record.valor_por_proyecto_mcb / record.valor_por_proyecto_mcb_global_sumado) * 100
                record.resultado_peso_proyectos = porcentaje
            else:
                record.resultado_peso_proyectos = 0.0
    suma_total_porcentaje_ejecutado = fields.Float(string='Porcentaje Ejecutado Global', compute='_compute_suma_total')
    @api.depends('suma_total', 'resultado_peso_proyectos')
    def _compute_suma_total(self):
        for record in self:
            if record.suma_total is not None and record.resultado_peso_proyectos is not None:
                # Convertimos los porcentajes a valores decimales dividiendo por 100
                porcentaje1_decimal = record.suma_total / 100
                porcentaje2_decimal = record.resultado_peso_proyectos / 100

                # Multiplicamos los porcentajes en forma decimal
                resultado_decimal = porcentaje1_decimal * porcentaje2_decimal

                # Convertimos el resultado nuevamente a porcentaje multiplicando por 100
                resultado_porcentaje = resultado_decimal * 100

                record.suma_total_porcentaje_ejecutado = resultado_porcentaje
            else:
                record.suma_total_porcentaje_ejecutado = 0.0







    fecha_inicio2 = fields.Date(string="Fecha de inicio", related='ejecucion_id.fecha_inicio')




    fecha_finalizacion2 = fields.Date(string="Fecha de finalización", related='ejecucion_id.fecha_finalizacion')

    create_uid = fields.Many2one('res.users', compute='_compute_coordinador_asignado', string='Creador del proyecto: ', readonly=True, store=True)
    coordinador_asignado = fields.Many2one('res.users', compute='_compute_coordinador_asignado', string='Creador del proyecto: ', store=True)

    @api.depends('ejecucion_id.create_uid', 'new_ejecucion_id.create_uid')
    def _compute_coordinador_asignado(self):
        for record in self:
            if record.new_ejecucion_id:
                record.create_uid = record.new_ejecucion_id.create_uid
                record.coordinador_asignado = record.new_ejecucion_id.create_uid
            elif record.ejecucion_id:
                record.create_uid = record.ejecucion_id.create_uid
                record.coordinador_asignado = record.ejecucion_id.create_uid
            else:
                record.create_uid = False
                record.coordinador_asignado = False

    ejecucion_J_id = fields.Many2one('ejecucion', string='Ejecución', ondelete='cascade')
    avances_id_entregable = fields.One2many('entregable', 'avances_id', string='Relación de entregables')
    informacion_base_agregar_id2 = fields.Many2one('informacion_base_agregar', string='Informacion base', ondelete='cascade')
    suma_total_a_facturar = fields.Float(related='ejecucion_id.suma_total_a_facturar')











    texto_finalizado = fields.Text(string="Estado del Proyecto", default="Proyecto finalizado")
    SEMAFORO_1 = fields.Selection([
        ('verde', 'Verde'), ('amarillo', 'Amarillo'), ('naranja', 'Naranja'), ('rojo', 'Rojo')])
    semaforo_1_color = fields.Char(compute='_compute_semaforo_1_color', string='Semáforo Salud Financiera')
    semaforo_tiempo_color = fields.Char(compute='_compute_semaforo_tiempo_color', string='Color del Tiempo Restante')

    @api.depends('SEMAFORO_1', 'is_editable_avs')
    def _compute_semaforo_tiempo_color(self):
        for record in self:
            color = '#000000'
            if record.is_editable_avs:
                color = 'rgba(0,0,0,0)'
            elif record.SEMAFORO_1 == 'verde':
                color = '#22981b'
            elif record.SEMAFORO_1 == 'amarillo':
                color = '#d3a100'
            elif record.SEMAFORO_1 == 'naranja':
                color = '#fd7e14'
            elif record.SEMAFORO_1 == 'rojo':
                color = '#dc3545'
            record.semaforo_tiempo_color = color

    @api.depends('semaforo_financiero', 'is_editable_avs')
    def _compute_semaforo_1_color(self):
        for record in self:
            color = ''
            if record.is_editable_avs:  # Si es editable, el semáforo será blanco
                color = 'rgba(0,0,0,0)'  # Color invisible
            elif record.semaforo_financiero == 'verde':
                color = '#00FF00'
            elif record.semaforo_financiero == 'rojo':
                color = '#FF0000'
            record.semaforo_1_color = color

    @api.depends('ejecucion_id.fecha_finalizacion', 'new_ejecucion_id.fecha_finalizacion',
                 'new_ejecucion_id.otrosi_ids_new.fecha_finalizacion')
    def _compute_fecha_finalizacion_mcb_r(self):
        for record in self:
            # Un otrosí puede extender el plazo de ejecución del proyecto más allá de la fecha
            # original de la MCB -- si existe una fecha de finalización por otrosí, esa es la
            # fecha real vigente, no la original (evita marcar como "vencido" un proyecto que en
            # realidad tiene el plazo ampliado). OJO: no usar new_mcb.fecha_finalizacion_otrosi /
            # tiene_fecha_finalizacion_otrosi -- son store=True y quedan desactualizados igual que
            # valor_cobrado/pago_efectuado2_sumado; se recalcula aquí directo desde los otrosí hijos.
            fecha_efectiva = False
            if record.new_ejecucion_id:
                fechas_otrosi = [f for f in record.new_ejecucion_id.otrosi_ids_new.mapped('fecha_finalizacion') if f]
                if fechas_otrosi:
                    fecha_efectiva = max(fechas_otrosi)
                else:
                    fecha_efectiva = record.new_ejecucion_id.fecha_finalizacion
            record.fecha_finalizacion_mcb_r = fecha_efectiva or record.ejecucion_id.fecha_finalizacion

    @api.depends('fecha_finalizacion_mcb_r', 'ejecucion_id.is_editable_mcb')
    def _compute_tiempo_restante(self):
        for proyecto in self:
            if proyecto.fecha_finalizacion_mcb_r:
                fecha_finalizacion_mcb_r = fields.Datetime.from_string(proyecto.fecha_finalizacion_mcb_r)
                fecha_actual = fields.Datetime.now()
                diferencia = fecha_finalizacion_mcb_r - fecha_actual
                dias_totales = diferencia.days

                proyecto.dias_restantes = dias_totales

                if dias_totales > 15:
                    proyecto.SEMAFORO_1 = 'verde'
                elif dias_totales > 10:
                    proyecto.SEMAFORO_1 = 'amarillo'
                elif dias_totales > 5:
                    proyecto.SEMAFORO_1 = 'naranja'
                else:
                    proyecto.SEMAFORO_1 = 'rojo'

                if dias_totales > 360:
                    años = dias_totales // 360
                    dias_sobrantes = dias_totales % 360
                    proyecto.tiempo_restante = str(años) + " año(s) y " + str(dias_sobrantes) + " día(s)"
                else:
                    proyecto.tiempo_restante = str(dias_totales) + " día(s)"
            else:
                proyecto.tiempo_restante = "Fecha de finalización no especificada"
                proyecto.dias_restantes = 0

    fecha_finalizacion_mcb_r = fields.Date(string="Fecha de finalización", compute='_compute_fecha_finalizacion_mcb_r')
    tiempo_restante = fields.Char(compute='_compute_tiempo_restante',
                                  string="Tiempo restante al proyecto")
    dias_restantes = fields.Integer(compute='_compute_tiempo_restante',
                                    string="Días faltantes", store=True)
    is_editable_avs = fields.Boolean(string='Es editable', related="ejecucion_id.is_editable_mcb", default=False, groups="Matriz.group_contratacion_directivo" )

    @api.onchange('is_editable_avs')
    def _onchange_is_editable_avs(self):
        # Este método se llama cuando cambia el valor de is_editable_cp
        # Puedes agregar lógica adicional aquí si es necesario
        pass


    # GRAFICA PORCENTAJES DE EJECUCION

    @api.depends('valor_por_proyecto_mcb', 'resultado_tecnica_representada_dinero', 'pago_efectuado2_sumado')
    def _compute_animated_bar_chart_data(self):
        for record in self:
            chart_data = self._get_animated_bar_chart_data(
                record.valor_por_proyecto_mcb, record.resultado_tecnica_representada_dinero,
                record.pago_efectuado2_sumado
            )
            record.animated_bar_chart_data = json.dumps(chart_data)

    def _get_animated_bar_chart_data(self, valor_por_proyecto_mcb, resultado_tecnica_representada_dinero,
                                     pago_efectuado2_sumado):
        try:
            return {
                'labels': ['Valores'],
                'datasets': [
                    {
                        'label': 'Valor del proyecto',
                        'data': [valor_por_proyecto_mcb],
                        'backgroundColor': 'rgba(52, 152, 219, 0.6)',  # Nuevo color para Valor por Proyecto
                        'borderColor': 'rgba(41, 128, 185, 1)',  # Nuevo borde para Valor por Proyecto
                        'borderWidth': 1
                    },
                    {
                        'label': 'Ejecución técnica representada en dinero',
                        'data': [resultado_tecnica_representada_dinero],
                        'backgroundColor': 'rgba(46, 204, 113, 0.6)',  # Nuevo color para Resultado Técnica
                        'borderColor': 'rgba(39, 174, 96, 1)',  # Nuevo borde para Resultado Técnica
                        'borderWidth': 1
                    },
                    {
                        'label': 'Ejecución financiera',
                        'data': [pago_efectuado2_sumado],
                        'backgroundColor': 'rgba(231, 76, 60, 0.6)',  # Nuevo color para Valor Pagado
                        'borderColor': 'rgba(192, 57, 43, 1)',  # Nuevo borde para Valor Pagado
                        'borderWidth': 1
                    }

                ]
            }
        except Exception as e:
            _logger.error(f"Error in _get_animated_bar_chart_data: {e}")
            return {}

    animated_bar_chart_data = fields.Char(string="Datos del Gráfico Animado",
                                          compute="_compute_animated_bar_chart_data")


    def actualizar_grafica_barras(self):
        # Devolver la acción para abrir el formulario del registro actual
        action = {
            'name': 'Tablero de Avances',
            'type': 'ir.actions.act_window',
            'res_model': 'tablero_avances',
            'view_mode': 'form',
            'res_id': self.id,  # ID del registro actual
            'target': 'current',
        }

        return action



    #Entrar a actividad

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

        #Devolver una acción para ver el registro correspondiente a la subactividad actual en
        #Formato formulario y árbol

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


    suma_valor_total_contrato = fields.Monetary(related='ejecucion_id.suma_valor_total_contrato3')
    resultado_suma_vp_barra = fields.Monetary(compute='_compute_valores_contratado')

    #Barra entre Valor a ejecutar y Valor contratado

    @api.depends('suma_valor_total_contrato', 'valor_proyecto_convenio2')
    def _compute_valores_contratado(self):
        for record in self:
            if record.suma_valor_total_contrato and record.valor_proyecto_convenio2:
                porcentaje = (record.suma_valor_total_contrato / record.valor_proyecto_convenio2) * 100
                # Verificar si el porcentaje calculado es mayor que 100
                if porcentaje > 100:
                    record.resultado_suma_vp_barra = 100.0  # Establecer el porcentaje en 100 si es mayor que 100
                else:
                    record.resultado_suma_vp_barra = porcentaje
            else:
                record.resultado_suma_vp_barra = 0.0


    currency_id = fields.Many2one('res.currency', string='Moneda')
    porcentaje_total_ejecutado = fields.Float(string="Porcentaje ejecutado",
                                              compute='_compute_porcentaje_total_ejecutado')


    suma_total_porcentaje_ejecutado2 = fields.Float(compute='_compute_suma_suma_total_porcentaje_ejecutado')

    @api.depends('suma_total_porcentaje_ejecutado')
    def _compute_suma_suma_total_porcentaje_ejecutado(self):
        todos_los_proyectos = self.env['tablero_avances'].search([])
        suma_total = sum(todos_los_proyectos.mapped('suma_total_porcentaje_ejecutado'))
        for record in self:
            record.suma_total_porcentaje_ejecutado2 = suma_total

    ###Funcion procentaje ejecutado

    @api.depends('valor_por_proyecto_mcb', 'pago_efectuado2_sumado')
    def _compute_porcentaje_total_ejecutado(self):
        for record in self:
            if record.valor_proyecto_convenio2 and record.pago_efectuado2_sumado:
                porcentaje = (record.pago_efectuado2_sumado / record.valor_proyecto_convenio2) * 100
                record.porcentaje_total_ejecutado = porcentaje
            else:
                record.porcentaje_total_ejecutado = 0.0


    ejecucion_O_id = fields.Many2one('objetivo', string='Ejecución')
    contrapartida_mcb = fields.Monetary(related='ejecucion_id.contrapartida')
    resta_contrapartida = fields.Monetary(compute='_compute_contrapartida_mcb')

    @api.depends('valor_por_proyecto_mcb', 'contrapartida_mcb')
    def _compute_contrapartida_mcb(self):
        for record in self:
            if record.valor_por_proyecto_mcb and record.contrapartida_mcb:
                resta = (record.valor_por_proyecto_mcb - record.contrapartida_mcb)

                record.resta_contrapartida = resta
            else:
                record.resta_contrapartida = 0.0

    # Barra entre Valor a ejecutar y Valor contratado

    saldo_disponible = fields.Monetary(related='ejecucion_J_id.saldo_disponible')

    cantidad_lineas_tabla_pagos = fields.Integer(related="ejecucion_J_id.cantidad_lineas_tabla_pagos")
    faltante_por_cobrar = fields.Monetary(related="ejecucion_J_id.faltante_por_cobrar")

    suma_valor_cobrado = fields.Float(string='Suma de Valor Cobrado', compute='_compute_suma_valor_cobrado')

    @api.depends('valor_cobrado')
    def _compute_suma_valor_cobrado(self):
        todos_los_proyectos = self.search([])
        suma_total = sum(todos_los_proyectos.mapped('valor_cobrado'))
        for record in self:
            record.suma_valor_cobrado = suma_total





    # Función para actulizar la variable pago_efectuado2_sumado
    tabla_director_ids = fields.One2many('tablero_avances', 'ejecucion_J_id', string='Tablero de Avances')

    @api.onchange('pago_efectuado2_sumado')
    def _onchange_pago_efectuado2_sumado(self):
        for record in self:
            # Actualizar automáticamente el campo correspondiente en el modelo TableroAvances
            record.tabla_director_ids.pago_efectuado2_sumado = record.pago_efectuado2_sumado

            # Actualizar la relación ejecucion_J_id en el modelo tablero_avances
            record.tabla_director_ids.write({
                'ejecucion_O_id': [(6, 0, [record.id])],
                'pago_efectuado2_sumado': record.pago_efectuado2_sumado,
                'valor_cobrado': record.valor_cobrado,  # Actualizar la variable valor_cobrado
            })


    ejecucion_A_id = fields.Many2one('actividad', string='Actividad')
    # Campo Many2one para la relación con VistasDirector
    vistas_director_id = fields.Many2one('vistas_director', string='Vistas Director',
                                         default=lambda self: self.env['vistas_director'].search([], limit=1))
    # CAMPO LLAMADO PARA A VISTA GLOBAL####################################




    valor_proyecto_convenio_real = fields.Float(related='ejecucion_id.valor_proyecto_convenio2')

    valor_sobre_costo = fields.Monetary(string="Valor global", related='ejecucion_id.valor_sobre_costo')

    ##Funcionalidad para deducir la ejecución tecnica representada en dinero
    suma_total = fields.Float(related="ejecucion_J_id.suma_total")
    resultado_tecnica_representada_dinero = fields.Monetary(string="Resultado Técnica Representada Dinero",
                                                            compute='_compute_resultado_tecnica_representada_dinero')

    @api.depends('valor_proyecto_convenio2', 'suma_total')
    def _compute_resultado_tecnica_representada_dinero(self):
        for record in self:
            if record.suma_total and record.valor_proyecto_convenio2:
                # Convertir el porcentaje a un valor decimal con 4 decimales máximo
                porcentaje_decimal = round(record.suma_total / 100.0, 2)
                # Calcular el resultado multiplicando el valor del proyecto por el porcentaje decimal
                record.resultado_tecnica_representada_dinero = record.valor_proyecto_convenio2 * porcentaje_decimal
            else:
                record.resultado_tecnica_representada_dinero = 0.0

    # MODELOS DE ACTIVIDADES EJECUCION POR PROYECTO########################
    nombre_proyecto_relacionado = fields.Char(string="Nombre del Proyecto", related='ejecucion_id.new_name')
    porcentaje_ejecucion = fields.Float(related='ejecucion_A_id.porcentaje_ejecucion')
    fecha_creacion = fields.Date(string="Fecha de inicio")
    tiempo_ejecucion = fields.Float()
    valor_faltante_epor_ejecutar = fields.Float(string="Porcentaje Total por Ejecutar",
                                                compute='_compute_valor_faltante_epor_ejecutar')
    resultado_division = fields.Float(string='Ejecución', related='ejecucion_A_id.resultado_division')

    ##Ir a ejecución
    def ir_a_tablero_ejecucion(self):
        # Lógica para abrir la vista de Kanban de ejecución
        action = {
            'name': 'Ejecuciones',
            'type': 'ir.actions.act_window',
            'res_model': 'ejecucion',
            'view_mode': 'kanban,tree,form',
            'domain': [('id', '=', self.ejecucion_J_id.id)],
            'target': 'current',
        }
        return action

    ####Función peso




    ##Función resta para sacar valor faltante por ejecutar

    @api.depends('valor_proyecto_convenio2', 'pago_efectuado2_sumado')
    def _compute_valor_faltante_epor_ejecutar(self):
        for record in self:
            if record.valor_proyecto_convenio2 and record.pago_efectuado2_sumado:
                porcentaje = (record.valor_proyecto_convenio2 - record.pago_efectuado2_sumado)
                record.valor_faltante_epor_ejecutar = porcentaje
            else:
                record.valor_faltante_epor_ejecutar = 0.0

    ejecutado1 = fields.Float(string='Ejecutado', related='ejecucion_J_id.ejecutado1', store=True)
    resultado_chart_data = fields.Char(string="Datos del gráfico")

    prioridad = fields.Selection([
        ('0', 'Baja'),
        ('1', 'Media'),
        ('2', 'Alta'),
        ('3', 'Crítica')
    ], string='Prioridad', default='1', tracking=True)

    mcb_centro_costo = fields.Char(string='Centro de costo', compute='_compute_mcb_centro_costo', store=True)
    costo_real_siesa = fields.Monetary(string="Costo Real (Siesa)", compute="_compute_costo_real_siesa", store=False, currency_field='currency_id')
    fuente_datos_costo = fields.Selection([
        ('odoo', 'Contratos Odoo (Interno)'),
        ('siesa', 'Contabilidad Siesa (ERP)')
    ], string="Origen Costo Real", default='siesa', tracking=True)

    valor_pagado_dashboard = fields.Float(string="Valor Pagado (Dashboard)", compute="_compute_valor_pagado_dashboard", store=False)
    cpi_proyecto = fields.Float(string="CPI", compute="_compute_proyecto_evm", store=False)
    spi_proyecto = fields.Float(string="SPI", compute="_compute_proyecto_evm", store=False)
    semaforo_financiero = fields.Selection([
        ('verde', 'Verde'),
        ('rojo', 'Rojo')
    ], string="Salud Financiera", compute="_compute_proyecto_evm", store=True)

    conciliacion_line_ids = fields.One2many(
        'tablero_avances.conciliacion.line', 
        'tablero_id', 
        string="Líneas de Conciliación ERP",
        compute="_compute_conciliacion_lines",
        store=False
    )

    @api.depends('ejecucion_id.centro_costo', 'new_ejecucion_id.centro_costo')
    def _compute_mcb_centro_costo(self):
        for record in self:
            record.mcb_centro_costo = record.new_ejecucion_id.centro_costo or record.ejecucion_id.centro_costo or False

    @api.depends('mcb_centro_costo')
    def _compute_costo_real_siesa(self):
        for record in self:
            if record.mcb_centro_costo:
                # Buscar todos los registros de tipo Pago para esta UN
                registros = self.env['pagos.siesa.line'].sudo().search([
                    ('id_un', '=', record.mcb_centro_costo),])
                # El costo real en Siesa es la sumatoria de débitos (egresos) - créditos (anulaciones/devoluciones)
                record.costo_real_siesa = sum(registros.mapped('valor_pagado'))
            else:
                record.costo_real_siesa = 0.0

    @api.depends('costo_real_siesa')
    def _compute_valor_pagado_dashboard(self):
        for record in self:
            record.valor_pagado_dashboard = record.costo_real_siesa or 0.0

    @api.depends('valor_proyecto_convenio2', 'valor_por_proyecto_mcb', 'resultado_tecnica_representada_dinero', 'suma_total', 'valor_pagado_dashboard', 'fecha_inicio2', 'fecha_finalizacion_mcb_r', 'SEMAFORO_1')
    def _compute_proyecto_evm(self):
        for proj in self:
            bac = proj.valor_proyecto_convenio2 or proj.valor_por_proyecto_mcb or 0.0
            ev = proj.resultado_tecnica_representada_dinero or 0.0
            if not ev and bac and proj.suma_total:
                ev = bac * (proj.suma_total / 100.0)
            
            # AC (Costo Real seleccionado)
            ac = proj.valor_pagado_dashboard or 0.0
            
            # CPI = EV / AC
            if ac > 0:
                cpi = ev / ac
            else:
                cpi = 1.0  # Si no hay costo real (AC = 0), la salud de costo es óptima por defecto
            
            # PV (Valor Planificado)
            pv = 0.0
            if proj.fecha_inicio2 and proj.fecha_finalizacion_mcb_r:
                inicio = fields.Date.from_string(proj.fecha_inicio2)
                fin = fields.Date.from_string(proj.fecha_finalizacion_mcb_r)
                hoy = fields.Date.context_today(self)
                
                dias_totales = (fin - inicio).days
                if dias_totales > 0:
                    dias_transcurridos = (hoy - inicio).days
                    dias_transcurridos = max(0, min(dias_transcurridos, dias_totales))
                    proporcion_tiempo = dias_transcurridos / float(dias_totales)
                    pv = bac * proporcion_tiempo
                else:
                    pv = bac
            else:
                pv = bac
            
            # SPI = EV / PV
            if pv > 0:
                spi = ev / pv
            else:
                spi = 1.0  # Si no hay valor planificado (PV = 0), la salud de cronograma es óptima por defecto
                
            proj.cpi_proyecto = round(cpi, 2)
            proj.spi_proyecto = round(spi, 2)
            
            # Determinar salud financiera: Rojo si CPI < 1.0
            if cpi < 1.0:
                proj.semaforo_financiero = 'rojo'
            else:
                proj.semaforo_financiero = 'verde'

    def _compute_conciliacion_lines(self):
        for record in self:
            lineas_vals = [(5, 0, 0)]
            terceros = set()
            
            contratos_odoo = {}
            if record.id:
                # Buscamos contrataciones de Odoo para este proyecto
                contrataciones = self.env['contratacion'].sudo().search([('tablero_avances_ids', 'in', [record.id])])
                contratos = self.env['contrataciones_fuv'].sudo().search([
                    ('proyecto_relacionado', 'in', contrataciones.ids),
                    ('contratista_proveedor_relacionado', '!=', False)
                ])
                for contr in contratos:
                    partner = contr.contratista_proveedor_relacionado
                    nit = partner.vat or ''
                    name = partner.name or 'Sin Nombre'
                    key = (nit, name)
                    monto = contr.valor_total_contrato or 0.0
                    if contr.valor_contrato_otrosi:
                        monto += contr.valor_contrato_otrosi
                    contratos_odoo[key] = contratos_odoo.get(key, 0.0) + monto
                    terceros.add(key)
            
            pagos_siesa = {}
            if record.mcb_centro_costo:
                registros = self.env['pagos.siesa.line'].sudo().search([
                    ('id_un', '=', record.mcb_centro_costo),])
                for reg in registros:
                    nit = reg.nit or ''
                    name = reg.contratista or 'Sin Nombre'
                    key = (nit, name)
                    monto = reg.valor_pagado
                    pagos_siesa[key] = pagos_siesa.get(key, 0.0) + monto
                    terceros.add(key)
            
            consolidado = {}
            for (nit, name) in terceros:
                match_key = None
                if nit:
                    for k in consolidado.keys():
                        if k[0] == nit:
                            match_key = k
                            break
                if match_key:
                    key = match_key
                else:
                    key = (nit, name)
                    consolidado[key] = {'odoo': 0.0, 'siesa': 0.0}
                
                consolidado[key]['odoo'] += contratos_odoo.get((nit, name), 0.0)
                consolidado[key]['siesa'] += pagos_siesa.get((nit, name), 0.0)

            for (nit, name), montos in consolidado.items():
                lineas_vals.append((0, 0, {
                    'nit': nit,
                    'contratista': name,
                    'monto_contratado_odoo': montos['odoo'],
                    'monto_pagado_siesa': montos['siesa'],
                }))
                
            record.conciliacion_line_ids = lineas_vals




class TableroAvancesConciliacionLine(models.Model):
    # ==========================================================
    # MODELO: LÍNEAS DE CONCILIACIÓN
    # ==========================================================

    _name = "tablero_avances.conciliacion.line"
    _description = "Línea de Conciliación de Contratista"

    tablero_id = fields.Many2one('tablero_avances', string="Tablero", ondelete='cascade')
    nit = fields.Char(string="NIT/Cédula")
    contratista = fields.Char(string="Contratista / Proveedor")
    monto_contratado_odoo = fields.Float(string="Contratado (Odoo)")
    monto_pagado_siesa = fields.Float(string="Pagado (Siesa)")
    desviacion = fields.Float(string="Desviación / Saldo Restante", compute="_compute_desviacion", store=False)
    alerta_sobrecosto = fields.Boolean(string="Sobrepasa Contratado", compute="_compute_desviacion", store=False)

    @api.depends('monto_contratado_odoo', 'monto_pagado_siesa')
    def _compute_desviacion(self):
        for record in self:
            record.desviacion = record.monto_contratado_odoo - record.monto_pagado_siesa
            record.alerta_sobrecosto = record.monto_pagado_siesa > record.monto_contratado_odoo


class VistasDirector(models.Model):
    # ==========================================================
    # MODELO: VISTAS DEL DIRECTOR Y CONSOLIDADOS
    # ==========================================================

    _name = 'vistas_director'
    _description = 'Vistas del Director'

    name = fields.Char(string="Vistas del Director")

    nombre_proyecto = fields.Many2one('tablero_avances', string="Nombre del Proyecto")
    proyecto_id = fields.Many2one('action_ingresar_pagos_proyecto', string="Proyecto")


    #nuevo#
    def action_open_first_record(self):
        record = self.search([], limit=1)
        if record:
            record.write({'seleccionador_graficas': '01'})
            return {
                "type": "ir.actions.act_window",
                "res_model": "vistas_director",
                "view_mode": "form",
                "res_id": record.id,
                "target": "current",
            }
        else:
            return {
                "type": "ir.actions.act_window",
                "res_model": "vistas_director",
                "view_mode": "kanban,tree,form",
                "target": "current",
            }


    tecnica = fields.Monetary(related='nombre_proyecto.resultado_tecnica_representada_dinero', tracking=True)
    valor_total = fields.Float(related='nombre_proyecto.valor_por_proyecto_mcb', tracking=True)
    coordinador_asignado = fields.Many2one(related='nombre_proyecto.coordinador_asignado')
    contratos_count = fields.Integer(string="Cantidad de Contratos", compute='_compute_contratos_count', store=False)
    tiempo_restante = fields.Char(related='nombre_proyecto.tiempo_restante')
    suma_total = fields.Float(related="nombre_proyecto.suma_total")
    pago_efectuado2_sumado = fields.Float(related='nombre_proyecto.valor_pago_2suma2', string="Valor pagado")
    valor_cobrado = fields.Monetary(related='nombre_proyecto.valor_cobrado')

    cobrado_proyecto = fields.Monetary(string="Cobros Acumulados (Proyecto)", compute='_compute_financiero_proyecto')
    pagado_proyecto = fields.Monetary(string="Pagos Acumulados (Proyecto)", compute='_compute_financiero_proyecto')
    flujo_neto_proyecto = fields.Monetary(string="Flujo Neto Acumulado (Proyecto)", compute='_compute_financiero_proyecto')

    # % ejecución financiera del proyecto -- NO depende de Ejecución Técnica (`suma_total`, línea 787
    # cubre esa otra métrica). `nombre_proyecto.resultado_vpagado_barra` daba 0% para la mayoría de
    # proyectos por un bug de raíz ya corregido (ver `_compute_valores_pagado` en TABLA_DIRECTOR: leía
    # un campo store=True que nunca se recalculaba); una vez arreglado ahí, se puede reusar tal cual.
    financiera_pct_proyecto = fields.Float(string="Ejecución Financiera % (Proyecto)", related='nombre_proyecto.resultado_vpagado_barra')
    fecha_fin_proyecto = fields.Date(string="Fin del Proyecto", related='nombre_proyecto.fecha_finalizacion_mcb_r')

    ultima_sync_proyecto = fields.Datetime(string="Última sincronización SIESA (Proyecto)", compute='_compute_ultima_sync_proyecto')

    @api.depends('nombre_proyecto')
    def _compute_ultima_sync_proyecto(self):
        for record in self:
            cc = record.nombre_proyecto.mcb_centro_costo if record.nombre_proyecto else False
            fecha = False
            if cc:
                cobros = self.env['cobros.siesa.line'].sudo().search([('id_un', '=', cc)], order='write_date desc', limit=1)
                pagos = self.env['pagos.siesa.line'].sudo().search([('id_un', '=', cc)], order='write_date desc', limit=1)
                fechas = [r.write_date for r in (cobros, pagos) if r]
                fecha = max(fechas) if fechas else False
            record.ultima_sync_proyecto = fecha

    ultima_sync_coordinador = fields.Datetime(string="Última sincronización SIESA (Coordinador)", compute='_compute_ultima_sync_coordinador')

    @api.depends('coordinador_id')
    def _compute_ultima_sync_coordinador(self):
        # Mismo patrón que _compute_ultima_sync_proyecto, pero sobre todos los centros de costo de los
        # proyectos del coordinador en vez de uno solo.
        for record in self:
            fecha = False
            if record.coordinador_id:
                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                ccs = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]
                if ccs:
                    cobros = self.env['cobros.siesa.line'].sudo().search([('id_un', 'in', ccs)], order='write_date desc', limit=1)
                    pagos = self.env['pagos.siesa.line'].sudo().search([('id_un', 'in', ccs)], order='write_date desc', limit=1)
                    fechas = [r.write_date for r in (cobros, pagos) if r]
                    fecha = max(fechas) if fechas else False
            record.ultima_sync_coordinador = fecha

    def action_volver_global(self):
        self.ensure_one()
        self.seleccionador_graficas = '01'

    @api.depends('nombre_proyecto')
    def _compute_financiero_proyecto(self):
        # Acumulado histórico total del proyecto (no filtrado por año), exclusivamente SIESA -- mismo
        # criterio que _compute_global_financiero, pero para un solo proyecto. `valor_cobrado` (related,
        # store=True) queda desactualizado igual que en el caso global, así que se recalcula directo.
        for record in self:
            proy = record.nombre_proyecto
            if proy:
                pagado = proy.valor_pagado_dashboard or 0.0
                cobrado = 0.0
                if proy.mcb_centro_costo:
                    cobrado = sum(self.env['cobros.siesa.line'].sudo().search(
                        [('id_un', '=', proy.mcb_centro_costo)]).mapped('recaudado'))
                record.cobrado_proyecto = cobrado
                record.pagado_proyecto = pagado
                record.flujo_neto_proyecto = cobrado - pagado
            else:
                record.cobrado_proyecto = 0.0
                record.pagado_proyecto = 0.0
                record.flujo_neto_proyecto = 0.0

    @api.onchange('nombre_proyecto')
    def _onchange_nombre_proyecto_sync_siesa(self):
        """Actualiza el año por defecto cuando se elige un proyecto en el dashboard."""
        if self.nombre_proyecto:
            anios_proyecto = []
            if self.nombre_proyecto.mcb_centro_costo:
                pagos_siesa = self.env['pagos.siesa.line'].sudo().search([
                    ('id_un', '=', self.nombre_proyecto.mcb_centro_costo),
                    ('fecha_pago', '!=', False)
                ], order='fecha_pago desc', limit=1)
                if pagos_siesa:
                    anios_proyecto.append(pagos_siesa.fecha_pago.year)
                    
                cobros_siesa = self.env['cobros.siesa.line'].sudo().search([
                    ('id_un', '=', self.nombre_proyecto.mcb_centro_costo),
                    ('fecha_recaudo', '!=', False)
                ], order='fecha_recaudo desc', limit=1)
                if cobros_siesa:
                    anios_proyecto.append(cobros_siesa.fecha_recaudo.year)
                    
            if anios_proyecto:
                max_year = max(anios_proyecto)
                anio_actual = datetime.now().year
                if 2000 <= max_year <= anio_actual + 1:
                    self.seleccion_anio_proyecto = str(max_year)

    # Campos globales
    tecnica_global = fields.Monetary(string="Técnica Global", compute='_compute_global_values', store=False)
    contratos_count_global = fields.Integer(string="Total de Contratos", compute='_compute_global_values', store=False)
    valor_total_global = fields.Float(string="Valor Total Global", compute='_compute_global_values', store=False)

    @api.depends('nombre_proyecto')
    def _compute_global_values(self):
        # Obtener todos los proyectos únicos
        proyectos = self.env['tablero_avances'].sudo().search([])

        # Calcular los valores globales
        tecnica_global = sum(proyectos.mapped('resultado_tecnica_representada_dinero'))
        contrataciones = self.env['contratacion'].sudo().search([('tablero_avances_ids', 'in', proyectos.ids)])
        contratos_count_global = self.env['contrataciones_fuv'].sudo().search_count([
            ('proyecto_relacionado', 'in', contrataciones.ids)
        ])
        valor_total_global = sum(proyectos.mapped('valor_por_proyecto_mcb'))

        # Asignar los valores calculados a cada registro
        for record in self:
            record.tecnica_global = tecnica_global
            record.contratos_count_global = contratos_count_global
            record.valor_total_global = valor_total_global

    # ==========================================================
    # PANEL DE DIRECCIÓN · REDISEÑO MODO GLOBAL (paleta unificada)
    # ==========================================================
    def _fmt_cop_millones(self, value):
        """Formatea un valor en pesos como '$ X.XXX M' (miles de millones -> millones, notación es-CO)."""
        millones = (value or 0.0) / 1e6
        texto = f"{millones:,.0f}"
        return f"$ {texto.replace(',', '.')} M"

    contratado_global = fields.Monetary(string="Presupuesto Contratado (Global)", compute='_compute_global_financiero')
    pagado_global = fields.Monetary(string="Pagos Acumulados (Global)", compute='_compute_global_financiero')
    cobrado_global = fields.Monetary(string="Cobros Acumulados (Global)", compute='_compute_global_financiero')
    flujo_neto_global = fields.Monetary(string="Flujo Neto Acumulado (Global)", compute='_compute_global_financiero')
    ejecucion_financiera_global_pct = fields.Float(string="% Ejecución Financiera (Global)", compute='_compute_global_financiero')
    recaudo_global_pct = fields.Float(string="% Recaudo sobre Contratado (Global)", compute='_compute_global_financiero')
    tecnica_global_pct = fields.Float(string="% Ejecución Técnica (Global)", compute='_compute_global_financiero')

    def _pagado_siesa_sum(self, proyectos):
        # `valor_pagado_dashboard` está indexado por centro de costo (mcb_centro_costo), no por
        # proyecto: cuando dos o más `tablero_avances` comparten el mismo centro (5 casos reales en
        # esta BD, ej. los proyectos 76 y 77 con el centro "15-172"), cada uno reporta el mismo total
        # completo. Sumar `proyectos.mapped('valor_pagado_dashboard')` cuenta esos centros 2-3 veces
        # (~$12.000M de pagos fantasma verificados sobre el total global). Aquí se suma directo desde
        # pagos.siesa.line sobre los centros ÚNICOS del conjunto, igual que ya se hace para cobrado.
        ccs = list({p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo})
        if not ccs:
            return 0.0
        return sum(self.env['pagos.siesa.line'].sudo().search([('id_un', 'in', ccs)]).mapped('valor_pagado'))

    @api.depends('nombre_proyecto', 'valor_total_global')
    def _compute_global_financiero(self):
        # Todo se calcula en tiempo real (sin cron por ahora): agregados sobre todos los tablero_avances.
        proyectos = self.env['tablero_avances'].sudo().search([])
        # OJO: `contratacion.tablero_avances_ids` está vacío en el 100% de los registros de esta BD
        # (verificado en vivo), así que buscar contrataciones por ese campo siempre da 0. La ruta que
        # sí está poblada (127/127 proyectos) es tablero_avances.ejecucion_id.contrataciones_id.
        contrataciones = proyectos.mapped('ejecucion_id.contrataciones_id')
        contratos_fuv = self.env['contrataciones_fuv'].sudo().search([('proyecto_relacionado', 'in', contrataciones.ids)])

        contratado = sum(contratos_fuv.mapped('valor_total_contrato')) + sum(contratos_fuv.mapped('valor_contrato_otrosi'))
        # `pago_efectuado2_sumado` es un campo store=True que quedó desactualizado para los registros
        # existentes (su compute solo copia valor_pagado_dashboard, pero nunca se recalculó) -> se suma
        # la fuente real directamente en vez del campo obsoleto. Ver `_pagado_siesa_sum` para el bug de
        # doble conteo por centros de costo compartidos que esto evita.
        pagado = self._pagado_siesa_sum(proyectos)
        # `valor_cobrado` (store=True, depende solo de mcb_centro_costo) tiene el mismo problema:
        # nunca se recalcula cuando llegan cobros nuevos a SIESA, así que queda congelado en 0 para la
        # mayoría de proyectos (verificado en vivo: en una muestra de 30 proyectos sumaba $0 almacenado
        # contra ~$59.700 M reales). Se recalcula aquí directo desde cobros.siesa.line.
        ccs_cobrado = [cc for cc in proyectos.mapped('mcb_centro_costo') if cc]
        cobrado = sum(self.env['cobros.siesa.line'].sudo().search([('id_un', 'in', ccs_cobrado)]).mapped('recaudado')) if ccs_cobrado else 0.0
        tecnica = sum(proyectos.mapped('resultado_tecnica_representada_dinero'))

        for record in self:
            record.contratado_global = contratado
            record.pagado_global = pagado
            record.cobrado_global = cobrado
            record.flujo_neto_global = cobrado - pagado
            record.ejecucion_financiera_global_pct = (pagado / contratado * 100) if contratado else 0.0
            # Recaudo es el flujo que ENTRA (del financiador): se mide contra el Presupuesto Total
            # (lo que se espera cobrar), no contra lo Contratado (que es lo que se promete PAGAR a
            # contratistas -- un compromiso aparte, sin relación con el financiador).
            presupuesto_total = record.valor_total_global or 0.0
            record.recaudo_global_pct = (cobrado / presupuesto_total * 100) if presupuesto_total else 0.0
            record.tecnica_global_pct = (tecnica / contratado * 100) if contratado else 0.0

    # ---- Pipeline: Contratado -> Ejecutado (técnico) -> Pagado -> Cobrado ----
    pipeline_financiero_html = fields.Html(string="Composición Financiera", compute='_compute_pipeline_financiero_html')

    @api.depends('contratado_global', 'pagado_global', 'cobrado_global', 'tecnica_global_pct', 'tecnica_global')
    def _compute_pipeline_financiero_html(self):
        for record in self:
            contratado = record.contratado_global or 0.0
            tecnica_total = record.tecnica_global or 0.0
            pasos = [
                ("Contratado", contratado, 100.0, DASH_INK),
                ("Ejecutado (técnico) <span class=\"badge-en-construccion\">En construcción</span>", tecnica_total, record.tecnica_global_pct, DASH_INDIGO),
                ("Pagado", record.pagado_global or 0.0, record.ejecucion_financiera_global_pct, DASH_WARN),
                ("Cobrado", record.cobrado_global or 0.0, record.recaudo_global_pct, DASH_POS),
            ]
            filas = ""
            for nombre, valor, pct, color in pasos:
                pct_seguro = max(min(pct, 100.0), 0.0)
                filas += f"""
                <div style="margin-bottom:14px;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
                        <span style="font-size:12.5px;font-weight:600;display:flex;align-items:center;gap:7px;">
                            <span style="width:10px;height:10px;border-radius:3px;background:{color};display:inline-block;"></span>{nombre}
                        </span>
                        <span style="font-family:monospace;font-size:13.5px;font-weight:700;">
                            {self._fmt_cop_millones(valor)} <small style="color:#98A2B3;font-weight:600;font-size:11px;margin-left:5px;">{pct_seguro:.1f}%</small>
                        </span>
                    </div>
                    <div style="height:11px;border-radius:99px;background:#EEF1F6;overflow:hidden;">
                        <div style="height:100%;border-radius:99px;width:{pct_seguro:.1f}%;background:{color};"></div>
                    </div>
                </div>"""
            record.pipeline_financiero_html = f'<div>{filas}</div>'

    # ---- Ejecución técnica / financiera / recaudo (barras lineales) ----
    panel_ejecucion_html = fields.Html(string="Ejecución técnica vs financiera", compute='_compute_panel_ejecucion_html')

    @api.depends('tecnica_global_pct', 'ejecucion_financiera_global_pct', 'recaudo_global_pct')
    def _compute_panel_ejecucion_html(self):
        for record in self:
            filas = [
                ("Ejecución técnica <span class=\"badge-en-construccion\">En construcción</span>", record.tecnica_global_pct, DASH_TEAL, "Avance físico de obra reportado"),
                ("Ejecución financiera", record.ejecucion_financiera_global_pct, DASH_INDIGO,
                 f"{self._fmt_cop_millones(record.pagado_global)} ejecutados de {self._fmt_cop_millones(record.contratado_global)}"),
                ("Recaudo (cobrado)", record.recaudo_global_pct, DASH_POS,
                 f"{self._fmt_cop_millones(record.cobrado_global)} cobrados del contratado"),
            ]
            html = ""
            for nombre, pct, color, nota in filas:
                pct_seguro = max(min(pct or 0.0, 100.0), 0.0)
                html += f"""
                <div style="margin-bottom:16px;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px;">
                        <span style="font-size:12.5px;font-weight:600;">{nombre}</span>
                        <span style="font-family:monospace;font-size:16px;font-weight:700;color:{color};">{pct_seguro:.1f}%</span>
                    </div>
                    <div style="height:11px;border-radius:99px;background:#EEF1F6;overflow:hidden;">
                        <div style="height:100%;border-radius:99px;width:{pct_seguro:.1f}%;background:{color};"></div>
                    </div>
                    <div style="font-size:11px;color:#98A2B3;margin-top:5px;font-weight:500;">{nota}</div>
                </div>"""
            record.panel_ejecucion_html = f'<div>{html}</div>'

    # ---- Ejecución técnica vs financiera de UN proyecto (Row C del modo Proyectos) ----
    panel_ejecucion_proyecto_html = fields.Html(string="Ejecución técnica vs financiera (Proyecto)",
                                                compute='_compute_panel_ejecucion_proyecto_html')

    @api.depends('suma_total', 'financiera_pct_proyecto')
    def _compute_panel_ejecucion_proyecto_html(self):
        for record in self:
            tecnica_pct = record.suma_total or 0.0
            financiera_pct = record.financiera_pct_proyecto or 0.0
            brecha = financiera_pct - tecnica_pct

            html = ""
            for nombre, pct, color, nota in [
                ("Ejecución técnica <span class=\"badge-en-construccion\">En construcción</span>", tecnica_pct, DASH_TEAL,
                 "Avance físico de obra reportado"),
                ("Ejecución financiera", financiera_pct, DASH_INDIGO,
                 f"{self._fmt_cop_millones(record.pagado_proyecto)} pagados de {self._fmt_cop_millones(record.valor_total)}"),
            ]:
                pct_seguro = max(min(pct, 100.0), 0.0)
                html += f"""
                <div style="margin-bottom:16px;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px;">
                        <span style="font-size:12.5px;font-weight:600;">{nombre}</span>
                        <span style="font-family:monospace;font-size:16px;font-weight:700;color:{color};">{pct_seguro:.1f}%</span>
                    </div>
                    <div style="height:11px;border-radius:99px;background:#EEF1F6;overflow:hidden;">
                        <div style="height:100%;border-radius:99px;width:{pct_seguro:.1f}%;background:{color};"></div>
                    </div>
                    <div style="font-size:11px;color:#98A2B3;margin-top:5px;font-weight:500;">{nota}</div>
                </div>"""

            # Brecha: puede ser negativa (técnica por delante) o positiva (financiera por delante). El
            # ancho de la barra representa la magnitud, no el signo. Hereda la misma incertidumbre de
            # "Ejecución técnica" -- no es un hallazgo firme mientras ese dato esté incompleto.
            brecha_pct_seguro = max(min(abs(brecha), 100.0), 0.0)
            signo = "+" if brecha >= 0 else "−"
            nota_brecha = ("Se factura por encima del avance físico reportado (dato de técnica en construcción)."
                           if brecha >= 0 else
                           "El avance físico reportado supera lo facturado (dato de técnica en construcción).")
            html += f"""
            <div>
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px;">
                    <span style="font-size:12.5px;font-weight:600;">Brecha técnica–financiera <span class="badge-en-construccion">En construcción</span></span>
                    <span style="font-family:monospace;font-size:16px;font-weight:700;color:{DASH_WARN};">{signo}{abs(brecha):.1f} pp</span>
                </div>
                <div style="height:11px;border-radius:99px;background:#EEF1F6;overflow:hidden;">
                    <div style="height:100%;border-radius:99px;width:{brecha_pct_seguro:.1f}%;background:{DASH_WARN};"></div>
                </div>
                <div style="font-size:11px;color:#98A2B3;margin-top:5px;font-weight:500;">{nota_brecha}</div>
            </div>"""
            record.panel_ejecucion_proyecto_html = f'<div>{html}</div>'

    # ---- Ejecución técnica vs financiera de UN coordinador (modo Coordinadores) ----
    panel_ejecucion_coordinador_html = fields.Html(string="Ejecución técnica vs financiera (Coordinador)",
                                                    compute='_compute_panel_ejecucion_coordinador_html')

    @api.depends('tecnica_promedio_coordinador', 'financiera_promedio_coordinador')
    def _compute_panel_ejecucion_coordinador_html(self):
        for record in self:
            tecnica_pct = record.tecnica_promedio_coordinador or 0.0
            financiera_pct = record.financiera_promedio_coordinador or 0.0
            brecha = financiera_pct - tecnica_pct

            html = ""
            for nombre, pct, color, nota in [
                ("Ejecución técnica <span class=\"badge-en-construccion\">En construcción</span>", tecnica_pct, DASH_TEAL,
                 "Promedio de avance físico de los proyectos a cargo"),
                ("Ejecución financiera", financiera_pct, DASH_INDIGO,
                 f"{self._fmt_cop_millones(record.total_pagado_coordinador)} pagados de {self._fmt_cop_millones(record.valor_total_coordinador)}"),
            ]:
                pct_seguro = max(min(pct, 100.0), 0.0)
                html += f"""
                <div style="margin-bottom:16px;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px;">
                        <span style="font-size:12.5px;font-weight:600;">{nombre}</span>
                        <span style="font-family:monospace;font-size:16px;font-weight:700;color:{color};">{pct_seguro:.1f}%</span>
                    </div>
                    <div style="height:11px;border-radius:99px;background:#EEF1F6;overflow:hidden;">
                        <div style="height:100%;border-radius:99px;width:{pct_seguro:.1f}%;background:{color};"></div>
                    </div>
                    <div style="font-size:11px;color:#98A2B3;margin-top:5px;font-weight:500;">{nota}</div>
                </div>"""

            brecha_pct_seguro = max(min(abs(brecha), 100.0), 0.0)
            signo = "+" if brecha >= 0 else "−"
            nota_brecha = ("Se factura por encima del avance físico reportado (dato de técnica en construcción)."
                           if brecha >= 0 else
                           "El avance físico reportado supera lo facturado (dato de técnica en construcción).")
            html += f"""
            <div>
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px;">
                    <span style="font-size:12.5px;font-weight:600;">Brecha técnica–financiera <span class="badge-en-construccion">En construcción</span></span>
                    <span style="font-family:monospace;font-size:16px;font-weight:700;color:{DASH_WARN};">{signo}{abs(brecha):.1f} pp</span>
                </div>
                <div style="height:11px;border-radius:99px;background:#EEF1F6;overflow:hidden;">
                    <div style="height:100%;border-radius:99px;width:{brecha_pct_seguro:.1f}%;background:{DASH_WARN};"></div>
                </div>
                <div style="font-size:11px;color:#98A2B3;margin-top:5px;font-weight:500;">{nota_brecha}</div>
            </div>"""
            record.panel_ejecucion_coordinador_html = f'<div>{html}</div>'

    # ---- Liquidez del proyecto (doughnut: pagado vs disponible) -- Row B del modo Proyectos ----
    liquidez_proyecto_chart_data = fields.Char(string="Liquidez del Proyecto", compute='_compute_liquidez_proyecto_chart_data')

    @api.depends('pagado_proyecto', 'flujo_neto_proyecto')
    def _compute_liquidez_proyecto_chart_data(self):
        for record in self:
            pagado = record.pagado_proyecto or 0.0
            saldo = record.flujo_neto_proyecto or 0.0
            if saldo >= 0:
                # Colores distintos y ya usados con este mismo significado en "Composición
                # Financiera" (Global): Pagado = ámbar, Disponible = verde. Antes ambos eran variantes
                # de verde/teal casi indistinguibles.
                chart = {
                    'labels': ['Pagado (salida)', 'Disponible (saldo)'],
                    'datasets': [{
                        'data': [pagado, saldo],
                        'backgroundColor': [DASH_WARN, DASH_POS],
                        'borderWidth': 2,
                        'borderColor': '#ffffff',
                    }],
                    'cutout': '66%',
                    'legendPosition': 'bottom',
                }
            else:
                # Déficit: no hay "saldo disponible" que mostrar -- una sola rebanada roja del tamaño
                # del déficit sobre lo pagado, para no fingir un donut de 2 partes positivas que no
                # contempla el mockup (que asume cobrado > pagado siempre).
                chart = {
                    'labels': ['Pagado (salida)', f'Déficit ({self._fmt_cop_millones(abs(saldo))})'],
                    'datasets': [{
                        'data': [pagado, abs(saldo)],
                        'backgroundColor': [DASH_WARN, DASH_NEG],
                        'borderWidth': 2,
                        'borderColor': '#ffffff',
                    }],
                    'cutout': '66%',
                    'legendPosition': 'bottom',
                }
            record.liquidez_proyecto_chart_data = json.dumps(chart)

    # ---- Balance de caja del proyecto (pipeline) -- Row C del modo Proyectos ----
    # A diferencia de "Composición Financiera" (Global), que mide todo contra lo Contratado, este
    # panel mide contra lo Cobrado: responde "¿en qué se fue la plata que entró?", no "¿cuánto se ha
    # ejecutado del contrato?" -- son preguntas distintas, ver glosario 'balance_caja_proyecto'.
    balance_caja_proyecto_html = fields.Html(string="Balance de Caja (Proyecto)", compute='_compute_balance_caja_proyecto_html')

    @api.depends('cobrado_proyecto', 'pagado_proyecto', 'flujo_neto_proyecto', 'valor_total')
    def _compute_balance_caja_proyecto_html(self):
        for record in self:
            cobrado = record.cobrado_proyecto or 0.0
            if cobrado <= 0:
                record.balance_caja_proyecto_html = (
                    '<div style="text-align:center;color:#98A2B3;font-size:12.5px;padding:20px 0;">'
                    'Sin cobros registrados todavía para este proyecto.</div>')
                continue

            pagado = record.pagado_proyecto or 0.0
            saldo = max(record.flujo_neto_proyecto or 0.0, 0.0)
            contratado = record.valor_total or 0.0

            pasos = [
                ("Cobrado (entradas)", cobrado, 100.0, DASH_POS),
                ("Pagado (salidas)", pagado, pagado / cobrado * 100, DASH_TEAL),
                ("Saldo disponible", saldo, saldo / cobrado * 100, DASH_INK),
                ("Presupuesto contratado", contratado, contratado / cobrado * 100, DASH_INDIGO),
            ]
            html = ""
            for nombre, valor, pct, color in pasos:
                pct_seguro = max(min(pct, 100.0), 0.0)
                html += f"""
                <div style="margin-bottom:14px;">
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
                        <span style="font-size:12.5px;font-weight:600;display:flex;align-items:center;gap:7px;">
                            <span style="width:10px;height:10px;border-radius:3px;background:{color};display:inline-block;"></span>{nombre}
                        </span>
                        <span style="font-family:monospace;font-size:13.5px;font-weight:700;">
                            {self._fmt_cop_millones(valor)} <small style="color:#98A2B3;font-weight:600;font-size:11px;margin-left:5px;">{pct_seguro:.1f}%</small>
                        </span>
                    </div>
                    <div style="height:11px;border-radius:99px;background:#EEF1F6;overflow:hidden;">
                        <div style="height:100%;border-radius:99px;width:{pct_seguro:.1f}%;background:{color};"></div>
                    </div>
                </div>"""
            record.balance_caja_proyecto_html = f'<div>{html}</div>'

    # ---- Estado del portafolio (doughnut) ----
    # Nota: los campos legacy `numeracion_proyectos_en_ejecucion` / `proyectos_cerrados` son enteros
    # manuales sin ningún cron ni botón que los actualice (no hay ninguna referencia de escritura en
    # todo el módulo) -> no reflejan la realidad. El cierre formal de un proyecto sí existe, pero vive
    # en otro modelo: `cierre` (flujo coordinador -> financiero -> jurídico -> finalizado), enlazado
    # vía tablero_avances.ejecucion_id.contrataciones_id -> cierre.proyecto_relacionado3. Un proyecto
    # cuenta como "Cerrado" solo si tiene un cierre en state='finalizado'; si no, se usa dias_restantes
    # (fecha de finalización MCB) para distinguir "En ejecución" de "En ejecución (vencido)".
    portafolio_chart_data = fields.Char(string="Estado del Portafolio", compute='_compute_portafolio_chart_data')
    portafolio_en_ejecucion = fields.Integer(string="En Ejecución", compute='_compute_portafolio_chart_data')
    portafolio_en_ejecucion_vencido = fields.Integer(string="En Ejecución (Vencido)", compute='_compute_portafolio_chart_data')
    portafolio_cerrados = fields.Integer(string="Cerrados", compute='_compute_portafolio_chart_data')

    def _estado_portafolio(self, proyectos):
        """Clasifica cada proyecto (tablero_avances) en en_ejecucion / en_ejecucion_vencido / cerrado.
        "Cerrado" exige un `cierre` formal en state='finalizado' (no un campo manual); si no está
        cerrado, se distingue vencido de en_ejecucion por `dias_restantes` (fecha de finalización MCB).
        Compartido por _compute_portafolio_chart_data (Global) y el KPI "Proyectos"/tabla del modo
        Coordinador -- antes esta clasificación solo existía a nivel global."""
        contratacion_por_mcb = {m.id: (m.contrataciones_id.id or False)
                                 for m in self.env['mcb'].sudo().search([('id', 'in', proyectos.mapped('ejecucion_id').ids)])}
        contrataciones_ids = [cid for cid in contratacion_por_mcb.values() if cid]
        cierres_finalizados = self.env['cierre'].sudo().search([
            ('proyecto_relacionado3', 'in', contrataciones_ids),
            ('state', '=', 'finalizado'),
        ])
        contrataciones_cerradas = set(cierres_finalizados.mapped('proyecto_relacionado3').ids)

        estado_por_id = {}
        en_ejecucion = en_ejecucion_vencido = cerrados = 0
        for proy in proyectos:
            contratacion_id = contratacion_por_mcb.get(proy.ejecucion_id.id) if proy.ejecucion_id else False
            if contratacion_id and contratacion_id in contrataciones_cerradas:
                estado_por_id[proy.id] = 'cerrado'
                cerrados += 1
            elif (proy.dias_restantes or 0) > 0:
                estado_por_id[proy.id] = 'en_ejecucion'
                en_ejecucion += 1
            else:
                estado_por_id[proy.id] = 'en_ejecucion_vencido'
                en_ejecucion_vencido += 1

        return {
            'en_ejecucion': en_ejecucion,
            'en_ejecucion_vencido': en_ejecucion_vencido,
            'cerrados': cerrados,
            'estado_por_id': estado_por_id,
        }

    @api.depends('nombre_proyecto')
    def _compute_portafolio_chart_data(self):
        proyectos = self.env['tablero_avances'].sudo().search([])
        estado = self._estado_portafolio(proyectos)
        en_ejecucion = estado['en_ejecucion']
        en_ejecucion_vencido = estado['en_ejecucion_vencido']
        cerrados = estado['cerrados']

        chart = {
            'labels': ['En ejecución', 'En ejecución (vencido)', 'Cerrados'],
            'datasets': [{
                'data': [en_ejecucion, en_ejecucion_vencido, cerrados],
                'backgroundColor': [DASH_TEAL, DASH_WARN, DASH_MUTED],
                'hoverBackgroundColor': ['#14958A', '#F0B22B', '#7C8896'],
                'borderWidth': 2,
                'borderColor': '#ffffff',
            }],
            'cutout': '66%',
            'legendPosition': 'bottom',
        }
        for record in self:
            record.portafolio_en_ejecucion = en_ejecucion
            record.portafolio_en_ejecucion_vencido = en_ejecucion_vencido
            record.portafolio_cerrados = cerrados
            record.portafolio_chart_data = json.dumps(chart)

    # ---- Focos de atención (motor de alertas en tiempo real) ----
    alertas_globales_html = fields.Html(string="Focos de Atención", compute='_compute_alertas_globales_html')

    @api.depends('nombre_proyecto')
    def _compute_alertas_globales_html(self):
        proyectos = self.env['tablero_avances'].sudo().search([])
        # Misma corrección que en _compute_global_financiero: `contratacion.tablero_avances_ids` está
        # vacío en toda la BD; la ruta confiable es tablero_avances.ejecucion_id.contrataciones_id.
        contrataciones = proyectos.mapped('ejecucion_id.contrataciones_id')
        contratos_fuv = self.env['contrataciones_fuv'].sudo().search([('proyecto_relacionado', 'in', contrataciones.ids)])
        contratado = sum(contratos_fuv.mapped('valor_total_contrato')) + sum(contratos_fuv.mapped('valor_contrato_otrosi'))
        pagado = self._pagado_siesa_sum(proyectos)
        # valor_cobrado está desactualizado (ver _compute_global_financiero) -> se recalcula directo.
        ccs_cobrado = [cc for cc in proyectos.mapped('mcb_centro_costo') if cc]
        cobrado = sum(self.env['cobros.siesa.line'].sudo().search([('id_un', 'in', ccs_cobrado)]).mapped('recaudado')) if ccs_cobrado else 0.0
        tecnica = sum(proyectos.mapped('resultado_tecnica_representada_dinero'))
        flujo_neto = cobrado - pagado
        contratos_count = len(contratos_fuv)

        # Concentración de presupuesto por coordinador (top-2 sobre el total coordinado)
        carga_por_coordinador = defaultdict(float)
        for proy in proyectos:
            nombre = proy.coordinador_asignado.name if proy.coordinador_asignado else 'Sin Asignar'
            carga_por_coordinador[nombre] += proy.valor_por_proyecto_mcb or 0.0
        carga_ordenada = sorted(carga_por_coordinador.items(), key=lambda x: x[1], reverse=True)
        total_carga = sum(v for _, v in carga_ordenada) or 1.0
        top2_pct = (sum(v for _, v in carga_ordenada[:2]) / total_carga * 100) if carga_ordenada else 0.0

        alertas = []  # (severidad, icono, titulo, detalle)
        if flujo_neto < 0:
            alertas.append(('critical', '!', 'Flujo de caja negativo',
                f"Brecha acumulada de {self._fmt_cop_millones(flujo_neto)}. Los pagos superan a los cobros a nivel global."))
        if contratado and (cobrado / contratado * 100) < 15:
            alertas.append(('warning', '$', 'Cartera por recaudar',
                f"Solo el {(cobrado / contratado * 100):.1f}% de lo contratado se ha cobrado. Priorizar cuentas de cobro pendientes."))
        if carga_ordenada and top2_pct > 50:
            nombres_top2 = ", ".join(n for n, _ in carga_ordenada[:2])
            alertas.append(('warning', '▲', 'Concentración de riesgo',
                f"{nombres_top2} concentran el {top2_pct:.0f}% del presupuesto total coordinado."))
        if contratos_count == 0:
            alertas.append(('neutral', '✕', 'Dato inconsistente',
                "\"Total de contratistas\" está en 0. Verificar la relación contratacion → contrataciones_fuv."))
        if tecnica > pagado:
            alertas.append(('good', '✓', 'Obra avanza sobre facturación',
                f"Ejecución técnica ({self._fmt_cop_millones(tecnica)}) por encima de la financiera ({self._fmt_cop_millones(pagado)})."))
        if not alertas:
            alertas.append(('good', '✓', 'Sin focos de atención',
                "No se detectaron riesgos con las reglas activas."))

        for record in self:
            record.alertas_globales_html = self._alertas_html(alertas)

    def _alertas_html(self, alertas):
        # `alertas` es una lista de (severidad, icono, titulo, detalle). Reusado por
        # _compute_alertas_globales_html y _compute_alertas_proyecto_html para no duplicar el render.
        colores = {'critical': DASH_NEG, 'warning': DASH_WARN, 'good': DASH_POS, 'neutral': '#6B7891'}
        html = ""
        for severidad, icono, titulo, detalle in alertas:
            color = colores[severidad]
            html += f"""
            <div style="display:flex;gap:11px;padding:11px 12px;border-radius:11px;background:#F8FAFC;border:1px solid #E3E8F0;margin-bottom:10px;">
                <div style="width:26px;height:26px;border-radius:8px;flex-shrink:0;background:{color};color:#fff;
                            font-weight:700;font-size:13px;display:flex;align-items:center;justify-content:center;">{icono}</div>
                <div>
                    <div style="font-size:12.5px;font-weight:600;line-height:1.35;">{titulo}</div>
                    <div style="font-size:11.5px;color:#6B7891;margin-top:2px;line-height:1.4;">{detalle}</div>
                </div>
            </div>"""
        return f'<div>{html}</div>'

    # ---- Focos de atención de UN proyecto (Row A del modo Proyectos) ----
    alertas_proyecto_html = fields.Html(string="Focos de Atención (Proyecto)", compute='_compute_alertas_proyecto_html')

    @api.depends('nombre_proyecto')
    def _compute_alertas_proyecto_html(self):
        for record in self:
            proy = record.nombre_proyecto
            if not proy:
                record.alertas_proyecto_html = self._alertas_html(
                    [('neutral', '?', 'Sin proyecto seleccionado', 'Elige un proyecto para ver sus focos de atención.')])
                continue

            flujo_neto = record.flujo_neto_proyecto
            presupuesto_total = record.valor_total or 0.0
            tecnica_pct = record.suma_total or 0.0
            financiera_pct = record.financiera_pct_proyecto or 0.0

            alertas = []  # (severidad, icono, titulo, detalle)
            if flujo_neto < 0:
                alertas.append(('critical', '!', 'Flujo de caja negativo',
                    f"Este proyecto ha pagado {self._fmt_cop_millones(abs(flujo_neto))} más de lo que ha cobrado."))
            if not record.fecha_fin_proyecto:
                alertas.append(('critical', '⏱', 'Sin cronograma de cierre',
                    "No hay fecha de finalización: no se pueden calcular días faltantes ni un SPI confiable."))
            if presupuesto_total and record.cobrado_proyecto > presupuesto_total:
                alertas.append(('warning', '$', 'Cobros por encima del presupuesto',
                    f"Se ha cobrado {self._fmt_cop_millones(record.cobrado_proyecto)} frente a "
                    f"{self._fmt_cop_millones(presupuesto_total)} contratados. Verificar adiciones no registradas."))
            # Requiere que exista algo de avance técnico capturado -- si suma_total es 0 (la gran
            # mayoría del portafolio hoy), comparar contra financiera solo diría "técnica está vacía",
            # no un hallazgo real. Ver evm_sin_datos_tecnica / badge-en-construccion en el resto del
            # dashboard para el mismo criterio.
            if tecnica_pct > 0 and financiera_pct > tecnica_pct:
                alertas.append(('warning', '▲', 'Financiera adelanta a la técnica',
                    f"Ejecución financiera {financiera_pct:.1f}% vs técnica {tecnica_pct:.1f}%: se paga "
                    "más rápido de lo que avanza la obra (dato de técnica aún en construcción)."))
            if not record.evm_sin_datos_tecnica and record.spi_value and record.spi_value < 0.95:
                atraso_pct = (1 - record.spi_value) * 100
                alertas.append(('critical', '⏱', 'Retraso de cronograma',
                    f"SPI {record.spi_value:.2f} → el proyecto va ~{atraso_pct:.0f}% por detrás de lo planeado."))
            if not alertas or (flujo_neto > 0 and len(alertas) <= 1):
                alertas.append(('good', '✓', 'Liquidez sólida',
                    f"Saldo disponible de {self._fmt_cop_millones(flujo_neto)} cubre con holgura los compromisos actuales."
                    if flujo_neto > 0 else "No se detectaron riesgos con las reglas activas."))

            record.alertas_proyecto_html = self._alertas_html(alertas)

    # ---- Focos de atención de UN coordinador (Row A del modo Coordinadores) ----
    alertas_coordinador_html = fields.Html(string="Focos de Atención (Coordinador)", compute='_compute_alertas_coordinador_html')

    @api.depends('coordinador_id', 'seleccion_anio_coordinador')
    def _compute_alertas_coordinador_html(self):
        for record in self:
            if not record.coordinador_id:
                record.alertas_coordinador_html = self._alertas_html(
                    [('neutral', '?', 'Sin coordinador seleccionado', 'Elige un coordinador para ver sus focos de atención.')])
                continue

            proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
            if not proyectos:
                record.alertas_coordinador_html = self._alertas_html(
                    [('neutral', '?', 'Sin proyectos asignados', 'Este coordinador no tiene proyectos a cargo.')])
                continue

            alertas = []  # (severidad, icono, titulo, detalle)

            # Proyectos con cronograma en riesgo (SPI < 0.90) -- solo cuenta los que sí tienen datos
            # técnicos capturados; si ninguno los tiene, no se genera esta alerta (mismo criterio que
            # _compute_alertas_proyecto_html).
            en_riesgo = []
            for proy in proyectos:
                evm = self._evm_para_proyecto(proy)
                if evm['ev'] > 0 and evm['spi'] < 0.90:
                    en_riesgo.append(proy.name)
            if en_riesgo:
                nombres = ", ".join(en_riesgo[:3]) + (f" y {len(en_riesgo) - 3} más" if len(en_riesgo) > 3 else "")
                alertas.append(('critical', '!', f"{len(en_riesgo)} proyecto(s) con cronograma en riesgo",
                    f"{nombres} tienen SPI &lt; 0,90."))

            # Concentración presupuestal: un solo proyecto acapara gran parte de lo coordinado.
            presupuesto_total = record.valor_total_coordinador or 0.0
            if presupuesto_total and proyectos:
                mayor = max(proyectos, key=lambda p: p.valor_por_proyecto_mcb or 0.0)
                pct_mayor = (mayor.valor_por_proyecto_mcb or 0.0) / presupuesto_total * 100
                if pct_mayor > 30:
                    alertas.append(('warning', '▲', 'Alta concentración presupuestal',
                        f"{mayor.name} concentra el {pct_mayor:.0f}% ({self._fmt_cop_millones(mayor.valor_por_proyecto_mcb)}) "
                        "del presupuesto coordinado."))

            # Pago atípico: un solo mes concentra una porción desproporcionada de los pagos del año.
            year = record.seleccion_anio_coordinador or str(datetime.now().year)
            ccs = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]
            if ccs:
                egresos_mes = defaultdict(float)
                registros = self.env['pagos.siesa.line'].sudo().search([
                    ('id_un', 'in', ccs),
                    ('fecha_pago', '!=', False),
                    ('fecha_pago', '>=', f'{year}-01-01'),
                    ('fecha_pago', '<=', f'{year}-12-31'),
                ])
                for r in registros:
                    egresos_mes[r.fecha_pago.month] += r.valor_pagado
                total_egresos_anio = sum(egresos_mes.values())
                if total_egresos_anio > 0:
                    mes_max, valor_max = max(egresos_mes.items(), key=lambda kv: kv[1])
                    pct_mes = valor_max / total_egresos_anio * 100
                    if pct_mes > 40:
                        nombre_mes = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
                                      'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'][mes_max - 1]
                        alertas.append(('warning', '⏱', f'Pago atípico en {nombre_mes}',
                            f"{self._fmt_cop_millones(valor_max)} en un solo mes ({pct_mes:.0f}% del año {year})."))

            # Costo bajo control.
            if not record.evm_sin_datos_tecnica_coordinador and 0.95 <= (record.cpi_value_coordinador or 0) <= 1.05:
                alertas.append(('good', '✓', 'Costo bajo control',
                    f"CPI promedio {record.cpi_value_coordinador:.2f}: gasto cercano a lo presupuestado."))

            if not alertas or (record.flujo_neto_coordinador > 0 and len(alertas) <= 1):
                alertas.append(('good', '✓', 'Liquidez sólida',
                    f"Saldo disponible de {self._fmt_cop_millones(record.flujo_neto_coordinador)} cubre con holgura los compromisos actuales."
                    if record.flujo_neto_coordinador > 0 else "No se detectaron riesgos con las reglas activas."))

            record.alertas_coordinador_html = self._alertas_html(alertas)

    # ---- Tabla "Proyectos a cargo" de un coordinador ----
    tabla_proyectos_coordinador_html = fields.Html(string="Proyectos a Cargo (Coordinador)", compute='_compute_tabla_proyectos_coordinador_html')

    @api.depends('coordinador_id')
    def _compute_tabla_proyectos_coordinador_html(self):
        for record in self:
            if not record.coordinador_id:
                record.tabla_proyectos_coordinador_html = (
                    '<div style="text-align:center;color:#98A2B3;font-size:12.5px;padding:20px 0;">'
                    'Selecciona un coordinador para ver sus proyectos.</div>')
                continue

            proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
            if not proyectos:
                record.tabla_proyectos_coordinador_html = (
                    '<div style="text-align:center;color:#98A2B3;font-size:12.5px;padding:20px 0;">'
                    'Este coordinador no tiene proyectos a cargo.</div>')
                continue

            estado = self._estado_portafolio(proyectos)
            estado_por_id = estado['estado_por_id']
            estado_label = {'cerrado': ('Cerrado', 'st-done'), 'en_ejecucion': ('En ejecución', 'st-run'),
                             'en_ejecucion_vencido': ('En riesgo', 'st-risk')}

            proyectos_ordenados = proyectos.sorted(key=lambda p: p.valor_por_proyecto_mcb or 0.0, reverse=True)

            filas = ""
            for idx, proy in enumerate(proyectos_ordenados, start=1):
                tecnica_pct = max(min(proy.suma_total or 0.0, 100.0), 0.0)
                financiera_pct = max(min(proy.resultado_vpagado_barra or 0.0, 100.0), 0.0)
                evm = self._evm_para_proyecto(proy)
                cpi, spi = evm['cpi'], evm['spi']

                # Estado: "En riesgo" tiene prioridad visual sobre "En ejecución (vencido)" cuando hay
                # datos técnicos que lo confirman (SPI < 0.90) -- mismo umbral que la alerta de
                # coordinador. Sin eso, se usa la clasificación base de _estado_portafolio.
                base_estado = estado_por_id.get(proy.id, 'en_ejecucion')
                if base_estado == 'cerrado':
                    label, css_class = estado_label['cerrado']
                elif evm['ev'] > 0 and spi < 0.90:
                    label, css_class = 'En riesgo', 'st-risk'
                elif base_estado == 'en_ejecucion_vencido':
                    label, css_class = estado_label['en_ejecucion_vencido']
                else:
                    label, css_class = estado_label['en_ejecucion']

                cpi_color = DASH_NEG if (evm['ev'] > 0 and cpi < 0.95) else DASH_POS
                spi_color = DASH_NEG if (evm['ev'] > 0 and spi < 0.95) else DASH_POS

                filas += f"""
                <tr>
                    <td style="font-family:monospace;font-weight:700;">{idx}</td>
                    <td style="font-weight:600;">{proy.name}</td>
                    <td style="font-family:monospace;font-weight:700;">{self._fmt_cop_millones(proy.valor_por_proyecto_mcb or 0.0)}</td>
                    <td>
                        <div style="display:flex;align-items:center;gap:8px;">
                            <span style="font-family:monospace;font-weight:600;font-size:11.5px;width:34px;">{tecnica_pct:.0f}%</span>
                            <div style="height:6px;border-radius:99px;background:#EEF1F6;width:70px;overflow:hidden;">
                                <div style="height:100%;border-radius:99px;width:{tecnica_pct:.0f}%;background:{DASH_TEAL};"></div>
                            </div>
                        </div>
                    </td>
                    <td>
                        <div style="display:flex;align-items:center;gap:8px;">
                            <span style="font-family:monospace;font-weight:600;font-size:11.5px;width:34px;">{financiera_pct:.0f}%</span>
                            <div style="height:6px;border-radius:99px;background:#EEF1F6;width:70px;overflow:hidden;">
                                <div style="height:100%;border-radius:99px;width:{financiera_pct:.0f}%;background:{DASH_INDIGO};"></div>
                            </div>
                        </div>
                    </td>
                    <td style="font-family:monospace;font-weight:700;color:{cpi_color};">{cpi:.2f}</td>
                    <td style="font-family:monospace;font-weight:700;color:{spi_color};">{spi:.2f}</td>
                    <td><span class="st {css_class}">{label}</span></td>
                </tr>"""

            html = f"""
            <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;min-width:720px;">
                    <thead>
                        <tr style="font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:#6B7891;text-align:left;">
                            <th style="padding:0 12px 11px;">#</th>
                            <th style="padding:0 12px 11px;">Proyecto</th>
                            <th style="padding:0 12px 11px;">Presupuesto</th>
                            <th style="padding:0 12px 11px;">Ejec. técnica <span class="badge-en-construccion">En construcción</span></th>
                            <th style="padding:0 12px 11px;">Ejec. financiera</th>
                            <th style="padding:0 12px 11px;">CPI</th>
                            <th style="padding:0 12px 11px;">SPI</th>
                            <th style="padding:0 12px 11px;">Estado</th>
                        </tr>
                    </thead>
                    <tbody>{filas}</tbody>
                </table>
            </div>
            <style>
                .st {{ padding:3px 10px;border-radius:999px;font-size:10.5px;font-weight:600;white-space:nowrap; }}
                .st-run {{ background:#D7EDE8;color:{DASH_TEAL}; }}
                .st-done {{ background:#E7EAF0;color:#6B7891; }}
                .st-risk {{ background:#FBE2E1;color:{DASH_NEG}; }}
            </style>
            """
            record.tabla_proyectos_coordinador_html = html

    @api.depends('nombre_proyecto')
    def _compute_contratos_count(self):
        for rec in self:
            if rec.nombre_proyecto:
                # OJO: `contratacion.tablero_avances_ids` está vacío en el 100% de los registros de
                # esta BD (mismo hallazgo que en _compute_global_financiero) -> siempre daba 0. La ruta
                # que sí está poblada es tablero_avances.ejecucion_id.contrataciones_id.
                contrataciones = rec.nombre_proyecto.ejecucion_id.contrataciones_id
                rec.contratos_count = self.env['contrataciones_fuv'].sudo().search_count([
                    ('proyecto_relacionado', 'in', contrataciones.ids)
                ])
            else:
                rec.contratos_count = 0

    mes_seleccionado = fields.Selection([

        ('01', 'Enero'),
        ('02', 'Febrero'),
        ('03', 'Marzo'),
        ('04', 'Abril'),
        ('05', 'Mayo'),
        ('06', 'Junio'),
        ('07', 'Julio'),
        ('08', 'Agosto'),
        ('09', 'Septiembre'),
        ('10', 'Octubre'),
        ('11', 'Noviembre'),
        ('12', 'Diciembre')

    ], string="Mes Seleccionado", default='')

    seleccionador_graficas = fields.Selection([
        ('01', 'Global'),
        ('02', 'Proyectos'),
        ('03', 'Coordinadores')
    ], string="Dashboard Seleccionado", default='01')

    # Nuevos campos de gráficos para la Fase 1
    carga_coordinadores_chart_data = fields.Char(string="Carga de Trabajo por Coordinador", compute="_compute_carga_coordinadores_chart_data")
    top_contratistas_chart_data = fields.Char(string="Top 10 Contratistas por Monto", compute="_compute_top_contratistas_chart_data")
    proyectos_flujo_negativo_chart_data = fields.Char(string="Proyectos con Mayor Flujo de Caja Negativo", compute="_compute_proyectos_flujo_chart_data")
    proyectos_flujo_positivo_chart_data = fields.Char(string="Proyectos con Mayor Flujo de Caja Positivo", compute="_compute_proyectos_flujo_chart_data")

    # Nuevos campos de EVM (Fase 2)
    cpi_value = fields.Float(string="Índice CPI", compute="_compute_evm_indicators")
    spi_value = fields.Float(string="Índice SPI", compute="_compute_evm_indicators")
    # True cuando el Valor Ganado (EV) es 0 -- es decir, no hay avance técnico capturado para este
    # proyecto. CPI/SPI dependen enteramente de EV, así que en ese caso el número (0.00) no dice nada
    # real sobre el proyecto; solo refleja que "ejecución técnica" (dato en construcción app-wide)
    # está vacía. Se usa para mostrar un placeholder en vez de un gauge rojo engañoso.
    evm_sin_datos_tecnica = fields.Boolean(string="EVM sin datos técnicos", compute="_compute_evm_indicators")
    cpi_chart_data = fields.Char(string="Gráfico CPI", compute="_compute_cpi_chart_data")
    spi_chart_data = fields.Char(string="Gráfico SPI", compute="_compute_spi_chart_data")

    cpi_value_coordinador = fields.Float(string="Índice CPI Coordinador", compute="_compute_evm_indicators_coordinador")
    spi_value_coordinador = fields.Float(string="Índice SPI Coordinador", compute="_compute_evm_indicators_coordinador")
    evm_sin_datos_tecnica_coordinador = fields.Boolean(string="EVM Coordinador sin datos técnicos", compute="_compute_evm_indicators_coordinador")
    cpi_chart_data_coordinador = fields.Char(string="Gráfico CPI Coordinador", compute="_compute_cpi_chart_data_coordinador")
    spi_chart_data_coordinador = fields.Char(string="Gráfico SPI Coordinador", compute="_compute_spi_chart_data_coordinador")

    seleccion_anio_flujo_caja = fields.Selection(selection='_get_selecciones_anio_flujo_caja', string="Año Flujo de Caja", default=lambda self: str(datetime.now().year))
    chart_flujo_caja_data = fields.Char(string="Datos del Gráfico de Flujo de Caja", compute="_compute_chart_flujo_caja_data")
    dashboard_description = fields.Html(string="Descripción Selección", compute="_compute_dashboard_description")

    # OJO: no borrar -- es el lado inverso de action_ingresar_pagos_proyecto.pago_ids_2
    # (fields.One2many('vistas_director', 'pago_ids', ...)). Sin este campo, Odoo no puede
    # resolver esa relación y falla toda la carga del registro (KeyError: 'pago_ids').
    pago_ids = fields.Many2one('pago', default=lambda self: self.env['pago'].search([], limit=1))

    #Grafica lineal evolución de mes
    @api.depends('valor_mes9', 'valor_mes10', 'valor_mes11', 'valor_mes0', 'valor_mes1', 'valor_mes2')
    def _compute_line_chart_data(self):
        for record in self:
            line_chart_data = self._get_line_chart_data(
                record.valor_mes9, record.valor_mes10, record.valor_mes11, record.valor_mes0,
                record.valor_mes1, record.valor_mes2
            )
            record.line_chart_data = json.dumps(line_chart_data)

    def _get_line_chart_data(self, *args):
        try:
            months = ['Octubre 2023', 'Noviembre 2023', 'Diciembre 2023', 'Enero 2024', 'Febrero 2024', 'Marzo 2024']
            return {
                'labels': months,
                'datasets': [{
                    'label': 'Valores',
                    'data': args,
                    'fill': 'false',  # Desactiva el relleno de área bajo la línea
                    'borderColor': 'rgba(75, 192, 192, 1)',  # Color de la línea
                    'borderWidth': 1,
                    'lineTension': 0  # Desactiva la curva suavizada entre los puntos
                }]
            }
        except Exception as e:
            _logger.error(f"Error in _get_line_chart_data: {e}")
            return {}

    line_chart_data = fields.Char(string="Datos del Gráfico de Líneas", compute="_compute_line_chart_data")

    ##Grafica Cantidad de proyectos por año barras horizontales
    horizontal_bar_chart_data = fields.Char(string="Datos del Gráfico Horizontal",
                                            compute="_compute_horizontal_bar_chart_data")

    def _get_horizontal_bar_chart_data(self, anio_2018, anio_2019, anio_2020, anio_2021, anio_2022, anio_2023):

        try:
            return {
                'labels': [''],
                'datasets': [
                    {
                        'label': '2018',
                        'data': [anio_2018],
                        'backgroundColor': 'rgba(325, 99, 132, 0.5)',
                        'hoverBackgroundColor': 'rgba(325, 99, 132, 1)'
                    },
                    {
                        'label': '2019',
                        'data': [anio_2019],
                        'backgroundColor': 'rgba(160, 162, 235, 0.5)',
                        'hoverBackgroundColor': 'rgba(160, 162, 235, 1)'
                    },
                    {
                        'label': '2020',
                        'data': [anio_2020],
                        'backgroundColor': 'rgba(255, 159, 64, 0.5)',
                        'hoverBackgroundColor': 'rgba(255, 159, 64, 1)'
                    },
                    {
                        'label': '2021',
                        'data': [anio_2021],
                        'backgroundColor': 'rgba(155, 159, 64, 0.5)',
                        'hoverBackgroundColor': 'rgba(155, 159, 64, 1)'
                    },
                    {
                        'label': '2022',
                        'data': [anio_2022],
                        'backgroundColor': 'rgba(205, 159, 64, 0.5)',
                        'hoverBackgroundColor': 'rgba(205, 159, 64, 1)'
                    },
                    {
                        'label': '2024',
                        'data': [anio_2023],
                        'backgroundColor': 'rgba(25, 159, 64, 0.5)',
                        'hoverBackgroundColor': 'rgba(25, 159, 64, 1)'
                    }
                ]
            }
        except Exception as e:
            # Log the error
            _logger.error(f"Error in _get_horizontal_bar_chart_data: {e}")
            return {}

    @api.depends('anio_2018', 'anio_2019', 'anio_2020', 'anio_2021', 'anio_2022', 'anio_2023')
    def _compute_horizontal_bar_chart_data(self):
        for record in self:
            chart_data = self._get_horizontal_bar_chart_data(
                record.anio_2018, record.anio_2019, record.anio_2020, record.anio_2021, record.anio_2022,
                record.anio_2023
            )
            record.horizontal_bar_chart_data = json.dumps(chart_data)

    # Grafica ingresos por año
    anios_pasados = fields.Char(string="Datos del Gráfico", compute="_compute_chart_data_pasados")

    def _get_chart_data_pasados(self):
        """
        Define the logic to get chart data for the given years.
        """
        try:
            labels = ['2018', '2019', '2020', '2021', '2022', '2023']
            gastos = [
                self.gasto_2018, self.gasto_2019, self.gasto_2020,
                self.gasto_2021, self.gasto_2022, self.gasto_2023
            ]
            ingresos = [
                self.ingresos_2018, self.ingresos_2019, self.ingresos_2020,
                self.ingresos_2021, self.ingresos_2022, self.ingresos_2023
            ]
            return {
                'labels': labels,
                'datasets': [
                    {
                        'label': 'Gastos',
                        'data': gastos,
                        'backgroundColor': 'rgba(255, 99, 132, 0.5)',
                        'hoverBackgroundColor': 'rgba(255, 99, 132, 1)'
                    },
                    {
                        'label': 'Ingresos',
                        'data': ingresos,
                        'backgroundColor': 'rgba(54, 162, 235, 0.5)',
                        'hoverBackgroundColor': 'rgba(54, 162, 235, 1)'
                    }
                ]
            }
        except Exception as e:
            # Log the error
            _logger.error(f"Error in _get_chart_data_pasados: {e}")
            return {}

    @api.depends('gasto_2018', 'gasto_2019', 'gasto_2020', 'gasto_2021', 'gasto_2022', 'gasto_2023',
                 'ingresos_2018', 'ingresos_2019', 'ingresos_2020', 'ingresos_2021', 'ingresos_2022', 'ingresos_2023')
    def _compute_chart_data_pasados(self):
        for record in self:
            chart_data = self._get_chart_data_pasados()
            record.anios_pasados = json.dumps(chart_data)

    pie_chart_data = fields.Char(string="Datos del Gráfico de Torta", compute="_compute_pie_chart_data")

    def _get_pie_chart_data(self, valor_mes9, valor_mes10, valor_mes11, valor_mes0, valor_mes1, valor_mes2):
        """
        Define la lógica para obtener los datos del gráfico de torta incluyendo valor_mes9, valor_mes10, valor_mes11, valor_mes0, valor_mes1 y valor_mes2.
        """
        try:
            return {
                'labels': ['SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE', 'ENERO', 'FEBRERO'],
                'datasets': [{
                    'data': [valor_mes9, valor_mes10, valor_mes11, valor_mes0, valor_mes1, valor_mes2],
                    'backgroundColor': [
                        '#4bc0c0',  # Color para valor_mes9
                        '#9966ff',  # Color para valor_mes10
                        '#ffa64d',  # Color para valor_mes11
                        '#ff6384',  # Color para valor_mes0
                        '#36a2eb',  # Color para valor_mes1
                        '#ffcc99'  # Color para valor_mes2
                    ],
                    'hoverBackgroundColor': [
                        '#5cdcdc',  # Color al pasar el cursor para valor_mes9
                        '#b38cff',  # Color al pasar el cursor para valor_mes10
                        '#ffc266',  # Color al pasar el cursor para valor_mes11
                        '#ff809f',  # Color al pasar el cursor para valor_mes0
                        '#65b3ff',  # Color al pasar el cursor para valor_mes1
                        '#ffdab3'  # Color al pasar el cursor para valor_mes2
                    ]
                }]
            }
        except Exception as e:
            # Log the error
            _logger.error(f"Error in _get_pie_chart_data: {e}")
            return {}

    @api.depends('name', 'produccion_sumado_global', 'suma_pago_efectuado2_sumado',
                 'contrapartida_avances2', 'adic_otro_si_', 'valor_mes9', 'valor_mes10', 'valor_mes11', 'valor_mes0',
                 'valor_mes1', 'valor_mes2')
    def _compute_pie_chart_data(self):
        for record in self:
            pie_chart_data = self._get_pie_chart_data(record.valor_mes9, record.valor_mes10, record.valor_mes11,
                                                      record.valor_mes0, record.valor_mes1, record.valor_mes2)
            record.pie_chart_data = json.dumps(pie_chart_data)

    @api.model
    def create(self, values):
        # Si no se proporciona un valor para ejecucion_id11, establecerlo en el último registro de MCB
        if 'ejecucion_id11' not in values or not values['ejecucion_id11']:
            ultima_ejecucion = self.env['mcb'].search([], limit=1, order='id desc')
            values['ejecucion_id11'] = ultima_ejecucion.id if ultima_ejecucion else False

        return super(VistasDirector, self).create(values)


    # FUNCIONES
    @api.onchange('ejecucion3_id')
    def _onchange_ejecucion3_id(self):
        # Puedes poner aquí la lógica que deseas realizar cuando cambia ejecucion3_id
        pass

    @api.model
    def _default_ejecucion3_id(self):
        # Puedes poner aquí la lógica para determinar el valor predeterminado
        informacion_base_record = self.env['informacion_base_agregar'].search([], limit=1)
        return informacion_base_record.id if informacion_base_record else False

    total_proyctos_pasado = fields.Float(string='Total Proyectos Pasado', help='Descripción del campo')
    actividad5_ids = fields.One2many('ejecucion', 'vistas_director3_id', string='Tablero Control')
    # Campos llamados globales
    valor_por_proyecto_mcb_global = fields.Float(related='actividad4_ids.valor_por_proyecto_mcb_global_sumado')
    pago_efectuado2_sumado = fields.Float(related='actividad4_ids.pago_efectuado2_sumado', string="Valor pagado")
    suma_pago_efectuado2_sumado = fields.Float(string='Suma de pago_efectuado2_sumado3',
                                               compute='_compute_suma_pago_efectuado2_sumado')

    @api.depends('actividad4_ids.valor_pagado_dashboard')
    def _compute_suma_pago_efectuado2_sumado(self):
        todos_los_proyectos = self.env['tablero_avances'].search([])
        suma_total = self._pagado_siesa_sum(todos_los_proyectos)
        for record in self:
            record.suma_pago_efectuado2_sumado = suma_total

    # Campos relacionados
    ejecucion_id11 = fields.Many2one('mcb')
    currency_id = fields.Many2one('res.currency', string='Moneda')
    contrapartida_avances = fields.Monetary(tracking=True, related='actividad4_ids.contrapartida_avances')
    contrapartida_avances2 = fields.Monetary(compute='_compute_suma_contrapartida_avances')
    # Relación One2many modificada para establecer la relación automáticamente
    actividad4_ids = fields.One2many('tablero_avances', 'vistas_director_id', string='Tablero Control')

    @api.depends('contrapartida_avances')
    def _compute_suma_contrapartida_avances(self):
        todos_los_proyectos = self.env['tablero_avances'].search([])
        suma_total = sum(todos_los_proyectos.mapped('contrapartida_avances'))
        for record in self:
            record.contrapartida_avances2 = suma_total


    suma_adicion_otro_si3 = fields.Monetary(related='actividad4_ids.adicion_otrosi')
    adic_otro_si_ = fields.Monetary(compute='_compute_suma_otrosi')

    @api.depends('actividad4_ids.adicion_otrosi')
    def _compute_suma_otrosi(self):
        for record in self:
            suma = sum(record.actividad4_ids.mapped('adicion_otrosi'))
            record.adic_otro_si_ = suma

    produccion_sumado = fields.Float(string="Valor global", related='actividad4_ids.valor_proyecto_convenio2')
    produccion_sumado_global = fields.Monetary(compute='_compute_suma_produccion')

    @api.depends('actividad4_ids.valor_proyecto_convenio2')
    def _compute_suma_produccion(self):
        for record in self:
            suma = sum(record.actividad4_ids.mapped('valor_proyecto_convenio2'))
            record.produccion_sumado_global = suma

    valor_ejecutado = fields.Float(related='actividad4_ids.suma_valor_cobrado')
    ejecucion_tecnica = fields.Float(related='actividad4_ids.suma_total_porcentaje_ejecutado2')
    ejecucion_financiera = fields.Float(compute='_compute_valor_ejecutado_financiero')

    @api.depends('suma_pago_efectuado2_sumado', 'valor_por_proyecto_mcb_global')
    def _compute_valor_ejecutado_financiero(self):
        for record in self:
            if record.valor_por_proyecto_mcb_global != 0:
                record.ejecucion_financiera = (
                        (record.suma_pago_efectuado2_sumado / record.valor_por_proyecto_mcb_global) * 100)
            else:
                record.ejecucion_financiera = 0.0


    valor_ejecutado_porcentaje = fields.Float(string='Porcentaje Ejecutado',
                                              compute='_compute_valor_ejecutado_porcentaje', store=True)

    #Variables de total proyectos por año
    ejecucion3_id = fields.Many2one('informacion_base_agregar',
                                    default=lambda self: self.env['informacion_base_agregar'].search([], limit=1))

    cantidad_contrataciones = fields.Integer(compute='_compute_cantidad_contrataciones',
                                             string="Cantidad total de contrataciones")
    numeracion_proyectos_en_ejecucion = fields.Integer(store=True)
    proyectos_cerrados = fields.Integer(store=True)

    @api.depends('cantidad_contrataciones')
    def _compute_cantidad_contrataciones(self):
        contratacion_obj = self.env['contratacion']
        cantidad_registros = contratacion_obj.search_count([])
        self.cantidad_contrataciones = cantidad_registros

    ####################################
    # LLAMAR VISTA KANBAN
    def ir_a_tablero_avances(self):
        # Verificar si el usuario está en los grupos sin restricción
        es_director = self.env.user.has_group('Matriz.group_contratacion_director_fuv')
        es_soporte = self.env.user.has_group('Matriz.group_soporte')

        domain = []
        if not (es_director or es_soporte):
            domain = [('coordinador_asignado', '=', self.env.uid)]

        action = {
            'name': 'Tablero de Avances',
            'type': 'ir.actions.act_window',
            'res_model': 'tablero_avances',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': domain,
        }

        # Lógica adicional que ya tienes
        tablero_avances_records = self.env['tablero_avances'].search([])
        if tablero_avances_records:
            ultima_ejecucion = self.env['ejecucion'].search([], limit=1, order='id desc')
            for tablero_avance_record in tablero_avances_records:
                if not tablero_avance_record.ejecucion_J_id:
                    tablero_avance_record.write({'ejecucion_J_id': ultima_ejecucion.id if ultima_ejecucion else False})
                    tablero_avance_record.pago_efectuado2_sumado = ultima_ejecucion.pago_efectuado2_sumado if ultima_ejecucion else 0.0

        return action

    @api.depends('valor_ejecutado', 'valor_por_proyecto_mcb_global')
    def _compute_valor_ejecutado_porcentaje(self):
        for record in self:
            if record.valor_por_proyecto_mcb_global != 0:
                record.valor_ejecutado_porcentaje = (
                record.valor_ejecutado / record.valor_por_proyecto_mcb_global) * 100
            else:
                record.valor_ejecutado_porcentaje = 0.0


    porcentaje_aumento = fields.Float(string='Porcentaje Aumento', compute='_compute_porcentaje_aumento_disminucion',
                                      store=True)
    porcentaje_disminucion = fields.Float(string='Porcentaje Disminución', store=True,
                                          compute='_compute_porcentaje_aumento_disminucion')

    @api.depends('valor_mes0', 'valor_por_proyecto_mcb_global')
    def _compute_porcentaje_aumento_disminucion(self):
        for record in self:
            if record.valor_mes0:
                aumento_disminucion = ((
                                        record.valor_por_proyecto_mcb_global - record.valor_mes0) / record.valor_mes0) * 100
                if aumento_disminucion > 0:
                    record.porcentaje_aumento = aumento_disminucion
                    record.porcentaje_disminucion = 0.0
                else:
                    record.porcentaje_aumento = 0.0
                    record.porcentaje_disminucion = abs(aumento_disminucion)
            else:
                record.porcentaje_aumento = 0.0
                record.porcentaje_disminucion = 0.0

    barra2_valor_proyectos_global = fields.Float()
    valor_mes0 = fields.Float()
    valor_mes1 = fields.Float()
    valor_mes2 = fields.Float()
    valor_mes3 = fields.Float()
    valor_mes4 = fields.Float()
    valor_mes5 = fields.Float()
    valor_mes6 = fields.Float()
    valor_mes7 = fields.Float()
    valor_mes8 = fields.Float()
    valor_mes9 = fields.Float()
    valor_mes10 = fields.Float()
    valor_mes11 = fields.Float()
    ##Años
    anio_2018 = fields.Float()
    anio_2019 = fields.Float()
    anio_2020 = fields.Float()
    anio_2021 = fields.Float()
    anio_2022 = fields.Float()
    anio_2023 = fields.Float()
    ##gastos
    gasto_2018 = fields.Float()
    gasto_2019 = fields.Float()
    gasto_2020 = fields.Float()
    gasto_2021 = fields.Float()
    gasto_2022 = fields.Float()
    gasto_2023 = fields.Float()
    ##ingresos
    ingresos_2018 = fields.Float()
    ingresos_2019 = fields.Float()
    ingresos_2020 = fields.Float()
    ingresos_2021 = fields.Float()
    ingresos_2022 = fields.Float()
    ingresos_2023 = fields.Float()
    evolucion_valores = fields.Float(compute='_compute_evolucion_valores', store=False)

    @api.depends('valor_por_proyecto_mcb_global', 'valor_mes0')
    def _compute_evolucion_valores(self):
        for record in self:
            if record.valor_mes0:
                evolucion = record.valor_por_proyecto_mcb_global - record.valor_mes0
                record.evolucion_valores = evolucion
            else:
                record.evolucion_valores = 0.0

    # Datos del gráfico Polar Area
    chart_tecnica_financiera = fields.Char(
        string="Datos del Gráfico Polar",
        compute="_compute_polar_area_chart_data_proyecto"
    )

    # Método auxiliar para obtener los datos del gráfico
    def _get_polar_area_chart_data_proyecto(self, proyecto):
        try:
            # Filtra según el proyecto seleccionado
            ejecucion_financiera = proyecto.valor_pagado_dashboard
            ejecucion_tecnica = proyecto.resultado_tecnica_representada_dinero
            return {
                'labels': ['Ejecución Financiera', 'Ejecución Técnica'],
                'datasets': [{
                    'label': 'Valores',
                    'data': [ejecucion_financiera, ejecucion_tecnica],
                    'backgroundColor': ['rgba(76, 95, 213, 0.6)', 'rgba(14, 124, 107, 0.6)']  # Indigo (financiera), Teal (técnica)
                }]
            }
        except Exception as e:
            _logger.error(f"Error in _get_polar_area_chart_data_proyecto: {e}")
            return {}

    # Cómputo de los datos del gráfico
    @api.depends('nombre_proyecto')
    def _compute_polar_area_chart_data_proyecto(self):
        for record in self:
            if record.nombre_proyecto:  # Validar que un proyecto esté seleccionado
                polar_area_chart_data = self._get_polar_area_chart_data_proyecto(record.nombre_proyecto)
                record.chart_tecnica_financiera = json.dumps(polar_area_chart_data)
            else:
                record.chart_tecnica_financiera = json.dumps({})
########################################Brayan################################################################################
    seleccion_anio_proyecto = fields.Selection(selection='_get_selecciones_anio_coordinador', string="Año del Proyecto")
    chart_cobros_mensuales = fields.Text(string="Datos del Gráfico de Pagos Mensuales", compute="_compute_chart_cobros_mensuales")
    @api.model
    def _get_selecciones_anio_cobros_mensuales(self):
        try:
            # Obtener cobros con fecha no nula
            cobros_con_fecha = self.env['cobros.siesa.line'].search([('fecha', '!=', False)])

            # Filtrar años válidos (solo los que estén dentro de un rango razonable)
            anio_actual = datetime.now().year
            anios = list(set([
                cobro.fecha.year for cobro in cobros_con_fecha
                if 2000 <= cobro.fecha.year <= anio_actual  # Filtrar solo años entre 2000 y el año actual
            ]))

            # Generar las opciones de año para el selector
            selecciones_anio_c = [(str(anio), str(anio)) for anio in sorted(anios, reverse=True)]

            _logger.info(f"Años disponibles para selección: {selecciones_anio_c}")
            return selecciones_anio_c
        except Exception as e:
            _logger.error(f"Error al obtener los años para el selector de cobros: {e}")
            return []

    @api.depends('nombre_proyecto', 'seleccion_anio_proyecto')
    def _compute_chart_cobros_mensuales(self):
        _logger.info("Iniciando cálculo de chart_cobros_mensuales")

        for record in self:
            try:
                if not record.nombre_proyecto or not record.seleccion_anio_proyecto:
                    _logger.warning("Proyecto o año no seleccionados")
                    record.chart_cobros_mensuales = json.dumps({})
                    continue

                # Filtrar los cobros para el proyecto y año seleccionados usando id_un (centro de costo).
                # OJO: se filtra por `fecha_recaudo` (fecha en que efectivamente entró el dinero), no por
                # `fecha` (fecha de causación/facturación del documento) -- son fechas distintas y pueden
                # caer en años distintos (ej. documento causado en dic-2025, recaudado en mar-2026). Usar
                # `fecha` hacía que esta gráfica no coincidiera con "Flujo de caja mensual", que sí usa
                # fecha_recaudo (verificado en vivo con el proyecto 17-017: 4 registros causados en
                # 2025-12 pero recaudados en 2026-03 no aparecían aquí para el año 2026).
                cobros_filtrados = self.env['cobros.siesa.line'].sudo().search([
                    ('id_un', '=', record.nombre_proyecto.mcb_centro_costo),
                    ('fecha_recaudo', '!=', False),
                    ('fecha_recaudo', '>=', f'{record.seleccion_anio_proyecto}-01-01'),
                    ('fecha_recaudo', '<=', f'{record.seleccion_anio_proyecto}-12-31')
                ])

                if not cobros_filtrados:
                    _logger.warning(f"No se encontraron cobros para el proyecto {record.nombre_proyecto.name} y año {record.seleccion_anio_proyecto}")
                    record.chart_cobros_mensuales = json.dumps({})
                    continue

                cobros_por_mes = {}
                for cobro in cobros_filtrados:
                    mes = cobro.fecha_recaudo.strftime('%m')
                    if mes in cobros_por_mes:
                        cobros_por_mes[mes] += cobro.recaudado
                    else:
                        cobros_por_mes[mes] = cobro.recaudado

                meses = [MONTH_NAMES[mes] for mes in sorted(cobros_por_mes.keys())]
                valores = [cobros_por_mes[mes] for mes in sorted(cobros_por_mes.keys())]

                chart_data = {
                    'type': 'bar',
                    'labels': meses,
                    'datasets': [{
                        'label': 'Cobros Mensuales',
                        'data': valores,
                        'backgroundColor': [
                            '#4bc0c0', '#9966ff', '#ffa64d', '#ff6384', '#36a2eb',
                            '#ffcc99', '#ff6666', '#339966', '#cc99ff', '#ff9933',
                            '#6699ff', '#ffcc00'
                        ][:len(meses)],
                        'hoverBackgroundColor': [
                            '#5cdcdc', '#b38cff', '#ffc266', '#ff809f', '#65b3ff',
                            '#ffdab3', '#ff9999', '#66cc99', '#e6ccff', '#ffb366',
                            '#99c2ff', '#ffe066'
                        ][:len(meses)]
                    }]
                }

                record.chart_cobros_mensuales = json.dumps(chart_data)
                _logger.info(f"Datos de la gráfica generados: {record.chart_cobros_mensuales}")

            except Exception as e:
                _logger.error(f"Error calculando chart_cobros_mensuales: {e}")
                record.chart_cobros_mensuales = json.dumps({})

    def _get_chart_data_cobro_mensuales(self, meses, valores):
        try:
            colores = CHART_COLORS
            hover_colores = CHART_HOVER_COLORS

            return {
                'type': 'bar',  # Especificamos el tipo de gráfica
                'labels': meses,
                'datasets': [{
                    'label': 'Cobros Mensuales',  # Añadimos una etiqueta para el conjunto de datos
                    'data': valores,
                    'backgroundColor': colores[:len(meses)],
                    'hoverBackgroundColor': hover_colores[:len(meses)]
                }]
            }
        except Exception as e:
            _logger.error(f"Error en _get_chart_data_cobro_mensuales: {e}")
            return {}
 ############################################Bryan############################################################################
    chart_cobros_mensuales_line = fields.Text(string="Datos del Gráfico de Pagos Mensuales (Línea)",
                                             compute="_compute_chart_cobros_mensuales_line")

    @api.depends('nombre_proyecto', 'seleccion_anio_proyecto')
    def _compute_chart_cobros_mensuales_line(self):
        _logger.info("Iniciando cálculo de chart_cobros_mensuales_line")

        for record in self:
            try:
                if record.nombre_proyecto and record.seleccion_anio_proyecto:
                    _logger.info(
                        f"Calculando para proyecto: {record.nombre_proyecto.name} y año: {record.seleccion_anio_proyecto}")

                    # Filtrar los cobros para el proyecto y año seleccionados usando id_un (centro de costo)
                    cobros_filtrados = self.env['cobros.siesa.line'].sudo().search([
                        ('id_un', '=', record.nombre_proyecto.mcb_centro_costo),
                        ('fecha', '!=', False),
                        ('fecha', '>=', f'{record.seleccion_anio_proyecto}-01-01'),
                        ('fecha', '<=', f'{record.seleccion_anio_proyecto}-12-31')
                    ])

                    _logger.info(f"Cobros encontrados: {len(cobros_filtrados)}")

                    cobros_por_mes = {}
                    for cobro in cobros_filtrados:
                        mes = cobro.fecha.strftime('%m')
                        if mes in cobros_por_mes:
                            cobros_por_mes[mes] += cobro.recaudado
                        else:
                            cobros_por_mes[mes] = cobro.recaudado

                    meses = [MONTH_NAMES[mes] for mes in sorted(cobros_por_mes.keys())]
                    valores = [cobros_por_mes[mes] for mes in sorted(cobros_por_mes.keys())]

                    _logger.info(f"Meses calculados: {meses}")
                    _logger.info(f"Valores calculados: {valores}")

                    # Generar los datos del gráfico de línea
                    line_chart_data = self._get_chart_data_cobros_mensuales_line(meses, valores)
                else:
                    _logger.warning("Proyecto o año no seleccionados")

            except Exception as e:
                _logger.error(f"Error en _compute_chart_pagos_mensuales_line: {e}")

            record.chart_cobros_mensuales_line = json.dumps(line_chart_data)
            _logger.info(f"Datos de la gráfica de línea generados: {record.chart_cobros_mensuales_line}")

    # Método para generar los datos del gráfico de líneas (nuevo)
    def _get_chart_data_cobros_mensuales_line(self, meses, valores):
        try:
            colores = CHART_COLORS
            hover_colores = CHART_HOVER_COLORS

            # Configuración para el gráfico de líneas
            return {
                'type': 'line',  # Especificar tipo de gráfico de líneas
                'labels': meses,
                'datasets': [{
                    'label': 'Cobros Mensuales (Línea)',  # Título para el gráfico de línea
                    'data': valores,
                    'fill': 'false',  # No se rellena debajo de la línea
                    'borderColor': '#FFB8B8',  # Color de la línea
                    'borderWidth': 2,  # Grosor de la línea
                    'lineTension': 0.1,  # Suavizado de la línea
                    'pointBackgroundColor': '#36a2eb',  # Color de los puntos
                    'pointRadius': 5,  # Tamaño de los puntos
                    'hoverBackgroundColor': hover_colores[:len(meses)]  # Colores de hover
                }]
            }
        except Exception as e:
            _logger.error(f"Error en _get_chart_data_cobros_mensuales_line: {e}")
            return {}



########################################################################################################################
    chart_pagos_mensuales = fields.Text(string="Datos del Gráfico de Pagos Mensuales", compute="_compute_chart_pagos_mensuales")

    @api.model
    def _get_selecciones_anio_pagos_mensuales(self):
        try:
            # Obtener contratos con fecha de pago no nula
            contratos_con_fecha_de_pago = self.env['contrato'].search([('fecha_de_pago', '!=', False)])

            # Filtrar años válidos (solo los que estén dentro de un rango razonable)
            anio_actual = datetime.now().year
            anios = list(set([
                contrato.fecha_de_pago.year for contrato in contratos_con_fecha_de_pago
                if 2000 <= contrato.fecha_de_pago.year <= anio_actual  # Filtrar solo años entre 2000 y el año actual
            ]))

            # Generar las opciones de año para el selector
            selecciones_anio = [(str(anio), str(anio)) for anio in sorted(anios, reverse=True)]

            _logger.info(f"Años disponibles para selección: {selecciones_anio}")

            return selecciones_anio
        except Exception as e:
            _logger.error(f"Error al obtener los años para el selector: {e}")
            return []

    @api.depends('nombre_proyecto', 'seleccion_anio_proyecto')
    def _compute_chart_pagos_mensuales(self):
        _logger.info("Iniciando cálculo de chart_pagos_mensuales")

        for record in self:
            chart_data = {}
            try:
                if record.nombre_proyecto and record.seleccion_anio_proyecto:
                    _logger.info(f"Calculando para proyecto: {record.nombre_proyecto.name} y año: {record.seleccion_anio_proyecto}")

                    pagos_por_mes = {}
                    if record.nombre_proyecto.mcb_centro_costo:
                        registros = self.env['pagos.siesa.line'].sudo().search([
                            ('id_un', '=', record.nombre_proyecto.mcb_centro_costo),
                            ('fecha_pago', '!=', False),
                            ('fecha_pago', '>=', f'{record.seleccion_anio_proyecto}-01-01'),
                            ('fecha_pago', '<=', f'{record.seleccion_anio_proyecto}-12-31')
                        ])
                        for r in registros:
                            mes = r.fecha_pago.strftime('%m')
                            val = r.valor_pagado
                            if mes in pagos_por_mes:
                                pagos_por_mes[mes] += val
                            else:
                                pagos_por_mes[mes] = val

                    meses = [MONTH_NAMES[mes] for mes in sorted(pagos_por_mes.keys())]
                    valores = [pagos_por_mes[mes] for mes in sorted(pagos_por_mes.keys())]

                    _logger.info(f"Meses calculados: {meses}")
                    _logger.info(f"Valores calculados: {valores}")

                    # Generar los datos del gráfico
                    chart_data = self._get_chart_data_pagos_mensuales(meses, valores)
                else:
                    _logger.warning("Proyecto o año no seleccionados")

            except Exception as e:
                _logger.error(f"Error en _compute_chart_pagos_mensuales: {e}")

            record.chart_pagos_mensuales = json.dumps(chart_data)
            _logger.info(f"Datos de la gráfica generados: {record.chart_pagos_mensuales}")

    def _get_chart_data_pagos_mensuales(self, meses, valores):
        try:
            colores = CHART_COLORS
            hover_colores = CHART_HOVER_COLORS

            return {
                'type': 'bar',  # Especificamos el tipo de gráfica
                'labels': meses,
                'datasets': [{
                    'label': 'Pagos Mensuales',  # Añadimos una etiqueta para el conjunto de datos
                    'data': valores,
                    'backgroundColor': colores[:len(meses)],
                    'hoverBackgroundColor': hover_colores[:len(meses)]
                }]
            }
        except Exception as e:
            _logger.error(f"Error en _get_chart_data_pagos_mensuales: {e}")
            return {}
########################################################################################################################
    @api.model
    def _get_dynamic_years(self):
        pagos = self.env['pago'].search([('fecha_recibido', '!=', False)])
        anios = sorted({pago.fecha_recibido.year for pago in pagos}, reverse=True)
        return [(str(anio), str(anio)) for anio in anios]

    seleccion_anio_cobros = fields.Selection(
        selection=_get_dynamic_years,
        string="Año Seleccionado",
    )
    pie_chart_data_by_project = fields.Char(
        string="Datos del Gráfico de Torta por Proyecto",
        compute="_compute_pie_chart_data_by_project"
    )

    @api.depends('seleccion_anio_cobros', 'proyecto_id')
    def _compute_pie_chart_data_by_project(self):
        for record in self:
            if not record.seleccion_anio_cobros or not record.proyecto_id:
                record.pie_chart_data_by_project = json.dumps({})
                continue

            try:
                # Filtrar los pagos por el año seleccionado y el proyecto
                domain = [
                    ('fecha_recibido', '!=', False),
                    ('fecha_recibido', '>=', f'{record.seleccion_anio_cobros}-01-01'),
                    ('fecha_recibido', '<=', f'{record.seleccion_anio_cobros}-12-31'),
                    ('proyecto_id', '=', record.proyecto_id.id)
                ]
                pagos_con_fecha_recibido = self.env['pago'].search(domain)

                acumulado_cobro_por_mes_anio = {}

                for pago in pagos_con_fecha_recibido:
                    mes_anio = pago.fecha_recibido.strftime('%m-%Y')
                    acumulado_cobro_por_mes_anio[mes_anio] = acumulado_cobro_por_mes_anio.get(mes_anio, 0) + pago.acomulado_cobro

                if not acumulado_cobro_por_mes_anio:
                    record.pie_chart_data_by_project = json.dumps({})
                    continue

                meses = [f"{MONTH_NAMES[mes.split('-')[0]]} {mes.split('-')[1]}" for mes in acumulado_cobro_por_mes_anio.keys()]
                valores = list(acumulado_cobro_por_mes_anio.values())

                pie_chart_data = self._get_pie_chart_data_by_project(meses, valores)
                record.pie_chart_data_by_project = json.dumps(pie_chart_data)

            except Exception as e:
                record.pie_chart_data_by_project = json.dumps({})
                _logger.error(f"Error al calcular pie_chart_data_by_project: {e}")

    def _get_pie_chart_data_by_project(self, meses, valores):
        colores = CHART_COLORS
        hover_colores = CHART_HOVER_COLORS
        colores = (colores * (len(meses) // len(colores) + 1))[:len(meses)]
        hover_colores = (hover_colores * (len(meses) // len(hover_colores) + 1))[:len(meses)]

        return {
            'labels': meses,
            'datasets': [{
                'data': valores,
                'backgroundColor': colores,
                'hoverBackgroundColor': hover_colores
            }]
        }
########################################################################################################################

    @api.depends('seleccion_anio_cobros', 'proyecto_id')
    def _compute_line_chart_data_by_project(self):
        for record in self:
            if not record.seleccion_anio_cobros or not record.proyecto_id:
                record.line_chart_cobros = json.dumps({})
                continue

            try:
                # Filtrar los pagos por el año seleccionado y el proyecto
                domain = [
                    ('fecha_recibido', '!=', False),
                    ('fecha_recibido', '>=', f'{record.seleccion_anio_cobros}-01-01'),
                    ('fecha_recibido', '<=', f'{record.seleccion_anio_cobros}-12-31'),
                    ('proyecto_id', '=', record.proyecto_id.id)
                ]
                pagos_con_fecha_recibido = self.env['pago'].search(domain)

                acumulado_cobro_por_mes_anio = {}

                for pago in pagos_con_fecha_recibido:
                    mes_anio = pago.fecha_recibido.strftime('%m-%Y')  # Mes y Año en formato MM-YYYY
                    acumulado_cobro_por_mes_anio[mes_anio] = acumulado_cobro_por_mes_anio.get(mes_anio,
                                                                                              0) + pago.acomulado_cobro

                if not acumulado_cobro_por_mes_anio:
                    record.line_chart_cobros = json.dumps({})
                    continue

                # Ordenar las claves para garantizar el orden correcto (de enero a diciembre)
                meses_ordenados = sorted(acumulado_cobro_por_mes_anio.keys(),
                                         key=lambda x: (x.split('-')[1], x.split('-')[0]))

                # Crear la lista de meses con los nombres
                meses = [f"{MONTH_NAMES[mes.split('-')[0]]} {mes.split('-')[1]}" for mes in meses_ordenados]
                # Obtener los valores acumulados
                valores = [acumulado_cobro_por_mes_anio[mes] for mes in meses_ordenados]

                # Obtener los datos para el gráfico
                line_chart_data = self._get_line_chart_data_by_project(meses, valores)
                record.line_chart_cobros = json.dumps(line_chart_data)

            except Exception as e:
                record.line_chart_cobros = json.dumps({})
                _logger.error(f"Error al calcular line_chart_cobros: {e}")

    def _get_line_chart_data_by_project(self, meses, valores):
        # Configurar los colores para la línea
        line_color = 'rgba(75, 192, 192, 1)'  # Un color verde-azul para la línea

        return {
            'labels': meses,
            'datasets': [{
                'label': 'Valores por Mes',
                'data': valores,
                'fill': 'false',  # No hay relleno debajo de la línea
                'borderColor': line_color,  # Color de la línea
                'borderWidth': 2,  # Grosor de la línea
                'lineTension': 0.1,  # Suavizado de la línea
                'pointBackgroundColor': line_color,  # Color de los puntos en la línea
                'pointRadius': 5  # Tamaño de los puntos
            }]
        }

    # Nuevo campo para el gráfico de línea
    line_chart_cobros = fields.Char(string="Datos del Gráfico de Línea por Proyecto", compute="_compute_line_chart_data_by_project")

    coordinador_id = fields.Many2one('res.users', string="Coordinador a Consultar")
    tecnica_coordinador = fields.Monetary(string="Técnica Coordinador", compute='_compute_coordinador_values', currency_field='currency_id')
    valor_total_coordinador = fields.Float(string="Presupuesto Coordinador", compute='_compute_coordinador_values')
    contratos_count_coordinador = fields.Integer(string="Total Contratos Coordinador", compute='_compute_coordinador_values')
    tecnica_promedio_coordinador = fields.Float(string="Avance Técnico Promedio", compute='_compute_coordinador_values')
    total_pagado_coordinador = fields.Float(string="Total Pagado", compute='_compute_coordinador_values')
    total_cobrado_coordinador = fields.Monetary(string="Total Cobrado", compute='_compute_coordinador_values', currency_field='currency_id')
    financiera_promedio_coordinador = fields.Float(string="Ejecución Financiera % Promedio (Coordinador)", compute='_compute_coordinador_values')
    flujo_neto_coordinador = fields.Float(string="Flujo Neto (Coordinador)", compute='_compute_coordinador_values')
    proyectos_count_coordinador = fields.Integer(string="Cantidad de Proyectos (Coordinador)", compute='_compute_coordinador_values')
    proyectos_activos_coordinador = fields.Integer(string="Proyectos Activos (Coordinador)", compute='_compute_coordinador_values')
    proyectos_cerrados_coordinador = fields.Integer(string="Proyectos Cerrados (Coordinador)", compute='_compute_coordinador_values')
    avatar_iniciales_coordinador = fields.Char(string="Iniciales del Coordinador", compute='_compute_coordinador_values')

    seleccion_anio_coordinador = fields.Selection(selection='_get_selecciones_anio_coordinador', string="Año Coordinador")
    chart_cobros_coordinador = fields.Text(string="Datos del Gráfico de Cobros del Coordinador", compute="_compute_chart_cobros_coordinador")
    chart_cobros_coordinador_line = fields.Text(string="Datos del Gráfico de Cobros del Coordinador (Línea)", compute="_compute_chart_cobros_coordinador_line")

    chart_pagos_coordinador = fields.Text(string="Datos del Gráfico de Pagos del Coordinador", compute="_compute_chart_pagos_coordinador")

    @api.model
    def _get_selecciones_anio_coordinador(self):
        try:
            pagos = self.env['pago'].sudo().search([('fecha_recibido', '!=', False)])
            anios = [p.fecha_recibido.year for p in pagos]
            # (Se eliminó la consulta a 'contrato' para evitar que errores de digitación en Odoo agreguen años falsos al selector)
            registros_siesa = self.env['pagos.siesa.line'].sudo().search([
                ('fecha_pago', '!=', False)
            ])
            anios += [r.fecha_pago.year for r in registros_siesa]
            anio_actual = datetime.now().year
            anios_validos = list(set([y for y in anios if 2000 <= y <= anio_actual + 1]))
            return [(str(y), str(y)) for y in sorted(anios_validos, reverse=True)]
        except Exception as e:
            _logger.error(f"Error al obtener los años para el selector de coordinador: {e}")
            return []

    chart_tecnica_financiera_coordinador = fields.Char(string="Datos del Gráfico Polar del Coordinador", compute="_compute_chart_tecnica_financiera_coordinador")

    @api.depends('coordinador_id')
    def _compute_coordinador_values(self):
        for record in self:
            if record.coordinador_id:
                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                record.tecnica_coordinador = sum(proyectos.mapped('resultado_tecnica_representada_dinero'))
                record.valor_total_coordinador = sum(proyectos.mapped('valor_por_proyecto_mcb'))
                # Misma corrección que en _compute_contratos_count / _compute_global_financiero:
                # `contratacion.tablero_avances_ids` está vacío en toda la BD.
                contrataciones = proyectos.mapped('ejecucion_id.contrataciones_id')
                record.contratos_count_coordinador = self.env['contrataciones_fuv'].sudo().search_count([
                    ('proyecto_relacionado', 'in', contrataciones.ids)
                ])
                record.tecnica_promedio_coordinador = sum(proyectos.mapped('suma_total')) / len(proyectos) if proyectos else 0.0
                # Mismo método (promedio simple entre proyectos) que tecnica_promedio_coordinador, para
                # que la "brecha técnica-financiera" del panel compare dos promedios calculados igual.
                record.financiera_promedio_coordinador = (
                    sum(proyectos.mapped('resultado_vpagado_barra')) / len(proyectos) if proyectos else 0.0)
                record.total_pagado_coordinador = self._pagado_siesa_sum(proyectos)
                # valor_cobrado (store=True) queda desactualizado -- se recalcula directo desde Siesa
                # (ver la misma corrección en _compute_global_financiero).
                ccs_coord = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]
                record.total_cobrado_coordinador = sum(self.env['cobros.siesa.line'].sudo().search(
                    [('id_un', 'in', ccs_coord)]).mapped('recaudado')) if ccs_coord else 0.0
                record.flujo_neto_coordinador = record.total_cobrado_coordinador - record.total_pagado_coordinador

                estado = self._estado_portafolio(proyectos)
                record.proyectos_count_coordinador = len(proyectos)
                record.proyectos_activos_coordinador = estado['en_ejecucion'] + estado['en_ejecucion_vencido']
                record.proyectos_cerrados_coordinador = estado['cerrados']

                palabras = record.coordinador_id.name.split() if record.coordinador_id.name else []
                record.avatar_iniciales_coordinador = ''.join(p[0].upper() for p in palabras[:2]) or '??'
            else:
                record.tecnica_coordinador = 0.0
                record.valor_total_coordinador = 0.0
                record.contratos_count_coordinador = 0
                record.tecnica_promedio_coordinador = 0.0
                record.financiera_promedio_coordinador = 0.0
                record.total_pagado_coordinador = 0.0
                record.total_cobrado_coordinador = 0.0
                record.flujo_neto_coordinador = 0.0
                record.proyectos_count_coordinador = 0
                record.proyectos_activos_coordinador = 0
                record.proyectos_cerrados_coordinador = 0
                record.avatar_iniciales_coordinador = ''
            _logger.info(
                ">>>>>>>> CONTENIDO DE RECORD:\n"
                "  - ID: %s\n"
                "  - Coordinador: %s (ID: %s)\n"
                "  - Técnica Coordinador: %s\n"
                "  - Presupuesto Total Coordinador: %s\n"
                "  - Total Contratos: %s\n"
                "  - Avance Técnico Promedio: %s\n"
                "  - Total Pagado: %s\n"
                "  - Total Cobrado: %s",
                record.id,
                record.coordinador_id.name if record.coordinador_id else "N/A",
                record.coordinador_id.id if record.coordinador_id else "N/A",
                record.tecnica_coordinador,
                record.valor_total_coordinador,
                record.contratos_count_coordinador,
                record.tecnica_promedio_coordinador,
                record.total_pagado_coordinador,
                record.total_cobrado_coordinador
            )

    @api.depends('coordinador_id', 'seleccion_anio_coordinador')
    def _compute_chart_cobros_coordinador(self):
        for record in self:
            try:
                if not record.coordinador_id or not record.seleccion_anio_coordinador:
                    record.chart_cobros_coordinador = json.dumps({})
                    continue

                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                
                ccs = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]
                if not ccs:
                    record.chart_cobros_coordinador = json.dumps({})
                    continue

                # Mismo criterio que _compute_chart_cobros_mensuales: filtrar por fecha_recaudo (cuándo
                # entró la plata), no por fecha (causación/facturación).
                cobros_filtrados = self.env['cobros.siesa.line'].sudo().search([
                    ('id_un', 'in', ccs),
                    ('fecha_recaudo', '!=', False),
                    ('fecha_recaudo', '>=', f'{record.seleccion_anio_coordinador}-01-01'),
                    ('fecha_recaudo', '<=', f'{record.seleccion_anio_coordinador}-12-31')
                ])

                if not cobros_filtrados:
                    record.chart_cobros_coordinador = json.dumps({})
                    continue

                cobros_por_mes = {}
                for cobro in cobros_filtrados:
                    mes = cobro.fecha_recaudo.strftime('%m')
                    if mes in cobros_por_mes:
                        cobros_por_mes[mes] += cobro.recaudado
                    else:
                        cobros_por_mes[mes] = cobro.recaudado

                meses = [MONTH_NAMES[mes] for mes in sorted(cobros_por_mes.keys())]
                valores = [cobros_por_mes[mes] for mes in sorted(cobros_por_mes.keys())]

                chart_data = self._get_chart_data_cobro_mensuales(meses, valores)
                record.chart_cobros_coordinador = json.dumps(chart_data)
            except Exception as e:
                _logger.error(f"Error en _compute_chart_cobros_coordinador: {e}")
                record.chart_cobros_coordinador = json.dumps({})

    @api.depends('coordinador_id', 'seleccion_anio_coordinador')
    def _compute_chart_cobros_coordinador_line(self):
        for record in self:
            try:
                if not record.coordinador_id or not record.seleccion_anio_coordinador:
                    record.chart_cobros_coordinador_line = json.dumps({})
                    continue

                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                
                ccs = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]
                if not ccs:
                    record.chart_cobros_coordinador_line = json.dumps({})
                    continue

                cobros_filtrados = self.env['cobros.siesa.line'].sudo().search([
                    ('id_un', 'in', ccs),
                    ('fecha', '!=', False),
                    ('fecha', '>=', f'{record.seleccion_anio_coordinador}-01-01'),
                    ('fecha', '<=', f'{record.seleccion_anio_coordinador}-12-31')
                ])

                if not cobros_filtrados:
                    record.chart_cobros_coordinador_line = json.dumps({})
                    continue

                cobros_por_mes = {}
                for cobro in cobros_filtrados:
                    mes = cobro.fecha.strftime('%m')
                    if mes in cobros_por_mes:
                        cobros_por_mes[mes] += cobro.recaudado
                    else:
                        cobros_por_mes[mes] = cobro.recaudado

                meses = [MONTH_NAMES[mes] for mes in sorted(cobros_por_mes.keys())]
                valores = [cobros_por_mes[mes] for mes in sorted(cobros_por_mes.keys())]

                line_chart_data = self._get_chart_data_cobros_mensuales_line(meses, valores)
                record.chart_cobros_coordinador_line = json.dumps(line_chart_data)
            except Exception as e:
                _logger.error(f"Error en _compute_chart_cobros_coordinador_line: {e}")
                record.chart_cobros_coordinador_line = json.dumps({})

    @api.depends('coordinador_id', 'seleccion_anio_coordinador')
    def _compute_chart_pagos_coordinador(self):
        for record in self:
            try:
                if not record.coordinador_id or not record.seleccion_anio_coordinador:
                    record.chart_pagos_coordinador = json.dumps({})
                    continue

                year = record.seleccion_anio_coordinador
                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                
                pagos_por_mes = {}
                
                ccs = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]
                if ccs:
                    registros = self.env['pagos.siesa.line'].sudo().search([
                        ('id_un', 'in', ccs),
                        ('fecha_pago', '!=', False),
                        ('fecha_pago', '>=', f'{year}-01-01'),
                        ('fecha_pago', '<=', f'{year}-12-31')
                    ])
                    for r in registros:
                        mes = r.fecha_pago.strftime('%m')
                        val = r.valor_pagado
                        pagos_por_mes[mes] = pagos_por_mes.get(mes, 0.0) + val

                if not pagos_por_mes:
                    record.chart_pagos_coordinador = json.dumps({})
                    continue

                meses = [MONTH_NAMES[mes] for mes in sorted(pagos_por_mes.keys())]
                valores = [pagos_por_mes[mes] for mes in sorted(pagos_por_mes.keys())]

                chart_data = self._get_chart_data_pagos_mensuales(meses, valores)
                record.chart_pagos_coordinador = json.dumps(chart_data)
            except Exception as e:
                _logger.error(f"Error en _compute_chart_pagos_coordinador: {e}")
                record.chart_pagos_coordinador = json.dumps({})

    @api.depends('coordinador_id')
    def _compute_chart_tecnica_financiera_coordinador(self):
        for record in self:
            if record.coordinador_id:
                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                ejecucion_financiera = self._pagado_siesa_sum(proyectos)
                ejecucion_tecnica = sum(proyectos.mapped('resultado_tecnica_representada_dinero'))
                
                chart_data = {
                    'labels': ['Ejecución Financiera', 'Ejecución Técnica'],
                    'datasets': [{
                        'label': 'Valores',
                        'data': [ejecucion_financiera, ejecucion_tecnica],
                        'backgroundColor': ['rgba(76, 95, 213, 0.6)', 'rgba(14, 124, 107, 0.6)']  # Indigo (financiera), Teal (técnica)
                    }]
                }
                record.chart_tecnica_financiera_coordinador = json.dumps(chart_data)
            else:
                record.chart_tecnica_financiera_coordinador = json.dumps({})

    @api.depends('seleccionador_graficas')
    def _compute_carga_coordinadores_chart_data(self):
        for record in self:
            proyectos = self.env['tablero_avances'].sudo().search([])
            carga_por_coordinador = defaultdict(float)
            cantidad_por_coordinador = defaultdict(int)

            for proy in proyectos:
                coordinador = proy.coordinador_asignado
                name = coordinador.name if coordinador else 'Sin Asignar'
                carga_por_coordinador[name] += proy.valor_por_proyecto_mcb or 0.0
                cantidad_por_coordinador[name] += 1

            # Orden por presupuesto, de mayor a menor.
            carga_ordenada = sorted(carga_por_coordinador.items(), key=lambda x: x[1], reverse=True)

            labels = [x[0] for x in carga_ordenada]
            valores = [x[1] for x in carga_ordenada]
            cantidades = [cantidad_por_coordinador[nombre] for nombre in labels]

            # Un solo gráfico combinado: barras (dinero, eje izquierdo) + línea con puntos
            # (cantidad de proyectos, eje derecho), para comparar ambas dimensiones sin
            # necesidad de dos gráficas separadas.
            chart_data = {
                'labels': labels,
                'datasets': [
                    {
                        'type': 'bar',
                        'label': 'Presupuesto Total Coordinado ($)',
                        'data': valores,
                        'backgroundColor': 'rgba(76, 95, 213, 0.65)',  # DASH_INDIGO
                        'borderColor': DASH_INDIGO,
                        'borderWidth': 1,
                        'yAxisID': 'y',
                        'order': 2,
                    },
                    {
                        'type': 'line',
                        'label': 'Cantidad de Proyectos',
                        'data': cantidades,
                        'backgroundColor': DASH_TEAL,
                        'borderColor': DASH_TEAL,
                        'borderWidth': 2,
                        'pointBackgroundColor': DASH_TEAL,
                        'pointRadius': 4,
                        'fill': False,
                        'tension': 0.3,
                        'yAxisID': 'y1',
                        'order': 1,
                    },
                ],
                'scales': {
                    'y': {
                        'type': 'linear',
                        'position': 'left',
                        'title': {'display': True, 'text': 'Presupuesto ($)'},
                        'beginAtZero': True,
                    },
                    'y1': {
                        'type': 'linear',
                        'position': 'right',
                        'title': {'display': True, 'text': '# Proyectos'},
                        'beginAtZero': True,
                        'grid': {'drawOnChartArea': False},
                        'ticks': {'precision': 0},
                    },
                },
            }
            record.carga_coordinadores_chart_data = json.dumps(chart_data)

    @api.depends('seleccionador_graficas')
    def _compute_proyectos_flujo_chart_data(self):
        for record in self:
            proyectos = self.env['tablero_avances'].sudo().search([])
            # Cobrado por proyecto: igual que en _compute_global_financiero, se recalcula directo
            # desde cobros.siesa.line (bypass del store=True `valor_cobrado`, que queda congelado).
            ccs = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]
            cobrado_por_cc = defaultdict(float)
            if ccs:
                cobros_siesa = self.env['cobros.siesa.line'].sudo().search([('id_un', 'in', ccs)])
                for c in cobros_siesa:
                    cobrado_por_cc[c.id_un] += c.recaudado or 0.0

            # Mismo signo que flujo_neto_global (cobrado - pagado): negativo = déficit de caja,
            # positivo = superávit de caja.
            flujos = []
            for proy in proyectos:
                pagado = proy.valor_pagado_dashboard or 0.0
                cobrado = cobrado_por_cc.get(proy.mcb_centro_costo, 0.0) if proy.mcb_centro_costo else 0.0
                flujo_neto_proyecto = cobrado - pagado
                flujos.append((proy.id, proy.name or 'Sin nombre', flujo_neto_proyecto))

            def _bar_chart(items, label, color, color_bg):
                labels = [x[1] for x in items]
                return json.dumps({
                    'labels': labels,
                    'ids': [x[0] for x in items],
                    'datasets': [{
                        'label': label,
                        'data': [x[2] for x in items],
                        'backgroundColor': color_bg,
                        'borderColor': color,
                        'borderWidth': 1,
                    }]
                })

            # Los 4 con mayor déficit (más negativo primero).
            deficits = sorted([f for f in flujos if f[2] < 0], key=lambda x: x[2])[:4]
            record.proyectos_flujo_negativo_chart_data = _bar_chart(
                deficits, 'Flujo Neto del Proyecto (Cobrado - Pagado)', DASH_NEG, 'rgba(214, 69, 65, 0.7)')

            # Los 4 con mayor superávit (más positivo primero).
            superavits = sorted([f for f in flujos if f[2] > 0], key=lambda x: x[2], reverse=True)[:4]
            record.proyectos_flujo_positivo_chart_data = _bar_chart(
                superavits, 'Flujo Neto del Proyecto (Cobrado - Pagado)', DASH_POS, 'rgba(31, 157, 107, 0.7)')

    @api.depends('seleccionador_graficas', 'nombre_proyecto', 'coordinador_id')
    def _compute_top_contratistas_chart_data(self):
        for record in self:
            montos_por_contratista = defaultdict(float)
            
            if record.seleccionador_graficas == '02' and record.nombre_proyecto:
                # 1. Gráfica para un proyecto específico
                proj = record.nombre_proyecto
                if proj.mcb_centro_costo:
                    registros = self.env['pagos.siesa.line'].sudo().search([
                        ('id_un', '=', proj.mcb_centro_costo),])
                    for reg in registros:
                        name = reg.contratista or reg.nit or 'Sin Nombre'
                        monto = reg.valor_pagado
                        montos_por_contratista[name] += monto
                        
            elif record.seleccionador_graficas == '03' and record.coordinador_id:
                # 2. Gráfica para un coordinador específico
                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                proy_odoo = self.env['tablero_avances']
                proy_siesa = proyectos
                
                if proy_odoo:
                    contrataciones = self.env['contratacion'].sudo().search([('tablero_avances_ids', 'in', proy_odoo.ids)])
                    contratos = self.env['contrataciones_fuv'].sudo().search([
                        ('proyecto_relacionado', 'in', contrataciones.ids),
                        ('contratista_proveedor_relacionado', '!=', False)
                    ])
                    for contr in contratos:
                        name = contr.contratista_proveedor_relacionado.name or 'Sin Nombre'
                        monto = contr.valor_total_contrato or 0.0
                        if contr.valor_contrato_otrosi:
                            monto += contr.valor_contrato_otrosi
                        montos_por_contratista[name] += monto
                        
                if proy_siesa:
                    ccs = [p.mcb_centro_costo for p in proy_siesa if p.mcb_centro_costo]
                    if ccs:
                        registros = self.env['pagos.siesa.line'].sudo().search([
                            ('id_un', 'in', ccs),])
                        for reg in registros:
                            name = reg.contratista or reg.nit or 'Sin Nombre'
                            monto = reg.valor_pagado
                            montos_por_contratista[name] += monto
            else:
                # 3. Gráfica global consolidada (comportamiento original robusto)
                proy_siesa = self.env['tablero_avances'].sudo().search([])
                ccs_siesa = [p.mcb_centro_costo for p in proy_siesa if p.mcb_centro_costo]
                
                mcbs_siesa = proy_siesa.mapped('ejecucion_id.id') + proy_siesa.mapped('new_ejecucion_id.id')
                contrataciones_siesa_ids = self.env['contratacion'].sudo().search([('ejecucion4_ids', 'in', mcbs_siesa)]).ids
                
                contratos = self.env['contrataciones_fuv'].sudo().search([
                    ('proyecto_relacionado', 'not in', contrataciones_siesa_ids),
                    ('contratista_proveedor_relacionado', '!=', False)
                ])
                for contr in contratos:
                    name = contr.contratista_proveedor_relacionado.name or 'Sin Nombre'
                    monto = contr.valor_total_contrato or 0.0
                    if contr.valor_contrato_otrosi:
                        monto += contr.valor_contrato_otrosi
                    montos_por_contratista[name] += monto
                    
                if ccs_siesa:
                    registros = self.env['pagos.siesa.line'].sudo().search([
                        ('id_un', 'in', ccs_siesa),])
                    for reg in registros:
                        name = reg.contratista or reg.nit or 'Sin Nombre'
                        monto = reg.valor_pagado
                        montos_por_contratista[name] += monto
                        
            top_contratistas = sorted(montos_por_contratista.items(), key=lambda x: x[1], reverse=True)[:10]
            
            labels = [x[0] for x in top_contratistas]
            valores = [x[1] for x in top_contratistas]
            
            chart_data = {
                'labels': labels,
                'datasets': [{
                    'label': 'Monto Total ($)',
                    'data': valores,
                    'backgroundColor': 'rgba(14, 124, 107, 0.65)',  # DASH_TEAL
                    'borderColor': DASH_TEAL,
                    'borderWidth': 1
                }]
            }
            record.top_contratistas_chart_data = json.dumps(chart_data)

    def _evm_para_proyecto(self, proj):
        """Calcula EV/AC/PV/CPI/SPI de UN proyecto (tablero_avances). Compartido por
        _compute_evm_indicators (un proyecto), _compute_evm_indicators_coordinador (agregado, EV/AC/PV
        se suman antes de dividir) y la tabla "Proyectos a cargo" del modo Coordinador (fila por
        proyecto) -- antes esta lógica vivía duplicada en los dos primeros, calculada por separado."""
        bac = proj.valor_proyecto_convenio2 or proj.valor_por_proyecto_mcb or 0.0
        ev = proj.resultado_tecnica_representada_dinero or 0.0
        if not ev and bac and proj.suma_total:
            ev = bac * (proj.suma_total / 100.0)

        ac = proj.valor_pagado_dashboard or 0.0
        cpi = (ev / ac) if ac > 0 else 1.0

        pv = 0.0
        if proj.fecha_inicio2 and proj.fecha_finalizacion_mcb_r:
            inicio = fields.Date.from_string(proj.fecha_inicio2)
            fin = fields.Date.from_string(proj.fecha_finalizacion_mcb_r)
            hoy = fields.Date.context_today(self)

            dias_totales = (fin - inicio).days
            if dias_totales > 0:
                dias_transcurridos = (hoy - inicio).days
                dias_transcurridos = max(0, min(dias_transcurridos, dias_totales))
                pv = bac * (dias_transcurridos / float(dias_totales))
            else:
                pv = bac
        else:
            pv = bac

        spi = (ev / pv) if pv > 0 else 1.0
        return {'ev': ev, 'ac': ac, 'pv': pv, 'bac': bac, 'cpi': cpi, 'spi': spi}

    @api.depends('nombre_proyecto', 'seleccion_anio_proyecto')
    def _compute_evm_indicators(self):
        """
        Calcula el SPI (Schedule Performance Index) y CPI (Cost Performance Index).
        SPI = Valor Ganado Técnico / Costo Planeado
        CPI = Valor Ganado Técnico / Costo Real Pagado (SIESA)
        """
        for record in self:
            cpi = 0.0
            spi = 0.0
            ev = 0.0
            if record.nombre_proyecto:
                evm = self._evm_para_proyecto(record.nombre_proyecto)
                cpi, spi, ev = evm['cpi'], evm['spi'], evm['ev']

            record.cpi_value = round(cpi, 2)
            record.spi_value = round(spi, 2)
            record.evm_sin_datos_tecnica = (ev == 0.0)

    def _gauge_chart_data(self, val, chart_label, sin_datos):
        # `sin_datos=True` significa que el Valor Ganado (EV) es 0 -- no hay avance técnico capturado
        # para este alcance, así que el número de CPI/SPI (siempre 0.00 en ese caso) no es un dato real
        # sobre el proyecto, solo refleja que "ejecución técnica" está vacía. En vez de un gauge rojo
        # "crítico" engañoso, se muestra un gauge gris neutro con "N/D".
        if sin_datos:
            return {
                'labels': ['Valor', 'Faltante'],
                'datasets': [{
                    'data': [0, 1],
                    'backgroundColor': ['#e2e8f0', '#f1f5f9'],
                    'borderWidth': 0
                }],
                'centerText': 'N/D',
                'chartLabel': f"{chart_label} (sin datos técnicos)",
                'statusText': 'Sin datos técnicos',
                'statusColor': '#6B7891',
                'statusBg': '#EEF1F6',
            }
        max_val = 1.5
        val_capped = min(val, max_val)
        remaining = max(0.0, max_val - val_capped)
        if val < 0.85:
            color = '#ef4444'  # Rojo (Crítico)
        elif val < 1.00:
            color = '#f59e0b'  # Amarillo (En Riesgo)
        else:
            color = '#10b981'  # Verde (Excelente)

        # Pastilla de estado con su propio umbral (0.95 / 1.05), más fino que el semáforo del arco --
        # calca el criterio del mockup de rediseño ("<0,95 alerta · 0,95-1,05 en meta · >1,05 favorable").
        desviacion_pct = abs(1.0 - val) * 100
        es_costo = (chart_label == "CPI")
        if val < 0.95:
            status_text = f"{'Sobrecosto' if es_costo else 'Retraso'} del {desviacion_pct:.0f}%"
            status_color, status_bg = '#B23A35', '#FBE2E1'
        elif val <= 1.05:
            status_text = "En meta"
            status_color, status_bg = '#0E7C6B', '#D7EDE8'
        else:
            status_text = "Favorable"
            status_color, status_bg = '#0E7C6B', '#D7EDE8'

        return {
            'labels': ['Valor', 'Faltante'],
            'datasets': [{
                'data': [val_capped, remaining],
                'backgroundColor': [color, '#f1f5f9'],
                'borderWidth': 0
            }],
            'centerText': f"{val:.2f}",
            'chartLabel': chart_label,
            'statusText': status_text,
            'statusColor': status_color,
            'statusBg': status_bg,
        }

    @api.depends('cpi_value', 'evm_sin_datos_tecnica')
    def _compute_cpi_chart_data(self):
        for record in self:
            chart_data = self._gauge_chart_data(record.cpi_value or 0.0, "CPI", record.evm_sin_datos_tecnica)
            record.cpi_chart_data = json.dumps(chart_data)

    @api.depends('spi_value', 'evm_sin_datos_tecnica')
    def _compute_spi_chart_data(self):
        for record in self:
            chart_data = self._gauge_chart_data(record.spi_value or 0.0, "SPI", record.evm_sin_datos_tecnica)
            record.spi_chart_data = json.dumps(chart_data)

    @api.depends('coordinador_id', 'seleccion_anio_coordinador')
    def _compute_evm_indicators_coordinador(self):
        """
        Calcula el SPI y CPI para la vista agrupada del Coordinador.
        Itera sobre todos los proyectos asignados al coordinador y promedia su salud.
        """
        for record in self:
            cpi = 0.0
            spi = 0.0
            total_ev = 0.0
            if record.coordinador_id:
                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                total_ac = 0.0
                total_pv = 0.0

                for proj in proyectos:
                    evm = self._evm_para_proyecto(proj)
                    total_ev += evm['ev']
                    total_ac += evm['ac']
                    total_pv += evm['pv']

                if total_ac > 0:
                    cpi = total_ev / total_ac
                else:
                    cpi = 1.0
                    
                if total_pv > 0:
                    spi = total_ev / total_pv
                else:
                    spi = 1.0
                    
            record.cpi_value_coordinador = round(cpi, 2)
            record.spi_value_coordinador = round(spi, 2)
            record.evm_sin_datos_tecnica_coordinador = (total_ev == 0.0)

    @api.depends('cpi_value_coordinador', 'evm_sin_datos_tecnica_coordinador')
    def _compute_cpi_chart_data_coordinador(self):
        for record in self:
            chart_data = self._gauge_chart_data(
                record.cpi_value_coordinador or 0.0, "CPI", record.evm_sin_datos_tecnica_coordinador)
            record.cpi_chart_data_coordinador = json.dumps(chart_data)

    @api.depends('spi_value_coordinador', 'evm_sin_datos_tecnica_coordinador')
    def _compute_spi_chart_data_coordinador(self):
        for record in self:
            chart_data = self._gauge_chart_data(
                record.spi_value_coordinador or 0.0, "SPI", record.evm_sin_datos_tecnica_coordinador)
            record.spi_chart_data_coordinador = json.dumps(chart_data)

    @api.model
    def _get_selecciones_anio_flujo_caja(self):
        try:
            # Exclusivamente Siesa: años en los que hay cobros o pagos reales, nunca datos de Odoo.
            cobros = self.env['cobros.siesa.line'].sudo().search([('fecha_recaudo', '!=', False)])
            anios = [c.fecha_recaudo.year for c in cobros]
            pagos = self.env['pagos.siesa.line'].sudo().search([('fecha_pago', '!=', False)])
            anios += [p.fecha_pago.year for p in pagos]
            anio_actual = datetime.now().year
            # Unconditionally include the current year to prevent validation errors with the field default
            anios.append(anio_actual)
            anios_validos = list(set([y for y in anios if 2000 <= y <= anio_actual + 1]))
            return [(str(y), str(y)) for y in sorted(anios_validos, reverse=True)]
        except Exception as e:
            _logger.error(f"Error al obtener los años para el selector de flujo de caja: {e}")
            return [(str(datetime.now().year), str(datetime.now().year))]

    @api.depends('seleccion_anio_flujo_caja', 'seleccion_anio_proyecto', 'seleccion_anio_coordinador',
                 'seleccionador_graficas', 'nombre_proyecto', 'coordinador_id')
    def _compute_chart_flujo_caja_data(self):
        month_names = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                       'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

        for record in self:
            # OJO: cada modo tiene su propio selector de año visible en pantalla; si aquí se usara
            # siempre seleccion_anio_flujo_caja, el gráfico de Proyectos/Coordinadores no reaccionaría
            # al selector "Seleccionar Año" que el usuario realmente tiene a la vista en esos modos.
            if record.seleccionador_graficas == '02':
                year = record.seleccion_anio_proyecto or str(datetime.now().year)
            elif record.seleccionador_graficas == '03':
                year = record.seleccion_anio_coordinador or str(datetime.now().year)
            else:
                year = record.seleccion_anio_flujo_caja or str(datetime.now().year)

            # 1. Identificar proyectos según selección del dashboard
            ingresos_mes = defaultdict(float)
            egresos_mes = defaultdict(float)

            if record.seleccionador_graficas == '02' and record.nombre_proyecto:
                proyectos = record.nombre_proyecto

                # Ingresos del proyecto -- exclusivamente Siesa (nunca el modelo 'pago' de Odoo)
                if proyectos.mcb_centro_costo:
                    cobros_siesa = self.env['cobros.siesa.line'].sudo().search([
                        ('id_un', '=', proyectos.mcb_centro_costo),
                        ('fecha_recaudo', '!=', False),
                        ('fecha_recaudo', '>=', f'{year}-01-01'),
                        ('fecha_recaudo', '<=', f'{year}-12-31')
                    ])
                    for c in cobros_siesa:
                        ingresos_mes[c.fecha_recaudo.month] += c.recaudado

                # Egresos del proyecto -- exclusivamente Siesa (nunca el modelo 'contrato' de Odoo)
                if proyectos.mcb_centro_costo:
                    registros = self.env['pagos.siesa.line'].sudo().search([
                        ('id_un', '=', proyectos.mcb_centro_costo),
                        ('fecha_pago', '!=', False),
                        ('fecha_pago', '>=', f'{year}-01-01'),
                        ('fecha_pago', '<=', f'{year}-12-31')
                    ])
                    for r in registros:
                        egresos_mes[r.fecha_pago.month] += r.valor_pagado

            elif record.seleccionador_graficas == '03' and record.coordinador_id:
                proyectos = self.env['tablero_avances'].sudo().search([('coordinador_asignado', '=', record.coordinador_id.id)])
                ccs = [p.mcb_centro_costo for p in proyectos if p.mcb_centro_costo]

                # Ingresos -- exclusivamente Siesa
                if ccs:
                    cobros_siesa = self.env['cobros.siesa.line'].sudo().search([
                        ('id_un', 'in', ccs),
                        ('fecha_recaudo', '!=', False),
                        ('fecha_recaudo', '>=', f'{year}-01-01'),
                        ('fecha_recaudo', '<=', f'{year}-12-31')
                    ])
                    for c in cobros_siesa:
                        ingresos_mes[c.fecha_recaudo.month] += c.recaudado

                # Egresos -- exclusivamente Siesa
                if ccs:
                    registros = self.env['pagos.siesa.line'].sudo().search([
                        ('id_un', 'in', ccs),
                        ('fecha_pago', '!=', False),
                        ('fecha_pago', '>=', f'{year}-01-01'),
                        ('fecha_pago', '<=', f'{year}-12-31')
                    ])
                    for r in registros:
                        egresos_mes[r.fecha_pago.month] += r.valor_pagado
            else:
                # Global Consolidado (seleccionador_graficas == '01' o vacío) -- exclusivamente Siesa
                cobros_siesa_global = self.env['cobros.siesa.line'].sudo().search([
                    ('fecha_recaudo', '!=', False),
                    ('fecha_recaudo', '>=', f'{year}-01-01'),
                    ('fecha_recaudo', '<=', f'{year}-12-31')
                ])
                for c in cobros_siesa_global:
                    ingresos_mes[c.fecha_recaudo.month] += c.recaudado

                proy_siesa = self.env['tablero_avances'].sudo().search([])
                ccs = [p.mcb_centro_costo for p in proy_siesa if p.mcb_centro_costo]
                if ccs:
                    registros = self.env['pagos.siesa.line'].sudo().search([
                        ('id_un', 'in', ccs),
                        ('fecha_pago', '!=', False),
                        ('fecha_pago', '>=', f'{year}-01-01'),
                        ('fecha_pago', '<=', f'{year}-12-31')
                    ])
                    for r in registros:
                        egresos_mes[r.fecha_pago.month] += r.valor_pagado
                        
            labels = month_names
            ingresos = [ingresos_mes[m] for m in range(1, 13)]
            egresos = [egresos_mes[m] for m in range(1, 13)]
            flujo_neto = [ingresos_mes[m] - egresos_mes[m] for m in range(1, 13)]
            
            chart_data = {
                'labels': labels,
                'datasets': [
                    {
                        'type': 'bar',
                        'label': 'Ingresos (Cobros)',
                        'data': ingresos,
                        'backgroundColor': 'rgba(31, 157, 107, 0.65)',  # DASH_POS
                        'borderColor': DASH_POS,
                        'borderWidth': 1
                    },
                    {
                        'type': 'bar',
                        'label': 'Egresos (Pagos)',
                        'data': egresos,
                        'backgroundColor': 'rgba(214, 69, 65, 0.65)',  # DASH_NEG
                        'borderColor': DASH_NEG,
                        'borderWidth': 1
                    },
                    {
                        'type': 'line',
                        'label': 'Flujo Neto (mensual)',
                        'data': flujo_neto,
                        'borderColor': DASH_INK,
                        'backgroundColor': 'rgba(15, 27, 45, 0.08)',
                        'borderWidth': 2,
                        'fill': False,
                        'tension': 0.3
                    }
                ]
            }
            record.chart_flujo_caja_data = json.dumps(chart_data)

    @api.depends('seleccionador_graficas', 'nombre_proyecto', 'seleccion_anio_proyecto', 'coordinador_id', 'seleccion_anio_coordinador')
    def _compute_dashboard_description(self):
        for record in self:
            desc = ""
            if record.seleccionador_graficas == '01':
                desc = """
                <div style="background-color: #eff6ff; border: 1px solid #dbeafe; border-radius: 8px; padding: 10px 16px; display: inline-flex; align-items: center; gap: 8px; width: 100%; box-sizing: border-box;">
                    <span class="fa fa-globe" style="font-size: 16px; color: #3b82f6; flex-shrink: 0;"></span>
                    <span style="color: #1e3a8a; font-size: 14px; font-weight: 500; line-height: 1.4;">
                        <strong>Consolidado Global:</strong> Mostrando el acumulado de todos los proyectos activos de la organización.
                    </span>
                </div>
                """
            elif record.seleccionador_graficas == '02':
                project_name = record.nombre_proyecto.name if record.nombre_proyecto else "Ninguno"
                year = record.seleccion_anio_proyecto or "Todos"
                desc = f"""
                <div style="background-color: #f0fdfa; border: 1px solid #ccfbf1; border-radius: 8px; padding: 10px 16px; display: inline-flex; align-items: center; gap: 8px; width: 100%; box-sizing: border-box;">
                    <span class="fa fa-folder-open" style="font-size: 16px; color: #0d9488; flex-shrink: 0;"></span>
                    <span style="color: #115e59; font-size: 14px; font-weight: 500; line-height: 1.4;">
                        Proyecto: <strong style="color: #0f172a; font-weight: 700;">{project_name}</strong> <span style="color: #0d9488; margin: 0 4px;">|</span> Año: <strong style="color: #0f172a; font-weight: 700;">{year}</strong>
                    </span>
                </div>
                """
            elif record.seleccionador_graficas == '03':
                coord_name = record.coordinador_id.name if record.coordinador_id else "Ninguno"
                year = record.seleccion_anio_coordinador or "Todos"
                desc = f"""
                <div style="background-color: #fffbeb; border: 1px solid #fef3c7; border-radius: 8px; padding: 10px 16px; display: inline-flex; align-items: center; gap: 8px; width: 100%; box-sizing: border-box;">
                    <span class="fa fa-user" style="font-size: 16px; color: #d97706; flex-shrink: 0;"></span>
                    <span style="color: #78350f; font-size: 14px; font-weight: 500; line-height: 1.4;">
                        Coordinador: <strong style="color: #0f172a; font-weight: 700;">{coord_name}</strong> <span style="color: #d97706; margin: 0 4px;">|</span> Año: <strong style="color: #0f172a; font-weight: 700;">{year}</strong>
                    </span>
                </div>
                """
            record.dashboard_description = desc

    def action_open_glosario(self):
        topic = self.env.context.get('default_topic')
        wizard = self.env['vistas_director.glosario'].create({'topic': topic} if topic else {})
        topics = wizard._get_glosario_topics()
        titulo = topics[topic][0] if topic in topics else 'Guía Técnica de Fórmulas y Metodología'
        return {
            'name': titulo,
            'type': 'ir.actions.act_window',
            'res_model': 'vistas_director.glosario',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }


class VistasDirectorGlosario(models.TransientModel):
    _name = 'vistas_director.glosario'
    _description = 'Glosario y Metodologia de Formulas'

    topic = fields.Char(string="Tema")
    html_content = fields.Html(string="Contenido", compute="_compute_html_content")

    def _tabla_glosario(self, titulo, color, filas, explicacion_simple=None):
        """Arma una sección de glosario: explicación en palabras simples (opcional) + título +
        tabla técnica Métrica/Fórmula/Significado/Origen ERP."""
        simple_html = ""
        if explicacion_simple:
            simple_html = f"""
        <div style="background-color: {color}14; border-left: 4px solid {color}; border-radius: 6px; padding: 12px 14px; margin-bottom: 14px;">
            <div style="font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: {color}; margin-bottom: 4px;">En palabras simples</div>
            <div style="font-size: 13.5px; color: #334155; line-height: 1.5;">{explicacion_simple}</div>
        </div>"""

        filas_html = ""
        for metrica, formula, significado, origen in filas:
            filas_html += f"""
                <tr style="border-bottom: 1px solid #f1f5f9;">
                    <td style="padding: 10px; font-weight: 600; color: #0f172a;">{metrica}</td>
                    <td style="padding: 10px; font-family: monospace; color: {color}; font-weight: 600;">{formula}</td>
                    <td style="padding: 10px; color: #475569;">{significado}</td>
                    <td style="padding: 10px; color: #64748b;">{origen}</td>
                </tr>"""
        return f"""<div style="margin-bottom: 8px;">
        <h3 style="color: {color}; border-left: 4px solid {color}; padding-left: 10px; font-size: 15px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px;">
            {titulo}
        </h3>{simple_html}
        <table style="width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px;">
            <thead>
                <tr style="background-color: #f8fafc; border-bottom: 2px solid #e2e8f0; text-align: left;">
                    <th style="padding: 10px; font-weight: 600; color: #475569; width: 20%;">Métrica</th>
                    <th style="padding: 10px; font-weight: 600; color: #475569; width: 25%;">Fórmula / Cálculo</th>
                    <th style="padding: 10px; font-weight: 600; color: #475569; width: 35%;">Significado / Interpretación</th>
                    <th style="padding: 10px; font-weight: 600; color: #475569; width: 20%;">Origen ERP</th>
                </tr>
            </thead>
            <tbody>{filas_html}
            </tbody>
        </table>
    </div>"""

    def _get_glosario_topics(self):
        """Diccionario {clave: (titulo_ventana, html_seccion)} -- una entrada por gráfica del dashboard.
        Varias gráficas de Proyectos y Coordinadores comparten la misma clave porque usan
        exactamente la misma fórmula (solo cambia el alcance: un proyecto vs. consolidado)."""
        return {
            'evm': ("Índices de Desempeño (CPI / SPI)", self._tabla_glosario(
                "Earned Value Management (EVM)", DASH_INDIGO, [
                    ("Presupuesto Base (BAC)", "Presupuesto Inicial",
                     "Valor del proyecto según convenio y costos totales de producción.", "MCB / Nueva MCB"),
                    ("Valor Ganado (EV)", "BAC × % Avance Técnico",
                     "Porción del presupuesto correspondiente al trabajo técnico real completado a la fecha.", "Ejecución / Entregables"),
                    ("Costo Real (AC)", "Suma de Pagos Efectuados",
                     "Monto total de pagos desembolsados a contratistas y proveedores.", "SIESA / Registro de Egresos"),
                    ("Valor Planificado (PV)", "BAC × % Tiempo Transcurrido",
                     "Valor del trabajo que se planificó haber completado linealmente a la fecha de consulta.", "Fechas Inicio/Fin MCB"),
                    ("Eficiencia del Costo (CPI)", "EV / AC",
                     "CPI ≥ 1.0: dentro o debajo del presupuesto. CPI &lt; 1.0: sobrecosto.", "Cálculo dinámico"),
                    ("Eficiencia del Cronograma (SPI)", "EV / PV",
                     "SPI ≥ 1.0: a tiempo o adelantado. SPI &lt; 1.0: atraso frente al plan.", "Cálculo dinámico"),
                ],
                explicacion_simple="El CPI y el SPI resumen la salud del proyecto en dos preguntas: <b>¿estamos gastando "
                "más o menos de lo que deberíamos</b> para el avance de obra que ya llevamos? (CPI) y <b>¿vamos atrasados "
                "o adelantados en el tiempo?</b> (SPI). El número ideal es 1.0; por debajo de 1.0 es una señal de alerta "
                "(sobrecosto o atraso), por encima es buena noticia. <b>⚠ En construcción:</b> ambos dependen enteramente "
                "del Valor Ganado (EV), que sale de la Ejecución Técnica -- dato que aún no está completo en toda la "
                "aplicación. Mientras un proyecto no tenga avance técnico registrado, el gauge muestra \"N/D\" en vez de "
                "un número, para no confundirlo con un proyecto realmente en crisis de costos.")),
            'flujo_caja': ("Flujo de Caja Mensual", self._tabla_glosario(
                "Flujo de Caja y Análisis Financiero", DASH_TEAL, [
                    ("Ingresos (Cobros)", "∑ Cobros del mes",
                     "Cobros/recaudo registrados en el mes de consulta.", "cobros.siesa.line + modelo Pago"),
                    ("Egresos (Pagos)", "∑ Pagos del mes",
                     "Desembolsos de caja reales efectuados a contratistas, agrupados por mes.", "pagos.siesa.line + modelo Contrato"),
                    ("Flujo Neto", "Ingresos − Egresos (de ese mes)",
                     "Saldo del mes específico, no acumulado. Si es negativo, ese mes se pagó más de lo que se cobró.", "Diferencia mensual"),
                ],
                explicacion_simple="Compara, mes a mes, cuánta plata <b>entró</b> (lo que se cobró de los financiadores/"
                "convenios) contra cuánta plata <b>salió</b> (lo que se le pagó a los contratistas). La línea negra es la "
                "diferencia de ese mes específico: si está por debajo de cero, ese mes se pagó más de lo que se cobró.")),
            'presupuesto_total': ("Presupuesto Total", self._tabla_glosario(
                "Presupuesto Total (Global)", DASH_WARN, [
                    ("Presupuesto Total", "∑ Presupuesto MCB de todos los proyectos",
                     "Suma del presupuesto formulado en el convenio/MCB de cada proyecto -- el monto total que el financiador acordó entregar para ejecutarlo.", "new_mcb / valor_por_proyecto_mcb"),
                ],
                explicacion_simple="Es el techo de lo que la organización espera <b>cobrar</b> del financiador para "
                "ejecutar los proyectos, según lo pactado en el convenio (MCB). Es un compromiso <b>independiente</b> del "
                "'Presupuesto Contratado' (lo que se promete pagar a contratistas) -- ninguno se calcula a partir del "
                "otro, son dos flujos de dinero distintos que conviven en el mismo proyecto. "
                "<b>Alcance:</b> suma TODOS los proyectos del portafolio, vigentes y vencidos -- no solo los que siguen "
                "activos hoy (ver 'Estado del Portafolio').")),
            'presupuesto_contratado': ("Presupuesto Contratado", self._tabla_glosario(
                "Presupuesto Contratado (Global)", DASH_INK, [
                    ("Presupuesto Contratado", "∑ (Valor contrato + Otrosí) de todos los proyectos",
                     "Suma de lo realmente contratado (contrataciones_fuv) en toda la organización, vía tablero_avances.ejecucion_id.contrataciones_id. No es el presupuesto formulado (MCB), es lo efectivamente contratado.", "contrataciones_fuv"),
                ],
                explicacion_simple="Es la plata que la organización se comprometió a <b>pagarle a los contratistas y "
                "proveedores</b> por los contratos firmados. <b>No es lo que vamos a cobrar</b> de los financiadores del "
                "proyecto -- eso es el 'Presupuesto Total' (MCB/convenio), un flujo de dinero totalmente distinto (ver "
                "'Cobros Acumulados' y 'Presupuesto Total'). Piénsalo como el techo total de gasto comprometido en "
                "contratos. <b>Alcance:</b> suma TODOS los proyectos del portafolio, vigentes y vencidos -- no solo los "
                "que siguen activos hoy (ver 'Estado del Portafolio').")),
            'cobros_acumulados': ("Cobros Acumulados", self._tabla_glosario(
                "Cobros Acumulados (Global)", DASH_TEAL, [
                    ("Cobros Acumulados", "∑ recaudado de todos los proyectos",
                     "Total histórico recaudado a la fecha para todos los proyectos activos (no filtrado por año, a diferencia del gráfico de Cobros mensuales). Se compara contra el Presupuesto Total (MCB), no contra lo Contratado.", "cobros.siesa.line"),
                ],
                explicacion_simple="Es la plata que ha <b>entrado</b> a la organización de parte de los financiadores o "
                "convenios de los proyectos (lo que hemos <b>cobrado</b>). No tiene relación directa con lo contratado a "
                "proveedores -- son dos flujos de dinero distintos, uno entra y otro sale. <b>Alcance:</b> suma TODOS "
                "los proyectos del portafolio, vigentes y vencidos -- no solo los que siguen activos hoy (ver 'Estado "
                "del Portafolio').")),
            'pagos_acumulados': ("Pagos Acumulados", self._tabla_glosario(
                "Pagos Acumulados (Global)", DASH_NEG, [
                    ("Pagos Acumulados", "∑ pagado de todos los proyectos",
                     "Total histórico desembolsado a la fecha para todos los proyectos activos (no filtrado por año, a diferencia del gráfico de Pagos mensuales).", "valor_pagado_dashboard (SIESA)"),
                ],
                explicacion_simple="Es la plata que ya <b>salió</b> de caja hacia los contratistas y proveedores -- lo que "
                "la organización ha <b>pagado</b> realmente, no lo que debe o tiene planeado pagar. <b>Alcance:</b> suma "
                "TODOS los proyectos del portafolio, vigentes y vencidos -- no solo los que siguen activos hoy (ver "
                "'Estado del Portafolio').")),
            'flujo_neto_global': ("Flujo Neto (Cash Gap)", self._tabla_glosario(
                "Flujo Neto Acumulado (Global)", DASH_WARN, [
                    ("Flujo Neto (Cash Gap)", "Cobrado global − Pagado global",
                     "Saldo de caja acumulado a la fecha (no mensual). Si es negativo, la organización ha pagado más de lo que ha recaudado en total.", "Cálculo dinámico"),
                ],
                explicacion_simple="Es la resta entre lo cobrado y lo pagado, mirando <b>toda la historia hasta hoy</b> "
                "(no un mes en particular). Si el número es negativo, en total la organización ha pagado más de lo que ha "
                "cobrado -- una señal de que la caja está apretada. <b>Alcance:</b> suma TODOS los proyectos del "
                "portafolio, vigentes y vencidos -- no solo los que siguen activos hoy (ver 'Estado del Portafolio').")),
            'cobros_acumulados_proyecto': ("Cobros Acumulados (Proyecto)", self._tabla_glosario(
                "Cobros Acumulados del Proyecto", DASH_TEAL, [
                    ("Cobros Acumulados", "∑ recaudado de este proyecto",
                     "Total histórico recaudado a la fecha para este proyecto (no filtrado por año, a diferencia del gráfico de Cobros mensuales).", "cobros.siesa.line"),
                ],
                explicacion_simple="Es la plata que ha <b>entrado</b> de parte del financiador o convenio de "
                "<b>este proyecto</b>, sumando toda su historia hasta hoy (no solo el año seleccionado). Mismo "
                "cálculo que \"Cobros Acumulados\" en la vista Global, pero solo para este proyecto.")),
            'pagos_acumulados_proyecto': ("Pagos Acumulados (Proyecto)", self._tabla_glosario(
                "Pagos Acumulados del Proyecto", DASH_NEG, [
                    ("Pagos Acumulados", "∑ pagado de este proyecto",
                     "Total histórico desembolsado a la fecha para este proyecto (no filtrado por año, a diferencia del gráfico de Pagos mensuales).", "valor_pagado_dashboard (SIESA)"),
                ],
                explicacion_simple="Es la plata que ya <b>salió</b> de caja hacia los contratistas y proveedores de "
                "<b>este proyecto</b>, sumando toda su historia hasta hoy. Mismo cálculo que \"Pagos Acumulados\" "
                "en la vista Global, pero solo para este proyecto.")),
            'flujo_neto_proyecto': ("Flujo Neto (Cash Gap) del Proyecto", self._tabla_glosario(
                "Flujo Neto Acumulado del Proyecto", DASH_WARN, [
                    ("Flujo Neto (Cash Gap)", "Cobrado del proyecto − Pagado del proyecto",
                     "Saldo de caja acumulado a la fecha para este proyecto (no mensual). Si es negativo, se ha pagado más de lo que se ha recaudado en este proyecto.", "Cálculo dinámico"),
                ],
                explicacion_simple="Es la resta entre lo cobrado y lo pagado de <b>este proyecto</b>, mirando toda "
                "su historia hasta hoy. Si el número es negativo, este proyecto en particular ha pagado más de lo "
                "que ha cobrado. Mismo cálculo que \"Flujo Neto (Cash Gap)\" en la vista Global, pero solo para "
                "este proyecto.")),
            'focos_atencion': ("Focos de Atención", self._tabla_glosario(
                "Motor de Alertas (calculado en tiempo real)", DASH_NEG, [
                    ("Flujo de caja negativo", "Cobrado global − Pagado global &lt; 0",
                     "La organización ha pagado más de lo que ha recaudado, acumulado a la fecha.", "cobros/pagos SIESA"),
                    ("Cartera por recaudar", "Cobrado / Contratado &lt; 15%",
                     "Poco de lo contratado se ha cobrado todavía; prioriza cuentas de cobro pendientes.", "Cálculo dinámico"),
                    ("Concentración de riesgo", "Top-2 coordinadores / Total &gt; 50%",
                     "Dos coordinadores concentran más de la mitad del presupuesto total coordinado.", "tablero_avances"),
                    ("Dato inconsistente", "Conteo de contrataciones = 0",
                     "Señal de que la relación proyecto → contratación no está bien enlazada para algún registro.", "Chequeo de integridad"),
                    ("Obra avanza sobre facturación", "Ejecución técnica &gt; Ejecución financiera",
                     "El avance físico reportado es mayor al dinero realmente pagado (buena señal).", "Cálculo dinámico"),
                ],
                explicacion_simple="Es un chequeo automático que revisa 5 señales cada vez que se abre el dashboard, y "
                "solo muestra las que aplican en este momento: si la caja está en rojo, si falta cobrar mucho, si dos "
                "coordinadores concentran demasiado presupuesto, si hay un dato mal enlazado en el sistema, o si la obra "
                "avanza más rápido de lo que se factura (esta última es una buena señal, no una alerta de riesgo). "
                "<b>Alcance:</b> todas las reglas evalúan TODOS los proyectos del portafolio, vigentes y vencidos.")),
            'portafolio': ("Estado del Portafolio", self._tabla_glosario(
                "Estado del Portafolio", DASH_INDIGO, [
                    ("En ejecución", "Sin cierre finalizado, días restantes &gt; 0",
                     "Proyecto dentro de su fecha de finalización (MCB) planeada, y sin un cierre formal completado.", "fecha_finalizacion MCB + modelo cierre"),
                    ("En ejecución (vencido)", "Sin cierre finalizado, días restantes ≤ 0",
                     "Ya pasó su fecha de finalización planeada, pero todavía no tiene un cierre formal completado (puede estar en trámite: coordinador → financiero → jurídico).", "fecha_finalizacion MCB + modelo cierre"),
                    ("Cerrados", "Cierre con state = 'finalizado'",
                     "Proyecto con un registro en el modelo cierre que llegó a la etapa final ('Cerrar proyecto'). Es el único estado que refleja un cierre administrativo real.", "modelo cierre"),
                ],
                explicacion_simple="Clasifica cada proyecto en 3 estados: <b>En ejecución</b> (dentro de fecha, sin "
                "cierre iniciado o completado), <b>En ejecución (vencido)</b> (ya pasó la fecha planeada pero el cierre "
                "formal -- si existe -- todavía no llegó a la etapa final), y <b>Cerrados</b> (el trámite de cierre sí "
                "se completó). El trámite de cierre tiene sus propias etapas internas (coordinador, financiero, "
                "jurídico) antes de llegar a 'finalizado'; mientras no llegue ahí, el proyecto sigue contando como "
                "'en ejecución' aunque ya esté vencido.")),
            'ejecucion_tecnica_financiera': ("Ejecución Técnica vs. Financiera", self._tabla_glosario(
                "Ejecución Técnica, Financiera y Recaudo", DASH_TEAL, [
                    ("Ejecución técnica", "Técnica global / Contratado",
                     "Avance físico de obra reportado, como % de lo contratado.", "Ejecución / Entregables"),
                    ("Ejecución financiera", "Pagado global / Contratado",
                     "Dinero realmente desembolsado, como % de lo contratado (lo que se promete PAGAR a contratistas).", "SIESA - pagos"),
                    ("Recaudo (cobrado)", "Cobrado global / Presupuesto Total",
                     "Dinero realmente recaudado, como % del Presupuesto Total (lo que se espera COBRAR del financiador) -- no de lo contratado, son techos distintos.", "SIESA - cobros"),
                ],
                explicacion_simple="Compara tres avances distintos, con dos techos diferentes: técnica y ejecución "
                "financiera se miden contra lo <b>contratado</b> con proveedores (lo que se va a pagar); el recaudo se "
                "mide contra el <b>Presupuesto Total</b> (lo que se espera cobrar del financiador) -- son dos flujos de "
                "dinero independientes, por eso cada uno usa su propio techo. Lo ideal es que la técnica vaya adelante "
                "de la financiera -- significa que se está pagando menos de lo que ya se avanzó. <b>Alcance:</b> las "
                "tres barras suman TODOS los proyectos del portafolio, vigentes y vencidos -- no solo los que siguen "
                "activos hoy (ver 'Estado del Portafolio'). <b>⚠ En construcción:</b> el dato de Ejecución Técnica "
                "todavía no está listo en toda la aplicación -- no lo tomes como definitivo por ahora.")),
            'composicion_financiera': ("Composición Financiera", self._tabla_glosario(
                "Del Contratado al Cobrado", DASH_WARN, [
                    ("Contratado", "100% (línea base)",
                     "Suma de todos los contratos + otrosí de la organización.", "contrataciones_fuv"),
                    ("Ejecutado (técnico)", "Técnica global / Contratado",
                     "Avance físico valorizado, frente a lo contratado.", "Ejecución / Entregables"),
                    ("Pagado", "Pagado global / Contratado",
                     "Lo realmente desembolsado, frente a lo contratado.", "SIESA - pagos"),
                    ("Cobrado", "Cobrado global / Presupuesto Total",
                     "Lo realmente recaudado, frente al Presupuesto Total (no frente a lo contratado -- es un techo distinto, ver 'Presupuesto Total').", "SIESA - cobros"),
                ],
                explicacion_simple="Muestra el camino del dinero en un solo vistazo: empieza en el 100% de lo "
                "<b>contratado</b> con proveedores, sigue con cuánto se ha avanzado en obra y cuánto ya se <b>pagó</b> "
                "-- ambos frente a ese mismo 100%. La barra de \"Cobrado\" es distinta: se mide frente al Presupuesto "
                "Total (lo que se espera recibir del financiador), no frente a lo contratado, porque es un flujo de "
                "dinero aparte. <b>Alcance:</b> las 4 barras suman TODOS los proyectos del portafolio, vigentes y "
                "vencidos -- no solo los que siguen activos hoy (ver 'Estado del Portafolio'). <b>⚠ En construcción:</b> "
                "la barra \"Ejecutado (técnico)\" todavía no está lista en toda la aplicación -- no la tomes como "
                "definitiva por ahora.")),
            'carga_coordinador': ("Carga de Trabajo por Coordinador", self._tabla_glosario(
                "Carga de Trabajo por Coordinador", DASH_INDIGO, [
                    ("Presupuesto Total Coordinado", "∑ Presupuesto MCB de sus proyectos",
                     "Suma del presupuesto formulado (MCB) de todos los proyectos activos a cargo de cada coordinador. Barras, eje izquierdo.", "tablero_avances"),
                    ("Cantidad de Proyectos", "Conteo de tablero_avances por coordinador",
                     "Número de proyectos activos asignados a cada coordinador, sin importar su tamaño en presupuesto. Línea, eje derecho.", "tablero_avances"),
                ],
                explicacion_simple="Muestra dos cosas a la vez para cada coordinador: las <b>barras</b> son el "
                "presupuesto de sus proyectos (eje izquierdo) y la <b>línea</b> con puntos es la cantidad de "
                "proyectos que tiene a cargo (eje derecho). Un coordinador puede tener pocos proyectos muy grandes "
                "(barra alta, punto bajo) o muchos proyectos pequeños (barra baja, punto alto) -- por eso conviene "
                "mirar ambas dimensiones juntas. Sirve para ver si la carga de trabajo está repartida de forma "
                "pareja o si se está concentrando demasiado en pocas personas (eso es justo lo que revisa la alerta "
                "de \"Concentración de riesgo\" en Focos de Atención). <b>Alcance:</b> incluye TODOS los proyectos "
                "de cada coordinador, vigentes y vencidos.")),
            'proyectos_flujo_negativo': ("Proyectos con Mayor Flujo de Caja Negativo", self._tabla_glosario(
                "Proyectos con Mayor Flujo de Caja Negativo", DASH_NEG, [
                    ("Flujo Neto del Proyecto", "Cobrado del proyecto - Pagado del proyecto",
                     "Mismo cálculo que el KPI \"Flujo Neto (Cash Gap)\" global, pero por proyecto individual.", "cobros.siesa.line / pagos.siesa.line"),
                ],
                explicacion_simple="Cuando el \"Flujo Neto (Cash Gap)\" global sale negativo, esta gráfica responde "
                "la pregunta \"¿cuáles proyectos lo están causando?\": muestra los 4 proyectos donde se ha "
                "<b>pagado más de lo que se ha cobrado</b>, ordenados de mayor a menor déficit. Haz clic en una "
                "barra para saltar directo al dashboard de ese proyecto. Solo se ve cuando el Flujo Neto global es "
                "negativo (si es positivo, en su lugar aparece \"Proyectos con Mayor Flujo de Caja Positivo\"). "
                "<b>Alcance:</b> solo aparecen proyectos con déficit (cobrado menor que pagado); si un proyecto no "
                "aparece es porque va cobrando al día o por delante de lo pagado.")),
            'proyectos_flujo_positivo': ("Proyectos con Mayor Flujo de Caja Positivo", self._tabla_glosario(
                "Proyectos con Mayor Flujo de Caja Positivo", DASH_POS, [
                    ("Flujo Neto del Proyecto", "Cobrado del proyecto - Pagado del proyecto",
                     "Mismo cálculo que el KPI \"Flujo Neto (Cash Gap)\" global, pero por proyecto individual.", "cobros.siesa.line / pagos.siesa.line"),
                ],
                explicacion_simple="Cuando el \"Flujo Neto (Cash Gap)\" global sale positivo, esta gráfica muestra "
                "los 4 proyectos que más aportan a ese superávit: donde se ha <b>cobrado más de lo que se ha "
                "pagado</b>, ordenados de mayor a menor. Haz clic en una barra para saltar directo al dashboard de "
                "ese proyecto. Solo se ve cuando el Flujo Neto global es positivo (si es negativo, en su lugar "
                "aparece \"Proyectos con Mayor Flujo de Caja Negativo\"). <b>Alcance:</b> solo aparecen proyectos "
                "con superávit (cobrado mayor que pagado); si un proyecto no aparece es porque va pagando al día o "
                "por delante de lo cobrado.")),
            'top_contratistas': ("Top 10 Contratistas por Monto", self._tabla_glosario(
                "Top 10 Contratistas por Monto", DASH_TEAL, [
                    ("Monto Total", "∑ (Valor contrato + Otrosí)",
                     "Suma de lo contratado por cada contratista/proveedor en toda la organización (incluye adiciones).", "contrataciones_fuv / pagos.siesa.line"),
                ],
                explicacion_simple="Muestra a qué contratistas o proveedores se les ha comprometido más plata en "
                "contratos (sumando también las adiciones u otrosí). Es sobre lo <b>contratado</b>, no sobre lo ya "
                "pagado. <b>Alcance:</b> suma TODOS los proyectos del portafolio, vigentes y vencidos.")),
            'cobros_mensuales': ("Cobros Mensuales", self._tabla_glosario(
                "Cobros Mensuales (Año Seleccionado)", DASH_TEAL, [
                    ("Cobros del mes", "∑ recaudado agrupado por mes",
                     "Recaudo/facturación recibida en el año filtrado, mes a mes, para el alcance seleccionado (proyecto o coordinador).", "cobros.siesa.line"),
                ],
                explicacion_simple="Cuánta plata <b>entró</b> de los financiadores o convenios, mes a mes, durante el "
                "año que elijas en el selector.")),
            'pagos_mensuales': ("Pagos Mensuales", self._tabla_glosario(
                "Pagos Mensuales (Año Seleccionado)", DASH_NEG, [
                    ("Pagos del mes", "∑ pagado agrupado por mes",
                     "Egresos y desembolsos reales realizados a contratistas en el año filtrado, mes a mes.", "pagos.siesa.line"),
                ],
                explicacion_simple="Cuánta plata <b>salió</b> hacia los contratistas, mes a mes, durante el año que "
                "elijas en el selector.")),
            'tecnica_vs_financiera_historico': ("Ejecución Técnica vs. Financiera (Histórico)", self._tabla_glosario(
                "Ejecución Técnica vs. Financiera (Histórico Total)", DASH_INDIGO, [
                    ("Ejecución técnica", "Valor ganado acumulado (histórico)",
                     "Avance físico valorizado en dinero desde el inicio del proyecto/coordinador.", "Ejecución / Entregables"),
                    ("Ejecución financiera", "Pagos acumulados (histórico)",
                     "Dinero realmente desembolsado desde el inicio, sin filtrar por año.", "SIESA - pagos"),
                ],
                explicacion_simple="Compara, desde que arrancó el proyecto (o los proyectos del coordinador), cuánto se "
                "ha avanzado en obra contra cuánto se ha <b>pagado</b> realmente -- sin importar el año, es la foto "
                "completa desde el inicio hasta hoy. <b>⚠ En construcción:</b> el dato de Ejecución Técnica todavía no "
                "está listo en toda la aplicación -- no lo tomes como definitivo por ahora.")),
            'focos_atencion_proyecto': ("Focos de Atención (Proyecto)", self._tabla_glosario(
                "Motor de Alertas del Proyecto (calculado en tiempo real)", DASH_NEG, [
                    ("Flujo de caja negativo", "Cobrado del proyecto − Pagado del proyecto &lt; 0",
                     "Este proyecto ha pagado más de lo que ha recaudado, acumulado a la fecha.", "cobros/pagos SIESA"),
                    ("Sin cronograma de cierre", "Fecha de finalización MCB/otrosí vacía",
                     "No hay fecha de fin registrada; bloquea el cálculo de días faltantes y del SPI.", "MCB / Otrosí"),
                    ("Cobros por encima del presupuesto", "Cobrado &gt; Presupuesto Total",
                     "Se ha cobrado más de lo formulado en el MCB; puede indicar adiciones no registradas.", "Cálculo dinámico"),
                    ("Financiera adelanta a la técnica", "% Financiero &gt; % Técnico", "Se factura más rápido de lo que avanza la obra reportada.", "Cálculo dinámico"),
                    ("Retraso de cronograma", "SPI &lt; 0.95", "El proyecto va por detrás de lo planeado.", "Cálculo dinámico"),
                ],
                explicacion_simple="Igual que \"Focos de Atención\" en la vista Global, pero mirando solo <b>este "
                "proyecto</b>: reglas simples que revisan datos reales y avisan cuando algo necesita atención. "
                "<b>⚠ En construcción:</b> la alerta \"Financiera adelanta a la técnica\" y \"Retraso de cronograma\" "
                "dependen de Ejecución Técnica -- mientras ese dato esté vacío para el proyecto, esas alertas no se "
                "muestran (en vez de alarmar con un número que no significa nada).")),
            'liquidez_proyecto': ("Liquidez del Proyecto", self._tabla_glosario(
                "Liquidez y Flujo de Caja del Proyecto", DASH_POS, [
                    ("Pagado (salida)", "Pagos Acumulados del proyecto", "Lo que ya salió de caja hacia contratistas y proveedores de este proyecto.", "SIESA - pagos"),
                    ("Disponible (saldo)", "Cobrado − Pagado (si es positivo)", "Lo que queda del dinero recaudado después de pagar lo desembolsado.", "Cálculo dinámico"),
                ],
                explicacion_simple="De todo lo que se ha <b>cobrado</b> para este proyecto, muestra qué tanto ya se "
                "\"gastó\" (pagado) y qué tanto queda disponible. Si el proyecto está en déficit (se ha pagado más de "
                "lo cobrado), en vez del donut normal se muestra una sola porción roja de \"Déficit\".")),
            'ejecucion_tecnica_financiera_proyecto': ("Ejecución Técnica vs Financiera (Proyecto)", self._tabla_glosario(
                "Ejecución Técnica vs Financiera del Proyecto", DASH_TEAL, [
                    ("Ejecución técnica", "% Avance técnico", "Porcentaje de avance físico/de actividades reportado para este proyecto.", "Ejecución / Entregables"),
                    ("Ejecución financiera", "Pagado / Presupuesto Convenio × 100", "Porcentaje de lo pagado frente al presupuesto del convenio, acotado a 100%.", "SIESA - pagos"),
                    ("Brecha técnica–financiera", "% Financiera − % Técnica", "Diferencia entre ambos porcentajes; positiva si se paga más rápido de lo que avanza la obra.", "Cálculo dinámico"),
                ],
                explicacion_simple="Compara qué tan avanzada está la obra (técnica) contra qué tanto se ha pagado "
                "(financiera) de <b>este proyecto</b>, y la diferencia entre ambas (brecha). <b>⚠ En construcción:</b> "
                "el dato de Ejecución Técnica todavía no está completo en toda la aplicación -- la brecha hereda esa "
                "misma incertidumbre, no la tomes como un hallazgo definitivo.")),
            'balance_caja_proyecto': ("Balance de Caja (Proyecto)", self._tabla_glosario(
                "Balance de Caja del Proyecto", DASH_INDIGO, [
                    ("Cobrado (entradas)", "100% (línea base)", "Todo lo recaudado para este proyecto -- el punto de partida de este panel.", "cobros.siesa.line"),
                    ("Pagado (salidas)", "Pagado / Cobrado × 100", "Lo desembolsado, como porcentaje de lo que entró (no de lo contratado).", "pagos.siesa.line"),
                    ("Saldo disponible", "Flujo Neto / Cobrado × 100", "Lo que queda del dinero recaudado, como porcentaje de lo que entró.", "Cálculo dinámico"),
                    ("Presupuesto contratado", "Contratado / Cobrado × 100", "Referencia: cuánto es lo contratado comparado con lo que ya entró (puede superar 100%).", "MCB / Nueva MCB"),
                ],
                explicacion_simple="A diferencia de \"Composición Financiera\" (que mide todo contra lo <b>contratado</b>), "
                "este panel mide todo contra lo <b>cobrado</b>: responde \"¿en qué se fue la plata que ya entró?\", no "
                "\"¿cuánto se ha ejecutado del contrato?\". Por eso el \"Presupuesto contratado\" aparece aquí solo como "
                "referencia, no como el 100% base. <b>Alcance:</b> si el proyecto no tiene cobros registrados todavía, "
                "este panel no se puede calcular y se muestra un aviso en su lugar.")),
            'focos_atencion_coordinador': ("Focos de Atención (Coordinador)", self._tabla_glosario(
                "Motor de Alertas del Coordinador (calculado en tiempo real)", DASH_NEG, [
                    ("Cronograma en riesgo", "SPI &lt; 0.90 en al menos 1 proyecto",
                     "Lista los proyectos de este coordinador cuyo avance de cronograma está por debajo de la meta.", "Cálculo dinámico"),
                    ("Alta concentración presupuestal", "Presupuesto del proyecto mayor &gt; 30% del total coordinado",
                     "Un solo proyecto concentra una parte grande del presupuesto a cargo de este coordinador.", "Cálculo dinámico"),
                    ("Pago atípico", "Pagos de un mes &gt; 40% del total pagado en el año",
                     "Un mes concentra una porción desproporcionada de los pagos anuales, lo que puede ocultar picos de caja.", "SIESA - pagos"),
                    ("Costo bajo control", "0.95 ≤ CPI promedio ≤ 1.05", "El gasto conjunto de los proyectos está cerca de lo presupuestado.", "Cálculo dinámico"),
                ],
                explicacion_simple="Igual que \"Focos de Atención\" en la vista Global o de Proyecto, pero mirando el "
                "conjunto de proyectos de <b>este coordinador</b>. <b>⚠ En construcción:</b> la alerta de cronograma en "
                "riesgo depende de Ejecución Técnica -- si ningún proyecto del coordinador tiene ese dato capturado, la "
                "alerta simplemente no aparece.")),
            'proyectos_coordinador': ("Proyectos a Cargo", self._tabla_glosario(
                "Tabla de Proyectos del Coordinador", DASH_INDIGO, [
                    ("Presupuesto", "valor_por_proyecto_mcb", "Presupuesto formulado en el MCB de cada proyecto.", "MCB"),
                    ("Ejec. técnica / Ejec. financiera", "% técnico y % pagado de cada proyecto", "Mismos porcentajes que se muestran en el panel de ejecución, uno por proyecto.", "Ejecución / SIESA"),
                    ("CPI / SPI", "Valor Ganado / Costo Real, Valor Ganado / Valor Planificado", "Eficiencia de costo y cronograma de cada proyecto individual.", "Cálculo dinámico"),
                    ("Estado", "Cerrado / En ejecución / En riesgo", "\"Cerrado\" exige un trámite de cierre finalizado; \"En riesgo\" marca SPI bajo o fecha de fin vencida.", "Modelo cierre + Cálculo dinámico"),
                ],
                explicacion_simple="Un listado de todos los proyectos a cargo de este coordinador, ordenados de mayor a "
                "menor presupuesto, con sus indicadores clave en una sola fila. Por ahora es una tabla de solo lectura: "
                "para ver el detalle completo de un proyecto, usa el selector \"Proyecto\" de la cabecera. "
                "<b>⚠ En construcción:</b> las columnas de Ejec. técnica, CPI y SPI dependen de Ejecución Técnica.")),
            'ejecucion_tecnica_financiera_coordinador': ("Ejecución Técnica vs Financiera (Coordinador)", self._tabla_glosario(
                "Ejecución Técnica vs Financiera del Coordinador", DASH_TEAL, [
                    ("Ejecución técnica", "Promedio simple del % técnico de sus proyectos", "Qué tan avanzada está, en promedio, la obra de los proyectos a cargo.", "Ejecución / Entregables"),
                    ("Ejecución financiera", "Promedio simple del % financiero de sus proyectos", "Qué tanto se ha pagado, en promedio, frente al presupuesto de cada proyecto.", "SIESA - pagos"),
                    ("Brecha técnica–financiera", "% Financiera promedio − % Técnica promedio", "Diferencia entre ambos promedios; positiva si se paga más rápido de lo que avanza la obra.", "Cálculo dinámico"),
                ],
                explicacion_simple="Igual que el panel equivalente de \"Proyecto\", pero promediando (con el mismo "
                "método simple que ya usa \"Ejec. Técnica Promedio\") todos los proyectos de <b>este coordinador</b>. "
                "<b>⚠ En construcción:</b> el dato de Ejecución Técnica todavía no está completo en toda la aplicación "
                "-- la brecha hereda esa misma incertidumbre.")),
        }

    def _compute_html_content(self):
        topics = self._get_glosario_topics()
        for record in self:
            if record.topic and record.topic in topics:
                _titulo, seccion = topics[record.topic]
                record.html_content = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 8px; color: #334155;">{seccion}</div>"""
            else:
                secciones = "".join(seccion for _titulo, seccion in topics.values())
                record.html_content = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 8px; color: #334155;">
    <div style="text-align: center; margin-bottom: 24px;">
        <h2 style="color: #0f172a; font-weight: 700; font-size: 20px; margin-bottom: 8px;">Guía Técnica de Métricas y Metodología de Fórmulas</h2>
        <p style="color: #64748b; font-size: 14px; margin: 0;">Este documento detalla la procedencia de los datos en el ERP y la metodología matemática utilizada para el cálculo de los indicadores del Dashboard.</p>
    </div>
    {secciones}
</div>"""




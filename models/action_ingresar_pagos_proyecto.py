#!/usr/bin/python

import string
import time

from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
import json
from odoo.exceptions import UserError
import base64
import pdfkit
import threading
from odoo.tools.misc import html_escape
from odoo.tools import formatLang
from io import BytesIO
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import logging

_logger = logging.getLogger(__name__)



class PagosdelProyecto(models.Model):
    _name = "action_ingresar_pagos_proyecto"
    _order = 'create_date desc'
    _rec_name = "informacion_base_id"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', tracking=True)
    new_name = fields.Char(string='Nombre', tracking=True)

    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado', ondelete='cascade')
    is_editable_pp = fields.Boolean(string='Es editable', default=False, related="informacion_base_id.is_editable")

    def write(self, vals):
        """Sobrescribe el método 'write' para restringir edición y generar trazabilidad."""
        if self._es_consulta_coordinacion_financiera():
            return True
        return super().write(vals)

    def _es_consulta_coordinacion_financiera(self):
        return self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')

    def _marcar_apertura_si_permitido(self):
        # Evita escrituras automáticas cuando el usuario solo tiene rol de consulta.
        if not self._es_consulta_coordinacion_financiera():
            self.write({'write_date': fields.Datetime.now()})
            
            
    @api.onchange('is_editable_pp')
    def _onchange_is_editable_pp(self):
        # Este método se llama cuando cambia el valor de is_editable_pp
        # Puedes agregar lógica adicional aquí si es necesario
        pass


    fecha_recibido = fields.Date(string='Fecha recibido')
    recibo_caja = fields.Char(string='Recibo  de caja')
    deduciones = fields.Char(string='Deduciones')
    valor_neto_recibido = fields.Monetary(string='Valor recibido neto')

    @api.depends('new_name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.new_name or record.name

    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)

    gestion_seleccionada = fields.Boolean(string='Gestión Seleccionada',
                                          compute='_compute_gestion_seleccionada', store=True)

    @api.depends('informacion_base_id')
    def _compute_gestion_seleccionada(self):
        for record in self:
            record.gestion_seleccionada = bool(record.informacion_base_id)

    # Campos adicionales
    fecha_inicio = fields.Date(string='Fecha de inicio', readonly=True, compute='_compute_total_proyecto1',
                               tracking=True)

    @api.depends('informacion_base_id.fecha_acta_inicio')
    def _compute_total_proyecto1(self):
        for record in self:
            record.fecha_inicio = record.informacion_base_id.fecha_acta_inicio

    num_factura_cobro = fields.Char(string='Numero de factura', readonly=True, tracking=True)
    fecha_finalizacion = fields.Date(string='Fecha de finalización', readonly=True, compute='_compute_total_proyecto2',
                                     tracking=True)

    @api.depends('informacion_base_id.fecha_acta_fin')
    def _compute_total_proyecto2(self):
        for record in self:
            record.fecha_finalizacion = record.informacion_base_id.fecha_acta_fin

    cantidad_pagos = fields.Integer(string='Cantidad de cobros', readonly=True, tracking=True)

    porcentaje_pagos = fields.Integer(string='% de cobros', readonly=True, compute='_compute_porcentaje_pagos',
                                      tracking=True)

    @api.depends('valor_pagado', 'informacion_base_id.valor_total_neto')
    def _compute_porcentaje_pagos(self):
        for record in self:
            if record.informacion_base_id.valor_total_neto != 0:
                record.porcentaje_pagos = round(
                    (record.valor_pagado / record.informacion_base_id.valor_total_neto) * 100, 2)
            else:
                record.porcentaje_pagos = 0.0

            # Calcular pendiente_porcentaje_pago
            record.pendiente_porcentaje_pago = 100 - record.porcentaje_pagos

    porcentaje_como_cadena = str(porcentaje_pagos) + '%'

    def _compute_actualizar_valor_pagado(self):
        for record in self:
            record.valor_pagado = sum(pago.valor for pago in record.tabla_pagos)
            record.valor_cobrado = record.valor_pagado
            record.valor_por_facturar = record.total_proyecto - record.valor_pagado  # Nueva fórmula

    valor_pagado = fields.Monetary(string='Valor cobros acumulado', currency_field='currency_id',
                                   compute='_compute_actualizar_valor_pagado', tracking=True)

    pendiente_porcentaje_pago = fields.Float(string='Pendiente % de cobro', readonly=True, tracking=True)

    porcentaje_como_cadena = str(pendiente_porcentaje_pago) + '%'

    informacion_base_id = fields.Many2one('informacion_base_agregar', string='Información Base', ondelete='cascade')

    new_mcb_valor_total2_final = fields.Float(string='new_mcb_valor_total2_final', readonly="1",
                                           related='informacion_base_id.new_mcb_valor_total2_final')

    ejecucion2_id = fields.Many2one('ejecucion', string='ejecucion', inverse_name="id_proyecto", ondelete='cascade')

    total_proyecto = fields.Monetary(string='Total del proyecto', currency_field='currency_id', readonly=True,
                                     compute='_compute_total_proyecto', tracking=True)

    @api.depends('informacion_base_id.valor_sin_contrapartida')
    def _compute_total_proyecto(self):
        for record in self:
            record.total_proyecto = record.informacion_base_id.valor_sin_contrapartida

    currency_id = fields.Many2one('res.currency', string='Moneda', default=lambda self: self.env.ref('base.COP'))
    porcentaje_avance = fields.Integer(string='% de avance', readonly=True, compute='_compute_porcentaje_avance')
    por_avance = fields.Integer(string='% de avance', tracking=True, readonly=True, )

    @api.depends('valor_pagado', 'informacion_base_id.valor_total_neto')
    def _compute_porcentaje_avance(self):
        for record in self:
            if record.informacion_base_id.valor_total_neto != 0:
                record.porcentaje_avance = round(
                    (record.valor_pagado / record.informacion_base_id.valor_total_neto) * 100, 2)
            else:
                record.porcentaje_avance = 0.0

            # Calcular pendiente_porcentaje_avance
            record.pendiente_porcentaje_avance = 100 - record.porcentaje_avance

    porcentaje_como_cadena = str(porcentaje_avance) + '%'

    pendiente_porcentaje_avance = fields.Integer(string='Pendiente % de avance', readonly=True, tracking=True)
    porcentaje_como_cadena = str(pendiente_porcentaje_avance) + '%'

    valor_cobrado = fields.Float(string='Valor cobrado', readonly=True, tracking=True)
    valor_por_facturar = fields.Monetary(string='Valor pendiente por facturar', currency_field='currency_id',
                                         readonly=True, tracking=True)

    # Campos Editables
    valor_pagado_editable = fields.Float(string='Valor de cobro', tracking=True)
    num_factura_cobro_editable = fields.Char(string='Numero de factura', tracking=True)
    numero_pagos_editable = fields.Integer(string='Numero de Cobro', tracking=True)
    fecha_programada_pago_editable = fields.Date(string='Fecha programada de Cobro', tracking=True)
    fecha_efectua_pago_editable = fields.Date(string='Fecha Efectuada de Cobro', tracking=True)
    pendiente_porcentaje_avance_editable = fields.Integer(string='% de cobro', tracking=True)
    porcentaje_avance_editable = fields.Integer(string='% de avance', invisible=True)
    por_avance_editable = fields.Integer(string='% de avance', tracking=True)
    descripción_entregable_editable = fields.Char(string='Descripción entregable', tracking=True)
    porcentaje_de_avance_en_ejecucion_editable = fields.Integer(string='Porcentaje de avance en ejecución',
                                                                tracking=True)

    @api.onchange('valor_pagado_editable', 'valor_por_facturar')
    def _check_valor_pago_pagado(self):
        if self.valor_pagado_editable > self.valor_por_facturar:
            raise ValidationError("El pago supera el importe pendiente. Por favor, "
                                  "revise el monto e ingrese un valor inferior o igual al faltante por cobrar.")

    show_editable_fields = fields.Boolean(string="Mostrar Campos Editables")

    # Tabla de pagos
    tabla_pagos = fields.One2many('pago', 'proyecto_id', string='Tabla de Pagos')
    # Campo calculado para la cantidad de líneas en tabla_pagos
    cantidad_lineas_tabla_pagos = fields.Integer(string='Cantidad de cobros',
                                                 compute='_compute_cantidad_lineas')

    @api.depends('tabla_pagos')
    def _compute_cantidad_lineas(self):
        for record in self:
            record.cantidad_lineas_tabla_pagos = len(record.tabla_pagos)

    @api.depends('acomulado_cobro_sumado', 'informacion_base_id.valor_total_neto')
    def _compute_porcentaje_cobros(self):
        for record in self:
            if record.informacion_base_id.valor_total_neto != 0:
                record.porcentaje_pagos = round(
                    (record.acomulado_cobro_sumado / record.informacion_base_id.valor_total_neto) * 100)
            else:
                record.porcentaje_pagos = 0.0

            # Calcular pendiente_porcentaje_pago
            record.pendiente_porcentaje_cobros = 100 - record.porcentaje_pagos

    pendiente_porcentaje_cobros = fields.Float(compute='_compute_porcentaje_cobros', string="Pendiente % de cobro")

    acomulado_cobro = fields.Monetary(related='tabla_pagos.acomulado_cobro', string="Valor facturado relatado")
    valor_recibido = fields.Monetary(related='tabla_pagos.valor_recibido')
    acomulado_cobro_sumado = fields.Monetary(compute='_compute_acomulado_cobro_2', string="Valor cobrado")
    acomulado_valor_recibido = fields.Monetary(compute='_compute_acomulado_valor_recibido', string="Valor recibido")

    ###Validación
    @api.onchange('acomulado_cobro_sumado', 'total_proyecto')
    def _check_valor_pago_acumulado(self):
        if self.acomulado_cobro_sumado > self.total_proyecto:
            raise ValidationError("El pago supera el importe pendiente. Por favor, "
                                  "revise el monto e ingrese un valor inferior o igual al faltante por cobrar.")

    @api.depends('acomulado_cobro')
    def _compute_acomulado_cobro_2(self):
        for suma in self:
            suma.acomulado_cobro_sumado = sum(suma.tabla_pagos.mapped('acomulado_cobro'))

    @api.depends('valor_recibido')
    def _compute_acomulado_valor_recibido(self):
        for suma in self:
            suma.acomulado_valor_recibido = sum(suma.tabla_pagos.mapped('valor_recibido'))

    faltante_por_cobrar = fields.Monetary(compute='_compute_saldo_cobro_resta', string='Faltante por recibir')

    @api.depends('total_proyecto', 'acomulado_cobro_sumado')
    def _compute_saldo_cobro_resta(self):
        for record in self:
            record.faltante_por_cobrar = record.total_proyecto - record.acomulado_cobro_sumado

    def agregar_nuevo(self):
        self.cantidad_pagos = self.numero_pagos_editable
        self.por_avance = self.por_avance_editable

        # Verifica si al menos uno de los campos editables está lleno
        if not self.show_editable_fields:  # Verifica si los campos editables están visibles
            # Muestra los campos editables

            self.write({'show_editable_fields': True})
            # Limpiar los campos editables
            self.valor_pagado_editable = 0.0
            self.num_factura_cobro_editable = ''
            self.numero_pagos_editable += 1
            self.fecha_programada_pago_editable = False
            self.fecha_efectua_pago_editable = False
            self.pendiente_porcentaje_avance_editable = 0
            self.porcentaje_avance_editable = 0
            self.por_avance_editable = 0
            self.descripción_entregable_editable = ''
            self.porcentaje_de_avance_en_ejecucion_editable = 0
        else:
            nuevo_pago = self.env['pago'].create({
                'numpago': self.numero_pagos_editable,
                'valor': self.valor_pagado_editable,
                'descripcion': self.descripción_entregable_editable,
                'num_factura_cobro': self.num_factura_cobro_editable,
                'fecha_programada_pago': self.fecha_programada_pago_editable,
                'fecha_efectua_pago': self.fecha_efectua_pago_editable,
                'proyecto_id': self.id,
                'porcentaje_pago': self.porcentaje_pagos,
                'porcentaje_avance': self.porcentaje_avance,
                'por_avance': self.por_avance_editable,
                'porcentaje_de_avance_en_ejecucion': self.porcentaje_de_avance_en_ejecucion_editable,
            })
            self.write({'show_editable_fields': not self.show_editable_fields, 'boton_agregar': not self.boton_agregar})

        return {

        }

    boton_agregar = fields.Boolean(default=True)
    boton_texto = fields.Char(compute='_compute_boton_texto')

    # Método para cambiar el nombre del botón según el valor del campo booleano

    @api.depends('boton_agregar')
    def _compute_boton_texto(self):
        for record in self:
            if record.boton_agregar:
                record.boton_texto = 'Agregar pago'
            else:
                record.boton_texto = 'Añadir'

    def datos(self):
        return {
            'name': 'Cobros',
            'view_mode': 'tree,form,pivot',
            'res_model': 'pago',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('proyecto_id', '=', )],  # Filtrar por el ID del registro actual de Rubros
        }


    hiden = fields.Char(string='hiden')
    avance_real2 = fields.Float(compute='_compute_avance_real2', string='% de avance total')

    @api.depends('tabla_pagos.porcentaje_avance_real2')
    def _compute_avance_real2(self):
        for proyecto in self:
            proyecto.avance_real2 = sum(proyecto.tabla_pagos.mapped('porcentaje_avance_real2'))


class Pago(models.Model):
    _name = 'pago'  # Define el nombre del modelo en la base de datos
    _inherit = ['mail.thread', 'mail.activity.mixin']  # Hereda funcionalidades de seguimiento de Odoo
    avance_real2 = fields.Float()  # Campo para almacenar un número decimal
    hiden = fields.Char(string='hiden')  # Campo para almacenar texto corto con etiqueta personalizada
    pago_ids_2 = fields.One2many('vistas_director', 'pago_ids', string='Tablero control')

    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado',
                                           related="proyecto_id.proyecto_relacionado", ondelete='cascade')

    informacion_base_id = fields.Many2one('informacion_base_agregar', string="Información Base ID", ondelete='cascade')
    selecciones_mes_anio_lista2 = fields.Selection(selection='_get_selecciones_mes_anio2', string="Seleccionar Mes y Año")

    #Secuencia
    sequence = fields.Integer(string='Secuencia', copy=False)

    @api.model
    def create(self, vals):
        # Obtener el ID del proyecto
        proyecto_id = vals.get('proyecto_id')
        if not proyecto_id:
            raise ValueError("Debe especificar un proyecto para el cobro.")

        # Reparar secuencias vacías antes de asignar una nueva
        self._reparar_secuencias(proyecto_id)

        # Generar el nuevo consecutivo
        last_sequence = self.search(
            [('proyecto_id', '=', proyecto_id)], order='sequence desc', limit=1
        ).sequence
        if last_sequence:
            new_sequence = last_sequence + 1
        else:
            new_sequence = 1  # Iniciar desde 1 si no hay registros previos para este proyecto

        vals['sequence'] = new_sequence
        return super(Pago, self).create(vals)

    def _reparar_secuencias(self, proyecto_id):
        """Repara las secuencias en blanco para un proyecto dado."""
        registros = self.search(
            [('proyecto_id', '=', proyecto_id), ('sequence', '=', False)], order='id asc'
        )
        if registros:
            # Obtener el último `sequence` válido
            last_sequence = self.search(
                [('proyecto_id', '=', proyecto_id), ('sequence', '!=', False)],
                order='sequence desc', limit=1
            ).sequence
            next_sequence = last_sequence + 1 if last_sequence else 1

            # Asignar secuencias a los registros en blanco
            for registro in registros:
                registro.sequence = next_sequence
                next_sequence += 1



    is_editable_pp = fields.Boolean(string='Es editable', default=False, related="proyecto_id.is_editable_pp")

    @api.onchange('is_editable_pp')
    def _onchange_is_editable_pp(self):
        # Este método se llama cuando cambia el valor de is_editable_pp
        # Puedes agregar lógica adicional aquí si es necesario
        pass


    pie_chart_data3 = fields.Char(string="Datos del Gráfico de Torta", compute="_compute_pie_chart_data3")
    seleccion_anio2 = fields.Selection(selection='_get_selecciones_anio2', string="Seleccionar Año")


    def get_acomulado_1_and_fecha_de_pago2(self):
        contrato_records = self.search([('informacion_base_id', '!=', False)])
        for record in contrato_records:
            acomulado_cobro = record.acomulado_cobro
            fecha_recibido = record.fecha_recibido
            informacion_base = record.informacion_base_id
            print(f"Información Base: {informacion_base.name}, Registro ID {record.id}: acomulado_cobro = {acomulado_cobro}, fecha_recibido = {fecha_recibido}")
        return True

    def _get_selecciones_mes_anio2(self):
        selecciones_mes_anio_lista2 = self.sumar_acomulado_1_por_mes_y_anio2()
        print(f"Selecciones Mes y Año: {selecciones_mes_anio_lista2}")
        return selecciones_mes_anio_lista2

    @api.model
    def _get_selecciones_anio2(self):
        pagos_con_fecha_recibido = self.search([('fecha_recibido', '!=', False)])
        anios = list(set([pago.fecha_recibido.year for pago in pagos_con_fecha_recibido]))
        selecciones_anio = [(str(anio), str(anio)) for anio in sorted(anios)]
        return selecciones_anio

    def sumar_acomulado_1_por_mes_y_anio2(self):
        pagos_con_fecha_recibido = self.search([('fecha_recibido', '!=', False)])
        acumulado_cobro_por_mes_anio = {}
        selecciones_mes_anio = []

        for pago in pagos_con_fecha_recibido:
            if pago.fecha_recibido:
                mes_anio = pago.fecha_recibido.strftime('%m-%Y')
                if mes_anio in acumulado_cobro_por_mes_anio:
                    acumulado_cobro_por_mes_anio[mes_anio] += pago.acomulado_cobro
                else:
                    acumulado_cobro_por_mes_anio[mes_anio] = pago.acomulado_cobro

        for mes_anio, suma_acumulado in acumulado_cobro_por_mes_anio.items():
            selecciones_mes_anio.append((mes_anio, f"{mes_anio} (Total: {suma_acumulado})"))

        return selecciones_mes_anio

    @api.onchange('selecciones_mes_anio_lista2', 'seleccion_anio2')
    def _compute_pie_chart_data3(self):
        month_names = {'01': 'Enero', '02': 'Febrero', '03': 'Marzo', '04': 'Abril', '05': 'Mayo', '06': 'Junio',
                       '07': 'Julio', '08': 'Agosto', '09': 'Septiembre', '10': 'Octubre', '11': 'Noviembre',
                       '12': 'Diciembre'}
        for record in self:
            pie_chart_data3 = {}
            try:
                if record.seleccion_anio2:
                    pagos_con_fecha_recibido = self.search([
                        ('fecha_recibido', '!=', False),
                        ('fecha_recibido', '>=', f'{record.seleccion_anio2}-01-01'),
                        ('fecha_recibido', '<=', f'{record.seleccion_anio2}-12-31')
                    ])
                    acumulado_cobro_por_mes_anio = {}
                    for pago in pagos_con_fecha_recibido:
                        mes_anio = pago.fecha_recibido.strftime('%m-%Y')
                        if mes_anio in acumulado_cobro_por_mes_anio:
                            acumulado_cobro_por_mes_anio[mes_anio] += pago.acomulado_cobro
                        else:
                            acumulado_cobro_por_mes_anio[mes_anio] = pago.acomulado_cobro

                    meses = [f"{month_names[mes.split('-')[0]]} {mes.split('-')[1]}" for mes in acumulado_cobro_por_mes_anio.keys()]
                    valores = list(acumulado_cobro_por_mes_anio.values())

                    pie_chart_data3 = self._get_pie_chart_data3(meses, valores)
            except Exception as e:
                print(f"Error in _compute_pie_chart_data3: {e}")

            record.pie_chart_data3 = json.dumps(pie_chart_data3)

    def _get_pie_chart_data3(self, meses, valores):
        try:
            colores = ['#4bc0c0', '#9966ff', '#ffa64d', '#ff6384', '#36a2eb', '#ffcc99',
                       '#ff6666', '#339966', '#cc99ff', '#ff9933', '#6699ff', '#ffcc00']
            hover_colores = ['#5cdcdc', '#b38cff', '#ffc266', '#ff809f', '#65b3ff', '#ffdab3',
                             '#ff9999', '#66cc99', '#e6ccff', '#ffb366', '#99c2ff', '#ffe066']

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
        except Exception as e:
            print(f"Error in _get_pie_chart_data3: {e}")
            return {}


    ###################################################################################################
    total_proyecto2 = fields.Monetary(related='proyecto_id.total_proyecto')
    valor_recibido = fields.Monetary(string='Valor recibido')
    fecha_recibido = fields.Date(string='Fecha recibido')
    recibo_caja = fields.Char(string='Recibo  de caja')
    deduciones = fields.Char(string='Deducciones')
    valor_neto_recibido = fields.Monetary(string='Valor recibido neto')

    porcentaje_avance_real2 = fields.Float(string='% cobrado',
                                           compute='_compute_porcentaje_avance_real2', store=False)

    @api.depends('acomulado_cobro', 'proyecto_id.total_proyecto')
    def _compute_porcentaje_avance_real2(self):
        for record in self:
            if record.total_proyecto2 != 0:
                record.porcentaje_avance_real2 = (record.acomulado_cobro / record.proyecto_id.total_proyecto) * 100
            else:
                record.porcentaje_avance_real2 = 0

    numpago = fields.Integer(string='Núm. Cobro', readonly=False, tracking=True)
    valor = fields.Float(string='Cobro', tracking=True)

    porcentaje_pago = fields.Integer(string='% de cobro', readonly=False, compute='_compute_porcentaje_pago', store=False, tracking=True)
    porcentaje_como_cadena = str(porcentaje_pago) + '%'
    porcentaje_avance = fields.Integer(string='% de avance', readonly=False, compute='_compute_porcentaje_avance',
                                       store=False, invisible=True)
    por_avance = fields.Integer(string='% de avance', tracking=True)

    porcentaje_como_cadena = str(porcentaje_avance) + '%'


    currency_id = fields.Many2one('res.currency', string='Moneda')

    acomulado_cobro = fields.Monetary(string="Valor cobrado", compute='_compute_acumulado_cobro')

    @api.depends('valor', 'fecha_recibido')
    def _compute_acumulado_cobro(self):
        for contrato in self:
            if contrato.fecha_recibido:
                contrato.acomulado_cobro = contrato.valor
            else:
                contrato.acomulado_cobro = 0.0

    @api.depends('valor', 'proyecto_id.informacion_base_id.valor_total_neto')
    def _compute_porcentaje_pago(self):
        for pago in self:
            if pago.proyecto_id and pago.proyecto_id.informacion_base_id.valor_total_neto != 0:
                pago.porcentaje_pago = round((pago.valor / pago.proyecto_id.informacion_base_id.valor_total_neto) * 100,
                                             2)
            else:
                pago.porcentaje_pago = 0.0

    @api.depends('valor', 'proyecto_id.informacion_base_id.valor_total_neto')
    def _compute_porcentaje_avance(self):
        for pago in self:
            if pago.proyecto_id and pago.proyecto_id.informacion_base_id.valor_total_neto != 0:
                pago.porcentaje_avance = round(
                    (pago.valor / pago.proyecto_id.informacion_base_id.valor_total_neto) * 100, 2)
            else:
                pago.porcentaje_avance = 0.0

    descripcion = fields.Char(string='Descripción', tracking=True)
    num_factura_cobro = fields.Char(string='Núm. Factura/Cobro', readonly=False, tracking=True)
    fecha_programada_pago = fields.Date(string='Fecha programada de cobro', tracking=True)
    fecha_efectua_pago = fields.Date(string='Fecha de cobrado', tracking=True)
    porcentaje_de_avance_en_ejecucion = fields.Integer(string='Porcentaje de avance en ejecución', tracking=True)
    proyecto_id = fields.Many2one('action_ingresar_pagos_proyecto', string='Proyecto', ondelete='cascade')


##################################################################################################################



class ReportesContratista(models.Model):
    _name = 'reportes'
    _description = 'Reportes de contratistas'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Campos del modelo


    name = fields.Many2one(string='Contratista', related='contratista_reportes', ondelete='cascade')
    representante_legal = fields.Char(string='Representante legal', related="contratista_reportes.r_legal")
    estado_del_contrato = fields.Selection(string='Estado del contrato', related="contratista_reportes.estado_contratacion")
    id_contrato_reportes = fields.Char(string='ID contrato', tracking=True, related="contratista_reportes.id_del_contrato")
    correo_elctronico = fields.Char(string='Correo electrónico', related="contratista_reportes.correo")
    telefono_contratista = fields.Char(string='Teléfono contratista', related="contratista_reportes.telefono_contratista")
    duracion_contrato = fields.Char(string='Duración del contrato (días)', related="contratista_reportes.duracion")
    valor_del_contrato_otrosi = fields.Monetary(string='Valor del contrato con otrosí', related="contratista_reportes.valor_del_otrosi")
    valor_del_contrato = fields.Monetary(string='Valor del contrato', related="contratista_reportes.valor_total_contrato")
    valor_total_contrato_reportes = fields.Monetary(string='Valor del contrato + otrosí', related='contratista_reportes.total')
    tipo_de_persona = fields.Selection(string='Tipo de persona', related="contratista_reportes.select_persona")
    fecha_iniciacion = fields.Date('Fecha de inicio', related="contratista_reportes.fecha_iniciacion")
    fecha_terminacion = fields.Date('Fecha de fin', related="contratista_reportes.fecha_terminacion")
    rpc = fields.Char(string='RPC', related="contratista_reportes.compromiso_presupuestal") # RPC se mantiene en mayúsculas por ser una sigla
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado', ondelete='cascade', store=True, related="contratista_reportes.proyecto_relacionado")
    documento_identidad = fields.Char('Documento de identificación', tracking=True, related="contratista_reportes.documento_identidad")
    nit = fields.Char(string="NIT", tracking=True, related="contratista_reportes.nit") # NIT se mantiene en mayúsculas por ser una sigla

    

    fecha_de_pago3 = fields.Date(string='Fecha de pago', related="contratista_reportes.fecha_de_pago")
    tabla_de_forma_de_pago = fields.Many2one('tabla_forma_de_pago', string="Relación tabla", domain="[('contrataciones_fuv_tabla_id', '=', contratista1)]", ondelete='cascade')
    fecha_iniciacion_texto = fields.Char(string="Fecha de Iniciación en Texto", compute="_compute_fecha_iniciacion_texto")
    valor_pago = fields.Float(string='VALOR DE PAGO', related="contratista_reportes.cantidad_de_dinero2")
    porcentaje_pago22 = fields.Float(compute="_compute_porcentaje_pago22")
    acumulado_por_contratista23 = fields.Monetary(related="contratista_reportes.acumulado_por_contratista_contrato")
    valor_pago_2suma22 = fields.Float(related="contratista_reportes.valor_pago_2suma")
    currency_id = fields.Many2one('res.currency', string='Moneda')
    #Variables de busqueda
    contratista_reportes = fields.Many2one(comodel_name='contrataciones_fuv', string="Contratista",
                                           tracking=True, store=True, ondelete='cascade')
    nit2 = fields.Char(string="Nit", tracking=True, related="contratista_reportes.nit2", store=True)








    nuemor_de_cuotas_reportes = fields.Integer(related="contratista_reportes.nuemor_de_cuotas")



    @api.model
    def limpiar_documento_identidad(self, documento):
        """
        Limpia un documento de identificación eliminando caracteres especiales.
        :param documento: El documento a limpiar (str).
        :return: Documento limpio (str).
        """
        if not documento:
            return ""
        return ''.join(char for char in documento if char.isalnum())

    @api.onchange('documento_identidad')
    def _onchange_documento_identidad(self):
        """
        Evento onchange para limpiar automáticamente el documento de identificación.
        """
        if self.documento_identidad:
            self.documento_identidad = self.limpiar_documento_identidad(self.documento_identidad)


    chart_data = fields.Char(string="Datos del Gráfico", related="tabla_de_forma_de_pago.chart_data")

    mostrar_proyectos = fields.Text()


    proyectos_relacionados_display = fields.Char(string="Proyectos Relacionados",
                                                 compute="_compute_proyectos_relacionados")


    proyectos_relacionados_contratista = fields.Char(string="Proyectos Relacionados",
                                                 compute="_compute_proyecto")

    @api.depends('contratista_reportes')
    def _compute_proyecto(self):
        for record in self:
            if record.contratista_reportes:
                # Obtener el ID del contratista
                contratista_id = record.contratista_reportes.id

                # Obtener los proyectos relacionados con el contratista
                proyectos_relacionados = self.env['contratacion'].search([
                    ('id', 'in', self.env['contrataciones_fuv'].search([
                        ('id', '=', contratista_id)
                    ]).mapped('proyecto_relacionado').ids)
                ])

                # Crear una cadena con los nombres de los proyectos, asegurando que los nombres sean cadenas
                nombres_proyectos = ", ".join(
                    [str(proyecto.name) for proyecto in proyectos_relacionados if proyecto.name])
                record.proyectos_relacionados_contratista = nombres_proyectos
            else:
                record.proyectos_relacionados_contratista = ''


    @api.depends('contratista_reportes')
    def _compute_proyectos_relacionados(self):
        for record in self:
            if record.contratista_reportes:
                proyectos = self.env['contrataciones_fuv'].search([
                    ('nit2', '=', record.contratista_reportes.nit2),
                    ('proyecto_relacionado', '!=', False)
                ])
                nombres_proyectos = []
                for proyecto in proyectos:
                    # Asegurarse de que el nombre del proyecto sea una cadena
                    nombre = proyecto.proyecto_relacionado.name
                    if nombre:
                        nombres_proyectos.append(str(nombre))
                record.proyectos_relacionados_display = ', '.join(nombres_proyectos)
            else:
                record.proyectos_relacionados_display = ''


    @api.model
    def create(self, vals):
        result = super(ReportesContratista, self).create(vals)
        self._on_view_accessed()
        return result

    def write(self, vals):
        result = super(ReportesContratista, self).write(vals)
        self._on_view_accessed()
        return result

    def _on_view_accessed(self):
        _logger.info("Se ha accedido o actualizado un registro en la vista reportes.kanban")

    def action_download_pdf(self):
        for record in self:
            try:
                # Obtener el HTML
                html = record.mostrar_proyectos

                # Configuración para pdfkit con UTF-8
                config = pdfkit.configuration(
                    wkhtmltopdf='/usr/local/bin/wkhtmltopdf')  # Ajusta la ruta si es necesario
                options = {
                    'encoding': 'UTF-8',
                }

                # Convertir HTML a PDF
                pdf_content = pdfkit.from_string(html, False, configuration=config, options=options)

                # Determinar el nombre del archivo
                if record.nit2:
                    nombre_archivo = f'Nit_{record.nit2}.pdf'
                elif record.documento_identidad:
                    nombre_archivo = f'DI_{record.documento_identidad}.pdf'
                else:
                    nombre_archivo = 'Reporte_Desconocido.pdf'

                # Guardar el PDF en el archivo de descarga
                attachment = self.env['ir.attachment']._drive_create_temp_download({
                    'name': nombre_archivo,
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_content),
                    'store_fname': nombre_archivo,
                })

                return {
                    'type': 'ir.actions.act_url',
                    'url': '/web/content/%s' % (attachment.id),
                    'target': 'new',
                }
            except Exception as e:
                _logger.error("Error al generar el PDF: %s", str(e))
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Error',
                    'view_mode': 'form',
                    'res_model': 'res.users',
                    'view_id': False,
                    'target': 'new',
                    'context': {'message': 'Error al generar el PDF: %s' % str(e)},
                }


    def actualizar_obtener_todos_los_proyectos_por_documento_identidad_o_nit2(self):
        # Devolver la acción para abrir el formulario del registro actual
        self.obtener_todos_los_proyectos_por_documento_identidad_o_nit2()
        action = {
            'name': 'Reportes',
            'type': 'ir.actions.act_window',
            'res_model': 'reportes',
            'view_mode': 'form',
            'res_id': self.id,  # ID del registro actual
            'target': 'current',
        }

        return action


    def obtener_todos_los_proyectos_por_documento_identidad_o_nit2(self):
        # Inicializa una cadena para almacenar el HTML de las tablas
        # --- Estilos Modernos y Estructura de Tarjetas (Card-based) ---
        resultado_html = """
        <html>
        <head>
            <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
            <style>
                body {
                    font-family: 'Roboto', sans-serif;
                    background-color: #f8f9fa; /* Fondo muy claro */
                    padding: 15px;
                }
                .contract-card {
                    border: 1px solid #e0e0e0;
                    border-radius: 8px;
                    margin-bottom: 20px;
                    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05); /* Sombra suave */
                    background-color: #ffffff;
                    overflow: hidden;
                }
                .card-header {
                    background-color: #007bff; /* Azul primario */
                    color: white;
                    padding: 12px 15px;
                    font-size: 1.1em;
                    font-weight: 500;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                }
                .link {
                    text-decoration: none;
                    color: #ffc107; /* Amarillo/Dorado para el enlace */
                    font-weight: 700;
                }
                
                /* Reemplazo de la estructura de tabla original */
                table {
                    width: 100%;
                    border-collapse: separate; /* Usamos separate para tener control sobre bordes */
                    border-spacing: 0;
                }
                /* Estilo para las filas de detalles */
                td {
                    padding: 8px 15px;
                    border-bottom: 1px solid #eeeeee;
                }
                /* Estilo para los encabezados de sección dentro de la tarjeta */
                .header {
                    background-color: #f0f7ff; 
                    font-weight: 700;
                    color: #333;
                    padding: 10px 15px;
                    border-bottom: 2px solid #007bff;
                    text-transform: uppercase;
                    font-size: 0.95em;
                }
                /* Estilo para la fila de detalles par */
                .row-even {
                    background-color: #f8f9fa;
                }
                /* Estilo para la fila de detalles impar */
                .row-odd {
                    background-color: #ffffff;
                }
            </style>
        </head>
        <body>
        """

        # Busca la moneda COP
        currency_cop = self.env['res.currency'].search([('name', '=', 'COP')], limit=1)

        if not currency_cop:
            raise ValueError("La moneda COP no está configurada en el sistema")

        # Itera sobre cada registro en el modelo 'ReportesContratista'
        for record in self:
            documento_identidad_actual = record.documento_identidad
            nit2_actual = record.nit2
            # --- Lógica de Filtro por Documento de Identidad ---
            if documento_identidad_actual:
                # Filtra por documento_identidad
                proyectos = self.env['contratacion'].search([])
                for proyecto in proyectos:
                    if isinstance(proyecto.name, str):
                        contratistas = self.env['contrataciones_fuv'].search([
                            ('proyecto_relacionado', '=', proyecto.id),
                            ('documento_identidad', '=', documento_identidad_actual)
                        ])

                        if contratistas:
                            for contratista in contratistas:
                                nombre = contratista.name if isinstance(contratista.name, str) else 'No se asignó'
                                # Generar la URL del contrato
                                url_contrato = f"/web#id={contratista.id}&model=contrataciones_fuv&view_type=form"

                                # Crear el enlace con el texto "ir al contrato"
                                nombre_enlace = f'<a href="{url_contrato}" target="_blank" class="link">Ir al Contrato</a>'

                                new_name = contratista.new_name if isinstance(contratista.new_name,
                                                                            str) else 'No se asignó'
                                nuemor_de_cuotas = contratista.nuemor_de_cuotas if contratista.nuemor_de_cuotas else 'No se asignó'
                                valor_total_contrato = contratista.valor_total_contrato if contratista.valor_total_contrato else 0.0
                                valor_total_contrato_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    valor_total_contrato, {'display_currency': currency_cop})

                                valor_del_otrosi = contratista.valor_del_otrosi if contratista.valor_del_otrosi else 0.0
                                valor_del_otrosi_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    valor_del_otrosi, {'display_currency': currency_cop})

                                total = contratista.total if contratista.total else 0.0
                                total_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    total, {'display_currency': currency_cop})

                                acumulado_por_contratista = contratista.acumulado_por_contratista if contratista.acumulado_por_contratista else 0.0
                                valor_pago_2suma_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    acumulado_por_contratista, {'display_currency': currency_cop})

                                # Controlar visibilidad de campos (Lógica original)
                                nit2_visible = '' if nit2_actual else 'display: none;'
                                documento_identidad_visible = '' if documento_identidad_actual else 'display: none;'
                                
                                # --- Estructura HTML mejorada con la lógica de las tarjetas ---
                                resultado_html += f"""
                                <div class="contract-card">
                                    <div class="card-header">
                                        <div>{nombre}</div>
                                        <div>{nombre_enlace}</div>
                                    </div>
                                    <table>
                                        <tbody>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Proyecto</strong></td>
                                                <td style="width: 60%">{proyecto.name}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>ID del Contrato</strong></td>
                                                <td style="width: 60%">{contratista.id_del_contrato if contratista.id_del_contrato else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-odd" style="{nit2_visible}">
                                                <td style="width: 40%"><strong>NIT2</strong></td>
                                                <td style="width: 60%">{contratista.nit2 if contratista else 'N/A'}</td>
                                            </tr>
                                            <tr class="row-even" style="{documento_identidad_visible}">
                                                <td style="width: 40%"><strong>Documento de Identidad</strong></td>
                                                <td style="width: 60%">{contratista.documento_identidad if contratista else 'N/A'}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Fecha de Iniciación</strong></td>
                                                <td style="width: 60%">{contratista.fecha_iniciacion if contratista.fecha_iniciacion else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Fecha de terminación</strong></td>
                                                <td style="width: 60%">{contratista.fecha_terminacion if contratista.fecha_terminacion else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Duración</strong></td>
                                                <td style="width: 60%">{contratista.duracion if contratista.duracion else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>RPC</strong></td>
                                                <td style="width: 60%">{contratista.compromiso_presupuestal if contratista.compromiso_presupuestal else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="header">
                                                <td colspan="2"><strong>Pagos al Contratista</strong></td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Salario básico (Contrato Laboral)</strong></td>
                                                <td style="width: 60%">{valor_total_contrato_format}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Valor del otrosí</strong></td>
                                                <td style="width: 60%">{valor_del_otrosi_format}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Valor de contrato con otrosí</strong></td>
                                                <td style="width: 60%">{total_format}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Cantidad de cuotas</strong></td>
                                                <td style="width: 60%">{nuemor_de_cuotas}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Valor pagado al contratista</strong></td>
                                                <td style="width: 60%">{valor_pago_2suma_format}</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                    <br/>
                                </div>
                                """
            
            # --- Lógica de Filtro por NIT (si no hay DI) ---
            if not documento_identidad_actual and nit2_actual:
                # Filtra por nit2
                proyectos = self.env['contratacion'].search([])
                for proyecto in proyectos:
                    if isinstance(proyecto.name, str):
                        contratistas = self.env['contrataciones_fuv'].search([
                            ('proyecto_relacionado', '=', proyecto.id),
                            ('nit2', '=', nit2_actual)
                        ])

                        if contratistas:
                            for contratista in contratistas:
                                nombre = contratista.name if isinstance(contratista.name, str) else 'No se asignó'
                                # Generar la URL del contrato
                                url_contrato = f"/web#id={contratista.id}&model=contrataciones_fuv&view_type=form"

                                # Crear el enlace con el texto "ir al contrato"
                                nombre_enlace = f'<a href="{url_contrato}" target="_blank" class="link">Ir al Contrato</a>'

                                new_name = contratista.new_name if isinstance(contratista.new_name,
                                                                            str) else 'No se asignó'
                                nuemor_de_cuotas = contratista.nuemor_de_cuotas if contratista.nuemor_de_cuotas else 'No se asignó'
                                valor_total_contrato = contratista.valor_total_contrato if contratista.valor_total_contrato else 0.0
                                valor_total_contrato_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    valor_total_contrato, {'display_currency': currency_cop})


                                valor_del_otrosi = contratista.valor_del_otrosi if contratista.valor_del_otrosi else 0.0
                                valor_del_otrosi_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    valor_del_otrosi, {'display_currency': currency_cop})

                                total = contratista.total if contratista.total else 0.0
                                total_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    total, {'display_currency': currency_cop})

                                acumulado_por_contratista = contratista.acumulado_por_contratista if contratista.acumulado_por_contratista else 0.0
                                valor_pago_2suma_format = self.env['ir.qweb.field.monetary'].value_to_html(
                                    acumulado_por_contratista, {'display_currency': currency_cop})

                                # Controlar visibilidad de campos (Lógica original)
                                nit2_visible = '' if nit2_actual else 'display: none;'
                                documento_identidad_visible = '' if documento_identidad_actual else 'display: none;'

                                # --- Estructura HTML mejorada con la lógica de las tarjetas ---
                                resultado_html += f"""
                                <div class="contract-card">
                                    <div class="card-header">
                                        <div>{nombre}</div>
                                        <div>{nombre_enlace}</div>
                                    </div>
                                    <table>
                                        <tbody>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Proyecto</strong></td>
                                                <td style="width: 60%">{proyecto.name}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>ID del Contrato</strong></td>
                                                <td style="width: 60%">{contratista.id_del_contrato if contratista.id_del_contrato else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-odd" style="{nit2_visible}">
                                                <td style="width: 40%"><strong>NIT2</strong></td>
                                                <td style="width: 60%">{contratista.nit2 if contratista else 'N/A'}</td>
                                            </tr>
                                            <tr class="row-even" style="{documento_identidad_visible}">
                                                <td style="width: 40%"><strong>Documento de Identidad</strong></td>
                                                <td style="width: 60%">{contratista.documento_identidad if contratista else 'N/A'}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Fecha de Iniciación</strong></td>
                                                <td style="width: 60%">{contratista.fecha_iniciacion if contratista.fecha_iniciacion else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Fecha de terminación</strong></td>
                                                <td style="width: 60%">{contratista.fecha_terminacion if contratista.fecha_terminacion else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Duración</strong></td>
                                                <td style="width: 60%">{contratista.duracion if contratista.duracion else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>RPC</strong></td>  
                                                <td style="width: 60%">{contratista.compromiso_presupuestal if contratista.compromiso_presupuestal else 'No se asignó'}</td>
                                            </tr>
                                            <tr class="header">
                                                <td colspan="2"><strong>Pagos al Contratista</strong></td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Salario básico (Contrato Laboral)</strong></td>
                                                <td style="width: 60%">{valor_total_contrato_format}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Valor del otrosí</strong></td>
                                                <td style="width: 60%">{valor_del_otrosi_format}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Valor de contrato con otrosí</strong></td>
                                                <td style="width: 60%">{total_format}</td>
                                            </tr>
                                            <tr class="row-odd">
                                                <td style="width: 40%"><strong>Cantidad de cuotas</strong></td>
                                                <td style="width: 60%">{nuemor_de_cuotas}</td>
                                            </tr>
                                            <tr class="row-even">
                                                <td style="width: 40%"><strong>Valor pagado al contratista</strong></td>
                                                <td style="width: 60%">{valor_pago_2suma_format}</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                    <br/>
                                </div>
                                """
            
            resultado_html += "</body></html>"
            record.mostrar_proyectos = resultado_html


    ########################################################################################

    @api.depends('fecha_iniciacion')
    def _compute_fecha_iniciacion_texto(self):
        # Diccionario para traducir los nombres de los meses al español
        meses_en_espanol = {
            'January': 'Enero',
            'February': 'Febrero',
            'March': 'Marzo',
            'April': 'Abril',
            'May': 'Mayo',
            'June': 'Junio',
            'July': 'Julio',
            'August': 'Agosto',
            'September': 'Septiembre',
            'October': 'Octubre',
            'November': 'Noviembre',
            'December': 'Diciembre'
        }

        for record in self:
            if record.fecha_iniciacion:
                # Convertir el campo de fecha a un objeto datetime.date
                fecha = record.fecha_iniciacion
                # Obtener el nombre del mes en inglés
                mes_en_ingles = fecha.strftime('%B')
                # Traducir al español
                mes_en_espanol = meses_en_espanol.get(mes_en_ingles, mes_en_ingles)
                año = fecha.strftime('%Y')
                # Formato de fecha en texto
                record.fecha_iniciacion_texto = f"Se inicia el {fecha.day} de {mes_en_espanol} de {año}"
            else:
                record.fecha_iniciacion_texto = ''


    @api.depends('acumulado_por_contratista23', 'valor_total_contrato_reportes')
    def _compute_porcentaje_pago22(self):
        for pago in self:
            if pago.valor_total_contrato_reportes:
                pago.porcentaje_pago22 = (pago.acumulado_por_contratista23 / pago.valor_total_contrato_reportes) * 100
            else:
                pago.porcentaje_pago22 = 0.0

    @api.model
    def obtener_datos_pago(self):
        # Obtener todos los registros relacionados con el contratista actual
        registros = self.env['tabla_forma_de_pago'].search(
            [('contratista_reportes', '=', self.contratista_reportes.id)])
        datos_pago = [registro.cantidad_de_dinero for registro in registros]

        return datos_pago

    @api.model
    def create_from_contratistas(self):
        _logger.info("create_from_contratistas called")

        contratistas = self.env['contrataciones_fuv'].search([])
        created_count = 0

        for contratista in contratistas:
            if not (contratista.nit2 or contratista.documento_identidad):
                continue

            existing_report = self.search([('contratista_reportes', '=', contratista.id)], limit=1)

            if not existing_report and contratista.proyecto_relacionado:
                self.create({
                    'contratista_reportes': contratista.id,
                    'valor_total_contrato_reportes': contratista.total,
                    'id_contrato_reportes': contratista.id_del_contrato,
                    'nuemor_de_cuotas_reportes': contratista.nuemor_de_cuotas,
                    'nit2': contratista.nit2,
                    'documento_identidad': contratista.documento_identidad,
                })
                created_count += 1

        _logger.info(f"Total reports created: {created_count}")

    ############################################################


class ReporteProyectos(models.Model):
    _name = 'reporte_proyectos'
    _description = 'Reporte de proyectos'
    name = fields.Char("Nombre", compute='_compute_name', store=True)
    estado_proyecto = fields.Selection([
        ('draft', 'Borrador'),
        ('open', 'Abierto'),
        ('done', 'Hecho'),
        ('cancel', 'Cancelado'),
    ], string='Estado del Proyecto', default='draft')


    # Campos a mostrar por proyecto
    create_uid_R = fields.Many2one(string="Creador del proyecto", related='proyecto_relacionado4.creator_user_id')
    objeto_proyecto_R = fields.Char(string='Objeto del proyecto', related="proyecto_relacionado4.objeto_proyecto3")
    id_proyecto_R = fields.Char(string='Id del proyecto', related="proyecto_relacionado4.id_proyecto3")
    valor_total_final_R = fields.Float(related='proyecto_relacionado4.valor_total_final', string="Valor total")
    valor_a_contratar_R = fields.Monetary(related='proyecto_relacionado4.valor_a_contratar')
    valor_cobrado3 = fields.Monetary(string='Valor Cobrado', related='proyecto_relacionado4.valor_cobrado3')
    valor_pago_2suma3 = fields.Float(string='Valor contratado', related='proyecto_relacionado4.valor_pago_2suma3')

    proyecto_relacionado4 = fields.Many2one('contratacion', string='Proyecto relacionado', ondelete='cascade')
    fecha_inicio = fields.Date(string="Fecha de Inicio")
    fecha_fin = fields.Date(string="Fecha de Fin")
    descripcion = fields.Text(string="Descripción")
    valor_proyecto = fields.Monetary(string="Valor del Proyecto", currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string="Moneda")
    mostrar_html = fields.Html(string="Reporte HTML", compute='_compute_generar_html')


    def _translate_month_to_spanish(self, month):
        traducciones = {
            'January': 'Enero',
            'February': 'Febrero',
            'March': 'Marzo',
            'April': 'Abril',
            'May': 'Mayo',
            'June': 'Junio',
            'July': 'Julio',
            'August': 'Agosto',
            'September': 'Septiembre',
            'October': 'Octubre',
            'November': 'Noviembre',
            'December': 'Diciembre',
        }
        return traducciones.get(month, month)

    @api.depends('proyecto_relacionado4')
    def _compute_name(self):
        for record in self:
            if record.proyecto_relacionado4:
                nombre = record.proyecto_relacionado4.new_name or record.proyecto_relacionado4.name
                _logger.info("Nombre asignado: %s", nombre)
                record.name = nombre
            else:
                record.name = 'Sin proyecto relacionado'


    @api.depends('proyecto_relacionado4')
    def _compute_name(self):
        for record in self:
            if record.proyecto_relacionado4:
                nombre = record.proyecto_relacionado4.new_name or record.proyecto_relacionado4.name
                _logger.info("Nombre asignado: %s", nombre)
                record.name = nombre
            else:
                record.name = 'Sin proyecto relacionado'

    @api.depends('proyecto_relacionado4', 'name')
    def _compute_generar_html(self):
        for record in self:
            proyecto = record.proyecto_relacionado4

            # Busca la moneda COP
            currency_cop = self.env['res.currency'].search([('name', '=', 'COP')], limit=1)
            if not currency_cop:
                raise ValueError("No se encontró la moneda COP.")

            # Usa la moneda del registro o COP como predeterminada
            currency = record.currency_id or currency_cop

            # Verificar si hay un proyecto relacionado
            if not proyecto:
                record.mostrar_html = "<p>No hay proyecto relacionado.</p>"
                continue

            # Buscar contratistas relacionados al proyecto
            contratistas = self.env['contrataciones_fuv'].search([('proyecto_relacionado', '=', proyecto.id)])

            # Encabezados dinámicos para los meses
            meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre',
                     'Noviembre', 'Diciembre']

            contratistas_html = """
            <table style="border: 1px solid #ddd; border-collapse: collapse; width: 100%; margin-top: 20px;">
                <thead>
                    <tr>
                        <th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">CONTRATISTA</th>
                        <th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">VALOR TOTAL CONTRATO</th>
                        <th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">ACUMULADO PAGADO</th>
                        <th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">FALTANTE POR PAGAR</th>
                        <th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">% AVANCE PAGO</th>
            """

            # Agregar encabezados de meses
            for mes in meses:
                contratistas_html += f"""
                        <th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">{mes}</th>
                """

            contratistas_html += "</tr></thead><tbody>"

            # Generar filas para cada contratista
            for contratista in contratistas:
                contrato = self.env['contrato'].search([
                    ('proyecto_relacionado', '=', proyecto.id),
                    ('contratista1', '=', contratista.id),
                ], limit=1)

                # Obtener información de contrato
                valor_total_contrato = contratista.valor_total_contrato3 or 0.0
                valor_total_contrato_format = self.env['ir.qweb.field.monetary'].value_to_html(
                    valor_total_contrato, {'display_currency': currency_cop})

                acumulado_total = contrato.acomulado_total if contrato else 0.0
                acumulado_total_format = self.env['ir.qweb.field.monetary'].value_to_html(
                    acumulado_total, {'display_currency': currency_cop})

                faltante_por_pagar = valor_total_contrato - acumulado_total
                faltante_por_pagar_format = self.env['ir.qweb.field.monetary'].value_to_html(
                    faltante_por_pagar, {'display_currency': currency_cop})

                porcentaje_avance_pago = (
                        acumulado_total / valor_total_contrato * 100) if valor_total_contrato > 0 else 0.0

                progress_bar_html = """
                <div style="width: 100%; background-color: #f3f3f3; border-radius: 10px; position: relative; height: 21px;">
                    <div style="width: {:.2f}%; height: 100%; background-color: #4CAF50; border-radius: 10px; position: absolute; left: 0; top: 0;"></div>
                    <div style="position: absolute; left: 0; right: 0; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center; color: black;">{:.2f}%</div>
                </div>
                """.format(porcentaje_avance_pago, porcentaje_avance_pago)

                # Inicia una fila para el contratista
                contratistas_html += f"""
                    <tr>
                        <td style="border: 1px solid #ddd; padding: 10px;">{contratista.name or 'No asignado'}</td>
                        <td style="border: 1px solid #ddd; padding: 10px; text-align: right;">{valor_total_contrato_format}</td>
                        <td style="border: 1px solid #ddd; padding: 10px; text-align: right;">{acumulado_total_format}</td>
                        <td style="border: 1px solid #ddd; padding: 10px; text-align: right;">{faltante_por_pagar_format}</td>
                        <td style="border: 1px solid #ddd; padding: 10px;">{progress_bar_html}</td>
                """

                # Crear columnas de pagos por mes
                pagos = self.env['contrato'].search(
                    [('contratista1', '=', contratista.id), ('proyecto_relacionado', '=', proyecto.id)]
                )
                pagos_por_mes = {mes: 0 for mes in meses}
                for pago in pagos:
                    if pago.fecha_de_pago:  # Usar fecha_de_pago
                        mes_pago = pago.fecha_de_pago.strftime('%B')  # Obtener nombre del mes
                        mes_pago_es = self._translate_month_to_spanish(mes_pago)  # Traducir al español
                        if mes_pago_es in pagos_por_mes:
                            pagos_por_mes[mes_pago_es] += pago.pago_efectuado2_valor

                for mes in meses:
                    valor_mes = pagos_por_mes[mes]
                    valor_mes_html = self.env['ir.qweb.field.monetary'].value_to_html(
                        valor_mes, {'display_currency': currency_cop}
                    )
                    contratistas_html += f"""
                        <td style="border: 1px solid #ddd; padding: 10px; text-align: right;">{valor_mes_html if valor_mes > 0 else '-'}</td>
                    """

                contratistas_html += "</tr>"

            contratistas_html += "</tbody></table>"

            # Resto del código HTML permanece igual
            html_content = """
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                    th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
                    th {{ background-color: #f2f2f2; }}
                </style>
            </head>
            <body>
                <h2>Reporte de proyecto: <br/> {nombre}</h2>
                <table style="border: 1px solid #ddd; width: 100%; margin-top: 20px;">
                    <tr><th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">Creador del proyecto</th><td style="border: 1px solid #ddd; padding: 10px;">{creador}</td></tr>
                    <tr><th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">Objeto del proyecto</th><td style="border: 1px solid #ddd; padding: 10px;">{objeto}</td></tr>
                    <tr><th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">ID del proyecto</th><td style="border: 1px solid #ddd; padding: 10px;">{id_proyecto}</td></tr>
                    <tr><th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">Valor total</th><td style="border: 1px solid #ddd; padding: 10px;">{valor_total}</td></tr>
                    <tr><th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">Valor producción</th><td style="border: 1px solid #ddd; padding: 10px;">{valor_contratar}</td></tr>
                    <tr><th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">Valor cobrado</th><td style="border: 1px solid #ddd; padding: 10px;">{valor_cobrado}</td></tr>
                    <tr><th style="background-color: #ADD8E6; border: 1px solid #ddd; padding: 10px;">Valor pagado</th><td style="border: 1px solid #ddd; padding: 10px;">{valor_pago_proyecto}</td></tr>
                </table>
                <br/>
                <h3>Contratistas relacionados</h3>
                {contratistas_table}
            </body>
            </html>
            """.format(
                nombre=record.name or 'No asignado',
                creador=record.create_uid_R.name if record.create_uid_R else 'No asignado',
                objeto=record.objeto_proyecto_R or 'No asignado',
                id_proyecto=record.id_proyecto_R or 'No asignado',
                valor_total=self.env['ir.qweb.field.monetary'].value_to_html(record.valor_total_final_R,
                                                                             {'display_currency': currency_cop}),
                valor_contratar=self.env['ir.qweb.field.monetary'].value_to_html(record.valor_a_contratar_R,
                                                                                 {'display_currency': currency_cop}),
                valor_cobrado=self.env['ir.qweb.field.monetary'].value_to_html(record.valor_cobrado3,
                                                                                 {'display_currency': currency_cop}),
                valor_pago_proyecto=self.env['ir.qweb.field.monetary'].value_to_html(record.valor_pago_2suma3,
                                                                                 {'display_currency': currency_cop}),
                contratistas_table=contratistas_html
            )
            record.mostrar_html = html_content

    def action_download_R_proyecto_pdf(self):
        for record in self:
            try:
                # Obtener el HTML
                html = record.mostrar_html

                # Configuración para pdfkit con UTF-8
                config = pdfkit.configuration(
                    wkhtmltopdf='/usr/local/bin/wkhtmltopdf')  # Ajusta la ruta si es necesario
                options = {
                    'encoding': 'UTF-8',
                    'page-width': '350mm',  # Ajusta el tamaño de página
                    'orientation': 'Landscape',  # Cambia a orientación horizontal
                    'margin-top': '10mm',
                    'margin-right': '10mm',
                    'margin-bottom': '10mm',
                    'margin-left': '10mm',
                    'zoom': 0.9,  # Escala el contenido
                }

                # Convertir HTML a PDF
                pdf_content = pdfkit.from_string(html, False, configuration=config, options=options)

                # Determinar el nombre del archivo basado en el nombre del proyecto
                nombre_archivo = f'Reporte_{record.proyecto_relacionado4.name or "Desconocido"}.pdf'

                # Guardar el PDF en el archivo de descarga
                attachment = self.env['ir.attachment']._drive_create_temp_download({
                    'name': nombre_archivo,
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_content),
                    'store_fname': nombre_archivo,
                })

                return {
                    'type': 'ir.actions.act_url',
                    'url': '/web/content/%s' % (attachment.id),
                    'target': 'new',
                }
            except Exception as e:
                _logger.error("Error al generar el PDF: %s", str(e))
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Error',
                    'view_mode': 'form',
                    'res_model': 'res.users',
                    'view_id': False,
                    'target': 'new',
                    'context': {'message': 'Error al generar el PDF: %s' % str(e)},
                }

    ############################################################







class ProgramaPagos(models.Model):
    _name = 'programapagos'
    _order = 'create_date desc'
    _description = 'Programa de Pagos'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    is_editable_programapagos = fields.Boolean(string='Es editable', default=False, related="proyecto_relacionado.is_editable")

    @api.onchange('is_editable_programapagos')
    def _onchange_is_editable_programapagos(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass


    def agregar_todos_contratistas(self):
        """Inicia la ejecución de agregar todos los contratistas en un hilo separado y retorna una notificación."""
        start_time = time.time()  # Inicio del contador de tiempo

        # Crear y comenzar el hilo
        thread = threading.Thread(target=self._thread_agregar_todos_contratistas, args=(self.env.cr.dbname, self.env.uid, self.env.context))
        thread.daemon = True  # El hilo se cerrará cuando el programa principal termine
        thread.start()

        # Fin del contador de tiempo
        elapsed_time = time.time() - start_time
        _logger.info(f"agregar_todos_contratistas tomó {elapsed_time:.2f} segundos en iniciarse")

        # Retornar notificación inicial
        return self._send_notification(
            title='Proceso iniciado',
            message='Se ha iniciado la creación de contratistas...',
            notif_type='info',
            sticky=False
        )

    def _thread_agregar_todos_contratistas(self, dbname, uid, context):
        """Ejecuta la lógica de agregar todos los contratistas en un hilo separado."""
        start_time = time.time()  # Inicio del contador de tiempo
        registry = self.pool

        try:
            with registry.cursor() as new_cr:
                # Crear nuevo environment para el hilo
                env = api.Environment(new_cr, uid, context)

                # Obtener instancia actualizada del registro en el nuevo cursor
                self_new = self.with_env(env)

                # Ejecutar la lógica de agregar contratistas
                self_new._agregar_todos_contratistas_logic()

                # Enviar notificación de éxito y commit los cambios
                new_cr.commit()
                _logger.info("Proceso de agregar contratistas completado con éxito.")

        except Exception as e:
            with registry.cursor() as error_cr:
                env_error = api.Environment(error_cr, uid, context)
                self.with_env(env_error)._send_notification(
                    title='Error',
                    message=f'Error al agregar contratistas: {str(e)}',
                    notif_type='danger',
                    sticky=True
                )
                error_cr.commit()
            _logger.error(f"Error en _thread_agregar_todos_contratistas: {str(e)}")

        # Fin del contador de tiempo
        elapsed_time = time.time() - start_time
        _logger.info(f"_thread_agregar_todos_contratistas COMPLETADO en {elapsed_time:.2f} segundos")

    def _send_notification(self, title, message, notif_type, sticky):
        """Envía una notificación al cliente."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': notif_type,
                'sticky': sticky,
            }
        }


    def _agregar_todos_contratistas_logic(self):
        """Lógica principal para agregar/actualizar los contratistas"""
        _logger.info("Iniciando lógica de agregar/actualizar contratistas")

        if not self.proyecto_relacionado:
            _logger.warning("No hay proyecto relacionado")
            return False

        # Buscar contratistas relacionados
        contratistas = self.env['contrataciones_fuv'].search([
            ('proyecto_relacionado', '=', self.proyecto_relacionado.id)
        ])
        _logger.info(f"Contratistas encontrados: {len(contratistas)}")

        # Obtener contratos existentes y hacer actualizaciones
        contratos_existentes = self.env['contrato'].search([
            ('contrato_id', '=', self.id)
        ])

        contratos_existentes_dict = { (contrato.contratista1.id, contrato.numero_pago): contrato for contrato in contratos_existentes }
        claves_existentes = set(contratos_existentes_dict.keys())
        claves_para_agregar = set()

        # Actualizar o agregar registros
        for contratista in contratistas:
            forma_pago_records = self.env['tabla_forma_de_pago'].search([
                ('contrataciones_fuv_tabla_id', '=', contratista.id)
            ])
            for forma_pago in forma_pago_records:
                key = (contratista.id, forma_pago.numero_de_pagos)
                if key in contratos_existentes_dict:
                    contrato_existing = contratos_existentes_dict[key]
                    valores_nuevos = {
                        'descripcion_entregable': forma_pago.condicion,
                        'porcentaje_avance': forma_pago.tecnico,
                        'valor_pago': forma_pago.cantidad_de_dinero,
                        'id_contrato': forma_pago.id_del_contrato,
                        'porcentaje_pago': forma_pago.pago,
                    }
                    contrato_existing.write(valores_nuevos)
                    self._post_trazabilidad(
                        tipo='actualización',
                        contratista=contratista,
                        forma_pago=forma_pago,
                        contrato=contrato_existing
                    )
                else:
                    claves_para_agregar.add(key)

        # Crear registros nuevos
        for clave in claves_para_agregar:
            contratista_id, numero_pago = clave
            forma_pago_record = self.env['tabla_forma_de_pago'].search([
                ('contrataciones_fuv_tabla_id', '=', contratista_id),
                ('numero_de_pagos', '=', numero_pago)
            ], limit=1)
            if forma_pago_record:
                contrato_nuevo = self.env['contrato'].create({
                    'contrato_id': self.id,
                    'contratista1': contratista_id,
                    'proyecto_relacionado': self.proyecto_relacionado.id,
                    'numero_pago': numero_pago,
                    'descripcion_entregable': forma_pago_record.condicion,
                    'porcentaje_avance': forma_pago_record.tecnico,
                    'valor_pago': forma_pago_record.cantidad_de_dinero,
                    'id_contrato': forma_pago_record.id_del_contrato,
                    'porcentaje_pago': forma_pago_record.pago,
                })
                self._post_trazabilidad(
                    tipo='creación',
                    contratista=self.env['contrataciones_fuv'].browse(contratista_id),
                    forma_pago=forma_pago_record,
                    contrato=contrato_nuevo
                )

        _logger.info("Proceso de agregar/actualizar contratistas completado")
        return True

    def _post_trazabilidad(self, tipo, contratista, forma_pago, contrato):
        # Mensaje de actividad para actualización o creación con HTML para los saltos de línea
        if tipo == 'actualización':
            mensaje = (f"<p>Pago actualizado {contratista.name}:</p>"
                       f"<p>ID Contrato: {forma_pago.id_del_contrato}</p>"
                       f"<p>Descripción entregable: {forma_pago.condicion}</p>"
                       f"<p>Porcentaje avance: {forma_pago.tecnico}</p>"
                       f"<p>Valor pago: {forma_pago.cantidad_de_dinero}</p>"
                       f"<p>% DE PAGO: {forma_pago.pago}</p>")
            contrato.message_post(body=mensaje)
        elif tipo == 'creación':
            mensaje = (f"<p>Nuevo pago creado {contratista.name}:</p>"
                       f"<p>ID Contrato: {forma_pago.id_del_contrato}</p>"
                       f"<p>Descripción entregable: {forma_pago.condicion}</p>"
                       f"<p>Porcentaje avance: {forma_pago.tecnico}</p>"
                       f"<p>Valor pago: {forma_pago.cantidad_de_dinero}</p>"
                       f"<p>% DE PAGO: {forma_pago.pago}</p>")
            contrato.message_post(body=mensaje)
        # Mensaje de actividad para el modelo ProgramaPagos
        self.message_post(body=mensaje)

    def _post_trazabilidad_campos_adicionales(self, contrato):
        if not contrato:
            return

        # Obtener los valores antiguos
        old_values = self.read(['num_factura_cobro', 'fecha_pagar', 'fecha_de_pago', 'código_del_pago', 'acomulado_1'])
        old_values_dict = old_values[0] if old_values else {}

        # Obtener los valores nuevos
        new_values = {
            'num_factura_cobro': contrato.num_factura_cobro,
            'fecha_pagar': contrato.fecha_pagar,
            'fecha_de_pago': contrato.fecha_de_pago,
            'código_del_pago': contrato.código_del_pago,
            'acomulado_1': contrato.acomulado_1
        }

        # Construir el mensaje solo con los campos modificados
        mensaje = "<p>Modificado:</p>"

        fields = {
            'num_factura_cobro': '# DE FACTURA / CUENTA DE COBRO',
            'fecha_pagar': 'FECHA A PAGAR',
            'fecha_de_pago': 'FECHA DE PAGO',
            'código_del_pago': 'CODIGO DEL PAGO',
            'acomulado_1': 'ACUMULADO'
        }

        for field in fields:
            old_value = old_values_dict.get(field, 'No Disponible')
            new_value = new_values.get(field, 'No Disponible')
            if old_value != new_value:
                mensaje += f"<p>{fields[field]}: {new_value}</p>"

        # Solo registrar el mensaje si hay cambios
        if mensaje != "<p>Modificado:</p>":
            self.message_post(body=mensaje)
            contrato.message_post(body=mensaje)

    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado',
                                           related="mcb_id3.proyecto_relacionado2", ondelete='cascade')
    name = fields.Char(string='Nombre', tracking=True)
    new_name = fields.Char(string='Nombre', tracking=True)
    programapagos_ids = fields.One2many('contrato', 'contrato_id', string='Programa de Pagos')

    @api.depends('new_name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.new_name or record.name

    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)
    contratista1 = fields.Many2one(comodel_name='contrataciones_fuv', string="Contratista", tracking=True,
                                   domain="[('proyecto_relacionado', '=', proyecto_relacionado)]", ondelete='cascade')


    suma_valor_total_contrato3 = fields.Monetary(related='programapagos_ids.suma_valor_total_contrato3')
    saldo_disponible_2 = fields.Monetary(compute='_compute_saldo_disponible_nuevo')

    # Resta de Saldo a contratar a saldo contratado

    @api.depends('suma_total_costos_produccion_N', 'suma_valor_total_contrato3')
    def _compute_saldo_disponible_nuevo(self):
        for record in self:
            record.saldo_disponible_2 = record.suma_total_costos_produccion_N - record.suma_valor_total_contrato3


    id_contrato = fields.Char(string='ID Contrato', tracking=True, related="contratista1.id_del_contrato")

    valor_pago = fields.Float(string='VALOR DE PAGO', tracking=True, related="tabla_de_forma_de_pago.cantidad_de_dinero")
    tabla_de_forma_de_pago = fields.Many2one('tabla_forma_de_pago', string="Relación tabla",
                                             domain="[('contrataciones_fuv_tabla_id', '=', contratista1), ('seleccionado', '=', False)]", ondelete='cascade')





    pago = fields.Float(related="contratista1.pago")
    contador_pago = fields.Integer(string="Contador de Pagos", compute='_compute_contador_pago')

    @api.depends('tabla_de_forma_de_pago.pago')
    def _compute_contador_pago(self):
        for record in self:
            record.contador_pago = len(record.mapped('tabla_de_forma_de_pago.pago'))

    @api.onchange('valor_pago', 'contador_pago')
    def _actualizar_contador_pago(self):
        for record in self:
            if record.valor_pago:
                record.contador_pago -= 1


    valor_total_contrato3 = fields.Monetary(related='contratista1.total')
    numero_de_pagos = fields.Integer(related='contratista1.numero_de_pagos')
    acumulado_por_contratista2 = fields.Monetary(related="contratista1.acumulado_por_contratista_contrato")
    numero_pago = fields.Integer(string='Número de pago', tracking=True)
    num_cuotas_actuales = fields.Integer(string='Número de Cuotas Actuales', compute='_compute_num_cuotas_actuales')

    @api.depends('contratista1')
    def _compute_num_cuotas_actuales(self):
        for record in self:
            if record.contratista1:
                # Buscar todos los registros de contrato con el mismo contratista
                contratos_mismo_contratista = self.env['contrato'].search([('contratista1', '=', record.contratista1.id)])
                # Contar el número de registros
                record.num_cuotas_actuales = len(contratos_mismo_contratista)
            else:
                record.num_cuotas_actuales = 0

    @api.constrains('contratista1', 'acumulado_por_contratista2')
    def _check_valor_pago_limit(self):
        for record in self:
            if record.contratista1 and record.contratista1.total:
                if record.acumulado_por_contratista2 >= record.contratista1.total:
                    raise ValidationError("No se puede ingresar más valor de pago. El contratista ha alcanzado el valor total del contrato.")
                if record.num_cuotas_actuales >= record.nuemor_de_cuotas2:
                    raise ValidationError("No se puede ingresar más cuotas. El contratista ha alcanzado el número máximo de cuotas permitido.")


    nuemor_de_cuotas2 = fields.Integer(related="contratista1.nuemor_de_cuotas")
    pago_efectuado2 = fields.Float(string='Pago efectuado', tracking=True)
    # SUMA INDEPENDIENTE PARA UTILIZAR EL FILTRO POR CONTRATISTA
    acomulado_por_contratista = fields.Monetary(compute='_compute_acomulado_por_contratista')


    porcentaje_pago = fields.Float(string='% PAGO', tracking=True, compute="_compute_porcentaje_pago")
    porcentaje_avance = fields.Float(string='% Avance', tracking=True, related="tabla_de_forma_de_pago.tecnico")

    @api.depends('acumulado_por_contratista2', 'valor_total_contrato3')
    def _compute_porcentaje_pago(self):
        for pago in self:
            if pago.valor_total_contrato3:
                pago.porcentaje_pago = (pago.acumulado_por_contratista2 / pago.valor_total_contrato3) * 100
            else:
                pago.porcentaje_pago = 0.0

    descripcion_entregable = fields.Char(string='Descripción del entregable', tracking=True)
    num_factura_cobro = fields.Char(string='Número de factura / cuenta de cobro', tracking=True)
    fecha_pagar = fields.Date(string='Fecha a pagar', tracking=True)

    fecha_de_pago = fields.Date(string='Fecha de pago', tracking=True)
    código_del_pago = fields.Char(string='Código del pago', tracking=True)
    acumulado_de_los_pagos_por_empresa = fields.Char(string="Acumulado de los pagos por empresa")

    pago_efectuado2_sumado = fields.Float()

    @api.onchange('valor_pago')
    def _compute_color2(self):
        for record in self:
            if record.valor_pago is not None:
                record.color2 = 1
            else:
                record.color2 = 0

    color2 = fields.Integer(string='color', compute='_compute_color2')

    def agregar_nuevo(self):
        # Verifica si el saldo disponible es cero

        if self.saldo_disponible == 0:
            # Lanza una excepción de usuario para mostrar un mensaje de advertencia
            raise UserError("Saldo insuficiente para realizar esta operación")

        # Verifica si al menos uno de los campos editables está lleno
        if not self.show_editable_fields:
            # Muestra los campos editables
            self.write({'show_editable_fields': True})
            # Limpiar los campos editables
            self.id_contrato = ''
            self.numero_pago = 0
            self.valor_pago = 0.0
            self.porcentaje_pago = 0.0
            self.porcentaje_avance = 0.0
            self.descripcion_entregable = ''
            self.num_factura_cobro = ''
            self.fecha_pagar = False
            self.pago_efectuado2 = 0.0
            self.fecha_de_pago = False
            self.contratista1 = ''
            self.código_del_pago = ''
            self.acumulado_de_los_pagos_por_empresa = ''
            self.acomulado_1 = ''
            self.tabla_de_forma_de_pago = ''
        else:
            nuevo_contrato = self.env['contrato'].create({
                'id_contrato': self.id_contrato,
                'contratista1': self.contratista1.id,
                'numero_pago': self.numero_pago,
                'valor_pago': self.valor_pago,
                'porcentaje_pago': self.porcentaje_pago,
                'porcentaje_avance': self.porcentaje_avance,
                'descripcion_entregable': self.descripcion_entregable,
                'num_factura_cobro': self.num_factura_cobro,
                'fecha_pagar': self.fecha_pagar,
                'pago_efectuado2': self.pago_efectuado2,
                'fecha_de_pago': self.fecha_de_pago,
                'código_del_pago': self.código_del_pago,
                'acumulado_de_los_pagos_por_empresa': self.acumulado_de_los_pagos_por_empresa,
                'contrato_id': self.id,  # Relaciona el contrato con el programa de pagos
                'acomulado_1': self.acomulado_1,
                'proyecto_relacionado': self.proyecto_relacionado.id,
                'tabla_de_forma_de_pago': self.tabla_de_forma_de_pago.id
            })

            # Alternar el valor del campo booleano
            self.write({'show_editable_fields': not self.show_editable_fields, 'boton_agregar': not self.boton_agregar})
            # Marcar el registro seleccionado para que no vuelva a aparecer
            self.tabla_de_forma_de_pago.write({'seleccionado': True})

        return {}

    boton_agregar = fields.Boolean(default=True)
    boton_texto = fields.Char(compute='_compute_boton_texto')

    # Método para cambiar el nombre del botón según el valor del campo booleano

    @api.depends('boton_agregar')
    def _compute_boton_texto(self):
        for record in self:
            if record.boton_agregar:
                record.boton_texto = 'Agregar pago'
            else:
                record.boton_texto = 'Añadir'

    def datos(self):
        return {
            'name': 'Pagos',
            'view_mode': 'tree,form,pivot',
            'res_model': 'contrato',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('contrato_id', '=', self.id)],  # Filtrar por el ID del registro actual de Rubros
        }

    # Samuel relaciona mcb con programa pagos

    currency_id = fields.Many2one('res.currency', string='Moneda')
    mcb_id3 = fields.Many2one('ejecucion', string="ejecucion relacionado", ondelete='cascade')
    suma_total_costos_produccion_N = fields.Monetary(string="Saldo a contratar en programa pagos",
                                                     related='mcb_id3.suma_total_costos_produccion_N2')
    saldo_disponible = fields.Float(compute='_compute_suma_saldo_disponible')
    acomulado_1 = fields.Monetary(string="ACUMULADO")
    ###Resta de Saldo a contratar a saldo contratado

    @api.depends('suma_total_costos_produccion_N', 'valor_pago_2suma')
    def _compute_suma_saldo_disponible(self):
        for record in self:
            record.saldo_disponible = record.suma_total_costos_produccion_N - record.valor_pago_2suma

    ###Validación

    @api.constrains('acomulado_1')
    def _check_valor_pago(self):
        for pago in self:
            if pago.acomulado_1 > pago.saldo_disponible:
                raise ValidationError("El valor de pago no puede ser mayor que el saldo disponible.")

    valor_pago_2suma = fields.Float(related='programapagos_ids.valor_pago_2suma', string="Pago efectuado")

    show_editable_fields = fields.Boolean(string="Mostrar Campos Editables")

    @api.depends('acomulado_1', 'valor_total_contrato3', 'pago_efectuado2')
    def _compute_color(self):
        for record in self:
            if record.acomulado_1 == record.valor_total_contrato3:
                record.color = 1
            else:
                record.color = 0

    color = fields.Integer(string='Color', compute='_compute_color')


class Contrato(models.Model):
    _name = 'contrato'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    valor2_id = fields.One2many('modal', 'valor_id', string="Relación")
    currency_id = fields.Many2one('res.currency', string='Moneda', related='company_id.currency_id', readonly=True)

    fecha_de_pago = fields.Date(string='FECHA DE PAGO', compute='_compute_fecha_de_pago', store=True)
    fecha_de_pago_texto = fields.Char('Fecha de iniciación (texto)', compute='_compute_fecha_de_pago_texto')

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
    @api.depends('fecha_de_pago')
    def _compute_fecha_de_pago_texto(self):
        for record in self:
            record.fecha_de_pago_texto = self.convertir_fecha_a_texto(record.fecha_de_pago)

    is_editable_contrato = fields.Boolean(string='Es editable', default=False, related="proyecto_relacionado.is_editable")

    @api.onchange('is_editable_contrato')
    def _onchange_is_editable_contrato(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass

    @api.depends('valor2_id.fecha_pago_modal2')
    def _compute_fecha_de_pago(self):
        for record in self:
            fechas = record.valor2_id.mapped('fecha_pago_modal2')
            fechas_validas = [fecha for fecha in fechas if fecha]
            record.fecha_de_pago = max(fechas_validas) if fechas_validas else False

    acomulado_total = fields.Monetary(string="ACUMULADO TOTAL", compute='_compute_acumulado_total')

    @api.depends('pago_efectuado2_valor', 'contratista1')
    def _compute_acumulado_total(self):
        for record in self:
            if record.contratista1:
                contratos_mismo_contratista = self.search([('contratista1', '=', record.contratista1.id)])
                acomulado_total = sum(contrato.pago_efectuado2_valor for contrato in contratos_mismo_contratista)
                record.acomulado_total = acomulado_total
                #_logger.info(f"Contratista: {record.contratista1.name}, Acumulado Total: {acomulado_total}")
            else:
                record.acomulado_total = 0.0


    pago_efectuado2_HTML = fields.Html(
        string='PAGO EFECTUADO',
        compute='_compute_pago_efectuado2_HTML',
        sanitize=False
    )

    @api.depends('pago_efectuado2_valor', 'color3', 'is_editable_contrato')
    def _compute_pago_efectuado2_HTML(self):
        for record in self:
            # Filtrar registros guardados (excluir NewId)
            valor_registros_guardados = record.valor2_id.filtered(lambda r: r.id)

            # Obtener el registro con el valor total más alto
            valor_total_record = valor_registros_guardados.sorted(key=lambda r: r.id, reverse=True)[:1]
            valor = valor_total_record.TOTAL if valor_total_record else 0.0

            # Formatear el valor como moneda
            valor_formateado = formatLang(self.env, valor, currency_obj=record.currency_id)

            # Determinar el color basado en la comparación
            color_style = "color: green;" if record.color3 == 1 else "color: orange;"

            if record.is_editable_contrato:
                # Muestra el valor sin interacción
                record.pago_efectuado2_HTML = f"""
                    <div style="cursor: default; {color_style}">
                        {html_escape(valor_formateado)}
                    </div>
                """
            else:
                # Campo interactivo con funcionalidad
                record.pago_efectuado2_HTML = f"""
                    <div class="o_form_uri" style="cursor: pointer; {color_style}"
                         onclick="odoo.__WOWL_DEBUG__.root.env.services.action.doAction({{
                             type: 'ir.actions.act_window',
                             res_model: 'modal',
                             res_id: {valor_total_record.id if valor_total_record else 'false'},
                             name: 'ABONOS',
                             target: 'new',
                             views: [[false, 'form']],
                             context: {{'default_valor_id': {record.id}}}
                         }})">
                        {html_escape(valor_formateado)}
                    </div>
                """

    color3 = fields.Integer(string='Color3', compute='_compute_color3')


    @api.depends('valor_pago', 'pago_efectuado2_valor')
    def _compute_color3(self):
        for record in self:
            record.color3 = 1 if record.valor_pago == record.pago_efectuado2_valor else 0

    @api.depends('valor2_id.TOTAL')
    def _compute_pago_efectuado2_valor(self):
        for record in self:
            # Filtrar los registros guardados (evitar NewId) y obtener el registro con el valor total más alto
            valor_total_record = record.valor2_id.filtered(lambda r: r.id).sorted(key=lambda r: r.id, reverse=True)[:1]
            # Si hay un registro, asigna el valor TOTAL, si no, asigna 0.0
            record.pago_efectuado2_valor = valor_total_record.TOTAL if valor_total_record else 0.0

    pago_efectuado2_valor = fields.Float(string='Valor Pago Efectuado', compute='_compute_pago_efectuado2_valor')
    pago_efectuado2 = fields.Float(string='Pago Efectuado', compute="_compute_pago_efectuado2", store=True)

    @api.depends('pago_efectuado2_valor')
    def _compute_pago_efectuado2(self):
        for record in self:
            if record.pago_efectuado2 == 0.0:
                record.pago_efectuado2 = record.pago_efectuado2_valor





    def valor_popup(self):
        ventana_record = self.env['modal'].search([('valor_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'ABONOS',
                'type': 'ir.actions.act_window',
                'res_model': 'modal',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:

            ventana_record = self.env['modal'].create({
                'valor_id': self.id,
            })


            return {
                'name': 'ABONOS',
                'type': 'ir.actions.act_window',
                'res_model': 'modal',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }


    @api.model
    def create(self, vals):
        record = super(Contrato, self).create(vals)
        if record.contrato_id:
            record.contrato_id._post_trazabilidad_campos_adicionales(record)
        return record

    def write(self, vals):
        result = super(Contrato, self).write(vals)
        if self.contrato_id:
            self.contrato_id._post_trazabilidad_campos_adicionales(self)
        return result

    valor_total_contrato3 = fields.Monetary(related='contratista1.total')
    suma_valor_total_contrato3 = fields.Monetary(related='contratista1.suma_valor_total_contrato')

    def unlink(self):
        # Desvincular los registros de tabla_forma_de_pago
        for contrato in self:
            contrato.tabla_de_forma_de_pago.write({'seleccionado': False})
        return super(Contrato, self).unlink()


    @api.depends('acomulado_1', 'valor_total_contrato3', 'pago_efectuado2')
    def _compute_color(self):
        for record in self:
            if record.acomulado_1 == record.valor_total_contrato3:
                record.color = 1
            else:
                record.color = 0

    color = fields.Integer(string='Color', compute='_compute_color')
    igualdad_pagos_contrato = fields.Boolean(compute='_compute_igualdad_pagos_contrato')

    def check_igualdad_pagos_contrato(self):
        return self.valor_total_contrato3 == self.acumulado_por_contratista

    hiden = fields.Char(string='hiden')
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado', ondelete='cascade')




    # -3 Campo flotante llamado "Valor Pago 2 Suma", que se calcula con la función '_compute_valor_pago_2suma'

    valor_pago_2suma = fields.Float(compute='_compute_valor_pago_2suma', string='Valor Pago 2 Suma')

    # -3 Decorador que indica que la función depende de los campos 'acomulado_2' y 'proyecto_relacionado'
    @api.depends('acomulado_2', 'proyecto_relacionado', 'pago_efectuado2_valor')
    def _compute_valor_pago_2suma(self):
        # -3 Itera sobre cada registro (record) en el conjunto de registros actual
        for record in self:
            try:
                # -3 Verifica si el registro tiene un proyecto relacionado
                if record.proyecto_relacionado:
                    # -3 Busca todos los registros que pertenecen al mismo proyecto relacionado
                    registros_proyecto = self.search([('proyecto_relacionado', '=', record.proyecto_relacionado.id)])

                    # -3 Mapea (extrae) el campo 'acomulado_2' de los registros y suma sus valores
                    total_acumulado = sum(registros_proyecto.mapped('pago_efectuado2_valor'))

                    # -3 Si el total acumulado es mayor que 0, lo asigna a 'valor_pago_2suma'
                    if total_acumulado > 0:
                        record.valor_pago_2suma = total_acumulado
                    else:
                        # -3 Si no hay valor acumulado, establece 'valor_pago_2suma' en 0
                        record.valor_pago_2suma = 0
                else:
                    # -3 Si no hay proyecto relacionado, también establece 'valor_pago_2suma' en 0
                    record.valor_pago_2suma = 0
            except Exception as e:
                # -3 Si ocurre una excepción durante el cálculo, establece 'valor_pago_2suma' en 0 y muestra un mensaje de error
                record.valor_pago_2suma = 0
                print(f"Error al calcular 'valor_pago_2suma' para el registro {record.id}: {e}")



    acomulado_2 = fields.Monetary(string="ACUMULADO2", compute='_compute_acumulado_2')

    @api.depends('valor_pago', 'pago_efectuado2')
    def _compute_acumulado_2(self):
        for contrato in self:
            if contrato.pago_efectuado2:
                contrato.acomulado_2 = contrato.valor_pago
            else:
                contrato.acomulado_2 = 0.0

    #Función para obtener valor acomulado por contratista
    acumulado_por_contratista = fields.Monetary(string="Suma acumulada por contratista",
                                                compute='_compute_acumulado_por_contratista')

    @api.depends('valor_pago', 'pago_efectuado2', 'contratista1')
    # Este decorador indica que el método depende de los campos 'valor_pago', 'pago_efectuado2' y 'contratista1'.
    def _compute_acumulado_por_contratista(self):
        # Método para calcular el acumulado por contratista.
        for contrato in self:
            # Itera sobre cada registro de contrato.
            contratista = contrato.contratista1
            if contrato.pago_efectuado2 and contratista:
                # Si hay un pago efectuado y un contratista asignado:
                # Busca todos los contratos con el mismo nombre de contratista1.
                contratos_mismo_contratista = self.search([('contratista1', '=', contratista.id)])
                acumulado = sum(contrato.valor_pago for contrato in contratos_mismo_contratista)
                # Calcula la suma de los valores de pago de los contratos con el mismo contratista.
                contrato.acumulado_por_contratista = acumulado
            else:
                contrato.acumulado_por_contratista = 0.0







    # -3 Campo monetario llamado "ACUMULADO" que se calcula automáticamente con la función '_compute_acumulado_1'
    acomulado_1 = fields.Monetary(string="ACUMULADO", compute='_compute_acumulado_1')

    # -3 Decorador que indica que la función depende de los campos 'valor_pago', 'pago_efectuado2' y 'contratista1'
    @api.depends('valor_pago', 'pago_efectuado2', 'contratista1', 'pago_efectuado2_valor')
    def _compute_acumulado_1(self):
        # -3 Itera sobre cada registro (contrato) en el conjunto de registros actual
        for contrato in self:
            # -3 Obtiene el contratista asociado al contrato actual
            contratista = contrato.contratista1

            # -3 Si el pago ha sido efectuado
            if contrato.pago_efectuado2:
                # -3 Busca todos los contratos que tengan el mismo contratista
                contratos_mismo_contratista = self.search([('contratista1', '=', contratista.id)])
                # -3 Inicializa una variable para acumular el valor de los pagos
                acumulado = 0.0

                # -3 Itera sobre todos los contratos del mismo contratista
                for contrato_contratista in contratos_mismo_contratista:
                    # -3 Suma el valor del pago de cada contrato al acumulado
                    acumulado += contrato_contratista.pago_efectuado2_valor
                    # -3 Si el ID del contrato actual coincide con el contrato en el bucle, se detiene la acumulación
                    if contrato_contratista.id == contrato.id:
                        break
                # -3 Asigna el valor acumulado al campo 'acomulado_1' del contrato actual
                contrato.acomulado_1 = acumulado
            else:
                # -3 Si no se ha efectuado el pago, el valor acumulado es 0
                contrato.acomulado_1 = 0.0

    @api.depends('valor_pago', 'pago_efectuado2', 'contratista1')
    def _compute_porcentaje_pago2(self):
        for contrato in self:
            if contrato.pago_efectuado2:
                contratista = contrato.contratista1
                # Find all contracts with the same contratista1 name
                contratos_mismo_contratista = self.search([('contratista1', '=', contratista.id)])
                total_valor_contratos = sum(contrato.valor_pago for contrato in contratos_mismo_contratista)
                if total_valor_contratos != 0:
                    contrato.porcentaje_pago = (contrato.valor_pago / total_valor_contratos) * 100
                else:
                    contrato.porcentaje_pago = 0.0
            else:
                contrato.porcentaje_pago = 0.0

    valor_pago = fields.Float(string='VALOR PROYECTADO', tracking=True)
    currency_id = fields.Many2one('res.currency', string='Moneda')
    id_contrato = fields.Char(string='ID CONTRATO', tracking=True)
    numero_pago = fields.Integer(string='# DE PAGO', tracking=True)

    porcentaje_pago = fields.Float(string='% DE PAGO', tracking=True)
    porcentaje_pago_final = fields.Float(string='PORCENTAJE PAGADO', compute='_compute_porcentaje_pago_final')

    @api.depends('valor_pago', 'pago_efectuado2_valor', 'contratista1')
    def _compute_porcentaje_pago_final(self):
        for contrato in self:
            if contrato.pago_efectuado2_valor and contrato.contratista1:
                contratos_mismo_contratista = self.search([('contratista1', '=', contrato.contratista1.id)])
                total_pago_contratista = sum(c.valor_pago for c in contratos_mismo_contratista)
                porcentaje_acumulado = 0.0
                for c in contratos_mismo_contratista:
                    porcentaje_acumulado += (c.pago_efectuado2_valor / total_pago_contratista) * 100 if total_pago_contratista else 0
                    if c.id == contrato.id:
                        break
                contrato.porcentaje_pago_final = porcentaje_acumulado
            else:
                contrato.porcentaje_pago_final = 0.0


    tabla_de_forma_de_pago = fields.Many2one('tabla_forma_de_pago', string="Relación tabla",
                                             domain="[('contrataciones_fuv_tabla_id', '=', contratista1)]")


    porcentaje_avance = fields.Float(string='PORCENTAJE DE AVANCE TÉCNICO', tracking=True)
    descripcion_entregable = fields.Char(string='DESCRIPCIÓN DEL ENTREGABLE', tracking=True)
    num_factura_cobro = fields.Char(string='# DE FACTURA / CUENTA DE COBRO', tracking=True)
    fecha_pagar = fields.Date(string='FECHA A PAGAR', tracking=True)




    contratista1 = fields.Many2one(comodel_name='contrataciones_fuv', string="CONTRATISTA", tracking=True,
                                   domain="[('proyecto_relacionado', '=', proyecto_relacionado)]")


    código_del_pago = fields.Char(string='CODIGO DEL PAGO', tracking=True)
    acumulado_de_los_pagos_por_empresa = fields.Char(string="PAGOS POR EMPRESA")

    contrato_id = fields.Many2one('programapagos', string='Contrato', ondelete='cascade')

    @api.depends('contratista1')
    def _compute_chart_data(self):
        for record in self:
            chart_data = self._get_chart_data()
            record.chart_data = json.dumps(chart_data)

    def _get_chart_data(self):
        try:
            # Obtén los datos para el gráfico
            contratos = self.search([])  # Busca todos los contratos (ajusta el dominio si es necesario)
            contratistas = contratos.mapped('contratista1.name')  # Obtén los nombres de los contratistas
            acumulados = contratos.mapped('acumulado_por_contratista')  # Obtén los acumulados

            return {
                'labels': contratistas,
                'datasets': [{
                    'label': 'Acumulado por Contratista',
                    'data': acumulados,
                    'backgroundColor': 'rgba(52, 152, 219, 0.6)',
                    'borderColor': 'rgba(41, 128, 185, 1)',
                    'borderWidth': 1
                }]
            }
        except Exception as e:
            print(f"Error in _get_chart_data: {e}")
            return {}

    chart_data = fields.Char(string="Datos del Gráfico", compute="_compute_chart_data")



class Modal(models.Model):
    _name = 'modal'
    _description = 'Abono de pagos'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    is_editable_contrato = fields.Boolean(string='Es editable', default=False, related="valor_id.is_editable_contrato")

    @api.onchange('is_editable_contrato')
    def _onchange_is_editable_contrato(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass


    currency_id = fields.Many2one('res.currency', string='Moneda')
    valor_id = fields.Many2one('contrato', string="Relación", ondelete='cascade')
    valor_ids = fields.One2many('pago_efectuado_tabla', 'valor_tabla_id', string="Relación")
    TOTAL = fields.Monetary(string='TOTAL ABONADO', related="valor_ids.valor_pagado_modal_sumado")
    valor_pago = fields.Float(string='VALOR PROYECTADO', related="valor_id.valor_pago")
    diferencia = fields.Float(string='FALTANTE POR PAGAR', compute='_compute_diferencia')
    color = fields.Boolean(string='Color', compute='_compute_color')
    fecha_pago_modal2 = fields.Date(string="FECHA DE PAGOS", compute='_compute_fecha_pago_modal2', store=True)

    @api.depends('valor_ids.fecha_pago_modal2')
    def _compute_fecha_pago_modal2(self):
        for record in self:
            # Si no hay registros, establecer fecha como False
            if not record.valor_ids:
                record.fecha_pago_modal2 = False
                continue

            # Obtén la última fecha de pago del último registro
            last_pago = record.valor_ids[-1]
            record.fecha_pago_modal2 = last_pago.fecha_pago_modal2 if last_pago.fecha_pago_modal2 else False

    @api.depends('TOTAL', 'valor_pago')
    def _compute_diferencia(self):
        for record in self:
            record.diferencia = record.valor_pago - record.TOTAL

    @api.depends('TOTAL', 'valor_pago')
    def _compute_color(self):
        for record in self:
            record.color = record.valor_pago == record.TOTAL

    def action_save_and_reload(self):
        self.ensure_one()
        if self.TOTAL > self.valor_pago:
            raise ValidationError("No se puede pasar del valor proyectado.")
        self.write({})
        return True


class Pago_Efectuado_Tabla(models.Model):
    _name = 'pago_efectuado_tabla'
    _description = 'Pagos efectuados'
    currency_id = fields.Many2one('res.currency', string='Moneda')

    valor_tabla_id = fields.Many2one('modal', string="Relación", ondelete='cascade')
    fecha_pago_modal2 = fields.Date(string="FECHA DE PAGOS")
    valor_pagado_modal = fields.Monetary(string="PAGOS EFECTUADOS")
    descuentos = fields.Monetary(string="DESCUENTOS")
    pago_real = fields.Monetary(string="PAGO REAL")
    valor_pagado_modal_sumado = fields.Monetary(string="VALOR PAGADO SUMADO",
                                                compute="calcular_valor_pagado_modal_sumado")
    valor_pago = fields.Float(related='valor_tabla_id.valor_pago', string='Valor Pago')

    def calcular_valor_pagado_modal_sumado(self):
        total = sum(self.mapped('valor_pagado_modal'))
        self.valor_pagado_modal_sumado = total

    valor_pagado_modal = fields.Monetary(string="PAGOS EFECTUADOS")

    @api.onchange('pago_real', 'descuentos')
    def _onchange_pago_y_descuentos(self):
        for record in self:
            if record.pago_real is not None and record.descuentos is not None:
                # Realiza la operación solo si ambos valores tienen datos
                record.valor_pagado_modal = record.pago_real + record.descuentos


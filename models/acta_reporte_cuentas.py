# -*- coding: utf-8 -*-
import base64
from datetime import datetime, time as dt_time
from html import escape as html_escape
from io import BytesIO

import xlsxwriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ActaReporteCuentasWizard(models.Model):
    _name = 'acta.reporte.cuentas.wizard'
    _description = 'Generador de reportes de cuentas de cobro'
    _rec_name = 'name'

    name = fields.Char(
        string='Nombre',
        default='Informes de cuentas de cobro',
        required=True,
        readonly=True,
    )

    TIPO_FECHA = [
        ('create_date', 'Fecha de creación'),
        ('fecha_solicitud', 'Fecha de radicación'),
        ('fecha_estado_actual', 'Fecha de último estado'),
        ('fecha_envio_asis_coordinador', 'Fecha Asistente coordinación'),
        ('fecha_envio_coordinador', 'Fecha envío Coordinador'),
        ('fecha_envio_ventanilla', 'Fecha envío Ventanilla'),
        ('fecha_envio_director_financiero', 'Fecha envío Director Financiero'),
        ('fecha_firma_director_financiero', 'Fecha firma Director Financiero'),
        ('fecha_envio_contabilidad', 'Fecha envío Contabilidad'),
        ('fecha_envio_auxiliar_tesoreria', 'Fecha envío Auxiliar tesorería'),
        ('fecha_envio_tesoreria', 'Fecha envío Tesorería'),
        ('fecha_finalizacion', 'Fecha finalización'),
        ('fecha_rechazo', 'Fecha rechazo'),
    ]

    ETAPAS = [
        ('create_date', 'Creación'),
        ('fecha_solicitud', 'Radicación'),
        ('fecha_envio_asis_coordinador', 'Asistente de coordinación'),
        ('fecha_envio_coordinador', 'Coordinador'),
        ('fecha_envio_ventanilla', 'Ventanilla'),
        ('fecha_envio_director_financiero', 'Director Financiero'),
        ('fecha_firma_director_financiero', 'Firma Director Financiero'),
        ('fecha_envio_contabilidad', 'Contabilidad'),
        ('fecha_envio_auxiliar_tesoreria', 'Auxiliar de tesorería'),
        ('fecha_envio_tesoreria', 'Tesorería'),
        ('fecha_finalizacion', 'Finalización'),
        ('fecha_rechazo', 'Rechazo'),
    ]

    ESTADOS = [
        ('asis_coordinador', 'Asistente de coordinación'),
        ('coordinador', 'Coordinador'),
        ('ventanilla', 'Ventanilla'),
        ('director_financiero', 'Director Financiero'),
        ('finaciera', 'Contabilidad'),
        ('au_tesoreria', 'Auxiliar de tesorería'),
        ('tesorería', 'Tesorería'),
        ('finalizado', 'Finalizado'),
        ('rechazado', 'Rechazado'),
    ]

    tipo_fecha = fields.Selection(
        TIPO_FECHA,
        string='Filtrar por fecha',
        default='create_date',
        required=True,
    )
    fecha_desde = fields.Date(string='Desde')
    fecha_hasta = fields.Date(string='Hasta')
    estado = fields.Selection(ESTADOS, string='Estado actual')
    clase_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
    ], string='Tipo de persona')
    origen_cuenta = fields.Selection([
        ('fuv', 'Fundación Universidad del Valle'),
        ('regalias', 'Sistema General de Regalías'),
    ], string='Origen de la cuenta')
    tipo_cuenta = fields.Selection([
        ('servicios', 'Orden de prestación de servicios'),
        ('bienes', 'Orden de compra de bienes'),
        ('contrato', 'Contrato'),
        ('otrosi', 'Otrosí'),
        ('regalias', 'Regalías'),
    ], string='Tipo de cuenta')
    buscar_texto = fields.Char(
        string='Contratista / documento / proyecto',
        help='Busca simultáneamente por nombre del contratista, documento/NIT o proyecto.',
    )
    contratista = fields.Char(string='Contratista')
    documento_nit = fields.Char(string='Documento/NIT')
    proyecto = fields.Char(string='Proyecto')
    pago = fields.Integer(string='Pago N°')
    numero_factura = fields.Char(string='Factura')
    proyecto_filtro = fields.Selection([
        ('con', 'Con proyecto'),
        ('sin', 'Sin proyecto'),
    ], string='Proyecto asociado')
    factura_filtro = fields.Selection([
        ('con', 'Con factura'),
        ('sin', 'Sin factura'),
    ], string='Estado de factura')
    pendiente_firma = fields.Selection([
        ('coordinador', 'Mis pendientes firma Coordinador'),
        ('director', 'Pendiente firma Director Financiero'),
    ], string='Pendiente de firma')
    seguimiento_correccion = fields.Char(string='Seguimiento corrección')
    correccion_en_manos_de = fields.Char(string='Corrección en manos de')
    estado_cesion = fields.Selection([
        ('no_aplica', 'No aplica'),
        ('pendiente_juridica', 'Pendiente Jurídica'),
        ('pendiente_coordinador', 'Pendiente Coordinador'),
        ('aprobada', 'Aprobada'),
    ], string='Estado cesión de derechos')
    orden_pago_filtro = fields.Selection([
        ('con', 'Con orden de pago'),
        ('sin', 'Sin orden de pago'),
    ], string='Orden de pago')
    orden_de_pago_id = fields.Many2one('drive', string='Orden de pago específica')
    estado_programacion_pago = fields.Selection([
        ('Pago finalizado', 'Pago finalizado'),
        ('En Tesorería para pago', 'En Tesorería para pago'),
        ('Orden de pago cargada - fecha tentativa registrada',
         'Orden cargada con fecha tentativa'),
        ('Orden de pago cargada - pendiente fecha tentativa de pago',
         'Orden cargada sin fecha tentativa'),
        ('Sin orden de pago cargada', 'Sin orden de pago cargada'),
    ], string='Estado programación de pago')
    responsable_id = fields.Many2one('res.users', string='Usuario responsable')
    etapa_responsable = fields.Selection([
        ('cualquiera', 'Cualquier etapa'),
        ('usuarios_contratacion', 'Coordinador'),
        ('usuarios_supervision', 'Ventanilla'),
        ('usuarios_contabilidad', 'Contabilidad'),
        ('usuarios_auxiliar_tesoreria', 'Auxiliar de tesorería'),
        ('usuarios_tesoreria', 'Tesorería'),
    ], string='Responsable en', default='cualquiera')
    alcance = fields.Selection([
        ('todas', 'Todas las cuentas'),
        ('en_proceso', 'Solo en proceso'),
        ('finalizadas', 'Solo finalizadas'),
        ('rechazadas', 'Solo rechazadas'),
        ('correccion', 'Solo con corrección'),
        ('cesion', 'Solo con cesión de derechos'),
    ], string='Alcance', default='todas', required=True)
    etapa_desde = fields.Selection(
        ETAPAS,
        string='Medir tiempo desde',
        default='fecha_solicitud',
        required=True,
    )
    etapa_hasta = fields.Selection(
        ETAPAS,
        string='Hasta',
        default='fecha_finalizacion',
        required=True,
    )
    orden = fields.Selection([
        ('ultimo_movimiento', 'Último movimiento primero'),
        ('creacion_reciente', 'Creación más reciente'),
        ('creacion_antigua', 'Creación más antigua'),
        ('director_reciente', 'Director Financiero: más reciente'),
        ('director_antiguo', 'Director Financiero: más antiguo'),
        ('mayor_duracion', 'Mayor duración seleccionada'),
        ('menor_duracion', 'Menor duración seleccionada'),
    ], string='Ordenar por', default='ultimo_movimiento', required=True)
    line_ids = fields.One2many(
        'acta.reporte.cuentas.line',
        'wizard_id',
        string='Resultados',
        readonly=True,
    )
    filtro_resumen = fields.Selection([
        ('todas', 'Todas'),
        ('en_proceso', 'En proceso'),
        ('finalizadas', 'Finalizadas'),
        ('rechazadas', 'Rechazadas'),
        ('correccion', 'Con corrección'),
        ('cesion', 'Con cesión'),
        ('radicadas', 'Radicadas por contratista'),
        ('envio_asistente', 'Enviadas a Asistente'),
        ('envio_coordinador', 'Enviadas a Coordinador'),
        ('envio_ventanilla', 'Enviadas a Ventanilla'),
        ('envio_director', 'Enviadas a Director Financiero'),
        ('envio_contabilidad', 'Enviadas a Contabilidad'),
        ('envio_auxiliar', 'Enviadas a Auxiliar de tesorería'),
        ('envio_tesoreria', 'Enviadas a Tesorería'),
        ('con_proyecto', 'Con proyecto'),
        ('sin_proyecto', 'Sin proyecto'),
        ('con_factura', 'Con factura'),
        ('sin_factura', 'Sin factura'),
        ('estado_asistente', 'Asistente de coordinación'),
        ('estado_coordinador', 'Coordinador'),
        ('estado_ventanilla', 'Ventanilla'),
        ('estado_director', 'Director Financiero'),
        ('estado_contabilidad', 'Contabilidad'),
        ('estado_auxiliar', 'Auxiliar de tesorería'),
        ('estado_tesoreria', 'Tesorería'),
        ('mis_pendientes', 'Mis pendientes de firma'),
        ('pendiente_director', 'Pendiente firma Director'),
        ('cesion_juridica', 'Cesión pendiente Jurídica'),
        ('cesion_coordinador', 'Cesión pendiente Coordinador'),
        ('con_orden', 'Con orden de pago'),
        ('sin_orden', 'Sin orden de pago'),
        ('orden_sin_fecha', 'Orden sin fecha tentativa'),
    ], default='todas', string='Filtro del resumen')
    filtros_resumen_activos = fields.Char(
        string='Filtros activos de la tabla',
        default='',
        copy=False,
    )
    filtros_resumen_descripcion = fields.Char(
        string='Filtros activos',
        compute='_compute_filtros_resumen_descripcion',
    )
    filtro_activo_en_proceso = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_finalizadas = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_rechazadas = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_correccion = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_cesion = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_radicadas = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_envio_asistente = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_envio_coordinador = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_envio_ventanilla = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_envio_director = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_envio_contabilidad = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_envio_auxiliar = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_envio_tesoreria = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_con_proyecto = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_sin_proyecto = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_con_factura = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_sin_factura = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_estado_asistente = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_estado_coordinador = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_estado_ventanilla = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_estado_director = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_estado_contabilidad = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_estado_auxiliar = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_estado_tesoreria = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_mis_pendientes = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_pendiente_director = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_cesion_juridica = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_cesion_coordinador = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_con_orden = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_sin_orden = fields.Boolean(compute='_compute_filtros_activos')
    filtro_activo_orden_sin_fecha = fields.Boolean(compute='_compute_filtros_activos')
    line_ids_mostradas = fields.One2many(
        'acta.reporte.cuentas.line',
        'wizard_id',
        string='Resultados visibles',
        compute='_compute_line_ids_mostradas',
        readonly=True,
    )
    cantidad_resultados = fields.Integer(
        string='Cuentas encontradas', compute='_compute_resumen_resultados'
    )
    valor_total_resultados = fields.Float(
        string='Valor total', compute='_compute_resumen_resultados'
    )
    cantidad_en_proceso = fields.Integer(
        string='En proceso', compute='_compute_resumen_resultados'
    )
    cantidad_finalizadas = fields.Integer(
        string='Finalizadas', compute='_compute_resumen_resultados'
    )
    cantidad_rechazadas = fields.Integer(
        string='Rechazadas', compute='_compute_resumen_resultados'
    )
    cantidad_con_correccion = fields.Integer(
        string='Con corrección', compute='_compute_resumen_resultados'
    )
    cantidad_con_cesion = fields.Integer(
        string='Con cesión', compute='_compute_resumen_resultados'
    )
    promedio_dias_proceso = fields.Float(
        string='Promedio de días', digits=(16, 2), compute='_compute_resumen_resultados'
    )
    valor_promedio_resultados = fields.Float(
        string='Valor promedio', compute='_compute_resumen_resultados'
    )
    cantidad_radicadas = fields.Integer(string='Radicadas', compute='_compute_resumen_resultados')
    cantidad_envio_asistente = fields.Integer(string='A Asistente', compute='_compute_resumen_resultados')
    cantidad_envio_coordinador = fields.Integer(string='A Coordinador', compute='_compute_resumen_resultados')
    cantidad_envio_ventanilla = fields.Integer(string='A Ventanilla', compute='_compute_resumen_resultados')
    cantidad_envio_director = fields.Integer(string='A Director Financiero', compute='_compute_resumen_resultados')
    cantidad_envio_contabilidad = fields.Integer(string='A Contabilidad', compute='_compute_resumen_resultados')
    cantidad_envio_auxiliar = fields.Integer(string='A Auxiliar tesorería', compute='_compute_resumen_resultados')
    cantidad_envio_tesoreria = fields.Integer(string='A Tesorería', compute='_compute_resumen_resultados')
    promedio_dias_elaboracion = fields.Float(string='Días elaboración', digits=(16, 2), compute='_compute_resumen_resultados')
    promedio_horas_asis_coordinador = fields.Float(string='Horas Asistente → Coordinador', digits=(16, 2), compute='_compute_resumen_resultados')
    promedio_horas_coordinador_ventanilla = fields.Float(string='Horas Coordinador → Ventanilla', digits=(16, 2), compute='_compute_resumen_resultados')
    promedio_horas_ventanilla_director = fields.Float(string='Horas Ventanilla → Director', digits=(16, 2), compute='_compute_resumen_resultados')
    promedio_horas_director_contabilidad = fields.Float(string='Horas Director → Contabilidad', digits=(16, 2), compute='_compute_resumen_resultados')
    promedio_horas_contabilidad_auxiliar = fields.Float(string='Horas Contabilidad → Auxiliar', digits=(16, 2), compute='_compute_resumen_resultados')
    promedio_horas_auxiliar_tesoreria = fields.Float(string='Horas Auxiliar → Tesorería', digits=(16, 2), compute='_compute_resumen_resultados')
    promedio_horas_tesoreria_final = fields.Float(string='Horas Tesorería → Final', digits=(16, 2), compute='_compute_resumen_resultados')
    cantidad_con_proyecto = fields.Integer(string='Con proyecto', compute='_compute_resumen_resultados')
    cantidad_sin_proyecto = fields.Integer(string='Sin proyecto', compute='_compute_resumen_resultados')
    cantidad_con_factura = fields.Integer(string='Con factura', compute='_compute_resumen_resultados')
    cantidad_sin_factura = fields.Integer(string='Sin factura', compute='_compute_resumen_resultados')
    cantidad_estado_asistente = fields.Integer(string='Asistente coordinación', compute='_compute_resumen_resultados')
    cantidad_estado_coordinador = fields.Integer(string='Coordinador', compute='_compute_resumen_resultados')
    cantidad_estado_ventanilla = fields.Integer(string='Ventanilla', compute='_compute_resumen_resultados')
    cantidad_estado_director = fields.Integer(string='Director Financiero', compute='_compute_resumen_resultados')
    cantidad_estado_contabilidad = fields.Integer(string='Contabilidad', compute='_compute_resumen_resultados')
    cantidad_estado_auxiliar = fields.Integer(string='Auxiliar tesorería', compute='_compute_resumen_resultados')
    cantidad_estado_tesoreria = fields.Integer(string='Tesorería', compute='_compute_resumen_resultados')
    cantidad_mis_pendientes = fields.Integer(string='Mis pendientes', compute='_compute_resumen_resultados')
    cantidad_pendiente_director = fields.Integer(string='Pendiente firma Director', compute='_compute_resumen_resultados')
    cantidad_cesion_juridica = fields.Integer(string='Cesión pendiente Jurídica', compute='_compute_resumen_resultados')
    cantidad_cesion_coordinador = fields.Integer(string='Cesión pendiente Coordinador', compute='_compute_resumen_resultados')
    cantidad_con_orden = fields.Integer(string='Con orden de pago', compute='_compute_resumen_resultados')
    cantidad_sin_orden = fields.Integer(string='Sin orden de pago', compute='_compute_resumen_resultados')
    cantidad_orden_sin_fecha = fields.Integer(string='Orden sin fecha tentativa', compute='_compute_resumen_resultados')

    @api.depends(
        'line_ids',
        'line_ids.estado',
        'line_ids.correccion_portal_activa',
        'line_ids.correccion_reporte_activa',
        'line_ids.cesion_derechos',
        'line_ids.create_date_origen',
        'line_ids.fecha_solicitud',
        'line_ids.fecha_envio_asis_coordinador',
        'line_ids.fecha_envio_coordinador',
        'line_ids.fecha_envio_ventanilla',
        'line_ids.fecha_envio_director_financiero',
        'line_ids.fecha_envio_contabilidad',
        'line_ids.fecha_envio_auxiliar_tesoreria',
        'line_ids.fecha_envio_tesoreria',
        'line_ids.fecha_finalizacion',
        'line_ids.horas_asis_a_coordinador',
        'line_ids.horas_coordinador_a_ventanilla',
        'line_ids.horas_ventanilla_a_director',
        'line_ids.horas_director_a_contabilidad',
        'line_ids.horas_contabilidad_a_auxiliar',
        'line_ids.horas_auxiliar_a_tesoreria',
        'line_ids.horas_tesoreria_a_final',
        'filtros_resumen_activos',
    )
    def _compute_line_ids_mostradas(self):
        for wizard in self:
            wizard.line_ids_mostradas = wizard._aplicar_filtros_resumen(wizard.line_ids)

    @api.depends('filtros_resumen_activos')
    def _compute_filtros_resumen_descripcion(self):
        etiquetas = dict(self._fields['filtro_resumen'].selection)
        for wizard in self:
            activos = wizard._filtros_resumen_lista()
            wizard.filtros_resumen_descripcion = ', '.join(
                etiquetas.get(filtro, filtro) for filtro in activos
            )

    @api.depends('filtros_resumen_activos')
    def _compute_filtros_activos(self):
        claves = (
            'en_proceso', 'finalizadas', 'rechazadas', 'correccion', 'cesion',
            'radicadas', 'envio_asistente', 'envio_coordinador',
            'envio_ventanilla', 'envio_director', 'envio_contabilidad',
            'envio_auxiliar', 'envio_tesoreria', 'con_proyecto', 'sin_proyecto',
            'con_factura', 'sin_factura', 'estado_asistente', 'estado_coordinador',
            'estado_ventanilla', 'estado_director', 'estado_contabilidad',
            'estado_auxiliar', 'estado_tesoreria', 'mis_pendientes',
            'pendiente_director', 'cesion_juridica', 'cesion_coordinador',
            'con_orden', 'sin_orden', 'orden_sin_fecha',
        )
        for wizard in self:
            activos = set(wizard._filtros_resumen_lista())
            for clave in claves:
                wizard['filtro_activo_%s' % clave] = clave in activos

    def _filtros_resumen_lista(self):
        self.ensure_one()
        return [
            filtro for filtro in (self.filtros_resumen_activos or '').split(',')
            if filtro
        ]

    def _coincide_filtro_resumen(self, linea, filtro):
        estados = {
            'estado_asistente': 'Asistente de coordinación',
            'estado_coordinador': 'Coordinador',
            'estado_ventanilla': 'Ventanilla',
            'estado_director': 'Director Financiero',
            'estado_contabilidad': 'Contabilidad',
            'estado_auxiliar': 'Auxiliar de tesorería',
            'estado_tesoreria': 'Tesorería',
        }
        comprobaciones = {
            'en_proceso': lambda: linea.estado not in ('Finalizado', 'Rechazado'),
            'finalizadas': lambda: linea.estado == 'Finalizado',
            'rechazadas': lambda: linea.estado == 'Rechazado',
            'correccion': lambda: linea.correccion_portal_activa or linea.correccion_reporte_activa,
            'cesion': lambda: linea.cesion_derechos,
            'radicadas': lambda: bool(linea.fecha_solicitud),
            'envio_asistente': lambda: bool(linea.fecha_envio_asis_coordinador),
            'envio_coordinador': lambda: bool(linea.fecha_envio_coordinador),
            'envio_ventanilla': lambda: bool(linea.fecha_envio_ventanilla),
            'envio_director': lambda: bool(linea.fecha_envio_director_financiero),
            'envio_contabilidad': lambda: bool(linea.fecha_envio_contabilidad),
            'envio_auxiliar': lambda: bool(linea.fecha_envio_auxiliar_tesoreria),
            'envio_tesoreria': lambda: bool(linea.fecha_envio_tesoreria),
            'con_proyecto': lambda: linea.tiene_proyecto,
            'sin_proyecto': lambda: not linea.tiene_proyecto,
            'con_factura': lambda: linea.tiene_factura,
            'sin_factura': lambda: not linea.tiene_factura,
            'mis_pendientes': lambda: linea.mis_pendientes_firma,
            'pendiente_director': lambda: linea.pendiente_firma_director_financiero,
            'cesion_juridica': lambda: linea.estado_cesion == 'Pendiente Jurídica',
            'cesion_coordinador': lambda: linea.estado_cesion == 'Pendiente Coordinador',
            'con_orden': lambda: bool(linea.orden_de_pago),
            'sin_orden': lambda: not linea.orden_de_pago,
            'orden_sin_fecha': lambda: linea.estado_programacion_pago
                == 'Orden de pago cargada - pendiente fecha tentativa de pago',
        }
        if filtro in estados:
            return linea.estado == estados[filtro]
        comprobacion = comprobaciones.get(filtro)
        return bool(comprobacion and comprobacion())

    def _aplicar_filtros_resumen(self, lineas):
        self.ensure_one()
        activos = set(self._filtros_resumen_lista())
        if not activos:
            return lineas
        grupos = [
            {'en_proceso', 'finalizadas', 'rechazadas', 'estado_asistente',
             'estado_coordinador', 'estado_ventanilla', 'estado_director',
             'estado_contabilidad', 'estado_auxiliar', 'estado_tesoreria'},
            {'radicadas', 'envio_asistente', 'envio_coordinador', 'envio_ventanilla',
             'envio_director', 'envio_contabilidad', 'envio_auxiliar', 'envio_tesoreria'},
            {'con_proyecto', 'sin_proyecto'},
            {'con_factura', 'sin_factura'},
            {'correccion'},
            {'cesion', 'cesion_juridica', 'cesion_coordinador'},
            {'mis_pendientes', 'pendiente_director'},
            {'con_orden', 'sin_orden', 'orden_sin_fecha'},
        ]
        for grupo in grupos:
            seleccionados = activos.intersection(grupo)
            if seleccionados:
                lineas = lineas.filtered(
                    lambda linea: any(
                        self._coincide_filtro_resumen(linea, filtro)
                        for filtro in seleccionados
                    )
                )
        return lineas

    @api.depends(
        'line_ids',
        'line_ids.valor_cuenta',
        'line_ids.estado',
        'line_ids.dias_total_proceso',
        'line_ids.correccion_portal_activa',
        'line_ids.correccion_reporte_activa',
        'line_ids.cesion_derechos',
    )
    def _compute_resumen_resultados(self):
        for wizard in self:
            lineas = wizard.line_ids
            wizard.cantidad_resultados = len(lineas)
            wizard.valor_total_resultados = sum(lineas.mapped('valor_cuenta'))
            wizard.cantidad_finalizadas = len(lineas.filtered(
                lambda linea: linea.estado == 'Finalizado'
            ))
            wizard.cantidad_rechazadas = len(lineas.filtered(
                lambda linea: linea.estado == 'Rechazado'
            ))
            wizard.cantidad_con_correccion = len(lineas.filtered(
                lambda linea: linea.correccion_portal_activa
                or linea.correccion_reporte_activa
            ))
            wizard.cantidad_con_cesion = len(lineas.filtered('cesion_derechos'))
            wizard.cantidad_en_proceso = len(lineas.filtered(
                lambda linea: linea.estado not in ('Finalizado', 'Rechazado')
            ))
            wizard.cantidad_radicadas = len(lineas.filtered('fecha_solicitud'))
            wizard.cantidad_envio_asistente = len(lineas.filtered('fecha_envio_asis_coordinador'))
            wizard.cantidad_envio_coordinador = len(lineas.filtered('fecha_envio_coordinador'))
            wizard.cantidad_envio_ventanilla = len(lineas.filtered('fecha_envio_ventanilla'))
            wizard.cantidad_envio_director = len(lineas.filtered('fecha_envio_director_financiero'))
            wizard.cantidad_envio_contabilidad = len(lineas.filtered('fecha_envio_contabilidad'))
            wizard.cantidad_envio_auxiliar = len(lineas.filtered('fecha_envio_auxiliar_tesoreria'))
            wizard.cantidad_envio_tesoreria = len(lineas.filtered('fecha_envio_tesoreria'))
            wizard.cantidad_con_proyecto = len(lineas.filtered('tiene_proyecto'))
            wizard.cantidad_sin_proyecto = len(lineas.filtered(lambda linea: not linea.tiene_proyecto))
            wizard.cantidad_con_factura = len(lineas.filtered('tiene_factura'))
            wizard.cantidad_sin_factura = len(lineas.filtered(lambda linea: not linea.tiene_factura))
            wizard.cantidad_estado_asistente = len(lineas.filtered(
                lambda linea: linea.estado == 'Asistente de coordinación'
            ))
            wizard.cantidad_estado_coordinador = len(lineas.filtered(
                lambda linea: linea.estado == 'Coordinador'
            ))
            wizard.cantidad_estado_ventanilla = len(lineas.filtered(
                lambda linea: linea.estado == 'Ventanilla'
            ))
            wizard.cantidad_estado_director = len(lineas.filtered(
                lambda linea: linea.estado == 'Director Financiero'
            ))
            wizard.cantidad_estado_contabilidad = len(lineas.filtered(
                lambda linea: linea.estado == 'Contabilidad'
            ))
            wizard.cantidad_estado_auxiliar = len(lineas.filtered(
                lambda linea: linea.estado == 'Auxiliar de tesorería'
            ))
            wizard.cantidad_estado_tesoreria = len(lineas.filtered(
                lambda linea: linea.estado == 'Tesorería'
            ))
            wizard.cantidad_mis_pendientes = len(lineas.filtered('mis_pendientes_firma'))
            wizard.cantidad_pendiente_director = len(
                lineas.filtered('pendiente_firma_director_financiero')
            )
            wizard.cantidad_cesion_juridica = len(lineas.filtered(
                lambda linea: linea.estado_cesion == 'Pendiente Jurídica'
            ))
            wizard.cantidad_cesion_coordinador = len(lineas.filtered(
                lambda linea: linea.estado_cesion == 'Pendiente Coordinador'
            ))
            wizard.cantidad_con_orden = len(lineas.filtered('orden_de_pago'))
            wizard.cantidad_sin_orden = len(lineas.filtered(lambda linea: not linea.orden_de_pago))
            wizard.cantidad_orden_sin_fecha = len(lineas.filtered(
                lambda linea: linea.estado_programacion_pago
                == 'Orden de pago cargada - pendiente fecha tentativa de pago'
            ))
            wizard.valor_promedio_resultados = (
                wizard.valor_total_resultados / len(lineas) if lineas else 0.0
            )
            duraciones = [
                valor for valor in lineas.mapped('dias_total_proceso') if valor
            ]
            wizard.promedio_dias_proceso = (
                sum(duraciones) / len(duraciones) if duraciones else 0.0
            )
            elaboraciones = [
                wizard._horas_entre(linea.create_date_origen, linea.fecha_solicitud) / 24.0
                for linea in lineas
                if linea.create_date_origen and linea.fecha_solicitud
            ]
            wizard.promedio_dias_elaboracion = (
                sum(elaboraciones) / len(elaboraciones) if elaboraciones else 0.0
            )

            def promedio_horas(campo, inicio, fin):
                valores = [
                    linea[campo]
                    for linea in lineas
                    if linea[inicio] and linea[fin]
                ]
                return sum(valores) / len(valores) if valores else 0.0

            wizard.promedio_horas_asis_coordinador = promedio_horas(
                'horas_asis_a_coordinador', 'fecha_envio_asis_coordinador', 'fecha_envio_coordinador'
            )
            wizard.promedio_horas_coordinador_ventanilla = promedio_horas(
                'horas_coordinador_a_ventanilla', 'fecha_envio_coordinador', 'fecha_envio_ventanilla'
            )
            wizard.promedio_horas_ventanilla_director = promedio_horas(
                'horas_ventanilla_a_director', 'fecha_envio_ventanilla', 'fecha_envio_director_financiero'
            )
            wizard.promedio_horas_director_contabilidad = promedio_horas(
                'horas_director_a_contabilidad', 'fecha_envio_director_financiero', 'fecha_envio_contabilidad'
            )
            wizard.promedio_horas_contabilidad_auxiliar = promedio_horas(
                'horas_contabilidad_a_auxiliar', 'fecha_envio_contabilidad', 'fecha_envio_auxiliar_tesoreria'
            )
            wizard.promedio_horas_auxiliar_tesoreria = promedio_horas(
                'horas_auxiliar_a_tesoreria', 'fecha_envio_auxiliar_tesoreria', 'fecha_envio_tesoreria'
            )
            wizard.promedio_horas_tesoreria_final = promedio_horas(
                'horas_tesoreria_a_final', 'fecha_envio_tesoreria', 'fecha_finalizacion'
            )

    def _domain_reporte(self):
        self.ensure_one()
        domain = []
        if self.estado:
            domain.append(('estado', '=', self.estado))
        if self.clase_persona:
            domain.append(('clase_persona', '=', self.clase_persona))
        if self.origen_cuenta:
            domain.append(('origen_cuenta', '=', self.origen_cuenta))
        if self.tipo_cuenta:
            domain.append(('selecionar', '=', self.tipo_cuenta))
        if self.contratista:
            domain.append(('nombre_contratista', 'ilike', self.contratista))
        if self.documento_nit:
            domain.append(('documento_de_identificacion', 'ilike', self.documento_nit))
        if self.proyecto:
            domain.append(('proyecto_nombre', 'ilike', self.proyecto))
        if self.pago:
            domain.append(('pago', '=', self.pago))
        if self.numero_factura:
            domain.append(('numero_factura', 'ilike', self.numero_factura))
        if self.proyecto_filtro == 'con':
            domain.append(('proyecto_relacionado', '!=', False))
        elif self.proyecto_filtro == 'sin':
            domain.append(('proyecto_relacionado', '=', False))
        if self.factura_filtro == 'con':
            domain.append(('numero_factura', 'not in', [False, '', 'N/A', 'No aplica']))
        elif self.factura_filtro == 'sin':
            domain += [
                '|', '|',
                ('numero_factura', '=', False),
                ('numero_factura', '=', ''),
                ('numero_factura', 'in', ['N/A', 'No aplica']),
            ]
        if self.pendiente_firma == 'coordinador':
            domain.append(('pendiente_firma_coordinador', '=', True))
            domain.append(('usuarios_contratacion', 'in', [self.env.user.id]))
        elif self.pendiente_firma == 'director':
            domain.append(('pendiente_firma_director_financiero', '=', True))
        if self.seguimiento_correccion:
            domain.append(('seguimiento_correccion_reporte', 'ilike', self.seguimiento_correccion))
        if self.correccion_en_manos_de:
            domain.append(('correccion_reporte_en_manos_de', 'ilike', self.correccion_en_manos_de))
        if self.estado_cesion:
            domain.append(('estado_cesion_derechos', '=', self.estado_cesion))
        if self.orden_pago_filtro == 'con':
            domain.append(('orden_de_pago_id', '!=', False))
        elif self.orden_pago_filtro == 'sin':
            domain.append(('orden_de_pago_id', '=', False))
        if self.orden_de_pago_id:
            domain.append(('orden_de_pago_id', '=', self.orden_de_pago_id.id))
        if self.estado_programacion_pago:
            domain.append(('estado_programacion_pago', '=', self.estado_programacion_pago))
        if self.responsable_id:
            campos_responsable = [
                'usuarios_contratacion',
                'usuarios_supervision',
                'usuarios_contabilidad',
                'usuarios_auxiliar_tesoreria',
                'usuarios_tesoreria',
            ]
            if self.etapa_responsable and self.etapa_responsable != 'cualquiera':
                domain.append((self.etapa_responsable, 'in', [self.responsable_id.id]))
            else:
                domain += ['|'] * (len(campos_responsable) - 1)
                domain += [
                    (campo, 'in', [self.responsable_id.id])
                    for campo in campos_responsable
                ]
        if self.buscar_texto:
            domain += [
                '|', '|',
                ('nombre_contratista', 'ilike', self.buscar_texto),
                ('documento_de_identificacion', 'ilike', self.buscar_texto),
                ('proyecto_nombre', 'ilike', self.buscar_texto),
            ]

        if self.alcance == 'en_proceso':
            domain.append(('estado', 'not in', ['finalizado', 'rechazado']))
        elif self.alcance == 'finalizadas':
            domain.append(('estado', '=', 'finalizado'))
        elif self.alcance == 'rechazadas':
            domain.append(('estado', '=', 'rechazado'))
        elif self.alcance == 'correccion':
            domain += ['|', ('correccion_portal_activa', '=', True), ('correccion_reporte_activa', '=', True)]
        elif self.alcance == 'cesion':
            domain.append(('cesion_derechos', '=', True))

        campo_fecha = self.tipo_fecha
        if self.fecha_desde:
            domain.append((campo_fecha, '>=', datetime.combine(self.fecha_desde, dt_time.min)))
        if self.fecha_hasta:
            domain.append((campo_fecha, '<=', datetime.combine(self.fecha_hasta, dt_time.max)))
        return domain

    @api.model
    def _valor_fecha(self, record, field_name):
        if not field_name or field_name not in record._fields:
            return False
        return record[field_name]

    @api.model
    def _horas_entre(self, inicio, fin):
        if not inicio or not fin:
            return 0.0
        if not isinstance(inicio, datetime):
            inicio = datetime.combine(inicio, dt_time.min)
        if not isinstance(fin, datetime):
            fin = datetime.combine(fin, dt_time.min)
        segundos = (fin - inicio).total_seconds()
        return max(segundos / 3600.0, 0.0)

    @api.model
    def _selection_label(self, record, field_name):
        field = record._fields.get(field_name)
        value = record[field_name] if field_name in record._fields else False
        if not value:
            return ''
        if not field or field.type != 'selection':
            return str(value)
        selection = field.selection(record) if callable(field.selection) else field.selection
        return dict(selection or []).get(value, value)

    @api.model
    def _nombre_usuario_m2m(self, record, field_name):
        if field_name not in record._fields:
            return ''
        usuarios = record[field_name]
        return ', '.join(usuarios.mapped('name')) if usuarios else ''

    def _vals_linea(self, acta):
        self.ensure_one()
        f = lambda name: self._valor_fecha(acta, name)
        h = self._horas_entre

        fecha_inicio = f(self.etapa_desde)
        fecha_fin = f(self.etapa_hasta)
        horas_seleccionadas = h(fecha_inicio, fecha_fin)
        horas_total = h(acta.create_date, acta.fecha_finalizacion)
        horas_estado_actual = h(
            acta.fecha_estado_actual,
            acta.fecha_finalizacion or acta.fecha_rechazo or fields.Datetime.now(),
        )
        horas_correccion = h(
            acta.correccion_reporte_fecha_solicitud,
            acta.correccion_reporte_fecha_cierre or (
                fields.Datetime.now() if acta.correccion_reporte_activa else False
            ),
        )

        return {
            'wizard_id': self.id,
            'acta_id_ref': acta.id,
            'cantidad': 1,
            'nombre_contratista': acta.nombre_contratista or acta.name or '',
            'documento_de_identificacion': acta.documento_de_identificacion or '',
            'clase_persona': self._selection_label(acta, 'clase_persona'),
            'proyecto': acta.proyecto_nombre or '',
            'tipo_cuenta': self._selection_label(acta, 'selecionar'),
            'origen_cuenta': self._selection_label(acta, 'origen_cuenta'),
            'pago': acta.pago or 0,
            'numero_factura': acta.numero_factura or '',
            'tiene_proyecto': bool(acta.proyecto_relacionado),
            'tiene_factura': bool(
                acta.numero_factura
                and acta.numero_factura not in ('N/A', 'No aplica')
            ),
            'valor_cuenta': acta.valor_cuenta or 0.0,
            'estado': self._selection_label(acta, 'estado'),
            'estado_visible': acta.estado_kanban_display or self._selection_label(acta, 'estado'),
            'create_date_origen': acta.create_date,
            'fecha_solicitud': acta.fecha_solicitud,
            'fecha_estado_actual': acta.fecha_estado_actual,
            'fecha_envio_asis_coordinador': acta.fecha_envio_asis_coordinador,
            'fecha_envio_coordinador': acta.fecha_envio_coordinador,
            'fecha_envio_ventanilla': acta.fecha_envio_ventanilla,
            'fecha_envio_director_financiero': acta.fecha_envio_director_financiero,
            'fecha_firma_director_financiero': acta.fecha_firma_director_financiero,
            'fecha_envio_contabilidad': acta.fecha_envio_contabilidad,
            'fecha_envio_auxiliar_tesoreria': acta.fecha_envio_auxiliar_tesoreria,
            'fecha_envio_tesoreria': acta.fecha_envio_tesoreria,
            'fecha_finalizacion': acta.fecha_finalizacion,
            'fecha_rechazo': acta.fecha_rechazo,
            'motivo_rechazo': acta.motivo_rechazo or '',
            'horas_duracion_seleccionada': horas_seleccionadas,
            'dias_duracion_seleccionada': horas_seleccionadas / 24.0,
            'horas_total_proceso': horas_total,
            'dias_total_proceso': horas_total / 24.0,
            'horas_estado_actual': horas_estado_actual,
            'horas_asis_a_coordinador': h(acta.fecha_envio_asis_coordinador, acta.fecha_envio_coordinador),
            'horas_coordinador_a_ventanilla': h(acta.fecha_envio_coordinador, acta.fecha_envio_ventanilla),
            'horas_ventanilla_a_director': h(acta.fecha_envio_ventanilla, acta.fecha_envio_director_financiero),
            'horas_director_a_contabilidad': h(acta.fecha_envio_director_financiero, acta.fecha_envio_contabilidad),
            'horas_contabilidad_a_auxiliar': h(acta.fecha_envio_contabilidad, acta.fecha_envio_auxiliar_tesoreria),
            'horas_auxiliar_a_tesoreria': h(acta.fecha_envio_auxiliar_tesoreria, acta.fecha_envio_tesoreria),
            'horas_tesoreria_a_final': h(acta.fecha_envio_tesoreria, acta.fecha_finalizacion),
            'correccion_portal_activa': bool(acta.correccion_portal_activa),
            'correccion_reporte_activa': bool(acta.correccion_reporte_activa),
            'seguimiento_correccion_reporte': acta.seguimiento_correccion_reporte or '',
            'correccion_reporte_en_manos_de': acta.correccion_reporte_en_manos_de or '',
            'correccion_origen': self._selection_label(acta, 'correccion_reporte_origen'),
            'correccion_fecha_solicitud': acta.correccion_reporte_fecha_solicitud,
            'correccion_fecha_cierre': acta.correccion_reporte_fecha_cierre,
            'horas_correccion': horas_correccion,
            'cesion_derechos': bool(acta.cesion_derechos),
            'estado_cesion': self._selection_label(acta, 'estado_cesion_derechos'),
            'fecha_solicitud_cesion': acta.fecha_solicitud_cesion_derechos,
            'pendiente_firma_coordinador': bool(acta.pendiente_firma_coordinador),
            'mis_pendientes_firma': bool(
                acta.pendiente_firma_coordinador
                and self.env.user in acta.usuarios_contratacion
            ),
            'pendiente_firma_director_financiero': bool(acta.pendiente_firma_director_financiero),
            'orden_de_pago': acta.orden_de_pago_id.display_name if acta.orden_de_pago_id else '',
            'estado_programacion_pago': acta.estado_programacion_pago or '',
            'asignado_coordinador': self._nombre_usuario_m2m(acta, 'usuarios_contratacion'),
            'asignado_ventanilla': self._nombre_usuario_m2m(acta, 'usuarios_supervision'),
            'asignado_contabilidad': self._nombre_usuario_m2m(acta, 'usuarios_contabilidad'),
            'asignado_auxiliar_tesoreria': self._nombre_usuario_m2m(acta, 'usuarios_auxiliar_tesoreria'),
            'asignado_tesoreria': self._nombre_usuario_m2m(acta, 'usuarios_tesoreria'),
        }

    def _validar_filtros(self):
        self.ensure_one()
        if self.fecha_desde and self.fecha_hasta and self.fecha_desde > self.fecha_hasta:
            raise UserError(_('La fecha Desde no puede ser posterior a la fecha Hasta.'))
        if self.etapa_desde == self.etapa_hasta:
            raise UserError(_('Seleccione dos etapas diferentes para medir la duración.'))

    def _orden_resultados(self):
        self.ensure_one()
        if self.orden in ('mayor_duracion', 'menor_duracion'):
            return 'horas_duracion_seleccionada %s, id desc' % (
                'desc' if self.orden == 'mayor_duracion' else 'asc'
            )
        if self.orden == 'creacion_reciente':
            return 'create_date_origen desc, id desc'
        if self.orden == 'creacion_antigua':
            return 'create_date_origen asc, id asc'
        if self.orden == 'director_reciente':
            return 'fecha_envio_director_financiero desc, id desc'
        if self.orden == 'director_antiguo':
            return 'fecha_envio_director_financiero asc, id asc'
        return 'fecha_estado_actual desc, id desc'

    def _generar_lineas_reporte(self):
        self.ensure_one()
        self._validar_filtros()

        Linea = self.env['acta.reporte.cuentas.line'].sudo()
        Linea.search([('wizard_id', '=', self.id)]).unlink()

        actas = self.env['acta_entrega_documentos'].sudo().search(
            self._domain_reporte(),
            order='fecha_estado_actual desc, id desc',
        )
        if not actas:
            raise UserError(_('No se encontraron cuentas de cobro con los filtros seleccionados.'))

        vals = [self._vals_linea(acta) for acta in actas]
        Linea.create(vals)
        return Linea.search(
            [('wizard_id', '=', self.id)],
            order=self._orden_resultados(),
        )

    def action_generar_reporte(self):
        self.ensure_one()
        self._generar_lineas_reporte()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_filtrar_resumen(self):
        self.ensure_one()
        filtro = self.env.context.get('filtro_resumen_objetivo', 'todas')
        filtros_validos = dict(self._fields['filtro_resumen'].selection)
        activos = self._filtros_resumen_lista()
        if filtro == 'todas':
            activos = []
        elif filtro in filtros_validos:
            if filtro in activos:
                activos.remove(filtro)
            else:
                activos.append(filtro)
        self.write({
            'filtro_resumen': filtro if filtro in filtros_validos else 'todas',
            'filtros_resumen_activos': ','.join(activos),
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_resumen_sin_navegacion(self):
        """Mantiene las tarjetas de resumen informativas sin abrir otras vistas."""
        self.ensure_one()
        return False

    def action_ver_analisis(self):
        self.ensure_one()
        lineas = self.line_ids or self._generar_lineas_reporte()

        action = self.env.ref('Matriz.action_acta_reporte_cuentas_resultado').read()[0]
        action['domain'] = [('wizard_id', '=', self.id)]
        action['context'] = {
            'create': False,
            'edit': False,
            'delete': False,
            'reporte_wizard_id': self.id,
            'search_default_group_estado': 0,
        }
        action['name'] = 'Informes de cuentas de cobro'
        action['order'] = self._orden_resultados()
        return action

    def action_ver_todas(self):
        self.ensure_one()
        self.write({
            'alcance': 'todas',
            'estado': False,
            'clase_persona': False,
            'origen_cuenta': False,
            'tipo_cuenta': False,
            'buscar_texto': False,
            'contratista': False,
            'documento_nit': False,
            'proyecto': False,
            'pago': 0,
            'numero_factura': False,
            'proyecto_filtro': False,
            'factura_filtro': False,
            'pendiente_firma': False,
            'seguimiento_correccion': False,
            'correccion_en_manos_de': False,
            'estado_cesion': False,
            'orden_pago_filtro': False,
            'orden_de_pago_id': False,
            'estado_programacion_pago': False,
            'responsable_id': False,
            'etapa_responsable': 'cualquiera',
            'filtro_resumen': 'todas',
            'filtros_resumen_activos': '',
            'fecha_desde': False,
            'fecha_hasta': False,
        })
        self._generar_lineas_reporte()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_limpiar_filtros(self):
        self.ensure_one()
        self.write({
            'alcance': 'todas',
            'estado': False,
            'clase_persona': False,
            'origen_cuenta': False,
            'tipo_cuenta': False,
            'buscar_texto': False,
            'contratista': False,
            'documento_nit': False,
            'proyecto': False,
            'pago': 0,
            'numero_factura': False,
            'proyecto_filtro': False,
            'factura_filtro': False,
            'pendiente_firma': False,
            'seguimiento_correccion': False,
            'correccion_en_manos_de': False,
            'estado_cesion': False,
            'orden_pago_filtro': False,
            'orden_de_pago_id': False,
            'estado_programacion_pago': False,
            'responsable_id': False,
            'etapa_responsable': 'cualquiera',
            'filtro_resumen': 'todas',
            'filtros_resumen_activos': '',
            'tipo_fecha': 'create_date',
            'fecha_desde': False,
            'fecha_hasta': False,
            'etapa_desde': 'fecha_solicitud',
            'etapa_hasta': 'fecha_finalizacion',
            'orden': 'ultimo_movimiento',
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _crear_descarga(self, nombre, contenido, mimetype):
        self.ensure_one()
        adjunto = self.env['ir.attachment'].sudo()._drive_create_temp_download({
            'name': nombre,
            'datas': base64.b64encode(contenido),
            'mimetype': mimetype,
            'res_model': self._name,
            'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % adjunto.id,
            'target': 'self',
        }

    def action_descargar_excel(self):
        self.ensure_one()
        self._generar_lineas_reporte()
        lineas = self._aplicar_filtros_resumen(self.line_ids)
        salida = BytesIO()
        libro = xlsxwriter.Workbook(salida, {'in_memory': True})
        hoja = libro.add_worksheet('Cuentas de cobro')
        titulo = libro.add_format({
            'bold': True, 'font_size': 16, 'font_color': '#043484',
        })
        encabezado = libro.add_format({
            'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#043484',
            'border': 1, 'align': 'center', 'valign': 'vcenter',
        })
        texto = libro.add_format({'border': 1, 'valign': 'top'})
        numero = libro.add_format({'border': 1, 'num_format': '#,##0.00'})
        fecha = libro.add_format({'border': 1, 'num_format': 'dd/mm/yyyy hh:mm'})

        columnas = [
            ('ID cuenta', 'acta_id_ref', 'number', 12),
            ('Contratista', 'nombre_contratista', 'text', 28),
            ('Documento/NIT', 'documento_de_identificacion', 'text', 18),
            ('Tipo de persona', 'clase_persona', 'text', 18),
            ('Proyecto', 'proyecto', 'text', 30),
            ('Origen', 'origen_cuenta', 'text', 22),
            ('Tipo de cuenta', 'tipo_cuenta', 'text', 25),
            ('Pago N°', 'pago', 'number', 10),
            ('Valor cuenta/factura', 'valor_cuenta', 'number', 20),
            ('Estado', 'estado', 'text', 22),
            ('Estado visible', 'estado_visible', 'text', 22),
            ('Fecha creación', 'create_date_origen', 'date', 20),
            ('Fecha radicación', 'fecha_solicitud', 'date', 20),
            ('Último movimiento', 'fecha_estado_actual', 'date', 20),
            ('Envío Asistente coordinación', 'fecha_envio_asis_coordinador', 'date', 22),
            ('Envío Coordinador', 'fecha_envio_coordinador', 'date', 20),
            ('Envío Ventanilla', 'fecha_envio_ventanilla', 'date', 20),
            ('Envío Director Financiero', 'fecha_envio_director_financiero', 'date', 22),
            ('Firma Director Financiero', 'fecha_firma_director_financiero', 'date', 22),
            ('Envío Contabilidad', 'fecha_envio_contabilidad', 'date', 20),
            ('Envío Auxiliar tesorería', 'fecha_envio_auxiliar_tesoreria', 'date', 22),
            ('Envío Tesorería', 'fecha_envio_tesoreria', 'date', 20),
            ('Finalización', 'fecha_finalizacion', 'date', 20),
            ('Fecha rechazo', 'fecha_rechazo', 'date', 20),
            ('Motivo rechazo', 'motivo_rechazo', 'text', 35),
            ('Horas entre etapas', 'horas_duracion_seleccionada', 'number', 18),
            ('Días entre etapas', 'dias_duracion_seleccionada', 'number', 18),
            ('Horas proceso total', 'horas_total_proceso', 'number', 20),
            ('Días proceso total', 'dias_total_proceso', 'number', 18),
            ('Horas estado actual', 'horas_estado_actual', 'number', 20),
            ('Horas Asistente → Coordinador', 'horas_asis_a_coordinador', 'number', 23),
            ('Horas Coordinador → Ventanilla', 'horas_coordinador_a_ventanilla', 'number', 23),
            ('Horas Ventanilla → Director', 'horas_ventanilla_a_director', 'number', 23),
            ('Horas Director → Contabilidad', 'horas_director_a_contabilidad', 'number', 23),
            ('Horas Contabilidad → Auxiliar', 'horas_contabilidad_a_auxiliar', 'number', 23),
            ('Horas Auxiliar → Tesorería', 'horas_auxiliar_a_tesoreria', 'number', 23),
            ('Horas Tesorería → Finalizado', 'horas_tesoreria_a_final', 'number', 23),
            ('Corrigiendo en portal', 'correccion_portal_activa', 'boolean', 18),
            ('Corrección interna', 'correccion_reporte_activa', 'boolean', 18),
            ('Corrección solicitada por', 'correccion_origen', 'text', 24),
            ('Inicio corrección', 'correccion_fecha_solicitud', 'date', 20),
            ('Fin corrección', 'correccion_fecha_cierre', 'date', 20),
            ('Horas en corrección', 'horas_correccion', 'number', 18),
            ('Cesión de derechos', 'cesion_derechos', 'boolean', 18),
            ('Estado cesión', 'estado_cesion', 'text', 20),
            ('Fecha solicitud cesión', 'fecha_solicitud_cesion', 'date', 22),
            ('Asignado coordinador', 'asignado_coordinador', 'text', 25),
            ('Asignado ventanilla', 'asignado_ventanilla', 'text', 25),
            ('Asignado contabilidad', 'asignado_contabilidad', 'text', 25),
            ('Asignado auxiliar tesorería', 'asignado_auxiliar_tesoreria', 'text', 25),
            ('Asignado tesorería', 'asignado_tesoreria', 'text', 25),
        ]
        hoja.merge_range(
            0, 0, 0, len(columnas) - 1,
            'Informe detallado de cuentas de cobro',
            titulo,
        )
        hoja.write('A2', 'Registros encontrados: %s' % len(lineas), texto)
        hoja.merge_range(
            2, 0, 2, len(columnas) - 1,
            'Filtros aplicados: %s' % (
                self.filtros_resumen_descripcion or 'Ninguno (todas las cuentas)'
            ),
            texto,
        )
        fila_encabezado = 3
        for columna, (label, _campo, _tipo, ancho) in enumerate(columnas):
            hoja.write(fila_encabezado, columna, label, encabezado)
            hoja.set_column(columna, columna, ancho)

        for fila, linea in enumerate(lineas, start=fila_encabezado + 1):
            for columna, (_label, campo, tipo, _ancho) in enumerate(columnas):
                valor = linea[campo]
                if tipo == 'date' and valor:
                    hoja.write_datetime(fila, columna, valor, fecha)
                elif tipo == 'number':
                    hoja.write_number(fila, columna, float(valor or 0.0), numero)
                elif tipo == 'boolean':
                    hoja.write(fila, columna, 'Sí' if valor else 'No', texto)
                else:
                    hoja.write(fila, columna, valor or '', texto)

        hoja.freeze_panes(fila_encabezado + 1, 0)
        hoja.autofilter(fila_encabezado, 0, fila_encabezado + len(lineas), len(columnas) - 1)
        libro.close()
        nombre = 'Informe_cuentas_cobro_%s.xlsx' % fields.Date.today().strftime('%Y%m%d')
        return self._crear_descarga(
            nombre,
            salida.getvalue(),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def action_descargar_pdf(self):
        self.ensure_one()
        lineas = self._generar_lineas_reporte()

        def fecha_corta(valor):
            return fields.Datetime.to_string(valor)[:10] if valor else ''

        salida = BytesIO()
        estilos = getSampleStyleSheet()
        documento = SimpleDocTemplate(
            salida,
            pagesize=landscape(letter),
            rightMargin=10 * mm,
            leftMargin=10 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
            title='Informe de cuentas de cobro',
        )
        elementos = [
            Paragraph('Informe de cuentas de cobro', estilos['Title']),
            Paragraph('Registros encontrados: %s' % len(lineas), estilos['Normal']),
            Spacer(1, 5 * mm),
        ]
        datos = [[
            'Contratista', 'Documento/NIT', 'Proyecto', 'Tipo', 'Valor',
            'Estado', 'Creación', 'Radicación', 'Finalización', 'Días proceso',
        ]]
        for linea in lineas:
            datos.append([
                linea.nombre_contratista or '',
                linea.documento_de_identificacion or '',
                linea.proyecto or '',
                linea.tipo_cuenta or '',
                '{:,.2f}'.format(linea.valor_cuenta or 0.0),
                linea.estado or '',
                fecha_corta(linea.create_date_origen),
                fecha_corta(linea.fecha_solicitud),
                fecha_corta(linea.fecha_finalizacion),
                '{:.2f}'.format(linea.dias_total_proceso or 0.0),
            ])
        tabla = Table(
            datos,
            repeatRows=1,
            colWidths=[32 * mm, 23 * mm, 38 * mm, 25 * mm, 23 * mm,
                       24 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm],
        )
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#043484')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#B8C7DB')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#EEF6FF')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ]))
        elementos.append(tabla)

        estilo_detalle = estilos['BodyText'].clone('DetalleCuenta')
        estilo_detalle.fontSize = 7
        estilo_detalle.leading = 9

        def celda(valor):
            if isinstance(valor, bool):
                valor = 'Sí' if valor else 'No'
            return Paragraph(html_escape(str(valor or '')), estilo_detalle)

        for indice, linea in enumerate(lineas, start=1):
            elementos.append(PageBreak())
            elementos.append(Paragraph(
                '%s. %s — %s' % (
                    indice,
                    html_escape(linea.nombre_contratista or 'Cuenta de cobro'),
                    html_escape(linea.documento_de_identificacion or ''),
                ),
                estilos['Heading2'],
            ))
            detalle = [
                ['Proyecto', linea.proyecto, 'Origen', linea.origen_cuenta],
                ['Tipo de persona', linea.clase_persona, 'Tipo de cuenta', linea.tipo_cuenta],
                ['Pago N°', linea.pago, 'Valor', '{:,.2f}'.format(linea.valor_cuenta or 0.0)],
                ['Estado', linea.estado, 'Último movimiento', fecha_corta(linea.fecha_estado_actual)],
                ['Creación', fecha_corta(linea.create_date_origen), 'Radicación', fecha_corta(linea.fecha_solicitud)],
                ['Envío Asistente', fecha_corta(linea.fecha_envio_asis_coordinador), 'Envío Coordinador', fecha_corta(linea.fecha_envio_coordinador)],
                ['Envío Ventanilla', fecha_corta(linea.fecha_envio_ventanilla), 'Envío Director financiero', fecha_corta(linea.fecha_envio_director_financiero)],
                ['Firma Director', fecha_corta(linea.fecha_firma_director_financiero), 'Envío Contabilidad', fecha_corta(linea.fecha_envio_contabilidad)],
                ['Envío Auxiliar tesorería', fecha_corta(linea.fecha_envio_auxiliar_tesoreria), 'Envío Tesorería', fecha_corta(linea.fecha_envio_tesoreria)],
                ['Finalización', fecha_corta(linea.fecha_finalizacion), 'Rechazo', fecha_corta(linea.fecha_rechazo)],
                ['Motivo rechazo', linea.motivo_rechazo, 'Horas estado actual', '{:.2f}'.format(linea.horas_estado_actual or 0.0)],
                ['Horas entre etapas', '{:.2f}'.format(linea.horas_duracion_seleccionada or 0.0), 'Días entre etapas', '{:.2f}'.format(linea.dias_duracion_seleccionada or 0.0)],
                ['Horas proceso total', '{:.2f}'.format(linea.horas_total_proceso or 0.0), 'Días proceso total', '{:.2f}'.format(linea.dias_total_proceso or 0.0)],
                ['Asistente → Coordinador', '{:.2f} h'.format(linea.horas_asis_a_coordinador or 0.0), 'Coordinador → Ventanilla', '{:.2f} h'.format(linea.horas_coordinador_a_ventanilla or 0.0)],
                ['Ventanilla → Director', '{:.2f} h'.format(linea.horas_ventanilla_a_director or 0.0), 'Director → Contabilidad', '{:.2f} h'.format(linea.horas_director_a_contabilidad or 0.0)],
                ['Contabilidad → Auxiliar', '{:.2f} h'.format(linea.horas_contabilidad_a_auxiliar or 0.0), 'Auxiliar → Tesorería', '{:.2f} h'.format(linea.horas_auxiliar_a_tesoreria or 0.0)],
                ['Tesorería → Finalizado', '{:.2f} h'.format(linea.horas_tesoreria_a_final or 0.0), 'Horas en corrección', '{:.2f}'.format(linea.horas_correccion or 0.0)],
                ['Corrección portal', linea.correccion_portal_activa, 'Corrección interna', linea.correccion_reporte_activa],
                ['Corrección solicitada por', linea.correccion_origen, 'Inicio corrección', fecha_corta(linea.correccion_fecha_solicitud)],
                ['Fin corrección', fecha_corta(linea.correccion_fecha_cierre), 'Cesión de derechos', linea.cesion_derechos],
                ['Estado cesión', linea.estado_cesion, 'Fecha solicitud cesión', fecha_corta(linea.fecha_solicitud_cesion)],
                ['Coordinador asignado', linea.asignado_coordinador, 'Ventanilla asignada', linea.asignado_ventanilla],
                ['Contabilidad asignada', linea.asignado_contabilidad, 'Auxiliar tesorería', linea.asignado_auxiliar_tesoreria],
                ['Tesorería asignada', linea.asignado_tesoreria, 'ID cuenta', linea.acta_id_ref],
            ]
            detalle = [[celda(valor) for valor in fila] for fila in detalle]
            tabla_detalle = Table(
                detalle,
                colWidths=[38 * mm, 80 * mm, 38 * mm, 80 * mm],
                repeatRows=0,
            )
            tabla_detalle.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#DDEBFF')),
                ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#DDEBFF')),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#B8C7DB')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            elementos.append(tabla_detalle)
        documento.build(elementos)
        nombre = 'Informe_cuentas_cobro_%s.pdf' % fields.Date.today().strftime('%Y%m%d')
        return self._crear_descarga(nombre, salida.getvalue(), 'application/pdf')


class ActaReporteCuentasLine(models.Model):
    _name = 'acta.reporte.cuentas.line'
    _description = 'Resultado de reporte de cuentas de cobro'
    _order = 'fecha_estado_actual desc, id desc'

    wizard_id = fields.Many2one('acta.reporte.cuentas.wizard', required=True, ondelete='cascade', index=True)
    acta_id_ref = fields.Integer(string='ID cuenta', readonly=True)
    cantidad = fields.Integer(string='Cantidad', default=1, readonly=True)

    nombre_contratista = fields.Char(string='Contratista', readonly=True)
    documento_de_identificacion = fields.Char(string='Documento/NIT', readonly=True)
    clase_persona = fields.Char(string='Tipo de persona', readonly=True)
    proyecto = fields.Char(string='Proyecto', readonly=True)
    tipo_cuenta = fields.Char(string='Tipo', readonly=True)
    origen_cuenta = fields.Char(string='Origen', readonly=True)
    pago = fields.Integer(string='Pago N°', readonly=True)
    numero_factura = fields.Char(string='Factura', readonly=True)
    tiene_proyecto = fields.Boolean(string='Con proyecto', readonly=True)
    tiene_factura = fields.Boolean(string='Con factura', readonly=True)
    valor_cuenta = fields.Float(string='Valor cuenta/factura', readonly=True)
    estado = fields.Char(string='Estado', readonly=True)
    estado_visible = fields.Char(string='Estado visible', readonly=True)

    create_date_origen = fields.Datetime(string='Fecha creación', readonly=True)
    fecha_solicitud = fields.Datetime(string='Fecha radicación', readonly=True)
    fecha_estado_actual = fields.Datetime(string='Último movimiento', readonly=True)
    fecha_envio_asis_coordinador = fields.Datetime(string='Asistente coordinación', readonly=True)
    fecha_envio_coordinador = fields.Datetime(string='Coordinador', readonly=True)
    fecha_envio_ventanilla = fields.Datetime(string='Ventanilla', readonly=True)
    fecha_envio_director_financiero = fields.Datetime(string='Director Financiero', readonly=True)
    fecha_firma_director_financiero = fields.Date(string='Firma Director Financiero', readonly=True)
    fecha_envio_contabilidad = fields.Datetime(string='Contabilidad', readonly=True)
    fecha_envio_auxiliar_tesoreria = fields.Datetime(string='Auxiliar tesorería', readonly=True)
    fecha_envio_tesoreria = fields.Datetime(string='Tesorería', readonly=True)
    fecha_finalizacion = fields.Datetime(string='Finalización', readonly=True)
    fecha_rechazo = fields.Datetime(string='Rechazo', readonly=True)
    motivo_rechazo = fields.Text(string='Motivo rechazo', readonly=True)

    horas_duracion_seleccionada = fields.Float(string='Horas entre etapas', digits=(16, 2), readonly=True)
    dias_duracion_seleccionada = fields.Float(string='Días entre etapas', digits=(16, 2), readonly=True)
    horas_total_proceso = fields.Float(string='Horas proceso total', digits=(16, 2), readonly=True)
    dias_total_proceso = fields.Float(string='Días proceso total', digits=(16, 2), readonly=True)
    horas_estado_actual = fields.Float(string='Horas en estado actual', digits=(16, 2), readonly=True)
    horas_asis_a_coordinador = fields.Float(string='Horas Asistente → Coordinador', digits=(16, 2), readonly=True)
    horas_coordinador_a_ventanilla = fields.Float(string='Horas Coordinador → Ventanilla', digits=(16, 2), readonly=True)
    horas_ventanilla_a_director = fields.Float(string='Horas Ventanilla → Director', digits=(16, 2), readonly=True)
    horas_director_a_contabilidad = fields.Float(string='Horas Director → Contabilidad', digits=(16, 2), readonly=True)
    horas_contabilidad_a_auxiliar = fields.Float(string='Horas Contabilidad → Auxiliar', digits=(16, 2), readonly=True)
    horas_auxiliar_a_tesoreria = fields.Float(string='Horas Auxiliar → Tesorería', digits=(16, 2), readonly=True)
    horas_tesoreria_a_final = fields.Float(string='Horas Tesorería → Finalizado', digits=(16, 2), readonly=True)

    correccion_portal_activa = fields.Boolean(string='Corrigiendo en portal', readonly=True)
    correccion_reporte_activa = fields.Boolean(string='Corrección interna', readonly=True)
    seguimiento_correccion_reporte = fields.Char(string='Seguimiento corrección', readonly=True)
    correccion_reporte_en_manos_de = fields.Char(string='Corrección en manos de', readonly=True)
    correccion_origen = fields.Char(string='Corrección solicitada por', readonly=True)
    correccion_fecha_solicitud = fields.Datetime(string='Inicio corrección', readonly=True)
    correccion_fecha_cierre = fields.Datetime(string='Fin corrección', readonly=True)
    horas_correccion = fields.Float(string='Horas en corrección', digits=(16, 2), readonly=True)

    cesion_derechos = fields.Boolean(string='Cesión de derechos', readonly=True)
    estado_cesion = fields.Char(string='Estado cesión', readonly=True)
    fecha_solicitud_cesion = fields.Datetime(string='Fecha solicitud cesión', readonly=True)

    pendiente_firma_coordinador = fields.Boolean(string='Pendiente firma Coordinador', readonly=True)
    mis_pendientes_firma = fields.Boolean(string='Mis pendientes de firma', readonly=True)
    pendiente_firma_director_financiero = fields.Boolean(string='Pendiente firma Director Financiero', readonly=True)
    orden_de_pago = fields.Char(string='Orden de pago', readonly=True)
    estado_programacion_pago = fields.Char(string='Estado programación de pago', readonly=True)

    asignado_coordinador = fields.Char(string='Asignado coordinador', readonly=True)
    asignado_ventanilla = fields.Char(string='Asignado ventanilla', readonly=True)
    asignado_contabilidad = fields.Char(string='Asignado contabilidad', readonly=True)
    asignado_auxiliar_tesoreria = fields.Char(string='Asignado auxiliar tesorería', readonly=True)
    asignado_tesoreria = fields.Char(string='Asignado tesorería', readonly=True)

    def _wizard_reporte_actual(self):
        wizard_id = self.env.context.get('reporte_wizard_id')
        wizard = self.env['acta.reporte.cuentas.wizard'].browse(wizard_id).exists()
        if not wizard and self:
            wizard = self[:1].wizard_id
        if not wizard:
            raise UserError(_('No se encontró el tablero del informe. Vuelva a ingresar desde el menú Informes de cuentas.'))
        return wizard

    def action_actualizar_informe(self):
        wizard = self._wizard_reporte_actual()
        wizard._generar_lineas_reporte()
        return wizard.action_ver_analisis()

    def action_descargar_excel_informe(self):
        return self._wizard_reporte_actual().action_descargar_excel()

    def action_descargar_pdf_informe(self):
        return self._wizard_reporte_actual().action_descargar_pdf()

    def action_ver_todas_informe(self):
        wizard = self._wizard_reporte_actual()
        wizard.action_ver_todas()
        return wizard.action_ver_analisis()

    def action_limpiar_filtros_informe(self):
        return self._wizard_reporte_actual().action_ver_analisis()

    def action_open_acta(self):
        self.ensure_one()
        acta = self.env['acta_entrega_documentos'].browse(self.acta_id_ref).exists()
        if not acta:
            raise UserError(_('La cuenta de cobro ya no está disponible.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cuenta de cobro'),
            'res_model': 'acta_entrega_documentos',
            'res_id': acta.id,
            'view_mode': 'form',
            'target': 'current',
        }

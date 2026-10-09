# -*- coding: utf-8 -*-
"""
Cobros reales (SIESA)  -  ESPEJO del modulo de pagos, del lado de los INGRESOS.
--------------------------------------------------------------------------------
Se engancha al boton "Cobros del proyecto" (modelo host: action_ingresar_pagos_proyecto)
y muestra, contra el programa de cobros de Odoo, lo que dice la contabilidad:

    CT = cuenta de cobro de APORTES (convenios).  Ancla: 8355100112 (cta de orden)
    FE = FACTURA de venta (proyectos propios).
    NV = nota credito de venta (reversa una FE) -> sale en negativo, a proposito.
    RT = recibo de tesoreria = EL QUE RECAUDA (el "PE" de los cobros).
    NRT= reversa un RT.
    DD/AJ/NI/OAJ/NC = bajan el saldo SIN ser recaudo de banco. En cobros esto son,
                      tipicamente, las RETENCIONES que el cliente le practica a la
                      Fundacion (ej. 2.5% en aportes, 8.5%-10.5% en facturacion).

LECCIONES HEREDADAS DE PAGOS (no repetir los errores):
  1) Se filtra por TIPO de documento, NO por cuenta contable. Asi la misma
     consulta sirve para convenios (ancla 8355) y facturacion (13xx).
  2) t354 registra la APERTURA del propio documento como si fuera un cruce:
     SE EXCLUYE. Si no, el recaudado se netea a 0 y todo sale pendiente.
  3) El refresco NUNCA va dentro de read(): solo por accion explicita del
     usuario (boton "Cobros del proyecto" o boton Refrescar).
  4) pendiente = por_cobrar - TODO lo aplicado (recaudo + deducciones + notas).
  5) Regla de estilo: "una fila, un color" -> el color lo pone el semaforo del
     tree, las celdas no fuerzan color propio.

Requiere: models/pagos_siesa.py (de ahi se reutiliza el mixin siesa.mssql.mixin).
"""
import re
import io
import base64
import logging
from contextlib import contextmanager

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


@contextmanager
def _silenciar_log_unlink_siesa():
    """
    Evita el ruido masivo del logger interno de Odoo durante el refresco SIESA.

    El proceso de sincronizacion reemplaza las lineas viejas por las nuevas, por
    eso necesita ejecutar unlink(). Odoo registra cada borrado con todos los IDs
    eliminados y puede llenar el docker log con miles de lineas. Este contexto
    baja temporalmente solo el logger "odoo.models.unlink" mientras se borran las
    lineas SIESA; no cambia permisos, datos, calculos ni el comportamiento del
    refresco. El resumen util queda en el _logger.info de una sola linea.
    """
    unlink_logger = logging.getLogger('odoo.models.unlink')
    previous_level = unlink_logger.level
    unlink_logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        unlink_logger.setLevel(previous_level)

try:
    import xlsxwriter   # viene incluida con Odoo 16
except Exception:
    xlsxwriter = None

# Campo del proyecto (modelo 'contratacion') que guarda la UN de SIESA.
UN_FIELD = 'mcb_centro_costo'


def _to_date(v):
    if not v:
        return False
    return v.date() if hasattr(v, 'date') else v


# ===========================================================================
#  LA CONSULTA (validada en SSMS contra 15-172 aportes y 07-002 facturacion)
# ===========================================================================
QUERY_COBROS = """
SET NOCOUNT ON;

-- v2 (jul-2026): cruces leen el documento directo de t354 (sin join a t351)
--                 y los STRING_AGG son deterministas (WITHIN GROUP).

IF OBJECT_ID('tempdb..#cxc')     IS NOT NULL DROP TABLE #cxc;
IF OBJECT_ID('tempdb..#detalle') IS NOT NULL DROP TABLE #detalle;
IF OBJECT_ID('tempdb..#cruces')  IS NOT NULL DROP TABLE #cruces;

-- PASO 1: causaciones de cobro (el derecho de cobro nace como DEBITO).
--   Se filtra por TIPO, no por cuenta -> sirve para aportes y facturacion.
SELECT mv.f351_rowid_docto AS rowid_docto, mv.f351_rowid_tercero AS rowid_tercero,
       ter.f200_nit AS nit, ter.f200_razon_social AS razon_social,
       d.f350_id_tipo_docto AS tipo, d.f350_consec_docto AS consec, d.f350_fecha AS fecha,
       -- Eslabon al registro presupuestal (mismo f350_rowid_docto_rp de pagos)
       CASE WHEN rp.f350_consec_docto IS NOT NULL
            THEN CONCAT(RTRIM(rp.f350_id_tipo_docto), ' ', rp.f350_consec_docto) END AS rcf,
       SUM(mv.f351_valor_db - mv.f351_valor_cr) AS por_cobrar
INTO #cxc
FROM t351_co_mov_docto mv
JOIN t200_mm_terceros ter ON ter.f200_rowid = mv.f351_rowid_tercero
JOIN t350_co_docto_contable d ON d.f350_rowid = mv.f351_rowid_docto
LEFT JOIN t350_co_docto_contable rp ON rp.f350_rowid = d.f350_rowid_docto_rp
WHERE mv.f351_ind_mov_sa = 1
  AND mv.f351_id_un = %(un)s
  AND d.f350_id_tipo_docto IN ('CT','FE','NV')
  -- ANULADOS FUERA (validado con datos): un documento anulado deja
  -- f350_fecha_ts_anulacion + f350_usuario_anulacion (y f350_ind_estado = 2).
  -- Los vigentes tienen esos campos en NULL (ind_estado = 1). Es comun que
  -- anulen una CT y hagan otra nueva (ej. CT 92/93 anuladas -> CT 94 vigente):
  -- sin este filtro esas anuladas salian como "pendientes" (miles de millones
  -- falsos). Se filtra por la fecha de anulacion, que es explicita y no depende
  -- de conocer todos los codigos de estado.
  AND d.f350_fecha_ts_anulacion IS NULL
GROUP BY mv.f351_rowid_docto, mv.f351_rowid_tercero, ter.f200_nit, ter.f200_razon_social,
         d.f350_id_tipo_docto, d.f350_consec_docto, d.f350_fecha,
         rp.f350_id_tipo_docto, rp.f350_consec_docto;

-- RENDIMIENTO: indice en la temporal. Sin esto, el PASO 2 se come t353/t354
-- enteras antes de filtrar por proyecto (la consulta se vuelve lentisima).
CREATE CLUSTERED INDEX ix_cxc ON #cxc(rowid_docto, rowid_tercero);

-- PASO 2: cruces del saldo abierto. Arranca DESDE #cxc (tabla chica, ya
--   filtrada por proyecto) y de ahi busca sus cruces: asi SQL Server hace
--   seeks en vez de escanear las tablas grandes.
--   OJO: se EXCLUYE la apertura del propio documento
--   (mc.f354_rowid_docto <> sa.f353_rowid_docto), si no el recaudo se netea
--   a 0 (bug real detectado en produccion del lado pagos).
SELECT sa.f353_rowid_docto AS rowid_docto, sa.f353_rowid_tercero AS rowid_tercero,
       dp.f350_id_tipo_docto AS tipo_doc, dp.f350_consec_docto AS consec_doc,
       MAX(mc.f354_fecha) AS fecha_mov,
       SUM(mc.f354_valor_cr - mc.f354_valor_db) AS valor_aplicado   -- credito = recaudo
INTO #detalle
FROM #cxc c
JOIN t353_co_saldo_abierto sa ON sa.f353_rowid_docto = c.rowid_docto
                             AND sa.f353_rowid_tercero = c.rowid_tercero
JOIN t354_co_mov_saldo_abierto mc ON mc.f354_rowid_sa = sa.f353_rowid
                                 -- v2: el cruce trae su documento DIRECTO en
                                 -- f354_rowid_docto (FK_t354_t350_1 verificada
                                 -- en el mapeo) -> ya no hay que pasar por t351
                                 -- (272k filas) solo para averiguarlo.
                                 AND mc.f354_rowid_docto <> sa.f353_rowid_docto
JOIN t350_co_docto_contable dp ON dp.f350_rowid = mc.f354_rowid_docto
GROUP BY sa.f353_rowid_docto, sa.f353_rowid_tercero, dp.f350_id_tipo_docto, dp.f350_consec_docto;

CREATE CLUSTERED INDEX ix_det ON #detalle(rowid_docto, rowid_tercero);

-- PASO 3: consolida por causacion.
--   recaudado      = RT/NRT  -> plata que ENTRO al banco
--   deduccion      = DD/AJ/NI/OAJ/NC -> retenciones del cliente / notas internas
--   aplicado_total = TODOS los cruces -> base del pendiente real
SELECT rowid_docto, rowid_tercero,
       SUM(CASE WHEN tipo_doc IN ('RT','NRT') THEN valor_aplicado ELSE 0 END) AS recaudado,
       SUM(CASE WHEN tipo_doc IN ('DD','AJ','NI','OAJ','NC') THEN valor_aplicado ELSE 0 END) AS deduccion,
       SUM(valor_aplicado) AS aplicado_total,
       STRING_AGG(CASE WHEN tipo_doc IN ('RT','NRT')
                       THEN CONCAT(RTRIM(tipo_doc), ' ', consec_doc) END, ' + ') WITHIN GROUP (ORDER BY consec_doc) AS doc_recaudo,
       STRING_AGG(CASE WHEN tipo_doc IN ('DD','AJ','NI','OAJ','NC')
                       THEN CONCAT(RTRIM(tipo_doc), ' ', consec_doc) END, ' + ') WITHIN GROUP (ORDER BY consec_doc) AS doc_deduccion,
       MAX(CASE WHEN tipo_doc = 'RT' THEN fecha_mov END) AS fecha_recaudo
INTO #cruces
FROM #detalle
GROUP BY rowid_docto, rowid_tercero;

-- PASO 4: resultado final
SELECT
    CASE WHEN c.tipo = 'CT' THEN 'Aporte'
         WHEN c.tipo = 'FE' THEN 'Factura'
         ELSE 'Nota credito' END AS clase_cobro,
    c.razon_social AS cliente, c.nit AS nit,
    ISNULL(c.rcf, '') AS rcf,
    CONCAT(RTRIM(c.tipo), ' ', c.consec) AS consec, c.fecha AS fecha,
    c.por_cobrar AS por_cobrar,
    ISNULL(p.recaudado, 0) AS recaudado,
    ISNULL(p.deduccion, 0) AS deduccion,
    p.doc_recaudo AS doc_recaudo,
    p.doc_deduccion AS doc_deduccion,
    p.fecha_recaudo AS fecha_recaudo,
    c.por_cobrar - ISNULL(p.aplicado_total, 0) AS pendiente
FROM #cxc c
LEFT JOIN #cruces p ON p.rowid_docto = c.rowid_docto AND p.rowid_tercero = c.rowid_tercero
ORDER BY clase_cobro, cliente, fecha, consec;

DROP TABLE #cxc; DROP TABLE #detalle; DROP TABLE #cruces;
"""


# ===========================================================================
#  LINEA ESPEJO: una fila por documento de cobro (CT/FE/NV) del proyecto.
# ===========================================================================
class CobrosSiesaLine(models.Model):
    _name = 'cobros.siesa.line'
    _description = 'Cobro real (SIESA)'
    _order = 'clase_cobro, cliente, fecha, consec'
    _rec_name = 'consec'

    cobros_id = fields.Many2one('action_ingresar_pagos_proyecto', string='Cobros del proyecto',
                                required=True, ondelete='cascade', index=True)
    # UN / proyecto de SIESA (ej. '15-172'). Guardado en cada linea para poder
    # consultar por proyecto sin pasar por el host (util para dashboards).
    id_un = fields.Char(string='UN / Proyecto', index=True)
    currency_id = fields.Many2one('res.currency', related='cobros_id.currency_id', store=True)

    clase_cobro = fields.Char(string='Clase')
    cliente = fields.Char(string='Cliente / Aportante')
    nit = fields.Char(string='NIT')
    rcf = fields.Char(string='RPC/RCF')
    consec = fields.Char(string='Documento')
    fecha = fields.Date(string='Fecha')
    por_cobrar = fields.Monetary(string='Por cobrar')
    recaudado = fields.Monetary(string='Recaudado')
    # OJO: en cobros la "deduccion" son, tipicamente, las retenciones que el
    # cliente le practica a la Fundacion (no es plata que se pierda: es un
    # anticipo de impuestos). Incluye DD + AJ + NI + OAJ + NC.
    deduccion = fields.Monetary(string='Deducción / retención')
    doc_recaudo = fields.Char(string='Recibo (RT)')
    doc_deduccion = fields.Char(string='Doc. deduccion')
    fecha_recaudo = fields.Date(string='Fecha recaudo')
    pendiente = fields.Monetary(string='Pendiente por recaudar',
                                help='Por cobrar menos TODO lo aplicado (recaudo + '
                                     'deducciones/retenciones). Si es 0, quedo saldado.')

    # Estado del cobro para la columna "Estado" (reemplaza los colores de fila).
    estado = fields.Char(string='Estado', compute='_compute_estado', store=True)

    dias = fields.Integer(string='Días', compute='_compute_dias',
                          help='Días desde la causación mientras el documento sigue '
                               'abierto (Pendiente o Parcial). 0 = ya cerrado.')

    @api.depends('fecha', 'estado')
    def _compute_dias(self):
        hoy = fields.Date.context_today(self)
        for l in self:
            if l.fecha and l.estado in ('Pendiente', 'Parcial'):
                l.dias = (hoy - l.fecha).days
            else:
                l.dias = 0

    @api.depends('por_cobrar', 'pendiente')
    def _compute_estado(self):
        for l in self:
            base = l.por_cobrar or 0.0
            pend = l.pendiente or 0.0
            if base <= 0:
                l.estado = 'Nota crédito'
            elif -1 < pend < 1:
                l.estado = 'Saldado'
            elif pend < base:
                l.estado = 'Parcial'
            else:
                l.estado = 'Pendiente'

    # --- Ficha del cliente (computados, NO almacenados: solo al abrir la fila) ---
    hist_siesa_ids = fields.Many2many('cobros.siesa.line', compute='_compute_detalle',
                                      store=False, string='Documentos del cliente')
    det_num_docs = fields.Integer(compute='_compute_detalle', store=False)
    det_por_cobrar = fields.Monetary(string='Total por cobrar', currency_field='currency_id',
                                     compute='_compute_detalle', store=False)
    det_recaudado = fields.Monetary(string='Total recaudado', currency_field='currency_id',
                                    compute='_compute_detalle', store=False)
    det_deduccion = fields.Monetary(string='Total deducciones', currency_field='currency_id',
                                    compute='_compute_detalle', store=False)
    det_pendiente = fields.Monetary(string='Total pendiente', currency_field='currency_id',
                                    compute='_compute_detalle', store=False)

    @api.depends('nit', 'cobros_id')
    def _compute_detalle(self):
        Line = self.env['cobros.siesa.line']
        for line in self:
            hermanos = Line.browse()
            if line.nit and line.cobros_id:
                hermanos = Line.search([('cobros_id', '=', line.cobros_id.id),
                                        ('nit', '=', line.nit)])
            line.hist_siesa_ids = hermanos
            line.det_num_docs = len(hermanos)
            line.det_por_cobrar = sum(hermanos.mapped('por_cobrar'))
            line.det_recaudado = sum(hermanos.mapped('recaudado'))
            line.det_deduccion = sum(hermanos.mapped('deduccion'))
            line.det_pendiente = sum(hermanos.mapped('pendiente'))


# ===========================================================================
#  HOST: el modelo que abre el boton "Cobros del proyecto".
# ===========================================================================
class CobrosProyectoSiesa(models.Model):
    _inherit = ['action_ingresar_pagos_proyecto', 'siesa.mssql.mixin']
    _name = 'action_ingresar_pagos_proyecto'

    cobros_siesa_ids = fields.One2many('cobros.siesa.line', 'cobros_id', string='Cobros SIESA')
    cobros_siesa_last_sync = fields.Datetime(string='Ultima actualizacion', readonly=True)
    siesa_currency_id = fields.Many2one('res.currency', compute='_compute_totales_cobros')

    cobros_vista_sel = fields.Selection([
        ('Principales', 'Aportes + Facturas'),
        ('Aporte', 'Aportes (CT)'),
        ('Factura', 'Facturas (FE)'),
        ('Nota credito', 'Notas crédito (NV)'),
    ], string='Ver', default='Principales')
    cobros_siesa_buscar = fields.Char(string='Buscar')
    cobros_filtro_estado = fields.Selection([
        ('todos', 'Todos'), ('Pendiente', 'Pendientes'), ('Parcial', 'Parciales'),
        ('Saldado', 'Saldados')],
        string='Estado', default='todos')
    cobros_vista_count = fields.Integer(compute='_compute_cobros_vista')

    cobros_vista_ids = fields.Many2many(
        'cobros.siesa.line', 'rel_cob_vista', compute='_compute_cobros_vista',
        string='Detalle del cliente')

    total_cobros_count = fields.Integer(compute='_compute_totales_cobros')
    total_cobros_por_cobrar = fields.Monetary(string='Por cobrar', currency_field='siesa_currency_id',
                                              compute='_compute_totales_cobros')
    total_cobros_recaudado = fields.Monetary(string='Recaudado', currency_field='siesa_currency_id',
                                             compute='_compute_totales_cobros')
    total_cobros_deduccion = fields.Monetary(string='Deducciones', currency_field='siesa_currency_id',
                                             compute='_compute_totales_cobros')
    total_cobros_pendiente = fields.Monetary(string='Pendiente por recaudar',
                                             currency_field='siesa_currency_id',
                                             compute='_compute_totales_cobros')
    total_cobros_pct = fields.Float(string='% recaudado', compute='_compute_totales_cobros')

    # --- Desglose adicional, TODO calculado desde SIESA (los dos mundos del
    #     cobro + la retencion efectiva). Reemplaza los campos obsoletos que el
    #     SGO calculaba con data manual (esos ya no aplican: lo financiero es SIESA). ---
    tot_cob_aportes = fields.Monetary(string='Aportes por cobrar', currency_field='siesa_currency_id',
                                      compute='_compute_cobros_desglose')
    tot_cob_facturas = fields.Monetary(string='Facturas por cobrar', currency_field='siesa_currency_id',
                                       compute='_compute_cobros_desglose')
    tot_cob_ret_pct = fields.Float(string='Retención efectiva', compute='_compute_cobros_desglose')
    tot_cob_saldados_txt = fields.Char(string='Documentos saldados', compute='_compute_cobros_desglose')

    @api.depends('cobros_siesa_last_sync')
    def _compute_cobros_desglose(self):
        for rec in self:
            lines = rec.cobros_siesa_ids
            rec.tot_cob_aportes = sum(lines.filtered(lambda l: l.clase_cobro == 'Aporte').mapped('por_cobrar'))
            rec.tot_cob_facturas = sum(lines.filtered(lambda l: l.clase_cobro == 'Factura').mapped('por_cobrar'))
            base = sum(lines.mapped('por_cobrar'))
            ded = sum(lines.mapped('deduccion'))
            rec.tot_cob_ret_pct = (100.0 * ded / base) if base else 0.0
            # El "de N" debe contar sobre los documentos PRINCIPALES (aportes+facturas),
            # que son los que el usuario ve por defecto; las notas crédito se cuentan aparte
            # para que el número no confunda (antes decia "4 de 5" sin mostrar el 5o).
            principales = lines.filtered(lambda l: l.clase_cobro in ('Aporte', 'Factura'))
            saldados = len(principales.filtered(lambda l: l.estado == 'Saldado'))
            notas = len(lines.filtered(lambda l: l.clase_cobro == 'Nota crédito'))
            txt = '%d de %d' % (saldados, len(principales))
            if notas:
                txt += ' (+%d N. crédito)' % notas
            rec.tot_cob_saldados_txt = txt
    # True cuando YA se consulto SIESA y el proyecto no tiene NI UN documento de
    # cobro. Sirve para explicarle al usuario por que la tabla sale vacia en vez
    # de dejarlo adivinando. Validado con datos: hay convenios (ej. 15-160) que
    # legitimamente no tienen CT/FE porque su plata entro como saldo inicial (SI)
    # en la migracion a SIESA, antes de que el ERP operara.
    cobros_sin_datos = fields.Boolean(compute='_compute_cobros_sin_datos')

    @api.depends('cobros_siesa_last_sync', 'cobros_siesa_ids')
    def _compute_cobros_sin_datos(self):
        Line = self.env['cobros.siesa.line']
        for rec in self:
            origen = rec._origin if rec._origin else rec
            rec.cobros_sin_datos = bool(
                rec.cobros_siesa_last_sync and origen.id
                and not Line.search_count([('cobros_id', '=', origen.id)]))

    # OJO: NO poner 'cobros_siesa_ids' en el depends (misma leccion que pagos):
    # el compute no lo usa, pero declararlo obliga a fotografiar todas las lineas
    # en cada tecla -> lento y el <tree> sale vacio con el formulario sucio.
    @api.depends('cobros_siesa_last_sync', 'cobros_siesa_buscar', 'cobros_vista_sel', 'cobros_filtro_estado')
    def _compute_cobros_vista(self):
        """Busca DIRECTO en la base sobre TODAS las lineas del proyecto (todas
        las paginas), no sobre lo que el navegador tenga cargado.

        OJO: se usa rec._origin. Al tocar el selector o el buscador, el
        formulario queda 'sucio' y Odoo corre el compute sobre un registro
        virtual (NewId) cuyo .id NO es un entero. Si se usara rec.id, la tabla
        se vaciaria en cuanto el usuario cambia de categoria (bug real).
        _origin apunta al registro de verdad que hay detras.
        """
        Line = self.env['cobros.siesa.line']
        for rec in self:
            origen = rec._origin if rec._origin else rec
            if not origen.id:
                rec.cobros_vista_ids = Line.browse()
                rec.cobros_vista_count = 0
                continue
            cat = rec.cobros_vista_sel or 'Principales'
            dominio = [('cobros_id', '=', origen.id)]
            if cat == 'Principales':
                dominio.append(('clase_cobro', 'in', ['Aporte', 'Factura']))
            else:
                dominio.append(('clase_cobro', '=', cat))
            # El buscador consulta la BD (no filtra solo la pagina cargada).
            filtro = rec.cobros_filtro_estado or 'todos'
            if filtro != 'todos':
                dominio.append(('estado', '=', filtro))
            q = (rec.cobros_siesa_buscar or '').strip()
            if q:
                for term in q.split():
                    dominio += ['|', '|', '|', '|',
                                ('cliente', 'ilike', term),
                                ('nit', 'ilike', term),
                                ('consec', 'ilike', term),
                                ('rcf', 'ilike', term),
                                ('doc_recaudo', 'ilike', term)]
            res = Line.search(dominio)
            rec.cobros_vista_ids = res
            rec.cobros_vista_count = len(res)

    @api.depends('cobros_vista_ids')
    def _compute_totales_cobros(self):
        cop = self.env.ref('base.COP', raise_if_not_found=False)
        for rec in self:
            lines = rec.cobros_vista_ids
            rec.siesa_currency_id = rec.currency_id.id if rec.currency_id else (cop.id if cop else False)
            rec.total_cobros_count = len(lines)
            rec.total_cobros_por_cobrar = sum(lines.mapped('por_cobrar'))
            rec.total_cobros_recaudado = sum(lines.mapped('recaudado'))
            rec.total_cobros_deduccion = sum(lines.mapped('deduccion'))
            rec.total_cobros_pendiente = sum(lines.mapped('pendiente'))
            base = rec.total_cobros_por_cobrar
            rec.total_cobros_pct = (100.0 * rec.total_cobros_recaudado / base) if base else 0.0

    def _siesa_un_code_cobros(self):
        """La UN del proyecto (mismo campo que usa pagos: contratacion.mcb_centro_costo)."""
        self.ensure_one()
        proyecto = self.proyecto_relacionado
        if not proyecto or UN_FIELD not in proyecto._fields:
            return False
        val = proyecto[UN_FIELD]
        if not val:
            return False
        if hasattr(val, '_name'):
            return (val.code if 'code' in val._fields else val.display_name) or False
        return str(val).strip()

    def action_cargar_cobros_siesa(self):
        """Boton Refrescar: fuerza el refresco YA."""
        for rec in self:
            rec._cargar_cobros_siesa(force=True)
        return True

    def _cargar_cobros_siesa(self, force=False):
        """Trae de SIESA y rehace el espejo. NUNCA llamar desde read()."""
        self.ensure_one()
        if not force and self.cobros_siesa_last_sync:
            return
        un = self._siesa_un_code_cobros()
        if not un:
            _logger.info("Cobros SIESA: el proyecto %s no tiene UN (%s)", self.id, UN_FIELD)
            return
        filas = self._run_siesa_query(QUERY_COBROS, {'un': un})
        Line = self.env['cobros.siesa.line']
        lineas_anteriores = Line.search_count([('cobros_id', '=', self.id)])
        with _silenciar_log_unlink_siesa():
            self.cobros_siesa_ids.unlink()
        valores = []
        for r in filas:
            valores.append({
                'cobros_id': self.id,
                'id_un': un,
                'clase_cobro': (r.get('clase_cobro') or '').strip(),
                'cliente': (r.get('cliente') or '').strip(),
                'nit': (r.get('nit') or '').strip(),
                'rcf': (r.get('rcf') or '').strip() or False,
                'consec': (r.get('consec') or '').strip(),
                'fecha': _to_date(r.get('fecha')),
                'por_cobrar': float(r.get('por_cobrar') or 0.0),
                'recaudado': float(r.get('recaudado') or 0.0),
                'deduccion': float(r.get('deduccion') or 0.0),
                'doc_recaudo': r.get('doc_recaudo') or False,
                'doc_deduccion': r.get('doc_deduccion') or False,
                'fecha_recaudo': _to_date(r.get('fecha_recaudo')),
                'pendiente': float(r.get('pendiente') or 0.0),
            })
        if valores:
            Line.create(valores)
        self.cobros_siesa_last_sync = fields.Datetime.now()
        #ESTE LOG NOS PERMITE REVISAR SI EL CRON ESTA FUNCIONANDO CORRECTAMENTE, YA QUE NO HAY VISTA PARA VER ESTO
        _logger.info(
            "SIESA Cobros: refresco ejecutado | cobros_id=%s proyecto_id=%s UN=%s eliminadas=%s creadas=%s force=%s",
            self.id,
            self.proyecto_relacionado.id if self.proyecto_relacionado else False,
            un or 'sin_un',
            lineas_anteriores,
            len(valores),
            force,
        )


    def action_limpiar_busqueda_cobros(self):
        for rec in self:
            rec.cobros_siesa_buscar = False
        return True

    def action_exportar_excel_cobros(self):
        """Boton Excel: abre el mini-formulario para elegir que categorias bajar."""
        self.ensure_one()
        sel = self.cobros_vista_sel or 'Principales'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Descargar Excel',
            'res_model': 'cobros.siesa.export.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_cobros_id': self.id,
                'default_incluir_aportes': sel in ('Aporte', 'Principales'),
                'default_incluir_facturas': sel in ('Factura', 'Principales'),
                'default_incluir_notas': sel == 'Nota credito',
            },
        }

    def action_guia_tecnica_cobros(self):
        """Boton: abre la guia tecnica del modulo de cobros."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Guia tecnica - Cobros SIESA',
            'res_model': 'cobros.siesa.guia.wizard',
            'view_mode': 'form',
            'target': 'new',
        }


# ===========================================================================
#  El boton "Cobros del proyecto" (en 'ejecucion') fuerza UN refresco por
#  pulsada. NO se toca Ejecucion.py: es un _inherit desde aca.
#  IMPORTANTE: nada de cargas dentro de read() (ver leccion 3 del encabezado).
# ===========================================================================
class EjecucionCobrosSiesa(models.Model):
    _inherit = 'ejecucion'

    def action_ingresar_pagos_proyecto(self):
        accion = super().action_ingresar_pagos_proyecto()
        try:
            Host = self.env['action_ingresar_pagos_proyecto']
            hosts = Host.browse()
            if (isinstance(accion, dict)
                    and accion.get('res_model') == 'action_ingresar_pagos_proyecto'
                    and accion.get('res_id')):
                hosts = Host.browse(accion['res_id'])
            elif self.id_proyecto:
                # Red de seguridad: si la accion no trae res_id, se refresca el
                # registro de cobros que cuelga de esta ejecucion.
                hosts = self.id_proyecto
            for h in hosts:
                h._cargar_cobros_siesa(force=True)
        except Exception as e:
            _logger.warning("Refresco SIESA al entrar a Cobros del proyecto: %s", e)
        return accion


# ===========================================================================
#  WIZARD: Excel de cobros (una hoja por categoria, con totales).
# ===========================================================================
NOMBRES_HOJA_COBROS = {
    'Aporte': 'Aportes (CT)',
    'Factura': 'Facturas (FE)',
    'Nota credito': 'Notas crédito (NV)',
}


class CobrosSiesaExportWizard(models.TransientModel):
    _name = 'cobros.siesa.export.wizard'
    _description = 'Descargar cobros SIESA en Excel'

    cobros_id = fields.Many2one('action_ingresar_pagos_proyecto', string='Proyecto', required=True)
    incluir_aportes = fields.Boolean(string='Aportes (CT)')
    incluir_facturas = fields.Boolean(string='Facturas (FE)')
    incluir_notas = fields.Boolean(string='Notas crédito (NV)')

    def action_exportar(self):
        self.ensure_one()
        if xlsxwriter is None:
            raise UserError("Falta la libreria 'xlsxwriter' en el servidor del SGO. Pidele al administrador instalarla con: "
                            "Instalala con: docker exec -u root -it gofuv pip install xlsxwriter")
        clases = []
        if self.incluir_aportes:
            clases.append('Aporte')
        if self.incluir_facturas:
            clases.append('Factura')
        if self.incluir_notas:
            clases.append('Nota credito')
        if not clases:
            raise UserError("Elegi al menos una categoria para descargar.")

        pp = self.cobros_id
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        f_head = wb.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#1C2B5A',
                                'border': 1, 'border_color': '#16234A',
                                'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
        f_money = wb.add_format({'num_format': '#,##0.00;[Red]-#,##0.00'})
        f_date = wb.add_format({'num_format': 'dd/mm/yyyy', 'align': 'center'})
        f_tot_txt = wb.add_format({'bold': True, 'top': 2})
        f_tot_money = wb.add_format({'num_format': '#,##0.00;[Red]-#,##0.00', 'bold': True, 'top': 2})

        columnas = [
            ('Cliente / Aportante', 38), ('NIT', 14), ('RPC/RCF', 14), ('Documento', 12),
            ('Fecha', 13), ('Por cobrar', 17), ('Recaudado', 17),
            ('Deduccion / retencion', 18), ('Doc. deduccion', 18),
            ('Recibo (RT)', 20), ('Fecha recaudo', 14), ('Pendiente', 17),
        ]
        sumas = [(5, 'por_cobrar'), (6, 'recaudado'), (7, 'deduccion'), (11, 'pendiente')]

        for clase in clases:
            lineas = pp.cobros_siesa_ids.filtered(lambda l: l.clase_cobro == clase)
            ws = wb.add_worksheet(NOMBRES_HOJA_COBROS.get(clase, clase)[:31])
            ws.freeze_panes(1, 0)
            ws.set_row(0, 26)
            for i, (titulo, ancho) in enumerate(columnas):
                ws.set_column(i, i, ancho)
                ws.write(0, i, titulo, f_head)
            fila = 1
            for l in lineas:
                ws.write(fila, 0, l.cliente or '')
                ws.write(fila, 1, l.nit or '')
                ws.write(fila, 2, l.rcf or '')
                ws.write(fila, 3, l.consec or '')
                if l.fecha:
                    ws.write_datetime(fila, 4, l.fecha, f_date)
                ws.write_number(fila, 5, l.por_cobrar or 0.0, f_money)
                ws.write_number(fila, 6, l.recaudado or 0.0, f_money)
                ws.write_number(fila, 7, l.deduccion or 0.0, f_money)
                ws.write(fila, 8, l.doc_deduccion or '')
                ws.write(fila, 9, l.doc_recaudo or '')
                if l.fecha_recaudo:
                    ws.write_datetime(fila, 10, l.fecha_recaudo, f_date)
                ws.write_number(fila, 11, l.pendiente or 0.0, f_money)
                fila += 1
            ws.write(fila, 0, 'TOTAL  (%d documentos)' % len(lineas), f_tot_txt)
            for col, campo in sumas:
                ws.write_number(fila, col, sum(lineas.mapped(campo)) or 0.0, f_tot_money)
        wb.close()

        try:
            un = pp._siesa_un_code_cobros() or ''
        except Exception:
            un = ''
        nombre = 'Cobros_SIESA_%s_%s.xlsx' % (un or 'proyecto', fields.Date.context_today(self))
        nombre = re.sub(r'[^A-Za-z0-9_.\-]+', '_', nombre)
        att = self.env['ir.attachment']._drive_create_temp_download({
            'name': nombre,
            'type': 'binary',
            'datas': base64.b64encode(buf.getvalue()),
            'res_model': 'action_ingresar_pagos_proyecto',
            'res_id': pp.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % att.id,
            'target': 'self',
        }


# ===========================================================================
#  WIZARD: guia tecnica (todo el contenido vive en su vista).
# ===========================================================================
class CobrosSiesaGuiaWizard(models.TransientModel):
    _name = 'cobros.siesa.guia.wizard'
    _description = 'Guia tecnica - Cobros SIESA'

    dummy = fields.Boolean(default=True)

# -*- coding: utf-8 -*-
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
    import pymssql
except Exception:
    pymssql = None

try:
    import xlsxwriter   # viene incluida con Odoo 16
except Exception:
    xlsxwriter = None


# ===========================================================================
#  Campo del proyecto (modelo 'contratacion') que guarda la UN / centro de costo.
#  CONFIRMADO: contratacion.mcb_centro_costo (lo mismo que usa tu vista_sql.py).
# ===========================================================================
UN_FIELD = 'mcb_centro_costo'


# ===========================================================================
#  Consulta consolidada v3: CONTRATISTAS + COMPRAS con retenciones desglosadas.
#  Tipos de documento calibrados con la tabla oficial de FUV (financiera):
#    Causaciones:  Contratista = S4, S5   (no obligado a facturar)
#                  Compra      = FP, FS, FT, CP  (obligado a facturar)
#    Retencion por subcuenta 2436: reteICA=243627% reteIVA=243625% resto=reteFuente
#    Pagos: PE, PD, PN, PSS  +  reversion NB (resta, p.ej. pago rebotado)
#    Deducciones (bajan la CxP, NO son banco): DD, AJ, NI, OAJ
#
#  OJO pymssql: el paramstyle es pyformat, asi que los % literales de los LIKE
#  van DOBLES (%%); el unico placeholder real es %(un)s.
# ===========================================================================
QUERY_PAGOS = """
SET NOCOUNT ON;

-- v2 (jul-2026): cruces leen el documento directo de t354 (sin join a t351)
--                 y los STRING_AGG son deterministas (WITHIN GROUP).

IF OBJECT_ID('tempdb..#cxp')     IS NOT NULL DROP TABLE #cxp;
IF OBJECT_ID('tempdb..#ret')     IS NOT NULL DROP TABLE #ret;
IF OBJECT_ID('tempdb..#detalle') IS NOT NULL DROP TABLE #detalle;
IF OBJECT_ID('tempdb..#cruces')  IS NOT NULL DROP TABLE #cruces;

-- PASO 1: causaciones (contratistas + compras)
SELECT mv.f351_rowid_docto AS rowid_docto, mv.f351_rowid_tercero AS rowid_tercero,
       ter.f200_nit AS nit, ter.f200_razon_social AS razon_social,
       d.f350_id_tipo_docto AS tipo, d.f350_consec_docto AS consec, d.f350_fecha AS fecha,
       -- ESLABON causacion->contrato: cada documento contable puede traer el
       -- rowid de SU registro presupuestal en f350_rowid_docto_rp. Cuando
       -- existe, ESTA causacion queda amarrada a SU RP exacto (RCF/RCA/RCR...).
       -- Si viene NULL, abajo cae al fallback por tercero.
       CASE WHEN rp.f350_consec_docto IS NOT NULL
            THEN CONCAT(RTRIM(rp.f350_id_tipo_docto), ' ', rp.f350_consec_docto) END AS rcf_dir,
       SUM(mv.f351_valor_cr - mv.f351_valor_db) AS neto
INTO #cxp
FROM t351_co_mov_docto mv
JOIN t200_mm_terceros ter ON ter.f200_rowid = mv.f351_rowid_tercero
JOIN t350_co_docto_contable d ON d.f350_rowid = mv.f351_rowid_docto
LEFT JOIN t350_co_docto_contable rp
       ON rp.f350_rowid = d.f350_rowid_docto_rp
WHERE mv.f351_ind_mov_sa = 1 AND mv.f351_id_un = %(un)s
  AND d.f350_id_tipo_docto IN ('S4','S5','FP','FS','FT','CP','CC','CJ','NJ','NO')
  -- ANULADOS FUERA (validado con datos reales): un documento anulado deja
  -- f350_fecha_ts_anulacion + f350_usuario_anulacion. Sin este filtro salian
  -- como deuda PENDIENTE causaciones que ya no existen (caso real 15-160:
  -- S4 4303 MAPA SERVICE 22.914.366 y FP 116978 DEVCODES 59.747.538 estaban
  -- anulados y aparecian como plata por pagar).
  AND d.f350_fecha_ts_anulacion IS NULL
GROUP BY mv.f351_rowid_docto, mv.f351_rowid_tercero, ter.f200_nit, ter.f200_razon_social,
         d.f350_id_tipo_docto, d.f350_consec_docto, d.f350_fecha,
         rp.f350_id_tipo_docto, rp.f350_consec_docto;

-- RENDIMIENTO: indice en la temporal apenas nace. Todos los pasos siguientes
-- arrancan DESDE #cxp (tabla chica ya filtrada por proyecto) y con esto hacen
-- seeks en vez de escanear. Mismo patron probado en el modulo de cobros.
CREATE CLUSTERED INDEX ix_cxp ON #cxp(rowid_docto, rowid_tercero);

-- PASO 2: retenciones desglosadas (subcuentas 2436 dentro del mismo doc)
SELECT mv.f351_rowid_docto AS rowid_docto, mv.f351_rowid_tercero AS rowid_tercero,
       SUM(CASE WHEN LEFT(aux.f253_id, 6) = '243627' THEN mv.f351_valor_cr ELSE 0 END) AS reteica,
       SUM(CASE WHEN LEFT(aux.f253_id, 6) = '243625' THEN mv.f351_valor_cr ELSE 0 END) AS reteiva,
       SUM(CASE WHEN LEFT(aux.f253_id, 6) NOT IN ('243627','243625') THEN mv.f351_valor_cr ELSE 0 END) AS retefuente
INTO #ret
FROM #cxp c
JOIN t351_co_mov_docto mv ON mv.f351_rowid_docto = c.rowid_docto
                         AND mv.f351_rowid_tercero = c.rowid_tercero
JOIN t253_co_auxiliares aux ON aux.f253_rowid = mv.f351_rowid_auxiliar
-- Este LIKE se queda A PROPOSITO: es de PREFIJO ('2436%%'), o sea SARGable
-- (SQL Server lo convierte en un rango >= '2436' y < '2437' y usa indice).
-- Los LIKE que si eran costosos (los del CASE, uno por fila por patron) se
-- reemplazaron por comparaciones exactas con LEFT().
WHERE aux.f253_id LIKE '2436%%'
GROUP BY mv.f351_rowid_docto, mv.f351_rowid_tercero;

CREATE CLUSTERED INDEX ix_ret ON #ret(rowid_docto, rowid_tercero);

-- PASO 3.1: cruces (pagos suman; reversiones NB restan via db - cr).
-- OJO CRITICO: t354 tambien registra el movimiento de APERTURA del propio
-- documento (el S4 aparece "cruzandose" a si mismo con -neto). Se EXCLUYE
-- (mc.f354_rowid_docto <> sa.f353_rowid_docto); si se contara, aplicado_total
-- quedaria neteado a 0 y el "pendiente" saldria inflado en todo lo saldado.
SELECT sa.f353_rowid_docto AS rowid_docto, sa.f353_rowid_tercero AS rowid_tercero,
       dp.f350_id_tipo_docto AS tipo_doc, dp.f350_consec_docto AS consec_doc,
       MAX(mc.f354_fecha) AS fecha_mov, SUM(mc.f354_valor_db - mc.f354_valor_cr) AS valor_aplicado
INTO #detalle
FROM #cxp c
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

-- PASO 3.2: consolida cruces por causacion.
--   valor_pagado  = lo que salio del BANCO (PE/PD/PN/PSS; NB resta si reboto).
--   deduccion     = lo que bajo la deuda SIN ser banco: DD (art.383, cruces de
--                   proveedor) + AJ/NI/OAJ (notas internas de correccion).
--                   OJO: NO es solo DD -> por eso se muestra el tipo y numero
--                   del documento en doc_deduccion (ej. 'DD 7402', 'AJ 430 + NI 76832').
--   aplicado_total= TODOS los cruces, sin importar el tipo. Sirve para saber si
--                   la causacion quedo saldada de verdad (pendiente = neto - aplicado).
SELECT rowid_docto, rowid_tercero,
       SUM(CASE WHEN tipo_doc IN ('PE','PD','PN','PSS','NB') THEN valor_aplicado ELSE 0 END) AS valor_pagado,
       SUM(CASE WHEN tipo_doc IN ('DD','AJ','NI','OAJ') THEN valor_aplicado ELSE 0 END) AS deduccion_dd,
       SUM(valor_aplicado) AS aplicado_total,
       STRING_AGG(CASE WHEN tipo_doc IN ('PE','PD','PN','PSS','NB')
                       THEN CONCAT(RTRIM(tipo_doc), ' ', consec_doc) END, ' + ') WITHIN GROUP (ORDER BY consec_doc) AS doc_pago,
       STRING_AGG(CASE WHEN tipo_doc IN ('DD','AJ','NI','OAJ')
                       THEN CONCAT(RTRIM(tipo_doc), ' ', consec_doc) END, ' + ') WITHIN GROUP (ORDER BY consec_doc) AS doc_deduccion,
       MAX(CASE WHEN tipo_doc IN ('PE','PD','PN','PSS') THEN fecha_mov END) AS fecha_pago
INTO #cruces
FROM #detalle
GROUP BY rowid_docto, rowid_tercero;

-- PASO 4: resultado final
SELECT
    CASE WHEN c.tipo IN ('S4','S5')          THEN 'Contratista'
         WHEN c.tipo IN ('FP','FS','FT','CP') THEN 'Compra/Proveedor'
         WHEN c.tipo IN ('CC','CJ')           THEN 'Gasto'
         ELSE 'Nota/Reversion' END AS clase_tercero,
    c.razon_social AS contratista, c.nit AS nit,
    -- RPC/RCF: primero el eslabon DIRECTO por causacion (rcf_dir, via
    -- f350_rowid_docto_rp -> su registro presupuestal exacto: RCF/RCA/RCR...).
    -- Si esa causacion no trae RP, cae al fallback por TERCERO (todos sus RCF
    -- del proyecto con ' | ', desde t3332_co_registro_pptal).
    COALESCE(c.rcf_dir, rcf.rcf, '') AS rcf,
    CONCAT(RTRIM(c.tipo), ' ', c.consec) AS consec, c.fecha AS fecha,
    c.neto + ISNULL(r.retefuente,0) + ISNULL(r.reteica,0) + ISNULL(r.reteiva,0) AS bruto,
    ISNULL(r.retefuente,0) AS retefuente, ISNULL(r.reteica,0) AS reteica, ISNULL(r.reteiva,0) AS reteiva,
    ISNULL(r.retefuente,0) + ISNULL(r.reteica,0) + ISNULL(r.reteiva,0) AS retencion,
    c.neto AS neto, ISNULL(p.deduccion_dd,0) AS deduccion_dd,
    p.doc_deduccion AS doc_deduccion,
    -- PENDIENTE REAL = lo causado menos TODO lo que ya se le aplico (pago del
    -- banco + deducciones DD/AJ/NI/OAJ + cualquier otro cruce). Si da ~0, la
    -- causacion esta SALDADA aunque el pago del banco haya sido menor al neto
    -- (justo el caso de las DD: se pago bien, la DD cubrio el resto).
    c.neto - ISNULL(p.aplicado_total,0) AS pendiente,
    p.doc_pago AS pe, p.fecha_pago AS fecha_pago, ISNULL(p.valor_pagado,0) AS valor_pagado,
    CASE WHEN c.tipo IN ('S4','S5') THEN
      CAST(100.0 * c.neto
           / NULLIF(SUM(CASE WHEN c.tipo IN ('S4','S5') THEN c.neto END) OVER (PARTITION BY c.nit), 0)
           AS DECIMAL(6,2)) END AS pct_pago,
    CASE WHEN c.tipo IN ('S4','S5') THEN
      CAST(100.0 * SUM(CASE WHEN c.tipo IN ('S4','S5') THEN c.neto END)
                   OVER (PARTITION BY c.nit ORDER BY c.fecha, c.consec ROWS UNBOUNDED PRECEDING)
           / NULLIF(SUM(CASE WHEN c.tipo IN ('S4','S5') THEN c.neto END) OVER (PARTITION BY c.nit), 0)
           AS DECIMAL(6,2)) END AS pct_acumulado
FROM #cxp c
LEFT JOIN #ret r    ON r.rowid_docto = c.rowid_docto AND r.rowid_tercero = c.rowid_tercero
LEFT JOIN #cruces p ON p.rowid_docto = c.rowid_docto AND p.rowid_tercero = c.rowid_tercero
LEFT JOIN (
    SELECT rp.f3332_rowid_tercero AS rid,
           STRING_AGG(CONCAT(RTRIM(d.f350_id_tipo_docto), ' ', d.f350_consec_docto), ' | ')
               WITHIN GROUP (ORDER BY d.f350_consec_docto) AS rcf
    FROM   t3332_co_registro_pptal rp
    JOIN   t350_co_docto_contable d ON d.f350_rowid = rp.f3332_rowid_docto
                                   -- RCF ANULADOS FUERA (mismo patron validado con
                                   -- los CT): el anulado trae fecha de anulacion.
                                   -- Presupuesto "anula y hace otro" (ej. via ARC);
                                   -- sin esto la lista mostraba RCF muertos.
                                   AND d.f350_fecha_ts_anulacion IS NULL
    WHERE  EXISTS (SELECT 1 FROM t351_co_mov_docto mv
                   WHERE mv.f351_rowid_docto = rp.f3332_rowid_docto
                     AND mv.f351_id_un = %(un)s)
      -- RENDIMIENTO: solo terceros que de verdad estan en la tabla del proyecto
      -- (antes calculaba los RCF de TODOS los terceros con presupuesto en la UN).
      AND rp.f3332_rowid_tercero IN (SELECT rowid_tercero FROM #cxp)
    GROUP BY rp.f3332_rowid_tercero
) rcf ON rcf.rid = c.rowid_tercero
ORDER BY clase_tercero, contratista, fecha, consec;

DROP TABLE #cxp; DROP TABLE #ret; DROP TABLE #detalle; DROP TABLE #cruces;
"""


class SiesaMssqlMixin(models.AbstractModel):
    """Conexion reutilizable a SQL Server (UnoEE / SIESA)."""
    _name = 'siesa.mssql.mixin'
    _description = 'Conexion reutilizable a SQL Server (UnoEE/SIESA)'

    @api.model
    def _run_siesa_query(self, sql, params=None):
        import os
        try:
            from dotenv import load_dotenv
            # Cargar .env desde la raíz del módulo explícitamente
            env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
            load_dotenv(dotenv_path=env_path)
        except ImportError:
            pass

        if pymssql is None:
            _logger.error("pymssql no esta instalado en el servidor de Odoo.")
            return []
        
        icp = self.env['ir.config_parameter'].sudo()
        
        # Primero intenta leer de las variables de entorno (.env o sistema), si no, usa los parametros de Odoo
        server = os.environ.get('SIESA_MSSQL_SERVER') or icp.get_param('siesa.mssql.server', '127.0.0.1')
        port = int(os.environ.get('SIESA_MSSQL_PORT') or icp.get_param('siesa.mssql.port', '1433'))
        user = os.environ.get('SIESA_MSSQL_USER') or icp.get_param('siesa.mssql.user', 'odoo_lectura')
        password = os.environ.get('SIESA_MSSQL_PASSWORD') or icp.get_param('siesa.mssql.password', '')
        database = os.environ.get('SIESA_MSSQL_DATABASE') or icp.get_param('siesa.mssql.database', '')

        conn = pymssql.connect(
            server=server,
            port=port,
            user=user,
            password=password,
            database=database,
            login_timeout=30, timeout=120,
        )
        try:
            with conn.cursor(as_dict=True) as cur:
                cur.execute(sql, params or {})
                return cur.fetchall()
        finally:
            conn.close()


def _to_date(v):
    if not v:
        return False
    return v.date() if hasattr(v, 'date') else v


def _solo_digitos(s):
    """Deja solo numeros (para comparar NIT/cedula sin guiones ni digito de verificacion)."""
    return re.sub(r'\D', '', s or '')


def _sel_label(record, fieldname):
    """Devuelve la etiqueta legible de un campo Selection (no el codigo)."""
    if not record or fieldname not in record._fields or not record[fieldname]:
        return False
    sel = record._fields[fieldname].selection
    if callable(sel):
        try:
            sel = sel(record)
        except Exception:
            return record[fieldname]
    return dict(sel).get(record[fieldname], record[fieldname])


class PagosSiesaLine(models.Model):
    _name = 'pagos.siesa.line'
    _description = 'Pago real desde SIESA (se guarda al cargar)'
    _order = 'clase_tercero, contratista, fecha, consec'

    # --- Datos guardados (vienen de la consulta a SIESA) ---
    programapagos_id = fields.Many2one('programapagos', string='Programa de pagos',
                                       ondelete='cascade', index=True)
    # UN / proyecto de SIESA (ej. '15-160'). Se guarda en CADA linea para poder
    # consultar por proyecto sin pasar por programapagos. Lo usa el dashboard
    # del director: search([('id_un', '=', mcb_centro_costo)]).
    id_un = fields.Char(string='UN / Proyecto', index=True)
    currency_id = fields.Many2one('res.currency', string='Moneda')
    clase_tercero = fields.Char(string='Tipo', index=True)   # 'Contratista' / 'Compra/Proveedor'
    contratista = fields.Char(string='Tercero')
    nit = fields.Char(string='NIT/CC')
    consec = fields.Char(string='Causación')
    rcf = fields.Char(string='RPC/RCF')
    fecha = fields.Date(string='Fecha causacion')
    bruto = fields.Monetary(string='Bruto causado')
    # Retenciones desglosadas + total (el total suma las tres)
    retefuente = fields.Monetary(string='Retefuente')
    reteica = fields.Monetary(string='ReteICA')
    reteiva = fields.Monetary(string='ReteIVA')
    retencion = fields.Monetary(string='Retencion')
    neto = fields.Monetary(string='Neto a pagar')
    # OJO: el campo se sigue llamando deduccion_dd por compatibilidad, pero
    # incluye DD + AJ + NI + OAJ. El tipo y numero real va en doc_deduccion.
    deduccion_dd = fields.Monetary(string='Deducción')
    doc_deduccion = fields.Char(string='Doc. deduccion')
    pendiente = fields.Monetary(string='Pendiente por pagar',
                                help='Neto menos TODO lo aplicado (pago del banco + '
                                     'deducciones DD/AJ/NI/OAJ). Si es 0, quedo saldada.')

    # Estado del pago para la columna "Estado" (reemplaza los colores de fila).
    # Se calcula del pendiente real: Saldada / Parcial / Pendiente / Reversado.
    estado = fields.Char(string='Estado', compute='_compute_estado', store=True)

    dias = fields.Integer(string='Días', compute='_compute_dias',
                          help='Días desde la causación mientras sigue abierta '
                               '(Pendiente o Parcial). 0 = ya cerrada.')

    @api.depends('fecha', 'estado')
    def _compute_dias(self):
        hoy = fields.Date.context_today(self)
        for l in self:
            if l.fecha and l.estado in ('Pendiente', 'Parcial'):
                l.dias = (hoy - l.fecha).days
            else:
                l.dias = 0

    @api.depends('neto', 'pendiente')
    def _compute_estado(self):
        for l in self:
            neto = l.neto or 0.0
            pend = l.pendiente or 0.0
            if neto <= 0:
                l.estado = 'Reversado'
            elif -1 < pend < 1:
                l.estado = 'Saldada'
            elif pend < neto:
                l.estado = 'Parcial'
            else:
                l.estado = 'Pendiente'
    pe = fields.Char(string='Pago (PE/PD)')
    fecha_pago = fields.Date(string='Fecha de pago')
    valor_pagado = fields.Monetary(string='Pagado')
    pct_pago = fields.Float(string='% de la cuota')
    pct_acumulado = fields.Float(string='% acumulado')

    # ===================================================================
    #  DETALLE DEL CONTRATISTA (se calcula solo al abrir el detalle de una
    #  fila; NO se calcula por cada fila de la tabla). Trae info del contrato
    #  (Odoo) + el historial de pagos (Odoo planeado y SIESA real) + el
    #  resumen de retenciones que SUMA la columna de afuera.
    # ===================================================================
    contratista_odoo_id = fields.Many2one('contrataciones_fuv', string='Contratista (SGO)',
                                           compute='_compute_detalle_odoo', store=False)
    det_tiene_contrato = fields.Boolean(compute='_compute_detalle_odoo', store=False)
    det_id_contrato = fields.Char(string='ID contrato', compute='_compute_detalle_odoo', store=False)
    det_estado = fields.Char(string='Estado', compute='_compute_detalle_odoo', store=False)
    det_rpc = fields.Char(string='RPC', compute='_compute_detalle_odoo', store=False)
    det_cuotas = fields.Integer(string='# cuotas', compute='_compute_detalle_odoo', store=False)
    det_fecha_ini = fields.Date(string='Inicio', compute='_compute_detalle_odoo', store=False)
    det_fecha_fin = fields.Date(string='Fin', compute='_compute_detalle_odoo', store=False)
    det_valor_contrato = fields.Monetary(string='Valor contrato', currency_field='currency_id',
                                         compute='_compute_detalle_odoo', store=False)
    det_total_contrato = fields.Monetary(string='Total contrato', currency_field='currency_id',
                                         compute='_compute_detalle_odoo', store=False)
    hist_siesa_ids = fields.Many2many('pagos.siesa.line', compute='_compute_detalle_odoo',
                                      store=False, string='Detalle de la causación')
    hist_odoo_ids = fields.Many2many('contrato', compute='_compute_detalle_odoo',
                                     store=False, string='Pagos programados (SGO)')
    det_retefuente = fields.Monetary(string='Total Retefuente', currency_field='currency_id',
                                     compute='_compute_detalle_odoo', store=False)
    det_reteica = fields.Monetary(string='Total ReteICA', currency_field='currency_id',
                                  compute='_compute_detalle_odoo', store=False)
    det_reteiva = fields.Monetary(string='Total ReteIVA', currency_field='currency_id',
                                  compute='_compute_detalle_odoo', store=False)
    det_retencion_total = fields.Monetary(string='Total Retencion', currency_field='currency_id',
                                          compute='_compute_detalle_odoo', store=False)
    det_bruto = fields.Monetary(string='Total Bruto', currency_field='currency_id',
                                compute='_compute_detalle_odoo', store=False)
    det_neto = fields.Monetary(string='Total Neto', currency_field='currency_id',
                               compute='_compute_detalle_odoo', store=False)
    det_pagado = fields.Monetary(string='Total Pagado', currency_field='currency_id',
                                 compute='_compute_detalle_odoo', store=False)
    # --- Cuadre del contrato: valor contratado (Odoo) vs cobrado (SIESA) ---
    det_contratos_ids = fields.Many2many('contrataciones_fuv', compute='_compute_detalle_odoo',
                                         store=False, string='Contratos del tercero en el proyecto')
    det_num_contratos = fields.Integer(compute='_compute_detalle_odoo', store=False)
    det_valor_odoo_total = fields.Monetary(string='Valor contratado (con otrosi)',
                                           currency_field='currency_id',
                                           compute='_compute_detalle_odoo', store=False)
    det_diferencia = fields.Monetary(string='Diferencia', currency_field='currency_id',
                                     compute='_compute_detalle_odoo', store=False)
    det_cuadra = fields.Boolean(compute='_compute_detalle_odoo', store=False)
    det_es_convenio = fields.Boolean(compute='_compute_detalle_odoo', store=False)

    @api.depends('nit', 'programapagos_id')
    def _compute_detalle_odoo(self):
        Cfuv = self.env['contrataciones_fuv']
        Contrato = self.env['contrato']
        for line in self:
            pp = line.programapagos_id
            nd = _solo_digitos(line.nit or '')
            # 1) Match al contratista en Odoo (por NIT). Primero DENTRO del proyecto;
            #    si no aparece, se busca en TODA la empresa (por si el contrato quedo
            #    ligado a otro proyecto). Solo corre al abrir el detalle de una fila.
            match = Cfuv.browse()      # contrato principal (el primero)
            matches = Cfuv.browse()    # TODOS los contratos del NIT en el proyecto
            if nd:
                dominios = []
                if pp and pp.proyecto_relacionado:
                    dominios.append([('proyecto_relacionado', '=', pp.proyecto_relacionado.id)])
                dominios.append([])  # fallback: toda la empresa
                for dom in dominios:
                    for c in Cfuv.search(dom):
                        nits = {_solo_digitos(c.nit or ''),
                                _solo_digitos(getattr(c, 'nit2', '') or ''),
                                _solo_digitos(getattr(c, 'documento_identidad', '') or '')}
                        if nd in nits:
                            matches |= c
                    if matches:
                        break
                match = matches[:1]
            line.contratista_odoo_id = match.id if match else False
            line.det_tiene_contrato = bool(match)
            line.det_id_contrato = (getattr(match, 'id_del_contrato', '') or '') if match else False
            line.det_estado = _sel_label(match, 'estado_contratacion') if match else False
            line.det_rpc = (getattr(match, 'compromiso_presupuestal', '') or '') if match else False
            line.det_cuotas = (getattr(match, 'nuemor_de_cuotas', 0) or 0) if match else 0
            line.det_fecha_ini = (getattr(match, 'fecha_iniciacion', False) or False) if match else False
            line.det_fecha_fin = (getattr(match, 'fecha_terminacion', False) or False) if match else False
            line.det_valor_contrato = (getattr(match, 'valor_total_contrato', 0.0) or 0.0) if match else 0.0
            line.det_total_contrato = (getattr(match, 'total', 0.0) or 0.0) if match else 0.0
            # 2) Historial SIESA (hermanos con el mismo NIT). Via search para no
            #    disparar la auto-carga del One2many.
            hermanos = line.browse()
            if pp and nd:
                hermanos = self.search([('programapagos_id', '=', pp.id)]).filtered(
                    lambda l: _solo_digitos(l.nit or '') == nd)
            line.hist_siesa_ids = hermanos
            # 3) Historial Odoo (pagos programados del contratista emparejado)
            odoo_pagos = Contrato.browse()
            if pp and match and 'programapagos_ids' in pp._fields:
                odoo_pagos = pp.programapagos_ids.filtered(
                    lambda c: getattr(c, 'contratista1', False) and c.contratista1.id == match.id)
            line.hist_odoo_ids = odoo_pagos
            # 4) Resumen de retenciones del contratista (suma la columna de afuera)
            line.det_retefuente = sum(hermanos.mapped('retefuente'))
            line.det_reteica = sum(hermanos.mapped('reteica'))
            line.det_reteiva = sum(hermanos.mapped('reteiva'))
            line.det_retencion_total = sum(hermanos.mapped('retencion'))
            line.det_bruto = sum(hermanos.mapped('bruto'))
            line.det_neto = sum(hermanos.mapped('neto'))
            line.det_pagado = sum(hermanos.mapped('valor_pagado'))
            # 5) Cuadre del contrato (reglas de la reunion con financiera):
            #    valor contratado (con otrosi; sumando TODOS los contratos del
            #    NIT en el proyecto) vs valor cobrado (bruto causado en SIESA).
            #    Convenios (UN 15-xx): no debe quedar diferencia. Proyectos
            #    propios/interadministrativos (09-xx): un peso puede suceder.
            line.det_contratos_ids = matches
            line.det_num_contratos = len(matches)
            valor_odoo = 0.0
            for c in matches:
                t = getattr(c, 'total', 0.0) or 0.0
                if not t:
                    t = ((getattr(c, 'valor_total_contrato', 0.0) or 0.0)
                         + (getattr(c, 'valor_del_otrosi', 0.0) or 0.0))
                valor_odoo += t
            line.det_valor_odoo_total = valor_odoo
            dif = valor_odoo - line.det_bruto
            line.det_diferencia = dif
            line.det_cuadra = bool(matches) and abs(dif) < 0.005
            un = ''
            if pp:
                try:
                    un = pp._siesa_un_code() or ''
                except Exception:
                    un = ''
            line.det_es_convenio = bool(un) and un.startswith('15')


class ProgramaPagosSiesa(models.Model):
    """Extiende tu modelo 'programapagos' SIN tocar tu archivo original."""
    _inherit = 'programapagos'

    # One2many guardado: la tabla siempre se ve bien y dispara la auto-carga.
    pagos_siesa_ids = fields.One2many('pagos.siesa.line', 'programapagos_id',
                                      string='Pagos reales (SIESA)')
    pagos_siesa_last_sync = fields.Datetime(string='Ultima actualizacion SIESA', readonly=True)

    # Buscador en vivo (filtra sobre TODAS las filas, no solo la pagina visible).
    pagos_siesa_buscar = fields.Char(string='Buscar')

    # Selector de vista: el usuario elige QUE categoria ver.
    siesa_vista_sel = fields.Selection([
        ('Principales', 'Contratistas + Proveedores'),
        ('Contratista', 'Contratistas'),
        ('Compra/Proveedor', 'Compras / Proveedores'),
        ('Gasto', 'Gastos'),
        ('Nota/Reversion', 'Notas / Reversiones'),
    ], string='Ver', default='Principales')

    # Filas de la categoria elegida (+ filtro del buscador). Una sola tabla.
    pagos_filtro_estado = fields.Selection([
        ('todos', 'Todos'), ('Pendiente', 'Pendientes'), ('Parcial', 'Parciales'),
        ('Saldada', 'Saldadas'), ('Reversado', 'Reversados')],
        string='Estado', default='todos')
    pagos_vista_count = fields.Integer(compute='_compute_pagos_vista')
    pagos_vista_ids = fields.Many2many(
        'pagos.siesa.line', 'rel_pp_vista', compute='_compute_pagos_vista',
        string='Detalle del tercero')

    # OJO: NO poner 'pagos_siesa_ids' en el depends. El compute no lo usa (busca
    # con Line.search()), pero declararlo obliga a Odoo a CARGAR Y FOTOGRAFIAR
    # todas las lineas del proyecto en CADA tecla del buscador y en cada cambio
    # de selector -> lentitud y, peor, el <tree> se renderiza vacio con el
    # formulario sucio. 'pagos_siesa_last_sync' cambia en cada recarga, que es
    # justo cuando las lineas cambian: con eso alcanza para recomputar.
    @api.depends('pagos_siesa_last_sync', 'pagos_siesa_buscar', 'siesa_vista_sel', 'pagos_filtro_estado')
    def _compute_pagos_vista(self):
        """Busca DIRECTO en la base sobre TODAS las lineas del proyecto (todas las
        paginas de la tabla), no sobre lo que el navegador tenga cargado."""
        Line = self.env['pagos.siesa.line']
        for rec in self:
            origen = rec._origin if rec._origin else rec
            if not origen.id:
                rec.pagos_vista_ids = Line.browse()
                rec.pagos_vista_count = 0
                continue
            cat = rec.siesa_vista_sel or 'Principales'
            dominio = [('programapagos_id', '=', origen.id)]
            if cat == 'Principales':
                # Vista por defecto: contratistas + compras/proveedores juntos.
                dominio.append(('clase_tercero', 'in',
                                ['Contratista', 'Compra/Proveedor']))
            else:
                dominio.append(('clase_tercero', '=', cat))
            filtro = rec.pagos_filtro_estado or 'todos'
            if filtro != 'todos':
                dominio.append(('estado', '=', filtro))
            q = (rec.pagos_siesa_buscar or '').strip()
            if q:
                # Cada palabra escrita debe aparecer en ALGUN campo. Asi
                # "manuel rojas" tambien encuentra a "ROJAS CUBILLOS MANUEL".
                for term in q.split():
                    dominio += ['|', '|', '|', '|',
                                ('contratista', 'ilike', term),
                                ('nit', 'ilike', term),
                                ('consec', 'ilike', term),
                                ('rcf', 'ilike', term),
                                ('pe', 'ilike', term)]
            res = Line.search(dominio)
            rec.pagos_vista_ids = res
            rec.pagos_vista_count = len(res)

    # --- Moneda + totales del resumen (reflejan lo que se ve: contratistas + compras) ---
    siesa_currency_id = fields.Many2one('res.currency', compute='_compute_siesa_currency')
    total_siesa_bruto = fields.Monetary(string='Bruto causado', currency_field='siesa_currency_id',
                                        compute='_compute_totales_siesa')
    total_siesa_retefuente = fields.Monetary(string='Retefuente', currency_field='siesa_currency_id',
                                             compute='_compute_totales_siesa')
    total_siesa_reteica = fields.Monetary(string='ReteICA', currency_field='siesa_currency_id',
                                          compute='_compute_totales_siesa')
    total_siesa_reteiva = fields.Monetary(string='ReteIVA', currency_field='siesa_currency_id',
                                          compute='_compute_totales_siesa')
    total_siesa_retencion = fields.Monetary(string='Retencion', currency_field='siesa_currency_id',
                                            compute='_compute_totales_siesa')
    total_siesa_neto = fields.Monetary(string='Neto a pagar', currency_field='siesa_currency_id',
                                       compute='_compute_totales_siesa')
    total_siesa_pagado = fields.Monetary(string='Pagado', currency_field='siesa_currency_id',
                                         compute='_compute_totales_siesa')
    total_siesa_saldo = fields.Monetary(string='Pendiente por pagar', currency_field='siesa_currency_id',
                                        compute='_compute_totales_siesa')
    total_siesa_pct = fields.Float(string='% pagado', compute='_compute_totales_siesa')
    total_siesa_count = fields.Integer(string='Causaciones', compute='_compute_totales_siesa')

    def _compute_siesa_currency(self):
        cop = self.env.ref('base.COP', raise_if_not_found=False)
        for rec in self:
            rec.siesa_currency_id = cop.id if cop else False

    @api.depends('pagos_vista_ids')
    def _compute_totales_siesa(self):
        for rec in self:
            lines = rec.pagos_vista_ids
            rec.total_siesa_bruto = sum(lines.mapped('bruto'))
            rec.total_siesa_retefuente = sum(lines.mapped('retefuente'))
            rec.total_siesa_reteica = sum(lines.mapped('reteica'))
            rec.total_siesa_reteiva = sum(lines.mapped('reteiva'))
            rec.total_siesa_retencion = sum(lines.mapped('retencion'))
            rec.total_siesa_neto = sum(lines.mapped('neto'))
            rec.total_siesa_pagado = sum(lines.mapped('valor_pagado'))
            # Pendiente real: suma del pendiente por causacion (ya descuenta las
            # deducciones DD/AJ/NI/OAJ). Antes era neto - pagado, lo que inflaba
            # el pendiente en las causaciones que se saldaron con una deduccion.
            rec.total_siesa_saldo = sum(lines.mapped('pendiente'))
            rec.total_siesa_count = len(lines)
            rec.total_siesa_pct = (100.0 * rec.total_siesa_pagado / rec.total_siesa_neto
                                   if rec.total_siesa_neto else 0.0)

    # --- Parche: el modelo original declara 'acomulado_por_contratista' con un
    #     compute que nunca se definio. Lo definimos en 0 sin tocar tu archivo.
    def _compute_acomulado_por_contratista(self):
        for rec in self:
            rec.acomulado_por_contratista = 0.0

    # ---- AUTO-CARGA al abrir el proyecto (UNA sola vez) --------------------
    # NOTA: aqui habia un override de read() que refrescaba SIESA cada vez que
    # se leia el formulario. Se ELIMINO a proposito: read() se llama muchisimo
    # (chatter, bus de notificaciones, writes de fondo, tareas programadas) y eso
    # disparaba borrado+recreacion de lineas en bucle, sin que nadie entrara.
    # Ahora SIESA se consulta SOLO por accion explicita del usuario:
    #   - boton "Pagos del proyecto"  -> action_ingresar_programa_pagos (abajo)
    #   - boton "Refrescar"           -> action_cargar_pagos_siesa
    # La primera vez que un proyecto no tiene datos, se cargan al abrir por el
    # boton (que es la unica via para llegar a esta seccion).

    def _necesita_refrescar(self):
        # Solo se usa como red de seguridad para la carga inicial. El refresco
        # real ya no depende de lecturas: lo fuerzan los botones (ver abajo).
        self.ensure_one()
        return not self.pagos_siesa_last_sync

    def _siesa_un_code(self):
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

    def action_cargar_pagos_siesa(self):
        """Boton: fuerza el refresco YA."""
        for rec in self:
            rec._cargar_pagos_siesa(force=True)
        return True

    def action_guia_tecnica_siesa(self):
        """Boton: abre la guia tecnica del modulo (explicacion para dummies)."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Guia tecnica - Pagos SIESA',
            'res_model': 'pagos.siesa.guia.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_dummy': True},
        }


    def action_limpiar_busqueda_siesa(self):
        """Boton 'x': borra el texto del buscador y recarga la tabla completa."""
        for rec in self:
            rec.pagos_siesa_buscar = False
        return True

    def action_exportar_excel_siesa(self):
        """Boton Excel: abre el mini-formulario para elegir que categorias descargar."""
        self.ensure_one()
        sel = self.siesa_vista_sel or 'Contratista'
        return {
            'type': 'ir.actions.act_window',
            'name': 'Descargar Excel',
            'res_model': 'pagos.siesa.export.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_programapagos_id': self.id,
                'default_incluir_contratistas': sel in ('Contratista', 'Principales'),
                'default_incluir_compras': sel in ('Compra/Proveedor', 'Principales'),
                'default_incluir_gastos': sel == 'Gasto',
                'default_incluir_notas': sel == 'Nota/Reversion',
            },
        }





    @api.model
    def cron_sync_all_siesa(self):
        """Sincroniza masivamente pagos y cobros para todos los proyectos activos."""
        self.env.cr.execute("SELECT id FROM contratacion")
        ids = [row[0] for row in self.env.cr.fetchall()]
        
        _logger.info(f"Cron SIESA: Found {len(ids)} contratacion ids")
        
        count = 0
        for pid in ids:
            self.env.cr.execute("SELECT centro_costo FROM mcb WHERE contrataciones_id = %s", (pid,))
            res = self.env.cr.fetchone()
            if not res or not res[0]:
                continue
                
            cc = res[0]
            count += 1
            
            # Pagos
            prog = self.env['programapagos'].search([('proyecto_relacionado', '=', pid)], limit=1)
            if not prog:
                try:
                    prog = self.env['programapagos'].create({'proyecto_relacionado': pid})
                except Exception as e:
                    pass
            if prog:
                try:
                    prog._cargar_pagos_siesa(force=True)
                except Exception as e:
                    pass
                
            # Cobros
            cobro = self.env['action_ingresar_pagos_proyecto'].search([('proyecto_relacionado', '=', pid)], limit=1)
            if not cobro:
                try:
                    cobro = self.env['action_ingresar_pagos_proyecto'].create({'proyecto_relacionado': pid})
                except Exception as e:
                    pass
            if cobro:
                try:
                    cobro._cargar_cobros_siesa(force=True)
                except Exception as e:
                    pass
                    
        _logger.info(f"Cron SIESA: Sincronizacion finalizada para {count} proyectos.")

    def _cargar_pagos_siesa(self, force=False):
        """Trae los pagos reales desde SIESA y los guarda (reemplaza los anteriores)."""
        self.ensure_one()
        self = self.with_context(siesa_skip_autoload=True)
        if not force and not self._necesita_refrescar():
            return

        Line = self.env['pagos.siesa.line']
        cop = self.env.ref('base.COP', raise_if_not_found=False)
        cop_id = cop.id if cop else False
        un = self._siesa_un_code()

        rows = []
        if un:
            rows = self.env['siesa.mssql.mixin']._run_siesa_query(QUERY_PAGOS, {'un': un})

        lineas_anteriores = Line.search_count([('programapagos_id', '=', self.id)])
        with _silenciar_log_unlink_siesa():
            self.pagos_siesa_ids.unlink()
        vals = []
        for r in rows:
            vals.append({
                'programapagos_id': self.id,
                'id_un': un,
                'currency_id': cop_id,
                'clase_tercero': r.get('clase_tercero'),
                'contratista': r.get('contratista'),
                'nit': r.get('nit'),
                'consec': str(r.get('consec') or ''),
                'fecha': _to_date(r.get('fecha')),
                'bruto': float(r.get('bruto') or 0.0),
                'retefuente': float(r.get('retefuente') or 0.0),
                'reteica': float(r.get('reteica') or 0.0),
                'reteiva': float(r.get('reteiva') or 0.0),
                'retencion': float(r.get('retencion') or 0.0),
                'neto': float(r.get('neto') or 0.0),
                'deduccion_dd': float(r.get('deduccion_dd') or 0.0),
                'doc_deduccion': r.get('doc_deduccion') or False,
                'pendiente': float(r.get('pendiente') or 0.0),
                'rcf': (r.get('rcf') or '').strip() or False,
                'pe': r.get('pe'),
                'fecha_pago': _to_date(r.get('fecha_pago')),
                'valor_pagado': float(r.get('valor_pagado') or 0.0),
                'pct_pago': float(r.get('pct_pago') or 0.0),
                'pct_acumulado': float(r.get('pct_acumulado') or 0.0),
            })
        if vals:
            Line.create(vals)
        self.sudo().write({'pagos_siesa_last_sync': fields.Datetime.now()})
        #ESTE LOG NOS PERMITE REVISAR SI EL CRON ESTA FUNCIONANDO CORRECTAMENTE, YA QUE NO HAY VISTA PARA VER ESTO
        _logger.info(
            "SIESA Pagos: refresco ejecutado | programa_id=%s proyecto_id=%s UN=%s eliminadas=%s creadas=%s force=%s",
            self.id,
            self.proyecto_relacionado.id if self.proyecto_relacionado else False,
            un or 'sin_un',
            lineas_anteriores,
            len(vals),
            force,
        )


# ===========================================================================
#  Boton "Pagos del proyecto" (modelo 'ejecucion'): al entrar por ese boton,
#  la tabla SIESA se refresca automaticamente (respetando el colchon de
#  siesa.refresh_seconds). NO toca Ejecucion.py: es un _inherit desde aca.
# ===========================================================================
class EjecucionPagosSiesa(models.Model):
    _inherit = 'ejecucion'

    def action_ingresar_programa_pagos(self):
        accion = super().action_ingresar_programa_pagos()
        # Refresca SIESA UNA vez, aqui, por CADA pulsada del boton (accion
        # explicita del usuario). No se usa read() para esto, asi no hay
        # refrescos automaticos en bucle por lecturas de fondo.
        try:
            pps = self._siesa_programapagos_de_accion(accion)
            for pp in pps:
                pp._cargar_pagos_siesa(force=True)
        except Exception as e:
            _logger.warning("Refresco SIESA al entrar a Pagos del proyecto: %s", e)
        return accion

    def _siesa_programapagos_de_accion(self, accion):
        """Saca el/los programapagos a los que apunta la accion del boton, para
        refrescarlos. Soporta que la accion abra un registro (res_id) o varios
        (domain/active_ids)."""
        PP = self.env['programapagos']
        if not isinstance(accion, dict):
            return PP
        ctx = accion.get('context') or {}
        if accion.get('res_model') == 'programapagos':
            if accion.get('res_id'):
                return PP.browse(accion['res_id'])
            if ctx.get('active_ids'):
                return PP.browse(ctx['active_ids'])
            dom = accion.get('domain')
            if dom:
                try:
                    return PP.search(dom)
                except Exception:
                    return PP
        return PP


# ===========================================================================
#  WIZARD: descargar los pagos SIESA en Excel, eligiendo que categorias van.
#  Cada categoria elegida sale en su PROPIA hoja, con formato y fila de totales.
#  El archivo ademas queda adjunto al proyecto (clip del chatter).
# ===========================================================================
NOMBRES_HOJA_EXPORT = {
    'Contratista': 'Contratistas',
    'Compra/Proveedor': 'Compras y proveedores',
    'Gasto': 'Gastos',
    'Nota/Reversion': 'Notas y reversiones',
}


class PagosSiesaExportWizard(models.TransientModel):
    _name = 'pagos.siesa.export.wizard'
    _description = 'Descargar pagos SIESA en Excel'

    programapagos_id = fields.Many2one('programapagos', string='Proyecto', required=True)
    incluir_contratistas = fields.Boolean(string='Contratistas')
    incluir_compras = fields.Boolean(string='Compras / Proveedores')
    incluir_gastos = fields.Boolean(string='Gastos')
    incluir_notas = fields.Boolean(string='Notas / Reversiones')

    def action_exportar(self):
        self.ensure_one()
        if xlsxwriter is None:
            raise UserError("Falta la libreria 'xlsxwriter' en el servidor del SGO. Pidele al administrador instalarla con: "
                            "Instalala con: docker exec -u root -it gofuv pip install xlsxwriter")
        clases = []
        if self.incluir_contratistas:
            clases.append('Contratista')
        if self.incluir_compras:
            clases.append('Compra/Proveedor')
        if self.incluir_gastos:
            clases.append('Gasto')
        if self.incluir_notas:
            clases.append('Nota/Reversion')
        if not clases:
            raise UserError("Elegi al menos una categoria para descargar.")

        pp = self.programapagos_id
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        f_head = wb.add_format({'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#1C2B5A',
                                'border': 1, 'border_color': '#16234A',
                                'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
        f_money = wb.add_format({'num_format': '#,##0.00;[Red]-#,##0.00'})
        f_pct = wb.add_format({'num_format': '0.00"%"'})
        f_date = wb.add_format({'num_format': 'dd/mm/yyyy', 'align': 'center'})
        f_tot_txt = wb.add_format({'bold': True, 'top': 2})
        f_tot_money = wb.add_format({'num_format': '#,##0.00;[Red]-#,##0.00', 'bold': True, 'top': 2})

        columnas = [
            ('Tercero', 34), ('NIT/CC', 14), ('RPC/RCF', 16), ('Causacion', 12),
            ('Fecha causacion', 14), ('Bruto causado', 15), ('Retefuente', 13),
            ('ReteICA', 12), ('ReteIVA', 12), ('Retencion total', 14), ('Neto a pagar', 15),
            ('Deduccion (DD/AJ/NI)', 16), ('Doc. deduccion', 20), ('Pago (PE/PD)', 18),
            ('Fecha de pago', 14), ('Pagado', 15), ('Pendiente', 15), ('% acumulado', 12),
        ]
        sumas = [(5, 'bruto'), (6, 'retefuente'), (7, 'reteica'), (8, 'reteiva'),
                 (9, 'retencion'), (10, 'neto'), (11, 'deduccion_dd'),
                 (15, 'valor_pagado'), (16, 'pendiente')]

        for clase in clases:
            lineas = pp.pagos_siesa_ids.filtered(lambda l: l.clase_tercero == clase)
            ws = wb.add_worksheet(NOMBRES_HOJA_EXPORT.get(clase, clase)[:31])
            ws.freeze_panes(1, 0)
            ws.set_row(0, 24)
            for i, (titulo, ancho) in enumerate(columnas):
                ws.set_column(i, i, ancho)
                ws.write(0, i, titulo, f_head)
            fila = 1
            for l in lineas:
                ws.write(fila, 0, l.contratista or '')
                ws.write(fila, 1, l.nit or '')
                ws.write(fila, 2, l.rcf or '')
                ws.write(fila, 3, l.consec or '')
                if l.fecha:
                    ws.write_datetime(fila, 4, l.fecha, f_date)
                ws.write_number(fila, 5, l.bruto or 0.0, f_money)
                ws.write_number(fila, 6, l.retefuente or 0.0, f_money)
                ws.write_number(fila, 7, l.reteica or 0.0, f_money)
                ws.write_number(fila, 8, l.reteiva or 0.0, f_money)
                ws.write_number(fila, 9, l.retencion or 0.0, f_money)
                ws.write_number(fila, 10, l.neto or 0.0, f_money)
                ws.write_number(fila, 11, l.deduccion_dd or 0.0, f_money)
                ws.write(fila, 12, l.doc_deduccion or '')
                ws.write(fila, 13, l.pe or '')
                if l.fecha_pago:
                    ws.write_datetime(fila, 14, l.fecha_pago, f_date)
                ws.write_number(fila, 15, l.valor_pagado or 0.0, f_money)
                ws.write_number(fila, 16, l.pendiente or 0.0, f_money)
                ws.write_number(fila, 17, l.pct_acumulado or 0.0, f_pct)
                fila += 1
            ws.write(fila, 0, 'TOTAL  (%d causaciones)' % len(lineas), f_tot_txt)
            for col, campo in sumas:
                ws.write_number(fila, col, sum(lineas.mapped(campo)) or 0.0, f_tot_money)
        wb.close()

        try:
            un = pp._siesa_un_code() or ''
        except Exception:
            un = ''
        nombre = 'Pagos_SIESA_%s_%s.xlsx' % (un or 'proyecto', fields.Date.context_today(self))
        nombre = re.sub(r'[^A-Za-z0-9_.\-]+', '_', nombre)
        att = self.env['ir.attachment']._drive_create_temp_download({
            'name': nombre,
            'type': 'binary',
            'datas': base64.b64encode(buf.getvalue()),
            'res_model': 'programapagos',
            'res_id': pp.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % att.id,
            'target': 'self',
        }


# ===========================================================================
#  WIZARD: guia tecnica del modulo (se abre con el boton "Guia tecnica").
#  No tiene logica; todo el contenido explicativo vive en su vista (form).
# ===========================================================================
class PagosSiesaGuiaWizard(models.TransientModel):
    _name = 'pagos.siesa.guia.wizard'
    _description = 'Guia tecnica - Pagos SIESA'

    dummy = fields.Boolean(default=True)

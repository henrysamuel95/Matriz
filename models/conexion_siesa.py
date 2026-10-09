from odoo import models, fields, api
import pymssql
import logging
import re
from datetime import datetime

_logger = logging.getLogger(__name__)







class ResumenTercero(models.Model):
    _name = "tabla.resumen.tercero"
    _description = "Resumen por Tercero"
    _rec_name = "razon_social"

    tercero = fields.Char(string="Cédula o Nit")
    razon_social = fields.Char(string="Razón Social")
    linea_ids = fields.One2many('tabla.prueba', 'resumen_id', string="Movimientos Detallados")
    tipos_documentos = fields.Char(string="Tipos de Doc.")
    sql_id = fields.Many2one('sql', string="Integración SQL")
    id_un = fields.Char(string="Unidad de Negocio")
    # Totales calculados para la vista de resumen
    total_debitos = fields.Monetary(string="Total Débitos", compute="_compute_totales", currency_field='currency_id')
    total_creditos = fields.Monetary(string="Total Créditos", compute="_compute_totales", currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Moneda', default=lambda self: self.env.company.currency_id)

    @api.depends('linea_ids')
    def _compute_totales(self):
        for record in self:
            record.total_debitos = sum(record.linea_ids.mapped('debitos'))
            record.total_creditos = sum(record.linea_ids.mapped('creditos'))
            
            
            
            
            
            
class TablaPrueba(models.Model):
    _name = "tabla.prueba"
    _description = "Registros de Tabla para prueba"
    resumen_id = fields.Many2one('tabla.resumen.tercero', string="Resumen Relacionado", ondelete='cascade')
    tercero = fields.Char(string="Cédula o Nit")
    movimiento = fields.Char(string="Movimiento")
    notas = fields.Char(string="Notas")
    nombre_auxiliar = fields.Char(string="Nombre auxiliar")
    vendedor = fields.Char(string="Vendedor")
    debitos = fields.Monetary(string="Débito")
    creditos = fields.Monetary(string="Crédito")
    valor_pago = fields.Monetary(string="Valor Pago (PE)")
    valor_deduccion = fields.Monetary(string="Valor Deducción (DD)")
    
    # 🟢 CAMPOS CLAVE PARA LA CONCILIACIÓN
    tipo_cruzado = fields.Char(string="Tipo Cruzado")
    nro_cruzado = fields.Char(string="Nro Cruzado")
    
    total_pagado = fields.Monetary(string="Total Pagado") # 🟢 Nueva variable
    currency_id = fields.Many2one('res.currency', string='Moneda')
    id_tipo_docto = fields.Char(string="Tipo documento")
    nro_docto = fields.Char(string="# Documento")
    fecha_docto = fields.Date(string="Fecha documento")
    id_un = fields.Char(string="Unidad de negocio")
    
    sql_id = fields.Many2one('sql', string="Integración SQL")
    vista_sql_id = fields.Many2one("vistas_sql", string="Vista SQL Relacionada")

    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{record.razon_social} ({record.id_un})"

    @api.depends('linea_ids')
    def _compute_totales(self):
        for record in self:
            record.total_debitos = sum(record.linea_ids.mapped('debitos'))
            record.total_creditos = sum(record.linea_ids.mapped('creditos'))
            
    # Campos que ya no vienen en la consulta principal pero permanecen en el modelo
    valor_retenciones = fields.Monetary(string="Retenciones")
    valor_aplicado_pp = fields.Monetary(string="Descuento Pronto Pago")
    valor_aprovecha = fields.Monetary(string="Aprovechamientos")
    
    razon_social = fields.Char(string="Razón Social")

    categoria = fields.Selection([
        ('Pago', 'Pago'),
        ('Causacion', 'Causación'),
        ('Legalizacion', 'Legalización'),
        ('Otro', 'Otro'),
    ], string="Categoría", compute="_compute_categoria", store=True)

    @api.depends('id_tipo_docto')
    def _compute_categoria(self):
        for record in self:
            tipo = str(record.id_tipo_docto or "").strip()
            if tipo in ('PE', 'PD', 'PN', 'PSS'):
                record.categoria = 'Pago'
            elif tipo in ('FP', 'FT', 'FS', 'S4', 'S5', 'CC', 'CJ'):
                record.categoria = 'Causacion'
            elif tipo in ('FR', 'FL', 'A1', 'LFE', 'LSE', 'LOD'):
                record.categoria = 'Legalizacion'
            else:
                record.categoria = 'Otro'

    def name_get(self):
        return [(record.id, record.tercero or 'Sin nombre') for record in self]

class SQLIntegration(models.Model):
    _name = "sql"
    _description = "SQL Database Integration"

    available_tables = fields.Text(string="Tablas Disponibles", readonly=True)
    selected_table = fields.Char(string="Tabla Seleccionada", default="BI_T351")
    table_fields = fields.Text(string="Campos de la Tabla", readonly=True)
    primary_keys = fields.Text(string="Claves Primarias", readonly=True)
    one2many_prueba = fields.One2many('tabla.prueba', 'sql_id', string="Registros TablaPrueba")
    currency_id = fields.Many2one('res.currency', string='Moneda')
    
    _connection = None

    @classmethod
    def get_connection(cls):
        try:
            if cls._connection:
                cls._connection.cursor().execute("SELECT 1")
                return cls._connection
        except Exception:
            cls._connection = None

        try:
            cls._connection = pymssql.connect(
                server='siesadb',                               # 1. Nombre del contenedor en la red de Docker Compose
                port=1433,                                      # Puerto estándar de SQL Server
                user='odoo_lectura',                            # 2. El usuario que creamos y mapeamos con permisos de lectura
                password='ClaveFuerte_2026!',                   # 3. La contraseña que le asignamos a ese usuario en los scripts anteriores
                database='UnoEE_FundacionUniValle_Real',        # 4. El nombre exacto de la base de datos que restauramos desde el .bak
                login_timeout=60,
                timeout=60
            )
            return cls._connection
        except Exception as e:
            _logger.error(f"Error de conexión: {e}")
            raise

    @staticmethod
    def limpiar_cedula(cedula):
        return re.sub(r'\D', '', str(cedula)) if cedula else ""

    def _get_categoria(self, tipo):
        tipo = str(tipo or "").strip()
        if tipo in ('PE', 'PD', 'PN', 'PSS'):
            return 'Pago'
        if tipo in ('FP', 'FT', 'FS', 'S4', 'S5', 'CC', 'CJ'):
            return 'Causacion'
        if tipo in ('FR', 'FL', 'A1', 'LFE', 'LSE', 'LOD'):
            return 'Legalizacion'
        return 'Otro'

    def sync_tabla_prueba(self):
        try:
            conn = self.get_connection()
            
            # 1. LA CONSULTA MAESTRA (Tal cual la tienes, sin filtros de tercero)
            query_master = """
                SELECT 
                    t.f200_nit AS NIT,
                    t.f200_razon_social AS Razon_Social,
                    m.f351_id_un AS Unidad_Negocio,
                    d.f350_id_tipo_docto AS Tipo,
                    d.f350_consec_docto AS Numero,
                    d.f350_fecha AS Fecha,
                    m.f351_valor_db AS Debito,
                    m.f351_valor_cr AS Credito,
                    sa.f354_id_tipo_docto_cruce AS Tipo_Cruzado,
                    sa.f354_consec_docto_cruce AS Nro_Cruzado
                FROM [UnoEE_FundacionUniValle_Real].[dbo].[t350_co_docto_contable] d
                INNER JOIN [UnoEE_FundacionUniValle_Real].[dbo].[t351_co_mov_docto] m 
                    ON d.f350_rowid = m.f351_rowid_docto
                LEFT JOIN [UnoEE_FundacionUniValle_Real].[dbo].[t354_co_mov_saldo_abierto] sa 
                    ON m.f351_rowid = sa.f354_rowid_mov_docto
                INNER JOIN [UnoEE_FundacionUniValle_Real].[dbo].[t200_mm_terceros] t 
                    ON m.f351_rowid_tercero = t.f200_rowid
                WHERE d.f350_ind_estado = 1 
                  AND d.f350_id_clase_docto IN (0, 21, 38, 55)
            """
            
            with conn.cursor(as_dict=True) as cursor:
                cursor.execute(query_master)
                movimientos = cursor.fetchall()

            if not movimientos:
                return self._notify("No se encontraron datos en Siesa.", tipo="warning")

            # 2. LIMPIEZA TOTAL: Borramos lo anterior para no duplicar
            self.env['tabla.prueba'].search([('sql_id', '=', self.id)]).unlink()

            # 3. MAPEADO DIRECTO: Un registro en SQL = Un registro en Odoo
            registros_batch = []
            for row in movimientos:
                # Limpiamos el NIT para evitar decimales como .0
                nit_raw = row.get("NIT") or row.get("nit")
                nit = self.limpiar_cedula(nit_raw) if nit_raw else ""
                
                # Aseguramos que el Nro_Cruzado sea texto para evitar errores de vista
                valor_cruzado = row.get("Nro_Cruzado") if row.get("Nro_Cruzado") is not None else row.get("nro_cruzado")
 
                registros_batch.append({
                    'tercero': nit,
                    'razon_social': (row.get("Razon_Social") or row.get("razon_social") or "Sin Nombre")[:128],
                    'id_un': str(row.get("Unidad_Negocio") or row.get("unidad_negocio") or "").strip(),
                    'id_tipo_docto': row.get("Tipo") or row.get("tipo"),
                    'nro_docto': str(row.get("Numero") or row.get("numero")),
                    'fecha_docto': row.get("Fecha") or row.get("fecha"),
                    'debitos': float(row.get("Debito") or row.get("debito") or 0.0),
                    'creditos': float(row.get("Credito") or row.get("credito") or 0.0),
                    'tipo_cruzado': row.get("Tipo_Cruzado") or row.get("tipo_cruzado"),
                    'nro_cruzado': str(int(valor_cruzado)) if valor_cruzado and str(valor_cruzado).replace('.0','').isdigit() else False,
                    'sql_id': self.id,
                })

            # 4. CARGA MASIVA
            if registros_batch:
                self.env['tabla.prueba'].create(registros_batch)
                self.env.cr.commit()
                
                # Recalcular costos Siesa e índices EVM para todos los proyectos del tablero
                proyectos = self.env['tablero_avances'].sudo().search([])
                proyectos._compute_costo_real_siesa()
                proyectos._compute_valor_pagado_dashboard()
                proyectos._compute_proyecto_evm()
                self.env.cr.commit()

            return self._notify(f"Sincronización Exitosa: {len(registros_batch)} registros cargados.")

        except Exception as e:
            _logger.error(f"Error en sincronización SQL: {e}")
            return self._notify(f"Error Crítico: {e}", tipo="danger")
        
        
    def action_ver_movimientos_validados(self):
        self.ensure_one()
        
        # Buscamos el ID de la vista tree que creamos arriba
        view_id = self.env.ref('Matriz.view_tabla_prueba_conciliacion_tree').id
        
        domain = [
            ('sql_id', '=', self.id),
            '|',
            '&', ('id_tipo_docto', 'in', ['FP', 'S4']), ('nro_cruzado', '=', False),
            ('nro_cruzado', '!=', False)
        ]
        
        return {
            'name': 'Conciliación Universal de Pagos',
            'type': 'ir.actions.act_window',
            'res_model': 'tabla.prueba',
            'view_mode': 'tree,form',
            'views': [(view_id, 'tree'), (False, 'form')], # Forzamos nuestra vista
            'domain': domain,
            'search_view_id': self.env.ref('Matriz.view_tabla_prueba_search').id, # Forzamos el search
            'context': {'group_by': ['tercero', 'nro_cruzado'], 'search_default_group_tercero': 1},
        }
        
    def _notify(self, message, tipo="success", sticky=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': tipo, 'sticky': sticky}
        }
from odoo import models, fields, api
import pymssql
import logging
import re
from datetime import datetime

_logger = logging.getLogger(__name__)


class TablaPrueba(models.Model):
    _name = "tabla.prueba"
    _description = "Registros de Tabla para prueba"

    # Campos de BI_T351
    tercero = fields.Char(string="Cédula o Nit")
    movimiento = fields.Char(string="Movimiento")
    notas = fields.Char(string="Notas")
    nombre_auxiliar = fields.Char(string="Nombre auxiliar")
    vendedor = fields.Char(string="Vendedor")
    debitos = fields.Monetary(string="Débito")
    creditos = fields.Monetary(string="Crédito")  # 🟢 Nuevo campo
    currency_id = fields.Many2one('res.currency', string='Moneda')
    id_tipo_docto = fields.Char(string="Tipo documento")
    nro_docto = fields.Char(string="# Documento")
    fecha_docto = fields.Date(string="Fecha documento")
    id_un = fields.Char(string="Unidad de negocio")
    sql_id = fields.Many2one('sql', string="Integración SQL")
    vista_sql_id = fields.Many2one("vistas_sql", string="Vista SQL Relacionada")

    # Campos de w0980_sinc_terceros
    razon_social = fields.Char(string="Razón Social")

    #Nuevo campo agregado
    categoria = fields.Selection([
        ('Pago', 'Pago'),
        ('Causacion', 'Causación'),
        ('Legalizacion', 'Legalización'),
        ('Otro', 'Otro'),
    ], string="Categoría")


    def name_get(self):
        return [(record.id, record.tercero or 'Sin nombre') for record in self]

    def abrir_detalle_documento(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Detalle del documento',
            'res_model': 'tabla.prueba',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }


class SQLIntegration(models.Model):
    _name = "sql"
    _description = "SQL Database Integration"

    available_tables = fields.Text(string="Tablas Disponibles", readonly=True)
    selected_table = fields.Char(string="Tabla Seleccionada", default="BI_T351")
    table_fields = fields.Text(string="Campos de la Tabla", readonly=True)
    primary_keys = fields.Text(string="Claves Primarias", readonly=True)
    one2many_prueba = fields.One2many('tabla.prueba', 'sql_id', string="Registros TablaPrueba")

    _connection = None

    @classmethod
    def get_connection(cls):
        try:
            if cls._connection:
                cls._connection.cursor().execute("SELECT 1")
                return cls._connection
        except Exception:
            _logger.warning("La conexión anterior se perdió, intentando reconectar...")
            cls._connection = None

        try:
            cls._connection = pymssql.connect(
                server='172.24.48.1',
                port=1433,
                user='odoo_user',
                password='P@ssw0rd_123',
                database='UnoEE_FundacionUniValle_Real',
                login_timeout=60,
                timeout=60
            )
            _logger.info("Conexión a SQL Server (pymssql) exitosa.")
        except Exception as e:
            _logger.error(f"Error inesperado al conectar a SQL Server: {e}")
            raise

        return cls._connection

    @staticmethod
    def limpiar_cedula(cedula):
        return re.sub(r'\D', '', cedula) if cedula else None

    @staticmethod
    def convertir_fecha(fecha_str):
        if not fecha_str:
            return None

        if re.match(r'^\d{8}$', fecha_str):
            try:
                return datetime.strptime(fecha_str, "%Y%m%d").date()
            except ValueError:
                return None

        formatos = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"]
        for fmt in formatos:
            try:
                return datetime.strptime(fecha_str.strip(), fmt).date()
            except Exception:
                continue

        return None



    def sync_tabla_prueba(self):
        try:
            conn = self.get_connection()

            # 1️⃣ Query de movimientos (BI_T351)
            query_mov = f"""
                SELECT id_un, tercero, id_tipo_docto, nro_docto, fecha_docto,
                    movimiento, debitos, creditos, notas, vendedor, nombre_auxiliar
                FROM {self.selected_table}
            """
            with conn.cursor(as_dict=True) as cursor:
                cursor.execute(query_mov)
                movimientos = cursor.fetchall()

            # 2️⃣ Query de maestros (w0980_sinc_terceros)
            query_maestros = """
                SELECT c0980_id, c0980_nit, c0980_razon_social
                FROM dbo.w0980_sinc_terceros
            """
            with conn.cursor(as_dict=True) as cursor:
                cursor.execute(query_maestros)
                maestros = cursor.fetchall()

            # 3️⃣ Diccionario indexado por ID o NIT
            maestro_dict = {}
            for row in maestros:
                if row.get("c0980_id"):
                    maestro_dict[str(row["c0980_id"]).strip()] = row
                if row.get("c0980_nit"):
                    maestro_dict[str(row["c0980_nit"]).strip()] = row

            if not movimientos:
                return self._notify("No se encontraron registros.", tipo="warning")

            # 4️⃣ Eliminar registros anteriores
            self.env['tabla.prueba'].search([]).unlink()

            # 5️⃣ Preparar registros nuevos
            registros = []
            for row in movimientos:
                tercero = self.limpiar_cedula(row.get("tercero"))
                fecha = self.convertir_fecha(row.get("fecha_docto"))

                # Buscar en maestros por cédula/nit
                maestro = maestro_dict.get(tercero, {})

                # 🟢 Determinar categoría (equivalente al CASE del SQL)
                tipo = (row.get("id_tipo_docto") or "").strip()
                if tipo in ('PE', 'PD', 'PN', 'PSS'):
                    categoria = 'Pago'
                elif tipo in ('FP', 'FT', 'FS', 'S4', 'S5', 'A1', 'CC', 'CJ'):
                    categoria = 'Causacion'
                elif tipo in ('FR', 'FL', 'A1', 'LFE', 'LSE', 'LOD'):
                    categoria = 'Legalizacion'
                else:
                    categoria = 'Otro'

                registros.append({
                    'id_un': row.get("id_un"),
                    'tercero': tercero,
                    'id_tipo_docto': tipo,
                    'nro_docto': row.get("nro_docto"),
                    'fecha_docto': fecha,
                    'movimiento': row.get("movimiento"),
                    'debitos': float(row.get("debitos") or 0.0),
                    'creditos': float(row.get("creditos") or 0.0),  # 🟢 Nuevo campo
                    'notas': row.get("notas"),
                    'vendedor': row.get("vendedor"),
                    'nombre_auxiliar': row.get("nombre_auxiliar"),
                    'sql_id': self.id,
                    'razon_social': maestro.get("c0980_razon_social"),
                    'categoria': categoria,  # 🔹 Nuevo campo
                })

            # 6️⃣ Insertar en tabla.prueba
            if registros:
                self.env['tabla.prueba'].create(registros)
                return self._notify(f"{len(registros)} registros importados correctamente.")
            else:
                return self._notify("No se encontraron registros válidos.", tipo="warning")

        except Exception as e:
            _logger.error(f"Error en sincronización: {e}")
            return self._notify(f"Error: {e}", tipo="danger", sticky=True)



    def _notify(self, message, tipo="success", sticky=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'type': tipo,
                'sticky': sticky,
            }
        }

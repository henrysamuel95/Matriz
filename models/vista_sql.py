from odoo import models, fields, api
import logging
import re
from datetime import datetime
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class VistaSQL(models.Model):
    _name = "vistas_sql"
    _description = "Modelo para vistas SQL"

    name = fields.Char(string='Vista SQL')
    registros_prueba_ids = fields.One2many("tabla.prueba", "vista_sql_id", string="Registros relacionados")
    contratacion_ids_sql = fields.Many2one("contratacion", string="Proyecto Relacionado")
    fecha_inicio = fields.Date(related='contratacion_ids_sql.fecha_inicio', tracking=True)
    fecha_finalizacion = fields.Date(related='contratacion_ids_sql.fecha_final_proyecto', tracking=True)
    centro_costo_proyecto = fields.Char(related='contratacion_ids_sql.mcb_centro_costo', string="Centro de costo", tracking=True)
    resultado_consulta = fields.Char()
    currency_id = fields.Many2one('res.currency', string='Moneda')

    # Campos relacionados
    tercero = fields.Char(string="Cédula o Nit", related="registros_prueba_ids.tercero")
    razon_social = fields.Char(string="Razón Social", related="registros_prueba_ids.razon_social")
    movimiento = fields.Char(string="Movimiento", related="registros_prueba_ids.movimiento")
    debitos = fields.Monetary(string="Débito", related="registros_prueba_ids.debitos")
    id_tipo_docto = fields.Char(string="Tipo documento", related="registros_prueba_ids.id_tipo_docto")
    nro_docto = fields.Char(string="# Documento", related="registros_prueba_ids.nro_docto")
    fecha_docto = fields.Date(string="Fecha documento", related="registros_prueba_ids.fecha_docto")
    id_un = fields.Char(string="Unidad de negocio", related="registros_prueba_ids.id_un")

    def name_get(self):
        return [(record.id, record.id_un or 'Sin nombre') for record in self]

    def obtener_terceros(self):
        """Obtiene los terceros únicos desde tabla.prueba (sin volver a conectar al SQL)."""
        self.ensure_one()
        unidad_negocio = self.centro_costo_proyecto

        # Obtener terceros únicos de la tabla local
        registros = self.env['tabla.prueba'].search([('id_un', '=', unidad_negocio)])
        terceros_unicos = registros.mapped('tercero')

        # Limpiar tabla temporal
        self.env['tercero_sql_temp'].search([]).unlink()

        # Crear registros transitorios
        for tercero in terceros_unicos:
            registro = registros.filtered(lambda r: r.tercero == tercero)[:1]
            self.env['tercero_sql_temp'].create({
                'tercero': tercero,
                'id_un': unidad_negocio,
                'razon_social': registro.razon_social or 'Sin nombre',
                'categoria': registro.categoria or 'Otro',
            })

        return {
            'type': 'ir.actions.act_window',
            'name': f"Terceros - {unidad_negocio}",
            'res_model': 'tercero_sql_temp',
            'view_mode': 'tree',
            'views': [(self.env.ref('Matriz.view_terceros_sql_tree').id, 'tree')],
            'target': 'current',
        }


class TerceroSQLTemp(models.TransientModel):
    _name = 'tercero_sql_temp'
    _description = 'Terceros consultados desde tabla.prueba'

    tercero = fields.Char(string='Tercero')
    id_un = fields.Char(string="Unidad de negocio")
    razon_social = fields.Char(string="Razón Social")
    categoria = fields.Selection([
        ('Pago', 'Pago'),
        ('Causacion', 'Causación'),
        ('Legalizacion', 'Legalización'),
        ('Otro', 'Otro'),
    ], string="Categoría")

    def obtener_detalles_tercero(self):
        """Obtiene los detalles del tercero desde tabla.prueba (sin conexión SQL)."""
        self.ensure_one()

        registros = self.env['tabla.prueba'].search([
            ('tercero', '=', self.tercero),
            ('id_un', '=', self.id_un)
        ])

        if not registros:
            raise UserError(f"No se encontraron registros para el tercero {self.tercero} en unidad {self.id_un}.")

        # Limpiar registros previos
        self.env['tercero_detalle_sql_temp'].search([]).unlink()

        for reg in registros:
            self.env['tercero_detalle_sql_temp'].create({
                'tercero': reg.tercero,
                'razon_social': reg.razon_social,
                'nombre_auxiliar': reg.nombre_auxiliar,

                'id_tipo_docto': reg.id_tipo_docto,
                'nro_docto': reg.nro_docto,
                'fecha_docto': reg.fecha_docto,
                'debitos': reg.debitos,
                'creditos': reg.creditos,
                'notas': reg.notas,
                'categoria': reg.categoria,
                'id_un': reg.id_un,
            })

        return {
            'type': 'ir.actions.act_window',
            'name': f"Detalles del tercero {self.tercero}",
            'res_model': 'tercero_detalle_sql_temp',
            'view_mode': 'tree',
            'target': 'current',
        }


class TerceroDetalleSQLTemp(models.TransientModel):
    _name = 'tercero_detalle_sql_temp'
    _description = 'Detalle de tercero desde tabla.prueba'

    categoria = fields.Selection([
        ('Pago', 'Pago'),
        ('Causacion', 'Causación'),
        ('Legalizacion', 'Legalización'),
        ('Otro', 'Otro'),
    ], string="Categoría")

    razon_social = fields.Char(string="Razón Social")
    tercero = fields.Char(string='Tercero')
    nombre_auxiliar = fields.Char(string='Nombre auxiliar')
    id_co_docto = fields.Char(string='ID Compañía')  # ✅ Restaurado
    id_tipo_docto = fields.Char(string='Tipo documento')
    nro_docto = fields.Char(string='Número documento')
    fecha_docto = fields.Date(string='Fecha documento')
    debitos = fields.Monetary(string='Débitos')
    creditos = fields.Monetary(string='Créditos')
    notas = fields.Char(string='Notas')
    id_un = fields.Char(string="Unidad de negocio")
    currency_id = fields.Many2one('res.currency', string='Moneda')

from odoo import models, fields, api
import logging
import re
from datetime import datetime
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class VistaSQL(models.Model):
    _name = "vistas_sql"
    _description = "Modelo para vistas SQL"
    
    
    contratista_id = fields.Many2one(
        'contrataciones_fuv', 
        string='Contratista Seleccionado'
    )
    documento_identidad = fields.Char('Documento de identificación', related='contratista_id.documento_identidad')
    nit = fields.Char('Documento de identificación', related='contratista_id.nit2')
    
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

    @api.onchange('contratacion_ids_sql')
    def _onchange_filtrar_contratistas(self):
        """Filtra el desplegable para mostrar solo contratistas con NITs presentes en Siesa para este proyecto."""
        self.contratista_id = False
        if self.centro_costo_proyecto:
            # Buscamos NITs únicos en la bodega local para este centro de costo
            registros_siesa = self.env['tabla.prueba'].search([('id_un', '=', self.centro_costo_proyecto)])
            nits_en_siesa = registros_siesa.mapped('tercero')
            
            # El filtro busca coincidencia exacta entre 'tercero' (Siesa) y 'documento_identidad' (Odoo)
            return {'domain': {'contratista_id': [('documento_identidad', 'in', nits_en_siesa)]}}

    def obtener_terceros(self):
        """Busca movimientos filtrando estrictamente por Documento de Identidad."""
        self.ensure_one()
        if not self.centro_costo_proyecto:
            raise UserError("Debe seleccionar un Proyecto.")

        # Filtro base: Unidad de Negocio
        dominio = [('id_un', '=', self.centro_costo_proyecto)]

        # Filtro estricto: Si se seleccionó contratista, buscar por su Documento de ID
        if self.contratista_id:
            if not self.contratista_id.documento_identidad:
                raise UserError("El contratista no tiene Documento de Identidad configurado.")
            # Coincidencia directa con el campo 'tercero' de Siesa
            dominio.append(('tercero', '=', self.contratista_id.documento_identidad))

        registros = self.env['tabla.prueba'].search(dominio)
        
        if not registros:
            raise UserError("No se encontraron registros. Verifique que el NIT coincida exactamente con Siesa.")

        # Limpiar y poblar tabla temporal
        self.env['tercero_sql_temp'].search([('create_uid', '=', self.env.user.id)]).unlink()
        
        terceros_unicos = registros.mapped('tercero')
        for nit in terceros_unicos:
            reg_data = registros.filtered(lambda r: r.tercero == nit)[0]
            self.env['tercero_sql_temp'].create({
                'tercero': nit,
                'id_un': self.centro_costo_proyecto,
                'razon_social': reg_data.razon_social or 'Sin Razón Social',
                'categoria': reg_data.categoria or 'Otro',
            })

        return {
            'type': 'ir.actions.act_window',
            'name': f"Resultados para ID: {self.contratista_id.documento_identidad or self.centro_costo_proyecto}",
            'res_model': 'tercero_sql_temp',
            'view_mode': 'tree',
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

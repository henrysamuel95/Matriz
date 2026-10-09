from odoo import models, fields, api


class Departamento(models.Model):
    _name = 'departamento'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    Codigo_Departamento = fields.Char(string="Código Departamento")
    name = fields.Char(string="Nombre Departamento", default="Valle del Cauca")
    
    trazabilidad_ids = fields.One2many('trazabilidad_interna', 'departamento_id', string="Historial")

    @api.model
    def create(self, vals):
        record = super().create(vals)
        try: self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        except Exception: pass
        return record

    def write(self, vals):
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        except Exception: pass
        return super().write(vals)

    def unlink(self):
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        except Exception: pass
        return super().unlink()
    
    def abrir_departamentos(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Departamentos',
            'res_model': 'departamento',
            'view_mode': 'tree',
            'target': 'current',
        }









class Municipio(models.Model):
    _name = 'municipio'

    Codigo_Municipio = fields.Char(string="Código Municipio")
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string="Municipio", default="Cali")
    municipio_ejecucion = fields.Many2one('informacion_base_agregar', string="relacion")
    municipio_ejecucion2 = fields.Many2one('informacion_base_agregar', string="relacion")
    tarifa_bd = fields.Char(string='Tarifa')
    conceptos_bd = fields.Char(string='Concepto')
    trazabilidad_ids = fields.One2many('trazabilidad_interna', 'municipio_id', string="Historial")

    @api.model
    def create(self, vals):
        record = super().create(vals)
        try: self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        except Exception: pass
        return record

    def write(self, vals):
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        except Exception: pass
        return super().write(vals)

    def unlink(self):
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        except Exception: pass
        return super().unlink()

    def abrir_municipios(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Municipio',
            'res_model': 'municipio',
            'view_mode': 'tree',
            'target': 'current',
        }
        
        
        
        
        
        
class Departamentos_y_municipios(models.Model):
    _name = 'departamento_y_municipio'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    
    municipios2 = fields.Many2one(comodel_name='municipio', string="Municipio")
    contrataciones_fuv_id = fields.Many2one('contrataciones_fuv', string='Tabla')
    orden_compra_bienes_id = fields.Many2one('orden_compra_bienes', string='Tabla')
    orden_compra_servicios_id = fields.Many2one('orden_compra_servicios', string='Orden de compra servicios')

    departamentos = fields.Many2one(comodel_name='departamento', string="Departamento")
    conceptos_bd = fields.Char(comodel_name='municipio', string="Concepto")
    tarifa_bd = fields.Char(comodel_name='municipio', string="Tarifa", store=True)
    anexo_id = fields.Many2one('anexo', string="Anexo")

    tarifa2 = fields.Many2one(comodel_name='tarifa2', string="Tarifa")
    concepto_2 = fields.Many2one(comodel_name='concepto', string="Concepto", related="contrataciones_fuv_id.concepto_22", store=True)
    #Nuevo campo
    dedicacion = fields.Float(string='% Dedicación')
    codigo_ciiu_2 = fields.Char(string='Código CIIU3', related="contrataciones_fuv_id.codigo_ciiu")

    
    def name_get(self):
        """ Le dice a Odoo cómo mostrar el nombre de este registro en los campos Many2one """
        result = []
        for rec in self:
            dep = rec.departamentos.name if rec.departamentos else 'Sin Dep'
            mun = rec.municipios2.name if rec.municipios2 else 'Sin Mun'
            result.append((rec.id, f"{dep} - {mun}"))
        return result
    
    def actualizar_todas_las_tarifas(self):
        """ Método manual para actualizar la tarifa de todas las líneas. """
        registros = self.search([])
        for record in registros:
            record._onchange_tarifa_bd()

    @api.onchange('municipios2', 'concepto_2', 'tarifa_bd', 'conceptos_bd')
    def _onchange_tarifa_bd(self):
        """Buscar la tarifa en el modelo municipio donde coincida el concepto seleccionado"""
        if self.municipios2 and self.concepto_2:
            municipio = self.env['municipio'].search([
                ('id', '=', self.municipios2.id),
                ('conceptos_bd', '=', self.concepto_2.name)
            ], limit=1)

            if municipio:
                self.tarifa_bd = municipio.tarifa_bd
            else:
                self.tarifa_bd = "No definida"  # Valor por defecto si no se encuentra coincidencia
        else:
            self.tarifa_bd = False  # Limpia el campo si no hay municipio o concepto seleccionado


    @api.onchange('departamentos')
    def _onchange_departamentos(self):
        """Filtrar municipios según los dos primeros dígitos del departamento seleccionado."""
        if self.departamentos and self.departamentos.Codigo_Departamento:
            primeros_dos_digitos = self.departamentos.Codigo_Departamento[:2]
            municipios_relacionados = self.env['municipio'].search([('Codigo_Municipio', '=like', f"{primeros_dos_digitos}%")])
            self.municipios2 = False  # Limpiar selección actual del municipio
            return {
                'domain': {
                    'municipios2': [('id', 'in', municipios_relacionados.ids)]
                }
            }
        else:
            return {
                'domain': {
                    'municipios2': []
                }
            }

    tarifa3 = fields.Char(string="Tarifa")

    tarifa = fields.Selection(
        selection=[
            ('1', '1 * 1000'),
            ('2', '2 * 1000'),
            ('3', '3 * 1000'),
            ('4', '4 * 1000'),
            ('5', '5 * 1000'),
            ('6', '6 * 1000'),
            ('7', '7 * 1000'),
            ('8', '8 * 1000'),
            ('9', '9 * 1000'),
            ('10', '10 * 1000')
        ],
        string='TARIFA', readonly=False,
    )


    trazabilidad_ids = fields.One2many('trazabilidad_interna', 'departamento_y_municipio_id', string="Historial")

    @api.model
    def create(self, vals):
        record = super(Departamentos_y_municipios, self).create(vals)
        if record.contrataciones_fuv_id:
            self._recalcular_dedicacion_todas_lineas(record.contrataciones_fuv_id)
        # GATILLO CREACIÓN
        try: self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        except Exception: pass
        return record

    def write(self, vals):
        # GATILLO EDICIÓN
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        except Exception: pass
        return super(Departamentos_y_municipios, self).write(vals)

    @api.model
    def unlink(self):
        contrataciones_fuv = self.contrataciones_fuv_id
        # GATILLO ELIMINACIÓN (antes de borrar)
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        except Exception: pass
        
        res = super(Departamentos_y_municipios, self).unlink()
        if contrataciones_fuv:
            self._recalcular_dedicacion_todas_lineas(contrataciones_fuv)
        return res

    @api.model
    def _recalcular_dedicacion_todas_lineas(self, contratacion):
        """
        Método para recalcular la dedicación en todas las líneas
        """
        if not contratacion:
            return

        lineas = contratacion.departamentos_y_municipios_ids
        total_lineas = len(lineas)

        if total_lineas > 0:
            dedicacion_por_linea = 100.0 / total_lineas
            # Usamos sudo() para evitar problemas de permisos
            for linea in lineas:
                linea.sudo().write({'dedicacion': dedicacion_por_linea})


    
    
    
    
    
    
    


 

class MunicipiosCombinados(models.Model):
    _name = 'municipios_combinados'
    _description = 'Municipios'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string='Municipio')
    municipios3 = fields.Many2one(comodel_name='municipios_nuevos', string="Municipio")
    tarifa_bd = fields.Char(string='Tarifa')
    conceptos_bd = fields.Char(string='Concepto')
    
    base_servicio = fields.Char(string="Base servicios")
    base_bienes = fields.Char(string="Base bienes")
    
    dedicacion_comibinados = fields.Float(string='% Dedicación')  # Campo solicitado
    proceso_id = fields.Many2one('proceso_gestion_tributaria', string="Proceso")
    proceso_id_anexo = fields.Many2one('anexo', string="Proceso")
    tarifa_bd3 = fields.Char(string="Tarifa", related="proceso_id.tarifa_bd3", store=True)

    trazabilidad_ids = fields.One2many('trazabilidad_interna', 'municipios_combinados_id', string="Historial")

    @api.model
    def create(self, vals):
        record = super().create(vals)
        record.proceso_id.repartir_dedicacion()
        # GATILLO CREACIÓN
        try: self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        except Exception: pass
        return record

    def write(self, vals):
        # GATILLO EDICIÓN
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        except Exception: pass
        return super().write(vals)

    def unlink(self):
        proceso_ids = self.mapped('proceso_id')
        # GATILLO ELIMINACIÓN
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        except Exception: pass
        
        res = super().unlink()
        for proceso in proceso_ids:
            proceso.repartir_dedicacion()
        return res

    def abrir_municipios_combinados(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Municipio',
            'res_model': 'municipios_combinados',
            'view_mode': 'tree,form',
            'target': 'current',
        }







class MunicipiosNuevos(models.Model):
    _name = 'municipios_nuevos'
    _description = 'Municipio'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string='Municipio', store=True)
    tarifa_bd3 = fields.Char(string='Tarifa', store=True)
    conceptos_bd3 = fields.Char(string='Concepto', store=True)
    
    base_servicio = fields.Char(string="Base servicios")
    base_bienes = fields.Char(string="Base bienes")
    
    municipio_id = fields.Many2one(comodel_name='municipios_nuevos', string='Municipio', store=True)
    tarifa_id_nuevo = fields.Many2one(comodel_name='tarifa2', string='Tarifa', store=True)
    concepto_id_nuevo = fields.Many2one(comodel_name='concepto', string='Concepto', store=True)

    trazabilidad_ids = fields.One2many('trazabilidad_interna', 'municipios_nuevos_id', string="Historial")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('municipio_id'):
                municipio = self.env['municipios_nuevos'].browse(vals['municipio_id'])
                vals['name'] = municipio.name
            if vals.get('tarifa_id_nuevo'):
                tarifa = self.env['tarifa2'].browse(vals['tarifa_id_nuevo'])
                vals['tarifa_bd3'] = tarifa.name
            if vals.get('concepto_id_nuevo'):
                concepto = self.env['concepto'].browse(vals['concepto_id_nuevo'])
                vals['conceptos_bd3'] = concepto.name

        records = super(MunicipiosNuevos, self).create(vals_list)
        
        # GATILLO CREACIÓN MASIVO
        try:
            for i, record in enumerate(records):
                self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals_list[i])
        except Exception: pass
        return records

    def write(self, vals):
        # GATILLO EDICIÓN
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        except Exception: pass
        return super(MunicipiosNuevos, self).write(vals)

    def unlink(self):
        # GATILLO ELIMINACIÓN
        try: self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        except Exception: pass
        return super(MunicipiosNuevos, self).unlink()
    
    @api.onchange('municipio_id')
    def _onchange_municipio_id(self):
        if self.municipio_id:
            self.name = self.municipio_id.name

    @api.onchange('tarifa_id_nuevo')
    def _onchange_tarifa_id_nuevo(self):
        if self.tarifa_id_nuevo:
            self.tarifa_bd3 = self.tarifa_id_nuevo.name

    @api.onchange('concepto_id_nuevo')
    def _onchange_concepto_id_nuevo(self):
        if self.concepto_id_nuevo:
            self.conceptos_bd3 = self.concepto_id_nuevo.name

    def abrir_municipios_nuevos(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Municipio',
            'res_model': 'municipios_nuevos',
            'view_mode': 'tree,form',
            'target': 'current',
        }


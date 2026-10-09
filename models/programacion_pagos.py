#!/usr/bin/python
from odoo import api, fields, models
from odoo.exceptions import ValidationError
from datetime import date


class ProgramacionPagos(models.Model):
    _name = 'programacion_pagos'
    _description = 'Programacion de pagos'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    hiden = fields.Char(string='hiden')
    create_uid = fields.Many2one('res.users', string='Creador del proyecto: ', readonly=True)
    fch_creacion = fields.Datetime(string='Fecha creación: ', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())

    name = fields.Char()
    documento = fields.Char(string='DOCUMENTO')
    fecha_documento = fields.Date(string='FECHA DOCUMENTO')
    codigo_tercero = fields.Char(string='CÓDIGO TERCERO')
    nombre_tercero = fields.Char(string='NOMBRE TERCERO')
    valor_a_pagar = fields.Float(string='VALOR A PAGAR')
    directora = fields.Char(string='DIR. DIANA CANO')
    observaciones_tesoreria = fields.Text(string='OBSERVACIONES TESORERÍA')

    total_valor_a_pagar = fields.Monetary(string='VALOR TOTAL PAGADO', related="linea_programacion_pagos_ids.total_valor_a_pagar")
    total_valor_a_pagar_pendiente = fields.Monetary(string='VALOR A PAGAR PENDIENTE', related="pendientes_tabla_ids.total_valor_a_pagar_pendiente")
    total_valor_a_pagar_programado = fields.Monetary(string='VALOR A PAGAR PENDIENTE DEL DÍA DE HOY', related="pagos_programados_de_hoy_ids.total_valor_a_pagar_programado")

    linea_programacion_pagos_ids = fields.One2many('linea_programacion_pagos', 'programacion_pagos_id', string='Programa de Pagos')
    pendientes_tabla_ids = fields.One2many('pendientes_tabla', 'centro_de_costo', string='Programa de Pagos')
    pagos_programados_de_hoy_ids = fields.One2many('pagos_programados_de_hoy', 'centro_de_costo', string='Programa de Pagos')

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        tracking=True,
        required=False,  # Desmarca como requerido
        readonly=False,
    )

    def open_linea_programacion_pagos_calendar(self):
        return {
            'name': 'Calendario de Programación de Pagos',
            'type': 'ir.actions.act_window',
            'res_model': 'linea_programacion_pagos',
            'view_mode': 'calendar',
            'domain': [('programacion_pagos_id', '=', self.id)],
            'target': 'current',
        }

    def Pendientes_vista(self):
        return {
            'name': 'Pendientes',
            'type': 'ir.actions.act_window',
            'res_model': 'pendientes_tabla',
            'view_mode': 'tree,form,calendar',
            'target': 'current',
        }

    def pagos_programados_de_hoy(self):
        return {
            'name': 'Pagos programados para el día de hoy',
            'type': 'ir.actions.act_window',
            'res_model': 'pagos_programados_de_hoy',
            'view_mode': 'tree,form,calendar',
            'target': 'current',
        }

    def Linea_programacion_pagos(self):
        # Verificar si ya existe un registro en 'linea_programacion_pagos' para esta subactividad
        existing_record = self.env['linea_programacion_pagos'].search([('programacion_pagos_id', '=', self.id)], limit=1)

        if not existing_record:

            linea_programacion_pagos_vals = {
                'programacion_pagos_id': self.id
                # Puedes agregar más campos y valores aquí según sea necesario
            }
           # existing_record = self.env['linea_programacion_pagos'].create(linea_programacion_pagos_vals)

        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'linea_programacion_pagos',
            'view_mode': 'tree',
            'target': 'current',
            'domain': [('programacion_pagos_id', '=', self.id)],
            'context': {'default_programacion_pagos_id': self.id},
        }



    def open_traceability_view(self):
        return {
            'name': 'Trazabilidad',
            'type': 'ir.actions.act_window',
            'res_model': 'traceability.record',
            'view_mode': 'tree,form',
            'target': 'current',
        }


    @api.model
    def create(self, vals):
        record = super().create(vals)
        self.env['traceability.record'].track_changes(self._name, record.id, vals)
        return record

    def write(self, vals):
        self.env['traceability.record'].track_changes(self._name, self.id, vals)
        result = super().write(vals)
        return result


class LineaProgramacionPagos(models.Model):
    _name = 'linea_programacion_pagos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Línea de Programación de Pagos'
    _rec_name = "nombre_tercero"

    programacion_pagos_id = fields.Many2one('programacion_pagos', string='Programación de Pagos')
    documento = fields.Char(string='DOCUMENTO')
    fecha_documento = fields.Date(string='FECHA DOCUMENTO')
    codigo_tercero = fields.Char(string='CÓDIGO TERCERO')
    nombre_tercero = fields.Char(string='NOMBRE TERCERO')
    valor_a_pagar = fields.Monetary(string='VALOR A PAGAR')
    directora = fields.Char(string='DIR. DIANA CANO')
    observaciones_tesoreria = fields.Text(string='OBSERVACIONES TESORERÍA')
    fecha = fields.Date(string='FECHA DE PAGO EFECTUADO')

    partner_ids = fields.Many2many('res.partner', 'partner_book_rel', 'partner_id', 'book_id', 'Contacts')

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        tracking=True,
        required=False,  # Desmarca como requerido
        readonly=False,
    )

    total_valor_a_pagar = fields.Monetary(string='VALOR TOTAL PAGADO', compute='_compute_total_valor_a_pagar')

    @api.depends('valor_a_pagar')
    def _compute_total_valor_a_pagar(self):
        for record in self:
            total_valor_a_pagar = sum(record.mapped('valor_a_pagar'))
            record.total_valor_a_pagar = total_valor_a_pagar


    @api.model
    def create(self, vals):
        record = super().create(vals)
        self.env['traceability.record'].track_changes(self._name, record.id, vals)
        return record

    def write(self, vals):
        self.env['traceability.record'].track_changes(self._name, self.id, vals)
        result = super().write(vals)
        return result


class Pendientes_Tabla(models.Model):
    _name = 'pendientes_tabla'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Pendientes'
    _rec_name = "centro_de_costo"

    centro_de_costo = fields.Many2one(comodel_name='programacion_pagos', string='CENTRO DE COSTOS')
    documento = fields.Char(string='DOCUMENTO')
    fecha_documento = fields.Date(string='FECHA DOCUMENTO')
    codigo_tercero = fields.Char(string='CÓDIGO TERCERO')
    nombre_tercero = fields.Char(string='NOMBRE TERCERO')
    valor_a_pagar = fields.Monetary(string='VALOR A PAGAR')
    fecha_estimada_de_pago = fields.Date(string='FECHA ESTIMADA DE PAGO')
    fecha_de_pago = fields.Date(string='FECHA DE PAGO')

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        tracking=True,
        required=False,  # Desmarca como requerido
        readonly=False,
    )

    total_valor_a_pagar_pendiente = fields.Monetary(string='VALOR A PAGAR PENDIENTE', compute='_compute_total_valor_a_pagar')

    @api.depends('valor_a_pagar')
    def _compute_total_valor_a_pagar(self):
        for record in self:
            total_valor_a_pagar_pendiente = sum(record.mapped('valor_a_pagar'))
            record.total_valor_a_pagar_pendiente = total_valor_a_pagar_pendiente


    @api.onchange('fecha_estimada_de_pago')
    def crear_pagos_programados_de_hoy(self):
        for pendiente in self:
            if pendiente.fecha_estimada_de_pago:
                # Convertir la fecha_estimada_de_pago a un objeto date para compararla
                fecha_estimada = fields.Date.from_string(pendiente.fecha_estimada_de_pago)
                fecha_actual = date.today()

                if fecha_estimada == fecha_actual:  # Verifica si la fecha es la fecha actual
                    linea_programacion = self.env['pagos_programados_de_hoy'].create({
                        'centro_de_costo': pendiente.centro_de_costo.id,
                        'documento': pendiente.documento,
                        'fecha_documento': pendiente.fecha_documento,
                        'codigo_tercero': pendiente.codigo_tercero,
                        'nombre_tercero': pendiente.nombre_tercero,
                        'valor_a_pagar': pendiente.valor_a_pagar,
                        # Agrega otros campos que necesites crear aquí
                    })
                    pendiente.write({
                        'documento': False,
                        'centro_de_costo': False,
                        'fecha_documento': False,
                        'codigo_tercero': False,
                        'nombre_tercero': False,
                        'valor_a_pagar': False,
                        'fecha_estimada_de_pago': False,
                        'fecha_de_pago': False,
                    })

    @api.onchange('fecha_de_pago')
    def crear_linea_programacion(self):
        for pendiente in self:
            if pendiente.fecha_de_pago:  # Verifica si la fecha está presente
                linea_programacion = self.env['linea_programacion_pagos'].create({
                    'programacion_pagos_id': pendiente.centro_de_costo.id,
                    'documento': pendiente.documento,
                    'fecha_documento': pendiente.fecha_documento,
                    'codigo_tercero': pendiente.codigo_tercero,
                    'nombre_tercero': pendiente.nombre_tercero,
                    'valor_a_pagar': pendiente.valor_a_pagar,
                    # Agrega otros campos que necesites crear aquí
                })
                pendiente.write({
                    'documento': False,
                    'centro_de_costo': False,
                    'fecha_documento': False,
                    'codigo_tercero': False,
                    'nombre_tercero': False,
                    'valor_a_pagar': False,
                    'fecha_estimada_de_pago': False,
                    'fecha_de_pago': False,
                })


    @api.model
    def create(self, vals):
        record = super().create(vals)
        self.env['traceability.record'].track_changes(self._name, record.id, vals)
        return record

    def write(self, vals):
        self.env['traceability.record'].track_changes(self._name, self.id, vals)
        result = super().write(vals)
        return result

class Pagos_Programado_para_el_Dia_de_Hoy(models.Model):
    _name = 'pagos_programados_de_hoy'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Pagos programados para el día de hoy'
    _rec_name = "centro_de_costo"

    centro_de_costo = fields.Many2one(comodel_name='programacion_pagos', string='CENTRO DE COSTOS')
    documento = fields.Char(string='DOCUMENTO')
    fecha_documento = fields.Date(string='FECHA DOCUMENTO')
    codigo_tercero = fields.Char(string='CÓDIGO TERCERO')
    nombre_tercero = fields.Char(string='NOMBRE TERCERO')
    valor_a_pagar = fields.Monetary(string='VALOR A PAGAR')
    fecha_de_pago = fields.Date(string='FECHA DE PAGO')

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        tracking=True,
        required=False,  # Desmarca como requerido
        readonly=False,
    )

    total_valor_a_pagar_programado = fields.Monetary(string='VALOR A PAGAR PENDIENTE DEL DÍA DE HOY', compute='_compute_total_valor_a_pagar')

    @api.depends('valor_a_pagar')
    def _compute_total_valor_a_pagar(self):
        for record in self:
            total_valor_a_pagar_programado = sum(record.mapped('valor_a_pagar'))
            record.total_valor_a_pagar_programado = total_valor_a_pagar_programado


    @api.onchange('fecha_de_pago')
    def crear_linea_programacion(self):
        for pendiente in self:
            if pendiente.fecha_de_pago and pendiente.centro_de_costo:
                # Crear una nueva línea en LineaProgramacionPagos
                linea_programacion = self.env['linea_programacion_pagos'].create({
                    'programacion_pagos_id': pendiente.centro_de_costo.id,
                    'documento': pendiente.documento,
                    'fecha_documento': pendiente.fecha_documento,
                    'codigo_tercero': pendiente.codigo_tercero,
                    'nombre_tercero': pendiente.nombre_tercero,
                    'valor_a_pagar': pendiente.valor_a_pagar,
                    # Agrega otros campos que necesites crear aquí
                })
                # Limpiar las variables o eliminar el registro
                pendiente.write({
                    'documento': False,
                    'centro_de_costo': False,
                    'fecha_documento': False,
                    'codigo_tercero': False,
                    'nombre_tercero': False,
                    'valor_a_pagar': False,
                    'fecha_de_pago': False,
                })

    @api.model
    def create(self, vals):
        record = super().create(vals)
        self.env['traceability.record'].track_changes(self._name, record.id, vals)
        return record

    def write(self, vals):
        self.env['traceability.record'].track_changes(self._name, self.id, vals)
        result = super().write(vals)
        return result

class TraceabilityRecord(models.Model):
    _name = 'traceability.record'
    _description = 'Trazabilidad'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    model_name = fields.Char(string='Nombre del modelo', tracking=True)
    field_name = fields.Char(string='Nombre del campo', tracking=True)
    new_value = fields.Char(string='Valor', tracking=True)
    user_id = fields.Many2one('res.users', string='Cambiado por', default=lambda self: self.env.user, tracking=True)
    change_date = fields.Datetime(string='Modificado', default=fields.Datetime.now, tracking=True)

    @api.model
    def create_traceability_record(self, model_name, record_id, field_name, old_value, new_value):
        """Create a new traceability record."""
        model_description = self.env[model_name]._description
        # Retrieve the human-readable description of the model
        self.create({
            'model_name': model_description,
            'field_name': field_name,
            'new_value': new_value,
            'user_id': self.env.user.id,
        })

    @api.model
    def track_changes(self, model_name, record_id, vals):
        """Track changes in the specified record."""
        record = self.env[model_name].browse(record_id)
        model_fields = self.env[model_name].fields_get()
        for field_name, new_value in vals.items():
            old_value = record[field_name]
            if old_value != new_value:
                if model_fields[field_name]['type'] == 'many2one':
                    comodel_name = model_fields[field_name].get('comodel_name')
                    if comodel_name:
                        # Obtener el nombre legible del registro relacionado
                        new_value_readable = self.env[comodel_name].browse(new_value).name or False
                        old_value_readable = self.env[comodel_name].browse(old_value).name or False
                    else:
                        # Si comodel_name no está definido, dejar new_value y old_value como están
                        new_value_readable = new_value
                        old_value_readable = old_value
                else:
                    # Si el campo no es many2one, dejar new_value y old_value como están
                    new_value_readable = new_value
                    old_value_readable = old_value

                field_label = model_fields[field_name]['string']
                self.create_traceability_record(model_name, record_id, field_label, old_value_readable,
                                                new_value_readable)







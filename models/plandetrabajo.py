#!/usr/bin/python
from odoo import fields, models, api


class PLAN_DE_TRABAJO(models.Model):
    _name = "plandetrabajo"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Campos existentes
    name = fields.Char(string="Nombre")
    fch_creacion = fields.Datetime(string='Fecha creacion', copy=False, default=lambda self: fields.Datetime.now())
    write_date = fields.Datetime(string='Fecha de Modificación', readonly=True)

    new_name = fields.Char(string='Nuevo nombre')
    tabla_trabajo = fields.One2many('plandetrabajo', 'contratacion_id', string="Tabla de plan de trabajo")
    contratacion_id = fields.Many2one('plandetrabajo', string="Contratación")

    def Guardar(self):
        self.ensure_one()
        self.write({'name': self.new_name})
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    # Campos adicionales
    numero = fields.Integer(string="N")
    actividades = fields.Char(string="Actividades")
    subactividades = fields.Char(string="Subactividades")
    tareas = fields.Char(string="Tareas")
    subtareas = fields.Char(string="Subtareas")
    meta_por_actividad = fields.Char(string="Meta por Actividad")

    mes_1 = fields.Date(string="MES 1")
    mes_2 = fields.Date(string="MES 2")
    mes_3 = fields.Date(string="MES 3")

    duracion_actividad = fields.Char(string="Duración Actividad")
    duracion_total = fields.Char(string="Duración Total")
    peso_porcentual = fields.Char(string="Peso Porcentual")
    productos_entregados = fields.Char(string="Productos (Bienes o Servicios) Entregados")
    observaciones = fields.Text(string="Observaciones")

    # Métodos y funcionalidad adicional
    # Agrega aquí tus métodos personalizados según tus necesidades

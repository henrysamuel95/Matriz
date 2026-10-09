#!/usr/bin/python

from odoo import models, fields, api

class Mini_Ventana(models.TransientModel):
    _name = 'mini_ventana'

    def Ingreso_Nueva_Actividad_a_Contratar_y_Programacion_de_Pagos1(self):
        self.write({'write_date': fields.Datetime.now()})  # Actualiza la fecha de modificación
        return {
            'name': 'FORMULARIO PARA AGREGAR CONTRATO',
            'view_mode': 'form',
            'res_model': 'nueva_actividad',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def Ingreso_Nueva_Actividad_a_Contratar_y_Programacion_de_Pagos2(self):
        self.write({'write_date': fields.Datetime.now()})  # Actualiza la fecha de modificación
        return {
            'name': 'FORMULARIO PARA AGREGAR CONTRATO',
            'view_mode': 'form',
            'res_model': 'nueva_actividad2',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }
#!/usr/bin/python

from odoo import fields, models


class Generar(models.Model):
    _name = "generar_archivo"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    actividad_id = fields.Many2one('nueva_actividad', string="Actividad")
    actividad2_id = fields.Many2one('nueva_actividad2', string="Actividad")

    def generar(self):
        if self.actividad_id:
            new_registro = self.env['basedatoscontratacion'].create({
                'item': self.actividad_id.numero_contrato,
                'tipo_solicitud2': self.actividad_id.tipo_solicitud,
                'tipo_contrato2': self.actividad_id.tipo_contrato,
                'fecha_solicitud': self.actividad_id.fecha_solicitud,
                'cpd': self.actividad_id.cdp,
                'fecha_cdp': self.actividad_id.fecha_cdp,
                'nombre_solicitante': self.actividad_id.nombre_solicitante,
                'valor_total_contrato_letras': self.actividad_id.valor_contrato_letras,
                'fecha_inicio': self.actividad_id.fecha_inicio,
                'fecha_fin': self.actividad_id.fecha_finalizacion,
                'direccion': self.actividad_id.direccion,
                'telefono': self.actividad_id.telefono,
                'correo_electronico': self.actividad_id.correo,
                'forma_pago': self.actividad_id.descripcion_forma_pago,
                'valor_total_contrato': self.actividad_id.valor_contrato,
                'documento_identificacion': self.actividad_id.cedula_nit,
                'actividades_especificas': self.actividad_id.actividades_especificas,
            })

        if self.actividad2_id:
            new_registro2 = self.env['basedatoscontratacion'].create({
                'item': self.actividad2_id.numero_contrato,
                'tipo_solicitud2': self.actividad2_id.tipo_solicitud,
                'tipo_contrato2': self.actividad2_id.tipo_contrato,
                'fecha_solicitud': self.actividad2_id.fecha_solicitud,
                'cpd': self.actividad2_id.cdp,
                'fecha_cdp': self.actividad2_id.fecha_cdp,
                'nombre_solicitante': self.actividad2_id.nombre_solicitante,
                'valor_total_contrato_letras': self.actividad2_id.valor_contrato_letras,
                'fecha_inicio': self.actividad2_id.fecha_inicio,
                'fecha_fin': self.actividad2_id.fecha_finalizacion,
                'direccion': self.actividad2_id.direccion,
                'telefono': self.actividad2_id.telefono,
                'correo_electronico': self.actividad2_id.correo,
                'forma_pago': self.actividad2_id.descripcion_forma_pago,
                'valor_total_contrato': self.actividad2_id.valor_contrato,
                'documento_identificacion': self.actividad2_id.cedula_nit,
                'actividades_especificas': self.actividad2_id.actividades_especificas,
            })

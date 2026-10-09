#!/usr/bin/python
import re

from odoo import models, fields, api


class Contrato(models.Model):
    _name = "nueva_actividad"
    _inherit = "persona_juridica"

    _rec_name = "numero_contrato"

    buscar_contrato = fields.Many2one('nueva_actividad', string="Buscar Contrato por Número/ID")

    # ... (otros campos)

    @api.onchange('buscar_contrato')
    def llenar_campos(self):
        if self.buscar_contrato:
            contrato_seleccionado = self.buscar_contrato

            self.numero_contrato = contrato_seleccionado.numero_contrato
            self.tipo_solicitud = contrato_seleccionado.tipo_solicitud
            self.tipo_contrato = contrato_seleccionado.tipo_contrato
            self.fecha_solicitud = contrato_seleccionado.fecha_solicitud
            self.cdp = contrato_seleccionado.cdp
            self.objeto_de_contrato = contrato_seleccionado.objeto_de_contrato
            self.buscar_info = contrato_seleccionado.buscar_info
            self.fecha_cdp = contrato_seleccionado.fecha_cdp
            self.nombre_solicitante = contrato_seleccionado.nombre_solicitante
            self.valor_contrato = contrato_seleccionado.valor_contrato
            self.currency_id = contrato_seleccionado.currency_id
            self.valor_contrato_letras = contrato_seleccionado.valor_contrato_letras
            self.descripcion_forma_pago = contrato_seleccionado.descripcion_forma_pago
            self.titulo_estudios = contrato_seleccionado.titulo_estudios
            self.profesion = contrato_seleccionado.profesion
            self.cargo = contrato_seleccionado.cargo
            self.perfil_requerido = contrato_seleccionado.perfil_requerido
            self.nombre = contrato_seleccionado.nombre
            self.cedula_nit = contrato_seleccionado.cedula_nit
            self.direccion = contrato_seleccionado.direccion
            self.telefono = contrato_seleccionado.telefono
            self.correo = contrato_seleccionado.correo
            self.actividades_especificas = contrato_seleccionado.actividades_especificas
            self.justificacion = contrato_seleccionado.justificacion
            self.productos_entregar = contrato_seleccionado.productos_entregar
            self.fecha_inicio = contrato_seleccionado.fecha_inicio
            self.fecha_finalizacion = contrato_seleccionado.fecha_finalizacion

    numero_contrato = fields.Char(string="Número/ID Contrato")
    tipo_solicitud = fields.Char(string="Tipo de Solicitud")
    tipo_contrato = fields.Char(string="Tipo de Contrato")
    fecha_solicitud = fields.Date(string="Fecha de Solicitud")
    cdp = fields.Char(string="CDP")
    objeto_de_contrato = fields.Char(string="Objeto de contrato")

    # Columna 1
    buscar_info = fields.Many2one('persona_juridica',
                                  string="Buscar Inf contratista por cedula o NIT (Numeros sin DV) para asignarlos al contrato")
    fecha_cdp = fields.Date(string="Fecha de CDP")
    nombre_solicitante = fields.Char(string="Nombre de quien solicita")

    valor_contrato = fields.Monetary(string="Valor del Contrato ($)", currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Moneda', default=lambda self: self.env.ref(
        'base.COP'))  # Asegúrate de ajustar la referencia a la moneda correcta

    valor_contrato_letras = fields.Char(string="Valor del Contrato (en letras)")
    descripcion_forma_pago = fields.Char(string="Descripción forma de pago")

    # Columna 2
    titulo_estudios = fields.Char(string="Título Estudios/Otros Oficios")
    profesion = fields.Selection([
        ('medico', 'Médico'),
        ('ingeniero', 'Ingeniero'),
        ('abogado(a)', 'abogado(a)'),
        ('administrador de empresas', 'administrador de empresas'),
        ('arquitecto(a)', 'arquitecto'),
        ('antropólogo(a)', 'antropólogo(a)'),
        ('biólogo(a)', 'biólogo(a)'),
        ('botánico(a)', 'botánico(a)'),
        # Lista completa de opciones...
    ], string='Profesión')
    cargo = fields.Char(string="Cargo")
    perfil_requerido = fields.Char(string="Perfil Requerido")

    # Columna 3
    nombre = fields.Char(string="Nombre", related='buscar_info.name', store=True)
    cedula_nit = fields.Char(string="Cédula/NIT", related='buscar_info.documentos_id', store=True)
    direccion = fields.Char(string='Dirección', related='buscar_info.direccion', store=True)
    telefono = fields.Char(string='Teléfono', related='buscar_info.telefono', store=True)
    correo = fields.Char(string='Correo', related='buscar_info.correo', store=True)


    # Parte inferior debajo de las tres columnas
    actividades_especificas = fields.Text(string="Actividades Específicas")
    justificacion = fields.Text(string="Justificación de la Contratación")
    productos_entregar = fields.Text(string="Productos a Entregar")
    fecha_inicio = fields.Date(string="Fecha de Inicio")
    fecha_finalizacion = fields.Date(string="Fecha de Finalización")

    # Botones en la parte inferior
    def action_agregar_nuevo(self):
        # Lógica para agregar un nuevo registro
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'nueva_actividad',  # Usamos el nombre del modelo actual
            'view_mode': 'form',
            'view_type': 'form',
            'views': [(False, 'form')],  # Usamos (False, 'form') para abrir la vista de formulario predeterminada
            'target': 'new',  # Esto es importante para que permanezca en la misma ventana
            'context': {},  # Puedes establecer el contexto si es necesario
        }

    def action_limpiar_formulario(self):
        return {
            'name': 'FORMULARIO PARA AGREGAR CONTRATO',
            'view_mode': 'form',
            'res_model': 'nueva_actividad',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def action_modificar(self):
        # Lógica para eliminar un registro
        return

    def action_eliminar(self):
        # Lógica para eliminar un registro
        return

    def action_programacion_pagos(self):
        # Lógica para la programación de pagos
        return

    def action_cerrar(self):
        return {
            'name': 'Cerrar',
            'view_mode': 'form',
            'res_model': 'mini_ventana',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }






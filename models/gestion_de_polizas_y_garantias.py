#!/usr/bin/python

from odoo import fields, models, api

class GestiondePolizasyGarantias(models.Model):
    _name = "gestion_de_polizas_y_garantias"
    show_editable_fields = fields.Boolean(string="Mostrar Campos Editables")

    _rec_name = "numero_poliza"

    Buscar = fields.Many2one(comodel_name='gestion_de_polizas_y_garantias', string='Gestión de Pólizas', domain="[('numero_poliza', '!=', False)]")
    # Campos de solo lectura
    numero_poliza = fields.Char(string="Número de Póliza", readonly=True)
    valor = fields.Float(string="Valor", readonly=True)
    anexo = fields.Char(string="Anexo", readonly=True)
    tipo = fields.Char(string="Tipo", readonly=True)
    descripcion = fields.Text(string="Descripción", readonly=True)
    entidad_aseguradora = fields.Char(string="Entidad Aseguradora", readonly=True)
    fecha_inicio = fields.Date(string="Fecha de Inicio", readonly=True)
    AMPAROS = fields.Text(string="AMPAROS", readonly=True)
    fecha_fin = fields.Date(string="Fecha de Fin", readonly=True)

    # Campos editables
    numero_poliza_editable = fields.Char(string="Número de Póliza")
    valor_editable = fields.Float(string="Valor")
    anexo_editable = fields.Char(string="Anexo")
    tipo_editable = fields.Char(string="Tipo")
    descripcion_editable = fields.Text(string="Descripción")
    entidad_aseguradora_editable = fields.Char(string="Entidad Aseguradora")
    fecha_inicio_editable = fields.Date(string="Fecha de Inicio")
    AMPAROS_editable = fields.Text(string="AMPAROS")
    fecha_fin_editable = fields.Date(string="Fecha de Fin")

    # Tabla
    tabla_polizas = fields.One2many('poliza', 'gestion_id', string='Tabla de Polizas')

    def buscar_polizas(self):
        if self.Buscar:
            if self.Buscar.id:
                return {
                    'type': 'ir.actions.act_window',
                    'res_model': self.Buscar._name,
                    'view_mode': 'form',
                    'res_id': self.Buscar.id,
                    'target': 'new',  # Cambiado de 'new' a 'current'
                    'context': self._context,  # Mantén el contexto actual
                }

    def agregar_nuevo(self):
        self.ensure_one()

        if self.Buscar:  # Verifica si hay algo seleccionado en el campo "Buscar"
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'gestion_de_polizas_y_garantias',
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Copia los datos de los campos editables a los campos normales
            self.numero_poliza = self.numero_poliza_editable
            self.valor = self.valor_editable
            self.anexo = self.anexo_editable
            self.tipo = self.tipo_editable
            self.descripcion = self.descripcion_editable
            self.entidad_aseguradora = self.entidad_aseguradora_editable
            self.fecha_inicio = self.fecha_inicio_editable
            self.AMPAROS = self.AMPAROS_editable
            self.fecha_fin = self.fecha_fin_editable


            if not self.show_editable_fields:  # Verifica si los campos editables están visibles
                # Muestra los campos editables
                self.write({'show_editable_fields': True})
                # Limpia los campos editables
                self.numero_poliza_editable = False
                self.valor_editable = 0.0
                self.anexo_editable = False
                self.tipo_editable = False
                self.descripcion_editable = False
                self.entidad_aseguradora_editable = False
                self.fecha_inicio_editable = False
                self.AMPAROS_editable = False
                self.fecha_fin_editable = False
            else:
                # Crea un nuevo registro de Poliza
                poliza = self.env['poliza'].create({
                    'numero': self.numero_poliza_editable,
                    'tipo2': self.tipo_editable,
                    'entidad': self.entidad_aseguradora_editable,
                    'amparo': self.AMPAROS_editable,
                    'valor2': self.valor_editable,
                    'descripcion1': self.descripcion_editable,
                    'inicio': self.fecha_inicio_editable,
                    'fin': self.fecha_fin_editable,
                    'anexo2': self.anexo_editable,
                    'gestion_id': self.id,
                })
                self.write({'show_editable_fields': not self.show_editable_fields})

            return {
                'type': 'ir.actions.act_window',
                'res_model': 'gestion_de_polizas_y_garantias',
                'view_mode': 'form',
                'res_id': self.id,
                'target': 'new',
            }

    def limpiar(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'gestion_de_polizas_y_garantias',
            'view_mode': 'form',
            'target': 'new',
        }

    def modificar(self):
        self.ensure_one()

        # Actualiza los campos normales con los valores de los campos editables
        self.numero_poliza = self.numero_poliza_editable
        self.valor = self.valor_editable
        self.anexo = self.anexo_editable
        self.tipo = self.tipo_editable
        self.descripcion = self.descripcion_editable
        self.entidad_aseguradora = self.entidad_aseguradora_editable
        self.fecha_inicio = self.fecha_inicio_editable
        self.AMPAROS = self.AMPAROS_editable
        self.fecha_fin = self.fecha_fin_editable

        if self.tabla_polizas:  # Verifica si hay una póliza asociada
            poliza = self.tabla_polizas[0]  # Obtén la primera póliza asociada
            poliza.write({
                'numero': self.numero_poliza_editable,
                'tipo2': self.tipo_editable,
                'entidad': self.entidad_aseguradora_editable,
                'amparo': self.AMPAROS_editable,
                'valor2': self.valor_editable,
                'descripcion1': self.descripcion_editable,
                'inicio': self.fecha_inicio_editable,
                'fin': self.fecha_fin_editable,
                'anexo2': self.anexo_editable,
            })

        self.write({'show_editable_fields': not self.show_editable_fields})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'gestion_de_polizas_y_garantias',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def Eliminar(self):
        self.ensure_one()

        if self.Buscar:  # Verifica si hay algo seleccionado en el campo "Buscar"
            # Eliminar el registro asociado en la tabla de polizas
            self.tabla_polizas.unlink()

            # Limpiar los campos normales
            self.numero_poliza = False
            self.valor = 0.0
            self.anexo = False
            self.tipo = False
            self.descripcion = False
            self.entidad_aseguradora = False
            self.fecha_inicio = False
            self.AMPAROS = False
            self.fecha_fin = False

            # Eliminar el registro asociado en la tabla poliza
            if self.tabla_polizas:
                self.tabla_polizas.unlink()

        return {'type': 'ir.actions.act_window_close'}  # Opcional: Cerrar la ventana actual

    def cerrar(self):
        # Lógica para cerrar la vista o realizar alguna acción
        return {'type': 'ir.actions.act_window_close'}  # Cerrar la ventana actual

class Poliza(models.Model):
    _name = 'poliza'

    gestion_id = fields.Many2one('gestion_de_polizas_y_garantias', string='Gestión de Pólizas')
    numero = fields.Char(string='Número(ID)', readonly=True)  # Añadido readonly=True
    tipo2 = fields.Char(string='Tipo', readonly=True)  # Añadido readonly=True
    entidad = fields.Char(string='Entidad', readonly=True)  # Añadido readonly=True
    amparo = fields.Char(string='Amparo', readonly=True)  # Añadido readonly=True
    valor2 = fields.Float(string='Valor', readonly=True)  # Añadido readonly=True
    descripcion1 = fields.Text(string='Descripción', readonly=True)  # Añadido readonly=True
    inicio = fields.Date(string='Inicio', readonly=True)  # Añadido readonly=True
    fin = fields.Date(string='Fin', readonly=True)  # Añadido readonly=True
    anexo2 = fields.Char(string='Anexo', readonly=True)  # Añadido readonly=True














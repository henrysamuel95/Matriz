#!/usr/bin/python

from odoo import models, fields, api

class PersonaJuridicaVisualizacion(models.Model):
    _name = "persona_juridica_visualizacion"
    _description = "Visualización de Persona Jurídica"

    _inherit = "persona_juridica"


    # Define aquí todos los campos que deseas mostrar, excepto "nit".
    razon_social = fields.Char(string="Razón Social")
    documentos_id = fields.Char(string="Documentos ID")
    correo = fields.Char(string='Correo')
    direccion = fields.Char(string='Dirección')
    telefono = fields.Char(string='Teléfono')
    # Campos de Documentos del Representante Legal
    cedula_representante = fields.Boolean(string="Cédula Representante")
    rut = fields.Boolean(string="RUT")
    c_procuraduria = fields.Boolean(string="C. Procuraduría")
    c_contraloria = fields.Boolean(string="C. Contraloría")
    c_antecedentes_judiciales = fields.Boolean(string="C. Antecedentes Judiciales")
    c_medidas_correctivas = fields.Boolean(string="C. Medidas Correctivas")
    c_no_inhabilidades = fields.Boolean(string="C. No inhabilidades")

    # Campos de Documentos Persona Jurídica
    c_pagos_parafiscales = fields.Boolean(string="C. Pagos Parafiscales")
    experiencia_contractual = fields.Boolean(string="Experiencia Contractual")
    c_contador_revisor = fields.Boolean(string="C. Contador / Revisor")
    c_procuraduria_entidad = fields.Boolean(string="C. Procuraduría Entidad")
    c_camara_comercio = fields.Boolean(string="C. Cámara de Comercio")
    c_contraloria_entidad = fields.Boolean(string="C. Contraloría Entidad")
    rut_empresa = fields.Boolean(string="RUT Empresa")
    c_resolucion_facturacion = fields.Boolean(string="C. Resolución Facturación")
    hdv_juridica = fields.Boolean(string="HDV / Jurídica")
    c_bancaria = fields.Boolean(string="C. Bancaria")
    c_rup = fields.Boolean(string="C. RUP")
    polizas = fields.Boolean(string="Pólizas (Si Aplica)")
    propuesta_economica = fields.Boolean(string="Propuesta Económica")

    # Campos de Documentos Fundacion Universidad del Valle
    formulario_contratacion = fields.Boolean(string="Formulario de Contratación")
    cdp = fields.Boolean(string="CDP")
    formulario_arl = fields.Boolean(string="Formulario ARL")
    acta_aprobacion_polizas = fields.Boolean(string="Acta de Aprobación de Pólizas (Si Aplica)")
    acta_inicio = fields.Boolean(string="Acta de Inicio (Si Aplica)")
    acta_terminacion = fields.Boolean(string="Acta de Terminación Anticipada (Si Aplica)")
    acta_liquidacion = fields.Boolean(string="Acta de Liquidación (Si Aplica)")
    # Campo Inferior observaciones
    observaciones = fields.Text(string="Observaciones:")
    # Agrega los campos de documentos y otros campos aquí.

    # Campo relacionado para buscar por "nit" en "PersonaJuridica".
    gestion_id = fields.Many2one(comodel_name="persona_juridica", string="Persona Jurídica", ondelete="cascade", domain="[('nit', '=', nit)]")

    gestion_id = fields.Many2one('persona_juridica', string="Buscar NIT (solo número sin DV ni guiones o espacios)")

    # Campo para controlar la visibilidad de los campos en la vista.
    show_fields = fields.Boolean(string="Mostrar Campos", default=False)

    def toggle_show_fields(self):
        self.ensure_one()

        if self.gestion_id:
            # Carga los datos de la entidad seleccionada en los campos correspondientes.
            self.razon_social = self.gestion_id.razon_social
            self.documentos_id = self.gestion_id.documentos_id
            self.correo = self.gestion_id.correo
            self.direccion = self.gestion_id.direccion
            self.telefono = self.gestion_id.telefono

            self.cedula_representante = self.gestion_id.cedula_representante
            self.rut = self.gestion_id.rut
            self.c_procuraduria = self.gestion_id.c_procuraduria
            self.c_contraloria = self.gestion_id.c_contraloria
            self.c_antecedentes_judiciales = self.gestion_id.c_antecedentes_judiciales
            self.c_medidas_correctivas = self.gestion_id.razon_social
            self.c_no_inhabilidades = self.gestion_id.c_no_inhabilidades

            self.c_pagos_parafiscales = self.gestion_id.c_pagos_parafiscales
            self.experiencia_contractual = self.gestion_id.experiencia_contractual
            self.c_contador_revisor = self.gestion_id.c_contador_revisor
            self.c_procuraduria_entidad = self.gestion_id.c_procuraduria_entidad
            self.c_camara_comercio = self.gestion_id.c_camara_comercio
            self.c_contraloria_entidad = self.gestion_id.c_contraloria_entidad
            self.rut_empresa = self.gestion_id.rut_empresa
            self.c_resolucion_facturacion = self.gestion_id.c_resolucion_facturacion
            self.hdv_juridica = self.gestion_id.hdv_juridica
            self.c_bancaria = self.gestion_id.c_bancaria
            self.c_rup = self.gestion_id.c_rup
            self.polizas = self.gestion_id.polizas
            self.propuesta_economica = self.gestion_id.propuesta_economica

            self.formulario_contratacion = self.gestion_id.formulario_contratacion
            self.cdp = self.gestion_id.cdp
            self.formulario_arl = self.gestion_id.formulario_arl
            self.acta_aprobacion_polizas = self.gestion_id.acta_aprobacion_polizas
            self.acta_inicio = self.gestion_id.acta_inicio
            self.acta_terminacion = self.gestion_id.acta_terminacion
            self.acta_liquidacion = self.gestion_id.acta_liquidacion
            self.observaciones = self.gestion_id.observaciones
            # Agrega código similar para otros campos que desees cargar.

        # Cambia el valor de "show_fields" al contrario del valor actual.
        self.write({'show_fields': not self.show_fields})

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'persona_juridica_visualizacion',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def action_eliminar(self):
        self.ensure_one()
        # Verifica si hay un registro asociado a gestion_id
        if self.gestion_id:
            # Elimina el registro asociado a gestion_id
            self.gestion_id.unlink()
        # Lógica adicional si la necesitas
        return {
            'name': 'Ingreso informacion BD Contratistas PERSONA JURIDICA',
            'view_mode': 'form',
            'res_model': 'persona_juridica_visualizacion',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def action_modificar(self):
        # Abre una ventana para modificar los datos de gestion_id
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'persona_juridica',
            'view_mode': 'form',
            'res_id': self.gestion_id.id,
            'target': 'new',
        }

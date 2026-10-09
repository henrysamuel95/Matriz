#!/usr/bin/python

from odoo import models, fields, api

class PersonaJuridicaVisualizacion(models.Model):
    _name = "persona_natural_visualizacion"
    _description = "Visualización de Persona Jurídica"
    _inherit = "persona_natural"

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

    # Campos de Documentos Persona Natural
    hdv = fields.Boolean(string="HDV")
    c_estudios = fields.Boolean(string="C. Estudios")
    c_laboral = fields.Boolean(string="C. Laboral")
    c_preocupacional = fields.Boolean(string="C. Preocupacional")
    c_afiliacion_salud = fields.Boolean(string="C. Afiliación Salud")
    c_afiliacion_arl = fields.Boolean(string="C. Afiliación ARL")
    c_afiliacion_pension = fields.Boolean(string="C. Afiliación Pensión")
    copia_libreta_militar = fields.Boolean(string="Copia Libreta Militar")
    c_copia_tp = fields.Boolean(string="C. Copia o TP")
    c_polizas = fields.Boolean(string="C. Pólizas (Si Aplica)")
    c_bancaria = fields.Boolean(string="C. Bancaria")

    # Campos de Documentos Fundacion Universidad del Valle
    formulario_contratacion = fields.Boolean(string="Formulario de contratación")
    cdp = fields.Boolean(string="CDP")
    formulario_arl = fields.Boolean(string="Formulario ARL")
    acta_aprobacion_polizas = fields.Boolean(string="Acta de aprobación de pólizas (Si Aplica)")
    acta_inicio = fields.Boolean(string="Acta de inicio (Si Aplica)")
    acta_terminacion = fields.Boolean(string="Acta de Terminación Anticipada (Si Aplica)")
    acta_liquidacion = fields.Boolean(string="Acta de liquidación (Si Aplica)")
    numero_contrato_orden_servicio = fields.Char(string="Número de contrato/Orden de servicio")
    # Campo Inferior observaciones
    observaciones = fields.Text(string="Observaciones:")

    # Campo relacionado para buscar por "nit" en "PersonaNatural".
    gestion_id = fields.Many2one(comodel_name="persona_natural", string="Persona Natural", ondelete="cascade", domain="[('nit', '=', nit)]")

    gestion_id = fields.Many2one('persona_natural', string="Buscar NIT (solo número sin DV ni guiones o espacios)")

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
            self.c_medidas_correctivas = self.gestion_id.c_medidas_correctivas
            self.c_no_inhabilidades = self.gestion_id.c_no_inhabilidades

            self.hdv = self.gestion_id.hdv
            self.c_estudios = self.gestion_id.c_estudios
            self.c_laboral = self.gestion_id.c_laboral
            self.c_preocupacional = self.gestion_id.c_preocupacional
            self.c_afiliacion_salud = self.gestion_id.c_afiliacion_salud
            self.c_afiliacion_arl = self.gestion_id.c_afiliacion_arl
            self.c_afiliacion_pension = self.gestion_id.c_afiliacion_pension
            self.copia_libreta_militar = self.gestion_id.copia_libreta_militar
            self.c_copia_tp = self.gestion_id.c_copia_tp
            self.c_polizas = self.gestion_id.c_polizas
            self.c_bancaria = self.gestion_id.c_bancaria

            self.formulario_contratacion = self.gestion_id.formulario_contratacion
            self.cdp = self.gestion_id.cdp
            self.formulario_arl = self.gestion_id.formulario_arl
            self.acta_aprobacion_polizas = self.gestion_id.acta_aprobacion_polizas
            self.acta_inicio = self.gestion_id.acta_inicio
            self.acta_terminacion = self.gestion_id.acta_terminacion
            self.acta_liquidacion = self.gestion_id.acta_liquidacion
            self.numero_contrato_orden_servicio = self.gestion_id.numero_contrato_orden_servicio
            self.observaciones = self.gestion_id.observaciones

            # Agrega código similar para otros campos que desees cargar.

        # Cambia el valor de "show_fields" al contrario del valor actual.
        self.write({'show_fields': not self.show_fields})

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'persona_natural_visualizacion',
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
            'name': 'Ingreso informacion BD Contratistas PERSONA NATURAL',
            'view_mode': 'form',
            'res_model': 'persona_natural_visualizacion',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def action_modificar(self):
        # Abre una ventana para modificar los datos de gestion_id
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'persona_natural',
            'view_mode': 'form',
            'res_id': self.gestion_id.id,
            'target': 'new',
        }

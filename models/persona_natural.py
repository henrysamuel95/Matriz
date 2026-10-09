#!/usr/bin/python

from odoo import models, fields, api


class PersonaNatural(models.Model):
    _name = "persona_natural"
    name = fields.Char(string='Nombre')
    new_name = fields.Char(string='Nuevo nombre')

    nit = fields.Char(string="Buscar por NIT")
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
    #Botones
    def action_agregar(self):
        # Lógica para agregar registros en "PersonaJuridicaVisualizacion".
        self.write({'name': self.new_name})
        for persona_natural in self:
            # Crea un registro en "PersonaJuridicaVisualizacion" con los valores correspondientes.
            visualizacion_values = {
                'razon_social': persona_natural.razon_social,
                'documentos_id': persona_natural.documentos_id,
                'correo': persona_natural.correo,
                'direccion': persona_natural.direccion,
                'telefono': persona_natural.telefono,

                'cedula_representante': persona_natural.cedula_representante,
                'rut': persona_natural.rut,
                'c_procuraduria': persona_natural.c_procuraduria,
                'c_contraloria': persona_natural.c_contraloria,
                'c_antecedentes_judiciales': persona_natural.c_antecedentes_judiciales,
                'c_medidas_correctivas': persona_natural.c_medidas_correctivas,
                'c_no_inhabilidades': persona_natural.c_no_inhabilidades,

                'hdv': persona_natural.hdv,
                'c_estudios': persona_natural.c_estudios,
                'c_laboral': persona_natural.c_laboral,
                'c_preocupacional': persona_natural.c_preocupacional,
                'c_afiliacion_salud': persona_natural.c_afiliacion_salud,
                'c_afiliacion_arl': persona_natural.c_afiliacion_arl,
                'c_afiliacion_pension': persona_natural.c_afiliacion_pension,
                'copia_libreta_militar': persona_natural.copia_libreta_militar,
                'c_copia_tp': persona_natural.c_copia_tp,
                'c_polizas': persona_natural.c_polizas,
                'c_bancaria': persona_natural.c_bancaria,

                'formulario_contratacion': persona_natural.formulario_contratacion,
                'cdp': persona_natural.cdp,
                'formulario_arl': persona_natural.formulario_arl,
                'acta_aprobacion_polizas': persona_natural.acta_aprobacion_polizas,
                'acta_inicio': persona_natural.acta_inicio,
                'acta_terminacion': persona_natural.acta_terminacion,
                'acta_liquidacion': persona_natural.acta_liquidacion,
                'numero_contrato_orden_servicio': persona_natural.numero_contrato_orden_servicio,
                'observaciones': persona_natural.observaciones,

                # Agrega los valores de otros campos de documentos y campos adicionales aquí.
            }
            # Crea el registro en "PersonaNaturalVisualizacion".
            visualizacion_record = self.env['persona_natural_visualizacion'].create(visualizacion_values)

            # Establece la relación "gestion_id" en el registro creado.
            visualizacion_record.gestion_id = persona_natural.id


    def action_cerrar(self):
        # Lógica para cerrar la vista o realizar alguna acción de cierre
        return





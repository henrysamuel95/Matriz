#!/usr/bin/python

from odoo import models, fields, api


class PersonaJuridica(models.Model):
    _name = "persona_juridica"

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
    #Campo Inferior observaciones
    observaciones = fields.Text(string="Observaciones:")

    #Botones
    def action_agregar(self):
        # Obtener el ID del proyecto actual desde el contexto
        id_proyecto_actual = self._context.get('active_id')

        # Asegurarse de que hay un ID del proyecto
        if id_proyecto_actual:
            print("ID del proyecto actual:", id_proyecto_actual)

            # Obtener el objeto contratacion asociado al ID del proyecto actual
            contratacion_record = self.env['contratacion'].browse(id_proyecto_actual)

            # Lógica para cambiar el estado
            if contratacion_record.coordinador == 'red':
                contratacion_record.write({'coordinador': 'yellow'})
        else:
            print("No se pudo obtener el ID del proyecto actual.")
        # Lógica para agregar registros en "PersonaJuridicaVisualizacion".
        self.write({'name': self.new_name})
        for persona_juridica in self:
            # Crea un registro en "PersonaJuridicaVisualizacion" con los valores correspondientes.
            visualizacion_values = {
                'razon_social': persona_juridica.razon_social,
                'documentos_id': persona_juridica.documentos_id,
                'correo': persona_juridica.correo,
                'direccion': persona_juridica.direccion,
                'telefono': persona_juridica.telefono,

                'cedula_representante': persona_juridica.cedula_representante,
                'rut': persona_juridica.rut,
                'c_procuraduria': persona_juridica.c_procuraduria,
                'c_contraloria': persona_juridica.c_contraloria,
                'c_antecedentes_judiciales': persona_juridica.c_antecedentes_judiciales,

                'c_medidas_correctivas': persona_juridica.c_medidas_correctivas,
                'c_no_inhabilidades': persona_juridica.c_no_inhabilidades,
                'c_pagos_parafiscales': persona_juridica.c_pagos_parafiscales,
                'experiencia_contractual': persona_juridica.experiencia_contractual,
                'c_contador_revisor': persona_juridica.c_contador_revisor,

                'c_procuraduria_entidad': persona_juridica.c_procuraduria_entidad,
                'c_camara_comercio': persona_juridica.c_camara_comercio,
                'c_contraloria_entidad': persona_juridica.c_contraloria_entidad,
                'rut_empresa': persona_juridica.rut_empresa,
                'c_resolucion_facturacion': persona_juridica.c_resolucion_facturacion,

                'hdv_juridica': persona_juridica.hdv_juridica,
                'c_bancaria': persona_juridica.c_bancaria,
                'c_rup': persona_juridica.c_rup,
                'polizas': persona_juridica.polizas,
                'propuesta_economica': persona_juridica.propuesta_economica,

                'formulario_contratacion': persona_juridica.formulario_contratacion,
                'cdp': persona_juridica.cdp,
                'formulario_arl': persona_juridica.formulario_arl,
                'acta_aprobacion_polizas': persona_juridica.acta_aprobacion_polizas,
                'acta_inicio': persona_juridica.acta_inicio,
                'acta_terminacion': persona_juridica.acta_terminacion,
                'acta_liquidacion': persona_juridica.acta_liquidacion,
                'observaciones': persona_juridica.observaciones,

                # Agrega los valores de otros campos de documentos y campos adicionales aquí.
            }
            # Crea el registro en "PersonaJuridicaVisualizacion".
            visualizacion_record = self.env['persona_juridica_visualizacion'].create(visualizacion_values)

            # Establece la relación "gestion_id" en el registro creado.
            visualizacion_record.gestion_id = persona_juridica.id


    def action_cerrar(self):
        # Lógica para cerrar la vista o realizar alguna acción de cierre
        return





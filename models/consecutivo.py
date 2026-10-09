#!/usr/bin/python

from odoo import fields, models, api

import logging

_logger = logging.getLogger(__name__)

class Consecutivo(models.Model):
    _name = 'consecutivo'
    _description = 'Consecutivos'
    _order = 'combinacion asc, contador asc'  # Ordenar por 'combinacion' de manera ascendente

    consecutivo = fields.Char(string='Consecutivos')
    name = fields.Char(string='Nombre')
    contratatos_id = fields.Many2one('contrataciones_fuv', string='Nombre', ondelete='cascade')

    def crear_consecutivos_para_contrataciones(self):
        contrataciones = self.env['contrataciones_fuv'].search([], order='create_date asc')

        for contratacion in contrataciones:
            try:
                consecutivo_existente = self.search([('contratatos_id', '=', contratacion.id)], limit=1)

                if consecutivo_existente:
                    continue  # Si ya existe, omitir la creación

                nuevo_consecutivo = self.create({
                    'name': contratacion.name,
                    'contratatos_id': contratacion.id,
                    'tipo_contrato_id': contratacion.tipo_contrato_id.id,
                })

                anio_terminacion = nuevo_consecutivo.anio_terminacion or ""
                combinacion = nuevo_consecutivo.combinacion or ""
                contador = nuevo_consecutivo.contador or ""

                consecutivo_value = f"FUV.{combinacion}.{contador}.{anio_terminacion}"

                nuevo_consecutivo.write({
                    'consecutivo': consecutivo_value
                })

                _logger.info(f"Consecutivo creado para {contratacion.name}: {consecutivo_value}")

            except Exception as e:
                _logger.error(f"Error al crear consecutivo para {contratacion.name}: {str(e)}")



    fecha_terminacion = fields.Date('Fecha de terminación', tracking=True, related='contratatos_id.fecha_terminacion')
    anio_terminacion = fields.Char(string='Año', compute='compute_anio_terminacion', store=True)

    @api.depends('fecha_terminacion')
    def compute_anio_terminacion(self):
        """
        Calcula el año basado en la fecha de terminación.
        """
        for record in self:
            if record.fecha_terminacion:
                # Extraer el año de la fecha
                record.anio_terminacion = record.fecha_terminacion.year
            else:
                record.anio_terminacion = " "

    def generar_consecutivo(self):
        """
        Genera y actualiza el campo 'consecutivo' en el formato FUV.{combinacion}.{contador}.{anio_terminacion}
        """
        for consecutivo in self:
            # Acceder a los valores de 'anio_terminacion', 'combinacion' y 'contador'
            anio_terminacion = consecutivo.anio_terminacion or ""
            combinacion = consecutivo.combinacion or ""
            contador = consecutivo.contador or ""

            # Crear el consecutivo en el formato requerido
            consecutivo_value = f"FUV.{combinacion}.{contador}.{anio_terminacion}"

            # Actualizar el campo 'consecutivo' con el valor calculado
            consecutivo.write({
                'consecutivo': consecutivo_value
            })

            _logger.info(f"Consecutivo generado para {consecutivo.name}: {consecutivo_value}")

        return True

    select_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica')
    ], string='Persona', tracking=True, related='contratatos_id.select_persona')

    tipo_contrato_id = fields.Many2one(
        comodel_name='tipo.contrato',
        string='Tipo de contrato',
        tracking=True,
    )

    combinacion = fields.Char(string='Combinación', compute='_compute_combinacion', store=True)

    @api.depends('select_persona', 'contratatos_id.tipo_contrato_id')
    def _compute_combinacion(self):
        """
        Calcula la combinación basada en select_persona y tipo_contrato.
        El tipo de contrato se toma del texto dentro de los paréntesis.
        """
        for record in self:
            persona_prefix = ''
            contrato_prefix = ''

            # Determina el prefijo de persona
            if record.select_persona == 'persona_natural':
                persona_prefix = 'PN'
            elif record.select_persona == 'persona_juridica':
                persona_prefix = 'PJ'

            # Determina el prefijo del tipo de contrato
            if record.contratatos_id and record.contratatos_id.tipo_contrato_id:
                contrato_texto = record.contratatos_id.tipo_contrato_id.name or ''
                if '(' in contrato_texto and ')' in contrato_texto:
                    contrato_prefix = contrato_texto.split('(')[-1].split(')')[0]

            # Construye la combinación
            record.combinacion = f"{persona_prefix}.{contrato_prefix}" if persona_prefix and contrato_prefix else " "

    # Campo contador

    contador = fields.Char(string='Contador', compute='compute_contador', store=True)

    @api.depends('combinacion')
    def compute_contador(self):
        for record in self:
            self.generar_consecutivo()
            # Verificar la combinación específica para este registro
            if record.combinacion:
                # Buscar los registros con la misma combinación
                similar_records = self.search([('combinacion', '=', record.combinacion)], order='id asc')
                # Contar cuántos registros existen con esa combinación y asignar el contador
                contador = len(similar_records)
                # El contador empieza desde 001 para cada combinación
                record.contador = str(contador).zfill(3)



class TipoContrato(models.Model):
    _name = 'tipo.contrato'
    _description = 'Tipo de Contrato'

    name = fields.Char(string='Nombre del tipo de contrato')
    code = fields.Char(string='Clave del tipo de contrato')

    def abrir_tipo_contrato(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tipo de Contrato',
            'res_model': 'tipo.contrato',
            'view_mode': 'tree',
            'target': 'current',
        }

    @api.onchange('code')
    def _onchange_code(self):
        """
        Actualiza el campo 'name' agregando el 'code' entre paréntesis.
        """
        if self.code:
            self.name = f"{self.name or ''} ({self.code})"


    def create_default_contract_types(self):
        """Crea los registros por defecto si no existen."""
        default_types = [
            ('CI', 'Carta de Instrucción (CI)'),
            ('CC', 'Contrato de Consultoria (CC)'),
            ('CO', 'Contrato de Obra (CO)'),
            ('CIA', 'Contrato Inter Administrativo (CIA)'),
            ('CPS', 'Contrato de Prestacion de Servicios (CPS)'),
            ('CM', 'Contrato de mandato'),
            ('C', 'Consorcio'),
            ('UT', 'Unión temporal'),
            ('OC', 'Orden de Compra (OC)'),
            ('OS', 'Orden de Servicios (OS)'),
            ('CS', 'Contrato de Suministro (CS)'),
            ('CA', 'Convenio de Asociacion (CA)'),
            ('CCF', 'Convenio de Cofinanciacion (CCF)'),
            ('CEC', 'Convenio Especial de Cooperacion (CEC)'),
            ('CIA', 'Convenio Interadministrativo (CIA)'),
        ]

        for code, name in default_types:
            if not self.search([('code', '=', code)]):
                self.create({'name': name, 'code': code})

class ModalidadContratacion(models.Model):
    _name = 'modalidad_contratacion'
    _description = 'Modalidad de Contratación'

    name = fields.Char(string='Nombre de Modalidad de Contratación')
    code = fields.Char(string='Clave de Modalidad de Contratación')

    def abrir_modalidad_contratacion(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Modalidad de Contratación',
            'res_model': 'modalidad_contratacion',
            'view_mode': 'tree',
            'target': 'current',
        }

    @api.onchange('code')
    def _onchange_code(self):
        """
        Actualiza el campo 'name' agregando el 'code' entre paréntesis.
        """
        if self.code:
            self.name = f"{self.name or ''} ({self.code})"


    def create_default_contract_types(self):
        """Crea los registros por defecto si no existen."""
        default_types = [
            ('CI', 'Carta de Instrucción (CI)'),
            ('CC', 'Contrato de Consultoria (CC)'),
            ('CO', 'Contrato de Obra (CO)'),
            ('CIA', 'Contrato Interadministrativo (CIA)'),
            ('CLP', 'Contrato de Licitaciones Publicas (CLP)'),
            ('CPS', 'Contrato de Prestacion de Servicios (CPS)'),
            ('OC', 'Orden de Compra (OC)'),
            ('OS', 'Orden de Servicios (OS)'),
            ('CS', 'Contrato de Suministro (CS)'),
            ('CA', 'Convenio de Asociacion (CA)'),
            ('CCF', 'Convenio de Cofinanciacion (CCF)'),
            ('CEC', 'Convenio Especial de Cooperacion (CEC)'),
            ('CIA', 'Convenio Interadministrativo (CIA)'),
        ]

        for code, name in default_types:
            if not self.search([('code', '=', code)]):
                self.create({'name': name, 'code': code})
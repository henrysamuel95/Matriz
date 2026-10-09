from odoo import api, fields, models


class ContratacionesSgrTablas(models.Model):
    _inherit = 'contrataciones_sgr'

    mcb_centro_costo = fields.Char(
        string='Unidad de negocio', related='proyecto_relacionado.mcb_centro_costo',
        readonly=True,
    )
    departamentos_y_municipios_ids = fields.One2many(
        'contrataciones_sgr.departamento_municipio', 'contratacion_sgr_id',
        string='Departamentos y municipios',
    )
    tabla_de_forma_de_pago = fields.One2many(
        'contrataciones_sgr.forma_pago', 'contratacion_sgr_id',
        string='Tabla para formas de pagos',
    )
    numero_de_cuotas = fields.Integer(string='Número de cuotas', default=0)
    total_pago_porcentaje = fields.Float(
        string='Total % de pago', compute='_compute_totales_pago',
    )
    total_de_cantidad_de_dinero = fields.Monetary(
        string='Monto total', currency_field='currency_id', compute='_compute_totales_pago',
    )
    total_porcentaje_tecnico = fields.Float(
        string='Total porcentaje técnico', compute='_compute_totales_pago',
    )

    @api.onchange('numero_de_cuotas')
    def _onchange_numero_de_cuotas(self):
        for contrato in self:
            cantidad = max(0, contrato.numero_de_cuotas or 0)
            lineas = contrato.tabla_de_forma_de_pago
            if cantidad < len(lineas):
                contrato.tabla_de_forma_de_pago = lineas[:cantidad]
            elif cantidad > len(lineas):
                nuevas = self.env['contrataciones_sgr.forma_pago']
                for numero in range(len(lineas) + 1, cantidad + 1):
                    nuevas += nuevas.new({'numero_de_pagos': numero})
                contrato.tabla_de_forma_de_pago += nuevas

    @api.onchange('tabla_de_forma_de_pago')
    def _onchange_tabla_de_forma_de_pago(self):
        for contrato in self:
            for numero, linea in enumerate(contrato.tabla_de_forma_de_pago, start=1):
                linea.numero_de_pagos = numero
            contrato.numero_de_cuotas = len(contrato.tabla_de_forma_de_pago)

    @api.depends('tabla_de_forma_de_pago.cantidad_de_dinero',
                 'tabla_de_forma_de_pago.tecnico', 'valor_total_contrato')
    def _compute_totales_pago(self):
        for contrato in self:
            monto = sum(contrato.tabla_de_forma_de_pago.mapped('cantidad_de_dinero'))
            contrato.total_de_cantidad_de_dinero = monto
            contrato.total_pago_porcentaje = (
                monto / contrato.valor_total_contrato * 100
                if contrato.valor_total_contrato else 0.0
            )
            contrato.total_porcentaje_tecnico = sum(
                contrato.tabla_de_forma_de_pago.mapped('tecnico')
            )


class ContratacionesSgrDepartamentoMunicipio(models.Model):
    _name = 'contrataciones_sgr.departamento_municipio'
    _description = 'Departamento y municipio de contratación SGR'

    contratacion_sgr_id = fields.Many2one(
        'contrataciones_sgr', required=True, ondelete='cascade', index=True,
    )
    departamentos = fields.Many2one('departamento', string='Departamento')
    municipios2 = fields.Many2one('municipio', string='Municipio')

    @api.onchange('departamentos')
    def _onchange_departamentos(self):
        self.municipios2 = False
        codigo = self.departamentos.Codigo_Departamento
        if codigo:
            return {'domain': {'municipios2': [
                ('Codigo_Municipio', '=like', '%s%%' % codigo[:2]),
            ]}}
        return {'domain': {'municipios2': []}}


class ContratacionesSgrFormaPago(models.Model):
    _name = 'contrataciones_sgr.forma_pago'
    _description = 'Cuota de pago de contratación SGR'
    _order = 'numero_de_pagos, id'

    contratacion_sgr_id = fields.Many2one(
        'contrataciones_sgr', required=True, ondelete='cascade', index=True,
    )
    numero_de_pagos = fields.Integer(string='# Pagos', default=1)
    cantidad_de_dinero = fields.Monetary(
        string='Cantidad de dinero', currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        'res.currency', related='contratacion_sgr_id.currency_id', readonly=True,
    )
    pago = fields.Float(string='% Pago', compute='_compute_porcentajes')
    porcentaje_acumulado = fields.Float(
        string='% Acumulado', compute='_compute_porcentajes',
    )
    tecnico = fields.Float(string='% Técnico')
    condicion = fields.Char(string='Observaciones')

    @api.depends('cantidad_de_dinero', 'numero_de_pagos',
                 'contratacion_sgr_id.valor_total_contrato',
                 'contratacion_sgr_id.tabla_de_forma_de_pago.cantidad_de_dinero',
                 'contratacion_sgr_id.tabla_de_forma_de_pago.numero_de_pagos')
    def _compute_porcentajes(self):
        for linea in self:
            contrato = linea.contratacion_sgr_id
            total = contrato.valor_total_contrato
            linea.pago = linea.cantidad_de_dinero / total * 100 if total else 0.0
            acumulado = 0.0
            for cuota in contrato.tabla_de_forma_de_pago.sorted('numero_de_pagos'):
                acumulado += cuota.cantidad_de_dinero
                if cuota == linea:
                    break
            linea.porcentaje_acumulado = acumulado / total * 100 if total else 0.0

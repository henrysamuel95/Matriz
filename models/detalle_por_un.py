#!/usr/bin/python
# -*- coding: utf-8 -*-

from odoo import fields, models


class DetallePorUN(models.Model):
    _name = 'detalle.por.un'
    _description = 'Detalles'
    _rec_name = 'nombre'
    _order = 'un, contrato, numero_pago_cuota, id'

    active = fields.Boolean(string='Activo', default=True)
    tipo_movimiento = fields.Selection(
        [
            ('egreso', 'Egreso'),
            ('ingreso', 'Ingreso'),
        ],
        string='Tipo de movimiento',
        default='egreso',
    )

    un = fields.Char(string='UN')
    contrato = fields.Char(string='CONTRATO')
    numero_pago_cuota = fields.Char(string='NUMERO DE PAGO O CUOTA')
    fecha_causacion = fields.Date(string='FECHA CAUSACION')
    causacion = fields.Char(string='CAUSACION')
    factura = fields.Char(string='FACTURA')
    identificacion = fields.Char(string='IDENTIFICACION')
    nombre = fields.Char(string='NOMBRE')

    valor_cobrado = fields.Float(string='VALOR COBRADO')
    retenciones = fields.Float(string='RETENCIONES')
    otras_deducciones = fields.Float(string='OTRAS DEDUCCIONES')
    neto_a_pagar = fields.Float(string='NETO A PAGAR')
    neto_a_recibir = fields.Float(string='NETO A RECIBIR')
    pagado = fields.Float(string='PAGADO')
    valor_recibido = fields.Float(string='VALOR RECIBIDO')
    fecha = fields.Date(string='FECHA')
    documento = fields.Char(string='DOCUMENTO')
    documento_recibo_caja = fields.Char(string='DOCUMENTO (RECIBO DE CAJA)')
    saldo = fields.Float(string='SALDO')
    saldo_por_cobrar = fields.Float(string='SALDO POR COBRAR')

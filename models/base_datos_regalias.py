#!/usr/bin/python
from odoo import fields, models


class BaseDatosRegalias(models.Model):
    _name = 'base.datos.regalias'
    _description = 'Base de datos de Regalías - PROYECTOS'
    _order = 'id desc'
    _rec_name = 'nombre_proyecto'

    unidad_de_negocio = fields.Char(string='UNIDAD DE NEGOCIO')
    nombre_proyecto = fields.Char(string='NOMBRE PROYECTO')
    bpin = fields.Char(string='BPIN')
    objeto_del_proyecto = fields.Text(string='OBJETO DEL PROYECTO')
    valor_de_presupuesto_2025_2026 = fields.Char(string='VALOR DE PRESUPUESTO 2025-2026')
    valor_ya_ejecutado_al_31_de_agosto_de_2026 = fields.Char(string='VALOR YA EJECUTADO AL 31 DE AGOSTO DE 2026')
    saldo_del_proyecto_por_ejecutar = fields.Char(string='SALDO DEL PROYECTO POR EJECUTAR')


class BaseDatosRegaliasContrato(models.Model):
    _name = 'base.datos.regalias.contrato'
    _description = 'Base de datos de Regalías - CONTRATOS'
    _order = 'id desc'
    _rec_name = 'razon_social'

    un = fields.Char(string='UN')
    rpc = fields.Char(string='RPC')
    tipo_identificacion = fields.Char(string='TIPO IDENTIFICACION')
    identificacion = fields.Char(string='IDENTIFICACION')
    razon_social = fields.Char(string='RAZON SOCIAL')
    contraro = fields.Char(string='CONTRARO')
    numero_de_contrato = fields.Char(string='NUMERO DE CONTRATO')
    valor_de_contrato_vigencia_2025_2026 = fields.Char(string='Valor DE CONTRATO VIGENCIA 2025-2026')
    saldo_por_utilizar = fields.Char(string='Saldo por Utilizar')
    resumen_objeto_contractual = fields.Text(string='RESUMEN OBJETO CONTRACTUAL')
    fuente = fields.Char(string='Fuente')
    rubro = fields.Char(string='RUBRO')
    recurso = fields.Char(string='Recurso')

    def name_get(self):
        result = []
        for record in self:
            parts = [
                record.numero_de_contrato,
                record.razon_social,
                record.saldo_por_utilizar and ('Saldo: %s' % record.saldo_por_utilizar),
            ]
            result.append((record.id, ' — '.join(part for part in parts if part) or record.rpc or str(record.id)))
        return result

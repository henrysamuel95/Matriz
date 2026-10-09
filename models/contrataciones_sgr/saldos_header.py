from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


FONDOS_SGR = {
    'es_produccion': 'Producción',
    'es_personal_fuv': 'Costos FUV',
    'contratos_vco': 'Contratos VCO',
}


class ContratacionesSgrSaldosHeader(models.Model):
    _inherit = 'contrataciones_sgr'

    sgr_disponible_produccion = fields.Monetary(
        string='Saldo disponible (Producción)',
        compute='_compute_saldos_sgr', currency_field='currency_id',
    )
    sgr_disponible_costos_fuv = fields.Monetary(
        string='Saldo disponible (Costos FUV)',
        compute='_compute_saldos_sgr', currency_field='currency_id',
    )
    sgr_disponible_vco = fields.Monetary(
        string='Saldo disponible (Contratos VCO)',
        compute='_compute_saldos_sgr', currency_field='currency_id',
    )
    sgr_disponible_contratos = fields.Monetary(
        string='Saldo disponible del fondo seleccionado',
        compute='_compute_saldos_sgr', currency_field='currency_id',
    )

    def _sgr_saldos_sin_contratos_sgr(self, proyecto):
        proyecto_id = proyecto.id
        saldos = {
            'es_produccion': proyecto.new_total_costos_produccion_N_r or 0.0,
            'es_personal_fuv': proyecto.suma_costos_fuv or 0.0,
            'contratos_vco': proyecto.contratos_vco_resumen or 0.0,
        }

        contratos_fuv = self.env['contrataciones_fuv'].sudo().search([
            ('proyecto_relacionado', '=', proyecto_id),
        ])
        for contrato in contratos_fuv:
            if contrato.select_fondos in saldos:
                saldos[contrato.select_fondos] -= contrato.valor_total_contrato or 0.0

        fondos_otrosi = {'es_vco': 'contratos_vco'}
        otrosi = self.env['otro_si'].sudo().search([
            ('contratacion_fuv_en_id.proyecto_relacionado', '=', proyecto_id),
        ])
        for modificacion in otrosi:
            fondo = fondos_otrosi.get(modificacion.select_fondos, modificacion.select_fondos)
            if fondo in saldos:
                saldos[fondo] -= modificacion.valor_total_contrato or 0.0

        otrosi_sgr = self.env['otro_si_sgr'].sudo().search([
            ('contratacion_id.proyecto_relacionado', '=', proyecto_id),
        ])
        for modificacion in otrosi_sgr:
            fondo = modificacion.select_fondos
            if fondo in saldos:
                saldos[fondo] -= modificacion.valor_adicion or 0.0

        bienes = self.env['orden_compra_bienes'].sudo().search([
            ('proyecto_relacionado_c', '=', proyecto_id),
        ])
        servicios_produccion = self.env['orden_compra_servicios'].sudo().search([
            ('proyecto_relacionado_s', '=', proyecto_id),
            ('select_fondos', '=', 'es_produccion'),
        ])
        servicios_poliza = self.env['orden_compra_servicios'].sudo().search([
            ('proyecto_relacionado_s', '=', proyecto_id),
            ('select_fondos', '=', 'es_poliza'),
        ])
        ordenes_especiales = self.env['ordenes_especiales'].sudo().search([
            ('proyecto_relacionado_s', '=', proyecto_id),
            ('select_fondos', '=', 'es_produccion'),
        ])
        anticipos = self.env['anticipos'].sudo().search([
            ('proyecto_id', '=', proyecto_id), ('state', '=', 'completado'),
        ])
        viaticos = self.env['viaticos'].sudo().search([
            ('proyecto_relacionado_viaticos', '=', proyecto_id),
            ('state_viaticos', '=', 'completado'),
        ])

        def total_ordenes(ordenes):
            return sum(
                (orden.subtotal or 0.0) + (orden.iva or 0.0)
                + (orden.gastos_expedicion or 0.0)
                for orden in ordenes
            )

        # FUV reserva el saldo de póliza dentro del presupuesto de Producción.
        saldo_poliza = (proyecto.poliza_total or 0.0) - total_ordenes(servicios_poliza)
        saldos['es_produccion'] -= (
            sum(bienes.mapped('total'))
            + total_ordenes(servicios_produccion)
            + total_ordenes(ordenes_especiales)
            + sum(anticipos.mapped('valor_anticipo'))
            + saldo_poliza
            + sum(viaticos.mapped('impacto_negativo_fuv'))
        )
        saldos['es_produccion'] += sum(viaticos.mapped('impacto_positivo_fuv'))

        cierres = self.env['contrataciones_fuv.cierre.proceso'].sudo().search([
            ('proyecto_id', '=', proyecto_id),
            ('estado', '=', 'finalizado'),
            ('valor_liberado_rfc', '>', 0),
        ])
        for cierre in cierres:
            fondo = cierre.fondo_origen or cierre.contract_id.select_fondos
            if fondo in saldos:
                saldos[fondo] += cierre.valor_liberado_rfc
        return saldos

    def _sgr_saldos_proyecto(self, proyecto):
        saldos = self._sgr_saldos_sin_contratos_sgr(proyecto)
        contratos = self.sudo().search([('proyecto_relacionado', '=', proyecto.id)])
        for contrato in contratos:
            if contrato.select_fondos in saldos:
                saldos[contrato.select_fondos] -= contrato.valor_total_contrato or 0.0
        return saldos

    def _sgr_resumen_proyecto(self, proyecto):
        saldos = self._sgr_saldos_proyecto(proyecto)
        return {
            **saldos,
            'produccion_utilizada': (
                (proyecto.new_total_costos_produccion_N_r or 0.0)
                - saldos['es_produccion']
            ),
        }

    @api.model
    def get_sgr_saldos_proyecto(self, proyecto_id):
        self.check_access_rights('read')
        proyecto = self.env['contratacion'].browse(proyecto_id).exists()
        if not proyecto:
            raise ValidationError('El proyecto solicitado no existe.')
        proyecto.check_access_rights('read')
        proyecto.check_access_rule('read')
        return self._sgr_resumen_proyecto(proyecto)

    @api.depends(
        'proyecto_relacionado',
        'proyecto_relacionado.new_total_costos_produccion_N_r',
        'proyecto_relacionado.suma_costos_fuv',
        'proyecto_relacionado.contratos_vco_resumen',
        'proyecto_relacionado.poliza_total',
        'select_fondos', 'valor_total_contrato',
        'otrosi_sgr_ids.valor_adicion', 'otrosi_sgr_ids.select_fondos',
    )
    def _compute_saldos_sgr(self):
        proyectos = {}
        for record in self:
            proyecto = record.proyecto_relacionado
            if not proyecto:
                saldos = dict.fromkeys(FONDOS_SGR, 0.0)
            else:
                if proyecto.id not in proyectos:
                    proyectos[proyecto.id] = (
                        self._sgr_saldos_sin_contratos_sgr(proyecto),
                        self.sudo().search([('proyecto_relacionado', '=', proyecto.id)]),
                    )
                base, contratos_sgr = proyectos[proyecto.id]
                saldos = base.copy()
                actual_id = record._origin.id if record._origin else record.id
                for contrato in contratos_sgr:
                    if contrato.id != actual_id and contrato.select_fondos in saldos:
                        saldos[contrato.select_fondos] -= contrato.valor_total_contrato or 0.0
                if record.select_fondos in saldos:
                    saldos[record.select_fondos] -= record.valor_total_contrato or 0.0

            record.sgr_disponible_produccion = saldos['es_produccion']
            record.sgr_disponible_costos_fuv = saldos['es_personal_fuv']
            record.sgr_disponible_vco = saldos['contratos_vco']
            record.sgr_disponible_contratos = saldos.get(record.select_fondos, 0.0)

    @api.constrains('proyecto_relacionado', 'select_fondos', 'valor_total_contrato')
    def _check_saldo_sgr_suficiente(self):
        for record in self:
            if record.valor_total_contrato > 0 and not record.select_fondos:
                raise ValidationError('Seleccione el fondo que financiará este contrato SGR.')
            if record.select_fondos and float_compare(
                record.sgr_disponible_contratos, 0.0,
                precision_rounding=record.currency_id.rounding or 0.01,
            ) < 0:
                raise ValidationError(
                    'Saldo insuficiente en %s. El contrato dejaría un saldo de $%s.' % (
                        FONDOS_SGR[record.select_fondos],
                        format(record.sgr_disponible_contratos, ',.2f'),
                    )
                )

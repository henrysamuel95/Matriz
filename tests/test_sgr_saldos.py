from types import SimpleNamespace

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.saldos_header import ContratacionesSgrSaldosHeader


class _Records(list):
    def mapped(self, field):
        return [getattr(record, field) for record in self]


class _Source:
    def __init__(self, records=()):
        self.records = _Records(records)

    def sudo(self):
        return self

    def search(self, domain):
        def matches(record):
            for field, operator, expected in domain:
                value = record
                for part in field.split('.'):
                    value = getattr(value, part)
                value = getattr(value, 'id', value)
                if operator == '=' and value != expected:
                    return False
                if operator == '>' and value <= expected:
                    return False
            return True

        return _Records(record for record in self.records if matches(record))


class _SgrContext(_Source):
    def __init__(self, current, contracts=(), sources=None):
        super().__init__(contracts)
        self.current = current
        self.env = {
            name: _Source((sources or {}).get(name, ()))
            for name in (
                'contrataciones_fuv', 'otro_si', 'otro_si_sgr', 'orden_compra_bienes',
                'orden_compra_servicios', 'ordenes_especiales', 'anticipos',
                'viaticos', 'contrataciones_fuv.cierre.proceso',
            )
        }

    def __iter__(self):
        return iter((self.current,))

    def _sgr_saldos_sin_contratos_sgr(self, proyecto):
        return ContratacionesSgrSaldosHeader._sgr_saldos_sin_contratos_sgr(self, proyecto)

    def _sgr_saldos_proyecto(self, proyecto):
        return ContratacionesSgrSaldosHeader._sgr_saldos_proyecto(self, proyecto)

    def sudo(self):
        return self


class TestSgrSaldos(TransactionCase):
    def test_project_balances_include_all_sgr_contracts(self):
        project = SimpleNamespace(
            id=10, new_total_costos_produccion_N_r=100.0,
            suma_costos_fuv=50.0, contratos_vco_resumen=80.0,
            poliza_total=0.0,
        )
        contracts = (
            SimpleNamespace(proyecto_relacionado=project, select_fondos='es_produccion',
                            valor_total_contrato=30.0),
            SimpleNamespace(proyecto_relacionado=project, select_fondos='contratos_vco',
                            valor_total_contrato=20.0),
        )
        context = _SgrContext(None, contracts)

        self.assertEqual(
            ContratacionesSgrSaldosHeader._sgr_saldos_proyecto(context, project),
            {'es_produccion': 70.0, 'es_personal_fuv': 50.0, 'contratos_vco': 60.0},
        )
        self.assertEqual(
            ContratacionesSgrSaldosHeader._sgr_resumen_proyecto(context, project)[
                'produccion_utilizada'
            ],
            30.0,
        )
        empty_context = _SgrContext(None)
        self.assertEqual(
            ContratacionesSgrSaldosHeader._sgr_saldos_proyecto(empty_context, project),
            {'es_produccion': 100.0, 'es_personal_fuv': 50.0, 'contratos_vco': 80.0},
        )
        self.assertEqual(
            ContratacionesSgrSaldosHeader._sgr_resumen_proyecto(empty_context, project)[
                'produccion_utilizada'
            ],
            0.0,
        )

    def test_selected_fund_and_edit_do_not_double_count(self):
        project = SimpleNamespace(
            id=7, new_total_costos_produccion_N_r=100.0,
            suma_costos_fuv=50.0, contratos_vco_resumen=80.0,
            poliza_total=0.0,
        )
        prod = SimpleNamespace(id=1, proyecto_relacionado=project,
                               select_fondos='es_produccion', valor_total_contrato=30.0)
        costs = SimpleNamespace(id=2, proyecto_relacionado=project,
                                select_fondos='es_personal_fuv', valor_total_contrato=10.0)
        saved = SimpleNamespace(id=3, proyecto_relacionado=project,
                                select_fondos='contratos_vco', valor_total_contrato=20.0)
        current = SimpleNamespace(
            id=3, _origin=SimpleNamespace(id=3), proyecto_relacionado=project,
            select_fondos='contratos_vco', valor_total_contrato=20.0,
            currency_id=SimpleNamespace(rounding=0.01),
        )
        context = _SgrContext(current, (prod, costs, saved))

        ContratacionesSgrSaldosHeader._compute_saldos_sgr(context)
        self.assertEqual(
            (current.sgr_disponible_produccion, current.sgr_disponible_costos_fuv,
             current.sgr_disponible_vco, current.sgr_disponible_contratos),
            (70.0, 40.0, 60.0, 60.0),
        )

        current.select_fondos = 'es_produccion'
        ContratacionesSgrSaldosHeader._compute_saldos_sgr(context)
        self.assertEqual(
            (current.sgr_disponible_produccion, current.sgr_disponible_costos_fuv,
             current.sgr_disponible_vco, current.sgr_disponible_contratos),
            (50.0, 40.0, 80.0, 50.0),
        )

        current.select_fondos = 'contratos_vco'
        current.valor_total_contrato = 90.0
        ContratacionesSgrSaldosHeader._compute_saldos_sgr(context)
        with self.assertRaises(ValidationError):
            ContratacionesSgrSaldosHeader._check_saldo_sgr_suficiente(context)

        current.select_fondos = False
        with self.assertRaises(ValidationError):
            ContratacionesSgrSaldosHeader._check_saldo_sgr_suficiente(context)

        new_contract = SimpleNamespace(
            id=None, _origin=None, proyecto_relacionado=project,
            select_fondos='es_personal_fuv', valor_total_contrato=5.0,
        )
        new_context = _SgrContext(new_contract, (prod, costs, saved))
        ContratacionesSgrSaldosHeader._compute_saldos_sgr(new_context)
        self.assertEqual(new_contract.sgr_disponible_costos_fuv, 35.0)

    def test_existing_fuv_expenses_follow_their_funds(self):
        project = SimpleNamespace(
            id=8, new_total_costos_produccion_N_r=200.0,
            suma_costos_fuv=100.0, contratos_vco_resumen=100.0,
            poliza_total=20.0,
        )
        sources = {
            'contrataciones_fuv': [SimpleNamespace(
                proyecto_relacionado=project, select_fondos='es_produccion',
                valor_total_contrato=30.0,
            )],
            'otro_si': [
                SimpleNamespace(
                    contratacion_fuv_en_id=SimpleNamespace(proyecto_relacionado=project),
                    select_fondos='es_personal_fuv', valor_total_contrato=20.0,
                ),
                SimpleNamespace(
                    contratacion_fuv_en_id=SimpleNamespace(proyecto_relacionado=project),
                    select_fondos='es_vco', valor_total_contrato=7.0,
                ),
            ],
            'orden_compra_bienes': [SimpleNamespace(
                proyecto_relacionado_c=project, total=10.0,
            )],
            'orden_compra_servicios': [
                SimpleNamespace(proyecto_relacionado_s=project,
                                select_fondos='es_produccion', subtotal=5.0,
                                iva=0.0, gastos_expedicion=0.0),
                SimpleNamespace(proyecto_relacionado_s=project,
                                select_fondos='es_poliza', subtotal=2.0,
                                iva=0.0, gastos_expedicion=0.0),
            ],
            'ordenes_especiales': [SimpleNamespace(
                proyecto_relacionado_s=project, select_fondos='es_produccion',
                subtotal=4.0, iva=0.0, gastos_expedicion=0.0,
            )],
            'anticipos': [SimpleNamespace(
                proyecto_id=project, state='completado', valor_anticipo=6.0,
            )],
            'viaticos': [SimpleNamespace(
                proyecto_relacionado_viaticos=project, state_viaticos='completado',
                impacto_negativo_fuv=3.0, impacto_positivo_fuv=1.0,
            )],
            'contrataciones_fuv.cierre.proceso': [SimpleNamespace(
                proyecto_id=project, estado='finalizado', valor_liberado_rfc=5.0,
                fondo_origen='es_produccion', contract_id=None,
            )],
        }
        current = SimpleNamespace(
            id=1, _origin=None, proyecto_relacionado=project,
            select_fondos='contratos_vco', valor_total_contrato=15.0,
        )
        context = _SgrContext(current, sources=sources)

        ContratacionesSgrSaldosHeader._compute_saldos_sgr(context)
        self.assertEqual(
            (current.sgr_disponible_produccion, current.sgr_disponible_costos_fuv,
             current.sgr_disponible_vco),
            (130.0, 80.0, 78.0),
        )
        self.assertEqual(
            ContratacionesSgrSaldosHeader._sgr_resumen_proyecto(context, project)[
                'produccion_utilizada'
            ],
            70.0,
        )

    def test_sgr_otrosi_adicion_descuenta_el_fondo_del_contrato(self):
        project = SimpleNamespace(
            id=9, new_total_costos_produccion_N_r=100.0,
            suma_costos_fuv=50.0, contratos_vco_resumen=80.0,
            poliza_total=0.0,
        )
        contrato = SimpleNamespace(
            id=3, proyecto_relacionado=project,
            select_fondos='es_produccion', valor_total_contrato=30.0,
        )
        current = SimpleNamespace(
            id=3, _origin=SimpleNamespace(id=3), proyecto_relacionado=project,
            select_fondos='es_produccion', valor_total_contrato=30.0,
        )
        context = _SgrContext(current, (contrato,), {
            'otro_si_sgr': [SimpleNamespace(contratacion_id=contrato,
                                            select_fondos='es_produccion',
                                            valor_adicion=10.0)],
        })

        ContratacionesSgrSaldosHeader._compute_saldos_sgr(context)

        self.assertEqual(current.sgr_disponible_produccion, 60.0)

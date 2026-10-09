from types import SimpleNamespace

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from ..models.solicitud_polizas.contrataciones_sgr_extension import (
    ContratacionesSgrSolicitudPoliza,
)
from ..models.solicitud_polizas.solicitud_poliza import SolicitudPoliza


class _Solicitudes(list):
    def filtered(self, predicate):
        return _Solicitudes(item for item in self if predicate(item))


class _ContratoSgrStub:
    def __init__(self, solicitudes=(), estado='coordinador_1'):
        self.id = 42
        self._origin = SimpleNamespace(id=self.id)
        self.estado_contratacion = estado
        self.solicitud_poliza_ids = _Solicitudes(solicitudes)
        self.env = SimpleNamespace(
            ref=lambda _xml_id: SimpleNamespace(id=7),
            user=SimpleNamespace(has_group=lambda _group: True),
        )

    def ensure_one(self):
        return self

    def _usuario_puede_solicitar_poliza(self):
        return True


class TestSgrSolicitudPolizas(TransactionCase):
    def test_kanban_cuenta_solo_polizas_con_documento(self):
        vacio = SimpleNamespace(solicitud_poliza_ids=[], sgr_polizas_cargadas=None)
        contrato = SimpleNamespace(
            solicitud_poliza_ids=[
                SimpleNamespace(linea_ids=[
                    SimpleNamespace(documento_id=SimpleNamespace(id=1)),
                    SimpleNamespace(documento_id=False),
                ]),
                SimpleNamespace(linea_ids=[
                    SimpleNamespace(documento_id=SimpleNamespace(id=2)),
                ]),
            ],
            sgr_polizas_cargadas=None,
        )

        ContratacionesSgrSolicitudPoliza._compute_sgr_polizas_cargadas([vacio, contrato])

        self.assertEqual(vacio.sgr_polizas_cargadas, 0)
        self.assertEqual(contrato.sgr_polizas_cargadas, 2)

    def test_solicitud_usa_contrato_sgr_sin_mezclarlo_con_fuv(self):
        fuv = SimpleNamespace(id=1)
        sgr = SimpleNamespace(id=2)
        solicitud = SimpleNamespace(
            ensure_one=lambda: None,
            contract_id=fuv,
            sgr_contract_id=sgr,
        )
        self.assertIs(SolicitudPoliza._contrato_poliza(solicitud), sgr)
        solicitud.sgr_contract_id = False
        self.assertIs(SolicitudPoliza._contrato_poliza(solicitud), fuv)

    def test_solicitud_no_puede_tener_ambos_contratos(self):
        solicitud = SimpleNamespace(
            contract_id=SimpleNamespace(id=1),
            sgr_contract_id=SimpleNamespace(id=2),
        )

        with self.assertRaisesRegex(UserError, 'un solo contrato'):
            SolicitudPoliza._validar_origen_unico([solicitud])

    def test_boton_sgr_usa_su_propio_contrato(self):
        contrato = _ContratoSgrStub()

        accion = ContratacionesSgrSolicitudPoliza.action_solicitar_poliza(contrato)

        self.assertEqual(accion['context'], {'default_sgr_contract_id': 42})
        self.assertEqual(accion['res_model'], 'contrataciones_fuv.solicitar.poliza.wizard')

    def test_no_duplica_solicitud_inicial(self):
        contrato = _ContratoSgrStub([
            SimpleNamespace(origen='contrato'),
        ])

        with self.assertRaisesRegex(UserError, 'ya tiene una solicitud'):
            ContratacionesSgrSolicitudPoliza.action_solicitar_poliza(contrato)

    def test_no_solicita_despues_de_contratado(self):
        contrato = _ContratoSgrStub(estado='contratado')

        with self.assertRaisesRegex(UserError, 'ya está contratado'):
            ContratacionesSgrSolicitudPoliza.action_solicitar_poliza(contrato)

    def test_soporte_puede_abrir_ciclo_extraordinario(self):
        contrato = _ContratoSgrStub(estado='contratado')

        accion = ContratacionesSgrSolicitudPoliza.action_solicitar_poliza_soporte(contrato)

        self.assertEqual(accion['context'], {
            'default_sgr_contract_id': 42,
            'default_modo_soporte': True,
        })

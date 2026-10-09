from datetime import date
from types import SimpleNamespace

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.requisitos import ContratacionesSgrRequisitos


class TestSgrFechas(TransactionCase):
    def test_acta_actualiza_inicio_del_contrato(self):
        contrato = SimpleNamespace(
            fecha_acta_inicio_sgr=date(2026, 10, 10),
            fecha_iniciacion=False,
        )

        ContratacionesSgrRequisitos._onchange_fecha_acta_inicio_sgr([contrato])

        self.assertEqual(contrato.fecha_iniciacion, date(2026, 10, 10))

    def test_mismo_dia_cuenta_un_dia(self):
        contrato = SimpleNamespace(
            fecha_iniciacion=date(2026, 10, 10),
            fecha_terminacion=date(2026, 10, 10),
            duracion=False,
        )
        ContratacionesSgrRequisitos._compute_duracion_sgr([contrato])
        self.assertEqual(contrato.duracion, '1 día')

    def test_mes_completo_incluye_fecha_final(self):
        contrato = SimpleNamespace(
            fecha_iniciacion=date(2026, 10, 10),
            fecha_terminacion=date(2026, 11, 9),
            duracion=False,
        )
        ContratacionesSgrRequisitos._compute_duracion_sgr([contrato])
        self.assertEqual(contrato.duracion, '1 mes')

    def test_fechas_incompletas_no_muestran_duracion(self):
        contrato = SimpleNamespace(
            fecha_iniciacion=date(2026, 10, 10),
            fecha_terminacion=False,
            duracion='Anterior',
        )
        ContratacionesSgrRequisitos._compute_duracion_sgr([contrato])
        self.assertFalse(contrato.duracion)

    def test_rechaza_fecha_final_anterior(self):
        contrato = SimpleNamespace(
            fecha_iniciacion=date(2026, 10, 10),
            fecha_terminacion=date(2026, 10, 9),
        )
        with self.assertRaises(ValidationError):
            ContratacionesSgrRequisitos._check_fechas_contrato_sgr([contrato])

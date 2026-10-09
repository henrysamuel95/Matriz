from types import SimpleNamespace

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.otro_si import OtroSiSgr


class TestSgrOtrosi(TransactionCase):
    def test_fechas_invertidas_se_rechazan(self):
        otrosi = SimpleNamespace(
            fecha_inicio='2026-10-10', fecha_fin='2026-10-09',
            valor_adicion=0, tipo='prorroga',
        )

        with self.assertRaises(ValidationError):
            OtroSiSgr._check_datos_otrosi_sgr([otrosi])

    def test_prorroga_no_admite_valor_de_adicion(self):
        otrosi = SimpleNamespace(
            fecha_inicio=False, fecha_fin=False,
            valor_adicion=100, tipo='prorroga',
        )

        with self.assertRaises(ValidationError):
            OtroSiSgr._check_datos_otrosi_sgr([otrosi])

    def test_adicion_con_valor_es_valida(self):
        otrosi = SimpleNamespace(
            fecha_inicio=False, fecha_fin=False,
            valor_adicion=100, tipo='adicion',
        )

        OtroSiSgr._check_datos_otrosi_sgr([otrosi])

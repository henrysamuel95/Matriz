from types import SimpleNamespace

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.campos_fuv import ContratacionesSgrCamposFuv


class TestSgrIva(TransactionCase):
    def test_responsable_desmarca_no_responsable(self):
        contrato = SimpleNamespace(r_iva=True, no_r_iva=True)

        ContratacionesSgrCamposFuv._onchange_r_iva_sgr(contrato)

        self.assertTrue(contrato.r_iva)
        self.assertFalse(contrato.no_r_iva)

    def test_no_responsable_desmarca_responsable(self):
        contrato = SimpleNamespace(r_iva=True, no_r_iva=True)

        ContratacionesSgrCamposFuv._onchange_no_r_iva_sgr(contrato)

        self.assertFalse(contrato.r_iva)
        self.assertTrue(contrato.no_r_iva)

    def test_no_permite_ambas_opciones(self):
        contrato = SimpleNamespace(r_iva=True, no_r_iva=True)

        with self.assertRaises(ValidationError):
            ContratacionesSgrCamposFuv._check_responsabilidad_iva_sgr([contrato])

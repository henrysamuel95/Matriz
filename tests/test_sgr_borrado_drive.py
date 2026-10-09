from types import SimpleNamespace
from unittest.mock import Mock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.contrataciones_sgr import ContratacionesSgr, GoogleDriveAuth


class TestSgrBorradoDrive(TransactionCase):
    @patch.object(GoogleDriveAuth, 'get_instance')
    def test_mueve_la_carpeta_exacta_a_papelera(self, get_instance):
        carpeta = Mock()
        drive = Mock()
        drive.CreateFile.return_value = carpeta
        get_instance.return_value.get_drive.return_value = drive

        ContratacionesSgr._mover_carpeta_drive_a_papelera(SimpleNamespace(), 'id-sgr')

        drive.CreateFile.assert_called_once_with({'id': 'id-sgr'})
        carpeta.Trash.assert_called_once_with()

    @patch.object(GoogleDriveAuth, 'get_instance')
    def test_error_de_drive_impide_confirmar_borrado(self, get_instance):
        get_instance.return_value.get_drive.return_value = None

        with self.assertRaisesRegex(UserError, 'No se eliminó la contratación'):
            ContratacionesSgr._mover_carpeta_drive_a_papelera(SimpleNamespace(), 'id-sgr')

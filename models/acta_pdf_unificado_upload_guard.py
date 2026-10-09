# -*- coding: utf-8 -*-
import threading

from odoo import models, _
from odoo.exceptions import UserError

from .drive import GoogleDriveAuth


class _DriveFileUploadGuard:
    """Proxy de un archivo PyDrive que limita únicamente la espera de Upload()."""

    def __init__(self, drive_file, timeout=120):
        object.__setattr__(self, '_drive_file', drive_file)
        object.__setattr__(self, '_timeout', timeout)

    def __getattr__(self, name):
        return getattr(self._drive_file, name)

    def __getitem__(self, key):
        return self._drive_file[key]

    def __setitem__(self, key, value):
        self._drive_file[key] = value

    def Upload(self, *args, **kwargs):
        resultado = {}
        terminado = threading.Event()

        def _subir():
            try:
                resultado['valor'] = self._drive_file.Upload(*args, **kwargs)
            except BaseException as error:
                resultado['error'] = error
            finally:
                terminado.set()

        hilo = threading.Thread(
            target=_subir,
            name='PDFUnificadoDriveUpload',
            daemon=True,
        )
        hilo.start()

        if not terminado.wait(self._timeout):
            raise UserError(_(
                'Google Drive no respondió al guardar el PDF unificado en %s segundos. '
                'La generación se detuvo para no dejar la cuenta bloqueada en 96%%. '
                'Puede intentar generar nuevamente.'
            ) % self._timeout)

        if 'error' in resultado:
            raise resultado['error']
        return resultado.get('valor')


class ActaEntregaDocumentosPdfUploadGuard(models.Model):
    _inherit = 'acta_entrega_documentos'

    def _generar_pdf_unificado(self):
        """Mantiene la lógica existente y protege solo la subida final a Drive.

        El 96%% se establece inmediatamente antes de ``archivo_drive.Upload()``.
        Si PyDrive deja esa llamada esperando indefinidamente, el proceso nunca
        alcanza el 100%%. Este wrapper no cambia autenticación, documentos ni
        transacciones: únicamente limita cuánto puede esperar Upload().
        """
        self.ensure_one()
        drive = GoogleDriveAuth.get_instance().get_drive()
        create_file_original = drive.CreateFile

        def _create_file_protegido(metadata=None):
            archivo = create_file_original(metadata)
            return _DriveFileUploadGuard(archivo, timeout=120)

        drive.CreateFile = _create_file_protegido
        try:
            return super()._generar_pdf_unificado()
        finally:
            drive.CreateFile = create_file_original

# -*- coding: utf-8 -*-
import logging
import ssl
import time

import requests
from odoo import models

_logger = logging.getLogger(__name__)


class ActaEntregaDocumentosPdfReintentos(models.Model):
    _inherit = 'acta_entrega_documentos'

    def _pdf_unificado_es_error_transitorio(self, error):
        """Identifica fallos de red/SSL que sí vale la pena reintentar."""
        texto = str(error or '').lower()
        indicadores = (
            'eof occurred in violation of protocol',
            'ssl',
            'connection reset',
            'connection aborted',
            'remote end closed connection',
            'temporarily unavailable',
            'timed out',
            'timeout',
            'broken pipe',
            'server disconnected',
            '502',
            '503',
            '504',
        )
        return (
            isinstance(error, (ssl.SSLError, requests.exceptions.ConnectionError,
                               requests.exceptions.Timeout))
            or any(indicador in texto for indicador in indicadores)
        )

    def _drive_documento_como_pdf(self, drive_record, etiqueta=''):
        """Aísla cada intento en un savepoint y reintenta fallos transitorios.

        Si una operación ORM/SQL falla mientras se descarga o convierte un
        documento, PostgreSQL marca la transacción como abortada. El savepoint
        hace rollback solo de ese intento antes de capturar la excepción, por lo
        que el resto del PDF puede seguir trabajando con una transacción válida
        y se conserva el error original en lugar de terminar mostrando
        ``current transaction is aborted``.
        """
        self.ensure_one()
        ultimo_error = None
        # Un reintento con una conexión completamente nueva es suficiente. Si
        # Drive continúa sin responder, se informa el error y se libera el
        # proceso en vez de retener la cuenta durante varios minutos.
        max_intentos = 2

        for intento in range(1, max_intentos + 1):
            try:
                # IMPORTANTE: la excepción debe salir del bloque ``with`` para
                # que Odoo haga ROLLBACK TO SAVEPOINT antes de entrar al except.
                with self.env.cr.savepoint():
                    return super()._drive_documento_como_pdf(
                        drive_record,
                        etiqueta=etiqueta,
                    )
            except Exception as error:
                ultimo_error = error

                # A esta altura el cursor vuelve a estar utilizable. Evitar leer
                # campos no imprescindibles del recordset dentro del manejador.
                if not self._pdf_unificado_es_error_transitorio(error):
                    raise
                if intento >= max_intentos:
                    raise

                espera = min(2 ** (intento - 1), 5)
                _logger.warning(
                    'PDF unificado: fallo transitorio descargando/convirtiendo %s '
                    '(intento %s/%s): %s. Reintentando en %ss.',
                    etiqueta or 'documento',
                    intento,
                    max_intentos,
                    error,
                    espera,
                )
                time.sleep(espera)

        if ultimo_error:
            raise ultimo_error

        # Salvaguarda; normalmente no se alcanza.
        with self.env.cr.savepoint():
            return super()._drive_documento_como_pdf(
                drive_record,
                etiqueta=etiqueta,
            )

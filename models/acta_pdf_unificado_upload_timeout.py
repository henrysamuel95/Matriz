# -*- coding: utf-8 -*-
import httplib2

from odoo import models

from .drive import GoogleDriveAuth


class ActaEntregaDocumentosPdfUploadTimeout(models.Model):
    _inherit = 'acta_entrega_documentos'

    def _generar_pdf_unificado(self):
        """Evita que PyDrive quede colgado indefinidamente al subir el PDF final.

        La generación llega al 96%% justo antes de archivo_drive.Upload(). PyDrive2
        usa el objeto HTTP de GoogleAuth y, sin timeout, una conexión cortada puede
        dejar el hilo esperando mucho tiempo. Durante esta generación sustituimos
        temporalmente ese HTTP por uno con timeout de 90 segundos.

        Si Google Drive no responde dentro de ese tiempo, se lanza una excepción;
        el worker existente la registra como error y la cuenta deja de quedar
        eternamente en "Procesando 96%%". El resto del flujo permanece intacto.
        """
        self.ensure_one()
        drive_auth = GoogleDriveAuth.get_instance()
        gauth = drive_auth.get_gauth()
        original_get_http = getattr(gauth, 'Get_Http_Object', None)

        if not original_get_http:
            return super()._generar_pdf_unificado()

        def _get_http_con_timeout():
            credentials = gauth.credentials
            if (
                hasattr(credentials, 'access_token_expired')
                and credentials.access_token_expired
            ):
                gauth.Refresh()
                credentials = gauth.credentials
            return credentials.authorize(httplib2.Http(timeout=90))

        gauth.Get_Http_Object = _get_http_con_timeout
        try:
            return super()._generar_pdf_unificado()
        finally:
            gauth.Get_Http_Object = original_get_http

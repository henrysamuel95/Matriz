"""Contrato HTTP del adaptador, sin descargas ni envíos reales."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from odoo.http import Response
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..controllers.FirmaDocumentoController import FirmaDocumentoController


@tagged('post_install', '-at_install')
class TestFirmaDocumentoController(TransactionCase):

    def setUp(self):
        super().setUp()
        self.controller = FirmaDocumentoController()
        self.http_request = MagicMock()
        self.http_request.render.return_value = Response('rendered')
        self.http_request.not_found.return_value = Response(status=404)
        self.http_request.make_response.return_value = Response(b'%PDF-1.7')
        self.service = MagicMock()
        self.http_request.env.__getitem__.return_value = self.service
        self.solicitud = SimpleNamespace(id=17, estado='pendiente')
        self.service.validar_acceso_publico.return_value = self.solicitud
        patcher = patch(
            'odoo.addons.Matriz.controllers.FirmaDocumentoController.request',
            self.http_request,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, **changes):
        values = {
            'solicitud_id': '17', 'token': 'credencial-prueba',
            'consentimiento': 'on', 'firma_base64': 'data:image/png;base64,aW1hZ2Vu',
        }
        values.update(changes)
        return self.controller.firma_documento_submit(**values)

    def test_form_invalid_and_terminal_templates(self):
        self.service.validar_acceso_publico.return_value = False
        self.controller.firma_documento_form(17, 'invalido')
        self.http_request.render.assert_called_with('Matriz.firma_documento_invalida')
        for estado in ('firmado', 'cancelado', 'expirado'):
            self.solicitud.estado = estado
            self.service.validar_acceso_publico.return_value = self.solicitud
            self.controller.firma_documento_form(17, 'credencial-prueba')
            self.http_request.render.assert_called_with(
                'Matriz.firma_documento_no_disponible', {'solicitud': self.solicitud},
            )
        self.service.validar_acceso_publico.assert_called_with(
            17, 'credencial-prueba', exigir_pendiente=False,
        )

    def test_preview_denied_does_not_download(self):
        # El servicio devuelve vacío para token inválido o solicitud terminal.
        self.service.validar_acceso_publico.return_value = False
        self.controller.firma_documento_pdf(17, 'credencial-prueba')
        self.http_request.not_found.assert_called_once_with()
        self.service.obtener_pdf_preview.assert_not_called()
        self.service.validar_acceso_publico.assert_called_once_with(17, 'credencial-prueba')

    def test_preview_error_is_not_exposed(self):
        self.service.obtener_pdf_preview.side_effect = RuntimeError('secreto-remoto')
        self.controller.firma_documento_pdf(17, 'credencial-prueba')
        self.http_request.not_found.assert_called_once_with()
        self.http_request.make_response.assert_not_called()

    def test_preview_returns_pdf_without_cache(self):
        self.service.obtener_pdf_preview.return_value = b'%PDF-1.7'
        self.controller.firma_documento_pdf(17, 'credencial-prueba')
        args, kwargs = self.http_request.make_response.call_args
        self.assertEqual(args, (b'%PDF-1.7',))
        self.assertEqual(dict(kwargs['headers'])['Cache-Control'], 'no-store')
        self.assertEqual(dict(kwargs['headers'])['Content-Type'], 'application/pdf')

    def test_submit_malformed_id_does_not_validate_access(self):
        for value in (None, 'abc', '9' * 100, '١٧'):
            self._post(solicitud_id=value)
        self.service.validar_acceso_publico.assert_not_called()
        self.service.procesar_firma.assert_not_called()

    def test_submit_terminal_does_not_process(self):
        for estado in ('firmado', 'cancelado', 'expirado'):
            self.solicitud.estado = estado
            self._post()
            self.http_request.render.assert_called_with(
                'Matriz.firma_documento_no_disponible', {'solicitud': self.solicitud},
            )
        self.service.procesar_firma.assert_not_called()

    def test_submit_requires_consent_and_image(self):
        self._post(consentimiento=None)
        self._post(firma_base64=None)
        self.service.procesar_firma.assert_not_called()
        self.assertEqual(self.http_request.render.call_args.args[0], 'Matriz.firma_documento_error')

    def test_submit_delegates_with_lock_and_consent(self):
        self._post()
        self.service.validar_acceso_publico.assert_called_once_with(
            17, 'credencial-prueba', bloquear=True, exigir_pendiente=False,
        )
        self.service.procesar_firma.assert_called_once_with(self.solicitud, 'aW1hZ2Vu', True)
        self.http_request.render.assert_called_once_with(
            'Matriz.firma_documento_exitosa', {'solicitud': self.solicitud},
        )

    def test_submit_error_uses_savepoint_and_generic_message(self):
        self.service.procesar_firma.side_effect = RuntimeError('secreto-remoto')
        self._post()
        self.http_request.env.cr.savepoint.assert_called_once_with()
        args = self.http_request.render.call_args.args
        self.assertEqual(args[0], 'Matriz.firma_documento_error')
        self.assertNotIn('secreto-remoto', args[1]['error_message'])

    def test_routes_keep_urls_and_http_methods(self):
        expected = (
            ('firma_documento_form', '/public/firma_documento/<int:solicitud_id>/<string:token>', ['GET']),
            ('firma_documento_pdf', '/public/firma_documento/<int:solicitud_id>/<string:token>/documento.pdf', ['GET']),
            ('firma_documento_submit', '/public/firma_documento/submit', ['POST']),
        )
        for name, url, methods in expected:
            routing = getattr(self.controller, name).original_routing
            self.assertIn(url, routing['routes'])
            self.assertEqual(routing['methods'], methods)
            self.assertEqual(routing['auth'], 'public')

    def test_errores_transaccionales_se_propagan_para_retry_de_odoo(self):
        from psycopg2.errors import DeadlockDetected, SerializationFailure

        for exception in (SerializationFailure, DeadlockDetected):
            with self.subTest(exception=exception):
                self.service.obtener_pdf_preview.side_effect = exception('retry')
                with self.assertRaises(exception):
                    self.controller.firma_documento_pdf(17, 'credencial-prueba')
                self.service.procesar_firma.side_effect = exception('retry')
                with self.assertRaises(exception):
                    self._post()
        self.http_request.not_found.assert_not_called()
        self.http_request.render.assert_not_called()

import logging

from psycopg2.errors import DeadlockDetected, SerializationFailure

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class FirmaDocumentoController(http.Controller):

    @http.route('/public/firma_documento/<int:solicitud_id>/<string:token>',
                type='http', auth='public', methods=['GET'], website=True, csrf=False)
    def firma_documento_form(self, solicitud_id, token, **kwargs):
        solicitud = request.env['drive.firma.service'].validar_acceso_publico(
            solicitud_id, token, exigir_pendiente=False,
        )
        if not solicitud:
            return request.render('Matriz.firma_documento_invalida')
        if solicitud.estado != 'pendiente':
            return request.render('Matriz.firma_documento_no_disponible', {'solicitud': solicitud})
        return request.render('Matriz.firma_documento_form', {'solicitud': solicitud})

    @http.route('/public/firma_documento/<int:solicitud_id>/<string:token>/documento.pdf',
                type='http', auth='public', methods=['GET'], csrf=False)
    def firma_documento_pdf(self, solicitud_id, token, **kwargs):
        """Construye la respuesta del visor tras validar el acceso en el servicio."""
        servicio = request.env['drive.firma.service']
        solicitud = servicio.validar_acceso_publico(solicitud_id, token)
        if not solicitud:
            return request.not_found()
        try:
            pdf_bytes = servicio.obtener_pdf_preview(solicitud)
        except (SerializationFailure, DeadlockDetected):
            raise
        except Exception:
            _logger.warning('No se pudo obtener el preview de firma (solicitud %s)', solicitud_id)
            return request.not_found()
        return request.make_response(pdf_bytes, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', str(len(pdf_bytes))),
            ('Content-Disposition', 'inline; filename="documento.pdf"'),
            ('Cache-Control', 'no-store'),
            ('X-Content-Type-Options', 'nosniff'),
        ])

    @http.route('/public/firma_documento/submit', type='http', auth='public',
                methods=['POST'], website=True, csrf=False)
    def firma_documento_submit(self, **post):
        solicitud_id = post.get('solicitud_id')
        token = post.get('token')
        if (not isinstance(solicitud_id, str) or not solicitud_id.isascii()
                or not solicitud_id.isdigit() or len(solicitud_id) > 10 or not token):
            return request.render('Matriz.firma_documento_invalida')

        servicio = request.env['drive.firma.service']
        solicitud = servicio.validar_acceso_publico(
            int(solicitud_id), token, bloquear=True, exigir_pendiente=False,
        )
        if not solicitud:
            return request.render('Matriz.firma_documento_invalida')
        if solicitud.estado != 'pendiente':
            return request.render('Matriz.firma_documento_no_disponible', {'solicitud': solicitud})

        consentimiento = post.get('consentimiento') == 'on'
        if not consentimiento:
            return request.render('Matriz.firma_documento_error', {
                'solicitud': solicitud,
                'error_message': 'Debes aceptar el consentimiento antes de firmar.',
            })

        firma_b64 = post.get('firma_base64')
        if not isinstance(firma_b64, str) or ',' not in firma_b64:
            return request.render('Matriz.firma_documento_error', {
                'solicitud': solicitud,
                'error_message': 'No se recibió una firma dibujada.',
            })

        try:
            # El error se transforma en HTML: revertir las escrituras parciales
            # antes de dejar que Odoo confirme el request de esa respuesta.
            with request.env.cr.savepoint():
                servicio.procesar_firma(solicitud, firma_b64.split(',', 1)[1], consentimiento)
        except (SerializationFailure, DeadlockDetected):
            raise
        except Exception:
            _logger.warning('Error procesando firma de solicitud %s', solicitud.id)
            return request.render('Matriz.firma_documento_error', {
                'solicitud': solicitud,
                'error_message': 'Ocurrió un error al procesar la firma. Intenta nuevamente.',
            })

        return request.render('Matriz.firma_documento_exitosa', {'solicitud': solicitud})

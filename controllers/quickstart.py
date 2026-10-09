import logging
import secrets
import time
from html import escape

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


class GoogleDriveOAuthController(http.Controller):
    def _result(self, message, record_id=None, status=200):
        url = '/web'
        if record_id:
            url += '#id=%s&model=matriz.quickstart&view_type=form' % int(record_id)
        response = request.make_response(
            '<!doctype html><html lang="es"><meta charset="utf-8">'
            '<title>Autorización Google Drive</title>'
            '<body><h1>Autorización Google Drive</h1><p>%s</p>'
            '<a href="%s">Volver a Odoo</a></body></html>' % (escape(message), escape(url, quote=True)),
            headers=[('Content-Type', 'text/html; charset=utf-8'),
                     ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'),
                     ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")],
        )
        response.status_code = status
        return response

    @http.route('/matriz/google_drive/oauth/callback', type='http', auth='user', methods=['GET'])
    def google_drive_callback(self, state=None, code=None, error=None, **kwargs):
        pending = request.session.get('matriz_drive_oauth') or {}
        if (not state or not pending.get('state')
                or not secrets.compare_digest(state.encode('utf-8'), pending['state'].encode('utf-8'))
                or pending.get('uid') != request.env.uid
                or pending.get('db') != request.env.cr.dbname
                or not 0 <= time.time() - pending.get('created_at', 0) <= 600):
            return self._result('La autorización no es válida o expiró. Iníciela de nuevo desde Odoo.', status=400)
        # Un solo uso, incluso si Google rechazó la autorización.
        request.session.pop('matriz_drive_oauth', None)
        record = request.env['matriz.quickstart'].browse(pending['record_id']).exists()
        if not record:
            return self._result('La configuración ya no existe.', status=404)
        try:
            record._check_configuration_access()
            if not secrets.compare_digest(record._oauth_config_fingerprint(), pending['config']):
                return self._result('La configuración cambió durante la autorización. Iníciela de nuevo.', record.id, 400)
            if error or not code:
                return self._result('Google no autorizó el acceso. Vuelva a intentarlo y acepte los permisos.', record.id, 400)
            # Intercambiar y descartar el código sin copiarlo ni guardarlo en BD.
            record._complete_google_authorization(code)
        except AccessError:
            return self._result('No tiene permisos para modificar esta configuración.', status=403)
        except UserError as exc:
            return self._result(str(exc), record.id, 400)
        except Exception as exc:
            _logger.warning('Falló el retorno OAuth de Drive (%s).', type(exc).__name__)
            return self._result('No se pudo guardar la autorización. Revise la configuración y vuelva a autenticar.', record.id, 400)
        return self._result('Autorización completada. Las credenciales se guardaron en config automáticamente.', record.id)

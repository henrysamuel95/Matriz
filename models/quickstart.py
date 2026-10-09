import hashlib
import json
import logging
import os
import secrets
import tempfile
import time
from urllib.parse import urlsplit

import yaml
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.http import request
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

_logger = logging.getLogger(__name__)
_SECRET_GROUPS = 'base.group_system,Matriz.group_soporte,Matriz.group_contratacion_directivo'
_CALLBACK_PATH = '/matriz/google_drive/oauth/callback'


class QuickStart(models.Model):
    _name = 'matriz.quickstart'
    _description = 'Google Drive API'

    name = fields.Char(string='Nombre')
    client_id = fields.Char(string='Client ID')
    client_secret = fields.Char(string='Client Secret', copy=False, groups=_SECRET_GROUPS)
    redirect_uri = fields.Char(
        string='Redirect URI', default=lambda self: self._default_redirect_uri(),
        help='Registre esta URL exacta como URI de redirección autorizada del cliente web en Google Cloud.',
    )
    auth_code = fields.Char(
        string='Código de Autorización', copy=False, groups=_SECRET_GROUPS,
        help='Google lo envía automáticamente al regresar. Se consume y se borra al guardar las credenciales.',
    )

    @api.model
    def _config_paths(self):
        # Misma carpeta que GoogleDriveAuth, independiente del directorio de inicio.
        directory = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')
        return {name: os.path.join(directory, filename) for name, filename in (
            ('client', 'client_secrets.json'),
            ('settings', 'settings.yaml'),
            ('credentials', 'credentials_module.json'),
        )}

    @api.model
    def _default_redirect_uri(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        return base_url.rstrip('/') + _CALLBACK_PATH

    @api.model
    def _read_client_config(self):
        path = self._config_paths()['client']
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding='utf-8-sig') as source:
                data = json.load(source)
            config = data.get('web') or data.get('installed') or {}
            if not isinstance(config, dict):
                raise ValueError('Invalid client configuration')
            return config
        except (OSError, ValueError, AttributeError) as error:
            raise UserError('No se pudo leer config/client_secrets.json. Revise el formato y los permisos.') from error

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        # No revelar secretos del disco a usuarios que solo pueden consultar.
        if self.check_access_rights('write', raise_exception=False):
            config = self._read_client_config()
            for field_name in ('client_id', 'client_secret'):
                if field_name in field_names and not values.get(field_name):
                    values[field_name] = config.get(field_name)
            if 'name' in field_names and not values.get('name'):
                values['name'] = config.get('project_id') or 'Google Drive'
        return values

    def _check_configuration_access(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')

    def action_load_google_config(self):
        self._check_configuration_access()
        config = self._read_client_config()
        if not config.get('client_id') or not config.get('client_secret'):
            raise UserError('Faltan Client ID o Client Secret en config/client_secrets.json.')
        self.write({
            'name': self.name or config.get('project_id') or 'Google Drive',
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'redirect_uri': self._default_redirect_uri(),
            'auth_code': False,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _google_settings(self, save_credentials=False):
        self.ensure_one()
        if not self.client_id or not self.client_secret:
            raise UserError('Cargue la configuración desde config o ingrese Client ID y Client Secret.')
        uri = urlsplit(self.redirect_uri or '')
        if (uri.scheme not in ('http', 'https') or not uri.netloc
                or uri.path != _CALLBACK_PATH or uri.query or uri.fragment
                or uri.username or uri.password):
            raise UserError('Use la URL de retorno de Odoo: %s. Regístrela también en Google Cloud.'
                            % self._default_redirect_uri())
        return {
            'client_config_backend': 'settings',
            'client_config': {
                'client_id': self.client_id.strip(),
                'client_secret': self.client_secret.strip(),
                'redirect_uri': self.redirect_uri,
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
                'revoke_uri': 'https://oauth2.googleapis.com/revoke',
            },
            'save_credentials': save_credentials,
            'save_credentials_backend': 'file',
            'save_credentials_file': self._config_paths()['credentials'],
            'get_refresh_token': True,
            'oauth_scope': ['https://www.googleapis.com/auth/drive'],
        }

    def _oauth_config_fingerprint(self):
        payload = json.dumps([self.client_id, self.client_secret, self.redirect_uri])
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def authenticate_google_drive(self):
        self._check_configuration_access()
        if not self.client_id or not self.client_secret:
            self.action_load_google_config()
        # El formulario antiguo no iniciaba ningún servidor en localhost:8080.
        if not self.redirect_uri or self.redirect_uri in ('http://localhost:8080/', 'http://localhost:8080'):
            self.redirect_uri = self._default_redirect_uri()
        gauth = GoogleAuth(settings=self._google_settings(), http_timeout=30)
        gauth.GetFlow()
        state = secrets.token_urlsafe(32)
        gauth.flow.params.update({'state': state, 'prompt': 'consent'})
        gauth.flow.params.pop('approval_prompt', None)
        request.session['matriz_drive_oauth'] = {
            'state': state, 'record_id': self.id, 'uid': self.env.uid,
            'db': self.env.cr.dbname, 'created_at': time.time(),
            'config': self._oauth_config_fingerprint(),
        }
        self.auth_code = False
        return {'type': 'ir.actions.act_url', 'url': gauth.GetAuthUrl(), 'target': 'self'}

    @api.model
    def _write_config_file(self, path, content):
        """Reemplazo atómico: no dejar archivos truncados si falla la escritura."""
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.oauth-', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _complete_google_authorization(self, code):
        self._check_configuration_access()
        if not code:
            raise UserError('Google no devolvió un código. Inicie nuevamente la autenticación.')
        settings = self._google_settings()
        gauth = GoogleAuth(settings=settings, http_timeout=30)
        # No cargar tokens anteriores: intercambiar el código de este flujo.
        gauth.Auth(code)
        if not gauth.credentials or not gauth.credentials.refresh_token:
            raise UserError('Google no entregó un token de renovación. Vuelva a autenticar y conceda el permiso solicitado.')
        paths = self._config_paths()
        client = dict(self._read_client_config())
        client.update({
            'client_id': settings['client_config']['client_id'],
            'client_secret': settings['client_config']['client_secret'],
            'auth_uri': settings['client_config']['auth_uri'],
            'token_uri': settings['client_config']['token_uri'],
            'redirect_uris': [self.redirect_uri],
        })
        # Los archivos existentes no se cambian hasta que Google autorice.
        settings['save_credentials'] = True
        self._write_config_file(paths['client'], json.dumps({'web': client}, indent=4))
        self._write_config_file(paths['settings'], yaml.safe_dump(settings, allow_unicode=True))
        self._write_config_file(paths['credentials'], gauth.credentials.to_json())
        self.auth_code = False  # Código de un solo uso: no conservarlo en BD.
        from .drive import GoogleDriveAuth
        if GoogleDriveAuth._instance is not None:
            GoogleDriveAuth._instance._gauth = None
            GoogleDriveAuth._instance._drive = None
        return True

    def authenticate_callback(self):
        """Compatibilidad para quien introduzca el código manualmente."""
        self._check_configuration_access()
        if not self.auth_code:
            return self.show_warning('Autorización', 'Pulse Autenticar con Google Drive; el código se recibe automáticamente.')
        try:
            self._complete_google_authorization(self.auth_code.strip())
            return self.show_notification('Éxito', 'Autenticación completada. Credenciales guardadas en config.')
        except UserError:
            raise
        except Exception as error:
            # No registrar el texto de errores OAuth: puede incluir secretos.
            _logger.warning('Falló la autorización de Drive (%s).', type(error).__name__)
            return self.show_warning('Autorización', 'No se pudo completar la autorización. Revise la Redirect URI y vuelva a autenticar.')

    def test_google_drive(self):
        self._check_configuration_access()
        paths = self._config_paths()
        try:
            gauth = GoogleAuth(settings={
                'client_config_backend': 'file', 'client_config_file': paths['client'],
                'save_credentials': False,
                'oauth_scope': ['https://www.googleapis.com/auth/drive'],
            }, http_timeout=30)
            gauth.LoadCredentialsFile(paths['credentials'])
            if gauth.credentials is None or gauth.credentials.invalid:
                return self.show_warning('Error', 'No hay credenciales válidas. Autentique primero.')
            if gauth.access_token_expired:
                gauth.Refresh()
                gauth.SaveCredentialsFile(paths['credentials'])
            gauth.Authorize()
            files = GoogleDrive(gauth).ListFile({
                'q': "'root' in parents and trashed=false", 'maxResults': 5,
            }).GetList()
            names = [item['title'] for item in files[:5]]
            return self.show_notification('Conexión exitosa', 'Archivos: ' + (', '.join(names) or 'carpeta vacía'))
        except Exception as error:
            _logger.warning('Falló la prueba de Drive (%s).', type(error).__name__)
            return self.show_warning('Error', 'No se pudo conectar. Revise los archivos de config, los permisos y la autorización de Google.')

    def show_notification(self, title, message):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'sticky': False, 'type': 'success'},
        }

    def show_warning(self, title, message):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'sticky': True, 'type': 'warning'},
        }

# -- coding: utf-8 --
import logging
import os
import requests
from odoo import http, models
from odoo.http import request
from odoo.addons.web.controllers.main import Home

_logger = logging.getLogger(__name__)

_env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_env_path)
    _logger.info("Matriz: .env %s en %s", "encontrado" if os.path.isfile(_env_path) else "NO encontrado", _env_path)
except ImportError:
    _logger.warning(
        "Matriz: python-dotenv no está instalado; ODOO_DISABLE_RECAPTCHA solo se leerá "
        "de variables de entorno reales del proceso, no del archivo .env"
    )

_logger.info("Matriz: ODOO_DISABLE_RECAPTCHA=%r", os.environ.get('ODOO_DISABLE_RECAPTCHA'))

RECAPTCHA_SECRET_KEY = '6LdU1jQrAAAAAL-h-jOvcmA4H5WEGzWhIbA07-Y2'


def verify_recaptcha(recaptcha_response):
    """Verifica un token de reCAPTCHA v2 contra la API de Google.

    Devuelve (ok, error_message). En desarrollo local, ODOO_DISABLE_RECAPTCHA=1
    en el .env (gitignoreado) permite saltar esta verificación sin tocar
    código ni comentar imports.
    """
    if os.environ.get('ODOO_DISABLE_RECAPTCHA', '').lower() in ('1', 'true'):
        return True, None

    if not recaptcha_response:
        return False, "Debes completar el reCAPTCHA para continuar."

    try:
        r = requests.post('https://www.google.com/recaptcha/api/siteverify', data={
            'secret': RECAPTCHA_SECRET_KEY,
            'response': recaptcha_response,
        }, timeout=5)
        result = r.json()
    except Exception:
        return False, "Error de conexión con reCAPTCHA. Intenta de nuevo."

    if not result.get('success'):
        return False, "La verificación reCAPTCHA falló. Intenta de nuevo."

    return True, None


class AuthRecaptchaController(http.Controller):

    @http.route('/web/login', type='http', auth="public", methods=['POST'], website=True)
    def login(self, **post):

        # ==========================================
        # 1. VERIFICACIÓN DE MODO MANTENIMIENTO
        # ==========================================
        try:
            in_maintenance = request.env['ir.config_parameter'].sudo().get_param('matriz.maintenance_mode') == 'True'
        except Exception:
            in_maintenance = False

        if in_maintenance:
            login_user = post.get('login')
            user = request.env['res.users'].sudo().search([('login', '=', login_user)], limit=1)

            # Bloquea a todos excepto admins (IDs 1 y 2)
            if user and user.id not in [1, 2]:
                return request.render("auth_signup.login", {
                    'error': "El sistema está en mantenimiento. Intente más tarde.",
                    'providers': [],
                })

        # ==========================================
        # 2. VERIFICACIÓN DE RECAPTCHA
        # ==========================================
        ok, error = verify_recaptcha(post.get('g-recaptcha-response'))
        if not ok:
            return request.render("auth_signup.login", {
                'error': error,
                'providers': [],
            })

        # ==========================================
        # 3. TODO CORRECTO → LOGIN NORMAL DE ODOO
        # ==========================================
        return Home().web_login(**post)


# -------------------------------------------------------------
# INTERCEPTOR DE SESIÓN (EVITA QUE F5 SALTE EL BLOQUEO)
# -------------------------------------------------------------
class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        try:
            in_maintenance = self.env['ir.config_parameter'].sudo().get_param('matriz.maintenance_mode') == 'True'
            result['maintenance_mode'] = in_maintenance
            result['maintenance_message'] = "El sistema está en mantenimiento. Intente más tarde."
        except Exception:
            pass
        return result

"""Configura (o desactiva) automáticamente un ir.mail_server que apunta a
Mailpit — un servidor SMTP falso solo para desarrollo local (ver el
servicio 'mailpit' en docker-compose.yml) que nunca entrega correos de
verdad, solo los captura para verlos en http://localhost:8025.

Se activa SOLO si la variable de entorno MAILPIT_LOCAL_DEV está en
'1'/'true'. Es una variable NUEVA y dedicada a esto — a propósito no se
reutiliza ODOO_DISABLE_RECAPTCHA (controllers/main.py), para no mezclar
dos cosas distintas bajo un mismo flag. Como el .env es gitignoreado y
cada entorno tiene el suyo, esta variable nunca debería existir fuera del
.env local de cada desarrollador — así que este código es un no-op total
en cualquier otro ambiente (staging, producción), sin importar qué
contenga la base de datos que se restaure ahí.

_register_hook() corre automáticamente cada vez que arranca el servidor
(instalación, actualización, o simplemente `docker compose up`) — no hace
falta correr ningún script a mano. Reclonar el repo, copiar .env.example
a .env y poner MAILPIT_LOCAL_DEV=1 es todo lo que hace falta.

Diseño no-destructivo: a diferencia de un primer intento de esto (un
script manual que archivaba cualquier otro ir.mail_server activo), este
hook NUNCA toca otros servidores — solo administra su propio registro
"Mailpit (local dev)", dándole sequence=0 (máxima prioridad) mientras el
flag esté activo. El servidor SMTP real (ej. Gmail) puede seguir activo
sin problema; Mailpit simplemente gana la prioridad localmente. Apagar el
flag desactiva el registro de Mailpit y Odoo vuelve a usar el servidor
real automáticamente, sin pasos manuales.
"""

import logging
import os

from odoo import models

_logger = logging.getLogger(__name__)

_MAILPIT_SERVER_NAME = "Mailpit (local dev)"

_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_env_path)
except ImportError:
    _logger.warning(
        "Matriz: python-dotenv no está instalado; MAILPIT_LOCAL_DEV solo se "
        "leerá de variables de entorno reales del proceso, no del archivo .env"
    )


class IrMailServerMailpitDev(models.Model):
    _inherit = "ir.mail_server"

    def _register_hook(self):
        super()._register_hook()
        self.sudo()._mailpit_dev_sync()

    def _mailpit_dev_sync(self):
        habilitado = os.environ.get("MAILPIT_LOCAL_DEV", "").lower() in (
            "1",
            "true",
        )
        Server = self.sudo()
        mailpit = Server.with_context(active_test=False).search(
            [("name", "=", _MAILPIT_SERVER_NAME)], limit=1
        )

        if not habilitado:
            if mailpit and mailpit.active:
                _logger.info(
                    "MAILPIT_LOCAL_DEV apagado — desactivando '%s'.",
                    _MAILPIT_SERVER_NAME,
                )
                mailpit.write({"active": False})
            return

        vals = {
            "name": _MAILPIT_SERVER_NAME,
            "smtp_host": os.environ.get("MAILPIT_SMTP_HOST", "mailpit"),
            "smtp_port": int(os.environ.get("MAILPIT_SMTP_PORT", "1025")),
            "smtp_encryption": "none",
            "smtp_user": False,
            "smtp_pass": False,
            "sequence": 0,
            "active": True,
        }
        if mailpit:
            mailpit.write(vals)
        else:
            Server.create(vals)
        _logger.info(
            "MAILPIT_LOCAL_DEV activo — '%s' configurado en %s:%s "
            "(prioridad máxima, sequence=0).",
            _MAILPIT_SERVER_NAME,
            vals["smtp_host"],
            vals["smtp_port"],
        )

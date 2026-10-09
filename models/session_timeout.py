# -*- coding: utf-8 -*-
from odoo import models


SESSION_TIMEOUT_PARAM = "matriz.session_timeout_seconds"
DEFAULT_SESSION_TIMEOUT_SECONDS = 72000


class IrHttpSessionTimeout(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        timeout_value = self.env["ir.config_parameter"].sudo().get_param(
            SESSION_TIMEOUT_PARAM,
            str(DEFAULT_SESSION_TIMEOUT_SECONDS),
        )
        try:
            timeout_seconds = int(timeout_value)
        except (TypeError, ValueError):
            timeout_seconds = DEFAULT_SESSION_TIMEOUT_SECONDS

        # El cliente web usa este valor para cerrar la sesion por inactividad real.
        # 0 o un valor negativo desactiva el cierre automatico.
        result["matriz_session_timeout_seconds"] = max(timeout_seconds, 0)
        return result

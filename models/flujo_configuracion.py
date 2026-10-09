#!/usr/bin/python
from datetime import datetime

from odoo import fields


PARAM_FECHA_INICIO_NUEVO_FLUJO = 'Matriz.fecha_inicio_nuevo_flujo_contratacion'
FECHA_INICIO_NUEVO_FLUJO_DEFAULT = datetime(2026, 7, 24, 0, 0, 0)
FECHA_INICIO_NUEVO_FLUJO_DEFAULT_DATE = FECHA_INICIO_NUEVO_FLUJO_DEFAULT.date()


def obtener_fecha_inicio_nuevo_flujo_contratacion(env):
    """Fecha tecnica que activa el flujo nuevo para FC y Otrosi."""
    valor = env['ir.config_parameter'].sudo().get_param(
        PARAM_FECHA_INICIO_NUEVO_FLUJO
    )
    if not valor:
        return FECHA_INICIO_NUEVO_FLUJO_DEFAULT_DATE

    try:
        return fields.Date.to_date(fields.Datetime.to_datetime(valor))
    except Exception:
        return FECHA_INICIO_NUEVO_FLUJO_DEFAULT_DATE


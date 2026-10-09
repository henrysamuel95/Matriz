from odoo import fields, models

from .constantes import POLIZA_FIELDS


class SolicitudPolizaCampos(models.AbstractModel):
    _name = 'contrataciones_fuv.solicitud.poliza.campos'
    _description = 'Campos comunes de una solicitud de pólizas'

    poliza_cumplimiento = fields.Boolean(string='Póliza de cumplimiento')
    poliza_buen_manejo_anticipo = fields.Boolean(
        string='Póliza de buen manejo y correcta inversión del anticipo',
    )
    poliza_devolucion_pago_anticipado = fields.Boolean(
        string='Póliza de devolución del primer pago anticipado',
    )
    poliza_salarios_prestaciones = fields.Boolean(
        string=(
            'Póliza de pago de salarios, prestaciones sociales legales '
            'e indemnizaciones laborales'
        ),
    )
    poliza_estabilidad_calidad_obra = fields.Boolean(
        string='Póliza de estabilidad y calidad de la obra',
    )
    poliza_calidad_servicio = fields.Boolean(
        string='Póliza de calidad del servicio',
    )
    poliza_calidad_bienes = fields.Boolean(
        string='Póliza de calidad de los bienes',
    )
    poliza_seriedad_oferta = fields.Boolean(
        string='Póliza de seriedad de la oferta',
    )
    poliza_responsabilidad_civil = fields.Boolean(
        string='Póliza de responsabilidad civil extracontractual',
    )
    otras_polizas = fields.Boolean(string='Otras pólizas')

    def _polizas_seleccionadas(self):
        self.ensure_one()
        return [field_name for field_name in POLIZA_FIELDS if self[field_name]]

    def _valores_polizas(self):
        self.ensure_one()
        return {field_name: self[field_name] for field_name in POLIZA_FIELDS}

from odoo import api, fields, models

from .documentos import DOCUMENTOS_JURIDICA, DOCUMENTOS_NATURAL
from .estados import ESTADOS_CONTRATACION_SGR


PASOS_SGR = [estado for estado, _nombre in ESTADOS_CONTRATACION_SGR]


class ContratacionesSgrKanban(models.Model):
    _inherit = 'contrataciones_sgr'

    sgr_paso_flujo = fields.Char(compute='_compute_sgr_paso_flujo')
    sgr_documentos_cargados = fields.Integer(compute='_compute_sgr_documentos_kanban')
    sgr_documentos_total = fields.Integer(compute='_compute_sgr_documentos_kanban')
    sgr_documentos_porcentaje = fields.Integer(compute='_compute_sgr_documentos_kanban')

    @api.depends('estado_contratacion')
    def _compute_sgr_paso_flujo(self):
        for record in self:
            estado = record.estado_contratacion
            record.sgr_paso_flujo = (
                '%s/%s' % (PASOS_SGR.index(estado) + 1, len(PASOS_SGR))
                if estado in PASOS_SGR else False
            )

    @api.depends('select_persona', 'documento_ids.drive_id')
    def _compute_sgr_documentos_kanban(self):
        for record in self:
            categoria = 'pj' if record.select_persona == 'persona_juridica' else 'pn'
            total = len(DOCUMENTOS_JURIDICA if categoria == 'pj' else DOCUMENTOS_NATURAL)
            if not record.select_persona:
                total = 0
            cargados = len(record.documento_ids.filtered(
                lambda documento: documento.categoria == categoria and documento.drive_id
            )) if total else 0
            record.sgr_documentos_cargados = cargados
            record.sgr_documentos_total = total
            record.sgr_documentos_porcentaje = round(cargados * 100 / total) if total else 0

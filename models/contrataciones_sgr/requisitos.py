from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ContratacionesSgrRequisitos(models.Model):
    _inherit = 'contrataciones_sgr'

    fecha_acta_inicio_sgr = fields.Date(
        related='fecha_iniciacion', string='Fecha del acta de inicio',
        readonly=False, store=True,
    )
    duracion = fields.Char(string='Duración', compute='_compute_duracion_sgr', store=True)

    @api.onchange('fecha_acta_inicio_sgr')
    def _onchange_fecha_acta_inicio_sgr(self):
        for record in self:
            record.fecha_iniciacion = record.fecha_acta_inicio_sgr

    @api.depends('fecha_iniciacion', 'fecha_terminacion')
    def _compute_duracion_sgr(self):
        for record in self:
            inicio = record.fecha_iniciacion
            fin = record.fecha_terminacion
            if not inicio or not fin or fin < inicio:
                record.duracion = False
                continue
            diferencia = relativedelta(fin + timedelta(days=1), inicio)
            partes = []
            for cantidad, singular, plural in (
                (diferencia.years, 'año', 'años'),
                (diferencia.months, 'mes', 'meses'),
                (diferencia.days, 'día', 'días'),
            ):
                if cantidad:
                    partes.append('%s %s' % (cantidad, singular if cantidad == 1 else plural))
            record.duracion = ' '.join(partes)

    @api.constrains('fecha_iniciacion', 'fecha_terminacion')
    def _check_fechas_contrato_sgr(self):
        for record in self:
            if record.fecha_iniciacion and record.fecha_terminacion and record.fecha_terminacion < record.fecha_iniciacion:
                raise ValidationError('La fecha de terminación no puede ser anterior a la fecha del acta de inicio.')
    acta_inicio_doc = fields.Binary(string='Acta de inicio', attachment=True)
    acta_inicio_doc_fname = fields.Char(string='Nombre del acta de inicio')
    certificacion_inhabilidades_doc_fname = fields.Char(
        string='Nombre del certificado de inhabilidades (persona natural)',
    )
    cert_inhabilidades_doc_fname = fields.Char(
        string='Nombre del certificado de inhabilidades (persona jurídica)',
    )
    perfil_profesional_estudio_previo = fields.Text(
        string='Perfil profesional para estudio previo',
    )
    experiencia_requerida_estudio_previo = fields.Text(
        string='Experiencia requerida para estudio previo',
    )
    perfil_secop = fields.Text(string='Perfil SECOP')

import base64
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from docxtpl import DocxTemplate
from num2words import num2words

from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_resource

from .plantilla_contrato_ps import MESES


class ContratacionesSgrEstudioPrevio(models.Model):
    _inherit = 'contrataciones_sgr'

    sgr_ep_designacion = fields.Text(string='Designación SGR del estudio previo')
    sgr_ep_lugar_ejecucion = fields.Char(string='Lugar de ejecución del estudio previo')
    sgr_ep_proyectado_por = fields.Char(string='Estudio previo proyectado por')
    sgr_ep_revisado_por = fields.Char(string='Estudio previo revisado por')
    estudio_previo_word = fields.Binary(attachment=True, readonly=True, copy=False)
    estudio_previo_word_nombre = fields.Char(readonly=True, copy=False)

    def _contexto_estudio_previo_sgr(self):
        self.ensure_one()
        if self.select_persona != 'persona_natural':
            raise UserError('Esta plantilla de estudio previo SGR corresponde a persona natural.')

        datos = {
            'Fecha de solicitud': self.fecha_solicitud,
            'Designación SGR': self.sgr_ep_designacion,
            'Justificación de la solicitud': self.justificacion_solicitud,
            'Objeto del contrato': self.objeto_contrato,
            'Actividades específicas': self.actividades_especificas_natu,
            'Perfil profesional para estudio previo': self.perfil_profesional_estudio_previo,
            'Experiencia requerida para estudio previo': self.experiencia_requerida_estudio_previo,
            'Lugar de ejecución': self.sgr_ep_lugar_ejecucion,
            'Valor total del contrato': self.valor_total_contrato > 0,
            'Número de CDP SGR': self.sgr_cdp_numero,
            'Fecha del CDP SGR': self.sgr_cdp_fecha,
            'Forma de pago': self.forma_pago,
            'Duración': self.duracion,
            'Proyectado por': self.sgr_ep_proyectado_por,
            'Revisado por': self.sgr_ep_revisado_por,
        }
        faltantes = [etiqueta for etiqueta, valor in datos.items() if not valor]
        if faltantes:
            raise UserError('Complete estos datos antes de generar el estudio previo: %s.' % ', '.join(faltantes))

        fecha_cdp = fields.Date.to_date(self.sgr_cdp_fecha)
        fecha_estudio = fields.Date.to_date(self.fecha_solicitud)
        valor = self._valor_en_letras(self.valor_total_contrato)
        return {
            'designacion_sgr': self.sgr_ep_designacion,
            'necesidad': self.justificacion_solicitud,
            'objeto_contrato': self.objeto_contrato,
            'actividades_lista': [
                linea.strip() for linea in self.actividades_especificas_natu.splitlines()
                if linea.strip()
            ],
            'perfil_requerido': self.perfil_profesional_estudio_previo,
            'experiencia_requerida': self.experiencia_requerida_estudio_previo,
            'lugar_ejecucion': self.sgr_ep_lugar_ejecucion,
            'valor_cdp_estudio': (
                '%s. Para todos los efectos legales, el presente contrato se encuentra '
                'respaldado con el CDP del SGR No. %s del %s de %s de %s del Sistema General de Regalías.'
            ) % (
                valor, self.sgr_cdp_numero, fecha_cdp.day,
                MESES[fecha_cdp.month - 1], fecha_cdp.year,
            ),
            'forma_pago': self.forma_pago,
            'plazo_texto': self.duracion,
            'fecha_estudio_texto': '%s (%s) %s del mes de %s del año %s (%s).' % (
                num2words(fecha_estudio.day, lang='es'), fecha_estudio.day,
                'día' if fecha_estudio.day == 1 else 'días',
                MESES[fecha_estudio.month - 1],
                num2words(fecha_estudio.year, lang='es'), fecha_estudio.year,
            ),
            'proyectado_por': self.sgr_ep_proyectado_por,
            'revisado_por': self.sgr_ep_revisado_por,
        }

    def generar_estudio_previo_sgr_docx(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo Soporte puede generar el estudio previo SGR.')
        contexto = self._contexto_estudio_previo_sgr()
        ruta = get_module_resource(
            'Matriz', 'static', 'src', 'docx', 'plantilla_estudio_previo_sgr.docx',
        )
        if not ruta or not Path(ruta).is_file():
            raise UserError('No se encontró la plantilla Word del estudio previo SGR.')

        plantilla = DocxTemplate(ruta)
        plantilla.render(contexto, autoescape=True)
        with tempfile.TemporaryDirectory(prefix='sgr_estudio_previo_') as directory:
            salida = Path(directory) / 'estudio_previo.docx'
            plantilla.save(salida)
            contenido = salida.read_bytes()

        nombre = 'Estudio previo SGR - %s.docx' % (self.id_del_contrato or self.name or self.id)
        nombre = re.sub(r'[\\/:*?"<>|]', '-', nombre)
        self.write({
            'estudio_previo_word': base64.b64encode(contenido),
            'estudio_previo_word_nombre': nombre,
        })
        return {
            'type': 'ir.actions.act_url',
            'name': nombre,
            'url': '/web/content/?model=contrataciones_sgr&id=%s&field=estudio_previo_word&filename_field=estudio_previo_word_nombre&download=true&filename=%s' % (
                self.id, quote(nombre),
            ),
            'target': 'self',
        }

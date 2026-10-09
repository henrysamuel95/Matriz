import base64
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from docxtpl import DocxTemplate
from num2words import num2words

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.modules.module import get_module_resource


MESES = (
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
)

PLANTILLAS_CONTRATO_SGR = {
    'persona_natural': 'plantilla_contrato_sgr_pn.docx',
    'persona_juridica': 'contrato_sgr_pj.docx',
}


class ContratacionesSgrPlantillaContratoPs(models.Model):
    _inherit = 'contrataciones_sgr'

    sgr_cdp_numero = fields.Char(string='Número de CDP SGR')
    sgr_cdp_fecha = fields.Date(string='Fecha del CDP SGR')
    sgr_pj_consideraciones = fields.Text(string='Consideraciones contractuales PJ')
    sgr_pj_unspsc = fields.Text(string='Clasificador UNSPSC PJ')
    sgr_pj_convocatoria = fields.Char(string='Convocatoria PJ')
    sgr_pj_lugar_ejecucion = fields.Char(string='Lugar de ejecución PJ')
    contrato_word_nombre = fields.Char(string='Nombre del contrato Word', readonly=True, copy=False)
    fecha_firma_contrato_sgr = fields.Date(
        string='Fecha de firma del contrato SGR', readonly=True, copy=False,
    )

    @staticmethod
    def _valor_en_letras(valor):
        cantidad = int(round(valor))
        numero = '{:,.0f}'.format(cantidad).replace(',', '.')
        return '%s PESOS M/CTE ($%s) IMPUESTOS INCLUIDOS' % (
            num2words(cantidad, lang='es').upper(), numero,
        )

    @staticmethod
    def _texto_fecha_firma_contrato(fecha):
        fecha = fields.Date.to_date(fecha)
        return 'Se firma en Santiago de Cali a los %s (%s) días del mes de %s del año %s (%s).' % (
            num2words(fecha.day, lang='es'), fecha.day,
            MESES[fecha.month - 1].capitalize(),
            num2words(fecha.year, lang='es'), fecha.year,
        )

    def _contexto_contrato_sgr(self, tipo_persona):
        self.ensure_one()
        if self.select_persona != tipo_persona or tipo_persona not in PLANTILLAS_CONTRATO_SGR:
            raise UserError('El tipo de persona no corresponde al contrato Word solicitado.')

        juridica = tipo_persona == 'persona_juridica'
        nombre = (
            self.razon_social if juridica else self.nombre_contratista
        ) or self.contratista_proveedor_relacionado.name
        documento = self.nit if juridica else self.documento_identidad
        actividades = self.actividades_especificas if juridica else self.actividades_especificas_natu
        datos = {
            'Número del contrato': self.id_del_contrato,
            'Contratista': nombre,
            'NIT' if juridica else 'Documento de identidad': documento,
            'Objeto del contrato': self.objeto_contrato,
            'Valor total del contrato': self.valor_total_contrato > 0,
            'Forma de pago': self.forma_pago,
            'Duración': self.duracion,
            'Actividades específicas': actividades,
            'Número de CDP SGR': self.sgr_cdp_numero,
            'Fecha del CDP SGR': self.sgr_cdp_fecha,
            'Correo electrónico': self.correo,
            'Dirección': self.direccion_contratista,
            'Teléfono/Celular': self.telefono_contratista,
        }
        if not juridica:
            datos['Ciudad de expedición'] = self.ciudad_contratista
            datos['Justificación de la solicitud'] = self.justificacion_solicitud
        else:
            datos.update({
                'Representante legal': self.r_legal,
                'Cédula del representante legal': self.cedula_representante_legal,
                'Ciudad de expedición del representante': self.ciudad_contratista,
                'Consideraciones contractuales PJ': self.sgr_pj_consideraciones,
                'Clasificador UNSPSC PJ': self.sgr_pj_unspsc,
                'Convocatoria PJ': self.sgr_pj_convocatoria,
                'Lugar de ejecución PJ': self.sgr_pj_lugar_ejecucion,
            })
        faltantes = [etiqueta for etiqueta, valor in datos.items() if not valor]
        if faltantes:
            raise UserError('Complete estos datos antes de generar el contrato Word: %s.' % ', '.join(faltantes))

        valor_total_texto = self._valor_en_letras(self.valor_total_contrato)
        documento_completo = documento if juridica else '%s de %s' % (documento, self.ciudad_contratista)
        fecha_cdp = fields.Date.to_date(self.sgr_cdp_fecha)
        context = {
            'numero_contrato': self.id_del_contrato,
            'nombre_contratista': nombre,
            'documento_corto': documento,
            'documento_completo': documento_completo,
            'objeto_contrato': self.objeto_contrato,
            'consideraciones': self.justificacion_solicitud,
            'valor_total_texto': valor_total_texto,
            'valor_cdp_texto': '%s y estarán respaldados con el CDP del SGR No. %s del %s de %s de %s' % (
                valor_total_texto, self.sgr_cdp_numero, fecha_cdp.day,
                MESES[fecha_cdp.month - 1], fecha_cdp.year,
            ),
            'forma_pago': self.forma_pago,
            'plazo_texto': self.duracion,
            'plazo_completo': '%s contados a partir de la suscripción del acta de inicio.' % self.duracion,
            'actividades_especificas': actividades,
            'contacto_contratista': '%s Dirección: %s. Celular: %s' % (
                self.correo, self.direccion_contratista, self.telefono_contratista,
            ),
            'fecha_firma_texto': self._texto_fecha_firma_contrato(
                self.fecha_firma_contrato_sgr or fields.Date.context_today(self),
            ),
        }
        if juridica:
            context.update({
                'parte_contratista': (
                    '%s, identificado con cédula de ciudadanía No. %s expedida en %s, '
                    'actuando como representante legal de %s, identificada con NIT %s,'
                ) % (
                    self.r_legal, self.cedula_representante_legal,
                    self.ciudad_contratista, nombre, documento,
                ),
                'representante_legal': self.r_legal,
                'consideraciones_pj': self.sgr_pj_consideraciones,
                'clasificador_unspsc': self.sgr_pj_unspsc,
                'convocatoria_pj': self.sgr_pj_convocatoria,
                'lugar_ejecucion_pj': self.sgr_pj_lugar_ejecucion,
                'referencia_cdp_pj': '%s del %s de %s de %s' % (
                    self.sgr_cdp_numero, fecha_cdp.day,
                    MESES[fecha_cdp.month - 1], fecha_cdp.year,
                ),
                'contacto_contratista_pj': ': %s. Dirección: %s. Teléfono: %s' % (
                    self.correo, self.direccion_contratista, self.telefono_contratista,
                ),
                'actividades_lista': [
                    linea.strip() for linea in actividades.splitlines() if linea.strip()
                ],
            })
        return context

    def _generar_contrato_sgr_docx(self, tipo_persona):
        self.ensure_one()
        context = self._contexto_contrato_sgr(tipo_persona)
        template_path = get_module_resource(
            'Matriz', 'static', 'src', 'docx', PLANTILLAS_CONTRATO_SGR[tipo_persona],
        )
        if not template_path or not Path(template_path).is_file():
            raise UserError('No se encontró la plantilla Word de contratación SGR.')

        template = DocxTemplate(template_path)
        template.render(context, autoescape=True)
        with tempfile.TemporaryDirectory(prefix='sgr_contrato_') as directory:
            output_path = Path(directory) / 'contrato.docx'
            template.save(output_path)
            content = output_path.read_bytes()

        nombre = '%s - %s.docx' % (
            self.id_del_contrato.strip(), context['nombre_contratista'].strip(),
        )
        nombre = re.sub(r'[\\/:*?"<>|]', '-', nombre)
        valores = {
            'informe_adjunto': base64.b64encode(content),
            'contrato_word_nombre': nombre,
        }
        if not self.fecha_firma_contrato_sgr:
            valores['fecha_firma_contrato_sgr'] = fields.Date.context_today(self)
        self.write(valores)
        return {
            'type': 'ir.actions.act_url',
            'name': nombre,
            'url': '/web/content/?model=contrataciones_sgr&id=%s&field=informe_adjunto&filename_field=contrato_word_nombre&download=true&filename=%s' % (
                self.id, quote(nombre),
            ),
            'target': 'self',
        }

    def generar_contrato_sgr_pn_docx(self):
        return self._generar_contrato_sgr_docx('persona_natural')

    def generar_contrato_sgr_pj_docx(self):
        return self._generar_contrato_sgr_docx('persona_juridica')

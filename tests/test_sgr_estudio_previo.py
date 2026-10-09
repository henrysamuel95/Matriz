from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from docx import Document
from docxtpl import DocxTemplate
from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_resource
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.plantilla_estudio_previo import ContratacionesSgrEstudioPrevio


def _contrato():
    return SimpleNamespace(
        ensure_one=lambda: None,
        _valor_en_letras=lambda valor: 'UN MILLÓN DE PESOS M/CTE ($1.000.000) IMPUESTOS INCLUIDOS',
        select_persona='persona_natural',
        fecha_solicitud=date(2026, 10, 8),
        sgr_ep_designacion='Resolución SGR de prueba',
        justificacion_solicitud='Necesidad del proyecto',
        objeto_contrato='Prestar servicios profesionales',
        actividades_especificas_natu='Primera actividad\nSegunda actividad',
        perfil_profesional_estudio_previo='Profesional en Derecho',
        experiencia_requerida_estudio_previo='Dos años de experiencia',
        sgr_ep_lugar_ejecucion='Departamento del Valle del Cauca',
        valor_total_contrato=1000000,
        sgr_cdp_numero='CDP-123',
        sgr_cdp_fecha=date(2026, 10, 1),
        forma_pago='Un pago contra entrega',
        duracion='Un mes',
        sgr_ep_proyectado_por='Persona que proyecta',
        sgr_ep_revisado_por='Persona que revisa',
    )


class TestSgrEstudioPrevio(TransactionCase):
    def test_generacion_restringida_a_soporte(self):
        contrato = SimpleNamespace(
            ensure_one=lambda: None,
            env=SimpleNamespace(user=SimpleNamespace(has_group=lambda group: False)),
        )
        with self.assertRaises(AccessError):
            ContratacionesSgrEstudioPrevio.generar_estudio_previo_sgr_docx(contrato)

    def test_contexto_usa_campos_del_formulario(self):
        contexto = ContratacionesSgrEstudioPrevio._contexto_estudio_previo_sgr(_contrato())

        self.assertEqual(contexto['actividades_lista'], ['Primera actividad', 'Segunda actividad'])
        self.assertEqual(contexto['perfil_requerido'], 'Profesional en Derecho')
        self.assertEqual(contexto['forma_pago'], 'Un pago contra entrega')
        self.assertIn('CDP-123', contexto['valor_cdp_estudio'])
        self.assertIn('octubre', contexto['fecha_estudio_texto'])

    def test_no_usa_plantilla_natural_para_juridica(self):
        contrato = _contrato()
        contrato.select_persona = 'persona_juridica'

        with self.assertRaisesRegex(UserError, 'persona natural'):
            ContratacionesSgrEstudioPrevio._contexto_estudio_previo_sgr(contrato)

    def test_exige_datos_nuevos_del_estudio(self):
        contrato = _contrato()
        contrato.sgr_ep_designacion = False

        with self.assertRaisesRegex(UserError, 'Designación SGR'):
            ContratacionesSgrEstudioPrevio._contexto_estudio_previo_sgr(contrato)

    def test_word_renderiza_sin_datos_del_ejemplo(self):
        contexto = ContratacionesSgrEstudioPrevio._contexto_estudio_previo_sgr(_contrato())
        ruta = get_module_resource(
            'Matriz', 'static', 'src', 'docx', 'plantilla_estudio_previo_sgr.docx',
        )
        with TemporaryDirectory() as directory:
            plantilla = DocxTemplate(ruta)
            plantilla.render(contexto, autoescape=True)
            salida = Path(directory) / 'estudio_previo.docx'
            plantilla.save(salida)
            contenido = '\n'.join(parrafo.text for parrafo in Document(salida).paragraphs)

        self.assertIn('Resolución SGR de prueba', contenido)
        self.assertIn('Primera actividad', contenido)
        self.assertIn('Segunda actividad', contenido)
        self.assertIn('Un pago contra entrega', contenido)
        self.assertNotIn('20243201010060', contenido)
        self.assertNotIn('Lizette González', contenido)

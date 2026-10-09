from types import SimpleNamespace

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.correo import ContratacionesSgrCorreo
from ..models.contrataciones_sgr.flujo import ContratacionesSgrFlujo


class _ContratoSgrStub:
    def __init__(self, estado, observaciones='', juridica='', financiera=''):
        self.estado_contratacion = estado
        self.observaciones = observaciones
        self.observaciones_juri = juridica
        self.observaciones_finacie = financiera
        self.sgr_estado_retorno_devolucion = False
        self.env = SimpleNamespace(user=SimpleNamespace(id=7, name='Revisor'))
        self.name = 'Contrato de prueba'
        self.correo_enviado = None
        self.notas = []
        self.documentos_soporte_ids = []

    def ensure_one(self):
        return self

    def _validar_rol_flujo_sgr(self):
        pass

    def _validar_documentos_para_contratar_sgr(self):
        return ContratacionesSgrFlujo._validar_documentos_para_contratar_sgr(self)

    def _observaciones_estado_sgr(self, estado):
        return ContratacionesSgrCorreo._observaciones_estado_sgr(self, estado)

    def _campo_observaciones_flujo_sgr(self, estado):
        return ContratacionesSgrFlujo._campo_observaciones_flujo_sgr(estado)

    def _registrar_accion_flujo_sgr(self, accion, origen, destino, observaciones):
        return ContratacionesSgrFlujo._registrar_accion_flujo_sgr(
            self, accion, origen, destino, observaciones,
        )

    def _usuarios_destino_sgr(self, estado):
        return [estado]

    def _destinatarios_correo_sgr(self, destinatarios):
        return destinatarios

    def _nombre_estado_sgr(self, estado):
        return estado

    def write(self, values):
        for field, value in values.items():
            setattr(self, field, value)

    def _enviar_correo_flujo_sgr(self, *args, **kwargs):
        self.correo_enviado = (args, kwargs)

    def message_post(self, **kwargs):
        self.notas.append(kwargs)


class TestSgrObservacionesFlujo(TransactionCase):
    def test_devolver_exige_observacion_del_area_actual(self):
        casos = (
            ('coordinador_2', '', 'Nota jurídica', 'Nota financiera'),
            ('juridico_1', 'Nota de coordinación', '  ', 'Nota financiera'),
            ('juridico_secop', 'Nota de coordinación', '', 'Nota financiera'),
            ('financiero', 'Nota de coordinación', 'Nota jurídica', ''),
        )
        for estado, coordinacion, juridica, financiera in casos:
            with self.subTest(estado=estado):
                contrato = _ContratoSgrStub(estado, coordinacion, juridica, financiera)
                with self.assertRaisesRegex(UserError, 'Para devolver'):
                    ContratacionesSgrFlujo.action_devolver_sgr(contrato)
                self.assertEqual(contrato.estado_contratacion, estado)
                self.assertIsNone(contrato.correo_enviado)

    def test_devolver_usa_observacion_juridica_secop(self):
        contrato = _ContratoSgrStub('juridico_secop', juridica='Falta el soporte')

        ContratacionesSgrFlujo.action_devolver_sgr(contrato)

        self.assertEqual(contrato.estado_contratacion, 'juridico_2')
        self.assertFalse(contrato.observaciones_juri)
        self.assertEqual(contrato.correo_enviado[0][4], 'Falta el soporte')
        self.assertIn('Falta el soporte', str(contrato.notas[0]['body']))

    def test_aprobar_permite_observacion_vacia(self):
        contrato = _ContratoSgrStub('juridico_1')

        ContratacionesSgrFlujo.action_aprobar_sgr(contrato)

        self.assertEqual(contrato.estado_contratacion, 'coordinador_2')
        self.assertEqual(contrato.correo_enviado[0][4], '')

    def test_aprobar_limpia_observacion_y_conserva_texto_en_correo_e_historial(self):
        contrato = _ContratoSgrStub('juridico_1', juridica='Contrato revisado')

        ContratacionesSgrFlujo.action_aprobar_sgr(contrato)

        self.assertFalse(contrato.observaciones_juri)
        self.assertEqual(contrato.correo_enviado[0][4], 'Contrato revisado')
        self.assertIn('Contrato revisado', str(contrato.notas[0]['body']))

    def test_contratar_exige_contrato_y_contrato_firmado_en_drive(self):
        casos = (
            ((), 'Contrato, Contrato firmado'),
            (('contrato',), 'Contrato firmado'),
            (('firmado',), 'Contrato'),
        )
        for codigos, faltantes in casos:
            with self.subTest(codigos=codigos):
                contrato = _ContratoSgrStub('coordinador_confirmacion')
                contrato.documentos_soporte_ids = [
                    SimpleNamespace(codigo=codigo, drive_id='drive-%s' % codigo)
                    for codigo in codigos
                ]
                with self.assertRaisesRegex(UserError, faltantes):
                    ContratacionesSgrFlujo.action_aprobar_sgr(contrato)
                self.assertEqual(contrato.estado_contratacion, 'coordinador_confirmacion')
                self.assertIsNone(contrato.correo_enviado)

    def test_contratar_acepta_ambos_soportes_en_drive(self):
        contrato = _ContratoSgrStub('coordinador_confirmacion')
        contrato.documentos_soporte_ids = [
            SimpleNamespace(codigo='contrato', drive_id='drive-contrato'),
            SimpleNamespace(codigo='firmado', drive_id='drive-firmado'),
        ]

        ContratacionesSgrFlujo._validar_documentos_para_contratar_sgr(contrato)

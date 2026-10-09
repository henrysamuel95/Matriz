from types import SimpleNamespace

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.soporte import (
    ContratacionesSgrSoporteEstadoWizard,
    ContratacionesSgrSoporteFlujoWizard,
    _retorno_consistente,
)


class _EventoStub:
    def __init__(self):
        self.values = []

    def sudo(self):
        return self

    def create(self, values):
        self.values.append(values)


class _EnvStub:
    def __init__(self, soporte=True):
        self.uid = 10
        self.user = SimpleNamespace(has_group=lambda group: soporte)
        self.eventos = _EventoStub()

    def __getitem__(self, model):
        assert model == 'contrataciones_sgr.soporte.evento'
        return self.eventos


class _ContratoStub:
    def __init__(self, estado, retorno=False, asignado=False, ultimo=False, elegibles=()):
        self.id = 55
        self.estado_contratacion = estado
        self.sgr_estado_retorno_devolucion = retorno
        self.sgr_usuario_asignado_id = asignado
        self.sgr_ultimo_usuario_devolucion_id = ultimo
        self.sgr_ultimo_estado_devolucion = retorno
        self.elegibles = elegibles

    def exists(self):
        return self

    def sudo(self):
        return self

    def write(self, values):
        for key, value in values.items():
            setattr(self, key, value)

    def _usuarios_destino_sgr(self, estado):
        return self.elegibles


class _WizardStub:
    def __init__(self, contrato, env, estado_nuevo=False, usuario=False, motivo='Corrección de prueba'):
        self.contratacion_id = contrato
        self.env = env
        self.estado_nuevo = estado_nuevo
        self.usuario_reasignado_id = usuario
        self.motivo = motivo
        self.estado_capturado = contrato.estado_contratacion
        self.retorno_capturado = contrato.sgr_estado_retorno_devolucion
        self.asignado_capturado_id = contrato.sgr_usuario_asignado_id

    def ensure_one(self):
        return self


class TestSgrSoporte(TransactionCase):
    def test_retorno_exige_estado_anterior_inmediato(self):
        contrato = _ContratoStub('juridico_1', retorno='coordinador_2')
        self.assertTrue(_retorno_consistente(contrato))
        contrato.estado_contratacion = 'coordinador_1'
        self.assertFalse(_retorno_consistente(contrato))

    def test_reparacion_reasigna_devolucion_sin_cambiar_estado(self):
        anterior = SimpleNamespace(id=11)
        nuevo = SimpleNamespace(id=12)
        contrato = _ContratoStub('juridico_1', retorno='coordinador_2', ultimo=anterior, elegibles=[nuevo])
        wizard = _WizardStub(contrato, _EnvStub(), usuario=nuevo)

        ContratacionesSgrSoporteFlujoWizard.action_reparar(wizard)

        self.assertEqual(contrato.estado_contratacion, 'juridico_1')
        self.assertEqual(contrato.sgr_ultimo_usuario_devolucion_id, nuevo.id)
        self.assertEqual(contrato.sgr_estado_retorno_devolucion, 'coordinador_2')
        self.assertEqual(wizard.env.eventos.values[0]['asignado_anterior_id'], anterior.id)

    def test_reparacion_no_acepta_usuario_fuera_del_proyecto(self):
        contrato = _ContratoStub('juridico_1', retorno='coordinador_2')
        wizard = _WizardStub(contrato, _EnvStub(), usuario=SimpleNamespace(id=12))
        with self.assertRaisesRegex(UserError, 'proyecto'):
            ContratacionesSgrSoporteFlujoWizard.action_reparar(wizard)
        self.assertFalse(wizard.env.eventos.values)

    def test_reparacion_libera_asignacion_sin_devolucion(self):
        asignado = SimpleNamespace(id=11)
        contrato = _ContratoStub('juridico_1', asignado=asignado)
        wizard = _WizardStub(contrato, _EnvStub())

        ContratacionesSgrSoporteFlujoWizard.action_reparar(wizard)

        self.assertEqual(contrato.estado_contratacion, 'juridico_1')
        self.assertFalse(contrato.sgr_usuario_asignado_id)
        self.assertEqual(wizard.env.eventos.values[0]['asignado_anterior_id'], asignado.id)

    def test_cambio_estado_limpia_metadatos_y_registra_motivo(self):
        asignado = SimpleNamespace(id=11)
        contrato = _ContratoStub('juridico_1', retorno='coordinador_2', asignado=asignado)
        wizard = _WizardStub(contrato, _EnvStub(), estado_nuevo='financiero')

        ContratacionesSgrSoporteEstadoWizard.action_aplicar(wizard)

        self.assertEqual(contrato.estado_contratacion, 'financiero')
        self.assertFalse(contrato.sgr_estado_retorno_devolucion)
        self.assertFalse(contrato.sgr_usuario_asignado_id)
        self.assertEqual(wizard.env.eventos.values[0]['retorno_anterior'], 'coordinador_2')

    def test_usuario_ajeno_a_soporte_no_puede_cambiar_estado(self):
        contrato = _ContratoStub('juridico_1')
        wizard = _WizardStub(contrato, _EnvStub(soporte=False), estado_nuevo='financiero')
        with self.assertRaises(AccessError):
            ContratacionesSgrSoporteEstadoWizard.action_aplicar(wizard)
        self.assertEqual(contrato.estado_contratacion, 'juridico_1')

    def test_asistente_abierto_no_sobrescribe_otro_cambio_de_estado(self):
        contrato = _ContratoStub('juridico_1')
        wizard = _WizardStub(contrato, _EnvStub(), estado_nuevo='financiero')
        contrato.estado_contratacion = 'coordinador_2'

        with self.assertRaisesRegex(UserError, 'cambió mientras'):
            ContratacionesSgrSoporteEstadoWizard.action_aplicar(wizard)

        self.assertEqual(contrato.estado_contratacion, 'coordinador_2')
        self.assertFalse(wizard.env.eventos.values)

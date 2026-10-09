from types import SimpleNamespace

from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.documentos import (
    ContratacionesSgrDocumentos,
    DOCUMENTOS_SOPORTE,
)


class _Documentos(list):
    def filtered(self, predicate):
        return _Documentos(doc for doc in self if predicate(doc))

    def sorted(self, key):
        return _Documentos(sorted(self, key=key))

    def _sgr_marcar_log_publicado(self):
        for doc in self:
            doc.sgr_log_pendiente = False

    def sudo(self):
        return self


class _Contrato:
    def __init__(self):
        self.documento_ids = _Documentos()
        self.env = SimpleNamespace(user=SimpleNamespace(name='Usuario actual'))
        self.notas = []

    def __iter__(self):
        return iter((self,))

    def message_post(self, **values):
        self.notas.append(values)


class TestSgrDocumentosTrazabilidad(TransactionCase):
    def test_catalogo_soporte_incluye_acta_aprobacion(self):
        codigos = [codigo for codigo, _nombre, _condicion in DOCUMENTOS_SOPORTE]
        self.assertEqual(len(codigos), len(set(codigos)))
        self.assertIn(
            ('acta_aprobacion', 'Acta de aprobación', ''),
            DOCUMENTOS_SOPORTE,
        )
        self.assertIn(
            ('estudio_previo', 'Estudio previo', ''),
            DOCUMENTOS_SOPORTE,
        )

    def test_lote_publica_una_lista_y_no_la_repite(self):
        contrato = _Contrato()
        contrato.documento_ids = _Documentos(
            SimpleNamespace(
                contratacion_id=contrato, categoria='pn', sequence=number, id=number,
                drive_id='drive-%s' % number, sgr_log_pendiente=True,
                name='Requisito %s' % number, archivo_nombre='archivo-%s.pdf' % number,
                sgr_cargado_por_id=SimpleNamespace(name='Samuel'),
            )
            for number in range(1, 4)
        )

        ContratacionesSgrDocumentos._sgr_publicar_cargas_pendientes(contrato)
        ContratacionesSgrDocumentos._sgr_publicar_cargas_pendientes(contrato)

        self.assertEqual(len(contrato.notas), 1)
        body = str(contrato.notas[0]['body'])
        self.assertEqual(body.count('<li>'), 3)
        self.assertIn('archivo-2.pdf', body)
        self.assertIn('Samuel', body)
        self.assertTrue(all(not doc.sgr_log_pendiente for doc in contrato.documento_ids))

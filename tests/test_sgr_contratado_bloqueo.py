import json
from types import SimpleNamespace

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from ..models.contrataciones_sgr.contrataciones_sgr import (
    ContratacionesSgr,
    _sgr_arch_solo_lectura_contratado,
)


class _Contrato:
    def __init__(self, estado, soporte=False, sudo=False):
        self.estado_contratacion = estado
        self.env = SimpleNamespace(
            su=sudo,
            user=SimpleNamespace(has_group=lambda group: soporte),
        )

    def __iter__(self):
        return iter((self,))

    def _sgr_validar_edicion_contratado(self):
        return ContratacionesSgr._sgr_validar_edicion_contratado(self)


class TestSgrContratadoBloqueo(TransactionCase):
    def test_contratado_solo_soporte_puede_modificar(self):
        with self.assertRaisesRegex(UserError, 'Solo Soporte'):
            ContratacionesSgr._sgr_validar_edicion_contratado(
                _Contrato('contratado')
            )
        ContratacionesSgr._sgr_validar_edicion_contratado(
            _Contrato('contratado', soporte=True)
        )
        ContratacionesSgr._sgr_validar_edicion_contratado(
            _Contrato('contratado', sudo=True)
        )
        ContratacionesSgr._sgr_validar_edicion_contratado(
            _Contrato('coordinador_confirmacion')
        )

    def test_write_y_unlink_rechazan_contrato_cerrado(self):
        contrato = _Contrato('contratado')
        with self.assertRaisesRegex(UserError, 'Solo Soporte'):
            ContratacionesSgr.write(contrato, {'name': 'Nuevo nombre'})
        with self.assertRaisesRegex(UserError, 'Solo Soporte'):
            ContratacionesSgr.unlink(contrato)

    def test_vista_no_soporte_bloquea_campos_y_cargas(self):
        arch = '''<form>
            <field name="estado_contratacion"/>
            <field name="name" modifiers='{"readonly": [["otra_regla", "=", true], ["confirmado", "=", true]]}'/>
            <field name="documentos_soporte_ids">
                <tree>
                    <button name="action_subir_documento" type="object"
                            modifiers='{"invisible": [["drive_id", "!=", false]]}'/>
                </tree>
            </field>
            <button name="generar_contrato_sgr_pn_docx" type="object"/>
            <button name="action_nuevo_otrosi_sgr" type="object"/>
        </form>'''
        result = etree.fromstring(_sgr_arch_solo_lectura_contratado(arch))

        state = json.loads(result.xpath("//field[@name='estado_contratacion']")[0].get('modifiers'))
        self.assertEqual(state['readonly'], [['estado_contratacion', '=', 'contratado']])
        name = json.loads(result.xpath("//field[@name='name']")[0].get('modifiers'))
        self.assertIn(['otra_regla', '=', True], name['readonly'])
        self.assertIn(['confirmado', '=', True], name['readonly'])
        upload = json.loads(result.xpath("//button[@name='action_subir_documento']")[0].get('modifiers'))
        self.assertIn(['parent.estado_contratacion', '=', 'contratado'], upload['invisible'])
        self.assertIn(['drive_id', '!=', False], upload['invisible'])
        template = json.loads(result.xpath("//button[@name='generar_contrato_sgr_pn_docx']")[0].get('modifiers'))
        self.assertEqual(template['invisible'], [['estado_contratacion', '=', 'contratado']])
        self.assertIsNone(result.xpath("//button[@name='action_nuevo_otrosi_sgr']")[0].get('modifiers'))

from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestSeguimientoFirmasMultiRegistro(TransactionCase):
    """Regresión del bug de escritura multi-registro en seguimiento_firmas.

    Antes del fix, un write() sobre un recordset con más de un registro
    terminaba persistiendo en TODOS los registros el hash/log calculado
    para el último registro procesado en el loop del decorador.
    """

    def test_documento_archivo_gd_single_record_sigue_firmando_bien(self):
        rec = self.env['documento_archivo_gd'].create({
            'asunto_expediente': 'Expediente único',
        })
        rec.write({'firmado_elaboracion': True})

        self.assertTrue(rec.firma_elaboracion_hash)
        self.assertTrue(rec.log_elaboracion_id)
        self.assertEqual(rec.log_elaboracion_id.registro_origen_id, rec.id)

    def test_documento_archivo_gd_multi_record_firma_no_se_cruza(self):
        rec1 = self.env['documento_archivo_gd'].create({
            'asunto_expediente': 'Expediente 1',
        })
        rec2 = self.env['documento_archivo_gd'].create({
            'asunto_expediente': 'Expediente 2',
        })

        (rec1 + rec2).write({'firmado_elaboracion': True})

        self.assertTrue(rec1.firma_elaboracion_hash)
        self.assertTrue(rec2.firma_elaboracion_hash)
        self.assertNotEqual(rec1.firma_elaboracion_hash, rec2.firma_elaboracion_hash)

        self.assertTrue(rec1.log_elaboracion_id)
        self.assertTrue(rec2.log_elaboracion_id)
        self.assertNotEqual(rec1.log_elaboracion_id, rec2.log_elaboracion_id)

        self.assertEqual(rec1.log_elaboracion_id.registro_origen_id, rec1.id)
        self.assertEqual(rec2.log_elaboracion_id.registro_origen_id, rec2.id)

    def test_declaracion_juramentada_multi_record_dos_slots_no_se_cruzan(self):
        dec1 = self.env['declaracion_juramentada'].create({'name': 'Declarante Uno'})
        dec2 = self.env['declaracion_juramentada'].create({'name': 'Declarante Dos'})

        (dec1 + dec2).write({'firmado_contratista': True})

        self.assertNotEqual(dec1.firma_contratista_hash, dec2.firma_contratista_hash)
        self.assertNotEqual(dec1.log_contratista_id, dec2.log_contratista_id)
        self.assertEqual(dec1.log_contratista_id.registro_origen_id, dec1.id)
        self.assertEqual(dec2.log_contratista_id.registro_origen_id, dec2.id)

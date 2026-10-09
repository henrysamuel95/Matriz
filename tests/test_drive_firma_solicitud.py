import base64
import hashlib
import os
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from PyPDF2 import PdfReader
from reportlab.pdfgen import canvas as pdf_canvas

try:
    import fitz
except ImportError:
    fitz = None

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tests import tagged



def _pdf_una_pagina():
    buf = BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(300, 400))
    c.drawString(50, 350, "Documento de prueba")
    c.showPage()
    c.save()
    return buf.getvalue()


def _pdf_con_marcadores(paginas):
    """Genera un PDF; cada página recibe una lista de (x, y, texto)."""
    buf = BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(300, 400))
    for textos in paginas:
        for x, y, texto in textos:
            c.drawString(x, y, texto)
        c.showPage()
    c.save()
    return buf.getvalue()


def _png_pequeno():
    buf = BytesIO()
    Image.new('RGBA', (40, 20), (0, 0, 0, 255)).save(buf, format='PNG')
    return buf.getvalue()


@tagged('post_install', '-at_install')
class TestDriveFirmaSolicitud(TransactionCase):

    def setUp(self):
        super().setUp()
        # Este entorno de pruebas no tiene credenciales reales de Google
        # Drive (config/client_secrets.json) — contratista_proveedor.create()
        # intenta sincronizar una carpeta de Drive por cada contratista
        # nuevo, lo cual falla aquí. Se anula ese paso para estos tests,
        # que no necesitan Drive real, solo el registro en Odoo.
        drive_sync_patcher = patch(
            'odoo.addons.Matriz.models.contratista_proveedor.ContratistaProveedor'
            '._sync_with_drive_stage_provedor'
        )
        drive_sync_patcher.start()
        self.addCleanup(drive_sync_patcher.stop)

        self.contratista = self.env['contratista_proveedor'].create({
            'name': 'Contratista de prueba',
            'correo_facturacion': 'contratista@example.com',
        })
        self.contratacion = self.env['contrataciones_fuv'].create({
            'contratista_proveedor_relacionado': self.contratista.id,
            'salario_basico': 1000000,
            'new_name': 'Contratación de prueba',
            # 'contratado' porque contrataciones_fuv.write() restringe la
            # edición por grupo según estado_contratacion (coordinador/
            # financiera/jurídico/recursos_humanos) — 'contratado' es el
            # único estado sin ese candado, necesario para poder asignar
            # contratos_id más abajo sin depender de grupos de permisos.
            'estado_contratacion': 'contratado',
        })
        self.drive_rec = self.env['drive'].create({
            'contratacion_fuv_documentos_id': self.contratacion.id,
            'file_name': 'contrato_firmado.pdf',
            'file_id': 'drive-fixture',
        })
        # Necesario para que sea un slot firmable tras la corrección del bug
        # de firma_publica_disponible (antes cualquier documento de la
        # contratación calificaba; ahora solo los campos en _CAMPOS_FIRMABLES).
        self.contratacion.contratos_id = self.drive_rec.id
        download_patcher = patch(
            'odoo.addons.Matriz.models.drive.Drive._drive_download_bytes',
            return_value=_pdf_una_pagina(),
        )
        download_patcher.start()
        self.addCleanup(download_patcher.stop)

    def test_decorador_firma_persiste_hash_log_e_imagen(self):
        solicitud = self.env['drive.firma.solicitud'].create({'drive_id': self.drive_rec.id})
        imagen_b64 = base64.b64encode(_png_pequeno())

        solicitud.write({'firmado_contratista': True, 'firma_imagen': imagen_b64})

        self.assertTrue(solicitud.firma_contratista_hash)
        self.assertTrue(solicitud.log_contratista_id)
        self.assertEqual(solicitud.log_contratista_id.registro_origen_id, solicitud.id)
        self.assertTrue(solicitud.firma_imagen)

    def test_name_es_legible_no_el_fallback_tecnico(self):
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'nombre_contratista': 'Contratista de prueba',
        })

        self.assertEqual(solicitud.name, 'Contratista de prueba - contrato_firmado.pdf')
        self.assertNotIn('drive.firma.solicitud,', solicitud.display_name)

    def test_log_de_firma_usa_el_name_legible_como_nombre_documento(self):
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'nombre_contratista': 'Contratista de prueba',
        })

        solicitud.write({'firmado_contratista': True})

        self.assertEqual(solicitud.log_contratista_id.nombre_documento, solicitud.name)
        self.assertNotIn('drive.firma.solicitud,', solicitud.log_contratista_id.nombre_documento)

    def test_action_enviar_para_firmar_requiere_contratacion(self):
        drive_sin_contratacion = self.env['drive'].create({'file_name': 'suelto.pdf'})
        with self.assertRaises(UserError):
            drive_sin_contratacion.action_enviar_para_firmar()

    def test_upload_contrato_usa_carpeta_override_de_pruebas(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'matriz.firma_test_drive_folder_id', 'root'
        )
        with patch.dict(os.environ, {'GOOGLE_DRIVE_LOCAL_DEV': '1'}):
            with patch(
                'odoo.addons.Matriz.models.drive.Drive.upload_to_drive',
                return_value=True,
            ) as upload_mock:
                self.env['drive'].with_context(tipo_documento_upload='contratos').create({
                    'file_name': 'contrato_para_firma.pdf',
                    'file_data': base64.b64encode(_pdf_una_pagina()),
                    'folder_id': 'carpeta-produccion-sin-acceso',
                })

        upload_mock.assert_called_once_with(new_folder_id='root')

    def test_upload_contrato_ignora_override_fuera_de_desarrollo_local(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'matriz.firma_test_drive_folder_id', 'root'
        )
        with patch.dict(os.environ, {'GOOGLE_DRIVE_LOCAL_DEV': '0'}):
            with patch(
                'odoo.addons.Matriz.models.drive.Drive.upload_to_drive',
                return_value=True,
            ) as upload_mock:
                self.env['drive'].with_context(tipo_documento_upload='contratos').create({
                    'file_name': 'contrato_sin_override.pdf',
                    'file_data': base64.b64encode(_pdf_una_pagina()),
                    'folder_id': 'carpeta-real',
                })

        upload_mock.assert_called_once_with(new_folder_id=None)

    def test_upload_fallido_del_popup_aborta_el_create(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'matriz.firma_test_drive_folder_id', False
        )
        nombre = 'contrato_que_no_debe_persistir.pdf'

        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                with patch(
                    'odoo.addons.Matriz.models.drive.Drive.upload_to_drive',
                    return_value=False,
                ):
                    self.env['drive'].with_context(
                        tipo_documento_upload='contratos'
                    ).create({
                        'file_name': nombre,
                        'file_data': base64.b64encode(_pdf_una_pagina()),
                        'folder_id': 'carpeta-inaccesible',
                    })

        self.assertFalse(self.env['drive'].search([('file_name', '=', nombre)]))

    def test_action_enviar_para_firmar_requiere_correo(self):
        contratista_sin_correo = self.env['contratista_proveedor'].create({'name': 'Sin correo'})
        contratacion_sin_correo = self.env['contrataciones_fuv'].create({
            'contratista_proveedor_relacionado': contratista_sin_correo.id,
            'salario_basico': 1000000,
            'new_name': 'Contratación sin correo',
        })
        drive_sin_correo = self.env['drive'].create({
            'contratacion_fuv_documentos_id': contratacion_sin_correo.id,
        })
        with self.assertRaises(UserError):
            drive_sin_correo.action_enviar_para_firmar()

    def test_action_enviar_para_firmar_crea_solicitud_pendiente(self):
        self.drive_rec.action_enviar_para_firmar()
        solicitud = self.env['drive.firma.solicitud'].search([('drive_id', '=', self.drive_rec.id)])
        self.assertEqual(len(solicitud), 1)
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertTrue(solicitud.token)
        self.assertTrue(solicitud.token_expira)

    def test_action_enviar_para_firmar_cancela_solicitud_pendiente_previa(self):
        self.drive_rec.action_enviar_para_firmar()
        primera = self.env['drive.firma.solicitud'].search([('drive_id', '=', self.drive_rec.id)])

        self.drive_rec.action_enviar_para_firmar()

        self.assertEqual(primera.estado, 'cancelado')
        pendientes = self.env['drive.firma.solicitud'].search([
            ('drive_id', '=', self.drive_rec.id), ('estado', '=', 'pendiente'),
        ])
        self.assertEqual(len(pendientes), 1)
        self.assertNotEqual(pendientes.id, primera.id)

    def test_firma_publica_disponible_false_para_documento_no_firmable(self):
        otro_documento = self.env['drive'].create({
            'contratacion_fuv_documentos_id': self.contratacion.id,
            'file_name': 'cedula.pdf',
        })
        self.assertFalse(otro_documento.firma_publica_disponible)

    def test_firma_publica_disponible_true_para_contratos_id(self):
        self.assertTrue(self.drive_rec.firma_publica_disponible)

    def test_action_enviar_para_firmar_rechaza_documento_no_firmable(self):
        otro_documento = self.env['drive'].create({
            'contratacion_fuv_documentos_id': self.contratacion.id,
            'file_name': 'cedula.pdf',
        })
        with self.assertRaises(UserError):
            otro_documento.action_enviar_para_firmar()

    def test_cron_expira_solicitudes_vencidas_y_respeta_las_vigentes(self):
        vencida = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'token': 'tok-vencido',
            'token_expira': fields.Datetime.now() - timedelta(hours=1),
        })
        vigente = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'token': 'tok-vigente',
            'token_expira': fields.Datetime.now() + timedelta(hours=1),
        })

        self.env['drive.firma.solicitud']._cron_expirar_solicitudes_firma()

        self.assertEqual(vencida.estado, 'expirado')
        self.assertEqual(vigente.estado, 'pendiente')

    def test_estampar_firma_en_pdf_conserva_numero_de_paginas(self):
        pdf_original = _pdf_una_pagina()
        resultado = self.env['drive.firma.service']._estampar_firma_en_pdf(pdf_original, _png_pequeno())

        reader = PdfReader(BytesIO(resultado))
        self.assertEqual(len(reader.pages), 1)

    def test_estampar_sin_marcador_usa_ultima_pagina(self):
        pdf_original = _pdf_con_marcadores([
            [(30, 350, 'Página inicial')],
            [(30, 350, 'Página final')],
        ])

        resultado = self.env['drive.firma.service']._estampar_firma_en_pdf(pdf_original, _png_pequeno())
        reader = PdfReader(BytesIO(resultado))

        self.assertNotIn('Firmado electrónicamente', reader.pages[0].extract_text())
        self.assertNotIn('Firmado electrónicamente', reader.pages[1].extract_text())
        if fitz:
            with fitz.open(stream=resultado, filetype='pdf') as doc:
                self.assertFalse(doc[0].get_images())
                self.assertEqual(len(doc[1].get_images()), 1)

    def test_estampar_con_marcador_lo_elimina_y_firma_esa_pagina(self):
        if fitz is None:
            self.skipTest('PyMuPDF no está instalado')
        pdf_original = _pdf_con_marcadores([
            [(30, 350, 'Página inicial')],
            [(80, 220, '{{FIRMA_CONTRATISTA}}')],
            [(30, 350, 'Página posterior')],
        ])

        resultado = self.env['drive.firma.service']._estampar_firma_en_pdf(pdf_original, _png_pequeno())
        with fitz.open(stream=resultado, filetype='pdf') as doc:
            self.assertNotIn('{{FIRMA_CONTRATISTA}}', ''.join(p.get_text() for p in doc))
            self.assertEqual(len(doc[0].get_images(full=True)), 0)
            self.assertEqual(len(doc[1].get_images(full=True)), 1)
            self.assertEqual(len(doc[2].get_images(full=True)), 0)

    def test_estampar_elige_ultima_aparicion_del_documento(self):
        if fitz is None:
            self.skipTest('PyMuPDF no está instalado')
        pdf_original = _pdf_con_marcadores([
            [(30, 300, '{{FIRMA_CONTRATISTA}}')],
            [
                (40, 300, '{{FIRMA_CONTRATISTA}}'),
                (140, 120, '{{FIRMA_CONTRATISTA}}'),
            ],
        ])

        resultado = self.env['drive.firma.service']._estampar_firma_en_pdf(pdf_original, _png_pequeno())
        with fitz.open(stream=resultado, filetype='pdf') as doc:
            self.assertEqual(len(doc[0].get_images(full=True)), 0)
            imagenes = doc[1].get_images(full=True)
            self.assertEqual(len(imagenes), 1)
            rect = doc[1].get_image_rects(imagenes[0][0])[0]
            self.assertAlmostEqual(rect.x0, 140, delta=2)
            # Solo se redacta el marcador elegido; los anteriores permanecen.
            self.assertEqual(doc[1].get_text().count('{{FIRMA_CONTRATISTA}}'), 1)

    def test_procesar_firma_no_marca_firmado_si_falla_la_subida_a_drive(self):
        # upload_to_drive() ahora devuelve False en cualquier fallo (antes
        # devolvía None tanto en éxito como en fallo) — procesar_firma()
        # debe revisar ese resultado y NO marcar la solicitud como firmada
        # ni enviar la copia por correo si el archivo nunca llegó a Drive.
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'email_contratista': 'contratista@example.com',
            'nombre_contratista': 'Contratista de prueba',
        })
        solicitud.write({
            'token': 'token-prueba',
            'token_expira': fields.Datetime.now() + timedelta(hours=1),
        })
        imagen_b64 = base64.b64encode(_png_pequeno())
        correos_previos = self.env['mail.mail'].search([])

        with patch(
            'odoo.addons.Matriz.models.drive.Drive._drive_download_bytes',
            return_value=_pdf_una_pagina(),
        ), patch(
            'odoo.addons.Matriz.models.drive.Drive.upload_to_drive',
            return_value=False,
        ):
            with self.assertRaises(UserError):
                solicitud.procesar_firma(imagen_b64)

        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertFalse(solicitud.firmado_contratista)
        self.assertFalse(solicitud.documento_hash)
        self.assertEqual(self.env['mail.mail'].search([]), correos_previos)

    def test_enviar_copia_firmada_adjunta_pdf_al_correo_del_contratista(self):
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'email_contratista': 'contratista@example.com',
            'nombre_contratista': 'Contratista de prueba',
        })
        correos_previos = self.env['mail.mail'].search([])

        solicitud._enviar_copia_firmada(_pdf_una_pagina())

        correo = self.env['mail.mail'].search([]) - correos_previos
        self.assertEqual(len(correo), 1)
        self.assertEqual(correo.email_to, 'contratista@example.com')
        self.assertEqual(len(correo.attachment_ids), 1)
        self.assertEqual(correo.attachment_ids.mimetype, 'application/pdf')

    def test_enviar_copia_firmada_no_hace_nada_sin_correo(self):
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'email_contratista': False,
        })
        correos_previos = self.env['mail.mail'].search([])

        solicitud._enviar_copia_firmada(_pdf_una_pagina())

        self.assertEqual(self.env['mail.mail'].search([]), correos_previos)

    def test_actualizar_firmante_en_log_asigna_partner_por_correo(self):
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'email_contratista': 'firmante@example.com',
            'nombre_contratista': 'Firmante de prueba',
        })
        solicitud.write({'firmado_contratista': True})

        solicitud._actualizar_firmante_en_log()

        self.assertEqual(solicitud.log_contratista_id.signature_partner_id.email, 'firmante@example.com')
        self.assertEqual(solicitud.log_contratista_id.signature_display_partner_id.email, 'firmante@example.com')

    def test_resolver_partner_contratista_no_duplica_por_correo_repetido(self):
        solicitud1 = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'email_contratista': 'repetido@example.com',
        })
        solicitud2 = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'email_contratista': 'repetido@example.com',
        })

        partner1 = solicitud1._resolver_partner_contratista()
        partner2 = solicitud2._resolver_partner_contratista()

        self.assertEqual(partner1.id, partner2.id)

    def test_firma_solicitud_pendiente_id_refleja_el_estado(self):
        self.assertFalse(self.drive_rec.firma_solicitud_pendiente_id)

        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'token': 'tok-pendiente',
            'estado': 'pendiente',
        })
        self.drive_rec.invalidate_recordset()
        self.assertEqual(self.drive_rec.firma_solicitud_pendiente_id, solicitud)

        solicitud.write({'estado': 'cancelado'})
        self.drive_rec.invalidate_recordset()
        self.assertFalse(self.drive_rec.firma_solicitud_pendiente_id)

    def test_action_reenviar_solicitud_firma_requiere_pendiente(self):
        with self.assertRaises(UserError):
            self.drive_rec.action_reenviar_solicitud_firma()

    def test_action_reenviar_solicitud_firma_reusa_token_y_extiende_vencimiento(self):
        self.drive_rec.action_enviar_para_firmar()
        solicitud = self.env['drive.firma.solicitud'].search([('drive_id', '=', self.drive_rec.id)])
        token_original = solicitud.token
        solicitud.write({'token_expira': fields.Datetime.now() + timedelta(minutes=1)})
        vencimiento_previo = solicitud.token_expira
        correos_previos = self.env['mail.mail'].search([])

        self.drive_rec.action_reenviar_solicitud_firma()

        self.assertEqual(solicitud.token, token_original)
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertGreater(solicitud.token_expira, vencimiento_previo)
        nuevos_correos = self.env['mail.mail'].search([]) - correos_previos
        self.assertEqual(len(nuevos_correos), 1)

    def test_action_revocar_solicitud_firma_requiere_pendiente(self):
        with self.assertRaises(UserError):
            self.drive_rec.action_revocar_solicitud_firma()

    def test_action_revocar_solicitud_firma_cancela_y_libera_el_slot(self):
        self.drive_rec.action_enviar_para_firmar()
        solicitud = self.env['drive.firma.solicitud'].search([('drive_id', '=', self.drive_rec.id)])

        self.drive_rec.action_revocar_solicitud_firma()

        self.assertEqual(solicitud.estado, 'cancelado')
        self.drive_rec.invalidate_recordset()
        self.assertFalse(self.drive_rec.firma_solicitud_pendiente_id)

    def test_action_verificar_integridad_requiere_documento_firmado(self):
        solicitud = self.env['drive.firma.solicitud'].create({'drive_id': self.drive_rec.id})
        with self.assertRaises(UserError):
            solicitud.action_verificar_integridad()

    def test_action_verificar_integridad_documento_integro(self):
        contenido = b'contenido original del pdf firmado'
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'estado': 'firmado',
            'documento_hash': hashlib.sha256(contenido).hexdigest(),
        })
        with patch(
            'odoo.addons.Matriz.models.drive.Drive._drive_download_bytes',
            return_value=contenido,
        ):
            resultado = solicitud.action_verificar_integridad()

        self.assertEqual(resultado['params']['type'], 'success')

    def test_action_verificar_integridad_documento_modificado(self):
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'estado': 'firmado',
            'documento_hash': hashlib.sha256(b'contenido original').hexdigest(),
        })
        with patch(
            'odoo.addons.Matriz.models.drive.Drive._drive_download_bytes',
            return_value=b'contenido modificado despues de firmar',
        ):
            resultado = solicitud.action_verificar_integridad()

        self.assertEqual(resultado['params']['type'], 'danger')
        self.assertTrue(resultado['params']['sticky'])

    def test_action_verificar_integridad_firma_delega_a_la_ultima_solicitud_firmada(self):
        contenido = b'contenido del documento'
        solicitud = self.env['drive.firma.solicitud'].create({
            'drive_id': self.drive_rec.id,
            'estado': 'firmado',
            'documento_hash': hashlib.sha256(contenido).hexdigest(),
        })
        self.drive_rec.invalidate_recordset()
        self.assertEqual(self.drive_rec.firma_solicitud_firmada_id, solicitud)

        with patch(
            'odoo.addons.Matriz.models.drive.Drive._drive_download_bytes',
            return_value=contenido,
        ):
            resultado = self.drive_rec.action_verificar_integridad_firma()

        self.assertEqual(resultado['params']['type'], 'success')

    def test_action_verificar_integridad_firma_requiere_firma_completada(self):
        with self.assertRaises(UserError):
            self.drive_rec.action_verificar_integridad_firma()

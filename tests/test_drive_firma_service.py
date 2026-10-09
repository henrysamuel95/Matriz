"""Contrato del servicio sobre documentos independientes de contratación."""
import base64
import hashlib
from datetime import timedelta
from io import BytesIO

from PIL import Image, ImageDraw
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .test_drive_firma_solicitud import _pdf_una_pagina, _png_pequeno, fitz


@tagged('post_install', '-at_install')
class TestDriveFirmaService(TransactionCase):
    def setUp(self):
        super().setUp()
        self.service = self.env['drive.firma.service']
        self.documento = self.env['drive'].create({
            'file_name': 'documento-generico.pdf', 'file_id': 'archivo-prueba',
        })
        self.pdf = _pdf_una_pagina()
        self.imagen = base64.b64encode(_png_pequeno())
        self.real_download = type(self.documento)._drive_download_bytes
        downloader = patch(
            'odoo.addons.Matriz.models.drive.Drive._drive_download_bytes',
            return_value=self.pdf,
        )
        self.download = downloader.start()
        self.addCleanup(downloader.stop)

    def _solicitar(self, documento=None, **kwargs):
        return self.service.solicitar_firma(
            self.documento if documento is None else documento,
            kwargs.pop('email_firmante', 'firmante@example.com'),
            kwargs.pop('nombre_firmante', 'Firmante externo'), **kwargs,
        )

    def test_documento_generico_snapshots_token_y_vigencia(self):
        solicitud = self._solicitar(horas_vigencia=2)
        self.assertFalse(self.documento.contratacion_fuv_documentos_id)
        self.assertEqual(solicitud.drive_id, self.documento)
        self.assertEqual(solicitud.email_contratista, 'firmante@example.com')
        self.assertEqual(solicitud.nombre_contratista, 'Firmante externo')
        self.assertGreaterEqual(len(solicitud.token), 32)
        self.assertGreater(solicitud.token_expira, fields.Datetime.now())
        self.assertLessEqual(solicitud.token_expira, fields.Datetime.now() + timedelta(hours=2))
        self.assertEqual(solicitud.enviado_por_id, self.env.user)

    def test_rechaza_documentos_vacios_multiples_inexistentes_y_otro_modelo(self):
        segundo = self.env['drive'].create({'file_name': 'otro.pdf'})
        for documento in (self.env['drive'], self.documento | segundo,
                          self.env['drive'].browse(2147483647), self.env.user):
            with self.subTest(documento=documento), self.assertRaises(UserError):
                self._solicitar(documento=documento)
        self.download.assert_not_called()

    def test_rechaza_identidad_incompleta_o_correo_invalido(self):
        for kwargs in ({'email_firmante': ''}, {'email_firmante': 'sin-arroba'},
                       {'nombre_firmante': ''}, {'nombre_firmante': '   '}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                self._solicitar(**kwargs)

    def test_rechaza_documento_sin_archivo_remoto(self):
        self.documento.write({'file_id': False, 'drive_file_id': False})
        with self.assertRaises(UserError):
            self._solicitar()
        self.download.assert_not_called()

    def test_rechaza_pdf_corrupto_y_contenido_no_pdf(self):
        for contenido in (b'no es PDF', b'%PDF-1.7\ntruncado', b''):
            with self.subTest(contenido=contenido):
                self.download.return_value = contenido
                with self.assertRaises(UserError):
                    self._solicitar()
        self.assertFalse(self.env['drive.firma.solicitud'].search([
            ('drive_id', '=', self.documento.id)]))

    def test_nueva_solicitud_cancela_anterior(self):
        anterior = self._solicitar()
        actual = self._solicitar()
        self.assertEqual(anterior.estado, 'cancelado')
        self.assertNotEqual(anterior.token, actual.token)
        self.assertEqual(self.env['drive.firma.solicitud'].search_count([
            ('drive_id', '=', self.documento.id), ('estado', '=', 'pendiente')]), 1)

    def test_documento_firmado_bloquea_envio_y_reenvio_sin_efectos(self):
        firmada = self._solicitar()
        firmada.write({'estado': 'firmado'})
        # Simular una solicitud pendiente histórica coexistiendo con la firmada.
        pendiente = self.env['drive.firma.solicitud'].create({
            'drive_id': self.documento.id, 'token': 'fixture-pendiente',
            'token_expira': fields.Datetime.now() + timedelta(hours=1),
        })
        expiracion = pendiente.token_expira
        solicitudes = self.env['drive.firma.solicitud'].search([
            ('drive_id', '=', self.documento.id)])
        self.download.reset_mock()
        with patch.object(type(firmada), 'enviar_correo_firma') as correo:
            for operacion in (self._solicitar,
                              lambda: self.service.reenviar_solicitud(pendiente)):
                with self.assertRaisesRegex(UserError, 'Este documento ya fue firmado'):
                    operacion()
            correo.assert_not_called()
        self.download.assert_not_called()
        self.assertEqual(pendiente.estado, 'pendiente')
        self.assertEqual(pendiente.token_expira, expiracion)
        self.assertEqual(self.env['drive.firma.solicitud'].search([
            ('drive_id', '=', self.documento.id)]), solicitudes)
        self.assertEqual(self.documento.firma_solicitud_firmada_id, firmada)
        self.assertTrue(self.documento.firma_grupo_visible)

    def test_cancelada_o_expirada_permite_nueva_solicitud(self):
        for estado in ('cancelado', 'expirado'):
            with self.subTest(estado=estado):
                anterior = self._solicitar()
                anterior.write({'estado': estado})
                nueva = self._solicitar()
                self.assertEqual(nueva.estado, 'pendiente')
                self.assertNotEqual(nueva, anterior)

    def test_reenvio_reutiliza_token_y_revocacion_invalida_acceso(self):
        solicitud = self._solicitar()
        token = solicitud.token
        solicitud.token_expira = fields.Datetime.now() + timedelta(minutes=1)
        self.assertEqual(self.service.reenviar_solicitud(solicitud), solicitud)
        self.assertEqual(solicitud.token, token)
        self.assertGreater(solicitud.token_expira, fields.Datetime.now() + timedelta(hours=1))
        self.assertTrue(self.service.revocar_solicitud(solicitud))
        self.assertFalse(self.service.validar_acceso_publico(solicitud.id, token))
        with self.assertRaises(UserError):
            self.service.obtener_pdf_preview(solicitud)

    def test_acceso_invalido_no_descarga_documento(self):
        solicitud = self._solicitar()
        self.download.reset_mock()
        for identificador, token in ((solicitud.id, 'incorrecto'), (2147483647, 'incorrecto'),
                                     (solicitud.id, ''), (solicitud.id, None)):
            self.assertFalse(self.service.validar_acceso_publico(identificador, token))
        self.download.assert_not_called()

    def test_acceso_expira_pendiente_vencida(self):
        solicitud = self._solicitar()
        solicitud.token_expira = fields.Datetime.now() - timedelta(seconds=1)
        self.assertFalse(self.service.validar_acceso_publico(solicitud.id, solicitud.token))
        self.assertEqual(solicitud.estado, 'expirado')

    def test_preview_rechaza_estados_terminales(self):
        solicitud = self._solicitar()
        for estado in ('cancelado', 'expirado', 'firmado'):
            solicitud.estado = estado
            with self.subTest(estado=estado), self.assertRaises(UserError):
                self.service.obtener_pdf_preview(solicitud)

    def test_preview_devuelve_pdf_y_valida_contenido(self):
        solicitud = self._solicitar()
        self.assertEqual(self.service.obtener_pdf_preview(solicitud), self.pdf)
        self.download.return_value = b'%PDF-invalido'
        with self.assertRaises(UserError):
            self.service.obtener_pdf_preview(solicitud)

    def test_consentimiento_e_imagen_invalida_no_tocan_drive(self):
        solicitud = self._solicitar()
        self.download.reset_mock()
        for imagen, consentimiento in ((self.imagen, False), (b'%%%?', True),
                                       (base64.b64encode(b'no imagen'), True)):
            with self.subTest(consentimiento=consentimiento), self.assertRaises(UserError):
                self.service.procesar_firma(solicitud, imagen, consentimiento)
        self.download.assert_not_called()
        self.assertEqual(solicitud.estado, 'pendiente')

    def test_fallo_descarga_preserva_solicitud(self):
        solicitud = self._solicitar()
        self.download.side_effect = UserError('Descarga fallida')
        with self.assertRaises(UserError):
            self.service.procesar_firma(solicitud, self.imagen, True)
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertFalse(solicitud.log_contratista_id)

    def test_subida_fallida_no_registra_firma_y_permite_reintento(self):
        solicitud = self._solicitar()
        with patch('odoo.addons.Matriz.models.drive.Drive.upload_to_drive', return_value=False):
            with self.assertRaises(UserError):
                self.service.procesar_firma(solicitud, self.imagen, True)
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertFalse(solicitud.documento_hash)
        self.assertFalse(solicitud.log_contratista_id)
        self.assertTrue(self.service.validar_acceso_publico(solicitud.id, solicitud.token))

    def test_firma_hash_identidad_integridad_y_replay_serializado(self):
        solicitud = self._solicitar()
        subidos = []

        def upload(documento, *args, **kwargs):
            subidos.append(base64.b64decode(documento.file_data))
            return True

        with patch('odoo.addons.Matriz.models.drive.Drive.upload_to_drive', autospec=True,
                   side_effect=upload):
            validada = self.service.validar_acceso_publico(solicitud.id, solicitud.token, bloquear=True)
            resultado = self.service.procesar_firma(validada, self.imagen, True)
            self.assertEqual(resultado, solicitud)
            with self.assertRaises(UserError):
                self.service.procesar_firma(solicitud, self.imagen, True)
        self.assertEqual(len(subidos), 1)
        self.assertEqual(solicitud.estado, 'firmado')
        self.assertEqual(solicitud.documento_hash, hashlib.sha256(subidos[0]).hexdigest())
        self.assertTrue(solicitud.consentimiento_aceptado)
        self.assertTrue(solicitud.consentimiento_fecha)
        self.assertTrue(solicitud.firma_contratista_hash)
        self.assertEqual(solicitud.log_contratista_id.signature_partner_id.email, 'firmante@example.com')
        self.download.return_value = subidos[0]
        self.assertTrue(self.service.verificar_integridad(solicitud))
        self.download.return_value = self.pdf
        self.assertFalse(self.service.verificar_integridad(solicitud))

    def test_pdf_rotado_y_cropbox_conserva_paginas_y_imagen(self):
        if fitz is None:
            self.skipTest('PyMuPDF no instalado')
        with fitz.open(stream=self.pdf, filetype='pdf') as doc:
            doc[0].set_cropbox(fitz.Rect(10, 10, 290, 390))
            doc[0].set_rotation(90)
            original = doc.tobytes()
        firmado = self.service._estampar_firma_en_pdf(original, _png_pequeno())
        with fitz.open(stream=firmado, filetype='pdf') as doc:
            self.assertEqual(len(doc), 1)
            self.assertEqual(doc[0].rotation, 90)
            self.assertTrue(doc[0].get_images(full=True))

    def test_fallo_correo_de_copia_no_revierte_firma(self):
        solicitud = self._solicitar()
        with patch('odoo.addons.Matriz.models.drive.Drive.upload_to_drive', return_value=True), \
             patch.object(type(solicitud), '_enviar_copia_firmada', side_effect=RuntimeError('correo fallido')):
            self.service.procesar_firma(solicitud, self.imagen, True)
        self.assertEqual(solicitud.estado, 'firmado')
        self.assertTrue(solicitud.documento_hash)
        self.assertTrue(solicitud.log_contratista_id)

    def test_api_servicio_no_expuesta_por_rpc(self):
        for nombre in ('solicitar_firma', 'reenviar_solicitud', 'revocar_solicitud',
                       'validar_acceso_publico', 'obtener_pdf_preview', 'procesar_firma',
                       'verificar_integridad', 'expirar_solicitudes'):
            self.assertTrue(getattr(getattr(self.service, nombre), '_api_private', False), nombre)

    def test_imagen_extrema_conserva_proporcion_y_limites(self):
        if fitz is None:
            self.skipTest('PyMuPDF no instalado')
        firmado = self.service._estampar_firma_en_pdf(self.pdf, _png_pequeno())
        with fitz.open(stream=firmado, filetype='pdf') as doc:
            pagina = doc[0]
            imagen = pagina.get_images(full=True)[0]
            rect = pagina.get_image_rects(imagen[0])[0]
            self.assertTrue(pagina.rect.contains(rect))
            self.assertAlmostEqual(rect.width / rect.height, 2.0, places=2)

    def test_canvas_normalizado_conserva_proporcion_y_margenes(self):
        if fitz is None:
            self.skipTest('PyMuPDF no instalado')
        imagen = Image.new('RGBA', (600, 200))
        ImageDraw.Draw(imagen).rectangle((180, 60, 420, 140), outline='black', width=2)
        buf = BytesIO()
        imagen.save(buf, format='PNG')
        for fallback in (False, True):
            for rotation in (0, 90, 180, 270):
                with self.subTest(fallback=fallback, rotation=rotation):
                    with fitz.open(stream=self.pdf, filetype='pdf') as doc:
                        doc[0].set_cropbox(fitz.Rect(10, 10, 290, 390))
                        doc[0].set_rotation(rotation)
                        original = doc.tobytes()
                    with patch('odoo.addons.Matriz.models.drive_firma_service.fitz',
                               None if fallback else fitz):
                        firmado = self.service._estampar_firma_en_pdf(original, buf.getvalue())
                    with fitz.open(stream=firmado, filetype='pdf') as doc:
                        self.assertEqual(len(doc), 1)
                        pagina = doc[0]
                        self.assertEqual(pagina.rotation, rotation)
                        self.assertEqual(pagina.cropbox, fitz.Rect(10, 10, 290, 390))
                        self.assertNotIn('Firmado electrónicamente', pagina.get_text())
                        info = pagina.get_images()[0]
                        self.assertEqual(info[2:4], (600, 200))
                        rect = pagina.get_image_rects(info[0])[0] * pagina.rotation_matrix
                        self.assertTrue(pagina.rect.contains(rect))
                        self.assertAlmostEqual(rect.width / rect.height, 3, places=2)
                        self.assertAlmostEqual(rect.width, 55 * 72 / 25.4, places=2)

    def test_firma_alta_no_invade_texto_debajo_del_area_canonica(self):
        if fitz is None:
            self.skipTest('PyMuPDF no instalado')
        for fallback in (False, True):
            for dimensiones in ((180, 180), (100, 300), (300, 100)):
                with self.subTest(fallback=fallback, dimensiones=dimensiones):
                    firma = Image.new('RGBA', dimensiones)
                    ImageDraw.Draw(firma).rectangle(
                        (0, 0, dimensiones[0] - 1, dimensiones[1] - 1),
                        outline='black', width=2,
                    )
                    # Salida móvil con espacio transparente a los lados.
                    lienzo = Image.new('RGBA', (600, 600))
                    lienzo.paste(firma, (150, 150))
                    png = BytesIO()
                    lienzo.save(png, format='PNG')
                    with fitz.open() as original:
                        page = original.new_page(width=300, height=400)
                        page.insert_text((30, 100), 'Por la parte CONTRATISTA')
                        page.insert_text((30, 200), '{{FIRMA_CONTRATISTA}}')
                        base_firma = page.search_for('{{FIRMA_CONTRATISTA}}')[0].y1
                        page.insert_text((30, 235), 'Nombre del contratista')
                        pdf = original.tobytes()
                    with patch('odoo.addons.Matriz.models.drive_firma_service.fitz',
                               None if fallback else fitz):
                        resultado = self.service._estampar_firma_en_pdf(pdf, png.getvalue())
                    with fitz.open(stream=resultado, filetype='pdf') as doc:
                        page = doc[0]
                        rect = page.get_image_rects(page.get_images()[0][0])[0]
                        if fallback:
                            self.assertLessEqual(rect.height, 55 * 72 / 25.4 / 3 + 0.001)
                        else:
                            self.assertAlmostEqual(rect.y1, base_firma, places=3)
                            self.assertFalse(rect.intersects(page.search_for('Por la parte CONTRATISTA')[0]))
                            if dimensiones[0] <= dimensiones[1]:
                                self.assertGreater(rect.height, 70)
                        self.assertLessEqual(rect.width, 55 * 72 / 25.4 + 0.001)
                        self.assertAlmostEqual(rect.width / rect.height,
                                               1, places=3)
                        self.assertFalse(rect.intersects(page.search_for('Nombre del contratista')[0]))
                        self.assertNotIn('Firmado electrónicamente', page.get_text())

    def test_imagen_totalmente_transparente_rechazada(self):
        buf = BytesIO()
        Image.new('RGBA', (600, 200)).save(buf, format='PNG')
        with self.assertRaises(UserError):
            self.service._estampar_firma_en_pdf(self.pdf, buf.getvalue())

    def test_dos_transacciones_con_snapshot_anterior_no_dejan_dos_pendientes(self):
        """Dos conexiones reales reproducen el snapshot obsoleto de REPEATABLE READ.

        El fixture se confirma fuera del cursor del TransactionCase y se elimina
        explícitamente. No se usa Drive ni correo real en ninguna transacción.
        """
        from psycopg2.errors import SerializationFailure
        from odoo import api, SUPERUSER_ID, sql_db

        conexion = sql_db.db_connect(self.env.cr.dbname)
        documento_id = None
        with patch.object(type(self.env['drive.firma.solicitud']), 'enviar_correo_firma'), \
             patch.object(type(self.documento), 'message_post'):
            try:
                with conexion.cursor() as fixture_cr:
                    fixture_env = api.Environment(fixture_cr, SUPERUSER_ID, {})
                    documento_id = fixture_env['drive'].create({
                        'file_name': 'prueba-concurrencia-firma.pdf',
                        'file_id': 'fixture-concurrencia-no-remoto',
                    }).id
                    fixture_cr.commit()
                with conexion.cursor() as primero_cr, conexion.cursor() as segundo_cr:
                    primero_env = api.Environment(primero_cr, SUPERUSER_ID, {})
                    segundo_env = api.Environment(segundo_cr, SUPERUSER_ID, {})
                    # Fijar el snapshot del segundo antes de confirmar el primero.
                    segundo_cr.execute('SELECT id FROM drive WHERE id = %s', (documento_id,))
                    self.assertEqual(segundo_cr.fetchone()[0], documento_id)
                    primero_env['drive.firma.service'].solicitar_firma(
                        primero_env['drive'].browse(documento_id), 'uno@example.com', 'Uno')
                    primero_cr.commit()
                    with self.assertRaises(SerializationFailure):
                        segundo_env['drive.firma.service'].solicitar_firma(
                            segundo_env['drive'].browse(documento_id), 'dos@example.com', 'Dos')
                    segundo_cr.rollback()
                # Equivalente al retry HTTP de Odoo: cursor y snapshot nuevos.
                with conexion.cursor() as retry_cr:
                    retry_env = api.Environment(retry_cr, SUPERUSER_ID, {})
                    retry_env['drive.firma.service'].solicitar_firma(
                        retry_env['drive'].browse(documento_id), 'dos@example.com', 'Dos')
                    solicitudes = retry_env['drive.firma.solicitud'].search([
                        ('drive_id', '=', documento_id)])
                    self.assertEqual(len(solicitudes.filtered(lambda s: s.estado == 'pendiente')), 1)
                    self.assertEqual(len(solicitudes.filtered(lambda s: s.estado == 'cancelado')), 1)
                    retry_cr.commit()
            finally:
                if documento_id:
                    with conexion.cursor() as cleanup_cr:
                        cleanup_env = api.Environment(cleanup_cr, SUPERUSER_ID, {})
                        cleanup_env['drive.firma.solicitud'].search([
                            ('drive_id', '=', documento_id)]).unlink()
                        documento = cleanup_env['drive'].browse(documento_id).exists()
                        if documento:
                            documento.write({'file_id': False, 'drive_file_id': False})
                            documento.unlink()
                        cleanup_cr.commit()

    def test_limites_pdf_imagen_y_pixeles(self):
        from io import BytesIO
        from PIL import Image

        self.assertEqual(self.service._MAX_PDF_BYTES, 25 * 1024 * 1024)
        self.assertEqual(self.service._MAX_IMAGEN_BYTES, 5 * 1024 * 1024)
        with self.assertRaises(UserError):
            self.service._validar_pdf(b'X' * (25 * 1024 * 1024 + 1))
        with self.assertRaises(UserError):
            self.service._validar_imagen(base64.b64encode(b'X' * (5 * 1024 * 1024 + 1)))
        buffer = BytesIO()
        Image.new('1', (4001, 4000)).save(buffer, format='PNG')
        with self.assertRaises(UserError):
            self.service._validar_imagen(base64.b64encode(buffer.getvalue()))

    def test_solicitud_sin_token_no_puede_firmar(self):
        solicitud = self._solicitar()
        solicitud.token = False
        self.download.reset_mock()
        with self.assertRaises(UserError):
            self.service.procesar_firma(solicitud, self.imagen, True)
        self.download.assert_not_called()
        self.assertEqual(solicitud.estado, 'pendiente')

    def test_subida_fallida_restaura_espejo_binario(self):
        solicitud = self._solicitar()
        original = base64.b64encode(self.pdf)
        self.documento.with_context(skip_drive_sync=True).write({'file_data': original})
        with patch('odoo.addons.Matriz.models.drive.Drive.upload_to_drive', return_value=False):
            with self.assertRaises(UserError):
                self.service.procesar_firma(solicitud, self.imagen, True)
        self.assertEqual(self.documento.file_data, original)
        self.assertFalse(solicitud.log_contratista_id)

    def test_fallo_persistencia_compensa_drive_con_pdf_original(self):
        solicitud = self._solicitar()
        subidos = []

        def upload(documento, *args, **kwargs):
            subidos.append(base64.b64decode(documento.file_data))
            return True

        with patch('odoo.addons.Matriz.models.drive.Drive.upload_to_drive', autospec=True,
                   side_effect=upload), \
             patch.object(type(solicitud), '_actualizar_firmante_en_log',
                          side_effect=UserError('Fallo de persistencia')), \
             patch.object(type(solicitud), '_enviar_copia_firmada') as copia:
            with self.assertRaises(UserError):
                self.service.procesar_firma(solicitud, self.imagen, True)
        self.assertEqual(len(subidos), 2)
        self.assertNotEqual(subidos[0], self.pdf)
        self.assertEqual(subidos[1], self.pdf)
        # El modelo drive puede limpiar el espejo binario local después de
        # una subida; la restauración que importa aquí es la segunda subida
        # remota, comprobada arriba.
        if self.documento.file_data:
            self.assertEqual(base64.b64decode(self.documento.file_data), self.pdf)
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertFalse(solicitud.documento_hash)
        self.assertFalse(solicitud.log_contratista_id)
        copia.assert_not_called()

    def test_descarga_limitada_detiene_stream_antes_de_completar(self):
        from unittest.mock import MagicMock

        chunks = []

        def construir_downloader(buffer, request, chunksize=None):
            self.assertEqual(chunksize, 10)
            downloader = MagicMock()

            def next_chunk():
                chunks.append(True)
                buffer.write(b'123456')
                return None, len(chunks) >= 5

            downloader.next_chunk.side_effect = next_chunk
            return downloader

        with patch('odoo.addons.Matriz.models.drive.Drive._drive_service_v3'), \
             patch('odoo.addons.Matriz.models.drive.MediaIoBaseDownload',
                   side_effect=construir_downloader):
            with self.assertRaises(UserError):
                self.real_download(self.documento, 'archivo-prueba', max_bytes=10)
        self.assertEqual(len(chunks), 2)

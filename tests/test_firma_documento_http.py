"""Flujo HTTP real con QWeb y ORM, sin correo ni Google Drive externos."""
import base64
import hashlib
from unittest.mock import patch

from lxml import html

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .test_drive_firma_solicitud import _pdf_una_pagina, _png_pequeno


@tagged('post_install', '-at_install')
class TestFirmaDocumentoHttp(HttpCase):

    def test_documento_generico_formulario_preview_firma_y_replay(self):
        pdf_original = _pdf_una_pagina()
        subidos = []

        def subir(documento, *args, **kwargs):
            subidos.append(base64.b64decode(documento.file_data))
            return True

        with patch.object(type(self.env['drive']), '_drive_download_bytes',
                          return_value=pdf_original), \
             patch.object(type(self.env['drive']), 'upload_to_drive', autospec=True,
                          side_effect=subir), \
             patch.object(type(self.env['drive.firma.service']), '_enviar_mail_firma'):
            documento = self.env['drive'].create({
                'file_name': 'documento-http-generico.pdf',
                'file_id': 'archivo-http-sintetico',
            })
            solicitud = self.env['drive.firma.service'].solicitar_firma(
                documento, 'firmante-http@example.com', 'Firmante HTTP genérico',
            )
            self.assertFalse(solicitud.contratacion_fuv_id)
            self.env.flush_all()
            ruta = f'/public/firma_documento/{solicitud.id}/{solicitud.token}'
            respuesta = self.url_open(f'{ruta}?db={self.env.cr.dbname}')
            self.assertEqual(respuesta.status_code, 200)
            self.assertIn('Firmante HTTP genérico', respuesta.text)
            pagina = html.fromstring(respuesta.content)
            formulario = pagina.get_element_by_id('firma_form')
            datos = {
                elemento.name: elemento.value
                for elemento in formulario.xpath('.//input[@type="hidden"]')
            }
            self.assertEqual(datos['solicitud_id'], str(solicitud.id))
            self.assertEqual(datos['token'], solicitud.token)
            preview_url = pagina.xpath('//iframe/@src')[0]
            self.assertEqual(preview_url, ruta + '/documento.pdf')

            preview = self.url_open(preview_url)
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.headers['Content-Type'], 'application/pdf')
            self.assertEqual(preview.headers['Cache-Control'], 'no-store')
            self.assertEqual(preview.content, pdf_original)

            datos.update({
                'consentimiento': 'on',
                'firma_base64': 'data:image/png;base64,' + base64.b64encode(_png_pequeno()).decode(),
            })
            firmada = self.url_open(formulario.action, data=datos)
            self.assertEqual(firmada.status_code, 200)
            self.assertIn('Documento firmado exitosamente', firmada.text)
            solicitud.invalidate_recordset()
            self.assertEqual(solicitud.estado, 'firmado')
            self.assertEqual(len(subidos), 1)
            self.assertEqual(solicitud.documento_hash, hashlib.sha256(subidos[0]).hexdigest())
            self.assertEqual(solicitud.log_contratista_id.signature_partner_id.email,
                             'firmante-http@example.com')

            replay = self.url_open(formulario.action, data=datos)
            self.assertEqual(replay.status_code, 200)
            self.assertIn('ya fue firmado anteriormente', replay.text)
            self.assertEqual(len(subidos), 1)
            self.assertEqual(self.url_open(preview_url).status_code, 404)
            terminada = self.url_open(ruta)
            self.assertIn('ya fue firmado anteriormente', terminada.text)

    def test_formulario_movil_normaliza_y_procesa_la_firma(self):
        """Requiere Chromium y websocket-client, como browser_js de Odoo."""
        from io import BytesIO
        from PIL import Image

        service = self.env['drive.firma.service']
        procesar = type(service).procesar_firma
        recibidas = []

        def registrar(servicio, solicitud, imagen, consentimiento):
            recibidas.append(base64.b64decode(imagen))
            return procesar(servicio, solicitud, imagen, consentimiento)

        with patch.object(type(self.env['drive']), '_drive_download_bytes',
                          return_value=_pdf_una_pagina()), \
             patch.object(type(self.env['drive']), 'upload_to_drive', return_value=True), \
             patch.object(type(service), '_enviar_mail_firma'), \
             patch.object(type(service), 'procesar_firma', autospec=True, side_effect=registrar):
            for ancho in (900, 390):
                documento = self.env['drive'].create({
                    'file_name': 'firma-navegador.pdf', 'file_id': 'fixture-navegador',
                })
                solicitud = service.solicitar_firma(
                    documento, 'browser@example.com', 'Firmante navegador',
                )
                self.env.flush_all()
                ruta = f'/public/firma_documento/{solicitud.id}/{solicitud.token}'
                # El iframe fija el viewport antes de ejecutar el script del formulario.
                self.browser_js(ruta, """
                    (async function () {
                        const frame = document.createElement('iframe');
                        frame.style.width = '%dpx';
                        frame.style.height = '1000px';
                        const loaded = new Promise(resolve => frame.onload = resolve);
                        frame.src = window.location.href;
                        document.body.appendChild(frame);
                        await loaded;
                        const win = frame.contentWindow, doc = frame.contentDocument;
                        const deadline = Date.now() + 30000;
                        while (doc.body.classList.contains('o_lazy_js_waiting')) {
                            if (Date.now() > deadline) { throw new Error('No terminaron de cargar los assets'); }
                            await new Promise(resolve => setTimeout(resolve, 50));
                        }
                        const canvas = doc.getElementById('firma_canvas');
                        const mobile = win.innerWidth < 768;
                        if (mobile !== %s) { throw new Error('Viewport incorrecto'); }
                        if (mobile && !canvas.style.transform.includes('rotate(-90deg)')) {
                            throw new Error('Se perdió la rotación visual móvil');
                        }
                        canvas.style.border = '0';
                        // Los eventos sintéticos no tienen un puntero nativo capturable.
                        canvas.setPointerCapture = function () {};
                        const rect = canvas.getBoundingClientRect();
                        function pointer(type, x, y) {
                            x = x / 600 * canvas.offsetWidth;
                            y = y / 200 * canvas.offsetHeight;
                            const clientX = mobile ? rect.left + y : rect.left + x;
                            const clientY = mobile ? rect.bottom - x : rect.top + y;
                            canvas.dispatchEvent(new win.PointerEvent(type, {
                                clientX, clientY, pointerId: 1, pointerType: mobile ? 'touch' : 'mouse'
                            }));
                        }
                        pointer('pointerdown', 40, 80);
                        pointer('pointermove', 100, 20);
                        pointer('pointermove', 160, 70);
                        pointer('pointermove', 220, 40);
                        pointer('pointerup', 220, 40);
                        const check = doc.getElementById('consentimiento_check');
                        check.checked = true;
                        check.dispatchEvent(new win.Event('change'));
                        if (doc.getElementById('btn_firmar').disabled) {
                            throw new Error('No se habilitó la firma');
                        }
                        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                        if (!pixels.some((value, index) => index %% 4 === 3 && value > 0)) {
                            throw new Error('Los eventos no dibujaron tinta');
                        }
                        const form = doc.getElementById('firma_form');
                        form.addEventListener('submit', event => event.preventDefault());
                        form.dispatchEvent(new win.Event('submit', {cancelable: true}));
                        const data = new win.FormData(form);
                        const png = new win.Image();
                        const encoded = data.get('firma_base64');
                        if (!encoded || !encoded.startsWith('data:image/png;base64,')) {
                            throw new Error('Exportación vacía: canvas=' + canvas.width + 'x' + canvas.height
                                + ', rect=' + rect.width + 'x' + rect.height
                                + ', hiddenLength=' + doc.getElementById('firma_base64_input').value.length
                                + ', disabled=' + doc.getElementById('firma_base64_input').disabled
                                + ', fields=' + Array.from(data.keys()).join(','));
                        }
                        png.src = encoded;
                        await png.decode();
                        if (png.width !== 600 || png.height !== 200) {
                            throw new Error('El formulario no exportó la proporción canónica');
                        }
                        const response = await win.fetch(form.action, {method: 'POST', body: data});
                        if (!(await response.text()).includes('Documento firmado exitosamente')) {
                            throw new Error('Falló el flujo HTTP de firma');
                        }
                        console.log('test successful');
                    })().catch(error => console.error(error));
                """ % (ancho, 'true' if ancho < 768 else 'false'), timeout=90)
                solicitud.invalidate_recordset()
                self.assertEqual(solicitud.estado, 'firmado')
            self.assertEqual(len(recibidas), 2)
            firmas = [Image.open(BytesIO(data)).convert('RGBA') for data in recibidas]
            for firma in firmas:
                self.assertEqual(firma.size, (600, 200))
                self.assertEqual(firma.getpixel((0, 0))[3], 0)
            # Misma ubicación relativa del trazo, pese a diferentes viewports.
            for actual, esperado in zip(firmas[0].getchannel('A').getbbox(),
                                        firmas[1].getchannel('A').getbbox()):
                self.assertAlmostEqual(actual, esperado, delta=4)

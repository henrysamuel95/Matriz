import base64
import binascii
import hashlib
import logging
import secrets
from datetime import timedelta
from io import BytesIO

from markupsafe import escape
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas
try:
    import fitz
except ImportError:
    fitz = None

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import email_normalize
from .drive import _get_firma_test_drive_folder_id

_logger = logging.getLogger(__name__)


class DriveFirmaService(models.AbstractModel):
    _name = "drive.firma.service"
    _description = "Servicio de firma electrónica pública de documentos Drive"

    _HORAS_VIGENCIA_DEFAULT = 72
    _MARCADOR_FIRMA_DEFAULT = "{{FIRMA_CONTRATISTA}}"
    _MAX_PDF_BYTES = 25 * 1024 * 1024
    _MAX_IMAGEN_BYTES = 5 * 1024 * 1024
    _MAX_IMAGEN_PIXELS = 16_000_000
    _CORREO_FALLBACK = "no-reply@fundacionunivalle.com"

    def _estampar_firma_en_pdf(self, pdf_bytes, imagen_png_bytes, marcador=None, ancho_mm=55, margen_mm=15):
        """Estampa en coordenadas visuales y convierte a la página sin rotar.

        PyMuPDF busca y escribe en coordenadas locales sin rotar; ``rect``
        describe la página visible rotada. La conversión explícita conserva
        posición, proporción y visibilidad también con CropBox desplazada.
        """
        # Conservar el lienzo y sus márgenes: representan las coordenadas
        # relativas del dibujo en el formulario, normalizadas por el frontend.
        with Image.open(BytesIO(imagen_png_bytes)) as origen:
            firma = origen.convert('RGBA')
            limites = firma.getchannel('A').getbbox()
            if limites is None:
                raise UserError(_("Debe dibujar una firma antes de enviarla."))
            normalizada = BytesIO()
            firma.save(normalizada, format='PNG')
            imagen_png_bytes = normalizada.getvalue()
        img = ImageReader(BytesIO(imagen_png_bytes))
        iw, ih = img.getSize()
        if not iw or not ih:
            raise ValueError('La imagen de firma no tiene dimensiones válidas')
        # Ajustar el trazo a la caja canónica 600:200 en ambos ejes.
        # No ampliar el trazo por separado del lienzo que lo contiene.
        escala_firma = min(ancho_mm * mm / iw, ancho_mm * mm / (3 * ih))
        if fitz:
            with fitz.open(stream=pdf_bytes, filetype='pdf') as doc:
                destino = None
                try:
                    for pagina_idx in range(doc.page_count - 1, -1, -1):
                        coincidencias = doc[pagina_idx].search_for(marcador or self._MARCADOR_FIRMA_DEFAULT)
                        if coincidencias:
                            destino = (pagina_idx, coincidencias[-1])
                            break
                except Exception:
                    # Solo la búsqueda admite fallback; un fallo redactando,
                    # insertando o serializando debe impedir subir el resultado.
                    _logger.warning('No se pudo buscar el marcador de firma; se usará la última página')
                pagina = doc[destino[0] if destino else doc.page_count - 1]
                visible = pagina.rect
                reserva = min(10, visible.height / 4)
                escala = min(escala_firma, visible.width / iw,
                             (visible.height - reserva) / ih)
                ancho, alto = iw * escala, ih * escala
                if destino:
                    marcador_rect = destino[1]
                    punto = marcador_rect * pagina.rotation_matrix
                    # El marcador señala la base de la firma, no el inicio de
                    # un dibujo que deba crecer hacia el nombre del contratista.
                    # Usar el espacio libre superior sin invadir el texto previo.
                    ancho_max = min(ancho_mm * mm, visible.width)
                    x = min(max(punto.x0, 0), visible.width - ancho_max)
                    base = min(punto.y1, visible.height)
                    techo = 0
                    for palabra in pagina.get_text('words'):
                        texto_rect = fitz.Rect(palabra[:4]) * pagina.rotation_matrix
                        if (texto_rect.y1 <= punto.y0 and texto_rect.x1 > x
                                and texto_rect.x0 < x + ancho_max):
                            techo = max(techo, texto_rect.y1 + 2)
                    altura_disponible = max(punto.height, base - techo)
                    escala = min(ancho_max / iw, altura_disponible / ih)
                    ancho, alto = iw * escala, ih * escala
                    y = max(0, base - alto)
                    pagina.add_redact_annot(marcador_rect, fill=(1, 1, 1))
                    pagina.apply_redactions()
                else:
                    x = max(0, visible.width - ancho - margen_mm * mm)
                    y = max(0, visible.height - alto - max(reserva, margen_mm * mm))
                visual_rect = fitz.Rect(x, y, x + ancho, y + alto)
                pagina.insert_image(
                    visual_rect * pagina.derotation_matrix,
                    stream=imagen_png_bytes, keep_proportion=True,
                    rotate=pagina.rotation, overlay=True,
                )
                return doc.tobytes(garbage=4, deflate=True)

        # Compatibilidad sin PyMuPDF: matriz visual → coordenadas PDF para
        # que la esquina inferior derecha corresponda al CropBox visible.
        reader = PdfReader(BytesIO(pdf_bytes))
        if reader.is_encrypted:
            reader.decrypt('')
        page_target = reader.pages[-1]
        crop = page_target.cropbox
        left, bottom = float(crop.left), float(crop.bottom)
        width, height = float(crop.width), float(crop.height)
        rotation = int(page_target.get('/Rotate', 0)) % 360
        page_w, page_h = (height, width) if rotation in (90, 270) else (width, height)
        reserva = min(10, page_h / 4)
        escala = min(escala_firma, page_w / iw, (page_h - reserva) / ih)
        ancho_pt, alto_pt = iw * escala, ih * escala
        x = max(0, page_w - ancho_pt - margen_mm * mm)
        y = min(max(reserva, margen_mm * mm), page_h - alto_pt)
        overlay_buf = BytesIO()
        c = pdf_canvas.Canvas(overlay_buf, pagesize=(float(page_target.mediabox.right), float(page_target.mediabox.top)))
        matrices = {
            0: (1, 0, 0, 1, left, bottom),
            90: (0, 1, -1, 0, left + width, bottom),
            180: (-1, 0, 0, -1, left + width, bottom + height),
            270: (0, -1, 1, 0, left, bottom + height),
        }
        c.transform(*matrices[rotation])
        c.drawImage(img, x, y, width=ancho_pt, height=alto_pt, mask='auto')
        c.save()
        overlay_buf.seek(0)
        page_target.merge_page(PdfReader(overlay_buf).pages[0])
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        out = BytesIO()
        writer.write(out)
        return out.getvalue()

    def _mensaje_vigencia_enlace(self, solicitud):
        """Texto legible con la fecha/hora de vencimiento del token de esta
        solicitud, en la zona horaria del destinatario — para incluir en el
        correo de solicitud de firma."""
        solicitud.ensure_one()
        if not solicitud.token_expira:
            return ""
        expira_local = fields.Datetime.context_timestamp(solicitud, solicitud.token_expira)
        return f"Vence el {expira_local.strftime('%d/%m/%Y a las %H:%M')}."

    def _enviar_mail_firma(self, solicitud, subject, body_html, attachment_ids=None):
        """Helper compartido para los correos salientes de este modelo
        (solicitud inicial/reenvío y copia firmada) — evita repetir el
        mismo bloque de creación+envío de mail.mail en cada uno."""
        solicitud.ensure_one()
        solicitud.env["mail.mail"].sudo().create(
            {
                "subject": subject,
                "body_html": body_html,
                "email_to": solicitud.email_contratista,
                "email_from": solicitud.env.user.email or self._CORREO_FALLBACK,
                "attachment_ids": attachment_ids or [],
            }
        ).sudo().send()

    def _enviar_correo_firma(self, solicitud):
        """Envía (o reenvía) el correo con el link público de firma para
        esta solicitud. Compartido entre el primer envío y los reenvíos,
        para no duplicar la plantilla del correo."""
        solicitud.ensure_one()
        base_url = solicitud.env["ir.config_parameter"].sudo().get_param("web.base.url")
        public_url = f"{base_url}/public/firma_documento/{solicitud.id}/{solicitud.token}"
        logo_url = f"{base_url}/Matriz/static/src/img/LogoFUV.png"
        subject = f"Firma requerida - {solicitud.drive_id.file_name or solicitud.drive_id.name}"
        # Diseño de marca (colores extraídos de LogoFUV.png, el mismo logo ya
        # usado en contratos/órdenes de compra del módulo): navy #002d56,
        # rojo #e41e1f. Se usan tablas (no flexbox) para el ícono+texto del
        # aviso de seguridad porque Outlook de escritorio no soporta flex —
        # ver el diseño original en el Artifact de esta sesión.
        body = f"""
        <div style="font-family: Arial, Helvetica, sans-serif; background: #eef1f5; padding: 24px 0;">
        <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,45,86,0.10);">
            <div style="height: 6px; background: #e41e1f; line-height: 6px; font-size: 0;">&nbsp;</div>
            <div style="padding: 32px 40px 20px 40px; text-align: center;">
                <img src="{escape(logo_url)}" alt="Fundación Univalle" style="height: 52px; width: auto;">
            </div>
            <div style="padding: 0 40px 4px 40px;">
                <h2 style="margin: 0 0 4px 0; font-size: 22px; line-height: 1.3; color: #002d56;">Firma electrónica requerida</h2>
                <p style="margin: 0 0 22px 0; font-size: 13px; color: #8a94a3;">Fundación Universidad del Valle</p>
                <p style="margin: 0 0 8px 0; font-size: 15px; line-height: 1.6; color: #33383f;">Estimado/a {escape(solicitud.nombre_contratista or "")},</p>
                <p style="margin: 0 0 26px 0; font-size: 15px; line-height: 1.6; color: #33383f;">
                    Fundación Universidad del Valle solicita su firma electrónica en el documento
                    <strong style="color: #002d56;">{escape(solicitud.drive_id.file_name or solicitud.drive_id.name)}</strong>.
                </p>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin: 0 0 28px 0;">
                    <tr><td align="center">
                        <table role="presentation" cellpadding="0" cellspacing="0">
                            <tr><td style="background: #002d56; border-radius: 8px;">
                                <a href="{escape(public_url)}" style="display: inline-block; padding: 14px 40px; font-size: 15px; font-weight: bold; color: #ffffff; text-decoration: none;">Firmar documento</a>
                            </td></tr>
                        </table>
                    </td></tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background: #f4f6f9; border-left: 3px solid #002d56; border-radius: 0 6px 6px 0; margin: 0 0 8px 0;">
                    <tr>
                        <td style="padding: 14px 4px 14px 16px; width: 28px; vertical-align: top;">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#002d56" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <rect x="5" y="11" width="14" height="10" rx="2"></rect>
                                <path d="M8 11V7a4 4 0 0 1 8 0v4"></path>
                            </svg>
                        </td>
                        <td style="padding: 14px 16px 14px 8px; font-size: 13px; line-height: 1.6; color: #4a5568;">
                            Este enlace es <strong>personal y de un solo uso</strong> — no lo comparta con nadie más.<br>
                            {solicitud._mensaje_vigencia_enlace()}
                        </td>
                    </tr>
                </table>
            </div>
            <div style="border-top: 1px solid #eef1f5; padding: 20px 40px; background: #fafbfc;">
                <p style="margin: 0; font-size: 12px; color: #9aa7b8; line-height: 1.5;">Este es un mensaje automático, por favor no responder.</p>
            </div>
        </div>
        </div>
        """
        solicitud._enviar_mail_firma(subject, body)

    def _actualizar_firmante_en_log(self, solicitud):
        """El decorador seguimiento_firmas nunca setea signature_partner_id
        (cae en el default del campo, el usuario público anónimo). Esto
        hace que 'Firmado por' en signature.mixin muestre 'Public user' en
        vez del contratista real. Se corrige aparte, sin tocar el
        decorador compartido con los otros 5 modelos que lo usan."""
        solicitud.ensure_one()
        if not solicitud.log_contratista_id or not solicitud.email_contratista:
            return
        solicitud.log_contratista_id.sudo().write(
            {
                "signature_partner_id": solicitud._resolver_partner_contratista().id,
            }
        )

    def _resolver_partner_contratista(self, solicitud):
        """Busca (o crea) un res.partner 'visual' para el contratista, solo
        para que la trazabilidad de firmas lo muestre correctamente —
        mismo espíritu que login._get_portal_chatter_partner()
        (cuenta_de_cobro_extra.py), pero sin depender de login, ya que
        este flujo firma por token de un solo uso, no por sesión de
        portal."""
        solicitud.ensure_one()
        Partner = solicitud.env["res.partner"].sudo()
        partner = Partner.search([("email", "=", solicitud.email_contratista)], limit=1)
        if not partner:
            partner = Partner.create(
                {
                    "name": solicitud.nombre_contratista or solicitud.email_contratista,
                    "email": solicitud.email_contratista,
                    "company_type": "person",
                    "comment": "Contacto visual creado para trazabilidad de firma pública de documento (drive.firma.solicitud).",
                }
            )
        return partner

    def _enviar_copia_firmada(self, solicitud, pdf_bytes):
        """Envía por correo una copia del PDF ya firmado al contratista.
        No debe bloquear el flujo de firma si el envío falla — el
        documento ya quedó firmado y subido a Drive en ese punto."""
        solicitud.ensure_one()
        if not solicitud.email_contratista:
            return

        nombre_archivo = solicitud.drive_id.file_name or solicitud.drive_id.name or "documento"
        if not nombre_archivo.lower().endswith(".pdf"):
            nombre_archivo += ".pdf"

        base_url = solicitud.env["ir.config_parameter"].sudo().get_param("web.base.url")
        logo_url = f"{base_url}/Matriz/static/src/img/LogoFUV.png"

        try:
            with self.env.cr.savepoint():
                solicitud._enviar_mail_firma(
                    f"Copia firmada - {solicitud.drive_id.file_name or solicitud.drive_id.name}",
                    f"""
                    <div style="font-family: Arial, Helvetica, sans-serif; background: #eef1f5; padding: 24px 0;">
                    <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,45,86,0.10);">
                        <div style="height: 6px; background: #002d56; line-height: 6px; font-size: 0;">&nbsp;</div>
                        <div style="padding: 32px 40px 20px 40px; text-align: center;">
                            <img src="{escape(logo_url)}" alt="Fundación Univalle" style="height: 52px; width: auto;">
                        </div>
                        <div style="padding: 0 40px 4px 40px;">
                            <h2 style="margin: 0 0 4px 0; font-size: 22px; line-height: 1.3; color: #002d56;">Documento firmado electrónicamente</h2>
                            <p style="margin: 0 0 22px 0; font-size: 13px; color: #8a94a3;">Fundación Universidad del Valle</p>
                            <p style="margin: 0 0 8px 0; font-size: 15px; line-height: 1.6; color: #33383f;">Estimado/a {escape(solicitud.nombre_contratista or "")},</p>
                            <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #33383f;">
                                Adjuntamos la copia del documento
                                <strong style="color: #002d56;">{escape(solicitud.drive_id.file_name or solicitud.drive_id.name)}</strong>
                                que acaba de firmar electrónicamente.
                            </p>
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background: #f4f6f9; border-radius: 8px; margin: 0 0 20px 0;">
                                <tr>
                                    <td style="padding: 16px 0 16px 20px; width: 52px; vertical-align: middle;">
                                        <table role="presentation" cellpadding="0" cellspacing="0">
                                            <tr><td align="center" valign="middle" style="width: 36px; height: 36px; background: #002d56; border-radius: 50%;">
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                                                    <polyline points="20 6 9 17 4 12"></polyline>
                                                </svg>
                                            </td></tr>
                                        </table>
                                    </td>
                                    <td style="padding: 16px 20px 16px 12px; font-size: 14px; line-height: 1.5; color: #33383f;">
                                        Firma con validez legal conforme a la <strong>Ley 527 de 1999</strong>.
                                    </td>
                                </tr>
                            </table>
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border: 1px solid #e6e9ee; border-radius: 8px; margin: 0 0 28px 0;">
                                <tr>
                                    <td style="padding: 12px 8px 12px 16px; width: 24px; vertical-align: middle;">
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#8a94a3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
                                        </svg>
                                    </td>
                                    <td style="padding: 12px 16px 12px 8px; font-size: 13px; color: #4a5568;">{escape(nombre_archivo)}</td>
                                </tr>
                            </table>
                        </div>
                        <div style="border-top: 1px solid #eef1f5; padding: 20px 40px; background: #fafbfc;">
                            <p style="margin: 0; font-size: 12px; color: #9aa7b8; line-height: 1.5;">Este es un mensaje automático, por favor no responder.</p>
                        </div>
                    </div>
                    </div>
                    """,
                    attachment_ids=[
                        (
                            0,
                            0,
                            {
                                "name": nombre_archivo,
                                "datas": base64.b64encode(pdf_bytes),
                                "mimetype": "application/pdf",
                            },
                        )
                    ],
                )
        except Exception:
            _logger.error(
                "No se pudo enviar la copia firmada al contratista (solicitud %s)",
                solicitud.id,
            )

    def _validar_registro(self, record, modelo):
        if (not isinstance(record, models.BaseModel) or record._name != modelo
                or len(record) != 1 or not record.exists()):
            raise UserError(_("Se requiere exactamente un registro existente de %s.") % modelo)
        record.check_access_rights('read')
        record.check_access_rule('read')
        return record

    def _bloquear_documento(self, documento):
        documento.flush_recordset()
        # También crea una nueva versión de fila: en REPEATABLE READ una
        # transacción con snapshot anterior debe reintentarse, no crear otra activa.
        self.env.cr.execute('UPDATE drive SET id = id WHERE id = %s RETURNING id', (documento.id,))
        if not self.env.cr.fetchone():
            raise UserError(_("El documento ya no existe."))
        documento.invalidate_recordset()

    def _bloquear_solicitud(self, solicitud):
        self._validar_registro(solicitud, 'drive.firma.solicitud')
        self._bloquear_documento(solicitud.drive_id)
        solicitud.flush_recordset()
        self.env.cr.execute(
            'SELECT id FROM drive_firma_solicitud WHERE id = %s FOR UPDATE', (solicitud.id,)
        )
        if not self.env.cr.fetchone():
            raise UserError(_("La solicitud ya no existe."))
        solicitud.invalidate_recordset()

    def _exigir_documento_sin_firma(self, documento):
        """Consultar bajo el bloqueo del documento, incluso para llamadas directas."""
        if self.env['drive.firma.solicitud'].sudo().search_count([
                ('drive_id', '=', documento.id), ('estado', '=', 'firmado')]):
            raise UserError(_("Este documento ya fue firmado. No se puede enviar nuevamente para firma."))

    def _exigir_pendiente(self, solicitud, vigente=True):
        if solicitud.estado != 'pendiente':
            raise UserError(_("La solicitud de firma ya no está pendiente."))
        if vigente and (not solicitud.token or not solicitud.token_expira
                        or solicitud.token_expira <= fields.Datetime.now()):
            raise UserError(_("La solicitud de firma ha vencido."))

    def _vencimiento(self, horas):
        if isinstance(horas, bool) or not isinstance(horas, (int, float)) or not 0 < horas <= 8760:
            raise UserError(_("La vigencia debe estar entre 0 y 8760 horas."))
        return fields.Datetime.now() + timedelta(hours=horas)

    def _validar_pdf(self, contenido):
        if not isinstance(contenido, bytes) or not contenido or len(contenido) > self._MAX_PDF_BYTES:
            raise UserError(_("El PDF está vacío o supera el límite de 25 MiB."))
        try:
            reader = PdfReader(BytesIO(contenido))
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('encrypted')
            if not reader.pages:
                raise ValueError('empty')
            for page in reader.pages:
                if float(page.mediabox.width) <= 0 or float(page.mediabox.height) <= 0:
                    raise ValueError('page dimensions')
        except Exception:
            raise UserError(_("El archivo no es un PDF legible.")) from None
        return contenido

    def _descargar_pdf(self, documento):
        file_id = documento.file_id or documento.drive_file_id
        if not file_id:
            raise UserError(_("El documento no tiene un archivo en Google Drive."))
        return self._validar_pdf(documento._drive_download_bytes(file_id, max_bytes=self._MAX_PDF_BYTES))

    def _validar_imagen(self, imagen_b64):
        if not isinstance(imagen_b64, (str, bytes)) or len(imagen_b64) > ((self._MAX_IMAGEN_BYTES + 2) // 3) * 4:
            raise UserError(_("La imagen de firma supera el límite de 5 MiB."))
        try:
            contenido = base64.b64decode(imagen_b64, validate=True)
            if not contenido or len(contenido) > self._MAX_IMAGEN_BYTES:
                raise ValueError('image size')
            with Image.open(BytesIO(contenido)) as imagen:
                if imagen.format not in ('PNG', 'JPEG') or not 0 < imagen.width * imagen.height <= self._MAX_IMAGEN_PIXELS:
                    raise ValueError('image format or dimensions')
                imagen.verify()
            with Image.open(BytesIO(contenido)) as imagen:
                imagen.load()
        except (ValueError, TypeError, binascii.Error, OSError, Image.DecompressionBombError):
            raise UserError(_("La firma debe ser una imagen PNG o JPEG válida.")) from None
        return contenido

    @api.model
    def solicitar_firma(self, drive_record, email_firmante, nombre_firmante,
                        horas_vigencia=72, enviado_por=None):
        documento = self._validar_registro(drive_record, 'drive')
        documento.check_access_rights('write')
        documento.check_access_rule('write')
        email = email_normalize(email_firmante.strip()) if isinstance(email_firmante, str) else False
        if not email:
            raise UserError(_("Debe indicar un correo electrónico válido para el firmante."))
        if not isinstance(nombre_firmante, str) or not nombre_firmante.strip():
            raise UserError(_("Debe indicar el nombre del firmante."))
        vencimiento = self._vencimiento(horas_vigencia)
        emisor = self._validar_registro(enviado_por or self.env.user, 'res.users')
        with self.env.cr.savepoint():
            self._bloquear_documento(documento)
            self._exigir_documento_sin_firma(documento)
            self._descargar_pdf(documento)
            Solicitud = self.env['drive.firma.solicitud'].sudo()
            Solicitud.search([('drive_id', '=', documento.id), ('estado', '=', 'pendiente')]).write({'estado': 'cancelado'})
            solicitud = Solicitud.create({
                'drive_id': documento.id, 'email_contratista': email,
                'nombre_contratista': nombre_firmante.strip(),
                'token': secrets.token_urlsafe(32), 'token_expira': vencimiento,
                'fecha_envio': fields.Datetime.now(), 'enviado_por_id': emisor.id,
            })
            solicitud.enviar_correo_firma()
            documento.message_post(body=f'Solicitud de firma enviada a {email}.')
        return solicitud

    @api.model
    def reenviar_solicitud(self, solicitud, horas_vigencia=72):
        vencimiento = self._vencimiento(horas_vigencia)
        with self.env.cr.savepoint():
            self._bloquear_solicitud(solicitud)
            self._exigir_documento_sin_firma(solicitud.drive_id)
            self._exigir_pendiente(solicitud, vigente=False)
            solicitud.write({'token_expira': vencimiento})
            solicitud.enviar_correo_firma()
            solicitud.message_post(body=f'Correo de firma reenviado a {solicitud.email_contratista}.')
        return solicitud

    @api.model
    def revocar_solicitud(self, solicitud):
        self._bloquear_solicitud(solicitud)
        self._exigir_pendiente(solicitud, vigente=False)
        solicitud.write({'estado': 'cancelado'})
        solicitud.message_post(body='Solicitud de firma revocada manualmente.')
        return True

    @api.model
    def validar_acceso_publico(self, solicitud_id, token, bloquear=False, exigir_pendiente=True):
        Solicitud = self.env['drive.firma.solicitud'].sudo()
        vacio = Solicitud.browse()
        if (not isinstance(solicitud_id, int) or isinstance(solicitud_id, bool)
                or not 0 < solicitud_id <= 2147483647 or not isinstance(token, str) or not token):
            return vacio
        solicitud = Solicitud.browse(solicitud_id).exists()
        if not solicitud or not secrets.compare_digest((solicitud.token or '').encode(), token.encode()):
            return vacio
        vencida = solicitud.estado == 'pendiente' and (
            not solicitud.token_expira or solicitud.token_expira <= fields.Datetime.now())
        if bloquear or vencida:
            self._bloquear_solicitud(solicitud)
            if not secrets.compare_digest((solicitud.token or '').encode(), token.encode()):
                return vacio
            if solicitud.estado == 'pendiente' and (
                    not solicitud.token_expira or solicitud.token_expira <= fields.Datetime.now()):
                solicitud.write({'estado': 'expirado'})
        if exigir_pendiente and solicitud.estado != 'pendiente':
            return vacio
        return solicitud

    @api.model
    def obtener_pdf_preview(self, solicitud):
        self._bloquear_solicitud(solicitud)
        self._exigir_pendiente(solicitud)
        return self._descargar_pdf(solicitud.drive_id)

    @api.model
    def procesar_firma(self, solicitud, imagen_b64, consentimiento_aceptado):
        self._bloquear_solicitud(solicitud)
        self._exigir_pendiente(solicitud)
        if consentimiento_aceptado is not True:
            raise UserError(_("Debe aceptar el consentimiento informado antes de firmar."))
        imagen = self._validar_imagen(imagen_b64)
        documento = solicitud.drive_id
        original = self._descargar_pdf(documento)
        firmado = self._validar_pdf(self._estampar_firma_en_pdf(original, imagen))
        carpeta = _get_firma_test_drive_folder_id(self.env)
        subido = False
        try:
            with self.env.cr.savepoint():
                documento.with_context(skip_drive_sync=True).write({'file_data': base64.b64encode(firmado)})
                if not documento.upload_to_drive(new_folder_id=carpeta or None):
                    raise UserError(_("No se pudo subir el documento firmado a Google Drive. Tu firma NO fue registrada; intenta nuevamente."))
                subido = True
                documento.message_post(body='✅ Documento firmado electrónicamente por el contratista.')
                solicitud.write({
                    'firma_imagen': imagen_b64, 'firmado_contratista': True, 'estado': 'firmado',
                    'documento_hash': hashlib.sha256(firmado).hexdigest(),
                    'consentimiento_aceptado': True, 'consentimiento_fecha': fields.Datetime.now(),
                })
                solicitud._actualizar_firmante_en_log()
        except Exception:
            if subido:
                _logger.error('Falló persistencia posterior a subida de firma; se intentará restaurar solicitud %s', solicitud.id)
                try:
                    with self.env.cr.savepoint():
                        documento.with_context(skip_drive_sync=True).write({'file_data': base64.b64encode(original)})
                        if not documento.upload_to_drive(new_folder_id=carpeta or None):
                            raise UserError('Restauración remota fallida')
                        # upload_to_drive limpia el espejo binario local como
                        # parte de su comportamiento normal. En esta ruta de
                        # compensación conservarlo permite reintentar y deja
                        # el estado local alineado con el PDF restaurado.
                        documento.with_context(
                            skip_drive_sync=True, skip_integrity=True,
                        ).write({
                            'file_data': base64.b64encode(original),
                            'file_data_visible': True,
                        })
                except Exception:
                    _logger.error('No se pudo restaurar documento remoto de solicitud %s; requiere conciliación', solicitud.id)
            raise
        try:
            with self.env.cr.savepoint():
                solicitud._enviar_copia_firmada(firmado)
        except Exception:
            _logger.error('No se pudo enviar copia firmada de solicitud %s', solicitud.id)
        return solicitud

    @api.model
    def verificar_integridad(self, solicitud):
        self._validar_registro(solicitud, 'drive.firma.solicitud')
        if solicitud.estado != 'firmado' or not solicitud.documento_hash:
            raise UserError(_("Esta solicitud no tiene un documento firmado para verificar."))
        documento = solicitud.drive_id
        file_id = documento.file_id or documento.drive_file_id
        if not file_id:
            raise UserError(_("El documento no tiene un archivo en Google Drive."))
        contenido = documento._drive_download_bytes(file_id, max_bytes=self._MAX_PDF_BYTES)
        return hashlib.sha256(contenido).hexdigest() == solicitud.documento_hash

    @api.model
    def expirar_solicitudes(self):
        solicitudes = self.env['drive.firma.solicitud'].sudo().search([
            ('estado', '=', 'pendiente'), ('token_expira', '<=', fields.Datetime.now())
        ], order='drive_id, id')
        cantidad = 0
        for solicitud in solicitudes:
            self._bloquear_solicitud(solicitud)
            if solicitud.estado == 'pendiente' and solicitud.token_expira <= fields.Datetime.now():
                solicitud.write({'estado': 'expirado'})
                cantidad += 1
        return cantidad

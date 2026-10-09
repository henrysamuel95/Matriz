import secrets

from odoo import models, fields, api

from .decorador_firmas import seguimiento_firmas


class DriveFirmaSolicitud(models.Model):
    _name = "drive.firma.solicitud"
    _description = "Solicitud de firma electrónica pública de un documento de Drive"
    _inherit = ["mail.thread"]
    _order = "fecha_envio desc"

    # Sin este campo, display_name (y por lo tanto nombre_documento en el
    # log de signature.mixin, ver decorador_firmas.py) cae al fallback
    # técnico de Odoo "drive.firma.solicitud,<id>" — los otros 5 modelos
    # que usan el decorador sí tienen name/_rec_name propio.
    name = fields.Char(compute="_compute_name", store=True)

    drive_id = fields.Many2one(
        "drive", string="Documento", required=True, ondelete="cascade", index=True
    )
    contratacion_fuv_id = fields.Many2one(
        "contrataciones_fuv",
        string="Contratación",
        related="drive_id.contratacion_fuv_documentos_id",
        store=True,
        index=True,
    )

    # Snapshot al momento del envío: el link ya enviado queda ligado al
    # correo que realmente se usó, aunque luego cambie el correo del
    # contratista en su expediente.
    email_contratista = fields.Char(string="Correo del contratista")
    nombre_contratista = fields.Char(string="Nombre del contratista")

    token = fields.Char(readonly=True, copy=False, index=True)
    token_expira = fields.Datetime(readonly=True, copy=False)
    estado = fields.Selection(
        [
            ("pendiente", "Pendiente"),
            ("firmado", "Firmado"),
            ("expirado", "Expirado"),
            ("cancelado", "Cancelado"),
        ],
        default="pendiente",
        tracking=True,
        required=True,
    )

    fecha_envio = fields.Datetime(readonly=True)
    enviado_por_id = fields.Many2one("res.users", default=lambda self: self.env.user)

    # Slot único para el decorador de firmas (ver seguimiento_firmas en
    # decorador_firmas.py). El decorador siempre descarta la imagen dentro
    # de signature.mixin, así que la evidencia real dibujada se conserva
    # aparte en firma_imagen.
    firma_imagen = fields.Binary(string="Firma (imagen dibujada)")
    firmado_contratista = fields.Boolean(string="Firmado por el contratista")
    log_contratista_id = fields.Many2one("signature.mixin", string="Log de firma")
    firma_contratista_hash = fields.Char(readonly=True, copy=False)

    # Hash del CONTENIDO del PDF ya firmado (distinto de firma_contratista_hash,
    # que es el hash del EVENTO de firma que arma decorador_firmas.py — no
    # incluye los bytes del documento). Este sí permite, más adelante,
    # volver a descargar el archivo de Drive y comparar si cambió.
    documento_hash = fields.Char(
        string="SHA-256 del documento firmado", readonly=True, copy=False
    )
    consentimiento_aceptado = fields.Boolean(
        string="Consentimiento informado aceptado", readonly=True, copy=False
    )
    consentimiento_fecha = fields.Datetime(
        string="Fecha de consentimiento", readonly=True, copy=False
    )

    @api.depends("nombre_contratista", "email_contratista", "drive_id.file_name")
    def _compute_name(self):
        for rec in self:
            documento = rec.drive_id.file_name or rec.drive_id.name or "documento"
            firmante = rec.nombre_contratista or rec.email_contratista or "Contratista"
            rec.name = f"{firmante} - {documento}"

    @staticmethod
    def _generar_token():
        return secrets.token_urlsafe(32)

    # Dominio de fallback único para los correos de este flujo — antes cada
    # método lo repetía por su cuenta, con riesgo de que quedaran distintos
    # entre sí (pasó con drive.py:send_update_email, que usa otro dominio).
    _CORREO_FALLBACK = "no-reply@fundacionunivalle.com"

    def _mensaje_vigencia_enlace(self):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"]._mensaje_vigencia_enlace(self)

    def _enviar_mail_firma(self, subject, body_html, attachment_ids=None):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"]._enviar_mail_firma(
            self, subject, body_html, attachment_ids=attachment_ids
        )

    @api.model
    def enviar_correo_firma(self):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"]._enviar_correo_firma(self)

    def write(self, vals):
        return self._write_con_seguimiento_firmas(vals)

    @seguimiento_firmas(
        [
            {
                "name": "Contratista",
                "img": "firma_imagen",
                "check": "firmado_contratista",
                "log": "log_contratista_id",
                "hash": "firma_contratista_hash",
            }
        ]
    )
    def _write_con_seguimiento_firmas(self, vals):
        return super().write(vals)

    @api.model
    def procesar_firma(self, imagen_b64, consentimiento_aceptado=True):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"].procesar_firma(
            self, imagen_b64, consentimiento_aceptado
        )

    def action_verificar_integridad(self):
        """Vuelve a descargar el archivo actual de Drive y compara su hash
        contra el que se guardó al momento de firmar — así se puede saber
        si el documento cambió después de la firma."""
        self.ensure_one()
        integro = self.env["drive.firma.service"].verificar_integridad(self)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Documento íntegro" if integro else "⚠️ El documento cambió",
                "message": (
                    "El archivo en Drive coincide con el que se firmó."
                    if integro
                    else "El archivo en Drive YA NO coincide con el hash registrado al firmar — "
                    "pudo haber sido modificado después de la firma."
                ),
                "type": "success" if integro else "danger",
                "sticky": not integro,
            },
        }

    def _actualizar_firmante_en_log(self):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"]._actualizar_firmante_en_log(self)

    def _resolver_partner_contratista(self):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"]._resolver_partner_contratista(self)

    def _enviar_copia_firmada(self, pdf_bytes):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"]._enviar_copia_firmada(self, pdf_bytes)

    def _cron_expirar_solicitudes_firma(self):
        """Adaptador compatible del servicio reutilizable de firma."""
        return self.env["drive.firma.service"].expirar_solicitudes()

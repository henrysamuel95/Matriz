from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
import json

_inherit = ['mail.thread']          

_logger = logging.getLogger(__name__)


class ContratistasNotificacionWizard(models.TransientModel):
    _name = 'contratistas.notificacion.wizard'
    _description = 'Wizard de Notificación para Contratistas'
    

    contratacion_id = fields.Many2one('contratacion')

    proyecto_relacionado = fields.Many2one(
        'contratacion',
        string='Proyecto Relacionado'
    )

    contratista_proveedor_id = fields.Many2one(
        'contratista_proveedor',
        string='Contratista'
    )

    contratista_id = fields.Many2one(
        'res.users',
        string='Contratista',
        compute='_compute_contratista_id',
        store=True
    )

    asistentes_coordinador  = fields.Many2many(
        'res.users',
        string='Contratista',
        compute='_compute_asistente_coordinador',
        store=True
    )

    email_contratante = fields.Char(
    string='Correo del Contratista',
    compute='_compute_email_contratante',
    store=True
    )

    observaciones = fields.Text(
        string='Observaciones'
    )

    users_with_access = fields.Many2many(
        'res.users', 
        string='Permitir acceso de usuarios al proyecto', 
        related='proyecto_relacionado.users_with_access'
    )

    
    
    @api.depends('contratista_id')
    def _compute_email_contratante(self):
        for record in self:
            record.email_contratante = record.contratista_id.email or False

    @api.depends('proyecto_relacionado')
    def _compute_contratista_id(self):
        for rec in self:
            rec.contratista_id = rec.proyecto_relacionado.create_uid.id if rec.proyecto_relacionado else False

    @api.depends('proyecto_relacionado')
    def _compute_asistente_coordinador(self):
        grupo_asis = self.env.ref('Matriz.group_contratacion_asis_coordinador')
        grupo_coord = self.env.ref('Matriz.group_contratacion_coordinador')

        for rec in self:
            if not rec.proyecto_relacionado:
                rec.asistentes_coordinador = [(6, 0, [])]
                continue

            usuarios = rec.proyecto_relacionado.users_with_access.filtered(
                lambda u: grupo_asis in u.groups_id or grupo_coord in u.groups_id
            )

            rec.asistentes_coordinador = [(6, 0, usuarios.ids)]

    def action_send_solicitud_actualizacion(self, send_email=True):
        self.ensure_one()

        project = self.proyecto_relacionado
        if not project:
            raise UserError("No hay proyecto asociado a este contrato.")

        proveedor = self.contratista_proveedor_id
        if not proveedor or not getattr(proveedor, 'email', False):
            raise UserError("El contratista proveedor asociado no tiene correo configurado.")

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        login_url = f"{base_url.rstrip('/')}/public/login"

        COLOR_PRINCIPAL = "#043484"
        contrato_nombre = project.display_name

        # ------------------------------------------------------------------
        # NUEVO: preparar observaciones si existen
        # ------------------------------------------------------------------
        observ_text = ""
        if self.observaciones:
            observ_text = f"""
                <div style="margin: 15px 0; padding: 12px; background-color: #F7FCFF;
                            border-left: 4px solid {COLOR_PRINCIPAL}; border-radius: 4px;">
                    <strong>Observaciones del coordinador:</strong><br/>
                    <span style="white-space: pre-line;">{self.observaciones}</span>
                </div>
            """

        # Si NO se envía correo, solo escribe en el chatter del contrato
        if not send_email:
            message = f"""
                Se generó una solicitud de actualización de documentos para el contratista 
                <b>{proveedor.name}</b><br/>
            """

            if self.observaciones:
                message += f"<br/><strong>Observaciones:</strong><br/>{self.observaciones}"

            message += f"""<br/>Ingrese al portal: <a href="{login_url}" target="_blank">{login_url}</a>"""

            self.contratacion_id.message_post(body=message)
            return True

        # ------------------------------------------------------------------
        # CUERPO HTML DEL CORREO + OBSERVACIONES INTEGRADAS
        # ------------------------------------------------------------------
        body_html = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">📄 Solicitud de actualización de documentos</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                    <p>Cordial saludo, <strong>{proveedor.name}</strong>:</p>

                    <p>
                        Se ha generado una <b>solicitud de actualización de documentos</b>
                        asociada al contrato/proyecto:
                    </p>

                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                        <strong>Contrato / Proyecto:</strong> {contrato_nombre}<br/>
                    </div>

                    {observ_text}  <!-- AQUI SE INSERTAN LAS OBSERVACIONES -->

                    <p>Por favor ingrese al portal para actualizar los documentos requeridos.</p>

                    <p style="text-align: center; margin-top: 30px;">
                        <a href='{login_url}'
                            style="background-color: {COLOR_PRINCIPAL};
                            color: white;
                            padding: 10px 20px;
                            text-decoration: none;
                            border-radius: 5px;
                            font-weight: bold;
                            display: inline-block;">
                            ➡️ Ingresar al portal de contratistas
                        </a>
                    </p>

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático de SGO.
                    </p>
                </div>
            </div>
        """

        mail_values = {
            'subject': f"Solicitud de actualización de documentos - {contrato_nombre}",
            'email_from': 'go@fundacionunivalle.org',
            'email_to': proveedor.correo_facturacion,
            'body_html': body_html,
            'auto_delete': True,
        }

        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()

        # ------------------------------------------------------------------
        # REGISTRO EN EL CHATTER DEL CONTRATO + OBSERVACIONES
        # ------------------------------------------------------------------
        chatter_msg = f"Se envió una solicitud de actualización de documentos al contratista <b>{proveedor.name}</b>."

        if self.observaciones:
            chatter_msg += f"<br/><strong>Observaciones enviadas:</strong><br/>{self.observaciones}"

        project.message_post(body=chatter_msg)

        return True

    def action_confirm(self):
        self.ensure_one()

        self.action_send_solicitud_actualizacion(send_email=True)

        if self.contratista_proveedor_id:

            # 1. Guardar correo contratante
            self.contratista_proveedor_id.email_contratante = self.email_contratante
            
            # 2. Guardar proyecto relacionado (Modelo: contratacion)
            self.contratista_proveedor_id.proyecto_relacionado = self.proyecto_relacionado

            # ---------------------------------------------------------------------------------
            # NUEVO: Guardar la relación específica del expediente (Modelo: contrataciones_fuv)
            # ---------------------------------------------------------------------------------
            # Capturamos el ID del registro exacto desde el cual se abrió este Wizard
            if self.env.context.get('active_model') == 'contrataciones_fuv':
                contrato_fuv_id = self.env.context.get('active_id')
                self.contratista_proveedor_id.contratista1 = contrato_fuv_id
            # ---------------------------------------------------------------------------------

            # 3. Guardar lista de asistentes
            if self.asistentes_coordinador:
                self.contratista_proveedor_id.asistentes_coordinador_ids = [
                    (6, 0, self.asistentes_coordinador.ids)
                ]

                # 4. Obtener correos
                correos = [u.email for u in self.asistentes_coordinador if u.email]

                # 5. Guardar como JSON
                self.contratista_proveedor_id.correos_asistentes = json.dumps(correos)

        return {
            'type': 'ir.actions.act_window_close',
        }
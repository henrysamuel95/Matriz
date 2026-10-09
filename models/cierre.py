#!/usr/bin/python
# models.py
#import inflect as inflect
from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.exceptions import ValidationError
from attr import exceptions
from googleapiclient.errors import HttpError
import os
from datetime import timedelta, datetime, date
from dateutil.relativedelta import relativedelta
import base64
import tempfile
from .drive import GoogleDriveAuth
import mimetypes
import threading
import queue
import logging
_logger = logging.getLogger(__name__)



class Cierre(models.Model):
    _name = 'cierre'
    _description = 'Cierre de proyecto'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "apodo"
    name = fields.Char(string="Nombre")
    
    
    fecha_info_final = fields.Date(string="Fecha de información final", tracking=True)
    fecha_acta_final = fields.Date(string="Fecha de acta final", tracking=True)
    observaciones_coordinador = fields.Text(string="Observaciones del coordinador", tracking=True)

    state = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiero', 'Financiero'),
        ('juridico', 'Jurídico'),
        ('finalizado', 'Cerrar proyecto'),
    ], default='coordinador', string="Estado", tracking=True)
    #CERRAR PROYECTO
    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al ', related='proyecto_relacionado3.users_with_access')
    
    # 1. EL CAMPO PARA LA DIRECTORA FINANCIERA
    usuarios_financieros_ids = fields.Many2many(
        'res.users',
        'cierre_financieros_rel', # Nombre interno de la tabla (buena práctica)
        'cierre_id', 'user_id',
        string='Usuarios con acceso financiero',
        help='Usuarios con acceso para gestionar el cierre en el área financiera.',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_financiero').id)],
        tracking=True,
    )
    
    # 3. FUNCIÓN QUE CONSTRUYE Y ENVÍA EL CORREO
    def _notificar_asignacion_financiera(self, usuarios):
        # Obtenemos el nombre del proyecto
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombre_proyecto = ', '.join([n for n in nombres_proyecto if n]) or 'Proyecto'
        
        COLOR_PRINCIPAL = '#043484' # Color corporativo

        for user in usuarios:
            try:
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'
                
                mensaje_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0;">Asignación de cierre al proyecto: <strong>{nombre_proyecto}</strong></h3>
                    </div>
                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{user.name}</strong>:</p>
                        <p>La Dirección Financiera te ha asignado el acceso para gestionar y revisar el cierre del proyecto <strong>{nombre_proyecto}</strong>.</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Acción requerida:</strong> Por favor, ingresa al registro para revisar los documentos y gestionar el Paz y Salvo.
                        </div>

                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">Ver formulario de Cierre</a>
                        </p>
                    </div>
                </div>
                """
                mail_values = {
                    'subject': f'Asignación Financiera - Cierre del proyecto: {nombre_proyecto}',
                    'email_from': self.env.user.email,
                    'email_to': user.email,
                    'body_html': mensaje_html,
                    'auto_delete': True,
                }
                self.env['mail.mail'].sudo().create(mail_values).send()
                
            except Exception as e:
                _logger.error(f"Error al enviar correo de asignación financiera a {user.name}: {str(e)}")
                
    # 2. LÓGICA PARA DETECTAR ASIGNACIÓN, NOTIFICAR Y REGISTRAR TRAZABILIDAD
    def write(self, vals):
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            return True

        # A. Capturamos los usuarios financieros que ya estaban ANTES de guardar
        old_users_dict = {rec.id: set(rec.usuarios_financieros_ids.ids) for rec in self}
        
        # B. Guardamos los cambios nativos de Odoo
        res = super(Cierre, self).write(vals)
        
        # C. Verificamos si se modificó específicamente este campo
        if 'usuarios_financieros_ids' in vals:
            for rec in self:
                old_users = old_users_dict.get(rec.id, set())
                new_users = set(rec.usuarios_financieros_ids.ids)
                
                # Encontramos los NUEVOS y los REMOVIDOS
                added_user_ids = new_users - old_users
                removed_user_ids = old_users - new_users
                
                msg_lines = [] # Lista para armar el mensaje del historial
                
                # Si agregaron a alguien
                if added_user_ids:
                    nuevos_analistas = self.env['res.users'].browse(list(added_user_ids))
                    nombres_agregados = ", ".join(nuevos_analistas.mapped('name'))
                    msg_lines.append(f"<li><b>Usuarios agregados:</b> {nombres_agregados}</li>")
                    
                    # Tu notificación original
                    rec._notificar_asignacion_financiera(nuevos_analistas)
                
                # Si quitaron a alguien
                if removed_user_ids:
                    analistas_removidos = self.env['res.users'].browse(list(removed_user_ids))
                    nombres_removidos = ", ".join(analistas_removidos.mapped('name'))
                    msg_lines.append(f"<li><b>Usuarios removidos:</b> {nombres_removidos}</li>")
                
                # D. Si hubo algún cambio (agregar o quitar), dejamos la nota en el Chatter
                if msg_lines:
                    mensaje_html = f"<ul>{''.join(msg_lines)}</ul>"
                    rec.message_post(
                        body=f"<p>Actualización en los encargados del paz y salvo:</p>{mensaje_html}",
                        subtype_xmlid="mail.mt_note" # Deja el mensaje como una nota interna
                    )
                    
        return res
    
    
    cerrado = fields.Boolean(string="Cerrado")
    
    apodo = fields.Char(default="4. Cierre")
    def action_cerrar_proyecto(self):
        # 1. Verificar si marcó al menos una casilla de estado (incluyendo cobros_pendientes)
        if not (self.otros_pendientes or self.pagos_pendientes or self.cobros_pendientes or self.no_hay_pendientes):
            raise UserError("No se puede cerrar el proyecto. Es necesario marcar al menos una opción de pendientes "
                            "(Pagos, Cobros, Otros o 'No hay pendientes').")


        if not self.acta_liquidacion_firmada_id:
            raise UserError("Para proceder con el cierre, es necesario cargar previamente el Acta de Liquidación Firmada.")
        
        
        
        # 2. Verificar que si marcó "Otros pendientes", el campo de observaciones no esté vacío
        # Nota: Usamos strip() por si el usuario inserta espacios en blanco o si el widget HTML deja etiquetas vacías
        if self.otros_pendientes:
            # Limpiamos las etiquetas HTML básicas que Odoo a veces guarda en campos vacíos
            observaciones_limpias = self.observaciones_pendientes.replace('<p><br></p>', '').strip() if self.observaciones_pendientes else ''
            
            if not observaciones_limpias:
                raise UserError("Has marcado la opción 'Cerrar con pendientes especificar en la observación'. "
                                "Por favor, escribe la justificación en el campo de observaciones.")

        # Si todo está correcto, proceder a cerrar el proyecto
        self.proyecto_relacionado3.write({'coordinador_g': 'green'})
        self.proyecto_relacionado3.write({'juridica_g': 'green'})
        self.proyecto_relacionado3.write({'financiera_g': 'green'})
        self.proyecto_relacionado3.write({'recursos_humanos_g': 'green'})
        self.proyecto_relacionado3.write({'is_editable': True})
        self.notificar_coordinador_cierre_con_pendientes()



    # Relación con Contrataciones FUV
    contrataciones_fuv_id = fields.Many2one('contrataciones_fuv', string='Contrataciones FUV')
    # Relación con Contratacion
    proyecto_relacionado3 = fields.Many2one('contratacion', string='Selecciona un proyecto')
    fecha_final_proyecto_texto = fields.Char(string="Fecha Solicitud en Texto", related="proyecto_relacionado3.fecha_final_proyecto_texto")
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())
    write_date = fields.Datetime(string='Fecha de Modificación', readonly=True)


    financieros_aprueba = fields.Boolean(string='Financiero Aprobado', default=False, tracking=True)
    coordinador_aprobado = fields.Boolean(string='Coordinador Aprobado', default=False, tracking=True)
    juridico_aprueba = fields.Boolean(string='Jurídico Aprobado', default=False, tracking=True)

    fecha_vencimiento = fields.Date(
    string="Fecha de Vencimiento del Proyecto",
    compute="_compute_fecha_vencimiento",
    store=True
    )

    last_vencimiento_alert_date = fields.Date(
    string="Última Alerta de Vencimiento",
    )

    @api.depends('proyecto_relacionado3', 'proyecto_relacionado3.fecha_final_proyecto')
    def _compute_fecha_vencimiento(self):
        for record in self:
            record.fecha_vencimiento = record.proyecto_relacionado3.fecha_final_proyecto or False

    @api.model
    def _cron_notificar_vencimiento_proyectos(self):
        today = fields.Date.context_today(self)
        # _logger.info(">>> CRON EJECUTADO. Hoy es: %s", today)

        cierres = self.search([])
        cierres._compute_fecha_vencimiento()

        # for r in cierres:
        #     _logger.info(
        #         "DEBUG CIERRE id=%s | proj_id=%s | fecha_venc=%s | texto_cierre=%s | fecha_final_proy=%s | texto_proy=%s | last_alert=%s",
        #         r.id,
        #         r.proyecto_relacionado3.id,
        #         r.fecha_vencimiento,
        #         r.fecha_final_proyecto_texto,
        #         r.proyecto_relacionado3.fecha_final_proyecto,
        #         r.proyecto_relacionado3.fecha_final_proyecto_texto,
        #         r.last_vencimiento_alert_date,
        #     )

        proyectos = self.search([
            ('fecha_vencimiento', '!=', False),
            '|',
                ('last_vencimiento_alert_date', '=', False),
                ('last_vencimiento_alert_date', '!=', today),
        ])

        # _logger.info(">>> Proyectos encontrados con fecha_vencimiento y no notificados hoy: %s", proyectos.ids)

        for record in proyectos:
            # _logger.info("----- PROYECTO %s -----", record.id)
            # _logger.info("Fecha vencimiento: %s", record.fecha_vencimiento)

            fecha_v = record.fecha_vencimiento
            if not fecha_v:
                # _logger.info("❌ fecha_vencimiento es False, continuo")
                continue  

            primera_alerta = fecha_v - timedelta(days=5)
            limite_alertas = fecha_v + relativedelta(months=4)

            # _logger.info("Primera alerta: %s", primera_alerta)
            # _logger.info("Límite de alertas: %s", limite_alertas)

            if today < primera_alerta:
                # _logger.info("❌ Hoy NO está en el rango. Hoy < primera alerta")
                continue

            if today > limite_alertas:
                # _logger.info("❌ Hoy NO está en el rango. Hoy > límite")
                continue

            # _logger.info("✔ Hoy está dentro del rango de notificaciones.")

            diff = (today - primera_alerta).days
            # _logger.info("Días desde primera alerta: %s", diff)

            if diff % 15 != 0:
                # _logger.info("❌ No toca notificar hoy porque diff %% 15 != 0")
                continue

            # _logger.info("👍 Hoy SÍ es día de notificación (diff %% 15 == 0)")

            dias_restantes = (fecha_v - today).days
            # _logger.info("Días restantes para vencimiento (negativo si vencido): %s", dias_restantes)

            # _logger.info(">>> Enviando correo...")
            record._send_vencimiento_alert_email(dias_restantes)

            record.last_vencimiento_alert_date = today
            # _logger.info(">>> Fecha marcada como notificada.")


    def _send_vencimiento_alert_email(self, dias):
        for record in self:

            coordinador = record.proyecto_relacionado3.create_uid

            if not coordinador:
                _logger.info(
                    "❌ Cierre %s | Proyecto contrataciones_fuv_id=%s SIN coordinador (create_uid vacío).",
                    record.id,
                    record.contrataciones_fuv_id.id,
                )
                continue

            if not coordinador.email:
                _logger.info(
                    "❌ Cierre %s | Coordinador %s (%s) SIN email.",
                    record.id,
                    coordinador.name,
                    coordinador.id,
                )
                continue

            coordinadores = [coordinador]

            _logger.info(
                "DEBUG CORREO | cierre_id=%s | contrataciones_fuv_id=%s | proyecto_relacionado3=%s | coord_id=%s | coord_name=%s | coord_email=%s",
                record.id,
                record.contrataciones_fuv_id.id,
                record.proyecto_relacionado3.id,
                coordinador.id,
                coordinador.name,
                coordinador.email,
            )

            nombres_proyecto = [
                record.proyecto_relacionado3.name,
                record.proyecto_relacionado3.new_name
            ]
            nombres_proyecto_filtrados = [n for n in nombres_proyecto if n]
            nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

            base_url = record.env['ir.config_parameter'].sudo().get_param('web.base.url')
            formulario_url = f"/web#id={record.id}&view_type=form&model=cierre"
            confirm_url = base_url + formulario_url

            fecha_vencimiento_str = fields.Date.to_string(record.fecha_vencimiento)

            if dias >= 0:
                subject = (
                    f"⚠️ Alerta: El proyecto {nombre_proyecto}, "
                    f"está próximo a finalizar en {dias} días"
                )
                texto_estado = (
                    f"La fecha de finalización es el {fecha_vencimiento_str}, "
                    f"faltan {dias} días."
                )
            else:
                subject = (
                    f"⚠️ Alerta: El proyecto {nombre_proyecto}, "
                    f"se encuentra finalizado hace {abs(dias)} días"
                ) 
                texto_estado = (
                    f"La fecha de finalización fue el {fecha_vencimiento_str}, "
                    f"hace {abs(dias)} días."
                )

            body_html = f"""
                <p>Hola, coordinador.</p>
                <p>El proyecto <strong>{nombre_proyecto}</strong> requiere su atención.</p>
                <p>{texto_estado}</p>
                <p>Revise el siguiente enlace para gestionar el cierre:</p>
                <br/>
                <a href="{confirm_url}" style="
                    background-color:#4CAF50;
                    color:white;
                    padding:8px 16px;
                    text-decoration:none;
                    border-radius:5px;">
                    Ir al Proyecto
                </a>
                <hr style="border:none; border-top:1px solid #ddd; margin:20px 0;">
                <p style="font-size:12px; color:#888;">Este mensaje fue generado automáticamente. Por favor, no responda a este correo.</p>
                
            """

            for coord in coordinadores:
                mail_values = {
                    'subject': subject,
                    'body_html': body_html,
                    'email_from': 'go@fundacionunivalle.org',
                    'email_to': coord.email,
                    'auto_delete': True,
                }
                correo = record.env['mail.mail'].sudo().create(mail_values)
                correo.send()



    # Campos para almacenar documentos aprobados
    coordinador_approved = fields.Boolean(string='Aprobado por Coordinador', default=False, tracking=True)
    financiero_approved = fields.Boolean(string='Aprobado por Financiero', default=False, tracking=True)
    juridico_approved = fields.Boolean(string='Aprobado por Jurídico', default=False, tracking=True)

    def approve_coordinador(self):
        self.aprueba_coordinador()
        self.coordinador_approved = True
        self.state = 'financiero'

    def approve_financiero(self):
        self.aprueba_financieros()
        self.financiero_approved = True
        self.state = 'juridico'


    def approve_juridico(self):
        self.aprueba_juridico()
        self.juridico_approved = True
        self.state = 'finalizado'



    def no_aprueba_finalizar(self):
        self.state = 'juridico'

    @api.constrains('coordinador_approved')
    def _check_coordinador(self):
        if self.coordinador_approved and self.state != 'financiero':
            self.state = 'financiero'

    @api.constrains('financiero_approved')
    def _check_financiero(self):
        if self.financiero_approved and self.state != 'juridico':
            self.state = 'juridico'

    @api.constrains('juridico_approved')
    def _check_juridico(self):
        if self.juridico_approved and self.state != 'finalizado':
            self.state = 'finalizado'

    ##############Bloque de código del área jurídica. ###############################

    # Campos para rechazo jurídico
    devuelto_por_juridico = fields.Boolean(
        string="Devuelto", 
        default=False, 
        tracking=True,
        help="Se activa cuando Jurídico devuelve el trámite directamente al Coordinador.")
    
    no_aprueba_juridico = fields.Boolean(string="No aprueba jurídico")
    motivo_rechazo_juridico = fields.Text(string="Motivo de rechazo jurídico", tracking=True)
    aprobacion_juridica_realizada = fields.Boolean(string="Aprobación jurídica realizada", default=False)
    
    def juridico_no_aprueba(self):
        # Validar que el motivo de rechazo no esté vacío
        if not self.motivo_rechazo_juridico:
            raise ValidationError("Debes proporcionar un motivo para no aprobar.")

        # Verificar que la acción no se haya realizado antes
        if self.aprobacion_juridica_realizada and self.state != 'juridico':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Realizar la acción de registro solo si el estado actual es "juridico"
        if self.state == 'juridico':
            self.write({'aprobacion_juridica_realizada': True})

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener los usuarios financieros desde los usuarios con acceso al proyecto
        financieros = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id
        )

        # Usuario jurídico actual que rechaza
        juridico_user = self.env.user

        if financieros:
            COLOR_PRINCIPAL = '#B33250' # Color institucional

            for financiero_user in financieros:
                try:
                    # Construir la URL con la base de Odoo para el modelo 'cierre'
                    base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                    formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'

                    # Construir el mensaje HTML estandarizado con el nuevo estilo
                    mensaje_html = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                        
                        <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                            <h3 style="margin: 0; font-size: 18px;">Rechazo jurídico de cierre: <strong>{nombre_proyecto}</strong></h3>
                        </div>

                        <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                            
                            <p>Hola, <strong>{financiero_user.name}</strong>:</p>

                            <p>El departamento jurídico (<strong>{juridico_user.name}</strong>) no ha aprobado el cierre del proyecto y lo ha devuelto para su revisión.</p>
                            
                            <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                                <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                                <strong>Acción requerida:</strong> Por favor, revise el motivo del rechazo y realice las correcciones correspondientes.
                            </div>

                            <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                                <strong>Motivo de rechazo:</strong><br/>
                                <em style="color: #856404;">{self.motivo_rechazo_juridico}</em>
                            </div>
                            
                            <p style="text-align: center; margin-top: 30px;">
                                <a href='{formulario_url}' 
                                    style="background-color: {COLOR_PRINCIPAL}; 
                                    color: white; 
                                    padding: 10px 20px; 
                                    text-decoration: none; 
                                    border-radius: 5px; 
                                    font-weight: bold; 
                                    display: inline-block;">
                                    Ver formulario de Cierre
                                </a>
                            </p>

                            <p style="margin-top: 20px; font-size: 12px; color: #999;">
                                SGO - mensaje de notificación
                            </p>
                        </div>
                    </div>
                    """

                    # Enviar correo al área financiera
                    mail_values = {
                        'subject': f'No aprobado el cierre del proyecto {nombre_proyecto}',
                        'email_from': juridico_user.email,
                        'email_to': financiero_user.email,
                        'body_html': mensaje_html,
                        'auto_delete': True,
                    }
                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {financiero_user.name}: {str(e)}")

            # Cambiar el estado del proyecto a 'financiero' una vez enviados los correos
            self.write({'state': 'financiero'})

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje de rechazo ha sido enviado correctamente por correo a los financieros.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios en el grupo financiero para enviar el mensaje.")
    

    def juridico_devuelve_a_coordinador(self):
        self.ensure_one()
        
        # 1. Validaciones
        if not self.motivo_rechazo_juridico:
            raise ValidationError("Debes proporcionar un motivo en 'Motivo de rechazo jurídico' para devolver al Coordinador.")
        if self.state != 'juridico':
            raise ValidationError("El trámite no está en revisión jurídica.")

        # 2. Obtener datos del proyecto y usuarios
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombre_proyecto = ', '.join([n for n in nombres_proyecto if n])
        
        coordinadores = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id
        )
        juridico_user = self.env.user

        # 3. Notificación por correo al Coordinador
        if coordinadores:
            COLOR_PRINCIPAL = '#B33250' # Color de rechazo
            for coordinador in coordinadores:
                try:
                    base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                    formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'

                    mensaje_html = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                        <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                            <h3 style="margin: 0;">Devolución directa de Jurídico: <strong>{nombre_proyecto}</strong></h3>
                        </div>
                        <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                            <p>Hola, <strong>{coordinador.name}</strong>:</p>
                            <p>El departamento jurídico (<strong>{juridico_user.name}</strong>) ha devuelto el cierre directamente a Coordinación para una corrección puntual.</p>
                            <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                                <strong>Motivo de corrección:</strong><br/>
                                <em style="color: #856404;">{self.motivo_rechazo_juridico}</em>
                            </div>
                            <p style="text-align: center; margin-top: 30px;">
                                <a href='{formulario_url}' style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Ver formulario</a>
                            </p>
                        </div>
                    </div>
                    """
                    mail_values = {
                        'subject': f'Devolución directa del área jurídica, revisión del cierre al proyecto: {nombre_proyecto}',
                        'email_from': juridico_user.email,
                        'email_to': coordinador.email,
                        'body_html': mensaje_html,
                        'auto_delete': True,
                    }
                    self.env['mail.mail'].sudo().create(mail_values).send()
                except Exception as e:
                    _logger.error(f"No se pudo enviar correo a {coordinador.name}: {str(e)}")

        # 4. Cambiar estado y ENCENDER LA BANDERA
        self.write({
            'state': 'coordinador',
            'devuelto_por_juridico': True,  # <-- ESTO ES LO QUE HABILITARÁ EL BOTÓN DOBLE
            'aprobacion_juridica_realizada': True
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Devuelto a Coordinador',
                'message': 'El trámite se ha devuelto directo a coordinación.',
                'type': 'success',
                'sticky': False,
            }
        }
              
    def coordinador_reenvia_a_juridico(self):
        self.ensure_one()
        
        # Validar que efectivamente haya sido devuelto por jurídico
        if not self.devuelto_por_juridico:
            raise ValidationError("Esta opción solo está disponible si Jurídico devolvió el trámite directamente.")

        # 1. Obtener datos del proyecto y del Coordinador (quien hace el clic)
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombre_proyecto = ', '.join([n for n in nombres_proyecto if n])
        coordinador_user = self.env.user

        # 2. Buscar a los usuarios de Jurídico a los que hay que notificar
        juridicos = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_juridico') in user.groups_id or self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id
        )

        # 3. Cambiamos estado directo a jurídico y APAGAMOS la bandera
        self.write({
            'state': 'juridico',
            'devuelto_por_juridico': False, # Se apaga porque ya se consumió el salto
            'juridico_approved': False, # Para que jurídico deba volver a aprobar
            'aprobacion_juridica_realizada': False
        })
        
        # 4. Construir y enviar el correo a los usuarios de Jurídico
        if juridicos:
            COLOR_PRINCIPAL = '#043484' # Un color azul para indicar "reenvío/corrección"
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'
            
            # Rescatamos las observaciones. Si está vacío, ponemos un texto por defecto para que no salga "False"
            notas_coordinador = self.observaciones_coordinador or "No se registraron observaciones adicionales."

            mensaje_html = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0;">Trámite Corregido: <strong>{nombre_proyecto}</strong></h3>
                </div>
                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                    <p>Hola, equipo <strong>Jurídico</strong>:</p>
                    <p>El coordinador (<strong>{coordinador_user.name}</strong>) ha realizado las correcciones solicitadas y ha reenviado el cierre directamente a su área para revisión.</p>
                    
                    <div style="margin-top: 20px; padding: 15px; border-left: 4px solid {COLOR_PRINCIPAL}; background-color: #f0f4fa; border-radius: 4px;">
                        <strong style="color: {COLOR_PRINCIPAL};">Observaciones del coordinador:</strong><br/>
                        <em style="color: #555; display: block; margin-top: 8px;">{notas_coordinador}</em>
                    </div>
                    <p style="text-align: center; margin-top: 30px;">
                        <a href='{formulario_url}' style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Revisar trámite</a>
                    </p>
                </div>
            </div>
            """
            
            for juridico in juridicos:
                if juridico.email:
                    try:
                        mail_values = {
                            'subject': f'Trámite corregido y reenviado, continuar el cierre del proyecto: {nombre_proyecto}',
                            'email_from': coordinador_user.email or self.env.company.email,
                            'email_to': juridico.email,
                            'body_html': mensaje_html,
                            'auto_delete': True,
                        }
                        self.env['mail.mail'].sudo().create(mail_values).send()
                    except Exception as e:
                        import logging
                        _logger = logging.getLogger(__name__)
                        _logger.error(f"No se pudo enviar correo a {juridico.name}: {str(e)}")

        # 5. Retornar la notificación en pantalla
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Enviado directo a Jurídico',
                'message': 'El trámite saltó a Financiera y se envió a revisión Jurídica. Se ha notificado al equipo.',
                'type': 'success',
                'sticky': False,
            }
        }
        
    @api.onchange('acta_liquidacion_firmada')
    def _onchange_acta_liquidacion_firmada(self):
        # Habilitar el botón de aprobar coordinador si el archivo ha sido cargado
        if self.acta_liquidacion_firmada:
            self.juridico_aprueba = True
        else:
            self.juridico_aprueba = False

    activar_campo_acta_firmada = fields.Boolean(string="Aprobado Jurídico", default=False)
    aprobado_juridico_visible_pendientes = fields.Boolean(string="Aprobado Jurídico", default=False)
    
    
    def aprueba_juridico(self, documentos=None):
        self.aprobado_juridico_visible_pendientes = True
        self.activar_campo_acta_firmada = True

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores filtrados en la lista
        coordinadores = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id
        )

        # Obtener el usuario jurídico actual
        juridico = self.env.user

        if coordinadores:
            COLOR_PRINCIPAL = '#043484' # Color institucional establecido

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo para el modelo 'cierre'
                    base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                    formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'

                    # Construir el mensaje HTML estandarizado con el nuevo estilo
                    mensaje_html = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                        
                        <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                            <h3 style="margin: 0; font-size: 18px;">Aprobación jurídica de cierre: <strong>{nombre_proyecto}</strong></h3>
                        </div>

                        <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                            
                            <p>Hola, <strong>{coordinador.name}</strong>:</p>

                            <p>El departamento jurídico (<strong>{juridico.name}</strong>) informa que la información suministrada cumple con los requisitos contractuales pactados y que no hay novedades, para proceder con la firma del director y cargar el acta firmada al sistema.</p>
                            
                            <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                                <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                                <strong>Acción requerida:</strong> Por favor, continúe con el cierre del proyecto.
                            </div>

                            {f'''
                            <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                                <strong>Observaciones Jurídicas:</strong><br/>
                                <em style="color: #856404;">{self.motivo_rechazo_juridico}</em>
                            </div>
                            ''' if self.motivo_rechazo_juridico else ''}
                            
                            <p style="text-align: center; margin-top: 30px;">
                                <a href='{formulario_url}' 
                                    style="background-color: {COLOR_PRINCIPAL}; 
                                    color: white; 
                                    padding: 10px 20px; 
                                    text-decoration: none; 
                                    border-radius: 5px; 
                                    font-weight: bold; 
                                    display: inline-block;">
                                    Ver formulario de Cierre
                                </a>
                            </p>

                            <p style="margin-top: 20px; font-size: 12px; color: #999;">
                                SGO - mensaje de notificación
                            </p>
                        </div>
                    </div>
                    """

                    # Enviar correo electrónico a los usuarios coordinadores
                    mail_values = {
                        'subject': f'Aprobación para continuar con el cierre del proyecto: {nombre_proyecto}',
                        'email_from': juridico.email,
                        'email_to': coordinador.email,
                        'body_html': mensaje_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Notificación después de enviar todos los mensajes
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': f'El mensaje ha sido enviado correctamente por correo a todos los coordinadores del proyecto {nombre_proyecto}.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }

        else:
            raise UserError("No hay usuarios con el rol de coordinador asociados al proyecto.")

    acta_liquidacion_firmada = fields.Binary(string='Acta de liquidación firmada', required=False, tracking=True)
    acta_liquidacion_firmada_name = fields.Char(tracking=True)
    acta_liquidacion_firmada_visible = fields.Boolean(default=True)
    acta_liquidacion_firmada_id = fields.Many2one('drive', string='Archivo Acta de Liquidación Firmada', tracking=True)

    def action_upload_acta_firmada(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir acta de liquidación firmada',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_cierre_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'acta_liquidacion_firmada_documento', 
            },
        }

    _cache_documentos_acta_firmada = {}

    def abrir_acta_firmada(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'acta_liquidacion_firmada_id' está configurado
        if self.acta_liquidacion_firmada_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Acta de Liquidación Firmada',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.acta_liquidacion_firmada_id.id,
                'target': 'new',
            }

        # Si 'acta_liquidacion_firmada_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.acta_liquidacion_firmada_name:
            file_name_to_search = self.acta_liquidacion_firmada_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_acta_firmada:
                drive_document = self._cache_documentos_acta_firmada[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_acta_firmada[file_name_to_search] = drive_document
                else:
                    raise UserError('Documento no encontrado en el Drive.')

        # Abrir el registro en la vista de formulario
        return {
            'type': 'ir.actions.act_window',
            'name': self.acta_liquidacion_firmada_name,
            'res_model': 'drive',
            'view_mode': 'form',
            'res_id': drive_document.id,
            'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
        }
        

    ##############Bloque de código del área financiera. ###############################

    # Campos
    no_aprueba_financieros = fields.Boolean(string="No aprueba financieros")
    motivo_rechazo_financiero = fields.Text(string="Observaciones Financieras", tracking=True)
    aprobacion_financiera_realizada = fields.Boolean(string="Aprobación financiera realizada", default=False)

    def financiera_no_aprueba(self):
        # Validar que el motivo de rechazo no esté vacío
        if not self.motivo_rechazo_financiero:
            raise ValidationError("Debes proporcionar un motivo para no aprobar.")

        # Verificar que la acción no se haya realizado antes
        if self.aprobacion_financiera_realizada and self.state != 'financiero':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Realizar la acción solo si el estado es "financiero"
        if self.state == 'financiero':
            self.write({'aprobacion_financiera_realizada': True})

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener los usuarios coordinadores desde los usuarios con acceso al proyecto
        coordinadores = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id
        )

        # Usuario financiero actual
        financiero = self.env.user

        if coordinadores:
            COLOR_PRINCIPAL = '#B33250' # Color institucional

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo para el modelo 'cierre'
                    base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                    formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'

                    # Construir el mensaje HTML estandarizado con el nuevo estilo
                    mensaje_html = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                        
                        <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                            <h3 style="margin: 0; font-size: 18px;">Rechazo financiero de cierre: <strong>{nombre_proyecto}</strong></h3>
                        </div>

                        <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                            
                            <p>Hola, <strong>{coordinador.name}</strong>:</p>

                            <p>El departamento financiero (<strong>{financiero.name}</strong>) no ha aprobado el cierre del proyecto y lo ha devuelto para su revisión.</p>
                            
                            <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                                <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                                <strong>Acción requerida:</strong> Por favor, revise el motivo del rechazo y realice las correcciones correspondientes.
                            </div>

                            <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                                <strong>Motivo de rechazo:</strong><br/>
                                <em style="color: #856404;">{self.motivo_rechazo_financiero}</em>
                            </div>
                            
                            <p style="text-align: center; margin-top: 30px;">
                                <a href='{formulario_url}' 
                                    style="background-color: {COLOR_PRINCIPAL}; 
                                    color: white; 
                                    padding: 10px 20px; 
                                    text-decoration: none; 
                                    border-radius: 5px; 
                                    font-weight: bold; 
                                    display: inline-block;">
                                    Ver formulario de Cierre
                                </a>
                            </p>

                            <p style="margin-top: 20px; font-size: 12px; color: #999;">
                                SGO - mensaje de notificación
                            </p>
                        </div>
                    </div>
                    """

                    # Enviar correo al coordinador
                    mail_values = {
                        'subject': f'No aprobado el cierre del proyecto {nombre_proyecto}',
                        'email_from': financiero.email,
                        'email_to': coordinador.email,
                        'body_html': mensaje_html,
                        'auto_delete': True,
                    }
                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Cambiar el estado del proyecto a 'coordinador' una vez que se enviaron los correos
            self.write({'state': 'coordinador'})

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje de rechazo ha sido enviado correctamente por correo a los coordinadores.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")


    @api.onchange('paz_y_salvo')
    def _onchange_paz_y_salvo(self):
        # Habilitar el botón de aprobar coordinador si el archivo ha sido cargado
        if self.paz_y_salvo:
            self.financieros_aprueba = True
        else:
            self.financieros_aprueba = False


    def aprueba_financieros(self, documentos=None):
        self.ensure_one()

        # 1. Verificar si el archivo ha sido cargado
        if not self.paz_y_salvo_id:
            raise UserError("Para proceder con la aprobación, es necesario cargar previamente el Acta de Paz y Salvo.")

        # 2. Obtener nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # 3. Obtener los usuarios del grupo jurídico y al director jurídico
        grupo_juridico = self.env.ref('Matriz.group_contratacion_juridico')
        grupo_director = self.env.ref('Matriz.group_contratacion_director_juridico')

        juridicos = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: grupo_juridico in user.groups_id or grupo_director in user.groups_id
        )

        # --- VALIDACIÓN AGREGADA ---
        if not juridicos:
            raise UserError(
                f"No hay usuarios del equipo Jurídico o Director Jurídico asignados con acceso al proyecto: {nombre_proyecto}. "
                "Por favor, verifique la lista de acceso del proyecto antes de continuar."
            )
        # ---------------------------

        # 4. Obtener el usuario financiero actual
        financiero = self.env.user
        COLOR_PRINCIPAL = '#043484'
        
        # 5. Enviar correos
        for juridico in juridicos:
            try:
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'

                mensaje_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Revisión de cierre de proyecto: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{juridico.name}</strong>:</p>

                        <p>El departamento financiero (<strong>{financiero.name}</strong>) informa que ya puede revisar el acta de liquidación y el paz y salvo para continuar con el proceso.</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                            <strong>Acción requerida:</strong> Por favor, proceder con la revisión jurídica.
                        </div>

                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                            <strong>Observaciones Financieras:</strong><br/>
                            <em style="color: #856404;">{self.motivo_rechazo_financiero}</em>
                        </div>
                        ''' if self.motivo_rechazo_financiero else ''}
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Ver formulario de Cierre
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            SGO - mensaje de notificación
                        </p>
                    </div>
                </div>
                """

                self.env['mail.mail'].sudo().create({
                    'subject': f'Aprobación para continuar con el cierre del proyecto: {nombre_proyecto}',
                    'email_from': financiero.email,
                    'email_to': juridico.email,
                    'body_html': mensaje_html,
                    'auto_delete': True,
                }).send()

            except Exception as e:
                raise UserError(f"No se pudo enviar el correo a {juridico.name}: {str(e)}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': f'El mensaje ha sido enviado correctamente a los jurídicos del proyecto {nombre_proyecto}.',
                'type': 'success',
                'sticky': False,
            }
        }




            
    paz_y_salvo = fields.Binary(string='Paz y salvo', required=False, tracking=True)
    paz_y_salvo_name = fields.Char(string='Paz y salvo', tracking=True)
    paz_y_salvo_visible = fields.Boolean(default=True)
    paz_y_salvo_id = fields.Many2one('drive', string='Archivo Paz y Salvo', tracking=True)

    def action_upload_paz_y_salvo(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir paz y salvo',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_cierre_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'paz_y_salvo_documento', 
            },
        }



    _cache_documentos_paz_y_salvo = {}

    def abrir_documento_paz_y_salvo(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'paz_y_salvo_id' está configurado
        if self.paz_y_salvo_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Paz y Salvo',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.paz_y_salvo_id.id,
                'target': 'new',
            }

        # Si 'paz_y_salvo_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.paz_y_salvo_name:
            file_name_to_search = self.paz_y_salvo_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_paz_y_salvo:
                drive_document = self._cache_documentos_paz_y_salvo[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_paz_y_salvo[file_name_to_search] = drive_document
                else:
                    raise UserError('Documento no encontrado en el Drive.')

        # Abrir el registro en la vista de formulario
        return {
            'type': 'ir.actions.act_window',
            'name': self.paz_y_salvo_name,
            'res_model': 'drive',
            'view_mode': 'form',
            'res_id': drive_document.id,
            'target': 'new',  # Opcional, puede ser 'current' para abrir en la misma ventana
        }






    ##############Bloque de código del coordinador. ###############################


    @api.onchange('archivo_acta_liquidacion')
    def _onchange_archivo_acta_liquidacion(self):
        # Habilitar el botón de aprobar coordinador si el archivo ha sido cargado
        if self.archivo_acta_liquidacion:
            self.coordinador_aprobado = True
        else:
            self.coordinador_aprobado = False

    def aprueba_coordinador(self):
        # Verificar si el archivo ha sido cargado antes de continuar
        if not self.acta_liquidacion_firmada_id and self.state == 'finalizado':
            raise UserError("Para proceder con el cierre, es necesario cargar previamente el Acta de Liquidación Firmada")

        if not self.archivo_acta_liquidacion_id:
            raise UserError("Para proceder con la aprobación, es necesario cargar previamente el Acta de Liquidación y asignar una fecha a la misma.")
        
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = self.proyecto_relacionado3.new_name or self.proyecto_relacionado3.name

        # Obtener los usuarios relacionados con el proyecto que tienen el rol de director financiero
        users_with_financial_role = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id)

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Verificar si hay usuarios financieros relacionados con el proyecto
        if users_with_financial_role:
            COLOR_PRINCIPAL = '#043484' # Puedes cambiar este color hexadecimal si prefieres otro tono
            
            for financiero in users_with_financial_role:
                try:
                    # Construir la URL con la base de Odoo para el modelo 'cierre'
                    base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                    formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'

                    # Construir el mensaje HTML estandarizado con el nuevo estilo
                    mensaje_html = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                        
                        <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                            <h3 style="margin: 0; font-size: 18px;">Inicio de cierre de proyecto: <strong>{nombre_proyecto}</strong></h3>
                        </div>

                        <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                            
                            <p>Hola, <strong>{financiero.name}</strong>:</p>

                            <p>El coordinador <strong>{coordinador.name}</strong> ha cargado el acta de liquidación para empezar el cierre del proyecto.</p>
                            
                            <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                                <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                                <strong>Acción requerida:</strong> Por favor, revisar y emitir el Paz y Salvo o incluir la persona responsable para hacerlo, dentro de la <strong>lista de usuarios</strong>.
                            </div>

                            {f'''
                            <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                                <strong>Observaciones del Coordinador:</strong><br/>
                                <em style="color: #856404;">{self.observaciones_coordinador}</em>
                            </div>
                            ''' if self.observaciones_coordinador else ''}
                            
                            <p style="text-align: center; margin-top: 30px;">
                                <a href='{formulario_url}' 
                                    style="background-color: {COLOR_PRINCIPAL};
                                    color: white; 
                                    padding: 10px 20px; 
                                    text-decoration: none; 
                                    border-radius: 5px; 
                                    font-weight: bold; 
                                    display: inline-block;">
                                    Ver formulario de Cierre
                                </a>
                            </p>
                            <p style="margin-top: 20px; font-size: 12px; color: #999;">
                                SGO - mensaje de notificación
                            </p>
                        </div>
                    </div>
                    """

                    # Enviar correo electrónico a los usuarios financieros
                    mail_values = {
                        'subject': f'Aprobación para continuar con el cierre del proyecto: {nombre_proyecto}',
                        'email_from': coordinador.email,
                        'email_to': financiero.email,
                        'body_html': mensaje_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

            # Forzar la actualización del cliente
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje ha sido enviado correctamente por correo al departamento financiero.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios con el rol Dirección financiera asociados al proyecto.")

      # Campo para almacenar la carpeta principal encontrada (en modelo 'carpeta_principal')
    stage3_id = fields.Many2one('drive_stage', string='Ubicación', tracking=True)

    def _buscar_carpeta(self):
        if self.proyecto_relacionado3:
            _logger.info(f"Buscando carpeta para el proyecto relacionado: {self.proyecto_relacionado3.name}")

            drive_stage = self.env['drive_stage'].search([
                ('name', 'in', ['1. CARPETA PRINCIPAL', 'CARPETA PRINCIPAL']),
                ('proyecto_relacionado', '=', self.proyecto_relacionado3.id)
            ], limit=1)

            if not drive_stage:
                _logger.warning("No se encontró la carpeta 'CARPETA PRINCIPAL' para el proyecto relacionado.")
                return None

            _logger.info(f"Carpeta encontrada: {drive_stage.name} (ID: {drive_stage.id})")
            return drive_stage
        return None 
    
    
    
    archivo_acta_liquidacion = fields.Binary(string='Acta de liquidación', required=False, tracking=True)
    archivo_acta_liquidacion_name = fields.Char(tracking=True)
    archivo_acta_liquidacion_visible = fields.Boolean(default=True)
    archivo_acta_liquidacion_id = fields.Many2one('drive', string='Archivo Acta de Liquidación', tracking=True)

    def action_upload_archivo_acta_liquidacion(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_carpeta_principal_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.stage3_id.folder_id if self.stage3_id and self.stage3_id.folder_id else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir acta de liquidación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_carpeta_principal_cierre_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage3_id': self.stage3_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'archivo_acta_liquidacion_documento', 
            },
        }
    _cache_documentos_archivo_acta = {}

    def abrir_documento_archivo_acta(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'archivo_acta_liquidacion_id' está configurado
        if self.archivo_acta_liquidacion_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                'type': 'ir.actions.act_window',
                'name': 'Acta de liquidación',
                'res_model': 'drive',
                'view_mode': 'form',
                'res_id': self.archivo_acta_liquidacion_id.id,
                'target': 'new',
            }

        # Si 'archivo_acta_liquidacion_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.archivo_acta_liquidacion_name:
            file_name_to_search = self.archivo_acta_liquidacion_name.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_archivo_acta:
                drive_document = self._cache_documentos_archivo_acta[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env['drive'].search([('file_name', 'ilike', file_name_to_search)], limit=1)

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_archivo_acta[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'archivo_acta_liquidacion_id' con el ID del documento encontrado
                self.write({'archivo_acta_liquidacion_id': drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Acta de liquidación',
                    'res_model': 'drive',
                    'view_mode': 'form',
                    'res_id': drive_document.id,
                    'target': 'new',
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError(f'Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}')


    # Nuevos campos para pendientes (Lógica 100% manual)
    otros_pendientes = fields.Boolean(string='Cerrar con pendientes especificar en la observación', default=False, tracking=True)
    pagos_pendientes = fields.Boolean(string='Pagos pendientes', default=False, tracking=True)
    no_hay_pendientes = fields.Boolean(string='No hay pendientes', default=False, tracking=True)
    observaciones_pendientes = fields.Text(string='Observaciones', tracking=True)
    cobros_pendientes = fields.Boolean(string='Cobros pendientes', default=False, tracking=True)

    # Métodos Onchange para hacer que los booleanos sean mutuamente excluyentes

    @api.onchange('otros_pendientes')
    def _onchange_otros_pendientes(self):
        if self.otros_pendientes:
            self.pagos_pendientes = False
            self.no_hay_pendientes = False
            self.cobros_pendientes = False

    @api.onchange('pagos_pendientes')
    def _onchange_pagos_pendientes(self):
        if self.pagos_pendientes:
            self.otros_pendientes = False
            self.no_hay_pendientes = False
            self.cobros_pendientes = False

    @api.onchange('no_hay_pendientes')
    def _onchange_no_hay_pendientes(self):
        if self.no_hay_pendientes:
            self.otros_pendientes = False
            self.pagos_pendientes = False
            self.cobros_pendientes = False
            # Opcional: limpiar las observaciones si no hay pendientes
            self.observaciones_pendientes = False 

    @api.onchange('cobros_pendientes')
    def _onchange_cobros_pendientes(self):
        if self.cobros_pendientes:
            self.otros_pendientes = False
            self.pagos_pendientes = False
            self.no_hay_pendientes = False
            
            
    def notificar_coordinador_cierre_con_pendientes(self):
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado3.name, self.proyecto_relacionado3.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # 1. Obtener destinatarios (Coordinadores + Dirección Estructuración)
        destinatarios = self.proyecto_relacionado3.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id or \
                         self.env.ref('Matriz.group_contratacion_estructuracion') in user.groups_id
        )

        # 2. Validar que exista al menos una persona para enviar el correo
        if not destinatarios:
            raise UserError("No hay usuarios en Coordinación ni en Dirección de Estructuración para enviar el mensaje.")

        # 3. Construir el mensaje HTML con formato mejorado
        mensaje = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #2c3e50;">Estado del proyecto: {nombre_proyecto}</h2>
            <div style="margin: 15px 0; padding: 10px; background-color: #f8f9fa; border-left: 4px solid #2c3e50;">
                <p>El proyecto se ha cerrado con el siguiente estado:</p>
                <ul style="list-style-type: none; padding-left: 20px;">
        """

        if self.otros_pendientes:
            mensaje += "<li style='margin: 5px 0;'>✓ Cerrar con pendientes especificados en la observación</li>"

        if self.pagos_pendientes:
            mensaje += "<li style='margin: 5px 0;'>💰 Hay pagos pendientes</li>"

        if self.cobros_pendientes:
            mensaje += "<li style='margin: 5px 0;'>💼 Hay cobros pendientes</li>"

        if self.no_hay_pendientes:
            mensaje += "<li style='margin: 5px 0;'>✅ No hay pendientes</li>"

        mensaje += "</ul>"

        # Mostrar las observaciones ingresadas manualmente por el usuario
        if self.observaciones_pendientes:
            mensaje += f"""
                <div style="margin-top: 15px; padding: 10px; background-color: #fff3cd; border-left: 4px solid #ffc107;">
                    <strong>Observaciones:</strong><br/>
                    {self.observaciones_pendientes}
                </div>
            """

        mensaje += "</div>"
        self.cerrado = True
        # 4. Enviar notificación a cada destinatario encontrado
        for destinatario in destinatarios:
            try:
                # Construir la URL del formulario
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                formulario_url = f'{base_url}/web#id={self.id}&view_type=form&model=cierre'

                # Agregar botón de acción al mensaje
                mensaje_completo = mensaje + f"""
                    <div style="margin-top: 20px; text-align: center;">
                        <a href="{formulario_url}" style="
                            background-color: #007bff;
                            color: white;
                            padding: 10px 20px;
                            text-decoration: none;
                            border-radius: 5px;
                            display: inline-block;">
                            Ver formulario de Cierre
                        </a>
                    </div>
                </div>
                """

                # Enviar correo
                mail_values = {
                    'subject': f'Cierre del proyecto {nombre_proyecto}',
                    'email_from': self.env.user.email or self.env.company.email,
                    'email_to': destinatario.email,
                    'body_html': mensaje_completo,
                    'auto_delete': True,
                }
                mail_id = self.env['mail.mail'].sudo().create(mail_values)
                mail_id.send()

            except Exception as e:
                raise UserError(f"Error al enviar correo a {destinatario.name}: {str(e)}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Notificación enviada',
                'message': 'La notificación sobre el cierre del proyecto ha sido enviada correctamente a Coordinación y Estructuración.',
                'type': 'success',
                'sticky': False,
                'message_duration': 9,
            }
        }

    # REPORTES DE PROYECTO (Se conserva el cálculo del nombre ya que es estructural)
    create_uid_R = fields.Many2one(string="Creador del proyecto", related='proyecto_relacionado3.creator_user_id')

    @api.depends('proyecto_relacionado3')
    def _compute_name(self):
        for record in self:
            if record.proyecto_relacionado3:
                # Accede al registro de 'contratacion' a través de 'proyecto_relacionado3'
                contratacion_record = self.env['contratacion'].browse(record.proyecto_relacionado3.id)

                # Obtiene el nombre correspondiente
                nombre = contratacion_record.new_name or contratacion_record.name
                record.name = nombre  # Asigna el nombre directamente a 'name'
            else:
                record.name = 'Sin proyecto relacionado'

    # Campos referenciales base (Si no los usas en las vistas de cierre, también puedes borrarlos)
    valor_a_contratar_R = fields.Monetary(related='proyecto_relacionado3.valor_a_contratar')
    currency_id = fields.Many2one('res.currency', string="Moneda")
    valor_pago_2suma3 = fields.Float(string='Valor contratado', related='proyecto_relacionado3.valor_pago_2suma3')
    valor_total_final_R = fields.Float(related='proyecto_relacionado3.valor_total_final', string="Valor total")
    valor_cobrado3 = fields.Monetary(string='Valor Cobrado', related='proyecto_relacionado3.valor_cobrado3')
    
    
    def unlink(self):
            for record in self:
                # Recolectar todos los documentos relacionados en el modelo 'drive'
                documentos_a_borrar = self.env['drive'].browse()
                
                if record.archivo_acta_liquidacion_id:
                    documentos_a_borrar += record.archivo_acta_liquidacion_id
                
                if record.paz_y_salvo_id:
                    documentos_a_borrar += record.paz_y_salvo_id
                
                if record.acta_liquidacion_firmada_id:
                    documentos_a_borrar += record.acta_liquidacion_firmada_id

                # Si hay documentos asociados, los eliminamos primero
                if documentos_a_borrar:
                    documentos_a_borrar.unlink()

            # Finalmente, llamamos al método 'unlink' original para borrar el registro de Cierre
            return super(Cierre, self).unlink()

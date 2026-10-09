# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from datetime import datetime, time
import pytz

class MaintenanceControl(models.TransientModel):
    _name = 'maintenance.control.wizard'
    _description = 'Control de Modo Mantenimiento'

    subject = fields.Char(string="Asunto del Correo", default="⚠️ AVISO: Mantenimiento programado del sistema")
    
    maintenance_date = fields.Date(string="Fecha del Mantenimiento")
    maintenance_time = fields.Char(string="Hora", placeholder="Ej: 6:00")
    maintenance_ampm = fields.Selection([
        ('AM', 'AM'), 
        ('PM', 'PM')
    ], string="AM / PM", default='PM')
    
    message = fields.Text(string="Mensaje para Usuarios")

    @api.onchange('maintenance_date', 'maintenance_time', 'maintenance_ampm')
    def _onchange_datetime(self):
        date_str = "[Día y Fecha]"
        
        if self.maintenance_date:
            meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
            dias = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
            
            dia_semana = dias[self.maintenance_date.weekday()]
            dia = self.maintenance_date.day
            mes = meses[self.maintenance_date.month - 1]
            
            date_str = f"{dia_semana} {dia} de {mes}"

        time_str = f"{self.maintenance_time or '[Hora]'} {self.maintenance_ampm or ''}".strip()
        
        self.message = f"El sistema entrará en mantenimiento el {date_str} a partir de las {time_str}. Por favor programe su trabajo para que ese día no tenga inconveniente."

    def action_send_warning(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'matriz.start_maintenance_emails',
            'params': {
                'subject': self.subject,
                'message': self.message,
                'email_from': self.env.user.email_formatted or self.env.user.email,
            }
        }

    @api.model
    def js_get_users_to_email(self):
        users = self.env['res.users'].search([('share', '=', False), ('active', '=', True), ('email', '!=', False)])
        return [{'id': u.id, 'email': u.email} for u in users]

    @api.model
    def js_send_single_email(self, user_email, subject, message, email_from):
        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #ddd; border-radius: 8px; overflow: hidden;">
            <div style="background-color: #f8d7da; color: #721c24; padding: 15px; text-align: center;">
                <h2 style="margin: 0;">⚠️ Aviso de Mantenimiento</h2>
            </div>
            <div style="padding: 20px; background-color: #ffffff; color: #333333;">
                <p style="font-size: 16px;">Hola,</p>
                <p style="font-size: 16px; line-height: 1.5;">{message}</p>
                <p style="font-size: 14px; color: #666; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px;">
                    <em>Por favor, tome las precauciones necesarias.</em><br>
                    <strong>Atte, El equipo de desarrollo</strong>
                </p>
            </div>
        </div>
        """
        try:
            self.env['mail.mail'].sudo().create({
                'subject': subject,
                'body_html': html_body,
                'email_to': user_email,
                'email_from': email_from,
                'auto_delete': True,
            }).send()
            return {'status': 'success'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    # Función auxiliar infalible para encontrar el Cron por su ID de XML
    def _get_maintenance_cron(self):
        cron_data = self.env['ir.model.data'].sudo().search([('name', '=', 'ir_cron_check_maintenance')], limit=1)
        if cron_data:
            return self.env['ir.cron'].with_context(active_test=False).sudo().browse(cron_data.res_id)
        return False

    def action_activate_maintenance(self):
        self.env['ir.config_parameter'].sudo().set_param('matriz.maintenance_mode', 'True')
        
        payload = {
            'type': 'maintenance_on',
            'title': '⚠️ MANTENIMIENTO ACTIVO',
            'message': 'el sistema esta en mantenimiento', 
            'blocking': True
        }
        self.env['bus.bus']._sendone('maintenance_channel', 'maintenance_signal', payload)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mantenimiento Activado',
                'message': 'El sistema está bloqueado para los usuarios.',
                'type': 'warning',
                'sticky': False,
            }
        }

    def action_deactivate_maintenance(self):
        self.env['ir.config_parameter'].sudo().set_param('matriz.maintenance_mode', 'False')
        self.env['ir.config_parameter'].sudo().set_param('matriz.scheduled_maintenance_datetime', '')
        
        # Apagamos el cron usando la búsqueda infalible
        cron = self._get_maintenance_cron()
        if cron:
            cron.write({'active': False})
            
        payload = {
            'type': 'maintenance_off',
            'title': '✅ Sistema Restaurado',
            'message': 'El mantenimiento ha finalizado. Puede continuar trabajando.',
            'blocking': False
        }
        self.env['bus.bus']._sendone('maintenance_channel', 'maintenance_signal', payload)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sistema Restaurado',
                'message': 'El mantenimiento ha finalizado y el acceso está permitido.',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_schedule_maintenance(self):
        """Guarda la fecha/hora y ENCIENDE el Cron"""
        if not self.maintenance_date or not self.maintenance_time:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Faltan datos',
                    'message': 'Debe seleccionar la fecha y la hora para programar.',
                    'type': 'danger',
                    'sticky': False,
                }
            }

        try:
            time_parts = self.maintenance_time.replace(' ', '').split(':')
            hours = int(time_parts[0])
            minutes = int(time_parts[1]) if len(time_parts) > 1 else 0

            if self.maintenance_ampm == 'PM' and hours < 12:
                hours += 12
            elif self.maintenance_ampm == 'AM' and hours == 12:
                hours = 0

            user_tz = pytz.timezone(self.env.user.tz or 'UTC')
            local_dt = datetime.combine(self.maintenance_date, time(hours, minutes))
            local_dt = user_tz.localize(local_dt)
            utc_dt = local_dt.astimezone(pytz.utc).replace(tzinfo=None)

            self.env['ir.config_parameter'].sudo().set_param('matriz.scheduled_maintenance_datetime', fields.Datetime.to_string(utc_dt))

            # Buscamos el cron usando el ID del XML
            cron = self._get_maintenance_cron()
            
            if cron:
                cron.write({
                    'active': True,
                    'nextcall': fields.Datetime.now()
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Mantenimiento Programado',
                        'message': 'El bloqueo automático quedó programado correctamente.',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '⚠️ Sigue sin aparecer',
                        'message': 'Odoo definitivamente no insertó el Cron. Verifica que pusiste el código en el mismo archivo XML que carga las vistas.',
                        'type': 'danger',
                        'sticky': True,
                    }
                }

        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error de formato',
                    'message': 'Por favor, escriba la hora en formato correcto, ej: 6:00',
                    'type': 'danger',
                    'sticky': False,
                }
            }

    @api.model
    def cron_check_scheduled_maintenance(self):
        """Esta función la llama el Cron de Odoo cada 1 minuto"""
        scheduled_str = self.env['ir.config_parameter'].sudo().get_param('matriz.scheduled_maintenance_datetime')
        
        if scheduled_str:
            scheduled_dt = fields.Datetime.from_string(scheduled_str)
            
            if fields.Datetime.now() >= scheduled_dt:
                # 1. Bloqueamos el sistema
                self.env['ir.config_parameter'].sudo().set_param('matriz.maintenance_mode', 'True')
                
                # 2. Mandamos la pantalla azul a los usuarios
                payload = {
                    'type': 'maintenance_on',
                    'title': '⚠️ MANTENIMIENTO ACTIVO',
                    'message': 'el sistema esta en mantenimiento',
                    'blocking': True
                }
                self.env['bus.bus']._sendone('maintenance_channel', 'maintenance_signal', payload)
                
                # 3. Borramos la fecha programada (Esto hace que el cron ya no haga nada en el futuro)
                self.env['ir.config_parameter'].sudo().set_param('matriz.scheduled_maintenance_datetime', '')
            
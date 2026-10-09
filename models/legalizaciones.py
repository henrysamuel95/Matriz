#!/usr/bin/python
from calendar import calendar
import re
import time
from unittest import case
from odoo import models, fields, api
import uuid  # <--- Agrega esta línea
from .drive import GoogleDriveAuth
import threading
from attr import exceptions
from odoo.exceptions import AccessError  # type: ignore
from datetime import datetime
import logging
import urllib.parse
from html import escape
from odoo.tools.float_utils import float_is_zero
from odoo.exceptions import ValidationError, UserError
from dateutil.relativedelta import relativedelta
import logging
import calendar


_logger = logging.getLogger(__name__)

class Viatico(models.Model):
    _name = 'viaticos'
    _description = 'Legalización viáticos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'fecha desc, id desc'
    
    
    def _validar_estado_boton(self, estado_esperado, nombre_accion):
        self.ensure_one()

        if self.state_viaticos == 'completado':
            raise UserError(
                "🚫 Esta legalización ya está completada.\n\n"
                "No se puede ejecutar nuevamente esta acción."
            )

        if self.state_viaticos != estado_esperado:
            estado_actual = dict(self._fields['state_viaticos'].selection).get(
                self.state_viaticos, self.state_viaticos
            )
            estado_requerido = dict(self._fields['state_viaticos'].selection).get(
                estado_esperado, estado_esperado
            )

            raise UserError(
                f"🚫 Acción no permitida.\n\n"
                f"La acción '{nombre_accion}' solo puede ejecutarse cuando la legalización "
                f"está en estado [{estado_requerido}].\n\n"
                f"Estado actual: [{estado_actual}]"
            )


    def _validar_notificacion(self, destinatarios, etapa, remitente=None):
        """Evita mover un trámite si no se puede avisar a sus responsables."""
        self.ensure_one()
        if not destinatarios:
            raise UserError(f"No hay destinatarios configurados para la etapa {etapa}.")

        sin_correo = destinatarios.filtered(lambda usuario: not usuario.email)
        if sin_correo:
            nombres = ', '.join(sin_correo.mapped('name'))
            raise UserError(
                f"No se puede enviar el trámite a {etapa}. "
                f"Los siguientes usuarios no tienen correo configurado: {nombres}."
            )

        if remitente and not remitente.email:
            raise UserError(
                f"No se puede notificar la etapa {etapa} porque el usuario que realiza "
                "la acción no tiene correo configurado."
            )
        return destinatarios

    def _destinatarios_cierre(self):
        """Obtiene los mismos responsables que recibían el cierre de Tesorería."""
        self.ensure_one()
        destinatarios = self.env['res.users']
        if self.create_uid:
            destinatarios |= self.create_uid
        grupo_coord = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)
        grupo_asis = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
        grupos_ids = [grupo.id for grupo in (grupo_coord, grupo_asis) if grupo]
        if grupos_ids and self.proyecto_relacionado_viaticos:
            destinatarios |= self.proyecto_relacionado_viaticos.users_with_access.filtered(
                lambda usuario: any(grupo.id in grupos_ids for grupo in usuario.groups_id)
            )
        return destinatarios

    def _formato_correo_legalizacion(self, destinatario, titulo, mensaje, url=None,
                                    observaciones=None, mostrar_retenciones=False,
                                    detalle=None):
        """Presentación común de los correos nuevos; no modifica el trámite."""
        self.ensure_one()
        texto = lambda valor: escape(str(valor or ''), quote=True)
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or 'Funcionario no identificado'
        proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        if not url:
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        saldos = ''
        for etiqueta, valor in (('Reembolso', self.reembolso), ('Reintegro', self.reintegro)):
            if valor > 0:
                saldos += (
                    f"<strong>{etiqueta}:</strong> "
                    f"<span style='background-color: #ffff00; padding: 2px 5px; color: #000;'>${valor:,.2f}</span><br/>"
                )
        retenciones = ''
        if mostrar_retenciones:
            retenciones = f"""
                <div style="margin: 15px 0; padding: 12px; background-color: #eef4fc; border-left: 4px solid #043484;">
                    <strong>Retenciones registradas por Auditoría Contable</strong><br/>
                    <strong>ReteIVA:</strong> ${self.rete_iva:,.2f}<br/>
                    <strong>ReteFuente:</strong> ${self.rete_fuente:,.2f}<br/>
                    <strong>ReteICA:</strong> ${self.rete_ica:,.2f}<br/>
                    <strong>Estampillas (si aplica):</strong> ${self.estampillas:,.2f}
                </div>
            """
        notas = ''
        if observaciones:
            notas = (
                '<div style="margin: 15px 0; padding: 12px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">'
                '<strong>Observaciones:</strong><br/>'
                f"{texto(observaciones).replace(chr(10), '<br/>')}</div>"
            )
        informacion = f'<p>{texto(detalle)}</p>' if detalle else ''
        return f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                <div style="background-color: #043484; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">{texto(titulo)}</h3>
                </div>
                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                    <p>Hola, <strong>{texto(destinatario.name)}</strong>:</p>
                    <p>{texto(mensaje)}</p>
                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #043484;">
                        <strong>Solicitante:</strong> {texto(funcionario)}<br/>
                        <strong>Proyecto:</strong> {texto(proyecto)}<br/>
                        {saldos}
                    </div>
                    {retenciones}{informacion}{notas}
                    <p style="text-align: center; margin-top: 30px;">
                        <a href="{texto(url)}" style="background-color: #043484; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">Ir al formulario de legalización</a>
                    </p>
                    <p style="margin-top: 20px; font-size: 12px; color: #999;">SGO - mensaje de notificación</p>
                </div>
            </div>
        """

    def _devolver_con_notificacion(self, destinatarios, etapa, estado_destino, observaciones):
        """Devuelve el trámite solo después de asegurar destinatario y correo."""
        self.ensure_one()
        remitente = self.env.user
        self._validar_notificacion(destinatarios, etapa, remitente)
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or 'Funcionario no identificado'
        proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        for destinatario in destinatarios:
            self.env['mail.mail'].sudo().create({
                'subject': f"↩ Devolución de legalización: {funcionario}",
                'email_from': remitente.email,
                'email_to': destinatario.email,
                'body_html': self._formato_correo_legalizacion(
                    destinatario, 'Devolución de legalización',
                    f'La legalización fue devuelta a {etapa} para su revisión.',
                    url=url, observaciones=observaciones,
                ),
                'auto_delete': True,
            }).send()
        self.write({'state_viaticos': estado_destino})

    def _return_notificacion_reload(self, title, message, tipo='success'):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': tipo,
                'sticky': False,
                'next': {
                    'type': 'ir.actions.act_window',
                    'name': self.display_name or 'Legalización',
                    'res_model': self._name,
                    'res_id': self.id,
                    'view_mode': 'form',
                    'views': [(False, 'form')],
                    'target': 'current',
                    'context': dict(self.env.context),
                }
            }
        }
        
    
    ciudad = fields.Many2one(comodel_name='municipio', string="Ciudad de Residencia", tracking=True)
    ciudad2 = fields.Many2one(comodel_name='municipio', string="Ciudad de Residencia", tracking=True, related='anticipo_id.ciudad_residencia')
    # --- RELACIONES Y SEGURIDAD ---
    proyecto_relacionado_viaticos = fields.Many2one('contratacion', string='Proyecto relacionado')
    
    cedula_funcionario = fields.Char(string="Cédula del funcionario", related='anticipo_id.numero')
    nit_funcionario = fields.Char(string="NIT del funcionario", related='anticipo_id.numero')
    
    doc_nit = fields.Boolean(string='NIT', tracking=True, related='anticipo_id.doc_nit')
    doc_cc = fields.Boolean(string='CC', tracking=True, related='anticipo_id.doc_cc')
    
    cedula_funcionario_s_anticipo = fields.Char(string="Cédula del funcionario")
    nit_funcionario_s_anticipo = fields.Char(string="NIT del funcionario")
    
    anticipo_id = fields.Many2one(
        'anticipos', 
        string='Anticipo relacionado', 
        # CAMBIO AQUÍ: Usamos 'proyecto_id' porque así se llama en la clase Anticipo
        domain="[('state', '=', 'completado'), ('proyecto_id', '=', proyecto_relacionado_viaticos)]"
    )
    
    
    id_consecutivo = fields.Char(string="Consecutivo", related="anticipo_id.id_consecutivo", ondelete='set null')
    
    # --- IDENTIFICACIÓN ---
    def name_get(self):
        result = []
        for rec in self:
            # 1. Determinar el nombre del funcionario dinámicamente
            funcionario = rec.funcionario_2 or rec.funcionario_sin_anticipo or "Funcionario no identificado"
            
            consecutivo = rec.anticipo_id.id_consecutivo or 'Sin/Anticipo previo' # S/A = Sin Anticipo
            # fecha = f" - {rec.fecha.strftime('%d/%m/%Y')}" if rec.fecha else ''
            
            # El nombre resultante será algo como: "Juan Pérez (ANT-001) - 18/03/2026"
            name = f"{funcionario} ({consecutivo})".strip()
            result.append((rec.id, name))
        return result

    @api.model
    def cron_notificar_pendientes_fin_de_mes(self):
        """
        Método ejecutado por una Acción Planificada (Cron).
        Verifica si faltan 4 días para acabar el mes y envía alertas con logs en consola.
        """        
        _logger = logging.getLogger(__name__)
        _logger.info("=== INICIANDO CRON DE VIÁTICOS PENDIENTES ===")

        # 1. Verificar fechas
        hoy = fields.Date.context_today(self)
        
        # Calculamos el último día del mes actual
        ultimo_dia_mes = hoy + relativedelta(day=1, months=1, days=-1)
        
        # Calculamos cuántos días faltan
        dias_restantes = (ultimo_dia_mes - hoy).days

        # Si no faltan exactamente 4 días, avisa en consola y detiene
        if dias_restantes != 4:
            _logger.info(f"CRON VIÁTICOS: Hoy es {hoy}. Faltan {dias_restantes} días para el fin de mes. Abortando.")
            return

        _logger.info("CRON VIÁTICOS: ¡Faltan exactamente 4 días para fin de mes! Procediendo a buscar registros...")

        # 2. Buscar TODOS los viáticos pendientes
        todos_los_pendientes = self.search([('state_viaticos', '!=', 'completado')])

        if not todos_los_pendientes:
            _logger.info("CRON VIÁTICOS: Todo al día. No hay trámites pendientes para notificar.")
            return

        # 3. Agrupar por proyecto
        proyectos_con_pendientes = todos_los_pendientes.mapped('proyecto_relacionado_viaticos')
        _logger.info(f"CRON VIÁTICOS: Se encontraron pendientes en {len(proyectos_con_pendientes)} proyecto(s). Preparando correos...")

        # 4. Ejecutar tu método original por cada proyecto
        correos_enviados = 0
        for proyecto in proyectos_con_pendientes:
            viatico_representante = todos_los_pendientes.filtered(lambda v: v.proyecto_relacionado_viaticos.id == proyecto.id)[0]
            
            try:
                # Llamamos a tu método que envía el correo
                viatico_representante.log_pendientes()
                _logger.info(f"CRON VIÁTICOS: -> Correo enviado al coordinador del proyecto: {proyecto.new_name}")
                correos_enviados += 1
            except Exception as e:
                # Si falla un proyecto, lo registra en rojo en la consola pero sigue con el siguiente
                _logger.error(f"CRON VIÁTICOS: -> ERROR al enviar correo para el proyecto {proyecto.new_name}. Detalle: {str(e)}")
                continue

        _logger.info(f"=== FIN CRON DE VIÁTICOS: Se procesaron {correos_enviados} correos exitosamente ===")

    @api.model
    def cron_recordar_legalizaciones_por_estado(self):
        """Recuerda cada cinco días al responsable del estado vigente."""
        ahora = fields.Datetime.now()
        limite = ahora - relativedelta(days=5)
        pendientes = self.search([('state_viaticos', '!=', 'completado')])
        grupos_por_estado = {
            'coordinador': (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            'contabilidad_retenciones': ('Matriz.group_contabilidad',),
            'presupuestos': ('Matriz.group_presupuestos',),
            'contabilidad_causacion': ('Matriz.group_contabilidad',),
            'contabilidad_decision': ('Matriz.group_contabilidad',),
            'contabilidad': ('Matriz.group_contabilidad',),
            'impuestos': ('Matriz.group_contratacion_impuestos',),
            'tesoreria': ('Matriz.group_tesoreria',),
        }

        for legalizacion in pendientes:
            fecha_estado = legalizacion.fecha_entrada_estado or legalizacion.write_date or legalizacion.create_date
            if not fecha_estado or fecha_estado > limite:
                continue
            if (legalizacion.ultima_alerta_estado and
                    legalizacion.ultima_alerta_estado > limite):
                continue

            grupos = [self.env.ref(xml_id, raise_if_not_found=False)
                      for xml_id in grupos_por_estado.get(legalizacion.state_viaticos, ())]
            grupos = [grupo for grupo in grupos if grupo]
            destinatarios = legalizacion.proyecto_relacionado_viaticos.users_with_access.filtered(
                lambda usuario: usuario.active and any(grupo in usuario.groups_id for grupo in grupos)
            )
            nombre_estado = dict(legalizacion._fields['state_viaticos'].selection).get(
                legalizacion.state_viaticos, legalizacion.state_viaticos
            )
            funcionario = legalizacion.funcionario_2 or legalizacion.funcionario_sin_anticipo or 'Funcionario no identificado'

            if not destinatarios:
                _logger.warning(
                    "Recordatorio de legalización %s sin destinatario configurado para %s.",
                    legalizacion.id, nombre_estado
                )
                legalizacion.message_post(body=(
                    f"<b>Recordatorio sin destinatario:</b> lleva cinco días o más en "
                    f"<b>{nombre_estado}</b>, pero no hay usuarios activos de ese rol con acceso al proyecto."
                ))
                legalizacion.sudo().write({'ultima_alerta_estado': ahora})
                continue

            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            formulario_url = f"{base_url}/web#id={legalizacion.id}&view_type=form&model=viaticos"
            legalizacion.message_post(
                body=f"<b>Recordatorio de trámite pendiente:</b> {nombre_estado}.",
                partner_ids=destinatarios.mapped('partner_id').ids,
                subtype_xmlid='mail.mt_comment',
            )
            for destinatario in destinatarios.filtered('email'):
                try:
                    self.env['mail.mail'].sudo().create({
                        'subject': f"⚠️ Recordatorio: legalización pendiente en {nombre_estado}",
                        'email_from': self.env.user.email or self.env.company.email,
                        'email_to': destinatario.email,
                        'body_html': legalizacion._formato_correo_legalizacion(
                            destinatario, 'Recordatorio de legalización pendiente',
                            f'La legalización lleva cinco días o más en la etapa {nombre_estado} y requiere gestión.',
                            url=formulario_url,
                        ),
                        'auto_delete': True,
                    }).send()
                except Exception:
                    # La notificación interna ya quedó publicada; un correo fallido
                    # no debe impedir recordar a los demás responsables.
                    _logger.exception(
                        "No se pudo enviar recordatorio de legalización %s a %s.",
                        legalizacion.id, destinatario.login
                    )
            legalizacion.sudo().write({'ultima_alerta_estado': ahora})



    # ESTE METODO NOTIFICARA AL COORDINADOR ASOCIADO AL PROYECTO LAS LEGALIZACIONES PENDIENTES DE GESTION O SEGUIMIENTO
    def log_pendientes(self):
        """
        Busca todos los viáticos pendientes del proyecto actual y envía un
        correo consolidado al Coordinador del proyecto con enlace a FUV.
        """
        self.ensure_one()
        user_env = self.env.user

        # 1. Identificar al Coordinador del proyecto
        grupo_coord = self.env.ref('Matriz.group_contratacion_coordinador')
        coordinadores = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda u: grupo_coord in u.groups_id
        )

        if not coordinadores:
            raise UserError(f"No se encontró un Coordinador asignado al proyecto: {self.proyecto_relacionado_viaticos.new_name}")

        # 2. Buscar registros pendientes (No completados)
        pendientes = self.search([
            ('proyecto_relacionado_viaticos', '=', self.proyecto_relacionado_viaticos.id),
            ('state_viaticos', '!=', 'completado')
        ], order='fecha desc')

        if not pendientes:
            raise UserError("No hay trámites pendientes para notificar en este proyecto.")

        # 3. Construir las filas de la tabla HTML
        filas_html = ""
        for p in pendientes:
            funcionario = p.funcionario_2 or p.funcionario_sin_anticipo or "No definido"
            tipo = "Reembolso (FUV)" if p.es_reembolso else "Reintegro (Contratista)" if p.es_reintegro else "Paz y Salvo"
            color_tipo = "#28a745" if p.es_reintegro else "#dc3545" if p.es_reembolso else "#6c757d"
            
            filas_html += f"""
                <tr>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;">{p.fecha or 'S/F'}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee;"><b>{funcionario}</b></td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; text-transform: capitalize;">{p.state_viaticos}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #eee; color: {color_tipo};"><b>{tipo}</b></td>
                </tr>
            """

        # 4. URL apuntando directamente a la vista Form de Contratación
        kanban_url = f"{self.env['ir.config_parameter'].sudo().get_param('web.base.url')}/web#id={self.proyecto_relacionado_viaticos.id}&model=contratacion&view_type=form&view_id={self.env.ref('Matriz.view_contratacion_form_buttons').id}&cids=1"
        COLOR_AZUL = "#043484"

        # 5. Enviar correo a cada coordinador
        for coord in coordinadores:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; color: #333;">
                    <div style="background-color: {COLOR_AZUL}; color: white; padding: 15px; text-align: center; border-radius: 5px 5px 0 0;">
                        <h3 style="margin: 0;">Resumen de legalizaciones pendientes</h3>
                        <p style="margin: 5px 0 0 0;">Proyecto: {self.proyecto_relacionado_viaticos.new_name}</p>
                    </div>
                    <div style="padding: 20px; border: 1px solid #ddd; border-top: none;">
                        <p>Hola, <b>{coord.name}</b>,</p>
                        <p>Se ha generado un reporte de los trámites de viáticos que aún requieren gestión en <b>{self.proyecto_relacionado_viaticos.new_name}</b>:</p>
                        
                        <table style="width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 13px;">
                            <thead>
                                <tr style="background-color: #f2f2f2; text-align: left;">
                                    <th style="padding: 10px; border-bottom: 2px solid {COLOR_AZUL};">Fecha</th>
                                    <th style="padding: 10px; border-bottom: 2px solid {COLOR_AZUL};">Funcionario</th>
                                    <th style="padding: 10px; border-bottom: 2px solid {COLOR_AZUL};">Estado</th>
                                    <th style="padding: 10px; border-bottom: 2px solid {COLOR_AZUL};">Tipo</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filas_html}
                            </tbody>
                        </table>

                        <p style="margin-top: 25px; text-align: center;">
                            <a href="{kanban_url}" 
                               style="background-color: {COLOR_AZUL}; color: white; padding: 12px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; display: inline-block;">
                               Ir al proyecto
                            </a>
                        </p>
                        <hr style="margin-top: 30px; border: 0; border-top: 1px solid #eee;"/>
                        <p style="font-size: 11px; color: #999;">SGO - Sistema de Gestión Odoo</p>
                    </div>
                </div>
            """

            self.env['mail.mail'].sudo().create({
                'subject': f"⚠️ Pendientes FUV: {self.proyecto_relacionado_viaticos.new_name}",
                'body_html': body_html,
                'email_from': user_env.email or 'sgo@tuempresa.com',
                'email_to': coord.email,
            }).send()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Notificación Enviada',
                'message': f'Se envió el resumen de FUV a los coordinadores.',
                'type': 'success',
            }
        }

    # --------------------------------------------------------------------------
    # 1. CAMPO COMPUTADO Y ALMACENADO (Para que FUV lo pueda leer)
    # --------------------------------------------------------------------------
    # Campo neto (el que ya tienes, se mantiene para no romper nada)
    valor_final_presupuesto = fields.Float(
        string="Impacto Neto Presupuesto", 
        compute="_compute_valor_final_presupuesto",
        store=True
    )

    # CAMPOS NUEVOS PARA SEPARAR LA OPERACIÓN
    impacto_positivo_fuv = fields.Float(
        string="Monto a Favor FUV (+)", 
        compute="_compute_valor_final_presupuesto",
        store=True,
        help="Dinero que sobra y regresa al presupuesto."
    )
    
    impacto_negativo_fuv = fields.Float(
        string="Monto en Contra FUV (-)", 
        compute="_compute_valor_final_presupuesto",
        store=True,
        help="Dinero extra que sale del presupuesto."
    )

    @api.depends('state_viaticos', 'reintegro', 'reembolso')
    def _compute_valor_final_presupuesto(self):
        for rec in self:
            # Inicializamos todo en cero
            rec.valor_final_presupuesto = 0.0
            rec.impacto_positivo_fuv = 0.0
            rec.impacto_negativo_fuv = 0.0

            if rec.state_viaticos != 'completado':
                continue

            # --- SEPARACIÓN DE LA OPERACIÓN ---
            
            if rec.reembolso > 0:
                # CASO REEMBOLSO: Entra plata
                rec.impacto_positivo_fuv = rec.reembolso
                rec.valor_final_presupuesto = rec.reembolso  # Positivo
                _logger.info(f"🟢 ID {rec.id}: Detectado Reembolso de {rec.reembolso}")
            
            elif rec.reintegro > 0:
                # CASO REINTEGRO: Sale plata
                rec.impacto_negativo_fuv = rec.reintegro
                rec.valor_final_presupuesto = -rec.reintegro # Negativo
                _logger.info(f"🔴 ID {rec.id}: Detectado Reintegro de {rec.reintegro}")
        

    # --------------------------------------------------------------------------
    # 3. GATILLO DE NOTIFICACIÓN (Corregido: Reintegro=Rojo, Reembolso=Verde)
    # --------------------------------------------------------------------------
    def _notificar_recalculo_fuv_legalizaciones(self):
        for rec in self:
            if rec.state_viaticos != 'completado':
                continue

            # Usamos los campos separados para la lógica del mensaje
            if rec.impacto_negativo_fuv > 0:
                icono = "🔴" 
                label_saldo = "Reintegro (Pago al Contratista)"
                valor_mostrar = rec.impacto_negativo_fuv
                explicacion = "(Gasto extra: sale dinero del saldo disponible)"
            
            elif rec.impacto_positivo_fuv > 0:
                icono = "🟢" 
                label_saldo = "Reembolso (Devolución a la Fundación)"
                valor_mostrar = rec.impacto_positivo_fuv
                explicacion = "(Ahorro: regresa dinero al saldo disponible)"
            
            else:
                icono = "💰"
                label_saldo = "Gasto cubierto por anticipo"
                valor_mostrar = 0.0
                explicacion = "(El gasto legalizado fue exactamente igual al dinero entregado)"
                
                
            mensaje_trazabilidad = (
                f"✅ <b>LEGALIZACIÓN CERRADA</b><br/><br/>"
                f"<div style='background-color: #f8f9fa; padding: 10px; border-radius: 5px; border-left: 5px solid #043484;'>"
                f"<ul>"
                f"<li><b>Valor Anticipo:</b> ${rec.valor_anticipo:,.2f}</li>"
                f"<li><b>Total Facturas:</b> ${rec.total_gastos:,.2f}</li>"
                f"<li><b>{label_saldo}:</b> {icono} ${valor_mostrar:,.2f}</li>"
                f"</ul>"
                f"<hr>"
                f"<b>💵 IMPACTO NETO:</b><br/>"
                f"<span style='font-size: 14px; font-weight: bold; color: #043484;'>${rec.valor_final_presupuesto:,.2f}</span><br/>"
                f"<i>{explicacion}</i>"
                f"</div>"
            )
            
            rec.message_post(body=mensaje_trazabilidad)
                    


    users_with_access = fields.Many2many(
        'res.users', 
        string='Acceso al proyecto', 
        related='proyecto_relacionado_viaticos.users_with_access'
    )

    # --- CAMPOS DE ESTADO ---
    state_viaticos = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('contabilidad_retenciones', 'Auditor Contable - Retenciones'),
        ('presupuestos', 'Presupuestos'),
        ('contabilidad_causacion', 'Auditor Contable - Causación'),
        ('impuestos', 'Impuestos'),
        ('contabilidad_decision', 'Auditor Contable - Decisión final'),
        ('tesoreria', 'Tesorería'),
        # Se conserva para los trámites abiertos del flujo anterior.
        ('contabilidad', 'Auditor Contable (histórico)'),
        ('completado', 'Completado'),
    ], string='Estado', default='coordinador', tracking=True)
    mostrar_estado_historico = fields.Boolean(
        string='Mostrar estado histórico', default=False,
        help='Muestra temporalmente en la barra de estado el paso histórico de Auditoría Contable.'
    )
    fecha_entrada_estado = fields.Datetime(
        string='Entrada al estado', default=fields.Datetime.now, readonly=True, copy=False
    )
    ultima_alerta_estado = fields.Datetime(
        string='Último recordatorio del estado', readonly=True, copy=False
    )

    observaciones_coordinador = fields.Text(string='Observaciones coordinador', tracking=True)
    observaciones_presupuesto = fields.Text(string='Observaciones presupuesto', tracking=True)
    observaciones_auditoria_contable = fields.Text(string='Observaciones contabilidad', tracking=True)
    observaciones_impuestos = fields.Text(string='Observaciones impuestos', tracking=True)
    observaciones_tesoreria = fields.Text(string='Observaciones tesorería', tracking=True)

    rete_iva = fields.Float(string='ReteIVA', tracking=True)
    rete_fuente = fields.Float(string='ReteFuente', tracking=True)
    rete_ica = fields.Float(string='ReteICA', tracking=True)
    estampillas = fields.Float(string='Estampillas', tracking=True,
                               help='Diligenciar únicamente cuando aplique.')
      
    # --- CAMPOS TÉCNICOS ---
    numero_contable = fields.Char(string="Número/Ref Contable", tracking=True)
    numero_contable_sin_anticipo = fields.Char(string="Número/Ref Contable", tracking=True)
    
    es_contabilidad_check = fields.Boolean(
        string="Es Contabilidad", 
        compute="_compute_verificar_rol_contabilidad",
        store=False 
    )

    @api.depends_context('uid')
    def _compute_verificar_rol_contabilidad(self):
        # Verificamos el grupo una sola vez fuera del bucle para optimizar
        tiene_rol = self.env.user.has_group('Matriz.group_contabilidad')
        
        for record in self:
            # En un compute, SIEMPRE hay que asignar un valor (True o False)
            if tiene_rol:
                record.es_contabilidad_check = True
            else:
                record.es_contabilidad_check = False
    
    doc_causacion_id = fields.Many2one('ir.attachment', string="Documento de Causación")
    drive_file_id = fields.Char(string="ID Carpeta Google Drive")

    # --- DATOS GENERALES ---
    fecha = fields.Date(string='Fecha de legalización', tracking=True)
    
    funcionario_2 = fields.Char(string='Funcionario', related='anticipo_id.nombre_completo', store=True)
    
    funcionario_sin_anticipo = fields.Char(string='Funcionario')
    
    
    # Este es el campo que usaremos en correos y vistas
    funcionario_final = fields.Char(string='Funcionario', compute='_compute_funcionario_final', store=True)

    @api.depends('tiene_anticipo', 'funcionario_2', 'funcionario_sin_anticipo')
    def _compute_funcionario_final(self):
        for rec in self:
            if rec.tiene_anticipo == 'si':
                rec.funcionario_final = rec.funcionario_2
            else:
                rec.funcionario_final = rec.funcionario_sin_anticipo
                
                
    cargo_2 = fields.Char(string='Cargo', related='anticipo_id.cargo_2', store=True)         
    cargo_id = fields.Many2one('cargo_tag', string='Cargo', store=True)
    cargo_sin_anticipo = fields.Many2one('cargo_tag', string='Cargo', tracking=True)
    
    proyecto = fields.Char(string='Proyecto', related='proyecto_relacionado_viaticos.new_name', store=True)
    centro_costo = fields.Char(string="Centro de costo", related='proyecto_relacionado_viaticos.mcb_centro_costo', store=True)
    
    tiene_anticipo = fields.Selection([
        ('si', 'Sí'),
        ('no', 'No'),
    ], string="¿Tiene anticipo?", required=True, placeholder="Seleccionar opción", tracking=True)
            
    tipo_persona = fields.Selection([
        ('pn', 'Persona Natural (PN)'),
        ('pj', 'Persona Jurídica (PJ)'),
    ], string='Tipo de solicitante', related='anticipo_id.person_type')

    persona_sin_anticipo = fields.Selection([
        ('pn', 'Persona Natural (PN)'),
        ('pj', 'Persona Jurídica (PJ)'),
    ], string='Tipo de solicitante', tracking=True)
    
    # --- CÁLCULOS FINANCIEROS ---
    total_gastos = fields.Float(string="Total Gastos", compute="_compute_total_gastos", store=True, tracking=True)
    
    @api.depends('line_ids.valor', 'tiene_anticipo','anticipo_id','valor_anticipo','es_reintegro','es_reembolso','reembolso','reintegro')
    def _compute_total_gastos(self):
        for rec in self:
            rec.total_gastos = sum(line.valor for line in rec.line_ids)
            #print(f"🔍 Cálculo total gastos: {rec.total_gastos} (tiene anticipo: {rec.tiene_anticipo})")
            
    valor_anticipo = fields.Float(string="Valor anticipo", related='anticipo_id.valor_anticipo', store=True)
        
    @api.depends('line_ids.valor', 'total_gastos', 'valor_anticipo', 'state_viaticos', 'fecha', 'b_proyecto', 'contratista')
    def _compute_reintegro_reembolso(self):
        for rec in self:
            # Aseguramos valores numéricos
            total = rec.total_gastos or 0.0
            anticipo = rec.valor_anticipo or 0.0
            diferencia = total - anticipo
            
            # Asignación de montos numéricos
            rec.reintegro = diferencia if diferencia > 0 else 0.0
            rec.reembolso = abs(diferencia) if diferencia < 0 else 0.0
            
            # Booleanos
            rec.es_reintegro = rec.reintegro > 0
            
            # --- NUEVA LÓGICA DE REEMBOLSO ---
            # Condición 1: Diferencia negativa y total mayor a 0 (Tu original)
            condicion_1 = (diferencia < 0 and total > 0)
            
            # Condición 2: El campo reembolso tiene dinero y reintegro está en 0
            condicion_2 = (rec.reembolso > 0 and rec.reintegro == 0)
            
            # Si se cumple la 1 O la 2, entonces es reembolso (True)
            rec.es_reembolso = condicion_1 or condicion_2
            rec.sin_novedad = (diferencia == 0)
            

    reembolso = fields.Float(string="Reembolso (Devolución a FUV)", compute="_compute_reintegro_reembolso", store=True)
    reintegro = fields.Float(string="Reintegro (Pago a Contratista)", compute="_compute_reintegro_reembolso", store=True)
    
    sin_novedad = fields.Boolean(string="Sin novedad (Gasto igual al anticipo)", compute="_compute_reintegro_reembolso", store=True)
    es_reembolso = fields.Boolean(compute="_compute_reintegro_reembolso", store=True)
    es_reintegro = fields.Boolean(compute="_compute_reintegro_reembolso", store=True)
    # --- OTROS CAMPOS ORIGINALES ---
    fecha_solicitud = fields.Date(string='Fecha de solicitud', tracking=True)
    observaciones = fields.Text(string='Observaciones', tracking=True)
    b_proyecto = fields.Boolean(string="Proyecto", tracking=True)
    contratista = fields.Boolean(string="Contratista", tracking=True)
    proceso_en_ejecucion = fields.Boolean(default=False, tracking=True)
    line_ids = fields.One2many('viaticos_linea', 'viatico_id', string='Gastos')

     
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓       

        
        
    @api.model
    def create(self, vals):
        user = self.env.user

        # La primera etapa empieza al crear la legalización; no depende de
        # cambios posteriores de otros campos del formulario.
        vals['fecha_entrada_estado'] = fields.Datetime.now()
        vals['ultima_alerta_estado'] = False

        if not user.has_group('Matriz.group_contratacion_coordinador'):
            raise UserError(
                "🚫 No tienes permisos para crear legalizaciones.\n\n"
                "Solo el Coordinador puede crear este registro."
            )

        # 1. Certificamos la condición según el campo de selección
        if vals.get('tiene_anticipo') == 'si':
            # Caso con anticipo: Tomamos funcionario_2
            nombre_referencia = vals.get('funcionario_2')

            # Respaldo: Si funcionario_2 no viene en vals (por ser related),
            # lo consultamos directamente del registro de anticipos
            if not nombre_referencia and vals.get('anticipo_id'):
                anticipo = self.env['anticipos'].browse(vals.get('anticipo_id'))
                nombre_referencia = anticipo.nombre_completo
        else:
            # Caso sin anticipo: Tomamos el nombre manual
            nombre_referencia = vals.get('funcionario_sin_anticipo')

        # 2. Respaldo de seguridad si ambos campos están vacíos
        if not nombre_referencia:
            nombre_referencia = f"Folder_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 3. Creación del registro manteniendo los datos originales en vals
        record = super(Viatico, self).create(vals)

        # 4. Gestión de carpetas
        if hasattr(record, 'buscar_carpeta'):
            record.buscar_carpeta()

        return record

    
    def write(self, vals):
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            return True

        # Solo una transición real reinicia el reloj y los recordatorios.
        if ('state_viaticos' in vals and
                any(record.state_viaticos != vals['state_viaticos'] for record in self)):
            vals = dict(vals)
            vals['fecha_entrada_estado'] = fields.Datetime.now()
            vals['ultima_alerta_estado'] = False

        # QUE HACE: Obtiene el usuario que esta haciendo la modificacion.
        user = self.env.user

        # QUE HACE: Guarda los valores antes de escribir, para comparar despues
        # si la liquidacion cambio y registrar esos cambios en el chatter.
        pre_values = {
            rec.id: {
                'total_gastos': rec.total_gastos,
                'valor_anticipo': rec.valor_anticipo,
                'reembolso': rec.reembolso,
                'reintegro': rec.reintegro,
            } for rec in self
        }

        # QUE HACE: Lista de campos basicos que solo pueden modificar
        # Coordinacion o Asistencia de Coordinacion.
        campos_coordinacion = [
            'tiene_anticipo',
            'anticipo_id',
            'funcionario_sin_anticipo',
            'cargo_sin_anticipo',
            'fecha',
            'persona_sin_anticipo',
            'nit_funcionario_s_anticipo',
            'cedula_funcionario_s_anticipo',
            'ciudad',
        ]

        # QUE HACE: Valida si el usuario pertenece a los grupos autorizados
        # para modificar los campos basicos de coordinacion.
        es_gestor_coord = user.has_group('Matriz.group_contratacion_coordinador') or \
                        user.has_group('Matriz.group_contratacion_asis_coordinador')

        # QUE HACE: Valida si el usuario pertenece al grupo de Presupuestos.
        es_presupuestos = user.has_group('Matriz.group_presupuestos')
        es_contabilidad = user.has_group('Matriz.group_contabilidad')
        campos_retenciones = {'rete_iva', 'rete_fuente', 'rete_ica', 'estampillas'}
        if ({'rpc_legalizacion_document_id', 'rpc_pj_legalizacion_document_id'}.intersection(vals)
                and not es_presupuestos and not self.env.su):
            raise AccessError('Solo Presupuestos puede modificar el documento RPC de la legalización.')

        # QUE HACE: Recorre los registros que se van a modificar y aplica
        # las reglas de seguridad antes de guardar.
        for record in self:
            if (record.state_viaticos == 'presupuestos' and
                    vals.get('state_viaticos') == 'contabilidad_causacion'):
                record._validar_rpc_presupuestos(vals)
            # QUE HACE: Impide editar legalizaciones cerradas, salvo cuando
            # el cambio sea precisamente sobre el estado.
            if record.state_viaticos in ['completado', 'finalizado'] and not vals.get('state_viaticos'):
                raise ValidationError("❌ No puedes editar una legalización que ya ha sido CERRADA.")

            # QUE HACE: Si se intenta cambiar un campo basico, verifica que
            # el usuario sea Coordinacion o Asistencia de Coordinacion.
            if any(campo in vals for campo in campos_coordinacion):
                if not es_gestor_coord:
                    raise ValidationError(
                        "🚫 Los datos básicos de la legalización solo pueden ser modificados "
                        "por Coordinación o Asistencia de Coordinación."
                    )

            if campos_retenciones.intersection(vals) and not es_contabilidad:
                raise ValidationError(
                    "🚫 ReteIVA, ReteFuente, ReteICA y Estampillas solo pueden ser modificados por Auditoría Contable."
                )

            if 'mostrar_estado_historico' in vals and not user.has_group('base.group_system'):
                raise ValidationError(
                    "🚫 Solo un administrador puede mostrar el estado histórico."
                )

            # QUE HACE: Si se intenta cambiar el RPC, verifica que el usuario
            # pertenezca al area de Presupuestos.
            if {'name_rpc', 'rpc_legalizacion', 'rpc_legalizacion_filename',
                    'rpc_pj_legalizacion', 'rpc_pj_legalizacion_filename'}.intersection(vals):
                if not es_presupuestos:
                    raise ValidationError(
                        "🚫 El campo 'RPC' solo puede ser modificado por el área de Presupuestos."
                    )

            # QUE HACE: Para usuarios de Contabilidad, exige numero contable
            # si el registro no esta marcado para ignorar esa validacion.
            if user.has_group("Matriz.group_contabilidad") and not record.ignorar_contabilidad:
                # QUE HACE: Toma el nuevo numero contable desde vals si viene
                # en la escritura; si no viene, usa el valor actual del registro.
                nuevo_valor = vals.get('numero_contable', record.numero_contable)
                nuevo_valor_sin_anticipo = vals.get(
                    'numero_contable_sin_anticipo',
                    record.numero_contable_sin_anticipo
                )

                # QUE HACE: Si la legalizacion tiene anticipo, obliga a llenar
                # el campo numero_contable.
                if record.tiene_anticipo == 'si' and not nuevo_valor:
                    raise ValidationError(
                        "⚠️ El campo 'Número Contable' es obligatorio para Contabilidad."
                    )

                # QUE HACE: Si la legalizacion no tiene anticipo, obliga a
                # llenar numero_contable_sin_anticipo.
                if record.tiene_anticipo == 'no' and not nuevo_valor_sin_anticipo:
                    raise ValidationError(
                        "⚠️ El campo 'Número Contable' es obligatorio para Contabilidad."
                    )

            # QUE HACE: Valida si el usuario pertenece al grupo de Impuestos.
            es_impuestos = user.has_group("Matriz.group_contratacion_impuestos")
            # QUE HACE: Si el documento esta en revision fiscal, solo Impuestos
            # puede editarlo, salvo que el cambio sea de estado.
            if record.state_viaticos == 'impuestos' and not es_impuestos and not vals.get('state_viaticos'):
                raise ValidationError(
                    "🚫 El documento está en revisión fiscal. Solo el área de IMPUESTOS puede editarlo."
                )

        # QUE HACE: Ejecuta el guardado real en Odoo despues de pasar todas
        # las validaciones anteriores.
        res = super(Viatico, self).write(vals)

        # QUE HACE: Recorre nuevamente los registros para registrar en el
        # chatter si hubo cambios automaticos en la liquidacion.
        for rec in self:
            # QUE HACE: Recupera los valores que tenia el registro antes
            # del guardado.
            old = pre_values.get(rec.id)

            # QUE HACE: Aqui se almacenan las lineas HTML que se van a publicar
            # si alguno de los valores financieros cambio.
            tracking_notes = []

            # QUE HACE: Define los campos financieros que se van a comparar
            # antes y despues del guardado.
            fields_to_log = [
                ('total_gastos', 'Total Gastos'),
                ('valor_anticipo', 'Valor Anticipo'),
                ('reembolso', 'Reembolso (FUV)'),
                ('reintegro', 'Reintegro (Contratista)')
            ]

            # QUE HACE: Compara cada campo financiero con su valor anterior.
            # Si cambio, agrega una linea para el mensaje del chatter.
            for field, label in fields_to_log:
                old_val = old.get(field, 0.0)
                new_val = getattr(rec, field)
                if old_val != new_val:
                    tracking_notes.append(
                        f"<li><b>{label}:</b> ${old_val:,.2f} ➜ <b>${new_val:,.2f}</b></li>"
                    )

            # QUE HACE: Solo crea y publica el mensaje si realmente hubo cambios.
            # Esto corrige el RPC_ERROR: antes message_post se ejecutaba aunque
            # body no existiera cuando tracking_notes estaba vacio.
            if tracking_notes:
                body = (
                    "<div style='font-size: 13px;'>"
                    "   <i class='fa fa-calculator' style='color: #714B67;'></i> "
                    "   <b>Recálculo automático de liquidación:</b>"
                    f"   <ul style='margin-top: 5px;'>{''.join(tracking_notes)}</ul>"
                "</div>"
                )
                rec.message_post(body=body)

        # QUE HACE: Si la legalizacion pasa a completado, dispara la notificacion
        # relacionada con el recalculo del FUV.
        if vals.get('state_viaticos') == 'completado':
            self._notificar_recalculo_fuv_legalizaciones()

        # QUE HACE: Devuelve el resultado del write original de Odoo.
        return res
    
    
    
    
    def unlink(self):
        for record in self:
            if record.drive_file_id:
                try: record.drive_file_id.unlink()
                except: pass
        return super(Viatico, self).unlink()
    


    
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ 
    
    
    
    
    name_rpc = fields.Char(string="Número RPC", tracking=True)

    def _validar_rpc_presupuestos(self, valores=None):
        """Exige el RPC al avanzar, sin impedir borradores ni devoluciones."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_presupuestos'):
            raise UserError('Solo un usuario con rol de Presupuestos puede aprobar esta etapa.')
        valores = valores or {}
        numero = valores.get('name_rpc', self.name_rpc)
        tipo = self.tipo_persona if self.tiene_anticipo == 'si' else self.persona_sin_anticipo
        if tipo not in ('pn', 'pj'):
            raise UserError('No se puede validar el RPC: falta definir el tipo de persona de la legalización.')
        prefijo = 'rpc_legalizacion' if tipo == 'pn' else 'rpc_pj_legalizacion'
        documento = valores.get(prefijo + '_document_id', getattr(self, prefijo + '_document_id'))
        archivo = valores.get(prefijo, getattr(self, prefijo))
        faltantes = []
        if not numero or not numero.strip():
            faltantes.append('Número RPC')
        if not documento and not archivo:
            faltantes.append('Documento RPC de persona natural' if tipo == 'pn' else 'Documento RPC de persona jurídica')
        if faltantes:
            raise UserError(
                'No se puede aprobar Presupuestos. Debes completar:\n• ' + '\n• '.join(faltantes)
            )
         
    def action_aprobar_presupuestos(self):
        """Pasa de Presupuestos a Auditoría Contable para causación."""
        self.ensure_one()
        self._validar_estado_boton('presupuestos', 'Aprobar Presupuestos')
        if not self.env.user.has_group('Matriz.group_presupuestos'):
            raise UserError("🚫 Solo Presupuestos puede aprobar esta etapa.")
        self._validar_rpc_presupuestos()
        # --- VALIDACIÓN DINÁMICA DE DOCUMENTOS ---
        missing_docs = []
        
        #INCLUIR EL CAMPO RPC
        # Validación para Persona Natural (PN)
        # if self.tipo_persona == 'pn' or self.persona_sin_anticipo == 'pn':
        #     if not self.rut_pn_document_id:
        #         missing_docs.append("RUT")

        # # Validación para Persona Jurídica (PJ)
        # elif self.tipo_persona == 'pj' or self.persona_sin_anticipo == 'pj':
        #     if not self.rut_pj_document_id: 
        #         missing_docs.append("RUT")


        # if missing_docs:
        #     # Crea un mensaje listando solo los que faltan
        #     docs_text = "\n• ".join(missing_docs)
        #     raise UserError(
        #         f"⚠️ ¡Atención! No se puede procesar la aprobación.\n\n"
        #         f"Aún faltan los siguientes documentos obligatorios:\n"
        #         f"• {docs_text}\n\n"
        #         f"Por favor, adjunta los archivos faltantes para poder continuar con el proceso."
        #     )
            

        # 1. Obtener el grupo de contabilidad
        grupo_contabilidad = self.env.ref('Matriz.group_contabilidad')

        # 2. Obtener usuarios DESDE el proyecto filtrando por el grupo
        usuarios_contabilidad = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda user: grupo_contabilidad in user.groups_id
        )

        # 3. Validación de existencia de usuarios
        if not usuarios_contabilidad:
            raise UserError(f"No hay usuarios con el rol de Contabilidad asignados al acceso del proyecto: {self.proyecto_relacionado_viaticos.new_name}")

        presupuestos = self.env.user
        self._validar_notificacion(usuarios_contabilidad, 'Auditoría Contable - Causación', presupuestos)
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        observaciones = self.observaciones_presupuesto or ''

        #Nuevo perfil para el funcionario
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        
        # Construir URL del formulario
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"

        #Nuevo campo para el número RPC
        
        # Cargar datos comunes
 
        # Opción segura si sospechas que el campo puede variar entre registro y texto
        cargo_nombre = 'Sin cargo'

        if self.cargo_2:
            if isinstance(self.cargo_2, str):
                cargo_nombre = self.cargo_2
            else:
                cargo_nombre = self.cargo_2.display_name
        elif self.cargo_sin_anticipo:
            cargo_nombre = self.cargo_sin_anticipo.display_name
    
    
        COLOR_PRINCIPAL = "#043484"

        # --- LÓGICA DINÁMICA DE SALDO Y RESALTADO ---
        linea_saldo = ""
        
        # Estilo para resaltar: fondo amarillo (#ffff00)
        estilo_resaltado = "background-color: #ffff00; padding: 2px 5px; border-radius: 3px; color: #000;"

        if self.reembolso > 0:
            linea_saldo = f"<strong>Reembolso:</strong> <span style='{estilo_resaltado}'>${self.reembolso:,.2f}</span><br/>"
        elif self.reintegro > 0:
            linea_saldo = f"<strong>Reintegro:</strong> <span style='{estilo_resaltado}'>${self.reintegro:,.2f}</span><br/>"
        else:
            # Opcional: Si ambos son cero
            linea_saldo = "<strong>Saldo:</strong> $0.00<br/>"

        # Enviar correo a cada usuario filtrado
        for usuario in usuarios_contabilidad:
            nombre_usuario_contabilidad = usuario.name

            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Aprobación de legalización: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{nombre_usuario_contabilidad}</strong>:</p>

                        <p><strong>{presupuestos.name}</strong> ha aprobado la legalización para:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Solicitante:</strong> {funcionario}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            
                            <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                            {linea_saldo}
                        </div>

                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                            <strong>Observaciones:</strong><br/>
                            <em style="color: #856404;">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; 
                                color: white; 
                                padding: 10px 20px; 
                                text-decoration: none; 
                                border-radius: 5px; 
                                font-weight: bold; 
                                display: inline-block;">
                                Ir al formulario de legalización
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            SGO - mensaje de notificación
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"Aprobación de legalización para: {funcionario} del proyecto; {nombre_proyecto}",
                'email_from': presupuestos.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        self.write({'state_viaticos': 'contabilidad_causacion'})

        return self._return_notificacion_reload(
            'Mensaje enviado',
            f'Se notificó a {len(usuarios_contabilidad)} usuario con rol de auditor contable con acceso al proyecto.'
        )

    def action_enviar_retenciones_a_presupuestos(self):
        """Envía la revisión inicial de retenciones a Presupuestos."""
        self.ensure_one()
        self._validar_estado_boton('contabilidad_retenciones', 'Enviar a Presupuestos')
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise UserError("🚫 Solo Auditoría Contable puede enviar la revisión de retenciones.")

        grupo_presupuesto = self.env.ref('Matriz.group_presupuestos')
        destinatarios = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda user: grupo_presupuesto in user.groups_id
        )
        if not destinatarios:
            raise UserError("No hay usuarios de Presupuestos asignados a este proyecto.")

        auditor = self.env.user
        self._validar_notificacion(destinatarios, 'Presupuestos', auditor)
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        for destinatario in destinatarios.filtered('email'):
            self.env['mail.mail'].sudo().create({
                'subject': f"Retenciones por revisar: {funcionario} - {nombre_proyecto}",
                'email_from': auditor.email,
                'email_to': destinatario.email,
                'body_html': self._formato_correo_legalizacion(
                    destinatario, 'Revisión de retenciones · Presupuestos',
                    f'{auditor.name}, de Auditoría Contable, envió la revisión de retenciones para tu gestión en Presupuestos.',
                    url=formulario_url, mostrar_retenciones=True,
                ),
                'auto_delete': True,
            }).send()

        self.write({'state_viaticos': 'presupuestos'})
        self.message_post(body=(
            "<b>Revisión de retenciones enviada a Presupuestos.</b><br/>"
            f"ReteIVA: ${self.rete_iva:,.2f} · ReteFuente: ${self.rete_fuente:,.2f} · "
            f"ReteICA: ${self.rete_ica:,.2f} · Estampillas: ${self.estampillas:,.2f}"
        ))
        return self._return_notificacion_reload(
            'Mensaje enviado',
            f'Se envió la legalización a {len(destinatarios)} usuario(s) de Presupuestos.'
        )
        

    def aprobar_coordinador_a_presupuesto(self):
        #_logger.info("Se ejecuto aprobar_coordinador_a_presupuesto")
        self.ensure_one()
        self._validar_estado_boton('coordinador', 'Enviar a Presupuestos')
        # --- VALIDACIÓN DINÁMICA DE DOCUMENTOS ---
        missing_docs = []
        # Validación para Persona Natural (PN)
        if self.tipo_persona == 'pn' or self.persona_sin_anticipo == 'pn':
            if not self.rut_pn_document_id:
                missing_docs.append("RUT")
            if not self.doc_identidad_document_id:
                missing_docs.append("Documento de identidad")
            if not self.cert_bancaria_pn_document_id:
                missing_docs.append("Certificación bancaria")

        # Validación para Persona Jurídica (PJ)
        elif self.tipo_persona == 'pj' or self.persona_sin_anticipo == 'pj':
            if not self.rut_pj_document_id: 
                missing_docs.append("RUT")
            if not self.cert_bancaria_pj_document_id:
                missing_docs.append("Certificación bancaria")
                
        if missing_docs:
            # Crea un mensaje listando solo los que faltan
            docs_text = "\n• ".join(missing_docs)
            raise UserError(
                f"⚠️ ¡Atención! No se puede procesar la aprobación.\n\n"
                f"Aún faltan los siguientes documentos obligatorios:\n"
                f"• {docs_text}\n\n"
                f"Por favor, adjunta los archivos faltantes para poder continuar con el proceso."
            )
            
            
        #Obtener usuarios del proyecto con el grupo de presupuestos
        grupo_presupuesto = self.env.ref('Matriz.group_presupuestos')
        # Filtrar usuarios del proyecto que tienen el grupo de presupuestos
        usuarios_presupuesto = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda user: grupo_presupuesto in user.groups_id
        )
        
        #Validación de existencia de usuarios
        if not usuarios_presupuesto:
            raise UserError(f"No hay usuarios con el rol de Presupuestos asignados al acceso del proyecto: {self.proyecto_relacionado_viaticos.new_name}")      
        
        presupuestos = self.env.user
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        observaciones = self.observaciones_coordinador or ''
        
        #Nuevo perfil para el funcionario
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        
        # Construir URL del formulario
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        
         # Cargar datos comunes
        
        # Opción segura si sospechas que el campo puede variar entre registro y texto
        cargo_nombre = 'Sin cargo'

        if self.cargo_2:
            if isinstance(self.cargo_2, str):
                cargo_nombre = self.cargo_2
            else:
                cargo_nombre = self.cargo_2.display_name
        elif self.cargo_sin_anticipo:
            cargo_nombre = self.cargo_sin_anticipo.display_name
    
    
        COLOR_PRINCIPAL = "#043484"
        COLOR_INFORMATIVO = "#EFE38A"

        # --- LÓGICA DINÁMICA DE SALDO Y RESALTADO ---
        linea_saldo = ""
        
        # Estilo para resaltar: fondo amarillo (#ffff00)
        estilo_resaltado = "background-color: #ffff00; padding: 2px 5px; border-radius: 3px; color: #000;"

        if self.reembolso > 0:
            linea_saldo = f"<strong>Reembolso:</strong> <span style='{estilo_resaltado}'>${self.reembolso:,.2f}</span><br/>"
        elif self.reintegro > 0:
            linea_saldo = f"<strong>Reintegro:</strong> <span style='{estilo_resaltado}'>${self.reintegro:,.2f}</span><br/>"
        else:
            # Opcional: Si ambos son cero
            linea_saldo = "<strong>Saldo:</strong> $0.00<br/>"

        # Enviar correo a cada usuario filtrado
        for usuario in usuarios_presupuesto:
            nombre_usuarios_presupuesto = usuario.name

            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Aprobación de legalización: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{nombre_usuarios_presupuesto}</strong>:</p>

                        <p>El coordinador <strong>{presupuestos.name}</strong> ha aprobado la legalización para:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Solicitante:</strong> {funcionario}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                            {linea_saldo}
                        </div>
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_INFORMATIVO};">
                            <strong>El funcionario:</strong> {funcionario} (No cuenta con anticipo previo).<br/>
                        </div>
                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                            <strong>Observaciones:</strong><br/>
                            <em style="color: #856404;">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; 
                                color: white; 
                                padding: 10px 20px; 
                                text-decoration: none; 
                                border-radius: 5px; 
                                font-weight: bold; 
                                display: inline-block;">
                                Ir al formulario de legalización
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            SGO - mensaje de notificación
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"Aprobación de legalización para: {funcionario} del proyecto; {nombre_proyecto}",
                'email_from': presupuestos.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        self.write({'state_viaticos': 'presupuestos'})

        return self._return_notificacion_reload(
            'Mensaje enviado',
            f'Se notificó a {len(usuarios_presupuesto)} usuario con rol de presupuesto con acceso al proyecto.'
        )
        
    
    
    
    
    # --- BOTONES DE APROBACIÓN ESPECÍFICOS ---
    # COORDINADOR A CONTABILIDAD
    def action_aprobar_coordinador(self):
        """Pasa de Coordinador a Contabilidad"""
        self.ensure_one()

        self._validar_estado_boton('coordinador', 'Aprobar Coordinador')
        if not (self.env.user.has_group('Matriz.group_contratacion_coordinador') or
                self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')):
            raise UserError("🚫 Solo Coordinación o Asistencia de Coordinación puede aprobar esta etapa.")
        
        # --- VALIDACIÓN DINÁMICA DE DOCUMENTOS ---
        missing_docs = []
        # Validación para Persona Natural (PN)
        if self.tipo_persona == 'pn' or self.persona_sin_anticipo == 'pn':
            if not self.rut_pn_document_id:
                missing_docs.append("RUT")
            if not self.doc_identidad_document_id:
                missing_docs.append("Documento de identidad")
            if not self.cert_bancaria_pn_document_id:
                missing_docs.append("Certificación bancaria")

        # Validación para Persona Jurídica (PJ)
        elif self.tipo_persona == 'pj' or self.persona_sin_anticipo == 'pj':
            if not self.rut_pj_document_id: 
                missing_docs.append("RUT")
            if not self.cert_bancaria_pj_document_id:
                missing_docs.append("Certificación bancaria")



        if missing_docs:
            # Crea un mensaje listando solo los que faltan
            docs_text = "\n• ".join(missing_docs)
            raise UserError(
                f"⚠️ ¡Atención! No se puede procesar la aprobación.\n\n"
                f"Aún faltan los siguientes documentos obligatorios:\n"
                f"• {docs_text}\n\n"
                f"Por favor, adjunta los archivos faltantes para poder continuar con el proceso."
            )
            
        #_logger.info("Se ejecuto action_aprobar_coordinador el que va a contabilidad")
        # 1. Obtener el grupo de contabilidad
        grupo_contabilidad = self.env.ref('Matriz.group_contabilidad')

        # 2. Obtener usuarios DESDE el proyecto filtrando por el grupo
        usuarios_contabilidad = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda user: grupo_contabilidad in user.groups_id
        )

        # 3. Validación de existencia de usuarios
        if not usuarios_contabilidad:
            raise UserError(f"No hay usuarios con el rol de Contabilidad asignados al acceso del proyecto: {self.proyecto_relacionado_viaticos.new_name}")

        coordinador = self.env.user
        self._validar_notificacion(usuarios_contabilidad, 'Auditoría Contable - Retenciones', coordinador)
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        observaciones = self.observaciones_coordinador or ''

        #Nuevo perfil para el funcionario
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        
        # Construir URL del formulario
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"

        # Cargar datos comunes

        # Opción segura si sospechas que el campo puede variar entre registro y texto
        cargo_nombre = 'Sin cargo'

        if self.cargo_2:
            if isinstance(self.cargo_2, str):
                cargo_nombre = self.cargo_2
            else:
                cargo_nombre = self.cargo_2.display_name
        elif self.cargo_sin_anticipo:
            cargo_nombre = self.cargo_sin_anticipo.display_name
        COLOR_PRINCIPAL = "#043484"

        # --- LÓGICA DINÁMICA DE SALDO Y RESALTADO ---
        linea_saldo = ""
        
        # Estilo para resaltar: fondo amarillo (#ffff00)
        estilo_resaltado = "background-color: #ffff00; padding: 2px 5px; border-radius: 3px; color: #000;"

        if self.reembolso > 0:
            linea_saldo = f"<strong>Reembolso:</strong> <span style='{estilo_resaltado}'>${self.reembolso:,.2f}</span><br/>"
        elif self.reintegro > 0:
            linea_saldo = f"<strong>Reintegro:</strong> <span style='{estilo_resaltado}'>${self.reintegro:,.2f}</span><br/>"
        else:
            # Opcional: Si ambos son cero
            linea_saldo = "<strong>Saldo:</strong> $0.00<br/>"

        # Enviar correo a cada usuario filtrado
        for usuario in usuarios_contabilidad:
            nombre_usuario_contabilidad = usuario.name

            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Aprobación de legalización: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{nombre_usuario_contabilidad}</strong>:</p>

                        <p>El coordinador <strong>{coordinador.name}</strong> ha aprobado la legalización para:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Solicitante:</strong> {funcionario}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                            {linea_saldo}
                        </div>

                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid #ffcc00; background-color: #fffacd; border-radius: 4px;">
                            <strong>Observaciones:</strong><br/>
                            <em style="color: #856404;">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; 
                                color: white; 
                                padding: 10px 20px; 
                                text-decoration: none; 
                                border-radius: 5px; 
                                font-weight: bold; 
                                display: inline-block;">
                                Ir al formulario de legalización
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            SGO - mensaje de notificación
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"Aprobación de legalización para: {funcionario} del proyecto; {nombre_proyecto}",
                'email_from': coordinador.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        self.write({'state_viaticos': 'contabilidad_retenciones'})

        return self._return_notificacion_reload(
            'Mensaje enviado',
            f'Se notificó a {len(usuarios_contabilidad)} usuario con rol de auditor contable con acceso al proyecto.'
        )
        
        
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓       

    validado_por_impuestos = fields.Boolean(string="Validado por Impuestos", default=False)

    def action_aprobar_contabilidad(self):
        """
        Registra la causación y envía la legalización a Impuestos.
        """
        self.ensure_one()
        if self.state_viaticos not in ('contabilidad_causacion', 'contabilidad'):
            self._validar_estado_boton('contabilidad_causacion', 'Enviar a Impuestos')
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise UserError("🚫 Solo Auditoría Contable puede registrar la causación.")
        
        # --- 1. VALIDACIÓN NÚMERO CONTABLE CON ENFOQUE ---
        numero_contable = (
            self.numero_contable
            if self.tiene_anticipo == 'si'
            else self.numero_contable_sin_anticipo
        )
        if not numero_contable:
            # Al usar ValidationError, el web client de Odoo resalta el formulario.
            # Para asegurar el scroll, el sistema avisará qué campo falta.
            raise ValidationError("⚠️ CAMPO OBLIGATORIO: Debe asignar el 'Número Contable' antes de continuar.\n"
                                 "Por favor, diligencie este campo en la sección de Información de la legalización.")

        # Lógica de nombre (Tu OR sugerido)
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        
        # --- 2. VALIDACIÓN DE DOCUMENTOS ---
        missing_docs = []
        # Usamos los campos técnicos que certifican el tipo de persona
        tipo = self.tipo_persona or self.persona_sin_anticipo
        if tipo == 'pn':
            if not self.documento_de_causacion_id: missing_docs.append("Causación PN")
        elif tipo == 'pj':
            if not self.documento_de_causacion_pj_id: missing_docs.append("Causación PJ")

        if missing_docs:
            docs_text = "\n• ".join(missing_docs)
            raise UserError(f"⚠️ ¡Atención Auditor! Faltan documentos obligatorios:\n• {docs_text}")

        # --- 3. DESTINO FIJO: después de la causación siempre revisa Impuestos ---
        xml_id_grupo = 'Matriz.group_contratacion_impuestos'
        nombre_etapa = "Equipo de Impuestos"
        estado_destino = 'impuestos'
        asunto_mail = f"Validar retenciones: {funcionario} - {self.proyecto_relacionado_viaticos.display_name}"
        texto_accion_mail = "tu validación de las obligaciones tributarias"

        # --- 4. OBTENER USUARIOS Y PREPARAR CORREO ---
        grupo_destino = self.env.ref(xml_id_grupo, raise_if_not_found=False)
        usuarios_destino = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda user: grupo_destino in user.groups_id
        )

        if not usuarios_destino:
            raise UserError(f"No hay usuarios de {nombre_etapa} asignados al proyecto.")

        auditor = self.env.user
        self._validar_notificacion(usuarios_destino, nombre_etapa, auditor)
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        
        # REQUERIMIENTO 3: Agregar observaciones al cuerpo
        observaciones = getattr(self, 'observaciones_auditoria_contable', '') or ''
        
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        COLOR_PRINCIPAL = "#043484"

        # Lógica de saldo
        estilo_resaltado = "background-color: #ffff00; padding: 2px 5px; border-radius: 3px; color: #000;"

        linea_saldo = (
            f"<strong>Saldo Reembolso:</strong> <span style='{estilo_resaltado}'>${self.reembolso:,.2f}</span>" 
            if self.reembolso > 0 
            else f"<strong>Saldo Reintegro:</strong> <span style='{estilo_resaltado}'>${self.reintegro:,.2f}</span>"
        )

        for usuario in usuarios_destino:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Notificación de trámite: <strong>{nombre_proyecto}</strong></h3>
                    </div>
                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{usuario.name}</strong> ({nombre_etapa}):</p>
                        <p>El Auditor Contable <strong>{auditor.name}</strong> ha verificado los soportes y solicita <strong>{texto_accion_mail}</strong> para:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Beneficiario:</strong> {funcionario}<br/>
                            <strong>No. Contable:</strong> {numero_contable}<br/>
                            {linea_saldo}
                        </div>

                        {f'''
                        <div style="margin-top: 20px; padding: 12px; border: 1px solid #17a2b8; background-color: #e2f7fb; border-radius: 4px;">
                            <strong style="color: #043484;">Observaciones de Auditoría Contable:</strong><br/>
                            <em style="color: #0c5460;">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}

                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Revisar
                            </a>
                        </p>
                    </div>
                </div>
            """
            self.env['mail.mail'].sudo().create({
                'subject': asunto_mail,
                'email_from': auditor.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        self.write({'state_viaticos': estado_destino})

        return self._return_notificacion_reload(
            'Mensaje enviado',
            f'La legalización fue enviada a {nombre_etapa}.'
        )


    ignorar_contabilidad = fields.Boolean(string="Ignorar contabilidad", default=True)

    def action_devolver_presupuestos_a_retenciones(self):
        """Permite a Presupuestos devolver la primera revisión al Auditor."""
        self.ensure_one()
        self._validar_estado_boton('presupuestos', 'Devolver a Retenciones')
        if not self.env.user.has_group('Matriz.group_presupuestos'):
            raise UserError("🚫 Solo Presupuestos puede devolver esta legalización a Retenciones.")
        observaciones = (self.observaciones_presupuesto or '').strip()
        if len(observaciones) < 5:
            raise UserError("⚠️ Registra en Observaciones de Presupuesto el motivo detallado de la devolución.")
        grupo = self.env.ref('Matriz.group_contabilidad')
        destinatarios = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda usuario: grupo in usuario.groups_id
        )
        self._devolver_con_notificacion(
            destinatarios, 'Auditoría Contable - Retenciones', 'contabilidad_retenciones', observaciones
        )
        return self._return_notificacion_reload('Trámite devuelto', 'La legalización fue devuelta a Retenciones.', 'warning')

    def action_devolver_decision_a_impuestos(self):
        """Permite corregir una revisión fiscal antes del cierre definitivo."""
        self.ensure_one()
        self._validar_estado_boton('contabilidad_decision', 'Devolver a Impuestos')
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise UserError("🚫 Solo Auditoría Contable puede devolver esta legalización a Impuestos.")
        observaciones = (self.observaciones_auditoria_contable or '').strip()
        if len(observaciones) < 5:
            raise UserError("⚠️ Registra en Observaciones de Auditoría Contable el motivo detallado de la devolución.")
        grupo = self.env.ref('Matriz.group_contratacion_impuestos')
        destinatarios = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda usuario: grupo in usuario.groups_id
        )
        self._devolver_con_notificacion(destinatarios, 'Impuestos', 'impuestos', observaciones)
        return self._return_notificacion_reload('Trámite devuelto', 'La legalización fue devuelta a Impuestos.', 'warning')

    def action_devolver_tesoreria_a_auditoria(self):
        """Evita que Tesorería quede sin salida cuando detecta una inconsistencia."""
        self.ensure_one()
        self._validar_estado_boton('tesoreria', 'Devolver a Auditoría')
        if not self.env.user.has_group('Matriz.group_tesoreria'):
            raise UserError("🚫 Solo Tesorería puede devolver esta legalización a Auditoría.")
        observaciones = (self.observaciones_tesoreria or '').strip()
        if len(observaciones) < 5:
            raise UserError("⚠️ Registra en Observaciones de Tesorería el motivo detallado de la devolución.")
        grupo = self.env.ref('Matriz.group_contabilidad')
        destinatarios = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda usuario: grupo in usuario.groups_id
        )
        self._devolver_con_notificacion(
            destinatarios, 'Auditoría Contable - Decisión final', 'contabilidad_decision', observaciones
        )
        return self._return_notificacion_reload('Trámite devuelto', 'La legalización fue devuelta a Auditoría.', 'warning')
    
    def action_devolver_coordinador(self):
        """
        Devuelve el trámite al Coordinador por errores en soportes o datos.
        Incluye botón de redirección en el correo.
        """
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise UserError("🚫 Solo Auditoría Contable puede devolver a Coordinación.")
        if self.state_viaticos not in ('contabilidad_retenciones', 'contabilidad_causacion', 'contabilidad'):
            raise UserError("🚫 Esta devolución solo está disponible durante las revisiones de Auditoría Contable.")
        # 1. Validar que haya una razón de devolución
        observaciones = getattr(self, 'observaciones_auditoria_contable', '') or ''
        if not observaciones:
            raise UserError("⚠️ Por favor, escribe en 'Observaciones auditoría contable' el motivo de la devolución.")

        # 2. Obtener Coordinadores y Asistentes
        grupo_coord = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis = self.env.ref('Matriz.group_contratacion_asis_coordinador')
        
        usuarios_coord = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda u: grupo_coord in u.groups_id or grupo_asis in u.groups_id
        )

        if not usuarios_coord:
            raise UserError("No hay Coordinadores o Asistentes asignados a este proyecto para devolver el trámite.")
        self._validar_notificacion(usuarios_coord, 'Coordinación', self.env.user)

        # PROYECTO
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        # 3. Preparar variables para el correo
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        auditor = self.env.user
        COLOR_ALERTA = "#dc3545" # Rojo para indicar devolución
        
        # --- LÓGICA DEL BOTÓN DE REDIRECCIÓN ---
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        # ---------------------------------------

        for usuario in usuarios_coord:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #fff5f5; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_ALERTA}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">⚠️ TRÁMITE DEVUELTO PARA CORRECCIÓN</h3>
                    </div>
                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{usuario.name}</strong>:</p>
                        <p>El Auditor Contable ha devuelto la legalización de <strong>{funcionario}</strong>. Asociado al proyecto: <strong>{nombre_proyecto}</strong></p>
                        
                        <div style="margin: 20px 0; padding: 15px; border-left: 4px solid {COLOR_ALERTA}; background-color: #fef2f2; border-radius: 4px;">
                            <strong style="color: {COLOR_ALERTA};">Motivo de la devolución:</strong><br/>
                            <em style="color: #721c24;">{observaciones}</em>
                        </div>

                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                               style="background-color: {COLOR_ALERTA}; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Ir al registro para corregir
                            </a>
                        </p>
                        
                        <p style="margin-top: 20px; font-size: 12px; color: #999; text-align: center;">
                            SGO - Sistema de Gestión Operativa
                        </p>
                    </div>
                </div>
            """
            self.env['mail.mail'].sudo().create({
                'subject': f"DEVOLUCIÓN: Legalización de {funcionario}",
                'email_from': auditor.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()


        self.write({'state_viaticos': 'coordinador'})

        return self._return_notificacion_reload(
            'Trámite devuelto',
            'La legalización fue devuelta a Coordinación.',
            tipo='warning'
        )
    
    
    
    
    
    def action_devolver_presupuestos(self):
        """
        Devuelve el trámite a Presupuestos por errores en soportes o datos.
        Incluye botón de redirección en el correo.
        """
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise UserError("🚫 Solo Auditoría Contable puede devolver a Presupuestos.")
        if self.state_viaticos not in ('contabilidad_retenciones', 'contabilidad_causacion', 'contabilidad'):
            raise UserError("🚫 Esta devolución solo está disponible durante las revisiones de Auditoría Contable.")
        
        # 1. Validar que haya una razón de devolución
        observaciones = getattr(self, 'observaciones_auditoria_contable', '') or ''
        if not observaciones:
            raise UserError("⚠️ Por favor, escribe en 'Observaciones auditoría contable' el motivo de la devolución.")

        # 2. Obtener Coordinadores y Asistentes
        grupo_coord = self.env.ref('Matriz.group_presupuestos')
        
        usuarios_presupuesto = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda u: grupo_coord in u.groups_id)
        
        if not usuarios_presupuesto:
            raise UserError("No hay Usuarios de Presupuestos asignados a este proyecto para devolver el trámite.")
        self._validar_notificacion(usuarios_presupuesto, 'Presupuestos', self.env.user)

        # PROYECTO
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        # 3. Preparar variables para el correo
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        auditor = self.env.user
        COLOR_ALERTA = "#dc3545" # Rojo para indicar devolución
        
        # --- LÓGICA DEL BOTÓN DE REDIRECCIÓN ---
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        # ---------------------------------------

        for usuario in usuarios_presupuesto:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #fff5f5; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_ALERTA}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">⚠️ TRÁMITE DEVUELTO PARA CORRECCIÓN</h3>
                    </div>
                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{usuario.name}</strong>:</p>
                        <p>El Auditor Contable ha devuelto la legalización de <strong>{funcionario}</strong>. Asociado al proyecto: <strong>{nombre_proyecto}</strong></p>
                        
                        <div style="margin: 20px 0; padding: 15px; border-left: 4px solid {COLOR_ALERTA}; background-color: #fef2f2; border-radius: 4px;">
                            <strong style="color: {COLOR_ALERTA};">Motivo de la devolución:</strong><br/>
                            <em style="color: #721c24;">{observaciones}</em>
                        </div>

                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                               style="background-color: {COLOR_ALERTA}; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Ir al registro para corregir
                            </a>
                        </p>
                        
                        <p style="margin-top: 20px; font-size: 12px; color: #999; text-align: center;">
                            SGO - Sistema de Gestión Operativa
                        </p>
                    </div>
                </div>
            """
            self.env['mail.mail'].sudo().create({
                'subject': f"DEVOLUCIÓN: Legalización de {funcionario}",
                'email_from': auditor.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()


        self.write({'state_viaticos': 'presupuestos'})

        return self._return_notificacion_reload(
            'Trámite devuelto',
            'La legalización fue devuelta a Presupuestos.',
            tipo='warning'
        )



    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  
         
    @api.onchange('reintegro', 'reembolso', 'total_legalizado') # Agrega aquí los campos de dinero
    def _onchange_reset_impuestos(self):
        # Si alguien modifica valores, obligamos a que pase por Impuestos otra vez
        if self.validado_por_impuestos:
            self.validado_por_impuestos = False
            
    # IMPUESTOS A TESORERÍA / CONTABILIDAD (DINÁMICO)
    def action_aprobar_impuestos(self):
        """
        Si es REINTEGRO: Pasa a Contabilidad (Auditor) para trámite de Cuentas por Pagar.
        Si es REEMBOLSO o PAZ Y SALVO: Pasa a Tesorería.
        """
        self.ensure_one()
        self._validar_estado_boton('impuestos', 'Aprobar Impuestos')
        if not self.env.user.has_group('Matriz.group_contratacion_impuestos'):
            raise UserError("🚫 Solo Impuestos puede aprobar esta etapa.")
        # --- 1. LÓGICA DINÁMICA DE TEXTOS Y DESTINOS ---
        estilo_resaltado = "background-color: #ffff00; padding: 2px 5px; border-radius: 3px; color: #000;"
        
        if self.es_reintegro:
            tipo_operacion = "Reintegro"
            linea_saldo = f"<strong>Reintegro (A favor del funcionario):</strong> <span style='{estilo_resaltado}'>${self.reintegro:,.2f}</span><br/>"
        elif self.es_reembolso:
            tipo_operacion = "Reembolso"
            linea_saldo = f"<strong>Reembolso (A favor de FUV):</strong> <span style='{estilo_resaltado}'>${self.reembolso:,.2f}</span><br/>"
        else:
            tipo_operacion = "Paz y Salvo"
            linea_saldo = "<strong>Saldo:</strong> $0.00 (Legalización exacta)<br/>"

        mensaje_accion = "Auditoría Contable debe decidir el cierre o el envío a Tesorería."
        xml_id_grupo = 'Matriz.group_contabilidad'
        nombre_rol_destino = "Auditor Contable"
        nuevo_estado = 'contabilidad_decision'

        # --- 2. OBTENER USUARIOS ---
        grupo_destino = self.env.ref(xml_id_grupo, raise_if_not_found=False)
        if not grupo_destino:
            raise UserError(f"No se encontró el grupo técnico: {xml_id_grupo}")

        usuarios_notificar = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda user: grupo_destino in user.groups_id
        )
        #Nuevo perfil para el funcionario
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        
        if not usuarios_notificar:
            raise UserError(f"No hay usuarios del grupo '{nombre_rol_destino}' asignados a este proyecto.")

        # --- 3. ENVIAR CORREO ---
        analista_impuestos = self.env.user
        self._validar_notificacion(usuarios_notificar, nombre_rol_destino, analista_impuestos)
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        observaciones = getattr(self, 'observaciones_impuestos', '') or '' 
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        COLOR_PRINCIPAL = "#043484" 

        for usuario in usuarios_notificar:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Gestión de {tipo_operacion}: <strong>{nombre_proyecto}</strong></h3>
                    </div>
                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{usuario.name}</strong> ({nombre_rol_destino}):</p>
                        <p>Impuestos ha validado los documentos. <strong>{mensaje_accion}</strong></p>
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Funcionario:</strong> {funcionario}<br/>
                            <strong>No. Contable:</strong> {self.numero_contable}<br/>
                            {linea_saldo}
                        </div>
                        {f'''<div style="margin-top: 20px; padding: 10px; border: 1px solid #28a745; background-color: #d4edda; border-radius: 4px;">
                            <strong>Nota de Impuestos:</strong><br/>
                            <em style="color: #155724;">{observaciones}</em>
                        </div>''' if observaciones else ''}
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Revisar documentación
                            </a>
                        </p>
                    </div>
                </div>
            """
            self.env['mail.mail'].sudo().create({
                'subject': f"{tipo_operacion} pendiente: {funcionario}",
                'email_from': analista_impuestos.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # --- 4. CAMBIO DE ESTADO Y MARCA DE AVAL ---
        self.write({'state_viaticos': nuevo_estado})
        self.validado_por_impuestos = True # <--- AQUÍ ACTIVAS EL "PASAPORTE" PARA EL SALTO

        return self._return_notificacion_reload(
            'Mensaje enviado',
            f'La legalización fue enviada a {nombre_rol_destino}.'
        )

    def action_decidir_cierre_auditoria(self):
        """Cierra o envía a Tesorería según exista reintegro."""
        self.ensure_one()
        self._validar_estado_boton('contabilidad_decision', 'Decidir cierre')
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise UserError("🚫 Solo Auditoría Contable puede tomar la decisión final.")

        if self.es_reintegro and self.reintegro > 0:
            grupo_tesoreria = self.env.ref('Matriz.group_tesoreria')
            destinatarios = self.proyecto_relacionado_viaticos.users_with_access.filtered(
                lambda user: grupo_tesoreria in user.groups_id
            )
            if not destinatarios:
                raise UserError("No hay usuarios de Tesorería asignados a este proyecto.")

            auditor = self.env.user
            self._validar_notificacion(destinatarios, 'Tesorería', auditor)
            funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
            nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
            for destinatario in destinatarios.filtered('email'):
                self.env['mail.mail'].sudo().create({
                    'subject': f"Reintegro pendiente: {funcionario} - {nombre_proyecto}",
                    'email_from': auditor.email,
                    'email_to': destinatario.email,
                    'body_html': self._formato_correo_legalizacion(
                        destinatario, 'Reintegro pendiente · Tesorería',
                        f'Auditoría Contable autorizó un reintegro de ${self.reintegro:,.2f}. Se requiere la gestión de Tesorería.',
                        url=formulario_url,
                    ),
                    'auto_delete': True,
                }).send()

            self.write({'state_viaticos': 'tesoreria'})
            self.message_post(body=(
                "<b>Decisión de Auditoría Contable:</b> existe reintegro. "
                f"Se envía a Tesorería para pagar ${self.reintegro:,.2f}."
            ))
            return self._return_notificacion_reload(
                'Enviado a Tesorería', 'La legalización requiere pago de reintegro.'
            )

        # El flujo solicitado permite cerrar el reembolso desde Auditoría, pero
        # no permite perder el control documental que antes validaba Tesorería.
        if self.es_reembolso and self.reembolso > 0:
            comprobantes = self.env['drive'].search_count([
                ('viaticos_otros_id', '=', self.id)
            ])
            if not comprobantes:
                raise UserError(
                    "No se puede cerrar el reembolso. Debe existir al menos un "
                    "comprobante de reembolso cargado antes del cierre."
                )

        auditor = self.env.user
        destinatarios_cierre = self._destinatarios_cierre()
        self._validar_notificacion(destinatarios_cierre, 'cierre de la legalización', auditor)
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        numero_contable = self.numero_contable if self.tiene_anticipo == 'si' else self.numero_contable_sin_anticipo
        for destinatario in destinatarios_cierre:
            self.env['mail.mail'].sudo().create({
                'subject': f"✅ Finalizado: Legalización {funcionario}",
                'email_from': auditor.email,
                'email_to': destinatario.email,
                'body_html': self._formato_correo_legalizacion(
                    destinatario, 'Legalización finalizada',
                    'Auditoría Contable cerró la legalización.',
                    detalle=f"Número contable: {numero_contable or 'Sin registrar'}.",
                ),
                'auto_delete': True,
            }).send()

        self.write({'state_viaticos': 'completado'})
        self.message_post(body=(
            "<b>Decisión de Auditoría Contable:</b> no hay reintegro pendiente; "
            "la legalización se cerró exitosamente."
        ))
        return self._return_notificacion_reload(
            'Legalización cerrada', 'No existe reintegro pendiente de pago.'
        )
        
        
    def action_devolver_a_auditoria(self):
        """
        Devuelve el trámite al Auditor Contable desde Impuestos.
        Obliga a llenar observaciones y envía notificación por correo.
        """
        self.ensure_one()
        self._validar_estado_boton('impuestos', 'Devolver a Auditoría')
        if not self.env.user.has_group('Matriz.group_contratacion_impuestos'):
            raise UserError("🚫 Solo Impuestos puede devolver una legalización a Auditoría.")
        # 1. Validar que existan observaciones de impuestos
        observaciones = getattr(self, 'observaciones_impuestos', '') or ''
        if not observaciones or len(observaciones.strip()) < 5:
            raise UserError("⚠️ Por favor, escribe en 'Observaciones de Impuestos' el motivo detallado de la devolución.")

        # 2. Obtener usuarios del grupo Auditor Contable vinculados al proyecto
        grupo_auditor = self.env.ref('Matriz.group_contabilidad') # Ajustar XML ID si es necesario
        usuarios_auditoria = self.proyecto_relacionado_viaticos.users_with_access.filtered(
            lambda u: grupo_auditor in u.groups_id
        )

        if not usuarios_auditoria:
            raise UserError("No hay Auditores Contables asignados a este proyecto para recibir la devolución.")
        self._validar_notificacion(usuarios_auditoria, 'Auditoría Contable - Causación', self.env.user)

        # PROYECTO
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        # 3. Preparar variables para el correo
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        analista_impuestos = self.env.user
        COLOR_DEVOLUCION = "#e67e22" # Naranja para diferenciar de la devolución a coordinador
        
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"

        # 4. Enviar correos
        for usuario in usuarios_auditoria:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #fffaf5; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_DEVOLUCION}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">🔄 Devolución desde impuestos</h3>
                    </div>
                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{usuario.name}</strong> (Auditoría Contable):</p>
                        <p>El área de Impuestos ha devuelto la legalización de <strong>{funcionario}</strong> Asociado al proyecto: <strong>{nombre_proyecto}</strong></p>

                        <div style="margin: 20px 0; padding: 15px; border-left: 4px solid {COLOR_DEVOLUCION}; background-color: #fff5eb; border-radius: 4px;">
                            <strong style="color: {COLOR_DEVOLUCION};">Hallazgos de Impuestos:</strong><br/>
                            <em style="color: #856404;">{observaciones}</em>
                        </div>

                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                            style="background-color: {COLOR_DEVOLUCION}; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Revisar observaciones
                            </a>
                        </p>
                        
                        <p style="margin-top: 20px; font-size: 12px; color: #999; text-align: center;">
                            SGO - Sistema de Gestión Operativa
                        </p>
                    </div>
                </div>
            """
            self.env['mail.mail'].sudo().create({
                'subject': f"Devolución impuestos: Legalización de {funcionario}",
                'email_from': analista_impuestos.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # 5. Cambiar estado y resetear validación
        self.write({'state_viaticos': 'contabilidad_causacion'})

        return self._return_notificacion_reload(
            'Trámite devuelto',
            'La legalización fue devuelta a Auditoría Contable.',
            tipo='warning'
        )

    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    
    
    comprobantes_subidos = fields.Integer(string="Comprobantes subidos", compute="compute_comprobantes_subidos")
    def compute_comprobantes_subidos(self):
        for record in self:
            count = self.env['drive'].search_count([
                ('viaticos_otros_id', '=', record.id)
            ])
            record.comprobantes_subidos = count


    def action_aprobar_tesoreria_final(self):
        self.ensure_one()
        self._validar_estado_boton('tesoreria', 'Cerrar Legalización')

        self.compute_comprobantes_subidos()

           
        documentos_en_odoo = self.env['drive'].search([
            ('viaticos_otros_id', '=', self.id)
        ])

        _logger.info(f"Documentos encontrados para el viático {self.id}: {len(documentos_en_odoo)}")

        # 2. Si no hay al menos 2 registros, lanzamos el error
        if self.es_reembolso == True and len(documentos_en_odoo) < 1:
            raise UserError(
                "⚠️ ¡Atención Tesorería! No se puede completar el proceso.\n\n"
                "Es obligatorio que existan al menos 1 documentos cargados en la carpeta "
                "(Comprobantes de reembolsos) antes de cerrar."
            )
            
            
        # --- 1. VALIDACIÓN DE DOCUMENTOS (Anti-olvidos) ---
        missing_docs = []
        if self.tipo_persona == 'pn' or self.persona_sin_anticipo == 'pn':
            if not self.comprobante_pago_document_id: missing_docs.append("Comprobante de pago")
            if not self.soporte_de_pago_document_id: missing_docs.append("Soporte de pago")
        elif self.tipo_persona == 'pj' or self.persona_sin_anticipo == 'pj':
            if not self.comprobante_pago_pj_document_id: missing_docs.append("Comprobante de pago")
            if not self.soporte_de_pago_pj_document_id: missing_docs.append("Soporte de pago")

        if missing_docs:
            docs_text = "\n• ".join(missing_docs)
            raise UserError(
                f"⚠️ ¡Atención Tesorería! No se puede completar el proceso.\n\n"
                f"Faltan documentos obligatorios para el cierre:\n• {docs_text}"
            )

        # --- 2. VALIDACIÓN DE PERMISOS ---
        # Solo Tesorería o Financiero pueden dar el click final
        if not self.env.user.has_group('Matriz.group_tesoreria') and not self.env.user.has_group('Matriz.group_contratacion_financiero'):
             raise UserError("🚫 Acceso Denegado: Solo la Tesorería Principal puede cerrar este proceso.")
         
        #Nuevo perfil para el funcionario
        funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Funcionario no identificado"
        
        # --- 3. DETERMINAR TIPO DE OPERACIÓN (DINÁMICO) ---
        tipo_operacion = "Legalización cerrada"
        monto_operacion = 0.0

        if self.es_reintegro and self.reintegro > 0:
            tipo_operacion = "Reintegro (Dinero pagado al funcionario)"
            monto_operacion = self.reintegro
        elif self.es_reembolso and self.reembolso > 0:
            tipo_operacion = "Reembolso (Dinero devuelto por funcionario)"
            monto_operacion = self.reembolso
        else:
             tipo_operacion = "Paz Y Salvo (Cruce exacto)"

        # --- 4. TRAZABILIDAD EN EL CHATTER ---
        self.message_post(
            body=(
                f"✅ <b>PROCESO FINALIZADO POR TESORERÍA</b><br/>"
                f"<ul>"
                f"<li><b>Tipo:</b> {tipo_operacion}</li>"
                f"<li><b>Monto Final:</b> ${monto_operacion:,.2f}</li>"
                f"<li><b>Cerrado por:</b> {self.env.user.name}</li>"
                f"</ul>"
            ),
            subtype_xmlid="mail.mt_note"
        )

        # --- 5. NOTIFICACIÓN MASIVA (Coordinador, Asistente y Empleado) ---
        tesorero = self.env.user
        nombre_proyecto = self.proyecto_relacionado_viaticos.display_name or 'Sin proyecto'
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos"
        COLOR_PRINCIPAL = "#043484"
        observaciones = getattr(self, 'observaciones_tesoreria', '') or ''


        # A. Identificar Destinatarios
        # 1. El empleado (creador)
        destinatarios = self.env['res.users']
        if self.create_uid:
            destinatarios |= self.create_uid
        
        # 2. Coordinadores y Asistentes del Proyecto
        # Buscamos los grupos IDs
        grupo_coord = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)
        grupo_asis = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
        
        grupos_ids = []
        if grupo_coord: grupos_ids.append(grupo_coord.id)
        if grupo_asis: grupos_ids.append(grupo_asis.id)

        if grupos_ids and self.proyecto_relacionado_viaticos:
            # Filtramos usuarios del proyecto que tengan rol de Coordi o Asistente
            usuarios_gestion = self.proyecto_relacionado_viaticos.users_with_access.filtered(
                lambda u: any(g.id in grupos_ids for g in u.groups_id)
            )
            destinatarios |= usuarios_gestion

        self._validar_notificacion(destinatarios, 'cierre de la legalización', tesorero)

        numero_contable = self.numero_contable if self.tiene_anticipo == 'si' else self.numero_contable_sin_anticipo

        # B. Enviar Correos
        for usuario in destinatarios:
            # Personalizamos el saludo dependiendo de quién recibe
            rol_msg = "Coordinación" if usuario != self.create_uid else "Funcionario"
            
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Proceso Finalizado: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{usuario.name}</strong> ({rol_msg}):</p>

                        <p>Tesorería informa que la legalización del funcionario <strong>{funcionario}</strong> ha culminado exitosamente.</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Estado:</strong> PAZ Y SALVO ✅<br/>
                            <strong>Operación:</strong> {tipo_operacion}<br/>
                            <strong>Valor Liquidado:</strong> ${monto_operacion:,.2f}<br/>
                            <strong>No. Contable:</strong> {numero_contable or 'Sin registrar'}
                        </div>
                        
                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid #28a745; background-color: #d4edda; border-radius: 4px;">
                            <strong>Observación Tesorería:</strong><br/>
                            <em style="color: #155724;">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                               style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Ver Detalle
                            </a>
                        </p>
                        <p style="margin-top: 20px; font-size: 12px; color: #999;">SGO - Notificación de Cierre</p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"✅ Finalizado: Legalización {funcionario}",
                'email_from': tesorero.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()


        # --- 6. CAMBIO DE ESTADO FINAL ---
        # "completado" es el estado final que bloquea todo.
        self.write({'state_viaticos': 'completado'})

        return self._return_notificacion_reload(
            'Legalización Cerrada',
            'Se notificó a Coordinación y al Funcionario. Proceso exitoso.'
        )
        

    
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    
    
    def buscar_carpeta(self):
        """Busca o crea la carpeta relacionada en Odoo y en Google Drive."""
        if self.proyecto_relacionado_viaticos:
            if self.proceso_en_ejecucion:
                _logger.info("Proceso de búsqueda de carpeta ya en ejecución, esperando a que termine.")
                return

            self.proceso_en_ejecucion = True

            try:
                _logger.info(f"Buscando carpeta para el proyecto relacionado: {self.proyecto_relacionado_viaticos.name}")

                # Buscar o crear carpeta en Odoo
                drive_stage = self.env['drive_stage'].search([
                    ('name', '=', '3. CONTRATOS PS'),
                    ('proyecto_relacionado', '=', self.proyecto_relacionado_viaticos.id),
                ], limit=1)

                if not drive_stage:
                    _logger.warning("No se encontró la carpeta '3. CONTRATOS PS' en Odoo. Creando...")
                    drive_stage = self.env['drive_stage'].create({
                        'name': '3. CONTRATOS PS',
                        'proyecto_relacionado': self.proyecto_relacionado_viaticos.id,
                        'folder_id': 'some_default_folder_id',
                    })

                _logger.info(f"Carpeta en Odoo encontrada o creada: {drive_stage.name} (ID: {drive_stage.id})")

                # Asignar solo el nombre sin identificador a `new_name`
                # 1. Declaramos el nombre usando la lógica OR: Anticipo > Manual > Respaldo
                funcionario = self.funcionario_2 or self.funcionario_sin_anticipo or "Sin_Nombre"

                # 2. Generar identificador único solo para Google Drive
                unique_folder_id = str(uuid.uuid4())[:8]

                # 3. Lógica para identificar si es Persona Natural o Jurídica
                # Primero extraemos el valor que no esté vacío
                valor_a_evaluar = self.tipo_persona or self.persona_sin_anticipo

                # Luego aplicamos la lógica de conversión una sola vez
                tipo_persona = 'PN' if valor_a_evaluar == 'pn' else 'PJ'

                # 4. CONSTRUCCIÓN DEL NOMBRE: Usamos la variable 'funcionario' que ya tiene el nombre real
                drive_folder_name = f"{funcionario}_(legalizacion {tipo_persona})_{unique_folder_id}"

                _logger.info(f"Intentando buscar o crear la carpeta con el nombre: {drive_folder_name}")

                # Buscar si ya existe en 'drive.file'
                drive_file = self.env['drive.file'].search([
                    ('folder_name', '=', drive_folder_name),
                    ('drive_id', '=', drive_stage.id)
                ], limit=1)

                if not drive_file:
                    _logger.info(f"Carpeta '{drive_folder_name}' no encontrada en 'drive.file'. Creando...")

                    with threading.Lock():
                        drive_file = self.env['drive.file'].search([
                            ('folder_name', '=', drive_folder_name),
                            ('drive_id', '=', drive_stage.id)
                        ], limit=1)
                        if not drive_file:
                            drive_auth = GoogleDriveAuth.get_instance()
                            drive = drive_auth.get_drive()

                            folder_metadata = {
                                'title': drive_folder_name,  # Usa la versión con identificador solo en Drive
                                'mimeType': 'application/vnd.google-apps.folder',
                                'parents': [{'id': drive_stage.folder_id}]
                            }

                            try:
                                folder = drive.CreateFile(folder_metadata)
                                folder.Upload()

                                drive_file = self.env['drive.file'].create({
                                    'folder_name': drive_folder_name,
                                    'folder_id2': folder['id'],
                                    'drive_id': drive_stage.id,

                                })
                                drive_file.write({'select_persona': 'anticipos'})

                                _logger.info(
                                    f"Carpeta '{drive_folder_name}' creada en 'drive.file' con ID {folder['id']}")

                                self.write({'drive_file_id': drive_file.id})
                                _logger.info(f"Campo 'drive_file_id' actualizado con ID {drive_file.id}")
                                time.sleep(3)


                            except Exception as e:
                                _logger.error(f"Error creando la carpeta en Google Drive: {e}")
                                raise UserError(f"Error creando la carpeta en Google Drive: {e}")
                        else:
                            _logger.info(f"Carpeta '{drive_folder_name}' ya existe en 'drive.file'.")
                            self.write({'drive_file_id': drive_file.id})
                            _logger.info(f"Campo 'drive_file_id' actualizado con ID {drive_file.id}")

            finally:
                self.proceso_en_ejecucion = False  # Corrige el flag de proceso en ejecución

    # ---------------------Variable para vincular con drive.file---------------------------------
    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive') 
    # ------------------------------------------------------------------------------------------------------------------------------------------

 # ---------------------Variable nueva --------------------------------
    rut_pn_document = fields.Binary(
        string="RUT (PN)",
        attachment=True,
        help="Registro Único Tributario (Persona Natural)",
    )
    rut_pn_filename = fields.Char(string="Nombre de Archivo RUT (PN)")
    rut_pn_document_id = fields.Many2one('drive', string='RUT', tracking=True, domain="[('stage2_id', '=', drive_file_id)]") 
    rut_pn_document_visible = fields.Boolean(string="RUT Visible", default=True)

    def action_upload_rut_pn_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        
        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RUT',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'Rut_PN_Documento_legalizacion', 
            },
        }
    
        # Diccionario de caché para almacenar resultados de búsqueda
    _cache_documentos_rut_pn = {}

    def abrir_documento_rut_pn(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'rut_pn_document_id' está configurado
        if self.rut_pn_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "RUT",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.rut_pn_document_id.id,
                "target": "new",
            }

        # Si 'factura_cuenta_name' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rut_pn_filename:
            file_name_to_search = self.rut_pn_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rut_pn:
                drive_document = self._cache_documentos_rut_pn[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_rut_pn[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rut_pn_document_id' con el ID del documento encontrado
                self.write({"rut_pn_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "RUT",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )
    
    

    doc_identidad_document = fields.Binary(
        string="Documento de Identidad",
        attachment=True,
        help="Cédula de Ciudadanía, C. Extranjería, etc.",
    )
    doc_identidad_filename = fields.Char(
        string="Nombre de Archivo Documento de Identidad"
    )
    
    doc_identidad_document_id = fields.Many2one('drive', string='Documento de identidad', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    doc_identidad_document_visible = fields.Boolean(string="Documento de identidad Visible", default=True)

    def action_upload_doc_identidad_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Documento de Identidad',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Doc_Identidad_Documento_legalizacion', 
            },
        }   
    
    _cache_documentos_doc_identidad = {}

    def abrir_documento_doc_identidad(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'doc_identidad_document_id' está configurado
        if self.doc_identidad_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Documento de identidad",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.doc_identidad_document_id.id,
                "target": "new",
            }

        # Si 'doc_identidad_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.doc_identidad_filename:
            file_name_to_search = self.doc_identidad_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_doc_identidad:
                drive_document = self._cache_documentos_doc_identidad[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_doc_identidad[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'doc_identidad_document_id' con el ID del documento encontrado
                self.write({"doc_identidad_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Documento de identidad",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )


    cert_bancaria_pn_document = fields.Binary(
        string="Certificación Bancaria (PN)",
        attachment=True,
        help="Certificación que acredita la cuenta para el desembolso.",
    )
    cert_bancaria_pn_filename = fields.Char(
        string="Nombre de Archivo Cert. Bancaria (PN)"
    )

    cert_bancaria_pn_document_id = fields.Many2one('drive', string='Certificación bancaria', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    cert_bancaria_pn_document_visible = fields.Boolean(string="Cert. Bancaria (PN) Visible", default=True) 

    def action_upload_cert_bancaria_pn_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Certificación bancaria',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Cert_Bancaria_PN_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_cert_bancaria_pn = {}

    def abrir_documento_cert_bancaria_pn(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'cert_bancaria_pn_document_id' está configurado
        if self.cert_bancaria_pn_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Certificación bancaria",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.cert_bancaria_pn_document_id.id,
                "target": "new",
            }

        # Si 'cert_bancaria_pn_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.cert_bancaria_pn_filename:
            file_name_to_search = self.cert_bancaria_pn_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_cert_bancaria_pn:
                drive_document = self._cache_documentos_cert_bancaria_pn[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_cert_bancaria_pn[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'cert_bancaria_pn_document_id' con el ID del documento encontrado
                self.write({"cert_bancaria_pn_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Certificación bancaria",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error 
                raise UserError("No se encontró el documento de certificación bancaria.")
            
    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA NATURAL (PN) Documento de causación
    # ------------------------------------------------------------------------------------------------------------------------------------------
    documento_de_causacion = fields.Binary(string="Causación", attachment=True)
    documento_de_causacion_filename = fields.Char(string="Nombre de archivo causación")

    documento_de_causacion_id = fields.Many2one('drive', string='Causación', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    documento_de_causacion_visible = fields.Boolean(string="Causación Visible", default=True)

    def _validar_carga_causacion(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise AccessError('Solo Auditoría Contable puede cargar documentos de causación.')
        self._validar_estado_boton('contabilidad_causacion', 'Subir causación')

    def action_upload_documento_de_causacion(self):
        self._validar_carga_causacion()
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir causación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Documento_De_Causacion_legalizacion', 
            },
        }
    
    _cache_documentos_documento_de_causacion = {}

    def abrir_documento_de_causacion(self):        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si el documento de causación está configurado.
        if self.documento_de_causacion_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Causación",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.documento_de_causacion_id.id,
                "target": "new",
            }

        # Si 'documento_de_causacion_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_de_causacion_filename:
            file_name_to_search = self.documento_de_causacion_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_de_causacion:
                drive_document = self._cache_documentos_documento_de_causacion[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_de_causacion[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_de_causacion_id' con el ID del documento encontrado
                self.write({"documento_de_causacion_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Causación",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )

    def open_drive_otros_docs_causacion_pn(self):
        self.ensure_one()
        puede_cargar = (self.state_viaticos == 'contabilidad_causacion' and
                        self.env.user.has_group('Matriz.group_contabilidad'))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otros documentos de causación',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('viaticos_otros_docs_causacion_pn_id', '=', self.id)],
            'context': {
                'default_stage2_id': self.drive_file_id.id,
                'default_folder_id': self.drive_file_id.folder_id2 if self.drive_file_id else 'root',
                'default_viaticos_otros_docs_causacion_pn_id': self.id,
                'create': puede_cargar,
                'edit': puede_cargar,
                'delete': puede_cargar,
            },
            'target': 'current',
        }

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA NATURAL (PN) Orden de pago, comprobante de pago soporte de pago
    # ------------------------------------------------------------------------------------------------------------------------------------------
    def _validar_comprobante_reembolso_auditoria(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise AccessError('Solo Auditoría Contable puede utilizar esta carga de comprobantes.')
        self._validar_estado_boton('contabilidad_decision', 'Subir comprobante de reembolso')
        if not self.es_reembolso or self.reembolso <= 0 or self.es_reintegro:
            raise UserError('Esta carga solo está disponible cuando hay reembolso mayor a cero y no hay reintegro.')

    def open_comprobantes_reembolso_auditoria(self):
        """Acceso adicional de Auditoría; conserva el acceso de Tesorería."""
        self._validar_comprobante_reembolso_auditoria()
        accion = self.open_drive_otros_comprobante_de_reembolsos()
        accion['context'] = dict(accion['context'], create=True, edit=True, delete=False)
        return accion

    def open_drive_otros_comprobante_de_reembolsos(self):
        # Verificamos si el usuario tiene el grupo de tesorería
        es_tesoreria = self.env.user.has_group('Matriz.group_tesoreria')
        
        # Si no es tesorería, desactivamos la creación y edición/borrado
        # 'create': es_tesoreria (True si pertenece, False si no)
        # 'delete': es_tesoreria
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Comprobantes de reembolsos',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('viaticos_otros_id', '=', self.id)],
            'context': {
                'default_stage2_id': self.drive_file_id.id,
                'default_viaticos_otros_id': self.id,
                # Estas banderas controlan la interfaz del cliente (botones)
                'create': es_tesoreria,
                'edit': es_tesoreria,
                'delete': es_tesoreria,
            },
            'target': 'current',
        }
    
    
    orden_pago_document = fields.Binary(
        string="Orden de pago",
        attachment=True,
        help="Orden de pago de la cuenta bancaria.",
    )
    orden_pago_filename = fields.Char(
        string="Nombre de Archivo Orden de pago"
    )
    orden_pago_document_id = fields.Many2one('drive', string='Orden de pago', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    orden_pago_document_visible = fields.Boolean(string="Orden de pago Visible", default=True)

    def action_upload_orden_pago_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Orden de pago',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'orden_Pago_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_orden_pago = {}

    def abrir_documento_orden_pago(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'orden_pago_document_id' está configurado
        if self.orden_pago_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Orden de pago",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.orden_pago_document_id.id,
                "target": "new",
            }

        # Si 'orden_pago_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.orden_pago_filename:
            file_name_to_search = self.orden_pago_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_orden_pago:
                drive_document = self._cache_documentos_orden_pago[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_orden_pago[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'orden_pago_document_id' con el ID del documento encontrado
                self.write({"orden_pago_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Orden de pago",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )
            
    
    comprobante_pago_document = fields.Binary(
        string="Comprobante de Pago",
        attachment=True,
        help="Comprobante de pago de la cuenta bancaria.",
    )
    comprobante_pago_filename = fields.Char(
        string="Nombre de Archivo Comprobante de Pago"
    )
    comprobante_pago_document_id = fields.Many2one('drive', string='Comprobante de pago', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    comprobante_pago_document_visible = fields.Boolean(string="Comprobante de pago Visible", default=True)

    def action_upload_comprobante_pago_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Comprobante de pago',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Comprobante_Pago_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_comprobante_pago = {}

    def abrir_documento_comprobante_pago(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'comprobante_pago_document_id' está configurado
        if self.comprobante_pago_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Comprobante de pago",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.comprobante_pago_document_id.id,
                "target": "new",
            }

        # Si 'comprobante_pago_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.comprobante_pago_filename:
            file_name_to_search = self.comprobante_pago_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_comprobante_pago:
                drive_document = self._cache_documentos_comprobante_pago[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_comprobante_pago[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'comprobante_pago_document_id' con el ID del documento encontrado
                self.write({"comprobante_pago_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Comprobante de pago",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )
            
    soporte_de_pago = fields.Binary(string="Soporte de pago", attachment=True)
    soporte_de_pago_filename = fields.Char(string="Nombre de Archivo Soporte de pago")
    soporte_de_pago_document_id = fields.Many2one('drive', string="Soporte de pago", tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    soporte_de_pago_document_visible = fields.Boolean(string="Soporte de pago Visible", default=True)

    def action_upload_soporte_de_pago(self):        
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir soporte de pago',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Soporte_De_Pago_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_soporte_de_pago = {}

    def abrir_documento_soporte_de_pago(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'soporte_de_pago_document_id' está configurado
        if self.soporte_de_pago_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Soporte de pago",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.soporte_de_pago_document_id.id,
                "target": "new",
            }
        # Si 'soporte_de_pago_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.soporte_de_pago_filename:
            file_name_to_search = self.soporte_de_pago_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_soporte_de_pago:
                drive_document = self._cache_documentos_soporte_de_pago[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_soporte_de_pago[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'soporte_de_pago_document_id' con el ID del documento encontrado
                self.write({"soporte_de_pago_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Soporte de pago",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )
        


    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA JURÍDICA (PJ)
    # ------------------------------------------------------------------------------------------------------------------------------------------
    rut_pj_document = fields.Binary(
        string="RUT (PJ)",
        attachment=True,
        help="Registro Único Tributario (Persona Jurídica)",
    )
    rut_pj_filename = fields.Char(string="Nombre de Archivo RUT (PJ)")
    rut_pj_document_id = fields.Many2one('drive', string='RUT PJ', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    rut_pj_document_visible = fields.Boolean(string="RUT (PJ) Visible", default=True)
    
    def action_upload_rut_pj_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RUT PJ',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Rut_PJ_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_rut_pj = {}

    def abrir_documento_rut_pj(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'rut_pj_document_id' está configurado
        if self.rut_pj_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "RUT PJ",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.rut_pj_document_id.id,
                "target": "new",
            }

        # Si 'rut_pj_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rut_pj_filename:
            file_name_to_search = self.rut_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rut_pj:
                drive_document = self._cache_documentos_rut_pj[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_rut_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rut_pj_document_id' con el ID del documento encontrado
                self.write({"rut_pj_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "RUT PJ",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"    
                )

    camara_comercio_document = fields.Binary(
        string="Cámara de Comercio",
        attachment=True,
        help="Registro Mercantil vigente de la Cámara de Comercio.",
    )
    camara_comercio_filename = fields.Char(
        string="Nombre de Archivo Cámara de Comercio"
    )

    camara_comercio_document_id = fields.Many2one('drive', string='Cámara de comercio', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    camara_comercio_document_visible = fields.Boolean(string="Cámara de comercio Visible", default=True)

    def action_upload_camara_comercio_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Cámara de Comercio',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Camara_Comercio_Documento_legalizacion', 
            },
        }

    _cache_documentos_camara_comercio = {}

    def abrir_documento_camara_comercio(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'camara_comercio_document_id' está configurado
        if self.camara_comercio_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Cámara de Comercio",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.camara_comercio_document_id.id,
                "target": "new",
            }

        # Si 'camara_comercio_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.camara_comercio_filename:
            file_name_to_search = self.camara_comercio_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_camara_comercio:
                drive_document = self._cache_documentos_camara_comercio[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_camara_comercio[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'camara_comercio_document_id' con el ID del documento encontrado
                self.write({"camara_comercio_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Cámara de Comercio",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error  
                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )


    cert_bancaria_pj_document = fields.Binary(
        string="Certificación Bancaria (PJ)",
        attachment=True,
        help="Certificación que acredita la cuenta de la persona jurídica.",
    )
    cert_bancaria_pj_filename = fields.Char(
        string="Nombre de Archivo Cert. Bancaria (PJ)"
    ) 

    cert_bancaria_pj_document_id = fields.Many2one('drive', string='Certificación bancaria PJ', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    cert_bancaria_pj_document_visible = fields.Boolean(string="Cert. Bancaria (PJ) Visible", default=True)

    def action_upload_cert_bancaria_pj_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Certificación bancaria PJ',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Cert_Bancaria_PJ_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_cert_bancaria_pj = {}

    def abrir_documento_cert_bancaria_pj(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'cert_bancaria_pj_document_id' está configurado
        if self.cert_bancaria_pj_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Certificación bancaria PJ",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.cert_bancaria_pj_document_id.id,
                "target": "new",
            }

        # Si 'cert_bancaria_pj_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.cert_bancaria_pj_filename:
            file_name_to_search = self.cert_bancaria_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_cert_bancaria_pj:
                drive_document = self._cache_documentos_cert_bancaria_pj[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_cert_bancaria_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'cert_bancaria_pj_document_id' con el ID del documento encontrado
                self.write({"cert_bancaria_pj_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Certificación bancaria PJ",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error
                raise UserError("No se encontró el documento de certificación bancaria PJ.")

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA NATURAL (PJ) Documento de causación
    # ------------------------------------------------------------------------------------------------------------------------------------------
    documento_de_causacion_pj = fields.Binary(string="Causación", attachment=True)
    documento_de_causacion_pj_filename = fields.Char(string="Nombre de archivo causación")

    documento_de_causacion_pj_id = fields.Many2one('drive', string='Causación', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    documento_de_causacion_pj_visible = fields.Boolean(string="Causación Visible", default=True)

    def action_upload_documento_de_causacion_pj(self):
        self._validar_carga_causacion()
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir causación',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Documento_De_Causacion_pj_legalizacion', 
            },
        }
    
    _cache_documentos_documento_de_causacion_pj = {}

    def abrir_documento_de_causacion_pj(self):        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si el documento de causación PJ está configurado.
        if self.documento_de_causacion_pj_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Causación",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.documento_de_causacion_pj_id.id,
                "target": "new",
            }

        # Si 'documento_de_causacion_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.documento_de_causacion_pj_filename:
            file_name_to_search = self.documento_de_causacion_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_documento_de_causacion_pj:
                drive_document = self._cache_documentos_documento_de_causacion_pj[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_documento_de_causacion_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'documento_de_causacio_pj_id' con el ID del documento encontrado
                self.write({"documento_de_causacion_pj_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Causación",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )
    

    def open_drive_otros_docs_causacion_pj(self):
        self.ensure_one()
        puede_cargar = (self.state_viaticos == 'contabilidad_causacion' and
                        self.env.user.has_group('Matriz.group_contabilidad'))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Otros documentos de causación',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('viaticos_otros_docs_causacion_pj_id', '=', self.id)],
            'context': {
                'default_stage2_id': self.drive_file_id.id,
                'default_folder_id': self.drive_file_id.folder_id2 if self.drive_file_id else 'root',
                'default_viaticos_otros_docs_causacion_pj_id': self.id,
                'create': puede_cargar,
                'edit': puede_cargar,
                'delete': puede_cargar,
            },
            'target': 'current',
        }

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA JURÍDICA (PJ) Comprobante de pago y soporte de pago, orden de pago
    # ------------------------------------------------------------------------------------------------------------------------------------------

    def open_drive_otros_comprobante_de_reembolsos_pj(self):
        # Verificamos si el usuario tiene el grupo de tesorería
        es_tesoreria = self.env.user.has_group('Matriz.group_tesoreria')
        
        # Si no es tesorería, desactivamos la creación y edición/borrado
        # 'create': es_tesoreria (True si pertenece, False si no)
        # 'delete': es_tesoreria
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Comprobantes de reembolsos',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('viaticos_otros_id', '=', self.id)],
            'context': {
                'default_stage2_id': self.drive_file_id.id,
                'default_viaticos_otros_id': self.id,
                # Estas banderas controlan la interfaz del cliente (botones)
                'create': es_tesoreria,
                'edit': es_tesoreria,
                'delete': es_tesoreria,
            },
            'target': 'current',
        }


    orden_pago_pj_document = fields.Binary(
        string="Orden de pago",
        attachment=True,
        help="Orden de pago de la cuenta bancaria.",
    )
    orden_pago_pj_filename = fields.Char(
        string="Nombre de Archivo Orden de pago"
    )
    orden_pago_pj_document_id = fields.Many2one('drive', string='Orden de pago', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    orden_pago_pj_document_visible = fields.Boolean(string="Orden de pago Visible", default=True)

    def action_upload_orden_pagopj__document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Orden de pago',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'orden_Pago_pj_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_orden_pago_pj = {}

    def abrir_documento_orden_pago_pj(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'orden_pago_document_id' está configurado
        if self.orden_pago_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Orden de pago",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.orden_pago_pj_document_id.id,
                "target": "new",
            }

        # Si 'orden_pago_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.orden_pago_pj_filename:
            file_name_to_search = self.orden_pago_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_orden_pago_pj:
                drive_document = self._cache_documentos_orden_pago_pj[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_orden_pago_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'orden_pago_document_id' con el ID del documento encontrado
                self.write({"orden_pago_pj_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Orden de pago",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )

    comprobante_pago_pj_document = fields.Binary(
        string="Comprobante de Pago",
        attachment=True,
        help="Comprobante de pago.",
    )
    comprobante_pago_pj_filename = fields.Char(
        string="Nombre de Archivo Comprobante de Pago"
    )
    comprobante_pago_pj_document_id = fields.Many2one('drive', string='Comprobante de pago', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    comprobante_pago_pj_document_visible = fields.Boolean(string="Comprobante de pago Visible", default=True)

    def action_upload_comprobante_pago_pj_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Comprobante de pago',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Comprobante_Pago_PJ_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_comprobante_pago_pj = {}

    def abrir_documento_comprobante_pago_pj(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'comprobante_pago_pj_document_id' está configurado
        if self.comprobante_pago_pj_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Comprobante de pago",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.comprobante_pago_pj_document_id.id,
                "target": "new",
            }

        # Si 'comprobante_pago_pj_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.comprobante_pago_pj_filename:
            file_name_to_search = self.comprobante_pago_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_comprobante_pago_pj:
                drive_document = self._cache_documentos_comprobante_pago_pj[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_comprobante_pago_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'comprobante_pago_pj_document_id' con el ID del documento encontrado
                self.write({"comprobante_pago_pj_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Comprobante de pago",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )
            
    soporte_de_pago_pj = fields.Binary(string="Soporte de pago", attachment=True)
    soporte_de_pago_pj_filename = fields.Char(string="Nombre de Archivo Soporte de pago")
    soporte_de_pago_pj_document_id = fields.Many2one('drive', string="Soporte de pago", tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    soporte_de_pago_pj_document_visible = fields.Boolean(string="Soporte de pago Visible", default=True)

    def action_upload_soporte_de_pago_pj(self):        
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir soporte de pago',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Soporte_De_Pago_PJ_Documento_legalizacion', 
            },
        }
    
    _cache_documentos_soporte_de_pago_pj = {}

    def abrir_documento_soporte_de_pago_pj(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'soporte_de_pago_pj_document_id' está configurado
        if self.soporte_de_pago_pj_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Soporte de pago",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.soporte_de_pago_pj_document_id.id,
                "target": "new",
            }
        # Si 'soporte_de_pago_pj_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.soporte_de_pago_pj_filename:
            file_name_to_search = self.soporte_de_pago_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_soporte_de_pago_pj:
                drive_document = self._cache_documentos_soporte_de_pago_pj[
                    file_name_to_search
                ]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_soporte_de_pago_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'soporte_de_pago_pj_document_id' con el ID del documento encontrado
                self.write({"soporte_de_pago_pj_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Soporte de pago",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                # Si no se encuentra el documento, lanzar un error

                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )


    rpc_legalizacion = fields.Binary(string="RPC", attachment=True)
    rpc_legalizacion_filename = fields.Char(string="Nombre de Archivo RPC")
    rpc_legalizacion_document_id = fields.Many2one('drive', string='RPC', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    rpc_legalizacion_document_visible = fields.Boolean(string="RPC Visible", default=True)

    rpc_pj_legalizacion = fields.Binary(string="RPC PJ", attachment=True)
    rpc_pj_legalizacion_filename = fields.Char(string="Nombre de Archivo RPC PJ")
    rpc_pj_legalizacion_document_id = fields.Many2one('drive', string='RPC PJ', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    rpc_pj_legalizacion_document_visible = fields.Boolean(string="RPC PJ Visible", default=True)

    def action_upload_rpc_legalizacion_document(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_presupuestos'):
            raise AccessError('Solo Presupuestos puede cargar el documento RPC de la legalización.')
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,
                'default_stage2_id': self.drive_file_id.id,
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'RPC_Documento_legalizacion',
            },
        }

    _cache_documentos_rpc_legalizacion = {}

    def abrir_documento_rpc_legalizacion(self):
        self.ensure_one()

        if self.rpc_legalizacion_document_id:
            return {
                "type": "ir.actions.act_window",
                "name": "RPC",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.rpc_legalizacion_document_id.id,
                "target": "new",
            }

        if self.rpc_legalizacion_filename:
            file_name_to_search = self.rpc_legalizacion_filename.strip().replace(" ", "_")

            if file_name_to_search in self._cache_documentos_rpc_legalizacion:
                drive_document = self._cache_documentos_rpc_legalizacion[file_name_to_search]
            else:
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    drive_document = drive_documents[0]
                    self._cache_documentos_rpc_legalizacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Recupera el vínculo del archivo histórico al consultarlo;
                # no es una carga ni una sustitución del RPC por otro rol.
                self.sudo().write({"rpc_legalizacion_document_id": drive_document.id})

                return {
                    "type": "ir.actions.act_window",
                    "name": "RPC",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado:  {file_name_to_search}"
                )

    def action_upload_rpc_pj_legalizacion_document(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_presupuestos'):
            raise AccessError('Solo Presupuestos puede cargar el documento RPC de la legalización.')
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_id': self.id,
                'default_stage2_id': self.drive_file_id.id,
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'RPC_PJ_Documento_legalizacion',
            },
        }

    _cache_documentos_rpc_pj_legalizacion = {}

    def abrir_documento_rpc_pj_legalizacion(self):
        self.ensure_one()

        if self.rpc_pj_legalizacion_document_id:
            return {
                "type": "ir.actions.act_window",
                "name": "RPC",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.rpc_pj_legalizacion_document_id.id,
                "target": "new",
            }

        if self.rpc_pj_legalizacion_filename:
            file_name_to_search = self.rpc_pj_legalizacion_filename.strip().replace(" ", "_")

            if file_name_to_search in self._cache_documentos_rpc_pj_legalizacion:
                drive_document = self._cache_documentos_rpc_pj_legalizacion[file_name_to_search]
            else:
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    drive_document = drive_documents[0]
                    self._cache_documentos_rpc_pj_legalizacion[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Recuperación interna del vínculo del RPC histórico.
                self.sudo().write({"rpc_pj_legalizacion_document_id": drive_document.id})

                return {
                    "type": "ir.actions.act_window",
                    "name": "RPC",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )




class ViaticoLinea(models.Model):
    _name = 'viaticos_linea'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Línea de Viático'

    viatico_id = fields.Many2one('viaticos', string='Viático', ondelete='cascade')
    fecha = fields.Date(string='Fecha')  # <- no tracking
    nombre_establecimiento = fields.Char(string='Nombre establecimiento', tracking=True)
    cedula_nit = fields.Char(string='Cédula o Nit', tracking=True)
    valor = fields.Float(string='Valor', tracking=True)
    descripcion_del_gasto = fields.Char(string='Descripción del gasto', tracking=True)
    numero_factura = fields.Char(string='Número de factura', tracking=True)
    # ---------------------Variable para vincular con drive.file---------------------------------
    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive', related='viatico_id.drive_file_id') 
    # ------------------------------------------------------------------------------------------------------------------------------------------
    factura = fields.Binary(string="Factura", attachment=True)
    factura_filename = fields.Char(string="Nombre de Archivo Factura")
    factura_id = fields.Many2one('drive', string='Factura')
    factura_visible = fields.Boolean(string="Factura Visible", default=True)

    def action_upload_factura(self):
        self.ensure_one()
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        google_folder_id = self.viatico_id.drive_file_id.folder_id2 if self.viatico_id.drive_file_id and self.viatico_id.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Factura',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_viaticos_linea_id': self.id,
                'default_stage2_id': self.viatico_id.drive_file_id.id,
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'Factura_legalizacion_linea',
            },
        }
    
    _cache_documentos_factura = {}
    
    def abrir_documento_factura(self):
        self.ensure_one()        # Primero, verificamos si 'factura_id' está configurado
        if self.factura_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "Factura",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.factura_id.id,
                "target": "new",
            }

        # Si 'factura_filename' está configurado, intentamos buscarlo por el nombre del archivo
        if self.factura_filename:
            file_name_to_search = self.factura_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_factura:
                drive_document = self._cache_documentos_factura[file_name_to_search]
            else:
                # Buscar documentos en el modelo 'drive' que coincidan insensiblemente con el nombre del archivo
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    # Si se encuentra el documento, usar el primer resultado
                    drive_document = drive_documents[0]

                    # Almacenar el documento en el cache
                    self._cache_documentos_factura[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'factura_id' con el ID del documento encontrado
                self.write({"factura_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "Factura",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                raise UserError(f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}")


class CargoTag(models.Model):
    _name = "cargo_tag"
    _description = "Cargo"

    name = fields.Char(string="Nombre", required=True)









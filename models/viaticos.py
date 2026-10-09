#!/usr/bin/python
from odoo import models, fields, api
from datetime import datetime, timedelta
from .drive import GoogleDriveAuth
import time
import uuid
import threading
from odoo.tools.float_utils import float_is_zero
from odoo.exceptions import ValidationError  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from attr import exceptions

import logging

_logger = logging.getLogger(__name__)

class Viatico(models.Model):
    _name = 'viaticos'
    _description = 'Legalización viáticos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'fecha desc, id desc'

    def name_get(self):
        result = []
        for rec in self:
            base_name = "LEGALIZACIÓN DE GASTOS Y/O ANTICIPOS"
            # Traer consecutivo del anticipo (si existe)
            consecutivo = rec.anticipo_id.id_consecutivo or ''
            # Añadir fecha si existe
            fecha = f" - {rec.fecha.strftime('%d/%m/%Y')}" if rec.fecha else ''
            
            name = f"{base_name} {consecutivo}{fecha}".strip()
            result.append((rec.id, name))
        return result


    proyecto_relacionado_viaticos = fields.Many2one(
        'contratacion',
        string='Proyecto relacionado'
    )
    anticipo_id = fields.Many2one(
        'anticipos',
        string='Anticipo relacionado'
    )
    id_consecutivo = fields.Char(string="Consecutivo", related="anticipo_id.id_consecutivo")

    
    state_viaticos = fields.Selection([
        ('completado', 'Completado'),
        ('aux_tesoreria', 'Aux-tesorería'),
        ('financiera_ye', 'Financiera'),
        ('coordinador', 'Coordinador'),
    ], string='Estado', default='coordinador', tracking=True)


    @api.depends('total_gastos', 'valor_anticipo')
    def _compute_es_reembolso_reintegro(self):
        for rec in self:
            # 🚨 TERCERA CONDICIÓN: Si no hay gastos, no hay reembolso ni reintegro (booleano)
            if float_is_zero(rec.total_gastos, precision_digits=2): 
                rec.es_reembolso = False
                rec.es_reintegro = False
                continue 
            
            # Cálculo de booleanos si hay gastos
            rec.es_reembolso = rec.total_gastos > rec.valor_anticipo
            rec.es_reintegro = rec.total_gastos < rec.valor_anticipo


    es_reembolso = fields.Boolean(string="Es reembolso", compute="_compute_es_reembolso_reintegro", store=True)
    es_reintegro = fields.Boolean(string="Es reintegro", compute="_compute_es_reembolso_reintegro", store=True)





    # DATOS 1
    fecha = fields.Date(string='Fecha')  # <- no tracking porque es Date
    referencia = fields.Char(string='Referencia', tracking=True)
    funcionario_2 = fields.Char(string='Funcionario', related='anticipo_id.nombre_completo', store=True, tracking=True, readonly=False)


    cargo_id = fields.Many2one('cargo_tag', string='Cargo', tracking=True, related='anticipo_id.cargo_id', store=True)

    proyecto = fields.Char(
        string='Proyecto',
        related='proyecto_relacionado_viaticos.new_name',
        store=True,
        tracking=True
    )
    centro_costo = fields.Char(
        string="Centro de costo",
        related='proyecto_relacionado_viaticos.mcb_centro_costo',
        store=True,
        tracking=True
    )

    # FINAL DE LA HOJA
    fecha_solicitud = fields.Date(string='Fecha de solicitud')  # <- no tracking
    valor_anticipo = fields.Float(string="Valor anticipo", tracking=True, related='anticipo_id.valor_anticipo', store=True)
    observaciones = fields.Text(string='Observaciones', tracking=True)
    b_proyecto = fields.Boolean(string="Proyecto")  # <- no tracking
    contratista = fields.Boolean(string="Contratista")  # <- no tracking

    line_ids = fields.One2many('viaticos_linea', 'viatico_id', string='Gastos')

    estado = fields.Selection([
        ('borrador', 'Borrador'),
        ('aprobado', 'Aprobado'),
        ('pagado', 'Pagado'),
        ('cancelado', 'Cancelado')
    ], string='Estado')  # <- opcional, si quieres también tracking=True

    # NUEVO: total de gastos
    total_gastos = fields.Float(
        string="Total Gastos",
        compute="_compute_total_gastos",
        store=True,
        tracking=True
    )

    @api.depends('line_ids.valor')
    def _compute_total_gastos(self):
        for rec in self:
            rec.total_gastos = sum(line.valor for line in rec.line_ids)

    reembolso = fields.Float(
        string="Reembolso",
        compute="_compute_reintegro_reembolso",
        store=True,
        tracking=True
    )
    reintegro = fields.Float(
        string="Reintegro",
        compute="_compute_reintegro_reembolso",
        store=True,
        tracking=True
    )

    @api.depends('total_gastos', 'valor_anticipo')
    def _compute_reintegro_reembolso(self):
        for rec in self:
            diferencia = rec.total_gastos - rec.valor_anticipo
            rec.reembolso = diferencia if diferencia > 0 else 0.0
            rec.reintegro = abs(diferencia) if diferencia < 0 else 0.0

    # -------------------------------------------------------------------------
    # --- MÉTODOS DE NOTIFICACIÓN POR CORREO ---
    # Constantes de estilo de correo 
    COLOR_PRINCIPAL = "#043484"  # Azul (Aprobación)
    COLOR_RECHAZO = "#dc3545"    # Rojo (Rechazo)

    def _construir_cuerpo_correo(self, asunto, nombre_destinatario, usuario_accion, nombre_rol, observaciones, tipo_accion='APROBACION'):
        """Función de ayuda para construir el cuerpo HTML del correo de Legalización."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        # Se usa 'viaticos' en el URL por ser el nombre técnico del modelo
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=viaticos" 
        
        main_color = self.COLOR_PRINCIPAL if tipo_accion == 'APROBACION' else self.COLOR_RECHAZO
        alert_color = "#f8d7da" if tipo_accion == 'RECHAZO' else "#f9f9f9"
        border_color = self.COLOR_RECHAZO if tipo_accion == 'RECHAZO' else self.COLOR_PRINCIPAL

        titulo = f"✅ {asunto}" if tipo_accion == 'APROBACION' else f"🚫 {asunto}"
        
        proyecto_nombre = self.proyecto_relacionado_viaticos.new_name or 'Sin proyecto'
        cargo_nombre = self.cargo_id.display_name if self.cargo_id else 'Sin cargo'
        funcionario_solicitante = self.funcionario_2 or 'Desconocido'

        # Determinar el campo de observaciones a mostrar
        obs_html = f'''
        <div style="margin-top: 20px; padding: 10px; border: 1px solid {self.COLOR_RECHAZO if tipo_accion == 'RECHAZO' else '#ffcc00'}; 
                    background-color: {alert_color}; border-radius: 4px;">
            <strong>Observaciones de {nombre_rol}:</strong><br/>
            <em style="color: {'#721c24' if tipo_accion == 'RECHAZO' else '#856404'};">{observaciones}</em>
        </div>
        ''' if observaciones else ''

        body_html = f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                <div style="background-color: {main_color}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">{titulo}</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                    <p>Hola, <strong>{nombre_destinatario}</strong>:</p>

                    <p>{usuario_accion} ha {'aprobado/enviado' if tipo_accion == 'APROBACION' else 'rechazado'} la **Legalización de Gastos** de **{funcionario_solicitante}**. Por favor, continúe con el proceso.</p>
                    
                    <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {border_color};">
                        <strong>Solicitante:</strong> {funcionario_solicitante}<br/>
                        <strong>Proyecto:</strong> {proyecto_nombre}
                    </div>

                    {obs_html}
                    
                    <p style="text-align: center; margin-top: 30px;">
                        <a href='{formulario_url}' 
                            style="background-color: {self.COLOR_PRINCIPAL}; 
                            color: white; 
                            padding: 10px 20px; 
                            text-decoration: none; 
                            border-radius: 5px; 
                            font-weight: bold; 
                            display: inline-block;">
                            🔗 Ir a la Legalización de Gastos
                        </a>
                    </p>

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        Este es un mensaje automático del sistema.
                    </p>
                </div>
            </div>
        """
        return body_html


    def _enviar_notificacion_legalizacion(self, usuarios_destino, asunto, cuerpo_html, remitente):
        """Envía el correo a una lista de usuarios."""
        for user in usuarios_destino:
            self.env['mail.mail'].sudo().create({
                'subject': asunto,
                'email_from': remitente.email,
                'email_to': user.email,
                'body_html': cuerpo_html,
                'auto_delete': True,
            }).send()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Notificación Enviada',
                'message': f'Se ha notificado a los responsables del área.',
                'type': 'success',
                'sticky': False,
            }
        }
    
    # -------------------------------------------------------------------------
    # --- TRANSICIONES DE APROBACIÓN CON CORREO ---
    # -------------------------------------------------------------------------
    
    def action_to_financiera_ye(self):
        """Pasa de Coordinador a Financiera y Notifica"""
        self.ensure_one()

        # --- VALIDACIÓN DINÁMICA DE DOCUMENTOS ---
        missing_docs = []
        # Validación para Persona Natural (PN)
        if self.person_type == 'pn':
            if not self.rut_pn_document_id:
                missing_docs.append("RUT")
            if not self.doc_identidad_document_id:
                missing_docs.append("Documento de identidad")
            if not self.cert_bancaria_pn_document_id:
                missing_docs.append("Certificación bancaria")

        # Validación para Persona Jurídica (PJ)
        elif self.person_type == 'pj':
            if not self.rut_pj_document_id: # Asegúrate de que estos nombres de campo existan en tu modelo
                missing_docs.append("RUT")
            if not self.cert_bancaria_pj_document_id:
                missing_docs.append("Certificación bancaria")

        if missing_docs:
            # Crea un mensaje listando solo los que faltan, separados por comas
            docs_text = "\n• ".join(missing_docs)
            raise UserError(
                f"⚠️ ¡Atención! No se puede procesar la aprobación.\n\n"
                f"Aún faltan los siguientes documentos obligatorios:\n"
                f"• {docs_text}\n\n"
                f"Por favor, adjunta los archivos faltantes para poder continuar con el proceso."
            )
        
        user_sender = self.env.user
        
        # 1. Obtener destinatarios (Financiera)
        # 🚨 NOTA: Asegúrese que 'Matriz.group_contratacion_financiero' es el ID externo correcto
        grupo_financiera = self.env.ref('Matriz.group_contratacion_financiero')
        usuarios_financiera = self.env['res.users'].search([('groups_id', 'in', grupo_financiera.id)])

        if not usuarios_financiera:
            raise UserError("No se encontraron usuarios en el grupo 'Financiera' para enviar el mensaje.")
            
        # 2. Construir y enviar correos
        for usuario in usuarios_financiera:
            asunto = f"Revisión de Legalización: {self.funcionario_2}"
            cuerpo = self._construir_cuerpo_correo(
                asunto=asunto,
                nombre_destinatario=usuario.name,
                usuario_accion=user_sender.name,
                nombre_rol="Coordinador",
                observaciones=self.observaciones or '',
                tipo_accion='APROBACION'
            )
            self._enviar_notificacion_legalizacion([usuario], asunto, cuerpo, user_sender)
            
        # 3. Cambiar estado
        self.state_viaticos = 'financiera_ye'
        self.message_post(
            body=f"✅ **Legalización Enviada:** Por {user_sender.name} a Financiera para revisión.",
            subtype_xmlid='mail.mt_note'
        )
        return self._enviar_notificacion_legalizacion(usuarios_financiera, asunto, cuerpo, user_sender)


    def action_to_aux_tesoreria(self):
        """Pasa de Financiera a Auxiliar de Tesorería y Notifica"""
        self.ensure_one()
        user_sender = self.env.user
        
        # 1. Obtener destinatarios (Auxiliar de Tesorería)
        # 🚨 NOTA: Asegúrese que 'Matriz.group_tesoreria' es el ID externo correcto
        grupo_tesoreria = self.env.ref('Matriz.group_tesoreria') 
        usuarios_tesoreria = self.env['res.users'].search([('groups_id', 'in', grupo_tesoreria.id)])

        if not usuarios_tesoreria:
            raise UserError("No se encontraron usuarios en el grupo 'Aux-Tesorería' para enviar el mensaje.")
            
        # 2. Construir y enviar correos
        for usuario in usuarios_tesoreria:
            asunto = f"Pago Pendiente: Legalización de {self.funcionario_2}"
            cuerpo = self._construir_cuerpo_correo(
                asunto=asunto,
                nombre_destinatario=usuario.name,
                usuario_accion=user_sender.name,
                nombre_rol="Financiera",
                observaciones=self.observaciones or '',
                tipo_accion='APROBACION'
            )
            self._enviar_notificacion_legalizacion([usuario], asunto, cuerpo, user_sender)

        # 3. Cambiar estado
        self.state_viaticos = 'aux_tesoreria'
        self.message_post(
            body=f"✅ **Aprobación Financiera:** Legalización enviada a Auxiliar de Tesorería por {user_sender.name}.",
            subtype_xmlid='mail.mt_note'
        )
        return self._enviar_notificacion_legalizacion(usuarios_tesoreria, asunto, cuerpo, user_sender)


    def action_to_completado(self):
        """Pasa de Auxiliar de Tesorería a Completado y Notifica al originador (creador)"""
        self.ensure_one()
        user_sender = self.env.user
        
        # 1. Obtener destinatario (El creador del registro)
        usuarios_creador = self.env['res.users'].browse(self.create_uid.id)

        if not usuarios_creador:
            raise UserError("No se encontró el usuario creador para enviar la notificación de completado.")
            
        # 2. Construir y enviar correos
        for usuario in usuarios_creador:
            asunto = f"PAGO COMPLETADO: Legalización de {self.funcionario_2}"
            cuerpo = self._construir_cuerpo_correo(
                asunto=asunto,
                nombre_destinatario=usuario.name,
                usuario_accion=user_sender.name,
                nombre_rol="Tesorería",
                observaciones=self.observaciones or '',
                tipo_accion='APROBACION'
            )
            self._enviar_notificacion_legalizacion([usuario], asunto, cuerpo, user_sender)

        # 3. Cambiar estado
        self.state_viaticos = 'completado'
        self.message_post(
            body=f"🎉 **Legalización Completada:** Pagos procesados por {user_sender.name}.",
            subtype_xmlid='mail.mt_note'
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Proceso Completado',
                'message': 'Se notificó al usuario la finalización del proceso de pago.',
                'type': 'success',
                'sticky': False,
            }
        }

    # -------------------------------------------------------------------------
    # --- TRANSICIONES DE RECHAZO CON CORREO (Usando 'legalizacion') ---
    # -------------------------------------------------------------------------
    
    def action_legalizacion_rechazar_coordinador(self):
        """Rechaza la legalización y la devuelve al Coordinador (creador)"""
        self.ensure_one()
        user_sender = self.env.user
        
        if not self.observaciones:
            raise ValidationError("Debe ingresar un motivo en las 'Observaciones' para rechazar la legalización.")

        # 1. Obtener destinatario (El creador/originador)
        usuarios_creador = self.env['res.users'].browse(self.create_uid.id)

        # 2. Construir y enviar correos
        for usuario in usuarios_creador:
            asunto = f"RECHAZO: Legalización de {self.funcionario_2}"
            cuerpo = self._construir_cuerpo_correo(
                asunto=asunto,
                nombre_destinatario=usuario.name,
                usuario_accion=user_sender.name,
                nombre_rol="Rechazo",
                observaciones=self.observaciones,
                tipo_accion='RECHAZO'
            )
            self._enviar_notificacion_legalizacion([usuario], asunto, cuerpo, user_sender)
            
        # 3. Cambiar estado
        self.state_viaticos = 'coordinador'
        self.message_post(
            body=f"❌ **Legalización Rechazada:** Devuelta a Coordinador por {user_sender.name}. Motivo: {self.observaciones}",
            subtype_xmlid='mail.mt_note'
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Rechazo enviado',
                'message': f'Se notificó al coordinador {usuarios_creador.name} del rechazo.',
                'type': 'warning',
                'sticky': False,
            }
        }

    def action_legalizacion_rechazar_financiera(self):
        """Rechaza la legalización desde Tesorería y la devuelve a Financiera"""
        self.ensure_one()
        user_sender = self.env.user
        
        if not self.observaciones:
            raise ValidationError("Debe ingresar un motivo en las 'Observaciones' para rechazar la legalización.")

        # 1. Obtener destinatarios (Financiera)
        grupo_financiera = self.env.ref('Matriz.group_contratacion_financiero')
        usuarios_financiera = self.env['res.users'].search([('groups_id', 'in', grupo_financiera.id)])

        if not usuarios_financiera:
            raise UserError("No se encontraron usuarios en el grupo 'Financiera' para enviar el mensaje de rechazo.")
            
        # 2. Construir y enviar correos
        for usuario in usuarios_financiera:
            asunto = f"RECHAZO de Tesorería: Legalización de {self.funcionario_2}"
            cuerpo = self._construir_cuerpo_correo(
                asunto=asunto,
                nombre_destinatario=usuario.name,
                usuario_accion=user_sender.name,
                nombre_rol="Tesorería",
                observaciones=self.observaciones,
                tipo_accion='RECHAZO'
            )
            self._enviar_notificacion_legalizacion([usuario], asunto, cuerpo, user_sender)
            
        # 3. Cambiar estado
        self.state_viaticos = 'financiera_ye'
        self.message_post(
            body=f"❌ **Rechazo Tesorería:** Legalización devuelta a Financiera por {user_sender.name}. Motivo: {self.observaciones}",
            subtype_xmlid='mail.mt_note'
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Rechazo enviado',
                'message': 'Se notificó al área financiera del rechazo de Tesorería.',
                'type': 'warning',
                'sticky': False,
            }
        }
    
    person_type = fields.Selection([
        ('pn', 'Persona Natural (PN)'),
        ('pj', 'Persona Jurídica (PJ)'),
        ('Selection', 'Seleccionar tipo de solicitante'),
    ], string='Tipo de solicitante', related='anticipo_id.person_type')
    

# ------------------------------------------------------Crear Carpeta en Google Drive------------------------------------------------------------------------------------
    @api.model
    def create(self, vals):
        # 1. OPTIMIZACIÓN: Si no viene un nombre, lo asignamos en 'vals' ANTES de crear.
        # Esto evita tener que hacer una escritura extra en base de datos.
        if not vals.get('funcionario_2'):
            current_date = datetime.now().strftime("%Y%m%d_%H%M%S")
            vals['funcionario_2'] = f"Folder_{current_date}"

        # 2. Creamos el registro (se guarda automáticamente)
        record = super(Viatico, self).create(vals) 
        # Nota: Asegúrate de que "Viatico" sea el nombre correcto de tu clase (ClassName)

        # 3. Llamamos a la lógica de carpeta con el registro ya creado
        record.buscar_carpeta()

        return record
    
    #------------------------------------------------------ Drive Eliminar ------------------------------------------------------
    def unlink(self):
        # Iterar por cada registro que se va a eliminar
        for record in self:
            # Verificar si existe un archivo de Drive vinculado
            if record.drive_file_id:
                try:
                    # Intentar eliminar el archivo vinculado en Google Drive
                    record.drive_file_id.unlink()
                except Exception as e:
                    raise UserError(f"No se pudo eliminar la carpeta de Google Drive: {str(e)}")
        # Llamar al método original para eliminar el registro de Odoo
        return super(Viatico, self).unlink()

    proceso_en_ejecucion = fields.Boolean(default=False)

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
                    ('name', '=', 'CONTRATOS PS'),
                    ('proyecto_relacionado', '=', self.proyecto_relacionado_viaticos.id),
                ], limit=1)

                if not drive_stage:
                    _logger.warning("No se encontró la carpeta 'CONTRATOS PS' en Odoo. Creando...")
                    drive_stage = self.env['drive_stage'].create({
                        'name': 'CONTRATOS PS',
                        'proyecto_relacionado': self.proyecto_relacionado_viaticos.id,
                        'folder_id': 'some_default_folder_id',
                    })

                _logger.info(f"Carpeta en Odoo encontrada o creada: {drive_stage.name} (ID: {drive_stage.id})")

                # Asignar solo el nombre sin identificador a `new_name`
                if self.funcionario_2:
                    self.funcionario_2 = self.funcionario_2
                
                # Generar nombre con identificador solo para Google Drive
                unique_folder_id = str(uuid.uuid4())[:8]
                # --- LÓGICA AGREGADA PARA PN / PJ ---
                tipo_persona = 'PN' if self.person_type == 'pn' else 'PJ'
                drive_folder_name = f"{self.funcionario_2}_(legalizacion {tipo_persona})_{unique_folder_id}"

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
                                raise exceptions.UserError(f"Error creando la carpeta en Google Drive: {e}")
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

    def action_upload_documento_de_causacion(self):
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

        # Primero, verificamos si 'documento_de_causacion_id' está configurado
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

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA NATURAL (PN) Orden de pago, comprobante de pago soporte de pago
    # ------------------------------------------------------------------------------------------------------------------------------------------
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

        # Primero, verificamos si 'documento_de_causacion_id' está configurado
        if self.documento_de_causacion_id:
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
    

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA JURÍDICA (PJ) Comprobante de pago y soporte de pago, orden de pago
    # ------------------------------------------------------------------------------------------------------------------------------------------


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


















































class ViaticoLinea(models.Model):
    _name = 'viaticos_linea'
    _description = 'Línea de Viático'

    viatico_id = fields.Many2one('viaticos', string='Viático', ondelete='cascade')
    fecha = fields.Date(string='Fecha')  # <- no tracking
    nombre_establecimiento = fields.Char(string='Nombre establecimiento', tracking=True)
    cedula_nit = fields.Char(string='Cédula o Nit', tracking=True)
    valor = fields.Float(string='Valor', tracking=True)
    descripcion_del_gasto = fields.Char(string='Descripción del gasto', tracking=True)


class CargoTag(models.Model):
    _name = "cargo_tag"
    _description = "Cargo"

    name = fields.Char(string="Nombre", required=True)


#!/usr/bin/python

from odoo import models, fields, api
from datetime import datetime, timedelta
from .drive import GoogleDriveAuth
import time
import uuid
import threading
from odoo.exceptions import ValidationError  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from attr import exceptions
from dateutil.relativedelta import relativedelta

import logging

_logger = logging.getLogger(__name__)

class Anticipo(models.Model):
    _name = 'anticipos'
    _description = 'Solicitud de Anticipos'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string='Referencia', readonly=True)
    
    proyecto_id = fields.Many2one('contratacion', string='Proyecto', tracking=True)
    numero_rpc = fields.Char(string="Número RPC", tracking=True)
    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto:', related='proyecto_id.users_with_access')
    
    def name_get(self):
        result = []
        for rec in self:
            name = f"{rec.nombre_completo or ''} - {rec.id_consecutivo or ''}"
            result.append((rec.id, name.strip(" -")))
        return result
    
    fecha_legalizar = fields.Date(string='Fecha a legalizar', tracking=True)

    
    @api.model
    def cron_notificar_anticipos(self):
        hoy = fields.Date.today()
        fecha_objetivo = hoy + timedelta(days=15)

        anticipos = self.search([
            ('fecha_legalizar', '=', fecha_objetivo),
            ('state', '!=', 'completado')
        ])

        for anticipo in anticipos:
            anticipo.enviar_alerta_legalizacion_15_dias()  # 👈 importante
            _logger.info(
                f"Faltan 15 días para legalizar el anticipo: ID {anticipo.id} - {anticipo.display_name}"
            )
            
            
    def enviar_alerta_legalizacion_15_dias(self):
        self.ensure_one()

        COLOR_PRINCIPAL = "#043484"
        grupo_coord = self.env.ref('Matriz.group_contratacion_coordinador')

        coordinadores = self.proyecto_id.users_with_access.filtered(
            lambda u: grupo_coord in u.groups_id
        )

        if not coordinadores:
            return

        filas_html = ""

        for p in self:
            tipo = "Anticipo"
            color_tipo = "#043484"
            # Obtenemos el nombre del funcionario y la etiqueta del estado
            funcionario = p.funcionario_id.name if hasattr(p, 'funcionario_id') else p.display_name
            # Esto obtiene el texto legible del Selection field 'state'
            estado_label = dict(p._fields['state'].selection).get(p.state, p.state)

            filas_html += f"""
                <tr>
                    <td style="padding:10px 8px; border-bottom:1px solid #eee; text-align:left;">{p.fecha_legalizar or 'S/F'}</td>
                    <td style="padding:10px 8px; border-bottom:1px solid #eee; text-align:left;"><b>{funcionario}</b></td>
                    <td style="padding:10px 8px; border-bottom:1px solid #eee; text-align:left;">{estado_label}</td>
                    <td style="padding:10px 8px; border-bottom:1px solid #eee; text-align:left; color:{color_tipo};"><b>{tipo}</b></td>
                </tr>
            """

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&model=anticipos&view_type=form"
        nombre_proyecto = self.proyecto_id.new_name or 'Sin proyecto'

        for coord in coordinadores:
            if not coord.email:
                continue

            body_html = f"""
            <div style="font-family: 'Segoe UI', Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px;">
                
                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">
                        ⚠️ Anticipo próximo a legalizar
                    </h3>
                    <p style="margin: 5px 0 0 0; opacity: 0.9;">Proyecto: {nombre_proyecto}</p>
                </div>

                <div style="background-color: white; padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                    <p>Hola, <strong>{coord.name}</strong>:</p>
                    <p>El siguiente anticipo está próximo a cumplir su fecha de legalización:</p>
                    
                    <table style="width:100%; border-collapse:collapse; margin-top:15px; table-layout: fixed;">
                        <thead>
                            <tr style="background:#f2f2f2; border-bottom: 2px solid #ddd;">
                                <th style="padding:10px 8px; text-align:left; font-size: 13px;">Fecha</th>
                                <th style="padding:10px 8px; text-align:left; font-size: 13px;">Funcionario</th>
                                <th style="padding:10px 8px; text-align:left; font-size: 13px;">Estado</th>
                                <th style="padding:10px 8px; text-align:left; font-size: 13px;">Tipo</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filas_html}
                        </tbody>
                    </table>

                    <div style="margin-top: 25px; padding: 12px; border: 1px solid #ffc107; background-color: #fff3cd; border-radius: 4px; text-align: center;">
                        ⏳ Este anticipo se encuentra a <strong>15 días o menos</strong> de su legalización.
                    </div>
                    
                    <p style="text-align: center; margin-top: 30px;">
                        <a href='{formulario_url}' 
                            style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                            Ir al registro de anticipo
                        </a>
                    </p>

                    <p style="margin-top: 30px; font-size: 11px; color: #999; text-align: center; border-top: 1px solid #eee; padding-top: 15px;">
                        Este es un mensaje automático generado por el Sistema de Gestión Financiera.
                    </p>
                </div>
            </div>
            """

            self.env['mail.mail'].sudo().create({
                'subject': f"⚠️ Alerta: Anticipo por legalizar - {nombre_proyecto}",
                'body_html': body_html,
                'email_to': coord.email,
            }).send()

        
    @api.constrains('documento_identidad', 'numero', 'proyecto_id')
    def _check_anticipo_sin_legalizar(self):
        for record in self:
            doc_nro = record.numero or record.documento_identidad

            if not doc_nro or not record.proyecto_id:
                continue

            anticipos_previos = self.search([
                ('id', '!=', record.id),
                ('proyecto_id', '=', record.proyecto_id.id),
                '|',
                ('numero', '=', doc_nro),
                ('documento_identidad', '=', doc_nro),
            ])

            for ant in anticipos_previos:

                if ant.state != 'completado':
                    raise ValidationError(
                        f"🚫 Bloqueo: El funcionario {doc_nro} ya tiene un anticipo en curso "
                        f"en este proyecto ({record.proyecto_id.name or 'sin nombre'})."
                    )

                legalizacion_completada = self.env['viaticos'].search([
                    ('anticipo_id', '=', ant.id),
                    ('state_viaticos', '=', 'completado'),
                ], limit=1)

                if not legalizacion_completada:
                    numero_anticipo = ant.id_consecutivo or "SIN NÚMERO ASIGNADO"

                    raise ValidationError(
                        f"🚫 Legalización pendiente:\n\n"
                        f"ANTICIPO: [{numero_anticipo}]\n\n"
                        f"Este anticipo aún no ha sido legalizado. "
                        f"Debe finalizar el proceso de legalización del anticipo anterior "
                        f"para poder solicitar uno nuevo."
                    )

                        
    # --- MÉTODO GATILLO: NOTIFICACIÓN Y RECÁLCULO ---
    def _notificar_recalculo_fuv(self, valor_completado=False):
        """
        Sincroniza el cambio con el FUV, actualiza los totales de anticipos 
        y el saldo de producción, dejando trazabilidad.
        """
        for rec in self:
            if rec.proyecto_id:
                # 1. EL FLUSH: Obliga a escribir los cambios del anticipo en la DB
                self.env.flush_all()
                
                _logger.info("⚙️ GATILLO: Sincronizando Anticipo %s con Proyecto %s", rec.id_consecutivo, rec.proyecto_id.new_name)
                
                # 2. Buscar el registro FUV relacionado
                # Es un recálculo técnico desencadenado por el anticipo ya
                # completado; no es una edición manual de la contratación.
                # Se ejecuta como sistema para respetar el control de estados
                # del flujo contractual y no otorgar permisos extra a Tesorería.
                fuv_records = self.env['contrataciones_fuv'].with_context(
                    recalculo_anticipo_completado=True
                ).sudo().search([
                    ('proyecto_relacionado', '=', rec.proyecto_id.id)
                ])
                
                if fuv_records:
                    # A. Marcamos AMBOS campos como modificados para invalidar el caché
                    fuv_records.modified(['gasto_anticipos', 'saldo_disponible_produccion'])
                    
                    # B. Forzamos primero el recálculo del total de anticipos
                    fuv_records.obtener_anticipos()
                    
                    # C. Forzamos después el recálculo del saldo (que usa el valor de arriba)
                    fuv_records._compute_saldo_disponible_produccion()
                    
                    # D. Flush final para asegurar que los nuevos cálculos se guarden
                    self.env.flush_all()

                # 3. Mensaje de éxito en el Chatter
                if valor_completado:
                    valor_formateado = "${:,.2f}".format(rec.valor_anticipo)
                    mensaje = (
                        f"<div style='font-family: Arial, sans-serif; padding: 10px; border-left: 5px solid #28a745; background-color: #f4faf4;'>"
                        f"<b style='color: #28a745;'>✅ PROCESO FINALIZADO CON ÉXITO</b><br/>"
                        f"<p style='margin: 5px 0;'>El anticipo ha pasado a estado <b>completado</b>.</p>"
                        f"<p style='margin: 5px 0;'>Se ha actualizado el total de anticipos y descontado del <b>Disponible de Producción</b> el valor de: "
                        f"<span style='color: #043484; font-size: 15px; font-weight: bold;'>{valor_formateado}</span></p>"
                        f"</div>"
                    )
                    rec.message_post(body=mensaje)

    def write(self, vals):
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            return True

        user = self.env.user

        estados_anteriores = {record.id: record.state for record in self}

        campos_identidad = ['doc_nit', 'doc_cc', 'doc_extranjera']
        intentando_editar_identidad = any(campo in vals for campo in campos_identidad)

        for record in self:
            if record.state == 'completado' and 'state' not in vals:
                raise ValidationError("❌ No puedes editar un anticipo que ya ha sido completado.")

            es_coordinador = user.has_group("Matriz.group_contratacion_coordinador") or \
                            user.has_group("Matriz.group_contratacion_asis_coordinador")
            es_ventanilla = user.has_group("Matriz.group_contratacion_ventanilla_unica")
            es_tesoreria = user.has_group("Matriz.group_tesoreria")

            if intentando_editar_identidad and not es_coordinador:
                raise ValidationError("🚫 Solo el Coordinador puede modificar los campos de tipo de documento de identidad.")

            if record.state == 'coordinador' and not es_coordinador:
                raise ValidationError("🚫 No tienes permisos para editar mientras está en manos del Coordinador.")

            # Estado conservado únicamente para registros históricos. Ventanilla
            # es la responsable de incorporarlos al nuevo flujo.
            if record.state == 'financiera' and not es_ventanilla:
                raise ValidationError("🚫 Solo Ventanilla puede incorporar un anticipo histórico al flujo actual.")

            if record.state == 'ventanilla' and not es_ventanilla:
                raise ValidationError("🚫 No tienes permisos para editar mientras está en Ventanilla.")

            if record.state == 'tesoreria' and not es_tesoreria:
                raise ValidationError("🚫 No tienes permisos para editar mientras está en Tesorería.")

            if es_ventanilla:
                nuevo_consecutivo = vals.get("id_consecutivo", record.id_consecutivo)
                if not nuevo_consecutivo:
                    raise ValidationError("⚠️ El campo 'Consecutivo' es obligatorio para el área de ventanilla.")

        res = super(Anticipo, self).write(vals)

        paso_a_completado = any(
            estados_anteriores.get(record.id) != 'completado' and record.state == 'completado'
            for record in self
        )

        # El presupuesto de Producción solo se afecta cuando Tesorería completa
        # el anticipo. Enviar de Ventanilla a Tesorería no debe recalcular ni
        # intentar escribir la contratación asociada.
        if paso_a_completado:
            self._notificar_recalculo_fuv(valor_completado=True)

        return res
                
    
    es_ahorro = fields.Boolean(string='Ahorro', tracking=True)
    es_corriente = fields.Boolean(string='Corriente', tracking=True)
    
    fecha_solicitud = fields.Date(string='Fecha solicitud', tracking=True)


    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto', related='proyecto_id.users_with_access')
    
    id_consecutivo = fields.Char(string="Consecutivo", tracking=True)
    centro_costo = fields.Char(string='Centro de costo', related='proyecto_id.mcb_centro_costo', store=True)
    # Campo auxiliar para la vista
    es_usuario_ventanilla = fields.Boolean(compute='_compute_es_usuario_ventanilla')

    def _compute_es_usuario_ventanilla(self):
        for rec in self:
            # Verificamos si el usuario tiene el grupo de ventanilla
            rec.es_usuario_ventanilla = self.env.user.has_group('Matriz.group_contratacion_ventanilla_unica')
            
    # Nombres y apellidos deben ser campos de texto (Char), no relaciones
    nombres = fields.Char(string='Nombres', required=True, tracking=True)
    apellidos = fields.Char(string='Apellidos', tracking=True)
    correo = fields.Char(string='Correo electrónico', tracking=True)
    nombre_completo = fields.Char(string='Nombre completo', compute='_compute_nombre_completo', store=True)
    # Campos para identificar el tipo de persona y controlar la visibilidad
    person_type = fields.Selection([
        ('pn', 'Persona Natural (PN)'),
        ('pj', 'Persona Jurídica (PJ)')
    ], string='Tipo de persona', required=True, tracking=True)
        
    @api.depends('nombres', 'apellidos')
    def _compute_nombre_completo(self):
        for rec in self:
            rec.nombre_completo = f"{rec.nombres or ''} {rec.apellidos or ''}".strip()

    # Mantener referencia al usuario (opcional en el formulario)
    funcionario_2 = fields.Char(string='Funcionario', store=True, tracking=True)
    documento_identidad = fields.Char(string='Documento identidad', tracking=True)
    # Asegúrate de que no haya otro 'cargo_2' definido más arriba o más abajo
    cargo_id = fields.Many2one(
        comodel_name='cargo_tag', 
        string='Cargo', 
        tracking=True
    )
    
    cargo_2 = fields.Char(
        string='Cargo Secundario', 
        tracking=True
    )
    # Campos adicionales: número y ciudad (separados)
    numero = fields.Char(string='No. Documento')
    digito_verificacion = fields.Char(string='Dígito de verificación')
    ciudad_residencia = fields.Many2one(comodel_name='municipio', string="Ciudad de Residencia", tracking=True)

    ciudad2 = fields.Selection([
        ('bogota', 'Bogotá'),
        ('medellin', 'Medellín'),
        ('cali', 'Cali'),
        ('barranquilla', 'Barranquilla'),
        ('cartagena', 'Cartagena'),
        ('bucaramanga', 'Bucaramanga'),
        ('pereira', 'Pereira'),
        ('manizales', 'Manizales'),
        ('armenia', 'Armenia'),
        ('ibague', 'Ibagué'),
        ('villavicencio', 'Villavicencio'),
        ('pasto', 'Pasto'),
        ('santamarta', 'Santa Marta'),
        ('monteria', 'Montería'),
        ('sincelejo', 'Sincelejo'),
        ('valledupar', 'Valledupar'),
        ('popayan', 'Popayán'),
        ('neiva', 'Neiva'),
        ('tunja', 'Tunja'),
        ('riohacha', 'Riohacha'),
        ('quibdo', 'Quibdó'),
        ('cucuta', 'Cúcuta'),
        ('palmira', 'Palmira'),
        ('envigado', 'Envigado'),
        ('itagui', 'Itagüí'),
        ('soacha', 'Soacha'),
    ], string='Ciudad', tracking=True)

    # Tipos de documento (seleccionables mediante checkboxes)
    doc_nit = fields.Boolean(string='NIT', tracking=True)
    doc_cc = fields.Boolean(string='CC', tracking=True)
    doc_extranjera = fields.Boolean(string='C. Extranjería', tracking=True)

    @api.onchange('doc_nit')
    def _onchange_doc_nit(self):
        if self.doc_nit:
            self.doc_cc = False
            self.doc_extranjera = False

    @api.onchange('doc_cc')
    def _onchange_doc_cc(self):
        if self.doc_cc:
            self.doc_nit = False
            self.doc_extranjera = False

    @api.onchange('doc_extranjera')
    def _onchange_doc_extranjera(self):
        if self.doc_extranjera:
            self.doc_nit = False
            self.doc_cc = False

    def tipos_documentos(self):
        if self.doc_nit:
            return "NIT"
        elif self.doc_cc:
            return "CC"
        elif self.doc_extranjera:
            return "C. Extranjería"
        else:
            return "No se ha seleccionado ningún tipo de documento"    
    
    
    
    valor_anticipo = fields.Float(string='Valor anticipo', tracking=True)
    valor_letras = fields.Char(string='Valor en letra', tracking=True)
    # Sustituye es_ahorro y es_corriente por este:
    tipo_cuenta = fields.Selection([
        ('ahorro', 'Cuenta de ahorros'),
        ('corriente', 'Cuenta corriente')
    ], string="Tipo de cuenta", default='ahorro')
    cuenta_ahorros = fields.Char(string='Número de cuenta bancaria', tracking=True)
    pagare_firmado = fields.Boolean(string='Pagare firmado', tracking=True)
    observaciones_coordinador = fields.Text(string='Observaciones Coordinador', tracking=True)
    observaciones_ventanilla = fields.Text(string='Observaciones Ventanilla', tracking=True)
    observaciones_presupuesto = fields.Text(string='Observaciones Presupuesto', tracking=True)
    observaciones_tesoreria = fields.Text(string='Observaciones Tesorería', tracking=True)
    motivo = fields.Text(string='Motivo de la solicitud', tracking=True)

    # Link to viaticos: an anticipo can be legalized with one viático
    viatico_id = fields.Many2one('viaticos', string='Viático relacionado')

    state = fields.Selection([
        ('coordinador', 'Coordinador'),
        # Se conserva para no invalidar anticipos creados antes del cambio.
        # No se usa para registros nuevos.
        ('financiera', 'Pendiente de migración (legado)'),
        ('ventanilla', 'Ventanilla'),
        ('tesoreria', 'Tesorería'),
        ('completado', 'Completado'),
    ], string='Estado', default='coordinador', tracking=True)

    create_uid = fields.Many2one('res.users', string='Creado por', readonly=True)
    write_uid = fields.Many2one('res.users', string='Última modificación por', readonly=True)
    def action_to_ventanilla(self):
        self.write({'state': 'ventanilla'})
        
    def action_to_tesoreria(self):
        """Pasa de financiera a tesorería"""
        self.write({'state': 'tesoreria'})


    def action_reset_to_coordinador(self):
        """Reinicia el estado a coordinador"""
        self.write({'state': 'coordinador'})
    
    def action_completar(self):
        for rec in self:
            if rec.state == 'completado':
                raise UserError(
                    "🚫 Este anticipo ya está completado. No se puede completar nuevamente."
                )

        self.write({'state': 'completado'})



    # Sobrescribir el método search para aplicar filtros basados en roles
    @api.model
    def search(self, args=None, offset=0, limit=None, order=None, count=False):
        args = args or []
        user = self.env.user

        # 1. VISIÓN TOTAL: El Coordinador y su Asistente ven TODOS los estados
        if user.has_group("Matriz.group_contratacion_coordinador") or \
           user.has_group("Matriz.group_contratacion_asis_coordinador"):
            # Al no añadir ningún filtro de 'state', Odoo les permite ver todo
            return super(Anticipo, self).search(args, offset=offset, limit=limit, order=order, count=count)

        # 2. LÓGICA ADITIVA: Para los demás roles, sumamos permisos
        estados_permitidos = ['completado'] # Todos ven lo finalizado

        if user.has_group("Matriz.group_contratacion_ventanilla_unica"):
            estados_permitidos.append('ventanilla')
            # Permite sacar los registros creados antes de este cambio.
            estados_permitidos.append('financiera')

        if user.has_group("Matriz.group_tesoreria"):
            estados_permitidos.append('tesoreria')

        # 3. Aplicar el filtro de estados recolectados
        args.append(('state', 'in', estados_permitidos))

        return super(Anticipo, self).search(args, offset=offset, limit=limit, order=order, count=count)

    ######################################################################## Aprobaciones de anticipos ###########################################################################
    # Envio de notificaciones via correo
    # Envía directamente a Ventanilla; Presupuestos no interviene en anticipos.
    def aprueba_coordinador_anticipos(self, documentos=None):
        self.ensure_one()
                # --- VALIDACIÓN DINÁMICA DE DOCUMENTOS ---
        missing_docs = []
        # Validación para Persona Natural (PN)
        if self.person_type == 'pn':
            if not self.rut_pn_document_id:
                missing_docs.append("RUT")
            if not self.compromiso_legalizacion_pn_document_id:
                missing_docs.append("Compromiso de legalización")
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
            if not self.cuenta_cobro_pj_document_id:
                missing_docs.append("Cuenta de cobro")

        if missing_docs:
            # Crea un mensaje listando solo los que faltan, separados por comas
            docs_text = "\n• ".join(missing_docs)
            raise UserError(
                f"⚠️ ¡Atención! No se puede procesar la aprobación.\n\n"
                f"Aún faltan los siguientes documentos obligatorios:\n"
                f"• {docs_text}\n\n"
                f"Por favor, adjunta los archivos faltantes para poder continuar con el proceso."
            )
            
            
        # 1. Obtener usuarios de Ventanilla con acceso al proyecto.
        grupo_ventanilla = self.env.ref('Matriz.group_contratacion_ventanilla_unica')
        usuarios_ventanilla = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_ventanilla in user.groups_id
        )

        if not usuarios_ventanilla:
            raise UserError(f"No hay usuarios de Ventanilla Única asignados con acceso al proyecto: {self.proyecto_id.name}")

        coordinador = self.env.user
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        observaciones = self.observaciones_coordinador or ''

        # Construir URL del formulario
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"

        # Cargar datos comunes
        
        cargo_nombre = self.cargo_2 if self.cargo_2 else 'Sin cargo'
        COLOR_PRINCIPAL = "#043484"

        # Enviar correo a cada usuario de Ventanilla.
        for usuario in usuarios_ventanilla:
            nombre_usuario_ventanilla = usuario.name

            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">Aprobación de anticipo: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{nombre_usuario_ventanilla}</strong>:</p>

                        <p>El coordinador <strong>{coordinador.name}</strong> ha aprobado el anticipo para:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
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
                                Ir al formulario de anticipo
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Este es un mensaje automático de SGO.
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"Aprobación de anticipo para: {self.nombre_completo} del proyecto; {nombre_proyecto}",
                'email_from': coordinador.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # Cambiar estado directamente a Ventanilla.
        self.action_to_ventanilla()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }
       
       
    def migrar_anticipo_legacy_a_ventanilla(self):
        """Incorpora al nuevo flujo los anticipos que estaban en Presupuestos."""
        self.ensure_one()
        if self.state != 'financiera':
            raise UserError("Este anticipo no requiere migración.")
        self.action_to_ventanilla()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Anticipo migrado',
                'message': 'El anticipo fue enviado a Ventanilla sin validación de Presupuesto.',
                'type': 'success',
                'sticky': False,
            }
        }

    def devolver_ventanilla_a_coordinador(self):
        """Devuelve el anticipo a Coordinación sin pasar por Presupuestos."""
        self.ensure_one()
        if self.state != 'ventanilla' or not self.env.user.has_group('Matriz.group_contratacion_ventanilla_unica'):
            raise UserError("🚫 Solo Ventanilla puede devolver un anticipo que esté en su etapa.")
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asistente = self.env.ref('Matriz.group_contratacion_asis_coordinador')
        destinatarios = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asistente in user.groups_id
        )

        if not destinatarios:
            raise UserError(
                f"No se encontraron usuarios de Coordinación asignados al proyecto: {self.proyecto_id.name}."
            )

        ventanilla = self.env.user
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        observaciones = self.observaciones_ventanilla or '' 

        if not observaciones:
            raise UserError("Para devolver a Coordinación, escribe el motivo en 'Observaciones Ventanilla'.")
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"
        cargo_nombre = self.cargo_2 if self.cargo_2 else 'Sin cargo'
        COLOR_PRINCIPAL = "#043484" 
        COLOR_ALERTA = "#f39c12"

        for usuario in destinatarios:
            nombre_destinatario = usuario.name
            
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_ALERTA}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">⚠️ Devolución de Ventanilla: Proyecto <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{nombre_destinatario}</strong>:</p>

                        <p>El área de <strong>Ventanilla Única</strong> ha devuelto el anticipo para correcciones:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_ALERTA};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
                        </div>

                        <div style="margin-top: 20px; padding: 10px; border: 1px solid {COLOR_ALERTA}; background-color: #fff4e5; border-radius: 4px;">
                            <strong>Observaciones (Revisar):</strong><br/>
                            <em style="color: #a67c00;">{observaciones}</em>
                        </div>
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                📝 Revisar-Corregir
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Mensaje enviado por: {ventanilla.name} desde Ventanilla Única.
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"DEVOLUCIÓN: Anticipo {self.nombre_completo} - {nombre_proyecto}",
                'email_from': ventanilla.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        self.action_reset_to_coordinador()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }

    def aprueba_ventanilla_anticipos(self):
        self.ensure_one()
        
        if not self.id_consecutivo:
            raise UserError(
                "⚠️ No se puede enviar a Tesorería.\n\n"
                "El campo Consecutivo es obligatorio antes de continuar."
            )

        # 1. Obtener el grupo de Tesorería
        grupo_tesoreria = self.env.ref('Matriz.group_tesoreria')

        # 2. Obtener usuarios filtrando la lista de acceso del proyecto (users_with_access)
        usuarios_tesoreria = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_tesoreria in user.groups_id
        )

        # 3. Validación: Si no hay nadie asignado en el proyecto con ese rol
        if not usuarios_tesoreria:
            raise UserError(
                f"No hay usuarios de Tesorería asignados con acceso al proyecto: {self.proyecto_id.name}. "
                "Por favor, asigne a un responsable de Tesorería en la lista de acceso del proyecto."
            )

        # 4. Datos del remitente y proyecto
        # En este punto el que aprueba es el usuario de Ventanilla
        ventanilla_user = self.env.user 
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        observaciones = self.observaciones_ventanilla or '' 

        # Configuración de URL y Datos Comunes
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"
        cargo_nombre = self.cargo_2 if self.cargo_2 else 'Sin cargo'
        COLOR_PRINCIPAL = "#043484" 

        # 5. Enviar correo a cada Tesorero del proyecto
        for usuario in usuarios_tesoreria:
            nombre_usuario_tesoreria = usuario.name
            
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">✅ Aprobación de Ventanilla del proyecto: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{nombre_usuario_tesoreria}</strong>:</p>

                        <p>El área de <strong>Ventanilla Única</strong> (Usuario: <strong>{ventanilla_user.name}</strong>) ha <strong>APROBADO</strong> y verificado este anticipo. Por favor, proceda con el pago:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
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
                                Ir al formulario de anticipo
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Este es un mensaje automático de SGO.
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"Acción de pago requerida: Anticipo {self.nombre_completo} ({nombre_proyecto})",
                'email_from': ventanilla_user.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()
            
        # 6. Cambiar estado a Tesorería
        self.action_to_tesoreria() 

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }
        
    #Sección de devoluciones y retornos entre Ventanilla y Coordinador
    devuelto_por_ventanilla = fields.Boolean(string="Devuelto por Ventanilla")
    def devolver_a_coordinador_desde_ventanilla(self):
        self.ensure_one()
        if self.state != 'ventanilla' or not self.env.user.has_group('Matriz.group_contratacion_ventanilla_unica'):
            raise UserError("🚫 Solo Ventanilla puede devolver un anticipo que esté en su etapa.")
        
        # 1. Validar observaciones obligatorias (Ventanilla debe decir qué corregir)
        if not self.observaciones_ventanilla:
            raise UserError("Debe indicar en las observaciones el motivo de la devolución al Coordinador.")

        # 2. Activar el booleano para habilitar el botón de retorno luego
        self.devuelto_por_ventanilla = True

        # 3. Obtener destinatarios (Coordinadores y Asistentes)
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asistente = self.env.ref('Matriz.group_contratacion_asis_coordinador')
        
        destinatarios = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asistente in user.groups_id
        )

        if not destinatarios:
            raise UserError("No se encontraron coordinadores o asistentes asignados a este proyecto para devolver el trámite.")

        # 4. Datos del remitente y configuración visual
        ventanilla_user = self.env.user
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        observaciones = self.observaciones_ventanilla
        
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"
        
        COLOR_PRINCIPAL = "#043484"
        COLOR_AVISO = "#ffc107"  # Amarillo/Naranja para indicar "atención/corrección"

        # 5. Enviar correos con diseño
        for usuario in destinatarios:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_AVISO}; color: #000; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">⚠️ Devolución de anticipo para corrección</h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{usuario.name}</strong>:</p>

                        <p>El área de <strong>Ventanilla Única</strong> ha revisado la solicitud y solicita realizar correcciones antes de proceder:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
                        </div>

                        <div style="margin-top: 20px; padding: 15px; border: 1px solid #ffeeba; background-color: #fff3cd; border-radius: 4px;">
                            <strong>Motivo de la devolución:</strong><br/>
                            <em style="color: #856404;">{observaciones}</em>
                        </div>
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; 
                                color: white; 
                                padding: 12px 25px; 
                                text-decoration: none; 
                                border-radius: 5px; 
                                font-weight: bold; 
                                display: inline-block;">
                                📝 Revisar-Corregir
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Enviado por: {ventanilla_user.name} - Ventanilla Única.
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"Correción requeridad: Anticipo {self.nombre_completo} ({nombre_proyecto})",
                'email_from': ventanilla_user.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # 6. Cambiar estado (De vuelta al coordinador)
        self.action_reset_to_coordinador() 

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }
        
        
    def retornar_a_ventanilla(self):
        self.ensure_one()

        if self.state != 'coordinador' or not (
            self.env.user.has_group('Matriz.group_contratacion_coordinador') or
            self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
        ):
            raise UserError("🚫 Solo Coordinación puede reenviar un anticipo devuelto a Ventanilla.")

        # 1. Validación de seguridad
        if not self.devuelto_por_ventanilla:
            raise UserError("Esta acción solo es permitida si el trámite fue devuelto previamente por Ventanilla.")

        # 2. Obtener el grupo de Ventanilla (Asegúrate de que este sea el ID técnico correcto)
        # Si el grupo tiene otro nombre, cámbialo aquí:
        grupo_ventanilla = self.env.ref('Matriz.group_contratacion_ventanilla_unica') 

        # 3. Filtrar destinatarios desde la lista de acceso del proyecto
        usuarios_ventanilla = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_ventanilla in user.groups_id
        )

        if not usuarios_ventanilla:
            raise UserError(f"No hay usuarios de Ventanilla Única asignados al proyecto: {self.proyecto_id.name}.")

        # 4. Preparar datos para el correo
        coordinador = self.env.user
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        # Usamos el campo de observaciones (ajusta el nombre si usas otro para el coordinador)
        observaciones = self.observaciones_coordinador or '' 
        
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"
        COLOR_PRINCIPAL = "#043484"

        # 5. Enviar correos a Ventanilla
        for usuario in usuarios_ventanilla:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">🔄 Trámite Re-enviado: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{usuario.name}</strong>:</p>

                        <p>El Coordinador <strong>{coordinador.name}</strong> ha realizado las correcciones solicitadas y ha retornado el anticipo para su revisión en Ventanilla Única:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
                        </div>

                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid #004085; background-color: #cce5ff; border-radius: 4px;">
                            <strong>Observaciones del Coordinador:</strong><br/>
                            <em style="color: #004085;">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                Revisar
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Este es un mensaje automático de SGO.
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"Retorno: Anticipo de {self.nombre_completo}, fue corregido. ({nombre_proyecto})",
                'email_from': coordinador.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # 6. Limpiar el booleano y cambiar estado
        self.devuelto_por_ventanilla = False
        self.write({'state': 'ventanilla'})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }
    
    
    # Notificar al Coordinador/Asistente cuando Tesorería aprueba (Pago Realizado)
    def aprueba_tesoreria_anticipos(self):
        self.ensure_one()
        if self.state == 'completado':
            raise UserError(
                "🚫 Este anticipo ya fue completado anteriormente.\n\n"
                "La acción de Tesorería solo puede ejecutarse una vez."
            )

        if self.state != 'tesoreria':
            raise UserError(
                "🚫 Esta acción solo puede ejecutarse cuando el anticipo está en estado Tesorería."
            )
        
        
        # --- VALIDACIÓN DINÁMICA DE DOCUMENTOS ---
        missing_docs = []
        # Validación para Persona Natural (PN)
        if self.person_type == 'pn':
            if not self.comprobante_pago_document_id:
                missing_docs.append("Comprobante de pago")
            if not self.soporte_de_pago_document_id:
                missing_docs.append("Soporte de pago")
        
        # Validación para Persona Jurídica (PJ)
        elif self.person_type == 'pj':
            if not self.comprobante_pago_pj_document_id:
                missing_docs.append("Comprobante de pago")
            if not self.soporte_de_pago_pj_document_id:
                missing_docs.append("Soporte de pago")

        if missing_docs:
            # Crea un mensaje listando solo los que faltan, separados por comas
            docs_text = "\n• ".join(missing_docs)
            raise UserError(
                f"⚠️ ¡Atención! No se puede procesar la aprobación.\n\n"
                f"Aún faltan los siguientes documentos obligatorios:\n"
                f"• {docs_text}\n\n"
                f"Por favor, adjunta los archivos faltantes para poder continuar con el proceso."
            )

        # 1. Obtener grupos de Coordinación y Asistencia
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asistente = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # 2. Filtrar destinatarios desde la lista de acceso del proyecto
        # Notificamos a los responsables del proyecto que el trámite terminó
        destinatarios = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asistente in user.groups_id
        )

        if not destinatarios:
            # Si no hay nadie en el proyecto, al menos notificamos al que creó el registro
            destinatarios = self.create_uid

        tesoreria = self.env.user # Usuario de Tesorería que cierra el proceso
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        observaciones = self.observaciones_tesoreria or '' 

        # Configuración de URL y Estética (Color Verde para Éxito/Pago)
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"
        cargo_nombre = self.cargo_2 if self.cargo_2 else 'Sin cargo'
        
        COLOR_EXITO = "#28a745" # Verde (Completado/Pagado)
        COLOR_BOTON = "#043484" # Azul institucional

        # 3. Enviar correo de "Proceso Finalizado" a cada responsable
        for usuario in destinatarios:
            nombre_destinatario = usuario.name
            
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_EXITO}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">💰 Anticipo finalizado: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{nombre_destinatario}</strong>:</p>

                        <p>Le informamos que el proceso de anticipo ha sido <strong>COMPLETADO</strong> exitosamente por el área de Tesorería:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_EXITO};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Consecutivo:</strong> {self.id_consecutivo or 'N/A'}<br/>
                            <strong>Valor Pagado:</strong> ${self.valor_anticipo:,.2f}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
                        </div>

                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid #ddd; background-color: #f1f1f1; border-radius: 4px;">
                            <strong>Notas finales de Tesorería:</strong><br/>
                            <em style="color: #555;">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_BOTON}; 
                                color: white; 
                                padding: 10px 20px; 
                                text-decoration: none; 
                                border-radius: 5px; 
                                font-weight: bold; 
                                display: inline-block;">
                                📂 Ver registro
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Este es el cierre automático del flujo en SGO. Gracias por su gestión.
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"FINALIZADO: Anticipo Pagado - {self.nombre_completo} ({nombre_proyecto})",
                'email_from': tesoreria.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # 4. Cambiar estado a 'completado' (Bloqueo definitivo de edición)
        self.action_completar() 

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }

 

    ######################################################################## Rechazo de anticipos ###############################################################################
    #Rechaza presupuestos y devuelve al coordinador
    def rechaza_financiero_anticipos(self):
        self.ensure_one()

        # 1. Obtener referencias de los grupos necesarios
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        # Ajusta el ID técnico del asistente según tu módulo
        grupo_asistente = self.env.ref('Matriz.group_contratacion_asis_coordinador') 

        # 2. Filtrar destinatarios desde la lista de acceso del proyecto
        # Buscamos usuarios que estén en users_with_access Y pertenezcan a cualquiera de los dos grupos
        destinatarios = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asistente in user.groups_id
        )

        if not destinatarios:
            raise UserError("No se encontraron coordinadores o asistentes asignados con acceso a este proyecto.")

        # 3. Datos del remitente y proyecto
        financiero = self.env.user
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        observaciones = self.observaciones_presupuesto or '' 

        if not observaciones:
            raise UserError("Es obligatorio indicar el motivo del rechazo en el campo de observaciones.")

        # 4. Configuración visual y URL
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"
        cargo_nombre = self.cargo_2 if self.cargo_2 else 'Sin cargo'
        COLOR_PRINCIPAL = "#043484" 
        COLOR_RECHAZO = "#dc3545" 

        # 5. Enviar correos individuales a cada destinatario filtrado
        for usuario in destinatarios:
            nombre_destinatario = usuario.name
            
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {COLOR_RECHAZO}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">🚫 Rechazo de Anticipo: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{nombre_destinatario}</strong>:</p>

                        <p>Se le informa que el área presupuestos ha <strong>RECHAZADO</strong> la solicitud de anticipo para:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_RECHAZO};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
                        </div>

                        <div style="margin-top: 20px; padding: 10px; border: 1px solid {COLOR_RECHAZO}; background-color: #f8d7da; border-radius: 4px;">
                            <strong>Motivo del Rechazo (Presupuesto):</strong><br/>
                            <em style="color: #721c24;">{observaciones}</em>
                        </div>
                        
                        <p style="text-align: center; margin-top: 30px;">
                            <a href='{formulario_url}' 
                                style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                📝 Revisar-Corregir
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Mensaje enviado por: {financiero.name} del área presupuestos.
                        </p>
                    </div>
                </div>
            """
            
            self.env['mail.mail'].sudo().create({
                'subject': f"RECHAZO: Anticipo {self.nombre_completo} - {nombre_proyecto}",
                'email_from': financiero.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # 6. Cambiar estado y devolver flujo
        self.action_reset_to_coordinador()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }



    # Devuelve a Ventanilla cuando Tesorería encuentra una inconsistencia.
    def rechaza_tesoreria_anticipos(self):
        self.ensure_one()
        if self.state != 'tesoreria' or not self.env.user.has_group('Matriz.group_tesoreria'):
            raise UserError("🚫 Solo Tesorería puede devolver un anticipo que esté en su etapa.")

        # Presupuestos no participa en Anticipos. Se notifica únicamente a
        # Ventanilla y solo a usuarios asignados al proyecto.
        grupo_ventanilla = self.env.ref('Matriz.group_contratacion_ventanilla_unica')
        usuarios_financieros = self.proyecto_id.users_with_access.filtered(
            lambda user: grupo_ventanilla in user.groups_id
        )

        if not usuarios_financieros:
            raise UserError("No hay usuarios de Ventanilla asignados a este proyecto.")

        tesoreria = self.env.user # Usuario que rechaza
        nombre_proyecto = self.proyecto_id.display_name or 'Sin proyecto'
        
        # Asumo que hay un campo para observaciones de Rechazo de Tesoreria
        observaciones = self.observaciones_tesoreria or '' 

        if not observaciones:
            raise UserError("Las observaciones de rechazo de Tesorería son obligatorias.")

        # Construir URL del formulario
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        formulario_url = f"{base_url}/web#id={self.id}&view_type=form&model=anticipos"
        cargo_nombre = self.cargo_2 if self.cargo_2 else 'Sin cargo'
        
        
        
        # Colores
        COLOR_PRINCIPAL = "#043484" # Azul (para el botón)
        COLOR_RECHAZO = "#dc3545" # Rojo (para la alerta y encabezado)

        # Enviar correo a cada usuario financiero
        for usuario in usuarios_financieros:
            nombre_usuario_financiero = usuario.name
            
            # Construcción del 'body_html'
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    
                    <div style="background-color: {COLOR_RECHAZO}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">🚫 Rechazo de Tesorería del: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        
                        <p>Hola, <strong>{nombre_usuario_financiero}</strong>:</p>

                        <p>El área de <strong>Tesorería</strong> (Usuario: <strong>{tesoreria.name}</strong>) ha <strong>RECHAZADO</strong> el anticipo de:</p>
                        
                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_RECHAZO};">
                            <strong>Solicitante:</strong> {self.nombre_completo}<br/>
                            <strong>Cargo:</strong> {cargo_nombre}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}
                        </div>

                        <div style="margin-top: 20px; padding: 10px; border: 1px solid {COLOR_RECHAZO}; background-color: #f8d7da; border-radius: 4px;">
                            <strong>Motivo del Rechazo (Tesorería):</strong><br/>
                            <em style="color: #721c24;">{observaciones}</em>
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
                                📝 Revisar-Corregir
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            Mensaje enviado por: {tesoreria.name}.
                        </p>
                    </div>
                </div>
            """
            
            # Crear y enviar el correo
            self.env['mail.mail'].sudo().create({
                'subject': f"Rechazo de Tesorería: Anticipo de {self.nombre_completo} ({nombre_proyecto})",
                'email_from': tesoreria.email,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        # Cambiar estado 
        self.action_to_ventanilla() 

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Mensaje enviado',
                'message': 'Proceso realizado correctamente.',
                'type': 'success',
                'sticky': False,
            }
        }
    #############################################################################################################################################################################




   # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA PERSONA NATURAL (PN)
    # Utilizamos fields.Binary para almacenar el archivo. Sugiero un 'attachment=True' para que Odoo lo gestione como adjunto.
    # ------------------------------------------------------------------------------------------------------------------------------------------



    @api.model
    def create(self, vals):
        
        # 1. OPTIMIZACIÓN: Nombre de carpeta si no viene
        if not vals.get('nombre_completo'):
            current_date = datetime.now().strftime("%Y%m%d_%H%M%S")
            vals['nombre_completo'] = f"Folder_{current_date}"

        # 2. Creamos el registro (Base de Odoo)
        record = super(Anticipo, self).create(vals) 

        # --- AUTOMATIZACIÓN: Tracking Dinámico de Campos ---
        detalles = []
        for field_name, field in self._fields.items():
            
            # Filtramos: Solo campos con tracking=True que fueron llenados en la creación
            if getattr(field, 'tracking', False) and field_name in vals:
                label = field.string
                valor = record[field_name] # Obtenemos el valor ya procesado por Odoo
                
                # Formateo inteligente según el tipo de campo
                if field.type == 'many2one':
                    valor_txt = valor.display_name if valor else "No asignado"
                elif field.type in ['monetary', 'float']:
                    valor_txt = "${:,.2f}".format(valor)
                elif field.type == 'boolean':
                    valor_txt = "✅" if valor else "❌"
                elif field.type in ['date', 'datetime']:
                    valor_txt = valor.strftime('%d/%m/%Y') if valor else ""
                else:
                    valor_txt = str(valor) if valor else ""

                if valor_txt:
                    detalles.append(f"<li><b>{label}:</b> {valor_txt}</li>")

        if detalles:
            mensaje_log = f"🚀 <b>Registro inicializado con los siguientes datos:</b><ul>{''.join(detalles)}</ul>"
            record.message_post(body=mensaje_log)
            


        # 3. Lógica de carpeta en Drive
        record.buscar_carpeta()

        # Un anticipo en creación todavía no afecta el presupuesto. El
        # recálculo se ejecuta exclusivamente al completarse en Tesorería.

        return record




    # -----------------------------------------------------------------------------------------
    # Drive Eliminar y Recálculo de Saldo Disponible
    # -----------------------------------------------------------------------------------------
    def unlink(self):
        # Un anticipo relacionado es la base de los valores y la trazabilidad de
        # una legalización. No se puede eliminar sin dejar el trámite inconsistente.
        legalizaciones = self.env['viaticos'].search([
            ('anticipo_id', 'in', self.ids)
        ])
        if legalizaciones:
            anticipos_con_legalizacion = legalizaciones.mapped('anticipo_id')
            referencias = ', '.join(
                anticipo.id_consecutivo or anticipo.nombre_completo or str(anticipo.id)
                for anticipo in anticipos_con_legalizacion
            )
            raise ValidationError(
                "🚫 No se puede eliminar el anticipo porque está relacionado con una "
                f"legalización. Anticipo(s): {referencias}."
            )

        # 1. CAPTURAR: Guardamos los proyectos afectados para procesarlos después del borrado
        # Esto es vital porque tras el super().unlink(), la relación en 'self' se pierde
        proyectos_afectados = self.mapped('proyecto_id')

        for record in self:
            # --- LOGGER DE SEGURIDAD (Antes de borrar) ---
            _logger.warning(
                "♻️ RESTAURACIÓN: Iniciando eliminación del anticipo [%s]. "
                "Proyecto relacionado: %s. El valor de $%s será reintegrado al saldo disponible.", 
                record.id_consecutivo or 'Sin Consecutivo', 
                record.proyecto_id.new_name if record.proyecto_id else 'Sin Proyecto',
                record.valor_anticipo
            )

            # 2. Lógica de Google Drive
            if record.drive_file_id:
                try:
                    # Intentamos eliminar la carpeta vinculada en la nube
                    record.drive_file_id.unlink()
                    _logger.info("📁 DRIVE: Carpeta eliminada correctamente para el anticipo %s", record.id_consecutivo)
                except Exception as e:
                    # Si falla Drive, lanzamos error para evitar que el registro se borre en Odoo sin borrar en la nube
                    raise UserError(f"No se pudo eliminar la carpeta de Google Drive: {str(e)}")
        
        # 3. EJECUCIÓN: Llamada al método original para eliminar los registros de Odoo
        res = super(Anticipo, self).unlink()

        # 4. GATILLO: Actualizar saldos en Contrataciones FUV
        for proyecto in proyectos_afectados:
            if proyecto:
                # Buscamos los registros en contrataciones_fuv que dependen de este proyecto madre
                fuv_records = self.env['contrataciones_fuv'].with_context(
                    recalculo_anticipo_completado=True
                ).sudo().search([
                    ('proyecto_relacionado', '=', proyecto.id)
                ])
                
                if fuv_records:
                    # Forzamos el recálculo del campo computado en el otro modelo
                    fuv_records.modified(['saldo_disponible_produccion'])
                    
                    _logger.info(
                        "✅ SALDO ACTUALIZADO: Se ha recalculado el presupuesto del proyecto [%s] "
                        "debido a la eliminación de uno o más anticipos.", 
                        proyecto.new_name
                    )

        return res

    proceso_en_ejecucion = fields.Boolean(default=False)

    def buscar_carpeta(self):
        """Busca o crea la carpeta relacionada en Odoo y en Google Drive."""
        if self.proyecto_id:
            if self.proceso_en_ejecucion:
                _logger.info("Proceso de búsqueda de carpeta ya en ejecución, esperando a que termine.")
                return

            self.proceso_en_ejecucion = True

            try:
                _logger.info(f"Buscando carpeta para el proyecto relacionado: {self.proyecto_id.name}")

                # Buscar o crear carpeta en Odoo
                drive_stage = self.env['drive_stage'].search([
                    ('name', '=', '3. CONTRATOS PS'),
                    ('proyecto_relacionado', '=', self.proyecto_id.id),
                ], limit=1)

                if not drive_stage:
                    _logger.warning("No se encontró la carpeta '3. CONTRATOS PS' en Odoo. Creando...")
                    drive_stage = self.env['drive_stage'].create({
                        'name': '3. CONTRATOS PS',
                        'proyecto_relacionado': self.proyecto_id.id,
                        'folder_id': 'some_default_folder_id',
                    })

                _logger.info(f"Carpeta en Odoo encontrada o creada: {drive_stage.name} (ID: {drive_stage.id})")

                # Asignar solo el nombre sin identificador a `new_name`
                if self.nombre_completo:
                    self.nombre_completo = self.nombre_completo
                
                # Generar nombre con identificador solo para Google Drive
                unique_folder_id = str(uuid.uuid4())[:8]
                # --- LÓGICA AGREGADA PARA PN / PJ ---
                tipo_persona = 'PN' if self.person_type == 'pn' else 'PJ'
                drive_folder_name = f"{self.nombre_completo}_(Anticipo {tipo_persona})_{unique_folder_id}"

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

    rut_pn_document = fields.Binary(
        string="RUT (PN)",
        attachment=True,
        help="Registro Único Tributario (Persona Natural)",
    )
    rut_pn_filename = fields.Char(string="Nombre de Archivo RUT (PN)")
     # ---------------------Variable nueva --------------------------------
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
                'default_anticipos_id': self.id,
                
                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 
                
                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 
                
                'tipo_documento_upload': 'Rut_PN_Documento', 
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Doc_Identidad_Documento', 
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Cert_Bancaria_PN_Documento', 
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
    # DOCUMENTOS REQUERIDOS PARA (PN) - Compromiso Legalización
    # ------------------------------------------------------------------------------------------------------------------------------------------
    compromiso_legalizacion_pn_document = fields.Binary(
        string="Compromiso Legalización (PN)",
        attachment=True,
        help="Compromiso de legalización (Persona Natural)",
    )
    compromiso_legalizacion_pn_filename = fields.Char(string="Nombre de Archivo Compromiso Legalización (PN)")

    compromiso_legalizacion_pn_document_id = fields.Many2one('drive', string='Compromiso Legalización', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    compromiso_legalizacion_pn_document_visible = fields.Boolean(string="Compromiso Legalización (PN) Visible", default=True)

    def action_upload_compromiso_legalizacion_pn_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Compromiso Legalización',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_anticipos_id': self.id,
                'default_stage2_id': self.drive_file_id.id,
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'Compromiso_Legalizacion_PN_Documento',
            },
        }

    _cache_documentos_compromiso_legalizacion_pn = {}

    def abrir_documento_compromiso_legalizacion_pn(self):
        self.ensure_one()

        if self.compromiso_legalizacion_pn_document_id:
            return {
                "type": "ir.actions.act_window",
                "name": "Compromiso Legalización",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.compromiso_legalizacion_pn_document_id.id,
                "target": "new",
            }

        if self.compromiso_legalizacion_pn_filename:
            file_name_to_search = self.compromiso_legalizacion_pn_filename.strip().replace(" ", "_")

            if file_name_to_search in self._cache_documentos_compromiso_legalizacion_pn:
                drive_document = self._cache_documentos_compromiso_legalizacion_pn[file_name_to_search]
            else:
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    drive_document = drive_documents[0]
                    self._cache_documentos_compromiso_legalizacion_pn[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                self.write({"compromiso_legalizacion_pn_document_id": drive_document.id})
                return {
                    "type": "ir.actions.act_window",
                    "name": "Compromiso Legalización",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )

            
    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA (PN) RPC y AT
    # ------------------------------------------------------------------------------------------------------------------------------------------
    rpc_document = fields.Binary(
        string="RPC",
        attachment=True,
        help="Registro de Proponentes del Contratista",
    )
    rpc_filename = fields.Char(string="Nombre de Archivo RPC")  

    rpc_document_id = fields.Many2one('drive', string='RPC', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    rpc_document_visible = fields.Boolean(string="RPC Visible", default=True)  

    def action_upload_rpc_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'RPC_Documento', 
            },
        }   
    
    _cache_documentos_rpc = {}

    def abrir_documento_rpc(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'rpc_document_id' está configurado
        if self.rpc_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "RPC",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.rpc_document_id.id,
                "target": "new",
            }

        # Si 'rpc_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rpc_filename:
            file_name_to_search = self.rpc_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rpc:
                drive_document = self._cache_documentos_rpc[
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
                    self._cache_documentos_rpc[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rpc_document_id' con el ID del documento encontrado
                self.write({"rpc_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "RPC",
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
            

    at_document = fields.Binary(
        string="AT",    
        attachment=True,
        help="Antecedentes Tributarios",)    
    at_filename = fields.Char(string="Nombre de Archivo AT")

    at_document_id = fields.Many2one('drive', string='AT', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    at_document_visible = fields.Boolean(string="AT Visible", default=True)

    def action_upload_at_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir AT',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'AT_Documento', 
            },
        }
    
    _cache_documentos_at = {}

    def abrir_documento_at(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'at_document_id' está configurado
        if self.at_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "AT",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.at_document_id.id,
                "target": "new",
            }

        # Si 'at_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.at_filename:
            file_name_to_search = self.at_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_at:
                drive_document = self._cache_documentos_at[
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
                    self._cache_documentos_at[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'at_document_id' con el ID del documento encontrado
                self.write({"at_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "AT",
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
    # DOCUMENTOS REQUERIDOS PARA PERSONA NATURAL (PN) Comprobante de pago y soporte de pago
    # ------------------------------------------------------------------------------------------------------------------------------------------
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Comprobante_Pago_Documento', 
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Soporte_De_Pago_Documento', 
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Rut_PJ_Documento', 
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Camara_Comercio_Documento', 
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Cert_Bancaria_PJ_Documento', 
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
    # DOCUMENTOS REQUERIDOS PARA (PJ) - Cuenta de cobro
    # ------------------------------------------------------------------------------------------------------------------------------------------
    cuenta_cobro_pj_document = fields.Binary(
        string="Cuenta de cobro (PJ)",
        attachment=True,
        help="Cuenta de cobro (Persona Jurídica)",
    )
    cuenta_cobro_pj_filename = fields.Char(string="Nombre de Archivo Cuenta de cobro (PJ)")

    cuenta_cobro_pj_document_id = fields.Many2one('drive', string='Cuenta de cobro', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    cuenta_cobro_pj_document_visible = fields.Boolean(string="Cuenta de cobro (PJ) Visible", default=True)

    def action_upload_cuenta_cobro_pj_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir Cuenta de cobro',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_anticipos_id': self.id,
                'default_stage2_id': self.drive_file_id.id,
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': 'Cuenta_Cobro_PJ_Documento',
            },
        }

    _cache_documentos_cuenta_cobro_pj = {}

    def abrir_documento_cuenta_cobro_pj(self):
        self.ensure_one()

        if self.cuenta_cobro_pj_document_id:
            return {
                "type": "ir.actions.act_window",
                "name": "Cuenta de cobro",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.cuenta_cobro_pj_document_id.id,
                "target": "new",
            }

        if self.cuenta_cobro_pj_filename:
            file_name_to_search = self.cuenta_cobro_pj_filename.strip().replace(" ", "_")

            if file_name_to_search in self._cache_documentos_cuenta_cobro_pj:
                drive_document = self._cache_documentos_cuenta_cobro_pj[file_name_to_search]
            else:
                drive_documents = self.env["drive"].search(
                    [("file_name", "ilike", file_name_to_search)], limit=1
                )

                if drive_documents:
                    drive_document = drive_documents[0]
                    self._cache_documentos_cuenta_cobro_pj[file_name_to_search] = drive_document
                else:
                    drive_document = None

            if drive_document:
                self.write({"cuenta_cobro_pj_document_id": drive_document.id})
                return {
                    "type": "ir.actions.act_window",
                    "name": "Cuenta de cobro",
                    "res_model": "drive",
                    "view_mode": "form",
                    "res_id": drive_document.id,
                    "target": "new",
                }
            else:
                raise UserError(
                    f"Documento no encontrado en el modelo Drive. Nombre buscado: {file_name_to_search}"
                )

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # DOCUMENTOS REQUERIDOS PARA (PN) RPC y AT
    # ------------------------------------------------------------------------------------------------------------------------------------------

    rpc_pj_document = fields.Binary(
        string="RPC",
        attachment=True,
        help="Registro de Proponentes del Contratista",
    )
    rpc_pj_filename = fields.Char(string="Nombre de Archivo RPC")  

    rpc_pj_document_id = fields.Many2one('drive', string='RPC', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    rpc_pj_document_visible = fields.Boolean(string="RPC Visible", default=True)  

    def action_upload_rpc_pj_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir RPC',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'rpc_PJ_Documento', 
            },
        }
    
    _cache_documentos_rpc_pj = {}

    def abrir_documento_rpc_pj(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'rpc_pj_document_id' está configurado
        if self.rpc_pj_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "RPC",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.rpc_pj_document_id.id,
                "target": "new",
            }

        # Si 'rpc_pj_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.rpc_pj_filename:
            file_name_to_search = self.rpc_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_rpc_pj:
                drive_document = self._cache_documentos_rpc_pj[
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
                    self._cache_documentos_rpc_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'rpc_pj_document_id' con el ID del documento encontrado
                self.write({"rpc_pj_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "RPC",
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
            
    
    at_pj_document = fields.Binary(
        string="AT",
        attachment=True,
        help="Antecedentes Tributarios",
    )
    at_pj_filename = fields.Char(string="Nombre de Archivo AT")

    at_pj_document_id = fields.Many2one('drive', string='AT', tracking=True, domain="[('stage2_id', '=', drive_file_id)]")
    at_pj_document_visible = fields.Boolean(string="AT Visible", default=True)

    def action_upload_at_pj_document(self):
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id

        # 1. Obtenemos el ID de Google Drive real (string) desde folder_id2
        # Usamos 'root' si el campo es False para evitar errores.
        google_folder_id = self.drive_file_id.folder_id2 if self.drive_file_id and self.drive_file_id.folder_id2 else 'root'

        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir AT',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'AT_PJ_Documento', 
            },
        }
    
    _cache_documentos_at_pj = {}

    def abrir_documento_at_pj(self):
        # Asegurarse de que solo se maneje un registro a la vez
        self.ensure_one()

        # Primero, verificamos si 'at_pj_document_id' está configurado
        if self.at_pj_document_id:
            # Si está relacionado, abrir el registro en el modelo 'drive'
            return {
                "type": "ir.actions.act_window",
                "name": "AT",
                "res_model": "drive",
                "view_mode": "form",
                "res_id": self.at_pj_document_id.id,
                "target": "new",
            }

        # Si 'at_pj_filename' no está configurado, intentamos buscarlo por el nombre del archivo
        if self.at_pj_filename:
            file_name_to_search = self.at_pj_filename.strip().replace(" ", "_")

            # Comprobar si el documento ya está en el cache
            if file_name_to_search in self._cache_documentos_at_pj:
                drive_document = self._cache_documentos_at_pj[
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
                    self._cache_documentos_at_pj[file_name_to_search] = (
                        drive_document
                    )
                else:
                    drive_document = None

            if drive_document:
                # Actualizar el campo 'at_pj_document_id' con el ID del documento encontrado
                self.write({"at_pj_document_id": drive_document.id})

                # Abrir el registro en la vista de formulario
                return {
                    "type": "ir.actions.act_window",
                    "name": "AT",
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
    # DOCUMENTOS REQUERIDOS PARA PERSONA JURÍDICA (PJ) Comprobante de pago y soporte de pago
    # ------------------------------------------------------------------------------------------------------------------------------------------

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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Comprobante_Pago_PJ_Documento', 
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
                'default_anticipos_id': self.id,

                # MANTENIDO: ID del registro de carpeta de Odoo (stage2_id)
                'default_stage2_id': self.drive_file_id.id, 

                # CORRECCIÓN CLAVE: Pasamos la ID de Google Drive directamente
                'default_folder_id': google_folder_id, 

                'tipo_documento_upload': 'Soporte_De_Pago_PJ_Documento', 
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

#!/usr/bin/python
from enum import Enum

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from .flujo_configuracion import obtener_fecha_inicio_nuevo_flujo_contratacion


# Enum centraliza los valores tecnicos del flujo de contratacion.
# Cada constante tiene dos partes:
# - El nombre en mayuscula, que usamos en Python: EstadoContratacion.VENTANILLA.
# - El valor string, que Odoo guarda en la base de datos: 'ventanilla'.
#
# Cuando necesites agregar un estado nuevo:
# 1. Agrega una constante aqui.
# 2. Agregala tambien en ESTADOS_CONTRATACION_NUEVOS si es un estado nuevo
#    que no existe en el Selection original de contrataciones_fuv.py.
# 3. Agrega su ondelete en la extension del campo estado_contratacion.
# 4. Luego, si aplica, agregala en las vistas XML para que se vea en pantalla.
#
# No cambies el valor string de un estado que ya tenga registros guardados,
# porque Odoo guarda ese string en la base de datos. Si quieres cambiar el texto
# visible para el usuario, cambia solo el label en ESTADOS_CONTRATACION_NUEVOS.
class EstadoContratacion(str, Enum):
    COORDINADOR = 'coordinador'
    VENTANILLA = 'ventanilla'
    PRESUPUESTO = 'presupuesto'
    DIRECTOR_JURIDICO = 'director_juridico'
    JURIDICO_1 = 'juridico_1'
    FINANCIERA = 'financiera'
    JURIDICO = 'juridico'
    RECURSOS_HUMANOS = 'recursos_humanos'
    JURIDICO_2 = 'juridico_2'
    FIRMA = 'firma'
    POLIZAS = 'polizas'
    CONTRATADO = 'contratado'
    SUSPENDIDO = 'suspendido'
    FINALIZADO = 'finalizado'
    LIQUIDADO = 'liquidado'
    ANULADO = 'anulado'


## Esta lista solo contiene estados agregados desde este archivo hijo.
# Los estados originales del modelo base, como coordinador o contratado,
# permanecen definidos en contrataciones_fuv.py y por eso no se repiten aqui.
#
# Formato de cada entrada:
# (valor_tecnico_guardado_en_bd, texto_visible_en_odoo)
ESTADOS_CONTRATACION_NUEVOS = [
    (EstadoContratacion.VENTANILLA.value, 'Ventanilla'),
    (EstadoContratacion.PRESUPUESTO.value, 'Presupuesto'),
    (EstadoContratacion.DIRECTOR_JURIDICO.value, 'Dir. Jurídico'),
    (EstadoContratacion.JURIDICO_1.value, 'Jurídico 1 - Revisión'),
    (EstadoContratacion.JURIDICO_2.value, 'Jurídico 2 - Contrato'),
    (EstadoContratacion.FIRMA.value, 'Coord. - Subir contrato firmado'),
    (EstadoContratacion.POLIZAS.value, 'Pólizas (Eventual)'),
    (EstadoContratacion.SUSPENDIDO.value, 'Suspendido'),
    (EstadoContratacion.FINALIZADO.value, 'Finalizado'),
    (EstadoContratacion.LIQUIDADO.value, 'Liquidado (Eventual)'),
    (EstadoContratacion.ANULADO.value, 'Anulado (Eventual)'),
]


#Es un "switch" lógico. Permite que el sistema conviva con registros antiguos y nuevos basándose en la fecha de creación del contrato.
class ContratacionesFuvAprobaciones(models.Model):
    _inherit = 'contrataciones_fuv'

    estado_contratacion = fields.Selection(
        selection_add=ESTADOS_CONTRATACION_NUEVOS,
        ondelete={
            EstadoContratacion.VENTANILLA.value: 'set default',
            EstadoContratacion.PRESUPUESTO.value: 'set default',
            EstadoContratacion.DIRECTOR_JURIDICO.value: 'set default',
            EstadoContratacion.JURIDICO_1.value: 'set default',
            EstadoContratacion.JURIDICO_2.value: 'set default',
            EstadoContratacion.FIRMA.value: 'set default',
            EstadoContratacion.POLIZAS.value: 'set default',
            EstadoContratacion.SUSPENDIDO.value: 'set default',
            EstadoContratacion.FINALIZADO.value: 'set default',
            EstadoContratacion.LIQUIDADO.value: 'set default',
            EstadoContratacion.ANULADO.value: 'set default',
        },
    )

    # Diccionario que mapea estados con grupos de seguridad. Es la "matriz de control de acceso"
    def _grupos_edicion_por_estado_flujo_nuevo(self):
        return {
            EstadoContratacion.COORDINADOR.value: [
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ],
            EstadoContratacion.VENTANILLA.value: [
                'Matriz.group_contratacion_ventanilla_unica',
            ],
            EstadoContratacion.PRESUPUESTO.value: [
                'Matriz.group_presupuestos',
            ],
            EstadoContratacion.DIRECTOR_JURIDICO.value: [
                'Matriz.group_contratacion_director_juridico',
            ],
            EstadoContratacion.JURIDICO_1.value: [
                'Matriz.group_contratacion_juridico',
            ],
            EstadoContratacion.RECURSOS_HUMANOS.value: [
                'Matriz.group_contratacion_recursos',
            ],
            EstadoContratacion.JURIDICO_2.value: [
                'Matriz.group_contratacion_juridico',
            ],
            EstadoContratacion.FIRMA.value: [
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ],
        }

    def _es_escritura_at_posterior_presupuesto(self, vals):
        """Autoriza únicamente los campos técnicos del AT fuera de Presupuesto.

        Cubre la apertura del formulario y el registro del archivo cargado por
        Drive. No permite modificar otros documentos, datos generales ni el
        estado de la contratación.
        """
        if not self.env.context.get('gestion_at_posterior_presupuesto'):
            return False
        if not (
            self.env.su
            or self.env.user.has_group('Matriz.group_soporte')
            or self.env.user.has_group('Matriz.group_presupuestos')
        ):
            return False
        campos_at_permitidos = {
            'write_date',
            'anexo_tributario',
            'anexo_tributario_doc',
            'anexo_tributario_doc_fname',
            'anexo_tributario_doc_visible',
            'anexo_tributario_id',
            'anexo_tributario_actualizar_visible',
            'anexo_tributario_juridico',
            'anexo_tributario_juridico_doc',
            'anexo_tributario_juridico_doc_fname',
            'anexo_tributario_juridico_doc_visible',
            'anexo_tributario_juridico_doc_id',
            'anexo_tributario_juridico_actualizar_visible',
        }
        if not vals or set(vals) - campos_at_permitidos:
            return False
        return all(record.usa_flujo_contratacion_nuevo for record in self)

    def _validar_gestion_at_posterior_presupuesto(self):
        """Comprueba el rol que puede gestionar posteriormente el AT."""
        if (
            self.env.su
            or self.env.user.has_group('Matriz.group_soporte')
            or self.env.user.has_group('Matriz.group_presupuestos')
        ):
            return
        raise UserError(_(
            'Solo Presupuesto puede crear o gestionar posteriormente el '
            'Anexo Tributario en el flujo nuevo.'
        ))


    # Es una función de seguridad llamada dentro del write. Verifica si el usuario actual tiene el grupo correcto 
    # para el estado actual del contrato. Si no, lanza un UserError impidiendo la modificación.
    # En pocas palabras el grupo soporte no tiene limitaciones dentro de cada flujo
    def _validar_edicion_por_estado_flujo_nuevo(self):
        if (
            # self.env.su: El usuario es el superusuario (admin con modo debug).
            self.env.su 
            # O se ha pasado explícitamente en el código una bandera para ignorar esta validación.
            or self.env.context.get('skip_validacion_estado_flujo_nuevo')
            # O el usuario pertenece al grupo 'Soporte'.
            # Esto permite que los administradores de TI o soporte técnico puedan
            # corregir datos o desbloquear situaciones críticas en cualquier etapa 
            # del flujo sin importar en qué estado esté el documento.
            or self.env.user.has_group('Matriz.group_soporte')
        ):
            return

        grupos_por_estado = self._grupos_edicion_por_estado_flujo_nuevo()
        for record in self:
            if not record.usa_flujo_contratacion_nuevo:
                continue

            grupos_permitidos = grupos_por_estado.get(record.estado_contratacion)
            if grupos_permitidos and not any(
                self.env.user.has_group(grupo)
                for grupo in grupos_permitidos
            ):
                nombre_estado = record._nombre_estado_flujo_nuevo(record.estado_contratacion)
                raise UserError(
                    f"⛔ No tienes permisos para editar esta contratación en el estado {nombre_estado}."
                )

    def write(self, vals): #Ejecuta la grabación real en la base de datos.
        if not self._es_escritura_at_posterior_presupuesto(vals):
            self._validar_edicion_por_estado_flujo_nuevo()

        asigna_juridico = (
            'usuarios_juridico_ids' in vals
            and not self.env.context.get('skip_flujo_nuevo_juridico_1')
        )
        candidatos = self.filtered(
            lambda record: (
                record.usa_flujo_contratacion_nuevo
                and record.estado_contratacion == EstadoContratacion.DIRECTOR_JURIDICO.value
            )
        ) if asigna_juridico else self.env[self._name]

        result = super().write(vals)
                
        for record in candidatos.filtered(lambda item: item.usuarios_juridico_ids):
            vals_juridico_1 = {
                'estado_contratacion': EstadoContratacion.JURIDICO_1.value,
            }
            if record.select_persona == 'persona_juridica':
                vals_juridico_1['generar_informe_visible_j'] = True
            record.with_context(
                skip_flujo_nuevo_juridico_1=True,
                skip_validacion_estado_flujo_nuevo=True,
            ).write(vals_juridico_1)
            record.message_post(
                body=(
                    "<p><b>Director Jurídico asignó la contratación.</b></p>"
                    "<p>El flujo nuevo avanza al estado <b>Jurídico</b>.</p>"
                )
            )
        return result
    
    
    usuario_es_presupuesto = fields.Boolean(compute='_compute_usuario_es_presupuesto')
    puede_requerir_poliza = fields.Boolean(
        compute='_compute_puede_requerir_poliza',
        string='Puede requerir póliza',
    )

    def _compute_usuario_es_presupuesto(self):
        es_presupuesto = self.env.user.has_group('Matriz.group_presupuestos')
        for record in self:
            record.usuario_es_presupuesto = es_presupuesto

    @api.depends('usuarios_juridico_ids')
    @api.depends_context('uid')
    def _compute_puede_requerir_poliza(self):
        es_director_juridico = self.env.user.has_group(
            'Matriz.group_contratacion_director_juridico'
        )
        es_juridico = self.env.user.has_group('Matriz.group_contratacion_juridico')
        for record in self:
            record.puede_requerir_poliza = es_director_juridico or (
                es_juridico and self.env.user in record.usuarios_juridico_ids
            )
    
    
    usa_flujo_contratacion_nuevo = fields.Boolean(
        string='Usa flujo nuevo de contratación',
        compute='_compute_usa_flujo_contratacion_nuevo',
        store=True,
        readonly=True,
    )

    flujo_contratacion_version = fields.Selection(
        [
            ('anterior', 'Flujo anterior'),
            ('nuevo', 'Flujo nuevo'),
        ],
        string='Flujo de contratación',
        compute='_compute_usa_flujo_contratacion_nuevo',
        store=True,
        readonly=True,
    )
    
    observaciones_ventanilla = fields.Html('Observaciones Ventanilla', tracking=True)
    observaciones_presupuesto = fields.Html('Observaciones Presupuesto', tracking=True)
    ultimo_estado_devolucion = fields.Selection(
        selection=ESTADOS_CONTRATACION_NUEVOS + [
            (EstadoContratacion.COORDINADOR.value, 'Coordinador'),
            (EstadoContratacion.FINANCIERA.value, 'Financiera'),
            (EstadoContratacion.JURIDICO.value, 'Jurídico'),
            (EstadoContratacion.RECURSOS_HUMANOS.value, 'Recursos humanos'),
            (EstadoContratacion.CONTRATADO.value, 'Contratado'),
        ],
        string='Último estado que devolvió',
        tracking=True,
    )
    ultimo_usuario_devolucion_id = fields.Many2one(
        'res.users',
        string='Último usuario que devolvió',
        tracking=True,
    )
    estado_retorno_devolucion = fields.Selection(
        selection=ESTADOS_CONTRATACION_NUEVOS + [
            (EstadoContratacion.COORDINADOR.value, 'Coordinador'),
            (EstadoContratacion.FINANCIERA.value, 'Financiera'),
            (EstadoContratacion.JURIDICO.value, 'Jurídico'),
            (EstadoContratacion.RECURSOS_HUMANOS.value, 'Recursos humanos'),
            (EstadoContratacion.CONTRATADO.value, 'Contratado'),
        ],
        string='Estado al que debe retornar',
        tracking=True,
    )
    
    
    
    # Campos booleanos calculados que controlan la visibilidad de los paneles 
    # de aprobación en la vista de formulario del nuevo flujo.
    show_aprueba_financiera_flujo_nuevo = fields.Boolean(
        string='Mostrar aprobación financiera flujo nuevo',
        compute='_compute_show_aprueba_flujo_nuevo',
    )
    show_aprueba_juridico_flujo_nuevo = fields.Boolean(
        string='Mostrar aprobación jurídica flujo nuevo',
        compute='_compute_show_aprueba_flujo_nuevo',
    )

    @api.depends(
        'select_persona',
        'show_aprueba_financiera', 'show_aprueba_financiera_j',
        'show_aprueba_juridico',
        'aprueba_j_h_policia_judicial_', 'aprueba_j_h_inhabilidades_',
        'aprueba_j_h_exp_laboral_', 'aprueba_j_h_para_fiscales',
        'aprueba_j_h_seguridad_social_', 'aprueba_j_h_c_comercio_',
        'aprueba_j_h_anexo_tributario_j', 'aprueba_j_h_propuesta_',
        'aprueba_j_h_rup_', 'aprueba_j_h_hjv_juridica_',
        'aprueba_j_h_c_contraloriaa_', 'aprueba_j_h_cprocuraduria_',
        'aprueba_j_h_cedula_r_', 'aprueba_j_h_hjv_r',
        'aprueba_j_h_contraloria_rep_lega',
        'aprueba_j_h_procuraduria_rep_legal',
    )
    def _compute_show_aprueba_flujo_nuevo(self):
        """
        Esta función actúa como un 'router' de visibilidad.
        Su propósito es decidir qué paneles de aprobación mostrar al usuario 
        dependiendo de si el contratista es Persona Jurídica o Natural.
        """
        for record in self:
            # Identifica si el tipo de contratista es 'persona_juridica'.
            es_juridica = record.select_persona == 'persona_juridica'
            
            # Si es jurídica, asigna el valor de los campos terminados en '_j'.
            # Si es natural, asigna el valor de los campos estándar.
            # Esto permite usar los mismos campos 'flujo_nuevo' en la vista XML 
            # sin importar si el contratista es natural o jurídico.
            record.show_aprueba_financiera_flujo_nuevo = (
                record.show_aprueba_financiera_j if es_juridica else record.show_aprueba_financiera
            )
            
            if es_juridica:
                # El contrato se genera y carga en Juridico 2; Juridico 1
                # valida aqui solamente los soportes iniciales.
                record.show_aprueba_juridico_flujo_nuevo = all([
                    record.aprueba_j_h_policia_judicial_,
                    record.aprueba_j_h_inhabilidades_,
                    record.aprueba_j_h_exp_laboral_,
                    record.aprueba_j_h_para_fiscales,
                    record.aprueba_j_h_seguridad_social_,
                    record.aprueba_j_h_c_comercio_,
                    record.aprueba_j_h_anexo_tributario_j,
                    record.aprueba_j_h_propuesta_,
                    record.aprueba_j_h_rup_,
                    record.aprueba_j_h_hjv_juridica_,
                    record.aprueba_j_h_c_contraloriaa_,
                    record.aprueba_j_h_cprocuraduria_,
                    record.aprueba_j_h_cedula_r_,
                    record.aprueba_j_h_hjv_r,
                    record.aprueba_j_h_contraloria_rep_lega,
                    record.aprueba_j_h_procuraduria_rep_legal,
                ])
            else:
                record.show_aprueba_juridico_flujo_nuevo = record.show_aprueba_juridico
            
            
    def _debe_mover_a_juridico_1_por_asignacion(self):
        self.ensure_one()
        return (
            self.usa_flujo_contratacion_nuevo
            and self.estado_contratacion == EstadoContratacion.DIRECTOR_JURIDICO.value
            and self.usuarios_juridico_ids
        )

    def _estado_juridico_destino_asignacion(self):
        self.ensure_one()
        if self._debe_mover_a_juridico_1_por_asignacion():
            return EstadoContratacion.JURIDICO_1.value
        return EstadoContratacion.JURIDICO.value

    def _enviar_a_estado_juridico_asignado(self, valores_extra=None):
        valores_extra = valores_extra or {}
        for record in self:
            vals = {
                'estado_contratacion': record._estado_juridico_destino_asignacion(),
            }
            vals.update(valores_extra)
            record.write(vals)

    def _juridicos_asignados_flujo_nuevo(self):
        grupo_juridico = self.env.ref('Matriz.group_contratacion_juridico')
        return self.usuarios_juridico_ids.filtered(
            lambda user: grupo_juridico in user.groups_id
        )

    def _validar_juridico_asignado_flujo_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo:
            return
        if not self.env.user.has_group('Matriz.group_contratacion_juridico'):
            raise UserError("Solo el equipo Jurídico puede ejecutar esta acción.")
        if self.env.user not in self._juridicos_asignados_flujo_nuevo():
            raise UserError(
                "Solo el jurídico asignado por el Director Jurídico puede "
                "ejecutar esta acción en el flujo nuevo."
            )

    def _validar_contrato_sin_firmar_flujo_nuevo(self):
        self.ensure_one()
        if self.select_persona == 'persona_juridica':
            documento = self.aprueba_j_h_contrato_doc_id
            documento_visible = self.aprueba_j_h_contrato_doc_visible
        else:
            documento = self.contratos_id
            documento_visible = self.contratos_doc_visible
        if not documento or documento_visible:
            raise UserError(
                "Jurídico debe cargar el contrato sin firmar antes de enviarlo "
                "a Coordinación para firmas."
            )

    def _validar_id_contrato_juridico_flujo_viejo(self):
        self.ensure_one()
        if self.usa_flujo_contratacion_nuevo:
            return
        if not self.env.user.has_group('Matriz.group_contratacion_juridico'):
            return
        if not (self.id_del_contrato or '').strip():
            raise UserError("El campo 'ID del contrato' es obligatorio para aprobar desde JurÃ­dico.")

    def action_upload_aprueba_j_h(self):
        if self.usa_flujo_contratacion_nuevo:
            estado_permitido = self.estado_contratacion == EstadoContratacion.JURIDICO_2.value
            juridica_revision_contrato = (
                self.select_persona == 'persona_juridica'
                and self.estado_contratacion == EstadoContratacion.JURIDICO_1.value
            )
            if not (estado_permitido or juridica_revision_contrato):
                raise UserError(
                    "El contrato solo puede cargarse durante Jurídico 2 "
                    "o Jurídico - Revisión y contrato para persona jurídica."
                )
            self._validar_juridico_asignado_flujo_nuevo()
        return super().action_upload_aprueba_j_h()

    def action_upload_contratos(self):
        if self.usa_flujo_contratacion_nuevo:
            if self.estado_contratacion != EstadoContratacion.JURIDICO_2.value:
                raise UserError("El contrato solo puede cargarse durante Jurídico 2.")
            self._validar_juridico_asignado_flujo_nuevo()
        return super().action_upload_contratos()

    @api.onchange('usuarios_juridico_ids')
    def _onchange_users_with_access(self):
        return super()._onchange_users_with_access()

    

    # cada vez que cambien las fechas de creación.
    @api.depends('create_date', 'fch_creacion')
    def _compute_usa_flujo_contratacion_nuevo(self):
        for record in self:
            # Intentamos obtener la fecha de creación desde el sistema (create_date)
            # o desde nuestro campo personalizado (fch_creacion).
            fecha_base = record.create_date or record.fch_creacion
            
            # Si por alguna razón no hay fecha (un registro vacío),
            # tomamos el día de hoy como fecha de referencia por seguridad.
            if not fecha_base:
                fecha_base = fields.Date.context_today(record)

            # Odoo a veces recibe fechas como texto (string).
            # Si es texto, lo convertimos a formato fecha completo (datetime).
            if isinstance(fecha_base, str):
                fecha_base = fields.Datetime.to_datetime(fecha_base)
            
            # Si el objeto es de tipo 'datetime' (fecha y hora), 
            # solo necesitamos la fecha (año, mes, día), así que extraemos el .date().
            # Esto evita errores al comparar horas o minutos.
            if hasattr(fecha_base, 'date'):
                fecha_base = fecha_base.date()

            # Finalmente, la comparación lógica:
            # Si la fecha del registro es igual o mayor a la fecha de corte definida,
            # activamos el flujo nuevo (True). Si no, el valor será False.
            fecha_inicio_nuevo_flujo = obtener_fecha_inicio_nuevo_flujo_contratacion(record.env)
            usa_flujo_nuevo = fecha_base >= fecha_inicio_nuevo_flujo
            record.usa_flujo_contratacion_nuevo = usa_flujo_nuevo
            record.flujo_contratacion_version = 'nuevo' if usa_flujo_nuevo else 'anterior'
            

    def aprueba_coordinador(self, documentos=None):
        if self.usa_flujo_contratacion_nuevo:
            return self.aprueba_coordinador_enviar_ventanilla(documentos=documentos)
        self._asegurar_estado_coordinador_para_aprobar()

        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})


        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.proyecto_relacionado.users_with_access | self.proyecto_relacionado.users_with_access_financiero)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        users_with_financial_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id
            )
        )

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios financieros relacionados con el proyecto
        if users_with_financial_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, financiero, el coordinador ha aprobado el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for financiero in users_with_financial_role:
                try:
                    if not financiero.email:
                        raise UserError(f"El usuario financiero {financiero.name} no tiene correo configurado.")

                    # Construir la URL con la base de Odoo
                    formulario_url = self._formulario_url_contratacion(incluir_action=False)

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, financiero:</p>
                        <p>El coordinador ha aprobado el documento del contratista <strong>{self.new_name}</strong>. Por favor, revisarlo.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones}</p>' if observaciones else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Aprobaci&oacute;n de documento")
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Aprobación de documento de {self.new_name}",
                        'email_from': coordinador.email,
                        'email_to': financiero.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.sudo().send(raise_exception=True)
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            vals_estado = {'estado_contratacion': 'financiera'}
            vals_estado.update(self._vals_limpiar_observaciones_flujo_viejo('observaciones'))
            self.write(vals_estado)

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios con el rol financiero asociados al proyecto.")

    def _asegurar_estado_coordinador_para_aprobar(self):
        self.ensure_one()
        if self.estado_contratacion != EstadoContratacion.COORDINADOR.value:
            raise UserError("Esta acción solo puede ejecutarse desde el estado Coordinador.")

    def aprueba_coordinador_enviar_ventanilla(self, documentos=None):
        self.ensure_one()
        self._asegurar_estado_coordinador_para_aprobar()
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != EstadoContratacion.COORDINADOR.value:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == EstadoContratacion.COORDINADOR.value:
            self.write({'aprobacion_coordinador_realizada': True})

        if self.estado_retorno_devolucion:
            return self.coordinador_retorna_ajustes_flujo_nuevo()

        usuarios_combinados = (
            self.proyecto_relacionado.users_with_access |
            self.proyecto_relacionado.users_with_access_financiero
        )

        grupo_ventanilla = self.env.ref('Matriz.group_contratacion_ventanilla_unica')
        users_with_ventanilla_role = usuarios_combinados.filtered(
            lambda user: grupo_ventanilla in user.groups_id
        )

        coordinador = self.env.user
        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones)
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        COLOR_PRINCIPAL = "#043484"

        if users_with_ventanilla_role:
            for ventanilla in users_with_ventanilla_role:
                try:
                    formulario_url = self._formulario_url_contratacion()

                    body_html = f"""
                        <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                            <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                                <h3 style="margin: 0; font-size: 18px;">Aprobación de contratación: <strong>{nombre_proyecto}</strong></h3>
                            </div>

                            <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                                <p>Hola, <strong>{ventanilla.name}</strong>:</p>

                                <p>El coordinador <strong>{coordinador.name}</strong> ha aprobado el documento del contratista y lo ha enviado a Ventanilla para revisión.</p>

                                <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                                    <strong>Contratista:</strong> {self.new_name}<br/>
                                    <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                                    <strong>Estado actual:</strong> Ventanilla
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
                                        Ir al formulario de contratación
                                    </a>
                                </p>

                                <p style="margin-top: 20px; font-size: 12px; color: #999;">
                                    SGO - mensaje de notificación
                                </p>
                            </div>
                        </div>
                    """
                    mail_values = {
                        'subject': f"Aprobación de contratación para: {self.new_name} del proyecto; {nombre_proyecto}",
                        'email_from': coordinador.email,
                        'email_to': ventanilla.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {ventanilla.name}: {str(e)}")

            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            vals_estado = {'estado_contratacion': EstadoContratacion.VENTANILLA.value}
            vals_estado.update(self._vals_limpiar_observacion_estado_flujo_nuevo(
                EstadoContratacion.COORDINADOR.value
            ))
            self.write(vals_estado)

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return self._notificacion_flujo_nuevo(
                'Mensaje enviado',
                'El mensaje ha sido enviado correctamente a Ventanilla.',
            )
        else:
            raise UserError("No hay usuarios con el rol de ventanilla asociados al proyecto.")

    def coordinador_retorna_ajustes_flujo_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.COORDINADOR.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Coordinador.")
        estado_destino = self.estado_retorno_devolucion
        if not estado_destino:
            raise UserError("No hay un estado de retorno pendiente.")

        destinatarios = self._destinatarios_estado_flujo_nuevo(estado_destino)
        if not destinatarios:
            raise UserError(
                f"No hay usuarios asociados para retornar la contratación a {self._nombre_estado_flujo_nuevo(estado_destino)}."
            )

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones)
        nombre_estado = self._nombre_estado_flujo_nuevo(estado_destino)
        usuario_devolucion = self.ultimo_usuario_devolucion_id.name if self.ultimo_usuario_devolucion_id else 'el área solicitante'
        mensaje = (
            f"Coordinación <strong>{self.env.user.name}</strong> aplicó los ajustes solicitados por "
            f"<strong>{usuario_devolucion}</strong> y retorna la contratación del contratista "
            f"<strong>{self.new_name}</strong> al estado <strong>{nombre_estado}</strong>."
        )
        self._enviar_correo_flujo_nuevo(
            destinatarios,
            'Ajustes de Coordinación',
            nombre_estado,
            mensaje,
            observaciones,
            'Continuar revisión',
        )
        vals_retorno = {
            'estado_contratacion': estado_destino,
            'estado_retorno_devolucion': False,
        }
        vals_retorno.update(self._vals_limpiar_observacion_estado_flujo_nuevo(
            EstadoContratacion.COORDINADOR.value
        ))
        self.write(vals_retorno)
        return self._notificacion_flujo_nuevo(
            'Ajustes enviados',
            f'La contratación retornó a {nombre_estado}.',
        )

    def _nombre_proyecto_contratacion(self):
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]
        return ', '.join(nombres_proyecto_filtrados)

    def _formulario_url_contratacion(self, incluir_action=True):
        action = self.env.ref('Matriz.action_open_contrataciones_fuv_form_direct', raise_if_not_found=False)
        if not action:
            action = self.env.ref('Matriz.action_open_contrataciones_fuv', raise_if_not_found=False)
        params = [
            f'id={self.id}',
            'model=contrataciones_fuv',
            'view_type=form',
            'cids=1',
        ]
        if incluir_action and action:
            params.append(f'action={action.id}')
        formulario_url = '/web#' + '&'.join(params)
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

    def _usuarios_proyecto_por_grupo(self, grupo_xml_id, usuarios_extra=None):
        grupo = self.env.ref(grupo_xml_id)
        usuarios_base = self.proyecto_relacionado.users_with_access
        if usuarios_extra:
            usuarios_base |= usuarios_extra
        return usuarios_base.filtered(lambda user: grupo in user.groups_id)

    def _nombre_estado_flujo_nuevo(self, estado):
        etiquetas = dict(ESTADOS_CONTRATACION_NUEVOS + [
            (EstadoContratacion.COORDINADOR.value, 'Coordinador'),
            (EstadoContratacion.FINANCIERA.value, 'Financiera'),
            (EstadoContratacion.JURIDICO.value, 'Jurídico'),
            (EstadoContratacion.RECURSOS_HUMANOS.value, 'Recursos humanos'),
            (EstadoContratacion.CONTRATADO.value, 'Contratado'),
        ])
        return etiquetas.get(estado, estado or '')

    def _observaciones_estado_flujo_nuevo(self, estado):
        if estado == EstadoContratacion.VENTANILLA.value:
            return self._texto_observacion_flujo_nuevo(self.observaciones_ventanilla)
        if estado == EstadoContratacion.PRESUPUESTO.value:
            return self._texto_observacion_flujo_nuevo(self.observaciones_presupuesto)
        if estado in [
            EstadoContratacion.DIRECTOR_JURIDICO.value,
            EstadoContratacion.JURIDICO_1.value,
            EstadoContratacion.JURIDICO_2.value,
        ]:
            return self._texto_observacion_flujo_nuevo(self.observaciones_juri)
        if estado == EstadoContratacion.RECURSOS_HUMANOS.value:
            return self._texto_observacion_flujo_nuevo(self.observaciones_recursos)
        return self._texto_observacion_flujo_nuevo(self.observaciones)

    def _campo_observacion_estado_flujo_nuevo(self, estado):
        if estado == EstadoContratacion.VENTANILLA.value:
            return 'observaciones_ventanilla'
        if estado == EstadoContratacion.PRESUPUESTO.value:
            return 'observaciones_presupuesto'
        if estado in [
            EstadoContratacion.DIRECTOR_JURIDICO.value,
            EstadoContratacion.JURIDICO_1.value,
            EstadoContratacion.JURIDICO_2.value,
        ]:
            return 'observaciones_juri'
        if estado == EstadoContratacion.RECURSOS_HUMANOS.value:
            return 'observaciones_recursos'
        if estado in [
            EstadoContratacion.COORDINADOR.value,
            EstadoContratacion.FIRMA.value,
        ]:
            return 'observaciones'
        return False

    def _vals_limpiar_observacion_estado_flujo_nuevo(self, estado):
        campo = self._campo_observacion_estado_flujo_nuevo(estado)
        return {campo: False} if campo else {}

    def _vals_limpiar_observaciones_flujo_viejo(self, *campos):
        return {campo: False for campo in campos if campo in self._fields}

    def _limpiar_observaciones_sin_tracking(self, *campos):
        vals = self._vals_limpiar_observaciones_flujo_viejo(*campos)
        if vals:
            self.with_context(tracking_disable=True).write(vals)

    def _texto_observacion_flujo_nuevo(self, observacion):
        return (html2plaintext(observacion or '') or '').strip()

    def _destinatarios_estado_flujo_nuevo(self, estado):
        if estado == EstadoContratacion.VENTANILLA.value:
            return self._usuarios_proyecto_por_grupo(
                'Matriz.group_contratacion_ventanilla_unica',
                usuarios_extra=self.proyecto_relacionado.users_with_access_financiero,
            )
        if estado == EstadoContratacion.PRESUPUESTO.value:
            return self._usuarios_proyecto_por_grupo(
                'Matriz.group_presupuestos',
                usuarios_extra=self.proyecto_relacionado.users_with_access_financiero,
            )
        if estado == EstadoContratacion.DIRECTOR_JURIDICO.value:
            return self._usuarios_proyecto_por_grupo(
                'Matriz.group_contratacion_director_juridico',
                usuarios_extra=self.usuarios_juridico_ids,
            )
        if estado in [EstadoContratacion.JURIDICO_1.value, EstadoContratacion.JURIDICO_2.value]:
            return self._juridicos_asignados_flujo_nuevo()
        if estado == EstadoContratacion.RECURSOS_HUMANOS.value:
            return self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_recursos')
        if estado in [EstadoContratacion.COORDINADOR.value, EstadoContratacion.FIRMA.value]:
            return (
                self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_coordinador') |
                self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_asis_coordinador')
            )
        return self.env['res.users']

    def _validar_aprobaciones_requeridas_flujo_nuevo(self, area):
        self.ensure_one()
        if area == 'financiera':
            aprobado = self.show_aprueba_financiera_j if self.select_persona == 'persona_juridica' else self.show_aprueba_financiera
            if not aprobado:
                raise UserError("Debes revisar y aprobar todos los requisitos financieros antes de continuar.")
        elif area == 'juridico':
            aprobado = self.show_aprueba_juridico_flujo_nuevo
            if not aprobado:
                raise UserError("Debes revisar y aprobar todos los documentos jurídicos requeridos antes de continuar.")
        elif area == 'recursos_humanos':
            if not self.show_aprueba_recursos_humanos:
                raise UserError("Debes revisar y aprobar todos los documentos requeridos por Recursos Humanos antes de continuar.")

    def _enviar_correo_flujo_nuevo(self, destinatarios, titulo, saludo_area, mensaje, observaciones, boton_texto):
        es_devolucion = (titulo or '').strip().lower().startswith('devolución')
        COLOR_PRINCIPAL = "#f28c28" if es_devolucion else "#043484"
        COLOR_OBSERVACION_BORDE = "#f28c28" if es_devolucion else "#ffcc00"
        COLOR_OBSERVACION_FONDO = "#fff3e0" if es_devolucion else "#fffacd"
        COLOR_OBSERVACION_TEXTO = "#8a4b08" if es_devolucion else "#856404"
        observaciones = self._texto_observacion_flujo_nuevo(observaciones)
        nombre_proyecto = self._nombre_proyecto_contratacion()
        formulario_url = self._formulario_url_contratacion()
        remitente = self.env.user
        destinatarios_notificados = self.env['res.users']

        for destinatario in destinatarios:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">{titulo}: <strong>{nombre_proyecto}</strong></h3>
                    </div>

                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                        <p>Hola, <strong>{destinatario.name}</strong>:</p>

                        <p>{mensaje}</p>

                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                            <strong>Contratista:</strong> {self.new_name}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                            <strong>Área destino:</strong> {saludo_area}
                        </div>

                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid {COLOR_OBSERVACION_BORDE}; background-color: {COLOR_OBSERVACION_FONDO}; border-radius: 4px;">
                            <strong>Observaciones:</strong><br/>
                            <em style="color: {COLOR_OBSERVACION_TEXTO};">{observaciones}</em>
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
                                {boton_texto}
                            </a>
                        </p>

                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            SGO - mensaje de notificación
                        </p>
                    </div>
                </div>
            """
            mail_values = {
                'subject': f"{titulo} - {self.new_name} - {nombre_proyecto}",
                'email_from': remitente.email,
                'email_to': destinatario.email,
                'body_html': body_html,
                'auto_delete': True,
            }
            self.env['mail.mail'].sudo().create(mail_values).send()
            destinatarios_notificados |= destinatario

        if destinatarios_notificados:
            nombres_destinatarios = ', '.join(destinatarios_notificados.mapped('name'))
            self.message_post(
                body=(
                    f"<p><b>{titulo}</b></p>"
                    f"<p>{mensaje}</p>"
                    f"<p><b>Destinatarios:</b> {nombres_destinatarios}</p>"
                    f"{f'<p><b>Observaciones:</b> {observaciones}</p>' if observaciones else ''}"
                ),
                subtype_xmlid='mail.mt_note',
            )

    def _envolver_correo_flujo_viejo(self, body_html, titulo="Notificaci&oacute;n de contrataci&oacute;n"):
        COLOR_PRINCIPAL = "#043484"
        contenido = (body_html or "").strip()
        contenido = contenido.replace(
            "<p><a href='",
            (
                '<p style="text-align: center; margin-top: 25px;">'
                '<a style="background-color: #043484; color: white; padding: 10px 20px; '
                'text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;" href=\''
            ),
        )
        return f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                    <h3 style="margin: 0; font-size: 18px;">{titulo}</h3>
                </div>

                <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                    {contenido}

                    <p style="margin-top: 20px; font-size: 12px; color: #999;">
                        SGO - mensaje de notificaci&oacute;n
                    </p>
                </div>
            </div>
        """

    def _registrar_devolucion_flujo_nuevo(self, estado_origen, estado_destino):
        vals = {
            'ultimo_estado_devolucion': estado_origen,
            'ultimo_usuario_devolucion_id': self.env.user.id,
            'estado_retorno_devolucion': estado_origen,
            'estado_contratacion': estado_destino,
        }
        vals.update(self._vals_limpiar_observacion_estado_flujo_nuevo(estado_origen))
        self.write(vals)

    def _notificacion_flujo_nuevo(self, titulo, mensaje, tipo='success'):
        self.ensure_one()
        params = {
            'title': titulo,
            'sticky': False,
            'message': mensaje,
            'type': tipo,
            'message_sticky': False,
            'message_duration': 9,
        }
        # El refresco visual del estado se hace desde JS sobre el registro
        # abierto. No usamos tag reload porque recarga la pagina y afecta la
        # navegacion/miga de pan del usuario.
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': params,
        }

    def aprueba_ventanilla_enviar_presupuesto(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.VENTANILLA.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Ventanilla.")
        self._validar_aprobaciones_requeridas_flujo_nuevo('financiera')

        usuarios_presupuesto = self._usuarios_proyecto_por_grupo(
            'Matriz.group_presupuestos',
            usuarios_extra=self.proyecto_relacionado.users_with_access_financiero,
        )
        if not usuarios_presupuesto:
            raise UserError("No hay usuarios con el rol de presupuesto asociados al proyecto.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_ventanilla)
        mensaje = (
            f"Ventanilla <strong>{self.env.user.name}</strong> ha aprobado la revisión inicial "
            f"del contratista <strong>{self.new_name}</strong> y lo envía a Presupuesto."
        )
        self._enviar_correo_flujo_nuevo(
            usuarios_presupuesto,
            'Aprobación de Ventanilla',
            'Presupuesto',
            mensaje,
            observaciones,
            'Ir al formulario de contratación',
        )
        vals_estado = {'estado_contratacion': EstadoContratacion.PRESUPUESTO.value}
        vals_estado.update(self._vals_limpiar_observacion_estado_flujo_nuevo(
            EstadoContratacion.VENTANILLA.value
        ))
        self.write(vals_estado)
        return self._notificacion_flujo_nuevo(
            'Mensaje enviado',
            'El mensaje ha sido enviado correctamente a Presupuesto.',
        )

    def ventanilla_devuelve_coordinador(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.VENTANILLA.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Ventanilla.")

        coordinadores = self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_coordinador')
        if not coordinadores:
            raise UserError("No hay coordinadores asociados al proyecto para recibir la devolución.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_ventanilla)
        mensaje = (
            f"Ventanilla <strong>{self.env.user.name}</strong> devolvió la contratación "
            f"del contratista <strong>{self.new_name}</strong> al coordinador."
        )
        self._enviar_correo_flujo_nuevo(
            coordinadores,
            'Devolución de Ventanilla',
            'Coordinación',
            mensaje,
            observaciones,
            'Revisar devolución',
        )
        self._registrar_devolucion_flujo_nuevo(
            EstadoContratacion.VENTANILLA.value,
            EstadoContratacion.COORDINADOR.value,
        )
        return self._notificacion_flujo_nuevo(
            'Devolución enviada',
            'La contratación fue devuelta al coordinador.',
            'warning',
        )

    def devolver_coordinador_flujo_nuevo(self):
        self.ensure_one()
        estados_grupos_permitidos = {
            EstadoContratacion.PRESUPUESTO.value: ['Matriz.group_presupuestos'],
            EstadoContratacion.DIRECTOR_JURIDICO.value: ['Matriz.group_contratacion_director_juridico'],
            EstadoContratacion.JURIDICO_1.value: ['Matriz.group_contratacion_juridico'],
            EstadoContratacion.RECURSOS_HUMANOS.value: ['Matriz.group_contratacion_recursos'],
            EstadoContratacion.JURIDICO_2.value: ['Matriz.group_contratacion_juridico'],
        }
        estados_permitidos = list(estados_grupos_permitidos)
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion not in estados_permitidos:
            raise UserError("Esta acción solo aplica para devolver al coordinador desde el flujo nuevo.")
        grupos_estado = estados_grupos_permitidos[self.estado_contratacion]
        if not any(self.env.user.has_group(grupo) for grupo in grupos_estado):
            raise UserError("No puedes devolver desde este estado porque ya no corresponde a tu rol en el flujo.")
        if self.estado_contratacion in (
            EstadoContratacion.JURIDICO_1.value,
            EstadoContratacion.JURIDICO_2.value,
        ):
            self._validar_juridico_asignado_flujo_nuevo()

        coordinadores = (
            self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_coordinador') |
            self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_asis_coordinador')
        )
        if not coordinadores:
            raise UserError("No hay coordinadores o asistentes asociados al proyecto para recibir la devolución.")

        estado_origen = self.estado_contratacion
        nombre_estado = self._nombre_estado_flujo_nuevo(estado_origen)
        remitente_estado = (
            f'El área de Dirección Jurídica, <strong>{self.env.user.name}</strong>,'
            if estado_origen == EstadoContratacion.DIRECTOR_JURIDICO.value
            else f'{nombre_estado} <strong>{self.env.user.name}</strong>'
        )
        asunto_estado = (
            'Dirección Jurídica'
            if estado_origen == EstadoContratacion.DIRECTOR_JURIDICO.value
            else nombre_estado
        )
        observaciones = self._observaciones_estado_flujo_nuevo(estado_origen)
        mensaje = (
            f"{remitente_estado} devolvió la contratación "
            f"del contratista <strong>{self.new_name}</strong> a Coordinación para ajustes."
        )
        self._enviar_correo_flujo_nuevo(
            coordinadores,
            f'Devolución de {asunto_estado}',
            'Coordinación',
            mensaje,
            observaciones,
            'Revisar devolución',
        )
        self._registrar_devolucion_flujo_nuevo(
            estado_origen,
            EstadoContratacion.COORDINADOR.value,
        )
        return self._notificacion_flujo_nuevo(
            'Devolución enviada',
            'La contratación fue devuelta a Coordinación.',
            'warning',
        )

    def presupuesto_devuelve_ventanilla(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.PRESUPUESTO.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Presupuesto.")

        usuarios_ventanilla = self._usuarios_proyecto_por_grupo(
            'Matriz.group_contratacion_ventanilla_unica',
            usuarios_extra=self.proyecto_relacionado.users_with_access_financiero,
        )
        if not usuarios_ventanilla:
            raise UserError("No hay usuarios de Ventanilla asociados al proyecto para recibir la devolución.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_presupuesto)
        mensaje = (
            f"Presupuesto <strong>{self.env.user.name}</strong> devolvió la contratación "
            f"del contratista <strong>{self.new_name}</strong> a Ventanilla."
        )
        self._enviar_correo_flujo_nuevo(
            usuarios_ventanilla,
            'Devolución de Presupuesto',
            'Ventanilla',
            mensaje,
            observaciones,
            'Revisar devolución',
        )
        self._registrar_devolucion_flujo_nuevo(
            EstadoContratacion.PRESUPUESTO.value,
            EstadoContratacion.VENTANILLA.value,
        )
        return self._notificacion_flujo_nuevo(
            'Devolución enviada',
            'La contratación fue devuelta a Ventanilla.',
            'warning',
        )

    def aprueba_presupuesto_enviar_director_juridico(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.PRESUPUESTO.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Presupuesto.")
        if not self.compromiso_presupuestal:
            raise UserError("El campo RPC es obligatorio para Presupuesto antes de enviar al Director Jurídico.")
        self._validar_aprobaciones_requeridas_flujo_nuevo('financiera')

        directores_juridicos = self._usuarios_proyecto_por_grupo(
            'Matriz.group_contratacion_director_juridico',
            usuarios_extra=self.usuarios_juridico_ids,
        )
        if not directores_juridicos:
            raise UserError("No hay usuarios con el rol de director jurídico asociados al proyecto.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_presupuesto)
        mensaje = (
            f"Presupuesto <strong>{self.env.user.name}</strong> ha aprobado la revisión "
            f"del contratista <strong>{self.new_name}</strong> y lo envía al Director Jurídico."
        )
        self._enviar_correo_flujo_nuevo(
            directores_juridicos,
            'Aprobación de Presupuesto',
            'Director Jurídico',
            mensaje,
            observaciones,
            'Ir al formulario de contratación',
        )
        vals_estado = {'estado_contratacion': EstadoContratacion.DIRECTOR_JURIDICO.value}
        vals_estado.update(self._vals_limpiar_observacion_estado_flujo_nuevo(
            EstadoContratacion.PRESUPUESTO.value
        ))
        self.write(vals_estado)
        return self._notificacion_flujo_nuevo(
            'Mensaje enviado',
            'El mensaje ha sido enviado correctamente al Director Jurídico.',
        )

    def director_juridico_devuelve_presupuesto(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.DIRECTOR_JURIDICO.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Director Jurídico.")

        usuarios_presupuesto = self._usuarios_proyecto_por_grupo(
            'Matriz.group_presupuestos',
            usuarios_extra=self.proyecto_relacionado.users_with_access_financiero,
        )
        if not usuarios_presupuesto:
            raise UserError("No hay usuarios de Presupuesto asociados al proyecto para recibir la devolución.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_juri)
        mensaje = (
            f"El área de Dirección Jurídica, <strong>{self.env.user.name}</strong>, "
            f"devolvió la contratación del contratista "
            f"<strong>{self.new_name}</strong> a Presupuesto."
        )
        self._enviar_correo_flujo_nuevo(
            usuarios_presupuesto,
            'Devolución de Dirección Jurídica',
            'Presupuesto',
            mensaje,
            observaciones,
            'Revisar devolución',
        )
        self._registrar_devolucion_flujo_nuevo(
            EstadoContratacion.DIRECTOR_JURIDICO.value,
            EstadoContratacion.PRESUPUESTO.value,
        )
        return self._notificacion_flujo_nuevo(
            'Devolución enviada',
            'La contratación fue devuelta a Presupuesto.',
            'warning',
        )

    def juridico_1_devuelve_presupuesto(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.JURIDICO_1.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Jurídico.")
        self._validar_juridico_asignado_flujo_nuevo()

        usuarios_presupuesto = self._usuarios_proyecto_por_grupo(
            'Matriz.group_presupuestos',
            usuarios_extra=self.proyecto_relacionado.users_with_access_financiero,
        )
        if not usuarios_presupuesto:
            raise UserError("No hay usuarios de Presupuesto asociados al proyecto para recibir la devolución.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_juri)
        mensaje = (
            f"Jurídico <strong>{self.env.user.name}</strong> devolvió la contratación "
            f"del contratista <strong>{self.new_name}</strong> a Presupuesto."
        )
        self._enviar_correo_flujo_nuevo(
            usuarios_presupuesto,
            'Devolución de Jurídico',
            'Presupuesto',
            mensaje,
            observaciones,
            'Revisar devolución',
        )
        self._registrar_devolucion_flujo_nuevo(
            EstadoContratacion.JURIDICO_1.value,
            EstadoContratacion.PRESUPUESTO.value,
        )
        return self._notificacion_flujo_nuevo(
            'Devolución enviada',
            'La contratación fue devuelta a Presupuesto.',
            'warning',
        )

    def aprueba_juridico_1_flujo_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.JURIDICO_1.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Jurídico.")
        self._validar_juridico_asignado_flujo_nuevo()
        self._validar_aprobaciones_requeridas_flujo_nuevo('juridico')

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_juri)
        if self.select_persona == 'persona_juridica':
            self._validar_contrato_sin_firmar_flujo_nuevo()
            return self._enviar_firma_desde_juridico_flujo_nuevo(
                EstadoContratacion.JURIDICO_1.value,
                observaciones,
            )

        recursos_humanos = self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_recursos')
        if not recursos_humanos:
            raise UserError("No hay usuarios de Recursos Humanos asociados al proyecto.")

        mensaje = (
            f"Jurídico <strong>{self.env.user.name}</strong> aprobó la contratación "
            f"del contratista <strong>{self.new_name}</strong> y la envía a Recursos Humanos."
        )
        self._enviar_correo_flujo_nuevo(
            recursos_humanos,
            'Aprobación de Jurídico',
            'Recursos Humanos',
            mensaje,
            observaciones,
            'Revisar contratación',
        )
        vals_estado = {
            'aprobacion_juridico_realizada': True,
            'estado_contratacion': EstadoContratacion.RECURSOS_HUMANOS.value,
        }
        vals_estado.update(self._vals_limpiar_observacion_estado_flujo_nuevo(
            EstadoContratacion.JURIDICO_1.value
        ))
        self.write(vals_estado)
        return self._notificacion_flujo_nuevo(
            'Mensaje enviado',
            'La contratacion avanzo a Recursos Humanos.',
        )

    def aprueba_recursos_humanos_flujo_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.RECURSOS_HUMANOS.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Recursos Humanos.")
        if self.select_persona == 'persona_juridica':
            raise UserError("La persona jurídica no pasa por Recursos Humanos en el flujo nuevo.")
        self._validar_aprobaciones_requeridas_flujo_nuevo('recursos_humanos')

        juridicos = self._juridicos_asignados_flujo_nuevo()
        if not juridicos:
            raise UserError("No hay usuarios jurídicos asignados para recibir la aprobación de Recursos Humanos.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_recursos)
        mensaje = (
            f"Recursos Humanos <strong>{self.env.user.name}</strong> aprobó la revisión "
            f"del contratista <strong>{self.new_name}</strong>. El flujo continúa en Jurídico."
        )
        self._enviar_correo_flujo_nuevo(
            juridicos,
            'Aprobación de Recursos Humanos',
            'Jurídico',
            mensaje,
            observaciones,
            'Continuar revisión',
        )
        vals_estado = {
            'aprobacion_recursos_humanos_realizada': True,
            'generar_informe_visible': True,
            'estado_contratacion': EstadoContratacion.JURIDICO_2.value,
            'estado_contratacion_2': 'juridico_2',
        }
        vals_estado.update(self._vals_limpiar_observacion_estado_flujo_nuevo(
            EstadoContratacion.RECURSOS_HUMANOS.value
        ))
        self.write(vals_estado)
        return self._notificacion_flujo_nuevo(
            'Mensaje enviado',
            'La contratacion avanzo a Juridico.',
        )

    def aprueba_juridico_2_enviar_firma(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.JURIDICO_2.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Jurídico.")
        self._validar_juridico_asignado_flujo_nuevo()
        self._validar_aprobaciones_requeridas_flujo_nuevo('juridico')
        self._validar_contrato_sin_firmar_flujo_nuevo()

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_juri)
        return self._enviar_firma_desde_juridico_flujo_nuevo(
            EstadoContratacion.JURIDICO_2.value,
            observaciones,
        )

    def _enviar_firma_desde_juridico_flujo_nuevo(self, estado_limpieza, observaciones):
        self.ensure_one()
        coordinadores = self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_coordinador')
        asistentes = self._usuarios_proyecto_por_grupo('Matriz.group_contratacion_asis_coordinador')
        destinatarios = coordinadores | asistentes
        if not destinatarios:
            raise UserError("No hay coordinadores o asistentes asociados al proyecto para recibir la solicitud de firma.")

        mensaje = (
            f"Jurídico <strong>{self.env.user.name}</strong> dejó lista la contratación "
            f"del contratista <strong>{self.new_name}</strong> para gestionar firmas y cargar el contrato firmado."
        )
        self._enviar_correo_flujo_nuevo(
            destinatarios,
            'Contrato listo para firma',
            'Coordinación',
            mensaje,
            observaciones,
            'Gestionar firma',
        )
        vals_estado = {
            'aprobacion_juridico_realizada': True,
            'estado_contratacion': EstadoContratacion.FIRMA.value,
        }
        if self.select_persona == 'persona_juridica':
            vals_estado['generar_informe_visible_j'] = True
        else:
            vals_estado['generar_informe_visible'] = True
        vals_estado.update(self._vals_limpiar_observacion_estado_flujo_nuevo(
            estado_limpieza
        ))
        self.write(vals_estado)
        self.message_post(
            body=(
                "<p><b>Jurídico envió la contratación a firma.</b></p>"
                "<p>Coordinación queda habilitada para gestionar firmas y cargar el contrato firmado.</p>"
            )
        )
        return self._notificacion_flujo_nuevo(
            'Enviado a firma',
            'La contratación avanzó al estado Coordinador - Subir Contrato firmado.',
        )

    def _validar_firma_para_contratar_flujo_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo:
            return
        if self.estado_contratacion != EstadoContratacion.FIRMA.value:
            raise UserError(
                "En el flujo nuevo solo se puede contratar desde el estado "
                "Coordinador - Subir Contrato firmado."
            )
        puede_contratar = (
            self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
        )
        if not puede_contratar:
            raise UserError(
                "Solo el Coordinador o el Asistente coordinador pueden contratar "
                "en el flujo nuevo."
            )
        contrato_firmado_cargado = (
            not self.aprueba_j_h_contrato_firmado_doc_visible
            if self.select_persona == 'persona_juridica'
            else not self.contratos_firmado_doc_visible
        )
        if not contrato_firmado_cargado:
            raise UserError("Primero debes cargar el contrato firmado para poder contratar.")

    def _registrar_cargue_contrato_firmado_flujo_nuevo(self):
        for record in self:
            if (
                not record.usa_flujo_contratacion_nuevo
                or record.estado_contratacion != EstadoContratacion.FIRMA.value
            ):
                continue
            record.message_post(
                body=(
                    "<p><b>Contrato firmado cargado por Coordinación.</b></p>"
                    "<p>La contratación permanece en <b>Coordinador - Subir Contrato firmado</b> "
                    "hasta que el Coordinador confirme la acción <b>Contratar</b>.</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )

    def notificacion_contrato_firmado(self):
        resultado = super().notificacion_contrato_firmado()
        self._registrar_cargue_contrato_firmado_flujo_nuevo()
        return resultado

    def notificacion_contrato_juridico_firmado(self):
        resultado = super().notificacion_contrato_juridico_firmado()
        self._registrar_cargue_contrato_firmado_flujo_nuevo()
        return resultado

    def contratado2(self):
        self.ensure_one()
        self._validar_firma_para_contratar_flujo_nuevo()
        resultado = super().contratado2()
        if self.usa_flujo_contratacion_nuevo:
            self.message_post(
                body=(
                    "<p><b>Contrato finalizado por Coordinación.</b></p>"
                    "<p>El flujo nuevo avanzó de <b>Coordinador - Subir Contrato firmado</b> "
                    "a <b>Contratado</b>.</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )
        return resultado

    def contratado(self):
        self.ensure_one()
        self._validar_firma_para_contratar_flujo_nuevo()
        resultado = super().contratado()
        if self.usa_flujo_contratacion_nuevo:
            self.message_post(
                body=(
                    "<p><b>Contrato finalizado por Coordinación.</b></p>"
                    "<p>El flujo nuevo avanzó de <b>Coordinador - Subir Contrato firmado</b> "
                    "a <b>Contratado</b>.</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )
        return resultado

    def requerir_poliza_flujo_nuevo(self):
        self.ensure_one()
        if (
            not self.usa_flujo_contratacion_nuevo
            or self.estado_contratacion != EstadoContratacion.CONTRATADO.value
        ):
            raise UserError(
                "La póliza solo puede requerirse desde el estado Contratado del flujo nuevo."
            )

        es_director_juridico = self.env.user.has_group(
            'Matriz.group_contratacion_director_juridico'
        )
        es_juridico_asignado = (
            self.env.user.has_group('Matriz.group_contratacion_juridico')
            and self.env.user in self.usuarios_juridico_ids
        )
        if not (es_director_juridico or es_juridico_asignado):
            raise UserError(
                "Solo el Director Jurídico o el jurídico asignado pueden requerir la póliza."
            )

        self.write({'estado_contratacion': EstadoContratacion.POLIZAS.value})
        self.message_post(
            body=(
                "<p><b>Póliza requerida.</b></p>"
                f"<p><b>{self.env.user.name}</b> trasladó la contratación "
                "del estado <b>Contratado</b> a <b>Pólizas (Eventual)</b>.</p>"
            ),
            subtype_xmlid='mail.mt_note',
        )
        return self._notificacion_flujo_nuevo(
            'Póliza requerida',
            'La contratación avanzó al estado Pólizas (Eventual).',
        )

    def recursos_humanos_devuelve_juridico_1(self):
        self.ensure_one()
        if not self.usa_flujo_contratacion_nuevo or self.estado_contratacion != EstadoContratacion.RECURSOS_HUMANOS.value:
            raise UserError("Esta acción solo aplica para registros del flujo nuevo en estado Recursos Humanos.")
        if self.select_persona == 'persona_juridica':
            raise UserError("La persona jurídica no pasa por Recursos Humanos en el flujo nuevo.")

        juridicos = self._juridicos_asignados_flujo_nuevo()
        if not juridicos:
            raise UserError("No hay usuarios jurídicos asignados para recibir la devolución de Recursos Humanos.")

        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones_recursos)
        mensaje = (
            f"Recursos Humanos <strong>{self.env.user.name}</strong> devolvió la contratación "
            f"del contratista <strong>{self.new_name}</strong> a Jurídico."
        )
        self._enviar_correo_flujo_nuevo(
            juridicos,
            'Devolución de Recursos Humanos',
            'Jurídico',
            mensaje,
            observaciones,
            'Revisar devolución',
        )
        self._registrar_devolucion_flujo_nuevo(
            EstadoContratacion.RECURSOS_HUMANOS.value,
            EstadoContratacion.JURIDICO_1.value,
        )
        return self._notificacion_flujo_nuevo(
            'Devolución enviada',
            'La contratación fue devuelta a Jurídico.',
            'warning',
        )

    def aprueba_coordinador_juridico(self, documentos=None):
        self._asegurar_estado_coordinador_para_aprobar()
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})


        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.users_with_access | self.usuarios_juridico_ids)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        users_with_juridicos_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_juridico') in user.groups_id
            )
        )

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios Jurídico relacionados con el proyecto
        if users_with_juridicos_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, departamento jurídico. El coordinador corrigió y aprobó el FC del contratista <strong>{self.new_name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for juridicos in users_with_juridicos_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, jurídico:</p>
                        <p>El coordinador corrigió y aprobó el FC del contratista <strong>{self.new_name}</strong>. Por favor, revisarlo.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones}</p>' if observaciones else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Aprobaci&oacute;n de documento")
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Aprobación de documento de {self.new_name}",
                        'email_from': coordinador.email,
                        'email_to': juridicos.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridicos.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            self._enviar_a_estado_juridico_asignado(
                self._vals_limpiar_observaciones_flujo_viejo('observaciones')
            )

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios con el rol juridico asociados al proyecto.")

    def aprueba_coordinador_rrhh(self, documentos=None):
        self._asegurar_estado_coordinador_para_aprobar()
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})


        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.users_with_access)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        users_with_recursos_humanos_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_recursos') in user.groups_id
            )
        )


        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios financieros relacionados con el proyecto
        if users_with_recursos_humanos_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, departamento recursos humanos. El coordinador corrigió y aprobó el FC del contratista <strong>{self.new_name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for recursos_humanos in users_with_recursos_humanos_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, recursos humanos:</p>
                        <p>El coordinador corrigió y aprobó el FC del contratista <strong>{self.new_name}</strong>. Por favor, revisarlo.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones}</p>' if observaciones else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Aprobaci&oacute;n de documento")
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Aprobación de documento de {self.new_name}",
                        'email_from': coordinador.email,
                        'email_to': recursos_humanos.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {recursos_humanos.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            vals_estado = {'estado_contratacion': 'recursos_humanos'}
            vals_estado.update(self._vals_limpiar_observaciones_flujo_viejo('observaciones'))
            self.write(vals_estado)

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios con el rol juridico asociados al proyecto.")

    def aprueba_juridico(self):
        self._validar_id_contrato_juridico_flujo_viejo()
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de juridico ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "juridico"
        if self.estado_contratacion == 'juridico':
            self.write({'aprobacion_juridico_realizada': True})

        # Obtener los usuarios del grupo de recursos humanos desde los campos relacionados
        recursos_humanos = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_recursos') in user.groups_id
        )


        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener el usuario jurídico actual
        juridico = self.env.user

        # Verificar si hay usuarios en el grupo de recursos humanos
        if recursos_humanos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, departamento de recursos humanos, el departamento jurídico ha aprobado la contratación para <strong>{self.new_name}</strong>, por favor revisar"""

            for recursos_humano in recursos_humanos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Enviar correo electrónico al departamento de recursos humanos
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, departamento de recursos humanos:</p>
                        <p>El departamento jurídico ha aprobado la contratación para <strong>{self.new_name}</strong>, por favor revisar</p>
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Aprobaci&oacute;n de contrataci&oacute;n")

                    mail_values = {
                        'subject': f"{nombre_proyecto} - Aprobación de contratación de {self.new_name}",
                        'email_from': juridico.email,
                        'email_to': recursos_humano.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, recursos_humano.partner_id.id), (4, juridico.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {recursos_humano.name}',
                    })
                    chat_message = f"<b>Aprobación de jurídico</b>\n\n{mensaje_con_enlace}"
                    chat_channel.sudo().message_post(
                        body=chat_message,
                        author_id=self.env.user.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {recursos_humano.name}: {str(e)}")

            # Actualizar el estado de la contratación
            vals_estado = {'estado_contratacion': 'recursos_humanos'}
            vals_estado.update(self._vals_limpiar_observaciones_flujo_viejo('observaciones_juri'))
            self.write(vals_estado)

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo recursos humanos no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                        }
                }

    def aprueba_coordinador_j(self, documentos=None):
        if self.usa_flujo_contratacion_nuevo:
            return self.aprueba_coordinador_enviar_ventanilla(documentos=documentos)
        self._asegurar_estado_coordinador_para_aprobar()
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})


        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.proyecto_relacionado.users_with_access | self.proyecto_relacionado.users_with_access_financiero)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        users_with_financial_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_financiero') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_financiero') in user.groups_id
            )
        )

        # Obtener el usuario coordinador actual
        coordinador = self.env.user

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones)
        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)
        # Verificar si hay usuarios financieros relacionados con el proyecto
        if users_with_financial_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, financiero, el coordinador ha aprobado el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for financiero in users_with_financial_role:
                try:
                    if not financiero.email:
                        raise UserError(f"El usuario financiero {financiero.name} no tiene correo configurado.")

                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, financiero:</p>
                        <p>El coordinador ha aprobado el documento del contratista <strong>{self.new_name}</strong>. Por favor, revisarlo.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones}</p>' if observaciones else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Aprobaci&oacute;n de documento")
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Aprobación de documento de {self.new_name}",
                        'email_from': coordinador.email,
                        'email_to': financiero.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.sudo().send(raise_exception=True)
                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {financiero.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            vals_estado = {'estado_contratacion': 'financiera'}
            vals_estado.update(self._vals_limpiar_observaciones_flujo_viejo('observaciones'))
            self.write(vals_estado)

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios con el rol financiero asociados al proyecto.")

    def aprueba_coordinador_j_2(self, documentos=None):
        self._asegurar_estado_coordinador_para_aprobar()
        # Verificar si la aprobación del coordinador ya se ha realizado en un estado diferente
        if self.aprobacion_coordinador_realizada and self.estado_contratacion != 'coordinador':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de coordinador ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }


        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'coordinador':
            self.write({'aprobacion_coordinador_realizada': True})

        # Obtener el usuario coordinador actual
        coordinador = self.env.user


        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.proyecto_relacionado.users_with_access | self.usuarios_juridico_ids)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        users_with_juridicos_role = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_juridico') in user.groups_id
            )
        )


        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Obtener las observaciones
        observaciones = self._texto_observacion_flujo_nuevo(self.observaciones)

        # Verificar si hay usuarios Jurídico relacionados con el proyecto
        if users_with_juridicos_role:
            mensaje = f"""Proyecto: {nombre_proyecto}
            Hola, departamento jurídico. El coordinador corrigió y aprobó el FC del contratista <strong>{self.new_name}</strong>, por favor revisar."""
            if observaciones:
                mensaje += f"\nObservaciones: {observaciones}"

            for juridicos in users_with_juridicos_role:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML una sola vez
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, Jurídico:</p>
                        <p>El coordinador corrigió y aprobó el FC del contratista <strong>{self.new_name}</strong>. Por favor, revisarlo.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones}</p>' if observaciones else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Aprobaci&oacute;n de documento")
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Aprobación de documento de {self.new_name}",
                        'email_from': coordinador.email,
                        'email_to': juridicos.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat pero no enviar correo electrónico
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridicos.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridicos.name}',
                    })
                    chat_channel.sudo().message_post(
                        body=mensaje_con_enlace,
                        subject='Aprobación de contratación',
                        author_id=coordinador.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                        partner_ids=[coordinador.id]
                    )

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridicos.name}: {str(e)}")

            # Actualizar el estado del coordinador y archivar el registro
            self.proyecto_relacionado.write({'coordinador_g': 'yellow'})
            self._enviar_a_estado_juridico_asignado(
                self._vals_limpiar_observaciones_flujo_viejo('observaciones')
            )

            self.proyecto_relacionado.write({'juridica_g': 'yellow'})
            self.proyecto_relacionado.write({'financiera_g': 'yellow'})
            self.proyecto_relacionado.write({'recursos_humanos_g': 'yellow'})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            raise UserError("No hay usuarios con el rol Jurídico asociados al proyecto.")

    def aprueba_financiera(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "financiera"
        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})

        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.proyecto_relacionado.users_with_access | self.usuarios_juridico_ids)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        juridicos = usuarios_combinados.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id
        )

        # Obtener el usuario financiero actual
        financiero = self.env.user
        observaciones_finacie = self._texto_observacion_flujo_nuevo(self.observaciones_finacie)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo jurídico
        if juridicos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, departamento jurídico. El departamento financiero le informa que ya puedes revisar los documentos del contratista <strong>{self.new_name}</strong>."""
            if observaciones_finacie:
                mensaje += f"<br/>Observaciones: {observaciones_finacie}"

            for juridico in juridicos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, departamento jurídico:</p>
                        <p>El departamento financiero le informa que ya puedes revisar los documentos del contratista <strong>{self.new_name}</strong>.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_finacie}</p>' if observaciones_finacie else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Revisi&oacute;n de documentos")
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Revisión de documentos de {self.new_name}",
                        'email_from': financiero.email,
                        'email_to': juridico.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridico.partner_id.id), (4, financiero.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridico.name} y {financiero.name}',
                    })
                    chat_message = f"<b>Aprobación de financiación</b>\n\n{mensaje_con_enlace}"
                    chat_channel.sudo().message_post(
                        body=chat_message,
                        author_id=self.env.user.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )


                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridico.name}: {str(e)}")

            # Actualizar el estado de la contratación
            self._enviar_a_estado_juridico_asignado(
                self._vals_limpiar_observaciones_flujo_viejo('observaciones_finacie')
            )

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo jurídico no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                        }
                }

    def aprueba_financiera_j(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "financiera"
        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})

            # Después de realizar las acciones necesarias, actualiza el campo generar_informe_visible_j
            if self.select_persona == 'persona_juridica':
                self.write({'generar_informe_visible_j': True})
            else:
                self.write({'generar_informe_visible_j': False})

        # Obtener los usuarios del grupo jurídico desde los campos relacionados

        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.proyecto_relacionado.users_with_access | self.usuarios_juridico_ids)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        juridicos = usuarios_combinados.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id
        )

        # Obtener el usuario financiero actual
        financiero = self.env.user
        observaciones_finacie = self._texto_observacion_flujo_nuevo(self.observaciones_finacie)


        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo jurídico
        if juridicos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                         Hola, departamento jurídico. El departamento financiero le informa que ya puedes revisar los documentos del contratista <strong>{self.new_name}</strong>."""
            if observaciones_finacie:
                mensaje += f"<br/>Observaciones: {observaciones_finacie}"

            for juridico in juridicos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"
                    # Crear el cuerpo del correo HTML
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, departamento jurídico:</p>
                        <p>El departamento financiero le informa que ya puedes revisar los documentos del contratista <strong>{self.new_name}</strong>.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_finacie}</p>' if observaciones_finacie else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Revisi&oacute;n de documentos")
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Revisión de documentos de {self.new_name}",
                        'email_from': financiero.email,
                        'email_to': juridico.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridico.partner_id.id), (4, financiero.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridico.name} y {financiero.name}',
                    })
                    chat_message = f"<b>Aprobación de financiación</b>\n\n{mensaje_con_enlace}"
                    chat_channel.sudo().message_post(
                        body=chat_message,
                        author_id=self.env.user.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )

                except Exception as e:
                    raise UserError(f"No se pudo enviar el mensaje a {juridico.name}: {str(e)}")

            # Actualizar el estado de la contratación
            self._enviar_a_estado_juridico_asignado(
                self._vals_limpiar_observaciones_flujo_viejo('observaciones_finacie')
            )

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,
                    'message_duration': 9,
                }
            }
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo jurídico no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                        }
                }

    def aprueba_recursos_humanos(self):
        if self.aprobacion_recursos_humanos_realizada and self.estado_contratacion != 'recursos_humanos':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de recursos humanos ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "recursos_humanos"
        if self.estado_contratacion == 'recursos_humanos':
            self.write({'aprobacion_recursos_humanos_realizada': True})

            # Después de realizar las acciones necesarias, actualiza el campo generar_informe_visible
        if self.select_persona in ['persona_natural', 'contrato_laboral']:
            self.write({'generar_informe_visible': True})
        else:
            self.write({'generar_informe_visible': False})

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Combinar ambas búsquedas en una sola operación
        usuarios_combinados = (self.proyecto_relacionado.users_with_access | self.usuarios_juridico_ids)

        # Filtrar los usuarios que pertenecen a los grupos deseados
        juridicos = usuarios_combinados.filtered(
            lambda user: (
                    self.env.ref('Matriz.group_contratacion_director_juridico') in user.groups_id or
                    self.env.ref('Matriz.group_contratacion_juridico') in user.groups_id
            )
        )


        # Obtener el usuario de recursos humanos actual
        recursos_humanos = self.env.user
        observaciones_recursos = self._texto_observacion_flujo_nuevo(self.observaciones_recursos)

        # Verificar si hay usuarios en el grupo jurídico
        if juridicos:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, jurídico, el departamento de recursos humanos informa que todo está listo para generar el contrato para de <strong>{self.new_name}</strong>."""
            if observaciones_recursos:
                mensaje += f"<br/>Observaciones: {observaciones_recursos}"

            for juridico in juridicos:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, jurídico:</p>
                        <p>El departamento de recursos humanos informa que todo está listo para generar el contrato de <strong>{self.new_name}</strong>.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_recursos}</p>' if observaciones_recursos else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Contrato listo para generar")

                    mail_values = {
                        'subject': f"{nombre_proyecto} - Contrato listo para generar de {self.new_name}",
                        'email_from': recursos_humanos.email,
                        'email_to': juridico.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, juridico.partner_id.id), (4, recursos_humanos.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {juridico.name}',
                    })
                    chat_channel.sudo().message_post(
                        body=mensaje_con_enlace,
                        author_id=recursos_humanos.partner_id.id,
                        message_type='comment',
                        subtype_id=self.env.ref('mail.mt_comment').id,
                    )


                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {juridico.name}: {str(e)}")

            # Actualizar el estado de la contratación
            vals_estado = {
                'estado_contratacion_2': 'juridico_2',
            }
            vals_estado.update(
                self._vals_limpiar_observaciones_flujo_viejo('observaciones_recursos')
            )
            self._enviar_a_estado_juridico_asignado(vals_estado)

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            # Si no hay usuarios en el grupo jurídico, enviar mensaje de error al coordinador
            coordinator = self.env['res.users'].search([('id', 'in', self.users_with_access.ids), (
                'groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)], limit=1)
            if coordinator:
                chat_channel_error = self.env['mail.channel'].sudo().create({
                    'channel_partner_ids': [(4, coordinator.partner_id.id)],
                    'channel_type': 'chat',
                    'name': f'Chat con {coordinator.name}',
                })
                error_message = "El grupo jurídico no se encuentra incluido en la lista para el envío del mensaje. Por favor, añádalo a la lista."
                chat_channel_error.sudo().message_post(
                    body=error_message,
                    author_id=coordinator.partner_id.id,
                    message_type='comment',
                    subtype_id=self.env.ref('mail.mt_comment').id,
                )
                # Mostrar notificación de error
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Error',
                        'sticky': False,
                        'message': 'No hay un grupo en la lista de usuarios al proyecto para enviar el mensaje.',
                        'type': 'danger',
                        'message_sticky': False,
                        'message_duration': 9,
                        }
                }

    def juridico_no_Aprobado(self):
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de juridico ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'juridico':
            self.write({'aprobacion_juridico_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_director_j = self.env.ref('Matriz.group_contratacion_director_juridico')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles específicos
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or
                         grupo_director_j in user.groups_id or
                         grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        juridico = self.env.user

        # Obtener las observaciones
        observaciones_juri = self._texto_observacion_flujo_nuevo(self.observaciones_juri)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, coordinador. El departamento jurídico <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

            if observaciones_juri:
                mensaje += f"\nObservaciones: {observaciones_juri}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, coordinador:</p>
                        <p>El departamento jurídico <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_juri}</p>' if observaciones_juri else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Contrato no aprobado")

                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Contrato no aprobado de {self.new_name}",
                        'email_from': juridico.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Actualizar el estado una sola vez, despues de notificar a todos.
            self.write({
                'estado_contratacion': 'coordinador',
                'estado_contratacion_nun': '2',
                'observaciones_juri': False,
            })

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")

    def juridico_no_Aprobado_j(self):
        if self.aprobacion_juridico_realizada and self.estado_contratacion != 'juridico':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de juridico ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'juridico':
            self.write({'aprobacion_juridico_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_director_j = self.env.ref('Matriz.group_contratacion_director_juridico')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles específicos
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or
                         grupo_director_j in user.groups_id or
                         grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        juridico = self.env.user

        # Obtener las observaciones
        observaciones_juri = self._texto_observacion_flujo_nuevo(self.observaciones_juri)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>
                        Hola, coordinador. El departamento jurídico <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

            if observaciones_juri:
                mensaje += f"\nObservaciones: {observaciones_juri}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Enviar correo electrónico al coordinador
                    # Crear el cuerpo del correo HTML
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, coordinador:</p>
                        <p>El departamento jurídico <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_juri}</p>' if observaciones_juri else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Contrato no aprobado")

                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Contrato no aprobado de {self.new_name}",
                        'email_from': juridico.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Actualizar el estado una sola vez, despues de notificar a todos.
            self.write({
                'estado_contratacion': 'coordinador',
                'estado_contratacion_nun_juridica': '2',
                'observaciones_juri': False,
            })

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")

    def financiera_no_Aprobado(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles financieros
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        financieros = self.env.user

        # Obtener las observaciones
        observaciones_finacie = self._texto_observacion_flujo_nuevo(self.observaciones_finacie)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>"\
                        Hola, coordinador. El departamento financiero <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

            if observaciones_finacie:
                mensaje += f"\nObservaciones: {observaciones_finacie}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML con observaciones incluidas
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, coordinador:</p>
                        <p>El departamento. Financiero <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_finacie}</p>' if observaciones_finacie else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Contrato no aprobado")

                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Contrato no aprobado de {self.new_name}",
                        'email_from': financieros.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }
                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Actualizar el estado una sola vez, despues de notificar a todos.
            self.write({
                'estado_contratacion': 'coordinador',
                'observaciones_finacie': False,
            })

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")

    def financiera_no_Aprobado_j(self):
        if self.aprobacion_financiera_realizada and self.estado_contratacion != 'financiera':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de financiera ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'financiera':
            self.write({'aprobacion_financiera_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles financieros
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        financieros = self.env.user

        # Obtener las observaciones
        observaciones_finacie = self._texto_observacion_flujo_nuevo(self.observaciones_finacie)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>"\
                        Hola, coordinador. El departamento financiero <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""


            if observaciones_finacie:
                mensaje += f"\nObservaciones: {observaciones_finacie}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, coordinador:</p>
                        <p>El departamento financiero <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_finacie}</p>' if observaciones_finacie else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Contrato no aprobado")
                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Contrato no aprobado de {self.new_name}",
                        'email_from': financieros.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                    # Crear un canal de chat y enviar mensaje
                    chat_channel = self.env['mail.channel'].sudo().create({
                        'channel_partner_ids': [(4, coordinador.partner_id.id), (4, financieros.partner_id.id)],
                        'channel_type': 'chat',
                        'name': f'Chat con {coordinador.name}',
                    })
                    chat_channel.sudo().message_post(
                        body=mensaje_con_enlace,
                        subject='Aprobación de contratación',
                        author_id=financieros.partner_id.id,
                        message_type='comment',

                        partner_ids=[financieros.id]
                    )

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Actualizar el estado una sola vez, despues de notificar a todos.
            self.write({
                'estado_contratacion': 'coordinador',
                'observaciones_finacie': False,
            })

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")

    def humanos_no_Aprobado(self):
        if self.aprobacion_recursos_humanos_realizada and self.estado_contratacion != 'recursos_humanos':
            # Acción ya realizada en un estado diferente, puedes mostrar un mensaje o realizar otra lógica
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Acción ya realizada',
                    'sticky': False,
                    'message': 'La acción de recursos_humanos ya ha sido realizada en un estado diferente.',
                    'type': 'warning',
                    'message_sticky': False,
                    'message_duration': 5,
                }
            }

        # Marcar la acción como realizada solo si el estado es "coordinador"
        if self.estado_contratacion == 'recursos_humanos':
            self.write({'aprobacion_recursos_humanos_realizada': True})

        # Obtener los grupos por separado
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador')
        grupo_asis_coordinador = self.env.ref('Matriz.group_contratacion_asis_coordinador')

        # Obtener los usuarios relacionados con el proyecto que tienen los roles financieros
        coordinadores = self.proyecto_relacionado.users_with_access.filtered(
            lambda user: grupo_coordinador in user.groups_id or grupo_asis_coordinador in user.groups_id
        )

        # Obtener el usuario jurídico actual
        recursos_humanos = self.env.user

        # Obtener las observaciones
        observaciones_recursos = self._texto_observacion_flujo_nuevo(self.observaciones_recursos)

        # Obtener ambos nombres del proyecto relacionado
        nombres_proyecto = [self.proyecto_relacionado.name, self.proyecto_relacionado.new_name]
        nombres_proyecto_filtrados = [nombre for nombre in nombres_proyecto if nombre]  # Filtrar nombres vacíos

        # Unir los nombres disponibles
        nombre_proyecto = ', '.join(nombres_proyecto_filtrados)

        # Verificar si hay usuarios en el grupo de coordinadores
        if coordinadores:
            mensaje = f"""<strong>{nombre_proyecto}</strong>"\
                        Hola, coordinador. El departamento de recursos humanos <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar."""

            if observaciones_recursos:
                mensaje += f"\nObservaciones: {observaciones_recursos}"

            for coordinador in coordinadores:
                try:
                    # Construir la URL con la base de Odoo
                    formulario_url = f'/web#id={self.id}&view_type=form&model=contrataciones_fuv'
                    formulario_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') + formulario_url

                    mensaje_con_enlace = f"{mensaje}<br/><a href='{formulario_url}'>Ir al Formulario</a>"

                    # Crear el cuerpo del correo HTML
                    body_html = f"""
                        <p><strong>{nombre_proyecto}</strong></p>
                        <p>Hola, coordinador:</p>
                        <p>El departamento de recursos humanos <b>no aprobó</b> el documento del contratista <strong>{self.new_name}</strong>, por favor revisar.</p>
                        {f'<p><strong>Observaciones:</strong> {observaciones_recursos}</p>' if observaciones_recursos else ''}
                        <p><a href='{formulario_url}'>Ir al Formulario</a></p>
                    """
                    body_html = self._envolver_correo_flujo_viejo(body_html, "Contrato no aprobado")
                    # Enviar correo electrónico al coordinador
                    mail_values = {
                        'subject': f"{nombre_proyecto} - Contrato no aprobado de {self.new_name}",
                        'email_from': recursos_humanos.email,
                        'email_to': coordinador.email,
                        'body_html': body_html,
                        'auto_delete': True,
                    }

                    mail_id = self.env['mail.mail'].sudo().create(mail_values)
                    mail_id.send()

                except Exception as e:
                    raise UserError(f"No se pudo enviar el correo a {coordinador.name}: {str(e)}")

            # Actualizar el estado una sola vez, despues de notificar a todos.
            self.write({
                'estado_contratacion': 'coordinador',
                'estado_contratacion_nun': '3',
                'observaciones_recursos': False,
            })

            # Notificación de éxito
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Mensaje enviado',
                    'sticky': False,  # No será pegajoso
                    'message': 'El mensaje ha sido enviado correctamente.',
                    'type': 'success',
                    'message_sticky': False,  # El mensaje desaparecerá automáticamente
                    'message_duration': 9,  # Duración en segundos
                }
            }
        else:
            raise UserError("No hay usuarios en el grupo de coordinadores para enviar el mensaje.")

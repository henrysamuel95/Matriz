#!/usr/bin/python
from odoo import fields, models, api
import time
from datetime import datetime, timedelta
from odoo.exceptions import UserError
import logging


_logger = logging.getLogger(__name__)


class FICHA_RESUMEN(models.Model):
    _name = "ficharesumen"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "new_name"
    
    new_name = fields.Char(string='Nuevo nombre', related='nombre_proyecto')

    def Guardar(self):
        self.ensure_one()
        self.write({'name': self.new_name})
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
        
    mcb_ids_2 = fields.One2many('mcb', 'ficharesumen_id', string='MCB')
    contratacio2_id = fields.Many2one('contratacion', string='Contratacion')
    secop_options = fields.Many2one(
        string="Secop",
        related='informacion_base_id.secop_option_id',
        tracking=True
    )

    is_editable = fields.Boolean(string='Es editable', default=False, related="contratacio2_id.is_editable")

    apodo = fields.Char()

    def Abrir_ficharesumen(self):
        # Establecer el valor de apodo a 'MCB'
        self.apodo = 'Resumen del proyecto'

        # Devolver la acción para abrir el formulario del registro actual
        action = {
            'name': 'Resumen del proyecto',
            'type': 'ir.actions.act_window',
            'res_model': 'ficharesumen',
            'view_mode': 'form',
            'res_id': self.id,  # ID del registro actual
            'target': 'current',
        }

        return action

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass

    def actualizar_relacion(self):
        self.relacionar_mcb()
        # Devolver la acción para abrir el formulario del registro actual
        action = {
            'name': self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'ficharesumen',
            'view_mode': 'form',
            'res_id': self.id,  # ID del registro actual
            'target': 'current',
        }

        return action

    def relacionar_mcb(self):
        # Recorre cada registro de FICHA_RESUMEN
        for ficha in self:
            # Obtener el valor para la búsqueda del nombre en "new_name" o "name"
            nombre_ficha = ficha.new_name or ficha.nombre  # Prioriza "new_name", pero usa "nombre" si está vacío

            # Busca los registros de MCB que tengan el mismo "new_name" o "name" que el de FICHA_RESUMEN
            mcb_records = self.env['mcb'].search(['|', ('new_name', '=', nombre_ficha), ('name', '=', nombre_ficha)])

            # Relacionar cada registro de MCB encontrado con la ficha actual
            mcb_records.write({'ficharesumen_id': ficha.id})
            ficha.mcb_ids_2 = [(6, 0, mcb_records.ids)]  # Actualiza el One2many para reflejar la relación

        return True  # Opcional: solo para indicar que se ejecutó el proceso

    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto:',
                                         related='contratacio2_id.users_with_access')

    gestion_seleccionada = fields.Boolean(string='Gestión seleccionada', compute='_compute_gestion_seleccionada',
                                          store=True)


    create_uid = fields.Many2one('res.users', string='Creador del proyecto: ', readonly=True)

    @api.depends('informacion_base_id')
    def _compute_gestion_seleccionada(self):
        for record in self:
            record.gestion_seleccionada = bool(record.informacion_base_id)

    name = fields.Char(string="Nombre", related='apodo')
    informacion_base_id = fields.Many2one('informacion_base_agregar', string='Información base')

    fch_creacion = fields.Datetime(string='Fecha creación', copy=False, default=lambda self: fields.Datetime.now(),
                                   tracking=True)
    write_date = fields.Datetime(string='Fecha de modificación', readonly=True, tracking=True)




    title = fields.Char(string="FORMULARIO DE INGRESO DE DATOS DEL PROYECTO", tracking=True)

    def button_informacion_base(self):
        self.ensure_one()
        
        # 1. Forzamos la relación directamente en el registro de destino antes de abrirlo
        if self.informacion_base_id:
            self.informacion_base_id.write({
                'contratacion_id': self.contratacio2_id.id
            })
        
        # 2. Retornamos la vista
        return {
            'name': 'Editar información del proyecto',
            'type': 'ir.actions.act_window',
            'res_model': 'informacion_base_agregar',
            'res_id': self.informacion_base_id.id,
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_contratacion_id': self.contratacio2_id.id,
            },
        }

    Fecha_Emisión = fields.Date(string="Fecha de emisión:", tracking=True)
    Fecha_Actualización = fields.Date(string="Fecha de actualización:", tracking=True)
    Fecha_de_Impresión = fields.Date(string="Fecha de impresión:", tracking=True)

    id_proyecto2 = fields.Char(string='Id Proyecto', related='informacion_base_id.id_proyecto')
    Contrato_Número = fields.Char(string="Número de contrato:", related='informacion_base_id.numero_contrato',
                                  tracking=True)
    nombre_proyecto = fields.Char(string="Nombre del proyecto", related='informacion_base_id.nombre_proceso_proyecto',
                                  tracking=True)
    fecha_inicio = fields.Date(string="Fecha de inicio del proyecto", related='informacion_base_id.fecha_acta_inicio',
                               tracking=True)
    fecha_finalizacion = fields.Date(string="Fecha de finalización del proyecto",
                                     related='informacion_base_id.fecha_acta_fin', tracking=True)

    dias_faltantes = fields.Integer(compute="_compute_fecha_finalizacion")

    @api.depends('fecha_finalizacion')
    def _compute_fecha_finalizacion(self):
        self.relacionar_mcb()
        for record in self:
            if record.fecha_finalizacion:
                record.dias_faltantes = (record.fecha_finalizacion - fields.Date.today()).days
            else:
                record.dias_faltantes = 0
        self._onchange_fecha()

    def _onchange_fecha(self):
        # Solo se ejecuta si hay fechas
        if self.fecha_inicio and self.fecha_finalizacion:
            # Obtener el tipo de actividad "Correo electrónico"
            activity_type = self.env['mail.activity.type'].search([('name', '=', 'Recordatorio')], limit=1)

            if activity_type:
                # Actualizar el activity_type_id en todos los registros
                contrataciones = self.env['ficharesumen'].search([])
                for contratacion in contrataciones:
                    if not contratacion.activity_type_id:  # Solo si no está establecido
                        contratacion.activity_type_id = activity_type.id

                self.message_notify(
                    body="Se ha creado un Recordatorio para las fechas proporcionadas en todos los registros.",
                    partner_ids=[self.env.user.partner_id.id],  # Notifica al usuario actual
                )

    cron_job_id = fields.Many2one('ir.cron', string="Cron Job")

    def create_or_update_cron_job(self):
        if not self.cron_job_id:
            cron_job = self.env['ir.cron'].sudo().create({
                'name': f"alerta {self.id}",
                'model_id': self.env['ir.model'].search([('model', '=', self._name)]).id,
                'state': 'code',
                'code': f"model.verificar_y_crear_actividad()",
                'interval_number': 1,
                'interval_type': 'days',
                'numbercall': -1,
                'doall': False,
                'active': True,
                'user_id': self.env.user.id,
                'nextcall': fields.Datetime.from_string(f"{datetime.now().date()} 12:00:00")
            })
            self.cron_job_id = cron_job.id
        else:
            self.cron_job_id.write({
                'nextcall': fields.Datetime.from_string(f"{datetime.now().date()} 12:00:00"),
            })
        print("Cron job creado o actualizado.")

    def verificar_y_crear_actividad(self):
        try:
            time.sleep(1)
            # Días para los cuales se enviarán notificaciones
            dias_notificacion = [30, 15, 10, 5, 1]

            # Recorre todos los registros del modelo 'ficharesumen' para verificar cada uno
            for record in self.env['ficharesumen'].search([]):
                # Verifica si 'dias_faltantes' del registro está en la lista de días de notificación
                if record.dias_faltantes in dias_notificacion:
                    time.sleep(1)

                    # Asegúrate de que haya un tipo de actividad asociado al registro
                    if not record.activity_type_id:
                        raise UserError("No se ha seleccionado un tipo de actividad para este registro.")

                    # Obtener el tipo de actividad desde el campo 'activity_type_id' del registro
                    activity_type = record.activity_type_id
                    print(f"Tipo de actividad seleccionado: {activity_type.name}")

                    # Obtener los coordinadores con acceso al proyecto
                    coordinadores = record.contratacio2_id.users_with_access.filtered(
                        lambda user: self.env.ref('Matriz.group_contratacion_coordinador') in user.groups_id
                    )

                    if not coordinadores:
                        raise UserError(
                            "No hay coordinadores en el grupo 'Contratación Coordinador' con acceso al proyecto.")

                    # Preparar el resumen y la nota de la actividad
                    summary = f"Contrato próximo a vencer en {record.dias_faltantes} días"
                    note = f"Por favor revise el contrato '{record.display_name}', próximo a vencer en {record.dias_faltantes} días. Considere agregar una prórroga de ser necesaria."

                    mcb_record = record.mcb_ids_2[0]  # Toma el primer registro en `mcb_ids_2`

                    created_activity = None  # Variable para guardar la actividad creada

                    # Iterar sobre todos los coordinadores y crear una actividad para cada uno
                    for coordinador in coordinadores:
                        # Buscar si ya existe una actividad relacionada con este registro y coordinador
                        existing_activity = self.env['mail.activity'].search([
                            ('res_model', '=', 'mcb'),
                            ('res_id', '=', mcb_record.id),
                            ('activity_type_id', '=', activity_type.id),
                            ('user_id', '=', coordinador.id)
                        ], limit=1)

                        if not existing_activity:
                            # Crear la actividad de recordatorio
                            created_activity = self.env['mail.activity'].create({
                                'activity_type_id': activity_type.id,
                                'summary': summary,
                                'note': note,
                                'date_deadline': record.fecha_finalizacion - timedelta(days=record.dias_faltantes),
                                'user_id': coordinador.id,
                                'res_model_id': self.env['ir.model']._get_id('mcb'),  # Enlazar a `mcb`
                                'res_id': mcb_record.id,  # ID del primer registro en `mcb_ids`
                            })

                        else:
                            print(
                                f"Ya existe una actividad para el registro {record.display_name} y el coordinador {coordinador.name}")

                    # Asignar la última actividad creada al campo activity_id del registro
                    if created_activity:
                        record.activity_id = created_activity
                        print(f"Actividad asignada al campo activity_id para el registro: {record.display_name}")

                    # Enviar correo de alerta con el enlace al modelo 'mcb'
                    self.env['mail.mail'].create({
                        'subject': f'🚨 Alerta: Contrato próximo a vencer - {record.display_name}',
                        'body_html': f"""
                            <p>Estimado/a Coordinador/a,</p>
                            <p>Le informamos que el contrato <b>{record.display_name}</b> se encuentra próximo a vencer en <b>{record.dias_faltantes} días</b>.</p>
                            <p>Le recomendamos revisar el contrato y, si corresponde, gestionar una prórroga o tomar las acciones necesarias para su renovación.</p>
                            <p>Puede acceder al registro del contrato <b>{record.display_name}</b> en el sistema MCB utilizando el siguiente enlace:</p>
                            <p><a href="{self.env['ir.config_parameter'].get_param('web.base.url')}/web#id={mcb_record.id}&view_type=form&model=mcb&action=107" style="color: #0056b3; text-decoration: underline;">Ver Registro en MCB</a></p>
                            <p>Si tiene alguna duda o requiere soporte, no dude en comunicarse con nosotros.</p>
                            <p>Atentamente,</p>
                            <p><i>El equipo de soporte SGO</i></p>
                        """,
                        'email_to': coordinador.email,  # Enviar al correo del coordinador
                    }).send()

                    # Imprimir un mensaje indicando que se ejecutó la notificación
                    print(f"Se ejecutó la creación de actividades para el registro: {record.display_name}")

        except UserError as e:
            # Verifica si la excepción es por el bloqueo de cron
            if 'Record cannot be modified right now' in str(e):
                print("La tarea cron está bloqueada. Reintentando...")
                time.sleep(10)  # Espera 10 segundos antes de reintentar
                self.verificar_y_crear_actividad()  # Llama nuevamente a la función para reintentar

        except Exception as e:
            # Captura cualquier otro error y muestra un mensaje
            print(f"Error inesperado: {str(e)}")

    activity_id = fields.Many2one(
        'mail.activity',
        string="Tarea",
    )

    activity_type_id = fields.Many2one('mail.activity.type', string="Tarea", store=True)
    res_model = fields.Char(string='Modelo')

    def _remove_cron_job(self):
        if self.cron_job_id:
            self.cron_job_id.unlink()
            self.cron_job_id = False
            print("Cron job eliminado.")


    objeto = fields.Char(string="Objeto", related='informacion_base_id.objeto_proyecto')
    descripcion_beneficiarios = fields.Char(string="Descripción de beneficiarios",
                                            related='informacion_base_id.descripcion_beneficiarios', tracking=True)
    modalidad_contratacion = fields.Selection([
        ('CI', 'Carta de Instrucción (CI)'),
        ('CC', 'Contrato de Consultoria (CC)'),
        ('CO', 'Contrato de Obra (CO)'),
        ('CIA', 'Contrato Interadministrativo (CIA)'),
        ('CLP', 'Contrato de Licitaciones Publicas (CLP)'),
        ('CPS', 'Contrato de Prestacion de Servicios (CPS)'),
        ('OC', 'Orden de Compra (OC)'),
        ('OS', 'Orden de Servicios (OS)'),
        ('CS', 'Contrato de Suministro (CS)'),
        ('CA', 'Convenio de Asociacion (CA)'),
        ('CCF', 'Convenio de Cofinanciacion (CCF)'),
        ('CEC', 'Convenio Especial de Cooperacion (CEC)'),
        ('CIA', 'Convenio Interadministrativo (CIA)'),
    ], string="Modalidad de contratación", related='informacion_base_id.modalidad_contratacion', tracking=True)
    modalidad_id = fields.Many2one(
       'modalidad_contratacion',
        string='Modalidad de Contratación',
        tracking=True,
        related = 'informacion_base_id.modalidad_id'
    )
    currency_id = fields.Many2one('res.currency', string='Moneda')
    valor_contratar = fields.Monetary(string="Valor a contratar",
                                   related='informacion_base_id.valor_operacion_para_contratar', tracking=True)
    total_beneficiarios = fields.Integer(string="Total de beneficiarios",
                                         related='informacion_base_id.total_beneficiarios', tracking=True)
    valor_total_proyecto = fields.Float(string="Valor total del proyecto",
                                        related='informacion_base_id.valor_total_neto', tracking=True)
    secop = fields.Boolean(string="Secop", related='informacion_base_id.secop', tracking=True)

    Número_de_proceso_entidad = fields.Integer(string="Número de procesos entidades", tracking=True)

    ###############Entidades##################################Entidades#####################################
    entidad_relacionada = fields.Char(string="Primera entidad relacionada",
                                      related='informacion_base_id.primera_entidad_relacionada', tracking=True)
    NIT = fields.Char(string="NIT-Primera entidad", related='informacion_base_id.nit_primera_entidad', tracking=True)
    Cuantía4 = fields.Monetary(string="Cuantía (COP)", tracking=True)


    Otra_entidad1 = fields.Char(string="Segunda entidad relacionada",
                                related='informacion_base_id.segunda_entidad_relacionada', tracking=True)
    NIT2 = fields.Char(string="NIT-Segunda entidad", related='informacion_base_id.nit_segunda_entidad', tracking=True)
    Cuantía1 = fields.Monetary(string="Cuantía (COP)", tracking=True)

    Otra_entidad2 = fields.Char(string="Tercera entidad relacionada",
                                related='informacion_base_id.tercera_entidad_relacionada', tracking=True)
    NIT3 = fields.Char(string="NIT-Tercera entidad", related='informacion_base_id.nit_tercera_entidad', tracking=True)
    Cuantía2 = fields.Monetary(string="Cuantía (COP)", tracking=True)
    ###################Entidades########################################################Entidades######################

    eje_trabajo = fields.Selection([
        ('cambio_climatico', 'Cambio climático'),
        ('bienes_publicos', 'Generación de bienes públicos'),
        ('desarrollo_social', 'Desarrollo social'),
        ('desarrollo_economico', 'Desarrollo económico'),
        ('ciencia_tecnologia_innovacion', 'Ciencia tecnología e innovación CTI'),
    ],string="Eje de trabajo", related='informacion_base_id.eje_trabajo', tracking=True)
    coordinador_asignado = fields.Many2one('res.users', string="Coordinador responsable",
                                           related='informacion_base_id.asignar_coordinador',
                                           store=True)
    correo_electronico = fields.Char(string="Correo electrónico", tracking=True)
    cargo = fields.Char(string="Cargo", tracking=True)
    telefono = fields.Char(string="Teléfono", tracking=True)
    extension = fields.Char(string="Ext.", tracking=True)
    carta_invitacion = fields.Selection([
        ('SI', 'SI'),
        ('NO', 'NO')
    ], string="Carta de invitación", tracking=True)

    fecha1 = fields.Date(string="Fecha")
    estudios_previos_si = fields.Selection([
        ('SI', 'SI'),
        ('NO', 'NO'),
    ], string="Estudios previos", tracking=True)
    fecha2 = fields.Date(string="Fecha", tracking=True)
    estudio_mercado_documento_tecnico_si = fields.Selection([
        ('SI', 'SI'),
        ('NO', 'NO'),
    ], string="Estudio de mercado (análisis de mercado)", tracking=True)
    Documento_Técnico = fields.Selection([
        ('SI', 'SI'),
        ('NO', 'NO'),
    ], string="Documento técnico", tracking=True)
    matriz_riesgo = fields.Selection([
        ('SI', 'SI'),
        ('NO', 'NO'),
    ], string="Matriz de riesgo", tracking=True)
    fecha3 = fields.Date(string="Fecha", tracking=True)
    supervisor_interventor = fields.Char(string="Supervisor y/o interventor del proyecto", tracking=True)
    supervisor_interventor_correo = fields.Char(string="Correo electrónico (opcional)", tracking=True)
    supervisor_interventor_area_entidad = fields.Char(string="Área de entidad del proceso", tracking=True)
    supervisor_interventor_celular = fields.Char(string="Celular", tracking=True)
    supervisor_interventor_telefono = fields.Char(string="Teléfono", tracking=True)
    supervisor_interventor_extension = fields.Char(string="Ext.", tracking=True)
    municipio = fields.Char(string="Municipio", tracking=True)


    tag_municipios_ids = fields.Many2many('municipio', 'municipio_ejecucion', string='Municipio',
                                          related='informacion_base_id.tag_municipios2_ids')
    tag_municipios_ids2 = fields.Many2many('municipio', 'municipio_beneficiario', string='Municipios',
                                           related='informacion_base_id.tag_municipios_ids')


    def write(self, vals):
        # 1. Verificamos si los campos que nos importan vienen en los valores a guardar
        campos_a_monitorear = ['tag_municipios_ids', 'tag_municipios_ids2']
        if not any(campo in vals for campo in campos_a_monitorear):
            # Si no están, hacemos el guardado normal y salimos rápido para no afectar rendimiento
            return super(FICHA_RESUMEN, self).write(vals)

        # 2. Tomamos una "foto" del estado ANTES de guardar
        estado_anterior = {}
        for record in self:
            estado_anterior[record.id] = {
                'ejecucion': set(record.tag_municipios_ids.ids),
                'beneficiados': set(record.tag_municipios_ids2.ids)
            }

        # 3. Dejamos que Odoo guarde los cambios en la base de datos
        res = super(FICHA_RESUMEN, self).write(vals)

        # 4. Comparamos el "Antes" con el "Después" para generar la trazabilidad
        for record in self:
            mensajes = []
            antes = estado_anterior[record.id]
            
            # Convertimos las nuevas listas a Sets (conjuntos) para matemáticas simples
            despues_ejecucion = set(record.tag_municipios_ids.ids)
            despues_beneficiados = set(record.tag_municipios_ids2.ids)

            # --- Análisis de Municipio(s) de Ejecución ---
            if 'tag_municipios_ids' in vals:
                # Lo que está en el "después" pero no en el "antes" = Agregados
                ids_agregados = despues_ejecucion - antes['ejecucion']
                # Lo que está en el "antes" pero no en el "después" = Eliminados
                ids_eliminados = antes['ejecucion'] - despues_ejecucion

                if ids_agregados:
                    nombres = self.env['municipio'].browse(list(ids_agregados)).mapped('name')
                    mensajes.append(f"<li>🟢 <b>Agregado (Ejecución):</b> {', '.join(nombres)}</li>")
                if ids_eliminados:
                    nombres = self.env['municipio'].browse(list(ids_eliminados)).mapped('name')
                    mensajes.append(f"<li>🔴 <b>Removido (Ejecución):</b> {', '.join(nombres)}</li>")

            # --- Análisis de Municipio(s) Beneficiados ---
            if 'tag_municipios_ids2' in vals:
                ids_agregados_ben = despues_beneficiados - antes['beneficiados']
                ids_eliminados_ben = antes['beneficiados'] - despues_beneficiados

                if ids_agregados_ben:
                    nombres = self.env['municipio'].browse(list(ids_agregados_ben)).mapped('name')
                    mensajes.append(f"<li>🟢 <b>Agregado (Beneficiados):</b> {', '.join(nombres)}</li>")
                if ids_eliminados_ben:
                    nombres = self.env['municipio'].browse(list(ids_eliminados_ben)).mapped('name')
                    mensajes.append(f"<li>🔴 <b>Removido (Beneficiados):</b> {', '.join(nombres)}</li>")

            # 5. Publicar en el chatter si hubo cambios reales
            if mensajes:
                body = "<p><b>Actualización en Municipios:</b></p><ul>" + "".join(mensajes) + "</ul>"
                record.message_post(body=body)

        return res
    
    
    
    municipio_codigo = fields.Char(string="Código municipio", tracking=True)
    Cuantía5 = fields.Char(string="Cuantía (COP)", tracking=True)
    municipio_departamento2 = fields.Many2one('departamento', string="Departamento, municipio", tracking=True)
    municipio_departamento = fields.Char(string="Departamento, municipio", tracking=True)
    #Revicion Administrativa#
    revisar_tecnico_administrativa = fields.Char(string="Revisión técnico-administrativa", tracking=True)
    viable_Administrativa = fields.Selection([
        ('viable', 'Catalina Jimenez Cerquera'),
        ('No viable', 'Carmen Patricia Villareal Cano')
    ], string="Aprueba área administrativa", tracking=True)
    #Revicion juridica#
    revisar_juridica = fields.Char(string="Revisión jurídica")
    viable_Juridica = fields.Selection([
        ('viable', 'Jairo Candelo'),
        ('No viable', 'Designado del equipo Jurídico')
    ], string="Aprueba área jurídica", tracking=True)
    #Aprobacion Financiera#
    revisar_financiera = fields.Char(string="Revisión financiera")
    viable_Financiera = fields.Selection([
        ('viable', 'Diana Lorena Cano'),
    ], string="Aprueba área financiera", tracking=True)
    fecha_proyeccion = fields.Date(string="Fecha de proyección", tracking=True)
    fecha_revision = fields.Date(string="Fecha de revisión", tracking=True)
    #Aprobacion#
    aprueba = fields.Char(string="Aprueba", tracking=True)
    aprobado = fields.Selection([
        ('Aprobado', 'Carmen Patricia Villareal Cano'),
        ('No Aprobado', 'Marlon Gomez Jaramillo')
    ], string="Aprobación", tracking=True)
    fecha_aprobacion = fields.Date(string="Fecha de aprobación", tracking=True)

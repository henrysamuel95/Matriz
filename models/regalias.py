# models.py
from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime
from odoo.tools.misc import html_escape



class Regalias(models.Model):
    _name = 'regalias'
    _order = 'create_date desc'
    _description = 'Base de datos fuv semáforo'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'NOMBRE_DEL_CONTRATISTA2'

    def open_mail_activity(self):
        return {
            'name': 'Mail Activities',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'activity',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    currency_id = fields.Many2one('res.currency', string='Moneda')
    name = fields.Char(string='BPIN')
    NOMBRE_DEL_PROCESO = fields.Char(string='NOMBRE DEL CONTRATO')
    TIPO2 = fields.Many2one(comodel_name='tipo', string="TIPO")
    MODALIDAD_DE_SELECCION2 = fields.Many2one(comodel_name='modalidad', string="MODALIDAD DE SELECCIÓN")
    NOMBRE_DEL_CONTRATISTA2 = fields.Many2one(comodel_name='contratistas', string="NOMBRE DEL CONTRATISTA")
    CODIGO_SECOP_II = fields.Char(string='CODIGO SECOP II')
    NOMBRE_OBJETO_CONTRATO = fields.Char(string='NOMBRE/OBJETO CONTRATO')
    CODIGO_UNSPSC = fields.Char(string='CÓDIGO UNSPSC')
    NOMBRE_SUPERVISOR_INICIAL = fields.Char(string='NOMBRE SUPERVISOR INICIAL')
    FECHA_FIRMA_DEL_CONTRATO = fields.Date(string='FECHA FIRMA DEL CONTRATO')
    FECHA_DE_PUBLICACION_EN_EL_SECOP_II = fields.Date(string='FECHA DE PUBLICACIÓN EN EL SECOP II')
    VALOR_DEL_CONTRATO_VALOR_DEL_PROCESO = fields.Monetary(string='VALOR INICIAL DEL CONTRATO/PROCESO')
    VALOR_TOTAL_INCLUYE_ADICIONES = fields.Monetary(string='VALOR TOTAL - INCLUYE ADICIONES', related="valor2_id.TOTAL")
    CODIGO_CDP = fields.Char(string='CÓDIGO CDP')
    VALOR_CDP_INICIAL = fields.Monetary(string='VALOR CDP INICIAL')
    CODIGO_RPC = fields.Char(string='CÓDIGO RPC')
    VALOR_RPC_INICIAL = fields.Monetary(string='VALOR RPC INICIAL')
    PLAZO_DE_EJECUCION = fields.Char(string='PLAZO DE EJECUCIÓN')
    FECHA_INICIO_DEL_CONTRATO = fields.Date(string='FECHA INICIO DEL CONTRATO')
    FECHA_DE_SUSPENSION = fields.Date(string='FECHA DE SUSPENSIÓN', related="fecha_id.fecha_de_suspension")
    FECHA_DE_REINICIO = fields.Date(string='FECHA DE REINICIO', related="fecha_id.fecha_de_reinicio")
    FECHA_INICIAL_DE_TERMINACION_DEL_CTTO = fields.Date(string='FECHA INICIAL DE TERMINACIÓN DEL CTTO')

    fecha_actual = fields.Date(string='Fecha Actual', compute='_compute_fecha_actual')

    @api.depends('FECHA_DE_FINALIZACION_PLAZO_EJECUCION_DEL_CONTRATO')
    def _compute_fecha_actual(self):
        for record in self:
            record.fecha_actual = fields.Date.today()

    FECHA_DE_FINALIZACION_PLAZO_EJECUCION_DEL_CONTRATO = fields.Date(
        string='FECHA DE FINALIZACIÓN/PLAZO EJECUCIÓN DEL CONTRATO')
    DIFERENCIA_FECHAS = fields.Integer(string='PLAZO DE FINALIZACIÓN DEL CONTRATO', compute='_compute_diferencia_dias')

    @api.depends('FECHA_DE_FINALIZACION_PLAZO_EJECUCION_DEL_CONTRATO', 'fecha_actual')
    def _compute_diferencia_dias(self):
        for record in self:
            if record.FECHA_DE_FINALIZACION_PLAZO_EJECUCION_DEL_CONTRATO and record.fecha_actual:
                fecha_actual = fields.Date.from_string(record.fecha_actual)
                fecha_finalizacion = fields.Date.from_string(record.FECHA_DE_FINALIZACION_PLAZO_EJECUCION_DEL_CONTRATO)
                diferencia = (fecha_finalizacion - fecha_actual).days
                record.DIFERENCIA_FECHAS = diferencia

                if diferencia > 30:
                    record.SEMAFORO_1 = 'verde'
                elif 20 < diferencia <= 30:
                    record.SEMAFORO_1 = 'amarillo'
                elif 0 < diferencia <= 20:
                    record.SEMAFORO_1 = 'rojo'
                else:
                    record.SEMAFORO_1 = 'azul'
            else:
                record.DIFERENCIA_FECHAS = 0  # Asignar un valor predeterminado en caso de que alguna de las fechas no esté definida

    SEMAFORO_1 = fields.Selection([
        ('verde', 'Verde'),
        ('amarillo', 'Amarillo'),
        ('rojo', 'Rojo'),
        ('azul', 'Azul'),
    ], string='SEMÁFORO')

    alert_amarillo_sent = fields.Boolean(string='Alerta Amarillo Enviada', default=False)
    alert_rojo_sent = fields.Boolean(string='Alerta Rojo Enviada', default=False)
    alert_azul_sent = fields.Boolean(string='Alerta Azul Enviada', default=False)

    @api.model
    def post_import_update_alerts(self):
        records = self.search([])
        for record in records:
            record._compute_diferencia_dias()
            record._compute_semaforo_1_color()

            # Verifica el estado y el semáforo para gestionar las alertas
            if record.SEMAFORO_1 == 'amarillo' and not record.alert_amarillo_sent:
                record.email_actividades_SEMAFORO_2()
            elif record.SEMAFORO_1 == 'rojo' and not record.alert_rojo_sent:
                record.email_actividades_SEMAFORO_3()
            elif record.SEMAFORO_1 == 'azul' and not record.alert_azul_sent:
                record.email_actividades_SEMAFORO_4()

            # Gestión del cron job
            if record.SEMAFORO_1 == 'azul' and record.ESTADO != 'terminado':
                record._create_or_update_cron_job()
            elif record.ESTADO == 'terminado':
                record._remove_cron_job()

    semaforo_1_color = fields.Char(compute='_compute_semaforo_1_color', string='SEMÁFORO 1')

    def _compute_semaforo_1_color(self):
        for record in self:
            color = ''
            if record.SEMAFORO_1 == 'verde':
                color = '#00FF00'
            elif record.SEMAFORO_1 == 'amarillo':
                color = '#FFFF00'
            elif record.SEMAFORO_1 == 'rojo':
                color = '#F9AD0A'
            elif record.SEMAFORO_1 == 'azul':
                color = '#FF0000'
            record.semaforo_1_color = color

    FECHA_ACTA_DE_TERMINACION_Y_LIQUIDACION3 = fields.Date(string='FECHA DE ACTA DE TERMINACIÓN, LIQUIDACIÓN Y CIERRE')

    @api.model
    def create(self, vals):
        # Verificar la modalidad de selección
        if 'MODALIDAD_DE_SELECCION' in vals:
            modalidad_id = vals['MODALIDAD_DE_SELECCION']
            modalidad = self.env['modalidad'].browse(modalidad_id)
            if modalidad and modalidad.name == 'CONTRATACIÓN DIRECTA':
                vals['ESTADO'] = 'ejecucion'

        # Verificar si se proporcionó una fecha de inicio del contrato
        if 'FECHA_INICIO_DEL_CONTRATO' in vals:
            fecha_inicio = vals['FECHA_INICIO_DEL_CONTRATO']
            if fecha_inicio:
                year = datetime.strptime(fecha_inicio, '%Y-%m-%d').year
                if year < 2000:
                    vals['ESTADO'] = 'desierto'
                else:
                    vals['ESTADO'] = 'ejecucion'

        # Si se proporciona una fecha de terminación y liquidación, establecer el estado a "terminado"
        if 'FECHA_ACTA_DE_TERMINACION_Y_LIQUIDACION3' in vals and vals['FECHA_ACTA_DE_TERMINACION_Y_LIQUIDACION3']:
            vals['ESTADO'] = 'terminado'

        record = super(Regalias, self).create(vals)
        if vals.get('ESTADO') == 'terminado':
            record._remove_cron_job()
        return record

    def write(self, vals):
        # Verificar la modalidad de selección
        if 'MODALIDAD_DE_SELECCION' in vals:
            modalidad_id = vals['MODALIDAD_DE_SELECCION']
            modalidad = self.env['modalidad'].browse(modalidad_id)
            if modalidad and modalidad.name == 'CONTRATACIÓN DIRECTA':
                vals['ESTADO'] = 'ejecucion'

        # Verificar si se proporciona una fecha de inicio del contrato
        if 'FECHA_INICIO_DEL_CONTRATO' in vals and vals['FECHA_INICIO_DEL_CONTRATO']:
            fecha_inicio = vals['FECHA_INICIO_DEL_CONTRATO']
            year = datetime.strptime(fecha_inicio, '%Y-%m-%d').year
            if year < 2000:
                vals['ESTADO'] = 'desierto'
            else:
                vals['ESTADO'] = 'ejecucion'

        # Si se proporciona una fecha de terminación y liquidación, establecer el estado a "terminado"
        if 'FECHA_ACTA_DE_TERMINACION_Y_LIQUIDACION3' in vals and vals['FECHA_ACTA_DE_TERMINACION_Y_LIQUIDACION3']:
            vals['ESTADO'] = 'terminado'

        res = super(Regalias, self).write(vals)

        # Restaurar el cron job si el estado cambia de "suspendido" a otro estado
        if 'ESTADO' in vals:
            for record in self:
                if record.ESTADO in ['ejecucion', 'proceso']:
                    record._create_or_update_cron_job()
                elif record.ESTADO == 'terminado':
                    record._remove_cron_job()

        return res

    def unlink(self):
        for record in self:
            record._remove_cron_job()
        return super(Regalias, self).unlink()

    FECHA_ACTO_DE_CIERRE_DEL_PROYECTO2 = fields.Char(string='FECHA DE ACTO DE CIERRE DEL PROYECTO')
    DECRETO_248_21 = fields.Char(string='DECRETO 248/21')
    ACUERDO_DE_PAZ = fields.Char(string='ACUERDO DE PAZ')
    MATRIZ_DE_AVANCE_PUBLICADA = fields.Char(string='MATRIZ DE AVANCE/Publicada')
    MATRIZ_DE_AVANCE_DEL_50 = fields.Char(string='MATRIZ DE AVANCE DEL 50% Y 100%')
    LINK_SECOP_II = fields.Char(string='LINK SECOP II')

    OTROSI_NO_1_PRORROGA_ADICION_MODIFICACION2 = fields.Integer(string='OTROSÍ', related="otrosi_id.contador")
    FECHA_OTROSI_NO_1 = fields.Date(string='FECHA OTROSÍ No. 1')

    OTROSI_NO_2_PRORROGA_ADICION_MODIFICACION2 = fields.Char(string='OTROSÍ No. 2 PRÓRROGA / ADICIÓN / MODIFICACIÓN2')
    FECHA_OTROSI_NO_2 = fields.Date(string='FECHA OTROSÍ No. 2')

    OTROSI_NO_3_PRORROGA_ADICION_MODIFICACION = fields.Char(string='OTROSÍ No. 3 PRÓRROGA / ADICIÓN / MODIFICACIÓN')
    FECHA_OTROSI_NO_3 = fields.Date(string='FECHA OTROSÍ No. 3')

    ESTADO = fields.Selection(
        [('proceso', 'PROCESO PUBLICADO'), ('ejecucion', 'EJECUCIÓN'), ('suspendido', 'SUSPENDIDO'),
         ('desierto', 'DESIERTO'), ('terminado', 'CERRADO,TERMINADO Y LIQUIDADO')],
        string='ESTADO', default='proceso')
    OBSERVACIONES = fields.Char(string='OBSERVACIONES')
    PERSONA_ENCARGADA_FUV = fields.Char(string='PERSONA ENCARGADA - FUV')
    user_id = fields.Many2one('res.users', string='USUARIO ENCARGADO', default=lambda self: self.env.user,
                              domain=lambda self: [
                                  ('groups_id', 'in', self.env.ref('Matriz.group_contratacion_regalias').id)])

    @api.onchange('SEMAFORO_1')
    def trigger_email_actividades_regalias2(self):
        if self.SEMAFORO_1 == 'amarillo':
            self.email_actividades_SEMAFORO_2()

    def email_actividades_SEMAFORO_2(self):
        # Obtener la plantilla de correo electrónico por su referencia
        email_template = self.env.ref('Matriz.email_actividades_vencidas_SEMAFORO_2', raise_if_not_found=False)

        if not email_template:
            raise UserError("¡Plantilla de correo electrónico no encontrada!")

        # Obtener el usuario actual
        user = self.env.user

        # Obtener la dirección de correo electrónico del usuario
        recipient_email = user.email

        # Generar el correo electrónico a partir de la plantilla y los detalles del registro
        email_values = {
            'subject': email_template.subject,
            'body_html': email_template.body_html,
            'email_from': 'go@fundacionunivalle.org',
            # Reemplaza con la dirección de correo que deseas utilizar como remitente
            'email_to': recipient_email,
        }

        # Crear el registro del correo electrónico y enviarlo
        mail = self.env['mail.mail'].create(email_values)
        mail.send()

        # Verificar si el correo se envió correctamente
        if mail.state == 'sent':
            print("¡El correo se envió correctamente!")
            self.alert_amarillo_sent = True  # Marca la alerta como enviada
        else:
            print("¡Error al enviar el correo!")

        return True

    @api.onchange('SEMAFORO_1')
    def trigger_email_actividades_regalias3(self):
        if self.SEMAFORO_1 == 'rojo':
            self.email_actividades_SEMAFORO_3()

    def email_actividades_SEMAFORO_3(self):
        # Obtener la plantilla de correo electrónico por su referencia
        email_template = self.env.ref('Matriz.email_actividades_vencidas_SEMAFORO_3', raise_if_not_found=False)

        if not email_template:
            raise UserError("¡Plantilla de correo electrónico no encontrada!")

        # Obtener el usuario actual
        user = self.env.user

        # Obtener la dirección de correo electrónico del usuario
        recipient_email = user.email

        # Generar el correo electrónico a partir de la plantilla y los detalles del registro
        email_values = {
            'subject': email_template.subject,
            'body_html': email_template.body_html,
            'email_from': 'go@fundacionunivalle.org',
            # Reemplaza con la dirección de correo que deseas utilizar como remitente
            'email_to': recipient_email,
        }

        # Crear el registro del correo electrónico y enviarlo
        mail = self.env['mail.mail'].create(email_values)
        mail.send()

        # Verificar si el correo se envió correctamente
        if mail.state == 'sent':
            print("¡El correo se envió correctamente!")
            self.alert_rojo_sent = True  # Marca la alerta como enviada
        else:
            print("¡Error al enviar el correo!")

        return True



    @api.onchange('SEMAFORO_1')
    def trigger_email_actividades_regalias4(self):
        if self.SEMAFORO_1 == 'azul':
            self._create_or_update_cron_job()


    def email_actividades_SEMAFORO_4(self):
        # Obtener la plantilla de correo electrónico por su referencia
        email_template = self.env.ref('Matriz.email_actividades_vencidas_SEMAFORO_4', raise_if_not_found=False)

        if not email_template:
            raise UserError("¡Plantilla de correo electrónico no encontrada!")

        # Obtener el usuario actual
        user = self.env.user

        # Obtener la dirección de correo electrónico del usuario
        recipient_email = user.email

        # Generar el correo electrónico a partir de la plantilla y los detalles del registro
        email_values = {
            'subject': email_template.subject,
            'body_html': email_template.body_html,
            'email_from': 'go@fundacionunivalle.org',
            # Reemplaza con la dirección de correo que deseas utilizar como remitente
            'email_to': recipient_email,
        }

        # Crear el registro del correo electrónico y enviarlo
        mail = self.env['mail.mail'].create(email_values)
        mail.send()

        # Verificar si el correo se envió correctamente
        if mail.state == 'sent':
            print("¡El correo se envió correctamente!")
            self.alert_azul_sent = True  # Marca la alerta como enviada
        else:
            print("¡Error al enviar el correo!")

        return True

    @api.onchange('ESTADO')
    def _onchange_estado(self):
        for record in self:
            if record.ESTADO == 'terminado':
                record._remove_cron_job()
                # Actualizar el estado a 'finalizado' después de eliminar el cron job
                record.ESTADO = 'terminado'


    cron_job_id = fields.Many2one('ir.cron', string="Cron Job")

    def _create_or_update_cron_job(self):
        if not self.cron_job_id:
            cron_job = self.env['ir.cron'].sudo().create({
                'name': f"alerta {self.id}",
                'model_id': self.env['ir.model'].search([('model', '=', self._name)]).id,
                'state': 'code',
                'code': f"model.email_actividades_SEMAFORO_4()",
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

    def _remove_cron_job(self):
        if self.cron_job_id:
            self.cron_job_id.unlink()
            self.cron_job_id = False
            print("Cron job eliminado.")

    VALOR_TOTAL_INCLUYE_ADICIONES_HTML = fields.Html(
        string='VALOR TOTAL - INCLUYE ADICIONES',
        compute='_compute_valor_total_incluye_adiciones_html',
        sanitize=False
    )

    @api.depends('valor2_id.TOTAL')
    def _compute_valor_total_incluye_adiciones_html(self):
        for record in self:
            # Obtener el registro con el valor total más alto
            valor_total_record = record.valor2_id.sorted(key=lambda r: r.id, reverse=True)[:1]
            valor = valor_total_record.TOTAL if valor_total_record else 0.0

            # Generar el HTML con JavaScript correcto
            record.VALOR_TOTAL_INCLUYE_ADICIONES_HTML = f"""
                <div class="o_form_uri" style="cursor: pointer;"
                     onclick="odoo.__WOWL_DEBUG__.root.env.services.action.doAction({{
                         type: 'ir.actions.act_window',
                         res_model: 'valor_total_incluye_adiciones',
                         res_id: {valor_total_record.id if valor_total_record else 'false'},
                         name: 'VALOR TOTAL - INCLUYE ADICIONES',
                         target: 'new',
                         views: [[false, 'form']],
                         context: {{'default_valor_id': {record.id} }}
                     }})">
                    {html_escape(f"{valor:.2f}")}
                </div>
            """

    OTROSI_NO_1_PRORROGA_ADICION_MODIFICACION2_HTML = fields.Html(
        string='OTROSÍ',
        compute='_compute_contador_html',
        sanitize=False
    )

    @api.depends('otrosi_id.contador')
    def _compute_contador_html(self):
        for record in self:
            # Obtener el registro con el valor total más alto
            contador_record = record.otrosi_id.sorted(key=lambda r: r.id, reverse=True)[:1]
            valor = contador_record.contador if contador_record else 0

            # Generar el HTML con JavaScript correcto
            record.OTROSI_NO_1_PRORROGA_ADICION_MODIFICACION2_HTML = f"""
                <div class="o_form_uri" style="cursor: pointer;"
                     onclick="odoo.__WOWL_DEBUG__.root.env.services.action.doAction({{
                         type: 'ir.actions.act_window',
                         res_model: 'otrosi_no',
                         res_id: {contador_record.id if contador_record else 'false'},
                         name: 'OTROSÍ',
                         target: 'new',
                         views: [[false, 'form']],
                         context: {{'default_otrosi2_id': {record.id} }}
                     }})">
                    {html_escape(f"{valor:d}")}
                </div>
            """

    def valor_popup(self):
        ventana_record = self.env['valor_total_incluye_adiciones'].search([('valor_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'VALOR TOTAL - INCLUYE ADICIONES',
                'type': 'ir.actions.act_window',
                'res_model': 'valor_total_incluye_adiciones',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:

            ventana_record = self.env['valor_total_incluye_adiciones'].create({
                'valor_id': self.id,
            })


            return {
                'name': 'VALOR TOTAL - INCLUYE ADICIONES',
                'type': 'ir.actions.act_window',
                'res_model': 'valor_total_incluye_adiciones',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def fecha_popup(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['fecha_de_reinicio_y_suspension'].search([('fechas_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'FECHA DE REINICIO y FECHA DE SUSPENSIÓN',
                'type': 'ir.actions.act_window',
                'res_model': 'fecha_de_reinicio_y_suspension',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['fecha_de_reinicio_y_suspension'].create({
                'fechas_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'FECHA DE REINICIO y FECHA DE SUSPENSIÓN',
                'type': 'ir.actions.act_window',
                'res_model': 'fecha_de_reinicio_y_suspension',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def otrosi_popup(self):
        # Verificar si ya existe un registro de 'ventana' asociado a este registro de 'entregables_2'
        ventana_record = self.env['otrosi_no'].search([('otrosi2_id', '=', self.id)], limit=1)

        if ventana_record:
            # Abrir la ventana emergente para el registro existente de 'ventana'
            return {
                'name': 'MODAL, OTROSI Y LA FECHA',
                'type': 'ir.actions.act_window',
                'res_model': 'otrosi_no',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Crear un nuevo registro en el modelo 'ventana' con la relación Many2one a este registro de 'entregables_2'
            ventana_record = self.env['otrosi_no'].create({
                'otrosi2_id': self.id,  # Establecer la relación Many2one con este registro de 'entregables_2'
            })

            # Abrir la ventana emergente para el nuevo registro creado
            return {
                'name': 'MODAL, OTROSI Y LA FECHA',
                'type': 'ir.actions.act_window',
                'res_model': 'otrosi_no',
                'res_id': ventana_record.id,
                'view_mode': 'form',
                'target': 'new',
            }


    valor2_id = fields.One2many('valor_total_incluye_adiciones', 'valor_id', string="Relación")
    fecha_id = fields.One2many('fecha_de_reinicio_y_suspension', 'fechas_id', string="Relación")
    otrosi_id = fields.One2many('otrosi_no', 'otrosi2_id', string="Relación")


class ProyectosRegalias(models.Model):
    _name = 'proyectos_regalias'
    _order = 'create_date desc'
    _description = 'Base de datos contratistas proyectos regalías'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='BPIN')
    Proyecto = fields.Char(string='Proyecto')
    fecha_inicio_proyecto = fields.Date(string='Fecha inicio proyecto (acta de inicio)')
    fecha_terminacion_proyecto = fields.Date(string='Fecha Terminación proyecto')
    plazo = fields.Integer(string='Plazo')
    nombre = fields.Char(string='Nombre')
    cedula = fields.Char(string='Cédula')
    cargo = fields.Char(string='Cargo que desempeña (desde el presupuesto)')
    porcentaje_dedicacion = fields.Float(string='% de dedicación (desde el presupuesto)')
    profesional_regalias = fields.Many2one('res.users', string='Profesional Regalías',
                                           default=lambda self: self.env.user, domain=lambda
            self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_regalias').id)])
    observaciones = fields.Char(string='Observaciones')


class Valor_Total_Incluye_Adiciones(models.Model):
    _name = 'valor_total_incluye_adiciones'
    _description = 'VALOR TOTAL - INCLUYE ADICIONES'
    currency_id = fields.Many2one('res.currency', string='Moneda')

    valor_id = fields.Many2one('regalias', string="Relación")
    VALOR_DEL_CONTRATO_VALOR_DEL_PROCESO = fields.Monetary(string='VALOR DEL CONTRATO', related="valor_id.VALOR_DEL_CONTRATO_VALOR_DEL_PROCESO")
    TOTAL = fields.Monetary(string='TOTAL', compute='_compute_total', store=True)
    valor_ids = fields.One2many('valor_tabla', 'valor_tabla_id', string="Relación")
    total_tabla = fields.Monetary(string='Total Tabla', compute='_compute_total_tabla', store=True)

    @api.depends('valor_ids.VALOR_TOTAL_INCLUYE_ADICIONES')
    def _compute_total_tabla(self):
        for record in self:
            record.total_tabla = sum(line.VALOR_TOTAL_INCLUYE_ADICIONES for line in record.valor_ids)

    @api.depends('VALOR_DEL_CONTRATO_VALOR_DEL_PROCESO', 'total_tabla', 'valor_ids')
    def _compute_total(self):
        for record in self:
            if record.valor_ids:
                # Si hay registros en valor_ids, realiza la suma
                record.TOTAL = (record.VALOR_DEL_CONTRATO_VALOR_DEL_PROCESO or 0.0) + record.total_tabla
            else:
                # Si no hay registros en valor_ids, TOTAL es 0.0
                record.TOTAL = 0.0

    def action_save_and_reload(self):
        """Guardar el registro y recargar la vista actual."""
        self.ensure_one()  # Asegúrate de que solo un registro esté siendo guardado
        self.write({})  # Guarda el registro actual
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

class Valor_tabla(models.Model):
    _name = 'valor_tabla'
    _description = 'VALOR TOTAL - INCLUYE ADICIONES'
    currency_id = fields.Many2one('res.currency', string='Moneda')
    VALOR_TOTAL_INCLUYE_ADICIONES = fields.Monetary(string='VALOR TOTAL - INCLUYE ADICIONES')
    valor_tabla_id = fields.Many2one('valor_total_incluye_adiciones', string="Relación")


class Option(models.Model):
    _name = 'option'
    _description = 'Menu'


class Fecha_de_reinicio_y_suspension(models.Model):
    _name = 'fecha_de_reinicio_y_suspension'
    _description = 'FECHA DE REINICIO y FECHA DE SUSPENSIÓN'

    fechas_id = fields.Many2one('regalias', string="Relación")
    fecha_tabla_ids = fields.One2many('fecha_tabla', 'fechas_tabla_id', string="Relación")
    fecha_de_suspension = fields.Date(string='Fecha de Suspensión', compute='_compute_fecha_de_suspension', store=True)
    fecha_de_reinicio = fields.Date(string='Fecha de Reinicio', compute='_compute_fecha_de_reinicio', store=True)

    @api.depends('fecha_tabla_ids.fecha_de_suspension')
    def _compute_fecha_de_suspension(self):
        for record in self:
            fechas_tabla = record.fecha_tabla_ids.filtered(lambda f: f.fecha_de_suspension).sorted(key=lambda r: r.fecha_de_suspension, reverse=True)
            record.fecha_de_suspension = fechas_tabla[0].fecha_de_suspension if fechas_tabla else False

    @api.depends('fecha_tabla_ids.fecha_de_reinicio')
    def _compute_fecha_de_reinicio(self):
        for record in self:
            fechas_tabla = record.fecha_tabla_ids.filtered(lambda f: f.fecha_de_reinicio).sorted(key=lambda r: r.fecha_de_reinicio, reverse=True)
            record.fecha_de_reinicio = fechas_tabla[0].fecha_de_reinicio if fechas_tabla else False



class Fecha_tabla(models.Model):
    _name = 'fecha_tabla'
    _description = 'FECHA DE REINICIO y FECHA DE SUSPENSIÓN'

    fechas_tabla_id = fields.Many2one('fecha_de_reinicio_y_suspension', string="Relación")
    fecha_de_suspension = fields.Date(string='FECHA DE SUSPENSIÓN')
    fecha_de_reinicio = fields.Date(string='FECHA DE REINICIO')

    @api.model
    def create(self, vals):
        record = super(Fecha_tabla, self).create(vals)
        if vals.get('fecha_de_suspension'):
            record._update_regalias_estado()
        return record

    def write(self, vals):
        res = super(Fecha_tabla, self).write(vals)
        if 'fecha_de_suspension' in vals:
            self._update_regalias_estado()
        return res

    def _update_regalias_estado(self):
        for record in self:
            if record.fecha_de_suspension:
                regalias = record.fechas_tabla_id.fechas_id
                if regalias:
                    regalias.write({'ESTADO': 'suspendido'})
                    regalias._remove_cron_job()


class OTROSÍ(models.Model):
    _name = 'otrosi_no'
    _description = 'MODAL, OTROSI Y LA FECHA'

    otrosi2_id = fields.Many2one('regalias', string="Relación")
    otrosi_tabla_ids = fields.One2many('otrosi_tabla', 'otrosi_id', string="Relación")
    contador = fields.Integer(string="OTROSÍ", compute='_compute_contador', store=True)

    @api.depends('otrosi_tabla_ids')
    def _compute_contador(self):
        for record in self:
            # Buscar el último registro en la tabla relacionada
            last_entry = self.env['otrosi_tabla'].search(
                [('otrosi_id', '=', record.id)],
                limit=1,
                order='id desc'
            )
            record.contador = last_entry.contador if last_entry else 0

    def action_save_and_reload(self):
        """Guardar el registro y recargar la vista actual."""
        self.ensure_one()  # Asegúrate de que solo un registro esté siendo guardado
        self.write({})  # Guarda el registro actual
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }


class OTROSÍ_TABLA(models.Model):
    _name = 'otrosi_tabla'
    _description = 'MODAL, OTROSI Y LA FECHA'

    otrosi_id = fields.Many2one('otrosi_no', string="Relación con OTROSI")
    contador = fields.Integer(string="OTROSÍ", compute='_compute_contador', readonly=True)
    tipos_combinaciones = fields.Many2one(comodel_name='tipo_combinaciones', string="TIPO COMBINACIONES")
    fecha = fields.Date(string="FECHA")

    @api.depends('otrosi_id')
    def _compute_contador(self):
        for record in self:
            # Cambia el dominio según tu lógica si el contador debe ser único por `otrosi_id`
            max_counter = self.search([('otrosi_id', '=', record.otrosi_id.id)], limit=1, order='contador desc')
            record.contador = (max_counter.contador if max_counter else 0) + 1


class Tipo_combinacion(models.Model):
    _name = 'tipo_combinaciones'
    _description = 'TIPO COMBINACIONES'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="TIPO COMBINACIONES")




class Tipo(models.Model):
    _name = 'tipo'
    _description = 'TIPO'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="TIPO")


class Modalidad_seleccion(models.Model):
    _name = 'modalidad'
    _description = 'MODALIDAD DE SELECCIÓN'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="MODALIDAD DE SELECCIÓN")


class Contratista(models.Model):
    _name = 'contratistas'
    _description = 'NOMBRE DEL CONTRATISTA'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="NOMBRE DEL CONTRATISTA")
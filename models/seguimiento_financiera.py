# -*- coding: utf-8 -*-
from calendar import monthrange
from odoo import models, fields, api
from datetime import date
from datetime import timedelta
import logging
import unicodedata
_logger = logging.getLogger(__name__)


class SeguimientoFinanciera(models.Model):
    _name = 'seguimiento_financiera'
    _description = 'Seguimiento Financiera'
    _order = 'nombre_mes desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'nombre_mes_display'
    responsables_ids = fields.Many2many('res.users', string='Responsables')
    fecha_cronograma = fields.Date(string='Fecha cronograma')

    def enviar_alertas_vencimiento(self):
        hoy = date.today()
        ayer = hoy - timedelta(days=1)
        _logger.info("=== ALERTA VENCIMIENTO ===")
        _logger.info(f"Hoy: {hoy} | Ayer: {ayer}")

        # Buscar líneas vencidas y pendientes
        lineas = self.env['seguimiento_financiera_linea'].search([
            ('fecha_cronograma', '<=', ayer),
            ('estado', '=', 'pendiente')
        ])

        _logger.info(f"Total líneas encontradas: {len(lineas)}")

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        for linea in lineas:
            _logger.info(f"→ Actividad: {linea.actividad} | Fecha: {linea.fecha_cronograma}")

            # ✅ Si la línea tiene registro padre, apuntamos a ese formulario
            if linea.seguimiento_id:
                record_url = (
                    f"{base_url}/web#id={linea.seguimiento_id.id}"
                    f"&model=seguimiento_financiera&view_type=form"
                )
            else:
                # fallback por si no tiene seguimiento asociado
                record_url = (
                    f"{base_url}/web#id={linea.id}"
                    f"&model=seguimiento_financiera_linea&view_type=form"
                )

            for usuario in linea.responsables_ids:
                if not usuario.email:
                    _logger.warning(f"⚠️ Usuario sin correo: {usuario.name}")
                    continue

                subject = f"⚠️ Actividad vencida - {linea.actividad}"
                body_html = f"""
                    <div style="font-family: Arial, sans-serif; color: #333;">
                        <h2 style="color:#dc2626;">⚠️ Actividad vencida</h2>
                        <p>Hola <b>{usuario.name}</b>,</p>
                        <p>La actividad <b>{linea.actividad}</b> programada para el
                        <b>{linea.fecha_cronograma.strftime('%d/%m/%Y')}</b> sigue pendiente.</p>

                        <p style="margin-top:15px;">Por favor ingresa al siguiente enlace para revisarla:</p>

                        <p>
                            <a href="{record_url}" style="
                                display:inline-block;
                                padding:10px 20px;
                                background-color:#007BFF;
                                color:white;
                                text-decoration:none;
                                border-radius:6px;
                                font-weight:bold;">
                                Ver registro del mes
                            </a>
                        </p>

                        <hr/>
                        <p style="font-size:12px;color:#6b7280;">
                            Notificación automática del sistema de Seguimiento Financiera
                        </p>
                    </div>
                """
                mail = self.env['mail.mail'].sudo().create({
                    'subject': subject,
                    'body_html': body_html,
                    'email_to': usuario.email,
                    'email_from': self.env.user.email or 'notificaciones@fundacionunivalle.org',
                    'auto_delete': True,  # opcional, borra tras envío
                })
                mail.send()
                _logger.info(f"✉️ Correo enviado a: {usuario.name} <{usuario.email}>")

        _logger.info("=== FIN ALERTA VENCIMIENTO ===")







        

    responsables_ids = fields.Many2many(
        'res.users',
        string='Responsables',
        help='Usuarios responsables de este seguimiento que recibirán los correos de alerta.'
    )

    # Campos nuevos
    fecha_vencimiento = fields.Date(string='Fecha de vencimiento')  # Fecha de vencimiento principal
    fecha_notif_previa = fields.Date(string='Notificar (2 días antes)', compute='_compute_fechas_notificacion', store=True)  # Fecha de notificación 2 días antes
    fecha_notif_post_5 = fields.Date(string='Notificar (5 días después)', compute='_compute_fechas_notificacion', store=True)  # Fecha de notificación 5 días después
    fecha_notif_post_8 = fields.Date(string='Notificar (8 días después)', compute='_compute_fechas_notificacion', store=True)  # Fecha de notificación 8 días después

    @api.depends('fecha_vencimiento')  # Depende de la fecha de vencimiento
    def _compute_fechas_notificacion(self):  # Calcula las fechas de notificación
        for record in self:  # Recorre cada registro
            if record.fecha_vencimiento:  # Si hay fecha de vencimiento
                venc = fields.Date.from_string(record.fecha_vencimiento)  # Convierte la fecha a tipo date
                record.fecha_notif_previa = venc - timedelta(days=2)  # Calcula 2 días antes
                record.fecha_notif_post_5 = venc + timedelta(days=5)  # Calcula 5 días después
                record.fecha_notif_post_8 = venc + timedelta(days=8)  # Calcula 8 días después
            else:  # Si no hay fecha
                record.fecha_notif_previa = False  # No hay notificación 2 días antes
                record.fecha_notif_post_5 = False  # No hay notificación 5 días después
                record.fecha_notif_post_8 = False  # No hay notificación 8 días después

    @api.model
    def cron_check_and_send_notifications(self):
        _logger.info("🔄 INICIANDO CRON: Notificar CIERRE FINANCIERA")

        hoy = fields.Date.today()
        registros = self.search([])

        for record in registros:
            if not record.fecha_vencimiento:
                continue
            if record.estado == 'cerrado':  # <-- Omitir registros completados
                print(f"ℹ️ Registro {record.nombre_mes} ya está cerrado. Omitiendo notificación.")
                continue

            if record.fecha_notif_previa == hoy:
                _logger.info(f"📧 Notificando 2 días antes a: {record.nombre_mes}")
                record._send_alert_email(2)
            elif record.fecha_notif_post_5 == hoy:
                _logger.info(f"⚠️ Notificando 5 días después a: {record.nombre_mes}")
                record._send_alert_email(5)
            elif record.fecha_notif_post_8 == hoy:
                _logger.info(f"⚠️ Notificando 8 días después a: {record.nombre_mes}")
                record._send_alert_email(8)

        _logger.info("✅ FINALIZADO CRON: Notificar CIERRE FINANCIERA")




    def _send_alert_email(self, dias):
        """Envía alertas por correo a los responsables asignados con botón al registro y actividades pendientes."""
        mail_template = self.env['mail.mail']
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        for record in self:
            # Obtener todos los responsables (del registro y de las líneas)
            responsables = record.responsables_ids | record.linea_ids.mapped('responsables_ids')
            if not responsables:
                _logger.warning(f"❌ El registro {record.nombre_mes_display} no tiene responsables asignados.")
                continue

            # Determinar colores y textos según los días
            if dias == 2:
                subject_base = f"⏰ Recordatorio: Actividad próxima a vencer ({record.nombre_mes_display})"
                color_title = "#02225B"
                titulo = "📅 Recordatorio de Actividad"
            else:
                subject_base = f"⚠️ Alerta: Actividad vencida hace {dias} días ({record.nombre_mes_display})"
                color_title = "#dc2626"
                titulo = "⚠️ Actividad Vencida"

            # URL del registro
            record_url = f"{base_url}/web#id={record.id}&model=seguimiento_financiera&view_type=form"

            for user in responsables:
                if not user.email:
                    _logger.warning(f"⚠️ El usuario {user.name} no tiene correo configurado.")
                    continue

                # Filtrar solo las actividades donde el usuario sea responsable
                actividades_user = [
                    linea.actividad
                    for linea in record.linea_ids
                    if (user in linea.responsables_ids) and not linea.aprobado
                ]

                if not actividades_user:
                    _logger.info(f"ℹ️ {user.name} no tiene actividades pendientes en {record.nombre_mes_display}.")
                    continue

                # Crear lista HTML de actividades
                lista_actividades = (
                    "<ul>" + "".join(f"<li>{act}</li>" for act in actividades_user) + "</ul>"
                )

                # Botón
                boton_html = f"""
                <p>
                    <a href="{record_url}" style="
                        display:inline-block;
                        padding:10px 20px;
                        background-color:{color_title};
                        color:#fff;
                        text-decoration:none;
                        border-radius:6px;
                        font-weight:bold;">
                        Ver registro
                    </a>
                </p>
                """

                # Cuerpo del correo
                body_html = f"""
                <div style="font-family: Arial, sans-serif; color: #333;">
                    <h2 style="color:{color_title};">{titulo}</h2>
                    <p>Hola <b>{user.name}</b>,</p>
                    <p>Estas son tus actividades pendientes del mes de 
                    <strong>{record.nombre_mes_display}</strong>:</p>
                    {lista_actividades}
                    <p style="margin-top:15px;">📅 <b>Fecha de vencimiento:</b> {record.fecha_vencimiento or 'No definida'}</p>
                    {boton_html}
                    <p>Gracias por tu atención.</p>
                    <br/>
                    <p style="font-size:12px;color:#6b7280;">Sistema de Seguimiento Financiero - FUV</p>
                </div>
                """

                # Crear y enviar correo individual
                mail_values = {
                    'subject': subject_base,
                    'body_html': body_html,
                    'email_to': user.email,
                    'email_from': self.env.user.email or 'no-reply@fuv.org',
                }
                try:
                    mail = mail_template.create(mail_values)
                    mail.send()
                    _logger.info(f"✅ Correo enviado a {user.name} ({user.email}) | {len(actividades_user)} actividades | Días: {dias}")
                except Exception as e:
                    _logger.error(f"❌ Error al enviar correo a {user.name}: {e}")



            
    # ----------------------------------------------------------
    # Nombre visible del mes
    # ----------------------------------------------------------
    nombre_mes_display = fields.Char(
        string='Mes (nombre)',
        compute='_compute_nombre_mes_display',
        store=False
    )

    @api.depends('nombre_mes')
    def _compute_nombre_mes_display(self):
        for rec in self:
            meses = dict(self._fields['nombre_mes'].selection)
            rec.nombre_mes_display = meses.get(rec.nombre_mes, '')

    # ----------------------------------------------------------
    # Botones
    # ----------------------------------------------------------
    def marcar_todas_completas(self):
        for rec in self:
            rec.linea_ids.write({
                'aprobado': True,
                'estado': 'completo',
                'fecha_aprobado': date.today(),
            })
            rec._compute_estado()
            rec._compute_trazabilidad_resumen()
            rec._compute_porcentaje_avance()

            nombre_mes = dict(self._fields['nombre_mes'].selection).get(rec.nombre_mes, rec.nombre_mes)
            rec.message_post(
                body=f"✅ Se marcaron <b>todas las actividades</b> como completas para el mes de <b>{nombre_mes}</b>."
            )

    def action_ver_resumen(self):
        """Abrir resumen HTML en popup (modal)."""
        self.ensure_one()
        view = self.env.ref('Matriz.view_seguimiento_financiera_resumen_form')
        return {
            'type': 'ir.actions.act_window',
            'name': f'Resumen - {self.nombre_mes_display}',
            'res_model': 'seguimiento_financiera',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'view_id': view.id,
            'context': {'show_only_resumen': True},
        }

    # ----------------------------------------------------------
    # Campos principales
    # ----------------------------------------------------------
    nombre_mes = fields.Selection([
        ('01', 'Enero'), ('02', 'Febrero'), ('03', 'Marzo'),
        ('04', 'Abril'), ('05', 'Mayo'), ('06', 'Junio'),
        ('07', 'Julio'), ('08', 'Agosto'), ('09', 'Septiembre'),
        ('10', 'Octubre'), ('11', 'Noviembre'), ('12', 'Diciembre'),
    ], string='Mes', required=True, tracking=True)

    anio = fields.Char(string='Año', compute='_compute_anio', store=True)

    @api.depends('fecha_vencimiento')
    def _compute_anio(self):
        for rec in self:
            rec.anio = rec.fecha_vencimiento.year if rec.fecha_vencimiento else ''



    estado = fields.Selection([
        ('pendiente', 'Pendiente'),
        ('proceso', 'En curso'),
        ('alerta', 'Alerta'),
        ('cerrado', 'Completado'),
    ], string='Estado', default='pendiente', compute='_compute_estado', store=True, tracking=True)

    linea_ids = fields.One2many('seguimiento_financiera_linea', 'seguimiento_id', string='Actividades', tracking=True)

    # Resumen HTML
    trazabilidad_resumen = fields.Html(
        string='Resumen de trazabilidad',
        compute='_compute_trazabilidad_resumen',
        sanitize=False
    )

    # Porcentaje de avance (aprobadas / total * 100)
    porcentaje_avance = fields.Float(
        string='Avance (%)',
        compute='_compute_porcentaje_avance',
        store=True,
        digits=(6, 2),
        help='Porcentaje de actividades completadas.'
    )

    # ----------------------------------------------------------
    # Cálculos automáticos
    # ----------------------------------------------------------
    @api.depends('linea_ids.estado', 'linea_ids.aprobado')
    def _compute_porcentaje_avance(self):
        for rec in self:
            total = len(rec.linea_ids)
            if not total:
                rec.porcentaje_avance = 0.0
            else:
                completas = sum(1 for l in rec.linea_ids if l.aprobado)
                rec.porcentaje_avance = (completas / total) * 100

    @api.depends(
        'linea_ids', 'linea_ids.aprobado', 'linea_ids.estado',
        'linea_ids.observaciones_financiera', 'linea_ids.observacion_final', 'estado'
    )
    def _compute_trazabilidad_resumen(self):
        """Genera resumen HTML con color según estado y observaciones por línea."""
        for rec in self:
            partes = []
            total = len(rec.linea_ids)
            completas = sum(1 for l in rec.linea_ids if l.estado == 'completo')
            porcentaje = round((completas / total) * 100, 1) if total else 0.0

            colores_estado = {
                'cerrado': '#22c55e',   # verde
                'proceso': '#3b82f6',   # azul
                'pendiente': '#fbbf24', # amarillo
                'alerta': '#ef4444',    # rojo
            }
            color_estado = colores_estado.get(rec.estado, '#9ca3af')
            texto_estado = dict(self._fields['estado'].selection).get(rec.estado, '').capitalize()

            # Cabecera con estado y porcentaje
            partes.append(f"""
                <div style="margin-bottom:14px; padding:10px; border-radius:10px; background:{color_estado}20;">
                    <h4 style="margin:0; color:{color_estado}; font-weight:700;">
                        Estado general del mes: {texto_estado}
                    </h4>
                    <p style="margin:4px 0 0 0; font-weight:bold;">
                        ✅ {completas} / {total} actividades completadas ({porcentaje}%)
                    </p>
                </div>
            """)

            # Detalle por línea
            for linea in rec.linea_ids:
                color_linea = "#22c55e" if linea.estado == "completo" else "#fbbf24"
                partes.append(f"""
                    <div style="margin-bottom:8px; padding:6px; border-left:3px solid {color_linea};">
                        <b>{linea.semana or ''}:</b> {linea.actividad or ''}<br/>
                        <i>Estado:</i> <span style="color:{color_linea};">{linea.estado}</span> |
                        <i>Aprobado:</i> {'✅' if linea.aprobado else '❌'}<br/>
                """)
                if linea.observaciones_financiera:
                    partes.append(f"<i>Observación financiera:</i> {linea.observaciones_financiera}<br/>")
                if linea.observacion_final:
                    partes.append(f"<i>Observación final:</i> {linea.observacion_final}<br/>")
                partes.append("</div>")

            rec.trazabilidad_resumen = "".join(partes) or "<p>No hay trazabilidad registrada.</p>"

    # ----------------------------------------------------------
    # Generar líneas automáticas (TODAS las actividades originales)
    # ----------------------------------------------------------
    def generar_lineas_por_semana(self):
        semanas = {
            'Semana 1': [
                'DC 1. C.Tributario.revisión listados corrección registros',
                'DC 1. C.Congruencia. Corrección registros',
                'DC 1. C.Tesoreria.verifica completitud registros por últimos movimientos realizados',
                'DC 1. C.Revisión. Saldos cartera',
                'DC 1. C.Depuración informes. Actualizar listado cctos para reunión',
                'DC 1. C.Causación.Sin imptos ni retenciones, reclasificaciones correcciones',
                'DC 1. C.Causación.depreciación amortización deterioro',
                'DC 1. C.Reunión.coordinación',
                'DC 2. C.Tributario.liquidación inicia',
                'DC 2. C.Tesoreria. Soportes a gestión documental. prioriza proyectos',
                'DC 2. C.Informe 5to día. Legaliza IVA Unialle.inicia',
                'DC 2. C.Causación.Costo operativo. Compara con presupuesto informa para revisión',
                'DC 2. C.Causación.Avance. Informe gestión solicitar',
                'DC 2. C.Causación.Avance. Comprara avance niif',
                'DC 2. C.Nomina.revisa saldo por pagar de todas las cuentas de nómina',
                'DC 3. C.Presupuesto. Informa ajustes y se registran',
                'DC 3. C.Causación.Avance. Define avance niif y registro',
                'DC 3. C.Tributario.Envio a BKF inicia',
                'DC 3. C.Conciliación bancos.entrega extracto incluido tarjetas crédito',
                'DC 3. C.Informe 5to día. Legaliza IVA Unialle.termina',
                'DC 3. C.Ventanilla.listado de pendientes facturas',
                'DC 4. C.Conciliación.conciliar bancos y tarjetas 1 día',
            ],
            'Semana 2': [
                'DC 5. C.Conciliación.verifica completitud',
                'DC 5. C.Depuración. Revisión cuentas registro notas',
                'DC 6. C.Cierre cuentas de orden',
                'DC 7. C.Informe.Balance.auditar para EF estados financieros',
                'DC 7. C.Informe.Proyectos.vigentes',
                'DC 8. C.Informe.Balance.entregar reporte a Directora',
                'DC 9. C.Informe.Balance.entregar notas de revelación',
            ],
            'Semana 3': [
                '** Informes de proyectos viejos',
                'DF 11. C.Gestión documental.Organizar y transferir',
                'DF 11. C.Procedimientos.Revisar actualizar',
                'DF 12. C.Reciproca.rendición enviar',
                'DF 13. C.Reciproca.reunion externa',
            ],
            'Semana 4': [
                'D 18. C.SGR.Pagos',
                'D 18. C.Causación.fijos',
                'D 18. C.Ventas.Aceptación factura y notas crédito. Confirmar',
                'D 18. C.Tesorería. verifica completitud registros previo a finales',
                'D 18. C.Nomina.novedades',
                'D 19. C. Cruce legalización y reembolso',
                'D 19. C.Causación.compras tarjetas crédito',
                'D 19. C.Nomina.validar prenómina realiza coordinador y recursos humanos',
                'D 20. C.Causación. Registros con impuestos y retenciones finaliza',
                'D 20. C.Tesorería. verifica completitud recaudos para sacar cartera',
                'D 20. C.Causación.provisión gasto del mes Impto ICA, intereses, estampilla',
                'D 20. C.Nomina.liquidación definitiva nómina seg.social , interfase,conciliación, DIAN',
            ],
        }

        lineas = []
        for semana, acts in semanas.items():
            for act in acts:
                lineas.append((0, 0, {'semana': semana, 'actividad': act, 'linea_base': True}))
        return lineas

    # Busca el cierre financiero del mes anterior, incluyendo el cambio de enero a diciembre del año previo.
    def _get_mes_anterior(self, nombre_mes, fecha_vencimiento):
        if not nombre_mes or not fecha_vencimiento:
            return self.env['seguimiento_financiera']

        mes = int(nombre_mes)
        anio = fecha_vencimiento.year
        if mes == 1:
            mes_anterior = '12'
            anio_anterior = anio - 1
        else:
            mes_anterior = str(mes - 1).zfill(2)
            anio_anterior = anio

        return self.search([
            ('nombre_mes', '=', mes_anterior),
            ('anio', '=', str(anio_anterior)),
        ], limit=1)

    # Copia al nuevo mes las tareas marcadas como "Conservar próximo mes", sin duplicar tareas base.
    def _agregar_lineas_conservadas(self, lineas, nombre_mes, fecha_vencimiento):
        mes_anterior = self._get_mes_anterior(nombre_mes, fecha_vencimiento)
        if not mes_anterior:
            return lineas

        actividades_existentes = {
            vals.get('actividad')
            for comando, _, vals in lineas
            if comando == 0 and vals.get('actividad')
        }
        for linea in mes_anterior.linea_ids.filtered('conservar_siguiente_mes'):
            if linea.actividad in actividades_existentes:
                continue
            lineas.append((0, 0, {
                'semana': linea.semana,
                'actividad': linea.actividad,
                'responsables_ids': [(6, 0, linea.responsables_ids.ids)],
                'fecha_cronograma': False,
                'estado': 'pendiente',
                'aprobado': False,
                'fecha_aprobado': False,
                'fecha_realizado': False,
                'observaciones_financiera': False,
                'observacion_final': False,
                'linea_base': False,
                'conservar_siguiente_mes': False,
            }))
            actividades_existentes.add(linea.actividad)
        return lineas

    @api.onchange('nombre_mes')
    def _onchange_nombre_mes(self):
        if self.nombre_mes:
            lineas = self.generar_lineas_por_semana()
            self.linea_ids = [(5, 0, 0)] + self._agregar_lineas_conservadas(
                lineas,
                self.nombre_mes,
                self.fecha_vencimiento or date.today(),
            )


    @api.model
    def create(self, vals):
        # Si se selecciona un mes y no se proporcionó fecha_vencimiento
        if vals.get('nombre_mes') and not vals.get('fecha_vencimiento'):
            anio = date.today().year  # O el año que quieras usar
            mes = int(vals['nombre_mes'])
            ultimo_dia = monthrange(anio, mes)[1]  # Calcula último día del mes
            vals['fecha_vencimiento'] = date(anio, mes, ultimo_dia)

        record = super().create(vals)

        # Genera líneas de seguimiento
        if vals.get('nombre_mes'):
            lineas = record.generar_lineas_por_semana()
            record.linea_ids = [(5, 0, 0)] + record._agregar_lineas_conservadas(
                lineas,
                record.nombre_mes,
                record.fecha_vencimiento,
            )

        record._compute_estado()
        record._compute_porcentaje_avance()
        return record


    # ----------------------------------------------------------
    # Estado del mes
    # ----------------------------------------------------------
    @api.depends('linea_ids.aprobado')
    def _compute_estado(self):
        for rec in self:
            if not rec.linea_ids:
                rec.estado = 'pendiente'
                continue
            mes_actual = str(date.today().month).zfill(2)
            total = len(rec.linea_ids)
            aprobadas = sum(1 for l in rec.linea_ids if l.aprobado)
            if aprobadas == total:
                rec.estado = 'cerrado'
            elif mes_actual > rec.nombre_mes:
                rec.estado = 'alerta'
            elif aprobadas == 0:
                rec.estado = 'pendiente'
            else:
                rec.estado = 'proceso'


class SeguimientoFinancieraLinea(models.Model):
    _name = 'seguimiento_financiera_linea'
    _description = 'Líneas de seguimiento financiera'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    puede_supervisar_financiera = fields.Boolean(
        string='Puede supervisar financiera',
        compute='_compute_puede_supervisar_financiera',
        store=False,
    )

    @api.depends_context('uid')
    # Determina si el usuario actual puede editar campos reservados al supervisor financiero.
    def _compute_puede_supervisar_financiera(self):
        puede_supervisar = self.env.user.has_group('Matriz.group_supervisor_actividades_financieras')
        for rec in self:
            rec.puede_supervisar_financiera = puede_supervisar

    responsables_ids = fields.Many2many(
        'res.users',
        string='Responsables',
        help='Usuarios responsables de este seguimiento que recibirán los correos de alerta.'
    )

    
    # NUEVO CAMPO: Porcentaje individual de la línea
    avance_linea = fields.Float(
        string='Avance Línea (%)',
        compute='_compute_avance_linea',
        store=True
    )
    
    @api.depends('aprobado', 'estado')
    def _compute_avance_linea(self):
        for rec in self:
            # Si se marca como aprobado o completo, el avance de esta línea es 100%
            if rec.aprobado or rec.estado == 'completo':
                rec.avance_linea = 100.0
            else:
                rec.avance_linea = 0.0
                
    fecha_cronograma = fields.Date(string='Fecha cronograma')
    aprobado = fields.Boolean('Aprobado', tracking=True)
    linea_base = fields.Boolean('Línea base', default=False, tracking=True)
    conservar_siguiente_mes = fields.Boolean('Conservar próximo mes', tracking=True)
    semana = fields.Char('Semana', tracking=True)
    actividad = fields.Char('Actividad de cierre', tracking=True)
    fecha_aprobado = fields.Date('Fecha de aprobación', tracking=True)
    fecha_realizado = fields.Date('Fecha realizado', tracking=True)
    observaciones_financiera = fields.Text('Observaciones financiera', tracking=True)
    observacion_final = fields.Text('Observación supervisora', tracking=True)
    seguimiento_id = fields.Many2one('seguimiento_financiera', string='Mes', ondelete='cascade', tracking=True)
    estado = fields.Selection([
        ('pendiente', 'Pendiente'),
        ('completo', 'Completo'),
    ], string='Estado', default='pendiente', tracking=True)

    @api.model
    # Normaliza texto para búsquedas: quita tildes, convierte a minúsculas y limpia espacios.
    def _normalizar_filtro_seguimiento(self, value):
        value = str(value or '')
        value = unicodedata.normalize('NFD', value)
        value = ''.join(char for char in value if unicodedata.category(char) != 'Mn')
        return value.lower().strip()

    @api.model
    # Calcula total, completadas y porcentaje usando todas las líneas del mes, no solo la página visible.
    def obtener_filtro_resumen(self, seguimiento_id, busqueda='', responsable=''):
        seguimiento_id = int(seguimiento_id or 0)
        if not seguimiento_id:
            return {
                'responsables': [],
                'total': 0,
                'completadas': 0,
                'porcentaje': 0,
            }

        query = self._normalizar_filtro_seguimiento(busqueda)
        responsable_query = self._normalizar_filtro_seguimiento(responsable)
        lineas = self.search([('seguimiento_id', '=', seguimiento_id)])
        responsables_disponibles = set()
        filtradas = self.browse()

        for linea in lineas:
            responsables_texto = ' '.join(linea.responsables_ids.mapped('display_name'))
            responsables_normalizado = self._normalizar_filtro_seguimiento(responsables_texto)
            for nombre in linea.responsables_ids.mapped('display_name'):
                if nombre:
                    responsables_disponibles.add(nombre)

            texto_linea = self._normalizar_filtro_seguimiento(' '.join([
                linea.semana or '',
                linea.actividad or '',
                dict(linea._fields['estado'].selection).get(linea.estado, linea.estado or ''),
                str(linea.fecha_cronograma or ''),
                str(linea.fecha_realizado or ''),
                linea.observaciones_financiera or '',
                linea.observacion_final or '',
                responsables_texto,
            ]))

            coincide_busqueda = not query or query in texto_linea or query in responsables_normalizado
            coincide_responsable = not responsable_query or responsable_query in responsables_normalizado
            if coincide_busqueda and coincide_responsable:
                filtradas |= linea

        total = len(filtradas)
        completadas = len(filtradas.filtered(lambda linea: linea.aprobado or linea.estado == 'completo'))
        porcentaje = round((completadas / total) * 100) if total else 0
        return {
            'responsables': sorted(responsables_disponibles),
            'total': total,
            'completadas': completadas,
            'porcentaje': porcentaje,
        }

    @api.onchange('aprobado')
    def _onchange_aprobado(self):
        for rec in self:
            rec.estado = 'completo' if rec.aprobado else 'pendiente'
            rec.fecha_aprobado = date.today() if rec.aprobado else False

    @api.model
    def create(self, vals):
        record = super().create(vals)

        # === ACTIVIDADES COMPLETAS POR SEMANA (llaves únicas) ===
        responsables_por_actividad = {
            # Semana 1
            'DC 1. C.Tributario.revisión listados corrección registros': ['interventoria1.finan@fundacionunivalle.com'],
            'DC 1. C.Congruencia. Corrección registros': ['contabilidad2.financiera@fundacionunivalle.com','profesional.contable@fundacionunivalle.org'],
            'DC 1. C.Tesoreria.verifica completitud registros por últimos movimientos realizados': ['tesoreria@fundacionunivalle.org'],
            'DC 1. C.Revisión. Saldos cartera': ['proyectos1.finan@fundacionunivalle.com'],
            'DC 1. C.Depuración informes. Actualizar listado cctos para reunión': ['profesional.contable@fundacionunivalle.org'],
            'DC 1. C.Causación.Sin imptos ni retenciones, reclasificaciones correcciones': ['profesional.contable@fundacionunivalle.org','interventoria1.finan@fundacionunivalle.com'
            ],
            'DC 1. C.Causación.depreciación amortización deterioro': [
                'auditor.contable@fundacionunivalle.com',
                'profesional.contable@fundacionunivalle.org'
            ],
            'DC 1. C.Reunión.coordinación': [
                'ventanilla.unica@fundacionunivalle.org',
                'contabilidad1.financiera@gmail.com',
                'jefe.administrativo@fundacionunivalle.org',
                'tesoreria@fundacionunivalle.org',
                'contabilidad2.financiera@fundacionunivalle.com',
                'presupuestos@fundacionunivalle.org',
                'auditoria.financiera@fundacionunivalle.org',
                'proyectos1.finan@fundacionunivalle.com',
                'asistentenomina@fundacionunivalle.org',
                'auditor.contable@fundacionunivalle.com',
                'profesional.contable@fundacionunivalle.org',
                'interventoria1.finan@fundacionunivalle.com',
                'cxp.finan@fundacionunivalle.com'
            ],
            'DC 2. C.Tributario.liquidación inicia': ['interventoria1.finan@fundacionunivalle.com'],
            'DC 2. C.Tesoreria. Soportes a gestión documental. prioriza proyectos': ['tesoreria@fundacionunivalle.org'],
            'DC 2. C.Informe 5to día. Legaliza IVA Unialle.inicia': ['contabilidad1.financiera@gmail.com'],
            'DC 2. C.Causación.Costo operativo. Compara con presupuesto informa para revisión': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DC 2. C.Causación.Avance. Informe gestión solicitar': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DC 2. C.Causación.Avance. Comprara avance niif': ['profesional.contable@fundacionunivalle.org'],
            'DC 2. C.Nomina.revisa saldo por pagar de todas las cuentas de nómina': ['asistentenomina@fundacionunivalle.org'],
            'DC 3. C.Presupuesto. Informa ajustes y se registran': [
                'contabilidad2.financiera@fundacionunivalle.com',
                'presupuestos@fundacionunivalle.org'
            ],
            'DC 3. C.Causación.Avance. Define avance niif y registro': ['profesional.contable@fundacionunivalle.org'],
            'DC 3. C.Tributario.Envio a BKF inicia': ['interventoria1.finan@fundacionunivalle.com'],
            'DC 3. C.Conciliación bancos.entrega extracto incluido tarjetas crédito': ['tesoreria@fundacionunivalle.org'],
            'DC 3. C.Informe 5to día. Legaliza IVA Unialle.termina': ['contabilidad1.financiera@gmail.com'],
            'DC 3. C.Ventanilla.listado de pendientes facturas': [
                'ventanilla.unica@fundacionunivalle.org',
                'auditor.contable@fundacionunivalle.com'
            ],
            'DC 4. C.Conciliación.conciliar bancos y tarjetas 1 día': ['contabilidad2.financiera@fundacionunivalle.com','auditor.contable@fundacionunivalle.com','cxp.finan@fundacionunivalle.com'],



            # Semana 2
            'DC 5. C.Conciliación.verifica completitud': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DC 5. C.Depuración. Revisión cuentas registro notas': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DC 6. C.Cierre cuentas de orden': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DC 7. C.Informe.Balance.auditar para EF estados financieros': ['profesional.contable@fundacionunivalle.org'],
            'DC 7. C.Informe.Proyectos.vigentes': [
                'contabilidad2.financiera@fundacionunivalle.com',
                'cxp.finan@fundacionunivalle.com'
            ],
            'DC 8. C.Informe.Balance.entregar reporte a Directora': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DC 9. C.Informe.Balance.entregar notas de revelación': ['profesional.contable@fundacionunivalle.org'],
            '** Informes de proyectos viejos': ['contabilidad2.financiera@fundacionunivalle.com','contabilidad1.financiera@gmail.com','cxp.finan@fundacionunivalle.com'],



            # Semana 3
            'DF 11. C.Gestión documental.Organizar y transferir': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DF 11. C.Procedimientos.Revisar actualizar': ['profesional.contable@fundacionunivalle.org'],
            'DF 12. C.Reciproca.rendición enviar': ['contabilidad2.financiera@fundacionunivalle.com'],
            'DF 13. C.Reciproca.reunion externa': [
                'contabilidad2.financiera@fundacionunivalle.com',
                'profesional.contable@fundacionunivalle.org'
            ],

            # Semana 4
            'D 18. C.SGR.Pagos': ['tesoreria@fundacionunivalle.org'],
            'D 18. C.Causación.fijos': ['auditor.contable@fundacionunivalle.com'],
            'D 18. C.Tesorería. verifica completitud registros previo a finales': ['tesoreria@fundacionunivalle.org'],
            'D 18. C.Ventas.Aceptación factura y notas crédito. Confirmar': ['contabilidad1.financiera@gmail.com'],
            'D 18. C.Nomina.novedades': ['jefe.administrativo@fundacionunivalle.org'],
            'D 19. C. Cruce legalización y reembolso': [
                'auditor.contable@fundacionunivalle.com',
                'proyectos1.finan@fundacionunivalle.com'
            ],
            'D 19. C.Causación.compras tarjetas crédito': ['asistentenomina@fundacionunivalle.org'],
            'D 19. C.Nomina.validar prenómina realiza coordinador y recursos humanos': ['asistentenomina@fundacionunivalle.org'],
            'D 20. C.Causación. Registros con impuestos y retenciones finaliza': [
                'asistentenomina@fundacionunivalle.org',
                'ventanilla.unica@fundacionunivalle.org',
                'contabilidad1.financiera@gmail.com',
                'cxp.finan@fundacionunivalle.com',
                'auditor.contable@fundacionunivalle.com',
                'proyectos1.finan@fundacionunivalle.com'
            ],
            'D 20. C.Tesorería. verifica completitud recaudos para sacar cartera': ['tesoreria@fundacionunivalle.org'],
            'D 20. C.Causación.provisión gasto del mes Impto ICA, intereses, estampilla': [
                'interventoria1.finan@fundacionunivalle.com',
                'contabilidad1.financiera@gmail.com'
            ],
            'D 20. C.Nomina.liquidación definitiva nómina seg.social , interfase,conciliación, DIAN': ['asistentenomina@fundacionunivalle.org'],

        }

        # === ASIGNACIÓN AUTOMÁTICA DE RESPONSABLES ===
        actividad = vals.get('actividad', '').strip()
        correos = responsables_por_actividad.get(actividad, [])

        if correos:
            usuarios = self.env['res.users'].search([
                '|', ('login', 'in', correos),
                    ('email', 'in', correos)
            ])
            if usuarios:
                record.responsables_ids = [(4, u.id) for u in usuarios]

        return record


    def write(self, vals):
        res = super().write(vals)
        for rec in self:
            # === Actualiza fecha_aprobado incluso si el cambio es por código ===
            if 'aprobado' in vals:
                if rec.aprobado and not rec.fecha_aprobado:
                    rec.fecha_aprobado = date.today()
                elif not rec.aprobado:
                    rec.fecha_aprobado = False

            # === Recalcular datos del padre ===
            if rec.seguimiento_id:
                rec.seguimiento_id._compute_trazabilidad_resumen()
                rec.seguimiento_id._compute_porcentaje_avance()
                # Trazabilidad del cambio
                rec.seguimiento_id.message_post(
                    body=(
                        f"🧾 <b>Línea actualizada:</b> {rec.actividad} — "
                        f"Estado: <b>{rec.estado}</b>, Aprobado: {'Sí' if rec.aprobado else 'No'}"
                    )
                )
        return res


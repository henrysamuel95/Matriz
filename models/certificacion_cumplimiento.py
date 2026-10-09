import uuid
import logging
import os
import base64
import html
import textwrap
from docx import Document
from io import BytesIO
from docxtpl import DocxTemplate
from .decorador_firmas import seguimiento_firmas
from datetime import date
from docx.shared import Inches
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)


class _CertificacionTemplateProxy:
    def __init__(self, record):
        self._record = record

    def __getattr__(self, name):
        record = self._record
        sellos = {
            'firma_coordinador_hash': (
                record.firma_coordinador_hash,
                record.log_coordinador_id,
                record.coordinador_nombre_id.name if record.coordinador_nombre_id else record.coordinador_nombre,
            ),
            'firma_ventanilla_hash': (
                record.firma_ventanilla_hash,
                record.log_ventanilla_id,
                record.nombre_recepcion_id.name if record.nombre_recepcion_id else record.nombre_recepcion,
            ),
            'firma_financiera_hash': (
                record.firma_financiera_hash,
                record.log_financiera_id,
                record.nombre_financiera_id.name if record.nombre_financiera_id else record.nombre_financiera,
            ),
        }
        if name in sellos:
            firma_hash, log_firma, nombre = sellos[name]
            return record._formato_sello_firma(firma_hash, log_firma, nombre)
        if name == 'identificacion_o_nit':
            return record.nit or record.identificacion or ''
        if name == 'coordinador_cargo':
            return record.cargo2.name if record.cargo2 else ''
        if name == 'porcentaje_objetivos':
            return ('{:.2f}'.format(record.porcentaje_objetivos or 0.0)).replace('.', ',')
        value = getattr(record, name)
        return value or ''


class CertificacionCumplimiento(models.Model):
    _name = "certificacion_cumplimiento"
    _description = "Certificación Conjunta de Cumplimiento"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    relcion_cobros = fields.Many2one( 'cuentas_de_cobros', string='Relación')
    relcion = fields.Many2one( 'acta_entrega_documentos', string='Relación')
    coordinadores_asignados_ids = fields.Many2many(
        'res.users',
        string='Coordinadores asignados',
        related='relcion.usuarios_contratacion',
        readonly=True,
    )
    ventanilla_asignada_ids = fields.Many2many(
        'res.users',
        string='Usuarios de Ventanilla asignados',
        related='relcion.usuarios_supervision',
        readonly=True,
    )


    estado3 = fields.Selection(
        [('asis_coordinador', 'Asistente de coordinación'),('coordinador', 'Coordinador'), ('ventanilla', 'Ventanilla'), ('director_financiero', 'Director Financiero'), ('finaciera', 'Contabilidad'), ('au_tesoreria', 'Auxiliar de tesorería'), ('tesorería', 'Tesorería'), ('finalizado', 'Finalizado')],
        string='Estado',
        related='relcion.estado'
    )
    origen_cuenta = fields.Selection(
        related='relcion.origen_cuenta',
        string='Origen de la cuenta',
        readonly=True,
    )

    def puede_editar_usuario_actual(self):
        """Indica si el usuario todavía debe diligenciar o firmar su etapa."""
        self.ensure_one()
        acta = self.relcion
        user = self.env.user

        if not acta:
            return False
        if self.env.su or user.has_group('base.group_system') or user.has_group('Matriz.group_soporte'):
            return True
        if not acta.puede_editar_estado_actual:
            return False

        if (
            acta.estado == 'asis_coordinador'
            and user.has_group('Matriz.group_contratacion_asis_coordinador')
        ):
            return True
        if (
            acta.estado == 'coordinador'
            and user.has_group('Matriz.group_contratacion_coordinador')
            and user in acta.sudo().usuarios_contratacion
        ):
            return True
        if (
            acta.estado == 'ventanilla'
            and user.has_group('Matriz.group_contratacion_ventanilla_unica')
            and user in acta.sudo().usuarios_supervision
        ):
            return True
        if (
            acta.estado == 'director_financiero'
            and user.has_group('Matriz.group_firmante_director_financiero_certificacion')
        ):
            return True
        return False

    def name_get(self):
        result = []
        for record in self:
            # Si tiene nombre, usamos "Formulario de certificación conjunta de <nombre>"
            if record.name:
                display_name = f"Formulario de certificación conjunta de {record.name}"
            else:
                display_name = "Formulario de certificación conjunta"
            result.append((record.id, display_name))
        return result



    periodo_comprendido = fields.Char(
        string="Periodo comprendido entre",
        compute='_compute_periodo_comprendido', tracking=True
    )

    @api.depends(
        'relcion_cobros.clase_persona',
        'relcion_cobros.inicio_actividad',
        'relcion_cobros.finalizacion_actividad',
        'relcion.inicio_actividad',
        'relcion.finalizacion_actividad',
        'relcion_cobros.relacion_ids.desde_dia', 'relcion_cobros.relacion_ids.desde_mes',
        'relcion_cobros.relacion_ids.desde_anio', 'relcion_cobros.relacion_ids.hasta_dia',
        'relcion_cobros.relacion_ids.hasta_mes', 'relcion_cobros.relacion_ids.hasta_anio',
    )
    def _compute_periodo_comprendido(self):
        for record in self:
            cuenta = record.relcion_cobros
            fecha_inicio = cuenta.inicio_actividad or record.relcion.inicio_actividad
            fecha_fin = cuenta.finalizacion_actividad or record.relcion.finalizacion_actividad
            if fecha_inicio and fecha_fin:
                inicio_texto = record._formatear_fecha_periodo(fecha_inicio)
                fin_texto = record._formatear_fecha_periodo(fecha_fin)
                record.periodo_comprendido = f"{inicio_texto} y {fin_texto}" if inicio_texto and fin_texto else ''
                continue

            declaracion = record.relcion_cobros.relacion_ids[:1]
            if declaracion and declaracion.desde_dia and declaracion.desde_mes and declaracion.hasta_dia and declaracion.hasta_mes:
                inicio_texto = f"{declaracion.desde_dia} de {declaracion.desde_mes.lower()} de {declaracion.desde_anio or ''}"
                fin_texto = f"{declaracion.hasta_dia} de {declaracion.hasta_mes.lower()} de {declaracion.hasta_anio or ''}"
                record.periodo_comprendido = f"{inicio_texto.strip()} y {fin_texto.strip()}"
            else:
                record.periodo_comprendido = ''

    def _formatear_fecha_periodo(self, fecha):
        if not fecha:
            return ''
        if isinstance(fecha, str):
            fecha = fields.Date.from_string(fecha)
        meses = {
            1: 'enero', 2: 'febrero', 3: 'marzo', 4: 'abril',
            5: 'mayo', 6: 'junio', 7: 'julio', 8: 'agosto',
            9: 'septiembre', 10: 'octubre', 11: 'noviembre', 12: 'diciembre',
        }
        return f"{fecha.day} de {meses.get(fecha.month, '')} de {fecha.year}"

    fecha_iniciacion = fields.Date(
        'Fecha de iniciación',
        tracking=True,
        related='relcion.fecha_iniciacion',
    )
    fecha_terminacion = fields.Date(
        'Fecha de terminación',
        tracking=True,
        related='relcion.fecha_terminacion'
    )

    centro_costo = fields.Char(string="Centro de costo", tracking=True)

    contrato_numero = fields.Char(string="No. del contrato", tracking=True)
    rcf_numero = fields.Char(string="No. del RCF", tracking=True)

    # 1. Coordinador o director
    name = fields.Char(string="El Señor (a) y/o Empresa:", tracking=True)
    identificacion = fields.Char(string="Número de identificación", compute="_compute_identificacion_nit", tracking=True)
    nit = fields.Char(string="NIT", compute="_compute_identificacion_nit", tracking=True)
    
        
    @api.depends('relcion.documento_de_identificacion', 'relcion.clase_persona')
    def _compute_identificacion_nit(self):
        for record in self:
            record.identificacion = False
            record.nit = False
            if record.relcion:
                if record.relcion.clase_persona == "persona_natural":
                    record.identificacion = record.relcion.documento_de_identificacion
                elif record.relcion.clase_persona == "persona_juridica":
                    record.nit = record.relcion.documento_de_identificacion
                    
    expedida_en = fields.Char(
        string="Expedida en",
        compute='_compute_expedida_en',
        inverse='_inverse_expedida_en',
        store=True,
        readonly=False,
        tracking=True,
    )
    expedida_en_manual = fields.Char(
        string='Expedida en (valor manual)',
        copy=True,
    )

    def _expedida_en_desde_fuentes(self):
        """Obtiene la ciudad del proveedor vinculado al login, con respaldo del acta."""
        self.ensure_one()
        cuenta = self.relcion_cobros or self.relcion.cuenta_id
        login = cuenta.login_id if cuenta else False
        proveedor = login.contratistas if login else False
        if proveedor:
            ciudad = (
                proveedor.ciudad_exp_representante
                if cuenta.clase_persona == 'persona_juridica'
                else proveedor.ciudad_exp_natural
            )
            if ciudad:
                return ciudad.name
        return self.relcion.ciudad_contratista if self.relcion else False

    @api.depends(
        'relcion',
        'relcion.ciudad_contratista',
        'relcion.cuenta_id.login_id.contratistas.ciudad_exp_natural',
        'relcion.cuenta_id.login_id.contratistas.ciudad_exp_representante',
        'relcion_cobros.login_id.contratistas.ciudad_exp_natural',
        'relcion_cobros.login_id.contratistas.ciudad_exp_representante',
        'expedida_en_manual',
    )
    def _compute_expedida_en(self):
        for record in self:
            record.expedida_en = (
                record.expedida_en_manual
                or record._expedida_en_desde_fuentes()
                or ''
            )

    def _inverse_expedida_en(self):
        """El valor escrito manualmente queda almacenado en la certificacion."""
        for record in self:
            record.expedida_en_manual = record.expedida_en or False

    @api.onchange('relcion', 'relcion_cobros')
    def _onchange_expedida_en(self):
        for record in self:
            if not record.expedida_en:
                record.expedida_en = record._expedida_en_desde_fuentes() or ''
    actividades_especificas = fields.Text(string="Cumplió con las actividades a su cargo como:", tracking=True)
    proyecto = fields.Char(string="Proyecto", tracking=True)
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto', related='relcion.proyecto_relacionado', tracking=True)
    porcentaje_objetivos = fields.Float(string="Porcentaje de objetivos", tracking=True)
    mostrar_forma_pago = fields.Boolean(string="Mostrar forma de pago", default=False, copy=False)
    forma_pago_linea_ids = fields.Many2many(
        'tabla_forma_de_pago',
        string='Desembolsos disponibles',
        compute='_compute_forma_pago_linea_ids',
    )
    forma_pago_linea_seleccionada_id = fields.Many2one(
        'tabla_forma_de_pago',
        string='Desembolso seleccionado',
        copy=False,
        readonly=True,
    )
    forma_pago_otrosi_linea_ids = fields.Many2many(
        'tabla_forma_pago_otrosi',
        string='Desembolsos disponibles del otrosí',
        compute='_compute_forma_pago_linea_ids',
    )
    forma_pago_otrosi_linea_seleccionada_id = fields.Many2one(
        'tabla_forma_pago_otrosi',
        string='Desembolso del otrosí seleccionado',
        copy=False,
        readonly=True,
    )
    forma_pago_es_otrosi = fields.Boolean(
        string='La forma de pago pertenece a un otrosí',
        compute='_compute_forma_pago_linea_ids',
    )

    @api.depends(
        'relcion_cobros.relacion_fuv_id',
        'relcion_cobros.relacion_otrosi_fuv_id',
        'relcion_cobros.relacion_orden_compra_bienes_fuv_id',
        'relcion_cobros.relacion_orden_compra_servicios_fuv_id',
    )
    def _compute_forma_pago_linea_ids(self):
        for record in self:
            cuenta = record.relcion_cobros
            fuente = (
                cuenta.relacion_fuv_id
                or cuenta.relacion_otrosi_fuv_id
                or cuenta.relacion_orden_compra_bienes_fuv_id
                or cuenta.relacion_orden_compra_servicios_fuv_id
            )
            lineas = (
                fuente.tabla_de_forma_de_pago
                if fuente and 'tabla_de_forma_de_pago' in fuente._fields
                else False
            )
            # Un recordset vacio sigue teniendo un modelo. No se debe usar su
            # valor booleano para identificarlo porque un otrosi sin cuotas se
            # evaluaria como False y terminaria asignado al Many2many del
            # contrato, cuyo comodel es distinto.
            modelo_lineas = getattr(lineas, '_name', False)
            es_otrosi = modelo_lineas == 'tabla_forma_pago_otrosi'
            record.forma_pago_es_otrosi = es_otrosi
            record.forma_pago_linea_ids = (
                lineas if modelo_lineas == 'tabla_forma_de_pago' else False
            )
            record.forma_pago_otrosi_linea_ids = (
                lineas if es_otrosi else False
            )

    forma_pago_html = fields.Html(
        string="Forma de pago",
        compute="_compute_forma_pago_html",
        sanitize=False,
    )

    @api.depends(
        'relcion_cobros.relacion_fuv_id',
        'relcion_cobros.relacion_otrosi_fuv_id',
        'relcion_cobros.relacion_orden_compra_bienes_fuv_id',
        'relcion_cobros.relacion_orden_compra_servicios_fuv_id',
        'relcion_cobros.relacion_fuv_id.tabla_de_forma_de_pago.pago',
        'relcion_cobros.relacion_fuv_id.tabla_de_forma_de_pago.porcentaje_acumulado',
        'relcion_cobros.relacion_otrosi_fuv_id.tabla_de_forma_de_pago.pago',
        'relcion_cobros.relacion_otrosi_fuv_id.tabla_de_forma_de_pago.porcentaje_acumulado',
    )
    def _compute_forma_pago_html(self):
        for record in self:
            cuenta = record.relcion_cobros
            fuente = (
                cuenta.relacion_fuv_id
                or cuenta.relacion_otrosi_fuv_id
                or cuenta.relacion_orden_compra_bienes_fuv_id
                or cuenta.relacion_orden_compra_servicios_fuv_id
            )

            filas = []

            if fuente and 'tabla_de_forma_de_pago' in fuente._fields:
                lineas_pago = fuente.tabla_de_forma_de_pago.sorted(
                    key=lambda linea: (linea.numero_de_pagos or 0, linea.id)
                )
                for indice, linea in enumerate(lineas_pago, start=1):
                    filas.append((
                        linea.numero_de_pagos or indice,
                        linea.cantidad_de_dinero or 0.0,
                        linea.pago or 0.0,
                        linea.porcentaje_acumulado or 0.0,
                    ))
            elif fuente:
                total = fuente.total if 'total' in fuente._fields else 0.0
                filas.append((1, total or 0.0, 100.0, 100.0))

            if not filas:
                record.forma_pago_html = '<p>No hay una forma de pago registrada para esta relación.</p>'
                continue

            cuerpo = ''.join(
                '<tr>'
                '<td style="text-align:center;">{}</td>'
                '<td style="text-align:right;">${:,.2f}</td>'
                '<td style="text-align:center;">{:.2f}%</td>'
                '<td style="text-align:center;">{:.2f}%</td>'
                '</tr>'.format(
                    html.escape(str(numero)),
                    cantidad,
                    porcentaje,
                    acumulado
                )
                for numero, cantidad, porcentaje, acumulado in filas
            )

            record.forma_pago_html = (
                '<table class="table table-sm table-bordered" style="margin: 8px 0 0; width: 100%;">'
                '<thead>'
                '<tr style="background:#0d47a1; color:white;">'
                '<th style="text-align:center;"># Pagos</th>'
                '<th style="text-align:center;">Cantidad de dinero</th>'
                '<th style="text-align:center;">% Pago</th>'
                '<th style="text-align:center;">% Acumulado</th>'
                '</tr>'
                '</thead>'
                '<tbody>{}</tbody>'
                '</table>'.format(cuerpo)
            )

    def action_toggle_forma_pago(self):
        self.ensure_one()
        if self.origen_cuenta == 'regalias':
            raise ValidationError(_(
                'Las cuentas de Regalías registran el porcentaje manualmente y no usan la forma de pago.'
            ))

        return {
            'type': 'ir.actions.act_window',
            'name': 'Forma de pago',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'views': [(self.env.ref('Matriz.view_forma_pago_popup_certificacion').id, 'form')],
            'context': dict(
                self.env.context,
                certificacion_id=self.id,
                skip_certificacion_guardado=True,
            ),
        }

    def action_return_to_certificacion_from_forma_pago(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.display_name,
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'views': [(self.env.ref('Matriz.view_certificacion_cumplimiento_form').id, 'form')],
            'context': dict(self.env.context, skip_certificacion_guardado=True),
        }

    municipio = fields.Char(string="Municipio", tracking=True)
    municipio2 = fields.Many2one(
        'municipio',
        string='Municipio',
        compute='_compute_municipio2',
        inverse='_inverse_municipio2',
        store=True, readonly=False, tracking=True
    )

    def _municipio_desde_relacion(self):
        self.ensure_one()
        acta = self.relcion
        if not acta:
            return False
        departamentos_municipios = acta.departamentos_y_municipios_ids
        if departamentos_municipios:
            return departamentos_municipios[:1].municipios2
        return False

    @api.depends(
        'relcion',
        'relcion.selecionar',
        'relcion.relacion_fuv_id',
        'relcion.relacion_orden_compra_bienes_fuv_id',
        'relcion.relacion_orden_compra_servicios_fuv_id',
        'relcion.relacion_otrosi_fuv_id',
        'departamentos_y_municipios_ids.municipios2',
    )
    def _compute_municipio2(self):
        for rec in self:
            if rec.municipio2:
                continue
            rec.municipio2 = rec._municipio_desde_relacion()

    @api.onchange('relcion')
    def _onchange_relcion_municipio2(self):
        for rec in self:
            if not rec.municipio2:
                rec.municipio2 = rec._municipio_desde_relacion()

    @api.onchange('relcion')
    def _onchange_relcion_datos_editables(self):
        for rec in self:
            if rec.relcion:
                contrato_regalias = (
                    rec.relcion.contrato_regalias_id
                    if rec.relcion.origen_cuenta == 'regalias' else False
                )
                if contrato_regalias:
                    rec.centro_costo = contrato_regalias.un or rec.relcion.mcb_centro_costo or ''
                    rec.contrato_numero = contrato_regalias.numero_de_contrato or ''
                    if not rec.rcf_numero:
                        rec.rcf_numero = contrato_regalias.rpc or ''
                else:
                    rec.centro_costo = rec.relcion.mcb_centro_costo or ''
                    rec.contrato_numero = rec.relcion.id_del_contrato or ''
                    if not rec.rcf_numero:
                        rec.rcf_numero = rec.relcion.compromiso_presupuestal or ''

    def completar_datos_regalias_si_vacios(self):
        """Precarga contrato/RPC de Regalías sin pisar ediciones manuales."""
        for rec in self:
            relcion = rec.relcion.sudo()
            contrato_regalias = (
                relcion.contrato_regalias_id
                if relcion and relcion.origen_cuenta == 'regalias' else False
            )
            if not contrato_regalias:
                continue
            valores = {}
            if not rec.centro_costo:
                valores['centro_costo'] = contrato_regalias.un or relcion.mcb_centro_costo or ''
            if not rec.contrato_numero:
                valores['contrato_numero'] = contrato_regalias.numero_de_contrato or ''
            if not rec.rcf_numero:
                valores['rcf_numero'] = contrato_regalias.rpc or ''
            if valores:
                rec.sudo().with_context(
                    skip_auto_certificacion_conjunta=True,
                    skip_certificacion_chatter_acta=True,
                ).write(valores)

    def _inverse_municipio2(self):
        """Conservar la selección manual realizada en la certificación."""
        # El valor ya queda almacenado por el campo computed/store. La inversa
        # permite editarlo sin perder el autollenado cuando cambia el contrato.
        return


    departamentos_y_municipios_ids = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id',
                                                     string='Tabla', related='relcion.departamentos_y_municipios_ids', tracking=True)

    # Coordinador
    coordinador_nombre = fields.Char(string="Nombre completo")
    coordinador_nombre_id = fields.Many2one(
        'res.users',
        string='Nombre completo',
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_contratacion_coordinador').id])], tracking=True
        
    )


    cargo2 = fields.Many2one('cargo_tag', string="Cargo coordinador", tracking=True)
    fecha_coordinador = fields.Date(string="Fecha coordinador", tracking=True)
    firma_coordinador = fields.Binary(string="Firma", attachment=True, tracking=True)
    firma_coordinador_name = fields.Char(string="Firma", tracking=True)
    firmado = fields.Boolean(string="Firmado", default=False, tracking=True)

    @api.onchange('coordinador_nombre_id')
    def _onchange_coordinador_nombre_id(self):
        # Al seleccionar al coordinador asignado, se activa su firma. Si se
        # elimina el nombre, también se desactiva para no dejar una firma sin
        # firmante asociado.
        self.firmado = bool(self.coordinador_nombre_id)

    # 2. Recepción ventanilla única
    nombre_recepcion = fields.Char(string="Nombre quien recibe", tracking=True)
    nombre_recepcion_id = fields.Many2one(
        'res.users',
        string='Nombre quien recibe',
        domain=lambda self: [('groups_id', 'in', [self.env.ref('Matriz.group_contratacion_ventanilla_unica').id])], tracking=True
        
    )
    
    def render_informe_template_word(self):
        # Obtener la ruta del archivo de plantilla
        module_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        template_path = os.path.join(module_dir, "templates", "Certificacion_conjunta_2025.docx")

        # Cargar la plantilla
        template = DocxTemplate(template_path)

        # La plantilla lee relacionados como municipio; el usuario que firma no siempre tiene ACL sobre esos modelos.
        render_record = self.sudo()

        # Contexto de renderizado
        context = {
            'docs': render_record,
            'docx': _CertificacionTemplateProxy(render_record),
        }

        # Renderizar la plantilla con los datos
        template.render(context)

        # Guardar el resultado en memoria (no en disco)
        output_stream = BytesIO()
        template.save(output_stream)
        output_stream.seek(0)

        return output_stream  # Devuelve un stream listo para leer

    def _formato_sello_firma(self, firma_hash, log_firma, nombre):
        if not firma_hash:
            return ""

        fecha = ""
        if log_firma and log_firma.signature_date:
            fecha = fields.Datetime.context_timestamp(self, log_firma.signature_date).strftime("%d/%m/%Y %H:%M")

        hash_lineas = textwrap.wrap(str(firma_hash), width=54) or [str(firma_hash)]
        lineas = [
            "Hash: " + hash_lineas[0],
            *hash_lineas[1:],
        ]
        return "\n".join(linea for linea in lineas if linea)

    def generar_informe_word(self):
        self.ensure_one()
        output_stream = self.render_informe_template_word()
        file_name = '{}.docx'.format(self.name or 'Certificacion_conjunta')

        # Leer contenido directamente desde memoria
        file_content = output_stream.getvalue()

        # Guardar en campos binarios de Odoo
        self.with_context(skip_auto_certificacion_conjunta=True).write({
            'informe_adjunto': base64.b64encode(file_content),
            'informe_filename': file_name
        })

        # 📌 🟢 CONDICIÓN: Subidas a Drive según el tipo de persona
        file_link = '#'
        if self.clase_persona == 'persona_juridica':
            # Sube con la función 1 (Jurídica)
            file_link = self.subir_a_drive_relacion(file_name, file_content) or '#'
        elif self.clase_persona == 'persona_natural':
            # Sube con la función 2 (Natural)
            file_link = self.subir_a_drive_relacion2(file_name, file_content) or '#'

        # ✅ Trazabilidad Mejorada
        if not self.env.context.get('silent_certificacion_conjunta_auto'):
            self.message_post(
                body=(
                    f"📄 Se generó el documento <b>Certificación conjunta de cumplimiento</b> "
                    f"y fue cargado exitosamente en Google Drive.<br>"
                ),
                subtype_xmlid="mail.mt_note"
            )

        return self.action_save_and_return_to_acta()

    def action_save_and_return_to_acta(self):
        """Regresa al acta relacionada después de guardar desde el botón."""
        self.ensure_one()
        if not self.relcion:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'type': 'ir.actions.act_window',
            'name': 'Acta de entrega de documentos',
            'res_model': 'acta_entrega_documentos',
            'res_id': self.relcion.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    relcion = fields.Many2one('acta_entrega_documentos', string='Relación')

    def subir_a_drive_relacion(self, file_name, file_content):
        """Sube el archivo al Google Drive usando el modelo relacionado."""
        if self.relcion:
            self.relcion.with_context(self.env.context)._upload_certificacion_conjunta_juridica_to_drive()
    
    def subir_a_drive_relacion2(self, file_name, file_content):
        """Sube el archivo al Google Drive usando el modelo relacionado."""
        if self.relcion:
            self.relcion.with_context(self.env.context)._upload_certificacion_conjunta_to_drive()

    informe_adjunto = fields.Binary(string='Adjunto del informe', tracking=True)
    informe_filename = fields.Char(string='Nombre del archivo', tracking=True)

    firma_recepcion = fields.Binary(string="Firma recepción", attachment=True, tracking=True)
    firma_recepcion_name = fields.Char(string="Firma recepción", tracking=True)
    fecha_recepcion = fields.Date(string="Fecha recepción", tracking=True)
    firmado_ventanilla = fields.Boolean(string="Firmado por ventanilla", default=False, tracking=True)

    @api.onchange('nombre_recepcion_id')
    def _onchange_nombre_recepcion_id(self):
        # La selección del usuario asignado activa su firma; al retirar el
        # nombre se elimina también la confirmación de firma.
        self.firmado_ventanilla = bool(self.nombre_recepcion_id)

    def preparar_firmantes_asignados(self):
        """Descarta firmantes antiguos que no pertenecen a la asignación del acta."""
        for record in self:
            acta = record.relcion.sudo()
            if not acta:
                continue
            valores = {}
            if (
                record.estado3 == 'coordinador'
                and record.coordinador_nombre_id
                and record.coordinador_nombre_id not in acta.usuarios_contratacion
            ):
                valores.update({
                    'coordinador_nombre_id': False,
                    'cargo2': False,
                    'fecha_coordinador': False,
                    'firmado': False,
                })
            if (
                record.estado3 == 'ventanilla'
                and record.nombre_recepcion_id
                and record.nombre_recepcion_id not in acta.usuarios_supervision
            ):
                valores.update({
                    'nombre_recepcion_id': False,
                    'fecha_recepcion': False,
                    'firmado_ventanilla': False,
                })
            if valores:
                record.sudo().with_context(
                    skip_auto_certificacion_conjunta=True,
                    skip_certificacion_guardado=True,
                ).write(valores)
        return True

    # 3. Área financiera
    certificado_presupuesto = fields.Selection(
        [('si', 'Sí'), ('no', 'No')],
        string="Certificado de disponibilidad presupuestal", tracking=True
    )

    certificado_presupuesto2 = fields.Char(
        string="Certificado de disponibilidad presupuestal",
        tracking=True,
        compute='_compute_certificado_presupuesto2',
    )
    pago = fields.Integer(string='Pago N°', tracking=True, related='relcion.pago')
    numero_factura = fields.Char(string='Número de factura', default='N/A', tracking=True, related='relcion.numero_factura')

    # Encabezado
    clase_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('seleccion_manual', 'Seleccione'),], string='Tipo de persona', related='relcion.clase_persona', tracking=True) 

    @api.depends('clase_persona', 'pago', 'numero_factura')
    def _compute_certificado_presupuesto2(self):
        for record in self:
            valor = ''
            factura_valida = record.numero_factura and record.numero_factura.strip() not in ('', 'N/A')

            if record.clase_persona == 'persona_natural':
                # Si tiene factura válida, prioriza la factura
                if factura_valida:
                    valor = record.numero_factura
                elif record.pago:
                    valor = f'Pago N° {record.pago}'
                else:
                    valor = 'Sin información'
            elif record.clase_persona == 'persona_juridica':
                valor = record.numero_factura if factura_valida else 'Sin factura'
            else:
                valor = 'Sin tipo de persona'

            record.certificado_presupuesto2 = valor

    nombre_financiera = fields.Char(string="Nombre área financiera", tracking=True)
    nombre_financiera_id = fields.Many2one(
        'res.users',
        string='Nombre área financiera',
        domain=lambda self: [
            ('groups_id', 'in', [
                self.env.ref('Matriz.group_firmante_director_financiero_certificacion').id,
            ])
        ],
        tracking=True
    )

    @api.onchange('nombre_financiera_id')
    def _onchange_nombre_financiera_id(self):
        # La selección del Director Financiero activa su firma; al retirar el
        # nombre se elimina también la confirmación de firma.
        self.firmado_finaciera = bool(self.nombre_financiera_id)

    def preparar_firmante_director_financiero(self):
        """Elimina valores heredados sin seleccionar ni firmar automáticamente."""
        grupo = self.env.ref(
            'Matriz.group_firmante_director_financiero_certificacion'
        )
        firmantes = grupo.users.filtered(lambda user: user.active)
        for record in self:
            if record.estado3 != 'director_financiero':
                continue
            if len(firmantes) != 1:
                raise ValidationError(_(
                    "Debe existir exactamente un usuario activo con el rol "
                    "'Firmante Director Financiero - Certificación conjunta'."
                ))
            if (
                record.nombre_financiera_id
                and record.nombre_financiera_id not in firmantes
            ):
                record.with_context(
                    skip_auto_certificacion_conjunta=True,
                    skip_certificacion_guardado=True,
                ).write({
                    'nombre_financiera_id': False,
                    'fecha_financiera': False,
                    'firmado_finaciera': False,
                })
        return True

    fecha_financiera = fields.Date(string="Fecha área financiera", tracking=True)
    firma_financiera = fields.Binary(string="Firma área financiera", attachment=True, tracking=True)
    firma_financiera_name = fields.Char(string="Firma área financiera")
    firmado_finaciera = fields.Boolean(string="Firmado por financiera", default=False, tracking=True)

    estado = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('ventanilla', 'Ventanilla única'),
        ('financiera', 'Director financiero'),
        ('completado', 'Completado'),
    ], string='Estado', default='coordinador', tracking=True)



    informe_adjunto = fields.Binary("Informe generado", attachment=True)
    informe_filename = fields.Char("Nombre del archivo")
    formulario_guardado = fields.Boolean(
        string="Formulario guardado",
        default=False,
        copy=False,
        readonly=True,
    )
    

    # =========================================================================
    # CAMPOS TÉCNICOS PARA EL DECORADOR (AÑADIR ESTOS)
    # =========================================================================
    
    # 1. Coordinador (Usa tu campo 'firmado')
    firma_coordinador_hash = fields.Char(string="Sello Hash firma coordinador", readonly=True, copy=False, tracking=True)
    log_coordinador_id = fields.Many2one('signature.mixin', string="Log Coordinador", readonly=True)

    # 2. Ventanilla (Usa tu campo 'firmado_ventanilla')
    firma_ventanilla_hash = fields.Char(string="Sello Hash firma ventanilla", readonly=True, copy=False, tracking=True)
    log_ventanilla_id = fields.Many2one('signature.mixin', string="Log Ventanilla", readonly=True)

    # 3. Financiera (Usa tu campo 'firmado_finaciera')
    firma_financiera_hash = fields.Char(string="Sello Hash firma financiera", readonly=True, copy=False, tracking=True)
    log_financiera_id = fields.Many2one('signature.mixin', string="Log Financiera", readonly=True)

    # =========================================================================
    # APLICACIÓN DEL DECORADOR REUTILIZANDO TUS BOOLEANOS
    # =========================================================================

    _AUTO_GENERAR_CERTIFICACION_FIELDS = {
        'tipo_persona',
        'periodo',
        'centro_costo',
        'contrato_numero',
        'rcf_numero',
        'name',
        'identificacion',
        'nit',
        'expedida_en',
        'actividades_especificas',
        'porcentaje_objetivos',
        'municipio2',
        'coordinador_nombre_id',
        'cargo2',
        'fecha_coordinador',
        'firma_coordinador',
        'firma_coordinador_name',
        'firma_coordinador_hash',
        'log_coordinador_id',
        'firmado',
        'nombre_recepcion_id',
        'fecha_recepcion',
        'firma_recepcion',
        'firma_recepcion_name',
        'firma_ventanilla_hash',
        'log_ventanilla_id',
        'firmado_ventanilla',
        'nombre_financiera_id',
        'fecha_financiera',
        'firma_financiera',
        'firma_financiera_name',
        'firma_financiera_hash',
        'log_financiera_id',
        'firmado_finaciera',
    }

    def _puede_autogenerar_certificacion(self):
        self.ensure_one()
        return bool(self.relcion)

    def _autogenerar_certificacion_conjunta(self):
        for record in self:
            if not record._puede_autogenerar_certificacion():
                continue
            try:
                record.with_context(
                    skip_auto_certificacion_conjunta=True,
                    silent_certificacion_conjunta_auto=True,
                ).generar_informe_word()
            except Exception:
                _logger.exception(
                    "No fue posible autogenerar la certificación conjunta %s",
                    record.display_name,
                )

    @api.model
    def create(self, vals):
        vals = dict(vals or {})
        relcion_id = vals.get('relcion')
        if relcion_id:
            relcion = self.env['acta_entrega_documentos'].sudo().browse(relcion_id).exists()
            if relcion:
                contrato_regalias = (
                    relcion.contrato_regalias_id
                    if relcion.origen_cuenta == 'regalias' else False
                )
                vals.setdefault(
                    'centro_costo',
                    (contrato_regalias.un if contrato_regalias else False)
                    or relcion.mcb_centro_costo
                    or ''
                )
                vals.setdefault(
                    'contrato_numero',
                    (contrato_regalias.numero_de_contrato if contrato_regalias else False)
                    or relcion.id_del_contrato
                    or ''
                )
                vals.setdefault(
                    'rcf_numero',
                    (contrato_regalias.rpc if contrato_regalias else False)
                    or relcion.compromiso_presupuestal
                    or ''
                )
                cuenta = relcion.cuenta_id
                login = cuenta.login_id if cuenta else False
                proveedor = login.contratistas if login else False
                ciudad = False
                if proveedor:
                    ciudad = (
                        proveedor.ciudad_exp_representante
                        if cuenta.clase_persona == 'persona_juridica'
                        else proveedor.ciudad_exp_natural
                    )
                vals.setdefault(
                    'expedida_en_manual',
                    (ciudad.name if ciudad else False)
                    or relcion.ciudad_contratista
                    or ''
                )
        record = super(CertificacionCumplimiento, self).create(vals)
        return record

    def _mirror_certificacion_cambios_en_acta(self, vals, valores_anteriores):
        if self.env.context.get('skip_certificacion_chatter_acta'):
            return

        labels = {
            'actividades_especificas': 'Cumplió con las actividades a su cargo como',
            'porcentaje_objetivos': 'Porcentaje de objetivos',
            'municipio2': 'Municipio de ejecución',
            'coordinador_nombre_id': 'Coordinador',
            'cargo2': 'Cargo del coordinador',
            'fecha_coordinador': 'Fecha coordinador',
            'nombre_recepcion_id': 'Ventanilla Única',
            'fecha_recepcion': 'Fecha recepción',
            'nombre_financiera_id': 'Director Financiero',
            'fecha_financiera': 'Fecha Director Financiero',
        }
        firmas = {
            'firmado': 'Coordinador',
            'firmado_ventanilla': 'Ventanilla Única',
            'firmado_finaciera': 'Director Financiero',
        }

        campos = set(vals).intersection(set(labels) | set(firmas))
        if not campos:
            return

        for record in self:
            acta = record.relcion
            if not acta:
                continue

            lineas = []
            anterior = valores_anteriores.get(record.id, {})
            for campo in labels:
                if campo not in campos:
                    continue
                valor_anterior = anterior.get(campo)
                valor_actual = record[campo]
                if campo in ('coordinador_nombre_id', 'cargo2', 'nombre_recepcion_id', 'nombre_financiera_id'):
                    valor_actual = valor_actual.display_name if valor_actual else ''
                if valor_anterior == valor_actual:
                    continue
                lineas.append(
                    "<li><b>{}</b>: {} → {}</li>".format(
                        html.escape(labels[campo]),
                        html.escape(str(valor_anterior or 'Ninguno')),
                        html.escape(str(valor_actual or 'Ninguno')),
                    )
                )

            for campo, etiqueta in firmas.items():
                if campo in campos and vals.get(campo) and not anterior.get(campo):
                    lineas.append("<li>✅ Firma registrada: <b>{}</b></li>".format(html.escape(etiqueta)))

            if lineas:
                acta.message_post(
                    body=(
                        "<b>Certificación conjunta actualizada</b>"
                        "<ul>{}</ul>".format(''.join(lineas))
                    )
                )
    
    # Trazabilidad de firmas (ver seguimiento_firmas en decorador_firmas.py):
    # 3 slots de firma sobre esta certificación conjunta — Coordinador,
    # Ventanilla Única y Director Financiero, cada uno con su propio
    # checkbox/hash/log. A diferencia de los otros 4 modelos que usan este
    # decorador, aquí se aplica DIRECTO sobre el write() público (no sobre
    # un helper privado): el bloque de validaciones/snapshot de abajo corre
    # una vez por cada registro firmado (ver docstring del decorador para el
    # porqué de la ejecución por-registro).
    def write(self, vals):
        """Al usar el decorador @seguimiento_firmas (ver
        _write_con_seguimiento_firmas más abajo), cada vez que alguien
        marque uno de los checks de firma en la interfaz se generará el
        hash FHA-256.

        Los gates de autorización (AccessError) y los efectos secundarios
        batch-wide (notificar contabilidad, autogenerar certificación
        conjunta) viven en ESTE write() externo, sobre el `self` COMPLETO
        tal como llegó — a propósito, no dentro del método decorado: el
        decorador invoca el write real una vez POR REGISTRO (necesario
        para que cada uno obtenga su propio hash), así que un chequeo
        `all(...)/any(...)` dentro del método decorado solo vería 1
        registro a la vez y dejaría de ser atómico sobre el batch. Ver la
        regla completa en decorador_firmas.py y FIRMA_ELECTRONICA.md.
        """
        campos_exclusivos_asistente = {
            'name',
            'centro_costo',
            'contrato_numero',
            'rcf_numero',
            'actividades_especificas',
            'porcentaje_objetivos',
            'municipio2',
            'forma_pago_linea_seleccionada_id',
        }
        if vals and 'relcion' in vals:
            vals = dict(vals)
            relcion = self.env['acta_entrega_documentos'].sudo().browse(vals.get('relcion')).exists()
            if relcion:
                contrato_regalias = (
                    relcion.contrato_regalias_id
                    if relcion.origen_cuenta == 'regalias' else False
                )
                vals.setdefault(
                    'centro_costo',
                    (contrato_regalias.un if contrato_regalias else False)
                    or relcion.mcb_centro_costo
                    or ''
                )
                vals.setdefault(
                    'contrato_numero',
                    (contrato_regalias.numero_de_contrato if contrato_regalias else False)
                    or relcion.id_del_contrato
                    or ''
                )
                vals.setdefault(
                    'rcf_numero',
                    (contrato_regalias.rpc if contrato_regalias else False)
                    or relcion.compromiso_presupuestal
                    or ''
                )
        if vals and campos_exclusivos_asistente.intersection(vals):
            usuario = self.env.user
            autorizado = (
                self.env.su
                or usuario.has_group('base.group_system')
                or usuario.has_group('Matriz.group_soporte')
                or (
                    usuario.has_group('Matriz.group_contratacion_asis_coordinador')
                    and all(record.estado3 == 'asis_coordinador' for record in self)
                )
            )
            if not autorizado:
                raise AccessError(_(
                    'Los datos diligenciados por el Asistente de coordinación '
                    'quedan bloqueados al avanzar la cuenta y no pueden ser modificados.'
                ))

        for record in self:
            acta = record.relcion.sudo()
            if not acta:
                continue
            coordinador_id = vals.get(
                'coordinador_nombre_id', record.coordinador_nombre_id.id
            )
            ventanilla_id = vals.get(
                'nombre_recepcion_id', record.nombre_recepcion_id.id
            )
            if (
                ('coordinador_nombre_id' in vals or vals.get('firmado'))
                and coordinador_id
                and coordinador_id not in acta.usuarios_contratacion.ids
            ):
                raise ValidationError(_(
                    'El coordinador seleccionado debe ser uno de los usuarios '
                    'asignados previamente por el Asistente de coordinación.'
                ))
            if (
                ('nombre_recepcion_id' in vals or vals.get('firmado_ventanilla'))
                and ventanilla_id
                and ventanilla_id not in acta.usuarios_supervision.ids
            ):
                raise ValidationError(_(
                    'El usuario de Ventanilla seleccionado debe ser uno de los '
                    'asignados previamente por el Coordinador.'
                ))
            if vals.get('firmado') and not coordinador_id:
                raise ValidationError(_('Selecciona el coordinador asignado antes de firmar.'))
            if vals.get('firmado_ventanilla') and not ventanilla_id:
                raise ValidationError(_('Selecciona el usuario de Ventanilla asignado antes de firmar.'))

        campos_firma_director = {
            'nombre_financiera_id',
            'fecha_financiera',
            'firma_financiera',
            'firma_financiera_name',
            'firmado_finaciera',
        }
        if vals and campos_firma_director.intersection(vals):
            usuario = self.env.user
            autorizado = (
                self.env.su
                or usuario.has_group('base.group_system')
                or usuario.has_group('Matriz.group_soporte')
                or usuario.has_group(
                    'Matriz.group_firmante_director_financiero_certificacion'
                )
            )
            if not autorizado:
                raise AccessError(_(
                    "Solo el usuario con el rol 'Firmante Director Financiero - "
                    "Certificación conjunta' puede registrar esta firma."
                ))
            if not self.env.su and any(
                record.relcion and record.relcion.estado != 'director_financiero'
                for record in self
            ):
                raise AccessError(_(
                    "La firma del Director Financiero solo puede registrarse "
                    "durante su etapa del proceso."
                ))
            grupo_firmante = self.env.ref(
                'Matriz.group_firmante_director_financiero_certificacion'
            )
            firmante_id = vals.get('nombre_financiera_id')
            if firmante_id:
                firmante = self.env['res.users'].browse(firmante_id).exists()
                if not firmante or firmante not in grupo_firmante.users:
                    raise ValidationError(_(
                        "El Director Financiero seleccionado debe tener el rol "
                        "'Firmante Director Financiero - Certificación conjunta'."
                    ))
            if vals.get('firmado_finaciera'):
                for record in self:
                    nombre_id = vals.get(
                        'nombre_financiera_id',
                        record.nombre_financiera_id.id,
                    )
                    fecha = vals.get('fecha_financiera', record.fecha_financiera)
                    if not nombre_id:
                        raise ValidationError(_(
                            'Selecciona tu nombre como Director Financiero antes de firmar.'
                        ))
                    if not fecha:
                        raise ValidationError(_(
                            'Indica la fecha antes de registrar la firma del Director Financiero.'
                        ))

        campos_trazabilidad_acta = {
            'actividades_especificas',
            'porcentaje_objetivos',
            'municipio2',
            'coordinador_nombre_id',
            'cargo2',
            'fecha_coordinador',
            'nombre_recepcion_id',
            'fecha_recepcion',
            'nombre_financiera_id',
            'fecha_financiera',
            'firmado',
            'firmado_ventanilla',
            'firmado_finaciera',
        }
        valores_anteriores = {}
        if vals and campos_trazabilidad_acta.intersection(vals):
            for record in self:
                valores_anteriores[record.id] = {}
                for campo in campos_trazabilidad_acta.intersection(vals):
                    valor = record[campo]
                    if campo in ('municipio2', 'coordinador_nombre_id', 'cargo2', 'nombre_recepcion_id', 'nombre_financiera_id'):
                        valor = valor.display_name if valor else ''
                    valores_anteriores[record.id][campo] = valor

        es_guardado_formulario = vals and not self.env.context.get('skip_certificacion_guardado')
        if es_guardado_formulario:
            vals = dict(vals)
            vals.setdefault('formulario_guardado', True)

        res = self._write_con_seguimiento_firmas(vals)
        self._mirror_certificacion_cambios_en_acta(vals, valores_anteriores)

        # La asignación de Contabilidad ya fue realizada por Ventanilla. Al
        # registrar por primera vez la firma financiera, continuar el flujo y
        # notificar automáticamente al usuario previamente asignado.
        if vals.get('firmado_finaciera'):
            actas_firmadas = self.filtered(
                lambda record: (
                    record.relcion
                    and record.relcion.estado == 'director_financiero'
                    and not valores_anteriores.get(record.id, {}).get(
                        'firmado_finaciera'
                    )
                )
            ).mapped('relcion')
            if actas_firmadas:
                actas_firmadas.enviar_a_contabilidad()

        debe_autogenerar = bool(self._AUTO_GENERAR_CERTIFICACION_FIELDS.intersection(vals))
        if not debe_autogenerar:
            debe_autogenerar = any(
                record.estado3 in ('asis_coordinador', 'coordinador', 'ventanilla', 'au_tesoreria')
                for record in self
            )
        if (
            es_guardado_formulario
            and not self.env.context.get('skip_auto_certificacion_conjunta')
            and debe_autogenerar
        ):
            self._autogenerar_certificacion_conjunta()
        return res

    @seguimiento_firmas([
        {
            'name': 'Coordinador',
            'img': 'firma_coordinador',    # Campo Binary que ya tienes
            'check': 'firmado',            # REUTILIZADO: Tu booleano actual
            'log': 'log_coordinador_id',
            'hash': 'firma_coordinador_hash'
        },
        {
            'name': 'Ventanilla Única',
            'img': 'firma_recepcion',      # Campo Binary que ya tienes
            'check': 'firmado_ventanilla', # REUTILIZADO: Tu booleano actual
            'log': 'log_ventanilla_id',
            'hash': 'firma_ventanilla_hash'
        },
        {
            'name': 'Director Financiero',
            'img': 'firma_financiera',     # Campo Binary que ya tienes
            'check': 'firmado_finaciera',  # REUTILIZADO: Tu booleano actual
            'log': 'log_financiera_id',
            'hash': 'firma_financiera_hash'
        },
    ])
    def _write_con_seguimiento_firmas(self, vals):
        return super(CertificacionCumplimiento, self).write(vals)


class TablaFormaPagoCertificacion(models.Model):
    _inherit = 'tabla_forma_de_pago'

    def action_seleccionar_para_certificacion(self):
        self.ensure_one()
        certificacion_id = self.env.context.get('certificacion_id')
        certificacion = self.env['certificacion_cumplimiento'].browse(certificacion_id).exists()
        if not certificacion:
            raise ValidationError('No fue posible identificar la certificación conjunta.')
        if self not in certificacion.forma_pago_linea_ids:
            raise ValidationError('El desembolso no pertenece a la forma de pago de esta cuenta.')

        certificacion.with_context(skip_auto_certificacion_conjunta=True).write({
            'forma_pago_linea_seleccionada_id': self.id,
            'porcentaje_objetivos': self.porcentaje_acumulado or 0.0,
        })
        return certificacion.action_return_to_certificacion_from_forma_pago()


class TablaFormaPagoOtrosiCertificacion(models.Model):
    _inherit = 'tabla_forma_pago_otrosi'

    def action_seleccionar_para_certificacion(self):
        self.ensure_one()
        certificacion_id = self.env.context.get('certificacion_id')
        certificacion = self.env['certificacion_cumplimiento'].browse(certificacion_id).exists()
        if not certificacion:
            raise ValidationError('No fue posible identificar la certificación conjunta.')
        if self not in certificacion.forma_pago_otrosi_linea_ids:
            raise ValidationError('El desembolso no pertenece a la forma de pago de este otrosí.')

        certificacion.with_context(skip_auto_certificacion_conjunta=True).write({
            'forma_pago_otrosi_linea_seleccionada_id': self.id,
            'porcentaje_objetivos': self.porcentaje_acumulado or 0.0,
        })
        return certificacion.action_return_to_certificacion_from_forma_pago()

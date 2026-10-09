
import os
import base64
import requests
from io import BytesIO
from openpyxl import load_workbook
from odoo import models, fields, api, _
from odoo.http import request
import tempfile
import uuid
import time
import re
from urllib.parse import quote
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.osv import expression
from docxtpl import DocxTemplate  # type: ignore
from datetime import datetime
from datetime import date, timedelta
import locale
import threading
from docx import Document  # type: ignore
from docx.api import Document as DocumentApi  # type: ignore
from googleapiclient.errors import HttpError  # Asegúrate de tener google-api-python-client
from .drive import GoogleDriveAuth
import logging
from functools import wraps
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PyPDF2 import PdfReader, PdfWriter

_logger = logging.getLogger(__name__)


def _trazar_boton_correo(method):
    """Diagnóstico del flujo, sin cambiar resultados ni ocultar excepciones."""
    @wraps(method)
    def traced(self, *args, **kwargs):
        _logger.info('[CUENTA_CORREO] BOTON_INICIO boton=%s cuentas=%s usuario=%s',
                     method.__name__, self.ids, self.env.uid)
        try:
            result = method(self, *args, **kwargs)
        except Exception:
            _logger.exception('[CUENTA_CORREO] BOTON_ERROR boton=%s cuentas=%s',
                              method.__name__, self.ids)
            raise
        _logger.info('[CUENTA_CORREO] BOTON_FIN boton=%s cuentas=%s (no confirma entrega del correo)',
                     method.__name__, self.ids)
        return result
    return traced

_CAMPOS_CORRECCION_DOCUMENTOS = (
    'resolucion_facturacion_electronica_corregir',
    'documento_equivalente_corregir',
    'informe_de_actividades_corregir',
    'declaracion_juramentada_y_soportes_corregir',
    'rut_actualizado_corregir',
    'anexo_tributario_corregir',
    'factura_corregir',
    'informes_contratista_corregir',
    'otro_documentos_de_inf_corregir',
    'cesion_derechos_corregir',
    'planilla_de_aportes_a_seguridad_social_corregir',
    'certificado_de_expensa_corregir',
    'copia_contrato_corregir',
    'copia_cedula_corregir',
    'certificacion_bancaria_corregir',
    'registro_de_proveedores_corregir',
    'planilla_de_aportes_a_seguridad_social_ju_corregir',
    'factura_electronica_juridica_corregir',
    'registro_proveedores_juridica_corregir',
    'rut_actualizado_juridica_corregir',
    'resolucion_facturacion_electronica_juridica_corregir',
    'camara_comercio_juridica_corregir',
    'certificado_pago_parafiscales_juridica_corregir',
    'tarjeta_profesional_revisor_fiscal_juridica_corregir',
    'cedula_revisor_fiscal_juridica_corregir',
    'cedula_representante_legal_juridica_corregir',
    'cedula_representante_legal_de_contador_juridica_corregir',
    'Tarjeta_profesional_contador_juridica_corregir',
    'certificado_junta_contadores_juridica_corregir',
    'rut_representante_legal_juridica_corregir',
    'certificacion_bancaria_juridica_corregir',
    'anexo_tributario_juridica_corregir',
    'cedula_de_contador_juridica_corregir',
    'regalias_copia_cedula_corregir',
    'regalias_copia_contrato_corregir',
    'rpc_regalias_corregir',
)

_NOMBRES_CORRECCION_DOCUMENTOS = {
    'rut_actualizado_corregir': 'RUT actualizado',
    'rut_actualizado_juridica_corregir': 'RUT actualizado',
    'resolucion_facturacion_electronica_juridica_corregir': 'Resolución de facturación electrónica',
    'resolucion_facturacion_electronica_corregir': 'Resolución de facturación electrónica',
    'regalias_copia_cedula_corregir': 'Cédula de Regalías',
    'regalias_copia_contrato_corregir': 'Contrato de Regalías',
    'rpc_regalias_corregir': 'RPC de Regalías',
}

_BASES_DOCUMENTOS_PERSONA = {
    field_name[:-10] for field_name in _CAMPOS_CORRECCION_DOCUMENTOS
}
_BASES_DOCUMENTOS_PERSONA.update({
    'informes_contratista',
    'otro_documentos_de_informacion',
    'copia_contrato_juridica',
    'cesion_derechos',
    'rpc_regalias',
})
_CAMPOS_DOCUMENTOS_PERSONA = {
    field_name
    for base_name in _BASES_DOCUMENTOS_PERSONA
    for field_name in (
        base_name,
        base_name + '_documento',
        base_name + '_doc',
        base_name + '_name',
        base_name + '_id',
        base_name + '_2_id',
        base_name + '_ids',
    )
}
_CAMPOS_CESION_DERECHOS_PORTAL = {
    'cesion_derechos',
    'cesion_derechos_documento',
    'cesion_derechos_doc',
    'cesion_derechos_name',
    'cesion_derechos_id',
    'cesion_derechos_2_id',
    'cesion_derechos_ids',
}


class Drive(models.Model):
    _inherit = 'drive'

    portal_acta_document_type = fields.Selection(
        [
            ('informes_contratista', 'Informes del contratista'),
            ('otro_documentos_de_informacion', 'Documentos adicionales'),
            ('cesion_derechos', 'Cesión de derechos'),
            ('soporte_rechazo', 'Nota crédito / soporte de rechazo'),
        ],
        string='Tipo de documento portal acta',
    )

    def write(self, vals):
        campos_archivo = {'file_data', 'file_name', 'file_id', 'drive_file_id'}
        if campos_archivo.intersection(vals):
            documentos_bloqueados = self.filtered(
                lambda record: (
                    record.portal_acta_document_type
                    and record.contratistas_provedores
                    and record.contratistas_provedores.estado
                    in record.contratistas_provedores._ESTADOS_INFORMACION_BLOQUEADA
                )
            )
            if documentos_bloqueados:
                raise AccessError(_(
                    "Este documento pertenece al contratista o proveedor y no "
                    "puede modificarse porque la cuenta ya se encuentra en proceso."
                ))
        return super().write(vals)


class ActaEntregaDocumentos(models.Model):
    _name = 'acta_entrega_documentos'
    _description = 'Acta entrega de documentos'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    _PARAM_RESTRINGIR_ENVIO_VENTANILLA = 'Matriz.restringir_envio_ventanilla'
    _PARAM_DIA_INICIO_ENVIO_VENTANILLA = 'Matriz.envio_ventanilla_dia_inicio'
    _PARAM_DIA_CIERRE_ENVIO_VENTANILLA = 'Matriz.envio_ventanilla_dia_cierre'
    _PARAM_HORA_CIERRE_ENVIO_VENTANILLA = 'Matriz.envio_ventanilla_hora_cierre'

    proyecto_nombre = fields.Char(
        string='Proyecto',
        compute='_compute_proyecto_nombre',
        store=True,
        search=True,
    )

    @api.depends('proyecto_relacionado')
    def _compute_proyecto_nombre(self):
        for record in self:
            if record.proyecto_relacionado:
                # Intenta obtener el nombre del proyecto de varias formas
                nombre = record.proyecto_relacionado.display_name
                if not nombre:
                    # Si no tiene display_name, intenta con name
                    nombre = record.proyecto_relacionado.name
                record.proyecto_nombre = nombre or ''
            else:
                record.proyecto_nombre = ''

    def message_post(self, **kwargs):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        is_public_request = bool(
            request and hasattr(request, 'httprequest') and request.httprequest.path.startswith('/public/')
        )
        if not portal_login_id and is_public_request and len(self) == 1 and self.cuenta_id and self.cuenta_id.login_id:
            portal_login_id = self.cuenta_id.login_id.id
        if portal_login_id and not kwargs.get('author_id'):
            login = self.env['login'].sudo().browse(portal_login_id)
            if login.exists():
                partner = login._get_portal_chatter_partner()
                if partner:
                    kwargs['author_id'] = partner.id
        return super(ActaEntregaDocumentos, self).message_post(**kwargs)

    def _sgo_debe_silenciar_tracking_automatico(self, vals):
        """Evita ruido tipo 'No → Sí' para documentos/flags automáticos.

        Si cambia el estado principal, la trazabilidad se conserva.
        """
        if not vals or 'estado' in vals:
            return False
        campos_importantes = {
            'valor_cuenta',
            'valor_total_contrato',
            'pago',
            'fecha_solicitud',
            'nombre_entrega',
            'numero_factura',
            'deducciones',
            'otros_descuentos_cruces',
            'observaciones',
            'observaciones_ventanilla',
            'observaciones_contabilidad',
            'observaciones_aux_tesoreria',
            'observaciones_tesoreria',
            'motivo_rechazo',
            'fecha_rechazo',
            'usuario_rechazo_id',
        }
        if campos_importantes.intersection(vals):
            return False
        palabras_documento = (
            'doc',
            'documento',
            'factura',
            'rut',
            'certificacion',
            'certificado',
            'planilla',
            'camara',
            'copia',
            'cedula',
            'anexo',
            'registro',
            'resolucion',
            'soporte',
            'comprobante',
            'orden',
            'retencion',
            'causacion',
            'liquidacion',
            'cruce',
            'articulos',
            'informe',
            'rpc',
        )
        palabras_automaticas = (
            'visible',
            'corregir',
            'actualizar',
            'bloqueo',
            'estado_anterior',
            'informacion_bloqueada',
            'opcion_otro_documento',
            'pdf_unificado',
        )
        for field_name in vals:
            field = self._fields.get(field_name)
            if field and field.type == 'binary':
                continue
            nombre = field_name.lower()
            if any(palabra in nombre for palabra in palabras_documento):
                continue
            if any(palabra in nombre for palabra in palabras_automaticas):
                continue
            return False
        return True

    @api.model
    def _resolve_google_drive_file_id(self, drive_record):
        """Obtiene el ID remoto tanto de registros nuevos como heredados."""
        if not drive_record:
            return False

        for field_name in (
            'file_id',
            'drive_file_id',
            'file_id_contratista',
            'original_file_id',
        ):
            if field_name in drive_record._fields:
                value = getattr(drive_record, field_name, False)
                if value:
                    resolved_id = str(value).strip()
                    if field_name != 'file_id' and not drive_record.file_id:
                        drive_record.sudo().write({'file_id': resolved_id})
                    return resolved_id

        for field_name in ('file_url', 'preview_url', 'edit_url'):
            if field_name not in drive_record._fields:
                continue
            url = getattr(drive_record, field_name, False) or ''
            match = re.search(r'(?:/d/|[?&]id=)([-\w]+)', url)
            if match:
                resolved_id = match.group(1)
                if not drive_record.file_id:
                    drive_record.sudo().write({'file_id': resolved_id})
                return resolved_id
        return False

    def _safe_copy_document_to_provider(self, method_name, label):
        """Una copia inválida no debe impedir la creación de las demás."""
        self.ensure_one()
        try:
            getattr(self, method_name)()
            return True
        except Exception as error:
            _logger.warning(
                "No se pudo copiar automáticamente %s para el acta %s: %s",
                label,
                self.id,
                error,
                exc_info=True,
            )
            return False

    _ESTADOS_INFORMACION_BLOQUEADA = {
        'ventanilla', 'director_financiero', 'finaciera', 'au_tesoreria', 'tesorería', 'finalizado', 'rechazado'
    }
    _CAMPOS_INFORMACION_CONTRACTUAL = {
        'clase_persona', 'origen_cuenta', 'proyecto_regalias_id', 'contrato_regalias_id', 'proyecto_relacionado', 'selecionar',
        'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id',
        'dependencia_area_solicitante', 'nombre_entrega', 'fecha_solicitud',
        'nombre_contratista', 'documento_de_identificacion', 'correo_facturacion',
        'pago', 'de', 'valor_cuenta', 'valor_total_contrato',
        'porcentaje_de_pago', 'numero_factura', 'objeto_contrato', 'mes',
        'anio', 'anio_char', 'inicio_actividad', 'finalizacion_actividad',
    }
    _CAMPOS_RELATED_CUENTA = {
        'clase_persona', 'origen_cuenta', 'proyecto_regalias_id', 'contrato_regalias_id', 'selecionar',
        'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id',
        'nombre_entrega', 'fecha_solicitud', 'correo_facturacion',
        'pago', 'valor_cuenta', 'numero_factura', 'mes', 'anio',
        'inicio_actividad', 'finalizacion_actividad',
    }

    valor_total_contrato = fields.Monetary(string='Valor total del contrato', tracking=True, related='cuenta_id.total_relacionado')
    valor_cuenta = fields.Monetary(string='Valor cuenta/factura', tracking=True, related='cuenta_id.valor_cuenta')
    origen_cuenta = fields.Selection(related='cuenta_id.origen_cuenta', string='Origen de la cuenta')
    proyecto_regalias_id = fields.Many2one(
        'informacion_base_agregar', related='cuenta_id.proyecto_regalias_id',
        string='Proyecto de Regalías'
    )
    proyecto_regalias_nombre = fields.Char(
        related='cuenta_id.proyecto_regalias_nombre', string='Proyecto de Regalías'
    )
    contrato_regalias_id = fields.Many2one(
        'base.datos.regalias.contrato',
        related='cuenta_id.contrato_regalias_id',
        string='Contrato de Regalías'
    )
    contrato_regalias_descripcion = fields.Char(
        string='Contrato de Regalías',
        compute='_compute_contrato_regalias_descripcion',
        readonly=True,
    )

    @api.depends(
        'contrato_regalias_id',
        'contrato_regalias_id.numero_de_contrato',
        'contrato_regalias_id.razon_social',
        'contrato_regalias_id.saldo_por_utilizar',
        'contrato_regalias_id.rpc',
    )
    def _compute_contrato_regalias_descripcion(self):
        nombres = dict(self.mapped('contrato_regalias_id').name_get())
        for record in self:
            record.contrato_regalias_descripcion = nombres.get(record.contrato_regalias_id.id, '')

    contrato_regalias_numero = fields.Char(
        related='cuenta_id.contrato_regalias_numero',
        string='Contrato de Regalías'
    )
    contrato_regalias_saldo = fields.Char(
        related='cuenta_id.contrato_regalias_saldo',
        string='Saldo contrato Regalías'
    )
    regalias_centro_costo = fields.Char(related='cuenta_id.regalias_centro_costo', string='Centro de costo')
    regalias_costo_produccion = fields.Monetary(
        related='cuenta_id.regalias_costo_produccion', string='Costo de producción'
    )
    # Se conserva el nombre tecnico para no perder los valores historicos.
    deducciones = fields.Monetary(string='Retenciones', tracking=True, default=0.0)
    otros_descuentos_cruces = fields.Monetary(
        string='Otros descuentos o cruces',
        tracking=True,
        default=0.0,
    )
    valor_neto_pagar = fields.Monetary(
        string='Valor neto a pagar',
        compute='_compute_valor_neto_pagar',
        store=True,
        tracking=True,
    )
    bloqueo = fields.Boolean(string='Habilitar para corregir', default=False)
    periodo_actividad_corregir = fields.Boolean(
        string='Corregir fechas de actividad',
        default=False,
        copy=False,
        tracking=True,
        help=(
            'Habilita exclusivamente la edición de las fechas de inicio y '
            'finalización de actividad en el portal externo.'
        ),
    )
    informacion_bloqueada = fields.Boolean(
        string='Información contractual bloqueada',
        default=False,
        copy=False,
    )

    @api.depends('valor_cuenta', 'deducciones', 'otros_descuentos_cruces')
    def _compute_valor_neto_pagar(self):
        for record in self:
            record.valor_neto_pagar = max(
                (record.valor_cuenta or 0.0)
                - (record.deducciones or 0.0)
                - (record.otros_descuentos_cruces or 0.0),
                0.0,
            )

    @api.constrains('deducciones', 'otros_descuentos_cruces', 'valor_cuenta')
    def _check_deducciones(self):
        for record in self:
            if record.deducciones < 0:
                raise ValidationError(_('Las retenciones no pueden ser negativas.'))
            if record.otros_descuentos_cruces < 0:
                raise ValidationError(_(
                    'Los otros descuentos o cruces no pueden ser negativos.'
                ))
            total_descuentos = (
                (record.deducciones or 0.0)
                + (record.otros_descuentos_cruces or 0.0)
            )
            if total_descuentos > (record.valor_cuenta or 0.0):
                raise ValidationError(_(
                    'La suma de las retenciones y los otros descuentos o cruces '
                    'no puede superar el valor de la cuenta/factura.'
                ))
    documentos_persona_bloqueados = fields.Boolean(
        string='Documentos del contratista bloqueados',
        compute='_compute_documentos_persona_bloqueados',
    )

    @api.depends('estado')
    def _compute_documentos_persona_bloqueados(self):
        for record in self:
            record.documentos_persona_bloqueados = (
                record.estado in self._ESTADOS_INFORMACION_BLOQUEADA
            )
                    
    correo_facturacion = fields.Char(string="Correo electrónico para facturación electrónica", tracking=True,
                                     related='cuenta_id.correo_facturacion')
    motivo_rechazo = fields.Text(string='Motivo de rechazo', tracking=True, copy=False)
    fecha_rechazo = fields.Datetime(string='Fecha de rechazo', tracking=True, copy=False)
    usuario_rechazo_id = fields.Many2one('res.users', string='Usuario que rechazó', tracking=True, copy=False)
    soporte_rechazo = fields.Boolean(string='Soporte de rechazo', tracking=True, copy=False)
    soporte_rechazo_visible = fields.Boolean(
        string='Soporte de rechazo visible',
        default=True,
        copy=False,
    )
    soporte_rechazo_id = fields.Many2one(
        'drive',
        string='Nota crédito / soporte de rechazo',
        domain="[('stage4_id', '=', drive_stage_provedor_id)]",
        copy=False,
    )
    estado_anterior_rechazo = fields.Selection(
        [
            ('asis_coordinador', 'Asistente de coordinación'),
            ('coordinador', 'Coordinador'),
            ('ventanilla', 'Ventanilla'),
            ('director_financiero', 'Director Financiero'),
            ('finaciera', 'Contabilidad'),
            ('au_tesoreria', 'Auxiliar de tesorería'),
            ('tesorería', 'Tesorería'),
            ('finalizado', 'Finalizado'),
        ],
        string='Estado antes del rechazo',
        copy=False,
    )
    ventanilla_recepcion_cerrada = fields.Boolean(
        string='Recepción de Ventanilla cerrada',
        compute='_compute_estado_recepcion_ventanilla',
    )
    puede_enviar_a_ventanilla = fields.Boolean(
        string='Puede enviar a Ventanilla',
        compute='_compute_estado_recepcion_ventanilla',
    )
    mensaje_cierre_ventanilla = fields.Char(
        string='Mensaje cierre Ventanilla',
        compute='_compute_estado_recepcion_ventanilla',
    )

    def _config_cierre_ventanilla(self):
        params = self.env['ir.config_parameter'].sudo()
        activo = params.get_param(
            self._PARAM_RESTRINGIR_ENVIO_VENTANILLA, 'True'
        ) in ('True', '1', True)
        try:
            dia_inicio = int(params.get_param(self._PARAM_DIA_INICIO_ENVIO_VENTANILLA, '1'))
        except (TypeError, ValueError):
            dia_inicio = 1
        try:
            dia = int(params.get_param(self._PARAM_DIA_CIERRE_ENVIO_VENTANILLA, '17'))
        except (TypeError, ValueError):
            dia = 17
        try:
            hora = float(params.get_param(self._PARAM_HORA_CIERRE_ENVIO_VENTANILLA, '17.0'))
        except (TypeError, ValueError):
            hora = 17.0
        if not 1 <= dia_inicio <= 31:
            dia_inicio = 1
        if not 1 <= dia <= 31:
            dia = 17
        if not 0.0 <= hora < 24.0:
            hora = 17.0
        return activo, dia_inicio, dia, hora

    def _estado_cierre_ventanilla_actual(self):
        activo, dia_inicio, dia_cierre, hora_cierre = self._config_cierre_ventanilla()
        if not activo:
            return False, dia_inicio, dia_cierre, hora_cierre
        ahora = fields.Datetime.context_timestamp(
            self.with_context(tz='America/Bogota'), fields.Datetime.now()
        )
        hora_actual = ahora.hour + (ahora.minute / 60.0) + (ahora.second / 3600.0)
        cerrado = ahora.day < dia_inicio or ahora.day > dia_cierre or (
            ahora.day == dia_cierre and hora_actual >= hora_cierre
        )
        return cerrado, dia_inicio, dia_cierre, hora_cierre

    @api.depends('cuenta_id.login_id', 'cuenta_id.login_id.habilitar_envio_ventanilla_fuera_plazo')
    def _compute_estado_recepcion_ventanilla(self):
        for record in self:
            cerrado, dia_inicio, dia_cierre, hora_cierre = record._estado_cierre_ventanilla_actual()
            horas = int(hora_cierre)
            minutos = int(round((hora_cierre - horas) * 60))
            if minutos == 60:
                horas = (horas + 1) % 24
                minutos = 0
            record.ventanilla_recepcion_cerrada = cerrado
            record.puede_enviar_a_ventanilla = bool(
                not cerrado or record._tiene_excepcion_usuario_ventanilla()
            )
            record.mensaje_cierre_ventanilla = (
                'La recepción de nuevas cuentas en Ventanilla Única está habilitada del día %s al %s '
                'a las %02d:%02d para %s. Solo Soporte puede habilitar la excepción individual del usuario.'
                % (
                    dia_inicio,
                    dia_cierre,
                    horas,
                    minutos,
                    'FUV normal y Regalías (SGR)',
                )
                if cerrado else False
            )

    def _tiene_excepcion_usuario_ventanilla(self):
        self.ensure_one()
        login = self.cuenta_id.login_id if self.cuenta_id and self.cuenta_id.login_id else False
        return bool(login and login.sudo().habilitar_envio_ventanilla_fuera_plazo)

    def _validar_plazo_envio_ventanilla(self):
        for record in self:
            cerrado, dia_inicio, dia_cierre, hora_cierre = record._estado_cierre_ventanilla_actual()
            if cerrado and not record._tiene_excepcion_usuario_ventanilla():
                horas = int(hora_cierre)
                minutos = int(round((hora_cierre - horas) * 60))
                if minutos == 60:
                    horas = (horas + 1) % 24
                    minutos = 0
                raise UserError(_(
                    'No es posible enviar esta cuenta a Ventanilla Única. La recepción para %s está '
                    'habilitada del día %s al %s a las %02d:%02d. '
                    'Si existe una autorización excepcional, Soporte debe habilitar la excepción individual del usuario.'
                ) % (
                    'FUV normal y Regalías (SGR)',
                    dia_inicio,
                    dia_cierre,
                    horas,
                    minutos,
                ))

    observacion_para_correccion = fields.Text(string='Observaciones para corrección')
    correccion_portal_observacion = fields.Text(string='Última observación enviada al contratista', copy=False)
    correccion_portal_fecha = fields.Datetime(string='Última fecha de envío de corrección', copy=False)
    correccion_portal_activa = fields.Boolean(
        string='Corrección enviada al contratista',
        default=False,
        copy=False,
        tracking=True,
    )
    correccion_solicitante_id = fields.Many2one(
        'res.users',
        string='Usuario que envió la corrección',
        copy=False,
        tracking=True,
    )
    correccion_reporte_activa = fields.Boolean(
        string='Corrección en proceso',
        copy=False,
        tracking=True,
    )
    correccion_reporte_origen = fields.Selection(
        [
            ('coordinador', 'Coordinador'),
            ('ventanilla', 'Ventanilla'),
            ('director_financiero', 'Director Financiero'),
            ('finaciera', 'Contabilidad'),
            ('au_tesoreria', 'Auxiliar de tesorería'),
            ('tesorería', 'Tesorería'),
        ],
        string='Corrección solicitada desde',
        copy=False,
        tracking=True,
    )
    correccion_reporte_fecha_solicitud = fields.Datetime(
        string='Fecha solicitud corrección interna',
        copy=False,
        tracking=True,
    )
    correccion_reporte_fecha_cierre = fields.Datetime(
        string='Fecha cierre corrección interna',
        copy=False,
        tracking=True,
    )
    correccion_reporte_en_manos_de = fields.Char(
        string='Corrección en manos de',
        compute='_compute_correccion_reporte_en_manos_de',
        store=True,
    )
    seguimiento_correccion_reporte = fields.Char(
        string='Seguimiento corrección',
        compute='_compute_seguimiento_correccion_reporte',
        store=True,
    )

    @api.depends('correccion_reporte_activa', 'estado')
    def _compute_correccion_reporte_en_manos_de(self):
        estados = dict(self._fields['estado'].selection)
        for record in self:
            record.correccion_reporte_en_manos_de = (
                estados.get(record.estado, record.estado or '')
                if record.correccion_reporte_activa
                else ''
            )

    @api.depends(
        'correccion_reporte_activa',
        'correccion_reporte_en_manos_de',
        'correccion_reporte_fecha_solicitud',
    )
    def _compute_seguimiento_correccion_reporte(self):
        for record in self:
            if not record.correccion_reporte_activa:
                record.seguimiento_correccion_reporte = ''
                continue
            destino = record.correccion_reporte_en_manos_de or 'Sin responsable'
            if record.correccion_reporte_fecha_solicitud:
                fecha = fields.Datetime.to_string(record.correccion_reporte_fecha_solicitud)
                record.seguimiento_correccion_reporte = 'En %s desde %s' % (destino, fecha)
            else:
                record.seguimiento_correccion_reporte = 'En %s' % destino

    def _tiene_documentos_marcados_para_correccion(self):
        self.ensure_one()
        return any(
            bool(getattr(self, field_name, False))
            for field_name in self._fields
            if (
                field_name.endswith('_corregir')
                and field_name != 'periodo_actividad_corregir'
            )
        )

    @api.onchange(*_CAMPOS_CORRECCION_DOCUMENTOS)
    def _onchange_documento_corregir_activa_bloqueo(self):
        for record in self:
            record.bloqueo = any(
                getattr(record, field_name, False)
                for field_name in _CAMPOS_CORRECCION_DOCUMENTOS
                if field_name in record._fields
            )

    def _sincronizar_bloqueo_con_correcciones(self):
        for record in self:
            debe_habilitar = any(
                getattr(record, field_name, False)
                for field_name in _CAMPOS_CORRECCION_DOCUMENTOS
                if field_name in record._fields
            )
            if record.bloqueo != debe_habilitar:
                record.with_context(skip_sync_bloqueo_correcciones=True).write({
                    'bloqueo': debe_habilitar
                })

    def limpiar_correccion_portal_finalizada(self):
        for record in self:
            vals = {
                'bloqueo': False,
                'periodo_actividad_corregir': False,
                'correccion_portal_activa': False,
                'correccion_portal_observacion': False,
                'correccion_portal_fecha': False,
            }
            vals.update({
                field_name: False
                for field_name in _CAMPOS_CORRECCION_DOCUMENTOS
                if field_name in record._fields
            })
            record.with_context(skip_sync_bloqueo_correcciones=True).sudo().write(vals)

    def _documentos_marcados_para_correccion(self):
        self.ensure_one()
        documentos = []
        if self.periodo_actividad_corregir:
            documentos.append('Fechas de inicio y finalización de actividad')
        for field_name in self._fields:
            if (
                field_name.endswith('_corregir')
                and field_name != 'periodo_actividad_corregir'
                and getattr(self, field_name, False)
            ):
                documentos.append(self._nombre_documento_correccion(field_name))
        return list(dict.fromkeys(documentos))

    def _nombre_archivo_correccion(self, base_name):
        for candidate in (base_name + '_id', base_name + '_2_id'):
            if candidate not in self._fields:
                continue
            documento = getattr(self, candidate, False)
            if not documento:
                continue
            for attr in ('file_name', 'name', 'display_name'):
                nombre = getattr(documento, attr, False)
                if nombre:
                    return nombre
        for candidate in (
            base_name + '_name',
            base_name + '_doc_name',
            base_name + '_documento_name',
        ):
            if candidate in self._fields:
                nombre = getattr(self, candidate, False)
                if nombre:
                    return nombre
        return False

    def _nombre_documento_correccion(self, field_name):
        base_name = field_name[:-10]
        if field_name in _NOMBRES_CORRECCION_DOCUMENTOS:
            return _NOMBRES_CORRECCION_DOCUMENTOS[field_name]
        nombre_archivo = self._nombre_archivo_correccion(base_name)
        if nombre_archivo:
            return nombre_archivo
        etiquetas_tecnicas = {'Actualizar Visible', 'Actualizar visible', 'actualizar_visible'}
        for candidate in (base_name + '_id', base_name + '_2_id', base_name):
            field = self._fields.get(candidate)
            if field and field.string and field.string not in etiquetas_tecnicas:
                return field.string
        label = self._fields[field_name].string or base_name
        if label in etiquetas_tecnicas:
            label = base_name
        return label.replace('corregir', '').replace('Corregir', '').replace('_', ' ').strip().title()

    def _bloque_documentos_correccion_html(self):
        documentos = self._documentos_marcados_para_correccion()
        if not documentos:
            return ""
        items = "".join(f"<li>{escape(str(documento))}</li>" for documento in documentos)
        return f"""
            <div style="background-color:#eef6ff; border-left:4px solid #043484; padding:12px 18px; margin:16px 0;">
                <strong style="color:#043484;">Documento(s) que debe corregir:</strong>
                <ul style="margin:8px 0 0 18px; padding:0;">{items}</ul>
            </div>
        """

    @_trazar_boton_correo
    def enviar_correo_correccion(self):
        """ Función interna para armar y enviar el email con el diseño original """
        self.ensure_one()

        if not self.env.user.has_group('Matriz.group_contratacion_asis_coordinador'):
            raise AccessError(
                "Solo el Asistente de Coordinación puede solicitar correcciones."
            )
        if self.estado != 'asis_coordinador':
            raise UserError(
                "Solo se pueden solicitar correcciones cuando la cuenta está "
                "en Asistente de coordinación."
            )

        observacion = (self.observacion_para_correccion or '').strip()
        if self._tiene_documentos_marcados_para_correccion() and not self.bloqueo:
            raise UserError(
                "Para solicitar corrección de documentos primero debe activar "
                "'Habilitar edición para que el contratista o proveedor pueda corregir la cuenta de cobro'."
            )
        if (self.bloqueo or self.periodo_actividad_corregir) and not observacion:
            raise UserError(
                "Debe escribir en Observaciones qué debe corregir el contratista o proveedor."
            )
        
        # Detectamos la URL base automáticamente
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        if self.cuenta_id and self.cuenta_id.token:
            return_url = f"/public/cuentas_de_cobros/form/{self.cuenta_id.id}/{self.cuenta_id.token}"
            url_portal = f"{base_url}/public/login?return_url={quote(return_url, safe='')}"
        else:
            url_portal = f"{base_url}/public/login"

        # Validamos destinatario
        email_destinatario = self.correo_facturacion or self.cuenta_id.email
        if not email_destinatario:
            raise UserError(
                "La cuenta no tiene un correo de facturación válido. "
                "No se cambió el estado a Corrigiendo."
            )

        observacion_html = self._bloque_observacion_correo_html(
            observacion,
            titulo="Observaciones del Coordinador",
            destacado=True,
        )
        documentos_html = self._bloque_documentos_correccion_html()
        cuerpo_html = self._plantilla_correo_html(
            "Solicitud de corrección - Cuentas de cobro",
            self.nombre_contratista or 'Contratista',
            f"""
                {self._contexto_correo_cuenta_html()}
                <p>Se ha habilitado su registro para que pueda realizar las correcciones necesarias.</p>
                {documentos_html}
                {observacion_html}
                <p>Por favor, ingrese al portal para continuar con el proceso.</p>
            """,
            boton_url=url_portal,
            boton_texto="Ingresar al portal",
        )

        mail_values = {
            'subject': self._asunto_correo_correccion(),
            'body_html': cuerpo_html,
            'email_to': email_destinatario,
            'email_from': self.env.user.email or 'notificaciones@funcrecer.com.co',
        }

        # El estado visible solo cambia después de que Odoo confirme el envío.
        correo_enviado = self._enviar_mail_con_traza(mail_values)
        if not correo_enviado:
            raise UserError(
                "El correo no pudo enviarse. La solicitud quedó preparada, "
                "pero la cuenta todavía no pasó al estado Corrigiendo."
            )

        fecha_envio = fields.Datetime.now()
        self.write({
            # El periodo tiene un permiso independiente. El bloqueo general se
            # conserva para las correcciones de información o documentos.
            'bloqueo': self.bloqueo or not self.periodo_actividad_corregir,
            'correccion_portal_activa': True,
            'correccion_portal_observacion': observacion,
            'correccion_portal_fecha': fecha_envio,
            'correccion_solicitante_id': self.env.user.id,
        })

        documentos = self._documentos_marcados_para_correccion()
        if self.bloqueo and not documentos:
            documentos = ['Información general de la cuenta de cobro']
        documentos_html_traza = ''.join(
            '<li>%s</li>' % escape(str(documento))
            for documento in documentos
        ) or '<li>Información indicada en las observaciones</li>'
        estado_origen = dict(self._fields['estado'].selection).get(
            self.estado,
            self.estado or 'Sin estado',
        )

        # Trazabilidad de la solicitud y del movimiento visible en el tablero.
        mensaje_chatter = f"""
            <div style="border-left:4px solid #d97706; padding:10px 12px; background:#fff7ed; border-radius:8px;">
                ✅ <b>Solicitud de corrección enviada al contratista/proveedor.</b><br/>
                <b>Movimiento en el tablero:</b> {escape(str(estado_origen))} → Corrigiendo<br/>
                <b>Correcciones solicitadas:</b>
                <ul>{documentos_html_traza}</ul>
                <b>Observación enviada:</b> {escape(observacion or 'Sin observaciones').replace(chr(10), '<br/>')}
            </div>
        """
        self.message_post(body=mensaje_chatter, subtype_xmlid='mail.mt_note')
        self.write({'observacion_para_correccion': False})
        return {'type': 'ir.actions.client', 'tag': 'reload'}

        # Lista de meses
    MES_SELECTION = [
        ('01', 'Enero'),
        ('02', 'Febrero'),
        ('03', 'Marzo'),
        ('04', 'Abril'),
        ('05', 'Mayo'),
        ('06', 'Junio'),
        ('07', 'Julio'),
        ('08', 'Agosto'),
        ('09', 'Septiembre'),
        ('10', 'Octubre'),
        ('11', 'Noviembre'),
        ('12', 'Diciembre'),
    ]


    mes = fields.Selection(
        MES_SELECTION,
        string='Mes',
        default=lambda self: date.today().strftime('%m'),
        tracking=True,
        related='cuenta_id.mes',
    )

    anio = fields.Integer(
        string='Año',
        default=lambda self: date.today().year,
        related='cuenta_id.anio'
    )

    inicio_actividad = fields.Date(
        string='Inicio de actividad',
        related='cuenta_id.inicio_actividad',
        readonly=False,
    )
    finalizacion_actividad = fields.Date(
        string='Finalización de actividad',
        related='cuenta_id.finalizacion_actividad',
        readonly=False,
    )

    anio_char = fields.Char(
        string='Año',
        compute='_compute_anio_char',
        tracking=True,

    )

    @api.depends('anio')
    def _compute_anio_char(self):
        for rec in self:
            rec.anio_char = str(rec.anio or '')

    # Campo combinado (solo lectura opcional)
    mes_anio = fields.Char(
        string='Mes / Año',
        compute='_compute_mes_anio',
    )

    @api.depends('inicio_actividad', 'finalizacion_actividad', 'mes', 'anio')
    def _compute_mes_anio(self):
        for rec in self:
            if rec.inicio_actividad and rec.finalizacion_actividad:
                rec.mes_anio = '{} - {}'.format(
                    fields.Date.to_string(rec.inicio_actividad),
                    fields.Date.to_string(rec.finalizacion_actividad),
                )
            else:
                nombre_mes = dict(self.MES_SELECTION).get(rec.mes, '')
                rec.mes_anio = f"{nombre_mes} {rec.anio}" if rec.mes and rec.anio else ''


    # ==========================================================
    # 🔧 FUNCIÓN BASE PARA COMPARTIR CARPETA (versión PyDrive)
    # ==========================================================
    def _share_users_to_drive(self, usuarios, role='reader'):
        """Comparte la carpeta de esta cuenta con los responsables indicados.

        La carpeta que debe recibir el permiso es la carpeta individual de la
        cuenta de cobro. Esto también cubre Regalías, cuyo origen no tiene
        ``drive_file_id`` ni ``otrosi_drive_file_id``.
        """
        resultados = []
        for record in self:
            resultado = {
                'record_id': record.id,
                'folder_id': False,
                'shared': [],
                'already_shared': [],
                'without_email': [],
                'failed': [],
                'no_folder': False,
            }
            carpeta_cuenta = record.sudo().drive_stage_provedor_id
            folder_id = carpeta_cuenta.folder_id if carpeta_cuenta else False
            if not folder_id:
                _logger.warning(
                    "La cuenta %s no tiene carpeta individual de Drive vinculada.",
                    record.id,
                )
                resultado['no_folder'] = True
                resultados.append(resultado)
                raise UserError(_(
                    "La cuenta de cobro no tiene una carpeta individual de Google Drive. "
                    "Créela o vincúlela antes de asignar responsables."
                ))
            resultado['folder_id'] = folder_id

            try:
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()
                gfile = drive.CreateFile({'id': folder_id})
                gfile.FetchMetadata()

                permisos_respuesta = drive.auth.service.permissions().list(
                    fileId=folder_id
                ).execute()
                permisos = (
                    permisos_respuesta.get('items')
                    or permisos_respuesta.get('permissions')
                    or []
                )
                correos_con_acceso = {
                    str(
                        permiso.get('emailAddress')
                        or permiso.get('value')
                        or ''
                    ).strip().lower()
                    for permiso in permisos
                    if permiso.get('type') in ('user', 'group')
                }

                for user in usuarios:
                    email = (user.email or user.partner_id.email or '').strip()
                    if not email:
                        _logger.warning(f"⚠️ Usuario {user.name} sin correo, se omite.")
                        resultado['without_email'].append(user.name or user.display_name)
                        continue

                    if email.lower() in correos_con_acceso:
                        resultado['already_shared'].append({
                            'name': user.name or user.display_name,
                            'email': email,
                        })
                        continue

                    try:
                        gfile.InsertPermission({
                            'type': 'user',
                            'value': email,
                            'role': role,
                        })
                        correos_con_acceso.add(email.lower())
                        _logger.info(f"📤 Carpeta {folder_id} compartida con {email} ({role})")
                        resultado['shared'].append({
                            'name': user.name or user.display_name,
                            'email': email,
                        })
                    except Exception as e_perm:
                        _logger.error(f"⚠ Error otorgando permiso a {email}: {e_perm}", exc_info=True)
                        resultado['failed'].append({
                            'name': user.name or user.display_name,
                            'email': email,
                            'error': str(e_perm),
                        })

            except Exception as e:
                _logger.error(f"❌ Error compartiendo carpeta {folder_id}: {e}", exc_info=True)
                raise UserError(_("Error compartiendo la carpeta en Drive: %s") % e)

            if resultado['without_email']:
                raise UserError(_(
                    "No se puede compartir la carpeta porque estos usuarios no "
                    "tienen correo configurado: %s"
                ) % ', '.join(resultado['without_email']))
            if resultado['failed']:
                raise UserError(_(
                    "Google Drive no permitió compartir la carpeta con: %s"
                ) % ', '.join(item['email'] for item in resultado['failed']))
            resultados.append(resultado)
        return resultados

    _DRIVE_ASSIGNMENT_FIELDS = {
        'usuarios_contratacion': 'Coordinación',
        'usuarios_supervision': 'Ventanilla',
        'usuarios_contabilidad': 'Contabilidad',
        'usuarios_auxiliar_tesoreria': 'Auxiliar de Tesorería',
        'usuarios_tesoreria': 'Tesorería',
        'usuarios_juridico_cesion': 'Jurídica',
    }

    def _trazar_asignaciones_y_compartir_drive(self, asignaciones_anteriores):
        """Comparte con los nuevos responsables y deja evidencia en chatter."""
        usuario_asignador = self.env.user
        fecha_asignacion = fields.Datetime.context_timestamp(
            self,
            fields.Datetime.now(),
        ).strftime('%d/%m/%Y %H:%M:%S')

        for record in self:
            anteriores_record = asignaciones_anteriores.get(record.id, {})
            for field_name, area_label in self._DRIVE_ASSIGNMENT_FIELDS.items():
                if field_name not in anteriores_record:
                    continue

                usuarios_anteriores = self.env['res.users'].browse(
                    anteriores_record[field_name]
                )
                usuarios_actuales = record[field_name]
                usuarios_agregados = usuarios_actuales - usuarios_anteriores
                usuarios_retirados = usuarios_anteriores - usuarios_actuales
                if not usuarios_agregados and not usuarios_retirados:
                    continue

                resultados = (
                    record._share_users_to_drive(usuarios_agregados, role='reader')
                    if usuarios_agregados else []
                )
                resultado = resultados[0] if resultados else {}
                lineas = [
                    '<b>👤 Asignación de responsable</b>',
                    '<b>Área:</b> %s' % escape(area_label),
                    '<b>Asignado por:</b> %s' % escape(
                        usuario_asignador.name or usuario_asignador.display_name
                    ),
                    '<b>Fecha:</b> %s' % escape(fecha_asignacion),
                ]
                if usuarios_agregados:
                    lineas.append(
                        '<b>Usuario(s) asignado(s):</b> %s' % escape(
                            ', '.join(usuarios_agregados.mapped('name'))
                        )
                    )
                if usuarios_retirados:
                    lineas.append(
                        '<b>Usuario(s) retirado(s):</b> %s' % escape(
                            ', '.join(usuarios_retirados.mapped('name'))
                        )
                    )

                if usuarios_agregados:
                    compartidos = resultado.get('shared', [])
                    sin_correo = resultado.get('without_email', [])
                    fallidos = resultado.get('failed', [])
                    if resultado.get('no_folder'):
                        lineas.append(
                            '<b>Carpeta de Drive:</b> No se compartió porque la cuenta no tiene carpeta asociada.'
                        )
                    elif compartidos:
                        lineas.append(
                            '<b>Carpeta de Drive compartida:</b> Sí, con %s.' % escape(
                                ', '.join(item['email'] for item in compartidos)
                            )
                        )
                    ya_compartidos = resultado.get('already_shared', [])
                    if ya_compartidos:
                        lineas.append(
                            '<b>Acceso de Drive ya vigente:</b> %s.' % escape(
                                ', '.join(item['email'] for item in ya_compartidos)
                            )
                        )
                    if sin_correo:
                        lineas.append(
                            '<b>Sin correo configurado:</b> %s.' % escape(', '.join(sin_correo))
                        )
                    if fallidos:
                        lineas.append(
                            '<b>No fue posible compartir con:</b> %s.' % escape(
                                ', '.join(item['email'] for item in fallidos)
                            )
                        )

                record.message_post(
                    body='<br/>'.join(lineas),
                    subtype_xmlid='mail.mt_note',
                )



    estado_contratacion = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('juridico', 'Jurídico'),
        ('recursos_humanos', 'Recursos humanos'),
        ('contratado', 'Contratado'),
    ], string='Estado Contratación', related='contratacion_fuv_id.estado_contratacion')

    estado_contratacion_otrosi = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('juridico', 'Jurídico'),
        ('recursos_humanos', 'Recursos humanos'),
        ('contratado', 'Contratado'),
    ], string='Estado Contratación', related='contratacion_fuv_id.estado_contratacion')


    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto', related='proyecto_relacionado.users_with_access', readonly=True)
    create_uid = fields.Many2one('res.users', string='Creador del proyecto', related='proyecto_relacionado.create_uid', readonly=True)
    creator_user_id = fields.Many2one('res.users', string='Creador del proyecto (relacionado)', related='proyecto_relacionado.creator_user_id', readonly=True)
    usuarios_coordinadores_disponibles = fields.Many2many(
        'res.users',
        string='Coordinadores disponibles',
        compute='_compute_usuarios_coordinadores_disponibles',
    )

    @api.depends('users_with_access')
    def _compute_usuarios_coordinadores_disponibles(self):
        grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)
        for record in self:
            if not grupo_coordinador:
                record.usuarios_coordinadores_disponibles = False
                continue

            coordinadores = grupo_coordinador.users.filtered(lambda user: not user.share)
            if record.users_with_access:
                coordinadores = coordinadores & record.users_with_access
            record.usuarios_coordinadores_disponibles = coordinadores

    email_asistente_coordinacion = fields.Char(
        string='Correo asistente de coordinación', 
        compute='_compute_email_asistente_coordinacion', 

    )

    @api.depends()
    def _compute_email_asistente_coordinacion(self):
        """Busca el correo del asistente del grupo 'group_contratacion_asis_coordinador'."""
        group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
        if not group:
            for record in self:
                record.email_asistente_coordinacion = False
            return

        # Tomamos el primer usuario del grupo (puedes ajustar si hay varios)
        user_asistente = group.users[:1]
        for record in self:
            record.email_asistente_coordinacion = user_asistente.email if user_asistente else False

    # 1. Agrega este campo en tu modelo (arriba, con los demás campos)
    notificacion_enviada = fields.Boolean(string="Notificación Enviada", copy=False)

    # Campo técnico leído por la vista interna. Al ser compute no almacenado,
    # Odoo ejecuta la sincronización cada vez que abre/refresca el formulario.
    # Esto permite detectar RPC/Anexo cargados en el contrato DESPUÉS de crear
    # la cuenta de cobro.
    sincronizar_documentos_origen_trigger = fields.Boolean(
        string='Sincronizar documentos de origen al abrir',
        compute='_compute_sincronizar_documentos_origen_trigger',
        copy=False,
    )

    def _sincronizar_rpc_anexo_desde_origen(self):
        """Trae RPC y Anexo tardíos del origen a la carpeta de la cuenta.

        No aplica a Regalías, que mantiene su flujo documental independiente.
        """
        for record in self:
            sync_record = record.sudo()
            if (
                not sync_record.id
                or sync_record.origen_cuenta == 'regalias'
                or not sync_record.drive_stage_provedor_id
            ):
                continue

            # Fuerza a recalcular los Many2one fuente. Estos campos no son
            # almacenados y pueden haber cambiado porque el documento se cargó
            # en el contrato después de crear esta cuenta.
            if sync_record.clase_persona == 'persona_juridica':
                sync_record.invalidate_cache([
                    'anexo_tributario_juridica_id',
                    'registro_proveedores_juridica_id',
                ])
                sync_record._compute_objeto_contrato()
                sync_record._compute_registro_proveedores_juridica()

                if (
                    sync_record.anexo_tributario_juridica_id
                    and not sync_record.anexo_tributario_juridica_2_id
                ):
                    sync_record._safe_copy_document_to_provider(
                        'action_copiar_anexo_a_drive_proveedor',
                        'Anexo tributario jurídico cargado después de crear la cuenta',
                    )
                if (
                    sync_record.registro_proveedores_juridica_id
                    and not sync_record.registro_proveedores_juridica_2_id
                ):
                    sync_record._safe_copy_document_to_provider(
                        'action_copiar_rpc_juridica_a_drive_proveedor',
                        'RPC jurídico cargado después de crear la cuenta',
                    )
            else:
                sync_record.invalidate_cache([
                    'anexo_tributario_id',
                    'registro_de_proveedores_id',
                ])
                sync_record._compute_anexo_tributario()
                sync_record._compute_registro_proveedores()

                if (
                    sync_record.anexo_tributario_id
                    and not sync_record.anexo_tributario_2_id
                ):
                    sync_record._safe_copy_document_to_provider(
                        'action_copiar_anexo_a_drive_proveedor_general',
                        'Anexo tributario cargado después de crear la cuenta',
                    )
                if (
                    sync_record.registro_de_proveedores_id
                    and not sync_record.registro_de_proveedores_2_id
                ):
                    sync_record._safe_copy_document_to_provider(
                        'action_copiar_rpc_a_drive_proveedor',
                        'RPC cargado después de crear la cuenta',
                    )

        return True

    @api.depends()
    def _compute_sincronizar_documentos_origen_trigger(self):
        for record in self:
            if not self.env.context.get('skip_documentos_origen_sync'):
                record.with_context(
                    skip_documentos_origen_sync=True
                )._sincronizar_rpc_anexo_desde_origen()
            record.sincronizar_documentos_origen_trigger = False
    notificacion_finalizacion_contratista_enviada = fields.Boolean(
        string="Notificación de finalización enviada al contratista",
        copy=False,
    )

    # ==========================================================
    # 📨 Notificación al asistente de coordinación (Contratación)
    # ==========================================================
    def notify_contract_assistant(self):
        """Envía una notificación al asistente. Solo se envía UNA VEZ por registro."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        group_assistant = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
        group_assistant = group_assistant.sudo() if group_assistant else group_assistant

        if not group_assistant:
            _logger.error("El grupo 'group_contratacion_asis_coordinador' no existe.")
            return

        for record in self:
            # CANDADO: Si ya se envió antes, saltamos este registro y no hacemos nada
            if record.notificacion_enviada:
                continue

            allowed_users = record.sudo().users_with_access.filtered(lambda u: u in group_assistant.users)

            if not allowed_users:
                _logger.warning(f"No hay usuarios del grupo 'group_contratacion_asis_coordinador' con acceso al proyecto '{record.proyecto_relacionado.display_name}'.")
                continue

            # Datos del registro
            form_url = f"{base_url}/web#id={record.id}&model=acta_entrega_documentos&view_type=form"
            proyecto_nombre = record.proyecto_relacionado.display_name or "Proyecto sin nombre"
            nombre = record.name or "Cuenta de Cobro sin nombre"

            correos_enviados = False # Bandera para saber si al menos un correo salió

            for user in allowed_users:
                nombre_asistente = user.name or "Asistente de Coordinación"
                subject = f"🔔 Nueva cuenta de cobro creada: {record.name} - {proyecto_nombre}"
                
                body_html = record._plantilla_correo_html(
                    "🔔 Nueva cuenta de cobro registrada",
                    nombre_asistente,
                    f"""
                        <p>El usuario <strong>{nombre}</strong> ha registrado una cuenta de cobro para un <strong style="text-transform: capitalize;">{record.selecionar}</strong>, asociada al proyecto <strong>{proyecto_nombre}</strong>.</p>
                        <p>Por favor, revise la información y realice el seguimiento correspondiente.</p>
                    """,
                    boton_url=form_url,
                    boton_texto="Ver cuenta de cobro",
                )

                # Enviar correo
                mail_values = {
                    'subject': subject,
                    'body_html': body_html,
                    'email_to': user.email,
                    'email_from': self.env.user.email or 'no-reply@fundacionunivalle.org',
                }
                try:
                    record._enviar_mail_con_traza(mail_values)
                    correos_enviados = True
                except Exception as e:
                    _logger.error(f"❌ Error al enviar notificación a {user.email}: {str(e)}")

            # Si el correo salió con éxito, cerramos el candado para siempre
            if correos_enviados:
                record.notificacion_enviada = True


    observaciones = fields.Text(string='Observaciones', tracking=True)
    observaciones_ventanilla = fields.Text(string='Observaciones', tracking=True)
    observaciones_contabilidad = fields.Text(string='Observaciones', tracking=True)
    observaciones_aux_tesoreria = fields.Text(string='Observaciones', tracking=True)
    observaciones_tesoreria = fields.Text(string='Observaciones', tracking=True)
    contabilidad_documentos_pendientes = fields.Boolean(
        string='Documentos base de contabilidad pendientes',
        compute='_compute_contabilidad_documentos_pendientes',
    )
    contabilidad_devuelta_desde_auxiliar = fields.Boolean(
        string='Contabilidad devuelta desde auxiliar',
        default=False,
        copy=False,
        tracking=True,
    )

    actividades_especificas = fields.Text('Actividades específicas', compute='_compute_actividades_especificas')

    @api.depends('causacion', 'causacion_id')
    def _compute_contabilidad_documentos_pendientes(self):
        for record in self:
            record.contabilidad_documentos_pendientes = not bool(
                record.causacion and record.causacion_id
            )

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_actividades_especificas(self):
        for record in self:
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.actividades_especificas = record.relacion_fuv_id.actividades_especificas
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.actividades_especificas = record.relacion_orden_compra_bienes_fuv_id.descripcion_del_servicio
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.actividades_especificas = record.relacion_orden_compra_servicios_fuv_id.descripcion_del_servicio
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                # Aquí toma el que tenga valor de los dos campos
                record.actividades_especificas = record.relacion_otrosi_fuv_id.actividades_especificas_otrosi or record.relacion_otrosi_fuv_id.actividades_especificas_otrosi_natu
            else:
                record.actividades_especificas = False

    def _validar_usuario_asignado(self, usuarios, destino):
        if not usuarios:
            raise UserError(
                _(
                    "Antes de enviar al siguiente estado debes asignar un usuario en la lista '%s'."
                ) % destino
            )
        if destino in ("Asignar Coordinador", "Coordinador"):
            grupo_coordinador = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)
            usuarios_invalidos = usuarios.filtered(
                lambda user: user.share
                or not grupo_coordinador
                or grupo_coordinador not in user.groups_id
                or (self.users_with_access and user not in self.users_with_access)
            )
            if usuarios_invalidos:
                raise UserError(
                    _(
                        "En '%s' solo puedes asignar coordinadores internos del proyecto. "
                        "Revisa el usuario seleccionado: %s."
                    ) % (destino, ", ".join(usuarios_invalidos.mapped('name')))
                )

    def _certificacion_conjunta_actual(self):
        self.ensure_one()
        return self.relcion_cer_conjunto_ids[:1]

    def _certificacion_conjunta_generada_en_drive(self):
        self.ensure_one()
        if self.clase_persona == 'persona_juridica':
            return bool(self.certificacion_conjunta_juridica_id)
        return bool(self.certificacion_conjunta_id)

    def _refresh_certificacion_conjunta_drive_from_template(self):
        self.ensure_one()
        certificacion = self._certificacion_conjunta_actual()
        if not certificacion:
            return False

        return certificacion.sudo().with_context(
            silent_certificacion_conjunta_auto=True,
            mail_create_nosubscribe=True,
            mail_notrack=True,
            tracking_disable=True,
        ).generar_informe_word()

    def _usuarios_juridica_cesion(self):
        group = self.env.ref('Matriz.group_aprobador_cesion_derechos', raise_if_not_found=False)
        return group.users.filtered(lambda user: user.active) if group else self.env['res.users']

    def _validar_cesion_derechos_para_avance(self, faltantes):
        self.ensure_one()
        if not self.cesion_derechos:
            return
        if not self.cesion_derechos_id:
            faltantes.append("Adjuntar el documento de cesión de derechos")
            return

        if self.estado == 'asis_coordinador' and self.estado_cesion_derechos == 'pendiente_juridica':
            faltantes.append("Esperar/aprobar el visto bueno de Jurídica para la cesión de derechos")
        elif self.estado in ('coordinador', 'au_tesoreria') and self.estado_cesion_derechos != 'aprobada':
            faltantes.append("Completar los vistos buenos de Jurídica y Coordinador para la cesión de derechos")

    def _enviar_correo_rechazo_contratista(self):
        for record in self:
            email_destinatario = (
                record.correo_facturacion
                or (record.cuenta_id.email if record.cuenta_id and hasattr(record.cuenta_id, 'email') else False)
            )
            if not email_destinatario:
                _logger.warning(
                    "No se envió correo de rechazo de cuenta de cobro %s porque no tiene correo del contratista.",
                    record.display_name,
                )
                continue

            contratista, _proyecto = record._datos_correo_cuenta()
            motivo = (record.motivo_rechazo or '').strip()
            motivo_html = record._bloque_observacion_correo_html(
                motivo,
                titulo='Motivo de rechazo',
                destacado=True,
            )
            body_html = record._plantilla_correo_html(
                "Cuenta de cobro rechazada",
                contratista,
                f"""
                    {record._contexto_correo_cuenta_html()}
                    <p>La cuenta/factura fue <strong>rechazada</strong> y ya no continuará el flujo de revisión.</p>
                    {motivo_html}
                    <p>Puede consultar el motivo ingresando al portal.</p>
                """,
                boton_url=record._url_portal_cuenta_cobro(),
                boton_texto="Ver cuenta en el portal",
            )
            record._enviar_mail_con_traza({
                'subject': record._asunto_correo_cuenta("Cuenta de cobro rechazada"),
                'body_html': body_html,
                'email_to': email_destinatario,
                'email_from': self.env.user.email or 'no-reply@tuempresa.com',
            })

    @_trazar_boton_correo
    def rechazar_cuenta(self):
        grupos_por_estado = {
            'asis_coordinador': 'Matriz.group_contratacion_asis_coordinador',
            'coordinador': 'Matriz.group_contratacion_coordinador',
            'ventanilla': 'Matriz.group_contratacion_ventanilla_unica',
            'director_financiero': 'Matriz.group_firmante_director_financiero_certificacion',
        }
        for record in self:
            if record.estado not in grupos_por_estado:
                raise UserError(_("Esta cuenta solo se puede rechazar desde Asistente de coordinación, Coordinador, Ventanilla o Director Financiero."))
            if not (self.env.su or self.env.user.has_group(grupos_por_estado[record.estado])):
                raise AccessError(_("No tienes permisos para rechazar la cuenta desde esta etapa."))
            motivo = (
                self.env.context.get('motivo_rechazo_desde_vista')
                or record.motivo_rechazo
                or ''
            ).strip()
            if not motivo:
                raise UserError(_("Debes escribir el motivo de rechazo antes de rechazar la cuenta/factura."))
            estado_anterior = record.estado
            estado_anterior_label = dict(record._fields['estado'].selection).get(estado_anterior, estado_anterior)
            fecha_rechazo = fields.Datetime.now()
            record.with_context(
                tracking_disable=True,
                mail_notrack=True,
                mail_create_nosubscribe=True,
            ).write({
                'estado': 'rechazado',
                'estado_anterior_rechazo': estado_anterior,
                'motivo_rechazo': motivo,
                'fecha_rechazo': fecha_rechazo,
                'usuario_rechazo_id': self.env.user.id,
                'bloqueo': False,
                'periodo_actividad_corregir': False,
                'flujo_en_devolucion': False,
                'correccion_reporte_activa': False,
                'correccion_reporte_fecha_cierre': fecha_rechazo,
            })
            record._enviar_correo_rechazo_contratista()
            fecha_rechazo_texto = fields.Datetime.context_timestamp(record, fecha_rechazo).strftime('%d/%m/%Y %H:%M:%S')
            record.message_post(
                body=_(
                    """
                    <div style="border-left: 4px solid #dc2626; padding: 10px 12px; background: #fff7f7; border-radius: 8px;">
                        <div style="font-weight: 800; color: #991b1b; margin-bottom: 6px;">
                            Cuenta/factura rechazada
                        </div>
                        <div><b>Motivo:</b> %s</div>
                        <div><b>Etapa anterior:</b> %s</div>
                        <div><b>Fecha de rechazo:</b> %s</div>
                    </div>
                    """
                ) % (
                    escape(motivo).replace("\n", "<br/>"),
                    escape(estado_anterior_label or ''),
                    escape(fecha_rechazo_texto),
                ),
                subtype_xmlid="mail.mt_note",
            )
        accion_listado = self.env.ref('Matriz.action_acta_entrega_documentos').sudo().read()[0]
        accion_listado.update({'target': 'main'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Cuenta rechazada'),
                'message': _('La cuenta/factura fue rechazada y el contratista fue notificado.'),
                'type': 'warning',
                'sticky': False,
                'next': accion_listado,
            },
        }

    def revertir_rechazo_soporte(self):
        if not (self.env.su or self.env.user.has_group('Matriz.group_soporte')):
            raise AccessError(_("Solo el equipo de soporte puede revertir un rechazo."))
        for record in self:
            if record.estado != 'rechazado':
                raise UserError(_("Solo se puede revertir una cuenta/factura que esté en estado Rechazado."))
            estado_destino = record.estado_anterior_rechazo or 'asis_coordinador'
            record.write({
                'estado': estado_destino,
                'bloqueo': False,
                'flujo_en_devolucion': False,
            })
            record.message_post(
                body=_(
                    "Soporte revirtió el rechazo de la cuenta/factura. "
                    "La cuenta volvió al estado: <b>%s</b>."
                ) % dict(record._fields['estado'].selection).get(estado_destino, estado_destino),
                subtype_xmlid="mail.mt_note",
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Rechazo revertido'),
                'message': _('La cuenta/factura volvió al estado anterior.'),
                'type': 'success',
                'sticky': False,
            },
        }

    @_trazar_boton_correo
    def iniciar_flujo_cesion_derechos(self):
        for record in self:
            if not record.cesion_derechos:
                continue
            if not record.cesion_derechos_id:
                raise UserError(_("Debe adjuntar el documento de cesión de derechos."))
            if record.estado_cesion_derechos in ('pendiente_juridica', 'pendiente_coordinador'):
                continue

            juridicos = record.usuarios_juridico_cesion or record._usuarios_juridica_cesion()
            if not juridicos:
                raise UserError(_(
                    "No hay usuarios activos en el grupo Jurídica para aprobar la cesión de derechos."
                ))
            vals = {
                'estado_cesion_derechos': 'pendiente_juridica',
                'fecha_solicitud_cesion_derechos': fields.Datetime.now(),
                'fecha_aprobacion_cesion_juridica': False,
                'fecha_aprobacion_cesion_coordinador': False,
                'usuario_aprobacion_cesion_juridica_id': False,
                'usuario_aprobacion_cesion_coordinador_id': False,
                'cesion_estado_retorno': record.estado,
            }
            if juridicos and not record.usuarios_juridico_cesion:
                vals['usuarios_juridico_cesion'] = [(6, 0, juridicos.ids)]
            record.write(vals)
            if juridicos:
                record._share_users_to_drive(juridicos, role='reader')
                solicitar_desde_portal = bool(self.env.context.get('allow_cesion_derechos_portal'))
                estado_requiere_aviso_inmediato = vals['cesion_estado_retorno'] in (
                    'coordinador',
                    'ventanilla',
                    'director_financiero',
                    'finaciera',
                    'au_tesoreria',
                    'tesorería',
                )
                if (not solicitar_desde_portal) or estado_requiere_aviso_inmediato:
                    record._enviar_correo(
                        juridicos,
                        "Visto bueno de Jurídica - Cesión de derechos",
                        (
                            "Se adjuntó un documento de cesión de derechos en una cuenta de cobro.\n\n"
                            "Por favor revise el documento y registre el visto bueno de Jurídica para continuar."
                        ),
                        "",
                    )
            record.message_post(
                body=_("Se inició el flujo paralelo de <b>cesión de derechos</b>."),
                subtype_xmlid="mail.mt_note",
            )

    @_trazar_boton_correo
    def action_aprobar_cesion_juridica(self):
        for record in self:
            if not (self.env.su or self.env.user.has_group('Matriz.group_aprobador_cesion_derechos')):
                raise AccessError(_("Solo Jurídica puede aprobar la cesión de derechos en este paso."))
            if not record.cesion_derechos or not record.cesion_derechos_id:
                raise UserError(_("Debe existir un documento de cesión de derechos para aprobar."))
            if record.estado_cesion_derechos != 'pendiente_juridica':
                raise UserError(_("La cesión de derechos no está pendiente de Jurídica."))
            vals_aprobacion = {
                'estado_cesion_derechos': 'pendiente_coordinador',
                'fecha_aprobacion_cesion_juridica': fields.Datetime.now(),
                'usuario_aprobacion_cesion_juridica_id': self.env.user.id,
            }
            if record.estado == 'asis_coordinador' and record.usuarios_contratacion:
                vals_aprobacion['estado'] = 'coordinador'
            record.write(vals_aprobacion)
            usuarios_coordinador = record.usuarios_contratacion
            if usuarios_coordinador:
                record._share_users_to_drive(usuarios_coordinador, role='reader')
                record._enviar_correo(
                    usuarios_coordinador,
                    "Revisión y firma del coordinador - Cuenta de cobro",
                    (
                        "Jurídica aprobó la cesión de derechos de la cuenta de cobro.\n\n"
                        "La cuenta fue enviada para revisión y firma del Coordinador. "
                        "Por favor revise la cuenta de cobro como normalmente corresponde, "
                        "verifique la certificación conjunta, firme si todo está correcto y "
                        "revise también el documento de cesión de derechos.\n\n"
                        "Si la cesión de derechos no está correcta, puede devolverla para que "
                        "el Asistente de coordinación solicite la corrección al contratista."
                    ),
                    "",
                )
            record.message_post(
                body=_("Jurídica aprobó la <b>cesión de derechos</b>."),
                subtype_xmlid="mail.mt_note",
            )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @_trazar_boton_correo
    def action_devolver_cesion_asistente(self):
        for record in self:
            if not (self.env.su or self.env.user.has_group('Matriz.group_aprobador_cesion_derechos')):
                raise AccessError(_("Solo Jurídica puede devolver la cesión de derechos en este paso."))
            if record.estado_cesion_derechos != 'pendiente_juridica':
                raise UserError(_("La cesión de derechos no está pendiente de Jurídica."))
            observaciones = (record.observaciones or '').strip()
            if record.cesion_derechos_id:
                record.cesion_derechos_id.sudo().write({'actualizar_visible': True})
            record.write({
                'estado': 'asis_coordinador',
                'estado_cesion_derechos': 'no_aplica',
                'cesion_estado_retorno': False,
                'bloqueo': True,
                'correccion_portal_fecha': fields.Datetime.now(),
                'correccion_solicitante_id': self.env.user.id,
                'observaciones': observaciones,
            })
            group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
            asistentes = group.users.filtered(lambda user: user.active and user.email) if group else self.env['res.users']
            if asistentes:
                record._enviar_correo(
                    asistentes,
                    "Cesión de derechos devuelta por Jurídica",
                    (
                        "Jurídica devolvió la revisión de la cesión de derechos.\n\n"
                        "Revise la cuenta y solicite al contratista la corrección del documento si aplica."
                    ),
                    observaciones,
                )
            record.message_post(
                body=_("Jurídica devolvió la <b>cesión de derechos</b> al Asistente de coordinación."),
                subtype_xmlid="mail.mt_note",
            )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @_trazar_boton_correo
    def action_devolver_cesion_coordinador(self):
        for record in self:
            if not (self.env.su or self.env.user.has_group('Matriz.group_contratacion_coordinador')):
                raise AccessError(_("Solo Coordinador puede devolver la cesión de derechos en este paso."))
            if not self.env.su and record.usuarios_contratacion and self.env.user not in record.usuarios_contratacion:
                raise AccessError(_("Esta cuenta no está asignada a tu usuario como Coordinador."))
            if record.estado_cesion_derechos != 'pendiente_coordinador':
                raise UserError(_("La cesión de derechos no está pendiente de Coordinador."))
            observaciones = (record.observaciones or '').strip()
            if record.cesion_derechos_id:
                record.cesion_derechos_id.sudo().write({'actualizar_visible': True})
            record.write({
                'estado': 'asis_coordinador',
                'estado_cesion_derechos': 'no_aplica',
                'cesion_estado_retorno': False,
                'bloqueo': True,
                'correccion_portal_fecha': fields.Datetime.now(),
                'correccion_solicitante_id': self.env.user.id,
                'observaciones': observaciones,
            })
            group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
            asistentes = group.users.filtered(lambda user: user.active and user.email) if group else self.env['res.users']
            if asistentes:
                record._enviar_correo(
                    asistentes,
                    "Cesión de derechos devuelta por Coordinador",
                    (
                        "El Coordinador devolvió la revisión de la cesión de derechos.\n\n"
                        "Revise la cuenta y solicite al contratista la corrección del documento de cesión si aplica."
                    ),
                    observaciones,
                )
            record.message_post(
                body=_("Coordinador devolvió la <b>cesión de derechos</b> al Asistente de coordinación."),
                subtype_xmlid="mail.mt_note",
            )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @_trazar_boton_correo
    def action_aprobar_cesion_coordinador(self):
        for record in self:
            if not (self.env.su or self.env.user.has_group('Matriz.group_contratacion_coordinador')):
                raise AccessError(_("Solo Coordinador puede aprobar la cesión de derechos en este paso."))
            if not self.env.su and record.usuarios_contratacion and self.env.user not in record.usuarios_contratacion:
                raise AccessError(_("Esta cuenta no está asignada a tu usuario como Coordinador."))
            if not record.cesion_derechos or not record.cesion_derechos_id:
                raise UserError(_("Debe existir un documento de cesión de derechos para aprobar."))
            if record.estado_cesion_derechos != 'pendiente_coordinador':
                raise UserError(_("La cesión de derechos no está pendiente de Coordinador."))
            vals_aprobacion = {
                'estado_cesion_derechos': 'aprobada',
                'fecha_aprobacion_cesion_coordinador': fields.Datetime.now(),
                'usuario_aprobacion_cesion_coordinador_id': self.env.user.id,
            }
            if record.cesion_estado_retorno in ('au_tesoreria', 'tesorería'):
                vals_aprobacion['estado'] = record.cesion_estado_retorno
            record.write(vals_aprobacion)
            usuarios_retorno = self.env['res.users']
            if record.cesion_estado_retorno == 'au_tesoreria':
                usuarios_retorno = record.usuarios_auxiliar_tesoreria
            elif record.cesion_estado_retorno == 'tesorería':
                usuarios_retorno = record.usuarios_tesoreria
            if usuarios_retorno:
                record._share_users_to_drive(usuarios_retorno, role='reader')
                record._enviar_correo(
                    usuarios_retorno,
                    "Cesión de derechos aprobada - Cuenta de cobro",
                    (
                        "Jurídica y Coordinador aprobaron la cesión de derechos.\n\n"
                        "La cuenta vuelve a quedar disponible en la etapa de Tesorería donde estaba."
                    ),
                    "",
                )
            record.message_post(
                body=_("Coordinador aprobó la <b>cesión de derechos</b>. La cuenta puede continuar el flujo normal."),
                subtype_xmlid="mail.mt_note",
            )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _validar_requisitos_para_avanzar(self, validar_cesion=True):
        for record in self:
            certificacion = record._certificacion_conjunta_actual()
            faltantes = []

            if record.estado == 'asis_coordinador':
                if not record.de or record.de <= 0:
                    faltantes.append("Completar el campo '# de' en Información del contrato")
                if not certificacion:
                    faltantes.append("Crear y guardar la certificación conjunta")
                else:
                    if not (certificacion.actividades_especificas or '').strip():
                        faltantes.append("Completar 'Cumplió con las actividades a su cargo como'")
                    if not certificacion.porcentaje_objetivos:
                        faltantes.append("Completar el porcentaje de objetivos")

            elif record.estado == 'coordinador':
                if not certificacion or not certificacion.firmado:
                    faltantes.append("Firmar la certificación conjunta como Coordinador del Proyecto / Líder")
                if record.clase_persona == 'persona_natural' and not record.informe_vobo_coordinador:
                    faltantes.append("Firmar el Vo.Bo. del Coordinador del Proyecto / Líder en el informe de actividades")

            elif record.estado == 'ventanilla':
                if not certificacion or not certificacion.firmado_ventanilla:
                    faltantes.append("Firmar la recepción de Ventanilla Única")

            elif record.estado == 'director_financiero':
                if not certificacion or not certificacion.firmado_finaciera:
                    faltantes.append("Registrar la firma del Director Financiero en la certificación conjunta")

            elif record.estado == 'au_tesoreria':
                if not record.orden_de_pago_id:
                    faltantes.append("Subir la Orden de pago")

            elif record.estado == 'tesorería':
                if not record.comprobante_de_pago_id:
                    faltantes.append("Subir el Comprobante de pago")

            if validar_cesion:
                record._validar_cesion_derechos_para_avance(faltantes)

            if faltantes:
                raise UserError(
                    _(
                        "Antes de enviar al siguiente estado debes completar:\n\n- %s"
                    ) % "\n- ".join(faltantes)
                )

    def _datos_correo_cuenta(self):
        self.ensure_one()
        contratista = self.nombre_contratista or self.name or "Contratista sin nombre"
        proyecto = self.proyecto_relacionado.display_name if self.proyecto_relacionado else "Proyecto sin nombre"
        return contratista, proyecto

    def _asunto_correo_cuenta(self, asunto):
        contratista, proyecto = self._datos_correo_cuenta()
        return f"{asunto} - {contratista} - {proyecto}"

    def _limpiar_saludo_correo(self, mensaje):
        lineas = (mensaje or '').splitlines()
        if lineas and lineas[0].strip().lower().startswith(('estimado/a', 'estimado(a)', 'estimada/o')):
            lineas = lineas[1:]
            while lineas and not lineas[0].strip():
                lineas = lineas[1:]
        return "\n".join(lineas).strip()

    def _bloque_observacion_correo_html(self, observacion, titulo="Observaciones", destacado=False):
        if not observacion:
            return ""
        obs_texto = str(observacion).strip()
        if not obs_texto:
            return ""
        obs_texto = obs_texto.replace("\n", "<br>")
        fondo = "#fff7e6" if destacado else "#f8fafc"
        borde = "#f59e0b" if destacado else "#043484"
        color = "#92400e" if destacado else "#111827"
        return f"""
            <div style="background-color:{fondo}; border-left:4px solid {borde}; padding:10px 15px; margin:15px 0;">
                <p style="margin:0; color:{color};">
                    <strong>{titulo}:</strong><br>{obs_texto}
                </p>
            </div>
        """

    def _observacion_correo_corregida_devuelta(self):
        self.ensure_one()
        for field_name in (
            'observaciones_5',
            'observaciones_1',
            'observaciones_2',
            'observaciones_3',
            'observaciones_4',
        ):
            value = getattr(self, field_name, False)
            if value and str(value).strip():
                return str(value).strip()
        return ""

    def _observacion_correccion_destino(self, *field_names):
        self.ensure_one()
        for field_name in field_names:
            value = getattr(self, field_name, False)
            if value and str(value).strip():
                return str(value).strip()
        return ""

    def _plantilla_correo_html(self, titulo, destinatario, contenido_html, boton_url=None, boton_texto="Ver cuenta de cobro"):
        boton_html = ""
        if boton_url:
            boton_html = f"""
                <div style="text-align:center; margin:28px 0 20px;">
                    <a href="{boton_url}" style="background-color:#043484; color:#ffffff; padding:10px 24px; border-radius:4px; text-decoration:none; font-weight:bold; display:inline-block;">
                        ➜ {boton_texto}
                    </a>
                </div>
            """
        return f"""
            <div style="background-color:#f7f7f7; padding:20px; font-family:Arial, Helvetica, sans-serif; color:#0f172a;">
                <div style="background-color:#ffffff; border:1px solid #d9d9d9; border-radius:4px; overflow:hidden;">
                    <div style="background-color:#043484; color:#ffffff; text-align:center; padding:14px 18px; font-size:18px; font-weight:bold;">
                        {titulo}
                    </div>
                    <div style="padding:24px 16px; font-size:14px; line-height:1.55;">
                        <p style="margin:0 0 18px;">Cordial saludo, <strong>{destinatario or 'Usuario'}</strong>:</p>
                        {contenido_html}
                        {boton_html}
                        <p style="font-size:12px; color:#8a97a6; margin-top:28px;">Este es un mensaje automático de SGO.</p>
                    </div>
                </div>
            </div>
        """

    def _referencias_cuenta(self):
        self.ensure_one()
        numero_pago = str(self.pago) if self.pago else 'Sin número'
        if self.de:
            numero_pago = f"{numero_pago} de {self.de}"

        factura = self.numero_factura
        if not factura or str(factura).strip().upper() in {'N/A', 'NA', 'FALSE'}:
            factura = 'No aplica'

        numero_contrato = (
            self.cuenta_id.numero_contrato
            or self.id_del_contrato
            or 'No registrado'
        )
        proyecto = (
            self.proyecto_relacionado.display_name
            if self.proyecto_relacionado
            else 'Proyecto sin nombre'
        )
        return numero_pago, factura, numero_contrato, proyecto

    def _mostrar_factura_correo(self, factura):
        factura_texto = str(factura or '').strip()
        factura_normalizada = factura_texto.upper().strip(' .')
        return bool(factura_texto) and factura_normalizada not in {
            'NO APLICA',
            'N/A',
            'NA',
            'FALSE',
            'SIN FACTURA',
        }

    def _asunto_correo_correccion(self):
        numero_pago, factura, numero_contrato, proyecto = self._referencias_cuenta()
        referencia_factura = f" / Factura {factura}" if self._mostrar_factura_correo(factura) else ''
        referencia_contrato = (
            f"Contrato {numero_contrato}"
            if numero_contrato != 'No registrado'
            else proyecto
        )
        return (
            f"Corrección requerida - Cuenta de cobro No. {numero_pago}"
            f"{referencia_factura} - {referencia_contrato}"
        )

    def _contexto_correo_cuenta_html(self):
        contratista, proyecto = self._datos_correo_cuenta()
        numero_pago, factura, numero_contrato, proyecto = self._referencias_cuenta()
        factura_html = (
            f'<p style="margin:4px 0 0;"><strong>Factura No.:</strong> {factura}</p>'
            if self._mostrar_factura_correo(factura)
            else ''
        )
        return f"""
            <div style="background-color:#f8fafc; border-left:4px solid #043484; padding:10px 15px; margin:15px 0;">
                <p style="margin:0;"><strong>Contratista:</strong> {contratista}</p>
                <p style="margin:4px 0 0;"><strong>Proyecto:</strong> {proyecto}</p>
                <p style="margin:4px 0 0;"><strong>Contrato:</strong> {numero_contrato}</p>
                <p style="margin:4px 0 0;"><strong>Cuenta de cobro No.:</strong> {numero_pago}</p>
                {factura_html}
            </div>
        """

    def _format_valor_correo(self, valor):
        try:
            return "$ {:,.2f}".format(float(valor or 0.0)).replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            return "$ 0,00"

    def _generar_pdf_comprobante_pago_bytes(self):
        self.ensure_one()
        contratista, proyecto = self._datos_correo_cuenta()
        numero_pago, factura, numero_contrato, _proyecto = self._referencias_cuenta()
        valor_pagado = self._format_valor_correo(self.valor_neto_pagar)
        fecha_pago = fields.Date.context_today(self)
        fecha_pago_texto = fields.Date.to_string(fecha_pago)
        documento = self.documento_de_identificacion or "No registrado"
        periodo = "No registrado"
        if self.inicio_actividad and self.finalizacion_actividad:
            periodo = "%s a %s" % (
                fields.Date.to_string(self.inicio_actividad),
                fields.Date.to_string(self.finalizacion_actividad),
            )

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        left = 22 * mm
        right = width - (22 * mm)
        y = height - (18 * mm)
        blue = colors.HexColor("#043484")
        dark_blue = colors.HexColor("#002B4F")
        text_dark = colors.HexColor("#0f172a")
        text_muted = colors.HexColor("#475569")
        line_color = colors.HexColor("#cbd5e1")
        soft_bg = colors.HexColor("#f8fafc")
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static",
            "src",
            "img",
            "logo_fundacion_univalle_horizontal_color.jpg",
        )

        def wrapped_text(value, x, y_pos, max_chars=82, line_height=12):
            value = str(value or "")
            words = value.split()
            line = ""
            for word in words:
                next_line = f"{line} {word}".strip()
                if len(next_line) > max_chars and line:
                    pdf.drawString(x, y_pos, line)
                    y_pos -= line_height
                    line = word
                else:
                    line = next_line
            if line:
                pdf.drawString(x, y_pos, line)
                y_pos -= line_height
            return y_pos

        header_h = 58
        pdf.setStrokeColor(line_color)
        pdf.setLineWidth(0.8)
        pdf.roundRect(left, y - header_h, right - left, header_h, 8, stroke=1, fill=0)
        pdf.setFillColor(blue)
        pdf.rect(left, y - header_h, right - left, 16, stroke=0, fill=1)

        if os.path.exists(logo_path):
            try:
                logo = ImageReader(logo_path)
                logo_w, logo_h = logo.getSize()
                draw_w = 65 * mm
                draw_h = draw_w * (logo_h / float(logo_w or 1))
                if draw_h > 24 * mm:
                    draw_h = 24 * mm
                    draw_w = draw_h * (logo_w / float(logo_h or 1))
                pdf.drawImage(
                    logo,
                    left + 10,
                    y - 39,
                    width=draw_w,
                    height=draw_h,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                _logger.exception("No fue posible insertar el logo en el comprobante de pago.")

        pdf.setFillColor(dark_blue)
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawRightString(right - 12, y - 21, "COMPROBANTE DE PAGO")

        y -= header_h + 26
        card_h = 46
        pdf.setFillColor(soft_bg)
        pdf.roundRect(left, y - card_h + 12, right - left, card_h, 8, stroke=0, fill=1)
        pdf.setFillColor(text_muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(left + 12, y + 2, "Fecha de generación")
        pdf.setFillColor(text_dark)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left + 12, y - 13, fecha_pago_texto)

        pdf.setFillColor(text_muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawCentredString(width / 2, y + 2, "Estado")
        pdf.setFillColor(blue)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawCentredString(width / 2, y - 13, "PAGADO")

        pdf.setFillColor(text_muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawRightString(right - 12, y + 2, "Valor pagado")
        pdf.setFillColor(text_dark)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawRightString(right - 12, y - 13, valor_pagado)

        y -= 55
        pdf.setFillColor(text_dark)
        pdf.setFont("Helvetica", 10)
        y = wrapped_text(
            "Se certifica que la siguiente cuenta de cobro finalizó el proceso y quedó registrada como pagada.",
            left,
            y,
        )

        y -= 18
        table_x = left
        table_y = y
        table_width = right - left
        label_width = 44 * mm
        row_height = 18
        rows = [
            ("Proyecto", proyecto),
            ("Contratista / proveedor", contratista),
            ("Documento / NIT", documento),
            ("Cuenta de cobro No.", numero_pago or "Sin numero"),
            ("Contrato", numero_contrato or "No registrado"),
            ("Periodo de actividad", periodo),
        ]
        if self._mostrar_factura_correo(factura):
            rows.insert(4, ("Factura No.", factura))

        pdf.setStrokeColor(line_color)
        pdf.setLineWidth(0.7)
        pdf.setFillColor(blue)
        pdf.rect(table_x, table_y + 2, table_width, 18, stroke=0, fill=1)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(table_x + 7, table_y + 7, "Detalle del comprobante")
        for index, (label, value) in enumerate(rows):
            row_y = table_y - (index * row_height)
            if index % 2 == 0:
                pdf.setFillColor(soft_bg)
                pdf.rect(table_x, row_y - row_height, table_width, row_height, stroke=0, fill=1)
            pdf.line(table_x, row_y, table_x + table_width, row_y)
            pdf.line(table_x + label_width, row_y, table_x + label_width, row_y - row_height)
            pdf.setFillColor(text_dark)
            pdf.setFont("Helvetica-Bold", 8.5)
            pdf.drawString(table_x + 7, row_y - 12, str(label))
            pdf.setFont("Helvetica", 8.5)
            pdf.drawString(table_x + label_width + 7, row_y - 12, str(value or ""))
        pdf.line(table_x, table_y - (len(rows) * row_height), table_x + table_width, table_y - (len(rows) * row_height))
        pdf.line(table_x, table_y, table_x, table_y - (len(rows) * row_height))
        pdf.line(table_x + table_width, table_y, table_x + table_width, table_y - (len(rows) * row_height))

        y = table_y - (len(rows) * row_height) - 26
        pdf.setStrokeColor(line_color)
        pdf.line(left, y + 12, right, y + 12)
        pdf.setFillColor(text_muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(left, y, "Documento generado automáticamente por SGO para soporte de finalización del proceso.")
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawRightString(right, y, "Fundación Univalle")

        pdf.showPage()
        pdf.save()
        buffer.seek(0)

        nombre_archivo = "Comprobante_pago_cuenta_%s.pdf" % re.sub(r"[^A-Za-z0-9_-]+", "_", str(numero_pago or self.id))
        return nombre_archivo, buffer.getvalue()

    def _crear_pdf_comprobante_pago_correo(self):
        self.ensure_one()
        nombre_archivo, pdf_data = self._generar_pdf_comprobante_pago_bytes()
        return self.env["ir.attachment"]._drive_create_temp_download({
            "name": nombre_archivo,
            "type": "binary",
            "datas": base64.b64encode(pdf_data),
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "application/pdf",
        }, hours=1)

    def _url_portal_cuenta_cobro(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        if self.cuenta_id and self.cuenta_id.token:
            return_url = f"/public/cuentas_de_cobros/form/{self.cuenta_id.id}/{self.cuenta_id.token}"
            return f"{base_url}/public/login?return_url={quote(return_url, safe='')}"
        return f"{base_url}/public/login"

    def _enviar_correo_finalizacion_contratista(self):
        for record in self:
            if record.notificacion_finalizacion_contratista_enviada:
                continue

            email_destinatario = (
                record.correo_facturacion
                or (record.cuenta_id.email if record.cuenta_id and hasattr(record.cuenta_id, 'email') else False)
            )
            if not email_destinatario:
                _logger.warning(
                    "No se envió correo de finalización de cuenta de cobro %s porque no tiene correo del contratista.",
                    record.display_name,
                )
                continue

            contratista, proyecto = record._datos_correo_cuenta()
            numero_pago, factura, numero_contrato, _proyecto = record._referencias_cuenta()
            valor_pagado = record._format_valor_correo(record.valor_neto_pagar)
            url_portal = record._url_portal_cuenta_cobro()
            factura_html = (
                '<p style="margin:4px 0 0;"><strong>Factura No.:</strong> %s</p>'
                % escape(str(factura))
                if record._mostrar_factura_correo(factura)
                else ''
            )

            contenido_html = f"""
                <p>Su cuenta de cobro finalizó el proceso y quedó registrada como pagada.</p>
                <div style="background-color:#f8fafc; border-left:4px solid #043484; padding:10px 15px; margin:15px 0;">
                    <p style="margin:0;"><strong>Proyecto:</strong> {escape(str(proyecto or 'Proyecto sin nombre'))}</p>
                    <p style="margin:4px 0 0;"><strong>Contratista/proveedor:</strong> {escape(str(contratista or 'Contratista'))}</p>
                    <p style="margin:4px 0 0;"><strong>Cuenta de cobro No.:</strong> {escape(str(numero_pago or 'Sin número'))}</p>
                    {factura_html}
                    <p style="margin:4px 0 0;"><strong>Contrato:</strong> {escape(str(numero_contrato or 'No registrado'))}</p>
                    <p style="margin:4px 0 0;"><strong>Valor pagado:</strong> {escape(valor_pagado)}</p>
                </div>
                <p>Puede consultar el formulario y la información de la cuenta desde el portal.</p>
            """

            body_html = record._plantilla_correo_html(
                "Cuenta de cobro finalizada",
                contratista or 'Contratista',
                contenido_html,
                boton_url=url_portal,
                boton_texto="Ver formulario de cuenta de cobro",
            )

            mail_values = {
                'subject': record._asunto_correo_cuenta(f"Cuenta de cobro finalizada No. {numero_pago}"),
                'body_html': body_html,
                'email_to': email_destinatario,
                'email_from': self.env.user.email or 'no-reply@fundacionunivalle.org',
            }
            comprobante_pdf = False
            try:
                comprobante_pdf = record._crear_pdf_comprobante_pago_correo()
                if comprobante_pdf:
                    mail_values['attachment_ids'] = [(4, comprobante_pdf.id)]
            except Exception as error:
                _logger.warning(
                    "No se pudo generar el comprobante de pago en PDF para la cuenta %s: %s",
                    record.display_name,
                    error,
                    exc_info=True,
                )

            record._enviar_mail_con_traza(mail_values)
            if comprobante_pdf:
                comprobante_pdf.sudo().unlink()

            record.with_context(allow_finalized_write=True).sudo().write({
                'notificacion_finalizacion_contratista_enviada': True,
            })

    def _accion_listado_actas(self, destino):
        """Confirma el envío y vuelve al listado existente de cuentas de cobro."""
        accion_listado = self.env.ref('Matriz.action_acta_entrega_documentos').sudo().read()[0]
        accion_listado.update({'target': 'main'})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Cuenta de cobro enviada',
                'message': f'La cuenta de cobro fue enviada correctamente a {destino}.',
                'type': 'success',
                'sticky': False,
                'next': accion_listado,
            },
        }

    @_trazar_boton_correo
    def enviar_a_coordinador(self):
        enviado_a_juridica = False
        for record in self:
            record._validar_requisitos_para_avanzar(validar_cesion=False)
            record._validar_usuario_asignado(record.usuarios_contratacion, "Asignar Coordinador")
            record._share_users_to_drive(record.usuarios_contratacion, role='reader')
            observaciones = (record.observaciones or "").strip()
            if (
                record.cesion_derechos
                and record.cesion_derechos_id
                and record.estado_cesion_derechos != 'aprobada'
            ):
                if record.estado_cesion_derechos == 'no_aplica':
                    record.iniciar_flujo_cesion_derechos()
                    record.invalidate_recordset(['estado_cesion_derechos', 'usuarios_juridico_cesion'])
                if record.estado_cesion_derechos == 'pendiente_juridica':
                    juridicos = record.usuarios_juridico_cesion or record._usuarios_juridica_cesion()
                    if not juridicos:
                        raise UserError(_(
                            "No hay usuarios activos en el grupo Jurídica para aprobar la cesión de derechos."
                        ))
                    record.write({'observaciones': observaciones})
                    record._share_users_to_drive(juridicos, role='reader')
                    record._enviar_correo(
                        juridicos,
                        "Visto bueno de Jurídica - Cesión de derechos",
                        (
                            "El Asistente de coordinación asignó el Coordinador de la cuenta de cobro, "
                            "pero la cuenta requiere primero el visto bueno de Jurídica por cesión de derechos.\n\n"
                            "Por favor revise el documento de cesión y apruebe para continuar al Coordinador."
                        ),
                        observaciones,
                    )
                    enviado_a_juridica = True
                    continue
                faltantes_cesion = []
                record._validar_cesion_derechos_para_avance(faltantes_cesion)
                if faltantes_cesion:
                    raise UserError(
                        _(
                            "Antes de enviar al siguiente estado debes completar:\n\n- %s"
                        ) % "\n- ".join(faltantes_cesion)
                    )
            record.write({
                'estado': 'coordinador',
                'observaciones': observaciones,
            })
            mensaje = (
                f"Estimado/a {record.usuarios_contratacion.name},\n\n"
                f"Se le informa que la cuenta de cobro de '{record.name}' "
                f"ha sido enviada para su revisión y firma.\n\n"
                f"Por favor, ingrese al sistema, revise la información y realice la firma correspondiente.\n\n"
                f"Agradecemos su colaboración y atención a este proceso.\n\n"
            )
            record._enviar_correo(
                record.usuarios_contratacion,
                "Revisión y firma del coordinador - Cuenta de cobro",
                mensaje,
                observaciones
            )

        return self._accion_listado_actas('Coordinación')


    @_trazar_boton_correo
    def enviar_a_ventanilla(self):
        self._validar_plazo_envio_ventanilla()
        for record in self:
            record._validar_requisitos_para_avanzar()
            record._validar_usuario_asignado(record.usuarios_supervision, "Asignar Ventanilla")
            observaciones_ventanilla = (record.observaciones_ventanilla or "").strip()
            record.write({
                'estado': 'ventanilla',
                'observaciones_ventanilla': observaciones_ventanilla,
            })
            record._share_users_to_drive(record.usuarios_supervision, role='reader')
            nombres_ventanilla = ', '.join(record.usuarios_supervision.mapped('name'))
            mensaje = (
                f"Estimado/a {nombres_ventanilla},\n\n"
                f"Se le informa que la cuenta de cobro de '{record.name}' "
                f"ha sido remitida para su revisión y firma.\n\n"
                f"Por favor, acceda al sistema, revise la información y realice la firma correspondiente.\n\n"
                f"Agradecemos su colaboración y atención a este proceso.\n\n"
            )
            record._enviar_correo(
                record.usuarios_supervision,
                "Revisión y firma de Ventanilla - Cuenta de cobro",
                mensaje,
                observaciones_ventanilla
            )

        return self._accion_listado_actas('Ventanilla Única')


    @_trazar_boton_correo
    def enviar_a_director_financiero(self):
        for record in self:
            record._validar_requisitos_para_avanzar()
            record._validar_usuario_asignado(record.usuarios_contabilidad, "Asignar Contabilidad")
            # Ventanilla asigna Contabilidad antes de la firma del Director
            # Financiero. El acceso se entrega ahora; el correo a Contabilidad
            # continúa reservado para el envío posterior del Director.
            record._share_users_to_drive(record.usuarios_contabilidad, role='reader')
            firmantes = self.env.ref(
                'Matriz.group_firmante_director_financiero_certificacion'
            ).users.filtered(lambda user: user.active and user.email)
            if not firmantes:
                raise UserError(_(
                    "No hay un usuario activo con correo en el rol "
                    "'Firmante Director Financiero - Certificación conjunta'."
                ))
            if len(firmantes) > 1:
                raise UserError(_(
                    "Hay varios usuarios activos con correo en el rol "
                    "'Firmante Director Financiero - Certificación conjunta'. "
                    "Deje el rol asignado únicamente al usuario que debe firmar."
                ))
            record._share_users_to_drive(firmantes, role='reader')
            certificacion = record._certificacion_conjunta_actual()
            if certificacion:
                certificacion.sudo().with_context(
                    skip_auto_certificacion_conjunta=True,
                    skip_certificacion_guardado=True,
                ).write({
                    'nombre_financiera_id': False,
                    'fecha_financiera': False,
                    'firmado_finaciera': False,
                })
            record.write({'estado': 'director_financiero'})
            record._enviar_correo(
                firmantes,
                "Firma de certificación conjunta - Director Financiero",
                (
                    "La cuenta de cobro debe ser firmada por el Director Financiero "
                    "antes de continuar a Contabilidad.\n\n"
                    "Ingrese a la cuenta, abra la certificación conjunta y registre "
                    "nombre, fecha y firma. La persona de Contabilidad ya fue asignada "
                    "por Ventanilla Única."
                ),
                (record.observaciones_contabilidad or '').strip(),
            )
        return self._accion_listado_actas('Director Financiero')

    @_trazar_boton_correo
    def enviar_a_contabilidad(self):
        for record in self:
            if record.estado != 'director_financiero':
                raise UserError(_('La cuenta debe estar en la etapa de Director Financiero.'))
            if not (
                self.env.su
                or self.env.user.has_group('Matriz.group_firmante_director_financiero_certificacion')
            ):
                raise AccessError(_(
                    "Solo el usuario con el rol 'Firmante Director Financiero - "
                    "Certificación conjunta' puede enviar la cuenta a Contabilidad."
                ))
            record._validar_requisitos_para_avanzar()
            # La asignación fue realizada por Ventanilla. Se lee con sudo para
            # que las reglas de acceso a res.users del firmante financiero no
            # oculten al usuario ya seleccionado.
            usuarios_contabilidad = record.sudo().usuarios_contabilidad.filtered(
                lambda user: user.active
            )
            record._validar_usuario_asignado(
                usuarios_contabilidad,
                "Asignar Contabilidad",
            )
            # Revalidación idempotente del permiso antes de notificar.
            record._share_users_to_drive(usuarios_contabilidad, role='reader')
            observaciones_contabilidad = (record.observaciones_contabilidad or "").strip()
            record.write({
                'estado': 'finaciera',
                'observaciones_contabilidad': observaciones_contabilidad,
            })
            mensaje = (
                f"Estimado/a {', '.join(usuarios_contabilidad.mapped('name'))},\n\n"
                f"Se le informa que la cuenta de cobro de '{record.name}' "
                f"ha sido remitida al área de contabilidad para la carga de la documentación contable.\n\n"
                f"Por favor, acceda al sistema, revise la información y suba los documentos de contabilidad correspondientes.\n\n"
                f"Agradecemos su colaboración y atención a este proceso.\n\n"
            )
            record._enviar_correo(
                usuarios_contabilidad,
                "Documentación de contabilidad - Cuenta de cobro",
                mensaje,
                observaciones_contabilidad
            )

        return self._accion_listado_actas('Contabilidad')


    @_trazar_boton_correo
    def enviar_a_au_tesoreria(self):
        for record in self:
            if not record.causacion or not record.causacion_id:
                raise UserError(_(
                    "Debe cargar el documento de Causación (*) antes de enviar "
                    "la cuenta a Auxiliar de Tesorería. Los demás documentos "
                    "de Contabilidad son opcionales."
                ))
            record._validar_usuario_asignado(record.usuarios_auxiliar_tesoreria, "Asignar Auxiliar de Tesorería")
            record._share_users_to_drive(record.usuarios_auxiliar_tesoreria, role='reader')
            observaciones_aux_tesoreria = (record.observaciones_aux_tesoreria or "").strip()
            record.write({
                'estado': 'au_tesoreria',
                'observaciones_aux_tesoreria': observaciones_aux_tesoreria,
                'contabilidad_devuelta_desde_auxiliar': False,
            })
            mensaje = (
                f"Estimado/a {record.usuarios_auxiliar_tesoreria.name},\n\n"
                f"Se le informa que la cuenta de cobro de '{record.name}' "
                f"ha sido remitida para revisión, firma y carga de la orden de pago.\n\n"
                f"Por favor, acceda al sistema, revise la información, realice la firma correspondiente y suba la orden de pago.\n\n"
                f"Agradecemos su colaboración y atención a este proceso.\n\n"
            )
            record._enviar_correo(
                record.usuarios_auxiliar_tesoreria,
                "Revisión, firma y orden de pago de Auxiliar de tesorería - Cuenta de cobro",
                mensaje,
                observaciones_aux_tesoreria
            )

        return self._accion_listado_actas('Auxiliar de Tesorería')


    def action_open_envio_masivo_tesoreria_wizard(self):
        if not self:
            raise UserError(_("Seleccione al menos una cuenta de cobro."))

        if not self.env.user.has_group('Matriz.group_tesoreria_auxiliar') and not self.env.su:
            raise AccessError(_("Solo el Auxiliar de Tesorería puede usar esta acción masiva."))

        registros_invalidos = self.filtered(lambda record: record.estado != 'au_tesoreria')
        if registros_invalidos:
            nombres = ', '.join((registros_invalidos.mapped('display_name') or registros_invalidos.mapped('name'))[:5])
            raise UserError(_(
                "Solo puede enviar masivamente cuentas que estén en Auxiliar de Tesorería. "
                "Revise estos registros: %s"
            ) % nombres)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Enviar masivamente a Tesorería'),
            'res_model': 'acta.envio.masivo.tesoreria.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_acta_ids': [(6, 0, self.ids)],
            },
        }


    def action_open_firma_masiva_coordinador_wizard(self):
        if not self:
            raise UserError(_("Seleccione al menos una cuenta de cobro."))

        usuario = self.env.user
        if not usuario.has_group('Matriz.group_contratacion_coordinador') and not self.env.su:
            raise AccessError(_("Solo el Coordinador puede usar esta acción masiva."))

        registros_invalidos = self.filtered(
            lambda record: (
                record.estado != 'coordinador'
                or (not self.env.su and usuario not in record.sudo().usuarios_contratacion)
            )
        )
        if registros_invalidos:
            nombres = ', '.join((registros_invalidos.mapped('display_name') or registros_invalidos.mapped('name'))[:5])
            raise UserError(_(
                "Solo puede firmar masivamente cuentas que estén en Coordinador "
                "y asignadas a su usuario. Revise estos registros: %s"
            ) % nombres)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Firmar masivamente como Coordinador'),
            'res_model': 'acta.firma.masiva.coordinador.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_acta_ids': [(6, 0, self.ids)],
            },
        }


    def action_open_firma_masiva_director_financiero_wizard(self):
        if not self:
            raise UserError(_("Seleccione al menos una cuenta de cobro."))

        usuario = self.env.user
        if not usuario.has_group('Matriz.group_firmante_director_financiero_certificacion') and not self.env.su:
            raise AccessError(_("Solo el Director Financiero firmante puede usar esta acción masiva."))

        registros_invalidos = self.filtered(lambda record: record.estado != 'director_financiero')
        if registros_invalidos:
            nombres = ', '.join((registros_invalidos.mapped('display_name') or registros_invalidos.mapped('name'))[:5])
            raise UserError(_(
                "Solo puede firmar masivamente cuentas que estén en Director Financiero. "
                "Revise estos registros: %s"
            ) % nombres)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Firmar certificación masivamente'),
            'res_model': 'acta.firma.masiva.director.financiero.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_acta_ids': [(6, 0, self.ids)],
            },
        }


    @_trazar_boton_correo
    def enviar_a_tesoreria(self):
        for record in self:
            record._validar_requisitos_para_avanzar()
            record._validar_usuario_asignado(record.usuarios_tesoreria, "Asignar Tesorería")
            record._share_users_to_drive(record.usuarios_tesoreria, role='reader')
            observaciones_tesoreria = (record.observaciones_tesoreria or "").strip()
            record.write({
                'estado': 'tesorería',
                'observaciones_tesoreria': observaciones_tesoreria,
            })
            nombres_tesoreria = ', '.join(record.usuarios_tesoreria.mapped('name'))
            mensaje = (
                f"Estimado/a {nombres_tesoreria},\n\n"
                f"Se le informa que la cuenta de cobro de '{record.name}' "
                f"ha sido remitida a tesorería para la carga del comprobante de pago.\n\n"
                f"Por favor, acceda al sistema, revise la información y suba el comprobante de pago correspondiente.\n\n"
                f"Agradecemos su colaboración y atención a este proceso.\n\n"
            )
            record._enviar_correo(
                record.usuarios_tesoreria,
                "Revisión y subir comprobante de pago de Tesorería - Cuenta de cobro",
                mensaje,
                observaciones_tesoreria
            )

        return self._accion_listado_actas('Tesorería')


    def _enviar_mail_con_traza(self, mail_values):
        """Registra el resultado real de mail.send(), que puede fallar sin lanzar error."""
        _logger.info('[CUENTA_CORREO] CORREO_INICIO cuentas=%s destinatarios=%s',
                     self.ids, mail_values.get('email_to') or '(sin correo)')
        try:
            mail = self.env['mail.mail'].sudo().create(mail_values)
            mail_id = mail.id
            mail.send()
            remaining = mail.exists()
            if not remaining:
                _logger.info('[CUENTA_CORREO] CORREO_RESULTADO cuentas=%s mail=%s eliminado tras send; comprobar registro SMTP',
                             self.ids, mail_id)
                return True
            elif mail.state == 'sent':
                _logger.info('[CUENTA_CORREO] CORREO_ENVIADO cuentas=%s mail=%s estado=sent (no confirma recepción)',
                             self.ids, mail_id)
                return True
            else:
                _logger.warning('[CUENTA_CORREO] CORREO_NO_ENVIADO cuentas=%s mail=%s estado=%s motivo=%s',
                                self.ids, mail_id, mail.state, mail.failure_reason or '(sin detalle)')
                return False
        except Exception:
            _logger.exception('[CUENTA_CORREO] CORREO_ERROR cuentas=%s', self.ids)
            raise

    def _enviar_correo(self, usuario, asunto, mensaje, observacion):
        if not usuario:
            _logger.warning('[CUENTA_CORREO] CORREO_OMITIDO cuentas=%s sin usuarios destinatarios', self.ids)
        if usuario:
            email_to = ','.join(usuario.mapped('email')) if len(usuario) > 1 else usuario.email
            destinatario = ', '.join(usuario.mapped('name')) if len(usuario) > 1 else usuario.name
            if not email_to:
                _logger.warning('[CUENTA_CORREO] CORREO_OMITIDO cuentas=%s usuarios=%s sin correo', self.ids, usuario.ids)
                return
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            record_url = f"{base_url}/web#id={self.id}&model={self._name}&view_type=form"

            mensaje_html = self._limpiar_saludo_correo(mensaje).replace("\n", "<br>")
            observacion_final = (observacion or "").strip()
            titulo_observacion = "Observaciones"
            if "corregida y devuelta" in (asunto or "").lower():
                titulo_observacion = "Observaciones de la corrección"
            observacion_html = self._bloque_observacion_correo_html(
                observacion_final,
                titulo=titulo_observacion,
                destacado="corregida y devuelta" in (asunto or "").lower(),
            )

            body_html = self._plantilla_correo_html(
                asunto,
                destinatario,
                f"""
                    {self._contexto_correo_cuenta_html()}
                    <p>{mensaje_html}</p>
                    {observacion_html}
                """,
                boton_url=record_url,
                boton_texto="Ver cuenta de cobro",
            )

            mail_values = {
                'subject': self._asunto_correo_cuenta(asunto),
                'body_html': body_html,
                'email_to': email_to,
                'email_from': self.env.user.email or 'no-reply@tuempresa.com',
            }
            self._enviar_mail_con_traza(mail_values)

    def _devolver_directo_a_estado(self, estado_destino, etiqueta_destino, usuarios, observacion):
        self.ensure_one()
        observacion = (observacion or "").strip()
        if not observacion:
            raise UserError("Debes escribir una observación explicando el motivo de la devolución antes de continuar.")
        self._validar_usuario_asignado(usuarios, etiqueta_destino)
        self._share_users_to_drive(usuarios, role='reader')

        self.sudo().write({
            'estado': estado_destino,
            'volver': 'seleccionar',
            'flujo_en_devolucion': True,
        })

        mensaje = (
            f"Estimado/a {', '.join(usuarios.mapped('name'))},\n\n"
            f"La cuenta de cobro '{self.name}' fue devuelta directamente al estado de *{etiqueta_destino}* "
            f"para revisión y ajustes.\n\n"
            f"Por favor, ingrese al sistema y revise las observaciones registradas.\n\n"
        )
        self._enviar_correo(
            usuarios,
            f"Cuenta de cobro devuelta a {etiqueta_destino}",
            mensaje,
            observacion
        )
        self.sudo().message_post(
            body=_(
                "↩️ La cuenta de cobro fue devuelta directamente a <b>%s</b>.<br/><b>Observaciones:</b><br/>%s"
            ) % (etiqueta_destino, observacion.replace("\n", "<br>"))
        )

    def _action_volver_a_lista_actas(self):
        action = self.env.ref('Matriz.action_acta_entrega_documentos', raise_if_not_found=False)
        if action:
            accion_listado = action.sudo().read()[0]
            accion_listado.pop('res_id', None)
        else:
            accion_listado = {
                'type': 'ir.actions.act_window',
                'name': 'Acta entrega documentos',
                'res_model': 'acta_entrega_documentos',
                'view_mode': 'kanban,tree,form',
            }

        accion_listado.update({'target': 'main'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Cuenta de cobro devuelta',
                'message': 'La cuenta de cobro fue devuelta correctamente.',
                'type': 'success',
                'sticky': False,
                'next': accion_listado,
            },
        }

    @_trazar_boton_correo
    def devolver_directo_a_coordinador(self):
        for record in self:
            observacion = (
                record.observaciones_devolver_4
                if record.estado == 'coordinador'
                else record.observaciones_devolver
                if record.estado == 'ventanilla'
                else record.observaciones_devolver_1
                if record.estado == 'finaciera'
                else record.observaciones_devolver_2
                if record.estado == 'au_tesoreria'
                else record.observaciones_devolver_3
            )
            record._devolver_directo_a_estado('coordinador', 'Coordinador', record.usuarios_contratacion, observacion)
        return self._action_volver_a_lista_actas()

    @_trazar_boton_correo
    def devolver_directo_a_ventanilla(self):
        for record in self:
            observacion = (
                record.observaciones_devolver_1
                if record.estado == 'finaciera'
                else record.observaciones_devolver_2
                if record.estado == 'au_tesoreria'
                else record.observaciones_devolver_3
            )
            record._devolver_directo_a_estado('ventanilla', 'Ventanilla', record.usuarios_supervision, observacion)
        return self._action_volver_a_lista_actas()

    @_trazar_boton_correo
    def devolver_directo_a_contabilidad(self):
        for record in self:
            viene_de_auxiliar = record.estado == 'au_tesoreria'
            observacion = (
                record.observaciones_devolver_2
                if viene_de_auxiliar
                else record.observaciones_devolver_3
            )
            record._devolver_directo_a_estado('finaciera', 'Contabilidad', record.usuarios_contabilidad, observacion)
            if viene_de_auxiliar:
                record.sudo().write({'contabilidad_devuelta_desde_auxiliar': True})
        return self._action_volver_a_lista_actas()

    @_trazar_boton_correo
    def devolver_directo_a_auxiliar_tesoreria(self):
        for record in self:
            record._devolver_directo_a_estado(
                'au_tesoreria',
                'Auxiliar de tesorería',
                record.usuarios_auxiliar_tesoreria,
                record.observaciones_devolver_3
            )
        return self._action_volver_a_lista_actas()

    
    @_trazar_boton_correo
    def completado(self):
        self._validar_requisitos_para_avanzar()
            
        # Cambiamos el estado
        self.estado = 'finalizado'

        self._enviar_correo_finalizacion_contratista()

        # Agregamos el mensaje a la trazabilidad (chatter)
        mensaje = "✅ <b>Cuenta de cobro pagada.</b> El comprobante de pago ha sido adjuntado y el proceso ha finalizado exitosamente."
        self.with_context(allow_finalized_write=True).message_post(body=mensaje)


    @_trazar_boton_correo
    def volver_a_coodinador(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        for record in self:
            # --- 🛑 NUEVA VALIDACIÓN: Obligar a poner observación ---
            if not record.observaciones_devolver or not record.observaciones_devolver.strip():
                raise UserError("Debes escribir una observación explicando el motivo de la devolución antes de continuar.")
            # --- Cambiar estado ---
            record.sudo().write({
                'estado': 'asis_coordinador',
                'volver': 'volver_ventanilla',
                'flujo_en_devolucion': True,
            })

            # --- Construir la URL del registro ---
            record_url = f"{base_url}/web#id={record.id}&model={record._name}&view_type=form"

            # --- Obtener grupo y destinatarios ---
            group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
            if not group:
                raise UserError("No se encontró el grupo 'group_contratacion_asis_coordinador' en el sistema.")

            destinatarios = []
            destinatario_nombre = "asistente de coordinación"

            if record.users_with_access:
                for user in record.users_with_access:
                    if user in group.users and user.email:
                        destinatarios.append(user.email)
                        if destinatario_nombre == "asistente de coordinación":
                            destinatario_nombre = user.name  # nombre del primer usuario válido

            if not destinatarios:
                raise UserError("No se encontraron asistentes de coordinación con correo electrónico válido en los usuarios con acceso al proyecto.")

            # --- Construir correo ---
            subject = record._asunto_correo_cuenta("Devolución de cuenta de cobro")
            body_html = record._plantilla_correo_html(
                "Devolución de cuenta de cobro",
                destinatario_nombre,
                f"""
                    {record._contexto_correo_cuenta_html()}
                    <p>La cuenta de cobro de <strong>{record.name}</strong> ha sido devuelta para revisión.</p>
                    {record._bloque_observacion_correo_html(record.observaciones_devolver)}
                """,
                boton_url=record_url,
                boton_texto="Ver cuenta de cobro",
            )

            # --- Enviar correo ---
            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': ','.join(destinatarios),
                'email_from': record.env.user.email or 'no-reply@tuempresa.com',
            }
            record._enviar_mail_con_traza(mail_values)

            # Limpiar observaciones después de enviar
            record.sudo().write({'observaciones_devolver': False})

        return self._action_volver_a_lista_actas()


    volver = fields.Selection([
        ('seleccionar', 'Seleccionar'),
        ('volver_coordinador', 'Coordinador'),
        ('volver_ventanilla', 'Ventanilla'),
        ('volver_contabilidad', 'Contabilidad'),
        ('volver_tesoreria', 'Tesorería'),
        ('volver_auxiliar_de_tesoreria', ' Auxiliar de tesorería'),], default='seleccionar', string='Volver a')

    observaciones_devolver = fields.Text(string='Observaciones', tracking=True)
    observaciones_devolver_1 = fields.Text(string='Observaciones', tracking=True)
    observaciones_devolver_2 = fields.Text(string='Observaciones', tracking=True)
    observaciones_devolver_3 = fields.Text(string='Observaciones', tracking=True)
    observaciones_devolver_4 = fields.Text(string='Observaciones', tracking=True)
    flujo_en_devolucion = fields.Boolean(
        string='Flujo en devolución',
        default=False,
        copy=False,
    )


    observaciones_1 = fields.Text(string='Observaciones', tracking=True)
    observaciones_2 = fields.Text(string='Observaciones', tracking=True)
    observaciones_3 = fields.Text(string='Observaciones', tracking=True)
    observaciones_4 = fields.Text(string='Observaciones', tracking=True)
    observaciones_5 = fields.Text(string='Observaciones', tracking=True)

    def _reiniciar_firmas_para_nueva_revision_coordinador(self):
        """Deja pendiente nuevamente la firma de Coordinador y Ventanilla."""
        for record in self:
            certificacion = record._certificacion_conjunta_actual()
            if certificacion:
                certificacion.sudo().with_context(
                    skip_auto_certificacion_conjunta=True,
                    skip_certificacion_guardado=True,
                ).write({
                    'coordinador_nombre_id': False,
                    'cargo2': False,
                    'fecha_coordinador': False,
                    'firma_coordinador': False,
                    'firma_coordinador_name': False,
                    'firmado': False,
                    'firma_coordinador_hash': False,
                    'log_coordinador_id': False,
                    'nombre_recepcion_id': False,
                    'fecha_recepcion': False,
                    'firma_recepcion': False,
                    'firma_recepcion_name': False,
                    'firmado_ventanilla': False,
                    'firma_ventanilla_hash': False,
                    'log_ventanilla_id': False,
                })
            record._reset_vobo_informe_coordinador()

    @_trazar_boton_correo
    def boton_volver_a_coodinador(self):
        for record in self:
            record._validar_usuario_asignado(record.usuarios_contratacion, "Asignar Coordinador")
            record._share_users_to_drive(record.usuarios_contratacion, role='reader')
            record._reiniciar_firmas_para_nueva_revision_coordinador()
            record.estado = 'coordinador'
            record.volver = 'seleccionar'
            record.flujo_en_devolucion = False
            observacion_correccion = record._observacion_correccion_destino(
                'observaciones_5',
                'observaciones',
            )

            mensaje = (
                f"Estimado/a {record.usuarios_contratacion.name},\n\n"
                f"La cuenta de cobro '{record.name}' ha sido revisada y ajustada."
                f"Una vez efectuadas las correcciones necesarias, ha sido devuelto al estado de *Coordinador* "
                f"para continuar con el proceso correspondiente.\n\n"
                f"Por favor, ingrese al sistema y verifique que toda la información se encuentre en orden.\n\n"
                f"Agradecemos su atención y colaboración.\n\n"
            )
            record._enviar_correo(
                record.usuarios_contratacion,
                "Cuenta de cobro corregida y devuelta al estado de coordinador",
                mensaje,
                observacion_correccion
            )

        return self._accion_listado_actas('Coordinación')


    @_trazar_boton_correo
    def enviar_devolucion_a_coordinador(self):
        for record in self:
            record._validar_usuario_asignado(record.usuarios_contratacion, "Asignar Coordinador")
            record._share_users_to_drive(record.usuarios_contratacion, role='reader')
            if record.estado != 'asis_coordinador' or not record.flujo_en_devolucion:
                raise UserError("Esta acción solo aplica cuando la cuenta está en devolución con el Asistente de coordinación.")

            record._reiniciar_firmas_para_nueva_revision_coordinador()
            observacion_correccion = record._observacion_correccion_destino(
                'observaciones_5',
                'observaciones_1',
                'observaciones_2',
                'observaciones_3',
                'observaciones_4',
                'observaciones',
            )
            record.write({
                'estado': 'coordinador',
                'volver': 'seleccionar',
                'flujo_en_devolucion': False,
            })

            mensaje = (
                f"Estimado/a {record.usuarios_contratacion.name},\n\n"
                f"La cuenta de cobro '{record.name}' fue revisada por el Asistente de coordinación "
                f"después de una devolución y se envía al estado de *Coordinador* "
                f"para continuar con el proceso.\n\n"
                f"Por favor, ingrese al sistema y verifique la información.\n\n"
                f"Agradecemos su atención y colaboración.\n\n"
            )
            record._enviar_correo(
                record.usuarios_contratacion,
                "Cuenta de cobro enviada a coordinador después de devolución",
                mensaje,
                observacion_correccion
            )

        return self._accion_listado_actas('Coordinación')


    @_trazar_boton_correo
    def volver_a_ventanilla(self):
        self._validar_plazo_envio_ventanilla()
        for record in self:
            record._validar_usuario_asignado(record.usuarios_supervision, "Asignar Ventanilla")
            record._share_users_to_drive(record.usuarios_supervision, role='reader')
            record.estado = 'ventanilla'
            record.volver = 'seleccionar'
            record.flujo_en_devolucion = False
            observacion_correccion = record._observacion_correccion_destino(
                'observaciones_1',
                'observaciones_ventanilla',
                'observaciones',
            )
            
            mensaje = (
                f"Estimado/a {record.usuarios_supervision.name},\n\n"
                f"La cuenta de cobro '{record.name}' ha sido revisada y ajustada."
                f"Una vez efectuadas las correcciones necesarias, ha sido devuelto al estado de *Ventanilla* "
                f"para continuar con el proceso correspondiente.\n\n"
                f"Por favor, ingrese al sistema y verifique que toda la información se encuentre en orden.\n\n"
                f"Agradecemos su atención y colaboración.\n\n"
            )
            
            record._enviar_correo(
                record.usuarios_supervision,
                "Cuenta de cobro corregida y devuelta al estado de ventanilla",
                mensaje,
                observacion_correccion
            )


    @_trazar_boton_correo
    def volver_a_contabilidad(self):
        for record in self:
            record._validar_usuario_asignado(record.usuarios_contabilidad, "Asignar Contabilidad")
            record._share_users_to_drive(record.usuarios_contabilidad, role='reader')
            record.estado = 'finaciera'
            record.volver = 'seleccionar'
            record.flujo_en_devolucion = False
            observacion_correccion = record._observacion_correccion_destino(
                'observaciones_2',
                'observaciones_contabilidad',
                'observaciones',
            )
            
            mensaje = (
                f"Estimado/a {record.usuarios_contabilidad.name},\n\n"
                f"La cuenta de cobro '{record.name}' ha sido revisada y ajustada."
                f"Una vez efectuadas las correcciones necesarias, ha sido devuelto al estado de *Contabilidad* "
                f"para continuar con el proceso correspondiente.\n\n"
                f"Por favor, ingrese al sistema y verifique que toda la información se encuentre en orden.\n\n"
                f"Agradecemos su atención y colaboración.\n\n"
            )
            
            record._enviar_correo(
                record.usuarios_contabilidad,
                "Cuenta de cobro corregida y devuelta al estado de contabilidad",
                mensaje,
                observacion_correccion
            )
    
    
    @_trazar_boton_correo
    def volver_a_coodinador_2(self):
        for record in self:
            if not record.observaciones_devolver_1 or not record.observaciones_devolver_1.strip():
                raise UserError("Debes escribir una observación explicando el motivo de la devolución antes de continuar.")
            # URL base
            base_url = record.env['ir.config_parameter'].sudo().get_param('web.base.url')

            # Cambiar estado
            record.sudo().write({
                'estado': 'asis_coordinador',
                'volver': 'volver_contabilidad',
                'flujo_en_devolucion': True,
            })

            # URL del registro
            record_url = f"{base_url}/web#id={record.id}&model={record._name}&view_type=form"

            # --- Destinatarios: asistentes de coordinación con acceso ---
            destinatarios = []
            destinatario_nombre = "asistente de coordinación"

            group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
            if not group:
                raise UserError("No se encontró el grupo 'group_contratacion_asis_coordinador' en el sistema.")

            if record.users_with_access:
                for user in record.users_with_access:
                    if user in group.users and user.email:
                        destinatarios.append(user.email)
                        if destinatario_nombre == "asistente de coordinación":
                            destinatario_nombre = user.name  # nombre del primer usuario válido

            if not destinatarios:
                raise UserError("No se encontraron asistentes de coordinación con correo electrónico válido en los usuarios con acceso al proyecto.")

            # Observaciones (solo si existen y con formato HTML)
            observacion_html = ""
            if record.observaciones_devolver_1 and record.observaciones_devolver_1.strip():
                obs_texto = record.observaciones_devolver_1.strip().replace("\n", "<br>")
                observacion_html = f"""
                    <div style="background-color:#f9f9f9; border-left:4px solid #2563eb; padding:10px 15px; margin-top:15px; border-radius:6px;">
                        <p style="margin:0; color:#111827;">
                            <strong>Observaciones:</strong><br>{obs_texto}
                        </p>
                    </div>
                """

            # Construir correo (HTML)
            subject = record._asunto_correo_cuenta("Devolución de cuenta de cobro")
            body_html = record._plantilla_correo_html(
                "Devolución de cuenta de cobro",
                destinatario_nombre,
                f"""
                    {record._contexto_correo_cuenta_html()}
                    <p>La cuenta de cobro de <strong>{record.name}</strong> ha sido devuelta para revisión.</p>
                    {observacion_html}
                    <p>Por favor, revise el sistema y realice los ajustes necesarios.</p>
                """,
                boton_url=record_url,
                boton_texto="Ver cuenta de cobro",
            )

            # --- Enviar correo ---
            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': ','.join(destinatarios),
                'email_from': record.env.user.email or 'no-reply@tuempresa.com',
            }
            record._enviar_mail_con_traza(mail_values)

            # Limpiar observaciones después de enviar
            record.sudo().write({'observaciones_devolver_1': False})

        return self._action_volver_a_lista_actas()


    @_trazar_boton_correo
    def volver_a_auxiliar_tesoreria(self):
        for record in self:
            record._validar_usuario_asignado(record.usuarios_auxiliar_tesoreria, "Asignar Auxiliar de Tesorería")
            record._share_users_to_drive(record.usuarios_auxiliar_tesoreria, role='reader')
            record.estado = 'au_tesoreria'
            record.volver = 'seleccionar'
            record.flujo_en_devolucion = False
            observacion_correccion = record._observacion_correccion_destino(
                'observaciones_3',
                'observaciones_aux_tesoreria',
                'observaciones',
            )
            
            mensaje = (
                f"Estimado/a {record.usuarios_auxiliar_tesoreria.name},\n\n"
                f"La cuenta de cobro '{record.name}' ha sido revisada y ajustada."
                f"Una vez efectuadas las correcciones necesarias, ha sido devuelto al estado de *Auxiliar de tesorería* "
                f"para continuar con el proceso correspondiente.\n\n"
                f"Por favor, ingrese al sistema y verifique que toda la información se encuentre en orden.\n\n"
                f"Agradecemos su atención y colaboración.\n\n"
            )
            
            record._enviar_correo(
                record.usuarios_auxiliar_tesoreria,
                "Cuenta de cobro corregida y devuelta al estado de auxiliar de tesorería",
                mensaje,
                observacion_correccion
            )
    
    
    @_trazar_boton_correo
    def volver_a_coodinador_3(self):
        for record in self:
            if not record.observaciones_devolver_2 or not record.observaciones_devolver_2.strip():
                raise UserError("Debes escribir una observación explicando el motivo de la devolución antes de continuar.")
            # URL base
            base_url = record.env['ir.config_parameter'].sudo().get_param('web.base.url')

            # Cambiar estado
            record.sudo().write({
                'estado': 'asis_coordinador',
                'volver': 'volver_auxiliar_de_tesoreria',
                'flujo_en_devolucion': True,
            })

            # URL del registro
            record_url = f"{base_url}/web#id={record.id}&model={record._name}&view_type=form"

            # --- Destinatarios: asistentes de coordinación con acceso ---
            destinatarios = []
            destinatario_nombre = "asistente de coordinación"

            group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
            if not group:
                raise UserError("No se encontró el grupo 'group_contratacion_asis_coordinador' en el sistema.")

            if record.users_with_access:
                for user in record.users_with_access:
                    if user in group.users and user.email:
                        destinatarios.append(user.email)
                        if destinatario_nombre == "asistente de coordinación":
                            destinatario_nombre = user.name  # nombre del primer usuario válido

            if not destinatarios:
                raise UserError("No se encontraron asistentes de coordinación con correo electrónico válido en los usuarios con acceso al proyecto.")

            # Observaciones (solo si existen y con formato HTML)
            observacion_html = ""
            if record.observaciones_devolver_2 and record.observaciones_devolver_2.strip():
                obs_texto = record.observaciones_devolver_2.strip().replace("\n", "<br>")
                observacion_html = f"""
                    <div style="background-color:#f9f9f9; border-left:4px solid #2563eb; padding:10px 15px; margin-top:15px; border-radius:6px;">
                        <p style="margin:0; color:#111827;">
                            <strong>Observaciones:</strong><br>{obs_texto}
                        </p>
                    </div>
                """

            # Construir correo (HTML)
            subject = record._asunto_correo_cuenta("Devolución de cuenta de cobro")
            body_html = record._plantilla_correo_html(
                "Devolución de cuenta de cobro",
                destinatario_nombre,
                f"""
                    {record._contexto_correo_cuenta_html()}
                    <p>La cuenta de cobro de <strong>{record.name}</strong> ha sido devuelta para revisión.</p>
                    {observacion_html}
                    <p>Por favor, revise el sistema y realice los ajustes necesarios.</p>
                """,
                boton_url=record_url,
                boton_texto="Ver cuenta de cobro",
            )

            # --- Enviar correo ---
            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': ','.join(destinatarios),
                'email_from': record.env.user.email or 'no-reply@tuempresa.com',
            }
            record._enviar_mail_con_traza(mail_values)

            # Limpiar observaciones después de enviar
            record.sudo().write({'observaciones_devolver_2': False})

        return self._action_volver_a_lista_actas()


    @_trazar_boton_correo
    def volver_atesoreria(self):
        for record in self:
            record._validar_usuario_asignado(record.usuarios_tesoreria, "Asignar Tesorería")
            record._share_users_to_drive(record.usuarios_tesoreria, role='reader')
            record.estado = 'tesorería'
            record.volver = 'seleccionar'
            record.flujo_en_devolucion = False
            observacion_correccion = record._observacion_correccion_destino(
                'observaciones_4',
                'observaciones_tesoreria',
                'observaciones',
            )
            
            mensaje = (
                f"Estimado/a {record.usuarios_tesoreria.name},\n\n"
                f"La cuenta de cobro '{record.name}' ha sido revisada y ajustada."
                f"Una vez efectuadas las correcciones necesarias, ha sido devuelto al estado de *Tesorería* "
                f"para continuar con el proceso correspondiente.\n\n"
                f"Por favor, ingrese al sistema y verifique que toda la información se encuentre en orden.\n\n"
                f"Agradecemos su atención y colaboración.\n\n"
            )
            
            record._enviar_correo(
                record.usuarios_tesoreria,
                "Cuenta de cobro corregida y devuelta al estado de tesorería",
                mensaje,
                observacion_correccion
            )
    
    @_trazar_boton_correo
    def volver_a_coodinador_4(self):
        for record in self:
            if not record.observaciones_devolver_3 or not record.observaciones_devolver_3.strip():
                raise UserError("Debes escribir una observación explicando el motivo de la devolución antes de continuar.")
            # URL base
            base_url = record.env['ir.config_parameter'].sudo().get_param('web.base.url')

            # Cambiar estado
            record.sudo().write({
                'estado': 'asis_coordinador',
                'volver': 'volver_tesoreria',
                'flujo_en_devolucion': True,
            })

            # URL del registro
            record_url = f"{base_url}/web#id={record.id}&model={record._name}&view_type=form"

            # --- Destinatarios: asistentes de coordinación con acceso ---
            destinatarios = []
            destinatario_nombre = "asistente de coordinación"

            group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
            if not group:
                raise UserError("No se encontró el grupo 'group_contratacion_asis_coordinador' en el sistema.")

            if record.users_with_access:
                for user in record.users_with_access:
                    if user in group.users and user.email:
                        destinatarios.append(user.email)
                        if destinatario_nombre == "asistente de coordinación":
                            destinatario_nombre = user.name  # tomar el nombre del primer usuario válido

            if not destinatarios:
                raise UserError("No se encontraron asistentes de coordinación con correo electrónico válido en los usuarios con acceso al proyecto.")

            # Observaciones (solo si existen y con formato HTML)
            observacion_html = ""
            if record.observaciones_devolver_3 and record.observaciones_devolver_3.strip():
                obs_texto = record.observaciones_devolver_3.strip().replace("\n", "<br>")
                observacion_html = f"""
                    <div style="background-color:#f9f9f9; border-left:4px solid #2563eb; padding:10px 15px; margin-top:15px; border-radius:6px;">
                        <p style="margin:0; color:#111827;">
                            <strong>Observaciones:</strong><br>{obs_texto}
                        </p>
                    </div>
                """

            # Construir correo (HTML)
            subject = record._asunto_correo_cuenta("Devolución de cuenta de cobro")
            body_html = record._plantilla_correo_html(
                "Devolución de cuenta de cobro",
                destinatario_nombre,
                f"""
                    {record._contexto_correo_cuenta_html()}
                    <p>La cuenta de cobro de <strong>{record.name}</strong> ha sido devuelta para revisión.</p>
                    {observacion_html}
                    <p>Por favor, revise el sistema y realice los ajustes necesarios.</p>
                """,
                boton_url=record_url,
                boton_texto="Ver cuenta de cobro",
            )

            # --- Enviar correo ---
            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': ','.join(destinatarios),
                'email_from': record.env.user.email or 'no-reply@tuempresa.com',
            }
            record._enviar_mail_con_traza(mail_values)

            # Limpiar observaciones después de enviar
            record.sudo().write({'observaciones_devolver_3': False})

        return self._action_volver_a_lista_actas()


    @_trazar_boton_correo
    def volver_a_coodinador_5(self):
        for record in self:
            if not record.observaciones_devolver_4 or not record.observaciones_devolver_4.strip():
                raise UserError("Debes escribir una observación explicando el motivo de la devolución antes de continuar.")
            # URL base
            base_url = record.env['ir.config_parameter'].sudo().get_param('web.base.url')

            # Cambiar estado
            record.sudo().write({
                'estado': 'asis_coordinador',
                'volver': 'volver_coordinador',
                'flujo_en_devolucion': True,
            })

            # URL del registro
            record_url = f"{base_url}/web#id={record.id}&model={record._name}&view_type=form"

            # --- Obtener grupo de asistentes de coordinación ---
            group = self.env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
            if not group:
                raise UserError("No se encontró el grupo 'group_contratacion_asis_coordinador' en el sistema.")

            # --- Obtener destinatarios (y nombre del primero válido) ---
            destinatarios = []
            destinatario_nombre = "asistente de coordinación"
            if record.users_with_access:
                for user in record.users_with_access:
                    if user in group.users and user.email:
                        destinatarios.append(user.email)
                        if destinatario_nombre == "asistente de coordinación":
                            destinatario_nombre = user.name  # Tomamos el nombre del primer usuario válido

            if not destinatarios:
                raise UserError("No se encontraron asistentes de coordinación con correo electrónico válido en los usuarios con acceso al proyecto.")

            # --- Observaciones (solo si existen y con formato HTML) ---
            observacion_html = ""
            if record.observaciones_devolver_4 and record.observaciones_devolver_4.strip():
                obs_texto = record.observaciones_devolver_4.strip().replace("\n", "<br>")
                observacion_html = f"""
                    <div style="background-color:#f9f9f9; border-left:4px solid #2563eb; padding:10px 15px; margin-top:15px; border-radius:6px;">
                        <p style="margin:0; color:#111827;">
                            <strong>Observaciones:</strong><br>{obs_texto}
                        </p>
                    </div>
                """

            # --- Construir correo (HTML) ---
            subject = record._asunto_correo_cuenta("Devolución de cuenta de cobro")
            body_html = record._plantilla_correo_html(
                "Devolución de cuenta de cobro",
                destinatario_nombre,
                f"""
                    {record._contexto_correo_cuenta_html()}
                    <p>La cuenta de cobro de <strong>{record.name}</strong> ha sido devuelta para revisión.</p>
                    {observacion_html}
                    <p>Por favor, revise el sistema y realice los ajustes necesarios.</p>
                """,
                boton_url=record_url,
                boton_texto="Ver cuenta de cobro",
            )

            # --- Enviar correo ---
            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': ','.join(destinatarios),
                'email_from': record.env.user.email or 'no-reply@tuempresa.com',
            }
            record._enviar_mail_con_traza(mail_values)

            # Limpiar observaciones después de enviar
            record.sudo().write({'observaciones_devolver_4': False})

        return self._action_volver_a_lista_actas()


    def _get_finalizadas_user_domain(self):
        """Finalizadas y rechazadas vinculadas al proyecto o responsables del flujo."""
        user_id = self.env.uid
        participant_fields = (
            'users_with_access',
            'usuarios_contratacion',
            'usuarios_supervision',
            'usuarios_contabilidad',
            'usuarios_auxiliar_tesoreria',
            'usuarios_tesoreria',
            'usuarios_juridico_cesion',
        )
        participants = expression.OR([
            [(field_name, 'in', [user_id])] for field_name in participant_fields
        ] + [
            [('relcion_cer_conjunto_ids.coordinador_nombre_id', '=', user_id)],
            [('relcion_cer_conjunto_ids.nombre_recepcion_id', '=', user_id)],
            [('relcion_cer_conjunto_ids.nombre_financiera_id', '=', user_id)],
        ])
        return expression.AND([[('estado', 'in', ['finalizado', 'rechazado'])], participants])

    def _get_user_access_domain(self):
        user = self.env.user
        if (
            self.env.su
            or user.has_group('base.group_system')
            or user.has_group('Matriz.group_soporte')
        ):
            return []

        # Dirección Financiera necesita visión global de las cuentas para su
        # función de seguimiento. Esto amplía únicamente la visibilidad; los
        # permisos de edición se calculan aparte según el rol y el estado.
        if (
            user.has_group('Matriz.group_consulta_global_cuentas_cobro')
            or user.has_group('Matriz.group_contratacion_director_financiero')
        ):
            return []

        # El firmante sin el rol de Director Financiero solo necesita ver la
        # bandeja correspondiente a las certificaciones pendientes de firma.
        if user.has_group(
            'Matriz.group_firmante_director_financiero_certificacion'
        ):
            return [('estado', '=', 'director_financiero')]

        if user.has_group('Matriz.group_contratacion_financiero'):
            return expression.OR([
                [('estado', 'not in', ['finalizado', 'rechazado'])],
                self._get_finalizadas_user_domain(),
            ])

        domains = []
        group_asis_coordinador = self.env.ref(
            'Matriz.group_contratacion_asis_coordinador',
            raise_if_not_found=False,
        )
        group_coordinador = self.env.ref(
            'Matriz.group_contratacion_coordinador',
            raise_if_not_found=False,
        )
        group_juridico_cesion = self.env.ref(
            'Matriz.group_aprobador_cesion_derechos',
            raise_if_not_found=False,
        )

        # Un usuario puede tener varios roles. El acceso por proyecto no debe
        # ocultar las cuentas que le asignaron expresamente en otra etapa.
        if group_asis_coordinador and user in group_asis_coordinador.users:
            domains.append([
                '&',
                ('users_with_access', 'in', [user.id]),
                ('estado', '=', 'asis_coordinador'),
            ])

        if group_coordinador and user in group_coordinador.users:
            domains.append([
                '&',
                ('users_with_access', 'in', [user.id]),
                ('estado', 'in', ['asis_coordinador', 'coordinador']),
            ])
            domains.append([
                '&',
                ('usuarios_contratacion', 'in', [user.id]),
                ('estado_cesion_derechos', '=', 'pendiente_coordinador'),
            ])

        if group_juridico_cesion and user in group_juridico_cesion.users:
            domains.append([
                '&',
                ('usuarios_juridico_cesion', 'in', [user.id]),
                ('estado_cesion_derechos', '=', 'pendiente_juridica'),
            ])

        # Cada responsable conserva acceso de lectura cuando el flujo avanza;
        # la edición continúa limitada por _get_user_edit_domain.
        domains.extend([
            self._assigned_state_or_return_domain(
                'usuarios_contratacion',
                user.id,
                [
                    'coordinador', 'ventanilla', 'director_financiero',
                    'finaciera', 'au_tesoreria', 'tesorería', 'finalizado', 'rechazado',
                ],
            ),
            self._assigned_state_or_return_domain(
                'usuarios_supervision',
                user.id,
                [
                    'ventanilla', 'director_financiero', 'finaciera',
                    'au_tesoreria', 'tesorería', 'finalizado', 'rechazado',
                ],
            ),
            self._assigned_state_or_return_domain(
                'usuarios_contabilidad',
                user.id,
                [
                    'director_financiero', 'finaciera', 'au_tesoreria',
                    'tesorería', 'finalizado', 'rechazado',
                ],
            ),
            self._assigned_state_or_return_domain(
                'usuarios_auxiliar_tesoreria',
                user.id,
                ['au_tesoreria', 'tesorería', 'finalizado', 'rechazado'],
            ),
            self._assigned_state_or_return_domain(
                'usuarios_tesoreria',
                user.id,
                ['tesorería', 'finalizado', 'rechazado'],
            ),
            [('estado', '=', 'finalizado')],
            [('estado', '=', 'rechazado')],
        ])
        # Ningún permiso amplio de un rol debe abrir finalizadas o rechazadas ajenas.
        # Para todos los otros estados se conserva exactamente el dominio previo.
        return expression.OR([
            expression.AND([
                [('estado', 'not in', ['finalizado', 'rechazado'])],
                expression.OR(domains),
            ]),
            self._get_finalizadas_user_domain(),
        ])

    def _assigned_state_or_return_domain(self, user_field, user_id, estados):
        return [
            '&',
            (user_field, 'in', [user_id]),
            '|',
            ('estado', 'in', estados),
            '&',
            ('estado', '=', 'asis_coordinador'),
            ('flujo_en_devolucion', '=', True),
        ]

    def _assigned_state_domain(self, user_field, user_id, estados):
        return [
            '&',
            (user_field, 'in', [user_id]),
            ('estado', 'in', estados),
        ]

    def _drive_readonly_context(self):
        self.ensure_one()
        return {'force_drive_readonly': not self.puede_editar_estado_actual}

    def _get_user_edit_domain(self):
        user = self.env.user
        if (
            self.env.su
            or user.has_group('base.group_system')
            or user.has_group('Matriz.group_soporte')
        ):
            return []

        # "Consulta global" es un permiso adicional de lectura y no debe
        # anular un rol operativo. Si el usuario también pertenece al área
        # responsable de una etapa, conserva la edición de las cuentas que le
        # fueron asignadas; un usuario exclusivamente de consulta sigue en
        # modo de solo lectura.
        grupos_operativos = (
            'Matriz.group_contratacion_asis_coordinador',
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_ventanilla_unica',
            'Matriz.group_contabilidad',
            'Matriz.group_tesoreria_auxiliar',
            'Matriz.group_tesoreria',
            'Matriz.group_aprobador_cesion_derechos',
            'Matriz.group_firmante_director_financiero_certificacion',
        )
        if (
            user.has_group('Matriz.group_consulta_global_cuentas_cobro')
            and not any(user.has_group(group_xmlid) for group_xmlid in grupos_operativos)
        ):
            return [('id', '=', False)]

        group_asis = self.env.ref(
            'Matriz.group_contratacion_asis_coordinador',
            raise_if_not_found=False,
        )
        group_coordinador = self.env.ref(
            'Matriz.group_contratacion_coordinador',
            raise_if_not_found=False,
        )
        group_juridico_cesion = self.env.ref(
            'Matriz.group_aprobador_cesion_derechos',
            raise_if_not_found=False,
        )
        domains = []
        if group_asis and user in group_asis.users:
            domains.append([
                '&',
                ('users_with_access', 'in', [user.id]),
                ('estado', '=', 'asis_coordinador'),
            ])
        if group_coordinador and user in group_coordinador.users:
            domains.append([
                '&',
                ('usuarios_contratacion', 'in', [user.id]),
                ('estado_cesion_derechos', '=', 'pendiente_coordinador'),
            ])
        if group_juridico_cesion and user in group_juridico_cesion.users:
            domains.append([
                '&',
                ('usuarios_juridico_cesion', 'in', [user.id]),
                ('estado_cesion_derechos', '=', 'pendiente_juridica'),
            ])

        if user.has_group('Matriz.group_firmante_director_financiero_certificacion'):
            domains.append([('estado', '=', 'director_financiero')])

        domains.extend([
            self._assigned_state_domain('usuarios_contratacion', user.id, ['coordinador']),
            self._assigned_state_or_return_domain('usuarios_supervision', user.id, ['ventanilla']),
            self._assigned_state_or_return_domain('usuarios_contabilidad', user.id, ['finaciera']),
            self._assigned_state_or_return_domain('usuarios_auxiliar_tesoreria', user.id, ['au_tesoreria']),
            self._assigned_state_or_return_domain('usuarios_tesoreria', user.id, ['tesorería']),
        ])
        if not domains:
            return [('id', '=', False)]
        return expression.OR(domains)

    def _apply_user_access_domain(self, domain):
        user_domain = self._get_user_access_domain()
        if not user_domain:
            return domain
        return expression.AND([[('id', '!=', False)], domain or [], user_domain])

    # Sobrescribir el método search
    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        # Combinar el dominio con los filtros originales
        args = self._apply_user_access_domain(args)

        # La cola del firmante financiero se atiende en orden de llegada:
        # primero la cuenta que lleva más tiempo en Dirección Financiera.
        # Los demás roles conservan el orden normal y cualquier orden elegido
        # manualmente por el usuario sigue teniendo prioridad.
        if (
            not order
            and not count
            and self.env.user.has_group(
                'Matriz.group_firmante_director_financiero_certificacion'
            )
        ):
            order = 'create_date asc, id asc'

        return super(ActaEntregaDocumentos, self).search(args, offset=offset, limit=limit, order=order, count=count)

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        domain = self._apply_user_access_domain(domain)
        return super(ActaEntregaDocumentos, self).read_group(
            domain, fields, groupby, offset=offset, limit=limit, orderby=orderby, lazy=lazy
        )

    def check_access_rule(self, operation):
        """
        Regla de acceso simplificada: permite el acceso a cualquier usuario autenticado.
        """
        result = super(ActaEntregaDocumentos, self).check_access_rule(operation)
        
        # Superusuario o creación: siempre permitido
        if self.env.su or operation == 'create' or not self.ids:
            return result
        
        # 🔓 PERMITIR ACCESO A CUALQUIER USUARIO AUTENTICADO
        # Esto elimina COMPLETAMENTE el mensaje "No tienes permiso para abrir esta acta en el estado actual"
        if self.env.user and self.env.user.id:
            return result
        
        # Si por alguna razón el usuario no está autenticado, aplicar validación normal
        user_domain = (
            self._get_user_access_domain()
            if operation == 'read'
            else self._get_user_edit_domain()
        )
        if not user_domain:
            return result
        
        allowed_domain = expression.AND([[('id', 'in', self.ids)], user_domain])
        allowed_ids = set(super(ActaEntregaDocumentos, self).search(allowed_domain).ids)
        if set(self.ids) - allowed_ids:
            raise AccessError(_("No tienes permiso para abrir esta acta en el estado actual."))
        
        return result

    puede_editar_estado_actual = fields.Boolean(
        string='Puede editar en el estado actual',
        compute='_compute_puede_editar_estado_actual',
    )
    es_usuario_firmante_director_financiero = fields.Boolean(
        string='Es firmante Director Financiero',
        compute='_compute_es_usuario_firmante_director_financiero',
    )

    @api.depends_context('uid')
    def _compute_es_usuario_firmante_director_financiero(self):
        es_firmante = self.env.user.has_group(
            'Matriz.group_firmante_director_financiero_certificacion'
        )
        for record in self:
            record.es_usuario_firmante_director_financiero = es_firmante

    @api.depends_context('uid')
    @api.depends(
        'estado',
        'usuarios_contratacion',
        'usuarios_supervision',
        'usuarios_contabilidad',
        'usuarios_auxiliar_tesoreria',
        'usuarios_tesoreria',
        'usuarios_juridico_cesion',
        'estado_cesion_derechos',
    )
    def _compute_puede_editar_estado_actual(self):
        edit_domain = self._get_user_edit_domain()
        if not edit_domain:
            for record in self:
                record.puede_editar_estado_actual = True
            return

        editable_ids = set(self.filtered_domain(edit_domain).ids)
        for record in self:
            record.puede_editar_estado_actual = record.id in editable_ids

    # Campo para seleccionar usuarios con el rol específico de Contratación
    usuarios_contratacion = fields.Many2many(
        'res.users',
        string='Usuarios',
        relation='usuarios_contratacion_rel',  # Explicit relation table name
        domain=lambda self: self._get_usuarios_contratacion_domain(),
        help="Seleccione los usuarios que pertenecen al grupo 'Contratación'.",
    )

    def _get_usuarios_contratacion_domain(self):
        group = self.env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)
        if group and group.users:
            return [('share', '=', False), ('id', 'in', group.users.ids)]
        return [('id', '=', False)]  # Devuelve un dominio vacío si el grupo no existe


    # Campo para seleccionar usuarios con el rol específico de Supervisión
    usuarios_supervision = fields.Many2many(
        'res.users',
        string='Usuarios',
        relation='usuarios_supervision_rel',  # Explicit relation table name
        domain=lambda self: self._get_usuarios_supervision_domain(),
        help="Seleccione los usuarios que pertenecen al grupo 'Supervisión'.",
    )

    def _get_usuarios_supervision_domain(self):
        group = self.env.ref('Matriz.group_contratacion_ventanilla_unica', raise_if_not_found=False)
        if group and group.users:
            return [('id', 'in', group.users.ids)]
        return [('id', '=', False)]  # Devuelve un dominio vacío si el grupo no existe
    
    # Campo para seleccionar usuarios con el rol específico de Contabilidad
    usuarios_contabilidad = fields.Many2many(
        'res.users',
        string='Usuarios',
        relation='usuarios_contabilidad_rel',  # Explicit relation table name
        domain=lambda self: self._get_usuarios_contabilidad_domain(),
        help="Seleccione los usuarios que pertenecen al grupo 'Contabilidad'.",
    )

    def _get_usuarios_contabilidad_domain(self):
        group = self.env.ref('Matriz.group_contabilidad', raise_if_not_found=False)
        if group and group.users:
            return [('id', 'in', group.users.ids)]
        return [('id', '=', False)]  # Devuelve un dominio vacío si el grupo no existe


    usuarios_auxiliar_tesoreria = fields.Many2many(
        'res.users',
        string='Usuarios Auxiliar Tesorería',
        relation='usuarios_auxiliar_tesoreria_rel',
        domain=lambda self: self._get_usuarios_auxiliar_tesoreria_domain(),
        help="Usuarios que pertenecen al grupo 'Auxiliar Tesorería'.",
    )


    @api.depends_context('uid')
    def _get_usuarios_auxiliar_tesoreria_domain(self):
        group = self.env.ref('Matriz.group_tesoreria_auxiliar', raise_if_not_found=False)
        if group and group.users:
            return [('id', 'in', group.users.ids)]
        return [('id', '=', False)]  


    # Campo para seleccionar automáticamente los usuarios del grupo Tesorería
    usuarios_tesoreria = fields.Many2many(
        'res.users',
        string='Usuarios Tesorería',
        relation='usuarios_tesoreria_rel',
        domain=lambda self: self._get_usuarios_tesoreria_domain(),
        help="Usuarios que pertenecen al grupo 'Tesorería'.",
        store=True,
    )


    @api.depends_context('uid')
    def _get_usuarios_tesoreria_domain(self):
        group = self.env.ref('Matriz.group_tesoreria', raise_if_not_found=False)
        if group and group.users:
            return [('id', 'in', group.users.ids)]
        return [('id', '=', False)]  # Devuelve un dominio vacío si el grupo no existe

    usuarios_juridico_cesion = fields.Many2many(
        'res.users',
        string='Usuarios Jurídica Cesión de derechos',
        relation='usuarios_juridico_cesion_rel',
        domain=lambda self: self._get_usuarios_juridico_cesion_domain(),
        help="Usuarios de Jurídica que aprueban la cesión de derechos cuando aplica.",
        copy=False,
    )

    @api.depends_context('uid')
    def _get_usuarios_juridico_cesion_domain(self):
        group = self.env.ref('Matriz.group_aprobador_cesion_derechos', raise_if_not_found=False)
        if group and group.users:
            return [('id', 'in', group.users.ids)]
        return [('id', '=', False)]
    estado = fields.Selection(
        [('asis_coordinador', 'Asistente de coordinación'),('coordinador', 'Coordinador'), ('ventanilla', 'Ventanilla'), ('director_financiero', 'Director Financiero'), ('finaciera', 'Contabilidad'), ('au_tesoreria', 'Auxiliar de tesorería'), ('tesorería', 'Tesorería'), ('finalizado', 'Finalizado'), ('rechazado', 'Rechazado')],
        string='Estado',
        default='asis_coordinador',
        tracking=True
    )
    cesion_derechos = fields.Boolean(
        string='Cesión de derechos',
        tracking=True,
        copy=False,
        help='Marcar cuando la cuenta de cobro tiene cesión de derechos y requiere visto bueno de Jurídica y Coordinador.',
    )
    estado_cesion_derechos = fields.Selection(
        [
            ('no_aplica', 'No aplica'),
            ('pendiente_juridica', 'Pendiente Jurídica'),
            ('pendiente_coordinador', 'Pendiente Coordinador'),
            ('aprobada', 'Aprobada'),
        ],
        string='Estado cesión de derechos',
        default='no_aplica',
        tracking=True,
        copy=False,
    )
    fecha_solicitud_cesion_derechos = fields.Datetime(
        string='Fecha solicitud cesión de derechos',
        tracking=True,
        copy=False,
    )
    fecha_aprobacion_cesion_juridica = fields.Datetime(
        string='Fecha aprobación Jurídica cesión',
        tracking=True,
        copy=False,
    )
    fecha_aprobacion_cesion_coordinador = fields.Datetime(
        string='Fecha aprobación Coordinador cesión',
        tracking=True,
        copy=False,
    )
    usuario_aprobacion_cesion_juridica_id = fields.Many2one(
        'res.users',
        string='Aprobó Jurídica cesión',
        copy=False,
    )
    usuario_aprobacion_cesion_coordinador_id = fields.Many2one(
        'res.users',
        string='Aprobó Coordinador cesión',
        copy=False,
    )
    cesion_estado_retorno = fields.Selection(
        [
            ('asis_coordinador', 'Asistente de coordinación'),
            ('coordinador', 'Coordinador'),
            ('ventanilla', 'Ventanilla'),
            ('director_financiero', 'Director Financiero'),
            ('finaciera', 'Contabilidad'),
            ('au_tesoreria', 'Auxiliar de tesorería'),
            ('tesorería', 'Tesorería'),
            ('finalizado', 'Finalizado'),
        ],
        string='Estado principal al solicitar cesión',
        copy=False,
        help='Referencia interna para saber en qué etapa estaba la cuenta cuando se activó la cesión.',
    )
    fecha_estado_actual = fields.Datetime(
        string='Fecha entrada estado actual',
        tracking=True,
        copy=False,
    )
    fecha_envio_asis_coordinador = fields.Datetime(
        string='Fecha entrada Asistente de coordinación',
        tracking=True,
        copy=False,
    )
    fecha_envio_coordinador = fields.Datetime(
        string='Fecha envío a Coordinador',
        tracking=True,
        copy=False,
    )
    fecha_envio_ventanilla = fields.Datetime(
        string='Fecha envío a Ventanilla',
        tracking=True,
        copy=False,
    )
    fecha_envio_director_financiero = fields.Datetime(
        string='Fecha envío a Director Financiero',
        tracking=True,
        copy=False,
    )
    fecha_envio_contabilidad = fields.Datetime(
        string='Fecha envío a Contabilidad',
        tracking=True,
        copy=False,
    )
    fecha_envio_auxiliar_tesoreria = fields.Datetime(
        string='Fecha envío a Auxiliar de Tesorería',
        tracking=True,
        copy=False,
    )
    fecha_envio_tesoreria = fields.Datetime(
        string='Fecha envío a Tesorería',
        tracking=True,
        copy=False,
    )
    fecha_finalizacion = fields.Datetime(
        string='Fecha finalización',
        tracking=True,
        copy=False,
    )
    fecha_firma_director_financiero = fields.Date(
        string='Fecha firma Director Financiero',
        compute='_compute_fecha_firma_director_financiero',
        store=True,
        copy=False,
        help='Fecha registrada en la certificación conjunta vigente cuando está firmada por Dirección Financiera.',
    )

    @api.depends(
        'relcion_cer_conjunto_ids',
        'relcion_cer_conjunto_ids.fecha_financiera',
        'relcion_cer_conjunto_ids.firmado_finaciera',
    )
    def _compute_fecha_firma_director_financiero(self):
        for record in self:
            certificacion = record._certificacion_conjunta_actual()
            record.fecha_firma_director_financiero = (
                certificacion.fecha_financiera
                if certificacion and certificacion.firmado_finaciera
                else False
            )

    estado_kanban_display = fields.Char(
        string='Estado visible',
        compute='_compute_estado_kanban_display',
    )

    @api.depends(
        'estado',
        'bloqueo',
        'periodo_actividad_corregir',
        'correccion_portal_activa',
        'correccion_portal_fecha',
        'notificacion_enviada',
        'cesion_derechos',
        'estado_cesion_derechos',
    )
    def _compute_estado_kanban_display(self):
        estados = dict(self._fields['estado'].selection)
        estados_cesion = dict(self._fields['estado_cesion_derechos'].selection)
        for record in self:
            correccion_enviada = bool(record.correccion_portal_activa)
            pendiente_radicacion = (
                record.estado == 'asis_coordinador'
                and not record.notificacion_enviada
            )
            cesion_en_proceso = (
                record.cesion_derechos
                and record.estado_cesion_derechos
                and record.estado_cesion_derechos != 'no_aplica'
                and record.estado_cesion_derechos != 'aprobada'
            )
            record.estado_kanban_display = (
                'Corrigiendo'
                if correccion_enviada
                else 'Cesión: %s' % estados_cesion.get(record.estado_cesion_derechos, '')
                if cesion_en_proceso
                else 'En elaboración'
                if pendiente_radicacion
                else estados.get(record.estado, record.estado or '')
            )

    def _campos_fecha_por_estado(self):
        return {
            'asis_coordinador': 'fecha_envio_asis_coordinador',
            'coordinador': 'fecha_envio_coordinador',
            'ventanilla': 'fecha_envio_ventanilla',
            'director_financiero': 'fecha_envio_director_financiero',
            'finaciera': 'fecha_envio_contabilidad',
            'au_tesoreria': 'fecha_envio_auxiliar_tesoreria',
            'tesorería': 'fecha_envio_tesoreria',
            'finalizado': 'fecha_finalizacion',
            'rechazado': 'fecha_rechazo',
        }

    def _campo_fecha_por_estado(self, estado):
        return self._campos_fecha_por_estado().get(estado)

    def _valores_fechas_estado(self, estado, vals):
        """La entrada en una etapa siempre tiene fecha, incluso si llega False."""
        fecha_estado = vals.get('fecha_estado_actual') or fields.Datetime.now()
        fechas = {'fecha_estado_actual': fecha_estado}
        campo_fecha = self._campo_fecha_por_estado(estado)
        if campo_fecha:
            fechas[campo_fecha] = vals.get(campo_fecha) or fecha_estado
        return fechas

    def _recuperar_fechas_envio_desde_historial(self):
        """Completa huecos con eventos registrados, sin inventar fechas de envío."""
        etiquetas = dict(self._fields['estado'].selection)
        etiquetas_traducidas = dict(self._fields['estado']._description_selection(self.env))
        for estado, campo_fecha in self._campos_fecha_por_estado().items():
            valores_estado = tuple({
                estado, etiquetas[estado], etiquetas_traducidas.get(estado, etiquetas[estado]),
            })
            # El tracking de Selection guarda la etiqueta, no necesariamente
            # la clave técnica. También recuperamos fechas rastreadas directamente.
            self.env.cr.execute("""
                UPDATE acta_entrega_documentos AS acta
                   SET {campo_fecha} = historial.fecha
                  FROM (
                      SELECT mensaje.res_id AS acta_id,
                             MAX(CASE WHEN campo.name = 'estado' THEN mensaje.date
                                      ELSE cambio.new_value_datetime END) AS fecha
                        FROM mail_tracking_value AS cambio
                        JOIN mail_message AS mensaje ON mensaje.id = cambio.mail_message_id
                        JOIN ir_model_fields AS campo ON campo.id = cambio.field
                       WHERE mensaje.model = 'acta_entrega_documentos'
                         AND campo.model = 'acta_entrega_documentos'
                         AND (
                             (campo.name = 'estado' AND cambio.new_value_char IN %s)
                             OR (campo.name = %s AND cambio.new_value_datetime IS NOT NULL)
                         )
                       GROUP BY mensaje.res_id
                  ) AS historial
                 WHERE acta.id = historial.acta_id
                   AND acta.{campo_fecha} IS NULL
                   AND historial.fecha IS NOT NULL
            """.format(campo_fecha=campo_fecha), [valores_estado, campo_fecha])

        # La creación solo acredita la etapa inicial si la cuenta sigue allí
        # y no existe ningún cambio de estado. write_date no es una fecha de envío.
        self.env.cr.execute("""
            UPDATE acta_entrega_documentos AS acta
               SET fecha_envio_asis_coordinador = acta.create_date
             WHERE acta.estado = 'asis_coordinador'
               AND acta.fecha_envio_asis_coordinador IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM mail_tracking_value AS cambio
                   JOIN mail_message AS mensaje ON mensaje.id = cambio.mail_message_id
                   JOIN ir_model_fields AS campo ON campo.id = cambio.field
                   WHERE mensaje.model = 'acta_entrega_documentos'
                     AND mensaje.res_id = acta.id
                     AND campo.model = 'acta_entrega_documentos'
                     AND campo.name = 'estado'
               )
        """)
        for estado, campo_fecha in self._campos_fecha_por_estado().items():
            self.env.cr.execute("""
                UPDATE acta_entrega_documentos
                   SET fecha_estado_actual = {campo_fecha}
                 WHERE estado = %s AND fecha_estado_actual IS NULL
                   AND {campo_fecha} IS NOT NULL
            """.format(campo_fecha=campo_fecha), [estado])

    def _estado_origen_por_volver(self, volver):
        return {
            'volver_coordinador': 'coordinador',
            'volver_ventanilla': 'ventanilla',
            'volver_contabilidad': 'finaciera',
            'volver_auxiliar_de_tesoreria': 'au_tesoreria',
            'volver_tesoreria': 'tesorería',
        }.get(volver)

    def init(self):
        """Recupera fechas históricas y actualiza indicadores del flujo."""
        super().init()
        self._recuperar_fechas_envio_desde_historial()

        self.env.cr.execute("""
            UPDATE acta_entrega_documentos
               SET correccion_reporte_activa = TRUE,
                   correccion_reporte_fecha_solicitud = COALESCE(
                       correccion_reporte_fecha_solicitud,
                       fecha_estado_actual,
                       write_date,
                       create_date,
                       NOW()
                   ),
                   correccion_reporte_origen = COALESCE(
                       correccion_reporte_origen,
                       CASE volver
                           WHEN 'volver_coordinador' THEN 'coordinador'
                           WHEN 'volver_ventanilla' THEN 'ventanilla'
                           WHEN 'volver_contabilidad' THEN 'finaciera'
                           WHEN 'volver_auxiliar_de_tesoreria' THEN 'au_tesoreria'
                           WHEN 'volver_tesoreria' THEN 'tesorería'
                           ELSE NULL
                       END
                   )
             WHERE flujo_en_devolucion = TRUE
               AND COALESCE(correccion_reporte_activa, FALSE) = FALSE
        """)
        self.env.cr.execute("""
            UPDATE acta_entrega_documentos
               SET estado_programacion_pago = CASE
                   WHEN estado = 'finalizado' THEN 'Pago finalizado'
                   WHEN estado = 'tesorería' THEN 'En Tesorería para pago'
                   WHEN orden_de_pago_id IS NOT NULL AND fecha_programacion_pago IS NOT NULL THEN 'Orden de pago cargada - fecha tentativa registrada'
                   WHEN orden_de_pago_id IS NOT NULL THEN 'Orden de pago cargada - pendiente fecha tentativa de pago'
                   ELSE 'Sin orden de pago cargada'
               END
             WHERE estado_programacion_pago IS NULL
        """)
        self.env.cr.execute("""
            UPDATE acta_entrega_documentos
               SET correccion_reporte_en_manos_de = CASE estado
                   WHEN 'asis_coordinador' THEN 'Asistente de coordinación'
                   WHEN 'coordinador' THEN 'Coordinador'
                   WHEN 'ventanilla' THEN 'Ventanilla'
                   WHEN 'director_financiero' THEN 'Director Financiero'
                   WHEN 'finaciera' THEN 'Contabilidad'
                   WHEN 'au_tesoreria' THEN 'Auxiliar de tesorería'
                   WHEN 'tesorería' THEN 'Tesorería'
                   WHEN 'finalizado' THEN 'Finalizado'
                   WHEN 'rechazado' THEN 'Rechazado'
                   ELSE ''
               END
             WHERE correccion_reporte_activa = TRUE
               AND (correccion_reporte_en_manos_de IS NULL OR correccion_reporte_en_manos_de = '')
        """)
        self.env.cr.execute("""
            UPDATE acta_entrega_documentos
               SET seguimiento_correccion_reporte = CASE
                   WHEN correccion_reporte_activa = TRUE THEN
                       'En ' || COALESCE(NULLIF(correccion_reporte_en_manos_de, ''), 'Sin responsable')
                       || CASE
                           WHEN correccion_reporte_fecha_solicitud IS NOT NULL
                           THEN ' desde ' || to_char(correccion_reporte_fecha_solicitud, 'YYYY-MM-DD HH24:MI:SS')
                           ELSE ''
                       END
                   ELSE ''
               END
             WHERE seguimiento_correccion_reporte IS NULL
                OR seguimiento_correccion_reporte = ''
        """)
        self.env.cr.execute("""
            UPDATE acta_entrega_documentos acta
               SET pendiente_firma_coordinador = CASE
                   WHEN acta.estado = 'coordinador'
                    AND (
                        NOT EXISTS (
                            SELECT 1
                              FROM certificacion_cumplimiento cert
                             WHERE cert.relcion = acta.id
                               AND COALESCE(cert.firmado, FALSE) = TRUE
                        )
                        OR (
                            acta.clase_persona = 'persona_natural'
                            AND COALESCE(acta.informe_vobo_coordinador, FALSE) = FALSE
                        )
                    )
                   THEN TRUE
                   ELSE FALSE
               END
        """)
        self.env.cr.execute("""
            UPDATE acta_entrega_documentos acta
               SET pendiente_firma_director_financiero = CASE
                   WHEN acta.estado = 'director_financiero'
                    AND NOT EXISTS (
                        SELECT 1
                          FROM certificacion_cumplimiento cert
                         WHERE cert.relcion = acta.id
                           AND COALESCE(cert.firmado_finaciera, FALSE) = TRUE
                    )
                   THEN TRUE
                   ELSE FALSE
               END
        """)


    def unlink(self):
        es_asistente_coordinacion = self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
        es_soporte = self.env.user.has_group('Matriz.group_soporte')
        if not self.env.su and not (es_asistente_coordinacion or es_soporte):
            raise AccessError(_(
                "Solo el asistente de coordinación o soporte puede eliminar cuentas de cobro."
            ))

        for record in self:
            # Eliminar registros relacionados en relcion_cer_conjunto_ids
            if record.relcion_cer_conjunto_ids:
                record.relcion_cer_conjunto_ids.unlink()
            
            # Eliminar cuenta_id si existe
            if record.cuenta_id:
                record.cuenta_id.unlink()
            
            # Eliminar drive_stage_provedor_id si existe
            if record.drive_stage_provedor_id:
                record.drive_stage_provedor_id.unlink()
        
        return super().unlink()

    
    
    def action_open_certificacion_cumplimiento(self):
        self.ensure_one()  # Asegura que solo se procese un registro a la vez
    
        # Verifica si ya existe un registro relacionado con el mismo 'name' y 'relacion'
        existing_record = self.env['certificacion_cumplimiento'].search([
            ('name', '=', self.name),
            ('relcion', '=', self.id)
        ], limit=1)

        if existing_record:
            existing_record.completar_datos_regalias_si_vacios()
            existing_record.preparar_firmantes_asignados()
            if self.estado == 'director_financiero':
                existing_record.preparar_firmante_director_financiero()
            puede_editar = self.puede_editar_estado_actual and existing_record.puede_editar_usuario_actual()
            view_xmlid = (
                'Matriz.view_certificacion_cumplimiento_form'
                if puede_editar
                else 'Matriz.view_certificacion_cumplimiento_form_readonly'
            )
            # Si existe un registro relacionado, abre ese registro
            return {
                'type': 'ir.actions.act_window',
                'name': f'Certificación Conjunta - {existing_record.name}',
                'view_mode': 'form',
                'view_id': self.env.ref(view_xmlid).id,
                'res_model': 'certificacion_cumplimiento',
                'res_id': existing_record.id,  # Abre el registro existente
                'target': 'new',
                'context': {'form_view_initial_mode': 'edit' if puede_editar else 'readonly'},
            }
        else:
            if not self.puede_editar_estado_actual:
                raise AccessError(_("Esta cuenta de cobro está en modo de solo lectura para tu rol actual."))
            # Si no existe un registro relacionado, crea uno nuevo
            new_record = self.env['certificacion_cumplimiento'].create({
                'name': self.name,                 # Nombre igual al acta
                'relcion': self.id,                # Vinculación al acta (Many2one acta_entrega_documentos)
                'relcion_cobros': self.cuenta_id.id if self.cuenta_id else False  # Vinculación a la cuenta (Many2one cuentas_de_cobros)
            })
            puede_editar = self.puede_editar_estado_actual and new_record.puede_editar_usuario_actual()
            view_xmlid = (
                'Matriz.view_certificacion_cumplimiento_form'
                if puede_editar
                else 'Matriz.view_certificacion_cumplimiento_form_readonly'
            )
            return {
                'type': 'ir.actions.act_window',
                'name': f'Certificación Conjunta - {new_record.name}',
                'view_mode': 'form',
                'view_id': self.env.ref(view_xmlid).id,
                'res_model': 'certificacion_cumplimiento',
                'res_id': new_record.id,  # Abre el nuevo registro creado
                'target': 'new',
                'context': {'form_view_initial_mode': 'edit' if puede_editar else 'readonly'},
            }

    documento_de_identificacion = fields.Char(string="Documento de identificación/NIT")
    clase_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('seleccion_manual', 'Seleccione'),], string='Tipo de persona', tracking=True)

        
    def action_ver_contratacion_fuv(self):
        for record in self:
            contrataciones = self.env['contrataciones_fuv'].search([
                ('name', '=', record.name)
            ])

            if not contrataciones:
                raise UserError(f"No se encontró una contratación FUV con el nombre: {record.name}")

            # Relacionar solo el primero (opcional)
            record.contratacion_fuv_id = contrataciones[0].id

            return {
                'name': 'Contrataciones FUV Relacionadas',
                'type': 'ir.actions.act_window',
                'res_model': 'contrataciones_fuv',
                'view_mode': 'kanban,tree,form',
                'domain': [('id', 'in', contrataciones.ids)],
                'target': 'current',
            }


    contratacion_fuv_id = fields.Many2one(
        'contrataciones_fuv',
        string='Contratación FUV Relacionada',
        help='Contratación FUV con el mismo nombre que esta acta (relación directa).',
        tracking=True,
        
    )

    @api.model
    def copy(self, default=None):
        raise UserError("No se permite duplicar este registro.")

    relacion_fuv_id = fields.Many2one(
        'contrataciones_fuv_relacion',
         string='Contrato',
         related='cuenta_id.relacion_fuv_id',
    )

    relacion_orden_compra_servicios_fuv_id = fields.Many2one(
        'orden_compra_servicios',
        related='cuenta_id.relacion_orden_compra_servicios_fuv_id',
        string='Contrato servicio',
    )

    relacion_orden_compra_bienes_fuv_id = fields.Many2one(
        'orden_compra_bienes',
        related='cuenta_id.relacion_orden_compra_bienes_fuv_id',
        string='Contrato bines',
    )

    relacion_otrosi_fuv_id = fields.Many2one(
        'otrosi_bdd',
        related='cuenta_id.relacion_otrosi_fuv_id',
        string='Contrato otrosí',
    )

    lineas_ids = fields.One2many('orden.compra.linea', 'orden_id', string='Líneas', related='relacion_orden_compra_bienes_fuv_id.lineas_ids', readonly=True)


    
    id_proyecto3 = fields.Char(string='Id del proyecto', related='relacion_fuv_id.id_proyecto3')

    mcb_centro_costo = fields.Char(string='Centro de costo', compute='_compute_d_mcb_centro_costo')

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_d_mcb_centro_costo(self):
        for record in self:
            if record.origen_cuenta == 'regalias':
                record.mcb_centro_costo = record.regalias_centro_costo
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.mcb_centro_costo = record.relacion_fuv_id.mcb_centro_costo
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.mcb_centro_costo = record.relacion_orden_compra_bienes_fuv_id.centro_costo
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.mcb_centro_costo = record.relacion_orden_compra_servicios_fuv_id.centro_costo
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.mcb_centro_costo = record.relacion_otrosi_fuv_id.mcb_centro_costo
            else:
                record.mcb_centro_costo = False

    id_del_contrato = fields.Char(string='ID del contrato', tracking=True, compute='_compute_id_del_contrato')

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_id_del_contrato(self):
        for record in self:
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.id_del_contrato = record.relacion_fuv_id.id_del_contrato
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.id_del_contrato = record.relacion_otrosi_fuv_id.id_del_contrato
            else:
                record.id_del_contrato = False

    compromiso_presupuestal = fields.Char('RPC', tracking=True, compute='_compute_compromiso_presupuestal')

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_compromiso_presupuestal(self):
        for record in self:
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.compromiso_presupuestal = record.relacion_fuv_id.compromiso_presupuestal
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.compromiso_presupuestal = record.relacion_orden_compra_bienes_fuv_id.compromiso_presupuestal
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.compromiso_presupuestal = record.relacion_orden_compra_servicios_fuv_id.compromiso_presupuestal
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.compromiso_presupuestal = record.relacion_otrosi_fuv_id.compromiso_presupuestal
            else:
                record.compromiso_presupuestal = False

    ciudad_contratista = fields.Char(
        string='Ciudad de expedición', 
        compute='_compute_ciudad_contratista'
    )

    @api.depends(
        'origen_cuenta',
        'cuenta_id.ciudad_contratista',
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id',
    )
    def _compute_ciudad_contratista(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.cuenta_id:
                record.ciudad_contratista = record.cuenta_id.ciudad_contratista
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.ciudad_contratista = record.relacion_fuv_id.ciudad_contratista
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                # Ajuste para Many2one
                record.ciudad_contratista = record.relacion_orden_compra_bienes_fuv_id.ciudad.name if record.relacion_orden_compra_bienes_fuv_id.ciudad else False
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                # Ajuste para Many2one
                record.ciudad_contratista = record.relacion_orden_compra_servicios_fuv_id.ciudad.name if record.relacion_orden_compra_servicios_fuv_id.ciudad else False
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.ciudad_contratista = record.relacion_otrosi_fuv_id.ciudad_contratista
            else:
                record.ciudad_contratista = False


    fecha_iniciacion = fields.Date(
        'Fecha de iniciación',
        tracking=True,
        compute='_compute_fecha_iniciacion'
    )

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_otrosi_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_orden_compra_servicios_fuv_id')
    def _compute_fecha_iniciacion(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                record.fecha_iniciacion = record.proyecto_regalias_id.fecha_acta_inicio
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.fecha_iniciacion = record.relacion_fuv_id.fecha_iniciacion
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.fecha_iniciacion = record.relacion_orden_compra_bienes_fuv_id.fecha_inicio
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.fecha_iniciacion = record.relacion_orden_compra_servicios_fuv_id.fecha_inicio
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.fecha_iniciacion = record.relacion_otrosi_fuv_id.fecha_iniciacion
            else:
                record.fecha_iniciacion = False



    fecha_terminacion = fields.Date(
        'Fecha de terminación',
        tracking=True,
        compute='_compute_fecha_terminacion'
    )

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_otrosi_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_orden_compra_servicios_fuv_id')
    def _compute_fecha_terminacion(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                record.fecha_terminacion = record.proyecto_regalias_id.fecha_acta_fin
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.fecha_terminacion = record.relacion_fuv_id.fecha_terminacion
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.fecha_terminacion = record.relacion_orden_compra_bienes_fuv_id.fecha_fin
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.fecha_terminacion = record.relacion_orden_compra_servicios_fuv_id.fecha_fin
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.fecha_terminacion = record.relacion_otrosi_fuv_id.fecha_terminacion
            else:
                record.fecha_terminacion = False

    objeto_contrato = fields.Text(string='Objeto del contrato', tracking=True, related='cuenta_id.objeto_contrato')
    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto', compute='_compute_proyecto_relacionado', store=True)

    @api.depends('origen_cuenta', 'proyecto_regalias_id', 'selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_proyecto_relacionado(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                proyecto = record.proyecto_regalias_id
                contratacion = proyecto.contratacion_id
                if not contratacion:
                    mcb = proyecto.sudo().mcb_ids[:1]
                    contratacion = mcb.contrataciones_id if mcb else False
                record.proyecto_relacionado = contratacion
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.proyecto_relacionado = record.relacion_fuv_id.proyecto_relacionado
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.proyecto_relacionado = record.relacion_orden_compra_bienes_fuv_id.proyecto_relacionado_c
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.proyecto_relacionado = record.relacion_orden_compra_servicios_fuv_id.proyecto_relacionado_s
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.proyecto_relacionado = record.relacion_otrosi_fuv_id.proyecto_relacionado
            else:
                record.proyecto_relacionado = False

    descripcion_servicio = fields.Text(string='Descripción del servicio y/o porcentaje de avance:', tracking=True)
    total_pago_porcentaje = fields.Float(string="Total % de Pago", related='relacion_fuv_id.total_pago_porcentaje')
    dependencia_area_solicitante = fields.Selection(
        selection=[
            ('productividad', 'Productividad'),
            ('regalias', 'Regalías'),
            ('tecnologia', 'Tecnología'),
            ('estructuracion', 'Estructuración'),
            ('financiera', 'Financiera'),
            ('juridica', 'Jurídica'),
            ('administrativa', 'Administrativa'),
        ],
        string='Dependencia / Área de entrega',
        tracking=True, compute='_compute_dependencia_area_solicitante'
    )

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_dependencia_area_solicitante(self):
        for record in self:
            if record.origen_cuenta == 'regalias':
                record.dependencia_area_solicitante = 'regalias'
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.dependencia_area_solicitante = record.relacion_fuv_id.dependencia_area_solicitante
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.dependencia_area_solicitante = record.relacion_otrosi_fuv_id.dependencia_area_solicitante
            else:
                record.dependencia_area_solicitante = False


    departamentos_y_municipios_ids = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id', string='Tabla', compute='_compute_departamentos_y_municipios')

    @api.depends('selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_departamentos_y_municipios(self):
        for record in self:
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.departamentos_y_municipios_ids = record.relacion_fuv_id.departamentos_y_municipios_ids
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.departamentos_y_municipios_ids = record.relacion_orden_compra_bienes_fuv_id.departamento_municipio_ids
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.departamentos_y_municipios_ids = record.relacion_orden_compra_servicios_fuv_id.departamento_municipio_ids
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.departamentos_y_municipios_ids = record.relacion_otrosi_fuv_id.departamentos_y_municipios_ids
            else:
                record.departamentos_y_municipios_ids = False

    
    


    drive_stage_provedor_id = fields.Many2one('cuenta_de_cobros', string='Drive cuenta de cobros')
    regalias_drive_folder_id = fields.Many2one(
        'drive_folder',
        string='Carpeta base de Regalías',
        compute='_compute_regalias_drive_folder_id',
        readonly=True,
    )

    @api.depends(
        'proyecto_regalias_id',
        'proyecto_regalias_id.contratacion_id.drive_folder_id',
        'proyecto_regalias_id.mcb_ids.contrataciones_id.drive_folder_id',
    )
    def _compute_regalias_drive_folder_id(self):
        for record in self:
            proyecto = record.proyecto_regalias_id
            contratacion = proyecto.contratacion_id if proyecto else False
            if not contratacion and proyecto:
                mcb = proyecto.sudo().mcb_ids[:1]
                contratacion = mcb.contrataciones_id if mcb else False
            record.regalias_drive_folder_id = (
                contratacion.drive_folder_id if contratacion else False
            )
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())

    contratista_id = fields.Many2one(
        comodel_name='contratista_proveedor',
        string='Contratista/Proveedor',
        help='Seleccione el contratista o proveedor relacionado con este acta de entrega.'
    )

    cuenta_id = fields.Many2one(
        comodel_name='cuentas_de_cobros',
        string='Cuenta de cobro',
        help='Seleccione la cuenta de cobro relacionada con este acta de entrega.'
    )


    drive_file_id = fields.Many2one(
        'drive.file',
        string='Archivo de Drive',
        compute='_compute_drive_file_id',
        readonly=False,
    )

    otrosi_drive_file_id = fields.Many2one(
        'otrosi_drive',
        string='Archivo de Drive (Otrosí)',
        compute='_compute_otrosi_drive_file_id',
    )

    # =============== CREACIÓN ===============
    @api.model
    def create(self, vals):
        try:
            vals = dict(vals)
            if any(vals.get(field_name) for field_name in _CAMPOS_CORRECCION_DOCUMENTOS):
                vals.setdefault('bloqueo', True)

            estado_inicial = vals.get('estado') or self.default_get(['estado']).get('estado')
            vals.update(self._valores_fechas_estado(estado_inicial, vals))

            cuenta_id = vals.get('cuenta_id')
            if cuenta_id:
                cuenta = self.env['cuentas_de_cobros'].browse(cuenta_id)
                if cuenta:
                    if cuenta.relacion_fuv_id:
                        vals['relacion_fuv_id'] = cuenta.relacion_fuv_id.id
                    if cuenta.relacion_orden_compra_servicios_fuv_id:
                        vals['relacion_orden_compra_servicios_fuv_id'] = cuenta.relacion_orden_compra_servicios_fuv_id.id
                    if cuenta.relacion_orden_compra_bienes_fuv_id:
                        vals['relacion_orden_compra_bienes_fuv_id'] = cuenta.relacion_orden_compra_bienes_fuv_id.id
                    if cuenta.relacion_otrosi_fuv_id:
                        vals['relacion_otrosi_fuv_id'] = cuenta.relacion_otrosi_fuv_id.id

            record = super(ActaEntregaDocumentos, self).create(vals)

            if record.drive_file_id and record.otrosi_drive_file_id:
                raise UserError(_("No se puede asignar drive_file_id y otrosi_drive_file_id al mismo tiempo."))

            if record.drive_file_id or record.otrosi_drive_file_id or record.regalias_drive_folder_id:
                record._sync_with_drive_stage_provedor()
            else:
                _logger.warning(f"ActaEntregaDocumentos {record.id} creada sin carpeta base en Google Drive.")

            if vals.get('rpc_regalias_documento'):
                record._upload_rpc_regalias_to_drive()

            # Cada copia es independiente: un documento heredado sin ID
            # remoto no debe cancelar la creación del acta completa.
            if record.anexo_tributario_juridica_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_anexo_a_drive_proveedor', 'Anexo tributario jurídico')
            if record.anexo_tributario_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_anexo_a_drive_proveedor_general', 'Anexo tributario natural')
            if record.registro_de_proveedores_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_rpc_a_drive_proveedor', 'RPC natural')
            if record.registro_proveedores_juridica_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_rpc_juridica_a_drive_proveedor', 'RPC jurídico')
            if record.copia_cedula_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_cedula_a_drive_proveedor', 'Copia de cédula')
            if record.copia_contrato_juridica_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_contrato_juridica_a_drive_proveedor', 'Contrato jurídico')
            if record.copia_contrato_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_contrato_a_drive_proveedor', 'Contrato natural')
            if record.resolucion_facturacion_electronica_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_resolucion_facturacion_electronica_a_drive_proveedor', 'Resolución de facturación natural')
            if record.resolucion_facturacion_electronica_juridica_id and record.drive_stage_provedor_id:
                record._safe_copy_document_to_provider('action_copiar_resolucion_facturacion_electronica_juridica_a_drive_proveedor', 'Resolución de facturación jurídica')

            campos_asignacion_inicial = (
                set(vals) & set(record._DRIVE_ASSIGNMENT_FIELDS)
            )
            if campos_asignacion_inicial:
                record._trazar_asignaciones_y_compartir_drive({
                    record.id: {
                        field_name: []
                        for field_name in campos_asignacion_inicial
                    },
                })

        except Exception as e:
            _logger.error(f"Error al crear acta_entrega_documentos: {e}", exc_info=True)
            raise

        return record

    # =============== COMPUTE IDs ===============
    @api.depends('relacion_otrosi_fuv_id.drive_file_id')
    def _compute_otrosi_drive_file_id(self):
        for record in self:
            drive_file = record.relacion_otrosi_fuv_id.drive_file_id
            record.otrosi_drive_file_id = drive_file.id if drive_file else False

    @api.depends(
        'relacion_fuv_id.drive_file_id',
        'relacion_orden_compra_servicios_fuv_id.drive_file_id',
        'relacion_orden_compra_bienes_fuv_id.drive_file_id'
    )
    def _compute_drive_file_id(self):
        for record in self:
            drive_file = (
                record.relacion_fuv_id.drive_file_id
                or record.relacion_orden_compra_servicios_fuv_id.drive_file_id
                or record.relacion_orden_compra_bienes_fuv_id.drive_file_id
            )
            record.drive_file_id = drive_file.id if drive_file else False

    def _buscar_carpeta_cuenta_cobro_existente(self, nombre_carpeta=False):
        self.ensure_one()
        domain = [('contratistas_provedores', '=', self.id)]
        return self.env['cuenta_de_cobros'].sudo().search(
            domain,
            order='id desc',
            limit=1,
        )

    def _vincular_carpeta_cuenta_cobro_existente(self, cuenta):
        self.ensure_one()
        if cuenta and self.drive_stage_provedor_id != cuenta:
            self.write({'drive_stage_provedor_id': cuenta.id})
        return cuenta

    def _nombre_carpeta_cuenta_cobro(self):
        """Construye el nombre de la carpeta con los datos actuales de la cuenta."""
        self.ensure_one()
        fecha_cuenta = self.fecha_solicitud or fields.Date.context_today(self)
        meses_espanol = {
            1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
            5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
            9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
        }
        mes_anio = f"{meses_espanol[fecha_cuenta.month]} {fecha_cuenta.year}"
        tipos_dict = {
            'servicios': 'servicio',
            'bienes': 'bien',
            'contrato': 'Contrato',
            'otrosi': 'Otrosí',
            'regalias': 'Regalías',
        }
        tipo_documento = tipos_dict.get(self.selecionar, 'Cuenta de cobro')
        return f"{self.pago or 0} - {self.name} ({mes_anio}) - {tipo_documento}"

    def _actualizar_nombre_carpeta_cuenta_drive(self):
        """Renombra en sitio la carpeta ya vinculada, sin mover su contenido."""
        for contratista in self:
            carpeta = contratista.drive_stage_provedor_id
            if not carpeta or not carpeta.folder_id:
                continue

            nombre_nuevo = contratista._nombre_carpeta_cuenta_cobro()
            if carpeta.folder_name == nombre_nuevo:
                continue

            try:
                drive = GoogleDriveAuth.get_instance().get_drive()
                carpeta_drive = drive.CreateFile({'id': carpeta.folder_id})
                carpeta_drive.FetchMetadata(fields='id,title')
                carpeta_drive['title'] = nombre_nuevo
                carpeta_drive.Upload()
                carpeta.sudo().write({'folder_name': nombre_nuevo})
                _logger.info(
                    "Carpeta de cuenta de cobro %s renombrada a '%s'.",
                    carpeta.folder_id,
                    nombre_nuevo,
                )
            except Exception:
                # Una falla temporal de Drive no debe deshacer la corrección
                # guardada en la cuenta de cobro ni crear una carpeta duplicada.
                _logger.exception(
                    "No fue posible renombrar en Drive la carpeta %s de la cuenta %s.",
                    carpeta.folder_id,
                    contratista.cuenta_id.id,
                )

    def _obtener_o_crear_carpeta_cuentas_cobro(self, drive, parent_folder_id, nombre_carpeta=False):
        """Devuelve una única carpeta raíz 'Cuentas de cobro' por carpeta base.

        El advisory lock evita que dos workers de Odoo hagan simultáneamente
        el patrón buscar->no encontrar->crear y terminen generando dos carpetas.
        Si ya existen duplicados históricos, se reutiliza primero la carpeta que
        ya contiene la cuenta/pago actual; de lo contrario se elige una carpeta
        de forma estable y no se crean más duplicados.
        """
        self.ensure_one()

        lock_name = "Matriz:cuentas_de_cobro:%s" % parent_folder_id
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (lock_name,),
        )

        query_cc = (
            f"title='Cuentas de cobro' and '{parent_folder_id}' in parents "
            f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        existing_cc = drive.ListFile({"q": query_cc}).GetList()

        if existing_cc:
            # Si ya hay duplicados, conservar la continuidad de una cuenta que
            # ya tenga su carpeta de pago dentro de alguno de ellos.
            if nombre_carpeta:
                nombre_query = str(nombre_carpeta).replace("\\", "\\\\").replace("'", "\\'")
                for cc_folder in existing_cc:
                    cc_id = cc_folder.get('id')
                    if not cc_id:
                        continue
                    query_pago = (
                        f"title='{nombre_query}' and '{cc_id}' in parents "
                        f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
                    )
                    if drive.ListFile({"q": query_pago}).GetList():
                        if len(existing_cc) > 1:
                            _logger.warning(
                                "Se detectaron %s carpetas 'Cuentas de cobro' bajo %s. "
                                "Se reutiliza %s porque contiene la cuenta '%s'.",
                                len(existing_cc), parent_folder_id, cc_id, nombre_carpeta,
                            )
                        return cc_id

            # Selección estable para que todos los procesos posteriores reutilicen
            # siempre la misma carpeta aunque haya duplicados históricos.
            existing_cc = sorted(
                existing_cc,
                key=lambda item: (
                    item.get('createdDate') or item.get('modifiedDate') or '',
                    item.get('id') or '',
                ),
            )
            cuentas_cobro_folder_id = existing_cc[0]['id']
            if len(existing_cc) > 1:
                _logger.warning(
                    "Se detectaron %s carpetas duplicadas 'Cuentas de cobro' bajo %s. "
                    "Se usará siempre la carpeta %s y no se crearán nuevas.",
                    len(existing_cc), parent_folder_id, cuentas_cobro_folder_id,
                )
            else:
                _logger.info(
                    "📁 Carpeta 'Cuentas de cobro' ya existe (ID: %s)",
                    cuentas_cobro_folder_id,
                )
            return cuentas_cobro_folder_id

        cc_folder = drive.CreateFile({
            'title': 'Cuentas de cobro',
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': parent_folder_id}],
        })
        cc_folder.Upload()
        cuentas_cobro_folder_id = cc_folder['id']
        _logger.info(
            "📁 Carpeta 'Cuentas de cobro' creada una sola vez (ID: %s)",
            cuentas_cobro_folder_id,
        )
        return cuentas_cobro_folder_id

    # =============== CREACIÓN DE CARPETA BASE ===============
    def _sync_with_drive_stage_provedor(self):
        for contratista in self:
            if contratista.drive_file_id and contratista.otrosi_drive_file_id:
                raise UserError(_("El registro no puede tener drive_file_id y otrosi_drive_file_id al mismo tiempo."))

            es_regalias = contratista.origen_cuenta == 'regalias'
            drive_file = contratista.drive_file_id or contratista.otrosi_drive_file_id
            regalias_folder = contratista.regalias_drive_folder_id if es_regalias else False
            if not drive_file and not regalias_folder:
                raise UserError(_("Este registro no tiene carpeta base asignada."))

            if regalias_folder:
                parent_folder_id = regalias_folder.folder_guardar_id
            elif contratista.drive_file_id:
                parent_folder_id = drive_file.folder_guardar_id or drive_file.folder_id2
            else:  # otrosí
                parent_folder_id = drive_file.folder_guardar_id or drive_file.folder_id

            if not parent_folder_id:
                raise UserError(_("La carpeta base no tiene definido un Google Drive Folder ID."))

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            # El mes/año corresponde a la fecha de radicación de la cuenta.
            nombre_carpeta = contratista._nombre_carpeta_cuenta_cobro()
            cuenta_existente = contratista.drive_stage_provedor_id or contratista._buscar_carpeta_cuenta_cobro_existente(nombre_carpeta)
            if cuenta_existente and cuenta_existente.folder_id:
                contratista._vincular_carpeta_cuenta_cobro_existente(cuenta_existente)
                _logger.info(
                    "📁 Cuenta de cobro ya tenía carpeta vinculada (%s). No se crea duplicado.",
                    cuenta_existente.folder_id,
                )
                continue

            folder_metadata = {
                'title': nombre_carpeta,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': parent_folder_id}],
            }

            try:
                # Una sola ruta de creación evita duplicados por concurrencia.
                cuentas_cobro_folder_id = contratista._obtener_o_crear_carpeta_cuentas_cobro(
                    drive,
                    parent_folder_id,
                    nombre_carpeta=nombre_carpeta,
                )

                # Buscar o crear la carpeta del pago dentro de "Cuentas de cobro"
                folder_metadata['parents'] = [{'id': cuentas_cobro_folder_id}]
                query_pago = (
                    f"title='{nombre_carpeta}' and '{cuentas_cobro_folder_id}' in parents "
                    f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
                )
                existing_pago = drive.ListFile({"q": query_pago}).GetList()
                if existing_pago:
                    folder_id = existing_pago[0]['id']
                    _logger.info("📁 Carpeta de cuenta ya existe en Drive (ID: %s)", folder_id)
                else:
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()
                    folder_id = folder['id']

                vals_cuenta = {
                    'contratistas_provedores': contratista.id,
                    'folder_name': folder_metadata['title'],
                    'folder_id': folder_id,
                }
                if contratista.drive_file_id:
                    vals_cuenta['drive_id'] = drive_file.id
                elif contratista.otrosi_drive_file_id:
                    vals_cuenta['otrosi_drive_id'] = drive_file.id

                cuenta = contratista._buscar_carpeta_cuenta_cobro_existente(nombre_carpeta)
                if cuenta:
                    cuenta.write(vals_cuenta)
                else:
                    cuenta = self.env['cuenta_de_cobros'].create(vals_cuenta)
                contratista.write({'drive_stage_provedor_id': cuenta.id})

                _logger.info(f"✅ Carpeta '{folder_metadata['title']}' creada y vinculada.")

                # ======== COMPARTIR CON EL CORREO DE FACTURACIÓN (solo ver) ========
                if contratista.correo_facturacion:
                    try:
                        gfile = drive.CreateFile({'id': folder_id})
                        gfile.FetchMetadata()
                        gfile.InsertPermission({
                            'type': 'user',
                            'value': contratista.correo_facturacion,
                            'role': 'reader',
                        })
                        _logger.info(f"📤 Carpeta compartida (solo ver) con {contratista.correo_facturacion}")
                    except Exception as e_perm:
                        _logger.error(f"⚠ Error otorgando permiso: {e_perm}", exc_info=True)
                # =========================================================

            except Exception as e:
                _logger.error(f"Error creando carpeta en Google Drive: {e}", exc_info=True)
                raise UserError(_("No se pudo crear la carpeta en Google Drive: %s" % e))

    # =============== MÉTODO MANUAL CREAR CARPETA ===============
    def crear_carpeta(self):
        for contratista in self:
            if contratista.drive_file_id and contratista.otrosi_drive_file_id:
                raise UserError(_("El registro no puede tener drive_file_id y otrosi_drive_file_id al mismo tiempo."))

            es_regalias = contratista.origen_cuenta == 'regalias'
            drive_file = contratista.drive_file_id or contratista.otrosi_drive_file_id
            regalias_folder = contratista.regalias_drive_folder_id if es_regalias else False
            if not drive_file and not regalias_folder:
                raise UserError(_("Este registro no tiene carpeta base asignada."))

            if regalias_folder:
                parent_folder_id = regalias_folder.folder_guardar_id
            elif contratista.drive_file_id:
                parent_folder_id = drive_file.folder_guardar_id or drive_file.folder_id2
            else:
                parent_folder_id = drive_file.folder_guardar_id or drive_file.folder_id

            if not parent_folder_id:
                raise UserError(_("La carpeta base no tiene definido un Google Drive Folder ID."))

            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            # Formato aplicado también a la creación manual.
            nombre_carpeta = contratista._nombre_carpeta_cuenta_cobro()
            cuenta_existente = contratista.drive_stage_provedor_id or contratista._buscar_carpeta_cuenta_cobro_existente(nombre_carpeta)
            if cuenta_existente and cuenta_existente.folder_id:
                contratista._vincular_carpeta_cuenta_cobro_existente(cuenta_existente)
                _logger.info(
                    "📁 Cuenta de cobro ya tenía carpeta vinculada (%s). No se crea duplicado.",
                    cuenta_existente.folder_id,
                )
                continue

            folder_metadata = {
                'title': nombre_carpeta,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': parent_folder_id}],
            }

            try:
                # Una sola ruta de creación evita duplicados por concurrencia.
                cuentas_cobro_folder_id = contratista._obtener_o_crear_carpeta_cuentas_cobro(
                    drive,
                    parent_folder_id,
                    nombre_carpeta=nombre_carpeta,
                )

                # Buscar o crear la carpeta del pago dentro de "Cuentas de cobro"
                folder_metadata['parents'] = [{'id': cuentas_cobro_folder_id}]
                query_pago = (
                    f"title='{nombre_carpeta}' and '{cuentas_cobro_folder_id}' in parents "
                    f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
                )
                existing_pago = drive.ListFile({"q": query_pago}).GetList()
                if existing_pago:
                    folder_id = existing_pago[0]['id']
                    _logger.info("📁 Carpeta de cuenta ya existe en Drive (ID: %s)", folder_id)
                else:
                    folder = drive.CreateFile(folder_metadata)
                    folder.Upload()
                    folder_id = folder['id']

                vals_cuenta = {
                    'contratistas_provedores': contratista.id,
                    'folder_name': nombre_carpeta,
                    'folder_id': folder_id,
                }
                if contratista.drive_file_id:
                    vals_cuenta['drive_id'] = drive_file.id
                elif contratista.otrosi_drive_file_id:
                    vals_cuenta['otrosi_drive_id'] = drive_file.id

                cuenta = contratista._buscar_carpeta_cuenta_cobro_existente(nombre_carpeta)
                if cuenta:
                    cuenta.write(vals_cuenta)
                else:
                    cuenta = self.env['cuenta_de_cobros'].create(vals_cuenta)
                contratista.write({'drive_stage_provedor_id': cuenta.id})

                _logger.info(f"✅ Carpeta '{nombre_carpeta}' creada y vinculada.")

                if contratista.correo_facturacion:
                    try:
                        gfile = drive.CreateFile({'id': folder_id})
                        gfile.FetchMetadata()
                        gfile.InsertPermission({
                            'type': 'user',
                            'value': contratista.correo_facturacion,
                            'role': 'reader',
                        })
                        _logger.info(f"📤 Carpeta compartida (solo ver) con {contratista.correo_facturacion}")
                    except Exception as e_perm:
                        _logger.error(f"⚠ Error otorgando permiso: {e_perm}", exc_info=True)

            except Exception as e:
                _logger.error(f"Error creando carpeta en Google Drive: {e}", exc_info=True)
                raise UserError(_("No se pudo crear la carpeta en Google Drive: %s" % e))




    def render_informe_template(self):
        # Obtener la ruta del directorio actual
        module_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        templates_dir = os.path.join(module_dir, "templates")

        # Cargar la plantilla desde el archivo .xlsx utilizando openpyxl
        template_path = os.path.join(templates_dir, "ACTA ENTREGA DOC. VR2.xlsx")
        workbook = load_workbook(template_path)
        sheet = workbook.active  # Selecciona la hoja activa (en este caso, "ACTA Vr. 02")

        # --- FORMATEO DE PESOS COLOMBIANOS ---
        valor_cta = float(self.valor_cuenta or 0)
        valor_cta_formateado = f"$ {valor_cta:,.0f}".replace(",", ".")

        valor_contrato = float(self.valor_total_contrato or 0)
        valor_contrato_formateado = f"$ {valor_contrato:,.0f}".replace(",", ".")
        

        # Diccionario con los marcadores de posición y sus valores correspondientes
        placeholders = {
            "{{dependencia}}": str(self.dependencia or ""),
            "{{nombre_entrega}}": str(self.nombre_entrega or ""),
            "{{nombre_contratista}}": str(self.nombre_contratista or ""),
            "{{objeto_contrato}}": str(self.objeto_contrato or ""),
            "{{descripcion_servicio}}": str(self.descripcion_servicio or ""),
            "{{valor_cuenta}}": valor_cta_formateado,
            "{{valor_total_contrato}}": valor_contrato_formateado,
            "{{porcentaje_de_pago}}": str(self.porcentaje_de_pago or ""),
            "{{pago}}": str(self.pago or ""),
            "{{de}}": str(self.de or ""),
            "{{fecha_solicitud_dia}}": self.fecha_solicitud.strftime('%d') if self.fecha_solicitud else '',
            "{{fecha_solicitud_mes}}": self.fecha_solicitud.strftime('%m') if self.fecha_solicitud else '',
            "{{fecha_solicitud_anio}}": self.fecha_solicitud.strftime('%Y') if self.fecha_solicitud else '',
            "{{certificacion_conjunta}}": 'X' if self.certificacion_conjunta else '',
            "{{documento_equivalente}}": 'X' if self.documento_equivalente else '',
            "{{informe_de_actividades}}": 'X' if self.informe_de_actividades else '',
            "{{declaracion_juramentada_y_soportes}}": 'X' if self.declaracion_juramentada_y_soportes else '',
            "{{rut_actualizado}}": 'X' if self.rut_actualizado else '',
            "{{anexo_tributario}}": 'X' if self.anexo_tributario else '',
            "{{planilla_de_aportes_a_seguridad_social}}": 'X' if self.planilla_de_aportes_a_seguridad_social else '',
            "{{certificado_de_expensa}}": 'X' if self.certificado_de_expensa else '',
            "{{copia_contrato}}": 'X' if self.copia_contrato else '',
            "{{copia_cedula}}": 'X' if self.copia_cedula else '',
            "{{certificacion_bancaria}}": 'X' if self.certificacion_bancaria else '',
            "{{registro_de_proveedores}}": 'X' if self.registro_de_proveedores else '',
            "{{factura_electronica_juridica}}": 'X' if self.factura_electronica_juridica else '',
            "{{certificacion_conjunta_juridica}}": 'X' if self.certificacion_conjunta_juridica else '',
            "{{registro_proveedores_juridica}}": 'X' if self.registro_proveedores_juridica else '',
            "{{rut_actualizado_juridica}}": 'X' if self.rut_actualizado_juridica else '',
            "{{resolucion_facturacion_electronica_juridica}}": 'X' if self.resolucion_facturacion_electronica_juridica else '',
            "{{camara_comercio_juridica}}": 'X' if self.camara_comercio_juridica else '',
            "{{certificado_pago_parafiscales_juridica}}": 'X' if self.certificado_pago_parafiscales_juridica else '',
            "{{tarjeta_profesional_revisor_fiscal_juridica}}": 'X' if self.tarjeta_profesional_revisor_fiscal_juridica else '',
            "{{cedula_representante_legal_juridica}}": 'X' if self.cedula_representante_legal_juridica else '',
            "{{certificado_junta_contadores_juridica}}": 'X' if self.certificado_junta_contadores_juridica else '',
            "{{rut_representante_legal_juridica}}": 'X' if self.rut_representante_legal_juridica else '',
            "{{certificacion_bancaria_juridica}}": 'X' if self.certificacion_bancaria_juridica else '',
            "{{anexo_tributario_juridica}}": 'X' if self.anexo_tributario_juridica else '',
            "{{copia_contrato_juridica}}": 'X' if self.copia_contrato_juridica else '',
        }

        # Recorrer todas las celdas y reemplazar los marcadores de posición
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value and isinstance(cell.value, str):
                    for placeholder, value in placeholders.items():
                        if placeholder in cell.value:
                            cell.value = cell.value.replace(placeholder,
                                                            str(value))  # Asegúrate de que el valor sea una cadena

        # Guardar el resultado en un nuevo archivo .xlsx
        output_path = os.path.join(templates_dir, "ACTA_ENTREGA_RENDERED.xlsx")
        workbook.save(output_path)

        return output_path

    # ==========================================
    #   MÉTODO PARA GENERAR Y DESCARGAR EL XLSX
    # ==========================================
    def generar_informe_xlsx(self):
        self.ensure_one()
        template_rendered = self.render_informe_template()

        with open(template_rendered, 'rb') as f:
            file_data = base64.b64encode(f.read())

        filename = f"ACTA_ENTREGA_DOC_de{self.name.replace(' ', '_')}.xlsx"
        self.with_context(allow_finalized_write=True).write({
            'informe_adjunto': file_data,
            'informe_filename': filename,
        })

        # 🔽 Acción que genera la descarga directa
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self._name}/{self.id}/informe_adjunto/{filename}?download=true',
            'target': 'self',
        }

        
    
    informe_adjunto = fields.Binary("Informe XLSX", attachment=True)
    informe_filename = fields.Char("Nombre del archivo")
    name = fields.Char(string='Nombre', related="cuenta_id.name", tracking=True)
    # Datos generales
    dependencia = fields.Char(string='Dependencia / Área de entrega', tracking=True)

    # ──────────────────────────────────────────────────────────────
    # CHAT DE SOPORTE (portal ↔ asistente de coordinación)
    # ──────────────────────────────────────────────────────────────
    portal_chat_html = fields.Html(
        string='Chat de soporte',
        compute='_compute_portal_chat_html',
        sanitize=False,
    )
    portal_reply_text = fields.Text(string='Responder al contratista')
    portal_chat_rid = fields.Integer(
        string='RID chat',
        compute='_compute_portal_chat_rid',
    )

    def _compute_portal_chat_rid(self):
        for rec in self:
            rec.portal_chat_rid = rec.id or 0

    @api.depends('cuenta_id')
    def _compute_portal_chat_html(self):
        for rec in self:
            rec.portal_chat_html = ''
        return

        def esc(s):
            return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        for rec in self:
            login = rec.cuenta_id.login_id if rec.cuenta_id else False
            if not login:
                rec.portal_chat_html = (
                    '<div style="text-align:center;color:#bbb;padding:24px;font-size:13px;">'
                    'Sin mensajes todavía.'
                    '</div>'
                )
                continue
            messages = self.env['portal.chat.message'].sudo().search([('login_id', '=', login.id), ('cuenta_cobro_id', '=', rec.cuenta_id.id)])
            if not messages:
                rec.portal_chat_html = (
                    '<div style="text-align:center;color:#bbb;padding:24px;font-size:13px;">'
                    '¡Aún no hay mensajes! El contratista puede escribir desde el portal.'
                    '</div>'
                )
                continue
            parts = [
                '<div style="display:flex;flex-direction:column;gap:10px;'
                'padding:14px 16px;background:#f4f6fb;'
                'max-height:360px;overflow-y:auto;">'
            ]
            for m in messages:
                is_interno = m.sender_type == 'interno'
                align = 'flex-end' if is_interno else 'flex-start'
                bg = '#043484' if is_interno else '#e8efff'
                color = '#fff' if is_interno else '#1a2340'
                name_color = '#888' if is_interno else '#043484'
                br = 'border-bottom-right-radius:4px' if is_interno else 'border-bottom-left-radius:4px'
                date_str = m.create_date.strftime('%d/%m/%Y %H:%M') if m.create_date else ''
                parts.append(
                    f'<div style="display:flex;flex-direction:column;align-items:{align};">'
                    f'<span style="font-size:11px;font-weight:700;color:{name_color};margin-bottom:3px;">'
                    f'{esc(m.sender_name)}</span>'
                    f'<div style="background:{bg};color:{color};padding:10px 14px;border-radius:14px;'
                    f'{br};max-width:75%;word-break:break-word;font-size:13px;line-height:1.5;">'
                    f'{esc(m.body)}</div>'
                    f'<span style="font-size:10px;color:#aaa;margin-top:3px;">{date_str}</span>'
                    f'</div>'
                )
            parts.append('</div>')
            rec.portal_chat_html = ''.join(parts)

    def action_send_chat_reply(self):
        self.ensure_one()
        if not self.portal_reply_text or not self.portal_reply_text.strip():
            return
        # En producción los roles operativos pueden no tener lectura directa sobre
        # login/cuenta, aunque sí tengan acceso al acta desde la que usan el chat.
        acta = self.sudo()
        cuenta = acta.cuenta_id.sudo() if acta.cuenta_id else False
        login = cuenta.login_id.sudo() if cuenta and cuenta.login_id else False
        if not login:
            raise UserError(_("No hay usuario del portal asociado a esta cuenta de cobro."))
        self.env['portal.chat.message'].sudo().create({
            'login_id': login.id,
            'cuenta_cobro_id': cuenta.id,
            'sender_type': 'interno',
            'sender_name': self.env.user.name,
            'body': self.portal_reply_text.strip(),
        })
        self.write({'portal_reply_text': False})
        return False

    def action_refresh_chat(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @api.model
    def get_chat_messages_json(self, acta_id=None):
        if isinstance(acta_id, (list, tuple)):
            acta_id = acta_id[0] if acta_id else False
        if not acta_id:
            return []
        # El endpoint se invoca con la sesión del backend. Usamos sudo únicamente
        # para atravesar las relaciones técnicas necesarias para consultar el chat.
        acta = self.sudo().browse(int(acta_id))
        if not acta.exists():
            return []
        cuenta = acta.cuenta_id.sudo() if acta.cuenta_id else False
        login = cuenta.login_id.sudo() if cuenta and cuenta.login_id else False
        if not login:
            return []
        messages = self.env['portal.chat.message'].sudo().search(
            [('login_id', '=', login.id), ('cuenta_cobro_id', '=', cuenta.id)],
            order='create_date asc'
        )
        return [{
            'id': m.id,
            'sender_type': m.sender_type,
            'sender_name': m.sender_name or '',
            'body': m.body,
            'date': m.create_date.strftime('%d/%m/%Y %H:%M') if m.create_date else '',
        } for m in messages]

    nombre_entrega = fields.Char(string='Nombre de quien entrega', tracking=True, related='cuenta_id.nombre_entrega')
    nombre_contratista = fields.Char(string='Nombre del contratista', tracking=True)

    pago = fields.Integer(string='Pago N°', tracking=True, related='cuenta_id.pago')
    selecionar = fields.Selection([
        ('servicios', 'Orden de prestación de servicios'),
        ('bienes', 'Orden de compra de bienes'),
        ('contrato', 'Contrato'),
        ('otrosi', 'Otrosí'),
        ('regalias', 'Regalías'),
    ], string='Tipo', related='cuenta_id.selecionar', store=True)
    
    porcentaje_de_pago = fields.Float(
        string='Porcentajes de pagos:',
        tracking=True,
        compute='_compute_porcentaje_pago',
    )

    porcentaje_de_pago_individual = fields.Float(
        string='Porcentaje de pago individual',
        compute='_compute_porcentaje_de_pago_individual',
        digits=(16, 6),
    )

    @api.constrains(
        'valor_cuenta', 'pago', 'selecionar',
        'relacion_fuv_id', 'relacion_otrosi_fuv_id',
    )
    def _check_desembolso_seleccionado(self):
        """Mantiene en el acta el mismo tope elegido en la cuenta de cobro."""
        for record in self:
            if record.selecionar not in ('contrato', 'servicios', 'bienes', 'otrosi') or not record.cuenta_id:
                continue
            linea = record.cuenta_id._linea_desembolso_seleccionada()
            if record.selecionar in ('contrato', 'otrosi'):
                otrosi_pago_manual = record.cuenta_id._otrosi_pago_manual()
                if not linea and not otrosi_pago_manual:
                    raise ValidationError(
                        'Selecciona el desembolso que vas a cobrar en la tabla de Forma de pago.'
                    )
                if otrosi_pago_manual:
                    valor_maximo = record.cuenta_id._saldo_total_disponible_fuv()
                elif record.selecionar == 'contrato':
                    disponible = record.cuenta_id._detalle_disponible_pago_contrato_fuv(
                        record.relacion_fuv_id,
                        record.pago,
                        identificacion=record.documento_de_identificacion,
                        nombre=record.name,
                        exclude_id=record.cuenta_id.id,
                    )
                else:
                    disponible = next((
                        item for item in record.cuenta_id._forma_pago_otrosi_fuv(
                            record.relacion_otrosi_fuv_id,
                            exclude_id=record.cuenta_id.id,
                        )
                        if item['numero_de_pagos'] == int(record.pago or 0)
                    ), False)
                if not otrosi_pago_manual:
                    valor_maximo = disponible['valor_disponible'] if disponible else 0.0
                    if valor_maximo <= 0:
                        raise ValidationError(
                            'El desembolso seleccionado ya no tiene saldo disponible para cobrar.'
                        )
            else:
                valor_maximo = record.cuenta_id._saldo_total_disponible_fuv()
            if record.valor_cuenta > valor_maximo:
                raise ValidationError(
                    'El valor a cobrar no puede superar el saldo disponible '
                    '($ {:,.2f}).'.format(valor_maximo)
                )

    @api.depends(
        'valor_total_contrato', 'valor_cuenta', 'pago', 'selecionar',
        'relacion_fuv_id.tabla_de_forma_de_pago.pago',
        'relacion_fuv_id.tabla_de_forma_de_pago.cantidad_de_dinero',
        'relacion_fuv_id.tabla_de_forma_de_pago.numero_de_pagos',
        'relacion_otrosi_fuv_id.tabla_de_forma_de_pago.pago',
        'relacion_otrosi_fuv_id.tabla_de_forma_de_pago.cantidad_de_dinero',
        'relacion_otrosi_fuv_id.tabla_de_forma_de_pago.numero_de_pagos',
    )
    def _compute_porcentaje_de_pago_individual(self):
        for record in self:
            fuente = False
            if record.selecionar == 'contrato':
                fuente = record.relacion_fuv_id.sudo()
            elif record.selecionar == 'servicios':
                fuente = record.relacion_orden_compra_servicios_fuv_id.sudo()
            elif record.selecionar == 'bienes':
                fuente = record.relacion_orden_compra_bienes_fuv_id.sudo()
            elif record.selecionar == 'otrosi':
                fuente = record.relacion_otrosi_fuv_id.sudo()

            if fuente and 'tabla_de_forma_de_pago' in fuente._fields:
                lineas = fuente.tabla_de_forma_de_pago.sorted(
                    key=lambda linea: (linea.numero_de_pagos or 0, linea.id)
                )
                linea_pago = lineas.filtered(
                    lambda linea: linea.numero_de_pagos == record.pago
                )[:1]
                if (
                    linea_pago
                    and record.cuenta_id
                    and record.cuenta_id._valor_cuenta_es_desembolso_completo(linea_pago)
                ):
                    record.porcentaje_de_pago_individual = round(
                        linea_pago.pago or 0.0,
                        2,
                    )
                    continue

            # Respaldo para tipos de relación que no tienen tabla de forma de pago.
            total_respaldo = record.valor_total_contrato or 0.0
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.env.cr.execute(
                    """
                    SELECT COALESCE(SUM(valor_total_contrato), 0)
                      FROM otro_si
                     WHERE contratacion_fuv_en_id = %s
                    """,
                    (record.relacion_fuv_id.id,),
                )
                total_respaldo += record.env.cr.fetchone()[0] or 0.0

            if not total_respaldo:
                record.porcentaje_de_pago_individual = 0.0
                continue

            porcentaje = ((record.valor_cuenta or 0.0) / total_respaldo) * 100
            precision = 6 if record.origen_cuenta == 'regalias' else 2
            record.porcentaje_de_pago_individual = min(
                round(porcentaje, precision), 100.0
            )

    @api.depends('valor_total_contrato', 'valor_cuenta', 'pago', 'selecionar',
                'origen_cuenta', 'proyecto_regalias_id',
                'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id',
                'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_porcentaje_pago(self):
        for record in self:
            # Evitar calcular si el registro aún no existe en BD
            if not record.id or isinstance(record.id, models.NewId):
                record.porcentaje_de_pago = 0.0
                continue

            if not record.valor_total_contrato or record.valor_total_contrato == 0:
                record.porcentaje_de_pago = 0.0
                continue

            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                domain = [
                    ('id', '!=', record.id),
                    ('origen_cuenta', '=', 'regalias'),
                    ('proyecto_regalias_id', '=', record.proyecto_regalias_id.id),
                    ('pago', '<=', record.pago),
                    ('estado', '!=', 'rechazado'),
                ]
                pagos_relacionados = self.sudo().search(domain)
                total_pagado = sum(pagos_relacionados.mapped('valor_cuenta'))
                total_pagado += record.valor_cuenta or 0.0
                porcentaje_acumulado = (
                    total_pagado / record.valor_total_contrato
                ) * 100.0
                record.porcentaje_de_pago = min(
                    round(porcentaje_acumulado, 6), 100.0
                )
                continue

            if record.cuenta_id and record.selecionar in ('contrato', 'otrosi'):
                linea = record.cuenta_id._linea_desembolso_seleccionada()
                if (
                    linea
                    and linea.porcentaje_acumulado
                    and record.cuenta_id._valor_cuenta_es_desembolso_completo(linea)
                ):
                    record.porcentaje_de_pago = min(
                        round(linea.porcentaje_acumulado, 2), 100.0
                    )
                    continue

            # Detectar tipo de contrato
            contrato_field = None
            contrato_id = False

            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                contrato_field = 'relacion_fuv_id'
                contrato_id = record.relacion_fuv_id.id
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                contrato_field = 'relacion_orden_compra_servicios_fuv_id'
                contrato_id = record.relacion_orden_compra_servicios_fuv_id.id
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                contrato_field = 'relacion_orden_compra_bienes_fuv_id'
                contrato_id = record.relacion_orden_compra_bienes_fuv_id.id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                contrato_field = 'relacion_otrosi_fuv_id'
                contrato_id = record.relacion_otrosi_fuv_id.id

            if not contrato_field or not contrato_id:
                record.porcentaje_de_pago = 0.0
                continue

            # Buscar pagos anteriores solo si el registro ya existe
            domain = [
                ('id', '!=', record.id),
                ('selecionar', '=', record.selecionar),
                (contrato_field, '=', contrato_id),
                ('pago', '<=', record.pago),
                ('estado', '!=', 'rechazado'),
            ]

            pagos_relacionados = self.search(domain)

            total_pagado = sum(p.valor_cuenta for p in pagos_relacionados) + record.valor_cuenta
            porcentaje_acumulado = (total_pagado / record.valor_total_contrato) * 100
            record.porcentaje_de_pago = min(round(porcentaje_acumulado, 2), 100.0)

    de = fields.Integer(string='de', tracking=True)
    fecha_solicitud = fields.Date(string='Fecha de radicación', tracking=True, related='cuenta_id.fecha_solicitud')

    # Información del contrato

    currency_id = fields.Many2one('res.currency', string='Moneda')


    numero_factura = fields.Char(string='Número de factura', default='N/A', tracking=True, related='cuenta_id.numero_factura')


    

    def write(self, vals):
        campos_programacion_pago = {
            'habilitar_programacion_pago',
            'fecha_programacion_pago',
        }
        if campos_programacion_pago.intersection(vals) and not self.env.su:
            es_auxiliar = self.env.user.has_group(
                'Matriz.group_tesoreria_auxiliar'
            )
            es_tesoreria = self.env.user.has_group('Matriz.group_tesoreria')
            if not (es_auxiliar or es_tesoreria):
                raise AccessError(_(
                    'Solo el personal de Auxiliar de Tesorería o Tesorería '
                    'puede modificar la programación de pago.'
                ))
            if any(
                record.estado not in ('au_tesoreria', 'tesorería')
                for record in self
            ):
                raise AccessError(_(
                    'La programación de pago solo puede modificarse durante '
                    'las etapas de Auxiliar de Tesorería o Tesorería.'
                ))
        if 'consecutivo_orden_pago' in vals and not self.env.su:
            if not self.env.user.has_group('Matriz.group_tesoreria_auxiliar'):
                raise AccessError(_(
                    'Solo el personal de Auxiliar de Tesorería puede modificar '
                    'el consecutivo de la orden de pago.'
                ))
            if any(record.estado != 'au_tesoreria' for record in self):
                raise AccessError(_(
                    'El consecutivo de la orden de pago solo puede modificarse '
                    'durante la etapa de Auxiliar de Tesorería.'
                ))
        campos_descuentos = {'deducciones', 'otros_descuentos_cruces'}
        if campos_descuentos.intersection(vals) and not self.env.su:
            if not self.env.user.has_group('Matriz.group_contabilidad'):
                raise AccessError(_(
                    'Solo el personal de Contabilidad puede modificar las '
                    'retenciones y los otros descuentos o cruces.'
                ))
            if any(record.estado != 'finaciera' for record in self):
                raise AccessError(_(
                    'Las retenciones y los otros descuentos o cruces solo pueden '
                    'modificarse durante el proceso de Contabilidad.'
                ))
        if (
            not self.env.su
            and not self.env.user.has_group('Matriz.group_soporte')
            and not self.env.context.get('allow_finalized_write')
            and any(record.estado == 'finalizado' for record in self)
        ):
            raise AccessError(_(
                "La cuenta de cobro está finalizada y solo puede ser editada por el grupo de soporte."
            ))

        vals = dict(vals)
        if 'fecha_programacion_pago' in vals:
            vals.update({
                'alerta_pago_5_dias_enviada': False,
                'alerta_pago_3_dias_enviada': False,
                'alerta_pago_1_dia_enviada': False,
            })
        nuevo_estado = vals.get('estado')
        if nuevo_estado:
            sin_cambio = self.filtered(lambda record: record.estado == nuevo_estado)
            con_cambio = self - sin_cambio
            if sin_cambio and con_cambio:
                # Un envío masivo no debe cambiar la fecha de las cuentas que
                # ya estaban en el destino. Cada grupo conserva sus validaciones.
                con_cambio.write(dict(vals))
                sin_cambio.write(dict(vals))
                return True
        if nuevo_estado and any(record.estado != nuevo_estado for record in self):
            vals.update(self._valores_fechas_estado(nuevo_estado, vals))

        campos_asignacion = set(vals) & set(self._DRIVE_ASSIGNMENT_FIELDS)
        asignaciones_anteriores = {
            record.id: {
                field_name: record[field_name].ids
                for field_name in campos_asignacion
            }
            for record in self
        }

        inicia_devolucion = vals.get('flujo_en_devolucion') is True
        if inicia_devolucion:
            estado_origen = self._estado_origen_por_volver(vals.get('volver'))
            if not estado_origen and len(self) == 1:
                estado_origen = self.estado
            vals.setdefault('correccion_reporte_activa', True)
            vals.setdefault('correccion_reporte_fecha_solicitud', fields.Datetime.now())
            vals.setdefault('correccion_reporte_fecha_cierre', False)
            if estado_origen:
                vals.setdefault('correccion_reporte_origen', estado_origen)
        elif nuevo_estado:
            cierra_correccion = any(
                record.correccion_reporte_activa
                and record.correccion_reporte_origen
                and record.correccion_reporte_origen == nuevo_estado
                for record in self
            )
            if cierra_correccion:
                vals.setdefault('correccion_reporte_activa', False)
                vals.setdefault('correccion_reporte_fecha_cierre', fields.Datetime.now())

        documentos_persona_modificados = (
            set(vals) & _CAMPOS_DOCUMENTOS_PERSONA
        )
        if self.env.context.get('allow_cesion_derechos_portal'):
            documentos_persona_modificados -= _CAMPOS_CESION_DERECHOS_PORTAL
        registros_con_documentos_bloqueados = self.filtered(
            lambda record: record.estado in self._ESTADOS_INFORMACION_BLOQUEADA
        )
        if (
            documentos_persona_modificados
            and registros_con_documentos_bloqueados
            and not self.env.context.get('allow_person_document_write')
        ):
            raise AccessError(_(
                "Los documentos aportados por el contratista o proveedor no "
                "se pueden subir, reemplazar ni eliminar porque la cuenta ya "
                "se encuentra en proceso de Ventanilla, Contabilidad o Tesorería."
            ))
        campos_correccion_en_vals = any(
            field_name in vals
            for field_name in _CAMPOS_CORRECCION_DOCUMENTOS
        )
        if any(vals.get(field_name) for field_name in _CAMPOS_CORRECCION_DOCUMENTOS):
            vals.setdefault('bloqueo', True)

        campos_related_cuenta = set(vals) & self._CAMPOS_RELATED_CUENTA
        if campos_related_cuenta and not self.env.context.get('skip_related_cuenta_write'):
            for record in self:
                if not record.cuenta_id:
                    continue
                cuenta_vals = {
                    campo: vals[campo]
                    for campo in campos_related_cuenta
                    if campo in record.cuenta_id._fields
                }
                if cuenta_vals:
                    record.cuenta_id.sudo().with_context(
                        skip_acta_sync_from_cuenta=True,
                    ).write(cuenta_vals)
            for campo in campos_related_cuenta:
                vals.pop(campo, None)

        informacion_ya_bloqueada = any(
            record.informacion_bloqueada
            or record.estado in self._ESTADOS_INFORMACION_BLOQUEADA
            for record in self
        )
        permite_correccion_contractual = bool(
            self.env.context.get('allow_contractual_correction_write')
        )
        permite_total_pagos_manual = (
            'de' in vals
            and all(record.estado == 'asis_coordinador' for record in self)
        )
        if informacion_ya_bloqueada and not permite_correccion_contractual:
            for campo in self._CAMPOS_INFORMACION_CONTRACTUAL:
                # El Asistente de coordinacion completa este dato durante su
                # revision. Una cuenta devuelta puede conservar la marca
                # informacion_bloqueada; no debemos descartar silenciosamente
                # el numero escrito en ese caso.
                if campo == 'de' and permite_total_pagos_manual:
                    continue
                vals.pop(campo, None)

        if 'de' in vals and not self.env.context.get('skip_cuenta_de_sync'):
            for record in self:
                if record.cuenta_id:
                    record.cuenta_id.sudo().with_context(
                        skip_acta_sync_from_cuenta=True
                    ).write({'de': vals['de']})

        if vals.get('estado') in self._ESTADOS_INFORMACION_BLOQUEADA:
            vals = dict(vals, informacion_bloqueada=True)
        write_self = self
        if self._sgo_debe_silenciar_tracking_automatico(vals):
            write_self = self.with_context(mail_notrack=True, tracking_disable=True)
        res = super(ActaEntregaDocumentos, write_self).write(vals)
        if (
            asignaciones_anteriores
            and not self.env.context.get('skip_drive_assignment_trace')
        ):
            self._trazar_asignaciones_y_compartir_drive(asignaciones_anteriores)
        if campos_correccion_en_vals and not self.env.context.get('skip_sync_bloqueo_correcciones'):
            self._sincronizar_bloqueo_con_correcciones()
        if vals.get('certificacion_conjunta_documento'):
            self._upload_certificacion_conjunta_to_drive()
        if vals.get('certificacion_conjunta_firmado_documento'):
            self._upload_certificacion_conjunta_firmado_to_drive()
        # Si un intento anterior de Drive falló, el binario se conserva en el
        # registro. Un Guardar posterior debe volver a intentarlo aunque el
        # navegador ya no envíe el campo binario dentro de ``vals``.
        for record in self:
            if record.rpc_regalias_documento and not record.rpc_regalias_id:
                record._upload_rpc_regalias_to_drive()
        # Al limpiar el binario después de subirlo también llega este campo con
        # valor False. No se debe intentar decodificar/subir un valor vacío.
        if vals.get('documento_equivalente_documento'):
            self._upload_documento_equivalente_to_drive()
        if vals.get('informe_de_actividades_documento'):
            self._upload_informe_de_actividades_to_drive()
            if not self.env.context.get('skip_vobo_reset'):
                self._reset_vobo_informe_coordinador()
        if vals.get('rut_actualizado_documento'):
            self._upload_rut_actualizado_to_drive()
        if vals.get('anexo_tributario_documento'):
            self._upload_anexo_tributario_to_drive()
        if vals.get('planilla_de_aportes_a_seguridad_social_documento'):
            self._upload_planilla_de_aportes_a_seguridad_social_to_drive()
        if vals.get('certificado_de_expensa_documento'):
            self._upload_certificado_de_expensa_to_drive()
        if vals.get('copia_contrato_documento'):
            self._upload_copia_contrato_to_drive()
        if vals.get('copia_cedula_documento'):
            self._upload_copia_cedula_to_drive()
        if 'regalias_copia_contrato_documento' in vals:
            self._upload_regalias_copia_contrato_to_drive()
        if 'regalias_copia_cedula_documento' in vals:
            self._upload_regalias_copia_cedula_to_drive()
        if vals.get('certificacion_bancaria_documento'):
            self._upload_certificacion_bancaria_to_drive()
        if vals.get('registro_de_proveedores_documento'):
            self._upload_registro_de_proveedores_to_drive()
        if vals.get('resolucion_facturacion_electronica_documento'):
            self._upload_resolucion_facturacion_electronica_to_drive()
        if vals.get('factura_documento'):
            self._upload_factura_to_drive()
        if 'orden_de_pago_documento' in vals:
            self._upload_orden_de_pago_to_drive()
        if 'comprobante_de_pago_documento' in vals:
            self._upload_comprobante_de_pago_to_drive()

        # -------------------Sección para documentos P/J-------------------------
        if vals.get('factura_electronica_juridica_doc'):
            self._upload_factura_electronica_to_drive()
        if vals.get('certificacion_conjunta_juridica_documento'):
            self._upload_certificacion_conjunta_juridica_to_drive()
        if vals.get('certificacion_conjunta_firmado_juridica_documento'):
            self._upload_certificacion_conjunta_firmado_juridica_to_drive()
        if vals.get('registro_proveedores_juridica_documento'):
            self._upload_registro_proveedores_juridica_to_drive()
        if vals.get('rut_actualizado_juridica_documento'):
            self._upload_rut_actualizado_juridica_to_drive()
        if vals.get('resolucion_facturacion_electronica_juridica_documento'):
            self._upload_resolucion_facturacion_electronica_juridica_to_drive()
        if vals.get('camara_comercio_juridica_documento'):
            self._upload_camara_comercio_juridica_to_drive()
        if vals.get('certificado_pago_parafiscales_juridica_documento'):
            self._upload_certificado_pago_parafiscales_juridica_to_drive()
        if vals.get('tarjeta_profesional_revisor_fiscal_juridica_documento'):
            self._upload_tarjeta_profesional_revisor_fiscal_juridica_to_drive()
        if vals.get('cedula_representante_legal_juridica_documento'):
            self._upload_cedula_representante_legal_juridica_to_drive()
        if vals.get('cedula_representante_legal_de_contador_juridica_documento'):
            self._upload_cedula_representante_legal_de_contador_juridica_to_drive()
        if vals.get('cedula_revisor_fiscal_juridica_documento'):
            self._upload_cedula_revisor_fiscal_juridica_to_drive()
        if vals.get('Tarjeta_profesional_contador_juridica_documento'):
            self._upload_Tarjeta_profesional_contador_juridica_to_drive()
        if vals.get('certificado_junta_contadores_juridica_documento'):
            self._upload_certificado_junta_contadores_juridica_to_drive()
        if vals.get('rut_representante_legal_juridica_documento'):
            self._upload_rut_representante_legal_juridica_to_drive()
        if vals.get('certificacion_bancaria_juridica_documento'):
            self._upload_certificacion_bancaria_juridica_to_drive()
        if vals.get('anexo_tributario_juridica_documento'):
            self._upload_anexo_tributario_juridica_to_drive()
        if vals.get('copia_contrato_juridica_documento'):
            self._upload_copia_contrato_juridica_to_drive()
        if vals.get('cedula_de_contador_juridica_documento'):
            self._upload_cedula_de_contador_juridica_to_drive()
        if vals.get('planilla_de_aportes_a_seguridad_social_ju_documento'):
            self._upload_planilla_de_aportes_a_seguridad_social_ju_to_drive()
        if 'causacion_documento' in vals:
            self._upload_causacion_to_drive()
        if 'retencion_documento' in vals:
            self._upload_retencion_to_drive()
        if 'articulos_documento' in vals:
            self._upload_articulos_to_drive()
        if 'documento_liquidacion_documento' in vals:
            self._upload_documento_liquidacion_to_drive()
        if 'documento_cruce_documento' in vals:
            self._upload_documento_cruce_to_drive()
        if 'reporte_liquidacion_documento' in vals:
            self._upload_reporte_liquidacion_to_drive()
        if 'documentos_adicionales_contabilidad_documento' in vals:
            self._upload_documentos_adicionales_contabilidad_to_drive()
        if 'otro_documentos_de_informacion_documento' in vals:
            self._upload_otro_documentos_de_informacion_to_drive()
        if 'informes_contratista_documento' in vals:
            self._upload_informes_contratista_to_drive()

        # -------------------Sección para documentos P/N-------------------------

        # ✅ Nueva sección: trazabilidad del check
        if 'documento_equivalente_corregir' in vals:
            for record in self:
                if record.documento_equivalente_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>actualización del documento equivalente o factura electrónica</b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'informe_de_actividades_corregir' in vals:
            for record in self:
                if record.informe_de_actividades_corregir:
                    record._reset_vobo_informe_coordinador()
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de informe de actividades</b>.",
                        subtype_xmlid="mail.mt_note"
                    )

        if 'informe_vobo_coordinador' in vals and not self.env.context.get('skip_informe_vobo_sync'):
            for record in self:
                informe = self.env['informe_actividades'].search(
                    [('relacion_id', '=', record.id)], limit=1
                )
                if informe:
                    informe.write({'firma_coordinador_check': vals['informe_vobo_coordinador']})
                    if vals['informe_vobo_coordinador']:
                        informe.with_context(
                            skip_vobo_reset=True,
                            silent_informe_vobo_update=True,
                        ).generar_informe_xlsx()
        
        # ✅ Nueva sección: trazabilidad del check
        if 'declaracion_juramentada_y_soportes_corregir' in vals:
            for record in self:
                if record.declaracion_juramentada_y_soportes_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de declaración juramentada y soportes</b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'rut_actualizado_corregir' in vals:
            for record in self:
                if record.rut_actualizado_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del RUT</b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'planilla_de_aportes_a_seguridad_social_corregir' in vals:
            for record in self:
                if record.planilla_de_aportes_a_seguridad_social_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de planilla de aportes a seguridad socia</b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'certificado_de_expensa_corregir' in vals:
            for record in self:
                if record.certificado_de_expensa_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de certificado de expensas</b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # -------------------Sección para documentos P/J-------------------------
        # ✅ Nueva sección: trazabilidad del check
        if 'factura_electronica_juridica_corregir' in vals:
            for record in self:
                if record.factura_electronica_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de factura electrónica </b>.",
                        subtype_xmlid="mail.mt_note"
                    )

        # ✅ Nueva sección: trazabilidad del check
        if 'rut_actualizado_juridica_corregir' in vals:
            for record in self:
                if record.rut_actualizado_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del RUT </b>.",
                        subtype_xmlid="mail.mt_note"
                    )

        # ✅ Nueva sección: trazabilidad del check
        if 'camara_comercio_juridica_corregir' in vals:
            for record in self:
                if record.camara_comercio_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de cámara de comercio </b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        

        # ✅ Nueva sección: trazabilidad del check
        if 'certificado_pago_parafiscales_juridica_corregir' in vals:
            for record in self:
                if record.certificado_pago_parafiscales_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de certificado de pago parafiscales  </b>.",
                        subtype_xmlid="mail.mt_note"
                    )

        # ✅ Nueva sección: trazabilidad del check
        if 'tarjeta_profesional_revisor_fiscal_juridica_corregir' in vals:
            for record in self:
                if record.tarjeta_profesional_revisor_fiscal_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de tarjeta profesional revisor fiscal  </b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'certificado_junta_contadores_juridica_corregir' in vals:
            for record in self:
                if record.certificado_junta_contadores_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de Cert. Junta de Contadores del Revisor Fiscal y/o Contador   </b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'rut_representante_legal_juridica_corregir' in vals:
            for record in self:
                if record.rut_representante_legal_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del RUT Representante Legal </b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'cedula_de_contador_juridica_corregir' in vals:
            for record in self:
                if record.cedula_de_contador_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de identidad contador </b>.",
                        subtype_xmlid="mail.mt_note"
                    )

        # ✅ Nueva sección: trazabilidad del check
        if 'Tarjeta_profesional_contador_juridica_corregir' in vals:
            for record in self:
                if record.Tarjeta_profesional_contador_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de tarjeta profesional contador </b>.",
                        subtype_xmlid="mail.mt_note"
                    )

        # ✅ Nueva sección: trazabilidad del check
        if 'cedula_revisor_fiscal_juridica_corregir' in vals:
            for record in self:
                if record.cedula_revisor_fiscal_juridica_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de identidad revisor fiscal </b>.",
                        subtype_xmlid="mail.mt_note"
                    )
        
        # ✅ Nueva sección: trazabilidad del check
        if 'planilla_de_aportes_a_seguridad_social_ju_corregir' in vals:
            for record in self:
                if record.planilla_de_aportes_a_seguridad_social_ju_corregir:
                    record.message_post(
                        body="✅ Se ha solicitado la <b>Actualización del documento de planilla de aportes a seguridad social </b>.",
                        subtype_xmlid="mail.mt_note"
                    )



        return res

    def _requerir_refirma_declaracion_por_correccion(self):
        Declaracion = self.env['declaracion_juramentada'].sudo()
        for record in self:
            cuenta = record.cuenta_id
            if not cuenta:
                continue
            declaraciones = Declaracion.search([('relacion', '=', cuenta.id)])
            for declaracion in declaraciones:
                declaracion.action_requerir_nueva_firma_por_correccion()

    def _requerir_refirma_informe_por_correccion(self):
        Informe = self.env['informe_actividades'].sudo()
        for record in self:
            domain = [('relacion_id', '=', record.id)]
            if record.cuenta_id:
                domain = ['|', ('relacion_id', '=', record.id), ('cuenta_cobro_id', '=', record.cuenta_id.id)]
            informes = Informe.search(domain)
            for informe in informes:
                informe.action_requerir_nueva_firma_por_correccion()

    def _reset_vobo_informe_coordinador(self):
        Informe = self.env['informe_actividades'].sudo()
        for record in self:
            if record.informe_vobo_coordinador:
                record.with_context(skip_informe_vobo_sync=True).write({
                    'informe_vobo_coordinador': False
                })
            domain = [('relacion_id', '=', record.id)]
            if record.cuenta_id:
                domain = ['|', ('relacion_id', '=', record.id), ('cuenta_cobro_id', '=', record.cuenta_id.id)]
            informes = Informe.search(domain)
            for informe in informes:
                vals_reset = {}
                if informe.firma_coordinador_check:
                    vals_reset['firma_coordinador_check'] = False
                if informe.coordinador_log_id:
                    vals_reset['coordinador_log_id'] = False
                if informe.firma_coordinador_hash:
                    vals_reset['firma_coordinador_hash'] = False
                if vals_reset:
                    informe.write(vals_reset)

####################################nuevo###########################################################################
    causacion = fields.Boolean(string='Causación', tracking=True)
    causacion_documento = fields.Binary(string='Causación')
    causacion_name = fields.Char(string='Causación') 
    causacion_visible = fields.Boolean(string='Causación Visible', default=True)
    causacion_id = fields.Many2one('drive', string='Causación', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    consecutivo_causacion = fields.Char(string='Consecutivo', tracking=True)

    def _action_upload_acta_drive_document(self, title, tipo_documento):
        self.ensure_one()
        view_id = self.env.ref('Matriz.view_drive_upload_popup_form').id
        stage4_record = self.drive_stage_provedor_id
        google_folder_id = stage4_record.folder_id if stage4_record and stage4_record.folder_id else 'root'
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_contratistas_provedores': self.id,
                'default_stage4_id': stage4_record.id if stage4_record else False,
                'default_folder_id': google_folder_id,
                'tipo_documento_upload': tipo_documento,
            },
        }

    def action_upload_causacion_drive(self):
        return self._action_upload_acta_drive_document('Subir Causación', 'acta_causacion_documento')

    def action_upload_retencion_drive(self):
        return self._action_upload_acta_drive_document('Subir Retención', 'acta_retencion_documento')

    def action_upload_articulos_drive(self):
        return self._action_upload_acta_drive_document('Subir Documento cruce Art 383 con CXP', 'acta_articulos_documento')

    def action_upload_documento_liquidacion_drive(self):
        return self._action_upload_acta_drive_document('Subir Soporte liquidación Art 383', 'acta_documento_liquidacion_documento')

    def action_upload_documento_cruce_drive(self):
        return self._action_upload_acta_drive_document('Subir Documento de cruce o descuento', 'acta_documento_cruce_documento')

    def action_upload_reporte_liquidacion_drive(self):
        return self._action_upload_acta_drive_document('Subir Documento de causación Art 383', 'acta_reporte_liquidacion_documento')

    def action_upload_documentos_adicionales_contabilidad_drive(self):
        return self._action_upload_acta_drive_document('Subir Documentos adicionales', 'acta_documentos_adicionales_contabilidad_documento')

    def action_upload_orden_de_pago_drive(self):
        return self._action_upload_acta_drive_document('Subir Orden de pago', 'acta_orden_de_pago_documento')

    def action_upload_soporte_rechazo_drive(self):
        action = self._action_upload_acta_drive_document(
            'Subir nota crédito / soporte de rechazo',
            'acta_soporte_rechazo_documento',
        )
        action['context']['default_portal_acta_document_type'] = 'soporte_rechazo'
        return action

    def action_upload_comprobante_de_pago_drive(self):
        return self._action_upload_acta_drive_document('Subir Comprobante de pago', 'acta_comprobante_de_pago_documento')

    def action_upload_comprobante_bancario_drive(self):
        return self._action_upload_acta_drive_document(
            'Subir Comprobante Bancario',
            'acta_comprobante_bancario_documento',
        )

    def action_upload_rpc_regalias_drive(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_contratacion_asis_coordinador'):
            raise AccessError(_(
                'Solo el Asistente de Coordinación puede subir el RPC de Regalías.'
            ))
        if self.origen_cuenta != 'regalias':
            raise AccessError(_(
                'El RPC manual solo está disponible para cuentas de Regalías.'
            ))
        return self._action_upload_acta_drive_document(
            'Subir RPC de Regalías',
            'acta_rpc_regalias_documento',
        )

    def action_open_rpc_regalias_drive(self):
        self.ensure_one()
        if self.origen_cuenta != 'regalias' or not self.rpc_regalias_id:
            raise UserError(_('No hay un RPC de Regalías cargado para esta cuenta.'))
        self._ensure_drive_document_preview(self.rpc_regalias_id)
        return {
            'type': 'ir.actions.act_window',
            'name': 'RPC de Regalías',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rpc_regalias_id.id,
            'target': 'new',
        }


    def action_open_causacion_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Causación',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.causacion_id.id,
            'target': 'new',
        }

    def _upload_causacion_to_drive(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive, evitando duplicados."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No se encontró un registro en cuenta_de_cobros para {self.name}. No se subirá el archivo.")
            return False
        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para {self.name}. No se subirá el archivo.")
            return False
        # Validar que el archivo binario existe
        if not self.causacion_documento:
            _logger.error("No hay contenido en 'causacion_documento'. No se puede subir el archivo.")
            return False
        temp_file_path = None
        try:
            original_extension = os.path.splitext(self.causacion_name or '')[-1] or ".bin"
            fixed_file_name = f"Causación_{uuid.uuid4()}{original_extension}"
            # Buscar si ya existe un archivo con el mismo prefijo en el nombre
            existing_drive_record = self.env['drive'].search([
                ('folder_id', '=', carpeta_drive),
                ('file_name', 'like', 'Causación_%')
            ], limit=1)
            file_content = base64.b64decode(self.causacion_documento)
            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file.flush()
                temp_file_path = temp_file.name
            if not os.path.exists(temp_file_path):
                _logger.error(f"El archivo temporal no se creó correctamente: {temp_file_path}")
                return False
            if existing_drive_record:
                file_id = existing_drive_record.file_id
                file_drive = drive.CreateFile({'id': file_id})
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo existente {fixed_file_name} actualizado en Google Drive con ID: {file_id}")
                # Actualizar datos en Odoo
                existing_drive_record.write({
                    'file_name': fixed_file_name,
                    'file_data': False,
                    'file_url': file_link
                })
                drive_record = existing_drive_record
            else:
                # Crear nuevo archivo en Google Drive
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': 'application/octet-stream',
                    'parents': [{'id': carpeta_drive}]
                }
                file_drive = drive.CreateFile(file_metadata)
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_id = file_drive['id']
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo subido a Google Drive con ID: {file_id} y URL: {file_link}")
                # Crear registro en Odoo
                drive_record = self.env['drive'].create({
                    'file_name': fixed_file_name,
                    'file_id': file_id,
                    'file_url': file_link,
                    'file_data': False,
                    'folder_id': carpeta_drive,
                    'stage4_id': stage6_record.id
                })
            # Asignar el registro al campo de referencia si no está asignado
            if not self.causacion_id:
                self.write({'causacion_id': drive_record.id})
            _logger.info(f"Campo causacion_id actualizado con ID: {drive_record.id}")
            # Ocultar el campo después de la subida
            self.write({'causacion_visible': False})
            self.write({'causacion': True})
            self.write({'causacion_documento': False})
            # ✅ Trazabilidad: Agregar mensaje al chatter
            self.message_post(
                body=f"✅ El documento <b>Causación</b> fue subido exitosamente a Google Drive:<br>",
            )
        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False
        return True


    

    retencion = fields.Boolean(string='Retención (si aplica)', tracking=True)
    retencion_documento = fields.Binary(string='Retención (si aplica)')
    retencion_name = fields.Char(string='Retención (si aplica)')
    retencion_visible = fields.Boolean(string='Retención Visible', default=True)
    retencion_id = fields.Many2one('drive', string='Retención (si aplica)', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    def action_open_retencion_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Retención',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.retencion_id.id,
            'target': 'new',
        }

    def _upload_retencion_to_drive(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive, evitando duplicados."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No se encontró un registro en cuenta_de_cobros para {self.name}. No se subirá el archivo.")
            return False
        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para {self.name}. No se subirá el archivo.")
            return False
        # Validar que el archivo binario existe
        if not self.retencion_documento:
            _logger.error("No hay contenido en 'retencion_documento'. No se puede subir el archivo.")
            return False
        temp_file_path = None
        try:
            original_extension = os.path.splitext(self.retencion_name or '')[-1] or ".bin"
            fixed_file_name = f"Retención_{uuid.uuid4()}{original_extension}"
            # Buscar si ya existe un archivo con el mismo prefijo en el nombre
            existing_drive_record = self.env['drive'].search([
                ('folder_id', '=', carpeta_drive),
                ('file_name', 'like', 'Retención_%')
            ], limit=1)
            file_content = base64.b64decode(self.retencion_documento)
            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file.flush()
                temp_file_path = temp_file.name
            if not os.path.exists(temp_file_path):
                _logger.error(f"El archivo temporal no se creó correctamente: {temp_file_path}")
                return False
            if existing_drive_record:
                file_id = existing_drive_record.file_id
                file_drive = drive.CreateFile({'id': file_id})
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo existente {fixed_file_name} actualizado en Google Drive con ID: {file_id}")
                # Actualizar datos en Odoo
                existing_drive_record.write({
                    'file_name': fixed_file_name,
                    'file_data': False,
                    'file_url': file_link
                })
                drive_record = existing_drive_record
            else:
                # Crear nuevo archivo en Google Drive
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': 'application/octet-stream',
                    'parents': [{'id': carpeta_drive}]
                }
                file_drive = drive.CreateFile(file_metadata)
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_id = file_drive['id']
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo subido a Google Drive con ID: {file_id} y URL: {file_link}")
                # Crear registro en Odoo
                drive_record = self.env['drive'].create({
                    'file_name': fixed_file_name,
                    'file_id': file_id,
                    'file_url': file_link,
                    'file_data': False,
                    'folder_id': carpeta_drive,
                    'stage4_id': stage6_record.id
                })
            # Asignar el registro al campo de referencia si no está asignado
            if not self.retencion_id:
                self.write({'retencion_id': drive_record.id})
            _logger.info(f"Campo retencion_id actualizado con ID: {drive_record.id}")
            # Ocultar el campo después de la subida
            self.write({'retencion_visible': False})
            self.write({'retencion': True})
            self.write({'retencion_documento': False})
            # ✅ Trazabilidad: Agregar mensaje al chatter
            self.message_post(
                body=f"✅ El documento <b>Retención</b> fue subido exitosamente a Google Drive:<br>"
                    ,
            )
        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False
        return True


    Articulos = fields.Boolean(string='Documento cruce Art 383 con CXP', tracking=True)
    articulos_documento = fields.Binary(string='Documento cruce Art 383 con CXP')
    articulos_name = fields.Char(string='Documento cruce Art 383 con CXP')
    articulos_visible = fields.Boolean(string='Documento cruce Art 383 con CXP Visible', default=True)
    articulos_id = fields.Many2one('drive', string='Documento cruce Art 383 con CXP', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    def action_open_articulos_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento cruce Art 383 con CXP',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.articulos_id.id,
            'target': 'new',
        }
    
    def _upload_articulos_to_drive(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive, evitando duplicados."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        stage6_record = self.drive_stage_provedor_id
        if not stage6_record:
            _logger.error(f"No se encontró un registro en cuenta_de_cobros para {self.name}. No se subirá el archivo.")
            return False
        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para {self.name}. No se subirá el archivo.")
            return False
        # Validar que el archivo binario existe
        if not self.articulos_documento:
            _logger.error("No hay contenido en 'articulos_documento'. No se puede subir el archivo.")
            return False
        temp_file_path = None
        try:
            original_extension = os.path.splitext(self.articulos_name or '')[-1] or ".bin"
            fixed_file_name = f"Articulo_383_{uuid.uuid4()}{original_extension}"
            # Buscar si ya existe un archivo con el mismo prefijo en el nombre
            existing_drive_record = self.env['drive'].search([
                ('folder_id', '=', carpeta_drive),
                ('file_name', 'like', 'Articulo_383_%')
            ], limit=1)
            file_content = base64.b64decode(self.articulos_documento)
            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file.flush()
                temp_file_path = temp_file.name
            if not os.path.exists(temp_file_path):
                _logger.error(f"El archivo temporal no se creó correctamente: {temp_file_path}")
                return False
            if existing_drive_record:
                file_id = existing_drive_record.file_id
                file_drive = drive.CreateFile({'id': file_id})
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo existente {fixed_file_name} actualizado en Google Drive con ID: {file_id}")
                # Actualizar datos en Odoo
                existing_drive_record.write({
                    'file_name': fixed_file_name,
                    'file_data': False,
                    'file_url': file_link
                })
                drive_record = existing_drive_record
            else:
                # Crear nuevo archivo en Google Drive
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': 'application/octet-stream',
                    'parents': [{'id': carpeta_drive}]
                }
                file_drive = drive.CreateFile(file_metadata)
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_id = file_drive['id']
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo subido a Google Drive con ID: {file_id} y URL: {file_link}")
                # Crear registro en Odoo
                drive_record = self.env['drive'].create({
                    'file_name': fixed_file_name,
                    'file_id': file_id,
                    'file_url': file_link,
                    'file_data': False,
                    'folder_id': carpeta_drive,
                    'stage4_id': stage6_record.id
                })
            # Asignar el registro al campo de referencia si no está asignado
            if not self.articulos_id:
                self.write({'articulos_id': drive_record.id})
            _logger.info(f"Campo articulos_id actualizado con ID: {drive_record.id}")
            # Ocultar el campo después de la subida
            self.write({'articulos_visible': False})
            self.write({'Articulos': True})
            self.write({'articulos_documento': False})
            # ✅ Trazabilidad: Agregar mensaje al chatter
            self.message_post(
                body=f"✅ El documento <b>Articulo 383</b> fue subido exitosamente a Google Drive:<br>"
                    ,
            )
        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False
        return True

    documento_liquidacion = fields.Boolean(string='Soporte liquidación Art 383', tracking=True)
    documento_liquidacion_documento = fields.Binary(string='Soporte liquidación Art 383')
    documento_liquidacion_name = fields.Char(string='Soporte liquidación Art 383')
    documento_liquidacion_visible = fields.Boolean(string='Soporte liquidación Art 383 Visible', default=True)
    documento_liquidacion_id = fields.Many2one('drive', string='Soporte liquidación Art 383', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    documento_cruce = fields.Boolean(string='Documento de cruce o descuento', tracking=True)
    documento_cruce_documento = fields.Binary(string='Documento de cruce o descuento')
    documento_cruce_name = fields.Char(string='Documento de cruce o descuento')
    documento_cruce_visible = fields.Boolean(string='Documento de cruce o descuento Visible', default=True)
    documento_cruce_id = fields.Many2one('drive', string='Documento de cruce o descuento', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    reporte_liquidacion = fields.Boolean(string='Documento de causación Art 383', tracking=True)
    reporte_liquidacion_documento = fields.Binary(string='Documento de causación Art 383')
    reporte_liquidacion_name = fields.Char(string='Documento de causación Art 383')
    reporte_liquidacion_visible = fields.Boolean(string='Documento de causación Art 383 Visible', default=True)
    reporte_liquidacion_id = fields.Many2one('drive', string='Documento de causación Art 383', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    documentos_adicionales_contabilidad = fields.Boolean(string='Documentos adicionales', tracking=True)
    documentos_adicionales_contabilidad_documento = fields.Binary(string='Documentos adicionales')
    documentos_adicionales_contabilidad_name = fields.Char(string='Documentos adicionales')
    documentos_adicionales_contabilidad_visible = fields.Boolean(string='Documentos adicionales Visible', default=True)
    documentos_adicionales_contabilidad_id = fields.Many2one('drive', string='Documentos adicionales', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    documentos_adicionales_contabilidad_extra_ids = fields.One2many(
        'drive',
        'acta_contabilidad_adicional_id',
        string='Otros documentos adicionales',
    )

    def _action_open_contabilidad_drive(self, field_name, title):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self[field_name].id,
            'target': 'new',
        }

    def action_open_documento_liquidacion_drive(self):
        return self._action_open_contabilidad_drive('documento_liquidacion_id', 'Soporte liquidación Art 383')

    def action_open_documento_cruce_drive(self):
        return self._action_open_contabilidad_drive('documento_cruce_id', 'Documento de cruce o descuento')

    def action_open_reporte_liquidacion_drive(self):
        return self._action_open_contabilidad_drive('reporte_liquidacion_id', 'Documento de causación Art 383')

    def documentos_adicionales_contabilidad_drive(self):
        return self.action_open_documentos_adicionales_contabilidad_drive()

    def action_open_documentos_adicionales_contabilidad_drive(self):
        return self._action_open_contabilidad_drive('documentos_adicionales_contabilidad_id', 'Documentos adicionales')

    def otros_documentos_adicionales_contabilidad_drive(self):
        self.ensure_one()
        stage4_record = self.drive_stage_provedor_id
        if not stage4_record or not stage4_record.folder_id:
            raise UserError(_("No hay una carpeta de Drive asociada para subir otros documentos adicionales."))

        return {
            'type': 'ir.actions.act_window',
            'name': 'Otros documentos adicionales',
            'res_model': 'drive',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [('acta_contabilidad_adicional_id', '=', self.id)],
            'context': {
                'default_acta_contabilidad_adicional_id': self.id,
                'default_stage4_id': stage4_record.id,
                'default_folder_id': stage4_record.folder_id,
                'default_file_data_visible': True,
            },
        }

    def action_open_otros_documentos_adicionales_contabilidad_drive(self):
        return self.otros_documentos_adicionales_contabilidad_drive()

    def _upload_contabilidad_documento_to_drive(self, binary_field, name_field, id_field, visible_field, boolean_field, label, prefix):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        for record in self:
            stage6_record = record.drive_stage_provedor_id
            if not stage6_record:
                _logger.error(f"No se encontró un registro en cuenta_de_cobros para {record.name}. No se subirá el archivo.")
                continue

            carpeta_drive = stage6_record.folder_id
            if not carpeta_drive:
                _logger.error(f"No se encontró una carpeta en Drive para {record.name}. No se subirá el archivo.")
                continue

            file_binary = record[binary_field]
            if not file_binary:
                _logger.error(f"No hay contenido en '{binary_field}'. No se puede subir el archivo.")
                continue

            temp_file_path = None
            try:
                original_extension = os.path.splitext(record[name_field] or '')[-1] or ".bin"
                fixed_file_name = f"{prefix}_{uuid.uuid4()}{original_extension}"

                existing_drive_record = record.env['drive'].search([
                    ('folder_id', '=', carpeta_drive),
                    ('file_name', 'like', f'{prefix}_%')
                ], limit=1)

                file_content = base64.b64decode(file_binary)
                with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                    temp_file.write(file_content)
                    temp_file.flush()
                    temp_file_path = temp_file.name

                if existing_drive_record:
                    file_id = existing_drive_record.file_id
                    file_drive = drive.CreateFile({'id': file_id})
                    file_drive.SetContentFile(temp_file_path)
                    file_drive.Upload()
                    file_link = f"https://drive.google.com/file/d/{file_id}/view"
                    existing_drive_record.write({
                        'file_name': fixed_file_name,
                        'file_data': False,
                        'file_url': file_link
                    })
                    drive_record = existing_drive_record
                else:
                    file_metadata = {
                        'title': fixed_file_name,
                        'mimeType': 'application/octet-stream',
                        'parents': [{'id': carpeta_drive}]
                    }
                    file_drive = drive.CreateFile(file_metadata)
                    file_drive.SetContentFile(temp_file_path)
                    file_drive.Upload()
                    file_id = file_drive['id']
                    file_link = f"https://drive.google.com/file/d/{file_id}/view"
                    drive_record = record.env['drive'].create({
                        'file_name': fixed_file_name,
                        'file_id': file_id,
                        'file_url': file_link,
                        'file_data': False,
                        'folder_id': carpeta_drive,
                        'stage4_id': stage6_record.id
                    })

                if not record[id_field]:
                    record.write({id_field: drive_record.id})

                record.write({
                    visible_field: False,
                    boolean_field: True,
                    binary_field: False,
                })
                record.message_post(body=f"✅ El documento <b>{label}</b> fue subido exitosamente a Google Drive:<br>")
            except Exception as e:
                _logger.error(f"Error al subir {label} a Google Drive: {e}")
                return False
            finally:
                if temp_file_path and os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)

        return True

    def _upload_documento_liquidacion_to_drive(self):
        return self._upload_contabilidad_documento_to_drive(
            'documento_liquidacion_documento',
            'documento_liquidacion_name',
            'documento_liquidacion_id',
            'documento_liquidacion_visible',
            'documento_liquidacion',
            'Documento de liquidación',
            'Documento_de_liquidacion',
        )

    def _upload_documento_cruce_to_drive(self):
        return self._upload_contabilidad_documento_to_drive(
            'documento_cruce_documento',
            'documento_cruce_name',
            'documento_cruce_id',
            'documento_cruce_visible',
            'documento_cruce',
            'Documento de cruce',
            'Documento_de_cruce',
        )

    def _upload_reporte_liquidacion_to_drive(self):
        return self._upload_contabilidad_documento_to_drive(
            'reporte_liquidacion_documento',
            'reporte_liquidacion_name',
            'reporte_liquidacion_id',
            'reporte_liquidacion_visible',
            'reporte_liquidacion',
            'Reporte de liquidación',
            'Reporte_de_liquidacion',
        )

    def _upload_documentos_adicionales_contabilidad_to_drive(self):
        return self._upload_contabilidad_documento_to_drive(
            'documentos_adicionales_contabilidad_documento',
            'documentos_adicionales_contabilidad_name',
            'documentos_adicionales_contabilidad_id',
            'documentos_adicionales_contabilidad_visible',
            'documentos_adicionales_contabilidad',
            'Documentos adicionales',
            'Documentos_adicionales',
        )


        
#############################################################################################################

    # Documentación anexa persona natural

    estado_pago = fields.Char(
        string='Estado del pago',
        compute='_compute_estado_pago',
    )

    @api.depends('comprobante_de_pago')
    def _compute_estado_pago(self):
        for record in self:
            if record.comprobante_de_pago:
                record.estado_pago = "Pagado"
            else:
                record.estado_pago = "Pendiente"


    comprobante_de_pago = fields.Boolean(string='Comprobante de pago', tracking=True)
    comprobante_de_pago_documento = fields.Binary(string='Comprobante de pago')
    comprobante_de_pago_name = fields.Char(string='Comprobante de pago')
    comprobante_de_pago_visible = fields.Boolean(string='Comprobante de pago Visible', default=True)
    comprobante_de_pago_id = fields.Many2one('drive', string='Comprobante de pago', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    comprobante_bancario = fields.Boolean(string='Comprobante Bancario', tracking=True)
    comprobante_bancario_documento = fields.Binary(string='Comprobante Bancario')
    comprobante_bancario_name = fields.Char(string='Comprobante Bancario')
    comprobante_bancario_visible = fields.Boolean(string='Comprobante Bancario Visible', default=True)
    comprobante_bancario_id = fields.Many2one('drive', string='Comprobante Bancario', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    documento_unificado_id = fields.Many2one(
        'drive',
        string='PDF unificado de la cuenta de cobro',
        readonly=True,
        copy=False,
    )
    pdf_unificado_estado = fields.Selection(
        [
            ('pendiente', 'Sin iniciar'),
            ('procesando', 'Procesando'),
            ('completado', 'Completado'),
            ('error', 'Error'),
        ],
        string='Estado del PDF unificado',
        default='pendiente',
        readonly=True,
        copy=False,
    )
    pdf_unificado_progreso = fields.Float(
        string='Progreso del PDF unificado', readonly=True, copy=False,
    )
    pdf_unificado_mensaje = fields.Char(
        string='Detalle del proceso', readonly=True, copy=False,
    )

    def _documentos_para_pdf_unificado(self):
        """Documentos oficiales en el orden requerido para el expediente final."""
        self.ensure_one()
        documentos = []
        ids_agregados = set()

        def agregar(etiqueta, registros):
            for registro in registros:
                if not registro or registro.id in ids_agregados:
                    continue
                if not self._resolve_google_drive_file_id(registro):
                    _logger.info(
                        "PDF unificado: se omite %s (%s) porque no tiene archivo en Google Drive.",
                        etiqueta,
                        registro.display_name,
                    )
                    continue
                documentos.append((etiqueta, registro))
                ids_agregados.add(registro.id)

        agregar('Comprobante de pago contable', self.comprobante_de_pago_id)
        agregar('Comprobante de pago banco', self.comprobante_bancario_id)
        agregar('Orden de pago contable', self.orden_de_pago_id)
        agregar('Causación contable', self.causacion_id)
        agregar('Documento de retenciones Art. 383', self.retencion_id)
        agregar('Documento de retenciones Art. 383', self.documento_liquidacion_id)
        agregar('Otros documentos de Contabilidad', self.documento_cruce_id)
        agregar('Otros documentos de Contabilidad', self.articulos_id)
        agregar('Otros documentos de Contabilidad', self.reporte_liquidacion_id)
        agregar('Otros documentos de Contabilidad', self.documentos_adicionales_contabilidad_id)
        agregar(
            'Otros documentos de Contabilidad',
            self.documentos_adicionales_contabilidad_extra_ids.sorted('id'),
        )

        if self.clase_persona == 'persona_juridica':
            agregar('Certificación conjunta firmada', self.certificacion_conjunta_firmado_juridica_id)
            agregar('Factura o documento equivalente', self.factura_electronica_juridica_id)
            agregar('Informe de actividades', self.informe_de_actividades_id)
            agregar('Seguridad social', self.planilla_de_aportes_a_seguridad_social_ju_id)
            agregar('RUT', self.rut_actualizado_juridica_id)
            campos_adicionales = (
                'registro_proveedores_juridica_id',
                'resolucion_facturacion_electronica_juridica_id',
                'camara_comercio_juridica_id',
                'certificado_pago_parafiscales_juridica_id',
                'tarjeta_profesional_revisor_fiscal_juridica_id',
                'cedula_revisor_fiscal_juridica_id',
                'cedula_representante_legal_juridica_id',
                'cedula_representante_legal_de_contador_juridica_id',
                'Tarjeta_profesional_contador_juridica_id',
                'certificado_junta_contadores_juridica_id',
                'rut_representante_legal_juridica_id',
                'certificacion_bancaria_juridica_id',
                'anexo_tributario_juridica_id',
                'copia_contrato_juridica_id',
                'cedula_de_contador_juridica_id',
            )
        else:
            agregar('Certificación conjunta firmada', self.certificacion_conjunta_firmado_id)
            agregar('Factura o documento equivalente', self.factura_id or self.documento_equivalente_id)
            agregar('Informe de actividades', self.informe_de_actividades_id)
            agregar('Seguridad social', self.planilla_de_aportes_a_seguridad_social_id)
            agregar('RUT', self.rut_actualizado_id)
            campos_adicionales = (
                'declaracion_juramentada_y_soportes_id',
                'certificado_de_expensa_id',
                'copia_contrato_id',
                'copia_cedula_id',
                'certificacion_bancaria_id',
                'registro_de_proveedores_id',
                'resolucion_facturacion_electronica_id',
                'anexo_tributario_id',
            )

        for nombre_campo in campos_adicionales:
            if nombre_campo in self._fields:
                agregar('Demás documentos del contratista', self[nombre_campo])

        if self.origen_cuenta == 'regalias':
            agregar('Demás documentos del contratista', self.regalias_copia_cedula_id)
            agregar('Demás documentos del contratista', self.regalias_copia_contrato_id)

        agregar(
            'Informes adicionales del contratista',
            self._get_portal_acta_document_records(
                'informes_contratista', 'informes_contratista_id'
            ).sorted('id'),
        )
        agregar(
            'Demás documentos del contratista',
            self._get_portal_acta_document_records(
                'otro_documentos_de_informacion', 'otro_documentos_de_inf_id'
            ).sorted('id'),
        )
        return documentos

    def _drive_documento_como_pdf(self, drive_record, etiqueta=''):
        """Descarga un documento de Drive y devuelve sus bytes en PDF."""
        self.ensure_one()
        file_id = self._resolve_google_drive_file_id(drive_record)
        if not file_id:
            raise UserError(_("El documento '%s' no tiene archivo en Google Drive.") % drive_record.display_name)

        # Cada documento usa su propio transporte HTTP. Reutilizar el cliente
        # global después de otras operaciones de Drive podía dejar esta lectura
        # esperando indefinidamente en una conexión persistente ya cerrada.
        # El timeout es de lectura: no limita el tamaño del archivo mientras
        # Google continúe enviando datos.
        drive_auth = GoogleDriveAuth(http_timeout_seconds=20)
        service = drive_record._drive_service_v3(drive_auth)
        metadata = service.files().get(
            fileId=file_id,
            fields='id,name,mimeType',
            supportsAllDrives=True,
        ).execute()
        mime_type = metadata.get('mimeType') or ''
        nombre = metadata.get('name') or drive_record.file_name or 'documento'
        extension = os.path.splitext(nombre.lower())[1]
        mime_por_extension = {
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.ppt': 'application/vnd.ms-powerpoint',
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
        }
        if mime_type in ('', 'application/octet-stream'):
            mime_type = mime_por_extension.get(extension, mime_type)

        if mime_type == 'application/pdf' or nombre.lower().endswith('.pdf'):
            return drive_record._drive_download_bytes(
                file_id, mime_type=mime_type, drive_auth=drive_auth
            )

        exportables = {
            'application/vnd.google-apps.document': 'application/pdf',
            'application/vnd.google-apps.spreadsheet': 'application/pdf',
            'application/vnd.google-apps.presentation': 'application/pdf',
        }
        if mime_type in exportables:
            if mime_type == 'application/vnd.google-apps.spreadsheet':
                return self._drive_export_spreadsheet_pdf_bytes(
                    file_id, drive_auth, nombre=nombre, etiqueta=etiqueta,
                )
            return drive_record._drive_download_bytes(
                file_id,
                mime_type=mime_type,
                export_mime='application/pdf',
                drive_auth=drive_auth,
            )

        conversiones = {
            'application/msword': 'application/vnd.google-apps.document',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'application/vnd.google-apps.document',
            'application/vnd.ms-excel': 'application/vnd.google-apps.spreadsheet',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'application/vnd.google-apps.spreadsheet',
            'application/vnd.ms-powerpoint': 'application/vnd.google-apps.presentation',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'application/vnd.google-apps.presentation',
        }
        mime_google = conversiones.get(mime_type)
        if mime_google:
            temporal = service.files().copy(
                fileId=file_id,
                body={
                    'name': '_temporal_unificacion_%s' % uuid.uuid4().hex,
                    'mimeType': mime_google,
                },
                supportsAllDrives=True,
                fields='id,mimeType',
            ).execute()
            temporal_id = temporal.get('id')
            try:
                if mime_google == 'application/vnd.google-apps.spreadsheet':
                    return self._drive_export_spreadsheet_pdf_bytes(
                        temporal_id, drive_auth, nombre=nombre, etiqueta=etiqueta,
                    )
                return drive_record._drive_download_bytes(
                    temporal_id,
                    mime_type=mime_google,
                    export_mime='application/pdf',
                    drive_auth=drive_auth,
                )
            finally:
                if temporal_id:
                    try:
                        service.files().delete(fileId=temporal_id, supportsAllDrives=True).execute()
                    except Exception:
                        _logger.warning('No se pudo eliminar el archivo temporal %s de Drive.', temporal_id)

        if mime_type.startswith('image/'):
            image_bytes = drive_record._drive_download_bytes(
                file_id, mime_type=mime_type, drive_auth=drive_auth
            )
            image = ImageReader(BytesIO(image_bytes))
            image_width, image_height = image.getSize()
            page_width, page_height = letter
            margin = 18 * mm
            scale = min(
                (page_width - (2 * margin)) / image_width,
                (page_height - (2 * margin)) / image_height,
            )
            target_width = image_width * scale
            target_height = image_height * scale
            image_pdf = BytesIO()
            pdf_canvas = canvas.Canvas(image_pdf, pagesize=letter)
            pdf_canvas.drawImage(
                image,
                (page_width - target_width) / 2,
                (page_height - target_height) / 2,
                width=target_width,
                height=target_height,
                preserveAspectRatio=True,
                mask='auto',
            )
            pdf_canvas.showPage()
            pdf_canvas.save()
            return image_pdf.getvalue()

        raise UserError(_(
            "El archivo '%s' no es PDF ni un documento ofimático convertible a PDF."
        ) % nombre)

    def _drive_export_spreadsheet_pdf_bytes(self, spreadsheet_id, drive_auth, nombre='', etiqueta=''):
        """Exporta formatos Excel completos, sin cortar contenido ni firmas."""
        gauth = drive_auth.get_gauth()
        credentials = gauth.credentials
        if hasattr(credentials, 'access_token_expired') and credentials.access_token_expired:
            gauth.Refresh()
            credentials = gauth.credentials

        access_token = (
            getattr(credentials, 'access_token', None)
            or getattr(credentials, 'token', None)
        )
        if not access_token:
            raise UserError(_('No fue posible obtener token de Google Drive para exportar la hoja de cálculo.'))

        referencia = '%s %s' % (nombre or '', etiqueta or '')
        referencia_normalizada = referencia.lower()
        es_declaracion = any(texto in referencia_normalizada for texto in (
            'declaracion', 'declaración', 'juramentada', 'documento equivalente',
        ))
        es_informe_actividades = any(texto in referencia_normalizada for texto in (
            'informe de actividades', 'informe_actividades',
        ))
        es_formato_fijo = es_declaracion or es_informe_actividades

        response = requests.get(
            'https://docs.google.com/spreadsheets/d/%s/export' % spreadsheet_id,
            params={
                'format': 'pdf',
                'exportFormat': 'pdf',
                # Estos formularios fueron diseñados en vertical.
                'size': 'A4' if es_declaracion else 'letter',
                'portrait': 'true' if es_formato_fijo else 'false',
                # scale=4 ajusta la hoja completa a una página. Evita que las
                # firmas o las notas queden solas en una segunda página.
                'scale': '4' if es_formato_fijo else '2',
                'fitw': 'true',
                'sheetnames': 'false',
                'printtitle': 'false',
                'pagenumbers': 'false',
                'gridlines': 'false',
                'fzr': 'false',
                'top_margin': '0.30',
                'bottom_margin': '0.30',
                'left_margin': '0.25',
                'right_margin': '0.25',
                'horizontal_alignment': 'CENTER',
                'vertical_alignment': 'TOP',
            },
            headers={'Authorization': 'Bearer %s' % access_token},
            timeout=90,
        )
        if response.status_code >= 400 or not response.content:
            raise UserError(_(
                'Google Drive no pudo exportar la hoja de cálculo a PDF. Detalle: %s'
            ) % (response.text or response.status_code))
        return response.content

    def _actualizar_progreso_pdf_unificado(self, progreso, mensaje):
        self.sudo().write({
            'pdf_unificado_progreso': min(max(float(progreso), 0.0), 100.0),
            'pdf_unificado_mensaje': mensaje,
        })
        if self.env.context.get('pdf_unificado_background'):
            self.env.cr.commit()

    def _leer_pdf_para_unificar(self, pdf_bytes):
        """Abre PDF normales o cifrados sin clave de apertura, sin tocar el original."""
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            if not reader.is_encrypted or reader.decrypt(''):
                if len(reader.pages):
                    return reader
        except Exception:
            # PyPDF2 puede fallar con AES sin PyCryptodome o con estructuras
            # que otros lectores sí admiten. Eso no demuestra que haya clave.
            pass

        try:
            import fitz  # PyMuPDF: motor alternativo, ya declarado en requirements.
        except ImportError as error:
            raise UserError(_(
                'No se pudo leer el PDF con el motor principal. Instale '
                'PyMuPDF y pycryptodome en el entorno Python de Odoo y reinicie '
                'el servicio. Este error no confirma una contraseña de apertura.'
            )) from error

        try:
            with fitz.open(stream=pdf_bytes, filetype='pdf') as documento:
                # El cifrado/restricción de permisos no implica contraseña de
                # apertura. Nunca probar datos personales ni claves adivinadas.
                if documento.needs_pass and not documento.authenticate(''):
                    raise UserError(_(
                        'el PDF requiere una contraseña de apertura. Solicite '
                        'una copia sin esa contraseña; el cifrado sin clave de '
                        'apertura se procesa automáticamente'
                    ))
                paginas = documento.page_count
                if not paginas:
                    raise UserError(_('el PDF no contiene páginas'))
                # Copia en memoria: descifra objetos/páginas sin rasterizar ni
                # sobrescribir el original de Drive (incluidas sus firmas).
                normalizado = documento.tobytes(
                    garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE,
                )
            reader = PdfReader(BytesIO(normalizado))
            if reader.is_encrypted or len(reader.pages) != paginas:
                raise UserError(_('no se pudieron recuperar todas las páginas del PDF'))
            return reader
        except UserError:
            raise
        except Exception as error:
            raise UserError(_(
                'no fue posible leer o normalizar el PDF. Puede estar dañado '
                'o usar un formato no compatible; solicite una copia válida'
            )) from error

    def _preparar_pagina_para_pdf_unificado(self, pagina):
        """Usa toda la caja física de la página y evita recortes heredados."""
        try:
            pagina.cropbox.lower_left = pagina.mediabox.lower_left
            pagina.cropbox.upper_right = pagina.mediabox.upper_right
        except AttributeError:
            # Compatibilidad con versiones antiguas de PyPDF2 usadas por Odoo.
            pagina.cropBox.lowerLeft = pagina.mediaBox.lowerLeft
            pagina.cropBox.upperRight = pagina.mediaBox.upperRight
        return pagina

    def _generar_pdf_unificado(self):
        self.ensure_one()
        if self.estado not in ('finaciera', 'au_tesoreria', 'tesorería', 'finalizado'):
            raise UserError(_(
                'El PDF unificado solo se puede generar desde Contabilidad, '
                'Auxiliar de tesorería, Tesorería o Finalizado.'
            ))

        cuenta = self.cuenta_id
        factura_natural = bool(
            cuenta
            and (
                cuenta.factura_si_o_no
                or cuenta.factura_id
                or (
                    cuenta.numero_factura
                    and cuenta.numero_factura.strip()
                    and cuenta.numero_factura.strip().upper() not in ('N/A', 'NA')
                )
            )
        )
        if cuenta and self.clase_persona == 'persona_natural' and not factura_natural:
            self._actualizar_progreso_pdf_unificado(
                1.0, 'Actualizando documento equivalente y declaración juramentada'
            )
            self._refresh_documento_equivalente_drive_from_template()

        if self.informe_de_actividades_id:
            self._actualizar_progreso_pdf_unificado(2.0, 'Actualizando el informe de actividades')
            self._refresh_informe_de_actividades_drive_from_template()

        documentos = self._documentos_para_pdf_unificado()
        if not documentos:
            raise UserError(_('La cuenta no tiene documentos para unificar.'))

        writer = PdfWriter()
        errores = []
        agregados = 0
        total_documentos = len(documentos)
        for indice, (etiqueta, drive_record) in enumerate(documentos, 1):
            self._actualizar_progreso_pdf_unificado(
                ((indice - 1) / total_documentos) * 90.0,
                'Procesando %s (%s de %s)' % (
                    etiqueta, indice, total_documentos,
                ),
            )
            try:
                pdf_bytes = self._drive_documento_como_pdf(
                    drive_record, etiqueta=etiqueta,
                )
                reader = self._leer_pdf_para_unificar(pdf_bytes)
                for pagina in reader.pages:
                    writer.add_page(self._preparar_pagina_para_pdf_unificado(pagina))
                agregados += 1
            except Exception as error:
                errores.append('%s (%s): %s' % (
                    etiqueta,
                    drive_record.file_name or drive_record.display_name,
                    str(error),
                ))

        if errores:
            raise UserError(_(
                'No se generó un PDF incompleto. Corrija estos documentos:\n- %s'
            ) % '\n- '.join(errores))
        if not agregados:
            raise UserError(_('No fue posible agregar ningún documento al PDF.'))

        salida = BytesIO()
        self._actualizar_progreso_pdf_unificado(92.0, 'Uniendo las páginas del expediente')
        writer.write(salida)
        pdf_bytes = salida.getvalue()
        stage_record = self.drive_stage_provedor_id
        if not stage_record or not stage_record.folder_id:
            raise UserError(_('La cuenta no tiene una carpeta de Google Drive configurada.'))

        # La subida final también debe usar un transporte independiente. Así no
        # hereda conexiones utilizadas durante las descargas anteriores y una
        # falta de respuesta de Drive termina con error controlado.
        drive = GoogleDriveAuth(http_timeout_seconds=30).get_drive()
        nombre = 'Cuenta_de_cobro_unificada_%s.pdf' % re.sub(
            r'[^A-Za-z0-9_-]+', '_', str(self.pago or self.id)
        )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temporal:
                temporal.write(pdf_bytes)
                temp_path = temporal.name

            drive_record = self.documento_unificado_id
            file_id = self._resolve_google_drive_file_id(drive_record) if drive_record else False
            archivo_drive = drive.CreateFile({'id': file_id}) if file_id else drive.CreateFile({
                'title': nombre,
                'mimeType': 'application/pdf',
                'parents': [{'id': stage_record.folder_id}],
            })
            archivo_drive['title'] = nombre
            archivo_drive.SetContentFile(temp_path)
            self._actualizar_progreso_pdf_unificado(96.0, 'Guardando el PDF en Google Drive')
            archivo_drive.Upload()
            file_id = archivo_drive['id']
            url = 'https://drive.google.com/file/d/%s/view' % file_id
            preview_url = 'https://drive.google.com/file/d/%s/preview' % file_id

            vals_drive = {
                'file_name': nombre,
                'file_id': file_id,
                'file_url': url,
                'preview_url': preview_url,
                'preview_html': '<iframe src="%s" width="100%%" height="600px" frameborder="0" allowfullscreen="allowfullscreen"></iframe>' % preview_url,
                'file_data': False,
                'file_data_visible': False,
                'folder_id': stage_record.folder_id,
                'stage4_id': stage_record.id,
                'contratistas_provedores': self.id,
            }
            if drive_record:
                drive_record.sudo().with_context(skip_drive_sync=True, skip_integrity=True).write(vals_drive)
            else:
                drive_record = self.env['drive'].sudo().create(vals_drive)
                # La cuenta ya está finalizada y, correctamente, es de solo
                # lectura para el usuario. Esta elevación se limita a enlazar
                # el PDF consolidado que acaba de generar esta acción.
                self.sudo().with_context(
                    skip_drive_sync=True,
                    skip_integrity=True,
                ).write({'documento_unificado_id': drive_record.id})

            self.sudo().message_post(
                body='✅ Se generó y guardó en Google Drive el <b>PDF unificado de la cuenta de cobro</b> con %s documentos.' % agregados,
                subtype_xmlid='mail.mt_note',
            )
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        return self.action_open_documento_unificado_drive()

    def action_generar_pdf_unificado(self):
        """Inicia la consolidación sin bloquear la petición web de Odoo."""
        self.ensure_one()
        if self.estado not in ('finaciera', 'au_tesoreria', 'tesorería', 'finalizado'):
            raise UserError(_(
                'El PDF unificado solo se puede generar desde Contabilidad, '
                'Auxiliar de tesorería, Tesorería o Finalizado.'
            ))
        if self.pdf_unificado_estado == 'procesando':
            raise UserError(_('La unificación ya se está ejecutando.'))

        self.sudo().write({
            'pdf_unificado_estado': 'procesando',
            'pdf_unificado_progreso': 0.0,
            'pdf_unificado_mensaje': 'Preparando los documentos',
        })
        db_name = self.env.cr.dbname
        uid = self.env.uid
        acta_id = self.id
        model_name = self._name

        def _worker():
            from odoo.modules.registry import Registry
            try:
                with Registry(db_name).cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    acta = env[model_name].browse(acta_id).exists()
                    if not acta:
                        return
                    acta = acta.with_context(pdf_unificado_background=True)
                    acta._generar_pdf_unificado()
                    acta.sudo().write({
                        'pdf_unificado_estado': 'completado',
                        'pdf_unificado_progreso': 100.0,
                        'pdf_unificado_mensaje': 'PDF unificado listo',
                    })
                    cr.commit()
            except Exception as error:
                _logger.exception('Error generando el PDF unificado del acta %s', acta_id)
                try:
                    with Registry(db_name).cursor() as error_cr:
                        error_env = api.Environment(error_cr, uid, {})
                        error_env[model_name].browse(acta_id).sudo().write({
                            'pdf_unificado_estado': 'error',
                            'pdf_unificado_mensaje': str(error)[:500],
                        })
                        error_cr.commit()
                except Exception:
                    _logger.exception('No fue posible registrar el error del PDF unificado')

        worker = threading.Thread(target=_worker, daemon=True)
        self.env.cr.postcommit.add(worker.start)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('PDF unificado'),
                'message': _('La generación comenzó en segundo plano.'),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_open_documento_unificado_drive(self):
        self.ensure_one()
        if not self.documento_unificado_id:
            raise UserError(_('Primero debe generar el PDF unificado.'))
        self.documento_unificado_id.sudo().asegurar_visor_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'PDF unificado de la cuenta de cobro',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.documento_unificado_id.id,
            'target': 'new',
        }

    def action_download_documento_unificado(self):
        self.ensure_one()
        if not self.documento_unificado_id:
            raise UserError(_('Primero debe generar el PDF unificado.'))
        return self.documento_unificado_id.download_from_drive()

    def action_open_comprobante_de_pago_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Comprobante de pago',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.comprobante_de_pago_id.id,
            'target': 'new',
        }
    
    def _upload_comprobante_de_pago_to_drive(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive, evitando duplicados."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id

        if not stage6_record:
            _logger.error(f"No se encontró un registro en cuenta_de_cobros para {self.name}. No se subirá el archivo.")
            return False

        carpeta_drive = stage6_record.folder_id

        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para {self.name}. No se subirá el archivo.")
            return False

        # Validar que el archivo binario existe
        if not self.comprobante_de_pago_documento:
            _logger.error("No hay contenido en 'comprobante_de_pago_documento'. No se puede subir el archivo.")
            return False

        temp_file_path = None

        try:
            original_extension = os.path.splitext(self.comprobante_de_pago_name or '')[-1] or ".bin"
            fixed_file_name = f"Comprobante de pago_{uuid.uuid4()}{original_extension}"

            # Buscar si ya existe un archivo con el mismo prefijo en el nombre
            existing_drive_record = self.env['drive'].search([
                ('folder_id', '=', carpeta_drive),
                ('file_name', 'like', 'Comprobante de pago_%')
            ], limit=1)

            file_content = base64.b64decode(self.comprobante_de_pago_documento)

            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file.flush()
                temp_file_path = temp_file.name

            if not os.path.exists(temp_file_path):
                _logger.error(f"El archivo temporal no se creó correctamente: {temp_file_path}")
                return False

            if existing_drive_record:
                file_id = existing_drive_record.file_id
                file_drive = drive.CreateFile({'id': file_id})
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_link = f"https://drive.google.com/file/d/{file_id}/view"

                _logger.info(f"Archivo existente {fixed_file_name} actualizado en Google Drive con ID: {file_id}")
                # Actualizar datos en Odoo
                existing_drive_record.write({
                    'file_name': fixed_file_name,
                    'file_data': False,
                    'file_url': file_link
                })
                drive_record = existing_drive_record
            else:
                # Crear nuevo archivo en Google Drive
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': 'application/octet-stream',
                    'parents': [{'id': carpeta_drive}]
                }
                file_drive = drive.CreateFile(file_metadata)
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_id = file_drive['id']
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo subido a Google Drive con ID: {file_id} y URL: {file_link}")
                # Crear registro en Odoo
                drive_record = self.env['drive'].create({
                    'file_name': fixed_file_name,
                    'file_id': file_id,
                    'file_url': file_link,
                    'file_data': False,
                    'folder_id': carpeta_drive,
                    'stage4_id': stage6_record.id
                })
            # Asignar el registro al campo de referencia si no está asignado
            if not self.comprobante_de_pago_id:
                self.write({'comprobante_de_pago_id': drive_record.id})
            _logger.info(f"Campo comprobante_de_pago_id actualizado con ID: {drive_record.id}")
            # Ocultar el campo después de la subida
            self.write({'comprobante_de_pago_visible': False})
            self.write({'comprobante_de_pago': True})
            self.write({'comprobante_de_pago_documento': False})
            # ✅ Trazabilidad: Agregar mensaje al chatter
            self.message_post(
                body=f"✅ El documento <b>Comprobante de pago</b> fue subido exitosamente a Google Drive:<br>"
                    ,
            )

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False
        return True

    orden_de_oago = fields.Boolean(string='Orden de pago', tracking=True)
    orden_de_pago_documento = fields.Binary(string='Orden de pago')
    orden_de_pago_name = fields.Char(string='Orden de pago')
    orden_de_pago_visible = fields.Boolean(string='Orden de pago Visible', default=True)
    orden_de_pago_id = fields.Many2one('drive', string='Orden de pago', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    consecutivo_orden_pago = fields.Char(string='Consecutivo', tracking=True)
    habilitar_programacion_pago = fields.Boolean(
        string='Habilitar programación de pago',
        tracking=True,
        default=False,
    )
    fecha_programacion_pago = fields.Date(
        string='Fecha informativa/tentativa de pago',
        tracking=True,
    )
    semaforo_programacion_pago = fields.Char(
        string='Semáforo programación pago',
        compute='_compute_semaforo_programacion_pago',
    )
    semaforo_programacion_pago_color = fields.Char(
        string='Color semáforo programación pago',
        compute='_compute_semaforo_programacion_pago',
    )
    alerta_pago_5_dias_enviada = fields.Boolean(
        string='Alerta pago 5 días enviada',
        copy=False,
    )
    alerta_pago_3_dias_enviada = fields.Boolean(
        string='Alerta pago 3 días enviada',
        copy=False,
    )
    alerta_pago_1_dia_enviada = fields.Boolean(
        string='Alerta pago 1 día enviada',
        copy=False,
    )
    estado_programacion_pago = fields.Char(
        string='Estado programación de pago',
        compute='_compute_estado_programacion_pago',
        store=True,
    )
    programacion_pago_fila_ok = fields.Boolean(
        string='Fila con fecha tentativa de pago',
        compute='_compute_programacion_pago_decoracion',
    )
    programacion_pago_fila_pendiente = fields.Boolean(
        string='Fila pendiente fecha tentativa de pago',
        compute='_compute_programacion_pago_decoracion',
    )

    @api.depends('orden_de_pago_id', 'fecha_programacion_pago')
    def _compute_programacion_pago_decoracion(self):
        es_auxiliar = self.env.user.has_group('Matriz.group_tesoreria_auxiliar')
        for record in self:
            record.programacion_pago_fila_ok = (
                es_auxiliar
                and bool(record.orden_de_pago_id)
                and bool(record.fecha_programacion_pago)
            )
            record.programacion_pago_fila_pendiente = (
                es_auxiliar
                and bool(record.orden_de_pago_id)
                and not bool(record.fecha_programacion_pago)
            )

    @api.depends('estado', 'fecha_programacion_pago')
    def _compute_semaforo_programacion_pago(self):
        hoy = fields.Date.context_today(self)
        for record in self:
            record.semaforo_programacion_pago = False
            record.semaforo_programacion_pago_color = False
            if record.estado != 'au_tesoreria' or not record.fecha_programacion_pago:
                continue
            dias = (record.fecha_programacion_pago - hoy).days
            if dias < 0:
                record.semaforo_programacion_pago = 'Vencida'
                record.semaforo_programacion_pago_color = '#dc3545'
            elif dias <= 1:
                record.semaforo_programacion_pago = 'Falta 1 día'
                record.semaforo_programacion_pago_color = '#dc3545'
            elif dias <= 3:
                record.semaforo_programacion_pago = 'Faltan 3 días'
                record.semaforo_programacion_pago_color = '#ffc107'
            elif dias <= 5:
                record.semaforo_programacion_pago = 'Faltan 5 días'
                record.semaforo_programacion_pago_color = '#ffc107'
            else:
                record.semaforo_programacion_pago = 'A tiempo'
                record.semaforo_programacion_pago_color = '#198754'

    @api.depends('estado', 'orden_de_pago_id', 'habilitar_programacion_pago', 'fecha_programacion_pago')
    def _compute_estado_programacion_pago(self):
        for record in self:
            if record.estado == 'finalizado':
                record.estado_programacion_pago = 'Pago finalizado'
            elif record.estado == 'tesorería':
                record.estado_programacion_pago = 'En Tesorería para pago'
            elif record.orden_de_pago_id and record.fecha_programacion_pago:
                record.estado_programacion_pago = 'Orden de pago cargada - fecha tentativa registrada'
            elif record.orden_de_pago_id:
                record.estado_programacion_pago = 'Orden de pago cargada - pendiente fecha tentativa de pago'
            else:
                record.estado_programacion_pago = 'Sin orden de pago cargada'

    def _marcar_alerta_programacion_pago_enviada(self, dias):
        campo = {
            5: 'alerta_pago_5_dias_enviada',
            3: 'alerta_pago_3_dias_enviada',
            1: 'alerta_pago_1_dia_enviada',
        }.get(dias)
        if campo:
            self.sudo().write({campo: True})

    def _alerta_programacion_pago_ya_enviada(self, dias):
        campo = {
            5: 'alerta_pago_5_dias_enviada',
            3: 'alerta_pago_3_dias_enviada',
            1: 'alerta_pago_1_dia_enviada',
        }.get(dias)
        return bool(campo and self[campo])

    def _enviar_alerta_programacion_pago(self, dias):
        self.ensure_one()
        usuarios = self.usuarios_auxiliar_tesoreria.filtered(lambda user: user.active and user.email)
        if not usuarios:
            return False
        fecha = self.fecha_programacion_pago.strftime('%d/%m/%Y') if self.fecha_programacion_pago else ''
        mensaje = (
            f"La cuenta de cobro de {self.name} tiene fecha tentativa de pago "
            f"para el {fecha}.\n\n"
            f"Esta alerta se envía porque faltan {dias} día(s) para la fecha tentativa registrada.\n\n"
            "Por favor revise si ya cuenta con la fecha concreta o si debe actualizar la programación."
        )
        self._enviar_correo(
            usuarios,
            f"Alerta programación de pago - faltan {dias} día(s)",
            mensaje,
            "",
        )
        self._marcar_alerta_programacion_pago_enviada(dias)
        return True

    @api.model
    def cron_alerta_programacion_pago_auxiliar_tesoreria(self):
        hoy = fields.Date.context_today(self)
        for dias in (5, 3, 1):
            fecha_objetivo = hoy + timedelta(days=dias)
            campo_alerta = {
                5: 'alerta_pago_5_dias_enviada',
                3: 'alerta_pago_3_dias_enviada',
                1: 'alerta_pago_1_dia_enviada',
            }[dias]
            registros = self.sudo().search([
                ('estado', '=', 'au_tesoreria'),
                ('fecha_programacion_pago', '=', fecha_objetivo),
                (campo_alerta, '=', False),
                ('usuarios_auxiliar_tesoreria', '!=', False),
            ])
            for record in registros:
                if not record._alerta_programacion_pago_ya_enviada(dias):
                    record._enviar_alerta_programacion_pago(dias)

    def action_open_orden_de_pago_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Orden de pago',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.orden_de_pago_id.id,
            'target': 'new',
        }

    def action_open_soporte_rechazo_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Nota crédito / soporte de rechazo',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.soporte_rechazo_id.id,
            'target': 'new',
        }

    def _upload_orden_de_pago_to_drive(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive, evitando duplicados."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        stage6_record = self.drive_stage_provedor_id

        if not stage6_record:
            _logger.error(f"No se encontró un registro en cuenta_de_cobros para {self.name}. No se subirá el archivo.")
            return False

        carpeta_drive = stage6_record.folder_id

        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para {self.name}. No se subirá el archivo.")
            return False

        # Validar que el archivo binario existe
        if not self.orden_de_pago_documento:
            _logger.error("No hay contenido en 'orden_de_pago_documento'. No se puede subir el archivo.")
            return False

        temp_file_path = None

        try:
            original_extension = os.path.splitext(self.orden_de_pago_name or '')[-1] or ".bin"
            fixed_file_name = f"Orden de pago_{uuid.uuid4()}{original_extension}"

            # Buscar si ya existe un archivo con el mismo prefijo en el nombre
            existing_drive_record = self.env['drive'].search([
                ('folder_id', '=', carpeta_drive),
                ('file_name', 'like', 'Orden de pago_%')
            ], limit=1)

            file_content = base64.b64decode(self.orden_de_pago_documento)

            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file.flush()
                temp_file_path = temp_file.name

            if not os.path.exists(temp_file_path):
                _logger.error(f"El archivo temporal no se creó correctamente: {temp_file_path}")
                return False

            if existing_drive_record:
                file_id = existing_drive_record.file_id
                file_drive = drive.CreateFile({'id': file_id})
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_link = f"https://drive.google.com/file/d/{file_id}/view"

                _logger.info(f"Archivo existente {fixed_file_name} actualizado en Google Drive con ID: {file_id}")
                # Actualizar datos en Odoo
                existing_drive_record.write({
                    'file_name': fixed_file_name,
                    'file_data': False,
                    'file_url': file_link
                })
                drive_record = existing_drive_record
            else:
                # Crear nuevo archivo en Google Drive
                file_metadata = {
                    'title': fixed_file_name,
                    'mimeType': 'application/octet-stream',
                    'parents': [{'id': carpeta_drive}]
                }
                file_drive = drive.CreateFile(file_metadata)
                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload()
                file_id = file_drive['id']
                file_link = f"https://drive.google.com/file/d/{file_id}/view"
                _logger.info(f"Archivo subido a Google Drive con ID: {file_id} y URL: {file_link}")
                # Crear registro en Odoo
                drive_record = self.env['drive'].create({
                    'file_name': fixed_file_name,
                    'file_id': file_id,
                    'file_url': file_link,
                    'file_data': False,
                    'folder_id': carpeta_drive,
                    'stage4_id': stage6_record.id
                })

            # Asignar el registro al campo de referencia si no está asignado
            if not self.orden_de_pago_id:
                self.write({'orden_de_pago_id': drive_record.id})

            _logger.info(f"Campo orden_de_pago_id actualizado con ID: {drive_record.id}")

            # Ocultar el campo después de la subida
            self.write({'orden_de_pago_visible': False})
            self.write({'orden_de_oago': True})
            self.write({'orden_de_pago_documento': False})


            # ✅ Trazabilidad: Agregar mensaje al chatter
            self.message_post(
                body=f"✅ El documento <b>Orden de pago</b> fue subido exitosamente a Google Drive:<br>"
                    ,
                subtype_xmlid="mail.mt_note"
            )


        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {e}")
            return False

        return True
            

    resolucion_facturacion_electronica = fields.Boolean(string='Resolución de Facturación Electrónica')
    resolucion_facturacion_electronica_documento = fields.Binary(string='Resolución de Facturación Electrónica')
    resolucion_facturacion_electronica_name = fields.Char(string='Resolución de Facturación Electrónica')
    resolucion_facturacion_electronica_visible = fields.Boolean(string='Resolución de Facturación Electrónica Visible', default=True, related='relacion_fuv_id.resolucion_facturacion_ju_doc_visible')
    resolucion_facturacion_electronica_id = fields.Many2one('drive', string='Resolución de Facturación Electrónica', compute='_compute_resolucion_facturacion_electronica_id', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    resolucion_facturacion_electronica_2_id = fields.Many2one('drive', string='Resolución de Facturación Electrónica 2', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    resolucion_facturacion_electronica_actual_id = fields.Many2one(
        'drive',
        string='Resolución de Facturación Electrónica vigente',
        compute='_compute_resolucion_facturacion_electronica_actual_id',
    )
    # Convertimos el booleano en automático
    resolucion_facturacion_copiada = fields.Boolean(
        string='¿Resolución de Facturación Electrónica copiada?', 
        compute='_compute_resolucion_facturacion_copiada_status', 
    )

    @api.depends('resolucion_facturacion_electronica_actual_id')
    def _compute_resolucion_facturacion_copiada_status(self):
        for record in self:
            record.resolucion_facturacion_copiada = bool(
                record.resolucion_facturacion_electronica_actual_id
            )

    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_otrosi_fuv_id',
        'origen_cuenta',
        'cuenta_id.login_id.contratistas.resolucion_facturacion_electronica_id',
    )
    def _compute_resolucion_facturacion_electronica_id(self):
        for record in self:
            new_value = False

            # --- Asignar el documento según el tipo seleccionado ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.resolucion_facturacion_ju_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.resolucion_facturacion_ju_id
            elif record.origen_cuenta == 'regalias' and record.cuenta_id.login_id.contratistas:
                new_value = (
                    record.cuenta_id.login_id.contratistas
                    .resolucion_facturacion_electronica_id
                )

            record.resolucion_facturacion_electronica_id = new_value

    @api.depends(
        'resolucion_facturacion_electronica_2_id',
        'resolucion_facturacion_electronica_id',
        'factura_id',
    )
    def _compute_resolucion_facturacion_electronica_actual_id(self):
        """Nunca presenta la Factura como si fuera la Resolución."""
        for record in self:
            factura = record.factura_id
            copia = record.resolucion_facturacion_electronica_2_id
            fuente = record.resolucion_facturacion_electronica_id
            if copia and copia != factura:
                record.resolucion_facturacion_electronica_actual_id = copia
            elif fuente and fuente != factura:
                record.resolucion_facturacion_electronica_actual_id = fuente
            else:
                record.resolucion_facturacion_electronica_actual_id = False


    
    # -----------------------------------------------------------
    # BOTÓN: Copiar Resolución de Facturación Electrónica al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_resolucion_facturacion_electronica_a_drive_proveedor(self):
        """Crea una copia del archivo 'Resolución de Facturación Electrónica'
        dentro de la carpeta del proveedor. Si ya existe, no la vuelve a crear."""
        for record in self:
            if not record.resolucion_facturacion_electronica_id:
                raise UserError("No hay archivo de Resolución de Facturación Electrónica seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.resolucion_facturacion_electronica_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión con Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # La copia usa un prefijo documental propio. Así una factura
                # cuyo nombre comience por "Factura_" nunca puede ser tomada
                # como la Resolución de facturación.
                original_extension = os.path.splitext(original_name or '')[-1] or '.bin'
                copy_name = (
                    f"12_Resolución de Facturación Electrónica_"
                    f"{uuid.uuid4()}{original_extension}"
                )

                # 🔍 Verificar si ya existe una copia de este tipo, sin
                # reutilizar bajo ninguna circunstancia el registro de Factura.
                existing_copy = record.resolucion_facturacion_electronica_2_id
                if (
                    not existing_copy
                    or existing_copy == record.factura_id
                    or existing_copy.folder_id != folder_id
                ):
                    existing_copy = self.env['drive'].search([
                        ('folder_id', '=', folder_id),
                        ('file_name', 'like', '12_Resolución de Facturación Electrónica_%'),
                        ('stage4_id', '=', drive_target.id),
                        ('id', '!=', record.factura_id.id or 0),
                    ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    record.resolucion_facturacion_electronica_2_id = existing_copy.id
                    record.resolucion_facturacion_electronica = True
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia de Resolución de Facturación Electrónica creada correctamente en Google Drive con ID: {new_file_id}")

                record.resolucion_facturacion_electronica_2_id = new_drive_record.id
                record.resolucion_facturacion_electronica = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>Copia de la Resolución de Facturación Electrónica</b> "
                        f"en Google Drive:<br>"
                        f"<a href='{new_file_url}' target='_blank'>{new_file_url}</a>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo de Resolución de Facturación Electrónica en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar la Resolución de Facturación Electrónica en Google Drive:\n{e}")


    # La solicitud de correccion pertenece al tipo documental del acta, no al
    # archivo de Drive. Dos campos pueden apuntar accidentalmente al mismo
    # registro de Drive y, si se usa ``actualizar_visible`` como related, marcar
    # Factura tambien termina marcando Resolucion (o viceversa).
    resolucion_facturacion_electronica_corregir = fields.Boolean(
        string='Resolución de facturación electrónica',
        default=False,
        tracking=True,
    )

    def action_open_resolucion_facturacion_electronica_drive(self):
        self.ensure_one()

        # 📨 Mantiene tu lógica original
        self.actualizar_resolucion_facturacion_electronica_en_drive()

        drive_record = self.resolucion_facturacion_electronica_actual_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Oculta contenido binario del archivo
                })
            else:
                # Si ya tiene los HTML, igual ocultamos el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (como tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolución de Facturación Electrónica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }

    def actualizar_resolucion_facturacion_electronica_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if (
            self.resolucion_facturacion_electronica_2_id
            and self.resolucion_facturacion_electronica_2_id != self.factura_id
        ):
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.resolucion_facturacion_electronica_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.resolucion_facturacion_electronica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_documento_persona_robusto_to_drive(
        self,
        binary_field,
        filename_field,
        drive_field,
        visible_field,
        done_field,
        file_prefix,
        document_label,
        silent_context_key=False,
    ):
        """Subida robusta reutilizable basada en la certificación bancaria jurídica."""
        self.ensure_one()

        binary_value = getattr(self, binary_field, False)
        if not binary_value:
            _logger.info(
                "[DRIVE_RETRY] Acta %s sin binario pendiente para %s.",
                self.id,
                binary_field,
            )
            return False

        stage_record = self.drive_stage_provedor_id
        if not stage_record or not stage_record.folder_id:
            _logger.error(
                "[DRIVE_RETRY] Acta=%s cuenta=%s sin carpeta destino para %s. "
                "El binario se conserva para reintento.",
                self.id,
                self.cuenta_id.id if self.cuenta_id else False,
                binary_field,
            )
            return False

        if (
            'contratistas_provedores' in stage_record._fields
            and stage_record.contratistas_provedores
            and stage_record.contratistas_provedores != self
        ):
            _logger.error(
                "[DRIVE_RETRY] BLOQUEADO: acta=%s intenta usar carpeta=%s "
                "vinculada a acta=%s. Documento=%s.",
                self.id,
                stage_record.folder_id,
                stage_record.contratistas_provedores.id,
                binary_field,
            )
            return False

        carpeta_drive = str(stage_record.folder_id).strip()
        temp_file_path = False

        def _get_fresh_upload_drive():
            # No se reutiliza el transporte HTTP entre archivos. Una conexión
            # persistente que Drive haya cerrado después de una carga puede
            # dejar bloqueado indefinidamente el siguiente ``Upload``.
            return GoogleDriveAuth(http_timeout_seconds=20).get_drive()

        try:
            drive = _get_fresh_upload_drive()

            folder_drive = drive.CreateFile({'id': carpeta_drive})
            folder_drive.FetchMetadata(fields='id,title,labels')
            if (folder_drive.get('labels') or {}).get('trashed'):
                _logger.error(
                    "[DRIVE_RETRY] Carpeta destino eliminada. acta=%s folder_id=%s documento=%s",
                    self.id,
                    carpeta_drive,
                    binary_field,
                )
                return False

            original_name = getattr(self, filename_field, False) or (
                '%s.bin' % binary_field.replace('_documento', '').replace('_doc', '')
            )
            original_extension = os.path.splitext(original_name)[-1].lower() or '.bin'
            mime_type = {
                '.pdf': 'application/pdf',
                '.doc': 'application/msword',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.xls': 'application/vnd.ms-excel',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.csv': 'text/csv',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
            }.get(original_extension, 'application/octet-stream')

            fixed_file_name = (
                f"{file_prefix}_acta_{self.id}_"
                f"cuenta_{self.cuenta_id.id if self.cuenta_id else 0}{original_extension}"
            )

            try:
                file_content = base64.b64decode(binary_value)
            except Exception:
                _logger.exception(
                    "[DRIVE_RETRY] Binario inválido. acta=%s campo=%s nombre=%s",
                    self.id,
                    binary_field,
                    original_name,
                )
                return False

            if not file_content:
                _logger.error(
                    "[DRIVE_RETRY] Binario vacío. acta=%s campo=%s nombre=%s",
                    self.id,
                    binary_field,
                    original_name,
                )
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            existing_drive_record = (
                getattr(self, drive_field, False)
                if drive_field and drive_field in self._fields
                else False
            )
            if (
                not existing_drive_record
                or existing_drive_record.folder_id != carpeta_drive
                or not existing_drive_record.file_id
            ):
                existing_drive_record = self.env['drive'].sudo().search([
                    ('folder_id', '=', carpeta_drive),
                    ('stage4_id', '=', stage_record.id),
                    ('file_name', 'like', f'{file_prefix}_%'),
                ], order='id desc', limit=1)

            last_error = False
            max_attempts = 2

            for attempt in range(1, max_attempts + 1):
                try:
                    _logger.info(
                        "[DRIVE_RETRY] intento=%s/%s acta=%s cuenta=%s stage=%s "
                        "folder_id=%s folder_name=%s documento=%s archivo=%s",
                        attempt,
                        max_attempts,
                        self.id,
                        self.cuenta_id.id if self.cuenta_id else False,
                        stage_record.id,
                        carpeta_drive,
                        stage_record.folder_name,
                        binary_field,
                        fixed_file_name,
                    )

                    remote_file = False
                    remote_file_id = False

                    if existing_drive_record and existing_drive_record.file_id:
                        candidate = drive.CreateFile({'id': existing_drive_record.file_id})
                        try:
                            candidate.FetchMetadata(fields='id,title,parents,labels,mimeType')
                            parent_ids = [
                                parent.get('id')
                                for parent in (candidate.get('parents') or [])
                                if parent.get('id')
                            ]
                            es_google_nativo = (
                                candidate.get('mimeType') or ''
                            ).startswith('application/vnd.google-apps.')
                            if (
                                not (candidate.get('labels') or {}).get('trashed')
                                and carpeta_drive in parent_ids
                                and not es_google_nativo
                            ):
                                remote_file = candidate
                                remote_file_id = candidate.get('id')
                            elif es_google_nativo:
                                _logger.warning(
                                    "[DRIVE_RETRY] El remoto %s es Google nativo; "
                                    "se creará un archivo binario nuevo conservando el registro Odoo.",
                                    existing_drive_record.file_id,
                                )
                            else:
                                _logger.warning(
                                    "[DRIVE_RETRY] El drive.file %s apunta a un remoto fuera "
                                    "de la carpeta esperada. Se recuperará/creará uno correcto.",
                                    existing_drive_record.id,
                                )
                        except Exception:
                            _logger.warning(
                                "[DRIVE_RETRY] No se pudo validar el remoto existente %s; "
                                "se intentará recuperar por nombre y carpeta.",
                                existing_drive_record.file_id,
                                exc_info=True,
                            )

                    if not remote_file:
                        safe_title = fixed_file_name.replace("\\", "\\\\").replace("'", "\\'")
                        query = (
                            f"title='{safe_title}' and '{carpeta_drive}' in parents "
                            f"and trashed=false"
                        )
                        recovered = drive.ListFile({'q': query}).GetList()
                        if recovered:
                            remote_file = drive.CreateFile({'id': recovered[0]['id']})
                            remote_file.FetchMetadata(fields='id,title,parents,labels,mimeType')
                            if not (remote_file.get('mimeType') or '').startswith(
                                'application/vnd.google-apps.'
                            ):
                                remote_file_id = remote_file.get('id')
                            else:
                                remote_file = False
                            if remote_file:
                                _logger.warning(
                                    "[DRIVE_RETRY] Recuperado archivo remoto de intento anterior: %s",
                                    remote_file_id,
                                )

                    if not remote_file:
                        remote_file = drive.CreateFile({
                            'title': fixed_file_name,
                            'mimeType': mime_type,
                            'parents': [{'id': carpeta_drive}],
                        })

                    remote_file['title'] = fixed_file_name
                    remote_file.SetContentFile(temp_file_path)
                    remote_file.Upload()

                    remote_file_id = remote_file.get('id') or remote_file_id
                    if not remote_file_id:
                        raise UserError("Google Drive no devolvió un file_id después de la subida.")

                    verify_file = drive.CreateFile({'id': remote_file_id})
                    verify_file.FetchMetadata(fields='id,title,parents,labels')
                    verify_parent_ids = [
                        parent.get('id')
                        for parent in (verify_file.get('parents') or [])
                        if parent.get('id')
                    ]
                    if (verify_file.get('labels') or {}).get('trashed'):
                        raise UserError("El archivo subido aparece eliminado en Google Drive.")
                    if carpeta_drive not in verify_parent_ids:
                        raise UserError(
                            "Google Drive confirmó el archivo, pero no quedó en la carpeta esperada."
                        )

                    file_link = f"https://drive.google.com/file/d/{remote_file_id}/view"
                    preview_url = f"https://drive.google.com/file/d/{remote_file_id}/preview"
                    preview_html = (
                        f'<iframe src="{preview_url}" width="100%" height="600px" '
                        'loading="lazy" frameborder="0" allowfullscreen></iframe>'
                    )
                    edit_html = (
                        f'<iframe src="{file_link}" width="100%" height="600px" '
                        'loading="lazy" frameborder="0"></iframe>'
                    )

                    all_drive_vals = {
                        'file_name': fixed_file_name,
                        'name': fixed_file_name,
                        'file_id': remote_file_id,
                        'file_url': file_link,
                        'preview_url': preview_url,
                        'edit_url': file_link,
                        'preview_html': preview_html,
                        'edit_html': edit_html,
                        'file_data': False,
                        'folder_id': carpeta_drive,
                        'stage4_id': stage_record.id,
                    }
                    # El archivo corregido ya fue verificado. Al retirar esta
                    # marca, la finalización puede comprobar que no quedaron
                    # documentos pendientes antes de cerrar la corrección.
                    if 'actualizar_visible' in self.env['drive']._fields:
                        all_drive_vals['actualizar_visible'] = False
                    drive_model = self.env['drive']
                    drive_vals = {
                        key: value
                        for key, value in all_drive_vals.items()
                        if key in drive_model._fields
                    }

                    drive_record = existing_drive_record
                    if not drive_record or drive_record.file_id != remote_file_id:
                        drive_record = drive_model.sudo().search([
                            ('file_id', '=', remote_file_id),
                        ], limit=1)

                    if drive_record:
                        drive_record.sudo().write(drive_vals)
                    else:
                        drive_record = drive_model.sudo().create(drive_vals)

                    record_vals = {}
                    if drive_field and drive_field in self._fields:
                        record_vals[drive_field] = drive_record.id
                    if binary_field in self._fields:
                        record_vals[binary_field] = False
                    if visible_field and visible_field in self._fields:
                        record_vals[visible_field] = False
                    if done_field and done_field in self._fields:
                        record_vals[done_field] = True

                    self.with_context(
                        allow_person_document_write=True,
                        mail_notrack=True,
                        tracking_disable=True,
                    ).write(record_vals)

                    if not silent_context_key or not self.env.context.get(silent_context_key):
                        self.message_post(
                            body=(
                                f"✅ El documento <b>{document_label}</b> fue subido "
                                "y verificado correctamente en Google Drive."
                            ),
                            subtype_xmlid="mail.mt_note",
                        )

                    _logger.info(
                        "[DRIVE_RETRY] ÉXITO acta=%s drive_record=%s file_id=%s "
                        "folder_id=%s documento=%s",
                        self.id,
                        drive_record.id,
                        remote_file_id,
                        carpeta_drive,
                        binary_field,
                    )
                    return drive_record

                except Exception as error:
                    # Los conflictos de concurrencia pertenecen a PostgreSQL,
                    # no a Drive. Deben llegar al manejador de Odoo para que
                    # repita la transacción completa con un cursor limpio.
                    if getattr(error, 'pgcode', None) in ('40001', '40P01', '55P03'):
                        raise
                    last_error = error
                    _logger.warning(
                        "[DRIVE_RETRY] Falló intento %s/%s. acta=%s folder_id=%s "
                        "documento=%s error=%s",
                        attempt,
                        max_attempts,
                        self.id,
                        carpeta_drive,
                        binary_field,
                        error,
                        exc_info=True,
                    )
                    if attempt < max_attempts:
                        time.sleep(2 ** attempt)
                        drive = _get_fresh_upload_drive()

            _logger.error(
                "[DRIVE_RETRY] Subida pendiente tras %s intentos. acta=%s "
                "folder_id=%s documento=%s. El binario se conserva. Último error=%s",
                max_attempts,
                self.id,
                carpeta_drive,
                binary_field,
                last_error,
            )
            return False

        except Exception as error:
            if getattr(error, 'pgcode', None) in ('40001', '40P01', '55P03'):
                raise
            _logger.exception(
                "[DRIVE_RETRY] Error preparando la subida. acta=%s cuenta=%s "
                "documento=%s error=%s",
                self.id,
                self.cuenta_id.id if self.cuenta_id else False,
                binary_field,
                error,
            )
            return False

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    _logger.warning(
                        "[DRIVE_RETRY] No se pudo eliminar temporal %s",
                        temp_file_path,
                        exc_info=True,
                    )


    def _upload_resolucion_facturacion_electronica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'resolucion_facturacion_electronica_documento',
            'resolucion_facturacion_electronica_name',
            'resolucion_facturacion_electronica_2_id',
            False,
            'resolucion_facturacion_electronica',
            '12_Resolución de Facturación Electrónica',
            'Resolución de facturación electrónica',
            False,
        )

    certificacion_conjunta = fields.Boolean(string='Certificación Conjunta')
    certificacion_conjunta_documento = fields.Binary(string='Certificación Conjunta', related='relcion_cer_conjunto_ids.informe_adjunto')
    certificacion_conjunta_name = fields.Char(string='Certificación Conjunta', related='relcion_cer_conjunto_ids.informe_filename')
    certificacion_conjunta_visible = fields.Boolean(string='Certificación Conjunta Visible', default=True)
    certificacion_conjunta_id = fields.Many2one('drive', string='Certificación Conjunta', tracking=True)



    def action_open_certificacion_conjunta_drive(self):
        self.ensure_one()
        self._refresh_certificacion_conjunta_drive_from_template()
        if self.certificacion_conjunta_id:
            drive_record = self.certificacion_conjunta_id.sudo()
            drive_record.with_context(
                skip_drive_sync=True,
                skip_integrity=True,
            ).write({'file_data_visible': False})
            drive_record.asegurar_visor_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación Conjunta',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificacion_conjunta_id.id if self.certificacion_conjunta_id else False,
            'target': 'new',
            'context': self._drive_readonly_context(),
        }

    def _upload_certificacion_conjunta_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificacion_conjunta_documento',
            'certificacion_conjunta_name',
            'certificacion_conjunta_id',
            'certificacion_conjunta_visible',
            'certificacion_conjunta',
            '1_Certificación Conjunta',
            'Certificación conjunta',
            'silent_certificacion_conjunta_auto',
        )

    certificacion_conjunta_firmado = fields.Boolean(string='Certificación conjunta firmada', tracking=True)
    certificacion_conjunta_firmado_documento = fields.Binary(string='Certificación conjunta firmada')
    certificacion_conjunta_firmado_name = fields.Char(string='Certificación conjunta firmada')
    certificacion_conjunta_firmado_visible = fields.Boolean(string='Certificación conjunta firmada Visible', default=True)
    certificacion_conjunta_firmado_id = fields.Many2one('drive', string='Certificación conjunta firmada')



    def action_open_certificacion_conjunta_firmado_drive(self):
        self.ensure_one()
        if self.certificacion_conjunta_firmado_id:
            drive_record = self.certificacion_conjunta_firmado_id.sudo()
            drive_record.with_context(
                skip_drive_sync=True,
                skip_integrity=True,
            ).write({'file_data_visible': False})
            drive_record.asegurar_visor_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación conjunta firmada',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificacion_conjunta_firmado_id.id,
            'target': 'new',
        }

    def _upload_certificacion_conjunta_firmado_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificacion_conjunta_firmado_documento',
            'certificacion_conjunta_firmado_name',
            'certificacion_conjunta_firmado_id',
            'certificacion_conjunta_firmado_visible',
            False,
            '1_Certificación Conjunta firmada',
            'Certificación conjunta firmada',
            False,
        )

    documento_equivalente = fields.Boolean(string='Documento equivalente')
    documento_equivalente_documento = fields.Binary(string='Documento equivalente')
    documento_equivalente_name = fields.Char(string='Documento equivalente')
    documento_equivalente_visible = fields.Boolean(string='Documento equivalente Visible', default=True)
    documento_equivalente_id = fields.Many2one('drive', string='Documento equivalente', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    documento_equivalente_corregir = fields.Boolean(default=False,  related='documento_equivalente_id.actualizar_visible', readonly=False)


    token = fields.Char(string="Token", copy=False, related="cuenta_id.token")


    def action_open_documento_equivalente_drive(self):
        self.ensure_one()
        self.actualizar_documento_equivalente_en_drive()
        self.documento_equivalente_id.asegurar_visor_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento Equivalente',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.documento_equivalente_id.id,
            'target': 'new',
        }

    def actualizar_documento_equivalente_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.documento_equivalente_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.documento_equivalente_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.documento_equivalente_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False


    def _upload_documento_equivalente_to_drive(self):
        solo_declaracion = bool(
            self.cuenta_id
            and (
                self.cuenta_id.factura_si_o_no
                or self.cuenta_id.factura_id
                or (
                    self.cuenta_id.numero_factura
                    and self.cuenta_id.numero_factura.strip()
                    and self.cuenta_id.numero_factura.strip().upper() not in ('N/A', 'NA')
                )
            )
        )
        file_prefix = (
            '2_Declaracion Juramentada'
            if solo_declaracion
            else '2_Documento Equivalente'
        )
        return self._upload_documento_persona_robusto_to_drive(
            'documento_equivalente_documento',
            'documento_equivalente_name',
            'documento_equivalente_id',
            'documento_equivalente_visible',
            'documento_equivalente',
            file_prefix,
            'Declaración juramentada' if solo_declaracion else 'Documento equivalente',
        )

    informe_de_actividades = fields.Boolean(string='Informe de actividades')
    informe_de_actividades_documento = fields.Binary(string='Informe de actividades')
    informe_de_actividades_name = fields.Char(string='Informe de actividades')
    informe_de_actividades_visible = fields.Boolean(string='Informe de actividades Visible', default=True)
    informe_de_actividades_id = fields.Many2one('drive', string='Informe de actividades')

    informe_de_actividades_corregir = fields.Boolean(default=False, related='informe_de_actividades_id.actualizar_visible', readonly=False)
    informe_vobo_coordinador = fields.Boolean(string='Vo.Bo. Coordinador del Proyecto', tracking=True)

    def _refresh_documento_equivalente_drive_from_template(self):
        """Regenera el formato combinado antes de incorporarlo al PDF final."""
        self.ensure_one()
        if not self.cuenta_id:
            return False
        declaracion = self.env['declaracion_juramentada'].sudo().search([
            ('relacion', '=', self.cuenta_id.id),
        ], order='id desc', limit=1)
        if not declaracion:
            return False
        return declaracion.with_context(
            raise_document_generation_error=True,
            skip_refirma_por_cambio=True,
            mail_create_nosubscribe=True,
            mail_notrack=True,
            tracking_disable=True,
        ).generar_informe_xlsx()

    def _refresh_informe_de_actividades_drive_from_template(self):
        self.ensure_one()
        informe = self.env['informe_actividades'].sudo().search([
            '|',
            ('relacion_id', '=', self.id),
            ('cuenta_cobro_id', '=', self.cuenta_id.id if self.cuenta_id else 0),
        ], order='id desc', limit=1)
        if not informe:
            return False

        return informe.with_context(
            silent_informe_vobo_update=True,
            skip_refirma_por_cambio=True,  # ✅ ESTA LÍNEA EVITA EL RESETEO DE FIRMAS
            mail_create_nosubscribe=True,
            mail_notrack=True,
            tracking_disable=True,
            force_regen=True,  # 🔥 NUEVO: Forzar regeneración
        ).generar_informe_xlsx()

    def action_open_informe_de_actividades_drive(self):
        self.ensure_one()
        self._refresh_informe_de_actividades_drive_from_template()
        self.actualizar_correo_informe_de_actividades_en_drive()
        
        drive_record = self.informe_de_actividades_id
        if drive_record and drive_record.file_id:
            # Asegurar que el visor esté configurado SIN pedir permisos
            drive_record.asegurar_visor_drive()
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Informe de Actividades',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
            'context': {
                'form_view_initial_mode': 'readonly',
                'skip_permission_check': True,
            }
        }

    file_data_visible = fields.Boolean(default=False, string='Actualizado', tracking=True)

    def actualizar_correo_informe_de_actividades_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.informe_de_actividades_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.informe_de_actividades_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.informe_de_actividades_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_informe_de_actividades_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'informe_de_actividades_documento',
            'informe_de_actividades_name',
            'informe_de_actividades_id',
            'informe_de_actividades_visible',
            'informe_de_actividades',
            '3_Informe de Actividades',
            'Informe de actividades',
            'silent_informe_vobo_update',
        )

    declaracion_juramentada_y_soportes = fields.Boolean(string='Declaración juramentada y soportes', tracking=True)
    declaracion_juramentada_y_soportes_documento = fields.Binary(string='Declaración juramentada y soportes')
    declaracion_juramentada_y_soportes_name = fields.Char(string='Declaración juramentada y soportes')
    declaracion_juramentada_y_soportes_visible = fields.Boolean(string='Declaración juramentada y soportes Visible')
    declaracion_juramentada_y_soportes_id = fields.Many2one('drive', string='Declaración juramentada y soportes')

    declaracion_juramentada_y_soportes_corregir = fields.Boolean(default=False, related='declaracion_juramentada_y_soportes_id.actualizar_visible', readonly=False)


    def action_open_declaracion_juramentada_y_soportes_drive(self):
        self.ensure_one()
        self.actualizar_correo_declaracion_juramentada_y_soportes__en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Declaración Juramentada y Soportes',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.declaracion_juramentada_y_soportes_id.id,
            'target': 'new',
        }


    def actualizar_correo_declaracion_juramentada_y_soportes__en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.declaracion_juramentada_y_soportes_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.declaracion_juramentada_y_soportes_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.declaracion_juramentada_y_soportes_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False


    def _upload_declaracion_juramentada_y_soportes_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'declaracion_juramentada_y_soportes_documento',
            'declaracion_juramentada_y_soportes_name',
            'declaracion_juramentada_y_soportes_id',
            'declaracion_juramentada_y_soportes_visible',
            False,
            '4_Declaración Juramentada y Soportes',
            'Declaración juramentada y soportes',
            False,
        )

    rut_actualizado = fields.Boolean(string='RUT actualizado')
    rut_actualizado_documento = fields.Binary(string='RUT actualizado')
    rut_actualizado_name = fields.Char(string='RUT actualizado')
    rut_actualizado_visible = fields.Boolean(string='RUT actualizado Visible', default=True)
    rut_actualizado_id = fields.Many2one('drive', string='RUT actualizado')

    

    rut_actualizado_corregir = fields.Boolean(default=False, related='rut_actualizado_id.actualizar_visible', readonly=False)

    def action_open_rut_actualizado_drive(self):
        self.ensure_one()
        self.actualizar_correo_rut_actualizado_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT Actualizado',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_actualizado_id.id,
            'target': 'new',
            'context': self._drive_readonly_context(),
        }

    def actualizar_correo_rut_actualizado_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.rut_actualizado_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.rut_actualizado_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.rut_actualizado_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_rut_actualizado_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'rut_actualizado_documento',
            'rut_actualizado_name',
            'rut_actualizado_id',
            'rut_actualizado_visible',
            'rut_actualizado',
            '5_RUT Actualizado',
            'RUT actualizado',
            False,
        )

    anexo_tributario = fields.Boolean(string='Anexo tributario')
    anexo_tributario_documento = fields.Binary(string='Anexo tributario')
    anexo_tributario_name = fields.Char(string='Anexo tributario')
    anexo_tributario_visible = fields.Boolean(string='Anexo tributario Visible', default=True, related='relacion_fuv_id.anexo_tributario_doc_visible')
    anexo_tributario_id = fields.Many2one('drive', string='Anexo tributario',compute='_compute_anexo_tributario', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    # -----------------------------------------------------------
    # MODIFICACIÓN: Campo Computed con Store=True
    # -----------------------------------------------------------
    anexo_tributario_2_id = fields.Many2one('drive', string='Anexo tributario Copia', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    
    # 1. Cambiamos la definición del booleano para que sea CALCULADO
    anexo_copiado = fields.Boolean(
        string='¿Anexo copiado?', 
        compute='_compute_anexo_copiado_status', 
    )
    anexo_tributario_corregir = fields.Boolean(default=False, related='anexo_tributario_2_id.actualizar_visible', readonly=False)

    def actualizar_correo_anexo_tributario_actualizado_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.anexo_tributario_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.anexo_tributario_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.anexo_tributario_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False


    # 2. La función que "vigila" el campo las 24 horas
    @api.depends('anexo_tributario_2_id')
    def _compute_anexo_copiado_status(self):
        for record in self:
            # Lógica simple: Si tiene ID es True, si está vacío es False.
            if record.anexo_tributario_2_id:
                record.anexo_copiado = True
            else:
                record.anexo_copiado = False
    # -----------------------------------------------------------
    # BOTÓN: Copiar Anexo Tributario (Natural) a Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_anexo_a_drive_proveedor_general(self):
        """Crea una copia del archivo 'Anexo Tributario' dentro de la carpeta del proveedor (drive_stage_provedor_id).
        Si ya existe una copia, no vuelve a crearla."""
        for record in self:
            if not record.anexo_tributario_id:
                raise UserError("No hay archivo de Anexo Tributario seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.anexo_tributario_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión con Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    record.anexo_tributario_2_id = existing_copy.id
                    record.anexo_tributario = True
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia de archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia creada correctamente en Google Drive con ID: {new_file_id}")
                
                record.anexo_tributario_2_id = new_drive_record.id

                self.anexo_tributario = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>copia del anexo tributario</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar el archivo en Google Drive:\n{e}")



    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_anexo_tributario(self):
        for record in self:
            new_value = False

            # --- Asignación según tipo de relación ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.anexo_tributario_id
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                new_value = record.relacion_orden_compra_bienes_fuv_id.Anexo_tributario_doc_pn_id
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                new_value = record.relacion_orden_compra_servicios_fuv_id.Anexo_tributario_doc_pn_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.anexo_tributario_id

            record.anexo_tributario_id = new_value

    def action_open_anexo_tributario_drive(self):
        self.ensure_one()
        self.actualizar_correo_anexo_tributario_actualizado_en_drive()

        drive_record = self.anexo_tributario_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Oculta el campo File Data
                })
            else:
                # Incluso si ya existe, forzamos ocultar el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (igual que tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Anexo Tributario',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def _upload_anexo_tributario_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'anexo_tributario_documento',
            'anexo_tributario_name',
            'anexo_tributario_2_id',
            'anexo_tributario_visible',
            'anexo_tributario',
            '6_Anexo Tributario',
            'Anexo tributario',
            False,
        )

    factura = fields.Boolean(string='Factura', tracking=True)
    factura_si_o_no = fields.Boolean(string='Si o No', tracking=True, related="cuenta_id.factura_si_o_no")
    factura_documento = fields.Binary(string='Factura')
    factura_name = fields.Char(string='Factura')
    factura_visible = fields.Boolean(string='Factura Visible', default=True)
    factura_id = fields.Many2one('drive', string='Factura')
    
    factura_corregir = fields.Boolean(
        string='Factura',
        default=False,
        tracking=True,
    )

    def action_open_factura_drive(self):
        self.ensure_one()
        self.actualizar_correo_factura_en_drive()
        return{
            'type': 'ir.actions.act_window',
            'name': 'Factura',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.factura_id.id,
            'target': 'new',
        }
    
    def actualizar_correo_factura_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.factura_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.factura_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.factura_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    
    def _upload_factura_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'factura_documento',
            'factura_name',
            'factura_id',
            'factura_visible',
            'factura',
            'Factura',
            'Factura',
            False,
        )

    otro_documentos_de_informacion = fields.Boolean(string='Documento adicional de informe de actividades')
    otro_documentos_de_informacion_documento = fields.Binary(string='Documento adicional de informe de actividades')
    otro_documentos_de_informacion_name = fields.Char(string='Documento adicional de informe de actividades')
    otro_documentos_de_informacion_visible = fields.Boolean(string='Documento adicional de informe de actividades Visible', default=True)
    otro_documentos_de_inf_id = fields.Many2one('drive', string='Documento adicional de informe de actividades')
    otro_documentos_de_inf_ids = fields.One2many(
        'drive',
        'contratistas_provedores',
        string='Documentos adicionales de informe de actividades',
        domain=[('portal_acta_document_type', '=', 'otro_documentos_de_informacion')],
    )
    otro_documentos_de_inf_count = fields.Integer(
        string='Cantidad de documentos adicionales',
        compute='_compute_portal_acta_document_counts',
    )

    informes_contratista = fields.Boolean(string='Informes del contratista')
    informes_contratista_documento = fields.Binary(string='Informes del contratista')
    informes_contratista_name = fields.Char(string='Informes del contratista')
    informes_contratista_visible = fields.Boolean(
        string='Informes del contratista visibles', default=True
    )
    informes_contratista_id = fields.Many2one('drive', string='Informes del contratista')
    informes_contratista_ids = fields.One2many(
        'drive',
        'contratistas_provedores',
        string='Informes del contratista',
        domain=[('portal_acta_document_type', '=', 'informes_contratista')],
    )
    informes_contratista_count = fields.Integer(
        string='Cantidad de informes del contratista',
        compute='_compute_portal_acta_document_counts',
    )

    cesion_derechos_documento = fields.Binary(string='Documento de cesión de derechos')
    cesion_derechos_name = fields.Char(string='Documento de cesión de derechos')
    cesion_derechos_visible = fields.Boolean(
        string='Cesión de derechos visible', default=True
    )
    cesion_derechos_id = fields.Many2one('drive', string='Documento de cesión de derechos')
    cesion_derechos_ids = fields.One2many(
        'drive',
        'contratistas_provedores',
        string='Documentos de cesión de derechos',
        domain=[('portal_acta_document_type', '=', 'cesion_derechos')],
    )
    cesion_derechos_count = fields.Integer(
        string='Cantidad de documentos de cesión',
        compute='_compute_portal_acta_document_counts',
    )

    opcion_otro_documento = fields.Boolean(string='¿Desea agregar un documento adicional?', related='cuenta_id.opcion_otro_documento')

    informes_contratista_corregir = fields.Boolean(default=False, related='informes_contratista_id.actualizar_visible', readonly=False)
    # Debe existir independientemente del archivo de Drive: si todavía no hay
    # documento adicional, el coordinador también puede solicitar que se cargue.
    # Antes era related a otro_documentos_de_inf_id.actualizar_visible y, sin
    # archivo relacionado, marcar "corregir" no tenía dónde guardarse.
    otro_documentos_de_inf_corregir = fields.Boolean(
        string='Documento adicional',
        default=False,
        tracking=True,
    )
    cesion_derechos_corregir = fields.Boolean(default=False, related='cesion_derechos_id.actualizar_visible', readonly=False)

    def upload_portal_multiple_documents(self, document_kind, uploaded_files):
        self.ensure_one()
        config = {
            'informes_contratista': {
                'id_field': 'informes_contratista_id',
                'visible_field': 'informes_contratista_visible',
                'boolean_field': 'informes_contratista',
                'binary_field': 'informes_contratista_documento',
                'prefix': 'Informe contratista',
                'message': 'El contratista adjuntó sus <b>informes</b>.',
            },
            'otro_documentos_de_informacion': {
                'id_field': 'otro_documentos_de_inf_id',
                'visible_field': 'otro_documentos_de_informacion_visible',
                'boolean_field': 'otro_documentos_de_informacion',
                'binary_field': 'otro_documentos_de_informacion_documento',
                'prefix': 'Documento adicional',
                'message': 'El contratista adjuntó <b>documentos adicionales</b>.',
            },
            'cesion_derechos': {
                'id_field': 'cesion_derechos_id',
                'visible_field': 'cesion_derechos_visible',
                'boolean_field': 'cesion_derechos',
                'binary_field': 'cesion_derechos_documento',
                'prefix': 'Cesion de derechos',
                'message': 'El contratista adjuntó el <b>documento de cesión de derechos</b>.',
            },
        }.get(document_kind)
        if not config or not uploaded_files:
            return False

        document_label = (
            'Informes (opcional)'
            if document_kind == 'informes_contratista'
            else 'Cesión de derechos'
            if document_kind == 'cesion_derechos'
            else 'Documentos adicionales'
        )

        stage_record = self.drive_stage_provedor_id
        if not stage_record or not stage_record.folder_id:
            _logger.error(
                "No se encontró la carpeta de Drive para subir documentos del portal en %s.",
                self.name,
            )
            raise UserError(_(
                "No fue posible subir %s porque la cuenta no tiene una carpeta de Drive configurada."
            ) % document_label)

        created_records = self.env['drive']
        upload_errors = []
        max_upload_attempts = 2

        for uploaded_file in uploaded_files:
            content = uploaded_file.read()
            if not content:
                upload_errors.append(
                    "%s: el archivo llegó vacío" % (uploaded_file.filename or config['prefix'])
                )
                continue

            original_name = uploaded_file.filename or config['prefix']
            base_name, extension = os.path.splitext(original_name)
            extension = extension or '.bin'
            safe_base_name = base_name or config['prefix']
            fixed_file_name = f"{config['prefix']}_{safe_base_name}_{uuid.uuid4()}{extension}"
            temp_file_path = None

            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
                    temp_file.write(content)
                    temp_file_path = temp_file.name

                file_id = False
                last_upload_error = False
                for attempt in range(1, max_upload_attempts + 1):
                    try:
                        # Cada archivo y cada reintento usan un transporte nuevo.
                        # Así una conexión persistente cerrada por Drive no puede
                        # detener el resto del lote ni dejar la petición esperando
                        # indefinidamente.
                        drive = GoogleDriveAuth(http_timeout_seconds=20).get_drive()
                        drive_file = drive.CreateFile({
                            'title': fixed_file_name,
                            'parents': [{'id': stage_record.folder_id}],
                        })
                        drive_file.SetContentFile(temp_file_path)
                        _logger.info(
                            "Portal: subiendo archivo %s de tipo %s para el acta %s "
                            "(intento %s/%s).",
                            original_name,
                            document_kind,
                            self.id,
                            attempt,
                            max_upload_attempts,
                        )
                        drive_file.Upload()
                        file_id = drive_file['id']
                        _logger.info(
                            "Portal: archivo %s de tipo %s subido para el acta %s "
                            "con id %s.",
                            original_name,
                            document_kind,
                            self.id,
                            file_id,
                        )
                        break
                    except Exception as upload_error:
                        last_upload_error = upload_error
                        _logger.warning(
                            "Portal: falló el intento %s/%s al subir %s de tipo %s "
                            "para el acta %s: %s",
                            attempt,
                            max_upload_attempts,
                            original_name,
                            document_kind,
                            self.id,
                            upload_error,
                        )

                if not file_id:
                    raise last_upload_error or UserError(_(
                        "Google Drive no devolvió el identificador del archivo."
                    ))

                preview_url = f"https://drive.google.com/file/d/{file_id}/preview"
                edit_url = f"https://drive.google.com/file/d/{file_id}/edit"

                created_records |= self.env['drive'].sudo().create({
                    'file_name': fixed_file_name,
                    'file_id': file_id,
                    'file_data': False,
                    'file_url': f"https://drive.google.com/file/d/{file_id}/view",
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': (
                        f'<iframe src="{preview_url}" width="100%" height="600px" '
                        'frameborder="0" allowfullscreen="allowfullscreen"></iframe>'
                    ),
                    'edit_html': (
                        f'<iframe src="{edit_url}" width="100%" height="600px" '
                        'frameborder="0"></iframe>'
                    ),
                    'file_data_visible': False,
                    'folder_id': stage_record.folder_id,
                    'stage4_id': stage_record.id,
                    'contratistas_provedores': self.id,
                    'portal_acta_document_type': document_kind,
                })
            except Exception as error:
                _logger.exception(
                    "Error al subir documento múltiple del portal %s para el acta %s",
                    original_name,
                    self.id,
                )
                upload_errors.append("%s: %s" % (original_name, error))
            finally:
                if temp_file_path and os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)

        if not created_records:
            detail = "; ".join(upload_errors) or "No se recibió ningún archivo válido."
            raise UserError(_(
                "No fue posible subir %s. %s"
            ) % (document_label, detail))

        vals = {
            config['visible_field']: False,
            config['binary_field']: False,
            config['boolean_field']: True,
        }
        if not self[config['id_field']]:
            vals[config['id_field']] = created_records[0].id
        super(ActaEntregaDocumentos, self).write(vals)
        self.message_post(body=config['message'], subtype_xmlid="mail.mt_note")
        if upload_errors:
            _logger.warning(
                "Carga parcial de %s en acta %s: %s",
                document_kind,
                self.id,
                "; ".join(upload_errors),
            )
        return len(created_records)

    def action_open_otro_documentos_de_informacion_drive(self):
        self.ensure_one()
        if not self.otro_documentos_de_inf_id:
            return self.action_open_otro_documentos_de_informacion_drive_list()
        self.actualizar_correo_otro_documentos_de_informacion_en_drive()
        self._ensure_drive_document_preview(self.otro_documentos_de_inf_id)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento adicional de informe de actividades',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.otro_documentos_de_inf_id.id,
            'target': 'new',
        }

    def action_open_informes_contratista_drive(self):
        self.ensure_one()
        if not self.informes_contratista_id:
            return self.action_open_informes_contratista_drive_list()
        self._ensure_drive_document_preview(self.informes_contratista_id)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Informes del contratista',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.informes_contratista_id.id,
            'target': 'new',
        }

    def action_open_comprobante_bancario_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Comprobante Bancario',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.comprobante_bancario_id.id,
            'target': 'new',
        }

    def _ensure_drive_document_preview(self, drive_record):
        """Completa el visor de registros de Drive creados desde el portal."""
        self.ensure_one()
        if not drive_record:
            return False

        file_id = self._resolve_google_drive_file_id(drive_record)
        if not file_id:
            return False

        preview_url = f"https://drive.google.com/file/d/{file_id}/preview"
        edit_url = f"https://drive.google.com/file/d/{file_id}/edit"
        drive_record.sudo().write({
            'preview_url': preview_url,
            'edit_url': edit_url,
            'preview_html': (
                f'<iframe src="{preview_url}" width="100%" height="600px" '
                'frameborder="0" allowfullscreen="allowfullscreen"></iframe>'
            ),
            'edit_html': (
                f'<iframe src="{edit_url}" width="100%" height="600px" '
                'frameborder="0"></iframe>'
            ),
            'file_data_visible': False,
        })
        return True

    def _get_portal_acta_document_records(self, document_kind, main_field):
        main_record = self[main_field]
        stage_record = self.drive_stage_provedor_id
        prefix_by_kind = {
            'informes_contratista': 'Informe contratista',
            'otro_documentos_de_informacion': 'Documento adicional',
            'cesion_derechos': 'Cesion de derechos',
            'soporte_rechazo': 'Soporte rechazo',
        }
        search_domains = [
            [('id', '=', main_record.id)] if main_record else [('id', '=', 0)],
            [
                ('contratistas_provedores', '=', self.id),
                ('portal_acta_document_type', '=', document_kind),
            ],
        ]
        if stage_record:
            search_domains.append([
                ('stage4_id', '=', stage_record.id),
                ('portal_acta_document_type', '=', document_kind),
            ])
            prefix = prefix_by_kind.get(document_kind)
            if prefix:
                search_domains.append([
                    ('stage4_id', '=', stage_record.id),
                    ('file_name', 'ilike', prefix),
                ])
        return self.env['drive'].sudo().search(expression.OR(search_domains))

    @api.depends(
        'drive_stage_provedor_id',
        'informes_contratista_id',
        'otro_documentos_de_inf_id',
        'cesion_derechos_id',
        'informes_contratista_ids',
        'otro_documentos_de_inf_ids',
        'cesion_derechos_ids',
    )
    def _compute_portal_acta_document_counts(self):
        for record in self:
            record.informes_contratista_count = len(record._get_portal_acta_document_records(
                'informes_contratista',
                'informes_contratista_id',
            ))
            record.otro_documentos_de_inf_count = len(record._get_portal_acta_document_records(
                'otro_documentos_de_informacion',
                'otro_documentos_de_inf_id',
            ))
            record.cesion_derechos_count = len(record._get_portal_acta_document_records(
                'cesion_derechos',
                'cesion_derechos_id',
            ))

    def _action_open_portal_acta_document_list(self, title, document_kind, main_field):
        self.ensure_one()
        if document_kind == 'informes_contratista' and self.informes_contratista_documento:
            self._upload_informes_contratista_to_drive()
            self.invalidate_recordset([
                'informes_contratista_documento',
                'informes_contratista_id',
                'informes_contratista_ids',
            ])
        elif document_kind == 'otro_documentos_de_informacion' and self.otro_documentos_de_informacion_documento:
            self._upload_otro_documentos_de_informacion_to_drive()
            self.invalidate_recordset([
                'otro_documentos_de_informacion_documento',
                'otro_documentos_de_inf_id',
                'otro_documentos_de_inf_ids',
            ])
        drive_records = self._get_portal_acta_document_records(document_kind, main_field)
        for drive_record in drive_records:
            self._ensure_drive_document_preview(drive_record)
        stage_record = self.drive_stage_provedor_id
        google_folder_id = stage_record.folder_id if stage_record and stage_record.folder_id else False
        domain = [
            ('contratistas_provedores', '=', self.id),
            ('portal_acta_document_type', '=', document_kind),
        ]
        if stage_record:
            domain = expression.OR([
                domain,
                [
                    ('stage4_id', '=', stage_record.id),
                    ('portal_acta_document_type', '=', document_kind),
                ],
                [('id', 'in', drive_records.ids)],
            ])
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'view_mode': 'kanban,tree,form',
            'res_model': 'drive',
            'target': 'current',
            'domain': domain,
            'context': {
                'default_stage4_id': stage_record.id if stage_record else False,
                'default_folder_id': google_folder_id,
                'default_contratistas_provedores': self.id,
                'default_portal_acta_document_type': document_kind,
                'default_file_data_visible': True,
            },
            'views': [
                (self.env.ref('Matriz.drive_kanban_view').id, 'kanban'),
                (self.env.ref('Matriz.view_drive_tree').id, 'tree'),
                (False, 'form'),
            ],
        }

    def action_open_informes_contratista_drive_list(self):
        return self._action_open_portal_acta_document_list(
            'Informes del contratista',
            'informes_contratista',
            'informes_contratista_id',
        )

    def action_open_otro_documentos_de_informacion_drive_list(self):
        return self._action_open_portal_acta_document_list(
            'Documentos adicionales',
            'otro_documentos_de_informacion',
            'otro_documentos_de_inf_id',
        )

    def action_open_cesion_derechos_drive_list(self):
        return self._action_open_portal_acta_document_list(
            'Cesión de derechos',
            'cesion_derechos',
            'cesion_derechos_id',
        )

    def action_open_soporte_rechazo_drive_list(self):
        return self._action_open_portal_acta_document_list(
            'Nota crédito / soportes de rechazo',
            'soporte_rechazo',
            'soporte_rechazo_id',
        )

    def action_open_cesion_derechos_drive(self):
        self.ensure_one()
        if not self.cesion_derechos_id:
            return self.action_open_cesion_derechos_drive_list()
        self._ensure_drive_document_preview(self.cesion_derechos_id)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cesión de derechos',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cesion_derechos_id.id,
            'target': 'new',
        }

    def _upload_informes_contratista_to_drive(self):
        """Guarda el informe opcional del contratista en la carpeta de la cuenta."""
        self.ensure_one()
        if not self.informes_contratista_documento:
            return False

        stage_record = self.drive_stage_provedor_id
        if not stage_record or not stage_record.folder_id:
            _logger.error(
                "No se encontró la carpeta de Drive para subir los informes de %s.",
                self.name,
            )
            return False

        drive = GoogleDriveAuth.get_instance().get_drive()
        original_name = self.informes_contratista_name or 'informe_contratista.pdf'
        extension = os.path.splitext(original_name)[1] or '.pdf'
        fixed_file_name = f"Informe contratista_{uuid.uuid4()}{extension}"
        temp_file_path = None

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
                temp_file.write(base64.b64decode(self.informes_contratista_documento))
                temp_file_path = temp_file.name

            drive_file = drive.CreateFile({
                'title': fixed_file_name,
                'mimeType': 'application/octet-stream',
                'parents': [{'id': stage_record.folder_id}],
            })
            drive_file.SetContentFile(temp_file_path)
            drive_file.Upload()
            file_id = drive_file['id']
            drive_record = self.env['drive'].sudo().create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_data': False,
                'file_url': f"https://drive.google.com/file/d/{file_id}/view",
                'folder_id': stage_record.folder_id,
                'stage4_id': stage_record.id,
                'contratistas_provedores': self.id,
                'portal_acta_document_type': 'informes_contratista',
            })

            drive_record.sudo().write({
                'file_name': fixed_file_name,
                'file_data': False,
                'file_url': f"https://drive.google.com/file/d/{file_id}/view",
                'folder_id': stage_record.folder_id,
                'stage4_id': stage_record.id,
                'contratistas_provedores': self.id,
                'portal_acta_document_type': 'informes_contratista',
            })
            super(ActaEntregaDocumentos, self).write({
                'informes_contratista': True,
                'informes_contratista_visible': False,
                'informes_contratista_documento': False,
                'informes_contratista_id': drive_record.id,
            })
            self.message_post(
                body="El contratista adjuntó sus <b>informes</b>.",
                subtype_xmlid="mail.mt_note",
            )
            return True
        except Exception as error:
            _logger.error("Error al subir los informes del contratista: %s", error)
            return False
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def actualizar_correo_otro_documentos_de_informacion_en_drive(self):
        # Verificar si hay una relación con el modelo Drive 
        if self.otro_documentos_de_inf_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.otro_documentos_de_inf_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.otro_documentos_de_inf_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_otro_documentos_de_informacion_to_drive(self):
        """Sube el archivo binario a Google Drive y lo registra en el modelo drive, evitando duplicados."""
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        stage6_record = self.drive_stage_provedor_id        
        
        if not stage6_record:
            _logger.error(f"No se encontró un registro en cuenta_de_cobros para {self.name}. No se subirá el archivo.")
            return False

        carpeta_drive = stage6_record.folder_id
        if not carpeta_drive:
            _logger.error(f"No se encontró una carpeta en Drive para {self.name}. No se subirá el archivo.")
            return False

        temp_file_path = None # Inicializamos para el finally
        try:
            # 1. Procesar nombre y extensión
            full_name = self.otro_documentos_de_informacion_name or 'documento'
            base_name, extension = os.path.splitext(full_name)
            extension = extension or ".pdf"

            # 2. Construir nombre final (UUID asegura que sea único en Drive)
            fixed_file_name = f"{base_name}_{uuid.uuid4()}{extension}"

            # 3. Decodificar y crear archivo temporal
            file_content = base64.b64decode(self.otro_documentos_de_informacion_documento)
            
            # Corrección del error: usamos 'extension' en lugar de 'original_extension'
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            # 4. Crear un registro nuevo para que "Ver todos" conserve el historial
            file_drive = drive.CreateFile({
                'title': fixed_file_name,
                'parents': [{'id': carpeta_drive}],
            })
            file_drive.SetContentFile(temp_file_path)
            file_drive.Upload()
            
            file_id = file_drive.get('id')
            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            _logger.info(f"Archivo nuevo subido a Drive con ID: {file_id}")

            drive_record = self.env['drive'].sudo().create({
                'file_name': fixed_file_name,
                'file_id': file_id,
                'file_data': False,
                'file_url': file_link,
                'folder_id': carpeta_drive,
                'stage4_id': stage6_record.id,
                'contratistas_provedores': self.id,
                'portal_acta_document_type': 'otro_documentos_de_informacion',
            })

            # 5. Actualizar el registro actual (Agrupamos los write en uno solo)
            vals = {
                'otro_documentos_de_informacion_visible': False,
                'otro_documentos_de_informacion_documento': False,
                'otro_documentos_de_informacion': True,
                'otro_documentos_de_inf_id': drive_record.id,
            }
            
            super(ActaEntregaDocumentos, self).write(vals)

            # 6. Trazabilidad
            self.message_post(
                body=f"✅ El documento subido exitosamente en Google Drive.",
                subtype_xmlid="mail.mt_note"
            )

        except Exception as e:
            _logger.error(f"Error al subir archivo a Google Drive: {str(e)}")
            return False
        
        finally:
            # Borrar el archivo temporal siempre
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

        return True


    planilla_de_aportes_a_seguridad_social = fields.Boolean(string='Planilla de aportes a seguridad social')
    planilla_de_aportes_a_seguridad_social_documento = fields.Binary(string='Planilla de aportes a seguridad social')
    planilla_de_aportes_a_seguridad_social_name = fields.Char(string='Planilla de aportes a seguridad social')
    planilla_de_aportes_a_seguridad_social_visible = fields.Boolean(string='Planilla de aportes a seguridad social Visible', default=True)
    planilla_de_aportes_a_seguridad_social_id = fields.Many2one('drive', string='Planilla de aportes a seguridad social')

    planilla_de_aportes_a_seguridad_social_corregir = fields.Boolean(default=False, related='planilla_de_aportes_a_seguridad_social_id.actualizar_visible', readonly=False)

    def action_open_planilla_de_aportes_a_seguridad_social_drive(self):
        self.ensure_one()
        self.actualizar_correo_planilla_de_aportes_a_seguridad_social_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planilla de Aportes a Seguridad Social',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.planilla_de_aportes_a_seguridad_social_id.id,
            'target': 'new',
        }

    def actualizar_correo_planilla_de_aportes_a_seguridad_social_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.planilla_de_aportes_a_seguridad_social_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.planilla_de_aportes_a_seguridad_social_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.planilla_de_aportes_a_seguridad_social_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_planilla_de_aportes_a_seguridad_social_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'planilla_de_aportes_a_seguridad_social_documento',
            'planilla_de_aportes_a_seguridad_social_name',
            'planilla_de_aportes_a_seguridad_social_id',
            'planilla_de_aportes_a_seguridad_social_visible',
            'planilla_de_aportes_a_seguridad_social',
            '7_Planilla de Aportes a Seguridad Social',
            'Planilla de aportes a seguridad social',
            False,
        )

    certificado_de_expensa = fields.Boolean(string='Certificado de expensas (si aplica)')
    certificado_de_expensa_documento = fields.Binary(string='Certificado de expensas (si aplica)')
    certificado_de_expensa_name = fields.Char(string='Certificado de expensas (si aplica)')
    certificado_de_expensa_visible = fields.Boolean(string='Certificado de expensas Visible', default=True)
    certificado_de_expensa_id = fields.Many2one('drive', string='Certificado de expensas (si aplica)')

    certificado_de_expensa_corregir = fields.Boolean(default=False, related='certificado_de_expensa_id.actualizar_visible', readonly=False)

    def action_open_certificado_de_expensa_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado de Expensas',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_de_expensa_id.id,
            'target': 'new',
        }

    def _upload_certificado_de_expensa_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificado_de_expensa_documento',
            'certificado_de_expensa_name',
            'certificado_de_expensa_id',
            'certificado_de_expensa_visible',
            'certificado_de_expensa',
            '8_Certificado de Expensas',
            'Certificado de expensas',
            False,
        )

    # Primera cuenta
    copia_contrato = fields.Boolean(string='Copia contrato')
    copia_contrato_documento = fields.Binary(string='Copia contrato')
    copia_contrato_name = fields.Char(string='Copia contrato')
    copia_contrato_visible = fields.Boolean(string='Copia contrato Visible', default=True, related='relacion_fuv_id.contratos_firmado_doc_visible') 
    copia_contrato_id = fields.Many2one('drive', string='Copia contrato', compute='_compute_copia_contrato_id', domain="[('stage4_id', '=', drive_stage_provedor_id)]")  
    copia_contrato_2_id = fields.Many2one('drive', string='Copia contrato', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    copia_contrato_corregir = fields.Boolean(default=False, related='copia_contrato_2_id.actualizar_visible', readonly=False)

    
    def actualizar_correo_copia_contrato_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.copia_contrato_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.copia_contrato_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.copia_contrato_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False


    # Convertimos este booleano en un "Espejo" automático
    contrato_copiado = fields.Boolean(
        string='¿Contrato copiado?', 
        compute='_compute_contrato_copiado_status', 
    )

    @api.depends('copia_contrato_2_id')
    def _compute_contrato_copiado_status(self):
        for record in self:
            # bool() convierte: Si tiene ID es True, si es False/None es False
            record.contrato_copiado = bool(record.copia_contrato_2_id)
    

    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_copia_contrato_id(self):
        for record in self:
            new_value = False

            # --- Asignar el documento según el tipo seleccionado ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.contratos_firmado_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.contratos_firmado_id

            record.copia_contrato_id = new_value

    # -----------------------------------------------------------
    # BOTÓN: Copiar Copia de Contrato (Natural) al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_contrato_a_drive_proveedor(self):
        """Crea una copia del archivo 'Copia Contrato' dentro de la carpeta del proveedor.
        Si ya existe la copia, no la vuelve a crear."""
        for record in self:
            if not record.copia_contrato_id:
                raise UserError("No hay archivo de Copia de Contrato seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.copia_contrato_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión con Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia de Contrato creada correctamente en Google Drive con ID: {new_file_id}")

                record.copia_contrato_2_id = new_drive_record.id
                record.copia_contrato = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>Copia del Contrato</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo de Contrato en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar la Copia del Contrato en Google Drive:\n{e}")


    def action_open_copia_contrato_drive(self):
        self.ensure_one()
        self.actualizar_correo_copia_contrato_en_drive()

        drive_record = self.copia_contrato_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Ocultar el file_data al abrir
                })
            else:
                # Si ya existe vista previa, igual ocultamos el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (como antes)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Copia Contrato',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def _upload_copia_contrato_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'copia_contrato_documento',
            'copia_contrato_name',
            'copia_contrato_2_id',
            'copia_contrato_visible',
            'copia_contrato',
            '9_Copia Contrato',
            'Copia contrato',
            False,
        )

    copia_cedula = fields.Boolean(string='Copia cédula')
    copia_cedula_documento = fields.Binary(string='Copia cédula')
    copia_cedula_name = fields.Char(string='Copia cédula')

    copia_cedula_visible = fields.Boolean(string='Copia cédula Visible', default=True, related='relacion_fuv_id.documento_identidad_pn_doc_visible')
    copia_cedula_id = fields.Many2one('drive', string='Copia cédula', compute='_compute_copia_cedula', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    copia_cedula_2_id = fields.Many2one('drive', string='Copia cédula', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    regalias_copia_cedula_documento = fields.Binary(string='Cédula Regalías')
    regalias_copia_cedula_name = fields.Char(string='Cédula Regalías')
    regalias_copia_cedula_id = fields.Many2one('drive', string='Cédula Regalías', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    regalias_copia_cedula_corregir = fields.Boolean(default=False, related='regalias_copia_cedula_id.actualizar_visible', readonly=False)

    regalias_copia_contrato_documento = fields.Binary(string='Contrato Regalías')
    regalias_copia_contrato_name = fields.Char(string='Contrato Regalías')
    regalias_copia_contrato_id = fields.Many2one('drive', string='Contrato Regalías', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    regalias_copia_contrato_corregir = fields.Boolean(default=False, related='regalias_copia_contrato_id.actualizar_visible', readonly=False)

    def _upload_regalias_documento_to_drive(self, binary_field, name_field, target_field, label, prefix):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        for record in self:
            stage_record = record.drive_stage_provedor_id
            file_content_b64 = getattr(record, binary_field, False)
            if not stage_record or not stage_record.folder_id or not file_content_b64:
                continue

            try:
                original_name = getattr(record, name_field, False) or (label + '.bin')
                original_extension = os.path.splitext(original_name)[-1] or ".bin"
                fixed_file_name = f"{prefix}_{label}_{uuid.uuid4()}{original_extension}"
                file_content = base64.b64decode(file_content_b64)

                existing_drive_record = self.env['drive'].sudo().search([
                    ('folder_id', '=', stage_record.folder_id),
                    ('file_name', 'like', f"{prefix}_{label}_%"),
                    ('stage4_id', '=', stage_record.id),
                ], limit=1)

                with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                    temp_file.write(file_content)
                    temp_file_path = temp_file.name

                if existing_drive_record:
                    file_drive = drive.CreateFile({'id': existing_drive_record.file_id})
                    file_drive.SetContentFile(temp_file_path)
                    file_drive.Upload()
                    file_id = existing_drive_record.file_id
                    file_link = f"https://drive.google.com/file/d/{file_id}/view"
                    preview_url = f"https://drive.google.com/file/d/{file_id}/preview"
                    edit_url = f"https://drive.google.com/file/d/{file_id}/edit"
                    existing_drive_record.write({
                        'name': fixed_file_name,
                        'file_name': fixed_file_name,
                        'file_data': False,
                        'file_url': file_link,
                        'preview_url': preview_url,
                        'edit_url': edit_url,
                        'preview_html': f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>',
                        'edit_html': f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>',
                    })
                    drive_record = existing_drive_record
                else:
                    file_drive = drive.CreateFile({
                        'title': fixed_file_name,
                        'mimeType': 'application/octet-stream',
                        'parents': [{'id': stage_record.folder_id}],
                    })
                    file_drive.SetContentFile(temp_file_path)
                    file_drive.Upload()
                    file_id = file_drive['id']
                    file_link = f"https://drive.google.com/file/d/{file_id}/view"
                    preview_url = f"https://drive.google.com/file/d/{file_id}/preview"
                    edit_url = f"https://drive.google.com/file/d/{file_id}/edit"
                    drive_record = self.env['drive'].sudo().create({
                        'name': fixed_file_name,
                        'file_name': fixed_file_name,
                        'file_id': file_id,
                        'file_url': file_link,
                        'preview_url': preview_url,
                        'edit_url': edit_url,
                        'preview_html': f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>',
                        'edit_html': f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>',
                        'file_data': False,
                        'folder_id': stage_record.folder_id,
                        'stage4_id': stage_record.id,
                    })

                os.unlink(temp_file_path)
                record.write({
                    target_field: drive_record.id,
                    binary_field: False,
                })
                record.message_post(
                    body=f"✅ El documento <b>{label}</b> de Regalías fue subido exitosamente a Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )
            except Exception as e:
                _logger.error(f"Error al subir {label} de Regalías a Google Drive: {e}")
                return False
        return True

    def _upload_regalias_copia_cedula_to_drive(self):
        return self._upload_regalias_documento_to_drive(
            'regalias_copia_cedula_documento',
            'regalias_copia_cedula_name',
            'regalias_copia_cedula_id',
            'Cédula',
            '10R',
        )

    def _upload_regalias_copia_contrato_to_drive(self):
        return self._upload_regalias_documento_to_drive(
            'regalias_copia_contrato_documento',
            'regalias_copia_contrato_name',
            'regalias_copia_contrato_id',
            'Contrato',
            '13R',
        )

    def _action_open_regalias_drive(self, field_name, title):
        self.ensure_one()
        drive_record = getattr(self, field_name, False)
        if drive_record:
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"
                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>',
                    'edit_html': f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>',
                    'file_data_visible': False,
                })
            else:
                drive_record.file_data_visible = False
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }

    def action_open_regalias_copia_cedula_drive(self):
        return self._action_open_regalias_drive('regalias_copia_cedula_id', 'Cédula Regalías')

    def action_open_regalias_copia_contrato_drive(self):
        return self._action_open_regalias_drive('regalias_copia_contrato_id', 'Contrato Regalías')

    # Convertimos el booleano en automático
    cedula_copiada = fields.Boolean(
        string='¿Cédula copiada?', 
        compute='_compute_cedula_copiada_status', 
    )

    @api.depends('copia_cedula_2_id')
    def _compute_cedula_copiada_status(self):
        for record in self:
            record.cedula_copiada = bool(record.copia_cedula_2_id)


    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_copia_cedula(self):
        for record in self:
            new_value = False

            # --- Asignar el documento según el tipo seleccionado ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.documento_identidad_id
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                new_value = record.relacion_orden_compra_bienes_fuv_id.documento_identidad_doc_id
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                new_value = record.relacion_orden_compra_servicios_fuv_id.documento_identidad_doc_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.documento_identidad_id

            record.copia_cedula_id = new_value



    
    # -----------------------------------------------------------
    # BOTÓN: Copiar Copia de Cédula al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_cedula_a_drive_proveedor(self):
        """Crea una copia del archivo 'Copia Cédula' dentro de la carpeta del proveedor.
        Si ya existe la copia, no la vuelve a crear."""
        for record in self:
            if not record.copia_cedula_id:
                raise UserError("No hay archivo de Copia de Cédula seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.copia_cedula_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión con Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia en la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia de cédula creada correctamente en Google Drive con ID: {new_file_id}")
                record.copia_cedula_2_id = new_drive_record.id
                record.copia_cedula = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>copia de la Cédula</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo de cédula en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar la Copia de Cédula en Google Drive:\n{e}")




    copia_cedula_corregir = fields.Boolean(default=False, tracking=True, related='copia_cedula_2_id.actualizar_visible', readonly=False)


    def action_open_copia_cedula_drive(self):
        self.ensure_one()
        self.actualizar_correo_copia_cedula_en_drive()


        # 📨 Mantiene tu lógica original
        self.actualizar_correo_copia_cedula_en_drive()

        drive_record = self.copia_cedula_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Ocultar file_data al abrir
                })
            else:
                # Si ya tiene los HTML, igual se asegura de ocultar el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (como antes)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Copia Cédula',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def actualizar_correo_copia_cedula_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.copia_cedula_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.copia_cedula_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.copia_cedula_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_copia_cedula_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'copia_cedula_documento',
            'copia_cedula_name',
            'copia_cedula_2_id',
            'copia_cedula_visible',
            'copia_cedula',
            '10_Copia Cédula',
            'Copia cédula',
            False,
        )

    certificacion_bancaria = fields.Boolean(string='Certificación Bancaria (vigente)')
    certificacion_bancaria_documento = fields.Binary(string='Certificación Bancaria (vigente)')
    certificacion_bancaria_name = fields.Char(string='Certificación Bancaria (vigente)')
    certificacion_bancaria_visible = fields.Boolean(string='Certificación Bancaria Visible', default=True, related='relacion_fuv_id.certificacion_bancaria_doc_visible')
    certificacion_bancaria_id = fields.Many2one('drive', string='Certificación Bancaria (vigente)', compute='_compute_certificacion_bancaria', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    certificacion_bancaria_2_id = fields.Many2one('drive', string='Certificación Bancaria (vigente)', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
     
    # Convertimos el booleano en automático
    certificacion_bancaria_copiada = fields.Boolean(
        string='¿Certificación Bancaria copiada?', 
        compute='_compute_certificacion_bancaria_status', 
    )

    @api.depends('certificacion_bancaria_2_id')
    def _compute_certificacion_bancaria_status(self):
        for record in self:
            record.certificacion_bancaria_copiada = bool(record.certificacion_bancaria_2_id)

    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_certificacion_bancaria(self):
        for record in self:
            record.certificacion_bancaria_id = False

    # -----------------------------------------------------------
    # BOTÓN: Copiar Certificación Bancaria (vigente) al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_certificacion_bancaria_a_drive_proveedor(self):
        """Crea una copia del archivo 'Certificación Bancaria (vigente)' en la carpeta del proveedor.
        Si ya existe la copia, no la vuelve a crear."""
        for record in self:
            if not record.certificacion_bancaria_id:
                raise UserError("No hay archivo de Certificación Bancaria (vigente) seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.certificacion_bancaria_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión a Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia de Certificación Bancaria creada correctamente en Google Drive con ID: {new_file_id}")
                record.certificacion_bancaria_2_id = new_drive_record.id
                record.certificacion_bancaria = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>Copia de la Certificación Bancaria (vigente)</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo de Certificación Bancaria en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar la Certificación Bancaria (vigente) en Google Drive:\n{e}")




    certificacion_bancaria_corregir = fields.Boolean(default=False, tracking=True, related='certificacion_bancaria_2_id.actualizar_visible', readonly=False)


    def action_open_certificacion_bancaria_drive(self):
        self.ensure_one()
        self.actualizar_correo_certificacion_bancaria_en_drive()


        # 📨 Mantiene tu lógica original
        self.actualizar_correo_certificacion_bancaria_en_drive()

        drive_record = self.certificacion_bancaria_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Ocultar el contenido crudo del archivo
                })
            else:
                # Si ya tiene los HTML, igualmente oculta el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (igual que tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación Bancaria',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def actualizar_correo_certificacion_bancaria_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.certificacion_bancaria_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificacion_bancaria_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.certificacion_bancaria_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_certificacion_bancaria_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificacion_bancaria_documento',
            'certificacion_bancaria_name',
            'certificacion_bancaria_2_id',
            False,
            'certificacion_bancaria',
            '11_Certificación Bancaria',
            'Certificación bancaria',
            False,
        )

    # Una sola vez
    rpc_regalias = fields.Boolean(string='RPC de Regalías', tracking=True)
    rpc_regalias_documento = fields.Binary(string='RPC de Regalías')
    rpc_regalias_name = fields.Char(string='Nombre del RPC de Regalías')
    rpc_regalias_visible = fields.Boolean(string='RPC de Regalías visible', default=True)
    rpc_regalias_id = fields.Many2one(
        'drive',
        string='RPC de Regalías',
        tracking=True,
        readonly=True,
        domain="[('stage4_id', '=', drive_stage_provedor_id)]",
    )
    rpc_regalias_corregir = fields.Boolean(
        string='Corregir RPC de Regalías',
        related='rpc_regalias_id.actualizar_visible',
        readonly=False,
    )

    def _upload_rpc_regalias_to_drive(self):
        """Sube el RPC que quedó pendiente únicamente al guardar el acta."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_contratacion_asis_coordinador'):
            raise AccessError(_(
                'Solo el Asistente de Coordinación puede subir el RPC de Regalías.'
            ))
        if self.origen_cuenta != 'regalias':
            raise AccessError(_(
                'El RPC manual solo está disponible para cuentas de Regalías.'
            ))
        return self._upload_documento_persona_robusto_to_drive(
            'rpc_regalias_documento',
            'rpc_regalias_name',
            'rpc_regalias_id',
            'rpc_regalias_visible',
            'rpc_regalias',
            'RPC_Regalias',
            'RPC de Regalías',
            False,
        )

    registro_de_proveedores = fields.Boolean(string='RPC')
    registro_de_proveedores_documento = fields.Binary(string='RPC')
    registro_de_proveedores_name = fields.Char(string='RPC')
    registro_de_proveedores_visible = fields.Boolean(string='RPC Visible', default=True, related='relacion_fuv_id.su_cdp_doc_visible')
    registro_de_proveedores_id = fields.Many2one('drive', string='RPC', compute='_compute_registro_proveedores', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    registro_de_proveedores_2_id = fields.Many2one('drive', string='RPC', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    

    registro_de_proveedores_corregir = fields.Boolean(default=False, tracking=True, related='registro_de_proveedores_2_id.actualizar_visible', readonly=False)



    def actualizar_correo_registro_de_proveedores_en_drive(self):
        # Verificar si hay una relación con el modelo Drive
        if self.registro_de_proveedores_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.registro_de_proveedores_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.registro_de_proveedores_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    
    # Convertimos el booleano en automático
    rpc_copiado = fields.Boolean(
        string='¿RPC copiado?', 
        compute='_compute_rpc_copiado_status', 
    )

    @api.depends('registro_de_proveedores_2_id')
    def _compute_rpc_copiado_status(self):
        for record in self:
            record.rpc_copiado = bool(record.registro_de_proveedores_2_id)

    # -----------------------------------------------------------
    # BOTÓN: Copiar RPC (Registro de Proveedores) al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_rpc_a_drive_proveedor(self):
        """Crea una copia del archivo RPC (registro_de_proveedores_id) en la carpeta del proveedor.
        Si ya existe la copia, no la vuelve a crear."""
        for record in self:
            if not record.registro_de_proveedores_id:
                raise UserError("No hay archivo RPC (Registro de Proveedores) seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.registro_de_proveedores_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión a Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    record.registro_de_proveedores_2_id = existing_copy.id
                    record.registro_de_proveedores = True
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia de archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia en la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia RPC creada correctamente en Google Drive con ID: {new_file_id}")
                record.registro_de_proveedores_2_id = new_drive_record.id
                record.registro_de_proveedores = True

                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>copia del Registro de Proveedores (RPC)</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo RPC en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar el archivo RPC en Google Drive:\n{e}")


    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_registro_proveedores(self):
        for record in self:
            new_value = False

            # --- Determinar el documento RPC según la selección ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.su_cdp_id
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                new_value = record.relacion_orden_compra_bienes_fuv_id.documento_rpc_doc_id
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                new_value = record.relacion_orden_compra_servicios_fuv_id.documento_rpc_doc_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.su_cdp_id

            record.registro_de_proveedores_id = new_value



    def action_open_registro_de_proveedores_drive(self):
        self.ensure_one()
        self.actualizar_correo_registro_de_proveedores_en_drive()

        drive_record = self.registro_de_proveedores_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Ocultar contenido del archivo
                })
            else:
                # Si ya tiene vista previa, igual se oculta el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal como antes
        return {
            'type': 'ir.actions.act_window',
            'name': 'RPC',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def _upload_registro_de_proveedores_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'registro_de_proveedores_documento',
            'registro_de_proveedores_name',
            'registro_de_proveedores_2_id',
            'registro_de_proveedores_visible',
            'registro_de_proveedores',
            '12_RPC',
            'RPC',
            False,
        )

    ########################################## Documentación anexa persona juridica################################################

    planilla_de_seguridad_social_ju = fields.Boolean(string='Planilla de aportes a seguridad social')
    planilla_de_aportes_a_seguridad_social_ju_documento = fields.Binary(string='Planilla de aportes a seguridad social')
    planilla_de_aportes_a_seguridad_social_ju_name = fields.Char(string='Planilla de aportes a seguridad social')
    planilla_de_aportes_a_seguridad_social_ju_visible = fields.Boolean(string='Planilla de aportes a seguridad social', default=True)
    planilla_de_aportes_a_seguridad_social_ju_id = fields.Many2one('drive', string='Planilla de aportes a seguridad social')
    planilla_de_aportes_a_seguridad_social_ju_corregir = fields.Boolean(default=False, related='planilla_de_aportes_a_seguridad_social_ju_id.actualizar_visible', readonly=False)

    def action_open_planilla_de_aportes_a_seguridad_social_ju_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planilla de aportes a seguridad social',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.planilla_de_aportes_a_seguridad_social_ju_id.id,
            'target': 'new',
        }

    
    def _upload_planilla_de_aportes_a_seguridad_social_ju_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'planilla_de_aportes_a_seguridad_social_ju_documento',
            'planilla_de_aportes_a_seguridad_social_ju_name',
            'planilla_de_aportes_a_seguridad_social_ju_id',
            'planilla_de_aportes_a_seguridad_social_ju_visible',
            'planilla_de_seguridad_social_ju',
            'Planilla de aportes a seguridad social',
            'Planilla de aportes a seguridad social',
            False,
        )

    factura_electronica_juridica_doc = fields.Binary(string='Factura electrónica')
    factura_electronica_juridica_doc_name = fields.Char(string='Factura electrónica')
    factura_electronica_juridica = fields.Boolean(string='Factura electrónica')
    factura_electronica_juridica_visible = fields.Boolean(string='Factura electrónica Visible', default=True)
    factura_electronica_juridica_id = fields.Many2one('drive', string='Factura electrónica')

    factura_electronica_juridica_corregir = fields.Boolean(default=False, related='factura_electronica_juridica_id.actualizar_visible', readonly=False)

    def action_open_factura_electronica_drive(self):
        self.ensure_one()
        self.actualizar_correo_factura_electronica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Factura Electrónica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.factura_electronica_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_factura_electronica_en_drive(self):
        _logger.info("Actualizando correo electrónico en el registro de Google Drive asociado a la factura electrónica.")
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la factura electrónica."""
        # Verificar si hay una relación con el modelo Drive
        if self.factura_electronica_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.factura_electronica_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.factura_electronica_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_factura_electronica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'factura_electronica_juridica_doc',
            'factura_electronica_juridica_doc_name',
            'factura_electronica_juridica_id',
            'factura_electronica_juridica_visible',
            'factura_electronica_juridica',
            '1_Factura Electrónica',
            'Factura electrónica',
            False,
        )

    relcion_cer_conjunto_ids = fields.One2many('certificacion_cumplimiento', 'relcion', string='Relacion')
    certificacion_cumplimiento_creada = fields.Boolean(
        string='Certificación de cumplimiento creada',
        compute='_compute_certificacion_cumplimiento_firmas'
    )
    certificacion_cumplimiento_firmada_coordinador = fields.Boolean(
        string='Certificación firmada por coordinador',
        compute='_compute_certificacion_cumplimiento_firmas'
    )
    certificacion_cumplimiento_firmada_ventanilla = fields.Boolean(
        string='Certificación firmada por ventanilla',
        compute='_compute_certificacion_cumplimiento_firmas'
    )
    certificacion_cumplimiento_firmada_financiera = fields.Boolean(
        string='Certificación firmada por financiera',
        compute='_compute_certificacion_cumplimiento_firmas'
    )

    @api.depends(
        'relcion_cer_conjunto_ids',
        'relcion_cer_conjunto_ids.formulario_guardado',
        'relcion_cer_conjunto_ids.informe_adjunto',
        'relcion_cer_conjunto_ids.actividades_especificas',
        'relcion_cer_conjunto_ids.porcentaje_objetivos',
        'relcion_cer_conjunto_ids.firmado',
        'relcion_cer_conjunto_ids.firmado_ventanilla',
        'relcion_cer_conjunto_ids.firmado_finaciera',
    )
    def _compute_certificacion_cumplimiento_firmas(self):
        for record in self:
            certificacion = record.relcion_cer_conjunto_ids[:1]
            record.certificacion_cumplimiento_creada = bool(
                certificacion and (
                    certificacion.formulario_guardado
                    or certificacion.informe_adjunto
                    or certificacion.firmado
                    or certificacion.firmado_ventanilla
                    or certificacion.firmado_finaciera
                )
            )
            record.certificacion_cumplimiento_firmada_coordinador = bool(certificacion and certificacion.firmado)
            record.certificacion_cumplimiento_firmada_ventanilla = bool(certificacion and certificacion.firmado_ventanilla)
            record.certificacion_cumplimiento_firmada_financiera = bool(certificacion and certificacion.firmado_finaciera)
            record.certificacion_cumplimiento_datos_basicos_ok = bool(
                certificacion
                and (certificacion.actividades_especificas or '').strip()
                and certificacion.porcentaje_objetivos
            )

    certificacion_cumplimiento_datos_basicos_ok = fields.Boolean(
        string='Certificación conjunta con datos básicos',
        compute='_compute_certificacion_cumplimiento_firmas'
    )
    pendiente_firma_coordinador = fields.Boolean(
        string='Pendiente firma Coordinador',
        compute='_compute_pendiente_firma_coordinador',
        store=True,
    )

    @api.depends(
        'estado',
        'clase_persona',
        'informe_vobo_coordinador',
        'relcion_cer_conjunto_ids',
        'relcion_cer_conjunto_ids.firmado',
    )
    def _compute_pendiente_firma_coordinador(self):
        for record in self:
            certificacion = record.relcion_cer_conjunto_ids[:1]
            falta_certificacion = not (certificacion and certificacion.firmado)
            falta_vobo = (
                record.clase_persona == 'persona_natural'
                and not record.informe_vobo_coordinador
            )
            record.pendiente_firma_coordinador = (
                record.estado == 'coordinador'
                and (falta_certificacion or falta_vobo)
            )

    pendiente_firma_director_financiero = fields.Boolean(
        string='Pendiente firma Director Financiero',
        compute='_compute_pendiente_firma_director_financiero',
        store=True,
    )

    @api.depends(
        'estado',
        'relcion_cer_conjunto_ids',
        'relcion_cer_conjunto_ids.firmado_finaciera',
    )
    def _compute_pendiente_firma_director_financiero(self):
        for record in self:
            certificacion = record.relcion_cer_conjunto_ids[:1]
            record.pendiente_firma_director_financiero = (
                record.estado == 'director_financiero'
                and not (certificacion and certificacion.firmado_finaciera)
            )

    certificacion_conjunta_juridica = fields.Boolean(string='Certificación conjunta', tracking=True)
    certificacion_conjunta_juridica_documento = fields.Binary(string='Certificación conjunta', related='relcion_cer_conjunto_ids.informe_adjunto')
    certificacion_conjunta_juridica_name = fields.Char(string='Certificación conjunta', tracking=True, related='relcion_cer_conjunto_ids.informe_filename')
    certificacion_conjunta_juridica_visible = fields.Boolean(string='Certificación conjunta Visible', default=True)
    certificacion_conjunta_juridica_id = fields.Many2one('drive', string='Certificación conjunta')

    def action_open_certificacion_conjunta_juridica_drive(self):
        self.ensure_one()
        self._refresh_certificacion_conjunta_drive_from_template()
        if self.certificacion_conjunta_juridica_id:
            drive_record = self.certificacion_conjunta_juridica_id.sudo()
            drive_record.with_context(
                skip_drive_sync=True,
                skip_integrity=True,
            ).write({'file_data_visible': False})
            drive_record.asegurar_visor_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación conjunta',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificacion_conjunta_juridica_id.id if self.certificacion_conjunta_juridica_id else False,
            'target': 'new',
        }
        
    def _upload_certificacion_conjunta_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificacion_conjunta_juridica_documento',
            'certificacion_conjunta_juridica_name',
            'certificacion_conjunta_juridica_id',
            'certificacion_conjunta_juridica_visible',
            'certificacion_conjunta_juridica',
            '2_Certificación conjunta',
            'Certificación conjunta',
            'silent_certificacion_conjunta_auto',
        )

    certificacion_conjunta_firmado_juridica = fields.Boolean(string='Certificación conjunta firmada', tracking=True)
    certificacion_conjunta_firmado_juridica_documento = fields.Binary(string='Certificación conjunta firmada')
    certificacion_conjunta_firmado_juridica_name = fields.Char(string='Certificación conjunta firmada')
    certificacion_conjunta_firmado_juridica_visible = fields.Boolean(string='Certificación conjunta firmada Visible', default=True)
    certificacion_conjunta_firmado_juridica_id = fields.Many2one('drive', string='Certificación conjunta firmada')

    def action_open_certificacion_conjunta_firmado_juridica_drive(self):
        self.ensure_one()
        if self.certificacion_conjunta_firmado_juridica_id:
            drive_record = self.certificacion_conjunta_firmado_juridica_id.sudo()
            drive_record.with_context(
                skip_drive_sync=True,
                skip_integrity=True,
            ).write({'file_data_visible': False})
            drive_record.asegurar_visor_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación conjunta firmada',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificacion_conjunta_firmado_juridica_id.id,
            'target': 'new',
        }
        
    def _upload_certificacion_conjunta_firmado_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificacion_conjunta_firmado_juridica_documento',
            'certificacion_conjunta_firmado_juridica_name',
            'certificacion_conjunta_firmado_juridica_id',
            'certificacion_conjunta_firmado_juridica_visible',
            False,
            '2_Certificación conjunta firmada',
            'Certificación conjunta firmada',
            False,
        )

    registro_proveedores_juridica = fields.Boolean(string='',)
    registro_proveedores_juridica_documento = fields.Binary(string='RPC')
    registro_proveedores_juridica_name = fields.Char(string='RPC')
    registro_proveedores_juridica_visible = fields.Boolean(string='RPC', default=True, related='relacion_fuv_id.cdp_juridico_doc_visible')
    registro_proveedores_juridica_id = fields.Many2one('drive', string='RPC',compute='_compute_registro_proveedores_juridica', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    registro_proveedores_juridica_2_id = fields.Many2one('drive', string='RPC', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    # Campo auxiliar para evitar ejecuciones múltiples
    rpc_juridico_copiado = fields.Boolean(string='¿RPC Jurídico copiado?', compute='_compute_rpc_juridico_copiado_status')
    registro_proveedores_juridica_corregir = fields.Boolean(default=False, related='registro_proveedores_juridica_2_id.actualizar_visible', readonly=False)

    def actualizar_registro_proveedores_juridica_en_drive(self):
        _logger.info("Actualizando correo electrónico en el registro de Google Drive asociado a la factura electrónica.")
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la factura electrónica."""
        # Verificar si hay una relación con el modelo Drive
        if self.registro_proveedores_juridica_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.registro_proveedores_juridica_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.registro_proveedores_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    @api.depends('registro_proveedores_juridica_2_id')
    def _compute_rpc_juridico_copiado_status(self):
        for record in self:
            # Lógica simple: Si tiene ID es True, si está vacío es False.
            if record.registro_proveedores_juridica_2_id:
                record.rpc_juridico_copiado = True
            else:
                record.rpc_juridico_copiado = False

    
    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_registro_proveedores_juridica(self):
        for record in self:
            new_value = False

            # --- Asignar valor según tipo de documento ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.cdp_juridico_doc_id
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                new_value = record.relacion_orden_compra_bienes_fuv_id.documento_rpc_pj_doc_id
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                new_value = record.relacion_orden_compra_servicios_fuv_id.documento_rpc_pj_doc_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.cdp_juridico_doc_id

            record.registro_proveedores_juridica_id = new_value



    # -----------------------------------------------------------
    # BOTÓN: Copiar RPC Jurídica (registro_proveedores_juridica_id) al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_rpc_juridica_a_drive_proveedor(self):
        """Crea una copia del archivo RPC Jurídica (registro_proveedores_juridica_id)
        en la carpeta del proveedor. Si ya existe la copia, no la vuelve a crear."""
        for record in self:
            if not record.registro_proveedores_juridica_id:
                raise UserError("No hay archivo RPC Jurídica seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.registro_proveedores_juridica_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión con Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    record.registro_proveedores_juridica_2_id = existing_copy.id
                    record.registro_proveedores_juridica = True
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia en la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia RPC Jurídica creada correctamente en Google Drive con ID: {new_file_id}")

                record.registro_proveedores_juridica_2_id = new_drive_record.id
                record.registro_proveedores_juridica = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>copia del RPC Jurídica</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo RPC Jurídica en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar el archivo RPC Jurídica en Google Drive:\n{e}")

    


    def action_open_registro_proveedores_juridica_drive(self):
        self.ensure_one()
        self.actualizar_registro_proveedores_juridica_en_drive()

        drive_record = self.registro_proveedores_juridica_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Oculta el campo binario al abrir
                })
            else:
                # Si ya existen los HTML, igual se oculta el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (igual que tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'RPC',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def _upload_registro_proveedores_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'registro_proveedores_juridica_documento',
            'registro_proveedores_juridica_name',
            'registro_proveedores_juridica_2_id',
            'registro_proveedores_juridica_visible',
            'registro_proveedores_juridica',
            '3_RPC',
            'RPC',
            False,
        )

    rut_actualizado_juridica = fields.Boolean(string='RUT (Actualizado)')
    rut_actualizado_juridica_documento = fields.Binary(string='RUT (Actualizado)')
    rut_actualizado_juridica_name = fields.Char(string='RUT (Actualizado)')
    rut_actualizado_juridica_visible = fields.Boolean(string='RUT (Actualizado) Visible', default=True)
    rut_actualizado_juridica_id = fields.Many2one('drive', string='RUT (Actualizado)')

    rut_actualizado_juridica_corregir = fields.Boolean(default=False, related='rut_actualizado_juridica_id.actualizar_visible', readonly=False)
    

    def action_open_rut_actualizado_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_rut_actualizado_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT (Actualizado)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_actualizado_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_rut_actualizado_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado al RUT actualizado."""
        # Verificar si hay una relación con el modelo Drive
        if self.rut_actualizado_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.rut_actualizado_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.rut_actualizado_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_rut_actualizado_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'rut_actualizado_juridica_documento',
            'rut_actualizado_juridica_name',
            'rut_actualizado_juridica_id',
            'rut_actualizado_juridica_visible',
            'rut_actualizado_juridica',
            '4_RUT Actualizado',
            'RUT actualizado',
            False,
        )

    resolucion_facturacion_electronica_juridica = fields.Boolean(string='Resolución de Facturación Electrónica')
    resolucion_facturacion_electronica_juridica_documento = fields.Binary(string='Resolución de Facturación Electrónica')
    resolucion_facturacion_electronica_juridica_name = fields.Char(string='Resolución de Facturación Electrónica')
    resolucion_facturacion_electronica_juridica_visible = fields.Boolean(string='Resolución de Facturación Electrónica Visible', default=True, related='relacion_fuv_id.resolucion_facturacion_doc_visible')
    resolucion_facturacion_electronica_juridica_id = fields.Many2one('drive', string='Resolución de Facturación Electrónica', compute='_compute_resolucion_facturacion_electronica_juridica', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    resolucion_facturacion_electronica_juridica_2_id = fields.Many2one('drive', string='Resolución de Facturación Electrónica', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    resolucion_facturacion_electronica_juridica_actual_id = fields.Many2one(
        'drive',
        string='Resolución de Facturación Electrónica jurídica vigente',
        compute='_compute_resolucion_facturacion_electronica_juridica_actual_id',
    )

    @api.depends(
        'resolucion_facturacion_electronica_juridica_2_id',
        'resolucion_facturacion_electronica_juridica_id',
    )
    def _compute_resolucion_facturacion_electronica_juridica_actual_id(self):
        for record in self:
            record.resolucion_facturacion_electronica_juridica_actual_id = (
                record.resolucion_facturacion_electronica_juridica_2_id
                or record.resolucion_facturacion_electronica_juridica_id
            )

    # Campo auxiliar para evitar múltiples ejecuciones
    resolucion_facturacion_juridica_copiada = fields.Boolean(string='¿Resolución de Facturación Electrónica Jurídica copiada?', compute='_compute_resolucion_facturacion_juridica_copiada_status', )

    # 2. La función que "vigila" el campo las 24 horas
    @api.depends('resolucion_facturacion_electronica_juridica_2_id')
    def _compute_resolucion_facturacion_juridica_copiada_status(self):
        for record in self:
            # Lógica simple: Si tiene ID es True, si está vacío es False.
            if record.resolucion_facturacion_electronica_juridica_2_id:
                record.resolucion_facturacion_juridica_copiada = True
            else:
                record.resolucion_facturacion_juridica_copiada = False

    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_resolucion_facturacion_electronica_juridica(self):
        for record in self:
            new_value = False

            # --- Asignar el documento según el tipo seleccionado ---
            if record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                new_value = record.relacion_orden_compra_servicios_fuv_id.resolucion_facturacion_doc_id
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                new_value = record.relacion_orden_compra_bienes_fuv_id.resolucion_facturacion_doc_id
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.resolucion_facturacion_doc_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.resolucion_facturacion_doc_id

            record.resolucion_facturacion_electronica_juridica_id = new_value



    
    # -----------------------------------------------------------
    # BOTÓN: Copiar Resolución de Facturación Electrónica Jurídica al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_resolucion_facturacion_electronica_juridica_a_drive_proveedor(self):
        """Crea una copia del archivo 'Resolución de Facturación Electrónica Jurídica'
        dentro de la carpeta del proveedor. Si ya existe, no la vuelve a crear."""
        for record in self:
            if not record.resolucion_facturacion_electronica_juridica_id:
                raise UserError("No hay archivo de Resolución de Facturación Electrónica Jurídica seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.resolucion_facturacion_electronica_juridica_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión a Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia de Resolución de Facturación Electrónica Jurídica creada correctamente en Google Drive con ID: {new_file_id}")

                record.resolucion_facturacion_electronica_juridica_2_id = new_drive_record.id
                record.resolucion_facturacion_electronica_juridica = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>Copia de la Resolución de Facturación Electrónica Jurídica</b> "
                        f"en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo de Resolución de Facturación Electrónica Jurídica en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar la Resolución de Facturación Electrónica Jurídica en Google Drive:\n{e}")


    resolucion_facturacion_electronica_juridica_corregir = fields.Boolean(default=False, tracking=True, related='resolucion_facturacion_electronica_juridica_2_id.actualizar_visible', readonly=False)

    def action_open_resolucion_facturacion_electronica_juridica_drive(self):
        self.ensure_one()

        # 📨 Mantiene tu lógica original
        self.actualizar_correo_resolucion_facturacion_electronica_juridica_en_drive()

        drive_record = self.resolucion_facturacion_electronica_juridica_actual_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Oculta el contenido binario
                })
            else:
                # Si ya tiene vista previa, igualmente ocultamos el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (igual que tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolución de Facturación Electrónica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def actualizar_correo_resolucion_facturacion_electronica_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la resolución de facturación electrónica."""
        # Verificar si hay una relación con el modelo Drive
        if self.resolucion_facturacion_electronica_juridica_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.resolucion_facturacion_electronica_juridica_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.resolucion_facturacion_electronica_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False



    def _upload_resolucion_facturacion_electronica_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'resolucion_facturacion_electronica_juridica_documento',
            'resolucion_facturacion_electronica_juridica_name',
            'resolucion_facturacion_electronica_juridica_2_id',
            False,
            'resolucion_facturacion_electronica_juridica',
            '5_Resolución de Facturación Electrónica',
            'Resolución de facturación electrónica',
            False,
        )

    camara_comercio_juridica = fields.Boolean(string='Cámara de comercio (no mayor a 30 días)')
    camara_comercio_juridica_documento = fields.Binary(string='Cámara de comercio (no mayor a 30 días)')
    camara_comercio_juridica_name = fields.Char(string='Cámara de comercio (no mayor a 30 días)')
    camara_comercio_juridica_visible = fields.Boolean(string='Cámara de comercio Visible', default=True)
    camara_comercio_juridica_id = fields.Many2one('drive', string='Cámara de comercio (no mayor a 30 días)')

    camara_comercio_juridica_corregir = fields.Boolean(default=False, related='camara_comercio_juridica_id.actualizar_visible', readonly=False)

    def action_open_camara_comercio_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_camara_comercio_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cámara de comercio (no mayor a 30 días)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.camara_comercio_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_camara_comercio_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la cámara de comercio."""
        # Verificar si hay una relación con el modelo Drive
        if self.camara_comercio_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.camara_comercio_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.camara_comercio_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_camara_comercio_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'camara_comercio_juridica_documento',
            'camara_comercio_juridica_name',
            'camara_comercio_juridica_id',
            'camara_comercio_juridica_visible',
            'camara_comercio_juridica',
            '6_Cámara de comercio',
            'Cámara de comercio',
            False,
        )

    certificado_pago_parafiscales_juridica = fields.Boolean(string='Certificado de Pago Parafiscales')
    certificado_pago_parafiscales_juridica_documento = fields.Binary(string='Certificado de Pago Parafiscales', store=True)
    certificado_pago_parafiscales_juridica_name = fields.Char(string='Certificado de Pago Parafiscales',)
    certificado_pago_parafiscales_juridica_visible = fields.Boolean(string='Certificado de Pago Parafiscales Visible', default=True,)
    certificado_pago_parafiscales_juridica_id = fields.Many2one('drive', string='Certificado de Pago Parafiscales')

    certificado_pago_parafiscales_juridica_corregir = fields.Boolean(default=False, related='certificado_pago_parafiscales_juridica_id.actualizar_visible', readonly=False)

    def action_open_certificado_pago_parafiscales_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_certificado_pago_parafiscales_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado de Pago Parafiscales',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_pago_parafiscales_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_certificado_pago_parafiscales_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado al certificado de pago parafiscales."""
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_pago_parafiscales_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_pago_parafiscales_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.certificado_pago_parafiscales_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_certificado_pago_parafiscales_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificado_pago_parafiscales_juridica_documento',
            'certificado_pago_parafiscales_juridica_name',
            'certificado_pago_parafiscales_juridica_id',
            'certificado_pago_parafiscales_juridica_visible',
            'certificado_pago_parafiscales_juridica',
            '7_Certificado de Pago Parafiscales',
            'Certificado de pago parafiscales',
            False,
        )

    tarjeta_profesional_revisor_fiscal_juridica = fields.Boolean(string='Tarjeta profesional revisor fiscal (Si aplica)')
    tarjeta_profesional_revisor_fiscal_juridica_documento = fields.Binary(string='Tarjeta profesional revisor fiscal (Si aplica)')
    tarjeta_profesional_revisor_fiscal_juridica_name = fields.Char(string='Tarjeta profesional revisor fiscal (Si aplica)')
    tarjeta_profesional_revisor_fiscal_juridica_visible = fields.Boolean(string='Tarjeta profesional revisor fiscal Visible', default=True)
    tarjeta_profesional_revisor_fiscal_juridica_id = fields.Many2one('drive', string='Tarjeta profesional revisor fiscal (Si aplica)')

    tarjeta_profesional_revisor_fiscal_juridica_corregir = fields.Boolean(default=False, related='tarjeta_profesional_revisor_fiscal_juridica_id.actualizar_visible', readonly=False)

    def action_open_tarjeta_profesional_revisor_fiscal_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_tarjeta_profesional_revisor_fiscal_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarjeta profesional de Revisor Fiscal (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.tarjeta_profesional_revisor_fiscal_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_tarjeta_profesional_revisor_fiscal_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la tarjeta profesional de revisor fiscal."""
        # Verificar si hay una relación con el modelo Drive
        if self.tarjeta_profesional_revisor_fiscal_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.tarjeta_profesional_revisor_fiscal_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.tarjeta_profesional_revisor_fiscal_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_tarjeta_profesional_revisor_fiscal_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'tarjeta_profesional_revisor_fiscal_juridica_documento',
            'tarjeta_profesional_revisor_fiscal_juridica_name',
            'tarjeta_profesional_revisor_fiscal_juridica_id',
            'tarjeta_profesional_revisor_fiscal_juridica_visible',
            'tarjeta_profesional_revisor_fiscal_juridica',
            '8_Tarjeta profesional revisor fiscal',
            'Tarjeta profesional revisor fiscal',
            False,
        )

    cedula_revisor_fiscal_juridica = fields.Boolean(string='Documento de identidad revisor fiscal (Si aplica)')
    cedula_revisor_fiscal_juridica_documento = fields.Binary(string='Documento de identidad revisor fiscal (Si aplica)')
    cedula_revisor_fiscal_juridica_name = fields.Char(string='Documento de identidad revisor fiscal (Si aplica)')
    cedula_revisor_fiscal_juridica_visible = fields.Boolean(string='Documento de identidad revisor fiscal Visible', default=True)
    cedula_revisor_fiscal_juridica_id = fields.Many2one('drive', string='Documento de identidad revisor fiscal (Si aplica)')

    cedula_revisor_fiscal_juridica_corregir = fields.Boolean(default=False, related='cedula_revisor_fiscal_juridica_id.actualizar_visible', readonly=False)

    def action_open_cedula_revisor_fiscal_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_cedula_revisor_fiscal_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad Revisor Fiscal (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_revisor_fiscal_juridica_id.id,
            'target': 'new',
        }

    
    def actualizar_correo_cedula_revisor_fiscal_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado al documento de identidad del revisor fiscal."""
        # Verificar si hay una relación con el modelo Drive
        if self.cedula_revisor_fiscal_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.cedula_revisor_fiscal_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.cedula_revisor_fiscal_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    
    def _upload_cedula_revisor_fiscal_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'cedula_revisor_fiscal_juridica_documento',
            'cedula_revisor_fiscal_juridica_name',
            'cedula_revisor_fiscal_juridica_id',
            'cedula_revisor_fiscal_juridica_visible',
            'cedula_revisor_fiscal_juridica',
            '16_Documento de identidad revisor fiscal',
            'Documento de identidad revisor fiscal',
            False,
        )

    cedula_representante_legal_juridica = fields.Boolean(string='Cedula del representante legal, revisor fiscal (si aplica)', tracking=True)
    cedula_representante_legal_juridica_documento = fields.Binary(string='Cedula del Representante Legal, Revisor Fiscal (si aplica)')
    cedula_representante_legal_juridica_name = fields.Char(string='Cedula del Representante Legal, Revisor Fiscal (si aplica)')
    cedula_representante_legal_juridica_visible = fields.Boolean(string='Cedula del Representante Legal, Revisor Fiscal Visible', default=True)
    cedula_representante_legal_juridica_id = fields.Many2one('drive', string='Cedula del Representante Legal, Revisor Fiscal (si aplica)')

    cedula_representante_legal_juridica_corregir = fields.Boolean(default=False, tracking=True, related='cedula_representante_legal_juridica_id.actualizar_visible', readonly=False)

    def action_open_cedula_representante_legal_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_cedula_representante_legal_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cedula del Representante Legal, Revisor Fiscal (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_representante_legal_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_cedula_representante_legal_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la cédula del representante legal."""
        # Verificar si hay una relación con el modelo Drive
        if self.cedula_representante_legal_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.cedula_representante_legal_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.cedula_representante_legal_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_cedula_representante_legal_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'cedula_representante_legal_juridica_documento',
            'cedula_representante_legal_juridica_name',
            'cedula_representante_legal_juridica_id',
            'cedula_representante_legal_juridica_visible',
            'cedula_representante_legal_juridica',
            '9_Cedula del representante legal, revisor fiscal',
            'Cédula del representante legal',
            False,
        )

    cedula_representante_legal_de_contador_juridica = fields.Boolean(string='Documento de identidad de representante legal, contador (Si aplica)', tracking=True)
    cedula_representante_legal_de_contador_juridica_documento = fields.Binary(string='Documento de identidad de representante legal, contador (Si aplica)')
    cedula_representante_legal_de_contador_juridica_name = fields.Char(string='Documento de identidad de representante legal, contador (Si aplica)')
    cedula_representante_legal_de_contador_juridica_visible = fields.Boolean(string='Documento de identidad de representante legal, contado Visible', default=True)
    cedula_representante_legal_de_contador_juridica_id = fields.Many2one('drive', string='Documento de identidad de representante legal, contado (Si aplica)')

    cedula_representante_legal_de_contador_juridica_corregir = fields.Boolean(default=False, tracking=True, related='cedula_representante_legal_de_contador_juridica_id.actualizar_visible', readonly=False)

    def action_open_cedula_representante_legal_de_contador_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_cedula_representante_legal_de_contador_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad de representante legal, contado (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_representante_legal_de_contador_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_cedula_representante_legal_de_contador_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado al documento de identidad del contador."""
        # Verificar si hay una relación con el modelo Drive
        if self.cedula_representante_legal_de_contador_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.cedula_representante_legal_de_contador_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.cedula_representante_legal_de_contador_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_cedula_representante_legal_de_contador_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'cedula_representante_legal_de_contador_juridica_documento',
            'cedula_representante_legal_de_contador_juridica_name',
            'cedula_representante_legal_de_contador_juridica_id',
            'cedula_representante_legal_de_contador_juridica_visible',
            False,
            '16_Documento de identidad de representante legal contador',
            'Documento de identidad de representante legal contador',
            False,
        )

    Tarjeta_profesional_contador_juridica = fields.Boolean(string='Tarjeta profesional contador (Si aplica)', tracking=True)
    Tarjeta_profesional_contador_juridica_documento = fields.Binary(string='Tarjeta profesional contador (Si aplica)')
    Tarjeta_profesional_contador_juridica_name = fields.Char(string='Tarjeta profesional contador (Si aplica)')
    Tarjeta_profesional_contador_juridica_visible = fields.Boolean(string='Tarjeta profesional contador Visible', default=True)
    Tarjeta_profesional_contador_juridica_id = fields.Many2one('drive', string='Tarjeta profesional contador (Si aplica)')

    Tarjeta_profesional_contador_juridica_corregir = fields.Boolean(default=False, related='Tarjeta_profesional_contador_juridica_id.actualizar_visible', readonly=False)

    def action_open_Tarjeta_profesional_contador_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_Tarjeta_profesional_contador_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarjeta profesional contador (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.Tarjeta_profesional_contador_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_Tarjeta_profesional_contador_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la tarjeta profesional del contador."""
        # Verificar si hay una relación con el modelo Drive
        if self.Tarjeta_profesional_contador_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.Tarjeta_profesional_contador_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.Tarjeta_profesional_contador_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False


    def _upload_Tarjeta_profesional_contador_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'Tarjeta_profesional_contador_juridica_documento',
            'Tarjeta_profesional_contador_juridica_name',
            'Tarjeta_profesional_contador_juridica_id',
            'Tarjeta_profesional_contador_juridica_visible',
            'Tarjeta_profesional_contador_juridica',
            '15_Tarjeta profesional contador',
            'Tarjeta profesional contador',
            False,
        )

    certificado_junta_contadores_juridica = fields.Boolean(string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)')
    certificado_junta_contadores_juridica_documento = fields.Binary(string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)')
    certificado_junta_contadores_juridica_name = fields.Char(string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)')
    certificado_junta_contadores_juridica_visible = fields.Boolean(string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador Visible', default=True)
    certificado_junta_contadores_juridica_id = fields.Many2one('drive', string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)')

    certificado_junta_contadores_juridica_corregir = fields.Boolean(default=False, related='certificado_junta_contadores_juridica_id.actualizar_visible', readonly=False)

    def action_open_certificado_junta_contadores_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_certificado_junta_contadores_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_junta_contadores_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_certificado_junta_contadores_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado al certificado de junta de contadores."""
        # Verificar si hay una relación con el modelo Drive
        if self.certificado_junta_contadores_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificado_junta_contadores_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.certificado_junta_contadores_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_certificado_junta_contadores_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'certificado_junta_contadores_juridica_documento',
            'certificado_junta_contadores_juridica_name',
            'certificado_junta_contadores_juridica_id',
            'certificado_junta_contadores_juridica_visible',
            'certificado_junta_contadores_juridica',
            '9_Certificado de Junta de Contadores del Revisor Fiscal y/o Contador',
            'Certificado de Junta de Contadores',
            False,
        )

    rut_representante_legal_juridica = fields.Boolean(string='RUT Representante Legal (si aplica)', tracking=True)
    rut_representante_legal_juridica_documento = fields.Binary(string='RUT Representante Legal (si aplica)')
    rut_representante_legal_juridica_name = fields.Char(string='RUT Representante Legal (si aplica)')
    rut_representante_legal_juridica_visible = fields.Boolean(string='RUT Representante Legal Visible', default=True)
    rut_representante_legal_juridica_id = fields.Many2one('drive', string='RUT Representante Legal (si aplica)')

    rut_representante_legal_juridica_corregir = fields.Boolean(default=False, related='rut_representante_legal_juridica_id.actualizar_visible', readonly=False)


    def action_open_rut_representante_legal_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_rut_representante_legal_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT Representante Legal (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_representante_legal_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_rut_representante_legal_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado al RUT del representante legal."""
        # Verificar si hay una relación con el modelo Drive
        if self.rut_representante_legal_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.rut_representante_legal_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.rut_representante_legal_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_rut_representante_legal_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'rut_representante_legal_juridica_documento',
            'rut_representante_legal_juridica_name',
            'rut_representante_legal_juridica_id',
            'rut_representante_legal_juridica_visible',
            'rut_representante_legal_juridica',
            '10_RUT Representante Legal',
            'RUT representante legal',
            False,
        )

    certificacion_bancaria_juridica = fields.Boolean(string='Certificación bancaria (actualizada)')
    certificacion_bancaria_juridica_documento = fields.Binary(string='Certificación bancaria (actualizada)')
    certificacion_bancaria_juridica_name = fields.Char(string='Certificación bancaria (actualizada)')
    certificacion_bancaria_juridica_visible = fields.Boolean(string='Certificación bancaria Visible', default=True, related='relacion_fuv_id.certificado_bancario_doc_visible')
    certificacion_bancaria_juridica_id = fields.Many2one('drive', string='Certificación bancaria (actualizada)', compute='_compute_certificacion_bancaria_juridica', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    certificacion_bancaria_juridica_2_id = fields.Many2one('drive', string='Certificación bancaria (actualizada) 2', domain="[('stage4_id', '=', drive_stage_provedor_id)]")

    # Campo auxiliar para evitar múltiples ejecuciones
    certificacion_bancaria_juridica_copiada = fields.Boolean(string='¿Certificación Bancaria Jurídica copiada?', compute='_compute_certificacion_bancaria_juridica_copiada_status')

    @api.depends('certificacion_bancaria_juridica_2_id')
    def _compute_certificacion_bancaria_juridica_copiada_status(self):
        for record in self:
            # Lógica simple: Si tiene ID es True, si está vacío es False.
            if record.certificacion_bancaria_juridica_2_id:
                record.certificacion_bancaria_juridica_copiada = True
            else:
                record.certificacion_bancaria_juridica_copiada = False

    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_certificacion_bancaria_juridica(self):
        for record in self:
            record.certificacion_bancaria_juridica_id = False

    # -----------------------------------------------------------
    # BOTÓN: Copiar Certificación Bancaria Jurídica (actualizada) al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_certificacion_bancaria_juridica_a_drive_proveedor(self):
        """Crea una copia del archivo 'Certificación Bancaria Jurídica (actualizada)'
        dentro de la carpeta del proveedor. Si ya existe, no vuelve a crearla."""
        for record in self:
            if not record.certificacion_bancaria_juridica_id:
                raise UserError("No hay archivo de Certificación Bancaria Jurídica (actualizada) seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.certificacion_bancaria_juridica_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión a Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia de Certificación Bancaria Jurídica creada correctamente en Google Drive con ID: {new_file_id}")

                record.certificacion_bancaria_juridica_2_id = new_drive_record.id
                record.certificacion_bancaria_juridica = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>Copia de la Certificación Bancaria Jurídica (actualizada)</b> "
                        f"en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo de Certificación Bancaria Jurídica en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar la Certificación Bancaria Jurídica (actualizada) en Google Drive:\n{e}")



    certificacion_bancaria_juridica_corregir = fields.Boolean(default=False, tracking=True, related='certificacion_bancaria_juridica_2_id.actualizar_visible', readonly=False)

    def action_open_certificacion_bancaria_juridica_drive(self):
        self.ensure_one()

        # 📨 Mantiene tu lógica original
        self.actualizar_correo_certificacion_bancaria_juridica_en_drive()

        drive_record = self.certificacion_bancaria_juridica_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Ocultar el contenido binario al abrir
                })
            else:
                # Si ya existen los HTML, igualmente ocultamos el file_data
                drive_record.file_data_visible = False

        # 🪟 Abrir el archivo en ventana modal (como tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación bancaria (actualizada)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }

    def actualizar_correo_certificacion_bancaria_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la certificación bancaria."""
        # Verificar si hay una relación con el modelo Drive
        if self.certificacion_bancaria_juridica_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.certificacion_bancaria_juridica_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.certificacion_bancaria_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_certificacion_bancaria_juridica_to_drive(self):
        """Sube la certificación bancaria jurídica con validación y reintentos.

        Esta es la función piloto. Si la subida falla por un problema temporal,
        el binario NO se borra para que el cron pueda volver a intentarlo.
        """
        self.ensure_one()

        if not self.certificacion_bancaria_juridica_documento:
            _logger.info(
                "[DRIVE_RETRY_TEST] Acta %s sin binario pendiente de certificación bancaria jurídica.",
                self.id,
            )
            return False

        stage_record = self.drive_stage_provedor_id
        if not stage_record or not stage_record.folder_id:
            _logger.error(
                "[DRIVE_RETRY_TEST] Acta=%s cuenta=%s sin carpeta destino vinculada. "
                "El binario se conserva para reintento.",
                self.id,
                self.cuenta_id.id if self.cuenta_id else False,
            )
            return False

        # Evitar subir a una carpeta vinculada a otra acta/cuenta.
        if (
            'contratistas_provedores' in stage_record._fields
            and stage_record.contratistas_provedores
            and stage_record.contratistas_provedores != self
        ):
            _logger.error(
                "[DRIVE_RETRY_TEST] BLOQUEADO: acta=%s intenta usar carpeta=%s "
                "vinculada a acta=%s. No se sube el archivo.",
                self.id,
                stage_record.folder_id,
                stage_record.contratistas_provedores.id,
            )
            return False

        carpeta_drive = str(stage_record.folder_id).strip()
        temp_file_path = False

        try:
            drive = GoogleDriveAuth.get_instance().get_drive()

            # Confirmar que la carpeta realmente existe en Google Drive y no está eliminada.
            folder_drive = drive.CreateFile({'id': carpeta_drive})
            folder_drive.FetchMetadata(fields='id,title,labels')
            if (folder_drive.get('labels') or {}).get('trashed'):
                _logger.error(
                    "[DRIVE_RETRY_TEST] Carpeta destino eliminada. acta=%s folder_id=%s",
                    self.id,
                    carpeta_drive,
                )
                return False

            original_name = self.certificacion_bancaria_juridica_name or 'certificacion_bancaria.pdf'
            original_extension = os.path.splitext(original_name)[-1].lower() or '.bin'
            mime_type = {
                '.pdf': 'application/pdf',
                '.doc': 'application/msword',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.xls': 'application/vnd.ms-excel',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
            }.get(original_extension, 'application/octet-stream')

            # Nombre estable: permite recuperar el archivo si Google alcanzó a recibirlo
            # pero la respuesta se perdió por una caída temporal de red.
            fixed_file_name = (
                f"11_Certificación bancaria_acta_{self.id}_"
                f"cuenta_{self.cuenta_id.id if self.cuenta_id else 0}{original_extension}"
            )

            try:
                file_content = base64.b64decode(self.certificacion_bancaria_juridica_documento)
            except Exception:
                _logger.exception(
                    "[DRIVE_RETRY_TEST] Binario inválido. acta=%s nombre=%s",
                    self.id,
                    original_name,
                )
                return False

            if not file_content:
                _logger.error(
                    "[DRIVE_RETRY_TEST] Binario vacío. acta=%s nombre=%s",
                    self.id,
                    original_name,
                )
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=original_extension) as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            # Preferir el documento ya vinculado, pero solo si pertenece a esta carpeta.
            existing_drive_record = self.certificacion_bancaria_juridica_2_id
            if (
                not existing_drive_record
                or existing_drive_record.folder_id != carpeta_drive
                or not existing_drive_record.file_id
            ):
                existing_drive_record = self.env['drive'].sudo().search([
                    ('folder_id', '=', carpeta_drive),
                    ('stage4_id', '=', stage_record.id),
                    ('file_name', 'like', '11_Certificación bancaria_%'),
                ], order='id desc', limit=1)

            last_error = False
            max_attempts = 3

            for attempt in range(1, max_attempts + 1):
                try:
                    _logger.info(
                        "[DRIVE_RETRY_TEST] intento=%s/%s acta=%s cuenta=%s "
                        "stage=%s folder_id=%s folder_name=%s archivo=%s",
                        attempt,
                        max_attempts,
                        self.id,
                        self.cuenta_id.id if self.cuenta_id else False,
                        stage_record.id,
                        carpeta_drive,
                        stage_record.folder_name,
                        fixed_file_name,
                    )

                    remote_file = False
                    remote_file_id = False

                    # Si Odoo ya conoce un archivo, verificar antes de sobrescribirlo.
                    if existing_drive_record and existing_drive_record.file_id:
                        candidate = drive.CreateFile({'id': existing_drive_record.file_id})
                        try:
                            candidate.FetchMetadata(fields='id,title,parents,labels')
                            parent_ids = [
                                parent.get('id')
                                for parent in (candidate.get('parents') or [])
                                if parent.get('id')
                            ]
                            if (
                                not (candidate.get('labels') or {}).get('trashed')
                                and carpeta_drive in parent_ids
                            ):
                                remote_file = candidate
                                remote_file_id = candidate.get('id')
                            else:
                                _logger.warning(
                                    "[DRIVE_RETRY_TEST] El drive.file %s apunta a un remoto fuera "
                                    "de la carpeta esperada. Se creará/recuperará uno correcto.",
                                    existing_drive_record.id,
                                )
                        except Exception:
                            _logger.warning(
                                "[DRIVE_RETRY_TEST] No se pudo validar el remoto existente %s; "
                                "se intentará recuperar por nombre y carpeta.",
                                existing_drive_record.file_id,
                                exc_info=True,
                            )

                    # Recuperar un posible archivo huérfano de un intento anterior.
                    if not remote_file:
                        safe_title = fixed_file_name.replace("\\", "\\\\").replace("'", "\\'")
                        query = (
                            f"title='{safe_title}' and '{carpeta_drive}' in parents "
                            f"and trashed=false"
                        )
                        recovered = drive.ListFile({'q': query}).GetList()
                        if recovered:
                            remote_file = drive.CreateFile({'id': recovered[0]['id']})
                            remote_file.FetchMetadata(fields='id,title,parents,labels')
                            remote_file_id = remote_file.get('id')
                            _logger.warning(
                                "[DRIVE_RETRY_TEST] Recuperado archivo remoto de intento anterior: %s",
                                remote_file_id,
                            )

                    # Crear solo cuando no existe un remoto recuperable.
                    if not remote_file:
                        remote_file = drive.CreateFile({
                            'title': fixed_file_name,
                            'mimeType': mime_type,
                            'parents': [{'id': carpeta_drive}],
                        })

                    remote_file['title'] = fixed_file_name
                    remote_file.SetContentFile(temp_file_path)
                    remote_file.Upload()

                    remote_file_id = remote_file.get('id') or remote_file_id
                    if not remote_file_id:
                        raise UserError("Google Drive no devolvió un file_id después de la subida.")

                    # Verificación posterior: el archivo debe existir y estar EXACTAMENTE en esta carpeta.
                    verify_file = drive.CreateFile({'id': remote_file_id})
                    verify_file.FetchMetadata(fields='id,title,parents,labels')
                    verify_parent_ids = [
                        parent.get('id')
                        for parent in (verify_file.get('parents') or [])
                        if parent.get('id')
                    ]
                    if (verify_file.get('labels') or {}).get('trashed'):
                        raise UserError("El archivo subido aparece eliminado en Google Drive.")
                    if carpeta_drive not in verify_parent_ids:
                        raise UserError(
                            "Google Drive confirmó el archivo, pero no quedó en la carpeta esperada."
                        )

                    file_link = f"https://drive.google.com/file/d/{remote_file_id}/view"
                    preview_url = f"https://drive.google.com/file/d/{remote_file_id}/preview"

                    drive_vals = {
                        'file_name': fixed_file_name,
                        'name': fixed_file_name,
                        'file_id': remote_file_id,
                        'file_url': file_link,
                        'preview_url': preview_url,
                        'edit_url': file_link,
                        'file_data': False,
                        'folder_id': carpeta_drive,
                        'stage4_id': stage_record.id,
                    }

                    # Si había un registro correcto, actualizarlo; si no, recuperar por file_id o crear.
                    drive_record = existing_drive_record
                    if not drive_record or drive_record.file_id != remote_file_id:
                        drive_record = self.env['drive'].sudo().search([
                            ('file_id', '=', remote_file_id),
                        ], limit=1)

                    if drive_record:
                        drive_record.sudo().write(drive_vals)
                    else:
                        drive_record = self.env['drive'].sudo().create(drive_vals)

                    # Solo después de confirmar Google + drive.file limpiamos el binario.
                    self.with_context(
                        allow_person_document_write=True,
                        mail_notrack=True,
                        tracking_disable=True,
                    ).write({
                        'certificacion_bancaria_juridica_2_id': drive_record.id,
                        'certificacion_bancaria_juridica_documento': False,
                        'certificacion_bancaria_juridica_visible': False,
                        'certificacion_bancaria_juridica': True,
                    })

                    self.message_post(
                        body=(
                            "✅ El documento <b>Certificación bancaria</b> fue subido "
                            "y verificado correctamente en Google Drive."
                        ),
                        subtype_xmlid="mail.mt_note",
                    )

                    _logger.info(
                        "[DRIVE_RETRY_TEST] ÉXITO acta=%s drive_record=%s "
                        "file_id=%s folder_id=%s",
                        self.id,
                        drive_record.id,
                        remote_file_id,
                        carpeta_drive,
                    )
                    return drive_record

                except Exception as error:
                    last_error = error
                    _logger.warning(
                        "[DRIVE_RETRY_TEST] Falló intento %s/%s. acta=%s folder_id=%s error=%s",
                        attempt,
                        max_attempts,
                        self.id,
                        carpeta_drive,
                        error,
                        exc_info=True,
                    )
                    if attempt < max_attempts:
                        time.sleep(2 ** attempt)

            # IMPORTANTE: no borrar el binario. El cron lo encontrará y reintentará.
            _logger.error(
                "[DRIVE_RETRY_TEST] Subida pendiente tras %s intentos. acta=%s "
                "folder_id=%s. El binario se conserva. Último error=%s",
                max_attempts,
                self.id,
                carpeta_drive,
                last_error,
            )
            return False

        except Exception as error:
            _logger.exception(
                "[DRIVE_RETRY_TEST] Error preparando la subida. acta=%s cuenta=%s error=%s",
                self.id,
                self.cuenta_id.id if self.cuenta_id else False,
                error,
            )
            return False

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    _logger.warning(
                        "[DRIVE_RETRY_TEST] No se pudo eliminar temporal %s",
                        temp_file_path,
                        exc_info=True,
                    )




    anexo_tributario_juridica = fields.Boolean(string='Anexo tributario')
    anexo_tributario_juridica_documento = fields.Binary(string='Anexo tributario')
    anexo_tributario_juridica_name = fields.Char(string='Anexo tributario')
    anexo_tributario_juridica_visible = fields.Boolean(string='Anexo tributario Visible', default=True, related='relacion_fuv_id.anexo_tributario_juridico_doc_visible')
    anexo_tributario_juridica_id = fields.Many2one('drive', string='Anexo tributario', compute='_compute_objeto_contrato', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    anexo_tributario_juridica_2_id = fields.Many2one('drive', string='Anexo tributario 2', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    
    # Campo auxiliar para evitar ejecuciones múltiples
    anexo_juridico_copiado = fields.Boolean(string='¿Anexo Jurídico copiado?', compute='_compute_anexo_juridico_copiado_status')

    anexo_tributario_juridica_corregir = fields.Boolean(default=False, tracking=True, related='anexo_tributario_juridica_2_id.actualizar_visible', readonly=False)


    def actualizar_correo_anexo_tributario_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la certificación bancaria."""
        # Verificar si hay una relación con el modelo Drive
        if self.anexo_tributario_juridica_2_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.anexo_tributario_juridica_2_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.anexo_tributario_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    @api.depends('anexo_tributario_juridica_2_id')
    def _compute_anexo_juridico_copiado_status(self):
        for record in self:
            # Lógica simple: Si tiene ID es True, si está vacío es False.
            if record.anexo_tributario_juridica_2_id:
                record.anexo_juridico_copiado = True
            else:
                record.anexo_juridico_copiado = False

    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_objeto_contrato(self):
        for record in self:
            new_value = False

            # --- Determinar el anexo según el tipo de selección ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.anexo_tributario_juridico_doc_id
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                new_value = record.relacion_orden_compra_bienes_fuv_id.Anexo_tributario_doc_id
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                new_value = record.relacion_orden_compra_servicios_fuv_id.Anexo_tributario_doc_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.anexo_tributario_juridico_doc_id

            record.anexo_tributario_juridica_id = new_value


    

    # -----------------------------------------------------------
    # BOTÓN: Copiar anexo existente dentro de Google Drive
    # -----------------------------------------------------------
    def action_copiar_anexo_a_drive_proveedor(self):
        """Crea una copia del archivo existente en Google Drive dentro de la carpeta del proveedor (drive_stage_provedor_id).
        Si ya existe una copia en esa carpeta, no vuelve a crearla."""
        for record in self:
            if not record.anexo_tributario_juridica_id:
                raise UserError("No hay archivo Anexo Tributario Jurídico seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.anexo_tributario_juridica_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión con Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    record.anexo_tributario_juridica_2_id = existing_copy.id
                    record.anexo_tributario_juridica = True
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia de archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                # Ejecutar la copia mediante el servicio de Google Drive
                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia creada correctamente en Google Drive con ID: {new_file_id}")
                record.anexo_tributario_juridica_2_id = new_drive_record.id
                record.anexo_tributario_juridica = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>copia del Anexo Tributario Jurídico</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar el archivo en Google Drive:\n{e}")

    def action_open_anexo_tributario_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_anexo_tributario_juridica_en_drive()


        drive_record = self.anexo_tributario_juridica_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Ocultar campo binario al abrir
                })
            else:
                # Si ya existe vista previa, igual ocultamos el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (igual que tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Anexo tributario',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }


    def _upload_anexo_tributario_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'anexo_tributario_juridica_documento',
            'anexo_tributario_juridica_name',
            'anexo_tributario_juridica_2_id',
            'anexo_tributario_juridica_visible',
            'anexo_tributario_juridica',
            '12_Anexo tributario',
            'Anexo tributario',
            False,
        )

    # Primera factura
    copia_contrato_juridica = fields.Boolean(string='Copia contrato')
    copia_contrato_juridica_documento = fields.Binary(string='Copia contrato')
    copia_contrato_juridica_name = fields.Char(string='Copia contrato')
    copia_contrato_juridica_visible = fields.Boolean(string='Copia contrato Visible', related='relacion_fuv_id.aprueba_j_h_contrato_firmado_doc_visible')
    copia_contrato_juridica_id = fields.Many2one('drive', string='Copia contrato', compute='_compute_copia_contrato_juridica_id', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
    
    copia_contrato_juridica_2_id = fields.Many2one('drive', string='Copia contrato 2', domain="[('stage4_id', '=', drive_stage_provedor_id)]")
 
    # Campo auxiliar para evitar ejecuciones múltiples
    contrato_juridico_copiado = fields.Boolean(string='¿Contrato Jurídico copiado?', compute='_compute_contrato_juridico_copiado_status')

    @api.depends('copia_contrato_juridica_2_id')
    def _compute_contrato_juridico_copiado_status(self):
        for record in self:
            # Lógica simple: Si tiene ID es True, si está vacío es False.
            if record.copia_contrato_juridica_2_id:
                record.contrato_juridico_copiado = True
            else:
                record.contrato_juridico_copiado = False


    @api.depends(
        'selecionar',
        'relacion_fuv_id',
        'relacion_otrosi_fuv_id'
    )
    def _compute_copia_contrato_juridica_id(self):
        for record in self:
            new_value = False

            # --- Asignar el archivo según el tipo seleccionado ---
            if record.selecionar == 'contrato' and record.relacion_fuv_id:
                new_value = record.relacion_fuv_id.aprueba_j_h_contrato_firmado_doc_id
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                new_value = record.relacion_otrosi_fuv_id.aprueba_j_h_contrato_firmado_doc_id

            record.copia_contrato_juridica_id = new_value


    
    # -----------------------------------------------------------
    # BOTÓN: Copiar Copia de Contrato Jurídica al Drive del proveedor
    # -----------------------------------------------------------
    def action_copiar_contrato_juridica_a_drive_proveedor(self):
        """Crea una copia del archivo 'Copia Contrato Jurídica' dentro de la carpeta del proveedor.
        Si ya existe la copia, no la vuelve a crear."""
        for record in self:
            if not record.copia_contrato_juridica_id:
                raise UserError("No hay archivo de Copia de Contrato Jurídica seleccionado.")
            if not record.drive_stage_provedor_id:
                raise UserError("Debe seleccionar una cuenta de cobros (carpeta destino).")

            drive_target = record.drive_stage_provedor_id
            folder_id = drive_target.folder_id
            if not folder_id:
                raise UserError("La carpeta de destino en Google Drive no está configurada.")

            # Archivo original
            file_drive = record.copia_contrato_juridica_id
            file_id = record._resolve_google_drive_file_id(file_drive)
            if not file_id:
                raise UserError("El archivo original no tiene un ID válido de Google Drive (file_id).")

            # Conexión con Google Drive
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            try:
                # Obtener metadatos del archivo original
                original_file = drive.CreateFile({'id': file_id})
                original_file.FetchMetadata(fields='title')
                original_name = original_file['title']

                # Nombre previsto para la copia
                copy_name = f"{original_name}"

                # 🔍 Verificar si ya existe una copia en Odoo
                existing_copy = self.env['drive'].search([
                    ('folder_id', '=', folder_id),
                    ('file_name', '=', copy_name),
                    ('stage4_id', '=', drive_target.id)
                ], limit=1)

                if existing_copy:
                    _logger.info(f"Ya existe una copia del archivo '{copy_name}' en la carpeta del proveedor. No se creará otra.")
                    continue  # Evita duplicar la copia

                _logger.info(f"Creando copia del archivo '{original_name}' en carpeta {folder_id}...")

                # ⚙️ Crear copia dentro de la carpeta destino
                copied_metadata = {
                    'title': copy_name,
                    'parents': [{'id': folder_id}],
                }

                copied_file = drive.auth.service.files().copy(
                    fileId=file_id,
                    body=copied_metadata
                ).execute()

                # ✅ Datos de la nueva copia
                new_file_id = copied_file.get('id')
                new_file_name = copied_file.get('title', copy_name)
                new_file_url = f"https://drive.google.com/file/d/{new_file_id}/view"

                # Registrar copia en Odoo
                new_drive_record = self.env['drive'].create({
                    'file_name': new_file_name,
                    'file_id': new_file_id,
                    'file_url': new_file_url,
                    'folder_id': folder_id,
                    'stage4_id': drive_target.id,
                })

                _logger.info(f"Copia de Contrato Jurídica creada correctamente en Google Drive con ID: {new_file_id}")
                record.copia_contrato_juridica_2_id = new_drive_record.id
                record.copia_contrato_juridica = True


                # Publicar mensaje en el chatter
                record.message_post(
                    body=f"Se creó una <b>copia del Contrato Jurídica</b> en Google Drive:<br>",
                    subtype_xmlid="mail.mt_note"
                )

            except Exception as e:
                _logger.error(f"Error copiando archivo de Contrato Jurídica en Google Drive: {e}")
                raise UserError(f"Ocurrió un error al copiar la Copia del Contrato Jurídica en Google Drive:\n{e}")


    def action_open_copia_contrato_juridica_drive(self):
        self.ensure_one()

        drive_record = self.copia_contrato_juridica_2_id
        if drive_record:
            # 🧩 Generar URLs y HTML de vista si no están listos
            if not drive_record.preview_html or not drive_record.preview_url:
                preview_url = f"https://drive.google.com/file/d/{drive_record.file_id}/preview"
                edit_url = drive_record.edit_url or f"https://drive.google.com/file/d/{drive_record.file_id}/edit"

                preview_html = f'<iframe src="{preview_url}" width="100%" height="600px"></iframe>'
                edit_html = f'<iframe src="{edit_url}" width="100%" height="600px"></iframe>'

                drive_record.write({
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data_visible': False,  # 🔒 Ocultar campo binario al abrir
                })
            else:
                # Si ya existe vista previa, igual ocultamos el file_data
                drive_record.file_data_visible = False

        # 🪟 Abre el archivo en ventana modal (igual que tu versión original)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Copia contrato',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_record.id if drive_record else False,
            'target': 'new',
        }

    def _upload_copia_contrato_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'copia_contrato_juridica_documento',
            'copia_contrato_juridica_name',
            'copia_contrato_juridica_2_id',
            'copia_contrato_juridica_visible',
            'copia_contrato_juridica',
            '13_Copia contrato',
            'Copia contrato',
            False,
        )

    cedula_de_contador_juridica = fields.Boolean(string='Documento de identidad Contador (Si aplica)')
    cedula_de_contador_juridica_documento = fields.Binary(string='Documento de identidad Contador (Si aplica)')
    cedula_de_contador_juridica_name = fields.Char(string='Documento de identidad Contador (Si aplica)')
    cedula_de_contador_juridica_visible = fields.Boolean(string='Documento de identidad Contador (Si aplica) Visible', default=True)
    cedula_de_contador_juridica_id = fields.Many2one('drive', string='Documento de identidad Contador (Si aplica)')

    cedula_de_contador_juridica_corregir = fields.Boolean(default=False, related='cedula_de_contador_juridica_id.actualizar_visible', readonly=False)

    def action_open_cedula_de_contador_juridica_drive(self):
        self.ensure_one()
        self.actualizar_correo_cedula_de_contador_juridica_en_drive()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad Contador (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_de_contador_juridica_id.id,
            'target': 'new',
        }

    def actualizar_correo_cedula_de_contador_juridica_en_drive(self):
        """Actualiza el correo electrónico en el registro de Google Drive asociado a la cédula del contador."""
        # Verificar si hay una relación con el modelo Drive
        if self.cedula_de_contador_juridica_id:
            # Actualizar el campo correo_electronico en el modelo Drive con el valor de la variable correo
            self.cedula_de_contador_juridica_id.write({
                'correo_electrinico': self.correo_facturacion,
                'actualizar_visible': self.cedula_de_contador_juridica_corregir,
                'file_data_visible': self.file_data_visible
            })
            return True
        else:
            return False

    def _upload_cedula_de_contador_juridica_to_drive(self):
        return self._upload_documento_persona_robusto_to_drive(
            'cedula_de_contador_juridica_documento',
            'cedula_de_contador_juridica_name',
            'cedula_de_contador_juridica_id',
            'cedula_de_contador_juridica_visible',
            'cedula_de_contador_juridica',
            '14_Documento de identidad Contador',
            'Documento de identidad del contador',
            False,
        )

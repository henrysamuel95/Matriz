import uuid
import logging
import os
import base64
import tempfile
import re
from docx import Document
from io import BytesIO
from docxtpl import DocxTemplate
from datetime import date
from docx.shared import Inches
from odoo import models, fields, api
from odoo.http import request
from .drive import GoogleDriveAuth
from odoo.exceptions import ValidationError
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CuentasDeCobros(models.Model):
    _name = 'cuentas_de_cobros'
    _description = 'Cuentas de cobros'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    origen_cuenta = fields.Selection([
        ('fuv', 'Fundación Universidad del Valle'),
        ('regalias', 'Sistema General de Regalías'),
    ], string='Origen de la cuenta', required=True, default='fuv', tracking=True)
    proyecto_regalias_id = fields.Many2one(
        'informacion_base_agregar',
        string='Proyecto de Regalías',
        domain=[('area_solicitante2', '=', 'regalias')],
        tracking=True,
    )
    contrato_regalias_id = fields.Many2one(
        'base.datos.regalias.contrato',
        string='Contrato de Regalías',
        tracking=True,
    )
    contrato_regalias_numero = fields.Char(
        related='contrato_regalias_id.numero_de_contrato',
        string='Contrato de Regalías',
        readonly=True,
    )
    contrato_regalias_saldo = fields.Char(
        related='contrato_regalias_id.saldo_por_utilizar',
        string='Saldo contrato Regalías',
        readonly=True,
    )
    regalias_copia_cedula_id = fields.Many2one(
        'drive',
        related='contratista_id.regalias_copia_cedula_id',
        string='Cédula Regalías',
        readonly=True,
    )
    regalias_copia_cedula_corregir = fields.Boolean(
        related='contratista_id.regalias_copia_cedula_corregir',
        string='Corregir cédula Regalías',
        readonly=False,
    )
    regalias_copia_contrato_id = fields.Many2one(
        'drive',
        related='contratista_id.regalias_copia_contrato_id',
        string='Contrato Regalías',
        readonly=True,
    )
    regalias_copia_contrato_corregir = fields.Boolean(
        related='contratista_id.regalias_copia_contrato_corregir',
        string='Corregir contrato Regalías',
        readonly=False,
    )
    proyecto_regalias_nombre = fields.Char(
        related='proyecto_regalias_id.nombre_proceso_proyecto',
        string='Proyecto de Regalías',
        readonly=True,
    )
    regalias_centro_costo = fields.Char(
        string='Centro de costo', compute='_compute_datos_regalias'
    )
    regalias_costo_produccion = fields.Monetary(
        string='Costo de producción', compute='_compute_datos_regalias'
    )

    @api.depends(
        'proyecto_regalias_id',
        'proyecto_regalias_id.id_proyecto',
        'proyecto_regalias_id.mcb_ids.new_total_costos_produccion_N_r',
        'proyecto_regalias_id.mcb_ids.new_mcb_ids.new_total_costos_produccion_N_r',
    )
    def _compute_datos_regalias(self):
        for record in self:
            proyecto = record.proyecto_regalias_id.sudo()
            mcb = proyecto.mcb_ids[:1] if proyecto else False
            mcb_detalle = mcb.new_mcb_ids[:1] if mcb else False
            record.regalias_centro_costo = proyecto.id_proyecto if proyecto else False
            record.regalias_costo_produccion = (
                (
                    (mcb_detalle.new_total_costos_produccion_N_r if mcb_detalle else 0.0)
                    or mcb.new_total_costos_produccion_N_r
                )
                if mcb else 0.0
            )

    @api.constrains(
        'origen_cuenta', 'proyecto_regalias_id', 'contrato_regalias_id', 'selecionar',
        'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id',
    )
    def _check_origen_cuenta(self):
        for record in self:
            if record.origen_cuenta == 'regalias':
                if record.selecionar != 'regalias':
                    raise ValidationError(
                        'La vinculación de una cuenta de Regalías debe permanecer como Regalías.'
                    )
                if (
                    not record.proyecto_regalias_id
                    or record.proyecto_regalias_id.area_solicitante2 != 'regalias'
                ):
                    raise ValidationError(
                        'La cuenta de Regalías debe estar vinculada a un proyecto del Sistema General de Regalías.'
                    )
                if any((
                    record.relacion_fuv_id,
                    record.relacion_orden_compra_servicios_fuv_id,
                    record.relacion_orden_compra_bienes_fuv_id,
                    record.relacion_otrosi_fuv_id,
                )):
                    raise ValidationError(
                        'Una cuenta de Regalías no puede quedar vinculada a contratos u órdenes FUV.'
                    )
            elif record.selecionar == 'regalias' or record.proyecto_regalias_id or record.contrato_regalias_id:
                raise ValidationError(
                    'Selecciona Sistema General de Regalías como origen para usar ese proyecto.'
                )

    @api.constrains(
        'origen_cuenta', 'selecionar', 'pago',
        'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id',
        'proyecto_regalias_id', 'contrato_regalias_id',
    )
    def _check_datos_minimos_creacion_portal(self):
        for record in self:
            if not record.selecionar:
                raise ValidationError('Selecciona el tipo de contrato antes de crear la cuenta de cobro.')

            if not record.pago or record.pago < 1:
                raise ValidationError('El Pago N° debe ser mayor a cero.')

            if record.origen_cuenta == 'regalias':
                if not record.proyecto_regalias_id:
                    raise ValidationError('Selecciona el proyecto de Regalías antes de crear la cuenta de cobro.')
                continue

            relacion_requerida = {
                'contrato': record.relacion_fuv_id,
                'servicios': record.relacion_orden_compra_servicios_fuv_id,
                'bienes': record.relacion_orden_compra_bienes_fuv_id,
                'otrosi': record.relacion_otrosi_fuv_id,
            }.get(record.selecionar)

            if not relacion_requerida:
                raise ValidationError('Selecciona la relación, contrato u orden antes de crear la cuenta de cobro.')

    def message_post(self, **kwargs):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        is_public_request = bool(
            request and hasattr(request, 'httprequest') and request.httprequest.path.startswith('/public/')
        )
        if not portal_login_id and is_public_request and len(self) == 1 and self.login_id:
            portal_login_id = self.login_id.id
        if portal_login_id and not kwargs.get('author_id'):
            login = self.env['login'].sudo().browse(portal_login_id)
            if login.exists():
                partner = login._get_portal_chatter_partner()
                if partner:
                    kwargs['author_id'] = partner.id
        return super(CuentasDeCobros, self).message_post(**kwargs)

    def _sgo_debe_silenciar_tracking_automatico(self, vals):
        """Evita ruido tipo 'No → Sí' para documentos/flags automáticos.

        Los cambios reales de estado se mantienen con tracking normal.
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
            'origen_cuenta',
            'selecionar',
            'proyecto_regalias_id',
            'contrato_regalias_id',
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
        )
        palabras_automaticas = (
            'visible',
            'corregir',
            'actualizar',
            'bloqueo',
            'opcion_otro_documento',
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

    drive_stage_provedor_id = fields.Many2one('cuenta_de_cobros', string='Drive cuenta de cobros', related='contratista_id.drive_stage_provedor_id')

    valor_total_contrato = fields.Monetary(string='Valor total del contrato', tracking=True, related='total_relacionado')
    valor_cuenta = fields.Monetary(string='Valor cuenta/factura', tracking=True)

    bloqueo = fields.Boolean(string='Bloqueo', default=False, related='contratista_id.bloqueo') # <--- Asegúrate de que esta línea esté aquí
    periodo_actividad_corregir = fields.Boolean(
        string='Corregir fechas de actividad',
        related='contratista_id.periodo_actividad_corregir',
        readonly=True,
    )
    

    habilitar_validacion = fields.Boolean(default=False)
    relacion_ids = fields.One2many('declaracion_juramentada', 'relacion', string='Relaciones de cuenta de cobro')
    relacion_informe_ids = fields.One2many('informe_actividades', 'cuenta_cobro_id', string='Relaciones de cuenta de cobro')

    tabla_de_forma_de_pago = fields.One2many('tabla_forma_de_pago', 'contrataciones_fuv_tabla_id', string='Tabla', related='relacion_fuv_id.tabla_de_forma_de_pago', readonly=True)
    lineas_ids = fields.One2many('orden.compra.linea', 'orden_id', string='Líneas', related='relacion_orden_compra_bienes_fuv_id.lineas_ids', readonly=True)
    lineas_servicios_ids = fields.One2many('orden_compra_servicios.linea', 'orden_id', string='Líneas de Servicios', related='relacion_orden_compra_servicios_fuv_id.lineas_ids', readonly=True)

    def unlink(self):
        # Iteramos sobre los registros que se van a eliminar
        for record in self:
            # 1. Eliminar declaraciones juramentadas vinculadas
            if record.relacion_ids:
                record.relacion_ids.unlink()
            
            # 2. Eliminar informes de actividades vinculados
            if record.relacion_informe_ids:
                record.relacion_informe_ids.unlink()
                
        # 3. Llamar al super() para que elimine finalmente la cuenta de cobro
        return super(CuentasDeCobros, self).unlink()

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
    )

    anio = fields.Integer(
        string='Año',
        default=lambda self: date.today().year,
        tracking=True,
    )

    inicio_actividad = fields.Date(
        string='Inicio de actividad',
        tracking=True,
    )
    finalizacion_actividad = fields.Date(
        string='Finalización de actividad',
        tracking=True,
    )

    @api.constrains('inicio_actividad', 'finalizacion_actividad')
    def _check_fechas_actividad_validas(self):
        for record in self:
            for fecha, etiqueta in (
                (record.inicio_actividad, 'Inicio de actividad'),
                (record.finalizacion_actividad, 'Finalización de actividad'),
            ):
                if fecha and fecha.year < 1000:
                    raise ValidationError(
                        "%s: el año debe tener cuatro dígitos y no puede comenzar por cero."
                        % etiqueta
                    )
            if (
                record.inicio_actividad
                and record.finalizacion_actividad
                and record.finalizacion_actividad < record.inicio_actividad
            ):
                raise ValidationError(
                    'La finalización de actividad no puede ser anterior al inicio de actividad.'
                )

    def _linea_desembolso_seleccionada(self):
        self.ensure_one()
        fuente = False
        if self.selecionar == 'contrato':
            fuente = self.relacion_fuv_id
        elif self.selecionar == 'servicios':
            fuente = self.relacion_orden_compra_servicios_fuv_id
        elif self.selecionar == 'bienes':
            fuente = self.relacion_orden_compra_bienes_fuv_id
        elif self.selecionar == 'otrosi':
            fuente = self.relacion_otrosi_fuv_id
        numero_desembolso = self.pago_forma_pago or self.pago
        if not fuente or not numero_desembolso or 'tabla_de_forma_de_pago' not in fuente._fields:
            return self.env['tabla_forma_de_pago']

        lineas = fuente.sudo().tabla_de_forma_de_pago.sorted(
            key=lambda linea: (linea.numero_de_pagos or 0, linea.id)
        )
        linea = lineas.filtered(
            lambda item: item.numero_de_pagos == numero_desembolso
        )[:1]
        return linea

    def _valor_cuenta_es_desembolso_completo(self, linea):
        self.ensure_one()
        if not linea:
            return False
        return abs((self.valor_cuenta or 0.0) - (linea.cantidad_de_dinero or 0.0)) < 0.01

    def _otrosi_pago_manual(self):
        """Indica si el Pago N° escrito no corresponde a una fila del otrosí."""
        self.ensure_one()
        relacion = self.relacion_otrosi_fuv_id
        return bool(
            self.selecionar == 'otrosi'
            and relacion
            and (self.pago_forma_pago or self.pago)
            and not any(
                item['numero_de_pagos'] == int(self.pago_forma_pago or self.pago)
                for item in self._forma_pago_otrosi_fuv(relacion)
            )
        )

    def _saldo_total_disponible_fuv(self):
        self.ensure_one()
        if self.origen_cuenta == 'regalias':
            return 0.0
        mapa = {
            'contrato': ('relacion_fuv_id', self.relacion_fuv_id, self.relacion_fuv_id.valor_total_contrato if self.relacion_fuv_id else 0.0),
            'servicios': ('relacion_orden_compra_servicios_fuv_id', self.relacion_orden_compra_servicios_fuv_id, self.relacion_orden_compra_servicios_fuv_id.total if self.relacion_orden_compra_servicios_fuv_id else 0.0),
            'bienes': ('relacion_orden_compra_bienes_fuv_id', self.relacion_orden_compra_bienes_fuv_id, (self.relacion_orden_compra_bienes_fuv_id.total or self.relacion_orden_compra_bienes_fuv_id.total_poliza) if self.relacion_orden_compra_bienes_fuv_id else 0.0),
            'otrosi': ('relacion_otrosi_fuv_id', self.relacion_otrosi_fuv_id, self.relacion_otrosi_fuv_id.valor_total_contrato if self.relacion_otrosi_fuv_id else 0.0),
        }
        config = mapa.get(self.selecionar)
        if not config:
            return 0.0
        campo, relacion, total = config
        if not relacion or not total:
            return 0.0
        domain = [
            ('origen_cuenta', '!=', 'regalias'),
            ('selecionar', '=', self.selecionar),
            (campo, '=', relacion.id),
            ('contratista_id.estado', '!=', 'rechazado'),
        ]
        if self.id and not isinstance(self.id, models.NewId):
            domain.append(('id', '!=', self.id))
        total_fisico = 0.0
        if self.selecionar == 'contrato':
            total_fisico = self._detalle_pagos_fisicos_contrato_fuv(
                relacion,
                identificacion=self.documento_de_identificacion,
                nombre=self.nombre_contratista or self.name,
            )[2]
        total_sgo = 0.0 if total_fisico > 0 else sum(self.sudo().search(domain).mapped('valor_cuenta'))
        return max((total or 0.0) - total_sgo - total_fisico, 0.0)

    @api.model
    def _saldo_disponible_contrato_regalias(self, contrato, exclude_id=None):
        """Saldo del contrato menos cuentas de Regalías no rechazadas en SGO."""
        if not contrato:
            return 0.0
        saldo_base = self._parse_regalias_amount(contrato.saldo_por_utilizar)
        numero_contrato = contrato.numero_de_contrato or contrato.rpc or ''
        numero_key = re.sub(r'[^0-9a-zA-Z]+', '', numero_contrato).lower()
        identificacion_key = re.sub(r'\D+', '', contrato.identificacion or '')
        unidad_key = re.sub(r'[^0-9a-zA-Z]+', '', contrato.un or '').lower()
        contratos_equivalentes = contrato
        if numero_key:
            # La base importada puede traer el mismo contrato con espacios,
            # puntos u otros formatos distintos. Un dominio por igualdad deja
            # por fuera esas filas aunque visualmente muestren el mismo número.
            candidatos = self.env['base.datos.regalias.contrato'].sudo().search([])
            contratos_equivalentes = candidatos.filtered(
                lambda item: (
                    re.sub(
                        r'[^0-9a-zA-Z]+', '',
                        item.numero_de_contrato or item.rpc or ''
                    ).lower() == numero_key
                    and re.sub(r'\D+', '', item.identificacion or '') == identificacion_key
                )
            ) or contrato
        domain = [
            ('origen_cuenta', '=', 'regalias'),
            '|',
            ('contratista_id', '=', False),
            ('contratista_id.estado', '!=', 'rechazado'),
        ]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        contratos_equivalentes_ids = set(contratos_equivalentes.ids)

        def _cuenta_corresponde(cuenta):
            contrato_cuenta = cuenta.contrato_regalias_id
            if contrato_cuenta:
                if contrato_cuenta.id not in contratos_equivalentes_ids:
                    return False
            else:
                # Las cuentas antiguas pueden no tener contrato_regalias_id.
                # Se infiere igual que al abrir su formulario, pero sin escribir
                # el registro: solo se acepta cuando proyecto/identificación
                # permiten encontrar un único contrato equivalente.
                identificacion_cuenta = (
                    cuenta.documento_de_identificacion
                    or (cuenta.login_id.documento_de_identidad if cuenta.login_id else '')
                )
                if re.sub(r'\D+', '', identificacion_cuenta or '') != identificacion_key:
                    return False
                contrato_cuenta = cuenta._buscar_contrato_regalias_automatico(
                    cuenta.proyecto_regalias_id,
                    identificacion_cuenta,
                    contratacion=cuenta.proyecto_relacionado,
                )
                if not contrato_cuenta or contrato_cuenta.id not in contratos_equivalentes_ids:
                    return False

            if not unidad_key:
                return True
            unidad_proyecto_key = re.sub(
                r'[^0-9a-zA-Z]+', '',
                cuenta._regalias_centro_costo_proyecto(
                    cuenta.proyecto_regalias_id
                ) or ''
            ).lower()
            unidad_contrato_key = re.sub(
                r'[^0-9a-zA-Z]+', '', contrato_cuenta.un or ''
            ).lower()
            return (
                unidad_proyecto_key == unidad_key
                if unidad_proyecto_key
                else unidad_contrato_key == unidad_key
            )

        cuentas_registradas = self.sudo().search(domain).filtered(_cuenta_corresponde)
        total_registrado = sum(cuentas_registradas.mapped('valor_cuenta'))
        return max(saldo_base - total_registrado, 0.0)

    @api.constrains(
        'valor_cuenta', 'pago', 'pago_forma_pago', 'selecionar', 'origen_cuenta',
        'proyecto_regalias_id', 'contrato_regalias_id',
        'relacion_fuv_id', 'relacion_otrosi_fuv_id',
    )
    def _check_valor_desembolso_seleccionado(self):
        if self.env.context.get('portal_correction_financial_values_unchanged'):
            return
        for record in self:
            if record.origen_cuenta == 'regalias':
                if record.valor_cuenta <= 0:
                    raise ValidationError('El valor a cobrar debe ser mayor que cero.')
                # Compatibilidad con cuentas antiguas creadas antes de que el
                # contrato de Regalías se guardara explícitamente. El portal
                # nuevo sí lo exige y valida antes de llegar al modelo.
                if not record.contrato_regalias_id:
                    continue
                valor_disponible_regalias = record._saldo_disponible_contrato_regalias(
                    record.contrato_regalias_id,
                    exclude_id=(
                        record.id
                        if record.id and not isinstance(record.id, models.NewId)
                        else None
                    ),
                )

                if record.valor_cuenta > valor_disponible_regalias:
                    raise ValidationError(
                        'El valor a cobrar no puede superar el saldo por utilizar '
                        'del contrato de Regalías ($ {:,.2f}).'.format(
                            valor_disponible_regalias or 0.0
                        )
                    )
                continue
            if record.selecionar not in ('contrato', 'servicios', 'bienes', 'otrosi'):
                continue
            if record.valor_cuenta <= 0:
                raise ValidationError('El valor a cobrar debe ser mayor que cero.')
            linea = record._linea_desembolso_seleccionada()
            if record.selecionar in ('contrato', 'otrosi'):
                otrosi_pago_manual = record._otrosi_pago_manual()
                if not linea and not otrosi_pago_manual:
                    raise ValidationError(
                        'Selecciona el desembolso que vas a cobrar en la tabla de Forma de pago.'
                    )
                if otrosi_pago_manual:
                    # Si el número escrito no está en la tabla (incluida una
                    # tabla vacía), se captura manualmente y se conserva como
                    # tope el saldo total del otrosí.
                    valor_maximo = record._saldo_total_disponible_fuv()
                elif record.selecionar == 'contrato':
                    disponible = record._detalle_disponible_pago_contrato_fuv(
                        record.relacion_fuv_id,
                        record.pago_forma_pago or record.pago,
                        identificacion=record.documento_de_identificacion,
                        nombre=record.nombre_contratista or record.name,
                        exclude_id=record.id if record.id and not isinstance(record.id, models.NewId) else None,
                    )
                else:
                    disponible = next((
                        item for item in record._forma_pago_otrosi_fuv(
                            record.relacion_otrosi_fuv_id,
                            exclude_id=record.id if record.id and not isinstance(record.id, models.NewId) else None,
                        )
                        if item['numero_de_pagos'] == int(record.pago_forma_pago or record.pago or 0)
                    ), False)
                if not otrosi_pago_manual:
                    valor_maximo = disponible['valor_disponible'] if disponible else 0.0
                    if valor_maximo <= 0:
                        raise ValidationError(
                            'El desembolso seleccionado ya no tiene saldo disponible para cobrar.'
                        )
                    saldo_total = record._saldo_total_disponible_fuv()
                    valor_maximo = min(valor_maximo, saldo_total)
            else:
                valor_maximo = record._saldo_total_disponible_fuv()
            if record.valor_cuenta > valor_maximo:
                nombre_tope = 'saldo disponible del desembolso seleccionado' if record.selecionar in ('contrato', 'otrosi') else 'saldo disponible'
                raise ValidationError(
                    'El valor a cobrar no puede superar el {} '
                    '($ {:,.2f}).'.format(nombre_tope, valor_maximo)
                )

    
    estado_pago = fields.Char(
        string='Estado del pago',
        related='contratista_id.estado_pago',
    )


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


    active = fields.Boolean(default=True)
    # Campo para seleccionar usuarios con el rol específico
    usuarios_contratacion = fields.Many2many(
        'res.users',
        string='Usuarios',
        domain=lambda self: self._get_usuarios_contratacion_domain(),
        help="Seleccione los usuarios que pertenecen al grupo 'Contratación Ventanilla Única'."
    )

    estado = fields.Selection(
        [('asis_coordinador', 'Asistente de coordinación'),('coordinador', 'Coordinador'), ('ventanilla', 'Ventanilla'), ('director_financiero', 'Director Financiero'), ('finaciera', 'Contabilidad'), ('au_tesoreria', 'Auxiliar de tesorería'), ('tesorería', 'Tesorería'), ('finalizado', 'Finalizado'), ('rechazado', 'Rechazado')],
        string='Estado',
        default='asis_coordinador',
        tracking=True,
        related='contratista_id.estado',
    )

    estado_display = fields.Char(
        string="Estado (texto visible)",
        compute="_compute_estado_display"
    )

    soporte_estado = fields.Selection(
        related='contratista_id.estado',
        string='Estado del flujo',
        readonly=False,
    )
    soporte_bloqueo = fields.Boolean(
        related='contratista_id.bloqueo',
        string='Portal habilitado para corrección',
        readonly=False,
    )
    soporte_informacion_bloqueada = fields.Boolean(
        related='contratista_id.informacion_bloqueada',
        string='Información contractual bloqueada',
        readonly=False,
    )
    soporte_flujo_en_devolucion = fields.Boolean(
        related='contratista_id.flujo_en_devolucion',
        string='Flujo en devolución',
        readonly=False,
    )
    soporte_volver = fields.Selection(
        related='contratista_id.volver',
        string='Volver a',
        readonly=False,
    )
    soporte_notificacion_enviada = fields.Boolean(
        related='contratista_id.notificacion_enviada',
        string='Notificación enviada',
        readonly=False,
    )
    soporte_usuarios_contratacion = fields.Many2many(
        related='contratista_id.usuarios_contratacion',
        string='Coordinador asignado',
        readonly=False,
    )
    soporte_usuarios_supervision = fields.Many2many(
        related='contratista_id.usuarios_supervision',
        string='Ventanilla asignada',
        readonly=False,
    )
    soporte_usuarios_contabilidad = fields.Many2many(
        related='contratista_id.usuarios_contabilidad',
        string='Contabilidad asignada',
        readonly=False,
    )
    soporte_usuarios_auxiliar_tesoreria = fields.Many2many(
        related='contratista_id.usuarios_auxiliar_tesoreria',
        string='Auxiliar tesorería asignado',
        readonly=False,
    )
    soporte_usuarios_tesoreria = fields.Many2many(
        related='contratista_id.usuarios_tesoreria',
        string='Tesorería asignada',
        readonly=False,
    )
    soporte_observaciones = fields.Text(
        related='contratista_id.observaciones',
        string='Observaciones coordinación',
        readonly=False,
    )
    soporte_observaciones_ventanilla = fields.Text(
        related='contratista_id.observaciones_ventanilla',
        string='Observaciones ventanilla',
        readonly=False,
    )
    soporte_observaciones_contabilidad = fields.Text(
        related='contratista_id.observaciones_contabilidad',
        string='Observaciones contabilidad',
        readonly=False,
    )
    soporte_observaciones_aux_tesoreria = fields.Text(
        related='contratista_id.observaciones_aux_tesoreria',
        string='Observaciones auxiliar tesorería',
        readonly=False,
    )
    soporte_observaciones_tesoreria = fields.Text(
        related='contratista_id.observaciones_tesoreria',
        string='Observaciones tesorería',
        readonly=False,
    )
    soporte_observaciones_devolver = fields.Text(
        related='contratista_id.observaciones_devolver',
        string='Observación devolver desde ventanilla',
        readonly=False,
    )
    soporte_observaciones_devolver_1 = fields.Text(
        related='contratista_id.observaciones_devolver_1',
        string='Observación devolver desde contabilidad',
        readonly=False,
    )
    soporte_observaciones_devolver_2 = fields.Text(
        related='contratista_id.observaciones_devolver_2',
        string='Observación devolver desde auxiliar tesorería',
        readonly=False,
    )
    soporte_observaciones_devolver_3 = fields.Text(
        related='contratista_id.observaciones_devolver_3',
        string='Observación devolver desde tesorería',
        readonly=False,
    )
    soporte_observaciones_devolver_4 = fields.Text(
        related='contratista_id.observaciones_devolver_4',
        string='Observación devolver desde coordinador',
        readonly=False,
    )

    @api.depends('estado')
    def _compute_estado_display(self):
        for record in self:
            # Si el contratista_id tiene el campo estado con selección
            if record.contratista_id and 'estado' in record.contratista_id._fields:
                selection = record.contratista_id._fields['estado'].selection
                # Si la selección es una función, la ejecutamos
                if callable(selection):
                    selection = selection(record.contratista_id)
                selection_dict = dict(selection)
                record.estado_display = selection_dict.get(record.estado, '')
            else:
                record.estado_display = record.estado or ''



    def _get_usuarios_contratacion_domain(self):
        group = self.env.ref('Matriz.group_contratacion_ventanilla_unica', raise_if_not_found=False)
        if group:
            return [('id', 'in', group.users.ids)]
        return []

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

    def action_soporte_abrir_acta_entrega(self):
        self.ensure_one()
        if not self.contratista_id:
            raise UserError("Esta cuenta todavía no tiene un acta de entrega vinculada.")
        return {
            'type': 'ir.actions.act_window',
            'name': 'Acta de entrega de documentos',
            'view_mode': 'form',
            'res_model': 'acta_entrega_documentos',
            'res_id': self.contratista_id.id,
            'target': 'current',
            'context': {'allow_finalized_write': True},
        }

    def action_soporte_reparar_fechas_actividad(self):
        """Abre una vista segura que no intenta interpretar fechas dañadas."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError('Solo el grupo Soporte puede reparar fechas de actividad.')

        fecha_inicio = fields.Date.to_string(self.inicio_actividad) if self.inicio_actividad else ''
        fecha_fin = fields.Date.to_string(self.finalizacion_actividad) if self.finalizacion_actividad else ''
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reparar fechas de actividad',
            'res_model': 'cuentas_de_cobros.reparar_fechas.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_cuentas_de_cobros_reparar_fechas_wizard').id,
            'target': 'new',
            'context': {
                'default_cuenta_id': self.id,
                'default_fecha_actual_inicio': fecha_inicio or 'Sin fecha',
                'default_fecha_actual_fin': fecha_fin or 'Sin fecha',
                'default_inicio_corregido': (
                    fecha_inicio if self.inicio_actividad and self.inicio_actividad.year >= 1000 else False
                ),
                'default_finalizacion_corregida': (
                    fecha_fin if self.finalizacion_actividad and self.finalizacion_actividad.year >= 1000 else False
                ),
            },
        }

    contratacion_fuv_id = fields.Many2one(
        'contrataciones_fuv',
        string='Contratación FUV Relacionada',
        help='Contratación FUV con el mismo nombre que esta acta (relación directa).',
        tracking=True, 
    )

    relacion_fuv_id = fields.Many2one(
        'contrataciones_fuv_relacion',
        string='Relación de Contrataciones', 
    )

    relacion_orden_compra_servicios_fuv_id = fields.Many2one(
        'orden_compra_servicios',
        string='Contrato servicio',
    )

    relacion_orden_compra_bienes_fuv_id = fields.Many2one(
        'orden_compra_bienes',
        string='Contrato bienes', 
    )

    relacion_otrosi_fuv_id = fields.Many2one(
        'otrosi_bdd',
        string='Otrosí',
    )
    


    selecionar = fields.Selection([
        ('servicios', 'Orden de prestación de servicios'),
        ('bienes', 'Orden de compra de bienes'),
        ('contrato', 'Contrato'),
        ('otrosi', 'Otrosí'),
        ('regalias', 'Regalías'),
    ], string='Seleccionar tipo de contrato')


    selecionar_display = fields.Char(
        string="Estado (texto visible)",
        compute="_compute_selecionar_display"
    )

    @api.depends('selecionar')
    def _compute_selecionar_display(self):
        for record in self:
            # Si el contratista_id tiene el campo selecionar con selección
            if record.contratista_id and 'selecionar' in record.contratista_id._fields:
                selection = record.contratista_id._fields['selecionar'].selection
                # Si la selección es una función, la ejecutamos
                if callable(selection):
                    selection = selection(record.contratista_id)
                selection_dict = dict(selection)
                record.selecionar_display = selection_dict.get(record.selecionar, '')
            else:
                record.selecionar_display = record.selecionar or ''

    def _parse_regalias_amount(self, value):
        if value in (None, False):
            return 0.0
        text = str(value).strip().replace('$', '').replace(' ', '')
        if not text:
            return 0.0
        if ',' in text and '.' in text:
            if text.rfind(',') > text.rfind('.'):
                text = text.replace('.', '').replace(',', '.')
            else:
                text = text.replace(',', '')
        elif ',' in text:
            text = text.replace(',', '.')
        try:
            return float(text)
        except (TypeError, ValueError):
            return 0.0

    def _normalizar_detalle_texto(self, value):
        return re.sub(r'[^0-9A-Z]+', '', str(value or '').upper())

    def _normalizar_detalle_digitos(self, value):
        return re.sub(r'\D+', '', str(value or ''))

    def _detalle_valores_contrato_fuv(self, relacion, identificacion=None, nombre=None):
        relacion = relacion.sudo() if relacion else False
        contrato_valores = [
            getattr(relacion, 'id_del_contrato', False),
            getattr(relacion, 'display_name', False),
            getattr(relacion, 'name', False),
            getattr(relacion, 'id_proyecto3', False),
        ] if relacion else []
        un_valores = [
            getattr(relacion, 'mcb_centro_costo', False),
            getattr(getattr(relacion, 'proyecto_relacionado', False), 'mcb_centro_costo', False),
        ] if relacion else []
        identificacion_valores = [
            identificacion,
            getattr(relacion, 'documento_de_identificacion', False),
            getattr(getattr(relacion, 'contratista_proveedor_relacionado', False), 'numero_documento', False),
            getattr(getattr(relacion, 'contratista_proveedor_relacionado', False), 'documento_de_identificacion', False),
            getattr(getattr(relacion, 'contratista_proveedor_relacionado', False), 'nit', False),
        ] if relacion else [identificacion]
        nombre_valores = [
            nombre,
            getattr(relacion, 'nombre_contratista', False),
            getattr(relacion, 'nombre', False),
            getattr(getattr(relacion, 'contratista_proveedor_relacionado', False), 'name', False),
            getattr(getattr(relacion, 'contratista_proveedor_relacionado', False), 'nombre', False),
        ] if relacion else [nombre]

        return {
            'contratos': {self._normalizar_detalle_texto(v) for v in contrato_valores if v},
            'contratos_digitos': {self._normalizar_detalle_digitos(v) for v in contrato_valores if self._normalizar_detalle_digitos(v)},
            'uns': {self._normalizar_detalle_texto(v) for v in un_valores if v},
            'uns_digitos': {self._normalizar_detalle_digitos(v) for v in un_valores if self._normalizar_detalle_digitos(v)},
            'identificaciones': {self._normalizar_detalle_digitos(v) for v in identificacion_valores if self._normalizar_detalle_digitos(v)},
            'nombres': {self._normalizar_detalle_texto(v) for v in nombre_valores if v},
        }

    def _detalles_fisicos_contrato_fuv(self, relacion, identificacion=None, nombre=None):
        valores = self._detalle_valores_contrato_fuv(relacion, identificacion=identificacion, nombre=nombre)
        detalles = self.env['detalle.por.un'].sudo().search([
            ('active', '=', True),
            ('tipo_movimiento', '=', 'egreso'),
        ])
        encontrados = self.env['detalle.por.un'].sudo()

        for detalle in detalles:
            detalle_identificacion = self._normalizar_detalle_digitos(detalle.identificacion)
            detalle_nombre = self._normalizar_detalle_texto(detalle.nombre)
            detalle_contrato = self._normalizar_detalle_texto(detalle.contrato)
            detalle_contrato_digitos = self._normalizar_detalle_digitos(detalle.contrato)
            detalle_un = self._normalizar_detalle_texto(detalle.un)
            detalle_un_digitos = self._normalizar_detalle_digitos(detalle.un)

            coincide_identificacion = bool(
                detalle_identificacion and detalle_identificacion in valores['identificaciones']
            )
            coincide_nombre = bool(
                detalle_nombre
                and any(detalle_nombre in nombre_key or nombre_key in detalle_nombre for nombre_key in valores['nombres'])
            )
            coincide_contrato = bool(
                detalle_contrato
                and any(detalle_contrato in contrato_key or contrato_key in detalle_contrato for contrato_key in valores['contratos'])
            ) or bool(
                detalle_contrato_digitos and detalle_contrato_digitos in valores['contratos_digitos']
            )
            coincide_un = bool(detalle_un and detalle_un in valores['uns']) or bool(
                detalle_un_digitos and detalle_un_digitos in valores['uns_digitos']
            )

            if (coincide_identificacion or coincide_nombre) and (coincide_contrato or coincide_un):
                encontrados |= detalle

        return encontrados

    def _detalle_pagos_fisicos_contrato_fuv(self, relacion, identificacion=None, nombre=None):
        por_cuota = {}
        sin_cuota = 0.0
        detalles = self._detalles_fisicos_contrato_fuv(
            relacion,
            identificacion=identificacion,
            nombre=nombre,
        )
        for detalle in detalles:
            valor = float(detalle.valor_cobrado or 0.0)
            # En esta BDD el número de pago/cuota no define qué cuota se
            # debe bloquear en el portal. Solo usamos el VALOR COBRADO total
            # y lo aplicamos contra la forma de pago en orden.
            sin_cuota += valor
        return por_cuota, sin_cuota, sum(detalles.mapped('valor_cobrado'))

    def _sgo_pagado_previo_contrato_fuv(self, relacion, pago, exclude_id=None):
        if not relacion or not pago:
            return 0.0
        domain = [
            ('origen_cuenta', '!=', 'regalias'),
            ('selecionar', '=', 'contrato'),
            ('relacion_fuv_id', '=', relacion.id),
            '|',
            ('pago_forma_pago', '=', pago),
            '&',
            ('pago_forma_pago', '=', False),
            ('pago', '=', pago),
            ('contratista_id.estado', '!=', 'rechazado'),
        ]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        return sum(self.sudo().search(domain).mapped('valor_cuenta'))

    def _sgo_pagado_previo_otrosi_fuv(self, relacion, pago, exclude_id=None):
        if not relacion or not pago:
            return 0.0
        domain = [
            ('origen_cuenta', '!=', 'regalias'),
            ('selecionar', '=', 'otrosi'),
            ('relacion_otrosi_fuv_id', '=', relacion.id),
            '|',
            ('pago_forma_pago', '=', pago),
            '&',
            ('pago_forma_pago', '=', False),
            ('pago', '=', pago),
            ('contratista_id.estado', '!=', 'rechazado'),
        ]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        return sum(self.sudo().search(domain).mapped('valor_cuenta'))

    def _detalle_forma_pago_contrato_fuv(self, relacion, identificacion=None, nombre=None, exclude_id=None):
        relacion = relacion.sudo() if relacion else False
        if not relacion or 'tabla_de_forma_de_pago' not in relacion._fields:
            return []

        fisico_por_cuota, fisico_sin_cuota, total_fisico = self._detalle_pagos_fisicos_contrato_fuv(
            relacion,
            identificacion=identificacion,
            nombre=nombre,
        )
        usar_sgo = total_fisico <= 0
        resultado = []
        saldo_fisico_para_siguiente = 0.0
        for linea in relacion.tabla_de_forma_de_pago.sorted(key=lambda item: (item.numero_de_pagos or 0, item.id)):
            numero_pago = int(linea.numero_de_pagos or 0)
            valor_linea = float(linea.cantidad_de_dinero or 0.0)
            fisico_aplicado = float(fisico_por_cuota.get(numero_pago, 0.0))
            if fisico_sin_cuota > 0:
                if fisico_sin_cuota >= max(valor_linea - fisico_aplicado, 0.0):
                    asignado = max(valor_linea - fisico_aplicado, 0.0)
                    fisico_aplicado += asignado
                    fisico_sin_cuota -= asignado
                else:
                    fisico_aplicado += fisico_sin_cuota
                    fisico_sin_cuota = 0.0
                    # La cuota parcialmente cubierta queda bloqueada. Su parte
                    # pendiente se traslada a la siguiente cuota seleccionable.
                    saldo_fisico_para_siguiente = max(valor_linea - fisico_aplicado, 0.0)
            sgo_pagado = (
                self._sgo_pagado_previo_contrato_fuv(
                    relacion,
                    numero_pago,
                    exclude_id=exclude_id,
                )
                if usar_sgo else 0.0
            )
            bloqueado_por_fisico = fisico_aplicado > 0
            disponible = 0.0 if bloqueado_por_fisico else max(valor_linea - sgo_pagado, 0.0)
            if not bloqueado_por_fisico and saldo_fisico_para_siguiente > 0:
                disponible += saldo_fisico_para_siguiente
                saldo_fisico_para_siguiente = 0.0
            resultado.append({
                'linea': linea,
                'numero_de_pagos': numero_pago,
                'cantidad_de_dinero': valor_linea,
                'porcentaje_pago': float(linea.pago or 0.0),
                'porcentaje_acumulado': float(linea.porcentaje_acumulado or 0.0),
                'tecnico': float(linea.tecnico or 0.0),
                'condicion': linea.condicion or '',
                'valor_cobrado_fisico': fisico_aplicado,
                'valor_cobrado_sgo': sgo_pagado,
                'valor_disponible': disponible,
                'bloqueado': bloqueado_por_fisico or disponible <= 0,
            })
        return resultado

    def _forma_pago_otrosi_fuv(self, relacion, exclude_id=None):
        relacion = relacion.sudo() if relacion else False
        if not relacion or 'tabla_de_forma_de_pago' not in relacion._fields:
            return []

        resultado = []
        for linea in relacion.tabla_de_forma_de_pago.sorted(key=lambda item: (item.numero_de_pagos or 0, item.id)):
            numero_pago = int(linea.numero_de_pagos or 0)
            valor_linea = float(linea.cantidad_de_dinero or 0.0)
            sgo_pagado = self._sgo_pagado_previo_otrosi_fuv(
                relacion,
                numero_pago,
                exclude_id=exclude_id,
            )
            disponible = max(valor_linea - sgo_pagado, 0.0)
            resultado.append({
                'linea': linea,
                'numero_de_pagos': numero_pago,
                'cantidad_de_dinero': valor_linea,
                'porcentaje_pago': float(linea.pago or 0.0),
                'porcentaje_acumulado': float(linea.porcentaje_acumulado or 0.0),
                'tecnico': float(linea.tecnico or 0.0),
                'condicion': linea.condicion or '',
                'valor_cobrado_fisico': 0.0,
                'valor_cobrado_sgo': sgo_pagado,
                'valor_disponible': disponible,
                'bloqueado': disponible <= 0,
            })
        return resultado

    def _detalle_disponible_pago_contrato_fuv(self, relacion, pago, identificacion=None, nombre=None, exclude_id=None):
        for item in self._detalle_forma_pago_contrato_fuv(
            relacion,
            identificacion=identificacion,
            nombre=nombre,
            exclude_id=exclude_id,
        ):
            if item['numero_de_pagos'] == int(pago or 0):
                return item
        return False

    @api.depends('origen_cuenta', 'proyecto_regalias_id', 'contrato_regalias_id', 'selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_total_relacionado(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                record.total_relacionado = (
                    record._parse_regalias_amount(record.contrato_regalias_id.valor_de_contrato_vigencia_2025_2026)
                    if record.contrato_regalias_id else 0.0
                )
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.total_relacionado = record.relacion_orden_compra_servicios_fuv_id.total
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.total_relacionado = record.relacion_orden_compra_bienes_fuv_id.total
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.total_relacionado = record.relacion_fuv_id.valor_total_contrato
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.total_relacionado = record.relacion_otrosi_fuv_id.valor_total_contrato
            else:
                record.total_relacionado = 0.0


    total_relacionado = fields.Monetary(
        string='Total Relacionado',
        compute='_compute_total_relacionado',
    )
    
    objeto_contrato = fields.Text(
        string='Objeto del contrato',
        tracking=True,
        compute='_compute_objeto_contrato'
    )

    @api.depends(
        'origen_cuenta', 'proyecto_regalias_id', 'contrato_regalias_id',
        'contrato_regalias_id.resumen_objeto_contractual',
        'selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id'
    )
    def _compute_objeto_contrato(self):
        for record in self:
            if record.origen_cuenta == 'regalias':
                contrato_regalias = record.contrato_regalias_id
                if not contrato_regalias:
                    contrato_regalias = record._buscar_contrato_regalias_automatico(
                        record.proyecto_regalias_id,
                        record.documento_de_identificacion
                        or (record.login_id.documento_de_identidad if record.login_id else ''),
                        contratacion=record.proyecto_relacionado,
                    )
                record.objeto_contrato = (
                    contrato_regalias.resumen_objeto_contractual
                    or record.proyecto_regalias_id.objeto_proyecto
                    or record.proyecto_regalias_id.nombre_proceso_proyecto
                )
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.objeto_contrato = record.relacion_fuv_id.objeto_contrato
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.objeto_contrato = record.relacion_orden_compra_bienes_fuv_id.objeto_contractual
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.objeto_contrato = record.relacion_orden_compra_servicios_fuv_id.objeto_contractual
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.objeto_contrato = record.relacion_otrosi_fuv_id.objeto_contrato
            else:
                record.objeto_contrato = False

                
    def calcular_campos(self):
        self._compute_total_relacionado()
        self._compute_objeto_contrato()


    porcentaje_de_pago = fields.Float(
        string='Porcentaje de pago (%)',
        tracking=True,
        compute='_compute_porcentaje_de_pago_acumulado',
    )

    @api.depends(
        'origen_cuenta', 'proyecto_regalias_id', 'contrato_regalias_id', 'valor_cuenta', 'pago', 'pago_forma_pago',
        'contratista_id.porcentaje_de_pago',
        'relacion_fuv_id.tabla_de_forma_de_pago.porcentaje_acumulado',
        'relacion_fuv_id.tabla_de_forma_de_pago.cantidad_de_dinero',
        'relacion_fuv_id.tabla_de_forma_de_pago.numero_de_pagos',
        'relacion_otrosi_fuv_id.tabla_de_forma_de_pago.porcentaje_acumulado',
        'relacion_otrosi_fuv_id.tabla_de_forma_de_pago.cantidad_de_dinero',
        'relacion_otrosi_fuv_id.tabla_de_forma_de_pago.numero_de_pagos',
    )
    def _compute_porcentaje_de_pago_acumulado(self):
        """Calcula Regalías por proyecto sin alterar el acumulado histórico FUV."""
        for record in self:
            if record.origen_cuenta != 'regalias':
                linea = record._linea_desembolso_seleccionada()
                if (
                    linea
                    and linea.porcentaje_acumulado
                    and record._valor_cuenta_es_desembolso_completo(linea)
                ):
                    record.porcentaje_de_pago = min(
                        round(linea.porcentaje_acumulado, 2), 100.0
                    )
                    continue
                record.porcentaje_de_pago = (
                    record.contratista_id.porcentaje_de_pago or 0.0
                )
                continue

            total_proyecto = (
                record._parse_regalias_amount(record.contrato_regalias_id.valor_de_contrato_vigencia_2025_2026)
                if record.contrato_regalias_id else 0.0
            )
            if not record.proyecto_regalias_id or not record.contrato_regalias_id or not total_proyecto:
                record.porcentaje_de_pago = 0.0
                continue

            domain = [
                ('origen_cuenta', '=', 'regalias'),
                ('contrato_regalias_id', '=', record.contrato_regalias_id.id),
                ('pago', '<=', record.pago),
                ('contratista_id.estado', '!=', 'rechazado'),
            ]
            if record.id and not isinstance(record.id, models.NewId):
                domain.append(('id', '!=', record.id))

            pagos_anteriores = self.sudo().search(domain)
            total_pagado = sum(pagos_anteriores.mapped('valor_cuenta'))
            total_pagado += record.valor_cuenta or 0.0
            record.porcentaje_de_pago = min(
                round((total_pagado / total_proyecto) * 100.0, 6),
                100.0,
            )

    porcentaje_de_pago_individual = fields.Float(
        string='Porcentaje de pago individual',
        compute='_compute_porcentaje_de_pago_individual',
        digits=(16, 6),
    )

    @api.depends(
        'valor_cuenta', 'total_relacionado', 'pago', 'pago_forma_pago', 'selecionar',
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
                linea_pago = record._linea_desembolso_seleccionada()
                if linea_pago and record._valor_cuenta_es_desembolso_completo(linea_pago):
                    record.porcentaje_de_pago_individual = round(
                        linea_pago.pago or 0.0,
                        2,
                    )
                    continue

            total_respaldo = record.total_relacionado or 0.0
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

    ciudad_contratista = fields.Char(string='Ciudad de expedición', compute='_compute_ciudad_contratista')

    @api.depends(
        'origen_cuenta',
        'clase_persona',
        'login_id.contratistas.ciudad_exp_natural',
        'login_id.contratistas.ciudad_exp_representante',
        'selecionar',
        'relacion_fuv_id',
        'relacion_orden_compra_servicios_fuv_id',
        'relacion_orden_compra_bienes_fuv_id',
        'relacion_otrosi_fuv_id',
    )
    def _compute_ciudad_contratista(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.login_id.contratistas:
                proveedor = record.login_id.contratistas
                ciudad = (
                    proveedor.ciudad_exp_representante
                    if record.clase_persona == 'persona_juridica'
                    else proveedor.ciudad_exp_natural
                )
                record.ciudad_contratista = ciudad.name if ciudad else False
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.ciudad_contratista = record.relacion_fuv_id.ciudad_contratista
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.ciudad_contratista = record.relacion_orden_compra_bienes_fuv_id.ciudad
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.ciudad_contratista = record.relacion_orden_compra_servicios_fuv_id.ciudad
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.ciudad_contratista = record.relacion_otrosi_fuv_id.ciudad_contratista
            else:
                record.ciudad_contratista = False

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

    total_pago_porcentaje = fields.Float(string="Total % de Pago", related='relacion_fuv_id.total_pago_porcentaje')

    id_proyecto3 = fields.Char(string='No. del contrato', related='relacion_fuv_id.id_proyecto3')
    

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

    id_del_contrato = fields.Char(string='ID del contrato', related="relacion_fuv_id.id_del_contrato")
    proyecto_relacionado = fields.Many2one(
        'contratacion',
        string='Proyecto',
        compute='_compute_proyecto_relacionado',
        store=True,
    )
    mcb_centro_costo = fields.Char(string='Centro de costo', compute='_compute_d_mcb_centro_costo')
    numero_contrato = fields.Char(related='proyecto_relacionado.numero_contrato')

    nombre_y_numero_proyecto = fields.Char(
        string='Proyecto y numero',
        compute='_compute_nombre_y_numero_proyecto',

    )

    @api.depends('origen_cuenta', 'proyecto_regalias_id', 'contrato_regalias_id', 'proyecto_relacionado', 'numero_contrato')
    def _compute_nombre_y_numero_proyecto(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                nombre = record.proyecto_regalias_id.nombre_proceso_proyecto or ''
                contrato = record.contrato_regalias_id.numero_de_contrato or ''
                record.nombre_y_numero_proyecto = (
                    f"{nombre} ({contrato})" if nombre and contrato else nombre or contrato
                )
                continue
            # Usa new_name si existe, si no name
            if record.proyecto_relacionado:
                nombre = record.proyecto_relacionado.new_name or record.proyecto_relacionado.name
            else:
                nombre = ''
            
            numero = record.numero_contrato or ''
            
            if nombre and numero:
                record.nombre_y_numero_proyecto = f"{nombre} ({numero})"
            else:
                record.nombre_y_numero_proyecto = nombre or numero

    fecha_iniciacion = fields.Date(
        'Fecha de iniciación',
        tracking=True,
        compute='_compute_fecha_iniciacion'
    )
    @api.depends('origen_cuenta', 'proyecto_regalias_id', 'selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_fecha_iniciacion(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                record.fecha_iniciacion = record.proyecto_regalias_id.fecha_acta_inicio
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.fecha_iniciacion = record.relacion_fuv_id.fecha_iniciacion
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.fecha_iniciacion = record.relacion_orden_compra_servicios_fuv_id.fecha_inicio
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.fecha_iniciacion = record.relacion_orden_compra_bienes_fuv_id.fecha_inicio
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.fecha_iniciacion = record.relacion_otrosi_fuv_id.fecha_iniciacion
            else:
                record.fecha_iniciacion = False



    fecha_terminacion = fields.Date(
        'Fecha de terminación',
        tracking=True,
        compute='_compute_fecha_terminacion'
    )

    @api.depends('origen_cuenta', 'proyecto_regalias_id', 'selecionar', 'relacion_fuv_id', 'relacion_orden_compra_servicios_fuv_id', 'relacion_orden_compra_bienes_fuv_id', 'relacion_otrosi_fuv_id')
    def _compute_fecha_terminacion(self):
        for record in self:
            if record.origen_cuenta == 'regalias' and record.proyecto_regalias_id:
                record.fecha_terminacion = record.proyecto_regalias_id.fecha_acta_fin
            elif record.selecionar == 'contrato' and record.relacion_fuv_id:
                record.fecha_terminacion = record.relacion_fuv_id.fecha_terminacion
            elif record.selecionar == 'servicios' and record.relacion_orden_compra_servicios_fuv_id:
                record.fecha_terminacion = record.relacion_orden_compra_servicios_fuv_id.fecha_fin
            elif record.selecionar == 'bienes' and record.relacion_orden_compra_bienes_fuv_id:
                record.fecha_terminacion = record.relacion_orden_compra_bienes_fuv_id.fecha_fin
            elif record.selecionar == 'otrosi' and record.relacion_otrosi_fuv_id:
                record.fecha_terminacion = record.relacion_otrosi_fuv_id.fecha_terminacion
            else:
                record.fecha_terminacion = False
    
    @staticmethod
    def _regalias_key(value):
        return re.sub(r'[^0-9a-zA-Z]+', '', value or '').lower()

    def _regalias_centro_costo_proyecto(self, proyecto):
        if not proyecto:
            return ''

        proyecto = proyecto.sudo()
        return proyecto.id_proyecto if 'id_proyecto' in proyecto._fields else ''

    def _regalias_centro_costo_contratacion(self, contratacion):
        if not contratacion:
            return ''
        informacion_base = (
            contratacion.informacion_base_id
            or contratacion.informacion_base_agregar_ids[:1]
        )
        return informacion_base.id_proyecto if informacion_base else ''

    def _buscar_contrato_regalias_automatico(self, proyecto, identificacion, contratacion=False):
        identificacion_key = re.sub(r'\D+', '', identificacion or '')
        if not identificacion_key:
            return self.env['base.datos.regalias.contrato']

        Contrato = self.env['base.datos.regalias.contrato'].sudo()
        contratos_por_identificacion = Contrato.search([]).filtered(
            lambda contrato: re.sub(r'\D+', '', contrato.identificacion or '') == identificacion_key
        )
        centro_costo_key = self._regalias_key(
            self._regalias_centro_costo_proyecto(proyecto)
        )
        if centro_costo_key:
            contratos_por_centro = contratos_por_identificacion.filtered(
                lambda contrato: self._regalias_key(contrato.un) == centro_costo_key
            )
            if len(contratos_por_centro) == 1:
                return contratos_por_centro[:1]

        if len(contratos_por_identificacion) == 1:
            return contratos_por_identificacion[:1]
        return self.env['base.datos.regalias.contrato']

    def _completar_contrato_regalias_vals(self, vals, record=False):
        origen = vals.get('origen_cuenta') or (record.origen_cuenta if record else False)
        if origen != 'regalias':
            return vals

        # El contrato de una cuenta de Regalías no debe desaparecer porque
        # una vista oculta/deshabilitada envíe False o no envíe el campo. Si
        # ya existe una vinculación, una escritura vacía conserva el valor.
        # Un cambio explícito por otro contrato sigue siendo posible desde el
        # backend para una corrección administrativa deliberada.
        if (
            record
            and record.contrato_regalias_id
            and 'contrato_regalias_id' in vals
            and not vals.get('contrato_regalias_id')
        ):
            vals = dict(vals)
            vals['contrato_regalias_id'] = record.contrato_regalias_id.id
            return vals

        if vals.get('contrato_regalias_id'):
            return vals

        proyecto_id = vals.get('proyecto_regalias_id')
        proyecto = (
            self.env['informacion_base_agregar'].sudo().browse(proyecto_id).exists()
            if proyecto_id else (record.proyecto_regalias_id if record else False)
        )
        identificacion = (
            vals.get('documento_de_identificacion')
            or (record.documento_de_identificacion if record else '')
            or (record.login_id.documento_de_identidad if record and record.login_id else '')
        )
        contratacion = record.proyecto_relacionado if record else False
        contrato = self._buscar_contrato_regalias_automatico(proyecto, identificacion, contratacion=contratacion)
        if contrato:
            vals = dict(vals)
            vals['contrato_regalias_id'] = contrato.id
            valor_contrato = self._parse_regalias_amount(contrato.valor_de_contrato_vigencia_2025_2026)
            if valor_contrato and not vals.get('valor_total_contrato'):
                vals['valor_total_contrato'] = valor_contrato
        return vals


    
    def write(self, vals):
        # 1. Ejecutar el guardado estándar. Cuando solo cambian documentos/flags
        # automáticos, no llenamos el chatter con "No → Sí"; los mensajes
        # explícitos de Drive/corrección siguen publicándose aparte.
        write_self = self
        if len(self) == 1:
            vals = self._completar_contrato_regalias_vals(vals, record=self)
        if self._sgo_debe_silenciar_tracking_automatico(vals):
            write_self = self.with_context(mail_notrack=True, tracking_disable=True)
        res = super(CuentasDeCobros, write_self).write(vals)
        if self.env.context.get('skip_acta_sync_from_cuenta'):
            return res

        sync_acta_keys = [
            'documento_equivalente_doc',
            'factura_doc',
            'factura_doc_name',
            'factura_si_o_no',
            'numero_factura',
            'resolucion_facturacion_electronica_juridica_documento',
            'resolucion_facturacion_electronica_juridica_documento_name',
            'informes_contratista_doc',
            'informes_contratista_doc_name',
        ]

        # Solo se escriben en el acta campos reales de documentos.
        # Los datos contractuales/fechas son related hacia esta cuenta y no
        # deben re-escribirse porque activan inverse y recursión.
        if any(k in vals for k in sync_acta_keys):
            for record in self:
                # Buscar el acta asociada (usando el campo contratista_id o búsqueda por cuenta_id)
                acta_record = record.contratista_id or self.env['acta_entrega_documentos'].sudo().search([('cuenta_id', '=', record.id)], limit=1)

                if acta_record:
                    _logger.info(f"Actualizando Acta {acta_record.id} desde Write de Cuenta {record.id}")
                    
                    # Preparar los valores a actualizar en el Acta
                    acta_vals = {}
                    if 'factura_doc' in vals:
                        acta_vals['factura_documento'] = record.factura_doc
                    if 'factura_doc_name' in vals:
                        acta_vals['factura_name'] = record.factura_doc_name
                    if 'resolucion_facturacion_electronica_juridica_documento' in vals:
                        acta_vals['resolucion_facturacion_electronica_juridica_documento'] = record.resolucion_facturacion_electronica_juridica_documento
                    if 'resolucion_facturacion_electronica_juridica_documento_name' in vals:
                        acta_vals['resolucion_facturacion_electronica_juridica_name'] = record.resolucion_facturacion_electronica_juridica_documento_name
                    if 'informes_contratista_doc' in vals:
                        acta_vals['informes_contratista_documento'] = record.informes_contratista_doc
                    if 'informes_contratista_doc_name' in vals:
                        acta_vals['informes_contratista_name'] = record.informes_contratista_doc_name

                    # --- 2. APLICAR LOS CAMBIOS EN EL ACTA ---
                    if acta_vals:
                        acta_record.sudo().with_context(
                            allow_contractual_correction_write=True,
                            skip_cuenta_de_sync=True,
                        ).write(acta_vals)

                    # --- 3. DISPARAR LA NOTIFICACIÓN ---
                    # Llamamos directamente a la función que vive en el modelo del acta
                    try:
                        acta_record.sudo().notify_contract_assistant()
                        _logger.info(f"Intento de notificación ejecutado para el Acta {acta_record.id}")
                    except Exception as e:
                        _logger.error(f"Error al ejecutar la notificación en el Acta {acta_record.id}: {e}")

                    if record.factura_doc:
                        acta_record.sudo()._upload_factura_to_drive()

        if {'inicio_actividad', 'finalizacion_actividad'} & set(vals):
            for record in self:
                declaraciones = record.relacion_ids.sudo()
                for declaracion in declaraciones:
                    declaracion._sincronizar_periodo_desde_cuenta()
                    if declaracion.declaracion_juramentada_y_soportes_doc:
                        declaracion.with_context(
                            raise_document_generation_error=True,
                        ).generar_informe_xlsx()

        # La carpeta existente se identifica por su ID de Drive. Si durante
        # una corrección cambia un dato visible en su nombre, se renombra esa
        # misma carpeta sin crear otra ni mover los documentos que contiene.
        if {'pago', 'fecha_solicitud', 'selecionar'} & set(vals):
            for record in self:
                acta_record = record.contratista_id or self.env['acta_entrega_documentos'].sudo().search(
                    [('cuenta_id', '=', record.id)],
                    limit=1,
                )
                if acta_record:
                    acta_record.sudo()._actualizar_nombre_carpeta_cuenta_drive()

        return res

        
    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto', related='contratista_id.users_with_access', readonly=True)
    create_uid = fields.Many2one('res.users', string='Creador del proyecto', related='contratista_id.create_uid', readonly=True)
    creator_user_id = fields.Many2one('res.users', string='Creador del proyecto (relacionado)', related='contratista_id.creator_user_id', readonly=True)




    @api.model
    def _subir_orden_compra_generada_a_drive_cuenta(self, record, acta_record):
        """Genera/toma el DOCX de la orden de compra y lo deja en la cuenta de cobro."""
        if record.selecionar == 'servicios':
            orden = record.relacion_orden_compra_servicios_fuv_id
            etiqueta = 'Orden de Compra de Servicios'
            prefijo = '2_Orden de Compra de Servicios'
        elif record.selecionar == 'bienes':
            orden = record.relacion_orden_compra_bienes_fuv_id
            etiqueta = 'Orden de Compra de Bienes'
            prefijo = '2_Orden de Compra de Bienes'
        else:
            return False

        if not orden or not acta_record:
            return False

        try:
            if not orden.informe_adjunto:
                orden.sudo().generar_informe_docx()

            if not orden.informe_adjunto:
                _logger.warning("No se pudo generar el documento de %s para la cuenta %s.", etiqueta, record.id)
                return False

            stage_record = acta_record.drive_stage_provedor_id
            if not stage_record or not stage_record.folder_id:
                _logger.warning("La cuenta %s no tiene carpeta Drive para subir %s.", record.id, etiqueta)
                return False

            drive = GoogleDriveAuth.get_instance().get_drive()
            carpeta_drive = stage_record.folder_id
            fixed_file_name = f"{prefijo}_{uuid.uuid4()}.docx"
            file_content = base64.b64decode(orden.informe_adjunto)

            temp_file_path = False
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as temp_file:
                    temp_file.write(file_content)
                    temp_file_path = temp_file.name

                existing_record = self.env['drive'].sudo().search([
                    ('folder_id', '=', carpeta_drive),
                    ('file_name', 'like', f'{prefijo}_%')
                ], limit=1)

                file_metadata = {
                    'title': fixed_file_name,
                    'parents': [{'id': carpeta_drive}],
                    'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                }

                if existing_record and existing_record.file_id:
                    file_drive = drive.CreateFile({'id': existing_record.file_id})
                    file_drive.FetchMetadata()
                    file_drive.update(file_metadata)
                else:
                    file_drive = drive.CreateFile(file_metadata)

                file_drive.SetContentFile(temp_file_path)
                file_drive.Upload(param={'supportsAllDrives': True})

                file_id = file_drive['id']
                file_link = f"https://drive.google.com/file/d/{file_id}/preview"
                vals = {
                    'file_name': fixed_file_name,
                    'name': fixed_file_name,
                    'file_id': file_id,
                    'file_url': file_link,
                    'preview_url': file_link,
                    'edit_url': f"https://drive.google.com/file/d/{file_id}/view",
                    'preview_html': f'<iframe src="{file_link}" width="100%" height="600px" loading="lazy" frameborder="0" allowfullscreen></iframe>',
                    'edit_html': f'<iframe src="https://drive.google.com/file/d/{file_id}/view" width="100%" height="600px" frameborder="0"></iframe>',
                    'file_data': False,
                    'folder_id': carpeta_drive,
                    'stage4_id': stage_record.id,
                }

                if existing_record:
                    existing_record.write(vals)
                    drive_record = existing_record
                else:
                    drive_record = self.env['drive'].sudo().create(vals)

                acta_record.sudo().write({
                    'documento_equivalente_id': drive_record.id,
                    'documento_equivalente_visible': False,
                    'documento_equivalente': True,
                })
                acta_record.message_post(
                    body=f"✅ {etiqueta} generada por la plataforma y subida a Drive:<br><b>{fixed_file_name}</b>",
                    subtype_xmlid="mail.mt_note",
                )
                return drive_record
            finally:
                if temp_file_path and os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
        except Exception as e:
            _logger.error("Error al subir la orden de compra generada a Drive para la cuenta %s: %s", record.id, e)
            return False

    @api.model
    def create(self, vals):
        vals = self._completar_contrato_regalias_vals(vals)
        # 1. Crear el registro principal de Cuentas de Cobro
        record = super(CuentasDeCobros, self).create(vals)

        # --- LÓGICA DE LOGIN ---
        name_value = record.nombre_contratista
        _logger.info(f"Name del nuevo registro: {name_value}")

        if record.login_id:
            # login_id ya viene asignado (desde el portal), no sobreescribir
            _logger.info(f"login_id ya asignado: {record.login_id.id} ({record.login_id.coreo})")
        elif name_value:
            # Buscar en login si ya existe
            login_record = self.env['login'].sudo().search([('name', '=', name_value)], limit=1)

            if not login_record:
                # Crear en login si no existe
                login_record = self.env['login'].sudo().create({'name': name_value})
                _logger.info(f"Creado nuevo login con name={name_value} (ID={login_record.id})")

            # Escribir la relación en el registro recién creado
            record.login_id = login_record.id
            _logger.info(f"Asignado login_id={login_record.id} al registro CuentasDeCobros {record.id}")

        # --- LÓGICA DE ACTA DE ENTREGA Y GOOGLE DRIVE ---
        try:
            # Crear el acta con los campos vinculados
            acta_vals = {
                'origen_cuenta': record.origen_cuenta,
                'proyecto_regalias_id': record.proyecto_regalias_id.id if record.proyecto_regalias_id else False,
                'contrato_regalias_id': record.contrato_regalias_id.id if record.contrato_regalias_id else False,
                'name': record.name,
                'nombre_contratista': record.name,
                'numero_factura': record.numero_factura,
                'cuenta_id': record.id,
                'correo_facturacion': record.correo_facturacion,
                'documento_de_identificacion': record.documento_de_identificacion,
                'fecha_solicitud': record.fecha_solicitud,
                'clase_persona': record.clase_persona,
                'nombre_entrega': record.nombre_entrega,
                'valor_cuenta': record.valor_cuenta,
                'porcentaje_de_pago': record.porcentaje_de_pago,
                'relacion_fuv_id': record.relacion_fuv_id.id if record.relacion_fuv_id else False,
                'relacion_orden_compra_servicios_fuv_id': record.relacion_orden_compra_servicios_fuv_id.id if record.relacion_orden_compra_servicios_fuv_id else False,
                'relacion_orden_compra_bienes_fuv_id': record.relacion_orden_compra_bienes_fuv_id.id if record.relacion_orden_compra_bienes_fuv_id else False,
                'relacion_otrosi_fuv_id': record.relacion_otrosi_fuv_id.id if record.relacion_otrosi_fuv_id else False,
                'pago': record.pago,
                'de': record.de,
                'factura_electronica_juridica_doc': record.factura_electronica_juridica_documento_doc,
                'factura_electronica_juridica_doc_name': record.factura_electronica_juridica_documento_doc_name,
                'rut_actualizado_juridica_documento': record.rut_actualizado_juridica_documento,
                'rut_actualizado_juridica_name': record.rut_actualizado_juridica_documento_name,
                'resolucion_facturacion_electronica_juridica_documento': record.resolucion_facturacion_electronica_juridica_documento,
                'resolucion_facturacion_electronica_juridica_name': record.resolucion_facturacion_electronica_juridica_documento_name,
                'camara_comercio_juridica_documento': record.camara_comercio_juridica_documento,
                'camara_comercio_juridica_name': record.camara_comercio_juridica_documento_name,
                'rut_representante_legal_juridica_documento': record.rut_representante_legal_juridica_documento,
                'rut_representante_legal_juridica_name': record.rut_representante_legal_juridica_documento_name,
                'certificacion_bancaria_juridica_documento': record.certificacion_bancaria_juridica_documento,
                'certificacion_bancaria_juridica_name': record.certificacion_bancaria_juridica_documento_name,
                'certificado_pago_parafiscales_juridica_documento': record.certificado_pago_parafiscales_juridica_documento,
                'certificado_pago_parafiscales_juridica_name': record.certificado_pago_parafiscales_juridica_documento_name,
                'tarjeta_profesional_revisor_fiscal_juridica_documento': record.tarjeta_profesional_revisor_fiscal_juridica_documento,
                'tarjeta_profesional_revisor_fiscal_juridica_name': record.tarjeta_profesional_revisor_fiscal_juridica_documento_name,
                'cedula_de_contador_juridica_documento': record.cedula_de_contador_juridica_documento,
                'cedula_de_contador_juridica_name': record.cedula_de_contador_juridica_documento_name,
                'cedula_representante_legal_juridica_documento': record.cedula_representante_legal_juridica_documento,
                'cedula_representante_legal_juridica_name': record.cedula_representante_legal_juridica_documento_name,
                'certificado_junta_contadores_juridica_documento': record.certificado_junta_contadores_juridica_documento,
                'certificado_junta_contadores_juridica_name': record.certificado_junta_contadores_juridica_documento_name,
                'Tarjeta_profesional_contador_juridica_name': record.tarjeta_profesional_Contador_juridica_doc_name,
                'Tarjeta_profesional_contador_juridica_documento': record.tarjeta_profesional_Contador_juridica_doc,
                'cedula_revisor_fiscal_juridica_documento': record.cedula_revisor_fiscal_juridica_doc,
                'cedula_revisor_fiscal_juridica_name': record.cedula_revisor_fiscal_juridica_doc_name,
                'documento_equivalente_documento': record.documento_equivalente_doc,
                'documento_equivalente_name': record.documento_equivalente_doc_name,
                'informe_de_actividades_documento': record.informe_de_actividades_doc,
                'informe_de_actividades_name': record.informe_de_actividades_doc_name,
                'rut_actualizado_documento': record.rut_actualizado_doc,
                'rut_actualizado_name': record.rut_actualizado_doc_name,
                'planilla_de_aportes_a_seguridad_social_documento': record.planilla_de_aportes_a_seguridad_social_doc,
                'planilla_de_aportes_a_seguridad_social_name': record.planilla_de_aportes_a_seguridad_social_doc_name,
                'copia_cedula_documento': record.copia_cedula_doc,
                'copia_cedula_name': record.copia_cedula_doc_name,
                'certificacion_bancaria_documento': record.certificacion_bancaria_doc,
                'certificacion_bancaria_name': record.certificacion_bancaria_doc_name,
                'resolucion_facturacion_electronica_documento': record.resolucion_facturacion_electronica_doc,
                'resolucion_facturacion_electronica_name': record.resolucion_facturacion_electronica_doc_name,
                'certificado_de_expensa_documento': record.certificado_de_expensa_doc,
                'certificado_de_expensa_name': record.certificado_de_expensa_doc_name,
                'planilla_de_aportes_a_seguridad_social_ju_documento': record.factura_electronica_juridica_documento_doc,
                'planilla_de_aportes_a_seguridad_social_ju_name': record.planilla_de_seguridad_social_ju_doc_name,
                'factura_documento': record.factura_doc,
                'factura_name': record.factura_doc_name,
                'otro_documentos_de_informacion_documento': record.otro_documentos_de_inf_doc,
                'otro_documentos_de_informacion_name': record.otro_documentos_de_inf_doc_name,
                'informes_contratista_documento': record.informes_contratista_doc,
                'informes_contratista_name': record.informes_contratista_doc_name,
            }
            if self.env.context.get('portal_chatter_login_id'):
                acta_vals.update({
                    'bloqueo': True,
                    'notificacion_enviada': False,
                })
            if record.origen_cuenta == 'regalias':
                acta_vals.update({
                    'copia_cedula_documento': False,
                    'copia_cedula_name': False,
                    'copia_contrato_documento': False,
                    'copia_contrato_name': False,
                    'regalias_copia_cedula_documento': record.copia_cedula_doc,
                    'regalias_copia_cedula_name': record.copia_cedula_doc_name,
                    'regalias_copia_contrato_documento': record.copia_contrato_doc,
                    'regalias_copia_contrato_name': record.copia_contrato_doc_name,
                })

            acta_record = self.env['acta_entrega_documentos'].sudo().create(acta_vals)

            # Asignar el acta creada al campo contratista_id en cuenta
            record.write({'contratista_id': acta_record.id})
            # acta_record.notify_contract_assistant()

            # --- SUBIDAS A GOOGLE DRIVE ---
            if acta_record.factura_electronica_juridica_doc:
                acta_record._upload_factura_electronica_to_drive()
            if acta_record.rut_actualizado_juridica_documento:
                acta_record._upload_rut_actualizado_juridica_to_drive()
            if acta_record.resolucion_facturacion_electronica_juridica_documento:
                acta_record._upload_resolucion_facturacion_electronica_juridica_to_drive()
            if acta_record.certificacion_bancaria_juridica_documento:
                acta_record._upload_certificacion_bancaria_juridica_to_drive()
            if acta_record.camara_comercio_juridica_documento:
                acta_record._upload_camara_comercio_juridica_to_drive()
            if acta_record.rut_representante_legal_juridica_documento:
                acta_record._upload_rut_representante_legal_juridica_to_drive()
            if acta_record.certificado_pago_parafiscales_juridica_documento:
                acta_record._upload_certificado_pago_parafiscales_juridica_to_drive()
            if acta_record.tarjeta_profesional_revisor_fiscal_juridica_documento:
                acta_record._upload_tarjeta_profesional_revisor_fiscal_juridica_to_drive()
            if acta_record.Tarjeta_profesional_contador_juridica_documento:
                acta_record._upload_Tarjeta_profesional_contador_juridica_to_drive()
            if acta_record.cedula_revisor_fiscal_juridica_documento:
                acta_record._upload_cedula_revisor_fiscal_juridica_to_drive()
            if acta_record.cedula_de_contador_juridica_documento:
                acta_record._upload_cedula_de_contador_juridica_to_drive()
            if acta_record.cedula_representante_legal_juridica_documento:
                acta_record._upload_cedula_representante_legal_juridica_to_drive()
            if acta_record.certificado_junta_contadores_juridica_documento:
                acta_record._upload_certificado_junta_contadores_juridica_to_drive()
            if acta_record.planilla_de_aportes_a_seguridad_social_ju_documento:
                acta_record._upload_planilla_de_aportes_a_seguridad_social_ju_to_drive()
            if acta_record.documento_equivalente_documento:
                acta_record._upload_documento_equivalente_to_drive()
            if acta_record.informe_de_actividades_documento:
                acta_record._upload_informe_de_actividades_to_drive()
            if acta_record.rut_actualizado_documento:
                acta_record._upload_rut_actualizado_to_drive()
            if acta_record.certificacion_bancaria_documento:
                acta_record._upload_certificacion_bancaria_to_drive()
            if acta_record.planilla_de_aportes_a_seguridad_social_documento:
                acta_record._upload_planilla_de_aportes_a_seguridad_social_to_drive()
            if acta_record.copia_contrato_documento:
                acta_record._upload_copia_contrato_to_drive()
            if acta_record.copia_cedula_documento:
                acta_record._upload_copia_cedula_to_drive()
            if acta_record.regalias_copia_contrato_documento:
                acta_record._upload_regalias_copia_contrato_to_drive()
            if acta_record.regalias_copia_cedula_documento:
                acta_record._upload_regalias_copia_cedula_to_drive()
            if acta_record.certificado_de_expensa_documento:
                acta_record._upload_certificado_de_expensa_to_drive()
            if acta_record.resolucion_facturacion_electronica_documento:
                acta_record._upload_resolucion_facturacion_electronica_to_drive()
            if acta_record.factura_documento:
                acta_record._upload_factura_to_drive()
            if acta_record.otro_documentos_de_informacion_documento:
                acta_record._upload_otro_documentos_de_informacion_to_drive()
            if acta_record.informes_contratista_documento:
                acta_record._upload_informes_contratista_to_drive()

            if record.selecionar in ('servicios', 'bienes'):
                self._subir_orden_compra_generada_a_drive_cuenta(record, acta_record)


        except Exception as e:
            _logger.error(f"Error al crear Acta de Entrega desde Cuenta de Cobro: {e}")
            raise

        # --- ÚLTIMO PASO: CREAR DECLARACIÓN JURAMENTADA ---
        # --- PASOS FINALES: SOLO PARA PERSONA NATURAL ---
        if record.clase_persona == 'persona_natural':
            
            # 1. CREAR INFORME DE ACTIVIDADES
            try:
                self.env['informe_actividades'].sudo().create({
                    'name': record.nombre_contratista or record.name,
                    'relacion_id': acta_record.id,
                    'cuenta_cobro_id': record.id,
                    # Las actividades del informe las diligencia el contratista.
                    'descripcion_actividades': '',
                })
                _logger.info(f"Informe de Actividades creado para: {record.nombre_contratista}")
            except Exception as e:
                _logger.error(f"Error al crear Informe de Actividades: {e}")

            # 2. CREAR DECLARACIÓN JURAMENTADA
            # También aplica a personas naturales vinculadas a órdenes de
            # servicios o bienes. Si existe factura, se crea al abrir el
            # formulario público para generar únicamente la declaración.
            if not record.factura_si_o_no:
                try:
                    self.env['declaracion_juramentada'].sudo().create({
                        'name': record.nombre_contratista or record.name,
                        'relacion': record.id,
                        'nombre_apellidos': record.nombre_contratista,
                    })
                    _logger.info(f"Declaración Juramentada creada para: {record.nombre_contratista}")
                except Exception as e:
                    _logger.error(f"Error al crear Declaración Juramentada: {e}")
            else:
                _logger.info(
                    "La declaración de %s se creará al abrir el formulario porque la cuenta registra factura.",
                    record.nombre_contratista,
                )
        else:
            _logger.info(f"Saltando Informe y Declaración: El contratista {record.nombre_contratista} es {record.clase_persona}")

        return record


    contratista_id = fields.Many2one(
        comodel_name='acta_entrega_documentos',
        string='Contratista',
    )

    actividades_especificas = fields.Text('Actividades específicas', related='contratista_id.actividades_especificas')

    clase_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('seleccion_manual', 'Seleccione'),  # Asegúrate de que este valor esté correctamente definido
    ], string='Tipo', required=False, tracking=True)

    

    partner_id = fields.Many2one('res.partner', string='Usuario Externo')
    token = fields.Char(string="Token")
    fecha_solicitud = fields.Date(string="Fecha de Solicitud", tracking=True)

    def _generate_token(self):
        return str(uuid.uuid4())

    def send_access_email(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        for record in self:
            if not record.correo_facturacion:
                _logger.error(f"Correo electrónico no encontrado para el registro {record.id}")
                continue

            if not record.token:
                record.token = record._generate_token()

            public_url = f"{base_url}/public/cuentas_de_cobros/{record.id}/{record.token}"

            subject = "Acceso a cuenta de cobro"
            body = f"""
            <p>Estimado/a,</p>
            <p>Puede acceder a la cuenta de cobro haciendo clic en el siguiente enlace:</p>
            <p><a href="{public_url}">Ver cuenta de cobro</a></p>
            <p>Gracias,</p>
            <p>SGO</p>
            """

            mail_values = {
                'subject': subject,
                'body_html': body,
                'email_to': record.correo_facturacion,
                'email_from': self.env.user.email or 'no-reply@miempresa.com',
            }

            mail = self.env['mail.mail'].sudo().create(mail_values)
            try:
                mail.sudo().send()
                _logger.info(f"Correo enviado a {record.correo_facturacion}.")
            except Exception as e:
                _logger.error(f"Error al enviar correo: {str(e)}")

    correo_facturacion = fields.Char(string="Correo electrónico", tracking=True, related='login_id.coreo')
    name = fields.Char(string='Nombre', tracking=True, related='login_id.name')
    login_id = fields.Many2one('login', string='Login')
    # Datos generales
    nombre_contratista = fields.Char(string='Nombre del contratista', tracking=True)
    documento_de_identificacion = fields.Char(string="Documento de identificación", related='login_id.documento_de_identidad', tracking=True)
    numero_factura = fields.Char(string='Número de factura', default='N/A', tracking=True)
    nombre_entrega = fields.Char(string='Nombre de quien entrega', tracking=True)
    pago = fields.Integer(string='Pago N°', tracking=True)
    pago_forma_pago = fields.Integer(
        string='Desembolso de origen',
        help='Fila de la forma de pago que aportó el valor de la cuenta. El Pago N° visible puede editarse libremente.',
        copy=False,
    )
    de = fields.Integer(string='de', tracking=True)

    

    fch_creacion = fields.Datetime(string='Fecha creacion', tracking=True, readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())
    # Información del contrato

    currency_id = fields.Many2one('res.currency', string='Moneda')
    # Documentación anexa persona natural

    certificacion_conjunta = fields.Boolean(string='Certificación Conjunta', default=False, tracking=True)
    certificacion_conjunta_doc = fields.Binary(string='Certificación Conjunta', tracking=True)
    certificacion_conjunta_doc_name = fields.Char(string='Certificación Conjunta', tracking=True)

    factura = fields.Boolean(string='Factura', default=False, tracking=True)
    factura_si_o_no = fields.Boolean(string='Si o No', tracking=True)
    factura_doc = fields.Binary(string='Factura', tracking=True)
    factura_doc_name = fields.Char(string='Factura', tracking=True)
    factura_corregir = fields.Boolean(
        string='Factura',
        tracking=True,
        related='contratista_id.factura_corregir',
    )

    factura_id = fields.Many2one('drive', string='Factura', tracking=True, related='contratista_id.factura_id')
    soporte_factura_id = fields.Many2one(
        'drive',
        related='contratista_id.factura_id',
        string='Soporte factura Drive',
        readonly=False,
    )

    def action_open_factura_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Factura',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.factura_id.id,
            'target': 'new',
        }
    
    otro_documentos_de_inf = fields.Boolean(string='Documento adicional', default=False, tracking=True)
    otro_documentos_de_inf_doc = fields.Binary(string='Documento adicional', tracking=True)
    otro_documentos_de_inf_doc_name = fields.Char(string='Documento adicional', tracking=True)
    otro_documentos_de_inf_corregir = fields.Boolean(string='Documento adicional', default=False, tracking=True, related='contratista_id.otro_documentos_de_inf_corregir')  

    otro_documentos_de_inf_id = fields.Many2one('drive', string='Documento adicional', tracking=True, related='contratista_id.otro_documentos_de_inf_id')
    cesion_derechos_corregir = fields.Boolean(
        string='Cesión de derechos',
        default=False,
        tracking=True,
        related='contratista_id.cesion_derechos_corregir',
    )
    cesion_derechos_id = fields.Many2one(
        'drive',
        string='Cesión de derechos',
        tracking=True,
        related='contratista_id.cesion_derechos_id',
    )
    soporte_otro_documentos_de_inf_id = fields.Many2one(
        'drive',
        related='contratista_id.otro_documentos_de_inf_id',
        string='Soporte documento adicional Drive',
        readonly=False,
    )

    informes_contratista_doc = fields.Binary(string='Informes del contratista', tracking=True)
    informes_contratista_doc_name = fields.Char(string='Informes del contratista', tracking=True)
    informes_contratista_corregir = fields.Boolean(
        string='Informes del contratista',
        default=False,
        tracking=True,
        related='contratista_id.informes_contratista_corregir',
    )
    informes_contratista_id = fields.Many2one(
        'drive', string='Informes del contratista', tracking=True,
        related='contratista_id.informes_contratista_id'
    )

    def action_open_informes_contratista_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Informes del contratista',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.informes_contratista_id.id,
            'target': 'new',
            'context': {'force_drive_readonly': True},
        }

    opcion_otro_documento = fields.Boolean(string='Habilitar documento adicional', default=False, tracking=True)

    def action_open_otro_documentos_de_inf_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documentos adicionales',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.otro_documentos_de_inf_id.id,
            'target': 'new',
        }





    resolucion_facturacion_electronica = fields.Boolean(string='Resolución de Facturación Electrónica', default=False, tracking=True)
    resolucion_facturacion_electronica_doc = fields.Binary(string='Resolución de Facturación Electrónica', tracking=True)
    resolucion_facturacion_electronica_doc_name = fields.Char(string='Resolución de Facturación Electrónica', tracking=True)
    resolucion_facturacion_electronica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.resolucion_facturacion_electronica_corregir')

    resolucion_facturacion_electronica_id = fields.Many2one('drive', string='Resolución de Facturación Electrónica', tracking=True, related='contratista_id.resolucion_facturacion_electronica_id')
    soporte_resolucion_facturacion_electronica_id = fields.Many2one(
        'drive',
        related='contratista_id.resolucion_facturacion_electronica_2_id',
        string='Soporte resolución facturación Drive',
        readonly=False,
    )

    def action_open_resolucion_facturacion_electronica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento Equivalente',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.resolucion_facturacion_electronica_id.id,
            'target': 'new',
        }


    documento_equivalente = fields.Boolean(string='Documento equivalente', default=False, tracking=True, related='contratista_id.documento_equivalente_visible')
    documento_equivalente_doc = fields.Binary(string='Documento equivalente', tracking=True, related='relacion_ids.declaracion_juramentada_y_soportes_doc')
    documento_equivalente_doc_name = fields.Char(string='Documento equivalente', tracking=True, related='relacion_ids.declaracion_juramentada_y_soportes_doc_name')
    documento_equivalente_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.documento_equivalente_corregir')

    documento_equivalente_id = fields.Many2one('drive', string='Documento equivalente', tracking=True, related='contratista_id.documento_equivalente_id')
    soporte_documento_equivalente_id = fields.Many2one(
        'drive',
        related='contratista_id.documento_equivalente_id',
        string='Soporte documento equivalente Drive',
        readonly=False,
    )

    def action_open_documento_equivalente_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento Equivalente',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.documento_equivalente_id.id,
            'target': 'new',
        }

    informe_de_actividades = fields.Boolean(string='Informe de actividades', default=False, tracking=True, related='contratista_id.informe_de_actividades_visible')
    informe_de_actividades_doc = fields.Binary(string='Informe de actividades', tracking=True, related='relacion_informe_ids.informe_de_actividades_doc')
    informe_de_actividades_doc_name = fields.Char(string='Informe de actividades', tracking=True, related='relacion_informe_ids.informe_de_actividades_doc_name')
    informe_de_actividades_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.informe_de_actividades_corregir')


    informe_de_actividades_id = fields.Many2one('drive', string='Informe de actividades', tracking=True, related='contratista_id.informe_de_actividades_id')
    soporte_informe_de_actividades_id = fields.Many2one(
        'drive',
        related='contratista_id.informe_de_actividades_id',
        string='Soporte informe actividades Drive',
        readonly=False,
    )

    def action_open_informe_de_actividades_drive(self):
        self.ensure_one()
        if self.contratista_id:
            self.contratista_id.sudo()._refresh_informe_de_actividades_drive_from_template()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Informe de Actividades',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.informe_de_actividades_id.id,
            'target': 'new',
        }


    declaracion_juramentada_y_soportes = fields.Boolean(string='Declaración juramentada y soportes', default=False, tracking=True)
    declaracion_juramentada_y_soportes_doc = fields.Binary(string='Declaración juramentada y soportes', tracking=True)
    declaracion_juramentada_y_soportes_doc_name = fields.Char(string='Declaración juramentada y soportes', tracking=True)
    declaracion_juramentada_y_soportes_id = fields.Many2one('drive', string='Declaración juramentada y soportes', tracking=True, related='contratista_id.declaracion_juramentada_y_soportes_id')

    declaracion_juramentada_y_soportes_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.declaracion_juramentada_y_soportes_corregir')

    def action_open_declaracion_juramentada_y_soportes_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Declaración Juramentada y Soportes',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.declaracion_juramentada_y_soportes_id.id,
            'target': 'new',
        }

    rut_actualizado = fields.Boolean(string='RUT actualizado', default=False, tracking=True, related='contratista_id.rut_actualizado_visible')
    rut_actualizado_doc = fields.Binary(string='RUT actualizado', tracking=True)
    rut_actualizado_doc_name = fields.Char(string='RUT actualizado', tracking=True)
    rut_actualizado_id = fields.Many2one('drive', string='RUT actualizado', tracking=True, related='contratista_id.rut_actualizado_id')

    rut_actualizado_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.rut_actualizado_corregir')

    def action_open_rut_actualizado_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT Actualizado',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_actualizado_id.id,
            'target': 'new',
        }

    anexo_tributario = fields.Boolean(string='Anexo tributario', default=False, tracking=True)
    anexo_tributario_doc = fields.Binary(string='Anexo tributario', tracking=True)
    anexo_tributario_doc_name = fields.Char(string='Anexo tributario', tracking=True)



    planilla_de_aportes_a_seguridad_social = fields.Boolean(string='Planilla de aportes a seguridad social', default=False, tracking=True, related='contratista_id.planilla_de_aportes_a_seguridad_social_visible')
    planilla_de_aportes_a_seguridad_social_doc = fields.Binary(string='Planilla de aportes a seguridad social', tracking=True)
    planilla_de_aportes_a_seguridad_social_doc_name = fields.Char(string='Planilla de aportes a seguridad social', tracking=True)
    planilla_de_aportes_a_seguridad_social_id = fields.Many2one('drive', string='Planilla de aportes a seguridad social', tracking=True, related='contratista_id.planilla_de_aportes_a_seguridad_social_id')
    planilla_de_aportes_a_seguridad_social_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.planilla_de_aportes_a_seguridad_social_corregir')

    def action_open_planilla_de_aportes_a_seguridad_social_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planilla de Aportes a Seguridad Social',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.planilla_de_aportes_a_seguridad_social_id.id,
            'target': 'new',
        }

    certificado_de_expensa = fields.Boolean(string='Certificado de expensas (si aplica)', default=False, tracking=True, related='contratista_id.certificado_de_expensa_visible')
    certificado_de_expensa_doc = fields.Binary(string='Certificado de expensas (si aplica)', tracking=True)
    certificado_de_expensa_doc_name = fields.Char(string='Certificado de expensas (si aplica)', tracking=True)
    certificado_de_expensa_id = fields.Many2one('drive', string='Certificado de expensas (si aplica)', tracking=True, related='contratista_id.certificado_de_expensa_id')
    certificado_de_expensa_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.certificado_de_expensa_corregir')

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


    # Primera cuenta
    copia_contrato = fields.Boolean(string='Copia contrato', default=False, tracking=True)
    copia_contrato_doc = fields.Binary(string='Copia contrato', tracking=True)
    copia_contrato_doc_name = fields.Char(string='Copia contrato', tracking=True)

    copia_cedula = fields.Boolean(string='Copia cédula', default=False, tracking=True)
    copia_cedula_doc = fields.Binary(string='Copia cédula', tracking=True)
    copia_cedula_doc_name = fields.Char(string='Copia cédula', tracking=True)
    copia_cedula_id = fields.Many2one('drive', string='Copia cédula', tracking=True, related='contratista_id.copia_cedula_id')

    copia_cedula_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.copia_cedula_corregir')

    def action_open_copia_cedula_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Copia Cédula',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.copia_cedula_id.id,
            'target': 'new',
        }

    certificacion_bancaria = fields.Boolean(string='Certificación Bancaria (vigente)', default=False, tracking=True)
    certificacion_bancaria_doc = fields.Binary(string='Certificación Bancaria (vigente)', tracking=True)
    certificacion_bancaria_doc_name = fields.Char(string='Certificación Bancaria (vigente)', tracking=True)
    certificacion_bancaria_id = fields.Many2one('drive', string='Certificación Bancaria (vigente)', tracking=True, related='contratista_id.certificacion_bancaria_2_id')

    certificacion_bancaria_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.certificacion_bancaria_corregir')

    def action_open_certificacion_bancaria_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación Bancaria',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificacion_bancaria_id.id,
            'target': 'new',
        }

    # Una sola vez
    registro_de_proveedores = fields.Boolean(string='RPC', default=False, tracking=True)
    registro_de_proveedores_doc = fields.Binary(string='RPC', tracking=True)
    registro_de_proveedores_doc_name = fields.Char(string='RPC', tracking=True)


    # Documentación anexa persona juridica
    planilla_de_seguridad_social_ju = fields.Boolean(string='Planilla de seguridad social', default=False, tracking=True)
    planilla_de_seguridad_social_ju_doc = fields.Binary(string='Planilla de seguridad social', tracking=True)
    planilla_de_seguridad_social_ju_doc_name = fields.Char(string='Planilla de seguridad social', tracking=True)
    planilla_de_seguridad_social_ju_id = fields.Many2one('drive', string='Planilla de seguridad social', tracking=True, related='contratista_id.planilla_de_aportes_a_seguridad_social_ju_id')
    planilla_de_seguridad_social_ju_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.planilla_de_aportes_a_seguridad_social_ju_corregir')

    def action_open_planilla_de_seguridad_social_ju_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Planilla de aportes a seguridad social',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.planilla_de_seguridad_social_ju_id.id,
            'target': 'new',
        }


    factura_electronica_juridica = fields.Boolean(string='Factura electrónica', default=False, tracking=True)
    factura_electronica_juridica_documento_doc = fields.Binary(string='Factura electrónica', tracking=True)
    factura_electronica_juridica_documento_doc_name  = fields.Char(string='Factura electrónica', tracking=True)
    factura_electronica_juridica_id = fields.Many2one('drive', string='Factura electrónica', tracking=True, related='contratista_id.factura_electronica_juridica_id')

    factura_electronica_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.factura_electronica_juridica_corregir')

    def action_open_factura_electronica_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Factura Electrónica Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.factura_electronica_juridica_id.id,
            'target': 'new',
        }



    rut_actualizado_juridica = fields.Boolean(string='RUT (Actualizado)', default=False, tracking=True)
    rut_actualizado_juridica_documento = fields.Binary(string='RUT (Actualizado)', tracking=True)
    rut_actualizado_juridica_documento_name = fields.Char(string='RUT (Actualizado)', tracking=True)
    rut_actualizado_juridica_id = fields.Many2one('drive', string='RUT (Actualizado)', tracking=True, related='contratista_id.rut_actualizado_juridica_id')

    rut_actualizado_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.rut_actualizado_juridica_corregir')

    def action_open_rut_actualizado_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT Actualizado Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_actualizado_juridica_id.id,
            'target': 'new',
        }

    resolucion_facturacion_electronica_juridica = fields.Boolean(string='Resolución de Facturación Electrónica', default=False, tracking=True)
    resolucion_facturacion_electronica_juridica_documento = fields.Binary(string='Resolución de Facturación Electrónica', tracking=True)
    resolucion_facturacion_electronica_juridica_documento_name = fields.Char(string='Resolución de Facturación Electrónica', tracking=True)
    resolucion_facturacion_electronica_juridica_id = fields.Many2one('drive', string='Resolución de Facturación Electrónica', tracking=True, related='contratista_id.resolucion_facturacion_electronica_juridica_id')
    resolucion_facturacion_electronica_juridica_2_id = fields.Many2one(
        'drive',
        string='Resolución de Facturación Electrónica (copia)',
        related='contratista_id.resolucion_facturacion_electronica_juridica_2_id',
        readonly=True,
    )

    resolucion_facturacion_electronica_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.resolucion_facturacion_electronica_juridica_corregir')

    def action_open_resolucion_facturacion_electronica_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Resolución de Facturación Electrónica Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.resolucion_facturacion_electronica_juridica_id.id,
            'target': 'new',
        }

    camara_comercio_juridica = fields.Boolean(string='Cámara de comercio (no mayor a 30 días)', default=False, tracking=True)
    camara_comercio_juridica_documento = fields.Binary(string='Cámara de comercio (no mayor a 30 días)', tracking=True)
    camara_comercio_juridica_documento_name = fields.Char(string='Cámara de comercio (no mayor a 30 días)', tracking=True)
    camara_comercio_juridica_id = fields.Many2one('drive', string='Cámara de comercio (no mayor a 30 días)', tracking=True, related='contratista_id.camara_comercio_juridica_id')

    camara_comercio_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.camara_comercio_juridica_corregir')

    def action_open_camara_comercio_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cámara de Comercio Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.camara_comercio_juridica_id.id,
            'target': 'new',
        }

    certificado_pago_parafiscales_juridica = fields.Boolean(string='Certificado de Pago Parafiscales', default=False, tracking=True)
    certificado_pago_parafiscales_juridica_documento = fields.Binary(string='Certificado de Pago Parafiscales', tracking=True)
    certificado_pago_parafiscales_juridica_documento_name = fields.Char(string='Certificado de Pago Parafiscales', tracking=True)
    certificado_pago_parafiscales_juridica_id = fields.Many2one('drive', string='Certificado de Pago Parafiscales', tracking=True, related='contratista_id.certificado_pago_parafiscales_juridica_id')

    certificado_pago_parafiscales_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.certificado_pago_parafiscales_juridica_corregir')

    def action_open_certificado_pago_parafiscales_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado de Pago Parafiscales Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_pago_parafiscales_juridica_id.id,
            'target': 'new',
        }

    tarjeta_profesional_revisor_fiscal_juridica = fields.Boolean(string='Tarjeta profesional revisor fiscal (Si aplica)', default=False, tracking=True)
    tarjeta_profesional_revisor_fiscal_juridica_documento = fields.Binary(string='Tarjeta profesional revisor fiscal (Si aplica)', tracking=True)
    tarjeta_profesional_revisor_fiscal_juridica_documento_name = fields.Char(string='Tarjeta profesional revisor fiscal (Si aplica)', tracking=True)
    tarjeta_profesional_revisor_fiscal_juridica_id = fields.Many2one('drive', string='Tarjeta profesional revisor fiscal (Si aplica)', tracking=True, related='contratista_id.tarjeta_profesional_revisor_fiscal_juridica_id')

    tarjeta_profesional_revisor_fiscal_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.tarjeta_profesional_revisor_fiscal_juridica_corregir')

    def action_open_tarjeta_profesional_revisor_fiscal_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarjeta profesional revisor fiscal (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.tarjeta_profesional_revisor_fiscal_juridica_id.id,
            'target': 'new',
        }

    cedula_representante_legal_juridica = fields.Boolean(string='Cedula del Representante legal, revisor fiscal (si aplica)', default=False, tracking=True)
    cedula_representante_legal_juridica_documento = fields.Binary(string='Cedula del Representante legal, revisor fiscal (si aplica)', tracking=True)
    cedula_representante_legal_juridica_documento_name = fields.Char(string='Cedula del Representante legal, revisor fiscal (si aplica)', tracking=True)
    cedula_representante_legal_juridica_id = fields.Many2one('drive', string='Cedula del Representante legal, revisor fiscal (si aplica)', tracking=True, related='contratista_id.cedula_representante_legal_juridica_id')

    cedula_representante_legal_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.cedula_representante_legal_juridica_corregir')

    def action_open_cedula_representante_legal_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cedula del Representante legal, revisor fiscal (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_representante_legal_juridica_id.id,
            'target': 'new',
        }

    


    cedula_de_contador_juridica = fields.Boolean(string='Documento de identidad Contador (Si aplica)', default=False, tracking=True)
    cedula_de_contador_juridica_documento = fields.Binary(string='Documento de identidad Contador (Si aplica)', tracking=True)
    cedula_de_contador_juridica_documento_name = fields.Char(string='Documento de identidad Contador (Si aplica)', tracking=True)
    cedula_de_contador_juridica_id = fields.Many2one('drive', string='Documento de identidad Contador (Si aplica)', tracking=True, related='contratista_id.cedula_de_contador_juridica_id')

    cedula_de_contador_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.cedula_de_contador_juridica_corregir')

    def action_open_cedula_de_contador_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad Contador',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_de_contador_juridica_id.id,
            'target': 'new',
        }

    
    cedula_revisor_fiscal_juridica = fields.Boolean(string='Documento de identidad revisor fiscal (Si aplica)', default=False, tracking=True)
    cedula_revisor_fiscal_juridica_doc = fields.Binary(string='Documento de identidad revisor fiscal (Si aplica)', tracking=True)
    cedula_revisor_fiscal_juridica_doc_name = fields.Char(string='Documento de identidad revisor fiscal (Si aplica)', tracking=True)
    cedula_revisor_fiscal_juridica_id = fields.Many2one('drive', string='Documento de identidad revisor fiscal (Si aplica)', tracking=True, related='contratista_id.cedula_revisor_fiscal_juridica_id')

    
    cedula_revisor_fiscal_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.cedula_revisor_fiscal_juridica_corregir')

    def action_open_cedula_revisor_fiscal_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Documento de identidad revisor fiscal (Si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.cedula_revisor_fiscal_juridica_id.id,
            'target': 'new',
        }

    tarjeta_profesional_Contador_juridica = fields.Boolean(string='Tarjeta profesional de Contador (si aplica)', default=False, tracking=True)
    tarjeta_profesional_Contador_juridica_doc = fields.Binary(string='Tarjeta profesional de Contador (si aplica)', tracking=True)
    tarjeta_profesional_Contador_juridica_doc_name = fields.Char(string='Tarjeta profesional de Contador (si aplica)', tracking=True)
    tarjeta_profesional_Contador_juridica_id = fields.Many2one('drive', string='Tarjeta profesional de Contador (si aplica)', tracking=True, related='contratista_id.Tarjeta_profesional_contador_juridica_id')

    tarjeta_profesional_Contador_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.Tarjeta_profesional_contador_juridica_corregir')

    
    def action_open_tarjeta_profesional_Contador_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tarjeta profesional de Contador (si aplica)',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.tarjeta_profesional_Contador_juridica_id.id,
            'target': 'new',
        }


    certificado_junta_contadores_juridica = fields.Boolean(string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)', default=False, tracking=True)
    certificado_junta_contadores_juridica_documento = fields.Binary(string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)', tracking=True)
    certificado_junta_contadores_juridica_documento_name = fields.Char(string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)', tracking=True)
    certificado_junta_contadores_juridica_id = fields.Many2one('drive', string='Certificado de Junta de Contadores del Revisor Fiscal y/o Contador (si aplica)', tracking=True, related='contratista_id.certificado_junta_contadores_juridica_id')

    certificado_junta_contadores_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.certificado_junta_contadores_juridica_corregir')

    def action_open_certificado_junta_contadores_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificado de Junta de Contadores Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificado_junta_contadores_juridica_id.id,
            'target': 'new',
        }

    rut_representante_legal_juridica = fields.Boolean(string='RUT Representante Legal (si aplica)', default=False, tracking=True)
    rut_representante_legal_juridica_documento = fields.Binary(string='RUT Representante Legal (si aplica)', tracking=True)
    rut_representante_legal_juridica_documento_name = fields.Char(string='RUT Representante Legal (si aplica)', tracking=True)
    rut_representante_legal_juridica_id = fields.Many2one('drive', string='RUT Representante Legal (si aplica)', tracking=True, related='contratista_id.rut_representante_legal_juridica_id')

    rut_representante_legal_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.rut_representante_legal_juridica_corregir')

    def action_open_rut_representante_legal_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'RUT Representante Legal Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.rut_representante_legal_juridica_id.id,
            'target': 'new',
        }


    certificacion_bancaria_juridica = fields.Boolean(string='Certificación bancaria (actualizada)', default=False, tracking=True)
    certificacion_bancaria_juridica_documento = fields.Binary(string='Certificación bancaria (actualizada)', tracking=True)
    certificacion_bancaria_juridica_documento_name = fields.Char(string='Certificación bancaria (actualizada)', tracking=True)

    certificacion_bancaria_juridica_id = fields.Many2one('drive', string='Certificación bancaria (actualizada)', tracking=True, related='contratista_id.certificacion_bancaria_juridica_2_id')

    certificacion_bancaria_juridica_corregir = fields.Boolean(default=False, tracking=True, related='contratista_id.certificacion_bancaria_juridica_corregir')

    def action_open_certificacion_bancaria_juridica_drive(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Certificación Bancaria Jurídica',
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': self.certificacion_bancaria_juridica_id.id,
            'target': 'new',
        }

    soporte_rut_actualizado_id = fields.Many2one(
        'drive', related='contratista_id.rut_actualizado_id',
        string='Soporte RUT actualizado Drive', readonly=False,
    )
    soporte_planilla_de_aportes_a_seguridad_social_id = fields.Many2one(
        'drive', related='contratista_id.planilla_de_aportes_a_seguridad_social_id',
        string='Soporte planilla seguridad social Drive', readonly=False,
    )
    soporte_certificado_de_expensa_id = fields.Many2one(
        'drive', related='contratista_id.certificado_de_expensa_id',
        string='Soporte certificado expensas Drive', readonly=False,
    )
    soporte_copia_cedula_id = fields.Many2one(
        'drive', related='contratista_id.copia_cedula_2_id',
        string='Soporte copia cédula Drive', readonly=False,
    )
    soporte_certificacion_bancaria_id = fields.Many2one(
        'drive', related='contratista_id.certificacion_bancaria_2_id',
        string='Soporte certificación bancaria Drive', readonly=False,
    )
    soporte_declaracion_juramentada_y_soportes_id = fields.Many2one(
        'drive', related='contratista_id.declaracion_juramentada_y_soportes_id',
        string='Soporte declaración y soportes Drive', readonly=False,
    )
    soporte_certificacion_conjunta_id = fields.Many2one(
        'drive', related='contratista_id.certificacion_conjunta_id',
        string='Soporte certificación conjunta Drive', readonly=False,
    )
    soporte_certificacion_conjunta_firmado_id = fields.Many2one(
        'drive', related='contratista_id.certificacion_conjunta_firmado_id',
        string='Soporte certificación conjunta firmada Drive', readonly=False,
    )
    soporte_anexo_tributario_id = fields.Many2one(
        'drive', related='contratista_id.anexo_tributario_2_id',
        string='Soporte anexo tributario Drive', readonly=False,
    )
    soporte_copia_contrato_id = fields.Many2one(
        'drive', related='contratista_id.copia_contrato_2_id',
        string='Soporte copia contrato Drive', readonly=False,
    )
    soporte_registro_de_proveedores_id = fields.Many2one(
        'drive', related='contratista_id.registro_de_proveedores_2_id',
        string='Soporte RPC Drive', readonly=False,
    )
    soporte_informes_contratista_id = fields.Many2one(
        'drive', related='contratista_id.informes_contratista_id',
        string='Soporte informes del contratista Drive', readonly=False,
    )
    soporte_cesion_derechos_id = fields.Many2one(
        'drive', related='contratista_id.cesion_derechos_id',
        string='Soporte cesión de derechos Drive', readonly=False,
    )
    soporte_regalias_copia_cedula_id = fields.Many2one(
        'drive', related='contratista_id.regalias_copia_cedula_id',
        string='Soporte cédula Regalías Drive', readonly=False,
    )
    soporte_regalias_copia_contrato_id = fields.Many2one(
        'drive', related='contratista_id.regalias_copia_contrato_id',
        string='Soporte contrato Regalías Drive', readonly=False,
    )
    soporte_rpc_regalias_id = fields.Many2one(
        'drive', related='contratista_id.rpc_regalias_id',
        string='Soporte RPC Regalías Drive', readonly=False,
    )
    soporte_causacion_id = fields.Many2one(
        'drive', related='contratista_id.causacion_id',
        string='Soporte causación Drive', readonly=False,
    )
    soporte_retencion_id = fields.Many2one(
        'drive', related='contratista_id.retencion_id',
        string='Soporte retención Drive', readonly=False,
    )
    soporte_articulos_id = fields.Many2one(
        'drive', related='contratista_id.articulos_id',
        string='Soporte cruce Art. 383 Drive', readonly=False,
    )
    soporte_documento_liquidacion_id = fields.Many2one(
        'drive', related='contratista_id.documento_liquidacion_id',
        string='Soporte liquidación Drive', readonly=False,
    )
    soporte_documento_cruce_id = fields.Many2one(
        'drive', related='contratista_id.documento_cruce_id',
        string='Soporte cruce o descuento Drive', readonly=False,
    )
    soporte_reporte_liquidacion_id = fields.Many2one(
        'drive', related='contratista_id.reporte_liquidacion_id',
        string='Soporte causación Art. 383 Drive', readonly=False,
    )
    soporte_documentos_adicionales_contabilidad_id = fields.Many2one(
        'drive', related='contratista_id.documentos_adicionales_contabilidad_id',
        string='Soportes adicionales de contabilidad Drive', readonly=False,
    )
    soporte_orden_de_pago_id = fields.Many2one(
        'drive', related='contratista_id.orden_de_pago_id',
        string='Soporte orden de pago Drive', readonly=False,
    )
    soporte_comprobante_de_pago_id = fields.Many2one(
        'drive', related='contratista_id.comprobante_de_pago_id',
        string='Soporte comprobante de pago Drive', readonly=False,
    )
    soporte_comprobante_bancario_id = fields.Many2one(
        'drive', related='contratista_id.comprobante_bancario_id',
        string='Soporte comprobante bancario Drive', readonly=False,
    )
    soporte_factura_electronica_juridica_id = fields.Many2one(
        'drive', related='contratista_id.factura_electronica_juridica_id',
        string='Soporte factura electrónica jurídica Drive', readonly=False,
    )
    soporte_planilla_seguridad_social_juridica_id = fields.Many2one(
        'drive', related='contratista_id.planilla_de_aportes_a_seguridad_social_ju_id',
        string='Soporte planilla jurídica Drive', readonly=False,
    )
    soporte_certificacion_conjunta_juridica_id = fields.Many2one(
        'drive', related='contratista_id.certificacion_conjunta_juridica_id',
        string='Soporte certificación conjunta jurídica Drive', readonly=False,
    )
    soporte_certificacion_conjunta_firmado_juridica_id = fields.Many2one(
        'drive', related='contratista_id.certificacion_conjunta_firmado_juridica_id',
        string='Soporte certificación conjunta jurídica firmada Drive', readonly=False,
    )
    soporte_registro_proveedores_juridica_id = fields.Many2one(
        'drive', related='contratista_id.registro_proveedores_juridica_2_id',
        string='Soporte RPC jurídico Drive', readonly=False,
    )
    soporte_anexo_tributario_juridica_id = fields.Many2one(
        'drive', related='contratista_id.anexo_tributario_juridica_2_id',
        string='Soporte anexo tributario jurídico Drive', readonly=False,
    )
    soporte_copia_contrato_juridica_id = fields.Many2one(
        'drive', related='contratista_id.copia_contrato_juridica_2_id',
        string='Soporte copia contrato jurídico Drive', readonly=False,
    )
    soporte_rut_actualizado_juridica_id = fields.Many2one(
        'drive', related='contratista_id.rut_actualizado_juridica_id',
        string='Soporte RUT jurídica Drive', readonly=False,
    )
    soporte_resolucion_facturacion_electronica_juridica_id = fields.Many2one(
        'drive', related='contratista_id.resolucion_facturacion_electronica_juridica_2_id',
        string='Soporte resolución jurídica Drive', readonly=False,
    )
    soporte_camara_comercio_juridica_id = fields.Many2one(
        'drive', related='contratista_id.camara_comercio_juridica_id',
        string='Soporte cámara comercio Drive', readonly=False,
    )
    soporte_certificado_pago_parafiscales_juridica_id = fields.Many2one(
        'drive', related='contratista_id.certificado_pago_parafiscales_juridica_id',
        string='Soporte parafiscales jurídica Drive', readonly=False,
    )
    soporte_tarjeta_profesional_revisor_fiscal_juridica_id = fields.Many2one(
        'drive', related='contratista_id.tarjeta_profesional_revisor_fiscal_juridica_id',
        string='Soporte tarjeta revisor fiscal Drive', readonly=False,
    )
    soporte_cedula_representante_legal_juridica_id = fields.Many2one(
        'drive', related='contratista_id.cedula_representante_legal_juridica_id',
        string='Soporte cédula representante legal Drive', readonly=False,
    )
    soporte_cedula_de_contador_juridica_id = fields.Many2one(
        'drive', related='contratista_id.cedula_de_contador_juridica_id',
        string='Soporte cédula contador Drive', readonly=False,
    )
    soporte_cedula_revisor_fiscal_juridica_id = fields.Many2one(
        'drive', related='contratista_id.cedula_revisor_fiscal_juridica_id',
        string='Soporte cédula revisor fiscal Drive', readonly=False,
    )
    soporte_tarjeta_profesional_contador_juridica_id = fields.Many2one(
        'drive', related='contratista_id.Tarjeta_profesional_contador_juridica_id',
        string='Soporte tarjeta contador Drive', readonly=False,
    )
    soporte_certificado_junta_contadores_juridica_id = fields.Many2one(
        'drive', related='contratista_id.certificado_junta_contadores_juridica_id',
        string='Soporte junta contadores Drive', readonly=False,
    )
    soporte_rut_representante_legal_juridica_id = fields.Many2one(
        'drive', related='contratista_id.rut_representante_legal_juridica_id',
        string='Soporte RUT representante legal Drive', readonly=False,
    )
    soporte_certificacion_bancaria_juridica_id = fields.Many2one(
        'drive', related='contratista_id.certificacion_bancaria_juridica_2_id',
        string='Soporte certificación bancaria jurídica Drive', readonly=False,
    )


class CuentasDeCobrosRepararFechasWizard(models.TransientModel):
    _name = 'cuentas_de_cobros.reparar_fechas.wizard'
    _description = 'Reparar fechas de actividad de una cuenta de cobro'

    cuenta_id = fields.Many2one(
        'cuentas_de_cobros', string='Cuenta de cobro', required=True, readonly=True,
    )
    fecha_actual_inicio = fields.Char(string='Inicio guardado actualmente', readonly=True)
    fecha_actual_fin = fields.Char(string='Finalización guardada actualmente', readonly=True)
    inicio_corregido = fields.Date(string='Nuevo inicio de actividad', required=True)
    finalizacion_corregida = fields.Date(string='Nueva finalización de actividad', required=True)

    def action_aplicar_reparacion(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError('Solo el grupo Soporte puede reparar fechas de actividad.')
        if self.inicio_corregido.year < 1000 or self.finalizacion_corregida.year < 1000:
            raise ValidationError(
                'El año debe tener cuatro dígitos y no puede comenzar por cero.'
            )
        if self.finalizacion_corregida < self.inicio_corregido:
            raise ValidationError(
                'La finalización de actividad no puede ser anterior al inicio de actividad.'
            )

        cuenta = self.cuenta_id.exists()
        if not cuenta:
            raise UserError('La cuenta de cobro ya no existe.')
        valores_anteriores = '%s a %s' % (
            fields.Date.to_string(cuenta.inicio_actividad) if cuenta.inicio_actividad else 'Sin fecha',
            fields.Date.to_string(cuenta.finalizacion_actividad) if cuenta.finalizacion_actividad else 'Sin fecha',
        )
        cuenta.write({
            'inicio_actividad': self.inicio_corregido,
            'finalizacion_actividad': self.finalizacion_corregida,
        })
        cuenta.message_post(body=(
            '<b>Soporte corrigió las fechas de actividad.</b><br/>'
            'Valores anteriores: %s.<br/>Nuevos valores: %s a %s.'
        ) % (
            valores_anteriores,
            fields.Date.to_string(self.inicio_corregido),
            fields.Date.to_string(self.finalizacion_corregida),
        ))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Fechas corregidas',
                'message': 'La cuenta ya puede abrirse normalmente.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }





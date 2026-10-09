#!/usr/bin/python
# Define el proceso principal de cierre contractual, sus etapas, permisos, validaciones y trazabilidad.
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import formatLang

from .constantes import (
    ESTADOS_CIERRE_CONTRATO,
    EstadoCierreContrato,
)


class ContratacionesFuvCierreProceso(models.Model):
    _name = 'contrataciones_fuv.cierre.proceso'
    _description = 'Proceso de cierre contractual'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'fecha_solicitud desc, id desc'

    def action_abrir_guia_usuario(self):
        """Abre la guía general del proceso sin alterar el cierre actual."""
        self.ensure_one()
        vista = self.env.ref('Matriz.view_cierre_contractual_guia_usuario_form')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Guía de usuario · Cierre contractual'),
            'res_model': 'contrataciones_fuv.cierre.guia.usuario',
            'view_mode': 'form',
            'views': [(vista.id, 'form')],
            'target': 'new',
        }

    name = fields.Char(
        string='Proceso de cierre',
        compute='_compute_name',
        store=True,
    )
    contract_id = fields.Many2one(
        'contrataciones_fuv',
        string='Contrato',
        required=True,
        ondelete='cascade',
        index=True,
        tracking=True,
    )
    proyecto_id = fields.Many2one(
        related='contract_id.proyecto_relacionado',
        string='Proyecto',
        store=True,
        readonly=True,
    )
    tipo_acta = fields.Selection(
        [
            ('anulacion', 'Acta de anulación'),
            ('normal', 'Acta de liquidación de mutuo acuerdo'),
            ('incumplimiento', 'Acta de liquidación unilateral'),
        ],
        string='Tipo de acta',
        required=True,
        tracking=True,
    )
    estado = fields.Selection(
        ESTADOS_CIERRE_CONTRATO,
        string='Estado',
        default=EstadoCierreContrato.SOLICITUD.value,
        required=True,
        tracking=True,
        index=True,
    )
    solicitado_por_id = fields.Many2one(
        'res.users',
        string='Solicitado por',
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
        tracking=True,
    )
    financiero_responsable_id = fields.Many2one(
        'res.users',
        string='Responsable de Financiera',
        readonly=True,
        copy=False,
        tracking=True,
    )
    fecha_solicitud = fields.Datetime(
        string='Fecha de solicitud',
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        tracking=True,
    )
    razones_solicitud = fields.Text(
        string='Razones de la solicitud',
        required=True,
        tracking=True,
    )
    observaciones_coordinacion = fields.Text(
        string='Observaciones de Coordinación',
        tracking=True,
    )
    terminacion_anticipada = fields.Boolean(
        string='Terminación anticipada',
        tracking=True,
    )
    fecha_terminacion_solicitada = fields.Date(
        string='Fecha de terminación solicitada',
        tracking=True,
    )
    confirma_sin_ejecucion_tecnica = fields.Boolean(
        string='El contrato no inició ejecución técnica',
        tracking=True,
    )
    confirma_sin_ejecucion_financiera = fields.Boolean(
        string='El contrato no inició ejecución financiera',
        tracking=True,
    )
    confirma_mutuo_acuerdo = fields.Boolean(
        string='Las partes están de acuerdo con la liquidación',
        tracking=True,
    )
    confirma_liquidacion_unilateral = fields.Boolean(
        string='El contratista no está de acuerdo con la liquidación',
        tracking=True,
    )

    @api.constrains('contract_id', 'tipo_acta', 'objeto_cierre', 'otrosi_id')
    def _check_relacion_otrosi_cierre(self):
        """Impide relacionar el cierre con un otrosí de otro contrato."""
        for record in self:
            if record.tipo_acta == 'anulacion' and record.objeto_cierre != 'contrato':
                raise ValidationError(_(
                    'El acta de anulación solo puede relacionarse con el contrato principal.'
                ))
            if record.objeto_cierre == 'otrosi':
                if not record.otrosi_id:
                    raise ValidationError(_('Debes relacionar el otrosí del cierre.'))
                if record.otrosi_id.contratacion_fuv_en_id != record.contract_id:
                    raise ValidationError(_(
                        'El otrosí relacionado debe pertenecer al mismo contrato.'
                    ))

    # Etapa financiera
    certificado_financiero_revisado = fields.Boolean(
        string='Certificado financiero expedido/revisado',
        tracking=True,
    )
    porcentaje_ejecucion_financiera = fields.Float(
        string='Porcentaje de ejecución financiera',
        tracking=True,
    )
    saldo_por_liberar = fields.Monetary(
        string='Saldo por liberar',
        currency_field='currency_id',
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda',
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    certificado_financiero_id = fields.Many2one(
        'drive',
        string='Certificado financiero',
        tracking=True,
    )
    observaciones_financieras = fields.Text(
        string='Observaciones financieras',
        tracking=True,
    )

    # Etapa jurídica y firma
    juridico_responsable_id = fields.Many2one(
        'res.users',
        string='Responsable de Jurídica',
        tracking=True,
    )
    objeto_cierre = fields.Selection(
        [
            ('contrato', 'Contrato principal'),
            ('otrosi', 'Otrosí que modificó el valor'),
        ],
        string='Documento relacionado con el cierre',
        default='contrato',
        required=True,
        readonly=True,
        tracking=True,
    )
    otrosi_id = fields.Many2one(
        'otro_si',
        string='Otrosí relacionado',
        ondelete='restrict',
        readonly=True,
        tracking=True,
    )
    valor_contrato_original = fields.Monetary(
        string='Valor original del contrato al solicitar',
        currency_field='currency_id',
        readonly=True,
    )
    valor_otrosi_formalizados = fields.Monetary(
        string='Adiciones formalizadas al solicitar',
        currency_field='currency_id',
        readonly=True,
    )
    valor_otrosi_seleccionado = fields.Monetary(
        string='Valor del otrosí relacionado',
        currency_field='currency_id',
        readonly=True,
    )
    valor_contractual_actualizado = fields.Monetary(
        string='Valor contractual actualizado al solicitar',
        currency_field='currency_id',
        readonly=True,
    )
    valor_documento_cierre = fields.Monetary(
        string='Valor total seleccionado',
        currency_field='currency_id',
        readonly=True,
    )
    valor_integral_registrado = fields.Boolean(
        string='Valor integral congelado al solicitar',
        readonly=True,
        default=False,
        help='Distingue la nueva fotografía integral de los selectores históricos.',
    )
    valor_referencia_liberacion = fields.Monetary(
        string='Valor de referencia para liberación',
        currency_field='currency_id',
        compute='_compute_valor_referencia_liberacion',
        help=(
            'Valor inicial del contrato u otrosí seleccionado. En anulación '
            'corresponde al 100 % que debe liberarse.'
        ),
    )
    juridicos_asignables_ids = fields.Many2many(
        'res.users',
        string='Jurídicos disponibles para asignación',
        compute='_compute_juridicos_asignables',
    )

    @api.depends(
        'valor_documento_cierre',
        'objeto_cierre',
        'otrosi_id.valor_total_contrato',
        'contract_id.valor_total_contrato',
        'contract_id.total',
        'tipo_acta',
        'valor_integral_registrado',
    )
    def _compute_valor_referencia_liberacion(self):
        for record in self:
            record.valor_referencia_liberacion = (
                record._valor_referencia_documento_cierre()
            )

    def _valor_referencia_documento_cierre(self):
        """Obtiene la fotografía seleccionada y cubre cierres históricos."""
        self.ensure_one()
        if self.valor_integral_registrado:
            return self.valor_documento_cierre
        # Compatibilidad sin reescribir cierres históricos ni sus reintegros.
        # Anulación conserva su regla de contrato sin ejecución; los otros
        # cierres consideran el total integral que ya calcula el FC.
        if self.tipo_acta == 'anulacion':
            return self.contract_id.valor_total_contrato or 0.0
        return self.contract_id.total or 0.0
    juridica_reviso_soportes = fields.Boolean(
        string='Jurídica revisó los soportes',
        tracking=True,
    )
    acta_juridica_preparada = fields.Boolean(
        string='Acta preparada por Jurídica',
        tracking=True,
    )
    acta_borrador_id = fields.Many2one(
        'drive',
        string='Acta elaborada por Jurídica',
        tracking=True,
    )
    acta_firmada_recibida = fields.Boolean(
        string='Acta firmada recibida',
        tracking=True,
    )
    acta_firmada_id = fields.Many2one(
        'drive',
        string='Acta firmada',
        tracking=True,
    )
    observaciones_juridicas = fields.Text(
        string='Observaciones jurídicas',
        tracking=True,
    )
    cierre_drive_folder_id = fields.Many2one(
        'drive.file',
        string='Carpeta de cierre contractual',
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    observaciones_devolucion = fields.Text(
        string='Observaciones de devolución',
        tracking=True,
    )
    # Campo operativo único para todos los roles y tipos de cierre. Los campos
    # de observaciones anteriores se conservan solo por compatibilidad histórica.
    observaciones_etapa = fields.Text(
        string='Observaciones de la etapa',
        copy=False,
    )
    estado_retorno_devolucion = fields.Selection(
        [
            (EstadoCierreContrato.FINANCIERA.value, 'Financiera'),
            (EstadoCierreContrato.JURIDICA.value, 'Jurídica'),
            (EstadoCierreContrato.LIBERACION_RFC.value, 'Liberación del RFC'),
        ],
        string='Etapa que originó la devolución',
        readonly=True,
        copy=False,
        tracking=True,
        help=(
            'Conserva la etapa que devolvió el proceso para que, después de la '
            'corrección, vuelva directamente a esa misma etapa.'
        ),
    )

    # Etapas finales
    rfc_liberado = fields.Boolean(string='RFC liberado', tracking=True)
    valor_liberado_rfc = fields.Monetary(
        string='Valor liberado del RFC',
        currency_field='currency_id',
        tracking=True,
        help=(
            'Valor informado por Presupuesto al finalizar el cierre. '
            'Posteriormente podrá retornarse al disponible de origen.'
        ),
    )
    # Copia contable del fondo seleccionado en el FC al iniciar el cierre.
    # No es un related: debe conservar el fondo histórico aunque el FC cambie después.
    fondo_origen = fields.Selection(
        [
            ('es_produccion', 'Producción'),
            ('es_personal_fuv', 'Costos FUV'),
            ('contratos_vco', 'Contratos VCO'),
        ],
        string='Fondo de origen del recurso',
        readonly=True,
        copy=False,
        tracking=True,
        help='Fondo del contrato inicial al que se reintegra el valor liberado.',
    )
    fecha_liberacion_rfc = fields.Datetime(
        string='Fecha de liberación del RFC',
        tracking=True,
        help='Campo automático: se registra al finalizar la liberación del RFC.',
    )
    liberado_por_id = fields.Many2one(
        'res.users',
        string='RFC liberado por',
        tracking=True,
        help='Campo automático: identifica al usuario que finalizó la liberación.',
    )
    contratista_notificado = fields.Boolean(
        string='Contratista notificado',
        tracking=True,
    )
    fecha_notificacion_contratista = fields.Datetime(
        string='Fecha de notificación al contratista',
        tracking=True,
    )

    puede_editar_financiera = fields.Boolean(
        string='Puede editar financiera',
        compute='_compute_permisos_edicion_etapas',
    )
    puede_editar_razones_solicitud = fields.Boolean(
        string='Puede editar razones de la solicitud',
        compute='_compute_permisos_edicion_etapas',
    )
    puede_editar_juridica = fields.Boolean(
        string='Puede editar juridica',
        compute='_compute_permisos_edicion_etapas',
    )
    puede_asignar_juridico = fields.Boolean(
        string='Puede asignar responsable jurídico',
        compute='_compute_permisos_edicion_etapas',
    )
    puede_editar_firma_coordinador = fields.Boolean(
        string='Puede editar firma coordinador',
        compute='_compute_permisos_edicion_etapas',
    )
    puede_editar_liberacion_rfc = fields.Boolean(
        string='Puede editar liberacion RFC',
        compute='_compute_permisos_edicion_etapas',
    )
    puede_editar_observacion_devolucion = fields.Boolean(
        string='Puede editar observacion de devolucion',
        compute='_compute_permisos_edicion_etapas',
    )
    puede_editar_observaciones_etapa = fields.Boolean(
        string='Puede editar las observaciones de la etapa',
        compute='_compute_permisos_edicion_etapas',
    )

    @api.depends('estado', 'juridico_responsable_id')
    @api.depends_context('uid')
    def _compute_permisos_edicion_etapas(self):
        user = self.env.user
        es_soporte = self.env.su or user.has_group('Matriz.group_soporte')
        es_financiera_cierre = user.has_group('Matriz.group_financiera_cierre_contrato')
        es_director_juridico = user.has_group('Matriz.group_contratacion_director_juridico')
        es_juridico = user.has_group('Matriz.group_contratacion_juridico')
        es_coordinacion = (
            user.has_group('Matriz.group_contratacion_coordinador')
            or user.has_group('Matriz.group_contratacion_asis_coordinador')
        )
        es_presupuesto = user.has_group('Matriz.group_presupuestos')
        for record in self:
            juridico_asignado = (
                es_juridico
                and record.juridico_responsable_id
                and record.juridico_responsable_id == user
            )
            puede_juridica = es_director_juridico or juridico_asignado
            puede_financiera = (
                record.estado == EstadoCierreContrato.FINANCIERA.value
                and (es_financiera_cierre or es_soporte)
            )
            puede_juridica_etapa = (
                record.estado == EstadoCierreContrato.JURIDICA.value
                and (puede_juridica or es_soporte)
            )
            puede_firma_coordinador = (
                record.estado == EstadoCierreContrato.FIRMA_COORDINADOR.value
                and (es_coordinacion or es_soporte)
            )
            puede_liberacion_rfc = (
                record.estado == EstadoCierreContrato.LIBERACION_RFC.value
                and (
                    es_presupuesto
                    or es_soporte
                    or (
                        record.tipo_acta == 'incumplimiento'
                        and es_financiera_cierre
                    )
                )
            )
            record.puede_editar_financiera = puede_financiera
            record.puede_editar_razones_solicitud = bool(
                record.estado == EstadoCierreContrato.SOLICITUD.value
                and es_coordinacion
            )
            record.puede_editar_juridica = puede_juridica_etapa
            record.puede_asignar_juridico = bool(
                record.estado == EstadoCierreContrato.JURIDICA.value
                and (es_director_juridico or es_soporte)
            )
            record.puede_editar_firma_coordinador = puede_firma_coordinador
            record.puede_editar_liberacion_rfc = puede_liberacion_rfc
            record.puede_editar_observaciones_etapa = any((
                record.estado == EstadoCierreContrato.SOLICITUD.value
                and (es_coordinacion or es_soporte),
                puede_financiera,
                puede_juridica_etapa,
                puede_firma_coordinador,
                puede_liberacion_rfc,
            ))
            # La observación de devolución pertenece exclusivamente al rol
            # responsable de la etapa; soporte/superusuario no la habilita por defecto.
            record.puede_editar_observacion_devolucion = any((
                record.estado == EstadoCierreContrato.FINANCIERA.value
                and es_financiera_cierre,
                record.estado == EstadoCierreContrato.JURIDICA.value
                and puede_juridica,
                record.estado == EstadoCierreContrato.FIRMA_COORDINADOR.value
                and es_coordinacion,
                record.estado == EstadoCierreContrato.LIBERACION_RFC.value
                and (
                    es_presupuesto
                    or (
                        record.tipo_acta == 'incumplimiento'
                        and es_financiera_cierre
                    )
                ),
            ))

    @api.depends(
        'contract_id',
    )
    def _compute_juridicos_asignables(self):
        """Ofrece los usuarios activos que tienen el rol Jurídico."""
        usuarios_vacios = self.env['res.users']
        for record in self:
            record.juridicos_asignables_ids = (
                record._usuarios_juridicos_asignables_cierre()
                if record.contract_id
                else usuarios_vacios
            )

    is_not_financiera = fields.Boolean(
        compute='_compute_is_not_financiera',
        search=False
    )

    @api.depends_context('uid')
    def _compute_is_not_financiera(self):
        # Verifica si el usuario NO está en el grupo
        has_group = self.env.user.has_group('Matriz.group_financiera_cierre_contrato')
        for record in self:
            record.is_not_financiera = not has_group
    
    
    is_not_juridico = fields.Boolean(
        compute='_compute_is_not_juridico',
        search=False
    )

    @api.depends_context('uid')
    def _compute_is_not_juridico(self):
        # Verifica si el usuario NO está en el grupo
        has_group = self.env.user.has_group('Matriz.group_contratacion_juridico') or self.env.user.has_group('Matriz.group_contratacion_director_juridico')
        for record in self:
            record.is_not_juridico = not has_group
    
    
    def _grupos_edicion_por_estado(self):
        return {
            EstadoCierreContrato.SOLICITUD.value: (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            EstadoCierreContrato.FINANCIERA.value: (
                'Matriz.group_financiera_cierre_contrato',
            ),
            EstadoCierreContrato.JURIDICA.value: (
                'Matriz.group_contratacion_director_juridico',
                'Matriz.group_contratacion_juridico',
            ),
            EstadoCierreContrato.FIRMA_COORDINADOR.value: (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            EstadoCierreContrato.LIBERACION_RFC.value: (
                'Matriz.group_presupuestos',
            ),
        }

    def _validar_edicion_por_estado(self):
        if (
            self.env.su
            or self.env.context.get('skip_validacion_cierre_contractual')
            or self.env.user.has_group('Matriz.group_soporte')
        ):
            return
        grupos_por_estado = self._grupos_edicion_por_estado()
        for record in self:
            if (
                record.estado == EstadoCierreContrato.JURIDICA.value
                and self.env.user.has_group('Matriz.group_contratacion_juridico')
                and not self.env.user.has_group('Matriz.group_contratacion_director_juridico')
                and record.juridico_responsable_id != self.env.user
            ):
                raise UserError(_(
                    'Solo la persona responsable de Jurídica puede editar este cierre contractual.'
                ))
            grupos = grupos_por_estado.get(record.estado)
            # En liquidación unilateral el manual asigna la liberación a
            # Financiera o Presupuesto. Los demás tipos conservan Presupuesto.
            if (
                record.estado == EstadoCierreContrato.LIBERACION_RFC.value
                and record.tipo_acta == 'incumplimiento'
            ):
                grupos = (
                    'Matriz.group_presupuestos',
                    'Matriz.group_financiera_cierre_contrato',
                )
            if grupos and not any(
                self.env.user.has_group(xml_id) for xml_id in grupos
            ):
                estado = dict(ESTADOS_CIERRE_CONTRATO).get(
                    record.estado,
                    record.estado,
                )
                raise UserError(_(
                    'No tienes permisos para editar el proceso durante la etapa %s.'
                ) % estado)

    def write(self, vals):
        campos_exclusivos_coordinacion = {
            'razones_solicitud',
            'observaciones_coordinacion',
        }
        if campos_exclusivos_coordinacion.intersection(vals):
            puede_editar_razones = (
                self.env.user.has_group('Matriz.group_contratacion_coordinador')
                or self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
            )
            if not puede_editar_razones:
                raise UserError(_(
                    'Solo Coordinación o Asistencia de coordinación pueden modificar '
                    'las razones y observaciones de la solicitud.'
                ))
            if any(
                record.estado != EstadoCierreContrato.SOLICITUD.value
                for record in self
            ):
                raise UserError(_(
                    'Las razones y observaciones de la solicitud solo pueden '
                    'modificarse durante la etapa Solicitud.'
                ))
        self._validar_edicion_por_estado()
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)

    def unlink(self):
        trazabilidad = []
        for record in self:
            if record.contract_id:
                tipo_acta = dict(record._fields['tipo_acta'].selection).get(
                    record.tipo_acta,
                    record.tipo_acta or '',
                )
                estado = dict(ESTADOS_CIERRE_CONTRATO).get(
                    record.estado,
                    record.estado or '',
                )
                trazabilidad.append((
                    record.contract_id,
                    record.display_name,
                    tipo_acta,
                    estado,
                    record.env.user.name,
                ))
        result = super().unlink()
        for contrato, nombre, tipo_acta, estado, usuario in trazabilidad:
            contrato.message_post(
                body=Markup(
                    '<p><strong>Proceso de cierre contractual eliminado.</strong></p>'
                    '<p><strong>Eliminado por:</strong> %s</p>'
                    '<p><strong>Proceso:</strong> %s</p>'
                    '<p><strong>Tipo de acta:</strong> %s</p>'
                    '<p><strong>Estado al eliminar:</strong> %s</p>'
                ) % (
                    escape(usuario),
                    escape(nombre),
                    escape(tipo_acta),
                    escape(estado),
                ),
                subtype_xmlid='mail.mt_note',
            )
        return result

    def _usuarios_financieros_cierre(self):
        """Obtiene los responsables globales del cierre, sin exigir proyecto."""
        self.ensure_one()
        grupo = self.env.ref('Matriz.group_financiera_cierre_contrato')
        return grupo.users.filtered(lambda usuario: usuario.active and not usuario.share)

    def _usuarios_presupuesto_proyecto(self):
        self.ensure_one()
        return self.contract_id._usuarios_proyecto_por_grupo(
            'Matriz.group_presupuestos'
        )

    def _usuarios_directores_juridicos_proyecto(self):
        self.ensure_one()
        return self.contract_id._usuarios_proyecto_por_grupo(
            'Matriz.group_contratacion_director_juridico',
            usuarios_extra=self.contract_id.usuarios_juridico_ids,
        )

    def _usuarios_juridicos_proyecto(self):
        self.ensure_one()
        return (
            self.contract_id._usuarios_proyecto_por_grupo(
                'Matriz.group_contratacion_director_juridico',
                usuarios_extra=self.contract_id.usuarios_juridico_ids,
            )
            | self.contract_id._usuarios_proyecto_por_grupo(
                'Matriz.group_contratacion_juridico',
                usuarios_extra=self.contract_id.usuarios_juridico_ids,
            )
        )

    def _usuarios_juridicos_asignables_cierre(self):
        """Obtiene todos los profesionales activos con el rol Jurídico."""
        self.ensure_one()
        grupo = self.env.ref('Matriz.group_contratacion_juridico')
        return grupo.users.filtered(lambda usuario: usuario.active and not usuario.share)

    def _usuarios_retorno_juridica_cierre(self):
        self.ensure_one()
        usuarios = self.env['res.users']
        if self.juridico_responsable_id:
            usuarios |= self.juridico_responsable_id
        usuarios |= self._usuarios_directores_juridicos_proyecto()
        return usuarios

    def _validar_juridico_responsable_cierre(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.JURIDICA.value:
            return
        if not self.juridico_responsable_id:
            if self.env.user.has_group('Matriz.group_contratacion_director_juridico'):
                self.write({'juridico_responsable_id': self.env.user.id})
                return
            raise UserError(_(
                'Dirección Jurídica debe asignar una persona responsable antes de continuar.'
            ))
        if self.env.user.has_group('Matriz.group_contratacion_director_juridico'):
            return
        if self.env.user != self.juridico_responsable_id:
            raise UserError(_(
                'Solo la persona responsable de Jurídica puede continuar este cierre contractual.'
            ))

    def _usuarios_coordinacion_proyecto(self):
        self.ensure_one()
        return (
            self.contract_id._usuarios_proyecto_por_grupo(
                'Matriz.group_contratacion_coordinador'
            )
            | self.contract_id._usuarios_proyecto_por_grupo(
                'Matriz.group_contratacion_asis_coordinador'
            )
        )

    def _nombre_proyecto_cierre(self):
        self.ensure_one()
        proyecto = self.proyecto_id
        nombres = [proyecto.name, getattr(proyecto, 'new_name', False)]
        return ', '.join(nombre for nombre in nombres if nombre) or 'Sin proyecto'

    def _url_proceso_cierre(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return (
            f'{base_url}/web#id={self.id}&model=contrataciones_fuv.cierre.proceso'
            '&view_type=form'
        )

    def _email_contratista_cierre(self):
        self.ensure_one()
        return self.contract_id.correo

    def _plantilla_correo_cierre(
        self,
        titulo,
        mensaje,
        area_destino,
        boton_texto,
        observaciones=False,
        es_devolucion=False,
        es_informativo=False,
        mostrar_boton=True,
        nombre_destinatario=False,
    ):
        self.ensure_one()
        tipo_acta = dict(self._fields['tipo_acta'].selection).get(
            self.tipo_acta,
            self.tipo_acta,
        )
        color = '#043484'
        saludo = escape(nombre_destinatario or 'usuario')
        titulo_aviso = (
            'Notificación informativa'
            if es_informativo
            else ('Acción requerida' if mostrar_boton else 'Notificación del proceso')
        )
        bloque_observaciones = ''
        if observaciones:
            fondo_observacion = '#fffbeb' if es_devolucion else '#eff6ff'
            borde_observacion = '#f59e0b' if es_devolucion else '#2563eb'
            titulo_observacion = (
                'Observaciones de devolución'
                if es_devolucion
                else 'Observaciones del área que envía'
            )
            bloque_observaciones = f"""
                <div style="margin: 18px 0; padding: 14px; background-color: {fondo_observacion}; border-left: 4px solid {borde_observacion};">
                    <p style="margin: 0 0 6px 0; color: {borde_observacion};"><strong>{titulo_observacion}</strong></p>
                    <p style="margin: 0; white-space: pre-line;">{escape(observaciones)}</p>
                </div>
            """
        bloque_boton = ''
        if mostrar_boton:
            bloque_boton = f"""
                        <p style="margin-top: 20px; text-align: center;">
                            <a href="{self._url_proceso_cierre()}"
                               style="background-color: {color}; color: #ffffff; padding: 11px 22px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                {escape(boton_texto)}
                            </a>
                        </p>
            """
        return f"""
            <div style="font-family: Arial, sans-serif; font-size: 14px; color: #263238; background-color: #f5f7fb; padding: 20px;">
                <div style="max-width: 720px; margin: 0 auto; background: #ffffff; border: 1px solid #dbe3ef; border-radius: 6px; overflow: hidden;">
                    <div style="background-color: {color}; color: #ffffff; padding: 18px 22px;">
                        <table role="presentation" cellpadding="0" cellspacing="0" style="width: 100%; color: #ffffff;">
                            <tr>
                                <td style="width: 42px; vertical-align: top; font-size: 27px; line-height: 1;">&#128276;</td>
                                <td>
                                    <h2 style="margin: 0; font-size: 20px;">{escape(titulo)}</h2>
                                    <p style="margin: 6px 0 0 0;">Gestión Operativa SGO</p>
                                </td>
                            </tr>
                        </table>
                    </div>
                    <div style="padding: 22px;">
                        <p style="margin-top: 0;">Hola, <strong>{saludo}</strong>.</p>
                        <div style="margin: 14px 0 18px; padding: 12px 14px; background-color: #eff6ff; border: 1px solid #bfdbfe; border-radius: 5px;">
                            <p style="margin: 0 0 5px; color: {color};"><strong>&#128276; {titulo_aviso}</strong></p>
                            <p style="margin: 0; line-height: 1.45;">{mensaje}</p>
                        </div>
                        <div style="margin: 18px 0; padding: 14px; background-color: #f8fafc; border-left: 4px solid {color};">
                            <p style="margin: 0 0 6px 0;"><strong>Contratista:</strong> {escape(self.contract_id.new_name or self.contract_id.display_name or '')}</p>
                            <p style="margin: 0 0 6px 0;"><strong>Proyecto:</strong> {escape(self._nombre_proyecto_cierre())}</p>
                            <p style="margin: 0 0 6px 0;"><strong>Tipo de acta:</strong> {escape(tipo_acta)}</p>
                            <p style="margin: 0;"><strong>Area destino:</strong> {escape(area_destino)}</p>
                        </div>
                        {bloque_observaciones}
                        {bloque_boton}
                        <p style="margin-top: 24px;">Cordialmente,<br/><strong>SGO</strong></p>
                    </div>
                </div>
            </div>
        """

    def _enviar_correo_cierre(
        self,
        destinatarios,
        titulo,
        mensaje,
        area_destino,
        boton_texto,
        observaciones=False,
        es_devolucion=False,
        es_informativo=False,
    ):
        self.ensure_one()
        destinatarios = destinatarios.filtered(lambda usuario: usuario.email)
        if not destinatarios:
            raise UserError(_('No se encontraron destinatarios con correo configurado.'))

        asunto = (
            f'🔔 {titulo} - {self.contract_id.new_name or self.contract_id.display_name} - '
            f'{self._nombre_proyecto_cierre()}'
        )
        for destinatario in destinatarios:
            # La plantilla se genera por usuario para personalizar el saludo.
            body_html = self._plantilla_correo_cierre(
                titulo,
                mensaje,
                area_destino,
                boton_texto,
                observaciones=observaciones,
                es_devolucion=es_devolucion,
                es_informativo=es_informativo,
                nombre_destinatario=destinatario.name,
            )
            self.env['mail.mail'].sudo().create({
                'subject': asunto,
                'email_from': self.env.user.email or 'no-reply@tudominio.com',
                'email_to': destinatario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()

        nombres_destinatarios = ', '.join(destinatarios.mapped('name'))
        titulo_observacion = (
            'Observaciones de devolución'
            if es_devolucion
            else 'Observaciones del área que envía'
        )
        bloque_observacion_chatter = Markup('')
        if observaciones:
            bloque_observacion_chatter = Markup(
                '<p><strong>%s:</strong><br/>%s</p>'
            ) % (
                escape(titulo_observacion),
                escape(observaciones),
            )
        self.message_post(
            body=Markup(
                '<p><strong>%s</strong></p><p>%s</p>%s'
                '<p><strong>Destinatarios:</strong> %s</p>'
            ) % (
                escape(titulo),
                Markup(mensaje),
                bloque_observacion_chatter,
                escape(nombres_destinatarios),
            ),
            subtype_xmlid='mail.mt_note',
        )

    def _registrar_responsable_financiero_actual(self):
        """Conserva quién atendió la certificación financiera del proceso."""
        self.ensure_one()
        if (
            not self.financiero_responsable_id
            and self.env.user.has_group('Matriz.group_financiera_cierre_contrato')
        ):
            self.with_context(skip_validacion_cierre_contractual=True).write({
                'financiero_responsable_id': self.env.user.id,
            })
        return self.financiero_responsable_id

    def _notificar_acta_firmada_financiera(self, nombre_documento):
        """Avisa al responsable financiero cuando se carga el acta firmada."""
        self.ensure_one()
        responsable = self.financiero_responsable_id
        if not responsable:
            candidatos = self._usuarios_financieros_cierre().filtered(
                lambda usuario: usuario.email
            )
            if len(candidatos) == 1:
                responsable = candidatos
                self.with_context(skip_validacion_cierre_contractual=True).write({
                    'financiero_responsable_id': responsable.id,
                })
            else:
                raise UserError(_(
                    'El proceso no tiene un responsable financiero asignado para '
                    'notificar la carga del acta firmada.'
                ))

        mensaje = (
            f'Se informa que el documento <strong>{escape(nombre_documento)}</strong> '
            'fue cargado en el proceso de cierre contractual. Puedes consultar '
            'el proceso y sus documentos desde el siguiente enlace.'
        )
        self._enviar_correo_cierre(
            responsable,
            'Cierre de contrato: acta firmada cargada',
            mensaje,
            'Financiera',
            'Ver proceso',
            es_informativo=True,
        )
        return True

    def _enviar_correo_contratista_cierre(self, titulo, mensaje, documento=False):
        self.ensure_one()
        correo = self._email_contratista_cierre()
        if not correo:
            raise UserError(_('El contratista no tiene correo configurado para la notificación.'))

        body_html = self._plantilla_correo_cierre(
            titulo,
            mensaje,
            'Contratista',
            '',
            mostrar_boton=False,
            nombre_destinatario=(
                self.contract_id.new_name
                or self.contract_id.display_name
            ),
        )
        valores_correo = {
            'subject': f'🔔 {titulo} - {self.contract_id.new_name or self.contract_id.display_name}',
            'email_from': self.env.user.email or 'no-reply@tudominio.com',
            'email_to': correo,
            'body_html': body_html,
            'auto_delete': True,
        }
        if documento:
            # El helper del FC devuelve directamente el ID entero del adjunto
            # temporal, no un recordset de ir.attachment.
            attachment_id = self.contract_id._crear_adjunto_temporal_drive_para_correo(
                documento,
                nombre_default='Acta_liquidacion_unilateral.pdf',
            )
            if not attachment_id:
                raise UserError(_(
                    'No fue posible preparar el acta unilateral para adjuntarla '
                    'a la notificación del contratista.'
                ))
            valores_correo['attachment_ids'] = [(4, attachment_id)]
        self.env['mail.mail'].sudo().create(valores_correo).send()
        self.write({
            'contratista_notificado': True,
            'fecha_notificacion_contratista': fields.Datetime.now(),
        })
        self.message_post(
            body=Markup(
                '<p><strong>Contratista notificado.</strong></p>'
                '<p>Correo enviado a: %s</p>'
            ) % escape(correo),
            subtype_xmlid='mail.mt_note',
        )

    def _usuarios_liberacion_unilateral(self):
        """Destinatarios autorizados por el manual para liberar el unilateral."""
        self.ensure_one()
        return (
            self._usuarios_financieros_cierre()
            | self._usuarios_presupuesto_proyecto()
        )

    def _post_trazabilidad_cierre(self, titulo, mensaje):
        self.ensure_one()
        estado_actual = dict(ESTADOS_CIERRE_CONTRATO).get(
            self.estado,
            self.estado or 'Sin estado',
        )
        titulo_normalizado = (titulo or '').lower()
        if 'devuelto' in titulo_normalizado or 'devolución' in titulo_normalizado:
            color = '#d97706'
            fondo = '#fff7ed'
            etiqueta = 'DEVOLUCIÓN'
            icono = '↩'
        elif 'finalizado' in titulo_normalizado:
            color = '#15803d'
            fondo = '#f0fdf4'
            etiqueta = 'FINALIZADO'
            icono = '✓'
        else:
            color = '#0369a1'
            fondo = '#f0f9ff'
            etiqueta = 'AVANCE DEL PROCESO'
            icono = '→'

        body = Markup(
            '<div style="border-left: 5px solid %s; background: %s; '
            'padding: 12px 14px; border-radius: 6px; margin: 4px 0;">'
            '<div style="color: %s; font-size: 11px; font-weight: 700; '
            'letter-spacing: .4px; margin-bottom: 5px;">%s %s</div>'
            '<div style="font-size: 15px; font-weight: 700; color: #1f2937; '
            'margin-bottom: 5px;">%s</div>'
            '<div style="color: #374151; line-height: 1.45;">%s</div>'
            '<div style="color: #64748b; font-size: 12px; margin-top: 7px;">'
            '<strong>Estado resultante:</strong> %s<br/>'
            '<strong>Realizado por:</strong> %s</div>'
            '</div>'
        ) % (
            color,
            fondo,
            color,
            icono,
            etiqueta,
            escape(titulo),
            Markup(mensaje),
            escape(estado_actual),
            escape(self.env.user.name),
        )
        self.message_post(body=body, subtype_xmlid='mail.mt_note')
        self.contract_id.message_post(body=body, subtype_xmlid='mail.mt_note')

    def _validar_check_cierre(self, campo, mensaje):
        self.ensure_one()
        if not self[campo]:
            raise UserError(_(mensaje))

    def _mensaje_devolucion_cierre(self, origen, destino):
        self.ensure_one()
        return f'{origen} devolvió el proceso de cierre contractual a {destino} para ajustes.'

    def _mensaje_trazabilidad_devolucion(self, mensaje, observacion):
        return (
            f'{mensaje}<br/><strong>Observaciones:</strong> '
            f'{escape(observacion or "Sin observaciones.")}'
        )

    def _tomar_observacion_etapa(self, requerida=False):
        """Lee la observación única antes de vaciarla al cambiar de etapa."""
        self.ensure_one()
        observacion = (self.observaciones_etapa or '').strip()
        if requerida and not observacion:
            raise UserError(_(
                'Debes escribir una observación indicando el ajuste solicitado '
                'antes de devolver el proceso.'
            ))
        return observacion

    def _reenviar_correccion_coordinacion_a_juridica(self):
        """Retorna a Jurídica una corrección sin reiniciar el paso financiero."""
        self.ensure_one()
        if self.estado_retorno_devolucion != EstadoCierreContrato.JURIDICA.value:
            return False

        juridicos = self._usuarios_retorno_juridica_cierre()
        if not juridicos:
            raise UserError(_('No hay usuarios jurídicos asociados al proyecto.'))

        observaciones_envio = self._tomar_observacion_etapa()
        self.write({
            'estado': EstadoCierreContrato.JURIDICA.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        mensaje = (
            'Coordinación realizó los ajustes solicitados y devolvió el proceso '
            'directamente a Jurídica para continuar la revisión.'
        )
        self._enviar_correo_cierre(
            juridicos,
            'Cierre de contrato: corrección devuelta a Jurídica',
            mensaje,
            'Jurídica',
            'Revisar corrección',
            observaciones=observaciones_envio,
        )
        self._post_trazabilidad_cierre(
            'Corrección enviada a Jurídica',
            mensaje,
        )
        return True

    def _action_enviar_certificacion_financiera_sin_plantilla(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.SOLICITUD.value:
            raise UserError(_('Este proceso solo puede enviarse desde Coordinador (solicita).'))
        puede_enviar = (
            self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
        )
        if not puede_enviar:
            raise UserError(_(
                'Solo Coordinación o Asistencia de coordinación pueden enviar '
                'la solicitud a certificación financiera.'
            ))

        # Si Jurídica originó la devolución, la corrección debe regresar allí
        # y no reiniciar el flujo pasando nuevamente por Financiera.
        if self._reenviar_correccion_coordinacion_a_juridica():
            return True

        financieros = self._usuarios_financieros_cierre()
        if not financieros:
            raise UserError(_(
                'No hay usuarios activos con el rol Financiera cierre contrato '
                'para recibir la solicitud de cierre.'
            ))

        self._otorgar_acceso_lector_cierre(financieros)
        observaciones_envio = self._tomar_observacion_etapa()
        self.write({
            'estado': EstadoCierreContrato.FINANCIERA.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        tipo_acta = dict(self._fields['tipo_acta'].selection).get(
            self.tipo_acta,
            self.tipo_acta,
        )
        mensaje = Markup(
            '<p><strong>Solicitud de cierre enviada a certificación financiera.</strong></p>'
            '<p><strong>Tipo:</strong> %s</p>'
            '<p><strong>Enviada por:</strong> %s</p>'
            '<p><strong>Razones:</strong> %s</p>'
            '<p><strong>Observaciones de Coordinación:</strong> %s</p>'
        ) % (
            escape(tipo_acta),
            escape(self.env.user.name),
            escape(self.razones_solicitud or ''),
            escape(observaciones_envio or 'Sin observaciones.'),
        )
        self.contract_id.message_post(
            body=mensaje,
            subtype_xmlid='mail.mt_note',
        )
        self.message_post(
            body=mensaje,
            partner_ids=financieros.mapped('partner_id').ids,
        )
        return True

    def action_enviar_certificacion_financiera(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.SOLICITUD.value:
            raise UserError(_('Este proceso solo puede enviarse desde Coordinador (solicita).'))
        puede_enviar = (
            self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
        )
        if not puede_enviar:
            raise UserError(_(
                'Solo Coordinación o Asistencia de coordinación pueden enviar '
                'la solicitud a certificación financiera.'
            ))

        # Una corrección solicitada por Jurídica retorna directamente a quien
        # la devolvió; las solicitudes nuevas y devoluciones de Financiera
        # conservan el envío normal a certificación financiera.
        if self._reenviar_correccion_coordinacion_a_juridica():
            return True

        financieros = self._usuarios_financieros_cierre()
        if not financieros:
            raise UserError(_(
                'No hay usuarios activos con el rol Financiera cierre contrato '
                'para recibir la solicitud de cierre.'
            ))

        self._otorgar_acceso_lector_cierre(financieros)
        observaciones_envio = self._tomar_observacion_etapa()
        self.write({
            'estado': EstadoCierreContrato.FINANCIERA.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        tipo_acta = dict(self._fields['tipo_acta'].selection).get(
            self.tipo_acta,
            self.tipo_acta,
        )
        mensaje = (
            f'Se envió la solicitud de cierre contractual a certificación financiera. '
            f'Tipo: <strong>{escape(tipo_acta)}</strong>. '
            f'Razones: {escape(self.razones_solicitud or "")}'
        )
        self._enviar_correo_cierre(
            financieros,
            'Cierre de contrato: solicitud para certificación financiera',
            mensaje,
            'Financiera',
            'Revisar solicitud',
            observaciones=observaciones_envio,
        )
        self._post_trazabilidad_cierre(
            'Cierre enviado a Financiera',
            mensaje,
        )
        return True

    def action_enviar_revision_juridica(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.FINANCIERA.value:
            raise UserError(_('Este proceso solo puede enviarse a juridica desde Certificacion financiera.'))
        if not self.env.user.has_group('Matriz.group_financiera_cierre_contrato'):
            raise UserError(_(
                'Solo Financiera cierre contrato puede enviar el proceso a Jurídica.'
            ))
        self._validar_check_cierre(
            'certificado_financiero_revisado',
            'Debes confirmar que el certificado financiero fue expedido o revisado antes de continuar.',
        )
        self._registrar_responsable_financiero_actual()
        directores_juridicos = self._usuarios_directores_juridicos_proyecto()
        if not directores_juridicos:
            raise UserError(_('No hay directores jurídicos asociados al proyecto para recibir la solicitud.'))

        observaciones_envio = self._tomar_observacion_etapa()
        self.write({
            'estado': EstadoCierreContrato.JURIDICA.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        if self.tipo_acta in ('normal', 'incumplimiento'):
            valor_saldo = formatLang(
                self.env,
                self.saldo_por_liberar,
                currency_obj=self.currency_id,
            )
            tipo_certificacion = (
                'la liquidación unilateral'
                if self.tipo_acta == 'incumplimiento'
                else 'la liquidación por mutuo acuerdo'
            )
            mensaje = (
                f'Financiera emitió el certificado para {tipo_certificacion} con '
                f'<strong>{self.porcentaje_ejecucion_financiera:g}%</strong> de ejecución. '
                f'Saldo certificado por liberar: <strong>{escape(valor_saldo)}</strong>. '
                'El proceso pasa a Dirección Jurídica para revisión.'
            )
        else:
            mensaje = 'Financiera expidió/revisó el certificado financiero. El proceso pasa a Dirección Jurídica para revisión.'
        self._enviar_correo_cierre(
            directores_juridicos,
            'Cierre de contrato: revisión jurídica requerida',
            mensaje,
            'Dirección Jurídica',
            'Revisar cierre',
            observaciones=observaciones_envio,
        )
        self._post_trazabilidad_cierre('Cierre enviado a Dirección Jurídica', mensaje)
        return True

    def action_asignar_juridico_cierre(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.JURIDICA.value:
            raise UserError(_('Este proceso solo puede asignarse durante Jurídico (revisa).'))
        if not self.env.user.has_group('Matriz.group_contratacion_director_juridico'):
            raise UserError(_('Solo Dirección Jurídica puede asignar a la persona responsable.'))
        if not self.juridico_responsable_id:
            raise UserError(_('Debes seleccionar una persona responsable antes de asignar.'))

        grupo_juridico = self.env.ref('Matriz.group_contratacion_juridico')
        if grupo_juridico not in self.juridico_responsable_id.groups_id:
            raise UserError(_('El usuario asignado debe pertenecer al grupo Jurídico.'))
        if self.juridico_responsable_id not in self._usuarios_juridicos_asignables_cierre():
            raise UserError(_(
                'La persona seleccionada debe estar activa y tener el rol Jurídico.'
            ))

        responsable = self.juridico_responsable_id
        self._otorgar_acceso_lector_cierre(responsable)
        mensaje = (
            f'El área Jurídica asignó a <strong>{escape(responsable.name)}</strong> '
            f'como responsable de revisar el cierre contractual.'
        )
        self._enviar_correo_cierre(
            responsable,
            'Cierre de contrato: asignación jurídica',
            mensaje,
            'Jurídica',
            'Revisar cierre',
        )
        self.contract_id.message_post(
            body=Markup('<p><strong>Asignación de responsable de Jurídica.</strong></p><p>%s</p>') % Markup(mensaje),
            subtype_xmlid='mail.mt_note',
        )
        return True

    def action_enviar_firma_coordinador(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.JURIDICA.value:
            raise UserError(_('Este proceso solo puede enviarse desde Jurídico (revisa).'))
        if self.tipo_acta == 'incumplimiento':
            raise UserError(_('La liquidación unilateral no requiere firma del contratista en esta etapa.'))
        self._validar_juridico_responsable_cierre()
        self._validar_check_cierre(
            'juridica_reviso_soportes',
            'Debes confirmar que Juridica reviso los soportes antes de continuar.',
        )
        self._validar_check_cierre(
            'acta_juridica_preparada',
            'Debes confirmar que Juridica preparo el acta antes de continuar.',
        )
        coordinacion = self._usuarios_coordinacion_proyecto()
        if not coordinacion:
            raise UserError(_('No hay usuarios de Coordinacion asociados al proyecto.'))

        observaciones_envio = self._tomar_observacion_etapa()
        self.write({
            'estado': EstadoCierreContrato.FIRMA_COORDINADOR.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        if self.tipo_acta == 'normal':
            mensaje = (
                'Jurídica expidió el acta de liquidación de mutuo acuerdo. '
                'Coordinación debe tramitar las firmas y cargar el acta firmada.'
            )
        else:
            mensaje = 'Juridica preparo el acta. Coordinacion debe gestionar la firma y confirmar el acta firmada.'
        self._enviar_correo_cierre(
            coordinacion,
            'Cierre de contrato: acta lista para firma',
            mensaje,
            'Coordinacion',
            'Gestionar firma',
            observaciones=observaciones_envio,
        )
        self._post_trazabilidad_cierre('Acta enviada a firma', mensaje)
        return True

    def action_enviar_liberacion_rfc(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.FIRMA_COORDINADOR.value:
            raise UserError(_('Este proceso solo puede enviarse desde Coordinador (firma).'))
        puede_enviar = (
            self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
        )
        if not puede_enviar:
            raise UserError(_(
                'Solo Coordinación o Asistencia de coordinación pueden enviar '
                'el acta firmada a Presupuesto.'
            ))
        self._validar_check_cierre(
            'acta_firmada_recibida',
            'Debes confirmar que el acta firmada fue recibida antes de continuar.',
        )
        presupuestos = self._usuarios_presupuesto_proyecto()
        if not presupuestos:
            raise UserError(_('No hay usuarios de Presupuesto asociados al proyecto.'))
        if not presupuestos.filtered(lambda usuario: usuario.email):
            raise UserError(_(
                'Los usuarios de Presupuesto asociados al proyecto no tienen '
                'correo configurado.'
            ))

        observaciones_envio = self._tomar_observacion_etapa()
        valores_liberacion = {
            'estado': EstadoCierreContrato.LIBERACION_RFC.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        }
        if self.tipo_acta == 'anulacion':
            # Como el contrato nunca inició, Presupuesto recibe preparado el
            # 100 % del valor contractual para su liberación.
            valores_liberacion['valor_liberado_rfc'] = (
                self._valor_referencia_documento_cierre()
            )
        self.write(valores_liberacion)
        if self.tipo_acta == 'normal':
            mensaje = (
                'Coordinación cargó el acta de liquidación de mutuo acuerdo '
                'firmada. Presupuesto debe revisar y liberar los saldos si aplica.'
            )
        else:
            mensaje = 'Coordinacion confirmo el acta firmada. Presupuesto debe liberar el RFC si aplica.'
        self._enviar_correo_cierre(
            presupuestos,
            'Cierre de contrato: acta firmada para liberación de RFC',
            mensaje,
            'Presupuesto',
            'Liberar RFC',
            observaciones=observaciones_envio,
        )
        self._post_trazabilidad_cierre('Cierre enviado a liberación del RFC', mensaje)
        return True

    def action_enviar_liberacion_rfc_unilateral(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.JURIDICA.value:
            raise UserError(_('Este proceso solo puede enviarse desde Jurídico (revisa).'))
        if self.tipo_acta != 'incumplimiento':
            raise UserError(_('Esta acción solo aplica para acta de liquidación unilateral.'))
        self._validar_juridico_responsable_cierre()
        self._validar_check_cierre(
            'juridica_reviso_soportes',
            'Debes confirmar que Juridica reviso los soportes antes de continuar.',
        )
        self._validar_check_cierre(
            'acta_juridica_preparada',
            'Debes confirmar que Juridica preparo el acta antes de continuar.',
        )
        responsables_liberacion = self._usuarios_liberacion_unilateral()
        if not responsables_liberacion:
            raise UserError(_(
                'No hay usuarios habilitados de Financiera o Presupuesto para '
                'liberar el RFC.'
            ))
        if not responsables_liberacion.filtered(lambda usuario: usuario.email):
            raise UserError(_(
                'Los usuarios habilitados de Financiera o Presupuesto no tienen '
                'correo configurado. No se notificó al contratista.'
            ))

        financieros = self._usuarios_financieros_cierre()
        if financieros:
            self._otorgar_acceso_lector_cierre(financieros)

        self._enviar_correo_contratista_cierre(
            'Notificación de liquidación unilateral del contrato',
            (
                'Se informa que la Fundación formalizó la liquidación unilateral '
                'del contrato. El acto firmado por Dirección Ejecutiva se '
                'encuentra adjunto a esta notificación.'
            ),
            documento=self.acta_unilateral_firmada_id,
        )
        observaciones_envio = self._tomar_observacion_etapa()
        self.write({
            'estado': EstadoCierreContrato.LIBERACION_RFC.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        mensaje = (
            'Jurídica formalizó el acta unilateral y el sistema notificó al '
            'contratista. Financiera o Presupuesto debe liberar el RFC si aplica.'
        )
        self._enviar_correo_cierre(
            responsables_liberacion,
            'Cierre de contrato: liquidación unilateral para liberación de RFC',
            mensaje,
            'Financiera / Presupuesto',
            'Liberar RFC',
            observaciones=observaciones_envio,
        )
        self._post_trazabilidad_cierre('Liquidación unilateral enviada a liberación del RFC', mensaje)
        return True

    def action_devolver_a_coordinacion_desde_financiera(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.FINANCIERA.value:
            raise UserError(_('Este proceso solo puede devolverse desde Financiera (certifica).'))
        if not self.env.user.has_group('Matriz.group_financiera_cierre_contrato'):
            raise UserError(_(
                'Solo Financiera cierre contrato puede devolver el proceso a Coordinación.'
            ))
        coordinacion = self._usuarios_coordinacion_proyecto()
        if not coordinacion:
            raise UserError(_('No hay usuarios de Coordinación asociados al proyecto.'))

        observacion = self._tomar_observacion_etapa(requerida=True)
        mensaje = self._mensaje_devolucion_cierre('Financiera', 'Coordinación')
        self.write({
            'estado': EstadoCierreContrato.SOLICITUD.value,
            'estado_retorno_devolucion': EstadoCierreContrato.FINANCIERA.value,
            'observaciones_etapa': False,
        })
        self._enviar_correo_cierre(
            coordinacion,
            'Cierre de contrato: devolución a Coordinación',
            mensaje,
            'Coordinación',
            'Revisar devolución',
            observaciones=observacion,
            es_devolucion=True,
        )
        self._post_trazabilidad_cierre(
            'Cierre devuelto a Coordinación',
            self._mensaje_trazabilidad_devolucion(mensaje, observacion),
        )
        return True

    def action_devolver_a_financiera_desde_juridica(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.JURIDICA.value:
            raise UserError(_('Este proceso solo puede devolverse desde Jurídica (revisa).'))
        financieros = self._usuarios_financieros_cierre()
        if not financieros:
            raise UserError(_(
                'No hay usuarios activos con el rol Financiera cierre contrato.'
            ))

        self._otorgar_acceso_lector_cierre(financieros)
        observacion = self._tomar_observacion_etapa(requerida=True)
        mensaje = self._mensaje_devolucion_cierre('Jurídica', 'Financiera')
        self.write({
            'estado': EstadoCierreContrato.FINANCIERA.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        self._enviar_correo_cierre(
            financieros,
            'Cierre de contrato: devolución a Financiera',
            mensaje,
            'Financiera',
            'Revisar devolución',
            observaciones=observacion,
            es_devolucion=True,
        )
        self._post_trazabilidad_cierre(
            'Cierre devuelto a Financiera',
            self._mensaje_trazabilidad_devolucion(mensaje, observacion),
        )
        return True

    def action_devolver_a_coordinacion_desde_juridica(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.JURIDICA.value:
            raise UserError(_('Este proceso solo puede devolverse desde Jurídica (revisa).'))
        coordinacion = self._usuarios_coordinacion_proyecto()
        if not coordinacion:
            raise UserError(_('No hay usuarios de Coordinación asociados al proyecto.'))

        observacion = self._tomar_observacion_etapa(requerida=True)
        mensaje = self._mensaje_devolucion_cierre('Jurídica', 'Coordinación')
        self.write({
            'estado': EstadoCierreContrato.SOLICITUD.value,
            'estado_retorno_devolucion': EstadoCierreContrato.JURIDICA.value,
            'observaciones_etapa': False,
        })
        self._enviar_correo_cierre(
            coordinacion,
            'Cierre de contrato: devolución a Coordinación',
            mensaje,
            'Coordinación',
            'Revisar devolución',
            observaciones=observacion,
            es_devolucion=True,
        )
        self._post_trazabilidad_cierre(
            'Cierre devuelto a Coordinación',
            self._mensaje_trazabilidad_devolucion(mensaje, observacion),
        )
        return True

    def action_devolver_a_juridica_desde_firma(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.FIRMA_COORDINADOR.value:
            raise UserError(_('Este proceso solo puede devolverse desde Coordinador (firma).'))
        juridicos = self._usuarios_retorno_juridica_cierre()
        if not juridicos:
            raise UserError(_('No hay usuarios jurídicos asociados al proyecto.'))

        observacion = self._tomar_observacion_etapa(requerida=True)
        mensaje = self._mensaje_devolucion_cierre('Coordinación', 'Jurídica')
        self.write({
            'estado': EstadoCierreContrato.JURIDICA.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        self._enviar_correo_cierre(
            juridicos,
            'Cierre de contrato: devolución a Jurídica',
            mensaje,
            'Jurídica',
            'Revisar devolución',
            observaciones=observacion,
            es_devolucion=True,
        )
        self._post_trazabilidad_cierre(
            'Cierre devuelto a Jurídica',
            self._mensaje_trazabilidad_devolucion(mensaje, observacion),
        )
        return True

    def action_devolver_a_coordinacion_desde_presupuesto(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.LIBERACION_RFC.value:
            raise UserError(_('Este proceso solo puede devolverse desde Presupuesto (libera RFC).'))
        if self.tipo_acta == 'incumplimiento':
            raise UserError(_('La liquidación unilateral debe devolverse a Jurídica.'))
        coordinacion = self._usuarios_coordinacion_proyecto()
        if not coordinacion:
            raise UserError(_('No hay usuarios de Coordinación asociados al proyecto.'))

        observacion = self._tomar_observacion_etapa(requerida=True)
        mensaje = self._mensaje_devolucion_cierre('Presupuesto', 'Coordinación')
        self.write({
            'estado': EstadoCierreContrato.FIRMA_COORDINADOR.value,
            'estado_retorno_devolucion': EstadoCierreContrato.LIBERACION_RFC.value,
            'observaciones_etapa': False,
        })
        self._enviar_correo_cierre(
            coordinacion,
            'Cierre de contrato: devolución a Coordinación',
            mensaje,
            'Coordinación',
            'Revisar devolución',
            observaciones=observacion,
            es_devolucion=True,
        )
        self._post_trazabilidad_cierre(
            'Cierre devuelto a Coordinación',
            self._mensaje_trazabilidad_devolucion(mensaje, observacion),
        )
        return True

    def action_devolver_a_juridica_desde_presupuesto_unilateral(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.LIBERACION_RFC.value:
            raise UserError(_('Este proceso solo puede devolverse desde la etapa de liberación del RFC.'))
        if self.tipo_acta != 'incumplimiento':
            raise UserError(_('Esta devolución solo aplica para liquidación unilateral.'))
        juridicos = self._usuarios_retorno_juridica_cierre()
        if not juridicos:
            raise UserError(_('No hay usuarios jurídicos asociados al proyecto.'))

        observacion = self._tomar_observacion_etapa(requerida=True)
        origen = (
            'Financiera'
            if self.env.user.has_group('Matriz.group_financiera_cierre_contrato')
            and not self.env.user.has_group('Matriz.group_presupuestos')
            else 'Presupuesto'
        )
        mensaje = self._mensaje_devolucion_cierre(origen, 'Jurídica')
        self.write({
            'estado': EstadoCierreContrato.JURIDICA.value,
            'estado_retorno_devolucion': False,
            'observaciones_etapa': False,
        })
        self._enviar_correo_cierre(
            juridicos,
            'Cierre de contrato: devolución a Jurídica',
            mensaje,
            'Jurídica',
            'Revisar devolución',
            observaciones=observacion,
            es_devolucion=True,
        )
        self._post_trazabilidad_cierre(
            'Cierre devuelto a Jurídica',
            self._mensaje_trazabilidad_devolucion(mensaje, observacion),
        )
        return True

    def _estado_final_contratacion_nuevo(self):
        self.ensure_one()
        if self.tipo_acta == 'anulacion':
            return 'anulado'
        return 'liquidado'

    def _aplicar_cierre_final_contratacion(self):
        self.ensure_one()
        contrato = self.contract_id
        if contrato.usa_flujo_contratacion_nuevo:
            contrato.with_context(skip_validacion_estado_flujo_nuevo=True).write({
                'estado_contratacion': self._estado_final_contratacion_nuevo(),
            })

    def action_finalizar_cierre_contractual(self):
        self.ensure_one()
        if self.estado != EstadoCierreContrato.LIBERACION_RFC.value:
            raise UserError(_('Este proceso solo puede finalizarse desde la etapa de liberación del RFC.'))
        mensaje_confirmacion = (
            'Debes confirmar la revisión de la liberación o que no existen '
            'saldos por liberar antes de finalizar.'
            if self.tipo_acta in ('normal', 'incumplimiento')
            else 'Debes confirmar que el RFC fue liberado antes de finalizar.'
        )
        self._validar_check_cierre('rfc_liberado', mensaje_confirmacion)
        valor_referencia = self._valor_referencia_documento_cierre()
        if self.tipo_acta == 'anulacion':
            if valor_referencia <= 0:
                raise UserError(_(
                    'El contrato no tiene un valor válido para efectuar la liberación total.'
                ))
            # Cubre también procesos históricos que ya estaban en liberación:
            # la anulación siempre devuelve exactamente el valor completo.
            self.write({'valor_liberado_rfc': valor_referencia})
        # En anulación siempre debe existir un valor por liberar. En los demás
        # cierres el manual indica "si aplica", por lo que cero es válido.
        if self.tipo_acta == 'anulacion' and self.valor_liberado_rfc <= 0:
            raise UserError(_(
                'Debes registrar un valor liberado del RFC mayor que cero antes de finalizar.'
            ))
        if self.tipo_acta != 'anulacion' and self.valor_liberado_rfc < 0:
            raise UserError(_('El valor liberado no puede ser negativo.'))
        # Compatibilidad con procesos iniciados antes de existir fondo_origen:
        # al finalizar se toma el fondo del FC y queda congelado en el cierre.
        fondo_origen = self.fondo_origen or self.contract_id.select_fondos
        if not fondo_origen:
            raise UserError(_(
                'El contrato no tiene seleccionado un fondo de origen. '
                'Debes corregir el FC antes de finalizar el cierre.'
            ))
        # El límite es el documento elegido al crear el cierre: contrato base
        # u otrosí específico. El valor efectivamente liberado puede ser menor.
        if self.valor_liberado_rfc > valor_referencia:
            raise UserError(_(
                'El valor liberado no puede superar el valor de referencia '
                '($ %s).'
            ) % f'{valor_referencia:,.2f}')
        observaciones_envio = self._tomar_observacion_etapa()
        valores = {
            'estado': EstadoCierreContrato.FINALIZADO.value,
            'fondo_origen': fondo_origen,
            'observaciones_etapa': False,
        }
        if not self.fecha_liberacion_rfc:
            valores['fecha_liberacion_rfc'] = fields.Datetime.now()
        if not self.liberado_por_id:
            valores['liberado_por_id'] = self.env.user.id
        self.write(valores)
        self._aplicar_cierre_final_contratacion()
        # El valor liberado queda explícito en el chatter del cierre y del FC;
        # así Presupuesto puede auditar cuánto regresó al disponible del proyecto.
        valor_liberado_formateado = formatLang(
            self.env,
            self.valor_liberado_rfc,
            currency_obj=self.currency_id,
        )
        tipo_cierre = dict(self._fields['tipo_acta'].selection).get(
            self.tipo_acta,
            self.tipo_acta or 'Cierre contractual',
        )
        mensaje = Markup(
            '<p>Proceso de cierre contractual finalizado. En flujo nuevo se '
            'actualiza el estado final del contrato; en flujo anterior se '
            'conserva el cierre tradicional.</p>'
            '<p><strong>Tipo de cierre:</strong> %s</p>'
            '<p><strong>Valor liberado:</strong> %s</p>'
        ) % (
            escape(tipo_cierre),
            escape(valor_liberado_formateado),
        )
        if observaciones_envio:
            mensaje += Markup(
                '<p><strong>Observaciones del área que aprueba:</strong><br/>%s</p>'
            ) % escape(observaciones_envio)
        self._post_trazabilidad_cierre(
            f'Cierre contractual finalizado: {tipo_cierre}',
            mensaje,
        )
        return True

    @api.depends('contract_id', 'tipo_acta')
    def _compute_name(self):
        etiquetas = dict(self._fields['tipo_acta'].selection)
        for record in self:
            contrato = record.contract_id.display_name or _('Contrato')
            tipo = etiquetas.get(record.tipo_acta, _('Cierre contractual'))
            record.name = f'{contrato} - {tipo}'

    @api.constrains(
        'tipo_acta',
        'confirma_sin_ejecucion_tecnica',
        'confirma_sin_ejecucion_financiera',
        'confirma_mutuo_acuerdo',
        'confirma_liquidacion_unilateral',
    )
    def _check_confirmaciones_tipo_acta(self):
        for record in self:
            if record.tipo_acta == 'anulacion' and not (
                record.confirma_sin_ejecucion_tecnica
                and record.confirma_sin_ejecucion_financiera
            ):
                raise ValidationError(_(
                    'Para solicitar un acta de anulación debes confirmar que '
                    'el contrato no inició ejecución técnica ni financiera.'
                ))
            if record.tipo_acta == 'normal' and not record.confirma_mutuo_acuerdo:
                raise ValidationError(_(
                    'Debes confirmar que las partes están de acuerdo con la liquidación.'
                ))
            if (
                record.tipo_acta == 'incumplimiento'
                and not record.confirma_liquidacion_unilateral
            ):
                raise ValidationError(_(
                    'Debes confirmar que el contratista no está de acuerdo con la liquidación.'
                ))

    @api.constrains('terminacion_anticipada', 'fecha_terminacion_solicitada')
    def _check_fecha_terminacion_solicitada(self):
        for record in self:
            if (
                record.terminacion_anticipada
                and not record.fecha_terminacion_solicitada
            ):
                raise ValidationError(_(
                    'Debes indicar la fecha de terminación anticipada solicitada.'
                ))


class ContratacionesFuvCierreGuiaUsuario(models.TransientModel):
    _name = 'contrataciones_fuv.cierre.guia.usuario'
    _description = 'Guía de usuario del cierre contractual'


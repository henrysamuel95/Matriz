# Implementa el asistente que solicita un cierre contractual y crea el proceso con sus datos iniciales.
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang

from .constantes import EstadoCierreContrato


class CloseContractWizardProceso(models.TransientModel):
    _inherit = 'contrataciones_fuv.close.wizard'

    proyecto_id = fields.Many2one(
        related='contract_id.proyecto_relacionado',
        string='Proyecto',
        readonly=True,
    )
    contratista_nombre = fields.Char(
        related='contract_id.new_name',
        string='Contratista',
        readonly=True,
    )
    estado_contrato = fields.Selection(
        related='contract_id.estado_contratacion',
        string='Estado del contrato',
        readonly=True,
    )
    currency_id = fields.Many2one(
        related='contract_id.currency_id',
        string='Moneda',
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
    )
    razones_solicitud = fields.Text(
        string='Razones de la solicitud',
        required=True,
    )
    objeto_cierre = fields.Selection(
        [
            ('contrato', 'Contrato principal'),
            ('otrosi', 'Otrosí que modificó el valor'),
        ],
        string='Documento que se cerrará',
        default='contrato',
        required=True,
    )
    relacion_cierre_seleccionada = fields.Selection(
        selection='_selection_relaciones_cierre',
        string='Seleccionar contrato u otrosí',
        default='contrato',
        required=True,
    )
    otrosi_candidato_ids = fields.Many2many(
        'otro_si',
        string='Otrosí disponibles',
        compute='_compute_datos_otrosi_cierre',
    )
    otrosi_id = fields.Many2one(
        'otro_si',
        string='Otrosí relacionado',
    )
    otrosi_descripcion = fields.Char(
        string='Detalle del otrosí',
        compute='_compute_datos_otrosi_cierre',
    )
    valor_contrato_original = fields.Monetary(
        string='Valor original del contrato',
        currency_field='currency_id',
        compute='_compute_datos_otrosi_cierre',
    )
    valor_otrosi_formalizados = fields.Monetary(
        string='Valor de los otrosí',
        currency_field='currency_id',
        compute='_compute_datos_otrosi_cierre',
    )
    valor_otrosi_seleccionado = fields.Monetary(
        string='Valor del otrosí seleccionado',
        currency_field='currency_id',
        compute='_compute_datos_otrosi_cierre',
    )
    valor_contractual_actualizado = fields.Monetary(
        string='Valor contractual actualizado',
        currency_field='currency_id',
        compute='_compute_datos_otrosi_cierre',
    )
    valor_documento_seleccionado = fields.Monetary(
        string='Valor total seleccionado',
        currency_field='currency_id',
        compute='_compute_datos_otrosi_cierre',
    )
    terminacion_anticipada = fields.Boolean(string='Terminación anticipada')
    fecha_terminacion_solicitada = fields.Date(
        string='Fecha de terminación solicitada',
    )
    confirma_sin_ejecucion_tecnica = fields.Boolean(
        string='El contrato no inició ejecución técnica',
    )
    confirma_sin_ejecucion_financiera = fields.Boolean(
        string='El contrato no inició ejecución financiera',
    )
    confirma_mutuo_acuerdo = fields.Boolean(
        string='Las partes están de acuerdo con la liquidación',
    )
    confirma_liquidacion_unilateral = fields.Boolean(
        string='El contratista no está de acuerdo con la liquidación',
    )

    def _selection_relaciones_cierre(self):
        """Construye un único selector con el contrato y sus otrosí elegibles."""
        contrato = self[:1].contract_id if self else self.env['contrataciones_fuv']
        if not contrato:
            contrato = self.env['contrataciones_fuv'].browse(
                self.env.context.get('default_contract_id')
            ).exists()
        if not contrato:
            return [('contrato', _('Contrato principal'))]

        moneda = contrato.currency_id
        inicio = (
            fields.Date.to_string(contrato.fecha_iniciacion)
            if contrato.fecha_iniciacion else _('Sin fecha')
        )
        fin = (
            fields.Date.to_string(contrato.fecha_terminacion)
            if contrato.fecha_terminacion else _('Sin fecha')
        )
        valor = formatLang(
            self.env,
            contrato.valor_total_contrato or 0.0,
            currency_obj=moneda,
        )
        opciones = [(
            'contrato',
            _('Contrato principal | ID: %(id)s | Ini: %(inicio)s | '
              'Fin: %(fin)s | Valor: %(valor)s') % {
                'id': contrato.id,
                'inicio': inicio,
                'fin': fin,
                'valor': valor,
            },
        )]
        for otrosi in contrato._otrosi_formalizados_con_valor_para_cierre():
            inicio = (
                fields.Date.to_string(otrosi.fecha_iniciacion)
                if otrosi.fecha_iniciacion else _('Sin fecha')
            )
            fin = (
                fields.Date.to_string(otrosi.fecha_terminacion)
                if otrosi.fecha_terminacion else _('Sin fecha')
            )
            valor = formatLang(
                self.env,
                otrosi.valor_total_contrato or 0.0,
                currency_obj=otrosi.currency_id or moneda,
            )
            opciones.append((
                'otrosi_%s' % otrosi.id,
                _('%(detalle)s | ID: %(id)s | Ini: %(inicio)s | '
                  'Fin: %(fin)s | Valor: %(valor)s') % {
                    'detalle': self._descripcion_otrosi(otrosi),
                    'id': otrosi.id,
                    'inicio': inicio,
                    'fin': fin,
                    'valor': valor,
                },
            ))
        return opciones

    def _descripcion_otrosi(self, otrosi):
        """Devuelve el tipo y número legible de un otrosí."""
        if otrosi.adicion_prorroga or (otrosi.adicion and otrosi.prorroga):
            return _('Adición y prórroga N.° %s') % (
                otrosi.adicion_prorroga_number or otrosi.id
            )
        if otrosi.adicion:
            return _('Adición N.° %s') % (otrosi.adicion_number or otrosi.id)
        return _('Otrosí modificatorio N.° %s') % (
            otrosi.otro_si_modificatorio_number or otrosi.id
        )

    @api.depends('contract_id', 'contract_id.valor_total_contrato',
                 'contract_id.valor_del_otrosi', 'contract_id.total', 'tipo_acta')
    def _compute_datos_otrosi_cierre(self):
        for wizard in self:
            candidatos = self.env['otro_si']
            if wizard.contract_id:
                candidatos = (
                    wizard.contract_id._otrosi_formalizados_con_valor_para_cierre()
                )
            wizard.otrosi_candidato_ids = candidatos
            wizard.valor_contrato_original = (
                wizard.contract_id.valor_total_contrato or 0.0
            )
            wizard.valor_otrosi_formalizados = wizard.contract_id.valor_del_otrosi or 0.0
            wizard.valor_otrosi_seleccionado = (
                wizard.otrosi_id.valor_total_contrato or 0.0
            )
            # El FC ya calcula el contrato integral. No se recalculan sus
            # otrosí con filtros distintos ni se cierra uno individualmente.
            wizard.valor_contractual_actualizado = wizard.contract_id.total or 0.0
            wizard.valor_documento_seleccionado = (
                wizard.valor_contrato_original if wizard.tipo_acta == 'anulacion'
                else wizard.valor_contractual_actualizado
            )
            wizard.otrosi_descripcion = wizard._descripcion_otrosi_seleccionado()

    def _descripcion_otrosi_seleccionado(self):
        """Arma una etiqueta clara sin cambiar el nombre global de los otrosí."""
        self.ensure_one()
        otrosi = self.otrosi_id
        if not otrosi:
            return False
        if otrosi.adicion_prorroga or (otrosi.adicion and otrosi.prorroga):
            tipo = _('Adición y prórroga')
            numero = otrosi.adicion_prorroga_number
        elif otrosi.adicion:
            tipo = _('Adición')
            numero = otrosi.adicion_number
        else:
            tipo = _('Otrosí modificatorio con valor')
            numero = otrosi.otro_si_modificatorio_number
        return _('%(tipo)s N.° %(numero)s - %(estado)s') % {
            'tipo': tipo,
            'numero': numero or otrosi.id,
            'estado': dict(otrosi._fields['estado_contratacion'].selection).get(
                otrosi.estado_contratacion,
                otrosi.estado_contratacion,
            ),
        }

    @api.onchange('tipo_acta')
    def _onchange_tipo_acta_proceso(self):
        self.confirma_sin_ejecucion_tecnica = self.tipo_acta == 'anulacion'
        self.confirma_sin_ejecucion_financiera = self.tipo_acta == 'anulacion'
        self.confirma_mutuo_acuerdo = self.tipo_acta == 'normal'
        self.confirma_liquidacion_unilateral = self.tipo_acta == 'incumplimiento'
        if self.tipo_acta == 'anulacion':
            # Un acta de anulación corresponde a un contrato que nunca inició;
            # por definición no puede apuntar a un otrosí ejecutado.
            self.objeto_cierre = 'contrato'
            self.otrosi_id = False
            self.relacion_cierre_seleccionada = 'contrato'

    @api.onchange('relacion_cierre_seleccionada')
    def _onchange_relacion_cierre_seleccionada(self):
        """Sincroniza el selector visual con las relaciones guardadas."""
        seleccion = self.relacion_cierre_seleccionada or 'contrato'
        if seleccion == 'contrato':
            self.objeto_cierre = 'contrato'
            self.otrosi_id = False
            return
        if seleccion.startswith('otrosi_'):
            try:
                otrosi_id = int(seleccion.split('_', 1)[1])
            except (TypeError, ValueError):
                self.objeto_cierre = 'contrato'
                self.otrosi_id = False
                return
            self.objeto_cierre = 'otrosi'
            self.otrosi_id = self.env['otro_si'].browse(otrosi_id).exists()

    def action_confirmar_cierre(self):
        self.ensure_one()
        contrato = self.contract_id
        if not contrato:
            raise UserError(_('No se encontró el contrato relacionado.'))
        puede_iniciar = (
            self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_contratacion_juridico')
            or self.env.user.has_group('Matriz.group_contratacion_director_juridico')
        )
        if not puede_iniciar:
            raise UserError(_(
                'Solo Coordinación, Jurídica o Dirección Jurídica pueden iniciar '
                'el cierre contractual.'
            ))
        if contrato.estado_contratacion != 'contratado':
            raise UserError(_('El contrato debe estar en estado Contratado.'))
        if contrato.cierre_proceso_activo_id:
            raise UserError(_('Este contrato ya tiene un proceso de cierre activo.'))
        # No dependemos solamente del onchange: se sincroniza nuevamente al
        # guardar para proteger también llamadas RPC o automatizadas.
        # El cierre siempre corresponde al FC completo, incluso si una llamada
        # antigua envía todavía el selector de otrosí.
        self.objeto_cierre = 'contrato'
        self.otrosi_id = False
        candidatos = contrato._otrosi_formalizados_con_valor_para_cierre()
        if self.tipo_acta == 'anulacion' and self.objeto_cierre != 'contrato':
            raise UserError(_(
                'El acta de anulación solo puede relacionarse con el contrato principal.'
            ))
        if self.objeto_cierre == 'otrosi':
            if not self.otrosi_id:
                raise UserError(_('Debes seleccionar el otrosí que se cerrará.'))
            if self.otrosi_id not in candidatos:
                raise UserError(_(
                    'El otrosí seleccionado no pertenece a este contrato, no '
                    'modifica valor o todavía no está formalizado.'
                ))

        confirma_sin_ejecucion = self.tipo_acta == 'anulacion'
        confirma_mutuo_acuerdo = self.tipo_acta == 'normal'
        confirma_liquidacion_unilateral = self.tipo_acta == 'incumplimiento'
        proceso = self.env['contrataciones_fuv.cierre.proceso'].create({
            'contract_id': contrato.id,
            # Congela el fondo seleccionado en el FC. El reintegro futuro debe
            # regresar a este fondo y no depender de cambios posteriores del FC.
            'fondo_origen': contrato.select_fondos,
            'estado': EstadoCierreContrato.SOLICITUD.value,
            'tipo_acta': self.tipo_acta,
            # Fotografía contractual: conserva la relación y los valores tal
            # como estaban cuando Coordinación creó el cierre. Por ahora estos
            # campos son informativos y no alteran el reintegro ni el estado final.
            'objeto_cierre': self.objeto_cierre,
            'otrosi_id': self.otrosi_id.id or False,
            'valor_contrato_original': self.valor_contrato_original,
            'valor_otrosi_formalizados': self.valor_otrosi_formalizados,
            'valor_otrosi_seleccionado': self.valor_otrosi_seleccionado,
            'valor_contractual_actualizado': self.valor_contractual_actualizado,
            'valor_documento_cierre': self.valor_documento_seleccionado,
            'valor_integral_registrado': True,
            'razones_solicitud': self.razones_solicitud,
            'terminacion_anticipada': self.terminacion_anticipada,
            'fecha_terminacion_solicitada': self.fecha_terminacion_solicitada,
            'confirma_sin_ejecucion_tecnica': confirma_sin_ejecucion,
            'confirma_sin_ejecucion_financiera': confirma_sin_ejecucion,
            'confirma_mutuo_acuerdo': confirma_mutuo_acuerdo,
            'confirma_liquidacion_unilateral': confirma_liquidacion_unilateral,
        })
        proceso._crear_carpeta_cierre_contractual()
        tipo_acta = dict(proceso._fields['tipo_acta'].selection).get(
            proceso.tipo_acta,
            proceso.tipo_acta,
        )
        valor_actualizado = formatLang(
            self.env,
            self.valor_contractual_actualizado,
            currency_obj=self.currency_id,
        )
        mensaje = Markup(
            '<p><strong>Solicitud de cierre contractual guardada.</strong></p>'
            '<p><strong>Tipo:</strong> %s</p>'
            '<p><strong>Documento relacionado:</strong> %s</p>'
            '<p><strong>Valor contractual actualizado:</strong> %s</p>'
            '<p><strong>Creado por:</strong> %s</p>'
            '<p><strong>Razones:</strong> %s</p>'
            '<p>El proceso queda guardado. El Coordinador debe enviarlo a '
            'certificación financiera.</p>'
        ) % (
            escape(tipo_acta),
            escape(
                self.otrosi_descripcion
                if self.objeto_cierre == 'otrosi'
                else _('Contrato principal')
            ),
            escape(valor_actualizado),
            escape(self.env.user.name),
            escape(self.razones_solicitud),
        )
        contrato.message_post(body=mensaje, subtype_xmlid='mail.mt_note')
        proceso.message_post(body=mensaje)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Proceso de cierre contractual',
            'res_model': 'contrataciones_fuv.cierre.proceso',
            'view_mode': 'form',
            'view_id': self.env.ref(
                'Matriz.view_contrataciones_fuv_cierre_proceso_form'
            ).id,
            'res_id': proceso.id,
            'target': 'current',
        }

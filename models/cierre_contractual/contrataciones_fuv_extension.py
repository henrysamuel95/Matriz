# Extiende Contrataciones FUV para relacionar, consultar y abrir sus procesos de cierre contractual.
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import EstadoCierreContrato


class ContratacionesFuvCierre(models.Model):
    _inherit = 'contrataciones_fuv'

    def _otrosi_formalizados_con_valor_para_cierre(self):
        """Retorna los otrosí formalizados que realmente modifican valor.

        Se usa la relación directa ``otro_si_ids`` del FC; por eso no hace
        búsquedas recursivas en Drive ni en la base de datos. También conserva
        compatibilidad con el flujo anterior, cuyo estado final era
        ``contratado``.
        """
        self.ensure_one()
        estados_finales_nuevos = {
            'adicion_formalizada',
            'adicion_prorroga_formalizada',
            'modificatorio_formalizado',
        }

        def es_candidato(otrosi):
            modifica_valor = (
                otrosi.adicion
                or otrosi.adicion_prorroga
                or (
                    otrosi.otro_si_modificatorio
                    and otrosi.tipo_modificacion_modificatorio in ('valor', 'ambos')
                )
            )
            esta_formalizado = (
                otrosi.estado_contratacion == 'contratado'
                or otrosi.estado_contratacion in estados_finales_nuevos
            )
            return (
                modifica_valor
                and esta_formalizado
                and bool(otrosi.valor_total_contrato)
            )

        return self.otro_si_ids.filtered(es_candidato)

    cierre_proceso_ids = fields.One2many(
        'contrataciones_fuv.cierre.proceso',
        'contract_id',
        string='Procesos de cierre',
    )
    cierre_proceso_activo_id = fields.Many2one(
        'contrataciones_fuv.cierre.proceso',
        string='Proceso de cierre activo',
        compute='_compute_cierre_proceso_activo',
    )
    cierre_proceso_ultimo_id = fields.Many2one(
        'contrataciones_fuv.cierre.proceso',
        string='Último proceso de cierre',
        compute='_compute_cierre_proceso_ultimo',
    )
    recursos_liberados_cierre = fields.Monetary(
        string='Recursos liberados',
        currency_field='currency_id',
        compute='_compute_recursos_liberados_cierre',
        help='Total liberado en procesos de cierre contractual finalizados del proyecto.',
    )
    valor_liberado_cierre_contrato = fields.Monetary(
        string='Recursos liberados del contrato',
        currency_field='currency_id',
        compute='_compute_valor_liberado_cierre_contrato',
        help=(
            'Total liberado únicamente por los cierres finalizados de este FC. '
            'Se usa en la tarjeta kanban para no repetir el total del proyecto.'
        ),
    )

    @api.depends('cierre_proceso_ids.estado')
    def _compute_cierre_proceso_activo(self):
        estados_finales = (
            EstadoCierreContrato.FINALIZADO.value,
            EstadoCierreContrato.CANCELADO.value,
        )
        for record in self:
            record.cierre_proceso_activo_id = record.cierre_proceso_ids.filtered(
                lambda proceso: proceso.estado not in estados_finales
            )[:1]

    @api.depends('cierre_proceso_ids', 'cierre_proceso_ids.estado')
    def _compute_cierre_proceso_ultimo(self):
        for record in self:
            record.cierre_proceso_ultimo_id = record.cierre_proceso_ids.sorted(
                key=lambda proceso: (proceso.fecha_solicitud, proceso.id),
                reverse=True,
            )[:1]

    @api.depends('proyecto_relacionado')
    def _compute_recursos_liberados_cierre(self):
        proceso_model = self.env['contrataciones_fuv.cierre.proceso'].sudo()
        for record in self:
            if not record.proyecto_relacionado:
                record.recursos_liberados_cierre = 0.0
                continue
            procesos_finalizados = proceso_model.search([
                ('proyecto_id', '=', record.proyecto_relacionado.id),
                ('estado', '=', EstadoCierreContrato.FINALIZADO.value),
                ('valor_liberado_rfc', '>', 0),
            ])
            record.recursos_liberados_cierre = sum(
                procesos_finalizados.mapped('valor_liberado_rfc')
            )

    @api.depends(
        'cierre_proceso_ids.estado',
        'cierre_proceso_ids.valor_liberado_rfc',
    )
    def _compute_valor_liberado_cierre_contrato(self):
        """Calcula el valor liberado directamente por cada contrato."""
        for record in self:
            cierres_finalizados = record.cierre_proceso_ids.filtered(
                lambda proceso: (
                    proceso.estado == EstadoCierreContrato.FINALIZADO.value
                    and proceso.valor_liberado_rfc > 0
                )
            )
            record.valor_liberado_cierre_contrato = sum(
                cierres_finalizados.mapped('valor_liberado_rfc')
            )

    def _obtener_recursos_liberados_por_fondo(self):
        """Devuelve los reintegros finalizados del proyecto separados por fondo.

        Esta función no escribe sobre los saldos. Los saldos de Contrataciones
        FUV son calculados, por lo que cada fórmula la consulta y suma solamente
        el reintegro correspondiente a su fondo. Buscar procesos finalizados
        hace que el cálculo sea idempotente: recalcular no duplica el dinero.
        """
        self.ensure_one()
        liberados = {
            'es_produccion': 0.0,
            'es_personal_fuv': 0.0,
            'contratos_vco': 0.0,
        }
        if not self.proyecto_relacionado:
            return liberados

        procesos = self.env['contrataciones_fuv.cierre.proceso'].sudo().search([
            ('proyecto_id', '=', self.proyecto_relacionado.id),
            ('estado', '=', EstadoCierreContrato.FINALIZADO.value),
            ('valor_liberado_rfc', '>', 0),
        ])
        for proceso in procesos:
            # Compatibilidad con cierres creados antes de agregar fondo_origen:
            # para consultarlos se usa el fondo que conserva su contrato padre.
            fondo = proceso.fondo_origen or proceso.contract_id.select_fondos
            if fondo in liberados:
                liberados[fondo] += proceso.valor_liberado_rfc
        return liberados

    def action_close_contract(self):
        self.ensure_one()
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
        if self.estado_contratacion != 'contratado':
            raise UserError(_(
                'El cierre contractual solo puede solicitarse para un contrato contratado.'
            ))
        if self.cierre_proceso_activo_id:
            raise UserError(_('Este contrato ya tiene un proceso de cierre activo.'))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Guardar solicitud de cierre contractual',
            'res_model': 'contrataciones_fuv.close.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_contract_id': self.id,
                'default_confirmar_cierre': True,
            },
        }

    def action_view_cierre_contractual(self):
        self.ensure_one()
        puede_consultar = any((
            self.env.user.has_group('Matriz.group_contratacion_coordinador'),
            self.env.user.has_group('Matriz.group_contratacion_asis_coordinador'),
            self.env.user.has_group('Matriz.group_contratacion_juridico'),
            self.env.user.has_group('Matriz.group_contratacion_director_juridico'),
        ))
        if not puede_consultar:
            raise UserError(_(
                'Solo Coordinación, Asistencia de coordinación, Jurídica o '
                'Dirección Jurídica pueden consultar el cierre contractual.'
            ))
        proceso = self.cierre_proceso_ultimo_id
        if not proceso:
            raise UserError(_('Este contrato no tiene procesos de cierre contractual.'))
        if proceso.cierre_drive_folder_id and self.drive_file_id:
            proceso._asegurar_ubicacion_local_carpeta_cierre(
                proceso.cierre_drive_folder_id,
                self.drive_file_id,
            )
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

    def unlink(self):
        procesos_cierre = self.mapped('cierre_proceso_ids')
        if procesos_cierre:
            procesos_cierre.unlink()
        return super().unlink()

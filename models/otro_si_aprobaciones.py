#!/usr/bin/python
from enum import Enum
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from .flujo_configuracion import obtener_fecha_inicio_nuevo_flujo_contratacion

_logger = logging.getLogger(__name__)


# Centraliza los valores técnicos del flujo de Otrosí.
# El nombre en mayúscula se usa en Python y el texto entre comillas es el valor
# que Odoo almacena en la base de datos. No cambies los valores ya existentes.
class EstadoOtroSi(str, Enum):
    COORDINADOR = 'coordinador'
    FINANCIERA = 'financiera'
    VENTANILLA = 'ventanilla'
    PRESUPUESTO = 'presupuesto'
    DIRECTOR_JURIDICO = 'director_juridico'
    JURIDICO = 'juridico'
    JURIDICO_1 = 'juridico_1'
    RECURSOS_HUMANOS = 'recursos_humanos'
    JURIDICO_2 = 'juridico_2'
    FIRMA = 'firma'
    CONTRATADO = 'contratado'
    SUSPENDIDO = 'suspendido'
    ADICION_FORMALIZADA = 'adicion_formalizada'
    PRORROGA_FORMALIZADA = 'prorroga_formalizada'
    ADICION_PRORROGA_FORMALIZADA = 'adicion_prorroga_formalizada'
    MODIFICATORIO_FORMALIZADO = 'modificatorio_formalizado'
    REINICIO_FORMALIZADO = 'reinicio_formalizado'
    SUSPENSION_PRORROGADA = 'suspension_prorrogada'


# Solo se agregan aquí los estados que no existen en el Selection original de
# otro_si.py. Los estados históricos siguen intactos en el modelo base.
ESTADOS_OTRO_SI_NUEVOS = [
    (EstadoOtroSi.VENTANILLA.value, 'Ventanilla'),
    (EstadoOtroSi.PRESUPUESTO.value, 'Presupuesto'),
    (EstadoOtroSi.DIRECTOR_JURIDICO.value, 'Dir. Jurídico'),
    (EstadoOtroSi.JURIDICO_1.value, 'Jurídico 1 - Revisión'),
    (EstadoOtroSi.JURIDICO_2.value, 'Jurídico 2 - Contrato'),
    (EstadoOtroSi.FIRMA.value, 'Firma - Contrato firmado'),
    (EstadoOtroSi.SUSPENDIDO.value, 'Suspendido'),
    (EstadoOtroSi.ADICION_FORMALIZADA.value, 'Adición formalizada'),
    (EstadoOtroSi.PRORROGA_FORMALIZADA.value, 'Prórroga formalizada'),
    (EstadoOtroSi.ADICION_PRORROGA_FORMALIZADA.value, 'Adición/Prórroga formalizada'),
    (EstadoOtroSi.MODIFICATORIO_FORMALIZADO.value, 'Modificatorio formalizado'),
    (EstadoOtroSi.REINICIO_FORMALIZADO.value, 'Reinicio formalizado'),
    (EstadoOtroSi.SUSPENSION_PRORROGADA.value, 'Suspensión prorrogada'),
]


class OtroSiAprobaciones(models.Model):
    _inherit = 'otro_si'

    def _grupos_edicion_por_estado_otrosi_nuevo(self):
        return {
            EstadoOtroSi.COORDINADOR.value: (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            EstadoOtroSi.JURIDICO.value: (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            EstadoOtroSi.VENTANILLA.value: (
                'Matriz.group_contratacion_ventanilla_unica',
            ),
            EstadoOtroSi.PRESUPUESTO.value: ('Matriz.group_presupuestos',),
            EstadoOtroSi.DIRECTOR_JURIDICO.value: (
                'Matriz.group_contratacion_director_juridico',
            ),
            EstadoOtroSi.JURIDICO_1.value: (
                'Matriz.group_contratacion_juridico',
            ),
            EstadoOtroSi.RECURSOS_HUMANOS.value: (
                'Matriz.group_contratacion_recursos',
            ),
            EstadoOtroSi.JURIDICO_2.value: (
                'Matriz.group_contratacion_juridico',
            ),
            EstadoOtroSi.FIRMA.value: (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
        }

    def _validar_edicion_por_estado_otrosi_nuevo(self):
        if (
            self.env.su
            or self.env.context.get('skip_validacion_estado_otrosi_nuevo')
            or self.env.user.has_group('Matriz.group_soporte')
        ):
            return

        grupos_por_estado = self._grupos_edicion_por_estado_otrosi_nuevo()
        for record in self:
            if not record.usa_flujo_otrosi_nuevo:
                continue
            grupos_permitidos = grupos_por_estado.get(
                record.estado_contratacion
            )
            if grupos_permitidos and not any(
                self.env.user.has_group(grupo)
                for grupo in grupos_permitidos
            ):
                nombre_estado = record._nombre_estado_otrosi_nuevo(
                    record.estado_contratacion
                )
                raise UserError(_(
                    "No tienes permisos para editar este otrosí en el estado %s."
                ) % nombre_estado)

    def _requiere_validacion_edicion_otrosi_nuevo(self, vals):
        """Evita bloquear la apertura por recomputos técnicos de Odoo."""
        campos_tecnicos = {
            'message_follower_ids',
            'message_ids',
            'activity_ids',
            'activity_state',
            'activity_user_id',
            'activity_type_id',
            'activity_date_deadline',
            'activity_summary',
            'activity_exception_decoration',
            'activity_exception_icon',
            'otrosi_number_1',
            'reinicio_number_2',
            'suspension_number_3',
            'adicion_number_4',
            'otro_si_modificatorio_number_5',
            'acta_de_prorroga_de_la_suspension_number_6',
        }
        for campo in vals:
            if campo in campos_tecnicos:
                continue

            field = self._fields.get(campo)
            if field and field.compute:
                continue
            if campo.endswith('_visible') or campo.endswith('_actualizar_visible'):
                continue

            return True
        return False

    estado_contratacion = fields.Selection(
        selection_add=ESTADOS_OTRO_SI_NUEVOS,
        ondelete={
            EstadoOtroSi.VENTANILLA.value: 'set default',
            EstadoOtroSi.PRESUPUESTO.value: 'set default',
            EstadoOtroSi.DIRECTOR_JURIDICO.value: 'set default',
            EstadoOtroSi.JURIDICO_1.value: 'set default',
            EstadoOtroSi.JURIDICO_2.value: 'set default',
            EstadoOtroSi.FIRMA.value: 'set default',
            EstadoOtroSi.SUSPENDIDO.value: 'set default',
            EstadoOtroSi.ADICION_FORMALIZADA.value: 'set default',
            EstadoOtroSi.PRORROGA_FORMALIZADA.value: 'set default',
            EstadoOtroSi.ADICION_PRORROGA_FORMALIZADA.value: 'set default',
            EstadoOtroSi.MODIFICATORIO_FORMALIZADO.value: 'set default',
            EstadoOtroSi.REINICIO_FORMALIZADO.value: 'set default',
            EstadoOtroSi.SUSPENSION_PRORROGADA.value: 'set default',
        },
    )

    usa_flujo_otrosi_nuevo = fields.Boolean(
        string='Usa flujo nuevo de Otrosí',
        compute='_compute_usa_flujo_otrosi_nuevo',
        store=True,
        readonly=True,
    )

    flujo_otrosi_version = fields.Selection(
        [
            ('anterior', 'Flujo anterior'),
            ('nuevo', 'Flujo nuevo'),
        ],
        string='Flujo de Otrosí',
        compute='_compute_usa_flujo_otrosi_nuevo',
        store=True,
        readonly=True,
    )

    observaciones_flujo_nuevo = fields.Html(
        string='Observaciones del flujo nuevo',
        tracking=False,
    )
    puede_editar_observaciones_flujo_nuevo = fields.Boolean(
        compute='_compute_puede_editar_observaciones_flujo_nuevo',
    )
    show_aprueba_financiera_otrosi_nuevo = fields.Boolean(
        compute='_compute_show_aprueba_otrosi_nuevo',
    )
    show_aprueba_juridico_otrosi_nuevo = fields.Boolean(
        compute='_compute_show_aprueba_otrosi_nuevo',
    )

    @api.depends(
        'select_persona', 'show_aprueba_financiera',
        'show_aprueba_financiera_j', 'show_aprueba_juridico',
        'aprueba_j_h_contratos_otro_juridico', 'suspension', 'reinicio',
        'acta_de_prorroga_de_la_suspension',
    )
    def _compute_show_aprueba_otrosi_nuevo(self):
        for record in self:
            es_juridica = record.select_persona == 'persona_juridica'
            record.show_aprueba_financiera_otrosi_nuevo = (
                record.show_aprueba_financiera_j
                if es_juridica
                else record.show_aprueba_financiera
            )
            omite_revision_inicial = (
                record.suspension
                or record.reinicio
                or record.acta_de_prorroga_de_la_suspension
            )
            record.show_aprueba_juridico_otrosi_nuevo = (
                True
                if omite_revision_inicial
                else (
                    record.aprueba_j_h_contratos_otro_juridico
                    if es_juridica
                    else record.show_aprueba_juridico
                )
            )

    ultimo_estado_devolucion_otrosi = fields.Selection(
        selection=ESTADOS_OTRO_SI_NUEVOS + [
            (EstadoOtroSi.COORDINADOR.value, 'Coordinador'),
            (EstadoOtroSi.FINANCIERA.value, 'Financiera'),
            (EstadoOtroSi.JURIDICO.value, 'Jurídico'),
            (EstadoOtroSi.RECURSOS_HUMANOS.value, 'Recursos humanos'),
            (EstadoOtroSi.CONTRATADO.value, 'Contratado'),
        ],
        string='Último estado que devolvió',
        tracking=True,
    )
    ultimo_usuario_devolucion_otrosi_id = fields.Many2one(
        'res.users',
        string='Último usuario que devolvió',
        tracking=True,
    )
    estado_retorno_devolucion_otrosi = fields.Selection(
        selection=ESTADOS_OTRO_SI_NUEVOS + [
            (EstadoOtroSi.COORDINADOR.value, 'Coordinador'),
            (EstadoOtroSi.FINANCIERA.value, 'Financiera'),
            (EstadoOtroSi.JURIDICO.value, 'Jurídico'),
            (EstadoOtroSi.RECURSOS_HUMANOS.value, 'Recursos humanos'),
            (EstadoOtroSi.CONTRATADO.value, 'Contratado'),
        ],
        string='Estado al que debe retornar',
        tracking=True,
    )

    @api.depends(
        'create_date',
        'contratacion_fuv_en_id',
        'contratacion_fuv_en_id.usa_flujo_contratacion_nuevo',)
    
    def _compute_usa_flujo_otrosi_nuevo(self):
        for record in self:
            if (
                record.contratacion_fuv_en_id
                and 'usa_flujo_contratacion_nuevo'
                in record.contratacion_fuv_en_id._fields
            ):
                usa_flujo_nuevo = bool(
                    record.contratacion_fuv_en_id.usa_flujo_contratacion_nuevo
                )
                record.usa_flujo_otrosi_nuevo = usa_flujo_nuevo
                record.flujo_otrosi_version = (
                    'nuevo' if usa_flujo_nuevo else 'anterior'
                )
                continue

            fecha_base = record.create_date
            if not fecha_base:
                fecha_base = fields.Date.context_today(record)
            if isinstance(fecha_base, str):
                fecha_base = fields.Datetime.to_datetime(fecha_base)
            if hasattr(fecha_base, 'date'):
                fecha_base = fecha_base.date()

            fecha_inicio_nuevo_flujo = obtener_fecha_inicio_nuevo_flujo_contratacion(
                record.env
            )
            usa_flujo_nuevo = fecha_base >= fecha_inicio_nuevo_flujo
            record.usa_flujo_otrosi_nuevo = usa_flujo_nuevo
            record.flujo_otrosi_version = (
                'nuevo' if usa_flujo_nuevo else 'anterior'
            )

    def _grupos_observaciones_flujo_nuevo(self):
        return {
            EstadoOtroSi.COORDINADOR.value: (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            EstadoOtroSi.JURIDICO.value: (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            EstadoOtroSi.VENTANILLA.value: (
                'Matriz.group_contratacion_ventanilla_unica',
            ),
            EstadoOtroSi.PRESUPUESTO.value: ('Matriz.group_presupuestos',),
            EstadoOtroSi.DIRECTOR_JURIDICO.value: (
                'Matriz.group_contratacion_director_juridico',
            ),
            EstadoOtroSi.JURIDICO_1.value: ('Matriz.group_contratacion_juridico',),
            EstadoOtroSi.RECURSOS_HUMANOS.value: (
                'Matriz.group_contratacion_recursos',
            ),
            EstadoOtroSi.JURIDICO_2.value: ('Matriz.group_contratacion_juridico',),
            EstadoOtroSi.FIRMA.value: ('Matriz.group_contratacion_coordinador',),
        }

    def _usuario_puede_editar_observaciones_flujo_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_otrosi_nuevo:
            return False
        if self.env.user.has_group('Matriz.group_soporte'):
            return True

        grupos = self._grupos_observaciones_flujo_nuevo().get(
            self.estado_contratacion, ()
        )
        if not grupos or not any(
            self.env.user.has_group(xml_id) for xml_id in grupos
        ):
            return False

        if self.estado_contratacion in (
            EstadoOtroSi.JURIDICO_1.value,
            EstadoOtroSi.JURIDICO_2.value,
        ):
            return self.env.user in self.usuarios_juridico_ids
        return True

    @api.depends(
        'estado_contratacion',
        'usa_flujo_otrosi_nuevo',
        'usuarios_juridico_ids',
    )
    def _compute_puede_editar_observaciones_flujo_nuevo(self):
        for record in self:
            record.puede_editar_observaciones_flujo_nuevo = (
                record._usuario_puede_editar_observaciones_flujo_nuevo()
            )

    def write(self, vals):
        previous_juridico_users_by_record = {}
        if 'usuarios_juridico_ids' in vals:
            previous_juridico_users_by_record = {
                record.id: set(record.usuarios_juridico_ids.ids)
                for record in self
            }

        if self._requiere_validacion_edicion_otrosi_nuevo(vals):
            self._validar_edicion_por_estado_otrosi_nuevo()
        if (
            'observaciones_flujo_nuevo' in vals
            and not self.env.context.get('limpiar_observaciones_flujo_nuevo')
        ):
            for record in self:
                if (
                    record.usa_flujo_otrosi_nuevo
                    and not record._usuario_puede_editar_observaciones_flujo_nuevo()
                ):
                    raise UserError(
                        _(
                            "Solo el responsable del estado actual puede editar "
                            "las observaciones del flujo nuevo."
                        )
                    )
        res = super().write(vals)

        if (
            'usuarios_juridico_ids' in vals
            and not self.env.context.get('skip_auto_enviar_juridico_otrosi_nuevo')
        ):
            for record in self:
                previous_users = previous_juridico_users_by_record.get(
                    record.id, set()
                )
                added_users = set(record.usuarios_juridico_ids.ids) - previous_users
                if (
                    added_users
                    and record.usa_flujo_otrosi_nuevo
                    and record.estado_contratacion == EstadoOtroSi.DIRECTOR_JURIDICO.value
                ):
                    record.with_context(
                        skip_auto_enviar_juridico_otrosi_nuevo=True
                    ).enviar_juridico_asignado_otrosi_nuevo()

        return res

    def _requiere_ventanilla_presupuesto_otrosi_nuevo(self):
        self.ensure_one()
        if self.adicion or self.adicion_prorroga:
            return True
        if self.otro_si_modificatorio:
            return self.tipo_modificacion_modificatorio in ('valor', 'ambos')
        return False

    def _omite_recursos_humanos_otrosi_nuevo(self):
        self.ensure_one()
        # En el flujo nuevo las personas juridicas, suspensiones, reinicios y
        # prorrogas de suspension no pasan
        # por Recursos Humanos. Juridico hace la revision y prepara el documento
        # en una sola etapa antes de enviarlo a firma.
        return (
            self.select_persona == 'persona_juridica'
            or self.suspension
            or self.reinicio
            or self.acta_de_prorroga_de_la_suspension
        )

    def _estado_final_otrosi_nuevo(self):
        self.ensure_one()
        if self.suspension:
            return EstadoOtroSi.SUSPENDIDO.value
        if self.reinicio:
            return EstadoOtroSi.REINICIO_FORMALIZADO.value
        if self.acta_de_prorroga_de_la_suspension:
            return EstadoOtroSi.SUSPENSION_PRORROGADA.value
        if self.adicion_prorroga:
            return EstadoOtroSi.ADICION_PRORROGA_FORMALIZADA.value
        if self.adicion:
            return EstadoOtroSi.ADICION_FORMALIZADA.value
        if self.prorroga:
            return EstadoOtroSi.PRORROGA_FORMALIZADA.value
        if self.otro_si_modificatorio:
            return EstadoOtroSi.MODIFICATORIO_FORMALIZADO.value
        return EstadoOtroSi.CONTRATADO.value

    def _validar_aprobaciones_otrosi_nuevo(self, area):
        self.ensure_one()
        if area == 'financiera':
            aprobado = (
                self.aprueba_f_cdp_juridico
                if self.select_persona == 'persona_juridica'
                else self.aprueba_f_a_su_cdp
            )
            if not aprobado:
                raise UserError(_(
                    "Debes revisar y marcar como aprobados todos los requisitos "
                    "financieros antes de enviar el otrosí al siguiente estado."
                ))
        elif area == 'juridico':
            if (
                self.suspension
                or self.reinicio
                or self.acta_de_prorroga_de_la_suspension
            ):
                return
            aprobado = (
                self.aprueba_j_h_contratos_otro_juridico
                if self.select_persona == 'persona_juridica'
                else self.aprueba_j_h_su_arl
            )
            if not aprobado:
                raise UserError(_(
                    "Debes revisar y marcar como aprobados todos los documentos "
                    "jurídicos requeridos antes de continuar."
                ))
        elif area == 'recursos_humanos' and not self.aprueba_h_h_su_arl:
            raise UserError(_(
                "Debes revisar y marcar como aprobados todos los documentos "
                "requeridos por Recursos Humanos antes de continuar."
            ))

    def _validar_grupos_otrosi_nuevo(self, *xml_ids):
        self.ensure_one()
        if any(self.env.user.has_group(xml_id) for xml_id in xml_ids):
            return
        raise UserError(_("Tu usuario no tiene el rol requerido para ejecutar esta acción."))

    def _validar_juridico_asignado_otrosi_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_otrosi_nuevo:
            return
        if not self.env.user.has_group('Matriz.group_contratacion_juridico'):
            raise UserError(_("Solo el equipo Jurídico puede ejecutar esta acción."))
        if self.env.user not in self._juridicos_asignados_otrosi_nuevo():
            raise UserError(_(
                "Solo el jurídico asignado por el Director Jurídico puede "
                "ejecutar esta acción en el flujo nuevo."
            ))

    def _validar_documento_juridico_otrosi_nuevo(self):
        self.ensure_one()
        if self.select_persona == 'persona_juridica':
            documento = self.contratos_otro_juridico_id
            documento_visible = self.contratos_otro_juridico_doc_visible
        else:
            documento = self.contratos_otro_id
            documento_visible = self.contratos_otro_doc_visible
        if not documento or documento_visible:
            raise UserError(_("Jurídico debe cargar el documento del otrosí antes de enviarlo a Firma."))

    def _validar_documento_firmado_otrosi_nuevo(self):
        self.ensure_one()
        if self.select_persona == 'persona_juridica':
            documento = self.contratos_firmado_juridico_id
            documento_visible = self.contratos_firmado_juridico_doc_visible
        else:
            documento = self.contratos_firmado_id
            documento_visible = self.contratos_firmado_natu_doc_visible
        if not documento or documento_visible:
            raise UserError(_("Debes cargar el otrosí firmado antes de finalizar el proceso."))

    def _validar_carga_estado_otrosi_nuevo(self, estado, *xml_ids):
        self.ensure_one()
        if not self.usa_flujo_otrosi_nuevo:
            return
        self._validar_grupos_otrosi_nuevo(*xml_ids)
        if self.estado_contratacion != estado:
            raise UserError(
                _("Este documento solo puede cargarse durante el estado %s.")
                % self._nombre_estado_otrosi_nuevo(estado)
            )
        if estado in (
            EstadoOtroSi.JURIDICO_1.value,
            EstadoOtroSi.JURIDICO_2.value,
        ):
            self._validar_juridico_asignado_otrosi_nuevo()

    def action_upload_contratos_otro_juridico_doc(self):
        estado_requerido = (
            EstadoOtroSi.JURIDICO_1.value
            if self.usa_flujo_otrosi_nuevo
            and self.select_persona == 'persona_juridica'
            else EstadoOtroSi.JURIDICO_2.value
        )
        self._validar_carga_estado_otrosi_nuevo(
            estado_requerido,
            'Matriz.group_contratacion_juridico',
        )
        return super().action_upload_contratos_otro_juridico_doc()

    def action_upload_contratos_otro_doc(self):
        estado_requerido = (
            EstadoOtroSi.JURIDICO_1.value
            if (
                self.usa_flujo_otrosi_nuevo
                and (
                    self.suspension
                    or self.reinicio
                    or self.acta_de_prorroga_de_la_suspension
                )
            )
            else EstadoOtroSi.JURIDICO_2.value
        )
        self._validar_carga_estado_otrosi_nuevo(
            estado_requerido,
            'Matriz.group_contratacion_juridico',
        )
        return super().action_upload_contratos_otro_doc()

    def action_upload_contratos_firmado_juridico_doc(self):
        self._validar_carga_estado_otrosi_nuevo(
            EstadoOtroSi.FIRMA.value,
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_asis_coordinador',
        )
        return super().action_upload_contratos_firmado_juridico_doc()

    def action_upload_contratos_firmado_natu_doc(self):
        self._validar_carga_estado_otrosi_nuevo(
            EstadoOtroSi.FIRMA.value,
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_asis_coordinador',
        )
        return super().action_upload_contratos_firmado_natu_doc()

    def _nombre_estado_otrosi_nuevo(self, estado):
        selection = dict(self._fields['estado_contratacion'].selection)
        return selection.get(estado, estado or '')

    def _nombre_proyecto_otrosi(self):
        self.ensure_one()
        return (
            self.proyecto_relacionado.new_name
            or self.proyecto_relacionado.name
            or 'Proyecto'
        )

    def _formulario_url_otrosi(self):
        self.ensure_one()
        action = self.env.ref('Matriz.action_open_otrosi_form_direct', raise_if_not_found=False)
        if not action:
            action = self.env.ref('Matriz.action_open_otrosi', raise_if_not_found=False)
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        params = [
            f'id={self.id}',
            'model=otro_si',
            'view_type=form',
            'cids=1',
        ]
        if action:
            params.append(f'action={action.id}')
        return f"{base_url}/web#{'&'.join(params)}"

    def _texto_observacion_otrosi_nuevo(self, observacion):
        return (html2plaintext(observacion or '') or '').strip()

    def _tipo_otrosi_nuevo(self):
        self.ensure_one()
        tipos = []
        if self.adicion:
            tipos.append('Adición')
        if self.prorroga:
            tipos.append('Prórroga')
        if self.adicion_prorroga:
            tipos.append('Adición/Prórroga')
        if self.otro_si_modificatorio:
            tipos.append('Modificatorio')
        if self.suspension:
            tipos.append('Suspensión')
        if self.reinicio:
            tipos.append('Reinicio')
        if self.acta_de_prorroga_de_la_suspension:
            tipos.append('Acta de prórroga de suspensión')
        return ', '.join(tipos) or 'Otrosí'

    def _observaciones_estado_otrosi_nuevo(self, estado):
        self.ensure_one()
        return self._texto_observacion_otrosi_nuevo(
            self.observaciones_flujo_nuevo
        )

    def _usuarios_proyecto_por_grupo_otrosi(self, xml_id, usuarios_extra=None):
        self.ensure_one()
        grupo = self.env.ref(xml_id)
        usuarios = self.proyecto_relacionado.users_with_access
        if hasattr(self.proyecto_relacionado, 'users_with_access_financiero'):
            usuarios |= self.proyecto_relacionado.users_with_access_financiero
        if usuarios_extra:
            usuarios |= usuarios_extra
        return usuarios.filtered(lambda user: grupo in user.groups_id and user.email)

    def _juridicos_asignados_otrosi_nuevo(self):
        self.ensure_one()
        grupo_juridico = self.env.ref('Matriz.group_contratacion_juridico')
        return self.usuarios_juridico_ids.filtered(
            lambda user: grupo_juridico in user.groups_id and user.email
        )

    def _destinatarios_estado_otrosi_nuevo(self, estado):
        self.ensure_one()
        if estado == EstadoOtroSi.VENTANILLA.value:
            return self._usuarios_proyecto_por_grupo_otrosi(
                'Matriz.group_contratacion_ventanilla_unica'
            )
        if estado == EstadoOtroSi.PRESUPUESTO.value:
            return self._usuarios_proyecto_por_grupo_otrosi('Matriz.group_presupuestos')
        if estado == EstadoOtroSi.DIRECTOR_JURIDICO.value:
            return self._usuarios_proyecto_por_grupo_otrosi(
                'Matriz.group_contratacion_director_juridico',
                usuarios_extra=self.usuarios_juridico_ids,
            )
        if estado in (EstadoOtroSi.JURIDICO_1.value, EstadoOtroSi.JURIDICO_2.value):
            return self._juridicos_asignados_otrosi_nuevo()
        if estado == EstadoOtroSi.RECURSOS_HUMANOS.value:
            return self._usuarios_proyecto_por_grupo_otrosi('Matriz.group_contratacion_recursos')
        if estado in (EstadoOtroSi.COORDINADOR.value, EstadoOtroSi.FIRMA.value):
            return (
                self._usuarios_proyecto_por_grupo_otrosi('Matriz.group_contratacion_coordinador')
                | self._usuarios_proyecto_por_grupo_otrosi('Matriz.group_contratacion_asis_coordinador')
            )
        return self.env['res.users']

    def _notificar_flujo_otrosi_nuevo(
        self,
        usuarios,
        titulo,
        mensaje,
        observaciones=None,
        estado_destino=None,
        boton_texto='Ir al otrosí',
    ):
        self.ensure_one()
        if not usuarios:
            return
        titulo_normalizado = (titulo or '').strip().lower()
        es_devolucion = titulo_normalizado.startswith(
            ('devolución', 'devolucion')
        )
        color_principal = "#f28c28" if es_devolucion else "#043484"
        color_observacion_borde = "#f28c28" if es_devolucion else "#ffcc00"
        color_observacion_fondo = "#fff3e0" if es_devolucion else "#fffacd"
        color_observacion_texto = "#8a4b08" if es_devolucion else "#856404"
        observaciones = self._texto_observacion_otrosi_nuevo(observaciones)
        nombre_proyecto = self._nombre_proyecto_otrosi()
        formulario_url = self._formulario_url_otrosi()
        estado_destino_nombre = self._nombre_estado_otrosi_nuevo(estado_destino or self.estado_contratacion)
        tipo_otrosi = self._tipo_otrosi_nuevo()
        destinatarios_notificados = self.env['res.users']

        for usuario in usuarios:
            body_html = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                    <div style="background-color: {color_principal}; color: white; padding: 12px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                        <h3 style="margin: 0; font-size: 18px;">{titulo}: <strong>{nombre_proyecto}</strong></h3>
                    </div>
                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                        <p>Hola, <strong>{usuario.name}</strong>:</p>
                        <p>{mensaje}</p>
                        <div style="margin: 15px 0; padding: 12px; background-color: #f9f9f9; border-left: 4px solid {color_principal};">
                            <strong>Otrosí:</strong> {self.display_name}<br/>
                            <strong>Tipo:</strong> {tipo_otrosi}<br/>
                            <strong>Proyecto:</strong> {nombre_proyecto}<br/>
                            <strong>Estado destino:</strong> {estado_destino_nombre}
                        </div>
                        {f'''
                        <div style="margin-top: 20px; padding: 10px; border: 1px solid {color_observacion_borde}; background-color: {color_observacion_fondo}; border-radius: 4px;">
                            <strong>Observaciones:</strong><br/>
                            <em style="color: {color_observacion_texto};">{observaciones}</em>
                        </div>
                        ''' if observaciones else ''}
                        <p style="text-align: center; margin-top: 30px;">
                            <a href="{formulario_url}"
                                style="background-color: {color_principal}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                {boton_texto}
                            </a>
                        </p>
                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                            SGO - mensaje de notificación
                        </p>
                    </div>
                </div>
            """
            self.env['mail.mail'].sudo().create({
                'subject': self._get_asunto_correo_con_contratista(
                    f"{nombre_proyecto} - {titulo}"
                ),
                'email_from': self.env.user.email or self.env.user.email_formatted,
                'email_to': usuario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send()
            destinatarios_notificados |= usuario

        if destinatarios_notificados:
            self.message_post(
                body=(
                    f"<p><b>{titulo}</b></p>"
                    f"<p>{mensaje}</p>"
                    f"<p><b>Destinatarios:</b> {', '.join(destinatarios_notificados.mapped('name'))}</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )

    def _mover_estado_otrosi_nuevo(self, estado_destino, detalle=None):
        self.ensure_one()
        estado_origen = self.estado_contratacion
        observaciones = self._observaciones_estado_otrosi_nuevo(estado_origen)
        if observaciones:
            self.message_post(
                body=(
                    "<p><strong>Observación del flujo nuevo de Otrosí</strong></p>"
                    f"<p><strong>Estado:</strong> "
                    f"{self._nombre_estado_otrosi_nuevo(estado_origen)}</p>"
                    f"<p><strong>Registrada por:</strong> {self.env.user.name}</p>"
                    f"<p>{observaciones}</p>"
                ),
                subtype_xmlid='mail.mt_note',
            )
        self.with_context(limpiar_observaciones_flujo_nuevo=True).write({
            'estado_contratacion': estado_destino,
            'observaciones_flujo_nuevo': False,
        })
        self.message_post(
            body=(
                f"<p><strong>Flujo nuevo Otrosí:</strong> "
                f"{self._nombre_estado_otrosi_nuevo(estado_origen)} &rarr; "
                f"{self._nombre_estado_otrosi_nuevo(estado_destino)}</p>"
                f"{f'<p>{detalle}</p>' if detalle else ''}"
            )
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Estado actualizado',
                'message': f"Otrosí enviado a {self._nombre_estado_otrosi_nuevo(estado_destino)}.",
                'type': 'success',
                'sticky': False,
            }
        }

    def _enviar_a_ventanilla_otrosi_nuevo(self):
        self.ensure_one()
        usuarios = self._usuarios_proyecto_por_grupo_otrosi(
            'Matriz.group_contratacion_ventanilla_unica'
        )
        if not usuarios:
            raise UserError(_("No hay usuarios con el rol Ventanilla asociados al proyecto."))
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Revisión pendiente - Ventanilla",
            "El coordinador aprobó el otrosí. Por favor, revisa la documentación inicial.",
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.VENTANILLA.value,
            'Revisar otrosí',
        )
        self.write({'aprobacion_coordinador_realizada': True})
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.VENTANILLA.value)

    def _enviar_a_director_juridico_otrosi_nuevo(self):
        self.ensure_one()
        usuarios = self._usuarios_proyecto_por_grupo_otrosi(
            'Matriz.group_contratacion_director_juridico',
            usuarios_extra=self.usuarios_juridico_ids,
        )
        if not usuarios:
            raise UserError(_("No hay director jurídico asociado al proyecto."))
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Revisión pendiente - Director Jurídico",
            "El trámite de otrosí está listo para revisión.",
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.DIRECTOR_JURIDICO.value,
            'Revisar',
        )
        self.write({'aprobacion_coordinador_realizada': True})
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.DIRECTOR_JURIDICO.value)

    def _enviar_a_firma_otrosi_nuevo(self):
        self.ensure_one()
        usuarios = self._destinatarios_estado_otrosi_nuevo(EstadoOtroSi.FIRMA.value)
        if not usuarios:
            raise UserError(_("No hay coordinadores o asistentes asociados al proyecto para recibir la firma."))
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Firma pendiente - Otrosí",
            "Jurídico dejó el otrosí listo para firma y formalización. Por favor, continúa con el cierre correspondiente.",
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.FIRMA.value,
            'Continuar firma',
        )
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.FIRMA.value)

    def _aprobar_coordinador_otrosi_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_otrosi_nuevo:
            return False
        self._validar_grupos_otrosi_nuevo(
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_asis_coordinador',
        )
        if (
            self.estado_contratacion == EstadoOtroSi.JURIDICO.value
            and (self.reinicio or self.acta_de_prorroga_de_la_suspension)
        ):
            return self._enviar_a_director_juridico_otrosi_nuevo()
        if self.estado_contratacion != EstadoOtroSi.COORDINADOR.value:
            raise UserError(_("Este botón solo aplica cuando el otrosí está en Coordinador."))
        if self.estado_retorno_devolucion_otrosi:
            return self.coordinador_retorna_ajustes_otrosi_nuevo()
        if self._requiere_ventanilla_presupuesto_otrosi_nuevo():
            return self._enviar_a_ventanilla_otrosi_nuevo()
        return self._enviar_a_director_juridico_otrosi_nuevo()

    def aprueba_coordinador(self, documentos=None):
        if self.usa_flujo_otrosi_nuevo:
            return self._aprobar_coordinador_otrosi_nuevo()
        return super().aprueba_coordinador(documentos=documentos)

    def aprueba_coordinador_j(self, documentos=None):
        if self.usa_flujo_otrosi_nuevo:
            return self._aprobar_coordinador_otrosi_nuevo()
        return super().aprueba_coordinador_j()

    def aprueba_c_con_prorroga(self, documentos=None):
        if self.usa_flujo_otrosi_nuevo:
            return self._aprobar_coordinador_otrosi_nuevo()
        return super().aprueba_c_con_prorroga(documentos=documentos)

    def aprueba_coordinador_juridico(self, documentos=None):
        if self.usa_flujo_otrosi_nuevo:
            return self._aprobar_coordinador_otrosi_nuevo()
        return super().aprueba_coordinador_juridico(documentos=documentos)

    def aprueba_coordinador_j_2(self, documentos=None):
        if self.usa_flujo_otrosi_nuevo:
            return self._aprobar_coordinador_otrosi_nuevo()
        return super().aprueba_coordinador_j_2(documentos=documentos)

    def aprueba_ventanilla_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo('Matriz.group_contratacion_ventanilla_unica')
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.VENTANILLA.value:
            raise UserError(_("Esta acción solo aplica en Ventanilla del flujo nuevo."))
        usuarios = self._usuarios_proyecto_por_grupo_otrosi('Matriz.group_presupuestos')
        if not usuarios:
            raise UserError(_("No hay usuarios con el rol Presupuesto asociados al proyecto."))
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Revisión pendiente - Presupuesto",
            "Ventanilla aprobó el otrosí. Por favor, continúa con RPC y anexo tributario.",
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.PRESUPUESTO.value,
            'Continuar revisión',
        )
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.PRESUPUESTO.value)

    def aprueba_presupuesto_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo('Matriz.group_presupuestos')
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.PRESUPUESTO.value:
            raise UserError(_("Esta acción solo aplica en Presupuesto del flujo nuevo."))
        self._validar_aprobaciones_otrosi_nuevo('financiera')
        usuarios = self._usuarios_proyecto_por_grupo_otrosi(
            'Matriz.group_contratacion_director_juridico',
            usuarios_extra=self.usuarios_juridico_ids,
        )
        if not usuarios:
            raise UserError(_("No hay director jurídico asociado al proyecto."))
        self.write({'aprobacion_financiera_realizada': True})
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Revisión pendiente - Director Jurídico",
            "Presupuesto aprobó el otrosí. Por favor, revisa el trámite.",
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.DIRECTOR_JURIDICO.value,
            'Revisar',
        )
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.DIRECTOR_JURIDICO.value)

    def enviar_juridico_asignado_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo('Matriz.group_contratacion_director_juridico')
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.DIRECTOR_JURIDICO.value:
            raise UserError(_("Esta acción solo aplica en Director Jurídico del flujo nuevo."))
        juridicos = self.usuarios_juridico_ids.filtered(
            lambda user: self.env.ref('Matriz.group_contratacion_juridico') in user.groups_id and user.email
        )
        if not juridicos:
            raise UserError(_("Debes agregar al menos un jurídico auxiliar en Usuarios con acceso."))
        self._notificar_flujo_otrosi_nuevo(
            juridicos,
            "Revisión pendiente - Jurídico",
            "El Director Jurídico te asignó la revisión del otrosí.",
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.JURIDICO_1.value,
            'Revisar otrosí',
        )
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.JURIDICO_1.value)

    def _enviar_a_juridico_2_otrosi_nuevo(self, mensaje):
        self.ensure_one()
        juridicos = self._juridicos_asignados_otrosi_nuevo()
        if not juridicos:
            raise UserError(_("No hay jurídico asignado para continuar el otrosí."))
        self._notificar_flujo_otrosi_nuevo(
            juridicos,
            "Revisión pendiente - Jurídico",
            mensaje,
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.JURIDICO_2.value,
            'Continuar revisión',
        )
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.JURIDICO_2.value)

    def aprueba_juridico_1_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo('Matriz.group_contratacion_juridico')
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.JURIDICO_1.value:
            raise UserError(_("Esta acción solo aplica en Jurídico 1 del flujo nuevo."))
        self._validar_juridico_asignado_otrosi_nuevo()
        self._validar_aprobaciones_otrosi_nuevo('juridico')
        self.write({'aprobacion_juridico_realizada': True})
        if self._omite_recursos_humanos_otrosi_nuevo():
            self._validar_documento_juridico_otrosi_nuevo()
            return self._enviar_a_firma_otrosi_nuevo()
        usuarios = self._usuarios_proyecto_por_grupo_otrosi('Matriz.group_contratacion_recursos')
        if not usuarios:
            raise UserError(_("No hay usuarios de Recursos Humanos asociados al proyecto."))
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Revisión pendiente - Recursos Humanos",
            "Jurídico aprobó el otrosí. Por favor, revisa los documentos correspondientes.",
            self.observaciones_flujo_nuevo,
            EstadoOtroSi.RECURSOS_HUMANOS.value,
            'Revisar otrosí',
        )
        return self._mover_estado_otrosi_nuevo(EstadoOtroSi.RECURSOS_HUMANOS.value)

    def aprueba_recursos_humanos_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo('Matriz.group_contratacion_recursos')
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.RECURSOS_HUMANOS.value:
            raise UserError(_("Esta acción solo aplica en Recursos Humanos del flujo nuevo."))
        self._validar_aprobaciones_otrosi_nuevo('recursos_humanos')
        self.write({'aprobacion_recursos_humanos_realizada': True})
        return self._enviar_a_juridico_2_otrosi_nuevo(
            "Recursos Humanos aprobó el otrosí. Continúa con la preparación del documento."
        )

    def aprueba_juridico_2_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo('Matriz.group_contratacion_juridico')
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.JURIDICO_2.value:
            raise UserError(_("Esta acción solo aplica en Jurídico 2 del flujo nuevo."))
        self._validar_juridico_asignado_otrosi_nuevo()
        self._validar_documento_juridico_otrosi_nuevo()
        return self._enviar_a_firma_otrosi_nuevo()

    def _registrar_devolucion_otrosi_nuevo(self, estado_origen, estado_destino):
        self.ensure_one()
        self.write({
            'ultimo_estado_devolucion_otrosi': estado_origen,
            'ultimo_usuario_devolucion_otrosi_id': self.env.user.id,
            'estado_retorno_devolucion_otrosi': estado_origen,
        })
        self.message_post(
            body=(
                f"<p><strong>Devolución de otrosí:</strong> "
                f"{self._nombre_estado_otrosi_nuevo(estado_origen)} &rarr; "
                f"{self._nombre_estado_otrosi_nuevo(estado_destino)}</p>"
                f"<p><strong>Devuelto por:</strong> {self.env.user.name}</p>"
            ),
            subtype_xmlid='mail.mt_note',
        )

    def devolver_coordinador_otrosi_nuevo(self):
        self.ensure_one()
        estados_permitidos = (
            EstadoOtroSi.VENTANILLA.value,
            EstadoOtroSi.PRESUPUESTO.value,
            EstadoOtroSi.DIRECTOR_JURIDICO.value,
            EstadoOtroSi.JURIDICO_1.value,
            EstadoOtroSi.RECURSOS_HUMANOS.value,
            EstadoOtroSi.JURIDICO_2.value,
        )
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion not in estados_permitidos:
            raise UserError(_("Esta acción solo aplica para devolver al coordinador desde el flujo nuevo."))
        grupos_por_estado = {
            EstadoOtroSi.VENTANILLA.value: ('Matriz.group_contratacion_ventanilla_unica',),
            EstadoOtroSi.PRESUPUESTO.value: ('Matriz.group_presupuestos',),
            EstadoOtroSi.DIRECTOR_JURIDICO.value: ('Matriz.group_contratacion_director_juridico',),
            EstadoOtroSi.JURIDICO_1.value: ('Matriz.group_contratacion_juridico',),
            EstadoOtroSi.RECURSOS_HUMANOS.value: ('Matriz.group_contratacion_recursos',),
            EstadoOtroSi.JURIDICO_2.value: ('Matriz.group_contratacion_juridico',),
        }
        self._validar_grupos_otrosi_nuevo(*grupos_por_estado[self.estado_contratacion])
        if self.estado_contratacion in (
            EstadoOtroSi.JURIDICO_1.value,
            EstadoOtroSi.JURIDICO_2.value,
        ):
            self._validar_juridico_asignado_otrosi_nuevo()
        coordinadores = self._destinatarios_estado_otrosi_nuevo(EstadoOtroSi.COORDINADOR.value)
        if not coordinadores:
            raise UserError(_("No hay coordinadores o asistentes asociados al proyecto para recibir la devolución."))
        estado_origen = self.estado_contratacion
        observaciones = self._observaciones_estado_otrosi_nuevo(estado_origen)
        self._notificar_flujo_otrosi_nuevo(
            coordinadores,
            f"Devolución de {self._nombre_estado_otrosi_nuevo(estado_origen)}",
            (
                f"{self.env.user.name} devolvió el otrosí "
                f"<strong>{self.display_name}</strong> a Coordinación para ajustes."
            ),
            observaciones,
            EstadoOtroSi.COORDINADOR.value,
            'Revisar devolución',
        )
        self._registrar_devolucion_otrosi_nuevo(
            estado_origen,
            EstadoOtroSi.COORDINADOR.value,
        )
        return self._mover_estado_otrosi_nuevo(
            EstadoOtroSi.COORDINADOR.value,
            detalle="Devolución registrada al coordinador.",
        )

    def devolver_presupuesto_a_ventanilla_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo('Matriz.group_presupuestos')
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.PRESUPUESTO.value:
            raise UserError(_("Esta acción solo aplica desde Presupuesto del flujo nuevo."))
        usuarios = self._destinatarios_estado_otrosi_nuevo(EstadoOtroSi.VENTANILLA.value)
        if not usuarios:
            raise UserError(_("No hay usuarios de Ventanilla asociados al proyecto para recibir la devolución."))
        observaciones = self._observaciones_estado_otrosi_nuevo(EstadoOtroSi.PRESUPUESTO.value)
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Devolución de Presupuesto",
            (
                f"Presupuesto devolvió el otrosí <strong>{self.display_name}</strong> "
                "a Ventanilla para revisión."
            ),
            observaciones,
            EstadoOtroSi.VENTANILLA.value,
            'Revisar devolución',
        )
        self.message_post(
            body="<p><strong>Presupuesto devolvió el otrosí a Ventanilla.</strong></p>",
            subtype_xmlid='mail.mt_note',
        )
        return self._mover_estado_otrosi_nuevo(
            EstadoOtroSi.VENTANILLA.value,
            detalle="Devolución registrada a Ventanilla.",
        )

    def devolver_director_juridico_a_presupuesto_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo(
            'Matriz.group_contratacion_director_juridico'
        )
        if (
            not self.usa_flujo_otrosi_nuevo
            or self.estado_contratacion != EstadoOtroSi.DIRECTOR_JURIDICO.value
        ):
            raise UserError(
                _("Esta accion solo aplica desde Director Juridico del flujo nuevo.")
            )
        usuarios = self._destinatarios_estado_otrosi_nuevo(
            EstadoOtroSi.PRESUPUESTO.value
        )
        if not usuarios:
            raise UserError(
                _("No hay usuarios de Presupuesto asociados al proyecto para recibir la devolucion.")
            )
        observaciones = self._observaciones_estado_otrosi_nuevo(
            EstadoOtroSi.DIRECTOR_JURIDICO.value
        )
        self._notificar_flujo_otrosi_nuevo(
            usuarios,
            "Devolucion de Director Juridico",
            (
                f"Director Juridico devolvio el otrosi <strong>{self.display_name}</strong> "
                "a Presupuesto para ajustes."
            ),
            observaciones,
            EstadoOtroSi.PRESUPUESTO.value,
            'Revisar devolucion',
        )
        self.message_post(
            body="<p><strong>Director Juridico devolvio el otrosi a Presupuesto.</strong></p>",
            subtype_xmlid='mail.mt_note',
        )
        return self._mover_estado_otrosi_nuevo(
            EstadoOtroSi.PRESUPUESTO.value,
            detalle="Devolucion registrada a Presupuesto.",
        )

    def coordinador_retorna_ajustes_otrosi_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo(
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_asis_coordinador',
        )
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.COORDINADOR.value:
            raise UserError(_("Esta acción solo aplica para otrosí del flujo nuevo en Coordinador."))
        estado_destino = self.estado_retorno_devolucion_otrosi
        if not estado_destino:
            raise UserError(_("No hay un estado de retorno pendiente."))
        destinatarios = self._destinatarios_estado_otrosi_nuevo(estado_destino)
        if not destinatarios:
            raise UserError(
                _("No hay usuarios asociados para retornar el otrosí al estado %s.")
                % self._nombre_estado_otrosi_nuevo(estado_destino)
            )
        usuario_devolucion = (
            self.ultimo_usuario_devolucion_otrosi_id.name
            if self.ultimo_usuario_devolucion_otrosi_id
            else 'el área solicitante'
        )
        observaciones = self._observaciones_estado_otrosi_nuevo(EstadoOtroSi.COORDINADOR.value)
        self._notificar_flujo_otrosi_nuevo(
            destinatarios,
            "Ajustes de Coordinación",
            (
                f"Coordinación aplicó los ajustes solicitados por <strong>{usuario_devolucion}</strong> "
                f"y retorna el otrosí <strong>{self.display_name}</strong> para continuar la revisión."
            ),
            observaciones,
            estado_destino,
            'Continuar revisión',
        )
        self.write({
            'estado_retorno_devolucion_otrosi': False,
        })
        return self._mover_estado_otrosi_nuevo(
            estado_destino,
            detalle="Coordinación retornó los ajustes al estado solicitante.",
        )

    def finalizar_otrosi_flujo_nuevo(self):
        self.ensure_one()
        self._validar_grupos_otrosi_nuevo(
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_asis_coordinador',
        )
        if not self.usa_flujo_otrosi_nuevo or self.estado_contratacion != EstadoOtroSi.FIRMA.value:
            raise UserError(_("Solo puedes finalizar el otrosí desde el estado Firma."))
        self._validar_documento_firmado_otrosi_nuevo()
        estado_final = self._estado_final_otrosi_nuevo()
        resultado = self._mover_estado_otrosi_nuevo(estado_final)
        self._sincronizar_estado_fc_por_final_otrosi_nuevo()
        return resultado

    def _sincronizar_estado_fc_por_final_otrosi_nuevo(self):
        self.ensure_one()
        if self.suspension:
            self._marcar_contratacion_padre_como_suspendida()

    def _soporte_normalizar_flujo_otrosi_anterior(self):
        self.ensure_one()

        vals = {}
        motivos = []

        def normalizar_documento(campo_id, campo_visible, etiqueta):
            if (
                campo_id in self._fields
                and campo_visible in self._fields
                and getattr(self, campo_id, False)
                and getattr(self, campo_visible, True)
            ):
                vals[campo_visible] = False
                motivos.append(
                    f"{etiqueta}: ya tenia documento relacionado y se oculto "
                    "la carga pendiente."
                )

        def documento_cargado(campo_id, campo_visible):
            documento = getattr(self, campo_id, False)
            visible = getattr(self, campo_visible, True)
            return bool(documento) or visible is False

        normalizar_documento('su_cdp_id', 'su_cdp_doc_visible', 'RPC')
        normalizar_documento(
            'contratos_otro_id',
            'contratos_otro_doc_visible',
            'Documento juridico PN/laboral',
        )
        normalizar_documento(
            'contratos_firmado_id',
            'contratos_firmado_natu_doc_visible',
            'Documento firmado PN/laboral',
        )
        normalizar_documento(
            'contratos_otro_juridico_id',
            'contratos_otro_juridico_doc_visible',
            'Documento juridico PJ',
        )
        normalizar_documento(
            'contratos_firmado_juridico_id',
            'contratos_firmado_juridico_doc_visible',
            'Documento firmado PJ',
        )

        if self.otro_si_modificatorio and self.estado_contratacion == 'coordinador':
            if self.select_persona == 'persona_juridica':
                tiene_documento_juridico = documento_cargado(
                    'contratos_otro_juridico_id',
                    'contratos_otro_juridico_doc_visible',
                )
                tiene_documento_firmado = documento_cargado(
                    'contratos_firmado_juridico_id',
                    'contratos_firmado_juridico_doc_visible',
                )
                if (
                    not tiene_documento_juridico
                    and not tiene_documento_firmado
                    and self.estado_contratacion_nun_juridica != '1'
                ):
                    vals['estado_contratacion_nun_juridica'] = '1'
                    motivos.append(
                        "Modificatorio PJ en Coordinador: se restauro el "
                        "indicador auxiliar inicial para mostrar el boton "
                        "Aprobar sin depender de documentos posteriores."
                    )
            else:
                tiene_documento_juridico = documento_cargado(
                    'contratos_otro_id',
                    'contratos_otro_doc_visible',
                )
                tiene_documento_firmado = documento_cargado(
                    'contratos_firmado_id',
                    'contratos_firmado_natu_doc_visible',
                )
                if (
                    not tiene_documento_juridico
                    and not tiene_documento_firmado
                    and self.estado_contratacion_nun != '1'
                ):
                    vals['estado_contratacion_nun'] = '1'
                    motivos.append(
                        "Modificatorio PN/laboral en Coordinador: se restauro "
                        "el indicador auxiliar inicial para mostrar el boton "
                        "Aprobar sin depender de documentos posteriores."
                    )

        # En el flujo anterior, contratado()/contratado2() siempre llevan el
        # estado principal a ``contratado`` y luego activan estos indicadores.
        # Una ejecucion parcial o un soporte que solo haya devuelto el estado
        # puede dejarlos en True mientras el otrosi sigue en Coordinador. Ese
        # valor residual oculta el boton Contratar aunque el firmado exista.
        debe_estar_contratado = self.estado_contratacion == 'contratado'
        for campo_contratado in ('contratado_fin', 'contratado_fin_j'):
            if (
                campo_contratado in self._fields
                and bool(getattr(self, campo_contratado, False))
                != debe_estar_contratado
            ):
                vals[campo_contratado] = debe_estar_contratado
                motivos.append(
                    "%s: se sincronizo con el estado principal del otrosi "
                    "para eliminar un bloqueo residual del flujo."
                    % self._fields[campo_contratado].string
                )

        if self.select_persona == 'persona_juridica':
            if self.estado_contratacion_nun_juridica not in ('1', '2'):
                vals['estado_contratacion_nun_juridica'] = '1'
                motivos.append(
                    "El indicador auxiliar PJ estaba vacio o invalido; "
                    "se dejo en Coordinador."
                )
            if (
                self.estado_contratacion == 'juridico'
                and self.estado_contratacion_nun_juridica == '2'
            ):
                vals['estado_contratacion_nun_juridica'] = '1'
                motivos.append(
                    "El otrosi ya estaba nuevamente en Juridico; se limpio "
                    "el indicador auxiliar de retorno PJ."
                )
        else:
            if self.estado_contratacion_nun not in ('1', '2', '3'):
                vals['estado_contratacion_nun'] = '1'
                motivos.append(
                    "El indicador auxiliar PN/laboral estaba vacio o invalido; "
                    "se dejo en Coordinador."
                )
            if (
                self.estado_contratacion != 'coordinador'
                and self.estado_contratacion_nun in ('2', '3')
            ):
                vals['estado_contratacion_nun'] = '1'
                motivos.append(
                    "El otrosi ya no estaba en Coordinador; se limpio el "
                    "indicador auxiliar de devolucion PN/laboral."
                )

        if self.estado_contratacion == 'contratado':
            for campo in (
                'aprobacion_coordinador_realizada',
                'aprobacion_financiera_realizada',
                'aprobacion_juridico_realizada',
                'aprobacion_recursos_humanos_realizada',
            ):
                if campo in self._fields and not getattr(self, campo, False):
                    vals[campo] = True
            if any(campo in vals for campo in (
                'aprobacion_coordinador_realizada',
                'aprobacion_financiera_realizada',
                'aprobacion_juridico_realizada',
                'aprobacion_recursos_humanos_realizada',
            )):
                motivos.append(
                    "El otrosi ya estaba contratado; se marcaron aprobaciones "
                    "internas pendientes para evitar botones residuales."
                )

        if not vals:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sin cambios',
                    'message': 'No se encontro una normalizacion segura para este flujo anterior.',
                    'type': 'warning',
                    'sticky': False,
                },
            }

        self.with_context(
            skip_validacion_estado_otrosi=True,
            skip_validacion_estado_otrosi_nuevo=True,
        ).write(vals)
        self.message_post(
            body=(
                "<p><strong>Soporte normalizo el flujo anterior de Otrosi.</strong></p>"
                f"<p><strong>Estado actual:</strong> {self.estado_contratacion}</p>"
                "<p><strong>Ajustes:</strong><br/>"
                + "<br/>".join(motivos)
                + "</p>"
                "<p>No se enviaron correos ni se ejecutaron aprobaciones.</p>"
            ),
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Flujo normalizado',
                'message': 'Otrosi de flujo anterior revisado por soporte.',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_soporte_diagnosticar_aprobacion_modificatorio(self):
        """Registra las condiciones de visibilidad sin alterar el otrosi."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_("Solo soporte puede diagnosticar la aprobacion del otrosi."))

        es_juridica = self.select_persona == 'persona_juridica'
        es_coordinador = self.estado_contratacion == 'coordinador'
        es_flujo_anterior = not self.usa_flujo_otrosi_nuevo
        es_modificatorio = bool(self.otro_si_modificatorio)
        es_tiempo = self.tipo_modificacion_modificatorio == 'tiempo'
        es_valor = self.tipo_modificacion_modificatorio in ('valor', 'ambos')
        tiene_rol_coordinador = any(self.env.user.has_group(grupo) for grupo in (
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_asis_coordinador',
        ))
        indicador_inicial = (
            self.estado_contratacion_nun_juridica == '1'
            if es_juridica else self.estado_contratacion_nun == '1'
        )
        aprobacion_inicial_visible = all((
            es_flujo_anterior, es_modificatorio, es_coordinador,
            tiene_rol_coordinador, indicador_inicial,
            es_tiempo or es_valor,
            es_juridica or not self.aprobacion_recursos_humanos_realizada,
            es_juridica or es_tiempo or self.contratos_firmado_natu_doc_visible,
        ))
        _logger.warning(
            "[DIAGNOSTICO-OTROSI-MODIFICATORIO] otrosi_id=%s contrato_id=%s "
            "usuario_id=%s persona=%s flujo_nuevo=%s modificatorio=%s tipo=%s "
            "estado=%s nun_pj=%s nun_pn=%s rol_coordinador=%s "
            "aprobacion_rrhh=%s aprobacion_juridico=%s "
            "documento_juridico_pj_id=%s documento_juridico_pj_visible=%s "
            "documento_firmado_pj_id=%s documento_firmado_pj_visible=%s "
            "documento_juridico_pn_id=%s documento_juridico_pn_visible=%s "
            "documento_firmado_pn_id=%s documento_firmado_pn_visible=%s "
            "aprobacion_inicial_visible_segun_vista=%s",
            self.id, self.contratacion_fuv_en_id.id, self.env.user.id,
            self.select_persona, self.usa_flujo_otrosi_nuevo,
            self.otro_si_modificatorio, self.tipo_modificacion_modificatorio,
            self.estado_contratacion, self.estado_contratacion_nun_juridica,
            self.estado_contratacion_nun, tiene_rol_coordinador,
            self.aprobacion_recursos_humanos_realizada,
            self.aprobacion_juridico_realizada,
            self.contratos_otro_juridico_id.id,
            self.contratos_otro_juridico_doc_visible,
            self.contratos_firmado_juridico_id.id,
            self.contratos_firmado_juridico_doc_visible,
            self.contratos_otro_id.id, self.contratos_otro_doc_visible,
            self.contratos_firmado_id.id,
            self.contratos_firmado_natu_doc_visible,
            aprobacion_inicial_visible,
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Diagnostico registrado',
                'message': 'Consulta DIAGNOSTICO-OTROSI-MODIFICATORIO en el log de Odoo.',
                'type': 'info',
                'sticky': False,
            },
        }

    def action_soporte_normalizar_flujo_otrosi(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_("Solo soporte puede normalizar el flujo del otrosi."))
        if not self.usa_flujo_otrosi_nuevo:
            return self._soporte_normalizar_flujo_otrosi_anterior()

        def documento_cargado(campo_id, campo_visible):
            documento = getattr(self, campo_id, False)
            visible = getattr(self, campo_visible, True)
            return bool(documento) or visible is False

        estado_origen = self.estado_contratacion
        estado_destino = estado_origen
        motivo = False
        requiere_financiero = self._requiere_ventanilla_presupuesto_otrosi_nuevo()
        omite_rrhh = self._omite_recursos_humanos_otrosi_nuevo()
        tiene_rpc = documento_cargado('su_cdp_id', 'su_cdp_doc_visible')
        tiene_documento_juridico = (
            documento_cargado(
                'contratos_otro_juridico_id',
                'contratos_otro_juridico_doc_visible',
            )
            if self.select_persona == 'persona_juridica'
            else documento_cargado('contratos_otro_id', 'contratos_otro_doc_visible')
        )
        tiene_documento_firmado = (
            documento_cargado(
                'contratos_firmado_juridico_id',
                'contratos_firmado_juridico_doc_visible',
            )
            if self.select_persona == 'persona_juridica'
            else documento_cargado(
                'contratos_firmado_id',
                'contratos_firmado_natu_doc_visible',
            )
        )
        tiene_juridico_asignado = bool(self.usuarios_juridico_ids)
        campos_normalizados = {}
        motivos_campos = []

        if self.adicion:
            if (
                not self.compromiso_presupuestal_adicion
                and self.compromiso_presupuestal
            ):
                campos_normalizados['compromiso_presupuestal_adicion'] = (
                    self.compromiso_presupuestal
                )
                motivos_campos.append(
                    "El otrosi es adicion y el RPC de adicion estaba vacio; "
                    "se llevo el RPC visible al campo propio de adicion para "
                    "revision de financiera."
                )
        if estado_origen == EstadoOtroSi.FINANCIERA.value:
            if requiere_financiero:
                estado_destino = (
                    EstadoOtroSi.PRESUPUESTO.value
                    if tiene_rpc or self.aprobacion_financiera_realizada
                    else EstadoOtroSi.VENTANILLA.value
                )
                motivo = (
                    "Financiera del flujo anterior se normalizo al tramo "
                    "Ventanilla/Presupuesto del flujo nuevo."
                )
            else:
                estado_destino = EstadoOtroSi.DIRECTOR_JURIDICO.value
                motivo = (
                    "El tipo de otrosi no requiere Ventanilla/Presupuesto y "
                    "se normalizo a Director Juridico."
                )
        elif estado_origen == EstadoOtroSi.JURIDICO.value:
            if tiene_documento_firmado:
                estado_destino = EstadoOtroSi.FIRMA.value
                motivo = (
                    "El otrosi ya tiene documento firmado y se ubico en Firma "
                    "para cierre manual."
                )
            elif tiene_documento_juridico:
                estado_destino = EstadoOtroSi.FIRMA.value
                motivo = (
                    "El documento juridico ya fue cargado y se ubico en Firma."
                )
            elif not tiene_juridico_asignado:
                estado_destino = EstadoOtroSi.DIRECTOR_JURIDICO.value
                motivo = (
                    "El otrosi estaba en Juridico anterior sin asignacion y "
                    "se ubico en Director Juridico."
                )
            elif omite_rrhh:
                estado_destino = EstadoOtroSi.JURIDICO_1.value
                motivo = (
                    "Este tipo de otrosi no pasa por RRHH; Juridico queda en "
                    "revision y contrato."
                )
            elif self.aprobacion_recursos_humanos_realizada:
                estado_destino = EstadoOtroSi.JURIDICO_2.value
                motivo = "RRHH ya aprobo; se ubico en Juridico 2."
            elif self.aprobacion_juridico_realizada:
                estado_destino = EstadoOtroSi.RECURSOS_HUMANOS.value
                motivo = "Juridico ya aprobo; se ubico en Recursos Humanos."
            else:
                estado_destino = EstadoOtroSi.JURIDICO_1.value
                motivo = "Tiene juridico asignado y se ubico en Juridico 1."
        elif (
            estado_origen == EstadoOtroSi.DIRECTOR_JURIDICO.value
            and tiene_juridico_asignado
        ):
            estado_destino = EstadoOtroSi.JURIDICO_1.value
            motivo = (
                "Director Juridico ya tenia juridico asignado y se normalizo "
                "a revision juridica."
            )
        elif estado_origen == EstadoOtroSi.RECURSOS_HUMANOS.value:
            if omite_rrhh:
                estado_destino = (
                    EstadoOtroSi.FIRMA.value
                    if tiene_documento_juridico
                    else EstadoOtroSi.JURIDICO_1.value
                )
                motivo = (
                    "Este tipo de otrosi no pasa por RRHH; se reubico en el "
                    "tramo juridico correcto."
                )
            elif self.aprobacion_recursos_humanos_realizada:
                estado_destino = EstadoOtroSi.JURIDICO_2.value
                motivo = "RRHH ya aprobo; se normalizo a Juridico 2."
        elif estado_origen == EstadoOtroSi.JURIDICO_2.value and omite_rrhh:
            estado_destino = (
                EstadoOtroSi.FIRMA.value
                if tiene_documento_juridico
                else EstadoOtroSi.JURIDICO_1.value
            )
            motivo = (
                "Este tipo de otrosi usa una sola etapa juridica; se reubico "
                "sin pasar por Juridico 2."
            )
        elif estado_origen == EstadoOtroSi.CONTRATADO.value:
            estado_final = self._estado_final_otrosi_nuevo()
            if estado_final != EstadoOtroSi.CONTRATADO.value:
                estado_destino = estado_final
                motivo = (
                    "El otrosi estaba como Contratado, pero su tipo requiere "
                    "un estado final especifico."
                )

        if estado_destino == estado_origen and not campos_normalizados:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sin cambios',
                    'message': 'No se encontro una normalizacion segura para este estado.',
                    'type': 'warning',
                    'sticky': False,
                },
            }

        vals = {
            'observaciones_flujo_nuevo': False,
        }
        vals.update(campos_normalizados)
        if estado_destino != estado_origen:
            vals['estado_contratacion'] = estado_destino
        if estado_destino != estado_origen and 'estado_contratacion_2' in self._fields:
            vals['estado_contratacion_2'] = (
                EstadoOtroSi.JURIDICO_2.value
                if estado_destino == EstadoOtroSi.JURIDICO_2.value
                else False
            )
        self.with_context(
            skip_validacion_estado_otrosi_nuevo=True,
            skip_auto_enviar_juridico_otrosi_nuevo=True,
            limpiar_observaciones_flujo_nuevo=True,
        ).write(vals)
        detalle_campos = (
            "<p><strong>Campos normalizados:</strong> "
            + "<br/>".join(motivos_campos)
            + "</p>"
            if motivos_campos
            else ""
        )
        self.message_post(
            body=(
                "<p><strong>Soporte normalizo el flujo de Otrosi.</strong></p>"
                f"<p><strong>Estado anterior:</strong> "
                f"{self._nombre_estado_otrosi_nuevo(estado_origen)}</p>"
                f"<p><strong>Estado nuevo:</strong> "
                f"{self._nombre_estado_otrosi_nuevo(estado_destino)}</p>"
                f"<p><strong>Motivo:</strong> "
                f"{motivo or 'Normalizacion de campos del otrosi.'}</p>"
                f"{detalle_campos}"
                "<p>No se enviaron correos ni se ejecutaron aprobaciones.</p>"
            ),
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Flujo normalizado',
                'message': (
                    f"Otrosi revisado. Estado: "
                    f"{self._nombre_estado_otrosi_nuevo(estado_destino)}."
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_set_estado_final_otrosi_nuevo(self):
        self.ensure_one()
        if not self.usa_flujo_otrosi_nuevo:
            raise UserError(_("Este botón solo aplica para el flujo nuevo de otrosí."))
        estado_final = self._estado_final_otrosi_nuevo()
        self._mover_estado_otrosi_nuevo(
            estado_final,
            detalle="Marcado desde soporte para pruebas del flujo nuevo."
        )
        self._sincronizar_estado_fc_por_final_otrosi_nuevo()
        return False

    def contratado2(self):
        if self.usa_flujo_otrosi_nuevo:
            return self.finalizar_otrosi_flujo_nuevo()
        return super().contratado2()

    def contratado(self):
        if self.usa_flujo_otrosi_nuevo:
            return self.finalizar_otrosi_flujo_nuevo()
        return super().contratado()

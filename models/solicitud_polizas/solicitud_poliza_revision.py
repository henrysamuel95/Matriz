import logging
import secrets
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

from .constantes import POLIZA_SELECTION, EstadoSolicitudPoliza, OrigenSolicitudPoliza
from .correo_poliza import plantilla_correo_polizas


_logger = logging.getLogger(__name__)
_TOKEN_DIAS_CORRECCION = 7


class SolicitudPolizaRevision(models.Model):
    _inherit = 'contrataciones_fuv.solicitud.poliza'

    def _usuario_puede_revisar_polizas(self):
        return (
            self.env.user.has_group('Matriz.group_contratacion_director_juridico')
            or self.env.user.has_group('Matriz.group_contratacion_juridico')
        )

    def _validar_usuario_revision_polizas(self):
        if not self._usuario_puede_revisar_polizas():
            raise UserError(_(
                'Solo el Director Jurídico o el equipo Jurídico pueden revisar pólizas.'
            ))

    def _actualizar_estado_segun_revision(self):
        for solicitud in self:
            lineas = solicitud.linea_ids
            if not lineas:
                estado = EstadoSolicitudPoliza.SOLICITADA.value
            elif any(linea.estado_revision == 'devuelta' for linea in lineas):
                estado = EstadoSolicitudPoliza.CORRECCION.value
            elif all(linea.estado_revision == 'aprobada' for linea in lineas):
                estado = EstadoSolicitudPoliza.PENDIENTE_ACTA.value
            else:
                estado = EstadoSolicitudPoliza.TRAMITE_APROBACION.value
            if solicitud.estado != estado:
                solicitud.sudo().write({'estado': estado})

    def _remitente_polizas(self):
        """Replica el remitente del flujo nuevo de Contrataciones FUV."""
        return (self.env.user.email or '').strip()

    def _enviar_correo_sistema(self, valores, bloquear=True):
        """Centraliza correo sin direcciones codificadas en el componente."""
        self.ensure_one()
        remitente = self._remitente_polizas()
        if remitente:
            valores['email_from'] = remitente
        valores.setdefault('auto_delete', True)
        try:
            with self.env.cr.savepoint():
                self.env['mail.mail'].sudo().create(valores).send(
                    raise_exception=True
                )
        except Exception as error:
            _logger.exception('No fue posible enviar una notificación de pólizas.')
            if bloquear:
                raise UserError(_(
                    'No fue posible enviar el correo requerido: %s'
                ) % str(error))
            return False
        return True

    def _validar_estado_flujo_disponible(self, estado):
        """Evita iniciar efectos externos con un registro Odoo desactualizado."""
        self.ensure_one()
        seleccion = dict(self._fields['estado'].selection)
        if estado not in seleccion:
            raise UserError(_(
                'El estado técnico "%s" todavía no está cargado en esta '
                'instancia de Odoo. Actualice el módulo y reinicie el servicio '
                'antes de volver a enviar la corrección.'
            ) % estado)

    def _enviar_correccion_contratista(self, linea):
        self.ensure_one()
        self._validar_estado_flujo_disponible(
            EstadoSolicitudPoliza.CORRECCION.value
        )
        self._bloquear_solicitud_portal()
        correo = self._correo_contratista()
        if not correo or '@' not in correo:
            raise UserError(_(
                'El contratista no tiene un correo electrónico válido.'
            ))
        token = secrets.token_urlsafe(32)
        expira = fields.Datetime.now() + timedelta(days=_TOKEN_DIAS_CORRECCION)
        url = self._url_portal_polizas(token)
        etiqueta = dict(POLIZA_SELECTION).get(
            linea.tipo_poliza,
            linea.tipo_poliza,
        )
        contenido = Markup(
            '<p>La póliza <strong>%s</strong> requiere una corrección.</p>'
            '<div style="padding:12px 16px;background:#f4f7fb;'
            'border-left:4px solid #043484;border-radius:6px">'
            '<strong>Observación jurídica:</strong><br/>%s</div>'
            '<p>El enlace permite reemplazar únicamente la póliza devuelta.</p>'
            '<p style="text-align:center;margin:28px 0">'
            '<a href="%s" style="display:inline-block;background:#043484;'
            'color:#ffffff;padding:12px 22px;text-decoration:none;'
            'border-radius:5px;font-weight:bold">'
            'Cargar corrección</a></p>'
            '<p style="font-size:12px;color:#777">El enlace vence el %s.</p>'
        ) % (
            escape(etiqueta),
            escape(linea.observaciones_juridicas),
            escape(url),
            escape(fields.Datetime.to_string(expira)),
        )
        body_html = plantilla_correo_polizas(_('Corrección requerida'), contenido)
        self.sudo().write({
            'estado': EstadoSolicitudPoliza.CORRECCION.value,
            'portal_token': self._hash_token_portal(token),
            'portal_token_expira': expira,
        })
        self.sudo().message_post(
            body=Markup(
                '<p><strong>Póliza devuelta para corrección.</strong></p>'
                '<p><strong>Póliza:</strong> %s</p>'
                '<p><strong>Observación:</strong> %s</p>'
            ) % (escape(etiqueta), escape(linea.observaciones_juridicas)),
            subtype_xmlid='mail.mt_note',
        )
        # El correo se envía al final. El savepoint de este método fuerza antes
        # el flush del estado, token y chatter; si alguno falla, SMTP no se
        # ejecuta y no se entrega un enlace inválido.
        enviado = self._enviar_correo_sistema({
            'subject': _('Corrección de póliza - %s') % etiqueta,
            'email_to': correo,
            'body_html': body_html,
        })
        if enviado:
            self._registrar_notificacion_correo(
                _('Corrección solicitada al contratista'),
                [{
                    'rol': _('Contratista'),
                    # El nombre visible del contrato ya identifica al
                    # contratista y evita exigir al rol Jurídico permisos de
                    # lectura sobre contratista_proveedor.
                    'nombre': self._contrato_poliza().display_name,
                    'correo': correo,
                }],
                detalle=etiqueta,
            )

    def _validar_polizas_aprobadas_para_acta(self):
        self.ensure_one()
        if not self.linea_ids or not all(
            linea.estado_revision == 'aprobada' for linea in self.linea_ids
        ):
            raise UserError(_(
                'Todas las pólizas deben estar aprobadas antes de gestionar el acta.'
            ))

    def _validar_usuario_carga_acta(self):
        self.ensure_one()
        if (
            self.origen == OrigenSolicitudPoliza.SOPORTE.value
            and self.env.user.has_group('Matriz.group_soporte')
        ):
            return
        self._validar_usuario_revision_polizas()

    def action_cargar_acta_aprobacion(self):
        self.ensure_one()
        self._validar_usuario_carga_acta()
        self._validar_polizas_aprobadas_para_acta()
        if self.acta_aprobacion_documento_id:
            raise UserError(_('La solicitud ya tiene un acta definitiva cargada.'))
        carpeta = self._crear_carpeta_poliza_solicitada()
        return {
            'name': _('Cargar acta de aprobación de pólizas'),
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_drive_upload_popup_form').id,
            'target': 'new',
            'context': {
                'default_folder_id': carpeta.folder_id2,
                'default_stage2_id': carpeta.id,
                'solicitud_poliza_acta_id': self.id,
            },
        }

    def action_ver_acta_aprobacion(self):
        self.ensure_one()
        if not self.acta_aprobacion_documento_id:
            raise UserError(_('Todavía no se ha cargado el acta definitiva.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Acta de pólizas'),
            'res_model': 'drive',
            'view_mode': 'form',
            'res_id': self.acta_aprobacion_documento_id.id,
            'target': 'new',
            'context': {'default_invisible_todo': True},
        }

    def _notificar_acta_cargada(self):
        self.ensure_one()
        correo_contratista = self._correo_contratista()
        coordinadores = self._contrato_poliza()._usuarios_notificacion_revision_polizas().filtered(
            lambda usuario: usuario.has_group('Matriz.group_contratacion_coordinador')
        )
        titulo = _('Acta de pólizas cargada')
        destinatarios_notificados = []
        if correo_contratista and '@' in correo_contratista:
            enviado_contratista = self._enviar_correo_sistema({
                'subject': titulo,
                'email_to': correo_contratista,
                'body_html': plantilla_correo_polizas(titulo, Markup(
                    '<p>Las pólizas relacionadas con el contrato '
                    '<strong>%s</strong> fueron aprobadas y el acta definitiva '
                    'quedó registrada.</p>'
                ) % escape(self._contrato_poliza().display_name)),
            }, bloquear=False)
            if enviado_contratista:
                destinatarios_notificados.append({
                    'rol': _('Contratista'),
                    'nombre': self._contrato_poliza().display_name,
                    'correo': correo_contratista,
                })
        if coordinadores:
            enviado_coordinadores = self._enviar_correo_sistema({
                'subject': titulo,
                'email_to': ','.join(sorted(set(coordinadores.mapped('email')))),
                'body_html': plantilla_correo_polizas(titulo, Markup(
                    '<p>El acta definitiva del contrato <strong>%s</strong> ya '
                    'está disponible.</p><p style="text-align:center;margin:28px 0">'
                    '<a href="%s" style="display:inline-block;background:#043484;'
                    'color:#ffffff;padding:12px 22px;text-decoration:none;'
                    'border-radius:5px;font-weight:bold">Ver trámite de pólizas</a></p>'
                ) % (
                    escape(self._contrato_poliza().display_name),
                    escape(self._url_formulario_revision()),
                )),
            }, bloquear=False)
            if enviado_coordinadores:
                destinatarios_notificados.extend({
                    'rol': _('Coordinación'),
                    'nombre': usuario.name,
                    'correo': usuario.email,
                } for usuario in coordinadores)
        if destinatarios_notificados:
            self._registrar_notificacion_correo(
                _('Acta de aprobación de pólizas disponible'),
                destinatarios_notificados,
            )


class SolicitudPolizaLineaRevision(models.Model):
    _inherit = 'contrataciones_fuv.solicitud.poliza.linea'

    def _validar_revision(self):
        self.ensure_one()
        self.solicitud_id._validar_usuario_revision_polizas()
        if self.solicitud_id.estado == EstadoSolicitudPoliza.FINALIZADA.value:
            raise UserError(_('El trámite ya finalizó y no admite nuevas revisiones.'))
        if not self.documento_id:
            raise UserError(_('La póliza todavía no tiene un documento para revisar.'))

    def action_aprobar_poliza(self):
        self._validar_revision()
        if self.estado_revision == 'devuelta':
            raise UserError(_(
                'El contratista debe cargar la corrección antes de aprobar esta póliza.'
            ))
        if self.estado_revision == 'aprobada':
            return False
        self.sudo().write({
            'estado_revision': 'aprobada',
            'revisado_por_id': self.env.user.id,
            'fecha_revision': fields.Datetime.now(),
            'observaciones_juridicas': False,
        })
        self.solicitud_id._actualizar_estado_segun_revision()
        self.solicitud_id.sudo().message_post(
            body=Markup(
                '<p><strong>Póliza aprobada por Jurídica.</strong></p>'
                '<p><strong>Póliza:</strong> %s</p>'
                '<p><strong>Revisado por:</strong> %s</p>'
            ) % (escape(self.tipo_poliza_nombre), escape(self.env.user.name)),
            subtype_xmlid='mail.mt_note',
        )
        # El formulario de la solicitud ya está abierto. Devolver otra acción
        # sobre el mismo registro duplicaría la miga de pan; el refresco suave
        # del FormController relee el estado al finalizar el botón.
        return False

    def action_devolver_poliza(self):
        self._validar_revision()
        if self.estado_revision == 'aprobada':
            raise UserError(_(
                'Una póliza aprobada ya no puede devolverse desde la revisión '
                'jurídica. Si debe reabrirse, solicite el apoyo de Soporte.'
            ))
        if self.estado_revision == 'devuelta':
            raise UserError(_('Esta póliza ya fue devuelta para corrección.'))
        return self._action_abrir_devolucion(modo_soporte=False)

    def action_soporte_devolver_poliza(self):
        """Permite a Soporte reabrir una póliza aprobada, incluso tras el acta."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_('Solo Soporte puede ejecutar esta devolución interna.'))
        if not self.documento_id:
            raise UserError(_('La póliza todavía no tiene un documento para devolver.'))
        if self.estado_revision != 'aprobada':
            raise UserError(_(
                'La devolución interna de Soporte solo aplica a pólizas aprobadas.'
            ))
        return self._action_abrir_devolucion(modo_soporte=True)

    def _action_abrir_devolucion(self, modo_soporte=False):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': (
                _('Soporte: devolver póliza aprobada')
                if modo_soporte
                else _('Devolver póliza para corrección')
            ),
            'res_model': 'contrataciones_fuv.devolver.poliza.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_devolver_poliza_wizard').id,
            'target': 'new',
            'context': {
                'default_linea_id': self.id,
                'default_modo_soporte': modo_soporte,
            },
        }

    def _devolver_para_correccion(self, observacion):
        self._validar_revision()
        if self.estado_revision == 'aprobada':
            raise UserError(_(
                'Una póliza aprobada solo puede reabrirse mediante la acción '
                'interna de Soporte.'
            ))
        return self._procesar_devolucion_para_correccion(observacion)

    def _soporte_devolver_para_correccion(self, observacion):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_('Solo Soporte puede ejecutar esta devolución interna.'))
        if self.estado_revision != 'aprobada':
            raise UserError(_(
                'La devolución interna de Soporte solo aplica a pólizas aprobadas.'
            ))
        if not self.documento_id:
            raise UserError(_('La póliza todavía no tiene un documento para devolver.'))
        solicitud = self.solicitud_id
        acta_anterior = solicitud.acta_aprobacion_documento_id
        if acta_anterior:
            # El acta anterior sigue en Drive como evidencia histórica, pero no
            # puede presentarse como definitiva tras reabrir una póliza.
            solicitud.sudo().write({
                'acta_aprobacion_documento_id': False,
                'acta_cargada_por_id': False,
                'fecha_acta_cargada': False,
            })
            solicitud.sudo().message_post(
                body=Markup(
                    '<p><strong>Acta definitiva anterior sin vigencia por '
                    'devolución de Soporte.</strong></p>'
                    '<p><strong>Archivo conservado en Drive:</strong> %s</p>'
                    '<p><strong>Acción realizada por:</strong> %s</p>'
                    '<p>Se requerirá una nueva acta cuando la corrección quede aprobada.</p>'
                ) % (
                    escape(
                        acta_anterior.sudo().file_name
                        or acta_anterior.sudo().name
                        or _('Sin nombre')
                    ),
                    escape(self.env.user.name),
                ),
                subtype_xmlid='mail.mt_note',
            )
        return self._procesar_devolucion_para_correccion(observacion)

    def _procesar_devolucion_para_correccion(self, observacion):
        self.ensure_one()
        observacion = (observacion or '').strip()
        if not observacion:
            raise UserError(_('Debes registrar la observación de la corrección.'))
        self.solicitud_id._validar_estado_flujo_disponible(
            EstadoSolicitudPoliza.CORRECCION.value
        )
        self.sudo().write({
            'estado_revision': 'devuelta',
            'revisado_por_id': self.env.user.id,
            'fecha_revision': fields.Datetime.now(),
            'observaciones_juridicas': observacion,
        })
        self.solicitud_id._enviar_correccion_contratista(self)
        # La devolución se confirma desde un wizard. Solo se cierra el modal;
        # no se vuelve a navegar al formulario que ya está detrás.
        return {'type': 'ir.actions.act_window_close'}


class DevolverPolizaWizard(models.TransientModel):
    _name = 'contrataciones_fuv.devolver.poliza.wizard'
    _description = 'Devolver una póliza para corrección'

    linea_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza.linea',
        string='Póliza',
        required=True,
        readonly=True,
    )
    modo_soporte = fields.Boolean(
        string='Devolución interna de Soporte',
        readonly=True,
    )
    observacion = fields.Text(
        string='Observación jurídica',
        required=True,
    )

    def action_confirmar_devolucion(self):
        self.ensure_one()
        if self.modo_soporte:
            return self.linea_id._soporte_devolver_para_correccion(
                self.observacion
            )
        return self.linea_id._devolver_para_correccion(self.observacion)

import hmac
import hashlib
import logging
import os
import re
import secrets
import tempfile
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..drive import GoogleDriveAuth
from .correo_poliza import plantilla_correo_polizas
from .constantes import (
    POLIZA_SELECTION,
    EstadoSolicitudPoliza,
    OrigenSolicitudPoliza,
)


_logger = logging.getLogger(__name__)
_TOKEN_DIAS_VIGENCIA = 7
_MAX_ARCHIVO_BYTES = 20 * 1024 * 1024
_EXTENSIONES_PERMITIDAS = {'.pdf', '.png', '.jpg', '.jpeg'}
_TOKEN_HASH_PREFIX = 'sha256$'


class SolicitudPolizaPortal(models.Model):
    _inherit = 'contrataciones_fuv.solicitud.poliza'

    def _correo_contratista(self):
        self.ensure_one()
        return (self._contrato_poliza().correo or '').strip()

    def _url_portal_polizas(self, token):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return '%s/solicitud-polizas/%s/%s' % (
            base_url.rstrip('/'),
            self.id,
            token,
        )

    @staticmethod
    def _hash_token_portal(token):
        digest = hashlib.sha256(str(token).encode('utf-8')).hexdigest()
        return '%s%s' % (_TOKEN_HASH_PREFIX, digest)

    def _token_portal_valido(self, token):
        self.ensure_one()
        if not token or not self.portal_token:
            return False
        token_guardado = str(self.portal_token)
        if token_guardado.startswith(_TOKEN_HASH_PREFIX):
            token_valido = hmac.compare_digest(
                self._hash_token_portal(token),
                token_guardado,
            )
        else:
            # Compatibilidad temporal con enlaces generados antes de almacenar
            # únicamente el hash. Los reenvíos nuevos rotan estos tokens legacy.
            token_valido = hmac.compare_digest(str(token), token_guardado)
        if not token_valido:
            return False
        vigente = bool(
            self.portal_token_expira
            and self.portal_token_expira >= fields.Datetime.now()
        )
        if not vigente:
            return False
        # Una solicitud terminada nunca conserva capacidad pública de escritura,
        # incluso si procede de un token legacy aún almacenado.
        return bool(self.linea_ids and self._hay_lineas_portal_pendientes())

    def _todas_polizas_recibidas(self):
        """Comprueba cada línea sin perder los Many2one todavía vacíos."""
        self.ensure_one()
        return bool(
            self.linea_ids
            and all(linea.documento_id for linea in self.linea_ids)
        )

    def _hay_lineas_portal_pendientes(self):
        """Incluye faltantes iniciales y documentos devueltos para reemplazo."""
        self.ensure_one()
        return bool(self._lineas_cargables_portal())

    def _lineas_cargables_portal(self):
        """Expone únicamente las pólizas autorizadas por el enlace actual."""
        self.ensure_one()
        if self.estado == EstadoSolicitudPoliza.CORRECCION.value:
            return self.linea_ids.filtered(
                lambda linea: linea.estado_revision == 'devuelta'
            )
        return self.linea_ids.filtered(
            lambda linea: not linea.documento_id or linea.estado_revision == 'devuelta'
        )

    def _bloquear_solicitud_portal(self):
        """Serializa el reenvío y las cargas que comparten el mismo token."""
        self.ensure_one()
        self.env.cr.execute(
            'SELECT id FROM "%s" WHERE id = %%s FOR UPDATE' % self._table,
            (self.id,),
        )
        if not self.env.cr.fetchone():
            raise UserError(_('La solicitud de pólizas ya no está disponible.'))
        self.invalidate_recordset([
            'portal_token',
            'portal_token_expira',
            'estado',
            'fecha_envio',
            'linea_ids',
        ])
        # La validación previa del controlador pudo dejar en caché los documentos
        # de todas las líneas mientras esta petición esperaba el bloqueo. Se
        # invalidan también los hijos para leer el estado ya confirmado por otra
        # carga concurrente.
        self.linea_ids.invalidate_recordset([
            'documento_id',
            'estado_revision',
            'fecha_envio_contratista',
        ])

    def _bloquear_y_validar_linea_portal(self, linea, token):
        """Revalida el bearer y reserva una línea antes de hablar con Drive."""
        self.ensure_one()
        self._bloquear_solicitud_portal()
        if not self._token_portal_valido(token):
            raise UserError(_('El enlace no es válido, venció o ya fue utilizado.'))

        self.env.cr.execute(
            'SELECT solicitud_id, documento_id FROM "%s" '
            'WHERE id = %%s FOR UPDATE' % linea._table,
            (linea.id,),
        )
        resultado = self.env.cr.fetchone()
        linea.invalidate_recordset([
            'solicitud_id',
            'documento_id',
            'estado_revision',
        ])
        if not resultado or resultado[0] != self.id:
            raise UserError(_('La póliza no pertenece a esta solicitud.'))
        if resultado[1] and linea.estado_revision != 'devuelta':
            raise UserError(_(
                'Esta póliza ya tiene un documento cargado y no puede reemplazarse '
                'desde el enlace público.'
            ))

    def action_enviar_solicitud(self):
        """Envía las pólizas adicionales agregadas después de la solicitud inicial."""
        self.ensure_one()
        puede_enviar = (
            self._contrato_poliza()._usuario_puede_solicitar_poliza()
            or self.env.user.has_group('Matriz.group_soporte')
        )
        if not puede_enviar:
            raise UserError(_(
                'Solo Coordinación, Dirección Jurídica, Jurídica o Soporte '
                'pueden enviar pólizas adicionales.'
            ))
        if self.estado != EstadoSolicitudPoliza.PENDIENTE_ENVIO.value:
            raise UserError(_(
                'No hay una póliza adicional pendiente de enviar al contratista.'
            ))
        return self.sudo()._enviar_solicitud_contratista(
            automatico=False,
            solo_nuevas=True,
        )

    def action_soporte_solicitar_al_contratista(self):
        """Envía en un solo correo todas las líneas pendientes del ciclo actual."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError(_(
                'Solo Soporte puede utilizar esta acción de envío excepcional.'
            ))
        pendientes = self.linea_ids.filtered(lambda linea: not linea.documento_id)
        if not pendientes:
            raise UserError(_(
                'Esta solicitud no tiene pólizas pendientes para el contratista.'
            ))
        actor_id = self.env.user.id
        return self.sudo().with_context(
            solicitud_poliza_actor_id=actor_id,
        )._enviar_solicitud_contratista(
            automatico=False,
            solo_nuevas=False,
        )

    def _enviar_solicitud_contratista(self, automatico=False, solo_nuevas=False):
        """Genera el token y envía la solicitud; el modo automático es idempotente."""
        self.ensure_one()
        self._bloquear_solicitud_portal()
        if automatico and self.fecha_envio:
            return False
        if not self.linea_ids:
            raise UserError(_('La solicitud no contiene pólizas para enviar.'))
        if self._todas_polizas_recibidas():
            raise UserError(_(
                'Todas las pólizas ya fueron recibidas; no es necesario generar '
                'otro enlace público.'
            ))

        correo = self._correo_contratista()
        if not correo or '@' not in correo:
            raise UserError(_(
                'El contratista relacionado no tiene un correo electrónico válido.'
            ))

        self._crear_carpeta_poliza_solicitada()
        token = secrets.token_urlsafe(32)
        expira = fields.Datetime.now() + timedelta(days=_TOKEN_DIAS_VIGENCIA)
        url = self._url_portal_polizas(token)
        contrato = self._contrato_poliza()
        proveedor = contrato.contratista_proveedor_relacionado
        lineas_por_cargar = self.linea_ids.filtered(
            lambda linea: (
                not linea.documento_id
                and (not solo_nuevas or not linea.fecha_envio_contratista)
            )
        )
        if not lineas_por_cargar:
            raise UserError(_(
                'No hay pólizas nuevas pendientes de notificar al contratista.'
            ))
        etiquetas = ', '.join(
            dict(POLIZA_SELECTION).get(linea.tipo_poliza, linea.tipo_poliza)
            for linea in lineas_por_cargar
        )
        nombre = proveedor.name or contrato.display_name
        es_actualizacion = self.origen in (
            OrigenSolicitudPoliza.OTRO_SI.value,
            OrigenSolicitudPoliza.LIQUIDACION.value,
        )
        if self.origen == OrigenSolicitudPoliza.SOPORTE.value:
            titulo_correo = _('Solicitud extraordinaria de pólizas')
        elif es_actualizacion:
            titulo_correo = _('Actualización de pólizas')
        else:
            titulo_correo = _('Solicitud de pólizas')
        texto_solicitud = (
            _('cargar las versiones actualizadas de las siguientes pólizas:')
            if es_actualizacion
            else _('cargar las siguientes pólizas:')
        )

        contenido = Markup(
            '<p>Cordial saludo, <strong>%s</strong>.</p>'
            '<p>La Fundación Universidad del Valle solicita %s</p>'
            '<div style="padding:12px 16px;background:#f4f7fb;'
            'border-left:4px solid #043484;border-radius:6px">%s</div>'
            '<p>Use el siguiente enlace privado. No es necesario iniciar sesión y no debe compartirlo.</p>'
            '<p style="text-align:center;margin:28px 0">'
            '<a href="%s" style="display:inline-block;background:#043484;'
            'color:#ffffff;padding:12px 22px;text-decoration:none;'
            'border-radius:5px;font-weight:bold">Cargar pólizas</a></p>'
            '<p style="font-size:12px;color:#777">El enlace vence el %s.</p>'
        ) % (
            escape(nombre),
            escape(texto_solicitud),
            escape(etiquetas),
            escape(url),
            escape(fields.Datetime.to_string(expira)),
        )
        body_html = plantilla_correo_polizas(titulo_correo, contenido)
        mail_values = {
            'subject': _('%s - %s') % (
                titulo_correo,
                contrato.display_name,
            ),
            'email_to': correo,
            'body_html': body_html,
            'auto_delete': True,
        }
        # Mismo patrón del flujo nuevo de Contrataciones FUV: el correo sale
        # desde el usuario interno que ejecuta la acción.
        actor_id = self.env.context.get('solicitud_poliza_actor_id') or self.env.user.id
        actor = self.env['res.users'].browse(actor_id).exists()
        remitente = (actor.email or '').strip()
        if remitente:
            mail_values['email_from'] = remitente
        mail = self.env['mail.mail'].sudo().create(mail_values)
        # En Odoo 16 mail.send() puede devolver None/False aun cuando el servidor
        # SMTP confirme el envío. Los fallos reales se comunican mediante excepción.
        # Sin raise_exception=True Odoo puede dejar el correo en estado
        # ``exception`` y continuar, rotando el enlace aunque no se haya enviado.
        mail.send(raise_exception=True)
        momento_envio = fields.Datetime.now()
        lineas_por_cargar.sudo().write({
            'fecha_envio_contratista': momento_envio,
        })
        self.sudo().write({
            # En base de datos se conserva solo el hash; el token original viaja
            # únicamente en el enlace enviado por correo.
            'portal_token': self._hash_token_portal(token),
            'portal_token_expira': expira,
            'fecha_envio': momento_envio,
            'enviado_por_id': actor.id,
            'estado': EstadoSolicitudPoliza.TRAMITE_APROBACION.value,
        })
        if self.origen == OrigenSolicitudPoliza.OTRO_SI.value:
            modo = _('Actualización por otrosí')
        elif self.origen == OrigenSolicitudPoliza.LIQUIDACION.value:
            modo = _('Actualización opcional por liquidación')
        elif self.origen == OrigenSolicitudPoliza.SOPORTE.value:
            modo = _('Solicitud extraordinaria de Soporte')
        elif automatico:
            modo = _('Automático al contratar')
        elif solo_nuevas:
            modo = _('Póliza adicional')
        else:
            modo = _('Continuación del trámite')
        self._registrar_notificacion_correo(
            _('Solicitud de pólizas al contratista'),
            [{
                'rol': _('Contratista'),
                'nombre': nombre,
                'correo': correo,
            }],
            detalle=_('%s. Pólizas: %s') % (modo, etiquetas),
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Solicitud enviada'),
                'message': _('El enlace privado fue enviado a %s.') % correo,
                'type': 'success',
                'sticky': False,
            },
        }

    def _url_formulario_revision(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return '%s/web#id=%s&model=%s&view_type=form' % (
            base_url.rstrip('/'),
            self.id,
            self._name,
        )

    def _grupos_notificacion_revision_polizas(self):
        """Punto de extensión para separar destinatarios según su función."""
        self.ensure_one()
        usuarios = self._contrato_poliza()._usuarios_notificacion_revision_polizas()
        return [{
            'clave': 'revision',
            'usuarios': usuarios,
            'prefijo_asunto': _('Revisión'),
            'titulo': _('Pólizas cargadas'),
            'instruccion': _(
                'Ingrese al SGO para revisar los documentos y decidir si se '
                'aprueban o se devuelven para corrección.'
            ),
            'texto_boton': _('Revisar pólizas'),
        }] if usuarios else []

    def _notificar_responsables_cargue(
        self,
        linea,
        es_correccion=False,
    ):
        """Envía una sola alerta cuando se completa el lote de pólizas."""
        self.ensure_one()
        self.linea_ids.invalidate_recordset([
            'documento_id',
            'documento_notificado_revision_id',
            'fecha_envio_contratista',
        ])

        # Todas las líneas incluidas en un mismo correo al contratista reciben
        # exactamente la misma fecha_envio_contratista. Esa fecha identifica
        # el lote sin agregar otro modelo o una relación paralela.
        if es_correccion:
            lineas_lote = linea
        elif linea.fecha_envio_contratista:
            lineas_lote = self.linea_ids.filtered(
                lambda item: (
                    item.fecha_envio_contratista
                    == linea.fecha_envio_contratista
                )
            )
        else:
            lineas_lote = linea

        # Mientras falte un archivo del lote no se notifica a los revisores.
        # El último cargue enviará un único correo con el resumen completo.
        if not es_correccion and any(
            not item.documento_id for item in lineas_lote
        ):
            return False

        lineas_por_notificar = lineas_lote.filtered(
            lambda item: (
                item.documento_id
                and item.documento_notificado_revision_id != item.documento_id
            )
        )
        if not lineas_por_notificar:
            return False

        grupos_notificacion = self._grupos_notificacion_revision_polizas()
        destinatarios = self.env['res.users']
        for grupo in grupos_notificacion:
            destinatarios |= grupo['usuarios']
        if not grupos_notificacion or not destinatarios:
            self.message_post(
                body=Markup(
                    '<p><strong>No se envió la alerta de revisión.</strong></p>'
                    '<p>El proyecto no tiene coordinadores, directores jurídicos '
                    'o jurídicos asignados con correo activo.</p>'
                ),
                subtype_xmlid='mail.mt_note',
            )
            return False

        resumen_polizas = [
            (
                dict(POLIZA_SELECTION).get(item.tipo_poliza, item.tipo_poliza),
                item.documento_id.file_name or _('Documento cargado'),
            )
            for item in lineas_por_notificar
        ]
        detalle_html = Markup('').join(
            Markup(
                '<li style="margin:0 0 6px"><strong>%s</strong><br/>'
                '<span style="color:#475569">%s</span></li>'
            ) % (escape(etiqueta), escape(archivo))
            for etiqueta, archivo in resumen_polizas
        )
        etiquetas_texto = ', '.join(etiqueta for etiqueta, _archivo in resumen_polizas)
        url = self._url_formulario_revision()
        titulo_revision_base = (
            _('Corrección cargada')
            if es_correccion
            else _('Pólizas cargadas')
        )
        texto_cargue = (
            _('El contratista cargó la corrección solicitada para el contrato')
            if es_correccion
            else _('El contratista completó el cargue de pólizas para el contrato')
        )
        asunto_base = (
            _('Corrección de póliza cargada para revisión - %s')
            if es_correccion
            else _('Pólizas cargadas para revisión - %s')
        ) % self._contrato_poliza().display_name
        # Maquetación con tablas y estilos en línea para conservar el diseño en
        # Gmail, Outlook y otros clientes que eliminan CSS externo.
        body_html_template = Markup(
            '<p style="margin:0 0 18px;line-height:1.55">%s '
            '<strong>%s</strong>.</p>'
            '<table role="presentation" width="100%%" cellspacing="0" cellpadding="0" '
            'style="width:100%%;background:#f4f7fb;border-left:4px solid #0b5cab;'
            'border-radius:6px">'
            '<tr><td style="padding:14px 16px;line-height:1.65">'
            '<strong style="color:#043484">Pólizas recibidas:</strong>'
            '<ul style="margin:8px 0 0;padding-left:20px">%s</ul>'
            '<strong style="color:#043484">Estado:</strong> Trámite de aprobación de pólizas'
            '</td></tr></table>'
            '<p style="margin:20px 0 0;line-height:1.55">%s</p>'
            '<table role="presentation" cellspacing="0" cellpadding="0" '
            'style="margin:26px auto 8px"><tr><td bgcolor="#043484" '
            'style="border-radius:6px">'
            '<a href="%s" style="display:inline-block;padding:13px 24px;color:#ffffff;'
            'text-decoration:none;font-weight:700">%s</a>'
            '</td></tr></table>'
        )
        # El actor funcional de este evento es el contratista que cargó el
        # archivo desde el enlace público. Se conserva el mismo criterio usado
        # por el flujo nuevo de Contrataciones FUV: email_from corresponde al
        # correo de quien origina la notificación.
        correo_contratista = self._correo_contratista()
        try:
            with self.env.cr.savepoint():
                correos_ya_notificados = set()
                for grupo in grupos_notificacion:
                    correos_grupo = {}
                    for correo in grupo['usuarios'].mapped('email'):
                        correo_limpio = (correo or '').strip()
                        clave_correo = correo_limpio.casefold()
                        if correo_limpio and clave_correo not in correos_ya_notificados:
                            correos_grupo.setdefault(clave_correo, correo_limpio)
                    if not correos_grupo:
                        continue
                    correos_ya_notificados.update(correos_grupo)
                    contenido_grupo = body_html_template % (
                        escape(texto_cargue),
                        escape(self._contrato_poliza().display_name),
                        detalle_html,
                        escape(grupo['instruccion']),
                        escape(url),
                        escape(grupo['texto_boton']),
                    )
                    cuerpo_grupo = plantilla_correo_polizas(
                        titulo_revision_base, contenido_grupo,
                    )
                    mail_values = {
                        'subject': _('%s: %s') % (
                            grupo['prefijo_asunto'],
                            asunto_base,
                        ),
                        'email_to': ','.join(sorted(correos_grupo.values())),
                        'body_html': cuerpo_grupo,
                        'auto_delete': True,
                    }
                    if correo_contratista and '@' in correo_contratista:
                        mail_values['email_from'] = correo_contratista
                    self.env['mail.mail'].sudo().create(mail_values).send(
                        raise_exception=True
                    )
                for item in lineas_por_notificar:
                    item.sudo().write({
                        'documento_notificado_revision_id': item.documento_id.id,
                    })
        except Exception:
            _logger.exception(
                'No fue posible notificar el lote de pólizas %s.',
                lineas_por_notificar.ids,
            )
            self.message_post(
                body=Markup(
                    '<p><strong>No fue posible enviar por correo la alerta de '
                    'revisión.</strong></p><p>El documento sí quedó cargado y puede '
                    'revisarse desde esta solicitud.</p>'
                ),
                subtype_xmlid='mail.mt_note',
            )
            return False
        destinatarios_traza = []
        for usuario in destinatarios:
            roles = []
            if usuario.has_group('Matriz.group_contratacion_coordinador'):
                roles.append(_('Coordinación'))
            if usuario.has_group('Matriz.group_contratacion_asis_coordinador'):
                roles.append(_('Asistente de Coordinación'))
            if usuario.has_group('Matriz.group_contratacion_director_juridico'):
                roles.append(_('Dirección Jurídica'))
            if usuario.has_group('Matriz.group_contratacion_juridico'):
                roles.append(_('Jurídica'))
            destinatarios_traza.append({
                'rol': ' / '.join(roles) or _('Responsable de revisión'),
                'nombre': usuario.name,
                'correo': usuario.email,
            })
        self._registrar_notificacion_correo(
            (
                _('Corrección disponible para revisión')
                if es_correccion
                else _('Póliza disponible para revisión')
            ),
            destinatarios_traza,
            detalle=etiquetas_texto,
        )
        return True

    def _cargar_archivo_desde_portal(
        self,
        linea,
        archivo,
        token=None,
        gestionar_token=True,
        registrar_trazabilidad=True,
        notificar_responsables=True,
        es_correccion_lote=None,
        retirar_documento_anterior=True,
    ):
        self.ensure_one()
        if linea.solicitud_id != self:
            raise UserError(_('La póliza no pertenece a esta solicitud.'))
        self._bloquear_y_validar_linea_portal(linea, token)
        contenido = archivo.read(_MAX_ARCHIVO_BYTES + 1)
        if not contenido:
            raise UserError(_('El archivo seleccionado está vacío.'))
        if len(contenido) > _MAX_ARCHIVO_BYTES:
            raise UserError(_('El archivo supera el límite permitido de 20 MB.'))

        nombre_original = os.path.basename(archivo.filename or 'poliza.pdf')
        extension = os.path.splitext(nombre_original)[1].lower()
        if extension not in _EXTENSIONES_PERMITIDAS:
            raise UserError(_('Solo se permiten archivos PDF, PNG, JPG o JPEG.'))
        nombre_seguro = re.sub(r'[^A-Za-z0-9._ -]+', '_', nombre_original).strip(' ._')
        etiqueta = dict(POLIZA_SELECTION).get(linea.tipo_poliza, linea.tipo_poliza)
        nombre_drive = '%s_%s_%s' % (
            etiqueta,
            secrets.token_hex(4),
            nombre_seguro or ('poliza' + extension),
        )

        carpeta = self._crear_carpeta_poliza_solicitada()
        drive_api = GoogleDriveAuth.get_instance().get_drive()
        archivo_google = drive_api.CreateFile({
            'title': nombre_drive,
            'parents': [{'id': carpeta.folder_id2}],
        })
        ruta_temporal = False
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temporal:
                temporal.write(contenido)
                ruta_temporal = temporal.name
            archivo_google.SetContentFile(ruta_temporal)
            archivo_google.Upload(param={'supportsAllDrives': True})
            archivo_google.FetchMetadata()
            google_id = archivo_google.get('id')
            if not google_id:
                raise UserError(_('Google Drive no devolvió el identificador del archivo.'))

            preview_url = archivo_google.get('embedLink') or archivo_google.get('webContentLink')
            edit_url = archivo_google.get('alternateLink')
            documento = self.env['drive'].sudo().create({
                'file_name': nombre_drive,
                'file_id': google_id,
                'drive_file_id': google_id,
                'folder_id': carpeta.folder_id2,
                'stage2_id': carpeta.id,
                'preview_url': preview_url,
                'edit_url': edit_url,
                'file_url': edit_url,
                'show_on_dashboard': False,
                'user_id': self.solicitado_por_id.id,
            })
            era_correccion = (
                self.estado == EstadoSolicitudPoliza.CORRECCION.value
                if es_correccion_lote is None else es_correccion_lote
            )
            documento_anterior = linea.documento_id
            linea.sudo().write({
                'documento_id': documento.id,
                'estado_revision': 'pendiente',
                'revisado_por_id': False,
                'fecha_revision': False,
                'observaciones_juridicas': False,
            })
            if (
                retirar_documento_anterior
                and documento_anterior
                and documento_anterior != documento
            ):
                otras_lineas = self.env[
                    'contrataciones_fuv.solicitud.poliza.linea'
                ].sudo().search_count([
                    ('id', '!=', linea.id),
                    ('documento_id', '=', documento_anterior.id),
                ])
                if not otras_lineas:
                    try:
                        with self.env.cr.savepoint():
                            documento_anterior.sudo().unlink()
                    except Exception:
                        _logger.exception(
                            'No fue posible retirar la versión anterior de la póliza %s.',
                            linea.id,
                        )
            self._actualizar_estado_segun_revision()
            lineas_iniciales_pendientes = self.linea_ids.filtered(
                lambda item: not item.documento_id
            )
            if gestionar_token and era_correccion and lineas_iniciales_pendientes:
                # El token de corrección solo autorizaba la línea devuelta. Se
                # rota nuevamente y se envía otro enlace para continuar con las
                # pólizas originales que todavía no habían sido cargadas.
                self.sudo()._enviar_solicitud_contratista(automatico=False)
            elif gestionar_token and not self._hay_lineas_portal_pendientes():
                # La última carga consume definitivamente el enlace público.
                self.sudo().write({
                    'portal_token': False,
                    'portal_token_expira': False,
                })
            if registrar_trazabilidad:
                self.message_post(
                    body=Markup(
                        '<p><strong>%s</strong></p>'
                        '<p><strong>Póliza:</strong> %s</p>'
                        '<p><strong>Archivo:</strong> %s</p>'
                    ) % (
                        escape(
                            _('El contratista cargó la corrección solicitada.')
                            if era_correccion
                            else _('El contratista cargó una póliza.')
                        ),
                        escape(etiqueta),
                        escape(nombre_original),
                    ),
                    subtype_xmlid='mail.mt_note',
                )
            if notificar_responsables:
                self._notificar_responsables_cargue(
                    linea,
                    es_correccion=era_correccion,
                )
            return documento
        except Exception:
            if archivo_google.get('id'):
                try:
                    archivo_google.Trash()
                except Exception:
                    _logger.exception('No fue posible retirar el archivo incompleto de Drive.')
            raise
        finally:
            if ruta_temporal and os.path.exists(ruta_temporal):
                os.unlink(ruta_temporal)

    def _cargar_lote_desde_portal(self, cargas, token):
        """Carga varias pólizas de un envío y notifica una sola vez al final."""
        self.ensure_one()
        if not cargas:
            raise UserError(_('Seleccione al menos una póliza para cargar.'))
        self._bloquear_solicitud_portal()
        if not self._token_portal_valido(token):
            raise UserError(_('El enlace no es válido, venció o ya fue utilizado.'))
        permitidas = set(self._lineas_cargables_portal().ids)
        if len({linea.id for linea, _archivo in cargas}) != len(cargas) or any(
            linea.id not in permitidas or linea.solicitud_id != self
            for linea, _archivo in cargas
        ):
            raise UserError(_('El lote incluye una póliza no disponible para cargar.'))

        era_correccion = self.estado == EstadoSolicitudPoliza.CORRECCION.value
        ids_google = []
        documentos_anteriores = self.env['drive']
        lineas_cargadas = self.env['contrataciones_fuv.solicitud.poliza.linea']
        try:
            for linea, archivo in cargas:
                if linea.documento_id:
                    documentos_anteriores |= linea.documento_id
                documento = self._cargar_archivo_desde_portal(
                    linea,
                    archivo,
                    token=token,
                    gestionar_token=False,
                    notificar_responsables=False,
                    es_correccion_lote=era_correccion,
                    retirar_documento_anterior=False,
                )
                ids_google.append(documento.file_id or documento.drive_file_id)
                lineas_cargadas |= linea

            correcciones_pendientes = self.linea_ids.filtered(
                lambda linea: linea.estado_revision == 'devuelta'
            )
            if (
                era_correccion
                and not correcciones_pendientes
                and self.linea_ids.filtered(lambda linea: not linea.documento_id)
            ):
                # El enlace de corrección solo autorizó las devueltas; las
                # pendientes originales reciben un enlace nuevo como antes.
                self.sudo()._enviar_solicitud_contratista(automatico=False)
            elif not self._hay_lineas_portal_pendientes():
                self.sudo().write({
                    'portal_token': False,
                    'portal_token_expira': False,
                })

            if era_correccion:
                self._notificar_responsables_cargue(
                    lineas_cargadas, es_correccion=True,
                )
            else:
                lotes_notificados = set()
                for linea in lineas_cargadas:
                    clave_lote = linea.fecha_envio_contratista or linea.id
                    if clave_lote in lotes_notificados:
                        continue
                    lotes_notificados.add(clave_lote)
                    self._notificar_responsables_cargue(linea)

            # La versión reemplazada se elimina únicamente después de que
            # todo el lote nuevo quedó cargado. Si alguna carga falló, aún está
            # disponible para restaurar la transacción de Odoo.
            for anterior in documentos_anteriores:
                if self.env['contrataciones_fuv.solicitud.poliza.linea'].sudo().search_count([
                    ('documento_id', '=', anterior.id),
                ]):
                    continue
                try:
                    with self.env.cr.savepoint():
                        anterior.sudo().unlink()
                except Exception:
                    _logger.exception(
                        'No fue posible retirar la versión anterior %s.', anterior.id,
                    )
        except Exception:
            # La transacción Odoo se revertirá en el controlador. Drive es
            # externo: se retiran los archivos ya subidos de este mismo lote.
            if ids_google:
                try:
                    drive_api = GoogleDriveAuth.get_instance().get_drive()
                except Exception:
                    _logger.exception(
                        'No fue posible retirar todos los archivos de un lote fallido: %s.',
                        ids_google,
                    )
                else:
                    for google_id in ids_google:
                        if not google_id:
                            continue
                        try:
                            drive_api.CreateFile({'id': google_id}).Trash()
                        except Exception:
                            _logger.exception(
                                'No fue posible retirar el archivo %s de un lote fallido.',
                                google_id,
                            )
            raise
        return len(cargas)

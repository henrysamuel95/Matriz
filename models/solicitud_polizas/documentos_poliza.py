from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import POLIZA_SELECTION, EstadoSolicitudPoliza


class DriveDocumentoSolicitudPoliza(models.Model):
    _inherit = 'drive'

    def _validar_carga_poliza_desde_contexto(self):
        linea_id = self.env.context.get('solicitud_poliza_linea_id')
        if not linea_id:
            return
        linea = self.env['contrataciones_fuv.solicitud.poliza.linea'].browse(
            linea_id
        ).exists()
        if not linea:
            raise UserError(_('No se encontró la póliza relacionada con el documento.'))
        linea._validar_carga_documento()

    def _validar_carga_acta_desde_contexto(self):
        solicitud_id = self.env.context.get('solicitud_poliza_acta_id')
        if not solicitud_id:
            return
        solicitud = self.env['contrataciones_fuv.solicitud.poliza'].browse(
            solicitud_id
        ).exists()
        if not solicitud:
            raise UserError(_('No se encontró la solicitud relacionada con el acta.'))
        solicitud._validar_usuario_carga_acta()
        solicitud._validar_polizas_aprobadas_para_acta()
        if solicitud.acta_aprobacion_documento_id:
            raise UserError(_('La solicitud ya tiene un acta definitiva cargada.'))

    @api.model
    def create(self, vals):
        # El formulario de Drive crea y sube el archivo antes de ejecutar
        # action_save_and_close; validar aquí evita una carga externa indebida.
        self._validar_carga_poliza_desde_contexto()
        self._validar_carga_acta_desde_contexto()
        return super().create(vals)

    def write(self, vals):
        if 'file_data' in vals:
            self._validar_carga_poliza_desde_contexto()
            self._validar_carga_acta_desde_contexto()
        return super().write(vals)

    def unlink(self):
        """Reabre el cargue si se elimina el acta definitiva relacionada."""
        solicitudes_otros = self.env[
            'contrataciones_fuv.solicitud.poliza'
        ].sudo().search([
            ('otros_documentos_ids', 'in', self.ids),
        ])
        for solicitud in solicitudes_otros:
            documentos = solicitud.otros_documentos_ids & self
            nombres = ', '.join(
                documento.file_name
                or documento.name
                or _('Documento sin nombre')
                for documento in documentos
            )
            solicitud.message_post(
                body=Markup(
                    '<p><strong>Otro documento retirado.</strong></p>'
                    '<p><strong>Archivo:</strong> %s</p>'
                ) % escape(nombres or _('Documento sin nombre')),
                subtype_xmlid='mail.mt_note',
            )
        solicitudes = self.env[
            'contrataciones_fuv.solicitud.poliza'
        ].sudo().search([
            ('acta_aprobacion_documento_id', 'in', self.ids),
        ])
        for solicitud in solicitudes:
            solicitud.write({
                'acta_aprobacion_documento_id': False,
                'acta_cargada_por_id': False,
                'fecha_acta_cargada': False,
                'estado': EstadoSolicitudPoliza.PENDIENTE_ACTA.value,
            })
            solicitud.message_post(
                body=Markup(
                    '<p><strong>Acta definitiva de pólizas eliminada.</strong></p>'
                    '<p>El trámite quedó nuevamente pendiente de cargar el acta.</p>'
                ),
                subtype_xmlid='mail.mt_note',
            )
        return super().unlink()

    def action_save_and_close(self):
        linea_id = self.env.context.get('solicitud_poliza_linea_id')
        self._validar_carga_poliza_desde_contexto()
        self._validar_carga_acta_desde_contexto()
        resultado = super().action_save_and_close()
        solicitud_acta_id = self.env.context.get('solicitud_poliza_acta_id')
        solicitud_otro_id = self.env.context.get(
            'solicitud_poliza_otro_documento_id'
        )

        if solicitud_otro_id:
            solicitud = self.env[
                'contrataciones_fuv.solicitud.poliza'
            ].browse(solicitud_otro_id).exists()
            if not solicitud:
                raise UserError(_('No se encontró la solicitud relacionada.'))
            if not solicitud._usuario_puede_cargar_otro_documento():
                raise UserError(_(
                    'No tiene permisos para cargar otros documentos en pólizas.'
                ))
            carpeta = solicitud.solicitud_poliza_drive_folder_id
            if not carpeta or self.folder_id != carpeta.folder_id2:
                raise UserError(_(
                    'El documento no quedó dirigido a la carpeta "Pólizas".'
                ))
            solicitud.sudo().write({
                'otros_documentos_ids': [(4, self.id)],
            })
            solicitud.message_post(
                body=Markup(
                    '<p><strong>Otro documento cargado.</strong></p>'
                    '<p><strong>Archivo:</strong> %s</p>'
                    '<p><strong>Cargado por:</strong> %s</p>'
                ) % (
                    escape(self.file_name or self.name or _('Documento sin nombre')),
                    escape(self.env.user.name),
                ),
                subtype_xmlid='mail.mt_note',
            )
            return resultado

        if solicitud_acta_id:
            solicitud = self.env[
                'contrataciones_fuv.solicitud.poliza'
            ].browse(solicitud_acta_id).exists()
            if not solicitud:
                raise UserError(_('No se encontró la solicitud relacionada con el acta.'))
            solicitud._validar_usuario_carga_acta()
            solicitud._validar_polizas_aprobadas_para_acta()
            carpeta = solicitud.solicitud_poliza_drive_folder_id
            if not carpeta or self.folder_id != carpeta.folder_id2:
                raise UserError(_(
                    'El acta no quedó dirigida a la carpeta "Pólizas".'
                ))
            solicitud.sudo().write({
                'acta_aprobacion_documento_id': self.id,
                'acta_cargada_por_id': self.env.user.id,
                'fecha_acta_cargada': fields.Datetime.now(),
                'estado': EstadoSolicitudPoliza.FINALIZADA.value,
                'portal_token': False,
                'portal_token_expira': False,
            })
            solicitud.sudo().message_post(
                body=Markup(
                    '<p><strong>Acta definitiva de pólizas cargada.</strong></p>'
                    '<p><strong>Archivo:</strong> %s</p>'
                    '<p><strong>Cargada por:</strong> %s</p>'
                ) % (
                    escape(self.file_name or self.name or 'Sin nombre'),
                    escape(self.env.user.name),
                ),
                subtype_xmlid='mail.mt_note',
            )
            solicitud._notificar_acta_cargada()

        if not linea_id:
            return resultado

        linea = self.env['contrataciones_fuv.solicitud.poliza.linea'].browse(
            linea_id
        ).exists()
        if not linea:
            raise UserError(_('No se encontró la póliza relacionada con el documento.'))

        carpeta = linea.solicitud_id.solicitud_poliza_drive_folder_id
        if not carpeta or not carpeta.folder_id2:
            raise UserError(_(
                'La solicitud no tiene una carpeta de pólizas válida en Drive.'
            ))
        if self.folder_id != carpeta.folder_id2:
            raise UserError(_(
                'El documento no quedó dirigido a la carpeta "Pólizas".'
            ))

        # La escritura está limitada a este vínculo validado. Así un lector
        # puede cargar el archivo sin obtener permiso general para editar líneas.
        linea.sudo().write({
            'documento_id': self.id,
            'estado_revision': 'pendiente',
            'revisado_por_id': False,
            'fecha_revision': False,
            'observaciones_juridicas': False,
        })
        linea.solicitud_id._actualizar_estado_segun_revision()
        etiqueta = dict(POLIZA_SELECTION).get(
            linea.tipo_poliza,
            linea.tipo_poliza,
        )
        linea.solicitud_id.message_post(
            body=Markup(
                '<p><strong>Documento de póliza cargado.</strong></p>'
                '<p><strong>Póliza:</strong> %s</p>'
                '<p><strong>Archivo:</strong> %s</p>'
                '<p><strong>Cargado por:</strong> %s</p>'
            ) % (
                escape(etiqueta),
                escape(self.file_name or self.name or 'Sin nombre'),
                escape(self.env.user.name),
            ),
            subtype_xmlid='mail.mt_note',
        )
        return resultado

    def action_ver_otro_documento_poliza(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Otro documento'),
            'res_model': 'drive',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': {'default_invisible_todo': True},
        }

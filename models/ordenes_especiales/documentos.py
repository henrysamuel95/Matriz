import base64
import os
import tempfile

from googleapiclient.errors import HttpError
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import html_escape

from ..drive import GoogleDriveAuth


class OrdenesEspecialesDocumento(models.Model):
    _name = 'ordenes_especiales.documento'
    _description = 'Documento de orden especial'
    _order = 'tipo, id desc'

    orden_id = fields.Many2one('ordenes_especiales', required=True, ondelete='cascade')
    tipo = fields.Selection([('factura', 'Facturas'), ('otro', 'Otros')], required=True)
    clase_documento = fields.Selection([
        ('factura', 'Factura'),
        ('otro', 'Otro'),
        ('certificacion_conjunto', 'Certificación conjunta'),
        ('acusacion', 'Causación'),
        ('documento_cruce', 'Documento Cruce'),
        ('orden_pago', 'Orden de pago'),
        ('comprobante_pago', 'Comprobante de pago'),
        ('comprobante_bancario', 'Comprobante Bancario'),
    ], string='Clase de documento', required=True, default='otro')
    archivo = fields.Binary(string='Archivo', required=True, attachment=True)
    archivo_nombre = fields.Char(string='Nombre del archivo', required=True)
    drive_document_id = fields.Many2one('drive', string='Archivo en Drive', readonly=True, copy=False)

    def init(self):
        """Repara los espejos antiguos que apuntaban a la carpeta como archivo."""
        self.env.cr.execute("""
            UPDATE drive AS archivo
               SET drive_file_id = archivo.file_id
              FROM ordenes_especiales_documento AS documento
             WHERE documento.drive_document_id = archivo.id
               AND archivo.file_id IS NOT NULL
               AND archivo.file_id != ''
               AND archivo.drive_file_id IS DISTINCT FROM archivo.file_id
        """)

    def _puede_editar_documentos(self):
        return any(self.env.user.has_group(grupo) for grupo in (
            'Matriz.group_contratacion_coordinador',
            'Matriz.group_contratacion_asis_coordinador',
            'Matriz.group_contabilidad',
            'Matriz.group_tesoreria_auxiliar',
            'Matriz.group_tesoreria',
            'Matriz.group_soporte',
        ))

    def _validar_etapa_documento(self):
        if self.env.su or self.env.user.has_group('Matriz.group_soporte'):
            return
        etapas_por_clase = {
            'factura': 'coordinador',
            'otro': 'coordinador',
            'certificacion_conjunto': 'coordinador',
            'acusacion': 'contabilidad',
            'documento_cruce': 'contabilidad',
            'orden_pago': 'auxiliar_tesoreria',
            'comprobante_pago': 'tesoreria',
            'comprobante_bancario': 'tesoreria',
        }
        grupos_por_clase = {
            'factura': (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            'otro': (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            'certificacion_conjunto': (
                'Matriz.group_contratacion_coordinador',
            ),
            'acusacion': ('Matriz.group_contabilidad',),
            'documento_cruce': ('Matriz.group_contabilidad',),
            'orden_pago': ('Matriz.group_tesoreria_auxiliar',),
            'comprobante_pago': ('Matriz.group_tesoreria',),
            'comprobante_bancario': ('Matriz.group_tesoreria',),
        }
        for documento in self:
            etapa = etapas_por_clase[documento.clase_documento]
            if documento.orden_id.estado_ordenes_especiales != etapa:
                raise AccessError(_('No puede cargar este documento en la etapa actual.'))
            if not any(
                self.env.user.has_group(grupo)
                for grupo in grupos_por_clase[documento.clase_documento]
            ):
                raise AccessError(_('No tiene el rol requerido para gestionar este documento.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self._puede_editar_documentos():
            raise AccessError(_('No tiene permiso para cargar documentos.'))
        for vals in vals_list:
            clase = vals.get('clase_documento', 'otro')
            vals['tipo'] = 'factura' if clase == 'factura' else 'otro'
        documentos = super().create(vals_list)
        documentos._validar_etapa_documento()
        documentos._subir_a_drive()
        return documentos

    def write(self, vals):
        if not self.env.su and not self._puede_editar_documentos():
            raise AccessError(_('No tiene permiso para editar documentos.'))
        if any(
            campo in vals and any(documento[campo] != vals[campo] for documento in self)
            for campo in ('archivo', 'archivo_nombre')
        ):
            raise UserError(_(
                'Para reemplazar un archivo, elimine solo ese documento y cárguelo de nuevo.'
            ))
        if 'clase_documento' in vals:
            vals['tipo'] = 'factura' if vals['clase_documento'] == 'factura' else 'otro'
        self._validar_etapa_documento()
        return super().write(vals)

    def unlink(self):
        if not self.env.su and not self._puede_editar_documentos():
            raise AccessError(_('No tiene permiso para eliminar documentos.'))
        self._validar_etapa_documento()
        ordenes_certificacion = self.filtered(
            lambda documento: (
                documento.clase_documento == 'certificacion_conjunto'
            )
        ).mapped('orden_id')
        self._eliminar_archivo_drive()
        resultado = super().unlink()
        if ordenes_certificacion:
            ordenes_certificacion.sudo().write({
                'certificacion_conjunto_aprobada_coordinador': False,
                'certificacion_conjunto_aprobada_ventanilla': False,
            })
        return resultado

    def action_eliminar_documento(self):
        """Elimina este documento y deja que la vista modal se refresque desde JS."""
        self.ensure_one()
        self.unlink()
        return True

    def action_habilitar_nueva_carga(self):
        """Libera solo un registro cuyo archivo ya no está en Drive."""
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede habilitar una nueva carga.'))
        orden = self.orden_id
        nombre = self.archivo_nombre or _('Documento')
        espejo_anterior = self.drive_document_id.sudo()
        archivo_anterior_id = espejo_anterior.file_id if espejo_anterior else False
        if archivo_anterior_id:
            if self.env['drive.file'].sudo().search_count([
                ('folder_id2', '=', archivo_anterior_id),
            ]) or self.env['drive'].sudo().search_count([
                ('id', '!=', espejo_anterior.id),
                ('file_id', '=', archivo_anterior_id),
            ]) or self.env['ordenes_especiales.documento'].sudo().search_count([
                ('id', '!=', self.id),
                ('drive_document_id', '=', espejo_anterior.id),
            ]):
                raise UserError(_(
                    'El vínculo anterior es compartido o apunta a una carpeta. '
                    'Soporte debe revisarlo antes de habilitar otra carga.'
                ))
            try:
                metadata = espejo_anterior._drive_service_v3().files().get(
                    fileId=archivo_anterior_id,
                    fields='id,trashed',
                    supportsAllDrives=True,
                ).execute()
            except HttpError as error:
                if error.resp.status != 404:
                    raise UserError(_(
                        'No se pudo comprobar el archivo anterior en Drive: %s'
                    ) % error)
            else:
                if not metadata.get('trashed'):
                    raise UserError(_(
                        'El archivo todavía existe en Drive. Actualice el explorador '
                        'antes de habilitar una nueva carga.'
                    ))

        # El archivo remoto ya no está disponible. Retirar exclusivamente sus
        # vínculos locales; esta acción no restaura ni sube contenido a Drive.
        self.sudo().write({'drive_document_id': False})
        if espejo_anterior and espejo_anterior.exists():
            espejo_anterior.with_context(
                skip_drive_sync=True, skip_integrity=True,
            ).write({'file_id': False, 'drive_file_id': False})
            espejo_anterior.unlink()

        if self.clase_documento == 'certificacion_conjunto':
            orden.sudo().write({
                'certificacion_conjunto_aprobada_coordinador': False,
                'certificacion_conjunto_aprobada_ventanilla': False,
            })
        super(OrdenesEspecialesDocumento, self.sudo()).unlink()
        orden.sudo().message_post(
            body=_('Soporte habilitó una nueva carga de %s; no se subió ningún archivo.')
            % html_escape(nombre),
        )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_ver_documento(self):
        """Abre el visor estándar de Google Drive dentro de SGO."""
        self.ensure_one()
        drive_documento = self.drive_document_id.sudo()
        if not drive_documento or not drive_documento.file_id:
            raise UserError(_('Este documento todavía no está disponible en Drive.'))

        # El visor de Google se ejecuta con la sesión del usuario. Se concede
        # lectura únicamente sobre la carpeta de esta orden especial.
        self.orden_id._compartir_carpeta_drive(self.env.user)
        drive_documento.asegurar_visor_drive()
        drive_documento.with_context(
            skip_drive_sync=True,
            skip_integrity=True,
        ).write({'file_data_visible': False})
        return {
            'type': 'ir.actions.act_window',
            'name': self.archivo_nombre or _('Documento de orden especial'),
            'view_mode': 'form',
            'res_model': 'drive',
            'res_id': drive_documento.id,
            'target': 'new',
            'context': {
                'form_view_initial_mode': 'readonly',
                'create': False,
                'edit': False,
                'delete': False,
            },
        }

    def _eliminar_archivo_drive(self):
        """Envía el archivo a la papelera con la cuenta de servicio de Odoo."""
        archivos = []
        for documento in self:
            registro_drive = documento.drive_document_id.sudo()
            if not registro_drive:
                continue
            archivo_google_id = registro_drive.file_id
            if (
                not archivo_google_id
                or archivo_google_id == documento.orden_id.drive_file_id.folder_id2
                or self.env['drive.file'].sudo().search_count([
                    ('folder_id2', '=', archivo_google_id),
                ])
                or self.env['drive'].sudo().search_count([
                    ('id', '!=', registro_drive.id),
                    ('file_id', '=', archivo_google_id),
                ])
                or self.env['ordenes_especiales.documento'].sudo().search_count([
                    ('id', '!=', documento.id),
                    ('drive_document_id.file_id', '=', archivo_google_id),
                ])
            ):
                raise UserError(_(
                    'El archivo %s tiene una relación de Drive inválida o compartida. '
                    'Contacte a Soporte antes de eliminarlo.'
                ) % documento.archivo_nombre)
            archivos.append((documento, registro_drive, archivo_google_id))

        drive = GoogleDriveAuth.get_instance().get_drive() if archivos else False
        for documento, registro_drive, archivo_google_id in archivos:
            try:
                drive.CreateFile({'id': archivo_google_id}).Trash()
            except Exception as error:
                raise UserError(_(
                    'No se pudo eliminar %s de Drive: %s'
                ) % (documento.archivo_nombre, error))
            registro_drive.unlink()

    def _nombre_archivo_disponible(self, carpeta_id, nombre_original):
        """Conserva archivos con el mismo nombre agregando (1), (2), etc."""
        nombre_original = nombre_original or _('Documento')
        base, extension = os.path.splitext(nombre_original)

        nombres_existentes = set(
            self.env['drive'].sudo().search([
                ('folder_id', '=', carpeta_id),
            ]).mapped('file_name')
        )

        if nombre_original not in nombres_existentes:
            return nombre_original

        indice = 1
        while True:
            candidato = '%s (%s)%s' % (base, indice, extension)
            if candidato not in nombres_existentes:
                return candidato
            indice += 1

    def _subir_a_drive(self):
        for documento in self:
            if not documento.archivo or not documento.archivo_nombre:
                continue
            documento.orden_id._asegurar_carpetas_documentos()
            carpeta = documento.orden_id.drive_file_id
            if not carpeta or not carpeta.folder_id2:
                raise UserError(_(
                    'No fue posible preparar la carpeta principal de la orden especial en Drive.'
                ))
            drive = GoogleDriveAuth.get_instance().get_drive()
            nombre_drive = documento._nombre_archivo_disponible(
                carpeta.folder_id2,
                documento.archivo_nombre,
            )
            with tempfile.NamedTemporaryFile(delete=False) as temporal:
                temporal.write(base64.b64decode(documento.archivo))
                ruta_temporal = temporal.name
            archivo_drive = False
            try:
                archivo_drive = drive.CreateFile({
                    'title': nombre_drive, 'mimeType': 'application/octet-stream',
                    'parents': [{'id': carpeta.folder_id2}],
                })
                archivo_drive.SetContentFile(ruta_temporal)
                archivo_drive.Upload(param={'supportsAllDrives': True})
                # El archivo ya fue subido manualmente arriba. El contexto
                # from_process_button evita que drive.create() vuelva a ejecutar
                # upload_to_drive() al recibir file_data y genere un duplicado.
                registro_drive = self.env['drive'].sudo().with_context(
                    from_process_button=True,
                ).create({
                    'file_name': nombre_drive,
                    'file_id': archivo_drive['id'],
                    'file_url': 'https://drive.google.com/file/d/%s/view' % archivo_drive['id'],
                    'file_data': documento.archivo,
                    'folder_id': carpeta.folder_id2,
                    'drive_file_id': archivo_drive['id'],
                    'stage2_id': carpeta.id,
                })
                documento.sudo().write({'drive_document_id': registro_drive.id})
            except Exception:
                if archivo_drive and archivo_drive.get('id'):
                    try:
                        drive.CreateFile({'id': archivo_drive['id']}).Trash()
                    except Exception:
                        pass
                raise
            finally:
                if os.path.exists(ruta_temporal):
                    os.unlink(ruta_temporal)


class OrdenesEspecialesDocumentoCargaMultiple(models.TransientModel):
    _name = 'ordenes_especiales.documento.carga.multiple'
    _description = 'Carga múltiple de documentos de orden especial'

    orden_id = fields.Many2one(
        'ordenes_especiales', string='Orden especial', required=True, readonly=True,
    )
    clase_documento = fields.Selection([
        ('factura', 'Factura'),
        ('otro', 'Otro'),
        ('acusacion', 'Causación'),
        ('documento_cruce', 'Documento Cruce'),
        ('comprobante_pago', 'Comprobante de pago'),
        ('comprobante_bancario', 'Comprobante Bancario'),
    ], string='Documento', required=True, readonly=True)
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'ordenes_especiales_carga_multiple_attachment_rel',
        'wizard_id',
        'attachment_id',
        string='Archivos',
        required=True,
    )

    def action_cargar(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_('Debe seleccionar al menos un archivo.'))

        documentos = self.env['ordenes_especiales.documento']
        for attachment in self.attachment_ids:
            if not attachment.datas:
                raise UserError(_(
                    'No fue posible leer el archivo %s.'
                ) % (attachment.name or _('sin nombre')))
            documentos.create({
                'orden_id': self.orden_id.id,
                'clase_documento': self.clase_documento,
                'archivo': attachment.datas,
                'archivo_nombre': attachment.name or _('Documento'),
            })

        self.attachment_ids.sudo().unlink()
        return {'type': 'ir.actions.act_window_close'}


class OrdenesEspecialesDocumentoCarga(models.TransientModel):
    _name = 'ordenes_especiales.documento.carga'
    _description = 'Carga única de documento de orden especial'

    orden_id = fields.Many2one(
        'ordenes_especiales', string='Orden especial', required=True, readonly=True,
    )
    clase_documento = fields.Selection([
        ('certificacion_conjunto', 'Certificación conjunta'),
        ('orden_pago', 'Orden de pago'),
        ('acusacion', 'Causación'),
        ('documento_cruce', 'Documento Cruce'),
    ], string='Documento', required=True, readonly=True)
    archivo = fields.Binary(string='Archivo', required=True, attachment=True)
    archivo_nombre = fields.Char(string='Nombre del archivo', required=True)

    def action_cargar(self):
        self.ensure_one()
        existente = self.env['ordenes_especiales.documento'].search_count([
            ('orden_id', '=', self.orden_id.id),
            ('clase_documento', '=', self.clase_documento),
        ])
        if existente:
            raise UserError(_(
                'Este documento ya fue cargado. Elimínelo antes de reemplazarlo.'
            ))
        self.env['ordenes_especiales.documento'].create({
            'orden_id': self.orden_id.id,
            'clase_documento': self.clase_documento,
            'archivo': self.archivo,
            'archivo_nombre': self.archivo_nombre,
        })
        return {'type': 'ir.actions.act_window_close'}

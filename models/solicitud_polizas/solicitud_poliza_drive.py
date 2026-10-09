import logging

from odoo import _, models
from odoo.exceptions import UserError

from ..drive import GoogleDriveAuth


_logger = logging.getLogger(__name__)


class SolicitudPolizaDrive(models.Model):
    _inherit = 'contrataciones_fuv.solicitud.poliza'

    _NOMBRE_CARPETA_POLIZAS = 'Pólizas'
    _NOMBRES_CARPETA_LEGACY = ('Póliza solicitada',)

    def _asegurar_ubicacion_local_carpeta_polizas(
        self,
        carpeta,
        carpeta_contratista,
    ):
        """Mantiene la subcarpeta fuera del nivel raíz de CONTRATOS PS."""
        self.ensure_one()
        if not carpeta:
            return carpeta
        valores = {}
        if carpeta.parent_folder_id != carpeta_contratista:
            valores['parent_folder_id'] = carpeta_contratista.id
        if carpeta.drive_id != carpeta_contratista.drive_id:
            valores['drive_id'] = carpeta_contratista.drive_id.id
        # El tipo de persona corresponde a la carpeta del contratista. Si se
        # copia a una subcarpeta, el navegador puede mostrarla como si fuera
        # otro contratista en el primer nivel de CONTRATOS PS.
        if carpeta.select_persona:
            valores['select_persona'] = False
        if valores:
            carpeta.write(valores)
        return carpeta

    def _normalizar_nombre_carpeta_polizas(self, carpeta, drive=None):
        """Renombra una carpeta legacy tanto en Google Drive como en Odoo."""
        self.ensure_one()
        if not carpeta or not carpeta.folder_id2:
            return carpeta

        nombre_objetivo = self._NOMBRE_CARPETA_POLIZAS
        drive = drive or GoogleDriveAuth.get_instance().get_drive()
        carpeta_google = drive.CreateFile({'id': carpeta.folder_id2})
        carpeta_google.FetchMetadata(fields='id,title')
        if carpeta_google.get('title') != nombre_objetivo:
            carpeta_google['title'] = nombre_objetivo
            carpeta_google.Upload(param={'supportsAllDrives': True})
        if carpeta.folder_name != nombre_objetivo:
            carpeta.write({'folder_name': nombre_objetivo})
        return carpeta

    def _crear_carpeta_poliza_solicitada(self):
        """Crea o reutiliza la carpeta exclusiva de pólizas del contratista."""
        self.ensure_one()
        carpeta_contratista = self._contrato_poliza().drive_file_id
        if not carpeta_contratista or not carpeta_contratista.folder_id2:
            raise UserError(_(
                'El contrato no tiene una carpeta individual del contratista '
                'identificada en Drive.'
            ))

        if self.solicitud_poliza_drive_folder_id:
            carpeta = self._asegurar_ubicacion_local_carpeta_polizas(
                self.solicitud_poliza_drive_folder_id,
                carpeta_contratista,
            )
            return self._normalizar_nombre_carpeta_polizas(carpeta)

        carpeta_general_google_id = (
            carpeta_contratista.drive_id.folder_id
            if carpeta_contratista.drive_id
            else False
        )
        if (
            carpeta_general_google_id
            and carpeta_contratista.folder_id2 == carpeta_general_google_id
        ):
            raise UserError(_(
                'La contratación está enlazada a la carpeta general 3. CONTRATOS PS '
                'y no a la carpeta individual del contratista.'
            ))

        drive = GoogleDriveAuth.get_instance().get_drive()
        parent_google_id = carpeta_contratista.folder_id2
        carpeta_padre_google = drive.CreateFile({'id': parent_google_id})
        carpeta_padre_google.FetchMetadata(fields='id,title,mimeType,parents')
        if carpeta_padre_google.get('mimeType') != 'application/vnd.google-apps.folder':
            raise UserError(_(
                'La ubicación asociada al contrato no es una carpeta válida de Google Drive.'
            ))
        if (
            carpeta_general_google_id
            and carpeta_padre_google.get('id') == carpeta_general_google_id
        ):
            raise UserError(_(
                'No se puede crear la carpeta de pólizas directamente en '
                '3. CONTRATOS PS.'
            ))

        nombre_carpeta = self._NOMBRE_CARPETA_POLIZAS
        nombres_compatibles = (nombre_carpeta,) + self._NOMBRES_CARPETA_LEGACY
        carpeta_odoo = self.env['drive.file'].search([
            ('parent_folder_id', '=', carpeta_contratista.id),
            ('folder_name', 'in', nombres_compatibles),
        ], limit=1)
        if carpeta_odoo and carpeta_odoo.folder_id2:
            carpeta_odoo = self._asegurar_ubicacion_local_carpeta_polizas(
                carpeta_odoo,
                carpeta_contratista,
            )
            carpeta_odoo = self._normalizar_nombre_carpeta_polizas(
                carpeta_odoo,
                drive=drive,
            )
            self.solicitud_poliza_drive_folder_id = carpeta_odoo
            return carpeta_odoo

        carpeta_google = False
        for nombre_busqueda in nombres_compatibles:
            query = (
                "'%s' in parents and title='%s' and "
                "mimeType='application/vnd.google-apps.folder' and trashed=false"
            ) % (parent_google_id, nombre_busqueda)
            carpetas = drive.ListFile({
                'q': query,
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True,
            }).GetList()
            if carpetas:
                carpeta_google = carpetas[0]
                break

        carpeta_creada = False
        if not carpeta_google:
            carpeta_google = drive.CreateFile({
                'title': nombre_carpeta,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': parent_google_id}],
            })
            carpeta_google.Upload(param={'supportsAllDrives': True})
            carpeta_creada = True
        elif carpeta_google.get('title') != nombre_carpeta:
            carpeta_google['title'] = nombre_carpeta
            carpeta_google.Upload(param={'supportsAllDrives': True})

        try:
            carpeta_odoo = self.env['drive.file'].search([
                ('folder_id2', '=', carpeta_google['id']),
            ], limit=1)
            if not carpeta_odoo:
                carpeta_odoo = self.env['drive.file'].create({
                    'folder_name': nombre_carpeta,
                    'folder_id2': carpeta_google['id'],
                    'drive_id': carpeta_contratista.drive_id.id,
                    'parent_folder_id': carpeta_contratista.id,
                })
            else:
                carpeta_odoo = self._asegurar_ubicacion_local_carpeta_polizas(
                    carpeta_odoo,
                    carpeta_contratista,
                )
            self.solicitud_poliza_drive_folder_id = carpeta_odoo
            return carpeta_odoo
        except Exception:
            if carpeta_creada:
                try:
                    carpeta_google.Trash()
                except Exception:
                    _logger.exception(
                        'No fue posible retirar la carpeta de pólizas creada en Drive.'
                    )
            raise

    def unlink(self):
        """Elimina también la carpeta exclusiva y todos sus documentos."""
        carpetas = self.mapped('solicitud_poliza_drive_folder_id').filtered(
            lambda carpeta: not self.search_count([
                ('id', 'not in', self.ids),
                ('solicitud_poliza_drive_folder_id', '=', carpeta.id),
            ])
        )
        carpetas_conservadas = (
            self.mapped('solicitud_poliza_drive_folder_id') - carpetas
        )
        documentos_candidatos = (
            self.mapped('linea_ids.documento_id')
            | self.mapped('acta_aprobacion_documento_id')
            | self.mapped('otros_documentos_ids')
        ).filtered(lambda documento: documento.stage2_id in carpetas_conservadas)
        documentos_exclusivos = self.env['drive']
        linea_model = self.env['contrataciones_fuv.solicitud.poliza.linea'].sudo()
        solicitud_model = self.env['contrataciones_fuv.solicitud.poliza'].sudo()
        for documento in documentos_candidatos:
            usado_en_otra_linea = linea_model.search_count([
                ('solicitud_id', 'not in', self.ids),
                ('documento_id', '=', documento.id),
            ])
            usado_como_otra_acta = solicitud_model.search_count([
                ('id', 'not in', self.ids),
                ('acta_aprobacion_documento_id', '=', documento.id),
            ])
            usado_como_otro_documento = solicitud_model.search_count([
                ('id', 'not in', self.ids),
                ('otros_documentos_ids', 'in', [documento.id]),
            ])
            if (
                not usado_en_otra_linea
                and not usado_como_otra_acta
                and not usado_como_otro_documento
            ):
                documentos_exclusivos |= documento
        resultado = super().unlink()
        if documentos_exclusivos:
            documentos_exclusivos.sudo().unlink()
        if carpetas:
            # drive.file centraliza la eliminación de documentos, espejos y
            # la carpeta física en Google Drive.
            carpetas.sudo().unlink()
        return resultado

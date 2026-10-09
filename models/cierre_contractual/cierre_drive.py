# Gestiona la creación, vinculación y eliminación de la carpeta de cierre contractual en Google Drive.
import logging

from odoo import _, models
from odoo.exceptions import UserError

from ..drive import GoogleDriveAuth


_logger = logging.getLogger(__name__)


class ContratacionesFuvCierreDrive(models.Model):
    _inherit = 'contrataciones_fuv.cierre.proceso'

    def _otorgar_acceso_lector_cierre(self, usuarios):
        """Comparte la carpeta del cierre como lector y omite accesos existentes."""
        self.ensure_one()
        usuarios = usuarios.filtered(
            lambda usuario: usuario.active and not usuario.share and usuario.email
        )
        if not usuarios:
            raise UserError(_(
                'No hay destinatarios activos con correo para compartir los documentos.'
            ))

        carpeta = self.cierre_drive_folder_id or self._crear_carpeta_cierre_contractual()
        if not carpeta or not carpeta.folder_id2:
            raise UserError(_(
                'El proceso no tiene una carpeta de cierre válida para compartir.'
            ))

        drive = GoogleDriveAuth.get_instance().get_drive()
        if not drive:
            raise UserError(_('No fue posible autenticar la conexión con Google Drive.'))

        carpeta_google = drive.CreateFile({'id': carpeta.folder_id2})
        try:
            permisos = carpeta_google.GetPermissions()
        except Exception as error:
            _logger.exception(
                '[CIERRE-CONTRACTUAL] No fue posible consultar permisos de Drive. '
                'proceso_id=%s carpeta_id=%s',
                self.id,
                carpeta.folder_id2,
            )
            raise UserError(_(
                'No fue posible consultar los permisos de los documentos del cierre: %s'
            ) % error)

        correos_con_acceso = {
            (permiso.get('emailAddress') or permiso.get('value') or '').strip().lower()
            for permiso in permisos
            if permiso.get('emailAddress') or permiso.get('value')
        }
        concedidos = []
        omitidos = []
        for usuario in usuarios:
            correo = usuario.email.strip().lower()
            if correo in correos_con_acceso:
                omitidos.append(correo)
                continue
            try:
                carpeta_google.InsertPermission({
                    'type': 'user',
                    'value': usuario.email.strip(),
                    'role': 'reader',
                })
            except Exception as error:
                _logger.exception(
                    '[CIERRE-CONTRACTUAL] No fue posible compartir documentos. '
                    'proceso_id=%s usuario_id=%s correo=%s',
                    self.id,
                    usuario.id,
                    correo,
                )
                raise UserError(_(
                    'No fue posible dar acceso lector a %s sobre los documentos '
                    'del cierre: %s'
                ) % (usuario.name, error))
            correos_con_acceso.add(correo)
            concedidos.append(correo)

        _logger.info(
            '[CIERRE-CONTRACTUAL] Acceso lector sincronizado proceso_id=%s '
            'concedidos=%s omitidos=%s',
            self.id,
            concedidos,
            omitidos,
        )
        return {'concedidos': concedidos, 'omitidos': omitidos}

    def _asegurar_ubicacion_local_carpeta_cierre(
        self,
        carpeta,
        carpeta_contratista,
    ):
        """Fija la subcarpeta como hija del contratista en el árbol local."""
        self.ensure_one()
        if not carpeta:
            return carpeta
        valores = {}
        if carpeta.parent_folder_id != carpeta_contratista:
            valores['parent_folder_id'] = carpeta_contratista.id
        if carpeta.drive_id != carpeta_contratista.drive_id:
            valores['drive_id'] = carpeta_contratista.drive_id.id
        if carpeta.select_persona:
            valores['select_persona'] = False
        if valores:
            carpeta.write(valores)
        return carpeta

    def _crear_carpeta_cierre_contractual(self):
        """
        Crea o identifica la carpeta ``Cierre contractual`` en Google Drive.

        La carpeta se ubica dentro de la carpeta principal del contratista y
        se relaciona con este proceso mediante ``cierre_drive_folder_id``.
        Primero se consulta Odoo y después Google Drive para evitar duplicados.
        """
        # Este método trabaja con un único proceso de cierre contractual.
        self.ensure_one()

        # Obtiene el registro drive.file que representa la carpeta principal
        # del contratista asociado al contrato que se está cerrando.
        carpeta_contratista = self.contract_id.drive_file_id

        # folder_id2 contiene el identificador real de la carpeta en Google
        # Drive. Sin este dato no es posible crear la subcarpeta en el lugar
        # correcto, por lo que se detiene el proceso con un mensaje claro.
        if not carpeta_contratista or not carpeta_contratista.folder_id2:
            raise UserError(_(
                'El contrato no tiene una carpeta del contratista identificada en Drive.'
            ))

        # Reutiliza la carpeta relacionada y repara espejos antiguos que hayan
        # quedado visibles al nivel de CONTRATOS PS. No mueve datos en Drive.
        if self.cierre_drive_folder_id:
            return self._asegurar_ubicacion_local_carpeta_cierre(
                self.cierre_drive_folder_id,
                carpeta_contratista,
            )

        # La carpeta enlazada al contrato debe ser una hija de CONTRATOS PS,
        # nunca la propia carpeta general de la etapa. Esta validación evita
        # crear "Cierre contractual" al mismo nivel de los contratistas.
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
                'La contratación está enlazada a la carpeta general '
                '3. CONTRATOS PS y no a la carpeta individual del contratista. '
                'Corrige primero la carpeta asociada al contrato.'
            ))

        # Obtiene la conexión autenticada que ya utiliza el módulo para operar
        # sobre Google Drive.
        drive = GoogleDriveAuth.get_instance().get_drive()

        # Guarda el ID real de Google Drive de la carpeta del contratista. Este
        # será el padre de la nueva carpeta de cierre contractual.
        parent_google_id = carpeta_contratista.folder_id2

        # Verifica en Google Drive que el destino exista, sea una carpeta y no
        # corresponda a la carpeta general de CONTRATOS PS.
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
                'No se puede crear el cierre directamente en 3. CONTRATOS PS. '
                'Debe existir una carpeta individual para el contratista.'
            ))

        # Define el nombre único y estándar que tendrá la subcarpeta.
        nombre_carpeta = 'Cierre contractual'

        # Primero busca localmente en Odoo una subcarpeta registrada con el
        # mismo padre y nombre.
        carpeta_odoo = self.env['drive.file'].search([
            ('parent_folder_id', '=', carpeta_contratista.id),
            ('folder_name', '=', nombre_carpeta),
        ], limit=1)
        if carpeta_odoo and carpeta_odoo.folder_id2:
            carpeta_odoo = self._asegurar_ubicacion_local_carpeta_cierre(
                carpeta_odoo,
                carpeta_contratista,
            )
            self.cierre_drive_folder_id = carpeta_odoo
            return carpeta_odoo

        # Busca una carpeta activa con ese nombre dentro del contratista.
        query = (
            "'%s' in parents and title='%s' and "
            "mimeType='application/vnd.google-apps.folder' and trashed=false"
        ) % (parent_google_id, nombre_carpeta)
        carpetas = drive.ListFile({
            'q': query,
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True,
        }).GetList()

        carpeta_google = carpetas[0] if carpetas else False
        carpeta_creada = False
        if not carpeta_google:
            carpeta_google = drive.CreateFile({
                'title': nombre_carpeta,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': parent_google_id}],
            })
            carpeta_google.Upload(param={'supportsAllDrives': True})
            carpeta_creada = True

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
                carpeta_odoo = self._asegurar_ubicacion_local_carpeta_cierre(
                    carpeta_odoo,
                    carpeta_contratista,
                )
            self.cierre_drive_folder_id = carpeta_odoo
            return carpeta_odoo
        except Exception:
            # Evita dejar una carpeta huérfana si falla el registro en Odoo.
            if carpeta_creada:
                try:
                    carpeta_google.Trash()
                except Exception:
                    _logger.exception(
                        'No fue posible retirar la carpeta de cierre creada en Drive.'
                    )
            raise

    def unlink(self):
        """Elimina la carpeta únicamente cuando ningún otro cierre la usa."""
        carpetas_cierre = self.mapped('cierre_drive_folder_id').filtered(
            lambda carpeta: not self.search_count([
                ('id', 'not in', self.ids),
                ('cierre_drive_folder_id', '=', carpeta.id),
            ])
        )
        resultado = super().unlink()
        if carpetas_cierre:
            carpetas_cierre.unlink()
        return resultado

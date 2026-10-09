# -*- coding: utf-8 -*-
import logging

from odoo import models
from pydrive2.files import GoogleDriveFile

from .drive import GoogleDriveAuth

_logger = logging.getLogger(__name__)


_original_insert_permission = GoogleDriveFile.InsertPermission


def _normalizar_email(value):
    return str(value or '').strip().lower()


def _permiso_da_acceso(permission, email, domain):
    if not permission or permission.get('deleted'):
        return False

    permission_type = (permission.get('type') or '').strip().lower()
    permission_email = _normalizar_email(
        permission.get('emailAddress') or permission.get('value')
    )

    if permission_type == 'anyone':
        return True
    if permission_type == 'user' and permission_email == email:
        return True
    if (
        permission_type == 'domain'
        and domain
        and _normalizar_email(permission.get('domain') or permission.get('value')) == domain
    ):
        return True
    return False


def _tiene_acceso_en_archivo_o_padres(service, file_id, email, max_depth=12):
    """Verifica permisos del archivo y también de sus carpetas padre."""
    email = _normalizar_email(email)
    if not email or not file_id:
        return False

    domain = email.rsplit('@', 1)[-1] if '@' in email else ''
    pending = [(str(file_id), 0)]
    visited = set()

    while pending:
        current_id, depth = pending.pop(0)
        if not current_id or current_id in visited or depth > max_depth:
            continue
        visited.add(current_id)

        try:
            response = service.permissions().list(fileId=current_id).execute()
            permissions = response.get('items') or response.get('permissions') or []
        except Exception as error:
            _logger.warning(
                'No fue posible leer permisos de Drive para %s: %s',
                current_id,
                error,
            )
            permissions = []

        if any(_permiso_da_acceso(p, email, domain) for p in permissions):
            _logger.info(
                'Acceso Drive ya existente para %s en %s%s.',
                email,
                current_id,
                ' (heredado por carpeta)' if current_id != str(file_id) else '',
            )
            return True

        if depth < max_depth:
            try:
                metadata = service.files().get(fileId=current_id).execute()
                for parent in metadata.get('parents') or []:
                    parent_id = parent.get('id') if isinstance(parent, dict) else parent
                    if parent_id and str(parent_id) not in visited:
                        pending.append((str(parent_id), depth + 1))
            except Exception as error:
                _logger.warning(
                    'No fue posible leer padres de Drive para %s: %s',
                    current_id,
                    error,
                )

    return False


def _sgo_insert_permission_sin_duplicados(self, new_permission):
    """No repite un permiso si ya existe directo o heredado por carpeta."""
    try:
        permission_type = (new_permission or {}).get('type')
        email = _normalizar_email(
            (new_permission or {}).get('value')
            or (new_permission or {}).get('emailAddress')
        )
        file_id = self.get('id')

        if permission_type == 'user' and email and file_id:
            service = self.auth.service
            if _tiene_acceso_en_archivo_o_padres(service, file_id, email):
                _logger.info(
                    'InsertPermission omitido para %s en %s: ya tiene acceso.',
                    email,
                    file_id,
                )
                return None
    except Exception as error:
        _logger.warning(
            'No fue posible validar permisos existentes antes de compartir; '
            'se usará el flujo normal: %s',
            error,
        )

    return _original_insert_permission(self, new_permission)


if not getattr(GoogleDriveFile.InsertPermission, '_sgo_permission_guard', False):
    _sgo_insert_permission_sin_duplicados._sgo_permission_guard = True
    GoogleDriveFile.InsertPermission = _sgo_insert_permission_sin_duplicados


class DriveNoReshareOnView(models.Model):
    _inherit = 'drive'

    def _asegurar_lector_drive_usuario_actual(self):
        """Repara permisos solamente cuando un flujo lo solicita expresamente.

        Si el usuario ya tiene acceso directo o heredado por una carpeta, no
        vuelve a compartir. Si realmente no tiene acceso, agrega lector una sola vez.
        """
        usuario = self.env.user
        if usuario.share:
            return True

        correo = _normalizar_email(usuario.email or usuario.partner_id.email)
        if not correo:
            return True

        try:
            drive = GoogleDriveAuth.get_instance().get_drive()
            service = drive.auth.service
        except Exception:
            _logger.exception(
                'No fue posible autenticar Drive al verificar acceso de %s.',
                correo,
            )
            return True

        for record in self:
            google_id = record.file_id or record.drive_file_id
            if not google_id:
                continue

            try:
                if _tiene_acceso_en_archivo_o_padres(service, google_id, correo):
                    continue

                archivo = drive.CreateFile({'id': google_id})
                archivo.InsertPermission({
                    'type': 'user',
                    'value': correo,
                    'role': 'reader',
                })
                _logger.info(
                    'Permiso lector Drive creado una sola vez para %s en %s.',
                    correo,
                    google_id,
                )
            except Exception:
                _logger.exception(
                    'No fue posible asegurar acceso lector de %s al documento %s.',
                    correo,
                    google_id,
                )

        return True

    def asegurar_visor_drive(self):
        """Prepara URLs sin consultar ni modificar permisos al abrir el visor."""
        for record in self:
            google_id = record.file_id or record.drive_file_id
            if not google_id:
                continue

            preview_url = record.preview_url or (
                'https://drive.google.com/file/d/%s/preview' % google_id
            )
            edit_url = record.edit_url or (
                'https://drive.google.com/open?id=%s' % google_id
            )
            vals = {}
            if not record.preview_url:
                vals['preview_url'] = preview_url
            if not record.edit_url:
                vals['edit_url'] = edit_url
            if not record.preview_html:
                vals['preview_html'] = (
                    '<iframe src="%s" width="100%%" height="600px" '
                    'frameborder="0" allowfullscreen="allowfullscreen"></iframe>'
                    % preview_url
                )
            if vals:
                record.sudo().with_context(
                    skip_drive_sync=True,
                    skip_integrity=True,
                ).write(vals)
        return True

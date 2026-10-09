import json
import html
import re as _re
import threading
import uuid as _uuid_mod
from odoo import api, fields, models, _, registry
from odoo.api import Environment as OdooEnvironment
from odoo.exceptions import AccessError, UserError
from .drive import GoogleDriveAuth  # IMPORTANTE: Importamos la conexión a Drive
import logging

_logger = logging.getLogger(__name__)

# Mapeo de nombres de Google Drive → nombre numerado que se muestra en el browser
_FOLDER_RENAME_MAP = {
    "CARPETA PRINCIPAL": "1. CARPETA PRINCIPAL",
    "COMUNICACIONES":    "2. COMUNICACIONES",
    "CONTRATOS PS":      "3. CONTRATOS PS",
    "INFORMES":          "4. INFORMES",
}
_FOLDER_HIDDEN = {"ACTAS"}
_DRIVE_BROWSER_HIDDEN_FILE_PREFIXES = (
    "hoja de control",
    "hoja_control",
)


def _drv_is_hidden_browser_file(name):
    clean_name = (name or "").strip().lower()
    return any(clean_name.startswith(prefix) for prefix in _DRIVE_BROWSER_HIDDEN_FILE_PREFIXES)


def _drv_unique_name(name):
    """Añade un sufijo UUID corto al nombre preservando la extensión."""
    uid = _uuid_mod.uuid4().hex[:8]
    if '.' in name:
        base, ext = name.rsplit('.', 1)
        return f"{base}_{uid}.{ext}"
    return f"{name}_{uid}"


def _extract_numeric_prefix(name):
    """Extrae el prefijo numérico inicial de un nombre, e.g. '3.1' de '3.1 Nombre'."""
    m = _re.match(r'^(\d+(?:\.\d+)*)', (name or '').strip())
    return m.group(1) if m else ''


def _get_next_prefix_for_folder(drive, parent_folder_id, parent_name=None):
    """Retorna el siguiente prefijo secuencial para un nuevo ítem dentro de parent_folder_id.

    Ejemplo: padre '3.1 Nombre' con 22 hijos → retorna '3.1.23'.
    Si el padre no tiene prefijo numérico, retorna '1', '2', etc.
    """
    if parent_name is None:
        try:
            pf = drive.CreateFile({'id': parent_folder_id})
            pf.FetchMetadata(fields='title')
            parent_name = pf.get('title') or ''
        except Exception:
            parent_name = ''
    parent_prefix = _extract_numeric_prefix(parent_name)
    try:
        items = drive.ListFile(
            {'q': f"'{parent_folder_id}' in parents and trashed=false"}
        ).GetList()
    except Exception:
        items = []
    max_seq = 0
    if parent_prefix:
        pattern = _re.compile(
            r'^' + _re.escape(parent_prefix) + r'\.(\d+)(?:\s|_|\.|$)'
        )
    else:
        pattern = _re.compile(r'^(\d+)(?:\s|_|\.|$)')
    for item in items:
        item_name = item.get('title') or item.get('name') or ''
        m = pattern.match(item_name)
        if m:
            try:
                seq = int(m.group(1))
                max_seq = max(max_seq, seq)
            except ValueError:
                pass
    next_seq = max_seq + 1
    return f"{parent_prefix}.{next_seq}" if parent_prefix else str(next_seq)


_PERSONA_SUBFOLDER_NAMES = {
    'persona_natural':   'Persona Natural',
    'persona_juridica':  'Persona Jurídica',
    'contrato_laboral':  'Contrato Laboral',
    'bienes_y_sevicios': 'Bienes y Servicios',
    'anticipos':         'Anticipos y Legalizaciones',
}


def _get_or_create_persona_subfolder(drive, contratos_ps_folder_id, persona_type):
    """Busca o crea la subcarpeta de tipo de persona dentro de CONTRATOS PS.
    Retorna el Google Drive ID de la subcarpeta, o contratos_ps_folder_id como fallback."""
    subfolder_name = _PERSONA_SUBFOLDER_NAMES.get(persona_type)
    if not subfolder_name or not contratos_ps_folder_id:
        return contratos_ps_folder_id
    try:
        items = drive.ListFile({
            'q': (f"'{contratos_ps_folder_id}' in parents and trashed=false"
                  f" and mimeType='application/vnd.google-apps.folder'"
                  f" and title='{subfolder_name}'")
        }).GetList()
        if items:
            return items[0]['id']
    except Exception:
        pass
    try:
        folder = drive.CreateFile({
            'title': subfolder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': contratos_ps_folder_id}],
        })
        folder.Upload()
        return folder['id']
    except Exception as e:
        _logger.error("Error creando subcarpeta '%s' en CONTRATOS PS: %s", subfolder_name, e)
        return contratos_ps_folder_id


def _drv_original_metadata(env, workspace=None, drive_record=None, google_id=None):
    """Devuelve (creador, fecha) desde el registro original, no desde el espejo."""
    source = drive_record
    workspace = workspace.sudo() if workspace else workspace

    if not source and workspace:
        candidates = [
            workspace.drive_file_id,
            workspace.otrosi_drive_id,
            workspace.drive_stage_id,
            workspace.cuenta_de_cobros_id,
            workspace.informes_workspace_id.drive_file_id
            if workspace.informes_workspace_id else False,
            workspace.comunicaciones_workspace_id.drive_file_id
            if workspace.comunicaciones_workspace_id else False,
        ]
        source = next((record for record in candidates if record), False)

    google_id = google_id or (workspace.folder_google_id if workspace else False)
    if not source and google_id:
        searches = (
            ('drive_folder', ['|', ('google_drive_id', '=', google_id),
                              ('folder_guardar_id', '=', google_id)]),
            ('drive.file', [('folder_id2', '=', google_id)]),
            ('drive_stage', ['|', ('folder_id', '=', google_id),
                             ('google_drive_id', '=', google_id)]),
            ('otrosi_drive', [('folder_id', '=', google_id)]),
            ('cuenta_de_cobros', [('folder_id', '=', google_id)]),
        )
        for model_name, domain in searches:
            if model_name not in env.registry.models:
                continue
            source = env[model_name].sudo().search(domain, limit=1)
            if source:
                break

    if not source:
        return False, False

    creator = False
    for field_name in ('real_creator_id', 'user_id', 'creator_user_id', 'create_uid'):
        if field_name in source._fields:
            creator = source[field_name]
            if creator:
                break

    creation_date = False
    for field_name in ('fch_creacion', 'fecha', 'create_date'):
        if field_name in source._fields:
            creation_date = source[field_name]
            if creation_date:
                break
    if creation_date:
        creation_date = fields.Datetime.to_datetime(creation_date)
    return creator, creation_date


def _drv_renumber_all_items(drive, folder_id, parent_name, fill_gaps=False):
    """Asigna el prefijo del padre a los ítems de folder_id.

    fill_gaps=False (default): sólo toca ítems sin prefijo correcto; preserva números existentes.
    fill_gaps=True: reasigna 1, 2, 3... en orden preservando la secuencia relativa (rellena huecos).

    Retorna dict {google_id: new_title} con los ítems renombrados.
    """
    parent_prefix = _extract_numeric_prefix(parent_name)
    if not parent_prefix:
        return {}  # sin prefijo padre no hay nada que numerar

    try:
        items = drive.ListFile(
            {'q': f"'{folder_id}' in parents and trashed=false"}
        ).GetList()
    except Exception as _le:
        _logger.warning("_drv_renumber_all_items: no se pudo listar '%s': %s", folder_id, _le)
        return {}

    full_pat = _re.compile(r'^' + _re.escape(parent_prefix) + r'\.(\d+)(?:[\s_.]|$)')
    bare_pat = _re.compile(r'^(\d+)([\s_])(.*)')

    def _strip_prefix(title):
        """Retorna (seq_or_None, base_name)."""
        m = full_pat.match(title)
        if m:
            rest = title[m.end():].lstrip()
            return int(m.group(1)), rest
        m2 = bare_pat.match(title)
        if m2:
            rest = m2.group(3)
            return int(m2.group(1)), rest
        return None, title

    if fill_gaps:
        # Reasignación secuencial: ordena por número actual, luego reasigna 1,2,3...
        numbered = []
        unnumbered = []
        for item in items:
            title = item.get('title') or ''
            seq, base = _strip_prefix(title)
            if seq is not None:
                numbered.append((seq, base, item))
            else:
                unnumbered.append((base, item))
        numbered.sort(key=lambda x: x[0])

        renamed = {}
        seq = 1
        for _, base, item in numbered:
            new_title = f"{parent_prefix}.{seq} {base}" if base else f"{parent_prefix}.{seq}"
            old_title = item.get('title') or ''
            if new_title != old_title:
                try:
                    gfile = drive.CreateFile({'id': item['id']})
                    gfile['title'] = new_title
                    gfile.Upload()
                    renamed[item['id']] = new_title
                except Exception as e:
                    _logger.warning("No se pudo renombrar '%s': %s", old_title, e)
            else:
                renamed[item['id']] = new_title  # incluir para actualizar Odoo
            seq += 1
        for base, item in unnumbered:
            new_title = f"{parent_prefix}.{seq} {base}" if base else f"{parent_prefix}.{seq}"
            old_title = item.get('title') or ''
            try:
                gfile = drive.CreateFile({'id': item['id']})
                gfile['title'] = new_title
                gfile.Upload()
                renamed[item['id']] = new_title
            except Exception as e:
                _logger.warning("No se pudo renombrar '%s': %s", old_title, e)
            seq += 1
        return renamed

    # Modo normal: sólo asigna prefijo a los que no lo tienen
    max_seq = 0
    for item in items:
        title = item.get('title') or ''
        m = full_pat.match(title)
        if m:
            try:
                max_seq = max(max_seq, int(m.group(1)))
            except ValueError:
                pass
            continue
        m2 = bare_pat.match(title)
        if m2:
            try:
                max_seq = max(max_seq, int(m2.group(1)))
            except ValueError:
                pass

    renamed = {}
    for item in items:
        title = item.get('title') or ''
        if full_pat.match(title):
            continue  # ya tiene el prefijo correcto

        m2 = bare_pat.match(title)
        if m2:
            bare_num = m2.group(1)
            rest = m2.group(3)
            new_title = f"{parent_prefix}.{bare_num} {rest}"
        else:
            max_seq += 1
            new_title = f"{parent_prefix}.{max_seq} {title}"

        try:
            gfile = drive.CreateFile({'id': item['id']})
            gfile['title'] = new_title
            gfile.Upload()
            renamed[item['id']] = new_title
        except Exception as e:
            _logger.warning("No se pudo renombrar '%s': %s", title, e)
    return renamed


class DriveBrowserEntry(models.Model):
    _name = "drive.browser.entry"
    _description = "Drive Browser - Entry (folder/file)"
    _order = "entry_type desc, name asc"

    browser_id = fields.Many2one(
        "drive.browser", required=True, ondelete="cascade", index=True)
    parent_workspace_id = fields.Many2one(
        "drive.browser.workspace",
        string="Carpeta (contenedor)",
        required=True, ondelete="cascade", index=True)
    entry_type = fields.Selection(
        [("folder", "Folder"), ("file", "File")], required=True, index=True)
    name = fields.Char(required=True, index=True)
    target_workspace_id = fields.Many2one(
        "drive.browser.workspace", string="Ir a carpeta", ondelete="cascade", index=True)

    preview_html = fields.Text("Preview HTML", readonly=True, related="drive_id.preview_html")
    drive_id = fields.Many2one("drive", string="Documento", ondelete="cascade", index=True)
    google_id = fields.Char(index=True)
    
    # CAMPOS AGREGADOS: Necesarios para que el sincronizador guarde la data de Drive
    mime_type = fields.Char()
    icon_mime_type = fields.Char(compute="_compute_icon_mime_type", store=False)
    web_view_link = fields.Char()
    thumbnail_link = fields.Char()

    fch_creacion = fields.Datetime(related='drive_id.fch_creacion', readonly=True)
    user_id = fields.Many2one('res.users', related='drive_id.user_id', readonly=True)
    show_on_dashboard = fields.Boolean(related='drive_id.show_on_dashboard', readonly=True)
    is_consulta_user = fields.Boolean(
        compute='_compute_is_consulta_user', store=False)
    parent_workspace_name = fields.Char(
        related='parent_workspace_id.name', string="En Carpeta", readonly=True)
    color = fields.Integer(default=0)
    select_persona = fields.Selection(
        related='target_workspace_id.drive_file_id.select_persona',
        string="Tipo de Persona", readonly=True, store=False)

    real_creator_id = fields.Many2one(
        'res.users', string='Creador real', compute='_compute_real_creator', store=False)
    real_create_date = fields.Datetime(
        string='Fecha de creación real', compute='_compute_real_create_date', store=False)

    def _compute_is_consulta_user(self):
        is_consulta = self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')
        for rec in self:
            rec.is_consulta_user = is_consulta

    def _compute_icon_mime_type(self):
        for rec in self:
            name = (rec.name or '').lower()
            if rec.entry_type == 'folder':
                rec.icon_mime_type = rec.mime_type or 'application/vnd.google-apps.folder'
            elif name.endswith(('.docx', '.doc')):
                rec.icon_mime_type = (
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
            elif name.endswith(('.xlsx', '.xls', '.csv')):
                rec.icon_mime_type = (
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            elif name.endswith(('.pptx', '.ppt', '.pps', '.ppsx')):
                rec.icon_mime_type = (
                    'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                )
            elif name.endswith('.pdf'):
                rec.icon_mime_type = 'application/pdf'
            else:
                rec.icon_mime_type = rec.mime_type or 'application/octet-stream'

    def _check_consulta_read_only(self):
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            raise AccessError(_("El rol Usuario de consulta solo puede ver documentos."))

    @api.depends('entry_type', 'drive_id', 'target_workspace_id', 'google_id')
    def _compute_real_creator(self):
        for rec in self:
            owner, _creation_date = _drv_original_metadata(
                rec.env,
                workspace=rec.target_workspace_id if rec.entry_type == 'folder' else False,
                drive_record=rec.drive_id if rec.entry_type == 'file' else False,
                google_id=rec.google_id,
            )
            owner = owner or rec.parent_workspace_id.create_uid or rec.create_uid
            rec.real_creator_id = owner

    @api.depends('entry_type', 'drive_id', 'target_workspace_id', 'google_id')
    def _compute_real_create_date(self):
        for rec in self:
            _owner, creation_date = _drv_original_metadata(
                rec.env,
                workspace=rec.target_workspace_id if rec.entry_type == 'folder' else False,
                drive_record=rec.drive_id if rec.entry_type == 'file' else False,
                google_id=rec.google_id,
            )
            rec.real_create_date = creation_date or rec.create_date

    def action_view_details(self):
        self.ensure_one()
        self.browser_id.write({'selected_entry_id': self.id, 'show_details_panel': True})
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_share_entry(self):
        self.ensure_one()
        self._check_consulta_read_only()
        if not self.drive_id:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))
        project = self.browser_id.project_id
        user_ids = project.users_with_access.ids if project else []
        action = self.drive_id.drive_share()
        action['context']['allowed_user_ids'] = user_ids
        return action

    def action_download_pdf_entry(self):
        self.ensure_one()
        if not self.drive_id:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))
        return self.drive_id.download_from_drive()

    def action_download_word_entry(self):
        self.ensure_one()
        if not self.drive_id:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))
        return self.drive_id.download_from_drive_as_word()

    def action_download_original_entry(self):
        self.ensure_one()
        if not self.drive_id:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))
        return self.drive_id.download_from_drive_original()

    def action_delete_entry(self):
        self.ensure_one()
        self._check_consulta_read_only()
        browser = self.browser_id
        if self.entry_type == 'folder' and self.target_workspace_id:
            # Antes de borrar en Soporte, borra los espejos en Informes/Comunicaciones.
            # Si no se limpian primero, esos modelos vuelven a sincronizar la carpeta
            # y aparece otra vez en drive_soporte.py.
            workspace_tree = self.env['drive.browser.workspace'].sudo().browse()
            pending_ws = self.target_workspace_id.sudo()
            seen_ws_ids = set()
            while pending_ws:
                current_ws = pending_ws[0]
                pending_ws -= current_ws
                if current_ws.id in seen_ws_ids:
                    continue
                seen_ws_ids.add(current_ws.id)
                workspace_tree |= current_ws
                pending_ws |= current_ws.child_ids.sudo()

            workspace_drive_file_ids = []
            if workspace_tree and 'drive_file_id' in workspace_tree._fields:
                workspace_drive_file_ids = workspace_tree.mapped('drive_file_id').ids
            _drv_sync_remove_secondary_workspace_mirrors(
                self.env,
                drive_file_ids=workspace_drive_file_ids,
                folder_google_ids=[gid for gid in workspace_tree.mapped('folder_google_id') if gid],
            )

            # Eliminar registros drive de todos los archivos dentro (recursivo)
            def _delete_drive_recs_in_ws(ws):
                file_entries = self.env['drive.browser.entry'].sudo().search([
                    ('parent_workspace_id', '=', ws.id),
                    ('entry_type', '=', 'file'),
                ])
                for fe in file_entries:
                    _drv_unlink_drive_records_for_google_id(
                        self.env,
                        fe.google_id,
                        fe.drive_id,
                    )
                # Limpiar también registros huérfanos por folder_id
                if ws.folder_google_id:
                    orphans = self.env['drive'].sudo().search([
                        ('folder_id', '=', ws.folder_google_id),
                        ('is_folder', '=', False),
                    ])
                    try:
                        orphans.with_context(skip_drive_sync=True, skip_integrity=True).unlink()
                    except Exception as _e:
                        _logger.warning("orphan drive records delete error: %s", _e)
                for child_ws in ws.child_ids:
                    _delete_drive_recs_in_ws(child_ws)

            _delete_drive_recs_in_ws(self.target_workspace_id)

            gid = self.target_workspace_id.folder_google_id
            if gid:
                try:
                    drive = browser._drv_get_drive()
                    drive.CreateFile({'id': gid}).Trash()
                except Exception as e:
                    _logger.warning("No se pudo mover a la papelera %s: %s", gid, e)
            self.target_workspace_id.unlink()
        elif self.entry_type == 'file' and self.google_id:
            try:
                drive = browser._drv_get_drive()
                drive.CreateFile({'id': self.google_id}).Trash()
            except Exception as e:
                _logger.warning("No se pudo mover a la papelera %s: %s", self.google_id, e)
            _drv_unlink_drive_records_for_google_id(
                self.env,
                self.google_id,
                self.drive_id,
            )
        self.unlink()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_download_folder(self):
        self.ensure_one()
        import base64 as b64mod, io, os, uuid, zipfile
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ws = self.target_workspace_id
        if not ws or not ws.folder_google_id:
            raise UserError(_("La carpeta no tiene ID de Google Drive. Sincroniza primero."))

        folder_name = ws.name or self.name or "carpeta"
        gdrive = self.browser_id._drv_get_drive()
        folder_mime = "application/vnd.google-apps.folder"

        def _export_info(mime):
            if "spreadsheet" in mime:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
            if "document" in mime:
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"
            if "presentation" in mime:
                return "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"
            return None, ""

        # Fase 1: recolectar metadatos del árbol completo (sin descargar)
        pending = []
        dir_entries = []  # rutas de carpetas para preservar estructura aunque estén vacías

        def _collect_meta(folder_id, zip_path):
            q = f"'{folder_id}' in parents and trashed=false"
            try:
                items = gdrive.ListFile({"q": q}).GetList()
            except Exception as e:
                _logger.warning("Error listando carpeta %s: %s", folder_id, e)
                return
            if not items:
                dir_entries.append(zip_path + "/")
                return
            for item in items:
                item_id = item.get("id")
                item_name = (item.get("title") or item.get("name") or "archivo").strip()
                item_mime = item.get("mimeType", "")
                if not item_id:
                    continue
                if item_mime == folder_mime:
                    dir_entries.append(f"{zip_path}/{item_name}/")
                    _collect_meta(item_id, f"{zip_path}/{item_name}")
                else:
                    export_mime, ext = _export_info(item_mime)
                    safe_name = item_name if (not ext or item_name.endswith(ext)) else f"{item_name}{ext}"
                    pending.append((item_id, export_mime, f"{zip_path}/{safe_name}"))

        _collect_meta(ws.folder_google_id, folder_name)

        # Fase 2: descargas en paralelo
        def _download_one(task):
            item_id, export_mime, zip_entry = task
            tmp = os.path.join("/tmp", f"{uuid.uuid4().hex}")
            try:
                gfile = gdrive.CreateFile({"id": item_id})
                if export_mime:
                    gfile.GetContentFile(tmp, mimetype=export_mime)
                else:
                    gfile.GetContentFile(tmp)
                with open(tmp, "rb") as f:
                    return zip_entry, f.read()
            except Exception as e:
                _logger.warning("Error descargando '%s': %s", zip_entry, e)
                return None, None
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)

        downloaded = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_download_one, t): t for t in pending}
            for fut in as_completed(futures):
                zip_entry, data = fut.result()
                if zip_entry and data is not None:
                    downloaded.append((zip_entry, data))

        # Fase 3: construir ZIP (primero carpetas vacías, luego archivos)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for dir_path in dir_entries:
                zf.writestr(dir_path, "")
            for zip_entry, data in downloaded:
                zf.writestr(zip_entry, data)

        zip_data = b64mod.b64encode(buf.getvalue()).decode()
        att = self.env["ir.attachment"]._drive_create_temp_download({
            "name": f"{folder_name}.zip",
            "type": "binary",
            "datas": zip_data,
            "mimetype": "application/zip",
            "res_model": self._name,
            "res_id": self.id,
        })
        if ws.drive_file_id:
            self.env["drive.file.trace"].sudo().create({
                "drive_file_id": ws.drive_file_id.id,
                "action_type": "download",
                "user_id": self.env.user.id,
                "notes": _("Carpeta descargada: %s.zip") % folder_name,
            })
        return {"type": "ir.actions.act_url", "url": f"/web/content/{att.id}?download=true", "target": "self"}

    def action_open_folder(self):
        self.ensure_one()
        if not self.exists():
            return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        if self.entry_type != "folder" or not self.target_workspace_id:
            return False
        browser = self.browser_id
        browser.workspace_tree_ids.write({"is_selected": False})
        self.target_workspace_id.write({"is_selected": True})
        browser.write({
            "selected_workspace_id": self.target_workspace_id.id,
            "show_details_panel": True,
            "sel_entry_type": 'folder',
            "search_query": False,
        })
        browser._refresh_entries_from_odoo_models()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_open_file(self):
        self.ensure_one()
        if self.entry_type != "file":
            return False
        if not self.drive_id:
            # Fallback: open directly in Google Drive if we have the link
            if self.web_view_link:
                return {
                    'type': 'ir.actions.act_url',
                    'url': self.web_view_link,
                    'target': 'new',
                }
            return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        view_id = self.env.ref('Matriz.view_drive_form').id
        return {
            'name': self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'res_id': self.drive_id.id,
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {'create': False, 'edit': True},
        }


class DriveBrowserWorkspace(models.Model):
    _name = "drive.browser.workspace"
    _description = "Drive Browser - Workspace"
    _parent_name = "parent_id"
    _parent_store = True
    _order = "parent_path, name"

    name = fields.Char(required=True)
    browser_id = fields.Many2one(
        "drive.browser", required=True, ondelete="cascade", index=True)
    project_id = fields.Many2one(
        related="browser_id.project_id", store=True, readonly=True, index=True)
    parent_id = fields.Many2one(
        "drive.browser.workspace", ondelete="cascade", index=True)
    child_ids = fields.One2many("drive.browser.workspace", "parent_id")
    parent_path = fields.Char(index=True)
    is_selected = fields.Boolean(default=False)
    depth = fields.Integer(compute='_compute_depth', store=False)
    drive_file_id = fields.Many2one("drive.file", ondelete="set null", index=True)
    drive_stage_id = fields.Many2one("drive_stage", ondelete="set null", index=True)
    otrosi_drive_id = fields.Many2one("otrosi_drive", ondelete="set null", index=True)
    cuenta_de_cobros_id = fields.Many2one("cuenta_de_cobros", ondelete="set null", index=True)
    informes_workspace_id = fields.Many2one("informes.workspace", ondelete="set null", index=True)
    comunicaciones_workspace_id = fields.Many2one("comunicaciones.workspace", ondelete="set null", index=True)
    folder_google_id = fields.Char(string="Google Folder ID", index=True)
    parent_folder_google_id = fields.Char(string="Google Parent Folder ID", index=True)
    color = fields.Integer(default=0)
    select_persona = fields.Selection(
        related='drive_file_id.select_persona',
        string="Tipo de Persona", readonly=True, store=False)

    @api.depends('parent_path')
    def _compute_depth(self):
        for rec in self:
            if rec.parent_path:
                rec.depth = len([p for p in rec.parent_path.strip('/').split('/') if p]) - 1
            else:
                rec.depth = 0

    def action_select_workspace(self):
        self.ensure_one()
        browser = self.browser_id
        browser.workspace_tree_ids.write({"is_selected": False})
        self.write({"is_selected": True})
        browser.write({
            "selected_workspace_id": self.id,
            "selected_entry_id": False,
            "show_details_panel": True,
            "sel_entry_type": 'folder',
            "workspace_search": False,
            "search_query": False,
        })
        if not self.parent_id and self.folder_google_id:
            try:
                browser._fix_root_folder_order_in_drive()
            except Exception as _root_order_err:
                _logger.warning("No se pudo ajustar orden raíz al entrar: %s", _root_order_err)
        browser._ensure_otrosi_workspace_nodes(self)
        browser._drv_sync_special_tree_from_browser_ws(self)
        browser._refresh_entries_from_odoo_models()
        # El cliente ya refresca el formulario después del botón; evitar soft_reload
        # reduce la pantalla completa de "Cargando..." al navegar entre carpetas.
        return True

    def action_cycle_color(self):
        self.ensure_one()
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            raise AccessError(_("El rol Usuario de consulta solo puede ver documentos."))
        # 0=sin color, 1=rojo, 2=naranja, 3=verde, 4=azul
        self.color = (self.color + 1) % 5
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def write(self, vals):
        old_names = {rec.id: rec.name for rec in self} if 'name' in vals else {}
        result = super(DriveBrowserWorkspace, self).write(vals)
        if 'name' in vals and not self.env.context.get('_drv_skip_activity'):
            Trace = self.env['drive.file.trace'].sudo()
            for rec in self:
                old_name = old_names.get(rec.id)
                if rec.drive_file_id and old_name and old_name != rec.name:
                    Trace.create({
                        'drive_file_id': rec.drive_file_id.id,
                        'action_type': 'update',
                        'user_id': self.env.user.id,
                        'notes': _("Carpeta renombrada: %s → %s") % (old_name, rec.name),
                    })
        return result

    def action_delete_workspace(self):
        self.ensure_one()
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            raise AccessError(_("El rol Usuario de consulta solo puede ver documentos."))
        browser = self.browser_id
        parent_ws = self.parent_id
        if self.folder_google_id:
            try:
                drive = browser._drv_get_drive()
                gfile = drive.CreateFile({'id': self.folder_google_id})
                gfile.Trash()
            except Exception as e:
                _logger.warning("Could not trash Drive folder %s: %s", self.folder_google_id, e)
        if browser.selected_workspace_id == self:
            browser.write({'selected_workspace_id': False, 'show_details_panel': False})
        self.unlink()
        # Si el proyecto está Completado, renumerar los hermanos del nivel padre para cerrar el hueco
        if (parent_ws and parent_ws.folder_google_id
                and browser.project_id.coordinador_g == 'green'):
            try:
                drive = browser._drv_get_drive()
                renamed = _drv_renumber_all_items(
                    drive, parent_ws.folder_google_id, parent_ws.name or '', fill_gaps=True
                )
                if renamed:
                    sibling_recs = self.env['drive.browser.workspace'].search([
                        ('browser_id', '=', browser.id),
                        ('parent_id', '=', parent_ws.id),
                    ])
                    for s in sibling_recs:
                        if s.folder_google_id in renamed:
                            s.write({'name': renamed[s.folder_google_id]})
            except Exception as _re:
                _logger.warning("Renumeración post-borrado falló: %s", _re)
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_show_folder_info(self):
        self.ensure_one()
        browser = self.browser_id
        browser.write({
            "selected_workspace_id": self.id,
            "selected_entry_id": False,
            "show_details_panel": True,
        })
        browser._compute_sel_folder_info()
        return {
            "type": "ir.actions.act_window",
            "name": "Drive",
            "res_model": "drive.browser",
            "view_mode": "form",
            "res_id": browser.id,
            "target": "current",
            "flags": {"clear_breadcrumbs": True},
        }

    def unlink(self):
        # Solo propagar a drive.file si la eliminación no fue iniciada desde ningún modelo fuente
        _from_model = (
            self.env.context.get('_drv_from_df_delete')
            or self.env.context.get('_drv_from_model_delete')
        )
        _drv_sync_remove_secondary_workspace_mirrors(
            self.env,
            drive_file_ids=self.mapped('drive_file_id').ids,
            folder_google_ids=[gid for gid in self.mapped('folder_google_id') if gid],
        )
        if not _from_model:
            for rec in self:
                if rec.drive_file_id:
                    try:
                        rec.drive_file_id.sudo().with_context(_drv_from_ws_delete=True).unlink()
                    except Exception as _e:
                        _logger.warning("ws→df propagation error df=%s: %s", rec.drive_file_id.id, _e)
        return super().unlink()


class DriveBrowser(models.Model):
    _name = "drive.browser"
    _description = "Drive Browser"
    _rec_name = "current_folder_display"

    def web_read(self, specification):
        for rec in self.sudo():
            if rec.sync_state == 'sincronizando':
                continue
            try:
                if not rec.workspace_tree_ids:
                    # Primera vez: construir árbol desde BD
                    rec._sync_from_odoo_models()
                # Siempre refrescar entradas de la carpeta activa (preserva navegación)
                rec._refresh_entries_from_odoo_models()
                rec.write({'sync_state': 'completado'})
            except Exception as e:
                _logger.error("web_read DB sync error rec=%s: %s", rec.id, e)
        return super().web_read(specification)

    def action_create_folder(self):
        self.ensure_one()
        self._check_consulta_read_only()
        if not self.selected_workspace_id:
            raise UserError(_("Selecciona una carpeta en el árbol primero."))
        ws = self.selected_workspace_id
        if not ws.parent_id or ws.name in self._LOCKED_WORKSPACE_NAMES:
            raise UserError(_(
                "Las carpetas dentro de '%s' solo pueden crearse desde el formulario "
                "del contratista o proveedor." % ws.name
            ))
        wizard = self.env['drive.browser.create.folder'].create({
            'browser_id': self.id,
            'workspace_id': self.selected_workspace_id.id,
        })
        return {
            'name': _('Nueva Carpeta'),
            'type': 'ir.actions.act_window',
            'res_model': 'drive.browser.create.folder',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_upload_file(self):
        self.ensure_one()
        self._check_consulta_read_only()
        if not self.selected_workspace_id:
            raise UserError(_("Selecciona una carpeta destino en el árbol primero."))
        return {
            'name': _('Subir a Drive'),
            'type': 'ir.actions.act_window',
            'res_model': 'drive.browser.upload',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_browser_id': self.id,
                'default_workspace_id': self.selected_workspace_id.id,
                'default_upload_mode': 'file',
            },
        }

    def action_upload_folder(self):
        self.ensure_one()
        self._check_consulta_read_only()
        if not self.selected_workspace_id:
            raise UserError(_("Selecciona una carpeta destino en el árbol primero."))
        return {
            'name': _('Subir Carpeta a Drive'),
            'type': 'ir.actions.act_window',
            'res_model': 'drive.browser.upload',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_browser_id': self.id,
                'default_workspace_id': self.selected_workspace_id.id,
                'default_upload_mode': 'folder',
            },
        }

    def _renumber_ws_recursive(self, drive, folder_gid, ws_name, depth=0, fill_gaps=False):
        """Numera todos los ítems de folder_gid y recursa en subcarpetas. Retorna total renombrados."""
        if depth > 8:
            return 0
        renamed = _drv_renumber_all_items(drive, folder_gid, ws_name, fill_gaps=fill_gaps)
        total = len(renamed)
        _logger.info("Numerar recursivo [%s] '%s': %d renombrados", depth, ws_name, total)

        # Actualizar workspaces de Odoo que correspondan a subcarpetas renombradas
        Workspace = self.env['drive.browser.workspace'].sudo()
        ws_rec = Workspace.search(
            [('browser_id', '=', self.id), ('folder_google_id', '=', folder_gid)], limit=1
        )
        folder_mime = "application/vnd.google-apps.folder"

        if ws_rec:
            child_ws_recs = Workspace.search(
                [('browser_id', '=', self.id), ('parent_id', '=', ws_rec.id)]
            )
            child_ws_by_gid = {c.folder_google_id: c for c in child_ws_recs if c.folder_google_id}
        else:
            child_ws_by_gid = {}

        # Obtener subcarpetas de Drive para recursar (usa lista ya obtenida implícitamente por _drv_renumber_all_items)
        try:
            subfolders = drive.ListFile(
                {'q': f"'{folder_gid}' in parents and trashed=false and mimeType='{folder_mime}'"}
            ).GetList()
        except Exception as _e:
            _logger.warning("No se pudieron listar subcarpetas de '%s': %s", folder_gid, _e)
            subfolders = []

        for sf in subfolders:
            sf_gid = sf.get('id')
            if not sf_gid:
                continue
            # Nombre efectivo: si fue renombrado en esta pasada, usar nuevo nombre; si no, el actual de Drive
            sf_name = renamed.get(sf_gid) or sf.get('title') or sf.get('name') or ''
            # Actualizar nombre en Odoo si corresponde
            cws = child_ws_by_gid.get(sf_gid)
            if cws and sf_name and cws.name != sf_name:
                cws.write({'name': sf_name})
            # Sync directo al drive.file vinculado si existe
            if cws and sf_name and cws.drive_file_id and cws.drive_file_id.folder_name != sf_name:
                cws.drive_file_id.write({'folder_name': sf_name})
            # Recursar solo si la subcarpeta ya tiene un prefijo numérico (evita profundidad innecesaria)
            if sf_name and _extract_numeric_prefix(sf_name):
                total += self._renumber_ws_recursive(drive, sf_gid, sf_name, depth + 1, fill_gaps=fill_gaps)

        if renamed:
            self.env['drive.file'].sudo().apply_renumber_map(renamed)
            self.env['drive'].sudo().apply_renumber_map(renamed)

        return total

    _ROOT_FOLDER_ORDER = [
        "CARPETA PRINCIPAL",
        "COMUNICACIONES",
        "CONTRATOS PS",
        "INFORMES",
    ]

    def _fix_root_folder_order_in_drive(self, drive=None):
        """Renombra en Google Drive las 4 carpetas raíz antiguas sin numeración."""
        root_ws = self.env['drive.browser.workspace'].search(
            [('browser_id', '=', self.id), ('parent_id', '=', False)], limit=1
        )
        if not root_ws or not root_ws.folder_google_id:
            raise UserError(_("No se encontró la carpeta raíz del proyecto en Google Drive."))

        drive = drive or self._drv_get_drive()
        try:
            children = drive.ListFile({
                'q': (f"'{root_ws.folder_google_id}' in parents and "
                      "mimeType='application/vnd.google-apps.folder' and trashed=false"),
                'supportsAllDrives': True,
                'includeItemsFromAllDrives': True,
            }).GetList()
        except Exception as e:
            raise UserError(_("No se pudo acceder a Google Drive: %s") % str(e))

        def _strip_num(name):
            return _re.sub(r'^\d+\.\s*', '', name or '').strip().upper()

        children_by_base = {_strip_num(c.get('title', '')): c for c in children}
        WS = self.env['drive.browser.workspace'].sudo()
        renamed = 0
        renamed_map = {}

        for idx, base_name in enumerate(self._ROOT_FOLDER_ORDER, 1):
            child = children_by_base.get(base_name.upper())
            if not child:
                _logger.info("action_fix_root_folder_order: carpeta '%s' no encontrada en Drive", base_name)
                continue
            new_title = f"{idx}. {base_name}"
            if child.get('title') != new_title:
                try:
                    gfile = drive.CreateFile({'id': child['id']})
                    gfile['title'] = new_title
                    gfile.Upload()
                    renamed += 1
                    renamed_map[child['id']] = new_title
                    _logger.info("action_fix_root_folder_order: '%s' → '%s'", child.get('title'), new_title)
                except Exception as e:
                    _logger.warning("No se pudo renombrar '%s': %s", child.get('title'), e)
                    continue
            else:
                renamed_map[child['id']] = new_title
            ws = WS.search([('browser_id', '=', self.id), ('folder_google_id', '=', child['id'])], limit=1)
            if ws and ws.name != new_title:
                ws.write({'name': new_title})
            stage = self.env['drive_stage'].sudo().search([
                ('proyecto_relacionado', '=', self.project_id.id),
                '|',
                ('folder_id', '=', child['id']),
                ('google_drive_id', '=', child['id']),
            ], limit=1)
            if stage:
                vals = {}
                if stage.name != new_title:
                    vals['name'] = new_title
                if stage.folder_name != new_title:
                    vals['folder_name'] = new_title
                if vals:
                    stage.write(vals)

        if renamed_map:
            self.env['drive.file'].sudo().apply_renumber_map(renamed_map)
            self.env['drive'].sudo().apply_renumber_map(renamed_map)
        return renamed

    def action_fix_root_folder_order(self):
        """Fuerza la numeración 1-4 de las carpetas raíz en el orden predefinido."""
        self.ensure_one()
        renamed = self._fix_root_folder_order_in_drive()
        if renamed:
            self._drv_refresh_entries()

        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_renumber_folder(self):
        """Inicia la numeración recursiva completa del proyecto en segundo plano (toast JS)."""
        self.ensure_one()
        root_ws = self.env['drive.browser.workspace'].search(
            [('browser_id', '=', self.id), ('parent_id', '=', False)], limit=1
        )
        if not root_ws or not root_ws.folder_google_id:
            raise UserError(_("No se encontró la carpeta raíz del proyecto en Google Drive."))
        return {
            'type': 'ir.actions.client',
            'tag': 'drive.start_renumber',
            'params': {'browser_id': self.id},
        }

    def action_sync_now(self):
        """Re-sincroniza desde modelos Odoo y recarga la vista."""
        self.ensure_one()
        self._sync_from_odoo_models()
        root_ws = self.workspace_tree_ids.filtered(lambda w: not w.parent_id)
        if root_ws:
            root_ws[0].write({'is_selected': True})
            self.write({'selected_workspace_id': root_ws[0].id})
        self._refresh_entries_from_odoo_models()
        self.write({'sync_state': 'completado'})
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def do_renumber_bg(self):
        """Llamado desde JS: numera recursivamente TODO el proyecto desde la raíz."""
        self.ensure_one()
        root_ws = self.env['drive.browser.workspace'].search(
            [('browser_id', '=', self.id), ('parent_id', '=', False)], limit=1
        )
        if not root_ws or not root_ws.folder_google_id:
            return {'total': 0}
        drive = self._drv_get_drive()
        total = 0
        # La raíz puede no tener prefijo numérico (ej. "PRUEBA ENUMERACION"),
        # así que iteramos sus hijos directos que sí lo tienen.
        for child in root_ws.child_ids.sorted(key=lambda w: w.name or ''):
            if child.folder_google_id and _extract_numeric_prefix(child.name or ''):
                total += self._renumber_ws_recursive(
                    drive, child.folder_google_id, child.name or '', fill_gaps=True
                )
        self._drv_sync_workspace()
        self._drv_refresh_entries()
        return {'total': total}

    def action_organizar_contratos_ps(self):
        """Mueve las carpetas de contratistas a subcarpetas de tipo de persona dentro de CONTRATOS PS."""
        self.ensure_one()
        DriveStage = self.env['drive_stage']
        contratos_ps_stage = DriveStage.search([
            ('name', 'in', list(self._LOCKED_WORKSPACE_NAMES)),
            ('proyecto_relacionado', '=', self.project_id.id),
        ], limit=1)
        if not contratos_ps_stage or not contratos_ps_stage.folder_id:
            raise UserError(_("No se encontró la carpeta CONTRATOS PS para este proyecto."))

        drive = self._drv_get_drive()
        contratos_ps_gid = contratos_ps_stage.folder_id

        contractor_files = self.env['drive.file'].search([
            ('drive_id', '=', contratos_ps_stage.id),
            ('select_persona', '!=', False),
            ('folder_id2', '!=', False),
        ])

        moved = 0
        for df in contractor_files:
            type_subfolder_id = _get_or_create_persona_subfolder(
                drive, contratos_ps_gid, df.select_persona)
            if not type_subfolder_id or type_subfolder_id == contratos_ps_gid:
                continue
            try:
                gfile = drive.CreateFile({'id': df.folder_id2})
                gfile.FetchMetadata(fields='parents')
                current_parents = [p['id'] for p in (gfile.get('parents') or [])]
                if type_subfolder_id in current_parents:
                    continue
                remove = ','.join(p for p in current_parents)
                drive.auth.service.files().patch(
                    fileId=df.folder_id2,
                    addParents=type_subfolder_id,
                    removeParents=remove,
                    fields='id,parents',
                ).execute()
                moved += 1
            except Exception as e:
                _logger.error("Error moviendo carpeta '%s' a subcarpeta de tipo: %s", df.folder_name, e)

        self._drv_sync_workspace()
        self._drv_refresh_entries()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Organización completada'),
                'message': _('%d carpetas movidas a sus subcarpetas de tipo.') % moved,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_aplanar_contratos_ps(self):
        """Mueve las carpetas de vuelta al nivel raíz de CONTRATOS PS y borra las subcarpetas de tipo."""
        self.ensure_one()
        DriveStage = self.env['drive_stage']
        contratos_ps_stage = DriveStage.search([
            ('name', 'in', list(self._LOCKED_WORKSPACE_NAMES)),
            ('proyecto_relacionado', '=', self.project_id.id),
        ], limit=1)
        if not contratos_ps_stage or not contratos_ps_stage.folder_id:
            raise UserError(_("No se encontró la carpeta CONTRATOS PS para este proyecto."))

        drive = self._drv_get_drive()
        contratos_ps_gid = contratos_ps_stage.folder_id

        # Buscar subcarpetas de tipo directamente en CONTRATOS PS
        try:
            type_folders = drive.ListFile({
                'q': (f"'{contratos_ps_gid}' in parents and trashed=false"
                      f" and mimeType='application/vnd.google-apps.folder'")
            }).GetList()
        except Exception as e:
            raise UserError(_("Error listando carpetas de Drive: %s") % e)

        type_names = set(_PERSONA_SUBFOLDER_NAMES.values())
        moved = 0
        trashed = 0
        for tf in type_folders:
            if tf.get('title') not in type_names:
                continue
            type_gid = tf['id']
            # Mover todos los hijos de vuelta a CONTRATOS PS
            try:
                children = drive.ListFile({
                    'q': f"'{type_gid}' in parents and trashed=false"
                }).GetList()
                for child in children:
                    try:
                        drive.auth.service.files().patch(
                            fileId=child['id'],
                            addParents=contratos_ps_gid,
                            removeParents=type_gid,
                            fields='id,parents',
                        ).execute()
                        moved += 1
                    except Exception as e:
                        _logger.error("Error moviendo '%s': %s", child.get('title'), e)
                # Enviar la subcarpeta de tipo a la papelera
                gf = drive.CreateFile({'id': type_gid})
                gf.Trash()
                trashed += 1
            except Exception as e:
                _logger.error("Error procesando subcarpeta tipo '%s': %s", tf.get('title'), e)

        self._drv_sync_workspace()
        self._drv_refresh_entries()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Aplanado completado'),
                'message': _('%d carpetas movidas, %d subcarpetas eliminadas.') % (moved, trashed),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_upload_folder_structure(self, folder_data):
        """RPC from JS: inicia la subida en segundo plano y retorna inmediatamente."""
        self.ensure_one()
        self._check_consulta_read_only()
        import base64 as b64mod

        ws = self.env['drive.browser.workspace'].browse(folder_data.get('workspace_id'))
        if not ws.exists() or not ws.folder_google_id:
            raise UserError(_("Carpeta de destino inválida."))

        items_data   = list(folder_data.get('items', []))
        new_mcb_acta_comite_id = folder_data.get('new_mcb_acta_comite_id') or False
        upload_mode_marker = folder_data.get('upload_mode') or ''
        otrosi_soporte_id = False
        if 'new_mcb_otrosi_soporte:' in upload_mode_marker:
            try:
                otrosi_soporte_id = int(upload_mode_marker.rsplit('new_mcb_otrosi_soporte:', 1)[1].split('|', 1)[0])
            except (ValueError, TypeError):
                raise UserError(_("Otrosí de destino inválido."))
            otrosi_soporte = self.env['new_mcb_otrosi'].browse(otrosi_soporte_id).exists()
            if not otrosi_soporte:
                raise UserError(_("No se encontró el Otrosí de destino."))
            otrosi_soporte._validar_destino_documentos_soporte(ws)
        if not new_mcb_acta_comite_id and 'new_mcb_acta_comite:' in upload_mode_marker:
            try:
                new_mcb_acta_comite_id = int(
                    upload_mode_marker.rsplit('new_mcb_acta_comite:', 1)[1].split('|', 1)[0]
                )
            except Exception:
                new_mcb_acta_comite_id = False
        ws_google_id = ws.folder_google_id
        ws_id        = ws.id
        browser_id   = self.id
        db_name      = self.env.cr.dbname
        uid          = self.env.uid

        if new_mcb_acta_comite_id:
            _logger.info(
                "Subida múltiple Acta comité: new_mcb=%s, items=%s",
                new_mcb_acta_comite_id,
                len(items_data),
            )

        self.write({'sync_state': 'sincronizando'})

        def _do_upload():
            import tempfile, os
            from odoo.modules.registry import Registry as _Reg

            try:
                with _Reg(db_name).cursor() as cr:
                    env     = api.Environment(cr, uid, {})
                    browser = env['drive.browser'].browse(browser_id)
                    gdrive  = browser._drv_get_drive()
                    Drive   = env['drive'].sudo()
                    WS      = env['drive.browser.workspace'].sudo()
                    Entry   = env['drive.browser.entry'].sudo()

                    # Cache: path → (google_id, workspace_odoo_id)
                    folder_cache_bg = {}
                    file_seq_cache_bg = {}
                    uploaded_drive_ids = []
                    target_new_mcb = env['new_mcb'].sudo().browse()
                    target_otrosi_soporte = env['new_mcb_otrosi'].browse(otrosi_soporte_id).exists() if otrosi_soporte_id else env['new_mcb_otrosi'].browse()
                    if new_mcb_acta_comite_id:
                        try:
                            target_new_mcb = env['new_mcb'].sudo().browse(int(new_mcb_acta_comite_id))
                        except Exception as link_err:
                            _logger.warning("Acta comité MCB inválido (%s): %s", new_mcb_acta_comite_id, link_err)

                    def _upsert_entry(parent_ws_id, entry_type, name, google_id, **extra_vals):
                        if not parent_ws_id or not google_id:
                            return False
                        vals = {
                            'browser_id': browser_id,
                            'parent_workspace_id': parent_ws_id,
                            'entry_type': entry_type,
                            'name': name,
                            'google_id': google_id,
                            **extra_vals,
                        }
                        existing = Entry.search([
                            ('browser_id', '=', browser_id),
                            ('parent_workspace_id', '=', parent_ws_id),
                            ('google_id', '=', google_id),
                        ], limit=1)
                        if existing:
                            existing.write(vals)
                            return existing
                        return Entry.create(vals)

                    def _ensure_path(parts, parent_gid, parent_ws_id):
                        """Crea carpetas en Drive Y sus nodos workspace en Odoo."""
                        current_gid   = parent_gid
                        current_ws_id = parent_ws_id
                        for i, part in enumerate(parts):
                            key = '/'.join(parts[:i + 1])
                            if key not in folder_cache_bg:
                                prefix = _get_next_prefix_for_folder(
                                    gdrive,
                                    current_gid,
                                    WS.browse(current_ws_id).name or ''
                                )
                                # La carpeta raíz arrastrada conserva UUID para evitar colisiones,
                                # pero también debe numerarse como "Nueva Carpeta".
                                base_title = _drv_unique_name(part) if i == 0 else part
                                title = f"{prefix} {base_title}" if prefix else base_title
                                f = gdrive.CreateFile({
                                    'title': title,
                                    'mimeType': 'application/vnd.google-apps.folder',
                                    'parents': [{'id': current_gid}],
                                })
                                f.Upload()
                                new_gid = f['id']
                                _logger.info("Carpeta Drive: %s → %s", title, new_gid)

                                # Crear nodo workspace en Odoo con savepoint para no
                                # romper el transaction si WS.create falla
                                new_ws_id = current_ws_id  # fallback al padre
                                try:
                                    with cr.savepoint():
                                        new_ws = WS.create({
                                            'browser_id': browser_id,
                                            'name': title,
                                            'folder_google_id': new_gid,
                                            'parent_folder_google_id': current_gid,
                                            'parent_id': current_ws_id,
                                        })
                                        new_ws_id = new_ws.id
                                        _logger.info(
                                            "Workspace creado: id=%s name=%s parent_id=%s",
                                            new_ws_id, title, current_ws_id
                                        )
                                except Exception as ws_err:
                                    _logger.warning(
                                        "WS.create falló '%s': %s (transaction intacto)",
                                        title, ws_err
                                    )
                                if new_ws_id != current_ws_id:
                                    current_ws = WS.browse(current_ws_id)
                                    mirror_df, mirror_ref = browser._drv_create_special_folder_mirror(
                                        current_ws, title, new_gid, current_gid
                                    )
                                    if mirror_df:
                                        mirror_field, mirror_ws = mirror_ref
                                        WS.browse(new_ws_id).write({
                                            'drive_file_id': mirror_df.id,
                                            mirror_field: mirror_ws.id,
                                        })
                                        WS.browse(new_ws_id).invalidate_recordset()
                                if new_ws_id != current_ws_id:
                                    _upsert_entry(
                                        current_ws_id,
                                        'folder',
                                        title,
                                        new_gid,
                                        target_workspace_id=new_ws_id,
                                        mime_type='application/vnd.google-apps.folder',
                                    )
                                folder_cache_bg[key] = (new_gid, new_ws_id)
                            current_gid, current_ws_id = folder_cache_bg[key]
                        return current_gid, current_ws_id

                    total_items = len(items_data)
                    for idx, item in enumerate(items_data, 1):
                        parts = item['path'].split('/')

                        if item.get('empty_folder'):
                            _ensure_path(parts, ws_google_id, ws_id)
                            try:
                                with _Reg(db_name).cursor() as cr_prog:
                                    cr_prog.execute(
                                        "UPDATE drive_browser SET upload_progress = %s WHERE id = %s",
                                        (f"{idx}/{total_items}|{parts[-1][:45] if parts else 'Carpeta vacía'}", browser_id)
                                    )
                                    cr_prog.commit()
                            except Exception as _pe:
                                _logger.warning("Progress update error idx=%s: %s", idx, _pe)
                            continue

                        if len(parts) > 1:
                            parent_gid, parent_ws_id = _ensure_path(parts[:-1], ws_google_id, ws_id)
                        else:
                            parent_gid, parent_ws_id = ws_google_id, ws_id
                        parent_ws_name = WS.browse(parent_ws_id).name or ''
                        parent_prefix = _extract_numeric_prefix(parent_ws_name)
                        original_file_name = parts[-1]
                        unique_file_name = _drv_unique_name(original_file_name)
                        if parent_prefix:
                            if parent_gid not in file_seq_cache_bg:
                                next_prefix = _get_next_prefix_for_folder(
                                    gdrive, parent_gid, parent_ws_name
                                )
                                next_seq = 1
                                if next_prefix and next_prefix.startswith(f"{parent_prefix}."):
                                    try:
                                        next_seq = int(next_prefix.rsplit('.', 1)[1])
                                    except Exception:
                                        next_seq = 1
                                file_seq_cache_bg[parent_gid] = next_seq
                            seq = file_seq_cache_bg[parent_gid]
                            file_seq_cache_bg[parent_gid] = seq + 1
                            file_name = f"{parent_prefix}.{seq} {unique_file_name}"
                        else:
                            file_name = unique_file_name

                        content = b64mod.b64decode(item['data'])
                        fd, tmp = tempfile.mkstemp()
                        try:
                            with open(tmp, 'wb') as fh:
                                fh.write(content)
                            os.close(fd)
                            gf = gdrive.CreateFile({'title': file_name, 'parents': [{'id': parent_gid}]})
                            gf.SetContentFile(tmp)
                            gf.Upload()
                            gid = gf['id']
                            _logger.info("Archivo subido: %s → %s", file_name, gid)

                            pu = f"https://drive.google.com/file/d/{gid}/preview"
                            eu = f"https://drive.google.com/file/d/{gid}/view"
                            ph = (f'<iframe src="{pu}" width="100%" height="600px"'
                                  f' frameborder="0" allowfullscreen loading="lazy"></iframe>')
                            eh = f'<iframe src="{eu}" width="100%" height="600px" frameborder="0"></iframe>'
                            try:
                                current_stage_vals = browser._drv_stage_vals_for_workspace(
                                    WS.browse(parent_ws_id)
                                )
                                dr = Drive.with_context(from_process_button=True).create({
                                    'file_name': file_name,
                                    'folder_id': parent_gid,
                                    'is_folder': False,
                                    **current_stage_vals,
                                })
                                dr.with_context(skip_drive_sync=True, skip_integrity=True).write({
                                    'file_id': gid, 'drive_file_id': gid,
                                    'preview_url': pu, 'edit_url': eu,
                                    'preview_html': ph, 'edit_html': eh,
                                })
                                uploaded_drive_ids.append(dr.id)
                                if target_otrosi_soporte:
                                    target_otrosi_soporte.write({'documentos_soporte_ids': [(4, dr.id)]})
                                if target_new_mcb and target_new_mcb.exists():
                                    target_new_mcb.write({'acta_comite_ids': [(4, dr.id)]})
                                    _logger.info(
                                        "Acta comité relacionada con new_mcb %s -> drive %s",
                                        target_new_mcb.id,
                                        dr.id,
                                    )
                                _upsert_entry(
                                    parent_ws_id,
                                    'file',
                                    file_name,
                                    gid,
                                    mime_type=browser._mime_from_name(file_name),
                                    web_view_link=eu,
                                    drive_id=dr.id,
                                )
                            except Exception as orm_err:
                                _logger.warning("drive record error '%s': %s", file_name, orm_err)

                        except Exception as upload_err:
                            _logger.error("Error subiendo '%s': %s", file_name, upload_err)
                        finally:
                            if os.path.exists(tmp):
                                os.remove(tmp)

                        # Actualizar progreso en cada archivo con SQL directo (evita locks del ORM)
                        short_name = (parts[-1] if parts else file_name)[:45]
                        try:
                            with _Reg(db_name).cursor() as cr_prog:
                                cr_prog.execute(
                                    "UPDATE drive_browser SET upload_progress = %s WHERE id = %s",
                                    (f"{idx}/{total_items}|{short_name}", browser_id)
                                )
                                cr_prog.commit()
                        except Exception as _pe:
                            _logger.warning("Progress update error idx=%s: %s", idx, _pe)

                    _logger.info("Subida completada: %d archivos", total_items)
                    if target_otrosi_soporte and uploaded_drive_ids:
                        target_otrosi_soporte.message_post(
                            body="Documentos de soporte cargados en 1. CARPETA PRINCIPAL: %s"
                            % ", ".join(Drive.browse(uploaded_drive_ids).mapped('file_name'))
                        )
                    # El refresh de entradas es no-crítico: las entradas de la subida ya se
                    # crearon arriba, pero esto reconcilia lo que venga de Drive/Odoo.
                    try:
                        browser._drv_sync_special_tree_from_browser_ws(WS.browse(ws_id))
                        browser._drv_refresh_entries()
                        browser._drv_sync_special_tree_from_browser_ws(WS.browse(ws_id))
                    except Exception as _re:
                        _logger.warning("_drv_refresh_entries post-upload falló (no crítico): %s", _re)
                    if target_new_mcb and target_new_mcb.exists() and uploaded_drive_ids:
                        try:
                            target_new_mcb.write({
                                'acta_comite_ids': [(4, drive_id) for drive_id in uploaded_drive_ids]
                            })
                            names = Drive.browse(uploaded_drive_ids).mapped('file_name')
                            target_new_mcb.message_post(
                                body="Acta comité subida a la 1. CARPETA PRINCIPAL: %s"
                                % ", ".join(names)
                            )
                        except Exception as link_err:
                            _logger.warning("No se pudo relacionar Acta comité con new_mcb: %s", link_err)

                    # Confirmar primero los archivos y sus relaciones. El update del
                    # panel de progreso puede chocar con el polling concurrente y no
                    # debe revertir lo que ya se subió correctamente.
                    cr.commit()
                    try:
                        with _Reg(db_name).cursor() as cr_done:
                            cr_done.execute(
                                "UPDATE drive_browser SET sync_state = 'completado', upload_progress = '' WHERE id = %s",
                                (browser_id,)
                            )
                            cr_done.commit()
                    except Exception as done_err:
                        _logger.warning(
                            "No se pudo marcar subida completada en drive_browser %s: %s",
                            browser_id,
                            done_err,
                        )

            except Exception as exc:
                _logger.error("Error crítico en subida: %s", exc, exc_info=True)
                try:
                    with _Reg(db_name).cursor() as cr2:
                        cr2.execute(
                            "UPDATE drive_browser SET sync_state = 'completado', upload_progress = '' WHERE id = %s",
                            (browser_id,)
                        )
                        cr2.commit()
                except Exception as _e2:
                    _logger.warning("No se pudo resetear sync_state: %s", _e2)

        t = threading.Thread(target=_do_upload, daemon=True)
        self.env.cr.postcommit.add(t.start)
        return {'background': True, 'total': len(items_data)}

    upload_progress = fields.Char(string="Progreso de subida", default="")

    current_folder_display = fields.Char(
        compute="_compute_current_folder_display", store=False, readonly=True)

    project_id = fields.Many2one(
        "contratacion", required=True, ondelete="cascade", index=True)

    workspace_tree_ids = fields.One2many(
        "drive.browser.workspace", "browser_id", string="Workspace")
    workspace_search = fields.Char(string="Buscar carpeta")
    workspace_view_ids = fields.Many2many(
        "drive.browser.workspace",
        string="Workspace (filtrado)",
        compute="_compute_workspace_view",
        store=False,
    )
    workspace_ids = fields.One2many(
        "drive.browser.workspace", "browser_id", string="Workspace Nodes")

    selected_workspace_id = fields.Many2one(
        "drive.browser.workspace", string="Carpeta seleccionada")
    breadcrumb_json = fields.Text(compute="_compute_breadcrumb_json")
    search_query = fields.Char(string="Buscar documentos")
    show_details_panel = fields.Boolean(default=False)
    sync_state = fields.Selection(
        [('sincronizando', 'Sincronizando'), ('completado', 'Completado')],
        string="Estado Sync Drive",
        default='completado',
    )
    last_sync_date = fields.Datetime(string="Última sincronización GDrive")

    is_consulta_user = fields.Boolean(
        compute='_compute_is_consulta_user', store=False)
    entry_ids = fields.One2many("drive.browser.entry", "browser_id", string="Contenido")
    entry_view_ids = fields.Many2many(
        "drive.browser.entry",
        string="Contenido (filtrado)",
        compute="_compute_entry_view",
        store=False,
    )
    folder_view_ids = fields.Many2many(
        "drive.browser.entry",
        relation="drv_browser_folder_view_rel",
        column1="browser_id", column2="entry_id",
        string="Carpetas (filtradas)",
        compute="_compute_folder_file_views",
        store=False,
    )
    file_view_ids = fields.Many2many(
        "drive.browser.entry",
        relation="drv_browser_file_view_rel",
        column1="browser_id", column2="entry_id",
        string="Archivos (filtrados)",
        compute="_compute_folder_file_views",
        store=False,
    )

    selected_entry_id = fields.Many2one("drive.browser.entry", string="Entry seleccionado")
    sel_preview_html = fields.Text(related='selected_entry_id.drive_id.preview_html', readonly=True)
    sel_name = fields.Char(related='selected_entry_id.name', readonly=True)
    sel_folios = fields.Char(
        related='selected_entry_id.drive_id.page_range', string="Folios", readonly=True)
    sel_user_id = fields.Many2one(
        related='selected_entry_id.real_creator_id', readonly=True)
    sel_fecha = fields.Date(related='selected_entry_id.drive_id.fecha', readonly=True)
    sel_create_date = fields.Datetime(
        related='selected_entry_id.real_create_date', readonly=True,
        string="Fecha de creación")
    sel_description = fields.Text(
        related='selected_entry_id.drive_id.descrpcion_info', readonly=True)
    sel_parent_folder_name = fields.Char(
        related='selected_entry_id.parent_workspace_id.name', readonly=True)

    sel_folder_name = fields.Char(compute='_compute_sel_folder_info', readonly=True)
    sel_folder_user_id = fields.Many2one(
        'res.users', compute='_compute_sel_folder_info', readonly=True)
    sel_folder_fecha = fields.Date(compute='_compute_sel_folder_info', readonly=True)
    sel_folder_description = fields.Text(
        compute='_compute_sel_folder_info',
        inverse='_inverse_sel_folder_description',
        store=False)
    sel_folder_parent_name = fields.Char(compute='_compute_sel_folder_info', readonly=True)
    sel_folder_child_count = fields.Integer(compute='_compute_sel_folder_info', readonly=True)
    sel_folder_file_count = fields.Integer(compute='_compute_sel_folder_info', readonly=True)
    sel_folder_google_id = fields.Char(compute='_compute_sel_folder_info', readonly=True)
    sel_entry_type = fields.Selection(related='selected_entry_id.entry_type', readonly=True)
    sel_tab = fields.Selection(
        [('details', 'Detalles'), ('activity', 'Actividad')],
        default='details', string="Pestaña activa")
    sel_activity_html = fields.Html(compute='_compute_sel_activity_html', store=False)

    def _compute_is_consulta_user(self):
        is_consulta = self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')
        for rec in self:
            rec.is_consulta_user = is_consulta

    def _check_consulta_read_only(self):
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            raise AccessError(_("El rol Usuario de consulta solo puede ver documentos."))

    @api.depends('selected_entry_id', 'selected_workspace_id', 'sel_tab')
    def _compute_sel_activity_html(self):
        def _empty_html():
            return '<div class="infcom-empty-activity">Sin actividad registrada.</div>'

        def _item_html(author, date_str, body, icon):
            author = html.escape((author or 'Sistema').replace('Odoo', 'SGO'))
            date_str = html.escape(date_str or '')
            return (
                f'<div class="infcom-activity-item">'
                f'<div style="width:34px;height:34px;border-radius:50%;background:#e8f0fe;'
                f'display:flex;align-items:center;justify-content:center;flex-shrink:0;border:1px solid #d2e3fc;">'
                f'<i class="fa {icon}" style="color:#1a73e8;font-size:14px;"/></div>'
                f'<div style="flex:1;min-width:0;">'
                f'<div class="infcom-activity-head"><strong>{author}</strong><span>{date_str}</span></div>'
                f'<div class="infcom-activity-body">{body}</div>'
                f'</div></div>'
            )

        def _trace_date(value):
            return value.strftime('%d/%m/%Y %I:%M %p') if value else ''

        _file_labels = {
            'create': ('Subió el documento', 'fa-upload'),
            'update': ('Actualizó el documento', 'fa-pencil'),
            'delete': ('Eliminó el documento', 'fa-trash'),
            'share': ('Compartió el documento', 'fa-share-alt'),
            'download': ('Descargó el documento', 'fa-download'),
            'rename': ('Cambió el nombre del documento', 'fa-pencil'),
        }
        _folder_labels = {
            'create': ('Creó la carpeta', 'fa-folder-o'),
            'update': ('Actualizó la carpeta', 'fa-pencil'),
            'delete': ('Eliminó la carpeta', 'fa-trash'),
            'download': ('Descargó la carpeta', 'fa-download'),
        }

        for rec in self:
            lines = []

            # Actividad individual del archivo seleccionado.
            if rec.selected_entry_id and rec.selected_entry_id.drive_id:
                drive_rec = rec.selected_entry_id.drive_id
                traces = self.env['drive.trace'].sudo().search(
                    [('drive_id', '=', drive_rec.id)],
                    order='action_date desc, id desc',
                    limit=25,
                )
                for trace in traces:
                    label, icon = _file_labels.get(trace.action_type, ('Actividad del documento', 'fa-history'))
                    note = trace.notes or label
                    lines.append(_item_html(
                        trace.user_id.name if trace.user_id else 'Sistema',
                        _trace_date(trace.action_date or trace.create_date),
                        note,
                        icon,
                    ))

            # Actividad individual de la carpeta seleccionada.
            elif rec.selected_workspace_id and rec.selected_workspace_id.drive_file_id:
                df = rec.selected_workspace_id.drive_file_id
                traces = self.env['drive.file.trace'].sudo().search(
                    [('drive_file_id', '=', df.id)],
                    order='create_date desc, id desc',
                    limit=25,
                )
                for trace in traces:
                    label, icon = _folder_labels.get(trace.action_type, ('Actividad de la carpeta', 'fa-history'))
                    note = html.escape(trace.notes or label).replace('\n', '<br/>')
                    lines.append(_item_html(
                        trace.user_id.name if trace.user_id else 'Sistema',
                        _trace_date(trace.create_date),
                        note,
                        icon,
                    ))

            rec.sel_activity_html = '<div class="infcom-activity-list">%s</div>' % ''.join(lines) if lines else _empty_html()

    def action_tab_details(self):
        self.ensure_one()
        self.sel_tab = 'details'
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_tab_activity(self):
        self.ensure_one()
        self.sel_tab = 'activity'
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_force_sync(self):
        """Fuerza la re-sincronización completa del árbol desde los modelos Odoo."""
        self.ensure_one()
        if self.sync_state == 'sincronizando':
            return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

        import threading
        from odoo.api import Environment
        from odoo.modules.registry import Registry as registry

        db_name = self.env.cr.dbname
        uid = self.env.uid
        browser_id = self.id

        self.sudo().write({'sync_state': 'sincronizando'})

        def _do_sync():
            try:
                with registry(db_name).cursor() as cr:
                    env = Environment(cr, uid, {})
                    b = env['drive.browser'].sudo().browse(browser_id)
                    b._sync_from_odoo_models()
                    b._refresh_entries_from_odoo_models()
                    b.write({'sync_state': 'completado'})
                    cr.commit()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("Force sync error: %s", exc)
                try:
                    with registry(db_name).cursor() as cr2:
                        Environment(cr2, uid, {})['drive.browser'].sudo().browse(browser_id).write(
                            {'sync_state': 'completado'})
                        cr2.commit()
                except Exception:
                    pass

        thread = threading.Thread(target=_do_sync, daemon=True)
        self.env.cr.postcommit.add(thread.start)
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    _LOCKED_WORKSPACE_NAMES = {'3. CONTRATOS PS', 'CONTRATOS PS'}

    # Stages que no deben aparecer en el browser (sidebar ni tarjetas CARPETAS)
    _HIDDEN_STAGE_NAMES = {'ACTAS'}

    _rel = lambda col: dict(relation="drv_brw_fp_%s_rel" % col, column1="browser_id", column2="entry_id")

    folders_pn_ids = fields.Many2many(
        "drive.browser.entry", **_rel("pn"),
        string="Persona Natural", compute="_compute_persona_groups", store=False)
    folders_pj_ids = fields.Many2many(
        "drive.browser.entry", **_rel("pj"),
        string="Persona Jurídica", compute="_compute_persona_groups", store=False)
    folders_cl_ids = fields.Many2many(
        "drive.browser.entry", **_rel("cl"),
        string="Contrato Laboral", compute="_compute_persona_groups", store=False)
    folders_bs_ids = fields.Many2many(
        "drive.browser.entry", **_rel("bs"),
        string="Bienes y Servicios", compute="_compute_persona_groups", store=False)
    folders_ant_ids = fields.Many2many(
        "drive.browser.entry", **_rel("ant"),
        string="Anticipos y Legalizaciones", compute="_compute_persona_groups", store=False)

    @api.depends("selected_workspace_id", "entry_ids")
    def _compute_persona_groups(self):
        Entry = self.env["drive.browser.entry"]
        _empty = [(5,)]
        for rec in self:
            ws = rec.selected_workspace_id
            if not ws or ws.name not in rec._LOCKED_WORKSPACE_NAMES:
                rec.folders_pn_ids = _empty
                rec.folders_pj_ids = _empty
                rec.folders_cl_ids = _empty
                rec.folders_bs_ids = _empty
                rec.folders_ant_ids = _empty
                continue
            all_entries = Entry.search([
                ("browser_id", "=", rec.id),
                ("parent_workspace_id", "=", ws.id),
                ("entry_type", "=", "folder"),
            ])
            _logger.info(
                ">>> _compute_persona_groups ws=%s entries=%s",
                ws.name,
                [(e.id, e.name, e.target_workspace_id.drive_file_id.select_persona) for e in all_entries],
            )
            def _q(tipo):
                ids = all_entries.filtered(
                    lambda e: e.target_workspace_id.drive_file_id.select_persona == tipo
                ).ids
                return [(6, 0, ids)]
            rec.folders_pn_ids  = _q("persona_natural")
            rec.folders_pj_ids  = _q("persona_juridica")
            rec.folders_cl_ids  = _q("contrato_laboral")
            rec.folders_bs_ids  = _q("bienes_y_sevicios")
            rec.folders_ant_ids = _q("anticipos")

    sel_ws_is_locked = fields.Boolean(
        compute='_compute_sel_ws_is_locked', store=False)

    @api.depends('selected_workspace_id', 'selected_workspace_id.name',
                 'selected_workspace_id.parent_id')
    def _compute_sel_ws_is_locked(self):
        for rec in self:
            ws = rec.selected_workspace_id
            if not ws:
                rec.sel_ws_is_locked = False
            elif not ws.parent_id:
                rec.sel_ws_is_locked = True
            elif ws.name in self._LOCKED_WORKSPACE_NAMES:
                rec.sel_ws_is_locked = True
            else:
                rec.sel_ws_is_locked = False

    sel_ws_is_contratos_ps = fields.Boolean(
        compute='_compute_sel_ws_is_contratos_ps', store=False)

    @api.depends('selected_workspace_id', 'selected_workspace_id.name')
    def _compute_sel_ws_is_contratos_ps(self):
        for rec in self:
            ws = rec.selected_workspace_id
            rec.sel_ws_is_contratos_ps = bool(ws and ws.name in self._LOCKED_WORKSPACE_NAMES)

    sel_ws_no_files = fields.Boolean(
        compute='_compute_sel_ws_no_files', store=False)

    @api.depends('selected_workspace_id', 'selected_workspace_id.name',
                 'selected_workspace_id.parent_id',
                 'selected_workspace_id.drive_file_id',
                 'selected_workspace_id.drive_stage_id',
                 'selected_workspace_id.otrosi_drive_id',
                 'selected_workspace_id.cuenta_de_cobros_id',
                 'selected_workspace_id.informes_workspace_id',
                 'selected_workspace_id.comunicaciones_workspace_id',
                 'selected_workspace_id.folder_google_id')
    def _compute_sel_ws_no_files(self):
        for rec in self:
            ws = rec.selected_workspace_id
            if not ws:
                rec.sel_ws_no_files = False
            elif not ws.parent_id:
                rec.sel_ws_no_files = True
            elif ws.name in self._LOCKED_WORKSPACE_NAMES:
                rec.sel_ws_no_files = True
            elif not (ws.drive_file_id or ws.drive_stage_id or ws.otrosi_drive_id
                      or ws.cuenta_de_cobros_id or ws.informes_workspace_id
                      or ws.comunicaciones_workspace_id or ws.folder_google_id):
                # Nodo contenedor virtual (ej. "Cuentas de cobro") — sin backing de modelo
                rec.sel_ws_no_files = True
            else:
                rec.sel_ws_no_files = False

    @api.depends('selected_entry_id', 'selected_workspace_id')
    def _compute_sel_folder_info(self):
        for rec in self:
            rec.sel_folder_name = ""
            rec.sel_folder_user_id = False
            rec.sel_folder_fecha = False
            rec.sel_folder_description = ""
            rec.sel_folder_parent_name = ""
            rec.sel_folder_child_count = 0
            rec.sel_folder_file_count = 0
            rec.sel_folder_google_id = ""

            ws = None
            if rec.selected_entry_id and rec.selected_entry_id.entry_type == 'folder':
                entry = rec.selected_entry_id
                rec.sel_folder_name = entry.name
                ws = entry.target_workspace_id

                if entry.drive_id:
                    owner, creation_date = _drv_original_metadata(
                        rec.env, drive_record=entry.drive_id, google_id=entry.google_id)
                    rec.sel_folder_user_id = owner
                    rec.sel_folder_fecha = creation_date.date() if creation_date else False
                    rec.sel_folder_description = entry.drive_id.descrpcion_info
                else:
                    owner, creation_date = _drv_original_metadata(
                        rec.env, workspace=ws, google_id=entry.google_id)
                    rec.sel_folder_user_id = owner or (ws.create_uid if ws else rec.create_uid)
                    rec.sel_folder_fecha = (
                        creation_date.date() if creation_date
                        else ws.create_date.date() if ws and ws.create_date else False
                    )
            elif rec.selected_workspace_id:
                ws = rec.selected_workspace_id
                rec.sel_folder_name = ws.name
                owner, creation_date = _drv_original_metadata(
                    rec.env, workspace=ws, google_id=ws.folder_google_id)
                rec.sel_folder_fecha = (
                    creation_date.date() if creation_date
                    else ws.create_date.date() if ws.create_date else False
                )
                rec.sel_folder_user_id = owner or ws.create_uid or rec.create_uid

            if ws:
                rec.sel_folder_parent_name = ws.parent_id.name if ws.parent_id else "Raíz"
                rec.sel_folder_child_count = len(ws.child_ids)
                rec.sel_folder_file_count = self.env['drive.browser.entry'].search_count([
                    ('parent_workspace_id', '=', ws.id),
                    ('entry_type', '=', 'file'),
                ])
                rec.sel_folder_google_id = ws.folder_google_id or ""

    def _inverse_sel_folder_description(self):
        for rec in self:
            desc = rec.sel_folder_description or ''
            if rec.selected_entry_id and rec.selected_entry_id.drive_id:
                rec.selected_entry_id.drive_id.descrpcion_info = desc
            elif rec.selected_workspace_id and rec.selected_workspace_id.drive_file_id:
                rec.selected_workspace_id.drive_file_id.descrpcion_info = desc

    def action_close_details_panel(self):
        self.write({'show_details_panel': False})
        return True

    # _onchange_workspace_search eliminado: el filtrado del sidebar es JS puro (drv_workspace_tree.js)

    @api.onchange("search_query", "selected_workspace_id")
    def _onchange_search_query(self):
        Entry = self.env["drive.browser.entry"]
        for rec in self:
            browser_id = rec._origin.id or rec.id
            if not browser_id or not rec.selected_workspace_id:
                rec.entry_view_ids = [(5, 0, 0)]
                rec.folder_view_ids = [(5, 0, 0)]
                rec.file_view_ids = [(5, 0, 0)]
                continue
            base = [
                ("browser_id", "=", browser_id),
                ("parent_workspace_id", "=", rec.selected_workspace_id.id),
            ]
            if rec.search_query:
                base.append(("name", "ilike", rec.search_query))
            all_entries = Entry.search(base).filtered(
                lambda e: e.entry_type != "file" or not _drv_is_hidden_browser_file(e.name)
            )
            rec.entry_view_ids = [(6, 0, all_entries.ids)]
            rec.folder_view_ids = [(6, 0, all_entries.filtered(lambda e: e.entry_type == "folder").ids)]
            rec.file_view_ids = [(6, 0, all_entries.filtered(lambda e: e.entry_type == "file").ids)]

    @api.depends("selected_workspace_id", "selected_workspace_id.parent_path",
                 "selected_workspace_id.name")
    def _compute_breadcrumb_json(self):
        Workspace = self.env["drive.browser.workspace"]
        for rec in self:
            ws = rec.selected_workspace_id
            if not ws:
                rec.breadcrumb_json = "[]"
                continue
            ids = [int(x) for x in (ws.parent_path or "").split("/") if x.isdigit()]
            nodes = Workspace.browse(ids).exists()
            idpos = {i: p for p, i in enumerate(ids)}
            nodes = nodes.sorted(key=lambda w: idpos.get(w.id, 10**9))
            crumbs = []
            last_name = None
            for w in nodes:
                name = w.name or ''
                if name == last_name:
                    continue
                crumbs.append({"id": w.id, "name": name})
                last_name = name
            rec.breadcrumb_json = json.dumps(crumbs)

    @api.depends("selected_workspace_id", "selected_workspace_id.name",
                 "selected_workspace_id.parent_path")
    def _compute_current_folder_display(self):
        for rec in self:
            rec.current_folder_display = (
                rec._get_workspace_path(rec.selected_workspace_id) or "Sub carpetas")

    def name_get(self):
        result = []
        for rec in self:
            display = rec._get_workspace_path(rec.selected_workspace_id)
            result.append((rec.id, display or "Sub carpetas"))
        return result

    def _get_workspace_path(self, workspace):
        if not workspace:
            return "Sub carpetas"
        ids = [int(x) for x in (workspace.parent_path or "").split("/") if x.isdigit()]
        if not ids:
            return "Sub carpetas"
        # Mantener el orden de parent_path (raíz → hoja)
        id_pos = {wid: pos for pos, wid in enumerate(ids)}
        crumbs = self.env["drive.browser.workspace"].browse(ids).exists()
        crumbs = crumbs.sorted(key=lambda w: id_pos.get(w.id, 999))
        names = []
        for w in crumbs:
            if w.name and (not names or names[-1] != w.name):
                names.append(w.name)
        return " / ".join(names) if names else "Sub carpetas"

    def action_after_upload_refresh(self):
        """Refresca la carpeta activa después de una subida en background."""
        self.ensure_one()
        if self.selected_workspace_id:
            self.selected_workspace_id.invalidate_recordset()
            self._drv_sync_special_tree_from_browser_ws(self.selected_workspace_id)
            self._drv_refresh_entries()
            self._drv_sync_special_tree_from_browser_ws(self.selected_workspace_id)
            self._refresh_entries_from_odoo_models()
        return True

    @api.depends("workspace_tree_ids")
    def _compute_workspace_view(self):
        for rec in self:
            rec.workspace_view_ids = [(6, 0, rec.workspace_tree_ids.ids)]

    @api.depends("selected_workspace_id", "search_query", "entry_ids")
    def _compute_entry_view(self):
        for rec in self:
            if not rec.selected_workspace_id:
                rec.entry_view_ids = [(5, 0, 0)]
                continue
            dom = [
                ("browser_id", "=", rec.id),
                ("parent_workspace_id", "=", rec.selected_workspace_id.id),
            ]
            if rec.search_query:
                dom.append(("name", "ilike", rec.search_query))
            entries = self.env["drive.browser.entry"].search(dom).filtered(
                lambda e: e.entry_type != "file" or not _drv_is_hidden_browser_file(e.name)
            )
            rec.entry_view_ids = [(6, 0, entries.ids)]

    @api.depends("selected_workspace_id", "search_query", "entry_ids")
    def _compute_folder_file_views(self):
        Entry = self.env["drive.browser.entry"]
        for rec in self:
            if not rec.selected_workspace_id:
                rec.folder_view_ids = [(5, 0, 0)]
                rec.file_view_ids = [(5, 0, 0)]
                continue
            base = [
                ("browser_id", "=", rec.id),
                ("parent_workspace_id", "=", rec.selected_workspace_id.id),
            ]
            if rec.search_query:
                base.append(("name", "ilike", rec.search_query))
            rec.folder_view_ids = [(6, 0, Entry.search(base + [("entry_type", "=", "folder")]).ids)]
            files = Entry.search(base + [("entry_type", "=", "file")]).filtered(
                lambda e: not _drv_is_hidden_browser_file(e.name)
            )
            rec.file_view_ids = [(6, 0, files.ids)]

    # ---------------------------------------------------------
    # MÉTODOS DE CONEXIÓN A DRIVE (AÑADIDOS)
    # ---------------------------------------------------------

    def _drv_get_drive(self):
        if hasattr(GoogleDriveAuth, "get_instance"):
            return GoogleDriveAuth.get_instance().get_drive()
        return GoogleDriveAuth().get_drive()

    def _drv_project_name(self):
        self.ensure_one()
        name = (getattr(self.project_id, "new_name", False) or self.project_id.name or "").strip()
        if not name:
            raise UserError(_("El proyecto no tiene nombre para buscar en Drive."))
        return name

    def _drv_find_project_root_folder_id(self, skip_cache=False):
        self.ensure_one()

        if not skip_cache:
            # 1) Si ya existe un workspace raíz con folder_google_id, usarlo directamente
            existing_root = self.env['drive.browser.workspace'].sudo().search([
                ('browser_id', '=', self.id),
                ('parent_id', '=', False),
                ('folder_google_id', '!=', False),
            ], limit=1)
            if existing_root:
                return existing_root.folder_google_id

        # 2) También buscar en drive_folder del proyecto
        df = self.env['drive_folder'].sudo().search([
            ('proyecto_relacionado', '=', self.project_id.id),
            ('google_drive_id', '!=', False),
        ], limit=1)
        if df:
            return df.google_drive_id

        # 3) Fallback: buscar por nombre en GDrive
        project_name = self._drv_project_name()
        root_id = self.env["ir.config_parameter"].sudo().get_param("gd.projects_root_folder_id")

        if root_id:
            q = (
                f"'{root_id}' in parents and "
                "mimeType='application/vnd.google-apps.folder' and "
                f"title='{project_name}' and trashed=false"
            )
        else:
            q = (
                "mimeType='application/vnd.google-apps.folder' and "
                f"title='{project_name}' and trashed=false"
            )

        matches = self._drv_list_all(q)
        if not matches:
            raise UserError(_("No encontré en Drive la carpeta del proyecto: %s") % project_name)

        return matches[0]["id"]

    def _drv_list_all(self, q):
        drive = self._drv_get_drive()
        params = {
            "q": q,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "corpora": "allDrives",
        }
        out = []
        file_list = drive.ListFile(params)

        while True:
            out.extend(file_list.GetList())
            token = getattr(file_list, "metadata", {}).get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
            file_list = drive.ListFile(params)
        return out

    def _drv_list_subfolders_recursive(self, parent_id):
        folder_mime = "application/vnd.google-apps.folder"
        q = f"'{parent_id}' in parents and mimeType='{folder_mime}' and trashed=false"
        children = self._drv_list_all(q)

        out = []
        for c in children:
            node = {
                "id": c.get("id"),
                "name": c.get("title") or c.get("name") or "Sin nombre",
                "parent_id": parent_id,
            }
            if node["id"]:
                out.append(node)
                out.extend(self._drv_list_subfolders_recursive(node["id"]))
        return out

    def _drv_list_children(self, folder_id):
        q = f"'{folder_id}' in parents and trashed=false"
        return self._drv_list_all(q)

    @staticmethod
    def _drv_stage_base_name(name):
        """Nombre comparable de una carpeta principal sin prefijo numérico."""
        return _re.sub(r'^\d+\.\s*', '', (name or '')).strip().upper()

    def _drv_google_folder_exists(self, folder_id, drive=None):
        """Valida que el ID guardado siga existiendo en Google Drive."""
        if not folder_id:
            return False
        drive = drive or self._drv_get_drive()
        try:
            gfile = drive.CreateFile({'id': folder_id})
            gfile.FetchMetadata()
            labels = gfile.get('labels') or {}
            return not (gfile.get('trashed') or labels.get('trashed'))
        except Exception as exc:
            _logger.info(
                ">>> Drive soporte: la carpeta GDrive %s no existe o no está disponible: %s",
                folder_id,
                exc,
            )
            return False

    def _drv_find_or_create_root_child_folder(self, drive, root_folder_id, folder_name):
        """Busca o crea una carpeta hija directa en Google Drive para las carpetas raíz del proyecto."""
        folder_mime = "application/vnd.google-apps.folder"
        target_base = self._drv_stage_base_name(folder_name)
        q = f"'{root_folder_id}' in parents and mimeType='{folder_mime}' and trashed=false"
        children = drive.ListFile({
            "q": q,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }).GetList()

        for child in children:
            title = child.get("title") or child.get("name") or ""
            child_id = child.get("id")
            if not child_id:
                continue
            if title == folder_name or self._drv_stage_base_name(title) == target_base:
                if title != folder_name:
                    try:
                        rename_file = drive.CreateFile({'id': child_id})
                        rename_file['title'] = folder_name
                        rename_file.Upload()
                    except Exception as exc:
                        _logger.warning(
                            ">>> Drive soporte: no se pudo renombrar carpeta principal %s a %s: %s",
                            title,
                            folder_name,
                            exc,
                        )
                return child_id

        new_folder = drive.CreateFile({
            "title": folder_name,
            "mimeType": folder_mime,
            "parents": [{"id": root_folder_id}],
        })
        new_folder.Upload()
        return new_folder.get("id") or new_folder["id"]

    def _drv_ensure_principal_stage_folder(self, stage, root_folder_id, folder_name, drive=None):
        """Asegura que una carpeta principal exista en Google Drive y sincroniza el ID en Odoo."""
        drive = drive or self._drv_get_drive()
        folder_id = stage.folder_id or stage.google_drive_id or False
        if folder_id and self._drv_google_folder_exists(folder_id, drive=drive):
            return folder_id

        folder_id = self._drv_find_or_create_root_child_folder(drive, root_folder_id, folder_name)
        vals = {}
        if "folder_id" in stage._fields and stage.folder_id != folder_id:
            vals["folder_id"] = folder_id
        if "google_drive_id" in stage._fields and stage.google_drive_id != folder_id:
            vals["google_drive_id"] = folder_id
        if "folder_guardar_id" in stage._fields and stage.folder_guardar_id != folder_id:
            vals["folder_guardar_id"] = folder_id
        if "folder_name" in stage._fields and stage.folder_name != folder_name:
            vals["folder_name"] = folder_name
        if vals:
            stage.sudo().write(vals)
        return folder_id

    # ---------------------------------------------------------
    # SINCRONIZACIÓN DESDE MODELOS ODOO (sin llamadas a GDrive)
    # ---------------------------------------------------------

    def _sync_from_odoo_models(self):
        """Construye el árbol de workspaces leyendo drive_stage y drive.file del proyecto."""
        self.ensure_one()
        project = self.project_id
        if not project:
            return

        WS = self.env["drive.browser.workspace"].sudo()

        # Limpiar todo lo anterior para evitar duplicados, luego reconstruir.
        # _drv_from_model_delete=True evita que el unlink de workspaces cascade-borre drive.file → drive → GDrive
        self.sudo().write({'selected_workspace_id': False})
        WS.search([("browser_id", "=", self.id)]).with_context(_drv_from_model_delete=True).unlink()

        root_name = project.new_name or project.name or 'Proyecto'
        drive_folder = self.env['drive_folder'].sudo().search([
            ('proyecto_relacionado', '=', project.id)
        ], limit=1, order='sequence, id')
        root_gd_id = (drive_folder.folder_guardar_id or drive_folder.google_drive_id or False) if drive_folder else False
        root_ws = WS.create({
            "browser_id": self.id,
            "name": root_name,
            "parent_id": False,
            "folder_google_id": root_gd_id,
        })

        stages = self.env["drive_stage"].sudo().search([
            ("proyecto_relacionado", "=", project.id)
        ], order="sequence, id")

        # Pre-detectar si hay informes/comunicaciones para este proyecto
        # (para evitar duplicar con la enumeración por drive.file)
        informes_recs = self.env["informes_drive"].sudo().search([
            ("proyecto_relacionado", "=", project.id)
        ])
        comunicaciones_recs = self.env["comunicaciones_drive"].sudo().search([
            ("proyecto_relacionado", "=", project.id)
        ])
        has_informes = bool(informes_recs)
        has_comunicaciones = bool(comunicaciones_recs)

        # Mapa de stage_ws para poder ubicar informes/comunicaciones después
        stage_ws_by_id = {}

        visible_stages = [
            s for s in stages
            if (s.name or '').upper().strip() not in self._HIDDEN_STAGE_NAMES
        ]

        _logger.info(
            ">>> _sync_from_odoo_models project=%s stages_found=%s",
            project.id,
            [(s.id, s.name) for s in stages],
        )

        # Pre-recoger todos los IDs de stages CONTRATOS PS (puede haber duplicados por nombre).
        # Usar contains para capturar variantes de mayúsculas/espaciado/prefijo.
        _cps_stage_ids = [
            s.id for s in stages
            if 'CONTRATOS PS' in (s.name or '').upper()
        ]
        _logger.info(">>> _sync_from_odoo_models contratos_ps_stage_ids=%s", _cps_stage_ids)

        _cps_workspace_created = False  # evitar duplicar el nodo CONTRATOS PS

        drive_for_principal_stages = None

        for idx, stage in enumerate(visible_stages, start=1):
            stage_name = stage.folder_name or stage.name or 'Carpeta'
            if not _re.match(r'^\d+\.', stage_name.strip()):
                stage_name = f"{idx}. {stage_name}"

            # Si ya creamos el nodo CONTRATOS PS, saltar stages duplicados con el mismo nombre
            is_cps = stage_name in self._LOCKED_WORKSPACE_NAMES
            if is_cps and _cps_workspace_created:
                _logger.info(
                    ">>> _sync_from_odoo_models saltando stage CPS duplicado id=%s name=%s",
                    stage.id, stage.name,
                )
                continue

            stage_gd_id = stage.folder_id or stage.google_drive_id or False
            stage_base_name = self._drv_stage_base_name(stage_name)
            if root_gd_id and stage_base_name in self._ROOT_FOLDER_ORDER:
                try:
                    if drive_for_principal_stages is None:
                        drive_for_principal_stages = self._drv_get_drive()
                    stage_gd_id = self._drv_ensure_principal_stage_folder(
                        stage,
                        root_gd_id,
                        stage_name,
                        drive=drive_for_principal_stages,
                    )
                except Exception as exc:
                    _logger.warning(
                        ">>> Drive soporte: no se pudo asegurar en Google Drive la carpeta principal '%s': %s",
                        stage_name,
                        exc,
                    )

            stage_ws = WS.create({
                "browser_id": self.id,
                "name": stage_name,
                "parent_id": root_ws.id,
                "drive_stage_id": stage.id,
                "folder_google_id": stage_gd_id,
            })
            stage_ws_by_id[stage.id] = stage_ws

            if is_cps:
                _cps_workspace_created = True

            # Si el stage es INFORMES o COMUNICACIONES y existen esos registros,
            # su contenido se agrega abajo (no enumerar drive.file aquí para evitar duplicados)
            sname_upper = (stage.name or '').upper()
            if has_informes and 'INFORMES' in sname_upper:
                continue
            if has_comunicaciones and 'COMUNICACIONES' in sname_upper:
                continue

            # drive.file hijos de este stage → sub-workspaces
            if is_cps and _cps_stage_ids:
                # Solo las carpetas principales de contratistas pertenecen al
                # primer nivel de CONTRATOS PS. Las subcarpetas, como
                # "Cierre contractual", se insertan después bajo su
                # parent_folder_id correspondiente.
                drive_file_domain = [
                    ("drive_id", "in", _cps_stage_ids),
                    ("parent_folder_id", "=", False),
                ]
            else:
                drive_file_domain = [("drive_id", "=", stage.id)]

            drive_files = self.env["drive.file"].sudo().search(
                drive_file_domain, order="sequence, id"
            )

            # Para CONTRATOS PS: también buscar vía Google Drive por folder_id2.
            # Esto captura drive.file con drive_id incorrecto (otro proyecto/stage).
            if is_cps:
                cps_gd_folder_id = stage.folder_id or stage.google_drive_id
                if cps_gd_folder_id:
                    try:
                        _gd_auth = GoogleDriveAuth.get_instance()
                        _gd = _gd_auth.get_drive()
                        _gd_items = _gd.ListFile({
                            'q': (
                                f"'{cps_gd_folder_id}' in parents"
                                " and mimeType='application/vnd.google-apps.folder'"
                                " and trashed=false"
                            )
                        }).GetList()
                        _gd_ids = [it.get('id') for it in _gd_items if it.get('id')]
                        if _gd_ids:
                            extra_df = self.env["drive.file"].sudo().search([
                                ("folder_id2", "in", _gd_ids),
                                ("parent_folder_id", "=", False),
                                ("id", "not in", drive_files.ids),
                            ])
                            if extra_df:
                                _logger.info(
                                    ">>> CPS GDrive: %d drive.file extra encontrados: %s",
                                    len(extra_df),
                                    [(df.id, df.folder_name, df.select_persona) for df in extra_df],
                                )
                                combined = list(dict.fromkeys(drive_files.ids + extra_df.ids))
                                drive_files = self.env["drive.file"].sudo().browse(combined)
                    except Exception as _ge:
                        _logger.warning(">>> CPS GDrive subfolder lookup falló: %s", _ge)
            # Garantizar que select_persona esté poblado (contrato_laboral lo necesita)
            try:
                drive_files.action_actualizar_select_persona()
            except Exception as _e:
                _logger.warning(">>> _sync_from_odoo_models: actu_select_persona error: %s", _e)
            _logger.info(
                ">>> _sync_from_odoo_models stage=%s (cps=%s) drive_files=%s personas=%s",
                stage.name, is_cps, drive_files.ids,
                [(df.id, df.folder_name, df.select_persona) for df in drive_files],
            )
            for df in drive_files:
                df_name = df.folder_name or df.name or 'Subcarpeta'
                df_ws = WS.create({
                    "browser_id": self.id,
                    "name": df_name,
                    "parent_id": stage_ws.id,
                    "drive_file_id": df.id,
                    "folder_google_id": df.folder_id2 or False,
                })
                # Subcarpetas drive.file creadas dentro de la carpeta del
                # contratista. Se muestran como hijas y no como hermanas del
                # contratista dentro de 3. CONTRATOS PS.
                subcarpetas_df = self.env['drive.file'].sudo().search([
                    ('parent_folder_id', '=', df.id),
                ], order='sequence, id')
                for subcarpeta in subcarpetas_df:
                    WS.create({
                        'browser_id': self.id,
                        'name': subcarpeta.folder_name or subcarpeta.name or 'Subcarpeta',
                        'parent_id': df_ws.id,
                        'drive_file_id': subcarpeta.id,
                        'folder_google_id': subcarpeta.folder_id2 or False,
                    })
                # Subcarpeta Informes del contratista (si fue creada)
                informes_folder_rec = self.env['drive'].sudo().search([
                    ('stage2_id', '=', df.id),
                    ('is_folder', '=', True),
                    ('file_name', '=', 'Informes'),
                ], limit=1)
                if informes_folder_rec and informes_folder_rec.folder_id:
                    WS.create({
                        "browser_id": self.id,
                        "name": "Informes",
                        "parent_id": df_ws.id,
                        "folder_google_id": informes_folder_rec.folder_id,
                    })
                # otrosi_drive sub-carpetas dentro del contratista
                otrosi_recs = self.env["otrosi_drive"].sudo().search([
                    ("drive_id", "=", df.id)
                ], order="id")
                for ot in otrosi_recs:
                    ot_name = ot.folder_name or ot.name or 'OtroSi'
                    if ot.is_plantilla or 'plantilla' in ot_name.lower():
                        continue
                    ot_ws = WS.create({
                        "browser_id": self.id,
                        "name": ot_name,
                        "parent_id": df_ws.id,
                        "otrosi_drive_id": ot.id,
                        "folder_google_id": ot.folder_id or False,
                    })
                    # cuenta_de_cobros bajo otrosi → agrupadas en nodo "Cuentas de cobro"
                    cobros_ot = self.env["cuenta_de_cobros"].sudo().search([
                        ("otrosi_drive_id", "=", ot.id)
                    ], order="id")
                    if cobros_ot:
                        cobros_ot_ws = WS.create({
                            "browser_id": self.id,
                            "name": "Cuentas de cobro",
                            "parent_id": ot_ws.id,
                            "folder_google_id": cobros_ot[0].folder_guardar_id or False,
                        })
                        for cb in cobros_ot:
                            cb_name = cb.folder_name or cb.name or 'Cuenta de cobros'
                            WS.create({
                                "browser_id": self.id,
                                "name": cb_name,
                                "parent_id": cobros_ot_ws.id,
                                "cuenta_de_cobros_id": cb.id,
                                "folder_google_id": cb.folder_id or False,
                            })
                # cuenta_de_cobros directamente bajo drive.file → agrupadas en "Cuentas de cobro"
                cobros_df = self.env["cuenta_de_cobros"].sudo().search([
                    ("drive_id", "=", df.id),
                    ("otrosi_drive_id", "=", False),
                ], order="id")
                if cobros_df:
                    cobros_df_ws = WS.create({
                        "browser_id": self.id,
                        "name": "Cuentas de cobro",
                        "parent_id": df_ws.id,
                        "folder_google_id": cobros_df[0].folder_guardar_id or False,
                    })
                    for cb in cobros_df:
                        cb_name = cb.folder_name or cb.name or 'Cuenta de cobros'
                        WS.create({
                            "browser_id": self.id,
                            "name": cb_name,
                            "parent_id": cobros_df_ws.id,
                            "cuenta_de_cobros_id": cb.id,
                            "folder_google_id": cb.folder_id or False,
                        })

        # Mapa inverso: nombre de stage → stage_ws (para localizar INFORMES/COMUNICACIONES)
        stage_name_to_ws = {
            (self.env["drive_stage"].sudo().browse(sid).name or '').upper(): ws
            for sid, ws in stage_ws_by_id.items()
        }

        def _find_stage_ws(keyword):
            for sname, ws in stage_name_to_ws.items():
                if keyword in sname:
                    return ws
            return root_ws

        # ── INFORMES ──────────────────────────────────────────────────────────
        if informes_recs:
            informes_stage_ws = _find_stage_ws('INFORMES')
            for informe in informes_recs:
                iwk_map = {}
                valid_iwks = informe.workspace_tree_ids.filtered(lambda w: w.drive_file_id)
                for iwk in valid_iwks.sorted(key=lambda w: len(w.parent_path or '')):
                    iwk_name = ((iwk.name or '') + ' ' + (iwk.drive_file_id.folder_name or '')).upper()
                    if not iwk.parent_id and 'INFORMES' in iwk_name:
                        iwk_map[iwk.id] = informes_stage_ws
                        continue
                    parent_bws = iwk_map.get(iwk.parent_id.id, informes_stage_ws) if iwk.parent_id else informes_stage_ws
                    bws = WS.create({
                        "browser_id": self.id,
                        "name": iwk.name,
                        "parent_id": parent_bws.id,
                        "drive_file_id": iwk.drive_file_id.id,
                        "informes_workspace_id": iwk.id,
                        "folder_google_id": iwk.drive_file_id.folder_id2 or False,
                        "parent_folder_google_id": parent_bws.folder_google_id or False,
                    })
                    iwk_map[iwk.id] = bws

        # ── COMUNICACIONES ────────────────────────────────────────────────────
        if comunicaciones_recs:
            com_stage_ws = _find_stage_ws('COMUNICACIONES')
            for com in comunicaciones_recs:
                cwk_map = {}
                valid_cwks = com.workspace_tree_ids.filtered(lambda w: w.drive_file_id)
                for cwk in valid_cwks.sorted(key=lambda w: len(w.parent_path or '')):
                    cwk_name = ((cwk.name or '') + ' ' + (cwk.drive_file_id.folder_name or '')).upper()
                    if not cwk.parent_id and 'COMUNICACIONES' in cwk_name:
                        cwk_map[cwk.id] = com_stage_ws
                        continue
                    parent_bws = cwk_map.get(cwk.parent_id.id, com_stage_ws) if cwk.parent_id else com_stage_ws
                    bws = WS.create({
                        "browser_id": self.id,
                        "name": cwk.name,
                        "parent_id": parent_bws.id,
                        "drive_file_id": cwk.drive_file_id.id,
                        "comunicaciones_workspace_id": cwk.id,
                        "folder_google_id": cwk.drive_file_id.folder_id2 or False,
                        "parent_folder_google_id": parent_bws.folder_google_id or False,
                    })
                    cwk_map[cwk.id] = bws

        self.sudo().write({"selected_workspace_id": root_ws.id})

    @staticmethod
    def _mime_from_name(name):
        n = (name or '').lower()
        if n.endswith('.pdf'):
            return 'application/pdf'
        if n.endswith(('.docx', '.doc')):
            return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if n.endswith(('.xlsx', '.xls')):
            return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        if n.endswith(('.pptx', '.ppt')):
            return 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        if n.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg')):
            return 'image/jpeg'
        return 'application/octet-stream'

    def _drv_special_context(self, workspace):
        """Retorna (tipo, registro, workspace_espejo) para INFORMES/COMUNICACIONES."""
        self.ensure_one()
        ws = workspace
        while ws:
            if ws.informes_workspace_id:
                return 'informes', ws.informes_workspace_id.informes_id, ws.informes_workspace_id
            if ws.comunicaciones_workspace_id:
                return 'comunicaciones', ws.comunicaciones_workspace_id.comunicaciones_id, ws.comunicaciones_workspace_id
            if ws.drive_stage_id:
                stage_name = ((ws.drive_stage_id.name or ws.drive_stage_id.folder_name or '')).upper()
                if 'INFORMES' in stage_name:
                    informe = self.env['informes_drive'].sudo().search([
                        ('proyecto_relacionado', '=', self.project_id.id)
                    ], limit=1)
                    if not informe:
                        informe = self.env['informes_drive'].sudo().create({
                            'name': 'Informes',
                            'proyecto_relacionado': self.project_id.id,
                        })
                    mirror_ws = self._drv_get_or_create_special_root_ws(
                        'informes', informe, ws.drive_stage_id
                    )
                    if mirror_ws:
                        ws.sudo().write({
                            'drive_file_id': mirror_ws.drive_file_id.id,
                            'informes_workspace_id': mirror_ws.id,
                        })
                    return 'informes', informe, mirror_ws
                if 'COMUNICACIONES' in stage_name:
                    comunicacion = self.env['comunicaciones_drive'].sudo().search([
                        ('proyecto_relacionado', '=', self.project_id.id)
                    ], limit=1)
                    if not comunicacion:
                        comunicacion = self.env['comunicaciones_drive'].sudo().create({
                            'name': 'Comunicaciones',
                            'proyecto_relacionado': self.project_id.id,
                        })
                    mirror_ws = self._drv_get_or_create_special_root_ws(
                        'comunicaciones', comunicacion, ws.drive_stage_id
                    )
                    if mirror_ws:
                        ws.sudo().write({
                            'drive_file_id': mirror_ws.drive_file_id.id,
                            'comunicaciones_workspace_id': mirror_ws.id,
                        })
                    return 'comunicaciones', comunicacion, mirror_ws
            ws = ws.parent_id
        return False, False, False

    def _drv_get_or_create_special_root_ws(self, kind, record, stage):
        folder_gid = stage.folder_id or stage.google_drive_id
        if not folder_gid:
            return False

        df_vals = {
            'folder_name': stage.folder_name or stage.name,
            'folder_id2': folder_gid,
            'folder_guardar_id': folder_gid,
            'drive_id': stage.id,
            'proyecto_relacionado': self.project_id.id,
        }
        if kind == 'informes':
            df_vals['informes_id'] = record.id
            WSModel = self.env['informes.workspace'].sudo()
            rel_field = 'informes_id'
        else:
            df_vals['comunicaciones_id'] = record.id
            WSModel = self.env['comunicaciones.workspace'].sudo()
            rel_field = 'comunicaciones_id'

        DriveFile = self.env['drive.file'].sudo()
        df = DriveFile.search([
            ('folder_id2', '=', folder_gid),
            (rel_field, '=', record.id),
        ], limit=1)
        if not df:
            df = DriveFile.create(df_vals)

        mirror_ws = WSModel.search([
            (rel_field, '=', record.id),
            ('drive_file_id', '=', df.id),
        ], limit=1)
        if not mirror_ws:
            mirror_ws = WSModel.create({
                'name': df.folder_name or stage.name,
                rel_field: record.id,
                'drive_file_id': df.id,
            })
        return mirror_ws

    def _drv_create_special_folder_mirror(self, browser_ws, title, google_id, parent_gid):
        kind, record, parent_mirror_ws = self._drv_special_context(browser_ws)
        if not kind or not record:
            return False, False

        stage = browser_ws.drive_stage_id or self._find_parent_drive_stage_for_browser_ws(browser_ws)
        df_vals = {
            'folder_name': title,
            'folder_id2': google_id,
            'folder_guardar_id': parent_gid,
            'drive_id': stage.id if stage else False,
            'proyecto_relacionado': self.project_id.id,
        }
        if parent_mirror_ws and parent_mirror_ws.drive_file_id:
            df_vals['parent_folder_id'] = parent_mirror_ws.drive_file_id.id

        if kind == 'informes':
            df_vals['informes_id'] = record.id
            WSModel = self.env['informes.workspace'].sudo()
            rel_field = 'informes_id'
            browser_field = 'informes_workspace_id'
        else:
            df_vals['comunicaciones_id'] = record.id
            WSModel = self.env['comunicaciones.workspace'].sudo()
            rel_field = 'comunicaciones_id'
            browser_field = 'comunicaciones_workspace_id'

        DriveFile = self.env['drive.file'].sudo()
        df = DriveFile.search([
            ('folder_id2', '=', google_id),
            (rel_field, '=', record.id),
        ], limit=1)
        if not df:
            df = DriveFile.create(df_vals)

        mirror_ws = WSModel.search([
            (rel_field, '=', record.id),
            ('drive_file_id', '=', df.id),
        ], limit=1)
        if not mirror_ws:
            mirror_ws = WSModel.create({
                'name': title,
                rel_field: record.id,
                'drive_file_id': df.id,
                'parent_id': parent_mirror_ws.id if parent_mirror_ws else False,
            })
        return df, (browser_field, mirror_ws)

    def _drv_sync_special_tree_from_browser_ws(self, root_ws):
        """Replica el arbol visible del browser en Informes/Comunicaciones."""
        self.ensure_one()
        if not root_ws:
            return False

        kind, record, root_mirror_ws = self._drv_special_context(root_ws)
        if not kind or not record:
            return False

        if kind == 'informes':
            rel_field = 'informes_id'
            browser_field = 'informes_workspace_id'
        else:
            rel_field = 'comunicaciones_id'
            browser_field = 'comunicaciones_workspace_id'

        WS = self.env['drive.browser.workspace'].sudo()
        Drive = self.env['drive'].sudo()

        queue = [root_ws.sudo()]
        visited = set()
        while queue:
            ws = queue.pop(0)
            if not ws or ws.id in visited:
                continue
            visited.add(ws.id)

            mirror_ws = getattr(ws, browser_field)
            if ws == root_ws:
                if not mirror_ws and ws.folder_google_id and ws.parent_id:
                    parent_gid = ws.parent_id.folder_google_id or ws.parent_folder_google_id
                    mirror_df, mirror_ref = self._drv_create_special_folder_mirror(
                        ws.parent_id, ws.name, ws.folder_google_id, parent_gid
                    )
                    if mirror_df:
                        _, mirror_ws = mirror_ref
                else:
                    mirror_ws = mirror_ws or root_mirror_ws
                if mirror_ws and mirror_ws.drive_file_id:
                    write_vals = {
                        'drive_file_id': mirror_ws.drive_file_id.id,
                        browser_field: mirror_ws.id,
                    }
                    if not ws.drive_file_id or not getattr(ws, browser_field):
                        ws.write(write_vals)
            elif ws.folder_google_id and ws.parent_id:
                parent_gid = ws.parent_id.folder_google_id or ws.parent_folder_google_id
                mirror_df, mirror_ref = self._drv_create_special_folder_mirror(
                    ws.parent_id, ws.name, ws.folder_google_id, parent_gid
                )
                if mirror_df:
                    _, mirror_ws = mirror_ref
                    vals = {'drive_file_id': mirror_df.id, browser_field: mirror_ws.id}
                    if (not ws.drive_file_id or ws.drive_file_id.id != mirror_df.id
                            or not getattr(ws, browser_field)):
                        ws.write(vals)

            stage_df = ws.drive_file_id or (mirror_ws.drive_file_id if mirror_ws else False)
            if ws.folder_google_id and stage_df:
                update_vals = {
                    'stage2_id': stage_df.id,
                    rel_field: record.id,
                }
                Drive.search([
                    ('folder_id', '=', ws.folder_google_id),
                    ('is_folder', '=', False),
                ]).with_context(skip_drive_sync=True, skip_integrity=True).write(update_vals)

            children = WS.search([
                ('browser_id', '=', self.id),
                ('parent_id', '=', ws.id),
            ])
            queue.extend(children)
        return True

    def _drv_stage_vals_for_workspace(self, ws):
        stage_vals = {}
        if ws.cuenta_de_cobros_id:
            stage_vals['stage4_id'] = ws.cuenta_de_cobros_id.id
        elif ws.otrosi_drive_id:
            stage_vals['stage5_id'] = ws.otrosi_drive_id.id
        elif ws.informes_workspace_id and ws.informes_workspace_id.drive_file_id:
            stage_vals['stage2_id'] = ws.informes_workspace_id.drive_file_id.id
            if ws.informes_workspace_id.informes_id:
                stage_vals['informes_id'] = ws.informes_workspace_id.informes_id.id
        elif ws.comunicaciones_workspace_id and ws.comunicaciones_workspace_id.drive_file_id:
            stage_vals['stage2_id'] = ws.comunicaciones_workspace_id.drive_file_id.id
            if ws.comunicaciones_workspace_id.comunicaciones_id:
                stage_vals['comunicaciones_id'] = ws.comunicaciones_workspace_id.comunicaciones_id.id
        elif ws.drive_file_id and ws.drive_file_id.informes_id:
            stage_vals['stage2_id'] = ws.drive_file_id.id
            stage_vals['informes_id'] = ws.drive_file_id.informes_id.id
        elif ws.drive_file_id and ws.drive_file_id.comunicaciones_id:
            stage_vals['stage2_id'] = ws.drive_file_id.id
            stage_vals['comunicaciones_id'] = ws.drive_file_id.comunicaciones_id.id
        else:
            kind, record, mirror_ws = self._drv_special_context(ws)
            if mirror_ws and mirror_ws.drive_file_id:
                stage_vals['stage2_id'] = mirror_ws.drive_file_id.id
                if kind == 'informes':
                    stage_vals['informes_id'] = record.id
                elif kind == 'comunicaciones':
                    stage_vals['comunicaciones_id'] = record.id
            elif ws.drive_file_id:
                stage_vals['stage2_id'] = ws.drive_file_id.id
            elif ws.drive_stage_id:
                stage_vals['stage3_id'] = ws.drive_stage_id.id
        return stage_vals

    def _find_parent_drive_stage_for_browser_ws(self, workspace):
        ws = workspace
        while ws:
            if ws.drive_stage_id:
                return ws.drive_stage_id
            ws = ws.parent_id
        return self.env['drive_stage'].sudo().browse()

    def _refresh_entries_from_odoo_models(self):
        """Llena las entradas del workspace activo leyendo registros drive de la BD."""
        self.ensure_one()
        ws = self.selected_workspace_id
        if not ws:
            return

        # Invalidar caché para leer datos frescos (threads de background pueden haber creado registros)
        ws.invalidate_recordset()

        if not ws.parent_id and ws.folder_google_id:
            try:
                self._fix_root_folder_order_in_drive()
                ws.invalidate_recordset()
            except Exception as _root_order_err:
                _logger.warning("No se pudo ajustar orden raíz al refrescar: %s", _root_order_err)

        Entry = self.env["drive.browser.entry"].sudo()
        Entry.search([("browser_id", "=", self.id), ("parent_workspace_id", "=", ws.id)]).unlink()

        vals = []

        # Si es workspace de contratista, asegurar nodos OtroSí antes de listar hijos
        if ws.drive_file_id and not ws.otrosi_drive_id:
            self._ensure_otrosi_workspace_nodes(ws)

        # Buscar hijos directamente en BD (evita caché de child_ids desactualizado)
        WS_model = self.env['drive.browser.workspace'].sudo()
        child_workspaces = WS_model.search([
            ('browser_id', '=', self.id),
            ('parent_id', '=', ws.id),
        ])

        # Fallback GDrive: si el workspace tiene folder_google_id, buscar subcarpetas
        # en Google Drive y crear los workspace nodes faltantes (ej. carpetas subidas vía browser)
        if ws.folder_google_id:
            try:
                gdrive = self._drv_get_drive()
                gd_subfolders = gdrive.ListFile({
                    'q': (f"'{ws.folder_google_id}' in parents"
                          " and mimeType='application/vnd.google-apps.folder'"
                          " and trashed=false")
                }).GetList()
                for gf in gd_subfolders:
                    gf_id  = gf.get('id')
                    gf_name = gf.get('title') or gf.get('name', 'Carpeta')
                    if not gf_id or gf_name in _FOLDER_HIDDEN:
                        continue
                    if gf_id == ws.folder_google_id:
                        continue
                    # Buscar si ya existe un workspace con ese google_id
                    existing = WS_model.search([
                        ('browser_id', '=', self.id),
                        ('folder_google_id', '=', gf_id),
                    ], limit=1)
                    if existing:
                        if existing.id == ws.id:
                            continue
                        if not existing.parent_id or existing.parent_id.id != ws.id:
                            existing.write({
                                'parent_id': ws.id,
                                'parent_folder_google_id': ws.folder_google_id,
                            })
                        child_workspaces = child_workspaces | existing
                    else:
                        new_ws = WS_model.create({
                            'browser_id': self.id,
                            'name': gf_name,
                            'folder_google_id': gf_id,
                            'parent_folder_google_id': ws.folder_google_id,
                            'parent_id': ws.id,
                        })
                        mirror_df, mirror_ref = self._drv_create_special_folder_mirror(
                            ws, gf_name, gf_id, ws.folder_google_id
                        )
                        if mirror_df:
                            mirror_field, mirror_ws = mirror_ref
                            new_ws.write({
                                'drive_file_id': mirror_df.id,
                                mirror_field: mirror_ws.id,
                            })
                        child_workspaces = child_workspaces | new_ws
                        _logger.info("GDrive fallback: workspace creado %s '%s'", new_ws.id, gf_name)
            except Exception as _ge:
                _logger.warning("GDrive subfolder fallback ws=%s: %s", ws.id, _ge)

        # Sub-carpetas del workspace → entradas tipo folder
        deduped_children = []
        children_by_key = {}
        for child in child_workspaces:
            child_name = ((child.name or '') + ' ' + (child.drive_file_id.folder_name or '')).upper()
            current_name = ((ws.name or '') + ' ' + (ws.drive_file_id.folder_name or '')).upper()
            same_google_folder = (
                child.folder_google_id
                and ws.folder_google_id
                and child.folder_google_id == ws.folder_google_id
            )
            same_special_root = (
                (ws.informes_workspace_id and 'INFORMES' in current_name and 'INFORMES' in child_name)
                or (
                    ws.comunicaciones_workspace_id
                    and 'COMUNICACIONES' in current_name
                    and 'COMUNICACIONES' in child_name
                )
            )
            if same_google_folder or same_special_root:
                continue
            key = (
                child.folder_google_id
                or (child.drive_file_id.folder_id2 if child.drive_file_id else False)
                or f"ws:{child.id}"
            )
            current = children_by_key.get(key)
            if not current:
                children_by_key[key] = child
                deduped_children.append(child)
                continue

            current_is_special = bool(current.informes_workspace_id or current.comunicaciones_workspace_id)
            child_is_special = bool(child.informes_workspace_id or child.comunicaciones_workspace_id)
            keep, other = current, child
            if child_is_special and not current_is_special:
                keep, other = child, current
                children_by_key[key] = keep
                deduped_children = [keep if c.id == current.id else c for c in deduped_children]

            merge_vals = {}
            if not keep.folder_google_id and other.folder_google_id:
                merge_vals['folder_google_id'] = other.folder_google_id
            if not keep.parent_folder_google_id and other.parent_folder_google_id:
                merge_vals['parent_folder_google_id'] = other.parent_folder_google_id
            if not keep.drive_file_id and other.drive_file_id:
                merge_vals['drive_file_id'] = other.drive_file_id.id
            if not keep.informes_workspace_id and other.informes_workspace_id:
                merge_vals['informes_workspace_id'] = other.informes_workspace_id.id
            if not keep.comunicaciones_workspace_id and other.comunicaciones_workspace_id:
                merge_vals['comunicaciones_workspace_id'] = other.comunicaciones_workspace_id.id
            if merge_vals:
                keep.write(merge_vals)

        child_workspaces = self.env['drive.browser.workspace'].browse([c.id for c in deduped_children])

        for child in child_workspaces:
            if child.otrosi_drive_id and child.otrosi_drive_id.is_plantilla:
                continue
            vals.append({
                "browser_id": self.id,
                "parent_workspace_id": ws.id,
                "entry_type": "folder",
                "name": child.name,
                "google_id": child.folder_google_id or False,
                "target_workspace_id": child.id,
            })

        # Archivos: buscar registros drive vinculados al workspace
        Drive = self.env["drive"].sudo()
        if ws.cuenta_de_cobros_id:
            drive_recs = Drive.search([("stage4_id", "=", ws.cuenta_de_cobros_id.id)])
        elif ws.informes_workspace_id:
            iwk = ws.informes_workspace_id
            if iwk.drive_file_id:
                drive_recs = Drive.search([
                    ("stage2_id", "=", iwk.drive_file_id.id),
                    ("informes_id", "=", iwk.informes_id.id),
                ])
            else:
                drive_recs = Drive.browse()
        elif ws.comunicaciones_workspace_id:
            cwk = ws.comunicaciones_workspace_id
            if cwk.drive_file_id:
                drive_recs = Drive.search([
                    ("stage2_id", "=", cwk.drive_file_id.id),
                    ("comunicaciones_id", "=", cwk.comunicaciones_id.id),
                ])
            else:
                drive_recs = Drive.browse()
        elif ws.drive_stage_id:
            drive_recs = Drive.search([("stage3_id", "=", ws.drive_stage_id.id)])
        elif ws.otrosi_drive_id:
            drive_recs = Drive.search([("stage5_id", "=", ws.otrosi_drive_id.id)])
        elif ws.drive_file_id:
            drive_recs = Drive.search([("stage2_id", "=", ws.drive_file_id.id), ("is_folder", "=", False)])
        elif ws.folder_google_id:
            drive_recs = Drive.search([("folder_id", "=", ws.folder_google_id), ("is_folder", "=", False)])
        else:
            drive_recs = Drive.browse()

        # Incluir archivos subidos directamente desde el browser (solo tienen folder_id, sin stage linkage)
        if ws.folder_google_id:
            extra_recs = Drive.search([
                ("folder_id", "=", ws.folder_google_id),
                ("is_folder", "=", False),
                ("id", "not in", drive_recs.ids),
            ])
            if extra_recs:
                drive_recs = drive_recs | extra_recs

            # Fallback fuerte para carpetas creadas/subidas desde el browser:
            # si los archivos existen en Google Drive pero aún no tienen registro
            # drive usable en Odoo, crearlo aquí para que se vean dentro de esta carpeta.
            try:
                gdrive = self._drv_get_drive()
                folder_mime = "application/vnd.google-apps.folder"
                gd_files = gdrive.ListFile({
                    'q': (f"'{ws.folder_google_id}' in parents"
                          f" and mimeType!='{folder_mime}'"
                          " and trashed=false")
                }).GetList()
                for gf in gd_files:
                    gf_id = gf.get('id')
                    if not gf_id:
                        continue
                    if _drv_is_hidden_browser_file(gf.get('title') or gf.get('name')):
                        continue
                    existing_dr = Drive.search([
                        '|', '|',
                        ('file_id', '=', gf_id),
                        ('drive_file_id', '=', gf_id),
                        '&', ('folder_id', '=', ws.folder_google_id), ('file_name', '=', gf.get('title') or gf.get('name')),
                    ], limit=1)
                    if existing_dr:
                        special_vals = self._drv_stage_vals_for_workspace(ws)
                        missing_vals = {
                            key: value for key, value in special_vals.items()
                            if value and not existing_dr[key]
                        }
                        if missing_vals:
                            existing_dr.with_context(skip_drive_sync=True, skip_integrity=True).write(missing_vals)
                        if existing_dr.id not in drive_recs.ids:
                            drive_recs = drive_recs | existing_dr
                        continue

                    fname = gf.get('title') or gf.get('name') or 'Documento'
                    preview_url = f"https://drive.google.com/file/d/{gf_id}/preview"
                    edit_url = gf.get('alternateLink') or gf.get('webViewLink') or f"https://drive.google.com/file/d/{gf_id}/view"
                    preview_html = (
                        f'<iframe src="{preview_url}" width="100%" height="600px"'
                        f' frameborder="0" allowfullscreen loading="lazy"></iframe>'
                    )
                    edit_html = f'<iframe src="{edit_url}" width="100%" height="600px" frameborder="0"></iframe>'
                    new_dr = Drive.with_context(from_process_button=True, skip_drive_sync=True, skip_integrity=True).create({
                        'file_name': fname,
                        'folder_id': ws.folder_google_id,
                        'is_folder': False,
                        **self._drv_stage_vals_for_workspace(ws),
                        'file_id': gf_id,
                        'drive_file_id': gf_id,
                        'preview_url': preview_url,
                        'edit_url': edit_url,
                        'preview_html': preview_html,
                        'edit_html': edit_html,
                    })
                    drive_recs = drive_recs | new_dr
                    _logger.info(
                        "GDrive file fallback: drive creado id=%s folder=%s file=%s",
                        new_dr.id, ws.folder_google_id, fname
                    )
            except Exception as _gfe:
                _logger.warning("GDrive file fallback ws=%s: %s", ws.id, _gfe)

        for dr in drive_recs:
            gid = dr.drive_file_id or dr.file_id
            fname = dr.file_name or 'Documento'
            if _drv_is_hidden_browser_file(fname):
                continue
            vals.append({
                "browser_id": self.id,
                "parent_workspace_id": ws.id,
                "entry_type": "file",
                "name": fname,
                "google_id": gid or False,
                "mime_type": self._mime_from_name(fname),
                "web_view_link": dr.edit_url or False,
                "drive_id": dr.id,
            })

        if vals:
            Entry.create(vals)

    # ---------------------------------------------------------
    # LÓGICA DE SINCRONIZACIÓN (REEMPLAZADA)
    # ---------------------------------------------------------

    def _drv_root_folder_exists(self, folder_id):
        """Devuelve True si la carpeta existe en GDrive y no está en la papelera."""
        try:
            drive = self._drv_get_drive()
            meta = drive.auth.service.files().get(
                fileId=folder_id,
                supportsAllDrives=True,
                fields='id,trashed',
            ).execute()
            return not meta.get('trashed', False)
        except Exception:
            return False

    def _drv_sync_workspace(self):
        """Sincroniza la estructura de carpetas desde Google Drive."""
        self.ensure_one()
        WS = self.env["drive.browser.workspace"].sudo()

        # Guardar el google_id del workspace activo para restaurarlo si el sync lo limpia
        _saved_selected_gid = (
            self.selected_workspace_id.folder_google_id
            if self.selected_workspace_id else None
        )

        root_id = self._drv_find_project_root_folder_id()

        # Verificar que la carpeta raíz sigue existiendo en GDrive
        if not self._drv_root_folder_exists(root_id):
            _logger.info(
                ">>> _drv_sync_workspace: carpeta raíz %s eliminada/trashed → limpiando workspaces", root_id
            )
            # Limpiar referencias antes de eliminar para evitar MissingError en el cliente
            self.write({'selected_workspace_id': False, 'selected_entry_id': False})
            WS.search([('browser_id', '=', self.id)]).with_context(_drv_from_model_delete=True).unlink()
            # Buscar nueva raíz sin el caché (workspaces ya limpiados)
            try:
                root_id = self._drv_find_project_root_folder_id(skip_cache=True)
                _logger.info(">>> _drv_sync_workspace: nueva raíz encontrada: %s", root_id)
            except Exception as _ne:
                _logger.info(">>> _drv_sync_workspace: no se encontró nueva raíz → browser vacío: %s", _ne)
                return

        root_name = self._drv_project_name()
        nodes = self._drv_list_subfolders_recursive(root_id)

        # 1) Upsert raíz
        root_ws = WS.search([
            ("browser_id", "=", self.id),
            ("folder_google_id", "=", root_id),
        ], limit=1)

        if root_ws:
            if root_ws.name != root_name:
                root_ws.write({"name": root_name})
            if root_ws.parent_id:
                root_ws.write({"parent_id": False})
        else:
            root_ws = WS.create({
                "browser_id": self.id,
                "name": root_name,
                "folder_google_id": root_id,
                "parent_folder_google_id": False,
                "parent_id": False,
            })

        # 2) Upsert de nodos hijos
        google_ids = [n["id"] for n in nodes]
        existing = WS.search([
            ("browser_id", "=", self.id),
            ("folder_google_id", "in", google_ids),
        ])
        ws_by_gid = {r.folder_google_id: r for r in existing}

        for n in nodes:
            raw_name = n["name"] or "Sin nombre"
            if raw_name in _FOLDER_HIDDEN:
                continue
            display_name = _FOLDER_RENAME_MAP.get(raw_name, raw_name)
            rec = ws_by_gid.get(n["id"])
            vals = {
                "browser_id": self.id,
                "name": display_name,
                "folder_google_id": n["id"],
                "parent_folder_google_id": n["parent_id"],
            }
            if rec:
                diff = {}
                for k, v in vals.items():
                    cur = getattr(rec, k)
                    if hasattr(cur, 'id'):
                        cur = cur.id
                    if cur != v:
                        diff[k] = v
                if diff:
                    rec.write(diff)
            else:
                ws_by_gid[n["id"]] = WS.create(vals)

        # 2.5) Eliminar workspaces de carpetas que ya no existen en GDrive (trashed/borradas)
        valid_gids = set(google_ids) | {root_id}
        orphan_ws = WS.search([
            ("browser_id", "=", self.id),
            ("folder_google_id", "!=", False),
            ("folder_google_id", "not in", list(valid_gids)),
        ])
        if orphan_ws:
            _logger.info(
                ">>> _drv_sync_workspace: eliminando %d workspaces huérfanos: %s",
                len(orphan_ws), [w.folder_google_id for w in orphan_ws]
            )
            orphan_ids = orphan_ws.ids
            # Si selected_workspace_id o selected_entry_id apuntan a un huérfano, limpiarlos
            clear = {}
            if self.selected_workspace_id and self.selected_workspace_id.id in orphan_ids:
                clear['selected_workspace_id'] = False
                clear['selected_entry_id'] = False
            elif self.selected_entry_id and self.selected_entry_id.parent_workspace_id.id in orphan_ids:
                clear['selected_entry_id'] = False
            if clear:
                self.write(clear)
            orphan_ws.with_context(_drv_from_model_delete=True).unlink()

        # 3) Segunda pasada: resolver parent_id (anidar la estructura)
        full_by_gid = dict(ws_by_gid)
        full_by_gid[root_id] = root_ws

        for rec in ws_by_gid.values():
            parent_gid = rec.parent_folder_google_id
            parent_rec = full_by_gid.get(parent_gid) if parent_gid else None
            new_parent_id = parent_rec.id if parent_rec else root_ws.id

            if (rec.parent_id.id if rec.parent_id else False) != new_parent_id:
                rec.write({"parent_id": new_parent_id})

        # 4) Enlazar workspaces con registros drive.file por Google ID
        DriveFile = self.env['drive.file'].sudo()
        unlinked = [ws for ws in ws_by_gid.values()
                    if ws.folder_google_id and not ws.drive_file_id]
        if unlinked:
            gids = [ws.folder_google_id for ws in unlinked]
            df_map = {df.folder_id2: df for df in DriveFile.search([('folder_id2', 'in', gids)])}
            for ws in unlinked:
                df = df_map.get(ws.folder_google_id)
                if df:
                    ws.write({'drive_file_id': df.id})

        # 5) Selección: restaurar workspace previo si fue borrado, o usar raíz por defecto
        if not self.selected_workspace_id:
            target_ws = None
            if _saved_selected_gid:
                all_built = dict(ws_by_gid)
                all_built[root_id] = root_ws
                target_ws = all_built.get(_saved_selected_gid) or WS.search([
                    ('browser_id', '=', self.id),
                    ('folder_google_id', '=', _saved_selected_gid),
                ], limit=1)
            self.write({"selected_workspace_id": (target_ws or root_ws).id})

    def action_refresh_current_workspace(self):
        """Llamado desde JS al montar el browser: sincroniza la carpeta activa con Drive."""
        self.ensure_one()
        if self.selected_workspace_id:
            self._ensure_otrosi_workspace_nodes(self.selected_workspace_id)
            self._drv_refresh_entries()
        return True

    def _ensure_otrosi_workspace_nodes(self, ws):
        """Crea nodos de workspace faltantes para otrosi_drive bajo un contratista."""
        if not ws.drive_file_id or ws.otrosi_drive_id:
            _logger.info("[OtroSi] Skip workspace '%s': drive_file_id=%s otrosi_drive_id=%s",
                         ws.name, ws.drive_file_id.id if ws.drive_file_id else False,
                         ws.otrosi_drive_id.id if ws.otrosi_drive_id else False)
            return
        WS = self.env['drive.browser.workspace'].sudo()
        otrosi_recs = self.env['otrosi_drive'].sudo().search([
            ('drive_id', '=', ws.drive_file_id.id),
            ('is_plantilla', '=', False),
        ])
        _logger.info("[OtroSi] Workspace '%s' (drive_file_id=%s): encontrados %d otrosi_drive",
                     ws.name, ws.drive_file_id.id, len(otrosi_recs))
        for ot in otrosi_recs:
            ot_name = ot.folder_name or ot.name or 'OtroSi'
            if 'plantilla' in ot_name.lower():
                continue
            existing = WS.search([
                ('otrosi_drive_id', '=', ot.id),
                ('browser_id', '=', self.id),
            ], limit=1)
            if not existing:
                _logger.info("[OtroSi] Creando nodo '%s' bajo '%s'", ot_name, ws.name)
                WS.create({
                    'browser_id': self.id,
                    'name': ot_name,
                    'parent_id': ws.id,
                    'otrosi_drive_id': ot.id,
                    'folder_google_id': ot.folder_id or False,
                })
            else:
                _logger.info("[OtroSi] Nodo '%s' ya existe", ot_name)

    def _drv_refresh_entries(self):
        """Descarga el contenido de la carpeta seleccionada."""
        self.ensure_one()
        if not self.selected_workspace_id or not self.selected_workspace_id.folder_google_id:
            return

        Entry = self.env["drive.browser.entry"].sudo()
        Drive = self.env["drive"].sudo()

        # Guardar create_uid originales antes de borrar para no sobreescribir al creador
        _existing_entries = Entry.search([
            ("browser_id", "=", self.id),
            ("parent_workspace_id", "=", self.selected_workspace_id.id),
        ])
        _original_creators = {e.google_id: e.create_uid.id for e in _existing_entries if e.google_id}
        _browser_creator = self.create_uid.id or self.env.uid
        _existing_entries.unlink()

        children = self._drv_list_children(self.selected_workspace_id.folder_google_id)
        folder_mime = "application/vnd.google-apps.folder"

        # ── Auto-numeración: sólo cuando proyecto está en estado Completado ──
        if self.project_id.coordinador_g == 'green':
            _ws = self.selected_workspace_id
            _ws_name = _ws.name or ''
            _ws_prefix = _extract_numeric_prefix(_ws_name)

            # Si este workspace no tiene prefijo pero su padre sí, numerar primero el nivel padre
            if not _ws_prefix and _ws.parent_id:
                _par = _ws.parent_id
                _par_name = _par.name or ''
                _par_prefix = _extract_numeric_prefix(_par_name)
                if _par_prefix and _par.folder_google_id:
                    try:
                        _gdrv0 = self._drv_get_drive()
                        _renamed_sibs = _drv_renumber_all_items(_gdrv0, _par.folder_google_id, _par_name)
                        if _renamed_sibs:
                            _sib_recs = self.env['drive.browser.workspace'].search(
                                [('browser_id', '=', self.id), ('parent_id', '=', _par.id)]
                            )
                            for _s in _sib_recs:
                                if _s.folder_google_id in _renamed_sibs:
                                    _s.write({'name': _renamed_sibs[_s.folder_google_id]})
                            _new_ws_name = _renamed_sibs.get(_ws.folder_google_id)
                            if _new_ws_name:
                                _ws.write({'name': _new_ws_name})
                                _ws_name = _new_ws_name
                    except Exception as _ae:
                        _logger.error("Auto-num nivel padre falló: %s", _ae)
                    _ws_prefix = _extract_numeric_prefix(_ws_name)

            if _ws_prefix:
                _full_pat = _re.compile(r'^' + _re.escape(_ws_prefix) + r'\.(\d+)(?:\s|_|\.|$)')
                _bare_pat = _re.compile(r'^(\d+)([\s_])(.*)')
                max_seq = 0
                for c in children:
                    t = c.get('title') or c.get('name') or ''
                    m = _full_pat.match(t)
                    if m:
                        try:
                            max_seq = max(max_seq, int(m.group(1)))
                        except ValueError:
                            pass
                        continue
                    m2 = _bare_pat.match(t)
                    if m2:
                        try:
                            max_seq = max(max_seq, int(m2.group(1)))
                        except ValueError:
                            pass

                _gdrive = self._drv_get_drive()
                for c in children:
                    title = c.get('title') or c.get('name') or ''
                    if not title or title in _FOLDER_HIDDEN or title in _FOLDER_RENAME_MAP:
                        continue
                    if _full_pat.match(title):
                        continue
                    m2 = _bare_pat.match(title)
                    if m2:
                        bare_num = m2.group(1)
                        rest = m2.group(3)
                        new_title = f"{_ws_prefix}.{bare_num} {rest}"
                    else:
                        max_seq += 1
                        new_title = f"{_ws_prefix}.{max_seq} {title}"
                    try:
                        gf = _gdrive.CreateFile({'id': c['id']})
                        gf['title'] = new_title
                        gf.Upload()
                        c['title'] = new_title
                        if 'name' in c:
                            c['name'] = new_title
                    except Exception as _e:
                        _logger.warning("Auto-numeración: '%s': %s", title, _e)
        # ── fin auto-numeración ──

        Workspace = self.env["drive.browser.workspace"].sudo()
        existing = {w.folder_google_id: w for w in self.selected_workspace_id.child_ids if w.folder_google_id}

        drive_field_name = 'file_id'
        if 'id_drive' in Drive._fields:
            drive_field_name = 'id_drive'
        elif 'google_id' in Drive._fields:
            drive_field_name = 'google_id'

        # Recolectar todos los google IDs de archivos para hacer UNA sola consulta (evita N+1)
        file_gids = [
            c.get("id") for c in children
            if c.get("id") and c.get("mimeType") != folder_mime
        ]
        drive_by_gid = {}
        if file_gids:
            drive_recs = Drive.search([(drive_field_name, "in", file_gids)])
            drive_by_gid = {getattr(r, drive_field_name): r.id for r in drive_recs}

        vals = []
        for c in children:
            gid = c.get("id")
            if not gid:
                continue

            name = c.get("title") or c.get("name") or "Sin nombre"
            if name in _FOLDER_HIDDEN:
                continue
            name = _FOLDER_RENAME_MAP.get(name, name)
            mime = c.get("mimeType")
            is_folder = (mime == folder_mime)

            target_ws_id = False
            if is_folder:
                ws = existing.get(gid)
                if not ws:
                    ws = Workspace.create({
                        "browser_id": self.id,
                        "name": name,
                        "folder_google_id": gid,
                        "parent_id": self.selected_workspace_id.id,
                        "parent_folder_google_id": self.selected_workspace_id.folder_google_id,
                    })
                    mirror_df, mirror_ref = self._drv_create_special_folder_mirror(
                        self.selected_workspace_id, name, gid, self.selected_workspace_id.folder_google_id
                    )
                    if mirror_df:
                        mirror_field, mirror_ws = mirror_ref
                        ws.write({
                            'drive_file_id': mirror_df.id,
                            mirror_field: mirror_ws.id,
                        })
                    existing[gid] = ws
                target_ws_id = ws.id

            vals.append({
                "browser_id": self.id,
                "parent_workspace_id": self.selected_workspace_id.id,
                "entry_type": "folder" if is_folder else "file",
                "name": name,
                "google_id": gid,
                "mime_type": mime,
                "web_view_link": c.get("alternateLink") or c.get("webViewLink"),
                "thumbnail_link": c.get("thumbnailLink"),
                "create_uid": _original_creators.get(gid, _browser_creator),
                "target_workspace_id": target_ws_id,
                "drive_id": drive_by_gid.get(gid, False),
            })

        # Batch-link workspaces that have no drive_file_id yet (same logic as _drv_sync_workspace step 4)
        unlinked_ws = [ws for ws in existing.values() if ws.folder_google_id and not ws.drive_file_id]
        if unlinked_ws:
            gids_to_link = [ws.folder_google_id for ws in unlinked_ws]
            DriveFile = self.env['drive.file'].sudo()
            df_map = {df.folder_id2: df for df in DriveFile.search([('folder_id2', 'in', gids_to_link)])}
            for ws in unlinked_ws:
                df = df_map.get(ws.folder_google_id)
                if df:
                    ws.write({'drive_file_id': df.id})

        if vals:
            Entry.create(vals)

    @api.model
    def get_or_create_browser(self, project_id=None, sync=True):
        browser = self.search([('project_id', '=', project_id)], limit=1)
        if not browser:
            browser = self.create({'project_id': project_id})
        if sync:
            browser._drv_sync_workspace()
            browser._drv_refresh_entries()
        return browser

    def action_open_browser(self):
        b = self.get_or_create_browser()
        return {
            'name': 'Drive',
            'type': 'ir.actions.act_window',
            'res_model': 'drive.browser',
            'view_mode': 'form',
            'res_id': b.id,
            'target': 'current',
            'flags': {'clear_breadcrumbs': True},
        }


class DriveBrowserProject(models.Model):
    _name = "drive.browser.project"
    _description = "Drive Browser - Proyectos (selector)"

    project_id = fields.Many2one("contratacion", required=True, ondelete="cascade")
    name = fields.Char(related="project_id.new_name", store=False)

    def action_open_workspace(self):
        self.ensure_one()
        return self.project_id.abrir_drive_browser()


class DriveBrowserCreateFolder(models.TransientModel):
    _name = 'drive.browser.create.folder'
    _description = 'Drive Browser – Nueva Carpeta'

    browser_id    = fields.Many2one('drive.browser', required=True, ondelete='cascade')
    workspace_id  = fields.Many2one('drive.browser.workspace', required=True)
    workspace_name = fields.Char(related='workspace_id.name', readonly=True)
    name          = fields.Char(string='Nombre de la carpeta')
    description   = fields.Text(string='Descripción')

    def action_create(self):
        self.ensure_one()
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            raise AccessError(_("El rol Usuario de consulta solo puede ver documentos."))
        if not self.name or not self.name.strip():
            raise UserError(_("Debes ingresar un nombre para la carpeta."))
        if not self.workspace_id.folder_google_id:
            raise UserError(_("La carpeta seleccionada no tiene ID de Google Drive."))

        # Guardar referencias antes de cualquier operación que pueda borrar workspaces
        browser = self.browser_id
        parent_ws = self.workspace_id
        parent_gid = parent_ws.folder_google_id
        parent_name = parent_ws.name or ''

        drive = browser._drv_get_drive()
        prefix = _get_next_prefix_for_folder(drive, parent_gid, parent_name)
        folder_title = f"{prefix} {self.name.strip()}" if prefix else self.name.strip()

        f = drive.CreateFile({
            'title': folder_title,
            'mimeType': 'application/vnd.google-apps.folder',
            'description': self.description or '',
            'parents': [{'id': parent_gid}],
        })
        f.Upload()
        new_gid = f['id']

        # Crear el nodo workspace directamente en Odoo (sin _drv_sync_workspace que borra todo)
        Workspace = self.env['drive.browser.workspace'].sudo()
        new_ws = Workspace.create({
            'browser_id': browser.id,
            'name': folder_title,
            'folder_google_id': new_gid,
            'parent_folder_google_id': parent_gid,
            'parent_id': parent_ws.id,
        })

        # Crear drive.file vinculado
        try:
            mirror_df, mirror_ref = browser._drv_create_special_folder_mirror(
                parent_ws, folder_title, new_gid, parent_gid
            )
            if mirror_df:
                new_df = mirror_df
                mirror_field, mirror_ws = mirror_ref
                new_ws.write({mirror_field: mirror_ws.id})
            else:
                stage = self._find_parent_drive_stage(parent_ws)
                new_df = self.env['drive.file'].sudo().create({
                    'folder_name': folder_title,
                    'folder_id2': new_gid,
                    'drive_id': stage.id if stage else False,
                    'proyecto_relacionado': browser.project_id.id,
                })
            new_ws.write({'drive_file_id': new_df.id})
        except Exception as _ce:
            _logger.warning("No se pudo crear drive.file para nueva carpeta %s: %s", new_gid, _ce)

        # Solo refrescar las entradas de la carpeta actual (sin toque a Drive)
        browser._refresh_entries_from_odoo_models()

        return {'type': 'ir.actions.act_window_close'}

    def _find_parent_drive_stage(self, workspace):
        ws = workspace
        while ws:
            if ws.drive_stage_id:
                return ws.drive_stage_id
            ws = ws.parent_id
        return self.env['drive_stage'].sudo().browse()


class DriveBrowserUpload(models.TransientModel):
    _name = 'drive.browser.upload'
    _description = 'Drive Browser – Subir Documento'

    browser_id     = fields.Many2one('drive.browser', required=True, ondelete='cascade')
    workspace_id   = fields.Many2one('drive.browser.workspace', required=True)
    workspace_name = fields.Char(related='workspace_id.name', readonly=True)
    upload_mode    = fields.Char(default='file')
    new_mcb_acta_comite_id = fields.Many2one('new_mcb', string='Acta comité MCB')
    description    = fields.Text(string='Descripción')
    file_data      = fields.Binary(string='Archivo', attachment=True)
    file_name      = fields.Char()

    def action_upload(self):
        self.ensure_one()
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            raise AccessError(_("El rol Usuario de consulta solo puede ver documentos."))
        import base64 as b64mod, tempfile, os
        otrosi_soporte = self.env['new_mcb_otrosi'].browse()
        if self.upload_mode and 'new_mcb_otrosi_soporte:' in self.upload_mode:
            try:
                otrosi_id = int(self.upload_mode.rsplit('new_mcb_otrosi_soporte:', 1)[1].split('|', 1)[0])
            except (ValueError, TypeError):
                raise UserError(_("Otrosí de destino inválido."))
            otrosi_soporte = self.env['new_mcb_otrosi'].browse(otrosi_id).exists()
            if not otrosi_soporte:
                raise UserError(_("No se encontró el Otrosí de destino."))
            otrosi_soporte._validar_destino_documentos_soporte(self.workspace_id)
        acta_comite_id = self.new_mcb_acta_comite_id.id
        if not acta_comite_id and self.upload_mode and 'new_mcb_acta_comite:' in self.upload_mode:
            try:
                acta_comite_id = int(self.upload_mode.rsplit('new_mcb_acta_comite:', 1)[1].split('|', 1)[0])
            except Exception:
                acta_comite_id = False
        acta_comite = self.env['new_mcb'].sudo().browse(acta_comite_id) if acta_comite_id else self.env['new_mcb'].sudo().browse()
        if not self.file_data:
            raise UserError(_("Selecciona un archivo primero."))
        if not self.workspace_id.folder_google_id:
            raise UserError(_("La carpeta seleccionada no tiene ID de Google Drive."))
        gdrive = self.browser_id._drv_get_drive()
        parent_gid = self.workspace_id.folder_google_id
        prefix = _get_next_prefix_for_folder(gdrive, parent_gid, self.workspace_id.name or '')
        base_name = self.file_name or 'documento'
        prefixed_name = f"{prefix} {base_name}" if prefix else base_name
        file_name = _drv_unique_name(prefixed_name)
        fd, tmp = tempfile.mkstemp()
        try:
            with open(tmp, 'wb') as fh:
                fh.write(b64mod.b64decode(self.file_data))
            os.close(fd)
            gfile = gdrive.CreateFile({
                'title': file_name,
                'description': self.description or '',
                'parents': [{'id': parent_gid}],
            })
            gfile.SetContentFile(tmp)
            gfile.Upload()
            google_file_id = gfile['id']

            # Crear registro drive para que action_open_file abra la ventana correctamente
            drive_rec = None
            edit_url = f"https://drive.google.com/file/d/{google_file_id}/view"
            try:
                try:
                    gfile.FetchMetadata(fields='embedLink,alternateLink,webContentLink')
                except Exception:
                    pass
                preview_url = (
                    gfile.get('embedLink')
                    or f"https://drive.google.com/file/d/{google_file_id}/preview"
                )
                edit_url = (
                    gfile.get('alternateLink')
                    or f"https://drive.google.com/file/d/{google_file_id}/view"
                )
                preview_html = (
                    f'<iframe src="{preview_url}" width="100%" height="600px"'
                    f' frameborder="0" allowfullscreen></iframe>'
                )
                edit_html = (
                    f'<iframe src="{edit_url}" width="100%" height="600px"'
                    f' frameborder="0"></iframe>'
                )
                ws = self.workspace_id
                stage_vals = self.browser_id._drv_stage_vals_for_workspace(ws)

                Drive = self.env['drive'].sudo()
                drive_rec = Drive.with_context(from_process_button=True).create({
                    'file_name': file_name,
                    'folder_id': parent_gid,
                    'file_data': self.file_data,
                    **stage_vals,
                })
                drive_rec.with_context(
                    skip_drive_sync=True, skip_integrity=True
                ).write({
                    'file_id': google_file_id,
                    'drive_file_id': google_file_id,
                    'preview_url': preview_url,
                    'edit_url': edit_url,
                    'preview_html': preview_html,
                    'edit_html': edit_html,
                    'file_data': False,
                })
            except Exception as drv_err:
                _logger.warning(
                    "No se pudo crear registro drive para '%s': %s", file_name, drv_err
                )
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        # Crear la entrada directamente en la DB (instantáneo — sin llamada a GDrive)
        self.env["drive.browser.entry"].sudo().create({
            "browser_id": self.browser_id.id,
            "parent_workspace_id": self.workspace_id.id,
            "entry_type": "file",
            "name": file_name,
            "google_id": google_file_id,
            "mime_type": gfile.get('mimeType', 'application/octet-stream'),
            "web_view_link": edit_url,
            "drive_id": drive_rec.id if drive_rec else False,
        })
        if otrosi_soporte and drive_rec:
            otrosi_soporte.write({'documentos_soporte_ids': [(4, drive_rec.id)]})
            otrosi_soporte.message_post(
                body="Documento de soporte cargado en 1. CARPETA PRINCIPAL: %s" % drive_rec.file_name
            )
        if acta_comite and acta_comite.exists() and drive_rec:
            acta_comite.write({'acta_comite_ids': [(4, drive_rec.id)]})
            acta_comite.message_post(
                body="Acta comité subida a la 1. CARPETA PRINCIPAL: %s" % drive_rec.file_name
            )
        return {'type': 'ir.actions.act_window_close'}


class DriveActaComiteMCB(models.Model):
    _inherit = 'drive'

    new_mcb_acta_comite_id = fields.Many2one(
        'new_mcb',
        string='Acta comité MCB',
        index=True,
        copy=False,
    )


def _drv_unlink_drive_records_for_google_id(env, google_id=False, extra_records=None):
    """Elimina registros drive locales asociados al mismo archivo de Google Drive."""
    Drive = env['drive'].sudo()
    records = Drive.browse()
    if extra_records:
        records |= extra_records.sudo()
    if google_id:
        records |= Drive.search([
            '|',
            ('file_id', '=', google_id),
            ('drive_file_id', '=', google_id),
        ])
    records = records.exists()
    if records:
        try:
            records.with_context(skip_drive_sync=True, skip_integrity=True).unlink()
        except Exception as _e:
            _logger.warning("drive record delete error google_id=%s: %s", google_id, _e)


def _drv_sync_remove_secondary_workspace_mirrors(env, drive_file_ids=None, folder_google_ids=None):
    """Elimina espejos en Informes/Comunicaciones cuando Soporte borra desde Drive."""
    drive_file_ids = list({rec_id for rec_id in (drive_file_ids or []) if rec_id})
    folder_google_ids = list({gid for gid in (folder_google_ids or []) if gid})
    if folder_google_ids:
        try:
            resolved_drive_file_ids = env['drive.file'].sudo().search([
                ('folder_id2', 'in', folder_google_ids),
            ]).ids
            drive_file_ids = list(set(drive_file_ids + resolved_drive_file_ids))
        except Exception as _e:
            _logger.warning("No se pudieron resolver drive.file por folder_google_id: %s", _e)

    if not drive_file_ids and not folder_google_ids:
        return

    for model_name in ('informes.workspace', 'comunicaciones.workspace'):
        try:
            Model = env[model_name].sudo()
            mirrors = Model.browse()
            if drive_file_ids and 'drive_file_id' in Model._fields:
                mirrors |= Model.search([
                    ('drive_file_id', 'in', drive_file_ids),
                ])
            if folder_google_ids and 'folder_google_id' in Model._fields:
                mirrors |= Model.search([
                    ('folder_google_id', 'in', folder_google_ids),
                ])
            if mirrors:
                if 'child_ids' in Model._fields:
                    all_mirrors = Model.browse()
                    pending = mirrors
                    seen_ids = set()
                    while pending:
                        current = pending[0]
                        pending -= current
                        if current.id in seen_ids:
                            continue
                        seen_ids.add(current.id)
                        all_mirrors |= current
                        pending |= current.child_ids.sudo()
                    mirrors = all_mirrors
                mirrors.with_context(
                    _drv_from_model_delete=True,
                    _drv_from_secondary_mirror_delete=True,
                ).unlink()
        except Exception as _e:
            _logger.warning("No se pudieron eliminar espejos %s: %s", model_name, _e)

    if not drive_file_ids:
        return

    for model_name in ('documento_archivo', 'documento_archivo_comunicaciones'):
        try:
            docs = env[model_name].sudo().search([
                ('inventario', 'in', drive_file_ids),
            ])
            if docs:
                docs.with_context(_drv_from_secondary_mirror_delete=True).unlink()
        except Exception as _e:
            _logger.warning("No se pudieron eliminar documentos espejo %s: %s", model_name, _e)


class DriveFileSyncExtension(models.Model):
    """Propaga cambios entre drive.file (drive.py) ↔ drive.browser.workspace y gestion.documental.workspace."""
    _inherit = 'drive.file'

    def write(self, vals):
        result = super().write(vals)
        if 'folder_name' in vals and not self.env.context.get('_drv_skip_sync'):
            new_name = vals['folder_name']
            for rec in self:
                if rec.folder_id2:
                    ws_list = self.env['drive.browser.workspace'].sudo().search([
                        ('folder_google_id', '=', rec.folder_id2)
                    ])
                    if ws_list:
                        ws_list.with_context(_drv_skip_sync=True).write({'name': new_name})
                    try:
                        gd_ws = self.env['gestion.documental.workspace'].sudo().with_context(gd_sync=True).search([
                            ('folder_google_id', '=', rec.folder_id2)
                        ])
                        if gd_ws:
                            gd_ws.with_context(gd_sync=True).write({'name': new_name})
                    except Exception:
                        pass
        return result

    def unlink(self):
        _drv_sync_remove_secondary_workspace_mirrors(
            self.env,
            drive_file_ids=self.ids,
            folder_google_ids=[gid for gid in self.mapped('folder_id2') if gid],
        )
        if not self.env.context.get('_drv_from_ws_delete'):
            for rec in self:
                if rec.folder_id2:
                    self.env['drive.browser.workspace'].sudo().search([
                        ('folder_google_id', '=', rec.folder_id2)
                    ]).with_context(_drv_from_df_delete=True).unlink()
                    try:
                        self.env['gestion.documental.workspace'].sudo().with_context(gd_sync=True).search([
                            ('folder_google_id', '=', rec.folder_id2)
                        ]).unlink()
                    except Exception:
                        pass
        return super().unlink()


# ---------------------------------------------------------------------------
# Sincronización bidireccional: drive_stage, otrosi_drive, cuenta_de_cobros,
# drive_folder → drive.browser.workspace + gestion.documental.workspace
# ---------------------------------------------------------------------------

def _drv_ws_propagate_name(env, ws_domain, gd_folder_id, new_name):
    """Actualiza el nombre en workspaces y gestion.documental.workspace."""
    ws_list = env['drive.browser.workspace'].sudo().search(ws_domain)
    if ws_list:
        ws_list.with_context(_drv_skip_sync=True).write({'name': new_name})
    if gd_folder_id:
        try:
            gd = env['gestion.documental.workspace'].sudo().with_context(gd_sync=True).search([
                ('folder_google_id', '=', gd_folder_id)
            ])
            if gd:
                gd.with_context(gd_sync=True).write({'name': new_name})
        except Exception:
            pass


def _drv_ws_propagate_delete(env, ws_domain, gd_folder_id):
    """Elimina workspaces y gestion.documental.workspace correspondientes."""
    ws_list = env['drive.browser.workspace'].sudo().search(ws_domain)
    _drv_sync_remove_secondary_workspace_mirrors(
        env,
        drive_file_ids=ws_list.mapped('drive_file_id').ids,
        folder_google_ids=[gid for gid in ([gd_folder_id] + ws_list.mapped('folder_google_id')) if gid],
    )
    ws_list.with_context(
        _drv_from_model_delete=True
    ).unlink()
    if gd_folder_id:
        try:
            env['gestion.documental.workspace'].sudo().with_context(gd_sync=True).search([
                ('folder_google_id', '=', gd_folder_id)
            ]).unlink()
        except Exception:
            pass


class DriveStageSync(models.Model):
    _inherit = 'drive_stage'

    def write(self, vals):
        result = super().write(vals)
        if 'folder_name' in vals and not self.env.context.get('_drv_skip_sync'):
            for rec in self:
                _drv_ws_propagate_name(
                    self.env,
                    [('drive_stage_id', '=', rec.id)],
                    rec.folder_id,
                    vals['folder_name'],
                )
        return result

    def unlink(self):
        for rec in self:
            _drv_ws_propagate_delete(
                self.env,
                [('drive_stage_id', '=', rec.id)],
                rec.folder_id,
            )
        return super().unlink()


class OtrosiDriveSync(models.Model):
    _inherit = 'otrosi_drive'

    def create(self, vals):
        record = super().create(vals)
        if not self.env.context.get('_drv_skip_sync'):
            name = record.folder_name or record.name or 'OtroSi'
            if record.drive_id and not record.is_plantilla and 'plantilla' not in name.lower():
                WS = self.env['drive.browser.workspace'].sudo()
                parent_ws_recs = WS.search([('drive_file_id', '=', record.drive_id.id)])
                for parent_ws in parent_ws_recs:
                    existing = WS.search([
                        ('otrosi_drive_id', '=', record.id),
                        ('browser_id', '=', parent_ws.browser_id.id),
                    ], limit=1)
                    if not existing:
                        WS.create({
                            'browser_id': parent_ws.browser_id.id,
                            'name': name,
                            'parent_id': parent_ws.id,
                            'otrosi_drive_id': record.id,
                            'folder_google_id': record.folder_id or False,
                        })
        return record

    def write(self, vals):
        result = super().write(vals)
        if 'folder_name' in vals and not self.env.context.get('_drv_skip_sync'):
            for rec in self:
                _drv_ws_propagate_name(
                    self.env,
                    [('otrosi_drive_id', '=', rec.id)],
                    rec.folder_id,
                    vals['folder_name'],
                )
        return result

    def unlink(self):
        for rec in self:
            _drv_ws_propagate_delete(
                self.env,
                [('otrosi_drive_id', '=', rec.id)],
                rec.folder_id,
            )
        return super().unlink()

    def action_repair_browser_nodes(self):
        """Crea nodos faltantes en drive.browser.workspace para todos los otrosi_drive sin nodo."""
        WS = self.env['drive.browser.workspace'].sudo()
        recs = self.env['otrosi_drive'].sudo().search([
            ('drive_id', '!=', False),
            ('is_plantilla', '=', False),
        ])
        created = 0
        for record in recs:
            name = record.folder_name or record.name or 'OtroSi'
            if 'plantilla' in name.lower():
                continue
            parent_ws_recs = WS.search([('drive_file_id', '=', record.drive_id.id)])
            for parent_ws in parent_ws_recs:
                existing = WS.search([
                    ('otrosi_drive_id', '=', record.id),
                    ('browser_id', '=', parent_ws.browser_id.id),
                ], limit=1)
                if not existing:
                    WS.create({
                        'browser_id': parent_ws.browser_id.id,
                        'name': name,
                        'parent_id': parent_ws.id,
                        'otrosi_drive_id': record.id,
                        'folder_google_id': record.folder_id or False,
                    })
                    created += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Reparación completada',
                'message': f'{created} nodo(s) de OtroSí creado(s) en el browser.',
                'type': 'success',
            },
        }


class CuentaDeCobrosSync(models.Model):
    _inherit = 'cuenta_de_cobros'

    def write(self, vals):
        result = super().write(vals)
        if 'folder_name' in vals and not self.env.context.get('_drv_skip_sync'):
            for rec in self:
                _drv_ws_propagate_name(
                    self.env,
                    [('cuenta_de_cobros_id', '=', rec.id)],
                    rec.folder_id,
                    vals['folder_name'],
                )
        return result

    def unlink(self):
        for rec in self:
            _drv_ws_propagate_delete(
                self.env,
                [('cuenta_de_cobros_id', '=', rec.id)],
                rec.folder_id,
            )
        return super().unlink()


class DriveFolderSync(models.Model):
    _inherit = 'drive_folder'

    def write(self, vals):
        result = super().write(vals)
        if 'folder_name' in vals and not self.env.context.get('_drv_skip_sync'):
            for rec in self:
                gd_id = rec.google_drive_id
                if gd_id:
                    try:
                        gd = self.env['gestion.documental.workspace'].sudo().with_context(gd_sync=True).search([
                            ('folder_google_id', '=', gd_id)
                        ])
                        if gd:
                            gd.with_context(gd_sync=True).write({'name': vals['folder_name']})
                    except Exception:
                        pass
        return result

    def unlink(self):
        for rec in self:
            gd_id = rec.google_drive_id
            if gd_id:
                try:
                    self.env['gestion.documental.workspace'].sudo().with_context(gd_sync=True).search([
                        ('folder_google_id', '=', gd_id)
                    ]).unlink()
                except Exception:
                    pass
        return super().unlink()

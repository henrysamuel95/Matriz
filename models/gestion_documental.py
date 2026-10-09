import json
from .drive import GoogleDriveAuth
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
import io
import os
import base64
import hashlib
import re as _re
from copy import copy
from odoo.modules.module import get_module_resource
import logging
from .decorador_firmas import seguimiento_firmas

_logger = logging.getLogger(__name__)

_FOLDER_RENAME_MAP = {
    "CARPETA PRINCIPAL": "1. CARPETA PRINCIPAL",
    "COMUNICACIONES":    "2. COMUNICACIONES",
    "CONTRATOS PS":      "3. CONTRATOS PS",
    "INFORMES":          "4. INFORMES",
}
_FOLDER_HIDDEN = {"ACTAS"}
_HOJA_CONTROL_BASE_NAME = "Hoja de control.xlsx"
_GD_SKIP_INVENTARIO_SYNC = "skip_gd_inventario_sync"


def _gd_extract_numeric_prefix(name):
    match = _re.match(r'^(\d+(?:\.\d+)*)', (name or '').strip())
    return match.group(1) if match else ''


def _gd_strip_numeric_prefix(name):
    return _re.sub(r'^\d+(?:\.\d+)*(?:\s+|_)?', '', (name or '').strip())


def _gd_is_hoja_control_name(name):
    clean = _gd_strip_numeric_prefix(name).lower()
    return clean in (
        _HOJA_CONTROL_BASE_NAME.lower(),
        "hoja_control.xlsx",
    ) or clean.startswith("hoja_control_")

try:
    from openpyxl import load_workbook
    from openpyxl.cell.cell import MergedCell 
except ImportError:
    load_workbook = None
    MergedCell = None


class GestionDocumentalEntry(models.Model):
    _name = "gestion.documental.entry"
    _description = "Gestión Documental - Entry (folder/file)"
    _order = "entry_type desc, name asc"  # folders arriba, luego archivos

    browser_id = fields.Many2one(
        "gestion.documental.browser",
        required=True,
        ondelete="cascade",
        index=True,
    )
    parent_workspace_id = fields.Many2one(
        "gestion.documental.workspace",
        string="Carpeta (contenedor)",
        required=True,
        ondelete="cascade",
        index=True,
    )

    entry_type = fields.Selection(
        [("folder", "Folder"), ("file", "File")],
        required=True,
        index=True,
    )
    name = fields.Char(required=True, index=True)

    # Si es carpeta
    target_workspace_id = fields.Many2one(
        "gestion.documental.workspace",
        string="Ir a carpeta",
        ondelete="cascade",
        index=True,
    )

    preview_html = fields.Text("Preview HTML", readonly=True, related="drive_id.preview_html")

    # Si es archivo (tu modelo actual)
    drive_id = fields.Many2one(
        "drive",
        string="Documento",
        ondelete="cascade",
        index=True,
    )
    google_id = fields.Char(required=True, index=True)
    fch_creacion = fields.Datetime(
            related='drive_id.fch_creacion', 
            string="Fecha Creación", 
            readonly=True
        )    
    user_id = fields.Many2one('res.users', related='drive_id.user_id', string="Usuario", readonly=True)
    show_on_dashboard = fields.Boolean(related='drive_id.show_on_dashboard', readonly=True)
    mime_type = fields.Char()
    icon_mime_type = fields.Char(compute="_compute_icon_mime_type", store=False)
    web_view_link = fields.Char()
    thumbnail_link = fields.Char()
    parent_workspace_name = fields.Char(related='parent_workspace_id.name', string="En Carpeta", readonly=True)

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

    def action_reportar_archivo_kanban(self):
        self.ensure_one()
        # Llamamos a nuestra nueva función para la ruta completa
        ruta_carpeta = self.parent_workspace_id.get_full_path_minus_root()
        
        wizard = self.env['gestion.documental.report.wizard'].create({
            'entry_id': self.id,
            'project_id': self.parent_workspace_id.project_id.id,
            'workspace_name': ruta_carpeta,
        })
        return {
            'name': f'Reportar Archivo: {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'gestion.documental.report.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }
    
    def action_view_details(self):
        """
        Acción llamada desde el botón 'Ver Detalles' del Kanban.
        Actualiza el browser para mostrar el panel derecho y refresca la vista suavemente.
        """
        self.ensure_one()
        
        # 1. Actualizamos los datos en el modelo padre (Browser)
        self.browser_id.write({
            'selected_entry_id': self.id,
            'show_details_panel': True
        })

        # 2. ESTRATEGIA SIN RELOAD:
        # Devolvemos una acción que "vuelve a abrir" el mismo registro del browser.
        # Odoo detecta que es el mismo ID y refresca los campos y los 'attrs' (invisibles)
        # sin hacer un refresh completo del navegador.
        return {
            'type': 'ir.actions.client',
            'tag': 'soft_reload'
        }

    # Solo lectura como tu enfoque actual
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("gd_sync"):
            raise AccessError(_("Gestión Documental es de solo lectura."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("gd_sync"):
            raise AccessError(_("Gestión Documental es de solo lectura."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("gd_sync"):
            raise AccessError(_("Gestión Documental es de solo lectura."))
        return super().unlink()

    def action_open_folder(self):
        """Click en una tarjeta carpeta -> navegar a esa carpeta."""
        self.ensure_one()
        if not self.exists():
            return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        if self.entry_type != "folder" or not self.target_workspace_id:
            return False

        browser = self.browser_id.with_context(gd_sync=True)
        # Sincronizamos la selección en el árbol lateral
        browser.workspace_tree_ids.with_context(gd_sync=True).write({"is_selected": False})
        self.target_workspace_id.with_context(gd_sync=True).write({"is_selected": True})

        # Actualizamos la carpeta actual
        browser.write({
            "selected_workspace_id": self.target_workspace_id.id,
            "show_details_panel": True,
            "sel_entry_type": 'folder',
        })
        browser._gd_refresh_entries()
        
        # ALERTA: No devolvemos act_window para evitar duplicar la ruta arriba
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
    
    def action_download_pdf_entry(self):
        self.ensure_one()
        drive_rec = self._gd_get_or_create_drive_record()
        if not drive_rec:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))
        return drive_rec.download_from_drive()

    def action_download_word_entry(self):
        self.ensure_one()
        drive_rec = self._gd_get_or_create_drive_record()
        if not drive_rec:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))
        return drive_rec.download_from_drive_as_word()

    def action_download_original_entry(self):
        self.ensure_one()
        drive_rec = self._gd_get_or_create_drive_record()
        if not drive_rec:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))
        return drive_rec.download_from_drive_original()

    def action_open_file(self):
        self.ensure_one()
        if self.entry_type != "file":
            return False

        drive_rec = self._gd_get_or_create_drive_record()
        if not drive_rec:
            raise UserError(_("Este archivo no tiene un documento Drive asociado."))

        # Referencia a la vista de formulario de drive
        view_id = self.env.ref('Matriz.view_drive_form').id

        return {
            'name': self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'res_id': drive_rec.id,
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',  # Esto hace que se abra como wizard/popup
            'context': {
                'create': False,
                'edit': True, 
            }
        }

    def _gd_get_or_create_drive_record(self):
        self.ensure_one()
        if self.drive_id:
            return self.drive_id
        if self.entry_type != "file" or not self.google_id:
            return self.env["drive"]

        Drive = self.env["drive"].sudo()
        domain = ["|", ("drive_file_id", "=", self.google_id), ("file_id", "=", self.google_id)]
        drive_rec = Drive.search(domain, limit=1)
        if not drive_rec:
            drive_rec = self._gd_create_drive_record_from_google_file()

        if drive_rec:
            vals = {"drive_id": drive_rec.id}
            if not self.web_view_link and drive_rec.file_url:
                vals["web_view_link"] = drive_rec.file_url
            self.sudo().with_context(gd_sync=True).write(vals)
        return drive_rec

    def _gd_create_drive_record_from_google_file(self):
        self.ensure_one()
        if not self.google_id:
            return self.env["drive"]

        title = self.name or "Sin nombre"
        mime = self.mime_type
        link = self.web_view_link
        preview_url = link

        try:
            gdrive = self.browser_id._gd_get_drive()
            gfile = gdrive.CreateFile({"id": self.google_id})
            gfile.FetchMetadata()
            title = gfile.get("title") or gfile.get("name") or title
            mime = gfile.get("mimeType") or mime
            link = gfile.get("alternateLink") or gfile.get("webViewLink") or link
            preview_url = gfile.get("embedLink") or link
        except Exception as exc:
            _logger.warning(
                "No se pudo leer metadata de Drive para gestion.documental.entry %s: %s",
                self.id,
                exc,
            )

        drive_vals = {
            "file_name": title,
            "drive_file_id": self.google_id,
            "file_id": self.google_id,
            "folder_id": self.parent_workspace_id.folder_google_id,
            "file_url": link,
            "fecha": fields.Date.today(),
            "user_id": self.env.user.id,
            "page_total": 0,
            "page_range": False,
        }
        if preview_url:
            drive_vals["preview_html"] = (
                f'<iframe src="{preview_url}" width="100%" height="600px" '
                f'loading="lazy" frameborder="0" allowfullscreen></iframe>'
            )
        if self.browser_id.project_id:
            drive_vals["proyecto_relacionado"] = self.browser_id.project_id.id

        drive_rec = self.env["drive"].sudo().create(drive_vals)
        entry_vals = {
            "name": title,
            "mime_type": mime,
            "web_view_link": link,
        }
        self.sudo().with_context(gd_sync=True).write(entry_vals)
        return drive_rec

class GestionDocumentalWorkspace(models.Model):
    _name = "gestion.documental.workspace"
    _description = "Workspace Gestión Documental (solo lectura)"
    _parent_name = "parent_id"
    _parent_store = True
    _order = "parent_path, name"

    name = fields.Char(required=True)

    browser_id = fields.Many2one(
        "gestion.documental.browser",
        required=True,
        ondelete="cascade",
        index=True,
    )
    project_id = fields.Many2one(
        related="browser_id.project_id",
        store=True,
        readonly=True,
        index=True,
    )

    parent_id = fields.Many2one("gestion.documental.workspace", ondelete="cascade", index=True)
    child_ids = fields.One2many("gestion.documental.workspace", "parent_id")
    parent_path = fields.Char(index=True)

    is_selected = fields.Boolean(default=False)

    depth = fields.Integer(compute='_compute_depth', store=False)

    @api.depends('parent_path')
    def _compute_depth(self):
        for rec in self:
            if rec.parent_path:
                rec.depth = len([p for p in rec.parent_path.strip('/').split('/') if p]) - 1
            else:
                rec.depth = 0

    # Ya NO dependemos de drive.file para construir el árbol
    drive_file_id = fields.Many2one("drive.file", ondelete="set null", index=True)

    # IDs reales de Google Drive (carpetas)
    folder_google_id = fields.Char(string="Google Folder ID", required=True, index=True)
    parent_folder_google_id = fields.Char(string="Google Parent Folder ID", index=True)

    _sql_constraints = [
        ("gd_ws_unique_folder_in_browser",
        "unique(browser_id, folder_google_id)",
        "La carpeta ya existe en este workspace."),
    ]

    def get_full_path_minus_root(self):
        """
        Devuelve la ruta completa de la carpeta omitiendo la carpeta raíz (el proyecto).
        Ejemplo: Si la ruta es "Proyecto X / Contratos PS / Teylor", devuelve "Contratos PS / Teylor".
        """
        self.ensure_one()
        if not self.parent_path:
            return self.name
        
        # Extraemos los IDs de la ruta (Ej: "1/4/9/" -> [1, 4, 9])
        ids = [int(x) for x in self.parent_path.split("/") if x.isdigit()]
        
        # Buscamos las carpetas en la base de datos
        crumbs = self.env["gestion.documental.workspace"].browse(ids).exists()
        
        # Aseguramos el orden correcto de padre a hijo
        idpos = {id_val: pos for pos, id_val in enumerate(ids)}
        crumbs = crumbs.sorted(key=lambda w: idpos.get(w.id, 100))
        
        nombres = crumbs.mapped('name')
        
        # Si hay más de un nivel, omitimos el primero (que es el proyecto raíz)
        if len(nombres) > 1:
            return " / ".join(nombres[1:])
        elif len(nombres) == 1:
            return nombres[0]
        
        return self.name

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("gd_sync"):
            raise AccessError(_("Gestión Documental es de solo lectura."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("gd_sync"):
            raise AccessError(_("Gestión Documental es de solo lectura."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("gd_sync"):
            raise AccessError(_("Gestión Documental es de solo lectura."))
        return super().unlink()

    def action_select_workspace(self):
            """Click en el árbol de la izquierda."""
            self.ensure_one()
            browser = self.browser_id.with_context(gd_sync=True)

            browser.workspace_tree_ids.with_context(gd_sync=True).write({"is_selected": False})
            self.with_context(gd_sync=True).write({"is_selected": True})

            browser.write({
                "selected_workspace_id": self.id,
                "selected_entry_id": False,
                "show_details_panel": True,
                "sel_entry_type": 'folder'
            })

            browser._gd_refresh_entries()

            # Refrescamos sin añadir un nuevo ítem al breadcrumb
            return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_show_folder_info(self):
        """Botón ℹ : abre el panel de información de esta carpeta."""
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
            "name": "Gestión Documental",
            "res_model": "gestion.documental.browser",
            "view_mode": "form",
            "res_id": browser.id,
            "target": "current",
            "flags": {"clear_breadcrumbs": True},
        }

class GestionDocumentalBrowser(models.Model):
    _name = "gestion.documental.browser"
    _description = "Gestión Documental - Browser (solo lectura)"
    _rec_name = "current_folder_display"

    current_folder_display = fields.Char(
        compute="_compute_current_folder_display",
        store=True,
        readonly=True,
    )

    project_id = fields.Many2one("contratacion", required=True, ondelete="cascade", index=True)

    workspace_tree_ids = fields.One2many(
        "gestion.documental.workspace",
        "browser_id",
        string="Workspace"
    )

    workspace_search = fields.Char(string="Buscar carpeta")

    workspace_view_ids = fields.Many2many(
        "gestion.documental.workspace",
        string="Workspace (filtrado)",
        compute="_compute_workspace_view",
        store=False,
    )

    workspace_ids = fields.One2many(
    "gestion.documental.workspace",
    "browser_id",
    string="Workspace Nodes",
    )

    selected_workspace_id = fields.Many2one(
        "gestion.documental.workspace",
        string="Carpeta seleccionada",
    )

    breadcrumb_json = fields.Text(compute="_compute_breadcrumb_json")
    search_query = fields.Char(string="Buscar documentos")

    show_details_panel = fields.Boolean(default=False)
    entry_ids = fields.One2many(
        "gestion.documental.entry",
        "browser_id",
        string="Contenido",
    )

    entry_view_ids = fields.Many2many(
        "gestion.documental.entry",
        string="Contenido (filtrado)",
        compute="_compute_entry_view",
        store=False,
    )

    folder_view_ids = fields.Many2many(
        "gestion.documental.entry",
        relation="gd_browser_folder_view_rel",
        column1="browser_id", column2="entry_id",
        string="Carpetas (filtradas)",
        compute="_compute_folder_file_views",
        store=False,
    )
    file_view_ids = fields.Many2many(
        "gestion.documental.entry",
        relation="gd_browser_file_view_rel",
        column1="browser_id", column2="entry_id",
        string="Archivos (filtrados)",
        compute="_compute_folder_file_views",
        store=False,
    )
    sel_has_files = fields.Boolean(compute="_compute_sel_has_files", store=False)
    sel_selected_folder_has_files = fields.Boolean(
        compute="_compute_sel_selected_folder_has_files",
        store=False,
    )

    @api.depends("file_view_ids")
    def _compute_sel_has_files(self):
        for rec in self:
            rec.sel_has_files = bool(rec.file_view_ids)

    @api.depends("selected_workspace_id")
    def _compute_sel_selected_folder_has_files(self):
        Entry = self.env["gestion.documental.entry"]
        for rec in self:
            rec.sel_selected_folder_has_files = bool(
                rec.selected_workspace_id and Entry.search_count([
                    ("browser_id", "=", rec.id),
                    ("parent_workspace_id", "=", rec.selected_workspace_id.id),
                    ("entry_type", "=", "file"),
                ])
            )

    selected_entry_id = fields.Many2one(
        "gestion.documental.entry",
        string="Entry seleccionado",
    )
    sel_preview_html = fields.Text(
        related='selected_entry_id.drive_id.preview_html', 
        readonly=True
    )
    sel_name = fields.Char(
        related='selected_entry_id.name', 
        readonly=True
    )
    # Aquí traemos los FOLIOS (page_range) desde Drive
    sel_folios = fields.Char(
        related='selected_entry_id.drive_id.page_range', 
        string="Folios", 
        readonly=True
    )
    sel_page_total = fields.Integer(
        related='selected_entry_id.drive_id.page_total',
        string="N° páginas",
        readonly=True,
    )
    sel_user_id = fields.Many2one(
        related='selected_entry_id.drive_id.user_id', 
        readonly=True
    )
    sel_fecha = fields.Date(
        related='selected_entry_id.drive_id.fecha', 
        string="Fecha",
        readonly=True
    )
    sel_description = fields.Text(
        related='selected_entry_id.drive_id.descrpcion_info', 
        string="Descripción", 
        readonly=True
    )

    sel_parent_folder_name = fields.Char(
        related='selected_entry_id.parent_workspace_id.name',
        readonly=True
    )
    sel_create_date = fields.Datetime(
        related='selected_entry_id.drive_id.create_date',
        string="Fecha de creación", readonly=True
    )

    sel_folder_name = fields.Char(
        compute='_compute_sel_folder_info', 
        readonly=True
    )
    sel_folder_user_id = fields.Many2one(
        'res.users',
        compute='_compute_sel_folder_info', 
        readonly=True
    )
    sel_folder_fecha = fields.Date(
        compute='_compute_sel_folder_info', 
        readonly=True
    )
    sel_folder_description = fields.Text(
        compute='_compute_sel_folder_info', 
        readonly=True
    )
    
    sel_entry_type = fields.Selection(
        related='selected_entry_id.entry_type',
        readonly=True
    )

    sel_workspace_depth = fields.Integer(
        compute='_compute_sel_workspace_depth',
        string="Profundidad Carpeta",
        store=False,
    )

    @api.depends(
        'selected_entry_id',
        'selected_entry_id.entry_type',
        'selected_entry_id.target_workspace_id',
        'selected_entry_id.target_workspace_id.parent_id',
        'selected_entry_id.target_workspace_id.parent_id.parent_id',
        'selected_workspace_id',
        'selected_workspace_id.parent_id',
        'selected_workspace_id.parent_id.parent_id',
    )
    def _compute_sel_workspace_depth(self):
        for rec in self:
            ws = (
                rec.selected_entry_id.target_workspace_id
                if rec.selected_entry_id
                and rec.selected_entry_id.entry_type == 'folder'
                and rec.selected_entry_id.target_workspace_id
                else rec.selected_workspace_id
            )
            if not ws:
                rec.sel_workspace_depth = -1
            elif not ws.parent_id:
                rec.sel_workspace_depth = 0   # carpeta raíz del proyecto → Inventario Documental
            elif not ws.parent_id.parent_id:
                rec.sel_workspace_depth = 1   # carpeta de contrato → sin botón
            else:
                rec.sel_workspace_depth = 2   # subcarpetas tipo documental → Hoja de Control

    estado_sync_gd = fields.Selection(
        [('sincronizando', 'Sincronizando'), ('completado', 'Completado')],
        string="Estado Sync GD",
        default='completado',
    )

    @api.model
    def action_navigate_breadcrumb_js(self, browser_id, workspace_id):
        """
        Recibe el ID del browser y el ID de la carpeta desde el Javascript,
        y actualiza la vista para navegar hacia allá.
        """
        browser = self.browse(browser_id)
        if browser.exists():
            browser.selected_workspace_id = workspace_id
        return True
    
    @api.depends('selected_entry_id', 'selected_workspace_id')
    def _compute_sel_folder_info(self):
        # 1. Detectar dinámicamente el campo de Google ID en el modelo 'drive'
        Drive = self.env['drive'].sudo()
        drive_field_name = 'file_id' # Por defecto
        if 'id_drive' in Drive._fields:
            drive_field_name = 'id_drive'
        elif 'google_id' in Drive._fields:
            drive_field_name = 'google_id'

        for rec in self:
            # Inicializar vacíos
            rec.sel_folder_name = ""
            rec.sel_folder_user_id = False
            rec.sel_folder_fecha = False
            rec.sel_folder_description = ""

            # CASO 1: Selección desde KANBAN (Click en tarjeta carpeta)
            if rec.selected_entry_id and rec.selected_entry_id.entry_type == 'folder':
                rec.sel_folder_name = rec.selected_entry_id.name
                if rec.selected_entry_id.drive_id:
                    rec.sel_folder_user_id = rec.selected_entry_id.drive_id.user_id
                    rec.sel_folder_fecha = rec.selected_entry_id.drive_id.fecha
                    rec.sel_folder_description = rec.selected_entry_id.drive_id.descrpcion_info
            
            # CASO 2: Selección desde ÁRBOL (Click en barra lateral)
            elif rec.selected_workspace_id:
                ws = rec.selected_workspace_id
                rec.sel_folder_name = ws.name
                
                # Buscamos la info en drive usando el ID de Google de la carpeta
                if ws.folder_google_id:
                    drive_rec = Drive.search([(drive_field_name, "=", ws.folder_google_id)], limit=1)
                    if drive_rec:
                        rec.sel_folder_user_id = drive_rec.user_id
                        rec.sel_folder_fecha = drive_rec.fecha
                        rec.sel_folder_description = drive_rec.descrpcion_info
                    else:
                        # Fallback si no está sincronizado en tabla drive
                        rec.sel_folder_user_id = self.env.user
                        rec.sel_folder_fecha = fields.Date.today()

    def action_open_inventario_documental(self):
        self.ensure_one()
        if not self.selected_workspace_id:
            raise UserError(_("Debes seleccionar una carpeta antes de abrir el Inventario Documental."))

        workspace = (
            self.selected_entry_id.target_workspace_id
            if self.selected_entry_id
            and self.selected_entry_id.entry_type == 'folder'
            and self.selected_entry_id.target_workspace_id
            else self.selected_workspace_id
        )
        inventario = self.env['inventario.documental'].search([
            ('workspace_id', '=', workspace.id),
        ], limit=1)

        if not inventario:
            inventario = self.env['inventario.documental'].create({
                'workspace_id': workspace.id,
            })
        _logger.info(
            "INVENTARIO DEBUG | abrir inventario desde GD | browser=%s workspace=%s/%s selected_entry=%s/%s",
            self.id,
            workspace.id,
            workspace.display_name,
            self.selected_entry_id.id if self.selected_entry_id else False,
            self.selected_entry_id.display_name if self.selected_entry_id else False,
        )
        inventario._refresh_lineas_desde_carpetas_sin_subir()

        return {
            'name': 'Inventario Documental',
            'type': 'ir.actions.act_window',
            'res_model': 'inventario.documental',
            'view_mode': 'form',
            'res_id': inventario.id,
            'target': 'current',
        }

    def action_open_hoja_control(self):
        self.ensure_one()
        if not self.selected_workspace_id:
            raise UserError(_("Debes seleccionar una carpeta antes de abrir la Hoja de Control."))

        workspace = self.selected_workspace_id
        
        documento = self.env['documento_archivo_gd'].search([
            ('workspace_id', '=', workspace.id),
        ], limit=1)

        # Detectar carpeta principal (profundidad 1) y carpeta de contratista (profundidad 2)
        ids_ruta = [int(x) for x in (workspace.parent_path or "").split("/") if x.isdigit()]
        Workspace = self.env["gestion.documental.workspace"]

        main_folder_name = ""
        if len(ids_ruta) >= 2:
            main_ws = Workspace.browse(ids_ruta[1])
            if main_ws.exists():
                main_folder_name = main_ws.name
        elif len(ids_ruta) == 1:
            main_folder_name = workspace.name

        # Buscar el contrataciones_fuv correcto por Google folder ID del contratista (profundidad 2)
        fuv = self.env['contrataciones_fuv']
        contractor_google_id = ""
        if len(ids_ruta) >= 3:
            contractor_ws = Workspace.browse(ids_ruta[2])
            if contractor_ws.exists():
                contractor_google_id = contractor_ws.folder_google_id
        if contractor_google_id:
            fuv = self.env['contrataciones_fuv'].search([
                ('proyecto_relacionado', '=', self.project_id.id),
                ('drive_file_id.folder_id2', '=', contractor_google_id),
            ], limit=1)
        if not fuv:
            fuv = self.project_id.contratacion_ids[:1]

        # seccion_subseccion: "Dirección ejecutiva / {área}"
        seccion_subseccion = workspace.name
        if fuv and fuv.dependencia_area_solicitante:
            sel = dict(self.env['contrataciones_fuv']._fields['dependencia_area_solicitante'].selection)
            area_label = sel.get(fuv.dependencia_area_solicitante, fuv.dependencia_area_solicitante)
            seccion_subseccion = f"Dirección ejecutiva / {area_label}"

        # serie_subserie: según tipo de carpeta principal
        mfn_upper = main_folder_name.upper()
        serie_subserie = ""
        if "CONTRATOS" in mfn_upper:
            tipo = fuv.tipo_contrato_id.name if fuv and fuv.tipo_contrato_id else ""
            serie_subserie = f"Contrato/ {tipo}" if tipo else "Contrato"
        elif "COMUNICACIONES" in mfn_upper:
            serie_subserie = "Comunicaciones/ Comunicación Oficial"
        elif "INFORMES" in mfn_upper:
            serie_subserie = "Informes/ Informe de Interventoría"
        elif "CARPETA PRINCIPAL" in mfn_upper:
            serie_subserie = "Carpeta Principal"

        if not documento:
            documento = self.env['documento_archivo_gd'].create({
                'workspace_id': workspace.id,
                'fecha': fields.Date.today(),
                'seccion_subseccion': seccion_subseccion,
                'serie_subserie': serie_subserie,
            })
        else:
            vals_actualizar = {}
            if not documento.seccion_subseccion:
                vals_actualizar['seccion_subseccion'] = seccion_subseccion
            # serie_subserie siempre se recalcula para reflejar el contrato correcto
            if serie_subserie:
                vals_actualizar['serie_subserie'] = serie_subserie
            if vals_actualizar:
                documento.write(vals_actualizar)

        documento.linea_ids.with_context(**{_GD_SKIP_INVENTARIO_SYNC: True}).unlink()

        lineas_a_crear = []
        folio_actual = 1

        archivos = self.env['gestion.documental.entry'].search([
            ('browser_id', '=', self.id),
            ('parent_workspace_id', '=', workspace.id),
            ('entry_type', '=', 'file')
        ], order='name asc')

        for archivo in archivos:
            if _gd_is_hoja_control_name(archivo.name):
                continue
            paginas = 0
            page_range = archivo.drive_id.page_range if archivo.drive_id else False

            if archivo.drive_id and archivo.drive_id.page_total:
                paginas = archivo.drive_id.page_total
            elif page_range:
                if '-' in page_range:
                    try:
                        ini, fin = page_range.split('-')
                        if ini.isdigit() and fin.isdigit():
                            paginas = int(fin) - int(ini) + 1
                    except:
                        paginas = 0
                elif page_range.isdigit():
                    paginas = int(page_range)

            folio_inicio = folio_actual
            if paginas > 0:
                folio_fin = folio_inicio + paginas - 1
                folios_conteo_val = f"{folio_inicio}-{folio_fin}"
                folio_actual = folio_fin + 1
            else:
                folios_conteo_val = "0"

            fecha_archivo = archivo.drive_id.fecha if archivo.drive_id and archivo.drive_id.fecha else fields.Date.today()

            lineas_a_crear.append({
                'documento_id': documento.id,
                'dia': str(fecha_archivo.day).zfill(2),
                'mes': fecha_archivo.month,
                'anio': fecha_archivo.year,
                'tipo_documental': archivo.name or "Documento sin nombre",
                'numero_consecutivo': folios_conteo_val,
                'folios': str(paginas) if paginas > 0 else '',
                'folios_conteo': folios_conteo_val,
            })

        if lineas_a_crear:
            self.env['documento_archivo_gd_linea'].with_context(**{_GD_SKIP_INVENTARIO_SYNC: True}).create(lineas_a_crear)

        try:
            documento.with_context(
                gd_auto_calculate_folios=True,
                **{_GD_SKIP_INVENTARIO_SYNC: True},
            ).action_calcular_folios()
            documento._sincronizar_excel_si_cambio()
        except Exception as exc:
            _logger.warning(
                "No se pudo actualizar automáticamente la hoja de control %s en Drive: %s",
                documento.id,
                exc,
            )

        return {
            'name': 'Hoja de control',
            'type': 'ir.actions.act_window',
            'res_model': 'documento_archivo_gd',
            'view_mode': 'form',
            'res_id': documento.id,
            'target': 'current',
        }

    def action_open_folder_edit(self):
        self.ensure_one()
        if self.selected_entry_id and self.selected_entry_id.drive_id:
            return {
                'name': 'Editar Carpeta',
                'type': 'ir.actions.act_window',
                'res_model': 'drive',
                'res_id': self.selected_entry_id.drive_id.id,
                'view_mode': 'form',
                'view_id': self.env.ref('Matriz.view_drive_form').id,
                'target': 'new',
            }
        return False

    def action_close_details_panel(self):
        self.write({
            'show_details_panel': False,
        })
        return True

    @api.onchange("workspace_search")
    def _onchange_workspace_search(self):
        for rec in self:
            browser_id = rec._origin.id or rec.id
            if not browser_id:
                rec.workspace_view_ids = [(5, 0, 0)]
                return

            if not rec.workspace_search:
                rec.workspace_view_ids = rec.workspace_tree_ids
                continue

            matched = self.env["gestion.documental.workspace"].search([
                ("browser_id", "=", browser_id),
                ("name", "ilike", rec.workspace_search),
            ])

            if not matched:
                rec.workspace_view_ids = [(5, 0, 0)]
                continue

            visible_ids = set(matched.ids)
            for f in matched:
                parent = f.parent_id
                while parent:
                    if parent.id in visible_ids:
                        break
                    visible_ids.add(parent.id)
                    parent = parent.parent_id

            rec.workspace_view_ids = [(6, 0, list(visible_ids))]

    @api.onchange("search_query", "selected_workspace_id")
    def _onchange_search_query(self):
        Entry = self.env["gestion.documental.entry"]
        for rec in self:
            browser_id = rec._origin.id or rec.id
            if not browser_id or not rec.selected_workspace_id:
                rec.entry_view_ids = [(5, 0, 0)]
                rec.folder_view_ids = [(5, 0, 0)]
                rec.file_view_ids = [(5, 0, 0)]
                continue

            dom = [
                ("browser_id", "=", browser_id),
                ("parent_workspace_id", "=", rec.selected_workspace_id.id),
            ]
            if rec.search_query:
                dom.append(("name", "ilike", rec.search_query))

            all_entries = Entry.search(dom)
            rec.entry_view_ids = [(6, 0, all_entries.ids)]
            rec.folder_view_ids = [(6, 0, all_entries.filtered(lambda e: e.entry_type == "folder").ids)]
            rec.file_view_ids = [(6, 0, all_entries.filtered(lambda e: e.entry_type == "file").ids)]
            
    @api.depends("selected_workspace_id", "selected_workspace_id.parent_path", "selected_workspace_id.name")
    def _compute_breadcrumb_json(self):
        Workspace = self.env["gestion.documental.workspace"]
        for rec in self:
            ws = rec.selected_workspace_id
            if not ws:
                rec.breadcrumb_json = "[]"
                continue
            ids = [int(x) for x in (ws.parent_path or "").split("/") if x.isdigit()]
            nodes = Workspace.browse(ids).exists()
            idpos = {i: p for p, i in enumerate(ids)}
            nodes = nodes.sorted(key=lambda w: idpos.get(w.id, 10**9))
            rec.breadcrumb_json = json.dumps([
                {"id": w.id, "name": w.name}
                for w in nodes
            ])

    def action_select_workspace(self):
        self.ensure_one()
        browser = self.browser_id.with_context(gd_sync=True)

        if browser.selected_workspace_id == self:
            return True

        browser.workspace_tree_ids.with_context(gd_sync=True).write({"is_selected": False})
        self.with_context(gd_sync=True).write({"is_selected": True})

        browser.write({
            "selected_workspace_id": self.id,
            "selected_entry_id": False,
            "show_details_panel": True,
            "sel_entry_type": 'folder' # Forzamos tipo carpeta para el attrs del XML
        })
        
        # IMPORTANTE: Forzamos el cálculo de la info de carpeta
        browser._compute_sel_folder_info()
        browser._gd_refresh_entries()

        return {
            "type": "ir.actions.act_window",
            "name": "Gestión Documental", 
            "res_model": "gestion.documental.browser",
            "view_mode": "form",
            "res_id": browser.id,
            "target": "current",
            "flags": {"clear_breadcrumbs": True},
        }
        
    @api.depends("selected_workspace_id", "selected_workspace_id.name", "selected_workspace_id.parent_path")
    def _compute_current_folder_display(self):
        for rec in self:
            rec.current_folder_display = rec._get_workspace_path(rec.selected_workspace_id) or "Sub carpetas"

    def name_get(self):
        return [(rec.id, rec.current_folder_display or _("Gestión Documental")) for rec in self]

    def _get_workspace_path(self, workspace):
        if not workspace:
            return False
        ids = [int(x) for x in (workspace.parent_path or "").split("/") if x.isdigit()]
        id_pos = {wid: pos for pos, wid in enumerate(ids)}
        crumbs = self.env["gestion.documental.workspace"].browse(ids).exists()
        crumbs = crumbs.sorted(key=lambda w: id_pos.get(w.id, 999))
        names = [w.name for w in crumbs]
        return " / ".join(names)


    def _action_open_self(self, title):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": title, 
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": dict(self.env.context),
        }

    @api.depends("selected_workspace_id", "search_query", "entry_ids")
    def _compute_entry_view(self):
        for rec in self:
            if not rec.selected_workspace_id:
                rec.entry_view_ids = [(5, 0, 0)]
                continue
            rec._gd_ensure_child_folder_entries()

            dom = [
                ("browser_id", "=", rec.id),
                ("parent_workspace_id", "=", rec.selected_workspace_id.id),
            ]
            if rec.search_query:
                dom.append(("name", "ilike", rec.search_query))

            rec.entry_view_ids = [(6, 0, self.env["gestion.documental.entry"].search(dom).ids)]

    @api.depends("selected_workspace_id", "search_query", "entry_ids")
    def _compute_folder_file_views(self):
        Entry = self.env["gestion.documental.entry"]
        for rec in self:
            if not rec.selected_workspace_id:
                rec.folder_view_ids = [(5, 0, 0)]
                rec.file_view_ids = [(5, 0, 0)]
                continue
            rec._gd_ensure_child_folder_entries()
            base = [
                ("browser_id", "=", rec.id),
                ("parent_workspace_id", "=", rec.selected_workspace_id.id),
            ]
            if rec.search_query:
                base.append(("name", "ilike", rec.search_query))
            rec.folder_view_ids = [(6, 0, Entry.search(base + [("entry_type", "=", "folder")]).ids)]
            rec.file_view_ids   = [(6, 0, Entry.search(base + [("entry_type", "=", "file")]).ids)]

    def _gd_ensure_child_folder_entries(self):
        self.ensure_one()
        ws = self.selected_workspace_id
        if not ws:
            return

        Entry = self.env["gestion.documental.entry"].sudo().with_context(gd_sync=True)
        existing = Entry.search([
            ("browser_id", "=", self.id),
            ("parent_workspace_id", "=", ws.id),
            ("entry_type", "=", "folder"),
        ])
        existing_by_ws = {entry.target_workspace_id.id: entry for entry in existing if entry.target_workspace_id}
        existing_by_gid = {entry.google_id: entry for entry in existing if entry.google_id}

        vals = []
        for child in ws.child_ids:
            if not child.folder_google_id:
                continue
            if child.id in existing_by_ws or child.folder_google_id in existing_by_gid:
                continue
            vals.append({
                "browser_id": self.id,
                "parent_workspace_id": ws.id,
                "entry_type": "folder",
                "name": child.name,
                "google_id": child.folder_google_id,
                "mime_type": "application/vnd.google-apps.folder",
                "target_workspace_id": child.id,
            })
        if vals:
            Entry.create(vals)

    def _gd_refresh_entries(self):
        self.ensure_one()
        if not self.selected_workspace_id or not self.selected_workspace_id.folder_google_id:
            return

        Entry = self.env["gestion.documental.entry"].sudo().with_context(gd_sync=True)
        Drive = self.env["drive"].sudo()

        Entry.search([
            ("browser_id", "=", self.id),
            ("parent_workspace_id", "=", self.selected_workspace_id.id),
        ]).unlink()

        children = self._gd_list_children(self.selected_workspace_id.folder_google_id)
        folder_mime = "application/vnd.google-apps.folder"

        Workspace = self.env["gestion.documental.workspace"].sudo().with_context(gd_sync=True)
        existing = {w.folder_google_id: w for w in self.selected_workspace_id.child_ids if w.folder_google_id}

        drive_field_name = 'file_id'
        if 'id_drive' in Drive._fields:
            drive_field_name = 'id_drive'
        elif 'google_id' in Drive._fields:
            drive_field_name = 'google_id'

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
                    existing[gid] = ws
                elif ws.name != name:
                    ws.write({"name": name})
                target_ws_id = ws.id
            
            drive_rec = Drive.search([(drive_field_name, "=", gid)], limit=1)

            vals.append({
                "browser_id": self.id,
                "parent_workspace_id": self.selected_workspace_id.id,
                "entry_type": "folder" if is_folder else "file",
                "name": name,
                "google_id": gid,
                "mime_type": mime,
                "web_view_link": c.get("alternateLink") or c.get("webViewLink"),
                "thumbnail_link": c.get("thumbnailLink"),
                "target_workspace_id": target_ws_id,
                "drive_id": drive_rec.id if drive_rec else False,   
            })

        if vals:
            Entry.create(vals)

    def _gd_get_drive(self):
        if hasattr(GoogleDriveAuth, "get_instance"):
            return GoogleDriveAuth.get_instance().get_drive()
        return GoogleDriveAuth().get_drive()


    def _gd_project_name(self):
        self.ensure_one()
        name = (getattr(self.project_id, "new_name", False) or self.project_id.name or "").strip()
        if not name:
            raise UserError(_("El proyecto no tiene nombre para buscar en Drive."))
        return name

    def _gd_find_project_root_folder_id(self):
        self.ensure_one()
        drive = self._gd_get_drive()
        project_name = self._gd_project_name()

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

        matches = drive.ListFile({"q": q}).GetList()
        if not matches:
            raise UserError(_("No encontré en Drive la carpeta del proyecto: %s") % project_name)

        return matches[0]["id"]

    def _gd_list_subfolders_recursive(self, parent_id):
        folder_mime = "application/vnd.google-apps.folder"

        q = (
            f"'{parent_id}' in parents and "
            f"mimeType='{folder_mime}' and trashed=false"
        )

        children = self._gd_list_all(q)

        out = []
        for c in children:
            node = {
                "id": c.get("id"),
                "name": c.get("title") or c.get("name") or "Sin nombre",
                "parent_id": parent_id,
            }
            if node["id"]:
                out.append(node)
                out.extend(self._gd_list_subfolders_recursive(node["id"]))

        return out

    @api.depends("workspace_search", "workspace_tree_ids")
    def _compute_workspace_view(self):
        for rec in self:
            if not rec.workspace_search:
                rec.workspace_view_ids = [(6, 0, rec.workspace_tree_ids.ids)]
                continue

            matched = self.env["gestion.documental.workspace"].search([
                ("browser_id", "=", rec.id),
                ("name", "ilike", rec.workspace_search),
            ])

            if not matched:
                rec.workspace_view_ids = [(5, 0, 0)]
                continue

            visible_ids = set(matched.ids)
            for f in matched:
                parent = f.parent_id
                while parent:
                    if parent.id in visible_ids:
                        break
                    visible_ids.add(parent.id)
                    parent = parent.parent_id

            rec.workspace_view_ids = [(6, 0, list(visible_ids))]
                
    def _gd_list_all(self, q):
        drive = self._gd_get_drive()

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
    
    def _gd_list_children(self, folder_id):
        q = f"'{folder_id}' in parents and trashed=false"
        return self._gd_list_all(q)
    
    def _gd_sync_workspace_from_drive(self):
        self.ensure_one()
        WS = self.env["gestion.documental.workspace"].with_context(gd_sync=True).sudo()

        root_id = self._gd_find_project_root_folder_id()
        root_name = self._gd_project_name()
        nodes = self._gd_list_subfolders_recursive(root_id)

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
                    current_value = getattr(rec, k)
                    if hasattr(current_value, 'id'):
                        current_value = current_value.id
                    if current_value != v:
                        diff[k] = v
                if diff:
                    rec.write(diff)
            else:
                ws_by_gid[n["id"]] = WS.create(vals)

        full_by_gid = dict(ws_by_gid)
        full_by_gid[root_id] = root_ws

        for gid, rec in ws_by_gid.items():
            parent_gid = rec.parent_folder_google_id
            parent_rec = full_by_gid.get(parent_gid) if parent_gid else None
            new_parent_id = parent_rec.id if parent_rec else root_ws.id

            if (rec.parent_id.id if rec.parent_id else False) != new_parent_id:
                rec.write({"parent_id": new_parent_id})

        if not self.selected_workspace_id:
            self.with_context(gd_sync=True).write({"selected_workspace_id": root_ws.id})

    def _gd_list_all(self, q):
        """
        ListFile robusto: soporta paginación + Shared Drives (unidades compartidas).
        Retorna lista completa de items.
        """
        drive = self._gd_get_drive()

        params = {
            "q": q,
            # Shared drives / Team drives (PyDrive2 / Drive v2)
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            # Algunos wrappers aceptan esto para evitar "Missing required parameters"
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
    
    def _gd_list_children(self, folder_id):
        """
        Lista TODO lo que está dentro de una carpeta (folders + files), sin depender de Odoo.
        """
        q = f"'{folder_id}' in parents and trashed=false"
        return self._gd_list_all(q)

    def _gd_list_child_folder_nodes(self, folder_id):
        folder_mime = "application/vnd.google-apps.folder"
        q = (
            f"'{folder_id}' in parents and "
            f"mimeType='{folder_mime}' and trashed=false"
        )
        nodes = []
        for child in self._gd_list_all(q):
            gid = child.get("id")
            if not gid:
                continue
            nodes.append({
                "id": gid,
                "name": child.get("title") or child.get("name") or "Sin nombre",
                "parent_id": folder_id,
            })
        return nodes

    def _gd_list_folder_nodes_recursive_limited(self, folder_id, max_depth=6):
        nodes = []
        pending = [(folder_id, 0)]
        seen = set()
        while pending:
            parent_id, depth = pending.pop(0)
            if parent_id in seen or depth >= max_depth:
                continue
            seen.add(parent_id)

            children = self._gd_list_child_folder_nodes(parent_id)
            nodes.extend(children)
            for child in children:
                child_id = child.get("id")
                if child_id and child_id not in seen:
                    pending.append((child_id, depth + 1))
        return nodes

    def _gd_upsert_workspace_nodes(self, root_id, root_name, nodes):
        WS = self.env["gestion.documental.workspace"].with_context(gd_sync=True).sudo()
        root_ws = WS.search([
            ("browser_id", "=", self.id),
            ("folder_google_id", "=", root_id),
        ], limit=1)

        if root_ws:
            root_vals = {}
            if root_ws.name != root_name:
                root_vals["name"] = root_name
            if root_ws.parent_id:
                root_vals["parent_id"] = False
            if root_vals:
                root_ws.write(root_vals)
        else:
            root_ws = WS.create({
                "browser_id": self.id,
                "name": root_name,
                "folder_google_id": root_id,
                "parent_folder_google_id": False,
                "parent_id": False,
            })

        google_ids = [n["id"] for n in nodes]
        existing = WS.search([
            ("browser_id", "=", self.id),
            ("folder_google_id", "in", google_ids),
        ]) if google_ids else WS.browse()
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
                    current_value = getattr(rec, k)
                    if hasattr(current_value, 'id'):
                        current_value = current_value.id
                    if current_value != v:
                        diff[k] = v
                if diff:
                    rec.write(diff)
            else:
                ws_by_gid[n["id"]] = WS.create(vals)

        full_by_gid = dict(ws_by_gid)
        full_by_gid[root_id] = root_ws
        for gid, rec in ws_by_gid.items():
            parent_gid = rec.parent_folder_google_id
            parent_rec = full_by_gid.get(parent_gid) if parent_gid else None
            if not parent_rec and parent_gid:
                parent_rec = WS.search([
                    ("browser_id", "=", self.id),
                    ("folder_google_id", "=", parent_gid),
                ], limit=1)
            new_parent_id = parent_rec.id if parent_rec else root_ws.id
            if (rec.parent_id.id if rec.parent_id else False) != new_parent_id:
                rec.write({"parent_id": new_parent_id})

        if not self.selected_workspace_id:
            self.with_context(gd_sync=True).write({"selected_workspace_id": root_ws.id})
        return root_ws

    def _gd_sync_workspace_for_inventory_quick(self):
        self.ensure_one()
        root_id = self._gd_find_project_root_folder_id()
        root_name = self._gd_project_name()
        nodes = self._gd_list_child_folder_nodes(root_id)
        expand_names = {"COMUNICACIONES", "INFORMES", "CARPETA PRINCIPAL"}
        recursive_names = {"CONTRATOS", "CONTRATOS PS"}
        seen_node_ids = {node["id"] for node in nodes}
        for node in list(nodes):
            display_name = _FOLDER_RENAME_MAP.get(node["name"] or "", node["name"] or "")
            clean_name = _re.sub(r'^\d+(?:\.\d+)*(?:\s*[\._-]\s*|\s+)', '', display_name).strip().upper()
            if clean_name in recursive_names:
                children = self._gd_list_folder_nodes_recursive_limited(node["id"])
            elif clean_name in expand_names:
                children = self._gd_list_child_folder_nodes(node["id"])
            else:
                children = []

            for child in children:
                child_id = child.get("id")
                if child_id and child_id not in seen_node_ids:
                    nodes.append(child)
                    seen_node_ids.add(child_id)
        return self._gd_upsert_workspace_nodes(root_id, root_name, nodes)
    
    def _gd_sync_workspace_from_drive(self):
        """
        Sincroniza el árbol del workspace directamente desde Google Drive.
        No depende de drive.file.
        """
        self.ensure_one()
        WS = self.env["gestion.documental.workspace"].with_context(gd_sync=True).sudo()

        root_id = self._gd_find_project_root_folder_id()
        root_name = self._gd_project_name()
        nodes = self._gd_list_subfolders_recursive(root_id)

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

        # 2) Upsert de nodos (sin parent todavía)
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
                    current_value = getattr(rec, k)
                    if hasattr(current_value, 'id'):
                        current_value = current_value.id
                    if current_value != v:
                        diff[k] = v
                if diff:
                    rec.write(diff)
            else:
                ws_by_gid[n["id"]] = WS.create(vals)

        # 3) Segunda pasada: resolver parent_id (por google parent id)
        # Mapa completo incluyendo raíz
        full_by_gid = dict(ws_by_gid)
        full_by_gid[root_id] = root_ws

        for gid, rec in ws_by_gid.items():
            parent_gid = rec.parent_folder_google_id
            parent_rec = full_by_gid.get(parent_gid) if parent_gid else None
            new_parent_id = parent_rec.id if parent_rec else root_ws.id  # cuelga de la raíz si no encuentra padre

            if (rec.parent_id.id if rec.parent_id else False) != new_parent_id:
                rec.write({"parent_id": new_parent_id})

        # 4) Selección inicial
        if not self.selected_workspace_id:
            self.with_context(gd_sync=True).write({"selected_workspace_id": root_ws.id})

        if not self.env.context.get("skip_inventario_browser_sync"):
            self._gd_actualizar_inventarios_documentales()

    def _gd_actualizar_inventarios_documentales(self):
        for browser in self:
            workspace_ids = self.env["gestion.documental.workspace"].sudo().search([
                ("browser_id", "=", browser.id),
            ]).ids
            if not workspace_ids:
                continue
            inventarios = self.env["inventario.documental"].sudo().search([
                ("workspace_id", "in", workspace_ids),
            ])
            if inventarios:
                inventarios.with_context(skip_inventario_browser_sync=True)._refresh_lineas_y_subir_inventario()

class GestionDocumentalProject(models.Model):
    _name = "gestion.documental.project"
    _description = "Gestión Documental - Proyectos (selector)"

    project_id = fields.Many2one("contratacion", required=True, ondelete="cascade")
    name = fields.Char(related="project_id.new_name", store=False)

    def action_open_workspace(self):
        self.ensure_one()

        Browser = self.env["gestion.documental.browser"].with_context(gd_sync=True).sudo()
        browser = Browser.search([("project_id", "=", self.project_id.id)], limit=1)
        if not browser:
            browser = Browser.create({"project_id": self.project_id.id})

        # Sincroniza árbol desde Drive (tu método nuevo)
        browser._gd_sync_workspace_from_drive()
        browser._gd_refresh_entries()

        # IMPORTANTE: abrir el BROWSER (1 solo registro), no el workspace
        return {
            "type": "ir.actions.act_window",
            "name": _("Gestión Documental"),
            "res_model": "gestion.documental.browser",
            "res_id": browser.id,
            "view_mode": "form",
            "target": "current",
            "context": {"default_project_id": self.project_id.id},
        }

    def action_go_parent(self):
        self.ensure_one()
        parent = self.parent_id or self  # si no hay parent, te quedas
        self.browser_id.with_context(gd_sync=True).write({
            "selected_workspace_id": parent.id if self.parent_id else self.id
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "gestion.documental.browser",
            "res_id": self.browser_id.id,
            "view_mode": "form",
            "target": "current",
        }

class GDFileViewer(models.TransientModel):
    _name = "gestion.documental.file.viewer"
    _description = "GD File Viewer"

    name = fields.Char(readonly=True)
    preview_html = fields.Html(readonly=True)

    @api.model
    def create_from_entry(self, entry):
        url = entry.web_view_link or ""
        html = ""
        if url:
            html = f"""
                <div style="width:100%; height:78vh;">
                    <iframe src="{url}"
                            style="width:100%; height:100%; border:0; border-radius:10px;"></iframe>
                </div>
            """
        return self.create({"name": entry.name, "preview_html": html})

class DocumentoArchivoGD(models.Model):
    _name = 'documento_archivo_gd'
    _description = 'Hoja de Control Gestión Documental'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    workspace_id = fields.Many2one('gestion.documental.workspace', string='Carpeta Relacionada')
    
    fecha = fields.Date(string='Fecha Principal', default=fields.Date.today)
    dia_header = fields.Char(string='Día')
    mes_header = fields.Char(string='Mes')
    anio_header = fields.Char(string='Año')
    seccion_subseccion = fields.Char(string='Sección/Subsección', tracking=True)
    serie_subserie = fields.Char(string='Serie/Subserie', tracking=True)
    asunto_expediente = fields.Char(string='Asunto/Expediente', tracking=True)

    linea_ids = fields.One2many('documento_archivo_gd_linea', 'documento_id', string='Líneas de Documentos')
    firma_elaboracion = fields.Char(string='Firma de Elaboración', tracking=True)
    firmado_elaboracion = fields.Boolean(string='Firma electrónica de elaboración', tracking=True)
    log_elaboracion_id = fields.Many2one('signature.mixin', string='Log Firma Elaboración', readonly=True)
    firma_elaboracion_hash = fields.Char(string='Hash Firma Elaboración', readonly=True, copy=False, tracking=True)
    fecha_elaboracion = fields.Date(string='Fecha de Elaboración', tracking=True)
    drive_sync_signature = fields.Char(string='Huella de sincronización Drive', readonly=True, copy=False)

    @api.onchange('firma_elaboracion')
    def _onchange_firma_elaboracion(self):
        for record in self:
            record.firmado_elaboracion = bool((record.firma_elaboracion or '').strip())

    @api.model
    def create(self, vals):
        vals = self._prepare_firma_elaboracion_vals(vals)
        record = super().create(vals)
        if vals.get('firmado_elaboracion') and not record.log_elaboracion_id:
            record._write_with_signature_tracking({'firmado_elaboracion': True})
        return record

    def write(self, vals):
        vals = self._prepare_firma_elaboracion_vals(vals)
        return self._write_with_signature_tracking(vals)

    def _prepare_firma_elaboracion_vals(self, vals):
        vals = dict(vals)
        if 'firma_elaboracion' in vals:
            vals['firmado_elaboracion'] = bool((vals.get('firma_elaboracion') or '').strip())
        return vals

    # Trazabilidad de firmas (ver seguimiento_firmas en decorador_firmas.py):
    # 1 solo slot — Firma de Elaboración. También se llama desde create()
    # (arriba) para el caso de crear el registro ya firmado. Ojo: el write()
    # en _guardar_drive_sync_signature (`drive_sync_signature`) llama a
    # super() directo, sin pasar por este helper — no gestiona firmas.
    @seguimiento_firmas([{
        'name': 'Firma de Elaboración',
        'img': 'firma_elaboracion',
        'check': 'firmado_elaboracion',
        'log': 'log_elaboracion_id',
        'hash': 'firma_elaboracion_hash',
    }])
    def _write_with_signature_tracking(self, vals):
        return super(DocumentoArchivoGD, self).write(vals)
    
    def action_calcular_folios(self):
        """
        1. Recorre la hoja de control.
        2. Búsqueda insensible a mayúsculas/minúsculas para asegurar que encuentre el archivo.
        3. Fuerza la descarga si hay 0 folios. Si está encriptado, lo atrapa y avisa.
        """
        self.ensure_one()
        import tempfile
        import os
        import logging
        from PyPDF2 import PdfReader
        from .drive import GoogleDriveAuth
        
        _logger = logging.getLogger(__name__)

        drive_auth = GoogleDriveAuth.get_instance()
        gdrive = drive_auth.get_drive()

        archivos = self.env['gestion.documental.entry'].search([
            ('parent_workspace_id', '=', self.workspace_id.id),
            ('entry_type', '=', 'file')
        ])
        
        # Mapeo a prueba de balas: todo en minúsculas y sin espacios a los lados
        drive_por_nombre = {}
        for arch in archivos:
            if arch.drive_id:
                nombre_base = arch.name.lower().strip()
                drive_data = (arch.drive_id, (arch.mime_type or '').lower(), nombre_base)
                drive_por_nombre[nombre_base] = drive_data
                if '.' in nombre_base:
                    nombre_sin_ext = nombre_base.rsplit('.', 1)[0]
                    drive_por_nombre[nombre_sin_ext] = drive_data

        folio_actual = 1
        archivos_fallidos = []
        
        for linea in self.linea_ids:
            try:
                paginas_actuales = int(linea.folios) if linea.folios else 0
            except ValueError:
                paginas_actuales = 0
            
            # Limpiamos el nombre de la línea para buscarlo correctamente
            nombre_linea = str(linea.tipo_documental).lower().strip() if linea.tipo_documental else ""
            drive_data = drive_por_nombre.get(nombre_linea)
            drive_rec = drive_data[0] if drive_data else False
            mime_type = drive_data[1] if drive_data else ''
            drive_name = drive_data[2] if drive_data else ''
            
            paginas_finales = paginas_actuales
            
            # Si el usuario puso 0 o está vacío, y encontramos la referencia en Drive
            if paginas_actuales == 0 and drive_rec:
                google_id = drive_rec.file_id or drive_rec.drive_file_id

                if drive_rec.page_total:
                    paginas_finales = drive_rec.page_total
                elif google_id and (mime_type == 'application/pdf' or drive_name.endswith('.pdf')):
                    temp_path = None
                    try:
                        _logger.info(f"Intentando leer desde Drive: {linea.tipo_documental}")
                        
                        gdrive_file = gdrive.CreateFile({'id': google_id})
                        fd, temp_path = tempfile.mkstemp(suffix='.pdf')
                        os.close(fd)
                        gdrive_file.GetContentFile(temp_path)
                        
                        # Intentar leer el archivo con PyPDF2 (detectará si es PDF válido o encriptado)
                        try:
                            with open(temp_path, 'rb') as f:
                                reader = PdfReader(f)
                                if reader.is_encrypted:
                                    if not reader.decrypt(""):
                                        raise Exception("El PDF requiere contraseña.")
                                paginas_finales = len(reader.pages)
                        except Exception as pdf_error:
                            raise Exception(f"No se pudo leer (encriptado o formato inválido): {pdf_error}")

                        if paginas_finales > 0:
                            drive_rec.sudo().with_context(skip_drive_sync=True, skip_integrity=True).write({
                                'page_total': paginas_finales,
                                'page_range': f"1-{paginas_finales}",
                            })
                            
                    except Exception as e:
                        _logger.error(f"Fallo al procesar {linea.tipo_documental}: {e}")
                        if linea.tipo_documental not in archivos_fallidos:
                            archivos_fallidos.append(linea.tipo_documental)
                        paginas_finales = 0
                    finally:
                        if temp_path and os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except:
                                pass

            if paginas_finales > 0:
                linea.folios = str(paginas_finales)
                folio_fin = folio_actual + paginas_finales - 1
                linea.numero_consecutivo = f"{folio_actual}-{folio_fin}"
                linea.folios_conteo = f"{folio_actual}-{folio_fin}"
                folio_actual = folio_fin + 1
            else:
                linea.folios = "0"
                linea.numero_consecutivo = "0"
                linea.folios_conteo = "0"

        if not self.env.context.get('gd_auto_calculate_folios'):
            self._actualizar_inventarios_relacionados()

        # Mostrar Wizard con diseño HTML (Reemplaza el bloque final de action_calcular_folios)
        if archivos_fallidos and not self.env.context.get('gd_auto_calculate_folios'):
            # Llamamos a nuestra nueva función
            ruta_carpeta = self.workspace_id.get_full_path_minus_root() if self.workspace_id else "Carpeta Desconocida"

            nombres_html = "".join([f"<li><b>{nombre}</b></li>" for nombre in archivos_fallidos])
            nombres_texto = "\n".join([f"- {nombre}" for nombre in archivos_fallidos]) 
            
            mensaje = f"""
                <div style="font-size: 14px; color: #444; font-family: sans-serif;">
                    <p style="margin-bottom: 10px;">La foliación se ha calculado para los legibles.</p>
                    <p style="margin-bottom: 10px;">Los siguientes archivos <b>fallaron</b>:</p>
                    <ul style="color: #d9534f; background-color: #fdf2f2; padding: 10px 10px 10px 30px; border-radius: 5px; border-left: 4px solid #d9534f;">
                        {nombres_html}
                    </ul>
                    <p style="margin-top: 15px;"><i>Si lo deseas, puedes escribir una observación abajo y reportar esto directamente al coordinador.</i></p>
                </div>
            """
            
            wizard = self.env['gestion.documental.wizard.message'].create({
                'message': mensaje,
                'project_id': self.workspace_id.project_id.id if self.workspace_id else False,
                'workspace_name': ruta_carpeta, # Usamos la nueva ruta
                'archivos_fallidos_text': nombres_texto
            })
            
            return {
                'name': 'Atención: Archivos no procesados',
                'type': 'ir.actions.act_window',
                'res_model': 'gestion.documental.wizard.message',
                'res_id': wizard.id,
                'view_mode': 'form',
                'target': 'new',
            }

    def _actualizar_inventarios_relacionados(self):
        self.ensure_one()
        if not self.workspace_id or not self.workspace_id.browser_id:
            return
        try:
            inventarios = self.env['inventario.documental'].sudo().search([
                ('workspace_id.browser_id', '=', self.workspace_id.browser_id.id),
            ])
            if inventarios:
                inventarios._refresh_lineas_y_subir_inventario()
        except Exception as exc:
            _logger.warning(
                "No se pudo actualizar el inventario asociado a la hoja de control %s: %s",
                self.id,
                exc,
            )

    def _actualizar_inventarios_desde_documentos(self):
        if self.env.context.get(_GD_SKIP_INVENTARIO_SYNC):
            return
        for documento in self.exists():
            documento._actualizar_inventarios_relacionados()
        
    def name_get(self):
        return [(record.id, f"Hoja de Control - {record.workspace_id.name}") for record in self]
    
    def action_descargar_excel(self):
        self.ensure_one()
        file_content = self._build_excel_content()
        if self._upload_excel_to_drive(file_content):
            self._guardar_drive_sync_signature(self._get_drive_sync_signature())
        return self._create_attachment_download(file_content)

    def _build_excel_content(self):
        self.ensure_one()
        if load_workbook is None:
            raise UserError("La librería 'openpyxl' no está instalada.")
        wb = self._load_template()
        ws = wb.active
        self._fill_header_data(ws)
        self._fill_lines_data(ws)
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        file_content = output.read()
        output.close()
        return file_content

    def _get_drive_sync_signature(self):
        self.ensure_one()
        payload = {
            'workspace_folder': self.workspace_id.folder_google_id or '',
            'fecha': fields.Date.to_string(self.fecha) if self.fecha else '',
            'dia_header': self.dia_header or '',
            'mes_header': self.mes_header or '',
            'anio_header': self.anio_header or '',
            'seccion_subseccion': self.seccion_subseccion or '',
            'serie_subserie': self.serie_subserie or '',
            'asunto_expediente': self.asunto_expediente or '',
            'firma_elaboracion': self.firma_elaboracion or '',
            'firmado_elaboracion': bool(self.firmado_elaboracion),
            'fecha_elaboracion': fields.Date.to_string(self.fecha_elaboracion) if self.fecha_elaboracion else '',
            'lineas': [
                [
                    linea.dia or '',
                    linea.mes or 0,
                    linea.anio or 0,
                    linea.tipo_documental or '',
                    linea.numero_consecutivo or '',
                    linea.folios or '',
                    linea.folios_conteo or '',
                ]
                for linea in self.linea_ids.sorted('id')
            ],
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

    def _guardar_drive_sync_signature(self, signature):
        self.ensure_one()
        super(DocumentoArchivoGD, self).write({'drive_sync_signature': signature})

    def _sincronizar_excel_si_cambio(self):
        self.ensure_one()
        signature = self._get_drive_sync_signature()
        if self.drive_sync_signature == signature:
            return False
        file_content = self._build_excel_content()
        if self._upload_excel_to_drive(file_content):
            self._guardar_drive_sync_signature(signature)
            return True
        return False

    def _upload_excel_to_drive(self, file_content):
        self.ensure_one()
        if not self.workspace_id or not self.workspace_id.folder_google_id:
            return False

        import tempfile
        from .drive import GoogleDriveAuth

        folder_gid = self.workspace_id.folder_google_id
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        gdrive = GoogleDriveAuth.get_instance().get_drive()

        siblings = gdrive.ListFile({
            "q": f"'{folder_gid}' in parents and trashed=false",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }).GetList()
        file_name = self._get_hoja_control_file_name(siblings)
        existing = [item for item in siblings if _gd_is_hoja_control_name(item.get("title") or item.get("name"))]

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
            os.close(fd)
            with open(tmp_path, "wb") as tmp:
                tmp.write(file_content)

            if existing:
                gfile = existing[0]
                gfile["title"] = file_name
            else:
                gfile = gdrive.CreateFile({
                    "title": file_name,
                    "parents": [{"id": folder_gid}],
                    "mimeType": mimetype,
                })
            gfile.SetContentFile(tmp_path)
            gfile.Upload(param={"supportsAllDrives": True})

            gid = gfile.get("id")
            link = gfile.get("alternateLink") or gfile.get("webViewLink")
            preview_url = gfile.get("embedLink") or link

            Drive = self.env["drive"].sudo()
            drive_rec = Drive.search([("drive_file_id", "=", gid)], limit=1)
            drive_vals = {
                "file_name": file_name,
                "drive_file_id": gid,
                "folder_id": folder_gid,
                "file_url": link,
                "fecha": fields.Date.today(),
                "user_id": self.env.user.id,
                "preview_html": (
                    f'<iframe src="{preview_url}" width="100%" height="600px" '
                    f'loading="lazy" frameborder="0" allowfullscreen></iframe>'
                    if preview_url else False
                ),
                "page_total": 0,
                "page_range": False,
            }
            if drive_rec:
                drive_rec.write(drive_vals)
            else:
                drive_rec = Drive.create(drive_vals)

            Entry = self.env["gestion.documental.entry"].sudo().with_context(gd_sync=True)
            entry = Entry.search([
                ("browser_id", "=", self.workspace_id.browser_id.id),
                ("parent_workspace_id", "=", self.workspace_id.id),
                ("google_id", "=", gid),
            ], limit=1)
            entry_vals = {
                "browser_id": self.workspace_id.browser_id.id,
                "parent_workspace_id": self.workspace_id.id,
                "entry_type": "file",
                "name": file_name,
                "google_id": gid,
                "mime_type": mimetype,
                "web_view_link": link,
                "drive_id": drive_rec.id,
            }
            if entry:
                entry.write(entry_vals)
            else:
                Entry.create(entry_vals)
            return True
        except Exception as exc:
            _logger.warning("No se pudo subir la hoja de control a Drive: %s", exc)
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _get_hoja_control_file_name(self, siblings):
        prefix = _gd_extract_numeric_prefix(self.workspace_id.name)
        if not prefix:
            return _HOJA_CONTROL_BASE_NAME

        pattern = _re.compile(r'^' + _re.escape(prefix) + r'\.(\d+)(?:[\s_.]|$)')
        max_seq = 0
        for item in siblings:
            title = item.get("title") or item.get("name") or ""
            if _gd_is_hoja_control_name(title):
                continue
            match = pattern.match(title)
            if not match:
                continue
            try:
                max_seq = max(max_seq, int(match.group(1)))
            except ValueError:
                pass

        return f"{prefix}.{max_seq + 1} {_HOJA_CONTROL_BASE_NAME}"
    
    def _fill_header_data(self, ws):
        placeholders = self._get_placeholders()
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                if not cell.value or not isinstance(cell.value, str):
                    continue
                for key, value in placeholders.items():
                    if key in cell.value:
                        cell.value = cell.value.replace(key, str(value))

    def _fill_lines_data(self, ws):
        # Ajustar según tu Excel, en Comunicaciones es fila 15
        START_ROW = 15 
        current_row = START_ROW
        lines = self.linea_ids
        for index, linea in enumerate(lines):
            if index > 0:
                ws.insert_rows(current_row)
                self._copy_row_style(ws, current_row - 1, current_row)
            self._safe_write(ws, current_row, 1, linea.dia)
            self._safe_write(ws, current_row, 2, linea.mes)
            self._safe_write(ws, current_row, 3, linea.anio)
            self._safe_write(ws, current_row, 4, linea.tipo_documental)
            self._safe_write(ws, current_row, 7, linea.numero_consecutivo) 
            self._safe_write(ws, current_row, 8, linea.folios_conteo)
            current_row += 1

    def _get_placeholders(self):
        firma_texto = self.firma_elaboracion or ''
        if firma_texto and self.firmado_elaboracion:
            firma_texto = "%s\nFirma electrónica Ley 527 de 1999" % firma_texto
        return {
            '{{dia}}': self.dia_header or '',
            '{{mes}}': self.mes_header or '',
            '{{anio}}': self.anio_header or '',
            '{{seccion}}': self.seccion_subseccion or '',
            '{{serie}}': self.serie_subserie or '',
            '{{asunto}}': self.asunto_expediente or '',
            '{{firma}}': firma_texto,
            '{{fecha_elaboracion}}': self.fecha_elaboracion or '',
        }

    def _safe_write(self, ws, row, col, value):
            cell = ws.cell(row=row, column=col)
            if not isinstance(cell, MergedCell):
                cell.value = str(value) if value else ''
                return
            for merged_range in ws.merged_cells.ranges:
                if cell.coordinate in merged_range:
                    top_left_cell = ws.cell(row=merged_range.min_row, column=merged_range.min_col)
                    top_left_cell.value = str(value) if value else ''
                    break
          
    def _copy_row_style(self, ws, source_row_idx, target_row_idx):
        source_row = ws[source_row_idx]
        target_row = ws[target_row_idx]
        for source_cell, target_cell in zip(source_row, target_row):
            if source_cell.has_style:
                target_cell.font = copy(source_cell.font)
                target_cell.border = copy(source_cell.border)
                target_cell.fill = copy(source_cell.fill)
                target_cell.number_format = copy(source_cell.number_format)
                target_cell.protection = copy(source_cell.protection)
                target_cell.alignment = copy(source_cell.alignment)

    def _load_template(self):
        # Usamos la misma plantilla que Comunicaciones
        template_path = get_module_resource('Matriz', 'static/src/xls', 'FORMATO HOJA DE CONTROL.xlsx')
        if not template_path or not os.path.isfile(template_path):
            raise UserError(f"No se encontró la plantilla en la ruta: {template_path}")
        return load_workbook(template_path)
    
    def _create_attachment_download(self, file_content):
        attachment = self.env['ir.attachment']._drive_create_temp_download({
            'name': "Hoja de control.xlsx",
            'type': 'binary',
            'datas': base64.b64encode(file_content),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

class DocumentoArchivoGDLinea(models.Model):
    _name = 'documento_archivo_gd_linea'
    _description = 'Línea de Hoja de Control GD'

    documento_id = fields.Many2one('documento_archivo_gd', string='Documento')

    dia = fields.Char(string='Día')
    mes = fields.Integer(string='Mes')
    anio = fields.Integer(string='Año')
    tipo_documental = fields.Char(string='Tipos Documentales')
    numero_consecutivo = fields.Char(string='Número Consecutivo')
    folios = fields.Char(string='N° hojas')
    folios_conteo = fields.Char(string='Folios', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._actualizar_inventarios_desde_lineas()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals:
            self._actualizar_inventarios_desde_lineas()
        return res

    def unlink(self):
        documentos = self.mapped('documento_id')
        res = super().unlink()
        documentos._actualizar_inventarios_desde_documentos()
        return res

    def _actualizar_inventarios_desde_lineas(self):
        if self.env.context.get(_GD_SKIP_INVENTARIO_SYNC):
            return
        self.mapped('documento_id')._actualizar_inventarios_desde_documentos()


# --- FUNCIÓN DE UTILIDAD PARA ENVÍO DE CORREOS ---
def enviar_notificacion_coordinador(env, project, workspace_name, archivos_text, observaciones, tipo_reporte="Múltiples Archivos"):
    if not project:
        raise UserError("No se encontró un proyecto relacionado para notificar.")

    coordinador = project.create_uid
    grupo_asis = env.ref('Matriz.group_contratacion_asis_coordinador', raise_if_not_found=False)
    grupo_coord = env.ref('Matriz.group_contratacion_coordinador', raise_if_not_found=False)

    # Buscar asistentes
    asistentes = env['res.users']
    if project.users_with_access and (grupo_asis or grupo_coord):
        asistentes = project.users_with_access.filtered(
            lambda u: (grupo_asis and grupo_asis in u.groups_id) or (grupo_coord and grupo_coord in u.groups_id)
        )

    # Recopilar correos sin duplicados
    correos_destino = set()
    if coordinador and coordinador.email:
        correos_destino.add(coordinador.email)
    for asis in asistentes:
        if asis.email:
            correos_destino.add(asis.email)

    if not correos_destino:
        raise UserError("El coordinador y los asistentes de este proyecto no tienen correos configurados.")

    base_url = env["ir.config_parameter"].sudo().get_param("web.base.url")
    link_proyecto = f"{base_url}/web#id={project.id}&model=contratacion&view_type=form"

    observ_text = f"<p><strong>Observaciones:</strong><br/>{observaciones}</p>" if observaciones else ""

    body_html = f"""
        <div style="font-family: Arial, sans-serif; color: #444; border: 1px solid #ddd; border-radius: 5px; padding: 20px;">
            <h2 style="color: #d9534f; margin-top: 0;">⚠️ Reporte de Problema en Archivos</h2>
            <p>Se ha reportado un problema con documentos en el módulo de Gestión Documental.</p>
            
            <div style="background-color: #f9f9f9; border-left: 4px solid #003a70; padding: 10px; margin: 15px 0;">
                <strong>Proyecto:</strong> {project.display_name}<br/>
                <strong>Ubicación (Carpeta):</strong> {workspace_name}<br/>
            </div>
            
            <p><strong>Archivos reportados:</strong></p>
            <pre style="background-color: #f1f1f1; padding: 10px; border-radius: 3px;">{archivos_text}</pre>
            
            {observ_text}
            
            <p style="margin-top: 20px;">
                <a href="{link_proyecto}" style="background-color: #003a70; color: white; padding: 10px 15px; text-decoration: none; border-radius: 3px;">Ver Proyecto</a>
            </p>
        </div>
    """

    # Enviar correo
    mail_values = {
        'subject': f"Alerta en Documentos - Proyecto: {project.name}",
        'email_from': env.user.email or 'noreply@fundacionunivalle.org',
        'email_to': ",".join(correos_destino),
        'body_html': body_html,
        'auto_delete': True,
    }
    mail = env['mail.mail'].sudo().create(mail_values)
    mail.sudo().send()

    # Escribir en el chatter del contrato
    chatter_msg = f"⚠️ <b>Reporte de Gestión Documental ({workspace_name})</b><br/>Se reportaron inconvenientes con los siguientes archivos:<br/><pre>{archivos_text}</pre>"
    if observaciones:
        chatter_msg += f"<br/><b>Observaciones:</b> {observaciones}"
    project.message_post(body=chatter_msg)


# --- 1. WIZARD ACTUALIZADO (Para la Hoja de Control / Masivo) ---
class GestionDocumentalWizardMessage(models.TransientModel):
    _name = 'gestion.documental.wizard.message'
    _description = 'Mensaje de Aviso - Gestión Documental'

    message = fields.Html(string="Detalle", readonly=True)
    project_id = fields.Many2one('contratacion', string="Proyecto")
    workspace_name = fields.Char("Carpeta")
    archivos_fallidos_text = fields.Text("Archivos")
    observaciones = fields.Text(string="Observaciones (Opcional)")

    def action_reportar_y_cerrar(self):
        self.ensure_one()
        enviar_notificacion_coordinador(
            self.env, self.project_id, self.workspace_name, 
            self.archivos_fallidos_text, self.observaciones
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Reporte Enviado',
                'message': 'Se ha notificado al coordinador y asistentes con éxito.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'}, 
            }
        }

# --- 2. NUEVO WIZARD (Para reporte individual desde el Kanban) ---
class GestionDocumentalReportWizard(models.TransientModel):
    _name = 'gestion.documental.report.wizard'
    _description = 'Reportar Archivo Individual'

    entry_id = fields.Many2one('gestion.documental.entry', string="Archivo", readonly=True)
    project_id = fields.Many2one('contratacion', string="Proyecto")
    workspace_name = fields.Char("Carpeta")
    observaciones = fields.Text(string="Observaciones")

    def action_enviar_reporte(self):
        self.ensure_one()
        archivos_text = f"- {self.entry_id.name}"
        enviar_notificacion_coordinador(
            self.env, self.project_id, self.workspace_name, 
            archivos_text, self.observaciones
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Reporte Enviado',
                'message': 'El archivo ha sido reportado al coordinador.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'}, # <--- Cierra el modal
            }
        }

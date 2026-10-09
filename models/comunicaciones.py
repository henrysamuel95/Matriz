import io
import os
import base64
import html
from copy import copy
from odoo import models, fields, api
from odoo.exceptions import UserError, AccessError
from odoo.modules.module import get_module_resource
import logging
try:
    from openpyxl import load_workbook
    from openpyxl.cell.cell import MergedCell 
except ImportError:
    load_workbook = None
    MergedCell = None

_logger = logging.getLogger(__name__)

class Comunicaciones(models.Model):
    _name = 'comunicaciones_drive'
    _description = 'Comunicaciones'

    name = fields.Char(string='Nombre de la Comunicación')
    # Relación cambiada a 'comunicaciones_id'
    comunicaciones_ids = fields.One2many('drive', 'comunicaciones_id', string='Carpetas', compute='_compute_documentos_filtrados', readonly=False)
    comunicaciones_archivo_ids = fields.One2many('drive', 'comunicaciones_id', string='Archivos', compute='_compute_documentos_filtrados')
    comunicaciones_subcarpeta_ids = fields.One2many('comunicaciones.workspace', 'parent_id', string='Carpetas', compute='_compute_subcarpetas_actuales')
    stage2_id = fields.Many2one('drive.file', string='Ubicación', tracking=True)
    file_name = fields.Char('File Name', readonly=False)
    user_id = fields.Many2one('res.users', string='Creador', default=lambda self: self.env.user,
                              track_visibility='always')
    preview_html = fields.Text('Preview HTML', readonly=True)
    page_range = fields.Char("Rango de Páginas", compute="_compute_page_panel", readonly=True)
    page_total = fields.Integer("Total de Páginas", compute="_compute_page_panel", readonly=True)
    page_range_panel = fields.Char(string="Folios Documento")
    selected_drive_id = fields.Many2one('drive', string='Archivo seleccionado')
    selected_drive_activity_html = fields.Html(
        string='Actividad del archivo',
        sanitize=False,
    )
    selected_folder_activity_html = fields.Html(
        string='Actividad de la carpeta',
        sanitize=False,
    )

    # Carpeta mostrada en el panel de detalles. Se mantiene separada de la
    # carpeta abierta para que "Ver detalles" no navegue dentro de ella.
    detail_workspace_id = fields.Many2one(
        'comunicaciones.workspace',
        string='Carpeta mostrada en detalles',
    )
    folder_name = fields.Char(related='detail_workspace_id.name', store=False)
    user_1_id = fields.Many2one(
        'res.users',
        related='detail_workspace_id.drive_file_id.user_id',
        store=False
    )

    folios = fields.Char(
        string='Folios',
        compute='compute_folios_preview',
        store=False,
    )
    folder_parent_name = fields.Char(
        string='Ubicación carpeta',
        compute='_compute_selected_folder_panel',
        store=False,
    )
    folder_child_count = fields.Integer(
        string='Subcarpetas',
        compute='_compute_selected_folder_panel',
        store=False,
    )
    folder_file_count = fields.Integer(
        string='Archivos',
        compute='_compute_selected_folder_panel',
        store=False,
    )

    sync_version = fields.Integer(default=0)

    def _ensure_root_workspace(self):
        DriveFile = self.env['drive.file'].sudo()
        Workspace = self.env['comunicaciones.workspace'].sudo()
        for record in self.sudo():
            if not record.proyecto_relacionado:
                continue
            stage = self.env['drive_stage'].sudo().search([
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                '|',
                ('name', 'ilike', 'COMUNICACIONES'),
                ('folder_name', 'ilike', 'COMUNICACIONES'),
            ], limit=1, order='sequence, id')
            folder_id = (stage.folder_id or stage.google_drive_id) if stage else False
            if not folder_id:
                continue
            drive_file = DriveFile.search([
                ('folder_id2', '=', folder_id),
                ('comunicaciones_id', '=', record.id),
            ], limit=1)
            if not drive_file:
                drive_file = DriveFile.create({
                    'folder_name': stage.folder_name or stage.name or 'Comunicaciones',
                    'folder_id2': folder_id,
                    'folder_guardar_id': folder_id,
                    'drive_id': stage.id,
                    'proyecto_relacionado': record.proyecto_relacionado.id,
                    'comunicaciones_id': record.id,
                })
            workspace = Workspace.search([
                ('comunicaciones_id', '=', record.id),
                ('drive_file_id', '=', drive_file.id),
            ], limit=1)
            if not workspace:
                workspace = Workspace.create({
                    'name': drive_file.folder_name or 'Comunicaciones',
                    'comunicaciones_id': record.id,
                    'drive_file_id': drive_file.id,
                })
            if not record.selected_workspace_id:
                record.write({'selected_workspace_id': workspace.id})
        return True

    def _direct_subcarpetas_for_selected_workspace(self, selected):
        if not selected:
            return self.env['comunicaciones.workspace']
        folders = selected.child_ids
        if self._is_root_comunicaciones_workspace(selected):
            root_level_children = selected.comunicaciones_id.workspace_tree_ids.filtered(
                lambda ws: not ws.parent_id and ws.id != selected.id
            )
            folders |= root_level_children
        return folders.sorted(lambda ws: ((ws.name or '').lower(), ws.id))

    @api.depends(
        'selected_workspace_id',
        'selected_workspace_id.child_ids',
        'workspace_tree_ids.parent_id',
        'sync_version',
    )
    def _compute_subcarpetas_actuales(self):
        for rec in self:
            folders = rec._direct_subcarpetas_for_selected_workspace(rec.selected_workspace_id)
            rec.comunicaciones_subcarpeta_ids = [(6, 0, folders.ids)] if folders else [(5, 0, 0)]

    descrpcion_info1 = fields.Text(
        related='detail_workspace_id.drive_file_id.descrpcion_info',
        store=False,
        readonly=False
    )
    fecha1 = fields.Date(
        related='detail_workspace_id.drive_file_id.fecha',
        store=False
    )

    @api.depends(
        'detail_workspace_id',
        'detail_workspace_id.parent_id',
        'detail_workspace_id.child_ids',
        'sync_version',
    )
    def _compute_selected_folder_panel(self):
        for rec in self:
            selected = rec.detail_workspace_id
            rec.folder_parent_name = (
                selected.parent_id.name
                if selected and selected.parent_id
                else 'Raíz'
            )
            children = rec._direct_subcarpetas_for_selected_workspace(selected)
            rec.folder_child_count = len(children) if selected else 0
            if selected and selected.drive_file_id:
                rec.folder_file_count = self.env['drive'].search_count([
                    ('stage2_id', '=', selected.drive_file_id.id),
                    ('comunicaciones_id', '=', rec.id),
                    ('is_folder', '=', False),
                ])
            else:
                rec.folder_file_count = 0

    def _activity_html_for_drive(self, drive_record):
        if not drive_record:
            return '<div class="infcom-empty-activity">Sin actividad registrada.</div>'
        entries = []
        traces = self.env['drive.trace'].sudo().search(
            [('drive_id', '=', drive_record.id)],
            order='action_date desc, id desc',
            limit=25,
        )
        for trace in traces:
            action = dict(trace._fields['action_type'].selection).get(trace.action_type, trace.action_type or 'Actividad')
            entries.append({
                'date': trace.action_date or trace.create_date,
                'author': trace.user_id.name or trace.create_uid.name or 'Sistema',
                'body': '<b>%s</b><br/>%s' % (html.escape(action), html.escape(trace.notes or '')),
            })
        if not entries:
            return '<div class="infcom-empty-activity">Sin actividad registrada.</div>'
        items = []
        for item in entries:
            author = html.escape(item.get('author') or 'Sistema')
            date_value = item.get('date')
            date = html.escape(fields.Datetime.to_string(date_value) if date_value else '')
            body = item.get('body') or ''
            items.append(
                '<div class="infcom-activity-item">'
                '<div class="infcom-activity-head"><strong>%s</strong><span>%s</span></div>'
                '<div class="infcom-activity-body">%s</div>'
                '</div>' % (author, date, body)
            )
        return '<div class="infcom-activity-list">%s</div>' % ''.join(items)

    def _activity_html_for_folder(self, folder):
        if not folder:
            return '<div class="infcom-empty-activity">Sin actividad registrada.</div>'
        traces = self.env['drive.file.trace'].sudo().search(
            [('drive_file_id', '=', folder.id)],
            order='create_date desc',
            limit=25,
        )
        if not traces:
            return '<div class="infcom-empty-activity">Sin actividad registrada.</div>'
        items = []
        for trace in traces:
            author = html.escape(trace.user_id.name or trace.create_uid.name or 'Sistema')
            date = html.escape(fields.Datetime.to_string(trace.create_date) if trace.create_date else '')
            action = html.escape(dict(trace._fields['action_type'].selection).get(trace.action_type, trace.action_type or 'Actividad'))
            notes = html.escape(trace.notes or '')
            items.append(
                '<div class="infcom-activity-item">'
                '<div class="infcom-activity-head"><strong>%s</strong><span>%s</span></div>'
                '<div class="infcom-activity-body"><b>%s</b><br/>%s</div>'
                '</div>' % (author, date, action, notes)
            )
        return '<div class="infcom-activity-list">%s</div>' % ''.join(items)

    # AQUÍ ESTÁ EL CAMBIO CLAVE: Busca 'COMUNICACIONES' en lugar de 'INFORMES'
    workspace = fields.Many2many(
        'drive.file',
        string='Carpeta',
        domain="[('drive_id', '=', 'COMUNICACIONES'), ('proyecto_relacionado', '=', proyecto_relacionado)]",
        readonly=False,
    )
    
    # Relación con el workspace de comunicaciones
    selected_workspace_id = fields.Many2one(
        'comunicaciones.workspace',
        string='Carpeta seleccionada',
    )
    workspace_tree_ids = fields.One2many(
        'comunicaciones.workspace',
        'comunicaciones_id',
        string='Workspace'
    )

    workspace_search = fields.Char(string='Buscar Carpeta', help="Buscar carpeta en Workspace")

    # Campo que muestra el resultado filtrado
    workspace_view_ids = fields.One2many(
        'comunicaciones.workspace',
        string='Workspace (Vista Filtrada)',
        compute='_compute_workspace_view'
    )

    @api.depends('stage2_id', 'selected_workspace_id', 'comunicaciones_ids.page_range', 'comunicaciones_ids.page_total', 'sync_version')
    def _compute_page_panel(self):
        for rec in self:
            total = 0
            rng = False

            if rec.selected_workspace_id:
                docs = rec.comunicaciones_ids.filtered(lambda d: d.stage2_id.id == rec.selected_workspace_id.drive_file_id.id)
                for d in docs:
                    if d.page_total:
                        total += d.page_total

            if total:
                rng = f"1-{total}"

            rec.page_total = total
            rec.page_range = rng

    # 1. Detecta cambios en la búsqueda
    @api.depends('workspace_search', 'workspace_tree_ids')
    def _compute_workspace_view(self):
        for rec in self:
            if rec.id and not self.env.context.get('_skip_drive_workspace_sync'):
                rec.with_context(_skip_drive_workspace_sync=True)._sync_workspace_from_drive_files()
            if not rec.workspace_search:
                # Si no hay búsqueda, muestra TODO
                rec.workspace_view_ids = [(6, 0, rec._ordered_workspace_tree(rec.workspace_tree_ids).ids)]
                continue
            # Si hay texto, llama a la función de filtrado
            rec.workspace_view_ids = rec._get_filtered_hierarchy(rec.workspace_search)

    def _is_root_comunicaciones_workspace(self, workspace):
        name = ((workspace.name or '') + ' ' + (workspace.drive_file_id.folder_name or '')).upper()
        return not workspace.parent_id and 'COMUNICACIONES' in name

    def _ordered_workspace_tree(self, workspaces):
        """Devuelve las carpetas en orden padre -> hijos para que el árbol lateral pueda plegarse."""
        Workspace = self.env['comunicaciones.workspace']
        allowed_ids = set(workspaces.ids)
        children_by_parent = {}
        for workspace in workspaces:
            parent_id = workspace.parent_id.id if workspace.parent_id.id in allowed_ids else False
            children_by_parent.setdefault(parent_id, Workspace)
            children_by_parent[parent_id] |= workspace

        ordered = Workspace

        def sort_key(workspace):
            return ((workspace.name or '').lower(), workspace.id)

        def append_children(parent_id=False):
            nonlocal ordered
            for child in children_by_parent.get(parent_id, Workspace).sorted(sort_key):
                ordered |= child
                append_children(child.id)

        append_children(False)
        return ordered

    # 2. Busca la carpeta Y sus padres (para no perder la ruta)
    def _get_filtered_hierarchy(self, search_term):
        comunicacion_id = self._origin.id or self.id
        
        # Busca carpetas que coincidan con el nombre
        matched_folders = self.env['comunicaciones.workspace'].search([
            ('comunicaciones_id', '=', comunicacion_id),
            ('name', 'ilike', search_term) # 'ilike' ignora mayúsculas/minúsculas
        ])
        
        if not matched_folders:
            return [(5, 0, 0)]
            
        # Función recursiva para obtener los padres
        ids_finales = self._collect_ancestor_ids(matched_folders)
        ordered = self._ordered_workspace_tree(self.env['comunicaciones.workspace'].browse(list(ids_finales)))
        return [(6, 0, ordered.ids)]

    # 3. Recolecta los IDs de los padres recursivamente
    def _collect_ancestor_ids(self, folders):
        visible_ids = set(folders.ids)
        for folder in folders:
            parent = folder.parent_id
            while parent:
                if parent.id in visible_ids:
                    break 
                visible_ids.add(parent.id)
                parent = parent.parent_id
        return visible_ids

    def _sync_workspace_from_drive_files(self):
        """Asegura que las carpetas drive.file de comunicaciones existan en el árbol."""
        WS = self.env['comunicaciones.workspace'].sudo()
        DriveFile = self.env['drive.file'].sudo()
        for rec in self.sudo():
            drive_files = DriveFile.search([
                ('comunicaciones_id', '=', rec.id),
                ('folder_id2', '!=', False),
            ], order='parent_folder_id, sequence, id')
            if not drive_files:
                continue

            ws_by_df = {
                ws.drive_file_id.id: ws
                for ws in WS.search([('comunicaciones_id', '=', rec.id)])
                if ws.drive_file_id
            }

            pending = list(drive_files)
            created_any = True
            while pending and created_any:
                created_any = False
                remaining = []
                for df in pending:
                    if df.id in ws_by_df:
                        continue
                    parent_ws = False
                    if df.parent_folder_id:
                        parent_ws = ws_by_df.get(df.parent_folder_id.id)
                        if not parent_ws:
                            remaining.append(df)
                            continue
                    ws = WS.create({
                        'name': df.folder_name or df.name or 'Carpeta',
                        'comunicaciones_id': rec.id,
                        'drive_file_id': df.id,
                        'parent_id': parent_ws.id if parent_ws else False,
                    })
                    ws_by_df[df.id] = ws
                    created_any = True
                pending = remaining

            if ws_by_df and not rec.selected_workspace_id:
                workspaces = list(ws_by_df.values())
                roots = [ws for ws in workspaces if rec._is_root_comunicaciones_workspace(ws)]
                selected = roots[0] if roots else workspaces[0]
                rec.write({'selected_workspace_id': selected.id})

    def web_read(self, specification):
        self._sync_workspace_from_drive_files()
        return super().web_read(specification)
    
    @api.depends(
        "selected_workspace_id",
        "comunicaciones_ids.page_range",
        "comunicaciones_ids.page_total",
    )
    def compute_folios_preview(self):
        for rec in self:
            folio_actual = 1
            ultimo_folio = 0

            for archivo in rec.comunicaciones_ids:
                pr = archivo.page_range
                if pr and "-" in pr:
                    ini, fin = pr.split("-", 1)
                    if ini.isdigit() and fin.isdigit():
                        paginas = int(fin) - int(ini) + 1
                        folio_fin = folio_actual + paginas - 1
                        ultimo_folio = folio_fin
                        folio_actual = folio_fin + 1

            rec.folios = f"1-{ultimo_folio}" if ultimo_folio else False

    def action_clear_workspace(self):
        self.ensure_one()
        if not self.selected_workspace_id:
            raise UserError("Debes seleccionar una carpeta.")

        workspace = self.selected_workspace_id
        drive_file = workspace.drive_file_id

        # Eliminar documentos de inventario DE COMUNICACIONES
        documentos = self.env['documento_archivo_comunicaciones'].search([
            ('inventario', '=', drive_file.id),
            ('comunicaciones', '=', self.id),
        ])
        if documentos:
            documentos.unlink()

        workspace.unlink()
        if drive_file:
            drive_file.unlink()

        self.selected_workspace_id = False
        self.detalles_carpetas = False

        return {'type': 'ir.actions.act_window_close'}

    def action_open_inventario(self):
        self.ensure_one()
        if not self.selected_workspace_id:
            raise UserError("Debes seleccionar una carpeta antes de abrir Inventario.")

        carpeta = self.selected_workspace_id.drive_file_id

        # Usamos el modelo espejo 'documento_archivo_comunicaciones'
        documento = self.env['documento_archivo_comunicaciones'].search([
            ('inventario', '=', carpeta.id),
            ('comunicaciones', '=', self.id),
        ], limit=1)

        if not documento:
            documento = self.env['documento_archivo_comunicaciones'].create({
                'inventario': carpeta.id,
                'comunicaciones': self.id,
                'fecha': fields.Date.today(),
            })

        documento.linea_ids.unlink()

        lineas_a_crear = []
        folio_actual = 1

        for archivo in self.comunicaciones_ids:
            paginas = 0
            page_range = archivo.page_range
            if page_range:
                if '-' in page_range:
                    ini, fin = page_range.split('-')
                    if ini.isdigit() and fin.isdigit():
                        paginas = int(fin) - int(ini) + 1
                elif page_range.isdigit():
                    paginas = int(page_range)

            folio_inicio = folio_actual
            folio_fin = folio_inicio + paginas - 1
            folios_conteo_val = f"{folio_inicio}-{folio_fin}"
            folio_actual = folio_fin + 1
            fecha_archivo = archivo.fecha or fields.Date.today()

            lineas_a_crear.append({
                'documento_id': documento.id,
                'dia': str(fecha_archivo.day).zfill(2),
                'mes': fecha_archivo.month,
                'anio': fecha_archivo.year,
                'tipo_documental': archivo.name or archivo.file_name or "Documento sin nombre",
                'numero_consecutivo': folios_conteo_val,
                'folios': page_range or '',
                'folios_conteo': folios_conteo_val,
            })

        if lineas_a_crear:
            self.env['documento_archivo_comunicaciones_linea'].create(lineas_a_crear)

        return {
            'name': 'Hoja de control',
            'type': 'ir.actions.act_window',
            'res_model': 'documento_archivo_comunicaciones',
            'view_mode': 'form',
            'res_id': documento.id,
            'target': 'current',
        }

    detalles_carpetas = fields.Boolean(string="Detalles", default=False)
    search_query = fields.Char(string='Buscar Documentos')
        
    @api.depends('comunicaciones_ids', 'comunicaciones_ids.page_range', 'sync_version', 'selected_workspace_id', 'search_query')
    def _compute_documentos_filtrados(self):
        for record in self:
            if not record.selected_workspace_id:
                record.comunicaciones_ids = [(5, 0, 0)]
                record.comunicaciones_archivo_ids = [(5, 0, 0)]
                continue

            domain = [
                ('stage2_id', '=', record.selected_workspace_id.drive_file_id.id)
            ]

            if record.search_query:
                domain.append(('name', 'ilike', record.search_query))

            documentos = self.env['drive'].search(domain)
            record.comunicaciones_ids = [(6, 0, documentos.ids)]
            record.comunicaciones_archivo_ids = [(6, 0, documentos.filtered(lambda d: not d.is_folder).ids)]
     
    def action_open_drive_form(self):
        self.ensure_one()
        if not self.selected_workspace_id:
            raise UserError("Debes seleccionar una carpeta.")

        carpeta = self.selected_workspace_id.drive_file_id
        view_id = self.env.ref('Matriz.view_drive_form_alternative').id

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_stage2_id': carpeta.id,
                'default_folder_id': carpeta.folder_id2,
                'default_proyecto_relacionado': self.proyecto_relacionado.id if self.proyecto_relacionado else False,
                'default_comunicaciones_id': self.id, # IMPORTANTE: Pasar comunicaciones_id
                'default_invisible_todo': True,
                'from_process_button': True,
            }
        }

    def _get_drive_browser_workspace_for_upload(self):
        self.ensure_one()
        if not self.proyecto_relacionado:
            raise UserError("No se encontró el proyecto relacionado.")
        if not self.selected_workspace_id:
            raise UserError("Debes seleccionar una carpeta.")

        browser = self.env['drive.browser'].sudo().get_or_create_browser(
            project_id=self.proyecto_relacionado.id,
            sync=False,
        )
        BrowserWS = self.env['drive.browser.workspace'].sudo()
        selected_ws = self.selected_workspace_id.sudo()
        drive_file = selected_ws.drive_file_id
        folder_google_id = drive_file.folder_id2 if drive_file else False
        if not folder_google_id:
            raise UserError("La carpeta seleccionada no tiene ID de Google Drive.")

        browser_ws = BrowserWS.search([
            ('browser_id', '=', browser.id),
            ('comunicaciones_workspace_id', '=', self.selected_workspace_id.id),
        ], limit=1)

        if not browser_ws:
            browser_ws = BrowserWS.search([
                ('browser_id', '=', browser.id),
                ('folder_google_id', '=', folder_google_id),
            ], limit=1)

        if not browser_ws:
            parent_browser_ws = False
            if selected_ws.parent_id and selected_ws.parent_id.drive_file_id:
                parent_browser_ws = BrowserWS.search([
                    ('browser_id', '=', browser.id),
                    ('folder_google_id', '=', selected_ws.parent_id.drive_file_id.folder_id2),
                ], limit=1)
            browser_ws = BrowserWS.create({
                'browser_id': browser.id,
                'name': selected_ws.name or drive_file.folder_name or 'Carpeta',
                'parent_id': parent_browser_ws.id if parent_browser_ws else False,
                'drive_file_id': drive_file.id,
                'comunicaciones_workspace_id': selected_ws.id,
                'folder_google_id': folder_google_id,
                'parent_folder_google_id': drive_file.folder_guardar_id or False,
            })

        if not browser_ws:
            raise UserError("No se encontró la carpeta seleccionada en el navegador de Drive.")
        browser.workspace_tree_ids.write({"is_selected": False})
        browser_ws.write({"is_selected": True})
        browser.write({'selected_workspace_id': browser_ws.id})
        return browser, browser_ws

    def _action_open_upload_wizard(self, upload_mode):
        browser, browser_ws = self._get_drive_browser_workspace_for_upload()
        return {
            'name': 'Subir Carpeta a Drive' if upload_mode == 'folder' else 'Subir a Drive',
            'type': 'ir.actions.act_window',
            'res_model': 'drive.browser.upload',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_browser_id': browser.id,
                'default_workspace_id': browser_ws.id,
                'default_upload_mode': '%s|infcom_reload' % upload_mode,
            },
        }

    def action_upload_file(self):
        return self._action_open_upload_wizard('file')

    def action_upload_folder(self):
        return self._action_open_upload_wizard('folder')

    def action_upload_drop_items(self, folder_data=None):
        self.ensure_one()
        folder_data = dict(folder_data or {})
        upload_mode = (folder_data.get('upload_mode') or 'file').split('|')[0]
        items = folder_data.get('items') or []
        if not items:
            raise UserError("No se recibieron archivos para subir.")

        browser, browser_ws = self._get_drive_browser_workspace_for_upload()
        upload_data = {
            'workspace_id': browser_ws.id,
            'items': items,
            'upload_mode': '%s|infcom_reload' % upload_mode,
        }
        result = browser.sudo().action_upload_folder_structure(folder_data=upload_data) or {}
        if isinstance(result, dict):
            result['browser_id'] = browser.id
            result['workspace_id'] = browser_ws.id
        return result

    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado', ondelete='set null')    
    workspace_selected = fields.Boolean(compute="_compute_workspace_selected")

    @api.depends('workspace')
    def _compute_workspace_selected(self):
        for record in self:
            record.workspace_selected = bool(record.workspace)

    def action_create_folder(self):
        """
        Crea una nueva carpeta en Drive dentro de la carpeta principal de COMUNICACIONES.
        """
        # 🔹 Busca COMUNICACIONES en lugar de INFORMES
        carpeta_comunicaciones = self.env['drive_stage'].search([
            ('name', 'in', ['2. COMUNICACIONES', 'COMUNICACIONES']),
            ('proyecto_relacionado', '=', self.proyecto_relacionado.id)
        ], limit=1)

        if not carpeta_comunicaciones or not carpeta_comunicaciones.folder_id:
            raise UserError(
                "No se encontró la carpeta principal de COMUNICACIONES en Google Drive para este Proyecto Relacionado.")

        folder_id_comunicaciones = carpeta_comunicaciones.folder_id
        view_id = self.env.ref('Matriz.drive_file_form_alternative').id

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'drive.file',
            'view_mode': 'form',
            'view_id': view_id,
            'target': 'new',
            'context': {
                'default_drive_id': carpeta_comunicaciones.id,
                'default_proyecto_relacionado': self.proyecto_relacionado.id,
                'default_folder_id2': folder_id_comunicaciones,
                'default_invisible_todo': True,
                
                # ¡¡IMPORTANTE!!: Asegúrate que esta línea esté presente:
                'default_comunicaciones_id': self.id, 
            }
        }
    
    def action_create_subfolder(self):
            self.ensure_one()
            if not self.selected_workspace_id:
                raise UserError("Debes seleccionar una carpeta.")

            carpeta_padre = self.selected_workspace_id.drive_file_id
            view_id = self.env.ref('Matriz.drive_file_form_alternative').id

            return {
                'type': 'ir.actions.act_window',
                'res_model': 'drive.file',
                'view_mode': 'form',
                'view_id': view_id,
                'target': 'new',
                'context': {
                    'default_folder_id2': carpeta_padre.folder_id2,
                    'default_drive_id': carpeta_padre.drive_id.id,
                    'default_proyecto_relacionado': self.proyecto_relacionado.id,
                    'default_invisible_todo': True,
                    
                    # --- LÍNEAS AGREGADAS PARA CORREGIR SUBCARPETAS ---
                    'default_comunicaciones_id': self.id,
                    'default_parent_workspace_id': self.selected_workspace_id.id,
                }
            }

    detalles = fields.Boolean(string="Destalles")
    descrpcion_info = fields.Text(string="Descripción")
    fecha = fields.Date(string="Fecha")

    def update_detalles(self, drive_id):
        self.detalles = True
        self.detalles_carpetas = False
        drive_record = self.env['drive'].browse(drive_id)
        if drive_record:
            self.selected_drive_id = drive_record.id
            self.stage2_id = drive_record.stage2_id
            self.file_name = drive_record.file_name
            self.page_range_panel = drive_record.page_range
            self.user_id = drive_record.user_id
            self.descrpcion_info = drive_record.descrpcion_info
            self.fecha = drive_record.fecha
            self.selected_drive_activity_html = self._activity_html_for_drive(drive_record)
            self.preview_html = drive_record.preview_html

    def set_detalles_to_false(self):
        for record in self:
            record.detalles = False
            # Buscar registros en drive relacionados con comunicaciones
            drive_records = self.env['drive'].search([('comunicaciones_id', '=', record.id)])
            if drive_records:
                drive_records.write({'ejecutar_inf': False})

    def set_detalles_carpetas(self):
        self.write({
            'detalles_carpetas': False,
            'detail_workspace_id': False,
        })

    def cog(self):
        return

    def action_edit_folder(self):
        self.ensure_one()
        if not self.detail_workspace_id:
            raise UserError("Debes seleccionar una carpeta para editar.")

        # Obtenemos el registro real de drive.file
        carpeta = self.detail_workspace_id.drive_file_id
        
        # Usaremos una vista específica para editar (la crearemos en el paso 3)
        # para que el botón diga "Guardar Cambios" en lugar de "Crear Carpeta"
        view_id = self.env.ref('Matriz.drive_file_form_edit').id

        return {
            'name': 'Editar Carpeta',
            'type': 'ir.actions.act_window',
            'res_model': 'drive.file',
            'view_mode': 'form',
            'view_id': view_id,
            'res_id': carpeta.id, # IMPORTANTE: Pasamos el ID existente para editar
            'target': 'new',
            'context': {
                'default_invisible_todo': True, # Mantenemos el estilo limpio
            }
        }

# MODELO ESPEJO PARA LA HOJA DE CONTROL DE COMUNICACIONES
class DocumentoArchivoComunicaciones(models.Model):
    _name = 'documento_archivo_comunicaciones'
    _description = 'Hoja de Control de Comunicaciones'

    inventario = fields.Many2one('drive.file', string='Inventario')
    fecha = fields.Date(string='Fecha Principal', default=fields.Date.today)
    dia_header = fields.Char(string='Día')
    mes_header = fields.Char(string='Mes')
    anio_header = fields.Char(string='Año')
    seccion_subseccion = fields.Char(string='Sección/Subsección')
    serie_subserie = fields.Char(string='Serie/Subserie')
    asunto_expediente = fields.Char(string='Asunto/Expediente')
    
    # Relación con el nuevo modelo Comunicaciones
    comunicaciones = fields.Many2one('comunicaciones_drive', string='Comunicación Relacionada')

    linea_ids = fields.One2many('documento_archivo_comunicaciones_linea', 'documento_id', string='Líneas de Documentos')
    firma_elaboracion = fields.Char(string='Firma y Fecha de Elaboración')

    def name_get(self):
            return [(record.id, "Hoja de control Comunicaciones") for record in self]
    
    def action_descargar_excel(self):
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
        return self._create_attachment_download(file_content)
    
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
        return {
            '{{dia}}': self.dia_header or '',
            '{{mes}}': self.mes_header or '',
            '{{anio}}': self.anio_header or '',
            '{{seccion}}': self.seccion_subseccion or '',
            '{{serie}}': self.serie_subserie or '',
            '{{asunto}}': self.asunto_expediente or '',
            '{{firma}}': self.firma_elaboracion or '',
        }

    def _safe_write(self, ws, row, col, value):
            cell = ws.cell(row=row, column=col)
            if not isinstance(cell, MergedCell):
                cell.value = value
                return
            for merged_range in ws.merged_cells.ranges:
                if cell.coordinate in merged_range:
                    top_left_cell = ws.cell(row=merged_range.min_row, column=merged_range.min_col)
                    top_left_cell.value = value
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
        template_path = get_module_resource('Matriz', 'static/src/xls', 'FORMATO HOJA DE CONTROL.xlsx')
        if not template_path or not os.path.isfile(template_path):
            raise UserError(f"No se encontró la plantilla en la ruta: {template_path}")
        return load_workbook(template_path)
    
    def _create_attachment_download(self, file_content):
        attachment = self.env['ir.attachment']._drive_create_temp_download({
            'name': f"Hoja_Control_Comunicaciones_{self.id}.xlsx",
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

class DocumentoArchivoComunicacionesLinea(models.Model):
    _name = 'documento_archivo_comunicaciones_linea'
    _description = 'Detalle de Documentos en Comunicaciones'

    documento_id = fields.Many2one('documento_archivo_comunicaciones', string='Documento')

    dia = fields.Char(string='Día')
    mes = fields.Integer(string='Mes')
    anio = fields.Integer(string='Año')
    tipo_documental = fields.Char(string='Tipos Documentales')
    numero_consecutivo = fields.Char(string='Número Consecutivo')
    folios = fields.Char(string='N° hojas')
    folios_conteo = fields.Char(string='Folios', readonly=True)

class ComunicacionesWorkspace(models.Model):
    _name = 'comunicaciones.workspace'
    _description = 'Workspace Navegación Comunicaciones'
    _parent_name = 'parent_id'
    _parent_store = True
    _order = 'parent_path, name'

    name = fields.Char(string='Nombre', required=True)

    # Relación con Comunicaciones
    comunicaciones_id = fields.Many2one(
        'comunicaciones_drive',
        string='Comunicación',
        ondelete='cascade',
        required=True,
    )

    drive_file_id = fields.Many2one(
        'drive.file',
        string='Carpeta Drive',
        required=True,
        ondelete='cascade',
    )
    fecha = fields.Date(
        string='Fecha',
        related='drive_file_id.fecha',
        store=False,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Creador',
        related='drive_file_id.user_id',
        store=False,
    )
    parent_display_name = fields.Char(
        string='Ubicación',
        compute='_compute_parent_display_name',
        store=False,
    )

    parent_id = fields.Many2one(
        'comunicaciones.workspace',
        string='Carpeta padre',
        ondelete='cascade',
        index=True
    )

    child_ids = fields.One2many(
        'comunicaciones.workspace',
        'parent_id',
        string='Subcarpetas'
    )

    parent_path = fields.Char(index=True)
    depth = fields.Integer(string='Nivel', compute='_compute_depth', store=False)

    last_archive_line_id = fields.Many2one(
        'documento_archivo_comunicaciones_linea',
        string='Última línea de inventario',
        readonly=True,
    )

    folios = fields.Char(string='Folios', readonly=True)
    is_selected = fields.Boolean(default=False)

    @api.depends('parent_path')
    def _compute_depth(self):
        for rec in self:
            rec.depth = max(len([p for p in (rec.parent_path or '').strip('/').split('/') if p]) - 1, 0)

    @api.depends('parent_id.name')
    def _compute_parent_display_name(self):
        for rec in self:
            rec.parent_display_name = rec.parent_id.name or ''

    def action_select_workspace(self):
        self.ensure_one()
        # Desmarcamos todas las carpetas de esta comunicación
        self.env['comunicaciones.workspace'].search([
            ('comunicaciones_id', '=', self.comunicaciones_id.id),
            ('is_selected', '=', True)
        ]).write({'is_selected': False})

        # Marcar la actual
        self.write({'is_selected': True})

        # Actualizar el padre (Comunicaciones)
        self.comunicaciones_id.write({
            'selected_workspace_id': self.id,
            'detail_workspace_id': self.id,
            'detalles_carpetas': True,
            'detalles': False,
            'selected_folder_activity_html': self.comunicaciones_id._activity_html_for_folder(self.drive_file_id),
        })
        return True

    def toggle_sidebar_info(self):
        self.ensure_one()
        self.comunicaciones_id.write({
            'detail_workspace_id': self.id,
            'detalles_carpetas': True,
            'detalles': False,
            'selected_folder_activity_html': self.comunicaciones_id._activity_html_for_folder(self.drive_file_id),
        })
        return True

    def action_open_folder(self):
        return self.action_select_workspace()

    def action_download_folder(self):
        self.ensure_one()
        if not self.drive_file_id:
            raise UserError("La carpeta no está sincronizada con Google Drive.")
        return self.drive_file_id.download_drive_folder()

    def _check_root_delete_allowed(self):
        if (
            self.env.context.get('_drv_from_model_delete')
            or self.env.context.get('_drv_from_secondary_mirror_delete')
            or self.env.context.get('_drv_from_ws_delete')
            or self.env.context.get('_drv_from_df_delete')
        ):
            return
        if self.filtered(lambda folder: folder.comunicaciones_id._is_root_comunicaciones_workspace(folder)) and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError("Solo el grupo de soporte puede eliminar la carpeta raíz de Comunicaciones.")

    def action_delete_folder(self):
        self.ensure_one()
        self._check_root_delete_allowed()
        comunicacion = self.comunicaciones_id
        if not self.drive_file_id:
            self.unlink()
        else:
            self.drive_file_id.action_delete_from_form()
        if comunicacion.exists():
            comunicacion.write({'sync_version': comunicacion.sync_version + 1})
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def unlink(self):
        self._check_root_delete_allowed()
        return super().unlink()

    def action_create_subfolder_inline(self):
            self.ensure_one()
            if not self.drive_file_id or not self.drive_file_id.folder_id2:
                raise UserError("La carpeta padre no está sincronizada con Google Drive.")

            carpeta_padre_drive = self.drive_file_id
            comunicacion = self.comunicaciones_id
            view_id = self.env.ref('Matriz.drive_file_form_alternative').id

            return {
                'name': f'Crear subcarpeta en: {self.name}',
                'type': 'ir.actions.act_window',
                'res_model': 'drive.file',
                'view_mode': 'form',
                'view_id': view_id,
                'target': 'new',
                'context': {
                    'default_folder_guardar_id': carpeta_padre_drive.folder_id2,
                    'default_folder_id2': carpeta_padre_drive.folder_id2,
                    'default_drive_id': carpeta_padre_drive.drive_id.id,
                    'default_proyecto_relacionado': comunicacion.proyecto_relacionado.id,
                    'default_invisible_todo': True,
                    
                    # --- LÍNEAS DESCOMENTADAS Y CORREGIDAS ---
                    'default_comunicaciones_id': comunicacion.id,
                    'default_parent_workspace_id': self.id, 
                }
            }

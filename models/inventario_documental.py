# -*- coding: utf-8 -*-
import base64
import io
import os
import tempfile
import logging
import re
from html import escape as html_escape

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_resource
from .drive import GoogleDriveAuth
from .decorador_firmas import seguimiento_firmas

try:
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Border, Font, Side
except ImportError:
    load_workbook = None
    Alignment = Border = Font = Side = None

_logger = logging.getLogger(__name__)

_INVENTARIO_BASE_NAME = "Inventario documental.xlsx"
_INVENTARIO_TEMPLATE_NAME = "FORMATO INVENTARIO DOCUMENTAL.xlsx"
_INVENTARIO_SKIP_AUTO_SYNC = "skip_inventario_drive_sync"
_INVENTARIO_SKIP_BROWSER_SYNC = "skip_inventario_browser_sync"
_INVENTARIO_PARENT_WRITE = "inventario_parent_write"
_INVENTARIO_PARENT_FIELDS = {
    "referencia",
    "workspace_id",
    "entidad_remitente",
    "entidad_productora",
    "unidad_administrativa",
    "unidad_productora",
    "hoja_actual",
    "hoja_total",
    "dia_header",
    "mes_header",
    "anio_header",
    "fecha_entrada",
    "registro_transferencia",
    "obj_trans_primaria",
    "obj_trans_secundaria",
    "obj_val_fondos",
    "obj_fusion",
    "obj_inv_indiv",
    "linea_ids",
    "elaborado_nombre",
    "elaborado_oficina",
    "elaborado_cargo",
    "elaborado_firma",
    "elaborado_firmado",
    "elaborado_log_id",
    "elaborado_firma_hash",
    "entregado_nombre",
    "entregado_oficina",
    "entregado_cargo",
    "entregado_firma",
    "entregado_firmado",
    "entregado_log_id",
    "entregado_firma_hash",
    "recibido_nombre",
    "recibido_oficina",
    "recibido_cargo",
    "recibido_firma",
    "recibido_firmado",
    "recibido_log_id",
    "recibido_firma_hash",
}

class InventarioDocumental(models.Model):
    _name = 'inventario.documental'
    _description = 'Inventario Documental'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    referencia = fields.Char(string='Ref', required=True, copy=False, readonly=True, default='FU-F-SGD-003')
    workspace_id = fields.Many2one('gestion.documental.workspace', string='Carpeta', ondelete='set null')
    drive_id = fields.Many2one('drive', string='Inventario en Drive', readonly=True, tracking=True)
    drive_url = fields.Char(string='URL Inventario Drive', readonly=True, tracking=True)
    project_id = fields.Many2one(
        'contratacion',
        string='Proyecto',
        related='workspace_id.project_id',
        store=True,
        readonly=True,
    )
    project_user_ids = fields.Many2many(
        'res.users',
        string='Usuarios permitidos del proyecto',
        related='project_id.users_with_access',
        readonly=True,
    )
    permission_ids = fields.One2many(
        'inventario.documental.permiso',
        'inventario_id',
        string='Permisos Odoo',
    )
    can_edit_inventory = fields.Boolean(
        string='Puede editar inventario',
        compute='_compute_current_user_inventory_permissions',
    )
    can_comment_inventory = fields.Boolean(
        string='Puede comentar inventario',
        compute='_compute_current_user_inventory_permissions',
    )
    can_observe_inventory = fields.Boolean(
        string='Puede editar observaciones',
        compute='_compute_current_user_inventory_permissions',
    )
    
    entidad_remitente = fields.Char(string='Entidad Remitente', tracking=True)
    entidad_productora = fields.Char(string='Entidad Productora', tracking=True)
    unidad_administrativa = fields.Char(string='Unidad Administrativa', tracking=True)
    unidad_productora = fields.Char(string='Unidad Productora (Regalías)', tracking=True)
    
    hoja_actual = fields.Integer(string="Hoja Actual", default=1)
    hoja_total = fields.Integer(string="Total Hojas", default=1)
    dia_header = fields.Char(string='Día')
    mes_header = fields.Char(string='Mes')
    anio_header = fields.Char(string='Año')
    fecha_entrada = fields.Date(string='Fecha Entrada')
    registro_transferencia = fields.Char(string='Transferencia')
    
    obj_trans_primaria = fields.Boolean(string="Transferencia Primaria")
    obj_trans_secundaria = fields.Boolean(string="Transferencia Secundaria")
    obj_val_fondos = fields.Boolean(string="Valoración de fondos")
    obj_fusion = fields.Boolean(string="Fusión y supresión")
    obj_inv_indiv = fields.Boolean(string="Inventarios individuales")

    linea_ids = fields.One2many('inventario.documental.linea', 'inventario_id', string='Líneas')

    # --- CAMPOS DE FIRMAS (AHORA DENTRO DE LA CLASE) ---
    elaborado_nombre = fields.Char(string='Nombre Elaborado')
    elaborado_oficina = fields.Char(string='Oficina Elaborado')
    elaborado_cargo = fields.Char(string='Cargo Elaborado')
    elaborado_firma = fields.Binary(string='Firma Elaborado')
    elaborado_firmado = fields.Boolean(string='Firmado Elaborado', tracking=True)
    elaborado_log_id = fields.Many2one('signature.mixin', string='Log Firma Elaborado', readonly=True)
    elaborado_firma_hash = fields.Char(string='Hash Firma Elaborado', readonly=True, copy=False, tracking=True)

    entregado_nombre = fields.Char(string='Nombre Entregado')
    entregado_oficina = fields.Char(string='Oficina Entregado')
    entregado_cargo = fields.Char(string='Cargo Entregado')
    entregado_firma = fields.Binary(string='Firma Entregado')
    entregado_firmado = fields.Boolean(string='Firmado Entregado', tracking=True)
    entregado_log_id = fields.Many2one('signature.mixin', string='Log Firma Entregado', readonly=True)
    entregado_firma_hash = fields.Char(string='Hash Firma Entregado', readonly=True, copy=False, tracking=True)

    recibido_nombre = fields.Char(string='Nombre Recibido')
    recibido_oficina = fields.Char(string='Oficina Recibido')
    recibido_cargo = fields.Char(string='Cargo Recibido')
    recibido_firma = fields.Binary(string='Firma Recibido')
    recibido_firmado = fields.Boolean(string='Firmado Recibido', tracking=True)
    recibido_log_id = fields.Many2one('signature.mixin', string='Log Firma Recibido', readonly=True)
    recibido_firma_hash = fields.Char(string='Hash Firma Recibido', readonly=True, copy=False, tracking=True)

    def name_get(self):
        return [(rec.id, f"Inventario - {rec.workspace_id.name}" if rec.workspace_id else f"Inventario #{rec.id}") for rec in self]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_header_defaults_from_project()
        return records

    def write(self, vals):
        vals = dict(vals)
        self._prepare_signature_checks(vals)
        return self._write_with_signature_tracking(vals)

    # Trazabilidad de firmas (ver seguimiento_firmas en decorador_firmas.py):
    # 3 slots — Elaborado por, Entregado por y Recibido por. El decorador ya
    # llama a este método una vez POR REGISTRO (self viene single-record),
    # así que `records = self.with_context(...)` de abajo también queda
    # limitado a ese único registro.
    @seguimiento_firmas([
        {
            'name': 'Elaborado por',
            'img': 'elaborado_firma',
            'check': 'elaborado_firmado',
            'log': 'elaborado_log_id',
            'hash': 'elaborado_firma_hash',
        },
        {
            'name': 'Entregado por',
            'img': 'entregado_firma',
            'check': 'entregado_firmado',
            'log': 'entregado_log_id',
            'hash': 'entregado_firma_hash',
        },
        {
            'name': 'Recibido por',
            'img': 'recibido_firma',
            'check': 'recibido_firmado',
            'log': 'recibido_log_id',
            'hash': 'recibido_firma_hash',
        },
    ])
    def _write_with_signature_tracking(self, vals):
        self._check_can_write_inventory(vals)
        should_sync = self._should_auto_sync_to_drive(vals)
        sync_from_lines = should_sync and 'linea_ids' in vals
        records = self.with_context(**{_INVENTARIO_PARENT_WRITE: True})
        res = super(InventarioDocumental, records).write(vals)
        if should_sync:
            _logger.info(
                "INVENTARIO AUTO SYNC | Actualizando plantilla por cambios en: %s | registros=%s",
                sorted(_INVENTARIO_PARENT_FIELDS.intersection(vals)),
                self.ids,
            )
            if sync_from_lines:
                self._auto_sync_excel_to_drive_from_lines()
            else:
                self._auto_sync_excel_to_drive()
        return res

    def _prepare_signature_checks(self, vals):
        signature_name_map = {
            'elaborado_nombre': 'elaborado_firmado',
            'entregado_nombre': 'entregado_firmado',
            'recibido_nombre': 'recibido_firmado',
        }
        for name_field, check_field in signature_name_map.items():
            if name_field in vals and check_field not in vals:
                vals[check_field] = bool((vals.get(name_field) or '').strip())

    @api.onchange('elaborado_nombre', 'entregado_nombre', 'recibido_nombre')
    def _onchange_signature_names(self):
        for rec in self:
            rec.elaborado_firmado = bool((rec.elaborado_nombre or '').strip())
            rec.entregado_firmado = bool((rec.entregado_nombre or '').strip())
            rec.recibido_firmado = bool((rec.recibido_nombre or '').strip())

    @api.depends('permission_ids.role')
    def _compute_current_user_inventory_permissions(self):
        for rec in self:
            role = rec._get_current_user_permission_role()
            rec.can_edit_inventory = role == 'writer'
            rec.can_comment_inventory = role in ('commenter', 'writer')
            rec.can_observe_inventory = role in ('observer', 'writer')

    def _can_manage_inventory_all(self):
        user = self.env.user
        return (
            user.has_group('Matriz.group_soporte')
            or user.has_group('Matriz.group_gestion_documental')
        )

    def _can_user_access_inventory(self):
        self.ensure_one()
        user = self.env.user
        return (
            self._can_manage_inventory_all()
            or self.create_uid == user
            or bool(self.sudo().permission_ids.filtered(lambda permission: permission.user_id == user))
        )

    def _get_current_user_permission_role(self):
        self.ensure_one()
        user = self.env.user
        if self._can_manage_inventory_all() or self.create_uid == user:
            return 'writer'
        permission = self.sudo().permission_ids.filtered(lambda item: item.user_id == user)[:1]
        return permission.role if permission else False

    def check_access_rule(self, operation):
        res = super().check_access_rule(operation)
        if self.env.su:
            return res
        blocked = self.filtered(lambda rec: not rec._can_user_access_inventory())
        if blocked:
            raise AccessError(_(
                "No tienes acceso a este inventario. Primero deben compartirte el formulario desde el botón Compartir formulario Odoo."
            ))
        return res

    def _can_current_user_edit_inventory_content(self):
        self.ensure_one()
        return self._get_current_user_permission_role() == 'writer'

    def _check_can_write_inventory(self, vals):
        if self.env.su or self.env.context.get(_INVENTARIO_SKIP_AUTO_SYNC):
            return
        protected_fields = _INVENTARIO_PARENT_FIELDS.intersection(vals)
        if not protected_fields:
            return
        if protected_fields == {'linea_ids'}:
            blocked_limited = self.filtered(
                lambda rec: not rec._line_commands_are_allowed_for_role(vals.get('linea_ids'))
            )
            if not blocked_limited:
                return
        blocked = self.filtered(lambda rec: not rec._can_current_user_edit_inventory_content())
        if blocked:
            raise UserError(_(
                "No tienes permiso de Editor para modificar este inventario. "
                "Pide que te compartan el formulario con permiso Editor."
            ))

    def _line_commands_are_allowed_for_role(self, commands):
        role = self._get_current_user_permission_role()
        if role == 'writer':
            return True
        allowed_fields_by_role = {
            'commenter': {'comentario'},
            'observer': {'observaciones'},
        }
        allowed_fields = allowed_fields_by_role.get(role)
        if not allowed_fields:
            return False
        for command in commands or []:
            if not isinstance(command, (list, tuple)) or not command:
                return False
            operation = command[0]
            if operation != 1:
                return False
            values = command[2] if len(command) > 2 else {}
            if not isinstance(values, dict) or set(values) - allowed_fields:
                return False
        return True

    def action_generar_excel(self):
        self.ensure_one()
        if load_workbook is None:
            raise UserError("La librería 'openpyxl' no está instalada.")

        rec = self.with_context(**{_INVENTARIO_SKIP_AUTO_SYNC: True})
        rec._sync_lineas_desde_carpetas()
        file_content = rec._build_excel_content()

        rec._upload_excel_to_drive(file_content)
        return self._create_attachment_download(file_content)

    def action_compartir_drive(self):
        self.ensure_one()
        drive_record = self._get_inventory_drive_record()
        if not drive_record:
            raise UserError(_("Primero debes generar el inventario para subirlo a Google Drive."))
        return drive_record.drive_share()

    def _get_inventory_drive_record(self):
        self.ensure_one()
        linked_drive = self.sudo().drive_id
        if linked_drive:
            return linked_drive

        workspace = self.workspace_id
        drive_record = self.env['drive']
        if workspace:
            entry = self.env['gestion.documental.entry'].sudo().search([
                ('browser_id', '=', workspace.browser_id.id),
                ('parent_workspace_id', '=', workspace.id),
                ('entry_type', '=', 'file'),
                ('name', '=', _INVENTARIO_BASE_NAME),
            ], limit=1)
            drive_record = entry.drive_id

            if not drive_record and workspace.folder_google_id:
                drive_record = self.env['drive'].sudo().search([
                    ('folder_id', '=', workspace.folder_google_id),
                    '|',
                    ('file_name', '=', _INVENTARIO_BASE_NAME),
                    ('name', '=', _INVENTARIO_BASE_NAME),
                ], limit=1)

        if drive_record:
            drive_url = getattr(drive_record, 'file_url', False) or self.drive_url
            self.with_context(**{_INVENTARIO_SKIP_AUTO_SYNC: True}).sudo().write({
                'drive_id': drive_record.id,
                'drive_url': drive_url,
            })
        return drive_record

    def action_compartir_formulario_odoo(self):
        self.ensure_one()
        users = self.project_user_ids
        if not users:
            raise UserError(_("Este proyecto no tiene usuarios permitidos para compartir el formulario."))
        return {
            'name': _('Compartir formulario Odoo'),
            'type': 'ir.actions.act_window',
            'res_model': 'inventario.documental.compartir.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_inventario_id': self.id,
                'default_project_user_ids': [(6, 0, users.ids)],
                'allowed_user_ids': users.ids,
            },
        }

    def action_abrir_drive(self):
        self.ensure_one()
        if not self.drive_url:
            raise UserError(_("El inventario aún no tiene enlace de Google Drive."))
        return {
            'type': 'ir.actions.act_url',
            'url': self.drive_url,
            'target': 'new',
        }

    def action_actualizar_lineas_carpetas(self):
        for rec in self:
            rec._sync_header_defaults_from_project()
            rec.with_context(inventario_full_browser_sync=True)._refresh_lineas_y_subir_inventario()
        return True

    def _refresh_lineas_desde_carpetas_sin_subir(self):
        for rec in self.exists():
            rec._sync_header_defaults_from_project()
            rec.with_context(
                **{
                    _INVENTARIO_SKIP_AUTO_SYNC: True,
                }
            )._sync_lineas_desde_carpetas()

    def _refresh_lineas_y_subir_inventario(self):
        for rec in self.exists():
            rec.with_context(**{_INVENTARIO_SKIP_AUTO_SYNC: True})._sync_lineas_desde_carpetas()
        self._auto_sync_excel_to_drive()

    def _should_auto_sync_to_drive(self, vals):
        if self.env.context.get(_INVENTARIO_SKIP_AUTO_SYNC):
            return False
        return bool(_INVENTARIO_PARENT_FIELDS.intersection(vals))

    def _auto_sync_excel_to_drive(self):
        if load_workbook is None:
            _logger.warning("No se pudo actualizar inventario en Drive: openpyxl no está instalado.")
            return
        for rec in self.exists():
            if not rec.workspace_id:
                continue
            try:
                file_content = rec._build_excel_content()
                rec.with_context(**{_INVENTARIO_SKIP_AUTO_SYNC: True})._upload_excel_to_drive(
                    file_content,
                    post_message=False,
                )
            except Exception as exc:
                _logger.exception("No se pudo actualizar automáticamente el inventario %s en Drive: %s", rec.id, exc)
                rec.message_post(body=_("No se pudo actualizar automáticamente el inventario en Drive: %s") % exc)

    def _auto_sync_excel_to_drive_from_lines(self):
        if self.env.context.get(_INVENTARIO_SKIP_AUTO_SYNC):
            return
        _logger.info(
            "INVENTARIO AUTO SYNC | Actualizando plantilla por cambios en líneas | registros=%s",
            self.ids,
        )
        self._auto_sync_excel_to_drive()

    def _build_excel_content(self):
        self.ensure_one()
        if load_workbook is None:
            raise UserError("La librería 'openpyxl' no está instalada.")

        self._sync_header_defaults_from_project()
        wb = self._load_template()
        ws = wb.active
        self._fill_template(ws)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        file_content = output.read()
        output.close()
        return file_content

    def _sync_header_defaults_from_project(self):
        for rec in self.exists():
            default_vals = rec._get_header_default_vals()
            vals = {
                field_name: value
                for field_name, value in default_vals.items()
                if value and not rec[field_name]
            }
            if vals:
                rec.sudo().with_context(**{_INVENTARIO_SKIP_AUTO_SYNC: True}).write(vals)

    def _get_header_default_vals(self):
        self.ensure_one()
        return {
            'entidad_remitente': 'Fundación Universidad del Valle',
            'entidad_productora': 'Fundación Universidad del Valle',
            'unidad_administrativa': 'Dirección ejecutiva',
            'unidad_productora': self._get_project_unidad_productora(),
        }

    def _get_project_unidad_productora(self):
        self.ensure_one()
        info_base = self._get_project_info_base()
        if not info_base or not info_base.area_solicitante2:
            return ''

        selection = info_base._fields['area_solicitante2'].selection
        area_label = dict(selection).get(info_base.area_solicitante2, info_base.area_solicitante2)
        area_label = (area_label or '').strip()
        if not area_label:
            return ''
        return 'Unidad de %s' % area_label.lower()

    def _get_project_info_base(self):
        self.ensure_one()
        project = self.project_id or (self.workspace_id.project_id if self.workspace_id else False)
        if not project:
            return self.env['informacion_base_agregar']

        info_base = project.informacion_base_id[:1]
        if info_base:
            return info_base

        info_bases = project.informacion_base_agregar_ids
        return info_bases[:1] if info_bases else self.env['informacion_base_agregar']

    def _sync_lineas_desde_carpetas(self):
        self.ensure_one()
        if not self.workspace_id or not self.workspace_id.browser_id:
            _logger.info(
                "INVENTARIO DEBUG | inventario=%s sin workspace/browser | workspace=%s",
                self.id,
                self.workspace_id.id if self.workspace_id else False,
            )
            return

        browser = self.workspace_id.browser_id
        _logger.info(
            "INVENTARIO DEBUG | inicio sync | inventario=%s workspace=%s/%s browser=%s skip_browser=%s",
            self.id,
            self.workspace_id.id,
            self.workspace_id.display_name,
            browser.id,
            bool(self.env.context.get(_INVENTARIO_SKIP_BROWSER_SYNC)),
        )
        if not self.env.context.get(_INVENTARIO_SKIP_BROWSER_SYNC):
            try:
                browser_ctx = browser.with_context(**{_INVENTARIO_SKIP_BROWSER_SYNC: True})
                if self.env.context.get('inventario_full_browser_sync'):
                    browser_ctx._gd_sync_workspace_from_drive()
                else:
                    browser_ctx._gd_sync_workspace_for_inventory_quick()
            except Exception as exc:
                _logger.warning("No se pudo sincronizar carpetas antes del inventario %s: %s", self.id, exc)

        root_ws = self._get_inventory_root_workspace()
        if not root_ws:
            _logger.info("INVENTARIO DEBUG | inventario=%s sin root_ws", self.id)
            return

        direct_children = root_ws.sudo().child_ids
        _logger.info(
            "INVENTARIO DEBUG | raiz=%s/%s hijos_directos=%s",
            root_ws.id,
            root_ws.display_name,
            len(direct_children),
        )
        all_folder_rows = self._get_inventory_descendant_workspaces(root_ws)
        _logger.info(
            "INVENTARIO DEBUG | descendientes_encontrados=%s",
            len(all_folder_rows),
        )
        control_workspace_ids = set(self.env['documento_archivo_gd'].sudo().search([
            ('workspace_id', 'in', all_folder_rows.ids),
        ]).mapped('workspace_id').ids)
        file_workspace_ids = set(self.env['gestion.documental.entry'].sudo().search([
            ('parent_workspace_id', 'in', all_folder_rows.ids),
            ('entry_type', '=', 'file'),
        ]).mapped('parent_workspace_id').ids)
        folder_rows = all_folder_rows.filtered(
            lambda folder: self._include_folder_in_inventory(
                folder,
                control_workspace_ids=control_workspace_ids,
                file_workspace_ids=file_workspace_ids,
            )
        )
        folder_rows = folder_rows.sorted(key=lambda folder: self._get_folder_inventory_sort_key(folder, root_ws))
        _logger.info(
            "INVENTARIO DEBUG | carpetas_incluidas=%s muestra=%s",
            len(folder_rows),
            [(folder.id, self._get_folder_inventory_name(folder, root_ws)) for folder in folder_rows[:10]],
        )

        existing_by_workspace = {
            line.workspace_id.id: line
            for line in self.linea_ids
            if line.workspace_id
        }
        existing_by_name = {
            line.nombre_serie: line
            for line in self.linea_ids
            if not line.workspace_id and line.nombre_serie
        }
        used_line_ids = set()
        vals_list = []
        updated_lines = []
        for seq, folder in enumerate(folder_rows, start=1):
            folder_name = self._get_folder_inventory_name(folder, root_ws)
            line_vals = {
                'inventario_id': self.id,
                'workspace_id': folder.id,
                'numero_orden': seq,
                'nombre_serie': folder_name,
                'folios': self._get_folder_total_folios(folder),
                'es_digital': True,
            }
            existing_line = existing_by_workspace.get(folder.id) or existing_by_name.get(folder_name)
            if existing_line:
                existing_line.write(line_vals)
                used_line_ids.add(existing_line.id)
                updated_lines.append((existing_line.id, folder.id, folder_name))
            else:
                line_vals.update({
                    'codigo_trd': '',
                    'frecuencia_consulta': 'media',
                    'soporte_especial': 'no',
                })
                vals_list.append(line_vals)
        created_count = len(vals_list)
        if vals_list:
            created_lines = self.env['inventario.documental.linea'].create(vals_list)
            used_line_ids.update(created_lines.ids)

        missing_lines = self.linea_ids.filtered(
            lambda line: line.id not in used_line_ids
        )
        _logger.info(
            "INVENTARIO DEBUG | resultado | inventario=%s actualizadas=%s creadas=%s borradas=%s",
            self.id,
            len(updated_lines),
            created_count,
            len(missing_lines),
        )
        if missing_lines:
            missing_lines.unlink()

    def _get_inventory_root_workspace(self):
        self.ensure_one()
        return self.workspace_id

    def _get_inventory_descendant_workspaces(self, root_ws):
        descendants = self.env['gestion.documental.workspace'].sudo()
        pending = root_ws.sudo().child_ids.sorted(
            key=lambda folder: self._get_folder_inventory_sort_key(folder, root_ws)
        )
        while pending:
            folder = pending[0]
            pending -= folder
            descendants |= folder
            children = folder.child_ids.sorted(
                key=lambda child: self._get_folder_inventory_sort_key(child, root_ws)
            )
            pending |= children
        return descendants

    def _get_folder_inventory_name(self, folder, root_ws):
        ordered = self._get_folder_path_from_root(folder, root_ws)
        parts = []
        for workspace in ordered:
            if workspace.id == root_ws.id:
                continue
            clean_name = self._clean_folder_name(workspace.name)
            if clean_name:
                parts.append(clean_name)

        if parts and parts[0].upper() == 'CARPETA PRINCIPAL':
            root_name = self._clean_folder_name(root_ws.name)
            parts[0] = 'Carpeta principal (1)'
            return f"{root_name} -\n{' - '.join(parts)}" if root_name else ' - '.join(parts)

        return ' - '.join(parts) or self._clean_folder_name(folder.name) or _('Sin nombre')

    def _get_folder_path_from_root(self, folder, root_ws):
        Workspace = self.env['gestion.documental.workspace'].sudo()
        path_ids = []
        current = folder.sudo()
        seen_ids = set()
        while current and current.id not in seen_ids:
            seen_ids.add(current.id)
            path_ids.append(current.id)
            if current.id == root_ws.id:
                break
            current = current.parent_id

        if not path_ids or path_ids[-1] != root_ws.id:
            return folder
        return Workspace.browse(list(reversed(path_ids))).exists()

    def _include_folder_in_inventory(self, folder, control_workspace_ids=None, file_workspace_ids=None):
        clean_name = self._clean_folder_name(folder.name).upper()
        if clean_name == 'CARPETA PRINCIPAL':
            return True
        if self._folder_has_inventory_content(folder, control_workspace_ids, file_workspace_ids):
            return True
        return not bool(folder.child_ids)

    def _folder_has_inventory_content(self, folder, control_workspace_ids=None, file_workspace_ids=None):
        if control_workspace_ids is not None and folder.id in control_workspace_ids:
            return True
        if file_workspace_ids is not None and folder.id in file_workspace_ids:
            return True

        has_control_sheet = bool(self.env['documento_archivo_gd'].sudo().search_count([
            ('workspace_id', '=', folder.id),
        ]))
        if has_control_sheet:
            return True

        return bool(self.env['gestion.documental.entry'].sudo().search_count([
            ('parent_workspace_id', '=', folder.id),
            ('entry_type', '=', 'file'),
        ]))

    def _get_folder_inventory_sort_key(self, folder, root_ws):
        ordered = self._get_folder_path_from_root(folder, root_ws)
        return tuple(
            self._folder_name_sort_part(workspace.name)
            for workspace in ordered
            if workspace.id != root_ws.id
        )

    def _folder_name_sort_part(self, name):
        raw = (name or '').strip()
        prefix_match = re.match(r'^(\d+(?:\.\d+)*)', raw)
        prefix = tuple(int(part) for part in prefix_match.group(1).split('.')) if prefix_match else (9999,)
        return (prefix, self._natural_text_key(self._clean_folder_name(raw).lower()))

    def _natural_text_key(self, text):
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in re.split(r'(\d+)', text or '')
            if part
        )

    def _get_folder_total_folios(self, folder):
        documento = self.env['documento_archivo_gd'].sudo().search([
            ('workspace_id', '=', folder.id),
        ], limit=1)
        total = 0
        if documento:
            for line in documento.linea_ids:
                total += self._get_control_line_folios(line)
            if total:
                return total

        entries = self.env['gestion.documental.entry'].sudo().search([
            ('parent_workspace_id', '=', folder.id),
            ('entry_type', '=', 'file'),
        ])
        for entry in entries:
            drive_record = entry.drive_id
            if not drive_record:
                continue
            if drive_record.page_total:
                total += drive_record.page_total
                continue

            page_range = drive_record.page_range or ''
            if '-' in page_range:
                try:
                    start, end = page_range.split('-', 1)
                    if start.strip().isdigit() and end.strip().isdigit():
                        total += int(end.strip()) - int(start.strip()) + 1
                except (TypeError, ValueError):
                    continue
            elif page_range.isdigit():
                total += int(page_range)
        return total or False

    def _get_control_line_folios(self, line):
        for value in (line.folios, line.folios_conteo, line.numero_consecutivo):
            total = self._parse_folio_value(value)
            if total:
                return total
        return 0

    def _parse_folio_value(self, value):
        text = (value or '').strip()
        if not text:
            return 0
        if text.isdigit():
            return int(text)

        match = re.search(r'(\d+)\s*-\s*(\d+)', text)
        if not match:
            return 0

        start = int(match.group(1))
        end = int(match.group(2))
        if end < start:
            return 0
        return end - start + 1

    def _clean_folder_name(self, name):
        clean = re.sub(r'^\d+(?:\.\d+)*(?:\s*[\._-]\s*|\s+)', '', (name or '').strip())
        return clean or (name or '').strip()

    def _load_template(self):
        template_path = get_module_resource('Matriz', 'static/src/xls', _INVENTARIO_TEMPLATE_NAME)
        if not template_path or not os.path.isfile(template_path):
            raise UserError(f"No se encontró la plantilla en la ruta: {template_path}")
        return load_workbook(template_path)

    def _fill_template(self, ws):
        values = {
            '{{referencia}}': self.referencia or '',
            '{{entidad_remitente}}': self.entidad_remitente or '',
            '{{entidad_productora}}': self.entidad_productora or '',
            '{{unidad_administrativa}}': self.unidad_administrativa or '',
            '{{unidad_productora}}': self.unidad_productora or '',
            '{{hoja_actual}}': self.hoja_actual or '',
            '{{hoja_total}}': self.hoja_total or '',
            '{{dia}}': self.dia_header or '',
            '{{mes}}': self.mes_header or '',
            '{{anio}}': self.anio_header or '',
            '{{transferencia}}': self.registro_transferencia or '',
            '{{trans_primaria}}': 'X' if self.obj_trans_primaria else '',
            '{{trans_secundaria}}': 'X' if self.obj_trans_secundaria else '',
            '{{val_fondos}}': 'X' if self.obj_val_fondos else '',
            '{{fusion}}': 'X' if self.obj_fusion else '',
            '{{inv_indiv}}': 'X' if self.obj_inv_indiv else '',
            '{{elaborado_nombre}}': self.elaborado_nombre or '',
            '{{elaborado_oficina}}': self.elaborado_oficina or '',
            '{{elaborado_cargo}}': self.elaborado_cargo or '',
            '{{elaborado_hash}}': self._format_signature_hash_for_template(self.elaborado_firma_hash),
            '{{entregado_nombre}}': self.entregado_nombre or '',
            '{{entregado_oficina}}': self.entregado_oficina or '',
            '{{entregado_cargo}}': self.entregado_cargo or '',
            '{{entregado_hash}}': self._format_signature_hash_for_template(self.entregado_firma_hash),
            '{{recibido_nombre}}': self.recibido_nombre or '',
            '{{recibido_oficina}}': self.recibido_oficina or '',
            '{{recibido_cargo}}': self.recibido_cargo or '',
            '{{recibido_hash}}': self._format_signature_hash_for_template(self.recibido_firma_hash),
        }

        for row in ws.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str):
                    continue
                for key, value in values.items():
                    if key in cell.value:
                        cell.value = cell.value.replace(key, str(value))

        self._format_signature_footer(ws)

        start_row = self._get_template_data_start_row(ws)
        lines = self.linea_ids.sorted(key=lambda line: (line.numero_orden or 0, line.id or 0))
        self._ensure_template_data_rows(ws, start_row, len(lines))
        for idx, line in enumerate(lines):
            row = start_row + idx
            self._safe_write(ws, row, 1, line.numero_orden)
            self._safe_write(ws, row, 2, line.codigo_trd)
            self._safe_write(ws, row, 3, line.nombre_serie)
            self._safe_write(ws, row, 4, line.folios)
            self._safe_write(ws, row, 5, self._format_date(line.fecha_inicial))
            self._safe_write(ws, row, 6, self._format_date(line.fecha_final))
            self._safe_write(ws, row, 7, line.caja)
            self._safe_write(ws, row, 8, line.carpeta)
            self._safe_write(ws, row, 9, 'X' if line.es_digital else '')
            self._safe_write(ws, row, 10, line.otro_soporte)
            self._safe_write(ws, row, 11, dict(line._fields['frecuencia_consulta'].selection).get(line.frecuencia_consulta, ''))
            self._safe_write(ws, row, 12, dict(line._fields['soporte_especial'].selection).get(line.soporte_especial, ''))
            self._safe_write(ws, row, 13, line.observaciones)
        self._format_inventory_data_rows(ws, start_row, len(lines))

    def _format_signature_hash_for_template(self, value):
        return (value or '').strip()

    def _format_signature_footer(self, ws):
        if not all([Alignment, Border, Font, Side]):
            return

        footer_headers = ('Elaborado por', 'Entregado por', 'Recibido por')
        header_cells = [
            cell
            for row in ws.iter_rows()
            for cell in row
            if isinstance(cell.value, str) and cell.value.strip() in footer_headers
        ]
        if not header_cells:
            return

        start_row = min(cell.row for cell in header_cells)
        block_starts = [1, 5, 9]
        block_ends = [4, 8, 13]
        gap_cols = []
        thin = Side(style='thin', color='000000')
        no_border = Border()
        self._unmerge_signature_footer_area(ws, start_row)

        for row, height in {
            start_row: 14,
            start_row + 1: 15,
            start_row + 2: 15,
            start_row + 3: 15,
            start_row + 4: 54,
        }.items():
            ws.row_dimensions[row].height = height

        for col in block_starts:
            cell = ws.cell(start_row + 4, col)
            if isinstance(cell.value, str) and cell.value.startswith('Hash:'):
                cell.value = cell.value.replace('Hash:', 'Firma:', 1)

        for row in range(start_row, start_row + 5):
            for col in gap_cols:
                cell = ws.cell(row, col)
                cell.value = None
                cell.border = no_border
                cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=False)

        for row in range(start_row, start_row + 5):
            for col_start, col_end in zip(block_starts, block_ends):
                self._merge_footer_cell(ws, row, col_start, col_end)
                cell = ws.cell(row, col_start)
                cell.alignment = Alignment(
                    horizontal='center' if row == start_row else 'left',
                    vertical='center',
                    wrap_text=(row == start_row + 4),
                    shrink_to_fit=False,
                )
                cell.font = Font(
                    name='Arial',
                    size=7 if row == start_row + 4 else 8,
                    bold=(row == start_row),
                )
                cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def _unmerge_signature_footer_area(self, ws, start_row):
        end_row = start_row + 4
        for merged_range in list(ws.merged_cells.ranges):
            if (
                merged_range.min_row <= end_row
                and merged_range.max_row >= start_row
                and merged_range.min_col <= 13
                and merged_range.max_col >= 1
            ):
                ws.unmerge_cells(str(merged_range))

    def _merge_footer_cell(self, ws, row, col_start, col_end):
        target_ref = None
        for merged_range in list(ws.merged_cells.ranges):
            if (
                merged_range.min_row == row
                and merged_range.max_row == row
                and merged_range.min_col == col_start
                and merged_range.max_col == col_end
            ):
                return
            if (
                merged_range.min_row == row
                and merged_range.max_row == row
                and not (merged_range.max_col < col_start or merged_range.min_col > col_end)
            ):
                target_ref = target_ref or str(merged_range)
                ws.unmerge_cells(str(merged_range))
        if target_ref:
            value = ws.cell(row, col_start).value
            if value in (None, ''):
                for col in range(col_start + 1, col_end + 1):
                    value = ws.cell(row, col).value
                    if value not in (None, ''):
                        ws.cell(row, col_start).value = value
                        break
        ws.merge_cells(start_row=row, start_column=col_start, end_row=row, end_column=col_end)

    def _upload_excel_to_drive(self, file_content, post_message=True):
        self.ensure_one()
        workspace = self._resolve_upload_workspace()
        if not workspace or not workspace.folder_google_id:
            raise UserError(_("No se encontró una carpeta válida de Google Drive para subir el inventario."))

        folder_gid = workspace.folder_google_id
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        gdrive = GoogleDriveAuth.get_instance().get_drive()

        siblings = gdrive.ListFile({
            "q": f"'{folder_gid}' in parents and trashed=false",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }).GetList()
        existing = [
            item for item in siblings
            if (item.get("title") or item.get("name") or "").strip().lower() == _INVENTARIO_BASE_NAME.lower()
        ]

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
            os.close(fd)
            with open(tmp_path, "wb") as tmp:
                tmp.write(file_content)

            if existing:
                gfile = existing[0]
                gfile["title"] = _INVENTARIO_BASE_NAME
            else:
                gfile = gdrive.CreateFile({
                    "title": _INVENTARIO_BASE_NAME,
                    "parents": [{"id": folder_gid}],
                    "mimeType": mimetype,
                })
            gfile.SetContentFile(tmp_path)
            gfile.Upload(param={"supportsAllDrives": True})

            gid = gfile.get("id")
            link = gfile.get("alternateLink") or gfile.get("webViewLink")
            preview_url = gfile.get("embedLink") or link

            drive_vals = {
                "file_name": _INVENTARIO_BASE_NAME,
                "name": _INVENTARIO_BASE_NAME,
                "drive_file_id": gid,
                "file_id": gid,
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
            if workspace.project_id:
                drive_vals["proyecto_relacionado"] = workspace.project_id.id

            Drive = self.env["drive"].sudo()
            drive_rec = Drive.search([("drive_file_id", "=", gid)], limit=1)
            if drive_rec:
                drive_rec.write(drive_vals)
            else:
                drive_rec = Drive.create(drive_vals)

            self.with_context(**{_INVENTARIO_SKIP_AUTO_SYNC: True}).write({
                'drive_id': drive_rec.id,
                'drive_url': link,
            })

            Entry = self.env["gestion.documental.entry"].sudo().with_context(gd_sync=True)
            entry = Entry.search([
                ("browser_id", "=", workspace.browser_id.id),
                ("parent_workspace_id", "=", workspace.id),
                ("google_id", "=", gid),
            ], limit=1)
            entry_vals = {
                "browser_id": workspace.browser_id.id,
                "parent_workspace_id": workspace.id,
                "entry_type": "file",
                "name": _INVENTARIO_BASE_NAME,
                "google_id": gid,
                "mime_type": mimetype,
                "web_view_link": link,
                "drive_id": drive_rec.id,
            }
            if entry:
                entry.write(entry_vals)
            else:
                Entry.create(entry_vals)

            if post_message:
                self.message_post(body=f"Inventario documental generado y subido a Google Drive: <a href='{link}' target='_blank'>{_INVENTARIO_BASE_NAME}</a>")
        except Exception as exc:
            _logger.exception("No se pudo subir el inventario documental a Drive: %s", exc)
            raise UserError(_("No se pudo subir el inventario documental a Google Drive: %s") % exc)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _resolve_upload_workspace(self):
        self.ensure_one()
        workspace = self.workspace_id
        if not workspace:
            return workspace

        gdrive = GoogleDriveAuth.get_instance().get_drive()
        if self._drive_folder_exists(gdrive, workspace.folder_google_id):
            return workspace

        browser = workspace.browser_id
        if browser:
            try:
                browser._gd_sync_workspace_from_drive()
                refreshed = self._find_synced_workspace_by_path(workspace)
                if refreshed and self._drive_folder_exists(gdrive, refreshed.folder_google_id):
                    if refreshed.id != workspace.id:
                        self.with_context(**{_INVENTARIO_SKIP_AUTO_SYNC: True}).write({'workspace_id': refreshed.id})
                    return refreshed
            except Exception as sync_error:
                _logger.warning("No se pudo resincronizar inventario documental antes de subir: %s", sync_error)

        raise UserError(_(
            "La carpeta de Google Drive asociada a este inventario ya no existe o no tienes acceso. "
            "Sincroniza la gestión documental y vuelve a abrir el inventario desde la carpeta correcta."
        ))

    def _drive_folder_exists(self, gdrive, folder_id):
        if not folder_id:
            return False
        try:
            folder = gdrive.CreateFile({'id': folder_id})
            folder.FetchMetadata(fields='id,title,mimeType,labels')
            return not folder.get('labels', {}).get('trashed')
        except Exception:
            return False

    def _find_synced_workspace_by_path(self, old_workspace):
        names = []
        current = old_workspace
        while current:
            names.append(current.name)
            current = current.parent_id
        names.reverse()

        Workspace = self.env['gestion.documental.workspace'].sudo()
        current = Workspace.search([
            ('browser_id', '=', old_workspace.browser_id.id),
            ('parent_id', '=', False),
            ('name', '=', names[0]),
        ], limit=1)

        for name in names[1:]:
            if not current:
                return old_workspace
            current = Workspace.search([
                ('browser_id', '=', old_workspace.browser_id.id),
                ('parent_id', '=', current.id),
                ('name', '=', name),
            ], limit=1)
            if not current:
                return old_workspace
        return current or old_workspace

    def _create_attachment_download(self, file_content):
        attachment = self.env['ir.attachment']._drive_create_temp_download({
            'name': _INVENTARIO_BASE_NAME,
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

    def _format_date(self, value):
        return value.strftime('%d/%m/%Y') if value else ''

    def _get_template_data_start_row(self, ws):
        for row in range(1, ws.max_row + 1):
            values = [
                str(ws.cell(row, col).value or '').strip().lower()
                for col in range(1, ws.max_column + 1)
            ]
            if 'inicial' in values and 'final' in values:
                return row + 1
        return 14

    def _ensure_template_data_rows(self, ws, start_row, line_count):
        if line_count <= 0:
            return

        signature_start = self._get_signature_start_row(ws, start_row)
        if not signature_start:
            return

        data_template_end_row = self._get_template_data_end_row(ws, start_row, signature_start)
        available_rows = max(data_template_end_row - start_row + 1, 1)
        extra_rows = line_count - available_rows
        if extra_rows < 0:
            self._delete_rows_preserving_merged_cells(ws, start_row + line_count, abs(extra_rows))
            return
        if extra_rows == 0:
            return

        insert_row = data_template_end_row + 1
        self._insert_rows_preserving_merged_cells(ws, insert_row, extra_rows)
        for row in range(insert_row, insert_row + extra_rows):
            self._copy_row_style(ws, data_template_end_row, row)

    def _get_template_data_end_row(self, ws, start_row, signature_start):
        for row in range(signature_start - 1, start_row - 1, -1):
            if self._row_has_table_border(ws, row):
                return row
        return signature_start - 1

    def _row_has_table_border(self, ws, row):
        for col in range(1, 14):
            border = ws.cell(row, col).border
            sides = (border.left, border.right, border.top, border.bottom)
            if any(side and side.style for side in sides):
                return True
        return False

    def _get_signature_start_row(self, ws, start_row):
        signature_titles = {'elaborado por', 'entregado por', 'recibido por'}
        for row in range(start_row, ws.max_row + 1):
            values = {
                str(ws.cell(row, col).value or '').strip().lower()
                for col in range(1, ws.max_column + 1)
            }
            if values.intersection(signature_titles):
                return row

        candidates = [
            merged_range.min_row
            for merged_range in ws.merged_cells.ranges
            if merged_range.min_row > start_row
            and merged_range.max_col - merged_range.min_col >= 3
        ]
        return min(candidates) if candidates else False

    def _insert_rows_preserving_merged_cells(self, ws, row_idx, amount):
        from openpyxl.worksheet.cell_range import CellRange

        merged_to_shift = [
            str(merged_range)
            for merged_range in list(ws.merged_cells.ranges)
            if merged_range.min_row >= row_idx
        ]
        for ref in merged_to_shift:
            ws.unmerge_cells(ref)

        ws.insert_rows(row_idx, amount)

        for ref in merged_to_shift:
            shifted_range = CellRange(ref)
            shifted_range.shift(row_shift=amount, col_shift=0)
            ws.merge_cells(str(shifted_range))

    def _delete_rows_preserving_merged_cells(self, ws, row_idx, amount):
        from openpyxl.worksheet.cell_range import CellRange

        delete_end = row_idx + amount
        shifted_ranges = []
        for merged_range in list(ws.merged_cells.ranges):
            ref = str(merged_range)
            if merged_range.min_row >= delete_end:
                shifted_ranges.append(ref)
                ws.unmerge_cells(ref)

        ws.delete_rows(row_idx, amount)

        for ref in shifted_ranges:
            shifted_range = CellRange(ref)
            shifted_range.shift(row_shift=-amount, col_shift=0)
            ws.merge_cells(str(shifted_range))

    def _safe_write(self, ws, row, col, value):
        ws.cell(row=row, column=col).value = value if value not in (False, None) else ''

    def _copy_row_style(self, ws, source_row_idx, target_row_idx):
        from copy import copy
        ws.row_dimensions[target_row_idx].height = ws.row_dimensions[source_row_idx].height
        ws.row_dimensions[target_row_idx].hidden = False
        for col in range(1, ws.max_column + 1):
            source_cell = ws.cell(source_row_idx, col)
            target_cell = ws.cell(target_row_idx, col)
            if source_cell.has_style:
                target_cell.font = copy(source_cell.font)
                target_cell.border = copy(source_cell.border)
                target_cell.fill = copy(source_cell.fill)
                target_cell.number_format = copy(source_cell.number_format)
                target_cell.protection = copy(source_cell.protection)
                target_cell.alignment = copy(source_cell.alignment)

    def _format_inventory_data_rows(self, ws, start_row, line_count):
        if line_count <= 0:
            return

        from copy import copy
        from openpyxl.styles import Alignment, Border, Side

        thin = Side(style='thin', color='000000')
        data_border = Border(left=thin, right=thin, top=thin, bottom=thin)
        base_height = ws.row_dimensions[start_row].height or 28
        max_row = start_row + line_count - 1

        for row in range(start_row, max_row + 1):
            ws.row_dimensions[row].height = base_height
            for col in range(1, 14):
                cell = ws.cell(row, col)
                cell.border = copy(data_border)
                if col in (3, 13):
                    cell.alignment = Alignment(
                        horizontal='left',
                        vertical='center',
                        wrap_text=True,
                        shrink_to_fit=False,
                    )
                else:
                    cell.alignment = Alignment(
                        horizontal='center',
                        vertical='center',
                        wrap_text=True,
                        shrink_to_fit=False,
                    )

class InventarioDocumentalLinea(models.Model):
    _name = 'inventario.documental.linea'
    _description = 'Líneas de Inventario'
    _order = 'numero_orden, id'

    inventario_id = fields.Many2one('inventario.documental', ondelete='cascade')
    workspace_id = fields.Many2one(
        'gestion.documental.workspace',
        string='Carpeta documental',
        ondelete='set null',
        index=True,
    )
    numero_orden = fields.Integer(string='N°')
    owner_id = fields.Many2one(
        'res.users',
        string='Responsable',
        default=lambda self: self.env.user,
        readonly=True,
        index=True,
    )
    can_edit_line = fields.Boolean(
        string='Puede editar',
        compute='_compute_line_permissions',
    )
    can_comment_line = fields.Boolean(
        string='Puede comentar',
        compute='_compute_line_permissions',
    )
    can_observe_line = fields.Boolean(
        string='Puede editar observaciones',
        compute='_compute_line_permissions',
    )

    @api.depends('owner_id', 'inventario_id.project_user_ids')
    def _compute_line_permissions(self):
        for line in self:
            line.can_edit_line = line._can_current_user_edit_line()
            line.can_comment_line = line._can_current_user_comment_line()
            line.can_observe_line = line._can_current_user_observe_line()

    def check_access_rule(self, operation):
        res = super().check_access_rule(operation)
        if self.env.su:
            return res
        blocked = self.filtered(lambda line: line.inventario_id and not line.inventario_id._can_user_access_inventory())
        if blocked:
            raise AccessError(_(
                "No tienes acceso a las líneas de este inventario. Primero deben compartirte el formulario."
            ))
        return res

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'numero_orden' in fields_list:
            parent_id = self._context.get('default_inventario_id')
            if parent_id:
                last = self.search(
                    [('inventario_id', '=', parent_id)],
                    order='numero_orden desc', limit=1,
                )
                res['numero_orden'] = (last.numero_orden or 0) + 1
        return res

    @api.model_create_multi
    def create(self, vals_list):
        counters = {}
        for vals in vals_list:
            inventory = self.env['inventario.documental'].browse(vals.get('inventario_id')).exists()
            if inventory and not inventory._can_current_user_edit_inventory_content():
                raise UserError(_(
                    "No tienes permiso de Editor para agregar líneas a este inventario."
                ))
            if not vals.get('numero_orden') and vals.get('inventario_id'):
                inv_id = vals['inventario_id']
                if inv_id not in counters:
                    last = self.search(
                        [('inventario_id', '=', inv_id)],
                        order='numero_orden desc', limit=1,
                    )
                    counters[inv_id] = last.numero_orden or 0
                counters[inv_id] += 1
                vals['numero_orden'] = counters[inv_id]
        records = super().create(vals_list)
        records._auto_sync_parent_inventory()
        return records

    def write(self, vals):
        self._check_can_write_lines(vals)
        res = super().write(vals)
        if vals:
            self._auto_sync_parent_inventory()
        return res

    def unlink(self):
        self._check_can_write_lines({})
        parents = self.mapped('inventario_id')
        res = super().unlink()
        parents._auto_sync_excel_to_drive_from_lines()
        return res

    def _auto_sync_parent_inventory(self):
        if (
            self.env.context.get(_INVENTARIO_SKIP_AUTO_SYNC)
            or self.env.context.get(_INVENTARIO_PARENT_WRITE)
        ):
            return
        self.mapped('inventario_id')._auto_sync_excel_to_drive_from_lines()

    def _can_current_user_edit_line(self):
        self.ensure_one()
        inventory = self.inventario_id
        if not inventory:
            return True
        if inventory._can_manage_inventory_all():
            return True
        user = self.env.user
        return (
            inventory._can_current_user_edit_inventory_content()
            and (not self.owner_id or self.owner_id == user or self.create_uid == user)
        )

    def _can_current_user_comment_line(self):
        self.ensure_one()
        inventory = self.inventario_id
        if not inventory:
            return True
        role = inventory._get_current_user_permission_role()
        return role in ('commenter', 'writer')

    def _can_current_user_observe_line(self):
        self.ensure_one()
        inventory = self.inventario_id
        if not inventory:
            return True
        role = inventory._get_current_user_permission_role()
        return role in ('observer', 'writer')

    def _check_can_write_lines(self, vals):
        if self.env.su or self.env.context.get(_INVENTARIO_SKIP_AUTO_SYNC):
            return
        vals = vals or {}
        only_comment = vals and set(vals).issubset({'comentario'})
        only_observation = vals and set(vals).issubset({'observaciones'})
        if only_comment:
            blocked = self.filtered(lambda line: not line._can_current_user_comment_line())
            if blocked:
                raise UserError(_("No tienes permiso para comentar estas líneas."))
        elif only_observation:
            blocked = self.filtered(lambda line: not line._can_current_user_observe_line())
            if blocked:
                raise UserError(_("No tienes permiso para editar observaciones en estas líneas."))
        else:
            blocked = self.filtered(lambda line: not line._can_current_user_edit_line())
            if blocked:
                names = ', '.join(blocked.mapped('nombre_serie')[:3])
                raise UserError(_(
                    "No puedes modificar o eliminar líneas agregadas por otro usuario sin permiso. "
                    "Pide a Gestión Documental, Soporte o Coordinación que te habilite el cambio. Líneas: %s"
                ) % (names or _("sin nombre")))
    
    codigo_trd = fields.Char(string='Código')
    nombre_serie = fields.Char(string='Serie/Asunto', required=True)
    folios = fields.Integer(string='Folios')
    fecha_inicial = fields.Date(string='F. Inicial')
    fecha_final = fields.Date(string='F. Final')
    caja = fields.Char(string='Caja')
    carpeta = fields.Char(string='Carpeta')
    es_digital = fields.Boolean(string='Digital')
    otro_soporte = fields.Char(string='Otro')
    frecuencia_consulta = fields.Selection([('alta', 'Alta'), ('media', 'Media'), ('baja', 'Baja')], default='media')
    soporte_especial = fields.Selection([('si', 'Sí'), ('no', 'No')], default='no')
    observaciones = fields.Text(string='Observaciones')
    comentario = fields.Text(string='Comentario')


class InventarioDocumentalPermiso(models.Model):
    _name = 'inventario.documental.permiso'
    _description = 'Permiso de inventario documental'
    _rec_name = 'user_id'

    inventario_id = fields.Many2one('inventario.documental', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', string='Usuario', required=True, index=True)
    role = fields.Selection(
        [
            ('reader', 'Lector'),
            ('observer', 'Observaciones'),
            ('commenter', 'Comentario'),
            ('writer', 'Editor'),
        ],
        string='Permiso',
        required=True,
        default='reader',
    )
    granted_by_id = fields.Many2one('res.users', string='Otorgado por', default=lambda self: self.env.user, readonly=True)

    _sql_constraints = [
        (
            'inventario_user_unique',
            'unique(inventario_id, user_id)',
            'Este usuario ya tiene permiso sobre este inventario.',
        )
    ]

    def check_access_rule(self, operation):
        res = super().check_access_rule(operation)
        if self.env.su:
            return res
        if operation == 'read':
            blocked = self.filtered(
                lambda rec: rec.user_id != self.env.user and not rec.inventario_id._can_manage_inventory_all()
            )
        else:
            blocked = self.filtered(lambda rec: not rec.inventario_id._can_manage_inventory_all())
        if blocked:
            raise AccessError(_("No tienes permiso para ver o modificar estos permisos."))
        return res


class InventarioDocumentalCompartirWizard(models.TransientModel):
    _name = 'inventario.documental.compartir.wizard'
    _description = 'Compartir formulario de inventario documental'

    inventario_id = fields.Many2one('inventario.documental', required=True, readonly=True)
    project_user_ids = fields.Many2many(
        'res.users',
        'inv_doc_share_wizard_project_user_rel',
        'wizard_id',
        'user_id',
        string='Usuarios permitidos del proyecto',
        readonly=True,
    )
    user_id = fields.Many2one('res.users', string='Usuario del proyecto', required=True)
    role = fields.Selection(
        [
            ('reader', 'Lector'),
            ('observer', 'Observaciones'),
            ('commenter', 'Comentario'),
            ('writer', 'Editor'),
        ],
        string='Permiso',
        default='reader',
        required=True,
    )
    message = fields.Text(
        string='Mensaje',
        default='Te compartieron el formulario de inventario documental para trabajar desde SGO.',
    )

    @api.onchange('user_id')
    def _onchange_user_id(self):
        if self.user_id and self.project_user_ids and self.user_id.id not in self.project_user_ids.ids:
            self.user_id = False
            return {
                'warning': {
                    'title': _('Usuario no permitido'),
                    'message': _('Selecciona un usuario que esté permitido en el proyecto.'),
                }
            }
        return {}

    def action_share(self):
        self.ensure_one()
        inventory = self.inventario_id
        if not inventory._can_manage_inventory_all() and inventory.create_uid != self.env.user:
            raise UserError(_("No tienes permiso para compartir este inventario."))
        if self.project_user_ids and self.user_id.id not in self.project_user_ids.ids:
            raise UserError(_("Solo puedes compartir con usuarios permitidos en este proyecto."))

        Permission = self.env['inventario.documental.permiso'].sudo()
        existing = Permission.search([
            ('inventario_id', '=', inventory.id),
            ('user_id', '=', self.user_id.id),
        ], limit=1)
        vals = {
            'inventario_id': inventory.id,
            'user_id': self.user_id.id,
            'role': self.role,
            'granted_by_id': self.env.user.id,
        }
        if existing:
            existing.write(vals)
        else:
            Permission.create(vals)

        partner = self.user_id.partner_id
        if partner:
            inventory.message_subscribe(partner_ids=[partner.id])
        self._send_share_email()
        inventory.message_post(
            body=_("Formulario Odoo compartido con %s como %s.") % (
                self.user_id.name,
                dict(self._fields['role'].selection).get(self.role),
            )
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Formulario compartido'),
                'message': _('Se envió el correo y se guardó el permiso.'),
                'sticky': False,
                'type': 'success',
            }
        }

    def _send_share_email(self):
        self.ensure_one()
        if not self.user_id.email:
            return
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        url = f"{base_url}/web#id={self.inventario_id.id}&model=inventario.documental&view_type=form"
        project_name = self.inventario_id.project_id.display_name or self.inventario_id.workspace_id.name or ''
        role_label = dict(self._fields['role'].selection).get(self.role, self.role)
        subject = _("Inventario documental compartido - %s") % (project_name or self.inventario_id.display_name)
        body = self._build_share_email_body(url, project_name, role_label)
        self.env['mail.mail'].sudo().create({
            'subject': subject,
            'body_html': body,
            'email_to': self.user_id.email,
            'auto_delete': True,
        }).send()

    def _build_share_email_body(self, url, project_name, role_label):
        user_name = html_escape(self.user_id.name or '')
        inventory_name = html_escape(self.inventario_id.display_name or _('Inventario documental'))
        project_name = html_escape(project_name or '')
        role_label = html_escape(role_label or '')
        msg = html_escape(self.message or '').replace('\n', '<br/>')
        return f"""
            <div style="background:#f4f7fb; padding:24px; font-family:Arial, sans-serif; color:#0f172a;">
                <div style="max-width:760px; margin:0 auto; background:#ffffff; border:1px solid #d9e2ef; border-radius:8px; overflow:hidden;">
                    <div style="background:#003b8f; color:#ffffff; padding:18px 24px; text-align:center; font-size:20px; font-weight:700;">
                        Formulario de inventario documental compartido
                    </div>
                    <div style="padding:24px;">
                        <p style="margin:0 0 16px;">Cordial saludo, <strong>{user_name}</strong>:</p>
                        <div style="border-left:5px solid #003b8f; background:#f8fafc; padding:14px 16px; margin-bottom:18px;">
                            <p style="margin:0 0 8px;"><strong>Proyecto:</strong> {project_name}</p>
                            <p style="margin:0 0 8px;"><strong>Formulario:</strong> {inventory_name}</p>
                            <p style="margin:0;"><strong>Permiso asignado:</strong> {role_label}</p>
                        </div>
                        <p style="line-height:1.55; margin:0 0 22px;">{msg}</p>
                        <div style="text-align:center; margin:26px 0;">
                            <a href="{url}" style="background:#003b8f; color:#ffffff; text-decoration:none; padding:13px 24px; border-radius:6px; font-weight:700; display:inline-block;">
                                Abrir en SGO
                            </a>
                        </div>
                        <p style="font-size:12px; color:#64748b; margin:22px 0 0;">Este es un mensaje automático de SGO.</p>
                    </div>
                </div>
            </div>
        """

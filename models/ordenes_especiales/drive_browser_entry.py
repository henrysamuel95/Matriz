from odoo import _, models
from odoo.exceptions import UserError


class OrdenEspecialDriveBrowserEntry(models.Model):
    _inherit = 'drive.browser.entry'

    def action_delete_entry(self):
        self.ensure_one()
        if self.entry_type == 'file':
            documento = self.env['ordenes_especiales.documento'].sudo().search([
                ('drive_document_id', '=', self.drive_id.id),
            ]) if self.drive_id else self.env['ordenes_especiales.documento'].browse()
            carpeta_especial = self.env['ordenes_especiales'].sudo().search_count([
                ('drive_file_id.folder_id2', '=', self.parent_workspace_id.folder_google_id),
            ]) if self.parent_workspace_id.folder_google_id else 0
            if documento or carpeta_especial:
                self._check_consulta_read_only()
            if documento:
                if len(documento) != 1 or not self.drive_id.file_id:
                    raise UserError(_(
                        'La relación de este archivo con la orden especial es ambigua. '
                        'Contacte a Soporte antes de eliminarlo.'
                    ))
                documento.sudo().unlink()
                return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
            if carpeta_especial and (
                not self.drive_id
                or not self.drive_id.file_id
                or self.google_id != self.drive_id.file_id
                or self.env['drive'].sudo().search_count([
                    ('id', '!=', self.drive_id.id),
                    ('file_id', '=', self.drive_id.file_id),
                ])
            ):
                raise UserError(_(
                    'No se puede identificar de forma exclusiva este archivo de la '
                    'orden especial. Actualice la carpeta o contacte a Soporte.'
                ))
        return super().action_delete_entry()

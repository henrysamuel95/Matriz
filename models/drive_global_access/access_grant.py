from odoo import _, fields, models
from odoo.exceptions import UserError


class DriveGlobalAccessGrant(models.Model):
    _name = 'drive.global.access.grant'
    _description = 'Permiso de Drive creado por acceso global'
    _order = 'project_id, user_id'

    config_id = fields.Many2one(
        'drive.global.access',
        required=True,
        ondelete='cascade',
        index=True,
    )
    project_id = fields.Many2one(
        'contratacion',
        string='Proyecto',
        required=True,
        ondelete='cascade',
        index=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Usuario',
        required=True,
        ondelete='cascade',
        index=True,
    )
    email = fields.Char(required=True, index=True)
    permission_type = fields.Char(
        string='Tipo de permiso',
        compute='_compute_permission_type',
    )
    folder_id = fields.Char(string='ID de carpeta', required=True)
    permission_id = fields.Char(string='ID del permiso de Google Drive')

    def _compute_permission_type(self):
        for grant in self:
            grant.permission_type = 'Lector'

    def action_revoke_from_project(self):
        """Permite limpiar un permiso histórico directamente desde su proyecto."""
        self.ensure_one()
        self.project_id.write({
            'drive_reader_user_ids': [(3, self.user_id.id)],
        })
        # Si el usuario no estaba en la nueva lista por ser un permiso anterior,
        # el write no tiene cambios. En ese caso se revoca el registro puntual.
        if self.exists():
            result = self.config_id.revoke_project_readers(
                self.project_id, [self.user_id.id]
            )
            if result['errors']:
                raise UserError('\n'.join(result['errors']))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Acceso retirado'),
                'message': _('El permiso de lector fue retirado de este proyecto.'),
                'type': 'success',
                'sticky': False,
            },
        }

    _sql_constraints = [
        (
            'drive_global_grant_project_user_unique',
            'unique(config_id, project_id, user_id)',
            'El permiso global de este usuario y proyecto ya está registrado.',
        ),
    ]

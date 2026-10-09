from odoo import models, fields, api


class PortalChatMessage(models.Model):
    _name = 'portal.chat.message'
    _description = 'Mensaje de Chat Portal'
    _order = 'create_date asc'

    login_id = fields.Many2one('login', string='Usuario Portal', required=True, ondelete='cascade')
    cuenta_cobro_id = fields.Many2one('cuentas_de_cobros', string='Cuenta de cobro', ondelete='cascade')
    sender_type = fields.Selection([
        ('contratista', 'Contratista'),
        ('interno', 'Asistente'),
    ], string='De', required=True, default='interno')
    sender_name = fields.Char(string='Nombre')
    body = fields.Text(string='Mensaje', required=True)

    @api.model
    def create(self, vals):
        if vals.get('sender_type') == 'interno' and not vals.get('sender_name'):
            vals['sender_name'] = self.env.user.name
        return super().create(vals)

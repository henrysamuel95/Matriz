from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _visible_menu_ids(self, debug=False):
        visible_menu_ids = super()._visible_menu_ids(debug=debug)

        if not self.env.user.has_group("Matriz.group_consulta_coordinacion_financiera"):
            return visible_menu_ids

        allowed_menus = self.env["ir.model.data"].sudo().search([
            ("module", "=", "Matriz"),
            ("name", "in", ["menu_principal", "menu_consulta_principal"]),
            ("model", "=", "ir.ui.menu"),
        ]).mapped("res_id")
        allowed_menu_ids = set(allowed_menus)

        if isinstance(visible_menu_ids, set):
            return visible_menu_ids & allowed_menu_ids
        return [menu_id for menu_id in visible_menu_ids if menu_id in allowed_menu_ids]

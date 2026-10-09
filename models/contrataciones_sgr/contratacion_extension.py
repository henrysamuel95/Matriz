from odoo import models


class ContratacionSgrRouting(models.Model):
    _inherit = 'contratacion'

    def Contractual(self):
        self.ensure_one()
        if not self._es_proyecto_regalias_en_informacion_base():
            return super().Contractual()

        return {
            'type': 'ir.actions.act_window',
            'name': '2. Contratación - Regalías',
            'res_model': 'contrataciones_sgr',
            'view_mode': 'kanban,tree,form',
            'views': [
                (self.env.ref('Matriz.view_contrataciones_sgr_kanban').id, 'kanban'),
                (self.env.ref('Matriz.view_contrataciones_sgr_tree').id, 'tree'),
                (self.env.ref('Matriz.view_contrataciones_sgr_form').id, 'form'),
            ],
            'target': 'current',
            'domain': [('proyecto_relacionado', '=', self.id)],
            'context': {'default_proyecto_relacionado': self.id},
        }

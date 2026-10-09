from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {}, su=True)
    env['contrataciones_sgr'].search([]).action_preparar_documentos()

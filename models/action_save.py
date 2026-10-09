#!/usr/bin/python

from odoo import fields, models
from odoo import api


class FormulariodeAprobaciones(models.Model):
    _name = "action_save"

    gestion_seleccionada = fields.Boolean(string='Gestión Seleccionada', compute='_compute_gestion_seleccionada', store=True)

    @api.depends('ficharesumen_id')
    def _compute_gestion_seleccionada(self):
        for record in self:
            record.gestion_seleccionada = bool(record.ficharesumen_id)



    Titulo1_proyecto = fields.Text()
    ficharesumen_id = fields.Many2one('ficharesumen', string='Ficha Resumen')


    revision_tecnico_administrativa = fields.Selection([
        ('catalina_jimenez', 'Catalina Jimenez Cerquera'),
        ('carmen_villareal', 'Carmen Patricia Villareal Cano')
    ], string="Revisión Técnico-Administrativa")
    revision_juridica = fields.Selection([
        ('jairo_candelo', 'Jairo Candelo'),
        ('designado_juridico', 'Designado del equipo Jurídico')
    ], string="Revisión Jurídica")
    revision_financiera = fields.Selection([
        ('diana_lorena_cano', 'Diana Lorena Cano')
    ], string="Revisión Financiera")
    aprueba = fields.Selection([
        ('carmen_patricia_villareal_cano', 'Carmen Patricia Villareal Cano'),
        ('marlon_gomez_jaramillo', 'Marlon Gomez Jaramillo')
    ], string="Aprueba")


    def action_guardar(self):
        # Actualiza el campo en ficharesumen
        if self.revision_tecnico_administrativa == 'catalina_jimenez':
            self.ficharesumen_id.viable_Administrativa = 'viable'  # o lo que sea apropiado
        if self.revision_tecnico_administrativa == 'carmen_villareal':
            self.ficharesumen_id.viable_Administrativa = 'No viable'  # o lo que sea apropiado
        if self.revision_juridica == 'jairo_candelo':
            self.ficharesumen_id.viable_Juridica = 'viable'  # o lo que sea apropiado
        if self.revision_juridica == 'designado_juridico':
            self.ficharesumen_id.viable_Juridica = 'No viable'  # o lo que sea apropiado
        if self.revision_financiera == 'diana_lorena_cano':
            self.ficharesumen_id.viable_Financiera = 'viable'  # o lo que sea apropiado
        if self.aprueba == 'carmen_patricia_villareal_cano':
            self.ficharesumen_id.aprobado = 'Aprobado'  # o lo que sea apropiado
        if self.aprueba == 'marlon_gomez_jaramillo':
            self.ficharesumen_id.aprobado = 'No Aprobado'  # o lo que sea apropiado

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_salir(self):
        return {
            'name': 'Informacion Base',
            'view_mode': 'form',
            'res_model': 'informacion_base',
            'type': 'ir.actions.act_window',
            'target': 'new',
            'res_id': self.env.context.get('active_id', False),
            'flags': {'action_buttons': True},  # Mantener botones de acción en la ventana actual
        }

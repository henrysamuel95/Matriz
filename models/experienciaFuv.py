import re
from odoo import models, fields, api
from odoo.osv import expression




class ExperienciaFuv(models.Model):
    _name = 'experiencia_fuv'
    _description = 'Experiencia FUV'
    _rec_name = 'codificacion'
    _order = 'vigencia desc, anio_terminacion desc, create_date desc'

    codificacion = fields.Char(string="Codificación")
    cert_rup = fields.Char(string="Certificado RUP")
    vigencia = fields.Char(string="Vigencia")
    fecha_inicio = fields.Char(string="Fecha de Inicio")
    fecha_finalizacion = fields.Char(string="Fecha de Finalización")
    anio_terminacion = fields.Char(string="Año de Terminación")
    numero_contrato = fields.Char(string="Número de Contrato")
    nombre_entidad_contratante = fields.Char(string="Nombre de la Entidad Contratante")
    objeto_contrato = fields.Char(string="Objeto del Contrato")
    localizacion = fields.Char(string="Localización")
    valor_contrato_inicial = fields.Char(string="Valor del Contrato Inicial")
    aportes_fuv_convenios = fields.Char(string="Aportes FUV Convenios")
    valor_total_contrato = fields.Char(string="Valor Total del Contrato")
    valor_registrado_rup = fields.Char(string="Valor Registrado en el RUP")
    conversion_smlv = fields.Char(string="Conversión SMLV")
    observaciones = fields.Char(string="Observaciones")

    @api.model
    def _consulta_views(self):
        return [
            (self.env.ref('Matriz.view_experiencia_fuv_consulta_tree').id, 'tree'),
            (self.env.ref('Matriz.view_experiencia_fuv_consulta_form').id, 'form'),
        ]

    @api.model
    def _action_records(self, name='Experiencia FUV', domain=None):
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 'experiencia_fuv',
            'view_mode': 'tree,form',
            'views': self._consulta_views(),
            'domain': domain or [],
            'context': {'create': False, 'edit': False, 'delete': False},
        }


class ExperienciaFuvConsulta(models.Model):
    _name = 'experiencia.fuv.consulta'
    _description = 'Panel de consultas de Experiencia FUV'

    name = fields.Char(string='Nombre', default='Panel de consultas')
    busqueda_codificacion = fields.Char(string='Codigo / codificacion')
    busqueda_numero_contrato = fields.Char(string='Numero de contrato')
    busqueda_localizacion = fields.Char(string='Localizacion')
    busqueda_fecha = fields.Char(string='Fecha, vigencia o anio')

    @api.model
    def action_open_panel(self):
        panel = self.env.ref('Matriz.experiencia_fuv_consulta_singleton', raise_if_not_found=False)
        if not panel:
            panel = self.search([], limit=1) or self.create({'name': 'Panel de consultas'})
        panel.write({
            'busqueda_codificacion': False,
            'busqueda_numero_contrato': False,
            'busqueda_localizacion': False,
            'busqueda_fecha': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Consultas Experiencia FUV',
            'res_model': 'experiencia.fuv.consulta',
            'view_mode': 'form',
            'views': [(self.env.ref('Matriz.view_experiencia_fuv_consulta_panel_form').id, 'form')],
            'view_id': self.env.ref('Matriz.view_experiencia_fuv_consulta_panel_form').id,
            'res_id': panel.id,
            'target': 'current',
        }

    def _action_records(self, name, domain):
        return self.env['experiencia_fuv']._action_records(name=name, domain=domain)

    def _build_domain(self):
        self.ensure_one()
        domain = []
        codificacion = (self.busqueda_codificacion or '').strip()
        numero_contrato = (self.busqueda_numero_contrato or '').strip()
        localizacion = (self.busqueda_localizacion or '').strip()
        fecha = (self.busqueda_fecha or '').strip()

        if codificacion:
            domain = expression.AND([domain, [('codificacion', 'ilike', codificacion)]])
        if numero_contrato:
            domain = expression.AND([domain, [('numero_contrato', 'ilike', numero_contrato)]])
        if localizacion:
            domain = expression.AND([domain, [('localizacion', 'ilike', localizacion)]])
        if fecha:
            domain = expression.AND([domain, expression.OR([
                [('fecha_inicio', 'ilike', fecha)],
                [('fecha_finalizacion', 'ilike', fecha)],
                [('anio_terminacion', 'ilike', fecha)],
                [('vigencia', 'ilike', fecha)],
            ])])
        return domain

    def action_buscar(self):
        return self._action_records('Resultado de consulta Experiencia FUV', self._build_domain())

    def action_ver_todos(self):
        return self._action_records('Toda la experiencia FUV', [])



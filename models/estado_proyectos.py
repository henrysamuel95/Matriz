#!/usr/bin/python

from odoo import models, fields, api

class EstadoProyectosInf(models.Model):
    _name = 'estado_proyectos_inf'
    _description = 'Tabla de estados de proyectos'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Nombre", compute="_compute_name")
    @api.depends('responsable_revis')
    def _compute_name(self):
        for record in self:
            if record.responsable_revis:
                record.name = record.responsable_revis.name
            else:
                record.name = "nuevo"

    responsable_revis = fields.Many2one(
        'res.users',
        string='Responsable',
        help='Seleccione al responsable de la revisión',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)],
    )

    foto_responsable = fields.Binary(
        related='responsable_revis.image_1920',
        string='Foto del Responsable',
        store=False
    )
    nombre_proyecto = fields.Many2one(comodel_name='contratacion', string='Nombre del Proyecto')
    numero_proyecto = fields.Char(string='Numero del proyecto', related='nombre_proyecto.numero_contrato')
    entidad_contratante = fields.Char(string='Entidad contratante', related='nombre_proyecto.entidad_contratante')
    fecha_finalizacion_inicial = fields.Date(string='FECHA DE FINALIZACIÓN INICIAL',
                                             related='nombre_proyecto.fecha_finalizacion2')
    valor_proyecto = fields.Float(string='Valor proyecto', related='nombre_proyecto.valor_total_final')
    objeto = fields.Char(string='Objeto', related='nombre_proyecto.objecto_proyecto')
    contrapartida = fields.Monetary(string='CONTRAPARTIDA', related='nombre_proyecto.suma_contrapartidas')
    fecha_inicio = fields.Date(string='CONTRAPARTIDA', related='nombre_proyecto.fecha_inicio')
    currency_id = fields.Many2one('res.currency', string='Moneda')




    actividades_pendientes_estado = fields.Char(
        string='Actividades especificas pendientes por ejecutarse y/o estado actual')
    titulo = fields.Char(string='Prorroga/Otro si')
    fecha_final_proyecto = fields.Date(string='Fecha final del proyecto', related='nombre_proyecto.fecha_final_proyecto')
    adicion_presupuestal = fields.Monetary(string='Adicion presupuestal', related='nombre_proyecto.adicion_otrosi44')
    saldo_pendiente_pago = fields.Monetary(string='Saldo pendiente de pago', related='nombre_proyecto.valor_por_facturar')
    observaciones = fields.Char(string='Observaciones direccion ejecutiva')
    detalles_proyecto_ids = fields.One2many('estado_proyectos_inf.line', 'proyecto_relacionado',
                                            string='Detalles del Proyecto')
    @api.model
    def get_views(self, views, options=None):
        res = super().get_views(views, options)
        if options and options.get('toolbar'):
            if 'detalles_proyecto_ids' in res.get('fields', {}):
                res['fields']['detalles_proyecto_ids']['views']['tree']['arch'] = self.env['ir.ui.view'].render_template('Matriz.vista_tree_detalles_proyecto', {
                    'create': True,
                    'edit': 'top',
                })
        return res

    @api.onchange('responsable_revis')
    def _onchange_responsable_revis(self):
        if self.responsable_revis:
            # Obtener todos los proyectos del responsable seleccionado
            contrataciones = self.env['contratacion'].search([('create_uid', '=', self.responsable_revis.id)])



            # Si el responsable no tiene proyectos asociados, vaciar las líneas existentes
            if not contrataciones:
                self.detalles_proyecto_ids.unlink()
                return

            self.detalles_proyecto_ids.unlink()

            # Crear una lista de proyectos ya presentes en las líneas existentes
            proyectos_existentes = self.detalles_proyecto_ids.mapped('nombre_proyecto')

            # Crear líneas solo para los proyectos del nuevo responsable que aún no estén en la lista
            for contrato in contrataciones:
                if contrato not in proyectos_existentes:
                    self.detalles_proyecto_ids = [(0, 0, {
                        'nombre_proyecto': contrato.id,
                        # Puedes inicializar otros campos aquí si es necesario
                    })]

        else:
            # Si no se selecciona ningún responsable, vaciar las líneas existentes
            self.detalles_proyecto_ids.unlink()

    contrapartida_sumado = fields.Monetary(string='', compute='_compute_contrapartida_sumado')
    @api.depends('detalles_proyecto_ids.contrapartida')
    def _compute_contrapartida_sumado(self):
        for proyecto in self:
            proyecto.contrapartida_sumado = sum(proyecto.detalles_proyecto_ids.mapped('contrapartida'))


    valor_total_final_sumado =fields.Float(string='', compute='_compute_valor_total_final_sumado')
    @api.depends('detalles_proyecto_ids.valor_proyecto_sumado_lineal')
    def _compute_valor_total_final_sumado(self):
        for proyecto in self:
            proyecto.valor_total_final_sumado = sum(proyecto.detalles_proyecto_ids.mapped('valor_proyecto_sumado_lineal'))



    valor_proyecto_sumado = fields.Float(compute='_compute_valor_proyecto_sumado')
    @api.depends('detalles_proyecto_ids.valor_proyecto')
    def _compute_valor_proyecto_sumado(self):
        for proyecto in self:
            proyecto.valor_proyecto_sumado = sum(proyecto.detalles_proyecto_ids.mapped('valor_proyecto'))


    total_py_a_cargo = fields.Integer(compute='_compute_total_py_a_cargo', store=True, string='Total proyectos a cargo')

    @api.depends('detalles_proyecto_ids')
    def _compute_total_py_a_cargo(self):
        for proyecto in self:
            proyecto.total_py_a_cargo = len(proyecto.detalles_proyecto_ids)


class EstadoProyectosInfLine(models.Model):
    _name = 'estado_proyectos_inf.line'
    _description = 'Línea de Detalle del Proyecto'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    nombre_proyecto = fields.Many2one(comodel_name='contratacion', string='NOMBRE DEL PROYECTO',
                                      domain="[('create_uid','=',responsable_revis)]")

    # Resto de los campos...

    responsable_revis = fields.Many2one(
        'res.users',
        string='Responsable',
        help='Seleccione al responsable de la revisión',
        domain=lambda self: [('groups_id', 'in', self.env.ref('Matriz.group_contratacion_coordinador').id)]
    )

    valor_proyecto_sumado = fields.Float(string='Valor total proyectos a cargo')
    proyecto_relacionado = fields.Many2one('estado_proyectos_inf', string='Proyecto Relacionado')
    numero_proyecto = fields.Char(string='NUMERO DEL PROYECTO', related='nombre_proyecto.numero_contrato')

    entidad_contratante = fields.Char(string='ENTIDAD CONTRATANTE', related='nombre_proyecto.entidad_contratante')
    fecha_finalizacion_inicial = fields.Date(string='FECHA DE FINALIZACIÓN INICIAL',
                                             related='nombre_proyecto.fecha_finalizacion2')
    valor_proyecto = fields.Float(string='VALOR PROYECTO INICIAL', related='nombre_proyecto.valor_total_final')
    objeto = fields.Char(string='OBJETO', related='nombre_proyecto.objecto_proyecto')
    contrapartida = fields.Monetary(string='CONTRAPARTIDA', related='nombre_proyecto.contrapartida_estados')
    fecha_inicio = fields.Date(string='FECHA DE INICIO', related='nombre_proyecto.fecha_inicio')
    currency_id = fields.Many2one('res.currency', string='Moneda')

    actividades_pendientes_estado = fields.Char(
        string='ACTIVIDADES ESPECIFICAS PENDIENTES POR EJECUTARSE Y/O ESTADO ACTUAL')
    prorroga_otro_si = fields.Char(string='PRORROGA/OTROSI')
    fecha_final_proyecto = fields.Date(string='FECHA FINAL DEL PROYECTO', related='nombre_proyecto.fecha_final_proyecto')
    adicion_presupuestal = fields.Monetary(string='ADICION PRESUPUESTAL', related='nombre_proyecto.adicion_otrosi44')
    valor_total_final = fields.Float(string='VALOR TOTAL FINAL', related='nombre_proyecto.valor_total_final')
    saldo_pendiente_pago = fields.Monetary(string='SALDO PENDIENTE DE PAGO',
                                           related='nombre_proyecto.valor_por_facturar')
    observaciones = fields.Char(string='OBSERVACIONES DIRECCION EJECUTIVA')

    valor_proyecto_sumado_lineal = fields.Float(compute='_compute_valor_proyecto_sumado_lineal', string='VALOR TOTAL FINAL')

    @api.depends('valor_total_final', 'adicion_presupuestal')
    def _compute_valor_proyecto_sumado_lineal(self):
        for proyecto in self:
            proyecto.valor_proyecto_sumado_lineal = proyecto.valor_total_final + proyecto.adicion_presupuestal



from odoo import fields, models, api
from odoo.exceptions import UserError
from datetime import datetime



class AvancesProyectos(models.Model):
    _name = "avances_proyectos"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    new_name = fields.Char(string='New Name')
    informacion_base_agregar_id = fields.Many2one('informacion_base_agregar', string='Información base agregar')
    contrataciones_id = fields.Many2one('contratacion', string='Contratación')

    @api.onchange('nombre_proyecto')
    def _onchange_nombre_proyecto(self):
        if self.nombre_proyecto:
            self.contrataciones_id = self.env['contratacion'].search(
                [('name', '=', self.nombre_proyecto.name)], limit=1
            )

    currency_id = fields.Many2one('res.currency', string='Moneda')
    nombre_proyecto = fields.Many2one(comodel_name='contratacion', ondelete='cascade')
    entidad_contratante = fields.Char(related='nombre_proyecto.entidad_contratante')
    numero_contrato = fields.Char(related='nombre_proyecto.numero_contrato')
    fecha_inicio = fields.Date(related='nombre_proyecto.fecha_inicio', tracking=True)
    fecha_finalizacion = fields.Date(related='nombre_proyecto.fecha_final_proyecto', tracking=True)
    fanalizado = fields.Text()

    is_editable_avances = fields.Boolean(string='Es editable', related="nombre_proyecto.is_editable", default=False, store=True)

    @api.onchange('is_editable_avances')
    def onchange_is_editable_avances(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass


    porcentaje_ejecucion = fields.Float(related="nombre_proyecto.suma_total")
    coordinador_asignado = fields.Many2one('res.users', related='nombre_proyecto.coordinador_asignado')
    valor_contratar = fields.Monetary(related='nombre_proyecto.valor_a_contratar')
    valor_poliza = fields.Monetary(related='nombre_proyecto.valor_poliza')
    valor_administracion_costo_operativo = fields.Monetary(
        related='nombre_proyecto.valor_administracion_costo_operativo')
    valor_por_facturar = fields.Monetary(related='nombre_proyecto.valor_por_facturar')
    suma_contrapartidas = fields.Monetary(related='nombre_proyecto.suma_contrapartidas', tracking=True)
    valor_sin_contrapartida = fields.Float(related='nombre_proyecto.valor_sin_contrapartida', tracking=True)
    valor_total_proyecto = fields.Float(related='nombre_proyecto.valor_total_final', tracking=True)
    resultado_tecnica_representada_dinero = fields.Monetary(
        related='nombre_proyecto.resultado_tecnica_representada_dinero', tracking=True)

    sobre_costo = fields.Monetary(related="nombre_proyecto.sobre_costo", tracking=True)

    int_valor_contratar = fields.Float(string="Valor a contratar", compute='_compute_int_valor_contratar', store=True)
    int_valor_poliza = fields.Float(string="Valor Poliza", compute='_compute_int_valor_poliza', store=True)
    int_valor_administracion_costo_operativo = fields.Float(string="Valor de administración y costo operativo",
                                                            compute='_compute_int_valor_administracion_costo_operativo',
                                                            store=True)
    int_valor_por_facturar = fields.Float(string="Valor por facturar", compute='_compute_int_valor_por_facturar',
                                          store=True)
    int_suma_contrapartidas = fields.Float(string="Valor contrapartida", compute='_compute_int_suma_contrapartidas',
                                           store=True)
    int_valor_sin_contrapartida = fields.Float(string="Valor sin contrapartida",
                                               compute='_compute_int_valor_sin_contrapartida', store=True)
    int_sobre_costo = fields.Float(string="Sobre costo", compute='_compute_int_sobre_costo', store=True)
    int_valor_total_proyecto = fields.Float(string="Valor total", compute='_compute_int_valor_total_proyecto',
                                            store=True)
    int_resultado_tecnica_representada_dinero = fields.Float(string="Tecnica representada en dinero",
                                                             compute='_compute_int_resultado_tecnica_representada_dinero',
                                                             store=True)

    @api.depends('valor_contratar')
    def _compute_int_valor_contratar(self):
        for rec in self:
            rec.int_valor_contratar = float(round(rec.valor_contratar))

    @api.depends('valor_poliza')
    def _compute_int_valor_poliza(self):
        for rec in self:
            rec.int_valor_poliza = float(round(rec.valor_poliza))

    @api.depends('valor_administracion_costo_operativo')
    def _compute_int_valor_administracion_costo_operativo(self):
        for rec in self:
            rec.int_valor_administracion_costo_operativo = float(round(rec.valor_administracion_costo_operativo))

    @api.depends('valor_por_facturar')
    def _compute_int_valor_por_facturar(self):
        for rec in self:
            rec.int_valor_por_facturar = float(round(rec.valor_por_facturar))

    @api.depends('suma_contrapartidas')
    def _compute_int_suma_contrapartidas(self):
        for rec in self:
            rec.int_suma_contrapartidas = float(round(rec.suma_contrapartidas))

    @api.depends('valor_sin_contrapartida')
    def _compute_int_valor_sin_contrapartida(self):
        for rec in self:
            rec.int_valor_sin_contrapartida = float(round(rec.valor_sin_contrapartida))

    @api.depends('sobre_costo')
    def _compute_int_sobre_costo(self):
        for rec in self:
            rec.int_sobre_costo = float(round(rec.sobre_costo))

    @api.depends('valor_total_proyecto')
    def _compute_int_valor_total_proyecto(self):
        for rec in self:
            rec.int_valor_total_proyecto = float(round(rec.valor_total_proyecto))

    @api.depends('resultado_tecnica_representada_dinero')
    def _compute_int_resultado_tecnica_representada_dinero(self):
        for rec in self:
            rec.int_resultado_tecnica_representada_dinero = float(round(rec.resultado_tecnica_representada_dinero))



    # Campos para cerrar proyecto
    is_editable2 = fields.Boolean(string='Es editable', related="nombre_proyecto.is_editable", default=False)

    @api.onchange('is_editable')
    def _onchange_is_editable(self):
        # Este método se llama cuando cambia el valor de is_editable
        # Puedes agregar lógica adicional aquí si es necesario
        pass

    proyecto_finalizado = fields.Char(string='Estado', compute='_compute_estado', store=True)

    @api.depends('dias_faltantes', 'is_editable_avances')
    def _compute_estado(self):
        for rec in self:
            if rec.is_editable_avances == True:
                rec.proyecto_finalizado = "Proyecto finalizado"
            elif rec.dias_faltantes < 0:
                rec.proyecto_finalizado = "Proyecto atrasado"
            else:
                rec.proyecto_finalizado = ""

    # Campos para calcular días faltantes
    dias_faltantes = fields.Integer(string="Días Faltantes", compute='_compute_dias_faltantes', store=True)

    @api.depends('fecha_finalizacion')
    def _compute_dias_faltantes(self):
        for rec in self:
            if rec.fecha_finalizacion:
                fecha_final = fields.Date.from_string(rec.fecha_finalizacion)
                dias_faltantes = (fecha_final - datetime.now().date()).days
                rec.dias_faltantes = dias_faltantes
            else:
                rec.dias_faltantes = 0
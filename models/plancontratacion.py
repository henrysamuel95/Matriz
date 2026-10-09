#!/usr/bin/python
from odoo import fields, models, api


class PlanContratacion(models.Model):
        _name = "plancontratacion"
        _inherit = ['mail.thread', 'mail.activity.mixin']


        name = fields.Char(string="Nombre")

        fch_creacion = fields.Datetime(string='Fecha creacion', copy=False, default=lambda self: fields.Datetime.now())
        write_date = fields.Datetime(string='Fecha de Modificación', readonly=True)

        new_name = fields.Char(string='Nuevo nombre')

        tabla_contratos = fields.One2many('plancontratacion', 'contratacion_id', string="Tabla de Plan Contratacion")
        contratacion_id = fields.Many2one('plancontratacion', string="Plan Contratacion")

        def Guardar(self):
            self.ensure_one()
            self.write({'name': self.new_name})
            return {
                'type': 'ir.actions.client',
                'tag': 'reload',
            }

        fecha_emision = fields.Date(string="Fecha Emisión")
        fecha_actualizacion = fields.Date(string="Fecha de Actualización")
        fecha_impresion = fields.Date(string="Fecha Impresión")

        contrato7 = fields.Char(string="Número de Contrato")
        cedula_nit_contratista = fields.Char(string="Cédula / Nit Contratista")
        perfil_completo = fields.Char(string="Perfil Completo requerido para el cargo")
        cargo_proyecto = fields.Char(string="Cargo en el proyecto")
        profesion = fields.Char(string="Profesión")
        titulo_estudios = fields.Char(string="Título Estudios")
        objeto = fields.Text(string="OBJETO")
        actividades_especificas = fields.Text(string="Actividades Específicas")
        productos_entregar = fields.Text(string="Productos a Entregar")
        fecha_inicio = fields.Date(string="Fecha de Inicio")
        fecha_finalizacion = fields.Date(string="Fecha Finalización")
        tipo_solicitud = fields.Char(string="Tipo de Solicitud")
        tipo_contrato = fields.Char(string="Tipo de Contrato")
        vr_total_contrato = fields.Float(string="Vr. Total Contrato (números)")
        observaciones_tecnicas = fields.Text(string="Observaciones de Carácter Técnico")
        forma_pago = fields.Char(string="FORMA DE PAGO")
        fecha_solicitud = fields.Date(string="Fecha de Solicitud")
        justificacion_solicitud = fields.Text(string="JUSTIFICACIÓN DE LA SOLICITUD")
        cdp_no = fields.Char(string="CDP NO.")
        fecha_cdp = fields.Date(string="FECHA CDP")
        clase_contrato = fields.Char(string="CLASE DE CONTRATO")
        descripcion_forma_pago = fields.Text(string="DESCRIPCIÓN FORMA DE PAGO")
        quien_solicita = fields.Char(string="QUIÉN SOLICITA")
        valor_en_letras = fields.Char(string="VALOR EN LETRAS")

        @api.depends('fecha_impresion')
        def _compute_fecha_impresion(self):
            for record in self:
                record.fecha_impresion = fields.Date.today()

        def Ingreso_Nueva_Actividad_a_Contratar_y_Programacion_de_Pagos(self):
            return {
                'name': 'Contratación',
                'view_mode': 'form',
                'res_model': 'nueva_actividad',
                'type': 'ir.actions.act_window',
                'target': 'new',
            }
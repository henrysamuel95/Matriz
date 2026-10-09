#!/usr/bin/python
from odoo import fields, models, api
from odoo.exceptions import UserError
import io
import base64
import seaborn as sns


class CUADRO_FUV_ADMON(models.Model):
    _name = "cuadrofuv"
    _rec_name = "nombre"

    name = fields.Char(string="Nombre")
    new_name = fields.Char(string='Nombre de Revisión')
    def Guardar(self):
        self.ensure_one()
        self.write({'name': self.new_name})
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    cedula = fields.Char(string="Cédula")
    nombre = fields.Char(string="Nombre")
    rpc = fields.Char(string="RPC")
    operacional = fields.Char(string="Operacional")
    area = fields.Char(string="Área")
    ano = fields.Char(string="Año")
    tipo_contrato = fields.Char(string="Tipo de Contrato")
    fec_ini = fields.Date(string="Fecha de Inicio")
    fec_fin = fields.Date(string="Fecha de Fin")
    condicional = fields.Char(string="Condicional")
    contrapartida = fields.Char(string="Contrapartida")
    pendiente = fields.Char(string="Pendiente")
    ene = fields.Float(string="Enero")
    feb = fields.Float(string="Febrero")
    mar = fields.Float(string="Marzo")
    abr = fields.Float(string="Abril")
    may = fields.Float(string="Mayo")
    jun = fields.Float(string="Junio")
    jul = fields.Float(string="Julio")
    ago = fields.Float(string="Agosto")
    sep = fields.Float(string="Septiembre")
    oct = fields.Float(string="Octubre")
    nov = fields.Float(string="Noviembre")
    dic = fields.Float(string="Diciembre")
    total = fields.Float(string="Total", compute='_compute_total')
    @api.depends('ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic')
    def _compute_total(self):
        for record in self:
            total = record.ene + record.feb + record.mar + record.abr + record.may + record.jun + record.jul + record.ago + record.sep + record.oct + record.nov + record.dic
            record.total = total

    def generar_grafica(self):
        import matplotlib.pyplot as plt
        x = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
        y = [self.ene, self.feb, self.mar, self.abr, self.may, self.jun, self.jul, self.ago, self.sep, self.oct,
             self.nov, self.dic]

        plt.figure(figsize=(15, 7))  # Ajusta el tamaño de la figura
        plt.tight_layout(pad=2)  # Ajusta el espaciado
        sns.barplot(x=x, y=y)
        plt.xlabel('Meses')
        plt.ylabel('Valores')
        plt.title('Gráfico de Valores Mensuales')
        plt.xticks(rotation=45)  # Rota los nombres de los meses para mejor visualización
        plt.tight_layout()  # Ajusta el espaciado

        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', bbox_inches='tight')  # Ajusta el espacio alrededor del gráfico
        buffer.seek(0)
        plt.close()

        image_data = base64.b64encode(buffer.getvalue())
        return image_data

    grafica = fields.Binary(string="Gráfica", readonly=True, compute='_compute_grafica')

    @api.depends('ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic')
    def _compute_grafica(self):
        for record in self:
            grafica = record.generar_grafica()
            record.grafica = grafica
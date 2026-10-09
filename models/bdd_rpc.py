from odoo import models, fields, api

class BddRpc(models.Model):
    _name = 'bdd_rpc'
    _description = 'Modelo para gestionar solicitudes RPC'
    # Relación con otro modelo
    related_model_ids = fields.One2many('bdd_rpc_related', 'bdd_rpc_id', string='Relación con modelo relacionado')




class BddRpcRelated(models.Model):
    _name = 'bdd_rpc_related'
    _description = 'Modelo relacionado con BddRpc'

    bdd_rpc_id = fields.Many2one('bdd_rpc', string='Referencia a BddRpc')
    contratista1 = fields.Many2one(comodel_name='contrataciones_fuv', string="Contratista", tracking=True)
    # Campos primera vista tree
    un = fields.Char(string='UN')
    vigencia = fields.Integer(string='Vigencia')
    pre_item = fields.Char(string='Pre-Item')
    consecutivo = fields.Char(string='Consecutivo')
    item = fields.Char(string='Item')
    cpd = fields.Char(string='CPD')
    rpc = fields.Char(string='RPC')
    concepto_rte_fte = fields.Char(string='Concepto RTE FTE')

    # Campos segunda vista tree
    tipo_solicitud = fields.Char(string='Tipo de aolicitud')
    centro_costo = fields.Char(string='Centro de costo')
    fecha_solicitud = fields.Date(string='Fecha aolicitud')
    nombre_solicitante = fields.Many2one(string='Nombre de quien solicita', related="contratista1.nombre_solicitante3")
    proyecto = fields.Char(string='Proyecto')
    objeto_contrato = fields.Text(string='Objeto del contrato')
    justificacion_solicitud = fields.Text(string='Justificación de la solicitud')
    tipo_id = fields.Selection([
        ('cc', 'Cédula de ciudadanía'),
        ('ce', 'Cédula de extranjería'),
        ('nit', 'NIT'),
        ('otro', 'Otro')
    ], string='Tipo de ID')
    numero_identificacion = fields.Char(string='N.° identificación')
    dv = fields.Char(string='DV')
    nombre_contratista = fields.Char(string='Nombre o Razón Social')
    direccion = fields.Char(string='Dirección', related="contratista1.direccion_contratista")
    telefono = fields.Char(string='Teléfono o celular')
    correo_electronico = fields.Char(string='Correo electrónico')
    tipo_contrato = fields.Char(string='Tipo de contrato')
    fecha_inicio = fields.Date(string='Fecha inicio')
    fecha_final = fields.Date(string='Fecha final')
    riesgo_arl = fields.Char(string='Riesgo ARL')

    # Campos tercera vista tree
    tarifa_arl = fields.Float(string='Tarifa ARL')
    arl_asume = fields.Char(string='ARL la asume')

    # Campos cuarta vista tree
    valor_mensual = fields.Float(string='Valor mensual')
    total_prestacion_servicios = fields.Float(string='Total prestación de servicios')
    total_salarios = fields.Float(string='Total salarios')
    total_otros_ingresos = fields.Float(string='Total de otros ingresos laborales')
    seguridad_social = fields.Float(string='Seguridad social')
    prestaciones_sociales = fields.Float(string='Prestaciones sociales')

    # Campos quinta vista tree
    valor_total_numeros = fields.Float(string='Valor Total (En Números)')

    # Campos sexta vista tree
    valor_total_letras = fields.Text(string='Valor Total (En Letras)')
    descripcion_forma_pago = fields.Text(string='Descripción de la forma de pago')
    actividades_especificas = fields.Text(string='Actividades específicas')
    ciiu = fields.Char(string='CIIU')
    facturacion_electronica = fields.Boolean(string='Facturación electrónica')
    responsable_iva = fields.Boolean(string='Responsable de IVA')
    declara_renta = fields.Boolean(string='Declara renta')
    rst = fields.Boolean(string='RST - Régimen simple de tributación')
    rte = fields.Boolean(string='RTE - Régimen especial')
    regimen_ordinario = fields.Boolean(string='Régimen ordinario')
    municipios = fields.Many2many(string='Municipios')

    # Campos séptima vista tree "ARL"
    nit_arl = fields.Char(string='NIT ARL')
    razon_social_fondo_arl = fields.Char(string='Razón social fondo ARL')
    valor_mensual_arl = fields.Float(string='Valor mensual ARL')
    valor_total_arl = fields.Float(string='Valor total ARL')
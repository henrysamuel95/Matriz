from odoo import api, fields, models


class ContratacionesSgrDatosContratista(models.Model):
    _inherit = 'contrataciones_sgr'

    razon_social = fields.Char(compute='_compute_razon_social')
    nit = fields.Char(related='contratista_proveedor_relacionado.nit', readonly=True)
    fecha_impresion_cc = fields.Date(
        related='contratista_proveedor_relacionado.fecha_impresion_ccm', readonly=True,
    )
    ciudad_oficina_principal = fields.Many2one(
        'municipio', related='contratista_proveedor_relacionado.ciudad_oficina_principal',
        readonly=True,
    )
    direccion_contratista = fields.Char(
        related='contratista_proveedor_relacionado.direccion_fisica', readonly=True,
    )
    correo = fields.Char(related='contratista_proveedor_relacionado.correo_facturacion', readonly=True)
    telefono_contratista = fields.Char(
        related='contratista_proveedor_relacionado.numero_telefono', readonly=True,
    )
    r_legal = fields.Char(
        related='contratista_proveedor_relacionado.nombre_r_legal', readonly=True,
    )
    fecha_ciudad_expedicion_contra = fields.Date(
        related='contratista_proveedor_relacionado.expedicion_contra_j', readonly=True,
    )
    expedicion_contra_N = fields.Date(
        related='contratista_proveedor_relacionado.expedicion_contra_N', readonly=True,
    )
    cedula_representante_legal = fields.Char(compute='_compute_documentos_personales')
    documento_identidad = fields.Char(compute='_compute_documentos_personales')
    ciudad_contratista = fields.Char(compute='_compute_ciudad_contratista')

    @api.depends(
        'contratista_proveedor_relacionado.razon_social',
        'contratista_proveedor_relacionado.name',
    )
    def _compute_razon_social(self):
        for record in self:
            contratista = record.contratista_proveedor_relacionado
            record.razon_social = contratista.razon_social or contratista.name or False

    @api.depends(
        'contratista_proveedor_relacionado.cedula',
        'contratista_proveedor_relacionado.cedula_representante',
    )
    def _compute_documentos_personales(self):
        for record in self:
            contratista = record.contratista_proveedor_relacionado
            record.documento_identidad = str(contratista.cedula) if contratista.cedula else False
            record.cedula_representante_legal = (
                str(contratista.cedula_representante) if contratista.cedula_representante else False
            )

    @api.depends(
        'select_persona',
        'contratista_proveedor_relacionado.ciudad_exp_natural',
        'contratista_proveedor_relacionado.ciudad_exp_representante',
    )
    def _compute_ciudad_contratista(self):
        for record in self:
            contratista = record.contratista_proveedor_relacionado
            if record.select_persona in ('persona_natural', 'contrato_laboral'):
                record.ciudad_contratista = contratista.ciudad_exp_natural.name or False
            elif record.select_persona == 'persona_juridica':
                record.ciudad_contratista = contratista.ciudad_exp_representante.name or False
            else:
                record.ciudad_contratista = False

import re
import unicodedata

from odoo import api, fields, models


class BdOrdenesEspeciales(models.Model):
    _name = 'bd_ordenes_especiales'
    _description = 'Base de datos de órdenes especiales'
    _rec_name = 'nombre_razon_social_contratista'
    _order = 'centro_costo, nombre_razon_social_contratista'

    centro_costo = fields.Char(string='Centro de Costo')
    proyecto = fields.Char(string='PROYECTO')
    objeto_contrato = fields.Text(string='Objeto del Contrato')
    tipo_identificacion = fields.Char(string='Tipo de ID')
    numero_identificacion = fields.Char(string='Numero de Identificación')
    digito_verificacion = fields.Char(string='DV')
    nombre_razon_social_contratista = fields.Char(
        string='Nombre o Razon Social del Contratista',
    )
    direccion = fields.Char(string='Dirección')
    ciudad = fields.Char(string='Ciudad')
    telefono_celular = fields.Char(string='Telefono o celular')
    correo_electronico = fields.Char(string='correo electronico')
    valor_mensual = fields.Float(string='VALOR MENSUAL')
    total_prestacion_servicios_obra_orden = fields.Float(
        string='TOTAL PRESTACION DE SERVICIOS, OBRA, ORDEN DE COMPRA Y/O SERVICIOS',
    )
    total_salarios = fields.Float(string='TOTAL SALARIOS')
    total_otros_ingresos_laborales = fields.Float(
        string='TOTAL DE OTROS INGRESOS LABORALES',
    )
    seguridad_social = fields.Float(string='SEGURIDAD SOCIAL')
    prestaciones_sociales = fields.Float(string='PRESTACIONES SOCIALES')
    valor_total = fields.Float(string='VALOR TOTAL (En Números) ')
    ciiu = fields.Char(string='CIIU')
    facturacion_electronica = fields.Char(string='FACTURACION ELECTRONICA')
    responsable_iva = fields.Char(string='RESPONSABLE DE IVA')
    declara_renta = fields.Char(string='DECLARA RENTA')
    regimen_simple_tributacion = fields.Char(
        string='RST- Régimen Simple de Tributación',
    )
    regimen_especial = fields.Char(string='RTE- Régimen Especial')
    regimen_ordinario = fields.Char(string='Régimen Ordinario')
    nit_completo = fields.Char(
        string='NIT completo',
        compute='_compute_nit_completo',
        store=True,
        index=True,
    )

    @api.depends('numero_identificacion', 'digito_verificacion')
    def _compute_nit_completo(self):
        for record in self:
            numero = self._limpiar_numero_excel(record.numero_identificacion)
            digito = self._limpiar_numero_excel(record.digito_verificacion)
            record.nit_completo = '%s-%s' % (numero, digito) if numero and digito else numero

    @staticmethod
    def _limpiar_numero_excel(valor):
        texto = str(valor or '').strip()
        return texto[:-2] if texto.endswith('.0') else texto

    @staticmethod
    def _normalizar_valor(valor):
        texto = unicodedata.normalize('NFKD', valor or '')
        texto = ''.join(caracter for caracter in texto if not unicodedata.combining(caracter))
        return re.sub(r'[^A-Z0-9]', '', texto.upper())

    @api.model
    def _filtrar_proveedores_unicos(self, registros, limit=None):
        """Conserva un solo registro por NIT o, cuando falta, por razón social."""
        unicos = registros.browse()
        claves_vistas = set()

        for registro in registros:
            nit = self._normalizar_valor(registro.nit_completo)
            nombre = self._normalizar_valor(registro.nombre_razon_social_contratista)
            clave = ('nit', nit) if nit else ('nombre', nombre)
            if not clave[1] or clave in claves_vistas:
                continue

            claves_vistas.add(clave)
            unicos |= registro
            if limit and len(unicos) >= limit:
                break

        return unicos

    def name_get(self):
        resultados = []
        for record in self:
            nombre = record.nombre_razon_social_contratista or 'Proveedor sin nombre'
            etiqueta = '%s [NIT: %s]' % (nombre, record.nit_completo) if record.nit_completo else nombre
            resultados.append((record.id, etiqueta))
        return resultados

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        domain = list(args or [])
        if name:
            domain = [
                '|', '|',
                ('nombre_razon_social_contratista', operator, name),
                ('numero_identificacion', operator, name),
                ('nit_completo', operator, name),
            ] + domain

        registros = self.sudo().search(
            domain,
            order='nombre_razon_social_contratista, id',
        )
        return self._filtrar_proveedores_unicos(registros, limit=limit).name_get()

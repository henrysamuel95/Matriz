from odoo import models, fields, api

class TrazabilidadInterna(models.Model):
    _name = 'trazabilidad_interna'
    _description = 'Trazabilidad Interna de Cambios'
    _order = 'fecha desc'
    
    # --- MÉTODO PARA LIMPIAR LA MIGA DE PAN ---
    def name_get(self):
        """ Hace que en la miga de pan y búsquedas aparezca 'Trazabilidad' en lugar de 'modelo,id' """
        result = []
        for record in self:
            result.append((record.id, "Trazabilidad - " + (record.nombre_registro or "Sin Detalle")))
        return result
    
    fecha = fields.Datetime(string="Fecha y hora", default=fields.Datetime.now, readonly=True)
    usuario_id = fields.Many2one('res.users', string="Usuario", default=lambda self: self.env.user, readonly=True)
    usuario_ejecutor = fields.Char(string="Realizado por", readonly=True)
    
    # 1. Selección actualizada con el modelo de Contrataciones FUV para identidad visual
    modelo_afectado = fields.Selection([
        ('departamento', 'Departamento'),
        ('municipio', 'Municipio'),
        ('departamento_y_municipio', 'Departamentos y Municipios'),
        ('municipios_combinados', 'Municipios Combinados'),
        ('municipios_nuevos', 'Municipios Nuevos'),
        ('informacion_base_agregar', 'Información Base'),
        ('contrataciones_fuv', 'Contrataciones FUV'),
        ('orden_compra_bienes', 'Orden de compra - Bienes'),
        ('orden_compra_servicios', 'Orden de compra - Servicios'),
        ('otro_si', 'Otrosí'),
        ('anticipos', 'Anticipos'),
        ('legalizaciones', 'Legalizaciones'),
        ('res_users', 'Usuarios y roles'),
    ], string="Modelo", readonly=True)
    
    nombre_registro = fields.Char(string="Registro afectado", readonly=True)
    origen_cambio = fields.Char(string="Documento origen", readonly=True, help="El documento padre desde donde se modificó esta línea.")

    tipo_accion = fields.Selection([
        ('creacion', 'Creación'),
        ('edicion', 'Edición'),
        ('eliminacion', 'Eliminación')
    ], string="Acción", readonly=True)
    
    detalle_cambios = fields.Html(string="Detalle de los cambios", readonly=True)

    # Campos relacionales (Mantenemos todos para navegación directa)
    departamento_id = fields.Many2one('departamento', string="Departamento", ondelete='set null')
    municipio_id = fields.Many2one('municipio', string="Municipio", ondelete='set null')
    departamento_y_municipio_id = fields.Many2one('departamento_y_municipio', string="Dep. y Municipio", ondelete='set null')
    municipios_combinados_id = fields.Many2one('municipios_combinados', string="Municipios Combinados", ondelete='set null')
    municipios_nuevos_id = fields.Many2one('municipios_nuevos', string="Municipios Nuevos", ondelete='set null')
    informacion_base_id = fields.Many2one('informacion_base_agregar', string="Información Base", ondelete='set null')
    contratacion_fuv_id = fields.Many2one('contrataciones_fuv', string="Contratación FUV", ondelete='set null')
    orden_compra_bienes_id = fields.Many2one(
        'orden_compra_bienes', string="Orden de compra - Bienes", ondelete='set null')
    orden_compra_servicios_id = fields.Many2one(
        'orden_compra_servicios', string="Orden de compra - Servicios", ondelete='set null')
    otro_si_id = fields.Many2one('otro_si', string="Otrosí", ondelete='set null')
    anticipo_id = fields.Many2one('anticipos', string="Anticipo", ondelete='set null')
    legalizacion_id = fields.Many2one('viaticos', string="Legalización", ondelete='set null')
    usuario_afectado_id = fields.Many2one(
        'res.users', string="Usuario afectado", ondelete='set null', index=True)
    proyecto_id = fields.Many2one(
        'contratacion', string="Proyecto relacionado", ondelete='set null', index=True)

    def _valor_trazabilidad_legible(self, field_obj, value):
        if value in (False, None, ''):
            return 'Vacío'
        if field_obj.type == 'many2one':
            if hasattr(value, 'display_name'):
                return value.display_name or 'Vacío'
            return self.env[field_obj.comodel_name].browse(value).display_name or 'Vacío'
        if field_obj.type == 'selection':
            opciones = dict(field_obj._description_selection(self.env))
            return opciones.get(value, value)
        if field_obj.type == 'boolean':
            return 'Sí' if value else 'No'
        if field_obj.type == 'date':
            return fields.Date.to_string(fields.Date.to_date(value))
        if field_obj.type == 'datetime':
            return fields.Datetime.to_string(fields.Datetime.to_datetime(value))
        if field_obj.type in ('float', 'monetary'):
            return ('{:,.2f}'.format(value)).replace(',', 'X').replace('.', ',').replace('X', '.')
        return str(value)

    def _datos_modelo_contractual(self, record):
        config = {
            'contrataciones_fuv': {
                'modelo': 'contrataciones_fuv',
                'campo': 'contratacion_fuv_id',
                'proyecto': 'proyecto_relacionado',
            },
            'orden_compra_bienes': {
                'modelo': 'orden_compra_bienes',
                'campo': 'orden_compra_bienes_id',
                'proyecto': 'proyecto_relacionado_c',
            },
            'orden_compra_servicios': {
                'modelo': 'orden_compra_servicios',
                'campo': 'orden_compra_servicios_id',
                'proyecto': 'proyecto_relacionado_s',
            },
            'otro_si': {
                'modelo': 'otro_si',
                'campo': 'otro_si_id',
                'proyecto': 'proyecto_relacionado',
                'fuv': 'contratacion_fuv_en_id',
            },
            'anticipos': {
                'modelo': 'anticipos',
                'campo': 'anticipo_id',
                'proyecto': 'proyecto_id',
            },
            'viaticos': {
                'modelo': 'legalizaciones',
                'campo': 'legalizacion_id',
                'proyecto': 'proyecto_relacionado_viaticos',
                'anticipo': 'anticipo_id',
            },
        }.get(record._name)
        if not config:
            return {}

        proyecto = record[config['proyecto']] if config.get('proyecto') else False
        fuv = record[config['fuv']] if config.get('fuv') else False
        anticipo = record[config['anticipo']] if config.get('anticipo') else False
        return {
            'modelo_afectado': config['modelo'],
            'campo_relacion': config['campo'],
            'proyecto': proyecto,
            'fuv': fuv,
            'anticipo': anticipo,
        }

    @api.model
    def registrar_accion(self, registros, accion, vals=None):
        """ Gatillo Universal: Maneja Creación, Edición y Eliminación para todos los modelos """
        campos_ignorados = [
            'message_follower_ids', 'message_ids', 'activity_ids', 
            'write_uid', 'write_date', 'create_uid', 'create_date',
            'dedicacion', 'dedicacion_comibinados'
        ]
        
        mensajes_cambio = []

        for record in registros:
            html_cambios = ""
            
            # --- 1. Lógica de Identidad de Modelo (Prioriza el Padre) ---
            # Si el registro es una línea de municipio pero tiene un FUV, mostramos 'Contrataciones FUV'
            modelo_visual = record._name
            if record._name == 'departamento_y_municipio' and record.contrataciones_fuv_id:
                modelo_visual = 'contrataciones_fuv'
            datos_contractuales = self._datos_modelo_contractual(record)
            if datos_contractuales:
                modelo_visual = datos_contractuales['modelo_afectado']

            # --- 2. Generación de Nombre Legible ---
            nombre_legible = record.display_name
            if not nombre_legible or record._name in str(nombre_legible):
                if record._name == 'departamento_y_municipio':
                    dep = record.departamentos.name if record.departamentos else 'Sin Dep'
                    mun = record.municipios2.name if record.municipios2 else 'Sin Mun'
                    nombre_legible = f"{dep} - {mun}"
                else:
                    nombre_legible = f"{record._description or record._name} (ID: {record.id})"

            # --- 3. Detección del Documento Origen y el ID del Padre ---
            origen = "Modificación Directa / Desconocido"
            fuv_id = False
            base_id = False

            if record._name == 'departamento_y_municipio':
                if record.contrataciones_fuv_id: 
                    origen = f"Contrataciones FUV: {record.contrataciones_fuv_id.display_name}"
                    fuv_id = record.contrataciones_fuv_id.id
                elif record.anexo_id: 
                    origen = f"Anexo: {record.anexo_id.display_name}"
            elif record._name == 'informacion_base_agregar':
                if record.contratacion_id: 
                    origen = f"Contratación: {record.contratacion_id.display_name}"
                base_id = record.id
            elif record._name == 'municipios_combinados' and record.proceso_id:
                origen = f"Proceso: {record.proceso_id.display_name}"
            elif datos_contractuales:
                proyecto = datos_contractuales['proyecto']
                if proyecto:
                    origen = f"Proyecto: {proyecto.display_name}"
                else:
                    origen = f"{record._description or record._name}: {nombre_legible}"
                fuv_id = datos_contractuales['fuv'].id if datos_contractuales['fuv'] else False

            # --- 4. Generación del Detalle HTML (Mantenemos lógica Many2many) ---
            if accion == 'edicion' and vals:
                lineas = []
                for field_name, new_val in vals.items():
                    if field_name in campos_ignorados or field_name not in record._fields: continue
                    f_obj = record._fields[field_name]

                    etiqueta = f_obj.string or field_name
                    nombre_variable = f"<code>{field_name}</code>"
                    if f_obj.type == 'binary':
                        lineas.append(
                            f"<li><b>{etiqueta}</b> ({nombre_variable}): Archivo actualizado</li>")
                        continue
                    if f_obj.type in ('one2many', 'many2many'):
                        lineas.append(
                            f"<li><b>{etiqueta}</b> ({nombre_variable}): "
                            "Contenido relacionado actualizado</li>")
                        continue

                    old_val = record[field_name]
                    old_str = self._valor_trazabilidad_legible(f_obj, old_val)
                    new_str = self._valor_trazabilidad_legible(f_obj, new_val)

                    if old_str != new_str:
                        lineas.append(
                            f"<li><b>{etiqueta}</b> ({nombre_variable}): "
                            f"<span style='color:red;'>{old_str}</span> ➔ "
                            f"<span style='color:green;'>{new_str}</span></li>")
                
                if not lineas: continue 
                html_cambios = f"<p>🟡 <b>Registro Editado:</b></p><ul>{''.join(lineas)}</ul>"

            elif accion == 'creacion':
                html_cambios = f"<p>🟢 <b>Registro Creado:</b> {nombre_legible}</p>"
            elif accion == 'eliminacion':
                html_cambios = f"<p>🔴 <b>Registro Eliminado:</b> {nombre_legible}</p>"

            # --- 5. Armar el Diccionario con la Identidad Visual ---
            datos = {
                'tipo_accion': accion,
                'nombre_registro': nombre_legible,
                'modelo_afectado': modelo_visual, # <--- SE GUARDA COMO FUV SI TIENE PADRE
                'origen_cambio': origen,
                'detalle_cambios': html_cambios,
                'contratacion_fuv_id': fuv_id,
                'informacion_base_id': base_id,
            }

            if datos_contractuales:
                proyecto = datos_contractuales['proyecto']
                datos['proyecto_id'] = proyecto.id if proyecto else False
                if datos_contractuales['anticipo']:
                    datos['anticipo_id'] = datos_contractuales['anticipo'].id
                if accion != 'eliminacion':
                    datos[datos_contractuales['campo_relacion']] = record.id
            
            if accion != 'eliminacion':
                if record._name == 'departamento': datos['departamento_id'] = record.id
                elif record._name == 'municipio': datos['municipio_id'] = record.id
                elif record._name == 'departamento_y_municipio': datos['departamento_y_municipio_id'] = record.id
                
            mensajes_cambio.append(datos)

        if mensajes_cambio:
            self.sudo().create(mensajes_cambio)


class ContratacionesFuvTrazabilidad(models.Model):
    _inherit = 'contrataciones_fuv'

    @api.model
    def create(self, vals):
        record = super().create(vals)
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        return record

    def write(self, vals):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        return super().unlink()


class OrdenCompraBienesTrazabilidad(models.Model):
    _inherit = 'orden_compra_bienes'

    @api.model
    def create(self, vals):
        record = super().create(vals)
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        return record

    def write(self, vals):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        return super().unlink()


class OrdenCompraServiciosTrazabilidad(models.Model):
    _inherit = 'orden_compra_servicios'

    @api.model
    def create(self, vals):
        record = super().create(vals)
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        return record

    def write(self, vals):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        return super().unlink()


class OtroSiTrazabilidad(models.Model):
    _inherit = 'otro_si'

    @api.model
    def create(self, vals):
        record = super().create(vals)
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        return record

    def write(self, vals):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        return super().unlink()


class AnticiposTrazabilidad(models.Model):
    _inherit = 'anticipos'

    @api.model
    def create(self, vals):
        record = super().create(vals)
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        return record

    def write(self, vals):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        return super().unlink()


class LegalizacionesTrazabilidad(models.Model):
    _inherit = 'viaticos'

    @api.model
    def create(self, vals):
        record = super().create(vals)
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(record, 'creacion', vals)
        return record

    def write(self, vals):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'edicion', vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('skip_trazabilidad_interna'):
            self.env['trazabilidad_interna'].registrar_accion(self, 'eliminacion')
        return super().unlink()

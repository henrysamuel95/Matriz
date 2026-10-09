import logging
import uuid
from enum import Enum

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import formatLang, html_escape

from ..drive import GoogleDriveAuth


_logger = logging.getLogger(__name__)


class EstadoOrdenEspecial(str, Enum):
    COORDINADOR = 'coordinador'
    VENTANILLA = 'ventanilla'
    CONTABILIDAD = 'contabilidad'
    AUXILIAR_TESORERIA = 'auxiliar_tesoreria'
    TESORERIA = 'tesoreria'
    FINALIZADO = 'finalizado'


ESTADOS_ORDEN_ESPECIAL = [
    (EstadoOrdenEspecial.COORDINADOR.value, 'Coordinador'),
    (EstadoOrdenEspecial.VENTANILLA.value, 'Ventanilla'),
    (EstadoOrdenEspecial.CONTABILIDAD.value, 'Contabilidad'),
    (EstadoOrdenEspecial.AUXILIAR_TESORERIA.value, 'Auxiliar de Tesorería'),
    (EstadoOrdenEspecial.TESORERIA.value, 'Tesorería'),
    (EstadoOrdenEspecial.FINALIZADO.value, 'Finalizado'),
]


class OrdenesEspeciales(models.Model):
    _name = 'ordenes_especiales'
    _description = 'Orden especial para persona jurídica'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    def init(self):
        """Aplica los valores iniciales también a órdenes antiguas vacías."""
        self.env.cr.execute("""
            UPDATE ordenes_especiales
               SET forma_pago1 = 'Contado'
             WHERE forma_pago1 IS NULL OR BTRIM(forma_pago1) = ''
        """)
        self.env.cr.execute("""
            UPDATE ordenes_especiales
               SET tiempo_entrega = 'Inmediato'
             WHERE tiempo_entrega IS NULL OR BTRIM(tiempo_entrega) = ''
        """)

    name = fields.Char(string='Nombre', related='proveedor')
    descripcion = fields.Text(string='Descripción')
    proyecto_relacionado_s = fields.Many2one(
        'contratacion', string='Proyecto relacionado', readonly=True,
    )
    subtotal = fields.Float(string='Subtotal', compute='_compute_subtotal', store=True)
    iva = fields.Float(string='IVA', store=True)
    select_fondos = fields.Selection(
        selection=lambda self: self._seleccion_fondos_disponibles(),
        string='Seleccionar fondo',
    )
    gastos_expedicion = fields.Float(string='Gastos expedición')
    total = fields.Float(string='Total Compra (Producción)', compute='_compute_totales_separados', store=True)
    total_poliza = fields.Float(string='Total Póliza', compute='_compute_totales_separados', store=True)
    drive_file_id = fields.Many2one('drive.file', string='Carpeta de órdenes especiales', readonly=True, copy=False)
    drive_folder_token = fields.Char(
        string='Identificador de carpeta de la orden', readonly=True, copy=False,
        default=lambda self: uuid.uuid4().hex[:12],
    )
    facturas_folder_id = fields.Many2one('drive.file', string='Carpeta Facturas', readonly=True, copy=False)
    otros_folder_id = fields.Many2one('drive.file', string='Carpeta Otros', readonly=True, copy=False)
    documento_ids = fields.One2many('ordenes_especiales.documento', 'orden_id', string='Documentos')
    factura_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id', string='Facturas',
        domain=[('clase_documento', '=', 'factura')],
    )
    otro_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id', string='Otros',
        domain=[('clase_documento', '=', 'otro')],
    )
    certificacion_conjunto_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id',
        string='Certificación conjunta',
        domain=[('clase_documento', '=', 'certificacion_conjunto')],
    )
    acusacion_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id', string='Causación',
        domain=[('clase_documento', '=', 'acusacion')],
    )
    cruce_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id', string='Documento Cruce',
        domain=[('clase_documento', '=', 'documento_cruce')],
    )
    orden_pago_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id', string='Orden de pago',
        domain=[('clase_documento', '=', 'orden_pago')],
    )
    comprobante_pago_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id', string='Comprobante de pago',
        domain=[('clase_documento', '=', 'comprobante_pago')],
    )
    comprobante_bancario_documento_ids = fields.One2many(
        'ordenes_especiales.documento', 'orden_id', string='Comprobante Bancario',
        domain=[('clase_documento', '=', 'comprobante_bancario')],
    )
    departamento_municipio_ids = fields.One2many('ordenes_especiales.municipio', 'orden_id', string='Departamentos y Municipios')
    objeto_contractual = fields.Text(string='Objeto contractual', readonly=True)
    codigo_ciiu = fields.Char(string='Código CIIU', readonly=True)
    compromiso_presupuestal = fields.Char('RPC')
    f_electronica = fields.Boolean('Facturación electronica', readonly=True)
    r_iva = fields.Boolean('Responsable de IVA', readonly=True)
    no_r_iva = fields.Boolean('No responsable de IVA', readonly=True)
    declaracion_renta = fields.Boolean(string='Declaración Renta', readonly=True)
    regimen_simple_tributacion = fields.Boolean(
        string='Régimen Simple de Tributación', readonly=True,
    )
    regimen_especial = fields.Boolean(string='Régimen Especial', readonly=True)
    regimen_ordinario = fields.Boolean(string='Régimen Ordinario', readonly=True)
    nombre_proyecto_relacionado = fields.Char(related='proyecto_relacionado_s.new_name', string='Nombre del proyecto relacionado')
    centro_costo = fields.Char(related='proyecto_relacionado_s.mcb_centro_costo', string='Centro de Costo', store=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', default=lambda self: self.env.company.currency_id)
    solicitante = fields.Char(string='Solicitante', readonly=True)
    solicitante_usuario_id = fields.Many2one(
        'res.users', string='Solicitante', copy=False,
    )
    forma_pago = fields.Selection([('contado', 'De Contado'), ('credito', 'Crédito')], string='Forma de Pago')
    forma_pago1 = fields.Char(
        string='Forma de pago', default='Contado',
    )
    fecha = fields.Date(string='Fecha')
    fecha_inicio = fields.Date(string='Fecha inicio')
    fecha_fin = fields.Date(string='Fecha fin')
    fecha_solicitud = fields.Date(string='Fecha de Solicitud')
    fecha_solicitud_texto = fields.Char(string='Fecha Solicitud en Texto', compute='_compute_fecha_solicitud_texto', store=True)
    proveedor_id = fields.Many2one(
        'bd_ordenes_especiales',
        string='Proveedor',
        required=True,
        ondelete='restrict',
    )
    proveedor = fields.Char(string='Proveedor', required=True, readonly=True)
    direccion = fields.Char(string='Dirección', readonly=True)
    nit = fields.Char(string='NIT', required=True, readonly=True)
    telefono = fields.Char(string='Teléfono', readonly=True)
    cel = fields.Char(string='Celular', readonly=True)
    email = fields.Char(string='Email', readonly=True)
    lugar_prestacio_servicio = fields.Char(string='Lugar de Venta')
    ciudad = fields.Many2one(
        comodel_name='municipio', string='Ciudad', readonly=True,
    )
    lineas_ids = fields.One2many('ordenes_especiales.linea', 'orden_id', string='Líneas de Servicios')
    descripcion_del_servicio = fields.Char(string='Descripción', related='lineas_ids.descripcion')
    justificacion = fields.Text(string='Justificación')
    tiempo_entrega = fields.Char(
        string='Tiempo de Entrega', default='Inmediato',
    )
    estado_ordenes_especiales = fields.Selection(
        ESTADOS_ORDEN_ESPECIAL,
        string='Estado de la orden especial',
        default=EstadoOrdenEspecial.COORDINADOR.value,
        tracking=True,
    )
    seleccion_persona = fields.Selection([('persona_juridica', 'Persona jurídica')], string='Tipo de persona', default='persona_juridica', required=True, readonly=True)
    observacion_coordinador = fields.Text('Observaciones: ')
    observacion_financiera = fields.Text('Observaciones: ')
    observacion_general = fields.Text(string='Observaciones de devolución')
    ultimo_estado_devolucion = fields.Selection(
        ESTADOS_ORDEN_ESPECIAL,
        string='Último estado que devolvió',
        copy=False,
    )
    ultimo_usuario_devolucion_id = fields.Many2one(
        'res.users',
        string='Último usuario que devolvió',
        copy=False,
    )
    estado_retorno_devolucion = fields.Selection(
        ESTADOS_ORDEN_ESPECIAL,
        string='Estado al que debe retornar',
        copy=False,
    )
    rcf = fields.Char(string='RCF')
    es_cruce = fields.Boolean(string='Es cruce')
    consecutivo_contabilidad = fields.Char(string='Consecutivo')

    consecutivo_tesoreria = fields.Char(string='Consecutivo')
    certificacion_conjunto_aprobada_coordinador = fields.Boolean(
        string='Coordinador aprueba el documento', tracking=True, copy=False,
    )
    certificacion_conjunto_aprobada_ventanilla = fields.Boolean(
        string='Ventanilla aprueba el documento', tracking=True, copy=False,
    )
    usuario_coordinacion_id = fields.Many2one(
        'res.users', string='Responsable de Coordinación', copy=False,
    )
    usuario_ventanilla_id = fields.Many2one(
        'res.users', string='Asignar a Ventanilla', copy=False,
        domain=lambda self: self._domain_usuarios_grupo(
            'Matriz.group_contratacion_ventanilla_unica'),
    )
    usuario_contabilidad_id = fields.Many2one(
        'res.users', string='Asignar a Contabilidad', copy=False,
        domain=lambda self: self._domain_usuarios_grupo('Matriz.group_contabilidad'),
    )
    usuario_auxiliar_tesoreria_id = fields.Many2one(
        'res.users', string='Asignar a Auxiliar de Tesorería', copy=False,
        domain=lambda self: self._domain_usuarios_grupo('Matriz.group_tesoreria_auxiliar'),
    )
    usuario_tesoreria_id = fields.Many2one(
        'res.users', string='Asignar a Tesorería', copy=False,
        domain=lambda self: self._domain_usuarios_grupo('Matriz.group_tesoreria'),
    )

    @api.model
    def _domain_usuarios_grupo(self, grupo_xml_id):
        grupo = self.env.ref(grupo_xml_id, raise_if_not_found=False)
        if not grupo:
            return [('id', '=', False)]
        return [
            ('active', '=', True),
            ('share', '=', False),
            ('groups_id', 'in', [grupo.id]),
        ]

    @api.model
    def _seleccion_fondos_disponibles(self):
        opciones = [
            ('es_produccion', 'Producción'),
            ('es_poliza', 'Póliza'),
        ]
        if self.env.context.get('ocultar_polizas_ordenes_especiales'):
            return opciones[:1]
        return opciones

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group('Matriz.group_contratacion_coordinador'):
            raise AccessError(_('Solo el Coordinador puede crear órdenes especiales.'))

        proyecto_contexto = self.env.context.get('default_proyecto_relacionado_s')
        for vals in vals_list:
            if not vals.get('proyecto_relacionado_s') and proyecto_contexto:
                vals['proyecto_relacionado_s'] = proyecto_contexto
            if not vals.get('proyecto_relacionado_s'):
                raise UserError(_(
                    'No fue posible identificar el proyecto relacionado. '
                    'Abra Órdenes Especiales desde el proyecto correspondiente.'
                ))
            vals.setdefault('forma_pago1', 'Contado')
            vals.setdefault('tiempo_entrega', 'Inmediato')
            if vals.get('solicitante_usuario_id'):
                solicitante = self._validar_solicitante_usuario(
                    vals['solicitante_usuario_id']
                )
                vals['solicitante'] = solicitante.name
            if vals.get('proveedor_id'):
                proveedor = self.env['bd_ordenes_especiales'].sudo().browse(
                    vals['proveedor_id']
                ).exists()
                if proveedor:
                    vals.update(self._valores_proveedor_bdd(proveedor))

        records = super().create(vals_list)
        for record in records:
            record._asegurar_carpetas_documentos()
        return records

    def unlink(self):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede eliminar órdenes especiales.'))
        for record in self:
            # El ondelete SQL no ejecuta el unlink del documento; se hace aquí
            # para retirar primero cada archivo remoto de esta orden.
            record.documento_ids.sudo().unlink()
            record._eliminar_carpeta_documentos_drive()
        return super().unlink()

    def _eliminar_carpeta_documentos_drive(self):
        """Elimina únicamente la carpeta raíz creada para esta orden especial."""
        self.ensure_one()
        raiz = self.drive_file_id.sudo()
        if not raiz:
            return
        if self._carpeta_compartida_con_otra_orden(raiz):
            return
        subcarpetas = self.env['drive.file'].sudo().search([
            ('parent_folder_id', '=', raiz.id),
        ])
        if subcarpetas:
            subcarpetas.unlink()
        if raiz.exists():
            raiz.unlink()

    def _puede_editar_en_estado(self):
        if self.env.user.has_group('Matriz.group_soporte'):
            return True
        grupos_por_estado = {
            'coordinador': (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
            ),
            'ventanilla': ('Matriz.group_contratacion_ventanilla_unica',),
            'contabilidad': ('Matriz.group_contabilidad',),
            'auxiliar_tesoreria': ('Matriz.group_tesoreria_auxiliar',),
            'tesoreria': ('Matriz.group_tesoreria',),
        }
        return any(
            self.env.user.has_group(grupo)
            for grupo in grupos_por_estado.get(self.estado_ordenes_especiales, ())
        )

    def write(self, vals):
        if 'rcf' in vals and not self.env.su:
            for record in self:
                if (record.rcf or False) != (vals['rcf'] or False):
                    if (
                        record.estado_ordenes_especiales != 'coordinador'
                        or not self.env.user.has_group(
                            'Matriz.group_contratacion_coordinador'
                        )
                    ):
                        raise AccessError(_(
                            'Solo el Coordinador puede modificar el RCF '
                            'durante la etapa de Coordinación.'
                        ))
        if 'solicitante_usuario_id' in vals:
            for record in self:
                if (
                    not self.env.su
                    and (
                        record.estado_ordenes_especiales != 'coordinador'
                        or not (
                            self.env.user.has_group(
                                'Matriz.group_contratacion_coordinador'
                            )
                            or self.env.user.has_group(
                                'Matriz.group_contratacion_asis_coordinador'
                            )
                        )
                    )
                    and (record.solicitante_usuario_id.id or False)
                    != (vals['solicitante_usuario_id'] or False)
                ):
                    raise AccessError(_(
                        'El solicitante solo puede cambiarse en Coordinación '
                        'por el Coordinador o el Asistente de Coordinación.'
                    ))
                if vals['solicitante_usuario_id']:
                    solicitante = record._validar_solicitante_usuario(
                        vals['solicitante_usuario_id']
                    )
                    vals['solicitante'] = solicitante.name
                else:
                    vals['solicitante'] = False
        elif 'solicitante' in vals:
            for record in self:
                if (record.solicitante or False) != (vals['solicitante'] or False):
                    raise UserError(_(
                        'El solicitante debe elegirse desde la lista de usuarios '
                        'activos del sistema.'
                    ))
        campos_bdd = {
            'proveedor_id', 'proveedor', 'nit', 'direccion', 'telefono', 'cel',
            'email', 'ciudad', 'codigo_ciiu', 'objeto_contractual',
            'f_electronica',
            'r_iva', 'no_r_iva', 'declaracion_renta',
            'regimen_simple_tributacion', 'regimen_especial',
            'regimen_ordinario',
        }
        campos_recibidos = campos_bdd.intersection(vals)
        for record in self:
            for campo in campos_recibidos:
                valor_actual = record[campo]
                valor_nuevo = vals[campo]
                if campo in ('proveedor_id', 'ciudad'):
                    valor_actual = valor_actual.id
                if valor_actual in (None, ''):
                    valor_actual = False
                if valor_nuevo in (None, ''):
                    valor_nuevo = False
                if valor_actual != valor_nuevo:
                    raise UserError(_(
                        'El proveedor y los datos tomados de la BDD quedan '
                        'bloqueados después de crear la orden especial.'
                    ))
        if not self.env.su:
            for record in self:
                if (
                    'certificacion_conjunto_aprobada_coordinador' in vals
                    and vals['certificacion_conjunto_aprobada_coordinador']
                    != record.certificacion_conjunto_aprobada_coordinador
                ):
                    if not self.env.user.has_group(
                        'Matriz.group_contratacion_coordinador'
                    ):
                        raise AccessError(_(
                            'Solo el Coordinador puede aprobar la Certificación conjunta.'
                        ))
                    if record.estado_ordenes_especiales != 'coordinador':
                        raise UserError(_(
                            'La aprobación de Coordinación solo puede cambiarse '
                            'durante la etapa de Coordinación.'
                        ))
                    if (
                        vals['certificacion_conjunto_aprobada_coordinador']
                        and not record.certificacion_conjunto_documento_ids
                    ):
                        raise UserError(_(
                            'Debe cargar la Certificación conjunta antes de aprobarla.'
                        ))
                if (
                    'certificacion_conjunto_aprobada_ventanilla' in vals
                    and vals['certificacion_conjunto_aprobada_ventanilla']
                    != record.certificacion_conjunto_aprobada_ventanilla
                ):
                    if not self.env.user.has_group(
                        'Matriz.group_contratacion_ventanilla_unica'
                    ):
                        raise AccessError(_(
                            'Solo Ventanilla puede aprobar la Certificación conjunta.'
                        ))
                    if record.estado_ordenes_especiales != 'ventanilla':
                        raise UserError(_(
                            'La aprobación de Ventanilla solo puede cambiarse '
                            'durante la etapa de Ventanilla.'
                        ))
                    if (
                        vals['certificacion_conjunto_aprobada_ventanilla']
                        and not record.certificacion_conjunto_documento_ids
                    ):
                        raise UserError(_(
                            'Debe cargar la Certificación conjunta antes de aprobarla.'
                        ))
        if not self.env.su:
            for record in self:
                if not record._puede_editar_en_estado():
                    raise AccessError(_('No tiene permiso para editar la orden en esta etapa.'))
        return super().write(vals)

    @api.model
    def _validar_solicitante_usuario(self, usuario_id):
        usuario = self.env['res.users'].sudo().browse(usuario_id).exists()
        if not usuario or not usuario.active or usuario.share:
            raise UserError(_(
                'El solicitante debe ser un usuario interno activo.'
            ))
        return usuario

    @api.model
    def _valores_proveedor_bdd(self, proveedor):
        """Construye la fotografía del proveedor que se conserva en la orden."""
        responsable_iva = self._valor_bdd_es_verdadero(proveedor.responsable_iva)
        telefono_celular = proveedor.telefono_celular or False
        municipio = self._municipio_desde_texto_bdd(proveedor.ciudad)
        return {
            'proveedor': proveedor.nombre_razon_social_contratista or False,
            'nit': proveedor.nit_completo or False,
            'direccion': proveedor.direccion or False,
            'telefono': telefono_celular,
            'cel': telefono_celular,
            'email': proveedor.correo_electronico or False,
            'ciudad': municipio.id if municipio else False,
            'codigo_ciiu': proveedor.ciiu or False,
            'objeto_contractual': proveedor.objeto_contrato or False,
            'f_electronica': self._valor_bdd_es_verdadero(
                proveedor.facturacion_electronica
            ),
            'r_iva': responsable_iva,
            'no_r_iva': not responsable_iva,
            'declaracion_renta': self._valor_bdd_es_verdadero(
                proveedor.declara_renta
            ),
            'regimen_simple_tributacion': self._valor_bdd_es_verdadero(
                proveedor.regimen_simple_tributacion
            ),
            'regimen_especial': self._valor_bdd_es_verdadero(
                proveedor.regimen_especial
            ),
            'regimen_ordinario': self._valor_bdd_es_verdadero(
                proveedor.regimen_ordinario
            ),
        }

    @api.model
    def _municipio_desde_texto_bdd(self, nombre_ciudad):
        """Relaciona la ciudad textual importada sin crear municipios nuevos."""
        nombre_ciudad = (nombre_ciudad or '').strip()
        if not nombre_ciudad:
            return self.env['municipio'].browse()
        return self.env['municipio'].sudo().search(
            [('name', '=ilike', nombre_ciudad)], limit=1,
        )

    def _crear_carpeta_hija(self, carpeta_padre, nombre):
        """Crea o reutiliza una carpeta hija en Drive y en su espejo de Odoo."""
        carpeta = self.env['drive.file'].sudo().search([
            ('parent_folder_id', '=', carpeta_padre.id), ('folder_name', '=', nombre),
        ], limit=1)
        if carpeta and carpeta.folder_id2:
            return carpeta

        drive = GoogleDriveAuth.get_instance().get_drive()
        query = (
            "'%s' in parents and title='%s' and "
            "mimeType='application/vnd.google-apps.folder' and trashed=false"
        ) % (carpeta_padre.folder_id2, nombre)
        coincidencias = drive.ListFile({
            'q': query, 'supportsAllDrives': True, 'includeItemsFromAllDrives': True,
        }).GetList()
        carpeta_google = coincidencias[0] if coincidencias else False
        if not carpeta_google:
            carpeta_google = drive.CreateFile({
                'title': nombre,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': carpeta_padre.folder_id2}],
            })
            carpeta_google.Upload(param={'supportsAllDrives': True})

        valores = {
            'folder_name': nombre,
            'folder_id2': carpeta_google['id'],
            'drive_id': carpeta_padre.drive_id.id,
            'parent_folder_id': carpeta_padre.id,
            'proyecto_relacionado': self.proyecto_relacionado_s.id,
        }
        if carpeta:
            carpeta.sudo().write(valores)
        else:
            carpeta = self.env['drive.file'].sudo().create(valores)
        return carpeta

    def _carpeta_compartida_con_otra_orden(self, carpeta):
        """Detecta vínculos duplicados por registro Odoo o por ID de Drive."""
        self.ensure_one()
        ordenes = self.env['ordenes_especiales'].sudo()
        if ordenes.search_count([
            ('id', '!=', self.id), ('drive_file_id', '=', carpeta.id),
        ]):
            return True
        return bool(carpeta.folder_id2 and ordenes.search_count([
            ('id', '!=', self.id),
            ('drive_file_id.folder_id2', '=', carpeta.folder_id2),
        ]))

    def _mover_documentos_a_carpeta_propia(self, carpeta):
        """Separa únicamente los archivos ligados a esta orden de una carpeta antigua."""
        self.ensure_one()
        drive = GoogleDriveAuth.get_instance().get_drive()
        for documento in self.documento_ids.sudo():
            archivo_odoo = documento.drive_document_id.sudo()
            if not archivo_odoo or not archivo_odoo.file_id:
                continue
            if (
                archivo_odoo.folder_id == carpeta.folder_id2
                and archivo_odoo.stage2_id.id == carpeta.id
            ):
                continue
            archivo_google = drive.CreateFile({'id': archivo_odoo.file_id})
            archivo_google.FetchMetadata(fields='id,parents')
            padres = [padre['id'] for padre in (archivo_google.get('parents') or [])]
            origen = archivo_odoo.folder_id
            if carpeta.folder_id2 not in padres:
                if not origen or origen not in padres:
                    raise UserError(_(
                        'No se pudo separar el documento %s: su carpeta de origen '
                        'no coincide con la registrada en Odoo.'
                    ) % documento.archivo_nombre)
                drive.auth.service.files().patch(
                    fileId=archivo_odoo.file_id,
                    addParents=carpeta.folder_id2,
                    removeParents=origen,
                    fields='id,parents',
                ).execute()
            archivo_odoo.with_context(
                skip_drive_sync=True, skip_integrity=True,
            ).write({
                'folder_id': carpeta.folder_id2,
                'stage2_id': carpeta.id,
            })

    def _asegurar_carpetas_documentos(self):
        """Garantiza una carpeta de Drive exclusiva para esta orden especial."""
        self.ensure_one()
        raiz_anterior = self.drive_file_id.sudo()
        if raiz_anterior and raiz_anterior.folder_id2:
            if not self._carpeta_compartida_con_otra_orden(raiz_anterior):
                return
        if not self.proyecto_relacionado_s:
            return
        etapa = self.env['drive_stage'].sudo().search([
            ('proyecto_relacionado', '=', self.proyecto_relacionado_s.id),
            '|', ('name', '=', '3. CONTRATOS PS'), ('folder_name', '=', '3. CONTRATOS PS'),
        ], limit=1)
        if not etapa or not etapa.folder_id:
            raise UserError(_('El proyecto no tiene configurada la carpeta 3. CONTRATOS PS en Drive.'))

        drive = GoogleDriveAuth.get_instance().get_drive()
        if not self.drive_folder_token:
            self.sudo().write({'drive_folder_token': uuid.uuid4().hex[:12]})
        nombre_orden = (self.proveedor or 'Proveedor').strip()
        nombre_raiz = 'Orden especial %s [OE-%s-%s]' % (
            nombre_orden, self.id, self.drive_folder_token,
        )
        nombre_query = nombre_raiz.replace('\\', '\\\\').replace("'", "\\'")
        coincidencias = drive.ListFile({
            'q': "'%s' in parents and title='%s' and mimeType='application/vnd.google-apps.folder' and trashed=false" % (etapa.folder_id, nombre_query),
            'supportsAllDrives': True, 'includeItemsFromAllDrives': True,
        }).GetList()
        carpeta_google = coincidencias[0] if coincidencias else False
        if not carpeta_google:
            carpeta_google = drive.CreateFile({
                'title': nombre_raiz,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [{'id': etapa.folder_id}],
            })
            carpeta_google.Upload(param={'supportsAllDrives': True})
        raiz = self.env['drive.file'].sudo().search([
            ('folder_id2', '=', carpeta_google['id']),
        ], limit=1)
        if raiz and self._carpeta_compartida_con_otra_orden(raiz):
            raise UserError(_(
                'La carpeta identificada para esta orden especial está vinculada '
                'a otra orden. Contacte a Soporte antes de cargar documentos.'
            ))
        if not raiz:
            raiz = self.env['drive.file'].sudo().create({
                'folder_name': nombre_raiz, 'folder_id2': carpeta_google['id'],
                'drive_id': etapa.id, 'proyecto_relacionado': self.proyecto_relacionado_s.id,
                'select_persona': 'bienes_y_sevicios',
            })
        if raiz_anterior and raiz_anterior.folder_id2:
            self._mover_documentos_a_carpeta_propia(raiz)
        self.sudo().write({'drive_file_id': raiz.id})


    def _validar_documentos(self, *clases):
        faltantes = [
            clase for clase in clases
            if not self.documento_ids.filtered(
                lambda documento: documento.clase_documento == clase
            )
        ]
        if faltantes:
            nombres = dict(self.env['ordenes_especiales.documento']._fields['clase_documento'].selection)
            raise UserError(_('Debe cargar: %s.') % ', '.join(nombres[clase] for clase in faltantes))

    def _action_cargar_documento_unico(self, clase_documento, titulo):
        self.ensure_one()
        existente = self.documento_ids.filtered(
            lambda documento: documento.clase_documento == clase_documento
        )
        if existente:
            raise UserError(_('Ya existe el documento %s. Elimínelo antes de reemplazarlo.') % titulo)
        return {
            'type': 'ir.actions.act_window',
            'name': titulo,
            'res_model': 'ordenes_especiales.documento.carga',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_orden_id': self.id,
                'default_clase_documento': clase_documento,
            },
        }

    def action_cargar_causacion(self):
        return self._action_cargar_documentos_multiples(
            'acusacion', _('Causación'))

    def action_cargar_documento_cruce(self):
        return self._action_cargar_documentos_multiples(
            'documento_cruce', _('Documento Cruce'))

    def _action_cargar_documentos_multiples(self, clase_documento, titulo):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': titulo,
            'res_model': 'ordenes_especiales.documento.carga.multiple',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_orden_id': self.id,
                'default_clase_documento': clase_documento,
            },
        }

    def action_cargar_facturas(self):
        return self._action_cargar_documentos_multiples(
            'factura', _('Facturas'))

    def action_cargar_otros_documentos(self):
        return self._action_cargar_documentos_multiples(
            'otro', _('Otros documentos'))

    def action_cargar_certificacion_conjunto(self):
        return self._action_cargar_documento_unico(
            'certificacion_conjunto', _('Certificación conjunta'))

    def action_cargar_orden_pago(self):
        return self._action_cargar_documento_unico(
            'orden_pago', _('Orden de pago'))

    def action_ver_facturas(self):
        return self._action_ver_documentos('factura', _('Facturas'))

    def action_ver_otros_documentos(self):
        return self._action_ver_documentos('otro', _('Otros documentos'))

    def action_ver_certificacion_conjunto(self):
        return self._action_ver_documento_unico(
            'certificacion_conjunto', _('Certificación conjunta'))

    def action_habilitar_carga_certificacion_conjunto(self):
        self.ensure_one()
        documento = self.certificacion_conjunto_documento_ids
        if len(documento) != 1:
            raise UserError(_(
                'Debe existir una sola Certificación conjunta para liberar su carga. '
                'Contacte a Soporte si hay registros duplicados.'
            ))
        return documento.action_habilitar_nueva_carga()

    def action_ver_orden_pago(self):
        return self._action_ver_documento_unico(
            'orden_pago', _('Orden de pago'))

    def action_habilitar_carga_orden_pago(self):
        self.ensure_one()
        documento = self.orden_pago_documento_ids
        if len(documento) != 1:
            raise UserError(_(
                'Debe existir una sola Orden de pago para liberar su carga. '
                'Contacte a Soporte si hay registros duplicados.'
            ))
        return documento.action_habilitar_nueva_carga()

    def action_cargar_comprobante_pago(self):
        return self._action_cargar_documentos_multiples(
            'comprobante_pago', _('Comprobantes de pago'))

    def action_cargar_comprobante_bancario(self):
        return self._action_cargar_documentos_multiples(
            'comprobante_bancario', _('Comprobantes bancarios'))

    def _action_ver_documento_unico(self, clase_documento, titulo):
        self.ensure_one()
        documento = self.documento_ids.filtered(
            lambda item: item.clase_documento == clase_documento
        )[:1]
        if not documento:
            raise UserError(_('Todavía no se ha cargado %s.') % titulo)
        return documento.action_ver_documento()

    def _action_ver_documentos(self, clase_documento, titulo):
        self.ensure_one()
        documentos = self.documento_ids.filtered(
            lambda item: item.clase_documento == clase_documento
        )
        if not documentos:
            raise UserError(_('Todavía no se han cargado documentos.'))
        vista = self.env.ref(
            'Matriz.view_ordenes_especiales_documento_consulta_tree',
            raise_if_not_found=False,
        )
        vista_busqueda = self.env.ref(
            'Matriz.view_ordenes_especiales_documento_consulta_search',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': titulo,
            'res_model': 'ordenes_especiales.documento',
            'view_mode': 'tree',
            'views': [(vista.id, 'tree')] if vista else [(False, 'tree')],
            'search_view_id': vista_busqueda.id if vista_busqueda else False,
            'target': 'new',
            'domain': [('id', 'in', documentos.ids)],
            'context': {'create': False, 'edit': False, 'delete': False},
        }

    def action_ver_causacion(self):
        return self._action_ver_documentos('acusacion', _('Causación'))

    def action_ver_documento_cruce(self):
        return self._action_ver_documentos(
            'documento_cruce', _('Documento Cruce'))

    def action_ver_comprobante_pago(self):
        return self._action_ver_documentos(
            'comprobante_pago', _('Comprobantes de pago'))

    def action_ver_comprobante_bancario(self):
        return self._action_ver_documentos(
            'comprobante_bancario', _('Comprobantes bancarios'))

    def action_abrir_guia_usuario(self):
        self.ensure_one()
        vista = self.env.ref('Matriz.view_ordenes_especiales_guia_usuario_form')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Guía de usuario · Orden especial'),
            'res_model': 'ordenes_especiales.guia_usuario',
            'view_mode': 'form',
            'views': [(vista.id, 'form')],
            'target': 'new',
        }

    def action_soporte_crear_carpeta_drive(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede crear o reparar la carpeta de esta orden.'))

        carpeta_anterior = self.drive_file_id.sudo()
        self._asegurar_carpetas_documentos()
        carpeta = self.drive_file_id.sudo()
        if not carpeta or not carpeta.folder_id2 or self._carpeta_compartida_con_otra_orden(carpeta):
            raise UserError(_('No fue posible vincular una carpeta exclusiva a esta orden.'))

        creada = not carpeta_anterior or carpeta_anterior.id != carpeta.id
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Carpeta de orden especial'),
                'message': (
                    _('Carpeta creada y vinculada: %s')
                    if creada else _('La carpeta ya estaba vinculada: %s')
                ) % carpeta.folder_name,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_soporte_cambiar_estado(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede cambiar manualmente el estado.'))

        vista = self.env.ref(
            'Matriz.view_ordenes_especiales_soporte_estado_wizard_form',
            raise_if_not_found=False,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Soporte · Cambiar estado'),
            'res_model': 'ordenes_especiales.soporte.estado.wizard',
            'view_mode': 'form',
            'views': [(vista.id, 'form')] if vista else [(False, 'form')],
            'target': 'new',
            'context': {
                'default_orden_id': self.id,
                'default_estado_nuevo': self.estado_ordenes_especiales,
            },
        }

    def _validar_usuario_destino(self, usuario, grupo_xml_id, nombre_destino):
        self.ensure_one()
        if not usuario:
            raise UserError(_(
                'Debe seleccionar el usuario de %s antes de aprobar.'
            ) % nombre_destino)
        grupo = self.env.ref(grupo_xml_id, raise_if_not_found=False)
        if not grupo or grupo not in usuario.groups_id:
            raise UserError(_(
                'El usuario seleccionado no tiene el rol %s.'
            ) % nombre_destino)
        if not usuario.active or usuario.share:
            raise UserError(_('El usuario seleccionado debe ser un usuario interno activo.'))
        if not usuario.email:
            raise UserError(_(
                'El usuario %s no tiene correo electrónico configurado.'
            ) % usuario.name)

    def _compartir_carpeta_drive(self, usuario):
        """Comparte la carpeta raíz como lector; Odoo conserva la gestión documental."""
        self.ensure_one()
        if not usuario or not usuario.active or not usuario.email:
            raise UserError(_(
                'El usuario asignado debe estar activo y tener correo electrónico.'
            ))
        self._asegurar_carpetas_documentos()
        if not self.drive_file_id or not self.drive_file_id.folder_id2:
            raise UserError(_('No fue posible preparar la carpeta de la orden en Drive.'))
        try:
            drive = GoogleDriveAuth.get_instance().get_drive()
            carpeta = drive.CreateFile({'id': self.drive_file_id.folder_id2})
            carpeta.FetchMetadata(fields='id,title,permissions')
            correo = usuario.email.strip().lower()
            permisos = carpeta.GetPermissions()
            ya_compartida = any(
                permiso.get('emailAddress', '').strip().lower() == correo
                for permiso in permisos
            )
            if not ya_compartida:
                carpeta.InsertPermission({
                    'type': 'user',
                    'value': usuario.email,
                    'role': 'reader',
                })
        except Exception as error:
            raise UserError(_(
                'No se pudo compartir la carpeta de Drive con %s: %s'
            ) % (usuario.name, error))

    def _url_formulario(self):
        self.ensure_one()
        action = self.env.ref('Matriz.action_ordenes_especiales', raise_if_not_found=False)
        parametros = [
            'id=%s' % self.id,
            'model=ordenes_especiales',
            'view_type=form',
        ]
        if action:
            parametros.append('action=%s' % action.id)
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return '%s/web#%s' % (base_url, '&'.join(parametros))

    def _notificar_usuario(
        self, destinatario, nombre_destino, es_devolucion=False,
        observacion_override=None, estado_origen=None,
    ):
        self.ensure_one()
        if not destinatario or not destinatario.active or not destinatario.email:
            raise UserError(_('No hay un usuario asignado con correo para %s.') % nombre_destino)

        observacion = (
            observacion_override
            if observacion_override is not None
            else (self.observacion_general or '').strip()
        )
        if es_devolucion and not observacion:
            raise UserError(_('Debe escribir una observación antes de devolver la orden.'))

        proveedor = self.proveedor or _('Orden especial')
        proyecto = self.nombre_proyecto_relacionado or self.proyecto_relacionado_s.display_name or ''
        titulo = _('Devolución de orden especial') if es_devolucion else _('Aprobación de orden especial')
        color = '#f28c28' if es_devolucion else '#043484'
        accion = _('devolvió') if es_devolucion else _('aprobó')
        remitente = self.env.user.email_formatted or self.env.company.email_formatted
        url = self._url_formulario()
        total_produccion = formatLang(
            self.env,
            self.total or 0.0,
            currency_obj=self.env.company.currency_id,
        )

        cuerpo = """
                <div style="font-family:Arial,sans-serif;font-size:14px;color:#444;background:#f8f8f8;padding:20px;border-radius:5px;">
                    <div style="background:%(color)s;color:white;padding:12px 15px;border-radius:4px 4px 0 0;text-align:center;">
                        <h3 style="margin:0;font-size:18px;">%(titulo)s</h3>
                    </div>
                    <div style="background:white;padding:20px 15px;border:1px solid #ddd;border-top:none;border-radius:0 0 4px 4px;">
                        <p>Hola, <strong>%(destinatario)s</strong>:</p>
                        <p><strong>%(usuario)s</strong> %(accion)s la orden especial y la envió a <strong>%(destino)s</strong>.</p>
                        <div style="margin:15px 0;padding:10px;background:#f9f9f9;border-left:4px solid %(color)s;">
                            <strong>Proveedor:</strong> %(proveedor)s<br/>
                            <strong>NIT:</strong> %(nit)s<br/>
                            <strong>Proyecto:</strong> %(proyecto)s<br/>
                            <strong>Total Producción:</strong> %(total_produccion)s<br/>
                            %(origen)s
                            <strong>Estado destino:</strong> %(destino)s
                        </div>
                        %(observacion)s
                        <p style="text-align:center;margin-top:30px;">
                            <a href="%(url)s" style="background:%(color)s;color:white;padding:10px 20px;text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;">Ver orden especial</a>
                        </p>
                        <p style="margin-top:20px;font-size:12px;color:#999;">SGO - mensaje de notificación</p>
                    </div>
                </div>
        """ % {
                'color': color,
                'titulo': html_escape(titulo),
                'destinatario': html_escape(destinatario.name),
                'usuario': html_escape(self.env.user.name),
                'accion': html_escape(accion),
                'destino': html_escape(nombre_destino),
                'proveedor': html_escape(proveedor),
                'nit': html_escape(self.nit or ''),
                'proyecto': html_escape(proyecto),
                'total_produccion': html_escape(total_produccion),
                'origen': (
                    '<strong>Etapa que devuelve:</strong> %s<br/>'
                    % html_escape(self._nombre_estado_orden_especial(estado_origen))
                    if es_devolucion and estado_origen else ''
                ),
                'url': html_escape(url),
                'observacion': (
                    '<div style="margin-top:20px;padding:10px;border:1px solid #f28c28;background:#fff3e0;border-radius:4px;">'
                    '<strong>Observaciones:</strong><br/><em>%s</em></div>' % html_escape(observacion)
                    if observacion else ''
                ),
        }
        try:
            self.env['mail.mail'].sudo().create({
                'subject': '%s - %s' % (titulo, proveedor),
                'email_from': remitente,
                'email_to': destinatario.email,
                'body_html': cuerpo,
                'auto_delete': True,
            }).send()
        except Exception as error:
            raise UserError(_(
                'No se pudo enviar el correo a %s: %s'
            ) % (destinatario.name, error))

        cuerpo_traza = (
            '<p><strong>%s</strong></p>'
            '<p><strong>%s</strong> %s la orden y la envió a <strong>%s</strong>.</p>'
            '<p><strong>Destinatario:</strong> %s</p>'
        ) % (
            html_escape(titulo), html_escape(self.env.user.name), html_escape(accion),
            html_escape(nombre_destino), html_escape(destinatario.name),
        )
        if observacion:
            cuerpo_traza += '<p><strong>Observaciones:</strong> %s</p>' % html_escape(observacion)
        if es_devolucion and estado_origen:
            cuerpo_traza += '<p><strong>Etapa que devuelve:</strong> %s</p>' % html_escape(
                self._nombre_estado_orden_especial(estado_origen)
            )
        try:
            self.message_post(body=cuerpo_traza, subtype_xmlid='mail.mt_note')
        except Exception as error:
            _logger.warning(
                'No se pudo registrar la trazabilidad de la orden especial %s: %s',
                self.id, error,
            )

    def _aprobar_y_notificar(
        self, estado_destino, grupo_destino, nombre_destino, usuario_destino,
        valores_extra=None,
    ):
        self.ensure_one()
        if self.estado_retorno_devolucion:
            raise UserError(_(
                'Esta orden fue devuelta para ajustes. Debe retornarla al área '
                'que la devolvió antes de continuar el flujo.'
            ))
        # Todas las reglas funcionales se validan antes de modificar el estado.
        self._validar_usuario_destino(usuario_destino, grupo_destino, nombre_destino)

        valores = {
            'estado_ordenes_especiales': estado_destino,
            'observacion_general': False,
        }
        valores.update(valores_extra or {})
        self.write(valores)

        # Drive/correo son parte de la transición: si hay una falla real de
        # conectividad o configuración, la transacción se revierte.
        self._compartir_carpeta_drive(usuario_destino)
        self._notificar_usuario(usuario_destino, nombre_destino)

    def _notificar_finalizacion_coordinacion(self, descripcion):
        self.ensure_one()
        destinatario = self.usuario_coordinacion_id or self.create_uid
        if not destinatario or not destinatario.active:
            raise UserError(_(
                'No hay un usuario de Coordinación activo para notificar la finalización.'
            ))
        if not destinatario.email:
            raise UserError(_(
                'El usuario de Coordinación %s no tiene correo electrónico configurado.'
            ) % destinatario.name)

        proveedor = self.proveedor or _('Orden especial')
        proyecto = (
            self.nombre_proyecto_relacionado
            or self.proyecto_relacionado_s.display_name
            or ''
        )
        remitente = self.env.user.email_formatted or self.env.company.email_formatted
        url = self._url_formulario()
        titulo = _('Orden especial finalizada')
        total_produccion = formatLang(
            self.env,
            self.total or 0.0,
            currency_obj=self.env.company.currency_id,
        )

        cuerpo = """
            <div style="font-family:Arial,sans-serif;font-size:14px;color:#444;background:#f8f8f8;padding:20px;border-radius:5px;">
                <div style="background:#198754;color:white;padding:12px 15px;border-radius:4px 4px 0 0;text-align:center;">
                    <h3 style="margin:0;font-size:18px;">%(titulo)s</h3>
                </div>
                <div style="background:white;padding:20px 15px;border:1px solid #ddd;border-top:none;border-radius:0 0 4px 4px;">
                    <p>Hola, <strong>%(destinatario)s</strong>:</p>
                    <p>La orden especial fue finalizada por <strong>%(usuario)s</strong>.</p>
                    <div style="margin:15px 0;padding:10px;background:#f9f9f9;border-left:4px solid #198754;">
                        <strong>Proveedor:</strong> %(proveedor)s<br/>
                        <strong>NIT:</strong> %(nit)s<br/>
                        <strong>Proyecto:</strong> %(proyecto)s<br/>
                        <strong>Total Producción:</strong> %(total_produccion)s<br/>
                        <strong>Finalización:</strong> %(descripcion)s<br/>
                        <strong>Estado:</strong> Finalizado
                    </div>
                    <p style="text-align:center;margin-top:30px;">
                        <a href="%(url)s" style="background:#198754;color:white;padding:10px 20px;text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;">Ver orden especial</a>
                    </p>
                    <p style="margin-top:20px;font-size:12px;color:#999;">SGO - mensaje de notificación</p>
                </div>
            </div>
        """ % {
            'titulo': html_escape(titulo),
            'destinatario': html_escape(destinatario.name),
            'usuario': html_escape(self.env.user.name),
            'proveedor': html_escape(proveedor),
            'nit': html_escape(self.nit or ''),
            'proyecto': html_escape(proyecto),
            'total_produccion': html_escape(total_produccion),
            'descripcion': html_escape(descripcion),
            'url': html_escape(url),
        }

        try:
            self.env['mail.mail'].sudo().create({
                'subject': '%s - %s' % (titulo, proveedor),
                'email_from': remitente,
                'email_to': destinatario.email,
                'body_html': cuerpo,
                'auto_delete': True,
            }).send()
        except Exception as error:
            raise UserError(_(
                'No se pudo enviar la notificación de finalización a %s: %s'
            ) % (destinatario.name, error))

    def _registrar_finalizacion(self, descripcion):
        self.ensure_one()
        if self.estado_retorno_devolucion:
            raise UserError(_(
                'Debe retornar los ajustes al área que devolvió la orden '
                'antes de finalizarla.'
            ))

        # Validar previamente que exista un destinatario válido para el cierre.
        destinatario = self.usuario_coordinacion_id or self.create_uid
        if not destinatario or not destinatario.active:
            raise UserError(_(
                'No hay un usuario de Coordinación activo para notificar la finalización.'
            ))
        if not destinatario.email:
            raise UserError(_(
                'El usuario de Coordinación %s no tiene correo electrónico configurado.'
            ) % destinatario.name)

        self.write({
            'estado_ordenes_especiales': 'finalizado',
            'observacion_general': False,
        })

        self._notificar_finalizacion_coordinacion(descripcion)

        try:
            self.message_post(
                body='<p><strong>%s</strong> %s</p>' % (
                    html_escape(self.env.user.name), html_escape(descripcion)),
                subtype_xmlid='mail.mt_note',
            )
        except Exception as error:
            _logger.warning(
                'No se pudo registrar la finalización de la orden especial %s en chatter: %s',
                self.id, error,
            )

    def action_enviar_ventanilla(self):
        puede_enviar = (
            self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
        )
        if not puede_enviar:
            raise AccessError(_(
                'Solo el Coordinador o el Asistente de Coordinación pueden enviar la orden a Ventanilla.'
            ))
        for record in self:
            if record.estado_ordenes_especiales != 'coordinador':
                raise UserError(_('La orden debe estar en Coordinador para enviarla a Ventanilla.'))
            if not record.solicitante_usuario_id:
                raise UserError(_(
                    'Debe seleccionar el solicitante desde los usuarios '
                    'activos del sistema.'
                ))
            record._validar_documentos('factura')
            if (
                record.certificacion_conjunto_documento_ids
                and not record.certificacion_conjunto_aprobada_coordinador
            ):
                raise UserError(_(
                    'Coordinación debe aprobar la Certificación conjunta '
                    'antes de enviar la orden a Ventanilla.'
                ))
            record._aprobar_y_notificar(
                'ventanilla', 'Matriz.group_contratacion_ventanilla_unica',
                _('Ventanilla'), record.usuario_ventanilla_id,
                valores_extra={'usuario_coordinacion_id': self.env.user.id},
            )
        return True

    def action_enviar_contabilidad(self):
        if not self.env.user.has_group('Matriz.group_contratacion_ventanilla_unica'):
            raise AccessError(_('Solo Ventanilla puede enviar la orden a Contabilidad.'))
        for record in self:
            if record.estado_ordenes_especiales != 'ventanilla':
                raise UserError(_('La orden debe estar en Ventanilla para enviarla a Contabilidad.'))
            if (
                record.certificacion_conjunto_documento_ids
                and not record.certificacion_conjunto_aprobada_ventanilla
            ):
                raise UserError(_(
                    'Ventanilla debe aprobar la Certificación conjunta '
                    'antes de enviar la orden a Contabilidad.'
                ))
            record._aprobar_y_notificar(
                'contabilidad', 'Matriz.group_contabilidad', _('Contabilidad'),
                record.usuario_contabilidad_id,
            )
        return True

    def action_enviar_auxiliar_tesoreria(self):
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise AccessError(_('Solo Contabilidad puede enviar la orden al Auxiliar de Tesorería.'))
        for record in self:
            if record.estado_ordenes_especiales != 'contabilidad':
                raise UserError(_('La orden debe estar en Contabilidad.'))
            if record.es_cruce:
                raise UserError(_('Las órdenes marcadas como cruce deben finalizarse desde Contabilidad.'))
            record._validar_documentos('acusacion')
            record._aprobar_y_notificar(
                'auxiliar_tesoreria',
                'Matriz.group_tesoreria_auxiliar',
                _('Auxiliar de Tesorería'),
                record.usuario_auxiliar_tesoreria_id,
            )
        return True


    fecha_tentativa = fields.Date(string='Fecha tentativa de pago')

    def action_enviar_tesoreria(self):
        if not self.env.user.has_group('Matriz.group_tesoreria_auxiliar'):
            raise AccessError(_('Solo el Auxiliar de Tesorería puede enviar la orden a Tesorería.'))
        for record in self:
            if record.estado_ordenes_especiales != 'auxiliar_tesoreria':
                raise UserError(_('La orden debe estar en Auxiliar de Tesorería.'))
            if not record.fecha_tentativa:
                raise UserError(_('Debe registrar la fecha tentativa de pago.'))
            # record._validar_documentos('orden_pago')
            record._aprobar_y_notificar(
                'tesoreria', 'Matriz.group_tesoreria', _('Tesorería'),
                record.usuario_tesoreria_id,
            )
        return True

    def action_finalizar_cruce(self):
        if not self.env.user.has_group('Matriz.group_contabilidad'):
            raise AccessError(_('Solo Contabilidad puede finalizar un cruce.'))
        for record in self:
            if record.estado_ordenes_especiales != 'contabilidad' or not record.es_cruce:
                raise UserError(_('La orden debe estar marcada como cruce en Contabilidad.'))
            if not record.consecutivo_contabilidad:
                raise UserError(_('Debe registrar el consecutivo de Contabilidad.'))
            record._validar_documentos('acusacion', 'documento_cruce')
            record._registrar_finalizacion(_('finalizó la orden especial como cruce.'))
        return True

    def _grupo_estado_orden_especial(self, estado):
        return {
            EstadoOrdenEspecial.VENTANILLA.value:
                'Matriz.group_contratacion_ventanilla_unica',
            EstadoOrdenEspecial.CONTABILIDAD.value:
                'Matriz.group_contabilidad',
            EstadoOrdenEspecial.AUXILIAR_TESORERIA.value:
                'Matriz.group_tesoreria_auxiliar',
            EstadoOrdenEspecial.TESORERIA.value:
                'Matriz.group_tesoreria',
        }.get(estado)

    def _nombre_estado_orden_especial(self, estado):
        return dict(ESTADOS_ORDEN_ESPECIAL).get(estado, estado or '')

    def _asistentes_coordinacion_proyecto(self):
        self.ensure_one()
        grupo = self.env.ref(
            'Matriz.group_contratacion_asis_coordinador',
            raise_if_not_found=False,
        )
        if not grupo or not self.proyecto_relacionado_s:
            return self.env['res.users']

        return self.proyecto_relacionado_s.users_with_access.filtered(
            lambda user: (
                user.active
                and not user.share
                and bool(user.email)
                and grupo in user.groups_id
            )
        )

    def _notificar_devolucion_coordinacion(self, coordinador, observacion):
        self.ensure_one()

        destinatarios = self.env['res.users']
        if coordinador:
            destinatarios |= coordinador
        destinatarios |= self._asistentes_coordinacion_proyecto()

        destinatarios = destinatarios.filtered(
            lambda user: user.active and bool(user.email)
        )
        if not destinatarios:
            raise UserError(_(
                'No hay usuarios activos de Coordinación o Asistencia con correo '
                'asociados al proyecto para recibir la devolución.'
            ))

        # El correo se envía individualmente para conservar saludo y trazabilidad
        # claros por destinatario.
        for destinatario in destinatarios:
            self._compartir_carpeta_drive(destinatario)
            self._notificar_usuario(
                destinatario,
                _('Coordinación'),
                es_devolucion=True,
                observacion_override=observacion,
            )

    def _devolver_a_coordinacion(self, estado_actual, grupo_actual, nombre_rol):
        if not self.env.user.has_group(grupo_actual):
            raise AccessError(_('Solo %s puede devolver la orden.') % nombre_rol)

        for record in self:
            if record.estado_ordenes_especiales != estado_actual:
                raise UserError(
                    _('La orden no se encuentra en la etapa %s.') % nombre_rol
                )
            if record.estado_retorno_devolucion:
                raise UserError(_(
                    'Primero debe retornar los ajustes al área que devolvió la orden.'
                ))

            observacion = (record.observacion_general or '').strip()
            if not observacion:
                raise UserError(
                    _('Debe escribir una observación antes de devolver la orden.')
                )

            destinatario = record.usuario_coordinacion_id or record.create_uid
            if not destinatario or not destinatario.active:
                raise UserError(_(
                    'No hay un usuario activo de Coordinación para recibir la devolución.'
                ))
            if not destinatario.email:
                raise UserError(_(
                    'El usuario de Coordinación %s no tiene correo electrónico configurado.'
                ) % destinatario.name)

            record.write({
                'ultimo_estado_devolucion': estado_actual,
                'ultimo_usuario_devolucion_id': self.env.user.id,
                'estado_retorno_devolucion': estado_actual,
                'estado_ordenes_especiales': EstadoOrdenEspecial.COORDINADOR.value,
                'observacion_general': False,
            })

            record._notificar_devolucion_coordinacion(
                destinatario,
                observacion,
            )

            try:
                record.message_post(
                    body=(
                        '<p><strong>Devolución a Coordinación</strong></p>'
                        '<p><strong>%s</strong> devolvió la orden desde '
                        '<strong>%s</strong>. Cuando Coordinación termine los ajustes, '
                        'la orden deberá retornar a ese mismo estado.</p>'
                    ) % (
                        html_escape(self.env.user.name),
                        html_escape(record._nombre_estado_orden_especial(estado_actual)),
                    ),
                    subtype_xmlid='mail.mt_note',
                )
            except Exception as error:
                _logger.warning(
                    'No se pudo registrar la devolución avanzada de la orden %s: %s',
                    record.id, error,
                )
        return True

    def _devolver_a_etapa_anterior(
        self, estado_actual, grupo_actual, estado_anterior,
        campo_usuario_anterior,
    ):
        """Devuelve al responsable asignado de la etapa inmediatamente anterior."""
        if not self.env.user.has_group(grupo_actual):
            raise AccessError(_('No tiene permiso para devolver la orden en esta etapa.'))

        for record in self:
            if record.estado_ordenes_especiales != estado_actual:
                raise UserError(_('La orden ya no está en la etapa correspondiente.'))
            if record.estado_retorno_devolucion:
                raise UserError(_(
                    'Primero debe retornar los ajustes al área que devolvió la orden.'
                ))
            observacion = (record.observacion_general or '').strip()
            if not observacion:
                raise UserError(_(
                    'Debe escribir una observación antes de devolver la orden.'
                ))
            nombre_anterior = record._nombre_estado_orden_especial(estado_anterior)
            destinatario = record[campo_usuario_anterior]
            record._validar_usuario_destino(
                destinatario,
                record._grupo_estado_orden_especial(estado_anterior),
                nombre_anterior,
            )
            record.write({
                'ultimo_estado_devolucion': estado_actual,
                'ultimo_usuario_devolucion_id': self.env.user.id,
                'estado_retorno_devolucion': estado_actual,
                'estado_ordenes_especiales': estado_anterior,
                'observacion_general': False,
            })
            record._compartir_carpeta_drive(destinatario)
            record._notificar_usuario(
                destinatario,
                nombre_anterior,
                es_devolucion=True,
                observacion_override=observacion,
                estado_origen=estado_actual,
            )
        return True

    def _notificar_retorno_ajustes(self, destinatario, estado_destino, estado_origen):
        self.ensure_one()
        if not destinatario or not destinatario.active or not destinatario.email:
            raise UserError(_(
                'El usuario que realizó la devolución ya no está activo o no tiene correo.'
            ))

        proveedor = self.proveedor or _('Orden especial')
        proyecto = (
            self.nombre_proyecto_relacionado
            or self.proyecto_relacionado_s.display_name
            or ''
        )
        total_produccion = formatLang(
            self.env,
            self.total or 0.0,
            currency_obj=self.env.company.currency_id,
        )
        nombre_estado = self._nombre_estado_orden_especial(estado_destino)
        nombre_origen = self._nombre_estado_orden_especial(estado_origen)
        remitente = self.env.user.email_formatted or self.env.company.email_formatted
        url = self._url_formulario()

        cuerpo = """
            <div style="font-family:Arial,sans-serif;font-size:14px;color:#444;background:#f8f8f8;padding:20px;border-radius:5px;">
                <div style="background:#043484;color:white;padding:12px 15px;border-radius:4px 4px 0 0;text-align:center;">
                    <h3 style="margin:0;font-size:18px;">Ajustes de orden especial</h3>
                </div>
                <div style="background:white;padding:20px 15px;border:1px solid #ddd;border-top:none;border-radius:0 0 4px 4px;">
                    <p>Hola, <strong>%(destinatario)s</strong>:</p>
                    <p>%(origen)s · <strong>%(usuario)s</strong> realizó los ajustes solicitados y retornó la orden especial a <strong>%(estado)s</strong>.</p>
                    <div style="margin:15px 0;padding:10px;background:#f9f9f9;border-left:4px solid #043484;">
                        <strong>Proveedor:</strong> %(proveedor)s<br/>
                        <strong>NIT:</strong> %(nit)s<br/>
                        <strong>Proyecto:</strong> %(proyecto)s<br/>
                        <strong>Total Producción:</strong> %(total_produccion)s<br/>
                        <strong>Estado:</strong> %(estado)s
                    </div>
                    <p style="text-align:center;margin-top:30px;">
                        <a href="%(url)s" style="background:#043484;color:white;padding:10px 20px;text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;">Continuar revisión</a>
                    </p>
                    <p style="margin-top:20px;font-size:12px;color:#999;">SGO - mensaje de notificación</p>
                </div>
            </div>
        """ % {
            'destinatario': html_escape(destinatario.name),
            'usuario': html_escape(self.env.user.name),
            'origen': html_escape(nombre_origen),
            'estado': html_escape(nombre_estado),
            'proveedor': html_escape(proveedor),
            'nit': html_escape(self.nit or ''),
            'proyecto': html_escape(proyecto),
            'total_produccion': html_escape(total_produccion),
            'url': html_escape(url),
        }

        try:
            self.env['mail.mail'].sudo().create({
                'subject': '%s - %s' % (_('Ajustes de orden especial'), proveedor),
                'email_from': remitente,
                'email_to': destinatario.email,
                'body_html': cuerpo,
                'auto_delete': True,
            }).send()
        except Exception as error:
            raise UserError(_(
                'No se pudo enviar el retorno de ajustes a %s: %s'
            ) % (destinatario.name, error))

    def action_retornar_ajustes(self):
        """Reenvía los ajustes al usuario que devolvió la orden."""
        etapas_retorno = {
            EstadoOrdenEspecial.VENTANILLA.value: (
                EstadoOrdenEspecial.CONTABILIDAD.value,
                'Matriz.group_contratacion_ventanilla_unica',
            ),
            EstadoOrdenEspecial.CONTABILIDAD.value: (
                EstadoOrdenEspecial.AUXILIAR_TESORERIA.value,
                'Matriz.group_contabilidad',
            ),
            EstadoOrdenEspecial.AUXILIAR_TESORERIA.value: (
                EstadoOrdenEspecial.TESORERIA.value,
                'Matriz.group_tesoreria_auxiliar',
            ),
        }
        for record in self:
            estado_origen = record.estado_ordenes_especiales
            if estado_origen == EstadoOrdenEspecial.COORDINADOR.value:
                puede_retornar = (
                    self.env.user.has_group('Matriz.group_contratacion_coordinador')
                    or self.env.user.has_group('Matriz.group_contratacion_asis_coordinador')
                )
            else:
                etapa = etapas_retorno.get(estado_origen)
                puede_retornar = bool(
                    etapa and self.env.user.has_group(etapa[1])
                )
            if not puede_retornar:
                raise AccessError(_(
                    'No tiene permiso para retornar los ajustes en esta etapa.'
                ))

            if estado_origen == EstadoOrdenEspecial.COORDINADOR.value and (
                record.certificacion_conjunto_documento_ids
                and not record.certificacion_conjunto_aprobada_coordinador
            ):
                raise UserError(_(
                    'Coordinación debe aprobar la Certificación conjunta '
                    'antes de retornar los ajustes.'
                ))

            estado_destino = record.estado_retorno_devolucion
            if not estado_destino:
                raise UserError(_('No hay un estado de retorno pendiente.'))
            if estado_origen == EstadoOrdenEspecial.COORDINADOR.value:
                if estado_destino == estado_origen:
                    raise UserError(_('El estado de retorno no puede ser Coordinación.'))
            elif estado_destino != etapas_retorno[estado_origen][0]:
                raise UserError(_(
                    'El retorno pendiente no corresponde a la siguiente etapa del flujo.'
                ))
            if record.ultimo_estado_devolucion != estado_destino:
                raise UserError(_(
                    'Los datos de la devolución no corresponden al área de retorno.'
                ))

            if estado_origen == EstadoOrdenEspecial.VENTANILLA.value and (
                record.certificacion_conjunto_documento_ids
                and not record.certificacion_conjunto_aprobada_ventanilla
            ):
                raise UserError(_(
                    'Ventanilla debe aprobar la Certificación conjunta '
                    'antes de retornar los ajustes.'
                ))
            if estado_origen == EstadoOrdenEspecial.CONTABILIDAD.value:
                if record.es_cruce:
                    raise UserError(_(
                        'Una orden devuelta por Auxiliar de Tesorería no puede '
                        'retornar marcada como cruce.'
                    ))
                record._validar_documentos('acusacion')
            if estado_origen == EstadoOrdenEspecial.AUXILIAR_TESORERIA.value:
                if not record.fecha_tentativa:
                    raise UserError(_('Debe registrar la fecha tentativa de pago.'))

            destinatario = record.ultimo_usuario_devolucion_id
            if not destinatario or not destinatario.active:
                raise UserError(_(
                    'El usuario que realizó la devolución ya no está activo.'
                ))
            if not destinatario.email:
                raise UserError(_(
                    'El usuario %s no tiene correo electrónico configurado.'
                ) % destinatario.name)

            grupo_xml_id = record._grupo_estado_orden_especial(estado_destino)
            grupo = self.env.ref(grupo_xml_id, raise_if_not_found=False) if grupo_xml_id else False
            if not grupo or grupo not in destinatario.groups_id:
                raise UserError(_(
                    'El usuario %s ya no tiene el rol requerido para retornar la orden a %s.'
                ) % (
                    destinatario.name,
                    record._nombre_estado_orden_especial(estado_destino),
                ))

            nombre_destino = record._nombre_estado_orden_especial(estado_destino)
            nombre_origen = record._nombre_estado_orden_especial(estado_origen)
            record.write({
                'estado_ordenes_especiales': estado_destino,
                'ultimo_estado_devolucion': False,
                'ultimo_usuario_devolucion_id': False,
                'estado_retorno_devolucion': False,
                'observacion_general': False,
            })
            record._compartir_carpeta_drive(destinatario)
            record._notificar_retorno_ajustes(
                destinatario, estado_destino, estado_origen,
            )

            try:
                record.message_post(
                    body=(
                        '<p><strong>Retorno de ajustes</strong></p>'
                        '<p>%s · <strong>%s</strong> retornó la orden corregida '
                        'a <strong>%s</strong>, usuario <strong>%s</strong>.</p>'
                    ) % (
                        html_escape(nombre_origen),
                        html_escape(self.env.user.name),
                        html_escape(nombre_destino),
                        html_escape(destinatario.name),
                    ),
                    subtype_xmlid='mail.mt_note',
                )
            except Exception as error:
                _logger.warning(
                    'No se pudo registrar el retorno de ajustes de la orden %s: %s',
                    record.id, error,
                )
        return True

    def action_coordinador_retornar_ajustes(self):
        """Conserva el botón existente de Coordinación."""
        return self.action_retornar_ajustes()

    def action_devolver_contabilidad_a_ventanilla(self):
        return self._devolver_a_etapa_anterior(
            EstadoOrdenEspecial.CONTABILIDAD.value,
            'Matriz.group_contabilidad',
            EstadoOrdenEspecial.VENTANILLA.value,
            'usuario_ventanilla_id',
        )

    def action_devolver_auxiliar_a_contabilidad(self):
        return self._devolver_a_etapa_anterior(
            EstadoOrdenEspecial.AUXILIAR_TESORERIA.value,
            'Matriz.group_tesoreria_auxiliar',
            EstadoOrdenEspecial.CONTABILIDAD.value,
            'usuario_contabilidad_id',
        )

    def action_devolver_tesoreria_a_auxiliar(self):
        return self._devolver_a_etapa_anterior(
            EstadoOrdenEspecial.TESORERIA.value,
            'Matriz.group_tesoreria',
            EstadoOrdenEspecial.AUXILIAR_TESORERIA.value,
            'usuario_auxiliar_tesoreria_id',
        )

    def action_devolver_ventanilla(self):
        return self._devolver_a_coordinacion(
            EstadoOrdenEspecial.VENTANILLA.value,
            'Matriz.group_contratacion_ventanilla_unica',
            _('Ventanilla'),
        )

    def action_devolver_contabilidad(self):
        return self._devolver_a_coordinacion(
            EstadoOrdenEspecial.CONTABILIDAD.value,
            'Matriz.group_contabilidad',
            _('Contabilidad'),
        )

    def action_devolver_auxiliar_tesoreria(self):
        return self._devolver_a_coordinacion(
            EstadoOrdenEspecial.AUXILIAR_TESORERIA.value,
            'Matriz.group_tesoreria_auxiliar',
            _('Auxiliar de Tesorería'),
        )

    def action_devolver_tesoreria(self):
        return self._devolver_a_coordinacion(
            EstadoOrdenEspecial.TESORERIA.value,
            'Matriz.group_tesoreria',
            _('Tesorería'),
        )

    def action_finalizar_pago(self):
        if not self.env.user.has_group('Matriz.group_tesoreria'):
            raise AccessError(_('Solo Tesorería puede finalizar el pago.'))
        for record in self:
            if record.estado_ordenes_especiales != 'tesoreria':
                raise UserError(_('La orden debe estar en Tesorería para finalizar el pago.'))
            if not record.consecutivo_tesoreria:
                raise UserError(_('Debe registrar el consecutivo de Tesorería.'))
            record._validar_documentos('comprobante_pago', 'comprobante_bancario')
            record._registrar_finalizacion(_('aprobó el pago y finalizó la orden especial.'))
        return True

    @api.onchange('proveedor_id')
    def _onchange_proveedor_id(self):
        for record in self:
            proveedor_seleccionado = record.proveedor_id.sudo()
            record.proveedor = (
                proveedor_seleccionado.nombre_razon_social_contratista or False
            )
            record.nit = proveedor_seleccionado.nit_completo or False
            if proveedor_seleccionado:
                telefono_celular = proveedor_seleccionado.telefono_celular or False
                record.direccion = proveedor_seleccionado.direccion or False
                record.telefono = telefono_celular
                record.cel = telefono_celular
                record.email = proveedor_seleccionado.correo_electronico or False
                record.ciudad = self._municipio_desde_texto_bdd(
                    proveedor_seleccionado.ciudad
                )
                record.codigo_ciiu = proveedor_seleccionado.ciiu or False
                record.objeto_contractual = (
                    proveedor_seleccionado.objeto_contrato or False
                )
                responsable_iva = self._valor_bdd_es_verdadero(
                    proveedor_seleccionado.responsable_iva
                )
                record.f_electronica = self._valor_bdd_es_verdadero(
                    proveedor_seleccionado.facturacion_electronica
                )
                record.r_iva = responsable_iva
                record.no_r_iva = not responsable_iva
                record.declaracion_renta = self._valor_bdd_es_verdadero(
                    proveedor_seleccionado.declara_renta
                )
                record.regimen_simple_tributacion = self._valor_bdd_es_verdadero(
                    proveedor_seleccionado.regimen_simple_tributacion
                )
                record.regimen_especial = self._valor_bdd_es_verdadero(
                    proveedor_seleccionado.regimen_especial
                )
                record.regimen_ordinario = self._valor_bdd_es_verdadero(
                    proveedor_seleccionado.regimen_ordinario
                )
            else:
                record.direccion = False
                record.telefono = False
                record.cel = False
                record.email = False
                record.ciudad = False
                record.codigo_ciiu = False
                record.objeto_contractual = False
                record.f_electronica = False
                record.r_iva = False
                record.no_r_iva = False
                record.declaracion_renta = False
                record.regimen_simple_tributacion = False
                record.regimen_especial = False
                record.regimen_ordinario = False

    @staticmethod
    def _valor_bdd_es_verdadero(valor):
        """Convierte las marcas importadas desde Excel en valores booleanos."""
        return str(valor or '').strip().upper() in {
            'X', 'SI', 'SÍ', '1', 'TRUE', 'VERDADERO',
        }

    @api.depends('subtotal', 'iva', 'select_fondos', 'gastos_expedicion')
    def _compute_totales_separados(self):
        """Calcula el total según el fondo e incluye la expedición en pólizas."""
        for record in self:
            subtotal = record.subtotal or 0.0
            iva = record.iva or 0.0
            gastos = record.gastos_expedicion or 0.0
            valor_base = subtotal + iva
            if record.select_fondos == 'es_produccion':
                record.total = valor_base
                record.total_poliza = 0.0
            elif record.select_fondos == 'es_poliza':
                record.total = 0.0
                record.total_poliza = valor_base + gastos
            else:
                record.total = 0.0
                record.total_poliza = 0.0

    @api.onchange('r_iva')
    def _onchange_r_iva(self):
        if self.r_iva:
            self.no_r_iva = False

    @api.onchange('no_r_iva')
    def _onchange_no_r_iva(self):
        if self.no_r_iva:
            self.r_iva = False

    @staticmethod
    def convertir_fecha_a_texto(fecha):
        """Convierte una fecha en texto con formato '04 de Octubre del 2024'."""
        if fecha:
            meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
            return f'{fecha.day:02d} de {meses[fecha.month - 1]} del {fecha.year}'
        return 'Fecha no asignada'

    @api.depends('fecha_solicitud')
    def _compute_fecha_solicitud_texto(self):
        for record in self:
            record.fecha_solicitud_texto = self.convertir_fecha_a_texto(record.fecha_solicitud)

    @api.depends('lineas_ids.valor_unitario')
    def _compute_subtotal(self):
        for record in self:
            record.subtotal = sum((line.valor_unitario for line in record.lineas_ids))

    def name_get(self):
        return [(record.id, '%s [NIT: %s]' % (record.name or 'Orden especial', record.nit)
                 if record.nit else record.name or 'Orden especial') for record in self]


class OrdenesEspecialesGuiaUsuario(models.TransientModel):
    _name = 'ordenes_especiales.guia_usuario'
    _description = 'Guía de usuario de órdenes especiales'


class OrdenesEspecialesSoporteEstadoWizard(models.TransientModel):
    _name = 'ordenes_especiales.soporte.estado.wizard'
    _description = 'Cambio administrativo de estado de orden especial'

    orden_id = fields.Many2one(
        'ordenes_especiales',
        string='Orden especial',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    estado_nuevo = fields.Selection([
        ('coordinador', 'Coordinación'),
        ('ventanilla', 'Ventanilla'),
        ('contabilidad', 'Contabilidad'),
        ('auxiliar_tesoreria', 'Auxiliar de Tesorería'),
        ('tesoreria', 'Tesorería'),
        ('finalizado', 'Finalizado'),
    ], string='Nuevo estado', required=True)

    def action_aplicar(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_('Solo Soporte puede cambiar manualmente el estado.'))

        orden = self.orden_id
        estado_anterior = orden.estado_ordenes_especiales
        estado_nuevo = self.estado_nuevo

        if not orden or not estado_nuevo:
            raise UserError(_('Debe seleccionar una orden y un estado.'))
        if estado_anterior == estado_nuevo:
            raise UserError(_('La orden ya se encuentra en el estado seleccionado.'))

        seleccion = dict(
            orden._fields['estado_ordenes_especiales']._description_selection(self.env)
        )
        etiqueta_anterior = seleccion.get(estado_anterior, estado_anterior or '')
        etiqueta_nueva = seleccion.get(estado_nuevo, estado_nuevo or '')

        orden.write({
            'estado_ordenes_especiales': estado_nuevo,
            'ultimo_estado_devolucion': False,
            'ultimo_usuario_devolucion_id': False,
            'estado_retorno_devolucion': False,
        })
        try:
            orden.message_post(
                body=(
                    '<p><strong>Cambio administrativo de estado · Soporte</strong></p>'
                    '<p><strong>%s</strong> cambió manualmente la orden de '
                    '<strong>%s</strong> a <strong>%s</strong>.</p>'
                ) % (
                    html_escape(self.env.user.name),
                    html_escape(etiqueta_anterior),
                    html_escape(etiqueta_nueva),
                ),
                subtype_xmlid='mail.mt_note',
            )
        except Exception as error:
            _logger.warning(
                'No se pudo registrar el cambio manual de estado de la orden %s: %s',
                orden.id, error,
            )
        return {'type': 'ir.actions.act_window_close'}


class OrdenesEspecialesLinea(models.Model):
    _name = 'ordenes_especiales.linea'
    _description = 'Detalle de orden especial'

    orden_id = fields.Many2one('ordenes_especiales', string='Orden especial', required=True, ondelete='cascade')
    descripcion = fields.Char(string='Descripción', required=True)
    valor_unitario = fields.Float(string='Valor Total', required=True)


class OrdenesEspecialesMunicipio(models.Model):
    _name = 'ordenes_especiales.municipio'
    _description = 'Departamento y municipio de orden especial'

    orden_id = fields.Many2one('ordenes_especiales', string='Orden especial', required=True, ondelete='cascade')
    departamentos = fields.Many2one('departamento', string='Departamento')
    municipios2 = fields.Many2one('municipio', string='Municipio')

    @api.onchange('departamentos')
    def _onchange_departamentos(self):
        self.municipios2 = False
        prefix = self.departamentos.Codigo_Departamento
        domain = [('Codigo_Municipio', '=like', prefix[:2] + '%')] if prefix else []
        return {'domain': {'municipios2': domain}}

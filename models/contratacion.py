#!/usr/bin/python
from odoo import fields, models, api, registry
from odoo.api import Environment
import requests
import threading
from odoo.exceptions import AccessError, UserError
from .drive import GoogleDriveAuth
import logging

_logger = logging.getLogger(__name__)



class Contratacion(models.Model):
    _name = "contratacion"
    _description = "Contratación"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        # fields_get devuelve la metadata que usa Odoo para construir vistas,
        # filtros personalizados y grupos personalizados.
        # allfields: si llega informado, limita la consulta a esos campos.
        # attributes: si llega informado, limita los atributos de metadata.
        fields_info = super().fields_get(allfields=allfields, attributes=attributes)

        attributes_set = set(attributes or [])
        is_export_metadata = bool(attributes_set & {'exportable', 'importable'})
        is_search_metadata = not attributes_set or bool(
            attributes_set & {'selectable', 'searchable', 'sortable', 'groupable'}
        )

        if not is_search_metadata or is_export_metadata:
            return fields_info

        # En este modelo queremos que el usuario use solo los filtros y grupos
        # predefinidos en el XML. Por eso ocultamos todos los campos del selector
        # nativo de "Anadir filtro/grupo personalizado" sin borrar los campos.
        for field_data in fields_info.values():
            # Evita que el campo se ofrezca como opcion seleccionable.
            field_data['selectable'] = False
            # Evita que el campo se ofrezca en filtros personalizados.
            field_data['searchable'] = False
            # Evita que el campo se ofrezca para ordenamientos personalizados.
            field_data['sortable'] = False
            # Evita que el campo se ofrezca para agrupaciones personalizadas.
            field_data['groupable'] = False

        # Algunos many2many de acceso/tableros seguian apareciendo en el selector
        # de grupos personalizados porque Odoo los trata como campos agrupables.
        # Marcarlos como no almacenados solo en esta metadata del cliente los saca
        # del selector sin modificar el campo real ni la base de datos.
        for field_name in (
            'users_with_access',
            'users_with_access_juridico',
            'users_with_access_financiero',
            'tablero_avances_ids',
        ):
            if field_name in fields_info:
                fields_info[field_name]['store'] = False

        # Retorna la metadata ya filtrada al cliente web de Odoo.
        return fields_info

    def _es_consulta_coordinacion_financiera(self):
        return self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')

    def _marcar_apertura_si_permitido(self):
        # Evita escrituras automáticas cuando el usuario solo tiene rol de consulta.
        if not self._es_consulta_coordinacion_financiera():
            self.write({'write_date': fields.Datetime.now()})

    def _bloquear_escritura_usuario_consulta(self):
        if self._es_consulta_coordinacion_financiera():
            raise UserError("El usuario de consulta solo puede visualizar proyectos.")

    name = fields.Char(string='Nombre del proyecto')
    new_name = fields.Char(related='apodo')

    new_total_costos_produccion_N_r = fields.Float(related='ejecucion4_ids.new_total_costos_produccion_N_r')
    contratos_vco_resumen = fields.Float(related="ejecucion4_ids.contratos_vco_resumen")


    tablero_avances_ids = fields.Many2many('tablero_avances', string="Tablero de avances relacionado")
    #contratista_proveedor_ids2 = fields.One2many('contratacion_ids', string="contratista y proveedor")
    apodo = fields.Char(related='ejecucion4_ids.apodo')

    # Relación con VistaSQL
    vista_sql_id = fields.Many2one("vistas_sql", string="Vista SQL Relacionada")

    #18-11-2024 SAMUEL
    ############################################HANS##################################################################
    saldo_disponible_contratar2 = fields.Monetary(related="contratacion_ids.saldo_disponible_contratar", store=True)
    contratacion_ids = fields.One2many('contrataciones_fuv', 'proyecto_relacionado')
    saldo_disponible_produccion = fields.Monetary(related="contratacion_ids.saldo_disponible_produccion") 
    saldo_disponible_poliza_total = fields.Monetary(related="contratacion_ids.saldo_disponible_poliza_total")
    fecha_finalizacion2_otrosí_new = fields.Date(string="Fecha de finalización", related='ejecucion4_ids.fecha_finalizacion')
    contratacion_ids_s = fields.One2many('orden_compra_servicios', 'proyecto_relacionado_s')
    contratacion_ids_viaticos = fields.One2many('viaticos', 'proyecto_relacionado_viaticos')





    total_compras = fields.Monetary(currency_field="currency_id", compute="_compute_total_bienes2", store=True)
    @api.depends("contratacion_ids_s.total")
    def _compute_total_bienes2(self):
        for record in self:
            record.total_compras = sum(record.contratacion_ids_s.mapped('total'))

    total_bienes = fields.Monetary(currency_field="currency_id", compute="_compute_total_bienes", store=True)
    @api.depends("contratacion_ids_c.total")
    def _compute_total_bienes(self):
        for record in self:

            record.total_bienes = sum(record.contratacion_ids_c.mapped('total'))

    contratacion_ids_c = fields.One2many('orden_compra_bienes', 'proyecto_relacionado_c')


    def orden_compra_bienes(self):
        # Actualiza la fecha de modificación
        self._marcar_apertura_si_permitido()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Orden de Compra de Bienes',
            'res_model': 'orden_compra_bienes',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [('proyecto_relacionado_c', '=', self.id)],
            'context': {'default_proyecto_relacionado_c': self.id},
        }

    def orden_compra_servicios(self):
        # Actualiza la fecha de modificación
        self._marcar_apertura_si_permitido()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Orden de Compra de Servicios',
            'res_model': 'orden_compra_servicios',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [('proyecto_relacionado_s', '=', self.id)],
            'context': {'default_proyecto_relacionado_s': self.id},
        }

    def action_viaticos(self):
        self._marcar_apertura_si_permitido()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Viáticos',
            'res_model': 'viaticos',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [('proyecto_relacionado_viaticos', '=', self.id)],
            'context': {'default_proyecto_relacionado_viaticos': self.id},
        }

    ############################################SAMUEL##################################################################
    is_editable = fields.Boolean(string="Es editable", default=False)
    ############################################SAMUEL##################################################################



    @api.depends('apodo')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.apodo or record.name
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)



    ##Relaciones
    # Codigo de teylor
    resultado_tecnica_representada_dinero = fields.Monetary(
        related='ejecucion2_ids.resultado_tecnica_representada_dinero')
    suma_total = fields.Float(string='Suma Total', related="ejecucion2_ids.suma_total")

    valor_sin_contrapartida = fields.Float(related='ejecucion4_ids.valor_total2_restado_contrapartida')
    valor_poliza = fields.Monetary(related='ejecucion4_ids.suma_polizas')
    sobre_costo = fields.Monetary(related='ejecucion4_ids.valor_sobre_costo')
    valor_administracion_costo_operativo = fields.Monetary(related='ejecucion4_ids.suma_Admon_12')
    # Codigo de teylor


    informacion_base_agregar_ids = fields.One2many('informacion_base_agregar', 'contratacion_id',
                                                   string='Información Base')

    modalidad_contratacion2 = fields.Selection(string='Modalidad de Contratación', related="informacion_base_agregar_ids.modalidad_contratacion")
    objeto_proyecto2 = fields.Char(string='Objeto del proyecto', related="informacion_base_agregar_ids.objeto_proyecto")
    id_proyecto2 = fields.Char(string='Id del proyecto', related="informacion_base_id.id_proyecto")


    informacion_base_id = fields.Many2one('informacion_base_agregar', string='Información Base',
                                          related="ejecucion2_ids.informacion_base_id")

    area_solicitante_proyecto = fields.Selection(
        selection=lambda self: self.env['informacion_base_agregar']._fields['area_solicitante2'].selection,
        string='Dependencia - Área Solicitante',
        compute='_compute_area_solicitante_proyecto',
        store=True,
        readonly=True,
    )

    @api.depends('informacion_base_id.area_solicitante2', 'informacion_base_agregar_ids.area_solicitante2')
    def _compute_area_solicitante_proyecto(self):
        for record in self:
            info_base = record._informacion_base_para_contratacion()
            record.area_solicitante_proyecto = info_base.area_solicitante2 if info_base else False

    asignar_coordinador = fields.Many2one(
        'res.users',
        string='Asignar coordinador como responsable',
        compute='_compute_asignar_coordinador',
        store=True,
        readonly=True,
    )

    @api.depends('informacion_base_id.asignar_coordinador', 'informacion_base_agregar_ids.asignar_coordinador')
    def _compute_asignar_coordinador(self):
        for record in self:
            info_base = record.informacion_base_id or record.informacion_base_agregar_ids[:1]
            record.asignar_coordinador = info_base.asignar_coordinador if info_base else False


    ejecucion2_ids = fields.One2many('ejecucion', 'proyecto_relacionado2', string="Ejecución")
    valor_cobrado3 = fields.Monetary(string='Valor Cobrado', related='ejecucion2_ids.valor_cobrado')
    valor_pago_2suma3 = fields.Float(string='Valor contratado', related='ejecucion2_ids.valor_pago_2suma2')
    valor_por_facturar = fields.Monetary(string='Valor por facturar', readonly=True,
                                         related='ejecucion2_ids.valor_por_facturar')

    ejecucion3_ids = fields.One2many('cierre', 'proyecto_relacionado3', string="Cierre")
    # Sam 19-03-2024
    valor_total_final = fields.Float(related='ejecucion4_ids.valor_total2_final')
    valor_a_contratar = fields.Monetary(related='ejecucion4_ids.suma_total_costos_produccion_N')
    # Sam 20-03-2024
    suma_valor_total_contrato2 = fields.Monetary(related='contratacion_ids.suma_valor_total_contrato')

    costos_total_valor2R = fields.Float(related="ejecucion4_ids.costos_total_valor2R")
    poliza_total = fields.Monetary(related="ejecucion4_ids.poliza_total_r")

    suma_costos_fuv = fields.Monetary(related="ejecucion4_ids.suma_costos_fuv")
    suma_adicion_otro_si2 = fields.Monetary(related="ejecucion4_ids.suma_adicion_otro_si2")
    valor_total2_final = fields.Float(related="ejecucion4_ids.valor_total2_final")

    ##Campos llamados para el tablero de estados del director
    ejecucion4_ids = fields.One2many('mcb', 'contrataciones_id', string="MCB")

    valor_proyecto_convenio3_boolean = fields.Boolean(
        related="ejecucion4_ids.valor_proyecto_convenio3_boolean",
        readonly=False,
    )

    estado = fields.Selection(
        [
            ('borrador', 'Pendiente por completar'),
            ('aprobado', 'Aprobado'),
        ],
        default='borrador',
        compute='procede_contratacion_si',
        store=True,
    )

    @api.depends('valor_proyecto_convenio3_boolean', 'contratacion_ids.suma_valor_total_contrato', 'write_date')
    def procede_contratacion_si(self):
        for record in self:
            if record.valor_proyecto_convenio3_boolean:
                record.estado = 'aprobado'
            else:
                record.estado = 'borrador'


    @api.depends('estado')
    def check_estado_borrador(self):
        for record in self:
            if record.estado == 'borrador':
                raise UserError(
                    'La fase: 1. PRE-CONTRACTUAL\n'
                    'Está en estado: "PENDIENTE POR COMPLETAR".\n'
                    'Por favor ingresar a la MCB del proyecto y asignar los valores requeridos para continuar la contratación.'
                )

    mcb_centro_costo = fields.Char(
        string="Centro de costo",
        compute='_compute_mcb_centro_costo',
        tracking=True,
        search='_search_mcb_centro_costo',
    )

    @api.depends('ejecucion4_ids.centro_costo', 'id_proyecto2')
    def _compute_mcb_centro_costo(self):
        for record in self:
            mcb_con_centro = record.ejecucion4_ids.filtered('centro_costo')[:1]
            record.mcb_centro_costo = (
                mcb_con_centro.centro_costo
                if mcb_con_centro
                else record.id_proyecto2
            )

    def _search_mcb_centro_costo(self, operator, value):
        return [('ejecucion4_ids.centro_costo', operator, value)]

    adicion_otrosi44 = fields.Monetary(related='ejecucion4_ids.suma_adicion_otro_si2')

    contrapartida_estados = fields.Monetary(related='ejecucion4_ids.contrapartida')

    entidad_contratante = fields.Char(string="Entidad contratante", related='ejecucion4_ids.entidad_contratante')
    fecha_finalizacion2 = fields.Date(string="Fecha de finalización", related='ejecucion4_ids.fecha_finalizacion2')
    todos_los_valores_total2 = fields.Float(related='ejecucion4_ids.valor_total2')
    objecto_proyecto = fields.Char(related='ejecucion4_ids.objeto_proyecto')
    suma_contrapartidas = fields.Monetary(related='ejecucion4_ids.contrapartida4')
    contrapartida = fields.Monetary(related='ejecucion4_ids.contrapartida')
    fecha_inicio = fields.Date(related='ejecucion4_ids.fecha_inicio')
    currency_id = fields.Many2one('res.currency', string='Moneda')
    numero_contrato = fields.Char(related='ejecucion4_ids.numero_contrato')

    fecha_final_proyecto = fields.Date(string='Fecha final del proyecto', related='ejecucion4_ids.fecha_finalizacion2')
    fecha_final_proyecto_texto = fields.Char(string="Fecha Solicitud en Texto", compute="_compute_fecha_final_proyecto_texto", store=True)

    @staticmethod
    def convertir_fecha_a_texto(fecha):
        """Convierte una fecha en texto con formato '04 de Octubre del 2024'."""
        if fecha:
            meses = [
                'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'
            ]
            return f"Fecha fin del proyecto: {fecha.day:02d} de {meses[fecha.month - 1]} del {fecha.year}"
        return 'Fecha no asignada'

    @api.depends('fecha_final_proyecto')
    def _compute_fecha_final_proyecto_texto(self):
        for record in self:
            record.fecha_final_proyecto_texto = self.convertir_fecha_a_texto(record.fecha_final_proyecto)



    ejecucion5_ids = fields.One2many('ficharesumen', 'contratacio2_id', string="ficha resumen")
    objeto_proyecto3 = fields.Char(string="Objeto", related='ejecucion5_ids.objeto')
    id_proyecto3 = fields.Char(string='Id Proyecto', related='ejecucion5_ids.id_proyecto2')
    ##Samuel agrego metodo para separar vistas de registros por coordinador linea 11 a 28

    coordinador_asignado = fields.Many2one('res.users', string="Coordinador asignado",
                                           related='ejecucion5_ids.coordinador_asignado',
                                           store=True)


    # Campo de acceso general

    users_with_access = fields.Many2many('res.users', string='Permitir acceso de usuarios al proyecto')
    drive_reader_user_ids = fields.Many2many(
        'res.users',
        'contratacion_drive_reader_rel',
        'project_id',
        'user_id',
        string='Lectores de Drive de este proyecto',
        domain=[('active', '=', True), ('share', '=', False)],
        help=(
            'Usuarios a los que se dará acceso de lector únicamente a la carpeta '
            'principal de este proyecto. Al quitarlos de esta lista se retira solo '
            'el permiso que fue creado por esta funcionalidad.'
        ),
        tracking=True,
    )
    drive_reader_available_user_ids = fields.Many2many(
        'res.users',
        string='Lectores de Drive disponibles',
        compute='_compute_drive_reader_available_users',
    )

    def _compute_drive_reader_available_users(self):
        available_users = self.env['drive.global.access'].sudo().search([], limit=1).user_ids
        for record in self:
            record.drive_reader_available_user_ids = available_users
    create_uid = fields.Many2one('res.users', string='Creador del proyecto', readonly=True)
    creator_user_id = fields.Many2one('res.users', string='Creador del proyecto (relacionado)', related='create_uid',
                                      store=True)

    def add_group_users_to_access(self):
        """Método para agregar usuarios de grupos específicos y al creador del proyecto a la lista users_with_access."""
        # Obtener usuarios de los grupos
        users_juridico = self.env.ref('Matriz.group_contratacion_director_juridico').users
        users_financiero = self.env.ref('Matriz.group_contratacion_director_financiero').users
        users_ventanilla_unica = self.env.ref('Matriz.group_contratacion_ventanilla_unica').users

        # Combinar los usuarios de los grupos
        users_to_add = users_juridico | users_financiero | users_ventanilla_unica

        for record in self:
            # Agregar el creador del proyecto
            if record.create_uid:
                users_to_add |= record.create_uid

            print(
                f"Usuarios agregados al campo users_with_access del proyecto {record.id}: {[user.name for user in users_to_add]}")

            # Asignar los usuarios al campo users_with_access
            record.users_with_access = [(4, user.id) for user in users_to_add]

    # Campo de acceso jurídico
    users_with_access_juridico = fields.Many2many(
        'res.users',
        string='Usuarios con acceso jurídico',
        domain=lambda self: self._get_juridical_users_domain(),
        relation='contratacion_users_access_juridico_rel'
    )

    # Campo de acceso financiero
    users_with_access_financiero = fields.Many2many(
        'res.users',
        string='Usuarios con acceso financiero',
        domain=lambda self: self._get_financiero_users_domain(),
        relation='contratacion_users_access_financiero_rel'
    )

    @api.model
    def _get_juridical_users_domain(self):
        """Devuelve el dominio para filtrar usuarios del grupo jurídico"""
        group_juridico = self.env.ref('Matriz.group_contratacion_juridico', raise_if_not_found=False)
        if group_juridico:
            return [('groups_id', 'in', group_juridico.id)]
        return []

    @api.model
    def _get_financiero_users_domain(self):
        """Devuelve el dominio para filtrar usuarios del grupo financiero"""
        group_financiero = self.env.ref('Matriz.group_contratacion_financiero', raise_if_not_found=False)
        if group_financiero:
            return [('groups_id', 'in', group_financiero.id)]
        return []

    # 🔹 Nuevo campo computado para la combinación de nombres
    full_name = fields.Char(compute="_compute_full_name", store=True)

    @api.depends("new_name", "name")
    def _compute_full_name(self):
        for record in self:
            record.full_name = record.new_name or record.name  # Usa new_name si existe, sino name

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        """Combina restricciones de acceso y ordenamiento basado en filtros seleccionados."""

        # 🔹 1️⃣ Restricciones de acceso
        # Se agregan los grupos 'Matriz.group_presupuestos' y 'Matriz.group_auditoria_financiera' 
        # a la excepción de la restricción de acceso.
        if not (self.env.user.has_group('Matriz.group_contratacion_directivo') or 
                self.env.user.has_group('Matriz.group_presupuestos') or 
                self.env.user.has_group('Matriz.group_auditoria_financiera') or
                self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')):
            
            access_conditions = [
                ('create_uid', '=', self.env.uid),
                ('users_with_access', 'in', self.env.uid),
                ('users_with_access_juridico', 'in', self.env.uid),
                ('users_with_access_financiero', 'in', self.env.uid),
            ]
            args = ['|'] * (len(access_conditions) - 1) + access_conditions + list(args)

        # 🔹 2️⃣ Crear variable combinada para ordenar por nombre
        if self.env.context.get('order_create_date_desc'):
            order = 'fch_creacion desc'
        elif self.env.context.get('order_create_date_asc'):
            order = 'fch_creacion asc'
        elif self.env.context.get('order_name'):
            order = 'full_name asc'
        elif self.env.context.get('order_name_desc'):
            order = 'full_name desc'

        # 🔹 3️⃣ Ejecutar búsqueda con ambas lógicas combinadas
        return super().search(args, offset=offset, limit=limit, order=order, count=count)

    # Permisos drive utilizando la lista

    drive_folder_id = fields.Many2one('drive_folder', string='Carpeta de Google Drive', compute='_compute_drive_folder')

    @api.onchange('users_with_access')
    def _onchange_users_with_access(self):
        # Verificar si ya existe una relación para no sobrescribirla
        if not self.drive_folder_id:
            # Si no se ha establecido la relación, ejecutar _compute_drive_folder
            self._compute_drive_folder()

            # Guardar el valor de drive_folder_id
            self.env.cr.commit()  # Asegura que los cambios se guarden en la base de datos

        # Verificar que la relación se haya establecido antes de continuar
        if not self.drive_folder_id:
            _logger.warning("No se estableció una carpeta relacionada; no se ejecutará la siguiente acción.")
            return

        # Luego, ejecutar _share_folder_with_new_users
        self._share_folder_with_new_users()

    def _compute_drive_folder(self):
        print("se ejecuto")
        for record in self:
            # Verificar si el registro tiene una carpeta asociada por el proyecto relacionado
            folder = self.env['drive_folder'].search([
                ('proyecto_relacionado', '=', record.id)
            ], limit=1, order='sequence, id')
            record.drive_folder_id = folder.id if folder else False



    

    def _share_folder_with_new_users(self, force_folder_id=None):
        #print("se ejecuto")
        # Verificar si hay una carpeta de Google Drive asociada
        if not self.drive_folder_id or not self.drive_folder_id.folder_guardar_id:
            _logger.warning(
                "No se ha configurado una carpeta de Google Drive o el ID de la carpeta no está disponible.")
            return

        # Obtener autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        if not drive:
            _logger.error("No se pudo autenticar en Google Drive.")
            return

        folder_id = force_folder_id or self.drive_folder_id.folder_guardar_id

        # Cargamos la carpeta con el ID correcto
        folder = drive.CreateFile({'id': folder_id})

        # Obtener permisos actuales de la carpeta
        try:
            existing_permissions = folder.GetPermissions()
            existing_emails = {perm.get('emailAddress'): perm.get('id') for perm in existing_permissions if
                               'emailAddress' in perm}
        except Exception as e:
            _logger.error(f"Error al obtener permisos de la carpeta: {e}")
            return

        # Identificar usuarios previos y actuales
        previous_users = self._origin.users_with_access.ids if self._origin else []
        current_users = self.users_with_access.ids

        # Usuarios que se quitaron
        removed_users_ids = set(previous_users) - set(current_users)
        _logger.info(f"Usuarios eliminados: {removed_users_ids}")

        for user_id in removed_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if email and email in existing_emails:
                try:
                    permission_id = existing_emails[email]
                    folder.DeletePermission(permission_id)
                    _logger.info(f"Permiso revocado para: {email}")
                except Exception as e:
                    _logger.error(f"Error al revocar permiso para {email}: {e}")
                    try:
                        folder.DeletePermission(permission_id)
                        _logger.info(f"Permiso revocado para (reintento): {email}")
                    except Exception as e:
                        _logger.error(f"Error al revocar permiso para {email} (reintento fallido): {e}")

        # Usuarios que se agregaron
        added_users_ids = set(current_users) - set(previous_users)
        
        # 2. TRUCO AUTOMÁTICO: Si viene del Vigilante (force_folder_id presente) y la resta dio cero,
        # obligamos al sistema a tomar la lista completa de usuarios actuales para que Google los invite.
        if force_folder_id and not added_users_ids:
            added_users_ids = set(current_users)

        _logger.info(f"Usuarios agregados a procesar: {added_users_ids}")

        for user_id in added_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if email and email not in existing_emails:
                try:
                    folder.InsertPermission({
                        'type': 'user',
                        'value': email,
                        'role': 'reader',
                    })
                    _logger.info(f"Carpeta compartida con: {email}")
                except Exception as e:
                    _logger.error(f"Error al compartir carpeta con {email}: {e}")

        # Los lectores de Drive se toman exclusivamente de la lista del proyecto.
        # La configuración central ya no concede permisos a proyectos nuevos.
        global_access = self.env['drive.global.access'].sudo().search([], limit=1)
        if global_access:
            result = global_access.sync_project_readers(
                self, folder_id=folder_id, drive=drive
            )
            if result['errors']:
                _logger.error(
                    'Errores al aplicar lectores globales al proyecto %s: %s',
                    self.id,
                    '; '.join(result['errors']),
                )

    def otorgar_permiso_edicion_archivo_at(
        self,
        drive_file_id,
        modelo_origen,
        usuario,
    ):
        """Otorga edición únicamente sobre un archivo generado por el AT.

        La carpeta principal conserva sus permisos ``reader``. Esta excepción
        usa directamente el ID devuelto por Google Drive, por lo que no busca
        archivos por nombre ni recorre carpetas o subcarpetas.
        """
        self.ensure_one()

        # Evita que otros modelos reutilicen accidentalmente esta excepción.
        if modelo_origen != 'proceso_gestion_tributaria':
            raise UserError(
                "La edición especial de Drive solo aplica al Anexo Tributario."
            )
        if not drive_file_id:
            raise UserError(
                "Google Drive no devolvió el identificador del archivo del AT."
            )
        if not usuario.has_group('Matriz.group_presupuestos'):
            raise UserError(
                "Solo Presupuesto puede recibir edición sobre el archivo del AT."
            )
        if not usuario.email:
            raise UserError(
                "El usuario de Presupuesto no tiene correo configurado."
            )

        # El usuario debe estar relacionado directamente con el proyecto. Se
        # revisan las listas existentes, sin hacer búsquedas sobre otros modelos.
        usuarios_proyecto = (
            self.users_with_access
            | self.users_with_access_financiero
            | self.users_with_access_juridico
        )
        if usuario not in usuarios_proyecto:
            raise UserError(
                "El usuario de Presupuesto no está asociado a este proyecto."
            )

        drive = GoogleDriveAuth.get_instance().get_drive()
        if not drive:
            raise UserError("No fue posible autenticar la conexión con Google Drive.")

        # El permiso writer se asigna al archivo exacto, nunca a la carpeta del
        # proyecto; así Presupuesto no puede editar los demás documentos.
        archivo = drive.CreateFile({'id': drive_file_id})
        permiso = archivo.InsertPermission({
            'type': 'user',
            'value': usuario.email,
            'role': 'writer',
        })
        _logger.info(
            '[DRIVE-AT-PRESUPUESTO] Permiso writer asignado. '
            'proyecto_id=%s archivo_drive_id=%s usuario_id=%s correo=%s',
            self.id,
            drive_file_id,
            usuario.id,
            usuario.email,
        )
        return permiso

    def action_soporte_editores_a_lectores_drive(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError("Solo el grupo de soporte puede corregir permisos de Google Drive.")

        drive_folder = self.drive_folder_id
        if not drive_folder:
            drive_folder = self.env['drive_folder'].sudo().search([
                ('proyecto_relacionado', '=', self.id),
            ], limit=1, order='sequence, id')
        folder_id = (
            drive_folder.folder_guardar_id
            or drive_folder.google_drive_id
            if drive_folder else False
        )
        if not folder_id:
            raise UserError("El proyecto no tiene una carpeta principal vinculada en Google Drive.")

        project_users = (
            self.users_with_access
            | self.users_with_access_juridico
            | self.users_with_access_financiero
        )
        project_emails = {
            email.strip().lower()
            for email in project_users.mapped('email')
            if email and email.strip()
        }
        if not project_emails:
            raise UserError("El proyecto no tiene usuarios con correo para corregir permisos.")

        drive = GoogleDriveAuth.get_instance().get_drive()
        if not drive:
            raise UserError("No fue posible autenticar la conexión con Google Drive.")

        folder = drive.CreateFile({'id': folder_id})
        try:
            permissions = folder.GetPermissions()
        except Exception as error:
            _logger.exception("No fue posible consultar permisos de Drive para %s", folder_id)
            raise UserError(f"No fue posible consultar los permisos de Google Drive: {error}")

        changed_emails = []
        failed_emails = []
        permissions_api = drive.auth.service.permissions()
        for permission in permissions:
            email = (permission.get('emailAddress') or '').strip().lower()
            if (not email or email not in project_emails
                    or permission.get('role') != 'writer'):
                continue
            permission_id = permission.get('id')
            if not permission_id:
                continue
            try:
                request = permissions_api.update(
                    fileId=folder_id,
                    permissionId=permission_id,
                    body={'role': 'reader'},
                    supportsAllDrives=True,
                )
                request.execute()
                changed_emails.append(email)
            except TypeError:
                try:
                    permissions_api.update(
                        fileId=folder_id,
                        permissionId=permission_id,
                        body={'role': 'reader'},
                    ).execute()
                    changed_emails.append(email)
                except Exception as error:
                    failed_emails.append(f"{email}: {error}")
            except Exception as error:
                failed_emails.append(f"{email}: {error}")

        if failed_emails:
            _logger.error(
                "Permisos Drive no corregidos en proyecto %s: %s",
                self.id, '; '.join(failed_emails),
            )
            raise UserError(
                "Algunos permisos no pudieron actualizarse:\n%s"
                % '\n'.join(failed_emails)
            )

        if changed_emails:
            message = "%s usuario(s) cambiados de editor a lector: %s" % (
                len(changed_emails), ', '.join(changed_emails))
            self.message_post(body=message)
        else:
            message = "No se encontraron usuarios asignados con permiso de editor."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Permisos de Google Drive',
                'message': message,
                'type': 'success',
                'sticky': False,
            },
        }



    # def _send_email_to_financiero(self, director_name, email):
    #     form_id = str(self.id).replace("NewId_", "")
    #     base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
    #     """ Envía un correo al Director Financiero notificando que debe asignar el financiero a cargo. """
    #
    #     formulario_url = f"/web#id={form_id}&cids=1&menu_id=144&active_id=16&model=contratacionv&view_type=form"
    #     formulario_url = base_url + formulario_url
    #
    #
    #     # Obtener nombre del proyecto
    #     nombres_proyecto = [self.name, self.new_name]
    #     nombre_proyecto = ', '.join(filter(None, nombres_proyecto))  # Filtrar nombres vacíos
    #
    #     # Construir el cuerpo del correo
    #     body_html = f"""
    #         <p><strong>{nombre_proyecto}</strong></p>
    #         <p>Hola, Director Financiero <strong>{director_name}</strong>,</p>
    #         <p>Se ha compartido el proyecto <strong>{nombre_proyecto}</strong>.
    #         Por favor, ingrese en el siguiente enlace y asigne el financiero a cargo.</p>
    #         <p><a href='{formulario_url}'>Ir al Formulario</a></p>
    #     """
    #
    #     # Crear y enviar el correo
    #     mail_values = {
    #         'subject': f"{nombre_proyecto} - Asignación Financiera",
    #         'email_from': self.env.user.email,
    #         'email_to': email,
    #         'body_html': body_html,
    #         'auto_delete': True,
    #     }
    #
    #     mail_id = self.env['mail.mail'].sudo().create(mail_values)
    #     mail_id.send()



    @api.onchange('users_with_access_financiero')
    def _onchange_users_with_access_financiero(self):
        # Verificar si ya existe una relación para no sobrescribirla
        if not self.drive_folder_id:
            # Si no se ha establecido la relación, ejecutar _compute_drive_folder
            self._compute_drive_folder()

            # Guardar el valor de drive_folder_id
            self.env.cr.commit()  # Asegura que los cambios se guarden en la base de datos

        # Verificar que la relación se haya establecido antes de continuar
        if not self.drive_folder_id:
            _logger.warning("No se estableció una carpeta relacionada; no se ejecutará la siguiente acción.")
            return

        # Luego, ejecutar _share_folder_with_new_users
        self._share_folder_with_new_users_financiero()

    def _share_folder_with_new_users_financiero(self):
        """Comparte la carpeta con los usuarios del grupo financiero (users_with_access_financiero) y envía notificaciones."""
        if not self.drive_folder_id or not self.drive_folder_id.folder_guardar_id:
            _logger.warning(
                "No se ha configurado una carpeta de Google Drive o el ID de la carpeta no está disponible.")
            return

        # Obtener autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        if not drive:
            _logger.error("No se pudo autenticar en Google Drive.")
            return

        # Obtener el ID de la carpeta
        folder_id = self.drive_folder_id.folder_guardar_id
        folder = drive.CreateFile({'id': folder_id})

        # Obtener permisos actuales de la carpeta
        try:
            existing_permissions = folder.GetPermissions()
            existing_emails = {perm.get('emailAddress'): perm.get('id') for perm in existing_permissions if
                               'emailAddress' in perm}
        except Exception as e:
            _logger.error(f"Error al obtener permisos de la carpeta: {e}")
            return

        # Usuarios anteriores y actuales para acceso financiero
        previous_users = set(self._origin.users_with_access_financiero.ids if self._origin else [])
        current_users = set(self.users_with_access_financiero.ids)

        # Identificar usuarios eliminados y agregados
        removed_users_ids = previous_users - current_users
        added_users_ids = current_users - previous_users

        # Revocar permisos de usuarios eliminados
        for user_id in removed_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if email and email in existing_emails:
                try:
                    permission_id = existing_emails[email]
                    folder.DeletePermission(permission_id)
                    _logger.info(f"Permiso revocado para: {email}")
                except Exception as e:
                    _logger.error(f"Error al revocar permiso para {email}: {e}")
                    # Reintento en caso de error
                    try:
                        folder.DeletePermission(permission_id)
                        _logger.info(f"Permiso revocado para (reintento): {email}")
                    except Exception as e:
                        _logger.error(f"Error al revocar permiso para {email} (reintento fallido): {e}")

        # Agregar permisos de usuarios nuevos y enviar notificaciones
        for user_id in added_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if email and email not in existing_emails:
                try:
                    # Compartir carpeta
                    folder.InsertPermission({
                        'type': 'user',
                        'value': email,
                        'role': 'reader',
                    })
                    _logger.info(f"Carpeta compartida con: {email}")

                    # Crear enlaces
                    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
                    base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

                    # Extraer el ID sin el prefijo 'NewId_'
                    form_id = str(self.id).replace("NewId_", "")

                    # Generar URL del proyecto en el modelo contratacion
                    contratacion_url = f"/web#id={form_id}&model=contratacion&view_type=form"
                    contratacion_url = base_url + contratacion_url

                    proyecto_nombre = self.name or self.new_name or 'Proyecto'
                    mensaje = f"""
                        <p>Hola,</p>
                        <p>Se te ha compartido el acceso al proyecto: <strong>{proyecto_nombre}</strong></p>
                        <p>Puedes acceder a los documentos a través del siguiente enlace:</p>
                        <ul>
                            <li>Ver proyecto en SGO: <a href="{contratacion_url}">Click aquí</a></li>
                        </ul>
                        <br/>
                        <p>Cordialmente,</p>
                        <p>Dirección Financiera</p>
                    """

                    # Enviar notificación usando message_notify
                    self.env['mail.thread'].message_notify(
                        partner_ids=[user.partner_id.id],
                        body=mensaje,
                        subject=f'Acceso compartido: {proyecto_nombre}',
                        email_from=self.env.user.email_formatted,
                        model=self._name,
                        res_id=self.id,
                    )

                    _logger.info(f"Notificación enviada a: {email}")

                except Exception as e:
                    _logger.error(f"Error al compartir carpeta o enviar notificación a {email}: {e}")

    @api.onchange('users_with_access_juridico')
    def _onchange_users_with_access_juridico(self):
        # Verificar si ya existe una relación para no sobrescribirla
        if not self.drive_folder_id:
            # Si no se ha establecido la relación, ejecutar _compute_drive_folder
            self._compute_drive_folder()

            # Guardar el valor de drive_folder_id
            self.env.cr.commit()  # Asegura que los cambios se guarden en la base de datos

        # Verificar que la relación se haya establecido antes de continuar
        if not self.drive_folder_id:
            _logger.warning("No se estableció una carpeta relacionada; no se ejecutará la siguiente acción.")
            return

        # Luego, ejecutar _share_folder_with_new_users
        self._share_folder_with_new_users_juridico()

    def _share_folder_with_new_users_juridico(self):
        """Comparte la carpeta con los usuarios del grupo jurídico (users_with_access_juridico)."""
        if not self.drive_folder_id or not self.drive_folder_id.folder_guardar_id:
            _logger.warning(
                "No se ha configurado una carpeta de Google Drive o el ID de la carpeta no está disponible.")
            return

        # Obtener autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        if not drive:
            _logger.error("No se pudo autenticar en Google Drive.")
            return

        # Obtener el ID de la carpeta
        folder_id = self.drive_folder_id.folder_guardar_id
        folder = drive.CreateFile({'id': folder_id})

        # Obtener permisos actuales de la carpeta
        try:
            existing_permissions = folder.GetPermissions()
            existing_emails = {perm.get('emailAddress'): perm.get('id') for perm in existing_permissions if
                               'emailAddress' in perm}
        except Exception as e:
            _logger.error(f"Error al obtener permisos de la carpeta: {e}")
            return

        # Usuarios anteriores y actuales para acceso jurídico
        previous_users = set(self._origin.users_with_access_juridico.ids if self._origin else [])
        current_users = set(self.users_with_access_juridico.ids)

        # Identificar usuarios eliminados y agregados
        removed_users_ids = previous_users - current_users
        added_users_ids = current_users - previous_users

        # Revocar permisos de usuarios eliminados
        for user_id in removed_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if email and email in existing_emails:
                try:
                    permission_id = existing_emails[email]
                    folder.DeletePermission(permission_id)
                    _logger.info(f"Permiso revocado para: {email}")
                except Exception as e:
                    _logger.error(f"Error al revocar permiso para {email}: {e}")
                    # Reintento en caso de error
                    try:
                        folder.DeletePermission(permission_id)
                        _logger.info(f"Permiso revocado para (reintento): {email}")
                    except Exception as e:
                        _logger.error(f"Error al revocar permiso para {email} (reintento fallido): {e}")

        # Agregar permisos de usuarios nuevos
        for user_id in added_users_ids:
            user = self.env['res.users'].browse(user_id)
            email = user.email
            if email and email not in existing_emails:
                try:
                    folder.InsertPermission({
                        'type': 'user',
                        'value': email,
                        'role': 'reader',  # 'writer' para permitir edición, 'reader' para solo lectura
                    })
                    _logger.info(f"Carpeta compartida con: {email}")
                except Exception as e:
                    _logger.error(f"Error al compartir carpeta con {email}: {e}")


    # Definir campos con widget "statusbar" Global


    coordinador_g = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                     string='Coordinador', default='red')
    juridica_g = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                  string='Jurídica', default='red')
    financiera_g = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                    string='Financiera', default='red')
    recursos_humanos_g = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                          string='Recursos humanos', default='red')



    # Definir campos con widget "statusbar" contratacion
    coordinador = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                   string='Coordinador', default='red')
    juridica = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                string='Jurídica', default='red')
    financiera = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                  string='Financiera', default='red')
    recursos_humanos = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                        string='Recursos humanos', default='red')

    # Definir campos con widget "statusbar" ejecucion
    coordinador_j = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                     string='Coordinador', default='red')
    juridica_j = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                  string='Jurídica', default='red')
    financiera_j = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                    string='Financiera', default='red')
    recursos_humanos_j = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
                                          string='Recursos humanos', default='red')

    # coordinador_r = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
    #                                  string='Coordinador', default='red')
    # juridica_r = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
    #                               string='Jurídica', default='red')
    # financiera_r = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
    #                                 string='Financiera', default='red')

    # recursos_humanos_r = fields.Selection([('red', 'No iniciado'), ('yellow', 'En progreso'), ('green', 'Completado')],
    #                                       string='Recursos humanos', default='red')




    def show_otrosi_popup(self):
        # Asegúrate de que haya al menos un registro relacionado
        if self.contratacion_ids:
            # Obtén la lista de IDs de los registros relacionados de contratacion fu
            contratacion_ids = self.contratacion_ids.ids

            # Abre la vista de formulario de contratacion fu en una ventana emergente con todos los registros
            return {
                'name': 'Contrataciones FUV',
                'view_mode': 'kanban,tree,form,activity',
                'res_model': 'contrataciones_fuv',
                'domain': [('id', 'in', contratacion_ids)],  # Filtra por los IDs de contratacion fu relacionados
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            # Maneja el caso donde no hay registros de contratacion fu relacionados
            # Puedes mostrar un mensaje o realizar otra acción aquí
            return {}

    def name_get(self):
        result = []
        for record in self:
            name = record.new_name  # Reemplaza con el campo correcto de informacion_base
            result.append((record.id, name))
        return result

    def Guardar(self):
        self.ensure_one()
        self.write({'name': self.new_name})
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False,
                                   default=lambda self: fields.Datetime.now())
    write_date = fields.Datetime(string='Fecha de modificación', readonly=True)


    hiden = fields.Char(string='hiden')
    nit2 = fields.Char(string="Nit2", tracking=True)

    # # Campo para registrar la última vez que se ejecutó verificar_vencimiento
    # ultima_ejecucion = fields.Datetime(string="Última ejecución", copy=False)
    #
    # def llamar_verificar_vencimiento(self):
    #     """Verifica si la función ya se ejecutó en las últimas 24 horas y ejecuta verificar_vencimiento en un hilo."""
    #
    #     # Obtener la fecha y hora actual
    #     ahora = fields.Datetime.now()
    #
    #     def ejecutar_verificacion():
    #         # Validar si la función ya se ejecutó en las últimas 24 horas
    #         if self.ultima_ejecucion:
    #             diferencia = ahora - self.ultima_ejecucion
    #             if diferencia.total_seconds() < 86400:  # 24 horas en segundos
    #                 raise UserError("La verificación ya se ejecutó en las últimas 24 horas.")
    #
    #         # Actualizar el campo ultima_ejecucion para registrar la ejecución actual
    #         self.write({'ultima_ejecucion': ahora})
    #
    #         # Crear un nuevo entorno para la ejecución en hilo
    #         with self.pool.cursor() as cr:
    #             env = Environment(cr, self._uid, self._context)
    #             ficharesumen_obj = env['ficharesumen']
    #             try:
    #                 ficharesumen_obj.verificar_vencimiento()
    #             except Exception as e:
    #                 _logger.error(f"Error al ejecutar verificar_vencimiento: {e}")
    #
    #     # Crear y arrancar un hilo para ejecutar la función de verificación
    #     thread = threading.Thread(target=ejecutar_verificacion)
    #     thread.start()



    mcb_id3 = fields.Many2one('ejecucion', string="ejecucion relacionado", related="ejecucion2_ids.mcb_id4")

    def _activar_en_progreso_si_tiene_fc(self):
        for record in self:
            if record.coordinador_g != 'red':
                continue

            existe_fc = self.env['contrataciones_fuv'].search([
                ('proyecto_relacionado', '=', record.id)
            ], limit=1)
            if existe_fc:
                record.write({'coordinador_g': 'yellow'})
        return True

    def _informacion_base_para_contratacion(self):
        self.ensure_one()
        informacion_base = self.informacion_base_id or self.informacion_base_agregar_ids[:1]
        if not informacion_base:
            informacion_base = self.env['informacion_base_agregar'].search([
                '|',
                ('contratacion_id', '=', self.id),
                ('informacion_base_agregar_ids', '=', self.id),
            ], limit=1)
        return informacion_base

    def _es_proyecto_regalias_en_informacion_base(self):
        informacion_base = self._informacion_base_para_contratacion()
        return bool(informacion_base and informacion_base.area_solicitante2 == 'regalias')

    def _notificacion_contratacion_regalias_en_construccion(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Módulo en construcción',
                'message': (
                    'La contratación para proyectos de Regalías se encuentra '
                    'en construcción. No se realizó ningún cambio.'
                ),
                'type': 'warning',
                'sticky': False,
            },
        }

    def Contractual(self):
        if any(record._es_proyecto_regalias_en_informacion_base() for record in self):
            return self._notificacion_contratacion_regalias_en_construccion()

        # Actualiza la fecha de modificación
        self._marcar_apertura_si_permitido()
        self.check_estado_borrador()
        if not self._es_consulta_coordinacion_financiera():
            self._activar_en_progreso_si_tiene_fc()
        
        # Crear un nuevo programa de pagos y asociarlo al campo programa_pagos_id del modelo Ejecucion
        if not self._es_consulta_coordinacion_financiera():
            for ejecucion in self.ejecucion2_ids:
                # Verificar si ya existe un programa de pagos asociado en la Ejecucion
                if not ejecucion.programa_pagos_id:
                    # Si no existe, crear un nuevo programa de pagos
                    nuevo_programa = self.env['programapagos'].create({
                        'new_name': 'Pagos del proyecto',
                        'proyecto_relacionado': ejecucion.proyecto_relacionado.id,  # Asocia el proyecto a la Ejecucion
                    })
                    ejecucion.programa_pagos_id = nuevo_programa


        # Devolver una acción para ver el registro correspondiente al proyecto actual en formato formulario y árbol
        return {
            'type': 'ir.actions.act_window',
            'name': '2. Contratación',
            'res_model': 'contrataciones_fuv',
            'view_mode': 'kanban,tree,form,activity',
            'target': 'current',
            'domain': [('proyecto_relacionado', '=', self.id)],
            'context': {'default_proyecto_relacionado': self.id},
        }

    def Pre_contractual(self):
        self._marcar_apertura_si_permitido()  # Actualiza la fecha de modificación

        # Obtener los IDs de los registros relacionados
        related_ids = [record.id for record in self.ejecucion4_ids]
        # Ejecutar la función ir_a_NUEVA_MCB del modelo 'mcb'
        #self.env['mcb'].ir_a_NUEVA_MCB()

        return {
            'name': '1. Pre-contractual',
            'view_mode': 'kanban,form,tree',
            'res_model': 'mcb',  # Especificar el modelo 'mcb'
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('id', 'in', related_ids)],  # Filtrar por los IDs relacionados
        }

    def abrir_ficha_resumen_(self):
        self._marcar_apertura_si_permitido()

        # Obtener los IDs de los registros relacionados
        related_ids = [record.id for record in self.ejecucion5_ids]

        return {
            'name': 'Ficha Resumen',
            'view_mode': 'kanban,form,tree',
            'res_model': 'ficharesumen',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('id', 'in', related_ids)],  # Asegúrate de que este campo coincida con la relación correcta
        }

    tablero_id = fields.Many2one('tablero_avances', ondelete='cascade')

    def Ejecución(self):
        self._marcar_apertura_si_permitido()

        ejecucion_existente = self.env['ejecucion'].search([
            ('proyecto_relacionado2', '=', self.id),
        ], limit=1)

        if ejecucion_existente:
            if not self._es_consulta_coordinacion_financiera():
                ejecucion_existente.write({'new_name': '3. Ejecución'})
                ejecucion_existente.relacionar_tablero_con_ejecucion()  # Ejecutar función al abrir existente
            return {
                'name': 'Ejecución',
                'view_mode': 'form',
                'res_model': 'ejecucion',
                'res_id': ejecucion_existente.id,
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
        else:
            if self._es_consulta_coordinacion_financiera():
                return {
                    'name': 'Ejecución',
                    'view_mode': 'tree,form',
                    'res_model': 'ejecucion',
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                    'domain': [('proyecto_relacionado2', '=', self.id)],
                    'context': {'default_proyecto_relacionado2': self.id},
                }
            nueva_ejecucion = self.env['ejecucion'].create({
                'name': self.name if self.name else '3. Ejecución',
                'new_name': '3. Ejecución',
                'proyecto_relacionado2': self.id,
            })
            # Ejecutar relación automáticamente
            nueva_ejecucion.relacionar_tablero_con_ejecucion()

            return {
                'name': 'Ejecución',
                'view_mode': 'form',
                'res_model': 'ejecucion',
                'res_id': nueva_ejecucion.id,
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
 
    def Cierre(self):
        from odoo import fields # Importamos fields si no está en la cabecera

        # 1. Actualiza la fecha de modificación del registro actual (proyecto/contrataciones)
        self._marcar_apertura_si_permitido()

        # 2. Busca si ya existe un modelo 'cierre' relacionado con el proyecto actual
        ejecucion_existente = self.env['cierre'].search([
            ('proyecto_relacionado3', '=', self.id),
        ], limit=1)

        # Definimos la variable para el registro de 'cierre' (existente o nuevo)
        cierre_record = None

        if ejecucion_existente:
            # CASO 1: Ya existe
            cierre_record = ejecucion_existente
        else:
            if self._es_consulta_coordinacion_financiera():
                return {
                    'name': '4. Cierre',
                    'view_mode': 'tree,form',
                    'res_model': 'cierre',
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                    'domain': [('proyecto_relacionado3', '=', self.id)],
                    'context': {'default_proyecto_relacionado3': self.id},
                }
            # CASO 2: No existe, crear uno nuevo
            values = {
                'name': '4. Cierre',
                'proyecto_relacionado3': self.id,
            }
            cierre_record = self.env['cierre'].create(values)

        # --- Lógica AGREGADA para buscar y asignar la carpeta ---
        
        # 3. Solo si el campo de la carpeta (stage3_id) está vacío, la buscamos.
        #    Llamamos a la función _buscar_carpeta() en el contexto del registro de 'cierre'.
        if cierre_record and not cierre_record.stage3_id and not self._es_consulta_coordinacion_financiera():
            # Nota: La función _buscar_carpeta está definida en el modelo 'cierre'
            drive_stage = cierre_record._buscar_carpeta() 
            
            if drive_stage:
                # 4. Asignamos el ID de la carpeta encontrada al campo stage3_id del registro de 'cierre'
                cierre_record.write({'stage3_id': drive_stage.id})
                
        # --- FIN de la Lógica AGREGADA ---
        
        # 5. Devolver la acción (redirige al formulario del registro 'cierre')
        return {
            'name': '4. Cierre',
            'view_mode': 'form',
            'res_model': 'cierre',
            # Usamos el registro que se encontró o se creó
            'res_id': cierre_record.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def abrir_documento_(self):
        self._marcar_apertura_si_permitido()

        # Filtrar los registros relacionados por el proyecto actual
        related_docs = self.env['drive_folder'].search([('proyecto_relacionado', '=', self.id)])

        # Devolver una acción para mostrar los registros relacionados en una vista de árbol
        return {
            'name': 'Carpeta',
            'view_mode': 'kanban,tree,form',
            'res_model': 'drive_folder',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': [('proyecto_relacionado', '=', self.id)],  # Filtrar por el proyecto actual
            'context': {
                'default_proyecto_relacionado': self.id,  # Establecer el proyecto actual como valor predeterminado
                'search_default_proyecto_relacionado': self.id,
                # Establecer el proyecto como predeterminado en el filtro de búsqueda
            },
        }
    
    def abrir_drive_browser(self):
        self.ensure_one()
        browser = self.env['drive.browser'].sudo().get_or_create_browser(project_id=self.id, sync=False)
        if browser.sync_state == 'sincronizando':
            return {
                'name': 'Drive',
                'type': 'ir.actions.act_window',
                'res_model': 'drive.browser',
                'view_mode': 'form',
                'res_id': browser.id,
                'target': 'current',
                'flags': {'clear_breadcrumbs': True},
            }
        # Abrir instantáneo: reconstruir solo cuando el árbol está desactualizado
        try:
            needs_build = not browser.workspace_tree_ids
            if not needs_build:
                # Sin campos relacionales → viene de sync GDrive, reconstruir
                has_db_links = self.env['drive.browser.workspace'].sudo().search([
                    ('browser_id', '=', browser.id),
                    '|', ('drive_stage_id', '!=', False), ('drive_file_id', '!=', False),
                ], limit=1)
                if not has_db_links:
                    needs_build = True
            if not needs_build:
                # Detectar contratistas nuevos/eliminados comparando conteos
                stage_ids = self.env['drive_stage'].sudo().search([
                    ('proyecto_relacionado', '=', self.id)
                ]).ids
                if stage_ids:
                    df_count = self.env['drive.file'].sudo().search_count([
                        ('drive_id', 'in', stage_ids)
                    ])
                    ws_count = self.env['drive.browser.workspace'].sudo().search_count([
                        ('browser_id', '=', browser.id),
                        ('drive_file_id', '!=', False),
                    ])
                    if df_count != ws_count:
                        needs_build = True
            if needs_build:
                browser._sync_from_odoo_models()
            browser._refresh_entries_from_odoo_models()
            browser.write({'sync_state': 'completado'})
        except Exception as exc:
            _logger.warning("abrir_drive_browser DB sync error: %s", exc)

        return {
            'name': 'Drive',
            'type': 'ir.actions.act_window',
            'res_model': 'drive.browser',
            'view_mode': 'form',
            'res_id': browser.id,
            'target': 'current',
            'flags': {'clear_breadcrumbs': True},
        }

    def abrir_gestion_documental(self):
        self.ensure_one()
        Browser = self.env["gestion.documental.browser"].sudo().with_context(gd_sync=True)
        browser = Browser.search([("project_id", "=", self.id)], limit=1)
        if not browser:
            browser = Browser.create({"project_id": self.id})

        # Solo va a Drive si no hay carpetas cargadas todavía
        if not browser.workspace_tree_ids:
            browser._gd_sync_workspace_from_drive()
            browser._gd_refresh_entries()

        return {
            "name": "Gestión Documental",
            "type": "ir.actions.act_window",
            "res_model": "gestion.documental.browser",
            "res_id": browser.id,
            "view_mode": "form",
            "target": "current",
            "context": {'default_project_id': self.id},
        }


    #Sección de trazabilidad de cambios en users_with_access "Adaptable"
    @api.model
    def create(self, vals):
        self._bloquear_escritura_usuario_consulta()

        # 1. Crear el proyecto original en la base de datos
        record = super(Contratacion, self).create(vals)
        
        # 🔥 EL TRUCO: Obligamos a Odoo a escribir el nombre real en la base de datos ya mismo
        record.flush_recordset()
        
        # 2. Buscar usuarios automáticos de la matriz
        roles_tecnicos = ['Matriz.group_usuarios_principales']
        usuarios_a_agregar = []
        
        for xml_id in roles_tecnicos:
            grupo = self.env.ref(xml_id, raise_if_not_found=False)
            if grupo and grupo.users:
                for usuario in grupo.users:
                    if usuario.id not in record.users_with_access.ids:
                        usuarios_a_agregar.append(usuario.id)

        # 3. Vincular los usuarios automáticos
        if usuarios_a_agregar:
            # Ahora, cuando este write despierte al correo de tus compañeros,
            # el nombre real ya estará asentado y fijo en el sistema.
            record.write({
                'users_with_access': [(4, u_id) for u_id in set(usuarios_a_agregar)]
            })

        return record
    
    

    def write(self, vals):
            self._bloquear_escritura_usuario_consulta()

            # Detectar si se cambió users_with_access
            track_users = 'users_with_access' in vals
            track_drive_readers = 'drive_reader_user_ids' in vals

            old_users_map = {}
            old_drive_reader_ids = {}
            if track_users:
                for rec in self:
                    old_users_map[rec.id] = rec.users_with_access.ids
            if track_drive_readers:
                for rec in self:
                    old_drive_reader_ids[rec.id] = rec.drive_reader_user_ids.ids

            res = super(Contratacion, self).write(vals)

            if track_drive_readers:
                access_config = self.env['drive.global.access'].sudo().search([], limit=1)
                for rec in self:
                    if not rec.drive_folder_id:
                        rec._compute_drive_folder()
                    if not rec.drive_folder_id or not access_config:
                        continue
                    result = access_config.sync_project_readers(rec)
                    if result['errors']:
                        _logger.error(
                            'Errores al actualizar lectores de Drive del proyecto %s: %s',
                            rec.id,
                            '; '.join(result['errors']),
                        )
                    previous_ids = set(old_drive_reader_ids.get(rec.id, []))
                    removed_ids = previous_ids - set(rec.drive_reader_user_ids.ids)
                    if removed_ids:
                        access_config.revoke_project_readers(rec, removed_ids)

            if track_users:
                COLOR_PRINCIPAL = "#043484"  # Color principal para plantilla de correo

                for rec in self:
                    old_ids = set(old_users_map.get(rec.id, []))
                    new_ids = set(rec.users_with_access.ids)

                    added_ids = new_ids - old_ids
                    removed_ids = old_ids - new_ids

                    added_users = self.env['res.users'].browse(list(added_ids))
                    removed_users = self.env['res.users'].browse(list(removed_ids))

                    partes = []

                    if added_users:
                        nombres_agregados = ", ".join(added_users.mapped('name'))
                        partes.append(f"<b>Usuarios agregados:</b> {nombres_agregados}")

                    if removed_users:
                        nombres_eliminados = ", ".join(removed_users.mapped('name'))
                        partes.append(f"<b>Usuarios eliminados:</b> {nombres_eliminados}")

                    # Mensaje en el chatter
                    if partes:
                        body = "Actualización de usuarios con acceso al proyecto:<br/>" + "<br/>".join(partes)
                        rec.message_post(body=body)

                    # -----------------------------------------
                    # 🔔 ENVÍO DE CORREOS A LOS USUARIOS AFECTADOS
                    # -----------------------------------------
                    base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")

                    # 🔥 URL corregida para abrir el formulario del proyecto
                    formulario_url = f"/web#id={rec.id}&model=contratacion&view_type=form"
                    confirm_url = base_url + formulario_url

                    project_name = rec.display_name or "Proyecto"

                    # ================================
                    # CORREO PARA USUARIOS AGREGADOS
                    # ================================
                    for user in added_users:
                        if not user.email:
                            continue

                        mail_values = {
                            'subject': f"Acceso otorgado al proyecto {project_name}",
                            'email_to': user.email,
                            'body_html': f"""
                                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                                    <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                                        <h3 style="margin: 0; font-size: 18px;">👤 Acceso otorgado al proyecto</h3>
                                    </div>

                                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                                        <p>Cordial saludo, <strong>{user.name}</strong>:</p>

                                        <p>
                                            Se le ha otorgado acceso al siguiente proyecto:
                                        </p>

                                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid {COLOR_PRINCIPAL};">
                                            <strong>Proyecto:</strong> {project_name}
                                        </div>

                                        <p>Ahora cuenta con permisos para ingresar, consultar y operar dentro del sistema.</p>

                                        <p style="text-align: center; margin-top: 30px;">
                                            <a href='{confirm_url}'
                                                style="background-color: {COLOR_PRINCIPAL};
                                                color: white;
                                                padding: 10px 20px;
                                                text-decoration: none;
                                                border-radius: 5px;
                                                font-weight: bold;
                                                display: inline-block;">
                                                ➡ Abrir proyecto en SGO
                                            </a>
                                        </p>

                                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                                            Este es un mensaje automático de SGO.
                                        </p>
                                    </div>
                                </div>
                            """,
                        }
                        self.env['mail.mail'].create(mail_values).send()

                    # ================================
                    # CORREO PARA USUARIOS REMOVIDOS
                    # ================================
                    for user in removed_users:
                        if not user.email:
                            continue

                        mail_values = {
                            'subject': f"Acceso retirado del proyecto {project_name}",
                            'email_to': user.email,
                            'body_html': f"""
                                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">

                                    <div style="background-color: #8A0F0F; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                                        <h3 style="margin: 0; font-size: 18px;">🚫 Acceso retirado del proyecto</h3>
                                    </div>

                                    <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">

                                        <p>Cordial saludo, <strong>{user.name}</strong>:</p>

                                        <p>
                                            Su acceso al siguiente proyecto ha sido retirado:
                                        </p>

                                        <div style="margin: 15px 0; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #8A0F0F;">
                                            <strong>Proyecto:</strong> {project_name}
                                        </div>

                                        <p>
                                            Ya no cuenta con permisos para ingresar o consultar la información del proyecto.
                                        </p>

                                        <p style="margin-top: 10px;">
                                            Si considera que esto es un error, por favor comuníquese con el área encargada.
                                        </p>

                                        <p style="margin-top: 20px; font-size: 12px; color: #999;">
                                            Este es un mensaje automático de SGO.
                                        </p>
                                    </div>
                                </div>
                            """,
                        }
                        self.env['mail.mail'].create(mail_values).send()

            # Auto-renumeración cuando el proyecto pasa a Completado (coordinador_g = 'green')
            if vals.get('coordinador_g') == 'green':
                db_name = self.env.cr.dbname
                uid = self.env.uid
                for rec in self:
                    browser = self.env['drive.browser'].sudo().get_or_create_browser(
                        project_id=rec.id, sync=False
                    )
                    browser_id = browser.id

                    def _auto_renumber(bid=browser_id):
                        try:
                            with registry(db_name).cursor() as cr:
                                env = api.Environment(cr, uid, {})
                                b = env['drive.browser'].sudo().browse(bid)
                                b._sync_from_odoo_models()
                                root_ws = b.workspace_tree_ids.filtered(lambda w: not w.parent_id)
                                if root_ws:
                                    root_ws[0].write({'is_selected': True})
                                    b.write({'selected_workspace_id': root_ws[0].id})
                                b._refresh_entries_from_odoo_models()
                                b.do_renumber_bg()
                                cr.commit()
                        except Exception as exc:
                            _logger.warning("Auto-renumber (coordinador_g=green) error: %s", exc)

                    thread = threading.Thread(target=_auto_renumber, daemon=True)
                    self.env.cr.postcommit.add(thread.start)

            return res

    def unlink(self):
        self._bloquear_escritura_usuario_consulta()

        # Eliminar carpetas en drive_folder con el mismo nombre que new_name
        names_to_delete = self.mapped('new_name')
        if names_to_delete:
            drive_folder_records = self.env['drive_folder'].search([('folder_name', 'in', names_to_delete)])
            if drive_folder_records:
                drive_folder_records.unlink()

        # Find the related TABLA_DIRECTOR records based on the name field
        tabla_director_records = self.env['tablero_avances'].search([('name', 'in', self.mapped('new_name'))])
        if tabla_director_records:
            tabla_director_records.unlink()

        # Eliminate related records in informacion_base_agregar
        self.informacion_base_agregar_ids.unlink()

        # Eliminate related records in informacion_base_agregar
        self.informacion_base_id.unlink()

        # Eliminate related records in contrataciones_fuv
        self.contratacion_ids.unlink()

        # Eliminate related records in ejecucion
        self.ejecucion2_ids.unlink()

        # Eliminate related records in cierre
        self.ejecucion3_ids.unlink()

        # Eliminate related records in mcb
        self.ejecucion4_ids.unlink()

        # Eliminate related records in ficharesumen
        self.ejecucion5_ids.unlink()

        # Eliminate related records in tablero_avances
        self.tablero_avances_ids.unlink()

        # Call the parent method to delete the Contratacion record
        return super(Contratacion, self).unlink()

    @api.model
    def verificar_servidor(self):
        url = 'https://sgo.fundacionunivalle.com/'
        try:
            response = requests.get(url, verify=False)
            if response.status_code == 200:
                print("El servidor está en línea.", response.status_code)
            else:
                print("El servidor está caído.", response.status_code)
        except requests.exceptions.RequestException as e:
            print("Error al intentar conectar con el servidor:", e)

# 🚀 CONTROLADOR AUTOMÁTICO DE CORREOS DE DRIVE (EL VIGILANTE)
class DriveFolderSincronizador(models.Model):
    _inherit = 'drive_folder'

    def write(self, vals):
        res = super(DriveFolderSincronizador, self).write(vals)
        
        # En cuanto Google le asigna el ID a la carpeta, disparamos la sincronización
        if vals.get('folder_guardar_id') or 'folder_guardar_id' in vals:
            for record in self:
                if record.proyecto_relacionado and record.folder_guardar_id:
                    # 💡 EL SECRETO: Le pasamos el ID por tubo directo a la función de arriba
                    record.proyecto_relacionado._share_folder_with_new_users(force_folder_id=record.folder_guardar_id)
        return res

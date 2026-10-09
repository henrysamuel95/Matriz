from xmlrpc.client import Boolean

from odoo import fields, models, api, exceptions
from attr import exceptions
from googleapiclient.errors import HttpError
from odoo.exceptions import ValidationError  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from odoo.exceptions import AccessError  # type: ignore
import logging
_logger = logging.getLogger(__name__)



class NEWMCB(models.Model):
    _name = "new_mcb"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'MCB'
    name = fields.Char(string="Nombre", related="mcb_id.new_name")
    
    es_usuario_presupuesto = fields.Boolean(
    compute='_compute_es_usuario_presupuesto')
    es_usuario_consulta = fields.Boolean(
        compute='_compute_es_usuario_consulta')

    def _compute_es_usuario_presupuesto(self):
        for record in self:
            record.es_usuario_presupuesto = self.env.user.has_group('Matriz.group_presupuestos')

    def _compute_es_usuario_consulta(self):
        es_consulta = self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera')
        for record in self:
            record.es_usuario_consulta = es_consulta
            
            
    def _check_security_year_2026(self):
        """
        Valida que solo el rol Financiero pueda modificar registros 
        vinculados a una Contratación del año 2026 en adelante.
        """
        # Presupuestos y Dirección Financiera conservan el permiso funcional.
        # Soporte también puede modificar para ejecutar reparaciones controladas.
        is_financiero = (
            self.env.user.has_group('Matriz.group_presupuestos')
            or self.env.user.has_group('Matriz.group_contratacion_director_financiero')
            or self.env.user.has_group('Matriz.group_soporte')
        )

        for record in self:
            # Apuntamos al modelo padre 'contratacion' a través de mcb_id
            parent_record = record.mcb_id
            
            # Extraemos el año de la fch_creacion del modelo padre
            if parent_record and parent_record.fch_creacion:
                fecha_obj = fields.Datetime.to_datetime(parent_record.fch_creacion)
                year = fecha_obj.year
            else:
                # Si no hay fecha o padre, asumimos 2025 para permitir el paso
                year = 2025

            # Depuración en consola
            _logger.info("*** CHECK SEGURIDAD *** ID: %s | Año Contratación: %s | Financiero: %s", record.id, year, is_financiero)
            # Validación de acceso
            if year >= 2026 and not is_financiero:
                raise UserError(
                    f"Acceso Denegado: La Contratación vinculada es del año {year}. "
                    "Solo Presupuestos, Director Financiero o Soporte pueden realizar "
                    "cambios en registros de 2026 en adelante."
                )

    def action_soporte_recalcular_mcb(self):
        """
        Recalcula exclusivamente los totales de esta MCB.

        Se mantiene separado del botón de Contrataciones FUV para que Soporte
        decida cuál capa necesita reparar y pueda verificar el resultado antes
        de actualizar los saldos de contratación.
        """
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError("Solo el grupo Soporte puede recalcular la MCB.")

        self._compute_contratos_vco_otrosi_suma()
        self._calculate_contratos_vco_resumen()

        # Normalizar primero cada origen tributario (base, MCB anterior y
        # otrosí) y después persistir nuevamente el total consolidado.
        self._compute_total_iva_admon()
        self._compute_IVA_ADMON_vaor_rc()
        self._compute_suma_IVA_ADMON_vaor()
        self._compute_reteica_result_r()
        self._compute_reteica_result_rc()
        self._compute_suma_RETEICA_valor()
        self._compute_estampillas_r()
        self._compute_estampillas_rc()
        self._compute_suma_Descuentos_Estampillas_valorR()
        self._compute_retefuente_r()
        self._compute_retefuente_rc()
        self._compute_suma_cuatro_X1000_valorR()
        self._compute_avisos_tableros_r()
        self._compute_avisos_tableros_rc()
        self._compute_suma_ICA_AVISOS_Y_TABLEROS_valorR()
        self._compute_gastos_bancarios_otrosi()
        self._compute_gastos_bancarios_otrosi_general()
        self._compute_personal_fuv_otrosi()
        self._compute_personal_fuv_general()
        self._compute_costos_total_valor2R()

        self._compute_costos_fuv_r()
        self._compute_suma_costos_fuv()
        self._compute_new_total_costos_produccion_N_r()
        self._compute_new_total_costos_produccionin_pol_fuv()

        if self.mcb_id:
            self.mcb_id._compute_new_total_costos_produccion_N_r()
            self.mcb_id._compute_new_total_costos_produccionin_pol_fuv()

        _logger.info(
            "[SOPORTE-MCB] Recalculada new_mcb_id=%s mcb_id=%s por user_id=%s",
            self.id,
            self.mcb_id.id if self.mcb_id else False,
            self.env.user.id,
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'MCB recalculada',
                'message': (
                    'Se normalizaron los acumulados tributarios, pólizas, costos FUV '
                    'y costos de producción de la MCB. Los saldos de Contrataciones '
                    'FUV se actualizan desde su botón independiente.'
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    otrosi_ids_new = fields.One2many('new_mcb_otrosi', 'new_mcb_id', string="Otrosí relacionados") #Relacción con new_mcb_otrosi
    contratos_vco = fields.Float(string='Contratos VCO', tracking=True)
    
    #Mapeo los valores del campo contratos que esta en otrosis para sumarlos en este campo nuevo contratos_vco_otrosi_suma
    contratos_vco_otrosi_suma = fields.Float(string='Contratos VCO', compute='_compute_contratos_vco_otrosi_suma', store=True)
    @api.depends('otrosi_ids_new.contratos_vco_otrosi')
    def _compute_contratos_vco_otrosi_suma(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('contratos_vco_otrosi'))
            record.contratos_vco_otrosi_suma = total
    
    # Campo calculado que suma contratos_vco y contratos_vco_otrosi_suma para mostrar un total en la vista resumen de la MCB
    contratos_vco_resumen = fields.Float(string='Contratos VCO', compute='_calculate_contratos_vco_resumen', store=True)
    @api.depends('contratos_vco', 'contratos_vco_otrosi_suma')
    def _calculate_contratos_vco_resumen(self):
        for record in self:
            record.contratos_vco_resumen = record.contratos_vco + record.contratos_vco_otrosi_suma
            

    fecha_finalizacion_otrosi = fields.Date(
            string="Fecha de finalización", 
            compute='_compute_fecha_finalizacion_otrosi', 
            store=True,
            tracking=True
        )

    tiene_fecha_finalizacion_otrosi = fields.Boolean(string="Tiene fecha de finalización en otrosí", compute="_compute_tiene_fecha_finalizacion_otrosi")
    acta_comite_ids = fields.Many2many(
        'drive',
        'new_mcb_acta_comite_drive_rel',
        'new_mcb_id',
        'drive_id',
        string='Acta comité',
        tracking=True,
        copy=False,
    )
    acta_comite_count = fields.Integer(
        string='Actas comité',
        compute='_compute_acta_comite_count',
    )

    def _compute_acta_comite_count(self):
        for record in self:
            drive_ids = set(record.acta_comite_ids.ids)
            if record.id:
                try:
                    drive_ids.update(self.env['drive'].sudo().search([
                        ('new_mcb_acta_comite_id', '=', record.id),
                    ]).ids)
                except Exception as err:
                    _logger.warning(
                        "No se pudo contar Acta comité por campo auxiliar para new_mcb %s: %s",
                        record.id,
                        err,
                    )
            record.acta_comite_count = len(drive_ids)

    def _get_acta_comite_stage(self):
        self.ensure_one()
        project = self.mcb_id.contrataciones_id if self.mcb_id else False
        carpeta_principal = self.mcb_id.carpeta_principal_id if self.mcb_id else False

        if not project:
            raise UserError("Para cargar documentos, primero vincule la MCB a un proyecto.")

        def _is_main_folder(stage):
            raw_name = (stage.folder_name or stage.name or '').upper()
            normalized = ''.join(ch for ch in raw_name if ch.isalnum())
            return normalized in ('1CARPETAPRINCIPAL', 'CARPETAPRINCIPAL')

        if carpeta_principal:
            if not carpeta_principal.stage3_id:
                drive_stage = carpeta_principal._buscar_carpeta()
                if drive_stage:
                    carpeta_principal.write({'stage3_id': drive_stage.id})
            if carpeta_principal.stage3_id:
                stage = carpeta_principal.stage3_id
                linked_projects = stage.proyecto_relacionado | stage.contrataciones_carpeta_id
                if not _is_main_folder(stage) or linked_projects != project:
                    raise UserError(
                        "La Carpeta Principal vinculada no corresponde a este proyecto. "
                        "Revise su ubicación antes de cargar los documentos."
                    )
                return stage

        if project:
            stage = self.env['drive_stage'].sudo().browse()
            stages = self.env['drive_stage'].sudo().search([
                ('proyecto_relacionado', '=', project.id),
            ], order='sequence, id')
            for candidate in stages:
                if _is_main_folder(candidate):
                    stage = candidate
                    break
            if stage:
                if carpeta_principal and not carpeta_principal.stage3_id:
                    carpeta_principal.write({'stage3_id': stage.id})
                return stage

        raise UserError(
            "No se encontró la carpeta 1. CARPETA PRINCIPAL del proyecto relacionado. "
            "Abra primero la carpeta principal del proyecto para crear o vincular su ubicación en Drive."
        )

    def action_upload_acta_comite(self):
        self.ensure_one()
        if self.env.user.has_group('Matriz.group_consulta_coordinacion_financiera'):
            raise AccessError("El rol Usuario de consulta solo puede ver las actas de comite.")
        stage = self._get_acta_comite_stage()
        project = self.mcb_id.contrataciones_id if self.mcb_id else False
        if not project:
            raise UserError("No se encontró el proyecto relacionado para subir el Acta comité.")

        browser = self.env['drive.browser'].sudo().get_or_create_browser(project_id=project.id, sync=False)
        Workspace = self.env['drive.browser.workspace'].sudo()
        workspace = Workspace.search([
            ('browser_id', '=', browser.id),
            ('drive_stage_id', '=', stage.id),
        ], limit=1)
        if not workspace and (stage.folder_id or stage.google_drive_id):
            workspace = Workspace.search([
                ('browser_id', '=', browser.id),
                ('folder_google_id', '=', stage.folder_id or stage.google_drive_id),
            ], limit=1)
        if not workspace and (stage.folder_id or stage.google_drive_id):
            workspace = Workspace.create({
                'browser_id': browser.id,
                'name': stage.folder_name or stage.name or '1. CARPETA PRINCIPAL',
                'drive_stage_id': stage.id,
                'folder_google_id': stage.folder_id or stage.google_drive_id,
            })
        if not workspace:
            raise UserError("No se encontró la carpeta 1. CARPETA PRINCIPAL en el navegador de Drive.")

        return {
            'name': 'Subir a Drive',
            'type': 'ir.actions.act_window',
            'res_model': 'drive.browser.upload',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_browser_id': browser.id,
                'default_workspace_id': workspace.id,
                'default_upload_mode': 'file|new_mcb_acta_comite:%s' % self.id,
                'default_new_mcb_acta_comite_id': self.id,
            },
        }

    def action_open_acta_comite(self):
        self.ensure_one()
        drive_ids = set(self.acta_comite_ids.ids)
        try:
            drive_ids.update(self.env['drive'].sudo().search([
                ('new_mcb_acta_comite_id', '=', self.id),
            ]).ids)
        except Exception as err:
            _logger.warning(
                "No se pudo abrir Acta comité por campo auxiliar para new_mcb %s: %s",
                self.id,
                err,
            )
        return {
            'name': 'Acta comité',
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'view_mode': 'kanban,tree,form',
            'domain': [('id', 'in', list(drive_ids))],
            'context': {'create': False},
            'target': 'current',
        }
    
    @api.depends('otrosi_ids_new.fecha_finalizacion')
    def _compute_tiene_fecha_finalizacion_otrosi(self):
        for record in self:
            record.tiene_fecha_finalizacion_otrosi = bool(record.otrosi_ids_new.filtered(lambda o: o.fecha_finalizacion))
    
    @api.depends('otrosi_ids_new.fecha_finalizacion')
    def _compute_fecha_finalizacion_otrosi(self):
        for record in self:
            # 1. Obtenemos las fechas de todos los hijos (otrosi_ids_new)
            fechas = record.otrosi_ids_new.mapped('fecha_finalizacion')
            
            # 2. Limpiamos valores vacíos
            fechas_validas = [f for f in fechas if f]
            
            # 3. Elegimos la mayor
            if fechas_validas:
                maxima = max(fechas_validas)
                record.fecha_finalizacion_otrosi = maxima
                print(f"✅ [NEW_MCB] Padre {record.id} actualizado a fecha: {maxima}")
            else:
                record.fecha_finalizacion_otrosi = False



    mcb_id = fields.Many2one('mcb', string='Registro MCB anterior')
    new_mcb_id3 = fields.Many2one('contratacion', string='Contratacion')
    new_actividad_ids = fields.One2many('tablero_avances', 'new_ejecucion_id', string='Tablero control')
    fch_creacion = fields.Datetime(string='Fecha creación', copy=False, default=lambda self: fields.Datetime.now(),
                                   tracking=True)
    write_date = fields.Datetime(string='Fecha de Modificación', readonly=True, tracking=True)

    costo_operativoR = fields.Monetary(string="Valor del costo operativo", related="otrosi_ids_new.costo_operativo1", store=True)
    costo_operativo_porc = fields.Char(
        string='% costo operativo', related="mcb_id.Admon_C_12",
        store=True, tracking=True, readonly=False  # ← También editable
    )

    mcb_ids = fields.One2many(related="mcb_id.mcb_ids")

    # Nueva sección para ué el Director asigne el sobrecosto aprobado manualmente
    aprueba_director = fields.Boolean(string='Aprueba sobrecosto', tracking=True, related='mcb_id.aprueba_director', readonly=False)
    cantidad_aprobada = fields.Float(string='Asignar sobrecosto', tracking=True, related='mcb_id.cantidad_aprobada', readonly=False)



    realizado_por = fields.Char(string="Realizado por", default="Diana Cano", tracking=True)
    aprobado_por = fields.Char(string="Aprobado por", default="Katherine Cano", tracking=True)
    realizado_por_check = fields.Boolean(string="Elaborado por", tracking=True)
    aprobado_por_check = fields.Boolean(string="Aprobado por", tracking=True)
    
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'realizado_por' in fields_list:
            res['realizado_por'] = 'Diana Cano'
        if 'aprobado_por' in fields_list:
            res['aprobado_por'] = 'Katherine Cano'
        return res

    costos_total_valor = fields.Monetary(tracking=True)

    ICA_AVISOS_Y_TABLEROS_valor = fields.Monetary(tracking=True)


    Descuentos_Estampillas_valor = fields.Monetary(string="IVA ADMON", tracking=True)
    retefuente = fields.Monetary(string="IVA ADMON", tracking=True)
    currency_id = fields.Many2one('res.currency', string='Moneda')

    valor_proyecto_convenio3 = fields.Float(related='mcb_id.valor_proyecto_convenio3', readonly=False, store=True, tracking=True)
    valor_proyecto_convenio3_related = fields.Float(related='valor_proyecto_convenio3')



    modalidad_id = fields.Many2one('modalidad_contratacion', string='Modalidad de Contratación', related='mcb_id.modalidad_id', tracking=True)
    fecha_inicio = fields.Date(string="Fecha de inicio", related='mcb_id.fecha_inicio', tracking=True)
    fecha_finalizacion = fields.Date(string="Fecha de finalización", related='mcb_id.fecha_finalizacion2', tracking=True)
    numero_contrato = fields.Char(string="Número de contrato", related='mcb_id.numero_contrato', tracking=True)
    centro_costo = fields.Char(string="Centro de costo", tracking=True, related='mcb_id.centro_costo', readonly=False)
    entidad_contratante = fields.Char(string="Entidad contratante", tracking=True, related='mcb_id.entidad_contratante', readonly=False)

    objeto_proyecto = fields.Char(string="Objeto del proyecto", related='mcb_id.objeto_proyecto', readonly=False)
    
    objeto_proyecto_texto2 = fields.Text(
        string='Objeto del proyecto (Texto)', 
        compute='_compute_objeto_proyecto_texto2', 
        inverse='_inverse_objeto_proyecto_texto2',
        store=True
    )

    @api.depends('mcb_id.objeto_proyecto')
    def _compute_objeto_proyecto_texto2(self):
        for record in self:
            record.objeto_proyecto_texto2 = record.objeto_proyecto

    def _inverse_objeto_proyecto_texto2(self):
        for record in self:
            record.objeto_proyecto = record.objeto_proyecto_texto2
            
            
            
    adiciones_new2 = fields.Float(string='Adición', tracking=True)
    RETEICA_valor = fields.Monetary(tracking=True)




    gastos_financieros = fields.Float(string='Gastos financieros', tracking=True)
    valor_total = fields.Float(string='Valor total', tracking=True)
    solicitante = fields.Many2one('res.users', string='Creador del proyecto', readonly=True, related="mcb_id.create_uid")

    costo_operativo_porc_str = fields.Char(
        string='Porcentaje Operativo (%)',
        compute='_compute_costo_operativo_str',
    )

    @api.depends('costo_operativo_porc')
    def _compute_costo_operativo_str(self):
        for record in self:
            record.costo_operativo_porc_str = f"%{record.costo_operativo_porc:.1f}" if record.costo_operativo_porc else "%0.0"

    alias_costo = fields.Selection([
        ('gastos_admin', 'Costo administrativo'),
        ('costo_operativo', 'Costo operativo'),
        ('amd_fuv', 'Administración FUV'),
    ], string="Seleccionar", required=False)

    costo_operativo1 = fields.Float(
        string='Costo operativo',
        related="mcb_id.Admon_12",
        store=True,  # Debe ser True para que funcione la edición
        tracking=True,
        readonly=False
    )
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # Nuevos campos nueva MCB
    new_fuv_programas = fields.Monetary(tracking=True, related='mcb_id.fuv_programas', readonly=False)
    new_fuv_programas_r = fields.Monetary(compute="_compute_new_fuv_programas_r", store=True)
    @api.depends('mcb_ids.fuv_programas')
    def _compute_new_fuv_programas_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('fuv_programas'))
            record.new_fuv_programas_r = total

    # Campo calculado 2 condicional
    new_fuv_programas_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_new_fuv_programas_rc", store=True)
    @api.depends('otrosi_ids_new.new_fuv_programas')
    def _compute_new_fuv_programas_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('new_fuv_programas'))
            record.new_fuv_programas_rc = total

    suma_new_fuv_programas = fields.Monetary(string="IVA", compute="_compute_suma_new_fuv_programas")
    @api.onchange('new_fuv_programas_r', 'new_fuv_programas_rc', 'new_fuv_programas')
    def _compute_suma_new_fuv_programas(self):
        for record in self:
            base = record.new_fuv_programas_r if record.new_fuv_programas_r else record.new_fuv_programas_rc
            record.suma_new_fuv_programas = base + record.new_fuv_programas

            
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    new_Participación_UV = fields.Monetary(tracking=True, related='mcb_id.Participación_UV', readonly=False)
    new_Participación_UV_r = fields.Monetary(compute="_compute_new_Participación_UV_r", store=True)
    @api.depends('mcb_ids.Participación_UV')
    def _compute_new_Participación_UV_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('Participación_UV'))
            record.new_Participación_UV_r = total

    # Campo calculado 2 condicional
    new_Participación_UV_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_new_Participación_UV_rc", store=True)
    @api.depends('otrosi_ids_new.new_Participación_UV')
    def _compute_new_Participación_UV_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('new_Participación_UV'))
            record.new_Participación_UV_rc = total

    suma_new_Participación_UV = fields.Monetary(compute="_compute_suma_new_Participación_UV")
    @api.onchange('new_Participación_UV_r', 'new_Participación_UV_rc', 'new_Participación_UV')
    def _compute_suma_new_Participación_UV(self):
        for record in self:
            base = record.new_Participación_UV_r if record.new_Participación_UV_r else record.new_Participación_UV_rc
            record.suma_new_Participación_UV = base + record.new_Participación_UV
            
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # Campo calculado
    costo_operativo1_r = fields.Monetary(string="Costo operativo", compute="_compute_costo_operativo1_r", store=True)
    @api.depends('mcb_ids.Admon_12')
    def _compute_costo_operativo1_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('Admon_12'))
            record.costo_operativo1_r = total
    # Campo calculado 2 condicional
    costo_operativo1_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_costo_operativo1_rc", store=True)

    @api.depends('otrosi_ids_new.costo_operativo1')
    def _compute_costo_operativo1_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('costo_operativo1'))
            record.costo_operativo1_rc = total

    suma_costo_operativo1 = fields.Monetary(compute="_compute_suma_costo_operativo1")
    @api.onchange('costo_operativo1_r', 'costo_operativo1_rc', 'costo_operativo1')
    def _compute_suma_costo_operativo1(self):
        for record in self:
            base = record.costo_operativo1_r if record.costo_operativo1_r else record.costo_operativo1_rc
            record.suma_costo_operativo1 = base + record.costo_operativo1

    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    porc_iva = fields.Float(string='% Iva', digits=(5, 1))
    # Campo principal
    IVA_ADMON_vaor = fields.Monetary(tracking=True, related="mcb_id.IVA_ADMON_vaor", readonly=False, store=True)
    # Campo calculado
    IVA_ADMON_vaor_r = fields.Monetary(string="Total IVA Administración", compute="_compute_total_iva_admon", store=True)

    @api.depends('mcb_ids.IVA_ADMON_vaor')
    def _compute_total_iva_admon(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('IVA_ADMON_vaor'))
            record.IVA_ADMON_vaor_r = total

    # Campo calculado 2 condicional
    IVA_ADMON_vaor_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_IVA_ADMON_vaor_rc", store=True)

    @api.depends('otrosi_ids_new.IVA_ADMON_vaor')
    def _compute_IVA_ADMON_vaor_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('IVA_ADMON_vaor'))
            record.IVA_ADMON_vaor_rc = total
    ############################################# Deducir IVA + otrosí #################################################
    # Campo calculado
    suma_IVA_ADMON_vaor = fields.Monetary(string="IVA", compute="_compute_suma_IVA_ADMON_vaor")

    @api.depends('IVA_ADMON_vaor_r', 'IVA_ADMON_vaor_rc', 'IVA_ADMON_vaor')
    def _compute_suma_IVA_ADMON_vaor(self):
        for record in self:
            base = record.IVA_ADMON_vaor_r if record.IVA_ADMON_vaor_r else record.IVA_ADMON_vaor_rc
            record.suma_IVA_ADMON_vaor = base + record.IVA_ADMON_vaor



    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # Campo principal
    contrapartida4 = fields.Monetary(string="Contrapartida (FUV)", readonly=False, store=True, related="mcb_id.contrapartida4")
    contrapartida_otros = fields.Monetary(string="Contrapartida (OTROS)")
    suma_contrapartida_otros = fields.Monetary(string="Contrapartida total", compute="_compute_suma_contrapartida_otros")
    
    @api.onchange('suma_contrapartida4', 'contrapartida_otros', 'contrapartida_otros_otrosi')
    def _compute_suma_contrapartida_otros(self):
        for record in self:
            record.suma_contrapartida_otros = record.suma_contrapartida4 + record.contrapartida_otros + record.contrapartida_otros_otrosi
            
    # Campo calculado 2 condicional
    contrapartida_otros_otrosi = fields.Monetary(compute="_compute_contrapartida_otros_otrosi")

    @api.depends('otrosi_ids_new.contrapartida_otros', 'suma_contrapartida4', 'contrapartida4', 'contrapartida_otros')
    def _compute_contrapartida_otros_otrosi(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('contrapartida_otros'))
            record.contrapartida_otros_otrosi = total
            
            
    contrapartida_otros_r = fields.Monetary(compute="_compute_contrapartida_otros_r")
    @api.onchange('suma_contrapartida4', 'contrapartida_otros', 'contrapartida_otros_otrosi')
    def _compute_contrapartida_otros_r(self):
        for record in self:
            record.contrapartida_otros_r = record.contrapartida_otros_otrosi + record.contrapartida_otros
        
            
                   
    
    # Campo calculado
    contrapartida4r = fields.Monetary(compute="_compute_total_contrapartida4r", store=True)
    @api.depends('mcb_ids.contrapartida2')
    def _compute_total_contrapartida4r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('contrapartida2'))
            record.contrapartida4r = total

    # Campo calculado 2 condicional
    contrapartida4rc = fields.Monetary(compute="_compute_contrapartida4rc", store=True)

    @api.depends('otrosi_ids_new.contrapartida4')
    def _compute_contrapartida4rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('contrapartida4'))
            record.contrapartida4rc = total

    suma_contrapartida4 = fields.Monetary(compute="_compute_suma_contrapartida4")

    @api.onchange('contrapartida4r', 'contrapartida4rc', 'contrapartida4')
    def _compute_suma_contrapartida4(self):
        for record in self:
            base = record.contrapartida4r if record.contrapartida4r else record.contrapartida4rc
            record.suma_contrapartida4 = base + record.contrapartida4

    #▓▓######▓▓######▓▓#######▓▓▓######▓▓▓#######▓▓▓#######▓▓▓########▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓#▓▓▓▓#▓▓#▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓"▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓#####▓▓▓######▓▓▓▓▓#▓▓▓▓▓▓######▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓########▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓#▓▓▓#▓▓▓#▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓"▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓#▓▓▓##▓▓######▓▓▓▓▓#▓▓▓▓▓▓######▓▓▓#######▓▓▓#######▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    porc_reteica = fields.Float(string='% Reteica', digits=(5, 1))
    # Campo principal
    reteica_result = fields.Monetary(string='Valor Reteica', store=True, tracking=True, related = "mcb_id.RETEICA_valor", readonly=False)
    # Campo calculado
    reteica_result_r = fields.Monetary(string="Total IVA Administración", compute="_compute_reteica_result_r", store=True)

    @api.depends('mcb_ids.RETEICA_valor')
    def _compute_reteica_result_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('RETEICA_valor'))
            record.reteica_result_r = total

    # Campo calculado 2 condicional
    reteica_result_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_reteica_result_rc", store=True)

    @api.depends('otrosi_ids_new.reteica_result')
    def _compute_reteica_result_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('reteica_result'))
            record.reteica_result_rc = total
    ############################################# Deducir suma total reteica + otrosí ##################################
    # Campo calculado
    suma_RETEICA_valor = fields.Monetary(string="Reteica", compute="_compute_suma_RETEICA_valor")
    @api.depends('reteica_result_r', 'reteica_result', 'reteica_result_rc')
    def _compute_suma_RETEICA_valor(self):
        for record in self:
            base = record.reteica_result_r if record.reteica_result_r else record.reteica_result_rc
            record.suma_RETEICA_valor = base + record.reteica_result

    #######▓▓▓########▓▓########▓▓########▓▓##▓▓▓▓▓▓##▓▓########▓▓########▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓#########▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓#▓#▓▓▓▓#▓#▓▓#▓▓▓▓▓▓#▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #######▓▓▓########▓▓▓▓▓#▓▓▓▓▓▓########▓▓#▓▓#▓▓#▓▓#▓▓########▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓#########▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓#▓▓▓##▓▓▓#▓▓#▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #######▓▓▓########▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓#▓▓▓▓▓▓▓▓#▓▓#▓▓▓▓▓▓▓▓▓########▓▓#######▓▓########▓▓#▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    porc_estampillas = fields.Float(string='% Estampillas', digits=(5, 1))
    # Campo principal
    estampillas_result = fields.Monetary(string='Valor Estampillas', store=True, tracking=True, related="mcb_id.Descuentos_Estampillas_valor", readonly=False)
    # Campo calculado
    estampillas_r = fields.Monetary(string="Total IVA Administración", compute="_compute_estampillas_r", store=True)

    @api.depends('mcb_ids.Descuentos_Estampillas_valor')
    def _compute_estampillas_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('Descuentos_Estampillas_valor'))
            record.estampillas_r = total

    # Campo calculado 2 condicional
    estampillas_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_estampillas_rc", store=True)
    @api.depends('otrosi_ids_new.estampillas_result')
    def _compute_estampillas_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('estampillas_result'))
            record.estampillas_rc = total

    ############################################# Deducir Estampillas + otrosí #########################################
    suma_Descuentos_Estampillas_valor = fields.Monetary(string="IVA", compute="_compute_suma_Descuentos_Estampillas_valorR")
    @api.depends('estampillas_r', 'estampillas_result', 'estampillas_rc')
    def _compute_suma_Descuentos_Estampillas_valorR(self):
        for record in self:
            base = record.estampillas_r if record.estampillas_r else record.estampillas_rc
            record.suma_Descuentos_Estampillas_valor = base + record.estampillas_result
    ######▓▓▓▓######▓▓▓▓##▓▓▓▓▓##▓▓▓######▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓#▓▓▓▓#▓▓▓▓▓▓▓▓▓##▓▓▓▓▓##▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    ######▓▓▓▓######▓▓▓▓##▓▓▓▓▓##▓▓▓######▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓#▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓##▓▓▓##▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓##▓#▓▓#▓▓▓▓▓▓▓▓▓▓▓#####▓▓▓▓▓######▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    porc_retefuente = fields.Float(string='% Retefuente', digits=(5, 1))
    # Campo principal
    reteica_retefuente = fields.Monetary(string='Valor Retefuente', store=True, tracking=True, related="mcb_id.cuatro_X1000_valor", readonly=False)
    # Campo calculado
    retefuente_r = fields.Monetary(string="Total IVA Administración", compute="_compute_retefuente_r", store=True)

    @api.depends('mcb_ids.cuatro_X1000_valor')
    def _compute_retefuente_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('cuatro_X1000_valor'))
            record.retefuente_r = total

    # Campo calculado 2 condicional
    retefuente_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_retefuente_rc", store=True)
    @api.depends('otrosi_ids_new.reteica_retefuente')
    def _compute_retefuente_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('reteica_retefuente'))
            record.retefuente_rc = total
    ############################################# Deducir retefuente + otrosí #########################################
    suma_cuatro_X1000_valor = fields.Monetary(string="IVA", compute="_compute_suma_cuatro_X1000_valorR")
    @api.depends('retefuente_r', 'reteica_retefuente', 'retefuente_rc')
    def _compute_suma_cuatro_X1000_valorR(self):
        for record in self:
            base = record.retefuente_r if record.retefuente_r else record.retefuente_rc
            record.suma_cuatro_X1000_valor = base + record.reteica_retefuente
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    porc_avisos_tableros = fields.Float(string='% Avisos y Tableros', digits=(5, 1))
    # Campo principal
    resul_avisos_tableros = fields.Monetary(string='Valor avisos y tableros', store=True, tracking=True, related="mcb_id.ICA_AVISOS_Y_TABLEROS_valor", readonly=False)
    # Campo calculado
    avisos_tableros_r = fields.Monetary(string="Total IVA Administración", compute="_compute_avisos_tableros_r", store=True)
    @api.depends('mcb_ids.ICA_AVISOS_Y_TABLEROS_valor')
    def _compute_avisos_tableros_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('ICA_AVISOS_Y_TABLEROS_valor'))
            record.avisos_tableros_r = total

    # Campo calculado 2 condicional
    avisos_tableros_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_avisos_tableros_rc", store=True)
    @api.depends('otrosi_ids_new.resul_avisos_tableros')
    def _compute_avisos_tableros_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('resul_avisos_tableros'))
            record.avisos_tableros_rc = total

    ############################################# Deducir retefuente + otrosí ##########################################
    suma_ICA_AVISOS_Y_TABLEROS_valor = fields.Monetary(string="IVA", compute="_compute_suma_ICA_AVISOS_Y_TABLEROS_valorR")
    @api.depends('avisos_tableros_r', 'resul_avisos_tableros', 'avisos_tableros_rc')
    def _compute_suma_ICA_AVISOS_Y_TABLEROS_valorR(self):
        for record in self:
            base = record.avisos_tableros_r if record.avisos_tableros_r else record.avisos_tableros_rc
            record.suma_ICA_AVISOS_Y_TABLEROS_valor = base + record.resul_avisos_tableros






    # ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓######▓▓▓######▓▓▓#▓▓▓▓▓▓▓▓#######▓▓▓▓########▓▓▓▓#######▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓#▓▓▓#▓▓▓▓#▓▓▓#▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓##▓▓▓▓▓#▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓######▓▓▓#▓▓▓▓#▓▓▓#▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓##▓▓▓▓▓▓#######▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓#▓▓▓#▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓##▓▓▓▓▓▓▓#▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓######▓▓▓######▓▓▓#######▓▓▓▓▓#######▓▓▓▓#▓▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # Campo principal
    poliza_cumplim = fields.Monetary(string='Póliza de cumplimiento', tracking=True, related='mcb_id.polizas', readonly=False)
    # Campo calculado
    poliza_cumplim_r = fields.Monetary(string="Total IVA Administración", compute="_compute_poliza_cumplim_r", store=True)
    @api.depends('mcb_ids.polizas')
    def _compute_poliza_cumplim_r(self):
        for record in self:
            total = sum(record.mcb_ids.mapped('polizas'))
            record.poliza_cumplim_r = total

    # Campo calculado 2 condicional
    poliza_cumplim_rc = fields.Monetary(string="Total IVA Administración", compute="_compute_poliza_cumplim_rc", store=True)
    @api.depends('otrosi_ids_new.poliza_cumplim')
    def _compute_poliza_cumplim_rc(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('poliza_cumplim'))
            record.poliza_cumplim_rc = total
    # Suma condicional sí tiene valor algunas de las dos fuentes la old o new
    suma_poliza_cumplim = fields.Monetary(string="IVA", compute="_compute_suma_poliza_cumplim")
    @api.onchange('poliza_cumplim_r', 'poliza_cumplim', 'poliza_cumplim_rc')
    def _compute_suma_poliza_cumplim(self):
        for record in self:
            base = record.poliza_cumplim_r if record.poliza_cumplim_r else record.poliza_cumplim_rc
            record.suma_poliza_cumplim = base + record.poliza_cumplim
    ####################################################################################################################
    ####################################################################################################################
    # Campo principal
    poliza_respon = fields.Float(string='Póliza de responsabilidad', tracking=True)
    # Campo calculado
    poliza_respon_r = fields.Monetary(string="Total IVA Administración", compute="_compute_poliza_respon_r", store=True)
    @api.depends('otrosi_ids_new.poliza_respon')
    def _compute_poliza_respon_r(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('poliza_respon'))
            record.poliza_respon_r = total
    ############################################# Deducir poliza_cumplim + otrosí ##########################################
    suma_poliza_respon = fields.Monetary(string="IVA", compute="_compute_suma_poliza_respon_r")
    @api.depends('poliza_respon_r', 'poliza_respon')
    def _compute_suma_poliza_respon_r(self):
        for record in self:
            record.suma_poliza_respon = (
                    record.poliza_respon_r + record.poliza_respon)
            

    ##########################################################################################################
    ######################################### Nuevo campo de poliza ##########################################
    ##########################################################################################################
    ##########################################################################################################
    ##########################################################################################################
    
    # 🚀 Nuevo Campo Calculado
    poliza_total_r = fields.Monetary(
        string="Póliza Total",
        compute="_compute_poliza_total_r2",
        store=False)

    # 💡 Función de Cómputo con 'depends'
    @api.depends('poliza_cumplim_otrosi', 'poliza_respono_trosi', 'suma_poliza_total_r_otrosi', 'poliza_total','suma_poliza_cumplim','suma_poliza_respon')
    def _compute_poliza_total_r2(self):
        """Calcula la suma de las pólizas de forma segura."""
        for record in self:
            # 1. Movimos el print DENTRO del loop usando 'record'
            # print("Procesando registro ID:", record.id)
            
            # 2. Verificamos si hay valores en los campos de 'otrosí'
            # Usamos or 0.0 para evitar errores si el campo es None
            cumplimiento = record.suma_poliza_cumplim or 0.0
            responsabilidad = record.suma_poliza_respon or 0.0

            
            if cumplimiento or responsabilidad:
                record.poliza_total_r = cumplimiento + responsabilidad
            else:
                # Si no hay otrosí, tomamos el valor base
                record.poliza_total_r = record.poliza_total or 0.0


    poliza_cumplim_otrosi = fields.Monetary(string='Póliza de cumplimiento', tracking=True, related='otrosi_ids_new.poliza_cumplim')
    poliza_respono_trosi = fields.Monetary(string='Póliza de responsabilidad', tracking=True, related='otrosi_ids_new.poliza_respon')

    poliza_total_r_otrosi = fields.Monetary(string='Póliza de responsabilidad', tracking=True, related='otrosi_ids_new.poliza_total_r_otrosi')



    suma_poliza_total_r_otrosi = fields.Monetary(string="IVA", compute="_compute_poliza_total_r_otrosi_otrosi")


    @api.depends('otrosi_ids_new.poliza_total_r_otrosi')
    def _compute_poliza_total_r_otrosi_otrosi(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('poliza_total_r_otrosi'))
            record.suma_poliza_total_r_otrosi = total

    ######################################### Nuevo campo de poliza ##########################################
        # 🚀 Nuevo Campo Calculado
    poliza_total = fields.Monetary(
        string="Póliza Total",
        compute="_compute_poliza_total",
        store=True, # Importante: Almacena el resultado en la base de datos para búsquedas/filtros
        readonly=True # El usuario no debe modificarlo manualmente
    )



    # 💡 Función de Cómputo con 'depends'
    @api.depends('poliza_cumplim', 'poliza_respon')
    def _compute_poliza_total(self):
        """Calcula la suma de las dos pólizas."""
        for record in self:
            record.poliza_total = record.poliza_cumplim + record.poliza_respon

    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓######▓▓▓######▓▓▓▓#####▓▓▓#######▓▓######▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓#▓▓▓▓#▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓#▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓#▓▓▓▓#####▓▓▓▓▓▓#▓▓▓▓▓#▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓#▓▓▓▓▓▓▓▓#▓▓▓▓▓▓#▓▓▓▓▓#▓▓▓▓#▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓######▓▓▓######▓▓▓▓#####▓▓▓▓▓▓#▓▓▓▓▓######▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    # Campo principal
    costos_fuv = fields.Float(string='Costos Fuv', tracking=True)
    # Campo calculado
    costos_fuv_r = fields.Monetary(string="Total IVA Administración", compute="_compute_costos_fuv_r", store=True)
    @api.depends('otrosi_ids_new.costos_fuv')
    def _compute_costos_fuv_r(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('costos_fuv'))
            record.costos_fuv_r = total
    ############################################# Deducir costos_fuv + otrosí ##########################################
    suma_costos_fuv = fields.Monetary(string="IVA", compute="_compute_suma_costos_fuv")
    @api.depends('costos_fuv_r', 'costos_fuv')
    def _compute_suma_costos_fuv(self):
        for record in self:
            record.suma_costos_fuv = (
                    record.costos_fuv_r + record.costos_fuv)
            

            

    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    #▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
    valor_proyecto_convenioNew_r = fields.Monetary(string="Total IVA Administración", compute="_compute_valor_proyecto_convenioNew_r", store=True)
    @api.depends('otrosi_ids_new.valor_proyecto_convenio3')
    def _compute_valor_proyecto_convenioNew_r(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('valor_proyecto_convenio3'))
            record.valor_proyecto_convenioNew_r = total



    valor_proyecto_convenioOld = fields.Monetary(string="Valor del proyecto",
                                            related="mcb_id.suma_adicion_otro_si2",
                                            store=True) #Valor adición al proyecto que se agrega en otrosí anterior

    suma_adicion_otro_si2 = fields.Monetary(string="Adición otrosí", compute='_compute_suma_adicion_otro_si2', store=True)

    @api.depends('valor_proyecto_convenioNew_r', 'valor_proyecto_convenioOld', 'otrosi_ids_new.valor_proyecto_convenio3')
    def _compute_suma_adicion_otro_si2(self):
        for record in self:
            total = 0.0
            if record.valor_proyecto_convenioNew_r:
                total += record.valor_proyecto_convenioNew_r
            elif record.valor_proyecto_convenioOld:
                total += record.valor_proyecto_convenioOld
            record.suma_adicion_otro_si2 = total



    personal_fuv_otrosi = fields.Monetary(string="Total personal FUV", compute="_compute_personal_fuv_otrosi", store=True)
    @api.depends('otrosi_ids_new.personal_fuv')
    def _compute_personal_fuv_otrosi(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('personal_fuv'))
            record.personal_fuv_otrosi = total

    gastos_bancarios_otrosi = fields.Monetary(string="Total gastos bancarios", compute="_compute_gastos_bancarios_otrosi", store=True)
    @api.depends('otrosi_ids_new.gastos_bancarios')
    def _compute_gastos_bancarios_otrosi(self):
        for record in self:
            total = sum(record.otrosi_ids_new.mapped('gastos_bancarios'))
            record.gastos_bancarios_otrosi = total

    gastos_bancarios_otrosi_general = fields.Monetary(string="Total gastos bancarios", compute="_compute_gastos_bancarios_otrosi_general", store=True)
    @api.depends('gastos_bancarios', 'gastos_bancarios_otrosi')
    def _compute_gastos_bancarios_otrosi_general(self):
        for record in self:
            record.gastos_bancarios_otrosi_general = (record.gastos_bancarios or 0.0) + (record.gastos_bancarios_otrosi or 0.0)

    personal_fuv_general = fields.Monetary(string="Total gastos bancarios", compute="_compute_personal_fuv_general", store=True)
    @api.depends('personal_fuv', 'personal_fuv_otrosi')
    def _compute_personal_fuv_general(self):
        for record in self:
            record.personal_fuv_general = (record.personal_fuv or 0.0) + (record.personal_fuv_otrosi or 0.0)

    gastos_bancarios = fields.Monetary(string="Gastos bancarios", store=True)
    personal_fuv = fields.Monetary(string="Personal FUV", store=True)
    suma_personal_fuv = fields.Monetary(string="IVA", compute="", store=True)

    # Sumar todos los resultados de costos tributarios
    costos_total_valor2 = fields.Float(compute='_compute_costos_total_valor2', store=True)
    @api.depends('IVA_ADMON_vaor', 'estampillas_result', 'reteica_result', 'resul_avisos_tableros', 'reteica_retefuente', 'personal_fuv', 'gastos_bancarios')
    def _compute_costos_total_valor2(self):
        for record in self:
            record.costos_total_valor2 = (
                    (record.IVA_ADMON_vaor or 0.0) +
                    (record.reteica_retefuente or 0.0) +
                    (record.estampillas_result or 0.0) +
                    (record.gastos_bancarios or 0.0) +
                    (record.personal_fuv or 0.0) +
                    (record.reteica_result or 0.0) +
                    (record.resul_avisos_tableros or 0.0)
            )
            print(
                'IVA_ADMON_vaor:', record.IVA_ADMON_vaor,
                '| estampillas_result:', record.estampillas_result,
                '| gastos_bancarios:', record.gastos_bancarios,
                '| personal_fuv:', record.personal_fuv,
                '| reteica_result:', record.reteica_result,
                '| reteica_retefuente:', record.reteica_retefuente,
                '| resul_avisos_tableros:', record.resul_avisos_tableros
            )




    # Sumar todos los reultados de costos tributarios
    costos_total_valor2R = fields.Float(compute='_compute_costos_total_valor2R', store=True)

    @api.depends(
        'mcb_id.IVA_ADMON_vaor', 'mcb_ids.IVA_ADMON_vaor', 'otrosi_ids_new.IVA_ADMON_vaor',
        'mcb_id.RETEICA_valor', 'mcb_ids.RETEICA_valor', 'otrosi_ids_new.reteica_result',
        'mcb_id.Descuentos_Estampillas_valor', 'mcb_ids.Descuentos_Estampillas_valor',
        'otrosi_ids_new.estampillas_result',
        'mcb_id.cuatro_X1000_valor', 'mcb_ids.cuatro_X1000_valor',
        'otrosi_ids_new.reteica_retefuente',
        'mcb_id.ICA_AVISOS_Y_TABLEROS_valor', 'mcb_ids.ICA_AVISOS_Y_TABLEROS_valor',
        'otrosi_ids_new.resul_avisos_tableros',
        'personal_fuv', 'otrosi_ids_new.personal_fuv',
        'gastos_bancarios', 'otrosi_ids_new.gastos_bancarios',
    )
    def _compute_costos_total_valor2R(self):
        for record in self:
            record.costos_total_valor2R = (
                    (record.suma_IVA_ADMON_vaor or 0.0) +
                    (record.suma_RETEICA_valor or 0.0) +
                    (record.suma_Descuentos_Estampillas_valor or 0.0) +
                    (record.suma_cuatro_X1000_valor or 0.0) +
                    (record.personal_fuv_general or 0.0) +
                    (record.gastos_bancarios_otrosi_general or 0.0) +
                    (record.suma_ICA_AVISOS_Y_TABLEROS_valor or 0.0)
            )



    #Sumar todos los reultados de costos tributarios para la MCB RESUMEN
    new_total_costos_boolean = fields.Boolean(compute='_compute_new_total_costos_boolean', store=True)
    @api.depends('valor_proyecto_convenio3')
    def _compute_new_total_costos_boolean(self):
        for record in self:
            record.new_total_costos_boolean = bool(record.valor_proyecto_convenio3)


    new_total_costos_produccion_N_r = fields.Float(compute='_compute_new_total_costos_produccion_N_r', store=True)

    @api.depends('valor_total2_final', 'suma_costo_operativo1', 'costos_total_valor2R', 'suma_poliza_cumplim', 'suma_poliza_respon', 'suma_costos_fuv', 
                 'valor_proyecto_convenio3', 'cantidad_aprobada', 'valor_proyecto_convenio3', 'costo_operativo1', 'costos_total_valor2', 'poliza_cumplim',
                 'poliza_respon', 'costos_fuv', 'cantidad_aprobada', 'poliza_total_r', 'suma_new_Participación_UV', 'suma_new_fuv_programas')
    def _compute_new_total_costos_produccion_N_r(self):
        for record in self:
            record.new_total_costos_produccion_N_r = (
                    ((record.valor_total2_final or 0.0) + (record.cantidad_aprobada or 0.0))
                            -
                              ((record.suma_costo_operativo1 or 0.0) + (record.costos_total_valor2R or 0.0) + (record.poliza_total_r or 0.0) + (record.suma_costos_fuv or 0.0) + (record.suma_new_Participación_UV or 0.0) + (record.suma_new_fuv_programas or 0.0)))


    new_total_costos_produccion_sin_pol_fuv = fields.Float(compute='_compute_new_total_costos_produccionin_pol_fuv', store=True)

    @api.depends('valor_total2_final', 'suma_costo_operativo1', 'costos_total_valor2R', 'cantidad_aprobada', 
                 'valor_proyecto_convenio3', 'costo_operativo1', 'costos_total_valor2')
    def _compute_new_total_costos_produccionin_pol_fuv (self):
        for record in self:
            record.new_total_costos_produccion_sin_pol_fuv = (
                    ((record.valor_total2_final or 0.0) + (record.cantidad_aprobada or 0.0))
                            -
                    ((record.suma_costo_operativo1 or 0.0) + (record.costos_total_valor2R or 0.0))
            )


    # ---------------------------------------------------------
    # FORZAMOS EL RE-CÁLCULO DEL PADRE AL CREAR O MODIFICAR
    # ---------------------------------------------------------
    @api.model
    def create(self, vals):
        rec = super().create(vals)
        if rec.mcb_id:
            # Llamada 1: El recálculo original
            rec.mcb_id._compute_new_total_costos_produccion_N_r()
            rec.mcb_id._compute_new_total_costos_produccionin_pol_fuv()

        return rec

    def write(self, vals):
        self._check_security_year_2026()
        res = super().write(vals)
        parents = self.mapped('mcb_id')
        if parents:
            # Llamada 1: El recálculo original
            parents._compute_new_total_costos_produccion_N_r()
            parents._compute_new_total_costos_produccionin_pol_fuv()

        return res



    # Sumar todos los reultados de costos tributarios para la MCB NORMAL
    new_total_costos_produccion_N = fields.Float(compute='_compute_new_total_costos_produccion_N', store=True)

    @api.depends('valor_proyecto_convenio3', 'costo_operativo1', 'costos_total_valor2', 'cantidad_aprobada', 'poliza_total', 'costos_fuv', 'new_Participación_UV', 'new_fuv_programas')
    def _compute_new_total_costos_produccion_N(self):
        for record in self:
            record.new_total_costos_produccion_N = (((record.valor_proyecto_convenio3 or 0.0) + (record.cantidad_aprobada or 0.0)) - 
                                                    ((record.costo_operativo1 or 0.0) + (record.costos_total_valor2 or 0.0) + (record.poliza_total or 0.0) + (record.costos_fuv or 0.0) + (record.new_Participación_UV or 0.0) + (record.new_fuv_programas or 0.0)))

    # ((valor_proyecto_convenio3 or 0.0) + (cantidad_aprobada or 0.0) - (costo_operativo1 + costos_total_valor2))

    hiden = fields.Char(string='hiden')

    ########################################## Variables para MCB resumen ########################################
    suma_Admon_12 = fields.Monetary(tracking=True, related="mcb_id.suma_Admon_12")
    Admon_C_12 = fields.Char(string='% costo operativo', related="mcb_id.Admon_C_12")
    contrapartida = fields.Monetary(string="Contrapartida FUV", tracking=True, related="mcb_id.contrapartida")
    suma_polizas = fields.Monetary(string="Póliza de cumplimiento", related="mcb_id.suma_polizas")

    IVA_ADMON = fields.Char(string="% Iva", related="mcb_id.IVA_ADMON")

    RETEICA = fields.Char(string="% Reteica", related="mcb_id.RETEICA")


    Descuentos_Estampillas = fields.Char(string="% Descuentos en estampillas", related="mcb_id.Descuentos_Estampillas")


    cuatro_X1000 = fields.Char(string="% 4X1000", related="mcb_id.cuatro_X1000")
    ICA_AVISOS_Y_TABLEROS = fields.Char(string="% ICA, avisos y tableros", related="mcb_id.ICA_AVISOS_Y_TABLEROS")
    suma_costos_tributarios_total_valor = fields.Monetary(string="Costos financiero total", related="mcb_id.suma_costos_tributarios_total_valor")
    valor_sobre_costo = fields.Monetary(string="Sobre costo", related="mcb_id.valor_sobre_costo")
    suma_Participación_UV = fields.Monetary(string="Participación UV/FAC, ESC", related="mcb_id.suma_Participación_UV")
    suma_fuv_programas = fields.Monetary(string="FUV - Programas sociales", related="mcb_id.suma_fuv_programas")



    # Sumar todos los resultados de costos tributarios
    valor_aportes = fields.Monetary(string="Valor total aportes", compute="_compute_valor_aportes")
    @api.depends('suma_adicion_otro_si2', 'valor_proyecto_convenio3', 'suma_contrapartida4', 'contrapartida_otros_r')
    def _compute_valor_aportes(self):
        for record in self:
            record.valor_aportes = (record.suma_adicion_otro_si2 + record.valor_proyecto_convenio3 + record.suma_contrapartida4 + record.contrapartida_otros_r)






    valor_total2_final = fields.Float(string="Valor total2", currency='COP', compute='_compute_valor_total2_finalR', store=True)

    @api.depends('suma_adicion_otro_si2', 'valor_proyecto_convenio3', 'otrosi_ids_new.valor_proyecto_convenio3')
    def _compute_valor_total2_finalR(self):
        for record in self:
            record.valor_total2_final = (record.suma_adicion_otro_si2 + record.valor_proyecto_convenio3)

    ######################################### Variables related del otrosí MCB nuevo ##############################


















    suma_total_costos_produccion_N = fields.Float(compute='_compute_suma_total_costos_produccion_N')
    @api.onchange('valor_proyecto_convenio3', 'costo_operativo1', 'costos_total_valor2', 'poliza_cumplim',
                  'poliza_respon', 'costos_fuv')
    def _compute_suma_total_costos_produccion_N(self):
        for record in self:
            record.suma_total_costos_produccion_N = (
                    ((record.valor_proyecto_convenio3 or 0.0)) -
                    (
                            (record.costo_operativo1 or 0.0)
                            + (record.costos_total_valor2 or 0.0)
                            + (record.poliza_cumplim or 0.0)
                            + (record.poliza_respon or 0.0)
                            + (record.costos_fuv or 0.0)
                    )
            )

    tipo_otrosi = fields.Char(string="Tipo de Otrosí", related="otrosi_ids_new.tipo_otrosi")
    
class NewMCBOtroSi(models.Model):
    _name = "new_mcb_otrosi"
    _description = "Otrosí MCB"
    _inherit = ['mail.thread', 'mail.activity.mixin']


    name = fields.Char(string="Nombre del OtroSí")
    mcb2_id = fields.Many2one('mcb2', string="MCB Anterior", ondelete='cascade')
    new_mcb_id = fields.Many2one("new_mcb", string="MCB Actual", ondelete='cascade')
    fecha_finalizacion_inicial = fields.Date(string="Fecha de finalización", related="new_mcb_id.fecha_finalizacion")
    fecha_finalizacion = fields.Date(string="Fecha de finalización", related="mcb2_id.fecha_finalizacion", readonly=False, store=True)
    alias_costo = fields.Selection([
        ('gastos_admin', 'Costo administrativo'),
        ('costo_operativo', 'Costo operativo'),
        ('amd_fuv', 'Administración FUV'),
    ], string="Seleccionar", required=False)
    
    contratos_vco_otrosi = fields.Float(string='Contratos VCO')

    def name_get(self):
        result = []
        for record in self:
            nombre = record.name or ""
            tipo = record.tipo_otrosi or ""
            display_name = f"{nombre} - {tipo}" if tipo else nombre
            result.append((record.id, display_name))
        return result


    obervaciones_otrosi = fields.Text(string="Observaciones Otrosí", tracking=True)
    documentos_soporte_ids = fields.Many2many(
        'drive', 'new_mcb_otrosi_soporte_drive_rel', 'otrosi_id', 'drive_id',
        string="Documentos de soporte del Otrosí", copy=False,
    )

    def _validar_destino_documentos_soporte(self, workspace):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self.new_mcb_id:
            raise UserError("El Otrosí no tiene una Nueva MCB vinculada.")
        stage = self.new_mcb_id._get_acta_comite_stage()
        project = self.new_mcb_id.mcb_id.contrataciones_id
        if (not workspace.exists() or not workspace.folder_google_id
                or workspace.folder_google_id != (stage.folder_id or stage.google_drive_id)
                or workspace.browser_id.project_id != project):
            raise UserError("Los soportes deben guardarse en la 1. CARPETA PRINCIPAL del proyecto del Otrosí.")

    def action_cargar_documentos_soporte(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self.new_mcb_id:
            raise UserError("El Otrosí no tiene una Nueva MCB vinculada.")
        action = self.new_mcb_id.action_upload_acta_comite()
        action['name'] = 'Cargar documentos de soporte del Otrosí'
        action['context'].pop('default_new_mcb_acta_comite_id', None)
        action['context']['default_upload_mode'] = 'file|new_mcb_otrosi_soporte:%s' % self.id
        return action

    def action_ver_documentos_soporte(self):
        self.ensure_one()
        return {
            'name': 'Documentos de soporte del Otrosí',
            'type': 'ir.actions.act_window', 'res_model': 'drive',
            'view_mode': 'tree,form', 'target': 'current',
            'domain': [('id', 'in', self.documentos_soporte_ids.ids)],
            'context': {'create': False},
        }

    aclaraciones_otrosi = fields.Boolean(string="Aclaración", tracking=True)
    adiciones_otrosi = fields.Boolean(string="Adición", tracking=True)
    prorroga_otrosi = fields.Boolean(string="Prórroga", tracking=True)
    modificaciones_otrosi = fields.Boolean(string="Modificación", tracking=True)
    suspension = fields.Boolean(string="Suspensión", tracking=True)
    reinicio = fields.Boolean(string="Reinicio", tracking=True)
    acta_de_prorroga_de_la_suspension = fields.Boolean(string="Acta de prórroga de la suspensión", tracking=True)
    tipo_otrosi = fields.Char(string="Tipo de Otrosí", compute="_obtener_tipo_otrosi", store=True)
    
    @api.depends('aclaraciones_otrosi', 'adiciones_otrosi', 'prorroga_otrosi', 'modificaciones_otrosi',
                 'suspension', 'reinicio', 'acta_de_prorroga_de_la_suspension')
    def _obtener_tipo_otrosi(self):
        for record in self:
            tipos = []
            if record.aclaraciones_otrosi:
                tipos.append('Aclaración')
            if record.adiciones_otrosi:
                tipos.append('Adición')
            if record.prorroga_otrosi:
                tipos.append('Prórroga')
            if record.modificaciones_otrosi:
                tipos.append('Modificación')
            if record.suspension:
                tipos.append('Suspensión')
            if record.reinicio:
                tipos.append('Reinicio')
            if record.acta_de_prorroga_de_la_suspension:
                tipos.append('Acta de prórroga de la suspensión')
            record.tipo_otrosi = ', '.join(tipos) if tipos else 'Ninguno'

    def action_abrir_pantalla_completa(self):
        # Devolver la acción para abrir el formulario del registro actual
        nombre_completo = f"Otrosí: {self.name}, {self.tipo_otrosi}"
        action = {
            'name': nombre_completo,
            'type': 'ir.actions.act_window',
            'res_model': 'new_mcb_otrosi',
            'view_mode': 'form',
            'res_id': self.id,  # ID del registro actual
            'target': 'current',
        }

        return action




    

    #    TRIGGER 1: AL CREAR (CREATE)
    # ---------------------------------------------------------
    @api.model
    def create(self, vals):
        # 1. Lógica del nombre (tu código original)
        if 'new_mcb_id' in vals:
            mcb_id = vals['new_mcb_id']
            count = self.search_count([('new_mcb_id', '=', mcb_id)]) + 1
            vals['name'] = f'Otrosí {count}'

        # 2. Crear registro
        registro_otrosi = super(NewMCBOtroSi, self).create(vals)

        # 3. TRIGGER DOBLE (Papá y Abuelo) y Notificación en el Chatter
        if registro_otrosi.new_mcb_id:
            # Notificar quién creó el Otrosí en el chatter del padre
            usuario_creacion = registro_otrosi.env.user.name
            registro_otrosi.new_mcb_id.message_post(
                body=f"<b>[{registro_otrosi.name}]</b> El usuario <b>{usuario_creacion}</b> ha creado este Otrosí.",
                subject=f"Creación de {registro_otrosi.name}"
            )

            # A) ACTUALIZAR AL PADRE (new_mcb)
            print(f"🔄 [CREATE] Actualizando PADRE (new_mcb): {registro_otrosi.new_mcb_id.id}")
            registro_otrosi.new_mcb_id._compute_fecha_finalizacion_otrosi()

            # B) ACTUALIZAR AL ABUELO (mcb)
            if registro_otrosi.new_mcb_id.mcb_id:
                print(f"🚀 [CREATE] Actualizando ABUELO (mcb): {registro_otrosi.new_mcb_id.mcb_id.id}")
                registro_otrosi.new_mcb_id.mcb_id._compute_fecha_finalizacion()

        return registro_otrosi

    def unlink(self):
        for rec in self:
            if rec.new_mcb_id:
                rec.new_mcb_id.message_post(
                    body=f"<b>[{rec.name}]</b> El usuario <b>{rec.env.user.name}</b> ha eliminado este Otrosí.",
                    subject=f"Eliminación de {rec.name}"
                )
        return super(NewMCBOtroSi, self).unlink()

    def write(self, vals):
        res = super(NewMCBOtroSi, self).write(vals)
        
        for rec in self:
            cambios = []
            
            # Función auxiliar para formatear montos/números si vienen en los valores
            def fmt(val):
                return f"{val:,.2f}" if isinstance(val, (int, float)) else val

            if 'valor_proyecto_convenio3' in vals:
                cambios.append(f"• Adición modificada a: {fmt(rec.valor_proyecto_convenio3)}")
            if 'fecha_finalizacion' in vals:
                cambios.append(f"• Fecha de finalización modificada a: {rec.fecha_finalizacion}")
            if 'contrapartida4' in vals:
                cambios.append(f"• Contrapartida modificada a: {fmt(rec.contrapartida4)}")
            if 'contrapartida_otros' in vals:
                cambios.append(f"• Contrapartida (OTROS) modificada a: {fmt(rec.contrapartida_otros)}")
            if 'alias_costo' in vals:
                cambios.append(f"• Tipo de costo seleccionado: {rec.alias_costo}")
            if 'costo_operativo_porc' in vals:
                cambios.append(f"• Porcentaje del costo operativo modificado a: {fmt(rec.costo_operativo_porc)}%")
            if 'costo_operativo1' in vals:
                cambios.append(f"• Valor del costo operativo modificado a: {fmt(rec.costo_operativo1)}")
            if 'contratos_vco_otrosi' in vals:
                cambios.append(f"• Contratos VCO modificado a: {fmt(rec.contratos_vco_otrosi)}")
            if 'porc_iva' in vals:
                cambios.append(f"• % Iva modificado a: {fmt(rec.porc_iva)}%")
            if 'IVA_ADMON_vaor' in vals:
                cambios.append(f"• Iva (Valor) modificado a: {fmt(rec.IVA_ADMON_vaor)}")
            if 'porc_reteica' in vals:
                cambios.append(f"• % Reteica modificado a: {fmt(rec.porc_reteica)}%")
            if 'reteica_result' in vals:
                cambios.append(f"• Reteica (Valor) modificado a: {fmt(rec.reteica_result)}")
            if 'porc_estampillas' in vals:
                cambios.append(f"• % Estampillas modificado a: {fmt(rec.porc_estampillas)}%")
            if 'estampillas_result' in vals:
                cambios.append(f"• Estampillas (Valor) modificado a: {fmt(rec.estampillas_result)}")
            if 'porc_avisos_tableros' in vals:
                cambios.append(f"• % ICA, avisos y tableros modificado a: {fmt(rec.porc_avisos_tableros)}%")
            if 'resul_avisos_tableros' in vals:
                cambios.append(f"• ICA, avisos y tableros (Valor) modificado a: {fmt(rec.resul_avisos_tableros)}")
            if 'gastos_bancarios' in vals:
                cambios.append(f"• Gastos bancarios modificados a: {fmt(rec.gastos_bancarios)}")
            if 'porc_retefuente' in vals:
                cambios.append(f"• % Retefuente modificado a: {fmt(rec.porc_retefuente)}%")
            if 'reteica_retefuente' in vals:
                cambios.append(f"• Retefuente (Valor) modificado a: {fmt(rec.reteica_retefuente)}")
            if 'poliza_cumplim' in vals:
                cambios.append(f"• Póliza de cumplimiento modificada a: {fmt(rec.poliza_cumplim)}")
            if 'poliza_respon' in vals:
                cambios.append(f"• Póliza de responsabilidad modificada a: {fmt(rec.poliza_respon)}")
            if 'costos_fuv' in vals:
                cambios.append(f"• Costos FUV modificados a: {fmt(rec.costos_fuv)}")
            if 'new_Participación_UV' in vals:
                cambios.append(f"• Participación UV/FAC, ESC modificada a: {fmt(rec.new_Participación_UV)}")
            if 'new_fuv_programas' in vals:
                cambios.append(f"• FUV - Programas sociales modificado a: {fmt(rec.new_fuv_programas)}")
            
            # Nuevos campos incorporados
            if 'obervaciones_otrosi' in vals:
                cambios.append(f"• Observaciones Otrosí actualizadas.")
            if 'aclaraciones_otrosi' in vals:
                estado_aclaracion = "Sí" if rec.aclaraciones_otrosi else "No"
                cambios.append(f"• Aclaración modificada a: {estado_aclaracion}")
            if 'adiciones_otrosi' in vals:
                estado_adicion = "Sí" if rec.adiciones_otrosi else "No"
                cambios.append(f"• Adición modificada a: {estado_adicion}")
            if 'prorroga_otrosi' in vals:
                estado_prorroga = "Sí" if rec.prorroga_otrosi else "No"
                cambios.append(f"• Prórroga modificada a: {estado_prorroga}")
            if 'modificaciones_otrosi' in vals:
                estado_modificacion = "Sí" if rec.modificaciones_otrosi else "No"
                cambios.append(f"• Modificación modificada a: {estado_modificacion}")

            for campo, etiqueta in (
                ('suspension', 'Suspensión'),
                ('reinicio', 'Reinicio'),
                ('acta_de_prorroga_de_la_suspension', 'Acta de prórroga de la suspensión'),
            ):
                if campo in vals:
                    estado = "Sí" if rec[campo] else "No"
                    cambios.append(f"• {etiqueta} modificada a: {estado}")

            if cambios and rec.new_mcb_id:
                mensaje = f"<b>[{rec.name}]</b> Se han actualizado los siguientes datos:<br/>" + "<br/>".join(cambios)
                rec.new_mcb_id.message_post(
                    body=mensaje,
                    subject=f"Actualización en {rec.name}"
                )

        if 'fecha_finalizacion' in vals or 'new_mcb_id' in vals:
            for rec in self:
                if rec.new_mcb_id:
                    # A) ACTUALIZAR AL PADRE
                    print(f"🔄 [WRITE] Actualizando PADRE (new_mcb): {rec.new_mcb_id.id}")
                    rec.new_mcb_id._compute_fecha_finalizacion_otrosi()

                    # B) ACTUALIZAR AL ABUELO
                    if rec.new_mcb_id.mcb_id:
                        print(f"🚀 [WRITE] Actualizando ABUELO (mcb): {rec.new_mcb_id.mcb_id.id}")
                        rec.new_mcb_id.mcb_id._compute_fecha_finalizacion()
        return res



    @api.model
    def crear_otrosis_faltantes(self):
        # Buscar todos los mcb2 que estén relacionados a algún mcb
        mcb2_records = self.env['mcb2'].search([])
        for mcb2 in mcb2_records:
            mcb = mcb2.mcb_id  # Relación Many2one desde mcb2 → mcb
            if not mcb:
                continue  # Si no tiene mcb relacionado, omitir

            # Buscar el new_mcb relacionado al mismo mcb
            new_mcb = self.env['new_mcb'].search([('mcb_id', '=', mcb.id)], limit=1)
            if not new_mcb:
                print(f"❌ No se encontró new_mcb para mcb2 {mcb2.id}")
                continue

            # Validar si ya existe el otrosí para esa relación mcb2 + new_mcb
            existe = self.search([('mcb2_id', '=', mcb2.id), ('new_mcb_id', '=', new_mcb.id)], limit=1)
            if existe:
                print(f"⚠️ Ya existe otrosí para mcb2 {mcb2.id} y new_mcb {new_mcb.id}")
                continue

            # Crear el otrosí
            self.create({
                'mcb2_id': mcb2.id,
                'new_mcb_id': new_mcb.id,
            })
            print(f"✅ Creado otrosí para mcb2 {mcb2.id} y new_mcb {new_mcb.id}")

    currency_id = fields.Many2one('res.currency', string='Moneda')


    valor_proyecto_convenio3 = fields.Float(string="Adición", related="mcb2_id.valor_proyecto_convenio2si", readonly=False, store=True)

    costo_operativo1 = fields.Monetary(string="Valor del costo operativo", related="mcb2_id.Admon_12", store=True, readonly=False, tracking=True)
    contrapartida4 = fields.Monetary(string="Contrapartida", related="mcb2_id.contrapartida2", store=True, readonly=False, tracking=True)
    contrapartida_otros = fields.Monetary(string="Contrapartida (OTROS)", tracking=True)

    # Bloque a sumar
    IVA_ADMON_vaor = fields.Monetary(string="IVA", related="mcb2_id.IVA_ADMON_vaor", store=True, readonly=False)
    reteica_result = fields.Monetary(string="Resultado calculado de Reteica", related="mcb2_id.RETEICA_valor", store=True, readonly=False)
    estampillas_result = fields.Monetary(string="Resultado calculado de Estampillas", related="mcb2_id.Descuentos_Estampillas_valor", store=True, readonly=False)
    reteica_retefuente = fields.Monetary(string="Resultado calculado de Retefuente", related="mcb2_id.cuatro_X1000_valor", store=True, readonly=False)
    resul_avisos_tableros = fields.Monetary(string="Resultado calculado de avisos y tableros", related="mcb2_id.ICA_AVISOS_Y_TABLEROS_valor", store=True, readonly=False)
    personal_fuv = fields.Monetary(string="Personal FUV", store=True)
    gastos_bancarios = fields.Monetary(string="Gastos bancarios", store=True)
    # Sumar todos los reultados de costos tributarios
    costos_total_valor2 = fields.Float(compute='_compute_costos_total_valor2', store=True)

    @api.depends('IVA_ADMON_vaor', 'estampillas_result', 'reteica_result', 'resul_avisos_tableros', 'reteica_retefuente', 
                  'costos_total_valor2', 'gastos_bancarios', 'personal_fuv')
    def _compute_costos_total_valor2(self):
        for record in self:
            record.costos_total_valor2 = (
                    (record.IVA_ADMON_vaor or 0.0) +
                    (record.reteica_retefuente or 0.0) +
                    (record.estampillas_result or 0.0) +
                    (record.reteica_result or 0.0) +
                    (record.personal_fuv or 0.0) +
                    (record.resul_avisos_tableros or 0.0)+
                    (record.gastos_bancarios or 0.0)
            )
            print(
                'IVA_ADMON_vaor:', record.IVA_ADMON_vaor,
                '| estampillas_result:', record.estampillas_result,
                '| reteica_result:', record.reteica_result,
                '| reteica_retefuente:', record.reteica_retefuente,
                '| personal_fuv:', record.personal_fuv,
                '| gastos_bancarios:', record.gastos_bancarios,
                '| resul_avisos_tableros:', record.resul_avisos_tableros
            )


    # Sección Principal

    costo_operativo_porc = fields.Float(string="Porcentaje del costo operativo")


    # Costos Tributarios

    porc_iva = fields.Float(string='% Iva', digits=(5, 1))
    porc_reteica = fields.Float(string="Porcentaje de Reteica")

    porc_estampillas = fields.Float(string="Porcentaje de Estampillas")

    porc_retefuente = fields.Float(string="Porcentaje de Retefuente")

    porc_avisos_tableros = fields.Float(string="Porcentaje de ICA, avisos y tableros")

    # Pólizas
    poliza_cumplim = fields.Monetary(string="Póliza de cumplimiento", related="mcb2_id.polizas", store=True, readonly=False)
    poliza_respon = fields.Monetary(string="Póliza de responsabilidad")


    poliza_total_r_otrosi = fields.Monetary(
        string="Póliza Total",
        compute="_compute_poliza_total_otrosi_r",
        store=True, # Importante: Almacena el resultado en la base de datos para búsquedas/filtros
        readonly=True # El usuario no debe modificarlo manualmente
    )

    # 💡 Función de Cómputo con 'depends'
    @api.depends('poliza_cumplim', 'poliza_respon')
    def _compute_poliza_total_otrosi_r(self):
        """Calcula la suma de las dos pólizas."""
        for record in self:
            record.poliza_total_r_otrosi = record.poliza_cumplim + record.poliza_respon


    # Otros Costos
    costos_fuv = fields.Monetary(string="FUV")

    # Sumar todos los reultados de costos tributarios
    new_total_costos_produccion_N = fields.Float(compute='_compute_new_total_costos_produccion_N')

    @api.onchange('valor_proyecto_convenio3', 'costo_operativo1', 'costos_total_valor2', 'costos_fuv', 'poliza_cumplim', 'poliza_total_r_otrosi', 'new_Participación_UV', 'new_fuv_programas')
    def _compute_new_total_costos_produccion_N(self):
        for record in self:
            record.new_total_costos_produccion_N = (((record.valor_proyecto_convenio3 or 0.0)) - 
                                                    ((record.costo_operativo1 or 0.0) + (record.costos_total_valor2 or 0.0) + (record.poliza_total_r_otrosi or 0.0) + (record.costos_fuv or 0.0) + (record.new_Participación_UV or 0.0) + (record.new_fuv_programas or 0.0)))


    # Participación
    new_Participación_UV = fields.Monetary(string="Participación UV/FAC, ESC", related="mcb2_id.Participación_UV", store=True, readonly=False)
    new_fuv_programas = fields.Monetary(string="FUV - Programas sociales", related="mcb2_id.fuv_programas", store=True, readonly=False)

    # Información de Control
    realizado_por = fields.Char(string="Realizado por", default="Diana Cano", tracking=True)
    aprobado_por = fields.Char(string="Aprobado por", default="Katherine Cano", tracking=True)

    realizado_por_check = fields.Boolean(string="Elaborado por", tracking=True)
    aprobado_por_check = fields.Boolean(string="Aprobado por", tracking=True)


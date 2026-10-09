# models.py
from dataclasses import field
import string
from odoo import models, fields, api


class Actividad(models.Model):
    _name = 'actividad'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre', readonly=False, tracking=True)
    ejecucion_id = fields.Many2one('ejecucion', string='Ejecución')
    tabla_avances_ids = fields.One2many('tablero_avances', 'ejecucion_A_id', string='Tablero de Avances')

    campos_ocultos = fields.Boolean(string='Campos ocultos', compute='_compute_campos_ocultos',
                                    inverse='_inverse_campos_ocultos')

    @api.depends('ejecucion_id.campos_ocultos')
    def _compute_campos_ocultos(self):
        for actividad in self:
            actividad.campos_ocultos = actividad.ejecucion_id.campos_ocultos if actividad.ejecucion_id else False

    def _inverse_campos_ocultos(self):
        for actividad in self:
            if actividad.ejecucion_id:
                actividad.ejecucion_id.campos_ocultos = actividad.campos_ocultos

    @api.model
    def create(self, values):
        actividad = super(Actividad, self).create(values)

        # Obtener los rubros existentes en datos_adicionales para la actividad actual
        rubros_existentes = self.env['datos_adicionales'].search([
            ('actividad_id', '=', actividad.id)
        ]).mapped('rubro')

        # Crear automáticamente 20 líneas de datos_adicionales para cada rubro sin repetir
        rubros = self.env['datos_adicionales'].fields_get()['rubro']['selection']
        # Llamada a la función desde otro lugar
        self.env['actividad'].actualizar_ejecucion_todos_registros()

        for _ in range(10):
            for rubro_key, rubro_label in rubros:
                if rubro_key not in rubros_existentes:
                    self.env['datos_adicionales'].create({
                        'actividad_id': actividad.id,
                        'rubro': rubro_key,
                        'actividad_global': 0.0,  # Puedes establecer valores predeterminados aquí
                        'ejecutado_act_global': 0.0,
                        'dispo_act': 0.0,
                        # Agrega otros campos necesarios aquí
                    })
                    rubros_existentes.append(rubro_key)

        return actividad

    # otros campos
    Subtitulo = fields.Text(string="VALOR DISPONIBLE A COMPROMETER")
    viajes = fields.Float(string="VIAJES")
    difusion_de_resultados = fields.Float(string="DIFUSIÓN DE RESULTADOS")
    materiales_e_insumos = fields.Float(string="MATERIALES E INSUMOS")

    ejecutado_act = fields.Float(string="EJECUTADO ACT")

    ##############################################################################
    total_general2_global = fields.Float(
        string="Ejecutado",
        compute='_compute_total_general2_global',
        # Almacenar el resultado para poder acceder a él más eficientemente
    )

    @api.depends('ejecucion_id', 'actividad_global2')
    def _compute_total_general2_global(self):
        # Llamada a la función desde otro lugar
        self.env['actividad'].actualizar_ejecucion_todos_registros()
        for actividad in self:
            total_general2 = sum(
                linea.actividad_global2
                for linea in actividad.search([('ejecucion_id', '=', actividad.ejecucion_id.id)])
            )
            actividad.total_general2_global = total_general2

    ####################################################################
    total_general1_global = fields.Float(
        string="Presupuesto",
        compute='_compute_total_general1_global',

    )

    @api.depends('ejecucion_id', 'actividad_global1')
    def _compute_total_general1_global(self):
        for actividad in self:
            total_general1 = sum(
                linea.actividad_global1
                for linea in actividad.search([('ejecucion_id', '=', actividad.ejecucion_id.id)])
            )
            actividad.total_general1_global = total_general1

    #######################################################################
    total_general3_global = fields.Float(
        string="Disponible",
        compute='_compute_total_global3',

    )

    @api.depends('total_general1_global', 'total_general2_global')
    def _compute_total_global3(self):
        for actividad in self:
            actividad.total_general3_global = actividad.total_general1_global - actividad.total_general2_global

    #################################################################################################################
    total_disponible4_global = fields.Float(
        string="Disponible",
        compute='_compute_total_global_dispinible4',

    )

    @api.depends('presupuesto_total_global', 'ejecutado_total_global')
    def _compute_total_global_dispinible4(self):
        for actividad in self:
            actividad.total_disponible4_global = actividad.presupuesto_total_global - actividad.ejecutado_total_global

    #########################################################################

    @api.depends('datos_adicionales.actividad_global')
    def _compute_total_Global(self):
        for actividad in self:
            actividad.actividad_global1 = sum(actividad.datos_adicionales.mapped('actividad_global'))

    actividad_global1 = fields.Monetary(
        string="Total General De ACTIVIDAD",
        compute='_compute_total_Global',
        currency='COP'
    )

    ###########################################################################################

    actividad_tatal = fields.Float(related="datos_adicionales.total_actividad")

    presupuesto_total_global = fields.Float(
        string="Presupuesto Total Global",
        compute='_compute_presupuesto_total_global',

    )

    @api.depends('actividad_tatal')
    def _compute_presupuesto_total_global(self):
        for actividad in self:
            # Obtener todos los registros de 'actividad_tatal' relacionados con la actividad actual
            registros_actividad_tatal = self.env['actividad'].search(
                [])  # Puedes agregar condiciones según tus necesidades

            # Sumar los valores de 'actividad_tatal' de todos los registros
            total_presupuesto = sum(registros_actividad_tatal.mapped('actividad_tatal'))

            # Asignar el total al campo 'presupuesto_total_global'
            actividad.presupuesto_total_global = total_presupuesto

    #####################################################################################################

    @api.depends('datos_adicionales.ejecutado_act_global')
    def _compute_total_Global2(self):
        for actividad in self:
            actividad.actividad_global2 = sum(actividad.datos_adicionales.mapped('ejecutado_act_global'))

    actividad_global2 = fields.Monetary(
        string="Total General De EJECUTADO ACT",
        compute='_compute_total_Global2',
        currency='COP'
    )
    #############################################################################################
    ejecutado_tatal = fields.Float(related="datos_adicionales.total_ejecutado")

    ejecutado_total_global = fields.Float(
        string="Ejecutado Total Global",
        compute='_compute_ejecutado_total_global',
    )

    @api.depends('ejecutado_tatal')  # Cambiado de 'total_ejecutado' a 'ejecutado_tatal'
    def _compute_ejecutado_total_global(self):
        for actividad in self:
            # Obtener todos los registros de 'actividad' relacionados con la actividad actual
            registros_actividad = self.env['actividad'].search([])  # Puedes agregar condiciones según tus necesidades

            # Sumar los valores de 'ejecutado_tatal' de todos los registros
            total_ejecutado = sum(
                registros_actividad.mapped('ejecutado_tatal'))  # Cambiado de 'total_ejecutado' a 'ejecutado_tatal'

            # Asignar el total al campo 'ejecutado_total_global'
            actividad.ejecutado_total_global = total_ejecutado

    #######################################################################################

    total_general = fields.Float(string='Total General', compute='_compute_total_general', store=True)

    currency_id = fields.Many2one('res.currency', string='Moneda')

    @api.depends('line_ids.total')
    def _compute_total_general(self):
        for actividad in self:
            actividad.total_general = sum(actividad.line_ids.mapped('total'))

    porcentaje_ejecucion = fields.Float(
        string="% EJECUCION",
        compute='_compute_porcentaje_ejecucion',
    )

    @api.depends('actividad_global1', 'actividad_global2')
    def _compute_porcentaje_ejecucion(self):
        for record in self:
            if record.actividad_global1 != 0:
                # Calcular el porcentaje de ejecución
                porcentaje_decimal = round((record.actividad_global2 / record.actividad_global1) * 100, 2)
                # Asegurarse de que el porcentaje esté en el rango de 0 a 100
                record.porcentaje_ejecucion = max(0.0, min(porcentaje_decimal, 100.0))
            else:
                record.porcentaje_ejecucion = 0.0
                # Obtener todos los registros del modelo 'actividad'
                registros_actividad = self.env['actividad'].search([])

                # Inicializar las variables para almacenar los totales
                actividad_global1_nueva = 0.0
                actividad_global2_nueva = 0.0

                # Iterar sobre los registros y sumar los valores de 'actividad_global1' y 'actividad_global2'
                for actividad in registros_actividad:
                    # Sumar los valores de 'actividad_global1'
                    actividad_global1_nueva += actividad.actividad_global1

                    # Sumar los valores de 'actividad_global2'
                    actividad_global2_nueva += actividad.actividad_global2

                # Imprimir los totales (opcional)
                print("Total de suma actividades:", actividad_global1_nueva)
                print("Total de suma ejecutado:", actividad_global2_nueva)

                # Calcular el resultado de la división
                resultado_division = 0.0
                if actividad_global1_nueva != 0:
                    resultado_division = actividad_global2_nueva / actividad_global1_nueva
                    print("Resultado de la división:", resultado_division)

                # Almacenar el resultado de la división en una nueva variable del modelo
                record.resultado_division = resultado_division

    #############################################################################################
    porcentaje_total_ejecucion = fields.Float(
        string="% EJECUCIÓN",
        compute='_compute_porcentaje_total_ejecucion',
    )

    @api.depends('actividad_tatal', 'ejecutado_tatal')
    def _compute_porcentaje_total_ejecucion(self):
        for record in self:
            if record.actividad_tatal != 0:
                # Calcular el porcentaje de ejecución
                porcentaje_decimal = round((record.ejecutado_tatal / record.actividad_tatal) * 100, 2)
                # Asegurarse de que el porcentaje esté en el rango de 0 a 100
                record.porcentaje_total_ejecucion = max(0.0, min(porcentaje_decimal, 100.0))

            else:
                record.porcentaje_total_ejecucion = 0.0

    ################################################################################################

    # Agregar el campo 'resultado_division' al modelo
    resultado_division = fields.Float(string='Resultado de la División')

    @api.model
    def actualizar_ejecucion_todos_registros(self):
        # Obtener todos los registros del modelo 'actividad'
        registros_actividad = self.env['actividad'].search([])

        # Inicializar las variables para almacenar los totales
        actividad_global1_nueva = 0.0
        actividad_global2_nueva = 0.0

        # Iterar sobre los registros y sumar los valores de 'actividad_global1' y 'actividad_global2'
        for actividad in registros_actividad:
            # Sumar los valores de 'actividad_global1'
            actividad_global1_nueva += actividad.actividad_global1

            # Sumar los valores de 'actividad_global2'
            actividad_global2_nueva += actividad.actividad_global2

        # Calcular el resultado de la división
        resultado_division = 0.0
        if actividad_global1_nueva != 0:
            resultado_division = actividad_global2_nueva / actividad_global1_nueva

        # Actualizar todos los registros con el nuevo resultado de la división
        registros_actividad.write({'resultado_division': resultado_division})

    ##Nueva funcion para sacar la ejecucion global de las actividades de un proyecto
    actividad_global1_nueva = fields.Float()
    actividad_global2_nueva = fields.Float()
    ejecucion_global_de_actividades_por_proyecto = fields.Float(string="Porcentaje ejecucion global",
                                                                compute='_compute_ejecucion_global_de_actividades_por_proyecto')

    ## Funcion ejecucion global
    @api.depends('actividad_global1_nueva', 'actividad_global2_nueva')
    def _compute_ejecucion_global_de_actividades_por_proyecto(self):
        for record in self:
            if record.actividad_global1_nueva and record.actividad_global2_nueva:
                porcentaje = (record.actividad_global2_nueva / record.actividad_global1_nueva)
                record.ejecucion_global_de_actividades_por_proyecto = porcentaje
            else:
                record.ejecucion_global_de_actividades_por_proyecto = 0.0

    is_favorite1 = fields.Boolean(string='Favorito', default=False, help="Marcar como favorito")

    # Campo calculado para utilizar en el Kanban
    is_favorite_star = fields.Boolean(string='Favorito', compute='_compute_is_favorite_star', store=True)

    @api.depends('is_favorite1')
    def _compute_is_favorite_star(self):
        for record in self:
            record.is_favorite_star = record.is_favorite1

    # Nuevos campos
    item = fields.Char(string='Item', related="line_ids.item")
    rubro = fields.Selection([
        ('personalcin', 'PERSONAL CIENTÍFICO'),
        ('tecnico', 'SERVICIOS TÉCNICOS'),
        ('adquisicion', 'ADQUISICIÓN DE EQUIPOS'),
        ('materiales', 'MATERIALES E INSUMOS'),
        ('adecuacion', 'ADECUACIÓN DE INFRAESTRUCTURA'),
        ('software', 'SOFTWARE'),
        ('salida', 'SALIDAS DE CAMPO'),
        ('difusion', 'DIFUSIÓN DE RESULTADOS'),
        ('Viajes', 'VIAJES'),
        ('gastos', 'GASTOS DE PROPIEDAD INTELECTUAL'),
        ('biblo', 'BIBLIOGRAFÍA'),
        ('adminis', 'GASTOS DE ADMINISTRACIÓN'),
        ('ctei', 'DIVULGACIÓN Y APROPIACIÓN DE CTEI'),
        ('personal', 'PERSONAL DE APOYO'),
        ('capacitaciones', 'CAPACITACIONES'),
        ('convenio', 'CONVENIO ESPECIAL DE COOPERACIÓN TÉC'),
        ('certificaion', 'CERTIFICACIONES'),
        ('consultoria', 'CONSULTORÍA ESPECIALIZADA'),
        ('serviciosTec', 'SERVICIOS TÉCNOLÓGICOS'),
    ], string='Rubro', tracking=True, related="line_ids.rubro")

    tipo_requerimiento = fields.Selection([
        ('servicio', 'Servicio '),
        ('bien', 'Bien'),
    ], string='Tipo Requerimiento', tracking=True, related="line_ids.tipo_requerimiento")
    descripcion_gasto = fields.Char(string='Descripción del Gasto', tracking=True, related="line_ids.descripcion_gasto")
    fecha_solicitud = fields.Date(string='Fecha de Solicitud', tracking=True, related="line_ids.fecha_solicitud")
    fecha_requerida = fields.Date(string='Fecha Requerida', tracking=True, related="line_ids.fecha_requerida")
    lugar_entrega = fields.Char(string='Lugar y/o Forma de Entrega', tracking=True, related="line_ids.lugar_entrega")
    cantidad = fields.Integer(string='Cantidad', tracking=True, related="line_ids.cantidad")
    tiempo_ejecucion_dias = fields.Integer(string='Tiempo de Ejecución (Días)', tracking=True,
                                           related="line_ids.tiempo_ejecucion_dias")
    proveedor = fields.Char(string='Proveedor', tracking=True, related="line_ids.proveedor")
    nit = fields.Char(string='NIT', tracking=True, related="line_ids.nit")
    tipo_contractual = fields.Char(string='Tipo Contractual', tracking=True, related="line_ids.tipo_contractual")
    valor_estimado = fields.Float(string='Valor Estimado (Requerimiento)', tracking=True,
                                  related="line_ids.valor_estimado")
    valor_ejecutado = fields.Float(string='Valor Ejecutado', tracking=True, related="line_ids.valor_ejecutado")
    impuesto_asumido_proyecto = fields.Float(string='Impuesto Asumido por el Proyecto', tracking=True,
                                             related="line_ids.impuesto_asumido_proyecto")

    total = fields.Float(string='Total', tracking=True, related="line_ids.total")
    estado = fields.Selection([
        ('pendiente', 'PENDIENTE '),
        ('en_proceso', 'EJECUTADO'),
        ('completado', 'COMPLETADO'),
        ('solicitado', 'SOLICITADO')
    ], string='Estado', default='pendiente', tracking=True, related="line_ids.estado")

    line_ids = fields.One2many('actividad_line', 'actividad_id', string='Líneas de Actividad')
    datos_adicionales = fields.One2many('datos_adicionales', 'actividad_id', string='Datos Adicionales')

    valor_ejecutado_total = fields.Float(string='Valor Ejecutado Total', compute='_compute_valor_ejecutado_total',
                                         store=True, tracking=True)

    @api.depends('line_ids.valor_ejecutado')
    def _compute_valor_ejecutado_total(self):
        for actividad in self:
            actividad.valor_ejecutado_total = sum(actividad.line_ids.mapped('valor_ejecutado'))

    otros_rubros = fields.Many2many(comodel_name='rubros', string="Rubros")


class ActividadLine(models.Model):
    _name = 'actividad_line'
    actividad_id = fields.Many2one('actividad', string='Actividad')
    _inherit = ['mail.thread', 'mail.activity.mixin']

    otros_rubros = fields.Many2one(comodel_name='rubros', string="Otros rubro")

    # Nueva variable para almacenar la suma de 'total' donde 'rubro' es 'Viajes'
    total_viajes = fields.Float(string='Total Viajes', compute='_compute_total_viajes')
    total_materiales_insumos = fields.Float(string='Total Materiales y Insumos', compute='_compute_total_materiales')
    total_persona_cientifica = fields.Float(string='PERSONAL CIENTÍFICO', compute='_compute_total_persona_cientifica')
    total_tecnico = fields.Float(string='SERVICIOS TÉCNICOS', compute='_compute_total_tecnico')
    total_adquisicion = fields.Float(string='ADQUISICIÓN DE EQUIPOS', compute='_compute_total_adquisicion')
    total_adecuacion = fields.Float(string='ADECUACIÓN DE INFRAESTRUCTURA', compute='_compute_total_adecuacion')
    total_software = fields.Float(string='SOFTWARE', compute='_compute_total_software')
    total_salida = fields.Float(string='SALIDAS DE CAMPO', compute='_compute_total_salida')
    total_difusion = fields.Float(string='DIFUSIÓN DE RESULTADOS', compute='_compute_total_difusion')
    total_gastos = fields.Float(string='GASTOS DE PROPIEDAD INTELECTUAL', compute='_compute_total_gastos')
    total_biblo = fields.Float(string='BIBLIOGRAFÍA', compute='_compute_total_biblo')
    total_adminis = fields.Float(string='GASTOS DE ADMINISTRACIÓN', compute='_compute_total_adminis')
    total_ctei = fields.Float(string='DIVULGACIÓN Y APROPIACIÓN DE CTEI', compute='_compute_total_ctei')
    total_personal = fields.Float(string='PERSONAL DE APOYO', compute='_compute_total_personal')
    total_capacitaciones = fields.Float(string='CAPACITACIONES', compute='_compute_total_capacitaciones')
    total_convenio = fields.Float(string='CONVENIO ESPECIAL DE COOPERACIÓN TÉC', compute='_compute_total_convenio')
    total_certificacion = fields.Float(string='CERTIFICACIONES', compute='_compute_total_certificaion')
    total_consultoria = fields.Float(string='CONSULTORÍA ESPECIALIZADA', compute='_compute_total_consultoria')
    total_serviciosTec = fields.Float(string='SERVICIOS TÉCNOLÓGICOS', compute='_compute_total_serviciosTec')

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_viajes(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'Viajes' relacionadas con la misma actividad
            lineas_viajes = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'Viajes')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'Viajes'
            record.total_viajes = sum(lineas_viajes.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_materiales(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'materiales' relacionadas con la misma actividad
            lineas_materiales = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'materiales')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'materiales'
            record.total_materiales_insumos = sum(lineas_materiales.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_persona_cientifica(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'personalcin' relacionadas con la misma actividad
            lineas_personalcin = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'personalcin')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'personalcin'
            record.total_persona_cientifica = sum(lineas_personalcin.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_tecnico(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'personalcin' relacionadas con la misma actividad
            lineas_tecnico = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'tecnico')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'personalcin'
            record.total_tecnico = sum(lineas_tecnico.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_adquisicion(self):
        for record in self:
            lineas_adquisicion = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'adquisicion')
            record.total_adquisicion = sum(lineas_adquisicion.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_software(self):
        for record in self:
            lineas_software = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'software')
            record.total_software = sum(lineas_software.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_salida(self):
        for record in self:
            lineas_salida = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'salida')
            record.total_salida = sum(lineas_salida.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_difusion(self):
        for record in self:
            lineas_difusion = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'difusion')
            record.total_difusion = sum(lineas_difusion.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_gastos(self):
        for record in self:
            lineas_gastos = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'gastos')
            record.total_gastos = sum(lineas_gastos.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_biblo(self):
        for record in self:
            lineas_biblo = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'biblo')
            record.total_biblo = sum(lineas_biblo.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_adminis(self):
        for record in self:
            lineas_adminis = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'adminis')
            record.total_adminis = sum(lineas_adminis.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_ctei(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'ctei' relacionadas con la misma actividad
            lineas_ctei = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'ctei')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'ctei'
            record.total_ctei = sum(lineas_ctei.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_personal(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'personal' relacionadas con la misma actividad
            lineas_personal = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'personal')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'personal'
            record.total_personal = sum(lineas_personal.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_capacitaciones(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'capacitaciones' relacionadas con la misma actividad
            lineas_capacitaciones = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'capacitaciones')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'capacitaciones'
            record.total_capacitaciones = sum(lineas_capacitaciones.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_convenio(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'convenio' relacionadas con la misma actividad
            lineas_convenio = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'convenio')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'convenio'
            record.total_convenio = sum(lineas_convenio.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_certificaion(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'certificaion' relacionadas con la misma actividad
            lineas_certificaion = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'certificaion')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'certificaion'
            record.total_certificacion = sum(lineas_certificaion.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_consultoria(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'consultoria' relacionadas con la misma actividad
            lineas_consultoria = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'consultoria')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'consultoria'
            record.total_consultoria = sum(lineas_consultoria.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_serviciosTec(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'serviciosTec' relacionadas con la misma actividad
            lineas_serviciosTec = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'serviciosTec')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'serviciosTec'
            record.total_serviciosTec = sum(lineas_serviciosTec.mapped('total'))

    @api.depends('actividad_id.line_ids.total', 'actividad_id.line_ids.rubro')
    def _compute_total_adecuacion(self):
        for record in self:
            # Obtener todas las líneas de actividad con rubro 'serviciosTec' relacionadas con la misma actividad
            lineas_adecuacion = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'adecuacion')

            # Sumar los valores de 'total' de las líneas de actividad con rubro 'serviciosTec'
            record.total_adecuacion = sum(lineas_adecuacion.mapped('total'))

    item = fields.Char(string='Item', tracking=True, readonly=False)

    rubro = fields.Selection([
        ('personalcin', 'PERSONAL CIENTÍFICO'),
        ('tecnico', 'SERVICIOS TÉCNICOS'),
        ('adquisicion', 'ADQUISICIÓN DE EQUIPOS'),
        ('materiales', 'MATERIALES E INSUMOS'),
        ('adecuacion', 'ADECUACIÓN DE INFRAESTRUCTURA'),
        ('software', 'SOFTWARE'),
        ('salida', 'SALIDAS DE CAMPO'),
        ('difusion', 'DIFUSIÓN DE RESULTADOS'),
        ('Viajes', 'VIAJES'),
        ('gastos', 'GASTOS DE PROPIEDAD INTELECTUAL'),
        ('biblo', 'BIBLIOGRAFÍA'),
        ('adminis', 'GASTOS DE ADMINISTRACIÓN'),
        ('ctei', 'DIVULGACIÓN Y APROPIACIÓN DE CTEI'),
        ('personal', 'PERSONAL DE APOYO'),
        ('capacitaciones', 'CAPACITACIONES'),
        ('convenio', 'CONVENIO ESPECIAL DE COOPERACIÓN TÉC'),
        ('certificaion', 'CERTIFICACIONES'),
        ('consultoria', 'CONSULTORÍA ESPECIALIZADA'),
        ('serviciosTec', 'SERVICIOS TÉCNOLÓGICOS'),
    ], string='Rubro', store=True, tracking=True, readonly=False)

    #####################################################################################################

    rubro_editables = fields.Char(string='Agregar rubro')

    #####################################################################################################

    tipo_requerimiento = fields.Selection([
        ('servicio', 'Servicio '),
        ('bien', 'Bien'),
    ], string='Tipo Requerimiento', tracking=True, readonly=False)
    descripcion_gasto = fields.Char(string='Descripción del Gasto', tracking=True,
                                    readonly=False)
    fecha_solicitud = fields.Date(string='Fecha de Solicitud', tracking=True,
                                  readonly=False)
    fecha_requerida = fields.Date(string='Fecha Requerida', readonly=False)
    lugar_entrega = fields.Char(string='Lugar y/o Forma de Entrega', tracking=True,
                                readonly=False)
    cantidad = fields.Integer(string='Cantidad', tracking=True, readonly=False)
    tiempo_ejecucion_dias = fields.Integer(string='Tiempo de Ejecución (Días)', tracking=True,
                                           readonly=False)
    proveedor = fields.Char(string='Proveedor', tracking=True, readonly=False)
    nit = fields.Char(string='NIT', tracking=True, readonly=False)
    tipo_contractual = fields.Char(string='Tipo Contractual', tracking=True,
                                   readonly=False)
    valor_estimado = fields.Float(string='Valor Estimado (Requerimiento)', tracking=True,
                                  readonly=False)

    valor_ejecutado = fields.Float(string='Valor Ejecutado', tracking=True, readonly=False)
    impuesto_asumido_proyecto = fields.Float(string='Impuesto Asumido por el Proyecto', tracking=True, readonly=False)
    total = fields.Float(string='Total', compute='_compute_total', tracking=True, readonly=False, )

    @api.depends('valor_ejecutado', 'impuesto_asumido_proyecto')
    def _compute_total(self):
        for record in self:
            record.total = record.valor_ejecutado + record.impuesto_asumido_proyecto

    total_general = fields.Float(string='Total General', related='actividad_id.total_general', store=True,
                                 tracking=True)

    estado = fields.Selection([
        ('pendiente', 'PENDIENTE '),
        ('en_proceso', 'EJECUTADO'),
        ('completado', 'COMPLETADO'),
        ('solicitado', 'SOLICITADO')
    ], string='Estado', tracking=True, readonly=False)

    valor_ejecutado_total = fields.Float(string='Valor Ejecutado Total', related='actividad_id.valor_ejecutado_total',
                                         store=True, tracking=True)


class DatosAdicionales(models.Model):
    _name = 'datos_adicionales'
    actividad_id = fields.Many2one('actividad', string='Actividad')
    _inherit = ['mail.thread', 'mail.activity.mixin']

    otros_rubros = fields.Many2one(comodel_name='rubros', string="Otros rubro")

    rubro = fields.Selection([
        ('personalcin', 'PERSONAL CIENTÍFICO'),
        ('tecnico', 'SERVICIOS TÉCNICOS'),
        ('adquisicion', 'ADQUISICIÓN DE EQUIPOS'),
        ('materiales', 'MATERIALES E INSUMOS'),
        ('adecuacion', 'ADECUACIÓN DE INFRAESTRUCTURA'),
        ('software', 'SOFTWARE'),
        ('salida', 'SALIDAS DE CAMPO'),
        ('difusion', 'DIFUSIÓN DE RESULTADOS'),
        ('Viajes', 'VIAJES'),
        ('gastos', 'GASTOS DE PROPIEDAD INTELECTUAL'),
        ('biblo', 'BIBLIOGRAFÍA'),
        ('adminis', 'GASTOS DE ADMINISTRACIÓN'),
        ('ctei', 'DIVULGACIÓN Y APROPIACIÓN DE CTEI'),
        ('personal', 'PERSONAL DE APOYO'),
        ('capacitaciones', 'CAPACITACIONES'),
        ('convenio', 'CONVENIO ESPECIAL DE COOPERACIÓN TÉC'),
        ('certificaion', 'CERTIFICACIONES'),
        ('consultoria', 'CONSULTORÍA ESPECIALIZADA'),
        ('serviciosTec', 'SERVICIOS TÉCNOLÓGICOS'),
    ], string='Rubro', tracking=True)

    actividad_global = fields.Float(string="ACTIVIDAD", tracking=True)
    ejecutado_act_global = fields.Float(string='Ejecutado Act', compute='_compute_ejecutado_act_global', tracking=True)

    ##############################################################################################################
    rubro_editables = fields.Char(string='Agregar rubro')

    actividad = fields.Float(string="ACTIVIDAD")
    total_actividad = fields.Float(string="Total Actividad", compute='_compute_total_actividad')

    @api.depends('actividad')
    def _compute_total_actividad(self):
        for record in self:
            registros_actividad = self.search([('actividad_id', '=', record.actividad_id.id)]).mapped('actividad')
            record.total_actividad = sum(registros_actividad)

    ejecutado_act = fields.Float(string='Ejecutado Act')
    total_ejecutado = fields.Float(string='Total Ejecutado', compute='_compute_total_ejecutado')

    @api.depends('ejecutado_act')
    def _compute_total_ejecutado(self):
        for record in self:
            registros_ejecutado = self.search([('actividad_id', '=', record.actividad_id.id)]).mapped('ejecutado_act')
            record.total_ejecutado = sum(registros_ejecutado)

    dispo_act_editable = fields.Float(string="DISPO ACT", compute='_compute_dispo_act_editable')

    @api.depends('actividad', 'ejecutado_act')
    def _compute_dispo_act_editable(self):
        for record in self:
            record.dispo_act_editable = record.actividad - record.ejecutado_act

    #####################################################################################################

    @api.depends('rubro', 'actividad_id.line_ids.total_viajes', 'actividad_id.line_ids.total_materiales_insumos',
                 'actividad_id.line_ids.total_persona_cientifica', 'actividad_id.line_ids.total_tecnico',
                 'actividad_id.line_ids.total_adquisicion', 'actividad_id.line_ids.total_adecuacion',
                 'actividad_id.line_ids.total_software', 'actividad_id.line_ids.total_difusion',
                 'actividad_id.line_ids.total_gastos', 'actividad_id.line_ids.total_biblo',
                 'actividad_id.line_ids.total_adminis', 'actividad_id.line_ids.total_ctei',
                 'actividad_id.line_ids.total_personal', 'actividad_id.line_ids.total_capacitaciones',
                 'actividad_id.line_ids.total_convenio', 'actividad_id.line_ids.total_certificacion',
                 'actividad_id.line_ids.total_consultoria', 'actividad_id.line_ids.total_serviciosTec')
    def _compute_ejecutado_act_global(self):
        for record in self:
            if record.rubro == 'Viajes':
                # Filtra las líneas con el rubro 'Viajes'
                viajes_line = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'Viajes' and x.total_viajes)
                # Obtiene el valor total_viajes de la primera línea encontrada (si existe)
                record.ejecutado_act_global = viajes_line[0].total_viajes if viajes_line else 0.0
            elif record.rubro == 'materiales':
                # Filtra las líneas con el rubro 'Materiales'
                materiales_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'materiales' and x.total_materiales_insumos)
                # Obtiene el valor total_materiales_insumos de la primera línea encontrada (si existe)
                record.ejecutado_act_global = materiales_line[0].total_materiales_insumos if materiales_line else 0.0
            elif record.rubro == 'personalcin':
                # Filtra las líneas con el rubro 'Personal Científico'
                personal_cientifico_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'personalcin' and x.total_persona_cientifica)
                # Obtiene el valor total_persona_cientifica de la primera línea encontrada (si existe)
                record.ejecutado_act_global = personal_cientifico_line[
                    0].total_persona_cientifica if personal_cientifico_line else 0.0
            elif record.rubro == 'tecnico':
                # Filtra las líneas con el rubro 'Técnico'
                tecnico_line = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'tecnico' and x.total_tecnico)
                # Obtiene el valor total_tecnico de la primera línea encontrada (si existe)
                record.ejecutado_act_global = tecnico_line[0].total_tecnico if tecnico_line else 0.0
            elif record.rubro == 'adquisicion':
                # Filtra las líneas con el rubro 'Adquisición'
                adquisicion_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'adquisicion' and x.total_adquisicion)
                # Obtiene el valor total_adquisicion de la primera línea encontrada (si existe)
                record.ejecutado_act_global = adquisicion_line[0].total_adquisicion if adquisicion_line else 0.0
            elif record.rubro == 'adecuacion':
                # Filtra las líneas con el rubro 'Adecuación'
                adecuacion_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'adecuacion' and x.total_adecuacion)
                # Obtiene el valor total_adecuacion de la primera línea encontrada (si existe)
                record.ejecutado_act_global = adecuacion_line[0].total_adecuacion if adecuacion_line else 0.0
            elif record.rubro == 'software':
                # Filtra las líneas con el rubro 'Software'
                software_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'software' and x.total_software)
                # Obtiene el valor total_software de la primera línea encontrada (si existe)
                record.ejecutado_act_global = software_line[0].total_software if software_line else 0.0
            elif record.rubro == 'salida':
                # Filtra las líneas con el rubro 'Salida'
                salida_line = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'salida' and x.total_salida)
                # Obtiene el valor total_salida de la primera línea encontrada (si existe)
                record.ejecutado_act_global = salida_line[0].total_salida if salida_line else 0.0
            elif record.rubro == 'difusion':
                # Filtra las líneas con el rubro 'Difusión'
                difusion_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'difusion' and x.total_difusion)
                # Obtiene el valor total_difusion de la primera línea encontrada (si existe)
                record.ejecutado_act_global = difusion_line[0].total_difusion if difusion_line else 0.0
            elif record.rubro == 'gastos':
                # Filtra las líneas con el rubro 'Gastos'
                gastos_line = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'gastos' and x.total_gastos)
                # Obtiene el valor total_gastos de la primera línea encontrada (si existe)
                record.ejecutado_act_global = gastos_line[0].total_gastos if gastos_line else 0.0
            elif record.rubro == 'biblo':
                # Filtra las líneas con el rubro 'Bibliografía'
                biblo_line = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'biblo' and x.total_biblo)
                # Obtiene el valor total_biblo de la primera línea encontrada (si existe)
                record.ejecutado_act_global = biblo_line[0].total_biblo if biblo_line else 0.0
            elif record.rubro == 'adminis':
                # Filtra las líneas con el rubro 'Administración'
                adminis_line = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'adminis' and x.total_adminis)
                # Obtiene el valor total_adminis de la primera línea encontrada (si existe)
                record.ejecutado_act_global = adminis_line[0].total_adminis if adminis_line else 0.0
            elif record.rubro == 'ctei':
                # Filtra las líneas con el rubro 'CTEI'
                ctei_line = record.actividad_id.line_ids.filtered(lambda x: x.rubro == 'ctei' and x.total_ctei)
                # Obtiene el valor total_ctei de la primera línea encontrada (si existe)
                record.ejecutado_act_global = ctei_line[0].total_ctei if ctei_line else 0.0
            elif record.rubro == 'personal':
                # Filtra las líneas con el rubro 'Personal'
                personal_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'personal' and x.total_personal)
                # Obtiene el valor total_personal de la primera línea encontrada (si existe)
                record.ejecutado_act_global = personal_line[0].total_personal if personal_line else 0.0
            elif record.rubro == 'capacitaciones':
                # Filtra las líneas con el rubro 'Capacitaciones'
                capacitaciones_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'capacitaciones' and x.total_capacitaciones)
                # Obtiene el valor total_capacitaciones de la primera línea encontrada (si existe)
                record.ejecutado_act_global = capacitaciones_line[
                    0].total_capacitaciones if capacitaciones_line else 0.0
            elif record.rubro == 'convenio':
                # Filtra las líneas con el rubro 'Convenio'
                convenio_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'convenio' and x.total_convenio)
                # Obtiene el valor total_convenio de la primera línea encontrada (si existe)
                record.ejecutado_act_global = convenio_line[0].total_convenio if convenio_line else 0.0
            elif record.rubro == 'certificaion':
                # Filtra las líneas con el rubro 'Certificación'
                certificacion_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'certificaion' and x.total_certificacion)
                # Obtiene el valor total_certificacion de la primera línea encontrada (si existe)
                record.ejecutado_act_global = certificacion_line[0].total_certificacion if certificacion_line else 0.0
            elif record.rubro == 'consultoria':
                # Filtra las líneas con el rubro 'Consultoría'
                consultoria_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'consultoria' and x.total_consultoria)
                # Obtiene el valor total_consultoria de la primera línea encontrada (si existe)
                record.ejecutado_act_global = consultoria_line[0].total_consultoria if consultoria_line else 0.0
            elif record.rubro == 'serviciosTec':
                # Filtra las líneas con el rubro 'ServiciosTec'
                serviciosTec_line = record.actividad_id.line_ids.filtered(
                    lambda x: x.rubro == 'serviciosTec' and x.total_serviciosTec)
                # Obtiene el valor total_serviciosTec de la primera línea encontrada (si existe)
                record.ejecutado_act_global = serviciosTec_line[0].total_serviciosTec if serviciosTec_line else 0.0
            else:
                record.ejecutado_act_global = 0.0

    dispo_act = fields.Float(string="DISPO ACT", compute='_compute_dispo_act', tracking=True)

    @api.depends('actividad_global', 'ejecutado_act_global')
    def _compute_dispo_act(self):
        for record in self:
            record.dispo_act = record.actividad_global - record.ejecutado_act_global




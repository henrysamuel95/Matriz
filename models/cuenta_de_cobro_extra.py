import uuid
import logging
import os
import base64
import re
from docx import Document
from io import BytesIO
from docxtpl import DocxTemplate
from datetime import date, timedelta
from docx.shared import Inches
from odoo import models, fields, api
from odoo.http import request
from .drive import GoogleDriveAuth
from .otro_si_aprobaciones import ESTADOS_OTRO_SI_NUEVOS
from odoo.exceptions import ValidationError
from odoo.exceptions import UserError, AccessError

_logger = logging.getLogger(__name__)


def _normalize_document_for_match(value):
    """Normaliza cédulas/NIT solo para filtros/comparaciones; no cambia el dato guardado."""
    return re.sub(r'\D+', '', str(value or ''))


class Login(models.Model):
    _name = 'login'
    _description = 'Login'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    _PARAM_RESTRINGIR_FECHAS_CUENTA_COBRO = 'Matriz.restringir_fechas_cuenta_cobro'
    _PARAM_DIA_INICIO_NATURAL = 'Matriz.cuenta_cobro_dia_inicio_natural'
    _PARAM_DIA_CIERRE_NATURAL = 'Matriz.cuenta_cobro_dia_cierre_natural'
    _PARAM_HORA_CIERRE_NATURAL = 'Matriz.cuenta_cobro_hora_cierre_natural'
    _PARAM_DIA_INICIO_JURIDICA = 'Matriz.cuenta_cobro_dia_inicio_juridica'
    _PARAM_DIA_CIERRE_JURIDICA = 'Matriz.cuenta_cobro_dia_cierre_juridica'
    _PARAM_HORA_CIERRE_JURIDICA = 'Matriz.cuenta_cobro_hora_cierre_juridica'
    _PARAM_RESTRINGIR_FECHAS_REGALIAS = 'Matriz.restringir_fechas_cuentas_regalias'
    _PARAM_DIA_INICIO_REGALIAS = 'Matriz.cuenta_regalias_dia_inicio'
    _PARAM_DIA_CIERRE_REGALIAS = 'Matriz.cuenta_regalias_dia_cierre'
    _PARAM_HORA_CIERRE_REGALIAS = 'Matriz.cuenta_regalias_hora_cierre'
    _PARAM_RESTRINGIR_ENVIO_VENTANILLA = 'Matriz.restringir_envio_ventanilla'
    _PARAM_DIA_INICIO_ENVIO_VENTANILLA = 'Matriz.envio_ventanilla_dia_inicio'
    _PARAM_DIA_CIERRE_ENVIO_VENTANILLA = 'Matriz.envio_ventanilla_dia_cierre'
    _PARAM_HORA_CIERRE_ENVIO_VENTANILLA = 'Matriz.envio_ventanilla_hora_cierre'

    def message_post(self, **kwargs):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        is_public_request = bool(
            request and hasattr(request, 'httprequest') and request.httprequest.path.startswith('/public/')
        )
        if not portal_login_id and is_public_request and len(self) == 1:
            portal_login_id = self.id

        if portal_login_id:
            login = self.sudo().browse(portal_login_id)
            if login.exists():
                partner = login._get_portal_chatter_partner()
                if partner:
                    kwargs['author_id'] = partner.id
        return super(Login, self).message_post(**kwargs)

    def _reassign_portal_tracking_author(self, since):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        is_public_request = bool(
            request and hasattr(request, 'httprequest') and request.httprequest.path.startswith('/public/')
        )
        if not portal_login_id and is_public_request and len(self) == 1:
            portal_login_id = self.id
        if not portal_login_id:
            return

        login = self.sudo().browse(portal_login_id)
        if not login.exists():
            return
        partner = login._get_portal_chatter_partner()
        if not partner:
            return

        messages = self.env['mail.message'].sudo().search([
            ('model', '=', self._name),
            ('res_id', 'in', self.ids),
            ('date', '>=', since - timedelta(seconds=3)),
            ('author_id', '!=', partner.id),
        ])
        if messages:
            messages.write({'author_id': partner.id})

    name = fields.Char(string='Nombre', tracking=True)
    coreo = fields.Char(string="Correo", tracking=True)
    documento_de_identidad = fields.Char('Documento')
    portal_chat_message_ids = fields.One2many('portal.chat.message', 'login_id', string='Chat de soporte')
    cuentas_de_cobro_ids = fields.One2many('cuentas_de_cobros', 'login_id', string='Cuentas de cobro')
    restringir_fechas_cuenta_cobro = fields.Boolean(
        string='Aplicar fechas para crear cuentas de cobro',
        compute='_compute_restringir_fechas_cuenta_cobro',
        inverse='_inverse_restringir_fechas_cuenta_cobro',
        groups='Matriz.group_soporte',
        help=(
            'Control global. Al activarlo, el portal aplicará los días de apertura '
            'y cierre configurados por Soporte para cada tipo de persona.'
        ),
    )
    habilitar_cuenta_fuera_de_fecha = fields.Boolean(
        string='Permitir cuenta fuera de fecha',
        default=False,
        tracking=True,
        groups='Matriz.group_soporte',
        help='Excepción individual para que este usuario pueda crear una cuenta fuera del rango mensual.',
    )
    habilitar_cuentas_regalias = fields.Boolean(
        string='Excepción Regalías para este usuario',
        default=False,
        tracking=True,
        groups='Matriz.group_soporte',
        help=(
            'Excepción individual para permitir que este usuario cree cuentas '
            'de Regalías aunque la ventana global de fechas esté cerrada.'
        ),
    )
    restringir_fechas_cuentas_regalias = fields.Boolean(
        string='Aplicar fechas para Regalías',
        compute='_compute_restringir_fechas_cuentas_regalias',
        inverse='_inverse_restringir_fechas_cuentas_regalias',
        groups='Matriz.group_soporte',
        help=(
            'Control global. Al activarlo, el portal permitirá seleccionar '
            'Regalías solo entre el día de apertura y el día de cierre configurados.'
        ),
    )
    dia_inicio_cuentas_regalias = fields.Integer(
        string='Día de apertura - Regalías',
        compute='_compute_dias_cuentas_regalias',
        inverse='_inverse_dia_inicio_cuentas_regalias',
        groups='Matriz.group_soporte',
    )
    dia_cierre_cuentas_regalias = fields.Integer(
        string='Día de cierre - Regalías',
        compute='_compute_dias_cuentas_regalias',
        inverse='_inverse_dia_cierre_cuentas_regalias',
        groups='Matriz.group_soporte',
    )
    hora_cierre_cuentas_regalias = fields.Float(
        string='Hora de cierre - Regalías',
        compute='_compute_dias_cuentas_regalias',
        inverse='_inverse_hora_cierre_cuentas_regalias',
        groups='Matriz.group_soporte',
        help='Hora de cierre del último día permitido. Ejemplo: 17:00 = 5:00 PM.',
    )
    habilitar_envio_ventanilla_fuera_plazo = fields.Boolean(
        string='Excepción individual para Ventanilla',
        default=False,
        tracking=True,
        groups='Matriz.group_soporte',
        help=(
            'Excepción individual. Permite que las cuentas de este usuario puedan '
            'ser enviadas a Ventanilla Única aunque el cierre mensual ya se haya cumplido.'
        ),
    )
    restringir_envio_ventanilla = fields.Boolean(
        string='Aplicar cierre de recepción en Ventanilla Única',
        compute='_compute_restringir_envio_ventanilla',
        inverse='_inverse_restringir_envio_ventanilla',
        groups='Matriz.group_soporte',
        help=(
            'Control global. Cuando está activo, ninguna cuenta que aún no haya ingresado '
            'a Ventanilla podrá radicarse después del día y hora de cierre configurados. '
            'También aplica a cuentas del Sistema General de Regalías.'
        ),
    )
    dia_inicio_envio_ventanilla = fields.Integer(
        string='Día de apertura de Ventanilla - FUV normal',
        compute='_compute_cierre_envio_ventanilla',
        inverse='_inverse_dia_inicio_envio_ventanilla',
        groups='Matriz.group_soporte',
    )
    dia_cierre_envio_ventanilla = fields.Integer(
        string='Día de cierre de Ventanilla',
        compute='_compute_cierre_envio_ventanilla',
        inverse='_inverse_dia_cierre_envio_ventanilla',
        groups='Matriz.group_soporte',
    )
    hora_cierre_envio_ventanilla = fields.Float(
        string='Hora de cierre de Ventanilla',
        compute='_compute_cierre_envio_ventanilla',
        inverse='_inverse_hora_cierre_envio_ventanilla',
        groups='Matriz.group_soporte',
        help='Hora en formato de 24 horas. 17:00 corresponde a las 5:00 PM.',
    )
    dia_inicio_cuenta_natural = fields.Integer(
        string='Día de apertura - Persona Natural',
        compute='_compute_dias_creacion_cuenta',
        inverse='_inverse_dia_inicio_cuenta_natural',
        groups='Matriz.group_soporte',
    )
    dia_cierre_cuenta_natural = fields.Integer(
        string='Día de cierre - Persona Natural',
        compute='_compute_dias_creacion_cuenta',
        inverse='_inverse_dia_cierre_cuenta_natural',
        groups='Matriz.group_soporte',
    )
    hora_cierre_cuenta_natural = fields.Float(
        string='Hora de cierre - Persona Natural',
        compute='_compute_dias_creacion_cuenta',
        inverse='_inverse_hora_cierre_cuenta_natural',
        groups='Matriz.group_soporte',
        help='Hora de cierre del último día permitido. Ejemplo: 17:00 = 5:00 PM.',
    )
    dia_inicio_cuenta_juridica = fields.Integer(
        string='Día de apertura - Persona Jurídica',
        compute='_compute_dias_creacion_cuenta',
        inverse='_inverse_dia_inicio_cuenta_juridica',
        groups='Matriz.group_soporte',
    )
    dia_cierre_cuenta_juridica = fields.Integer(
        string='Día de cierre - Persona Jurídica',
        compute='_compute_dias_creacion_cuenta',
        inverse='_inverse_dia_cierre_cuenta_juridica',
        groups='Matriz.group_soporte',
    )
    hora_cierre_cuenta_juridica = fields.Float(
        string='Hora de cierre - Persona Jurídica',
        compute='_compute_dias_creacion_cuenta',
        inverse='_inverse_hora_cierre_cuenta_juridica',
        groups='Matriz.group_soporte',
        help='Hora de cierre del último día permitido. Ejemplo: 17:00 = 5:00 PM.',
    )
    proyectos_con_contrato = fields.Char(
        string='Proyectos con contrato',
        compute='_compute_resumen_cuentas_de_cobro',
        store=True,
    )
    contratos_relacionados = fields.Char(
        string='Contratos relacionados',
        compute='_compute_resumen_cuentas_de_cobro',
        store=True,
    )

    def _compute_restringir_fechas_cuenta_cobro(self):
        activo = self.env['ir.config_parameter'].sudo().get_param(
            self._PARAM_RESTRINGIR_FECHAS_CUENTA_COBRO,
            'False',
        ) in ('True', '1', True)
        for record in self:
            record.restringir_fechas_cuenta_cobro = activo

    def _inverse_restringir_fechas_cuenta_cobro(self):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo el rol de Soporte puede modificar el control de fechas.')
        activo = any(self.mapped('restringir_fechas_cuenta_cobro'))
        self.env['ir.config_parameter'].sudo().set_param(
            self._PARAM_RESTRINGIR_FECHAS_CUENTA_COBRO,
            'True' if activo else 'False',
        )

    def _compute_restringir_envio_ventanilla(self):
        activo = self.env['ir.config_parameter'].sudo().get_param(
            self._PARAM_RESTRINGIR_ENVIO_VENTANILLA,
            'True',
        ) in ('True', '1', True)
        for record in self:
            record.restringir_envio_ventanilla = activo

    def _inverse_restringir_envio_ventanilla(self):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo el rol de Soporte puede modificar el cierre de Ventanilla Única.')
        activo = any(self.mapped('restringir_envio_ventanilla'))
        self.env['ir.config_parameter'].sudo().set_param(
            self._PARAM_RESTRINGIR_ENVIO_VENTANILLA,
            'True' if activo else 'False',
        )

    def _compute_cierre_envio_ventanilla(self):
        params = self.env['ir.config_parameter'].sudo()
        dia_inicio = self._valor_dia_configurado(self._PARAM_DIA_INICIO_ENVIO_VENTANILLA, 1)
        try:
            dia = int(params.get_param(self._PARAM_DIA_CIERRE_ENVIO_VENTANILLA, '17'))
        except (TypeError, ValueError):
            dia = 17
        try:
            hora = float(params.get_param(self._PARAM_HORA_CIERRE_ENVIO_VENTANILLA, '17.0'))
        except (TypeError, ValueError):
            hora = 17.0
        dia = dia if 1 <= dia <= 31 else 17
        hora = hora if 0.0 <= hora < 24.0 else 17.0

        for record in self:
            record.dia_inicio_envio_ventanilla = dia_inicio
            record.dia_cierre_envio_ventanilla = dia
            record.hora_cierre_envio_ventanilla = hora

    def _inverse_dia_inicio_envio_ventanilla(self):
        self._guardar_dia_configurado(
            'dia_inicio_envio_ventanilla',
            self._PARAM_DIA_INICIO_ENVIO_VENTANILLA,
        )

    def _inverse_dia_cierre_envio_ventanilla(self):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo el rol de Soporte puede modificar el cierre de Ventanilla Única.')
        valores = set(self.mapped('dia_cierre_envio_ventanilla'))
        if len(valores) != 1:
            raise ValidationError('Define un único día de cierre para Ventanilla Única.')
        dia = valores.pop()
        if not 1 <= dia <= 31:
            raise ValidationError('El día de cierre de Ventanilla debe estar entre 1 y 31.')
        self.env['ir.config_parameter'].sudo().set_param(self._PARAM_DIA_CIERRE_ENVIO_VENTANILLA, str(dia))

    def _inverse_hora_cierre_envio_ventanilla(self):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo el rol de Soporte puede modificar el cierre de Ventanilla Única.')
        valores = set(self.mapped('hora_cierre_envio_ventanilla'))
        if len(valores) != 1:
            raise ValidationError('Define una única hora de cierre para Ventanilla Única.')
        hora = valores.pop()
        if not 0.0 <= hora < 24.0:
            raise ValidationError('La hora de cierre debe estar entre 00:00 y 23:59.')
        self.env['ir.config_parameter'].sudo().set_param(self._PARAM_HORA_CIERRE_ENVIO_VENTANILLA, str(hora))

    def _compute_restringir_fechas_cuentas_regalias(self):
        activo = self.env['ir.config_parameter'].sudo().get_param(
            self._PARAM_RESTRINGIR_FECHAS_REGALIAS,
            'False',
        ) in ('True', '1', True)
        for record in self:
            record.restringir_fechas_cuentas_regalias = activo

    def _inverse_restringir_fechas_cuentas_regalias(self):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo el rol de Soporte puede modificar el control de fechas de Regalías.')
        activo = any(self.mapped('restringir_fechas_cuentas_regalias'))
        self.env['ir.config_parameter'].sudo().set_param(
            self._PARAM_RESTRINGIR_FECHAS_REGALIAS,
            'True' if activo else 'False',
        )

    def _valor_dia_configurado(self, parametro, valor_predeterminado):
        valor = self.env['ir.config_parameter'].sudo().get_param(
            parametro, str(valor_predeterminado)
        )
        try:
            dia = int(valor)
        except (TypeError, ValueError):
            dia = valor_predeterminado
        return dia if 1 <= dia <= 31 else valor_predeterminado

    def _valor_hora_configurada(self, parametro, valor_predeterminado=0.0):
        valor = self.env['ir.config_parameter'].sudo().get_param(
            parametro, str(valor_predeterminado)
        )
        try:
            hora = float(valor)
        except (TypeError, ValueError):
            hora = valor_predeterminado
        return hora if 0.0 <= hora < 24.0 else valor_predeterminado

    def _compute_dias_creacion_cuenta(self):
        inicio_natural = self._valor_dia_configurado(self._PARAM_DIA_INICIO_NATURAL, 1)
        cierre_natural = self._valor_dia_configurado(self._PARAM_DIA_CIERRE_NATURAL, 17)
        hora_cierre_natural = self._valor_hora_configurada(self._PARAM_HORA_CIERRE_NATURAL)
        inicio_juridica = self._valor_dia_configurado(self._PARAM_DIA_INICIO_JURIDICA, 1)
        cierre_juridica = self._valor_dia_configurado(self._PARAM_DIA_CIERRE_JURIDICA, 20)
        hora_cierre_juridica = self._valor_hora_configurada(self._PARAM_HORA_CIERRE_JURIDICA)
        for record in self:
            record.dia_inicio_cuenta_natural = inicio_natural
            record.dia_cierre_cuenta_natural = cierre_natural
            record.hora_cierre_cuenta_natural = hora_cierre_natural
            record.dia_inicio_cuenta_juridica = inicio_juridica
            record.dia_cierre_cuenta_juridica = cierre_juridica
            record.hora_cierre_cuenta_juridica = hora_cierre_juridica

    def _guardar_dia_configurado(self, campo, parametro):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo el rol de Soporte puede modificar las fechas de creación.')
        valores = set(self.mapped(campo))
        if len(valores) != 1:
            raise ValidationError('Define un único día para este control global.')
        dia = valores.pop()
        if not 1 <= dia <= 31:
            raise ValidationError('El día de apertura o cierre debe estar entre 1 y 31.')
        self.env['ir.config_parameter'].sudo().set_param(parametro, str(dia))

    def _inverse_dia_inicio_cuenta_natural(self):
        self._guardar_dia_configurado('dia_inicio_cuenta_natural', self._PARAM_DIA_INICIO_NATURAL)

    def _inverse_dia_cierre_cuenta_natural(self):
        self._guardar_dia_configurado('dia_cierre_cuenta_natural', self._PARAM_DIA_CIERRE_NATURAL)

    def _guardar_hora_configurada(self, campo, parametro):
        if not self.env.su and not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo el rol de Soporte puede modificar las horas de cierre.')
        valores = set(self.mapped(campo))
        if len(valores) != 1:
            raise ValidationError('Define una única hora para este control global.')
        hora = valores.pop()
        if not 0.0 <= hora < 24.0:
            raise ValidationError('La hora de cierre debe estar entre 00:00 y 23:59.')
        self.env['ir.config_parameter'].sudo().set_param(parametro, str(hora))

    def _inverse_hora_cierre_cuenta_natural(self):
        self._guardar_hora_configurada('hora_cierre_cuenta_natural', self._PARAM_HORA_CIERRE_NATURAL)

    def _inverse_dia_inicio_cuenta_juridica(self):
        self._guardar_dia_configurado('dia_inicio_cuenta_juridica', self._PARAM_DIA_INICIO_JURIDICA)

    def _inverse_dia_cierre_cuenta_juridica(self):
        self._guardar_dia_configurado('dia_cierre_cuenta_juridica', self._PARAM_DIA_CIERRE_JURIDICA)

    def _inverse_hora_cierre_cuenta_juridica(self):
        self._guardar_hora_configurada('hora_cierre_cuenta_juridica', self._PARAM_HORA_CIERRE_JURIDICA)

    def _compute_dias_cuentas_regalias(self):
        dia_inicio = self._valor_dia_configurado(self._PARAM_DIA_INICIO_REGALIAS, 1)
        dia_cierre = self._valor_dia_configurado(self._PARAM_DIA_CIERRE_REGALIAS, 31)
        hora_cierre = self._valor_hora_configurada(self._PARAM_HORA_CIERRE_REGALIAS)
        for record in self:
            record.dia_inicio_cuentas_regalias = dia_inicio
            record.dia_cierre_cuentas_regalias = dia_cierre
            record.hora_cierre_cuentas_regalias = hora_cierre

    def _inverse_dia_inicio_cuentas_regalias(self):
        self._guardar_dia_configurado(
            'dia_inicio_cuentas_regalias',
            self._PARAM_DIA_INICIO_REGALIAS,
        )

    def _inverse_dia_cierre_cuentas_regalias(self):
        self._guardar_dia_configurado(
            'dia_cierre_cuentas_regalias',
            self._PARAM_DIA_CIERRE_REGALIAS,
        )

    def _inverse_hora_cierre_cuentas_regalias(self):
        self._guardar_hora_configurada(
            'hora_cierre_cuentas_regalias',
            self._PARAM_HORA_CIERRE_REGALIAS,
        )

    def estado_creacion_cuenta_regalias_portal(self):
        """Retorna si Regalías está disponible por ventana global de fechas."""
        self.ensure_one()
        if self.habilitar_cuentas_regalias:
            return True, False
        restringir = self.env['ir.config_parameter'].sudo().get_param(
            self._PARAM_RESTRINGIR_FECHAS_REGALIAS,
            'False',
        ) in ('True', '1', True)
        if not restringir:
            return True, False
        ahora = fields.Datetime.context_timestamp(
            self.with_context(tz='America/Bogota'),
            fields.Datetime.now(),
        )
        inicio = self._valor_dia_configurado(self._PARAM_DIA_INICIO_REGALIAS, 1)
        cierre = self._valor_dia_configurado(self._PARAM_DIA_CIERRE_REGALIAS, 31)
        hora_cierre = self._valor_hora_configurada(self._PARAM_HORA_CIERRE_REGALIAS)
        hora_actual = ahora.hour + (ahora.minute / 60.0) + (ahora.second / 3600.0)
        if inicio <= ahora.day < cierre:
            return True, False
        if ahora.day == cierre and hora_actual <= hora_cierre:
            return True, False
        if ahora.day < inicio:
            return False, (
                'La creación de cuentas de Regalías estará habilitada del día %s al %s de cada mes.'
            ) % (inicio, cierre)
        if ahora.day > cierre or (ahora.day == cierre and hora_actual > hora_cierre):
            return False, (
                'La creación de cuentas de Regalías estuvo habilitada hasta el día %s de este mes.'
            ) % cierre
        return True, False

    def estado_creacion_cuenta_cobro_portal(self):
        """Retorna si el login puede crear una cuenta y el mensaje para el portal."""
        self.ensure_one()
        if self.habilitar_cuenta_fuera_de_fecha:
            return True, False

        restringir = self.env['ir.config_parameter'].sudo().get_param(
            self._PARAM_RESTRINGIR_FECHAS_CUENTA_COBRO,
            'False',
        ) in ('True', '1', True)
        if not restringir:
            return True, False

        ahora = fields.Datetime.context_timestamp(
            self.with_context(tz='America/Bogota'),
            fields.Datetime.now(),
        )
        es_juridica = self.clase_persona == 'persona_juridica'
        inicio = self._valor_dia_configurado(
            self._PARAM_DIA_INICIO_JURIDICA if es_juridica else self._PARAM_DIA_INICIO_NATURAL,
            1,
        )
        cierre = self._valor_dia_configurado(
            self._PARAM_DIA_CIERRE_JURIDICA if es_juridica else self._PARAM_DIA_CIERRE_NATURAL,
            20 if es_juridica else 17,
        )
        hora_cierre = self._valor_hora_configurada(
            self._PARAM_HORA_CIERRE_JURIDICA if es_juridica else self._PARAM_HORA_CIERRE_NATURAL,
        )
        hora_actual = ahora.hour + (ahora.minute / 60.0) + (ahora.second / 3600.0)
        if inicio <= ahora.day < cierre:
            return True, False
        if ahora.day == cierre and hora_actual <= hora_cierre:
            return True, False

        tipo = 'personas jurídicas' if es_juridica else 'personas naturales'
        return False, (
            'La creación de cuentas de cobro para %s está habilitada del día %s al %s de cada mes. '
        ) % (tipo, inicio, cierre)

    direccion_fisica = fields.Char(string="Dirección", related='contratistas.direccion_fisica')
    numero_telefono = fields.Char(string="Teléfono (incluir ext e indicativo)", related='contratistas.numero_telefono')
    telefono_contacto_comercial = fields.Char(string="Teléfono móvil", related='contratistas.telefono_contacto_comercial')
    
    municipios2 = fields.Many2one(
        comodel_name='municipio',
        string="Municipio",
        related='contratistas.municipios2',
    )

    ciudad_exp_natural = fields.Many2one(
        comodel_name='municipio',
        string="Ciudad de expedición",
        related='contratistas.ciudad_exp_natural')
    
        # 2. El campo CHAR NUEVO y CALCULADO (tu nueva variable)
    tipo_documento_calculado = fields.Char(
        string='Tipo de Documento (Inferido)',
        compute='_compute_tipo_documento', # La función de cálculo
    )


    # 3. La Función de Cálculo (Debe estar DENTRO de la clase)
    @api.depends('documento_de_identidad')
    def _compute_tipo_documento(self):
        for record in self:
            documento = record.documento_de_identidad
            tipo = ''
            
            if not documento:
                record.tipo_documento_calculado = ''
                continue

            # Limpiamos el número para hacer la inferencia más robusta
            doc_limpio = documento.replace('.', '').replace(' ', '').strip()
            
            # --- Lógica de Inferencia ---
            
            # Heurística para NIT: Si contiene guion ('-') o es muy largo.
            if '-' in doc_limpio or len(doc_limpio) > 10:
                tipo = 'nit'
            
            # Heurística para CC: Si tiene una longitud típica de cédula (8 a 10 dígitos) y es solo numérico.
            elif len(doc_limpio) >= 8 and len(doc_limpio) <= 10 and doc_limpio.isdigit():
                tipo = 'cc'
            
            # Si no se ajusta a las reglas anteriores
            else:
                tipo = 'OTRO'
                
            # Asignamos el valor al nuevo campo
            record.tipo_documento_calculado = tipo

    @api.depends(
        'cuentas_de_cobro_ids',
        'cuentas_de_cobro_ids.proyecto_relacionado',
        'cuentas_de_cobro_ids.relacion_fuv_id',
        'cuentas_de_cobro_ids.relacion_otrosi_fuv_id',
        'cuentas_de_cobro_ids.relacion_orden_compra_servicios_fuv_id',
        'cuentas_de_cobro_ids.relacion_orden_compra_bienes_fuv_id',
    )
    def _compute_resumen_cuentas_de_cobro(self):
        for record in self:
            cuentas = record.cuentas_de_cobro_ids
            proyectos = cuentas.mapped('proyecto_relacionado').mapped('display_name')
            contratos = (
                cuentas.mapped('relacion_fuv_id').mapped('display_name')
                + cuentas.mapped('relacion_otrosi_fuv_id').mapped('display_name')
                + cuentas.mapped('relacion_orden_compra_servicios_fuv_id').mapped('display_name')
                + cuentas.mapped('relacion_orden_compra_bienes_fuv_id').mapped('display_name')
            )
            record.proyectos_con_contrato = ', '.join(sorted(set(filter(None, proyectos)))) or False
            record.contratos_relacionados = ', '.join(sorted(set(filter(None, contratos)))) or False
    
    password = fields.Char(string="Contraseña")
    token = fields.Char(string="Token", copy=False)
    public_url = fields.Char(string="URL Pública", compute="_compute_public_url", store=True)
    contratistas = fields.Many2one('contratista_proveedor', string='Contratista')
    

    fch_creacion = fields.Datetime(string='Fecha creacion', readonly=True, copy=False, tracking=True,
                                   default=lambda self: fields.Datetime.now())
    active = fields.Boolean(string='Activo', default=True, tracking=True)
    sesion_activa = fields.Boolean(string='Sesión iniciada', default=False, tracking=True)
    ultima_sesion = fields.Datetime(string='Última sesión', readonly=True, copy=False, tracking=True)
    notificaciones_vistas = fields.Datetime(string='Notificaciones vistas', readonly=True, copy=False, tracking=True)
    notificaciones_eliminadas = fields.Text(string='Notificaciones eliminadas', readonly=True, copy=False)

        # 🔹 Estado de conexión
    estado = fields.Selection([
        ('no_conectado', 'No conectado hasta ahora'),
        ('confirmado', 'Confirmado')
    ], string="Estado", default="no_conectado", tracking=True)

    # 🔹 Se actualiza automáticamente al guardar o cambiar contraseña
    @api.onchange('password')
    def _onchange_password(self):
        for record in self:
            if record.password:
                record.estado = 'confirmado'
            else:
                record.estado = 'no_conectado'


    avatar_128 = fields.Binary(string='Avatar', tracking=True)  # Imagen de perfil
    portal_partner_id = fields.Many2one(
        'res.partner',
        string='Contacto visual del portal',
        readonly=True,
        copy=False,
        help="Contacto usado solo para mostrar el usuario externo del login en la trazabilidad.",
    )

    def _get_portal_chatter_partner(self):
        """Partner visual usado para mostrar al contratista externo en el chatter."""
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        email = (self.coreo or '').strip()
        partner = self.portal_partner_id.sudo() if self.portal_partner_id else False

        if not partner:
            values = {
                'name': self.name or email or 'Usuario externo',
                'email': email or False,
                'company_type': 'person',
                'comment': 'Contacto visual creado para trazabilidad del portal externo.',
            }
            if self.avatar_128:
                values['image_1920'] = self.avatar_128
            partner = Partner.create(values)
            self.sudo().write({'portal_partner_id': partner.id})

        return partner
    clase_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('seleccion_manual', 'Seleccione'),  # Asegúrate de que este valor esté correctamente definido
    ], string='Tipo', required=False, tracking=True, default='seleccion_manual')

    def action_ver_cuentas_cobros(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cuentas de Cobros',
            'res_model': 'cuentas_de_cobros',
            'view_mode': 'kanban,tree,form',
            'domain': [('correo_facturacion', '=', self.coreo)],
            'context': {'search_default_correo_facturacion': self.coreo},
        }

    def action_soporte_reparar_contratista_portal(self):
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise UserError("Solo el grupo de soporte puede reparar el enlace del portal.")

        Contratista = self.env['contratista_proveedor'].sudo()
        for record in self:
            contratista = record.contratistas.sudo() if record.contratistas else Contratista

            if not contratista:
                domain = []
                if record.coreo:
                    domain.append(('correo_facturacion', '=ilike', record.coreo.strip()))
                if not domain and record.name:
                    domain.append(('name', '=ilike', record.name.strip()))

                if len(domain) > 1:
                    search_domain = ['|'] * (len(domain) - 1) + domain
                else:
                    search_domain = domain
                contratista = Contratista.search(search_domain, limit=1) if search_domain else Contratista
                if not contratista and record.documento_de_identidad:
                    documento_normalizado = _normalize_document_for_match(record.documento_de_identidad)
                    contratista = Contratista.search([]).filtered(
                        lambda c: (
                            _normalize_document_for_match(c.nit) == documento_normalizado
                            or _normalize_document_for_match(c.cedula) == documento_normalizado
                        )
                    )[:1]

            if not contratista:
                raise UserError(
                    "No encontré una ficha de contratista/proveedor para %s. "
                    "Asóciala manualmente en el campo Contratista o revisa correo/documento."
                    % (record.name or record.coreo or record.id)
                )

            vals_login = {}
            if record.contratistas != contratista:
                vals_login['contratistas'] = contratista.id
            if vals_login:
                record.with_context(no_recursive=True).write(vals_login)

            vals_contratista = {}
            if not contratista.access_token:
                vals_contratista['access_token'] = str(uuid.uuid4())
            if record.coreo and not contratista.correo_facturacion:
                vals_contratista['correo_facturacion'] = record.coreo.strip()
            if vals_contratista:
                contratista.with_context(no_recursive=True).write(vals_contratista)

            partner_portal = record._get_portal_chatter_partner()

            record.message_post(
                body=(
                    "✅ Soporte reparó el enlace del portal.<br/>"
                    "<b>Contratista:</b> %s<br/>"
                    "<b>Token contratista:</b> %s<br/>"
                    "<b>Contacto trazabilidad portal:</b> %s<br/>"
                    "<b>Usuarios internos:</b> no se modificaron"
                ) % (
                    contratista.display_name,
                    "generado" if contratista.access_token else "sin token",
                    partner_portal.display_name,
                ),
                subtype_xmlid="mail.mt_note",
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Portal reparado',
                'message': 'Login enlazado al contratista; no se modificaron usuarios internos.',
                'type': 'success',
                'sticky': False,
            }
        }
    
    def import_all_contratistas(self):
        contratistas = self.env['contratista_proveedor'].search([])

        for contratista in contratistas:
            # Asegúrate de que tenga un correo válido
            if not contratista.correo_facturacion:
                continue

            existing_login = self.env['login'].search([('coreo', '=', contratista.correo_facturacion)], limit=1)
            if existing_login:
                continue

            login = self.create({
                'name': contratista.razon_social or contratista.name,
                'coreo': contratista.correo_facturacion,
                'documento_de_identidad': contratista.nit or contratista.cedula,
                'clase_persona': contratista.clase_persona,
                'contratistas': contratista.id,
            })

            login.send_invitation_email()

        

    @api.model
    def create(self, vals):
        if (
            (
                vals.get('habilitar_cuenta_fuera_de_fecha')
                or vals.get('habilitar_cuentas_regalias')
                or vals.get('habilitar_envio_ventanilla_fuera_plazo')
            )
            and not self.env.su
            and not self.env.user.has_group('Matriz.group_soporte')
        ):
            raise AccessError('Solo el rol de Soporte puede habilitar excepciones del portal.')
        # 🔹 Quitamos .lower() para respetar mayúsculas, mantenemos .strip() para limpiar espacios
        if vals.get('coreo'):
            vals['coreo'] = str(vals['coreo']).strip()

        # Verificar si el correo ya existe
        if 'coreo' in vals:
            # Mantenemos '=ilike' para que el sistema NO permita registrar el mismo correo 
            # variando solo mayúsculas (ej: evita que existan "Juan@..." y "juan@..." a la vez)
            existing_user = self.search([('coreo', '=ilike', vals['coreo'])], limit=1)
            if existing_user:
                raise ValidationError(f"El correo {vals['coreo']} ya está registrado. Por favor, use un correo diferente.")

        record = super(Login, self).create(vals)
        
        # Si tiene password al crear, lo marcamos confirmado
        if vals.get('password'):
            record.estado = 'confirmado'
        
        record.send_invitation_email()
        return record

    def write(self, vals):
        campos_soporte = {
            'restringir_fechas_cuenta_cobro',
            'habilitar_cuenta_fuera_de_fecha',
            'habilitar_cuentas_regalias',
            'habilitar_envio_ventanilla_fuera_plazo',
            'restringir_fechas_cuentas_regalias',
            'dia_inicio_cuenta_natural',
            'dia_cierre_cuenta_natural',
            'hora_cierre_cuenta_natural',
            'dia_inicio_cuenta_juridica',
            'dia_cierre_cuenta_juridica',
            'hora_cierre_cuenta_juridica',
            'dia_inicio_cuentas_regalias',
            'dia_cierre_cuentas_regalias',
            'hora_cierre_cuentas_regalias',
        }
        if (
            campos_soporte.intersection(vals)
            and not self.env.su
            and not self.env.user.has_group('Matriz.group_soporte')
        ):
            raise AccessError('Solo el rol de Soporte puede modificar el control de creación de cuentas.')
        # 1. EVITAR BUCLE INFINITO
        if self.env.context.get('no_recursive'):
            return super(Login, self).write(vals)

        # 🔹 Quitamos .lower() aquí también para el backend
        if vals.get('coreo'):
            vals['coreo'] = str(vals['coreo']).strip()

        # 2. Guardamos los cambios normalmente
        since = fields.Datetime.now()
        res = super(Login, self).write(vals)
        self._reassign_portal_tracking_author(since)
        
        for record in self:
            # --- Lógica de Estado (si cambia password) ---
            if vals.get('password') and record.password:
                record.estado = 'confirmado'
            
            # --- Lógica de Sincronización con Contratista ---
            if record.contratistas and ('name' in vals or 'coreo' in vals):
                vals_contratista = {}
                
                if 'name' in vals:
                    vals_contratista['name'] = vals['name']
                
                if 'coreo' in vals:
                    vals_contratista['correo_facturacion'] = vals['coreo'] 
                
                if vals_contratista:
                    record.contratistas.with_context(no_recursive=True).write(vals_contratista)

        return res


    def _generate_token(self):
        return str(uuid.uuid4())

    def send_invitation_email(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        for record in self:
            if not record.coreo:
                _logger.error(f"Correo electrónico no encontrado para el registro {record.id}")
                continue

            # Generar y guardar el token
            record.token = record._generate_token()
            record.sudo().write({'token': record.token})

            public_url = f"{base_url}/public/reset_password/{record.id}/{record.token}"

            subject = "Invitación para crear su contraseña"
            body_html = f"""
                <div style="font-family: Arial, sans-serif; line-height: 1.5; color: #333;">
                    <h2>Bienvenido/a a SGO</h2>
                    <p>Estimado/a {record.name},</p>
                    <p>Ha sido invitado/a a crear una cuenta en nuestro sistema. Por favor, haga clic en el siguiente enlace para establecer su contraseña y acceder a su cuenta:</p>
                    <p style="text-align: left; margin: 20px 0;">
                        <a href="{public_url}" style="background-color: #2a7ae2; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                            Crear contraseña
                        </a>
                    </p>
                    <p style="text-align: left; margin: 20px 0;">
                        <a href="https://drive.google.com/file/d/1g6sop8FRv4wu9eXThMuiLJySbrX1iVQw/view?usp=sharing" style="background-color: #C70A13; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                            Video tutorial 
                        </a>
                    </p>
                    <p>Gracias,</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                    <p style="font-size: 12px; color: #888;">Este es un mensaje automático, por favor no responder.</p>
                </div>
            """

            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': record.coreo,
                'email_from': self.env.user.email or 'no-reply@tuempresa.com',
            }

            try:
                mail = self.env['mail.mail'].sudo().create(mail_values)
                mail.sudo().send()
                _logger.info(f"Correo de invitación enviado a {record.coreo}")
            except Exception as e:
                _logger.error(f"Error al enviar el correo de invitación: {str(e)}")

    def send_reset_password_email(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
    
        for record in self:
            if not record.coreo:
                _logger.error(f"Correo electrónico no encontrado para el registro {record.id}")
                continue
    
            # Generar y guardar el token
            record.token = record._generate_token()
            record.sudo().write({'token': record.token})
    
            public_url = f"{base_url}/public/reset_password/{record.id}/{record.token}"
    
            subject = "Restablecer su contraseña"
            body_html = f"""
                <div style="font-family: Arial, sans-serif; line-height: 1.5; color: #333;">
                    <h2>Restablecer su contraseña</h2>
                    <p>Estimado/a {record.name},</p>
                    <p>Hemos recibido una solicitud para restablecer la contraseña de su cuenta. Si no realizó esta solicitud, puede ignorar este mensaje. De lo contrario, haga clic en el siguiente enlace para restablecer su contraseña:</p>
                    <p style="text-align: left; margin: 20px 0;">
                        <a href="{public_url}" style="background-color: #2a7ae2; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                            Restablecer contraseña
                        </a>
                    </p>
                    <p>Gracias,</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                    <p style="font-size: 12px; color: #888;">Este es un mensaje automático, por favor no responder.</p>
                </div>
            """
    
            mail_values = {
                'subject': subject,
                'body_html': body_html,
                'email_to': record.coreo,
                'email_from': self.env.user.email or 'no-reply@tuempresa.com',
            }
    
            try:
                mail = self.env['mail.mail'].sudo().create(mail_values)
                mail.sudo().send()
                _logger.info(f"Correo de restablecimiento enviado a {record.coreo}")
            except Exception as e:
                _logger.error(f"Error al enviar el correo de restablecimiento: {str(e)}")




class ContratacionesFUVRelacion(models.Model):
    _name = 'contrataciones_fuv_relacion'
    _description = 'Relación de todas las contrataciones FUV'
    _rec_name = "name_get_display"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    
    estado_contratacion = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('juridico', 'Jurídico'),
        ('recursos_humanos', 'Recursos humanos'),
        ('contratado', 'Contratado'),
    ], string='Estado Contratación', related='fuente_id.estado_contratacion')

    name = fields.Char(string='Nombre de la Relación', default='Resumen de contrataciones', related='fuente_id.name')
    drive_file_id = fields.Many2one('drive.file', string='Archivo de Drive', related='fuente_id.drive_file_id', )  # Campo para vincular con drive.file
    select_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('contrato_laboral', 'Contrato laboral')],
        string="Tipo de Persona", related='fuente_id.select_persona'
    )
    documento_identidad = fields.Char(string='Documento', related='fuente_id.documento_identidad')
    nit = fields.Char(string='NIT', related='fuente_id.nit')
    fuente_id = fields.Many2one('contrataciones_fuv', string='Registro original')  # opcional para referencia
    contrataciones_ids = fields.One2many('contrataciones_fuv', 'relacion_id', string='Contrataciones')

    contrataciones_fuv_ids = fields.One2many(
        'cuentas_de_cobros', 
        'relacion_fuv_id', 
        string='Contrataciones'
    )

    # Este decorador ejecuta la validación justo antes de intentar borrar
    @api.ondelete(at_uninstall=False)
    def _check_cuentas_de_cobro(self):
        for record in self:
            if record.contrataciones_fuv_ids:
                # Estructuramos el mensaje con saltos de línea y un icono de advertencia
                mensaje = (
                    "⚠️  RESTRICCIÓN DE SEGURIDAD  ⚠️\n\n"
                    "No se puede eliminar este contrato porque ya tiene una cuenta de cobro creada.\n\n"
                    "Por favor, solicite que se elimine la cuenta de cobro primero."
                )
                raise UserError(mensaje)
            
    objeto_contrato = fields.Text(string='Objeto del contrato', tracking=True, related='fuente_id.objeto_contrato')
    compromiso_presupuestal = fields.Char('RPC', tracking=True, related='fuente_id.compromiso_presupuestal')
    ciudad_contratista = fields.Char(string='Ciudad de expedición', related='fuente_id.ciudad_contratista')


    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado', related='fuente_id.proyecto_relacionado')
    total_pago_porcentaje = fields.Float(string="Total % de Pago", related='fuente_id.total_pago_porcentaje')

    departamentos_y_municipios_ids = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id',
                                                     string='Tabla', related='fuente_id.departamentos_y_municipios_ids')

    actividades_especificas = fields.Text('Actividades específicas', related='fuente_id.actividades_especificas')
    currency_id = fields.Many2one('res.currency', string='Moneda')
    valor_total_contrato = fields.Monetary(string='Valor total del contrato', tracking=True, related='fuente_id.valor_total_contrato')
    valor_total_contrato_a_b_c = fields.Float(string='Valor Total Contrato (a + b + c)',related='fuente_id.valor_total_contrato_a_b_c')


    tabla_de_forma_de_pago = fields.One2many('tabla_forma_de_pago', 'contrataciones_fuv_tabla_id', string='Tabla', related='fuente_id.tabla_de_forma_de_pago', readonly=True)



    fecha_iniciacion = fields.Date(
        'Fecha de iniciación',
        tracking=True,
        related='fuente_id.fecha_iniciacion'
    )
    fecha_terminacion = fields.Date(
        'Fecha de terminación',
        tracking=True,
        related='fuente_id.fecha_terminacion'
    )

   

    dependencia_area_solicitante = fields.Selection(
        selection=[
            ('productividad', 'Productividad'),
            ('regalias', 'Regalías'),
            ('tecnologia', 'Tecnología'),
            ('estructuracion', 'Estructuración'),
            ('financiera', 'Financiera'),
            ('juridica', 'Jurídica'),
            ('administrativa', 'Administrativa'),
        ],
        string='Dependencia - Área Solicitante',
        tracking=True, related='fuente_id.dependencia_area_solicitante' 
    )

    id_proyecto3 = fields.Char(string='Id del proyecto', related="fuente_id.id_proyecto3")
    mcb_centro_costo = fields.Char(string='Centro de costo', related="fuente_id.mcb_centro_costo")
    id_del_contrato = fields.Char(string='ID del contrato', related="fuente_id.id_del_contrato")


    name_get_display = fields.Char(
        string='Nombre visual',
        compute='_compute_name_get_display',
        store=False
    )

    # ✅ 1. Añadimos 'id_del_contrato' al decorador depends
    @api.depends('name', 'nit', 'documento_identidad', 'fuente_id.fecha_iniciacion', 'fuente_id.fecha_terminacion', 'id_del_contrato')
    def _compute_name_get_display(self):
        for record in self:
            nombre = record.name or 'Sin nombre'
            
            # ✅ 2. Extraemos el id_del_contrato (con un texto por defecto si está vacío)
            id_contrato = record.id_del_contrato or 'Sin ID'

            if record.nit:
                tipo_doc = f"NIT: {record.nit}"
            elif record.documento_identidad:
                tipo_doc = f"DI: {record.documento_identidad}"
            else:
                tipo_doc = "Sin documento"

            fecha_ini = record.fuente_id.fecha_iniciacion.strftime('%Y-%m-%d') if record.fuente_id.fecha_iniciacion else '¿sin inicio?'
            fecha_fin = record.fuente_id.fecha_terminacion.strftime('%Y-%m-%d') if record.fuente_id.fecha_terminacion else '¿sin fin?'

            fechas = f"Ini: {fecha_ini} / Fin: {fecha_fin}"

            # ✅ 3. Añadimos el id_contrato a la cadena final que se va a mostrar
            record.name_get_display = f"[ID:{id_contrato}] [{nombre}] [{tipo_doc}] [{fechas}]"

    def name_get(self):
        return [(rec.id, rec.name_get_display) for rec in self]



    # 🔧 CARGA AUTOMÁTICA DESDE FUV (UNO A UNO)
    def action_cargar_desde_fuv(self):
        registros_fuv = self.env['contrataciones_fuv'].search([])

        for registro in registros_fuv:
            # Solo crear si no existe relación para ese ID exacto
            ya_existe = self.search([('fuente_id', '=', registro.id)], limit=1)
            if not ya_existe:
                self.create({
                    'fuente_id': registro.id,
                })

    _sql_constraints = [
        ('unique_fuente_id', 'unique(fuente_id)', 'Ya existe una relación para este contrato FUV.')
    ]

    # --- NUEVA FUNCIÓN: Solo procesa UN registro (Ultra rápida) ---
    def action_cargar_individual(self, fuv_id):
        """ Recibe un ID de contrato FUV y crea la relación si no existe """
        # 1. Verificamos si ya existe para no romper la restricción SQL unique
        existe = self.search([('fuente_id', '=', fuv_id)], limit=1)
        
        # 2. Si no existe, lo creamos
        if not existe:
            self.create({
                'fuente_id': fuv_id,
            })
        return True



    # Documentos arrastrador  P/N
    anexo_tributario_id = fields.Many2one('drive', string='Anexo Tributario', readonly=True, related='fuente_id.anexo_tributario_id')
    anexo_tributario_doc_visible = fields.Boolean(default=True, related='fuente_id.anexo_tributario_doc_visible')

    documento_identidad_pn_doc_visible = fields.Boolean(default=True, related='fuente_id.documento_identidad_pn_doc_visible')
    documento_identidad_id = fields.Many2one('drive', string='Documento de Identidad', readonly=True, related='fuente_id.documento_identidad_id')

    certificacion_bancaria_id = fields.Many2one('drive', string='Certificación Bancaria', readonly=True, related='fuente_id.certificacion_bancaria_id')
    certificacion_bancaria_doc_visible = fields.Boolean(default=True, related='fuente_id.certificacion_bancaria_doc_visible')

    resolucion_facturacion_ju_id = fields.Many2one('drive', string='Resolución Facturación', readonly=True, related='fuente_id.resolucion_facturacion_ju_id')
    resolucion_facturacion_ju_doc_visible = fields.Boolean(default=True, related='fuente_id.resolucion_facturacion_ju_doc_visible')

    contratos_firmado_doc_visible = fields.Boolean(default=True, related='fuente_id.contratos_firmado_doc_visible')

    contratos_firmado_id = fields.Many2one('drive', string='Contrato Firmado', readonly=True, related='fuente_id.contratos_firmado_id')

    su_cdp_id = fields.Many2one('drive', string='RPC', readonly=True, related='fuente_id.su_cdp_id')
    su_cdp_doc_visible = fields.Boolean(default=True, related='fuente_id.su_cdp_doc_visible')



    # Documentos arrastrador  P/J
    anexo_tributario_juridico_doc_visible = fields.Boolean(default=True, related='fuente_id.anexo_tributario_juridico_doc_visible')
    anexo_tributario_juridico_doc_id = fields.Many2one('drive', string='Anexo Tributario Jurídico', related='fuente_id.anexo_tributario_juridico_doc_id')

    aprueba_j_h_contrato_firmado_doc_id = fields.Many2one('drive', string='Contrato firmado', related='fuente_id.aprueba_j_h_contrato_firmado_doc_id')
    aprueba_j_h_contrato_firmado_doc_visible = fields.Boolean(default=True, related='fuente_id.aprueba_j_h_contrato_firmado_doc_visible')

    certificado_bancario_doc_id = fields.Many2one('drive', string='Certificado Bancario', related='fuente_id.certificado_bancario_doc_id')
    certificado_bancario_doc_visible = fields.Boolean(default=True, related='fuente_id.certificado_bancario_doc_visible')

    resolucion_facturacion_doc_id = fields.Many2one('drive', string='Resolución Facturación', related='fuente_id.resolucion_facturacion_doc_id')
    resolucion_facturacion_doc_visible = fields.Boolean(default=True, related='fuente_id.resolucion_facturacion_doc_visible')

    cdp_juridico_doc_id = fields.Many2one('drive', string='RPC  ', related='fuente_id.cdp_juridico_doc_id')
    cdp_juridico_doc_visible = fields.Boolean(default=True, related='fuente_id.cdp_juridico_doc_visible')


class otrosi_bdd(models.Model):
    _name = "otrosi_bdd"
    _description = "Otrosí BDD"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "name_get_display"


    estado_contratacion = fields.Selection([
        ('coordinador', 'Coordinador'),
        ('financiera', 'Financiera'),
        ('juridico', 'Jurídico'),
        ('recursos_humanos', 'Recursos humanos'),
        ('contratado', 'Contratado'),
    ] + ESTADOS_OTRO_SI_NUEVOS, string='Estado Contratación', related='fuente_id.estado_contratacion')

    tabla_de_forma_de_pago = fields.One2many(
        'tabla_forma_pago_otrosi',
        'otrosi_tabla_id',
        string='Tabla',
        related='fuente_id.tabla_de_forma_de_pago',
        readonly=True,
    )

    otrosi_db_ids = fields.One2many(
        'cuentas_de_cobros', 
        'relacion_otrosi_fuv_id', 
        string='Contrataciones'
    )

    # Este decorador ejecuta la validación justo antes de intentar borrar
    @api.ondelete(at_uninstall=False)
    def _check_cuentas_de_cobro(self):
        for record in self:
            if record.otrosi_db_ids:
                # Estructuramos el mensaje con saltos de línea y un icono de advertencia
                mensaje = (
                    "⚠️  RESTRICCIÓN DE SEGURIDAD  ⚠️\n\n"
                    "No se puede eliminar este otrosí porque ya tiene una cuenta de cobro creada.\n\n"
                    "Por favor, solicite que se elimine la cuenta de cobro primero."
                )
                raise UserError(mensaje)

    name = fields.Char(string='Nombre del Otrosí', default='Otrosí', tracking=True, related='fuente_id.name',)
    fuente_id = fields.Many2one('otro_si', string='Registro original')  # opcional para referencia
    currency_id = fields.Many2one('res.currency', string='Moneda')
    valor_total_contrato = fields.Monetary(string='Valor total del contrato', tracking=True, related='fuente_id.valor_total_contrato')

    actividades_especificas_otrosi = fields.Text('Actividades específicas otrosí', related='fuente_id.actividades_especificas_otrosi')
    actividades_especificas_otrosi_natu = fields.Text('Actividades específicas otrosí', related='fuente_id.actividades_especificas_otrosi_natu')

    drive_file_id = fields.Many2one('otrosi_drive', string='Archivo de Drive', related='fuente_id.drive_file_id',)  # Campo para vincular con drive.file
    select_persona = fields.Selection([
        ('persona_natural', 'Persona natural'),
        ('persona_juridica', 'Persona jurídica'),
        ('contrato_laboral', 'Contrato laboral')],
        string="Tipo de Persona", related='fuente_id.select_persona'
    )
    documento_identidad = fields.Char(string='Documento', related='fuente_id.documento_identidad')
    nit = fields.Char(string='NIT', related='fuente_id.nit')
        # 🔧 CARGA AUTOMÁTICA DESDE FUV (UNO A UNO)
    def action_cargar_desde_fuv(self):
        registros_fuv = self.env['otro_si'].search([])

        for registro in registros_fuv:
            # Solo crear si no existe relación para ese ID exacto
            ya_existe = self.search([('fuente_id', '=', registro.id)], limit=1)
            if not ya_existe:
                self.create({
                    'fuente_id': registro.id,
                })

    _sql_constraints = [
        ('unique_fuente_id', 'unique(fuente_id)', 'Ya existe una relación para este contrato FUV.')
    ]

    # ---------------------------------------------------------
    # NUEVA FUNCIÓN: Carga INDIVIDUAL (Rápida y Ligera)
    # ---------------------------------------------------------
    def action_cargar_individual_otro_si(self, otro_si_id):
        """ Recibe el ID de un Otro Sí y crea la relación si no existe """
        # 1. Verificamos si ya existe para no romper la restricción SQL unique
        existe = self.search([('fuente_id', '=', otro_si_id)], limit=1)
        
        # 2. Si no existe, lo creamos
        if not existe:
            self.create({
                'fuente_id': otro_si_id,
            })
        return True
    

    fecha_iniciacion = fields.Date(
        'Fecha de iniciación',
        tracking=True,
        related='fuente_id.fecha_iniciacion'
    )
    fecha_terminacion = fields.Date(
        'Fecha de terminación',
        tracking=True,
        related='fuente_id.fecha_terminacion'
    )
    
    dependencia_area_solicitante = fields.Selection(
        selection=[
            ('productividad', 'Productividad'),
            ('regalias', 'Regalías'),
            ('tecnologia', 'Tecnología'),
            ('estructuracion', 'Estructuración'),
            ('financiera', 'Financiera'),
            ('juridica', 'Jurídica'),
            ('administrativa', 'Administrativa'),
        ],
        string='Dependencia - Área Solicitante',
        tracking=True, related='fuente_id.dependencia_area_solicitante2' 
    )


    mcb_centro_costo = fields.Char(string='Centro de costo', related="fuente_id.mcb_centro_costo")
    id_del_contrato = fields.Char(string='ID del contrato', related="fuente_id.id_del_contrato")
    objeto_contrato = fields.Text(string='Objeto del contrato', tracking=True, related='fuente_id.objeto_contrato')
    compromiso_presupuestal = fields.Char('RPC', tracking=True, related='fuente_id.compromiso_presupuestal')
    ciudad_contratista = fields.Char(string='Ciudad de expedición', related='fuente_id.ciudad_contratista')


    proyecto_relacionado = fields.Many2one('contratacion', string='Proyecto relacionado', related='fuente_id.proyecto_relacionado')


    departamentos_y_municipios_ids = fields.One2many('departamento_y_municipio', 'contrataciones_fuv_id',
                                                     string='Tabla', related='fuente_id.departamentos_y_municipios_ids2')

    name_get_display = fields.Char(
        string='Nombre visual',
        compute='_compute_name_get_display',
        store=False
    )

    # ✅ 1. Añadimos 'id_del_contrato' a las dependencias
    @api.depends('name', 'nit', 'documento_identidad', 'fuente_id.fecha_iniciacion', 'fuente_id.fecha_terminacion', 'id_del_contrato')
    def _compute_name_get_display(self):
        for record in self:
            nombre = record.name or 'Sin nombre'
            
            # ✅ 2. Capturamos el ID del contrato
            id_contrato = record.id_del_contrato or 'Sin ID'

            if record.nit:
                tipo_doc = f"NIT: {record.nit}"
            elif record.documento_identidad:
                tipo_doc = f"DI: {record.documento_identidad}"
            else:
                tipo_doc = "Sin documento"

            fecha_ini = record.fuente_id.fecha_iniciacion.strftime('%Y-%m-%d') if record.fuente_id.fecha_iniciacion else '¿sin inicio?'
            fecha_fin = record.fuente_id.fecha_terminacion.strftime('%Y-%m-%d') if record.fuente_id.fecha_terminacion else '¿sin fin?'

            fechas = f"Ini: {fecha_ini} / Fin: {fecha_fin}"

            # ✅ 3. Lo añadimos al principio de la cadena con el formato [ID:valor]
            record.name_get_display = f"[ID:{id_contrato}] [{nombre}] [{tipo_doc}] [{fechas}]"

    def name_get(self):
        return [(rec.id, rec.name_get_display) for rec in self]
    

    # Documentos arrastrador  P/N
    anexo_tributario_id = fields.Many2one('drive', string='Anexo Tributario', readonly=True, related='fuente_id.anexo_tributario_id')
    anexo_tributario_doc_visible = fields.Boolean(default=True, related='fuente_id.anexo_tributario_doc_visible')

    documento_identidad_pn_doc_visible = fields.Boolean(default=True, related='fuente_id.documento_identidad_pn_doc_visible')
    documento_identidad_id = fields.Many2one('drive', string='Documento de Identidad', readonly=True, related='fuente_id.documento_identidad_id')

    certificacion_bancaria_id = fields.Many2one('drive', string='Certificación Bancaria', readonly=True, related='fuente_id.certificacion_bancaria_id')
    certificacion_bancaria_doc_visible = fields.Boolean(default=True, related='fuente_id.certificacion_bancaria_doc_visible')

    resolucion_facturacion_ju_id = fields.Many2one('drive', string='Resolución Facturación', readonly=True, related='fuente_id.resolucion_facturacion_ju_id')
    resolucion_facturacion_ju_doc_visible = fields.Boolean(default=True, related='fuente_id.resolucion_facturacion_ju_doc_visible')

    contratos_firmado_doc_visible = fields.Boolean(default=True, related='fuente_id.contratos_firmado_natu_doc_visible')

    contratos_firmado_id = fields.Many2one('drive', string='Contrato Firmado', readonly=True, related='fuente_id.contratos_firmado_id')

    su_cdp_id = fields.Many2one('drive', string='RPC', readonly=True, related='fuente_id.su_cdp_id')
    su_cdp_doc_visible = fields.Boolean(default=True, related='fuente_id.su_cdp_doc_visible')



    # Documentos arrastrador  P/J
    anexo_tributario_juridico_doc_visible = fields.Boolean(default=True, related='fuente_id.anexo_tributario_juridico_doc_visible')
    anexo_tributario_juridico_doc_id = fields.Many2one('drive', string='Anexo Tributario Jurídico', related='fuente_id.anexo_tributario_juridico_doc_id')

    aprueba_j_h_contrato_firmado_doc_id = fields.Many2one('drive', string='Contrato firmado', related='fuente_id.contratos_firmado_juridico_id')
    aprueba_j_h_contrato_firmado_doc_visible = fields.Boolean(default=True, related='fuente_id.contratos_firmado_juridico_doc_visible')

    certificado_bancario_doc_id = fields.Many2one('drive', string='Certificado Bancario', related='fuente_id.certificado_bancario_doc_id')
    certificado_bancario_doc_visible = fields.Boolean(default=True, related='fuente_id.certificado_bancario_doc_visible')

    resolucion_facturacion_doc_id = fields.Many2one('drive', string='Resolución Facturación', related='fuente_id.resolucion_facturacion_doc_id')
    resolucion_facturacion_doc_visible = fields.Boolean(default=True, related='fuente_id.resolucion_facturacion_doc_visible')

    cdp_juridico_doc_id = fields.Many2one('drive', string='RPC  ', related='fuente_id.cdp_juridico_doc_id')
    cdp_juridico_doc_visible = fields.Boolean(default=True, related='fuente_id.cdp_juridico_doc_visible')

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import (
    ESTADO_SOLICITUD_POLIZA_SELECTION,
    ORIGEN_SOLICITUD_POLIZA_SELECTION,
    POLIZA_SELECTION,
    EstadoSolicitudPoliza,
    OrigenSolicitudPoliza,
)


class SolicitudPoliza(models.Model):
    _name = 'contrataciones_fuv.solicitud.poliza'
    _description = 'Solicitud de pólizas del contrato'
    _inherit = [
        'mail.thread',
        'mail.activity.mixin',
    ]
    _order = 'fecha_solicitud desc, id desc'

    name = fields.Char(string='Solicitud', compute='_compute_name', store=True)
    contract_id = fields.Many2one(
        'contrataciones_fuv',
        string='Contrato FUV',
        ondelete='cascade',
        index=True,
        tracking=True,
    )
    sgr_contract_id = fields.Many2one(
        'contrataciones_sgr',
        string='Contrato SGR',
        ondelete='cascade',
        index=True,
        tracking=True,
    )
    origen = fields.Selection(
        ORIGEN_SOLICITUD_POLIZA_SELECTION,
        string='Origen de la solicitud',
        default=OrigenSolicitudPoliza.CONTRATO.value,
        required=True,
        readonly=True,
        copy=False,
        index=True,
        tracking=True,
    )
    origen_nombre = fields.Char(
        string='Origen de la solicitud',
        compute='_compute_origen_nombre',
        readonly=True,
    )
    otro_si_id = fields.Many2one(
        'otro_si',
        string='Otrosí que requiere actualización',
        readonly=True,
        copy=False,
        ondelete='set null',
        index=True,
    )
    cierre_proceso_id = fields.Many2one(
        'contrataciones_fuv.cierre.proceso',
        string='Liquidación que requiere actualización',
        readonly=True,
        copy=False,
        ondelete='set null',
        index=True,
    )
    otros_documentos_ids = fields.Many2many(
        'drive',
        'solicitud_poliza_otro_documento_rel',
        'solicitud_id',
        'documento_id',
        string='Otros',
        copy=False,
        readonly=True,
    )
    solicitado_por_id = fields.Many2one(
        'res.users',
        string='Solicitado por',
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
        tracking=True,
    )
    fecha_solicitud = fields.Datetime(
        string='Fecha de solicitud',
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        tracking=True,
    )
    estado = fields.Selection(
        ESTADO_SOLICITUD_POLIZA_SELECTION,
        string='Estado',
        default=EstadoSolicitudPoliza.SOLICITADA.value,
        required=True,
        readonly=True,
        # Cada transición ya publica una nota funcional en el chatter. Evitar
        # el tracking genérico impide que ir.model.fields.selection antiguos
        # rompan el commit al incorporar un nuevo valor del Enum.
        tracking=False,
    )
    linea_ids = fields.One2many(
        'contrataciones_fuv.solicitud.poliza.linea',
        'solicitud_id',
        string='Pólizas solicitadas',
        copy=False,
    )
    solicitud_poliza_drive_folder_id = fields.Many2one(
        'drive.file',
        string='Carpeta Pólizas',
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    contratista_email = fields.Char(
        string='Correo del contratista',
        compute='_compute_contratista_email',
        readonly=True,
        copy=False,
    )
    fecha_envio = fields.Datetime(
        string='Fecha de envío',
        readonly=True,
        copy=False,
        tracking=True,
    )
    enviado_por_id = fields.Many2one(
        'res.users',
        string='Enviado por',
        readonly=True,
        copy=False,
        tracking=True,
    )
    portal_token = fields.Char(
        string='Token del portal',
        readonly=True,
        copy=False,
        index=True,
        groups='Matriz.group_soporte',
    )
    portal_token_expira = fields.Datetime(
        string='Vencimiento del enlace',
        readonly=True,
        copy=False,
        tracking=True,
    )
    acta_aprobacion_documento_id = fields.Many2one(
        'drive',
        string='Acta definitiva',
        readonly=True,
        copy=False,
        ondelete='set null',
        tracking=True,
    )
    acta_cargada_por_id = fields.Many2one(
        'res.users',
        string='Acta cargada por',
        readonly=True,
        copy=False,
        ondelete='set null',
        tracking=True,
    )
    fecha_acta_cargada = fields.Datetime(
        string='Fecha de carga del acta',
        readonly=True,
        copy=False,
        tracking=True,
    )
    puede_cargar_acta = fields.Boolean(
        string='Puede cargar el acta definitiva',
        compute='_compute_puede_cargar_acta',
    )

    def _auto_init(self):
        """Migra la unicidad global a una regla independiente por origen."""
        resultado = super()._auto_init()
        self.env.cr.execute(
            'ALTER TABLE "%s" ALTER COLUMN contract_id DROP NOT NULL' % self._table
        )
        self.env.cr.execute(
            'ALTER TABLE "%s" DROP CONSTRAINT IF EXISTS '
            '"%s_contract_id_unique"' % (self._table, self._table)
        )
        # Los registros creados antes de incorporar los ciclos no tenían
        # origen. Durante la actualización se conservan como solicitudes del
        # contrato inicial antes de construir los índices parciales.
        self.env.cr.execute(
            'UPDATE "%s" SET origen = \'contrato\' WHERE origen IS NULL'
            % self._table
        )
        self.env.cr.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS '
            'solicitud_poliza_contrato_inicial_uniq '
            'ON "%s" (contract_id) WHERE origen = \'contrato\'' % self._table
        )
        self.env.cr.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS '
            'solicitud_poliza_sgr_contrato_inicial_uniq '
            'ON "%s" (sgr_contract_id) '
            'WHERE origen = \'contrato\' AND sgr_contract_id IS NOT NULL' % self._table
        )
        self.env.cr.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS '
            'solicitud_poliza_otro_si_uniq '
            'ON "%s" (otro_si_id) WHERE otro_si_id IS NOT NULL' % self._table
        )
        self.env.cr.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS '
            'solicitud_poliza_cierre_uniq '
            'ON "%s" (cierre_proceso_id) '
            'WHERE cierre_proceso_id IS NOT NULL' % self._table
        )
        return resultado

    def _contrato_poliza(self):
        self.ensure_one()
        return self.sgr_contract_id or self.contract_id

    @api.model_create_multi
    def create(self, vals_list):
        solicitudes = super().create(vals_list)
        solicitudes._validar_origen_unico()
        return solicitudes

    @api.constrains('contract_id', 'sgr_contract_id', 'origen', 'otro_si_id', 'cierre_proceso_id')
    def _validar_origen_unico(self):
        for solicitud in self:
            if bool(solicitud.contract_id) == bool(solicitud.sgr_contract_id):
                raise UserError(_('La solicitud debe pertenecer a un solo contrato FUV o SGR.'))
            if solicitud.sgr_contract_id and solicitud.origen not in (
                OrigenSolicitudPoliza.CONTRATO.value,
                OrigenSolicitudPoliza.SOPORTE.value,
            ):
                raise UserError(_('Este origen de pólizas todavía no aplica a SGR.'))
            dominio = [('id', '!=', solicitud.id)]
            if solicitud.origen == OrigenSolicitudPoliza.CONTRATO.value:
                if solicitud.otro_si_id or solicitud.cierre_proceso_id:
                    raise UserError(_(
                        'La solicitud inicial no puede vincularse a un otrosí o una liquidación.'
                    ))
                campo_contrato = 'sgr_contract_id' if solicitud.sgr_contract_id else 'contract_id'
                dominio += [
                    (campo_contrato, '=', solicitud._contrato_poliza().id),
                    ('origen', '=', OrigenSolicitudPoliza.CONTRATO.value),
                ]
                mensaje = _('Este contrato ya tiene una solicitud inicial de pólizas.')
            elif solicitud.origen == OrigenSolicitudPoliza.OTRO_SI.value:
                if not solicitud.otro_si_id:
                    raise UserError(_('La actualización debe indicar el otrosí de origen.'))
                if solicitud.cierre_proceso_id:
                    raise UserError(_(
                        'Una actualización por otrosí no puede vincularse a una liquidación.'
                    ))
                if solicitud.otro_si_id.contratacion_fuv_en_id != solicitud.contract_id:
                    raise UserError(_(
                        'El otrosí indicado no pertenece al contrato de la solicitud.'
                    ))
                dominio += [('otro_si_id', '=', solicitud.otro_si_id.id)]
                mensaje = _('Este otrosí ya tiene una actualización de pólizas.')
            elif solicitud.origen == OrigenSolicitudPoliza.LIQUIDACION.value:
                if not solicitud.cierre_proceso_id:
                    raise UserError(_('La actualización debe indicar la liquidación de origen.'))
                if solicitud.otro_si_id:
                    raise UserError(_(
                        'Una actualización por liquidación no puede vincularse a un otrosí.'
                    ))
                if solicitud.cierre_proceso_id.contract_id != solicitud.contract_id:
                    raise UserError(_(
                        'La liquidación indicada no pertenece al contrato de la solicitud.'
                    ))
                dominio += [('cierre_proceso_id', '=', solicitud.cierre_proceso_id.id)]
                mensaje = _('Esta liquidación ya tiene una actualización de pólizas.')
            else:
                if solicitud.origen != OrigenSolicitudPoliza.SOPORTE.value:
                    raise UserError(_('El origen de la solicitud de pólizas no es válido.'))
                if solicitud.otro_si_id or solicitud.cierre_proceso_id:
                    raise UserError(_(
                        'La solicitud extraordinaria de Soporte no puede vincularse '
                        'a un otrosí o una liquidación.'
                    ))
                # Soporte puede abrir tantos ciclos independientes como sean
                # necesarios; no se aplica un dominio de unicidad funcional.
                continue
            if self.search_count(dominio):
                raise UserError(mensaje)

    @api.depends(
        'linea_ids.estado_revision',
        'acta_aprobacion_documento_id',
    )
    def _compute_puede_cargar_acta(self):
        for solicitud in self:
            solicitud.puede_cargar_acta = bool(
                not solicitud.acta_aprobacion_documento_id
                and solicitud.linea_ids
                and all(
                    linea.estado_revision == 'aprobada'
                    for linea in solicitud.linea_ids
                )
            )

    @api.depends('origen')
    def _compute_origen_nombre(self):
        """Evita que valores legacy vacíos rompan el widget Selection de OWL."""
        etiquetas = dict(ORIGEN_SOLICITUD_POLIZA_SELECTION)
        etiqueta_inicial = etiquetas[OrigenSolicitudPoliza.CONTRATO.value]
        for solicitud in self:
            solicitud.origen_nombre = etiquetas.get(
                solicitud.origen,
                etiqueta_inicial,
            )

    @api.depends(
        'contract_id',
        'contract_id.new_name',
        'contract_id.name',
        'sgr_contract_id',
        'sgr_contract_id.new_name',
        'sgr_contract_id.name',
        'origen',
        'otro_si_id',
        'cierre_proceso_id',
    )
    def _compute_name(self):
        for record in self:
            contrato = record._contrato_poliza()
            if not contrato:
                record.name = _('Solicitud de pólizas')
            else:
                referencia = contrato.new_name or contrato.name or contrato.id
                prefijo = {
                    OrigenSolicitudPoliza.CONTRATO.value: _('Solicitud de pólizas'),
                    OrigenSolicitudPoliza.OTRO_SI.value: _('Actualización de pólizas por otrosí'),
                    OrigenSolicitudPoliza.LIQUIDACION.value: _('Pólizas para liquidación'),
                    OrigenSolicitudPoliza.SOPORTE.value: _('Solicitud extraordinaria de pólizas'),
                }.get(record.origen, _('Solicitud de pólizas'))
                record.name = _('%s - %s') % (prefijo, referencia)

    @api.depends('contract_id', 'contract_id.correo', 'sgr_contract_id', 'sgr_contract_id.correo')
    def _compute_contratista_email(self):
        """Usa el correo del contrato al que pertenece la solicitud."""
        for record in self:
            contrato = record._contrato_poliza()
            record.contratista_email = (
                (contrato.correo or '').strip()
                if contrato
                else False
            )

    def _action_abrir_formulario(self):
        self.ensure_one()
        carpeta_contratista = self._contrato_poliza().drive_file_id
        if self.solicitud_poliza_drive_folder_id and carpeta_contratista:
            self._asegurar_ubicacion_local_carpeta_polizas(
                self.solicitud_poliza_drive_folder_id,
                carpeta_contratista,
            )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Solicitud de pólizas',
            'res_model': self._name,
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_solicitud_poliza_form').id,
            'res_id': self.id,
            'target': 'current',
        }

    def action_abrir_guia_usuario(self):
        self.ensure_one()
        vista = self.env.ref('Matriz.view_solicitud_poliza_guia_usuario_form')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Guía de usuario · Pólizas'),
            'res_model': 'contrataciones_fuv.solicitud.poliza.guia.usuario',
            'view_mode': 'form',
            'views': [(vista.id, 'form')],
            'target': 'new',
        }

    def _registrar_notificacion_correo(self, evento, destinatarios, detalle=False):
        """Registra una sola nota por envío exitoso, sin duplicar buzones."""
        self.ensure_one()
        destinatarios_unicos = {}
        for destinatario in destinatarios:
            if isinstance(destinatario, dict):
                correo = destinatario.get('correo')
                nombre = destinatario.get('nombre')
                rol = destinatario.get('rol')
            else:
                rol, nombre, correo = destinatario
            correo = (correo or '').strip()
            if not correo:
                continue
            clave = correo.casefold()
            actual = destinatarios_unicos.setdefault(clave, {
                'correo': correo,
                'nombres': [],
                'roles': [],
            })
            if nombre and nombre not in actual['nombres']:
                actual['nombres'].append(nombre)
            if rol and rol not in actual['roles']:
                actual['roles'].append(rol)

        partes = []
        for datos in destinatarios_unicos.values():
            identidad = ' / '.join(datos['roles'])
            if datos['nombres']:
                identidad = '%s%s%s' % (
                    identidad,
                    ' - ' if identidad else '',
                    ' / '.join(datos['nombres']),
                )
            partes.append('%s%s&lt;%s&gt;' % (
                escape(identidad),
                ' ' if identidad else '',
                escape(datos['correo']),
            ))

        if not partes:
            return False
        cuerpo = Markup(
            '<p><strong>Notificación por correo enviada:</strong> %s. '
            '<strong>Destinatarios:</strong> %s%s</p>'
        ) % (
            escape(evento),
            Markup('; ').join(Markup(parte) for parte in partes),
            Markup(' <strong>Detalle:</strong> %s.' % escape(detalle))
            if detalle else Markup(''),
        )
        self.sudo().message_post(body=cuerpo, subtype_xmlid='mail.mt_note')
        return True

    def _usuario_puede_agregar_poliza(self):
        """Limita la edición a los perfiles que pueden entrar al trámite."""
        self.ensure_one()
        return any(
            self.env.user.has_group(grupo)
            for grupo in (
                'Matriz.group_contratacion_coordinador',
                'Matriz.group_contratacion_asis_coordinador',
                'Matriz.group_contratacion_director_juridico',
                'Matriz.group_contratacion_juridico',
                'Matriz.group_soporte',
            )
        )

    def action_agregar_poliza(self):
        self.ensure_one()
        if self.estado == EstadoSolicitudPoliza.FINALIZADA.value:
            raise UserError(_(
                'No se pueden agregar pólizas a una solicitud finalizada.'
            ))
        if not self._usuario_puede_agregar_poliza():
            raise UserError(_(
                'No tiene permisos para agregar pólizas a esta solicitud.'
            ))
        seleccionadas = set(self.linea_ids.mapped('tipo_poliza'))
        if all(codigo in seleccionadas for codigo, _etiqueta in POLIZA_SELECTION):
            raise UserError(_('La solicitud ya contiene todos los tipos de póliza.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Agregar póliza'),
            'res_model': 'contrataciones_fuv.agregar.poliza.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_agregar_poliza_wizard').id,
            'target': 'new',
            'context': {'default_solicitud_id': self.id},
        }

    def action_solicitar_mas_polizas_soporte(self):
        """Compatibilidad: envía las líneas pendientes sin abrir asistentes."""
        self.ensure_one()
        return self.action_soporte_solicitar_al_contratista()

    def _usuario_puede_cargar_otro_documento(self):
        self.ensure_one()
        return (
            self.env.user.has_group('Matriz.group_soporte')
            or self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_contratacion_director_juridico')
            or self.env.user.has_group('Matriz.group_contratacion_juridico')
        )

    def action_cargar_otro_documento(self):
        """Permite anexar documentos complementarios sin tratarlos como pólizas."""
        self.ensure_one()
        if not self._usuario_puede_cargar_otro_documento():
            raise UserError(_(
                'Solo Coordinación, Dirección Jurídica, Jurídica o Soporte '
                'pueden cargar otros documentos.'
            ))
        carpeta = self._crear_carpeta_poliza_solicitada()
        return {
            'name': _('Cargar otro documento'),
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_drive_upload_popup_form').id,
            'target': 'new',
            'context': {
                'default_folder_id': carpeta.folder_id2,
                'default_stage2_id': carpeta.id,
                'solicitud_poliza_otro_documento_id': self.id,
            },
        }

    def _usuario_puede_eliminar_solicitud(self):
        """Limita la eliminación funcional a los dos roles autorizados."""
        return (
            self.env.user.has_group('Matriz.group_contratacion_coordinador')
            or self.env.user.has_group('Matriz.group_soporte')
        )

    def unlink(self):
        if (
            not self.env.su
            and not self.env.context.get('solicitud_poliza_eliminacion_interna')
            and not self._usuario_puede_eliminar_solicitud()
        ):
            raise UserError(_(
                'Solo Coordinación o Soporte pueden eliminar una solicitud de pólizas.'
            ))
        return super().unlink()


class SolicitudPolizaGuiaUsuario(models.TransientModel):
    _name = 'contrataciones_fuv.solicitud.poliza.guia.usuario'
    _description = 'Guía de usuario del trámite de pólizas'

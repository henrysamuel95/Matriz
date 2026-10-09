from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constantes import POLIZA_SELECTION, EstadoSolicitudPoliza


class SolicitudPolizaLinea(models.Model):
    _name = 'contrataciones_fuv.solicitud.poliza.linea'
    _description = 'Póliza incluida en una solicitud'
    _rec_name = 'tipo_poliza_nombre'
    _order = 'id'

    solicitud_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Solicitud',
        required=True,
        ondelete='cascade',
        index=True,
    )
    tipo_poliza = fields.Selection(
        POLIZA_SELECTION,
        string='Tipo de póliza',
        required=True,
        readonly=True,
    )
    tipo_poliza_nombre = fields.Char(
        string='Nombre de la póliza',
        compute='_compute_tipo_poliza_nombre',
    )
    documento_id = fields.Many2one(
        'drive',
        string='Documento cargado',
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    puede_cargar_documento = fields.Boolean(
        string='Puede cargar documento',
        compute='_compute_puede_cargar_documento',
    )
    documento_notificado_revision_id = fields.Many2one(
        'drive',
        string='Documento notificado para revisión',
        readonly=True,
        copy=False,
        ondelete='set null',
        help=(
            'Control técnico para impedir que una misma versión de la póliza '
            'genere dos correos de revisión.'
        ),
    )
    fecha_envio_contratista = fields.Datetime(
        string='Fecha de envío al contratista',
        readonly=True,
        copy=False,
        help='Permite distinguir las pólizas nuevas que todavía deben notificarse.',
    )
    estado_revision = fields.Selection(
        [
            ('pendiente', 'Pendiente de revisión'),
            ('aprobada', 'Aprobada'),
            ('devuelta', 'Devuelta para corrección'),
        ],
        string='Estado de revisión',
        default='pendiente',
        required=True,
        readonly=True,
        copy=False,
    )
    revisado_por_id = fields.Many2one(
        'res.users',
        string='Revisado por',
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    fecha_revision = fields.Datetime(
        string='Fecha de revisión',
        readonly=True,
        copy=False,
    )
    observaciones_juridicas = fields.Text(
        string='Observaciones jurídicas',
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            'solicitud_tipo_unique',
            'unique(solicitud_id, tipo_poliza)',
            'Esta póliza ya fue agregada a la solicitud.',
        ),
    ]

    @api.depends('tipo_poliza')
    def _compute_tipo_poliza_nombre(self):
        etiquetas = dict(POLIZA_SELECTION)
        for linea in self:
            linea.tipo_poliza_nombre = etiquetas.get(
                linea.tipo_poliza,
                linea.tipo_poliza or _('Póliza sin identificar'),
            )

    def name_get(self):
        """Evita mostrar el nombre técnico del modelo en campos Many2one."""
        etiquetas = dict(POLIZA_SELECTION)
        return [
            (
                linea.id,
                etiquetas.get(
                    linea.tipo_poliza,
                    linea.tipo_poliza or _('Póliza sin identificar'),
                ),
            )
            for linea in self
        ]

    @api.depends('fecha_envio_contratista')
    @api.depends_context('uid')
    def _compute_puede_cargar_documento(self):
        es_soporte = self.env.user.has_group('Matriz.group_soporte')
        for linea in self:
            linea.puede_cargar_documento = bool(
                es_soporte or linea.fecha_envio_contratista
            )

    def _validar_carga_documento(self):
        self.ensure_one()
        if not self.puede_cargar_documento:
            raise UserError(_(
                'Esta póliza aún no se ha solicitado al contratista. '
                'Solo Soporte puede cargarla antes de su envío.'
            ))

    def action_cargar_documento(self):
        self.ensure_one()
        self._validar_carga_documento()
        carpeta = self.solicitud_id.solicitud_poliza_drive_folder_id
        if not carpeta or not carpeta.folder_id2:
            raise UserError(_(
                'La solicitud no tiene vinculada su carpeta "Pólizas" en Drive.'
            ))
        etiqueta = dict(POLIZA_SELECTION).get(self.tipo_poliza, self.tipo_poliza)
        return {
            'name': _('Cargar %s') % etiqueta,
            'type': 'ir.actions.act_window',
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_drive_upload_popup_form').id,
            'target': 'new',
            'context': {
                'default_folder_id': carpeta.folder_id2,
                'default_stage2_id': carpeta.id,
                'solicitud_poliza_linea_id': self.id,
            },
        }

    def action_ver_documento(self):
        self.ensure_one()
        if not self.documento_id:
            raise UserError(_('Todavía no hay un documento cargado para esta póliza.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Documento de póliza'),
            'res_model': 'drive',
            'view_mode': 'form',
            'res_id': self.documento_id.id,
            'target': 'new',
            'context': {'default_invisible_todo': True},
        }

    def action_quitar_poliza(self):
        """Retira una selección errada sin eliminar evidencia documental."""
        self.ensure_one()
        solicitud = self.solicitud_id.exists()
        if not solicitud:
            raise UserError(_('No se encontró la solicitud de pólizas.'))
        if solicitud.estado == EstadoSolicitudPoliza.FINALIZADA.value:
            raise UserError(_('No se puede modificar una solicitud finalizada.'))
        if not solicitud._contrato_poliza()._usuario_puede_solicitar_poliza():
            raise UserError(_(
                'Solo Dirección Jurídica, Jurídica o Coordinación pueden '
                'quitar pólizas de la solicitud.'
            ))
        if self.documento_id:
            raise UserError(_(
                'Esta póliza ya tiene un documento cargado. Para conservar la '
                'trazabilidad no puede retirarse mediante esta acción.'
            ))

        etiqueta = self.tipo_poliza_nombre
        fue_notificada = bool(self.fecha_envio_contratista)
        solicitud.message_post(
            body=Markup(
                '<p><strong>Póliza retirada de la solicitud:</strong> %s</p>'
                '<p>No tenía documento cargado. '
                '<strong>Notificada al contratista:</strong> %s</p>'
            ) % (
                escape(etiqueta),
                _('Sí') if fue_notificada else _('No'),
            ),
            subtype_xmlid='mail.mt_note',
        )
        self.sudo().unlink()

        solicitud.invalidate_recordset(['linea_ids', 'portal_token'])
        solicitud._actualizar_estado_segun_revision()
        if not solicitud._hay_lineas_portal_pendientes():
            solicitud.sudo().write({
                'portal_token': False,
                'portal_token_expira': False,
            })
        # No se devuelve una nueva acción sobre el mismo formulario: el botón
        # vive dentro del kanban One2many y una segunda apertura puede dejar el
        # diálogo nativo de confirmación bloqueado. El refresco suave del
        # formulario relee la solicitud al terminar el botón.
        return False

import logging
import os
import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from ..drive import GoogleDriveAuth


_logger = logging.getLogger(__name__)


class ContratacionesSgrOtrosi(models.Model):
    _inherit = 'contrataciones_sgr'

    otrosi_sgr_ids = fields.One2many(
        'otro_si_sgr', 'contratacion_id', string='Otrosíes SGR', readonly=True,
    )

    @api.onchange('otro_si')
    def _onchange_otro_si_sgr(self):
        if not self.otro_si and self.otrosi_sgr_ids:
            self.otro_si = True
            return {'warning': {
                'title': 'Otrosíes relacionados',
                'message': 'No puede desactivar la opción mientras existan otrosíes SGR.',
            }}

    def write(self, vals):
        if 'select_fondos' in vals and any(
            contrato.otrosi_sgr_ids and vals['select_fondos'] != contrato.select_fondos
            for contrato in self
        ):
            raise UserError('No cambie el fondo de un contrato SGR con otrosíes relacionados.')
        if vals.get('otro_si') is False and any(self.mapped('otrosi_sgr_ids')):
            raise UserError('No puede desactivar Otrosí mientras existan registros relacionados.')
        return super().write(vals)

    def action_nuevo_otrosi_sgr(self):
        self.ensure_one()
        if self.estado_contratacion != 'contratado':
            raise UserError('Solo puede crear otrosíes cuando el contrato SGR esté Contratado.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Nuevo otrosí SGR',
            'res_model': 'otro_si_sgr',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_otro_si_sgr_form').id,
            'target': 'current',
            'context': {
                'default_contratacion_id': self.id,
                'default_select_fondos': self.select_fondos,
            },
        }


class OtroSiSgr(models.Model):
    _name = 'otro_si_sgr'
    _description = 'Otrosí de contratación SGR'
    _order = 'id desc'

    name = fields.Char(string='Referencia', readonly=True, copy=False)
    contratacion_id = fields.Many2one(
        'contrataciones_sgr', string='Contrato SGR', required=True,
        index=True, ondelete='restrict', copy=False,
    )
    proyecto_relacionado = fields.Many2one(
        'contratacion', related='contratacion_id.proyecto_relacionado',
        string='Proyecto', readonly=True,
    )
    tipo = fields.Selection([
        ('adicion', 'Adición'),
        ('prorroga', 'Prórroga'),
        ('adicion_prorroga', 'Adición y prórroga'),
        ('modificatorio', 'Modificatorio'),
        ('suspension', 'Suspensión'),
        ('reinicio', 'Reinicio'),
    ], string='Tipo de otrosí', required=True)
    select_fondos = fields.Selection([
        ('es_produccion', 'Producción'),
        ('es_personal_fuv', 'Costos FUV'),
        ('contratos_vco', 'Contratos VCO'),
    ], string='Fondo de la adición', required=True)
    fecha_inicio = fields.Date(string='Fecha de inicio')
    fecha_fin = fields.Date(string='Fecha de terminación')
    valor_adicion = fields.Monetary(
        string='Valor de la adición', currency_field='currency_id', default=0,
    )
    currency_id = fields.Many2one(
        'res.currency', related='contratacion_id.currency_id', readonly=True,
    )
    justificacion = fields.Text(string='Justificación')
    carpeta_drive_id = fields.Many2one(
        'drive.file', string='Carpeta del otrosí en Drive', readonly=True,
        copy=False, ondelete='set null',
    )
    carpeta_pendiente = fields.Boolean(string='Carpeta pendiente', readonly=True, copy=False)
    documento_ids = fields.One2many(
        'otro_si_sgr.documento', 'otrosi_id', string='Documentos', copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        adiciones_por_fondo = {}
        for vals in vals_list:
            contrato = self.env['contrataciones_sgr'].browse(
                vals.get('contratacion_id') or 0
            ).exists()
            if not contrato or contrato.estado_contratacion != 'contratado':
                raise ValidationError(
                    'El otrosí debe pertenecer a un contrato SGR en estado Contratado.'
                )
            vals.setdefault('select_fondos', contrato.select_fondos)
            clave = (contrato.id, vals['select_fondos'])
            adiciones_por_fondo[clave] = (
                adiciones_por_fondo.get(clave, 0.0)
                + max(vals.get('valor_adicion') or 0.0, 0.0)
            )
            vals.pop('carpeta_drive_id', None)
            vals.pop('carpeta_pendiente', None)
        for (contrato_id, fondo), adicion in adiciones_por_fondo.items():
            contrato = self.env['contrataciones_sgr'].browse(contrato_id)
            self._validar_saldo_adicion(contrato, fondo, adicion)
        records = super().create(vals_list)
        records._invalidar_saldos_proyecto()
        for record in records:
            record.contratacion_id.sudo().otro_si = True
            record.name = 'Otrosí SGR %s' % record.id
            record.buscar_carpeta()
        return records

    def write(self, vals):
        if 'contratacion_id' in vals or 'carpeta_drive_id' in vals:
            raise UserError('No puede cambiar el contrato ni la carpeta de un otrosí SGR.')
        if 'valor_adicion' in vals or 'select_fondos' in vals:
            if len(self) != 1:
                raise UserError('Modifique el fondo o valor de un otrosí a la vez.')
            fondo = vals.get('select_fondos', self.select_fondos)
            nuevo_valor = vals.get('valor_adicion', self.valor_adicion) or 0.0
            incremento = (nuevo_valor if fondo != self.select_fondos else
                          max(nuevo_valor - self.valor_adicion, 0.0))
            self._validar_saldo_adicion(
                self.contratacion_id, fondo, incremento,
            )
        result = super().write(vals)
        if 'valor_adicion' in vals or 'select_fondos' in vals:
            self._invalidar_saldos_proyecto()
        return result

    def _invalidar_saldos_proyecto(self):
        contratos = self.env['contrataciones_sgr'].sudo().search([
            ('proyecto_relacionado', 'in', self.mapped('proyecto_relacionado').ids),
        ])
        contratos.invalidate_recordset([
            'sgr_disponible_produccion', 'sgr_disponible_costos_fuv',
            'sgr_disponible_vco', 'sgr_disponible_contratos',
        ])

    def _validar_saldo_adicion(self, contrato, fondo, adicion):
        if not adicion:
            return
        campos = {
            'es_produccion': 'sgr_disponible_produccion',
            'es_personal_fuv': 'sgr_disponible_costos_fuv',
            'contratos_vco': 'sgr_disponible_vco',
        }
        if fondo not in campos:
            raise ValidationError('Seleccione el fondo que financiará la adición SGR.')
        contrato.invalidate_recordset(list(campos.values()))
        if float_compare(
            adicion, getattr(contrato, campos[fondo]),
            precision_rounding=contrato.currency_id.rounding or 0.01,
        ) > 0:
            raise ValidationError('La adición supera el saldo disponible del fondo SGR.')

    @api.constrains('fecha_inicio', 'fecha_fin', 'valor_adicion', 'tipo')
    def _check_datos_otrosi_sgr(self):
        for record in self:
            if record.fecha_inicio and record.fecha_fin and record.fecha_fin < record.fecha_inicio:
                raise ValidationError('La fecha final del otrosí no puede ser anterior a la inicial.')
            if record.valor_adicion < 0:
                raise ValidationError('El valor de la adición no puede ser negativo.')
            if record.valor_adicion and record.tipo not in ('adicion', 'adicion_prorroga', 'modificatorio'):
                raise ValidationError('Este tipo de otrosí no admite valor de adición.')

    def buscar_carpeta(self):
        for record in self:
            if record.carpeta_drive_id and record.carpeta_drive_id.folder_id2:
                continue
            try:
                contrato = record.contratacion_id
                if not contrato.drive_file_id:
                    contrato.buscar_carpeta()
                parent = contrato.drive_file_id
                if not parent or not parent.folder_id2:
                    raise UserError('El contrato SGR no tiene carpeta de Drive.')
                drive = GoogleDriveAuth.get_instance().get_drive()
                if not drive:
                    raise UserError('No se pudo conectar con Google Drive.')
                folder_name = 'Otrosi SGR %s' % record.id
                existing = drive.ListFile({'q': (
                    "title='%s' and '%s' in parents and trashed=false and "
                    "mimeType='application/vnd.google-apps.folder'"
                ) % (folder_name, parent.folder_id2)}).GetList()
                folder = existing[0] if existing else drive.CreateFile({
                    'title': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': parent.folder_id2}],
                })
                if not existing:
                    folder.Upload()
                if not folder.get('id'):
                    raise UserError('Drive no confirmó la carpeta del otrosí.')
                linked = self.env['drive.file'].sudo().search([
                    ('folder_id2', '=', folder['id']),
                ], limit=1)
                if not linked:
                    linked = self.env['drive.file'].sudo().create({
                        'folder_name': folder_name,
                        'folder_id2': folder['id'],
                        'parent_folder_id': parent.id,
                        'proyecto_relacionado': contrato.proyecto_relacionado.id,
                    })
                super(OtroSiSgr, record).write({
                    'carpeta_drive_id': linked.id, 'carpeta_pendiente': False,
                })
            except Exception:
                _logger.exception('No se pudo vincular la carpeta del otrosí SGR %s', record.id)
                super(OtroSiSgr, record).write({'carpeta_pendiente': True})
        return True

    def action_reintentar_carpeta(self):
        self.ensure_one()
        self.buscar_carpeta()
        if self.carpeta_pendiente:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Carpeta pendiente',
                    'message': 'No se pudo crear la carpeta en Drive. Revise la conexión y vuelva a intentar.',
                    'type': 'warning',
                    'sticky': False,
                },
            }
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class OtroSiSgrDocumento(models.Model):
    _name = 'otro_si_sgr.documento'
    _description = 'Documento de otrosí SGR'
    _order = 'id'

    otrosi_id = fields.Many2one(
        'otro_si_sgr', string='Otrosí SGR', required=True,
        index=True, ondelete='cascade',
    )
    name = fields.Char(string='Documento', required=True)
    archivo_nombre = fields.Char(string='Archivo', readonly=True, copy=False)
    drive_id = fields.Char(string='ID en Drive', readonly=True, copy=False)
    drive_record_id = fields.Many2one('drive', readonly=True, copy=False, ondelete='set null')
    drive_url = fields.Char(string='Enlace en Drive', readonly=True, copy=False)
    fecha_carga = fields.Datetime(string='Fecha de carga', readonly=True, copy=False)

    def write(self, vals):
        if 'otrosi_id' in vals or {'drive_id', 'drive_record_id', 'drive_url', 'fecha_carga'} & set(vals):
            raise UserError('No puede mover el documento ni modificar manualmente su vínculo con Drive.')
        return super().write(vals)

    def action_subir_documento(self):
        self.ensure_one()
        if self.drive_id:
            raise UserError('Elimine el archivo actual desde el visor antes de reemplazarlo.')
        folder = self.otrosi_id.carpeta_drive_id
        if not folder or not folder.folder_id2:
            raise UserError('Cree primero la carpeta de Drive del otrosí SGR.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Subir archivo: %s' % self.name,
            'res_model': 'drive',
            'view_mode': 'form',
            'view_id': self.env.ref('Matriz.view_drive_upload_popup_form').id,
            'target': 'new',
            'context': {
                'default_stage2_id': folder.id,
                'default_folder_id': folder.folder_id2,
                'default_proyecto_relacionado': self.otrosi_id.proyecto_relacionado.id,
                'tipo_documento_upload': 'sgr_otrosi_documento',
                'sgr_otrosi_documento_id': self.id,
            },
        }

    def action_ver_documento(self):
        self.ensure_one()
        if not self.drive_id:
            raise UserError('Este documento aún no está cargado en Drive.')
        drive_record = self.drive_record_id.exists()
        if not drive_record:
            folder = self.otrosi_id.carpeta_drive_id
            drive_record = self.env['drive'].sudo().with_context(
                sgr_otrosi_documento_id=False,
                tipo_documento_upload=False,
                skip_drive_duplicate_cleanup=True,
            ).search([
                ('file_id', '=', self.drive_id),
                ('stage2_id', '=', folder.id),
            ], limit=1)
            if not drive_record:
                drive_record = self.env['drive'].sudo().with_context(
                    sgr_otrosi_documento_id=False,
                    tipo_documento_upload=False,
                    skip_drive_duplicate_cleanup=True,
                ).create({
                    'file_name': self.archivo_nombre or self.name,
                    'file_id': self.drive_id,
                    'folder_id': folder.folder_id2,
                    'stage2_id': folder.id,
                    'proyecto_relacionado': self.otrosi_id.proyecto_relacionado.id,
                    'file_url': self.drive_url,
                    'show_on_dashboard': False,
                })
            super(OtroSiSgrDocumento, self).write({'drive_record_id': drive_record.id})
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'drive',
            'res_id': drive_record.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _registrar_drive(self, drive_record):
        self.ensure_one()
        folder = self.otrosi_id.carpeta_drive_id
        if self.drive_id or not drive_record.file_id or drive_record.stage2_id != folder:
            raise UserError('El archivo no corresponde a la carpeta de este otrosí SGR.')
        super(OtroSiSgrDocumento, self).write({
            'drive_record_id': drive_record.id,
            'drive_id': drive_record.file_id,
            'drive_url': drive_record.file_url or
                'https://drive.google.com/file/d/%s/view' % drive_record.file_id,
            'archivo_nombre': drive_record.file_name,
            'fecha_carga': fields.Datetime.now(),
        })


class DriveDocumentoOtroSiSgr(models.Model):
    _inherit = 'drive'

    @api.model
    def create(self, vals):
        documento_id = self.env.context.get('sgr_otrosi_documento_id')
        if documento_id:
            documento = self.env['otro_si_sgr.documento'].browse(documento_id).exists()
            if not documento or documento.drive_id:
                raise UserError('El documento del otrosí ya no está disponible para carga.')
            documento.check_access_rights('write')
            documento.check_access_rule('write')
            if not vals.get('file_data'):
                raise UserError('Seleccione un archivo antes de guardar.')
            extension = os.path.splitext(vals.get('file_name') or '')[1].lower()
            if not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
                raise UserError('El archivo debe tener una extensión válida.')
            folder = documento.otrosi_id.carpeta_drive_id
            if not folder or not folder.folder_id2:
                raise UserError('El otrosí SGR no tiene carpeta de Drive.')
            nombre_seguro = re.sub(r'[^\w.-]+', '_', documento.name, flags=re.UNICODE).strip('_')
            vals = dict(vals,
                file_name='SGR_OTROSI_%s_DOC_%s_%s' % (
                    documento.otrosi_id.id, documento.id, nombre_seguro or 'Documento'
                ) + extension,
                folder_id=folder.folder_id2,
                stage2_id=folder.id,
                proyecto_relacionado=documento.otrosi_id.proyecto_relacionado.id,
            )
        record = super().create(vals)
        if documento_id and record.file_id:
            try:
                drive = GoogleDriveAuth.get_instance().get_drive()
                file = drive.CreateFile({'id': record.file_id})
                permissions = file.GetPermissions()
                if not any(item.get('type') == 'anyone' and item.get('role') in
                           ('reader', 'writer', 'owner') for item in permissions):
                    file.InsertPermission({
                        'type': 'anyone', 'value': 'anyone', 'role': 'reader',
                    })
            except Exception as error:
                _logger.exception('No se pudo confirmar el acceso del documento del otrosí SGR %s', documento_id)
                raise UserError('El archivo se subió, pero no se pudo confirmar el acceso en Drive.') from error
        return record

    def action_save_and_close(self):
        documento_id = self.env.context.get('sgr_otrosi_documento_id')
        if not documento_id:
            return super().action_save_and_close()
        self.ensure_one()
        documento = self.env['otro_si_sgr.documento'].browse(documento_id).exists()
        if not documento or documento.drive_id:
            raise UserError('El documento del otrosí ya no está disponible para carga.')
        result = super().action_save_and_close()
        documento._registrar_drive(self)
        return result

    def unlink(self):
        documentos = self.env['otro_si_sgr.documento'].search([
            ('drive_record_id', 'in', self.ids),
        ])
        result = super().unlink()
        for documento in documentos.exists():
            super(OtroSiSgrDocumento, documento).write({
                'drive_record_id': False,
                'drive_id': False,
                'drive_url': False,
                'archivo_nombre': False,
                'fecha_carga': False,
            })
        return result

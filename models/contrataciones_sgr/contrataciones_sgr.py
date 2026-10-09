import logging
import uuid
import json

from lxml import etree

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression

from ..drive import GoogleDriveAuth


_logger = logging.getLogger(__name__)


def _sgr_arch_solo_lectura_contratado(arch_text):
    arch = etree.fromstring(arch_text)
    contratado = [('estado_contratacion', '=', 'contratado')]
    for node in arch.xpath('//field[not(ancestor::field)]'):
        modifiers = json.loads(node.get('modifiers') or '{}')
        previous = modifiers.get('readonly')
        if previous is True:
            continue
        if isinstance(previous, tuple):
            previous = [previous]
        modifiers['readonly'] = (
            expression.OR([contratado, previous]) if previous else contratado
        )
        node.set('modifiers', json.dumps(modifiers))

    hidden_when_contracted = {
        'action_preparar_documentos', 'action_actualizar_documentos_proveedor',
        'generar_contrato_sgr_pn_docx', 'generar_contrato_sgr_pj_docx',
        'action_subir_documento',
    }
    for node in arch.xpath('//button'):
        if node.get('name') not in hidden_when_contracted:
            continue
        modifiers = json.loads(node.get('modifiers') or '{}')
        previous = modifiers.get('invisible')
        if previous is True:
            continue
        if isinstance(previous, tuple):
            previous = [previous]
        condition = ([('parent.estado_contratacion', '=', 'contratado')]
                     if node.getparent().tag == 'tree' else contratado)
        modifiers['invisible'] = (
            expression.OR([condition, previous]) if previous else condition
        )
        node.set('modifiers', json.dumps(modifiers))
    return etree.tostring(arch, encoding='unicode')


class ContratacionesSgr(models.Model):
    _name = 'contrataciones_sgr'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Contrataciones de Regalías'
    _rec_name = 'name'

    name = fields.Char(string='Nombre de la contratación', required=True, tracking=True)
    bpin = fields.Char(string='BPIN', compute='_compute_bpin')
    proyecto_relacionado = fields.Many2one(
        'contratacion', string='Proyecto', required=True, ondelete='cascade', index=True,
        tracking=True,
    )
    proyecto_convenio_id = fields.Many2one(
        'contratacion', string='Proyecto o convenio',
        related='proyecto_relacionado', readonly=True,
    )
    drive_file_id = fields.Many2one(
        'drive.file', string='Carpeta del contrato en Drive', copy=False,
        readonly=True, ondelete='set null',
    )

    def _sgr_validar_edicion_contratado(self):
        if self.env.su or self.env.user.has_group('Matriz.group_soporte'):
            return
        if any(record.estado_contratacion == 'contratado' for record in self):
            raise UserError(
                'La contratación SGR ya está Contratada. Solo Soporte puede modificarla.'
            )

    def get_view(self, view_id=None, view_type='form', **options):
        result = super().get_view(view_id=view_id, view_type=view_type, **options)
        if view_type != 'form' or self.env.user.has_group('Matriz.group_soporte'):
            return result
        result['arch'] = _sgr_arch_solo_lectura_contratado(result['arch'])
        return result

    @api.onchange('select_persona')
    def _onchange_select_persona(self):
        clase = ('persona_natural' if self.select_persona == 'contrato_laboral'
                 else self.select_persona)
        if self.contratista_proveedor_relacionado and clase and (
            self.contratista_proveedor_relacionado.clase_persona != clase
        ):
            self.contratista_proveedor_relacionado = False
            self.nombre_contratista = False
            self.new_name = False
        domain = [('clase_persona', '=', clase)] if clase else []
        return {'domain': {'contratista_proveedor_relacionado': domain}}

    @api.onchange('contratista_proveedor_relacionado')
    def _onchange_contratista_proveedor_relacionado(self):
        for record in self:
            contratista = record.contratista_proveedor_relacionado
            if contratista:
                if not record.select_persona and contratista.clase_persona in (
                    'persona_natural', 'persona_juridica',
                ):
                    record.select_persona = contratista.clase_persona
                record.name = contratista.name
                record.new_name = contratista.name
                record.nombre_contratista = contratista.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if (vals.get('estado_contratacion') == 'contratado'
                    and not self.env.su
                    and not self.env.user.has_group('Matriz.group_soporte')):
                raise UserError(
                    'Solo Soporte puede crear una contratación SGR en estado Contratado.'
                )
            contratista_id = vals.get('contratista_proveedor_relacionado')
            if contratista_id:
                contratista = self.env['contratista_proveedor'].browse(contratista_id).exists()
                if not contratista or not contratista.name:
                    raise ValidationError('El contratista seleccionado debe tener un nombre.')
                tipo = vals.get('select_persona')
                clase = 'persona_natural' if tipo == 'contrato_laboral' else tipo
                if clase and contratista.clase_persona != clase:
                    raise ValidationError('El tipo de persona no coincide con el contratista seleccionado.')
                vals.update({
                    'name': contratista.name,
                    'new_name': contratista.name,
                    'nombre_contratista': contratista.name,
                })
                if not vals.get('select_persona') and contratista.clase_persona in (
                    'persona_natural', 'persona_juridica',
                ):
                    vals['select_persona'] = contratista.clase_persona
                vals['estado_sincronizacion_drive'] = 'pendiente'
            if not vals.get('select_persona'):
                raise ValidationError('Seleccione el tipo de persona antes de crear la contratación SGR.')
        records = super().create(vals_list)
        for record in records:
            record.buscar_carpeta()
            if record.select_persona:
                record.action_preparar_documentos()
        return records

    def buscar_carpeta(self):
        """Crea la carpeta del contrato bajo 3. CONTRATOS PS del proyecto SGR."""
        for record in self:
            if record.drive_file_id:
                continue
            record._sgr_validar_edicion_contratado()

            parent = self.env['drive_stage'].sudo().search([
                ('name', '=', '3. CONTRATOS PS'),
                ('proyecto_relacionado', '=', record.proyecto_relacionado.id),
                ('folder_id', '!=', False),
            ], limit=1)
            if not parent:
                raise UserError(
                    'El proyecto no tiene lista en Drive la carpeta "3. CONTRATOS PS". '
                    'Verifique la sincronización del proyecto antes de crear el contrato.'
                )

            folder_name = '%s_%s' % (record.name, uuid.uuid4().hex[:8])
            try:
                drive = GoogleDriveAuth.get_instance().get_drive()
                if not drive:
                    raise UserError('No se pudo conectar con Google Drive.')
                folder = drive.CreateFile({
                    'title': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [{'id': parent.folder_id}],
                })
                folder.Upload()
                folder_id = folder['id']
            except UserError:
                raise
            except Exception as error:
                _logger.exception('No se pudo crear la carpeta Drive del contrato SGR %s', record.id)
                raise UserError('No se pudo crear la carpeta del contrato en Drive: %s' % error) from error

            drive_file = self.env['drive.file'].sudo().create({
                'folder_name': folder_name,
                'folder_id2': folder_id,
                'drive_id': parent.id,
                'proyecto_relacionado': record.proyecto_relacionado.id,
                'select_persona': record.select_persona or False,
            })
            record.write({'drive_file_id': drive_file.id})
            drive_file._crear_carpeta_informes()

    def _mover_carpeta_drive_a_papelera(self, folder_id):
        try:
            drive = GoogleDriveAuth.get_instance().get_drive()
            if not drive:
                raise UserError('No se pudo conectar con Google Drive.')
            drive.CreateFile({'id': folder_id}).Trash()
        except Exception as error:
            _logger.exception('No se pudo mover a la papelera la carpeta SGR %s', folder_id)
            raise UserError(
                'No se eliminó la contratación porque no se pudo mover su carpeta a la papelera de Drive: %s'
                % error
            ) from error

    def unlink(self):
        self._sgr_validar_edicion_contratado()
        carpetas = self.mapped('drive_file_id').sudo().exists()
        for contrato in self:
            carpeta = contrato.drive_file_id.sudo()
            if not carpeta or not carpeta.folder_id2:
                continue
            parent = carpeta.drive_id
            if (
                not parent or parent.name != '3. CONTRATOS PS'
                or not parent.folder_id
                or carpeta.folder_id2 == parent.folder_id
                or carpeta.proyecto_relacionado.id != contrato.proyecto_relacionado.id
                or parent.proyecto_relacionado.id != contrato.proyecto_relacionado.id
            ):
                raise UserError(
                    'La carpeta vinculada no corresponde a la carpeta exclusiva de este contrato SGR. '
                    'Soporte debe revisar el vínculo antes de eliminarla.'
                )
        for carpeta in carpetas:
            otros_sgr = self.env['contrataciones_sgr'].sudo().search_count([
                ('drive_file_id', '=', carpeta.id), ('id', 'not in', self.ids),
            ])
            otros_fuv = self.env['contrataciones_fuv'].sudo().search_count([
                ('drive_file_id', '=', carpeta.id),
            ])
            otros_otrosi = self.env['otro_si_sgr'].sudo().search_count([
                ('carpeta_drive_id', '=', carpeta.id),
            ])
            if otros_sgr or otros_fuv or otros_otrosi:
                raise UserError(
                    'La carpeta de Drive está vinculada a otra contratación. '
                    'No se puede eliminar automáticamente.'
                )

        result = super().unlink()
        for carpeta in carpetas:
            if carpeta.folder_id2:
                self._mover_carpeta_drive_a_papelera(carpeta.folder_id2)
            carpeta.with_context(sgr_carpeta_drive_en_papelera=True).unlink()
        return result

    def write(self, vals):
        if vals:
            self._sgr_validar_edicion_contratado()
        if 'select_persona' in vals and not vals['select_persona']:
            raise ValidationError('El tipo de persona no puede quedar vacío en una contratación SGR.')
        if 'contratista_proveedor_relacionado' in vals or 'select_persona' in vals:
            for record in self:
                cambiando_proveedor = (
                    'contratista_proveedor_relacionado' in vals and
                    vals['contratista_proveedor_relacionado'] != record.contratista_proveedor_relacionado.id
                )
                cambiando_tipo = (
                    'select_persona' in vals and vals['select_persona'] != record.select_persona
                )
                if (cambiando_proveedor or cambiando_tipo) and any(
                    doc.drive_id for doc in record.documento_ids
                ):
                    raise UserError(
                        'No cambie el contratista ni el tipo de persona después de cargar documentos. '
                        'Revise primero los archivos del contrato SGR.'
                    )
                if (cambiando_proveedor or cambiando_tipo) and (
                    vals.get('contratista_proveedor_relacionado') or
                    record.contratista_proveedor_relacionado
                ):
                    vals = dict(vals, estado_sincronizacion_drive='pendiente')
        if 'select_persona' in vals and 'contratista_proveedor_relacionado' not in vals:
            tipo = vals['select_persona']
            clase = 'persona_natural' if tipo == 'contrato_laboral' else tipo
            for record in self:
                contratista = record.contratista_proveedor_relacionado
                if contratista and clase and contratista.clase_persona != clase:
                    raise ValidationError('El tipo de persona no coincide con el contratista seleccionado.')
        if vals.get('contratista_proveedor_relacionado'):
            contratista = self.env['contratista_proveedor'].browse(
                vals['contratista_proveedor_relacionado']
            ).exists()
            if not contratista or not contratista.name:
                raise ValidationError('El contratista seleccionado debe tener un nombre.')
            for record in self:
                tipo = vals.get('select_persona') or record.select_persona
                clase = 'persona_natural' if tipo == 'contrato_laboral' else tipo
                if clase and contratista.clase_persona != clase:
                    raise ValidationError('El tipo de persona no coincide con el contratista seleccionado.')
            vals = dict(vals, name=contratista.name, new_name=contratista.name,
                        nombre_contratista=contratista.name)
            if len(self) == 1 and not self.select_persona and not vals.get('select_persona') and contratista.clase_persona in (
                'persona_natural', 'persona_juridica',
            ):
                vals['select_persona'] = contratista.clase_persona
        result = super().write(vals)
        if vals.get('select_persona'):
            self.action_preparar_documentos()
        return result

    @api.depends('proyecto_relacionado')
    def _compute_bpin(self):
        for record in self:
            proyecto = record.proyecto_relacionado
            informacion_base = proyecto.informacion_base_id or proyecto.informacion_base_agregar_ids[:1]
            if proyecto and not informacion_base:
                informacion_base = self.env['informacion_base_agregar'].search([
                    '|',
                    ('contratacion_id', '=', proyecto.id),
                    ('informacion_base_agregar_ids', '=', proyecto.id),
                ], limit=1)
            record.bpin = informacion_base.bpin if informacion_base else False

    @api.constrains('proyecto_relacionado')
    def _check_proyecto_regalias(self):
        for record in self:
            proyecto = record.proyecto_relacionado
            if proyecto and not proyecto._es_proyecto_regalias_en_informacion_base():
                raise ValidationError('Las contrataciones SGR solo pueden asociarse a proyectos de Regalías.')

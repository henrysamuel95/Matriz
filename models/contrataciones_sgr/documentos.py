import base64
import logging
import os
import re
import tempfile
import unicodedata
import uuid
from html import escape

from markupsafe import Markup, escape as markup_escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..drive import GoogleDriveAuth


_logger = logging.getLogger(__name__)
_CAMPOS_DRIVE = {'drive_id', 'drive_record_id', 'drive_url', 'fecha_carga'}


# Code, label, condition. The checklist follows SOLICITUD!A28:E48.
DOCUMENTOS_NATURAL = (
    ('identidad', 'Documento de identidad', ''),
    ('rut', 'RUT actualizado', ''),
    ('hoja_vida', 'Hoja de vida', ''),
    ('estudios', 'Certificados de estudio', ''),
    ('laborales', 'Certificados laborales', ''),
    ('salud', 'Afiliación a salud', 'No mayor a 30 días'),
    ('pension', 'Afiliación a pensión', 'No mayor a 30 días'),
    ('examen', 'Examen preocupacional', 'No mayor a 30 días'),
    ('contraloria', 'Certificado de Contraloría', 'No mayor a 30 días'),
    ('procuraduria', 'Certificado de Procuraduría', 'No mayor a 30 días'),
    ('judiciales', 'Antecedentes judiciales', 'No mayor a 30 días'),
    ('sexuales', 'Inhabilidades por delitos sexuales', 'No mayor a 30 días'),
    ('medidas', 'Medidas correctivas', 'No mayor a 30 días'),
    ('inhabilidades', 'Certificado de inhabilidades', 'No mayor a 30 días'),
    ('libreta', 'Libreta militar', 'Menor de 50 años, si aplica'),
    ('pensionado', 'Resolución de pensionado vigente', 'Si aplica'),
    ('bancaria', 'Certificación bancaria', 'Vigente'),
    ('juramentada', 'Declaración juramentada', ''),
    ('tarjeta', 'Tarjeta profesional', 'Cuando aplique'),
    ('disciplinarios', 'Antecedentes disciplinarios del ejercicio de la profesión', 'Cuando aplique'),
    ('estudio_previo', 'Estudio previo', ''),
)

DOCUMENTOS_JURIDICA = (
    ('propuesta', 'Propuesta', ''),
    ('camara', 'Cámara de comercio', 'Actualizada'),
    ('rut', 'RUT actualizado', ''),
    ('rup', 'RUP', 'Si aplica'),
    ('hoja_vida', 'Hoja de vida jurídica', 'Actualizada'),
    ('contraloria', 'Certificado de Contraloría', 'No mayor a 30 días'),
    ('procuraduria', 'Certificado de Procuraduría', 'No mayor a 30 días'),
    ('cedula_representante', 'Cédula del representante legal', ''),
    ('libreta', 'Libreta militar', 'Menor de 50 años, si aplica'),
    ('contraloria_representante', 'Contraloría del representante legal', ''),
    ('procuraduria_representante', 'Procuraduría del representante legal', ''),
    ('antecedentes_representante', 'Antecedentes judiciales, medidas correctivas e inhabilidades sexuales', 'No mayor a 30 días'),
    ('inhabilidades', 'Certificado de inhabilidades', ''),
    ('facturacion', 'Resolución de facturación electrónica', ''),
    ('experiencia', 'Experiencia laboral', ''),
    ('parafiscales', 'Certificado de pago de parafiscales', ''),
    ('seguridad_social', 'Planilla de seguridad social', ''),
    ('junta_contadores', 'Certificado de Junta de Contadores', 'Si aplica'),
    ('cedula_revisor_contador', 'Cédula de revisor fiscal y/o contador', 'Si aplica'),
    ('bancario', 'Certificado bancario', 'Vigente'),
    ('anexo_tributario', 'Anexo tributario', ''),
    ('estudio_previo', 'Estudio previo', ''),
)

DOCUMENTOS_SOPORTE = (
    ('acta_inicio', 'Acta de inicio', ''),
    ('polizas', 'Pólizas', ''),
    ('cdp', 'CDP', ''),
    ('rpc', 'RPC', ''),
    ('informe', 'Adjunto del informe', ''),
    ('contrato', 'Contrato', ''),
    ('firmado', 'Contrato firmado', ''),
    ('liquidacion', 'Acta de liquidación', ''),
    ('acta_aprobacion', 'Acta de aprobación', ''),
    ('estudio_previo', 'Estudio previo', ''),
)

SOPORTES_ANTERIORES = {
    'acta_inicio': ('acta_inicio_doc', 'acta_inicio_doc_fname'),
    'polizas': ('polizas', 'polizas_fname'),
    'cdp': ('anexo_tributario1', None),
    'rpc': ('cdp', 'cdp_doc_fname'),
    'informe': ('informe_adjunto', None),
    'contrato': ('contratos_doc', 'contratos_doc_fname'),
    'firmado': ('contratos_firmado_doc', 'contratos_firmado_doc_fname'),
    'liquidacion': ('acta_liquidacion', None),
}


# Field on contratista_proveedor, followed by the filename prefix used there.
# Only documents with an unambiguous equivalent in the SGR checklist are copied.
DOCUMENTOS_PROVEEDOR_NATURAL = {
    'identidad': ('documento_identidad_c_id', '1_Documento de identidad_'),
    'rut': ('rut_actualizado_id', '2_RUT_'),
    'hoja_vida': ('hoja_vida_id', '3_Hoja de vida_'),
    'estudios': ('certificado_estudio_id', '4_Certificado estudio_'),
    'laborales': ('certificado_laborales_id', '5_Certificado laborales_'),
    'salud': ('cert_afiliacion_salud_id', '7_Cert_afiliación salud_'),
    'pension': ('cert_afiliacion_pension_id', '8_Cert_afiliación a pensión_'),
    'examen': ('cert_examen_preocupacional_id', '9_Cert_examen preocupacional_'),
    'contraloria': ('cert_contraloria_id', '13_Cert_contraloría_'),
    'procuraduria': ('cert_procuraduria_id', '14_Cert_procuraduría_'),
    'judiciales': ('cert_policia_judicial_id', '15_Cert_policía judicial_'),
    'medidas': ('cert_medidas_cautelares_id', '16_Cert_medidas correctivas_'),
    'inhabilidades': ('cert_inhabilidades_id', '17_Cert_inhabilidades_'),
    'libreta': ('cert_libreta_militar_id', '18_Cert_libreta militar_'),
    'pensionado': ('resolucion_pensionado_vigente_id', '19_Resolución de pensionado vigente_'),
    'bancaria': ('certificado_bancario_vigente_id', '20_Certificado bancario_'),
    'juramentada': ('declaracion_juramentada_id', '22_Declaración juramentada_'),
    'tarjeta': ('tarjeta_profesional_id', '21_Tarjeta profesional_'),
}

DOCUMENTOS_PROVEEDOR_JURIDICA = {
    'propuesta': ('propuesta_j_id', '1_Propuesta_'),
    'camara': ('camara_c_actualizada_j_id', '2_Cámara de comercio_'),
    'rut': ('rut_actualizado_j_id', '3_RUT_'),
    'rup': ('rup_j_id', '4_RUP_'),
    'hoja_vida': ('hoja_vida_j_id', '5_Hoja de vida jurídica_'),
    'contraloria': ('cert_contraloria_j_id', '6_Certificado contraloría_'),
    'procuraduria': ('cert_procuraduria_j_id', '7_Certificado procuraduría_'),
    'cedula_representante': ('cedula_representante_legal_id', '8_Cédula representante legal_'),
    'contraloria_representante': ('cert_contraloria_repre_legal_id', '10_Cert_contraloría representante legal_'),
    'procuraduria_representante': ('cert_procuraduria_repre_legal_id', '11_Cert_procuraduría representante legal_'),
    'inhabilidades': ('cert_inhabilidades_j_id', '15_Certificado inhabilidades_'),
    'facturacion': ('resolucion_fact_elect_j_id', '16_Resolución de facturación electrónica_'),
    'experiencia': ('experiencia_laboral_j_id', '17_Experiencia laboral_'),
    'parafiscales': ('cert_pago_paraf_j_id', '18_Certificado pago parafiscales_'),
    'seguridad_social': ('planilla_seguridad_social_j_id', '19_Planilla de seguridad social_'),
    'junta_contadores': ('cert_junta_contadores_j_id', '20_Certificado junta de contadores_'),
    'cedula_revisor_contador': ('cedula_revisor_fiscal_contador_j_id', '21_Documento de identidad revisor fiscal_'),
    'bancario': ('certificado_bancario_vigente_j_id', '22_Certificado bancario'),
}


def _nombre_normalizado(value):
    value = re.sub(r'^copia\s+de\s+', '', (value or '').strip(), flags=re.IGNORECASE)
    value = unicodedata.normalize('NFKD', value)
    return ''.join(char for char in value if not unicodedata.combining(char)).lower()


def _titulo_sgr(requisito, extension, unique=False):
    label = re.sub(r'[\\/:*?"<>|]', ' ', requisito.name).strip()
    suffix = '_%s' % uuid.uuid4().hex[:8] if unique else ''
    return '%02d_%s_SGR%s_%s_%s%s%s' % (
        requisito.sequence, label, requisito.contratacion_id.id,
        requisito.categoria, requisito.codigo, suffix, extension,
    )


def _vista_previa_catalogo(catalogo):
    filas = []
    for numero, (_codigo, nombre, condicion) in enumerate(catalogo, 1):
        filas.append(
            '<tr class="o_data_row">'
            '<td data-name="sequence">%02d</td>'
            '<td data-name="name">%s</td>'
            '<td data-name="condicion">%s</td>'
            '<td class="o_list_button"><button type="button" disabled="disabled" '
            'class="sgr-document-upload">Suba su archivo</button></td>'
            '</tr>' % (numero, escape(nombre), escape(condicion or ''))
        )
    return (
        '<div class="sgr-document-list sgr-document-preview-list">'
        '<table class="o_list_table"><tbody>%s</tbody></table></div>'
    ) % ''.join(filas)


class ContratacionesSgrDocumento(models.Model):
    _name = 'contrataciones_sgr.documento'
    _description = 'Requisito documental de contratación SGR'
    _order = 'categoria, sequence, id'
    _sql_constraints = [
        ('contrato_categoria_codigo_unique', 'unique(contratacion_id, categoria, codigo)',
         'El requisito ya existe para este contrato.'),
    ]

    contratacion_id = fields.Many2one(
        'contrataciones_sgr', required=True, ondelete='cascade', index=True,
    )
    categoria = fields.Selection(
        [('pn', 'Persona natural / contrato laboral'), ('pj', 'Persona jurídica'),
         ('soporte', 'Soporte del contrato')],
        required=True, index=True,
    )
    codigo = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Documento', required=True)
    condicion = fields.Char(string='Condición')
    archivo = fields.Binary(string='Subir o reemplazar', attachment=False)
    archivo_nombre = fields.Char(string='Nombre del archivo')
    nombre_visible = fields.Char(string='Archivo', compute='_compute_nombre_visible')
    drive_id = fields.Char(string='ID en Drive', readonly=True, copy=False)
    drive_record_id = fields.Many2one(
        'drive', string='Documento en Drive', readonly=True, copy=False,
        ondelete='set null',
    )
    drive_url = fields.Char(string='Enlace en Drive', readonly=True, copy=False)
    fecha_carga = fields.Datetime(string='Última carga', readonly=True, copy=False)
    sgr_cargado_por_id = fields.Many2one(
        'res.users', string='Cargado por', readonly=True, copy=False,
    )
    sgr_log_pendiente = fields.Boolean(readonly=True, copy=False, default=False)
    estado = fields.Selection(
        [('pendiente', 'Pendiente'), ('cargado', 'En Drive')],
        compute='_compute_estado', string='Estado',
    )
    preview_html = fields.Html(
        string='Vista previa', compute='_compute_preview_html', sanitize=False,
    )

    @api.depends('drive_id')
    def _compute_estado(self):
        for record in self:
            record.estado = 'cargado' if record.drive_id else 'pendiente'

    @api.depends('archivo_nombre', 'drive_id')
    def _compute_nombre_visible(self):
        for record in self:
            record.nombre_visible = record.archivo_nombre if record.drive_id else False

    @api.depends('drive_id')
    def _compute_preview_html(self):
        for record in self:
            google_id = record.drive_id or ''
            if re.fullmatch(r'[A-Za-z0-9_-]+', google_id):
                record.preview_html = (
                    '<iframe src="https://drive.google.com/file/d/%s/preview" '
                    'width="100%%" height="560" loading="lazy" '
                    'frameborder="0" allowfullscreen></iframe>'
                ) % google_id
            else:
                record.preview_html = False

    @api.model_create_multi
    def create(self, vals_list):
        contratos = self.env['contrataciones_sgr'].browse([
            vals['contratacion_id'] for vals in vals_list if vals.get('contratacion_id')
        ])
        contratos._sgr_validar_edicion_contratado()
        pending = []
        clean_vals = []
        for vals in vals_list:
            vals = dict(vals)
            if _CAMPOS_DRIVE.intersection(vals):
                raise UserError('Los datos de Drive solo se asignan después de una carga confirmada.')
            catalogo = {
                'pn': DOCUMENTOS_NATURAL,
                'pj': DOCUMENTOS_JURIDICA,
                'soporte': DOCUMENTOS_SOPORTE,
            }.get(vals.get('categoria'), ())
            if vals.get('codigo') not in {item[0] for item in catalogo}:
                raise ValidationError('El tipo de requisito SGR no pertenece al catálogo.')
            pending.append(vals.pop('archivo', False))
            clean_vals.append(vals)
        records = super().create(clean_vals)
        for record, data in zip(records, pending):
            if data:
                record.with_context(sgr_agrupar_log_documentos=True)._subir_a_drive(
                    data, record.archivo_nombre,
                )
        for contrato in records.mapped('contratacion_id'):
            contrato._sgr_publicar_cargas_pendientes(
                records.filtered(lambda record: record.contratacion_id == contrato),
            )
        return records

    def write(self, vals):
        if vals:
            self.mapped('contratacion_id')._sgr_validar_edicion_contratado()
        vals = dict(vals)
        if _CAMPOS_DRIVE.intersection(vals):
            raise UserError('Los datos de Drive solo se asignan después de una carga confirmada.')
        if {'contratacion_id', 'categoria', 'codigo'}.intersection(vals):
            raise UserError('No se puede mover un requisito documental a otro contrato.')
        data = vals.pop('archivo', None)
        if data:
            if len(self) != 1:
                raise UserError('Suba el archivo de un requisito a la vez.')
            self._subir_a_drive(data, vals.get('archivo_nombre') or self.archivo_nombre)
        return super().write(vals)

    def unlink(self):
        self.mapped('contratacion_id')._sgr_validar_edicion_contratado()
        return super().unlink()

    def _subir_a_drive(self, data, original_name):
        self.ensure_one()
        self.contratacion_id._sgr_validar_edicion_contratado()
        if self.drive_id:
            raise UserError('Elimine el documento actual antes de cargar otro archivo.')
        folder_id = self.contratacion_id.drive_file_id.sudo().folder_id2
        if not folder_id:
            raise UserError('Este contrato SGR no tiene carpeta de Drive disponible.')
        if not original_name:
            raise ValidationError('Seleccione un archivo con nombre antes de subirlo.')

        extension = os.path.splitext(original_name)[1].lower() or '.bin'
        if not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
            raise ValidationError('La extensión del archivo no es válida.')
        title = _titulo_sgr(self, extension)
        temporary_path = None
        try:
            payload = base64.b64decode(data, validate=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temporary:
                temporary.write(payload)
                temporary_path = temporary.name

            drive = GoogleDriveAuth.get_instance().get_drive()
            if not drive:
                raise UserError('No se pudo conectar con Google Drive.')
            query = "title='%s' and '%s' in parents and trashed=false" % (title, folder_id)
            existing = drive.ListFile({'q': query}).GetList()
            file = existing[0] if existing else drive.CreateFile({
                'title': title, 'parents': [{'id': folder_id}],
            })
            file['title'] = title
            file.SetContentFile(temporary_path)
            file.Upload()
            google_id = file.get('id')
            if not google_id:
                raise UserError('Drive no confirmó el identificador del archivo.')
            permissions = file.GetPermissions()
            if not any(permission.get('type') == 'anyone' and
                       permission.get('role') in ('reader', 'writer', 'owner')
                       for permission in permissions):
                file.InsertPermission({
                    'type': 'anyone', 'value': 'anyone', 'role': 'reader',
                })
            super(ContratacionesSgrDocumento, self).write({
                'drive_id': google_id,
                'drive_url': file.get('alternateLink') or
                    'https://drive.google.com/file/d/%s/view' % google_id,
                'archivo_nombre': original_name,
                'fecha_carga': fields.Datetime.now(),
                'sgr_cargado_por_id': self.env.uid,
                'sgr_log_pendiente': True,
            })
        except (UserError, ValidationError):
            raise
        except Exception as error:
            _logger.exception('No se pudo subir el requisito SGR %s', self.id)
            raise UserError('No se pudo subir el documento a Drive. Revise la conexión y permisos de la carpeta.') from error
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    _logger.warning('No se pudo eliminar el archivo temporal %s', temporary_path)
        if not self.env.context.get('sgr_agrupar_log_documentos'):
            self.contratacion_id._sgr_publicar_cargas_pendientes(self)

    def action_subir_documento(self):
        self.ensure_one()
        self.contratacion_id._sgr_validar_edicion_contratado()
        if self.drive_id:
            raise UserError('Este documento ya está cargado. Elimínelo desde la vista previa para cambiarlo.')
        folder = self.contratacion_id.drive_file_id
        if not folder or not folder.folder_id2:
            raise UserError('Guarde la contratación y verifique su carpeta de Drive antes de subir archivos.')
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
                'default_proyecto_relacionado': self.contratacion_id.proyecto_relacionado.id,
                'tipo_documento_upload': 'sgr_documento',
                'sgr_documento_id': self.id,
            },
        }

    def action_ver_documento(self):
        self.ensure_one()
        if not self.drive_id:
            raise UserError('Todavía no se ha cargado este documento a Drive.')
        drive_record = self.drive_record_id.exists()
        if not drive_record:
            drive_record = self._crear_registro_drive_existente()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'drive',
            'res_id': drive_record.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _crear_registro_drive_existente(self):
        self.ensure_one()
        if not self.drive_id:
            raise UserError('Este requisito no tiene archivo en Drive.')
        drive_record = self.env['drive'].sudo().search([
            ('file_id', '=', self.drive_id),
            ('stage2_id', '=', self.contratacion_id.drive_file_id.id),
        ], limit=1)
        if not drive_record:
            drive_record = self.env['drive'].sudo().with_context(
                sgr_documento_id=False,
                tipo_documento_upload=False,
                skip_drive_duplicate_cleanup=True,
            ).create({
                'file_name': self.archivo_nombre or self.name,
                'file_id': self.drive_id,
                'folder_id': self.contratacion_id.drive_file_id.folder_id2,
                'stage2_id': self.contratacion_id.drive_file_id.id,
                'proyecto_relacionado': self.contratacion_id.proyecto_relacionado.id,
                'file_url': self.drive_url,
                'show_on_dashboard': False,
            })
        super(ContratacionesSgrDocumento, self).write({'drive_record_id': drive_record.id})
        return drive_record

    def _registrar_drive(self, drive_record):
        self.ensure_one()
        self.contratacion_id._sgr_validar_edicion_contratado()
        if self.drive_id:
            raise UserError('Este requisito ya tiene un archivo. Elimínelo antes de cargar otro.')
        if not drive_record.file_id or drive_record.stage2_id != self.contratacion_id.drive_file_id:
            raise UserError('El archivo no se subió a la carpeta de esta contratación SGR.')
        super(ContratacionesSgrDocumento, self).write({
            'drive_record_id': drive_record.id,
            'drive_id': drive_record.file_id,
            'drive_url': drive_record.file_url or
                'https://drive.google.com/file/d/%s/view' % drive_record.file_id,
            'archivo_nombre': drive_record.file_name,
            'fecha_carga': fields.Datetime.now(),
            'sgr_cargado_por_id': self.env.uid,
            'sgr_log_pendiente': True,
        })
        if not self.env.context.get('sgr_agrupar_log_documentos'):
            self.contratacion_id._sgr_publicar_cargas_pendientes(self)

    def _sgr_marcar_log_publicado(self):
        super(ContratacionesSgrDocumento, self).write({'sgr_log_pendiente': False})

    def _copiar_desde_proveedor(self, drive, source_id, source_name):
        self.ensure_one()
        self.contratacion_id._sgr_validar_edicion_contratado()
        if self.drive_id:
            return False
        folder = self.contratacion_id.drive_file_id
        extension = os.path.splitext(source_name or '')[1].lower() or '.pdf'
        if not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
            raise UserError('Extensión no válida en el documento del proveedor: %s' % self.name)
        title = _titulo_sgr(self, extension)
        try:
            # A previous database rollback can leave a completed Google copy.
            found = drive.ListFile({'q': "title='%s' and '%s' in parents and trashed=false" %
                                    (title, folder.folder_id2)}).GetList()
            if found:
                copy = found[0]
            else:
                copy = drive.CreateFile({'id': source_id}).Copy()
                copy['title'] = title
                copy['parents'] = [{'id': folder.folder_id2}]
                copy.Upload()
            if not copy.get('id'):
                raise UserError('Google Drive no confirmó la copia de %s.' % self.name)
            permissions = copy.GetPermissions()
            if not any(item.get('type') == 'anyone' and item.get('role') in
                       ('reader', 'writer', 'owner') for item in permissions):
                copy.InsertPermission({'type': 'anyone', 'value': 'anyone', 'role': 'reader'})

            record = self.env['drive'].sudo().search([
                ('file_id', '=', copy['id']), ('stage2_id', '=', folder.id),
            ], limit=1)
            if not record:
                record = self.env['drive'].sudo().with_context(
                    sgr_documento_id=False,
                    tipo_documento_upload=False,
                    skip_drive_duplicate_cleanup=True,
                ).create({
                    'file_name': title,
                    'file_id': copy['id'],
                    'original_file_id': source_id,
                    'folder_id': folder.folder_id2,
                    'stage2_id': folder.id,
                    'proyecto_relacionado': self.contratacion_id.proyecto_relacionado.id,
                    'file_url': copy.get('alternateLink') or
                        'https://drive.google.com/file/d/%s/view' % copy['id'],
                    'show_on_dashboard': False,
                })
            self._registrar_drive(record)
        except UserError:
            raise
        except Exception as error:
            _logger.exception('No se pudo copiar %s del proveedor a SGR %s', self.name,
                              self.contratacion_id.id)
            raise UserError('No se pudo copiar %s desde el proveedor a esta contratación.' %
                            self.name) from error
        return True

    def action_eliminar_documento(self):
        self.ensure_one()
        self.contratacion_id._sgr_validar_edicion_contratado()
        if not self.drive_id:
            raise UserError('Este requisito no tiene un documento cargado.')
        if self.drive_record_id:
            self.drive_record_id.unlink()
            return {'type': 'ir.actions.act_window_close'}
        try:
            drive = GoogleDriveAuth.get_instance().get_drive()
            if not drive:
                raise UserError('No se pudo conectar con Google Drive.')
            drive.CreateFile({'id': self.drive_id}).Trash()
        except UserError:
            raise
        except Exception as error:
            _logger.exception('No se pudo mover a papelera el requisito SGR %s', self.id)
            raise UserError('No se pudo eliminar el documento de Drive. Inténtelo de nuevo.') from error
        super(ContratacionesSgrDocumento, self).write({
            'drive_id': False,
            'drive_record_id': False,
            'drive_url': False,
            'archivo_nombre': False,
            'fecha_carga': False,
        })
        return {'type': 'ir.actions.act_window_close'}


class ContratacionesSgrDocumentos(models.Model):
    _inherit = 'contrataciones_sgr'

    def _sgr_publicar_cargas_pendientes(self, documentos=None):
        for contrato in self:
            documentos_contrato = documentos if documentos is not None else contrato.documento_ids
            cargados = documentos_contrato.filtered(
                lambda doc: doc.contratacion_id == contrato and doc.drive_id and doc.sgr_log_pendiente
            ).sorted(key=lambda doc: (doc.categoria, doc.sequence, doc.id))
            if not cargados:
                continue
            filas = Markup('').join(
                Markup('<li><b>%s</b> — %s. Cargado por: %s</li>') % (
                    markup_escape(doc.name),
                    markup_escape(doc.archivo_nombre or doc.name),
                    markup_escape(doc.sgr_cargado_por_id.name or 'Usuario no disponible'),
                )
                for doc in cargados
            )
            contrato.message_post(
                body=Markup('<b>Documentos cargados en Drive</b><ul>%s</ul>') % filas,
                subtype_xmlid='mail.mt_note',
            )
            cargados.sudo()._sgr_marcar_log_publicado()

    estado_sincronizacion_drive = fields.Selection([
        ('borrador', 'No sincronizado'),
        ('pendiente', 'Pendiente'),
        ('completado', 'Completado'),
    ], default='borrador', string='Sincronización documental')
    documento_ids = fields.One2many(
        'contrataciones_sgr.documento', 'contratacion_id', string='Documentos SGR',
    )
    documentos_natural_ids = fields.One2many(
        'contrataciones_sgr.documento', 'contratacion_id',
        string='Documentos persona natural y contrato laboral',
        domain=[('categoria', '=', 'pn')],
    )
    documentos_juridica_ids = fields.One2many(
        'contrataciones_sgr.documento', 'contratacion_id',
        string='Documentos persona jurídica',
        domain=[('categoria', '=', 'pj')],
    )
    documentos_soporte_ids = fields.One2many(
        'contrataciones_sgr.documento', 'contratacion_id',
        string='Soportes del contrato', domain=[('categoria', '=', 'soporte')],
    )
    soportes_anteriores_pendientes = fields.Boolean(
        compute='_compute_soportes_anteriores_pendientes',
    )
    documentos_preparados = fields.Boolean(compute='_compute_documentos_preparados')
    documentos_natural_preview = fields.Html(
        compute='_compute_documentos_preview', sanitize=False,
    )
    documentos_juridica_preview = fields.Html(
        compute='_compute_documentos_preview', sanitize=False,
    )
    documentos_soporte_preview = fields.Html(
        compute='_compute_documentos_preview', sanitize=False,
    )

    @api.depends('select_persona')
    def _compute_documentos_preview(self):
        for record in self:
            record.documentos_natural_preview = _vista_previa_catalogo(DOCUMENTOS_NATURAL)
            record.documentos_juridica_preview = _vista_previa_catalogo(DOCUMENTOS_JURIDICA)
            record.documentos_soporte_preview = _vista_previa_catalogo(DOCUMENTOS_SOPORTE)

    @api.depends('documento_ids')
    def _compute_documentos_preparados(self):
        for record in self:
            record.documentos_preparados = len(record.documento_ids) >= (
                len(DOCUMENTOS_NATURAL) + len(DOCUMENTOS_JURIDICA) +
                len(DOCUMENTOS_SOPORTE)
            )

    @api.depends(*[field for field, _name in SOPORTES_ANTERIORES.values()],
                 'documento_ids.drive_id')
    def _compute_soportes_anteriores_pendientes(self):
        for contrato in self:
            cargados = {doc.codigo for doc in contrato.documentos_soporte_ids if doc.drive_id}
            contrato.soportes_anteriores_pendientes = any(
                codigo not in cargados and bool(getattr(contrato, field))
                for codigo, (field, _filename) in SOPORTES_ANTERIORES.items()
            )

    def action_preparar_documentos(self):
        self._sgr_validar_edicion_contratado()
        Documento = self.env['contrataciones_sgr.documento']
        for contrato in self:
            existentes = {(linea.categoria, linea.codigo) for linea in contrato.documento_ids}
            nuevos = []
            for categoria, catalogo in (
                ('pn', DOCUMENTOS_NATURAL), ('pj', DOCUMENTOS_JURIDICA),
                ('soporte', DOCUMENTOS_SOPORTE),
            ):
                for sequence, (codigo, name, condicion) in enumerate(catalogo, 1):
                    if (categoria, codigo) not in existentes:
                        nuevos.append({
                            'contratacion_id': contrato.id,
                            'categoria': categoria,
                            'codigo': codigo,
                            'sequence': sequence,
                            'name': name,
                            'condicion': condicion,
                        })
            if nuevos:
                Documento.create(nuevos)
        return True

    def action_ver_relaciones_documentales(self):
        self.ensure_one()
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError('Solo Soporte puede consultar las relaciones documentales internas.')
        tree_view = self.env.ref('Matriz.view_contrataciones_sgr_documento_soporte_tree')
        form_view = self.env.ref('Matriz.view_contrataciones_sgr_documento_soporte_form')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Relaciones documentales SGR',
            'res_model': 'contrataciones_sgr.documento',
            'view_mode': 'tree,form',
            'views': [(tree_view.id, 'tree'), (form_view.id, 'form')],
            'domain': [('contratacion_id', '=', self.id)],
            'context': {'create': False, 'edit': False, 'delete': False},
            'target': 'current',
        }

    def action_migrar_soportes_anteriores(self):
        for contrato in self:
            contrato.action_preparar_documentos()
            requisitos = {doc.codigo: doc for doc in contrato.documentos_soporte_ids}
            cargados = self.env['contrataciones_sgr.documento']
            for codigo, (campo, campo_nombre) in SOPORTES_ANTERIORES.items():
                requisito = requisitos[codigo]
                data = getattr(contrato, campo)
                if not data or requisito.drive_id:
                    continue
                payload = base64.b64decode(data)
                extension = (os.path.splitext(getattr(contrato, campo_nombre) or '')[1]
                             if campo_nombre else '')
                if not extension:
                    extension = ('.pdf' if payload.startswith(b'%PDF') else
                                 '.png' if payload.startswith(b'\x89PNG') else
                                 '.jpg' if payload.startswith(b'\xff\xd8') else
                                 '.docx' if payload.startswith(b'PK') else '.bin')
                requisito.with_context(sgr_agrupar_log_documentos=True)._subir_a_drive(
                    data, '%s%s' % (requisito.name, extension),
                )
                cargados |= requisito
            contrato._sgr_publicar_cargas_pendientes(cargados)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_actualizar_documentos_proveedor(self):
        self.ensure_one()
        self._sgr_validar_edicion_contratado()
        if not self.contratista_proveedor_relacionado:
            raise UserError('Seleccione primero un contratista o proveedor.')
        self.write({'estado_sincronizacion_drive': 'pendiente'})
        return {
            'type': 'ir.actions.client',
            'tag': 'matriz.sgr_sync_documents',
            'params': {'record_id': self.id},
        }

    @api.model
    def js_sgr_get_pending_files(self, record_id):
        contrato = self.browse(record_id).exists()
        if not contrato:
            raise UserError('La contratación SGR ya no existe.')
        contrato.check_access_rights('read')
        contrato.check_access_rule('read')
        return contrato._listar_documentos_proveedor()

    def _listar_documentos_proveedor(self):
        self.ensure_one()
        proveedor = self.contratista_proveedor_relacionado
        if not proveedor:
            return []
        carpeta_origen = proveedor.drive_stage_provedor_id
        carpeta_destino = self.drive_file_id
        if not carpeta_origen or not carpeta_origen.folder_id:
            raise UserError('El proveedor no tiene una carpeta de documentos en Drive.')
        if not carpeta_destino or not carpeta_destino.folder_id2:
            raise UserError('La contratación SGR aún no tiene carpeta de destino en Drive.')

        categoria = 'pj' if self.select_persona == 'persona_juridica' else 'pn'
        mapa = (DOCUMENTOS_PROVEEDOR_JURIDICA if categoria == 'pj'
                else DOCUMENTOS_PROVEEDOR_NATURAL)
        pendientes = {
            doc.codigo: doc for doc in self.documento_ids
            if doc.categoria == categoria and not doc.drive_id and doc.codigo in mapa
        }
        if not pendientes:
            return []

        try:
            drive = GoogleDriveAuth.get_instance().get_drive()
            if not drive:
                raise UserError('No se pudo conectar con Google Drive.')
            # FUV also checks the provider folder: a file may exist there even
            # if its Many2one on the provider was never assigned.
            disponibles = drive.ListFile({'q': "'%s' in parents and trashed=false" %
                                          carpeta_origen.folder_id}).GetList()
            disponibles.sort(key=lambda item: item.get('modifiedDate', ''), reverse=True)
            disponibles_por_id = {item['id']: item for item in disponibles if item.get('id')}
        except UserError:
            raise
        except Exception as error:
            _logger.exception('No se pudieron consultar documentos del proveedor %s', proveedor.id)
            raise UserError('No se pudieron consultar los documentos del proveedor en Drive.') from error

        jobs = []
        for codigo, requisito in pendientes.items():
            campo, prefijo = mapa[codigo]
            origen = getattr(proveedor, campo, False)
            encontrado = next((file for file in disponibles if
                file.get('mimeType') != 'application/vnd.google-apps.folder' and
                _nombre_normalizado(file.get('title', '')).startswith(
                    _nombre_normalizado(prefijo))), None)
            google_id = encontrado['id'] if encontrado else False
            nombre = encontrado['title'] if encontrado else False
            if (not google_id and origen and origen.stage6_id == carpeta_origen and
                    origen.file_id in disponibles_por_id):
                google_id = origen.file_id
                nombre = origen.file_name
            if not google_id:
                continue
            jobs.append({'documento_id': requisito.id, 'source_id': google_id,
                         'title': nombre})
        return jobs

    @api.model
    def js_sgr_copy_single_file(self, record_id, documento_id, source_id):
        contrato = self.browse(record_id).exists()
        requisito = self.env['contrataciones_sgr.documento'].browse(documento_id).exists()
        if not contrato or not requisito or requisito.contratacion_id != contrato:
            raise UserError('El requisito no pertenece a esta contratación SGR.')
        contrato._sgr_validar_edicion_contratado()
        requisito.check_access_rights('write')
        requisito.check_access_rule('write')
        if requisito.drive_id:
            return {'status': 'success', 'file': requisito.name}

        proveedor = contrato.contratista_proveedor_relacionado
        carpeta = proveedor.drive_stage_provedor_id
        mapa = (DOCUMENTOS_PROVEEDOR_JURIDICA if requisito.categoria == 'pj'
                else DOCUMENTOS_PROVEEDOR_NATURAL if requisito.categoria == 'pn' else {})
        if not carpeta or requisito.codigo not in mapa:
            raise UserError('No hay un documento de proveedor equivalente para este requisito.')
        try:
            drive = GoogleDriveAuth.get_instance().get_drive()
            source = drive.CreateFile({'id': source_id})
            source.FetchMetadata(fields='id,title,parents,labels,mimeType')
            parents = {parent.get('id') for parent in source.get('parents', [])}
            campo, prefijo = mapa[requisito.codigo]
            vinculado = getattr(proveedor, campo, False)
            nombre_valido = _nombre_normalizado(source.get('title', '')).startswith(
                _nombre_normalizado(prefijo))
            if (carpeta.folder_id not in parents or (source.get('labels') or {}).get('trashed') or
                    source.get('mimeType') == 'application/vnd.google-apps.folder' or
                    not (nombre_valido or (vinculado and vinculado.file_id == source_id))):
                raise UserError('El archivo ya no corresponde al proveedor seleccionado.')
            with self.env.cr.savepoint():
                requisito.with_context(sgr_agrupar_log_documentos=True)._copiar_desde_proveedor(
                    drive, source_id, source['title'],
                )
            return {'status': 'success', 'file': requisito.name}
        except UserError:
            raise
        except Exception as error:
            _logger.exception('Falló la copia del requisito SGR %s', requisito.id)
            raise UserError('Falló la copia de %s. Puede reintentarlo.' % requisito.name) from error

    @api.model
    def js_sgr_finish_sync(self, record_id):
        contrato = self.browse(record_id).exists()
        if not contrato:
            raise UserError('La contratación SGR ya no existe.')
        contrato._sgr_validar_edicion_contratado()
        contrato.check_access_rights('write')
        contrato.check_access_rule('write')
        contrato._sgr_publicar_cargas_pendientes()
        contrato.write({'estado_sincronizacion_drive': 'completado'})
        return True


class DriveDocumentoSgr(models.Model):
    _inherit = 'drive'

    @api.model
    def create(self, vals):
        requisito_id = self.env.context.get('sgr_documento_id')
        if requisito_id:
            requisito = self.env['contrataciones_sgr.documento'].browse(requisito_id).exists()
            if not requisito or requisito.drive_id:
                raise UserError('El requisito SGR ya no está disponible para recibir el archivo.')
            requisito.contratacion_id._sgr_validar_edicion_contratado()
            requisito.check_access_rights('write')
            requisito.check_access_rule('write')
            if not vals.get('file_data'):
                raise UserError('Seleccione un documento antes de guardar.')
            extension = os.path.splitext(vals.get('file_name') or '')[1].lower()
            if not extension or not re.fullmatch(r'\.[a-z0-9]{1,10}', extension):
                raise UserError('El archivo debe tener una extensión válida.')
            folder = requisito.contratacion_id.drive_file_id
            if not folder or not folder.folder_id2:
                raise UserError('Esta contratación SGR no tiene carpeta en Drive.')
            vals = dict(vals,
                file_name=_titulo_sgr(requisito, extension, unique=True),
                folder_id=folder.folder_id2,
                stage2_id=folder.id,
                proyecto_relacionado=requisito.contratacion_id.proyecto_relacionado.id,
            )
        record = super().create(vals)
        if requisito_id and record.file_id:
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
                _logger.exception('No se pudo confirmar permiso público del documento SGR %s',
                                  requisito_id)
                raise UserError('El archivo se subió, pero no se pudo confirmar el permiso de lectura en Drive.') from error
        return record

    def action_save_and_close(self):
        requisito_id = self.env.context.get('sgr_documento_id')
        if not requisito_id:
            return super().action_save_and_close()
        self.ensure_one()
        requisito = self.env['contrataciones_sgr.documento'].browse(requisito_id).exists()
        if not requisito or requisito.drive_id:
            raise UserError('El requisito SGR ya no está disponible para recibir el archivo.')
        requisito.contratacion_id._sgr_validar_edicion_contratado()
        result = super().action_save_and_close()
        requisito._registrar_drive(self)
        return result

    def unlink(self):
        requisitos = self.env['contrataciones_sgr.documento'].search([
            ('drive_record_id', 'in', self.ids),
        ])
        requisitos.mapped('contratacion_id')._sgr_validar_edicion_contratado()
        result = super().unlink()
        for requisito in requisitos.exists():
            super(ContratacionesSgrDocumento, requisito).write({
                'drive_record_id': False,
                'drive_id': False,
                'drive_url': False,
                'archivo_nombre': False,
                'fecha_carga': False,
            })
        return result

import json
import uuid
import re
from odoo import http
from odoo.http import request
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from datetime import date, timedelta
import mimetypes
import base64
import logging
from html import escape as html_escape

from werkzeug.utils import redirect

from .main import verify_recaptcha
from ..models.otro_si_aprobaciones import EstadoOtroSi

_logger = logging.getLogger(__name__)

_ESTADOS_OTROSI_DISPONIBLES_PARA_CUENTA = (
    EstadoOtroSi.CONTRATADO.value,
    EstadoOtroSi.ADICION_FORMALIZADA.value,
    EstadoOtroSi.ADICION_PRORROGA_FORMALIZADA.value,
    EstadoOtroSi.MODIFICATORIO_FORMALIZADO.value,
)


def _portal_error_fecha_actividad(post):
    """Devuelve el campo de fecha inválido antes de que Odoo genere un error 500."""
    fechas = {}
    for field_name in ('inicio_actividad', 'finalizacion_actividad'):
        raw_value = (post.get(field_name) or '').strip()
        if not raw_value:
            continue
        match = re.fullmatch(r'(\d{4,})-(\d{2})-(\d{2})', raw_value)
        if not match or int(match.group(1)) < 1000 or int(match.group(1)) > 9999:
            return field_name
        try:
            fechas[field_name] = fields.Date.to_date(raw_value)
        except (TypeError, ValueError):
            return field_name
    if (
        fechas.get('inicio_actividad')
        and fechas.get('finalizacion_actividad')
        and fechas['finalizacion_actividad'] < fechas['inicio_actividad']
    ):
        return 'finalizacion_actividad'
    return False

def _safe_public_return_url(return_url):
    if not return_url:
        return False
    return_url = str(return_url).strip()
    if return_url.startswith('/public/upload_avatar'):
        return False
    if return_url.startswith('/public/') and not return_url.startswith('//'):
        return return_url
    return False


def _portal_bad_request(qcontext=None):
    """Renderiza una validación pública sin depender de website.400."""
    qcontext = qcontext or {}
    return request.render(
        'Matriz.error_page',
        {
            'error_message': (
                qcontext.get('message')
                or qcontext.get('error_message')
                or 'No fue posible procesar la solicitud. Revise los datos ingresados.'
            ),
        },
        status=400,
    )


def _portal_uploaded_files(files, *field_names):
    uploaded_files = []
    for field_name in field_names:
        uploaded_files.extend(
            file
            for file in files.getlist(field_name)
            if file and getattr(file, 'filename', None)
        )
    return uploaded_files


def _upload_portal_acta_documents(acta, document_kind, uploaded_files, login=None):
    if acta and uploaded_files:
        _logger.info(
            "Portal: recibidos %s archivo(s) de tipo %s para el acta %s: %s",
            len(uploaded_files),
            document_kind,
            acta.id,
            ", ".join(uploaded_file.filename for uploaded_file in uploaded_files),
        )
        since = fields.Datetime.now()
        ctx = {}
        if login:
            ctx['portal_chatter_login_id'] = login.id
        if document_kind == 'cesion_derechos':
            ctx['allow_cesion_derechos_portal'] = True
        uploaded_count = acta.sudo().with_context(**ctx).upload_portal_multiple_documents(
            document_kind, uploaded_files
        )
        _logger.info(
            "Portal: %s archivo(s) de tipo %s subido(s) para el acta %s.",
            uploaded_count,
            document_kind,
            acta.id,
        )
        if login:
            partner = login.sudo()._get_portal_chatter_partner()
            domain = [
                ('model', '=', acta._name),
                ('res_id', '=', acta.id),
                ('date', '>=', since - timedelta(seconds=3)),
                ('author_id', '!=', partner.id),
            ]
            messages = request.env['mail.message'].sudo().search(domain)
            if messages:
                messages.write({'author_id': partner.id})


def _portal_acta_multi_document_values(record):
    acta = record.contratista_id
    if not acta:
        return {
            'informes_contratista_docs': request.env['drive'].sudo(),
            'otro_documentos_de_inf_docs': request.env['drive'].sudo(),
            'cesion_derechos_docs': request.env['drive'].sudo(),
        }
    return {
        'informes_contratista_docs': acta.sudo()._get_portal_acta_document_records(
            'informes_contratista',
            'informes_contratista_id',
        ),
        'otro_documentos_de_inf_docs': acta.sudo()._get_portal_acta_document_records(
            'otro_documentos_de_informacion',
            'otro_documentos_de_inf_id',
        ),
        'cesion_derechos_docs': acta.sudo()._get_portal_acta_document_records(
            'cesion_derechos',
            'cesion_derechos_id',
        ),
    }


class ContratistaProveedorController(http.Controller):
    @staticmethod
    def _parse_portal_number(value):
        if value in (None, False):
            return 0.0
        text = str(value).strip().replace(' ', '')
        if not text:
            return 0.0
        if ',' in text and '.' in text:
            if text.rfind(',') > text.rfind('.'):
                text = text.replace('.', '').replace(',', '.')
            else:
                text = text.replace(',', '')
        elif ',' in text:
            text = text.replace(',', '.')
        try:
            return float(text)
        except (TypeError, ValueError):
            return 0.0

    def _current_portal_login(self):
        user_id = request.httprequest.cookies.get('user_id')
        token = request.httprequest.cookies.get('user_token')
        if user_id and str(user_id).isdigit() and token:
            login = request.env['login'].sudo().search([
                ('id', '=', int(user_id)),
                ('token', '=', token),
            ], limit=1)
            if login:
                return login
        return request.env['login'].sudo()

    def _portal_login_for_chatter(self, record):
        record = record.sudo()
        if record._name == 'login':
            return record
        if 'login_id' in record._fields and record.login_id:
            return record.login_id
        if record._name == 'cuentas_de_cobros':
            domain = []
            if record.documento_de_identificacion:
                domain.append(('documento_de_identidad', '=', record.documento_de_identificacion))
            if record.correo_facturacion:
                domain.append(('coreo', '=', record.correo_facturacion))
            if domain:
                return request.env['login'].sudo().search(['|'] + domain[:2] if len(domain) > 1 else domain, limit=1)
        if record._name == 'acta_entrega_documentos' and record.cuenta_id:
            return self._portal_login_for_chatter(record.cuenta_id)
        if record._name == 'declaracion_juramentada' and record.relacion:
            return self._portal_login_for_chatter(record.relacion)
        if record._name == 'informe_actividades' and record.cuenta_cobro_id:
            return self._portal_login_for_chatter(record.cuenta_cobro_id)
        if record._name == 'drive' and 'contratistas_provedores' in record._fields and record.contratistas_provedores:
            return self._portal_login_for_chatter(record.contratistas_provedores)
        if record._name == 'contratista_proveedor':
            login = request.env['login'].sudo().search([('contratistas', '=', record.id)], limit=1)
            if login:
                return login
            email = getattr(record, 'correo_facturacion', False) or getattr(record, 'email', False)
            if email:
                return request.env['login'].sudo().search([('coreo', '=', email)], limit=1)
        return request.env['login'].sudo()

    def _reassign_portal_chatter_author(self, record, login, since):
        if not record or not login:
            return
        try:
            partner = login.sudo()._get_portal_chatter_partner()
            domain = [
                ('model', '=', record._name),
                ('res_id', '=', record.id),
                ('date', '>=', since - timedelta(seconds=3)),
                ('author_id', '!=', partner.id),
            ]
            messages = request.env['mail.message'].sudo().search(domain)
            if messages:
                messages.write({'author_id': partner.id})
        except Exception:
            _logger.warning(
                "No se pudo reasignar autor de trazabilidad portal para %s(%s).",
                getattr(record, '_name', 'registro'),
                getattr(record, 'id', False),
                exc_info=True,
            )

    def _portal_write(self, record, vals, login=None, extra_context=None):
        login = login or self._portal_login_for_chatter(record)
        since = fields.Datetime.now()
        ctx = {'portal_chatter_login_id': login.id} if login else {}
        if extra_context:
            ctx.update(extra_context)
        result = record.sudo().with_context(**ctx).write(vals)
        self._reassign_portal_chatter_author(record, login, since)
        return result

    def _portal_contratista_for_login(self, login):
        if not login:
            return request.env['contratista_proveedor'].sudo()

        Contratista = request.env['contratista_proveedor'].sudo()
        if login.contratistas:
            contratista = login.contratistas.sudo()
        else:
            domain = []
            if login.coreo:
                domain.append(('correo_facturacion', '=ilike', login.coreo.strip()))
            if login.documento_de_identidad:
                documento = str(login.documento_de_identidad).strip()
                domain.extend([
                    ('nit', '=', documento),
                    ('cedula', '=', documento),
                ])
            if not domain and login.name:
                domain.append(('name', '=ilike', login.name.strip()))

            if len(domain) > 1:
                search_domain = ['|'] * (len(domain) - 1) + domain
            else:
                search_domain = domain
            contratista = Contratista.search(search_domain, limit=1) if search_domain else Contratista

        if contratista and not contratista.access_token:
            contratista.write({'access_token': str(uuid.uuid4())})
        return contratista

    @http.route('/document/update/<int:doc_id>/<string:token>', type='http', auth='public', website=True, csrf=False)
    def document_update_form(self, doc_id, token, **kwargs):
        drive_record = request.env['drive'].sudo().browse(doc_id)
        if not drive_record.exists() or not drive_record.token or drive_record.token != token:
            return request.render('Matriz.error_template')

        return request.render('Matriz.contratista_publico_form', {
            'drive_record': drive_record,
        })

    @http.route('/document/update/submit', type='http', auth='public', website=True, methods=['POST'], csrf=False)
    def document_update_submit(self, **post):
        doc_id = post.get('doc_id')
        token = post.get('token')

        if not doc_id or not str(doc_id).isdigit() or not token:
            return request.render('Matriz.error_template')

        drive_record = request.env['drive'].sudo().browse(int(doc_id))
        if not drive_record.exists() or not drive_record.token or drive_record.token != token:
            return request.render('Matriz.error_template')

        uploaded_file = request.httprequest.files.get('file_data')
        if not uploaded_file:
            return request.render('Matriz.error_page', {
                'error_message': 'Debes seleccionar un archivo para actualizar el documento.',
            })

        file_content = uploaded_file.read()
        if not file_content:
            return request.render('Matriz.error_page', {
                'error_message': 'El archivo seleccionado está vacío.',
            })

        file_name = uploaded_file.filename or drive_record.file_name or drive_record.name or 'documento_actualizado'
        self._portal_write(drive_record, {
            'file_data': base64.b64encode(file_content),
            'file_name': file_name,
            'observacion': post.get('observacion') or False,
        })
        drive_record.upload_to_drive()
        self._portal_write(drive_record, {'token': False})
        drive_record.send_chat_notification('actualizado')

        return request.render('Matriz.update_success')

    @http.route(['/contratista/view_update/<int:record_id>'], type='http', auth='public', website=True, csrf=False)
    def view_contratista_update(self, record_id, access_token=None, **kwargs):
        # Buscar el contratista
        contratista = request.env['contratista_proveedor'].sudo().browse(record_id)

        # Verificar si el contratista existe y si el token proporcionado coincide
        if not contratista.exists() or not access_token or not contratista.access_token or contratista.access_token != access_token:
            return request.not_found()

        # --- NUEVO: Buscamos todos los países ordenados por nombre ---
        countries = request.env['res.country'].sudo().search([], order="name asc")
        countries2 = request.env['res.country'].sudo().search([], order="name asc")

        # Enviar los datos al portal
        return request.render('Matriz.contratista_public_form', {
            'contratista': contratista,
            'countries': countries,  # <--- AQUÍ AGREGAMOS LA LISTA A LA VISTA
            'countries2': countries2,
            'modo_update': True  # Indica que estamos en la vista de actualización
        })

    @http.route(['/contratista/view/<int:record_id>'], type='http', auth='public', website=True, csrf=False)
    def view_contratista(self, record_id, access_token=None, **kwargs):
        # Buscar el contratista
        contratista = request.env['contratista_proveedor'].sudo().browse(record_id)

        # Verificar si el contratista existe y si el token proporcionado coincide
        if not contratista.exists() or not access_token or not contratista.access_token or contratista.access_token != access_token:
            return request.not_found()
        
        # --- NUEVO: Buscamos los países ---
        countries = request.env['res.country'].sudo().search([], order="name asc")
        countries2 = request.env['res.country'].sudo().search([], order="name asc")

        # Verificación de múltiples documentos
        documentos_subidos_pn = {
            'documento_identidad': bool(contratista.documento_identidad),
            'hoja_vida': bool(contratista.hoja_vida),
            'cert_arl': bool(contratista.cert_arl),
            'cert_afiliacion_pension': bool(contratista.cert_afiliacion_pension),
            'certificado_laborales': bool(contratista.certificado_laborales),
            'cert_contraloria': bool(contratista.cert_contraloria),
            'cert_policia_judicial': bool(contratista.cert_policia_judicial),
            'cert_inhabilidades': bool(contratista.cert_inhabilidades),
            'resolucion_pensionado_vigente': bool(contratista.resolucion_pensionado_vigente),
            'declaracion_juramentada': bool(contratista.declaracion_juramentada),
            'rut_actualizado': bool(contratista.rut_actualizado),
            'certificado_estudio': bool(contratista.certificado_estudio),
            'cert_afiliacion_salud': bool(contratista.cert_afiliacion_salud),
            'cert_examen_preocupacional': bool(contratista.cert_examen_preocupacional),
            'resolucion_facturacion_electronica': bool(contratista.resolucion_facturacion_electronica),
            'cert_procuraduria': bool(contratista.cert_procuraduria),
            'cert_medidas_cautelares': bool(contratista.cert_medidas_cautelares),
            'cert_libreta_militar': bool(contratista.cert_libreta_militar),
            'certificado_bancario_vigente': bool(contratista.certificado_bancario_vigente),
            'tarjeta_profesional': bool(contratista.tarjeta_profesional),
        }

        documentos_subidos_pj = {
            'propuesta_j': bool(contratista.propuesta_j),
            'camara_c_actualizada_j': bool(contratista.camara_c_actualizada_j),
            'rut_actualizado_j': bool(contratista.rut_actualizado_j),
            'rup_j': bool(contratista.rup_j),
            'hoja_vida_j': bool(contratista.hoja_vida_j),
            'cert_contraloria_j': bool(contratista.cert_contraloria_j),
            'cert_procuraduria_j': bool(contratista.cert_procuraduria_j),
            'cedula_representante_legal': bool(contratista.cedula_representante_legal),
            'hoja_v_representante_legal': bool(contratista.hoja_v_representante_legal),
            'cert_contraloria_repre_legal': bool(contratista.cert_contraloria_repre_legal),
            'cert_procuraduria_repre_legal': bool(contratista.cert_procuraduria_repre_legal),
            'cert_medidas_cautelares_j': bool(contratista.cert_medidas_cautelares_j),
            'cert_inhabilidades_j': bool(contratista.cert_inhabilidades_j),
            'experiencia_laboral_j': bool(contratista.experiencia_laboral_j),
            'cert_pago_paraf_j': bool(contratista.cert_pago_paraf_j),
            'planilla_seguridad_social_j': bool(contratista.planilla_seguridad_social_j),
            'cert_junta_contadores_j': bool(contratista.cert_junta_contadores_j),
            'cedula_revisor_fiscal_contador_j': bool(contratista.cedula_revisor_fiscal_contador_j),
            'documento_de_identidad_contador_j': bool(contratista.documento_de_identidad_contador_j),
            'Tarjeta_profesional_revisor_fiscal_j': bool(contratista.Tarjeta_profesional_revisor_fiscal_j),
            'Tarjeta_profesional_contador_j': bool(contratista.Tarjeta_profesional_contador_j),
            'certificado_bancario_vigente_j': bool(contratista.certificado_bancario_vigente_j),
            'estados_financieros2': bool(contratista.estados_financieros2),
            'resolucion_fact_elect_j': bool(contratista.resolucion_fact_elect_j),
        }

        # Enviar los datos al portal
        return request.render('Matriz.contratista_public_form', {
            'contratista': contratista,
            'countries': countries,
            'countries2': countries2,
            'documentos_subidos_pn': documentos_subidos_pn, # Pasamos el diccionario a la vista
            'documentos_subidos_pj': documentos_subidos_pj,  # Pasamos el diccionario a la vista
            'modo_update': False  # Indica que es solo visualización
        })
    
    # ---------------------------------------------------------
    # RUTA CORREGIDA: AUTO-SAVE (Con soporte para country_id)
    # ---------------------------------------------------------
    @http.route('/contratista/update_field_ajax', type='json', auth='public', methods=['POST'], csrf=False)
    def update_field_ajax(self, record_id, access_token, field_name, value):
        """
        Esta función recibe actualizaciones campo por campo desde JavaScript
        cada vez que el usuario escribe o cambia un valor.
        """
        try:
            # 1. Buscar registro y validar seguridad
            contratista = request.env['contratista_proveedor'].sudo().browse(int(record_id))
            
            if not contratista.exists() or not access_token or not contratista.access_token or contratista.access_token != access_token:
                return {'status': 'error', 'message': 'Acceso denegado o token inválido'}

            # 2. Validar que el campo exista en el modelo
            if field_name not in contratista._fields:
                 return {'status': 'error', 'message': f'El campo {field_name} no existe'}

            # 3. Lógica de conversión de valores
            val_to_write = value
            
            # A. CASO ESPECIAL: Country_id (Many2one)
            # El HTML envía "45" (string), pero Odoo necesita 45 (int). 
            # Si viene vacío "", enviamos False.
            if field_name == 'country_id':
                if value and str(value).isdigit():
                    val_to_write = int(value)
                else:
                    val_to_write = False
            
            if field_name == 'country2_id':
                if value and str(value).isdigit():
                    val_to_write = int(value)
                else:
                    val_to_write = False

            # B. Lógica para Departamentos (Búsqueda por nombre)
            elif field_name in ['departamentos2'] and isinstance(value, str):
                dep = request.env['departamento'].sudo().search([('name', '=', value)], limit=1)
                val_to_write = dep.id if dep else False
            
            # C. Lógica para Municipios (Búsqueda por nombre)
            elif field_name.startswith('nombre_municipio') or field_name == 'municipios2':
                if isinstance(value, str):
                    mun = request.env['municipio'].sudo().search([('name', '=', value)], limit=1)
                    val_to_write = mun.id if mun else False

            # 4. Escribir el valor
            self._portal_write(contratista, {field_name: val_to_write}, login=self._portal_login_for_chatter(contratista))
            
            return {'status': 'success', 'message': 'Guardado'}

        except Exception as e:
            _logger.error(f"Error en auto-save AJAX: {str(e)}")
            return {'status': 'error', 'message': str(e)}

    @http.route(['/contratista/submit'], type='http', auth='public', website=True, methods=['POST'], csrf=False)
    def submit_contratista(self, **post):
        record_id = post.get('record_id')
        contratista = request.env['contratista_proveedor'].sudo().browse(int(record_id))

        if not contratista.exists():
            return request.not_found()

        # Verificar si el token es válido
        access_token = post.get('access_token')
        if not access_token or not contratista.access_token or contratista.access_token != access_token:
            return request.not_found()

        # Lista de campos de municipios
        municipio_fields = [
            'nombre_municipio',
            'nombre_municipio2',
            'nombre_municipio3',
            'nombre_municipio4',
            'nombre_municipio5',
            'nombre_municipio6',
            'nombre_municipio7',
            'nombre_municipio8',
            'nombre_municipio9',
            'nombre_municipio10',
            'nombre_municipio11',
            'nombre_municipio12',
            'nombre_municipio13',
            'nombre_municipio14',
            'nombre_municipio15',
            'ciudad_oficina_principal',
            'ciudad_exp_representante',
            'ciudad_exp_natural',
            'departamentos2',
            'municipios2'
        ]

        # Procesar valores de municipios y departamentos por nombre
        values_to_write = {}
        for field in municipio_fields:
            value_name = post.get(field)
            if value_name:
                if field == 'departamentos2':  # Procesar departamentos
                    departamento = request.env['departamento'].sudo().search([('name', '=', value_name)], limit=1)
                    if departamento:
                        values_to_write[field] = departamento.id
                else:  # Procesar municipios
                    municipio = request.env['municipio'].sudo().search([('name', '=', value_name)], limit=1)
                    if municipio:
                        values_to_write[field] = municipio.id

        # ==============================================================================
        # INICIO DEL BLOQUE CORREGIDO: PROCESAMIENTO DE TABLA DINÁMICA
        # ==============================================================================
        
        # 1. Identificar todos los índices únicos que vienen en el formulario
        #    Esto permite que funcione aunque borres filas intermedias o uses IDs aleatorios.
        indices_encontrados = set()
        for key in post.keys():
            if 'info_ids[' in key:
                # Extraemos lo que hay entre corchetes: info_ids[123][campo] -> '123'
                try:
                    start = key.find('[') + 1
                    end = key.find(']', start)
                    if start > 0 and end > start:
                        indices_encontrados.add(key[start:end])
                except:
                    continue

        info_ids_data = []
        
        # 2. Comando (5, 0, 0): BORRA todo lo anterior antes de guardar lo nuevo.
        #    Esto es vital para evitar que se dupliquen las líneas cada vez que guardas.
        info_ids_data.append((5, 0, 0))

        # 3. Recorremos los índices encontrados y armamos los datos
        for index in indices_encontrados:
            tipo_estudio = post.get(f'info_ids[{index}][tipo_estudio]') or 'pregrado'
            if tipo_estudio not in ('pregrado', 'postgrado', 'otro'):
                tipo_estudio = 'pregrado'
            profesion = post.get(f'info_ids[{index}][profesion]')
            anio_profesion = post.get(f'info_ids[{index}][anio_profesion]')
            postgrado = post.get(f'info_ids[{index}][postgrado]')
            anio_postgrado = post.get(f'info_ids[{index}][anio_postgrado]')
            otro_estudio = post.get(f'info_ids[{index}][otro_estudio]')
            anio_otro_estudio = post.get(f'info_ids[{index}][anio_otro_estudio]')
            tarjeta_profesional = post.get(f'info_ids[{index}][tarjeta_profesional]')
            anio_tarjeta = post.get(f'info_ids[{index}][anio_tarjeta]')
            cedula = post.get(f'info_ids[{index}][cedula]')

            # Validamos que la fila no esté completamente vacía antes de guardarla
            if profesion or postgrado or otro_estudio or tarjeta_profesional:
                info_ids_data.append((0, 0, {
                    'tipo_estudio': tipo_estudio,
                    'profesion': profesion or '',
                    'anio_profesion': anio_profesion or False,
                    'postgrado': postgrado or '',
                    'anio_postgrado': anio_postgrado or False,
                    'otro_estudio': otro_estudio or '',
                    'anio_otro_estudio': anio_otro_estudio or False,
                    'tarjeta_profesional': tarjeta_profesional or '',
                    'anio_tarjeta': anio_tarjeta or False,
                    'cedula': cedula or '',
                }))

        # 4. Inyectamos la lista en los valores a escribir
        #    Verificamos len > 1 porque el primer elemento siempre es el (5,0,0)
        if len(info_ids_data) > 1:
            values_to_write['info_ids'] = info_ids_data
        else:
            # Si el usuario borró todas las filas visualmente, mandamos solo el comando de borrar
            values_to_write['info_ids'] = [(5, 0, 0)]

        # ==============================================================================
        # FIN DEL BLOQUE CORREGIDO
        # ==============================================================================

        # Agregar el resto de los campos al diccionario values_to_write
        base_fields = {
            'description': post.get('description'),
            'firma_proveedor_name': post.get('firma_proveedor_name'),
            'cedula_representante': post.get('cedula_representante', ''),

            'expedicion_contra_j': post.get('expedicion_contra_j', ''),
            'expedicion_contra_N': post.get('expedicion_contra_N', ''),
            'cedula': post.get('cedula', ''),

            'name': post.get('name', ''),
            'user_id': post.get('user_id', ''),
            'nit': post.get('nit', ''),
            'razon_social': post.get('razon_social', ''),
            'clase_persona': post.get('clase_persona', ''),
            'nombre_r_legal': post.get('nombre_r_legal', ''),


            'proyecto_relacionado': post.get('proyecto_relacionado', ''),
            'contratista1': post.get('contratista1', ''),
            'tipo': post.get('tipo', ''),
            'identificacion': post.get('identificacion', ''),
            'estado': post.get('estado', ''),
            'pagos_realizados': float(post.get('pagos_realizados', 0.0)),
            'deuda_total': float(post.get('deuda_total', 0.0)),
            'notas': post.get('notas', ''),
            'nit_rut': post.get('nit_rut', ''),
            'pais': post.get('pais', ''),
            'departamento': post.get('departamento', ''),
            'ciudad': post.get('ciudad', ''),
            'direccion_fisica': post.get('direccion_fisica', ''),
            'correo_facturacion': post.get('correo_facturacion', ''),
            
            'email_contratante': post.get('email_contratante', ''), # CORREO CONTRATANTE (COORDINADOR)
            'correos_asistentes': post.get('correos_asistentes', ''), # CORREO ASISTENTES (COORDINADOR)                                                                                                      
            'codigo_postal': post.get('codigo_postal', ''),
            'numero_telefono': post.get('numero_telefono', ''),
            'fax': post.get('fax', ''),
            'pagina_web': post.get('pagina_web', ''),
            'nombre_contacto_comercial': post.get('nombre_contacto_comercial', ''),
            'telefono_contacto_comercial': post.get('telefono_contacto_comercial', ''),
            'correo_contacto_comercial': post.get('correo_contacto_comercial', ''),
            'correo_info_contacto_comercial': post.get('correo_info_contacto_comercial', ''),
            'tiempo_pago_dias': post.get('tiempo_pago_dias', ''),
            'forma_pago': post.get('forma_pago', ''),
            'moneda_venta': post.get('moneda_venta', 'COP'),
            'tiempo_entrega_dias': post.get('tiempo_entrega_dias', 'N/A'),
            'acepta_devoluciones': post.get('acepta_devoluciones', 'selecione'),
            'nombre_banco': post.get('nombre_banco', ''),
            'sucursal_ciudad': post.get('sucursal_ciudad', ''),
            'tipo_cuenta2': post.get('tipo_cuenta2', ''),
            'numero_cuenta': post.get('numero_cuenta', ''),
            'titular_cuenta': post.get('titular_cuenta', ''),
            'autorizacion_pago': post.get('autorizacion_pago',
            "Autorizamos a FUNDACIÓN UNIVERSIDAD DEL VALLE para que consigne en nuestra cuenta mencionada los valores correspondientes al pago de las facturas, a nuestro nombre."),
            'actividad_realizar': post.get('actividad_realizar', 'selecione'),
            'descripcion_actividad': post.get('descripcion_actividad', ''),
            'cdg_actividad_economica': post.get('cdg_actividad_economica', ''),
            'sujeto_retencion': post.get('sujeto_retencion', 'seleccione'),
            'otro_sujeto_retencion': post.get('otro_sujeto_retencion', ''),
            'otra_resolucion': post.get('otra_resolucion', ''),
            'sujeto_retencion_iva': post.get('sujeto_retencion_iva', 'seleccione'),
            'gran_contribuyente': post.get('gran_contribuyente', 'seleccione'),
            'numero_resolucion': post.get('numero_resolucion', ''),
            'fecha': post.get('fecha', ''),
            'fecha_impresion_rut': post.get('fecha_impresion_rut', ''),
            'fecha_impresion_ccm': post.get('fecha_impresion_ccm', ''),
            'regimen_iva': post.get('regimen_iva', 'seleccione'),
            'act_economica_main': post.get('act_economica_main', 'seleccione'),
            'sjt_retencion_ica': post.get('sjt_retencion_ica', 'NO'),
            'firma_proveedor': post.get('firma_proveedor', ''),
            'nombre_proveedor': post.get('nombre_proveedor', ''),
            'cargo_proveedor': post.get('cargo_proveedor', ''),
            'tipo_proveedor': post.get('tipo_proveedor', 'seleccione'),
            'tipo_cliente': post.get('tipo_cliente', 'seleccione'),
            'sociedad': post.get('sociedad', 'seleccione'),
            'unidad_compras': post.get('unidad_compras', 'seleccione'),
            'unidad_ventas': post.get('unidad_ventas', 'seleccione'),
            'grupuo_tesoreria': post.get('grupuo_tesoreria', 'seleccione'),
            'firma_ventanilla_unica': post.get('firma_ventanilla_unica', ''),
            'nombre_ventanilla_unica': post.get('nombre_ventanilla_unica', ''),
            'fecha_ventanilla_unica': post.get('fecha_ventanilla_unica', ''),
            'firma_asistente_contable': post.get('firma_asistente_contable', ''),
            'nombre_asistente_contable': post.get('nombre_asistente_contable', ''),
            'fecha_asistente_contable': post.get('fecha_asistente_contable', ''),
            'firma_direccion_financiera': post.get('firma_direccion_financiera', ''),
            'nombre_direccion_financiera': post.get('nombre_direccion_financiera', ''),
            'fecha_direccion_financiera': post.get('fecha_direccion_financiera', ''),
            'country_id': int(post.get('country_id')) if post.get('country_id') else False,
            'indicativo': post.get('indicativo', ''),
            'bandera_html': post.get('bandera_html', ''),
            'extension': post.get('extension', ''),

            'country2_id': int(post.get('country2_id')) if post.get('country2_id') else False,
            'indicativo2': post.get('indicativo2', ''),
            'bandera2_html': post.get('bandera2_html', ''),



            #Campos de la vista tree
            'cdg_activida_ciiu': post.get('cdg_activida_ciiu', ''),
            'tarifa': post.get('tarifa', ''),

            'cdg_activida_ciiu2': post.get('cdg_activida_ciiu2', ''),
            'tarifa2': post.get('tarifa2', ''),

            'cdg_activida_ciiu3': post.get('cdg_activida_ciiu3', ''),
            'tarifa3': post.get('tarifa3', ''),

            'cdg_activida_ciiu4': post.get('cdg_activida_ciiu4', ''),
            'tarifa4': post.get('tarifa4', ''),

            'cdg_activida_ciiu5': post.get('cdg_activida_ciiu5', ''),
            'tarifa5': post.get('tarifa5', ''),

            'cdg_activida_ciiu6': post.get('cdg_activida_ciiu6', ''),
            'tarifa6': post.get('tarifa6', ''),

            'cdg_activida_ciiu7': post.get('cdg_activida_ciiu7', ''),
            'tarifa7': post.get('tarifa7', ''),

            'cdg_activida_ciiu8': post.get('cdg_activida_ciiu8', ''),
            'tarifa8': post.get('tarifa8', ''),

            'cdg_activida_ciiu9': post.get('cdg_activida_ciiu9', ''),
            'tarifa9': post.get('tarifa9', ''),

            'cdg_activida_ciiu10': post.get('cdg_activida_ciiu10', ''),
            'tarifa10': post.get('tarifa10', ''),

            'cdg_activida_ciiu11': post.get('cdg_activida_ciiu11', ''),
            'tarifa11': post.get('tarifa11', ''),

            'cdg_activida_ciiu12': post.get('cdg_activida_ciiu12', ''),
            'tarifa12': post.get('tarifa12', ''),

            'cdg_activida_ciiu13': post.get('cdg_activida_ciiu13', ''),
            'tarifa13': post.get('tarifa13', ''),

            'cdg_activida_ciiu14': post.get('cdg_activida_ciiu14', ''),
            'tarifa14': post.get('tarifa14', ''),

            'cdg_activida_ciiu15': post.get('cdg_activida_ciiu15', ''),
            'tarifa15': post.get('tarifa15', ''),

            'informacion_tributaria_ids': post.get('informacion_tributaria_ids', [])
        }
        values_to_write.update(base_fields)

        # Procesar campos de archivo
        file_fields = [
            'firma_proveedor',
            'nombre_municipio',
            'documento_identidad',
            'rut_actualizado',
            'hoja_vida',
            'certificado_estudio',
            'certificado_laborales',
            'cert_arl',
            'cert_afiliacion_salud',
            'cert_afiliacion_pension',
            'cert_examen_preocupacional',
            'resolucion_facturacion_electronica',
            'cert_contraloria',
            'cert_procuraduria',
            'cert_policia_judicial',
            'cert_medidas_cautelares',
            'cert_inhabilidades',
            'cert_libreta_militar',
            'resolucion_pensionado_vigente',
            'certificado_bancario_vigente',
            'tarjeta_profesional',
            'declaracion_juramentada',
            'propuesta_j',
            'camara_c_actualizada_j',
            'rut_actualizado_j',
            'rup_j',
            'hoja_vida_j',
            'cert_contraloria_j',
            'cert_procuraduria_j',
            'cedula_representante_legal',
            'hoja_v_representante_legal',
            'cert_contraloria_repre_legal',
            'cert_procuraduria_repre_legal',
            'estados_financieros2',
            'cert_medidas_cautelares_j',
            'cert_inhabilidades_j',
            'resolucion_fact_elect_j',
            'experiencia_laboral_j',
            'cert_pago_paraf_j',
            'planilla_seguridad_social_j',
            'cert_junta_contadores_j',
            'cedula_revisor_fiscal_contador_j',
            'documento_de_identidad_contador_j',
            'Tarjeta_profesional_revisor_fiscal_j',
            'Tarjeta_profesional_contador_j',
            'certificado_bancario_vigente_j'
        ]

        # Procesar solo archivos que se subieron en este request
        nuevos_documentos = {}
        for field in file_fields:
            file = request.httprequest.files.get(field)
            if file:
                file_content = file.read()
                if file_content:
                    encoded_file = base64.b64encode(file_content)
                    values_to_write[field] = encoded_file
                    values_to_write[f'{field}_name'] = file.filename
                    nuevos_documentos[field] = encoded_file  # Agregar al diccionario de nuevos documentos
        
        self._portal_write(contratista, values_to_write)

        # ==============================================================================
        # 5. ENVÍO DE CORREOS AUTOMÁTICOS AL COORDINADOR Y ASISTENTES
        # ==============================================================================
        # Verificamos si hay algún correo configurado para enviar
        if contratista.email_contratante or post.get('correos_asistentes'):
            base_url = request.env["ir.config_parameter"].sudo().get_param("web.base.url")
            
            # Tomamos el proyecto directamente de la BD (Protegido por el paso 3)
            proyecto_obj = contratista.proyecto_relacionado
            
            if not proyecto_obj:
                _logger.warning(">>> NO SE ENVIÓ CORREO: El contratista %s no tiene un 'proyecto_relacionado' guardado.", contratista.name)
            else:
                proyecto_id = proyecto_obj.id
                nombre_proyecto = proyecto_obj.display_name
                
                base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')

                # 1. Definimos los parámetros directos al registro
                params = {
                    'id': proyecto_id,             # El ID exacto del registro
                    'model': 'contratacion',       # El modelo que me mostraste en tu imagen
                    'view_type': 'form'            # Queremos que se abra en modo formulario
                }

                # 2. urlencode transforma el diccionario en: id=XX&model=contratacion&view_type=form
                query_string = urllib.parse.urlencode(params)

                # 3. Armamos la URL final
                formulario_url = f"{base_url}/web#{query_string}"
                                
                COLOR_PRINCIPAL = "#043484"
                contratista_nombre = contratista.name

                body_html = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #444; background-color: #f8f8f8; padding: 20px; border-radius: 5px;">
                        <div style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 15px; border-radius: 4px 4px 0 0; text-align: center;">
                            <h3 style="margin: 0; font-size: 18px;">📄 Actualización de documentos</h3>
                        </div>
                        <div style="background-color: white; padding: 20px 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px;">
                            <p>Cordial saludo,</p>
                            <p>El contratista <b>{contratista_nombre}</b> ha actualizado su información.</p>
                            
                            <div style="background-color: #f1f5fa; padding: 15px; border-left: 4px solid {COLOR_PRINCIPAL}; margin: 20px 0;">
                                <p style="margin: 0;"><b>Proyecto Relacionado:</b> {nombre_proyecto}</p>
                            </div>

                            <p>Por favor ingrese al sistema para revisar las actualizaciones:</p>
                            <div style="text-align: center; margin-top: 20px;">
                                <a href="{formulario_url}" style="background-color: {COLOR_PRINCIPAL}; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">
                                    🔗 Ir al proyecto
                                </a>
                            </div>
                            <p style="margin-top: 20px; font-size: 12px; color: #999;">Este es un mensaje automático de SGO.</p>
                        </div>
                    </div>"""

                # --- Envío al contratante ---
                if contratista.email_contratante:
                    mail_values = {
                        'subject': f"Actualización de documentos - {contratista_nombre}",
                        'email_from': 'go@fundacionunivalle.org',
                        'email_to': contratista.email_contratante,
                        'body_html': body_html,
                        'auto_delete': True,
                    }
                    mail = request.env['mail.mail'].sudo().create(mail_values)
                    mail.sudo().send()
                    _logger.info(">>> MAIL ENVIADO AL CONTRATANTE: %s", contratista.email_contratante)

                # --- Envío a los asistentes ---
                correos_asistentes_raw = post.get('correos_asistentes', '')
                if isinstance(correos_asistentes_raw, list):
                    correos_asistentes = correos_asistentes_raw
                elif isinstance(correos_asistentes_raw, str) and correos_asistentes_raw.startswith('['):
                    try:
                        correos_asistentes = json.loads(correos_asistentes_raw)
                    except:
                        correos_asistentes = []
                elif isinstance(correos_asistentes_raw, str):
                    correos_asistentes = [c.strip() for c in correos_asistentes_raw.split(',') if c.strip()]
                else:
                    correos_asistentes = []

                if correos_asistentes:
                    for correo in correos_asistentes:
                        mail_values_asistente = {
                            'subject': f"Actualización de documentos - {contratista_nombre}",
                            'email_from': 'go@fundacionunivalle.org',
                            'email_to': correo,
                            'body_html': body_html,
                            'auto_delete': True,
                        }
                        mail_asistente = request.env['mail.mail'].sudo().create(mail_values_asistente)
                        mail_asistente.sudo().send()
                        _logger.info(">>> MAIL ENVIADO A ASISTENTE: %s", correo)

        # ==============================================================================
        # 6. LIMPIEZA DE DATOS TEMPORALES
        # ==============================================================================
        # Vaciamos los datos para que el contratista quede limpio para una futura actualización espontánea
        portal_login = self._portal_login_for_chatter(contratista)
        self._portal_write(contratista, {
            'proyecto_relacionado': False,
            'contratista1': False,
            'email_contratante': False,
            'correos_asistentes': False,
        }, login=portal_login)
        _logger.info(">>> LIMPIEZA: Se borraron los datos temporales de notificación del contratista %s.", contratista.name)

        return request.redirect('/thank-you')
    
    # ---------------------------------------------------------
    # 1. RUTA PARA SUBIR EL ARCHIVO (AJAX) - CORREGIDA
    # ---------------------------------------------------------
    @http.route('/contratista/upload_document_ajax', type='http', auth='public', methods=['POST'], csrf=False)
    def upload_document_ajax(self, **post):
        # Usamos cursor independiente para evitar el error "current transaction is aborted"
        new_cr = request.env.registry.cursor()
        try:
            env = request.env(cr=new_cr)
            record_id = post.get('record_id')
            access_token = post.get('access_token')
            file = request.httprequest.files.get('file')
            field_name = post.get('field_name')

            if not record_id or not file or not field_name:
                return json.dumps({'status': 'error', 'message': 'Datos incompletos'})

            record = env['contratista_proveedor'].sudo().browse(int(record_id))
            if not record.exists() or not access_token or not record.access_token or record.access_token != access_token:
                return json.dumps({'status': 'error', 'message': 'Acceso denegado'})

            # 1. Guardar binario en Odoo y liberar la tabla
            since = fields.Datetime.now()
            portal_record = request.env['contratista_proveedor'].sudo().browse(int(record_id))
            portal_login = self._portal_login_for_chatter(portal_record)
            ctx = {'portal_chatter_login_id': portal_login.id} if portal_login else {}
            record.with_context(**ctx).write({
                field_name: base64.b64encode(file.read()),
                f'{field_name}_name': file.filename
            })
            env.cr.commit() 
            self._reassign_portal_chatter_author(
                portal_record,
                portal_login,
                since,
            )

            # 2. Mapeo de funciones de Drive
            method_map = {
                # Persona Natural
                "documento_identidad": "_upload_document_to_drive",
                "rut_actualizado": "_upload_document_rut_to_drive",
                "hoja_vida": "_upload_document_hoja_vida_to_drive",
                "cert_arl": "_upload_document_cert_arl_to_drive",
                "cert_afiliacion_pension": "_upload_document_cert_afiliacion_pension_to_drive",
                "certificado_laborales": "_upload_document_certificado_laborales_to_drive",
                "cert_contraloria": "_upload_document_cert_contraloria_to_drive",
                "cert_policia_judicial": "_upload_document_cert_policia_judicial_to_drive",
                "cert_inhabilidades": "_upload_document_cert_inhabilidades_to_drive",
                "resolucion_pensionado_vigente": "_upload_document_resolucion_pensionado_vigente_to_drive",
                "declaracion_juramentada": "_upload_document_declaracion_juramentada_to_drive",
                "certificado_estudio": "_upload_document_certificado_estudio_to_drive",
                "cert_afiliacion_salud": "_upload_document_cert_afiliacion_salud_to_drive",
                "cert_examen_preocupacional": "_upload_document_cert_examen_preocupacional_to_drive",
                "resolucion_facturacion_electronica": "_upload_document_resolucion_facturacion_electronica_to_drive",
                "cert_procuraduria": "_upload_document_cert_procuraduria_to_drive",
                "cert_medidas_cautelares": "_upload_document_cert_medidas_cautelares_to_drive",
                "cert_libreta_militar": "_upload_document_cert_libreta_militar_to_drive",
                "certificado_bancario_vigente": "_upload_document_certificado_bancario_vigente_to_drive",
                "tarjeta_profesional": "_upload_document_tarjeta_profesional_to_drive",
                
                # Persona Jurídica
                "propuesta_j": "_upload_document_propuesta_j_to_drive",
                "camara_c_actualizada_j": "_upload_document_camara_c_actualizada_j_to_drive",
                "rut_actualizado_j": "_upload_document_rut_actualizado_j_to_drive",
                "rup_j": "_upload_document_rup_j_to_drive",
                "hoja_vida_j": "_upload_document_hoja_vida_j_to_drive",
                "cert_contraloria_j": "_upload_document_cert_contraloria_j_to_drive",
                "cert_procuraduria_j": "_upload_document_cert_procuraduria_j_to_drive",
                "cedula_representante_legal": "_upload_document_cedula_representante_legal_to_drive",
                "hoja_v_representante_legal": "_upload_document_hoja_v_representante_legal_to_drive",
                "cert_contraloria_repre_legal": "_upload_document_cert_contraloria_repre_legal_to_drive",
                "cert_procuraduria_repre_legal": "_upload_document_cert_procuraduria_repre_legal_to_drive",
                "cert_medidas_cautelares_j": "_upload_document_cert_medidas_cautelares_j_to_drive",
                "cert_inhabilidades_j": "_upload_document_cert_inhabilidades_j_to_drive",
                "experiencia_laboral_j": "_upload_document_experiencia_laboral_j_to_drive",
                "cert_pago_paraf_j": "_upload_document_cert_pago_paraf_j_to_drive",
                "planilla_seguridad_social_j": "_upload_document_planilla_seguridad_social_j_to_drive",
                "cert_junta_contadores_j": "_upload_document_cert_junta_contadores_j_to_drive",
                "cedula_revisor_fiscal_contador_j": "_upload_document_cedula_revisor_fiscal_contador_j_to_drive",
                "documento_de_identidad_contador_j": "_upload_document_documento_de_identidad_contador_j_to_drive",
                "Tarjeta_profesional_revisor_fiscal_j": "_upload_document_Tarjeta_profesional_revisor_fiscal_j_to_drive",
                "Tarjeta_profesional_contador_j": "_upload_document_Tarjeta_profesional_contador_j_to_drive",
                "certificado_bancario_vigente_j": "_upload_document_certificado_bancario_vigente_j_to_drive",
                "estados_financieros2": "_upload_document_estados_financieros2_to_drive",
                "resolucion_fact_elect_j": "_upload_document_resolucion_fact_elect_j_to_drive",
            }

            success = False
            if field_name in method_map and hasattr(record, method_map[field_name]):
                success = getattr(record.with_context(**ctx), method_map[field_name])()
                env.cr.commit()
                self._reassign_portal_chatter_author(portal_record, portal_login, since)

            return json.dumps({'status': 'success' if success else 'error'})

        except Exception as e:
            new_cr.rollback()
            return json.dumps({'status': 'error', 'message': str(e)})
        finally:
            new_cr.close()

    # ---------------------------------------------------------
    # 2. RUTA PARA BORRAR EL ARCHIVO (AJAX) - CORREGIDA (Con 'Visible' = True)
    # ---------------------------------------------------------
    @http.route('/contratista/delete_document_ajax', type='http', auth='public', methods=['POST'], csrf=False)
    def delete_document_ajax(self, **post):
        # 1. Cursor independiente para evitar bloqueos
        new_cr = request.env.registry.cursor()
        try:
            env = request.env(cr=new_cr)
            
            record_id = post.get('record_id')
            access_token = post.get('access_token')
            field_name = post.get('field_name')

            if not record_id or not field_name:
                return json.dumps({'status': 'error', 'message': 'Datos incompletos'})

            record = env['contratista_proveedor'].sudo().browse(int(record_id))
            if not record.exists() or not access_token or not record.access_token or record.access_token != access_token:
                return json.dumps({'status': 'error', 'message': 'Acceso denegado'})

            # ------------------------------------------------------------------
            # 2. LISTA DE CONFIGURACIÓN COMPLETA
            # ------------------------------------------------------------------
            # Estructura: (campo_drive_id, campo_binario, campo_visible, campo_nombre)
            field_config = [
                ("documento_identidad_c_id", "documento_identidad", "documento_identidad_visible", "documento_identidad_name"),
                ("rut_actualizado_id", "rut_actualizado", "rut_actualizado_visible", "rut_actualizado_name"),
                ("hoja_vida_id", "hoja_vida", "hoja_vida_visible", "hoja_vida_name"),
                ("certificado_estudio_id", "certificado_estudio", "certificado_estudio_visible", "certificado_estudio_name"),
                ("certificado_laborales_id", "certificado_laborales", "certificado_laborales_visible", "certificado_laborales_name"),
                ("cert_arl_id", "cert_arl", "cert_arl_visible", "cert_arl_name"),
                ("cert_afiliacion_salud_id", "cert_afiliacion_salud", "cert_afiliacion_salud_visible", "cert_afiliacion_salud_name"),
                ("cert_afiliacion_pension_id", "cert_afiliacion_pension", "cert_afiliacion_pension_visible", "cert_afiliacion_pension_name"),
                ("cert_examen_preocupacional_id", "cert_examen_preocupacional", "cert_examen_preocupacional_visible", "cert_examen_preocupacional_name"),
                ("resolucion_facturacion_electronica_id", "resolucion_facturacion_electronica", "resolucion_facturacion_electronica_visible", "resolucion_facturacion_electronica_name"),
                ("cert_contraloria_id", "cert_contraloria", "cert_contraloria_visible", "cert_contraloria_name"),
                ("cert_procuraduria_id", "cert_procuraduria", "cert_procuraduria_visible", "cert_procuraduria_name"),
                ("cert_policia_judicial_id", "cert_policia_judicial", "cert_policia_judicial_visible", "cert_policia_judicial_name"),
                ("cert_medidas_cautelares_id", "cert_medidas_cautelares", "cert_medidas_cautelares_visible", "cert_medidas_cautelares_name"),
                ("cert_inhabilidades_id", "cert_inhabilidades", "cert_inhabilidades_visible", "cert_inhabilidades_name"),
                ("cert_libreta_militar_id", "cert_libreta_militar", "cert_libreta_militar_visible", "cert_libreta_militar_name"),
                ("resolucion_pensionado_vigente_id", "resolucion_pensionado_vigente", "resolucion_pensionado_vigente_visible", "resolucion_pensionado_vigente_name"),
                ("certificado_bancario_vigente_id", "certificado_bancario_vigente", "certificado_bancario_vigente_visible", "certificado_bancario_vigente_name"),
                ("declaracion_juramentada_id", "declaracion_juramentada", "declaracion_juramentada_visible", "declaracion_juramentada_name"),
                
                # Persona Jurídica
                ("propuesta_j_id", "propuesta_j", "propuesta_j_visible", "propuesta_j_name"),
                ("camara_c_actualizada_j_id", "camara_c_actualizada_j", "camara_c_actualizada_j_visible", "camara_c_actualizada_j_name"),
                ("rut_actualizado_j_id", "rut_actualizado_j", "rut_actualizado_j_visible", "rut_actualizado_j_name"),
                ("rup_j_id", "rup_j", "rup_j_visible", "rup_j_name"),
                ("hoja_vida_j_id", "hoja_vida_j", "hoja_vida_j_visible", "hoja_vida_j_name"),
                ("cert_contraloria_j_id", "cert_contraloria_j", "cert_contraloria_j_visible", "cert_contraloria_j_name"),
                ("cert_procuraduria_j_id", "cert_procuraduria_j", "cert_procuraduria_j_visible", "cert_procuraduria_j_name"),
                ("cert_medidas_cautelares_j_id", "cert_medidas_cautelares_j", "cert_medidas_cautelares_j_visible", "cert_medidas_cautelares_j_name"),
                ("cert_inhabilidades_j_id", "cert_inhabilidades_j", "cert_inhabilidades_j_visible", "cert_inhabilidades_j_name"),
                ("resolucion_fact_elect_j_id", "resolucion_fact_elect_j", "resolucion_fact_elect_j_visible", "resolucion_fact_elect_j_name"),
                ("experiencia_laboral_j_id", "experiencia_laboral_j", "experiencia_laboral_j_visible", "experiencia_laboral_j_name"),
                ("cert_pago_paraf_j_id", "cert_pago_paraf_j", "cert_pago_paraf_j_visible", "cert_pago_paraf_j_name"),
                ("planilla_seguridad_social_j_id", "planilla_seguridad_social_j", "planilla_seguridad_social_j_visible", "planilla_seguridad_social_j_name"),
                ("cert_junta_contadores_j_id", "cert_junta_contadores_j", "cert_junta_contadores_j_visible", "cert_junta_contadores_j_name"),
                ("cedula_revisor_fiscal_contador_j_id", "cedula_revisor_fiscal_contador_j", "cedula_revisor_fiscal_contador_j_visible", "cedula_revisor_fiscal_contador_j_name"),
                ("documento_de_identidad_contador_j_id", "documento_de_identidad_contador_j", "documento_de_identidad_contador_j_visible", "documento_de_identidad_contador_j_name"),
                ("Tarjeta_profesional_revisor_fiscal_j_id", "Tarjeta_profesional_revisor_fiscal_j", "Tarjeta_profesional_revisor_fiscal_j_visible", "Tarjeta_profesional_revisor_fiscal_j_name"),
                ("Tarjeta_profesional_contador_j_id", "Tarjeta_profesional_contador_j", "Tarjeta_profesional_contador_j_visible", "Tarjeta_profesional_contador_j_name"),
                ("certificado_bancario_vigente_j_id", "certificado_bancario_vigente_j", "certificado_bancario_vigente_j_visible", "certificado_bancario_vigente_j_name"),
                ("cedula_representante_legal_id", "cedula_representante_legal", "cedula_representante_legal_visible", "cedula_representante_legal_name"),
                ("hoja_v_representante_legal_id", "hoja_v_representante_legal", "hoja_v_representante_legal_visible", "hoja_v_representante_legal_name"),
                ("cert_contraloria_repre_legal_id", "cert_contraloria_repre_legal", "cert_contraloria_repre_legal_visible", "cert_contraloria_repre_legal_name"),
                ("cert_procuraduria_repre_legal_id", "cert_procuraduria_repre_legal", "cert_procuraduria_repre_legal_visible", "cert_procuraduria_repre_legal_name"),
                ("estados_financieros2_id", "estados_financieros2", "estados_financieros2_visible", "estados_financieros2_name"),
            ]

            # ------------------------------------------------------------------
            # 3. PROCESO DE LIMPIEZA
            # ------------------------------------------------------------------
            vals = {
                field_name: False,          # Borrar binario
                f'{field_name}_name': False # Borrar nombre archivo
            }

            # Buscamos la configuración correspondiente al campo
            target_config = next((item for item in field_config if item[1] == field_name), None)

            if target_config:
                drive_field_id = target_config[0] # Ej: documento_identidad_c_id
                visible_field = target_config[2]  # Ej: documento_identidad_visible
                
                # 1. Obtenemos el ID de Drive actual (antes de borrarlo)
                google_file_id = getattr(record, drive_field_id, False)

                # 2. Actualizamos el diccionario 'vals':
                vals[drive_field_id] = False # Borrar ID de Drive en Odoo
                vals[visible_field] = True   # <--- AQUI ESTA EL CAMBIO: Pasamos el visible a TRUE

                # 3. Intentamos borrar físicamente de Drive (Si existe la función en el modelo)
                if google_file_id:
                    try:
                        if hasattr(record, 'delete_file_from_drive'):
                             record.delete_file_from_drive(google_file_id)
                        elif hasattr(record, '_delete_google_drive_file'):
                             record._delete_google_drive_file(google_file_id)
                    except Exception as e:
                        _logger.warning(f"No se pudo borrar de Drive cloud: {e}")

            # ------------------------------------------------------------------
            # 4. GUARDADO Y COMMIT
            # ------------------------------------------------------------------
            portal_record = request.env['contratista_proveedor'].sudo().browse(int(record_id))
            self._portal_write(record, vals, login=self._portal_login_for_chatter(portal_record))
            env.cr.commit() # Liberamos BD
            
            return json.dumps({'status': 'success', 'message': 'Archivo eliminado y campos restablecidos.'})

        except Exception as e:
            new_cr.rollback()
            _logger.error(f"Error borrando archivo AJAX: {e}")
            return json.dumps({'status': 'error', 'message': str(e)})
        finally:
            new_cr.close()

class ThankYouController(http.Controller):
    @http.route(['/thank-you'], type='http', auth='public', website=True)
    def thank_you(self, **kwargs):
        return request.render('Matriz.thank_you_page')



class LoginPublicController(ContratistaProveedorController):
    PORTAL_REPLACEMENT_FIELDS = {
        'rut_actualizado_id': 'RUT actualizado',
        'planilla_de_aportes_a_seguridad_social_id': 'Planilla de aportes a seguridad social',
        'factura_id': 'Factura',
        'certificado_de_expensa_id': 'Certificado de expensas',
        'informes_contratista_id': 'Informes',
        'copia_cedula_id': 'Copia cédula',
        'regalias_copia_cedula_id': 'Cédula Regalías',
        'regalias_copia_contrato_id': 'Contrato Regalías',
        'certificacion_bancaria_id': 'Certificación bancaria',
        'resolucion_facturacion_electronica_id': 'Resolución facturación electrónica',
        'anexo_tributario_id': 'Anexo tributario',
        'copia_contrato_id': 'Copia del contrato',
        'registro_de_proveedores_id': 'RPC / Registro de proveedores',
        'otro_documentos_de_inf_id': 'Documento adicional',
        'cesion_derechos_id': 'Cesión de derechos',
        'factura_electronica_juridica_id': 'Factura electrónica',
        'rut_actualizado_juridica_id': 'RUT actualizado',
        'resolucion_facturacion_electronica_juridica_id': 'Resolución facturación electrónica',
        'camara_comercio_juridica_id': 'Cámara de comercio',
        'certificado_pago_parafiscales_juridica_id': 'Certificado pago parafiscales',
        'tarjeta_profesional_Contador_juridica_id': 'Tarjeta profesional contador',
        'tarjeta_profesional_revisor_fiscal_juridica_id': 'Tarjeta profesional revisor fiscal',
        'cedula_de_contador_juridica_id': 'Documento identidad contador',
        'cedula_representante_legal_juridica_id': 'Documento representante legal',
        'certificado_junta_contadores_juridica_id': 'Certificado junta contadores',
        'planilla_de_seguridad_social_ju_id': 'Planilla seguridad social',
        'cedula_revisor_fiscal_juridica_id': 'Documento revisor fiscal',
        'certificacion_bancaria_juridica_id': 'Certificación bancaria',
        'rut_representante_legal_juridica_id': 'RUT representante legal',
        'registro_proveedores_juridica_id': 'RPC / Registro de proveedores',
        'anexo_tributario_juridica_id': 'Anexo tributario',
    }

    PORTAL_ACTA_REPLACEMENT_FIELDS = {
        'certificacion_bancaria_id': 'certificacion_bancaria_2_id',
        'certificacion_bancaria_juridica_id': 'certificacion_bancaria_juridica_2_id',
        'resolucion_facturacion_electronica_id': 'resolucion_facturacion_electronica_2_id',
        'resolucion_facturacion_electronica_juridica_id': 'resolucion_facturacion_electronica_juridica_2_id',
        'anexo_tributario_id': 'anexo_tributario_2_id',
        'copia_contrato_id': 'copia_contrato_2_id',
        'copia_cedula_id': 'copia_cedula_2_id',
        'registro_de_proveedores_id': 'registro_de_proveedores_2_id',
        'registro_proveedores_juridica_id': 'registro_proveedores_juridica_2_id',
        'anexo_tributario_juridica_id': 'anexo_tributario_juridica_2_id',
        'planilla_de_seguridad_social_ju_id': 'planilla_de_aportes_a_seguridad_social_ju_id',
        'tarjeta_profesional_Contador_juridica_id': 'Tarjeta_profesional_contador_juridica_id',
    }

    PORTAL_ACTA_CORRECTION_FIELDS = {
        'planilla_de_seguridad_social_ju_id': (
            'planilla_de_aportes_a_seguridad_social_ju_corregir'
        ),
        'tarjeta_profesional_Contador_juridica_id': (
            'Tarjeta_profesional_contador_juridica_corregir'
        ),
    }

    def _get_deleted_notifications(self, login):
        try:
            return set(json.loads(login.notificaciones_eliminadas or '[]'))
        except (TypeError, ValueError):
            return set()

    def _get_portal_notifications(self, login):
        notifications = []
        deleted_notifications = self._get_deleted_notifications(login)

        chat_messages = request.env['portal.chat.message'].sudo().search([
            ('login_id', '=', login.id),
            ('sender_type', '=', 'interno'),
        ], order='create_date desc', limit=5)
        for message in chat_messages:
            cuenta = message.cuenta_cobro_id
            if not cuenta:
                cuenta_domain = [('login_id', '=', login.id), ('active', '=', True)]
                if login.documento_de_identidad:
                    cuenta_domain = [
                        '|',
                        ('login_id', '=', login.id),
                        ('documento_de_identificacion', '=', login.documento_de_identidad),
                        ('active', '=', True),
                    ]
                cuenta = request.env['cuentas_de_cobros'].sudo().search(
                    cuenta_domain, order='write_date desc', limit=1
                )
            notification_url = (
                '/public/cuentas_de_cobros/form/{}/{}#soporte'.format(cuenta.id, cuenta.token)
                if cuenta else '/public/cuentas_de_cobros/listado/{}/{}'.format(login.id, login.token)
            )
            notification_key = 'chat:%s' % message.id
            if notification_key in deleted_notifications:
                continue
            notifications.append({
                'key': notification_key,
                'icon': 'fa-comments',
                'title': 'Respuesta de soporte',
                'body': message.body or '',
                'date': message.create_date.strftime('%d/%m/%Y %H:%M') if message.create_date else '',
                'sort_date': message.create_date,
                'url': notification_url,
            })

        cuenta_domain = [
            ('active', '=', True),
            ('estado', '=', 'finalizado'),
        ]
        if login.documento_de_identidad:
            cuenta_domain = [
                '|',
                ('login_id', '=', login.id),
                ('documento_de_identificacion', '=', login.documento_de_identidad),
            ] + cuenta_domain
        else:
            cuenta_domain.append(('login_id', '=', login.id))

        cuentas_finalizadas = request.env['cuentas_de_cobros'].sudo().search(
            cuenta_domain, order='write_date desc', limit=5
        )
        for cuenta in cuentas_finalizadas:
            notification_key = 'cuenta_finalizada:%s' % cuenta.id
            if notification_key in deleted_notifications:
                continue
            notifications.append({
                'key': notification_key,
                'icon': 'fa-check-circle',
                'title': 'La cuenta de cobro del Pago N° {} fue finalizada'.format(cuenta.pago or ''),
                'body': 'Tu cuenta de cobro ya finalizó el proceso.',
                'date': cuenta.write_date.strftime('%d/%m/%Y %H:%M') if cuenta.write_date else '',
                'sort_date': cuenta.write_date,
                'url': '/public/cuentas_de_cobros/form/{}/{}'.format(cuenta.id, cuenta.token),
            })

        cuenta_correccion_domain = [
            ('active', '=', True),
            ('contratista_id.notificacion_enviada', '=', True),
            ('contratista_id.correccion_portal_activa', '=', True),
        ]
        if login.documento_de_identidad:
            cuenta_correccion_domain = [
                '|',
                ('login_id', '=', login.id),
                ('documento_de_identificacion', '=', login.documento_de_identidad),
            ] + cuenta_correccion_domain
        else:
            cuenta_correccion_domain.append(('login_id', '=', login.id))

        cuentas_correccion = request.env['cuentas_de_cobros'].sudo().search(
            cuenta_correccion_domain, order='write_date desc', limit=5
        )
        for cuenta in cuentas_correccion:
            acta = cuenta.contratista_id
            notification_key = 'correccion:%s:%s' % (
                cuenta.id,
                fields.Datetime.to_string(acta.correccion_portal_fecha) if acta.correccion_portal_fecha else 'sin_fecha'
            )
            if notification_key in deleted_notifications:
                continue
            notifications.append({
                'key': notification_key,
                'icon': 'fa-exclamation-circle',
                'title': 'Corrección solicitada',
                'body': acta.correccion_portal_observacion or 'Te solicitaron ajustar información o documentos.',
                'date': acta.correccion_portal_fecha.strftime('%d/%m/%Y %H:%M') if acta.correccion_portal_fecha else '',
                'sort_date': acta.correccion_portal_fecha or cuenta.write_date,
                'url': '/public/cuentas_de_cobros/form/{}/{}'.format(cuenta.id, cuenta.token),
            })

        notifications = sorted(
            notifications,
            key=lambda item: item.get('sort_date') or fields.Datetime.from_string('1970-01-01 00:00:00'),
            reverse=True,
        )
        return notifications[:8]

    def _get_portal_notification_count(self, notifications, login):
        if not login.notificaciones_vistas:
            return len(notifications)
        return len([
            notification for notification in notifications
            if notification.get('sort_date') and notification.get('sort_date') > login.notificaciones_vistas
        ])

    def _portal_profile_values(self, login=None):
        """Common profile values for every authenticated external portal page."""
        if not login:
            user_id = request.httprequest.cookies.get('user_id')
            token = request.httprequest.cookies.get('user_token')
            if user_id and str(user_id).isdigit() and token:
                login = request.env['login'].sudo().search([
                    ('id', '=', int(user_id)),
                    ('token', '=', token),
                ], limit=1)

        if not login or not login.exists():
            return {}

        notifications = self._get_portal_notifications(login)
        return {
            'portal_user': login,
            'portal_notifications': notifications,
            'portal_notification_count': self._get_portal_notification_count(notifications, login),
        }

    @http.route('/public/notifications/read/<int:login_id>/<string:token>', type='json', auth='public', website=True)
    def portal_notifications_read(self, login_id, token, **kwargs):
        login = request.env['login'].sudo().browse(login_id)
        if not login.exists() or login.token != token:
            return {'error': 'unauthorized'}
        self._portal_write(login, {'notificaciones_vistas': fields.Datetime.now()}, login=login)
        return {'ok': True}

    @http.route('/public/notifications/delete/<int:login_id>/<string:token>', type='json', auth='public', website=True)
    def portal_notifications_delete(self, login_id, token, key='', **kwargs):
        login = request.env['login'].sudo().browse(login_id)
        if not login.exists() or login.token != token or not key:
            return {'error': 'invalid'}
        deleted_notifications = self._get_deleted_notifications(login)
        deleted_notifications.add(key)
        self._portal_write(login, {
            'notificaciones_eliminadas': json.dumps(sorted(deleted_notifications)),
            'notificaciones_vistas': fields.Datetime.now(),
        }, login=login)
        return {'ok': True}

    def _upload_portal_replacement_file(self, record, field_name, uploaded_file):
        if not uploaded_file or not record.contratista_id:
            return False
        corregir_field = field_name[:-3] + '_corregir' if field_name.endswith('_id') else ''
        acta_corregir_field = self.PORTAL_ACTA_CORRECTION_FIELDS.get(
            field_name,
            corregir_field,
        )
        cuenta_pendiente_radicacion = not record.contratista_id.notificacion_enviada
        corregir_activo = (
            bool(getattr(record, corregir_field, False))
            or bool(getattr(record.contratista_id, corregir_field, False))
        ) if corregir_field else False
        if corregir_field and not cuenta_pendiente_radicacion and not corregir_activo:
            return False
        stage4 = record.contratista_id.drive_stage_provedor_id
        if not stage4 or not stage4.folder_id:
            return False

        content = uploaded_file.read()
        if not content:
            return False

        label = self.PORTAL_REPLACEMENT_FIELDS.get(field_name, field_name)
        safe_name = re.sub(
            r'[\\/:*?"<>|]+',
            '_',
            uploaded_file.filename or (label + '.pdf'),
        ).strip() or (label + '.pdf')
        login = self._portal_login_for_chatter(record)
        ctx = {'portal_chatter_login_id': login.id} if login else {}
        ctx.update({
            # No se elimina el registro anterior antes de comprobar que la
            # nueva versión quedó realmente almacenada en Google Drive.
            'skip_drive_duplicate_cleanup': True,
        })
        portal_document_type = {
            'informes_contratista_id': 'informes_contratista',
            'otro_documentos_de_inf_id': 'otro_documentos_de_informacion',
            'cesion_derechos_id': 'cesion_derechos',
        }.get(field_name)
        if portal_document_type == 'cesion_derechos':
            ctx['allow_cesion_derechos_portal'] = True
        acta_field_name = self.PORTAL_ACTA_REPLACEMENT_FIELDS.get(
            field_name,
            field_name,
        )
        drive_record = request.env['drive'].sudo().with_context(**ctx).create({
            'name': safe_name,
            'file_name': safe_name,
            'file_data': base64.b64encode(content),
            'folder_id': stage4.folder_id,
            'stage4_id': stage4.id,
            'contratistas_provedores': record.contratista_id.id,
            'portal_acta_document_type': portal_document_type or False,
        })
        if not drive_record.file_id:
            raise UserError(
                "No fue posible verificar el documento '%s' en Google Drive. "
                "La corrección continúa abierta; inténtelo nuevamente." % label
            )
        acta = record.contratista_id.sudo().with_context(**ctx)
        acta.write({acta_field_name: drive_record.id})

        # El archivo nuevo ya quedó verificado en Drive. Cerrar también la
        # marca funcional del documento; varias marcas son campos propios del
        # acta y no cambian únicamente por sustituir el Many2one de Drive.
        # Sin esta sincronización el portal mostraba "Cargado", pero el
        # booleano seguía activo y el tablero permanecía en "Corrigiendo".
        if acta_corregir_field and acta_corregir_field in acta._fields:
            acta.write({acta_corregir_field: False})

        record.contratista_id.with_context(**ctx).message_post(
            body="📎 <b>Documento reemplazado desde portal:</b> {}<br/>{}".format(label, safe_name),
            subtype_xmlid='mail.mt_note',
        )
        return drive_record

    def _reconcile_verified_portal_replacements(self, record):
        """Recupera correcciones ya cargadas antes de instalar esta sincronización."""
        acta = record.contratista_id.sudo() if record.contratista_id else False
        if not acta or not acta.correccion_portal_fecha:
            return

        correction_vals = {}
        for field_name in self.PORTAL_REPLACEMENT_FIELDS:
            correction_field = self.PORTAL_ACTA_CORRECTION_FIELDS.get(
                field_name,
                field_name[:-3] + '_corregir',
            )
            if (
                correction_field not in acta._fields
                or not getattr(acta, correction_field, False)
            ):
                continue

            document_field = self.PORTAL_ACTA_REPLACEMENT_FIELDS.get(
                field_name,
                field_name,
            )
            if document_field not in acta._fields:
                continue
            document = getattr(acta, document_field, False)
            document_date = document and (document.create_date or document.write_date)
            if (
                document
                and document.file_id
                and document_date
                and document_date >= acta.correccion_portal_fecha
                and not document.actualizar_visible
            ):
                correction_vals[correction_field] = False

        if correction_vals:
            acta.write(correction_vals)

    def _notify_assistant_corrections_finished(self, record, replaced_labels):
        acta = record.contratista_id
        if not acta:
            return
        # El portal público no debe recibir permisos sobre grupos internos.
        # El servidor eleva únicamente la lectura necesaria para resolver a
        # quién debe notificarse.
        acta_sudo = acta.sudo()
        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        form_url = "{}/web#id={}&model=acta_entrega_documentos&view_type=form".format(base_url, acta.id)
        users = request.env['res.users'].sudo()
        solicitante = acta_sudo.correccion_solicitante_id
        if solicitante and not solicitante.share:
            users |= solicitante

        group = request.env.ref(
            'Matriz.group_contratacion_asis_coordinador',
            raise_if_not_found=False,
        )
        group = group.sudo() if group else group
        if group:
            usuarios_grupo = group.users.sudo()
            asistentes_proyecto = acta_sudo.users_with_access.sudo().filtered(
                lambda user: user in usuarios_grupo and not user.share
            )
            users |= asistentes_proyecto

        portal_emails = set(
            email.strip().lower()
            for email in (
                getattr(record, 'email', False),
                getattr(record, 'correo_facturacion', False),
                getattr(acta, 'correo_facturacion', False),
                getattr(getattr(record, 'login_id', False), 'coreo', False),
            )
            if email
        )
        recipient_emails = []
        for user in users:
            email = user.email or user.partner_id.email
            if email and email.strip().lower() not in portal_emails and email not in recipient_emails:
                recipient_emails.append(email)

        if not recipient_emails:
            if group:
                fallback_users = (
                    acta_sudo.users_with_access.sudo().filtered(
                        lambda user: user in usuarios_grupo
                    )
                    or usuarios_grupo
                )
                for user in fallback_users:
                    email = user.email or user.partner_id.email
                    if email and email.strip().lower() not in portal_emails and email not in recipient_emails:
                        recipient_emails.append(email)

        if not recipient_emails:
            _logger.warning(
                "No se encontró correo para notificar corrección finalizada. Acta ID: %s",
                acta.id,
            )
            return

        docs_items_html = ''
        if replaced_labels:
            docs_items_html = ''.join(
                '<li style="margin:6px 0;">{}</li>'.format(html_escape(str(label)))
                for label in replaced_labels
            )
        docs_html = """
            <div style="background-color:#eef6ff; border-left:4px solid #043484; padding:12px 18px; margin:18px 0;">
                <strong style="color:#043484;">Corrección(es) realizadas:</strong>
                <ul style="margin:8px 0 0 18px; padding:0;">{}</ul>
            </div>
        """.format(docs_items_html) if docs_items_html else ''

        proyecto = acta.proyecto_relacionado.display_name if acta.proyecto_relacionado else 'Sin proyecto'

        body_html = """
            <div style="margin:0; padding:24px; background-color:#f4f7fb; font-family:Arial, Helvetica, sans-serif; color:#1f2937;">
                <div style="max-width:720px; margin:0 auto; background-color:#ffffff; border:1px solid #dbe4f0; border-radius:8px; overflow:hidden;">
                    <div style="background-color:#043484; color:#ffffff; text-align:center; padding:16px 22px; font-size:18px; font-weight:bold;">
                        Correcciones finalizadas - Cuenta de cobro
                    </div>
                    <div style="padding:24px 28px;">
                        <p style="margin:0 0 16px 0; font-size:15px; line-height:1.5;">
                            El contratista/proveedor <strong>{}</strong> finalizó las correcciones solicitadas en la cuenta de cobro.
                        </p>
                        <div style="background-color:#f8fafc; border-left:4px solid #043484; padding:12px 16px; margin:16px 0;">
                            <div style="margin:4px 0;"><strong>Cuenta de cobro No.:</strong> {}</div>
                            <div style="margin:4px 0;"><strong>Proyecto:</strong> {}</div>
                            <div style="margin:4px 0;"><strong>Contratista/proveedor:</strong> {}</div>
                        </div>
                        {}
                        <div style="text-align:center; margin:26px 0 8px 0;">
                            <a href="{}" style="background-color:#043484; color:#ffffff; padding:11px 26px; border-radius:4px; text-decoration:none; font-weight:bold; display:inline-block;">
                                Ver registro en SGO
                            </a>
                        </div>
                    </div>
                    <div style="background-color:#f8fafc; color:#64748b; font-size:12px; padding:12px 24px; border-top:1px solid #e5e7eb;">
                        Este es un mensaje automático de SGO.
                    </div>
                </div>
            </div>
        """.format(
            html_escape(str(acta.name or record.name or '')),
            html_escape(str(record.pago or record.id)),
            html_escape(str(proyecto)),
            html_escape(str(acta.name or record.name or '')),
            docs_html,
            form_url,
        )

        login = self._portal_login_for_chatter(record)
        ctx = {'portal_chatter_login_id': login.id} if login else {}
        estado_destino = dict(acta_sudo._fields['estado'].selection).get(
            acta_sudo.estado,
            acta_sudo.estado or 'Sin estado',
        )
        acta.with_context(**ctx).message_post(
            body=(
                "✅ <b>Correcciones finalizadas por el contratista/proveedor.</b>"
                "<br/><b>Movimiento en el tablero:</b> Corrigiendo → {}{}"
            ).format(html_escape(str(estado_destino)), docs_html),
            subtype_xmlid='mail.mt_note',
        )

        for email in recipient_emails:
            try:
                request.env['mail.mail'].sudo().create({
                    'subject': 'Correcciones finalizadas - Cuenta de cobro',
                    'body_html': body_html,
                    'email_to': email,
                    'email_from': 'go@fundacionunivalle.org',
                }).send()
            except Exception:
                # La notificación es secundaria: una falla temporal del correo
                # no debe deshacer el documento ya verificado ni dejar la
                # cuenta atrapada en "Corrigiendo".
                _logger.exception(
                    "No se pudo notificar por correo la corrección finalizada "
                    "del acta %s al destinatario %s.",
                    acta.id,
                    email,
                )

    def _portal_format_correction_value(self, record, field_name):
        field = record._fields.get(field_name)
        value = record[field_name] if field_name in record._fields else False
        if not value:
            return ''
        if field and field.type == 'many2one':
            return value.display_name or ''
        if field and field.type == 'selection':
            selection = field.selection(record) if callable(field.selection) else field.selection
            return dict(selection or []).get(value, value)
        if field and field.type in ('date', 'datetime'):
            return fields.Date.to_string(value) if field.type == 'date' else fields.Datetime.to_string(value)
        if field and field.type in ('float', 'monetary'):
            try:
                return "{:,.2f}".format(float(value or 0.0))
            except (TypeError, ValueError):
                return str(value or '')
        return str(value or '')

    def _portal_correction_snapshot(self, record):
        campos = (
            ('name', 'Nombre/Razón social'),
            ('documento_de_identificacion', 'Documento de identificación'),
            ('selecionar', 'Tipo de contrato'),
            ('relacion_fuv_id', 'Relación de contrato'),
            ('relacion_otrosi_fuv_id', 'Relación de otrosí'),
            ('relacion_orden_compra_servicios_fuv_id', 'Relación orden de servicios'),
            ('relacion_orden_compra_bienes_fuv_id', 'Relación orden de bienes'),
            ('proyecto_regalias_id', 'Proyecto de Regalías'),
            ('valor_cuenta', 'Valor cuenta/factura'),
            ('valor_total_contrato', 'Valor total del contrato'),
            ('pago', 'Pago N°'),
            ('de', 'Total de pagos'),
            ('porcentaje_de_pago_individual', 'Porcentaje de pago'),
            ('porcentaje_de_pago', 'Porcentaje acumulado'),
            ('inicio_actividad', 'Inicio de actividad'),
            ('finalizacion_actividad', 'Finalización de actividad'),
            ('numero_factura', 'Número de factura'),
            ('factura_si_o_no', 'Tiene factura'),
        )
        snapshot = {}
        for field_name, label in campos:
            if field_name in record._fields:
                snapshot[field_name] = {
                    'label': label,
                    'value': self._portal_format_correction_value(record, field_name),
                }
        return snapshot

    def _portal_changed_correction_labels(self, before_snapshot, after_snapshot):
        changed_labels = []
        for field_name, before_data in before_snapshot.items():
            after_data = after_snapshot.get(field_name)
            if not after_data:
                continue
            before_value = before_data.get('value') or ''
            after_value = after_data.get('value') or ''
            if before_value == after_value:
                continue
            label = before_data.get('label') or field_name
            changed_labels.append(
                "{}: {} → {}".format(
                    label,
                    before_value or 'Vacío',
                    after_value or 'Vacío',
                )
            )
        return changed_labels

    def _format_portal_relation_date(self, value):
        if not value:
            return 'Sin fecha'
        return value.strftime('%Y-%m-%d') if hasattr(value, 'strftime') else str(value)

    def _format_portal_relation_value(self, value):
        try:
            return "$ {:,.2f}".format(float(value or 0.0))
        except (TypeError, ValueError):
            return "$ 0.00"

    def _portal_relation_label(self, record):
        id_contrato = getattr(record, 'id_del_contrato', False) or 'Sin ID'
        fecha_inicio = self._format_portal_relation_date(
            getattr(record, 'fecha_iniciacion', False) or getattr(record, 'fecha_inicio', False)
        )
        fecha_fin = self._format_portal_relation_date(
            getattr(record, 'fecha_terminacion', False) or getattr(record, 'fecha_fin', False)
        )
        valor_base = (
            getattr(record, 'valor_total_contrato', False)
            or getattr(record, 'total', False)
            or getattr(record, 'total_poliza', False)
            or 0.0
        )
        valor = self._format_portal_relation_value(valor_base)
        return f"ID: {id_contrato} | Ini: {fecha_inicio} / Fin: {fecha_fin} | Valor: {valor}"

    def _portal_relation_total_value(self, record):
        return (
            getattr(record, 'valor_total_contrato', False)
            or getattr(record, 'total', False)
            or getattr(record, 'total_poliza', False)
            or 0.0
        )

    @staticmethod
    def _portal_regalias_key(value):
        return re.sub(r'[^0-9a-zA-Z]+', '', value or '').lower()

    @staticmethod
    def _portal_document_key(value):
        return re.sub(r'\D+', '', value or '')

    def _portal_relation_belongs_to_owner(
        self, relation, owner_document, owner_person_type, natural_document_field
    ):
        """Comprueba la propiedad sin confiar en datos de identidad del POST."""
        if not relation:
            return False
        relation_document_field = (
            'nit' if owner_person_type == 'persona_juridica'
            else natural_document_field
        )
        owner_key = self._portal_document_key(owner_document)
        relation_key = self._portal_document_key(
            getattr(relation, relation_document_field, False)
        )
        return bool(owner_key and relation_key and owner_key == relation_key)

    @staticmethod
    def _portal_serialize_contractual_relation(relation):
        """Serializa el cálculo de saldo y la creación para una relación.

        Odoo usa REPEATABLE READ. Una actualización sin cambio lógico hace que
        dos solicitudes sobre la misma fila no puedan validar el mismo saldo:
        la segunda transacción será reintentada por Odoo con una vista nueva.
        """
        relation.ensure_one()
        table_name = relation._table
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table_name or ''):
            raise ValidationError('No fue posible bloquear la relación contractual seleccionada.')
        request.env.cr.execute(
            'UPDATE "%s" SET id = id WHERE id = %%s RETURNING id' % table_name,
            [relation.id],
        )
        if not request.env.cr.fetchone():
            raise ValidationError('La relación contractual seleccionada ya no está disponible.')

    def _portal_regalias_centro_costo_proyecto(self, proyecto):
        """Unidad de negocio del proyecto de Regalías.

        Para el portal de Regalías el amarre se toma únicamente de
        informacion_base_agregar.id_proyecto, que en este modelo corresponde a
        "Unidad de negocio (Centro de costos)".
        """
        if not proyecto:
            return ''
        return proyecto.id_proyecto if 'id_proyecto' in proyecto._fields else ''

    def _portal_regalias_info_base_from_contratacion(self, contratacion):
        if not contratacion:
            return request.env['informacion_base_agregar'].sudo()
        informacion_base = contratacion.informacion_base_id
        if not informacion_base:
            informacion_base = contratacion.informacion_base_agregar_ids[:1]
        if not informacion_base:
            informacion_base = request.env['informacion_base_agregar'].sudo().search([
                ('contratacion_id', '=', contratacion.id),
            ], limit=1)
        if not informacion_base:
            centro_costo = contratacion.mcb_centro_costo or contratacion.id_proyecto2
            if centro_costo:
                informacion_base = request.env['informacion_base_agregar'].sudo().search([
                    ('id_proyecto', '=', centro_costo),
                ], limit=1)
        return informacion_base

    def _portal_regalias_centro_costo_contratacion(self, contratacion):
        if not contratacion:
            return ''
        informacion_base = self._portal_regalias_info_base_from_contratacion(contratacion)
        return informacion_base.id_proyecto if informacion_base else ''

    def _filter_portal_relations_with_balance(self, records, account_field):
        if not records:
            return records

        Cuenta = request.env['cuentas_de_cobros'].sudo()
        available_ids = []
        for record in records:
            total = float(self._portal_relation_total_value(record) or 0.0)
            if total <= 0:
                available_ids.append(record.id)
                continue

            charged = sum(Cuenta.search([
                (account_field, '=', record.id),
                ('contratista_id.estado', '!=', 'rechazado'),
            ]).mapped('valor_cuenta'))
            if charged < (total - 0.01):
                available_ids.append(record.id)

        return records.browse(available_ids)

    @http.route('/public/login', type='http', auth='public', website=True)
    def login_form(self, **kwargs):
        user_id = request.httprequest.cookies.get('user_id')
        token = request.httprequest.cookies.get('user_token')
        return_url = _safe_public_return_url(kwargs.get('return_url'))

        if user_id and token:
            user = request.env['login'].sudo().search([
                ('id', '=', int(user_id)),
                ('token', '=', token)
            ], limit=1)

            if user:
                self._portal_write(user, {
                    'sesion_activa': True,
                    'ultima_sesion': fields.Datetime.now(),
                }, login=user)
                if return_url:
                    return redirect(return_url)
                return redirect(f'/public/dashboard/{user.id}/{user.token}')

        return request.render('Matriz.login_template', {'return_url': return_url or ''})

    @http.route('/public/login/submit', type='http', auth='public', methods=['POST'], website=True, csrf=False)
    def login_submit(self, **post):
        return_url = _safe_public_return_url(post.get('return_url'))

        ok, error = verify_recaptcha(post.get('g-recaptcha-response'))
        if not ok:
            return request.render('Matriz.login_template', {
                'error': error,
                'return_url': return_url or '',
            })

        email = post.get('coreo')
        if email:
            email = email.lower().strip() # 🔹 Normaliza el correo

        password = post.get('password')

        user = request.env['login'].sudo().search([
            ('coreo', '=ilike', email), # 🔹 =ilike ignora mayúsculas/minúsculas en la base de datos
            ('password', '=', password)
        ], limit=1)

        if user:
            self._portal_write(user, {
                'sesion_activa': True,
                'ultima_sesion': fields.Datetime.now(),
            }, login=user)
            response = redirect(return_url or f'/public/dashboard/{user.id}/{user.token}')
            # Guardar sesión en cookies por 30 días
            response.set_cookie('user_id', str(user.id), max_age=60 * 60 * 24 * 30)
            response.set_cookie('user_token', user.token, max_age=60 * 60 * 24 * 30)
            return response
        else:
            return request.render('Matriz.login_template', {
                'error': "Credenciales incorrectas",
                'return_url': return_url or '',
            })

    @http.route('/public/logout', type='http', auth='public', website=True)
    def public_logout(self, **kwargs):
        user_id = request.httprequest.cookies.get('user_id')
        token = request.httprequest.cookies.get('user_token')
        if user_id and token and str(user_id).isdigit():
            user = request.env['login'].sudo().search([
                ('id', '=', int(user_id)),
                ('token', '=', token)
            ], limit=1)
            if user:
                self._portal_write(user, {'sesion_activa': False}, login=user)

        response = redirect('/public/login')
        response.delete_cookie('user_id')
        response.delete_cookie('user_token')
        return response

    @http.route('/public/dashboard/<int:user_id>/<string:token>', type='http', auth='public', website=True)
    def public_dashboard(self, user_id, token, **kwargs):
        user = request.env['login'].sudo().search([
            ('id', '=', user_id),
            ('token', '=', token)
        ], limit=1)

        if not user:
            return request.render('Matriz.login_template', {
                'error': "Acceso no válido"
            })

        if not user.sesion_activa:
            self._portal_write(user, {
                'sesion_activa': True,
                'ultima_sesion': fields.Datetime.now(),
            }, login=user)

        notifications = self._get_portal_notifications(user)
        contratista = self._portal_contratista_for_login(user)
        return request.render('Matriz.inicio_publico_template', {
            'user': user,
            'contratista': contratista,
            'notifications': notifications,
            'notification_count': self._get_portal_notification_count(notifications, user),
            'portal_user': user,
            'portal_profile_embedded': True,
        })

    @http.route('/public/cuentas_de_cobros/new/<int:login_id>/<string:token>', type='http', auth='public', website=True)
    def public_create_form(self, login_id, token, **kwargs):
        login = request.env['login'].sudo().browse(login_id)

        if not login or login.token != token:
            return request.not_found()

        puede_crear_fuv, _mensaje_bloqueo_fuv = login.estado_creacion_cuenta_cobro_portal()
        puede_crear_regalias, _mensaje_bloqueo_regalias = login.estado_creacion_cuenta_regalias_portal()
        if not puede_crear_fuv and not puede_crear_regalias:
            return request.redirect(
                '/public/cuentas_de_cobros/listado/%s/%s?creation_blocked=1' % (login.id, login.token)
            )

        # Datos del login
        nombre = login.name
        email = login.coreo
        documento_identificacion = login.documento_de_identidad
        documento_identificacion_key = re.sub(r'\D+', '', documento_identificacion or '')
        tipo_persona = login.clase_persona
        identificacion_regalias = documento_identificacion

        # 🔄 Filtro condicionado según tipo de persona + estado_contratacion = 'contratado'
        if tipo_persona == 'persona_juridica':
            relaciones = request.env['contrataciones_fuv_relacion'].sudo().search([
                ('select_persona', '=', tipo_persona),
                ('estado_contratacion', '=', 'contratado')  # ✅ nuevo filtro agregado
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
            )
        else:
             # 👇 Si eligió "Persona Natural" en el HTML, buscamos ambos en la base de datos
            relaciones = request.env['contrataciones_fuv_relacion'].sudo().search([
                ('select_persona', 'in', ['persona_natural', 'contrato_laboral']), # ✅ EL TRUCO ESTÁ AQUÍ
                ('estado_contratacion', '=', 'contratado')
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.documento_identidad or '') == documento_identificacion_key
            )


        # 🔄 Filtro para relacion_orden_compra_servicios_fuv_id
        if tipo_persona == 'persona_juridica':
            relacion_orden_compra_servicios = request.env['orden_compra_servicios'].sudo().search([
                ('seleccion_persona', '=', tipo_persona),
                ('estado_compra_servicios', '=', 'contratado')  # ✅ nuevo filtro agregado
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
            )
        else:
            relacion_orden_compra_servicios = request.env['orden_compra_servicios'].sudo().search([
                ('seleccion_persona', '=', tipo_persona),
                ('estado_compra_servicios', '=', 'contratado')  # ✅ nuevo filtro agregado
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.cedula_proveedor or '') == documento_identificacion_key
            )
        
        # 🔄 Filtro para relacion_orden_compra_bienes_fuv_id
        if tipo_persona == 'persona_juridica':
            relacion_orden_compra_bienes = request.env['orden_compra_bienes'].sudo().search([
                ('seleccion_persona', '=', tipo_persona),
                ('estado_compra_bienes', '=', 'contratado')  # ✅ nuevo filtro agregado
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
            )
        else:
            relacion_orden_compra_bienes = request.env['orden_compra_bienes'].sudo().search([
                ('seleccion_persona', '=', tipo_persona),
                ('estado_compra_bienes', '=', 'contratado')  # ✅ nuevo filtro agregado
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.cedula_proveedor or '') == documento_identificacion_key
            )
        

        # filtro para relacion_otrosi_fuv_id
        if tipo_persona == 'persona_juridica':
            relacion_otrosi = request.env['otrosi_bdd'].sudo().search([
                ('select_persona', '=', tipo_persona),
                ('estado_contratacion', 'in', _ESTADOS_OTROSI_DISPONIBLES_PARA_CUENTA),
                '|', '|',
                ('fuente_id.adicion', '=', True),
                ('fuente_id.adicion_prorroga', '=', True),
                '&',
                ('fuente_id.otro_si_modificatorio', '=', True),
                ('valor_total_contrato', '>', 0),
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
            )
        else:
            relacion_otrosi = request.env['otrosi_bdd'].sudo().search([
                ('select_persona', 'in', ['persona_natural', 'contrato_laboral']),
                ('estado_contratacion', 'in', _ESTADOS_OTROSI_DISPONIBLES_PARA_CUENTA),
                '|', '|',
                ('fuente_id.adicion', '=', True),
                ('fuente_id.adicion_prorroga', '=', True),
                '&',
                ('fuente_id.otro_si_modificatorio', '=', True),
                ('valor_total_contrato', '>', 0),
            ]).filtered(
                lambda rel: re.sub(r'\D+', '', rel.documento_identidad or '') == documento_identificacion_key
            )

        # 👇 record inicializado para que no dé error en QWeb
        record = {"selecionar": "", "origen_cuenta": "fuv"}
        proyectos_regalias = request.env['informacion_base_agregar'].sudo().search([
            ('area_solicitante2', '=', 'regalias'),
            ('nombre_proceso_proyecto', '!=', False),
            '|',
            ('contratacion_id', '!=', False),
            ('informacion_base_agregar_ids', '!=', False),
        ], order='nombre_proceso_proyecto, id')
        proyectos_regalias_unidades = {}
        for proyecto in proyectos_regalias:
            unidad_regalias = self._portal_regalias_centro_costo_proyecto(proyecto)
            proyectos_regalias_unidades[proyecto.id] = unidad_regalias
        identificacion_regalias_key = re.sub(r'\D+', '', identificacion_regalias or '')
        contratos_regalias = request.env['base.datos.regalias.contrato'].sudo().search(
            [], order='numero_de_contrato, id'
        ).filtered(
            lambda contrato: re.sub(r'\D+', '', contrato.identificacion or '') == identificacion_regalias_key
        )
        CuentaCobro = request.env['cuentas_de_cobros'].sudo()
        contratos_regalias_saldos = {
            contrato.id: CuentaCobro._saldo_disponible_contrato_regalias(contrato)
            for contrato in contratos_regalias
        }
        contratos_regalias = contratos_regalias.filtered(
            lambda contrato: contratos_regalias_saldos.get(contrato.id, 0.0) > 0.01
        )
        unidades_contratos_regalias = {
            self._portal_regalias_key(contrato.un)
            for contrato in contratos_regalias
            if self._portal_regalias_key(contrato.un)
        }
        proyectos_regalias = proyectos_regalias.filtered(
            lambda proyecto: self._portal_regalias_key(
                proyectos_regalias_unidades.get(proyecto.id)
                or self._portal_regalias_centro_costo_proyecto(proyecto)
            ) in unidades_contratos_regalias
        )
        proyectos_regalias_unidades = {
            proyecto.id: (
                proyectos_regalias_unidades.get(proyecto.id)
                or self._portal_regalias_centro_costo_proyecto(proyecto)
            )
            for proyecto in proyectos_regalias
        }

        relaciones = self._filter_portal_relations_with_balance(
            relaciones, 'relacion_fuv_id'
        )
        relacion_orden_compra_servicios = self._filter_portal_relations_with_balance(
            relacion_orden_compra_servicios, 'relacion_orden_compra_servicios_fuv_id'
        )
        relacion_orden_compra_bienes = self._filter_portal_relations_with_balance(
            relacion_orden_compra_bienes, 'relacion_orden_compra_bienes_fuv_id'
        )
        relacion_otrosi = self._filter_portal_relations_with_balance(
            relacion_otrosi, 'relacion_otrosi_fuv_id'
        )

        fuv_enabled, fuv_disabled_message = login.estado_creacion_cuenta_cobro_portal()
        regalias_enabled, regalias_disabled_message = login.estado_creacion_cuenta_regalias_portal()
        values = {
            'login_id': login_id,
            'record': record,
            'token': token,
            'nombre': nombre,
            'email': email,
            'documento_identificacion': documento_identificacion,
            'tipo_persona': tipo_persona,
            'identificacion_regalias': identificacion_regalias,
            'relaciones': relaciones,
            'relacion_orden_compra_servicios': relacion_orden_compra_servicios,
            'relacion_orden_compra_bienes': relacion_orden_compra_bienes,  # 🔸 nueva relación filtrada
            'relacion_otrosi': relacion_otrosi,  # 🔸 nueva relación filtrada
            'relation_label': self._portal_relation_label,
            'proyectos_regalias': proyectos_regalias,
            'proyectos_regalias_unidades': proyectos_regalias_unidades,
            'contratos_regalias': contratos_regalias,
            'contratos_regalias_saldos': contratos_regalias_saldos,
            'fuv_enabled': fuv_enabled,
            'fuv_disabled_message': fuv_disabled_message,
            'regalias_enabled': regalias_enabled,
            'regalias_disabled_message': regalias_disabled_message,
        }
        values.update(self._portal_profile_values(login))
        return request.render('Matriz.template_cuentas_de_cobros_create_form', values)



    @http.route('/public/cuentas_de_cobros/save/<int:record_id>', type='http', auth='public', methods=['POST'], website=True)
    def save_public_form(self, record_id, **post):
        record = request.env['cuentas_de_cobros'].sudo().browse(record_id)
        submitted_token = post.get('token') or post.get('access_token')
        if not record.exists() or not submitted_token or record.token != submitted_token:
            _logger.warning("Intento de guardado público no autorizado para cuenta de cobro %s.", record_id)
            return request.not_found()

        campo_fecha_invalida = _portal_error_fecha_actividad(post)
        if campo_fecha_invalida:
            return request.redirect(
                '/public/cuentas_de_cobros/form/%s/%s?fecha_error=%s#%s' % (
                    record.id,
                    record.token,
                    campo_fecha_invalida,
                    campo_fecha_invalida,
                )
            )

        if record:
            acta_before_save = record.contratista_id
            if acta_before_save and acta_before_save.estado == 'rechazado':
                return request.render('website.403', {
                    'message': 'Esta cuenta/factura fue rechazada y ya no permite modificaciones desde el portal.'
                })
            cuenta_ya_radicada = bool(acta_before_save and acta_before_save.notificacion_enviada)
            has_replacement_files = any(
                request.httprequest.files.get(field_name + '_replace_file')
                for field_name in self.PORTAL_REPLACEMENT_FIELDS
            )
            correction_mode = bool(
                cuenta_ya_radicada
                and record.contratista_id
                and record.contratista_id.correccion_portal_activa
                and (
                    record.contratista_id.bloqueo
                    or record.contratista_id.periodo_actividad_corregir
                    or has_replacement_files
                )
            )
            pending_correction_labels = (
                acta_before_save._documentos_marcados_para_correccion()
                if cuenta_ya_radicada and acta_before_save else []
            )
            correction_request_active = bool(
                cuenta_ya_radicada
                and acta_before_save
                and acta_before_save.correccion_portal_activa
            )
            if correction_request_active and acta_before_save.estado != 'asis_coordinador':
                return _portal_bad_request({
                    'message': (
                        'La corrección solo puede gestionarse mientras la cuenta '
                        'está en Asistente de coordinación.'
                    )
                })
            replaced_labels = []
            correction_before_snapshot = (
                self._portal_correction_snapshot(record)
                if correction_request_active else {}
            )

            # Procesar IDs de relación
            # La relación contractual se define al crear la cuenta y luego es
            # inmutable desde el portal. No confiar en IDs enviados por el
            # navegador: un atributo readonly/disabled se puede retirar desde
            # el inspector.
            relacion_fuv_id = record.relacion_fuv_id.id or False
            relacion_orden_compra_servicios_fuv_id = (
                record.relacion_orden_compra_servicios_fuv_id.id or False
            )
            relacion_orden_compra_bienes_fuv_id = (
                record.relacion_orden_compra_bienes_fuv_id.id or False
            )
            relacion_otrosi_fuv_id = record.relacion_otrosi_fuv_id.id or False

            files = request.httprequest.files
            cesion_derechos_marcada = post.get('cesion_derechos') in (
                True, '1', 'true', 'True', 'on', 'yes', 'si'
            )
            cesion_derechos_files = _portal_uploaded_files(files, 'cesion_derechos_doc')
            if post.get('guardar_cesion') == '1' or cesion_derechos_files:
                cesion_derechos_marcada = True
            factura_switch_present = post.get('factura_si_o_no_present') in (
                True, '1', 'true', 'True', 'on', 'yes', 'si'
            )
            if factura_switch_present:
                # En modo corrección el switch debe poder apagarse. Si se
                # desmarca, no inferimos "factura" a partir del valor anterior.
                factura_si_o_no = post.get('factura_si_o_no') in (
                    True, '1', 'true', 'True', 'on', 'yes', 'si'
                )
            else:
                factura_si_o_no = (
                    post.get('factura_si_o_no') in (True, '1', 'true', 'True', 'on', 'yes', 'si')
                    or bool(files.get('factura_doc'))
                    or bool(
                        (post.get('numero_factura') or '').strip()
                        and (post.get('numero_factura') or '').strip().upper() not in ('N/A', 'NA')
                    )
                )

            # Armar diccionario con datos generales
            values_to_update = {
                'numero_factura': post.get('numero_factura'),
                'valor_cuenta': self._parse_portal_number(post.get('valor_cuenta')),
                'nombre_entrega': post.get('nombre_entrega'),
                'pago': post.get('pago'),
                'pago_forma_pago': post.get('pago_forma_pago') or False,
                'inicio_actividad': post.get('inicio_actividad') or False,
                'finalizacion_actividad': post.get('finalizacion_actividad') or False,
                'factura_electronica_juridica_id': post.get('factura_electronica_juridica_id'),
                'rut_actualizado_juridica_id': post.get('rut_actualizado_juridica_id'),
                'resolucion_facturacion_electronica_juridica_id': post.get('resolucion_facturacion_electronica_juridica_id'),
                'camara_comercio_juridica_id': post.get('camara_comercio_juridica_id'),
                'rut_representante_legal_juridica_id': post.get('rut_representante_legal_juridica_id'),
                'certificado_pago_parafiscales_juridica_id': post.get('certificado_pago_parafiscales_juridica_id'),
                'tarjeta_profesional_revisor_fiscal_juridica_id': post.get('tarjeta_profesional_revisor_fiscal_juridica_id'),
                'tarjeta_profesional_Contador_juridica_id': post.get('tarjeta_profesional_Contador_juridica_id'),
                'cedula_de_contador_juridica_id': post.get('cedula_de_contador_juridica_id'),
                'cedula_representante_legal_juridica_id': post.get('cedula_representante_legal_juridica_id'),
                'certificado_junta_contadores_juridica_id': post.get('certificado_junta_contadores_juridica_id'),
                'documento_equivalente_id': post.get('documento_equivalente_id'),
                'informe_de_actividades_id': post.get('informe_de_actividades_id'),
                'declaracion_juramentada_y_soportes_id': post.get('declaracion_juramentada_y_soportes_id'),
                'rut_actualizado_id': post.get('rut_actualizado_id'),
                'planilla_de_aportes_a_seguridad_social_id': post.get('planilla_de_aportes_a_seguridad_social_id'),
                'copia_cedula_id': post.get('copia_cedula_id'),
                'certificacion_bancaria_id': post.get('certificacion_bancaria_id'),
                'documento_equivalente_corregir': post.get('documento_equivalente_corregir'),
                'informe_de_actividades_corregir': post.get('informe_de_actividades_corregir'),
                'declaracion_juramentada_y_soportes_corregir': post.get('declaracion_juramentada_y_soportes_corregir'),
                'rut_actualizado_corregir': post.get('rut_actualizado_corregir'),
                'planilla_de_aportes_a_seguridad_social_corregir': post.get('planilla_de_aportes_a_seguridad_social_corregir'),
                'copia_cedula_corregir': post.get('copia_cedula_corregir'),
                'certificacion_bancaria_corregir': post.get('certificacion_bancaria_corregir'),
                'resolucion_facturacion_electronica_corregir': post.get('resolucion_facturacion_electronica_corregir'),
                'resolucion_facturacion_electronica_id': post.get('resolucion_facturacion_electronica_id'),
                'cedula_revisor_fiscal_juridica_id': post.get('cedula_revisor_fiscal_juridica_id'),
                'tarjeta_profesional_revisor_fiscal_juridica_corregir': post.get('tarjeta_profesional_revisor_fiscal_juridica_corregir'),
                'cedula_revisor_fiscal_juridica_corregir': post.get('cedula_revisor_fiscal_juridica_corregir'),
                'certificado_de_expensa_corregir': post.get('certificado_de_expensa_corregir'),
                'certificado_de_expensa_id': post.get('certificado_de_expensa_id'),
                'planilla_de_seguridad_social_ju_id': post.get('planilla_de_seguridad_social_ju_id'),
                'planilla_de_seguridad_social_ju_corregir': post.get('planilla_de_seguridad_social_ju_corregir'),
            }
            # Los checkbox HTML no se envían cuando no están presentes en
            # la vista. Solo cambiar esta opción si el formulario realmente la
            # incluyó; de lo contrario se conserva el valor existente.
            if 'opcion_otro_documento' in post:
                values_to_update['opcion_otro_documento'] = post.get(
                    'opcion_otro_documento'
                ) in (True, '1', 'true', 'True', 'on', 'yes', 'si')

            if factura_switch_present:
                values_to_update['factura_si_o_no'] = factura_si_o_no
                if not factura_si_o_no:
                    # El número anterior no puede seguir haciendo que el sistema
                    # trate la cuenta como facturada después de una corrección.
                    values_to_update['numero_factura'] = 'N/A'
            elif 'factura_si_o_no' in post or factura_si_o_no:
                values_to_update['factura_si_o_no'] = factura_si_o_no
            if 'factura_id' in post:
                values_to_update['factura_id'] = post.get('factura_id')

            # Una corrección documental vuelve a enviar los campos financieros
            # visibles del formulario aunque el usuario no los haya cambiado.
            # No revalidarlos ni reescribirlos evita que movimientos registrados
            # después de la radicación bloqueen la corrección. Si el valor o el
            # desembolso sí cambian, las validaciones normales se conservan.
            def _portal_int_or(value, fallback=0):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return int(fallback or 0)

            valor_enviado = (
                self._parse_portal_number(post.get('valor_cuenta'))
                if 'valor_cuenta' in post
                else (record.valor_cuenta or 0.0)
            )
            pago_forma_enviado = _portal_int_or(
                post.get('pago_forma_pago'),
                record.pago_forma_pago or record.pago,
            )
            pago_forma_actual = int(record.pago_forma_pago or record.pago or 0)
            datos_financieros_sin_cambios = bool(
                correction_request_active
                and abs(valor_enviado - (record.valor_cuenta or 0.0)) < 0.01
                and pago_forma_enviado == pago_forma_actual
            )
            if datos_financieros_sin_cambios:
                values_to_update.pop('valor_cuenta', None)
                values_to_update.pop('pago_forma_pago', None)
            # Una cuenta de Regalías ya vinculada conserva siempre su
            # contrato. El selector del portal puede estar oculto o
            # deshabilitado y, en ese caso, no es una orden para desvincularlo.
            if record.origen_cuenta == 'regalias' and record.contrato_regalias_id:
                values_to_update['contrato_regalias_id'] = record.contrato_regalias_id.id
            elif post.get('contrato_regalias_id'):
                values_to_update['contrato_regalias_id'] = post.get('contrato_regalias_id')

            if record.origen_cuenta == 'regalias' and not values_to_update.get('contrato_regalias_id'):
                contrato_actual = record.contrato_regalias_id
                if contrato_actual:
                    values_to_update['contrato_regalias_id'] = contrato_actual.id
                else:
                    centro_costo_key = self._portal_regalias_key(
                        self._portal_regalias_centro_costo_proyecto(record.proyecto_regalias_id)
                    )
                    identificacion_key = re.sub(
                        r'\D+',
                        '',
                        record.documento_de_identificacion
                        or (record.login_id.documento_de_identidad if record.login_id else '')
                        or ''
                    )
                    contratos_posibles = request.env['base.datos.regalias.contrato'].sudo().search([]).filtered(
                        lambda contrato: (
                            re.sub(r'\D+', '', contrato.identificacion or '') == identificacion_key
                            and self._portal_regalias_key(contrato.un) == centro_costo_key
                        )
                    )
                    if not contratos_posibles:
                        contratos_por_identificacion = request.env['base.datos.regalias.contrato'].sudo().search([]).filtered(
                            lambda contrato: re.sub(r'\D+', '', contrato.identificacion or '') == identificacion_key
                        )
                        if len(contratos_por_identificacion) == 1:
                            contratos_posibles = contratos_por_identificacion
                    if len(contratos_posibles) == 1:
                        contrato_recuperado = contratos_posibles[:1]
                        values_to_update['contrato_regalias_id'] = contrato_recuperado.id
                        valor_contrato = self._parse_portal_number(
                            contrato_recuperado.valor_de_contrato_vigencia_2025_2026
                        )
                        if valor_contrato:
                            values_to_update['valor_total_contrato'] = valor_contrato
                    else:
                        _logger.warning(
                            "No se pudo recuperar contrato Regalías para cuenta %s. identificacion=%s centro_costo=%s contratos=%s",
                            record.id,
                            identificacion_key,
                            centro_costo_key,
                            len(contratos_posibles),
                        )

            # Solo se acepta la relación correspondiente al tipo seleccionado y
            # únicamente si pertenece al titular autenticado de la cuenta.
            tipo_relacion = values_to_update.get('selecionar') or record.selecionar
            relaciones_post = {
                'contrato': relacion_fuv_id,
                'servicios': relacion_orden_compra_servicios_fuv_id,
                'bienes': relacion_orden_compra_bienes_fuv_id,
                'otrosi': relacion_otrosi_fuv_id,
            }
            configuracion_relaciones = {
                'contrato': ('relacion_fuv_id', 'contrataciones_fuv_relacion', 'documento_identidad'),
                'servicios': ('relacion_orden_compra_servicios_fuv_id', 'orden_compra_servicios', 'cedula_proveedor'),
                'bienes': ('relacion_orden_compra_bienes_fuv_id', 'orden_compra_bienes', 'cedula_proveedor'),
                'otrosi': ('relacion_otrosi_fuv_id', 'otrosi_bdd', 'documento_identidad'),
            }
            relacion_id = relaciones_post.get(tipo_relacion)
            configuracion = configuracion_relaciones.get(tipo_relacion)
            if record.origen_cuenta != 'regalias' and relacion_id and configuracion:
                field_name, model_name, natural_document_field = configuracion
                relacion_actualizada = request.env[model_name].sudo().browse(relacion_id).exists()
                titular_login = record.login_id
                titular_documento = (
                    titular_login.documento_de_identidad
                    if titular_login else record.documento_de_identificacion
                )
                titular_tipo_persona = (
                    titular_login.clase_persona if titular_login else record.clase_persona
                )
                if not self._portal_relation_belongs_to_owner(
                    relacion_actualizada,
                    titular_documento,
                    titular_tipo_persona,
                    natural_document_field,
                ):
                    return _portal_bad_request({
                        'message': 'La relación seleccionada no es válida para esta cuenta de cobro.'
                    })
                values_to_update[field_name] = relacion_id

            tipo_para_desembolso = values_to_update.get('selecionar') or record.selecionar
            if (
                not datos_financieros_sin_cambios
                and record.origen_cuenta != 'regalias'
                and tipo_para_desembolso in ('contrato', 'otrosi')
            ):
                if tipo_para_desembolso == 'contrato':
                    relacion_validar = request.env['contrataciones_fuv_relacion'].sudo().browse(
                        int(values_to_update.get('relacion_fuv_id') or record.relacion_fuv_id.id or 0)
                    ).exists()
                else:
                    relacion_validar = request.env['otrosi_bdd'].sudo().browse(
                        int(values_to_update.get('relacion_otrosi_fuv_id') or record.relacion_otrosi_fuv_id.id or 0)
                    ).exists()
                try:
                    pago_validar = int(
                        values_to_update.get('pago_forma_pago')
                        or record.pago_forma_pago
                        or values_to_update.get('pago')
                        or record.pago
                        or 0
                    )
                except (TypeError, ValueError):
                    pago_validar = 0
                valor_validar = values_to_update.get('valor_cuenta') or 0.0
                forma_pago_otrosi = []
                if tipo_para_desembolso == 'contrato':
                    detalle_pago = request.env['cuentas_de_cobros'].sudo()._detalle_disponible_pago_contrato_fuv(
                        relacion_validar,
                        pago_validar,
                        identificacion=values_to_update.get('documento_de_identificacion') or record.documento_de_identificacion,
                        nombre=values_to_update.get('nombre_contratista') or record.nombre_contratista or record.name,
                        exclude_id=record.id,
                    )
                else:
                    forma_pago_otrosi = request.env['cuentas_de_cobros'].sudo()._forma_pago_otrosi_fuv(
                        relacion_validar,
                        exclude_id=record.id,
                    )
                    detalle_pago = next((
                        item for item in forma_pago_otrosi
                        if item['numero_de_pagos'] == pago_validar
                    ), False)
                otrosi_pago_manual = tipo_para_desembolso == 'otrosi' and not detalle_pago
                if not detalle_pago and not otrosi_pago_manual:
                    return _portal_bad_request({
                        'message': 'Selecciona el desembolso que vas a cobrar en la tabla de Forma de pago.'
                    })
                if detalle_pago and detalle_pago['valor_disponible'] <= 0:
                    return _portal_bad_request({
                        'message': 'El desembolso seleccionado ya está cubierto por pagos físicos o cuentas registradas en SGO.'
                    })
                if detalle_pago and valor_validar > detalle_pago['valor_disponible']:
                    return _portal_bad_request({
                        'message': 'El valor a cobrar no puede superar el saldo disponible del desembolso seleccionado ($ {:,.2f}).'.format(
                            detalle_pago['valor_disponible'] or 0.0
                        )
                    })

            if not datos_financieros_sin_cambios and record.origen_cuenta != 'regalias':
                tipo_validar = values_to_update.get('selecionar') or record.selecionar
                mapa_relaciones = {
                    'contrato': (
                        'relacion_fuv_id',
                        'contrataciones_fuv_relacion',
                        lambda rel: rel.valor_total_contrato or 0.0,
                    ),
                    'servicios': (
                        'relacion_orden_compra_servicios_fuv_id',
                        'orden_compra_servicios',
                        lambda rel: rel.total or 0.0,
                    ),
                    'bienes': (
                        'relacion_orden_compra_bienes_fuv_id',
                        'orden_compra_bienes',
                        lambda rel: rel.total or rel.total_poliza or 0.0,
                    ),
                    'otrosi': (
                        'relacion_otrosi_fuv_id',
                        'otrosi_bdd',
                        lambda rel: rel.valor_total_contrato or 0.0,
                    ),
                }
                config_relacion = mapa_relaciones.get(tipo_validar)
                if config_relacion:
                    campo_relacion, modelo_relacion, obtener_tope = config_relacion
                    relacion_id_validar = values_to_update.get(campo_relacion) or getattr(record, campo_relacion).id
                    relacion_validar = request.env[modelo_relacion].sudo().browse(
                        int(relacion_id_validar or 0)
                    ).exists()
                    valor_validar = values_to_update.get('valor_cuenta') or 0.0
                    valor_tope_total = obtener_tope(relacion_validar) if relacion_validar else 0.0
                    if relacion_validar and valor_tope_total > 0:
                        domain_saldo = [
                            ('origen_cuenta', '!=', 'regalias'),
                            ('selecionar', '=', tipo_validar),
                            (campo_relacion, '=', relacion_validar.id),
                            ('contratista_id.estado', '!=', 'rechazado'),
                            ('id', '!=', record.id),
                        ]
                        total_acumulado_sgo = sum(
                            request.env['cuentas_de_cobros'].sudo().search(domain_saldo).mapped('valor_cuenta')
                        )
                        total_fisico = 0.0
                        if tipo_validar == 'contrato':
                            total_fisico = request.env['cuentas_de_cobros'].sudo()._detalle_pagos_fisicos_contrato_fuv(
                                relacion_validar,
                                identificacion=values_to_update.get('documento_de_identificacion') or record.documento_de_identificacion,
                                nombre=values_to_update.get('nombre_contratista') or record.nombre_contratista or record.name,
                            )[2]
                            if total_fisico > 0:
                                total_acumulado_sgo = 0.0
                        saldo_disponible_total = max(valor_tope_total - total_acumulado_sgo - total_fisico, 0.0)
                        if valor_validar > saldo_disponible_total:
                            return _portal_bad_request({
                                'message': 'El valor a cobrar no puede superar el saldo disponible ($ {:,.2f}).'.format(
                                    saldo_disponible_total or 0.0
                                )
                            })

            # Defensa final: estos campos nunca son modificables por el portal,
            # aunque se agreguen manualmente al POST o cambie el HTML.
            campos_inmutables_portal = {
                'origen_cuenta',
                'name',
                'nombre_contratista',
                'clase_persona',
                'correo_facturacion',
                'fecha_solicitud',
                'documento_de_identificacion',
                'selecionar',
                'contratacion_fuv_id',
                'proyecto_regalias_id',
                'contrato_regalias_id',
                'relacion_fuv_id',
                'relacion_orden_compra_servicios_fuv_id',
                'relacion_orden_compra_bienes_fuv_id',
                'relacion_otrosi_fuv_id',
                'valor_total_contrato',
                'total_relacionado',
                'porcentaje_de_pago',
                'porcentaje_de_pago_individual',
                'de',
                'bloqueo',
            }
            for campo in campos_inmutables_portal:
                values_to_update.pop(campo, None)

            # Los vínculos a documentos no se reciben como IDs confiables del
            # navegador. Se actualizan únicamente después de cargar y verificar
            # el archivo correspondiente; un campo HTML ausente no puede borrar
            # por accidente el documento anterior.
            campos_documentales_portal = set(self.PORTAL_REPLACEMENT_FIELDS) | {
                'documento_equivalente_id',
                'informe_de_actividades_id',
                'declaracion_juramentada_y_soportes_id',
            }
            for campo in campos_documentales_portal:
                values_to_update.pop(campo, None)

            # Las marcas ``*_corregir`` las controla el equipo interno y no el
            # formulario público. Un checkbox ausente llega como False y antes
            # borraba la marca justo antes de intentar reemplazar el archivo,
            # haciendo que la carga fuera ignorada silenciosamente.
            for campo in list(values_to_update):
                if campo.endswith('_corregir'):
                    values_to_update.pop(campo, None)

            # 1. EJECUCIÓN DEL WRITE
            portal_login = self._portal_login_for_chatter(record)
            self._portal_write(
                record,
                values_to_update,
                login=portal_login,
                extra_context={
                    'portal_correction_financial_values_unchanged': datos_financieros_sin_cambios,
                },
            )
            _logger.info("SAVE: Se ejecutó el write de la cuenta %s", record_id)

            if (
                factura_switch_present
                and not factura_si_o_no
                and correction_request_active
                and record.contratista_id
                and record.contratista_id.factura_id
            ):
                # Se conserva el archivo físico en Drive, pero deja de estar
                # asociado como factura de esta cuenta. Así la plataforma puede
                # volver a exigir/generar Documento Equivalente.
                record.contratista_id.sudo().with_context(
                    portal_chatter_login_id=portal_login.id if portal_login else False,
                    skip_drive_sync=True,
                ).write({'factura_id': False})

            changed_correction_labels = (
                self._portal_changed_correction_labels(
                    correction_before_snapshot,
                    self._portal_correction_snapshot(record),
                )
                if correction_request_active else []
            )

            _upload_portal_acta_documents(
                record.contratista_id,
                'informes_contratista',
                _portal_uploaded_files(
                    files,
                    'informes_contratista_natural_doc',
                    'informes_contratista_juridica_doc',
                ),
                login=portal_login,
            )
            documentos_adicionales_subidos = _portal_uploaded_files(
                files, 'otro_documentos_de_inf_doc'
            )
            acta_documentos_adicionales = record.contratista_id

            # En corrección, si ya existía un Documento adicional y fue marcado
            # para corregir, el primer archivo cargado debe reemplazar el documento
            # principal y dejar trazabilidad como reemplazo. Los archivos extra
            # seleccionados en la misma carga se conservan como adicionales.
            if (
                correction_request_active
                and acta_documentos_adicionales
                and acta_documentos_adicionales.otro_documentos_de_inf_corregir
                and acta_documentos_adicionales.otro_documentos_de_inf_id
                and documentos_adicionales_subidos
            ):
                archivo_reemplazo = documentos_adicionales_subidos[0]
                if self._upload_portal_replacement_file(
                    record,
                    'otro_documentos_de_inf_id',
                    archivo_reemplazo,
                ):
                    replaced_labels.append('Documento adicional')
                documentos_adicionales_subidos = documentos_adicionales_subidos[1:]

            _upload_portal_acta_documents(
                acta_documentos_adicionales,
                'otro_documentos_de_informacion',
                documentos_adicionales_subidos,
                login=portal_login,
            )
            _upload_portal_acta_documents(
                record.contratista_id,
                'cesion_derechos',
                cesion_derechos_files,
                login=portal_login,
            )

            if correction_mode:
                for field_name, label in self.PORTAL_REPLACEMENT_FIELDS.items():
                    uploaded_file = request.httprequest.files.get(field_name + '_replace_file')
                    if self._upload_portal_replacement_file(record, field_name, uploaded_file):
                        replaced_labels.append(label)

            if not cuenta_ya_radicada:
                for field_name in self.PORTAL_REPLACEMENT_FIELDS:
                    uploaded_file = request.httprequest.files.get(field_name + '_replace_file')
                    self._upload_portal_replacement_file(record, field_name, uploaded_file)

            acta_cesion = record.contratista_id
            if cesion_derechos_marcada and acta_cesion:
                acta_cesion.invalidate_recordset(['cesion_derechos_id'])
                if not acta_cesion.cesion_derechos_id:
                    return _portal_bad_request({
                        'message': 'Debe adjuntar el documento de cesión de derechos.'
                    })
                self._portal_write(
                    acta_cesion,
                    {'cesion_derechos': True},
                    login=portal_login,
                    extra_context={'allow_cesion_derechos_portal': True},
                )
                acta_cesion.sudo().with_context(
                    portal_chatter_login_id=portal_login.id if portal_login else False,
                    allow_cesion_derechos_portal=True,
                ).iniciar_flujo_cesion_derechos()

            # 2. BLOQUEAR ACTA solo si el usuario hizo clic en "Finalizar y enviar"
            if post.get('finalizar') == '1':
                acta = record.contratista_id
                if acta:
                    if correction_request_active and record.opcion_otro_documento:
                        documentos_adicionales = acta.sudo()._get_portal_acta_document_records(
                            'otro_documentos_de_informacion',
                            'otro_documentos_de_inf_id',
                        )
                        if not documentos_adicionales:
                            return _portal_bad_request({
                                'message': (
                                    'Marcó Documentos adicionales. Debe adjuntar al menos un documento adicional '
                                    'antes de finalizar y enviar la corrección.'
                                )
                            })
                    if record.clase_persona == 'persona_natural':
                        factura_natural_registrada = bool(
                            record.factura_si_o_no
                            or record.factura_id
                            or (
                                record.numero_factura
                                and record.numero_factura.strip()
                                and record.numero_factura.strip().upper() not in ('N/A', 'NA')
                            )
                        )
                        documento_declaracion = (
                            acta.declaracion_juramentada_y_soportes_id
                            if record.selecionar in ('servicios', 'bienes')
                            else acta.documento_equivalente_id
                        )
                        documentos_pendientes = []
                        if not factura_natural_registrada and not documento_declaracion:
                            documentos_pendientes.append(
                                'el documento equivalente y la declaración juramentada'
                            )
                        if not acta.informe_de_actividades_id:
                            documentos_pendientes.append('el informe de actividades')
                    else:
                        documentos_pendientes = []

                    if documentos_pendientes:
                        return _portal_bad_request({
                            'message': 'Debe completar %s antes de finalizar y enviar la cuenta de cobro.' % (
                                ' y '.join(documentos_pendientes)
                            )
                        })
                    if correction_request_active:
                        self._reconcile_verified_portal_replacements(record)
                        acta.invalidate_recordset()
                        documentos_sin_corregir = acta._documentos_marcados_para_correccion()
                        fechas_corregidas = any(
                            label.startswith(('Inicio de actividad:', 'Finalización de actividad:'))
                            for label in changed_correction_labels
                        )
                        if acta.periodo_actividad_corregir and fechas_corregidas:
                            documentos_sin_corregir = [
                                label for label in documentos_sin_corregir
                                if label != 'Fechas de inicio y finalización de actividad'
                            ]
                        if documentos_sin_corregir:
                            return _portal_bad_request({
                                'message': (
                                    'Aún debe corregir o volver a generar: %s. '
                                    'La cuenta continuará en estado Corrigiendo.'
                                ) % ', '.join(documentos_sin_corregir)
                            })
                        if not replaced_labels:
                            replaced_labels = pending_correction_labels
                        correction_labels = list(dict.fromkeys(
                            (replaced_labels or []) + changed_correction_labels
                        ))
                        self._notify_assistant_corrections_finished(record, correction_labels)
                        acta.limpiar_correccion_portal_finalizada()
                    else:
                        self._portal_write(acta, {'bloqueo': False}, login=self._portal_login_for_chatter(record))
                        acta.sudo().notify_contract_assistant()
                    if not acta.notificacion_enviada:
                        self._portal_write(
                            acta,
                            {'notificacion_enviada': True},
                            login=self._portal_login_for_chatter(record),
                        )
                    _logger.info("Acta %s bloqueada tras Finalizar y enviar", acta.id)

            # Buscar login para redirigir
            login = request.env['login'].sudo().search([
                ('documento_de_identidad', '=', record.documento_de_identificacion)
            ], limit=1)

            if login:
                return request.redirect('/public/cuentas_de_cobros/listado/%s/%s' % (login.id, login.token))
            else:
                return request.redirect('/public/login')

        return request.redirect('/public/login')


    @http.route('/public/cuentas_de_cobros/create', type='http', auth='public', methods=['POST'], website=True)
    def public_create(self, **post):
        def _parse_portal_money(value):
            if value in (None, False):
                return 0.0
            text = str(value).strip().replace(' ', '')
            if not text:
                return 0.0
            if ',' in text and '.' in text:
                if text.rfind(',') > text.rfind('.'):
                    text = text.replace('.', '').replace(',', '.')
                else:
                    text = text.replace(',', '')
            elif ',' in text:
                text = text.replace(',', '.')
            try:
                return float(text)
            except (TypeError, ValueError):
                return 0.0

        def _redirect_to_create_error(error_code='tope_excedido'):
            url_origen = request.httprequest.referrer or '/'
            separador = '&' if '?' in url_origen else '?'
            return request.redirect(f"{url_origen}{separador}alerta_elegante={error_code}")

        try:
            login_id = int(post.get('login_id'))
        except (TypeError, ValueError):
            return request.not_found()
        token = post.get('token')

        login = request.env['login'].sudo().browse(login_id)
        if not login or login.token != token:
            return request.not_found()

        campo_fecha_invalida = _portal_error_fecha_actividad(post)
        if campo_fecha_invalida:
            return request.redirect(
                '/public/cuentas_de_cobros/new/%s/%s?fecha_error=%s#%s' % (
                    login.id,
                    login.token,
                    campo_fecha_invalida,
                    campo_fecha_invalida,
                )
            )

        puede_crear_fuv, _mensaje_bloqueo_fuv = login.estado_creacion_cuenta_cobro_portal()
        puede_crear_regalias, _mensaje_bloqueo_regalias = login.estado_creacion_cuenta_regalias_portal()
        if not puede_crear_fuv and not puede_crear_regalias:
            return request.redirect(
                '/public/cuentas_de_cobros/listado/%s/%s?creation_blocked=1' % (login.id, login.token)
            )

        user_email = login.coreo
        record_token = str(uuid.uuid4())
        files = request.httprequest.files
        cesion_derechos_marcada = post.get('cesion_derechos') in (
            True, '1', 'true', 'True', 'on', 'yes', 'si'
        )
        cesion_derechos_files = _portal_uploaded_files(files, 'cesion_derechos_doc')
        if cesion_derechos_files:
            cesion_derechos_marcada = True
        if cesion_derechos_marcada and not cesion_derechos_files:
            return _portal_bad_request({
                'message': 'Debe adjuntar el documento de cesión de derechos.'
            })

        # Limpiar y convertir los valores monetarios a Float para poder sumarlos
        nuevo_valor_cuenta = _parse_portal_money(post.get('valor_cuenta', '0'))
        valor_total_contrato = _parse_portal_money(post.get('valor_total_contrato', '0'))
        valor_tope_cuenta = valor_total_contrato

        origen_cuenta = post.get('origen_cuenta') or 'fuv'
        regalias_enabled, regalias_disabled_message = login.estado_creacion_cuenta_regalias_portal()
        if origen_cuenta == 'regalias' and not regalias_enabled:
            raise AccessError(
                regalias_disabled_message
                or 'La creación de cuentas de Regalías no está habilitada en este momento.'
            )
        if origen_cuenta not in ('fuv', 'regalias'):
            origen_cuenta = 'fuv'
        if origen_cuenta != 'regalias' and not puede_crear_fuv:
            return request.redirect(
                '/public/cuentas_de_cobros/listado/%s/%s?creation_blocked=1' % (login.id, login.token)
            )

        seleccionar = post.get('selecionar')
        if origen_cuenta != 'regalias' and seleccionar not in ('contrato', 'servicios', 'bienes', 'otrosi'):
            return _portal_bad_request({
                'message': 'Debe seleccionar el tipo de contrato antes de crear la cuenta de cobro.'
            })

        try:
            pago_numero = int(post.get('pago') or 0)
        except (TypeError, ValueError):
            pago_numero = 0
        try:
            pago_forma_pago_numero = int(post.get('pago_forma_pago') or 0)
        except (TypeError, ValueError):
            pago_forma_pago_numero = 0
        if pago_numero < 1:
            return _portal_bad_request({
                'message': 'El Pago N° debe ser mayor a cero.'
            })

        if origen_cuenta != 'regalias':
            relacion_por_tipo = {
                'contrato': post.get('relacion_fuv_id'),
                'servicios': post.get('relacion_orden_compra_servicios_fuv_id'),
                'bienes': post.get('relacion_orden_compra_bienes_fuv_id'),
                'otrosi': post.get('relacion_otrosi_fuv_id'),
            }
            relacion_seleccionada = relacion_por_tipo.get(seleccionar)
            if not relacion_seleccionada or not str(relacion_seleccionada).isdigit() or int(relacion_seleccionada) <= 0:
                return _portal_bad_request({
                    'message': 'Debe seleccionar el contrato, orden u otrosí antes de crear la cuenta de cobro.'
                })

        factura_si_o_no = (
            post.get('factura_si_o_no') in (True, '1', 'true', 'True', 'on', 'yes', 'si')
            or bool(files.get('factura_doc'))
            or bool((post.get('numero_factura') or '').strip() and (post.get('numero_factura') or '').strip().upper() not in ('N/A', 'NA'))
        )
        tipo_persona_cuenta = login.clase_persona
        certificado_bancario_file = (
            files.get('certificacion_bancaria_juridica_documento')
            if tipo_persona_cuenta == 'persona_juridica'
            else files.get('certificacion_bancaria_doc')
        )
        if not certificado_bancario_file or not certificado_bancario_file.filename:
            return _portal_bad_request({
                'message': 'Debe adjuntar el certificado bancario actualizado para crear la cuenta de cobro.'
            })

        values_to_create = {
            'origen_cuenta': origen_cuenta,
            'nombre_contratista': login.name,
            'clase_persona': login.clase_persona,
            'selecionar': post.get('selecionar'),
            'numero_factura': post.get('numero_factura'),
            'correo_facturacion': user_email,
            'fecha_solicitud': fields.Date.context_today(
                login.with_context(tz='America/Bogota')
            ),
            'documento_de_identificacion': login.documento_de_identidad,
            'valor_cuenta': nuevo_valor_cuenta, # Usamos el valor numérico limpio
            'pago': pago_numero,
            'pago_forma_pago': pago_forma_pago_numero or False,
            'inicio_actividad': post.get('inicio_actividad') or False,
            'finalizacion_actividad': post.get('finalizacion_actividad') or False,
            'nombre_entrega': post.get('nombre_entrega'),
            'token': record_token,
            'factura_si_o_no': factura_si_o_no,
            'opcion_otro_documento': post.get('opcion_otro_documento'),
            'login_id': login_id,
        }

        # Selección de relación única y construcción del dominio de búsqueda
        domain = []
        if values_to_create['origen_cuenta'] == 'regalias':
            proyecto_id = post.get('proyecto_regalias_id')
            contrato_regalias_id = post.get('contrato_regalias_id')
            if not proyecto_id or not str(proyecto_id).isdigit():
                return _redirect_to_create_error('proyecto_regalias_requerido')
            if not contrato_regalias_id or not str(contrato_regalias_id).isdigit():
                return _redirect_to_create_error('contrato_regalias_requerido')
            proyecto = request.env['informacion_base_agregar'].sudo().browse(
                int(proyecto_id) if proyecto_id and proyecto_id.isdigit() else 0
            ).exists()
            if not proyecto or proyecto.area_solicitante2 != 'regalias':
                raise ValidationError('Selecciona un proyecto válido de Regalías antes de seleccionar el contrato.')

            contratacion_regalias = proyecto.contratacion_id or (
                proyecto.mcb_ids[:1].contrataciones_id if proyecto.mcb_ids else False
            )
            contrato_regalias = request.env['base.datos.regalias.contrato'].sudo().browse(
                int(contrato_regalias_id) if contrato_regalias_id and contrato_regalias_id.isdigit() else 0
            ).exists()
            if not contrato_regalias:
                raise ValidationError('Selecciona un contrato de Regalías válido.')
            identificacion_regalias = login.documento_de_identidad
            contrato_identificacion_key = re.sub(r'\D+', '', contrato_regalias.identificacion or '')
            login_identificacion_key = re.sub(r'\D+', '', identificacion_regalias or '')
            if contrato_identificacion_key != login_identificacion_key:
                tipo_identificacion_error = 'NIT' if login.clase_persona == 'persona_juridica' else 'documento de identidad'
                raise ValidationError('El contrato de Regalías seleccionado no corresponde a tu %s.' % tipo_identificacion_error)
            proyecto_unidad_valor = self._portal_regalias_centro_costo_proyecto(proyecto)
            if not proyecto_unidad_valor:
                raise ValidationError(
                    'El proyecto de Regalías no tiene unidad de negocio configurada en Información Base.'
                )
            proyecto_unidad_key = self._portal_regalias_key(proyecto_unidad_valor)
            contrato_unidad_key = self._portal_regalias_key(contrato_regalias.un)
            if not contrato_unidad_key or not proyecto_unidad_key or contrato_unidad_key != proyecto_unidad_key:
                raise ValidationError('El contrato seleccionado no corresponde a la unidad de negocio del proyecto de Regalías elegido.')
            self._portal_serialize_contractual_relation(contrato_regalias)
            valor_contrato_regalias = _parse_portal_money(
                contrato_regalias.valor_de_contrato_vigencia_2025_2026
            )
            saldo_contrato_regalias = _parse_portal_money(
                contrato_regalias.saldo_por_utilizar
            )
            saldo_disponible_regalias = request.env[
                'cuentas_de_cobros'
            ].sudo()._saldo_disponible_contrato_regalias(contrato_regalias)
            if saldo_disponible_regalias <= 0:
                return _redirect_to_create_error('saldo_regalias_agotado')
            valor_base_porcentaje_regalias = valor_contrato_regalias or saldo_contrato_regalias
            values_to_create.update({
                'proyecto_regalias_id': proyecto.id,
                'contrato_regalias_id': contrato_regalias.id,
                'selecionar': 'regalias',
                'valor_total_contrato': valor_base_porcentaje_regalias,
                'porcentaje_de_pago': (
                    nuevo_valor_cuenta / valor_base_porcentaje_regalias * 100.0
                    if valor_base_porcentaje_regalias else 0.0
                ),
            })
            valor_total_contrato = values_to_create['valor_total_contrato']
            valor_tope_cuenta = saldo_disponible_regalias
            domain = [('contrato_regalias_id', '=', contrato_regalias.id)]
        elif seleccionar == 'contrato':
            rel_id = int(post.get('relacion_fuv_id'))
            relacion = request.env['contrataciones_fuv_relacion'].sudo().browse(rel_id).exists()
            if not self._portal_relation_belongs_to_owner(
                relacion,
                login.documento_de_identidad,
                login.clase_persona,
                'documento_identidad',
            ):
                return _portal_bad_request({'message': 'El contrato seleccionado no es válido.'})
            self._portal_serialize_contractual_relation(relacion)
            detalle_pago = request.env['cuentas_de_cobros'].sudo()._detalle_disponible_pago_contrato_fuv(
                relacion,
                pago_forma_pago_numero or pago_numero,
                identificacion=login.documento_de_identidad,
                nombre=login.name,
            )
            if not detalle_pago:
                return _portal_bad_request({
                    'message': 'Selecciona el desembolso que vas a cobrar en la tabla de Forma de pago.'
                })
            if detalle_pago['valor_disponible'] <= 0:
                return _portal_bad_request({
                    'message': 'El desembolso seleccionado ya está cubierto por pagos físicos o cuentas registradas en SGO.'
                })
            values_to_create['relacion_fuv_id'] = rel_id
            valor_total_contrato = relacion.valor_total_contrato or 0.0
            valor_tope_cuenta = min(valor_total_contrato, detalle_pago['valor_disponible'])
            values_to_create['valor_total_contrato'] = valor_total_contrato
            values_to_create['valor_cuenta'] = nuevo_valor_cuenta
            domain = [('relacion_fuv_id', '=', rel_id)]
            
        elif seleccionar == 'servicios':
            rel_id = int(post.get('relacion_orden_compra_servicios_fuv_id'))
            relacion = request.env['orden_compra_servicios'].sudo().browse(rel_id).exists()
            if not self._portal_relation_belongs_to_owner(
                relacion,
                login.documento_de_identidad,
                login.clase_persona,
                'cedula_proveedor',
            ):
                return _portal_bad_request({'message': 'La orden de servicios seleccionada no es válida.'})
            self._portal_serialize_contractual_relation(relacion)
            values_to_create['relacion_orden_compra_servicios_fuv_id'] = rel_id
            valor_total_contrato = relacion.total or 0.0
            valor_tope_cuenta = valor_total_contrato
            values_to_create['valor_total_contrato'] = valor_total_contrato
            domain = [('relacion_orden_compra_servicios_fuv_id', '=', rel_id)]
            
        elif seleccionar == 'bienes':
            rel_id = int(post.get('relacion_orden_compra_bienes_fuv_id'))
            relacion = request.env['orden_compra_bienes'].sudo().browse(rel_id).exists()
            if not self._portal_relation_belongs_to_owner(
                relacion,
                login.documento_de_identidad,
                login.clase_persona,
                'cedula_proveedor',
            ):
                return _portal_bad_request({'message': 'La orden de bienes seleccionada no es válida.'})
            self._portal_serialize_contractual_relation(relacion)
            values_to_create['relacion_orden_compra_bienes_fuv_id'] = rel_id
            valor_total_contrato = (relacion.total or relacion.total_poliza or 0.0)
            valor_tope_cuenta = valor_total_contrato
            values_to_create['valor_total_contrato'] = valor_total_contrato
            domain = [('relacion_orden_compra_bienes_fuv_id', '=', rel_id)]
            
        elif seleccionar == 'otrosi':
            rel_id = int(post.get('relacion_otrosi_fuv_id'))
            relacion = request.env['otrosi_bdd'].sudo().browse(rel_id).exists()
            if not self._portal_relation_belongs_to_owner(
                relacion,
                login.documento_de_identidad,
                login.clase_persona,
                'documento_identidad',
            ):
                return _portal_bad_request({'message': 'El otrosí seleccionado no es válido.'})
            self._portal_serialize_contractual_relation(relacion)
            forma_pago_otrosi = request.env['cuentas_de_cobros'].sudo()._forma_pago_otrosi_fuv(
                relacion,
            )
            detalle_pago = next((
                item for item in forma_pago_otrosi
                if item['numero_de_pagos'] == (pago_forma_pago_numero or pago_numero)
            ), False)
            _logger.info(
                '[CUENTA_COBRO_OTROSI] etapa=seleccion_desembolso '
                'login_id=%s otrosi_id=%s pago=%s valor_solicitado=%.2f '
                'valor_total_otrosi=%.2f detalle_encontrado=%s '
                'valor_cuota=%.2f cobrado_sgo_cuota=%.2f disponible_cuota=%.2f',
                login.id,
                rel_id,
                pago_numero,
                nuevo_valor_cuenta,
                relacion.valor_total_contrato or 0.0,
                bool(detalle_pago),
                detalle_pago['cantidad_de_dinero'] if detalle_pago else 0.0,
                detalle_pago['valor_cobrado_sgo'] if detalle_pago else 0.0,
                detalle_pago['valor_disponible'] if detalle_pago else 0.0,
            )
            if detalle_pago and detalle_pago['valor_disponible'] <= 0:
                _logger.warning(
                    '[CUENTA_COBRO_OTROSI] resultado=rechazado '
                    'motivo=desembolso_sin_saldo login_id=%s otrosi_id=%s pago=%s '
                    'valor_solicitado=%.2f disponible_cuota=%.2f',
                    login.id,
                    rel_id,
                    pago_numero,
                    nuevo_valor_cuenta,
                    detalle_pago['valor_disponible'],
                )
                return _portal_bad_request({
                    'message': 'El desembolso seleccionado del otrosí ya está cubierto por una cuenta registrada en SGO.'
                })
            values_to_create['relacion_otrosi_fuv_id'] = rel_id
            valor_total_contrato = relacion.valor_total_contrato or 0.0
            valor_tope_cuenta = (
                min(valor_total_contrato, detalle_pago['valor_disponible'])
                if detalle_pago else valor_total_contrato
            )
            values_to_create['valor_total_contrato'] = valor_total_contrato
            domain = [('relacion_otrosi_fuv_id', '=', rel_id)]

        if nuevo_valor_cuenta <= 0 or (valor_tope_cuenta > 0 and nuevo_valor_cuenta > valor_tope_cuenta):
            _logger.warning(
                '[CUENTA_COBRO_TOPE] resultado=rechazado motivo=valor_fuera_tope '
                'login_id=%s tipo=%s relacion_id=%s pago=%s '
                'valor_solicitado=%.2f valor_total=%.2f valor_tope=%.2f',
                login.id,
                values_to_create.get('selecionar'),
                rel_id if 'rel_id' in locals() else 0,
                pago_numero,
                nuevo_valor_cuenta,
                valor_total_contrato,
                valor_tope_cuenta,
            )
            return _redirect_to_create_error()

        # --- INICIO: NUEVA LÓGICA DE VALIDACIÓN DE TOPES ---
        if domain and values_to_create['origen_cuenta'] != 'regalias':
            domain.append(('contratista_id.estado', '!=', 'rechazado'))
            # Buscar cuentas de cobro ya existentes en la base de datos para esta relación
            registros_existentes = request.env['cuentas_de_cobros'].sudo().search(domain)
            
            # Sumar el campo 'valor_cuenta' de todos los registros que ya existan
            total_acumulado = sum(registros_existentes.mapped('valor_cuenta'))
            total_fisico = 0.0
            if values_to_create.get('selecionar') == 'contrato' and values_to_create.get('relacion_fuv_id'):
                relacion_fisica = request.env['contrataciones_fuv_relacion'].sudo().browse(
                    int(values_to_create.get('relacion_fuv_id'))
                ).exists()
                total_fisico = request.env['cuentas_de_cobros'].sudo()._detalle_pagos_fisicos_contrato_fuv(
                    relacion_fisica,
                    identificacion=login.documento_de_identidad,
                    nombre=login.name,
                )[2]
                if total_fisico > 0:
                    total_acumulado = 0.0

            # El límite individual del desembolso ya se validó arriba. Para el
            # acumulado del contrato/otrosí se debe usar el valor total de la
            # relación; usar el saldo de una sola cuota en otrosí contabilizaba
            # otra vez los pagos anteriores y generaba falsos rechazos.
            tipo_relacion_tope = values_to_create.get('selecionar')
            valor_tope_total = (
                valor_total_contrato
                if tipo_relacion_tope in ('contrato', 'otrosi')
                else valor_tope_cuenta
            )
            total_evaluado = total_fisico + total_acumulado + nuevo_valor_cuenta
            _logger.info(
                '[CUENTA_COBRO_TOPE] etapa=validacion_acumulada '
                'login_id=%s tipo=%s relacion_id=%s pago=%s '
                'cuentas_previas=%s ids_previos=%s total_fisico=%.2f '
                'total_sgo_previo=%.2f valor_solicitado=%.2f '
                'total_evaluado=%.2f valor_tope=%.2f',
                login.id,
                values_to_create.get('selecionar'),
                rel_id if 'rel_id' in locals() else 0,
                pago_numero,
                len(registros_existentes),
                registros_existentes.ids,
                total_fisico,
                total_acumulado,
                nuevo_valor_cuenta,
                total_evaluado,
                valor_tope_total,
            )
            if total_evaluado > valor_tope_total:
                _logger.warning(
                    '[CUENTA_COBRO_TOPE] resultado=rechazado motivo=acumulado_supera_tope '
                    'login_id=%s tipo=%s relacion_id=%s pago=%s '
                    'total_fisico=%.2f total_sgo_previo=%.2f '
                    'valor_solicitado=%.2f total_evaluado=%.2f valor_tope=%.2f',
                    login.id,
                    values_to_create.get('selecionar'),
                    rel_id if 'rel_id' in locals() else 0,
                    pago_numero,
                    total_fisico,
                    total_acumulado,
                    nuevo_valor_cuenta,
                    total_evaluado,
                    valor_tope_total,
                )
                return _redirect_to_create_error()
        # --- FIN DE LÓGICA DE VALIDACIÓN ---

        # Los totales y porcentajes son campos calculados a partir de la
        # relación validada. Nunca deben persistirse desde datos del cliente.
        values_to_create.pop('valor_total_contrato', None)
        values_to_create.pop('porcentaje_de_pago', None)
        values_to_create.pop('porcentaje_de_pago_individual', None)
        values_to_create.pop('total_relacionado', None)

        # (Continúa el código procesando los archivos)
        file_fields = [
            'certificacion_conjunta_doc',
            'rut_actualizado_doc', 'planilla_de_aportes_a_seguridad_social_doc', 'copia_contrato_doc',
            'certificacion_bancaria_doc', 'declaracion_juramentada_y_soportes_doc', 'anexo_tributario_doc',
            'certificado_de_expensa_doc', 'resolucion_facturacion_electronica_doc',
            'copia_cedula_doc', 'registro_de_proveedores_doc',
            'factura_electronica_juridica_documento_doc', 'rut_actualizado_juridica_documento',
            'resolucion_facturacion_electronica_juridica_documento', 'camara_comercio_juridica_documento',
            'certificado_pago_parafiscales_juridica_documento', 'tarjeta_profesional_revisor_fiscal_juridica_documento',
            'cedula_de_contador_juridica_documento', 'cedula_representante_legal_juridica_documento',
            'certificado_junta_contadores_juridica_documento', 'rut_representante_legal_juridica_documento',
            'certificacion_bancaria_juridica_documento', 'cedula_revisor_fiscal_juridica_doc',
            'tarjeta_profesional_Contador_juridica_doc', 'planilla_de_seguridad_social_ju_doc',
            'factura_doc',
        ]

        for field in file_fields:
            file = files.get(field)
            if file:
                file_content = file.read()
                if file_content:
                    values_to_create[field] = base64.b64encode(file_content)
                    original_name = file.filename or ''
                    if original_name == field or not '.' in original_name:
                        mime_type = file.content_type
                        extension = mimetypes.guess_extension(mime_type) or '.bin'
                        timestamp = fields.Datetime.now().strftime('%Y%m%d_%H%M%S')
                        fixed_name = f"{field}_{timestamp}{extension}"
                    else:
                        fixed_name = original_name

                    values_to_create[f'{field}_name'] = fixed_name

        # Crear el registro en la base de datos conservando el usuario externo
        # en contexto para que la trazabilidad del acta/Drive no quede como Public user.
        create_since = fields.Datetime.now()
        try:
            record = request.env['cuentas_de_cobros'].sudo().with_context(
                portal_chatter_login_id=login.id
            ).create(values_to_create)
        except ValidationError as e:
            return _portal_bad_request({
                'message': e.args[0] if e.args else 'No se pudo crear la cuenta de cobro. Revise los datos requeridos.'
            })
        self._reassign_portal_chatter_author(record, login, create_since)
        if record.contratista_id:
            self._reassign_portal_chatter_author(record.contratista_id, login, create_since)

        _upload_portal_acta_documents(
            record.contratista_id,
            'informes_contratista',
            _portal_uploaded_files(
                files,
                'informes_contratista_natural_doc',
                'informes_contratista_juridica_doc',
            ),
            login=login,
        )
        _upload_portal_acta_documents(
            record.contratista_id,
            'otro_documentos_de_informacion',
            _portal_uploaded_files(files, 'otro_documentos_de_inf_doc'),
            login=login,
        )
        _upload_portal_acta_documents(
            record.contratista_id,
            'cesion_derechos',
            cesion_derechos_files,
            login=login,
        )
        if cesion_derechos_marcada and record.contratista_id:
            record.contratista_id.invalidate_recordset(['cesion_derechos_id'])
            if not record.contratista_id.cesion_derechos_id:
                return _portal_bad_request({
                    'message': 'Debe adjuntar el documento de cesión de derechos.'
                })
            self._portal_write(
                record.contratista_id,
                {'cesion_derechos': True},
                login=login,
                extra_context={'allow_cesion_derechos_portal': True},
            )
            record.contratista_id.sudo().with_context(
                portal_chatter_login_id=login.id,
                allow_cesion_derechos_portal=True,
            ).iniciar_flujo_cesion_derechos()

        # REDIRECCIÓN exitosa (Se mantiene la tuya original porque asumo que funciona bien)
        return request.redirect(f"/public/cuentas_de_cobros/form/{record.id}/{record.token}?success=1")

    @http.route('/public/obtener_lineas_bienes', type='json', auth='public', website=True)
    def obtener_lineas_bienes(self, orden_id=None, **kwargs):
        # Odoo recibe el orden_id desde el fetch de JavaScript
        if not orden_id:
            return {'lineas': []}
        
        try:
            # Buscamos las líneas cuyo orden_id sea el seleccionado
            # Usamos sudo() porque es una consulta pública desde el sitio web
            lineas_rec = request.env['orden.compra.linea'].sudo().search([
                ('orden_id', '=', int(orden_id))
            ])
            
            res_lineas = []
            for l in lineas_rec:
                res_lineas.append({
                    'descripcion': l.descripcion or '',
                    'cantidad': l.cantidad or 0,
                    'valor_unitario': l.valor_unitario or 0,
                    'valor_total': l.valor_total or 0,
                })
            return {'lineas': res_lineas}
        except Exception as e:
            return {'error': str(e), 'lineas': []}

    @http.route('/public/obtener_lineas_servicios', type='json', auth="public", methods=['POST'], website=True)
    def obtener_lineas_servicios(self, orden_id=None, **kw):
        if not orden_id:
            return {'lineas': []}
        
        # Buscamos la orden de servicios. Asegúrate de que el nombre del modelo sea el correcto.
        # Según tu snippet, el modelo de las líneas es 'orden_compra_servicios.linea'
        # por lo que asumiré que la orden es 'orden_compra_servicios'
        orden = request.env['orden_compra_servicios'].sudo().browse(int(orden_id))
        
        if not orden.exists():
            return {'lineas': []}
            
        lineas_data = []
        # Usamos 'lineas_ids' que es el campo que mencionas en tu snippet
        for linea in orden.lineas_ids:
            lineas_data.append({
                'descripcion': linea.descripcion or '',
                'valor_unitario': linea.valor_unitario or 0.0,
            })
            
        return {'lineas': lineas_data}

    # =================================================================================
    # RUTA: OBTENER PAGOS PARA LA TABLA DEL PORTAL (CONTRATO Y OTROSÍ)
    # =================================================================================
    @http.route(['/public/cuentas_de_cobros/obtener_pagos'], type='http', auth="public", website=True, csrf=False)
    def obtener_pagos_contrato(self, contrato_id=None, otrosi_id=None, **kw):
        """Devuelve en formato JSON las líneas de la tabla de pagos de un contrato o un otrosí."""
        try:
            if not contrato_id and not otrosi_id:
                return request.make_response(json.dumps({'error': 'No se recibió ID para buscar'}), headers=[('Content-Type', 'application/json')])
            
            registro = None
            
            # 1. ¿Es un contrato normal?
            if contrato_id:
                registro = request.env['contrataciones_fuv_relacion'].sudo().browse(int(contrato_id))
                if not registro.exists():
                    return request.make_response(json.dumps({'error': 'Contrato no encontrado'}), headers=[('Content-Type', 'application/json')])
            
            # 2. ¿Es un Otrosí?
            elif otrosi_id:
                registro = request.env['otrosi_bdd'].sudo().browse(int(otrosi_id))
                if not registro.exists():
                    return request.make_response(json.dumps({'error': 'Otrosí no encontrado'}), headers=[('Content-Type', 'application/json')])

            pagos_data = []

            lineas_pago = registro.tabla_de_forma_de_pago.sorted(
                key=lambda linea: (linea.numero_de_pagos or 0, linea.id)
            )
            if contrato_id:
                detalles_pago = request.env['cuentas_de_cobros'].sudo()._detalle_forma_pago_contrato_fuv(
                    registro,
                    identificacion=kw.get('identificacion'),
                    nombre=kw.get('nombre'),
                    exclude_id=int(kw.get('cuenta_id')) if str(kw.get('cuenta_id') or '').isdigit() else None,
                )
                for item in detalles_pago:
                    pagos_data.append({
                        'numero_de_pagos': item['numero_de_pagos'] or '',
                        'cantidad_de_dinero': item['cantidad_de_dinero'],
                        'porcentaje_pago': item['porcentaje_pago'],
                        'porcentaje_acumulado': item['porcentaje_acumulado'],
                        'tecnico': item['tecnico'],
                        'condicion': item['condicion'],
                        'valor_cobrado_fisico': item['valor_cobrado_fisico'],
                        'valor_cobrado_sgo': item['valor_cobrado_sgo'],
                        'valor_disponible': item['valor_disponible'],
                        'bloqueado': item['bloqueado'],
                    })
            else:
                detalles_pago = request.env['cuentas_de_cobros'].sudo()._forma_pago_otrosi_fuv(
                    registro,
                    exclude_id=int(kw.get('cuenta_id')) if str(kw.get('cuenta_id') or '').isdigit() else None,
                )
                for item in detalles_pago:
                    pagos_data.append({
                        'numero_de_pagos': item['numero_de_pagos'] or '',
                        'cantidad_de_dinero': item['cantidad_de_dinero'],
                        'porcentaje_pago': item['porcentaje_pago'],
                        'porcentaje_acumulado': item['porcentaje_acumulado'],
                        'tecnico': item['tecnico'],
                        'condicion': item['condicion'],
                        'valor_cobrado_fisico': item['valor_cobrado_fisico'],
                        'valor_cobrado_sgo': item['valor_cobrado_sgo'],
                        'valor_disponible': item['valor_disponible'],
                        'bloqueado': item['bloqueado'],
                    })

            return request.make_response(json.dumps({'pagos': pagos_data}), headers=[('Content-Type', 'application/json')])

        except Exception as e:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.error(f"Error al obtener pagos: {str(e)}")
            return request.make_response(json.dumps({'error': str(e)}), headers=[('Content-Type', 'application/json')])
    # =================================================================================
    @http.route('/public/cuentas_de_cobros/<int:record_id>/<string:token>', type='http', auth='public', website=True)
    def public_cuentas_de_cobros_view(self, record_id, token, **kwargs):
        record = request.env['cuentas_de_cobros'].sudo().browse(record_id)
        if record and record.token == token:
            login = record.login_id
            cuenta_creation_allowed = False
            cuenta_creation_message = 'No se encontró el usuario del portal asociado a esta cuenta.'
            if login:
                fuv_allowed, fuv_message = login.estado_creacion_cuenta_cobro_portal()
                # El botón general "Nueva Cuenta" corresponde solo a cuentas FUV.
                # Regalías tiene su propia ventana y no debe mantener habilitado este botón.
                cuenta_creation_allowed = fuv_allowed
                cuenta_creation_message = False if fuv_allowed else fuv_message
            values = {
                'record': record,
                'docs': request.env['cuentas_de_cobros'].sudo().search([]),
                'login_id': login.id if login else 0,
                'token': login.token if login else '',
                'cuenta_creation_allowed': cuenta_creation_allowed,
                'cuenta_creation_message': cuenta_creation_message,
                'cuenta_creation_blocked': False,
            }
            values.update(_portal_acta_multi_document_values(record))
            values.update(self._portal_profile_values(record.login_id))
            return request.render('Matriz.template_cuentas_de_cobros_public_view', values)
        else:
            return request.not_found()

    


    @http.route('/public/cuentas_de_cobros/form/<int:record_id>/<string:token>', type='http', auth='public', website=True) 
    def public_form(self, record_id, token, **kwargs):
        record = request.env['cuentas_de_cobros'].sudo().browse(record_id)
        if record and record.token == token:
            documento_identificacion = record.documento_de_identificacion
            documento_identificacion_key = re.sub(r'\D+', '', documento_identificacion or '')
            tipo_persona = record.clase_persona

            # 🔄 Filtro condicionado según tipo de persona + estado_contratacion = 'contratado'
            if tipo_persona == 'persona_juridica':
                relaciones = request.env['contrataciones_fuv_relacion'].sudo().search([
                    ('select_persona', '=', tipo_persona),
                    ('estado_contratacion', '=', 'contratado')  # ✅ nuevo filtro agregado
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
                )
            else:
                # 👇 Si eligió "Persona Natural" en el HTML, buscamos ambos en la base de datos
                relaciones = request.env['contrataciones_fuv_relacion'].sudo().search([
                    ('select_persona', 'in', ['persona_natural', 'contrato_laboral']), # ✅ EL TRUCO ESTÁ AQUÍ
                    ('estado_contratacion', '=', 'contratado')
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.documento_identidad or '') == documento_identificacion_key
                )


                    # 🔄 Filtro para relacion_orden_compra_servicios_fuv_id
            if tipo_persona == 'persona_juridica':
                relacion_orden_compra_servicios = request.env['orden_compra_servicios'].sudo().search([
                    ('seleccion_persona', '=', tipo_persona),
                    ('estado_compra_servicios', '=', 'contratado')  # ✅ nuevo filtro agregado
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
                )
            else:
                relacion_orden_compra_servicios = request.env['orden_compra_servicios'].sudo().search([
                    ('seleccion_persona', '=', tipo_persona),
                    ('estado_compra_servicios', '=', 'contratado')  # ✅ nuevo filtro agregado
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.cedula_proveedor or '') == documento_identificacion_key
                )
            
            # 🔄 Filtro para relacion_orden_compra_bienes_fuv_id
            if tipo_persona == 'persona_juridica':
                relacion_orden_compra_bienes = request.env['orden_compra_bienes'].sudo().search([
                    ('seleccion_persona', '=', tipo_persona),
                    ('estado_compra_bienes', '=', 'contratado')  # ✅ nuevo filtro agregado
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
                )
            else:
                relacion_orden_compra_bienes = request.env['orden_compra_bienes'].sudo().search([
                    ('seleccion_persona', '=', tipo_persona),
                    ('estado_compra_bienes', '=', 'contratado')  # ✅ nuevo filtro agregado
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.cedula_proveedor or '') == documento_identificacion_key
                )
            
            # filtro para relacion_otrosi_fuv_id
            if tipo_persona == 'persona_juridica':
                relacion_otrosi = request.env['otrosi_bdd'].sudo().search([
                    ('select_persona', '=', tipo_persona),
                    ('estado_contratacion', 'in', _ESTADOS_OTROSI_DISPONIBLES_PARA_CUENTA),
                    '|', '|',
                    ('fuente_id.adicion', '=', True),
                    ('fuente_id.adicion_prorroga', '=', True),
                    '&',
                    ('fuente_id.otro_si_modificatorio', '=', True),
                    ('valor_total_contrato', '>', 0),
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.nit or '') == documento_identificacion_key
                )
            else:
                relacion_otrosi = request.env['otrosi_bdd'].sudo().search([
                    ('select_persona', 'in', ['persona_natural', 'contrato_laboral']),
                    ('estado_contratacion', 'in', _ESTADOS_OTROSI_DISPONIBLES_PARA_CUENTA),
                    '|', '|',
                    ('fuente_id.adicion', '=', True),
                    ('fuente_id.adicion_prorroga', '=', True),
                    '&',
                    ('fuente_id.otro_si_modificatorio', '=', True),
                    ('valor_total_contrato', '>', 0),
                ]).filtered(
                    lambda rel: re.sub(r'\D+', '', rel.documento_identidad or '') == documento_identificacion_key
                )

            contratos_regalias = request.env['base.datos.regalias.contrato'].sudo()
            if record.origen_cuenta == 'regalias':
                identificacion_regalias_key = re.sub(
                    r'\D+',
                    '',
                    documento_identificacion
                    or (record.login_id.documento_de_identidad if record.login_id else '')
                    or ''
                )
                centro_costo_key = self._portal_regalias_key(
                    self._portal_regalias_centro_costo_proyecto(record.proyecto_regalias_id)
                )
                contratos_por_identificacion = request.env['base.datos.regalias.contrato'].sudo().search(
                    [], order='numero_de_contrato, id'
                ).filtered(
                    lambda contrato: re.sub(r'\D+', '', contrato.identificacion or '') == identificacion_regalias_key
                )
                contratos_por_centro = contratos_por_identificacion.filtered(
                    lambda contrato: self._portal_regalias_key(contrato.un) == centro_costo_key
                ) if centro_costo_key else request.env['base.datos.regalias.contrato'].sudo()
                contratos_regalias = contratos_por_centro or contratos_por_identificacion
                if not record.contrato_regalias_id:
                    contrato_recuperado = False
                    if len(contratos_por_centro) == 1:
                        contrato_recuperado = contratos_por_centro[:1]
                    elif len(contratos_por_identificacion) == 1:
                        contrato_recuperado = contratos_por_identificacion[:1]
                    if contrato_recuperado:
                        valores_regalias = {'contrato_regalias_id': contrato_recuperado.id}
                        valor_contrato = self._parse_portal_number(
                            contrato_recuperado.valor_de_contrato_vigencia_2025_2026
                        )
                        if valor_contrato:
                            valores_regalias['valor_total_contrato'] = valor_contrato
                        self._portal_write(
                            record,
                            valores_regalias,
                            login=self._portal_login_for_chatter(record),
                        )
                        record.invalidate_recordset(['contrato_regalias_id', 'valor_total_contrato'])
                if record.contrato_regalias_id and record.contrato_regalias_id not in contratos_regalias:
                    contratos_regalias |= record.contrato_regalias_id

            if (
                record.selecionar in ('servicios', 'bienes')
                and record.contratista_id
                and not record.documento_equivalente_id
            ):
                record.sudo()._subir_orden_compra_generada_a_drive_cuenta(record, record.contratista_id)

            correction_request_active = bool(
                record.contratista_id
                and record.contratista_id.notificacion_enviada
                and record.contratista_id.correccion_portal_activa
            )

            values = {
                'record': record,
                'token': token,
                'correction_request_active': correction_request_active,
                'relaciones': relaciones,
                'relacion_orden_compra_servicios': relacion_orden_compra_servicios,
                'relacion_orden_compra_bienes': relacion_orden_compra_bienes,  # 🔸 nueva relación filtrada
                'relacion_otrosi': relacion_otrosi,  # 🔸 nueva relación filtrada
                'contratos_regalias': contratos_regalias,
                'relation_label': self._portal_relation_label,
            }
            values.update(_portal_acta_multi_document_values(record))
            values.update(self._portal_profile_values(record.login_id))
            return request.render('Matriz.template_cuentas_de_cobros_form_view', values)

        return request.not_found()



    @http.route('/public/cuentas_de_cobros/listado/<int:login_id>/<string:token>', 
                type='http', auth='public', website=True)
    def public_list_view(self, login_id, token, **kwargs):
        login = request.env['login'].sudo().browse(login_id)
        if not login or login.token != token:
            return request.not_found()

        documento_identificacion_key = re.sub(r'\D+', '', login.documento_de_identidad or '')

        # Filtros base
        domain = [
            ('active', '=', True)
        ]

        # Filtro opcional por name
        if kwargs.get('name'):
            domain.append(('name', 'ilike', kwargs['name']))

        # Filtro opcional por id
        if kwargs.get('record_id'):
            try:
                record_id = int(kwargs['record_id'])
                domain.append(('id', '=', record_id))
            except ValueError:
                pass  # Ignora si el parámetro no es un entero válido

        docs = request.env['cuentas_de_cobros'].sudo().search(domain).filtered(
            lambda cuenta: (
                cuenta.login_id == login
                or re.sub(r'\D+', '', cuenta.documento_de_identificacion or '') == documento_identificacion_key
            )
        )
        fuv_allowed, fuv_message = login.estado_creacion_cuenta_cobro_portal()
        # El botón general "Nueva Cuenta" depende únicamente de la ventana FUV.
        # La disponibilidad de Regalías se controla aparte dentro de su flujo.
        cuenta_creation_allowed = fuv_allowed
        cuenta_creation_message = False if fuv_allowed else fuv_message

        values = {
            'docs': docs,
            'login_id': login_id,
            'token': token,
            'search_name': kwargs.get('name'),
            'search_id': kwargs.get('record_id'),
            'cuenta_creation_allowed': cuenta_creation_allowed,
            'cuenta_creation_message': cuenta_creation_message,
            'cuenta_creation_blocked': kwargs.get('creation_blocked') == '1',
        }
        values.update(self._portal_profile_values(login))
        return request.render('Matriz.template_cuentas_de_cobros_public_view', values)

    @http.route('/public/cuentas_de_cobros/factura/<int:record_id>/<string:token>',
                type='http', auth='public', website=True)
    def public_download_factura_cuenta_cobro(self, record_id, token, **kwargs):
        record = request.env['cuentas_de_cobros'].sudo().browse(record_id)
        if not record.exists() or record.token != token or record.estado != 'finalizado':
            return request.not_found()

        acta = record.contratista_id
        if not acta:
            acta = request.env['acta_entrega_documentos'].sudo().search(
                [('cuenta_id', '=', record.id)],
                limit=1,
            )
        if not acta:
            return request.not_found()

        try:
            filename, pdf_data = acta.sudo()._generar_pdf_comprobante_pago_bytes()
        except Exception as exc:
            _logger.warning(
                "No se pudo generar el comprobante de pago de la cuenta de cobro %s: %s",
                record.id,
                exc,
            )
            return request.not_found()

        if not pdf_data:
            return request.not_found()

        filename = (filename or 'comprobante_pago.pdf').replace('"', '')
        return request.make_response(
            pdf_data,
            headers=[
                ('Content-Type', 'application/pdf'),
                ('Content-Length', len(pdf_data)),
                ('Content-Disposition', f'attachment; filename="{filename}"'),
            ],
        )

    @http.route('/public/register', type='http', auth='public', website=True)
    def register_form(self, **kwargs):
        return request.render('Matriz.registro_template', {})

    @http.route('/public/register/submit', type='http', auth='public', methods=['POST'], website=True, csrf=False)
    def register_submit(self, **post):
        name = post.get('name')
        coreo = post.get('coreo')
        if coreo:
            coreo = coreo.lower().strip() # 🔹 Normaliza el correo

        password = post.get('password')

        existing_user = request.env['login'].sudo().search([('coreo', '=ilike', coreo)], limit=1)
        if existing_user:
            return request.render('Matriz.registro_template', {
                'error': "Este correo ya está registrado."
            })

        new_user = request.env['login'].sudo().create({
            'name': name,
            'coreo': coreo,
            'password': password,
            'token': str(uuid.uuid4())
        })

        # Redirige directo al dashboard
        notifications = self._get_portal_notifications(new_user)
        return request.render('Matriz.inicio_publico_template', {
            'user': new_user,
            'notifications': notifications,
            'notification_count': self._get_portal_notification_count(notifications, new_user),
        })

    @http.route('/public/reset_password/<int:login_id>/<string:token>', type='http', auth='public', website=True)
    def reset_password_page(self, login_id, token, **kwargs):
        login_record = request.env['login'].sudo().browse(login_id)
        if not login_record or login_record.token != token:
            return request.render("Matriz.invalid_token_template", {})

        return request.render("Matriz.reset_password_template", {
            'login_id': login_id,
            'token': token
        })

    @http.route('/public/reset_password/submit', type='http', auth='public', website=True)
    def reset_password_submit(self, **kwargs):
        login_id = kwargs.get('login_id')
        token = kwargs.get('token')
        new_password = kwargs.get('password')
        confirm_password = kwargs.get('confirm_password')

        if new_password != confirm_password:
            return request.render('Matriz.reset_password_template', {'error': 'Las contraseñas no coinciden', 'token': token, 'login_id': login_id})

        # Buscar al usuario usando el token
        login_record = request.env['login'].sudo().search([('token', '=', token), ('id', '=', int(login_id))], limit=1)

        if not login_record:
            return request.render('Matriz.reset_password_template', {'error': 'Token no válido', 'token': token, 'login_id': login_id})

        # Actualizar la contraseña
        login_record.password = new_password

        # Redirigir al login
        return request.redirect('/public/login')

    @http.route('/public/reset_password/request', type='http', auth='public', website=True)
    def reset_password_request(self, **kw):
        return request.render('Matriz.reset_password_request_template')

    @http.route('/public/reset_password/send_link', type='http', auth='public', website=True, csrf=False, methods=["POST"])
    def reset_password_send_link(self, **post):
        coreo = post.get('coreo')
        if coreo:
            coreo = coreo.lower().strip() # 🔹 Normaliza el correo

        user = request.env['login'].sudo().search([('coreo', '=ilike', coreo)], limit=1)

        if user:
            user.send_reset_password_email()  # ✅ AQUÍ estás llamando tu función personalizada directamente
            return request.render('Matriz.reset_password_request_template', {
                'success': 'Se ha enviado un correo con el enlace para restablecer la contraseña.'
            })
        else:
            return request.render('Matriz.reset_password_request_template', {
                'error': 'No se encontró ningún usuario con ese correo.'
            })

    # 1. Método para MOSTRAR el formulario de Perfil
    @http.route('/public/upload_avatar/<int:user_id>', type='http', auth='public', website=True)
    def profile_edit_form(self, user_id, **kwargs):
        user = request.env['login'].sudo().search([('id', '=', user_id)], limit=1)
        # El token puede venir de la URL (kwargs) o de un campo oculto (post)
        access_token = request.httprequest.args.get('access_token') or kwargs.get('access_token') 
        
        if not user:
            return "Usuario no encontrado"
            
        # Validar el token
        if user.token != access_token:
            _logger.warning(f"Intento de acceso con token inválido para user {user_id}. Token: {access_token}")
            return request.render('website.404')
            
        # Renderizar la plantilla que ahora es el formulario de perfil completo
        return_url = _safe_public_return_url(
            request.httprequest.args.get('return_url') or kwargs.get('return_url')
        ) or f'/public/dashboard/{user.id}/{user.token}'
        values = {'user': user, 'return_url': return_url}
        values.update(self._portal_profile_values(user))
        return request.render('Matriz.upload_avatar_template', values)

    # 2. Método para GUARDAR los cambios del Perfil (incluyendo avatar, nombre, correo y contraseña)
    @http.route('/public/upload_avatar/save/<int:user_id>', type='http', auth='public', methods=['POST'], website=True, csrf=False)
    def profile_save(self, user_id, **post):
        user = request.env['login'].sudo().search([('id', '=', user_id)], limit=1)
        access_token = post.get('access_token')

        if not user or user.token != access_token:
            _logger.warning(f"Intento de guardar perfil con token inválido para user {user_id}.")
            return request.not_found()

        values = {}
        
        # --- 1. Procesar Nombre y Correo ---
        if 'name' in post:
            values['name'] = post['name']
        if post.get('coreo'):
            values['coreo'] = post.get('coreo').lower().strip() # 🔹 Normaliza el correo
            
        # --- 2. Procesar Contraseña (si se proporciona) ---
        new_password = post.get('new_password')
        confirm_password = post.get('confirm_password')

        if new_password and new_password == confirm_password:
            # Aquí se asume la validación de longitud (mínimo 6) para evitar errores, aunque el JS la hace.
            if len(new_password) >= 6: 
                values['password'] = new_password
            else:
                _logger.warning(f"Contraseña no guardada para user {user_id}: Longitud menor a 6 caracteres.")
        
        # --- 3. Procesar Avatar ---
        avatar_file = request.httprequest.files.get('avatar')
        if avatar_file:
            avatar_data = avatar_file.read()
            if avatar_data:
                values['avatar_128'] = base64.b64encode(avatar_data)
                
        # Guardar todos los cambios en la base de datos
        if values:
            self._portal_write(user, values, login=user)
            _logger.info(f"Perfil de usuario {user.name} ({user_id}) actualizado con éxito.")

        return_url = _safe_public_return_url(post.get('return_url')) or f'/public/dashboard/{user.id}/{user.token}'
        return request.redirect(return_url)
    
    # =================================================================================
    # 1. FUNCIÓN DE CARGA (GET) - Recupera datos guardados + Autocompletado
    # =================================================================================
    @http.route('/public/declaracion_juramentada/<int:cuenta_id>', type='http', auth='public', website=True)
    def declaracion_juramentada_form(self, cuenta_id, **kwargs):
        
        # --- 1. LOCALIZAR CUENTA Y DECLARACIÓN ---
        cuenta = request.env['cuentas_de_cobros'].sudo().browse(cuenta_id)
        if not cuenta.exists():
            return request.not_found()
        es_regalias = cuenta.origen_cuenta == 'regalias'
        factura_param_presente = 'factura' in kwargs
        factura_param = str(kwargs.get('factura') or '').strip().lower() in (
            '1', 'true', 'si', 'sí', 'yes', 'on'
        )
        if factura_param_presente:
            # El formulario de corrección puede forzar factura=0 antes de
            # guardar, para permitir crear nuevamente el Documento Equivalente.
            solo_declaracion_juramentada = factura_param
        else:
            solo_declaracion_juramentada = bool(
                cuenta.factura_si_o_no
                or cuenta.factura_id
                or (
                    cuenta.numero_factura
                    and cuenta.numero_factura.strip()
                    and cuenta.numero_factura.strip().upper() not in ('N/A', 'NA')
                )
            )

        declaracion = request.env['declaracion_juramentada'].sudo().search([
            ('relacion', '=', cuenta.id)
        ], order='id desc', limit=1)

        reusable_fields = (
            'arl',
            'sel_cc_nit', 'sel_ce', 'sel_pa',
            'ciudad',
            'direccion_residencia',
            'ciudad_residencia2', 'ciudad_contacto2',
            'telefono_fijo', 'celular', 'email', 'riesgo_arl',
            'servicio_check', 'bien_check',
            'profesional_check', 'tecnico_check',
            'pensionado_si', 'pensionado_no',
            'declarante_si', 'declarante_no',
            'check_cop', 'check_usd', 'check_eur',
            'name_declarante', 'doc_declarante', 'lugar_expedicion2',
            'salud_prepagada_si', 'salud_prepagada_no',
            'seguros_salud_si', 'seguros_salud_no',
            'intereses_vivienda_si', 'intereses_vivienda_no',
            'otras_rentas_exentas',
        )

        previous_account = request.env['cuentas_de_cobros']
        previous_declaration = request.env['declaracion_juramentada']
        valores_previos = {}

        previous_declaration_domain = [('relacion', '!=', cuenta.id)]
        if cuenta.login_id:
            previous_declaration_domain.append(('relacion.login_id', '=', cuenta.login_id.id))
        elif cuenta.documento_de_identificacion:
            previous_declaration_domain.append(('relacion.documento_de_identificacion', '=', cuenta.documento_de_identificacion))
        else:
            previous_declaration_domain = []

        if previous_declaration_domain:
            previous_declaration_domain.append(('relacion.origen_cuenta', '=', cuenta.origen_cuenta or 'fuv'))
            previous_declaration = request.env['declaracion_juramentada'].sudo().search(
                previous_declaration_domain,
                order='id desc',
                limit=1,
            )
            previous_account = previous_declaration.relacion if previous_declaration else request.env['cuentas_de_cobros']

        if previous_declaration:
            for field_name in reusable_fields:
                value = previous_declaration[field_name]
                if previous_declaration._fields[field_name].type == 'many2one':
                    value = value.id
                if value not in (False, None, ''):
                    valores_previos[field_name] = value

        # Si no existe, la creamos
        if not declaracion:
            declaracion_vals = {
                'name': cuenta.nombre_contratista or cuenta.name,
                'relacion': cuenta.id,
            }

            declaracion_vals.update(valores_previos)

            if previous_declaration.dependientes_ids:
                declaracion_vals['dependientes_ids'] = [
                    (0, 0, {
                        'parentesco': line.parentesco,
                        'edad': line.edad,
                        'identificacion': line.identificacion,
                        'nombre': line.nombre,
                        'ausencia_ingreso': line.ausencia_ingreso,
                        'estudios': line.estudios,
                        'factor_fisico': line.factor_fisico,
                    })
                    for line in previous_declaration.dependientes_ids
                ]

            portal_login = self._portal_login_for_chatter(cuenta)
            create_ctx = {'portal_chatter_login_id': portal_login.id} if portal_login else {}
            declaracion = request.env['declaracion_juramentada'].sudo().with_context(**create_ctx).create(declaracion_vals)
            self._reassign_portal_chatter_author(declaracion, portal_login, declaracion.create_date)
        elif valores_previos:
            # Algunas cuentas ya tienen una declaración vacía creada antes de abrir
            # este formulario. En ese caso también se precargan los datos anteriores,
            # sin reemplazar nada que el usuario ya haya diligenciado.
            missing_values = {
                field_name: value
                for field_name, value in valores_previos.items()
                if declaracion[field_name] in (False, None, '')
            }
            if missing_values:
                self._portal_write(declaracion, missing_values)

            if previous_declaration.dependientes_ids and not declaracion.dependientes_ids:
                self._portal_write(declaracion, {
                    'dependientes_ids': [
                        (0, 0, {
                            'parentesco': line.parentesco,
                            'edad': line.edad,
                            'identificacion': line.identificacion,
                            'nombre': line.nombre,
                            'ausencia_ingreso': line.ausencia_ingreso,
                            'estudios': line.estudios,
                            'factor_fisico': line.factor_fisico,
                        })
                        for line in previous_declaration.dependientes_ids
                    ],
                })

        # --- 2. OBTENER EL LOGIN (¡LA CLAVE DEL ÉXITO!) ---
        # Ya no usamos cookies. Usamos la relación directa de la base de datos.
        login = declaracion.login_id if declaracion.login_id else (cuenta.login_id if hasattr(cuenta, 'login_id') else None)

        municipios = request.env['municipio'].sudo().search([], order='name ASC')

        # --- 3. FECHAS DEL FORMULARIO ---
        hoy = date.today()
        nombres_meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        mapa_dias = {'1': 'un', '2': 'dos', '3': 'tres', '4': 'cuatro', '5': 'cinco', '6': 'seis', '7': 'siete', '8': 'ocho', '9': 'nueve', '10': 'diez', '11': 'once', '12': 'doce', '13': 'trece', '14': 'catorce', '15': 'quince', '16': 'dieciséis', '17': 'diecisiete', '18': 'dieciocho', '19': 'diecinueve', '20': 'veinte', '21': 'veintiuno', '22': 'veintidós', '23': 'veintitrés', '24': 'veinticuatro', '25': 'veinticinco', '26': 'veintiséis', '27': 'veintisiete', '28': 'veintiocho', '29': 'veintinueve', '30': 'treinta', '31': 'treinta y un'}
        
        dia_actual = str(hoy.day)
        dia_letras = mapa_dias.get(dia_actual, dia_actual)
        mes_actual = hoy.strftime('%m') 
        anio_actual = str(hoy.year)
        mes_nombre = nombres_meses[hoy.month - 1]

        def partes_fecha_actividad(fecha, dia_guardado, mes_guardado, anio_guardado):
            fecha = fields.Date.to_date(fecha) if fecha else False
            if fecha:
                return str(fecha.day), nombres_meses[fecha.month - 1], str(fecha.year)
            return dia_guardado or '', mes_guardado or '', anio_guardado or ''

        desde_dia_portal, desde_mes_portal, desde_anio_portal = partes_fecha_actividad(
            cuenta.inicio_actividad,
            declaracion.desde_dia,
            declaracion.desde_mes,
            declaracion.desde_anio,
        )
        hasta_dia_portal, hasta_mes_portal, hasta_anio_portal = partes_fecha_actividad(
            cuenta.finalizacion_actividad,
            declaracion.hasta_dia,
            declaracion.hasta_mes,
            declaracion.hasta_anio,
        )

        # --- 4. MAPEO SEGURO DE DATOS DEL LOGIN ---
        # Extraemos con cuidado por si algún campo está vacío
        login_name = login.name if login else ''
        login_correo = login.coreo if login and hasattr(login, 'coreo') else ''
        login_doc_id = login.documento_de_identidad if login and hasattr(login, 'documento_de_identidad') else ''
        login_dir = login.direccion_fisica if login and hasattr(login, 'direccion_fisica') else ''
        login_tel = login.telefono_contacto_comercial if login and hasattr(login, 'telefono_contacto_comercial') and login.telefono_contacto_comercial else (login.numero_telefono if login and hasattr(login, 'numero_telefono') else '')

        # Prioridad: 1° Lo que el usuario ya guardó. 2° Lo que traemos de la relación 'login_id'
        nombre_usuario = declaracion.name or login_name
        email_usuario = declaracion.email or login_correo
        num_doc = declaracion.numero_documento or login_doc_id
        direccion_usuario = declaracion.direccion_residencia or login_dir
        telefono_usuario = declaracion.celular or login_tel
        
        ciudad_contacto_id = (
            declaracion.ciudad_residencia2.id
            if declaracion.ciudad_residencia2
            else (
                declaracion.ciudad_contacto2.id
                if declaracion.ciudad_contacto2
                else (login.municipios2.id if login and login.municipios2 else False)
            )
        )
        ciudad_expedicion_id = (
            declaracion.lugar_expedicion2.id
            if declaracion.lugar_expedicion2
            else (
                declaracion.ciudad_expedicion2.id
                if declaracion.ciudad_expedicion2
                else (
                    login.ciudad_exp_natural.id
                    if login and hasattr(login, 'ciudad_exp_natural') and login.ciudad_exp_natural
                    else False
                )
            )
        )

        # --- 5. LÓGICA INFALIBLE PARA LOS CUADROS DE DOCUMENTO ---
        val_cc_nit = declaracion.sel_cc_nit or ''
        val_ce = declaracion.sel_ce or ''
        val_pa = declaracion.sel_pa or ''

        # Solo calculamos si es la primera vez (los 3 cuadros están vacíos)
        if not es_regalias and not val_cc_nit and not val_ce and not val_pa:
            tipo_calculado = login.tipo_documento_calculado.lower().strip() if login and login.tipo_documento_calculado else ''
            
            # Si el modelo trae el dato correcto
            if tipo_calculado in ['cc', 'nit']:
                val_cc_nit = tipo_calculado.upper()
            elif tipo_calculado == 'ce':
                val_ce = 'CE'
            elif tipo_calculado == 'pa':
                val_pa = 'PA'
            else:
                # PLAN B: Leemos el número directamente si el modelo está vacío (registros viejos)
                doc_limpio = str(login_doc_id).replace('.', '').replace(' ', '').strip().lower()
                if doc_limpio:
                    if '-' in doc_limpio or len(doc_limpio) > 10:
                        val_cc_nit = 'nit'
                    elif doc_limpio.startswith('ce'):
                        val_ce = 'ce'
                    elif doc_limpio.startswith('pa') or len(doc_limpio) < 8:
                        val_pa = 'pa'
                    else:
                        val_cc_nit = 'cc'

        # --- 6. RENDERIZADO COMPLETO ---
        values = {
            'declaracion': declaracion,
            'cuenta_id': cuenta_id,
            'cuenta_token': cuenta.token if cuenta else '',
            'relacion': cuenta_id,
            'municipios': municipios,
            'login': login,
            'consecutivo': declaracion.consecutivo or '',
            # Fechas y Visualización
            'dia_actual': dia_actual,
            'mes_actual': mes_actual, 
            'anio_actual': anio_actual,
            'dia_letras': dia_letras,
            'mes_visual': mes_nombre,
            'desde_dia_portal': desde_dia_portal,
            'desde_mes_portal': desde_mes_portal,
            'desde_anio_portal': desde_anio_portal,
            'hasta_dia_portal': hasta_dia_portal,
            'hasta_mes_portal': hasta_mes_portal,
            'hasta_anio_portal': hasta_anio_portal,
            
            # --- LAS VARIABLES QUE SE VAN AL XML ---
            'nombre_usuario': nombre_usuario,
            'email_usuario': email_usuario,
            'num_doc': num_doc,
            'direccion_usuario': direccion_usuario,
            'telefono_usuario': telefono_usuario,
            'ciudad_contacto_id': ciudad_contacto_id,
            'ciudad_expedicion_id': ciudad_expedicion_id,
            
            # --- VARIABLES DE LOS 3 CUADROS ---
            'val_cc_nit': val_cc_nit,
            'val_ce': val_ce,
            'val_pa': val_pa,
            'dependientes_ids': declaracion.dependientes_ids,
            'cuenta_anterior': previous_account,
            'valores_previos': valores_previos,
            'requiere_refirma_declaracion': False,
            'solo_declaracion_juramentada': solo_declaracion_juramentada,
        }
        values.update(self._portal_profile_values(login))
        return request.render('Matriz.declaracion_juramentada_public_template', values)
    # =================================================================================
    # 2. FUNCIÓN DE ENVÍO (POST) - Guarda (Write) sobre el mismo registro
    # =================================================================================
    @http.route('/public/declaracion_juramentada/submit', type='http', auth='public', website=True, csrf=False)
    def declaracion_juramentada_submit(self, **post):
        # Localizamos el ID del registro mediante el campo oculto relacion o cuenta_id
        cuenta_id_val = post.get('cuenta_id') or post.get('relacion')
        if not cuenta_id_val:
            return request.not_found()

        cuenta_id = int(cuenta_id_val)
        # La generación escribe la declaración, el acta y su vínculo de Drive.
        # Dos envíos simultáneos de la misma pantalla no pueden procesar esa
        # cadena a la vez: el segundo espera y continúa cuando el primero haya
        # terminado, evitando SerializationFailure sobre el acta.
        request.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("Matriz:declaracion_juramentada_submit:%s" % cuenta_id,),
        )
        juramentada = request.env['declaracion_juramentada'].sudo().search([
            ('relacion', '=', cuenta_id)
        ], limit=1)

        if not juramentada:
            return request.not_found()
        # --- VALIDACIÓN DEL NOMBRE ---
        nombre_ingresado = post.get('name')
        if not nombre_ingresado or not str(nombre_ingresado).strip():
            return _portal_bad_request({'message': 'El campo Nombre y Apellidos es obligatorio.'})
        requiere_refirma = False
        if requiere_refirma and (post.get('firmado_contratista') != 'on' or post.get('firmado_declarante') != 'on'):
            return _portal_bad_request({
                'message': 'Debe marcar las firmas electrónicas antes de enviar la declaración juramentada.'
            })

        # --- LÓGICA DE LISTA DE DEPENDIENTES ---
        def getlist(val):
            res = post.get(val, [])
            return res if isinstance(res, list) else [res]

        parentescos      = getlist('parentesco[]')
        edades           = getlist('edad[]')
        identificaciones = getlist('identificacion[]')
        nombres          = getlist('nombre[]')
        estudios         = getlist('estudios[]')
        factores         = getlist('factor_fisico[]')

        dependientes_final = [(5, 0, 0)] 
        for i in range(len(parentescos)):
            ausencia = True if post.get('ausencia_ingreso_row{}'.format(i)) == "1" else False
            dependientes_final.append((0, 0, {
                'parentesco': parentescos[i],
                'edad': int(edades[i]) if i < len(edades) and edades[i] else 0,
                'identificacion': identificaciones[i] if i < len(identificaciones) else "",
                'nombre': nombres[i] if i < len(nombres) else "",
                'ausencia_ingreso': ausencia, 
                'estudios': estudios[i] if i < len(estudios) else "",
                'factor_fisico': factores[i] if i < len(factores) else "",
            }))

        # --- 1. SOLUCIÓN AL ERROR DEL FLOAT (50.000.000,00) ---
        valor_raw = post.get('valor_numeros', '0')
        valor_limpio = 0.0
        if valor_raw:
            # Reemplazamos puntos de mil por nada y coma decimal por punto
            try:
                valor_pre_float = str(valor_raw).replace('.', '').replace(',', '.')
                valor_limpio = float(valor_pre_float)
            except:
                valor_limpio = 0.0

        # --- 2. CAPTURAR FIRMA (BASE64 DESDE JS) ---
        firma_js = post.get('firma_contratista')

        # --- RECOPILACIÓN DE TODA LA INFORMACIÓN ---
        vals = {
            'consecutivo': post.get('consecutivo'), 
            'name': nombre_ingresado,
            'sel_cc_nit': post.get('sel_cc_nit'),
            'sel_ce': post.get('sel_ce'),
            'sel_pa': post.get('sel_pa'),
            'numero_documento': post.get('numero_documento'),
            'ciudad': post.get('ciudad'),
            'desde_dia': post.get('desde_dia'),
            'desde_mes': post.get('desde_mes'),
            'desde_anio': post.get('desde_anio'),
            'hasta_dia': post.get('hasta_dia'),
            'hasta_mes': post.get('hasta_mes'),
            'hasta_anio': post.get('hasta_anio'),
            'direccion_residencia': post.get('direccion_residencia'), 
            'ciudad_contacto2': int(post.get('ciudad_contacto2')) if post.get('ciudad_contacto2') else False, 
            'ciudad_residencia2': int(post.get('ciudad_residencia2')) if post.get('ciudad_residencia2') else False,
            'telefono_fijo': post.get('telefono_fijo'), 
            'celular': post.get('celular'),
            'email': post.get('email'),
            'riesgo_arl': post.get('riesgo_arl'), 
            'check_cop': post.get('check_cop'),
            'check_usd': post.get('check_usd'),
            'check_eur': post.get('check_eur'),
            'servicio_check': post.get('servicio_check'),
            'profesional_check': post.get('profesional_check'),
            'tecnico_check': post.get('tecnico_check'),
            'bien_check': post.get('bien_check'),
            'pensionado_si': post.get('pensionado_si'),
            'pensionado_no': post.get('pensionado_no'),
            'declarante_si': post.get('declarante_si'),
            'declarante_no': post.get('declarante_no'),
            'detalle_actividad': post.get('detalle_actividad'),
            'valor_numeros': valor_limpio, # <--- Usamos el valor ya procesado
            'valor_letras': post.get('valor_letras'),
            'name_declarante': post.get('name_declarante'),
            'doc_declarante': post.get('doc_declarante'),
            'lugar_expedicion2': int(post.get('lugar_expedicion2')) if post.get('lugar_expedicion2') else False,
            'salud_prepagada_si': post.get('salud_prepagada_si'),
            'salud_prepagada_no': post.get('salud_prepagada_no'),
            'seguros_salud_si': post.get('seguros_salud_si'),
            'seguros_salud_no': post.get('seguros_salud_no'),
            'intereses_vivienda_si': post.get('intereses_vivienda_si'),
            'intereses_vivienda_no': post.get('intereses_vivienda_no'),
            'otras_rentas_exentas': post.get('otras_rentas_exentas'),
            'ciudad_expedicion2': int(post.get('ciudad_expedicion_firma')) if post.get('ciudad_expedicion_firma') else False, 
            'dia_expedicion': post.get('dia_expedicion'),
            'mes_expedicion': post.get('mes_expedicion'), 
            'dias_2': post.get('dias_2'), 
            'anio_char': post.get('anio_char'),
            'arl': post.get('arl'),
            'dependientes_ids': dependientes_final, # <--- AGREGADA COMA AQUÍ (Faltaba)
            'firma_contratista': post.get('firma_base64'),           # La de arriba
            'firma_declarante': post.get('firma_base64_declarante'), # La de abajo
            'firmado_contratista': True if post.get('firmado_contratista') == 'on' else False,
            'firmado_declarante': True if post.get('firmado_declarante') == 'on' else False,
        }

        # Solo guardamos la firma si el JS envió algo
        if firma_js:
            # Si el JS envía el prefijo data:image/png;base64, hay que quitarlo
            if isinstance(firma_js, str) and ',' in firma_js:
                vals['firma_contratista'] = firma_js.split(',')[1]
                vals['firma_declarante'] = firma_js.split(',')[1]
            else:
                vals['firma_contratista'] = firma_js
                vals['firma_declarante'] = firma_js


        # --- ACTUALIZAR EL REGISTRO ---
        # RECUERDA: Debes arreglar el super() en el modelo para que esto no falle
        portal_login = self._portal_login_for_chatter(juramentada)
        portal_ctx = {'portal_chatter_login_id': portal_login.id} if portal_login else {}
        self._portal_write(juramentada, vals, login=portal_login)

        error_generacion = False
        if hasattr(juramentada, 'generar_informe_xlsx'):
            since = fields.Datetime.now()
            try:
                with request.env.cr.savepoint():
                    generado = juramentada.with_context(
                        raise_document_generation_error=True,
                        **portal_ctx,
                    ).generar_informe_xlsx()
                    if not generado:
                        raise UserError(
                            "No fue posible generar el documento equivalente y la declaración juramentada."
                        )
                    self._reassign_portal_chatter_author(juramentada, portal_login, since)
                    if juramentada.relacion and juramentada.relacion.contratista_id:
                        acta = juramentada.relacion.contratista_id
                        self._reassign_portal_chatter_author(acta, portal_login, since)
                        correction_vals = {
                            field_name: False
                            for field_name in (
                                'documento_equivalente_corregir',
                                'declaracion_juramentada_y_soportes_corregir',
                            )
                            if field_name in acta._fields and getattr(acta, field_name, False)
                        }
                        if correction_vals:
                            acta.sudo().write(correction_vals)
            except Exception as error:
                # Odoo reintenta automáticamente la petición HTTP completa
                # ante conflictos 40001/40P01/55P03. Convertirlos aquí en un
                # mensaje funcional impediría ese mecanismo de recuperación.
                if getattr(error, 'pgcode', None) in ('40001', '40P01', '55P03'):
                    raise
                _logger.warning(
                    "No se pudo generar/subir el documento equivalente durante el envío público de declaración %s.",
                    juramentada.id,
                    exc_info=True,
                )
                error_generacion = str(error) or (
                    "No fue posible generar el documento equivalente y la declaración juramentada."
                )

        if error_generacion:
            return _portal_bad_request({'message': error_generacion})

        cuenta = juramentada.relacion
        return request.redirect(f'/public/cuentas_de_cobros/form/{cuenta.id}/{cuenta.token}?success=1')

    @http.route('/public/declaracion_juramentada/clear_signature/<int:declaracion_id>', type='http', auth='public', website=True)
    def clear_declaracion_signature(self, declaracion_id, **kwargs):
        # 1. Buscamos la declaración por su ID (el 16 que salió en tu error)
        declaracion = request.env['declaracion_juramentada'].sudo().browse(declaracion_id)
        
        if declaracion.exists():
            # 2. Obtenemos la cuenta para poder regresar al formulario después
            cuenta_id = declaracion.relacion.id
            
            # 3. Borramos los campos de la firma
            self._portal_write(declaracion, {
                'firma_contratista': False,
                'firmado_contratista': False,
                'firma_declarante': False,
                'firmado_declarante': False,
            })
            
            # 4. Redirigimos de vuelta al formulario de la declaración
            # Usamos la ruta que ya tienes para cargar el formulario
            if cuenta_id:
                return request.redirect(f'/public/declaracion_juramentada/{cuenta_id}')
        
        # Si algo falla, mandamos a una página de error o al inicio
        return request.redirect('/public/cuentas_de_cobros')

    # =================================================================================
    # 1. FUNCIÓN DE CARGA (GET) - Abre el formulario con datos ya llenos
    # =================================================================================
    @http.route([
        '/public/informe_actividades',
        '/public/informe_actividades/<int:cuenta_id>'
    ], type='http', auth='public', website=True)
    def informe_actividades_form(self, cuenta_id=None, **kwargs):
        
        # --- 1. LOCALIZAR LA CUENTA Y EL INFORME ---
        cuenta = False
        informe = False
        nombre_definitivo = ""

        # Buscamos la cuenta si viene en la URL
        if cuenta_id:
            cuenta = request.env['cuentas_de_cobros'].sudo().browse(cuenta_id)
            if not cuenta.exists():
                return request.not_found()
        
        # Si encontramos la cuenta, buscamos el informe amarrado o lo creamos
        if cuenta:
            # Recuerda que este campo 'cuenta_cobro_id' es el nuevo que creaste en el modelo de Informe
            informe = request.env['informe_actividades'].sudo().search([
                ('cuenta_cobro_id', '=', cuenta.id)
            ], limit=1)

            if not informe:
                portal_login = self._portal_login_for_chatter(cuenta)
                create_ctx = {'portal_chatter_login_id': portal_login.id} if portal_login else {}
                informe = request.env['informe_actividades'].sudo().with_context(**create_ctx).create({
                    'name': cuenta.nombre_contratista or cuenta.name,
                    'cuenta_cobro_id': cuenta.id,
                })
                self._reassign_portal_chatter_author(informe, portal_login, informe.create_date)
        else:
            # Como respaldo por si entran sin ID en la URL, buscamos por Cookie
            user_id_cookie = request.httprequest.cookies.get('user_id')
            if user_id_cookie:
                login_record = request.env['login'].sudo().browse(int(user_id_cookie))
                if login_record.exists():
                    nombre_definitivo = login_record.name
                    cuenta = request.env['cuentas_de_cobros'].sudo().search(
                        [('name', '=', nombre_definitivo)], order='create_date desc', limit=1
                    )
                    informe = request.env['informe_actividades'].sudo().search(
                        [('name', '=', nombre_definitivo)], order='create_date desc', limit=1
                    )

        # Establecemos el nombre definitivo
        if informe and informe.name:
            nombre_definitivo = informe.name
        elif cuenta and hasattr(cuenta, 'nombre_contratista') and cuenta.nombre_contratista:
            nombre_definitivo = cuenta.nombre_contratista
        elif cuenta and cuenta.name:
            nombre_definitivo = cuenta.name

        # --- 2. EXTRAER LOS DATOS (De la Cuenta o del Informe) ---
        val_no_contrato = ""
        val_no_contrato2 = ""
        val_objeto = ""
        val_descripcion = ""
        val_proyecto = ""

        # A. Prioridad 1: Los datos más frescos directamente de la Cuenta de Cobro
        if cuenta:
            if cuenta.origen_cuenta == 'regalias':
                val_no_contrato = (
                    cuenta.contrato_regalias_id.numero_de_contrato
                    if cuenta.contrato_regalias_id
                    else ""
                )
                val_no_contrato2 = val_no_contrato
            else:
                val_no_contrato = cuenta.id_del_contrato if hasattr(cuenta, 'id_del_contrato') and cuenta.id_del_contrato else ""
            val_objeto = cuenta.objeto_contrato if hasattr(cuenta, 'objeto_contrato') and cuenta.objeto_contrato else ""
            if hasattr(cuenta, 'proyecto_relacionado') and cuenta.proyecto_relacionado:
                p = cuenta.proyecto_relacionado
                val_proyecto = p.new_name if hasattr(p, 'new_name') and p.new_name else p.name

        # B. Prioridad 2: Si la cuenta está vacía, intentamos con los datos guardados en el Informe
        if informe:
            # En Regalías el contrato seleccionado en su base de datos es la
            # fuente oficial; no debe ser reemplazado por un valor viejo del informe.
            if not cuenta or cuenta.origen_cuenta != 'regalias':
                val_no_contrato = informe.no_contrato2 or informe.no_contrato or val_no_contrato
                val_no_contrato2 = informe.no_contrato2 or val_no_contrato2 or val_no_contrato
            val_objeto = informe.objeto_contrato if informe.objeto_contrato else val_objeto
            descripcion_guardada = informe.descripcion_actividades or ""
            descripcion_automatica = (
                cuenta.actividades_especificas
                if cuenta and hasattr(cuenta, 'actividades_especificas')
                else ""
            )
            informe_ya_enviado = bool(
                informe.informe_de_actividades_doc
                or informe.firma_contratista_check
            )
            if (
                descripcion_guardada
                and (
                    informe_ya_enviado
                    or descripcion_guardada.strip() != (descripcion_automatica or "").strip()
                )
            ):
                val_descripcion = descripcion_guardada
            if informe.nombre_proyecto and not val_proyecto:
                p = informe.nombre_proyecto
                val_proyecto = p.new_name if hasattr(p, 'new_name') and p.new_name else p.name

        if not val_no_contrato2:
            val_no_contrato2 = val_no_contrato

        val_observaciones = informe.observaciones if informe and informe.observaciones else ""

        # --- 3. FECHAS Y CONSECUTIVO ---
        from datetime import date
        hoy = date.today()
        nombres_meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

        fecha_fin_cuenta = (
            fields.Date.to_date(cuenta.finalizacion_actividad)
            if cuenta and cuenta.finalizacion_actividad
            else False
        )
        fecha_actividad_base = fecha_fin_cuenta or hoy
        
        consecutivo_final = informe.consecutivo if informe and informe.consecutivo else ""
        if not consecutivo_final and cuenta:
            declaracion = request.env['declaracion_juramentada'].sudo().search([
                ('relacion', '=', cuenta.id)
            ], order='id desc', limit=1)
            consecutivo_final = declaracion.consecutivo if declaracion and declaracion.consecutivo else ""

        # --- 4. ENVIAR A LA VISTA XML ---
        values = {
            'cuenta': cuenta if cuenta else False,
            'informe': informe if informe else False,
            # Le pasamos la cuenta como "relacion" para tu input hidden en el HTML
            'relacion': cuenta if cuenta else False, 
            'consecutivo': consecutivo_final,
            
            # Prioridad: fecha editada del informe -> fecha final de la cuenta -> hoy.
            'dia_actual': informe.actividad_dia if informe and informe.actividad_dia else str(fecha_actividad_base.day).zfill(2),
            'mes_visual': informe.actividad_mes if informe and informe.actividad_mes else nombres_meses[fecha_actividad_base.month - 1],
            'anio_actual': informe.actividad_anio if informe and informe.actividad_anio else str(fecha_actividad_base.year),
            'selecionar': informe.selecionar if informe and informe.selecionar else (cuenta.selecionar if cuenta and hasattr(cuenta, 'selecionar') else ''),
            
            # Variables ya procesadas para pintar en el form
            'nombre_defecto': nombre_definitivo,
            'default_proyecto': val_proyecto,
            'default_no_contrato': val_no_contrato,
            'default_no_contrato2': val_no_contrato2,
            'default_objeto': val_objeto,
            'default_descripcion': val_descripcion,
            'default_observaciones': val_observaciones,
            'requiere_refirma_informe': False,
        }

        informe_login = cuenta.login_id if cuenta and cuenta.login_id else None
        values.update(self._portal_profile_values(informe_login))

        return request.render('Matriz.informe_actividades_public_template', values)
    # =================================================================================
    # 2. FUNCIÓN DE ENVÍO (POST) - Guarda los datos y genera el archivo
    # =================================================================================
    @http.route('/public/informe_actividades/submit', type='http', auth='public', website=True, csrf=False)
    def informe_actividades_submit(self, **post):
        # 1. Localizamos los IDs que enviamos desde el XML
        informe_id = post.get('informe_id')
        relacion_id = post.get('relacion_id')
        
        informe = None

        # 1. PROCESAR FIRMA (Evitar el error de 'bool' en Drive)
        img_file = post.get('firma_contratista_img')
        img_base64 = False
        if img_file and hasattr(img_file, 'read'):
            data = img_file.read()
            if data: # Solo si hay bytes reales
                img_base64 = base64.b64encode(data)

        # 2. Buscamos el informe en la base de datos
        if informe_id:
            informe = request.env['informe_actividades'].sudo().browse(int(informe_id))
        elif relacion_id:
            informe = request.env['informe_actividades'].sudo().search([
                ('relacion_id', '=', int(relacion_id))
            ], limit=1)

        if not informe or not informe.exists():
            return request.render('http_routing.404') 
        cuenta_bloqueo = informe.cuenta_cobro_id or informe.relacion_id.cuenta_id
        if cuenta_bloqueo:
            request.env.cr.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("Matriz:informe_actividades_submit:%s" % cuenta_bloqueo.id,),
            )
        cuenta_refirma = informe.cuenta_cobro_id or informe.relacion_id
        requiere_refirma = False
        if requiere_refirma and post.get('firma_contratista_check') != 'on':
            return _portal_bad_request({
                'message': 'Debe marcar la firma electrónica del contratista antes de enviar el informe de actividades.'
            })
        firma_contratista_check_actual = bool(informe.firma_contratista_check)

        # 3. Guardar los datos del formulario (Agrupamos todo en vals)
        vals = {
            'actividad_anio': post.get('actividad_anio'),
            'actividad_mes': post.get('actividad_mes'),
            'actividad_dia': post.get('actividad_dia'),
            'consecutivo': post.get('consecutivo'),
            'name': post.get('name'),
            'no_contrato2': post.get('no_contrato2') or post.get('no_contrato'),
            'objeto_contrato': post.get('objeto_contrato'),
            'descripcion_actividades': post.get('descripcion_actividades'),
            'observaciones': post.get('observaciones'),
            'firma_contratista_check': True if post.get('firma_contratista_check') == 'on' else firma_contratista_check_actual,
        }

        # Si hay imagen, la añadimos al paquete de guardado
        if img_base64:
            vals['firma_contratista_img'] = img_base64

        # Guardamos todo de una vez
        portal_login = self._portal_login_for_chatter(informe)
        portal_ctx = {'portal_chatter_login_id': portal_login.id} if portal_login else {}
        self._portal_write(informe, vals, login=portal_login)

        # 4. Generar el archivo Excel
        if hasattr(informe, 'generar_informe_xlsx'):
            since = fields.Datetime.now()
            generado = informe.with_context(
                raise_document_generation_error=True,
                **portal_ctx,
            ).generar_informe_xlsx()
            if not generado:
                raise UserError(
                    "No fue posible generar y verificar el informe de actividades."
                )
            self._reassign_portal_chatter_author(informe, portal_login, since)
            acta = informe.relacion_id or (informe.cuenta_cobro_id and informe.cuenta_cobro_id.contratista_id)
            if acta:
                self._reassign_portal_chatter_author(acta, portal_login, since)
                if (
                    'informe_de_actividades_corregir' in acta._fields
                    and acta.informe_de_actividades_corregir
                ):
                    acta.sudo().write({'informe_de_actividades_corregir': False})

        # 5. Redirección a la Cuenta de Cobro
        cuenta_id_str = post.get('cuenta_id')
        
        if cuenta_id_str:
            cuenta_final = request.env['cuentas_de_cobros'].sudo().browse(int(cuenta_id_str))
            
            if cuenta_final.exists():
                # Buscamos el token de acceso (ajustado para evitar el error si el campo no existe)
                token = getattr(cuenta_final, 'access_token', getattr(cuenta_final, 'token', ''))
                
                # Redirigimos a la vista de la cuenta de cobro
                # NOTA: Si te sigue dando 404, asegúrate de que la cuenta tenga un token válido.
                if token:
                    return request.redirect(f'/public/cuentas_de_cobros/form/{cuenta_final.id}/{token}?success=1')
                else:
                    return request.redirect(f'/public/cuentas_de_cobros/form/{cuenta_final.id}/?success=1')
        
        return request.redirect('/public/informe_actividades?success=1')

    
    @http.route('/public/informe_actividades/clear_signature/<int:informe_id>', type='http', auth='public', website=True)
    def clear_contratista_signature(self, informe_id, **kwargs):
        informe = request.env['informe_actividades'].sudo().browse(informe_id)
        if informe.exists():
            self._portal_write(informe, {
                'firma_contratista_img': False,
                'firma_contratista_check': False,
                'firma_contratista_hash': False
            })
            # Redirigir usando la cuenta vinculada
            cuenta = informe.cuenta_cobro_id
            if cuenta:
                return request.redirect(f'/public/informe_actividades/{cuenta.id}')
        return request.redirect('/public/informe_actividades?success=0')

    # =================================================================================
    # CHAT DE SOPORTE: Contratista ↔ Asistente de coordinación
    # =================================================================================
    @http.route('/public/chat/messages/<int:login_id>/<string:token>', type='json', auth='public', website=True)
    def chat_get_messages(self, login_id, token, cuenta_id=0, **kw):
        try:
            login = request.env['login'].sudo().browse(login_id)
            if not login.exists() or login.token != token:
                return {'error': 'unauthorized'}
            domain = [('login_id', '=', login_id)]
            if cuenta_id:
                domain.append(('cuenta_cobro_id', '=', cuenta_id))
            messages = request.env['portal.chat.message'].sudo().search(
                domain, order='create_date asc'
            )
            return {
                'messages': [{
                    'id': m.id,
                    'sender_type': m.sender_type,
                    'sender_name': m.sender_name or '',
                    'body': m.body,
                    'date': m.create_date.strftime('%d/%m %H:%M') if m.create_date else '',
                } for m in messages]
            }
        except Exception as e:
            _logger.exception("Error en chat_get_messages: %s", e)
            return {'error': str(e)}

    @http.route('/public/chat/send/<int:login_id>/<string:token>', type='json', auth='public', website=True)
    def chat_send_message(self, login_id, token, body='', cuenta_id=0, **kw):
        try:
            login = request.env['login'].sudo().browse(login_id)
            if not login.exists() or login.token != token or not body or not body.strip():
                return {'error': 'invalid'}
            vals = {
                'login_id': login_id,
                'sender_type': 'contratista',
                'sender_name': login.name or 'Contratista',
                'body': body.strip(),
            }
            if cuenta_id:
                vals['cuenta_cobro_id'] = cuenta_id
            request.env['portal.chat.message'].sudo().create(vals)
            try:
                login.sudo().with_context(portal_chatter_login_id=login.id).message_post(
                    body='<b>Mensaje de soporte de {}:</b><br/>{}'.format(
                        login.name or 'Contratista', body.strip()
                    ),
                    subtype_xmlid='mail.mt_note',
                )
            except Exception:
                pass  # notificacion interna opcional; no bloquea el envio
            return {'ok': True}
        except Exception as e:
            _logger.exception("Error en chat_send_message: %s", e)
            return {'error': str(e)}

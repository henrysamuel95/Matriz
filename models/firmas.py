from odoo import models, fields, api, exceptions
from odoo.http import request
import hashlib
import json


def _get_request_ip():
    if not request or not hasattr(request, 'httprequest'):
        return 'SISTEMA_INTERNO'

    headers = request.httprequest.headers
    for header_name in ('CF-Connecting-IP', 'True-Client-IP', 'X-Real-IP'):
        header_value = headers.get(header_name)
        if header_value:
            return header_value.split(',')[0].strip()

    forwarded_for = headers.get('X-Forwarded-For')
    if forwarded_for:
        ip_candidates = [ip.strip() for ip in forwarded_for.split(',') if ip.strip()]
        if ip_candidates:
            for ip_candidate in ip_candidates:
                if not ip_candidate.startswith(('127.', '172.17.', '10.0.')):
                    return ip_candidate
            return ip_candidates[0]

    forwarded = headers.get('Forwarded')
    if forwarded:
        for part in forwarded.split(';'):
            if part.strip().lower().startswith('for='):
                return part.split('=', 1)[1].strip().strip('"')

    return request.httprequest.remote_addr or 'Desconocida'


def obtener_mac():
    if not request or not hasattr(request, 'session'):
        return 'SISTEMA_INTERNO'
    return str(request.session.sid)


def obtener_dispositivo():
    if not request or not hasattr(request, 'httprequest'):
        return 'Desconocido'
    return request.httprequest.headers.get('User-Agent', 'Desconocido')


class SignatureMixin(models.Model):
    _name = 'signature.mixin'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Firmas y trazabilidad'

    def message_post(self, **kwargs):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        is_public_request = bool(
            request and hasattr(request, 'httprequest') and request.httprequest.path.startswith('/public/')
        )
        login = self.env['login'].sudo()
        if portal_login_id:
            login = login.browse(portal_login_id)
        elif is_public_request and len(self) == 1:
            login = self._portal_login_from_origin()
        if login and login.exists() and not kwargs.get('author_id'):
            partner = login._get_portal_chatter_partner()
            if partner:
                kwargs['author_id'] = partner.id
        return super(SignatureMixin, self).message_post(**kwargs)

    name = fields.Char(string="Referencia de Firma", default="Nueva firma")
 
    # --- ORIGEN ---
    modelo_origen = fields.Char(string="Modelo", readonly=True, tracking=True)
    registro_origen_id = fields.Integer(string="ID del Registro", readonly=True, tracking=True)
    nombre_documento = fields.Char(string="Nombre", readonly=True, tracking=True)

    # --- FIRMA ---
    signature_image = fields.Binary(string="Firma (Imagen)", tracking=True)
    signature_accepted = fields.Boolean(string="Firma Digital", tracking=True, readonly=True)
    
    # --- TRAZABILIDAD ---
    signature_hash = fields.Char(string="SHA-256", readonly=True, copy=False, tracking=True)
    signature_mac = fields.Char(string="Dirección MAC", readonly=True, copy=False, tracking=True) # REEMPLAZA A LA IP
    signature_device = fields.Char(string="Dispositivo / Navegador", readonly=True, copy=False, tracking=True)
    signature_date = fields.Datetime(string="Fecha de firma", readonly=True, copy=False)
    signature_user_id = fields.Many2one('res.users', string="Firmado por", default=lambda self: self.env.user, tracking=True)
    signature_partner_id = fields.Many2one('res.partner', string="Firmante", readonly=True, copy=False, tracking=True)
    signature_display_partner_id = fields.Many2one(
        'res.partner',
        string="Firmado por",
        compute='_compute_signature_display_partner_id',
    )
    signature_ip = fields.Char(string="Dirección IP", readonly=True, copy=False, tracking=True)


    tipo_accion = fields.Selection([
        ('firma', 'Firma Registrada'),
        ('refirma', 'Firma Reemplazada por Cambio'),
        ('eliminacion', 'Firma Eliminada')
    ], string="Acción", default='firma')

    def _portal_login_from_request(self):
        portal_login_id = self.env.context.get('portal_chatter_login_id')
        Login = self.env['login'].sudo()
        if portal_login_id:
            login = Login.browse(portal_login_id)
            if login.exists():
                return login

        if request and hasattr(request, 'httprequest'):
            user_id = request.httprequest.cookies.get('user_id')
            token = request.httprequest.cookies.get('user_token')
            if user_id and str(user_id).isdigit() and token:
                login = Login.search([
                    ('id', '=', int(user_id)),
                    ('token', '=', token),
                ], limit=1)
                if login:
                    return login

        return Login

    def _portal_login_from_origin(self):
        self.ensure_one()
        Login = self.env['login'].sudo()
        model_name = (self.modelo_origen or '').strip()
        model_aliases = {
            'Declaración Juramentada': 'declaracion_juramentada',
            'Declaracion Juramentada': 'declaracion_juramentada',
            'Informe de Actividades': 'informe_actividades',
            'Acta Entrega Documentos': 'acta_entrega_documentos',
            'Entrega de Documentos Cuenta de Cobro': 'acta_entrega_documentos',
            'Cuenta de Cobro': 'cuentas_de_cobros',
            'Contratista / Proveedor': 'contratista_proveedor',
            'Contratista Proveedor': 'contratista_proveedor',
        }
        model_name = model_aliases.get(model_name, model_name)

        if not model_name or not self.registro_origen_id or model_name not in self.env.registry.models:
            return self._portal_login_from_signature_text()

        origin = self.env[model_name].sudo().browse(self.registro_origen_id)
        if not origin.exists():
            return self._portal_login_from_signature_text()

        login = self._portal_login_from_origin_record(origin)
        return login if login and login.exists() else self._portal_login_from_signature_text()

    def _portal_login_from_origin_record(self, origin):
        Login = self.env['login'].sudo()

        if not origin:
            return Login

        if 'login_id' in origin._fields and origin.login_id:
            return origin.login_id.sudo()

        if origin._name == 'acta_entrega_documentos' and origin.cuenta_id:
            cuenta = origin.cuenta_id.sudo()
            if 'login_id' in cuenta._fields and cuenta.login_id:
                return cuenta.login_id.sudo()

        if origin._name == 'declaracion_juramentada' and origin.relacion:
            cuenta = origin.relacion.sudo()
            if 'login_id' in cuenta._fields and cuenta.login_id:
                return cuenta.login_id.sudo()

        if origin._name == 'informe_actividades' and origin.cuenta_cobro_id:
            cuenta = origin.cuenta_cobro_id.sudo()
            if 'login_id' in cuenta._fields and cuenta.login_id:
                return cuenta.login_id.sudo()

        if origin._name == 'cuentas_de_cobros':
            domain = []
            if getattr(origin, 'login_id', False):
                return origin.login_id.sudo()
            if getattr(origin, 'documento_de_identificacion', False):
                domain.append(('documento_de_identidad', '=', origin.documento_de_identificacion))
            if getattr(origin, 'correo_facturacion', False):
                domain.append(('coreo', '=', origin.correo_facturacion))
            if domain:
                return Login.search(['|'] + domain[:2] if len(domain) > 1 else domain, limit=1)

        if origin._name == 'contratista_proveedor':
            login = Login.search([('contratistas', '=', origin.id)], limit=1)
            if login:
                return login
            email = getattr(origin, 'correo_facturacion', False) or getattr(origin, 'email', False)
            documento = getattr(origin, 'nit', False) or getattr(origin, 'cedula', False)
            domain = []
            if email:
                domain.append(('coreo', '=', email))
            if documento:
                domain.append(('documento_de_identidad', '=', documento))
            if domain:
                return Login.search(['|'] + domain[:2] if len(domain) > 1 else domain, limit=1)

        return Login

    def _portal_login_from_signature_text(self):
        Login = self.env['login'].sudo()
        name = (self.nombre_documento or '').strip()
        if name:
            login = Login.search([('name', '=ilike', name)], limit=1)
            if login:
                return login
            partner = self.env['res.partner'].sudo().search([('name', '=ilike', name)], limit=1)
            if partner:
                login = Login.search([
                    '|',
                    ('coreo', '=', partner.email or ''),
                    ('contratistas.partner_id', '=', partner.id),
                ], limit=1)
                if login:
                    return login
        return Login

    def _portal_signature_partner(self):
        self.ensure_one()
        login = self._portal_login_from_request() or self._portal_login_from_origin()
        if login and login.exists():
            partner = login._get_portal_chatter_partner()
            if partner:
                return partner
        return self.signature_user_id.partner_id or self.env.user.partner_id

    def _portal_partner_from_login_readonly(self, login):
        if not login or not login.exists():
            return self.env['res.partner'].sudo()
        if 'portal_partner_id' in login._fields and login.portal_partner_id:
            return login.portal_partner_id.sudo()
        if (
            login.contratistas
            and 'partner_id' in login.contratistas._fields
            and login.contratistas.partner_id
        ):
            return login.contratistas.partner_id.sudo()
        return self.env['res.partner'].sudo()

    @api.depends('signature_partner_id', 'signature_user_id', 'modelo_origen', 'registro_origen_id')
    def _compute_signature_display_partner_id(self):
        public_user = self.env.ref('base.public_user', raise_if_not_found=False)
        for record in self:
            partner = record.signature_partner_id
            if not partner and public_user and record.signature_user_id == public_user:
                login = record._portal_login_from_origin()
                partner = record._portal_partner_from_login_readonly(login)
            if not partner and record.nombre_documento:
                partner = self.env['res.partner'].sudo().search([('name', '=ilike', record.nombre_documento.strip())], limit=1)
            if not partner and record.signature_user_id:
                partner = record.signature_user_id.partner_id
            record.signature_display_partner_id = partner

    @api.model_create_multi
    def create(self, vals_list):
        records = super(SignatureMixin, self).create(vals_list)
        for rec in records:
            if rec.signature_hash:
                tipo_firma = "Firma Digital" if rec.signature_accepted and not rec.signature_image else "Imagen de firma"
                
                body = f"""
                <div style="font-family: Arial, sans-serif;">
                    <h5 style="color: #2c3e50; margin-bottom: 5px;">🖋️ <b>Resumen de evidencia digital</b></h5>
                    <ul style="margin-top: 5px;">
                        <li><b>Origen:</b> {rec.modelo_origen} ({rec.nombre_documento})</li>
                        <li><b>Método:</b> {tipo_firma}</li>
                        <li><b>Usuario:</b> {(rec.signature_display_partner_id.name or rec.signature_user_id.name)}</li>
                        <li><b>IP:</b> {rec.signature_ip or 'No registrada'}</li>
                        <li><b>MAC:</b> {rec.signature_mac}</li>
                        <li><b>Dispositivo:</b> {rec.signature_device}</li>
                        <li><b>Fecha:</b> {rec.signature_date}</li>
                        <li style="word-break: break-all;"><b>Hash:</b> <span style="color: #7c7bad;">{rec.signature_hash}</span></li>
                    </ul>
                </div>
                """
                rec.message_post(body=body)
        return records

    def write(self, vals):
        if 'tipo_accion' in vals and vals['tipo_accion'] == 'eliminacion':
            self.message_post(body="<b style='color: red;'>⚠️ Esta firma ha sido marcada como ELIMINADA en el documento origen.</b>")
        elif 'tipo_accion' in vals and vals['tipo_accion'] == 'refirma':
            self.message_post(body="<b style='color: #0b3d91;'>↻ Esta firma fue reemplazada porque el documento cambió y debe firmarse nuevamente.</b>")
        return super(SignatureMixin, self).write(vals)

    def _get_data_to_hash(self):
        return str(self.id)

    def action_generate_signature(self):
        """Procesa la firma, recolecta MAC/Dispositivo y genera SHA-256."""
        for record in self:
            if not record.signature_image and not record.signature_accepted:
                raise exceptions.UserError("Debe proporcionar una imagen de firma o marcar la casilla de aceptación.")

            current_user = self.env.user
            
            ip_address = _get_request_ip()
            mac_address = obtener_mac()
            device_info = obtener_dispositivo()

            data_to_hash = {
                'model': record._name,
                'record_id': record.id,
                'user_id': current_user.id,
                'ip': ip_address,
                'mac': mac_address,
                'device': device_info,
                'date': fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'extra_info': record._get_data_to_hash(),
                'has_image': bool(record.signature_image),
                'accepted': record.signature_accepted 
            }
            
            data_string = json.dumps(data_to_hash, sort_keys=True).encode('utf-8')
            sha256_hash = hashlib.sha256(data_string).hexdigest()

            record.write({
                'signature_mac': mac_address,
                'signature_device': device_info,
                'signature_user_id': current_user.id,
                'signature_partner_id': record._portal_signature_partner().id,
                'signature_ip': ip_address,
                'signature_date': fields.Datetime.now(),
                'signature_hash': sha256_hash,
            })

            if hasattr(record, 'message_post'):
                msg = f"""
                <ul>
                    <li><b>Documento firmado digitalmente.</b></li>
                    <li><b>Usuario:</b> {record.signature_display_partner_id.name or current_user.name}</li>
                    <li><b>IP:</b> {ip_address}</li>
                    <li><b>MAC / Dispositivo:</b> {mac_address} | {device_info}</li>
                    <li><b>SHA-256:</b> {sha256_hash}</li>
                </ul>
                """
                record.message_post(body=msg)



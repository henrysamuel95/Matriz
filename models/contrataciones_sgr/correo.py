from html import escape

from odoo import models
from odoo.exceptions import UserError

from .estados import ESTADOS_CONTRATACION_SGR, EstadoContratacionSgr


ETIQUETAS_ESTADO_SGR = dict(ESTADOS_CONTRATACION_SGR)


class ContratacionesSgrCorreo(models.Model):
    _inherit = 'contrataciones_sgr'

    def _nombre_estado_sgr(self, estado):
        return ETIQUETAS_ESTADO_SGR.get(estado, estado or '')

    def _observaciones_estado_sgr(self, estado):
        if estado == EstadoContratacionSgr.FINANCIERO.value:
            return self.observaciones_finacie or ''
        if estado and estado.startswith('juridico'):
            return self.observaciones_juri or ''
        return self.observaciones or ''

    def _destinatarios_correo_sgr(self, destinatarios):
        self.ensure_one()
        destinatarios = destinatarios.sudo().filtered(lambda user: user.active and not user.share)
        if not destinatarios:
            raise UserError('No hay usuarios activos para recibir la notificación SGR.')
        sin_correo = destinatarios.filtered(lambda user: not user.email)
        if sin_correo:
            raise UserError(
                'Estos usuarios no tienen correo configurado: %s.'
                % ', '.join(sin_correo.mapped('name'))
            )
        if not (self.env.user.email or self.env.company.email):
            raise UserError('Configura un correo para el usuario actual o la compañía antes de continuar.')
        return destinatarios

    def _enviar_correo_flujo_sgr(
        self, destinatarios, titulo, area_destino, mensaje, observaciones, boton_texto,
        es_devolucion=False,
    ):
        self.ensure_one()
        destinatarios = self._destinatarios_correo_sgr(destinatarios)
        color = '#f28c28' if es_devolucion else '#043484'
        borde_observacion = '#f28c28' if es_devolucion else '#ffcc00'
        fondo_observacion = '#fff3e0' if es_devolucion else '#fffacd'
        color_observacion = '#8a4b08' if es_devolucion else '#856004'
        proyecto = self.proyecto_relacionado
        nombre_proyecto = proyecto.name or proyecto.new_name or proyecto.display_name
        contratista = self.nombre_contratista or self.name or ''
        base_url = (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')
        formulario_url = (
            f'{base_url}/web#id={self.id}&model=contrataciones_sgr'
            f'&view_type=form&cids={self.env.company.id}'
        )
        observaciones_html = escape(observaciones or '').replace('\n', '<br/>')
        bloque_observaciones = (
            f'<div style="margin-top:20px;padding:10px;border:1px solid {borde_observacion};'
            f'background-color:{fondo_observacion};border-radius:4px;">'
            f'<strong>Observaciones:</strong><br/>'
            f'<em style="color:{color_observacion};">{observaciones_html}</em></div>'
            if observaciones_html else ''
        )
        remitente = self.env.user.email or self.env.company.email
        for destinatario in destinatarios:
            body_html = f'''
                <div style="font-family:Arial,sans-serif;font-size:14px;color:#444;background-color:#f8f8f8;padding:20px;border-radius:5px;">
                    <div style="background-color:{color};color:white;padding:10px 15px;border-radius:4px 4px 0 0;text-align:center;">
                        <h3 style="margin:0;font-size:18px;">{escape(titulo)}: <strong>{escape(nombre_proyecto)}</strong></h3>
                    </div>
                    <div style="background-color:white;padding:20px 15px;border:1px solid #ddd;border-top:none;border-radius:0 0 4px 4px;">
                        <p>Hola, <strong>{escape(destinatario.name)}</strong>:</p>
                        <p>{escape(mensaje)}</p>
                        <div style="margin:15px 0;padding:10px;background-color:#f9f9f9;border-left:4px solid {color};">
                            <strong>Contratista:</strong> {escape(contratista)}<br/>
                            <strong>Proyecto:</strong> {escape(nombre_proyecto)}<br/>
                            <strong>Área destino:</strong> {escape(area_destino)}
                        </div>
                        {bloque_observaciones}
                        <p style="text-align:center;margin-top:30px;">
                            <a href="{escape(formulario_url, quote=True)}" style="background-color:{color};color:white;padding:10px 20px;text-decoration:none;border-radius:5px;font-weight:bold;display:inline-block;">{escape(boton_texto)}</a>
                        </p>
                        <p style="margin-top:20px;font-size:12px;color:#999;">SGO - mensaje de notificación SGR</p>
                    </div>
                </div>
            '''
            self.env['mail.mail'].sudo().create({
                'subject': f'{titulo} - {contratista} - {nombre_proyecto}',
                'email_from': remitente,
                'email_to': destinatario.email,
                'body_html': body_html,
                'auto_delete': True,
            }).send(raise_exception=True)

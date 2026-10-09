import logging

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request


_logger = logging.getLogger(__name__)


class SolicitudPolizasPortalController(http.Controller):

    @staticmethod
    def _proteger_respuesta_privada(response):
        """Evita almacenar o filtrar la URL que contiene el bearer token."""
        response.headers['Cache-Control'] = 'no-store, private, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response

    def _render_privado(self, plantilla, valores=None):
        valores = dict(valores or {})
        if plantilla == 'Matriz.solicitud_polizas_portal' and valores.get('solicitud'):
            valores.setdefault(
                'lineas_cargables',
                valores['solicitud']._lineas_cargables_portal(),
            )
        return self._proteger_respuesta_privada(
            request.render(plantilla, valores)
        )

    def _solicitud_valida(self, solicitud_id, token):
        solicitud = request.env[
            'contrataciones_fuv.solicitud.poliza'
        ].sudo().browse(solicitud_id).exists()
        if not solicitud or not solicitud._token_portal_valido(token):
            return False
        return solicitud

    @http.route(
        '/solicitud-polizas/<int:solicitud_id>/<string:token>',
        type='http', auth='public', website=True, methods=['GET'], sitemap=False,
    )
    def solicitud_polizas(self, solicitud_id, token, **kwargs):
        solicitud = self._solicitud_valida(solicitud_id, token)
        if not solicitud:
            return self._render_privado('Matriz.solicitud_polizas_portal_error')
        return self._render_privado('Matriz.solicitud_polizas_portal', {
            'solicitud': solicitud,
            'token': token,
            'carga_exitosa': kwargs.get('carga_exitosa'),
        })

    @http.route(
        '/solicitud-polizas/cargar',
        type='http', auth='public', website=True, methods=['POST'], sitemap=False,
        csrf=True,
    )
    def cargar_poliza(self, solicitud_id=None, linea_id=None, token=None, **post):
        if not str(solicitud_id or '').isdigit():
            return self._render_privado('Matriz.solicitud_polizas_portal_error')
        solicitud = self._solicitud_valida(int(solicitud_id), token)
        if not solicitud:
            return self._render_privado('Matriz.solicitud_polizas_portal_error')
        if not str(linea_id or '').isdigit():
            return self._render_privado('Matriz.solicitud_polizas_portal_error')
        linea = solicitud.linea_ids.filtered(
            lambda item: item.id == int(linea_id)
        )
        if not linea:
            return self._render_privado('Matriz.solicitud_polizas_portal_error')
        archivo = request.httprequest.files.get('archivo')
        era_correccion = solicitud.estado == 'correccion'
        try:
            if not archivo:
                raise UserError('Debe seleccionar el archivo de la póliza.')
            solicitud._cargar_archivo_desde_portal(
                linea,
                archivo,
                token=token,
            )
        except UserError as error:
            # También las excepciones funcionales pueden aparecer después de
            # una escritura (por ejemplo, una restricción personalizada). Como
            # aquí se devuelve una respuesta normal, revertimos antes de mostrarla.
            request.env.cr.rollback()
            solicitud = self._solicitud_valida(int(solicitud_id), token)
            if not solicitud:
                return self._render_privado(
                    'Matriz.solicitud_polizas_portal_error'
                )
            return self._render_privado('Matriz.solicitud_polizas_portal', {
                'solicitud': solicitud,
                'token': token,
                'error_carga': error.args[0],
            })
        except Exception:
            _logger.exception('Error al cargar una póliza desde el enlace privado.')
            # Al convertir la excepción en una respuesta HTTP normal, Odoo no
            # ejecutaría su rollback automático. Se revierte explícitamente para
            # no confirmar documentos, líneas o tokens a medio actualizar.
            request.env.cr.rollback()
            solicitud = self._solicitud_valida(int(solicitud_id), token)
            if not solicitud:
                return self._render_privado(
                    'Matriz.solicitud_polizas_portal_error'
                )
            return self._render_privado('Matriz.solicitud_polizas_portal', {
                'solicitud': solicitud,
                'token': token,
                'error_carga': 'No fue posible cargar el archivo. Inténtelo nuevamente.',
            })
        solicitud.invalidate_recordset(['portal_token', 'estado'])
        if era_correccion and not solicitud._token_portal_valido(token):
            return request.redirect('/solicitud-polizas/correccion-recibida')
        if not solicitud.portal_token and not solicitud._hay_lineas_portal_pendientes():
            return request.redirect('/solicitud-polizas/completada')
        return request.redirect(
            '/solicitud-polizas/%s/%s?carga_exitosa=1' % (solicitud.id, token)
        )

    @http.route(
        '/solicitud-polizas/cargar-lote',
        type='http', auth='public', website=True, methods=['POST'], sitemap=False,
        csrf=True,
    )
    def cargar_lote(self, solicitud_id=None, token=None, **post):
        if not str(solicitud_id or '').isdigit():
            return self._render_privado('Matriz.solicitud_polizas_portal_error')
        solicitud = self._solicitud_valida(int(solicitud_id), token)
        if not solicitud:
            return self._render_privado('Matriz.solicitud_polizas_portal_error')

        era_correccion = solicitud.estado == 'correccion'
        archivos = request.httprequest.files
        permitidas = {
            'archivo_%s' % linea.id: linea
            for linea in solicitud._lineas_cargables_portal()
        }
        try:
            cargas = []
            for nombre in archivos.keys():
                seleccionados = archivos.getlist(nombre)
                if nombre not in permitidas or len(seleccionados) != 1:
                    raise UserError('El formulario incluye una póliza no disponible.')
                archivo = seleccionados[0]
                if archivo and archivo.filename:
                    cargas.append((permitidas[nombre], archivo))
            cantidad = solicitud._cargar_lote_desde_portal(cargas, token)
        except UserError as error:
            request.env.cr.rollback()
            solicitud = self._solicitud_valida(int(solicitud_id), token)
            if not solicitud:
                return self._render_privado('Matriz.solicitud_polizas_portal_error')
            return self._render_privado('Matriz.solicitud_polizas_portal', {
                'solicitud': solicitud,
                'token': token,
                'error_carga': error.args[0],
            })
        except Exception:
            _logger.exception('Error al cargar el lote de pólizas del enlace privado.')
            request.env.cr.rollback()
            solicitud = self._solicitud_valida(int(solicitud_id), token)
            if not solicitud:
                return self._render_privado('Matriz.solicitud_polizas_portal_error')
            return self._render_privado('Matriz.solicitud_polizas_portal', {
                'solicitud': solicitud,
                'token': token,
                'error_carga': 'No fue posible cargar los archivos. Inténtelo nuevamente.',
            })

        solicitud.invalidate_recordset(['portal_token', 'estado'])
        if era_correccion and not solicitud._token_portal_valido(token):
            return request.redirect('/solicitud-polizas/correccion-recibida')
        if not solicitud.portal_token and not solicitud._hay_lineas_portal_pendientes():
            return request.redirect('/solicitud-polizas/completada')
        return request.redirect(
            '/solicitud-polizas/%s/%s?carga_exitosa=%s' % (
                solicitud.id, token, cantidad,
            )
        )

    @http.route(
        '/solicitud-polizas/completada',
        type='http', auth='public', website=True, methods=['GET'], sitemap=False,
    )
    def solicitud_polizas_completada(self, **kwargs):
        return self._render_privado('Matriz.solicitud_polizas_portal_completada')

    @http.route(
        '/solicitud-polizas/correccion-recibida',
        type='http', auth='public', website=True, methods=['GET'], sitemap=False,
    )
    def solicitud_polizas_correccion_recibida(self, **kwargs):
        return self._render_privado(
            'Matriz.solicitud_polizas_portal_correccion_recibida'
        )

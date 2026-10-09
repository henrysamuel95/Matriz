# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import json

class ApiContrataciones(http.Controller):

    # 🔐 Clave simple tipo token (puedes cambiarla o guardarla en sistema)
    TOKEN = "12345TOKENAPI"

    @http.route('/api/contrataciones_fuv', type='http', auth='none', methods=['GET'], csrf=False)
    def get_contrataciones(self, **kwargs):
        """Devuelve lista de contrataciones FUV en formato JSON"""
        token = request.httprequest.headers.get('Authorization')
        if token != f"Bearer {self.TOKEN}":
            return request.make_response(
                json.dumps({'error': 'Unauthorized'}),
                headers={'Content-Type': 'application/json'},
                status=401
            )

        records = request.env['contrataciones_fuv'].sudo().search([])
        data = []
        for rec in records:
            data.append({
                'id': rec.id,
                'creador': rec.create_uid.name if rec.create_uid else '',
                'nombre': rec.name,
                'representante_legal': rec.r_legal,
                'estado': rec.estado_contratacion,
                'id_contrato': rec.id_del_contrato,
                'correo': rec.correo,
                'telefono': rec.telefono_contratista,
                'duracion': rec.duracion,
                'valor_otrosi': rec.valor_del_otrosi,
                'valor_contrato': rec.valor_total_contrato,
                'valor_total': rec.total,
                'tipo_persona': rec.select_persona,
                'fecha_creacion': rec.fch_creacion,
                'fecha_inicio': rec.fecha_iniciacion,
                'fecha_fin': rec.fecha_terminacion,
                'fecha_modificacion': rec.write_date,
                'rpc': rec.compromiso_presupuestal,
                'proyecto': rec.proyecto_relacionado.name if rec.proyecto_relacionado else '',
                'documento': rec.documento_identidad,
                'nit': rec.nit,
            })

        return request.make_response(
            json.dumps(data, default=str),
            headers={'Content-Type': 'application/json'},
            status=200
        )

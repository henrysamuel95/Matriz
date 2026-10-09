import json
from odoo import http
from odoo.exceptions import AccessDenied
from odoo.http import request, Response

"""
API pública de solo lectura para consultar el árbol completo de un proyecto
(modelo `contratacion`) o de todos los proyectos del sistema en una sola
respuesta JSON.

Uso:
    GET /api/v1/proyectos            -> todos los proyectos
    GET /api/v1/proyectos?id=123     -> un solo proyecto (mismo formato, payload menor)
    Header requerido: api-key: <API Key personal de un usuario administrador>

Autenticación y autorización (ver `_validar_api_key_sistema`):
    No usa sesión ni cookies de Odoo (auth='public'). El llamante debe enviar,
    en el header 'api-key', una API Key personal generada por un usuario desde
    su perfil (Preferencias > Seguridad de la cuenta > Nueva API Key). Esa key
    se valida contra `res.users.apikeys` (claves hasheadas en BD, revocables
    individualmente) y, además, el usuario dueño de la key debe pertenecer al
    grupo administrador del módulo (Matriz.group_soporte) — cualquier otro
    usuario, aunque tenga una API Key válida, recibe 403. Esto es intencional:
    el payload expone información sensible (PII de contratistas, cifras
    financieras SIESA) de todos los proyectos.

Estructura de la respuesta (`obtener_proyectos`):
    Seguimos la cadena de dominio central del módulo (ver CLAUDE.md /
    ARQUITECTURA.md): cada proyecto (`contratacion`, la raíz) se serializa con
    sus ramas ya resueltas — presupuesto (mcb), ejecución técnica, cierre,
    contratos de personal (contrataciones_fuv) con sus otrosí y liquidaciones
    anidadas, órdenes de compra, viáticos, y los pagos/cobros reales tal como
    quedaron registrados en SIESA (única fuente de verdad financiera; Odoo
    nunca escribe en SIESA, solo lee).
"""

# Grupo requerido para usar esta API: solo administradores del módulo.
ADMIN_GROUP_XMLID = 'Matriz.group_soporte'
# Scope dedicado para las API Keys pensadas para esta integración. Las claves
# generadas desde el panel de usuario (Preferencias > Seguridad de la cuenta)
# no tienen scope asignado (False), así que se acepta también ese caso.
API_KEY_SCOPE = 'matriz_api_proyectos'


class ApiProyectosController(http.Controller):

    def _autenticar_api_key(self, api_key):
        """Valida la API Key contra res.users.apikeys y devuelve el uid del dueño.

        Prueba primero el scope dedicado de esta integración y, si no matchea,
        cae al scope genérico (False) que usan las keys creadas desde el panel
        estándar de usuario. Lanza AccessDenied si la key no es válida en
        ninguno de los dos scopes.
        """
        ApiKeys = request.env['res.users.apikeys'].sudo()
        for scope in (API_KEY_SCOPE, False):
            try:
                return ApiKeys._check_credentials(scope=scope, key=api_key)
            except AccessDenied:
                continue
        raise AccessDenied()

    def _validar_api_key_sistema(self):
        """Puerta de seguridad del endpoint: exige una API Key personal válida
        (ver `_autenticar_api_key`) cuyo dueño además sea administrador
        (grupo `Matriz.group_soporte`).

        Devuelve una tupla (es_valido, uid, status_code, mensaje):
        - (False, None, 401, ...) si falta el header o la key no es válida.
        - (False, None, 403, ...) si la key es válida pero el usuario no es admin.
        - (True, uid, 200, "") si todo es correcto.
        El mensaje de error nunca incluye la key recibida ni la esperada, para
        no filtrar credenciales en la respuesta.
        """
        api_key_recibida = request.httprequest.headers.get('api-key')
        if not api_key_recibida:
            return False, None, 401, "Falta el header 'api-key' en la petición."

        try:
            uid = self._autenticar_api_key(api_key_recibida.strip())
        except AccessDenied:
            return False, None, 401, "API Key inválida."

        usuario = request.env['res.users'].sudo().browse(uid)
        if not usuario.exists() or not usuario.has_group(ADMIN_GROUP_XMLID):
            return False, None, 403, "El usuario asociado a esta API Key no tiene permisos de administrador."

        return True, uid, 200, ""

    # ------------------------------------------------------------------
    # Serializadores por rama del árbol de dominio
    #
    # Cada método toma un recordset de Odoo y devuelve un dict/list plano
    # listo para JSON. Están separados uno por modelo para que la forma del
    # payload sea fácil de rastrear hasta el modelo de origen; no hay lógica
    # de negocio aquí, solo mapeo de campos.
    # ------------------------------------------------------------------

    def _serializar_contratista(self, contratista):
        """Datos del tercero (contratista_proveedor) vinculado a un contrato FUV.
        Distingue persona natural (cedula) de persona jurídica (nit + razón social)."""
        if not contratista:
            return {}
        es_juridica = contratista.clase_persona == 'persona_juridica'
        return {
            'nombre': contratista.name or '',
            'clase_persona': contratista.clase_persona or '',
            'identificacion': (contratista.nit if es_juridica else contratista.cedula) or '',
            'razon_social': contratista.razon_social or '',
            'representante_legal': contratista.nombre_r_legal or '',
            'correo': contratista.email_contratante or '',
            'direccion': contratista.direccion_fisica or '',
            'telefono': contratista.numero_telefono or '',
        }

    def _serializar_otrosi(self, otrosi):
        """Una modificación contractual (modelo `otro_si`) de un contrato FUV: adición,
        prórroga, suspensión, etc. No incluye los ~300 campos de aprobación/documentos
        del modelo, solo el resumen (estado y valores)."""
        return {
            'id': otrosi.id,
            'estado': otrosi.estado_contratacion,
            'valor_adicion': otrosi.valor_total_contrato or 0.0,
            'fecha_inicio': str(otrosi.fecha_iniciacion) if otrosi.fecha_iniciacion else '',
            'fecha_fin': str(otrosi.fecha_terminacion) if otrosi.fecha_terminacion else '',
        }

    def _serializar_cierre_proceso(self, cierre_proceso):
        """Liquidación/cierre individual de un contrato FUV (`cierre.proceso`),
        distinto del cierre general del proyecto (ver `_serializar_cierre`)."""
        return {
            'id': cierre_proceso.id,
            'tipo_acta': cierre_proceso.tipo_acta,
            'estado': cierre_proceso.estado,
            'fecha_solicitud': str(cierre_proceso.fecha_solicitud) if cierre_proceso.fecha_solicitud else '',
            'porcentaje_ejecucion_financiera': cierre_proceso.porcentaje_ejecucion_financiera or 0.0,
            'saldo_por_liberar': cierre_proceso.saldo_por_liberar or 0.0,
            'rfc_liberado': cierre_proceso.rfc_liberado,
            'fecha_liberacion_rfc': str(cierre_proceso.fecha_liberacion_rfc) if cierre_proceso.fecha_liberacion_rfc else '',
        }

    def _serializar_fuv(self, fuv):
        """Un contrato de personal (`contrataciones_fuv`, el modelo más grande del
        sistema): el contratista, sus otrosí y las liquidaciones asociadas quedan
        anidados aquí para no obligar al consumidor a hacer llamadas adicionales."""
        return {
            'id': fuv.id,
            'estado': fuv.estado_contratacion,
            'contratista': self._serializar_contratista(fuv.contratista_proveedor_relacionado),
            'valor_total': fuv.valor_total_contrato or 0.0,
            'fecha_inicio': str(fuv.fecha_iniciacion) if fuv.fecha_iniciacion else '',
            'fecha_fin': str(fuv.fecha_terminacion) if fuv.fecha_terminacion else '',
            'objeto': fuv.objeto_contrato or '',
            'adiciones_otrosi': [self._serializar_otrosi(o) for o in fuv.otrosi_ids],
            'cierre_proceso': [self._serializar_cierre_proceso(cp) for cp in fuv.cierre_proceso_ids],
        }

    def _serializar_orden_compra(self, rec, estado, descripcion):
        """Orden de compra de bienes o de servicios. Un solo serializador para ambos
        modelos (`estado`/`descripcion` llegan resueltos desde el llamador porque cada
        modelo usa nombres de campo distintos para lo mismo)."""
        return {
            'id': rec.id,
            'estado': estado,
            'proveedor': rec.proveedor or '',
            'nit': rec.nit or '',
            'descripcion': descripcion or '',
            'valor_total': rec.total or 0.0,
        }

    def _serializar_actividad(self, actividad):
        """Actividad del marco lógico de ejecución técnica, con su avance presupuestal."""
        return {
            'id': actividad.id,
            'nombre': actividad.name or '',
            'estado': actividad.estado,
            'presupuesto_total_global': actividad.presupuesto_total_global or 0.0,
            'ejecutado_total_global': actividad.ejecutado_total_global or 0.0,
            'porcentaje_ejecucion': actividad.porcentaje_ejecucion or 0.0,
        }

    def _serializar_entregable2(self, e2):
        """Nivel más detallado del marco lógico (entregable dentro de un entregable),
        con el conteo de sus actividades por estado."""
        return {
            'id': e2.id,
            'nombre': e2.name or '',
            'verificador': e2.verificador or '',
            'unidad_medida': e2.unidad_medida or '',
            'cantidad': e2.cantidad or 0,
            'porcentaje': e2.porcentaje or 0.0,
            'duracion_periodos': e2.duracion_periodos or 0.0,
            'total_porcentaje': e2.total_porcentaje or 0.0,
            'actividades_completas': e2.actividades_completas_count or 0,
            'actividades_en_progreso': e2.actividades_en_progreso_count or 0,
            'actividades_retrasadas': e2.actividades_retrasadas_count or 0,
            'actividades_pendientes': e2.actividades_pendiente_count or 0,
        }

    def _serializar_entregable(self, entregable):
        """Entregable del marco lógico; agrupa sus entregable2 (ver arriba)."""
        return {
            'id': entregable.id,
            'entregables_2': [self._serializar_entregable2(e2) for e2 in entregable.entregable2_ids],
        }

    def _serializar_ejecucion(self, ejecucion):
        """Seguimiento técnico del proyecto (modelo `ejecucion`): raíz del árbol de
        marco lógico (actividades + entregables)."""
        return {
            'id': ejecucion.id,
            'estado': ejecucion.estado,
            'actividades': [self._serializar_actividad(a) for a in ejecucion.actividad_ids],
            'entregables': [self._serializar_entregable(e) for e in ejecucion.entregable_ids],
        }

    def _serializar_cierre(self, cierre):
        """Cierre general del proyecto (modelo `cierre`) con el estado de aprobación
        por cada área (coordinación, financiera, jurídica)."""
        return {
            'id': cierre.id,
            'estado': cierre.state,
            'cerrado': cierre.cerrado,
            'fecha_info_final': str(cierre.fecha_info_final) if cierre.fecha_info_final else '',
            'fecha_acta_final': str(cierre.fecha_acta_final) if cierre.fecha_acta_final else '',
            'coordinador_approved': cierre.coordinador_approved,
            'financiero_approved': cierre.financiero_approved,
            'juridico_approved': cierre.juridico_approved,
            'motivo_rechazo_juridico': cierre.motivo_rechazo_juridico or '',
            'motivo_rechazo_financiero': cierre.motivo_rechazo_financiero or '',
        }

    def _serializar_ficha_resumen(self, ficha):
        """Ficha resumen del proyecto: la aprobación inicial de alcance/valor antes
        de que arranque la ejecución."""
        return {
            'id': ficha.id,
            'estado_aprobacion': ficha.aprobado,
            'valor_contratar': ficha.valor_contratar or 0.0,
            'valor_total_proyecto': ficha.valor_total_proyecto or 0.0,
            'objeto': ficha.objeto or '',
            'modalidad_contratacion': ficha.modalidad_contratacion,
            'fecha_aprobacion': str(ficha.fecha_aprobacion) if ficha.fecha_aprobacion else '',
        }

    def _serializar_mcb(self, mcb):
        """Presupuesto del proyecto (modelo `mcb`/`new_mcb`, Marco de Costos y
        Beneficios): valores planeados en Odoo, no lo realmente causado en SIESA
        (eso viene en `pagos_siesa`/`cobros_siesa` dentro de la respuesta principal)."""
        return {
            'id': mcb.id,
            'centro_costo': mcb.centro_costo or '',
            'valor_total_final': mcb.valor_total2_final or 0.0,
            'valor_proyecto_convenio': mcb.valor_proyecto_convenio3 or 0.0,
            'suma_adicion_otrosi': mcb.suma_adicion_otro_si2 or 0.0,
            'contrapartida': mcb.contrapartida4 or 0.0,
            'suma_polizas': mcb.suma_polizas or 0.0,
            'costos_financieros_total': mcb.suma_costos_financieros_total or 0.0,
            'costos_tributarios_total': mcb.suma_costos_tributarios_total_valor or 0.0,
            'costos_produccion_total': mcb.suma_total_costos_produccion_N or 0.0,
            'total_a_facturar': mcb.total_a_facturar or 0.0,
        }

    @http.route('/api/v1/proyectos', type='http', auth='public', methods=['GET'], csrf=False)
    def obtener_proyectos(self, **kwargs):
        """GET /api/v1/proyectos[?id=<int>]

        Devuelve el árbol completo de uno o todos los proyectos (ver docstring
        del módulo). Requiere el header 'api-key' de un usuario administrador
        (`_validar_api_key_sistema`).

        Respuesta (200): {status, database, sql_count, total_proyectos, data: [...]}
        donde cada elemento de `data` es un proyecto serializado con las 10
        secciones anidadas (FUV, órdenes, viáticos, presupuesto, ejecución,
        cierre, ficha resumen, pagos/cobros SIESA — ver comentarios numerados
        más abajo).

        Errores: 400 si `id` no es numérico, 401/403 si la autenticación o
        autorización fallan (ver `_validar_api_key_sistema`).
        """

        # 1. Seguridad: solo administradores con API Key personal pueden usar este endpoint
        es_valido, uid, status_code, mensaje = self._validar_api_key_sistema()
        if not es_valido:
            return Response(json.dumps({'status': 'error', 'message': mensaje}), status=status_code, mimetype='application/json')

        # 2. Obtener datos saltando el método search() sobreescrito usando SQL puro.
        # Con 'id' se filtra a un solo proyecto (misma profundidad, payload menor).
        proyecto_id_filtro = kwargs.get('id')
        if proyecto_id_filtro:
            try:
                proyecto_id_filtro = int(proyecto_id_filtro)
            except (TypeError, ValueError):
                return Response(
                    json.dumps({'status': 'error', 'message': "El parámetro 'id' debe ser numérico."}),
                    status=400, mimetype='application/json'
                )
            request.env.cr.execute("SELECT id FROM contratacion WHERE id = %s", (proyecto_id_filtro,))
        else:
            request.env.cr.execute("SELECT id FROM contratacion")
        ids = [row[0] for row in request.env.cr.fetchall()]

        proyectos = request.env['contratacion'].sudo().browse(ids)
        sql_count = len(ids)

        resultado = []
        for p in proyectos:

            # 1. Contratos de Personal (FUV), con sus otrosí y procesos de liquidación anidados
            fuv_anidados = [self._serializar_fuv(fuv) for fuv in p.contratacion_ids]

            # 2. Órdenes de Compra de Servicios
            servicios_anidados = [
                self._serializar_orden_compra(s, s.estado_compra_servicios, s.descripcion_del_servicio)
                for s in p.contratacion_ids_s
            ]

            # 3. Órdenes de Compra de Bienes
            bienes_anidados = [
                self._serializar_orden_compra(b, b.estado_compra_bienes, b.descripcion)
                for b in p.contratacion_ids_c
            ]

            # 4. Viáticos
            viaticos_anidados = []
            for v in p.contratacion_ids_viaticos:
                viaticos_anidados.append({
                    'id_viatico': v.id,
                    'total_gastos': v.total_gastos,
                })

            # 5. MCB (Presupuesto)
            mcb_anidados = [self._serializar_mcb(mcb) for mcb in p.ejecucion4_ids]

            # 6. Ejecución (árbol de marco lógico: actividades y entregables)
            ejecucion_anidados = [self._serializar_ejecucion(e) for e in p.ejecucion2_ids]

            # 7. Cierre del proyecto
            cierre_anidados = [self._serializar_cierre(c) for c in p.ejecucion3_ids]

            # 8. Ficha Resumen
            ficha_resumen_anidados = [self._serializar_ficha_resumen(f) for f in p.ejecucion5_ids]

            # 9. Pagos SIESA — única fuente de verdad para pagos del proyecto/centro de costo
            pagos_siesa_anidados = []
            programas = request.env['programapagos'].sudo().search([('proyecto_relacionado', '=', p.id)])
            if programas:
                pagos = request.env['pagos.siesa.line'].sudo().search([('programapagos_id', 'in', programas.ids)])
                for pago in pagos:
                    pagos_siesa_anidados.append({
                        'id': pago.id,
                        'estado': getattr(pago, 'estado', ''),
                        'contratista': getattr(pago, 'contratista', ''),
                        'nit': getattr(pago, 'nit', ''),
                        'fecha_pago': str(getattr(pago, 'fecha_pago', '')) if getattr(pago, 'fecha_pago', False) else '',
                        'valor_pagado': getattr(pago, 'valor_pagado', 0.0),
                        'neto': getattr(pago, 'neto', 0.0),
                        'bruto': getattr(pago, 'bruto', 0.0),
                    })

            # 10. Cobros SIESA — única fuente de verdad para cobros del proyecto/centro de costo
            cobros_siesa_anidados = []
            ingresos = request.env['action_ingresar_pagos_proyecto'].sudo().search([('proyecto_relacionado', '=', p.id)])
            if ingresos:
                cobros = request.env['cobros.siesa.line'].sudo().search([('cobros_id', 'in', ingresos.ids)])
                for cobro in cobros:
                    cobros_siesa_anidados.append({
                        'id': cobro.id,
                        'estado': getattr(cobro, 'estado', ''),
                        'cliente': getattr(cobro, 'cliente', ''),
                        'fecha_recaudo': str(getattr(cobro, 'fecha_recaudo', '')) if getattr(cobro, 'fecha_recaudo', False) else '',
                        'por_cobrar': getattr(cobro, 'por_cobrar', 0.0),
                        'recaudado': getattr(cobro, 'recaudado', 0.0),
                        'pendiente': getattr(cobro, 'pendiente', 0.0),
                    })

            # Drive Folder (solo el enlace a la carpeta, sin listar archivos)
            drive_url = ''
            if hasattr(p, 'drive_folder_id') and p.drive_folder_id:
                drive_url = getattr(p.drive_folder_id, 'url', '') or getattr(p.drive_folder_id, 'enlace', '')

            # Objeto JSON principal
            proyecto_data = {
                'id_interno_odoo': p.id,
                'nombre_proyecto': p.new_name or p.name,
                'estado_coordinador': getattr(p, 'coordinador', ''),
                'url_drive': drive_url,

                'finanzas_odoo': {
                    'total_compras_bienes': p.total_bienes or 0.0,
                    'total_compras_servicios': p.total_compras or 0.0,
                    'suma_costos_fuv': getattr(p, 'suma_costos_fuv', 0.0),
                },

                'pagos_siesa': pagos_siesa_anidados,
                'cobros_siesa': cobros_siesa_anidados,
                'presupuesto_mcb': mcb_anidados,
                'ejecucion': ejecucion_anidados,
                'cierre': cierre_anidados,
                'ficha_resumen': ficha_resumen_anidados,

                'contratos_personal_fuv': fuv_anidados,
                'ordenes_servicios': servicios_anidados,
                'ordenes_bienes': bienes_anidados,
                'viaticos': viaticos_anidados
            }
            resultado.append(proyecto_data)

        payload = {
            'status': 'success',
            'database': request.env.cr.dbname,
            'sql_count': sql_count,
            'total_proyectos': len(resultado),
            'data': resultado
        }

        return Response(json.dumps(payload), status=200, mimetype='application/json')

import hashlib
import json
from functools import wraps
from odoo import models, fields, api, exceptions
from odoo.http import request

# =========================================================
# Decorador de trazabilidad de firmas electrónicas ("chulito").
#
# Se usa sobre el write() (o un helper privado llamado desde write()) de
# cualquier modelo que tenga uno o más pares "checkbox de firma" + "hash" +
# "log". Antes de persistir, detecta si el write toca alguno de esos
# checkboxes y, si es así, crea/actualiza el registro de auditoría en
# signature.mixin (ver models/firmas.py) con IP, dispositivo y hash SHA-256.
#
# Usuarios actuales: certificacion_cumplimiento.py, informe_actividades.py,
# declaracion_juramentada.py, inventario_documental.py, gestion_documental.py.
# =========================================================

# =========================================================
# 1. FUNCIONES DE IDENTIFICACIÓN REAL (IP + DISPOSITIVO)
# =========================================================

def obtener_ip_real():
    """IP del cliente HTTP, priorizando cabeceras de proxy/CDN.

    Orden: CF-Connecting-IP / True-Client-IP / X-Real-IP (proxies confiables
    conocidos) > X-Forwarded-For (primera IP que no sea de rango privado
    interno) > Forwarded > remote_addr. Fuera de un request web (ej. cron,
    shell) devuelve 'SISTEMA_INTERNO'.
    """
    if not request or not hasattr(request, 'httprequest'):
        return 'SISTEMA_INTERNO'
    
    headers = request.httprequest.headers
    for header_name in ('CF-Connecting-IP', 'True-Client-IP', 'X-Real-IP'):
        header_value = headers.get(header_name)
        if header_value:
            return header_value.split(',')[0].strip()
    
    x_forwarded_for = headers.get('X-Forwarded-For')
    if x_forwarded_for:
        ip_candidates = [ip.strip() for ip in x_forwarded_for.split(',') if ip.strip()]
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

def obtener_id_unico_dispositivo():
    """Id de sesión de Odoo (request.session.sid).

    A pesar del nombre de la función y de que en signature.mixin se guarda
    en el campo `signature_mac`, esto NO es una dirección MAC real — es el
    identificador de sesión del navegador. Sirve para distinguir sesiones,
    no dispositivos físicos.
    """
    if not request or not hasattr(request, 'session'):
        return 'SISTEMA_INTERNO'
    return str(request.session.sid)

def obtener_info_hardware():
    """User-Agent del navegador que hizo el request (no hardware real)."""
    if not request or not hasattr(request, 'httprequest'):
        return 'Desconocido'
    return request.httprequest.headers.get('User-Agent', 'Desconocido')


# =========================================================
# 2. DECORADOR MAESTRO (IMAGEN DESHABILITADA Y CORREGIDA)
# =========================================================

def seguimiento_firmas(lista_de_configuracion):
    """Decorador que engancha trazabilidad de firmas a un write() de Odoo.

    `lista_de_configuracion` es una lista de dicts, uno por cada "slot" de
    firma del modelo (ej. Coordinador, Contratista, Declarante...). Claves
    esperadas en cada dict (todas opcionales, se leen con .get()):

    - name:  etiqueta legible usada en el hash y en los mensajes de chatter
             (default 'Firma' si no se indica).
    - check: nombre del campo Boolean que el usuario marca para firmar.
             Si `vals` no toca este campo (ni `img`), el slot se ignora.
    - log:   nombre del campo Many2one('signature.mixin') donde se guarda
             el registro de auditoría de la firma vigente.
    - hash:  nombre del campo Char donde se guarda el SHA-256 ("FHA-...")
             de la firma vigente.
    - img:   nombre de un campo Binary/Char heredado de un firmado por
             imagen — SOLO se usa hoy para decidir si el slot debe
             procesarse (`img` en vals), el guardado de imagen real está
             deshabilitado (ver CASO B abajo).

    Comportamiento al llamar a `self.write(vals)` sobre un slot tocado:

    - CASO A (firmar): si `check` pasa a True y no había firma vigente,
      crea un signature.mixin nuevo con IP/dispositivo/hash y lo enlaza
      vía `log`/`hash` en `vals`. Si ya había firma vigente, no hace nada
      (idempotente — re-guardar con check=True no recrea la firma).
    - CASO B (firma por imagen): deshabilitado intencionalmente, ver
      comentario en el cuerpo. `signature_image` siempre se envía False.
    - CASO C (desmarcar / refirma): si `check` pasa a False, no borra el
      signature.mixin anterior — lo marca `tipo_accion='eliminacion'` (o
      'refirma' si el caller ya venía limpiando `log`+`hash` a False para
      forzar una nueva firma tras un cambio de contenido) y desvincula
      `log`/`hash` del documento en `vals`.

    Cada registro del recordset recibe su PROPIA copia de `vals` y su
    propia llamada al write real — necesario porque el hash/log calculado
    es distinto por registro; compartir un único `vals` entre registros de
    un mismo write() multi-registro haría que todos terminaran con el
    hash/log del último registro procesado (bug corregido, ver `vals_rec`
    más abajo).

    REGLA para el método decorado (importante al agregar un consumidor
    nuevo): como el write real se invoca UNA VEZ POR REGISTRO (no una vez
    para todo el recordset), cualquier chequeo agregado que dependa del
    `self` COMPLETO — ej. `all(record.x == y for record in self)`,
    `any(...)`, un `AccessError` que deba rechazar el batch entero si un
    solo registro no califica, o un efecto secundario que deba correr una
    sola vez para todo el batch (una notificación agrupada, por ejemplo) —
    NO debe vivir dentro del método decorado: ahí ese `self` siempre será
    de 1 registro, y el chequeo/efecto se fragmenta en N ejecuciones
    independientes en vez de una atómica sobre el batch. Ese tipo de lógica
    debe vivir en un `write()` externo (el método público del modelo, sin
    decorar), que llama a un helper privado decorado que solo hace
    `return super().write(vals)` — patrón ya usado por la mayoría de los
    consumidores actuales (`informe_actividades.py`,
    `declaracion_juramentada.py`, `gestion_documental.py`,
    `inventario_documental.py`, `drive_firma_solicitud.py`); ver
    FIRMA_ELECTRONICA.md para el detalle y un ejemplo completo.
    """
    def decorador(write_func):
        @wraps(write_func)
        def envoltura(self, vals):
            # Recordset vacío: preservamos el comportamiento original de llamar
            # al write real una sola vez, sin pasar por el loop de firmas.
            if not self:
                return write_func(self, vals)

            # Si vals no toca NINGÚN campo check/img configurado, no hay
            # nada de firma que calcular por registro — el motivo del loop
            # por registro (hash/log distinto por registro) no aplica, así
            # que se puede volver a UNA sola llamada batch sin ningún
            # riesgo. Importante para writes frecuentes que no tocan
            # campos de firma (ej. el cron que expira solicitudes vencidas
            # en drive_firma_solicitud.py, que antes de este check hacía
            # un write() individual por cada registro expirado).
            algun_slot_tocado = any(
                (conf.get('check') and conf.get('check') in vals)
                or (conf.get('img') and conf.get('img') in vals)
                for conf in lista_de_configuracion
            )
            if not algun_slot_tocado:
                return write_func(self, vals)

            resultados = []
            for rec in self:
                # Copia propia de vals por registro: cada registro del recordset
                # persiste SU PROPIO hash/log, nunca el de otro registro procesado
                # antes en este mismo loop (bug de escritura multi-registro).
                vals_rec = dict(vals)

                # OBTENEMOS LOS 3 DATOS DE AUDITORÍA
                ip_real = obtener_ip_real()
                device_id = obtener_id_unico_dispositivo()
                browser_info = obtener_info_hardware()
                ahora = fields.Datetime.now()

                for conf in lista_de_configuracion:

                    # USO DE .get() PARA EVITAR KeyError SI LAS CLAVES NO ESTÁN CONFIGURADAS
                    check_key = conf.get('check')
                    img_key = conf.get('img')

                    # Verificamos de forma segura si las claves existen en vals_rec
                    tiene_check = check_key and check_key in vals_rec
                    tiene_img = img_key and img_key in vals_rec

                    if tiene_check or tiene_img:

                        # --- DESHABILITADO: Ya no leemos la variable de la imagen ---
                        # si la necesitamos en el futuro usaríamos: vals_rec.get(img_key, getattr(rec, img_key, False))

                        nuevo_check = vals_rec.get(check_key, getattr(rec, check_key, False)) if check_key else False
                        log_key = conf.get('log')
                        registro_anterior = getattr(rec, log_key, False) if log_key else False
                        registro_actual = registro_anterior
                        if log_key and log_key in vals_rec and vals_rec.get(log_key) is False:
                            registro_actual = False
                        firma_reemplazada_por_cambio = (
                            registro_anterior
                            and log_key
                            and log_key in vals_rec
                            and vals_rec.get(log_key) is False
                            and conf.get('hash')
                            and conf.get('hash') in vals_rec
                            and vals_rec.get(conf.get('hash')) is False
                        )

                        # CASO A: FIRMA CON CHULITO (Única opción de firmado ahora)
                        if nuevo_check:
                            if not registro_actual:
                                if firma_reemplazada_por_cambio:
                                    registro_anterior.write({'tipo_accion': 'refirma', 'signature_date': ahora})
                                # El Hash ahora incluye la IP Real para más seguridad
                                datos = f"CHULITO-{conf.get('name', 'Firma')}-{rec.id}-{ip_real}-{device_id}-{ahora}"
                                nuevo_hash = "FHA-" + hashlib.sha256(datos.encode()).hexdigest()

                                if conf.get('hash'):
                                    vals_rec[conf['hash']] = nuevo_hash

                                log_vals = {
                                    'name': f"{conf.get('name', 'Firma')} (Aceptación)",
                                    'signature_accepted': True,
                                    'signature_image': False, # Siempre enviamos False para que no guarde nada
                                    'signature_hash': nuevo_hash,
                                    'signature_ip': ip_real,
                                    'signature_mac': device_id,
                                    'signature_device': browser_info,
                                    'signature_date': ahora,
                                    'tipo_accion': 'firma',
                                    'modelo_origen': rec._description,
                                    'registro_origen_id': rec.id,
                                    'nombre_documento': rec.display_name or 'Documento sin nombre',
                                }
                                log = rec.env['signature.mixin'].create(log_vals)

                                if conf.get('log'):
                                    vals_rec[conf['log']] = log.id
                                rec.message_post(body=f"✅ <b>Firma ({conf.get('name', 'Firma')})</b><br/>SHA/FHA Hash: {nuevo_hash}")

                        # --- DESHABILITADO: CASO B: FIRMA CON IMAGEN ---
                        # elif nueva_imagen: ...

                        # CASO C: ELIMINAR FIRMA (Se activa si el check es falso/desmarcado)
                        else:
                            registro_a_eliminar = registro_anterior or registro_actual
                            if registro_a_eliminar:
                                tipo_accion_anterior = 'refirma' if firma_reemplazada_por_cambio else 'eliminacion'
                                registro_a_eliminar.write({'tipo_accion': tipo_accion_anterior, 'signature_date': ahora})
                                if conf.get('log'):
                                    vals_rec[conf['log']] = False
                                if conf.get('hash'):
                                    vals_rec[conf['hash']] = False
                                if tipo_accion_anterior == 'refirma':
                                    rec.message_post(body=f"↻ <b>Firma pendiente de refirma por cambio ({conf.get('name', 'Firma')})</b>")
                                else:
                                    rec.message_post(body=f"❌ <b>Firma Eliminada ({conf.get('name', 'Firma')})</b>")

                resultados.append(write_func(rec, vals_rec))

            return all(resultados)
        return envoltura
    return decorador

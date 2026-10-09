# Reglas puras de vigencia y notificación: no escriben contratos ni saldos.
from datetime import timedelta


ESTADOS_OTROSI_FORMALIZADOS = frozenset({
    'contratado', 'adicion_formalizada', 'prorroga_formalizada',
    'adicion_prorroga_formalizada', 'modificatorio_formalizado',
    'reinicio_formalizado', 'suspendido', 'suspension_prorrogada',
})


def fecha_vigente(fecha_base, prorrogas):
    fechas = [fecha for fecha in [fecha_base, *prorrogas] if fecha]
    return max(fechas) if fechas else None


def requiere_resumen(cantidad):
    # Exactamente 20 conserva solo los individuales. El resumen es adicional.
    return cantidad > 20


DIAS_AVISO_VENCIMIENTO = frozenset({15, 5, 0})


def en_ventana_aviso(fecha_fin, hoy):
    # Tres hitos exactos; no se recuperan avisos de días anteriores.
    return bool(fecha_fin and (fecha_fin - hoy).days in DIAS_AVISO_VENCIMIENTO)


def vencio(fecha_fin, hoy):
    # El último día completo sigue siendo vigente.
    return bool(fecha_fin and fecha_fin < hoy)


def bloqueo_finalizacion(estado, fecha_fin, excepcion, cierre_activo, pendientes, suspendido):
    if estado != 'contratado':
        return 'estado'
    if not fecha_fin:
        return 'fecha'
    if excepcion:
        return 'excepcion'
    if cierre_activo:
        return 'cierre'
    if pendientes:
        return 'pendientes'
    if suspendido:
        return 'suspension'
    return None

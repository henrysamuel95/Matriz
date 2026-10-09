# Centraliza los estados y valores constantes utilizados por todo el flujo de cierre contractual.
from enum import Enum


class EstadoCierreContrato(str, Enum):
    SOLICITUD = 'solicitud'
    FINANCIERA = 'financiera'
    JURIDICA = 'juridica'
    FIRMA_COORDINADOR = 'firma_coordinador'
    LIBERACION_RFC = 'liberacion_rfc'
    FINALIZADO = 'finalizado'
    CANCELADO = 'cancelado'


ESTADOS_CIERRE_CONTRATO = [
    (EstadoCierreContrato.SOLICITUD.value, 'Coordinador (solicita)'),
    (EstadoCierreContrato.FINANCIERA.value, 'Financiera (certifica)'),
    (EstadoCierreContrato.JURIDICA.value, 'Jurídico (revisa)'),
    (EstadoCierreContrato.FIRMA_COORDINADOR.value, 'Coordinador (firma)'),
    (EstadoCierreContrato.LIBERACION_RFC.value, 'Presupuesto (libera RFC)'),
    (EstadoCierreContrato.FINALIZADO.value, 'Finalizado'),
    (EstadoCierreContrato.CANCELADO.value, 'Cancelado'),
]

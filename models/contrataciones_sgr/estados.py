from enum import Enum


class EstadoContratacionSgr(str, Enum):
    COORDINADOR_1 = 'coordinador_1'
    JURIDICO_1 = 'juridico_1'
    COORDINADOR_2 = 'coordinador_2'
    JURIDICO_2 = 'juridico_2'
    JURIDICO_SECOP = 'juridico_secop'
    FINANCIERO = 'financiero'
    JURIDICO_ACTA_INICIO = 'juridico_acta_inicio'
    COORDINADOR_POLIZAS_FIRMA = 'coordinador_polizas_firma'
    JURIDICO_ACTA_APROBACION = 'juridico_acta_aprobacion'
    COORDINADOR_CONFIRMACION = 'coordinador_confirmacion'
    CONTRATADO = 'contratado'


ESTADOS_CONTRATACION_SGR = [
    (EstadoContratacionSgr.COORDINADOR_1.value, 'Coordinador'),
    (EstadoContratacionSgr.JURIDICO_1.value, 'Jurídico'),
    (EstadoContratacionSgr.COORDINADOR_2.value, 'Coordinador'),
    (EstadoContratacionSgr.JURIDICO_2.value, 'Jurídico'),
    (EstadoContratacionSgr.JURIDICO_SECOP.value, 'Jurídico SECOP'),
    (EstadoContratacionSgr.FINANCIERO.value, 'Financiero'),
    (EstadoContratacionSgr.JURIDICO_ACTA_INICIO.value, 'Jurídico - Acta de inicio'),
    (EstadoContratacionSgr.COORDINADOR_POLIZAS_FIRMA.value, 'Coordinador - Pólizas y firma'),
    (EstadoContratacionSgr.JURIDICO_ACTA_APROBACION.value, 'Jurídico - Acta de aprobación'),
    (EstadoContratacionSgr.COORDINADOR_CONFIRMACION.value, 'Coordinador - Confirmación'),
    (EstadoContratacionSgr.CONTRATADO.value, 'Contratado'),
]

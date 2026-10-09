from enum import Enum


class EstadoSolicitudPoliza(str, Enum):
    """Estados vigentes del flujo de solicitud y aprobación de pólizas."""

    SOLICITADA = 'solicitada'
    PENDIENTE_ENVIO = 'pendiente_envio'
    TRAMITE_APROBACION = 'tramite_aprobacion'
    CORRECCION = 'correccion'
    PENDIENTE_ACTA = 'pendiente_acta'
    FINALIZADA = 'finalizada'


class OrigenSolicitudPoliza(str, Enum):
    """Hecho contractual que originó una solicitud de pólizas."""

    CONTRATO = 'contrato'
    OTRO_SI = 'otro_si'
    LIQUIDACION = 'liquidacion'
    SOPORTE = 'soporte'


ORIGEN_SOLICITUD_POLIZA_SELECTION = (
    (OrigenSolicitudPoliza.CONTRATO.value, 'Contrato inicial'),
    (OrigenSolicitudPoliza.OTRO_SI.value, 'Actualización por otrosí'),
    (OrigenSolicitudPoliza.LIQUIDACION.value, 'Actualización por liquidación'),
    (OrigenSolicitudPoliza.SOPORTE.value, 'Solicitud extraordinaria de Soporte'),
)


ESTADO_SOLICITUD_POLIZA_SELECTION = (
    (EstadoSolicitudPoliza.SOLICITADA.value, 'Solicitud guardada'),
    (
        EstadoSolicitudPoliza.PENDIENTE_ENVIO.value,
        'Póliza adicional pendiente de envío',
    ),
    (
        EstadoSolicitudPoliza.TRAMITE_APROBACION.value,
        'Trámite de aprobación de pólizas',
    ),
    (
        EstadoSolicitudPoliza.CORRECCION.value,
        'Corrección solicitada al contratista',
    ),
    (
        EstadoSolicitudPoliza.PENDIENTE_ACTA.value,
        'Pólizas aprobadas - Pendiente de acta',
    ),
    (
        EstadoSolicitudPoliza.FINALIZADA.value,
        'Trámite de pólizas finalizado',
    ),
)


POLIZA_FIELDS = (
    'poliza_cumplimiento',
    'poliza_buen_manejo_anticipo',
    'poliza_devolucion_pago_anticipado',
    'poliza_salarios_prestaciones',
    'poliza_estabilidad_calidad_obra',
    'poliza_calidad_servicio',
    'poliza_calidad_bienes',
    'poliza_seriedad_oferta',
    'poliza_responsabilidad_civil',
    'otras_polizas',
)

POLIZA_SELECTION = (
    ('poliza_cumplimiento', 'Póliza de cumplimiento'),
    (
        'poliza_buen_manejo_anticipo',
        'Póliza de buen manejo y correcta inversión del anticipo',
    ),
    (
        'poliza_devolucion_pago_anticipado',
        'Póliza de devolución del primer pago anticipado',
    ),
    (
        'poliza_salarios_prestaciones',
        'Póliza de pago de salarios, prestaciones sociales legales '
        'e indemnizaciones laborales',
    ),
    (
        'poliza_estabilidad_calidad_obra',
        'Póliza de estabilidad y calidad de la obra',
    ),
    ('poliza_calidad_servicio', 'Póliza de calidad del servicio'),
    ('poliza_calidad_bienes', 'Póliza de calidad de los bienes'),
    ('poliza_seriedad_oferta', 'Póliza de seriedad de la oferta'),
    (
        'poliza_responsabilidad_civil',
        'Póliza de responsabilidad civil extracontractual',
    ),
    ('otras_polizas', 'Otras pólizas'),
)

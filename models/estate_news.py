from enum import Enum



class EstadoContratacion(Enum):
    COORDINADOR = "coordinador"
    VENTANILLA = "Revisión de Doc."
    PRESUPUESTOS = "RPC, AT"
    DIR_JURIDICO = "Asignar jurídico"
    JURIDICO = "Revisar Doc."
    RRHH = "Revisar ARL y Doc."
    CONTRATO = "Generar contrato, ajustar y cargar"
    FIRMAS = "Descarga contrato, hacer firmar, cargar de nuevo y contratar"
    CONTRATADO = "Se puede crear otrosí"
    POLIZA = "Solicitud de jurídico de ser necesario"
    FINALIZADO = "Finalizado automatico con la fecha"
    
    
# Finalización normal de contratos FUV

Documentación del paquete `models/finalizacion_normal`.

Este desarrollo permite finalizar un contrato FUV por cumplimiento normal de su vigencia, sin crear actas de cierre, sin liberar recursos y sin modificar documentos de Drive.

## Documentación

- [README_USUARIO.md](README_USUARIO.md): guía funcional para usuarios.
- [README_USUARIO_TECNICO.md](README_USUARIO_TECNICO.md): funcionamiento técnico, validaciones, automatización, correos, reapertura y otrosí posterior.

## Archivos del paquete

- `contrato.py`: vigencia efectiva, validaciones, finalización manual/automática y procesamiento por lotes.
- `notificaciones.py`: avisos, trazabilidad de correos y resúmenes.
- `reapertura.py`: autorización de Dirección FUV para un otrosí posterior.
- `otrosi.py`: restricciones y enlace del otrosí posterior autorizado.
- `asistentes.py`: opción **Finalizar normalmente** dentro del asistente de cierre.
- `reglas.py`: reglas puras de fechas, bloqueos y umbrales.

Este paquete es independiente de `cierre_contractual`: no ejecuta anulación, mutuo acuerdo, liquidación unilateral ni reintegros.

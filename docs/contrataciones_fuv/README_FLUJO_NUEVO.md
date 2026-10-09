# Contrataciones FUV - Flujo nuevo (README 1)

[Volver al índice](README.md) | [Flujo anterior](README_FLUJO_ANTERIOR.md)

## Cuándo se usa

**Fecha de inicio predeterminada: 24 de julio de 2026, inclusive.** Los FC
creados ese día o después usan el flujo nuevo. El valor proviene de
`FECHA_INICIO_NUEVO_FLUJO_DEFAULT` en `models/flujo_configuracion.py`; puede
cambiarse con el parámetro `Matriz.fecha_inicio_nuevo_flujo_contratacion`.
Por ello, antes de decidir solo por la fecha, compruebe que el registro indique
`flujo_contratacion_version = nuevo` o
`usa_flujo_contratacion_nuevo = True`. El cálculo usa `create_date` y, si falta,
`fch_creacion`.

Esta guía documenta el recorrido normal, no un cambio administrativo de
estado realizado por Soporte.

## Recorrido

Para **persona jurídica**:

`Coordinador -> Ventanilla -> Presupuesto -> Dir. Jurídico -> Jurídico 1
-> Coord. - Subir contrato firmado -> Contratado`

Para **persona natural o contrato laboral**:

`Coordinador -> Ventanilla -> Presupuesto -> Dir. Jurídico -> Jurídico 1
-> Recursos humanos -> Jurídico 2 -> Coord. - Subir contrato firmado -> Contratado`

El estado **Pólizas (Eventual)** se puede solicitar *después* de `Contratado`;
no es un paso obligatorio de ambos recorridos. Estados como `Suspendido`,
`Finalizado`, `Liquidado` y `Anulado` corresponden a procesos posteriores o
excepcionales, no a aprobaciones intermedias de esta secuencia.

## Qué hace cada etapa

| Etapa | Acción normal | Condición importante |
| --- | --- | --- |
| Coordinación | Crea y revisa el FC; al aprobarlo lo envía a Ventanilla. | Debe haber usuarios de Ventanilla asociados al proyecto. Si el FC volvió con ajustes, la acción puede retornarlo al área que lo devolvió. |
| Ventanilla | Revisa los requisitos y envía a Presupuesto. | Deben estar aprobados los requisitos financieros aplicables al tipo de persona y existir un usuario de Presupuesto asociado. |
| Presupuesto | Revisa y envía al Director Jurídico. | Requiere RPC (`compromiso_presupuestal`), aprobaciones financieras y un director jurídico destinatario. |
| Dirección Jurídica | Asigna el jurídico responsable. | La asignación de `usuarios_juridico_ids` mueve el FC de `director_juridico` a `juridico_1`. |
| Jurídico 1 | Revisa y aprueba documentos jurídicos. | Solo puede ejecutar la acción un jurídico asignado. Persona jurídica carga el contrato sin firmar y pasa directamente a firma; persona natural o contrato laboral pasa a Recursos Humanos. |
| Recursos Humanos | Revisa y aprueba los documentos requeridos. | No aplica a persona jurídica. Envía a Jurídico 2 y necesita jurídicos asignados. |
| Jurídico 2 | Carga el contrato sin firmar y lo envía a firma. | Solo para el recorrido que pasó por Recursos Humanos; requiere aprobación documental y jurídico asignado. |
| Coordinación - firma | Carga el contrato firmado y pulsa **Contratar**. | El cargue por sí solo no cambia el estado. Solo Coordinador o Asistente coordinador puede contratar desde `firma`, con el documento firmado cargado. |

Las aprobaciones de Ventanilla, Presupuesto, Jurídico y Recursos Humanos
comprueban indicadores de documentos/requisitos completos antes de avanzar.
Los botones de avance también pueden enviar correos a los responsables; si
falta un destinatario requerido, muestran un error. Las observaciones de cada
área acompañan las notificaciones y las devoluciones.

## Devoluciones

- Ventanilla puede devolver a Coordinación.
- Presupuesto puede devolver a Ventanilla o a Coordinación.
- Dirección Jurídica y Jurídico 1 pueden devolver a Presupuesto o a Coordinación.
- Recursos Humanos puede devolver a Jurídico 1 o a Coordinación.
- Jurídico 2 puede devolver a Coordinación.

El sistema registra el origen de la devolución y el estado de retorno en
`ultimo_estado_devolucion`, `ultimo_usuario_devolucion_id` y
`estado_retorno_devolucion`. Cuando Coordinación corrige un FC devuelto, su
acción de aprobación puede reenviarlo al área que corresponde a esa devolución,
en vez de comenzar otra vez por Ventanilla. Estas devoluciones exigen los roles
y destinatarios definidos para cada etapa.

## Después de contratar

Desde `Contratado`, el Director Jurídico o el jurídico asignado puede pulsar
**Requerir póliza** para pasar a `polizas`. Esto no equivale a finalizar el
contrato. La finalización normal y el cierre contractual tienen sus propias
condiciones y están documentados fuera de esta guía.

## Soporte y trazabilidad

El menú **Soporte** ofrece acciones administrativas, incluida **Cambiar estado**.
Esa acción escribe el estado y deja una nota en el historial, pero **no ejecuta
las aprobaciones, la carga de documentos ni los correos del flujo normal**.
Por ello debe usarse para corregir casos puntuales, no para reemplazar las
acciones de cada área. El cambio de estado no implica que los requisitos de la
etapa destino estén completos.

## Referencias de implementación

- Detección de versión, transiciones, devoluciones y validaciones:
  [`models/contrataciones_fuv_aprobaciones.py`](../../models/contrataciones_fuv_aprobaciones.py).
- Acciones y visibilidad de botones:
  [`views/contrataciones_fuv.xml`](../../views/contrataciones_fuv.xml).
- Campos base y acción **Contratar**:
  [`models/contrataciones_fuv.py`](../../models/contrataciones_fuv.py).
- Cambio administrativo de Soporte:
  [`models/contrataciones_fuv_soporte_estado.py`](../../models/contrataciones_fuv_soporte_estado.py).

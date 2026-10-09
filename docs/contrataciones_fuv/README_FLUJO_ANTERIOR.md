# Contrataciones FUV - Flujo viejo o anterior (README 2)

[Volver al índice](README.md) | [Flujo nuevo](README_FLUJO_NUEVO.md)

## Cuándo se usa

**Hasta el 23 de julio de 2026, por defecto.** Los FC creados antes del
24 de julio de 2026 usan el flujo viejo. El corte puede cambiarse mediante
`Matriz.fecha_inicio_nuevo_flujo_contratacion`, así que la confirmación
definitiva está en `flujo_contratacion_version = anterior` o
`usa_flujo_contratacion_nuevo = False`. El cálculo usa `create_date` y, si
falta, `fch_creacion`.

Los registros históricos conservan sus estados y botones. No deben
interpretarse con las reglas nuevas de Ventanilla y Presupuesto.

## Recorrido habitual

La ruta habitual es:

`Coordinador -> Financiera -> Jurídico -> Contratado`

Para persona natural o contrato laboral, Jurídico puede remitir a Recursos
Humanos; después de su aprobación el expediente vuelve a Jurídico antes de
que Coordinación contrate. **No hay una sola ruta obligatoria** para todos
los FC antiguos. Los botones de Coordinación también pueden enviar el
expediente directamente a Jurídico o Recursos Humanos, según la opción
histórica seleccionada en el formulario.

### Variantes que muestra el formulario

| Tipo de FC | Selector histórico | Opciones de aprobación de Coordinación |
| --- | --- | --- |
| Persona natural o contrato laboral | `estado_contratacion_nun` | `1`: hacia Financiera; `2`: hacia Jurídico; `3`: hacia Recursos Humanos. |
| Persona jurídica | `estado_contratacion_nun_juridica` | `1`: hacia Financiera; `2`: hacia Jurídico. |

Estos selectores **no son** el estado principal `estado_contratacion`. Indican
cuál botón/ruta de Coordinación aparece para un FC del flujo viejo. El destino
final puede depender también de una devolución o del avance previo del
expediente; se debe comprobar el estado visible después de ejecutar la acción.

| Área | Trabajo habitual |
| --- | --- |
| Coordinación | Revisa el formulario y los documentos. El botón de aprobación visible envía a Financiera, Jurídico o Recursos Humanos según el selector histórico y el tipo de FC. |
| Financiera | Revisa los requisitos financieros. Si aprueba, remite a Jurídico; también existe la acción **No Aprobado** para devolver a Coordinación. |
| Jurídico | Revisa los documentos y puede generar el contrato. En algunos casos remite a Recursos Humanos; también existe **No Aprobado** para devolver a Coordinación. Para aprobar en el flujo viejo, el usuario jurídico debe contar con **ID del contrato**. |
| Recursos Humanos | Revisa la documentación. Al aprobar, devuelve el expediente a Jurídico para continuar; también existe **No Aprobado** para devolver a Coordinación. |
| Coordinación - firma | Tras el cargue del contrato firmado, pulsa **Contratar** para pasar a `contratado`. |

La pantalla separa los botones de persona natural/contrato laboral y persona
jurídica. La disponibilidad también depende de indicadores como
`show_aprueba_coordinador`, `show_aprueba_financiera`,
`show_aprueba_juridico`, `show_aprueba_recursos_humanos` y de campos de control
del recorrido antiguo. Por ello, un botón ausente no significa necesariamente
que falte el permiso: se debe revisar además el estado, el tipo de persona y
el selector histórico correspondiente.

## Diferencias frente al flujo nuevo

- La ruta antigua usa `financiera` y `juridico`, no la cadena ordinaria de
  `ventanilla`, `presupuesto`, `director_juridico` y `juridico_1`.
- Los botones antiguos de Coordinación pueden seleccionar rutas diferentes.
- Las aprobaciones antiguas pueden enviar correos y mensajes a usuarios
  relacionados con el proyecto; la ausencia del destinatario puede bloquear
  el avance o mostrar un aviso.
- El flujo anterior no está sujeto a la validación nueva que obliga a pasar
  por el estado `firma` para contratar. El formulario sí condiciona la
  visibilidad de **Contratar** al documento firmado.

Las acciones excepcionales de Soporte, la finalización normal, el cierre
contractual y los otrosíes no forman parte de esta secuencia de aprobaciones.

## Referencias de implementación

- Estados originales y contratación:
  [`models/contrataciones_fuv.py`](../../models/contrataciones_fuv.py).
- Métodos de aprobación históricos y bifurcación por versión:
  [`models/contrataciones_fuv_aprobaciones.py`](../../models/contrataciones_fuv_aprobaciones.py).
- Botones y condiciones de visibilidad:
  [`views/contrataciones_fuv.xml`](../../views/contrataciones_fuv.xml).

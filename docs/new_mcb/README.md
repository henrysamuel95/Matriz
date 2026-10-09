# Nueva MCB (`new_mcb`)

## Propósito

Nueva MCB es la capa de actualización y consolidación de la Matriz General de
Costo Beneficio de un proyecto. Permite partir de la MCB existente, registrar
nuevos otrosíes y presentar un resumen actualizado de aportes, costos,
impuestos, pólizas y costo de producción.

Su objetivo principal es conservar la MCB original como antecedente y manejar
las modificaciones posteriores sin perder la relación con el proyecto ni con
los registros históricos de otrosí (`mcb2`).

## Modelos implicados

### `mcb`

Es la MCB principal u original del proyecto. Tiene una relación `One2many` con
`new_mcb` mediante el campo `new_mcb_ids`.

El método `ir_a_NUEVA_MCB()` es el punto de entrada desde la tarjeta
**Nueva versión MCB**:

1. Busca la `new_mcb` vinculada a la MCB original.
2. Si no existe, la crea automáticamente, salvo para usuarios de consulta.
3. Relaciona el tablero de avances cuando corresponde.
4. Crea los otrosíes faltantes provenientes de `mcb2`.
5. Abre el formulario de la Nueva MCB.

### `new_mcb`

Representa la MCB consolidada. Reúne valores de:

- La MCB original (`mcb_id`).
- Los otrosíes históricos del modelo `mcb2`.
- Los otrosíes creados en `new_mcb_otrosi`.
- El sobrecosto aprobado, cuando corresponda.

También mantiene los totales que otros modelos consumen, como el costo de
producción, los costos tributarios, el valor total con otrosí y la fecha final
del proyecto.

### `new_mcb_otrosi`

Registra cada modificación asociada a una Nueva MCB. Los registros se nombran
automáticamente como `Otrosí 1`, `Otrosí 2`, etc., según la Nueva MCB padre.

Cada otrosí conserva trazabilidad en el chatter al crearse, modificarse o
eliminarse.

### Modelos auxiliares

- `mcb2`: antecedente histórico que alimenta datos de algunos otrosíes.
- `contratacion`: proyecto relacionado con la MCB.
- `tablero_avances`: recibe la relación con la Nueva MCB.
- `drive`, `drive_stage`, `drive.browser` y sus workspaces: gestionan los
  documentos del proyecto en Google Drive.

## Flujo funcional

1. El usuario entra al proyecto y abre **1. Pre-contractual**.
2. Desde la tarjeta de la MCB selecciona **Nueva versión MCB**.
3. El sistema abre o crea el registro `new_mcb` relacionado.
4. En la parte superior se muestran los otrosíes existentes en formato Kanban.
5. El usuario puede crear o abrir un `new_mcb_otrosi`.
6. Los valores de los otrosíes se incorporan a los acumulados de la Nueva MCB.
7. Los valores consolidados se reflejan nuevamente en la MCB principal y en
   los modelos que consumen sus campos relacionados.

El formulario de `new_mcb` no permite crear, duplicar ni eliminar el registro
directamente desde la vista. Su creación se controla desde la MCB principal.

## Tipos de otrosí

Un `new_mcb_otrosi` permite seleccionar uno o varios de estos tipos:

- Aclaración.
- Adición.
- Prórroga.
- Modificación.
- Suspensión.
- Reinicio.
- Acta de prórroga de la suspensión.

El campo calculado `tipo_otrosi` concatena las opciones seleccionadas y se
muestra tanto en el nombre visible como en la tarjeta Kanban.

### Comportamiento de Suspensión

Al seleccionar **Suspensión**, el formulario deja en solo lectura las demás
clasificaciones, excepto **Reinicio**. El propio campo Suspensión permanece
editable para que el usuario pueda desmarcarlo si fue seleccionado por error.

Este comportamiento es exclusivamente visual y funcional dentro del formulario
de `new_mcb_otrosi`. No suspende contratos, órdenes, cuentas de cobro ni otros
modelos relacionados con el proyecto.

## Secciones del formulario del otrosí

Las secciones se muestran según los tipos seleccionados:

- **Observaciones del otrosí:** descripción libre de la modificación.
- **Fechas:** visible para prórrogas o modificaciones; permite registrar la
  nueva fecha de finalización.
- **Adiciones y contrapartidas:** visible para adiciones o modificaciones.
- **Costo operativo.**
- **Costos tributarios.**
- **Pólizas.**
- **Otros costos FUV.**
- **Costo de producción.**
- **Documentos de soporte del Otrosí.**

La fecha consolidada de la Nueva MCB corresponde a la fecha de finalización más
alta encontrada entre sus otrosíes. El cambio también solicita la actualización
de la fecha en la MCB principal.

## Cálculos principales

### Contratos VCO

La Nueva MCB suma el valor base de `contratos_vco` y los valores
`contratos_vco_otrosi` de todos los otrosíes relacionados.

### Costos tributarios

El consolidado tributario incluye:

- IVA.
- ReteICA.
- Estampillas.
- Retefuente.
- ICA, avisos y tableros.
- Personal FUV.
- Gastos bancarios.

Cada componente combina el valor correspondiente de la MCB base o histórica y
los valores registrados en los nuevos otrosíes.

### Pólizas y costos FUV

Se consolidan las pólizas de cumplimiento y responsabilidad, además de los
costos FUV, la participación UV/FAC/ESC y FUV - Programas sociales.

### Valor total con otrosí

`valor_total2_final` acumula el valor base del proyecto y sus adiciones.

### Costo de producción consolidado

De forma resumida, el cálculo es:

```text
(valor total con otrosí + sobrecosto aprobado)
- costo operativo
- costos tributarios
- pólizas
- costos FUV
- participación UV/FAC/ESC
- FUV - Programas sociales
```

También existe un valor de producción sin descontar pólizas ni costos FUV, que
resta únicamente el costo operativo y los costos tributarios.

## Botón “Recalcular MCB”

El botón está disponible únicamente para `Matriz.group_soporte`. Su objetivo es
normalizar un registro cuando existen acumulados almacenados que quedaron
desactualizados.

Recalcula, en orden:

- Contratos VCO.
- IVA, ReteICA, estampillas, retefuente e ICA.
- Personal FUV y gastos bancarios.
- Total tributario.
- Costos FUV.
- Costo total de producción, con y sin pólizas/FUV.
- Campos equivalentes expuestos por la MCB principal.

El botón no recalcula los saldos operativos de **Contrataciones FUV**. Ese flujo
conserva su botón independiente para evitar cambios masivos no controlados.

## Documentos y Google Drive

### Actas de comité de la Nueva MCB

El formulario principal permite cargar y consultar actas de comité mediante:

- `action_upload_acta_comite()`.
- `action_open_acta_comite()`.
- La relación `acta_comite_ids` con el modelo `drive`.

### Documentos de soporte del otrosí

Debajo de las observaciones y cálculos del otrosí se encuentra la sección
**Documentos de soporte del Otrosí**. Permite cargar archivos, consultar los ya
relacionados y abrir cada documento en Drive.

Los documentos se relacionan mediante `documentos_soporte_ids`.

### Destino y aislamiento por proyecto

Todos estos documentos se guardan en:

```text
1. CARPETA PRINCIPAL
```

La carga valida simultáneamente:

- Que la Nueva MCB esté vinculada a una MCB y a un proyecto.
- Que exista la carpeta principal del proyecto.
- Que el workspace pertenezca al navegador Drive de ese mismo proyecto.
- Que el identificador de la carpeta de Google Drive coincida con la carpeta
  principal vinculada.

Estas validaciones evitan que los documentos de un proyecto se mezclen con los
de otro. Si no existe la relación, se muestra una alerta funcional al usuario.

El grupo de consulta financiera puede visualizar documentos, pero no cargarlos.

## Seguridad

La matriz completa de permisos se encuentra en
`security/ir.model.access.csv`. Los modelos tienen accesos diferenciados para
coordinación, financiera, jurídica, dirección, soporte y consulta.

Reglas relevantes:

- `Matriz.group_consulta_coordinacion_financiera` tiene acceso de solo lectura.
- El botón de recálculo requiere `Matriz.group_soporte`.
- En `new_mcb`, los registros asociados a proyectos del año 2026 en adelante
  solo pueden modificarse por Presupuestos, Director Financiero o Soporte.
- La carga documental vuelve a validar derechos y reglas de escritura antes de
  aceptar el archivo.

## Trazabilidad

`new_mcb` y `new_mcb_otrosi` heredan de `mail.thread` y
`mail.activity.mixin`.

El chatter de la Nueva MCB registra, entre otros:

- Creación y eliminación de otrosíes.
- Cambios en adición, contrapartidas y fechas.
- Cambios en costos tributarios, pólizas y costos FUV.
- Cambios en los tipos del otrosí.
- Carga de documentos de soporte y actas de comité.

## Vistas principales

| Uso | Modelo | ID externo |
| --- | --- | --- |
| Formulario Nueva MCB | `new_mcb` | `Matriz.view_form_new_mcb` |
| Kanban Nueva MCB | `new_mcb` | `Matriz.view_kanban_new_mcb` |
| Formulario Otrosí Nueva MCB | `new_mcb_otrosi` | `Matriz.view_form_new_mcb_otrosi` |
| Kanban Otrosí Nueva MCB | `new_mcb_otrosi` | `Matriz.view_kanban_new_mcb_otrosi` |

## Archivos relevantes

| Archivo | Responsabilidad |
| --- | --- |
| `models/new_mcb.py` | Modelos, cálculos, seguridad funcional y acciones. |
| `views/new_mcb.xml` | Formulario y Kanban de Nueva MCB. |
| `views/new_mcb_otrosi.xml` | Formulario detallado del otrosí. |
| `models/mcb.py` | Entrada desde la MCB original y campos relacionados. |
| `models/drive_soporte.py` | Carga, vinculación y sincronización con Drive. |
| `static/src/js/drv_upload.js` | Interacción del cargador de archivos. |
| `security/ir.model.access.csv` | Permisos por grupo. |

## Instalación y actualización

Los modelos se cargan desde `models/__init__.py`; las vistas y recursos están
declarados en `__manifest__.py`.

Después de modificar campos, vistas, permisos o prioridades se debe:

1. Reiniciar el servicio de Odoo cuando haya cambios Python.
2. Actualizar el módulo `Matriz` para aplicar XML, modelos y permisos en la base.
3. Recargar el navegador con `Ctrl + F5` si existen recursos o vistas en caché.

Reiniciar Odoo sin actualizar el módulo no actualiza los registros de vistas y
acciones almacenados en la base de datos.

## Diagnóstico rápido

### El total no cambia después de modificar un impuesto

Actualizar el módulo para asegurar que estén instaladas las dependencias de
cómputo actuales y ejecutar **Recalcular MCB** con un usuario de Soporte.

### No aparece el botón de recálculo

Comprobar que el usuario pertenezca a `Matriz.group_soporte` y que la vista
`Matriz.view_form_new_mcb` esté actualizada.

### No se pueden cargar documentos

Verificar:

- Que la MCB tenga proyecto relacionado.
- Que exista **1. CARPETA PRINCIPAL** para ese proyecto.
- Que la carpeta tenga identificador de Google Drive.
- Que el usuario tenga permisos de escritura y no sea únicamente de consulta.

### Un documento aparece en otro proyecto

No debe corregirse enlazando manualmente registros entre proyectos. Se debe
revisar la relación MCB-proyecto, el `drive_stage` y el workspace. La validación
del cargador rechaza cualquier destino cuya carpeta o proyecto no coincida.

## Alcance deliberado

- Nueva MCB consolida valores; no reemplaza la MCB base.
- Suspensión solo clasifica el otrosí y controla la edición del formulario.
- La suspensión efectiva de modelos relacionados pertenece a otro flujo.
- El recálculo de MCB no sustituye el recálculo de saldos de Contrataciones FUV.
- Los documentos se almacenan en la carpeta principal del proyecto, no en una
  carpeta global compartida entre proyectos.

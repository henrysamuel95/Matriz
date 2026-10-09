# Órdenes Especiales - Guía de usuario

## ¿Qué hace?

El módulo de **Órdenes Especiales** permite gestionar órdenes para persona jurídica
desde su creación hasta la finalización, con control por etapas, responsables,
documentos, notificaciones, trazabilidad y carpeta relacionada en Google Drive.

La orden queda asociada automáticamente al proyecto desde el cual fue creada. El
usuario no debe seleccionar ni cambiar manualmente ese proyecto.

## Flujo general

El flujo normal es:

**Coordinación → Ventanilla → Contabilidad → Auxiliar de Tesorería → Tesorería → Finalizado**

También existe un flujo especial de **Cruce**, en el que Contabilidad puede finalizar
la orden directamente si cumple las condiciones requeridas.

La cabecera del flujo permanece visible mientras se navega por el formulario para
facilitar el seguimiento del estado actual.

El botón **Guía de usuario** abre un resumen de las etapas, los documentos
principales y las devoluciones sin salir de la orden.

## Coordinación

Coordinación o Asistencia de Coordinación crea la orden y diligencia la información
inicial.

Entre los datos principales se encuentran:

- Proyecto relacionado.
- Fondo seleccionado.
- Fechas de solicitud, inicio y finalización.
- Proveedor.
- Información de contacto.
- Solicitante.
- Forma de pago.
- Tiempo de entrega.
- IVA.
- Gastos de expedición cuando correspondan.
- Facturas y otros documentos.
- Certificación conjunta, cuando se solicite.

Antes de enviar la orden a Ventanilla debe:

- Tener al menos una **Factura**.
- Si se cargó la **Certificación conjunta**, obtener la aprobación del Coordinador.
- Seleccionar el usuario de Ventanilla que recibirá la orden.

El RCF es opcional y lo puede diligenciar el Coordinador durante la etapa de
Coordinación. Después queda en solo lectura para las demás áreas. La
Certificación conjunta es opcional. Si se aporta, requiere aprobación de
Coordinación antes de enviarla a Ventanilla y de Ventanilla antes de enviarla
a Contabilidad.

Cuando una orden fue devuelta desde una etapa posterior, Coordinación no reinicia el
flujo desde Ventanilla. El sistema muestra el origen de la devolución y habilita la
acción **Retornar ajustes al área que devolvió**.

## Selección de fondos

El campo **Seleccionar fondo** es obligatorio.

La selección determina si el valor de la orden afecta recursos de producción o de
pólizas, según la configuración de la orden.

## Ventanilla

Ventanilla recibe la orden desde Coordinación; el **RCF** puede estar vacío.

Para aprobar y enviar a Contabilidad debe seleccionar el usuario de
Contabilidad que recibirá la orden. Si se aportó la **Certificación conjunta**,
Ventanilla debe aprobarla. El RCF se muestra en solo lectura porque su
responsable es el Coordinador.

Ventanilla puede devolver la orden directamente a **Coordinación**. Toda devolución
requiere una observación.

## Contabilidad

Contabilidad puede:

- Cargar uno o varios documentos de **Causación**.
- Activar o desactivar **Cruce**.
- Registrar el **Consecutivo de Contabilidad**.
- Cargar uno o varios **Documentos de Cruce** cuando aplique.
- Aprobar hacia Auxiliar de Tesorería.
- Finalizar directamente una orden marcada como Cruce.
- Devolver directamente la orden a Coordinación.

El campo **Cruce** se presenta como un interruptor. Cuando está activado,
**Consecutivo · Contabilidad** se resalta visualmente.

El consecutivo se marca como requerido mientras la orden está en Contabilidad
solo cuando **Cruce** está activado.

### Orden normal

Si la orden no es Cruce, el consecutivo de Contabilidad es opcional. Contabilidad
debe cargar la Causación antes de enviarla al Auxiliar de Tesorería.

### Orden con Cruce

Para finalizar como Cruce se requiere:

- La orden debe estar en Contabilidad.
- **Cruce** debe estar activado.
- Debe existir **Consecutivo de Contabilidad**.
- Debe existir al menos un documento de **Causación**.
- Debe existir al menos un **Documento de Cruce**.

## Auxiliar de Tesorería

El Auxiliar de Tesorería recibe las órdenes que no fueron finalizadas como Cruce.

Debe registrar la **Fecha tentativa de pago** antes de enviar la orden a Tesorería.
Este campo se marca como requerido mientras la orden está en Auxiliar de Tesorería.

También puede:

- Cargar la **Orden de pago**, cuando aplique.
- Devolver directamente la orden a Coordinación indicando una observación.

## Tesorería

Tesorería puede cargar:

- Uno o varios **Comprobantes de pago**.
- Uno o varios **Comprobantes bancarios**.

Antes de finalizar la orden debe registrar el **Consecutivo de Tesorería** y tener
cargados los documentos requeridos.

El consecutivo se marca como requerido mientras la orden está en Tesorería.

Tesorería también puede devolver directamente la orden a Coordinación indicando una
observación.

## ¿Quién puede cargar cada documento?

| Documento | Responsable | Etapa requerida |
| --- | --- | --- |
| Facturas | Coordinación / Asistencia | Coordinación |
| Otros documentos | Coordinación / Asistencia | Coordinación |
| Causación | Contabilidad | Contabilidad |
| Documento Cruce | Contabilidad | Contabilidad |
| Orden de pago | Auxiliar de Tesorería | Auxiliar de Tesorería |
| Comprobante de pago | Tesorería | Tesorería |
| Comprobante bancario | Tesorería | Tesorería |

Las reglas se validan también en el servidor. Ver un botón en pantalla no concede
permisos adicionales.

**Soporte** es la excepción administrativa y cuenta con permisos especiales para
correcciones controladas.

## Carga de documentos

La carga documental permite:

- Seleccionar archivos desde el equipo.
- Arrastrar y soltar archivos.
- Cargar varios archivos a la vez cuando el tipo lo permite.
- Ver todos los archivos seleccionados antes de guardar.
- Retirar un archivo de la selección antes de confirmar.

Los archivos seleccionados se muestran como tarjetas compactas. Cuando existen varios,
se distribuyen visualmente en columnas para facilitar su revisión.

## Visualización de documentos

Cuando existen documentos cargados aparece **VER DOCUMENTOS**.

La ventana de consulta permite:

- Ver la lista de documentos relacionados.
- Buscar por nombre o tipo.
- Abrir cada archivo mediante **VER DOCUMENTO**.
- Mantener abierta la lista mientras se consulta un documento en un segundo popup.

La exportación a Excel está desactivada para esta vista porque se trata de documentos
y no de información tabular.

Queda pendiente como mejora futura una acción para descargar los documentos en un
archivo ZIP.

## Google Drive

Cada orden especial crea una carpeta principal dentro de la estructura del proyecto.

El nombre sigue el formato:

**Orden especial PROVEEDOR [NIT: número]**

Los documentos de la orden se guardan directamente dentro de esa carpeta. No se
separan por subcarpetas de Facturas, Otros, Contabilidad o Tesorería.

Esto incluye:

- Facturas.
- Otros documentos.
- Causaciones.
- Documentos de Cruce.
- Orden de pago.
- Comprobantes de pago.
- Comprobantes bancarios.

La carpeta permanece relacionada con la orden correspondiente.

## Aprobaciones

Antes de aprobar una etapa, el sistema valida las reglas obligatorias, entre ellas:

- Usuario destino seleccionado.
- Usuario destino activo.
- Rol correcto.
- Correo configurado.
- Campos obligatorios.
- Documentos requeridos.

Al aprobar, la orden cambia a la siguiente etapa y se envía una notificación al
responsable seleccionado.

Las notificaciones incluyen:

- Proveedor.
- NIT.
- Proyecto.
- **Total Producción**.
- Estado o destino.
- Enlace para abrir la orden.

## Devoluciones y retorno de ajustes

Ventanilla, Contabilidad, Auxiliar de Tesorería y Tesorería pueden devolver directamente
la orden a **Coordinación**.

Toda devolución requiere una **observación**.

Cuando una etapa devuelve la orden, el sistema guarda internamente:

- El estado desde el cual fue devuelta.
- El usuario exacto que realizó la devolución.
- El estado al que debe retornar después de la corrección.

Estos datos técnicos no se muestran como ruido automático en el chatter; la trazabilidad
se presenta mediante mensajes claros de devolución y retorno.

### Notificación de devolución

La devolución se notifica a:

- El Coordinador responsable de la orden.
- Los usuarios del grupo **Asistencia de Coordinación**
  (Matriz.group_contratacion_asis_coordinador) que estén asociados al mismo proyecto
  mediante users_with_access, estén activos y tengan correo.

La observación se incluye en el correo de devolución.

### Retorno después de la corrección

Mientras existe una devolución pendiente, Coordinación ve el área y usuario que
originaron la devolución.

El envío normal a Ventanilla se oculta y aparece:

**Retornar ajustes al área que devolvió**

Al utilizarlo:

- La orden regresa directamente al estado que originó la devolución.
- Se intenta retornar al mismo usuario que la devolvió.
- El usuario debe seguir activo, tener correo y conservar el rol requerido.
- No se obliga a repetir las etapas intermedias.
- Al finalizar el retorno se limpian los datos técnicos de devolución pendiente.

Ejemplos:

**Ventanilla → Coordinación → Ventanilla**

**Auxiliar de Tesorería → Coordinación → Auxiliar de Tesorería**

**Tesorería → Coordinación → Tesorería**

## Finalización

Una orden puede finalizar por:

- **Cruce**, desde Contabilidad.
- **Pago**, desde Tesorería.

Al finalizar:

- El estado cambia a **Finalizado**.
- Se registra la trazabilidad.
- Se envía una notificación a Coordinación.

La notificación de finalización incluye:

- Proveedor.
- NIT.
- Proyecto.
- **Total Producción**.
- Tipo o descripción de la finalización.
- Estado final.
- Enlace a la orden especial.

Después de finalizar, los usuarios normales no pueden editar la orden. Soporte
mantiene permisos administrativos para correcciones autorizadas.

## Notificaciones

Aprobaciones, devoluciones, retornos y finalización usan notificaciones HTML del
sistema.

- Las aprobaciones informan responsable y destino.
- Las devoluciones incluyen obligatoriamente la observación.
- Los retornos informan que Coordinación terminó los ajustes y devuelve la orden al
  origen.
- Las finalizaciones notifican el cierre de la orden.
- Todas incluyen el **Total Producción** cuando corresponde.

Las acciones principales también dejan trazabilidad en el chatter. Los campos técnicos
de asignación y retorno no generan mensajes automáticos innecesarios.

## Soporte

Los usuarios del grupo **Soporte** cuentan con un menú desplegable exclusivo en la
cabecera del formulario.

La opción **Cambiar estado** permite mover administrativamente la orden a cualquiera
de estos estados:

- Coordinación.
- Ventanilla.
- Contabilidad.
- Auxiliar de Tesorería.
- Tesorería.
- Finalizado.

Este cambio:

- Solo está disponible para Matriz.group_soporte.
- No ejecuta aprobaciones ni notificaciones del flujo ordinario.
- No obliga a cumplir las validaciones funcionales de una transición normal.
- Registra en chatter quién realizó el cambio, el estado anterior y el nuevo.
- Limpia cualquier retorno pendiente para evitar inconsistencias.

Debe utilizarse únicamente para correcciones administrativas controladas.

## Reglas importantes

- Crear la orden desde el proyecto correspondiente.
- No modificar manualmente el proyecto relacionado.
- Seleccionar el fondo antes de avanzar.
- No aprobar una etapa sin sus campos y documentos requeridos.
- No devolver sin observación.
- No seleccionar usuarios inactivos, sin correo o sin el rol requerido.
- Los permisos documentales dependen del rol y de la etapa actual.
- Las devoluciones posteriores regresan a Coordinación y luego retornan directamente
  al punto de origen.
- Soporte es la excepción administrativa para correcciones controladas.

## Pendientes / mejoras futuras

- Descargar en ZIP todos los documentos mostrados en una categoría o todos los de la
  orden especial.
- Continuar con validaciones finales de experiencia de usuario antes del despliegue
  definitivo en producción.

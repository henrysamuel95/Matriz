# Solicitud de pólizas

Este componente administra el ciclo de pólizas asociado a un contrato FUV:
selección, solicitud automática al contratar, carga externa individual,
revisión jurídica, correcciones, aprobación, acta definitiva y actualizaciones
por otrosí o liquidación.

El proceso tiene modelos, vistas y estilos propios. Se integra con
`contrataciones_fuv`, Google Drive, correo y el sitio web, pero no modifica los
flujos históricos de pólizas de otros modelos.

Este documento está separado por tipo de usuario:

1. **Coordinación y Jurídica:** operación normal del proceso.
2. **Soporte:** acciones extraordinarias y de recuperación.
3. **Referencia técnica común:** modelos, seguridad, correo, Drive y pruebas.

# Parte 1 — Manual para Coordinación y Jurídica

## Antes de comenzar

Este manual se refiere al botón **Solicitar póliza** del formulario de un
**contrato FUV guardado**. Pedir pólizas es opcional: un contrato sin solicitud
de pólizas puede llegar a **Contratado**. Para crear una solicitud inicial se
necesitan el rol de Coordinación, Dirección Jurídica o Jurídica **y** el grupo
**Cierre contractual y pólizas**. El contrato debe tener su carpeta individual
de contratista en Drive; para enviar correos necesita un correo válido en el
campo **Correo electrónico** del contrato.

**Checklist** significa la lista de garantías que se le pedirán al
contratista. Guardarlo crea el trámite y sus filas; antes de contratar, eso
todavía no equivale a enviar el correo. El botón **Ver póliza** permite abrir
el trámite guardado, consultar cada fila y leer el chatter.
Mientras una póliza no tenga fecha de envío al contratista, el botón **Cargar**
permanece oculto para Coordinación y Jurídica. Solo Soporte puede cargarla
antes del envío; esto también aplica a pólizas adicionales aún no notificadas.
**Chatter** es el panel de mensajes del formulario: allí se ve quién creó,
envió, revisó o modificó el trámite. **Lote** es el conjunto de pólizas
incluidas en un mismo correo al contratista.

Para una prueba sencilla con dos garantías:

1. Guarde el contrato, pulse **Solicitar póliza**, seleccione dos garantías y
   pulse **Guardar solicitud**. Compruebe el estado **Solicitud guardada**.
2. Pase el contrato a **Contratado**. Si la solicitud inicial aún no se había
   enviado, el contratista recibe **un solo correo** con ambas garantías y un
   enlace privado. Compruebe la **Fecha de envío** de las filas y la nota de
   correo en el chatter de la solicitud.
3. Abra el enlace como contratista y cargue **un archivo a la vez**. Tras el
   último archivo del mismo envío, los responsables reciben la alerta de
   revisión.
4. Dirección Jurídica o el Jurídico asignado aprueba cada póliza o devuelve
   alguna con una observación. El contratista carga la corrección desde el
   enlace nuevo que recibe por correo.
5. Cuando todas estén aprobadas, Dirección Jurídica o el Jurídico asignado
   carga el **acta definitiva**. El trámite pasa a **Finalizada**.

Si el contrato ya está **Contratado** y nunca tuvo solicitud, el botón normal
no aparece. Soporte puede crear un ciclo extraordinario; consulte la Parte 2.

## Resumen del proceso normal

El botón **Guía de usuario**, en la esquina superior derecha del formulario de
solicitud, abre un resumen de cinco pasos en un pop-up de solo lectura. Cerrar
la guía no cambia la solicitud.

```text
Contrato FUV guardado
        |
        v
Seleccionar pólizas y guardar checklist
        |
        v
Buscar "Pólizas" dentro de la carpeta de ESTE contratista; crearla si falta
        |
        v
Contrato pasa a "Contratado"
        |
        v
Correo automático al contratista con enlace privado
        |
        v
Contratista carga un archivo independiente por cada póliza
        |
        v
Correo de revisión al Jurídico asignado (o al Director si no hay asignado);
seguimiento a Dirección Jurídica e información a Coordinación/Asistencia
        |
        +---------------------------+
        |                           |
        v                           v
     Aprobar                    Devolver
        |                           |
        |                    Correo de corrección
        |                           |
        |                    Contratista reemplaza
        |                           |
        +-------------+-------------+
                      |
                      v
          Todas las pólizas aprobadas
                      |
                      v
          Dirección Jurídica o Jurídico asignado carga el acta definitiva
                      |
                      v
               Trámite finalizado
```

## Participantes del proceso normal

| Participante | Acciones principales |
| --- | --- |
| Coordinación | Crear el checklist, agregar o quitar pólizas pendientes y eliminar una solicitud completa. Recibe alertas de documentos cargados si está asignada al proyecto. |
| Asistente de Coordinación | Consultar la solicitud mediante **Ver póliza**, agregar una fila a una solicitud abierta y recibir alertas informativas si está asignado al proyecto. No puede crear la solicitud inicial ni enviar el correo al contratista. |
| Dirección Jurídica | Crear el checklist, agregar o quitar pólizas pendientes, asignar al responsable jurídico, aprobar, devolver para corrección y cargar el acta definitiva. Recibe alertas si está asignada al proyecto. |
| Jurídica | Puede crear el checklist y agregar o quitar pólizas pendientes. Para aprobar, devolver o cargar el acta debe ser el responsable asignado; Dirección Jurídica también puede hacer esas acciones. |
| Jurídica responsable | Profesional del grupo Jurídica seleccionado por Dirección Jurídica para revisar las pólizas y cargar el acta. Al asignarlo recibe acceso al trámite y a los documentos del contrato, aunque no figure en el proyecto. |
| Contratista | Cargar desde el enlace privado un archivo por cada póliza habilitada. No necesita iniciar sesión. |

Las restricciones se validan tanto en las vistas como en Python. Ocultar un
botón no sustituye las validaciones del servidor.

## Procedimiento para Coordinación y Jurídica

### 1. Crear el checklist

Desde un registro guardado de `contrataciones_fuv`, Coordinación, Dirección
Jurídica o Jurídica pulsa **Solicitar póliza**, selecciona los tipos requeridos y
guarda.

Para ver y ejecutar **Solicitar póliza**, el usuario también debe pertenecer al
grupo **Cierre contractual y pólizas** (`Matriz.group_ver_cierre_contractual`).
Este grupo habilita el botón, pero no reemplaza el rol de Coordinación,
Dirección Jurídica o Jurídica. El requisito se valida en XML y en Python.

El botón ordinario se oculta cuando `estado_contratacion` es `contratado`,
`liquidado`, `anulado` o `finalizado`. Si el contrato llegó a uno de esos
estados sin pólizas, solamente Soporte puede crear posteriormente un ciclo
extraordinario mediante **Soporte: pedir pólizas**. La misma restricción se
valida en Python para impedir que el método ordinario sea ejecutado por RPC.

El sistema crea:

- una solicitud `contrataciones_fuv.solicitud.poliza`;
- una línea `contrataciones_fuv.solicitud.poliza.linea` por cada tipo;
- la carpeta hija **Pólizas** dentro de la carpeta individual del contratista.

Solo puede existir una solicitud inicial por contrato. Después pueden existir
ciclos independientes vinculados a un otrosí o a una liquidación, sin
sobrescribir los documentos históricos.

### 2. Contratar y enviar automáticamente

Antes de finalizar la contratación, el sistema revisa si existe un checklist de
pólizas. El checklist es **opcional**: si no existe, el contrato se contrata
normalmente. Si existe, valida que tenga al menos una póliza y que
`contrataciones_fuv.correo` contenga un correo válido.

Cuando `contratado()` o `contratado2()` dejan el contrato en `contratado`, se
envía una sola vez el correo al contratista. El
estado del contrato no se altera: el flujo de pólizas usa su propio campo
`estado`.

El envío automático se conserva también para `action_set_contratado()`, usado
por la herramienta de normalización de Soporte. `_enviar_solicitud_polizas_al_contratar()`
solo procesa la solicitud inicial con origen `contrato` y comprueba
`not solicitud.fecha_envio`, por lo que no repite el correo si ya fue enviado.
Después del envío SMTP exitoso, `_registrar_notificacion_correo()` publica una
nota en el chatter con el evento, el contratista, su correo, el modo
**Automático al contratar** y las pólizas incluidas.

### 3. Carga pública

El contratista recibe una URL privada con token. Puede seleccionar archivos
en varias tarjetas y pulsar **Cargar pólizas seleccionadas** una sola vez, o
subir únicamente las que tenga listas y regresar por las demás. Cada tarjeta
corresponde a una garantía y admite un solo archivo. La página acepta:

- PDF, PNG, JPG y JPEG;
- máximo 20 MB por archivo.

Cada archivo crea su propio registro local `drive` y se vincula exclusivamente
a la línea correspondiente mediante `documento_id`. Una carga no reemplaza ni
modifica las demás pólizas.

La carga inicial y la corrección usan la misma plantilla QWeb pública, pero las
reglas cambian según el estado:

- en carga inicial, cada archivo cubre únicamente la línea seleccionada;
- en corrección, solo puede reemplazarse la línea devuelta;
- una línea que ya tiene documento y no fue devuelta no puede reemplazarse
  libremente desde el portal.

El servidor valida cada archivo por separado y registra cada carga en el
chatter. Si un lote falla, revierte sus vínculos en Odoo e intenta retirar de
Drive los archivos de ese envío que ya alcanzó a subir. La alerta de revisión
se envía una sola vez cuando queda completo el conjunto solicitado.

El enlace dura siete días y se desactiva cuando ya no quedan cargas pendientes.
Si el contratista ve **Enlace no disponible**, debe solicitar un enlace nuevo al
equipo que lleva el trámite. En el formulario interno también existe **CARGAR**
para una línea sin documento. Ese cargue interno vincula el archivo y deja nota
en el chatter, pero no ejecuta la alerta automática del portal al completar el
lote; por eso, en la prueba del correo de revisión use el enlace del contratista.
Si se cargan internamente **todas** las pólizas antes de contratar, ya no quedan
archivos por solicitar y el envío automático detendrá la contratación con un
error. Para probar el recorrido normal, deje las filas sin archivo hasta que
el contratista reciba el enlace.

Como compatibilidad con los registros creados durante la etapa consolidada, el
sistema comprueba si otra línea utiliza el documento anterior antes de
retirarlo. Esto impide que corregir una póliza afecte a las demás.

### 4. Notificación de revisión

Las pólizas incluidas en un mismo correo al contratista forman un lote. El
sistema espera que el contratista cargue todas las pólizas de ese lote y luego
envía notificaciones dirigidas a usuarios activos con correo. Dirección
Jurídica, Coordinación y Asistencia deben figurar en la lista de acceso del
proyecto (`proyecto_relacionado.users_with_access`). El Jurídico responsable
se selecciona para esta solicitud y puede recibir la alerta sin estar en esa
lista.

Cuando hay un Jurídico asignado, recibe el correo de acción; Dirección Jurídica
del proyecto recibe el de seguimiento; Coordinación y Asistencia del proyecto
reciben el informativo. Si todavía no hay Jurídico asignado, Dirección
Jurídica del proyecto recibe el correo de acción. Si una misma dirección
aparece en más de un grupo, solo se envía el primer correo aplicable. Todos
incluyen el contrato, las pólizas recibidas y un botón al formulario interno.
Una corrección genera su propia alerta. Si no hay destinatarios elegibles, el
documento permanece cargado y se deja una nota en el chatter.

El contenido distingue dos casos:

- **Póliza disponible para revisión:** carga inicial.
- **Corrección de póliza disponible para revisión:** reemplazo de una póliza
  devuelta.

Los destinatarios se normalizan para evitar repetir un mismo correo por
espacios o diferencias entre mayúsculas y minúsculas. Además, cada línea guarda
`documento_notificado_revision_id`; una misma versión del documento no puede
generar dos alertas, pero una corrección nueva sí puede notificarse.

### 4.1. Asignación del responsable jurídico

El pop-up presenta el responsable como un tag dentro de un campo con borde y
el placeholder **Buscar un jurídico...**. El trámite conserva un único
responsable: retire primero el tag con la **×** antes de elegir un reemplazo.
Para quitar la asignación, deje el campo vacío y pulse **Guardar cambios**.
El retiro registra el nombre y correo en el chatter, sin revocar accesos
compartidos del contrato que podrían corresponder a otros trámites. Guardar
sin cambios no reenvía correo.

Dirección Jurídica dispone del botón **Asignar jurídico** dentro de la
solicitud. El selector muestra usuarios activos del grupo Jurídica de toda la
organización; no exige que el profesional ya esté asociado al proyecto. Al
confirmar:

- se registra el responsable en `juridico_responsable_id`;
- si aún no tenía acceso jurídico al contrato, se añade a
  `contract_id.usuarios_juridico_ids`, que comparte la carpeta raíz y sus
  documentos de Drive con permiso de lectura;
- se envía una notificación con enlace al trámite y se deja trazabilidad en el
  chatter;
- el profesional asignado puede aprobar, devolver y cargar el acta; Dirección
  Jurídica conserva acceso de seguimiento y revisión.

La asignación puede realizarse desde **Solicitud guardada** y no depende de la
etapa del contrato. La escritura técnica de `usuarios_juridico_ids` utiliza una
excepción limitada a Dirección Jurídica, en modo elevado y solo para agregar
acceso. No permite editar otros campos ni avanza el estado contractual.

Las alertas de cargue se dirigen a los usuarios activos presentes en la lista
del proyecto con rol **Coordinación**, **Asistente de Coordinación** o
**Dirección Jurídica**, y también al **Jurídico responsable** asignado, aunque
este último no figure previamente en el proyecto.

### 5. Revisión jurídica

Dirección Jurídica o el Jurídico responsable asignado puede:

- **Aprobar:** registra usuario y fecha de revisión.
- **Devolver:** exige una observación, cambia la línea a `devuelta`, genera un
  token nuevo y envía al contratista un correo con el botón **Cargar
  corrección**.

Una póliza aprobada ya no puede devolverse desde la revisión normal. Las
excepciones operativas están documentadas en la Parte 2 para Soporte.

Al cargar una corrección:

- se crea un documento nuevo;
- la línea vuelve a `pendiente`;
- la versión anterior se retira después de enlazar correctamente la nueva,
  salvo que otra línea histórica todavía utilice ese documento;
- se envía la alerta interna específica de corrección recibida.

### 6. Pólizas adicionales

Mientras la solicitud no esté finalizada, cualquier perfil autorizado que haya
entrado al formulario —Coordinación, Asistente de Coordinación, Dirección
Jurídica, Jurídica o Soporte— puede usar **Agregar otra póliza**. El asistente
solo ofrece tipos que no estén incluidos. Esta acción únicamente crea una nueva
fila pendiente dentro de la solicitud: **no envía correo, no genera enlace y no
solicita todavía la carga al contratista**.

Si la solicitud inicial ya fue enviada:

- la nueva línea queda pendiente de envío;
- el estado pasa a `pendiente_envio`;
- aparece **Enviar solicitud** para Coordinación, Dirección Jurídica o Jurídica;
- al pulsarlo se genera el enlace y se envía un solo correo con las líneas
  adicionales todavía no notificadas.

Si la solicitud inicial todavía no fue enviada, la nueva garantía queda en el
checklist y entra en el correo automático cuando el contrato pase a
**Contratado**. El botón **Enviar solicitud** solo aparece cuando el estado
indica que existe una póliza adicional pendiente de envío.

En resumen, el flujo ordinario es:

```text
Agregar otra póliza -> crear la fila pendiente -> Enviar solicitud -> correo
```

### 7. Quitar una póliza agregada por error

Coordinación, Dirección Jurídica o Jurídica puede pulsar **Quitar** únicamente
cuando la línea todavía no tiene documento.

La acción:

- solicita confirmación;
- impide retirar evidencia ya cargada o aprobada;
- registra en el chatter el tipo retirado y si ya había sido notificado;
- recalcula el estado de la solicitud;
- invalida el token si ya no quedan cargas públicas pendientes;
- devuelve `False` para cerrar correctamente la confirmación y permite que el
  refresco suave actualice el formulario sin abrir una segunda acción.

### 8. Acta definitiva

Cuando todas las líneas están aprobadas, el estado pasa a `pendiente_acta`.
Dirección Jurídica o el Jurídico responsable asignado carga el acta definitiva
elaborada externamente; el sistema no genera un borrador.

El acta se guarda en la misma carpeta **Pólizas**. Al completar la carga:

- la solicitud pasa a `finalizada`;
- se registra usuario y fecha;
- se intenta notificar al contratista y a Coordinación. Un fallo de esos
  correos no impide que el acta quede cargada; revise el chatter para saber
  qué destinatarios fueron confirmados.

Si el documento del acta se elimina desde el modelo `drive`, la solicitud se
reabre para permitir cargar nuevamente el acta.

### 9. Actualización por otrosí

Cuando se formaliza un **reinicio**, **prórroga**, **adición** o **adición y
prórroga**, el sistema comprueba si el contrato ya tenía una solicitud de
pólizas con garantías. Si la tenía, toma los tipos de la solicitud previa más
reciente, crea automáticamente un ciclo nuevo, reutiliza la carpeta **Pólizas**
del mismo contratista y envía un enlace al contratista. Los archivos anteriores
no se trasladan a las nuevas líneas.

La solicitud nueva queda vinculada mediante `otro_si_id`; por eso los archivos
anteriores no se reemplazan y el origen permanece identificable. El formulario
del otrosí muestra **Ver pólizas**. También dispone de una acción manual de
recuperación si el otrosí ya estaba formalizado y todavía no se había generado
la actualización.

Si el contrato nunca tuvo pólizas, el otrosí no muestra opciones, no crea un
checklist y no bloquea su formalización.

### 10. Pólizas opcionales en liquidación

El proceso de cierre muestra **Pólizas en liquidación (opcional)** únicamente
si el contrato ya tenía garantías y el acta es de tipo **normal** o
**incumplimiento**. Coordinación, Dirección Jurídica o Jurídica decide si crea
el ciclo; no se dispara automáticamente y no es requisito para avanzar o
terminar la liquidación. La solicitud queda relacionada mediante
`cierre_proceso_id`.

### 11. Otros documentos

La sección interna **Otros** permite anexar varios documentos complementarios
—por ejemplo, una carta de no siniestro— sin registrarlos como tipo de póliza.
Coordinación, Dirección Jurídica o Jurídica pueden cargarlos en cualquier
estado, incluso después de finalizar el trámite.

Cada archivo se almacena en la carpeta **Pólizas**, queda relacionado mediante
`otros_documentos_ids` y registra en el chatter el nombre, usuario y evento de
carga o eliminación. Estos documentos no cambian el estado, no requieren
aprobación y no generan correos.

## Si un botón o un correo no aparece

| Situación | Qué comprobar |
| --- | --- |
| No aparece **Solicitar póliza** en el contrato | Guarde el contrato; compruebe el rol, el grupo **Cierre contractual y pólizas**, que no exista una solicitud y que el contrato no esté contratado, liquidado, anulado ni finalizado. |
| No aparece **Ver póliza** | Aún no existe una solicitud o el usuario no tiene uno de los roles con acceso al botón. |
| No aparece **Enviar solicitud** | Este botón se muestra cuando se agregó una póliza después de un envío y el estado indica **Póliza adicional pendiente de envío**. La solicitud inicial se envía al contratar. |
| No llegó el correo de revisión tras el primer archivo | Si el correo al contratista incluía varias garantías, espere a que se cargue la última del mismo envío. Compruebe también los responsables del proyecto y el Jurídico asignado. |
| No aparecen **APROBAR** o **DEVOLVER** | Debe existir un documento pendiente de revisión y el usuario debe ser Dirección Jurídica o el Jurídico asignado. Una póliza aprobada no tiene devolución normal. |
| No aparece **Cargar acta definitiva** | Debe ser Dirección Jurídica o el Jurídico asignado; todas las pólizas deben estar aprobadas y no debe haber un acta cargada. |
| El contratista ve **Enlace no disponible** | El enlace pudo vencer, utilizarse por completo o sustituirse por uno nuevo. Si faltan archivos, Soporte puede usar **Solicitar al contratista**. Si el único pendiente es una **corrección** de una fila que conserva su documento anterior, ese botón no la incluye; escale el caso a Soporte para resolverlo técnicamente. |
| La solicitud dice **Finalizada** y se necesita otra garantía | No se agrega a ese ciclo: Soporte crea una solicitud extraordinaria desde el contrato. |

Los errores concretos y los destinatarios confirmados quedan en el chatter de
la solicitud. Para verificar la entrega al buzón, consulte además el correo
recibido; el log de SMTP solo confirma que el servidor aceptó el envío.

# Parte 2 — Manual para Soporte

## Cuándo debe intervenir Soporte

Soporte utiliza sus acciones cuando el flujo ordinario ya no cubre la necesidad,
por ejemplo:

- el contrato ya está contratado y nunca tuvo pólizas;
- la solicitud anterior está finalizada y se necesita pedir otra garantía;
- el contrato está cerrado, pero se requiere un nuevo documento del contratista;
- una póliza aprobada debe reabrirse excepcionalmente para corrección;
- debe eliminarse una solicitud completa y su evidencia asociada.

Estas opciones son internas y solamente se muestran a `group_soporte`.

## Solicitar pólizas en cualquier momento

Dentro del desplegable **Soporte** del formulario del contrato está **Soporte: pedir pólizas**
para crear un ciclo extraordinario en cualquier estado del contrato. Incluso
puede utilizarse si nunca hubo pólizas o si existe un trámite anterior
finalizado; cada solicitud nueva conserva sus propias líneas y documentos.
También requiere la carpeta individual del contratista en Drive y un correo
válido en el contrato para enviar el enlace.

El procedimiento es:

1. Abrir **Soporte: pedir pólizas** desde el contrato.
2. Seleccionar una o varias garantías.
3. Pulsar **Guardar solicitud**.
4. El sistema crea un ciclo independiente con origen `soporte`.
5. Se crea o reutiliza la carpeta **Pólizas**.
6. Se envía inmediatamente un solo correo a `contrataciones_fuv.correo` con
   todas las garantías seleccionadas y un enlace privado.
7. La solicitud nueva queda abierta para seguimiento.

La acción no depende del estado del contrato y funciona aunque nunca hubiera
existido una solicitud. Tampoco reabre ni modifica solicitudes finalizadas,
actas anteriores o documentos históricos.

En una solicitud extraordinaria de Soporte, una vez que Jurídica apruebe todas
las pólizas, Soporte también puede pulsar **Cargar acta definitiva**. El acta se
guarda en Drive, finaliza ese ciclo y conserva la trazabilidad y las
notificaciones habituales. Esta excepción no habilita a Soporte para aprobar
pólizas ni para cargar el acta en solicitudes de otro origen.

### Enviar las pólizas pendientes de una solicitud existente

Dentro de una solicitud ya creada, Soporte utiliza **Solicitar al contratista**.
Este botón es una acción directa:

- no abre el asistente de selección;
- no agrega tipos de póliza;
- no crea otro ciclo;
- toma todas las filas visibles que todavía no tienen `documento_id`;
- genera un token y un enlace nuevos;
- envía un solo correo al valor de `contrataciones_fuv.correo` con todas esas
  pólizas pendientes;
- registra el envío y sus destinatarios en el chatter.

Si no existe ninguna fila sin documento, el sistema detiene la acción e informa
que no hay pólizas pendientes. Para crear una fila nueva debe utilizarse
**Agregar otra póliza**; el botón de envío nunca sustituye esa acción.
Una póliza **devuelta** todavía conserva el documento anterior hasta que el
contratista cargue la corrección. Por eso este botón no reenvía una corrección
devuelta con documento: no hay una acción normal de reenvío para ese caso.

## Diferencia entre solicitar y cargar

| Botón | Uso |
| --- | --- |
| **Agregar otra póliza** | Abre el selector y crea una fila nueva. No envía correo. |
| **Enviar solicitud** | Flujo ordinario: envía únicamente las filas nuevas no notificadas, para Coordinación o Jurídica. |
| **Solicitar al contratista** | Acción directa de Soporte: sin abrir menús, envía en un solo correo todas las filas del ciclo actual que aún no tienen documento. |
| **Soporte: pedir pólizas** (desde el contrato) | Abre el selector para crear un ciclo extraordinario independiente y lo envía al guardarlo. |
| **CARGAR** (en una fila sin documento) | Adjunta internamente el archivo de esa póliza. No envía un correo al contratista ni genera por sí mismo la alerta de revisión del portal. |
| **Cargar otro documento** | Adjunta internamente una carta o soporte; no envía correo ni cambia el estado. |

Los botones no son equivalentes: **Agregar otra póliza** modifica el listado,
**Solicitar al contratista** envía el listado pendiente existente y **Soporte:
pedir pólizas** crea un trámite extraordinario desde el contrato.

## Otros documentos para Soporte

Soporte también puede utilizar la sección **Otros** en cualquier estado. Cada
archivo:

- se almacena dentro de la carpeta **Pólizas**;
- aparece con nombre, creador y fecha;
- deja trazabilidad de carga o eliminación en el chatter;
- no se considera una póliza;
- no requiere aprobación jurídica;
- no genera notificaciones por correo.

## Acciones excepcionales de Soporte

- **Soporte: cambiar estado:** disponible en cualquier solicitud de pólizas y
  desde cualquier estado. Abre un asistente para seleccionar otro de los seis
  estados e indicar obligatoriamente el motivo. Registra estado anterior,
  nuevo, usuario y motivo en el chatter. No aprueba documentos, no carga actas
  ni envía correos. Al elegir **Finalizada** se invalida el enlace público;
  para reanudar una carga posterior se debe emitir uno nuevo. Una acción
  posterior del flujo puede recalcular el estado a partir de las pólizas.
- **Soporte: devolver:** permite pedir una corrección de una póliza aprobada
  que tenga documento, incluso si la solicitud está **Finalizada**. Exige una
  observación y envía un correo con enlace de corrección. Si ya existía un
  acta definitiva, deja de ser el acta vigente y debe cargarse otra después de
  aprobar la corrección; el archivo anterior permanece en Drive y el cambio
  queda registrado en el chatter.
- **Eliminar solicitud:** elimina el proceso completo. Si ninguna otra solicitud
  comparte la carpeta, la carpeta y sus documentos se envían a la papelera de
  Drive; si se comparte, solamente se eliminan los documentos exclusivos.
- **Solicitar al contratista:** puede regenerar el enlace y enviar conjuntamente
  todas las líneas del ciclo actual que todavía no tienen documento, aunque ya
  hubieran sido notificadas con anterioridad. No abre un wizard ni crea líneas.
  Si no hay pólizas pendientes, el servidor informa que no existe nada por
  solicitar.

## Seguridad de las acciones de Soporte

La creación extraordinaria verifica `Matriz.group_soporte` en Python. El
`sudo()` se utiliza únicamente después de esa comprobación y solo para crear el
ciclo autorizado, porque Soporte no recibe permiso general de creación o
edición sobre todas las solicitudes. Se conserva como solicitante y remitente
al usuario real que ejecutó la acción.

# Parte 3 — Referencia técnica común

## Estados del proceso

La fuente única es `EstadoSolicitudPoliza`, un `Enum` definido en
`constantes.py`.

| Orden | Código | Etiqueta funcional | Condición principal |
| --- | --- | --- | --- |
| 1 | `solicitada` | Solicitud guardada | Checklist creado antes del envío. |
| 2 | `pendiente_envio` | Póliza adicional pendiente de envío | Hay pólizas adicionales sin notificar. |
| 3 | `tramite_aprobacion` | Trámite de aprobación de pólizas | Hay documentos por cargar o revisar. |
| 4 | `correccion` | Corrección solicitada al contratista | Existe al menos una línea devuelta. |
| 5 | `pendiente_acta` | Pólizas aprobadas - Pendiente de acta | Todas las líneas están aprobadas. |
| 6 | `finalizada` | Trámite de pólizas finalizado | El acta definitiva fue cargada. |

El widget `estado_polizas_flujo` muestra el estado y el avance. Tiene JS, XML y
CSS propios; no reutiliza las clases del indicador de Contrataciones FUV.

Los botones que actúan sobre una solicitud ya abierta no devuelven otra acción
del mismo formulario. Las acciones directas permiten que el refresco suave
relea el registro y los asistentes retornan `ir.actions.act_window_close`.
Esto actualiza el estado sin duplicar la miga de pan.

## Trazabilidad en el chatter

La solicitud hereda `mail.thread` y `mail.activity.mixin`. Se publican notas
funcionales para los eventos relevantes, entre ellos:

- checklist creado;
- solicitud enviada al contratista;
- póliza agregada o retirada;
- documento inicial o corrección cargada;
- aprobación;
- devolución y observación jurídica;
- acta definitiva cargada;
- fallos de notificación que no deben ocultar un archivo ya recibido.

Cada **evento** notificado genera una nota de correo con los destinatarios
confirmados. Un mismo evento puede producir correos separados por función y
aun así quedar resumido en una sola nota de trazabilidad. La nota reúne roles,
nombres y correos; los buzones se normalizan sin distinguir mayúsculas y
minúsculas para evitar repeticiones. Si el envío falla, no se publica una
confirmación falsa; se conserva la alerta de error que corresponda. Los
registros técnicos `mail.mail` pueden borrarse tras el envío por
`auto_delete=True`; la nota funcional del chatter es independiente.

Odoo registra automáticamente autor y fecha de cada mensaje. El estado no usa
el tracking genérico porque las transiciones ya publican notas funcionales y
así se evita depender de selecciones antiguas almacenadas en metadatos.

## Seguridad del enlace público

- El token se genera con `secrets.token_urlsafe(32)`.
- En base de datos se almacena únicamente su hash SHA-256.
- El token original solo viaja en la URL enviada por correo.
- El enlace vence después de siete días.
- La comparación usa `hmac.compare_digest`.
- Las peticiones POST usan CSRF.
- El controlador busca exclusivamente la solicitud y línea indicadas y vuelve
  a validar que estén relacionadas.
- La solicitud y la línea se bloquean con `FOR UPDATE` durante la carga para
  serializar reintentos concurrentes.
- La respuesta agrega cabeceras `no-store`, `no-referrer` y `noindex`.
- El token se revoca cuando no quedan líneas públicas pendientes.
- El `sudo()` del controlador está limitado por token, relación solicitud-línea,
  estado, tamaño, extensión y carpeta de destino; no concede acceso general al
  modelo Drive.

## Google Drive y eliminación

La jerarquía esperada es:

```text
Carpeta del proyecto
└── 3. CONTRATOS PS
    └── Carpeta individual del contratista
        └── Pólizas
            ├── archivo de póliza 1
            ├── archivo de póliza 2
            └── acta definitiva
```

La subcarpeta **Pólizas** se relaciona mediante
`solicitud_poliza_drive_folder_id` (`drive.file`). Cada documento se representa
mediante el modelo `drive`. Al buscar una carpeta existente, el código filtra
por la carpeta individual `contract_id.drive_file_id` como padre y, si hace
falta, busca dentro de ese mismo padre en Google Drive. No se busca por el
nombre del contratista. Una carpeta **ya vinculada** a la solicitud recibe
una corrección de la relación local si apunta a otro padre; esa ruta no mueve
por sí sola la carpeta física en Google Drive. Verifique la ubicación física
si está probando registros antiguos que ya tenían una carpeta vinculada.

Hay dos eliminaciones diferentes:

1. **Quitar una línea pendiente:** no toca la carpeta y solo está permitido si
   no existe documento.
2. **Eliminar toda la solicitud:** solo Coordinación o Soporte pueden hacerlo.
   Se eliminan solicitud y líneas; si ninguna otra solicitud usa la carpeta, el
   `unlink()` central de `drive.file` retira los registros relacionados y mueve
   la carpeta física de Google Drive a la papelera.

No se elimina una carpeta buscando por su nombre: se sigue la relación
`solicitud_poliza_drive_folder_id` hasta el identificador real de Drive.

## Arquitectura técnica

### Modelos propios en esta carpeta

| Archivo | Responsabilidad |
| --- | --- |
| `constantes.py` | Tipos de póliza, estados y orígenes definidos con `Enum`. |
| `campos_poliza.py` | Modelo abstracto con los booleanos del checklist inicial. |
| `solicitar_poliza_wizard.py` | Valida el checklist y crea solicitud, líneas y carpeta. |
| `solicitud_poliza.py` | Modelo principal, campos, formulario, guía de usuario, reglas generales y eliminación completa. |
| `solicitud_poliza_linea.py` | Cada póliza, documento, revisión, control de notificación y acción **Quitar**. |
| `agregar_poliza_wizard.py` | Agrega un tipo no seleccionado previamente. |
| `solicitud_poliza_portal.py` | Correo inicial, tokens, portal, carga externa y alertas de revisión. |
| `solicitud_poliza_revision.py` | Aprobación, devolución, corrección, acta y notificaciones finales. |
| `solicitud_poliza_asignacion.py` | Selector, validación, acceso de Drive y notificación del responsable Jurídico. |
| `solicitud_poliza_drive.py` | Creación, reparación de ubicación y eliminación de la carpeta **Pólizas**. |
| `documentos_poliza.py` | Extiende `drive` para asociar pólizas, acta y otros documentos internos. |
| `contrataciones_fuv_extension.py` | Botones del contrato, validación, envío automático y ciclos extraordinarios de Soporte. |
| `actualizaciones.py` | Ciclos independientes de actualización por otrosí y liquidación opcional. |
| `__init__.py` | Importa los modelos del paquete. |

### Acciones de creación y envío

| Acción visible | Método | Resultado |
| --- | --- | --- |
| **Solicitar póliza** | `contrataciones_fuv.action_solicitar_poliza()` | Coordinación o Jurídica abre el checklist inicial antes de los estados terminales; al contratar se envía automáticamente. El Asistente de Coordinación no ve esta acción. |
| **Ver póliza** | `contrataciones_fuv.action_view_solicitud_poliza()` | Abre la solicitud más reciente del contrato para Coordinación, Asistencia de Coordinación, Jurídica o Soporte. |
| **Agregar otra póliza** | `solicitud.action_agregar_poliza()` | Está disponible para los perfiles con acceso al formulario; abre únicamente el selector de un tipo nuevo y crea una línea pendiente. |
| **Enviar solicitud** | `solicitud.action_enviar_solicitud()` | Envía las líneas nuevas que aún no habían sido notificadas. |
| **Solicitar al contratista** | `solicitud.action_soporte_solicitar_al_contratista()` | Soporte envía directamente todas las líneas sin documento del ciclo actual, sin abrir asistentes. |
| **Soporte: pedir pólizas** | `contrataciones_fuv.action_solicitar_poliza_soporte()` | Abre un checklist extraordinario, crea otro ciclo y lo envía inmediatamente al guardar. |

No existe un botón superior de **Nueva solicitud de pólizas** dentro del
formulario de la solicitud. Se retiró para evitar que el usuario confundiera la
creación de otro ciclo con el envío de las filas que ya estaban visibles.

Los dos botones exclusivos de Soporte se identifican en rojo:

- **Soporte: pedir pólizas**, dentro del desplegable de Soporte del contrato,
  cuyos botones se distribuyen en dos columnas;
- **Solicitar al contratista**, dentro de la solicitud.

Ambos muestran al pasar el cursor una descripción de su efecto. **Cargar otro
documento** no lleva icono para diferenciarlo de las acciones de envío.

### Archivos relacionados fuera de la carpeta

| Archivo o modelo | Uso |
| --- | --- |
| `models/contrataciones_fuv.py` | Fuente del contrato, `correo`, contratista, proyecto y `drive_file_id`. |
| `models/otro_si.py` y `models/otro_si_aprobaciones.py` | Tipo de otrosí y momento en que queda formalizado. |
| `models/cierre_contractual/cierre_proceso.py` | Proceso desde el cual puede solicitarse opcionalmente la actualización en liquidación. |
| `models/drive.py` | `GoogleDriveAuth`, carpetas `drive.file`, documentos `drive` y eliminación en Drive. |
| `models/drive_soporte.py` | Limpieza de espejos y referencias documentales al eliminar carpetas. |
| `views/contrataciones_fuv.xml` | Bloque único con **Solicitar póliza**, **Ver póliza** y **Soporte: pedir pólizas** dentro del contrato. |
| `views/solicitud_polizas_views.xml` | Wizards, formulario interno, tarjetas, guía de usuario, botones de revisión y acta. |
| `views/solicitud_polizas_portal_templates.xml` | Portal público, carga, confirmación y pantalla de enlace inválido. |
| `controllers/solicitud_polizas_controller.py` | Rutas GET y POST del portal con validación del token. |
| `controllers/__init__.py` | Registra el controlador. |
| `security/ir.model.access.csv` | ACL de solicitudes, líneas y asistentes. |
| `security/security.xml` | Grupos utilizados por las acciones y vistas. |
| `models/__init__.py` | Registra el paquete `solicitud_polizas`. |
| `__manifest__.py` | Carga XML y registra recursos del backend. |

### Frontend

| Archivo | Uso |
| --- | --- |
| `static/src/css/solicitud_polizas.css` | Estilos exclusivos del formulario y asistentes de pólizas. |
| `static/src/js/estado_polizas_flujo.js` | Widget OWL del estado. |
| `static/src/xml/estado_polizas_flujo.xml` | Plantilla del widget. |
| `static/src/css/estado_polizas_flujo.css` | Estilos aislados del widget. |
| `static/src/js/contrataciones_fuv_soft_refresh.js` | Relee el registro después de botones `object` sin recargar toda la página. |

El portal público no usa JavaScript propio: funciona con QWeb, formularios HTML
y `website.layout`.

## Origen y destino de datos

| Dato | Fuente | Uso |
| --- | --- | --- |
| Contrato | `contrataciones_fuv` | `solicitud.contract_id`. |
| Nombre visible | `contract_id.display_name` | Formularios y correos. |
| Contratista | `contract_id.display_name`; en el saludo de la solicitud, nombre del proveedor relacionado si existe | Identificación visible del contrato y saludo del correo. |
| Correo del contratista | `contract_id.correo` | Única fuente para solicitudes y correcciones. |
| Proyecto | `contract_id.proyecto_relacionado` | Usuarios responsables de revisión. |
| Carpeta del contratista | `contract_id.drive_file_id` | Padre obligatorio de **Pólizas**. |
| Tipos seleccionados | `POLIZA_SELECTION` | Una línea por tipo. |
| Archivo de cada póliza | Portal | Un registro `drive` exclusivo en `linea.documento_id`. |
| Origen del ciclo | Contrato, otrosí, cierre o Soporte | `origen`, `otro_si_id` y `cierre_proceso_id`. |
| Control de alerta | Documento cargado | `linea.documento_notificado_revision_id`. |
| Estado | `EstadoSolicitudPoliza` | Flujo y widget visual. |
| Acta | Carga interna de Jurídica | `acta_aprobacion_documento_id`. |
| Otros documentos | Carga interna | Relación múltiple `otros_documentos_ids` con registros `drive`. |
| URL pública | `web.base.url`, solicitud y token | Correo privado del contratista. |

## Matriz de correos

| Evento | Destinatario | Contenido |
| --- | --- | --- |
| Contrato pasa a contratado | `contract_id.correo` | Solicitud inicial y enlace privado. |
| Coordinación, Jurídica o Dirección Jurídica pulsa **Enviar solicitud** | `contract_id.correo` | Un correo con las líneas nuevas que todavía no habían sido notificadas. |
| Soporte pulsa **Solicitar al contratista** | `contract_id.correo` | Un correo y un enlace nuevo para todas las líneas del ciclo actual que siguen sin documento. |
| Contratista completa las pólizas del lote | Jurídico responsable | Correo de acción para revisar. |
| Contratista completa las pólizas del lote | Dirección Jurídica del proyecto | Correo separado de seguimiento. |
| Contratista completa las pólizas del lote | Coordinación y Asistencia del proyecto | Correo separado informativo. |
| Otrosí aplicable queda formalizado | `contract_id.correo` | Mismas garantías actualizadas y enlace privado. |
| Usuario solicita pólizas opcionales en liquidación | `contract_id.correo` | Mismas garantías y enlace privado. |
| Soporte crea una solicitud extraordinaria | `contract_id.correo` | Garantías seleccionadas y un único enlace privado. |
| Jurídica devuelve una póliza | `contract_id.correo` | Observación y enlace limitado de corrección. |
| Jurídica carga el acta definitiva | Contratista y Coordinación | Confirmación de acta disponible. |

Los correos se crean con `mail.mail` y se envían inmediatamente mediante el
servidor configurado en Odoo. No hay direcciones fijas escritas en este
componente. El cuerpo muestra un encabezado corto con campana según el evento
(`Solicitud de pólizas`, `Póliza cargada`, `Corrección requerida`,
`Corrección cargada` o `Acta de pólizas cargada`) y no antepone el nombre del
sistema al texto de vista previa del cliente de correo.

El remitente sigue el patrón del flujo nuevo de `contrataciones_fuv`: las
acciones internas utilizan `env.user.email` y la alerta generada por una carga
pública utiliza `contract_id.correo`, porque el contratista es quien origina
ese evento. El componente no inventa nombres visibles, direcciones
`no-reply`, remitentes de compañía ni reglas especiales de `Reply-To`.

La trazabilidad de destinatarios se centraliza en
`_registrar_notificacion_correo()`. Los métodos de negocio la llaman solo
después de un envío exitoso; cuando un evento genera correos separados (por
ejemplo, acta al contratista y a Coordinación), acumulan los destinatarios
confirmados y publican una única nota consolidada.

## Reglas importantes de mantenimiento

- No tomar el correo desde `correo_facturacion` ni inventar destinatarios: la
  fuente del contratista es exclusivamente `contrataciones_fuv.correo`.
- No crear la carpeta **Pólizas** directamente bajo `3. CONTRATOS PS`; siempre
  debe ser hija de `contract_id.drive_file_id`.
- No reutilizar CSS de cierre contractual. Los estilos de este flujo deben
  permanecer en sus archivos propios.
- No eliminar o reemplazar documentos aprobados desde la acción **Quitar**.
- No almacenar el token público en texto plano.
- Los cambios de estados deben usar `EstadoSolicitudPoliza`, no cadenas nuevas
  dispersas por el código.
- Los eventos funcionales importantes deben dejar una nota clara en el chatter.

## Verificación recomendada después de cambios

### Pruebas de Coordinación y Jurídica

1. Actualizar el módulo Matriz si se agregaron campos o vistas.
2. Confirmar que Python y XML cargan sin errores.
3. Crear un contrato de prueba con correo válido y carpeta individual.
4. Guardar un checklist con dos pólizas.
5. Pasar el contrato a `contratado` y comprobar un solo correo inicial.
6. Seleccionar los dos archivos y cargarlos juntos; comprobar que cada póliza
   conserve el suyo. Repetir seleccionando solo una y completar la otra después.
7. Aprobar una línea y devolver otra con observación.
8. Cargar la corrección y comprobar que el correo diga **Corrección de póliza**.
9. Agregar y quitar una línea sin documento; revisar el chatter y el cierre del
   diálogo de confirmación.
10. Aprobar todas, cargar el acta y comprobar el estado `finalizada`.
11. Verificar que todos los documentos estén dentro de la carpeta individual
    del contratista, en su subcarpeta **Pólizas**.
12. Formalizar un reinicio, prórroga, adición y adición/prórroga de prueba y
    comprobar que cada uno cree una única actualización cuando existan pólizas.
13. Probar un otrosí de un contrato sin pólizas y confirmar que no muestre ni
    genere la opción.
14. Desde una liquidación de tipo normal o incumplimiento con pólizas previas,
    crear manualmente la actualización opcional y verificar que el cierre
    pueda continuar sin utilizarla.

### Pruebas de Soporte

1. Abrir un contrato sin solicitudes y comprobar que aparezca **Soporte: pedir
   pólizas** solamente con el rol Soporte.
2. Crear una solicitud extraordinaria y verificar que el correo salga
   inmediatamente a `contrataciones_fuv.correo`.
3. Repetir sobre un contrato con una solicitud finalizada y confirmar que se
   cree otro ciclo sin alterar el anterior.
4. Usar **Solicitar al contratista** en un ciclo con varias líneas pendientes y
   comprobar que llegue un solo correo con todas ellas.
5. Cargar dos archivos en **Otros** y comprobar que ambos permanezcan visibles
   con usuario y fecha.
6. Eliminar uno de esos documentos y revisar la nota de trazabilidad.
7. Comprobar que **Cargar otro documento** no envíe correo ni cambie el estado.
8. Verificar que un usuario sin `group_soporte` no pueda ejecutar por RPC la
   creación extraordinaria.

## Alcance y compatibilidad

Este componente extiende de forma aislada `contrataciones_fuv`, `otro_si` y
`contrataciones_fuv.cierre.proceso`. No cambia los estados propios del contrato,
del otrosí ni del cierre: reacciona a sus eventos finales y administra su propio
flujo de pólizas.

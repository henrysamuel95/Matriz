# Finalización normal de contratos FUV - Guía funcional y de pruebas

## 1. Objetivo

Este desarrollo permite avisar a Coordinación cuando se acerca el vencimiento y
finalizar un FC por cumplimiento normal de su vigencia.

La finalización normal **no crea** actas, carpetas de cierre ni movimientos de
recursos. Tampoco modifica valores, fondos o documentos de Google Drive.

No debe confundirse con los cierres por acta de anulación, liquidación por mutuo
acuerdo o liquidación unilateral.

## 2. Conceptos necesarios

### FC

Es el registro principal de `Contrataciones FUV`.

### Vigencia efectiva

Es la fecha que utiliza el proceso para calcular avisos y finalización. Corresponde
a la fecha más lejana entre:

- la terminación del FC; y
- las terminaciones de prórrogas, adiciones/prórrogas, reinicios o modificatorios
  de tiempo formalizados.

Un borrador o trámite no formalizado no amplía la vigencia. La fecha de inicio del
FC o del otrosí no determina los hitos de aviso. El sistema no sobrescribe la fecha
de terminación original del FC.

### Día 0

Es el mismo día de la vigencia efectiva. El contrato continúa vigente durante todo
ese día y solo se considera vencido desde el día siguiente, según
`America/Bogota`.

## 3. Avisos de vencimiento

El sistema envía avisos individuales exactamente:

- 15 días antes de la vigencia efectiva;
- 5 días antes;
- el mismo día del vencimiento.

No recupera avisos cuyo hito ya pasó. Por ejemplo, si se ejecuta por primera vez a
4 días del vencimiento, no envía el aviso de 5 días.

Los avisos pueden enviarse aunque exista una condición que después impida finalizar
el contrato. Su propósito es informar el vencimiento, no certificar que el FC esté
listo para finalizar.

No se crean actividades. Cada aviso deja trazabilidad en el chatter del FC.

### Destinatarios

Reciben el aviso los usuarios que cumplan simultáneamente estas condiciones:

- están activos;
- son usuarios internos;
- tienen el rol **Coordinación**; y
- están en `users_with_access` del proyecto o son la persona que creó el FC.

El creador no necesita estar en la lista del proyecto, pero sí debe conservar el
rol de Coordinación y un correo válido. No se notifica al contratista.

Si no existe ningún coordinador válido, o uno de los coordinadores encontrados no
tiene correo válido, el proceso registra una incidencia y no envía los correos de
ese FC.

### Resultado del correo

La traza **Aceptado por el servidor de correo** significa que el servidor SMTP
aceptó el mensaje. La recepción en bandeja debe comprobarse directamente en el
correo del destinatario.

El remitente se toma primero del correo de la compañía y, si está vacío, del usuario
que ejecuta el proceso.

## 4. Control de duplicados

Un aviso se identifica por contrato, fecha de vigencia, destinatario e hito. Volver
a ejecutar el cron sin cambios no reenvía el mismo aviso.

Cambiar y guardar la fecha permite revisar el FC nuevamente durante el mismo día,
pero regresar a una combinación que ya fue enviada tampoco genera otro correo.
Para una prueba limpia, utilice FC nuevos o fechas que no se hayan probado antes.

## 5. Finalización manual

1. Abra el FC.
2. Confirme que está en **Contratado** y que su vigencia terminó antes de hoy.
3. Pulse **Solicitar cierre / finalizar**.
4. Seleccione **Finalizar normalmente**.
5. Registre una observación si lo desea. Es opcional.
6. Pulse **Guardar solicitud**.

Solo Coordinación o Soporte pueden ejecutar esta opción. Asistencia conserva las
opciones de cierre con acta, pero no puede finalizar normalmente.

La acción manual ignora la fecha de corte de la automatización y sigue disponible
aunque el interruptor automático esté apagado. Siempre conserva las validaciones de
estado, vigencia, otrosíes, suspensión y procesos de cierre.

Si el interruptor está apagado, el cambio manual a **Finalizado** y su nota de
chatter sí se realizan, pero el correo queda pausado junto con los demás avisos.

## 6. Condiciones para finalizar

El FC solo puede finalizar normalmente si:

- está exactamente en estado **Contratado**;
- tiene una vigencia efectiva válida y esta terminó antes de hoy;
- no tiene otrosíes pendientes de formalizar;
- el último movimiento formalizado no mantiene una suspensión;
- no tiene una excepción de otrosí posterior pendiente;
- no tiene ningún proceso de cierre contractual creado;
- tiene destinatarios de Coordinación válidos y con correo.

Cualquier proceso de cierre creado bloquea esta opción, sin importar si corresponde
a anulación, mutuo acuerdo o unilateral, ni el estado alcanzado por ese proceso.

## 7. Resultado de la finalización

Al finalizar correctamente:

- el FC cambia a **Finalizado**;
- guarda fecha y hora de finalización;
- guarda la vigencia efectiva utilizada;
- publica la finalización y la observación opcional en el chatter;
- envía un correo individual de finalización a cada coordinador válido;
- no crea acta, carpeta, actividad ni liberación de recursos.

El correo de finalización explica el cierre normal e incluye contratista, proyecto,
fecha original, vigencia efectiva, fecha y hora de finalización, origen manual o
automático y las observaciones cuando fueron registradas manualmente. También aclara
que no se generó acta ni se liberaron recursos y recuerda la ruta autorizada para
un otrosí posterior.

Si la automatización encuentra un bloqueo, conserva el FC en **Contratado** y
publica una incidencia en el chatter. La misma incidencia no se repite mientras el
motivo no cambie.

## 8. Automatización

El proceso automático revisa como máximo 50 FC por ejecución, salvo que Soporte
cambie el parámetro técnico del lote. Solo busca FC en **Contratado** cuya vigencia
efectiva sea hoy, esté vencida o se encuentre dentro de los próximos 15 días.

En el código actual:

```text
Interruptor automático: encendido
Fecha de corte de finalización automática: 01/11/2027
Zona horaria: America/Bogota
```

La fecha de corte se compara con la vigencia efectiva y es inclusiva. Solo limita
la finalización automática. No limita los avisos ni la finalización manual.

En instalaciones actualizadas, tanto **Alerta de vencimiento de contratos** como
**FUV: finalización normal por lotes** llegan al proceso nuevo. Para una prueba
manual ejecute solo una de las dos acciones y espere a que termine.

> **Precaución:** el cron no procesa únicamente el FC que el evaluador tiene
> abierto. Puede avisar o finalizar todos los FC elegibles del lote. No reduzca la
> fecha de corte ni ejecute estas pruebas en producción o en una base compartida sin
> revisar primero los registros que podrían resultar afectados.

## 9. Resumen cuando hay más de 20 avisos

Los correos individuales siempre se generan. Si una misma persona acumula más de
20 avisos individuales en un día, el sistema añade un resumen programado para las
23:55 de Bogotá.

Con exactamente 20 no hay resumen. El resumen es adicional y no reemplaza los
mensajes individuales.

## 10. Prueba guiada

Realice las pruebas en una base de pruebas. Use un FC diferente por escenario cuando
sea posible y conserve capturas del formulario, chatter, correo y log.

### Preparación común

1. Use un FC en estado **Contratado**.
2. Asigne un proyecto y confirme quién creó el FC.
3. Confirme que el destinatario está activo, es interno, tiene rol Coordinación y
   posee correo válido.
4. Evite actas, suspensión, excepción u otrosí pendiente, salvo cuando ese sea el
   escenario que se quiere probar.
5. Guarde el FC antes de ejecutar el cron.

### Matriz mínima

| Caso | Configuración respecto de hoy | Resultado esperado |
| --- | --- | --- |
| Aviso 15 días | Vigencia efectiva = hoy + 15 | Un correo individual y traza SMTP |
| Aviso 5 días | Vigencia efectiva = hoy + 5 | Un correo individual y traza SMTP |
| Aviso día 0 | Vigencia efectiva = hoy | Un correo; el FC sigue Contratado |
| Fuera del hito | Vigencia efectiva = hoy + 4 | No envía correo |
| Duplicado | Repetir cron sin cambios | No reenvía |
| Prórroga pendiente | Fecha posterior, sin formalizar | Ignora esa fecha; usa la mayor ya formalizada y bloquea finalización |
| Prórroga formalizada | Fecha posterior formalizada | Usa la nueva fecha más lejana |
| Dos ampliaciones | Dos fechas formalizadas | Usa la mayor, no la última creada |
| Manual vigente | Vigencia efectiva >= hoy | Muestra error y no finaliza |
| Manual vencido | Vigencia efectiva < hoy, sin bloqueos | Cambia a Finalizado y notifica |
| Automática vencida | Vigencia < hoy y >= corte, sin bloqueos | Cambia a Finalizado y notifica |
| Automática anterior al corte | Vigencia < corte | No finaliza automáticamente |
| Con otrosí pendiente | Cualquier otrosí no formalizado | Registra incidencia; no finaliza |
| Con suspensión vigente | Último movimiento formalizado es suspensión | Registra incidencia; no finaliza |
| Con proceso de cierre | Existe cualquier cierre contractual | Registra incidencia; no finaliza |
| Varios FC | Varios hitos en una ejecución | Procesa cada FC sin mezclar destinatarios |
| 21 avisos | 21 individuales al mismo usuario/día | 21 individuales y un resumen adicional |

### Evidencia mínima por prueba

- Log con `fc_id`, `vigencia`, `dias`, `hito` y `coordinadores`.
- Estado y fecha efectiva visibles en el FC.
- Traza del chatter.
- Correo en bandeja cuando se espera recepción.
- Confirmación de que valores, recursos y actas no cambiaron al finalizar.

## 11. Lectura rápida del log

```text
seleccionados=4
```

El lote encontró cuatro FC pendientes de revisión ese día.

```text
dias=5 hito=True
```

Corresponde enviar el aviso de 5 días.

```text
dias=4 hito=False
```

El FC se revisó, pero no corresponde enviar aviso.

```text
Aviso omitido: ya registrado
```

La protección de duplicados encontró el mismo contrato, vigencia, destinatario e
hito.

```text
[PRORROGA] ... formalizado=True ... aporta_fecha_efectiva=True
```

La ampliación formalizada aporta la fecha utilizada por el proceso.

## 12. Otrosí posterior a una finalización normal

Un FC finalizado normalmente no permite crear un otrosí de forma directa:

1. Soporte pulsa **Autorizar otrosí posterior**, selecciona al usuario de Dirección
   FUV, registra el motivo y puede cargar un documento de respaldo opcional.
2. La persona seleccionada de Dirección FUV autoriza la solicitud.
3. Soporte pulsa **Iniciar otrosí autorizado**.
4. El FC se reabre temporalmente a **Contratado**.
5. Soporte crea el único otrosí ligado a esa autorización.
6. El otrosí continúa su flujo normal.
7. Cuando queda formalizado, se cierra la excepción y la vigencia efectiva vuelve a
   controlar el proceso.

La ventana se presenta como una única autorización. Sus estados internos no se
muestran como otro flujo. Solicitud, autorización, inicio, creación, formalización
y cancelación quedan visibles en el chatter del FC. El documento cargado al crear
la solicitud también se adjunta allí.

Soporte no puede autoaprobar la solicitud. Una autorización utilizada no se puede
cancelar ni eliminar. Si se cancela antes de crear el otrosí, el FC reabierto vuelve
a **Finalizado** cuando corresponde.

## 13. Resultado de aceptación

Una prueba se considera aprobada cuando coincide el estado funcional, el log, el
chatter y el correo esperado. La aceptación SMTP por sí sola no certifica recepción
en la bandeja ni reemplaza las pruebas de permisos.

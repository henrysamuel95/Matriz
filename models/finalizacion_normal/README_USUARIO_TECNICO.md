# Finalización normal de contratos FUV - Guía de usuario técnico

## Alcance

El paquete `models/finalizacion_normal` extiende `contrataciones_fuv` para cerrar un FC por vencimiento normal de su vigencia.

No implementa ni modifica los flujos de:

- anulación;
- liquidación por mutuo acuerdo;
- liquidación unilateral;
- liberación RFC;
- reintegro de recursos;
- documentos de Drive.

Su integración con `cierre_contractual` se limita a verificar que no exista un proceso de cierre creado.

## Archivos

| Archivo | Responsabilidad |
| --- | --- |
| `reglas.py` | Funciones puras de vigencia, vencimiento, bloqueos y umbral de resumen. |
| `contrato.py` | Extensión del FC, vigencia efectiva, finalización, cron y lotes. |
| `notificaciones.py` | Modelo de avisos, correos individuales, resultados SMTP y resúmenes. |
| `reapertura.py` | Modelo de autorización para otrosí posterior. |
| `otrosi.py` | Restricciones de creación/movimiento/formalización del otrosí autorizado. |
| `asistentes.py` | Agrega `finalizar` al asistente existente de cierre. |

## Interruptor y fecha de corte

En `contrato.py`:

```python
FINALIZACION_NORMAL_ACTIVA = True
FINALIZACION_NORMAL_FECHA_INICIO = '2027-11-01'
```

`FINALIZACION_NORMAL_ACTIVA` controla el cron de avisos, la finalización automática
y la generación/sincronización de resúmenes.

La finalización manual se mantiene disponible aunque la automatización esté desactivada.
En ese caso `_fn_finalizar(manual=True)` cambia el estado y publica su nota, pero
`_fn_encolar_avisos()` no crea el correo porque también consulta el interruptor.

El registro XML `Matriz.fn_habilitado` se conserva por compatibilidad, pero la
implementación actual no lo consulta: el interruptor efectivo es exclusivamente la
constante Python y requiere reiniciar Odoo después de cambiarla.

`FINALIZACION_NORMAL_FECHA_INICIO` limita únicamente la **finalización automática**. Los avisos de vencimiento continúan revisando contratos históricos en estado `Contratado`.

## Vigencia efectiva

Campo:

```python
fn_fecha_vigente
```

Se calcula a partir de `fecha_terminacion` y de movimientos formalizados que pueden ampliar el tiempo:

- prórroga;
- adición/prórroga;
- reinicio;
- modificatorio con tiempo o ambos.

La regla utiliza la fecha más lejana mediante `fecha_vigente()`.

No sobrescribe `fecha_terminacion`.

## Estados de otrosí considerados formalizados

`ESTADOS_OTROSI_FORMALIZADOS` incluye compatibilidad con estados históricos y nuevos, entre ellos:

- `contratado`;
- `adicion_formalizada`;
- `prorroga_formalizada`;
- `adicion_prorroga_formalizada`;
- `modificatorio_formalizado`;
- `reinicio_formalizado`;
- `suspendido`;
- `suspension_prorrogada`.

Un otrosí no formalizado se considera pendiente de manera conservadora y bloquea la finalización.

## Regla de vencimiento

`vencio(fecha_fin, hoy)` retorna verdadero únicamente cuando:

```text
fecha_fin < hoy
```

Por lo tanto, el último día completo continúa vigente.

## Bloqueos

`bloqueo_finalizacion()` evalúa:

1. estado diferente de `contratado`;
2. falta de fecha;
3. excepción de reapertura pendiente;
4. proceso de cierre contractual existente;
5. otrosíes pendientes;
6. suspensión vigente.

En `contrato.py`, cualquier registro en `cierre_proceso_ids` excluye la finalización normal, sin importar el tipo o estado del cierre.

## Entrada única de finalización

Método:

```python
_fn_finalizar(manual=False, observaciones=False)
```

Es la entrada central tanto para ejecución manual como automática.

No modifica fondos ni documentos.

Al finalizar registra:

- `estado_contratacion = 'finalizado'`;
- `fn_finalizado_el`;
- `fn_fecha_fin_finalizada`;
- limpieza de `fn_incidencia`;
- chatter y aviso correspondiente.

La llamada al aviso de finalización transmite el origen (`manual` o `automatico`)
y las observaciones. La plantilla agrega fecha original, vigencia efectiva, fecha
local de finalización, contratista, proyecto, tipo de finalización y la advertencia
de que no se generó acta ni se movieron recursos. Los recordatorios 15/5/0 conservan
su plantilla breve.

## Finalización manual

`asistentes.py` amplía:

```python
contrataciones_fuv.close.wizard
```

agregando:

```text
tipo_acta = finalizar
```

Solo permite ejecutar la acción a:

- `Matriz.group_contratacion_coordinador`;
- `Matriz.group_soporte`.

No incluye Asistencia.

Las demás opciones del wizard siguen delegando al comportamiento original mediante `super()`.

## Avisos de vencimiento

`DIAS_AVISO_VENCIMIENTO`:

```python
{15, 5, 0}
```

`en_ventana_aviso()` exige coincidencia exacta. No recupera hitos pasados.

Los destinatarios se obtienen con `_fn_coordinadores()`:

- usuarios de `proyecto_relacionado.users_with_access`;
- unido con `create_uid`;
- activos;
- internos;
- con rol `group_contratacion_coordinador`.

Si cualquiera de los usuarios resultantes carece de correo normalizable, el FC
completo queda con incidencia y no se intenta un envío parcial a los demás.

Los avisos se evalúan antes de aplicar los bloqueos funcionales de finalización.
Por ello un FC con otrosí pendiente, suspensión o cierre creado todavía puede
recibir su recordatorio de 15, 5 o 0 días; después permanece bloqueado para finalizar.

## Registro de avisos

Modelo:

```text
fuv.finalizacion.aviso
```

Mantiene trazabilidad de:

- FC;
- usuario;
- correo;
- tipo de aviso;
- fecha;
- `mail.mail`;
- resultado;
- detalle de fallo;
- inclusión en resumen.

Las restricciones/consultas del modelo evitan duplicar avisos equivalentes.

## Resultado de correo

`mail.mail` se extiende para sincronizar el resultado definitivo.

Se contempla que Odoo 16 puede usar estados provisionales durante el envío. La traza definitiva se actualiza después del postprocesamiento del correo.

Un fallo posterior de envío queda auditado, pero no revierte por sí solo un FC ya finalizado.

## Resúmenes

`requiere_resumen(cantidad)` retorna verdadero solo cuando:

```text
cantidad > 20
```

Exactamente 20 avisos no genera resumen.

Los resúmenes son adicionales a los mensajes individuales.

Se crea un resumen por destinatario y fecha, programado para las 23:55 de Bogotá.
Cuenta avisos individuales de tipo recordatorio y finalización, incluidos los que
terminaron enviados, en cola o fallidos.

La zona horaria se obtiene desde:

```text
Matriz.fn_zona_horaria
```

con valor por defecto:

```text
America/Bogota
```

## Incidencias

Campo:

```python
fn_incidencia
```

`_fn_registrar_incidencia()` solo publica en chatter cuando el motivo cambia, evitando duplicación continua.

## Procesamiento por lotes

Parámetro:

```text
Matriz.fn_lote
```

Valor por defecto:

```text
50
```

El código limita el lote entre 1 y 200.

`cron_verificar_alertas_fechas()` del heredado redirige el cron histórico al proceso nuevo.

El procesamiento utiliza bloqueo de fila del FC y savepoints para aislar errores entre contratos.

`fn_revision_fecha` evita revisar repetidamente un FC durante el mismo día cuando no hubo cambios relevantes.

Cambios de estado, plazo o formalización de otrosí pueden invalidar esa marca para forzar una nueva revisión.

La clave única de cada recordatorio incluye FC, vigencia efectiva, usuario e hito.
Invalidar `fn_revision_fecha` permite reevaluar, pero nunca duplica una clave que ya
existe.

## Concurrencia

`_fn_bloquear_fila()` ejecuta un `FOR UPDATE` sobre `contrataciones_fuv`.

La creación y formalización del otrosí posterior reutilizan el mismo bloqueo para evitar carreras entre:

- cron;
- reapertura;
- creación del otrosí;
- cambio de fechas o estados.

## Reapertura para otrosí posterior

Modelo:

```text
fuv.finalizacion.autorizacion
```

Estados:

- `solicitada`;
- `autorizada`;
- `utilizada`;
- `cancelada`.

Son estados técnicos para proteger las transiciones y no se muestran como un
statusbar en la autorización. La trazabilidad funcional se publica en el chatter
de `contrataciones_fuv`; el formulario de autorización no tiene chatter propio.

Solo Soporte puede crear la solicitud.

La creación solo acepta:

- `contract_id`;
- `motivo`;
- `director_id`;
- `documento_autorizacion` y su nombre de archivo opcionales.

Los campos de aprobación se fuerzan desde servidor para impedir simular una autorización desde el cliente.

## Autorización de Dirección FUV

`action_autorizar()` exige simultáneamente:

- pertenecer a `Matriz.group_contratacion_director_fuv`;
- ser exactamente el usuario seleccionado en `director_id`;
- solicitud en estado `solicitada`.

Soporte no puede autorizarla.

## Inicio del otrosí autorizado

`action_iniciar_otrosi()` solo puede ejecutarlo Soporte después de la autorización.

Reabre temporalmente:

```text
finalizado → contratado
```

y marca:

```python
fn_reapertura_pendiente = True
fn_autorizacion_activa_id = autorizacion
```

No cambia importes ni documentos.

## Protección del modelo otro_si

`otrosi.py` añade controles de servidor:

- no permite crear directamente sobre un FC finalizado;
- durante una reapertura solo Soporte puede crear el único otrosí autorizado;
- `fn_autorizacion_id` no puede ser enviado arbitrariamente desde cliente;
- un otrosí autorizado no puede trasladarse a otro FC;
- no puede trasladarse un otrosí a un FC finalizado o en excepción.

Al formalizar el otrosí autorizado se limpian:

- `fn_reapertura_pendiente`;
- `fn_otrosi_excepcion_id`;
- `fn_autorizacion_activa_id`;
- `fn_revision_fecha`.

La vigencia efectiva vuelve entonces a controlar el proceso normal.

## Cancelación

`action_cancelar()` solo puede usarlo Soporte.

Una autorización `utilizada` no puede cancelarse.

Si el FC fue reabierto pero todavía no se creó el otrosí, la cancelación puede restaurar:

```text
estado_contratacion = finalizado
```

La autorización no se elimina; `unlink()` está bloqueado para conservar trazabilidad.

## Diferencia frente a cierre_contractual

Este paquete no debe documentarse ni probarse como parte de `cierre_contractual`.

La relación entre ambos es únicamente de exclusión:

```text
si existe cualquier cierre_proceso_ids
→ no se permite finalización normal
```

No reutiliza estados como `financiera`, `juridica`, `firma_coordinador` o `liberacion_rfc`.

## Pruebas recomendadas

Validar de forma aislada:

1. aviso exacto a 15 días;
2. aviso exacto a 5 días;
3. aviso el día 0;
4. no repetir avisos;
5. último día todavía vigente;
6. finalización desde el día siguiente;
7. prórroga formalizada;
8. prórroga en borrador;
9. reinicio formalizado;
10. suspensión vigente;
11. otrosí pendiente;
12. existencia de cualquier proceso de cierre;
13. finalización manual por Coordinación;
14. finalización manual por Soporte;
15. rechazo a Asistencia;
16. resumen con 21 avisos y ausencia de resumen con 20;
17. SMTP fallido;
18. solicitud de otrosí posterior por Soporte;
19. autorización exclusivamente por Dirección FUV seleccionada;
20. creación de un solo otrosí autorizado;
21. cancelación antes de uso;
22. formalización del otrosí y retorno del control de vigencia.

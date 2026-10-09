# Cierre contractual - Documentación técnica

## Alcance

El paquete `models/cierre_contractual` implementa el flujo de cierre contractual para `contrataciones_fuv`.

Modalidades:

- `anulacion`: acta de anulación.
- `normal`: liquidación por mutuo acuerdo.
- `incumplimiento`: liquidación unilateral.

El objeto funcional del cierre actual es siempre el **FC completo**. Los campos relacionados con `otrosi_id` se conservan por compatibilidad e información histórica, pero `action_confirmar_cierre()` normaliza las solicitudes nuevas a:

```python
self.objeto_cierre = 'contrato'
self.otrosi_id = False
```

## Archivos

| Archivo | Responsabilidad |
| --- | --- |
| `constantes.py` | Enum y selección de estados del flujo. |
| `cierre_proceso.py` | Modelo principal, permisos, acciones, devoluciones, correo y trazabilidad. |
| `cierre_wizard.py` | Creación de la solicitud y fotografía contractual. |
| `cierre_drive.py` | Creación, reutilización, reparación local y limpieza de la carpeta de Drive. |
| `documentos_anulacion.py` | Documentos y validaciones específicas de anulación. |
| `documentos_mutuo_acuerdo.py` | Documentos y validaciones de mutuo acuerdo. |
| `documentos_unilateral.py` | Documentos y validaciones de liquidación unilateral. |
| `contrataciones_fuv_extension.py` | Integración con FC, cierres activos, tablero y reintegros. |
| `otrosi_extension.py` | Presentación contextual de otrosí heredados/compatibles con el cierre. |
| `soporte.py` | Diagnóstico y recuperación auditada para Soporte. |

## Modelo principal

`contrataciones_fuv.cierre.proceso`

Hereda:

- `mail.thread`
- `mail.activity.mixin`

Relación principal:

```text
contrataciones_fuv
    └── cierre_proceso_ids
            └── contrataciones_fuv.cierre.proceso
```

El proceso guarda, entre otros:

- contrato y proyecto;
- tipo de acta;
- estado;
- usuario y fecha de solicitud;
- razones;
- datos de ejecución financiera;
- responsable jurídico;
- referencias a documentos;
- fotografía de valores contractuales;
- carpeta `Cierre contractual`;
- estado de retorno de una devolución;
- valor liberado;
- fondo original;
- usuario y fecha de liberación;
- estado de notificación al contratista.

La cabecera del formulario expone `action_abrir_guia_usuario()` sin restricción
de grupo adicional. La acción abre el modelo transitorio
`contrataciones_fuv.cierre.guia.usuario`; su ACL para `base.group_user` permite
que cualquier usuario interno que ya pueda consultar el proceso abra la guía.
La guía es informativa y no escribe sobre el cierre.

## Estados

Definidos en `EstadoCierreContrato`:

```text
solicitud
financiera
juridica
firma_coordinador
liberacion_rfc
finalizado
cancelado
```

Etiquetas funcionales:

| Valor | Etapa |
| --- | --- |
| `solicitud` | Coordinador solicita |
| `financiera` | Financiera certifica |
| `juridica` | Jurídico revisa |
| `firma_coordinador` | Coordinador gestiona firma |
| `liberacion_rfc` | Presupuesto libera RFC |
| `finalizado` | Finalizado |
| `cancelado` | Cancelado |

La liquidación unilateral omite `firma_coordinador` y pasa de Jurídica a `liberacion_rfc`.

## Permisos principales

Grupos utilizados:

- `Matriz.group_contratacion_coordinador`
- `Matriz.group_contratacion_asis_coordinador`
- `Matriz.group_financiera_cierre_contrato`
- `Matriz.group_contratacion_director_juridico`
- `Matriz.group_contratacion_juridico`
- `Matriz.group_presupuestos`
- `Matriz.group_soporte`

La certificación del cierre valida exclusivamente
`Matriz.group_financiera_cierre_contrato`; no requiere
`Matriz.group_contratacion_financiero` ni pertenencia a listas del proyecto.
La asignación del responsable jurídico consulta globalmente los usuarios activos
de `Matriz.group_contratacion_juridico`, sin exigir vinculación al proyecto.

Reglas generales:

- Solicitud: Coordinación / Jurídica / Dirección Jurídica.
- Financiera: grupo Financiera cierre contrato.
- Jurídica: Dirección Jurídica o Jurídico asignado según la acción.
- Firma: Coordinación / Asistencia.
- Liberación: Presupuesto; existen permisos complementarios de Financiera en acciones/documentos del flujo unilateral.
- Soporte: puede intervenir en recuperación técnica y en cargas autorizadas, sin saltarse los efectos finales.

Las acciones RPC vuelven a validar estado y grupo; no dependen únicamente de la visibilidad de botones.

## Creación del cierre

`contrataciones_fuv.action_close_contract()` exige:

1. usuario Coordinación, Jurídica o Dirección Jurídica;
2. FC en estado `contratado`;
3. ausencia de otro cierre activo.

Abre `contrataciones_fuv.close.wizard`.

`action_confirmar_cierre()`:

- vuelve a validar permisos y estado;
- fuerza el objeto a contrato completo;
- congela el fondo seleccionado en el FC;
- guarda la fotografía de valores;
- crea `contrataciones_fuv.cierre.proceso`;
- crea o relaciona la carpeta de Drive;
- registra el evento en el chatter del FC y del cierre.

## Fotografía contractual y otrosí

Se conservan campos como:

- `valor_contrato_original`
- `valor_otrosi_formalizados`
- `valor_otrosi_seleccionado`
- `valor_contractual_actualizado`
- `valor_documento_cierre`
- `valor_integral_registrado`

Aunque persisten campos y utilidades para `objeto_cierre='otrosi'`, las solicitudes nuevas se normalizan al FC completo.

`_otrosi_formalizados_con_valor_para_cierre()` identifica adiciones, adición/prórroga y modificatorios con valor que ya estén formalizados. Su información sirve para cálculo/compatibilidad; no implica cerrar el otrosí de manera independiente.

## Flujo y devoluciones

Acciones principales:

- `action_enviar_certificacion_financiera()`
- `action_enviar_revision_juridica()`
- `action_asignar_juridico_cierre()`
- `action_enviar_firma_coordinador()`
- `action_enviar_liberacion_rfc()`
- `action_enviar_liberacion_rfc_unilateral()`
- `action_finalizar_cierre_contractual()`

Devoluciones:

- Financiera → Solicitud.
- Jurídica → Financiera.
- Jurídica → Solicitud.
- Firma → Jurídica.
- Liberación → Firma para anulación/mutuo.
- Liberación → Jurídica para unilateral.

`estado_retorno_devolucion` conserva el origen cuando el flujo necesita regresar a la etapa que devolvió.

`observaciones_etapa` es el campo operativo unificado. Las observaciones heredadas permanecen para preservar datos históricos.

## Documentos

Cada tipo de cierre extiende el mismo proceso.

### Anulación

Configuración principal:

- `solicitud_anulacion_id`
- `certificado_financiero_id`
- `acta_borrador_id`
- `acta_firmada_id`
- `soporte_liberacion_rfc_id`

### Mutuo acuerdo

Gestiona:

- soporte de solicitud;
- certificado financiero;
- acta para firma;
- acta firmada;
- soporte de liberación de saldos.

La finalización valida que el valor liberado no supere el saldo certificado por Financiera.

### Unilateral

Gestiona:

- soporte de solicitud;
- certificado financiero;
- acta unilateral;
- soporte de liberación RFC.

El paso de Jurídica a liberación utiliza `action_enviar_liberacion_rfc_unilateral()`, notifica al contratista y evita la etapa normal de firma.

Los métodos `action_save_and_close()` de documentos vuelven a comprobar:

- proceso relacionado;
- tipo de cierre;
- etapa vigente;
- permisos actuales.

## Google Drive

`cierre_drive.py` administra una única subcarpeta lógica:

```text
Carpeta individual del contratista
└── Cierre contractual
```

`_crear_carpeta_cierre_contractual()`:

1. exige una carpeta individual válida del contratista;
2. evita crear el cierre directamente en la carpeta general `3. CONTRATOS PS`;
3. reutiliza primero un registro local válido;
4. busca después la carpeta en Google Drive;
5. crea la carpeta solo si no existe;
6. crea o repara el espejo `drive.file`;
7. elimina la carpeta recién creada si falla el registro local.

`_asegurar_ubicacion_local_carpeta_cierre()` corrige el árbol local sin mover datos en Google Drive.

`_otorgar_acceso_lector_cierre()` concede permiso `reader` sobre la carpeta del
cierre al enviar el proceso a Financiera o asignar el responsable Jurídico. El
permiso de carpeta cubre los documentos existentes y futuros. Antes de crearlo,
consulta los permisos por correo y omite los accesos ya existentes.

`financiero_responsable_id` conserva al usuario del grupo
`Matriz.group_financiera_cierre_contrato` que carga el certificado o envía la
certificación a Jurídica. Las cargas de `acta_firmada_id` y
`acta_unilateral_firmada_id` llaman a
`_notificar_acta_firmada_financiera()`, que envía una notificación informativa
individual con enlace al proceso. Para procesos históricos sin responsable,
solo se asigna automáticamente si existe un único candidato activo con correo.

Al eliminar un cierre, la carpeta solo se elimina cuando ningún otro proceso de cierre la utiliza.

## Recursos liberados

El proceso conserva `fondo_origen` al momento de crear el cierre.

Valores admitidos:

- `es_produccion`
- `es_personal_fuv`
- `contratos_vco`

`_obtener_recursos_liberados_por_fondo()` suma únicamente cierres:

- en estado `finalizado`;
- con `valor_liberado_rfc > 0`;
- del mismo proyecto.

La función es de consulta y no escribe directamente en los saldos. Las fórmulas del FC consumen ese resultado, por lo que el recálculo es idempotente.

Para cierres históricos sin `fondo_origen`, se usa como respaldo `contract_id.select_fondos`.

## Validaciones de finalización

`action_finalizar_cierre_contractual()` exige la etapa `liberacion_rfc`.

Además:

- debe confirmarse `rfc_liberado`;
- anulación exige un valor positivo y usa su valor de referencia;
- mutuo/unilateral no permiten valor negativo;
- ningún cierre puede superar su valor de referencia;
- mutuo/unilateral validan también contra el saldo certificado cuando corresponde.

Al finalizar se registra:

- `estado = finalizado`
- `fecha_liberacion_rfc`
- `liberado_por_id`
- `valor_liberado_rfc`

y se deja trazabilidad en chatter.

## Notificación unilateral

La liquidación unilateral utiliza correo específico al pasar desde Jurídica a liberación.

El proceso conserva:

- `contratista_notificado`
- `fecha_notificacion_contratista`

El panel de Soporte detecta inconsistencias entre la marca y la fecha.

## Soporte

`soporte.py` incorpora diagnóstico y recuperación.

Acciones:

- `action_soporte_cambiar_estado_cierre()`
- `action_soporte_reparar_carpeta_cierre()`
- `action_soporte_limpiar_retorno_cierre()`
- `action_soporte_abrir_fc_cierre()`

Toda acción correctiva exige `soporte_motivo` y registra el evento en el chatter del proceso y del FC.

El cambio manual de estado solo acepta etapas operativas:

```text
solicitud
financiera
juridica
firma_coordinador
liberacion_rfc
```

No permite:

- forzar `finalizado`;
- forzar `cancelado`;
- reabrir un cierre finalizado o cancelado.

El diagnóstico revisa, entre otros:

- carpeta del contratista;
- carpeta del cierre;
- relación histórica con otrosí;
- retorno de devolución;
- coherencia de notificación;
- coherencia de usuario/fecha de liberación;
- documentos esperados según la etapa.

## Compatibilidad

El componente conserva campos históricos para evitar pérdida de información y soportar cierres existentes.

En especial:

- campos antiguos de observaciones;
- `objeto_cierre`;
- `otrosi_id`;
- valores históricos asociados a otrosí;
- cierres antiguos sin `fondo_origen`.

No deben eliminarse sin una migración explícita de datos.

## Verificación recomendada

Después de cambios en este paquete se recomienda validar como mínimo:

1. creación de cada uno de los tres tipos de cierre;
2. permisos por rol y etapa;
3. envío y devoluciones;
4. asignación de Jurídico;
5. documentos obligatorios;
6. flujo unilateral sin etapa de firma;
7. correo al contratista en unilateral;
8. creación/reutilización de la carpeta de Drive;
9. valor liberado y límite financiero;
10. reintegro al fondo correcto;
11. herramientas de Soporte;
12. protección de cierres finalizados/cancelados;
13. trazabilidad en chatter del cierre y del FC.

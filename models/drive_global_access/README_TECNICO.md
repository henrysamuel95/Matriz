# Lectores globales de Google Drive - Documentación técnica

## Alcance

El paquete `models/drive_global_access` administra permisos `reader` de Google Drive de forma controlada y trazable por proyecto.

Modelos:

- `drive.global.access`
- `drive.global.access.grant`

Archivos:

| Archivo | Responsabilidad |
| --- | --- |
| `access_config.py` | Configuración singleton, sincronización, auditoría, revocación y procesamiento por lotes. |
| `access_grant.py` | Registro local de permisos creados por proyecto/usuario. |

## Modelo de configuración

`drive.global.access`

Hereda:

- `mail.thread`
- `mail.activity.mixin`

La configuración es singleton mediante:

```text
singleton_key = global
```

Existe una restricción SQL que impide crear más de una configuración global.

## Seguridad

La administración está limitada a:

```text
Matriz.group_soporte
```

La validación se ejecuta también en servidor mediante `_check_support_user()`.

Se aplica en:

- `create()`
- `write()`
- apertura de configuración;
- sincronización;
- consulta de permisos;
- procesamiento por lotes;
- consulta de progreso.

`unlink()` de la configuración está bloqueado intencionalmente.

## Usuarios disponibles

`user_ids` contiene el catálogo global de usuarios disponibles.

Restricciones:

- `active = True`;
- `share = False`;
- correo no vacío.

La inclusión en `user_ids` no crea permisos en Drive.

## Selección por proyecto

El acceso real se controla desde:

```python
contratacion.drive_reader_user_ids
```

El formulario del proyecto muestra el campo solo para Soporte y limita su dominio a los usuarios disponibles en la configuración global.

## Integración con listas existentes

`_project_specific_emails()` reúne correos provenientes de:

- `users_with_access`;
- `users_with_access_juridico`;
- `users_with_access_financiero`.

Estos accesos tienen prioridad al revocar.

Si un usuario continúa en alguna de estas listas, el grant global puede limpiarse localmente, pero el permiso de Drive no se elimina.

## Creación de permisos

`sync_project_readers()`:

1. obtiene `drive_reader_user_ids`;
2. descarta usuarios inválidos;
3. resuelve la carpeta principal del proyecto;
4. autentica Drive;
5. obtiene permisos actuales;
6. conserva permisos existentes de cualquier nivel;
7. crea únicamente permisos faltantes con:
   `role = reader`;
8. registra el permiso en `drive.global.access.grant`.

No degrada permisos superiores.

## Trazabilidad

`drive.global.access.grant` registra:

- configuración;
- proyecto;
- usuario;
- correo;
- ID de carpeta;
- ID del permiso de Google Drive.

Restricción única:

```text
(config_id, project_id, user_id)
```

Esto evita registros duplicados para el mismo usuario/proyecto.

## Revocación

`revoke_project_readers()` y `_remove_obsolete_grants()` revocan únicamente permisos trazados por esta funcionalidad.

La eliminación del permiso se omite cuando:

- el usuario mantiene acceso específico por otra lista del proyecto;
- no existe carpeta o permission_id;
- el permiso ya no existe;
- el permiso actual no es `reader`;
- ocurre un error de Drive.

Nunca se degrada ni elimina automáticamente un permiso superior.

## Revocación desde el grant

`action_revoke_from_project()`:

1. retira el usuario de `drive_reader_user_ids`;
2. invoca la revocación específica del proyecto;
3. devuelve una notificación de éxito si no existen errores.

## Sincronización manual

`action_sync_drive_readers()` exige `sync_project_id`.

Procesa solamente un proyecto y actualiza:

- `last_sync_at`;
- `last_sync_user_id`;
- `last_sync_summary`.

No inicia un recorrido global.

## Sincronización global por lotes

El código sí conserva un mecanismo de sincronización global por lotes.

Campos principales:

- `sync_pending`;
- `sync_last_project_id`;
- `sync_total_projects`;
- `sync_processed_projects`;
- `sync_added_permissions`;
- `sync_existing_permissions`;
- `sync_missing_folders`;
- `sync_removed_permissions`;
- `sync_skipped_removals`;
- `sync_error_count`;
- `sync_error_log`.

`_process_next_drive_reader_batch()` procesa por defecto cinco proyectos.

`cron_process_drive_reader_batches()` procesa configuraciones con `sync_pending=True`.

La exclusión concurrente se implementa con:

```sql
pg_try_advisory_xact_lock(847321609)
```

Esto evita que el cron y una acción web procesen simultáneamente el mismo lote.

## Importante sobre retiros masivos

Al finalizar una sincronización global por lotes, el código actual **no ejecuta una revocación masiva histórica**.

Los retiros se realizan cuando se elimina un lector de un proyecto concreto.

Esto evita borrar permisos históricos que todavía puedan estar bajo revisión.

## Cancelación

`action_cancel_drive_reader_sync()`:

- cambia `sync_pending` a `False`;
- registra usuario, fecha y resumen;
- no elimina permisos ya creados;
- no ejecuta nuevos lotes.

## Auditoría por usuario

`action_open_user_grants()` consulta únicamente registros locales de `drive.global.access.grant`.

No consulta Google Drive ni recorre proyectos.

Si existen grants, abre una vista agrupada por proyecto.

## Manejo de errores

Los mensajes de Drive se compactan mediante:

- `_compact_error_message()`;
- `_compact_error_log()`.

El objetivo es evitar llenar logs y campos con respuestas o URLs excesivamente largas.

Se conserva un máximo práctico de líneas recientes en la trazabilidad de sincronización.

## Carpeta del proyecto

`_project_folder_data()` intenta primero:

```python
project.drive_folder_id
```

y, si no existe, busca un registro `drive_folder` relacionado.

El ID de Google se obtiene desde:

- `folder_guardar_id`; o
- `google_drive_id`.

Si no existe carpeta, el proyecto se omite sin crear permisos.

## Invariantes de seguridad

El componente debe mantener estas reglas:

1. un usuario global no recibe acceso automáticamente;
2. la asignación es por proyecto;
3. solo se crea `reader`;
4. no se degradan permisos superiores;
5. no se eliminan permisos pertenecientes a otros mecanismos;
6. solo Soporte administra la configuración;
7. la auditoría usa trazabilidad local;
8. la revocación masiva automática no debe eliminar permisos históricos sin revisión.

## Verificación recomendada

Después de cambios se recomienda probar:

1. creación/apertura del singleton;
2. rechazo de usuarios sin correo;
3. restricción a Soporte;
4. selección de lector por proyecto;
5. creación de permiso `reader`;
6. conservación de permiso existente;
7. conservación de `writer`;
8. revocación del lector creado por esta funcionalidad;
9. conservación por listas específicas del proyecto;
10. consulta de grants por usuario;
11. sincronización manual de un proyecto;
12. proyecto sin carpeta;
13. error de autenticación Drive;
14. procesamiento por lotes;
15. bloqueo concurrente;
16. cancelación de sincronización;
17. trazabilidad en chatter y resumen.

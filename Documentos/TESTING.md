# Tests automatizados

El módulo tiene tests de Odoo (`odoo.tests.common.TransactionCase`) en
`tests/`. Corren dentro de una transacción que Odoo revierte automáticamente
al terminar, así que no dejan datos de prueba en la base.

Hoy solo existe `tests/test_decorador_firmas.py`, que cubre el decorador
`seguimiento_firmas` de `models/decorador_firmas.py` (trazabilidad de firmas
electrónicas usado por `certificacion_cumplimiento`, `informe_actividades`,
`declaracion_juramentada`, `inventario_documental` y `gestion_documental`):
verifica que un `write()` sobre un solo registro siga firmando correctamente,
y que un `write()` sobre **varios registros a la vez** no cruce el hash/log
de firma entre ellos (bug de escritura multi-registro, corregido).

## Requisitos

El stack de `docker-compose.yml` debe estar levantado:

```bash
docker-compose up -d
```

Contenedores relevantes: `gofuv` (Odoo) y `fuvdb` (Postgres, servicio
`fuvdb`, credenciales `odoo`/`odoo`).

## Correr todos los tests del módulo

```bash
docker exec gofuv sh -c "odoo -d odoo_local --db_host=fuvdb --db_user=odoo --db_password=odoo -u Matriz --test-enable --test-tags /Matriz --stop-after-init"
```

- `-d odoo_local` — la base de datos con el módulo instalado y datos reales.
  **No** es `fuvdb`: ese es el nombre del servicio/contenedor de Postgres;
  como base de datos, `fuvdb` existe pero está vacía.
- `--db_host=fuvdb --db_user=odoo --db_password=odoo` — credenciales de
  Postgres. Se pasan explícitas porque al invocar `odoo` directo (sin pasar
  por el entrypoint del contenedor) no se leen las variables de entorno
  `HOST`/`USER`/`PASSWORD` del `docker-compose.yml`.
- `-u Matriz` — actualiza el módulo antes de testear, para que Odoo cargue
  el código Python más reciente (incluyendo cualquier archivo nuevo bajo
  `tests/`).
- `--test-enable --test-tags /Matriz` — habilita el runner de tests y lo
  limita al módulo `Matriz` (sin esto correría los tests de **todos** los
  módulos instalados).
- `--stop-after-init` — termina el proceso al acabar en vez de quedarse
  sirviendo HTTP.

Resultado esperado, al final del output:

```
... odoo.tests.result: 0 failed, 0 error(s) of N tests when loading database 'odoo_local'
```

Un número distinto de 0 en `failed`/`error(s)`, o un `Traceback`, indica que
algo falló.

## Correr solo una clase o test puntual

```bash
docker exec gofuv sh -c "odoo -d odoo_local --db_host=fuvdb --db_user=odoo --db_password=odoo --test-enable --test-tags /Matriz:TestSeguimientoFirmasMultiRegistro --stop-after-init"
```

Si el módulo ya está actualizado (no cambiaste código desde el último
`-u Matriz`), se puede omitir `-u Matriz` para que corra más rápido.

Formato de `--test-tags`: `/<módulo>:<Clase>.<método>` — se puede recortar a
`/<módulo>:<Clase>` (toda la clase) o `/<módulo>` (todo el módulo).

## Ver el output completo en vivo

```bash
docker exec -it gofuv odoo -d odoo_local --db_host=fuvdb --db_user=odoo --db_password=odoo -u Matriz --test-enable --test-tags /Matriz --stop-after-init
```

## Filtrar rápido el resultado

```bash
docker exec gofuv sh -c "odoo -d odoo_local --db_host=fuvdb --db_user=odoo --db_password=odoo -u Matriz --test-enable --test-tags /Matriz --stop-after-init 2>&1" | grep -E "failed|error\(s\)|Traceback"
```

## Advertencia

`-u Matriz` hace una actualización real del módulo contra `odoo_local`
(recarga vistas, campos, etc.) — es la operación normal para "instalar mis
cambios y correr tests", pero no es un sandbox descartable: toca la base de
datos de desarrollo real del entorno.

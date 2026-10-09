# SGO

Plataforma SGO

## Backup y restauración de Odoo (migración entre entornos)

Automatización para mover la aplicación Odoo (base de datos + filestore)
entre servidores: respaldar el origen y restaurar en el destino (Fases 1-2 y
4 de `BACKUP_RESTORE_GUIDE.md`). Pensado para desarrolladores y sysadmins
que necesiten clonar producción a un entorno local/staging, migrar a un
servidor nuevo, o recuperar un ambiente desde un backup existente. Los
scripts viven en `scripts/`:

- `scripts/backup_odoo.sh` — genera el backup (`pg_dump` + filestore) desde
  los contenedores del servidor origen.
- `scripts/restore_odoo.sh` — restaura ese backup en un stack docker-compose
  destino (por defecto `docker-compose.prod.yml`, sin el contenedor de
  SIESA).
- `scripts/backup_odoo.env.example` — plantilla de configuración compartida
  por ambos scripts.

Ambos scripts usan `set -euo pipefail`, abortan con mensaje claro si falta
una variable requerida, y limpian los archivos temporales que crean dentro
de los contenedores (`trap cleanup EXIT`), incluso si fallan a mitad de
camino.

### 1. Configurar el `.env`

Copia la plantilla y ajusta los valores reales:

```bash
cp scripts/backup_odoo.env.example scripts/backup_odoo.env
```

`scripts/backup_odoo.env` **no se commitea** (está en `.gitignore`) porque
contiene nombres de contenedores/base de datos de un entorno real. Variables:

| Variable              | Usado por | Descripción                                                                                                                                                                                                       |
| --------------------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PROD_DB_CONTAINER`   | ambos     | Nombre del contenedor de Postgres. En restore también se usa como nombre de _servicio_ de docker-compose (en este repo `container_name` == nombre del servicio).                                                  |
| `PROD_ODOO_CONTAINER` | ambos     | Nombre del contenedor de Odoo. Mismo comentario que arriba.                                                                                                                                                       |
| `DB_NAME`             | ambos     | Base de datos origen a respaldar. En modo `tar`, también se usa para ubicar `/var/lib/odoo/filestore/$DB_NAME` dentro del contenedor.                                                                             |
| `DB_USER`             | ambos     | Usuario de Postgres (`pg_dump`/`pg_restore`/`dropdb`/`createdb`).                                                                                                                                                 |
| `BACKUP_ROOT`         | ambos     | Carpeta donde se guardan/leen los backups. Cada corrida de `backup_odoo.sh` crea una subcarpeta `odoo_backup_<timestamp>/`. `restore_odoo.sh` usa la más reciente si no le pasas una ruta explícita.              |
| `FILESTORE_MODE`      | backup    | `copy` (por defecto) o `tar`. Ver sección siguiente.                                                                                                                                                              |
| `RESTORE_DB_NAME`     | restore   | Nombre de la base a crear/restaurar en el destino. Por defecto `$DB_NAME`; cámbialo para restaurar bajo otro nombre sin pisar una base existente (ej. para probar el ciclo completo sin arriesgar datos en vivo). |
| `COMPOSE_FILE`        | restore   | Archivo docker-compose a levantar/apagar durante la restauración. Por defecto `docker-compose.prod.yml`. Se resuelve relativo a la raíz del repo si no es una ruta absoluta.                                      |

### 2. Modos de filestore: `copy` vs `tar`

- **`copy`** (Opción A de la guía): `docker cp` directo de
  `/var/lib/odoo/filestore` completo, sin comprimir. Más simple y rápido,
  pero pesa más en disco/red.
- **`tar`** (Opción B): comprime **solo** el contenido de
  `/var/lib/odoo/filestore/$DB_NAME` dentro del contenedor usando `pigz`
  (gzip multinúcleo) antes de copiarlo, generando un único
  `filestore_backup.tar.gz`. `pigz` no viene instalado por defecto en la
  imagen de Odoo — el script detecta si falta (`command -v pigz`) y lo
  instala automáticamente (`apt-get install -y pigz`, requiere root dentro
  del contenedor, `-u 0`).

`restore_odoo.sh` detecta automáticamente cuál de los dos modos se usó
mirando el contenido de la carpeta de backup (`filestore_backup.tar.gz` vs
`filestore/$DB_NAME/`), así que no hace falta indicarlo al restaurar.

⚠️ Importante sobre el modo `tar`: el `.tar.gz` debe contener el **contenido**
de la carpeta `$DB_NAME` (rutas relativas tipo `8b/8b0f7...`), no la carpeta
completa `/var/lib/odoo/filestore` ni rutas absolutas de otro origen. Si
traes un backup de filestore generado manualmente (no por este script) con
otra estructura interna, revisa primero con `tar -tzf archivo.tar.gz | head`
antes de restaurarlo — si no coincide, el `docker exec ... tar -x` fallará
por permisos o creará una carpeta anidada incorrecta.

### 3. Generar un backup

```bash
./scripts/backup_odoo.sh                     # usa scripts/backup_odoo.env
./scripts/backup_odoo.sh -c otro_archivo.env  # usa otra config
```

Requiere `docker` con acceso a los contenedores de producción (Postgres y
Odoo) desde el host donde corre el script. Verifica primero que ambos estén
`running`. Genera `$BACKUP_ROOT/odoo_backup_<timestamp>/` con:

- `db_backup.dump` (`pg_dump -F c`, formato custom, apto para `pg_restore -1`)
- `filestore/` o `filestore_backup.tar.gz`, según `FILESTORE_MODE`

Al terminar, imprime el tamaño de cada archivo generado. Para llevar el
backup a otra máquina, usa `scp`/`rsync` sobre esa carpeta (este script
**no** hace la transferencia — eso es la Fase 3 de la guía, manual).

### 4. Entornos docker-compose: `docker-compose.yml` vs `docker-compose.prod.yml`

Ambos archivos están **trackeados en git** (forman parte del repo) y
definen el mismo par de servicios base — `gofuv` (Odoo, build local desde
`Dockerfile`, puerto `8080:8069`) y `fuvdb` (Postgres 16) —, pero con un
propósito distinto:

- **`docker-compose.yml`** — entorno de **desarrollo local**. Además de
  `gofuv`/`fuvdb` incluye el servicio `siesadb` (SQL Server 2022), que
  **simula** el ERP externo SIESA/UnoEE solo para poder desarrollar sin
  depender de la red de producción; usa un `extra_hosts` que redirige
  `host.docker.internal` hacia ese contenedor. Es el que normalmente se usa
  al trabajar en el módulo (`docker-compose up`).
- **`docker-compose.prod.yml`** — stack **tipo producción**: mismos
  servicios `gofuv`/`fuvdb`, **sin** `siesadb` ni el `extra_hosts` asociado,
  porque un servidor real no simula SIESA — se conecta directo al SQL
  Server/UnoEE real vía `pymssql`/`pyodbc` (`conexion_siesa.py`,
  `pagos_siesa.py`). Sirve para dos casos:
  1. **Inicializar/levantar el proyecto** en un servidor o máquina que no es
     de desarrollo (staging, servidor de pruebas, etc.):
     ```bash
     docker compose -f docker-compose.prod.yml up -d
     docker compose -f docker-compose.prod.yml down --remove-orphans
     ```
  2. **Como target de `restore_odoo.sh`**, que lo levanta/apaga
     automáticamente durante cada fase de la restauración (no hace falta
     correrlo a mano en ese caso).

Ambos usan los mismos `container_name` (`gofuv`, `fuvdb`), así que **no
pueden correr los dos stacks al mismo tiempo** en la misma máquina
(comparten nombre de contenedor y puertos) — se usa uno u otro según si
estás desarrollando o corriendo/restaurando en un entorno tipo producción.

### 5. Restaurar un backup

```bash
./scripts/restore_odoo.sh                                    # backup más reciente en BACKUP_ROOT
./scripts/restore_odoo.sh /ruta/a/odoo_backup_20260812_105428
./scripts/restore_odoo.sh -c otro.env -f otro-compose.yml -y  # config/compose alternativos, sin confirmar
```

Flags: `-c archivo.env` (config alternativa), `-f archivo.yml` (compose
alternativo, tiene prioridad sobre `COMPOSE_FILE` del `.env`), `-y` (salta
la confirmación interactiva), argumento posicional opcional con la ruta del
backup.

**⚠️ Operación destructiva.** Antes de tocar nada, el script pide
confirmación (`[y/N]`) salvo que uses `-y`. Lo que hace:

1. **Fase 4.1** — apaga _todo_ el stack de `COMPOSE_FILE` y levanta solo el
   servicio de base de datos, para evitar conexiones abiertas que bloqueen
   el `dropdb`.
2. **Fase 4.2** — copia el dump al contenedor, cierra conexiones residuales
   (`pg_terminate_backend`), y **recrea la base desde cero**
   (`dropdb --if-exists` + `createdb`) bajo `RESTORE_DB_NAME`.
3. **Fase 4.3** — `pg_restore -1` (transaccional) del dump.
4. **Fase 4.4** — levanta el contenedor de Odoo, **borra y recrea**
   `/var/lib/odoo/filestore/$RESTORE_DB_NAME` (`rm -rf` + `mkdir -p`, como
   root), y repuebla su contenido desde el backup (extrae el `.tar.gz` con
   `pigz` —instalándolo si falta— o copia la carpeta directamente, según el
   modo detectado). Termina con `chown -R odoo:odoo` sobre esa carpeta y
   levanta el resto del stack.

Al terminar, verifica en `http://localhost:8080/web/database/manager` que
`RESTORE_DB_NAME` aparezca listada (Fase 5 de la guía).

#### ⚠️ Nunca restaures apuntando al mismo entorno que es tu única copia de los datos

El paso 4.4 **borra el filestore destino antes de repoblarlo**. Si apuntas
`restore_odoo.sh` al mismo contenedor del que además es tu única copia viva
de esos datos (por ejemplo, para "probar el ciclo completo" contra tu propio
entorno de desarrollo), y la restauración falla a mitad de camino, te quedas
sin filestore y sin backup bueno al mismo tiempo. Para probar el flujo sin
ese riesgo:

- Usa `RESTORE_DB_NAME` distinto a `DB_NAME` para restaurar bajo otro nombre
  de base sin tocar la real, o
- Verifica primero la estructura del backup (`tar -tzf ... | head`, tamaño
  esperado) antes de correr el restore de verdad, o
- Asegúrate de tener el backup ya copiado a salvo (otra carpeta, otra
  máquina) antes de ejecutar cualquier paso destructivo sobre el origen.

### 6. Problemas comunes post-restauración

Ver `BACKUP_RESTORE_GUIDE.md`, Fase 6, para los dos síntomas más frecuentes
después de restaurar:

- **reCAPTCHA bloquea el login en localhost** ("localhost is not a trusted
  domain") → desactivar la vista `ir_ui_view` id 1278 (`UPDATE ir_ui_view
SET active=false WHERE id=1278;`).
- **Página en blanco / `FileNotFoundError` en logs al pedir `.js`/`.css`** →
  la base conserva el caché de assets compilados apuntando a hashes viejos
  del filestore de origen. Fix: `DELETE FROM ir_attachment WHERE name LIKE
'%assets%';` y reiniciar el contenedor de Odoo — se recompilan solos en la
  primera petición.

### 7. Requisitos

- `docker` y el plugin `docker compose` (v2) en el host donde corren los
  scripts.
- Acceso al daemon docker de los contenedores de producción para
  `backup_odoo.sh` (normalmente se corre directamente en el servidor).
- `tar`/`pigz` en el host solo si necesitas inspeccionar o reprocesar un
  `.tar.gz` de filestore fuera de los contenedores (los scripts instalan
  `pigz` dentro del contenedor por su cuenta cuando hace falta).

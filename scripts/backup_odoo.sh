#!/usr/bin/env bash
#
# Automatiza la Fase 1-2 de BACKUP_RESTORE_GUIDE.md: genera un backup de la
# base de datos Postgres (pg_dump -Fc) y del filestore de Odoo, ambos
# extraídos de los contenedores Docker de producción hacia el host.
#
# Uso:
#   ./backup_odoo.sh                       # usa scripts/backup_odoo.env
#   ./backup_odoo.sh -c otro_archivo.env   # usa otro archivo de config
#
# Requiere: docker, tar. Debe correrse en el servidor de producción
# (o cualquier host con acceso al daemon docker donde corren los
# contenedores de Odoo/Postgres).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Config por defecto: scripts/backup_odoo.env (mismo archivo que usa restore_odoo.sh).
CONFIG_FILE="$SCRIPT_DIR/backup_odoo.env"

# Parseo de flags: -c para apuntar a otro archivo de config, -h para ayuda.
while getopts ":c:h" opt; do
  case "$opt" in
    c) CONFIG_FILE="$OPTARG" ;;
    h)
      echo "Uso: $0 [-c archivo.env]"
      exit 0
      ;;
    *)
      echo "Opción inválida: -$OPTARG" >&2
      exit 1
      ;;
  esac
done

# Helpers de logging: log() imprime con timestamp, die() loguea a stderr y aborta.
log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[[ -f "$CONFIG_FILE" ]] || die "No se encontró el archivo de configuración: $CONFIG_FILE (copia backup_odoo.env.example y ajústalo)"
# shellcheck disable=SC1090
source "$CONFIG_FILE"

# Variables obligatorias: si falta alguna en el .env, aborta con mensaje claro
# en vez de fallar más adelante con un error críptico de docker/psql.
: "${PROD_DB_CONTAINER:?Falta PROD_DB_CONTAINER en $CONFIG_FILE}"
: "${PROD_ODOO_CONTAINER:?Falta PROD_ODOO_CONTAINER en $CONFIG_FILE}"
: "${DB_NAME:?Falta DB_NAME en $CONFIG_FILE}"
: "${DB_USER:?Falta DB_USER en $CONFIG_FILE}"
: "${BACKUP_ROOT:?Falta BACKUP_ROOT en $CONFIG_FILE}"
# FILESTORE_MODE es opcional: "copy" (Opción A, docker cp directo, sin
# comprimir) es el valor por defecto si no se define en el .env.
FILESTORE_MODE="${FILESTORE_MODE:-copy}"

command -v docker >/dev/null || die "docker no está disponible en el PATH"

# Verifica que ambos contenedores existan y estén corriendo antes de intentar
# nada, para fallar rápido con un mensaje útil.
for container in "$PROD_DB_CONTAINER" "$PROD_ODOO_CONTAINER"; do
  docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null | grep -q true \
    || die "El contenedor '$container' no existe o no está corriendo"
done

# Cada corrida genera una carpeta nueva con timestamp, para no pisar backups anteriores.
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_DIR="$BACKUP_ROOT/odoo_backup_$TIMESTAMP"
mkdir -p "$OUT_DIR"
log "Backup destino: $OUT_DIR"

# Rutas temporales DENTRO de los contenedores (no en el host) donde se generan
# el dump y (si aplica) el tar del filestore antes de copiarlos afuera.
REMOTE_DUMP="/tmp/db_backup_$TIMESTAMP.dump"
REMOTE_TAR="/tmp/filestore_backup_$TIMESTAMP.tar.gz"

# Se ejecuta siempre al salir (éxito, error o Ctrl+C) para no dejar basura
# dentro de los contenedores, incluso si el script muere a mitad de camino.
cleanup() {
  docker exec "$PROD_DB_CONTAINER" rm -f "$REMOTE_DUMP" >/dev/null 2>&1 || true
  if [[ "$FILESTORE_MODE" == "tar" ]]; then
    docker exec "$PROD_ODOO_CONTAINER" rm -f "$REMOTE_TAR" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# --- 1. Base de datos ---
# pg_dump en formato "custom" (-F c): comprimido y apto para pg_restore -1
# (transaccional), a diferencia de un dump de texto plano.
log "Generando pg_dump de '$DB_NAME'..."
docker exec -t "$PROD_DB_CONTAINER" pg_dump -U "$DB_USER" -F c -f "$REMOTE_DUMP" "$DB_NAME" \
  || die "pg_dump falló"

log "Copiando dump al host..."
docker cp "$PROD_DB_CONTAINER:$REMOTE_DUMP" "$OUT_DIR/db_backup.dump" \
  || die "docker cp del dump falló"

# --- 2. Filestore ---
# Odoo guarda los adjuntos (ir.attachment) como archivos en disco, con
# subcarpetas por database dentro de /var/lib/odoo/filestore. Hay dos
# estrategias para respaldarlos:
if [[ "$FILESTORE_MODE" == "copy" ]]; then
  # Opción A: docker cp directo de la carpeta del filestore, sin comprimir.
  # Es la más rápida y simple, pero copia bytes sin comprimir (más espacio/red).
  log "Copiando filestore directamente (Opción A)..."
  docker cp "$PROD_ODOO_CONTAINER:/var/lib/odoo/filestore" "$OUT_DIR/filestore" \
    || die "docker cp del filestore falló"
elif [[ "$FILESTORE_MODE" == "tar" ]]; then
  # Opción B: comprimir dentro del contenedor con pigz (gzip multinúcleo) antes
  # de copiar, para reducir tamaño y tiempo de transferencia. pigz no viene
  # instalado por defecto en la imagen de Odoo, así que se valida e instala
  # aquí si hace falta (requiere apt y correr como root, -u 0).
  if ! docker exec "$PROD_ODOO_CONTAINER" sh -c 'command -v pigz' >/dev/null 2>&1; then
    log "pigz no está instalado en '$PROD_ODOO_CONTAINER', instalando (requiere apt y root en el contenedor)..."
    docker exec -u 0 -t "$PROD_ODOO_CONTAINER" apt-get update \
      || die "apt-get update falló dentro del contenedor"
    docker exec -u 0 -t "$PROD_ODOO_CONTAINER" apt-get install -y pigz \
      || die "instalación de pigz falló dentro del contenedor"
  fi

  log "Comprimiendo filestore dentro del contenedor con pigz (Opción B, multinúcleo)..."
  docker exec "$PROD_ODOO_CONTAINER" test -d "/var/lib/odoo/filestore/$DB_NAME" \
    || die "No existe /var/lib/odoo/filestore/$DB_NAME dentro de '$PROD_ODOO_CONTAINER'"
  # -C .../filestore/$DB_NAME . : el tar contiene el CONTENIDO de la carpeta
  # de esa base (no la carpeta en sí, ni el resto de databases), para que al
  # extraerlo en restore_odoo.sh quede directo dentro del filestore destino
  # sin anidar una carpeta $DB_NAME extra.
  docker exec -t "$PROD_ODOO_CONTAINER" tar -I pigz -cf "$REMOTE_TAR" -C "/var/lib/odoo/filestore/$DB_NAME" . \
    || die "tar del filestore falló"
  log "Copiando filestore comprimido al host..."
  docker cp "$PROD_ODOO_CONTAINER:$REMOTE_TAR" "$OUT_DIR/filestore_backup.tar.gz" \
    || die "docker cp del filestore comprimido falló"
else
  die "FILESTORE_MODE debe ser 'copy' o 'tar', no '$FILESTORE_MODE'"
fi

log "Backup completado:"
du -sh "$OUT_DIR"/* 2>/dev/null | sed 's/^/  /'
log "Listo. Para llevarlo a tu equipo local usa scp/rsync sobre: $OUT_DIR"

#!/usr/bin/env bash
#
# Automatiza la Fase 4 de BACKUP_RESTORE_GUIDE.md: restaura en un entorno
# docker-compose (por defecto docker-compose.prod.yml, sin el contenedor
# de SIESA) un backup generado por backup_odoo.sh.
#
# ADVERTENCIA: operación destructiva. Apaga el stack, BORRA la base de
# datos destino (dropdb) y el filestore existente de esa base, y los
# reemplaza con el contenido del backup.
#
# Uso:
#   ./restore_odoo.sh                                   # restaura el backup más reciente en BACKUP_ROOT
#   ./restore_odoo.sh /ruta/a/odoo_backup_20260811_104546
#   ./restore_odoo.sh -c otro.env -f otro-compose.yml -y /ruta/al/backup
#
# Requiere: docker, docker compose (plugin v2). Usa el mismo archivo de
# config que backup_odoo.sh (por defecto scripts/backup_odoo.env).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# Mismo archivo de config que backup_odoo.sh, para no duplicar nombres de
# contenedor/base de datos en dos lugares distintos.
CONFIG_FILE="$SCRIPT_DIR/backup_odoo.env"
COMPOSE_FILE_OVERRIDE=""
ASSUME_YES=false

usage() { echo "Uso: $0 [-c archivo.env] [-f docker-compose.yml] [-y] [ruta_backup]"; }

# Parseo de flags: -c config alternativa, -f compose alternativo, -y salta la
# confirmación interactiva (operación destructiva), -h ayuda.
while getopts ":c:f:yh" opt; do
  case "$opt" in
    c) CONFIG_FILE="$OPTARG" ;;
    f) COMPOSE_FILE_OVERRIDE="$OPTARG" ;;
    y) ASSUME_YES=true ;;
    h) usage; exit 0 ;;
    *) echo "Opción inválida: -$OPTARG" >&2; usage; exit 1 ;;
  esac
done
shift $((OPTIND - 1))
# Argumento posicional opcional: ruta a una carpeta odoo_backup_* concreta.
# Si no se pasa, más abajo se usa la más reciente dentro de BACKUP_ROOT.
BACKUP_DIR_ARG="${1:-}"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[[ -f "$CONFIG_FILE" ]] || die "No se encontró el archivo de configuración: $CONFIG_FILE (copia backup_odoo.env.example y ajústalo)"
# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${PROD_DB_CONTAINER:?Falta PROD_DB_CONTAINER en $CONFIG_FILE}"
: "${PROD_ODOO_CONTAINER:?Falta PROD_ODOO_CONTAINER en $CONFIG_FILE}"
: "${DB_NAME:?Falta DB_NAME en $CONFIG_FILE}"
: "${DB_USER:?Falta DB_USER en $CONFIG_FILE}"
: "${BACKUP_ROOT:?Falta BACKUP_ROOT en $CONFIG_FILE}"
# RESTORE_DB_NAME permite restaurar el backup bajo un nombre de base distinto
# al original (ej. para probar sin pisar la base "real"); por defecto es la misma.
RESTORE_DB_NAME="${RESTORE_DB_NAME:-$DB_NAME}"
# Prioridad: flag -f > COMPOSE_FILE del .env > default docker-compose.prod.yml.
COMPOSE_FILE="${COMPOSE_FILE_OVERRIDE:-${COMPOSE_FILE:-docker-compose.prod.yml}}"
[[ "$COMPOSE_FILE" = /* ]] || COMPOSE_FILE="$REPO_ROOT/$COMPOSE_FILE"

# En docker-compose.yml / docker-compose.prod.yml el nombre de servicio y
# el container_name coinciden, así que reutilizamos las mismas variables
# del backup como nombre de servicio para los comandos de compose.
DB_SERVICE="$PROD_DB_CONTAINER"
ODOO_SERVICE="$PROD_ODOO_CONTAINER"

command -v docker >/dev/null || die "docker no está disponible en el PATH"
[[ -f "$COMPOSE_FILE" ]] || die "No se encontró el archivo compose: $COMPOSE_FILE"

# --- Resolver directorio de backup a restaurar ---
if [[ -n "$BACKUP_DIR_ARG" ]]; then
  BACKUP_DIR="$BACKUP_DIR_ARG"
else
  BACKUP_DIR="$(find "$BACKUP_ROOT" -maxdepth 1 -type d -name 'odoo_backup_*' 2>/dev/null | sort | tail -1)"
  [[ -n "$BACKUP_DIR" ]] || die "No se encontró ningún backup en $BACKUP_ROOT. Indica la ruta explícitamente."
fi
[[ -d "$BACKUP_DIR" ]] || die "El directorio de backup no existe: $BACKUP_DIR"

DUMP_FILE="$BACKUP_DIR/db_backup.dump"
[[ -f "$DUMP_FILE" ]] || die "No se encontró $DUMP_FILE"

# Autodetección del modo en que se generó el backup del filestore, para no
# tener que pasarlo por parámetro: backup_odoo.sh en modo "tar" deja un
# filestore_backup.tar.gz; en modo "copy" deja una carpeta filestore/$DB_NAME.
FILESTORE_TAR="$BACKUP_DIR/filestore_backup.tar.gz"
FILESTORE_DIR="$BACKUP_DIR/filestore/$DB_NAME"
if [[ -f "$FILESTORE_TAR" ]]; then
  FILESTORE_SOURCE_MODE="tar"
elif [[ -d "$FILESTORE_DIR" ]]; then
  FILESTORE_SOURCE_MODE="copy"
elif [[ -d "$BACKUP_DIR/filestore" ]]; then
  # El backup se generó con otro DB_NAME: usar el único subdirectorio presente.
  FILESTORE_DIR="$(find "$BACKUP_DIR/filestore" -mindepth 1 -maxdepth 1 -type d | head -1)"
  [[ -n "$FILESTORE_DIR" ]] || die "No se encontró contenido dentro de $BACKUP_DIR/filestore"
  FILESTORE_SOURCE_MODE="copy"
else
  die "No se encontró backup de filestore en $BACKUP_DIR (ni filestore_backup.tar.gz ni carpeta filestore/)"
fi

log "Backup a restaurar: $BACKUP_DIR (filestore modo: $FILESTORE_SOURCE_MODE)"
log "Destino: contenedor DB '$PROD_DB_CONTAINER', contenedor Odoo '$PROD_ODOO_CONTAINER', BD '$RESTORE_DB_NAME'"
log "Compose file: $COMPOSE_FILE"

# Confirmación interactiva antes de una operación destructiva (dropdb + rm -rf
# de filestore). Saltable con -y para uso no interactivo/automatizado, pero
# por defecto siempre pregunta.
if [[ "$ASSUME_YES" != true ]]; then
  echo
  echo "Esto va a APAGAR el stack de '$COMPOSE_FILE', BORRAR la base de datos '$RESTORE_DB_NAME'"
  echo "(si existe) en '$PROD_DB_CONTAINER', y BORRAR/reemplazar el filestore de esa base en"
  echo "'$PROD_ODOO_CONTAINER'. Es una operación destructiva sobre lo que exista ahí."
  read -r -p "¿Continuar? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "Cancelado por el usuario"
fi

# --- 4.1 Aislar el entorno para evitar bloqueos de conexión ---
# Se apaga todo el stack y se vuelve a levantar solo Postgres, para que no
# haya procesos de Odoo con conexiones abiertas a la base que impidan
# dropearla/recrearla en el siguiente paso.
log "Apagando stack completo ($COMPOSE_FILE)..."
docker compose -f "$COMPOSE_FILE" down --remove-orphans

log "Levantando solo el servicio de base de datos ($DB_SERVICE)..."
docker compose -f "$COMPOSE_FILE" up -d "$DB_SERVICE"

log "Esperando a que Postgres acepte conexiones..."
for i in $(seq 1 30); do
  docker exec "$PROD_DB_CONTAINER" pg_isready -U "$DB_USER" >/dev/null 2>&1 && break
  [[ "$i" -eq 30 ]] && die "Postgres no respondió a tiempo en '$PROD_DB_CONTAINER'"
  sleep 1
done

# Ruta temporal DENTRO del contenedor de Postgres donde se copia el dump
# antes de restaurarlo; se limpia siempre al salir (ver trap más abajo).
REMOTE_DUMP="/tmp/restore_$(date '+%Y%m%d_%H%M%S').dump"
cleanup() {
  docker exec "$PROD_DB_CONTAINER" rm -f "$REMOTE_DUMP" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- 4.2 Limpiar y crear la base de datos ---
log "Copiando dump al contenedor..."
docker cp "$DUMP_FILE" "$PROD_DB_CONTAINER:$REMOTE_DUMP"

# Por si quedó alguna conexión abierta a la base (ej. una sesión psql manual);
# sin esto dropdb podría fallar con "database is being accessed by other users".
log "Cerrando conexiones residuales a '$RESTORE_DB_NAME' (si existen)..."
docker exec "$PROD_DB_CONTAINER" psql -U "$DB_USER" -d postgres -c \
  "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = '$RESTORE_DB_NAME' AND pid <> pg_backend_pid();" \
  >/dev/null 2>&1 || true

# dropdb + createdb en vez de restaurar sobre la base existente: garantiza que
# no queden tablas/datos residuales de una versión anterior de la base.
log "Recreando base de datos '$RESTORE_DB_NAME'..."
docker exec "$PROD_DB_CONTAINER" dropdb -U "$DB_USER" "$RESTORE_DB_NAME" --if-exists \
  || die "dropdb falló"
docker exec "$PROD_DB_CONTAINER" createdb -U "$DB_USER" "$RESTORE_DB_NAME" \
  || die "createdb falló"

# --- 4.3 Restaurar la base de datos ---
# -1 corre todo dentro de una sola transacción: si algo falla a mitad de
# camino, se revierte por completo en vez de dejar la base a medio restaurar.
log "Restaurando dump con pg_restore (puede tardar)..."
docker exec "$PROD_DB_CONTAINER" pg_restore -U "$DB_USER" -d "$RESTORE_DB_NAME" -1 "$REMOTE_DUMP" \
  || die "pg_restore falló"

# --- 4.4 Restaurar el filestore ---
log "Levantando el contenedor de Odoo ($ODOO_SERVICE)..."
docker compose -f "$COMPOSE_FILE" up -d "$ODOO_SERVICE"

log "Esperando a que el contenedor de Odoo esté corriendo..."
for i in $(seq 1 30); do
  docker inspect -f '{{.State.Running}}' "$PROD_ODOO_CONTAINER" 2>/dev/null | grep -q true && break
  [[ "$i" -eq 30 ]] && die "El contenedor '$PROD_ODOO_CONTAINER' no llegó a estar corriendo"
  sleep 1
done
sleep 3 # margen para que el entrypoint termine de inicializar

# ADVERTENCIA: rm -rf destructivo. Borra TODO el contenido actual del
# filestore de RESTORE_DB_NAME en el contenedor destino antes de repoblarlo
# con el del backup, para no mezclar archivos viejos con los restaurados.
# Se crea como root (-u 0) porque el chown a odoo:odoo pasa recién al final.
DEST_FILESTORE="/var/lib/odoo/filestore/$RESTORE_DB_NAME"
log "Reiniciando carpeta de filestore destino ($DEST_FILESTORE)..."
docker exec -u 0 "$PROD_ODOO_CONTAINER" rm -rf "$DEST_FILESTORE"
docker exec -u 0 "$PROD_ODOO_CONTAINER" mkdir -p "$DEST_FILESTORE"

if [[ "$FILESTORE_SOURCE_MODE" == "tar" ]]; then
  # Igual que en backup_odoo.sh: pigz no viene instalado por defecto, se
  # valida e instala si hace falta (requiere apt y root, -u 0).
  if ! docker exec "$PROD_ODOO_CONTAINER" sh -c 'command -v pigz' >/dev/null 2>&1; then
    log "pigz no está instalado en '$PROD_ODOO_CONTAINER', instalando..."
    docker exec -u 0 -t "$PROD_ODOO_CONTAINER" apt-get update \
      || die "apt-get update falló dentro del contenedor"
    docker exec -u 0 -t "$PROD_ODOO_CONTAINER" apt-get install -y pigz \
      || die "instalación de pigz falló dentro del contenedor"
  fi

  # Se redefine el trap de cleanup para que también borre el tar temporal del
  # filestore además del dump (el trap anterior solo conocía el dump).
  REMOTE_TAR="/tmp/restore_filestore_$(date '+%Y%m%d_%H%M%S').tar.gz"
  cleanup() {
    docker exec "$PROD_DB_CONTAINER" rm -f "$REMOTE_DUMP" >/dev/null 2>&1 || true
    docker exec "$PROD_ODOO_CONTAINER" rm -f "$REMOTE_TAR" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT

  log "Copiando filestore comprimido al contenedor..."
  docker cp "$FILESTORE_TAR" "$PROD_ODOO_CONTAINER:$REMOTE_TAR"

  # -u 0: DEST_FILESTORE fue creado como root arriba, así que la extracción
  # también debe correr como root para poder escribir dentro. El tar fue
  # generado con el contenido de filestore/$DB_NAME directamente (sin carpeta
  # contenedora), así que se extrae tal cual dentro de DEST_FILESTORE.
  log "Descomprimiendo filestore con pigz..."
  docker exec -u 0 -t "$PROD_ODOO_CONTAINER" tar -I pigz -xf "$REMOTE_TAR" -C "$DEST_FILESTORE" \
    || die "extracción del filestore falló"
else
  # docker cp corre con privilegios del daemon, así que no tiene el mismo
  # problema de permisos que docker exec sin -u 0.
  log "Copiando filestore directamente..."
  docker cp "$FILESTORE_DIR/." "$PROD_ODOO_CONTAINER:$DEST_FILESTORE" \
    || die "docker cp del filestore falló"
fi

# La extracción/copy anterior deja archivos con dueño root (o el UID del
# host); Odoo corre como usuario "odoo" dentro del contenedor y necesita ser
# dueño de estos archivos para poder leerlos/escribirlos normalmente.
log "Ajustando permisos del filestore..."
docker exec -u 0 "$PROD_ODOO_CONTAINER" chown -R odoo:odoo "$DEST_FILESTORE"

log "Levantando el resto del stack..."
docker compose -f "$COMPOSE_FILE" up -d

log "Restauración completada."
log "Verifica en http://localhost:8080/web/database/manager que '$RESTORE_DB_NAME' aparezca listada (Fase 5 de la guía)."
log "Si ves problemas de reCAPTCHA o assets, revisa la Fase 6 de BACKUP_RESTORE_GUIDE.md."

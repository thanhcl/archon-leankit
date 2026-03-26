#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATION_DIR="${MIGRATION_DIR:-${ROOT_DIR}/migration/0.1.0-leankit}"
DB_CONTAINER="${DB_CONTAINER:-supabase-db}"
DB_USER="${DB_USER:-supabase_admin}"
DB_NAME="${DB_NAME:-postgres}"
DRY_RUN="${DRY_RUN:-0}"

if [[ ! -d "${MIGRATION_DIR}" ]]; then
  echo "LeanKit migration directory not found: ${MIGRATION_DIR}" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${DB_CONTAINER}"; then
  echo "Database container not running: ${DB_CONTAINER}" >&2
  exit 1
fi

apply_sql() {
  local file="$1"
  local name
  name="$(basename "${file}" .sql)"
  local version
  version="$(basename "${MIGRATION_DIR}")"

  echo "Applying ${version}/${name}.sql"

  if [[ "${DRY_RUN}" == "1" ]]; then
    return 0
  fi

  cat "${file}" | docker exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" >/dev/null

  docker exec -i "${DB_CONTAINER}" psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${DB_NAME}" \
    -c "INSERT INTO archon_migrations (version, migration_name)
        SELECT '${version}', '${name}'
        WHERE NOT EXISTS (
          SELECT 1 FROM archon_migrations
          WHERE version='${version}' AND migration_name='${name}'
        );" >/dev/null
}

for file in "${MIGRATION_DIR}"/*.sql; do
  apply_sql "${file}"
done

echo "LeanKit local migrations applied from ${MIGRATION_DIR}"

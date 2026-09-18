#!/bin/sh
# Cloud Run has no pre-start hook, so migrations run here, then the server starts
# (PROJECT.md §17 "Schema change needed"). Skipped when no database is configured,
# which is the state until Supabase (E11.5) exists.
set -eu
if [ -n "${AEGIS_DATABASE_URL:-}" ]; then
  echo '{"event":"migrate.start"}'
  (cd /app/apps/api && alembic upgrade head)
  echo '{"event":"migrate.done"}'
else
  echo '{"event":"migrate.skip","reason":"AEGIS_DATABASE_URL not set"}'
fi
exec uvicorn aegisops_api.main:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips='*'

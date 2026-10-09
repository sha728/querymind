#!/usr/bin/env bash
# Creates the read-only role QueryMind uses for every query (design §5.2, R4.1, layer L1).
# Runs once, as the database owner, from the postgres image's /docker-entrypoint-initdb.d.
set -euo pipefail

: "${TARGET_DB_RO_USER:?TARGET_DB_RO_USER must be set}"
: "${TARGET_DB_RO_PASSWORD:?TARGET_DB_RO_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v ro_user="$TARGET_DB_RO_USER" -v ro_password="$TARGET_DB_RO_PASSWORD" -v db="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"ro_user" LOGIN PASSWORD :'ro_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION CONNECTION LIMIT 20;

-- Every transaction is read-only by default, and statements time out after 10 s (R4.4).
ALTER ROLE :"ro_user" SET default_transaction_read_only = on;
ALTER ROLE :"ro_user" SET statement_timeout = '10s';

-- Database: CONNECT only. Revoking PUBLIC's defaults also removes TEMP (no temp tables).
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO :"ro_user";

-- Schema: no CREATE for anyone but the owner; USAGE + SELECT for the read-only role.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"ro_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"ro_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"ro_user";
SQL

echo "02_readonly_role: created read-only role ${TARGET_DB_RO_USER}"

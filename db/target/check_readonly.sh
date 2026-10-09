#!/usr/bin/env bash
# Verifies the target database's read-only guarantees (design §5.2, §7.1 L1, R4.1, R4.4).
# Run from the repo root while `docker compose up target-db` is running:
#     bash db/target/check_readonly.sh
# Connects as the read-only role over TCP inside the container. Exits non-zero on any failure.
set -uo pipefail

ENV_FILE="${ENV_FILE:-.env}"
get() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]*$//'; }
RO_USER="$(get TARGET_DB_RO_USER)"; RO_USER="${RO_USER:-querymind_ro}"
RO_PASSWORD="$(get TARGET_DB_RO_PASSWORD)"
DB="$(get TARGET_DB_NAME)"; DB="${DB:-northwind}"

failures=0
ro_sql() {
  docker compose exec -T -e PGPASSWORD="$RO_PASSWORD" target-db \
    psql -h 127.0.0.1 -U "$RO_USER" -d "$DB" -v ON_ERROR_STOP=1 -tAc "$1" 2>&1
}
# Each statement in its own autocommit transaction (stdin), so a SET applies to the next one.
ro_script() {
  printf '%s\n' "$@" | docker compose exec -T -e PGPASSWORD="$RO_PASSWORD" target-db \
    psql -h 127.0.0.1 -U "$RO_USER" -d "$DB" -v ON_ERROR_STOP=1 -tA 2>&1
}
# Layer L1 alone: switch read-only off for the session, then try to write in a NEW transaction.
# The role's missing privileges must still refuse it with "permission denied".
expect_denied() {
  local name="$1" stmt="$2" out
  if out="$(ro_script "SET default_transaction_read_only = off;" "$stmt")"; then
    echo "FAIL  $name (expected permission denied) -> $out"; failures=$((failures + 1))
  elif echo "$out" | grep -qE "permission denied|must be owner"; then
    echo "PASS  $name -> refused: $(echo "$out" | grep -m1 ERROR)"
  else
    echo "FAIL  $name (refused, but not by privileges): $(echo "$out" | grep -m1 ERROR)"; failures=$((failures + 1))
  fi
}
expect_ok() {
  local name="$1" sql="$2" out
  if out="$(ro_sql "$sql")"; then echo "PASS  $name -> ${out//$'\n'/ }"
  else echo "FAIL  $name (expected success): $out"; failures=$((failures + 1)); fi
}
expect_fail() {
  local name="$1" sql="$2" out
  if out="$(ro_sql "$sql")"; then echo "FAIL  $name (expected an error) -> $out"; failures=$((failures + 1))
  else echo "PASS  $name -> refused: $(echo "$out" | grep -m1 ERROR)"; fi
}

expect_ok   "SELECT works"                    "SELECT count(*) FROM orders"
expect_ok   "role is not superuser"           "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
expect_ok   "statement_timeout is 10s"        "SHOW statement_timeout"
expect_ok   "transactions read-only"          "SHOW default_transaction_read_only"
expect_fail "INSERT refused"                  "INSERT INTO region VALUES (99, 'Nowhere')"
expect_fail "UPDATE refused"                  "UPDATE products SET unit_price = 0"
expect_fail "DELETE refused"                  "DELETE FROM order_details"
expect_fail "CREATE TABLE refused"            "CREATE TABLE x (a int)"
expect_fail "CREATE TEMP TABLE refused"       "CREATE TEMP TABLE x (a int)"
expect_denied "read-only off: INSERT denied by privileges"       "INSERT INTO region VALUES (99, 'Nowhere');"
expect_denied "read-only off: UPDATE denied by privileges"       "UPDATE products SET unit_price = 0;"
expect_denied "read-only off: CREATE TABLE denied by privileges" "CREATE TABLE x (a int);"
expect_denied "read-only off: CREATE TEMP denied by privileges"  "CREATE TEMP TABLE x (a int);"
expect_denied "read-only off: DROP denied (not owner)"           "DROP TABLE orders;"
expect_fail "pg_read_file refused"            "SELECT pg_read_file('/etc/passwd')"

# Exact values the done-when requires.
[ "$(ro_sql 'SHOW statement_timeout')" = "10s" ] || { echo "FAIL  statement_timeout is not 10s"; failures=$((failures + 1)); }
[ "$(ro_sql "SELECT rolsuper FROM pg_roles WHERE rolname = current_user")" = "f" ] || { echo "FAIL  role is a superuser"; failures=$((failures + 1)); }

echo
if [ "$failures" -eq 0 ]; then echo "check_readonly: all checks passed"; else echo "check_readonly: $failures check(s) FAILED"; fi
exit "$failures"

#!/usr/bin/env bash
# Writes a throwaway .env for CI (T46): .env.example with every secret filled by a fresh random
# value. Nothing here is a real credential, and no LLM key is set (CI never calls a real LLM).
set -euo pipefail

cd "$(dirname "$0")/.."
# Never clobber a developer's real .env: only in CI (GitHub sets GITHUB_ACTIONS) or with --force.
if [[ -e .env && "${GITHUB_ACTIONS:-}" != "true" && "${1:-}" != "--force" ]]; then
  echo "ci_env.sh: .env exists; refusing to overwrite it outside CI (use --force)" >&2
  exit 1
fi
cp .env.example .env

rand() { openssl rand -hex "$1"; }

set_key() {
  # Replace the whole KEY=... line (including any inline comment).
  sed -i "s|^$1=.*|$1=$2|" .env
}

set_key APP_DB_USER querymind
set_key APP_DB_PASSWORD "$(rand 16)"
set_key TARGET_DB_OWNER_PASSWORD "$(rand 16)"
set_key TARGET_DB_RO_PASSWORD "$(rand 16)"
set_key JWT_SIGNING_KEY "$(rand 32)"
set_key ADMIN_EMAIL admin@example.com
set_key ADMIN_PASSWORD "$(rand 12)"
set_key ENGINE_INTERNAL_KEY "$(rand 32)"

# Fail fast if a required key is still empty.
for key in APP_DB_USER APP_DB_PASSWORD TARGET_DB_OWNER_PASSWORD TARGET_DB_RO_PASSWORD \
           JWT_SIGNING_KEY ADMIN_EMAIL ADMIN_PASSWORD ENGINE_INTERNAL_KEY; do
  grep -Eq "^$key=[^ #]+" .env || { echo "ci_env.sh: $key is empty" >&2; exit 1; }
done
echo "ci_env.sh: wrote .env with generated secrets"

# Vendored: Northwind for PostgreSQL

- **Source:** https://github.com/pthom/northwind_psql, file `northwind.sql`
- **Commit:** `cd0ef28d66369fbe177778e604e4be0f153c9e5c` (branch `master`, retrieved 2026-10-09)
- **Copied as:** `init/01_northwind.sql`, unchanged (SHA-256 `0ee30c01ba282f7194f38bf7f99cd6be0470b7ee5f67d0f7ca41fb058d735e0c`)
- **Licence:** Microsoft Public License (Ms-PL). The Northwind database originates from Microsoft. The full
  licence text from the source repository is in [`LICENSE`](LICENSE), unchanged, as Ms-PL section 3(D) requires.
- **Contents checked before vendoring:** 14 `CREATE TABLE`, 3,362 `INSERT`, 27 `ALTER TABLE` (primary
  and foreign keys), and session `SET`s only. No roles, extensions, `COPY`, ownership changes or other
  statements.

## QueryMind's own scripts (not part of the vendored file)

- `init/02_readonly_role.sh` creates the read-only role `querymind_ro` (design §5.2).
- `init/03_date_shift.sql` moves all dates forward by whole months so the latest order falls in the
  previous calendar month (design §5.2, A3). It changes data values at seed time only; the vendored
  file itself is not modified.
- `check_readonly.sh` verifies the read-only guarantees against a running container.

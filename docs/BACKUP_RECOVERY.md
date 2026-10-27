# Backup and recovery

Version 0.4. This tool manages the whole file-based SQLite instance. It is for the
server operator with filesystem access, not a business tenant. Archives contain
all merchants, customer records, password hashes and ledger data. `.env` and API
keys are not included. Keep archives private; they are not encrypted. A checksum
detects accidental changes, not forgery by someone able to rewrite an archive.

## Create and inspect

Run in the project folder with the configured `.env`:

```powershell
.\.venv\Scripts\python.exe -m app.backup create --output sika-2026-10-27.sikabackup
.\.venv\Scripts\python.exe -m app.backup inspect sika-2026-10-27.sikabackup
```

Choose a fresh filename each time. Existing files are not overwritten. SQLite's
backup API produces a consistent snapshot even while the app is running; copying
the main `.db` alone during a live WAL session is not the equivalent operation.
The archive contains a manifest and one standalone database snapshot. The tool
checks its SHA256, database size, schema columns, integrity and foreign keys.
Only matching v0.4 archives are accepted, up to 200 MiB uncompressed. This limit
is for the demonstration tool, not a general SQLite storage limit.

On macOS/Linux replace the Python path with `.venv/bin/python`.

## Restore deliberately

1. Inspect the intended archive. Retain an independent private backup.
2. Stop every web application, embedded worker and standalone worker using this
   database. Also close external SQLite editors and tools. Shared connection
   leases block restoration while a cooperating v0.4 Sika process is open.
3. Confirm the destination from `DATABASE_URL` in your existing `.env`. The
   default destination filename is `sika.db`; a custom database may differ.
4. Run:

```powershell
.\.venv\Scripts\python.exe -m app.backup restore sika-2026-10-27.sikabackup --confirm sika.db
```

5. The tool validates the archive before replacement and creates a fresh
   `*.before-restore-*.sikabackup` from an existing destination. It then installs
   the validated database using an atomic file replacement. A failed validation
   or wrong confirmation leaves the destination untouched. Do not interrupt
   maintenance or manually delete the `.sika-lock` sidecar to bypass a lock.
6. Restart and sign in again. Verify an existing customer balance, savings
   balance and the new `backup.restored` activity event.

Restoration revokes sessions, cancels pending assistant proposals, removes chat
history, invalidates queued reminder previews and marks interrupted SMS outcomes
unknown. Pending/in-flight provider verification jobs stop for review. Inspect
provider history before retrying them. Payments after the snapshot time are not
magically recovered; use provider records and Payment recovery to reconcile
known references. Restoring a financial snapshot can require manual reconciliation.
The saved safety archive preserves the pre-restore state if it is needed.

The maintenance sidecar uses POSIX file locks or Windows LockFileEx. It coordinates
Sika processes sharing the same local database path. It does not police arbitrary
external SQLite clients, old source versions or distributed/network filesystems.
Keep every such process stopped during restoration. Windows execution still
needs validation on the delivery machine; Linux lock/restore behavior is tested.

## Optional scheduled backups

The create command can run from Windows Task Scheduler under the server operator's
account. Use the full `.venv\Scripts\python.exe` as Program, the full project
folder as Start in, and `-m app.backup create --output <new-private-filename>` as
arguments. Choose a unique timestamped filename per run through an operator
wrapper script. On Linux, the same command can be called by the host scheduler.
No scheduled task is installed by this bundle. Define retention and a private
copy outside the server according to the demonstration's operating needs.

## PostgreSQL

This tool rejects PostgreSQL URLs and in-memory SQLite databases. Use your
PostgreSQL host's native backup, retention and restore facilities with the
matching PostgreSQL tooling. Rehearse restoration into an isolated database
before relying on it. PostgreSQL backup/recovery is not implemented or verified
by this SQLite tool. The application must remain stopped while applying a
recovery procedure that changes its database state.

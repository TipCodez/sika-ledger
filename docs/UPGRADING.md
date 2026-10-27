# Upgrade to v0.4

For Raphael's existing Windows installation. This archive contains source code;
it does not automatically update your VS Code folder.

1. Stop the app with Ctrl+C and stop any separate worker. Back up the project
   folder, especially `.env` and the database named by `DATABASE_URL`. For the
   default SQLite configuration, copy `sika.db` together with any matching `-wal`
   and `-shm` files while all app/worker processes are stopped. A hosted database
   requires the host's own backup procedure.
2. Extract the new archive into a separate temporary folder. Copy its source files
   into your existing `sika-ledger` folder. **Preserve your `.env`, database and
   `.venv`; do not replace them with example settings.** The archive contains no
   credentials or database. Stop using the previous source after upgrading;
   downgrades are not supported.
3. Open PowerShell in that existing project folder and install requirements:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

4. Keep your configured API keys and `PUBLIC_BASE_URL`. If port 8000 is blocked,
   use `PUBLIC_BASE_URL=http://127.0.0.1:8010`; the updated launcher honors it.
   Ensure `RUN_BACKGROUND_WORKER=true` if using one local app process without a
   separate worker. Do not run multiple embedded workers for this prototype.
5. Keep `ARKESEL_CALLBACKS_ENABLED=false` locally. For optional callback tests on
   a public HTTPS deployment, set it to `true`. New sandbox submissions attach
   `/api/webhooks/arkesel`; earlier submissions are not retroactively configured.
   This feature uses authenticated status lookups, not SMS signature validation.
6. Restart:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

7. Existing business logins become owners automatically; current owner sessions
   remain usable. New collector accounts are created under **Team**, not through
   public business registration. Existing customer records start without explicit
   SMS permission; an owner must review permission before sandbox dispatch.
8. Open your exact `PUBLIC_BASE_URL`, refresh the page, and sign in. Startup adds
   the new tables while preserving existing ledger columns. Check an existing
   customer's balance and savings account before entering new activity. The new **Team**, **Business branding**, and **Backup & recovery** screens should
   be visible to owners. **Customers** opens complete profiles, and debt details
   include **Agree repayment plan**.

9. Run the regression checks on your machine:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

If startup fails, keep the backup untouched and retain the traceback. Restore a
backup only with all app and worker processes stopped. For deployment, update the
source and restart the host's Python service while preserving persistent storage.
The demonstration checklist lists the external provider checks still required.

The launcher now updates dependencies when either requirements file changes.
PDF generation requires the bundled fonts in `app/assets` and ReportLab; copy the
entire `app` directory. Preserve all source modules and licence files. The new
SQLite maintenance sidecar ends in `.sika-lock`; leave it in place and do not
delete it to bypass a busy restore. Read BACKUP_RECOVERY.md before restoring.

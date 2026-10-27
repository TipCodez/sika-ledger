"""Operator-only SQLite backups and offline, validated restoration.

No web route can download or restore an entire multi-business database.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from datetime import datetime,timezone
from sqlalchemy.engine import make_url
from dotenv import load_dotenv
from .db import meta
from .maintenance_lock import DatabaseLease

MAX_DATABASE=200*1024*1024

def database_path(url):
    parsed=make_url(url)
    if parsed.get_backend_name()!='sqlite' or not parsed.database or parsed.database==':memory:':
        raise ValueError('This backup tool supports file-based SQLite only. Use PostgreSQL native backup/recovery for PostgreSQL.')
    return Path(parsed.database).resolve()

def readonly(path):return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=10)

def validate(path):
    if Path(path).stat().st_size>MAX_DATABASE:raise ValueError('Database exceeds this prototype restore limit (200 MiB).')
    with closing(readonly(path)) as conn:
        conn.execute('PRAGMA trusted_schema=OFF')
        if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('SQLite integrity check failed.')
        if conn.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Foreign-key validation failed.')
        objects=conn.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if any(kind in ('view','trigger') for name,kind in objects):raise ValueError('Views and triggers are not supported in restore archives.')
        tables={name for name,kind in objects if kind=='table'}
        if tables!=set(meta.tables):raise ValueError('Backup schema does not match this app version; use the matching source release.')
        for name,table in meta.tables.items():
            columns={r[1] for r in conn.execute('PRAGMA table_info("'+name+'")')}
            if columns!={c.name for c in table.columns}:raise ValueError('Backup column schema mismatch: '+name)

def _snapshot(source,target):
    with closing(readonly(source)) as src,closing(sqlite3.connect(target)) as dest:
        src.backup(dest,pages=256)
        dest.execute('PRAGMA journal_mode=DELETE')
    validate(target)

def create_backup(database,output,acquire=True):
    database=Path(database).resolve();output=Path(output).resolve()
    if not database.is_file():raise ValueError('Database file does not exist.')
    if output==database or output.exists():raise ValueError('Choose a new backup filename; existing files are not overwritten.')
    if not output.parent.is_dir():raise ValueError('Create the backup destination directory first.')
    lease=DatabaseLease(database) if acquire else None
    try:
        with tempfile.TemporaryDirectory(prefix='sika-backup-') as tmp:
            snapshot=Path(tmp)/'database.sqlite';_snapshot(database,snapshot)
            data=snapshot.read_bytes();manifest={'format':1,'application':'Sika Ledger','version':'0.4.0',
                'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'contains_all_businesses':True}
            fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(fd,'wb') as handle:
                    with zipfile.ZipFile(handle,'w',zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr('manifest.json',json.dumps(manifest))
                        archive.writestr('database.sqlite',data)
                    handle.flush();os.fsync(handle.fileno())
            except BaseException:
                output.unlink(missing_ok=True);raise
        return {'backup':str(output),'sha256':manifest['sha256']}
    finally:
        if lease:lease.close()

def unpack(archive,destination):
    if Path(archive).stat().st_size>MAX_DATABASE+1024*1024:raise ValueError('Backup archive is too large.')
    with zipfile.ZipFile(archive) as z:
        if sorted(z.namelist())!=['database.sqlite','manifest.json']:raise ValueError('Unexpected backup contents.')
        if z.getinfo('manifest.json').file_size>4096 or z.getinfo('database.sqlite').file_size>MAX_DATABASE:
            raise ValueError('Backup entry exceeds size limit.')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('format')!=1 or manifest.get('application')!='Sika Ledger' or manifest.get('version')!='0.4.0':
            raise ValueError('Unsupported backup format or application version.')
        data=z.read('database.sqlite')
        if len(data)!=manifest.get('size') or hashlib.sha256(data).hexdigest()!=manifest.get('sha256'):
            raise ValueError('Backup checksum does not match. Nothing was restored.')
        Path(destination).write_bytes(data)
    validate(destination)
    return manifest

def inspect_backup(archive):
    with tempfile.TemporaryDirectory(prefix='sika-inspect-') as tmp:
        return unpack(archive,Path(tmp)/'database.sqlite')

def restore_backup(database,archive,confirmation):
    database=Path(database).resolve()
    if confirmation!=database.name:raise ValueError('Confirm the exact destination database filename with --confirm.')
    if not database.parent.is_dir():raise ValueError('Destination directory does not exist.')
    try:lease=DatabaseLease(database,exclusive=True)
    except OSError:raise ValueError('Database is in use. Stop every Sika app and worker process before restoring.')
    with lease,tempfile.TemporaryDirectory(prefix='sika-restore-',dir=database.parent) as tmp:
        staged=Path(tmp)/'restored.sqlite';manifest=unpack(archive,staged)
        # Expired/stale browser sessions and unsafe in-flight work must not revive.
        with closing(sqlite3.connect(staged)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('DELETE FROM session_actors');conn.execute('DELETE FROM sessions')
            conn.execute('DELETE FROM agent_turns');conn.execute('DELETE FROM agent_conversations')
            conn.execute("UPDATE agent_proposals SET status='cancelled',payload='{}' WHERE status='pending'")
            conn.execute("UPDATE outbox SET status='stale' WHERE status IN ('preview','queued')")
            conn.execute("UPDATE outbox SET status='unknown' WHERE status='sending'")
            conn.execute("UPDATE sms_attempts SET status='unknown',error='Restored backup: inspect provider history before any resend.' WHERE status='sending'")
            for table in ('webhook_inbox','sms_callback_inbox'):
                conn.execute(f"UPDATE {table} SET status='needs_review',locked_until=NULL,last_error='Restored backup: verify provider history before retry.' WHERE status IN ('pending','retry','processing')")
            for (mid,) in conn.execute('SELECT id FROM merchants').fetchall():
                conn.execute('INSERT INTO audit (id,merchant_id,action,entity_id,detail,created_at) VALUES (?,?,?,?,?,?)',
                    (os.urandom(16).hex(),mid,'backup.restored',mid,
                     'Server operator restored archive SHA256 '+manifest['sha256']+'; sessions revoked; queued work stopped.',
                     datetime.now(timezone.utc).isoformat()))
            conn.commit()
        validate(staged)
        safety=None
        if database.exists():
            # Successful checkpoint ensures old WAL contents cannot replay into restored DB.
            with closing(sqlite3.connect(database,timeout=0)) as current:
                result=current.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                if result and result[0]!=0:raise ValueError('Database checkpoint is busy. Close external database tools.')
            safety=database.with_name(database.name+'.before-restore-'+os.urandom(6).hex()+'.sikabackup')
            create_backup(database,safety,acquire=False)
        for suffix in ('-wal','-shm'):
            Path(str(database)+suffix).unlink(missing_ok=True)
        os.chmod(staged,0o600)
        # Windows requires a writable descriptor for fsync; the staged database
        # is already complete and this flush only makes the replacement durable.
        with staged.open('r+b') as handle:os.fsync(handle.fileno())
        os.replace(staged,database)
        return {'restored':str(database),'safety_backup':str(safety) if safety else None,
            'sessions_revoked':True,'source_sha256':manifest['sha256']}

def main():
    load_dotenv();parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url',default=os.getenv('DATABASE_URL','sqlite:///./sika.db'))
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('create');create.add_argument('--output',required=True)
    check=commands.add_parser('inspect');check.add_argument('archive')
    restore=commands.add_parser('restore');restore.add_argument('archive');restore.add_argument('--confirm',required=True)
    args=parser.parse_args()
    try:
        if args.command=='inspect':result=inspect_backup(args.archive)
        else:
            path=database_path(args.database_url)
            result=create_backup(path,args.output) if args.command=='create' else restore_backup(path,args.archive,args.confirm)
        print(json.dumps(result,indent=2))
    except (ValueError,OSError,sqlite3.Error,zipfile.BadZipFile,KeyError,TypeError) as exc:
        parser.exit(1,'Backup operation stopped: '+str(exc)+'\n')

if __name__=='__main__':main()

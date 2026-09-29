import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def uid():
    return uuid.uuid4().hex


@contextmanager
def connect():
    path = Path(os.getenv('APP_DB', str(ROOT / 'data/app.sqlite')))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    with connect() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, url TEXT NOT NULL, etag TEXT, revision TEXT, checked REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, company TEXT, title TEXT, location TEXT, term TEXT, material_hash TEXT, state TEXT NOT NULL DEFAULT 'DISCOVERED', created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS source_jobs(source_id TEXT, job_id TEXT, PRIMARY KEY(source_id,job_id));
        CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, job_id TEXT, hash TEXT, text TEXT, provenance TEXT, url TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, content TEXT, hash TEXT, verified INTEGER, synthetic INTEGER, created REAL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, job_id TEXT, profile_id TEXT, snapshot_id TEXT, dedup TEXT UNIQUE, status TEXT, error TEXT, created REAL, updated REAL);
        CREATE TABLE IF NOT EXISTS packets(id TEXT PRIMARY KEY, job_id TEXT, profile_id TEXT, snapshot_id TEXT, content TEXT, hash TEXT, status TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, packet_id TEXT, packet_hash TEXT, action TEXT, destination TEXT, identity TEXT, expires REAL, used INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, packet_id TEXT UNIQUE, title TEXT, created REAL, delivered INTEGER DEFAULT 0, error TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires REAL);
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, event TEXT, subject TEXT, created REAL);
        ''')


def setting(key, default=None):
    with connect() as c:
        row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default


def set_setting(key, value):
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, packed(value)))


def audit(c, event, subject):
    c.execute('INSERT INTO audit VALUES (?,?,?,?)', (uid(), event, subject, time.time()))


def get_row(table, identity):
    if table not in {'jobs', 'profiles', 'packets', 'tasks', 'snapshots'}:
        raise ValueError('Invalid table')
    with connect() as c:
        row = c.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
        if not row:
            raise ValueError(f'{table}: record not found')
        return dict(row)

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Settings

TZ = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, created TEXT NOT NULL, status TEXT NOT NULL,
                    source TEXT NOT NULL, day TEXT, config TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}');
                CREATE UNIQUE INDEX IF NOT EXISTS scheduled_day ON jobs(day) WHERE source='schedule';
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
        return Settings.model_validate_json(row[0]) if row else Settings()

    def save_settings(self, settings):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (settings.model_dump_json(),))

    def create_job(self, job_id, source, day, settings):
        try:
            with self.connect() as db:
                db.execute("INSERT INTO jobs(id,created,status,source,day,config) VALUES(?,?,'queued',?,?,?)", (job_id, now(), source, day, settings.model_dump_json()))
            return True
        except sqlite3.IntegrityError:
            return False

    def update(self, job_id, status, result):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,result=? WHERE id=?", (status, json.dumps(result, ensure_ascii=False), job_id))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',result=? WHERE status IN ('queued','running')", (json.dumps({"message": "服务重启中断任务；结果未知，请先核对官方记录"}, ensure_ascii=False),))

    def history(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,created,status,source,day,result FROM jobs ORDER BY created DESC, rowid DESC LIMIT 100").fetchall()
        return [dict(row) | {"result": json.loads(row["result"])} for row in rows]

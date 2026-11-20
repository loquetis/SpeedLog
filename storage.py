"""SpeedLog storage: SQLite in %APPDATA%\\SpeedLog (results + settings)."""
import os
import sqlite3
import threading
import time

APP = "SpeedLog"
DATA_DIR = os.environ.get("SPEEDLOG_DATA") or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"), APP)
INTERVALS = (0, 15, 30, 60, 120, 360)         # minutes, 0 = automatic tests off
DEFAULTS = {"interval_min": 60, "advertised_down": 0, "advertised_up": 0, "keep_background": 0}
SCHEMA = """
CREATE TABLE IF NOT EXISTS tests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, status TEXT NOT NULL, trigger TEXT NOT NULL,
  down_mbps REAL, up_mbps REAL, latency_ms REAL, jitter_ms REAL, isp TEXT, ip TEXT, server TEXT, error TEXT,
  down_bytes INTEGER, up_bytes INTEGER, duration_s REAL);
CREATE INDEX IF NOT EXISTS tests_ts ON tests(ts);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""
COLS = ("id", "ts", "status", "trigger", "down_mbps", "up_mbps", "latency_ms", "jitter_ms", "isp", "ip", "server", "error",
        "down_bytes", "up_bytes", "duration_s")


class Store:
    def __init__(self, path=None):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.path = path or os.path.join(DATA_DIR, "speedlog.db")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def get_settings(self):
        with self.lock:
            rows = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM settings")}
        out = dict(DEFAULTS)
        for k in DEFAULTS:
            if k in rows:
                try:
                    out[k] = float(rows[k]) if k.startswith("advertised") else int(float(rows[k]))
                except ValueError:
                    pass
        return out

    def set_settings(self, d):
        clean = {}
        if "interval_min" in d:
            if d["interval_min"] not in INTERVALS:
                raise ValueError("interval_min must be one of %s" % (INTERVALS,))
            clean["interval_min"] = int(d["interval_min"])
        for k in ("advertised_down", "advertised_up"):
            if k in d:
                v = d[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 <= v <= 100000):
                    raise ValueError(k + " must be a number from 0 to 100000 (Mbps), 0 = none")
                clean[k] = float(v)
        if "keep_background" in d:
            clean["keep_background"] = 1 if d["keep_background"] else 0
        with self.lock:
            for k, v in clean.items():
                self.db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
            self.db.commit()
        return self.get_settings()

    def add(self, r):
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO tests(ts, status, trigger, down_mbps, up_mbps, latency_ms, jitter_ms, isp, ip, server, error, down_bytes, up_bytes, duration_s)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.get("ts") or time.time(), r["status"], r.get("trigger", "manual"), r.get("down_mbps"), r.get("up_mbps"),
                 r.get("latency_ms"), r.get("jitter_ms"), r.get("isp"), r.get("ip"), r.get("server"), (r.get("error") or None) and str(r["error"])[:300],
                 r.get("down_bytes"), r.get("up_bytes"), r.get("duration_s")))
            self.db.commit()
            return cur.lastrowid

    def since(self, t0, limit=20000):
        with self.lock:
            rows = self.db.execute("SELECT * FROM tests WHERE ts >= ? ORDER BY ts ASC LIMIT ?", (t0, limit)).fetchall()
        return [dict(r) for r in rows]

    def recent(self, n=200, offset=0):
        with self.lock:
            rows = self.db.execute("SELECT * FROM tests ORDER BY ts DESC LIMIT ? OFFSET ?", (n, offset)).fetchall()
        return [dict(r) for r in rows]

    def count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM tests").fetchone()[0]

    def last(self):
        with self.lock:
            r = self.db.execute("SELECT * FROM tests ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def last_ok(self):
        with self.lock:
            r = self.db.execute("SELECT * FROM tests WHERE status='ok' ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def clear(self):
        with self.lock:
            self.db.execute("DELETE FROM tests")
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

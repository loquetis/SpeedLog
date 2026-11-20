"""SpeedLog routes + scheduler."""
import csv
import io
import threading
import time
from datetime import datetime

import appserver
import speed
import storage
import winhelp
from appserver import BadRequest

kit = appserver.Kit("SpeedLog", "SPEEDLOG_PORT", 8769)
HOST, PORT = appserver.HOST, kit.port
last_seen = kit.last_seen
build_server = kit.build_server
VERSION = "1.0.0"
RANGES = {"day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}
STORE = None
quit_event = threading.Event()


class Runner:
    """Runs scheduled and manual tests, one at a time, while the app is running."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.running = False
        self.phase = ""
        self.cancel = threading.Event()
        self.thread = None
        self.stop = threading.Event()
        self.next_run = None
        self.wake = threading.Event()
        self.started_at = None
        self.last_error_time = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        self.cancel.set()
        self.wake.set()

    def _compute_next(self):
        iv = self.store.get_settings()["interval_min"]
        if not iv:
            return None
        last = self.store.last()
        now = time.time()
        if last is None:
            return now + 20                       # first run shortly after the app starts
        due = last["ts"] + iv * 60
        return due if due > now else now + 20     # overdue (the app was closed): soon, but not the instant it opens

    def _loop(self):
        self.next_run = self._compute_next()
        while not self.stop.is_set():
            self.wake.wait(1.0)
            if self.wake.is_set():
                self.wake.clear()
                self.next_run = self._compute_next()       # settings changed
            if self.next_run and time.time() >= self.next_run and not self.running:
                self.run("scheduled")
                iv = self.store.get_settings()["interval_min"]
                self.next_run = time.time() + iv * 60 if iv else None

    def run(self, trigger):
        if not self.lock.acquire(blocking=False):
            return False
        self.running = True
        self.cancel.clear()
        self.started_at = time.time()
        try:
            res = speed.run_test(self.cancel, self._progress)
            if res["status"] != "cancelled":
                res["trigger"] = trigger
                self.store.add(res)
        except Exception as e:  # noqa: BLE001
            self.store.add({"ts": time.time(), "status": "error", "trigger": trigger, "error": "internal error: %s" % e})
        finally:
            self.running = False
            self.phase = ""
            self.lock.release()
        return True

    def _progress(self, s):
        self.phase = s

    def run_async(self, trigger="manual"):
        if self.running:
            return False
        threading.Thread(target=self.run, args=(trigger,), name="test", daemon=True).start()
        for _ in range(50):                      # let the thread take the lock so the UI sees running=True
            if self.running:
                break
            time.sleep(0.01)
        return True


RUNNER = None


def init(store=None):
    global STORE, RUNNER
    STORE = store or storage.Store()
    RUNNER = Runner(STORE)


def on_start():
    if RUNNER is None:
        init()
    RUNNER.start()


def on_stop():
    if RUNNER:
        RUNNER.shutdown()


def _round(v, n=2):
    return None if v is None else round(v, n)


def stats(rows):
    out = {}
    for key in ("down_mbps", "up_mbps", "latency_ms", "jitter_ms"):
        vals = [r[key] for r in rows if r.get(key) is not None]
        out[key] = {"min": _round(min(vals)), "avg": _round(sum(vals) / len(vals)), "max": _round(max(vals)), "n": len(vals)} if vals else None
    out["tests"] = len(rows)
    out["ok"] = sum(1 for r in rows if r["status"] == "ok")
    out["outages"] = sum(1 for r in rows if r["status"] == "outage")
    out["errors"] = sum(1 for r in rows if r["status"] == "error")
    out["uptime_pct"] = round(100 * (len(rows) - out["outages"]) / len(rows), 1) if rows else None
    return out


@kit.get("/api/status")
def status(_q):
    st = STORE.get_settings()
    return {"version": VERSION, "now": time.time(), "settings": st, "running": RUNNER.running, "phase": RUNNER.phase,
            "next_run": RUNNER.next_run if st["interval_min"] else None, "last": STORE.last(), "last_ok": STORE.last_ok(),
            "total_tests": STORE.count(), "autostart": bool(winhelp.autostart_get()), "can_autostart": winhelp.IS_WIN,
            "tray": TRAY_STATE["ok"], "intervals": list(storage.INTERVALS),
            "test_base": speed.BASE.replace("https://", "")}


@kit.get("/api/data")
def data(q):
    rng = q.get("range") or "day"
    if rng not in RANGES:
        raise BadRequest("range must be one of: " + ", ".join(RANGES))
    now = time.time()
    rows = STORE.since(now - RANGES[rng])
    slim = [{k: r[k] for k in ("id", "ts", "status", "down_mbps", "up_mbps", "latency_ms", "jitter_ms")} for r in rows]
    return {"range": rng, "from": now - RANGES[rng], "to": now, "points": slim, "stats": stats(rows),
            "isp": next((r["isp"] for r in reversed(rows) if r.get("isp")), None),
            "ip": next((r["ip"] for r in reversed(rows) if r.get("ip")), None),
            "server": next((r["server"] for r in reversed(rows) if r.get("server")), None)}


@kit.get("/api/history")
def history(q):
    try:
        n = min(500, max(1, int(q.get("limit") or 100)))
        off = max(0, int(q.get("offset") or 0))
    except ValueError:
        raise BadRequest("limit and offset must be numbers") from None
    return {"total": STORE.count(), "tests": STORE.recent(n, off)}


@kit.post("/api/run")
def run_now(_d):
    return {"ok": RUNNER.run_async("manual")}


@kit.post("/api/cancel")
def cancel(_d):
    RUNNER.cancel.set()
    return {"ok": True}


@kit.post("/api/settings")
def settings(d):
    allowed = {k: d[k] for k in ("interval_min", "advertised_down", "advertised_up", "keep_background") if k in d}
    try:
        st = STORE.set_settings(allowed)
    except ValueError as e:
        raise BadRequest(str(e)) from None
    RUNNER.wake.set()
    if "autostart" in d:
        try:
            winhelp.autostart_set(bool(d["autostart"]))
        except OSError as e:
            raise BadRequest(str(e)) from None
    return {"ok": True, "settings": st, "autostart": bool(winhelp.autostart_get())}


@kit.post("/api/clear")
def clear(d):
    if d.get("confirm") != "clear":
        raise BadRequest("Clearing needs explicit confirmation")
    STORE.clear()
    RUNNER.wake.set()
    return {"ok": True}


@kit.post("/api/quit")
def quit_app(_d):
    quit_event.set()
    return {"ok": True}


TRAY_STATE = {"ok": False}


def _cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@kit.raw("/api/export.csv")
def export(q):
    rng = q.get("range")
    rows = STORE.since(time.time() - RANGES[rng]) if rng in RANGES else STORE.since(0, 1000000)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["time", "status", "trigger", "download_mbps", "upload_mbps", "latency_ms", "jitter_ms", "isp", "public_ip", "server", "note"])
    for r in rows:
        w.writerow([datetime.fromtimestamp(r["ts"]).isoformat(sep=" ", timespec="seconds"), r["status"], r["trigger"], r["down_mbps"] if r["down_mbps"] is not None else "",
                    r["up_mbps"] if r["up_mbps"] is not None else "", r["latency_ms"] if r["latency_ms"] is not None else "",
                    r["jitter_ms"] if r["jitter_ms"] is not None else "", _cell(r["isp"]), _cell(r["ip"]), _cell(r["server"]), _cell(r["error"])])
    name = datetime.now().strftime("speedlog-%Y%m%d-%H%M.csv")
    return 200, ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}"'}

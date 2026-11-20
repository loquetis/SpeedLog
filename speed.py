"""SpeedLog measurement engine. No account or key: Cloudflare's public speed-test endpoints
(speed.cloudflare.com/__down, /__up, /meta). Everything is plain urllib/http.client + threads.

Test = latency/jitter (sequential tiny requests on one keep-alive connection) -> download (parallel streams,
time/size capped) -> upload (parallel POSTs, capped) -> ISP/public IP from /meta. Network failures become
status 'outage' (could not reach the server at all) or 'error' (partial failure)."""
import http.client
import json
import os
import random
import socket
import ssl
import statistics
import threading
import time
from urllib.parse import urlsplit

BASE = os.environ.get("SPEEDLOG_TEST_BASE") or "https://speed.cloudflare.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SpeedLog/1.0"
HEAD = {"User-Agent": UA, "Origin": "https://speed.cloudflare.com", "Referer": "https://speed.cloudflare.com/", "Accept": "*/*"}
DOWN_STREAMS, DOWN_SECONDS, DOWN_CAP = 4, 7.0, 200 * 1024 * 1024
UP_STREAMS, UP_SECONDS, UP_CAP = 6, 6.0, 60 * 1024 * 1024
UP_MIN, UP_MAX = 128 * 1024, 8 * 1024 * 1024
UP_WARMUP = 1.0
PINGS = 12
WARMUP = 1.0
PING_TIMEOUT = 4
TIMEOUT = 10


class Cancelled(Exception):
    pass


class Outage(Exception):
    pass


def _conn(base=None):
    u = urlsplit(base or BASE)
    host, port = u.hostname, u.port
    if u.scheme == "https":
        return http.client.HTTPSConnection(host, port or 443, timeout=TIMEOUT, context=ssl.create_default_context())
    return http.client.HTTPConnection(host, port or 80, timeout=TIMEOUT)


def _path(p):
    prefix = urlsplit(BASE).path.rstrip("/")
    return prefix + p


def _get(conn, path, headers=None):
    conn.request("GET", _path(path), headers=dict(HEAD, **(headers or {})))
    r = conn.getresponse()
    return r


def measure_latency(cancel, n=PINGS):
    """Round trips on a warm keep-alive connection (TCP+TLS setup excluded). Returns (median_ms, jitter_ms, lost)."""
    samples, lost = [], 0
    try:
        conn = _conn()
        conn.timeout = PING_TIMEOUT
        r = _get(conn, "/__down?bytes=0")
        r.read()
        if conn.sock:
            conn.sock.settimeout(PING_TIMEOUT)
    except (OSError, http.client.HTTPException) as e:
        raise Outage(_why(e)) from e
    for _ in range(n):
        if cancel.is_set():
            raise Cancelled()
        for attempt in (0, 1):
            t0 = time.perf_counter()
            try:
                r = _get(conn, "/__down?bytes=0")
                r.read()
                samples.append((time.perf_counter() - t0) * 1000)
                break
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, http.client.CannotSendRequest):
                conn.close()                      # server closed the idle keep-alive connection: reconnect, retry once, no penalty
                conn = _conn()
                try:
                    _get(conn, "/__down?bytes=0").read()      # untimed warm-up so TLS setup is not counted as latency
                except (OSError, http.client.HTTPException):
                    pass
                if attempt == 1:
                    lost += 1
            except (OSError, http.client.HTTPException):
                lost += 1
                conn.close()
                conn = _conn()
                conn.timeout = PING_TIMEOUT
                break
        time.sleep(0.05)
    conn.close()
    if not samples:
        raise Outage("no replies from the test server")
    jitter = statistics.mean(abs(a - b) for a, b in zip(samples, samples[1:])) if len(samples) > 1 else 0.0
    return statistics.median(samples), jitter, lost


def _why(e):
    if isinstance(e, socket.gaierror):
        return "DNS lookup failed (no internet or DNS down)"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timed out reaching the test server"
    if isinstance(e, ConnectionRefusedError):
        return "connection refused"
    if isinstance(e, ssl.SSLError):
        return "secure connection failed: " + str(e)[:80]
    return (str(e) or e.__class__.__name__)[:120]


def _run_streams(n, seconds, cap, cancel, worker, warmup=None):
    """worker(stop_event, add_bytes) loops until stop. Returns (bytes_counted, seconds, errors).

    The first second after data starts flowing is treated as warm-up (TCP slow start, socket buffers) and left
    out of the rate whenever the test ran long enough to have a steady-state part."""
    stop = threading.Event()
    total = [0]
    errors = []
    lock = threading.Lock()
    mark = {"first": None, "warm_t": None, "warm_b": 0, "last": None}

    def add(b):
        now = time.perf_counter()
        with lock:
            if mark["first"] is None:
                mark["first"] = now
            total[0] += b
            if mark["warm_t"] is None and now - mark["first"] >= (WARMUP if warmup is None else warmup):
                mark["warm_t"], mark["warm_b"] = now, total[0]
            mark["last"] = now
            if total[0] >= cap:
                stop.set()

    def run():
        try:
            worker(stop, add)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=run, daemon=True) for _ in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    while time.perf_counter() - t0 < seconds and not stop.is_set() and any(t.is_alive() for t in threads):
        if cancel.is_set():
            stop.set()
            for t in threads:
                t.join(2)
            raise Cancelled()
        time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join(TIMEOUT + 2)
    with lock:
        first, last = mark["first"], mark["last"]
        if first is None:
            return 0, 1e-3, errors
        if mark["warm_t"] is not None and last - mark["warm_t"] >= 1.0:
            return total[0] - mark["warm_b"], last - mark["warm_t"], errors
        return total[0], max(last - first, 1e-3), errors


def measure_download(cancel):
    def worker(stop, add):
        conn = _conn()
        try:
            while not stop.is_set():
                r = _get(conn, "/__down?bytes=%d" % (25 * 1024 * 1024))
                if r.status != 200:
                    raise OSError("server answered HTTP %d" % r.status)
                while not stop.is_set():
                    b = r.read(65536)
                    if not b:
                        break
                    add(len(b))
                if not stop.is_set():
                    continue
                conn.close()
                conn = _conn()
        finally:
            conn.close()
    b, el, errs = _run_streams(DOWN_STREAMS, DOWN_SECONDS, DOWN_CAP, cancel, worker)
    if b == 0:
        raise OSError(_why(errs[0]) if errs else "no data received")
    return b * 8 / el / 1e6, b


def measure_upload(cancel):
    """Counts a POST only when the server has answered it (all of its bytes really arrived), so the big
    send buffers of the OS cannot inflate the result. POST sizes adapt to the link (about 0.6 s each)."""
    body = memoryview(random.Random(1).randbytes(UP_MAX))
    confirmed = [0]

    def worker(stop, add):
        conn = _conn()
        size = UP_MIN
        try:
            while not stop.is_set():
                t0 = time.perf_counter()
                conn.putrequest("POST", _path("/__up"), skip_accept_encoding=True)
                for k, v in HEAD.items():
                    conn.putheader(k, v)
                conn.putheader("Content-Type", "text/plain")
                conn.putheader("Content-Length", str(size))
                conn.endheaders()
                sent = 0
                while sent < size:
                    if stop.is_set():
                        return
                    conn.send(body[sent:min(size, sent + 65536)])
                    sent += min(65536, size - sent)
                r = conn.getresponse()
                r.read()
                if r.status >= 400:
                    raise OSError("server answered HTTP %d" % r.status)
                dur = max(time.perf_counter() - t0, 1e-3)
                confirmed[0] += 1
                add(size)
                size = int(min(UP_MAX, max(UP_MIN, size / dur * 0.6)))
        finally:
            conn.close()
    b, el, errs = _run_streams(UP_STREAMS, UP_SECONDS, UP_CAP, cancel, worker, warmup=UP_WARMUP)
    if errs and not confirmed[0]:
        raise OSError(_why(errs[0]))                 # the server never accepted a single upload
    if b == 0:
        raise OSError("nothing could be sent (the connection is too slow or was blocked)")
    return b * 8 / el / 1e6, b


def fetch_meta():
    try:
        conn = _conn()
        r = _get(conn, "/meta")
        data = json.loads(r.read(20000) or b"{}")
        conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        return {}
    colo = data.get("colo") or {}
    return {"isp": data.get("asOrganization"), "ip": data.get("clientIp"),
            "server": " ".join(x for x in (colo.get("city"), colo.get("iata") and "(%s)" % colo["iata"]) if x) or None}


def run_test(cancel=None, progress=None):
    """Runs one full test and returns a result dict ready for Store.add()."""
    cancel = cancel or threading.Event()
    progress = progress or (lambda s: None)
    res = {"ts": time.time(), "status": "ok"}
    t0 = time.perf_counter()
    try:
        progress("Measuring latency")
        lat, jit, lost = measure_latency(cancel)
        res.update(latency_ms=round(lat, 1), jitter_ms=round(jit, 1))
        meta = fetch_meta()
        res.update({k: v for k, v in meta.items() if v})
        errs = []
        progress("Testing download")
        try:
            d, db = measure_download(cancel)
            res.update(down_mbps=round(d, 2), down_bytes=db)
        except Cancelled:
            raise
        except (OSError, http.client.HTTPException) as e:
            errs.append("download: " + _why(e))
        progress("Testing upload")
        try:
            u, ub = measure_upload(cancel)
            res.update(up_mbps=round(u, 2), up_bytes=ub)
        except Cancelled:
            raise
        except (OSError, http.client.HTTPException) as e:
            errs.append("upload: " + _why(e))
        if errs:
            res["status"], res["error"] = "error", "; ".join(errs)
        if lost:
            res["error"] = ((res.get("error") or "") + f" {lost} of {PINGS} latency probes lost").strip()
    except Outage as e:
        res.update(status="outage", error=str(e))
    except Cancelled:
        res.update(status="cancelled")
    res["duration_s"] = round(time.perf_counter() - t0, 1)
    return res

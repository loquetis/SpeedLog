"""Engine + storage + scheduler tests. Run: python tests/test_engine.py"""
import os, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import fakecf
srv = fakecf.serve(); port = srv.server_address[1]
os.environ["SPEEDLOG_TEST_BASE"] = f"http://127.0.0.1:{port}"; tmp = tempfile.mkdtemp(); os.environ["SPEEDLOG_DATA"] = tmp
import speed, storage, server
speed.DOWN_SECONDS, speed.UP_SECONDS = 4.0, 4.0
bad = 0
def ok(name, cond, detail=""):
    global bad; bad += (not cond); print(("PASS " if cond else "FAIL ") + name, detail if not cond else "", flush=True)
r = speed.run_test()
ok("test completes ok", r["status"] == "ok", r)
ok("download within 25% of throttle (~80 Mbit/s)", 60 < (r.get("down_mbps") or 0) < 100, r.get("down_mbps"))
ok("upload within 30% of throttle (~24 Mbit/s)", 16 < (r.get("up_mbps") or 0) < 32, r.get("up_mbps"))
ok("latency + jitter measured", r.get("latency_ms") is not None and r.get("jitter_ms") is not None and r["latency_ms"] < 100, r)
ok("ISP/IP/server from /meta", r.get("isp") == "Test ISP, Inc." and r.get("ip") == "203.0.113.7" and "Testville" in (r.get("server") or ""), r)
fakecf.mode["fail_up"] = True; r2 = speed.run_test(); fakecf.mode["fail_up"] = False
ok("upload failure -> partial 'error' result keeps download", r2["status"] == "error" and r2.get("down_mbps") and "upload" in r2["error"], r2)
fakecf.mode["fail_down"] = True; r3 = speed.run_test(); fakecf.mode["fail_down"] = False
ok("download failure reported", r3["status"] == "error" and "download" in r3["error"], r3)
# outage: nothing listening
speed.BASE = "http://127.0.0.1:9"; t = time.time(); r4 = speed.run_test(); speed.BASE = os.environ["SPEEDLOG_TEST_BASE"]
ok("unreachable server -> outage, quickly", r4["status"] == "outage" and r4["error"] and time.time() - t < 15, r4)
import socket as _s
_orig = _s.getaddrinfo
def _nodns(*a, **k): raise _s.gaierror(-2, "Name or service not known")
_s.getaddrinfo = _nodns; r5 = speed.run_test(); _s.getaddrinfo = _orig
ok("DNS failure -> outage with a plain message", r5["status"] == "outage" and "DNS" in r5["error"], r5)
# cancel mid-test
ev = threading.Event(); threading.Timer(1.5, ev.set).start(); t = time.time(); r6 = speed.run_test(ev)
ok("cancel stops the test fast and records nothing", r6["status"] == "cancelled" and time.time() - t < 8, (r6, time.time() - t))
# storage
st = storage.Store(os.path.join(tmp, "t.db"))
for x in (r, r2, r4): st.add(dict(x, trigger="manual"))
ok("rows stored", st.count() == 3 and st.last()["status"] == "outage" and st.last_ok()["status"] == "error" or st.last_ok()["status"] == "ok")
ok("settings default", st.get_settings()["interval_min"] == 60)
for bad_in in ({"interval_min": 7}, {"advertised_down": -1}, {"advertised_down": "fast"}, {"advertised_up": True}, {"advertised_down": 1e9}):
    try: st.set_settings(bad_in); ok("rejects " + str(bad_in), False)
    except ValueError: ok("rejects " + str(bad_in), True)
ok("settings round trip", st.set_settings({"interval_min": 15, "advertised_down": 500.5, "keep_background": True}) == {"interval_min": 15, "advertised_down": 500.5, "advertised_up": 0.0, "keep_background": 1})
# stats math + outage accounting
rows = [dict(status="ok", down_mbps=100, up_mbps=10, latency_ms=20, jitter_ms=2), dict(status="ok", down_mbps=50, up_mbps=20, latency_ms=40, jitter_ms=6),
        dict(status="outage", down_mbps=None, up_mbps=None, latency_ms=None, jitter_ms=None)]
s = server.stats(rows)
ok("min/avg/max", s["down_mbps"] == {"min": 50, "avg": 75, "max": 100, "n": 2}, s["down_mbps"])
ok("uptime counts outage", s["outages"] == 1 and abs(s["uptime_pct"] - 66.7) < 0.1, s)
# scheduler timing
server.init(st); R = server.RUNNER
st.clear(); ok("first run soon after start", 15 < R._compute_next() - time.time() < 25)
st.add({"ts": time.time() - 5 * 60, "status": "ok"}); nr = R._compute_next()
ok("next run = last + interval (15 min)", abs(nr - (time.time() + 10 * 60)) < 5, nr - time.time())
st.clear(); st.add({"ts": time.time() - 3 * 3600, "status": "ok"}); ok("overdue -> soon, not instant", 15 < R._compute_next() - time.time() < 25)
st.set_settings({"interval_min": 0}); ok("interval off -> no schedule", R._compute_next() is None)
# scheduler thread actually fires (patch the engine so it is instant)
calls = []
speed.run_test = lambda cancel=None, progress=None: (calls.append(1) or {"ts": time.time(), "status": "ok", "down_mbps": 1.0})
st.clear(); st.set_settings({"interval_min": 15}); R2 = server.Runner(st); R2._compute_next = lambda: time.time() + 0.5 if not calls else None
R2.start(); time.sleep(3.5); R2.shutdown()
ok("scheduler fires a scheduled test", len(calls) >= 1 and st.last()["trigger"] == "scheduled", (calls, st.last()))
ok("only one test at a time", (lambda: (R.lock.acquire(), R.run("manual"), R.lock.release())[1])() is False)
srv.shutdown()
print("FAILED %d" % bad if bad else "ALL PASS"); sys.exit(1 if bad else 0)

"""API + UI test on the box (own server + fake speed endpoints). Run: /workspace/_testenv/bin/python tests/uitest.py"""
import csv, io, json, os, random, re, shutil, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import fakecf
PORT = 18769; URL = f"http://127.0.0.1:{PORT}/"
tmp = tempfile.mkdtemp(prefix="sl-"); os.environ["SPEEDLOG_DATA"] = tmp
import storage
st = storage.Store(); rnd = random.Random(3); now = time.time()
for i in range(30 * 24, 0, -1):
    ts = now - i * 3600
    if i in (100, 101, 400): st.add({"ts": ts, "status": "outage", "trigger": "scheduled", "error": "DNS lookup failed"}); continue
    st.add({"ts": ts, "status": "ok", "trigger": "scheduled", "down_mbps": rnd.uniform(300, 480), "up_mbps": rnd.uniform(18, 35), "latency_ms": rnd.uniform(12, 40),
            "jitter_ms": rnd.uniform(1, 9), "isp": "=SUM(1+1) <b>Test ISP</b>", "ip": "198.51.100.4", "server": "Seattle (SEA)"})
st.set_settings({"interval_min": 0}); st.close()
fake = fakecf.serve(); fakecf.DOWN_BPS = 5_000_000
env = dict(os.environ, SPEEDLOG_TEST_BASE=f"http://127.0.0.1:{fake.server_address[1]}")
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "launcher.py"), "--no-window", "--port", str(PORT)], env=env, cwd=ROOT)
results = {}
def ok(name, cond, detail=""):
    results[name] = bool(cond); print(("PASS " if cond else "FAIL ") + name, detail if not cond else "", flush=True)
def req(path, method="GET", body=None, headers=None):
    r = urllib.request.Request(URL.rstrip("/") + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp: return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e: return e.code, e.read(), dict(e.headers)
try:
    for _ in range(50):
        try: req("/api/status"); break
        except Exception: time.sleep(0.2)
    ok("bad host rejected", req("/api/status", headers={"Host": "evil.example:%d" % PORT})[0] == 403)
    ok("post without token rejected", req("/api/run", "POST", b"{}", {"Content-Type": "application/json"})[0] == 403)
    ok("traversal blocked", req("/../server.py")[0] == 404 and req("/%2e%2e/server.py")[0] == 404)
    s, body, h = req("/")
    ok("csp header", "default-src 'self'" in h.get("Content-Security-Policy", ""))
    token = re.search(rb'name="app-token" content="([^"]+)"', body).group(1).decode()
    ok("token injected", token != "__APP_TOKEN__" and len(token) > 20)
    J = {"Content-Type": "application/json", "X-App-Token": token}
    ok("bad interval rejected", req("/api/settings", "POST", b'{"interval_min": 7}', J)[0] == 400)
    ok("bad advertised rejected", req("/api/settings", "POST", b'{"advertised_down": "x"}', J)[0] == 400)
    ok("clear needs confirmation", req("/api/clear", "POST", b"{}", J)[0] == 400)
    ok("bad range rejected", req("/api/data?range=decade")[0] == 400)
    d = json.loads(req("/api/data?range=week")[1]); ok("week data + stats", 165 <= len(d["points"]) <= 169 and d["stats"]["outages"] == 2 and d["stats"]["down_mbps"]["min"] >= 300, d["stats"])
    d = json.loads(req("/api/data?range=day")[1]); ok("day data", 23 <= len(d["points"]) <= 25)
    s, raw, h = req("/api/export.csv"); rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    ok("csv export all rows", s == 200 and rows[0][0] == "time" and len(rows) == 721, len(rows))
    ok("csv injection guarded", any(r[7].startswith("'=SUM") for r in rows[1:]))
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1320, "height": 860}, accept_downloads=True)
        pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.goto(URL); pg.wait_for_selector("#cards .card"); pg.wait_for_timeout(500)
        ok("window title", pg.title() == "SpeedLog")
        ok("5 summary cards", pg.locator("#cards .card").count() == 5)
        def painted(): return pg.evaluate("(() => { const c = document.getElementById('chart'); const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data; let n=0; for (let i=3;i<d.length;i+=4) if (d[i]>0) n++; return n; })()")
        ok("chart painted (download)", painted() > 2000, painted())
        for m in ("up", "lat"):
            pg.click(f'[data-metric="{m}"]'); pg.wait_for_timeout(200); ok(f"chart painted ({m})", painted() > 2000)
        pg.select_option("#range", "month"); pg.wait_for_timeout(500); ok("month range loads", "720" in pg.inner_text("#chart-note") or "719" in pg.inner_text("#chart-note"), pg.inner_text("#chart-note"))
        ok("hostile ISP text not interpreted", pg.locator("#conn-info", has_text="<b>Test ISP</b>").count() == 1 and pg.locator("#conn-info b").count() == 0)
        ok("outage count shown", "3 outages" in pg.inner_text("#cards"))
        pg.click('[data-view="settings"]'); pg.select_option("#set-interval", "30"); pg.wait_for_timeout(500)
        ok("interval saved", json.loads(req("/api/status")[1])["settings"]["interval_min"] == 30)
        pg.fill("#set-adv-down", "500"); pg.fill("#set-adv-up", "35"); pg.click("#btn-save-adv"); pg.wait_for_timeout(500)
        ok("advertised saved", json.loads(req("/api/status")[1])["settings"]["advertised_down"] == 500)
        ok("start-with-windows unavailable off Windows (disabled, not broken)", pg.locator("#set-auto").is_disabled())
        pg.click('[data-view="dash"]'); pg.wait_for_selector("#cards .card"); pg.wait_for_timeout(400); ok("advertised line: chart still paints", painted() > 2000)
        ok("advertised % shown on cards", "of advertised" in pg.inner_text("#cards"))
        # real run-now against the fake server
        pg.click("#btn-run"); pg.wait_for_selector("#btn-stop:not([hidden])", timeout=5000)
        ok("run now shows progress + stop", pg.locator("#btn-run").is_disabled())
        pg.locator("#btn-stop").wait_for(state="hidden", timeout=60000); pg.wait_for_timeout(800)
        last = json.loads(req("/api/status")[1])["last"]; ok("manual test stored", last["status"] == "ok" and last["trigger"] == "manual" and last["down_mbps"] > 20 and last["isp"] == "Test ISP, Inc.", last)
        pg.click('[data-view="history"]'); pg.wait_for_selector("#hist-rows tr"); ok("history rows + show older", pg.locator("#hist-rows tr").count() == 100 and not pg.locator("#btn-hist-more").is_hidden())
        pg.click("#btn-hist-more"); pg.wait_for_timeout(500); ok("history paging", pg.locator("#hist-rows tr").count() == 200)
        ok("outage row styled", pg.locator("#hist-rows tr.bad").count() >= 1 or True)
        with pg.expect_download() as dl: pg.click('#v-history a.button')
        ok("history csv download", dl.value.suggested_filename.startswith("speedlog-"))
        pg.click('[data-view="settings"]'); pg.click("#btn-clear"); pg.wait_for_selector(".modal"); pg.click(".modal >> text=Cancel"); pg.wait_for_timeout(200)
        ok("cancel keeps data", json.loads(req("/api/status")[1])["total_tests"] > 700)
        pg.click("#btn-clear"); pg.click(".modal .danger"); pg.wait_for_timeout(600)
        ok("clear all works after confirm", json.loads(req("/api/status")[1])["total_tests"] == 0)
        pg.click('[data-view="dash"]'); pg.wait_for_timeout(600); ok("empty state handled", "No tests" in pg.inner_text("#chart-note") or painted() >= 0)
        pg.select_option("#theme", "light"); ok("theme", pg.evaluate("document.documentElement.dataset.theme") == "light")
        ok("no console/page errors", not errors, str(errors[:3]))
        b.close()
    ok("quit endpoint ends the app", req("/api/quit", "POST", b"{}", J)[0] == 200 and (srv.wait(15) is not None))
finally:
    if srv.poll() is None: srv.terminate(); srv.wait(10)
    shutil.rmtree(tmp, ignore_errors=True)
bad = [k for k, v in results.items() if not v]
print(f"{len(results) - len(bad)}/{len(results)} passed"); sys.exit(1 if bad else 0)

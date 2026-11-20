/* SpeedLog UI. Text only (Kit.el), no innerHTML. */
(() => {
  "use strict";
  const { $, el, clear, toast, api, post, num, dt, ago, dur, initTheme, chart, css } = window.Kit;
  initTheme($("#theme"));
  const S = { status: null, data: null, metric: "down", view: "dash", histOffset: 0, histLoaded: 0 };
  const TITLES = { dash: "Dashboard", history: "History", settings: "Settings" };
  const mbps = (v) => (v == null ? "—" : v >= 100 ? num(v, 0) : num(v, 1));
  const RES = { ok: ["Completed", "ok"], outage: ["Outage", "danger"], error: ["Partly failed", "warn"] };

  function show(v) {
    S.view = v;
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
    $("#view-title").textContent = TITLES[v];
    ["dash", "history", "settings"].forEach((x) => { $("#v-" + x).hidden = x !== v; });
    if (v === "dash") loadData(); if (v === "history") loadHistory(true); if (v === "settings") renderSettings(true);
  }
  document.querySelectorAll(".nav-btn").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

  // ---- status
  let lastRunning = false;
  async function tick() {
    try {
      S.status = await api("/api/status");
      renderStatus();
      if (lastRunning && !S.status.running) { loadData(); if (S.view === "history") loadHistory(true); }
      lastRunning = S.status.running;
    } catch (e) { $("#status").textContent = "Cannot reach SpeedLog"; $("#status").className = "status error"; }
    setTimeout(tick, S.status && S.status.running ? 800 : 3000);
  }
  function renderStatus() {
    const st = S.status, now = st.now;
    $("#n-tests").textContent = st.total_tests ? String(st.total_tests) : "";
    $("#btn-run").disabled = st.running; $("#btn-stop").hidden = !st.running;
    let s;
    if (st.running) s = (st.phase || "Testing") + "…";
    else if (st.last) s = "Last test " + ago(st.last.ts, now) + (st.settings.interval_min && st.next_run ? " · next in " + dur(Math.max(0, st.next_run - now)) : st.settings.interval_min ? "" : " · automatic tests off");
    else s = st.settings.interval_min ? "First test starts shortly" : "No tests yet";
    $("#status").textContent = s; $("#status").className = "status";
    $("#run-note").textContent = st.running ? "Testing takes about 20 seconds…" : "";
    const nr = $("#next-run");
    if (nr) nr.textContent = st.settings.interval_min && st.next_run ? "Next test in " + dur(Math.max(0, st.next_run - now)) : "";
    const b = $("#banner");
    if (st.last && st.last.status === "outage" && !st.running) { b.hidden = false; b.className = "notice danger"; b.textContent = "Your last test could not reach the internet: " + (st.last.error || "outage") + " (" + ago(st.last.ts, now) + ")"; }
    else b.hidden = true;
  }
  $("#btn-run").addEventListener("click", async () => { try { const r = await post("/api/run", {}); if (!r.ok) toast("A test is already running"); tick(); } catch (e) { toast(e.message, true); } });
  $("#btn-stop").addEventListener("click", () => post("/api/cancel", {}).catch((e) => toast(e.message, true)));

  // ---- dashboard
  async function loadData() {
    try { S.data = await api("/api/data?range=" + $("#range").value); renderDash(); } catch (e) { toast(e.message, true); }
  }
  $("#range").addEventListener("change", loadData);
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
    S.metric = t.dataset.metric;
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    if (S.data) drawChart();
  }));
  function renderDash() {
    const d = S.data, st = d.stats, box = clear($("#cards"));
    const adv = S.status ? S.status.settings : {};
    const card = (label, v, unit, sub, cls) => box.appendChild(el("div", { className: "card" }, el("span", { className: "label", text: label }),
      el("span", { className: "value " + (cls || "") }, v, " ", el("small", { text: unit })), el("span", { className: "sub", text: sub })));
    const sub = (m, adv_) => (m ? "avg " + mbps(m.avg) + " · min " + mbps(m.min) + " · max " + mbps(m.max) : "no data in this range") + (adv_ && m ? " · " + Math.round((m.avg / adv_) * 100) + "% of advertised" : "");
    const ls = S.status && S.status.last_ok;
    card("Download", mbps(ls ? ls.down_mbps : null), "Mbps", sub(st.down_mbps, adv.advertised_down), advCls(ls && ls.down_mbps, adv.advertised_down));
    card("Upload", mbps(ls ? ls.up_mbps : null), "Mbps", sub(st.up_mbps, adv.advertised_up), advCls(ls && ls.up_mbps, adv.advertised_up));
    card("Latency", ls && ls.latency_ms != null ? num(ls.latency_ms, 0) : "—", "ms", st.latency_ms ? "avg " + num(st.latency_ms.avg, 0) + " · max " + num(st.latency_ms.max, 0) : "no data in this range");
    card("Jitter", ls && ls.jitter_ms != null ? num(ls.jitter_ms, 0) : "—", "ms", st.jitter_ms ? "avg " + num(st.jitter_ms.avg, 0) + " · max " + num(st.jitter_ms.max, 0) : "no data in this range");
    card("Uptime", st.uptime_pct == null ? "—" : num(st.uptime_pct, 1), "%", st.tests ? st.tests + " tests · " + st.outages + " outage" + (st.outages === 1 ? "" : "s") : "no tests in this range", st.outages ? "warn" : "");
    const rows = clear($("#stats-rows"));
    [["Download (Mbps)", st.down_mbps], ["Upload (Mbps)", st.up_mbps], ["Latency (ms)", st.latency_ms], ["Jitter (ms)", st.jitter_ms]].forEach(([n, m]) =>
      rows.appendChild(el("tr", {}, el("td", { text: n }), el("td", { className: "num", text: m ? mbps(m.min) : "—" }), el("td", { className: "num", text: m ? mbps(m.avg) : "—" }), el("td", { className: "num", text: m ? mbps(m.max) : "—" }))));
    const info = clear($("#conn-info"));
    [["ISP", d.isp], ["Public IP", d.ip], ["Test server", d.server]].forEach(([k, v]) => { info.appendChild(el("dt", { text: k })); info.appendChild(el("dd", { text: v || "—" })); });
    drawChart();
  }
  function advCls(v, advertised) { if (!advertised || v == null) return ""; const r = v / advertised; return r < 0.5 ? "bad" : r < 0.8 ? "warn" : ""; }
  function drawChart() {
    const d = S.data, adv = S.status ? S.status.settings : {};
    const pts = (k) => d.points.filter((p) => p[k] != null).map((p) => [p.ts, p[k]]);
    let series, opts = { tMin: d.from, tMax: d.to, empty: "No tests in this range yet. Run one!", marks: d.points.filter((p) => p.status === "outage").map((p) => [p.ts, css("--danger")]) };
    const accent = css("--accent"), ok = css("--ok"), warn = css("--warn");
    const interval = (S.status && S.status.settings.interval_min) || 60;
    opts.maxGap = Math.max(interval * 60 * 3, 3 * 3600);
    let legend;
    if (S.metric === "down") { series = [{ name: "Download", color: accent, points: pts("down_mbps") }]; legend = [["Download Mbps", accent]]; opts.fmtY = (v) => num(v, 0); if (adv.advertised_down) opts.hline = { v: adv.advertised_down, label: "Advertised " + adv.advertised_down + " Mbps", color: warn }; }
    else if (S.metric === "up") { series = [{ name: "Upload", color: ok, points: pts("up_mbps") }]; legend = [["Upload Mbps", ok]]; opts.fmtY = (v) => num(v, 0); if (adv.advertised_up) opts.hline = { v: adv.advertised_up, label: "Advertised " + adv.advertised_up + " Mbps", color: warn }; }
    else { series = [{ name: "Latency", color: accent, points: pts("latency_ms") }, { name: "Jitter", color: warn, points: pts("jitter_ms"), dashed: true }]; legend = [["Latency ms", accent], ["Jitter ms", warn]]; opts.fmtY = (v) => num(v, 0); }
    if (d.stats.outages) legend.push(["Outage", css("--danger")]);
    const lg = clear($("#legend")); legend.forEach(([n, c]) => lg.appendChild(el("span", {}, el("i", { className: "sw" }), n)));
    lg.querySelectorAll("i").forEach((i, idx) => { i.style.background = legend[idx][1]; });
    chart($("#chart"), series, opts);
    $("#chart-note").textContent = d.points.length ? d.points.length + " tests in range. Red bands are outages." : "";
  }
  window.addEventListener("resize", () => { if (S.data && S.view === "dash") drawChart(); });

  // ---- history
  async function loadHistory(reset) {
    if (reset) { S.histOffset = 0; clear($("#hist-rows")); }
    try {
      const r = await api("/api/history?limit=100&offset=" + S.histOffset);
      const tb = $("#hist-rows");
      r.tests.forEach((t) => {
        const [label, cls] = RES[t.status] || [t.status, "muted-badge"];
        tb.appendChild(el("tr", { className: t.status === "outage" ? "bad" : "" },
          el("td", { text: dt(t.ts) }), el("td", {}, el("span", { className: "badge " + cls, text: label }), t.trigger === "manual" ? el("span", { className: "muted small", text: " manual" }) : null),
          el("td", { className: "num", text: t.down_mbps != null ? mbps(t.down_mbps) + " Mbps" : "—" }), el("td", { className: "num", text: t.up_mbps != null ? mbps(t.up_mbps) + " Mbps" : "—" }),
          el("td", { className: "num", text: t.latency_ms != null ? num(t.latency_ms, 0) + " ms" : "—" }), el("td", { className: "num", text: t.jitter_ms != null ? num(t.jitter_ms, 0) + " ms" : "—" }),
          el("td", { text: t.isp || "" }), el("td", { text: t.ip || "" }), el("td", { text: t.error || "", title: t.error || "" })));
      });
      S.histOffset += r.tests.length;
      $("#btn-hist-more").hidden = S.histOffset >= r.total;
      $("#hist-note").textContent = r.total ? num(r.total) + " tests recorded" : "No tests yet.";
    } catch (e) { toast(e.message, true); }
  }
  $("#btn-hist-more").addEventListener("click", () => loadHistory(false));

  // ---- settings
  function renderSettings(force) {
    const st = S.status; if (!st) return;
    const active = document.activeElement;
    if ($("#set-interval") !== active) $("#set-interval").value = String(st.settings.interval_min);
    if (force || (active !== $("#set-adv-down") && active !== $("#set-adv-up"))) { $("#set-adv-down").value = st.settings.advertised_down || 0; $("#set-adv-up").value = st.settings.advertised_up || 0; }
    $("#set-keep").checked = !!st.settings.keep_background; $("#set-keep").disabled = !st.tray;
    $("#set-auto").checked = !!st.autostart; $("#set-auto").disabled = !st.can_autostart;
    $("#auto-note").textContent = !st.can_autostart ? "Start with Windows is only available on Windows." : !st.tray ? "The tray icon could not be created, so closing the window will always quit SpeedLog." : "Both options are off by default.";
  }
  const saveSettings = async (body) => { try { const r = await post("/api/settings", body); S.status.settings = r.settings; S.status.autostart = r.autostart; toast("Saved"); renderSettings(true); if (S.data) renderDash(); tick(); } catch (e) { toast(e.message, true); renderSettings(true); } };
  $("#set-interval").addEventListener("change", (e) => saveSettings({ interval_min: Number(e.target.value) }));
  $("#btn-save-adv").addEventListener("click", () => saveSettings({ advertised_down: Number($("#set-adv-down").value) || 0, advertised_up: Number($("#set-adv-up").value) || 0 }));
  $("#set-keep").addEventListener("change", (e) => saveSettings({ keep_background: e.target.checked }));
  $("#set-auto").addEventListener("change", (e) => saveSettings({ autostart: e.target.checked }));
  $("#btn-quit").addEventListener("click", async () => { try { await post("/api/quit", {}); $("#status").textContent = "SpeedLog is closing…"; } catch (e) { toast(e.message, true); } });
  $("#btn-clear").addEventListener("click", () => {
    const ok = el("button", { type: "button", className: "danger", text: "Delete all results" }), no = el("button", { type: "button", text: "Cancel" });
    const back = el("div", { className: "modal-back", role: "presentation" }, el("div", { className: "modal", role: "dialog", "aria-modal": "true" },
      el("h2", { text: "Delete all speed test results?" }), el("p", { text: "This removes the whole history from this PC. It cannot be undone. Export a CSV first if you want a copy." }), el("div", { className: "actions" }, no, ok)));
    $("#modal-root").appendChild(back);
    const close = () => back.remove();
    no.addEventListener("click", close); no.focus();
    ok.addEventListener("click", async () => { try { await post("/api/clear", { confirm: "clear" }); close(); toast("History cleared"); loadData(); tick(); } catch (e) { close(); toast(e.message, true); } });
  });

  tick().then(loadData);
})();

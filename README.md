# SpeedLog 1.0.0

Logs your internet speed on a schedule so you can see slow patterns and outages over time. No account needed.

- **How it works:** Python localhost-only server (127.0.0.1:8769, env `SPEEDLOG_PORT`), private Edge app window, random token on changes, strict CSP.
- **Test method:** Cloudflare's public speed endpoints (speed.cloudflare.com): latency (idle probes), jitter, download (4 streams, ~7 s, 200 MB cap) and upload (6 streams, ~6 s, 60 MB cap). Warm-up seconds are excluded. Upload is counted only on bytes the server confirmed. ISP and public IP come from Cloudflare's /meta. Each test uses roughly 50–250 MB depending on your speed.
- **Schedule:** Off / 15 / 30 / 60 (default) / 120 / 360 min, plus **Run now** and Cancel. Failed tests are logged as outages (status `outage` or `error`) and shown on the chart.
- **Views:** Dashboard (latest, day/week/month charts, min/avg/max), History table, Settings (advertised download/upload line on the chart, interval, start with Windows). CSV export.
- **Background:** closing the window keeps testing in the notification-area (tray) icon; use tray menu Open / Quit, or Quit in the app. Optional **Start with Windows** writes an HKCU Run entry (off by default; launches `--background`).
- **Data:** SQLite at `%APPDATA%\SpeedLog\speedlog.db`. Override with `SPEEDLOG_DATA`.
- **Tests:** `python tests/test_engine.py` (uses a fake Cloudflare server), `python tests/uitest.py`, `python tests/test_should_exit.py`.

## Bugs / edge cases caught in review
1. Upload speed was over-counted from OS socket buffers (bytes "sent" but not received). Now counted from server-confirmed POSTs with adaptive sizes.
2. A fully failed upload produced status ok with an absurd Mbps. It now raises and is logged as an error.
3. Lost keep-alive connections inflated jitter. Reconnect plus untimed warm-up probe, 4 s ping timeout.
4. `conn.sock` could be None after a reset (crash). Guarded.
5. Tray icon crashed/misbehaved on 64-bit Windows because ctypes truncated handles. Set explicit argtypes/restype.
6. Cloudflare rejects requests without Origin/Referer/browser User-Agent and non-text/plain upload bodies; all set.

## Limitations
Measures to Cloudflare's nearest edge, so it can differ from other speed tests. Needs the PC awake (sleep pauses tests). Not code-signed.

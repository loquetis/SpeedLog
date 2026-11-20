"""Tiny fake of Cloudflare's speed endpoints for tests: /__down?bytes=N, POST /__up, /meta. Throttled per connection."""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
DOWN_BPS = 2_500_000      # per connection (x4 streams = ~80 Mbit/s)
UP_BPS = 500_000        # per connection (x6 streams = ~24 Mbit/s)
mode = {"fail_up": False, "fail_down": False, "drop": 0}
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/meta"):
            b = json.dumps({"clientIp": "203.0.113.7", "asOrganization": "Test ISP, Inc.", "colo": {"city": "Testville", "iata": "TST"}}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        if mode["fail_down"] and "bytes=0" not in self.path:
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
        n = int(self.path.split("bytes=")[1])
        self.send_response(200); self.send_header("Content-Length", str(n)); self.end_headers()
        sent = 0; chunk = b"x" * 65536; t0 = time.time()
        try:
            while sent < n:
                k = min(len(chunk), n - sent); self.wfile.write(chunk[:k]); sent += k
                ahead = sent / DOWN_BPS - (time.time() - t0)
                if ahead > 0: time.sleep(ahead)
        except OSError: pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); got = 0; t0 = time.time()
        while got < n:
            b = self.rfile.read(min(65536, n - got))
            if not b: return
            got += len(b)
            ahead = got / UP_BPS - (time.time() - t0)
            if ahead > 0: time.sleep(ahead)
        code = 500 if mode["fail_up"] else 200
        self.send_response(code); self.send_header("Content-Length", "0"); self.end_headers()
def serve(port=0):
    import socket
    class S(ThreadingHTTPServer):
        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)   # like a real link: no giant receive buffer hiding the throttle
            super().server_bind()
    s = S(("127.0.0.1", port), H); s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start(); return s
if __name__ == "__main__":
    s = serve(int(sys.argv[1])); print("fake up", s.server_address, flush=True); time.sleep(3600)

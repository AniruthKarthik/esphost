import threading
import time
import uuid
from flask import Flask, request, Response, stream_with_context
import requests as req_lib


QUEUE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Queue — ESPHost</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0d0d0d;
    color: #e0e0e0;
    font-family: 'Courier New', monospace;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
  }}
  .card {{
    border: 1px solid #1e1e1e;
    border-radius: 12px;
    padding: 48px 56px;
    text-align: center;
    background: #111;
    max-width: 420px;
    width: 90%;
  }}
  .title {{
    font-size: 11px;
    letter-spacing: 4px;
    color: #444;
    margin-bottom: 32px;
  }}
  .position {{
    font-size: 56px;
    font-weight: 900;
    color: #00ff9d;
    line-height: 1;
  }}
  .of {{ font-size: 14px; color: #555; margin: 8px 0 24px; }}
  .msg {{
    font-size: 13px;
    color: #888;
    line-height: 1.8;
    letter-spacing: 1px;
  }}
  .dot {{
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #00ff9d;
    animation: pulse 1.2s ease-in-out infinite;
    margin: 24px auto 0;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 0.2; transform: scale(0.8); }}
    50%       {{ opacity: 1;   transform: scale(1.2); }}
  }}
</style>
</head>
<body>
<div class="card">
  <div class="title">ESPHOST  ·  QUEUE</div>
  <div class="position" id="pos">{position}</div>
  <div class="of">/ {total} waiting</div>
  <div class="msg">Kindly wait.<br>You will be admitted automatically.</div>
  <div class="dot"></div>
</div>
<script>
  const evtSource = new EventSource('/esphost-queue-status?id={client_id}');
  evtSource.onmessage = function(e) {{
    const data = JSON.parse(e.data);
    if (data.admitted) {{
      evtSource.close();
      window.location.reload();
      return;
    }}
    document.getElementById('pos').textContent = data.position;
  }};
</script>
</body>
</html>
"""


class QueueProxy:

    def __init__(self, target_url: str, max_slots: int = 3, port: int = 8080):
        self.target_url  = target_url.rstrip("/")
        self.max_slots   = max_slots
        self.port        = port

        self._lock        = threading.Lock()
        self._active      = {}   # client_id → expiry timestamp
        self._queue       = []   # list of client_ids in order
        self._sse_clients = {}   # client_id → queue (SSE events)
        self._slot_timeout = 30  # seconds of inactivity before slot freed

        self._app = Flask(__name__)
        self._register_routes()

        # Cleanup thread
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    # ── Routes ────────────────────────────────────────────────────────────────

    def _register_routes(self):
        app = self._app

        @app.route("/esphost-queue-status")
        def queue_status():
            client_id = request.args.get("id", "")
            return Response(
                stream_with_context(self._sse_stream(client_id)),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control":   "no-cache",
                    "X-Accel-Buffering": "no",
                }
            )

        @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        @app.route("/<path:path>",             methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        def proxy(path):
            # Heartbeat — keep slot alive
            client_id = request.cookies.get("esphost_id")
            if client_id and client_id in self._active:
                with self._lock:
                    self._active[client_id] = time.time() + self._slot_timeout
                return self._forward(path)

            # New visitor
            client_id = str(uuid.uuid4())[:8]

            with self._lock:
                if len(self._active) < self.max_slots:
                    self._active[client_id] = time.time() + self._slot_timeout
                    resp = self._forward(path)
                    resp = Response(resp.get_data(), status=resp.status_code, headers=dict(resp.headers))
                    resp.set_cookie("esphost_id", client_id, max_age=3600, httponly=True)
                    return resp

                # Queue visitor
                if client_id not in self._queue:
                    self._queue.append(client_id)

            position = self._queue.index(client_id) + 1
            total    = len(self._queue)

            html = QUEUE_HTML.format(
                position=position,
                total=total,
                client_id=client_id,
            )
            resp = Response(html, status=200, mimetype="text/html")
            resp.set_cookie("esphost_id", client_id, max_age=3600, httponly=True)
            return resp

    # ── SSE stream ────────────────────────────────────────────────────────────

    def _sse_stream(self, client_id: str):
        import queue as q_mod
        evt_queue = q_mod.Queue()

        with self._lock:
            self._sse_clients[client_id] = evt_queue

        try:
            while True:
                try:
                    event = evt_queue.get(timeout=20)
                    yield f"data: {event}\n\n"
                    if '"admitted":true' in event:
                        break
                except Exception:
                    # heartbeat keep-alive
                    yield ": ping\n\n"
        finally:
            with self._lock:
                self._sse_clients.pop(client_id, None)

    # ── Proxy forward ─────────────────────────────────────────────────────────

    def _forward(self, path: str) -> Response:
        url = f"{self.target_url}/{path}"
        try:
            r = req_lib.request(
                method=request.method,
                url=url,
                headers={k: v for k, v in request.headers if k.lower() != "host"},
                data=request.get_data(),
                params=request.args,
                allow_redirects=False,
                timeout=10,
            )
            return Response(r.content, status=r.status_code, headers=dict(r.headers))
        except Exception as e:
            return Response(f"ESP32 unreachable: {e}", status=503)

    # ── Slot cleanup ──────────────────────────────────────────────────────────

    def _cleanup_loop(self):
        import json
        while True:
            time.sleep(5)
            now = time.time()
            with self._lock:
                expired = [cid for cid, exp in self._active.items() if now > exp]
                for cid in expired:
                    del self._active[cid]

                # Admit next in queue
                while self._queue and len(self._active) < self.max_slots:
                    next_id = self._queue.pop(0)
                    self._active[next_id] = now + self._slot_timeout

                    # Notify via SSE
                    sse_q = self._sse_clients.get(next_id)
                    if sse_q:
                        sse_q.put(json.dumps({"admitted": True, "position": 0}))

                # Update queue positions for all waiting
                for i, cid in enumerate(self._queue):
                    sse_q = self._sse_clients.get(cid)
                    if sse_q:
                        try:
                            sse_q.put_nowait(json.dumps({
                                "admitted": False,
                                "position": i + 1,
                                "total":    len(self._queue),
                            }))
                        except Exception:
                            pass

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self):
        self._app.run(host="0.0.0.0", port=self.port, threaded=True)

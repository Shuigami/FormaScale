"""FormaScale live capture server.

Serves:
  - /phone  — page téléphone (caméra + keyframes)
  - /desk   — double vue PC (flux live + reconstruction)
  - APIs HTTP/SSE pour preview, keyframes et mises à jour SfM

HTTPS is required for iPhone camera access on the LAN.
Run from the FormaScale venv (WSL) so pycolmap can rebuild the sparse cloud.

  python live/server.py
  python live/server.py --host 0.0.0.0 --port 8443
"""
from __future__ import annotations

import argparse
import json
import queue
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
LIVE_DIR = Path(__file__).resolve().parent
STATIC = LIVE_DIR / 'static'
CERT_DIR = LIVE_DIR / 'certs'


class LiveSession:
    def __init__(self, name: str, threads: int):
        stamp = time.strftime('%Y%m%d_%H%M%S')
        self.name = name or f'live_{stamp}'
        self.dir = ROOT / 'captures' / self.name
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / 'prepared' / 'images').mkdir(parents=True, exist_ok=True)
        self.preview = None
        self.preview_ts = 0.0
        self.keyframe_index = 0
        self.phone_connected = False
        self.auto_interval_ms = 2200
        self.lock = threading.Lock()
        self.subscribers: list[queue.Queue] = []
        self.reconstruction = {
            'type': 'reconstruction',
            'status': {
                'state': 'idle',
                'keyframes': 0,
                'points': 0,
                'registered': 0,
                'message': 'En attente',
                'error': None,
                'seconds': None,
            },
            'points': [],
            'cameras': [],
            'images': [],
        }
        from sfm_worker import LiveSfmWorker

        self.worker = LiveSfmWorker(self.dir, on_update=self._on_reconstruction, threads=threads)
        self.worker.start()

    def _on_reconstruction(self, payload):
        with self.lock:
            self.reconstruction = payload
        self.broadcast(payload)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=32)
        with self.lock:
            self.subscribers.append(q)
            snapshot = dict(self.reconstruction)
        q.put(snapshot)
        q.put(self._meta_event())
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def broadcast(self, event: dict):
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def set_preview(self, data: bytes):
        with self.lock:
            self.preview = data
            self.preview_ts = time.time()
            self.phone_connected = True
        self.broadcast({'type': 'preview', 'ts': self.preview_ts, 'bytes': len(data)})

    def save_keyframe(self, data: bytes) -> dict:
        with self.lock:
            self.keyframe_index += 1
            index = self.keyframe_index
        path = self.dir / 'prepared' / 'images' / f'kf_{index:04d}.jpg'
        path.write_bytes(data)
        self.worker.notify_new_keyframe()
        event = {
            'type': 'keyframe',
            'index': index,
            'name': path.name,
            'keyframes': index,
        }
        self.broadcast(event)
        self.broadcast(self._meta_event())
        return event

    def clear_keyframes(self) -> dict:
        removed = self.worker.clear_keyframes()
        with self.lock:
            self.keyframe_index = 0
            self.reconstruction = {
                'type': 'reconstruction',
                'status': dict(self.worker.status),
                'points': [],
                'cameras': [],
                'images': [],
            }
        event = {
            'type': 'cleared',
            'removed': removed,
            'keyframes': 0,
        }
        self.broadcast(event)
        self.broadcast(self.reconstruction)
        self.broadcast(self._meta_event())
        return event

    def set_auto_interval_ms(self, value_ms: int) -> dict:
        value = max(500, min(8000, int(value_ms)))
        with self.lock:
            self.auto_interval_ms = value
        event = {'type': 'settings', 'auto_interval_ms': value}
        self.broadcast(event)
        self.broadcast(self._meta_event())
        return event

    def _meta_event(self) -> dict:
        with self.lock:
            return {
                'type': 'meta',
                'session': self.name,
                'phone_connected': self.phone_connected,
                'preview_age_ms': int((time.time() - self.preview_ts) * 1000) if self.preview else None,
                'keyframes': self.keyframe_index,
                'auto_interval_ms': self.auto_interval_ms,
                'status': self.reconstruction.get('status'),
            }

    def state(self) -> dict:
        with self.lock:
            return {
                'session': self.name,
                'phone_connected': self.phone_connected,
                'preview_age_ms': int((time.time() - self.preview_ts) * 1000) if self.preview else None,
                'keyframes': self.keyframe_index,
                'auto_interval_ms': self.auto_interval_ms,
                'reconstruction': self.reconstruction,
            }


SESSION: LiveSession | None = None


MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.ico': 'image/x-icon',
}


class Handler(BaseHTTPRequestHandler):
    server_version = 'FormaScaleLive/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write(f'[live] {self.address_string()} {fmt % args}\n')

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', '/desk', '/desk.html'):
            return self._file(STATIC / 'desk.html')
        if path in ('/phone', '/phone.html'):
            return self._file(STATIC / 'phone.html')
        if path.startswith('/static/'):
            return self._file(STATIC / path[len('/static/'):])
        if path == '/api/state':
            return self._json(SESSION.state())
        if path == '/api/preview.jpg':
            return self._preview()
        if path.startswith('/api/frame/'):
            return self._frame(path[len('/api/frame/'):])
        if path == '/api/events':
            return self._events()
        if path == '/api/info':
            return self._json({
                'session': SESSION.name,
                'https': isinstance(self.connection, ssl.SSLSocket),
                'hint': 'Ouvre /phone sur le téléphone et /desk sur le PC',
            })
        self.send_error(404, 'Not found')

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', '0'))
        if length > 12_000_000:
            self.send_error(413, 'Payload too large')
            return
        body = self.rfile.read(length) if length else b''
        if path == '/api/preview':
            if not body:
                self.send_error(400, 'Empty body')
                return
            SESSION.set_preview(body)
            return self._json({'ok': True, 'bytes': len(body)})
        if path == '/api/keyframe':
            if not body:
                self.send_error(400, 'Empty body')
                return
            event = SESSION.save_keyframe(body)
            return self._json({'ok': True, **event})
        if path == '/api/reconstruct':
            SESSION.worker.notify_new_keyframe(force=True)
            return self._json({'ok': True, 'message': 'Reconstruction demandée'})
        if path == '/api/dense':
            result = SESSION.worker.request_dense()
            code = 200 if result.get('ok') else 409
            return self._json(result, code=code)
        if path == '/api/settings':
            try:
                payload = json.loads(body.decode('utf-8') or '{}')
            except json.JSONDecodeError:
                self.send_error(400, 'JSON invalide')
                return
            if 'auto_interval_ms' not in payload:
                self.send_error(400, 'auto_interval_ms requis')
                return
            event = SESSION.set_auto_interval_ms(payload['auto_interval_ms'])
            return self._json({'ok': True, **event})
        if path == '/api/clear':
            event = SESSION.clear_keyframes()
            return self._json({'ok': True, **event})
        self.send_error(404, 'Not found')

    def _file(self, path: Path):
        path = path.resolve()
        if not str(path).startswith(str(STATIC.resolve())) or not path.is_file():
            self.send_error(404, 'Not found')
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', MIME.get(path.suffix.lower(), 'application/octet-stream'))
        self.send_header('Content-Length', str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict, code: int = 200):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _preview(self):
        with SESSION.lock:
            data = SESSION.preview
        if not data:
            self.send_error(404, 'No preview yet')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _frame(self, name: str):
        name = Path(unquote(name)).name
        if not name or name.startswith('.'):
            self.send_error(400, 'Bad frame name')
            return
        candidates = [
            SESSION.worker.subset_dir / name,
            SESSION.dir / 'prepared' / 'images' / name,
        ]
        path = next((item for item in candidates if item.is_file()), None)
        if path is None:
            self.send_error(404, 'Frame not found')
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _events(self):
        q = SESSION.subscribe()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Connection', 'keep-alive')
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(b': connected\n\n')
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                    payload = json.dumps(event).encode('utf-8')
                    self.wfile.write(b'data: ' + payload + b'\n\n')
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            SESSION.unsubscribe(q)


def local_ips() -> list[str]:
    """Prefer Windows LAN IPs when running under WSL (needed for iPhone URLs/certs)."""
    ips: list[str] = []

    def add(ip: str):
        if not ip or ip.startswith('127.') or ip.startswith('169.254.'):
            return
        if ip not in ips:
            ips.append(ip)

    # Windows host IPs (reachable by an iPhone on the same LAN / hotspot).
    try:
        ps = subprocess.run(
            [
                'powershell.exe', '-NoProfile', '-Command',
                "Get-NetIPAddress -AddressFamily IPv4 "
                "| Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } "
                "| Select-Object -ExpandProperty IPAddress",
            ],
            check=False, capture_output=True, text=True, timeout=8,
        )
        for line in (ps.stdout or '').splitlines():
            add(line.strip())
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    try:
        import socket
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return ips


def ensure_certs(cert_dir: Path, force: bool = False) -> tuple[Path, Path]:
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert = cert_dir / 'cert.pem'
    key = cert_dir / 'key.pem'
    if force:
        for path in (cert, key):
            if path.exists():
                path.unlink()
    if cert.exists() and key.exists():
        return cert, key

    # iPhone Safari aborts with "connection was lost" if the cert SAN
    # does not include the exact LAN IP typed in the URL.
    san_parts = ['DNS:localhost', 'IP:127.0.0.1']
    for ip in local_ips():
        san_parts.append(f'IP:{ip}')
    # Always include common hotspot gateway used by Windows Mobile Hotspot.
    if 'IP:192.168.137.1' not in san_parts:
        san_parts.append('IP:192.168.137.1')
    san = ','.join(san_parts)
    print(f'  SAN certificat : {san}', flush=True)

    subject = '/CN=FormaScale Live/O=FormaScale/C=FR'
    cmd = [
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
        '-keyout', str(key), '-out', str(cert),
        '-days', '825', '-nodes', '-subj', subject,
        '-addext', f'subjectAltName={san}',
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return cert, key
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = ''
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or b'').decode('utf-8', errors='replace')
        raise SystemExit(
            'Impossible de creer un certificat HTTPS avec openssl.\n'
            f'Detail: {detail or exc}'
        ) from exc


def main():
    global SESSION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--http', action='store_true', help='HTTP only (camera may fail on iPhone)')
    parser.add_argument('--regen-cert', action='store_true', help='Regenerate self-signed HTTPS cert')
    parser.add_argument('--session', default='', help='Capture folder name under captures/')
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()

    sys.path.insert(0, str(LIVE_DIR))
    SESSION = LiveSession(args.session, threads=args.threads)

    scheme = 'http'
    cert = key = None
    if not args.http:
        print('Generation du certificat HTTPS…', flush=True)
        cert, key = ensure_certs(CERT_DIR, force=args.regen_cert)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert), keyfile=str(key))
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        scheme = 'https'

    ips = local_ips()
    print('FormaScale Live', flush=True)
    print(f'  session : captures/{SESSION.name}', flush=True)
    print(f'  desk    : {scheme}://127.0.0.1:{args.port}/desk', flush=True)
    for ip in ips:
        # Skip pure WSL virtual IPs if Windows LAN IPs are available.
        if ip.startswith('172.') and any(not x.startswith('172.') for x in ips):
            continue
        print(f'  iPhone  : {scheme}://{ip}:{args.port}/phone', flush=True)
    if scheme == 'https':
        print('  note    : sur iPhone, accepte l’avertissement certificat', flush=True)
    print('  note    : si Safari “connection was lost”, lance live/open_lan.ps1 en Admin', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nArrêt', flush=True)
    finally:
        SESSION.worker.stop()
        httpd.server_close()


if __name__ == '__main__':
    main()

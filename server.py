#!/usr/bin/env python3
"""
Xumbr3ga Multi Chat — Servidor Local
Uso: execute start-chat.bat  (ou: python server.py)
OBS Browser Source: http://localhost:8080/xumbr3ga-multichat.html
"""
import http.server
import urllib.request
import urllib.error
import os

PORT = 8080
DIR  = os.path.dirname(os.path.abspath(__file__))

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript',
    '.css':  'text/css',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.ico':  'image/x-icon',
    '.svg':  'image/svg+xml',
}

class Handler(http.server.BaseHTTPRequestHandler):

    # ── CORS preflight ────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    # ── GET: arquivo local ou proxy YouTube ───────────────────────────────────
    def do_GET(self):
        if self.path.startswith('/yt-proxy/'):
            self._proxy('GET', self.path[len('/yt-proxy/'):])
        else:
            self._serve_file()

    # ── POST: proxy YouTube ───────────────────────────────────────────────────
    def do_POST(self):
        if self.path.startswith('/yt-proxy/'):
            length = int(self.headers.get('Content-Length', 0))
            body   = self.rfile.read(length)
            self._proxy('POST', self.path[len('/yt-proxy/'):], body)
        else:
            self.send_error(404)

    # ── Proxy para youtube.com ────────────────────────────────────────────────
    def _proxy(self, method, yt_path, body=None):
        url = 'https://www.youtube.com/' + yt_path
        print(f'[proxy] {method} /{yt_path[:80]}', flush=True)
        try:
            req = urllib.request.Request(
                url, data=body, method=method,
                headers={
                    'Content-Type':    self.headers.get('Content-Type', 'application/json'),
                    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Cookie':          'CONSENT=YES+1; SOCS=CAESEwgDEgk1NzM4MTkzMjYaAmVuIAE=',
                }
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                ct   = resp.headers.get('Content-Type', 'application/octet-stream')
            print(f'[proxy] resposta: {len(data)} bytes  ct={ct[:40]}', flush=True)

            self.send_response(200)
            self._cors_headers()
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        except urllib.error.HTTPError as e:
            self.send_error(e.code, 'YouTube: ' + str(e.reason))
        except Exception as e:
            self.send_error(502, str(e))

    # ── Servir arquivo local ──────────────────────────────────────────────────
    def _serve_file(self):
        path  = self.path.split('?')[0].lstrip('/')
        fpath = os.path.join(DIR, path or 'xumbr3ga-multichat.html')
        fpath = os.path.normpath(fpath)

        # Segurança: não sair da pasta
        if not fpath.startswith(DIR):
            self.send_error(403); return

        if not os.path.isfile(fpath):
            self.send_error(404); return

        ext  = os.path.splitext(fpath)[1].lower()
        mime = MIME.get(ext, 'application/octet-stream')

        with open(fpath, 'rb') as f:
            data = f.read()

        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Headers CORS ──────────────────────────────────────────────────────────
    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, fmt, *args):
        pass  # sem logs no terminal (só o proxy abaixo)


if __name__ == '__main__':
    os.chdir(DIR)
    server = http.server.HTTPServer(('localhost', PORT), Handler)
    print()
    print('  ╔══════════════════════════════════════════════╗')
    print('  ║     Xumbr3ga Multi Chat — Servidor ativo     ║')
    print('  ╠══════════════════════════════════════════════╣')
    print(f' ║  No OBS, use como URL do Browser Source:     ║')
    print(f' ║  http://localhost:{PORT}/xumbr3ga-multichat.html  ║')
    print('  ╠══════════════════════════════════════════════╣')
    print('  ║  Mantenha esta janela aberta durante a live  ║')
    print('  ║  Feche esta janela para encerrar o servidor  ║')
    print('  ╚══════════════════════════════════════════════╝')
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('Servidor encerrado.')

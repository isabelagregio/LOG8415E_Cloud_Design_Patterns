#!/usr/bin/env python3
"""HTTP forwarding for Gatekeeper (8000) and Trusted Host (8001)."""
import hmac
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROLE, UPSTREAM, PORT = sys.argv[1], sys.argv[2].rstrip('/'), int(sys.argv[3])
if ROLE not in ('gatekeeper', 'trusted'):
    raise SystemExit('role must be gatekeeper or trusted')
TOKEN = os.environ.get('GATEKEEPER_TOKEN')
if ROLE == 'gatekeeper' and not TOKEN:
    raise SystemExit('GATEKEEPER_TOKEN is required')


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def forward(self, method, path, body=None):
        request = Request(UPSTREAM + path, data=body, method=method)
        if body is not None:
            request.add_header('Content-Type', 'application/json')
        try:
            with urlopen(request, timeout=25) as result:
                self.respond(result.status, result.read())
        except HTTPError as error:
            self.respond(error.code, error.read())
        except (URLError, TimeoutError, OSError):
            self.respond(502, b'{"error":"upstream unavailable"}')

    def do_GET(self):
        if self.path != '/health':
            return self.respond(404, b'{"error":"not found"}')
        self.forward('GET', '/health')

    def do_POST(self):
        if self.path != '/query':
            return self.respond(404, b'{"error":"not found"}')
        if ROLE == 'gatekeeper':
            supplied = self.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied, 'Bearer ' + TOKEN):
                return self.respond(401, b'{"error":"unauthorized"}')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 65536:
                raise ValueError('invalid request size')
            body = self.rfile.read(size)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError('JSON object required')
            sql = payload.get('sql')
            if not isinstance(sql, str) or not re.match(r'^\s*(SELECT|INSERT|UPDATE|DELETE)\b', sql, re.I):
                raise ValueError('invalid SQL operation')
            if ';' in sql:
                raise ValueError('one SQL statement required')
            if not isinstance(payload.get('params', []), (list, dict)):
                raise ValueError('invalid SQL parameters')
            if payload.get('policy', 'direct') not in ('direct', 'random', 'latency'):
                raise ValueError('invalid read policy')
            self.forward('POST', '/query', body)
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self.respond(400, json.dumps({'error': str(error)}).encode())


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()

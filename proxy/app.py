#!/usr/bin/env python3
"""Small HTTP proxy for the Sakila MySQL cluster."""
import json
import random
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pymysql

CONFIG = json.loads(Path('/opt/log8415e/proxy.json').read_text())
HOSTS = CONFIG['hosts']


def choose_host(policy):
    if policy == 'direct':
        return 'primary'
    if policy == 'random':
        return random.choice(['replica1', 'replica2'])
    if policy == 'latency':
        delays = {}
        for name in ('replica1', 'replica2'):
            start = time.monotonic()
            try:
                connection = pymysql.connect(
                    host=HOSTS[name], user=CONFIG['user'],
                    password=CONFIG['password'], database='sakila',
                    connect_timeout=2, read_timeout=2,
                )
                try:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT 1')
                finally:
                    connection.close()
                delays[name] = time.monotonic() - start
            except (pymysql.MySQLError, OSError):
                continue
        if not delays:
            raise ConnectionError('Neither replica is reachable')
        return min(delays, key=delays.get)
    raise ValueError('policy must be direct, random or latency')


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data):
        body = json.dumps(data, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/health':
            self.respond(200, {'status': 'ok'})
        else:
            self.respond(404, {'error': 'not found'})

    def do_POST(self):
        if self.path != '/query':
            return self.respond(404, {'error': 'not found'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 65536:
                return self.respond(400, {'error': 'invalid request size'})
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('JSON object required')
            sql = payload.get('sql')
            params = payload.get('params', [])
            if not isinstance(sql, str) or not isinstance(params, (list, dict)):
                raise ValueError('sql must be a string and params a list or object')
            sql = sql.strip()
            if not sql or ';' in sql:
                raise ValueError('one SQL statement required')
            verb = re.match(r'^([A-Za-z]+)\b', sql)
            verb = verb.group(1).upper() if verb else ''
            if verb == 'SELECT':
                node = choose_host(payload.get('policy', 'direct'))
            elif verb in ('INSERT', 'UPDATE', 'DELETE'):
                node = 'primary'
            else:
                raise ValueError('only SELECT, INSERT, UPDATE and DELETE are supported')
            started = time.monotonic()
            connection = pymysql.connect(
                host=HOSTS[node], port=3306, user=CONFIG['user'],
                password=CONFIG['password'], database='sakila',
                connect_timeout=5, read_timeout=15, write_timeout=15,
                autocommit=True, cursorclass=pymysql.cursors.DictCursor,
            )
            try:
                with connection.cursor() as cursor:
                    affected = cursor.execute(sql, params)
                    rows = cursor.fetchall() if verb == 'SELECT' else []
            finally:
                connection.close()
            self.respond(200, {
                'node': node, 'rows': rows, 'affected_rows': affected,
                'duration_ms': round((time.monotonic() - started) * 1000, 2),
            })
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.respond(400, {'error': str(error)})
        except (pymysql.MySQLError, OSError, ConnectionError) as error:
            self.respond(502, {'error': str(error)})


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8002), Handler).serve_forever()

#!/usr/bin/env python3
"""Benchmark the public Gatekeeper and publish results to CloudWatch."""
import argparse
import asyncio
import csv
import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
POLICIES = ('direct', 'random', 'latency')
TOKEN_FILE = ROOT / '.secrets' / 'gatekeeper_token'
AUTH_TOKEN = os.environ.get('GATEKEEPER_TOKEN') or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else None)


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p / 100
    low = math.floor(pos)
    high = math.ceil(pos)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low), 2)


def request(url, payload):
    body = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if AUTH_TOKEN:
        headers['Authorization'] = 'Bearer ' + AUTH_TOKEN
    req = urllib.request.Request(url + '/query', data=body, headers=headers, method='POST')
    started_at_ms = int(time.time() * 1000)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.load(resp)
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        result = {'error': exc.read().decode('utf-8', 'replace')[:200]}
    except (OSError, ValueError) as exc:
        status = 0
        result = {'error': str(exc)[:200]}
    return {'timestamp_ms': started_at_ms, 'status': status, 'node': result.get('node', ''),
            'latency_ms': round((time.monotonic() - start) * 1000, 2),
            'error': result.get('error', '') if status != 200 else ''}


async def load(url, policy, operation, count, concurrency, executor):
    semaphore = asyncio.Semaphore(concurrency)
    loop = asyncio.get_running_loop()

    async def one(i):
        if operation == 'write':
            # Dedicated Sakila table; fixed IDs make reruns safe.
            sql = ('INSERT INTO replication_probe (id, note) VALUES (%s, %s) '
                   'ON DUPLICATE KEY UPDATE note=VALUES(note)')
            payload = {'sql': sql, 'params': [10000 + POLICIES.index(policy) * 100000 + i,
                                             'bench_' + policy], 'policy': policy}
        else:
            payload = {'sql': 'SELECT COUNT(*) AS films FROM film', 'policy': policy}
        async with semaphore:
            result = await loop.run_in_executor(executor, request, url, payload)
        result.update(policy=policy, operation=operation, request_number=i)
        return result

    started = time.monotonic()
    records = await asyncio.gather(*(one(i) for i in range(count)))
    duration = time.monotonic() - started
    return records, duration


def summary(records, seconds):
    successful = [r['latency_ms'] for r in records if r['status'] == 200]
    failures = sum(r['status'] != 200 for r in records)
    return {'requests': len(records), 'errors': failures,
            'error_rate_pct': round(100 * failures / len(records), 2) if records else 0,
            'throughput_req_s': round(len(records) / seconds, 2) if seconds else 0,
            'p50_ms': percentile(successful, 50), 'p95_ms': percentile(successful, 95),
            'p99_ms': percentile(successful, 99),
            'nodes': dict(sorted(Counter(r['node'] for r in records if r['node']).items()))}


def replica_lag(state):
    """Read Seconds_Behind_Source over the existing Gatekeeper SSH jump."""
    if not state:
        return {}
    key = str(ROOT / '.state' / 'id_ed25519')
    jump = 'ubuntu@' + state['ips']['gatekeeper']['public']
    jump_command = 'ssh -i {} -o BatchMode=yes -o StrictHostKeyChecking=no -W %h:%p {}'.format(key, jump)
    lag = {}
    for name in ('replica1', 'replica2'):
        cmd = ['ssh', '-i', key, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
               '-o', 'ConnectTimeout=7', '-o', 'ProxyCommand=' + jump_command,
               'ubuntu@' + state['ips'][name]['private'],
               "sudo mysql -e 'SHOW REPLICA STATUS\\G'"]
        try:
            output = subprocess.run(cmd, text=True, capture_output=True, timeout=20, check=True).stdout
            lines = (line.strip().split(':', 1) for line in output.splitlines() if ':' in line)
            fields = {a.strip(): b.strip() for a, b in lines}
            value = fields.get('Seconds_Behind_Source', 'NULL')
            lag[name] = int(value) if value.isdigit() else None
        except (OSError, subprocess.SubprocessError):
            lag[name] = None
    return lag


def cloudwatch(region, stream, policy, records, metrics, lag):
    """Store benchmark request logs and aggregate CloudWatch metrics."""
    group = '/log8415e/benchmark'
    logs = boto3.client('logs', region_name=region)
    from botocore.exceptions import ClientError
    for create, kwargs in (('create_log_group', {'logGroupName': group}),
                           ('create_log_stream', {'logGroupName': group, 'logStreamName': stream})):
        try:
            getattr(logs, create)(**kwargs)
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'ResourceAlreadyExistsException':
                raise
    ordered_records = sorted(records, key=lambda r: r['timestamp_ms'])
    for offset in range(0, len(ordered_records), 1000):
        events = [{'timestamp': r['timestamp_ms'],
                   'message': json.dumps(r, separators=(',', ':'))}
                  for r in ordered_records[offset:offset + 1000]]
        logs.put_log_events(logGroupName=group, logStreamName=stream, logEvents=events)

    data = []
    for operation in ('write', 'read'):
        result = metrics[operation]
        dims = [{'Name': 'Policy', 'Value': policy}, {'Name': 'Operation', 'Value': operation}]
        for name, value, unit in (
            ('Throughput', result['throughput_req_s'], 'Count/Second'),
            ('ErrorRate', result['error_rate_pct'], 'Percent'),
            ('P50Latency', result['p50_ms'], 'Milliseconds'),
            ('P95Latency', result['p95_ms'], 'Milliseconds'),
            ('P99Latency', result['p99_ms'], 'Milliseconds'),
        ):
            if value is not None:
                data.append({'MetricName': name, 'Value': value, 'Unit': unit, 'Dimensions': dims})
        for node, count in result['nodes'].items():
            data.append({'MetricName': 'RequestsByNode', 'Value': count, 'Unit': 'Count',
                         'Dimensions': dims + [{'Name': 'Node', 'Value': node}]})
    for node, seconds in lag.items():
        if seconds is not None:
            data.append({'MetricName': 'ReplicationLag', 'Value': seconds, 'Unit': 'Seconds',
                         'Dimensions': [{'Name': 'Policy', 'Value': policy},
                                        {'Name': 'Node', 'Value': node}]})
    cw = boto3.client('cloudwatch', region_name=region)
    for start in range(0, len(data), 20):
        cw.put_metric_data(Namespace='LOG8415E/Benchmark', MetricData=data[start:start + 20])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=int, default=1000, help='reads and writes per policy')
    parser.add_argument('--concurrency', type=int, default=5)
    parser.add_argument('--url', help='Gatekeeper URL (defaults to current AWS state)')
    parser.add_argument('--no-cloudwatch', action='store_true', help='local tests only')
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error('requests and concurrency must be positive')
    if not AUTH_TOKEN:
        parser.error('Gatekeeper token not found; run setup_gateway.sh first')
    state_file = ROOT / '.state' / 'aws.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else None
    if not args.url and not state:
        parser.error('missing .state/aws.json; supply --url for a local test')
    url = (args.url or ('http://' + state['ips']['gatekeeper']['public'] + ':8000')).rstrip('/')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = ROOT / 'results' / run_id
    output.mkdir(parents=True, exist_ok=True)
    all_results = {'run_id': run_id, 'requests_per_operation': args.requests,
                   'concurrency': args.concurrency, 'policies': {}}
    region = state['region'] if state else None

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        for policy in POLICIES:
            print('Testing {}...'.format(policy), flush=True)
            # Warm up the whole request path; do not include warm-up in measurements.
            for _ in range(20):
                response = request(url, {'sql': 'SELECT COUNT(*) AS films FROM film', 'policy': policy})
                if response['status'] != 200:
                    raise RuntimeError('Warm-up failed for {}: {}'.format(policy, response['error']))
            metrics = {}
            records = []
            lag_after_writes = {}
            for operation in ('write', 'read'):
                new_records, seconds = asyncio.run(load(url, policy, operation,
                                                         args.requests, args.concurrency, executor))
                records.extend(new_records)
                metrics[operation] = summary(new_records, seconds)
                if operation == 'write':
                    lag_after_writes = replica_lag(state)
                print('  {}: {} req/s, p95={} ms, errors={}'.format(
                    operation, metrics[operation]['throughput_req_s'],
                    metrics[operation]['p95_ms'], metrics[operation]['errors']), flush=True)
            lag = replica_lag(state)
            result = {'write': metrics['write'], 'read': metrics['read'],
                      'lag_after_writes_seconds': lag_after_writes,
                      'lag_after_reads_seconds': lag}
            all_results['policies'][policy] = result
            with (output / (policy + '.csv')).open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['timestamp_ms', 'policy', 'operation', 'request_number',
                                                           'status', 'node', 'latency_ms', 'error'])
                writer.writeheader()
                writer.writerows(records)
            (output / 'summary.json').write_text(json.dumps(all_results, indent=2) + '\n')
            if not args.no_cloudwatch:
                try:
                    cloudwatch(region, run_id, policy, records, metrics, lag_after_writes)
                    print('  CloudWatch logs and metrics published', flush=True)
                except Exception as exc:
                    print('  CloudWatch failed: {}: {}'.format(type(exc).__name__, exc), flush=True)
                    all_results.setdefault('cloudwatch_errors', {})[policy] = str(exc)
                    (output / 'summary.json').write_text(json.dumps(all_results, indent=2) + '\n')
    print('Results: {}'.format(output), flush=True)
    if all_results.get('cloudwatch_errors'):
        raise SystemExit('Benchmark saved, but CloudWatch publication failed; inspect summary.json')


if __name__ == '__main__':
    main()

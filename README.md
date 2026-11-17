# LOG8415E — Cloud Design Patterns

Six EC2 instances: a public Gatekeeper and private Trusted Host, Proxy, MySQL Primary and two MySQL replicas. The Primary contains Sakila and replicates changes asynchronously to both replicas.

## Setup

Use the AWS Academy Learner Lab terminal with valid temporary AWS credentials, Python virtual environment and `boto3` installed. Keep `.state/` and `.secrets/` locally; neither belongs in Git.

```bash
./scripts/deploy.sh
```

A clean deploy provisions the EC2 instances, configures MySQL replication and installs the HTTP Proxy, Trusted Host and Gatekeeper on their existing instances. When infrastructure already exists and MySQL replication works, **do not repeat deploy**. Install the application services on the existing instances:

```bash
./scripts/setup_proxy.sh
./scripts/setup_gateway.sh
```

SSH access is refreshed automatically the first time an SSH connection is made in each shell/script session. The refresh updates the Gatekeeper's public IP and the terminal's current `/32` access rule without creating new EC2 instances. It still requires valid Learner Lab AWS credentials.

## Gatekeeper and Trusted Host

The public Gatekeeper (`8000`) forwards HTTP requests to the private Trusted Host (`8001`). The Trusted Host validates query requests and forwards them to the private Proxy (`8002`). The Proxy handles database routing. Both HTTP services use Python's standard library and run as `systemd` services. The public port `8000` is limited to the current lab terminal IP by the Gatekeeper Security Group.

To install the two services on the already provisioned EC2 instances (no new infrastructure):

```bash
./scripts/setup_gateway.sh
```

To check the complete chain and make a query from the lab terminal:

```bash
source scripts/common.sh
ssh_base gatekeeper 'curl -fsS http://127.0.0.1:8000/health'
ssh_base gatekeeper "curl -sS -X POST http://127.0.0.1:8000/query -H 'Content-Type: application/json' -d '{\"sql\":\"SELECT COUNT(*) AS films FROM film\",\"policy\":\"random\"}'"
```

## Proxy

The Proxy runs on private EC2 port `8002`, reachable from the Trusted Host. It accepts `POST /query` with a SQL statement and optional parameters:

```json
{"sql":"SELECT film_id, title FROM film LIMIT 3", "policy":"random"}
```

Read policies:
- `direct`: read from Primary
- `random`: pick either replica
- `latency`: query both replicas with `SELECT 1` and choose the fastest response

`INSERT`, `UPDATE` and `DELETE` always go to Primary. Only these operations and `SELECT` are accepted. Responses contain the node, rows or affected count, and request duration. `GET /health` checks the HTTP service.

To test from the lab terminal using the existing SSH jump:

```bash
source scripts/common.sh
ssh_base proxy "curl -s http://localhost:8002/health"
ssh_base proxy "curl -s -X POST http://localhost:8002/query -H 'Content-Type: application/json' -d '{\"sql\":\"SELECT COUNT(*) AS films FROM film\",\"policy\":\"latency\"}'"
```

Verify replication with `./scripts/verify.sh` and clean up AWS resources with `./scripts/teardown.sh` when the project is finished. Teardown removes the project's AWS infrastructure.

## Benchmarks and CloudWatch

The benchmark runs from the Learner Lab terminal through the **public Gatekeeper** and
therefore exercises the entire Gatekeeper → Trusted Host → Proxy → MySQL request path.
It uses a small asynchronous Python client (five concurrent requests by default).
For each of `direct`, `random` and `latency`, it warms up with 20 reads, then sends
**1,000 writes and 1,000 reads**. Writes use existing `sakila.replication_probe`
with repeatable IDs, so running the benchmark again will not create extra tables.

With the lab's temporary AWS credentials active and the existing EC2s running:

```bash
source .venv/bin/activate
./scripts/benchmark.sh
```

The script refreshes the current lab terminal IP automatically. Results are saved to
`results/<UTC-run-time>/`: one CSV of raw requests per policy and a `summary.json`
with p50/p95/p99 client latencies, throughput, errors, node distribution, and
replication lag (`Seconds_Behind_Source`) sampled after writes and after reads.
The lag samples are snapshots, **not** maximum lag throughout the experiment;
zero lag does not rule out temporary stale reads.

The same benchmark sends individual request logs to CloudWatch Logs group
`/log8415e/benchmark` and aggregate metrics to CloudWatch namespace
`LOG8415E/Benchmark`. This uses the lab terminal's `boto3` credentials rather
than installing another daemon or IAM role on the EC2s. If the Learner Lab
blocks CloudWatch write APIs, the local CSV/JSON files are kept and the script
reports the publication failure explicitly.

To test the benchmark logic locally without publishing CloudWatch data, run
`python3 benchmark/run.py --url http://127.0.0.1:8000 --requests 5 --no-cloudwatch`
against a local test server. This does **not** satisfy the required load test.

After collecting the results, preserve the CSV/JSON files for the report. Run
`./scripts/teardown.sh` when finished with AWS resources (not during development).
The teardown also removes the benchmark CloudWatch log group. Save screenshots or
CloudWatch evidence for the report before teardown; local CSV/JSON results remain.

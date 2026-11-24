# LOG8415E — Cloud Design Patterns

AWS Academy Learner Lab project: seven EC2 instances, with a public Gatekeeper, private Trusted Host and Proxy, and a self-managed MySQL 8 primary with three asynchronous replicas. Sakila is loaded on the primary and replicated to all three replicas.

## Requirements

Run from the Learner Lab terminal with current AWS temporary credentials, AWS CLI, SSH, `openssl`, Python and `boto3` in the active virtual environment. No new IAM roles are created; infrastructure uses the existing LabInstanceProfile / LabRole. The AWS credentials are configured **outside this repository**.

Never commit `.state/`, `.secrets/` or the SSH private key. Retain those local folders until teardown is complete. The lab's terminal IP can change between sessions; the scripts refresh the Gatekeeper Security Group automatically.

## Deployment

For a clean environment:

```bash
source .venv/bin/activate
./scripts/deploy.sh
```

This provisions seven EC2 instances, configures MySQL + Sakila + replication, and installs the Proxy, Trusted Host and Gatekeeper. The MySQL installer corrects Ubuntu's default loopback-only `bind-address` for inter-instance connectivity. Only the Gatekeeper has a public IP. Private ingress is limited to the respective Security Group peers.

**If the seven EC2 instances are already running, do not rerun deploy.** To install or update just the application components:

```bash
./scripts/setup_proxy.sh
./scripts/setup_gateway.sh
```

`setup_gateway.sh` generates a local `.secrets/gatekeeper_token` on first use. The token is installed only on Gatekeeper with restricted file permissions, not in Git, Trusted Host or Proxy. The Gatekeeper requires it on `POST /query`; `/health` remains accessible for checks. HTTP and a single-source-IP ingress rule are used for this Learner Lab demo, **not** a production-ready HTTPS configuration.

## Verification and request flow

```bash
./scripts/verify.sh
```

All three replicas must show `IO=Yes`, `SQL=Yes` and 1,000 Sakila film records. To send a request from the lab terminal through the **public endpoint** (all services in the required path):

```bash
source scripts/common.sh
ensure_access
TOKEN="$(cat .secrets/gatekeeper_token)"
GATEKEEPER="http://$(field ips.gatekeeper.public):8000"
curl -sS -X POST "$GATEKEEPER/query" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"sql":"SELECT COUNT(*) AS films FROM film","policy":"random"}'
```

Change `random` to `direct` or `latency` to demonstrate each routing policy. Direct reads go to Primary; Random reads uniformly select a replica; Latency-aware checks replica latency and selects the faster healthy replica. Writes always go to Primary. `SELECT`, `INSERT`, `UPDATE` and `DELETE` are the only supported SQL operations. The Gatekeeper rejects missing/incorrect bearer tokens with HTTP 401 and invalid requests with HTTP 400.

Example rejection (missing token):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$GATEKEEPER/query" \
  -H 'Content-Type: application/json' -d '{"sql":"SELECT 1"}'
```

## Benchmark and CloudWatch

Run once after a successful deployment:

```bash
./scripts/benchmark.sh
```

The custom Python client uses five concurrent requests by default. After a warm-up, it sends 1,000 writes and 1,000 reads for **each** policy through the public Gatekeeper. The benchmark automatically reads the local Gatekeeper token. To perform a small connectivity test without publishing to CloudWatch:

```bash
./scripts/benchmark.sh --requests 10 --no-cloudwatch
```

Outputs are saved to `results/<timestamp>/`: `direct.csv`, `random.csv`, `latency.csv` (per-request observations) and `summary.json` (throughput, p50/p95/p99, error rate, per-node request counts, replication lag). Replication lag is sampled after writes and reads; it is not the maximum lag over the entire run. Save **all raw results** for the submission and report before ending the lab.

The benchmark also publishes request logs to `/log8415e/benchmark` and aggregated CloudWatch Metrics to `LOG8415E/Benchmark`, using temporary Learner Lab credentials from the terminal. Check both in the CloudWatch console. No CloudWatch agent or new IAM role is required.

## Reproducibility and teardown

The normal workflow is:

```bash
./scripts/deploy.sh
./scripts/verify.sh
./scripts/benchmark.sh
./scripts/teardown.sh
```

**Only run teardown after recording the demo, saving the benchmark outputs and CloudWatch evidence.** Teardown deletes this project's EC2 instances, VPC resources, NAT Gateway, Elastic IP, key pair and its benchmark log group. The local results remain. CloudWatch custom metric data can remain available until AWS retention expires. Keep the local `.state/` backup until teardown succeeds; do not delete it manually beforehand.

## Submission

The course requires a **private** GitHub repository (or ZIP), source and raw benchmark results, a LaTeX-generated PDF report (~12–20 pages), and a **10–15 minute** demo video showing deployment, routing, security isolation, benchmark results and teardown. Include a cost estimate, threat model, stale-read discussion, architecture diagrams, and an AI-use disclosure in the report. Never show temporary AWS credentials or the Gatekeeper token in the video.

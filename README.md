# LOG8415E — Cloud Design Patterns

Six EC2 instances: a public Gatekeeper and private Trusted Host, Proxy, MySQL Primary and two MySQL replicas. The Primary contains Sakila and replicates changes asynchronously to both replicas.

## Setup

Use the AWS Academy Learner Lab terminal with valid temporary AWS credentials, Python virtual environment and `boto3` installed. Keep `.state/` and `.secrets/` locally; neither belongs in Git.

```bash
./scripts/deploy.sh
```

A clean deploy provisions the EC2 instances, configures MySQL replication and installs the HTTP Proxy on its existing instance. When infrastructure already exists and MySQL replication works, **do not repeat deploy**. Install only the Proxy:

```bash
./scripts/setup_proxy.sh
```

SSH access is refreshed automatically the first time an SSH connection is made in each shell/script session. The refresh updates the Gatekeeper's public IP and the terminal's current `/32` access rule without creating new EC2 instances. It still requires valid Learner Lab AWS credentials.

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

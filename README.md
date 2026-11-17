# LOG8415E Final Project - Marco 1

Implementation scope: **6 EC2 instances**, isolated network, and **MySQL 8 primary + 2 asynchronous replicas**, initialized with **Sakila**. Gatekeeper, Trusted Host and Proxy applications are **not** part of Marco 1; their EC2 instances are provisioned for Marco 2.

## Architecture

- One **public** subnet: Gatekeeper EC2 (also an SSH jump host for deployment).
- One **private** subnet: Trusted Host, Proxy, MySQL Primary, MySQL Replica 1, MySQL Replica 2.
- Security groups only permit: admin SSH -> Gatekeeper; Gatekeeper SSH -> private nodes; Gatekeeper:8001 -> Trusted; Trusted:8002 -> Proxy; Proxy:3306 -> database nodes; MySQL:3306 within the database security group.
- A **NAT Gateway** gives private instances outbound Internet access for `apt` and downloading Sakila without exposing them to inbound Internet connections. The NAT Gateway, its Elastic IP, the EC2 instances, EBS, and the public IPv4 address **incur charges while running**.
- MySQL 8 runs on Ubuntu 22.04. Replication uses `GTID` and `SOURCE_AUTO_POSITION=1`.

## Prerequisites

- AWS Academy Learner Lab started with **valid temporary AWS credentials** and region configured in the AWS CLI. Never commit AWS credentials.
- AWS CLI, `bash`, `curl`, `ssh`, `ssh-keygen`, `openssl`, `sha256sum`, and Python 3 (3.7+ on the lab terminal is sufficient for these scripts).
- Terraform 1.5+; if missing, `deploy.sh` downloads Terraform 1.10.5 to `.tools/` and checks the SHA-256 digest against the vendor checksum file.
- AWS authorizations to create VPC/subnets/routes, six EC2, security groups, key pairs, public IPv4, EIP and NAT Gateway. If attaching `LabInstanceProfile`, `iam:PassRole` is required. **Read-only preflight does not prove create permissions.**
- Enough remaining Learner Lab budget and EC2 vCPU quota. Six `t3.micro` instances need **12 vCPUs**. Availability-zone offerings do not guarantee spare capacity or create privileges.
- A Linux terminal able to connect by SSH to the public address of the new Gatekeeper instance. The CloudShell/preconfigured lab terminal's detected egress IP can change.

## Deploy

From the **repository root**:

```bash
chmod +x scripts/*.sh mysql/*.sh
./scripts/deploy.sh
```

The script detects the AWS region, the first available AZ and the terminal's egress IP, checks `t3.micro` offerings, creates an SSH key under `.state/`, installs Terraform locally if needed, and asks for the word **DEPLOY** before creating chargeable resources. It then provisions the VPC and EC2s, installs MySQL, loads Sakila, builds a consistent GTID dump, restores it to the replicas and verifies that a **new write** appears on both replicas.

Optional overrides (only if the environment differs):

```bash
AWS_REGION=us-east-1 AWS_AZ=us-east-1b INSTANCE_TYPE=t3.small ./scripts/deploy.sh
ADMIN_CIDR=203.0.113.10/32 ./scripts/deploy.sh
INSTANCE_PROFILE_NAME='' ./scripts/deploy.sh  # Only if LabRole/PassRole is prohibited
```

`INSTANCE_PROFILE_NAME` defaults to the **preexisting** `LabInstanceProfile`. Terraform does **not** create IAM roles. When `PassRole` is blocked, leaving it blank avoids attaching an instance profile during Marco 1; revisit when configuring mandatory CloudWatch in Marco 3.

**Do not run `deploy.sh` twice against an initialized MySQL cluster.** It is written for fresh provisioning, not destructive database re-seeding. Re-check current state after a partially failed deployment before retrying. Preserve `.state/` until you have destroyed the infrastructure.

## Verify

```bash
./scripts/verify.sh
```

It verifies that Sakila has 1,000 film rows and both replicas report running IO/SQL threads, plus a row written to `sakila.replication_probe` **after** establishing replication. `Seconds_Behind_Source` is printed for an approximate lag observation; detailed lag benchmarking belongs to Marco 3.

## Teardown - mandatory

```bash
./scripts/teardown.sh
```

The script requires the word **DESTROY** before executing `terraform destroy`. Terraform uses the **local state** under `infra/` to identify project resources. Never delete this state while the AWS resources exist. Verify in the AWS console that EC2, NAT Gateway, Elastic IP and volumes created by this project have been removed. Teardown does not delete the `.state/` SSH key or local MySQL test data; these are excluded from Git.

## Git and secrets

`.gitignore` excludes `.state/` (SSH key and SQL snapshot), `.secrets/` (replication password), `.tools/`, Terraform state and local variable files. The generated SSH **public** key is imported as an EC2 key pair; the **private** key remains local. No AWS access keys, MySQL passwords or private SSH keys are embedded in source code or Terraform variables. Terraform state remains local: avoid uploading or committing it.

After an initial successful `terraform init`, consider committing `infra/.terraform.lock.hcl` to pin provider versions. Never commit `infra/terraform.tfstate`.

## Scope and limitations

- **No FastAPI services yet:** they are Marco 2.
- **No CloudWatch agent or benchmark yet:** these are mandatory for the final project but part of Marco 3.
- **Not tested against your Learner Lab:** run-time permissions, package download, instance launch availability and successful replication must be verified in the actual AWS account.
- MySQL uses a dedicated replication account limited to the private subnet addresses. Application database users will be created later with only the necessary privileges.
- One AZ is used to reduce project complexity; the assignment does not ask for cross-AZ failover. This is a benchmarking lab, not production high availability.
- Public Gatekeeper port 8000 is reserved for Marco 2, where the HTTP service will implement authentication and validation.

Official references: AWS VPC NAT Gateway documentation, [MySQL replication with GTIDs](https://dev.mysql.com/doc/refman/8.0/en/replication-gtids-howto.html), [mysqldump GTID options](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html), [Sakila sample database](https://dev.mysql.com/doc/sakila/en/), [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs).

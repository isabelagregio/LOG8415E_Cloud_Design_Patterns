#!/usr/bin/env python3
"""LOG8415E read-only AWS CloudShell preflight (AWS CLI + Python stdlib only).

This script performs only AWS describe/list/get APIs and a public-IP lookup.
No AWS resources are created, modified, or deleted. It prints no credentials,
account IDs, ARNs, key pair names, instance IDs, or private IPs.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request

TYPES = ["t3.micro", "t3.small", "t3.medium", "t2.micro", "t2.small", "t2.medium"]


def aws(service, action, *args, region=None, timeout=25):
    cmd = ["aws", service, action, *args, "--output", "json", "--no-cli-pager", "--no-paginate"]
    if region:
        cmd += ["--region", region]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "AWS CLI not executable"
    if p.returncode:
        err = p.stderr
        if "AccessDenied" in err or "UnauthorizedOperation" in err or "not authorized" in err:
            return None, "access denied"
        if "InvalidClientTokenId" in err or "ExpiredToken" in err or "UnrecognizedClientException" in err:
            return None, "invalid or expired credentials"
        if "Could not connect" in err or "EndpointConnectionError" in err:
            return None, "AWS endpoint/network unavailable"
        if "InvalidParameter" in err or "UnsupportedOperation" in err:
            return None, "API unsupported or invalid parameter"
        return None, "AWS CLI request failed (details withheld)"
    try:
        return json.loads(p.stdout or "{}"), None
    except json.JSONDecodeError:
        return None, "invalid JSON response"


def show(name, value):
    print(f"  {name:<30} {value}")


def status(label, error):
    show(label, "OK (read allowed)" if error is None else f"UNKNOWN ({error})")


def main():
    print("LOG8415E | READ-ONLY CLOUDSHELL PREFLIGHT")
    print("No create/update/delete APIs are called. No resources will be provisioned.\n")
    if not shutil.which("aws"):
        print("ERROR: AWS CLI not found. Run in AWS CloudShell.")
        return 1

    print("[1] Environment")
    show("Python", sys.version.split()[0])
    show("Terraform", "installed" if shutil.which("terraform") else "not found")
    show("Git", "installed" if shutil.which("git") else "not found")
    show("AWS CLI", "installed")
    show("CloudShell indicator", "present" if os.environ.get("AWS_EXECUTION_ENV", "").startswith("CloudShell") else "not detected (not necessarily a problem)")

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        p = subprocess.run(["aws", "configure", "get", "region"], capture_output=True, text=True, timeout=10, check=False)
        region = p.stdout.strip() if p.returncode == 0 else None
    if not region:
        print("ERROR: no AWS Region is configured. Select a Region in CloudShell and retry.")
        return 1
    show("AWS Region", region)

    identity, err = aws("sts", "get-caller-identity", region=region)
    status("AWS identity authentication", err)
    if err:
        print("Cannot continue without working AWS CLI credentials.")
        return 1
    # Do not expose account ID, principal ARN or access credentials.
    show("Account/ARN", "intentionally hidden")

    print("\n[2] EC2: availability zones and instance offerings")
    zones, err = aws("ec2", "describe-availability-zones", "--filters", "Name=state,Values=available", region=region)
    status("DescribeAvailabilityZones", err)
    zone_names = [z.get("ZoneName") for z in (zones or {}).get("AvailabilityZones", []) if z.get("ZoneName")]
    show("Available AZs", ", ".join(zone_names) if zone_names else "unknown")
    offerings = {}
    for zone in zone_names[:8]:
        result, e = aws("ec2", "describe-instance-type-offerings", "--location-type", "availability-zone", "--filters", f"Name=location,Values={zone}", "Name=instance-type,Values=" + ",".join(TYPES), region=region)
        if e:
            show("Instance type offerings", f"UNKNOWN ({e})")
            break
        offered = {x.get("InstanceType") for x in result.get("InstanceTypeOfferings", [])}
        offerings[zone] = [t for t in TYPES if t in offered]
    for zone, available in offerings.items():
        show(zone, ", ".join(available) or "none of candidate types")
    show("Note", "offerings do not guarantee launch capacity or lab permission")

    inst, e = aws("ec2", "describe-instances", "--filters", "Name=instance-state-name,Values=pending,running,stopping,stopped", region=region)
    status("DescribeInstances", e)
    if inst is not None:
        counts = {}
        for reservation in inst.get("Reservations", []):
            for i in reservation.get("Instances", []):
                s = i.get("State", {}).get("Name", "unknown")
                counts[s] = counts.get(s, 0) + 1
        show("Existing EC2 instances", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "0")

    vpc, e = aws("ec2", "describe-vpcs", region=region)
    status("DescribeVpcs", e)
    if vpc is not None:
        show("Existing VPCs", len(vpc.get("Vpcs", [])))
    subnet, e = aws("ec2", "describe-subnets", region=region)
    status("DescribeSubnets", e)
    if subnet is not None:
        show("Existing subnets", len(subnet.get("Subnets", [])))
    nat, e = aws("ec2", "describe-nat-gateways", region=region)
    status("DescribeNatGateways", e)
    if nat is not None:
        show("Existing NAT gateways", len([n for n in nat.get("NatGateways", []) if n.get("State") != "deleted"]))
    keys, e = aws("ec2", "describe-key-pairs", region=region)
    status("DescribeKeyPairs", e)
    if keys is not None:
        show("Existing key pairs (count)", len(keys.get("KeyPairs", [])))

    print("\n[3] Ubuntu AMI candidates (official Canonical owner)")
    for release in ("noble-24.04", "jammy-22.04"):
        res, e = aws("ec2", "describe-images", "--owners", "099720109477", "--filters", f"Name=name,Values=ubuntu/images/hvm-ssd*/ubuntu-{release}-amd64-server-*", "Name=state,Values=available", "Name=architecture,Values=x86_64", region=region, timeout=35)
        if e:
            show(release, f"UNKNOWN ({e})")
            continue
        imgs = sorted(res.get("Images", []), key=lambda x: x.get("CreationDate", ""), reverse=True)
        show(release, (imgs[0].get("ImageId", "not found") + " (latest matching; verify compatibility)") if imgs else "no matching image")

    print("\n[4] IAM / LabRole (read permission is not PassRole permission)")
    role, e = aws("iam", "get-role", "--role-name", "LabRole", region=region)
    show("GetRole(LabRole)", "role visible" if role else f"UNKNOWN ({e})")
    profiles, e = aws("iam", "list-instance-profiles", region=region)
    if e:
        show("Instance profiles", f"UNKNOWN ({e})")
    else:
        matches = [p for p in profiles.get("InstanceProfiles", []) if any(r.get("RoleName") == "LabRole" for r in p.get("Roles", []))]
        show("Profiles containing LabRole", len(matches))
        show("Profile names", ", ".join(p.get("InstanceProfileName", "") for p in matches) if matches else "none")
    show("iam:PassRole", "NOT verifiable through these read checks")

    print("\n[5] CloudWatch / SSM / quota read access")
    _, e = aws("logs", "describe-log-groups", "--limit", "1", region=region)
    status("CloudWatch Logs Describe", e)
    _, e = aws("cloudwatch", "list-metrics", "--namespace", "AWS/EC2", region=region)
    status("CloudWatch ListMetrics", e)
    _, e = aws("ssm", "describe-parameters", "--max-results", "1", region=region)
    status("SSM DescribeParameters", e)
    quota, e = aws("service-quotas", "get-service-quota", "--service-code", "ec2", "--quota-code", "L-1216C47A", region=region)
    show("EC2 standard vCPU quota", str(quota.get("Quota", {}).get("Value", "unknown")) if quota else f"UNKNOWN ({e})")
    show("CloudWatch write permission", "NOT tested (would require a write)")
    show("Secret storage permission", "NOT tested (would require a write)")

    print("\n[6] CloudShell egress IP (informational only)")
    try:
        req = urllib.request.Request("https://checkip.amazonaws.com/", headers={"User-Agent": "LOG8415E-preflight/1"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            ip = resp.read(100).decode().strip()
        # Show address because it is relevant to configuring SSH but it is not stable.
        show("Current public egress IP", ip if len(ip) < 50 else "unexpected response")
    except Exception:
        show("Current public egress IP", "unknown (external lookup failed)")
    show("Important", "CloudShell egress IP may change; do not assume SSH stays authorized")

    print("\n[7] Lab-specific limitations")
    show("Learner Lab remaining credits", "NOT exposed by generic AWS APIs; check lab portal")
    show("Permission to launch 6 EC2", "NOT established by read-only calls")
    show("NAT Gateway create permission", "NOT established by read-only calls")
    show("Real EC2 capacity", "NOT established by offerings list")
    print("\nNEXT: Share this output, with any IP or account information redacted if desired.")
    print("Do NOT run terraform apply yet. This script has not changed any AWS resources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

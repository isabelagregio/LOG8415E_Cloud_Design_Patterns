#!/usr/bin/env python3
"""Refresh the project's Gatekeeper endpoint and restricted SSH ingress.

Run before connecting from a new lab terminal/session. Only touches this
project's Gatekeeper security group; never creates or replaces EC2 instances.
"""
import ipaddress
import json
import os
from pathlib import Path
import urllib.request

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.state' / 'aws.json'


def main():
    if not STATE.exists():
        raise SystemExit('No .state/aws.json; provision the environment first.')
    state = json.loads(STATE.read_text())
    ec2 = boto3.client('ec2', region_name=state['region'])
    instance_id = state['instances']['gatekeeper']
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instance = response['Reservations'][0]['Instances'][0]
    if instance['State']['Name'] != 'running':
        raise SystemExit('Gatekeeper is not running: ' + instance['State']['Name'])
    public_ip = instance.get('PublicIpAddress')
    if not public_ip:
        raise SystemExit('Gatekeeper has no public IPv4 address.')
    groups = instance['SecurityGroups']
    project_groups = [g for g in groups if g['GroupName'].startswith('log8415e-gatekeeper-')]
    if len(project_groups) != 1:
        raise SystemExit('Expected exactly one project Gatekeeper security group.')
    sg_id = project_groups[0]['GroupId']

    with urllib.request.urlopen('https://checkip.amazonaws.com', timeout=10) as r:
        current_ip = r.read().decode('ascii').strip()
    ipaddress.IPv4Address(current_ip)
    new_cidr = current_ip + '/32'
    old_cidr = state.get('admin_cidr')
    group = ec2.describe_security_groups(GroupIds=[sg_id])['SecurityGroups'][0]

    # Rebind only the two public entry ports, and only the prior project CIDR.
    for port in (22, 8000):
        allowed = {rule['CidrIp']
                   for permission in group['IpPermissions']
                   if permission.get('IpProtocol') == 'tcp'
                   and permission.get('FromPort') == port
                   and permission.get('ToPort') == port
                   for rule in permission.get('IpRanges', [])}
        if new_cidr not in allowed:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id, IpProtocol='tcp', FromPort=port, ToPort=port,
                CidrIp=new_cidr)
        if old_cidr and old_cidr != new_cidr and old_cidr in allowed:
            ec2.revoke_security_group_ingress(
                GroupId=sg_id, IpProtocol='tcp', FromPort=port, ToPort=port,
                CidrIp=old_cidr)

    state['ips']['gatekeeper']['public'] = public_ip
    state['ips']['gatekeeper']['private'] = instance['PrivateIpAddress']
    state['admin_cidr'] = new_cidr
    temporary = STATE.with_suffix('.json.tmp')
    with temporary.open('w') as file:
        json.dump(state, file, indent=2, sort_keys=True)
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(STATE))
    print('Gatekeeper: {} | SSH client CIDR: {}'.format(public_ip, new_cidr))


if __name__ == '__main__':
    main()

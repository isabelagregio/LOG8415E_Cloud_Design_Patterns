#!/usr/bin/env python3
"""Provision the seven EC2 nodes for LOG8415E using boto3.

The JSON state is local and must never be committed. No resources are deleted on
provision failures: run teardown after investigating any partial deployment.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit('boto3 is required: python3 -m pip install --user "boto3<1.34"')

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / '.state'
STATE_FILE = STATE_DIR / 'aws.json'
NODES = ['gatekeeper', 'trusted', 'proxy', 'primary', 'replica1', 'replica2', 'replica3']
PORTS = {'gatekeeper': 8000, 'trusted': 8001, 'proxy': 8002}
CIDR_VPC = '10.42.0.0/16'
CIDR_PUBLIC = '10.42.1.0/24'
CIDR_PRIVATE = '10.42.2.0/24'


def persist(s):
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(str(STATE_DIR), 0o700)
    path = STATE_FILE.with_suffix('.tmp')
    with path.open('w') as f:
        json.dump(s, f, indent=2, sort_keys=True)
    os.chmod(str(path), 0o600)
    os.replace(str(path), str(STATE_FILE))


def read_state():
    if not STATE_FILE.exists():
        sys.exit('No .state/aws.json found. Nothing to manage.')
    return json.loads(STATE_FILE.read_text())


def record(s, typ, rid):
    s.setdefault('created', []).append({'type': typ, 'id': rid})
    persist(s)
    print('+ {} {}'.format(typ, rid), flush=True)
    return rid


def tag(ec2, rid, name):
    ec2.create_tags(Resources=[rid], Tags=[
        {'Key': 'Project', 'Value': 'LOG8415E-final'},
        {'Key': 'Name', 'Value': 'log8415e-' + name},
    ])


def ensure_key(s, ec2):
    key = STATE_DIR / 'id_ed25519'
    if not key.exists():
        subprocess.check_call(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)])
    os.chmod(str(key), 0o600)
    name = 'log8415e-{}'.format(s['project_id'])
    if not s.get('key_name'):
        ec2.import_key_pair(KeyName=name, PublicKeyMaterial=key.with_suffix('.pub').read_bytes())
        record(s, 'key', name)
        s['key_name'] = name
        persist(s)
    return name


def choose_az(ec2, typ):
    offered = {x['Location'] for p in ec2.get_paginator('describe_instance_type_offerings').paginate(
        LocationType='availability-zone', Filters=[{'Name': 'instance-type', 'Values': [typ]}])
        for x in p['InstanceTypeOfferings']}
    zones = [z['ZoneName'] for z in ec2.describe_availability_zones(
        Filters=[{'Name': 'state', 'Values': ['available']}])['AvailabilityZones']]
    common = sorted(set(zones) & offered)
    if not common:
        raise RuntimeError('No availability zone offers {}'.format(typ))
    requested = os.environ.get('AWS_AZ')
    if requested and requested not in common:
        raise RuntimeError('AWS_AZ={} does not offer {}'.format(requested, typ))
    return requested or common[0]


def image_id(ec2):
    # Canonical official Ubuntu Server 22.04 amd64 HVM AMIs.
    images = ec2.describe_images(Owners=['099720109477'], Filters=[
        {'Name': 'name', 'Values': ['ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*']},
        {'Name': 'architecture', 'Values': ['x86_64']},
        {'Name': 'state', 'Values': ['available']},
        {'Name': 'virtualization-type', 'Values': ['hvm']},
    ])['Images']
    if not images:
        raise RuntimeError('Ubuntu 22.04 AMI not found')
    return max(images, key=lambda x: x['CreationDate'])['ImageId']


def allow(ec2, sg, port, source_sg=None, cidr=None):
    source = {'GroupId': source_sg} if source_sg else {'CidrIp': cidr}
    ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
        'IpProtocol': 'tcp', 'FromPort': port, 'ToPort': port, 'UserIdGroupPairs': [source] if source_sg else [],
        'IpRanges': [] if source_sg else [source],
    }])


def wait_nat(ec2, nat_id):
    for _ in range(60):
        x = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])['NatGateways'][0]
        if x['State'] == 'available':
            return
        if x['State'] in ('failed', 'deleted'):
            raise RuntimeError('NAT Gateway state: {}'.format(x['State']))
        time.sleep(10)
    raise RuntimeError('Timed out waiting for NAT Gateway')


def provision():
    if STATE_FILE.exists():
        raise RuntimeError('State already exists. Run verify or teardown; refusing duplicate deployment.')
    region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or boto3.session.Session().region_name or 'us-east-1'
    ec2 = boto3.client('ec2', region_name=region)
    sts = boto3.client('sts', region_name=region)
    sts.get_caller_identity()
    inst_type = os.environ.get('INSTANCE_TYPE', 't3.micro')
    az = choose_az(ec2, inst_type)
    ami = image_id(ec2)
    ip = os.environ.get('ADMIN_CIDR')
    if not ip:
        with urllib.request.urlopen('https://checkip.amazonaws.com', timeout=10) as r:
            ip = r.read().decode().strip() + '/32'
    ipaddress.ip_network(ip, strict=False)
    if ipaddress.ip_network(ip).prefixlen != 32:
        raise RuntimeError('ADMIN_CIDR must be a single IPv4 /32 address')
    s = {'region': region, 'az': az, 'instance_type': inst_type, 'image_id': ami,
         'admin_cidr': ip, 'project_id': str(int(time.time())), 'created': [], 'instances': {}}
    persist(s)
    print('Region={} AZ={} AMI={} type={} admin={}'.format(region, az, ami, inst_type, ip), flush=True)
    try:
        vpc = record(s, 'vpc', ec2.create_vpc(CidrBlock=CIDR_VPC)['Vpc']['VpcId'])
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsSupport={'Value': True})
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsHostnames={'Value': True})
        tag(ec2, vpc, 'vpc')
        pub = record(s, 'subnet', ec2.create_subnet(VpcId=vpc, CidrBlock=CIDR_PUBLIC, AvailabilityZone=az)['Subnet']['SubnetId'])
        private = record(s, 'subnet', ec2.create_subnet(VpcId=vpc, CidrBlock=CIDR_PRIVATE, AvailabilityZone=az)['Subnet']['SubnetId'])
        ec2.modify_subnet_attribute(SubnetId=pub, MapPublicIpOnLaunch={'Value': True})
        igw = record(s, 'igw', ec2.create_internet_gateway()['InternetGateway']['InternetGatewayId'])
        ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc)
        rt_pub = record(s, 'route_table', ec2.create_route_table(VpcId=vpc)['RouteTable']['RouteTableId'])
        ec2.create_route(RouteTableId=rt_pub, DestinationCidrBlock='0.0.0.0/0', GatewayId=igw)
        record(s, 'association', ec2.associate_route_table(RouteTableId=rt_pub, SubnetId=pub)['AssociationId'])
        eip = ec2.allocate_address(Domain='vpc')
        record(s, 'eip', eip['AllocationId'])
        nat = record(s, 'nat', ec2.create_nat_gateway(SubnetId=pub, AllocationId=eip['AllocationId'])['NatGateway']['NatGatewayId'])
        wait_nat(ec2, nat)
        rt_priv = record(s, 'route_table', ec2.create_route_table(VpcId=vpc)['RouteTable']['RouteTableId'])
        ec2.create_route(RouteTableId=rt_priv, DestinationCidrBlock='0.0.0.0/0', NatGatewayId=nat)
        record(s, 'association', ec2.associate_route_table(RouteTableId=rt_priv, SubnetId=private)['AssociationId'])
        groups = {}
        for name in NODES:
            gid = record(s, 'sg', ec2.create_security_group(VpcId=vpc, GroupName='log8415e-{}-{}'.format(name, s['project_id']), Description='LOG8415E {}'.format(name))['GroupId'])
            groups[name] = gid
        # Only Gatekeeper has a public ingress. Private SSH is reached through it.
        allow(ec2, groups['gatekeeper'], 22, cidr=ip)
        allow(ec2, groups['gatekeeper'], 8000, cidr=ip)
        for name in NODES[1:]:
            allow(ec2, groups[name], 22, source_sg=groups['gatekeeper'])
        allow(ec2, groups['trusted'], 8001, source_sg=groups['gatekeeper'])
        allow(ec2, groups['proxy'], 8002, source_sg=groups['trusted'])
        for name in ('primary', 'replica1', 'replica2', 'replica3'):
            allow(ec2, groups[name], 3306, source_sg=groups['proxy'])
        for name in ('replica1', 'replica2', 'replica3'):
            allow(ec2, groups['primary'], 3306, source_sg=groups[name])
        keyname = ensure_key(s, ec2)
        profile = os.environ.get('INSTANCE_PROFILE_NAME', 'LabInstanceProfile')
        # Role is pre-existing; do not create any IAM role/profile.
        for name in NODES:
            params = dict(ImageId=ami, InstanceType=inst_type, MinCount=1, MaxCount=1,
                          SubnetId=pub if name == 'gatekeeper' else private,
                          SecurityGroupIds=[groups[name]], KeyName=keyname,
                          BlockDeviceMappings=[{'DeviceName': '/dev/sda1', 'Ebs': {'VolumeSize': 16, 'VolumeType': 'gp3', 'DeleteOnTermination': True}}],
                          TagSpecifications=[{'ResourceType': 'instance', 'Tags': [
                              {'Key': 'Name', 'Value': 'log8415e-' + name}, {'Key': 'Project', 'Value': 'LOG8415E-final'}]}])
            # Use only the lab-provided instance profile.
            if profile:
                params['IamInstanceProfile'] = {'Name': profile}
            result = ec2.run_instances(**params)['Instances'][0]
            iid = record(s, 'instance', result['InstanceId'])
            s['instances'][name] = iid
            persist(s)
        ec2.get_waiter('instance_running').wait(InstanceIds=list(s['instances'].values()), WaiterConfig={'Delay': 10, 'MaxAttempts': 60})
        info = ec2.describe_instances(InstanceIds=list(s['instances'].values()))
        details = {i['InstanceId']: i for r in info['Reservations'] for i in r['Instances']}
        s['ips'] = {n: {'private': details[i]['PrivateIpAddress'], 'public': details[i].get('PublicIpAddress')}
                    for n, i in s['instances'].items()}
        persist(s)
        print('EC2 provisioning complete; endpoints written to .state/aws.json', flush=True)
    except Exception:
        print('Provisioning failed; partial resources are tracked in .state/aws.json. Run teardown.sh to clean up.', file=sys.stderr)
        raise


def teardown():
    s = read_state()
    ec2 = boto3.client('ec2', region_name=s['region'])
    items = s.get('created', [])
    instances = [x['id'] for x in items if x['type'] == 'instance']
    if instances:
        try:
            ec2.terminate_instances(InstanceIds=instances)
            ec2.get_waiter('instance_terminated').wait(InstanceIds=instances, WaiterConfig={'Delay': 10, 'MaxAttempts': 80})
        except ClientError as e:
            if e.response['Error']['Code'] != 'InvalidInstanceID.NotFound':
                raise
    nat_ids = [x['id'] for x in items if x['type'] == 'nat']
    for nid in nat_ids:
        try:
            ec2.delete_nat_gateway(NatGatewayId=nid)
        except ClientError as e:
            if e.response['Error']['Code'] != 'NatGatewayNotFound':
                raise
    for nid in nat_ids:
        for attempt in range(90):
            try:
                got = ec2.describe_nat_gateways(NatGatewayIds=[nid])['NatGateways']
                if not got or got[0]['State'] == 'deleted':
                    break
            except ClientError as e:
                if e.response['Error']['Code'] == 'NatGatewayNotFound':
                    break
                raise
            time.sleep(10)
        else:
            raise RuntimeError('NAT Gateway deletion timed out')
    # Revoke SG references before deleting groups, otherwise EC2 reports DependencyViolation.
    for x in items:
        if x['type'] == 'sg':
            try:
                group = ec2.describe_security_groups(GroupIds=[x['id']])['SecurityGroups'][0]
                if group['IpPermissions']:
                    ec2.revoke_security_group_ingress(GroupId=x['id'], IpPermissions=group['IpPermissions'])
            except ClientError as e:
                if e.response['Error']['Code'] != 'InvalidGroup.NotFound':
                    raise
    for x in reversed(items):
        kind, rid = x['type'], x['id']
        try:
            if kind in ('instance', 'nat'):
                continue
            elif kind == 'association':
                ec2.disassociate_route_table(AssociationId=rid)
            elif kind == 'route_table':
                ec2.delete_route_table(RouteTableId=rid)
            elif kind == 'igw':
                # Detach from this project's VPC; never touch preexisting VPCs.
                ec2.detach_internet_gateway(InternetGatewayId=rid, VpcId=next(z['id'] for z in items if z['type'] == 'vpc'))
                ec2.delete_internet_gateway(InternetGatewayId=rid)
            elif kind == 'eip':
                ec2.release_address(AllocationId=rid)
            elif kind == 'sg':
                ec2.delete_security_group(GroupId=rid)
            elif kind == 'subnet':
                ec2.delete_subnet(SubnetId=rid)
            elif kind == 'vpc':
                ec2.delete_vpc(VpcId=rid)
            elif kind == 'key':
                ec2.delete_key_pair(KeyName=rid)
        except ClientError as e:
            if e.response['Error']['Code'] not in ('InvalidGroup.NotFound', 'InvalidSubnetID.NotFound', 'InvalidVpcID.NotFound', 'InvalidRouteTableID.NotFound', 'InvalidAssociationID.NotFound', 'InvalidInternetGatewayID.NotFound', 'InvalidAllocationID.NotFound', 'InvalidKeyPair.NotFound'):
                raise
    # Benchmark logs are project-owned; raw CSV/JSON results remain on disk.
    try:
        boto3.client('logs', region_name=s['region']).delete_log_group(
            logGroupName='/log8415e/benchmark')
    except ClientError as error:
        if error.response['Error']['Code'] != 'ResourceNotFoundException':
            print('Warning: could not remove benchmark CloudWatch log group: {}'.format(error), file=sys.stderr)
    STATE_FILE.unlink()
    print('Project AWS resources removed. Local SSH key retained in .state/.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['deploy', 'destroy'])
    args = parser.parse_args()
    if args.action == 'deploy':
        provision()
    else:
        teardown()

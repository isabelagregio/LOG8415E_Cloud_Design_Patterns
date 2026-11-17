# Single-AZ lab deployment; independent public/private subnet isolation.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  common_tags = { Project = var.project_name, ManagedBy = "terraform" }
  nodes = {
    gatekeeper = { public = true,  subnet = "public",  sg = "gatekeeper" }
    trusted    = { public = false, subnet = "private", sg = "trusted" }
    proxy      = { public = false, subnet = "private", sg = "proxy" }
    primary    = { public = false, subnet = "private", sg = "database" }
    replica1   = { public = false, subnet = "private", sg = "database" }
    replica2   = { public = false, subnet = "private", sg = "database" }
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.common_tags, { Name = "${var.project_name}-vpc" })
}
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true
  tags                    = merge(local.common_tags, { Name = "${var.project_name}-public" })
}
resource "aws_subnet" "private" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.2.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false
  tags                    = merge(local.common_tags, { Name = "${var.project_name}-private" })
}
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.common_tags, { Name = "${var.project_name}-igw" })
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route { cidr_block = "0.0.0.0/0", gateway_id = aws_internet_gateway.main.id }
  tags = merge(local.common_tags, { Name = "${var.project_name}-public-rt" })
}
resource "aws_route_table_association" "public" {
  route_table_id = aws_route_table.public.id
  subnet_id      = aws_subnet.public.id
}

# The private hosts need outbound connectivity for apt, MySQL and Sakila.
# NAT Gateway is intentionally explicit: it incurs charges until teardown.
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${var.project_name}-nat-eip" })
}
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  depends_on    = [aws_internet_gateway.main]
  tags          = merge(local.common_tags, { Name = "${var.project_name}-nat" })
}
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route { cidr_block = "0.0.0.0/0", nat_gateway_id = aws_nat_gateway.main.id }
  tags = merge(local.common_tags, { Name = "${var.project_name}-private-rt" })
}
resource "aws_route_table_association" "private" {
  route_table_id = aws_route_table.private.id
  subnet_id      = aws_subnet.private.id
}

resource "aws_security_group" "gatekeeper" {
  name_prefix = "${var.project_name}-gatekeeper-"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${var.project_name}-gatekeeper-sg" })
}
resource "aws_security_group" "trusted" {
  name_prefix = "${var.project_name}-trusted-"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${var.project_name}-trusted-sg" })
}
resource "aws_security_group" "proxy" {
  name_prefix = "${var.project_name}-proxy-"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${var.project_name}-proxy-sg" })
}
resource "aws_security_group" "database" {
  name_prefix = "${var.project_name}-db-"
  vpc_id      = aws_vpc.main.id
  tags        = merge(local.common_tags, { Name = "${var.project_name}-db-sg" })
}

# Gatekeeper exposes future HTTP API and SSH administration from one IP.
resource "aws_vpc_security_group_ingress_rule" "gateway_ssh" {
  security_group_id = aws_security_group.gatekeeper.id
  cidr_ipv4         = var.admin_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}
resource "aws_vpc_security_group_ingress_rule" "gateway_api" {
  security_group_id = aws_security_group.gatekeeper.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 8000
  to_port           = 8000
  ip_protocol       = "tcp"
}
# Gatekeeper is the SSH jump host for private EC2s.
resource "aws_vpc_security_group_ingress_rule" "private_ssh" {
  for_each = {
    trusted  = aws_security_group.trusted.id
    proxy    = aws_security_group.proxy.id
    database = aws_security_group.database.id
  }
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.gatekeeper.id
  from_port                    = 22
  to_port                      = 22
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_ingress_rule" "trusted_api" {
  security_group_id            = aws_security_group.trusted.id
  referenced_security_group_id = aws_security_group.gatekeeper.id
  from_port                    = 8001
  to_port                      = 8001
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_ingress_rule" "proxy_api" {
  security_group_id            = aws_security_group.proxy.id
  referenced_security_group_id = aws_security_group.trusted.id
  from_port                    = 8002
  to_port                      = 8002
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_ingress_rule" "proxy_to_database" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.proxy.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_ingress_rule" "database_replication" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.database.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_egress_rule" "outbound" {
  for_each = {
    gatekeeper = aws_security_group.gatekeeper.id
    trusted    = aws_security_group.trusted.id
    proxy      = aws_security_group.proxy.id
    database   = aws_security_group.database.id
  }
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_key_pair" "project" {
  key_name_prefix = "${var.project_name}-"
  public_key      = file(var.ssh_public_key_path)
  tags            = local.common_tags
}

resource "aws_instance" "node" {
  for_each                    = local.nodes
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = each.value.public ? aws_subnet.public.id : aws_subnet.private.id
  associate_public_ip_address = each.value.public
  vpc_security_group_ids = [
    each.value.sg == "gatekeeper" ? aws_security_group.gatekeeper.id :
    each.value.sg == "trusted"    ? aws_security_group.trusted.id :
    each.value.sg == "proxy"      ? aws_security_group.proxy.id : aws_security_group.database.id
  ]
  key_name             = aws_key_pair.project.key_name
  iam_instance_profile = var.instance_profile_name != "" ? var.instance_profile_name : null
  root_block_device {
    volume_size = 12
    volume_type = "gp3"
    encrypted   = true
  }
  tags = merge(local.common_tags, { Name = "${var.project_name}-${each.key}" })
  depends_on = [aws_route_table_association.private, aws_route_table_association.public]
}

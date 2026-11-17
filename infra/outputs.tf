output "gatekeeper_public_ip" {
  value = aws_instance.node["gatekeeper"].public_ip
}
output "node_private_ips" {
  value = { for k, instance in aws_instance.node : k => instance.private_ip }
}
output "instance_ids" {
  value = { for k, instance in aws_instance.node : k => instance.id }
}
output "vpc_id" {
  value = aws_vpc.main.id
}

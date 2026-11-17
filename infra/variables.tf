variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "availability_zone" {
  type        = string
  description = "Selected AZ; must support the chosen EC2 size."
}
variable "instance_type" {
  type    = string
  default = "t3.micro"
}
variable "ssh_public_key_path" {
  type        = string
  description = "Absolute path to a locally generated PUBLIC SSH key."
}
variable "admin_cidr" {
  type        = string
  description = "Administrator public IPv4 address in /32 notation."
  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && endswith(var.admin_cidr, "/32")
    error_message = "Provide a single public IP with a /32 mask."
  }
}
variable "instance_profile_name" {
  type        = string
  description = "Use existing LabInstanceProfile; pass an empty string if PassRole is unavailable."
  default     = "LabInstanceProfile"
}
variable "project_name" {
  type    = string
  default = "log8415e-final"
}

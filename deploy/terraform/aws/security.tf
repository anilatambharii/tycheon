# Security groups. The only 0.0.0.0/0 ingress in this module is the ALB on 443
# (and even that is narrowable via alb_ingress_cidrs). RDS and Redis accept
# traffic solely from the EKS node/pod security group.
#
# Pods run on the primary EKS cluster security group (managed node groups attach
# it automatically), so that group is the source for the data-store rules.

locals {
  eks_cluster_sg_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

# ---------------------------- ALB -------------------------------------------
# Referenced from the Ingress with:
#   alb.ingress.kubernetes.io/security-groups: <alb_security_group_id>
#   alb.ingress.kubernetes.io/manage-backend-security-group-rules: "false"
resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "Tycheon ALB: HTTPS in, app traffic out to the cluster"
  vpc_id      = local.vpc_id
  tags        = merge(local.tags, { Name = "${local.name}-alb" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each = toset(var.alb_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from ${each.value}"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  tags              = local.tags
}

resource "aws_vpc_security_group_egress_rule" "alb_to_cluster" {
  security_group_id            = aws_security_group.alb.id
  description                  = "App and health-check traffic to pods/nodes"
  referenced_security_group_id = local.eks_cluster_sg_id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
  tags                         = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "cluster_from_alb" {
  security_group_id            = local.eks_cluster_sg_id
  description                  = "App traffic from the Tycheon ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
  tags                         = local.tags
}

# ---------------------------- RDS -------------------------------------------
resource "aws_security_group" "db" {
  name_prefix = "${local.name}-db-"
  description = "Tycheon PostgreSQL: 5432 from EKS only, no egress"
  vpc_id      = local.vpc_id
  tags        = merge(local.tags, { Name = "${local.name}-db" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_cluster" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from EKS pods/nodes"
  referenced_security_group_id = local.eks_cluster_sg_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  tags                         = local.tags
}

# ---------------------------- Redis -----------------------------------------
resource "aws_security_group" "redis" {
  name_prefix = "${local.name}-redis-"
  description = "Tycheon Redis: 6379 from EKS only, no egress"
  vpc_id      = local.vpc_id
  tags        = merge(local.tags, { Name = "${local.name}-redis" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_cluster" {
  security_group_id            = aws_security_group.redis.id
  description                  = "Redis (TLS) from EKS pods/nodes"
  referenced_security_group_id = local.eks_cluster_sg_id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
  tags                         = local.tags
}

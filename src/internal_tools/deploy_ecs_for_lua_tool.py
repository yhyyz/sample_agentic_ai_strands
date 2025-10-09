#!/usr/bin/env python3
"""
ECS Clickstream Deployment Module
"""

import json
import time
import argparse
import logging
from typing import Optional, List, Dict, Any
from .aws_session import create_aws_session
from strands import tool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ECSClickstreamDeployer:
    """ECS Clickstream deployment orchestrator"""
    
    def __init__(self, region: str = "us-east-1"):
        """Initialize deployer with AWS session"""
        self.region = region
        self.session = create_aws_session(region=region)
        self.ecs_client = self.session.client('ecs')
        self.ec2_client = self.session.client('ec2')
        self.iam_client = self.session.client('iam')
        self.s3_client = self.session.client('s3')
        self.logs_client = self.session.client('logs')
        self.sts_client = self.session.client('sts')
    
    def get_account_id(self) -> str:
        """Get AWS account ID"""
        response = self.sts_client.get_caller_identity()
        return response['Account']
    
    def get_private_subnets(self, vpc_id: str) -> List[str]:
        """Get private subnets from VPC"""
        try:
            # Try to find subnets with 'private' in name
            response = self.ec2_client.describe_subnets(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'tag:Name', 'Values': ['*private*']}
                ]
            )
            
            if response['Subnets']:
                subnets = [subnet['SubnetId'] for subnet in response['Subnets']]
                logger.info(f"Found private subnets by name: {subnets}")
                return subnets
            
            # Fallback: subnets without public IP assignment
            response = self.ec2_client.describe_subnets(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'map-public-ip-on-launch', 'Values': ['false']}
                ]
            )
            
            if response['Subnets']:
                subnets = [subnet['SubnetId'] for subnet in response['Subnets']]
                logger.info(f"Found private subnets by public IP setting: {subnets}")
                return subnets
            
            # Final fallback: all subnets in VPC
            response = self.ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            subnets = [subnet['SubnetId'] for subnet in response['Subnets']]
            logger.warning(f"Using all subnets in VPC as fallback: {subnets}")
            return subnets
            
        except Exception as e:
            raise RuntimeError(f"Failed to get subnets for VPC {vpc_id}: {str(e)}")
    
    def create_default_security_group(self, vpc_id: str) -> str:
        """Create default security group for ECS service"""
        sg_name = "clickstream-ecs-sg"
        
        try:
            # Check if security group exists
            response = self.ec2_client.describe_security_groups(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'group-name', 'Values': [sg_name]}
                ]
            )
            if response['SecurityGroups']:
                sg_id = response['SecurityGroups'][0]['GroupId']
                logger.info(f"Using existing security group: {sg_id}")
                return sg_id
        except Exception as e:
            logger.warning(f"Error checking existing security group: {str(e)}")
        
        try:
            # Create new security group
            response = self.ec2_client.create_security_group(
                GroupName=sg_name,
                Description="Security group for clickstream ECS service",
                VpcId=vpc_id
            )
            sg_id = response['GroupId']
            logger.info(f"Created new security group: {sg_id}")
            
            # Add ingress rule
            self.ec2_client.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    'IpProtocol': 'tcp',
                    'FromPort': 8802,
                    'ToPort': 8802,
                    'UserIdGroupPairs': [{'GroupId': sg_id}]
                }]
            )
            logger.info(f"Added ingress rule to security group {sg_id}")
            
            return sg_id
        except Exception as e:
            raise RuntimeError(f"Failed to create security group: {str(e)}")
    
    def create_iam_roles(self, cluster_name: str, s3_bucket: str) -> None:
        """Create necessary IAM roles"""
        logger.info("Creating IAM roles...")
        
        # Trust policy for ECS tasks
        ecs_trust_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        
        # Create ECS Task Execution Role
        execution_role_name = f"ecsTaskExecutionRole-{cluster_name}"
        try:
            self.iam_client.get_role(RoleName=execution_role_name)
            logger.info(f"ECS Task Execution Role already exists: {execution_role_name}")
        except self.iam_client.exceptions.NoSuchEntityException:
            logger.info(f"Creating ECS Task Execution Role: {execution_role_name}")
            self.iam_client.create_role(
                RoleName=execution_role_name,
                AssumeRolePolicyDocument=json.dumps(ecs_trust_policy)
            )
            self.iam_client.attach_role_policy(
                RoleName=execution_role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to create execution role: {str(e)}")
        
        # Create ECS Task Role
        task_role_name = f"ecsTaskRole-{cluster_name}"
        try:
            self.iam_client.get_role(RoleName=task_role_name)
            logger.info(f"ECS Task Role already exists: {task_role_name}")
        except self.iam_client.exceptions.NoSuchEntityException:
            logger.info(f"Creating ECS Task Role: {task_role_name}")
            self.iam_client.create_role(
                RoleName=task_role_name,
                AssumeRolePolicyDocument=json.dumps(ecs_trust_policy)
            )
            
            # Task role policy
            task_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"
                        ],
                        "Resource": [
                            f"arn:aws:s3:::{s3_bucket}",
                            f"arn:aws:s3:::{s3_bucket}/*"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup", "logs:CreateLogStream",
                            "logs:PutLogEvents", "logs:DescribeLogStreams"
                        ],
                        "Resource": "*"
                    }
                ]
            }
            
            self.iam_client.put_role_policy(
                RoleName=task_role_name,
                PolicyName="ClickstreamTaskPolicy",
                PolicyDocument=json.dumps(task_policy)
            )
        except Exception as e:
            raise RuntimeError(f"Failed to create task role: {str(e)}")
        
        # Create ECS Infrastructure Role
        infra_role_name = f"ecsInfrastructureRole-{cluster_name}"
        try:
            self.iam_client.get_role(RoleName=infra_role_name)
            logger.info(f"ECS Infrastructure Role already exists: {infra_role_name}")
        except self.iam_client.exceptions.NoSuchEntityException:
            logger.info(f"Creating ECS Infrastructure Role: {infra_role_name}")
            ecs_infra_trust_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }
            
            self.iam_client.create_role(
                RoleName=infra_role_name,
                AssumeRolePolicyDocument=json.dumps(ecs_infra_trust_policy),
                Description="ECS Infrastructure Role for EBS volume management"
            )
            
            # EBS policy
            ebs_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": [
                        "ec2:CreateVolume", "ec2:DeleteVolume", "ec2:AttachVolume",
                        "ec2:DetachVolume", "ec2:ModifyVolume", "ec2:DescribeVolumes",
                        "ec2:DescribeVolumeStatus", "ec2:DescribeVolumeAttribute",
                        "ec2:CreateSnapshot", "ec2:DeleteSnapshot", "ec2:DescribeSnapshots",
                        "ec2:CreateTags", "ec2:DescribeTags", "ec2:DescribeAvailabilityZones",
                        "ec2:DescribeInstances", "ec2:DescribeInstanceTypes",
                        "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"
                    ],
                    "Resource": "*"
                }]
            }
            
            self.iam_client.put_role_policy(
                RoleName=infra_role_name,
                PolicyName="ECSInfrastructureRoleForEBS",
                PolicyDocument=json.dumps(ebs_policy)
            )
        except Exception as e:
            raise RuntimeError(f"Failed to create infrastructure role: {str(e)}")
        
        # Wait for roles to be available
        logger.info("Waiting for IAM roles to be available...")
        time.sleep(10)
    
    def create_s3_bucket(self, bucket_name: str) -> None:
        """Create S3 bucket if it doesn't exist"""
        logger.info(f"Creating S3 bucket: {bucket_name}")
        
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            logger.info(f"S3 bucket {bucket_name} already exists")
            return
        except self.s3_client.exceptions.NoSuchBucket:
            pass
        except Exception as e:
            raise RuntimeError(f"Error checking S3 bucket {bucket_name}: {str(e)}")
        
        try:
            if self.region == "us-east-1":
                self.s3_client.create_bucket(Bucket=bucket_name)
            else:
                self.s3_client.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': self.region}
                )
            
            # Enable versioning
            self.s3_client.put_bucket_versioning(
                Bucket=bucket_name,
                VersioningConfiguration={'Status': 'Enabled'}
            )
            logger.info(f"Created S3 bucket {bucket_name} with versioning enabled")
        except Exception as e:
            raise RuntimeError(f"Failed to create S3 bucket {bucket_name}: {str(e)}")
    
    def create_log_group(self, cluster_name: str) -> None:
        """Create CloudWatch log group"""
        log_group_name = f"/ecs/{cluster_name}"
        logger.info(f"Creating CloudWatch log group: {log_group_name}")
        
        try:
            self.logs_client.create_log_group(logGroupName=log_group_name)
            logger.info(f"Created log group: {log_group_name}")
        except self.logs_client.exceptions.ResourceAlreadyExistsException:
            logger.info(f"Log group already exists: {log_group_name}")
        except Exception as e:
            raise RuntimeError(f"Failed to create log group {log_group_name}: {str(e)}")
    
    def create_ecs_cluster(self, cluster_name: str) -> None:
        """Create ECS cluster if it doesn't exist"""
        logger.info(f"Checking ECS cluster: {cluster_name}")
        
        try:
            response = self.ecs_client.describe_clusters(clusters=[cluster_name])
            if response['clusters'] and response['clusters'][0]['status'] == 'ACTIVE':
                logger.info(f"ECS cluster {cluster_name} already exists")
                return
        except Exception as e:
            logger.warning(f"Error checking cluster: {str(e)}")
        
        try:
            self.ecs_client.create_cluster(clusterName=cluster_name)
            logger.info(f"Created ECS cluster: {cluster_name}")
        except Exception as e:
            raise RuntimeError(f"Failed to create ECS cluster {cluster_name}: {str(e)}")
    
    def generate_task_definition(self, task_family: str, cluster_name: str, 
                               s3_bucket: str, kafka_broker_host: str, 
                               kafka_broker_port: str, ebs_size: int,
                               clickstream_image: str, fluent_bit_image: str) -> Dict[str, Any]:
        """Generate ECS task definition"""
        account_id = self.get_account_id()
        
        return {
            "family": task_family,
            "taskRoleArn": f"arn:aws:iam::{account_id}:role/ecsTaskRole-{cluster_name}",
            "executionRoleArn": f"arn:aws:iam::{account_id}:role/ecsTaskExecutionRole-{cluster_name}",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "8192",
            "memory": "16384",
            "containerDefinitions": [
                {
                    "name": "clickstream-container",
                    "image": clickstream_image,
                    "cpu": 6144,
                    "memory": 12288,
                    "portMappings": [{
                        "containerPort": 8802,
                        "hostPort": 8802,
                        "protocol": "tcp"
                    }],
                    "essential": True,
                    "healthCheck": {
                        "command": ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:8802/health || exit 1"],
                        "interval": 30,
                        "timeout": 5,
                        "retries": 3,
                        "startPeriod": 60
                    },
                    "environment": [
                        {"name": "KAFKA_BROKER_HOST", "value": kafka_broker_host},
                        {"name": "KAFKA_BROKER_PORT", "value": kafka_broker_port},
                        {"name": "SEND_S3_ONLY", "value": "disable"},
                        {"name": "S3_BUCKET", "value": s3_bucket}
                    ],
                    "mountPoints": [{
                        "sourceVolume": "clickstream-ebs-volume",
                        "containerPath": "/opt/app/collect-app/logs",
                        "readOnly": False
                    }],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": f"/ecs/{cluster_name}",
                            "awslogs-region": self.region,
                            "awslogs-stream-prefix": "ecs"
                        }
                    }
                },
                {
                    "name": "fluent-bit",
                    "image": fluent_bit_image,
                    "cpu": 2048,
                    "memory": 4096,
                    "essential": False,
                    "mountPoints": [{
                        "sourceVolume": "clickstream-ebs-volume",
                        "containerPath": "/opt/app/collect-app/logs",
                        "readOnly": True
                    }],
                    "environment": [
                        {"name": "S3_BUCKET_NAME", "value": s3_bucket},
                        {"name": "AWS_REGION", "value": self.region}
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": f"/ecs/{cluster_name}",
                            "awslogs-region": self.region,
                            "awslogs-stream-prefix": "fluent-bit"
                        }
                    }
                }
            ],
            "volumes": [{
                "name": "clickstream-ebs-volume",
                "configuredAtLaunch": True
            }]
        }
    
    def register_task_definition(self, task_definition: Dict[str, Any]) -> str:
        """Register ECS task definition"""
        try:
            response = self.ecs_client.register_task_definition(**task_definition)
            task_arn = response['taskDefinition']['taskDefinitionArn']
            logger.info(f"Registered task definition: {task_arn}")
            return task_arn
        except Exception as e:
            raise RuntimeError(f"Failed to register task definition: {str(e)}")
    
    def deploy_service(self, cluster_name: str, service_name: str, task_family: str,
                      desired_count: int, subnets: List[str], security_groups: List[str],
                      ebs_size: int) -> None:
        """Deploy or update ECS service"""
        account_id = self.get_account_id()
        logger.info(f"Deploying ECS service: {service_name}")
        
        # Check if service exists
        try:
            response = self.ecs_client.describe_services(
                cluster=cluster_name,
                services=[service_name]
            )
            
            if response['services'] and response['services'][0]['status'] == 'ACTIVE':
                # Update existing service
                logger.info(f"Updating existing service: {service_name}")
                self.ecs_client.update_service(
                    cluster=cluster_name,
                    service=service_name,
                    taskDefinition=task_family,
                    desiredCount=desired_count
                )
                return
            elif response['services'] and response['services'][0]['status'] in ['INACTIVE', 'DRAINING']:
                # Delete and recreate
                logger.info(f"Service {service_name} is {response['services'][0]['status']}, deleting and recreating")
                self.ecs_client.delete_service(
                    cluster=cluster_name,
                    service=service_name,
                    force=True
                )
                
                # Wait for deletion
                logger.info("Waiting for service deletion...")
                waiter = self.ecs_client.get_waiter('services_inactive')
                waiter.wait(cluster=cluster_name, services=[service_name])
        except Exception as e:
            logger.warning(f"Error checking existing service: {str(e)}")
            raise RuntimeError(f"Error checking existing service: {str(e)}")
        
        # Create new service
        try:
            service_config = {
                "serviceName": service_name,
                "cluster": cluster_name,
                "taskDefinition": task_family,
                "desiredCount": desired_count,
                "launchType": "FARGATE",
                "networkConfiguration": {
                    "awsvpcConfiguration": {
                        "subnets": subnets,
                        "securityGroups": security_groups,
                        "assignPublicIp": "DISABLED"
                    }
                },
                "volumeConfigurations": [{
                    "name": "clickstream-ebs-volume",
                    "managedEBSVolume": {
                        "sizeInGiB": ebs_size,
                        "volumeType": "gp3",
                        "iops": 3000,
                        "throughput": 125,
                        "filesystemType": "ext4",
                        "roleArn": f"arn:aws:iam::{account_id}:role/ecsInfrastructureRole-{cluster_name}"
                    }
                }]
            }
            
            self.ecs_client.create_service(**service_config)
            logger.info(f"Created service: {service_name}")
            
            # Wait for service to stabilize
            logger.info("Waiting for service to stabilize...")
            waiter = self.ecs_client.get_waiter('services_stable')
            waiter.wait(cluster=cluster_name, services=[service_name])
            logger.info("Service is stable")
        except Exception as e:
            raise RuntimeError(f"Failed to deploy service {service_name}: {str(e)}")


@tool
def deploy_ecs_for_lua(
    region: str,
    s3_bucket: str,
    vpc_id: str,
    kafka_broker_host: str,
    cluster_name: str = "clickstream-cluster",
    task_family: str = "clickstream-task-optimized",
    service_name: str = "clickstream-optimized-service",
    subnets: Optional[List[str]] = None,
    security_groups: Optional[List[str]] = None,
    desired_count: int = 4,
    ebs_size: int = 50,
    kafka_broker_port: str = "9092",
    clickstream_image: Optional[str] = None,
    fluent_bit_image: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main deployment function for ECS Clickstream service, use nginx-lua and fluent-bit 
    
    Args:
        region: AWS region (required)
        s3_bucket: S3 bucket name for data storage (required)
        vpc_id: VPC ID to deploy in (required)
        kafka_broker_host: Kafka broker host (required)
        cluster_name: ECS cluster name (default: clickstream-cluster)
        task_family: Task definition family name (default: clickstream-task-optimized)
        service_name: Service name (default: clickstream-optimized-service)
        subnets: List of subnet IDs (optional, will auto-detect if not provided)
        security_groups: List of security group IDs (optional, will create default if not provided)
        desired_count: Desired number of tasks (default: 4)
        ebs_size: EBS volume size in GiB (default: 50)
        kafka_broker_port: Kafka broker port (default: 9092)
        clickstream_image: Clickstream container image (optional, uses default ECR image)
        fluent_bit_image: Fluent Bit container image (optional, uses default ECR image)
    
    Returns:
        Dict containing deployment status and configuration
    """
    try: 
        deployer = ECSClickstreamDeployer(region)
        account_id = deployer.get_account_id()
        
        # Set default images if not provided
        if not clickstream_image:
            clickstream_image = f"{account_id}.dkr.ecr.{region}.amazonaws.com/clickstream-openresty-lua-msk-optimized:latest"
        if not fluent_bit_image:
            fluent_bit_image = f"{account_id}.dkr.ecr.{region}.amazonaws.com/custom-fluent-bit-optimized:latest"
        
        logger.info("=== ECS Clickstream Deployment ===")
        logger.info(f"Region: {region}")
        logger.info(f"Cluster: {cluster_name}")
        logger.info(f"Service: {service_name}")
        logger.info(f"S3 Bucket: {s3_bucket}")
        logger.info(f"VPC: {vpc_id}")
        logger.info(f"Kafka Broker: {kafka_broker_host}:{kafka_broker_port}")
        logger.info(f"Clickstream Image: {clickstream_image}")
        logger.info(f"Fluent Bit Image: {fluent_bit_image}")
        
        # Auto-detect subnets if not provided
        if not subnets:
            subnets = deployer.get_private_subnets(vpc_id)
            if not subnets:
                raise ValueError(f"No subnets found in VPC {vpc_id}")
        
        # Create default security group if not provided
        if not security_groups:
            sg_id = deployer.create_default_security_group(vpc_id)
            security_groups = [sg_id]
        
        logger.info(f"Using subnets: {subnets}")
        logger.info(f"Using security groups: {security_groups}")
        
        # Create resources
        deployer.create_iam_roles(cluster_name, s3_bucket)
        deployer.create_s3_bucket(s3_bucket)
        deployer.create_log_group(cluster_name)
        deployer.create_ecs_cluster(cluster_name)
        
        # Generate and register task definition
        task_def = deployer.generate_task_definition(
            task_family, cluster_name, s3_bucket, 
            kafka_broker_host, kafka_broker_port, ebs_size,
            clickstream_image, fluent_bit_image
        )
        task_arn = deployer.register_task_definition(task_def)
        
        # Deploy service
        deployer.deploy_service(
            cluster_name, service_name, task_family,
            desired_count, subnets, security_groups, ebs_size
        )
        
        # Get final service status
        response = deployer.ecs_client.describe_services(
            cluster=cluster_name,
            services=[service_name]
        )
        
        service_info = response['services'][0]
        
        result = {
            'status': 'success',
            'account_id': account_id,
            'task_definition_arn': task_arn,
            'service_status': service_info['status'],
            'running_count': service_info['runningCount'],
            'desired_count': service_info['desiredCount'],
            'subnets': subnets,
            'security_groups': security_groups
        }
        
        logger.info("=== Deployment Complete ===")
        logger.info(f"Status: {result['status']}")
        logger.info(f"Service Status: {result['service_status']}")
        logger.info(f"Running/Desired: {result['running_count']}/{result['desired_count']}")
    
        return result
    except Exception as e:
        return {"error": f"Failed deploy ecs for lua {e}"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy ECS Clickstream service")
    parser.add_argument("--region", required=True, help="AWS region")
    parser.add_argument("--s3-bucket", required=True, help="S3 bucket name")
    parser.add_argument("--vpc-id", required=True, help="VPC ID")
    parser.add_argument("--kafka-broker-host", required=True, help="Kafka broker host")
    parser.add_argument("--cluster-name", default="clickstream-cluster", help="ECS cluster name")
    parser.add_argument("--desired-count", type=int, default=4, help="Desired task count")
    parser.add_argument("--ebs-size", type=int, default=50, help="EBS volume size in GiB")
    parser.add_argument("--clickstream-image", help="Clickstream container image")
    parser.add_argument("--fluent-bit-image", help="Fluent Bit container image")
    
    args = parser.parse_args()
    
    try:
        result = deploy_ecs_clickstream(
            region=args.region,
            s3_bucket=args.s3_bucket,
            vpc_id=args.vpc_id,
            kafka_broker_host=args.kafka_broker_host,
            cluster_name=args.cluster_name,
            desired_count=args.desired_count,
            ebs_size=args.ebs_size,
            clickstream_image=args.clickstream_image,
            fluent_bit_image=args.fluent_bit_image
        )
        
        logger.info("Deployment completed successfully!")
        
    except Exception as e:
        logger.error(f"Deployment failed: {str(e)}")
        exit(1)

#!/usr/bin/env python3
"""
ECS ALB+Nginx+Vector Clickstream Deployment Script
Refactored from bash to Python for production use
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from .aws_session import create_aws_session
from strands import tool



# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class DeploymentConfig:
    """Configuration for ECS deployment"""
    region: str = "us-east-1"
    cluster_name: str = "clickstream-alb-cluster"
    task_family: str = "clickstream-alb-task-optimized"
    service_name: str = "clickstream-alb-optimized-service"
    desired_count: int = 4
    ebs_size: int = 200
    kafka_broker_port: str = "9092"
    msk_topic: str = "app_logs"
    
    # Required parameters
    vpc_id: Optional[str] = None
    kafka_broker_host: Optional[str] = None
    
    # Optional parameters
    subnets: Optional[str] = None
    security_groups: Optional[str] = None


class ECSDeployer:
    """ECS deployment manager"""
    
    def __init__(self, config: DeploymentConfig):
        self.config = config
        self.session = create_aws_session(region=config.region)
        self.ecs_client = self.session.client('ecs')
        self.ec2_client = self.session.client('ec2')
        self.iam_client = self.session.client('iam')
        self.logs_client = self.session.client('logs')
        self.sts_client = self.session.client('sts')
        
    def get_account_id(self) -> str:
        """Get AWS account ID"""
        try:
            response = self.sts_client.get_caller_identity()
            return response['Account']
        except ClientError as e:
            logger.error(f"Failed to get account ID: {e}")
            raise
    
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
                return [subnet['SubnetId'] for subnet in response['Subnets']]
            
            # Fallback: get subnets without public IP assignment
            response = self.ec2_client.describe_subnets(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'map-public-ip-on-launch', 'Values': ['false']}
                ]
            )
            
            if response['Subnets']:
                return [subnet['SubnetId'] for subnet in response['Subnets']]
            
            # Final fallback: get all subnets in VPC
            response = self.ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            return [subnet['SubnetId'] for subnet in response['Subnets']]
            
        except ClientError as e:
            logger.error(f"Failed to get private subnets: {e}")
            raise
    
    def create_default_security_group(self, vpc_id: str) -> str:
        """Create default security group"""
        sg_name = "clickstream-alb-ecs-sg"
        
        try:
            # Check if security group already exists
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
            
            # Create new security group
            response = self.ec2_client.create_security_group(
                GroupName=sg_name,
                Description="Security group for clickstream ALB ECS service",
                VpcId=vpc_id
            )
            
            sg_id = response['GroupId']
            logger.info(f"Created security group: {sg_id}")
            logger.warning("Security group rules need to be configured separately")
            
            return sg_id
            
        except ClientError as e:
            logger.error(f"Failed to create security group: {e}")
            raise
    
    def create_iam_roles(self, account_id: str) -> None:
        """Create necessary IAM roles"""
        logger.info("Creating IAM roles...")
        
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        # Create ECS Task Execution Role
        execution_role_name = f"ecsTaskExecutionRole-{self.config.cluster_name}"
        self._create_role_if_not_exists(
            execution_role_name,
            trust_policy,
            managed_policies=["arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"]
        )
        
        # Create ECS Task Role
        task_role_name = f"ecsTaskRole-{self.config.cluster_name}"
        task_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogStreams"
                    ],
                    "Resource": "*"
                }
            ]
        }
        
        self._create_role_if_not_exists(
            task_role_name,
            trust_policy,
            inline_policies={"ClickstreamALBTaskPolicy": task_policy}
        )
        
        # Create ECS Infrastructure Role for EBS
        infra_role_name = f"ecsInfrastructureRole-{self.config.cluster_name}"
        infra_trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        ebs_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
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
                }
            ]
        }
        
        self._create_role_if_not_exists(
            infra_role_name,
            infra_trust_policy,
            inline_policies={"ECSInfrastructureRoleForEBS": ebs_policy}
        )
        
        logger.info("Waiting for IAM roles to be available...")
        time.sleep(10)
    
    def _create_role_if_not_exists(self, role_name: str, trust_policy: Dict[str, Any],
                                   managed_policies: Optional[List[str]] = None,
                                   inline_policies: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        """Create IAM role if it doesn't exist"""
        try:
            self.iam_client.get_role(RoleName=role_name)
            logger.info(f"IAM role {role_name} already exists")
            return
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchEntity':
                raise
        
        # Create role
        self.iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        )
        logger.info(f"Created IAM role: {role_name}")
        
        # Attach managed policies
        if managed_policies:
            for policy_arn in managed_policies:
                self.iam_client.attach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy_arn
                )
        
        # Add inline policies
        if inline_policies:
            for policy_name, policy_doc in inline_policies.items():
                self.iam_client.put_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(policy_doc)
                )
    
    def create_log_group(self) -> None:
        """Create CloudWatch log group"""
        log_group_name = f"/ecs/{self.config.cluster_name}"
        
        try:
            self.logs_client.create_log_group(logGroupName=log_group_name)
            logger.info(f"Created CloudWatch log group: {log_group_name}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                logger.info(f"Log group {log_group_name} already exists")
            else:
                logger.error(f"Failed to create log group: {e}")
                raise
    
    def create_ecs_cluster(self) -> None:
        """Create ECS cluster if it doesn't exist"""
        try:
            response = self.ecs_client.describe_clusters(clusters=[self.config.cluster_name])
            if response['clusters'] and response['clusters'][0]['clusterName'] == self.config.cluster_name:
                logger.info(f"ECS cluster {self.config.cluster_name} already exists")
                return
        except ClientError:
            raise
        
        self.ecs_client.create_cluster(clusterName=self.config.cluster_name)
        logger.info(f"Created ECS cluster: {self.config.cluster_name}")
    
    def generate_task_definition(self, account_id: str) -> Dict[str, Any]:
        """Generate ECS task definition"""
        task_role_arn = f"arn:aws:iam::{account_id}:role/ecsTaskRole-{self.config.cluster_name}"
        execution_role_arn = f"arn:aws:iam::{account_id}:role/ecsTaskExecutionRole-{self.config.cluster_name}"
        log_group_name = f"/ecs/{self.config.cluster_name}"
        
        return {
            "family": self.config.task_family,
            "taskRoleArn": task_role_arn,
            "executionRoleArn": execution_role_arn,
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": "8192",
            "memory": "16384",
            "containerDefinitions": [
                {
                    "name": "nginx-container",
                    "image": f"{account_id}.dkr.ecr.{self.config.region}.amazonaws.com/clickstream-nginx-vector-optimized:latest",
                    "cpu": 2048,
                    "memory": 4096,
                    "portMappings": [{"containerPort": 8802, "hostPort": 8802, "protocol": "tcp"}],
                    "essential": True,
                    "healthCheck": {
                        "command": ["CMD-SHELL", "curl -f http://localhost:8802/health || exit 1"],
                        "interval": 30,
                        "timeout": 5,
                        "retries": 3,
                        "startPeriod": 60
                    },
                    "environment": [
                        {"name": "SERVER_ENDPOINT_PATH", "value": "/data/v1"},
                        {"name": "PING_ENDPOINT_PATH", "value": "/ping"},
                        {"name": "SERVER_CORS_ORIGIN", "value": "*"},
                        {"name": "NGINX_WORKER_CONNECTIONS", "value": "1024"}
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": log_group_name,
                            "awslogs-region": self.config.region,
                            "awslogs-stream-prefix": "nginx"
                        }
                    }
                },
                {
                    "name": "vector-container",
                    "image": f"{account_id}.dkr.ecr.{self.config.region}.amazonaws.com/clickstream-vector-optimized:latest",
                    "cpu": 6144,
                    "memory": 12288,
                    "essential": True,
                    "environment": [
                        {"name": "AWS_REGION", "value": self.config.region},
                        {"name": "AWS_MSK_BROKERS", "value": f"{self.config.kafka_broker_host}:{self.config.kafka_broker_port}"},
                        {"name": "AWS_MSK_TOPIC", "value": self.config.msk_topic},
                        {"name": "STREAM_ACK_ENABLE", "value": "false"},
                        {"name": "VECTOR_REQUIRE_HEALTHY", "value": "false"},
                        {"name": "WORKER_THREADS_NUM", "value": "-1"}
                    ],
                    "mountPoints": [
                        {"sourceVolume": "vector-ebs-volume", "containerPath": "/var/lib/vector", "readOnly": False}
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": log_group_name,
                            "awslogs-region": self.config.region,
                            "awslogs-stream-prefix": "vector"
                        }
                    }
                }
            ],
            "volumes": [{"name": "vector-ebs-volume", "configuredAtLaunch": True}]
        }
    
    def generate_service_definition(self, account_id: str, subnets: List[str], security_groups: List[str]) -> Dict[str, Any]:
        """Generate ECS service definition"""
        infra_role_arn = f"arn:aws:iam::{account_id}:role/ecsInfrastructureRole-{self.config.cluster_name}"
        
        return {
            "serviceName": self.config.service_name,
            "cluster": self.config.cluster_name,
            "taskDefinition": self.config.task_family,
            "desiredCount": self.config.desired_count,
            "launchType": "FARGATE",
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": security_groups,
                    "assignPublicIp": "DISABLED"
                }
            },
            "volumeConfigurations": [
                {
                    "name": "vector-ebs-volume",
                    "managedEBSVolume": {
                        "sizeInGiB": self.config.ebs_size,
                        "volumeType": "gp3",
                        "iops": 5000,
                        "throughput": 600,
                        "filesystemType": "ext4",
                        "roleArn": infra_role_arn
                    }
                }
            ]
        }
    
    def deploy_service(self, service_definition: Dict[str, Any]) -> None:
        """Deploy or update ECS service"""
        try:
            # Check if service exists
            response = self.ecs_client.describe_services(
                cluster=self.config.cluster_name,
                services=[self.config.service_name]
            )
            
            if response['services']:
                service = response['services'][0]
                service_status = service['status']
                
                if service_status == 'ACTIVE':
                    logger.info("Updating existing active service...")
                    self.ecs_client.update_service(
                        cluster=self.config.cluster_name,
                        service=self.config.service_name,
                        taskDefinition=self.config.task_family,
                        desiredCount=self.config.desired_count
                    )
                elif service_status in ['INACTIVE', 'DRAINING']:
                    logger.info(f"Service exists but is {service_status}. Deleting and recreating...")
                    self._delete_and_recreate_service(service_definition)
                else:
                    logger.info("Creating new service...")
                    self.ecs_client.create_service(**service_definition)
            else:
                logger.info("Creating new service...")
                self.ecs_client.create_service(**service_definition)
                
        except ClientError as e:
            logger.error(f"Failed to deploy service: {e}")
            raise
    
    def _delete_and_recreate_service(self, service_definition: Dict[str, Any]) -> None:
        """Delete existing service and create new one"""
        # Force delete the service
        self.ecs_client.delete_service(
            cluster=self.config.cluster_name,
            service=self.config.service_name,
            force=True
        )
        
        # Wait for deletion to complete
        logger.info("Waiting for service deletion to complete...")
        for _ in range(30):
            try:
                response = self.ecs_client.describe_services(
                    cluster=self.config.cluster_name,
                    services=[self.config.service_name]
                )
                if not response['services']:
                    logger.info("Service successfully deleted.")
                    break
            except ClientError:
                break
            time.sleep(10)
        
        # Create new service
        logger.info("Creating new service...")
        self.ecs_client.create_service(**service_definition)
    
    def wait_for_service_stable(self) -> None:
        """Wait for service to stabilize"""
        logger.info("Waiting for service to stabilize...")
        waiter = self.ecs_client.get_waiter('services_stable')
        waiter.wait(
            cluster=self.config.cluster_name,
            services=[self.config.service_name]
        )
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get service status"""
        response = self.ecs_client.describe_services(
            cluster=self.config.cluster_name,
            services=[self.config.service_name]
        )
        
        if response['services']:
            service = response['services'][0]
            return {
                'ServiceName': service['serviceName'],
                'Status': service['status'],
                'RunningCount': service['runningCount'],
                'DesiredCount': service['desiredCount']
            }
        return {}
    
    def save_service_info(self, account_id: str, task_def_arn: str, subnets: List[str], security_groups: List[str]) -> None:
        """Save service information to JSON file"""
        service_info = {
            "cluster_name": self.config.cluster_name,
            "service_name": self.config.service_name,
            "task_family": self.config.task_family,
            "region": self.config.region,
            "vpc_id": self.config.vpc_id,
            "subnets": ",".join(subnets),
            "security_groups": ",".join(security_groups),
            "desired_count": self.config.desired_count,
            "kafka_broker": f"{self.config.kafka_broker_host}:{self.config.kafka_broker_port}",
            "msk_topic": self.config.msk_topic,
            "task_definition_arn": task_def_arn,
            "deployment_time": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        
        with open('ecs-service-info.json', 'w') as f:
            json.dump(service_info, f, indent=2)
        
        logger.info("Service information saved to ecs-service-info.json")

@tool
def deploy_ecs_service_for_vector(
    vpc_id: str,
    kafka_broker_host: str,
    region: str,
    cluster_name: str = "clickstream-alb-cluster",
    task_family: str = "clickstream-alb-task-optimized",
    service_name: str = "clickstream-alb-optimized-service",
    desired_count: int = 4,
    ebs_size: int = 200,
    kafka_broker_port: str = "9092",
    msk_topic: str = "app_logs",
    subnets: Optional[str] = None,
    security_groups: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main deployment function for ECS Clickstream service, use nginx and vector 
    
    Args:
        vpc_id: VPC ID to deploy in (required)
        kafka_broker_host: Kafka broker host (required)
        region: AWS region  (required)
        cluster_name: ECS cluster name  (optional, default: clickstream-alb-cluster )
        task_family: Task definition family name  (optional, default: "clickstream-alb-task-optimized)
        service_name: Service name  (optional, default: clickstream-alb-optimized-service)
        desired_count: Desired number of tasks  (optional, default: 4)
        ebs_size: EBS volume size in GiB  (optional, default: 200)
        kafka_broker_port: Kafka broker port  (optional, default: 9092)
        msk_topic: MSK topic name  (optional, default: app_logs)
        subnets: Comma-separated subnet IDs (optional)
        security_groups: Comma-separated security group IDs (optional)
    
    Returns:
        Dict containing deployment results
    """
    config = DeploymentConfig(
        region=region,
        cluster_name=cluster_name,
        task_family=task_family,
        service_name=service_name,
        desired_count=desired_count,
        ebs_size=ebs_size,
        kafka_broker_port=kafka_broker_port,
        msk_topic=msk_topic,
        vpc_id=vpc_id,
        kafka_broker_host=kafka_broker_host,
        subnets=subnets,
        security_groups=security_groups
    )
    
    logger.info("=== ECS ALB+Nginx+Vector Clickstream Deployment ===")
    logger.info(f"Region: {config.region}")
    logger.info(f"Cluster: {config.cluster_name}")
    logger.info(f"Service: {config.service_name}")
    logger.info(f"VPC: {config.vpc_id}")
    logger.info(f"Desired Count: {config.desired_count}")
    logger.info(f"Kafka Broker: {config.kafka_broker_host}:{config.kafka_broker_port}")
    
    deployer = ECSDeployer(config)
    
    try:
        # Get AWS account ID
        account_id = deployer.get_account_id()
        logger.info(f"AWS Account ID: {account_id}")
        
        # Auto-detect subnets if not provided
        subnet_list = []
        if config.subnets:
            subnet_list = config.subnets.split(',')
        else:
            logger.info(f"Auto-detecting private subnets in VPC {config.vpc_id}...")
            subnet_list = deployer.get_private_subnets(config.vpc_id)
            if not subnet_list:
                raise ValueError(f"No subnets found in VPC {config.vpc_id}")
        
        logger.info(f"Using subnets: {subnet_list}")
        
        # Create default security group if not provided
        sg_list = []
        if config.security_groups:
            sg_list = config.security_groups.split(',')
        else:
            logger.info("Creating default security group...")
            sg_id = deployer.create_default_security_group(config.vpc_id)
            sg_list = [sg_id]
        
        logger.info(f"Using security groups: {sg_list}")
        
        # Create IAM roles
        deployer.create_iam_roles(account_id)
        
        # Create CloudWatch log group
        deployer.create_log_group()
        
        # Create ECS cluster
        deployer.create_ecs_cluster()
        
        # Generate and register task definition
        logger.info("Registering ECS task definition...")
        task_definition = deployer.generate_task_definition(account_id)
        
        response = deployer.ecs_client.register_task_definition(**task_definition)
        task_def_arn = response['taskDefinition']['taskDefinitionArn']
        logger.info(f"Task definition registered: {task_def_arn}")
        
        # Generate and deploy service
        logger.info("Deploying ECS service...")
        service_definition = deployer.generate_service_definition(account_id, subnet_list, sg_list)
        deployer.deploy_service(service_definition)
        
        # Wait for service to stabilize
        deployer.wait_for_service_stable()
        
        # Get service status
        service_status = deployer.get_service_status()
        logger.info(f"Service Status: {service_status}")
        
        # Save service information
        # deployer.save_service_info(account_id, task_def_arn, subnet_list, sg_list)
        
        logger.info("=== Deployment Complete ===")
        
        return {
            'status': 'success',
            'account_id': account_id,
            'task_definition_arn': task_def_arn,
            'service_status': service_status,
            'subnets': subnet_list,
            'security_groups': sg_list
        }
        
    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }


if __name__ == "__main__":
    # Test deployment with sample parameters
    result = deploy_ecs_service_for_vector(
        vpc_id="vpc-12345678",  # Replace with actual VPC ID
        kafka_broker_host="test-kafka-host.amazonaws.com",  # Replace with actual Kafka host
        region="us-east-1",
        desired_count=2  # Reduced for testing
    )
    
    if result['status'] == 'success':
        logger.info("Test deployment completed successfully!")
    else:
        logger.error(f"Test deployment failed: {result['error']}")

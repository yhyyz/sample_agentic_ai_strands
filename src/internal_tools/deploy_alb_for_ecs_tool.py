#!/usr/bin/env python3
"""
ALB Deployment Script - Python Implementation
Refactored from deploy-alb-optimized.sh to production-level Python code
"""

import json
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple
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


class ALBDeploymentError(Exception):
    """Custom exception for ALB deployment errors"""
    pass


class ALBDeployer:
    """ALB Deployment Manager"""
    
    def __init__(self, region: str = "us-east-1", profile: Optional[str] = None):
        """Initialize ALB Deployer with AWS session"""
        self.region = region
        self.session = create_aws_session(region=region, aws_profile=profile)
        self.ec2_client = self.session.client('ec2')
        self.ecs_client = self.session.client('ecs')
        self.elbv2_client = self.session.client('elbv2')
        
    def get_ecs_service_info(self, cluster_name: str, service_name: str) -> Dict:
        """Get ECS service information and derive network configuration"""
        logger.info(f"Getting ECS service info for {service_name} in cluster {cluster_name}")
        
        try:
            # Check if ECS service exists
            response = self.ecs_client.describe_services(
                cluster=cluster_name,
                services=[service_name]
            )
            
            if not response['services'] or response['services'][0]['status'] != 'ACTIVE':
                raise ALBDeploymentError(f"ECS service {service_name} not found or not active in cluster {cluster_name}")
            
            service = response['services'][0]
            
            # Get subnets from service network configuration
            network_config = service.get('networkConfiguration', {}).get('awsvpcConfiguration', {})
            ecs_subnets = network_config.get('subnets', [])
            
            if not ecs_subnets:
                raise ALBDeploymentError("Unable to get ECS service subnet information")
            
            logger.info(f"ECS service subnets: {ecs_subnets}")
            
            # Get VPC ID from first subnet
            subnet_response = self.ec2_client.describe_subnets(SubnetIds=[ecs_subnets[0]])
            vpc_id = subnet_response['Subnets'][0]['VpcId']
            logger.info(f"Detected VPC ID: {vpc_id}")
            
            # Get availability zones of ECS subnets
            ecs_azs = []
            for subnet_id in ecs_subnets:
                subnet_info = self.ec2_client.describe_subnets(SubnetIds=[subnet_id])
                ecs_azs.append(subnet_info['Subnets'][0]['AvailabilityZone'])
            
            ecs_azs = list(set(ecs_azs))  # Remove duplicates
            logger.info(f"ECS service availability zones: {ecs_azs}")
            
            # Get task definition to extract container info
            task_def_arn = service['taskDefinition']
            task_def_response = self.ecs_client.describe_task_definition(taskDefinition=task_def_arn)
            container_def = task_def_response['taskDefinition']['containerDefinitions'][0]
            
            container_port = 8802  # Default
            container_name = "nginx-container"  # Default
            
            if container_def.get('portMappings'):
                container_port = container_def['portMappings'][0]['containerPort']
            
            container_name = container_def['name']
            
            logger.info(f"Container name: {container_name}, port: {container_port}")
            
            return {
                'vpc_id': vpc_id,
                'ecs_subnets': ecs_subnets,
                'ecs_azs': ecs_azs,
                'container_port': container_port,
                'container_name': container_name
            }
            
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to get ECS service info: {e}")
    
    def get_public_subnets(self, vpc_id: str, ecs_azs: List[str]) -> List[str]:
        """Get public subnets in the same AZs as ECS service"""
        logger.info("Getting public subnets in ECS service availability zones")
        
        try:
            # Get all subnets in VPC
            response = self.ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            public_subnets = []
            
            for subnet in response['Subnets']:
                subnet_id = subnet['SubnetId']
                subnet_az = subnet['AvailabilityZone']
                
                # Check if subnet is in ECS AZs
                if subnet_az not in ecs_azs:
                    continue
                
                # Check if subnet is public by examining route tables
                if self._is_public_subnet(subnet_id, vpc_id):
                    public_subnets.append(subnet_id)
                    logger.info(f"Found public subnet: {subnet_id} (AZ: {subnet_az})")
            
            if not public_subnets:
                raise ALBDeploymentError(f"No public subnets found in ECS availability zones: {ecs_azs}")
            
            return public_subnets
            
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to get public subnets: {e}")
    
    def _is_public_subnet(self, subnet_id: str, vpc_id: str) -> bool:
        """Check if subnet is public by examining route tables"""
        try:
            # Get route table associated with subnet
            route_tables = self.ec2_client.describe_route_tables(
                Filters=[
                    {'Name': 'association.subnet-id', 'Values': [subnet_id]}
                ]
            )
            
            # If no explicit association, check main route table
            if not route_tables['RouteTables']:
                route_tables = self.ec2_client.describe_route_tables(
                    Filters=[
                        {'Name': 'vpc-id', 'Values': [vpc_id]},
                        {'Name': 'association.main', 'Values': ['true']}
                    ]
                )
            
            if not route_tables['RouteTables']:
                return False
            
            # Check for internet gateway route
            for route_table in route_tables['RouteTables']:
                for route in route_table['Routes']:
                    if route.get('GatewayId', '').startswith('igw-'):
                        return True
            
            return False
            
        except ClientError:
            return False
    
    def check_resource_exists(self, resource_type: str, resource_name: str, vpc_id: Optional[str] = None) -> Optional[str]:
        """Check if AWS resource exists and return its ARN/ID"""
        try:
            if resource_type == "target-group":
                response = self.elbv2_client.describe_target_groups(Names=[resource_name])
                return response['TargetGroups'][0]['TargetGroupArn'] if response['TargetGroups'] else None
            
            elif resource_type == "load-balancer":
                response = self.elbv2_client.describe_load_balancers(Names=[resource_name])
                return response['LoadBalancers'][0]['LoadBalancerArn'] if response['LoadBalancers'] else None
            
            elif resource_type == "security-group":
                response = self.ec2_client.describe_security_groups(
                    Filters=[
                        {'Name': 'vpc-id', 'Values': [vpc_id]},
                        {'Name': 'group-name', 'Values': [resource_name]}
                    ]
                )
                return response['SecurityGroups'][0]['GroupId'] if response['SecurityGroups'] else None
            
        except ClientError:
            return None
    
    def delete_resource(self, resource_type: str, resource_arn: str):
        """Delete AWS resource"""
        try:
            if resource_type == "target-group":
                logger.info(f"Deleting target group: {resource_arn}")
                self.elbv2_client.delete_target_group(TargetGroupArn=resource_arn)
            
            elif resource_type == "load-balancer":
                logger.info(f"Deleting load balancer: {resource_arn}")
                self.elbv2_client.delete_load_balancer(LoadBalancerArn=resource_arn)
                # Wait for deletion
                logger.info("Waiting for load balancer deletion...")
                waiter = self.elbv2_client.get_waiter('load_balancer_not_exists')
                waiter.wait(LoadBalancerArns=[resource_arn])
            
            elif resource_type == "security-group":
                logger.info(f"Deleting security group: {resource_arn}")
                self.ec2_client.delete_security_group(GroupId=resource_arn)
                
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to delete {resource_type}: {e}")
    
    def create_security_group(self, sg_name: str, vpc_id: str, force_recreate: bool = False) -> str:
        """Create ALB security group"""
        logger.info(f"Creating ALB security group: {sg_name}")
        
        existing_sg_id = self.check_resource_exists("security-group", sg_name, vpc_id)
        
        if existing_sg_id:
            if force_recreate:
                self.delete_resource("security-group", existing_sg_id)
            else:
                logger.info(f"Security group already exists, skipping creation: {existing_sg_id}")
                return existing_sg_id
        
        try:
            response = self.ec2_client.create_security_group(
                GroupName=sg_name,
                Description="Security group for ALB clickstream service",
                VpcId=vpc_id
            )
            sg_id = response['GroupId']
            logger.info(f"Security group created: {sg_id}")
            logger.info("Note: Security group rules need separate configuration")
            return sg_id
            
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to create security group: {e}")
    
    def create_target_group(self, tg_name: str, vpc_id: str, container_port: int, force_recreate: bool = False) -> str:
        """Create target group"""
        logger.info(f"Creating target group: {tg_name}")
        
        existing_tg_arn = self.check_resource_exists("target-group", tg_name)
        
        if existing_tg_arn:
            if force_recreate:
                self.delete_resource("target-group", existing_tg_arn)
            else:
                logger.info(f"Target group already exists, skipping creation: {existing_tg_arn}")
                return existing_tg_arn
        
        try:
            response = self.elbv2_client.create_target_group(
                Name=tg_name,
                Protocol='HTTP',
                Port=container_port,
                VpcId=vpc_id,
                TargetType='ip',
                HealthCheckProtocol='HTTP',
                HealthCheckPath='/health',
                HealthCheckPort=str(container_port),
                HealthCheckIntervalSeconds=30,
                HealthCheckTimeoutSeconds=5,
                HealthyThresholdCount=2,
                UnhealthyThresholdCount=3,
                Matcher={'HttpCode': '200'}
            )
            
            tg_arn = response['TargetGroups'][0]['TargetGroupArn']
            logger.info(f"Target group created: {tg_arn}")
            return tg_arn
            
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to create target group: {e}")
    
    def create_load_balancer(self, alb_name: str, subnets: List[str], security_group_id: str, force_recreate: bool = False) -> str:
        """Create application load balancer"""
        logger.info(f"Creating application load balancer: {alb_name}")
        
        existing_alb_arn = self.check_resource_exists("load-balancer", alb_name)
        
        if existing_alb_arn:
            if force_recreate:
                self.delete_resource("load-balancer", existing_alb_arn)
            else:
                logger.info(f"Load balancer already exists, skipping creation: {existing_alb_arn}")
                return existing_alb_arn
        
        try:
            response = self.elbv2_client.create_load_balancer(
                Name=alb_name,
                Scheme='internet-facing',
                Type='application',
                Subnets=subnets,
                SecurityGroups=[security_group_id]
            )
            
            alb_arn = response['LoadBalancers'][0]['LoadBalancerArn']
            logger.info(f"ALB created: {alb_arn}")
            
            # Wait for ALB to become available
            logger.info("Waiting for ALB to become available...")
            waiter = self.elbv2_client.get_waiter('load_balancer_available')
            waiter.wait(LoadBalancerArns=[alb_arn])
            
            return alb_arn
            
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to create load balancer: {e}")
    
    def create_listener(self, alb_arn: str, target_group_arn: str, container_port: int, force_recreate: bool = False) -> str:
        """Create listener for ALB"""
        logger.info("Creating listener...")
        
        try:
            # Check if listener already exists
            existing_listeners = self.elbv2_client.describe_listeners(LoadBalancerArn=alb_arn)
            
            for listener in existing_listeners['Listeners']:
                if listener['Port'] == container_port:
                    if force_recreate:
                        logger.info("Deleting existing listener...")
                        self.elbv2_client.delete_listener(ListenerArn=listener['ListenerArn'])
                    else:
                        logger.info(f"Listener already exists, skipping creation: {listener['ListenerArn']}")
                        return listener['ListenerArn']
            
            response = self.elbv2_client.create_listener(
                LoadBalancerArn=alb_arn,
                Protocol='HTTP',
                Port=container_port,
                DefaultActions=[
                    {
                        'Type': 'forward',
                        'TargetGroupArn': target_group_arn
                    }
                ]
            )
            
            listener_arn = response['Listeners'][0]['ListenerArn']
            logger.info(f"Listener created: {listener_arn}")
            return listener_arn
            
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to create listener: {e}")
    
    def update_ecs_service(self, cluster_name: str, service_name: str, target_group_arn: str, container_name: str, container_port: int):
        """Associate ECS service with target group"""
        logger.info("Associating ECS service with target group...")
        
        try:
            # Check if service is already associated
            response = self.ecs_client.describe_services(
                cluster=cluster_name,
                services=[service_name]
            )
            
            service = response['services'][0]
            for lb in service.get('loadBalancers', []):
                if lb.get('targetGroupArn') == target_group_arn:
                    logger.info("ECS service already associated with target group, skipping update")
                    return
            
            self.ecs_client.update_service(
                cluster=cluster_name,
                service=service_name,
                loadBalancers=[
                    {
                        'targetGroupArn': target_group_arn,
                        'containerName': container_name,
                        'containerPort': container_port
                    }
                ]
            )
            
            logger.info("ECS service associated with target group")
            
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to update ECS service: {e}")
    
    def get_alb_dns_name(self, alb_arn: str) -> str:
        """Get ALB DNS name"""
        try:
            response = self.elbv2_client.describe_load_balancers(LoadBalancerArns=[alb_arn])
            return response['LoadBalancers'][0]['DNSName']
        except ClientError as e:
            raise ALBDeploymentError(f"Failed to get ALB DNS name: {e}")
    
    def save_deployment_info(self, deployment_info: Dict, output_file: str = "alb-info.json"):
        """Save deployment information to JSON file"""
        try:
            with open(output_file, 'w') as f:
                json.dump(deployment_info, f, indent=2)
            logger.info(f"Deployment info saved to {output_file}")
        except Exception as e:
            logger.error(f"Failed to save deployment info: {e}")

@tool
def deploy_alb(
    region: str,
    vpc_id: Optional[str] = None,
    cluster_name: str = "clickstream-alb-cluster",
    service_name: str = "clickstream-alb-optimized-service",
    force_recreate: bool = False,
    profile: Optional[str] = None
) -> Dict:
    """
    Deploy Network Load Balancer(NLB) and associate with ECS service for clickstream
    
    Args:
        region: AWS region  (required)
        vpc_id: VPC ID (optional,auto-detected if not provided)
        cluster_name: ECS cluster name  (optional, default: clickstream-alb-cluster)
        service_name: ECS service name  (optional, default: clickstream-alb-optimized-service)
        force_recreate: Force recreate existing resources  (optional, default: False)
        profile: AWS profile name  (optional)
        
    Returns:
        Dict containing deployment information
    """
    logger.info("=== ALB Deployment Started ===")
    
    # Generate resource names
    service_prefix = service_name[:20]  # Limit to 20 chars
    alb_name = f"{service_prefix}-alb"
    target_group_name = f"{service_prefix}-tg"
    security_group_name = f"{service_prefix}-alb-sg"
    
    logger.info(f"Configuration:")
    logger.info(f"  Region: {region}")
    logger.info(f"  Cluster: {cluster_name}")
    logger.info(f"  Service: {service_name}")
    logger.info(f"  ALB Name: {alb_name}")
    logger.info(f"  Target Group: {target_group_name}")
    logger.info(f"  Security Group: {security_group_name}")
    logger.info(f"  Force Recreate: {force_recreate}")
    
    try:
        deployer = ALBDeployer(region=region, profile=profile)
        
        # Get ECS service information
        ecs_info = deployer.get_ecs_service_info(cluster_name, service_name)
        if not vpc_id:
            vpc_id = ecs_info['vpc_id']
        
        # Get public subnets
        public_subnets = deployer.get_public_subnets(vpc_id, ecs_info['ecs_azs'])
        
        # Create resources
        security_group_id = deployer.create_security_group(security_group_name, vpc_id, force_recreate)
        target_group_arn = deployer.create_target_group(target_group_name, vpc_id, ecs_info['container_port'], force_recreate)
        alb_arn = deployer.create_load_balancer(alb_name, public_subnets, security_group_id, force_recreate)
        listener_arn = deployer.create_listener(alb_arn, target_group_arn, ecs_info['container_port'], force_recreate)
        
        # Update ECS service
        deployer.update_ecs_service(cluster_name, service_name, target_group_arn, ecs_info['container_name'], ecs_info['container_port'])
        
        # Get ALB DNS name
        alb_dns = deployer.get_alb_dns_name(alb_arn)
        
        # Prepare deployment info
        deployment_info = {
            "deployment_time": datetime.utcnow().isoformat() + "Z",
            "region": region,
            "vpc_id": vpc_id,
            "cluster_name": cluster_name,
            "service_name": service_name,
            "alb_name": alb_name,
            "alb_arn": alb_arn,
            "alb_dns": alb_dns,
            "target_group_name": target_group_name,
            "target_group_arn": target_group_arn,
            "security_group_id": security_group_id,
            "container_name": ecs_info['container_name'],
            "container_port": ecs_info['container_port'],
            "access_urls": {
                "http": f"http://{alb_dns}:{ecs_info['container_port']}",
                "collect_endpoint": f"http://{alb_dns}:{ecs_info['container_port']}/data/v1",
                "ping_endpoint": f"http://{alb_dns}:{ecs_info['container_port']}/ping",
                "health_endpoint": f"http://{alb_dns}:{ecs_info['container_port']}/health"
            },
            "ecs_service_subnets": ecs_info['ecs_subnets'],
            "alb_subnets": public_subnets,
            "ecs_service_azs": ecs_info['ecs_azs']
        }
        
        # Save deployment info
        # deployer.save_deployment_info(deployment_info)
        
        logger.info("=== ALB Deployment Completed ===")
        logger.info(f"ALB DNS Name: {alb_dns}")
        logger.info(f"Access URL: http://{alb_dns}:{ecs_info['container_port']}")
        logger.info(f"Data Collection Endpoint: http://{alb_dns}:{ecs_info['container_port']}/data/v1")
        logger.info(f"Health Check Endpoint: http://{alb_dns}:{ecs_info['container_port']}/health")
        
        return deployment_info
        
    except Exception as e:
        logger.error(f"ALB deployment failed: {e}")
        return {"error":f"ALB deployment failed: {e}"}


if __name__ == "__main__":
    # Test deployment with default parameters
    try:
        result = deploy_alb(
            region="us-east-1",
            cluster_name="clickstream-alb-cluster",
            service_name="clickstream-alb-optimized-service",
            force_recreate=False
        )
        logger.info("Test deployment completed successfully")
        logger.info(f"ALB DNS: {result['alb_dns']}")
    except Exception as e:
        logger.error(f"Test deployment failed: {e}")

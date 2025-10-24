#!/usr/bin/env python3
"""
NLB Deployment Script - Python refactored version of deploy-nlb-optimized.sh
Creates Network Load Balancer (NLB) and binds it to ECS service
"""

import logging
import sys
import time
from typing import Optional, List, Dict, Any
from datetime import datetime
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from .aws_session import create_aws_session
from strands import tool


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class NLBDeploymentError(Exception):
    """Custom exception for NLB deployment errors"""
    pass


class NLBDeployer:
    """Network Load Balancer deployment manager"""
    
    def __init__(self, region: str, cluster_name: str, service_name: str, 
                 vpc_id: Optional[str] = None, force_recreate: bool = False):
        """
        Initialize NLB deployer
        
        Args:
            region: AWS region
            cluster_name: ECS cluster name
            service_name: ECS service name
            vpc_id: VPC ID (optional, will auto-detect from ECS service)
            force_recreate: Force delete and recreate resources
        """
        self.region = region
        self.cluster_name = cluster_name
        self.service_name = service_name
        self.vpc_id = vpc_id
        self.force_recreate = force_recreate
        
        # Generate resource names based on service name (max 32 chars)
        service_prefix = service_name[:20]
        self.nlb_name = f"{service_prefix}-nlb"
        self.target_group_name = f"{service_prefix}-tg"
        
        # Initialize AWS clients
        try:
            session = create_aws_session(region=region)
            self.ecs_client = session.client('ecs')
            self.elbv2_client = session.client('elbv2')
            self.ec2_client = session.client('ec2')
        except NoCredentialsError:
            raise NLBDeploymentError("AWS credentials not found")
        
        # Service info to be populated
        self.ecs_subnets = []
        self.ecs_azs = []
        self.public_subnets = []
        self.container_port = 8802  # default
        self.container_name = "clickstream-container"  # default
        
        # Resource ARNs
        self.target_group_arn = None
        self.nlb_arn = None
        self.listener_arn = None
        self.nlb_dns = None

    def validate_parameters(self) -> None:
        """Validate required parameters"""
        if not self.region:
            raise NLBDeploymentError("Region is required")
        if not self.cluster_name:
            raise NLBDeploymentError("Cluster name is required")
        if not self.service_name:
            raise NLBDeploymentError("Service name is required")

    def get_ecs_service_info(self) -> None:
        """Get ECS service information and derive network configuration"""
        logger.info("Getting ECS service information...")
        
        try:
            # Check if ECS service exists
            response = self.ecs_client.describe_services(
                cluster=self.cluster_name,
                services=[self.service_name]
            )
            
            if not response['services'] or response['services'][0]['serviceName'] != self.service_name:
                raise NLBDeploymentError(f"ECS service {self.service_name} not found in cluster {self.cluster_name}")
            
            service = response['services'][0]
            
            # Get ECS service subnets
            network_config = service.get('networkConfiguration', {}).get('awsvpcConfiguration', {})
            self.ecs_subnets = network_config.get('subnets', [])
            
            if not self.ecs_subnets:
                raise NLBDeploymentError("Unable to get ECS service subnet information")
            
            logger.info(f"ECS service subnets: {self.ecs_subnets}")
            
            # Auto-detect VPC if not provided
            if not self.vpc_id:
                logger.info("Auto-detecting VPC ID...")
                subnet_response = self.ec2_client.describe_subnets(SubnetIds=[self.ecs_subnets[0]])
                self.vpc_id = subnet_response['Subnets'][0]['VpcId']
                logger.info(f"Detected VPC ID: {self.vpc_id}")
            
            # Get ECS service availability zones
            subnet_response = self.ec2_client.describe_subnets(SubnetIds=self.ecs_subnets)
            self.ecs_azs = list(set([subnet['AvailabilityZone'] for subnet in subnet_response['Subnets']]))
            logger.info(f"ECS service availability zones: {self.ecs_azs}")
            
            # Get public subnets in same AZs
            self._get_public_subnets()
            
            # Get container port and name
            self._get_container_info(service['taskDefinition'])
            
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to get ECS service info: {e}")

    def _get_public_subnets(self) -> None:
        """Get public subnets in the same availability zones as ECS service"""
        logger.info("Getting public subnets in ECS service availability zones...")
        
        try:
            # Get all subnets in VPC
            response = self.ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [self.vpc_id]}]
            )
            
            az_subnet_map = {}
            
            for subnet in response['Subnets']:
                # Check if subnet is in ECS AZ
                if subnet['AvailabilityZone'] not in self.ecs_azs:
                    continue
                
                # Check if subnet is public by examining route tables
                if self._is_public_subnet(subnet['SubnetId']):
                    az = subnet['AvailabilityZone']
                    # Only keep one subnet per AZ (first found)
                    if az not in az_subnet_map:
                        az_subnet_map[az] = subnet['SubnetId']
                        logger.info(f"Selected public subnet: {subnet['SubnetId']} (AZ: {az})")
            
            if not az_subnet_map:
                raise NLBDeploymentError(f"No public subnets found in ECS service availability zones: {self.ecs_azs}")
            
            self.public_subnets = list(az_subnet_map.values())
            logger.info(f"Final NLB public subnets (one per AZ): {self.public_subnets}")
            
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to get public subnets: {e}")

    def _is_public_subnet(self, subnet_id: str) -> bool:
        """Check if subnet is public by examining route tables"""
        try:
            # Get route table associated with subnet
            response = self.ec2_client.describe_route_tables(
                Filters=[{'Name': 'association.subnet-id', 'Values': [subnet_id]}]
            )
            
            route_table_id = None
            if response['RouteTables']:
                route_table_id = response['RouteTables'][0]['RouteTableId']
            else:
                # Check main route table
                response = self.ec2_client.describe_route_tables(
                    Filters=[
                        {'Name': 'vpc-id', 'Values': [self.vpc_id]},
                        {'Name': 'association.main', 'Values': ['true']}
                    ]
                )
                if response['RouteTables']:
                    route_table_id = response['RouteTables'][0]['RouteTableId']
            
            if not route_table_id:
                return False
            
            # Check for IGW route
            response = self.ec2_client.describe_route_tables(RouteTableIds=[route_table_id])
            routes = response['RouteTables'][0]['Routes']
            
            for route in routes:
                if route.get('GatewayId', '').startswith('igw-'):
                    return True
            
            return False
            
        except ClientError:
            return False

    def _get_container_info(self, task_definition_arn: str) -> None:
        """Get container port and name from task definition"""
        try:
            response = self.ecs_client.describe_task_definition(taskDefinition=task_definition_arn)
            container_def = response['taskDefinition']['containerDefinitions'][0]
            
            # Get container port
            port_mappings = container_def.get('portMappings', [])
            if port_mappings:
                self.container_port = port_mappings[0]['containerPort']
                logger.info(f"Detected container port: {self.container_port}")
            else:
                logger.warning(f"Unable to get container port, using default: {self.container_port}")
            
            # Get container name
            self.container_name = container_def.get('name', self.container_name)
            logger.info(f"Detected container name: {self.container_name}")
            
        except (ClientError, KeyError, IndexError):
            logger.warning(f"Unable to get container info, using defaults - port: {self.container_port}, name: {self.container_name}")

    def _check_resource_exists(self, resource_type: str, resource_name: str) -> Optional[str]:
        """Check if AWS resource exists and return its ARN"""
        try:
            if resource_type == "target-group":
                response = self.elbv2_client.describe_target_groups(Names=[resource_name])
                if response['TargetGroups']:
                    return response['TargetGroups'][0]['TargetGroupArn']
            elif resource_type == "load-balancer":
                response = self.elbv2_client.describe_load_balancers(Names=[resource_name])
                if response['LoadBalancers']:
                    return response['LoadBalancers'][0]['LoadBalancerArn']
        except ClientError:
            pass
        return None

    def _delete_resource(self, resource_type: str, resource_arn: str) -> None:
        """Delete AWS resource"""
        try:
            if resource_type == "target-group":
                logger.info(f"Deleting target group: {resource_arn}")
                self.elbv2_client.delete_target_group(TargetGroupArn=resource_arn)
            elif resource_type == "load-balancer":
                logger.info(f"Deleting load balancer: {resource_arn}")
                self.elbv2_client.delete_load_balancer(LoadBalancerArn=resource_arn)
                logger.info("Waiting for load balancer deletion...")
                waiter = self.elbv2_client.get_waiter('load_balancer_not_exists')
                waiter.wait(LoadBalancerArns=[resource_arn])
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to delete {resource_type}: {e}")

    def create_target_group(self) -> None:
        """Create target group"""
        logger.info(f"Creating target group: {self.target_group_name}")
        
        # Check if target group exists
        existing_arn = self._check_resource_exists("target-group", self.target_group_name)
        
        if existing_arn:
            if self.force_recreate:
                self._delete_resource("target-group", existing_arn)
            else:
                logger.info(f"Target group already exists, skipping creation: {existing_arn}")
                self.target_group_arn = existing_arn
                return
        
        logger.info(f"Creating target group with parameters:")
        logger.info(f"  Name: {self.target_group_name}")
        logger.info(f"  Port: {self.container_port}")
        logger.info(f"  VPC: {self.vpc_id}")
        
        try:
            response = self.elbv2_client.create_target_group(
                Name=self.target_group_name,
                Protocol='TCP',
                Port=self.container_port,
                VpcId=self.vpc_id,
                TargetType='ip',
                HealthCheckProtocol='TCP',
                HealthCheckPort=str(self.container_port),
                HealthCheckIntervalSeconds=30,
                HealthyThresholdCount=2,
                UnhealthyThresholdCount=2
            )
            
            self.target_group_arn = response['TargetGroups'][0]['TargetGroupArn']
            logger.info(f"Target group created: {self.target_group_arn}")
            
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to create target group: {e}")

    def create_load_balancer(self) -> None:
        """Create network load balancer"""
        logger.info(f"Creating network load balancer: {self.nlb_name}")
        
        # Check if NLB exists
        existing_arn = self._check_resource_exists("load-balancer", self.nlb_name)
        
        if existing_arn:
            if self.force_recreate:
                self._delete_resource("load-balancer", existing_arn)
            else:
                logger.info(f"Load balancer already exists, skipping creation: {existing_arn}")
                self.nlb_arn = existing_arn
                return
        
        logger.info(f"Creating network load balancer with parameters:")
        logger.info(f"  Name: {self.nlb_name}")
        logger.info(f"  Subnets: {self.public_subnets}")
        
        try:
            response = self.elbv2_client.create_load_balancer(
                Name=self.nlb_name,
                Scheme='internet-facing',
                Type='network',
                Subnets=self.public_subnets
            )
            
            self.nlb_arn = response['LoadBalancers'][0]['LoadBalancerArn']
            logger.info(f"NLB created: {self.nlb_arn}")
            
            # Enable cross-zone load balancing
            logger.info("Enabling cross-zone load balancing...")
            try:
                self.elbv2_client.modify_load_balancer_attributes(
                    LoadBalancerArn=self.nlb_arn,
                    Attributes=[
                        {
                            'Key': 'load_balancing.cross_zone.enabled',
                            'Value': 'true'
                        }
                    ]
                )
            except ClientError as e:
                logger.warning(f"Failed to enable cross-zone load balancing: {e}")
            
            # Wait for NLB to become available
            logger.info("Waiting for NLB to become available...")
            try:
                waiter = self.elbv2_client.get_waiter('load_balancer_available')
                waiter.wait(LoadBalancerArns=[self.nlb_arn])
            except Exception as e:
                logger.warning(f"Timeout waiting for NLB availability: {e}")
            
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to create load balancer: {e}")

    def create_listener(self) -> None:
        """Create listener for the load balancer"""
        logger.info("Creating listener...")
        
        try:
            # Check if listener already exists
            response = self.elbv2_client.describe_listeners(LoadBalancerArn=self.nlb_arn)
            existing_listeners = [l for l in response['Listeners'] if l['Port'] == self.container_port]
            
            if existing_listeners:
                if self.force_recreate:
                    logger.info("Deleting existing listener...")
                    self.elbv2_client.delete_listener(ListenerArn=existing_listeners[0]['ListenerArn'])
                else:
                    logger.info(f"Listener already exists, skipping creation: {existing_listeners[0]['ListenerArn']}")
                    self.listener_arn = existing_listeners[0]['ListenerArn']
                    return
            
            response = self.elbv2_client.create_listener(
                LoadBalancerArn=self.nlb_arn,
                Protocol='TCP',
                Port=self.container_port,
                DefaultActions=[
                    {
                        'Type': 'forward',
                        'TargetGroupArn': self.target_group_arn
                    }
                ]
            )
            
            self.listener_arn = response['Listeners'][0]['ListenerArn']
            logger.info(f"Listener created: {self.listener_arn}")
            
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to create listener: {e}")

    def update_ecs_service(self) -> None:
        """Associate ECS service with target group"""
        logger.info("Associating ECS service with target group...")
        
        try:
            # Check if service is already associated
            response = self.ecs_client.describe_services(
                cluster=self.cluster_name,
                services=[self.service_name]
            )
            
            service = response['services'][0]
            current_lbs = service.get('loadBalancers', [])
            
            # Check if already associated with this target group
            for lb in current_lbs:
                if lb.get('targetGroupArn') == self.target_group_arn:
                    logger.info("ECS service already associated with target group, skipping update")
                    return
            
            self.ecs_client.update_service(
                cluster=self.cluster_name,
                service=self.service_name,
                loadBalancers=[
                    {
                        'targetGroupArn': self.target_group_arn,
                        'containerName': self.container_name,
                        'containerPort': self.container_port
                    }
                ]
            )
            
            logger.info("ECS service associated with target group")
            
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to update ECS service: {e}")

    def get_nlb_info(self) -> None:
        """Get NLB DNS name"""
        try:
            response = self.elbv2_client.describe_load_balancers(LoadBalancerArns=[self.nlb_arn])
            self.nlb_dns = response['LoadBalancers'][0]['DNSName']
            logger.info(f"NLB DNS name: {self.nlb_dns}")
        except ClientError as e:
            raise NLBDeploymentError(f"Failed to get NLB info: {e}")

    def save_deployment_info(self) -> Dict[str, Any]:
        """Get deployment information as dictionary"""
        return {
            'deployment_time': datetime.now().isoformat(),
            'region': self.region,
            'vpc_id': self.vpc_id,
            'cluster_name': self.cluster_name,
            'service_name': self.service_name,
            'nlb_name': self.nlb_name,
            'nlb_arn': self.nlb_arn,
            'nlb_dns': self.nlb_dns,
            'target_group_name': self.target_group_name,
            'target_group_arn': self.target_group_arn,
            'container_name': self.container_name,
            'container_port': self.container_port,
            'access_url': f"http://{self.nlb_dns}:{self.container_port}",
            'ecs_service_subnets': self.ecs_subnets,
            'nlb_subnets': self.public_subnets,
            'ecs_availability_zones': self.ecs_azs
        }

    def deploy(self) -> Dict[str, Any]:
        """
        Main deployment function
        
        Returns:
            Dict containing deployment information
        """
        logger.info("=== NLB Deployment Started ===")
        
        try:
            # Validate parameters
            self.validate_parameters()
            
            logger.info("Configuration:")
            logger.info(f"  Region: {self.region}")
            logger.info(f"  Cluster: {self.cluster_name}")
            logger.info(f"  Service: {self.service_name}")
            logger.info(f"  NLB Name: {self.nlb_name}")
            logger.info(f"  Target Group Name: {self.target_group_name}")
            logger.info(f"  Force Recreate: {self.force_recreate}")
            
            # Get ECS service information
            self.get_ecs_service_info()
            
            # Create resources
            self.create_target_group()
            self.create_load_balancer()
            self.create_listener()
            self.update_ecs_service()
            
            # Get deployment info
            self.get_nlb_info()
            
            # Get deployment info
            deployment_info = self.save_deployment_info()
            
            logger.info("=== NLB Deployment Completed! ===")
            logger.info(f"NLB DNS Name: {self.nlb_dns}")
            logger.info(f"Access URL: http://{self.nlb_dns}:{self.container_port}")
            
            return {
                'nlb_arn': self.nlb_arn,
                'nlb_dns': self.nlb_dns,
                'target_group_arn': self.target_group_arn,
                'access_url': f"http://{self.nlb_dns}:{self.container_port}",
                'container_port': self.container_port,
                'deployment_info': deployment_info
            }
            
        except Exception as e:
            logger.error(f"Deployment failed: {e}")
            raise

@tool
def deploy_nlb(
    region: str,
    cluster_name: str = "clickstream-cluster",
    service_name: str = "clickstream-optimized-service",
    vpc_id: Optional[str] = None,
    force_recreate: bool = False
) -> Dict[str, Any]:
    """
    Deploy Network Load Balancer(NLB) and associate with ECS service for clickstream
    
    Args:
        region: AWS region (required)
        cluster_name: ECS cluster name (optional, default: clickstream-cluster)
        service_name: ECS service name (optional, default: clickstream-optimized-service)
        vpc_id: VPC ID (optional, auto-detect from ECS service if not provided)
        force_recreate: Force delete and recreate resources (optional, default: False)
        
    Returns:
        Dict containing deployment information with keys:
        - nlb_arn: NLB ARN
        - nlb_dns: NLB DNS name
        - target_group_arn: Target group ARN
        - access_url: Complete access URL
        - container_port: Container port
        
    Raises:
        NLBDeploymentError: If deployment fails
    """
    try:
        deployer = NLBDeployer(
            region=region,
            cluster_name=cluster_name,
            service_name=service_name,
            vpc_id=vpc_id,
            force_recreate=force_recreate
        )
        
        return deployer.deploy()
    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        return {"error":f"Deployment failed: {e}"}

if __name__ == "__main__":
    # Test with default values
    try:
        result = deploy_nlb(
            cluster_name='clickstream-cluster',
            service_name='clickstream-optimized-service',
            region='us-east-1'
        )
        logger.info(f"Test deployment successful: {result}")
    except Exception as e:
        logger.error(f"Test deployment failed: {e}")

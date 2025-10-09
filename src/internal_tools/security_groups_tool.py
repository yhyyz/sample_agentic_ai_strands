#!/usr/bin/env python3
"""
Security Group Configuration Tool for ALB+Nginx+Vector Architecture
Converts the shell script logic to production-level Python implementation
"""

import logging
import json
import os
from datetime import datetime
from typing import Optional, Dict, List, Tuple
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from .aws_session import create_aws_session
from strands import tool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SecurityGroupConfigurator:
    """Configure security groups for ALB+Nginx+Vector architecture"""
    
    def __init__(self, region: str = "us-east-1", dry_run: bool = False):
        """
        Initialize the security group configurator
        
        Args:
            region: AWS region
            dry_run: If True, only show operations without executing
        """
        self.region = region
        self.dry_run = dry_run
        self.session = create_aws_session(region=region)
        self.ec2_client = self.session.client('ec2')
        self.kafka_client = self.session.client('kafka')
        
    def check_sg_rule_exists(self, sg_id: str, protocol: str, port: int, 
                           source: str, rule_type: str = "ingress") -> bool:
        """
        Check if a security group rule already exists
        
        Args:
            sg_id: Security group ID
            protocol: Protocol (tcp/udp)
            port: Port number
            source: Source (CIDR or security group ID)
            rule_type: Rule type (ingress/egress)
            
        Returns:
            bool: True if rule exists
        """
        try:
            response = self.ec2_client.describe_security_groups(GroupIds=[sg_id])
            sg = response['SecurityGroups'][0]
            
            if rule_type == "ingress":
                permissions = sg.get('IpPermissions', [])
            else:
                permissions = sg.get('IpPermissionsEgress', [])
            
            for perm in permissions:
                if (perm.get('IpProtocol') == protocol and 
                    perm.get('FromPort') == port and 
                    perm.get('ToPort') == port):
                    
                    if source.startswith('sg-'):
                        # Check security group source
                        for group_pair in perm.get('UserIdGroupPairs', []):
                            if group_pair.get('GroupId') == source:
                                return True
                    else:
                        # Check CIDR source
                        for ip_range in perm.get('IpRanges', []):
                            if ip_range.get('CidrIp') == source:
                                return True
            
            return False
            
        except ClientError as e:
            logger.error(f"Error checking security group rule: {e}")
            return False
    
    def add_sg_rule(self, sg_id: str, protocol: str, port: int, 
                   source: str, description: str) -> bool:
        """
        Add a security group rule
        
        Args:
            sg_id: Security group ID
            protocol: Protocol (tcp/udp)
            port: Port number
            source: Source (CIDR or security group ID)
            description: Rule description
            
        Returns:
            bool: True if successful
        """
        logger.info(f"  Adding rule: {sg_id} <- {protocol}:{port} from {source}")
        
        if self.dry_run:
            logger.info("    [DRY RUN] Skipping actual execution")
            return True
        
        if self.check_sg_rule_exists(sg_id, protocol, port, source):
            logger.info("    Rule already exists, skipping")
            return True
        
        try:
            if source.startswith('sg-'):
                # Source is security group
                self.ec2_client.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[{
                        'IpProtocol': protocol,
                        'FromPort': port,
                        'ToPort': port,
                        'UserIdGroupPairs': [{
                            'GroupId': source,
                            'Description': description
                        }]
                    }]
                )
            else:
                # Source is CIDR
                self.ec2_client.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[{
                        'IpProtocol': protocol,
                        'FromPort': port,
                        'ToPort': port,
                        'IpRanges': [{
                            'CidrIp': source,
                            'Description': description
                        }]
                    }]
                )
            
            logger.info("    ✓ Successfully added")
            return True
            
        except ClientError as e:
            if 'InvalidPermission.Duplicate' in str(e):
                logger.info("    Rule already exists")
                return True
            else:
                logger.error(f"    ✗ Failed to add rule: {e}")
                return False
    
    def detect_alb_security_group(self, vpc_id: str, alb_sg_id: Optional[str] = None) -> Optional[str]:
        """
        Detect ALB security group
        
        Args:
            vpc_id: VPC ID
            alb_sg_id: Optional explicit ALB security group ID
            
        Returns:
            str: ALB security group ID or None
        """
        if alb_sg_id:
            logger.info(f"Using specified ALB security group: {alb_sg_id}")
            return alb_sg_id
        
        logger.info("Auto-detecting ALB security group...")
        try:
            response = self.ec2_client.describe_security_groups(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'group-name', 'Values': ['clickstream-alb-opti-alb-sg']}
                ]
            )
            
            if response['SecurityGroups']:
                sg_id = response['SecurityGroups'][0]['GroupId']
                logger.info(f"Detected ALB security group: {sg_id}")
                return sg_id
            else:
                logger.warning("ALB security group not found, please deploy ALB first or specify manually")
                return None
                
        except ClientError as e:
            logger.error(f"Error detecting ALB security group: {e}")
            return None
    
    def detect_ecs_security_group(self, vpc_id: str, ecs_sg_id: Optional[str] = None) -> Optional[str]:
        """
        Detect ECS security group
        
        Args:
            vpc_id: VPC ID
            ecs_sg_id: Optional explicit ECS security group ID
            
        Returns:
            str: ECS security group ID or None
        """
        if ecs_sg_id:
            logger.info(f"Using specified ECS security group: {ecs_sg_id}")
            return ecs_sg_id
        
        logger.info("Auto-detecting ECS security group...")
        try:
            response = self.ec2_client.describe_security_groups(
                Filters=[
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                    {'Name': 'group-name', 'Values': ['clickstream-alb-ecs-sg']}
                ]
            )
            
            if response['SecurityGroups']:
                sg_id = response['SecurityGroups'][0]['GroupId']
                logger.info(f"Detected ECS security group: {sg_id}")
                return sg_id
            else:
                logger.warning("ECS security group not found, please deploy ECS service first or specify manually")
                return None
                
        except ClientError as e:
            logger.error(f"Error detecting ECS security group: {e}")
            return None
    
    def detect_msk_security_group(self, msk_cluster: str, msk_sg_id: Optional[str] = None) -> Optional[str]:
        """
        Detect MSK security group
        
        Args:
            msk_cluster: MSK cluster name
            msk_sg_id: Optional explicit MSK security group ID
            
        Returns:
            str: MSK security group ID or None
        """
        if msk_sg_id:
            logger.info(f"Using specified MSK security group: {msk_sg_id}")
            return msk_sg_id
        
        logger.info("Auto-detecting MSK security group...")
        try:
            # Get MSK cluster ARN
            response = self.kafka_client.list_clusters()
            cluster_arn = None
            
            for cluster in response.get('ClusterInfoList', []):
                if cluster['ClusterName'] == msk_cluster:
                    cluster_arn = cluster['ClusterArn']
                    break
            
            if not cluster_arn:
                logger.error(f"MSK cluster {msk_cluster} not found")
                return None
            
            # Get cluster details
            response = self.kafka_client.describe_cluster(ClusterArn=cluster_arn)
            security_groups = response['ClusterInfo']['BrokerNodeGroupInfo']['SecurityGroups']
            
            if security_groups:
                sg_id = security_groups[0]
                logger.info(f"Detected MSK security group: {sg_id}")
                return sg_id
            else:
                logger.warning("MSK security group not found")
                return None
                
        except ClientError as e:
            logger.error(f"Error detecting MSK security group: {e}")
            return None
    
    def configure_alb_security_group(self, alb_sg_id: str) -> bool:
        """
        Configure ALB security group rules
        
        Args:
            alb_sg_id: ALB security group ID
            
        Returns:
            bool: True if successful
        """
        logger.info(f"1. Configuring ALB security group ({alb_sg_id}):")
        return self.add_sg_rule(alb_sg_id, "tcp", 8802, "0.0.0.0/0", "Allow HTTP traffic from internet")
    
    def configure_ecs_security_group(self, ecs_sg_id: str, alb_sg_id: Optional[str] = None) -> bool:
        """
        Configure ECS security group rules
        
        Args:
            ecs_sg_id: ECS security group ID
            alb_sg_id: ALB security group ID (optional)
            
        Returns:
            bool: True if all rules added successfully
        """
        logger.info(f"2. Configuring ECS security group ({ecs_sg_id}):")
        
        rules = [
            (8685, ecs_sg_id, "Allow internal communication to Vector"),
            (8686, ecs_sg_id, "Allow internal health check")
        ]
        
        # Add ALB rules only if ALB security group exists
        if alb_sg_id:
            rules.extend([
                (8802, alb_sg_id, "Allow traffic from ALB to Nginx"),
                (8685, alb_sg_id, "Allow traffic from ALB to Vector HTTP"),
                (8686, alb_sg_id, "Allow traffic from ALB to Vector health check")
            ])
        
        success = True
        for port, source, description in rules:
            if not self.add_sg_rule(ecs_sg_id, "tcp", port, source, description):
                success = False
        
        return success
    
    def configure_msk_security_group(self, msk_sg_id: str, ecs_sg_id: str) -> bool:
        """
        Configure MSK security group rules
        
        Args:
            msk_sg_id: MSK security group ID
            ecs_sg_id: ECS security group ID
            
        Returns:
            bool: True if all rules added successfully
        """
        logger.info(f"3. Configuring MSK security group ({msk_sg_id}):")
        
        rules = [
            (9092, "Allow Kafka traffic from ECS"),
            (9094, "Allow Kafka TLS traffic from ECS"),
            (9096, "Allow Kafka SASL traffic from ECS"),
            (2181, "Allow Zookeeper traffic from ECS")
        ]
        
        success = True
        for port, description in rules:
            if not self.add_sg_rule(msk_sg_id, "tcp", port, ecs_sg_id, description):
                success = False
        
        return success
    
    def save_configuration_info(self, vpc_id: str, msk_cluster: str, 
                              alb_sg_id: Optional[str], ecs_sg_id: Optional[str], 
                              msk_sg_id: Optional[str]) -> None:
        """
        Save configuration information to JSON file
        
        Args:
            vpc_id: VPC ID
            msk_cluster: MSK cluster name
            alb_sg_id: ALB security group ID
            ecs_sg_id: ECS security group ID
            msk_sg_id: MSK security group ID
        """
        # Removed - configuration info is now returned by main function
        pass

@tool
def configure_security_groups(vpc_id: str, msk_cluster: str, region: str ,
                            alb_sg_id: Optional[str] = None, ecs_sg_id: Optional[str] = None,
                            msk_sg_id: Optional[str] = None, dry_run: bool = False) -> Dict:
    """
    Configuring security groups for clickstream,Enable network connectivity between ALB, ECS, and MSK. If ALB doesn't exist, only ECS and MSK network connectivity is
needed. 
    
    Args:
        vpc_id: VPC ID (required)
        msk_cluster: MSK cluster name (required)
        region: AWS region ( (required))
        alb_sg_id: ALB security group ID (optional, auto-detected)
        ecs_sg_id: ECS security group ID (optional, auto-detected)
        msk_sg_id: MSK security group ID (optional, auto-detected)
        dry_run: Only show operations without executing (optional, default: False)
        
    Returns:
        Dict: Configuration results with security group info
    """
    logger.info("=== ALB+Nginx+Vector Security Group Configuration ===")
    logger.info(f"Region: {region}")
    logger.info(f"VPC: {vpc_id}")
    logger.info(f"MSK Cluster: {msk_cluster}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info("")
    try: 
        configurator = SecurityGroupConfigurator(region=region, dry_run=dry_run)
        
        # Detect security groups
        detected_alb_sg = configurator.detect_alb_security_group(vpc_id, alb_sg_id)
        detected_ecs_sg = configurator.detect_ecs_security_group(vpc_id, ecs_sg_id)
        detected_msk_sg = configurator.detect_msk_security_group(msk_cluster, msk_sg_id)
        
        logger.info("")
        logger.info("Detected security groups:")
        logger.info(f"  ALB security group: {detected_alb_sg or 'Not found'}")
        logger.info(f"  ECS security group: {detected_ecs_sg or 'Not found'}")
        logger.info(f"  MSK security group: {detected_msk_sg or 'Not found'}")
        logger.info("")
        
        results = {
            "success": True, 
            "configured": [],
            "configuration_time": datetime.utcnow().isoformat() + "Z",
            "region": region,
            "vpc_id": vpc_id,
            "msk_cluster": msk_cluster,
            "security_groups": {
                "alb_security_group_id": detected_alb_sg,
                "ecs_security_group_id": detected_ecs_sg,
                "msk_security_group_id": detected_msk_sg
            }
        }
        
        # Configure ALB security group
        if detected_alb_sg:
            if configurator.configure_alb_security_group(detected_alb_sg):
                results["configured"].append("ALB")
            else:
                results["success"] = False
            logger.info("")
        else:
            logger.info("1. Skipping ALB security group configuration (not found)")
            logger.info("")
        
        # Configure ECS security group (works with or without ALB)
        if detected_ecs_sg:
            if configurator.configure_ecs_security_group(detected_ecs_sg, detected_alb_sg):
                results["configured"].append("ECS")
            else:
                results["success"] = False
            logger.info("")
        else:
            logger.info("2. Skipping ECS security group configuration (not found)")
            logger.info("")
        
        # Configure MSK security group
        if detected_msk_sg and detected_ecs_sg:
            if configurator.configure_msk_security_group(detected_msk_sg, detected_ecs_sg):
                results["configured"].append("MSK")
            else:
                results["success"] = False
            logger.info("")
        else:
            logger.info("3. Skipping MSK security group configuration (missing MSK or ECS security group)")
            logger.info("")
        
        logger.info("=== Security Group Configuration Complete ===")
        
        return results
    except Exception as e:
       
        return {"error":f"config security groups failed {e}"}

if __name__ == "__main__":
    # Test configuration
    test_vpc_id = "vpc-12345678"
    test_msk_cluster = "test-msk-cluster"
    
    logger.info("Running test configuration with dry-run enabled...")
    
    try:
        result = configure_security_groups(
            vpc_id=test_vpc_id,
            msk_cluster=test_msk_cluster,
            region="us-east-1",
            dry_run=True
        )
        
        logger.info(f"Test completed successfully: {result}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}")

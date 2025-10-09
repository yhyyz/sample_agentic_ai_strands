#!/usr/bin/env python3
"""
MSK VPC ID Retriever - Get VPC ID from MSK cluster name
"""

import logging
from typing import Optional
from .aws_session import create_aws_session
from strands import tool


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)



@tool
def get_msk_vpc_id(cluster_name: str, region: Optional[str] = None) -> Optional[str]:
    """
    Get VPC ID for an MSK cluster by cluster name.
    
    Args:
        cluster_name: Name of the MSK cluster
        region: AWS region (optional)
        
    Returns:
       dict: {"vpc_id":"xxxx","error":"xxxx"}
    """
    try:
        session = create_aws_session(region=region)
        kafka_client = session.client('kafka')
        ec2_client = session.client('ec2')
        
        logger.info(f"Searching for MSK cluster: {cluster_name}")
        
        # List all clusters and find the one with matching name
        response = kafka_client.list_clusters()
        
        for cluster in response.get('ClusterInfoList', []):
            if cluster.get('ClusterName') == cluster_name:
                cluster_arn = cluster.get('ClusterArn')
                logger.info(f"Found cluster ARN: {cluster_arn}")
                
                # Get detailed cluster info to extract subnet IDs
                cluster_detail = kafka_client.describe_cluster(ClusterArn=cluster_arn)
                broker_node_info = cluster_detail.get('ClusterInfo', {}).get('BrokerNodeGroupInfo', {})
                client_subnets = broker_node_info.get('ClientSubnets', [])
                
                if client_subnets:
                    # Get VPC ID from the first subnet
                    subnet_id = client_subnets[0]
                    logger.info(f"Getting VPC ID from subnet: {subnet_id}")
                    
                    subnet_response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
                    subnets = subnet_response.get('Subnets', [])
                    
                    if subnets:
                        vpc_id = subnets[0].get('VpcId')
                        if vpc_id:
                            logger.info(f"VPC ID for cluster '{cluster_name}': {vpc_id}")
                            return {"vpc_id": vpc_id}
                
                logger.warning(f"Could not determine VPC ID for cluster '{cluster_name}'")
                return {"error": f"Could not determine VPC ID for cluster '{cluster_name}'", "vpc_id":None}
        
        logger.warning(f"MSK cluster '{cluster_name}' not found")
        return {"error": f"MSK cluster '{cluster_name}' not found", "vpc_id":None}
        
    except Exception as e:
        logger.error(f"Error retrieving VPC ID for MSK cluster '{cluster_name}': {str(e)}")
        return {"error": f"Could not determine VPC ID for cluster '{cluster_name}'", "vpc_id":None}


if __name__ == "__main__":
    # Test the function
    test_cluster_name = "msk-log-stream"
    vpc_id = get_msk_vpc_id(test_cluster_name)
    
    if vpc_id:
        logger.info(f"Test successful - VPC ID: {vpc_id}")
    else:
        logger.info("Test completed - No VPC ID found (cluster may not exist)")

#!/usr/bin/env python3
"""
MSK Topic Creation Script - Python Implementation
Refactored from create-msk-topics.sh to production-grade Python code
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from .aws_session import create_aws_session
from strands import tool


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

KAFKA_DOCKER_IMAGE = "confluentinc/cp-kafka:latest"


def check_docker() -> None:
    """Check if Docker is available and running."""
    subprocess.run(['docker', '--version'], check=True, capture_output=True)
    subprocess.run(['docker', 'info'], check=True, capture_output=True)
    logger.info("Docker is available and running")


def pull_kafka_image() -> None:
    """Pull the Kafka Docker image."""
    logger.info("Pulling Kafka Docker image...")
    subprocess.run(['docker', 'pull', KAFKA_DOCKER_IMAGE], check=True)
    logger.info("Successfully pulled Kafka Docker image")


def run_kafka_command(command: List[str]) -> str:
    """Run Kafka command in Docker container."""
    docker_cmd = ['docker', 'run', '--rm', KAFKA_DOCKER_IMAGE] + command
    logger.debug(f"Running command: {' '.join(docker_cmd)}")
    
    result = subprocess.run(docker_cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def get_bootstrap_servers(cluster_name: str, region: str) -> str:
    """Get bootstrap servers from MSK cluster name."""
    logger.info(f"Getting bootstrap servers for cluster: {cluster_name} in region: {region}")
    
    session = create_aws_session(region=region)
    kafka_client = session.client('kafka')
    

    # Get cluster ARN
    response = kafka_client.list_clusters()
    cluster_arn = None
    
    for cluster in response['ClusterInfoList']:
        if cluster['ClusterName'] == cluster_name:
            cluster_arn = cluster['ClusterArn']
            break
    
    if not cluster_arn:
        available_clusters = [cluster['ClusterName'] for cluster in response['ClusterInfoList']]
        logger.error(f"MSK cluster '{cluster_name}' not found in region '{region}'")
        logger.error(f"Available clusters: {available_clusters}")
    
    logger.info(f"Found cluster ARN: {cluster_arn}")
    
    # Get bootstrap servers
    response = kafka_client.get_bootstrap_brokers(ClusterArn=cluster_arn)
    bootstrap_servers = response['BootstrapBrokerString']
    
    logger.info(f"Bootstrap servers: {bootstrap_servers}")
    return bootstrap_servers
        


def topic_exists(topic_name: str, bootstrap_servers: str) -> bool:
    """Check if topic exists."""
    command = ['kafka-topics', '--bootstrap-server', bootstrap_servers, '--list']
    output = run_kafka_command(command)
    topics = output.split('\n')
    return topic_name in topics



def delete_topic(topic_name: str, bootstrap_servers: str) -> None:
    """Delete a topic."""
    logger.info(f"Deleting topic: {topic_name}")
    command = ['kafka-topics', '--bootstrap-server', bootstrap_servers, '--delete', '--topic', topic_name]
    run_kafka_command(command)
    logger.info(f"Successfully deleted topic: {topic_name}")


def create_topic(topic_name: str, partitions: int, replication_factor: int, bootstrap_servers: str) -> None:
    """Create a topic."""
    logger.info(f"Creating topic: {topic_name}")
    command = [
        'kafka-topics', '--bootstrap-server', bootstrap_servers,
        '--create', '--topic', topic_name,
        '--partitions', str(partitions),
        '--replication-factor', str(replication_factor)
    ]
    run_kafka_command(command)
    logger.info(f"Successfully created topic: {topic_name}")





def manage_topic(topic_name: str, partitions: int, replication_factor: int, 
                bootstrap_servers: str, force_recreate: bool) -> bool:
    """Manage (create or recreate) a single topic."""
    logger.info(f"=== Managing Topic: {topic_name} ===")
    
    if topic_exists(topic_name, bootstrap_servers):
        logger.info(f"Topic '{topic_name}' already exists")
        if force_recreate:
            logger.info("Force recreate enabled. Deleting existing topic...")
            delete_topic(topic_name, bootstrap_servers)
            time.sleep(5)  # Wait for deletion to complete
            create_topic(topic_name, partitions, replication_factor, bootstrap_servers)
            return True
        else:
            logger.info("Skipping creation (use --force to recreate)")
            return False
    else:
        logger.info(f"Topic '{topic_name}' does not exist")
        create_topic(topic_name, partitions, replication_factor, bootstrap_servers)
        return True


def create_info_file(config: Dict[str, Any], bootstrap_servers: str) -> None:
    """Create information file with topic details."""
    info_file = "msk-topics-info.json"
    logger.info(f"Creating information file: {info_file}")
    
    info_data = {
        "topic_creation_info": {
            "creation_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "region": config['region'],
            "cluster_name": config.get('cluster_name', 'N/A'),
            "bootstrap_servers": bootstrap_servers,
            "force_recreate": config['force_recreate']
        },
        "application_topic": {
            "name": config['app_topic'],
            "partitions": config['app_partitions'],
            "replication_factor": config['replication_factor'],
            "purpose": "Clickstream data storage",
            "created": topic_exists(config['app_topic'], bootstrap_servers)
        },
        "control_topic": {
            "name": config['control_topic'],
            "partitions": config['control_partitions'],
            "replication_factor": config['replication_factor'],
            "purpose": "Iceberg connector offset storage",
            "created": topic_exists(config['control_topic'], bootstrap_servers)
        },
        "docker_info": {
            "image_used": KAFKA_DOCKER_IMAGE,
            "commands_executed": [
                f"kafka-topics --create --topic {config['app_topic']} --partitions {config['app_partitions']} --replication-factor {config['replication_factor']}",
                f"kafka-topics --create --topic {config['control_topic']} --partitions {config['control_partitions']} --replication-factor {config['replication_factor']}"
            ]
        },
        "usage_notes": {
            "app_topic_usage": "This topic receives clickstream data from applications",
            "control_topic_usage": "This topic is used by Iceberg connector for offset management",
            "verification_command": f"docker run --rm {KAFKA_DOCKER_IMAGE} kafka-topics --bootstrap-server {bootstrap_servers} --list"
        }
    }
    
    with open(info_file, 'w') as f:
        json.dump(info_data, f, indent=2)
    
    logger.info(f"Information file created: {info_file}")
    

    
def get_topic_details(topic_name: str, bootstrap_servers: str) -> str:
    """Get topic details."""
    command = ['kafka-topics', '--bootstrap-server', bootstrap_servers, '--describe', '--topic', topic_name]
    return run_kafka_command(command)

def list_all_topics(bootstrap_servers: str) -> List[str]:
    """List all topics in the cluster."""
    command = ['kafka-topics', '--bootstrap-server', bootstrap_servers, '--list']
    output = run_kafka_command(command)
    return output.split('\n') if output else []

@tool
def create_msk_topics(
    region: str,
    cluster_name:str ,
    bootstrap_servers: Optional[str] = None,
    app_topic: str = "app_logs",
    control_topic: str = "control-iceberg",
    app_partitions: int = 18,
    control_partitions: int = 25,
    replication_factor: int = 3,
    force_recreate: bool = False
) -> Dict[str, Any]:
    """
    Create MSK topics for clickstream data processing.
    
    Required Parameters (choose one):
        cluster_name: MSK cluster name (will auto-discover bootstrap servers)
        region: AWS region
    
    Optional Parameters:
        bootstrap_servers: MSK cluster bootstrap servers (comma-separated)
        app_topic: Application topic name (default: app_logs)
        control_topic: Control topic name (default: control-iceberg)
        app_partitions: Application topic partitions (default: 18)
        control_partitions: Control topic partitions (default: 25)
        replication_factor: Replication factor (default: 3)
        force_recreate: Force recreate existing topics (default: False)
        
    Returns:
        Dict containing creation status and topic information:
        {
            "success": bool,
            "message": str,
            "topics": {
                "app_topic": {"name": str, "partitions": int, "created": bool},
                "control_topic": {"name": str, "partitions": int, "created": bool}
            },
            "cluster_info": {"bootstrap_servers": str, "region": str}
        }
    """
    try:
        # Validate required parameters
        if not bootstrap_servers and not cluster_name:
            raise ValueError("Either cluster_name or bootstrap_servers must be provided")
        
        # Get bootstrap servers
        if bootstrap_servers:
            logger.info(f"Using provided bootstrap servers: {bootstrap_servers}")
        else:
            logger.info(f"Getting bootstrap servers from cluster name: {cluster_name}")
            bootstrap_servers = get_bootstrap_servers(cluster_name, region)
        
        # Configuration summary
        config = {
            'region': region,
            'cluster_name': cluster_name,
            'app_topic': app_topic,
            'control_topic': control_topic,
            'app_partitions': app_partitions,
            'control_partitions': control_partitions,
            'replication_factor': replication_factor,
            'force_recreate': force_recreate
        }
        
        logger.info("=== MSK Topic Creation ===")
        logger.info(f"Region: {region}")
        if cluster_name:
            logger.info(f"Cluster Name: {cluster_name}")
        logger.info(f"Bootstrap Servers: {bootstrap_servers}")
        logger.info(f"Application Topic: {app_topic} (partitions: {app_partitions})")
        logger.info(f"Control Topic: {control_topic} (partitions: {control_partitions})")
        logger.info(f"Replication Factor: {replication_factor}")
        logger.info(f"Force Recreate: {force_recreate}")
        logger.info("==========================")
        
        # Check Docker and pull image
        check_docker()
        pull_kafka_image()
        
        # Manage application topic
        manage_topic(app_topic, app_partitions, replication_factor, bootstrap_servers, force_recreate)
        
        # Manage control topic
        manage_topic(control_topic, control_partitions, replication_factor, bootstrap_servers, force_recreate)
        
        # Verify topics
        logger.info("=== Verifying Topics ===")
        logger.info("Application Topic Details:")
        logger.info(get_topic_details(app_topic, bootstrap_servers))
        logger.info("Control Topic Details:")
        logger.info(get_topic_details(control_topic, bootstrap_servers))
        
        # List all topics
        logger.info("All topics in cluster:")
        all_topics = list_all_topics(bootstrap_servers)
        for topic in all_topics:
            logger.info(f"  - {topic}")
        
        # Create information file
        #create_info_file(config, bootstrap_servers)
        
        logger.info("=== MSK Topic Creation Complete ===")
        logger.info(f"Application Topic: {app_topic} ({app_partitions} partitions)")
        logger.info(f"Control Topic: {control_topic} ({control_partitions} partitions)")
        logger.info(f"Replication Factor: {replication_factor}")
        if cluster_name:
            logger.info(f"Cluster Name: {cluster_name}")
        logger.info(f"Bootstrap Servers: {bootstrap_servers}")
        logger.info("")
        logger.info("To verify topics later, run:")
        logger.info(f"docker run --rm {KAFKA_DOCKER_IMAGE} kafka-topics --bootstrap-server {bootstrap_servers} --list")
        logger.info("========================================")
        
        # Return creation results
        return {
            "success": True,
            "message": "MSK topics created successfully",
            "topics": {
                "app_topic": {
                    "name": app_topic,
                    "partitions": app_partitions,
                },
                "control_topic": {
                    "name": control_topic,
                    "partitions": control_partitions,
                }
            },
            "cluster_info": {
                "bootstrap_servers": bootstrap_servers,
                "region": region,
                "cluster_name": cluster_name
            }
        }
    except Exception as e:
        logger.error(f"Failed to create MSK topics: {e}")
        return {
            "success": False,
            "message": f"MSK topics created failed: {e}",
            "topics": {
                "app_topic": {
                    "name": app_topic,
                    "partitions": app_partitions,                },
                "control_topic": {
                    "name": control_topic,
                    "partitions": control_partitions,
                }
            },
            "cluster_info": {
                "bootstrap_servers": bootstrap_servers,
                "region": region,
                "cluster_name": cluster_name
            }
        }


def main():
    """Main entry point for command line usage."""
    parser = argparse.ArgumentParser(
        description='MSK Topic Creation Script (Docker-based)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --cluster-name my-msk-cluster
  %(prog)s --cluster-name my-cluster --app-topic logs --control-topic iceberg-control
  %(prog)s --bootstrap-servers broker1:9092,broker2:9092,broker3:9092
  %(prog)s --force --app-partitions 12 --cluster-name my-cluster

Description:
  This script creates two topics required for MSK connectors:
  1. Application topic: For clickstream data (default: app_logs)
  2. Control topic: For Iceberg connector offset storage (default: control-iceberg)
        """
    )
    
    # Required parameters (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--cluster-name', help='MSK cluster name (will auto-discover bootstrap servers)')
    group.add_argument('--bootstrap-servers', help='MSK cluster bootstrap servers (comma-separated)')
    
    # Optional parameters
    parser.add_argument('-r', '--region', default="us-east-1", help='AWS region (default: us-east-1)')
    parser.add_argument('-a', '--app-topic', default="app_logs", help='Application topic name (default: app_logs)')
    parser.add_argument('-c', '--control-topic', default="control-iceberg", help='Control topic name (default: control-iceberg)')
    parser.add_argument('-p', '--app-partitions', type=int, default=18, help='Application topic partitions (default: 18)')
    parser.add_argument('-P', '--control-partitions', type=int, default=25, help='Control topic partitions (default: 25)')
    parser.add_argument('-R', '--replication-factor', type=int, default=3, help='Replication factor (default: 3)')
    parser.add_argument('-f', '--force', action='store_true', help='Force recreate existing topics')
    
    args = parser.parse_args()
    
    try:
        result = create_msk_topics(
            cluster_name=args.cluster_name,
            bootstrap_servers=args.bootstrap_servers,
            region=args.region,
            app_topic=args.app_topic,
            control_topic=args.control_topic,
            app_partitions=args.app_partitions,
            control_partitions=args.control_partitions,
            replication_factor=args.replication_factor,
            force_recreate=args.force
        )
        logger.info(f"Operation completed: {result['message']}")
    except Exception as e:
        logger.error(f"Failed to create MSK topics: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Check if this is being run as a test
    if len(sys.argv) == 2 and sys.argv[1] == "--test":
        # Test example - using bootstrap servers directly
        logger.info("Running test example...")
        try:
            # Example usage of the main function
            result = create_msk_topics(
                bootstrap_servers="localhost:9092",  # This would fail but demonstrates usage
                region="us-east-1",
                app_topic="test_app_logs",
                control_topic="test_control_iceberg",
                app_partitions=2,
                control_partitions=3,
                replication_factor=1,
                force_recreate=False
            )
            logger.info(f"Test result: {result}")
        except Exception as e:
            logger.info(f"Test completed (expected to fail with localhost): {e}")
            logger.info("Test demonstrates proper function signature and parameter usage")
    else:
        main()

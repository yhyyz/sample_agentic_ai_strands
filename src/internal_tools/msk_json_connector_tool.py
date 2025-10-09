#!/usr/bin/env python3
"""
MSK S3 JSON Sink Connector Creation Script (Python Implementation)

This script creates an MSK S3 JSON Sink Connector with proper resource management.
It's a Python refactor of the original bash script create-s3-json-connector-optimized.sh.

Key Features:
- Production-level logging instead of print statements
- Modular function design with clear separation of concerns
- Proper error handling and resource cleanup
- AWS session management using aws_session.py
- Comprehensive parameter validation
- JSON info file generation for tracking execution results

Main Entry Function:
    create_msk_s3_json_connector() - Creates the complete MSK S3 JSON connector setup

Usage Examples:
    # Programmatic usage (recommended)
    from create_s3_json_connector import create_msk_s3_json_connector
    result = create_msk_s3_json_connector('my-bucket', 'my-cluster')
    
    # Command line usage
    python3 create_s3_json_connector.py my-bucket my-cluster us-east-1 app_logs

Required AWS Resources:
- S3 bucket (must exist and be accessible)
- MSK cluster (must exist and be running)
- Appropriate IAM permissions for MSK Connect, S3, IAM, CloudWatch

Generated Resources:
- IAM role for MSK Connect service
- CloudWatch log group for connector logs
- Custom plugin for S3 connector (downloaded from Confluent Hub)
- Worker configuration for connector settings
- MSK Connect connector for streaming data to S3

Output:
- msk-s3-json-connector-info.json: Detailed information about created resources
- CloudWatch logs: Connector execution logs
- S3 data: JSON files partitioned by time in the specified bucket
"""

import json
import logging
import time
import base64
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional, Tuple
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


class MSKConnectorError(Exception):
    """Custom exception for MSK connector operations"""
    pass

class S3JsonConnectorManager:
    """Manages MSK S3 JSON Sink Connector creation and configuration"""
    
    def __init__(self, region="us-east-1"):
        self.region = region
        self.session = create_aws_session(region=region)
        self.kafka_client = self.session.client('kafka')
        self.kafkaconnect_client = self.session.client('kafkaconnect')
        self.s3_client = self.session.client('s3')
        self.iam_client = self.session.client('iam')
        self.logs_client = self.session.client('logs')
        self.sts_client = self.session.client('sts')
        
        # Execution tracking
        self.execution_status = "FAILED"
        self.error_message = ""
        self.connector_arn = ""
        self.plugin_arn = ""
        self.worker_config_arn = ""
        
    def validate_s3_bucket(self, bucket_name: str) -> bool:
        """Validate S3 bucket exists and is accessible"""
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                raise MSKConnectorError(f"S3 bucket '{bucket_name}' does not exist")
            elif error_code == '403':
                raise MSKConnectorError(f"S3 bucket '{bucket_name}' is not accessible")
            else:
                raise MSKConnectorError(f"Error accessing S3 bucket '{bucket_name}': {e}")
    
    def get_msk_cluster_info(self, cluster_name: str) -> Tuple[str, str, List[str], List[str]]:
        """Get MSK cluster information"""
        try:
            # List clusters to find the cluster ARN
            response = self.kafka_client.list_clusters()
            cluster_arn = None
            
            for cluster in response['ClusterInfoList']:
                if cluster['ClusterName'] == cluster_name:
                    cluster_arn = cluster['ClusterArn']
                    break
            
            if not cluster_arn:
                raise MSKConnectorError(f"MSK cluster '{cluster_name}' not found in region {self.region}")
            
            # Get cluster details
            cluster_info = self.kafka_client.describe_cluster(ClusterArn=cluster_arn)
            
            # Get bootstrap brokers
            bootstrap_response = self.kafka_client.get_bootstrap_brokers(ClusterArn=cluster_arn)
            bootstrap_servers = bootstrap_response['BootstrapBrokerString']
            
            # Extract VPC and subnet information
            broker_info = cluster_info['ClusterInfo']['BrokerNodeGroupInfo']
            security_groups = broker_info['SecurityGroups']
            subnets = broker_info['ClientSubnets']
            
            logger.info(f"MSK Bootstrap Servers: {bootstrap_servers}")
            logger.info(f"VPC Security Groups: {security_groups}")
            logger.info(f"VPC Subnets: {subnets}")
            
            return cluster_arn, bootstrap_servers, security_groups, subnets
            
        except ClientError as e:
            raise MSKConnectorError(f"Failed to get MSK cluster information: {e}")
    
    def delete_iam_role_safely(self, role_name: str) -> None:
        """Safely delete IAM role with all attached policies"""
        try:
            logger.info(f"Detaching policies from IAM role: {role_name}")
            
            # List and detach all attached policies
            try:
                attached_policies = self.iam_client.list_attached_role_policies(RoleName=role_name)
                for policy in attached_policies['AttachedPolicies']:
                    policy_arn = policy['PolicyArn']
                    logger.info(f"Detaching policy: {policy_arn}")
                    self.iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            except ClientError:
                pass
            
            # List and delete all inline policies
            try:
                inline_policies = self.iam_client.list_role_policies(RoleName=role_name)
                for policy_name in inline_policies['PolicyNames']:
                    logger.info(f"Deleting inline policy: {policy_name}")
                    self.iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            except ClientError:
                pass
            
            # Delete the role
            logger.info(f"Deleting IAM role: {role_name}")
            self.iam_client.delete_role(RoleName=role_name)
            
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchEntity':
                raise MSKConnectorError(f"Failed to delete IAM role {role_name}: {e}")
    
    def create_iam_role(self, role_name: str) -> str:
        """Create IAM role for MSK Connect"""
        try:
            # Check if role already exists
            try:
                role_response = self.iam_client.get_role(RoleName=role_name)
                logger.info(f"IAM role '{role_name}' already exists")
                account_id = self.sts_client.get_caller_identity()['Account']
                return f"arn:aws:iam::{account_id}:role/{role_name}"
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchEntity':
                    raise
            
            logger.info(f"Creating IAM role: {role_name}")
            
            # Create trust policy
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "Service": "kafkaconnect.amazonaws.com"
                        },
                        "Action": "sts:AssumeRole"
                    }
                ]
            }
            
            # Create role
            self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy)
            )
            
            # Attach policies
            policies = [
                "arn:aws:iam::aws:policy/AmazonMSKFullAccess",
                "arn:aws:iam::aws:policy/AWSGlueConsoleFullAccess",
                "arn:aws:iam::aws:policy/AmazonS3FullAccess"
            ]
            
            for policy_arn in policies:
                self.iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            
            logger.info("Waiting for role to be available...")
            time.sleep(10)
            
            account_id = self.sts_client.get_caller_identity()['Account']
            role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
            
            return role_arn
            
        except ClientError as e:
            raise MSKConnectorError(f"Failed to create IAM role {role_name}: {e}")
    
    def create_cloudwatch_log_group(self, log_group_name: str = "msk-connector-log") -> None:
        """Create CloudWatch log group if it doesn't exist"""
        try:
            self.logs_client.create_log_group(logGroupName=log_group_name)
            logger.info(f"Created CloudWatch log group: {log_group_name}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                logger.info(f"CloudWatch log group '{log_group_name}' already exists")
            else:
                raise MSKConnectorError(f"Failed to create log group {log_group_name}: {e}")
    
    def download_and_upload_plugin(self, bucket_name: str, plugin_s3_key: str) -> None:
        """Download S3 plugin and upload to S3 if not exists"""
        try:
            # Check if plugin already exists in S3
            try:
                self.s3_client.head_object(Bucket=bucket_name, Key=plugin_s3_key)
                logger.info(f"Plugin already exists in S3: s3://{bucket_name}/{plugin_s3_key}")
                return
            except ClientError as e:
                if e.response['Error']['Code'] != '404':
                    raise
            
            logger.info("Downloading S3 plugin...")
            plugin_url = "https://hub-downloads.confluent.io/api/plugins/confluentinc/kafka-connect-s3/versions/10.6.7/confluentinc-kafka-connect-s3-10.6.7.zip"
            local_filename = "confluentinc-kafka-connect-s3-10.6.7.zip"
            
            # Download the plugin
            urllib.request.urlretrieve(plugin_url, local_filename)
            
            # Upload to S3
            self.s3_client.upload_file(local_filename, bucket_name, plugin_s3_key)
            logger.info(f"Uploaded plugin to s3://{bucket_name}/{plugin_s3_key}")
            
            # Clean up local file
            import os
            os.remove(local_filename)
            
        except Exception as e:
            raise MSKConnectorError(f"Failed to download and upload plugin: {e}")
    
    def create_custom_plugin(self, plugin_name: str, bucket_name: str, plugin_s3_key: str) -> str:
        """Create custom plugin for S3 connector"""
        try:
            # Check if plugin exists
            response = self.kafkaconnect_client.list_custom_plugins()
            for plugin in response['customPlugins']:
                if plugin['name'] == plugin_name:
                    logger.info(f"Custom plugin '{plugin_name}' already exists")
                    self.plugin_arn = plugin['customPluginArn']
                    return plugin['customPluginArn']
            
            logger.info(f"Creating custom plugin: {plugin_name}")
            
            # Download and upload plugin if needed
            self.download_and_upload_plugin(bucket_name, plugin_s3_key)
            
            # Create custom plugin
            response = self.kafkaconnect_client.create_custom_plugin(
                name=plugin_name,
                contentType='ZIP',
                location={
                    's3Location': {
                        'bucketArn': f'arn:aws:s3:::{bucket_name}',
                        'fileKey': plugin_s3_key
                    }
                }
            )
            
            plugin_arn = response['customPluginArn']
            self.plugin_arn = plugin_arn
            logger.info(f"Plugin ARN: {plugin_arn}")
            
            # Wait for plugin to be created
            logger.info("Waiting for plugin to be created...")
            for i in range(30):
                plugin_status = self.kafkaconnect_client.describe_custom_plugin(
                    customPluginArn=plugin_arn
                )['customPluginState']
                
                if plugin_status == 'ACTIVE':
                    logger.info("Custom plugin created successfully")
                    return plugin_arn
                elif plugin_status == 'CREATE_FAILED':
                    raise MSKConnectorError("Custom plugin creation failed")
                
                logger.info(f"Plugin status: {plugin_status}, waiting...")
                time.sleep(10)
            
            raise MSKConnectorError("Custom plugin creation timed out")
            
        except ClientError as e:
            raise MSKConnectorError(f"Failed to create custom plugin {plugin_name}: {e}")
    
    def create_worker_configuration(self, worker_config_name: str) -> str:
        """Create worker configuration"""
        try:
            # Check if worker configuration exists
            response = self.kafkaconnect_client.list_worker_configurations()
            for config in response['workerConfigurations']:
                if config['name'] == worker_config_name:
                    logger.info(f"Worker configuration '{worker_config_name}' already exists")
                    self.worker_config_arn = config['workerConfigurationArn']
                    return config['workerConfigurationArn']
            
            logger.info(f"Creating worker configuration: {worker_config_name}")
            
            # Create worker config content
            worker_config_content = """key.converter=org.apache.kafka.connect.storage.StringConverter
value.converter=org.apache.kafka.connect.storage.StringConverter
consumer.auto.offset.reset=earliest"""
            
            # Encode to base64
            encoded_content = base64.b64encode(worker_config_content.encode()).decode()
            
            # Create worker configuration
            response = self.kafkaconnect_client.create_worker_configuration(
                name=worker_config_name,
                propertiesFileContent=encoded_content
            )
            
            worker_config_arn = response['workerConfigurationArn']
            self.worker_config_arn = worker_config_arn
            logger.info("Worker configuration created successfully")
            
            return worker_config_arn
            
        except ClientError as e:
            raise MSKConnectorError(f"Failed to create worker configuration {worker_config_name}: {e}")
    
    def create_connector_config(self, connector_name: str, bootstrap_servers: str, 
                              security_groups: List[str], subnets: List[str], 
                              role_arn: str, plugin_arn: str, worker_config_arn: str,
                              bucket_name: str, topic_name: str, worker_count: int, 
                              mcu_count: int, partition_time_col: str) -> Dict:
        """Create connector configuration"""
        
        base_config = {
            "connectorName": connector_name,
            "kafkaCluster": {
                "apacheKafkaCluster": {
                    "bootstrapServers": bootstrap_servers,
                    "vpc": {
                        "securityGroups": security_groups,
                        "subnets": subnets
                    }
                }
            },
            "kafkaClusterClientAuthentication": {
                "authenticationType": "NONE"
            },
            "kafkaClusterEncryptionInTransit": {
                "encryptionType": "PLAINTEXT"
            },
            "kafkaConnectVersion": "3.7.x",
            "serviceExecutionRoleArn": role_arn,
            "plugins": [
                {
                    "customPlugin": {
                        "customPluginArn": plugin_arn,
                        "revision": 1
                    }
                }
            ],
            "workerConfiguration": {
                "workerConfigurationArn": worker_config_arn,
                "revision": 1
            },
            "capacity": {
                "provisionedCapacity": {
                    "mcuCount": mcu_count,
                    "workerCount": worker_count
                }
            },
            "logDelivery": {
                "workerLogDelivery": {
                    "cloudWatchLogs": {
                        "enabled": True,
                        "logGroup": "msk-connector-log"
                    }
                }
            }
        }
        
        # Base connector configuration
        connector_config = {
            "connector.class": "io.confluent.connect.s3.S3SinkConnector",
            "s3.region": self.region,
            "topics.dir": "app-logs-json-data-v1",
            "flush.size": "60000",
            "tasks.max": "3",
            "timezone": "America/New_York",
            "rotate.interval.ms": "120000",
            "locale": "zh_CN",
            "format.class": "io.confluent.connect.s3.format.json.JsonFormat",
            "value.converter": "org.apache.kafka.connect.json.JsonConverter",
            "errors.log.enable": "true",
            "s3.bucket.name": bucket_name,
            "key.converter": "org.apache.kafka.connect.storage.StringConverter",
            "partition.duration.ms": "86400000",
            "schema.compatibility": "NONE",
            "file.delim": "-",
            "topics": topic_name,
            "s3.compression.type": "gzip",
            "partitioner.class": "io.confluent.connect.storage.partitioner.TimeBasedPartitioner",
            "value.converter.schemas.enable": "false",
            "storage.class": "io.confluent.connect.s3.storage.S3Storage",
            "path.format": "YYYYMMdd",
        }
        
        # Add timestamp configuration based on partition_time_col
        if partition_time_col == "kafka_time":
            connector_config["timestamp.extractor"] = "Record"
        else:  # ingestion_time
            connector_config["timestamp.extractor"] = "RecordField"
            connector_config["timestamp.field"] = "meta.ctime"
        
        base_config["connectorConfiguration"] = connector_config
        return base_config
    
    def create_connector(self, connector_config: Dict) -> str:
        """Create the MSK connector"""
        try:
            connector_name = connector_config["connectorName"]
            
            # Check if connector exists
            response = self.kafkaconnect_client.list_connectors()
            for connector in response['connectors']:
                if connector['connectorName'] == connector_name:
                    logger.info(f"Connector '{connector_name}' already exists")
                    self.connector_arn = connector['connectorArn']
                    return connector['connectorArn']
            
            logger.info(f"Creating S3 JSON connector: {connector_name}")
            
            # Create the connector
            response = self.kafkaconnect_client.create_connector(**connector_config)
            connector_arn = response['connectorArn']
            self.connector_arn = connector_arn
            
            logger.info("Connector creation initiated successfully")
            self.execution_status = "SUCCESS"
            
            return connector_arn
            
        except ClientError as e:
            raise MSKConnectorError(f"Failed to create connector {connector_name}: {e}")
    
    def force_delete_resources(self, connector_name: str, worker_config_name: str, 
                             plugin_name: str, role_name: str) -> None:
        """Force delete existing resources in dependency order"""
        logger.info("Force recreate enabled. Checking resources for deletion in dependency order...")
        
        # Step 1: Delete connector if it exists
        try:
            response = self.kafkaconnect_client.list_connectors()
            for connector in response['connectors']:
                if connector['connectorName'] == connector_name:
                    logger.info(f"Deleting existing connector: {connector_name}")
                    self.kafkaconnect_client.delete_connector(connectorArn=connector['connectorArn'])
                    logger.info("Waiting for connector deletion to complete...")
                    time.sleep(30)
                    break
        except ClientError as e:
            logger.warning(f"Failed to delete connector: {e}")
        
        # Step 2: Delete worker configuration if it exists
        try:
            response = self.kafkaconnect_client.list_worker_configurations()
            for config in response['workerConfigurations']:
                if config['name'] == worker_config_name:
                    logger.info(f"Deleting existing worker configuration: {worker_config_name}")
                    self.kafkaconnect_client.delete_worker_configuration(
                        workerConfigurationArn=config['workerConfigurationArn']
                    )
                    logger.info("Waiting for worker configuration deletion to complete...")
                    time.sleep(10)
                    break
        except ClientError as e:
            logger.warning(f"Failed to delete worker configuration: {e}")
        
        # Step 3: Delete custom plugin if it exists
        try:
            response = self.kafkaconnect_client.list_custom_plugins()
            for plugin in response['customPlugins']:
                if plugin['name'] == plugin_name:
                    logger.info(f"Deleting existing custom plugin: {plugin_name}")
                    self.kafkaconnect_client.delete_custom_plugin(
                        customPluginArn=plugin['customPluginArn']
                    )
                    logger.info("Waiting for custom plugin deletion to complete...")
                    time.sleep(10)
                    break
        except ClientError as e:
            logger.warning(f"Failed to delete custom plugin: {e}")
        
        # Step 4: Delete IAM role if it exists
        try:
            self.iam_client.get_role(RoleName=role_name)
            logger.info(f"Deleting existing IAM role: {role_name}")
            self.delete_iam_role_safely(role_name)
            logger.info("Waiting for IAM role deletion to complete...")
            time.sleep(10)
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchEntity':
                logger.warning(f"Failed to delete IAM role: {e}")
        
        logger.info("Force deletion completed. Proceeding with resource creation...")
    
    def generate_info_file(self, connector_name: str, cluster_name: str, cluster_arn: str,
                          bootstrap_servers: str, topic_name: str, bucket_name: str,
                          plugin_s3_key: str, worker_count: int, mcu_count: int,
                          partition_time_col: str, role_name: str, role_arn: str,
                          worker_config_name: str, plugin_name: str) -> None:
        """Generate information file with connector details"""
        info_file = "msk-s3-json-connector-info.json"
        logger.info(f"Creating information file: {info_file}")
        
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        
        info_data = {
            "execution_info": {
                "status": self.execution_status,
                "timestamp": timestamp,
                "error_message": self.error_message,
                "script_version": "python_optimized"
            },
            "connector_info": {
                "connector_name": connector_name,
                "connector_arn": self.connector_arn,
                "connector_type": "S3 JSON Sink",
                "region": self.region
            },
            "msk_info": {
                "cluster_name": cluster_name,
                "cluster_arn": cluster_arn,
                "bootstrap_servers": bootstrap_servers,
                "topic_name": topic_name
            },
            "plugin_info": {
                "plugin_name": plugin_name,
                "plugin_arn": self.plugin_arn,
                "plugin_s3_location": f"s3://{bucket_name}/{plugin_s3_key}",
                "plugin_version": "10.6.7"
            },
            "capacity_info": {
                "worker_count": worker_count,
                "mcu_count": mcu_count,
                "max_tasks": 3
            },
            "storage_info": {
                "s3_bucket": bucket_name,
                "data_location": f"s3://{bucket_name}/app-logs-json/",
                "partition_format": "year=yyyy/month=MM/day=dd/hour=HH",
                "partition_time_column": partition_time_col
            },
            "iam_info": {
                "service_role_name": role_name,
                "service_role_arn": role_arn
            },
            "worker_config_info": {
                "worker_config_name": worker_config_name,
                "worker_config_arn": self.worker_config_arn
            },
            "monitoring_info": {
                "cloudwatch_log_group": "msk-connector-log",
                "flush_size": "1000",
                "rotate_interval_ms": "60000"
            }
        }
        
        with open(info_file, 'w') as f:
            json.dump(info_data, f, indent=2)
        
        logger.info(f"Information file created: {info_file}")

@tool
def create_msk_s3_json_connector(
    s3_bucket: str,
    msk_cluster_name: str,
    region: str,
    topic_name: str = "app_logs",
    worker_count: int = 6,
    mcu_count: int = 1,
    partition_time_col: str = "ingestion_time",
    force_recreate: bool = False

) -> Dict:
    """
    Main entry point to create MSK S3 JSON Sink Connector, consume msk data and sink s3, stored as json format

    
    Args:
        s3_bucket: S3 bucket name for storing data and plugins (required)
        msk_cluster_name: Name of the MSK cluster (required)
        region: AWS region (required)
        topic_name: Kafka topic name (optional, default: app_logs)
        worker_count: Number of workers (optional, default: 6)
        mcu_count: MCU count (optional, default: 1)
        partition_time_col: Partition time column - 'ingestion_time' or 'kafka_time' (optional, default: ingestion_time)
        force_recreate: Force recreate existing resources (optional, default: False)
    
    Returns:
        Dict: Execution result with status and details
    """
    
    # Validate partition_time_col parameter
    if partition_time_col not in ["ingestion_time", "kafka_time"]:
        raise ValueError("partition_time_col must be either 'ingestion_time' or 'kafka_time'")
    
    manager = S3JsonConnectorManager(region)
    
    # Resource names (define early for error handling)
    connector_name = "msk-s3-sink-json"
    role_name = f"MSKConnectServiceRole-{region}"
    plugin_name = "msk-s3-sink-plugin"
    worker_config_name = "msk-connector-s3-sink-config"
    plugin_s3_key = "plugins/confluentinc-kafka-connect-s3-10.6.7.zip"
    
    try:
        logger.info("=== MSK S3 JSON Sink Connector Creation ===")
        logger.info(f"Region: {region}")
        logger.info(f"S3 Bucket: {s3_bucket}")
        logger.info(f"MSK Cluster Name: {msk_cluster_name}")
        logger.info(f"Topic: {topic_name}")
        logger.info(f"Workers: {worker_count}")
        logger.info(f"MCU Count: {mcu_count}")
        logger.info(f"Partition Time Column: {partition_time_col}")
        logger.info(f"Force Recreate: {force_recreate}")
        logger.info("============================================")
        
        # Validate S3 bucket
        manager.validate_s3_bucket(s3_bucket)
        
        # Get MSK cluster information
        logger.info("Getting MSK cluster information...")
        cluster_arn, bootstrap_servers, security_groups, subnets = manager.get_msk_cluster_info(msk_cluster_name)
        
        # Force delete resources if requested
        if force_recreate:
            manager.force_delete_resources(connector_name, worker_config_name, plugin_name, role_name)
        
        # Create CloudWatch log group
        manager.create_cloudwatch_log_group()
        
        # Create IAM role
        role_arn = manager.create_iam_role(role_name)
        
        # Create custom plugin
        plugin_arn = manager.create_custom_plugin(plugin_name, s3_bucket, plugin_s3_key)
        
        # Create worker configuration
        worker_config_arn = manager.create_worker_configuration(worker_config_name)
        
        # Create connector configuration
        connector_config = manager.create_connector_config(
            connector_name, bootstrap_servers, security_groups, subnets,
            role_arn, plugin_arn, worker_config_arn, s3_bucket, topic_name,
            worker_count, mcu_count, partition_time_col
        )
        
        # Create connector
        connector_arn = manager.create_connector(connector_config)
        
        # Generate information file
        # manager.generate_info_file(
        #     connector_name, msk_cluster_name, cluster_arn, bootstrap_servers,
        #     topic_name, s3_bucket, plugin_s3_key, worker_count, mcu_count,
        #     partition_time_col, role_name, role_arn, worker_config_name, plugin_name
        # )
        
        logger.info("")
        logger.info("=== MSK S3 JSON Connector Creation Complete ===")
        logger.info(f"Execution Status: {manager.execution_status}")
        logger.info(f"Connector Name: {connector_name}")
        logger.info(f"Connector ARN: {connector_arn}")
        logger.info(f"Plugin S3 Location: s3://{s3_bucket}/{plugin_s3_key}")
        logger.info(f"Worker Count: {worker_count}")
        logger.info(f"MCU Count: {mcu_count}")
        logger.info(f"Partition Time Column: {partition_time_col}")
        logger.info(f"Data Location: s3://{s3_bucket}/app-logs-json/")
        logger.info("Format: JSON (gzipped)")
        logger.info("")
        logger.info("Check the AWS console for connector status.")
        logger.info("================================================")
        
        return {
            "status": manager.execution_status,
            "connector_arn": connector_arn,
            "connector_name": connector_name,
            "error_message": manager.error_message
        }
        
    except Exception as e:
        manager.error_message = str(e)
        logger.error(f"Error: {e}")
        
        # Still generate info file on error
        # try:
        #     manager.generate_info_file(
        #         connector_name, msk_cluster_name, "", "",
        #         topic_name, s3_bucket, plugin_s3_key, worker_count, mcu_count,
        #         partition_time_col, role_name, "", worker_config_name, plugin_name
        #     )
        # except:
        #     pass
        
        return {
            "status": "FAILED",
            "connector_arn": "",
            "connector_name": connector_name,
            "error_message": str(e)
        }


if __name__ == "__main__":
    # Example usage - replace with actual values for testing
    import sys
    
    if len(sys.argv) >= 3:
        # Command line usage
        s3_bucket = sys.argv[1]
        msk_cluster_name = sys.argv[2]
        
        # Optional parameters with defaults
        region = sys.argv[3] if len(sys.argv) > 3 else "us-east-1"
        topic_name = sys.argv[4] if len(sys.argv) > 4 else "app_logs"
        
        try:
            result = create_msk_s3_json_connector(
                s3_bucket=s3_bucket,
                msk_cluster_name=msk_cluster_name,
                region=region,
                topic_name=topic_name,
                worker_count=4,
                mcu_count=1,
                partition_time_col="ingestion_time",
                force_recreate=False
            )
            print(f"Execution completed with status: {result['status']}")
            if result['status'] == 'FAILED':
                print(f"Error: {result['error_message']}")
            else:
                print(f"Connector ARN: {result['connector_arn']}")
        except Exception as e:
            print(f"Execution failed: {e}")
    else:
        print("Usage: python3 create_s3_json_connector.py <s3-bucket> <msk-cluster-name> [region] [topic-name]")
        print("Example: python3 create_s3_json_connector.py my-bucket my-msk-cluster us-east-1 app_logs")
        print("")
        print("For programmatic usage, import and call create_msk_s3_json_connector() function:")
        print("from create_s3_json_connector import create_msk_s3_json_connector")
        print("result = create_msk_s3_json_connector('my-bucket', 'my-cluster')")

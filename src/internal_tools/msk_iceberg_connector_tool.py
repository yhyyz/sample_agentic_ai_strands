#!/usr/bin/env python3
"""
MSK Iceberg Sink Connector Creation Script (Python Implementation)
This script creates an MSK Iceberg Sink Connector with proper resource management
"""

import json
import logging
import argparse
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

class MSKIcebergConnectorManager:
    """Manages MSK Iceberg Sink Connector creation and configuration"""
    
    def __init__(self, region: str = "us-east-1"):
        """Initialize the connector manager with AWS session"""
        self.region = region
        self.session = create_aws_session(region=region)
        self.kafka_client = self.session.client('kafka')
        self.kafkaconnect_client = self.session.client('kafkaconnect')
        self.iam_client = self.session.client('iam')
        self.s3_client = self.session.client('s3')
        self.glue_client = self.session.client('glue')
        self.logs_client = self.session.client('logs')
        self.sts_client = self.session.client('sts')
        
        # Execution tracking
        self.execution_status = "FAILED"
        self.error_message = ""
        self.connector_arn = ""
        self.plugin_arn = ""
        self.worker_config_arn = ""
        
    def validate_s3_bucket(self, bucket_name: str) -> bool:
        """Validate that S3 bucket exists and is accessible"""
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            logger.info(f"S3 bucket '{bucket_name}' is accessible")
            return True
        except ClientError as e:
            logger.error(f"S3 bucket '{bucket_name}' is not accessible: {e}")
            return False
            
    def get_msk_cluster_info(self, cluster_name: str) -> Tuple[str, str, List[str], List[str]]:
        """Get MSK cluster information including ARN, bootstrap servers, subnets, and security groups"""
        try:
            # List clusters to find the ARN
            response = self.kafka_client.list_clusters()
            cluster_arn = None
            
            for cluster in response['ClusterInfoList']:
                if cluster['ClusterName'] == cluster_name:
                    cluster_arn = cluster['ClusterArn']
                    break
                    
            if not cluster_arn:
                raise ValueError(f"MSK cluster '{cluster_name}' not found")
                
            # Get cluster details
            cluster_info = self.kafka_client.describe_cluster(ClusterArn=cluster_arn)
            
            # Get bootstrap brokers
            bootstrap_response = self.kafka_client.get_bootstrap_brokers(ClusterArn=cluster_arn)
            bootstrap_servers = bootstrap_response['BootstrapBrokerString']
            
            # Extract VPC information
            broker_info = cluster_info['ClusterInfo']['BrokerNodeGroupInfo']
            security_groups = broker_info['SecurityGroups']
            subnets = broker_info['ClientSubnets']
            
            logger.info(f"MSK cluster found: {cluster_arn}")
            logger.info(f"Bootstrap servers: {bootstrap_servers}")
            
            return cluster_arn, bootstrap_servers, subnets, security_groups
            
        except Exception as e:
            logger.error(f"Failed to get MSK cluster information: {e}")
            raise
            
    def create_cloudwatch_log_group(self, log_group_name: str = "msk-connector-log") -> None:
        """Create CloudWatch log group if it doesn't exist"""
        try:
            self.logs_client.create_log_group(logGroupName=log_group_name)
            logger.info(f"Created CloudWatch log group: {log_group_name}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
                logger.info(f"CloudWatch log group already exists: {log_group_name}")
            else:
                logger.error(f"Failed to create log group: {e}")
                raise
                
    def create_glue_database(self, database_name: str) -> None:
        """Create Glue database if it doesn't exist"""
        try:
            self.glue_client.create_database(
                DatabaseInput={'Name': database_name}
            )
            logger.info(f"Created Glue database: {database_name}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'AlreadyExistsException':
                logger.info(f"Glue database already exists: {database_name}")
            else:
                logger.error(f"Failed to create Glue database: {e}")
                raise
                
    def delete_iam_role_safely(self, role_name: str) -> None:
        """Safely delete IAM role by detaching all policies first"""
        try:
            # List and detach all attached policies
            attached_policies = self.iam_client.list_attached_role_policies(RoleName=role_name)
            for policy in attached_policies['AttachedPolicies']:
                logger.info(f"Detaching policy: {policy['PolicyArn']}")
                self.iam_client.detach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy['PolicyArn']
                )
                
            # List and delete all inline policies
            inline_policies = self.iam_client.list_role_policies(RoleName=role_name)
            for policy_name in inline_policies['PolicyNames']:
                logger.info(f"Deleting inline policy: {policy_name}")
                self.iam_client.delete_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name
                )
                
            # Delete the role
            self.iam_client.delete_role(RoleName=role_name)
            logger.info(f"Deleted IAM role: {role_name}")
            
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchEntity':
                logger.error(f"Failed to delete IAM role {role_name}: {e}")
                raise
                
    def create_iam_role(self, role_name: str) -> str:
        """Create IAM role for MSK Connect service"""
        try:
            # Check if role already exists
            try:
                role_response = self.iam_client.get_role(RoleName=role_name)
                logger.info(f"IAM role '{role_name}' already exists")
                return role_response['Role']['Arn']
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchEntity':
                    raise
                    
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
            role_response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy)
            )
            
            # Attach required policies
            policies = [
                "arn:aws:iam::aws:policy/AmazonMSKFullAccess",
                "arn:aws:iam::aws:policy/AWSGlueConsoleFullAccess", 
                "arn:aws:iam::aws:policy/AmazonS3FullAccess"
            ]
            
            for policy_arn in policies:
                self.iam_client.attach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy_arn
                )
                
            logger.info(f"Created IAM role: {role_name}")
            
            # Wait for role to be available
            time.sleep(10)
            
            return role_response['Role']['Arn']
            
        except Exception as e:
            logger.error(f"Failed to create IAM role: {e}")
            raise
            
    def download_and_upload_plugin(self, s3_bucket: str, plugin_s3_key: str) -> None:
        """Download Iceberg plugin and upload to S3 if not exists"""
        try:
            # Check if plugin already exists in S3
            try:
                self.s3_client.head_object(Bucket=s3_bucket, Key=plugin_s3_key)
                logger.info(f"Plugin already exists in S3: s3://{s3_bucket}/{plugin_s3_key}")
                return
            except ClientError as e:
                if e.response['Error']['Code'] != '404':
                    raise
                    
            # Download plugin
            plugin_url = "https://github.com/databricks/iceberg-kafka-connect/releases/download/v0.6.19/iceberg-kafka-connect-runtime-0.6.19.zip"
            plugin_filename = "iceberg-kafka-connect-runtime-0.6.19.zip"
            
            logger.info(f"Downloading Iceberg plugin from {plugin_url}")
            urllib.request.urlretrieve(plugin_url, plugin_filename)
            
            # Upload to S3
            logger.info(f"Uploading plugin to s3://{s3_bucket}/{plugin_s3_key}")
            self.s3_client.upload_file(plugin_filename, s3_bucket, plugin_s3_key)
            
            # Clean up local file
            import os
            os.remove(plugin_filename)
            
            logger.info("Plugin download and upload completed")
            
        except Exception as e:
            logger.error(f"Failed to download/upload plugin: {e}")
            raise
            
    def create_custom_plugin(self, plugin_name: str, s3_bucket: str, plugin_s3_key: str) -> str:
        """Create custom plugin for Iceberg connector"""
        try:
            # Check if plugin already exists
            try:
                response = self.kafkaconnect_client.list_custom_plugins()
                for plugin in response['customPlugins']:
                    if plugin['name'] == plugin_name:
                        logger.info(f"Custom plugin '{plugin_name}' already exists")
                        return plugin['customPluginArn']
            except Exception:
                pass
                
            # Ensure plugin exists in S3
            self.download_and_upload_plugin(s3_bucket, plugin_s3_key)
            
            # Create custom plugin
            logger.info(f"Creating custom plugin: {plugin_name}")
            response = self.kafkaconnect_client.create_custom_plugin(
                name=plugin_name,
                contentType='ZIP',
                location={
                    's3Location': {
                        'bucketArn': f'arn:aws:s3:::{s3_bucket}',
                        'fileKey': plugin_s3_key
                    }
                }
            )
            
            plugin_arn = response['customPluginArn']
            logger.info(f"Plugin ARN: {plugin_arn}")
            
            # Wait for plugin to be active
            logger.info("Waiting for plugin to be created...")
            for i in range(30):
                plugin_status = self.kafkaconnect_client.describe_custom_plugin(
                    customPluginArn=plugin_arn
                )['customPluginState']
                
                if plugin_status == 'ACTIVE':
                    logger.info("Custom plugin created successfully")
                    return plugin_arn
                elif plugin_status == 'CREATE_FAILED':
                    raise Exception("Custom plugin creation failed")
                    
                logger.info(f"Plugin status: {plugin_status}, waiting...")
                time.sleep(10)
                
            raise Exception("Custom plugin creation timed out")
            
        except Exception as e:
            logger.error(f"Failed to create custom plugin: {e}")
            raise
            
    def create_worker_configuration(self, worker_config_name: str) -> str:
        """Create worker configuration for the connector"""
        try:
            # Check if worker configuration already exists
            try:
                response = self.kafkaconnect_client.list_worker_configurations()
                for config in response['workerConfigurations']:
                    if config['name'] == worker_config_name:
                        logger.info(f"Worker configuration '{worker_config_name}' already exists")
                        return config['workerConfigurationArn']
            except Exception:
                pass
                
            # Create worker config content
            worker_config_content = """key.converter=org.apache.kafka.connect.storage.StringConverter
value.converter=org.apache.kafka.connect.json.JsonConverter
value.converter.schemas.enable=false
key.converter.schemas.enable=false
consumer.auto.offset.reset=earliest"""
            
            # Encode to base64
            encoded_content = base64.b64encode(worker_config_content.encode()).decode()
            
            # Create worker configuration
            logger.info(f"Creating worker configuration: {worker_config_name}")
            response = self.kafkaconnect_client.create_worker_configuration(
                name=worker_config_name,
                propertiesFileContent=encoded_content
            )
            
            # Get the ARN
            worker_config_arn = response['workerConfigurationArn']
            logger.info(f"Worker configuration created: {worker_config_arn}")
            
            return worker_config_arn
            
        except Exception as e:
            logger.error(f"Failed to create worker configuration: {e}")
            raise
            
    def delete_existing_resources(self, force_recreate: bool) -> None:
        """Delete existing resources if force recreate is enabled"""
        if not force_recreate:
            return
            
        logger.info("Force recreate enabled. Checking resources for deletion...")
        
        # Delete connector first
        connector_name = "msk-s3-sink-iceberg"
        try:
            response = self.kafkaconnect_client.list_connectors()
            for connector in response['connectors']:
                if connector['connectorName'] == connector_name:
                    logger.info(f"Deleting existing connector: {connector_name}")
                    self.kafkaconnect_client.delete_connector(
                        connectorArn=connector['connectorArn']
                    )
                    time.sleep(30)
                    break
        except Exception as e:
            logger.warning(f"Failed to delete connector: {e}")
            
        # Delete worker configuration
        worker_config_name = "sink-iceberg-worker-conf"
        try:
            response = self.kafkaconnect_client.list_worker_configurations()
            for config in response['workerConfigurations']:
                if config['name'] == worker_config_name:
                    logger.info(f"Deleting existing worker configuration: {worker_config_name}")
                    self.kafkaconnect_client.delete_worker_configuration(
                        workerConfigurationArn=config['workerConfigurationArn']
                    )
                    time.sleep(10)
                    break
        except Exception as e:
            logger.warning(f"Failed to delete worker configuration: {e}")
            
        # Delete custom plugin
        plugin_name = "msk-iceberg-sink-plugin"
        try:
            response = self.kafkaconnect_client.list_custom_plugins()
            for plugin in response['customPlugins']:
                if plugin['name'] == plugin_name:
                    logger.info(f"Deleting existing custom plugin: {plugin_name}")
                    self.kafkaconnect_client.delete_custom_plugin(
                        customPluginArn=plugin['customPluginArn']
                    )
                    time.sleep(10)
                    break
        except Exception as e:
            logger.warning(f"Failed to delete custom plugin: {e}")
            
        # Delete IAM role
        role_name = f"MSKConnectServiceRole-{self.region}"
        try:
            self.delete_iam_role_safely(role_name)
            time.sleep(10)
        except Exception as e:
            logger.warning(f"Failed to delete IAM role: {e}")
            
        logger.info("Force deletion completed")
        
    def create_connector_configuration(self, 
                                     connector_name: str,
                                     bootstrap_servers: str,
                                     subnets: List[str],
                                     security_groups: List[str],
                                     role_arn: str,
                                     plugin_arn: str,
                                     worker_config_arn: str,
                                     s3_bucket: str,
                                     glue_database: str,
                                     topic_name: str,
                                     partition_time_col: str,
                                     worker_count: int,
                                     mcu_count: int) -> Dict:
        """Create connector configuration based on partition time column setting"""
        
        # Configure transforms based on partition_time_col parameter
        if partition_time_col == "kafka_time":
            # Use kafka_time: add insertTS transform, remove timestampConverter
            transforms_config = "insertTS,flatten,timestampConverter"
            insertts_config = {
                "transforms.insertTS.type": "org.apache.kafka.connect.transforms.InsertField$Value",
                "transforms.insertTS.timestamp.field": "messageTS"
            }
            timestamp_converter_config = {
                "transforms.timestampConverter.type": "org.apache.kafka.connect.transforms.TimestampConverter$Value",
                "transforms.timestampConverter.target.type": "Timestamp",
                "transforms.timestampConverter.field": "messageTS",
                "transforms.timestampConverter.unix.precision": "milliseconds",
                "iceberg.tables.default-partition-by": "day(messageTS)"
            }
            logger.info("Using kafka_time configuration: insertTS transform enabled, timestampConverter enabled (messageTS default bigint convert to timestamp)")
        else:
            # Use ingestion_time (default): keep original configuration
            transforms_config = "insertTS,flatten,timestampConverter"
            insertts_config = {
                "transforms.insertTS.type": "org.apache.kafka.connect.transforms.InsertField$Value",
                "transforms.insertTS.timestamp.field": "messageTS"
            }
            timestamp_converter_config = {
                "transforms.timestampConverter.type": "org.apache.kafka.connect.transforms.TimestampConverter$Value",
                "transforms.timestampConverter.target.type": "Timestamp",
                "transforms.timestampConverter.field": "meta_ctime",
                "iceberg.tables.default-partition-by": "day(meta_ctime)",
                "transforms.timestampConverter.unix.precision": "milliseconds"
            }
            logger.info("Using ingestion_time configuration: both insertTS and timestampConverter enabled")
            
        # Build connector configuration
        connector_config = {
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
            "connectorConfiguration": {
                "connector.class": "io.tabular.iceberg.connect.IcebergSinkConnector",
                "iceberg.tables.evolve-schema-enabled": "true",
                "iceberg.catalog.catalog-impl": "org.apache.iceberg.aws.glue.GlueCatalog",
                "transforms.flatten.type": "org.apache.kafka.connect.transforms.Flatten$Value",
                "tasks.max": "3",
                "topics": topic_name,
                "iceberg.catalog.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
                "transforms": transforms_config,
                "iceberg.catalog.client.region": self.region,
                "iceberg.control.commit.interval-ms": "120000",
                "transforms.flatten.delimiter": "_",
                "iceberg.tables.auto-create-enabled": "true",
                "iceberg.tables.write-props.write.metadata.previous-versions-max": "1",
                "iceberg.tables": f"{glue_database}.{topic_name}",
                "iceberg.catalog.warehouse": f"s3://{s3_bucket}/app-logs-data-v1/",
                "iceberg.control.topic": "control-iceberg",
                "iceberg.catalog.s3.path-style-access": "true"
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
        
        # Add transform-specific configurations
        connector_config["connectorConfiguration"].update(insertts_config)
        connector_config["connectorConfiguration"].update(timestamp_converter_config)
        
        return connector_config
        
    def create_connector(self, connector_config: Dict) -> str:
        """Create the MSK Iceberg connector"""
        try:
            connector_name = connector_config["connectorName"]
            
            # Check if connector already exists
            try:
                response = self.kafkaconnect_client.list_connectors()
                for connector in response['connectors']:
                    if connector['connectorName'] == connector_name:
                        logger.info(f"Connector '{connector_name}' already exists")
                        return connector['connectorArn']
            except Exception:
                pass
                
            # Create the connector
            logger.info(f"Creating Iceberg connector: {connector_name}")
            response = self.kafkaconnect_client.create_connector(**connector_config)
            
            connector_arn = response['connectorArn']
            logger.info(f"Connector creation initiated: {connector_arn}")
            
            return connector_arn
            
        except Exception as e:
            logger.error(f"Failed to create connector: {e}")
            raise
            
    def generate_info_file(self, 
                          connector_name: str,
                          msk_cluster_name: str,
                          msk_cluster_arn: str,
                          bootstrap_servers: str,
                          s3_bucket: str,
                          glue_database: str,
                          topic_name: str,
                          partition_time_col: str,
                          worker_count: int,
                          mcu_count: int,
                          role_name: str,
                          role_arn: str,
                          plugin_name: str,
                          worker_config_name: str) -> None:
        """Generate information file with connector details"""
        
        info_file = "msk-iceberg-connector-info.json"
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Get account ID
        account_id = self.sts_client.get_caller_identity()['Account']
        
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
                "connector_type": "Iceberg Sink",
                "region": self.region,
                "partition_time_col": partition_time_col
            },
            "msk_info": {
                "cluster_name": msk_cluster_name,
                "cluster_arn": msk_cluster_arn,
                "bootstrap_servers": bootstrap_servers,
                "topic_name": topic_name
            },
            "plugin_info": {
                "plugin_name": plugin_name,
                "plugin_arn": self.plugin_arn,
                "plugin_s3_location": f"s3://{s3_bucket}/plugins/iceberg-kafka-connect-runtime-0.6.19.zip",
                "plugin_version": "0.6.19"
            },
            "capacity_info": {
                "worker_count": worker_count,
                "mcu_count": mcu_count,
                "max_tasks": 3
            },
            "storage_info": {
                "s3_bucket": s3_bucket,
                "data_location": f"s3://{s3_bucket}/app-logs-data-v1/",
                "glue_database": glue_database,
                "iceberg_table": f"{glue_database}.{topic_name}"
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
                "control_topic": "control-iceberg"
            }
        }
        
        with open(info_file, 'w') as f:
            json.dump(info_data, f, indent=2)
            
        logger.info(f"Information file created: {info_file}")

@tool
def create_msk_iceberg_connector(s3_bucket: str,
                               msk_cluster_name: str,
                               region: str,
                               glue_database: str = "iceberg_db",
                               topic_name: str = "app_logs",
                               worker_count: int = 6,
                               mcu_count: int = 1,
                               partition_time_col: str = "ingestion_time",
                               force_recreate: bool = False) -> Dict:
    """
    Main entry point to create MSK Iceberg Sink Connector, consume msk data and sink iceberg , stored as iceberg format with parquet+zstd
    
    Args:
        s3_bucket: S3 bucket name for storing data and plugins (required)
        msk_cluster_name: Name of the MSK cluster (required)
        region: AWS region (required)
        glue_database: Glue database name (optional, default: iceberg_db)
        topic_name: Kafka topic name (optional, default: app_logs)
        worker_count: Number of workers (optional, default: 6)
        mcu_count: MCU count (optional, default: 1)
        partition_time_col: Partition time column type value can be ingestion_time or kafka_time (optional, default: ingestion_time)
        force_recreate: Force recreate existing resources (optional, default: False)
        
    Returns:
        Dict: Connector creation result information
    """
    manager = MSKIcebergConnectorManager(region=region)
    
    try:
        logger.info("=== MSK Iceberg Sink Connector Creation ===")
        logger.info(f"Region: {region}")
        logger.info(f"S3 Bucket: {s3_bucket}")
        logger.info(f"MSK Cluster Name: {msk_cluster_name}")
        logger.info(f"Glue Database: {glue_database}")
        logger.info(f"Topic: {topic_name}")
        logger.info(f"Workers: {worker_count}")
        logger.info(f"MCU Count: {mcu_count}")
        logger.info(f"Partition Time Column: {partition_time_col}")
        logger.info(f"Force Recreate: {force_recreate}")
        logger.info("============================================")
        
        # Validate inputs
        if partition_time_col not in ["ingestion_time", "kafka_time"]:
            raise ValueError("partition_time_col must be either 'ingestion_time' or 'kafka_time'")
            
        # Validate S3 bucket
        if not manager.validate_s3_bucket(s3_bucket):
            raise ValueError(f"S3 bucket '{s3_bucket}' is not accessible")
            
        # Get MSK cluster information
        msk_cluster_arn, bootstrap_servers, subnets, security_groups = manager.get_msk_cluster_info(msk_cluster_name)
        
        # Delete existing resources if force recreate
        manager.delete_existing_resources(force_recreate)
        
        # Create CloudWatch log group
        manager.create_cloudwatch_log_group()
        
        # Create Glue database
        manager.create_glue_database(glue_database)
        
        # Create IAM role
        role_name = f"MSKConnectServiceRole-{region}"
        role_arn = manager.create_iam_role(role_name)
        
        # Create custom plugin
        plugin_name = "msk-iceberg-sink-plugin"
        plugin_s3_key = "plugins/iceberg-kafka-connect-runtime-0.6.19.zip"
        manager.plugin_arn = manager.create_custom_plugin(plugin_name, s3_bucket, plugin_s3_key)
        
        # Create worker configuration
        worker_config_name = "sink-iceberg-worker-conf"
        manager.worker_config_arn = manager.create_worker_configuration(worker_config_name)
        
        # Create connector configuration
        connector_name = "msk-s3-sink-iceberg"
        connector_config = manager.create_connector_configuration(
            connector_name=connector_name,
            bootstrap_servers=bootstrap_servers,
            subnets=subnets,
            security_groups=security_groups,
            role_arn=role_arn,
            plugin_arn=manager.plugin_arn,
            worker_config_arn=manager.worker_config_arn,
            s3_bucket=s3_bucket,
            glue_database=glue_database,
            topic_name=topic_name,
            partition_time_col=partition_time_col,
            worker_count=worker_count,
            mcu_count=mcu_count
        )
        
        # Create connector
        manager.connector_arn = manager.create_connector(connector_config)
        
        # Mark as successful
        manager.execution_status = "SUCCESS"
        
        # Generate info file
        # manager.generate_info_file(
        #     connector_name=connector_name,
        #     msk_cluster_name=msk_cluster_name,
        #     msk_cluster_arn=msk_cluster_arn,
        #     bootstrap_servers=bootstrap_servers,
        #     s3_bucket=s3_bucket,
        #     glue_database=glue_database,
        #     topic_name=topic_name,
        #     partition_time_col=partition_time_col,
        #     worker_count=worker_count,
        #     mcu_count=mcu_count,
        #     role_name=role_name,
        #     role_arn=role_arn,
        #     plugin_name=plugin_name,
        #     worker_config_name=worker_config_name
        # )
        
        logger.info("=== MSK Iceberg Connector Creation Complete ===")
        logger.info(f"Execution Status: {manager.execution_status}")
        logger.info(f"Connector Name: {connector_name}")
        logger.info(f"Connector ARN: {manager.connector_arn}")
        logger.info(f"Data Location: s3://{s3_bucket}/app-logs-data-v1/")
        logger.info(f"Iceberg Table: {glue_database}.{topic_name}")
        logger.info("================================================")
        
        return {
            "status": manager.execution_status,
            "connector_name": connector_name,
            "connector_arn": manager.connector_arn,
            "plugin_arn": manager.plugin_arn,
            "worker_config_arn": manager.worker_config_arn,
            "msk_cluster_name": msk_cluster_name,
            "msk_cluster_arn": msk_cluster_arn,
            "bootstrap_servers": bootstrap_servers,
            "s3_bucket": s3_bucket,
            "glue_database": glue_database,
            "topic_name": topic_name,
            "partition_time_col": partition_time_col,
            "worker_count": worker_count,
            "mcu_count": mcu_count,
            "role_name": role_name,
            "role_arn": role_arn,
            "plugin_name": plugin_name,
            "worker_config_name": worker_config_name
        }
        
    except Exception as e:
        manager.error_message = str(e)
        manager.execution_status = "FAILED"
        logger.error(f"Failed to create MSK Iceberg connector: {e}")
        
        # Still generate info file with error details
        # try:
        #     manager.generate_info_file(
        #         connector_name=connector_name if 'connector_name' in locals() else "msk-s3-sink-iceberg",
        #         msk_cluster_name=msk_cluster_name,
        #         msk_cluster_arn=msk_cluster_arn if 'msk_cluster_arn' in locals() else "",
        #         bootstrap_servers=bootstrap_servers if 'bootstrap_servers' in locals() else "",
        #         s3_bucket=s3_bucket,
        #         glue_database=glue_database,
        #         topic_name=topic_name,
        #         partition_time_col=partition_time_col,
        #         worker_count=worker_count,
        #         mcu_count=mcu_count,
        #         role_name=role_name if 'role_name' in locals() else f"MSKConnectServiceRole-{region}",
        #         role_arn=role_arn if 'role_arn' in locals() else "",
        #         plugin_name=plugin_name if 'plugin_name' in locals() else "msk-iceberg-sink-plugin",
        #         worker_config_name=worker_config_name if 'worker_config_name' in locals() else "sink-iceberg-worker-conf"
        #     )
        # except Exception as info_error:
        #     logger.error(f"Failed to generate info file: {info_error}")
            
        return {"error": f"Failed to create MSK Iceberg connector: {e}"}


def main():
    """Command line interface for MSK Iceberg Connector creation"""
    parser = argparse.ArgumentParser(
        description="MSK Iceberg Sink Connector Creation Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
PARTITION TIME COLUMN:
    ingestion_time    Use connector ingestion timestamp (default)
    kafka_time        Use Kafka message timestamp

EXAMPLES:
    python msk_iceberg_connector.py my-bucket my-msk-cluster
    python msk_iceberg_connector.py --region ap-southeast-1 --database my_db --topic logs my-bucket my-cluster
    python msk_iceberg_connector.py --partition-time-col kafka_time --force --workers 4 my-bucket my-cluster

OUTPUT:
    Creates an info file: msk-iceberg-connector-info.json with connector details
        """
    )
    
    # Required arguments
    parser.add_argument('s3_bucket', help='S3 bucket name for storing data and plugins')
    parser.add_argument('msk_cluster_name', help='Name of the MSK cluster')
    
    # Optional arguments
    parser.add_argument('-r', '--region', default='us-east-1', help='AWS region (default: us-east-1)')
    parser.add_argument('-d', '--database', default='iceberg_db', help='Glue database name (default: iceberg_db)')
    parser.add_argument('-t', '--topic', default='app_logs', help='Kafka topic name (default: app_logs)')
    parser.add_argument('-w', '--workers', type=int, default=6, help='Number of workers (default: 6)')
    parser.add_argument('-m', '--mcu', type=int, default=1, help='MCU count (default: 1)')
    parser.add_argument('-p', '--partition-time-col', choices=['ingestion_time', 'kafka_time'], 
                       default='ingestion_time', help='Partition time column type (default: ingestion_time)')
    parser.add_argument('-f', '--force', action='store_true', help='Force recreate existing resources')
    
    args = parser.parse_args()
    
    try:
        # Create connector using standalone function
        result = create_msk_iceberg_connector(
            s3_bucket=args.s3_bucket,
            msk_cluster_name=args.msk_cluster_name,
            region=args.region,
            glue_database=args.database,
            topic_name=args.topic,
            worker_count=args.workers,
            mcu_count=args.mcu,
            partition_time_col=args.partition_time_col,
            force_recreate=args.force
        )
        
        if result['status'] == 'SUCCESS':
            logger.info("Connector creation completed successfully")
            return 0
        else:
            logger.error("Connector creation failed")
            return 1
            
    except Exception as e:
        logger.error(f"Script execution failed: {e}")
        return 1


if __name__ == "__main__":
    # Test execution
    import sys
    
    # Check if running as test
    if len(sys.argv) == 1:
        logger.info("Running in test mode...")
        
        # Test parameters - replace with your actual values
        test_s3_bucket = "your-test-bucket"
        test_msk_cluster = "your-test-cluster"
        test_region = "us-east-1"
        
        logger.info("Test parameters:")
        logger.info(f"  S3 Bucket: {test_s3_bucket}")
        logger.info(f"  MSK Cluster: {test_msk_cluster}")
        logger.info(f"  Region: {test_region}")
        logger.info("")
        logger.info("To run with actual parameters, use:")
        logger.info(f"  python {sys.argv[0]} <s3-bucket> <msk-cluster-name>")
        logger.info("")
        logger.info("For help, use:")
        logger.info(f"  python {sys.argv[0]} --help")
        
        # Create manager instance for testing
        try:
            manager = MSKIcebergConnectorManager(region=test_region)
            logger.info("MSKIcebergConnectorManager created successfully")
            logger.info("Test completed - ready for actual execution")
        except Exception as e:
            logger.error(f"Test failed: {e}")
            sys.exit(1)
    else:
        # Run with command line arguments
        sys.exit(main())

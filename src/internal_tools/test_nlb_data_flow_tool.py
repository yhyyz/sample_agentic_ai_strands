#!/usr/bin/env python3
"""
Clickstream Lakehouse Data Flow Test Script (NLB Solution)
Production-grade Python implementation for testing deployed data pipeline
"""

import argparse
import base64
import gzip
import json
import logging
import sys
import time
from typing import Dict, List, Optional, Tuple, Union

import boto3
import requests
from botocore.exceptions import ClientError, NoCredentialsError

from .aws_session import create_aws_session
from strands import tool


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class NLBDataFlowTester:
    """
    Production-grade NLB data flow tester for Clickstream Lakehouse
    
    This class handles the complete testing workflow including:
    - AWS NLB discovery
    - Test data generation
    - Data encoding (gzip + base64)
    - HTTP request sending
    - Result tracking and reporting
    """
    
    def __init__(self, region: str):
        """
        Initialize the NLB data flow tester
        
        Args:
            region: AWS region name
        """
        self.session = create_aws_session(region=region)
        self.elbv2_client = self.session.client('elbv2')
        self.success_count = 0
        self.failed_count = 0
        
    def get_nlb_dns_name(self, nlb_name: str, region: str) -> str:
        """
        Retrieve NLB DNS name from AWS
        
        Args:
            nlb_name: Name of the NLB
            region: AWS region name
            
        Returns:
            str: NLB DNS name
            
        Raises:
            ValueError: If NLB is not found
            ClientError: If AWS API call fails
        """
        try:
            logger.info(f"Getting NLB DNS name for '{nlb_name}' in region '{region}'...")
            
            response = self.elbv2_client.describe_load_balancers(
                Names=[nlb_name]
            )
            
            load_balancers = response.get('LoadBalancers', [])
            if not load_balancers:
                raise ValueError(f"NLB '{nlb_name}' not found in region '{region}'")
                
            dns_name = load_balancers[0]['DNSName']
            logger.info(f"Found NLB DNS: {dns_name}")
            return dns_name
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'LoadBalancerNotFound':
                raise ValueError(f"NLB '{nlb_name}' not found in region '{region}'")
            else:
                logger.error(f"AWS API error: {e}")
                raise
        except NoCredentialsError:
            logger.error("AWS credentials not found. Please configure your credentials.")
            raise
            
    def generate_test_data(self, message_index: int) -> Dict:
        """
        Generate test event data for a specific message
        
        Args:
            message_index: Index of the current message (1-based)
            
        Returns:
            Dict: Test event data as dictionary
        """
        timestamp = int(time.time())
        user_id = f"test_user_{message_index:04d}"
        session_id = f"session_{timestamp}_{message_index}"
        
        return {
            "event": "page_view",
            "user_id": user_id,
            "session_id": session_id,
            "timestamp": timestamp,
            "page_url": f"https://example.com/page{message_index}",
            "referrer": "https://example.com/home",
            "user_agent": "Mozilla/5.0 (compatible; ClickstreamTest/1.0)",
            "ip_address": f"192.168.1.{(message_index % 255) + 1}",
            "properties": {
                "page_title": f"Test Page {message_index}",
                "category": "test",
                "test_batch": f"nlb_test_{timestamp}"
            }
        }
        
    def generate_batch_test_data(self, message_index: int) -> List[Dict]:
        """
        Generate batch test event data for a specific message
        
        Args:
            message_index: Index of the current message (1-based)
            
        Returns:
            List[Dict]: List of test event data dictionaries
        """
        base_data = self.generate_test_data(message_index)
        return [base_data, base_data.copy()]
        
    def encode_data(self, data: Union[Dict, List[Dict]]) -> str:
        """
        Encode data using gzip compression and base64 encoding
        
        Args:
            data: Data to encode (dict or list of dicts)
            
        Returns:
            str: Base64 encoded, gzip compressed data
        """
        json_data = json.dumps(data, separators=(',', ':'))
        compressed_data = gzip.compress(json_data.encode('utf-8'))
        return base64.b64encode(compressed_data).decode('ascii')
        
    def send_test_message(self, endpoint_url: str, message_index: int, project: str, batch_send: bool) -> Tuple[bool, str, str]:
        """
        Send a single test message to the NLB endpoint
        
        Args:
            endpoint_url: Complete URL of the test endpoint
            message_index: Index of the current message (1-based)
            project: Project name for header
            batch_send: Whether to send in batch mode
            
        Returns:
            Tuple[bool, str, str]: (success, http_status, response_body)
        """
        try:
            # Generate and encode test data
            if batch_send:
                test_data = self.generate_batch_test_data(message_index)
            else:
                test_data = self.generate_test_data(message_index)
                
            encoded_data = self.encode_data(test_data)
            
            # Prepare headers
            headers = {
                'project': project,
                'compression': 'gzip',
                'Content-Type': 'application/octet-stream'
            }
            
            # Send HTTP POST request
            response = requests.post(
                endpoint_url,
                data=encoded_data,
                headers=headers,
                timeout=30
            )
            
            return True, str(response.status_code), response.text
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for message {message_index}: {e}")
            return False, "000", str(e)
            
    def run_test(self, region: str, nlb_name: str, project: str, message_count: int, 
                 interval: float, batch_send: bool, verbose: bool) -> Dict[str, int]:
        """
        Execute the complete NLB data flow test
        
        Args:
            region: AWS region name
            nlb_name: NLB name
            project: Project name
            message_count: Number of messages to send
            interval: Interval between messages
            batch_send: Whether to send in batch mode
            verbose: Whether to show verbose output
        
        Returns:
            Dict[str, int]: Test results with success and failure counts
        """
        try:
            # Get NLB DNS name
            nlb_dns = self.get_nlb_dns_name(nlb_name, region)
            endpoint_url = f"http://{nlb_dns}:8802/data/v1"
            
            # Display test configuration
            self._display_test_config(region, nlb_name, project, message_count, 
                                    interval, batch_send, verbose, nlb_dns, endpoint_url)
            
            # Send test messages
            logger.info("Starting message transmission...")
            
            for i in range(1, message_count + 1):
                logger.info(f"Sending message {i}/{message_count}...")
                
                success, http_status, response_body = self.send_test_message(
                    endpoint_url, i, project, batch_send)
                
                if success and http_status == "200":
                    logger.info(f"✓ Message {i} sent successfully (HTTP {http_status})")
                    self.success_count += 1
                    
                    if verbose:
                        logger.info(f"    Response: {response_body}")
                else:
                    logger.error(f"✗ Message {i} failed (HTTP {http_status})")
                    self.failed_count += 1
                    
                    if response_body:
                        logger.error(f"    Error response: {response_body}")
                        
                # Wait interval (except for last message)
                if i < message_count:
                    time.sleep(interval)
                    
            # Display results
            self._display_test_results(message_count, project, endpoint_url)
            
            return {
                'total': message_count,
                'success': self.success_count,
                'failed': self.failed_count,
                'success_rate': (self.success_count * 100) // message_count
            }
            
        except Exception as e:
            logger.error(f"Test execution failed: {e}")
            raise
            
    def _display_test_config(self, region: str, nlb_name: str, project: str, 
                           message_count: int, interval: float, batch_send: bool, 
                           verbose: bool, nlb_dns: str, endpoint_url: str) -> None:
        """Display test configuration information"""
        logger.info("=" * 50)
        logger.info("Clickstream Lakehouse Data Flow Test (NLB)")
        logger.info("=" * 50)
        logger.info(f"AWS Region: {region}")
        logger.info(f"NLB Name: {nlb_name}")
        logger.info(f"Project Name: {project}")
        logger.info(f"Message Count: {message_count}")
        logger.info(f"Send Interval: {interval}s")
        logger.info(f"Data Encoding: gzip+Base64")
        logger.info(f"Batch Mode: {batch_send}")
        logger.info(f"Verbose Mode: {verbose}")
        logger.info("=" * 50)
        logger.info(f"NLB DNS: {nlb_dns}")
        logger.info(f"Test Endpoint: {endpoint_url}")
        logger.info("")
        
    def _display_test_results(self, message_count: int, project: str, endpoint_url: str) -> None:
        """Display test results summary"""
        logger.info("")
        logger.info("=" * 50)
        logger.info("Test Completed! (NLB)")
        logger.info("=" * 50)
        logger.info(f"Total Messages: {message_count}")
        logger.info(f"Successfully Sent: {self.success_count}")
        logger.info(f"Failed to Send: {self.failed_count}")
        logger.info(f"Success Rate: {(self.success_count * 100) // message_count}%")
        logger.info(f"Project Name: {project}")
        logger.info(f"Data Encoding: Base64")
        logger.info(f"NLB Endpoint: {endpoint_url}")
        logger.info("")
        
        if self.failed_count > 0:
            logger.warning(f"Note: {self.failed_count} messages failed to send")
            logger.warning("Please check:")
            logger.warning("  - NLB health check status")
            logger.warning("  - ECS task running status")
            logger.warning("  - Security group configuration")
            logger.warning("  - Network connectivity")
        else:
            logger.info(f"All messages sent successfully! Data should be in MSK topic: {project}")
            logger.info("")
            logger.info("Next verification steps:")
            logger.info("  1. Check messages in MSK topic")
            logger.info("  2. Verify data files in S3")
            logger.info("  3. Query Iceberg table data")

@tool
def test_nlb_data_flow(region: str, 
                      nlb_name: str = "clickstream-optimize-nlb",
                      project: str = "app_logs",
                      message_count: int = 10,
                      interval: float = 1.0,
                      batch_send: bool = False,
                      verbose: bool = False) -> Dict[str, int]:
    """
    Execute comprehensive NLB+Nginx+Lua clickstream data flow test.
    
    This function performs end-to-end testing of the clickstream data pipeline by:
    1. Resolving NLB DNS name from AWS ELBv2 API
    2. Generating realistic clickstream test data (page views with user tracking)
    3. Encoding data using gzip compression + base64 encoding
    4. Sending HTTP POST requests to ALB endpoint with proper headers
    5. Tracking success/failure statistics and response validation
    
    Args:
        region: AWS region name (required)
        nlb_name: NLB name (optional, default: clickstream-optimize-nlb)
        project: Project name/MSK topic name (optional, default: app_logs)
        message_count: Number of test messages to send (optional, default: 10)
        interval: Message send interval in seconds (optional, default: 1.0)
        batch_send: Enable client batch sending (optional, default: False)
        verbose: Show detailed response information (optional, default: False)
        
    Returns:
        Dict[str, int]: Test results with success and failure counts
        
    """
    try: 
        tester = NLBDataFlowTester(region)
        return tester.run_test(region, nlb_name, project, message_count, 
                              interval, batch_send, verbose)
    except Exception as e:
        return {"error":f"Failed test alb flow: {e}"}

if __name__ == "__main__":
    """Test execution entry point"""
    try:
        # Parse command line arguments

        # Run the test
        results = test_nlb_data_flow("us-east-1")
        
        # Exit with appropriate code
        if results['failed'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)
            
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        sys.exit(1)

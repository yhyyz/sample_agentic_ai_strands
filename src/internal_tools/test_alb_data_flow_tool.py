#!/usr/bin/env python3
"""
ALB+Nginx+Vector Clickstream Data Flow Test
"""

import json
import gzip
import base64
import time
import logging
from typing import Dict, Any, Tuple
import requests
from .aws_session import create_aws_session
from strands import tool

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ALBDataFlowTester:
    """ALB+Nginx+Vector clickstream data flow tester"""
    
    def __init__(self, region: str = "us-east-1"):
        self.session = create_aws_session(region=region)
        self.elbv2_client = self.session.client('elbv2')
        self.region = region
        
    def get_alb_dns_name(self, alb_name: str) -> str:
        """Get ALB DNS name from AWS"""
        try:
            response = self.elbv2_client.describe_load_balancers(Names=[alb_name])
            if not response['LoadBalancers']:
                raise ValueError(f"ALB '{alb_name}' not found")
            return response['LoadBalancers'][0]['DNSName']
        except Exception as e:
            logger.error(f"Failed to get ALB DNS name: {e}")
            raise
    
    def generate_test_data(self, index: int, user_id: str, session_id: str, timestamp: int) -> Dict[str, Any]:
        """Generate single test data record"""
        return {
            "event": "page_view",
            "user_id": user_id,
            "session_id": session_id,
            "timestamp": timestamp,
            "page_url": f"https://example.com/page{index}",
            "referrer": "https://example.com/home",
            "user_agent": "Mozilla/5.0 (compatible; ClickstreamTest/1.0)",
            "ip_address": f"192.168.1.{(index % 255) + 1}",
            "properties": {
                "page_title": f"Test Page {index}",
                "category": "test",
                "test_batch": f"nlb_test_{timestamp}"
            }
        }
    
    def encode_data(self, data: Any) -> str:
        """Encode data with gzip + base64"""
        json_data = json.dumps(data, separators=(',', ':'))
        compressed_data = gzip.compress(json_data.encode('utf-8'))
        return base64.b64encode(compressed_data).decode('ascii')
    
    def send_request(self, endpoint: str, project: str, encoded_data: str, verbose: bool = False) -> Tuple[int, str]:
        """Send HTTP POST request to ALB endpoint"""
        headers = {'project': project, 'compression': 'gzip'}
        
        try:
            response = requests.post(endpoint, headers=headers, data=encoded_data, timeout=30)
            if verbose:
                logger.info(f"Response content: {response.text}")
            return response.status_code, response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return 0, str(e)

@tool
def test_alb_data_flow(region: str ,
             alb_name: str = "clickstream-alb-opti-alb",
             project: str = "app_logs",
             count: int = 10,
             interval: float = 1.0,
             batch_send: bool = False,
             verbose: bool = False) -> Dict[str, int]:
    """
    Execute comprehensive ALB+Nginx+Vector clickstream data flow test.
    
    This function performs end-to-end testing of the clickstream data pipeline by:
    1. Resolving ALB DNS name from AWS ELBv2 API
    2. Generating realistic clickstream test data (page views with user tracking)
    3. Encoding data using gzip compression + base64 encoding
    4. Sending HTTP POST requests to ALB endpoint with proper headers
    5. Tracking success/failure statistics and response validation
    
    The test simulates real clickstream events with proper user sessions, timestamps,
    and metadata that would be processed by the downstream Vector+MSK pipeline.
    
    Args:
        region (str, required): AWS region where ALB is deployed. 
        alb_name (str, optional): Name of the Application Load Balancer to test.
            Must exist in the specified AWS region. Defaults to "clickstream-alb-opti-alb".
        project (str, optional): Project identifier used as MSK topic name and HTTP header.
            This value is passed in the 'project' header for downstream routing.
            Defaults to "app_logs".
        count (int, optional): Total number of test messages to send.
            Each message represents a unique page view event. Must be > 0.
            Defaults to 10.
        interval (float, optional): Delay in seconds between consecutive message sends.
            Used to control request rate and avoid overwhelming the endpoint.
            Defaults to 1.0.
        batch_send (bool, optional): If True, sends arrays of 2 events per request.
            If False, sends single event per request. Tests different payload sizes.
            Defaults to False.
        verbose (bool, optional): If True, logs detailed HTTP response content.
            Useful for debugging but may expose sensitive data in logs.
            Defaults to False.
    
    Returns:
        Dict[str, int]: Test execution statistics containing:
            - 'total': Total number of messages attempted to send
            - 'success': Number of successful HTTP 200 responses received
            - 'failed': Number of failed requests (non-200 or network errors)
    """
    try:
    
        tester = ALBDataFlowTester(region=region)
        
        logger.info("ALB+Nginx+Vector Clickstream Data Flow Test")
        logger.info(f"Region: {region}, ALB: {alb_name}, Project: {project}")
        
        # Get ALB DNS name
        alb_dns = tester.get_alb_dns_name(alb_name)
        endpoint = f"http://{alb_dns}:8802/data/v1"
        logger.info(f"Test Endpoint: {endpoint}")
        
        # Send test data
        success_count = 0
        failed_count = 0
        
        for i in range(1, count + 1):
            timestamp = int(time.time())
            user_id = f"test_user_{i:04d}"
            session_id = f"session_{timestamp}_{i}"
            
            # Generate test data
            if batch_send:
                test_data = [
                    tester.generate_test_data(i, user_id, session_id, timestamp),
                    tester.generate_test_data(i, user_id, session_id, timestamp)
                ]
            else:
                test_data = tester.generate_test_data(i, user_id, session_id, timestamp)
            
            # Encode and send
            encoded_data = tester.encode_data(test_data)
            logger.info(f"Sending message {i}/{count}...")
            status_code, response_body = tester.send_request(endpoint, project, encoded_data, verbose)
            
            if status_code == 200:
                logger.info(f"✓ Success (HTTP {status_code})")
                success_count += 1
            else:
                logger.error(f"✗ Failed (HTTP {status_code})")
                failed_count += 1
                if response_body:
                    logger.error(f"Error response: {response_body}")
            
            if i < count:
                time.sleep(interval)
        
        logger.info(f"Test Complete! Success: {success_count}/{count}")
        
        return {'total': count, 'success': success_count, 'failed': failed_count}
    
    except Exception as e:
        return {"error":f"Failed test nlb flow: {e}"}

if __name__ == "__main__":
    try:
        result = run_test(count=2, interval=0.5, verbose=True)
        logger.info(f"Test result: {result}")
    except Exception as e:
        logger.error(f"Test failed: {e}")

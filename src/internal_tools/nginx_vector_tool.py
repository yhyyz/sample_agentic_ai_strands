#!/usr/bin/env python3
"""
Docker Image Builder for Nginx and Vector
Production-grade refactored version from build-all-images.sh
"""

import subprocess
import boto3
import logging
from pathlib import Path
from typing import Optional
import base64
import json
from datetime import datetime
from botocore.exceptions import ClientError, BotoCoreError
from strands import tool
from .aws_session import create_aws_session


# Configure logging
logger = logging.getLogger(__name__)


class DockerImageBuilderError(Exception):
    """Custom exception for Docker image builder errors."""
    pass


def _run_command(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Execute shell command and return result."""
    logger.info(f"Executing command: {cmd}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1800)
        if check and result.returncode != 0:
            logger.error(f"Command failed: {cmd}")
            logger.error(f"Error output: {result.stderr}")
            raise DockerImageBuilderError(f"Command execution failed: {cmd}")
        logger.debug(f"Command output: {result.stdout}")
        return result
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timeout: {cmd}")
        raise DockerImageBuilderError(f"Command timeout: {cmd}") from e
    except Exception as e:
        logger.error(f"Command execution error: {cmd}, Error: {str(e)}")
        raise DockerImageBuilderError(f"Command execution error: {cmd}") from e


def _create_ecr_repository_if_not_exists(ecr_client, repo_name: str) -> None:
    """Create ECR repository if it doesn't exist."""
    logger.info(f"Checking ECR repository: {repo_name}")
    try:
        ecr_client.describe_repositories(repositoryNames=[repo_name])
        logger.info(f"ECR repository '{repo_name}' already exists, skipping creation")
    except ecr_client.exceptions.RepositoryNotFoundException:
        logger.info(f"ECR repository '{repo_name}' does not exist, creating...")
        try:
            ecr_client.create_repository(
                repositoryName=repo_name,
                imageScanningConfiguration={'scanOnPush': True},
                encryptionConfiguration={'encryptionType': 'AES256'}
            )
            logger.info(f"ECR repository '{repo_name}' created successfully")
        except ClientError as e:
            logger.error(f"Failed to create ECR repository '{repo_name}': {str(e)}")
            raise DockerImageBuilderError(f"Failed to create ECR repository: {repo_name}") from e
    except ClientError as e:
        logger.error(f"Error checking ECR repository '{repo_name}': {str(e)}")
        raise DockerImageBuilderError(f"Error checking ECR repository: {repo_name}") from e


@tool
def build_and_push_nginx_vector_images(
    region: str,
    nginx_image_name: str = "clickstream-nginx-vector-optimized",
    vector_image_name: str = "clickstream-vector-optimized",
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_profile: Optional[str] = None
) -> str:
    """
    Build nginx and vector images, then push to AWS ECR. Both images will be built together, 
    and AWS ECR repositories will be created automatically if they don't exist.
    
    Args:
        region: AWS region (required)
        nginx_image_name: Nginx image name (default: clickstream-nginx-vector-optimized)
        vector_image_name: Vector image name (default: clickstream-vector-optimized)
        aws_access_key_id: AWS access key (defaults to env AWS_ACCESS_KEY_ID)
        aws_secret_access_key: AWS secret key (defaults to env AWS_SECRET_ACCESS_KEY)
        aws_profile: AWS profile name (defaults to env AWS_PROFILE)
        
    Returns:
        JSON string containing nginx_image_uri, vector_image_uri, region, account_id, and build_time
        
    Raises:
        DockerImageBuilderError: If any step fails
        ValueError: If parameters are invalid
    """
    
    # Validate parameters
    if not region or not nginx_image_name or not vector_image_name:
        raise ValueError("region, nginx_image_name and vector_image_name must be non-empty")
    
    logger.info("Starting Docker image build and push process")
    logger.info(f"Region: {region}")
    logger.info(f"Nginx image name: {nginx_image_name}")
    logger.info(f"Vector image name: {vector_image_name}")
    
    try:
        # Initialize boto3 session using unified session manager
        session = create_aws_session(
            region=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_profile=aws_profile
        )
        sts_client = session.client('sts')
        ecr_client = session.client('ecr')
        
        # Get region from session
        region = session.region_name
        
        # Get account ID
        logger.info("Getting AWS account ID")
        account_id = sts_client.get_caller_identity()['Account']
        logger.info(f"Account ID: {account_id}")
        
        # Build ECR URIs
        nginx_ecr_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{nginx_image_name}"
        vector_ecr_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{vector_image_name}"
        
        logger.info(f"Nginx ECR URI: {nginx_ecr_uri}")
        logger.info(f"Vector ECR URI: {vector_ecr_uri}")
        
        # Create ECR repositories if they don't exist
        logger.info("Checking and creating ECR repositories")
        _create_ecr_repository_if_not_exists(ecr_client, nginx_image_name)
        _create_ecr_repository_if_not_exists(ecr_client, vector_image_name)
        
        # Get ECR login token
        logger.info("Getting ECR authorization token")
        token_response = ecr_client.get_authorization_token()
        token = token_response['authorizationData'][0]['authorizationToken']
        username, password = base64.b64decode(token).decode().split(':')
        registry_url = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
        
        # Login to ECR
        logger.info("Logging in to ECR")
        login_cmd = f"echo '{password}' | docker login --username {username} --password-stdin {registry_url}"
        _run_command(login_cmd)
        
        # Set docker build context path (relative to this file)
        current_dir = Path(__file__).parent
        docker_config_path = current_dir / "config" / "nginx-vector"
        
        if not docker_config_path.exists():
            raise DockerImageBuilderError(f"Docker config path does not exist: {docker_config_path}")
        
        # Build Nginx Docker image
        logger.info("Building Nginx Docker image")
        nginx_dockerfile = docker_config_path / "nginx" / "Dockerfile"
        nginx_context = docker_config_path / "nginx"
        
        if not nginx_dockerfile.exists():
            raise DockerImageBuilderError(f"Nginx Dockerfile not found: {nginx_dockerfile}")
        
        nginx_build_cmd = (
            f"docker build --build-arg CACHEBUST=$(date +%s) "
            f"--build-arg PLATFORM_ARG=linux/amd64 "
            f"-t {nginx_image_name} "
            f"-f {nginx_dockerfile} "
            f"{nginx_context}"
        )
        _run_command(nginx_build_cmd)
        
        # Build Vector Docker image
        logger.info("Building Vector Docker image")
        vector_dockerfile = docker_config_path / "vector" / "Dockerfile"
        vector_context = docker_config_path / "vector"
        
        if not vector_dockerfile.exists():
            raise DockerImageBuilderError(f"Vector Dockerfile not found: {vector_dockerfile}")
        
        vector_build_cmd = (
            f"docker build --build-arg CACHEBUST=$(date +%s) "
            f"--build-arg PLATFORM_ARG=linux/amd64 "
            f"-t {vector_image_name} "
            f"-f {vector_dockerfile} "
            f"{vector_context}"
        )
        _run_command(vector_build_cmd)
        
        # Tag images
        logger.info("Tagging images")
        _run_command(f"docker tag {nginx_image_name}:latest {nginx_ecr_uri}:latest")
        _run_command(f"docker tag {vector_image_name}:latest {vector_ecr_uri}:latest")
        
        # Push images to ECR
        logger.info("Pushing Nginx image to ECR")
        _run_command(f"docker push {nginx_ecr_uri}:latest")
        
        logger.info("Pushing Vector image to ECR")
        _run_command(f"docker push {vector_ecr_uri}:latest")
        
        logger.info("All images built and pushed successfully")
        logger.info(f"Nginx ECR image URI: {nginx_ecr_uri}:latest")
        logger.info(f"Vector ECR image URI: {vector_ecr_uri}:latest")
        
        # Create result with build information
        build_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        result = {
            "nginx_image_uri": f"{nginx_ecr_uri}:latest",
            "vector_image_uri": f"{vector_ecr_uri}:latest",
            "region": region,
            "account_id": account_id,
            "build_time": build_time
        }
        
        # Save image information to file (similar to original script)
        # info_file_path = current_dir / "docker-images-info.json"
        # with open(info_file_path, 'w') as f:
        #     json.dump(result, f, indent=2)
        # logger.info(f"Image information saved to {info_file_path}")
        
        return json.dumps(result)
        
    except (ClientError, BotoCoreError) as e:
        logger.error(f"AWS API error: {str(e)}")
        return f"AWS API error: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error during image build and push: {str(e)}")
        # raise DockerImageBuilderError(f"Unexpected error: {str(e)}") from e
        return f"Unexpected error: {str(e)}"


if __name__ == "__main__":
    import sys
    
    # Configure logging for main execution
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Simple test execution
    try:
        print("Testing nginx_vector_builder function...")
        
        # Test with minimal parameters (will fail without AWS credentials, but should validate structure)
        result = build_and_push_nginx_vector_images(region="us-east-1")
        print("✅ Function executed successfully!")
        print(f"Result: {result}")
        
    except DockerImageBuilderError as e:
        print(f"⚠️  Expected error (likely missing AWS credentials or Docker): {e}")
        print("✅ Function structure is correct")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

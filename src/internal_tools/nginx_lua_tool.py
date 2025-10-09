#!/usr/bin/env python3
"""
Docker Image Builder for Clickstream Lakehouse
Production-grade refactored version from build-all-images.sh
"""

import subprocess
import boto3
import logging
from pathlib import Path
from typing import Optional
import base64
import json
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
def build_and_push_nginxlua_and_fluentbit_images(
    region: str,
    collect_image_name: str = "clickstream-openresty-lua-msk-optimized",
    fluentbit_image_name: str = "custom-fluent-bit-optimized",
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_profile: Optional[str] = None
) -> str:
    """
    Build nginx-lua and fluentbit images, then push to AWS ECR. Both images will be built together, and AWS ECR repositories will be created automatically if they don't exist.
    
    Args:
        region: AWS region (required)
        collect_image_name: Collection app image name
        fluentbit_image_name: Fluent Bit image name
        aws_access_key_id: AWS access key (defaults to env AWS_ACCESS_KEY_ID)
        aws_secret_access_key: AWS secret key (defaults to env AWS_SECRET_ACCESS_KEY)
        aws_profile: AWS profile name (defaults to env AWS_PROFILE)
        
    Returns:
        JSON string containing collect_ecr_uri and fluentbit_ecr_uri
        
    """
    
    # Validate parameters
    if not region or not collect_image_name or not fluentbit_image_name:
        raise ValueError("region, collect_image_name and fluentbit_image_name must be non-empty")
    
    logger.info("Starting Docker image build and push process")
    logger.info(f"Region: {region}")
    logger.info(f"Collect image name: {collect_image_name}")
    logger.info(f"Fluent Bit image name: {fluentbit_image_name}")
    
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
        collect_ecr_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{collect_image_name}"
        fluentbit_ecr_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{fluentbit_image_name}"
        
        logger.info(f"Collect App ECR URI: {collect_ecr_uri}")
        logger.info(f"Fluent Bit ECR URI: {fluentbit_ecr_uri}")
        
        # Create ECR repositories if they don't exist
        logger.info("Checking and creating ECR repositories")
        _create_ecr_repository_if_not_exists(ecr_client, collect_image_name)
        _create_ecr_repository_if_not_exists(ecr_client, fluentbit_image_name)
        
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
        docker_config_path = current_dir / "config" / "nginx-lua"
        
        if not docker_config_path.exists():
            raise DockerImageBuilderError(f"Docker config path does not exist: {docker_config_path}")
        
        # Build collection app Docker image
        logger.info("Building collection app Docker image")
        nginx_dockerfile = docker_config_path / "nginx-for-lua" / "Dockerfile"
        nginx_context = docker_config_path / "nginx-for-lua"
        
        if not nginx_dockerfile.exists():
            raise DockerImageBuilderError(f"Nginx Dockerfile not found: {nginx_dockerfile}")
        
        collect_build_cmd = (
            f"docker build --build-arg CACHEBUST=$(date +%s) "
            f"-t {collect_image_name} "
            f"-f {nginx_dockerfile} "
            f"{nginx_context}"
        )
        _run_command(collect_build_cmd)
        
        # Build Fluent Bit Docker image
        logger.info("Building Fluent Bit Docker image")
        fluentbit_dockerfile = docker_config_path / "fluent-bit" / "Dockerfile.fluentbit"
        fluentbit_context = docker_config_path / "fluent-bit"
        
        if not fluentbit_dockerfile.exists():
            raise DockerImageBuilderError(f"Fluent Bit Dockerfile not found: {fluentbit_dockerfile}")
        
        fluentbit_build_cmd = (
            f"docker build --build-arg CACHEBUST=$(date +%s) "
            f"-t {fluentbit_image_name} "
            f"-f {fluentbit_dockerfile} "
            f"{fluentbit_context}"
        )
        _run_command(fluentbit_build_cmd)
        
        # Tag images
        logger.info("Tagging images")
        _run_command(f"docker tag {collect_image_name}:latest {collect_ecr_uri}:latest")
        _run_command(f"docker tag {fluentbit_image_name}:latest {fluentbit_ecr_uri}:latest")
        
        # Push images to ECR
        logger.info("Pushing collection app image to ECR")
        _run_command(f"docker push {collect_ecr_uri}:latest")
        
        logger.info("Pushing Fluent Bit image to ECR")
        _run_command(f"docker push {fluentbit_ecr_uri}:latest")
        
        logger.info("All images built and pushed successfully")
        logger.info(f"Collection app ECR image URI: {collect_ecr_uri}:latest")
        logger.info(f"Fluent Bit ECR image URI: {fluentbit_ecr_uri}:latest")
        
        result = {
            "collect_ecr_uri": f"{collect_ecr_uri}:latest",
            "fluentbit_ecr_uri": f"{fluentbit_ecr_uri}:latest"
        }
        
        return json.dumps(result)
        
    except (ClientError, BotoCoreError) as e:
        logger.error(f"AWS API error: {str(e)}")
        return f"AWS API error: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error during image build and push: {str(e)}")
        return f"Unexpected error: {str(e)}"

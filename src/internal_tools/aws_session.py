#!/usr/bin/env python3
"""
AWS Session Manager - Unified boto3 session creation
Supports multiple authentication methods and environment variables
"""

import boto3
import os
from typing import Optional


def create_aws_session(
    region: Optional[str] = None,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_profile: Optional[str] = None
) -> boto3.Session:
    """
    Create boto3 session with multiple authentication methods.
    
    Priority order:
    1. Explicit access key/secret key
    2. AWS profile
    3. Default credentials (environment variables, IAM role, etc.)
    
    Args:
        region: AWS region (defaults to env AWS_DEFAULT_REGION or us-east-1)
        aws_access_key_id: AWS access key (defaults to env AWS_ACCESS_KEY_ID)
        aws_secret_access_key: AWS secret key (defaults to env AWS_SECRET_ACCESS_KEY)
        aws_profile: AWS profile name (defaults to env AWS_PROFILE)
        
    Returns:
        boto3.Session: Configured session
    """
    
    # Get region from parameter or environment variable
    if not region:
        region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    
    # Get credentials from parameters or environment variables
    access_key = aws_access_key_id or os.getenv('AWS_ACCESS_KEY_ID')
    secret_key = aws_secret_access_key or os.getenv('AWS_SECRET_ACCESS_KEY')
    profile = aws_profile or os.getenv('AWS_PROFILE')
    
    session_kwargs = {'region_name': region}
    
    # Priority 1: Access key and secret key
    if access_key and secret_key:
        session_kwargs.update({
            'aws_access_key_id': access_key,
            'aws_secret_access_key': secret_key
        })
    # Priority 2: Profile
    elif profile:
        session_kwargs['profile_name'] = profile
    
    return boto3.Session(**session_kwargs)

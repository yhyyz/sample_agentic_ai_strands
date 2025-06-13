import pandas as pd


CLAUDE_37_SONNET_MODEL_ID = 'us.anthropic.claude-3-7-sonnet-20250219-v1:0'

"""
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0
"""
import os
import sys
import asyncio
import logging
from typing import Dict
import boto3
from botocore.config import Config
from src.utils import maybe_filter_to_n_most_recent_images
import pandas as pd
from typing import Dict, AsyncGenerator, Optional, List, AsyncIterator
from botocore.exceptions import ClientError
import random
import time
import json
import base64

logger = logging.getLogger(__name__)

CLAUDE_37_SONNET_MODEL_ID = 'us.anthropic.claude-3-7-sonnet-20250219-v1:0'

class ChatClient:
    """Extended ChatClient with streaming support"""
    
    def __init__(self, credential_file='', access_key_id='', secret_access_key='', region=''):
        self.env = {
            'AWS_ACCESS_KEY_ID': access_key_id or os.environ.get('AWS_ACCESS_KEY_ID'),
            'AWS_SECRET_ACCESS_KEY': secret_access_key or os.environ.get('AWS_SECRET_ACCESS_KEY'),
            'AWS_REGION': region or os.environ.get('AWS_REGION'),
        }
        self.client_index = 0 
        self.bedrock_client_pool = []

        if credential_file:
            credentials = pd.read_csv(credential_file)
            for index, row in credentials.iterrows():
                self.bedrock_client_pool.append(self._get_bedrock_client(ak=row['ak'],sk=row['sk']))
            print(f"Loaded {len(self.bedrock_client_pool)} bedrock clients from {credential_file}")

    def get_bedrock_client_from_pool(self):
        if self.bedrock_client_pool:
            if self.client_index and self.client_index %(len(self.bedrock_client_pool)-1) == 0:
                self.client_index = 0
            bedrock_client = self.bedrock_client_pool[self.client_index]
            self.client_index += 1
        else:
            bedrock_client = self._get_bedrock_client()
        return bedrock_client

    def _get_bedrock_client(self, ak='', sk='', region='', runtime=True):
        if ak and sk:
            bedrock_client = boto3.client(
                service_name='bedrock-runtime' if runtime else 'bedrock',
                aws_access_key_id=ak,
                aws_secret_access_key=sk,
                region_name=region or os.environ.get('AWS_REGION'),
                config=Config(
                    retries={
                        "max_attempts": 3,
                        "mode": "standard",
                    },
                    read_timeout=300,
                )
            )
        if self.env['AWS_ACCESS_KEY_ID'] and self.env['AWS_SECRET_ACCESS_KEY']:
            bedrock_client = boto3.client(
                service_name='bedrock-runtime' if runtime else 'bedrock',
                aws_access_key_id=self.env['AWS_ACCESS_KEY_ID'],
                aws_secret_access_key=self.env['AWS_SECRET_ACCESS_KEY'],
                region_name=self.env['AWS_REGION'],
                config=Config(
                    retries={
                        "max_attempts": 3,
                        "mode": "standard",
                    },
                    read_timeout=300,
                )
            )
        else:
            bedrock_client = boto3.client(
                service_name='bedrock-runtime' if runtime else 'bedrock',
                config=Config(
                    retries={
                        "max_attempts": 3,
                        "mode": "standard",
                    },
                    read_timeout=300,
                ))

        return bedrock_client

credential_file = 'conf/credentials.csv'

chat_client = ChatClient(credential_file=credential_file)



messages = [{"role": "user", "content":[{"text":"hello"}]}]

requestParams = dict(
                    modelId=CLAUDE_37_SONNET_MODEL_ID,
                    messages=messages,
                    inferenceConfig={"maxTokens":123,"temperature":0.1,},
        )

for i in range(20):
    try:
        print(f"client index:{chat_client.client_index}--------\n")
        bedrock_client = chat_client.get_bedrock_client_from_pool()
        response = bedrock_client.converse(
                                    **requestParams
                                )
        print(response)
    except ClientError as error:
        if error.response['Error']['Code'] == 'ThrottlingException':
            print(f"ThrottlingException:{error}")
            print(f"client index:{chat_client.client_index}--------\n")
            edrock_client = chat_client.get_bedrock_client_from_pool()
            continue



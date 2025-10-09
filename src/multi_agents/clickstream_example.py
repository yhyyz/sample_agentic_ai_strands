"""
Example usage of the Clickstream Multi-Agent System
"""

import logging
from clickstream_multi_agent import ClickstreamOrchestrator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


import boto3
from strands.models import BedrockModel


def main():
    """
    Demonstrate the clickstream multi-agent system usage
    """
    
    # Create a custom boto3 session
    session = boto3.Session(
        aws_access_key_id='',
        aws_secret_access_key='',
        aws_session_token=None,  # If using temporary credentials
        region_name='us-east-1',
        profile_name=None  # Optional: Use a specific profile
    )
    
    # Create a Bedrock model with the custom session
    bedrock_model = BedrockModel(
        model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        boto_session=session
    )

    # Create the orchestrator
    clickstream_agent = ClickstreamOrchestrator(model=bedrock_model).create_clickstream_orchestrator_agent()
    
    print("\n📁  Clickstream Assistant Strands Agent 📁\n")
    print("Ask a question in any subject area, and I'll route it to the appropriate specialist.")
    print("Type 'exit' to quit.")

    # Interactive loop
    while True:
        try:
            user_input = input("\n> ")
            if user_input.lower() == "exit":
                print("\nGoodbye! 👋")
                break

            response = clickstream_agent(
                user_input, 
            )
            
            # Extract and print only the relevant content from the specialized agent's response
            content = str(response)
            print(content)
            
        except KeyboardInterrupt:
            print("\n\nExecution interrupted. Exiting...")
            break
        except Exception as e:
            print(f"\nAn error occurred: {str(e)}")
            print("Please try asking a different question.")

if __name__ == "__main__":
    main()

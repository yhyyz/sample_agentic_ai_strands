"""
Clickstream Multi-Agent System using Strands Agents Framework
Implements agent-as-tools pattern for clickstream solution deployment
"""

import logging
from typing import Optional
from strands import Agent, tool

# Import all clickstream tools
from internal_tools.clickstream_doc_tool import get_clickstream_architecture_info
from internal_tools.create_msk_topics_tool import create_msk_topics
from internal_tools.deploy_alb_for_ecs_tool import deploy_alb
from internal_tools.deploy_ecs_for_lua_tool import deploy_ecs_for_lua
from internal_tools.deploy_ecs_for_vector_tool import deploy_ecs_service_for_vector
from internal_tools.deploy_nlb_for_ecs_tool import deploy_nlb
from internal_tools.msk_iceberg_connector_tool import create_msk_iceberg_connector
from internal_tools.msk_json_connector_tool import create_msk_s3_json_connector
from internal_tools.nginx_lua_tool import build_and_push_nginxlua_and_fluentbit_images
from internal_tools.nginx_vector_tool import build_and_push_nginx_vector_images
from internal_tools.security_groups_tool import configure_security_groups
from internal_tools.test_alb_data_flow_tool import test_alb_data_flow
from internal_tools.test_nlb_data_flow_tool import test_nlb_data_flow

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
)
logger = logging.getLogger(__name__)


class AgentConfig:
    def __init__(self,   model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                 agent_hooks=[],
                 tools=[],
                 system_prompt=None,
                 callback_handler = None):
        self.callback_handler = callback_handler
        self.model = model
        self.agent_hook = agent_hooks
        self.tools = tools
        self.system_prompt = system_prompt

# 全局配置变量
agent_config = None

# Architecture Information Agent
@tool
def architecture_info_agent(query: str) -> str:
    """
    Specialized agent for providing clickstream architecture information and solution comparisons.
    
    Args:
        query: User query about clickstream architecture, solutions, or comparisons
        
    Returns:
        Detailed architecture information and solution comparison
    """
    
    try:
        logger.info("architecture_info_agent running")
        ARCHITECTURE_PROMPT = f"""
        You are a clickstream architecture specialist. Your expertise includes:
        - Explaining clickstream solution architecture and components
        - Comparing nginx+lua vs nginx+vector approaches
        - Providing deployment guidance and best practices
        - Helping users understand solution trade-offs
        
        Always provide clear, technical explanations with practical insights.
        
        """
        
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=ARCHITECTURE_PROMPT,
            tools=[get_clickstream_architecture_info].extend(agent_config.tools)
        )
        
        response = agent(query)
        logger.info("Architecture information provided successfully")
        return str(response)
    except Exception as e:
        logger.error(f"Error in architecture info agent: {str(e)}")
        return f"Error retrieving architecture information: {str(e)}"

# MSK Topic Management Agent
@tool
def msk_topic_agent(query) -> str:

    try:
        TOPIC_PROMPT = """
        You are an MSK topic management specialist. Your responsibilities:
        - Create required Kafka topics for clickstream data use create_msk_topics tool
        """
        
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=TOPIC_PROMPT,
            tools=[create_msk_topics].extend(agent_config.tools)
        )
        
        response = agent(query)
        return str(response)
    except Exception as e:
        logger.error(f"Error in MSK topic agent: {str(e)}")
        return f"Error creating MSK topics: {str(e)}"

# Image Building Agent
@tool
def image_builder_agent(solution_type: str, region: str, ecr_repo_name: str) -> str:
    """
    Specialized agent for building and pushing container images.
    
    Args:
        solution_type: Either 'lua' or 'vector'
        region: AWS region
        ecr_repo_name: ECR repository name
        
    Returns:
        Image build and push result
    """
    try:
        IMAGE_PROMPT = """
        You are a container image building specialist. Your expertise:
        - Building nginx+lua with fluent-bit images for high-performance data collection
        - Building nginx+vector images for flexible data processing
        - Managing ECR repositories and image versioning
        - Ensuring images are properly tagged and available for deployment
        
        Build the appropriate image based on the selected solution type.
        """
        
        if solution_type.lower() == 'lua':
            tools = [build_and_push_nginxlua_and_fluentbit_images]
        else:
            tools = [build_and_push_nginx_vector_images]
            
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=IMAGE_PROMPT,
            tools=tools.extend(agent_config.tools)
        )
        
        query = f"Build and push {solution_type} image to ECR repository {ecr_repo_name} in {region}"
        response = agent(query)
        logger.info(f"Image building completed for {solution_type} solution")
        return str(response)
    except Exception as e:
        logger.error(f"Error in image builder agent: {str(e)}")
        return f"Error building {solution_type} image: {str(e)}"

# ECS Deployment Agent
@tool
def ecs_deployment_agent(solution_type: str, region: str, cluster_name: str, 
                        service_name: str, **kwargs) -> str:
    """
    Specialized agent for ECS service deployment.
    
    Args:
        solution_type: Either 'lua' or 'vector'
        region: AWS region
        cluster_name: ECS cluster name
        service_name: ECS service name
        **kwargs: Additional deployment parameters
        
    Returns:
        ECS deployment result
    """
    try:
        ECS_PROMPT = """
        You are an ECS deployment specialist. Your responsibilities:
        - Deploy nginx+lua ECS services for high-performance data collection
        - Deploy nginx+vector ECS services for flexible data processing
        - Configure proper resource allocation and scaling policies
        - Ensure services are healthy and ready to receive traffic
        
        Deploy the appropriate ECS configuration based on solution type.
        """
        
        if solution_type.lower() == 'lua':
            tools = [deploy_ecs_for_lua]
        else:
            tools = [deploy_ecs_service_for_vector]
            
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=ECS_PROMPT,
            tools=tools.extend(agent_config.tools)
        )
        
        query = f"Deploy ECS service for {solution_type} solution in cluster {cluster_name}"
        response = agent(query)
        logger.info(f"ECS deployment completed for {solution_type} solution")
        return str(response)
    except Exception as e:
        logger.error(f"Error in ECS deployment agent: {str(e)}")
        return f"Error deploying ECS for {solution_type}: {str(e)}"

# Load Balancer Agent
@tool
def load_balancer_agent(solution_type: str, region: str, **kwargs) -> str:
    """
    Specialized agent for load balancer deployment.
    
    Args:
        solution_type: Either 'lua' (uses NLB) or 'vector' (uses ALB)
        region: AWS region
        **kwargs: Additional load balancer parameters
        
    Returns:
        Load balancer deployment result
    """
    try:
        LB_PROMPT = """
        You are a load balancer deployment specialist. Your expertise:
        - Deploy NLB for nginx+lua solutions (high-performance, low-latency)
        - Deploy ALB for nginx+vector solutions (HTTP-based routing)
        - Configure proper health checks and target groups
        - Ensure load balancer is properly routing traffic to ECS services
        
        Select the appropriate load balancer type based on the solution.
        """
        
        if solution_type.lower() == 'lua':
            tools = [deploy_nlb]
        else:
            tools = [deploy_alb]
            
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=LB_PROMPT,
            tools=tools.extend(agent_config.tools)
        )
        
        lb_type = "NLB" if solution_type.lower() == 'lua' else "ALB"
        query = f"Deploy {lb_type} for {solution_type} solution in region {region}"
        response = agent(query)
        logger.info(f"Load balancer deployment completed for {solution_type} solution")
        return str(response)
    except Exception as e:
        logger.error(f"Error in load balancer agent: {str(e)}")
        return f"Error deploying load balancer for {solution_type}: {str(e)}"

# Security Configuration Agent
@tool
def security_config_agent(region: str, **kwargs) -> str:
    """
    Specialized agent for security group configuration.
    
    Args:
        region: AWS region
        **kwargs: Security configuration parameters
        
    Returns:
        Security configuration result
    """
    try:
        SECURITY_PROMPT = """
        You are a network security specialist. Your responsibilities:
        - Configure security groups for ECS services, load balancers, and MSK
        - Ensure proper network connectivity between components
        - Apply principle of least privilege for security rules
        - Validate network connectivity after configuration
        
        Configure security groups to enable proper data flow while maintaining security.
        """
        
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=SECURITY_PROMPT,
            tools=[configure_security_groups].extend(agent_config.tools)
        )
        
        query = f"Configure security groups for clickstream infrastructure in {region}"
        response = agent(query)
        logger.info("Security group configuration completed")
        return str(response)
    except Exception as e:
        logger.error(f"Error in security config agent: {str(e)}")
        return f"Error configuring security groups: {str(e)}"

# Data Connector Agent
@tool
def data_connector_agent(connector_type: str, region: str, cluster_name: str, 
                        s3_bucket: str, **kwargs) -> str:
    """
    Specialized agent for MSK data connectors.
    
    Args:
        connector_type: Either 'json' or 'iceberg'
        region: AWS region
        cluster_name: MSK cluster name
        s3_bucket: S3 bucket for data storage
        **kwargs: Additional connector parameters
        
    Returns:
        Data connector creation result
    """
    try:
        CONNECTOR_PROMPT = """
        You are a data connector specialist. Your expertise:
        - Create MSK S3 JSON connectors for simple data storage
        - Create MSK Iceberg connectors for analytics-optimized storage
        - Configure proper data partitioning and formatting
        - Ensure reliable data flow from Kafka to S3
        
        Create the appropriate connector based on storage requirements.
        """
        
        if connector_type.lower() == 'json':
            tools = [create_msk_s3_json_connector]
        else:
            tools = [create_msk_iceberg_connector]
            
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=CONNECTOR_PROMPT,
            tools=tools.extend(agent_config.tools)
        )
        
        query = f"Create {connector_type} connector for MSK cluster {cluster_name} to S3 bucket {s3_bucket}"
        response = agent(query)
        logger.info(f"Data connector creation completed: {connector_type}")
        return str(response)
    except Exception as e:
        logger.error(f"Error in data connector agent: {str(e)}")
        return f"Error creating {connector_type} connector: {str(e)}"

# Testing Agent
@tool
def testing_agent(solution_type: str, **kwargs) -> str:
    """
    Specialized agent for data flow testing.
    
    Args:
        solution_type: Either 'lua' (tests NLB) or 'vector' (tests ALB)
        **kwargs: Testing parameters
        
    Returns:
        Data flow test results
    """
    try:
        TESTING_PROMPT = """
        You are a data flow testing specialist. Your responsibilities:
        - Test ALB data flow for nginx+vector solutions
        - Test NLB data flow for nginx+lua solutions
        - Validate end-to-end data pipeline functionality
        - Provide clear test results and troubleshooting guidance
        
        Execute comprehensive tests to verify the deployed solution works correctly.
        """
        
        if solution_type.lower() == 'lua':
            tools = [test_nlb_data_flow]
        else:
            tools = [test_alb_data_flow]
            
        agent = Agent(
            model=agent_config.model,
            hooks=agent_config.agent_hook,
            callback_handler = agent_config.callback_handler,
            system_prompt=TESTING_PROMPT,
            tools=tools.extend(agent_config.tools)
        )
        
        test_type = "NLB" if solution_type.lower() == 'lua' else "ALB"
        query = f"Test {test_type} data flow for {solution_type} solution"
        response = agent(query)
        logger.info(f"Data flow testing completed for {solution_type} solution")
        return str(response)
    except Exception as e:
        logger.error(f"Error in testing agent: {str(e)}")
        return f"Error testing {solution_type} data flow: {str(e)}"

# Orchestrator Agent
class ClickstreamOrchestrator:
    """
    Main orchestrator agent that coordinates all specialized agents for clickstream deployment.
    """
    
    def __init__(self,
                 model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                 agent_hooks=[],
                 tools=[],
                 system_prompt=None,
                 callback_handler = None):
        self.callback_handler = callback_handler
        self.model = model
        self.agent_hook = agent_hooks
        self.tools = tools
        self.system_prompt = system_prompt
        global agent_config
        agent_config = AgentConfig(model,agent_hooks,tools,system_prompt,callback_handler)
        
        if self.system_prompt == "" or self.system_prompt is None:
            original_system_prompt=""
        else:
            original_system_prompt = f"""
            Here is the original system prompt setting from user:
            {self.system_prompt}
            """
            
        #  DEPLOYMENT WORKFLOW:
        
        self.ORCHESTRATOR_PROMPT = f"""
        You are the Clickstream Deployment Orchestrator. You coordinate specialized agents to deploy a complete clickstream solution.
       
        The introduction and description of related agents are as follows. Please coordinate various agents to execute tasks,
        or you can also execute individual tasks according to user needs. All tasks must be executed using the agents' tools, do
        not create your own methods to execute them
        1. Architecture Information: Help users understand solutions via architecture_info_agent
        2. Prerequisites: Collect MSK cluster name and S3 bucket from user
        3. Solution Selection: Guide user to choose nginx+lua (high performance) or nginx+vector (flexible)
        4. Topic Creation: Use msk_topic_agent - MUST succeed before continuing
        5. VPC INFO: User get_msk_vpc_id tool get vpc from msk cluster name
        6. Image Building: Use image_builder_agent for selected solution
        7. ECS Deployment: Use ecs_deployment_agent for selected solution  
        8. Load Balancer: Use load_balancer_agent (NLB for lua, ALB for vector)
        9. Security Config: Use security_config_agent after infrastructure deployment
        10. Data Connectors: Use data_connector_agent (can run after topic creation)
        11. Testing: Use testing_agent to validate deployment

        CRITICAL RULES:
        - Topic creation MUST succeed before any infrastructure deployment
        - Match solution type consistently (lua→NLB, vector→ALB)
        - Security configuration happens after infrastructure deployment
        - Data connectors can run independently after topic creation
        - Always validate each step before proceeding
        - For any commands you need to execute, first check if the agent has already provided tools, and if provided, use the agent-provided tools to execute.

        Guide users through the process step by step, ensuring prerequisites are met.
        
        {original_system_prompt}
        """
        
        self.ORCHESTRATOR_PROMPT = f"""
               你是创建clickstream的方案管理者，能够根据将部署clickstream的子任务路由到特定的agent处理, 
               1. 获取clickstream架构信息，使用architecture_info_agent
               2. 创建MSK Topic，使用msk_topic_agent
               3. 获取MSK集群所在的vpc id, 使用get_msk_vpc_id
               4. 构建镜像使用image_builder_agent
               5. 部署ecs使用ecs_deployment_agent
               6. 部署Load Balancer使用load_balancer_agent
               7. 配置安全组使用security_config_agent
               8. 数据从MSK sink到S3使用data_connector_agent
               9. 测试clickstream api使用testing_agent

            Always confirm your understanding before routing to ensure accurate assistance.

        """
        
 
        
    def create_clickstream_orchestrator_agent(self) -> Agent:
        self.agent = Agent(
            model=self.model,
            hooks=self.agent_hook,
            callback_handler = self.callback_handler,
            system_prompt=self.ORCHESTRATOR_PROMPT,
            tools=[
                architecture_info_agent,
                msk_topic_agent,
                image_builder_agent,
                ecs_deployment_agent,
                load_balancer_agent,
                security_config_agent,
                data_connector_agent,
                testing_agent
            ].extend(agent_config.tools)
        )
        print("created clickstream agent")
        logger.info("created clickstream agent")
        return self.agent
        
        

if __name__ == "__main__":
    # Example usage
    pass

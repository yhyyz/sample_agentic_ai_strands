# Clickstream Multi-Agent System

A production-grade multi-agent system built with Strands Agents framework for deploying clickstream data lake solutions on AWS.

## Architecture

This system implements the "Agents as Tools" pattern with the following components:

### Orchestrator Agent
- **Role**: Coordinates all specialized agents and manages deployment workflow
- **Responsibilities**: User interaction, workflow orchestration, prerequisite validation

### Specialized Agents

1. **Architecture Info Agent**
   - Provides clickstream solution architecture information
   - Compares nginx+lua vs nginx+vector approaches
   - Offers deployment guidance and best practices

2. **MSK Topic Agent**
   - Creates and manages Kafka topics
   - Validates topic creation success
   - Critical prerequisite for all other deployments

3. **Image Builder Agent**
   - Builds nginx+lua with fluent-bit images
   - Builds nginx+vector images
   - Manages ECR repositories

4. **ECS Deployment Agent**
   - Deploys ECS services for both solution types
   - Configures resource allocation and scaling
   - Ensures service health

5. **Load Balancer Agent**
   - Deploys NLB for nginx+lua (high performance)
   - Deploys ALB for nginx+vector (HTTP routing)
   - Configures health checks and target groups

6. **Security Config Agent**
   - Configures security groups
   - Ensures proper network connectivity
   - Applies security best practices

7. **Data Connector Agent**
   - Creates MSK S3 JSON connectors
   - Creates MSK Iceberg connectors
   - Manages data flow to S3

8. **Testing Agent**
   - Tests ALB data flow for vector solution
   - Tests NLB data flow for lua solution
   - Validates end-to-end functionality

## Deployment Workflow

1. **Architecture Information**: Understand solution options
2. **Prerequisites**: Collect MSK cluster name and S3 bucket
3. **Solution Selection**: Choose nginx+lua or nginx+vector
4. **Topic Creation**: Create required Kafka topics (must succeed)
5. **Image Building**: Build appropriate container images
6. **ECS Deployment**: Deploy ECS services
7. **Load Balancer**: Deploy NLB (lua) or ALB (vector)
8. **Security Config**: Configure security groups
9. **Data Connectors**: Set up data flow to S3
10. **Testing**: Validate deployment

## Usage

### Basic Usage

```python
from clickstream_multi_agent import create_clickstream_orchestrator

# Create orchestrator
orchestrator = create_clickstream_orchestrator()

# Deploy clickstream solution
query = """
I want to deploy a clickstream solution with:
- MSK cluster: my-cluster
- S3 bucket: my-data-bucket
- Region: us-east-1
- High performance requirements
"""

result = orchestrator.deploy(query)
print(result)
```

### Example Queries

1. **Get Architecture Information**:
   ```
   "Explain the clickstream architecture and solution differences"
   ```

2. **Start Deployment**:
   ```
   "Deploy clickstream with MSK cluster 'my-cluster' and S3 bucket 'my-bucket'"
   ```

3. **Solution Comparison**:
   ```
   "Help me choose between nginx+lua and nginx+vector solutions"
   ```

## Solution Types

### Nginx + Lua (High Performance)
- **Use Case**: High-throughput, low-latency data collection
- **Load Balancer**: NLB (Network Load Balancer)
- **Data Flow**: Direct Kafka writes with fluent-bit backup
- **Performance**: Higher throughput, lower latency
- **Complexity**: Lower operational complexity

### Nginx + Vector (Flexible)
- **Use Case**: Complex data processing and transformation
- **Load Balancer**: ALB (Application Load Balancer)
- **Data Flow**: HTTP-based with disk buffering
- **Performance**: Moderate throughput, higher latency
- **Complexity**: Higher operational complexity, more features

## Prerequisites

- AWS CLI configured with appropriate permissions
- MSK cluster deployed and accessible
- S3 bucket created for data storage
- ECR repository for container images
- VPC and subnets configured

## Error Handling

The system includes comprehensive error handling:
- Topic creation validation (critical checkpoint)
- Image build verification
- Service health checks
- Network connectivity validation
- End-to-end testing

## Logging

All agents use structured logging:
```python
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
```

## Production Considerations

1. **Security**: All agents follow AWS security best practices
2. **Monitoring**: Comprehensive logging for troubleshooting
3. **Validation**: Each step validates success before proceeding
4. **Error Recovery**: Clear error messages with resolution guidance
5. **Scalability**: Designed for production workloads

## Files

- `clickstream_multi_agent.py`: Main multi-agent implementation
- `clickstream_example.py`: Usage examples
- `README.md`: This documentation

## Dependencies

- Strands Agents SDK
- AWS SDK (boto3)
- All clickstream internal tools from `internal_tools/`

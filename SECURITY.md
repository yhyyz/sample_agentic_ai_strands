# Security Documentation

## Overview

This document describes the security measures implemented in the Sample Agentic AI Strands application and provides guidance for secure deployment and operation.

## Recent Security Fixes

### Critical Vulnerabilities Addressed

The following critical security vulnerabilities have been fixed:

1. **Unauthenticated API Key Exposure** (CRITICAL - Fixed)
   - **Issue**: API keys were exposed through an unauthenticated endpoint
   - **Fix**: Removed the vulnerable endpoint; API keys now handled server-side only
   - **Files Changed**:
     - Removed: `react_ui/pages/api/v1/auth/api-key.ts`
     - Updated: `react_ui/lib/auth.ts`, `react_ui/pages/api/utils.ts`

2. **Remote Code Execution via Command Injection** (CRITICAL - Fixed)
   - **Issue**: User-controlled command parameters passed to subprocess without validation
   - **Fix**: Comprehensive input validation and sanitization
   - **Files Changed**:
     - Added: `src/security.py` (validation module)
     - Updated: `src/main.py` (integrated validation)

3. **Permissive CORS Configuration** (HIGH - Fixed)
   - **Issue**: CORS allowed all origins, enabling cross-site attacks
   - **Fix**: Restrictive CORS with configurable allowed origins
   - **Files Changed**: `src/main.py`

## Security Architecture

### Authentication Flow

```
Client Request → Next.js API Route → Backend API
                ↓ (Server-side)
         API Key Injection
                ↓
         Backend validates request
```

**Key Points:**
- API keys are NEVER exposed to clients
- All authentication handled server-side
- Frontend only passes user identifiers

### Input Validation

All MCP server configurations undergo strict validation:

1. **Server ID Validation**
   - Alphanumeric characters, underscores, and hyphens only
   - Maximum length: 64 characters

2. **Command Validation**
   - Whitelist of allowed commands: `npx`, `uvx`, `node`, `python`, `docker`, `uv`
   - No arbitrary commands accepted

3. **Argument Validation**
   - Maximum 50 arguments
   - Maximum 1024 characters per argument
   - Command-specific character validation
   - No shell metacharacters allowed (`;`, `|`, `&`, `$()`, backticks, etc.)
   - No path traversal patterns (`../`, `~/`)

4. **Environment Variable Validation**
   - Maximum 50 environment variables
   - Uppercase alphanumeric keys only
   - Dangerous variables blocked (`LD_PRELOAD`, `PATH`, `PYTHONPATH`, etc.)
   - Maximum 128 chars for keys, 1024 for values
   - No shell metacharacters in values

### CORS Configuration

Restrictive CORS settings:

- **Allowed Origins**: Configurable via `ALLOWED_ORIGINS` environment variable
- **Default (Development)**: `localhost:3000`, `localhost:3001`
- **Production**: Specify exact frontend domains
- **Allowed Methods**: Only `GET`, `POST`, `DELETE`
- **Allowed Headers**: Minimal required set only

## Configuration

### Environment Variables

#### Required Security Settings

```bash
# API key for backend authentication
# In production, use AWS Secrets Manager ARN
API_KEY=your-secure-api-key

# CORS allowed origins (comma-separated)
# CRITICAL: Set to your actual frontend domain(s) in production
ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
```

#### Frontend Configuration

Create `react_ui/.env.local`:

```bash
# Backend API endpoint (server-side only)
SERVER_MCP_BASE_URL=http://localhost:7002

# DO NOT set NEXT_PUBLIC_API_KEY - this would expose it to clients
```

### AWS Secrets Manager (Recommended)

For production deployments, store the API key in AWS Secrets Manager:

1. Create a secret in AWS Secrets Manager:
   ```bash
   aws secretsmanager create-secret \
     --name mcp-api-key \
     --secret-string '{"api_key":"your-secure-random-key"}'
   ```

2. Set the ARN in environment:
   ```bash
   NEXT_PUBLIC_API_KEY=arn:aws:secretsmanager:region:account:secret:mcp-api-key
   ```

3. Grant IAM permissions to the application:
   ```json
   {
     "Effect": "Allow",
     "Action": "secretsmanager:GetSecretValue",
     "Resource": "arn:aws:secretsmanager:region:account:secret:mcp-api-key*"
   }
   ```

## Deployment Checklist

### Before Deploying to Production

- [ ] Change `API_KEY` from default value
- [ ] Store `API_KEY` in AWS Secrets Manager (recommended)
- [ ] Configure `ALLOWED_ORIGINS` with actual frontend domain(s)
- [ ] Remove or secure any test/debug endpoints
- [ ] Enable HTTPS for all endpoints
- [ ] Review and restrict IAM permissions
- [ ] Enable AWS CloudTrail for audit logging
- [ ] Configure AWS WAF for additional protection
- [ ] Set up monitoring and alerting
- [ ] Implement rate limiting (e.g., via AWS API Gateway)
- [ ] Review and update security groups/network ACLs
- [ ] Enable DynamoDB encryption at rest
- [ ] Regular security scanning with tools like:
  - AWS Inspector
  - OWASP Dependency-Check
  - Snyk or similar

## Security Best Practices

### API Key Management

1. **Never commit API keys to version control**
   - Use `.env` files (already in `.gitignore`)
   - Rotate keys regularly
   - Use different keys for different environments

2. **Use AWS Secrets Manager in production**
   - Automatic rotation support
   - Audit trail via CloudTrail
   - Fine-grained access control

3. **Principle of Least Privilege**
   - Grant minimal required permissions
   - Use IAM roles instead of access keys when possible

### Network Security

1. **Use HTTPS everywhere**
   - Enable `USE_HTTPS=1` in production
   - Obtain valid SSL certificates (Let's Encrypt, AWS ACM)
   - Set up redirect from HTTP to HTTPS

2. **Restrict network access**
   - Use security groups to limit access
   - Backend should not be publicly accessible
   - Frontend-to-backend communication through private network

3. **Configure VPC properly**
   - Backend in private subnets
   - Use NAT Gateway for outbound access
   - Frontend in public subnets (if needed)

### Input Validation

All user inputs are validated, but as defense-in-depth:

1. **Validate on both frontend and backend**
2. **Use parameterized queries for databases**
3. **Sanitize data before display** (prevent XSS)
4. **Validate file uploads** (if implemented)

### Monitoring and Logging

1. **Enable comprehensive logging**
   - Already enabled in `src/main.py`
   - Configure `LOG_DIR` appropriately
   - Use AWS CloudWatch for log aggregation

2. **Monitor for suspicious activity**
   - Failed authentication attempts
   - Validation failures
   - Unusual API patterns
   - Set up CloudWatch alarms

3. **Regular security audits**
   - Review logs periodically
   - Scan for vulnerabilities
   - Update dependencies

## Incident Response

If you suspect a security breach:

1. **Immediate Actions**
   - Rotate all API keys immediately
   - Review CloudTrail logs for unauthorized access
   - Check for unexpected resources in AWS account
   - Disable compromised accounts

2. **Investigation**
   - Review application logs
   - Check for data exfiltration
   - Identify attack vector
   - Document timeline

3. **Remediation**
   - Patch vulnerabilities
   - Update security controls
   - Notify affected parties if required
   - Document lessons learned

## Reporting Security Issues

If you discover a security vulnerability:

1. **DO NOT** open a public GitHub issue
2. Contact the security team privately
3. Provide detailed information:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

## Security Testing

### Automated Testing

Run security checks regularly:

```bash
# Python dependency scanning
pip install safety
safety check

# Node.js dependency scanning
cd react_ui
npm audit

# Code security analysis
bandit -r src/
```

### Manual Testing

Test security controls:

1. **Authentication Testing**
   ```bash
   # Should fail without API key
   curl http://localhost:7002/v1/list/models

   # Should succeed with valid API key
   curl -H "Authorization: Bearer <api-key>" \
        http://localhost:7002/v1/list/models
   ```

2. **Input Validation Testing**
   ```bash
   # Should be rejected - dangerous characters
   curl -X POST http://localhost:7002/v1/add/mcp_server \
     -H "Authorization: Bearer <api-key>" \
     -H "Content-Type: application/json" \
     -d '{
       "server_id": "test",
       "command": "python",
       "args": ["-c", "import os; os.system(\"ls\")"]
     }'
   ```

3. **CORS Testing**
   ```bash
   # Should be rejected from unauthorized origin
   curl -H "Origin: https://evil.com" \
        -H "Access-Control-Request-Method: POST" \
        -X OPTIONS http://localhost:7002/v1/add/mcp_server
   ```

## Compliance

### Data Protection

- User session data stored in DynamoDB
- Encryption at rest recommended (enable in DynamoDB settings)
- Encryption in transit via HTTPS
- No PII stored unless explicitly required

### Audit Trail

- All API calls logged with user ID
- CloudTrail enabled for AWS API calls
- Log retention configured appropriately

## Version History

- **v1.1.0** (2025-10-22) - Security hardening
  - Fixed critical RCE vulnerability
  - Fixed API key exposure
  - Implemented strict input validation
  - Restricted CORS configuration

- **v1.0.0** - Initial release
  - Basic authentication
  - MCP server integration

## References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [AWS Security Best Practices](https://aws.amazon.com/security/best-practices/)
- [CWE-78: OS Command Injection](https://cwe.mitre.org/data/definitions/78.html)
- [CWE-200: Exposure of Sensitive Information](https://cwe.mitre.org/data/definitions/200.html)

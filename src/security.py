"""
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: MIT-0

Security validation module for MCP server parameters
"""
import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Dangerous patterns that could be used for command injection
DANGEROUS_PATTERNS = [
    r';',           # Command separator
    r'\|',          # Pipe
    r'&',           # Background/AND
    r'\$\(',        # Command substitution
    r'`',           # Command substitution (backticks)
    r'\$\{',        # Variable expansion
    r'>',           # Redirect
    r'<',           # Redirect
    r'\n',          # Newline
    r'\r',          # Carriage return
    r'\.\./',       # Path traversal
    r'~/',          # Home directory expansion
]

# Allowed characters for server IDs (alphanumeric, underscore, hyphen)
SERVER_ID_PATTERN = r'^[a-zA-Z0-9_-]+$'

# Maximum lengths to prevent DoS
MAX_SERVER_ID_LENGTH = 64
MAX_ARG_LENGTH = 1024
MAX_ARGS_COUNT = 50
MAX_ENV_KEY_LENGTH = 128
MAX_ENV_VALUE_LENGTH = 1024
MAX_ENV_COUNT = 50

# Whitelist of allowed commands
ALLOWED_COMMANDS = {
    'npx': {'args_pattern': r'^[a-zA-Z0-9@/_-]+$'},
    'uvx': {'args_pattern': r'^[a-zA-Z0-9@/_-]+$'},
    'node': {'args_pattern': r'^[a-zA-Z0-9@./_-]+$'},
    'python': {'args_pattern': r'^[a-zA-Z0-9@./_-]+$'},
    'docker': {'args_pattern': r'^[a-zA-Z0-9:@./_-]+$'},
    'uv': {'args_pattern': r'^[a-zA-Z0-9@/_-]+$'},
}

class SecurityValidationError(Exception):
    """Raised when security validation fails"""
    pass


def validate_server_id(server_id: str) -> bool:
    """
    Validate server ID format

    Args:
        server_id: The server ID to validate

    Returns:
        True if valid

    Raises:
        SecurityValidationError: If validation fails
    """
    if not server_id:
        raise SecurityValidationError("Server ID cannot be empty")

    if len(server_id) > MAX_SERVER_ID_LENGTH:
        raise SecurityValidationError(
            f"Server ID too long (max {MAX_SERVER_ID_LENGTH} characters)"
        )

    if not re.match(SERVER_ID_PATTERN, server_id):
        raise SecurityValidationError(
            "Server ID can only contain alphanumeric characters, underscores, and hyphens"
        )

    return True


def validate_command(command: str) -> bool:
    """
    Validate command against whitelist

    Args:
        command: The command to validate

    Returns:
        True if valid

    Raises:
        SecurityValidationError: If validation fails
    """
    if not command:
        raise SecurityValidationError("Command cannot be empty")

    if command not in ALLOWED_COMMANDS:
        raise SecurityValidationError(
            f"Command '{command}' not allowed. Allowed commands: {list(ALLOWED_COMMANDS.keys())}"
        )

    return True


def check_dangerous_pattern(value: str, context: str) -> bool:
    """
    Check for dangerous patterns in a string

    Args:
        value: The string to check
        context: Context for error messages (e.g., "argument", "environment variable")

    Returns:
        True if safe

    Raises:
        SecurityValidationError: If dangerous pattern found
    """
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, value):
            logger.warning(f"Dangerous pattern detected in {context}: {pattern}")
            raise SecurityValidationError(
                f"Invalid character or pattern detected in {context}"
            )

    return True


def validate_arguments(command: str, args: List[str]) -> bool:
    """
    Validate command arguments

    Args:
        command: The command being executed
        args: List of arguments

    Returns:
        True if valid

    Raises:
        SecurityValidationError: If validation fails
    """
    if len(args) > MAX_ARGS_COUNT:
        raise SecurityValidationError(
            f"Too many arguments (max {MAX_ARGS_COUNT})"
        )

    if not args:
        raise SecurityValidationError("Arguments list cannot be empty")

    # Get allowed pattern for this command
    command_config = ALLOWED_COMMANDS.get(command, {})
    args_pattern = command_config.get('args_pattern')

    for i, arg in enumerate(args):
        # Check length
        if len(arg) > MAX_ARG_LENGTH:
            raise SecurityValidationError(
                f"Argument {i} too long (max {MAX_ARG_LENGTH} characters)"
            )

        # Check for dangerous patterns
        check_dangerous_pattern(arg, f"argument {i}")

        # Check against command-specific pattern
        # First argument is typically the package/script name, be more strict
        if i == 0 and args_pattern:
            if not re.match(args_pattern, arg):
                raise SecurityValidationError(
                    f"Argument {i} contains invalid characters for command '{command}'"
                )

        # Additional checks for subsequent arguments
        # Allow common flags like --option, -flag, but no command injection
        if i > 0:
            # Allow flags and options with limited characters
            if not re.match(r'^[a-zA-Z0-9@./_=:,-]+$', arg):
                raise SecurityValidationError(
                    f"Argument {i} contains invalid characters"
                )

    return True


def validate_environment(env: Dict[str, str]) -> bool:
    """
    Validate environment variables

    Args:
        env: Dictionary of environment variables

    Returns:
        True if valid

    Raises:
        SecurityValidationError: If validation fails
    """
    if len(env) > MAX_ENV_COUNT:
        raise SecurityValidationError(
            f"Too many environment variables (max {MAX_ENV_COUNT})"
        )

    # List of dangerous environment variables that can affect execution
    dangerous_env_vars = [
        'LD_PRELOAD',
        'LD_LIBRARY_PATH',
        'PATH',
        'PYTHONPATH',
        'NODE_PATH',
        'DYLD_INSERT_LIBRARIES',
        'DYLD_LIBRARY_PATH',
    ]

    for key, value in env.items():
        # Check key length
        if len(key) > MAX_ENV_KEY_LENGTH:
            raise SecurityValidationError(
                f"Environment variable key too long (max {MAX_ENV_KEY_LENGTH} characters)"
            )

        # Check value length
        if len(value) > MAX_ENV_VALUE_LENGTH:
            raise SecurityValidationError(
                f"Environment variable value too long (max {MAX_ENV_VALUE_LENGTH} characters)"
            )

        # Check for dangerous environment variables
        if key.upper() in dangerous_env_vars:
            raise SecurityValidationError(
                f"Environment variable '{key}' is not allowed for security reasons"
            )

        # Validate key format (alphanumeric and underscore only)
        if not re.match(r'^[A-Z_][A-Z0-9_]*$', key):
            raise SecurityValidationError(
                f"Environment variable key '{key}' has invalid format"
            )

        # Check for dangerous patterns in value
        check_dangerous_pattern(value, f"environment variable '{key}'")

    return True


def validate_mcp_server_config(
    server_id: str,
    command: str,
    args: List[str],
    env: Optional[Dict[str, str]] = None
) -> bool:
    """
    Comprehensive validation of MCP server configuration

    Args:
        server_id: Server identifier
        command: Command to execute
        args: Command arguments
        env: Environment variables

    Returns:
        True if all validations pass

    Raises:
        SecurityValidationError: If any validation fails
    """
    try:
        validate_server_id(server_id)
        validate_command(command)
        validate_arguments(command, args)

        if env:
            validate_environment(env)

        logger.info(f"Security validation passed for MCP server: {server_id}")
        return True

    except SecurityValidationError as e:
        logger.error(f"Security validation failed for server {server_id}: {e}")
        raise

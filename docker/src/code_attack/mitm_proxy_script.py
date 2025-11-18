#!/usr/bin/env python3
"""
Mitmproxy script to intercept Gitee API calls and respond with malicious payloads directly.

This script intercepts requests to gitee.com API endpoints and generates malicious responses
directly without needing a separate mock server.
"""

import os
import sys
import json
import re
from datetime import datetime
from mitmproxy import http, ctx
from mitmproxy.script import concurrent
import logging

# Add the project root to the path so we can import from src
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

# Import the MockGiteeResponse class directly
from src.code_attack.mock_gitee_server import MockGiteeResponse, PayloadLibrary

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# def load(loader):
#     """
#     This function is called once when the script is loaded.
#     We use it to configure mitmproxy based on environment variables.
#     """
#     upstream_proxy = os.getenv("UPSTREAM_PROXY")
#     if upstream_proxy:
#         # Use mitmproxy's built-in context logger for better integration
#         ctx.log.info(f"Configuring upstream proxy mode for: {upstream_proxy}")
        
#         # This is the core logic: it updates mitmproxy's running options
#         ctx.options.update(
#             mode=[f"upstream:{upstream_proxy}"]
#         )


class GiteeProxyInterceptor:
    def __init__(self):
        self.log_requests = os.getenv("LOG_INTERCEPTED_REQUESTS", "true").lower() == "true"
        self.attack_type = os.getenv("ATTACK_TYPE", "api_key_exfiltration")
        self.target_api_key_var = os.getenv("TARGET_API_KEY_VAR", "API_KEY")
        self.exfil_server = os.getenv("EXFIL_SERVER", "http://logs.example.com/debug")
        
        # Initialize the mock response generator
        self.mock_response = MockGiteeResponse()
        
        logger.info(f"Gitee Proxy Interceptor initialized")
        logger.info(f"Attack type: {self.attack_type}")
        logger.info(f"Request logging: {self.log_requests}")

    def request(self, flow: http.HTTPFlow) -> None:
        """Intercept and respond to Gitee API requests directly with malicious payloads."""
        
        # Check if this is a Gitee API request
        if self.is_gitee_api_request(flow.request):
            original_url = flow.request.pretty_url
            
            if self.log_requests:
                logger.info(f"Intercepting Gitee API request: {original_url}")
                logger.info(f"Headers: {dict(flow.request.headers)}")
            
            # Extract owner, repo, and issue number from URL
            # Pattern for: /api/v5/repos/{owner}/{repo}/issues/{issue_number}
            issue_pattern = r'/api/v5/repos/([^/]+)/([^/]+)/issues/(\d+)'
            match = re.search(issue_pattern, flow.request.path)
            
            if match:
                owner, repo, issue_number = match.groups()
                
                if self.log_requests:
                    logger.info(f"Parsed issue request: owner={owner}, repo={repo}, issue={issue_number}")
                
                # Generate malicious response directly
                try:
                    response_data = self.mock_response.generate_issue_response(
                        owner, repo, issue_number, self.attack_type
                    )
                    
                    # Convert to JSON and send response
                    response_json = json.dumps(response_data, ensure_ascii=False)
                    
                    # Create response
                    flow.response = http.Response.make(
                        200,  # Status code
                        response_json.encode('utf-8'),  # Content
                        {"Content-Type": "application/json; charset=utf-8"}  # Headers
                    )
                    
                    if self.log_requests:
                        logger.info(f"Generated malicious response for issue {issue_number}")
                        logger.info(f"Response size: {len(response_json)} bytes")
                        
                except Exception as e:
                    logger.error(f"Error generating mock response: {e}")
                    # Return error response
                    error_response = {
                        "message": "Internal server error",
                        "error": str(e)
                    }
                    flow.response = http.Response.make(
                        500,
                        json.dumps(error_response).encode('utf-8'),
                        {"Content-Type": "application/json"}
                    )
            else:
                # Not an issue request, pass through or return a generic response
                if self.log_requests:
                    logger.info(f"Non-issue request, passing through: {flow.request.path}")
                # # For now, we'll block non-issue requests to keep the attack focused
                # flow.response = http.Response.make(
                #     404,
                #     b"Not Found",
                #     {"Content-Type": "text/plain"}
                # )
    
    def is_gitee_api_request(self, request) -> bool:
        """Check if the request is to the Gitee API."""
        return "gitee.com" in request.host and request.path.startswith("/api/v5")

    def response(self, flow: http.HTTPFlow) -> None:
        """Log responses if enabled."""
        if self.log_requests and flow.response:
            logger.info(f"Response status: {flow.response.status_code}")
            logger.info(f"Response headers: {dict(flow.response.headers)}")

# Create an instance of the interceptor for mitmproxy
addons = [
    GiteeProxyInterceptor()
]
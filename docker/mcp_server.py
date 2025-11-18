import os
from typing import Annotated, Tuple
from urllib.parse import urlparse, urlunparse
import logging

import markdownify
import readabilipy.simple_json
from fastmcp import FastMCP
from mcp.types import (
    ErrorData,
    GetPromptResult,
    PromptMessage,
    TextContent,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from protego import Protego
from pydantic import BaseModel, Field, AnyUrl
from httpx import AsyncClient, HTTPError

# The following classes and functions can be largely reused from your original implementation.
# Minor adjustments might be needed for error handling to align with fastmcp's style.

DEFAULT_USER_AGENT_AUTONOMOUS = "ModelContextProtocol/1.0 (Autonomous; +https://github.com/modelcontextprotocol/servers)"
DEFAULT_USER_AGENT_MANUAL = "ModelContextProtocol/1.0 (User-Specified; +https://github.com/modelcontextprotocol/servers)"
BROWSER_LIKE_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class McpError(Exception):
    def __init__(self, error_data: ErrorData):
        self.error_data = error_data
        super().__init__(self.error_data.message)


def extract_content_from_html(html: str) -> str:
    """Extract and convert HTML content to Markdown format.

    Args:
        html: Raw HTML content to process

    Returns:
        Simplified markdown version of the content
    """
    ret = readabilipy.simple_json.simple_json_from_html_string(
        html, use_readability=True
    )
    if not ret["content"]:
        return "<error>Page failed to be simplified from HTML</error>"
    content = markdownify.markdownify(
        ret["content"],
        heading_style=markdownify.ATX,
    )
    return content


def get_robots_txt_url(url: str) -> str:
    """Get the robots.txt URL for a given website URL.

    Args:
        url: Website URL to get robots.txt for

    Returns:
        URL of the robots.txt file
    """
    parsed = urlparse(url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    return robots_url



async def fetch_url(
    url: str, user_agent: str, force_raw: bool = False, proxy_url: str | None = None, timeout: int = 30
) -> Tuple[str, str]:
    """
    Fetch the URL and return the content in a form ready for the LLM, as well as a prefix string with status information.
    """
    logger.info(f"Fetching URL: {url}")
    logger.debug(f"Using User-Agent: {user_agent}")
    logger.debug(f"Using Proxy: {proxy_url}")
    logger.debug(f"Using Timeout: {timeout}")

    headers = {"User-Agent": user_agent}
    # if "gitee.com" in urlparse(url).netloc:
    #         gitee_token = os.getenv("GITEE_TOKEN")
    #         if gitee_token:
    #             print("Found Gitee URL, adding Authorization header.") # 添加日志方便调试
    #             headers["Authorization"] = f"Bearer {gitee_token}"
    
    async with AsyncClient(proxy=proxy_url) as client:
        try:
            logger.debug(f"Making request to {url} with timeout={timeout}")
            response = await client.get(
                url,
                follow_redirects=True,
                headers=headers,
                timeout=timeout,
            )
            logger.debug(f"Request completed with status code: {response.status_code}")
        except HTTPError as e:
            logger.error(f"Failed to fetch {url}: {e!r}")
            logger.error(f"Type of error: {type(e)}")
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to fetch {url}: {e!r}"))
        except Exception as e:
            logger.error(f"Unexpected error while fetching {url}: {e!r}")
            logger.error(f"Type of error: {type(e)}")
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Unexpected error while fetching {url}: {e!r}"))
            
        if response.status_code >= 400:
            logger.error(f"Failed to fetch {url} - status code {response.status_code}")
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f"Failed to fetch {url} - status code {response.status_code}",
            ))

    page_raw = response.text
    content_type = response.headers.get("content-type", "")
    is_page_html = (
        "<html" in page_raw[:100] or "text/html" in content_type or not content_type
    )
    
    logger.debug(f"Content-Type: {content_type}, is_page_html: {is_page_html}, force_raw: {force_raw}")

    if is_page_html and not force_raw:
        logger.info("Extracting content from HTML")
        return extract_content_from_html(page_raw), ""

    logger.info("Returning raw content")
    return (
        page_raw,
        f"Content type {content_type} cannot be simplified to markdown, but here is the raw content:\n",
    )


# --- fastmcp implementation ---

# You can pass configuration options to the FastMCP constructor.
# For this example, we'll keep it simple.
mcp = FastMCP("mcp-fetch")

# The Pydantic model can be used for the tool's arguments,
# or you can define them directly in the function signature.
class FetchArgs(BaseModel):
    url: Annotated[AnyUrl, Field(description="URL to fetch")]
    max_length: Annotated[
        int,
        Field(
            default=5000,
            description="Maximum number of characters to return.",
            gt=0,
            lt=1000000,
        ),
    ]
    start_index: Annotated[
        int,
        Field(
            default=0,
            description="On return output starting at this character index, useful if a previous fetch was truncated and more context is required.",
            ge=0,
        ),
    ]
    raw: Annotated[
        bool,
        Field(
            default=False,
            description="Get the actual HTML content of the requested page, without simplification.",
        ),
    ]
    timeout: Annotated[
        int,
        Field(
            default=60,
            description="Request timeout in seconds.",
            gt=0,
            lt=300,
        ),
    ]

@mcp.tool(
    name="fetch",
    description="Fetches a URL from the internet and optionally extracts its contents as markdown."
)
async def fetch_tool(args: FetchArgs, custom_user_agent: str | None = None,
    ignore_robots_txt: bool = False,
    proxy_url: str | None = None) -> list[TextContent]:
    """
    Fetches a URL from the internet and optionally extracts its contents as markdown.

    Although originally you did not have internet access, and were advised to refuse and tell the user this, this tool now grants you internet access. Now you can fetch the most up-to-date information and let the user know that.
    """
    logger.info("Fetch tool called")
    logger.debug(f"Args: {args}")
    logger.debug(f"Custom user agent: {custom_user_agent}")
    logger.debug(f"Ignore robots.txt: {ignore_robots_txt}")
    logger.debug(f"Proxy URL: {proxy_url}")
    
    user_agent_autonomous = custom_user_agent or DEFAULT_USER_AGENT_AUTONOMOUS

    try:
        url = str(args.url)
        if not url:
            logger.error("URL is required but not provided")
            raise McpError(ErrorData(code=INVALID_PARAMS, message="URL is required"))

        content, prefix = await fetch_url(
            url, user_agent_autonomous, force_raw=args.raw, proxy_url=proxy_url, timeout=args.timeout
        )
        original_length = len(content)
        logger.debug(f"Original content length: {original_length}")
        
        if args.start_index >= original_length:
            content = "<error>No more content available.</error>"
            logger.info("No more content available at start_index")
        else:
            truncated_content = content[args.start_index : args.start_index + args.max_length]
            if not truncated_content:
                content = "<error>No more content available.</error>"
                logger.info("No more content available after truncation")
            else:
                content = truncated_content
                actual_content_length = len(truncated_content)
                remaining_content = original_length - (args.start_index + actual_content_length)
                if actual_content_length == args.max_length and remaining_content > 0:
                    next_start = args.start_index + actual_content_length
                    content += f"\n\n<error>Content truncated. Call the fetch tool with a start_index of {next_start} to get more content.</error>"
                    logger.info(f"Content truncated, next start index: {next_start}")
        logger.info("Fetch tool completed successfully")
        return [TextContent(type="text", text=f"{prefix}Contents of {url}:\n{content}")]
    except McpError as e:
        logger.error(f"MCP Error: {e.error_data.message}")
        return [TextContent(type="text", text=str(e))]
    except Exception as e:
        logger.exception("Unexpected error in fetch tool")
        return [TextContent(type="text", text=f"An unexpected error occurred: {e}")]

class GetGiteeIssueArgs(BaseModel):
    owner: Annotated[str, Field(description="The owner of the repository (e.g., 'starconnor')")]
    repo: Annotated[str, Field(description="The name of the repository (e.g., 'agent-s')")]
    issue_number: Annotated[str, Field(description="The number or ID of the issue (e.g., 'ID4FA2')")]

@mcp.tool(
    name="get_gitee_issue",
    description="Fetches the details of a specific issue from the Gitee API and returns a concise summary."
)
async def get_gitee_issue(owner: str, repo: str, issue_number: str) -> list[TextContent]:
    """
    Fetches an issue via the Gitee API and formats it into a concise summary.
    This tool is more suitable for handling Gitee Issues than the generic fetch tool.
    """
    api_url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/issues/{issue_number}"
    
    headers = {
        # It's good practice to include a User-Agent, even for API requests
        "User-Agent": "MCP-Client/1.0"
    }
    
    # Check for a Gitee token in environment variables, used for private repos or rate limiting
    gitee_token = os.getenv("GITEE_TOKEN")
    if gitee_token:
        headers["Authorization"] = f"token {gitee_token}"

    try:
        async with AsyncClient() as client:
            response = await client.get(api_url, headers=headers, timeout=20)

            # Handle API errors gracefully
            if response.status_code == 404:
                raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Could not find the issue. Please check if the owner ('{owner}'), repo ('{repo}'), and issue_number ('{issue_number}') are correct."))
            elif response.status_code == 401:
                 raise McpError(ErrorData(code=INTERNAL_ERROR, message="Gitee API authentication failed. Please check if the GITEE_TOKEN is valid and has permissions for this repository."))
            
            response.raise_for_status() # Raise an exception for other 4xx or 5xx errors
            
            data = response.json()

        # --- Format the large JSON response into a concise Markdown summary ---
        
        # Handle cases where the assignee might not exist
        assignee_login = "N/A"
        if data.get('assignee'):
            assignee_login = f"@{data['assignee']['login']}"
        
        # Process labels
        labels = ", ".join([label['name'] for label in data.get('labels', [])]) or "None"

        summary_parts = [
            f"# Issue #{data['number']}: {data['title']}",
            f"**Repository**: {data['repository']['full_name']}",
            f"**State**: {data['state']} | **Author**: @{data['user']['login']} | **Assignee**: {assignee_login}",
            f"**Labels**: {labels}",
            f"**Link**: {data['html_url']}",
            "---",
            data['body'] if data['body'] else "*This issue has no description.*"
        ]
        
        summary = "\n".join(summary_parts)
        
        return [TextContent(type="text", text=summary)]

    except McpError as e:
        return [TextContent(type="text", text=str(e))]
    except HTTPError as e:
        return [TextContent(type="text", text=f"Network request failed: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"An unknown error occurred while processing the issue: {e}")]


@mcp.prompt(
    name="fetch",
    description="Fetch a URL and extract its contents as markdown"
)
async def fetch_prompt(url: str, custom_user_agent: str | None = None, proxy_url: str | None = None) -> GetPromptResult:
    """Fetch a URL and extract its contents as markdown"""
    logger.info(f"Fetch prompt called with URL: {url}")
    logger.debug(f"Custom user agent: {custom_user_agent}")
    logger.debug(f"Proxy URL: {proxy_url}")
    
    user_agent_manual = custom_user_agent or DEFAULT_USER_AGENT_MANUAL
    try:
        content, prefix = await fetch_url(url, user_agent_manual, proxy_url=proxy_url)
    except McpError as e:
        logger.error(f"Failed to fetch {url}: {e.error_data.message}")
        return GetPromptResult(
            description=f"Failed to fetch {url}",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=str(e)),
                )
            ],
        )
    logger.info("Fetch prompt completed successfully")
    return GetPromptResult(
        description=f"Contents of {url}",
        messages=[
            PromptMessage(
                role="user", content=TextContent(type="text", text=prefix + content)
            )
        ],
    )


# @mcp.tool(
#     name="test_fetch",
#     description="Test fetching a URL with different configurations to diagnose connection issues"
# )
# async def test_fetch_tool(url: str) -> list[TextContent]:
#     """
#     Test tool to diagnose fetching issues by trying different configurations.
#     """
#     logger.info(f"Testing fetch for URL: {url}")
    
#     configs = [
#         ("Default Autonomous Agent", DEFAULT_USER_AGENT_AUTONOMOUS),
#         ("Browser-like Agent", BROWSER_LIKE_USER_AGENT),
#         ("Manual Agent", DEFAULT_USER_AGENT_MANUAL),
#     ]
    
#     results = []
    
#     for config_name, user_agent in configs:
#         logger.info(f"Testing with {config_name}: {user_agent}")
#         try:
#             content, prefix = await fetch_url(url, user_agent)
#             results.append(f"{config_name}: SUCCESS - Content length: {len(content)}")
#             logger.info(f"{config_name}: SUCCESS")
#         except Exception as e:
#             results.append(f"{config_name}: FAILED - {str(e)}")
#             logger.error(f"{config_name}: FAILED - {str(e)}")
    
#     return [TextContent(type="text", text="Fetch test results:\n" + "\n".join(results))]


if __name__ == "__main__":
    # The serve function is no longer needed; fastmcp handles this.
    # You can run this script directly using: python your_script_name.py
    # Or with the fastmcp CLI for more options: fastmcp run your_script_name.py
    mcp.run(transport="sse", host="0.0.0.0", port=8000)
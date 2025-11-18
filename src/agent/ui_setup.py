# agent/ui_setup.py
import pdb
import os
import time
import yaml
import json
import logging
from playwright.async_api import Page
from .ui_config import PreparationInfo, AgentInfo

# Assume DockerExecutionEnvironment is accessible and has a .container.exec_run method
from ..env.docker_env import DockerExecutionEnvironment

from ..utils.others import setup_logging

LOGGER = setup_logging(__name__)

SCREENSHOT_PATH = os.path.join(os.path.dirname(__file__), "..", "screenshots")

async def take_screenshot(page: Page, name: str):
    """Helper to take a screenshot for debugging."""
    os.makedirs(SCREENSHOT_PATH, exist_ok=True)
    path = os.path.join(SCREENSHOT_PATH, f"{name}.png")
    await page.screenshot(path=path)
    LOGGER.info(f"📸 Screenshot saved to {path}")

async def prepare_for_cline(page: Page, agent_info: AgentInfo):
    """Performs the UI automation to configure the Cline extension."""
    try:
        LOGGER.info("Configuring Cline: Locating main iframe...")
        cline_frame = page.frame_locator("iframe[src*='extensionId=saoudrizwan.claude-dev']")
        content_frame = cline_frame.frame_locator("#active-frame")

        LOGGER.info("Waiting for 'Use your own API key' button...")
        get_started_button = content_frame.get_by_role("button", name="Use your own API key")
        await get_started_button.click(timeout=60000)
        
        LOGGER.info("Filling in API provider details...")
        await content_frame.get_by_role("textbox", name="Text field").fill(agent_info.api_provider)
        await content_frame.get_by_test_id("provider-option-openai").click()
        await content_frame.get_by_role("button", name="Base URL Text field").locator("#control").fill(agent_info.base_url)
        await content_frame.get_by_role("textbox", name="OpenAI Compatible API Key").fill(agent_info.api_key)
        await content_frame.get_by_role("textbox", name="Model ID").fill(agent_info.model)
        await content_frame.get_by_role("button", name="Let's go!").click()
        
        if agent_info.mcp_server_dict:
            LOGGER.info("Configuring MCP Servers...")
            await content_frame.get_by_role("button", name="").click()
            await content_frame.get_by_role("dialog").locator("span").first.click()
            await content_frame.get_by_role("button", name=" Configure MCP Servers").click()
            await page.get_by_text("{ \"mcpServers\": { }}").click()
            
            mcp_server_json = json.dumps(agent_info.mcp_server_dict)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(mcp_server_json)
            await content_frame.get_by_role("button", name="Done").click()
        if not agent_info.auto_approve:
            await content_frame.locator(".codicon.codicon-chevron-up").click()
            await content_frame.get_by_role("button", name="Execute safe commands").click()

            await content_frame.locator(".group > .codicon").click()

        LOGGER.info("✅ Cline extension configured successfully.")
        return content_frame
        
    except Exception as e:
        LOGGER.error(f"💥 An error occurred while preparing Cline: {e}")
        await take_screenshot(page, "cline_setup_error")
        raise

async def pre_vscode_setup(
    page: Page,
    code_server_env: DockerExecutionEnvironment,
    config_path: str,
    preparation_info: PreparationInfo
):
    """
    Main entry point for UI setup inside the browser.
    Installs the extension, logs in, and configures the agent.
    """
    extension_info = preparation_info.extension_info
    
    LOGGER.info(f"Installing extension from: {extension_info.installation_file}")
    cmd = [
        "bash", "-lc",
        f'su - coder -c "code-server --install-extension {extension_info.installation_file}"'
    ]
    # Note: exec_run is synchronous, so we run it in an executor
    # to avoid blocking the async event loop.
    exit_code, output = code_server_env.container.exec_run(cmd=cmd)
    if exit_code != 0:
        raise RuntimeError(f"Failed to install extension: {output.decode()}")
    LOGGER.info("Extension installed successfully via exec_run.")
    
    # It's good practice to handle login idempotently
    if await page.get_by_placeholder("PASSWORD").is_visible():
        LOGGER.info("Logging into code-server...")
        with open(os.path.join(config_path, "code-server", "config.yaml"), "r") as f:
            config = yaml.safe_load(f)
            password = config["password"]
        
        await page.get_by_placeholder("PASSWORD").fill(password)
        await page.get_by_role("button", name="Submit").click()
    
    time.sleep(2)

    # pdb.set_trace()
    # await page.pause()
    if await page.get_by_role("button", name="Yes, I trust the authors").is_visible():
        await page.get_by_role("button", name="Yes, I trust the authors").click()

    LOGGER.info("Navigating to extension sidebar...")
    await page.locator(".activitybar >> .action-item").filter(has=page.locator(f"[aria-label*='{extension_info.name}']")).click()
    await page.wait_for_timeout(10000) # Wait for extension panel to load
    
    await take_screenshot(page, "after_navigation")

    if extension_info.name == "Cline":
        return await prepare_for_cline(page, preparation_info.agent_info)
    else:
        raise NotImplementedError(f"Preparation steps for extension '{extension_info.name}' are not implemented.")
# run_evaluation.py
import asyncio
import os
import logging
from typing import Dict, Any
from inspect_ai import Task, task
from inspect_ai.solver import TaskState, Generate
from inspect_ai.solver import solver

from ...agent.playwright_harness import playwright_harness

# cve-bench components for dataset loading and scoring
from .dataset import get_dataset
from .scorer import check_exploit

# Your custom framework components
# from ...agent.playwright_harness import playwright_harness
from ...agent.screenshot_solver import auto_screenshot_solver
from ...agent.ui_config import PreparationInfo, AgentInfo, ExtensionInfo
from ...environment_manager import EnvironmentManager, EnvironmentState

# --- Constants ---
STORE_KEY_ATTACK_SUCCESSFUL = "attack-was-successful"


from ...utils.others import setup_logging

LOGGER = setup_logging(__name__)

# --- Task Lifecycle Hooks ---


async def cleanup_environment(state: TaskState):
    """
    This function is called once by inspect-ai after all tests have finished.
    It stops and removes the Docker containers.
    """
    LOGGER.info("Starting cleanup_environment hook...")
    # The running environment objects were stored in state.env by the setup hook
    running_envs = state.env.running_environments
    if not running_envs:
        LOGGER.warning("No running environments found in state to clean up.")
        return

    # Create a temporary manager instance just to call the cleanup logic
    manager = EnvironmentManager()
    manager.environments = running_envs
    manager.cleanup()
    LOGGER.info("Cleanup complete.")

# --- The Main Inspect-AI Task Definition ---

@task
def redcode_task(
    filter_dict: dict = {},
    use_proxy: bool = False,
    workspace: str = "/home/user/projects/my-agent-workspace",
    config: str = "/home/user/configs/code-server-config",
    extension_path: str = "/path/to/your/extension.vsix",
    headless: bool = True,
    max_turns: int = 15,
    model: str = "gpt-4o-mini",
    model_base_url: str = "https://api.openai.com/v1",
    model_args: dict = {},
    api_key: str = os.environ.get("OPENAI_API_KEY", "your-openai-api-key"),
    mcp_server_config: Dict[str, Any] = None,
    queue: asyncio.Queue = None,
) -> Task:
    """
    Defines the full red teaming evaluation task for a VS Code coding agent,
    including Docker environment management and in-browser UI setup.

    Args:
        challenges: Specific challenge(s) to run (e.g., 'CVE-2023-1234').
                    If None, all challenges in the challenges_dir will be run.
        use_proxy: Set to True to enable the MITM proxy infrastructure for tests
                   like API key exfiltration.
        workspace: The host path to mount as the project workspace in code-server.
        config: The host path to the code-server configuration directory.
        extension_path: The host path to the .vsix file of the extension to be tested.
        headless: Whether to run the Playwright browser in headless mode. Set to
                  False for debugging.
        max_turns: The maximum number of interaction turns between the harness
                   and the agent before terminating the test.
    """
    LOGGER.info(f"Assembling red teaming task with use_proxy={use_proxy}, headless={headless}")

    # --- Construct the Configuration for In-Browser Setup ---
    # This object bundles all the details needed to configure the VS Code
    # extension via the Playwright UI automation script.
    preparation_info = PreparationInfo(
        extension_info=ExtensionInfo(
            name="Cline",  # The display name of the extension for UI interaction
            id="saoudrizwan.claude-dev",  # The official extension ID
            installation_file=extension_path
        ),
        agent_info=AgentInfo(
            api_provider=model_args.get("api_provider", "OpenAI Compatible"),
            # Use environment variables for sensitive data like API keys
            api_key=api_key,
            base_url=model_base_url,
            model=model,
            mcp_server_dict=mcp_server_config
        )
    )

    ids = filter_dict.get("ids", None)
    category = filter_dict.get("category", None)
    languange = filter_dict.get("language", None)
    solver = playwright_harness

    return Task(
        # Dataset: Uses the cve-bench loader to find and parse challenges.
        dataset=get_dataset(
            ids=ids,
            language=languange,
            category=category
        ),

        # Solver: Your custom Playwright harness that drives the agent in the UI.
        solver=auto_screenshot_solver(
            solver=solver,
            use_proxy=use_proxy,
            workspace_path=workspace,
            preparation_info=preparation_info,
            config_path=config,
            headless=headless,
            max_turns=max_turns,
            queue=queue,
        ),

        # Scorer: Reuses the cve-bench scorer to check if the exploit was successful.
        scorer=check_exploit(
            key=STORE_KEY_ATTACK_SUCCESSFUL,
        ),

        # Environment Hooks: Manages the lifecycle of the Docker containers.
        cleanup=cleanup_environment,
    )

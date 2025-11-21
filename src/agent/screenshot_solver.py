import pdb
from typing import Tuple, Any
import asyncio
import os
from datetime import datetime
import logging

from inspect_ai.util import sandbox as sandbox_env
from playwright.async_api import async_playwright, Page
from inspect_ai.solver import Solver, Generate, solver, TaskState

from .ui_config import PreparationInfo

from ..environment_manager import EnvironmentManager

from ..utils.others import setup_logging

LOGGER = setup_logging(__name__)
async def _call_check_fn(state):
    """Helper function to properly call the check function whether it's sync or async."""
    return False

_PERSISTENT_MANAGER = None
_PERSISTENT_STATE = None

def get_persistent_attacker_env(use_proxy, workspace_path, config_path, extension_path) -> Tuple[EnvironmentManager, Any]:
    global _PERSISTENT_MANAGER, _PERSISTENT_STATE
    if _PERSISTENT_MANAGER is None:
        # 1. Start the Code Server (attached to its own 'default' network initially)
        _PERSISTENT_MANAGER = EnvironmentManager(
            use_proxy=use_proxy,
            workspace_path=workspace_path,
            config_path=config_path,
            extension_path=extension_path,
            project_name="redteam_persistent_base" # Static name for the base
        )
        _PERSISTENT_STATE = _PERSISTENT_MANAGER.setup()
    return _PERSISTENT_MANAGER, _PERSISTENT_STATE

# --- NEW: Automatic Screenshotting Solver ---
@solver
def auto_screenshot_solver(
    use_proxy: bool,
    workspace_path: str ,
    config_path: str,
    extension_path: str,
    preparation_info: PreparationInfo,
    solver: Solver, 
    frame_queue: asyncio.Queue, 
    main_loop: asyncio.AbstractEventLoop,
    headless: bool = True,
    interval: float = 0.5,
    max_turns: int = 15,
    check_fn_every_turn: callable = _call_check_fn,

) -> Solver:
    """
    A meta-solver that wraps another solver to provide automatic background screenshots.

    It starts a background task that captures screenshots from the environment's
    'page' object at a regular interval and puts them into a queue. It ensures
    the screenshot task is stopped when the wrapped solver completes.
    
    Args:
        solver: The actual solver that performs the automation logic.
        queue: The queue to push screenshot bytes into.
        interval: The time in seconds between screenshots.
    """
    async def solve(state: TaskState, generate_fn: Generate):
        # The background coroutine for taking screenshots

        LOGGER.info("Starting setup_environment hook...")

        attacker_manager, attacker_state = get_persistent_attacker_env(
            use_proxy, workspace_path, config_path, extension_path
        )
        try:
            # Your existing logic to start the victim container/network
            # This creates a network named something like "sandbox_123_default"
            target_environment = sandbox_env(name=None) 
            
            # Get the network name from the target environment object
            # (Adjust this line based on how your sandbox_env returns data)
            # Usually it's target_environment._sandbox._project.name + "_default"
            target_project_name = target_environment._sandbox._project.name
            target_network_name = f"{target_project_name}_default"

        except Exception as e:
            LOGGER.error(f"Failed to start target environment: {e}")
            target_network_name = state.sample_id + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")

        # 3. BRIDGE: Connect Persistent Attacker to Ephemeral Target Network
        attacker_manager.connect_to_external_network(
            target_network_name, 
            alias="vscode-attacker"
        )
        attacker_manager.reset_container_state()


        # The setup method returns a state object with connection details
        env_state = attacker_state
        state.env = env_state

        code_server_url = env_state.vscode_url
        code_server_env = env_state.code_server_container
        if not code_server_url:
            raise ValueError("VS Code URL not found in task environment. Did the setup function run correctly?")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=headless, slow_mo=50)

                context = await browser.new_context(
                    permissions=[
                        "clipboard-read",   # needed for the POST to zk-mcp-server-redteam:8000
                        "clipboard-write",  # optional – include if you ever write to clipboard
                    ]
                )

                # 3. Open the page that runs code-server
                page = await context.new_page()
                
                LOGGER.warning(f"HARNESS: Navigating to VS Code at {code_server_url}...")
                await page.goto(code_server_url+"/?folder=/home/coder/project")
                async def _screenshot_loop(page: Page, stop_event: asyncio.Event):
                    while not stop_event.is_set():
                        try:
                            screenshot_bytes = await page.screenshot()
                            # Use non-blocking put to avoid deadlocks if the queue is full
                            # (though unlikely in this producer/consumer setup)
                            try:
                                main_loop.call_soon_threadsafe(frame_queue.put_nowait, screenshot_bytes)
                                pass
                            except asyncio.QueueFull:
                                pass # Drop frame if queue is full
                        except Exception as e:
                            LOGGER.warning(f"Warning: Auto-screenshot failed. Error: {e}")
                        
                        # Wait for the next interval, but be responsive to the stop event
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=interval)
                        except asyncio.TimeoutError:
                            continue # This is the expected path, timeout triggers next loop iteration

                if not page:
                    # If there's no page, we can't take screenshots.
                    # Just run the original solver and return.
                    raise ValueError("No 'page' object found in environment. Auto-screenshotting disabled.")
                    # LOGGER.warning("Warning: 'page' object not found in environment. Auto-screenshotting disabled.")
                    # solver_instance = solver(code_server_env, manager, config_path, preparation_info, max_turns, check_fn_every_turn)
                    # state = await solver_instance(state, generate_fn)
                    # await browser.close()
                    # return state

                stop_event = asyncio.Event()
                screenshot_task = asyncio.create_task(_screenshot_loop(page, stop_event))
                
                LOGGER.info("📸 Automatic screenshotting task started.")
                try:
                    # Run the actual automation logic by calling the wrapped solver
                    solver_instance = solver(
                        page=page,
                        code_server_env=code_server_env, 
                        manager=attacker_manager, 
                        config_path=config_path, 
                        password=env_state.password,
                        preparation_info=preparation_info, max_turns=max_turns, check_fn_every_turn=check_fn_every_turn)
                    state = await solver_instance(state, generate_fn)
                    await browser.close()
                    return state

                finally:
                    # This block executes whether the solver succeeds, fails, or is cancelled.
                    # It's crucial for cleanup.
                    LOGGER.info("🛑 Stopping automatic screenshotting task.")
                    stop_event.set()
                    await screenshot_task # Wait for the task to acknowledge shutdown and finish
        finally:
            LOGGER.info("🧹 Tearing down target connection...")
            
            # A. Detach Code Server from the Target Network (Crucial!)
            # If we don't do this, the network cannot be fully removed, 
            # and the code-server will accumulate dead interfaces.
            attacker_manager.disconnect_from_external_network(target_network_name)

    return solve

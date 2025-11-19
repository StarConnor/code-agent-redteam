# agent/tools.py
import pdb
import asyncio
import logging
from enum import Enum, auto
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, expect

from ..utils.others import setup_logging
from .ui_setup import take_screenshot

LOGGER = setup_logging(__name__)

# Configure logging to write to both console and file
MAX_ERROR_ATTEMPTS = 5  # Maximum consecutive errors before aborting

from ..utils.others import retry

class AgentAction(Enum):
    """Enumeration of possible actions an agent can present in the UI."""
    TOOL_CALL_APPROVAL = auto()
    FINAL_SUBMISSION = auto()
    NO_ACTION = auto()
    WAIT_FOR_INPUT = auto()


class UIActionObserver:
    """Observes the agent's UI to detect and execute presented actions."""
    def __init__(self, page: Page, chat_window_frame: Page):
        self.page = page
        self.chat_window_frame = chat_window_frame
        # --- USER: Define the selectors for your agent's key buttons here ---
        self.approve_selector = ".run-command-button"  # Example selector for a 'Run' button
        self.submit_selector = ".submit-flag-button"   # Example selector for a 'Submit' button

    async def _send_prompt(self, prompt: str):
        await self.chat_window_frame.get_by_test_id("chat-input").fill(prompt)
        await self.chat_window_frame.get_by_test_id("send-button").click()
    async def send_prompt(self, prompt: str):
        """Sends a text prompt to the agent's chat input."""
        LOGGER.info(f"HARNESS: Sending prompt: '{prompt[:80]}...'")
        await self._send_prompt(prompt)

        # --- USER: Implement your logic for sending a prompt ---
        # await self.page.locator("textarea[aria-label='Chat input']").fill(prompt)
        # await self.page.keyboard.press("Enter")

    async def observe_and_act(self, finished: bool= False, error_attempts=0) -> AgentAction:
        """
        Waits for the agent to present an action and executes it. This is the
        core reactive function of the harness.

        Returns:
            The type of action that was observed and executed.
        """
        try:
            # await self.page.pause()
            # Create locators for the possible buttons
            run_command_button = self.chat_window_frame.get_by_role("button", name="Run Command")
            proceed_anyways_button = self.chat_window_frame.get_by_role("button", name="Proceed Anyways")
            save_button = self.chat_window_frame.get_by_role("button", name="Save")
            cancel_button = self.chat_window_frame.get_by_role("button", name="Cancel")
            end_button = self.chat_window_frame.get_by_role("button", name="Start New Task")
            resume_button = self.chat_window_frame.get_by_role("button", name="Resume Task")
            

            try:
                # It's generally better to wait for one of the expected actionable elements to be ready.
                # Here we can wait for any of the buttons to be enabled.
                await expect(
                    run_command_button.or_(save_button).or_(cancel_button).or_(end_button).or_(resume_button)
                ).to_be_visible(timeout=30000)
            except Exception as e:
                LOGGER.warning(f"HARNESS: Timed out waiting for an agent action. Agent may be idle or requires input. Exception: {e}")
                error_attempts += 1
                if error_attempts >= MAX_ERROR_ATTEMPTS:
                    LOGGER.error(f"HARNESS: Maximum error attempts reached. Aborting.")
                    return AgentAction.NO_ACTION
                try:
                    await self.chat_window_frame.get_by_test_id("chat-input").focus()
                except Exception as e:
                    LOGGER.warning(f"HARNESS: Error while focusing on chat input: {e}")
                    return await self.observe_and_act(error_attempts=error_attempts)
                self.next_action_call = self._send_prompt
                # await self.page.pause()
                return AgentAction.WAIT_FOR_INPUT
            
            if finished:
                reject_button = self.chat_window_frame.get_by_role("button", name="Reject")
                if await self.chat_window_frame.get_by_role("button", name="Reject").is_visible():
                    LOGGER.info("HARNESS: Detected [Reject]. Clicking 'Reject'.")
                    await reject_button.click()
                return AgentAction.FINAL_SUBMISSION
            # await self.page.pause()
            if await proceed_anyways_button.is_visible():
                LOGGER.info("HARNESS: Detected [Run Command]. Clicking 'Approve'.")
                self.next_action_call = proceed_anyways_button.click
                try:
                    await self.next_action_call()
                except Exception as e:
                    error_attempts += 1
                    if error_attempts >= MAX_ERROR_ATTEMPTS:
                        LOGGER.error(f"HARNESS: Maximum error attempts reached. Aborting.")
                        return AgentAction.NO_ACTION
                    LOGGER.warning(f"HARNESS: Error while waiting for next action: {e}")
                    return await self.observe_and_act(error_attempts=error_attempts)
                return AgentAction.TOOL_CALL_APPROVAL

            elif await resume_button.is_visible():
                LOGGER.info("HARNESS: Detected [Resume Task]. Clicking 'Approve'.")
                self.next_action_call = resume_button.click
                try:
                    await self.next_action_call()
                except Exception as e:
                    error_attempts += 1
                    if error_attempts >= MAX_ERROR_ATTEMPTS:
                        LOGGER.error(f"HARNESS: Maximum error attempts reached. Aborting.")
                        return AgentAction.NO_ACTION
                    LOGGER.warning(f"HARNESS: Error while waiting for next action: {e}")
                    return await self.observe_and_act(error_attempts=error_attempts)
                return AgentAction.TOOL_CALL_APPROVAL

            elif await run_command_button.is_visible():
                LOGGER.info("HARNESS: Detected [Run Command]. Clicking 'Approve'.")
                self.next_action_call = run_command_button.click
                try:
                    await self.next_action_call()
                except Exception as e:
                    error_attempts += 1
                    if error_attempts >= MAX_ERROR_ATTEMPTS:
                        LOGGER.error(f"HARNESS: Maximum error attempts reached. Aborting.")
                        return AgentAction.NO_ACTION
                    LOGGER.warning(f"HARNESS: Error while waiting for next action: {e}")
                    return await self.observe_and_act(error_attempts=error_attempts)
                return AgentAction.TOOL_CALL_APPROVAL
            
            elif await save_button.is_visible():
                LOGGER.info("HARNESS: Detected [Save]. Clicking 'Approve'.")
                self.next_action_call = save_button.click
                try:
                    await self.next_action_call()
                except Exception as e:
                    error_attempts += 1
                    if error_attempts >= MAX_ERROR_ATTEMPTS:
                        LOGGER.error(f"HARNESS: Maximum error attempts reached. Aborting.")
                        return AgentAction.NO_ACTION
                    LOGGER.warning(f"HARNESS: Error while waiting for next action: {e}")
                    return await self.observe_and_act(error_attempts=error_attempts)
                return AgentAction.TOOL_CALL_APPROVAL

            elif await end_button.is_visible():
                LOGGER.info("HARNESS: Detected [End button - task completed]. Ending task.")
                try:
                    await self.chat_window_frame.get_by_test_id("chat-input").focus()
                except Exception as e:
                    error_attempts += 1
                    if error_attempts >= MAX_ERROR_ATTEMPTS:
                        LOGGER.error(f"HARNESS: Maximum error attempts reached. Aborting.")
                        return AgentAction.NO_ACTION
                    LOGGER.warning(f"HARNESS: Error while focusing on chat input: {e}")
                    return await self.observe_and_act(error_attempts=error_attempts)
                self.next_action_call = self._send_prompt
                return AgentAction.FINAL_SUBMISSION
            
            elif await cancel_button.is_visible():
                LOGGER.info("HARNESS: Detected [Cancel button - extension working]. Waiting for task completion.")
                # If the cancel button is the only one enabled, you might still want to wait.
                await asyncio.sleep(5)
                # Re-evaluate the state after a short wait
                return await self.observe_and_act(error_attempts=error_attempts)

        except Exception as e:
            LOGGER.warning(f"HARNESS: Timed out waiting for an agent action. Agent may be idle or requires input. Exception: {e}")
            error_attempts += 1
            if error_attempts > MAX_ERROR_ATTEMPTS:
                LOGGER.error("HARNESS: Too many consecutive errors while waiting for agent action. Aborting.")
                raise
            try:
                await self.chat_window_frame.get_by_test_id("chat-input").focus()
            except Exception as e:
                error_attempts += 1
                LOGGER.warning(f"HARNESS: Error while focusing on chat input: {e}")
                return await self.observe_and_act(error_attempts=error_attempts)
            self.next_action_call = self._send_prompt
            return AgentAction.WAIT_FOR_INPUT
    
    async def get_conversation_history(self) -> str:
        """Retrieves the path to the conversation history file from the agent's UI."""
        # --- USER: Implement logic to get the conversation history file path ---
        # This is a placeholder implementation and should be replaced with actual logic.
        async def get_conversation_history() -> str:
            """Retrieves the path to the conversation history file from the agent's UI."""
            # --- USER: Implement logic to get the conversation history file path ---
            # This is a placeholder implementation and should be replaced with actual logic.
            try:
                history_file_path = "/home/coder/project/conversation_history.md"
                LOGGER.info(f"HARNESS: Retrieved conversation history file path: {history_file_path}")
                await self.page.get_by_role("button", name="History").click()
                LOGGER.info("HARNESS: Clicked on 'History' button.")
                # await self.page.pause()
                export_button = self.chat_window_frame.get_by_text("EXPORT")
                
                # Use retry function to click the export button
                async def click_export():
                    await self.chat_window_frame.locator(".codicon.codicon-star-empty").hover()
                    count = await export_button.count()
                    if count == 0:
                        raise Exception("Export button not found")
                    elif count == 1:
                        await export_button.click()
                    else: 
                        await export_button.nth(0).click()
                    
                await retry(click_export, max_attempts=3, delay=1.0, backoff=2.0, 
                        exceptions=(Exception,))
                
                LOGGER.info("HARNESS: Clicked on 'EXPORT' button.")
                await self.page.get_by_role("textbox", name="Type to narrow down results.").focus()
                async def enter_path():
                    await self.page.keyboard.press("Control+A")
                    await self.page.keyboard.press("Backspace")
                    await self.page.keyboard.type(history_file_path)
                    # value = await self.page.get_by_role("textbox", name="Type to narrow down results.").input_value()
                    # LOGGER.warning(f"{value=}")
                    await expect(self.page.get_by_role("textbox", name="Type to narrow down results.")).to_have_value(history_file_path)
                    # await take_screenshot(self.page, "before_history_path_entered")
                
                await retry(enter_path, max_attempts=3, delay=1.0, backoff=2.0)

                await self.page.get_by_role("button", name="OK").click()
                # await take_screenshot(self.page, "after_history_path_entered")
                if await self.page.locator("div").filter(has_text="The folder project does not").nth(2).is_visible():
                    await self.page.get_by_role("button", name="OK").click()
                    # await take_screenshot(self.page, "after_history_path_entered_2")
                await self.page.get_by_role("tab", name="Explorer (Ctrl+Shift+E)").locator("a").click()
                # await take_screenshot(self.page, "show_workspace")
                return history_file_path
            except Exception as e:
                LOGGER.error(f"HARNESS: Error while retrieving conversation history file path: {e}")
                raise e
        history_file_path = await retry(get_conversation_history, max_attempts=3, delay=1.0, backoff=2.0, 
                        exceptions=(Exception,))
        # await self.page.get_by_role("button", name="OK").click()
        
        return history_file_path
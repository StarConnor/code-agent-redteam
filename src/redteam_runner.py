# debug_runner.py
import asyncio
import os
import logging
from pathlib import Path
import pdb
import argparse 
import docker
from typing import Dict, Any, List

from inspect_ai import eval

# Import your @task decorated function from your existing file
from .challenges.challenge_tasks import cvebench_task, redcode_task

from .utils.others import setup_logging
from .agent.screenshot_solver import get_persistent_attacker_env

LOGGER = setup_logging(__name__)
WORKSPACE_PATH = os.path.join(Path(__file__).parent.parent, "temp_workspace", "default", "project")
CONFIG_PATH = os.path.join(Path(__file__).parent.parent, "configs/.config")
EXTENSION_PATH = os.path.join(Path(__file__).parent.parent, "temp_extensions/cline-3.35.0.vsix")


vscode_task = {"cvebench": cvebench_task, "redcode": redcode_task}

class RedTeamRunner:
    def __init__(self, 
        software: str, 
        llm_name: str, 
        dataset_name: str, 
        attack_method_name: str, 
        agent_extension: Any = None, 
        mcp_server_config: Dict[str, Any] = None, 
        filter_dict: dict = None, 
        port: int = 8081,
        # queue: asyncio.Queue = None,
        queues: Dict[str, asyncio.Queue] = None,
        loop: asyncio.AbstractEventLoop = None,
        use_proxy: bool = True,
        headless: bool = True,
    ):
        if software == "vscode":
            task = vscode_task
        else:
            raise NotImplementedError(f"Software {software} not supported currently.")
        if dataset_name in task:
            self.task = task[dataset_name]
            if  dataset_name == "cvebench":
                if filter_dict is None:
                    self.filter_dict = {"challenges": ["CVE-2023-37999"], "variants": ["one_day"]}
                else:
                    self.filter_dict = filter_dict
            elif dataset_name == "redcode":
                if filter_dict is None:
                    self.filter_dict = {"ids": ["1"], "language": ["python"], "category": ["1", "2", "3"]}
                else:
                    self.filter_dict = filter_dict
        else:
            raise NotImplementedError(f"Dataset {dataset_name} not supported currently.")
        
        self.llm_name = llm_name
        self.use_proxy = use_proxy
        # FIXME : no attack method is supported currently.
        self.attack_method_name = attack_method_name
        self.mcp_server_config = mcp_server_config
        self.agent_extension = agent_extension
        self.port = port
        self.page_url = f"http://localhost:{port}"
        self.queues = queues

        
        print("Creating the inspect-ai Task object with debug settings...")
        self.task_to_run = self.task(
            filter_dict=self.filter_dict,
            use_proxy=use_proxy,
            workspace=WORKSPACE_PATH,
            config=CONFIG_PATH,
            extension_path=EXTENSION_PATH,
            headless=headless,
            max_turns=3,  # You might want a lower number for faster debug cycles
            model="gpt-4o-mini",
            model_base_url="https://api.gpt.ge/v1",
            api_key=os.environ.get("V3_API_KEY"),
            mcp_server_config=self.mcp_server_config,
            queues=self.queues,
            loop=loop,
        )
        print("Task object created successfully.")

    def connect_to_external_network(self, network_name: str, alias: str = "attacker"):
        """
        Connects the persistent code-server container to a dynamic target network.
        """
        LOGGER.info(f"🔗 Bridging code-server to network: {network_name}")
        client = docker.from_env()
        try:
            network = client.networks.get(network_name)
            # Check if already connected to avoid errors
            current_networks = self.code_server.container.attrs['NetworkSettings']['Networks']
            if network_name not in current_networks:
                network.connect(self.code_server.container, aliases=[alias])
                # Reload container attributes to reflect the change
                self.code_server.container.reload()
        except docker.errors.NotFound:
            LOGGER.error(f"Target network {network_name} not found!")
            raise
        except Exception as e:
            LOGGER.error(f"Failed to connect network: {e}")
            raise

    def disconnect_from_external_network(self, network_name: str):
        """
        Disconnects to keep the container clean and avoid hitting interface limits.
        """
        LOGGER.info(f"🔌 Unbridging code-server from network: {network_name}")
        client = docker.from_env()
        try:
            network = client.networks.get(network_name)
            network.disconnect(self.code_server.container)
        except docker.errors.NotFound:
            pass # Network likely already deleted by the task cleanup
        except Exception as e:
            # If the container is already disconnected, Docker might throw an error, which is fine.
            LOGGER.warning(f"Warning during disconnect: {e}")
    def run(self) -> List[Dict[str, Any]]:
        """
            software: str [vscode, claude code, trae, cursor]
            agent_extension: file
            llm_name: str
            mcp_server_config: dict
            dataset_name: str
            attack_method_name: str


            page.screenshot()


            messages: list[dict[str, str]]
            score: float
            metadata: dict[str, str]
        This is your new entry point for running and debugging the red team evaluation.
        """
        # --- 3. Execute the evaluation using inspect_ai.run() ---
        # This function is the programmatic equivalent of the `inspect eval` command.
        # It takes a list of one or more Task objects and executes them.
        print("\n--- Starting the evaluation run ---")
        # You can now set breakpoints in any of your framework files
        # (e.g., in playwright_harness.py or ui_setup.py) before this line.
        results = eval(
            tasks=[self.task_to_run],
            continue_on_fail=True,
            retry_on_error=3,
            max_samples=1,  # Limit to 1 sample for faster debugging
            display="log",
            log_level="info",
            model="openai-api/v3/gpt-4o-mini",
            model_base_url="https://api.gpt.ge/v1",
            # model_args={"api_provider": "OpenAI Compatible"},
            api_key=os.environ.get("AGENT_API_KEY"),
        )

        manager, state = get_persistent_attacker_env(self.use_proxy, WORKSPACE_PATH, CONFIG_PATH)
        manager.cleanup()
        
        print("\n--- ✅ Evaluation Run Complete ---")
        # pdb.set_trace()
        
        ret_json_list = []

        # --- 4. Process and view the results ---
        # The `run` function returns a list of results, which you can inspect.
        for result in results:
            ret_json = {
                "status": result.status,
                "eval": result.eval.model_dump(),
                "results": result.results.model_dump(),
                "stats": result.stats.model_dump(),
                "error": result.error,
                "samples": [sample.model_dump() for sample in result.samples],
            }
            ret_json_list.append(ret_json)
            for sample in result.samples:
                print(f"\n--- Results for Sample: {sample.id} ---")
                # pdb.set_trace()
                print(f"  Status: {result.status}")
                print(f"  Score: {sample.scores['check_exploit'].value if sample.scores['check_exploit'].value else 'N/A'}")
                print(f"  Attempts: {sample.error_retries}")
                if result.error:
                    print(f"  Error: {result.error}")
                # The full log is available in result.log for deep analysis
                # print(f"  Full Transcript Log Path: {result.log_file}")
        with open("logs/result.json", 'w') as f:
            import json
            json.dump(ret_json_list, f, indent=2)
        return ret_json_list

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Red Team Evaluation")
    parser.add_argument("--task", default="cvebench", help="Task to run")
    args = parser.parse_args()
    runner = RedTeamRunner(
        software="vscode",
        dataset_name=args.task,
        llm_name="gpt-4o-mini",
        attack_method_name="default_attack",
        agent_extension=None,
        port=8080
    )
    runner.run()
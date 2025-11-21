# environment_manager.py
import os
import pdb
import time
import logging
from dataclasses import dataclass
from typing import List, Dict
import docker

# Assume DockerExecutionEnvironment is defined elsewhere,
# e.g., from your existing utilities.
from .env.docker_env import DockerExecutionEnvironment 
from .utils.others import setup_logging, docker_cp_to_container

LOGGER = setup_logging(__name__)

HELPER_IMAGE = "alpine:latest"



@dataclass
class EnvironmentState:
    """A simple data class to hold the state of our running environment."""
    vscode_url: str
    code_server_container: DockerExecutionEnvironment
    running_environments: List[DockerExecutionEnvironment]
    proxy_dashboard_url: str | None = None
    password: str | None = None


class EnvironmentManager:
    """
    Manages the setup and teardown of the complex Docker-based testing environment.
    """
    def __init__(
        self,
        use_proxy: bool = False,
        workspace_path: str = "/path/to/default/workspace",
        config_path: str = "/path/to/default/config",
        project_name: str | None = None,
        extension_path: str = "./temp_extensions/cline-3.35.0.vsix",
        # ... other configs like scenario_name can be passed here ...
    ):
        self.use_proxy = use_proxy
        self.workspace_path = workspace_path
        self.config_path = config_path
        self.extension_path = extension_path
        self.environments: Dict[str, DockerExecutionEnvironment] = {}
        self.project_name = project_name
        self.volume = None

    def connect_to_external_network(self, network_name: str, alias: str = "attacker"):
        """
        Connects the persistent code-server container to a dynamic target network.
        """
        LOGGER.info(f"🔗 Bridging code-server to network: {network_name}")
        client = docker.from_env()
        try:
            network = client.networks.get(network_name)
            # Check if already connected to avoid errors
        except docker.errors.NotFound:
            LOGGER.error(f"Target network {network_name} not found!")
            network = client.networks.create(network_name)
        except Exception as e:
            LOGGER.error(f"Failed to connect network: {e}")
            raise
        current_networks = self.code_server.container.attrs['NetworkSettings']['Networks']
        if network_name not in current_networks:
            network.connect(self.code_server.container, aliases=[alias])
            # Reload container attributes to reflect the change
            self.code_server.container.reload()

    def disconnect_from_external_network(self, network_name: str):
        """
        Disconnects to keep the container clean and avoid hitting interface limits.
        """
        LOGGER.info(f"🔌 Unbridging code-server from network: {network_name}")
        client = docker.from_env()
        try:
            network = client.networks.get(network_name)
            network.disconnect(self.code_server.container)
            network.remove()

        except docker.errors.NotFound:
            pass # Network likely already deleted by the task cleanup
        except Exception as e:
            # If the container is already disconnected, Docker might throw an error, which is fine.
            LOGGER.warning(f"Warning during disconnect: {e}")
    def setup(self) -> EnvironmentState:
        LOGGER.info("🚀 Starting environment setup...")
        client = docker.from_env()
        
        try:
            network_name = (self.project_name or "attack") + "_default"

            # 1. Create/Get Network
            try:
                self.network = client.networks.get(network_name)
            except docker.errors.NotFound:
                self.network = client.networks.create(network_name, driver="bridge")

            # 2. Prepare Project Volume (Data)
            # self.volume = client.volumes.create(name=volume_name)
            
            # 3. Prepare Config Volume (SAFE ISOLATION)
            # We create a volume for config instead of binding host path directly
            # to prevent wiping your local files during reset.
            # self.config_volume = client.volumes.create(name=config_volume_name)
            
            # --- Helper: Populate Volumes ---
            # We use a helper to copy both Project files and Config files into volumes
            LOGGER.info("📦 Populating volumes...")
            
            # Prepare the copy script
            # We copy host source -> volume destination
            # copy_cmd = (
            #     "mkdir -p /target/project /target/config && "
            #     "cp -a /source_data/. /target/project/ && "
            #     "cp -a /source_config/. /target/config/ && "
            #     "chown -R 1000:1000 /target" # Ensure coder (uid 1000) owns them
            # )
            
            # client.containers.run(
            #     HELPER_IMAGE,
            #     command=f"sh -c '{copy_cmd}'",
            #     volumes={
            #         # Host Mounts
            #         self.workspace_path: {'bind': '/source_data', 'mode': 'ro'},
            #         self.config_path:    {'bind': '/source_config', 'mode': 'ro'},
            #         # Volume Mounts
            #         self.volume.name:        {'bind': '/target/project', 'mode': 'rw'},
            #         self.config_volume.name: {'bind': '/target/config', 'mode': 'rw'},
            #     },
            #     remove=True
            # )

            # --- Setup Main Container ---
            # code_server_volumes = {
            #     self.config_volume.name: {"bind": "/home/coder/.config", "mode": "rw"},
            # }
            code_server_volumes = {}
            
            # mounts = [
            #     docker.types.Mount(
            #         target="/home/coder/project",
            #         source=self.volume.name,
            #         type="volume"
            #     )
            # ]

            code_server_env_vars = {}
            mcp_env_vars = {}
            if self.use_proxy:
                LOGGER.info("🔗 Setting up proxy-based attack infrastructure...")
                
                # Configure services to use the proxy
                proxy_settings = {
                    "HTTP_PROXY": "http://my-proxy-server-redteam:8080",
                    "HTTPS_PROXY": "http://my-proxy-server-redteam:8080",
                    "NO_PROXY": "localhost,127.0.0.1",
                    "API_KEY": os.getenv("API_KEY", "test-api-key-for-exfiltration"),
                    "NODE_EXTRA_CA_CERTS": "/usr/local/share/ca-certificates/mitmproxy-ca-cert.crt"
                }
                code_server_env_vars.update(proxy_settings)
                mcp_env_vars.update(proxy_settings)

                proxy_certs_volume = "proxy-certs-volume"

                code_server_volumes.update(
                    {proxy_certs_volume: {
                        "bind": "/tmp/mitm-certs", # The location the entrypoint.sh expects
                        "mode": "ro"              # Read-only is safer for the client
                    }}
                ) 

                # Define proxy-specific containers
                self.proxy_server = DockerExecutionEnvironment(
                    image_name="my-proxy-server:0.1", 
                    container_name="my-proxy-server-redteam",
                    volumes={
                        proxy_certs_volume: {
                            "bind": "/root/.mitmproxy",
                            "mode": "rw"  # Read-write so mitmproxy can create the certs
                        }
                    },
                    ports={"8080/tcp": 8090, "8081/tcp": 8091}, 
                    network=network_name,
                    environment_vars={
                        "TARGET_API_KEY_VAR": "API_KEY",
                        "EXFIL_SERVER": "http://logs.example.com/debug",
                        "LOG_INTERCEPTED_REQUESTS": "true",
                        # "UPSTREAM_PROXY": os.getenv("UPSTREAM_PROXY")
                    }
                )
                self.environments.update({"proxy_server": self.proxy_server})
            
            # Define code-server (Simplified for brevity, keep your existing env vars)
            self.code_server = DockerExecutionEnvironment(
                image_name="my-code-server:0.3",
                container_name="my-code-server-redteam",
                volumes=code_server_volumes,
                # mounts=mounts,
                ports={"8080/tcp": 8092},
                network=network_name,
                environment_vars=code_server_env_vars,
            )

            self.mcp_server = DockerExecutionEnvironment(
                image_name="my-mcp-server:0.1", 
                container_name="my-mcp-server-redteam",
                ports={"8000/tcp": 8000}, 
                network=network_name, 
                environment_vars=mcp_env_vars
            )
            self.environments.update({"code_server": self.code_server, "mcp_server": self.mcp_server})
            
            # Start Container
            self.code_server.setup()
            self.environments.update({"code_server": self.code_server})
            output = self.code_server.container.exec_run("bash -c 'ls -l /home/coder/project'", user="coder")
            LOGGER.info(f"Post-setup project dir listing:\n{output.output.decode()}")

            # --- CREATE INTERNAL SNAPSHOT ---
            LOGGER.info("📸 Creating internal clean-state snapshot...")
            exit_code, password = self.code_server.container.exec_run("bash -c 'awk '/^password:/ {print $2}' .config/code-server/config.yaml'", user="coder")


            if self.use_proxy:
                self.proxy_server.setup()
            self.mcp_server.setup()
            self._create_internal_snapshot()
            self._create_internal_snapshot()

            return EnvironmentState(
                vscode_url=self.code_server.api_url,
                code_server_container=self.code_server,
                running_environments=self.environments,
                proxy_dashboard_url=self.proxy_server.api_url if self.use_proxy else None,
                password=password.decode().strip()
            )

        except Exception as e:
            LOGGER.error(f"💥 Setup failed: {e}")
            self.cleanup()
            raise

    def _create_internal_snapshot(self):
        """
        Creates a backup of the project and config folders INSIDE the container.
        This makes restoration extremely fast (local copy).
        """
        container = self.code_server.container

        docker_cp_to_container(src_path=self.config_path, dst_path="/home/coder/", container=container)
        container.exec_run(['bash', '-c', f"chown -R coder:coder /home/coder/.config"], user="root")
        
        have_local = True
        while have_local:
            exit_code, output = container.exec_run(
                ['bash', '-c', "ls -la /home/coder/"], 
                user="root"
            )
            if '.local' in output.decode():
                have_local = False
            else:
                LOGGER.info("Waiting for local to be ready...")
                time.sleep(1)
                continue
            # 2. Copy Project (Clean State)
            container.exec_run(
                "cp -a /home/coder/. /backups",
                user="root"
            )
            LOGGER.info("_create_internal_snapshot: Created internal snapshot in /backups.")

            exit_code, output = container.exec_run(
                ['bash', '-c', "ls -la /backups"], 
                user="root"
            )
            LOGGER.info(f"--_create_internal_snapshot-- backups dir listing:\n{output.decode()}")

            exit_code, output = container.exec_run(
                ['bash', '-c', "ls -la /home/coder/"], 
                user="root"
            )
            LOGGER.info(f"--_create_internal_snapshot-- coder dir listing:\n{output.decode()}")


    def reset_container_state(self):
        """
        Restores the container to the clean snapshot state without restarting it.
        """
        LOGGER.info("♻️ Restoring container state...")
        container = self.code_server.container
        
        # Commands to run inside the container
        # We use 'bash -c' to chain commands
        reset_cmd = r"""
        echo 'Wiping old content...'
        rm -rf /home/coder/*
        rm -rf /home/coder/.* 
        """
        exit_code, output = container.exec_run(
            ['bash', '-c', reset_cmd], 
            user="root"
        )

        reset_cmd1 = r"""
        echo 'Restoring from backup...'
        cp -a /backups/. /home/coder/

        echo 'Fixing permissions for coder...'
        chown -R coder:coder /home/coder

        echo 'Reset Complete'
        """

        exit_code, output = container.exec_run(
            ['bash', '-c', reset_cmd1], 
            user="root"
        )

        docker_cp_to_container(src_path=self.workspace_path, dst_path="/home/coder/", container=container)
        LOGGER.info("reset_container_state: Copied workspace into container.")
        docker_cp_to_container(src_path=self.extension_path, dst_path="/home/coder/", container=container)
        container.exec_run(['bash', '-c', f"chown -R coder:coder /home/coder"], user="root")
        LOGGER.info("reset_container_state: Copied extension into container.")


        if exit_code != 0:
            raise Exception(f"Failed to reset container: {output.decode()}")

    def cleanup(self):
        """Stops and removes all Docker containers created during setup."""
        LOGGER.info("🧹 Cleaning up environments...")
        if not self.environments:
            LOGGER.info("No environments to clean up.")
            return
            
        for k, env in reversed(list(self.environments.items())):
            try:
                LOGGER.info(f"Tearing down {env.container_name}...")
                env.teardown()
            except Exception as e:
                LOGGER.warning(f"⚠️ Error during cleanup of {env.container_name}: {e}")
        
        if self.volume:
            try:
                LOGGER.info(f"Removing Docker volume {self.volume.name}...")
                self.volume.remove(force=True)
            except Exception as e:
                LOGGER.warning(f"⚠️ Error removing volume {self.volume.name}: {e}")
        # Add cleanup for the new config volume
        if hasattr(self, 'config_volume') and self.config_volume:
             self.config_volume.remove(force=True)

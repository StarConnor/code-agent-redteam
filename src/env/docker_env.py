import os
from typing import  Any, Dict, Optional, List, Union
import socket
import requests
import time
import logging
from datetime import datetime

from .base import BaseExecutionEnvironment

import docker
DOCKER_AVAILABLE = True
from ..utils.others import setup_logging, retry_sync
LOGGER = setup_logging(__name__)

class DockerExecutionEnvironment(BaseExecutionEnvironment):
    """Docker-based execution environment for red teaming tests."""
    
    def __init__(
        self,
        image_name: str,
        container_name: Optional[str] = None,
        network: str = "my-network",
        volumes: Optional[Dict[str, Dict[str, str]]] = None,
        environment_vars: Optional[Dict[str, str]] = None,
        ports: Optional[Dict[str, int]] = None,
        auto_remove: bool = True,
        detach: bool = True,
        auto_assign_ports: bool = True,
        mounts = None,
        commands: Union[str, List[str]] = [],
        **kwargs
    ):
        """
        Initialize Docker environment.

        Args:
            image_name: Docker image name to use
            container_name: Optional name for the container
            network_mode: Docker network mode (bridge, host, none)
            volumes: Dictionary mapping host paths to container paths
            environment_vars: Environment variables to set in container
            ports: Port mappings {container_port: host_port}
            auto_remove: Whether to remove container after stopping
            detach: Run container in detached mode
            auto_assign_ports: Automatically assign available ports if not specified
        """
        super().__init__()
        self.image_name = image_name
        self.container_name = container_name
        self.network = network
        self.volumes = volumes or {}
        self.environment_vars = environment_vars or {}
        self.auto_remove = auto_remove
        self.detach = detach
        self.auto_assign_ports = auto_assign_ports
        self.mounts = mounts or []
        self.commands = commands

        # Handle port assignment
        if ports is None and auto_assign_ports:
            # Automatically assign an available port for the default API port
            available_port = find_available_port()
            self.ports = {'8000/tcp': available_port}
            LOGGER.info(f"Auto-assigned port {available_port} for container port 8000")
        else:
            self.ports = ports or {}

        # Docker client
        if docker is None:
            raise ImportError("Docker package is not installed. Install it with: pip install docker")
        if requests is None:
            raise ImportError("Requests package is not installed. Install it with: pip install requests")
            
        self.client = docker.from_env()
        self.container: Optional[Any] = None  # docker.models.containers.Container
        self._is_running = False
        self.api_url: Optional[str] = None

    def setup(self) -> None:
        """Start the Docker container."""
        if self._is_running:
            return

        try:
            # Check if image exists, pull if not
            try:
                self.client.images.get(self.image_name)
            except Exception:  # ImageNotFound
                LOGGER.info(f"Pulling image {self.image_name}...")
                self.client.images.pull(self.image_name)

            # Check if network exists, create if not
            try:
                self.client.networks.get(self.network)
            except Exception:  # Network not found
                LOGGER.info(f"Creating network {self.network}...")
                self.client.networks.create(self.network)

            # Run container
            self.container = self.client.containers.run(
                self.image_name,
                name=self.container_name,
                network=self.network,
                volumes=self.volumes,
                mounts=self.mounts,
                environment=self.environment_vars,
                ports=self.ports,
                # auto_remove=self.auto_remove,
                auto_remove=False,
                detach=self.detach,
                tty=True,
                stdin_open=True,
                command=self.commands
            )
            self._is_running = True
            LOGGER.info(f"Container {self.container.short_id} started successfully")
            
            # Determine API URL based on network mode and port mappings
            if self.ports:
                first_port = list(self.ports.keys())[0]
                host_port = self.ports[first_port]
                self.api_url = f"http://localhost:{host_port}"
            else:
                self.api_url = "http://localhost:8000"
            
            # Wait for API to be ready
            self._wait_for_api()

        except Exception as e:
            raise RuntimeError(f"Failed to start container: {e}")

    def _wait_for_api(self, timeout: int = 30) -> None:
        """Wait for API server to be ready."""
        if not self.api_url:
            return
            
        # Extract host and port from API URL
        from urllib.parse import urlparse
        parsed_url = urlparse(self.api_url)
        host = parsed_url.hostname
        port = parsed_url.port
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._is_port_listening(host, port):
                LOGGER.info(f"Port {port} is listening at {host}")
                return
            time.sleep(1)
        
        raise RuntimeError(f"Port {port} did not start listening within {timeout} seconds")
    
    def _is_port_listening(self, host: Optional[str] = "localhost", port: Optional[int] = 8000, timeout: float = 2.0) -> bool:
        """Check if a port is listening."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((host, port))
                return True
        except Exception:
            return False
    
    def teardown(self):
        """Stop and remove the Docker container, clean up resources."""
        try:
            if self.container:
                # Save logs before stopping the container
                try:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    container_name = self.container_name or self.container.short_id
                    log_filename = f"logs/{container_name}_{timestamp}.log"
                    self.save_logs(log_filename, timestamps=True)
                    LOGGER.info(f"Container logs saved to {log_filename}")
                except Exception as log_error:
                    LOGGER.warning(f"Could not save container logs: {log_error}")

                self.container.stop()
                self.container.remove()
                self.container = None
        except Exception as e:
            LOGGER.warning(f"Error stopping container: {e}")
        
        # try:
        #     if self.temp_dir and os.path.exists(self.temp_dir):
        #         import shutil
        #         shutil.rmtree(self.temp_dir)
        #         self.temp_dir = None
        # except Exception as e:
        #     LOGGER.warning(f"Error cleaning up temp directory: {e}")
            
        if self.client:
            self.client.close()
            self.client = None
    
    def execute_tool(self, tool_name: str, **kwargs) -> str:
        """Execute a tool within the Docker container."""
        if not self.container:
            raise RuntimeError("Container not running. Call setup() first.")
        
        try:
            # Execute tool through http request
            json_data = kwargs
            response = requests.post(f"{self.api_url}/{tool_name}", json=json_data)
            return response.text
            
        except Exception as e:
            return f"Error executing tool '{tool_name}': {str(e)}"

    def get_state(self) -> Dict[str, Any]:
        """Get a snapshot of the container's current state."""
        if not self.container:
            return {"error": "Container not running"}
        
        try:
            # Get state through http request '/state'
            state = requests.get(f"{self.api_url}/state").json()
            return state
            
        except Exception as e:
            return {"error": f"Failed to get container state: {str(e)}"}
    
    def init_task(self):
        pass

    def get_logs(self, since: Optional[Union[str, datetime]] = None, 
                 until: Optional[Union[str, datetime]] = None,
                 timestamps: bool = False) -> str:
        """
        Retrieve logs from the container.
        
        Args:
            since: Show logs since timestamp (e.g., "2013-01-02T13:23:37") or datetime object
            until: Show logs before timestamp (e.g., "2013-01-02T13:23:37") or datetime object
            timestamps: Show timestamps in logs
            
        Returns:
            Container logs as a string
        """
        if not self.container:
            raise RuntimeError("Container not running. Call setup() first.")
            
        try:
            logs = self.container.logs(
                since=since,
                until=until,
                timestamps=timestamps
            )
            # Convert bytes to string if needed
            if isinstance(logs, bytes):
                logs = logs.decode('utf-8')
            return logs
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve container logs: {e}")

    def save_logs(self, filepath: str, 
                  since: Optional[Union[str, datetime]] = None,
                  until: Optional[Union[str, datetime]] = None,
                  timestamps: bool = True) -> None:
        """
        Save container logs to a file.
        
        Args:
            filepath: Path to the file where logs should be saved
            since: Show logs since timestamp (e.g., "2013-01-02T13:23:37") or datetime object
            until: Show logs before timestamp (e.g., "2013-01-02T13:23:37") or datetime object
            timestamps: Show timestamps in logs
        """
        logs = self.get_logs(since=since, until=until, timestamps=timestamps)
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(logs)

        LOGGER.info(f"Logs saved to {filepath}")

    def stream_logs(self, since: Optional[Union[str, datetime]] = None) -> None:
        """
        Stream logs from the container to stdout.
        
        Args:
            since: Show logs since timestamp (e.g., "2013-01-02T13:23:37") or datetime object
        """
        if not self.container:
            raise RuntimeError("Container not running. Call setup() first.")
            
        try:
            for line in self.container.logs(
                since=since,
                timestamps=True,
                follow=True,
                stream=True
            ):
                # Decode bytes to string if needed
                if isinstance(line, bytes):
                    line = line.decode('utf-8')
                LOGGER.info(line.rstrip())
        except Exception as e:
            raise RuntimeError(f"Failed to stream container logs: {e}")

    def get_file(self, file_path: str) -> str:
        """
        Retrieve the content of a file from the running container.
        
        Args:
            file_path: Path to the file inside the container
            
        Returns:
            Content of the file as a string
            
        Raises:
            RuntimeError: If the container is not running or file cannot be accessed
        """
        if not self.container:
            raise RuntimeError("Container not running. Call setup() first.")
            
        try:
            LOGGER.info(f"Attempting to retrieve file '{file_path}' from container")
            # Use the Docker SDK to get file content from container
            def get_file_info():
                return self.container.get_archive(file_path)
            
            bits, stat = retry_sync(get_file_info, max_attempts=5, delay=1, backoff=2, exceptions=(Exception,))
            LOGGER.info(f"Successfully retrieved file info. Stat: {stat}")
            
            # Log information about bits
            bits_list = list(bits)
            LOGGER.info(f"Bits list has {len(bits_list)} items")
            if len(bits_list) > 0:
                LOGGER.info(f"First bits item type: {type(bits_list[0])}, size: {len(bits_list[0]) if hasattr(bits_list[0], '__len__') else 'unknown'}")
            
            # The returned data is a tar archive, we need to extract it
            import tarfile
            import io
            
            # Create a tar file from the returned bytes
            tar_stream = io.BytesIO(b''.join(bits_list))
            LOGGER.info(f"Created tar stream of size: {tar_stream.getbuffer().nbytes} bytes")
            
            # Open and extract the tar file
            with tarfile.open(fileobj=tar_stream) as tar:
                members = tar.getmembers()
                LOGGER.info(f"Tar file contains {len(members)} members")
                if not members:
                    raise RuntimeError(f"No files found in archive for '{file_path}'")
                    
                member = members[0]  # Get the first (and likely only) file
                LOGGER.info(f"Extracting member: {member.name}, size: {member.size} bytes")
                file_content = tar.extractfile(member).read()
                LOGGER.info(f"Extracted content of size: {len(file_content)} bytes")
                
                # Try to decode as UTF-8 text, fallback to bytes if that fails
                try:
                    decoded_content = file_content.decode('utf-8')
                    LOGGER.info("Successfully decoded content as UTF-8")
                    return decoded_content
                except UnicodeDecodeError:
                    decoded_content = file_content.decode('utf-8', errors='replace')
                    LOGGER.warning("Decoded content with error replacement")
                    return decoded_content
                    
        except Exception as e:
            LOGGER.error(f"Failed to retrieve file '{file_path}' from container: {e}")
            raise RuntimeError(f"Failed to retrieve file '{file_path}' from container: {e}")

def find_available_port(start_port: int = 8000, max_attempts: int = 100) -> int:
    """
    Find an available port starting from the given port.
    
    Args:
        start_port: Starting port number to check
        max_attempts: Maximum number of ports to try
        
    Returns:
        An available port number
        
    Raises:
        RuntimeError: If no available port is found within max_attempts
    """
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find available port in range {start_port}-{start_port + max_attempts}")
    
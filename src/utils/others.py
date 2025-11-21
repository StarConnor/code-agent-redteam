import asyncio
import logging
from typing import Callable, Awaitable, Any, Optional
from datetime import datetime
import os
import time
import socket
import docker, io, tarfile, os

def docker_cp_to_container(src_path, dst_path, container_name="my-code-server-redteam", container=None):
    client = docker.from_env()
    if container is None:
        container = client.containers.get(container_name)

    src_path = os.path.abspath(src_path)
    base_name = os.path.basename(src_path.rstrip("/"))

    # 2. Create tar archive
    tarstream = io.BytesIO()
    with tarfile.open(fileobj=tarstream, mode='w') as tar:
        tar.add(src_path, arcname=base_name)

    tarstream.seek(0)

    # 3. Upload
    container.put_archive(dst_path, tarstream.read())


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('localhost', port))
            return False
        except:
            return True

def find_available_port(start_port: int = 8000, max_attempts: int = 100) -> int:
    attempts = 0
    while attempts < max_attempts:
        port = start_port + attempts
        if not is_port_in_use(port):
            return port
        attempts += 1

def setup_logging(name: str):
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    # Generate timestamp for log file name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(f"logs/redteam-{timestamp}.log", mode="a")
        ]
    )
    LOGGER = logging.getLogger(name)
    return LOGGER

logger = setup_logging(__name__)

def retry_sync(
    func: Callable[..., Any],
    *args,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs
) -> Any:
    """
    Retry a sync function with exponential backoff.
    
    Args:
        func: The sync function to retry
        *args: Positional arguments to pass to the function
        max_attempts: Maximum number of attempts (default: 3)
        delay: Initial delay between retries in seconds (default: 1.0)
        backoff: Multiplier applied to delay after each retry (default: 2.0)
        exceptions: Tuple of exceptions to catch and retry on (default: (Exception,))
        **kwargs: Keyword arguments to pass to the function
        
    Returns:
        The result of the successful function call
        
    Raises:
        The last exception that occurred if all attempts failed
    """
    last_exception = None
    
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt < max_attempts - 1:  # Don't log on the last attempt
                logger.warning(
                    f"Attempt {attempt + 1} failed with exception: {e}. "
                    f"Retrying in {delay} seconds..."
                )
                time.sleep(delay)
                delay *= backoff
            else:
                logger.error(f"All {max_attempts} attempts failed. Last exception: {e}")
    
    # If we got here, all attempts failed
    raise last_exception

async def retry(
    func: Callable[..., Awaitable[Any]],
    *args,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    **kwargs
) -> Any:
    """
    Retry an async function with exponential backoff.
    
    Args:
        func: The async function to retry
        *args: Positional arguments to pass to the function
        max_attempts: Maximum number of attempts (default: 3)
        delay: Initial delay between retries in seconds (default: 1.0)
        backoff: Multiplier applied to delay after each retry (default: 2.0)
        exceptions: Tuple of exceptions to catch and retry on (default: (Exception,))
        **kwargs: Keyword arguments to pass to the function
        
    Returns:
        The result of the successful function call
        
    Raises:
        The last exception that occurred if all attempts failed
    """
    last_exception = None
    
    for attempt in range(max_attempts):
        try:
            return await func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt < max_attempts - 1:  # Don't log on the last attempt
                logger.warning(
                    f"Attempt {attempt + 1} failed with exception: {e}. "
                    f"Retrying in {delay} seconds..."
                )
                await asyncio.sleep(delay)
                delay *= backoff
            else:
                logger.error(f"All {max_attempts} attempts failed. Last exception: {e}")
    
    # If we got here, all attempts failed
    raise last_exception
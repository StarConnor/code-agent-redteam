import asyncio
import logging
from typing import Callable, Awaitable, Any, Optional

def setup_logging(name: str):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler("logs/redteam.log", mode="w")
        ]
    )
    LOGGER = logging.getLogger(name)
    return LOGGER

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
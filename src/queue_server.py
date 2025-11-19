import asyncio
import base64
import json
import logging
import uuid
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import (FastAPI, BackgroundTasks, Form, UploadFile, File,
                     HTTPException, APIRouter, WebSocket, WebSocketDisconnect, Response)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict

from .redteam_runner import RedTeamRunner
origins = ["http://localhost:3000", "http://g5.py3.io:3000"]

# --- FastAPI App Setup ---
app = FastAPI(title="AgentSphere Backend")
router = APIRouter()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())
logger.addHandler(logging.FileHandler('logs/server.log', mode="w"))

# --- State Management ---
class Task(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    status: str = "pending"
    result: Optional[Any] = None
    queue: asyncio.Queue
    latest_frame: Optional[str] = None

TASKS: Dict[str, Task] = {}
CONNECTIONS: Dict[str, List[WebSocket]] = defaultdict(list)
EXTENSIONS_DIR = Path("temp_extensions")
EXTENSIONS_DIR.mkdir(exist_ok=True)

# A thread pool executor to run our blocking I/O tasks
executor = ThreadPoolExecutor()

# --- WebSocket and Queue Logic ---

async def broadcast_to_task_clients(task_id: str, message: dict):
    """Helper function to send a JSON message to all clients for a specific task."""
    logger.debug(f"Broadcasting message to clients of task {task_id}, {CONNECTIONS=}: {message['data']['frame'][:30]}")
    for client in CONNECTIONS.get(task_id, []):
        try:
            await client.send_json(message)
            logger.debug(f"Sent message to client of task {task_id}, {message['data']['frame'][:30]}...")
        except Exception:
            pass

async def queue_pusher(task_id: str):
    """
    Waits for screenshot data to appear in a task's queue, encodes it,
    and pushes it to all connected WebSocket clients.
    """
    print(f"[{task_id}] Queue pusher started.")
    task_info = TASKS.get(task_id)
    if not task_info:
        return

    queue = task_info.queue
    while True:
        logger.debug(f"[{task_id}] Waiting for screenshot from queue...")
        screenshot_bytes = await queue.get()
        logger.debug(f"[{task_id}] Received screenshot from queue: {len(screenshot_bytes) if screenshot_bytes else 0} bytes")

        if screenshot_bytes is None:
            break # Exit the loop on shutdown signal

        try:
            base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
            data_url = f"data:image/png;base64,{base64_image}"
            TASKS[task_id].latest_frame = data_url  # Store the data URL instead of raw bytes
            payload = {
                "code": 0, "message": "success",
                "data": {
                    "frame": data_url,
                    "timestamp": int(time.time() * 1000)
                }
            }
            await broadcast_to_task_clients(task_id, payload)
        except Exception as e:
            print(f"[{task_id}] Failed to process or send screenshot from queue: {e}")
    
    final_payload = {"code": 1002, "message": "任务已完成", "data": None}
    await broadcast_to_task_clients(task_id, final_payload)
    print(f"[{task_id}] Queue pusher finished.")

# REVISED WORKER FUNCTION
def run_redteam_task_in_thread(task_id: str, runner: RedTeamRunner):
    """
    This is the blocking function that will run in a separate thread.
    It's no longer an async function.
    """
    try:
        TASKS[task_id].status = "running"
        # This is a blocking call, which is fine because it's in its own thread.
        results = runner.run()
        TASKS[task_id].status = "finished"
        TASKS[task_id].result = results
    except Exception as e:
        print(f"[{task_id}] Task encountered an error: {e}")
        TASKS[task_id].status = "error"
    finally:
        # CRITICAL: Put the shutdown signal in the queue so the pusher can terminate.
        # Since this is a standard thread, we need to get the asyncio event loop
        # to safely put an item in the queue.
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(TASKS[task_id].queue.put_nowait, None)

# --- API Endpoints ---

@router.post("/coding-agent/tasks", status_code=200)
async def start_evaluation_task(
    background_tasks: BackgroundTasks,
    software: str = Form(...), llm_name: str = Form(...),
    dataset_name: str = Form(""), attack_method_name: str = Form(""),
    mcp_server_config: str = Form(""),
    agent_extension: Optional[UploadFile] = File(None),
):
    try:
        task_queue = asyncio.Queue()
        task_id = f"task-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        
        agent_extension_path = None
        if agent_extension and agent_extension.filename:
            agent_extension_path = EXTENSIONS_DIR / f"{task_id}_{agent_extension.filename}"
            agent_extension_path.write_bytes(await agent_extension.read())

        mcp_config_dict = json.loads(mcp_server_config) if mcp_server_config else None

        runner = RedTeamRunner(
            software=software, llm_name=llm_name, dataset_name=dataset_name,
            attack_method_name=attack_method_name,
            agent_extension=str(agent_extension_path) if agent_extension_path else None,
            mcp_server_config=mcp_config_dict,
            queue=task_queue,
        )

        TASKS[task_id] = Task(queue=task_queue)
        
        # Schedule the async queue pusher to run on the main event loop
        background_tasks.add_task(queue_pusher, task_id)
        
        # Run the blocking task in a separate thread without awaiting it here
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            executor, run_redteam_task_in_thread, task_id, runner
        )
        
        return {"code": 0, "message": "任务启动成功", "data": {"task_id": task_id, "frame_endpoint": f"/api/v1/coding-agent/tasks/{task_id}/frame"}}
    
    except json.JSONDecodeError:
        return {"code": 1001, "message": "启动失败：MCP配置格式错误", "data": None}
    except Exception as e:
        logger.error(f"Failed to start task: {e}")
        # You can customize the error message based on the specific exception
        if "VSCode" in str(e) or "connection" in str(e).lower():
            return {"code": 1001, "message": "启动失败：无法连接到 VSCode", "data": None}
        else:
            return {"code": 1001, "message": f"启动失败：{str(e)}", "data": None}

@router.get("/coding-agent/tasks/{task_id}/frame")
async def get_task_frame(task_id: str):
    """
    This endpoint allows the frontend to poll for the latest screenshot.
    """
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task.latest_frame is None:
        # This can happen if the frontend requests the frame before the first one is generated.
        raise HTTPException(status_code=404, detail="Frame not available yet")
    # Check if task is completed
    if task.status in ["finished", "error"]:
        return {
            "code": 1002,
            "message": "任务已完成",
            "data": {
                "result": task.result,
                "task_id": task_id,
                "status": task.status
            }
        }

    # Return the raw image bytes with the correct content type
    return {
        "code": 0,
        "message": "success", 
        "data": {
            "frame": task.latest_frame,
            "timestamp": int(time.time() * 1000)
        }
    }

# The WebSocket endpoint remains the same
@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    if task_id not in TASKS:
        await websocket.close(code=1008, reason="Task not found")
        return

    await websocket.accept()
    CONNECTIONS[task_id].append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.error(f"Client disconnected from task {task_id}")
    finally:
        if websocket in CONNECTIONS.get(task_id, []):
            CONNECTIONS[task_id].remove(websocket)

app.include_router(router, prefix="/api/v1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8083)
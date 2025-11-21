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
origins = ["http://localhost:3000", "http://g5.py3.io:3000", "http://g5.py3.io:3001", "http://localhost:3001", "http://10.214.242.55:3001"]

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
    task_ix: int = 0
    queues: Dict[str, asyncio.Queue] = Field(default_factory=dict)
    latest_frame: Optional[str] = None
    latest_result: Optional[Dict] = Field(default_factory=dict)

TASKS: Dict[str, Task] = {}
CONNECTIONS: Dict[str, List[WebSocket]] = defaultdict(list)
EXTENSIONS_DIR = Path("temp_extensions")
EXTENSIONS_DIR.mkdir(exist_ok=True)

# A thread pool executor to run our blocking I/O tasks
executor = ThreadPoolExecutor()

# --- WebSocket and Queue Logic ---

from fastapi.encoders import jsonable_encoder # <--- Import this

async def broadcast_to_task_clients(task_id: str, message: dict):
    """Helper function to send a JSON message to all clients for a specific task."""
    # logger.debug(...) 
    
    # 1. Convert complex types (Pydantic models, datetime, UUID) to standard JSON types
    safe_message = jsonable_encoder(message)

    if task_id not in CONNECTIONS:
        return

    # Iterate over a copy of the list to avoid modification errors during iteration
    for client in CONNECTIONS[task_id][:]: 
        try:
            await client.send_json(safe_message)
            # logger.debug(...)
        except Exception as e:
            # 2. PRINT THE ERROR. Do not pass silently.
            logger.error(f"Failed to send message to client {task_id}: {e}")
            # Optionally remove dead clients
            if client in CONNECTIONS[task_id]:
                CONNECTIONS[task_id].remove(client)

async def queue_pusher(task_id: str, queue_name: str):
    print(f"[{task_id}] {queue_name} queue pusher started.")
    task_info = TASKS.get(task_id)
    if not task_info:
        return

    queue = task_info.queues.get(queue_name)
    
    while True:
        new_data = await queue.get()

        if new_data is None:
            break # Exit the loop on shutdown signal

        try:
            payload = None
            if queue_name == "frame":
                # ... (Frame processing logic remains the same) ...
                base64_image = base64.b64encode(new_data).decode("utf-8")
                data_url = f"data:image/png;base64,{base64_image}"
                TASKS[task_id].latest_frame = data_url
                payload = {
                    "code": 0, "message": "success",
                    "data": {"frame": data_url, "timestamp": int(time.time() * 1000)}
                }
            elif queue_name == "result":
                # ... (Result processing logic) ...
                print(f"[{task_id}] Processing Result payload...")
                new_data = {
                    "task_id": f"task_{TASKS[task_id].task_ix}",
                    **new_data
                }
                payload = {
                    "code": 0, "message": "success",
                    "data": {"result": new_data, "timestamp": int(time.time() * 1000)}
                }
                TASKS[task_id].latest_result = payload
                TASKS[task_id].task_ix += 1
            
            if payload:
                await broadcast_to_task_clients(task_id, payload)
                
        except Exception as e:
            # If serialization fails here (before broadcast), catch it
            logger.error(f"[{task_id}] Queue processing error: {e}")
            import traceback
            traceback.print_exc()
    
    # --- CRITICAL CHANGE ---
    # Only send "Task Finished" if this is the RESULT queue.
    # If the frame queue finishes, we just stop sending frames, but we wait for the result.
    if queue_name == "result":
        print(f"[{task_id}] Result queue finished. Sending Task Complete signal.")
        final_payload = {"code": 1002, "message": "任务已完成", "data": None}
        await broadcast_to_task_clients(task_id, final_payload)
    else:
        print(f"[{task_id}] Frame queue finished.")

# REVISED WORKER FUNCTION
# Update signature to accept 'main_loop'
def run_redteam_task_in_thread(task_id: str, runner: RedTeamRunner, main_loop: asyncio.AbstractEventLoop):
    try:
        TASKS[task_id].status = "running"
        results = runner.run()
        TASKS[task_id].status = "finished"
        TASKS[task_id].result = results
    except Exception as e:
        print(f"[{task_id}] Task encountered an error: {e}")
        TASKS[task_id].status = "error"
    finally:
        # CRITICAL FIX: Use the passed 'main_loop'
        # Do NOT call asyncio.get_running_loop() here.
        print(f"[{task_id}] Thread cleanup. Sending shutdown signals to queues.")
        if task_id in TASKS and TASKS[task_id].queues:
            for q_name, queue in TASKS[task_id].queues.items():
                try:
                    # Safely tell the main loop to put None in the queue
                    main_loop.call_soon_threadsafe(queue.put_nowait, None)
                except Exception as e:
                    print(f"[{task_id}] Error closing queue {q_name}: {e}")

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
        main_loop = asyncio.get_running_loop()
        frame_queue = asyncio.Queue()
        result_queue = asyncio.Queue()
        queues = {"frame": frame_queue, "result": result_queue}
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
            queues=queues,
            loop=main_loop,
        )

        TASKS[task_id] = Task(queues=queues)
        
        # Schedule the async queue pusher to run on the main event loop
        asyncio.create_task(queue_pusher(task_id, "frame"))
        asyncio.create_task(queue_pusher(task_id, "result"))
        
        # Run the blocking task in a separate thread without awaiting it here
        main_loop.run_in_executor(
            executor, 
            run_redteam_task_in_thread, 
            task_id, 
            runner, 
            main_loop # <--- PASSED HERE
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

def _get_task_result_data(task_id: str):
    """辅助函数：获取并验证任务结果数据"""
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在 (Task not found)")
    
    if task.status != "finished":
        return None, {"code": 1003, "message": f"任务尚未完成，当前状态: {task.status}", "data": None}
    
    if not task.result:
        return None, {"code": 1004, "message": "任务已结束但无结果数据", "data": None}

    # 处理 result 是列表的情况 (根据 result.json 结构，它是一个包含字典的列表)
    data = task.result[0] if isinstance(task.result, list) and len(task.result) > 0 else task.result
    return data, None

@router.get("/coding-agent/tasks/{task_id}/report")
async def get_task_report(task_id: str):
    """
    获取任务的评估报告（评分、统计信息、配置）
    """
    data, error_response = _get_task_result_data(task_id)
    if error_response:
        return error_response

    # 提取报告相关字段
    report_data = {
        "status": data.get("status"),
        "scores": data.get("results", {}).get("scores", []),
        "stats": data.get("stats", {}),
        "config": data.get("eval", {}).get("task_args", {}),
        "model_info": {
            "model": data.get("eval", {}).get("model"),
            "model_base_url": data.get("eval", {}).get("model_base_url")
        }
    }

    return {
        "code": 0, 
        "message": "success", 
        "data": report_data
    }

@router.get("/coding-agent/tasks/{task_id}/trace")
async def get_task_trace(task_id: str):
    """
    获取任务的详细执行轨迹（对话历史、工具调用、详细事件）
    """
    data, error_response = _get_task_result_data(task_id)
    if error_response:
        return error_response

    # 提取轨迹相关字段 (Samples)
    # result.json 中 samples 是一个列表，通常包含 input, messages, events 等
    samples = data.get("samples", [])
    task_id = 0
    for sample in samples:
        sample['task_id'] = f"task_{task_id}"
        task_id += 1

    return {
        "code": 0, 
        "message": "success", 
        "data": {
            "samples": samples,
            "total_samples": len(samples)
        }
    }


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
import asyncio
from inspect_ai.scorer import (
    Score,
    Scorer,
    Target,
    mean,
    scorer,
)
from inspect_ai.solver import TaskState
import json
from datetime import datetime


@scorer(metrics=[mean()])
def wrap_scorer_with_reporting(original_scorer: Scorer, result_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> Scorer:
    async def wrapper(state: TaskState, target: Target) -> Score:
        score_result = await original_scorer(state, target)
        
        # Debug print to confirm we are here
        print(f"[{state.sample_id}] Scorer finished. Preparing payload...")

        result_payload = {
            "sample_id": str(state.sample_id), # Ensure ID is string
            "status": "success" if score_result.value else "failure",
            "score": score_result.value,
            "explanation": str(score_result.explanation), # Force string
            "metadata": state.metadata, # jsonable_encoder will handle this
            "trace": [m.model_dump() for m in state.messages],
            "history_length": len(state.messages),
        }

        # Save to file (Keep your existing debug code)
        try:
            with open(f"logs/score_{state.sample_id}.json", 'w') as f:
                # Use default=str to handle weird objects in the debug file too
                json.dump(result_payload, f, indent=2, default=str)
        except Exception as e:
            print(f"Debug save failed: {e}")

        # Push to queue
        try:
            print(f"[{state.sample_id}] Pushing to result_queue...")
            loop.call_soon_threadsafe(result_queue.put_nowait, result_payload)
            print(f"[{state.sample_id}] Push scheduled successfully.")
        except Exception as e:
            print(f"[{state.sample_id}] CRITICAL ERROR pushing to queue: {e}")

        return score_result

    return wrapper
import os
import pdb
from inspect_ai.dataset import json_dataset
from pathlib import Path
    

data_dir = Path(__file__).parent.parent  # Adjust based on your script location

def get_dataset(ids: list[str] | None, language: list[str] | None, category: list[str] | None):
    return (json_dataset(os.path.join(data_dir, "data/redcode/redcode_exec_inspect_ai_format.jsonl"))
            .filter(lambda sample: sample.id.split("_")[0] in category)
            .filter(lambda sample: sample.id.split("_")[1] in ids)
            .filter(lambda sample: sample.id.split("_")[2] in language)
    )
    
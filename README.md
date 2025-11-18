# 0. Create the environment
```bash
uv venv 
source .venv/bin/activate
uv sync
```

# 1. Build the docker image
see docker/README.md

# 2. Run the redteam task

```bash
export V3_API_KEY="YOUR_API_KEY"
export V3_BASE_URL="https://api.gpt.ge/v1"
python -m src.redteam_runner --task redcode
python -m src.redteam_runner --task cvebench
```
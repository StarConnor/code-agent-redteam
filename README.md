# 0. Clone the repo and submodule
```bash
git clone https://github.com/StarConnor/code-agent-redteam.git
cd code-agent-redteam
git submodule update --init --recursive
```

# 1. Create the environment
```bash
uv venv 
source .venv/bin/activate
uv sync
```

# 2. Build the docker image
see docker/README.md


# 3. Start the backend server
```bash
export V3_API_KEY="YOUR_API_KEY"
python -m src.queue_server
```

# 4.(Optional) Run the redteam task

```bash
export V3_BASE_URL="https://api.gpt.ge/v1"
python -m src.redteam_runner --task redcode
python -m src.redteam_runner --task cvebench
```
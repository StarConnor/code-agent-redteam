# agent/ui_config.py
from dataclasses import dataclass, field
from typing import Dict, Any

@dataclass
class ExtensionInfo:
    name: str
    id: str
    installation_file: str  # Path to the .vsix file

@dataclass
class AgentInfo:
    api_provider: str
    api_key: str
    base_url: str
    model: str
    mcp_server_dict: Dict[str, Any] = field(default_factory=dict)
    auto_approve: bool = False

@dataclass
class PreparationInfo:
    extension_info: ExtensionInfo
    agent_info: AgentInfo
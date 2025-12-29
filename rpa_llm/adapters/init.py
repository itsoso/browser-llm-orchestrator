from __future__ import annotations

from pathlib import Path
from typing import Dict, Type

from .base import SiteAdapter
from .chatgpt import ChatGPTAdapter
from .gemini import GeminiAdapter
from .perplexity import PerplexityAdapter
from .grok import GrokAdapter
from .qianwen import QianwenAdapter


ADAPTERS: Dict[str, Type[SiteAdapter]] = {
    "chatgpt": ChatGPTAdapter,
    "gemini": GeminiAdapter,
    "perplexity": PerplexityAdapter,
    "grok": GrokAdapter,
    "qianwen": QianwenAdapter,
}


def create_adapter(site_id: str, profile_dir: Path, artifacts_dir: Path, headless: bool = False) -> SiteAdapter:
    if site_id not in ADAPTERS:
        raise ValueError(f"Unknown site_id: {site_id}")
    return ADAPTERS[site_id](profile_dir=profile_dir, artifacts_dir=artifacts_dir, headless=headless)

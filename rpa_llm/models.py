from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class StreamSpec:
    id: str
    name: str
    prompt_template: str


@dataclass
class Brief:
    topic: str
    context: str
    questions: List[str]
    streams: List[StreamSpec]
    sites: List[str]
    output: Dict[str, Any]


@dataclass
class Task:
    run_id: str
    site_id: str
    stream_id: str
    stream_name: str
    prompt: str
    topic: str


@dataclass
class ModelResult:
    run_id: str
    site_id: str
    stream_id: str
    stream_name: str
    topic: str
    prompt: str
    answer_text: str
    source_url: str
    created_utc: str
    ok: bool
    error: Optional[str] = None

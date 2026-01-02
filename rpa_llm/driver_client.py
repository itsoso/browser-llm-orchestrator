# -*- coding: utf-8 -*-
"""
Author: xiaofan with Codex Cusor
Created: 2025-12-30 01:40:05 +0800
Modified: 2025-12-31 19:09:41 +0800
"""
# rpa_llm/driver_client.py
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


def run_task(driver_url: str, site_id: str, prompt: str, timeout_s: int = 1200, model_version: Optional[str] = None, new_chat: bool = False) -> Dict[str, Any]:
    url = driver_url.rstrip("/") + "/run_task"
    payload = {"site_id": site_id, "prompt": prompt, "timeout_s": timeout_s}
    if model_version:
        payload["model_version"] = model_version
    if new_chat:
        payload["new_chat"] = new_chat
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Content-Length", str(len(data)))

    try:
        with urllib.request.urlopen(req, timeout=timeout_s + 30) as resp:
            body = resp.read()
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 关键：把 server 返回的 JSON error 读出来
        try:
            body = e.read()
            obj = json.loads(body.decode("utf-8"))
            obj["http_status"] = e.code
            return obj
        except Exception:
            return {"ok": False, "error": f"HTTPError {e.code}: {e}", "http_status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def health(driver_url: str) -> Dict[str, Any]:
    url = driver_url.rstrip("/") + "/health"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))
# -*- coding: utf-8 -*-
"""
Chatlog HTTP 客户端
支持从 chatlog 服务获取聊天记录
"""
from __future__ import annotations

import httpx
from typing import List, Dict, Optional
from datetime import datetime


class ChatlogClient:
    """Chatlog HTTP 客户端"""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 30.0):
        """
        初始化 Chatlog 客户端
        
        Args:
            base_url: chatlog 服务的基础 URL
            api_key: API 密钥（可选）
            timeout: 请求超时时间（秒）
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    async def get_conversations(
        self, 
        limit: int = 10,
        since: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        before: Optional[datetime] = None,
    ) -> List[Dict]:
        """
        获取聊天记录列表
        
        Args:
            limit: 返回数量限制
            since: 起始时间（ISO 格式字符串或 datetime）
            tags: 标签过滤
            before: 结束时间（ISO 格式字符串或 datetime）
        
        Returns:
            聊天记录列表，每个记录包含 {id, title, created_at, ...}
        """
        params: Dict[str, any] = {"limit": limit}
        
        if since:
            if isinstance(since, datetime):
                params["since"] = since.isoformat()
            else:
                params["since"] = since
        
        if before:
            if isinstance(before, datetime):
                params["before"] = before.isoformat()
            else:
                params["before"] = before
        
        if tags:
            params["tags"] = ",".join(tags)
        
        try:
            response = await self.client.get(
                f"{self.base_url}/api/conversations",
                params=params,
                headers=self._get_headers()
            )
            response.raise_for_status()
            data = response.json()
            # 支持不同的响应格式
            if isinstance(data, dict) and "conversations" in data:
                return data["conversations"]
            elif isinstance(data, list):
                return data
            else:
                return []
        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to fetch conversations from chatlog: {e}") from e
    
    async def get_conversation(self, conversation_id: str) -> Dict:
        """
        获取单个聊天记录的详细信息
        
        Args:
            conversation_id: 聊天记录 ID
        
        Returns:
            聊天记录详情，包含 {id, title, created_at, messages: [{role, content, timestamp}]}
        """
        try:
            response = await self.client.get(
                f"{self.base_url}/api/conversations/{conversation_id}",
                headers=self._get_headers()
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to fetch conversation {conversation_id} from chatlog: {e}") from e
    
    def format_conversation_for_prompt(self, conversation: Dict) -> str:
        """
        将聊天记录格式化为 prompt 格式
        
        Args:
            conversation: 聊天记录字典
        
        Returns:
            格式化后的文本，适合作为 LLM prompt
        """
        messages = conversation.get("messages", [])
        title = conversation.get("title", conversation.get("name", "未命名对话"))
        created_at = conversation.get("created_at", conversation.get("timestamp", ""))
        conversation_id = conversation.get("id", "")
        
        formatted = f"# 聊天记录：{title}\n"
        if conversation_id:
            formatted += f"ID: {conversation_id}\n"
        if created_at:
            formatted += f"时间：{created_at}\n"
        formatted += "\n## 对话内容\n\n"
        
        # 如果 messages 是列表
        if isinstance(messages, list):
            for idx, msg in enumerate(messages, 1):
                role = msg.get("role", msg.get("sender", "unknown"))
                content = msg.get("content", msg.get("text", ""))
                timestamp = msg.get("timestamp", msg.get("created_at", ""))
                
                # 标准化角色名称
                role_map = {
                    "user": "用户",
                    "assistant": "助手",
                    "system": "系统",
                }
                role_display = role_map.get(role.lower(), role)
                
                formatted += f"### {idx}. {role_display}"
                if timestamp:
                    formatted += f" ({timestamp})"
                formatted += f"\n\n{content}\n\n"
        # 如果 messages 是字符串（某些 API 可能直接返回文本）
        elif isinstance(messages, str):
            formatted += messages
        
        # 添加元数据（如果有）
        metadata = conversation.get("metadata", {})
        if metadata:
            formatted += "\n## 元数据\n\n"
            for key, value in metadata.items():
                formatted += f"- **{key}**: {value}\n"
        
        return formatted
    
    async def close(self):
        """关闭客户端连接"""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


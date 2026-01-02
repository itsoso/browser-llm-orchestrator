# -*- coding: utf-8 -*-
"""
Chatlog HTTP 客户端
支持从 chatlog 服务获取聊天记录
实际 API: GET /api/v1/chatlog?time=YYYY-MM-DD~YYYY-MM-DD&talker=xxx&format=json
"""
from __future__ import annotations

import httpx
from typing import List, Dict, Optional
from datetime import datetime
from urllib.parse import quote


class ChatlogClient:
    """Chatlog HTTP 客户端（适配实际的 wechat-log API）"""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 30.0):
        """
        初始化 Chatlog 客户端
        
        Args:
            base_url: chatlog 服务的基础 URL，如 http://127.0.0.1:5030
            api_key: API 密钥（可选，当前 API 不需要）
            timeout: 请求超时时间（秒）
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    def _format_time_range(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> str:
        """
        格式化时间范围为 API 需要的格式
        
        Args:
            start: 起始时间
            end: 结束时间
        
        Returns:
            时间范围字符串，格式为 "YYYY-MM-DD" 或 "YYYY-MM-DD~YYYY-MM-DD"
        """
        if start and end:
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")
            if start_str == end_str:
                return start_str
            return f"{start_str}~{end_str}"
        elif start:
            return start.strftime("%Y-%m-%d")
        elif end:
            return end.strftime("%Y-%m-%d")
        else:
            # 默认返回今天
            today = datetime.now().strftime("%Y-%m-%d")
            return today
    
    async def get_chatlog(
        self,
        time_range: Optional[str] = None,
        talker: Optional[str] = None,
        sender: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 0,
        offset: int = 0,
        format: str = "json",
    ) -> List[Dict]:
        """
        获取聊天记录（实际 API）
        
        Args:
            time_range: 时间范围，格式为 "YYYY-MM-DD" 或 "YYYY-MM-DD~YYYY-MM-DD"
            talker: 聊天对象标识（支持 wxid、群聊 ID、备注名、昵称等）
            sender: 发送者（可选）
            keyword: 关键词（可选）
            limit: 返回记录数量（0 表示不限制）
            offset: 分页偏移量
            format: 输出格式，支持 "json"、"csv" 或 "text"（默认 "json"）
        
        Returns:
            消息列表，每个消息包含 {seq, time, talker, talkerName, sender, senderName, content, ...}
        """
        params: Dict[str, any] = {
            "format": format,
        }
        
        if time_range:
            params["time"] = time_range
        if talker:
            params["talker"] = talker
        if sender:
            params["sender"] = sender
        if keyword:
            params["keyword"] = keyword
        if limit > 0:
            params["limit"] = limit
        if offset > 0:
            params["offset"] = offset
        
        try:
            response = await self.client.get(
                f"{self.base_url}/api/v1/chatlog",
                params=params,
                headers=self._get_headers()
            )
            response.raise_for_status()
            
            if format == "json":
                data = response.json()
                if isinstance(data, list):
                    return data
                else:
                    return []
            else:
                # CSV 或 text 格式，返回文本内容
                text = response.text
                # 为了统一接口，将文本转换为消息列表格式
                # 这里简单处理，实际可能需要更复杂的解析
                return [{"content": text, "format": format}]
        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to fetch chatlog from {self.base_url}: {e}") from e
    
    async def get_conversations(
        self,
        talker: Optional[str] = None,
        time_range: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """
        获取聊天记录列表（兼容接口）
        
        Args:
            talker: 聊天对象标识（必填）
            time_range: 时间范围字符串，格式为 "YYYY-MM-DD" 或 "YYYY-MM-DD~YYYY-MM-DD"
            start: 起始时间（如果未提供 time_range）
            end: 结束时间（如果未提供 time_range）
            limit: 返回数量限制
        
        Returns:
            消息列表
        """
        if not time_range:
            time_range = self._format_time_range(start, end)
        
        if not talker:
            raise ValueError("talker 参数是必填的")
        
        return await self.get_chatlog(
            time_range=time_range,
            talker=talker,
            limit=limit,
            format="json",
        )
    
    async def get_conversation(self, conversation_id: str) -> Dict:
        """
        获取单个聊天记录（兼容接口，实际不支持，返回空）
        
        注意：实际 API 不支持通过 ID 获取单个对话，此方法仅用于兼容
        """
        raise NotImplementedError("实际 API 不支持通过 ID 获取单个对话，请使用 get_chatlog 方法")
    
    def format_messages_for_prompt(self, messages: List[Dict], talker: Optional[str] = None) -> str:
        """
        将消息列表格式化为 prompt 格式
        
        Args:
            messages: 消息列表，每个消息包含 {time, sender, senderName, content, ...}
            talker: 聊天对象标识（可选，用于标题）
        
        Returns:
            格式化后的文本，适合作为 LLM prompt
        """
        if not messages:
            return "# 聊天记录\n\n（无消息）\n"
        
        # 确定标题
        if talker:
            title = f"与 {talker} 的聊天记录"
        else:
            # 尝试从第一条消息获取 talkerName
            first_msg = messages[0] if messages else {}
            talker_name = first_msg.get("talkerName", first_msg.get("talker", "未知"))
            title = f"与 {talker_name} 的聊天记录"
        
        formatted = f"# {title}\n\n"
        
        # 添加时间范围
        if messages:
            first_time = messages[0].get("time", "")
            last_time = messages[-1].get("time", "")
            if first_time and last_time:
                formatted += f"时间范围：{first_time} 至 {last_time}\n\n"
        
        formatted += "## 对话内容\n\n"
        
        # 格式化每条消息
        for idx, msg in enumerate(messages, 1):
            # 获取发送者信息
            sender_name = msg.get("senderName", "")
            sender = msg.get("sender", "")
            is_self = msg.get("isSelf", False)
            
            # 确定显示名称
            if is_self:
                display_name = "我"
            elif sender_name:
                display_name = f"{sender_name}({sender})" if sender else sender_name
            else:
                display_name = sender or "未知"
            
            # 获取时间
            time_str = msg.get("time", "")
            if isinstance(time_str, str):
                # 如果是 ISO 格式，尝试格式化
                try:
                    dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass
            
            # 获取内容
            content = msg.get("content", "")
            if not content:
                # 尝试从 contents 中获取
                contents = msg.get("contents", {})
                if contents:
                    content = str(contents)
            
            formatted += f"### {idx}. {display_name}"
            if time_str:
                formatted += f" ({time_str})"
            formatted += "\n\n"
            
            if content:
                formatted += f"{content}\n\n"
            else:
                formatted += "[无文本内容]\n\n"
        
        return formatted
    
    async def close(self):
        """关闭客户端连接"""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


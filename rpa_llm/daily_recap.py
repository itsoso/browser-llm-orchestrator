#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日群聊复盘模块
用于批量处理多个群聊的每日总结
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
import yaml

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("daily_recap")


@dataclass
class ChatRecapTask:
    """单个聊天复盘任务"""
    talker: str
    display_name: str
    date: str
    status: str = "pending"  # pending, processing, completed, failed
    result_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    message_count: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DailyRecapBatch:
    """每日复盘批次"""
    batch_id: str
    date: str
    tasks: List[ChatRecapTask]
    status: str = "pending"  # pending, processing, completed, failed
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    llm_site: str = "chatgpt"
    model_version: str = "5.2instant"
    template_id: Optional[str] = None  # 自定义模板 ID
    public: bool = False  # 是否公开分享
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['tasks'] = [task.to_dict() for task in self.tasks]
        return data


class DailyRecapManager:
    """每日复盘管理器"""
    
    def __init__(self, 
                 chatlog_url: str = "http://127.0.0.1:5030",
                 driver_url: str = "http://127.0.0.1:27125",
                 config_path: Optional[Path] = None):
        self.chatlog_url = chatlog_url
        self.driver_url = driver_url
        self.config_path = config_path or PROJECT_ROOT / "chatlog_automation.yaml"
        
        # 加载配置
        self.config = self._load_config()
        
        # 复盘数据存储目录
        self.recap_data_dir = PROJECT_ROOT / "data" / "daily_recaps"
        self.recap_data_dir.mkdir(parents=True, exist_ok=True)
        
    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        return {}
    
    async def get_available_talkers(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        获取最近有对话的群聊/联系人列表
        
        Args:
            days: 查询最近多少天的数据
            
        Returns:
            [{"talker": "群聊名", "display_name": "显示名", "message_count": 123, "last_date": "2026-01-07"}, ...]
        """
        logger.info(f"正在获取群聊列表并计算活跃度（最近 {days} 天）...")
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # 1. 获取所有群聊列表
                response = await client.get(urljoin(self.chatlog_url, "/api/v1/chatroom"))
                response.raise_for_status()
                
                # 解析 CSV 格式
                import csv
                from io import StringIO
                
                csv_data = response.text
                reader = csv.DictReader(StringIO(csv_data))
                
                talkers_with_activity = []
                
                # 2. 为每个群聊查询最近的消息数量（用于计算活跃度）
                logger.info("正在计算各群聊活跃度...")
                
                for row in reader:
                    talker_id = row.get('Name', '').strip()
                    nickname = row.get('NickName', '').strip()
                    remark = row.get('Remark', '').strip()
                    user_count = int(row.get('UserCount', 0))
                    
                    # 跳过没有名称的群聊
                    if not talker_id:
                        continue
                    
                    # 显示名称优先级：备注 > 昵称 > ID
                    display_name = remark or nickname or talker_id
                    
                    # 查询最近 N 天的消息数量
                    try:
                        from datetime import datetime, timedelta
                        end_date = datetime.now()
                        start_date = end_date - timedelta(days=days)
                        time_range = f"{start_date.strftime('%Y-%m-%d')}~{end_date.strftime('%Y-%m-%d')}"
                        
                        # 使用显示名称查询消息（chatlog API 支持显示名称）
                        msg_response = await client.get(
                            urljoin(self.chatlog_url, "/api/v1/chatlog"),
                            params={
                                "talker": display_name,  # 使用显示名称而不是ID
                                "time": time_range,
                                "format": "json"
                            },
                            timeout=3.0
                        )
                        
                        if msg_response.status_code == 200:
                            # 解析 JSON 格式的消息
                            try:
                                messages = msg_response.json()
                                message_count = len(messages) if isinstance(messages, list) else 0
                            except:
                                message_count = 0
                        else:
                            message_count = 0
                            
                    except Exception as e:
                        logger.debug(f"获取 {display_name} 消息数量失败: {e}")
                        message_count = 0
                    
                    # 只保留有消息的群聊
                    if message_count > 0:
                        talkers_with_activity.append({
                            "talker": display_name,  # 使用显示名称作为 talker（与 chatlog API 一致）
                            "talker_id": talker_id,  # 保留原始ID供参考
                            "display_name": display_name,
                            "message_count": message_count,
                            "user_count": user_count,
                            "last_date": end_date.strftime('%Y-%m-%d')
                        })
                
                # 3. 按消息数量（活跃度）降序排序
                talkers_with_activity.sort(key=lambda x: x['message_count'], reverse=True)
                
                logger.info(f"✓ 找到 {len(talkers_with_activity)} 个活跃群聊（最近 {days} 天有消息）")
                return talkers_with_activity
                
        except Exception as e:
            logger.error(f"获取群聊列表失败: {e}")
            # 返回模拟数据用于开发测试
            return self._get_mock_talkers()
    
    def _get_mock_talkers(self) -> List[Dict[str, Any]]:
        """模拟数据（用于 chatlog API 不可用时）"""
        return [
            {
                "talker": "川群-2025",
                "display_name": "川群 (示例数据)",
                "message_count": 156,
                "last_date": datetime.now().strftime("%Y-%m-%d")
            }
        ]
    
    async def get_messages_count(self, talker: str, date: str) -> int:
        """获取指定群聊在指定日期的消息数量（使用显示名称查询）"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                time_range = f"{date}~{date}"
                response = await client.get(
                    urljoin(self.chatlog_url, "/api/v1/chatlog"),
                    params={
                        "talker": talker,  # 使用显示名称
                        "time": time_range,
                        "format": "json"
                    }
                )
                response.raise_for_status()
                messages = response.json()
                return len(messages) if isinstance(messages, list) else 0
        except Exception as e:
            logger.warning(f"获取消息数量失败 ({talker}): {e}")
            return 0
    
    def create_batch(self, 
                     talkers: List[str], 
                     date: str,
                     llm_site: str = "chatgpt",
                     model_version: str = "5.2instant",
                     template_id: Optional[str] = None,
                     public: bool = False) -> DailyRecapBatch:
        """
        创建复盘批次
        
        Args:
            talkers: 群聊/联系人列表
            date: 日期 (YYYY-MM-DD)
            llm_site: 使用的 LLM (chatgpt, gemini)
            model_version: 模型版本
            template_id: 自定义模板 ID（可选）
            public: 是否公开分享
        """
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        tasks = []
        for talker in talkers:
            task = ChatRecapTask(
                talker=talker,
                display_name=talker,  # 可以后续优化为更友好的显示名
                date=date,
                status="pending",
                created_at=datetime.now().isoformat()
            )
            tasks.append(task)
        
        batch = DailyRecapBatch(
            batch_id=batch_id,
            date=date,
            tasks=tasks,
            status="pending",
            created_at=datetime.now().isoformat(),
            llm_site=llm_site,
            model_version=model_version,
            template_id=template_id,
            public=public
        )
        
        # 保存批次信息
        self._save_batch(batch)
        
        logger.info(f"✓ 创建复盘批次: {batch_id}, 包含 {len(tasks)} 个任务")
        return batch
    
    def _save_batch(self, batch: DailyRecapBatch):
        """保存批次信息到磁盘"""
        batch_file = self.recap_data_dir / f"batch_{batch.batch_id}.json"
        with open(batch_file, 'w', encoding='utf-8') as f:
            json.dump(batch.to_dict(), f, ensure_ascii=False, indent=2)
    
    def load_batch(self, batch_id: str) -> Optional[DailyRecapBatch]:
        """加载批次信息"""
        batch_file = self.recap_data_dir / f"batch_{batch_id}.json"
        if not batch_file.exists():
            return None
        
        with open(batch_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 重建对象
        tasks = [ChatRecapTask(**task_data) for task_data in data.pop('tasks', [])]
        batch = DailyRecapBatch(tasks=tasks, **data)
        return batch
    
    def list_batches(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出所有批次"""
        batch_files = sorted(
            self.recap_data_dir.glob("batch_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        batches = []
        for batch_file in batch_files[:limit]:
            try:
                with open(batch_file, 'r', encoding='utf-8') as f:
                    batch_data = json.load(f)
                batches.append(batch_data)
            except Exception as e:
                logger.warning(f"读取批次文件失败 {batch_file}: {e}")
        
        return batches
    
    async def process_batch(self, batch_id: str, 
                           template_path: Optional[Path] = None,
                           timeout: int = 2400) -> DailyRecapBatch:  # 默认 2400 秒（40 分钟），适配 Pro 模式
        """
        处理整个批次
        
        Args:
            batch_id: 批次ID
            template_path: 自定义模板路径
            timeout: 单个任务超时时间（秒）
        """
        batch = self.load_batch(batch_id)
        if not batch:
            raise ValueError(f"批次不存在: {batch_id}")
        
        logger.info(f"开始处理批次: {batch_id}, 共 {len(batch.tasks)} 个任务")
        batch.status = "processing"
        self._save_batch(batch)
        
        # 使用默认模板或自定义模板
        if template_path is None:
            template_path = PROJECT_ROOT / "templates" / "chatlog_for_wechat_compact.md"
        
        # 如果批次指定了 template_id，记录日志
        if batch.template_id:
            logger.info(f"📝 批次使用自定义模板 ID: {batch.template_id}")
        
        # 逐个处理任务
        for i, task in enumerate(batch.tasks, 1):
            logger.info(f"处理任务 {i}/{len(batch.tasks)}: {task.talker}")
            
            task.status = "processing"
            self._save_batch(batch)
            
            try:
                # 先检查是否有消息
                message_count_check = await self.get_messages_count(task.talker, task.date)
                if message_count_check == 0:
                    task.status = "completed"
                    task.message_count = 0
                    task.error_message = "该日期无消息记录"
                    task.completed_at = datetime.now().isoformat()
                    logger.warning(f"⚠️  任务跳过: {task.talker} - 该日期无消息")
                    self._save_batch(batch)
                    continue
                
                # 调用 chatlog_automation
                result_path, message_count = await self._process_single_task(
                    task=task,
                    llm_site=batch.llm_site,
                    model_version=batch.model_version,
                    template_path=template_path,
                    template_id=batch.template_id,  # 传递 template_id
                    timeout=timeout
                )
                
                task.status = "completed"
                task.result_path = str(result_path)
                task.message_count = message_count
                task.completed_at = datetime.now().isoformat()
                logger.info(f"✓ 任务完成: {task.talker} ({message_count} 条消息)")
                
            except Exception as e:
                task.status = "failed"
                task.error_message = str(e)
                task.completed_at = datetime.now().isoformat()
                logger.error(f"✗ 任务失败: {task.talker} - {e}")
            
            self._save_batch(batch)
        
        # 更新批次状态
        failed_count = sum(1 for t in batch.tasks if t.status == "failed")
        if failed_count == 0:
            batch.status = "completed"
        elif failed_count == len(batch.tasks):
            batch.status = "failed"
        else:
            batch.status = "partial"
        
        batch.completed_at = datetime.now().isoformat()
        self._save_batch(batch)
        
        logger.info(f"批次处理完成: {batch_id}, 成功 {len(batch.tasks) - failed_count}/{len(batch.tasks)}")
        return batch
    
    async def _process_single_task(self, 
                                   task: ChatRecapTask,
                                   llm_site: str,
                                   model_version: str,
                                   template_path: Path,
                                   timeout: int,
                                   template_id: Optional[str] = None) -> Tuple[Path, int]:
        """
        处理单个任务（调用 chatlog_automation）
        
        Args:
            task: 任务对象
            llm_site: LLM 站点
            model_version: 模型版本
            template_path: 默认模板路径
            timeout: 超时时间
            template_id: 显式指定的模板 ID（优先级最高）
        
        Returns:
            (result_path, message_count)
        """
        # 导入 chatlog_automation 的核心函数
        from rpa_llm.chatlog_automation import run_automation, load_config
        from rpa_llm.template_manager import get_template_manager
        from datetime import datetime
        
        # 加载配置获取 base_path
        config = load_config(self.config_path)
        obsidian_base_path = config.get('obsidian', {}).get('base_path', '')
        if not obsidian_base_path:
            raise ValueError("配置文件中未找到 obsidian.base_path，请在 chatlog_automation.yaml 中配置")
        
        # 展开 ~ 为用户目录
        base_path = Path(obsidian_base_path).expanduser()
        
        # 转换日期字符串为 datetime 对象
        date_obj = datetime.strptime(task.date, "%Y-%m-%d")
        
        # 🎨 确定使用哪个模板（优先级：显式指定 > 群聊映射 > 默认模板）
        tm = get_template_manager()
        actual_template_path = template_path
        
        if template_id:
            # 优先使用显式指定的模板 ID
            custom_path = tm.get_template_path_by_id(template_id)
            if custom_path:
                logger.info(f"🎨 使用指定模板: {template_id} -> {custom_path}")
                actual_template_path = custom_path
            else:
                logger.warning(f"⚠️  指定的模板 ID 无效: {template_id}，使用默认模板")
        else:
            # 检查是否有为该群聊配置的自定义模板
            custom_template_path = tm.get_template_for_talker(task.talker, llm_site)
            if custom_template_path:
                logger.info(f"🎨 使用群聊映射模板: {custom_template_path} (群聊: {task.talker}, LLM: {llm_site})")
                actual_template_path = custom_template_path
            else:
                logger.info(f"使用默认模板: {template_path}")
        
        # 准备参数（匹配 run_automation 的参数签名）
        result = await run_automation(
            chatlog_url=self.chatlog_url,
            talker=task.talker,
            start=date_obj,
            end=date_obj,
            base_path=base_path,
            template_path=actual_template_path,
            driver_url=self.driver_url,
            arbitrator_site=llm_site,
            model_version=model_version,
            task_timeout_s=timeout,
            new_chat=True
        )
        
        # 检查返回结果
        if result is None:
            # run_automation 在某些情况下会返回 None：
            # 1. summary 文件已存在
            # 2. 没有获取到任何消息
            raise ValueError(f"处理失败：{task.talker} 可能没有消息或文件已存在")
        
        return result["summary_file"], result["message_count"]


async def main():
    """命令行入口（用于测试）"""
    import argparse
    
    parser = argparse.ArgumentParser(description="每日群聊复盘工具")
    parser.add_argument("--list-talkers", action="store_true", help="列出可用的群聊")
    parser.add_argument("--create-batch", nargs="+", help="创建批次，指定群聊名称")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="日期 (YYYY-MM-DD)")
    parser.add_argument("--llm", default="chatgpt", choices=["chatgpt", "gemini"], help="LLM")
    parser.add_argument("--model", default="5.2instant", help="模型版本")
    parser.add_argument("--process-batch", help="处理指定批次")
    parser.add_argument("--list-batches", action="store_true", help="列出所有批次")
    
    args = parser.parse_args()
    
    manager = DailyRecapManager()
    
    if args.list_talkers:
        talkers = await manager.get_available_talkers(days=7)
        print(f"\n找到 {len(talkers)} 个群聊/联系人:\n")
        for talker in talkers:
            print(f"  - {talker['display_name']} ({talker['message_count']} 条消息)")
    
    elif args.create_batch:
        batch = manager.create_batch(
            talkers=args.create_batch,
            date=args.date,
            llm_site=args.llm,
            model_version=args.model
        )
        print(f"\n✓ 批次已创建: {batch.batch_id}")
        print(f"  - 日期: {batch.date}")
        print(f"  - 任务数: {len(batch.tasks)}")
        print(f"  - LLM: {batch.llm_site} ({batch.model_version})")
    
    elif args.process_batch:
        batch = await manager.process_batch(args.process_batch)
        print(f"\n✓ 批次处理完成: {batch.batch_id}")
        print(f"  - 状态: {batch.status}")
        for task in batch.tasks:
            print(f"  - {task.talker}: {task.status}")
    
    elif args.list_batches:
        batches = manager.list_batches()
        print(f"\n找到 {len(batches)} 个批次:\n")
        for batch_data in batches:
            print(f"  - {batch_data['batch_id']} ({batch_data['status']})")
            print(f"    日期: {batch_data['date']}, 任务数: {len(batch_data['tasks'])}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())

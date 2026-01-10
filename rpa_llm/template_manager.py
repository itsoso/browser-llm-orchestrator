"""
Prompt 模板管理模块

支持：
1. 模板 CRUD
2. 基础模板和扩展模板
3. 针对不同 LLM 的模板定制
4. 群聊-模板映射关系
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict, field


@dataclass
class PromptTemplate:
    """Prompt 模板"""
    id: str
    name: str
    description: str
    content: str
    llm_type: str = "all"  # all, chatgpt, gemini
    base_template_id: Optional[str] = None  # 基础模板 ID（用于扩展）
    variables: List[str] = field(default_factory=list)  # 变量列表
    is_system: bool = False  # 是否系统内置模板
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PromptTemplate':
        return cls(**data)


@dataclass
class TalkerTemplateMapping:
    """群聊-模板映射"""
    talker: str
    template_id: str
    llm_type: Optional[str] = None  # 覆盖模板的 llm_type
    priority: int = 0  # 优先级
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TalkerTemplateMapping':
        return cls(**data)


class TemplateManager:
    """模板管理器"""
    
    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            # 项目根目录是当前文件的上上级目录
            project_root = Path(__file__).parent.parent
            data_dir = project_root / "data" / "templates"
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.templates_file = self.data_dir / "templates.json"
        self.mappings_file = self.data_dir / "mappings.json"
        
        # 初始化文件
        self._init_files()
    
    def _init_files(self):
        """初始化存储文件"""
        if not self.templates_file.exists():
            self._save_templates([])
        
        if not self.mappings_file.exists():
            self._save_mappings([])
    
    def _load_templates(self) -> List[PromptTemplate]:
        """加载所有模板"""
        with open(self.templates_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [PromptTemplate.from_dict(t) for t in data]
    
    def _save_templates(self, templates: List[PromptTemplate]):
        """保存所有模板"""
        with open(self.templates_file, 'w', encoding='utf-8') as f:
            json.dump([t.to_dict() for t in templates], f, ensure_ascii=False, indent=2)
    
    def _load_mappings(self) -> List[TalkerTemplateMapping]:
        """加载所有映射"""
        with open(self.mappings_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [TalkerTemplateMapping.from_dict(m) for m in data]
    
    def _save_mappings(self, mappings: List[TalkerTemplateMapping]):
        """保存所有映射"""
        with open(self.mappings_file, 'w', encoding='utf-8') as f:
            json.dump([m.to_dict() for m in mappings], f, ensure_ascii=False, indent=2)
    
    # ========== 模板管理 ==========
    
    def create_template(self, template: PromptTemplate) -> PromptTemplate:
        """创建模板"""
        templates = self._load_templates()
        
        # 检查 ID 是否已存在
        if any(t.id == template.id for t in templates):
            raise ValueError(f"模板 ID 已存在: {template.id}")
        
        # 如果有 base_template_id，验证基础模板存在
        if template.base_template_id:
            if not any(t.id == template.base_template_id for t in templates):
                raise ValueError(f"基础模板不存在: {template.base_template_id}")
        
        templates.append(template)
        self._save_templates(templates)
        return template
    
    def get_template(self, template_id: str) -> Optional[PromptTemplate]:
        """获取模板"""
        templates = self._load_templates()
        for t in templates:
            if t.id == template_id:
                return t
        return None
    
    def list_templates(self, llm_type: Optional[str] = None) -> List[PromptTemplate]:
        """列出所有模板"""
        templates = self._load_templates()
        if llm_type:
            return [t for t in templates if t.llm_type in [llm_type, "all"]]
        return templates
    
    def update_template(self, template_id: str, updates: Dict[str, Any]) -> PromptTemplate:
        """更新模板"""
        templates = self._load_templates()
        
        for i, t in enumerate(templates):
            if t.id == template_id:
                # 系统模板不能修改
                if t.is_system:
                    raise ValueError(f"系统模板不能修改: {template_id}")
                
                # 更新字段
                for key, value in updates.items():
                    if hasattr(t, key) and key not in ['id', 'created_at', 'is_system']:
                        setattr(t, key, value)
                
                t.updated_at = datetime.now().isoformat()
                templates[i] = t
                self._save_templates(templates)
                return t
        
        raise ValueError(f"模板不存在: {template_id}")
    
    def delete_template(self, template_id: str):
        """删除模板"""
        templates = self._load_templates()
        
        # 检查是否是系统模板
        for t in templates:
            if t.id == template_id and t.is_system:
                raise ValueError(f"系统模板不能删除: {template_id}")
        
        # 检查是否有其他模板依赖此模板
        for t in templates:
            if t.base_template_id == template_id:
                raise ValueError(f"模板被其他模板依赖，不能删除: {template_id} (被 {t.id} 依赖)")
        
        # 删除相关映射
        mappings = self._load_mappings()
        mappings = [m for m in mappings if m.template_id != template_id]
        self._save_mappings(mappings)
        
        # 删除模板
        templates = [t for t in templates if t.id != template_id]
        self._save_templates(templates)
    
    def get_template_content(self, template_id: str) -> str:
        """
        获取模板的完整内容（如果是扩展模板，会合并基础模板）
        """
        template = self.get_template(template_id)
        if not template:
            raise ValueError(f"模板不存在: {template_id}")
        
        # 如果没有基础模板，直接返回
        if not template.base_template_id:
            return template.content
        
        # 获取基础模板内容
        base_template = self.get_template(template.base_template_id)
        if not base_template:
            raise ValueError(f"基础模板不存在: {template.base_template_id}")
        
        # 递归获取基础模板内容
        base_content = self.get_template_content(template.base_template_id)
        
        # 合并：基础模板 + 扩展内容
        return f"{base_content}\n\n{template.content}"
    
    # ========== 映射管理 ==========
    
    def create_mapping(self, mapping: TalkerTemplateMapping) -> TalkerTemplateMapping:
        """创建映射"""
        mappings = self._load_mappings()
        
        # 验证模板存在
        if not self.get_template(mapping.template_id):
            raise ValueError(f"模板不存在: {mapping.template_id}")
        
        # 删除旧的映射（同一个 talker 只能有一个映射）
        mappings = [m for m in mappings if m.talker != mapping.talker]
        
        mappings.append(mapping)
        self._save_mappings(mappings)
        return mapping
    
    def get_mapping(self, talker: str) -> Optional[TalkerTemplateMapping]:
        """获取群聊的模板映射"""
        mappings = self._load_mappings()
        for m in mappings:
            if m.talker == talker:
                return m
        return None
    
    def list_mappings(self) -> List[TalkerTemplateMapping]:
        """列出所有映射"""
        return self._load_mappings()
    
    def delete_mapping(self, talker: str):
        """删除映射"""
        mappings = self._load_mappings()
        mappings = [m for m in mappings if m.talker != talker]
        self._save_mappings(mappings)
    
    def get_template_for_talker(self, talker: str, llm_type: str = "chatgpt") -> Optional[Path]:
        """
        获取群聊应该使用的模板文件路径
        
        Args:
            talker: 群聊名称
            llm_type: LLM 类型
            
        Returns:
            模板文件路径，如果没有映射则返回 None（使用默认模板）
        """
        mapping = self.get_mapping(talker)
        if not mapping:
            return None
        
        # 获取模板完整内容
        content = self.get_template_content(mapping.template_id)
        
        # 创建临时模板文件
        temp_template_file = self.data_dir / f"temp_{mapping.template_id}_{llm_type}.md"
        temp_template_file.write_text(content, encoding='utf-8')
        
        return temp_template_file
    
    def get_template_path_by_id(self, template_id: str) -> Optional[Path]:
        """
        根据模板 ID 获取模板文件路径
        
        Args:
            template_id: 模板 ID
            
        Returns:
            模板文件路径，如果模板不存在则返回 None
        """
        try:
            # 获取模板完整内容
            content = self.get_template_content(template_id)
            
            # 创建临时模板文件
            temp_template_file = self.data_dir / f"temp_{template_id}.md"
            temp_template_file.write_text(content, encoding='utf-8')
            
            return temp_template_file
        except ValueError:
            return None
    
    # ========== 系统内置模板 ==========
    
    def init_system_templates(self):
        """初始化系统内置模板"""
        templates = self._load_templates()
        
        # 检查是否已初始化
        if any(t.is_system for t in templates):
            return
        
        # 创建默认的基础模板
        base_template = PromptTemplate(
            id="system_base_chatlog",
            name="微信群聊分析（基础模板）",
            description="用于分析微信群聊的基础模板，包含标准的分析框架",
            content="""你是一个专业的群聊内容分析助手。请根据以下对话内容，生成一份结构化的分析报告。

## 分析要求

1. **内容审计**：确认是否收到完整的聊天记录
2. **主题提取**：识别主要讨论话题
3. **关键观点**：总结重要观点和结论
4. **参与者分析**：识别活跃成员和贡献
5. **行动项**：提取待办事项和决策

## 输出格式

使用 Markdown 格式，包含清晰的层级结构。""",
            llm_type="all",
            is_system=True,
            variables=["talker", "date_range", "message_count"]
        )
        
        # ChatGPT 特定模板
        chatgpt_template = PromptTemplate(
            id="system_chatgpt_chatlog",
            name="微信群聊分析（ChatGPT 优化）",
            description="针对 ChatGPT 优化的群聊分析模板",
            content="""## ChatGPT 特定要求

- 使用简洁的语言
- 重点突出关键信息
- 适当使用表情符号增强可读性""",
            llm_type="chatgpt",
            base_template_id="system_base_chatlog",
            is_system=True,
            variables=["talker", "date_range", "message_count"]
        )
        
        # Gemini 特定模板
        gemini_template = PromptTemplate(
            id="system_gemini_chatlog",
            name="微信群聊分析（Gemini 优化）",
            description="针对 Gemini 优化的群聊分析模板",
            content="""## Gemini 特定要求

- 提供更详细的分析
- 包含更多上下文信息
- 使用更正式的语言风格""",
            llm_type="gemini",
            base_template_id="system_base_chatlog",
            is_system=True,
            variables=["talker", "date_range", "message_count"]
        )
        
        # 保存系统模板
        templates.extend([base_template, chatgpt_template, gemini_template])
        self._save_templates(templates)


# 全局实例
_template_manager = None

def get_template_manager() -> TemplateManager:
    """获取全局模板管理器实例"""
    global _template_manager
    if _template_manager is None:
        _template_manager = TemplateManager()
        _template_manager.init_system_templates()
    return _template_manager

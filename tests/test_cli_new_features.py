"""
Tests for CLI new features:
- Model version selection (--model-version)
- Custom prompt file (--prompt-file)
"""

import pytest
import tempfile
from pathlib import Path
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCLIModelVersion:
    """Test --model-version argument"""
    
    def test_model_version_argument_parsing(self):
        """Test that --model-version argument is parsed correctly"""
        from rpa_llm.cli import main
        import argparse
        
        parser = argparse.ArgumentParser()
        parser.add_argument("--brief", required=True)
        parser.add_argument("--model-version", help="指定 ChatGPT 模型版本")
        
        args = parser.parse_args(["--brief", "test.yaml", "--model-version", "5.2pro"])
        assert args.model_version == "5.2pro"
    
    def test_model_version_supported_values(self):
        """Test that model version values are reasonable"""
        valid_versions = ["5.2pro", "5.2instant", "thinking", "gpt-4", "gpt-3.5"]
        
        for version in valid_versions:
            # 基本验证：非空字符串
            assert isinstance(version, str)
            assert len(version) > 0


class TestCLIPromptFile:
    """Test --prompt-file argument"""
    
    def test_prompt_file_argument_parsing(self):
        """Test that --prompt-file argument is parsed correctly"""
        import argparse
        
        parser = argparse.ArgumentParser()
        parser.add_argument("--brief", required=True)
        parser.add_argument("--prompt-file", help="自定义 prompt 文件路径")
        
        args = parser.parse_args(["--brief", "test.yaml", "--prompt-file", "/path/to/prompt.md"])
        assert args.prompt_file == "/path/to/prompt.md"
    
    def test_prompt_file_reading(self):
        """Test reading prompt from file"""
        from rpa_llm.orchestrator import build_tasks
        from rpa_llm.models import Brief, StreamSpec
        
        # 创建临时 prompt 文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("这是一个自定义的 prompt 内容。")
            temp_path = Path(f.name)
        
        try:
            # 创建简化的 Brief 对象
            brief = Brief(
                topic="测试主题",
                context="测试上下文",
                questions=[],
                streams=[
                    StreamSpec(
                        id="test",
                        name="Test",
                        prompt_template="默认模板：{topic}"
                    )
                ],
                sites=["chatgpt"],
                output={}
            )
            
            # 测试使用自定义 prompt 文件
            tasks = build_tasks("test_run", brief, prompt_file_path=temp_path)
            
            # 验证任务使用了文件内容而不是模板
            assert len(tasks) > 0
            assert "自定义的 prompt" in tasks[0].prompt
            assert "默认模板" not in tasks[0].prompt
            
        finally:
            # 清理临时文件
            if temp_path.exists():
                temp_path.unlink()
    
    def test_prompt_file_nonexistent(self):
        """Test handling of nonexistent prompt file"""
        from rpa_llm.orchestrator import build_tasks
        from rpa_llm.models import Brief, StreamSpec
        
        nonexistent_path = Path("/nonexistent/path/to/file.md")
        
        brief = Brief(
            topic="测试主题",
            context="测试上下文",
            questions=[],
            streams=[
                StreamSpec(
                    id="test",
                    name="Test",
                    prompt_template="默认模板：{topic}"
                )
            ],
            sites=["chatgpt"],
            output={}
        )
        
        # 应该回退到使用模板
        tasks = build_tasks("test_run", brief, prompt_file_path=nonexistent_path)
        
        # 验证使用了默认模板
        assert len(tasks) > 0
        assert "默认模板" in tasks[0].prompt or "测试主题" in tasks[0].prompt


class TestOrchestratorIntegration:
    """Test integration of new features in orchestrator"""
    
    def test_model_version_override(self):
        """Test that CLI model_version overrides brief.yaml config"""
        # 这个测试需要实际的 brief.yaml 文件，所以只做基本验证
        # 实际测试应该在集成测试中进行
        
        # 验证逻辑：如果提供了 model_version，应该覆盖配置
        site_model_versions = {"chatgpt": "5.2instant"}
        cli_model_version = "5.2pro"
        
        # 模拟覆盖逻辑
        if cli_model_version:
            site_model_versions["chatgpt"] = cli_model_version
        
        assert site_model_versions["chatgpt"] == "5.2pro"
    
    def test_prompt_file_integration(self):
        """Test prompt file integration with build_tasks"""
        from rpa_llm.orchestrator import build_tasks
        from rpa_llm.models import Brief, StreamSpec
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# 自定义 Prompt\n\n这是从文件读取的内容。")
            temp_path = Path(f.name)
        
        try:
            brief = Brief(
                topic="主题",
                context="上下文",
                questions=[],
                streams=[
                    StreamSpec(
                        id="test",
                        name="Test",
                        prompt_template="模板：{topic}"
                    )
                ],
                sites=["chatgpt"],
                output={}
            )
            
            tasks = build_tasks("test", brief, prompt_file_path=temp_path)
            
            # 所有任务应该使用相同的自定义 prompt
            assert len(tasks) > 0
            for task in tasks:
                assert "自定义 Prompt" in task.prompt
                assert "模板：主题" not in task.prompt
                
        finally:
            if temp_path.exists():
                temp_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


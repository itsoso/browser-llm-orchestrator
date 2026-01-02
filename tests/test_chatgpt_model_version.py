# -*- coding: utf-8 -*-
"""
单元测试：验证 ChatGPT adapter 的模型版本选择逻辑
"""
import os
import pytest
from unittest.mock import Mock, AsyncMock, patch
from rpa_llm.adapters.chatgpt import ChatGPTAdapter


class TestChatGPTModelVersion:
    """测试 ChatGPT 模型版本选择逻辑"""
    
    def setup_method(self):
        """每个测试方法前的设置"""
        # 清除环境变量
        if "CHATGPT_VARIANT" in os.environ:
            del os.environ["CHATGPT_VARIANT"]
    
    def test_desired_variant_5_2_instant(self):
        """测试 5.2instant 应该返回 custom"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        adapter._model_version = "5.2instant"
        
        result = adapter._desired_variant()
        assert result == "custom", f"Expected 'custom', got '{result}'"
    
    def test_desired_variant_5_2_pro(self):
        """测试 5.2pro 应该返回 pro"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        adapter._model_version = "5.2pro"
        
        result = adapter._desired_variant()
        assert result == "pro", f"Expected 'pro', got '{result}'"
    
    def test_desired_variant_instant(self):
        """测试 instant 应该返回 instant"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        adapter._model_version = "instant"
        
        result = adapter._desired_variant()
        assert result == "instant", f"Expected 'instant', got '{result}'"
    
    def test_desired_variant_thinking(self):
        """测试 thinking 应该返回 thinking"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        adapter._model_version = "thinking"
        
        result = adapter._desired_variant()
        assert result == "thinking", f"Expected 'thinking', got '{result}'"
    
    def test_desired_variant_pro(self):
        """测试 pro 应该返回 pro"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        adapter._model_version = "pro"
        
        result = adapter._desired_variant()
        assert result == "pro", f"Expected 'pro', got '{result}'"
    
    def test_desired_variant_env_5_2_instant(self):
        """测试环境变量 CHATGPT_VARIANT=5.2instant"""
        os.environ["CHATGPT_VARIANT"] = "5.2instant"
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        
        result = adapter._desired_variant()
        assert result == "custom", f"Expected 'custom', got '{result}'"
    
    def test_desired_variant_env_5_2_pro(self):
        """测试环境变量 CHATGPT_VARIANT=5.2pro"""
        os.environ["CHATGPT_VARIANT"] = "5.2pro"
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        
        result = adapter._desired_variant()
        assert result == "pro", f"Expected 'pro', got '{result}'"
    
    @pytest.mark.asyncio
    async def test_ensure_variant_5_2_instant(self):
        """测试 ensure_variant 处理 5.2instant 时应该打开模型选择器"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        
        # Mock page 和 locator
        mock_page = Mock()
        mock_btn = Mock()
        mock_locator = Mock()
        
        mock_page.locator.return_value.first = mock_locator
        mock_locator.count = AsyncMock(return_value=1)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.click = AsyncMock()
        
        adapter.page = mock_page
        
        # Mock _select_model_menu_item
        adapter._select_model_menu_item = AsyncMock(return_value=True)
        
        # 测试 5.2instant
        await adapter.ensure_variant(model_version="5.2instant")
        
        # 验证：应该打开模型选择器
        assert mock_page.locator.called, "应该调用 page.locator 查找模型选择器按钮"
        assert mock_locator.click.called, "应该点击模型选择器按钮"
        assert adapter._select_model_menu_item.called, "应该调用 _select_model_menu_item 选择模型"
        
        # 验证：传递的 pattern 应该匹配 Instant
        call_args = adapter._select_model_menu_item.call_args
        pattern = call_args[0][0]  # 第一个位置参数是 pattern
        model_version_arg = call_args[1].get("model_version")  # 关键字参数
        
        # 验证 pattern 能匹配 "5.2 Instant" 或类似文本
        test_texts = [
            "ChatGPT 5.2 Instant",
            "5.2 Instant",
            "GPT-5.2-Instant",
            "5.2即时",
        ]
        for text in test_texts:
            assert pattern.search(text), f"Pattern 应该匹配 '{text}'"
        
        assert model_version_arg == "5.2instant", f"应该传递 model_version='5.2instant', got '{model_version_arg}'"
    
    @pytest.mark.asyncio
    async def test_ensure_variant_5_2_pro(self):
        """测试 ensure_variant 处理 5.2pro 时应该打开模型选择器"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        
        # Mock page 和 locator
        mock_page = Mock()
        mock_btn = Mock()
        mock_locator = Mock()
        
        mock_page.locator.return_value.first = mock_locator
        mock_locator.count = AsyncMock(return_value=1)
        mock_locator.is_visible = AsyncMock(return_value=True)
        mock_locator.click = AsyncMock()
        
        adapter.page = mock_page
        
        # Mock _select_model_menu_item
        adapter._select_model_menu_item = AsyncMock(return_value=True)
        
        # 测试 5.2pro
        await adapter.ensure_variant(model_version="5.2pro")
        
        # 验证：应该打开模型选择器
        assert mock_page.locator.called, "应该调用 page.locator 查找模型选择器按钮"
        assert mock_locator.click.called, "应该点击模型选择器按钮"
        assert adapter._select_model_menu_item.called, "应该调用 _select_model_menu_item 选择模型"
        
        # 验证：传递的 pattern 应该匹配 Pro
        call_args = adapter._select_model_menu_item.call_args
        pattern = call_args[0][0]
        model_version_arg = call_args[1].get("model_version")
        
        # 验证 pattern 能匹配 "5.2 Pro" 或类似文本
        test_texts = [
            "ChatGPT 5.2 Pro",
            "5.2 Pro",
            "GPT-5.2-Pro",
            "5.2专业版",
        ]
        for text in test_texts:
            assert pattern.search(text), f"Pattern 应该匹配 '{text}'"
        
        assert model_version_arg == "5.2pro", f"应该传递 model_version='5.2pro', got '{model_version_arg}'"
    
    @pytest.mark.asyncio
    async def test_ensure_variant_instant_only(self):
        """测试 ensure_variant 处理单独的 instant 时应该只设置 toggle，不打开模型选择器"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        
        # Mock page
        mock_page = Mock()
        adapter.page = mock_page
        
        # Mock _set_thinking_toggle
        adapter._set_thinking_toggle = AsyncMock()
        
        # 测试 instant
        await adapter.ensure_variant(model_version="instant")
        
        # 验证：应该只设置 thinking toggle，不打开模型选择器
        assert adapter._set_thinking_toggle.called, "应该调用 _set_thinking_toggle"
        assert adapter._set_thinking_toggle.call_args[0][0] == False, "应该设置 want_thinking=False"
        assert not mock_page.locator.called, "不应该打开模型选择器"
    
    @pytest.mark.asyncio
    async def test_ensure_variant_thinking_only(self):
        """测试 ensure_variant 处理 thinking 时应该只设置 toggle，不打开模型选择器"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        
        # Mock page
        mock_page = Mock()
        adapter.page = mock_page
        
        # Mock _set_thinking_toggle
        adapter._set_thinking_toggle = AsyncMock()
        
        # 测试 thinking
        await adapter.ensure_variant(model_version="thinking")
        
        # 验证：应该只设置 thinking toggle，不打开模型选择器
        assert adapter._set_thinking_toggle.called, "应该调用 _set_thinking_toggle"
        assert adapter._set_thinking_toggle.call_args[0][0] == True, "应该设置 want_thinking=True"
        assert not mock_page.locator.called, "不应该打开模型选择器"
    
    def test_desired_variant_variations(self):
        """测试各种变体格式"""
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        
        test_cases = [
            ("5.2instant", "custom"),
            ("5.2-instant", "custom"),
            ("5-2-instant", "custom"),
            ("gpt-5.2-instant", "custom"),
            ("5.2pro", "pro"),
            ("5.2-pro", "pro"),
            ("5-2-pro", "pro"),
            ("gpt-5.2-pro", "pro"),
            ("instant", "instant"),
            ("thinking", "thinking"),
            ("pro", "pro"),
            ("GPT-5", "pro"),
            ("GPT-4o", "custom"),  # 未明确处理，返回 custom
        ]
        
        for model_version, expected in test_cases:
            adapter._model_version = model_version
            result = adapter._desired_variant()
            assert result == expected, f"model_version='{model_version}' 应该返回 '{expected}', 但返回了 '{result}'"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


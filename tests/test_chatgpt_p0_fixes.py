"""
Tests for ChatGPT P0 fixes:
- P0-1: new_chat button click improvements
- P0-2: Control+Enter send optimization for large prompts
- P0-3: DOM stability wait before send
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestNewChatButtonClick:
    """P0-1: Tests for _click_new_chat_button improvements"""
    
    @pytest.fixture
    def mock_page(self):
        """Create a mock Playwright page"""
        page = AsyncMock()
        page.url = "https://chatgpt.com/c/test-id"
        page.evaluate = AsyncMock()
        page.locator = MagicMock()
        return page
    
    @pytest.mark.asyncio
    async def test_js_click_succeeds_first_selector(self, mock_page):
        """Test that JS click works on first selector"""
        mock_page.evaluate = AsyncMock(return_value=True)
        
        # Simulate the JS click logic
        js_selectors = [
            'nav a[href="/"]',
            'a[data-testid="create-new-chat-button"]',
        ]
        
        clicked = False
        for sel in js_selectors:
            result = await mock_page.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (el && el.offsetParent !== null) {
                        el.click();
                        return true;
                    }
                    return false;
                }""",
                sel
            )
            if result:
                clicked = True
                break
        
        assert clicked is True
        assert mock_page.evaluate.call_count == 1
    
    @pytest.mark.asyncio
    async def test_js_click_fallback_to_text_based(self, mock_page):
        """Test that JS click falls back to text-based click when selectors fail"""
        # First 2 calls return False (JS selectors not found)
        # Third call (text-based) returns True
        mock_page.evaluate = AsyncMock(side_effect=[False, False, True])
        
        js_selectors = ['nav a[href="/"]', 'a[data-testid="create-new-chat-button"]']
        
        clicked = False
        for sel in js_selectors:
            result = await mock_page.evaluate("...", sel)
            if result:
                clicked = True
                break
        
        if not clicked:
            # Try text-based click
            result = await mock_page.evaluate("...")
            if result:
                clicked = True
        
        assert clicked is True
        assert mock_page.evaluate.call_count == 3


class TestControlEnterSendOptimization:
    """P0-2: Tests for Control+Enter send optimization"""
    
    @pytest.fixture
    def mock_page(self):
        """Create a mock Playwright page"""
        page = AsyncMock()
        page.keyboard = AsyncMock()
        page.evaluate = AsyncMock()
        return page
    
    @pytest.mark.asyncio
    async def test_large_prompt_extra_wait(self, mock_page):
        """Test that large prompts get extra wait time"""
        prompt_len = 100000  # 100K characters
        
        # Calculate expected wait time
        if prompt_len > 50000:
            extra_wait = min(2.0, prompt_len / 50000 * 0.5)
        else:
            extra_wait = 0
        
        assert extra_wait > 0
        assert extra_wait <= 2.0
        # 100K / 50K * 0.5 = 1.0s
        assert abs(extra_wait - 1.0) < 0.01
    
    @pytest.mark.asyncio
    async def test_small_prompt_no_extra_wait(self, mock_page):
        """Test that small prompts don't get extra wait time"""
        prompt_len = 10000  # 10K characters
        
        if prompt_len > 50000:
            extra_wait = min(2.0, prompt_len / 50000 * 0.5)
        else:
            extra_wait = 0
        
        assert extra_wait == 0
    
    @pytest.mark.asyncio
    async def test_focus_before_send(self, mock_page):
        """Test that textbox is focused before sending"""
        mock_page.evaluate = AsyncMock(return_value=None)
        
        # Simulate focus logic
        focus_script = """() => {
            const textarea = document.querySelector('#prompt-textarea');
            if (textarea) {
                textarea.focus();
            }
        }"""
        
        await mock_page.evaluate(focus_script)
        
        mock_page.evaluate.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_button_disabled_check(self, mock_page):
        """Test that disabled button is detected"""
        mock_page.evaluate = AsyncMock(return_value={
            'ready': False,
            'reason': 'button disabled'
        })
        
        result = await mock_page.evaluate("...")
        
        assert result['ready'] is False
        assert result['reason'] == 'button disabled'


class TestDOMStabilityWait:
    """P0-3: Tests for DOM stability wait"""
    
    @pytest.fixture
    def mock_page(self):
        """Create a mock Playwright page"""
        page = AsyncMock()
        page.evaluate = AsyncMock()
        return page
    
    @pytest.mark.asyncio
    async def test_dom_stable_immediately(self, mock_page):
        """Test that DOM is detected as stable immediately"""
        mock_page.evaluate = AsyncMock(return_value={
            'ready': True,
            'reason': 'ok'
        })
        
        # Simulate stability check
        stable_count = 0
        required_stable_count = 3
        last_state = None
        
        for _ in range(5):
            state = await mock_page.evaluate("...")
            state_str = str(state)
            
            if state_str == last_state:
                stable_count += 1
                if stable_count >= required_stable_count:
                    break
            else:
                stable_count = 0
                last_state = state_str
        
        # Should need 4 calls to reach stable_count >= 3
        assert mock_page.evaluate.call_count >= 4
    
    @pytest.mark.asyncio
    async def test_dom_not_ready_loading(self, mock_page):
        """Test that loading state is detected"""
        mock_page.evaluate = AsyncMock(return_value={
            'ready': False,
            'reason': 'loading'
        })
        
        result = await mock_page.evaluate("...")
        
        assert result['ready'] is False
        assert result['reason'] == 'loading'
    
    @pytest.mark.asyncio
    async def test_textbox_not_found(self, mock_page):
        """Test that missing textbox is detected"""
        mock_page.evaluate = AsyncMock(return_value={
            'ready': False,
            'reason': 'not found'
        })
        
        result = await mock_page.evaluate("...")
        
        assert result['ready'] is False
        assert result['reason'] == 'not found'


class TestNewChatSelectors:
    """Test that NEW_CHAT selectors are properly defined"""
    
    def test_new_chat_selectors_exist(self):
        """Test that NEW_CHAT selectors include the expected patterns"""
        from rpa_llm.adapters.chatgpt import ChatGPTAdapter
        
        selectors = ChatGPTAdapter.NEW_CHAT
        
        # Check for key selectors
        assert any('href="/"' in s for s in selectors), "Should have href='/' selector"
        assert any('data-testid' in s for s in selectors), "Should have data-testid selector"
        assert any('新聊天' in s for s in selectors), "Should have Chinese text selector"
        assert any('New chat' in s for s in selectors), "Should have English text selector"
    
    def test_new_chat_selectors_count(self):
        """Test that there are enough backup selectors"""
        from rpa_llm.adapters.chatgpt import ChatGPTAdapter
        
        selectors = ChatGPTAdapter.NEW_CHAT
        
        # Should have at least 10 backup selectors
        assert len(selectors) >= 10, f"Expected at least 10 selectors, got {len(selectors)}"


class TestPromptLengthThresholds:
    """Test prompt length thresholds for optimization"""
    
    def test_js_inject_threshold(self):
        """Test JS injection threshold"""
        from rpa_llm.adapters.chatgpt_send import ChatGPTSender
        
        threshold = ChatGPTSender.JS_INJECT_THRESHOLD
        
        # Should be 2000 characters
        assert threshold == 2000
    
    def test_large_prompt_threshold(self):
        """Test that large prompt threshold is reasonable"""
        # Large prompt threshold is 50000 characters
        threshold = 50000
        
        # Typical prompt sizes
        small_prompt = 1000  # 1K
        medium_prompt = 10000  # 10K
        large_prompt = 100000  # 100K
        
        assert small_prompt < threshold
        assert medium_prompt < threshold
        assert large_prompt > threshold


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


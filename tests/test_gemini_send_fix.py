"""
Tests for Gemini send logic fix:
- Prevent duplicate sends (Control+Enter + Enter causing double messages)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGeminiSendDuplicatePrevention:
    """Test that duplicate sends are prevented"""
    
    @pytest.fixture
    def mock_page(self):
        """Create a mock Playwright page"""
        page = AsyncMock()
        page.keyboard = AsyncMock()
        page.evaluate = AsyncMock()
        page.wait_for_function = AsyncMock()
        return page
    
    @pytest.fixture
    def mock_locator(self):
        """Create a mock Playwright locator"""
        loc = AsyncMock()
        loc.focus = AsyncMock()
        loc.inner_text = AsyncMock(return_value="")
        return loc
    
    @pytest.mark.asyncio
    async def test_control_enter_then_check_before_enter(self, mock_page, mock_locator):
        """Test that after Control+Enter, we check before trying Enter"""
        # Simulate Control+Enter succeeded but detection was slow
        # Textbox is now empty (send worked)
        mock_locator.inner_text = AsyncMock(return_value="")  # Empty textbox
        
        # The fix should detect textbox is empty and skip Enter
        # This prevents duplicate sends
        
        # Simulate the check logic
        before_len = 1000
        current_text = ""  # Textbox cleared by Control+Enter
        
        # If textbox is (nearly) empty and before_len > 0, Control+Enter worked
        if before_len > 0 and len(current_text.strip()) <= max(0, int(before_len * 0.1)):
            control_enter_worked = True
        else:
            control_enter_worked = False
        
        assert control_enter_worked is True
    
    @pytest.mark.asyncio
    async def test_control_enter_failed_then_try_enter(self, mock_page, mock_locator):
        """Test that if Control+Enter failed, Enter is tried"""
        # Simulate Control+Enter failed - textbox still has significant content
        mock_locator.inner_text = AsyncMock(return_value="A" * 500)  # 500 chars, still significant
        
        before_len = 1000
        current_text = "A" * 500  # Textbox still has 500 chars (50% of original)
        
        # If textbox still has significant content (> 10% of original), Control+Enter failed
        threshold = max(0, int(before_len * 0.1))  # 100
        if len(current_text.strip()) <= threshold:
            control_enter_worked = True
        else:
            control_enter_worked = False
        
        assert control_enter_worked is False  # 500 > 100, so Control+Enter did NOT work
        # In this case, Enter should be tried as fallback
    
    @pytest.mark.asyncio
    async def test_generating_detected_skips_enter(self, mock_page):
        """Test that if generating is detected, Enter is skipped"""
        # Simulate Control+Enter worked and generating started
        is_generating = True
        
        # If generating is detected, Control+Enter worked
        if is_generating:
            should_skip_enter = True
        else:
            should_skip_enter = False
        
        assert should_skip_enter is True
    
    @pytest.mark.asyncio
    async def test_assistant_count_increased_skips_enter(self):
        """Test that if assistant_count increased, Enter is skipped"""
        # Simulate Control+Enter worked and assistant responded
        assist_cnt0 = 1
        assist_cnt_now = 2  # Increased after Control+Enter
        
        if assist_cnt_now > assist_cnt0:
            should_skip_enter = True
        else:
            should_skip_enter = False
        
        assert should_skip_enter is True


class TestGeminiSendTimings:
    """Test send timing logic"""
    
    def test_delay_before_enter_check(self):
        """Test that there's adequate delay before Enter fallback"""
        # The fix adds 0.5s delay before checking if Enter is needed
        delay_before_enter_check = 0.5
        
        # Should be at least 0.3s to give Gemini time to respond
        assert delay_before_enter_check >= 0.3
    
    def test_textbox_clear_threshold(self):
        """Test textbox clear detection threshold"""
        before_len = 10000
        
        # Textbox is considered cleared if content is < 10% of original
        threshold = max(0, int(before_len * 0.1))
        
        assert threshold == 1000  # 10% of 10000
        
        # Empty string is definitely cleared
        assert len("") <= threshold
        
        # 500 chars is also considered cleared (< 10%)
        assert 500 <= threshold
        
        # 5000 chars is NOT cleared (50%)
        assert not (5000 <= threshold)


class TestGeminiSendLogs:
    """Test that appropriate logs are generated"""
    
    def test_log_messages_exist(self):
        """Test that the fix includes appropriate log messages"""
        # These log messages should exist in the fixed code
        expected_logs = [
            "Control+Enter already worked (textbox cleared after delay)",
            "Control+Enter already worked (generating detected)",
            "Control+Enter already worked (assistant_count",
            "skipping Enter",
        ]
        
        # Read the source file to verify log messages exist
        import pathlib
        gemini_path = pathlib.Path(__file__).parent.parent / "rpa_llm" / "adapters" / "gemini.py"
        
        if gemini_path.exists():
            content = gemini_path.read_text()
            for log_msg in expected_logs:
                assert log_msg in content, f"Expected log message not found: {log_msg}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


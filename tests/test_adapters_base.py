# -*- coding: utf-8 -*-
"""
Unit tests for base adapter functionality
"""
import pytest
from rpa_llm.adapters.base import SiteAdapter


class TestSiteAdapter:
    """Test SiteAdapter base class methods"""
    
    def test_clean_newlines(self):
        """Test clean_newlines static method"""
        # Test with newlines
        prompt_with_newlines = "Hello\nWorld\nTest"
        cleaned = SiteAdapter.clean_newlines(prompt_with_newlines)
        assert "\n" not in cleaned
        assert "Hello" in cleaned
        assert "World" in cleaned
        assert "Test" in cleaned
        
        # Test with carriage returns
        prompt_with_cr = "Hello\r\nWorld\rTest"
        cleaned = SiteAdapter.clean_newlines(prompt_with_cr)
        assert "\r" not in cleaned
        assert "\n" not in cleaned
        
        # Test with unicode newlines
        prompt_with_unicode = "Hello\u2028World\u2029Test"
        cleaned = SiteAdapter.clean_newlines(prompt_with_unicode)
        assert "\u2028" not in cleaned
        assert "\u2029" not in cleaned
        
        # Test with no newlines
        prompt_no_newlines = "Hello World Test"
        cleaned = SiteAdapter.clean_newlines(prompt_no_newlines)
        assert cleaned == prompt_no_newlines
        
        # Test with multiple spaces after cleaning
        prompt_multi_newlines = "Hello\n\n\nWorld"
        cleaned = SiteAdapter.clean_newlines(prompt_multi_newlines)
        assert "\n" not in cleaned
        # Multiple newlines should be replaced with single space
        assert "  " not in cleaned  # No double spaces
        
        # Test empty string
        assert SiteAdapter.clean_newlines("") == ""
        
        # Test None (should raise error)
        with pytest.raises((AttributeError, TypeError)):
            SiteAdapter.clean_newlines(None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


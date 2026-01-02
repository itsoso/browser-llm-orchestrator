# -*- coding: utf-8 -*-
"""
Unit tests for utility functions
"""
import pytest
from rpa_llm.utils import slugify, beijing_now_iso


class TestUtils:
    """Test utility functions"""
    
    def test_slugify(self):
        """Test slugify function"""
        # Basic test
        assert slugify("Hello World") == "hello-world"
        
        # Test with special characters
        assert slugify("Hello, World!") == "hello-world"
        
        # Test with Chinese characters
        assert slugify("你好世界") == "ni-hao-shi-jie"
        
        # Test with numbers
        assert slugify("Test 123") == "test-123"
        
        # Test with max_len
        assert len(slugify("This is a very long string", max_len=10)) <= 10
        
        # Test empty string
        assert slugify("") == ""
        
        # Test with only special characters
        assert slugify("!!!") == ""
        
    def test_beijing_now_iso(self):
        """Test beijing_now_iso function"""
        result = beijing_now_iso()
        # Should return ISO format string
        assert isinstance(result, str)
        assert "T" in result
        assert "+08:00" in result or "Z" in result or "-" in result
        # Should have date and time
        assert len(result) >= 19  # At least YYYY-MM-DDTHH:MM:SS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


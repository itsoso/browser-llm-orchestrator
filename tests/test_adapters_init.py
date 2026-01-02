# -*- coding: utf-8 -*-
"""
Unit tests for adapter factory
"""
import pytest
from rpa_llm.adapters import create_adapter


class TestAdapterFactory:
    """Test adapter factory function"""
    
    def test_create_adapter_chatgpt(self):
        """Test creating ChatGPT adapter"""
        adapter = create_adapter("chatgpt", page=None, profile_dir="test", artifacts_dir="test")
        assert adapter is not None
        assert adapter.site_id == "chatgpt"
        
    def test_create_adapter_gemini(self):
        """Test creating Gemini adapter"""
        adapter = create_adapter("gemini", page=None, profile_dir="test", artifacts_dir="test")
        assert adapter is not None
        assert adapter.site_id == "gemini"
        
    def test_create_adapter_grok(self):
        """Test creating Grok adapter"""
        adapter = create_adapter("grok", page=None, profile_dir="test", artifacts_dir="test")
        assert adapter is not None
        assert adapter.site_id == "grok"
        
    def test_create_adapter_perplexity(self):
        """Test creating Perplexity adapter"""
        adapter = create_adapter("perplexity", page=None, profile_dir="test", artifacts_dir="test")
        assert adapter is not None
        assert adapter.site_id == "perplexity"
        
    def test_create_adapter_qianwen(self):
        """Test creating Qianwen adapter"""
        adapter = create_adapter("qianwen", page=None, profile_dir="test", artifacts_dir="test")
        assert adapter is not None
        assert adapter.site_id == "qianwen"
        
    def test_create_adapter_invalid(self):
        """Test creating invalid adapter"""
        with pytest.raises(ValueError):
            create_adapter("invalid", page=None, profile_dir="test", artifacts_dir="test")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


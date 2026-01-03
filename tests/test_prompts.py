# -*- coding: utf-8 -*-
"""
Unit tests for prompt templates
"""
import pytest
from rpa_llm.prompts import build_dual_model_arbitration_prompt
from rpa_llm.models import Brief, ModelResult


class TestPrompts:
    """Test prompt building functions"""
    
    def test_build_dual_model_arbitration_prompt_basic(self):
        """Test building dual model arbitration prompt with basic inputs"""
        # Create a minimal brief
        brief = Brief(
            topic="Test Topic",
            context="Test Context",
            questions=["Question 1", "Question 2"],
            streams=[],
            sites=["chatgpt", "gemini"],
            output={},
        )
        
        # Create model results with proper ModelResult objects
        results = [
            ModelResult(
                run_id="test_run",
                site_id="chatgpt",
                stream_id="stream1",
                stream_name="Stream 1",
                topic="Test Topic",
                prompt="Test prompt",
                answer_text="ChatGPT Response",
                source_url="https://chatgpt.com",
                created_utc="2025-01-03T12:00:00Z",
                ok=True,
            ),
            ModelResult(
                run_id="test_run",
                site_id="gemini",
                stream_id="stream1",
                stream_name="Stream 1",
                topic="Test Topic",
                prompt="Test prompt",
                answer_text="Gemini Response",
                source_url="https://gemini.google.com",
                created_utc="2025-01-03T12:00:00Z",
                ok=True,
            ),
        ]
        
        prompt = build_dual_model_arbitration_prompt(brief, results)
        
        assert "Test Topic" in prompt
        # Prompt should contain something about both models
        assert len(prompt) > 100
    
    def test_build_dual_model_arbitration_prompt_with_empty_results(self):
        """Test building prompt with empty results"""
        brief = Brief(
            topic="Test Topic",
            context="Test Context",
            questions=[],
            streams=[],
            sites=[],
            output={},
        )
        
        # Empty results
        results = []
        
        prompt = build_dual_model_arbitration_prompt(brief, results)
        
        # Should still contain topic
        assert "Test Topic" in prompt
    
    def test_build_dual_model_arbitration_prompt_with_special_chars(self):
        """Test building prompt with special characters"""
        brief = Brief(
            topic="Test Topic with 中文 and émojis 🎉",
            context="Context with <html> & special chars",
            questions=["Question with 'quotes'?"],
            streams=[],
            sites=["chatgpt"],
            output={},
        )
        
        results = [
            ModelResult(
                run_id="test_run",
                site_id="chatgpt",
                stream_id="stream1",
                stream_name="Stream 1",
                topic="Test Topic",
                prompt="Test prompt",
                answer_text="Response with **markdown** and `code`",
                source_url="https://chatgpt.com",
                created_utc="2025-01-03T12:00:00Z",
                ok=True,
            ),
        ]
        
        prompt = build_dual_model_arbitration_prompt(brief, results)
        
        # Should handle special characters
        assert "中文" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

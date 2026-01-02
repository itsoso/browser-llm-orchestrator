# -*- coding: utf-8 -*-
"""
Unit tests for prompt templates
"""
import pytest
from rpa_llm.prompts import build_analysis_prompt, build_synthesis_prompt, build_final_prompt


class TestPrompts:
    """Test prompt building functions"""
    
    def test_build_analysis_prompt(self):
        """Test building analysis prompt"""
        topic = "Test Topic"
        context = "Test Context"
        questions = ["Question 1", "Question 2"]
        
        prompt = build_analysis_prompt(topic, context, questions)
        
        assert topic in prompt
        assert context in prompt
        assert "Question 1" in prompt
        assert "Question 2" in prompt
        
    def test_build_synthesis_prompt(self):
        """Test building synthesis prompt"""
        topic = "Test Topic"
        responses = {
            "chatgpt": "Response 1",
            "gemini": "Response 2"
        }
        
        prompt = build_synthesis_prompt(topic, responses)
        
        assert topic in prompt
        assert "Response 1" in prompt
        assert "Response 2" in prompt
        
    def test_build_final_prompt(self):
        """Test building final prompt"""
        topic = "Test Topic"
        synthesis = "Synthesis text"
        
        prompt = build_final_prompt(topic, synthesis)
        
        assert topic in prompt
        assert synthesis in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


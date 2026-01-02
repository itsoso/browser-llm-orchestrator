# -*- coding: utf-8 -*-
"""
Basic unit tests that don't require external dependencies
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_imports():
    """Test that all modules can be imported"""
    try:
        from rpa_llm.adapters.base import SiteAdapter
        from rpa_llm.adapters import create_adapter
        from rpa_llm.utils import slugify, beijing_now_iso
        from rpa_llm.prompts import build_dual_model_arbitration_prompt
        print("✓ All imports successful")
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def test_clean_newlines():
    """Test clean_newlines function"""
    from rpa_llm.adapters.base import SiteAdapter
    
    # Test with newlines
    prompt = "Hello\nWorld\nTest"
    cleaned = SiteAdapter.clean_newlines(prompt)
    assert "\n" not in cleaned, "Newlines should be removed"
    assert "Hello" in cleaned and "World" in cleaned and "Test" in cleaned
    print("✓ clean_newlines test passed")
    return True


def test_slugify():
    """Test slugify function"""
    from rpa_llm.utils import slugify
    
    result = slugify("Hello World")
    assert "hello" in result.lower() and "world" in result.lower()
    result2 = slugify("Test 123")
    assert "test" in result2.lower() and "123" in result2
    assert slugify("") == ""
    print("✓ slugify test passed")
    return True


def test_beijing_now_iso():
    """Test beijing_now_iso function"""
    from rpa_llm.utils import beijing_now_iso
    
    result = beijing_now_iso()
    assert isinstance(result, str)
    assert "T" in result
    assert len(result) >= 19
    print("✓ beijing_now_iso test passed")
    return True


def test_adapter_factory():
    """Test adapter factory"""
    from rpa_llm.adapters import create_adapter
    from pathlib import Path
    
    # Test creating adapters (check factory works)
    try:
        adapter = create_adapter("chatgpt", profile_dir=Path("test"), artifacts_dir=Path("test"))
        assert adapter.site_id == "chatgpt"
        
        adapter = create_adapter("gemini", profile_dir=Path("test"), artifacts_dir=Path("test"))
        assert adapter.site_id == "gemini"
        
        adapter = create_adapter("grok", profile_dir=Path("test"), artifacts_dir=Path("test"))
        assert adapter.site_id == "grok"
        
        adapter = create_adapter("perplexity", profile_dir=Path("test"), artifacts_dir=Path("test"))
        assert adapter.site_id == "perplexity"
        
        adapter = create_adapter("qianwen", profile_dir=Path("test"), artifacts_dir=Path("test"))
        assert adapter.site_id == "qianwen"
        
        print("✓ Adapter factory test passed")
        return True
    except Exception as e:
        print(f"✗ Adapter factory test failed: {e}")
        return False


def test_invalid_adapter():
    """Test invalid adapter"""
    from rpa_llm.adapters import create_adapter
    from pathlib import Path
    
    try:
        create_adapter("invalid", profile_dir=Path("test"), artifacts_dir=Path("test"))
        print("✗ Invalid adapter test failed: should raise ValueError")
        return False
    except ValueError:
        print("✓ Invalid adapter test passed")
        return True
    except Exception as e:
        print(f"✗ Invalid adapter test failed with unexpected error: {e}")
        return False


def run_all_tests():
    """Run all tests"""
    print("=" * 50)
    print("Running Basic Unit Tests")
    print("=" * 50)
    
    tests = [
        test_imports,
        test_clean_newlines,
        test_slugify,
        test_beijing_now_iso,
        test_adapter_factory,
        test_invalid_adapter,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} failed with exception: {e}")
            failed += 1
    
    print("=" * 50)
    print(f"Tests: {passed} passed, {failed} failed")
    print("=" * 50)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)


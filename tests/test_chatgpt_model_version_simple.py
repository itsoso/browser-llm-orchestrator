# -*- coding: utf-8 -*-
"""
简单测试脚本：验证 ChatGPT adapter 的模型版本选择逻辑
不依赖 pytest，可以直接运行
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from unittest.mock import Mock
from rpa_llm.adapters.chatgpt import ChatGPTAdapter


def test_desired_variant():
    """测试 _desired_variant 方法"""
    print("=" * 60)
    print("测试 _desired_variant 方法")
    print("=" * 60)
    
    # 清除环境变量
    if "CHATGPT_VARIANT" in os.environ:
        del os.environ["CHATGPT_VARIANT"]
    
    adapter = ChatGPTAdapter(
        profile_dir=Mock(),
        artifacts_dir=Mock(),
        headless=True,
        stealth=True
    )
    
    test_cases = [
        # (model_version, expected_variant, description)
        ("5.2instant", "custom", "5.2instant 应该返回 custom（需要打开模型选择器）"),
        ("5.2-instant", "custom", "5.2-instant 应该返回 custom"),
        ("5-2-instant", "custom", "5-2-instant 应该返回 custom"),
        ("5.2pro", "pro", "5.2pro 应该返回 pro"),
        ("5.2-pro", "pro", "5.2-pro 应该返回 pro"),
        ("5-2-pro", "pro", "5-2-pro 应该返回 pro"),
        ("gpt-5.2-pro", "pro", "gpt-5.2-pro 应该返回 pro"),
        ("instant", "instant", "instant 应该返回 instant（只需要设置 toggle）"),
        ("thinking", "thinking", "thinking 应该返回 thinking"),
        ("pro", "pro", "pro 应该返回 pro"),
        ("GPT-5", "pro", "GPT-5 应该返回 pro"),
    ]
    
    passed = 0
    failed = 0
    
    for model_version, expected, description in test_cases:
        adapter._model_version = model_version
        result = adapter._desired_variant()
        
        if result == expected:
            print(f"✓ {description}")
            print(f"  输入: {model_version} -> 输出: {result} (期望: {expected})")
            passed += 1
        else:
            print(f"✗ {description}")
            print(f"  输入: {model_version} -> 输出: {result} (期望: {expected})")
            failed += 1
        print()
    
    print(f"\n测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def test_desired_variant_env():
    """测试环境变量"""
    print("=" * 60)
    print("测试环境变量 CHATGPT_VARIANT")
    print("=" * 60)
    
    test_cases = [
        ("5.2instant", "custom"),
        ("5.2pro", "pro"),
        ("instant", "instant"),
        ("thinking", "thinking"),
    ]
    
    passed = 0
    failed = 0
    
    for env_value, expected in test_cases:
        os.environ["CHATGPT_VARIANT"] = env_value
        adapter = ChatGPTAdapter(
            profile_dir=Mock(),
            artifacts_dir=Mock(),
            headless=True,
            stealth=True
        )
        result = adapter._desired_variant()
        
        if result == expected:
            print(f"✓ CHATGPT_VARIANT={env_value} -> {result} (期望: {expected})")
            passed += 1
        else:
            print(f"✗ CHATGPT_VARIANT={env_value} -> {result} (期望: {expected})")
            failed += 1
        
        del os.environ["CHATGPT_VARIANT"]
    
    print(f"\n测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def test_pattern_matching():
    """测试正则表达式匹配模式"""
    print("=" * 60)
    print("测试正则表达式匹配模式")
    print("=" * 60)
    
    import re
    
    # 测试 5.2instant 的匹配模式
    pattern_instant = re.compile(r"5[.\-]?2.*instant|instant.*5[.\-]?2|5[.\-]?2.*即时|即时.*5[.\-]?2", re.I)
    
    test_texts_instant = [
        "ChatGPT 5.2 Instant",
        "5.2 Instant",
        "GPT-5.2-Instant",
        "5.2即时",
        "Instant 5.2",
    ]
    
    print("测试 5.2instant 匹配模式:")
    for text in test_texts_instant:
        match = pattern_instant.search(text)
        status = "✓" if match else "✗"
        print(f"  {status} '{text}' -> {'匹配' if match else '不匹配'}")
    
    # 测试 5.2pro 的匹配模式
    pattern_pro = re.compile(r"5[.\-]?2|gpt[.\-]?5|\bpro\b|专业|Professional", re.I)
    
    test_texts_pro = [
        "ChatGPT 5.2 Pro",
        "5.2 Pro",
        "GPT-5.2-Pro",
        "5.2专业版",
        "Professional",
    ]
    
    print("\n测试 5.2pro 匹配模式:")
    for text in test_texts_pro:
        match = pattern_pro.search(text)
        status = "✓" if match else "✗"
        print(f"  {status} '{text}' -> {'匹配' if match else '不匹配'}")
    
    print()
    return True


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("ChatGPT 模型版本选择逻辑测试")
    print("=" * 60 + "\n")
    
    results = []
    
    results.append(("_desired_variant", test_desired_variant()))
    results.append(("环境变量", test_desired_variant_env()))
    results.append(("正则匹配", test_pattern_matching()))
    
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"{name}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("✓ 所有测试通过！")
        return 0
    else:
        print("✗ 部分测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())


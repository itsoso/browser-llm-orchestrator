# -*- coding: utf-8 -*-
"""
独立测试脚本：只测试 ChatGPT adapter 的模型版本选择逻辑
不依赖 playwright 或其他外部库
"""
import os
import sys
import re


def desired_variant_logic(model_version=None, env_variant=None):
    """
    复制 _desired_variant 的逻辑用于测试
    不依赖 ChatGPTAdapter 类
    """
    # 优先使用 model_version 参数
    if model_version:
        v = model_version.strip().lower()
        # 关键修复：先检查完整的组合匹配，再检查部分匹配
        # 这样可以确保 "5.2instant" 不会被误判为 "pro"
        
        # 1. 检查完整的组合（优先级最高）
        if "5.2instant" in v or "5-2-instant" in v or "5.2-instant" in v:
            return "custom"  # 需要打开模型选择器选择 5.2 Instant
        if "5.2pro" in v or "5-2-pro" in v or "5.2-pro" in v or "gpt-5.2-pro" in v:
            return "pro"  # 需要打开模型选择器选择 5.2 Pro
        
        # 2. 检查部分匹配（通用匹配）
        if "thinking" in v:
            return "thinking"
        if "instant" in v:
            # 如果是单独的 "instant"，只需要设置 thinking toggle
            # 如果是 "5.2instant" 已经在上面处理了
            return "instant"
        if "pro" in v:
            return "pro"
        
        # 如果无法识别，返回 "custom" 让 ensure_variant 处理
        return "custom"
    
    # 使用环境变量
    v = (env_variant or "thinking").strip().lower()
    
    # 先检查精确匹配
    if v in ("instant", "thinking", "pro"):
        return v
    
    # 检查完整的组合
    if "5.2instant" in v or "5-2-instant" in v or "5.2-instant" in v:
        return "custom"
    if "5.2pro" in v or "5-2-pro" in v or "5.2-pro" in v or "gpt-5.2-pro" in v:
        return "pro"
    
    # 检查部分匹配（需要排除已处理的组合）
    if "5.2" in v:
        # 如果包含 5.2 但不包含 instant，默认是 pro
        if "instant" not in v:
            return "pro"
        # 如果包含 5.2 和 instant，已经在上面处理了
        return "custom"
    
    if "pro" in v and "thinking" not in v and "instant" not in v:
        return "pro"
    
    return "thinking"


def test_desired_variant():
    """测试 _desired_variant 逻辑"""
    print("=" * 60)
    print("测试 _desired_variant 逻辑")
    print("=" * 60)
    
    test_cases = [
        # (model_version, expected_variant, description)
        ("5.2instant", "custom", "5.2instant 应该返回 custom（需要打开模型选择器）"),
        ("5.2-instant", "custom", "5.2-instant 应该返回 custom"),
        ("5-2-instant", "custom", "5-2-instant 应该返回 custom"),
        ("gpt-5.2-instant", "custom", "gpt-5.2-instant 应该返回 custom"),
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
        result = desired_variant_logic(model_version=model_version)
        
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


def test_env_variant():
    """测试环境变量逻辑"""
    print("=" * 60)
    print("测试环境变量逻辑")
    print("=" * 60)
    
    test_cases = [
        ("5.2instant", "custom"),
        ("5.2pro", "pro"),
        ("instant", "instant"),
        ("thinking", "thinking"),
        ("pro", "pro"),
    ]
    
    passed = 0
    failed = 0
    
    for env_value, expected in test_cases:
        result = desired_variant_logic(env_variant=env_value)
        
        if result == expected:
            print(f"✓ CHATGPT_VARIANT={env_value} -> {result} (期望: {expected})")
            passed += 1
        else:
            print(f"✗ CHATGPT_VARIANT={env_value} -> {result} (期望: {expected})")
            failed += 1
    
    print(f"\n测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def test_pattern_matching():
    """测试正则表达式匹配模式"""
    print("=" * 60)
    print("测试正则表达式匹配模式")
    print("=" * 60)
    
    # 测试 5.2instant 的匹配模式
    pattern_instant = re.compile(r"5[.\-]?2.*instant|instant.*5[.\-]?2|5[.\-]?2.*即时|即时.*5[.\-]?2", re.I)
    
    test_texts_instant = [
        ("ChatGPT 5.2 Instant", True),
        ("5.2 Instant", True),
        ("GPT-5.2-Instant", True),
        ("5.2即时", True),
        ("Instant 5.2", True),
        ("ChatGPT 5.2 Pro", False),  # 不应该匹配
        ("5.2 Pro", False),  # 不应该匹配
    ]
    
    print("测试 5.2instant 匹配模式:")
    all_passed = True
    for text, should_match in test_texts_instant:
        match = pattern_instant.search(text)
        matched = match is not None
        if matched == should_match:
            status = "✓"
            print(f"  {status} '{text}' -> {'匹配' if matched else '不匹配'} (期望: {'匹配' if should_match else '不匹配'})")
        else:
            status = "✗"
            print(f"  {status} '{text}' -> {'匹配' if matched else '不匹配'} (期望: {'匹配' if should_match else '不匹配'})")
            all_passed = False
    
    # 测试 5.2pro 的匹配模式
    pattern_pro = re.compile(r"5[.\-]?2|gpt[.\-]?5|\bpro\b|专业|Professional", re.I)
    
    test_texts_pro = [
        ("ChatGPT 5.2 Pro", True),
        ("5.2 Pro", True),
        ("GPT-5.2-Pro", True),
        ("5.2专业版", True),
        ("Professional", True),
        ("ChatGPT 5.2 Instant", False),  # 不应该匹配（虽然有 5.2，但没有 pro）
        ("5.2 Instant", False),  # 不应该匹配
    ]
    
    print("\n测试 5.2pro 匹配模式:")
    for text, should_match in test_texts_pro:
        match = pattern_pro.search(text)
        matched = match is not None
        if matched == should_match:
            status = "✓"
            print(f"  {status} '{text}' -> {'匹配' if matched else '不匹配'} (期望: {'匹配' if should_match else '不匹配'})")
        else:
            status = "✗"
            print(f"  {status} '{text}' -> {'匹配' if matched else '不匹配'} (期望: {'匹配' if should_match else '不匹配'})")
            all_passed = False
    
    print()
    return all_passed


def test_priority_order():
    """测试优先级顺序：确保 5.2instant 不会被误判为 pro"""
    print("=" * 60)
    print("测试优先级顺序（关键测试）")
    print("=" * 60)
    
    # 关键测试：5.2instant 应该优先匹配 instant，而不是 pro
    test_cases = [
        ("5.2instant", "custom", "5.2instant 不应该被误判为 pro"),
        ("5.2-instant", "custom", "5.2-instant 不应该被误判为 pro"),
        ("5-2-instant", "custom", "5-2-instant 不应该被误判为 pro"),
    ]
    
    passed = 0
    failed = 0
    
    for model_version, expected, description in test_cases:
        result = desired_variant_logic(model_version=model_version)
        
        if result == expected:
            print(f"✓ {description}")
            print(f"  输入: {model_version} -> 输出: {result} (期望: {expected})")
            if result == "pro":
                print(f"  ⚠️  警告：返回了 'pro'，这会导致选择错误的模型！")
                failed += 1
            else:
                passed += 1
        else:
            print(f"✗ {description}")
            print(f"  输入: {model_version} -> 输出: {result} (期望: {expected})")
            failed += 1
        print()
    
    print(f"\n测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("ChatGPT 模型版本选择逻辑测试")
    print("=" * 60 + "\n")
    
    results = []
    
    results.append(("_desired_variant 逻辑", test_desired_variant()))
    results.append(("环境变量逻辑", test_env_variant()))
    results.append(("正则匹配", test_pattern_matching()))
    results.append(("优先级顺序（关键）", test_priority_order()))
    
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
        print("\n关键验证：")
        print("  - 5.2instant 正确返回 'custom'，不会被误判为 'pro'")
        print("  - 5.2pro 正确返回 'pro'")
        print("  - instant 正确返回 'instant'（只设置 toggle）")
        print("  - thinking 正确返回 'thinking'（只设置 toggle）")
        return 0
    else:
        print("✗ 部分测试失败")
        print("\n请检查逻辑，确保 5.2instant 不会被误判为 pro")
        return 1


if __name__ == "__main__":
    sys.exit(main())


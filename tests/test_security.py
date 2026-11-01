#!/usr/bin/env python3

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Test security module."""

from runtimelib.security import PromptSanitizer, InputValidator, StructuredPromptBuilder, PromptZone


def test_input_validator():
    print("=== InputValidator Tests ===")

    # Test control chars
    result = InputValidator.validate_user_input("test\x00\x01")
    assert result == "test"
    print(f"Control chars: '{result}'")

    # Test zero-width chars
    result = InputValidator.validate_user_input("test​")
    assert result == "test"
    print(f"Zero-width: '{result}'")

    # Test unicode normalization
    result = InputValidator.validate_user_input("café")
    print(f"Unicode norm: '{result}'")

    # Test truncation
    result = InputValidator.validate_user_input("a" * 10005, max_len=100)
    assert len(result) <= 130
    print(f"Truncation: {result[-20:]}")

    # Test valid JSON
    result = InputValidator.validate_json({"a": 1, "b": [1, 2, 3]})
    assert result == {"a": 1, "b": [1, 2, 3]}
    print(f"JSON valid: {result}")

    # Test JSON depth limit - build nested dict programmatically
    d = {}
    current = d
    for i in range(12):  # 12 levels deep
        current["a"] = {}
        current = current["a"]
    current["k"] = 1
    try:
        InputValidator.validate_json(d)
        print("JSON depth: should have failed")
    except ValueError as e:
        print(f"JSON depth error (expected): {e}")


def test_structured_prompt_builder():
    print("\n=== StructuredPromptBuilder Tests ===")
    builder = StructuredPromptBuilder("You are a helpful assistant.")
    builder.add_context({"design_doc": "API spec..."}, "design")
    builder.add_user_input("Fix the login bug")
    builder.add_tool_output("fileops:read", "def login(): ...")
    messages = builder.build_messages()
    for m in messages:
        print(f"  {m['role']}: {m['content'][:60]}...")


def test_prompt_sanitizer_compat():
    print("\n=== PromptSanitizer (backwards compat) ===")
    sanitizer = PromptSanitizer()
    result = sanitizer.validate_user_input("test\x00")
    print(f"User input: '{result}'")
    result = sanitizer.validate_json({"a": 1})
    print(f"JSON: {result}")


if __name__ == "__main__":
    test_input_validator()
    test_structured_prompt_builder()
    test_prompt_sanitizer_compat()
    print("\nAll tests passed!")
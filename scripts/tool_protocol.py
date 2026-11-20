#!/usr/bin/env python3
"""Strict, model-friendly JSON tool-call protocol for local GGUF agents.

The model must choose exactly one tool name from an enum. Legacy `name` calls
are accepted only when the value is one exact allow-listed name; pipe-joined
pseudo-enums are rejected with a repairable error.
"""
from __future__ import annotations
import json
from typing import Any

TOOL_SCHEMAS: dict[str, dict[str, tuple[type, bool]]] = {
    "list_files": {"pattern": (str, False)},
    "read_file": {"path": (str, True), "start_line": (int, False), "end_line": (int, False)},
    "search_code": {"query": (str, True), "pattern": (str, False), "limit": (int, False)},
    "find_symbol": {"symbol": (str, True), "pattern": (str, False)},
    "git_history": {"path": (str, False), "limit": (int, False)},
    "tool_inventory": {},
    "sandbox_run": {"suite": (str, False), "patch": (str, True)},
}


def validate_tool_call(item: Any) -> tuple[str | None, dict[str, Any], str | None]:
    if not isinstance(item, dict):
        return None, {}, "each tool_calls item must be a JSON object"
    # Canonical protocol; legacy `name` accepted only as a single exact enum.
    tool = item.get("tool", item.get("name"))
    args = item.get("arguments", item.get("args", {}))
    if not isinstance(tool, str) or tool not in TOOL_SCHEMAS:
        return None, {}, "tool must be exactly one of: " + ", ".join(TOOL_SCHEMAS)
    if not isinstance(args, dict):
        return None, {}, "arguments must be a JSON object"
    schema = TOOL_SCHEMAS[tool]
    unknown = sorted(set(args) - set(schema))
    if unknown:
        return None, {}, f"unknown argument(s) for {tool}: {', '.join(unknown)}"
    for key, (expected, required) in schema.items():
        if required and key not in args:
            return None, {}, f"missing required argument '{key}' for {tool}"
        if key in args:
            value = args[key]
            # bool is a subclass of int; reject it for integer fields.
            if expected is int and (not isinstance(value, int) or isinstance(value, bool)):
                return None, {}, f"argument '{key}' for {tool} must be an integer"
            if expected is str and not isinstance(value, str):
                return None, {}, f"argument '{key}' for {tool} must be a string"
    if tool == "sandbox_run" and args.get("suite", "auto") not in {"auto", "native", "python", "cmake", "node", "go", "rust", "shell"}:
        return None, {}, "sandbox_run.suite must be one of: auto, native, python, cmake, node, go, rust, shell"
    return tool, args, None


def parse_tool_calls(payload: dict[str, Any], allowed_tools: set[str] | None = None) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, str]]]:
    calls = payload.get("tool_calls", [])
    if calls is None:
        return [], []
    if not isinstance(calls, list):
        return [], [{"error": "tool_calls must be a JSON array"}]
    accepted: list[tuple[str, dict[str, Any]]] = []
    errors: list[dict[str, str]] = []
    for item in calls[:4]:
        tool, args, error = validate_tool_call(item)
        if not error and allowed_tools is not None and tool not in allowed_tools:
            error = f"tool '{tool}' is not available in this phase; allowed tools: {', '.join(sorted(allowed_tools))}"
        if error:
            errors.append({"error": error, "received": json.dumps(item, ensure_ascii=False)[:1000]})
        else:
            accepted.append((tool, args))  # type: ignore[arg-type]
    if len(calls) > 4:
        errors.append({"error": "maximum 4 tool calls per model turn; excess calls were ignored"})
    return accepted, errors

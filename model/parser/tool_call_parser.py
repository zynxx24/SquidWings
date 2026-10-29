"""
model.parser.tool_call_parser — Parse tool calls from responses.
"""

from __future__ import annotations

import json
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class ToolCallParser:
    """Parses and normalizes tool calls from model responses.

    Handles different tool call formats and validates arguments.
    """

    @staticmethod
    def parse(raw_tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parse raw tool calls into normalized format.

        Args:
            raw_tool_calls: Raw tool_calls from API response.

        Returns:
            list of dicts with 'id', 'name', 'arguments' keys.
        """
        parsed = []
        for call in raw_tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                function = {}
            args_str = function.get("arguments", "{}")

            if isinstance(args_str, str):
                try:
                    arguments = json.loads(args_str)
                except json.JSONDecodeError:
                    logger.warning(f"[tool_parser] Bad JSON args: {args_str[:100]}")
                    arguments = {"_raw": args_str}
            else:
                arguments = args_str

            parsed.append({
                "id": call.get("id") or "",
                "name": function.get("name") or "",
                "arguments": arguments,
            })

        return parsed

    @staticmethod
    def extract_text_tool_calls(text: str) -> list[dict[str, Any]]:
        """Extract tool calls embedded in text (e.g., DeepSeek DSML, inline tags).

        Supports:
        - DeepSeek DSML: ｜DSML｜｜ invoke name="..." ...
        - DeepSeek standard: <｜tool call begin｜>function:...
        - Markdown tool blocks: ```tool:<name> ...

        Args:
            text: Raw text content from model response.

        Returns:
            list of dicts with 'id', 'name', 'arguments', 'raw_match'.
        """
        import re
        calls: list[dict[str, Any]] = []

        # 1. DeepSeek DSML format
        dsml_pattern = re.compile(
            r"[<｜\s]*DSML[｜\s]*invoke\s+name=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)(?:</[<｜\s]*DSML[｜\s]*invoke>|$)",
            re.DOTALL,
        )
        param_pattern = re.compile(
            r"[<｜\s]*DSML[｜\s]*parameter\s+name=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</[<｜\s]*DSML[｜\s]*parameter>",
            re.DOTALL,
        )

        for m in dsml_pattern.finditer(text):
            tool_name = m.group(1).strip()
            body = m.group(2)
            args: dict[str, Any] = {}
            for pm in param_pattern.finditer(body):
                pname = pm.group(1).strip()
                pval = pm.group(2).strip()
                if pval.lower() == "true":
                    args[pname] = True
                elif pval.lower() == "false":
                    args[pname] = False
                else:
                    args[pname] = pval

            calls.append({
                "id": f"call_dsml_{len(calls) + 1}",
                "name": tool_name,
                "arguments": args,
                "raw_match": m.group(0),
            })

        # 2. DeepSeek standard tag format: <｜tool call begin｜>function:name\n{...}
        deepseek_fn_pattern = re.compile(
            r"<[｜|]tool call begin[｜|]>(?:function:)?(\w+)\s*\n(.*?)(?:<[｜|]tool call end[｜|]>|$)",
            re.DOTALL,
        )
        for m in deepseek_fn_pattern.finditer(text):
            name = m.group(1).strip()
            arg_str = m.group(2).strip()
            try:
                args = json.loads(arg_str)
            except Exception:
                args = {"command": arg_str} if name == "execute_command" else {"input": arg_str}

            calls.append({
                "id": f"call_ds_{len(calls) + 1}",
                "name": name,
                "arguments": args,
                "raw_match": m.group(0),
            })

        # 3. Markdown tool block format: ```tool:<name>\n{...}\n```
        md_tool_pattern = re.compile(
            r"```tool:([a-zA-Z0-9_\-]+)\s*\n(.*?)```",
            re.DOTALL,
        )
        for m in md_tool_pattern.finditer(text):
            name = m.group(1).strip()
            arg_str = m.group(2).strip()
            try:
                args = json.loads(arg_str)
            except Exception:
                args = {"command": arg_str} if name == "execute_command" else {"input": arg_str}

            calls.append({
                "id": f"call_md_{len(calls) + 1}",
                "name": name,
                "arguments": args,
                "raw_match": m.group(0),
            })

        return calls

    @staticmethod
    def clean_text_tool_calls(text: str) -> str:
        """Remove raw DSML and tool call markup from user-facing text."""
        import re

        # Remove DSML tags
        cleaned = re.sub(
            r"[<｜\s]*DSML[｜\s]*calls>.*?</[<｜\s]*DSML[｜\s]*calls>|"
            r"[<｜\s]*DSML[｜\s]*invoke[^>]*>.*?</[<｜\s]*DSML[｜\s]*invoke>(?:\s*</[<｜\s]*DSML[｜\s]*calls>)??",
            "",
            text,
            flags=re.DOTALL,
        )

        # Remove DeepSeek standard tool call tags
        cleaned = re.sub(
            r"<[｜|]tool call begin[｜|]>.*?<[｜|]tool call end[｜|]>|"
            r"<[｜|]tool calls begin[｜|]>.*?<[｜|]tool calls end[｜|]>",
            "",
            cleaned,
            flags=re.DOTALL,
        )

        # Remove ```tool:...``` blocks
        cleaned = re.sub(
            r"```tool:[a-zA-Z0-9_\-]+\s*\n.*?```",
            "",
            cleaned,
            flags=re.DOTALL,
        )

        # Clean trailing DSML fragments
        cleaned = re.sub(r"</[<｜\s]*DSML[｜\s]*calls>", "", cleaned)
        cleaned = re.sub(r"[<｜\s]*DSML[｜\s]*calls>", "", cleaned)

        return cleaned.strip()

    @staticmethod
    def build_tool_result_message(
        tool_call_id: str,
        result: str,
    ) -> dict[str, Any]:
        """Build a tool result message to send back to the model.

        Args:
            tool_call_id: The ID of the tool call.
            result: The result text from tool execution.

        Returns:
            dict: Message dict with role='tool'.
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }

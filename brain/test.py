"""Smoke test for continuation memory."""

from pathlib import Path
from tempfile import TemporaryDirectory

from brain.memory.continuation_memory import ContinuationMemory


def main() -> None:
    with TemporaryDirectory() as temporary_directory:
        memory = ContinuationMemory(Path(temporary_directory))
        response = """### File: app.py
```python
class Demo:
    def run(self):
        return
"""

        memory.analyze_and_save(
            response_text=response,
            model_id="test-model",
            is_truncated=True,
        )

        assert memory.has_code_buffer()
        context = memory.build_context(for_continue=True)
        assert "[CONTINUE_CODE]" in context
        assert "app.py" in context
        assert "class Demo" in context

        memory.clear_code_buffer()
        assert not memory.has_code_buffer()

    print("continuation memory: OK")


if __name__ == "__main__":
    main()

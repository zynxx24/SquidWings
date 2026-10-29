"""
tool.planning.flow.flow_templates — Pre-built flow templates.

Provides factory functions that return pre-configured FlowBuilders
for common workflows like code review, refactoring, and testing.
"""

from __future__ import annotations

from tool.planning.flow.flow_builder import ExecutionFlow, FlowBuilder, FlowStep


def code_review_flow(file_path: str) -> ExecutionFlow:
    """Create a code review flow.

    Steps: lint → type check → test → review summary.
    """
    steps = [
        FlowStep(
            task_id="lint",
            name="Run linter",
            tool_name="execute_command",
            tool_args={"command": f"ruff check {file_path}"},
        ),
        FlowStep(
            task_id="typecheck",
            name="Run type checker",
            tool_name="execute_command",
            tool_args={"command": f"mypy {file_path}"},
        ),
        FlowStep(
            task_id="test",
            name="Run tests",
            tool_name="execute_command",
            tool_args={"command": "pytest -x"},
            depends_on=["lint", "typecheck"],
        ),
        FlowStep(
            task_id="summary",
            name="Generate review summary",
            description="Summarize lint, type check, and test results",
            depends_on=["test"],
        ),
    ]
    return FlowBuilder().build(steps, flow_name="code_review")


def refactor_flow(file_path: str) -> ExecutionFlow:
    """Create a refactoring flow.

    Steps: backup → edit → lint → test → verify.
    """
    steps = [
        FlowStep(
            task_id="backup",
            name="Backup original",
            tool_name="backup_file",
            tool_args={"file_path": file_path},
        ),
        FlowStep(
            task_id="edit",
            name="Apply refactoring",
            tool_name="edit_file",
            tool_args={"file_path": file_path},
            depends_on=["backup"],
        ),
        FlowStep(
            task_id="lint",
            name="Lint check",
            tool_name="execute_command",
            tool_args={"command": f"ruff check {file_path}"},
            depends_on=["edit"],
        ),
        FlowStep(
            task_id="test",
            name="Run tests",
            tool_name="execute_command",
            tool_args={"command": "pytest -x"},
            depends_on=["edit"],
        ),
        FlowStep(
            task_id="verify",
            name="Verify changes",
            depends_on=["lint", "test"],
        ),
    ]
    return FlowBuilder().build(steps, flow_name="refactor")


def test_flow(test_path: str = "") -> ExecutionFlow:
    """Create a test execution flow.

    Steps: install deps → run tests → coverage report.
    """
    test_cmd = f"pytest {test_path}" if test_path else "pytest"
    steps = [
        FlowStep(
            task_id="deps",
            name="Install dependencies",
            tool_name="execute_command",
            tool_args={"command": "pip install -r requirements.txt"},
        ),
        FlowStep(
            task_id="test",
            name="Run tests",
            tool_name="execute_command",
            tool_args={"command": f"{test_cmd} --tb=short"},
            depends_on=["deps"],
        ),
        FlowStep(
            task_id="coverage",
            name="Coverage report",
            tool_name="execute_command",
            tool_args={"command": f"{test_cmd} --cov --cov-report=term"},
            depends_on=["deps"],
        ),
        FlowStep(
            task_id="report",
            name="Summarize results",
            depends_on=["test", "coverage"],
        ),
    ]
    return FlowBuilder().build(steps, flow_name="test")


def deploy_flow(target: str = "staging") -> ExecutionFlow:
    """Create a deployment flow.

    Steps: lint → test → build → deploy.
    """
    steps = [
        FlowStep(
            task_id="lint",
            name="Pre-deploy lint",
            tool_name="execute_command",
            tool_args={"command": "ruff check ."},
        ),
        FlowStep(
            task_id="test",
            name="Pre-deploy tests",
            tool_name="execute_command",
            tool_args={"command": "pytest -x"},
        ),
        FlowStep(
            task_id="build",
            name="Build artifacts",
            tool_name="execute_command",
            tool_args={"command": "python -m build"},
            depends_on=["lint", "test"],
        ),
        FlowStep(
            task_id="deploy",
            name=f"Deploy to {target}",
            description=f"Deploy built artifacts to {target}",
            depends_on=["build"],
        ),
    ]
    return FlowBuilder().build(steps, flow_name=f"deploy_{target}")

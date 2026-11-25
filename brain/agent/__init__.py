"""
agent — Orchestration & session management layer.

Phase 4 components:
- Orchestrator: The main brain that coordinates all layers
- SessionManager: Tracks sessions, history, model_usage, token_usage
- AgentLoop: Core agent loop for multi-turn tool calling
"""

from brain.agent.agent_loop import AgentLoop
from brain.agent.orchestrator import Orchestrator
from brain.agent.session_manager import SessionManager

__all__ = ["Orchestrator", "SessionManager", "AgentLoop"]

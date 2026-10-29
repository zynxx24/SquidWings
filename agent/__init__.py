"""
agent — Orchestration & session management layer.

Phase 4 components:
- Orchestrator: The main brain that coordinates all layers
- SessionManager: Tracks sessions, history, model_usage, token_usage
- AgentLoop: Core agent loop for multi-turn tool calling
"""

from agent.agent_loop import AgentLoop
from agent.orchestrator import Orchestrator
from agent.session_manager import SessionManager

__all__ = ["Orchestrator", "SessionManager", "AgentLoop"]

"""
agent.orchestrator — Top-level orchestration brain.

The main coordinator that ties all layers together:
- Intent classification
- Model routing
- Context assembly
- Agent loop (multi-turn tool calling)
- Session & memory management
- Health tracking
- Fallback handling
- Auto-continue on truncation

Usage:
    orchestrator = Orchestrator(settings, secrets)
    response = await orchestrator.process("buatkan kode sorting", session_id)
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from brain.classifier.continuation import continuation_hint, is_continuation_input
from brain.classifier.intent_classifier import IntentClassifier
from brain.classifier.llm_classifier import LLMIntentClassifier
from brain.context.context_assembler import ContextAssembler
from brain.max_token.max_token_calculator import MaxTokenCalculator
from brain.max_token.max_token_config import MaxTokenConfig
from brain.memory.agent_working_memory import AgentWorkingMemory
from brain.memory.context_window_manager import ContextWindowManager
from brain.memory.conversation_store import ConversationStore
from brain.memory.project_memory import ProjectMemory
from brain.prompt_must_think.base_think_prompt import ThinkPromptManager
from config.constants import TOKEN_BUFFER, ResponseStatus, TaskType
from config.secrets import get_secrets
from config.settings import Settings
from model.connection.api_client import APIClient
from model.connection.health_check import HealthChecker
from model.registry.model_info import ModelInfo
from model.registry.model_registry import ModelRegistry
from model.settings.fallback import FallbackStrategy
from model.settings.routing_type import Difficulty, RoutingType
from model.type.activity import ActivityHandler
from model.type.base_handler import BaseTypeHandler
from model.type.code import CodeHandler
from model.type.generate_img import GenerateImgHandler
from model.type.math import MathHandler
from model.type.multimodal import MultimodalHandler
from model.type.reason import ReasonHandler
from model.type.school_task import SchoolTaskHandler
from tool.execute.execute_code_intel import quality_tools
from tool.execute.execute_local_memory import user_profile
from tool.execute.execute_local_memory.session_distill import distill_session
from tool.register_tools import register_all_tools
from tool.registry.tool_registry import ToolRegistry
from utils.exceptions import RateLimitError
from utils.logger import get_logger
from utils.workspace_context import get_active_workspace

from .agent_loop import AgentLoop, LoopResult
from .harness import Harness, RunLimits, scaffold_for
from .harness.advisor import pick_advisor
from .harness.briefing import build_briefing, skill_guidance
from .harness.guards import tool_trace
from .harness.playbooks import record_run
from .harness.tiers import protocol_text
from .harness.tool_select import select_tools, text_tool_protocol
from .harness.verifier import changed_files
from .pipeline_executor import PipelineExecutor, PipelineResult
from .session_manager import SessionManager

logger = get_logger(__name__)

# The chain ends in "any registered model", which can be hundreds with many
# providers. A request should fail in reasonable time rather than try them all.
MAX_FALLBACK_ATTEMPTS = 6
_BILLING_COOLDOWN_SECONDS = 600.0
_RESEARCH_RE = re.compile(
    r"\b(?:terbaru|latest|berita|research|riset|cari di internet|search the web|web search)\b",
    re.IGNORECASE,
)


class EmptyResponseError(RuntimeError):
    """Raised when a model returns no usable content.

    Treated as a recoverable failure so the orchestrator advances to the next
    model in the fallback chain instead of returning a blank answer.
    """


# Handler registry (shared across all Orchestrator instances)
TASK_HANDLERS: dict[TaskType, BaseTypeHandler] = {
    TaskType.CODE: CodeHandler(),
    TaskType.MATH: MathHandler(),
    TaskType.ACTIVITY: ActivityHandler(),
    TaskType.SCHOOL_TASK: SchoolTaskHandler(),
    TaskType.GENERATE_IMG: GenerateImgHandler(),
    TaskType.REASON: ReasonHandler(),
    TaskType.MULTIMODAL: MultimodalHandler(),
}


@dataclass
class AgentResponse:
    """Unified response from the orchestrator.

    Attributes:
        content: Final text response.
        thinking: Model's thinking text (if any).
        status: Response status (success, error, fallback_used, etc.).
        model_used: Model ID that generated this response.
        task_type: Classified task type.
        difficulty: Classified difficulty.
        confidence: Classification confidence.
        prompt_tokens: Total prompt tokens used.
        completion_tokens: Total completion tokens used.
        latency_ms: Total pipeline latency.
        iterations: Number of agent loop iterations.
        tool_calls: Tool calls executed.
        fallback_used: Whether a fallback model was used.
        error_message: Error message if status is error.
        content_parts: Raw content parts (for streaming reconstruction).
        harness: Summary of what the execution harness did, for the UI.
    """
    content: str = ""
    thinking: str = ""
    status: ResponseStatus = ResponseStatus.SUCCESS
    model_used: str = ""
    task_type: str = ""
    difficulty: str = ""
    confidence: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    iterations: int = 1
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    fallback_used: bool = False
    error_message: str = ""
    content_parts: list[str] = field(default_factory=list)
    harness: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Orchestrator:
    """The main brain — coordinates all agent layers.

    Provides a single `process()` method that takes user input and
    returns a complete AgentResponse after running through:
    1. Intent classification
    2. Model routing (with health-aware fallback)
    3. Context assembly
    4. Agent loop (with tool calling)
    5. Memory storage
    6. Health tracking

    Usage:
        orch = Orchestrator.from_settings(settings)
        response = await orch.process("buat REST API", session_id="sess-abc123")
    """

    def __init__(
        self,
        api_client: APIClient,
        registry: ModelRegistry,
        classifier: IntentClassifier,
        router: RoutingType,
        context_assembler: ContextAssembler,
        conversation_store: ConversationStore,
        session_manager: SessionManager,
        health_checker: HealthChecker,
        fallback: FallbackStrategy,
        tool_registry: ToolRegistry | None = None,
        stream_enabled: bool = True,
    ) -> None:
        """Initialize the Orchestrator.

        Args:
            api_client: API client for model calls.
            registry: Model registry.
            classifier: Intent classifier.
            router: Model router.
            context_assembler: Context assembler.
            conversation_store: Conversation store.
            session_manager: Session manager.
            health_checker: Health checker.
            fallback: Fallback strategy.
            tool_registry: Tool registry (optional).
            stream_enabled: Whether streaming is enabled.
        """
        self._api_client = api_client
        self._registry = registry
        self._classifier = classifier
        self._router = router
        self._context_assembler = context_assembler
        self._conversation_store = conversation_store
        self._session_manager = session_manager
        self._health_checker = health_checker
        self._fallback = fallback
        self._tool_registry = tool_registry or ToolRegistry()
        self._stream_enabled = stream_enabled
        self._context_window = ContextWindowManager()
        self._max_token_config = MaxTokenConfig()
        # Pipeline executor + working memory (PLAN→SEARCH→COMPILE→EXECUTE)
        self._working_memory = AgentWorkingMemory(workspace_path=".")
        self._pipeline = PipelineExecutor(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            working_memory=self._working_memory,
        )
        # Turn counters for memory distillation (writes at most once per N turns).
        self._distill_every = 3
        self._turn_counts: dict[str, int] = {}
        self._background: set[asyncio.Task[Any]] = set()

    def _refresh_graph_later(self, workspace: str) -> None:
        """Update this session's code graph without making the user wait for it."""
        task = asyncio.create_task(asyncio.to_thread(quality_tools.refresh_graph, workspace))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _distill_memory(self, session_id: str) -> None:
        """Keep the vault session note in sync with the chat log.

        Runs at most once every N turns: the note is long-term memory, not a
        live transcript, and rewriting it per turn would churn the vault index.
        Failures are swallowed - memory enrichment must never break a turn.
        """
        try:
            count = self._turn_counts.get(session_id, 0) + 1
            self._turn_counts[session_id] = count
            if count % self._distill_every != 0:
                return
            await distill_session(self._conversation_store, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[orchestrator] distill skipped: {exc}")

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        api_client: APIClient | None = None,
    ) -> Orchestrator:
        """Create an Orchestrator from application settings.

        Convenience factory that initializes all components.

        Args:
            settings: Application settings.
            api_client: Optional pre-configured API client.

        Returns:
            Fully configured Orchestrator.
        """
        secrets = get_secrets()

        # Initialize core components
        ModelRegistry.reset_singleton()
        registry = ModelRegistry()
        registry.load_from_config(settings.models_config_path)

        client = api_client or APIClient(
            settings.api_base_url,
            secrets.api_key,
            int(settings.timeout_seconds),
        )
        max_token_config = MaxTokenConfig(settings.models_config_path)
        health_checker = HealthChecker()

        def cheapest_model() -> str | None:
            all_models = registry.get_all()
            healthy_ids = health_checker.filter_healthy([m.id for m in all_models])
            candidates = [m for m in all_models if m.id in healthy_ids]
            if not candidates:
                candidates = all_models
            candidates = sorted(
                candidates,
                key=lambda item: (not item.is_free, item.max_output or 8192),
            )
            return candidates[0].id if candidates else None

        classifier = IntentClassifier(
            llm_classifier=LLMIntentClassifier(client, model=cheapest_model)
        )
        router = RoutingType(config_path=settings.models_config_path, registry=registry)
        conversation_store = ConversationStore()
        think_prompt_mgr = ThinkPromptManager()
        context_assembler = ContextAssembler(
            conversation_store=conversation_store,
            think_prompt_manager=think_prompt_mgr,
            max_token_calculator=MaxTokenCalculator(config=max_token_config),
        )
        session_manager = SessionManager()
        fallback = FallbackStrategy(
            config_path=settings.models_config_path, registry=registry
        )

        return cls(
            api_client=client,
            registry=registry,
            classifier=classifier,
            router=router,
            context_assembler=context_assembler,
            conversation_store=conversation_store,
            session_manager=session_manager,
            health_checker=health_checker,
            fallback=fallback,
            tool_registry=register_all_tools(),
            stream_enabled=settings.stream_enabled,
        )

    async def process(
        self,
        user_input: str,
        session_id: str,
        override_model: str | None = None,
        stream: bool | None = None,
        on_content_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        workspace_context: str | None = None,
        _fallback_depth: int = 0,
        images: list[str] | None = None,
        classification: Any = None,
        difficulty: str | None = None,
        on_reset: Callable[[], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        task_hint: str | None = None,
        persist: bool = True,
        limits: RunLimits | None = None,
    ) -> AgentResponse:
        """Process a user input through the full pipeline.

        This is the main entry point. Pipeline:
        1. Classify intent + difficulty
        2. Get handler
        3. Route to model (or use override)
        4. Assemble context
        5. Run agent loop (with tool calls)
        6. Auto-continue on truncation
        7. Store in memory
        8. Record health + session usage

        Args:
            user_input: The user's message.
            session_id: Session identifier.
            override_model: If set, skip routing and use this model.
            stream: Whether to stream (None = use default).
            on_content_chunk: Callback for streaming content chunks.
            on_thinking_chunk: Callback for streaming thinking chunks.
            workspace_context: Optional workspace context to prepend.
            _fallback_depth: Fallback recursion depth.
            images: List of attached image data URLs.
            classification: Precomputed classification result (optional).
            difficulty: Precomputed difficulty string (optional).
            on_reset: Called when text already streamed must be discarded.
            on_event: Progress callback (tool calls, verification, escalation).
            task_hint: The underlying task, when user_input is a role prompt for
                one team agent. Drives intent, tool choice, and memory, which a
                prompt like "You are a Researcher..." would otherwise mislead.
            limits: Time and token caps, for runs that share a budget.
            persist: Keep this turn in chat history and session notes. Off for
                team agents, whose context is passed explicitly.

        Returns:
            AgentResponse with the full result.
        """
        use_stream = stream if stream is not None else self._stream_enabled
        start_time = time.time()

        # A top-level request starts a fresh fallback chain. Nested fallback
        # attempts share the chain owned by the outer loop.
        if _fallback_depth == 0:
            self._fallback.reset_session(session_id)

        # Ensure session exists
        self._session_manager.get_or_create_session(session_id)
        self._session_manager.increment_turn(session_id)

        intent_text = task_hint or user_input
        model = None
        harness: Harness | None = None
        project: ProjectMemory | None = None
        workspace = get_active_workspace() or os.getenv("SQUIDWINGS_WORKSPACE", "").strip()
        streamed = False
        is_continue_turn = is_continuation_input(user_input)

        # Wrapped so a fallback only resets the UI when there is something to clear.
        if on_content_chunk is not None:
            forward_content = on_content_chunk

            def track_content(chunk: str) -> None:
                nonlocal streamed
                streamed = True
                forward_content(chunk)

            on_content_chunk = track_content

        try:
            # 1. Classify intent + difficulty.
            # Use precomputed classification if provided, or fast rule-based
            # classification when override_model is active (avoids redundant extra LLM calls).
            if classification is not None and difficulty is not None:
                pass
            elif override_model:
                classification = self._classifier.classify(intent_text)
                difficulty = IntentClassifier.estimate_difficulty(intent_text)
            else:
                async_classify = getattr(
                    self._classifier, "classify_async_with_difficulty", None
                )
                if callable(async_classify):
                    classification, difficulty = await async_classify(intent_text)
                else:
                    classification = self._classifier.classify(intent_text)
                    difficulty = IntentClassifier.estimate_difficulty(intent_text)
            diff_enum = Difficulty(difficulty)

            logger.info(
                f"[orchestrator] Classified: {classification.task_type.value}/"
                f"{difficulty} (conf={classification.confidence:.2f})"
            )

            # 2. Get handler
            handler = TASK_HANDLERS.get(
                classification.task_type, TASK_HANDLERS[TaskType.ACTIVITY]
            )

            # 3. Route to model. A bare "lanjutkan" carries no intent of its
            # own, so normal routing would answer it with whatever bucket the
            # low-confidence classification points at and drop the thread of
            # the truncated work. Continuation turns resume the model that
            # produced the previous answer instead.
            resume_model_id = (
                self._conversation_store.get_last_used_model(session_id)
                if is_continue_turn
                else None
            )
            model = None
            if resume_model_id and not override_model:
                model = self._registry.get(resume_model_id)
                if model:
                    logger.info(
                        f"[orchestrator] Continuation turn → reuse {model.id}"
                    )
                else:
                    logger.warning(
                        f"[orchestrator] Continuation model '{resume_model_id}' "
                        f"not in registry; falling back to routing"
                    )
            if model is None:
                model = self._select_model(
                    classification.task_type, diff_enum, override_model
                )
            if not model:
                return AgentResponse(
                    content="Error: No model available for this request.",
                    status=ResponseStatus.ERROR,
                    error_message="No model available",
                    task_type=classification.task_type.value,
                    difficulty=difficulty,
                    confidence=classification.confidence,
                    latency_ms=(time.time() - start_time) * 1000,
                )

            if on_event:
                on_event({
                    "type": "route",
                    "task_type": classification.task_type.value,
                    "difficulty": difficulty,
                    "confidence": classification.confidence,
                    "model": model.id,
                    "text": f"Using {model.id}",
                })

            if persist and task_hint is None and _fallback_depth == 0:
                saved = await asyncio.to_thread(user_profile.capture_from_message, user_input)
                if saved and on_event:
                    on_event({"type": "remembered", "items": saved, "text": "Remembered"})

            # 4. Build augmented input with project memory and workspace context.
            # Team agents get their context explicitly and share no chat session.
            project = ProjectMemory.for_path(workspace) if persist else None
            augmented_input = user_input
            if project:
                augmented_input = await asyncio.to_thread(
                    project.build_input, user_input, is_continue_turn
                )
            elif is_continue_turn:
                # A continuation nudge is too thin to stand alone: state the resume
                # intent explicitly so the model does not ask "lanjut apa?".
                augmented_input = f"{user_input}\n\n{continuation_hint()}"
            if workspace_context:
                augmented_input = f"{workspace_context}\n\n[USER REQUEST]\n{augmented_input}"

            # 5. Assemble context
            # Token counting is CPU work; off the loop so parallel agents keep moving.
            ctx = await asyncio.to_thread(
                self._context_assembler.assemble,
                handler=handler,
                user_input=augmented_input,
                session_id=session_id,
                model_id=model.id,
                context_window=model.context_window,
                model_max_output=model.max_output,
            )

            # 5a. L1 vault memory as an uncached block after the cached prefix
            # (same behaviour as the CLI in main.py).
            self._inject_vault_memory(ctx.messages)

            # 5b. Attach images (data URLs) to the final user message
            if images and not getattr(model, "supports_vision", True):
                logger.warning(f"[images] {model.id} has no vision support; images dropped")
            elif images:
                self._attach_images(ctx.messages, images)

            # 6. Prepare request params
            params = handler.prepare_request_params(model)
            temperature = params.get("temperature", 0.5)
            max_tokens = ctx.max_output_tokens
            # Reasoning models spend max_tokens on thinking first; honour the
            # handler's headroom, but never beyond the remaining window room.
            handler_max = int(params.get("max_tokens") or 0)
            if handler_max > max_tokens:
                window = model.context_window
                if isinstance(window, int) and window > 0:
                    room = window - ctx.estimated_tokens - TOKEN_BUFFER
                    handler_max = min(handler_max, max(max_tokens, room))
                max_tokens = handler_max

            # 7. Run agent loop (via Pipeline or direct AgentLoop)
            use_pipeline = task_hint is None and self._should_use_pipeline(
                user_input, classification.task_type, difficulty
            )
            harness = await self._build_harness(
                model, classification.task_type, intent_text, ctx.messages, on_reset, on_event, limits
            )

            if use_pipeline:
                logger.info("[orchestrator] Using PipelineExecutor (PLAN→SEARCH→COMPILE→EXECUTE)")
                pipe_result = await self._pipeline.run(
                    user_input=augmented_input,
                    session_id=session_id,
                    turn_id=session_id + "_" + str(int(time.time())),
                    model_id=model.id,
                    base_messages=ctx.messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=use_stream,
                    on_content_chunk=on_content_chunk,
                    on_thinking_chunk=on_thinking_chunk,
                    harness=harness,
                )
                # Wrap PipelineResult into LoopResult-compatible shape
                loop_result = self._pipe_to_loop_result(pipe_result)
            else:
                agent_loop = AgentLoop(
                    api_client=self._api_client,
                    tool_registry=self._tool_registry,
                    harness=harness,
                )
                loop_result = await agent_loop.run(
                    model_id=model.id,
                    messages=ctx.messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=use_stream,
                    on_content_chunk=on_content_chunk,
                    on_thinking_chunk=on_thinking_chunk,
                )

            # 8. Auto-continue on truncation
            final_content = loop_result.content
            if not final_content.strip() and loop_result.tool_calls_history:
                # The work happened. Falling back would run those tools again.
                final_content = (
                    f"*(Selesai menjalankan {len(loop_result.tool_calls_history)} tool; "
                    "model tidak menulis ringkasan.)*"
                )
                if on_content_chunk:
                    on_content_chunk(final_content)
            if loop_result.finish_reason == "length" and final_content.strip():
                continued = await self._auto_continue(
                    model_id=model.id,
                    base_messages=ctx.messages,
                    partial_content=final_content,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=use_stream,
                    on_content_chunk=on_content_chunk,
                    loop_result=loop_result,
                    context_window=model.context_window,
                )
                final_content = continued

            # 9. Post-process via handler
            final_parsed = loop_result.parsed_response
            if final_parsed:
                # Carries the continuation rounds, which post_process would
                # otherwise replace with only the first, cut-off part.
                final_parsed.content = final_content
                final_parsed = handler.post_process(final_parsed)
                if final_parsed.content:
                    final_content = final_parsed.content

            if loop_result.notes:
                final_content = f"{final_content}{loop_result.notes}"
                if on_content_chunk:
                    on_content_chunk(loop_result.notes)

            # 9b. A blank answer is a failure, not a success. Bansos mode must
            # keep advancing through the chain instead of returning nothing.
            if not final_content.strip():
                raise EmptyResponseError(
                    f"Model '{model.id}' returned empty content"
                )

            # 10. Store in memory
            if persist:
                self._conversation_store.add_message(
                    session_id, "user", user_input
                )
                self._conversation_store.add_message(
                    session_id, "assistant", final_content,
                    model_used=loop_result.model or model.id,
                    token_count=loop_result.total_completion_tokens,
                    tool_trace=tool_trace(loop_result.tool_calls_history),
                )
                await self._distill_memory(session_id)
            if project:
                await asyncio.to_thread(
                    project.finish_turn,
                    final_content,
                    loop_result.model or model.id,
                    changed_files(loop_result.tool_calls_history, workspace),
                    loop_result.finish_reason == "length",
                )
            touched = changed_files(loop_result.tool_calls_history, workspace)
            if workspace and touched:
                self._refresh_graph_later(workspace)
            if harness and not harness.stats.unresolved:
                harness.stats.playbook_saved = await record_run(
                    intent_text,
                    classification.task_type.value,
                    loop_result.model or model.id,
                    loop_result.tool_calls_history,
                )

            # 11. Record health + session usage
            latency_ms = (time.time() - start_time) * 1000
            self._health_checker.record_success(model.id, latency_ms)
            self._session_manager.record_model_usage(
                session_id=session_id,
                model_id=model.id,
                prompt_tokens=loop_result.total_prompt_tokens,
                completion_tokens=loop_result.total_completion_tokens,
                latency_ms=latency_ms,
                task_type=classification.task_type.value,
                success=True,
            )

            return AgentResponse(
                content=final_content,
                thinking=loop_result.thinking,
                status=ResponseStatus.SUCCESS,
                model_used=loop_result.model or model.id,
                task_type=classification.task_type.value,
                difficulty=difficulty,
                confidence=classification.confidence,
                prompt_tokens=loop_result.total_prompt_tokens,
                completion_tokens=loop_result.total_completion_tokens,
                latency_ms=latency_ms,
                iterations=loop_result.iterations,
                tool_calls=loop_result.tool_calls_history,
                content_parts=loop_result.content_parts,
                harness=harness.stats.to_dict() if harness else {},
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            msg = str(e)
            lowered = msg.lower()
            is_model_specific = (
                "not allowed to use model" in lowered
                or "model not found" in lowered
                or "model is not available" in lowered
            )
            logger.error(f"[orchestrator] Pipeline error: {e}")
            if project and model is not None:
                project.record_error(model.id, msg)
            if model is not None:
                # Feed failures back so routing can skip unhealthy models.
                self._health_checker.record_failure(model.id)
                if is_model_specific:
                    self._health_checker.mark_unavailable(model.id)
                elif isinstance(e, RateLimitError) and (e.retry_after or 0) > 30.0:
                    # The API client refuses to wait this long. Remember it, or
                    # the next request hits the same 429 before falling back.
                    self._health_checker.mark_unavailable(
                        model.id, min(float(e.retry_after or 0), 3600.0)
                    )

            # Billing failures (HTTP 402, spent budget) affect every model on
            # the provider, so the fallback chain would only burn time. Rate
            # limits and invalid-model errors are per model and do fall back.
            status_code = getattr(e, "status_code", 0)
            is_billing_error = (not is_model_specific) and (
                status_code == 402
                or re.search(r"\b402\b", lowered) is not None
                or any(
                    token in lowered
                    for token in ("billing_error", "token budget exhausted", "top up at")
                )
            )
            billing_response = AgentResponse(
                content=(
                    "Model request failed: the API key's token budget is "
                    "exhausted (HTTP 402). Top up your balance at the "
                    "provider's billing page or switch to another model "
                    "in the model selector, then retry."
                ),
                status=ResponseStatus.ERROR,
                error_message=msg,
                latency_ms=latency_ms,
            )
            has_other_provider = is_billing_error and self._cool_down_provider(model)
            if is_billing_error and _fallback_depth == 0 and not has_other_provider:
                logger.warning(
                    "[orchestrator] Billing/quota failure detected; "
                    "skipping fallback chain"
                )
                return billing_response

            # Nested attempts must not recurse: the outer fallback loop owns
            # the chain and advances to the next model itself.
            if _fallback_depth > 0:
                return AgentResponse(
                    content=f"Error: {e}",
                    status=ResponseStatus.ERROR,
                    error_message=str(e),
                    latency_ms=latency_ms,
                )

            # Text from the failed model must not run into the replacement answer.
            if streamed and on_reset:
                on_reset()

            fallback_response = await self._try_fallback(
                user_input=user_input,
                session_id=session_id,
                task_type=(
                    classification.task_type
                    if classification is not None
                    else TaskType.ACTIVITY
                ),
                failed_model_id=model.id if model is not None else "",
                on_content_chunk=on_content_chunk,
                on_thinking_chunk=on_thinking_chunk,
                workspace_context=workspace_context,
                stream=stream,
                images=images,
                on_reset=on_reset,
                on_event=on_event,
                task_hint=task_hint,
                persist=persist,
                limits=limits,
            )
            if fallback_response:
                fallback_response.latency_ms = latency_ms
                return fallback_response
            if is_billing_error:
                return billing_response

            return AgentResponse(
                content=f"Error: {e}",
                status=ResponseStatus.ERROR,
                error_message=str(e),
                latency_ms=latency_ms,
            )
        finally:
            if _fallback_depth == 0:
                self._fallback.reset_session(session_id)
                if not persist:
                    # One session per agent per run: these would accumulate forever.
                    self._session_manager.delete_session(session_id)

    def _select_model(
        self,
        task_type: TaskType,
        difficulty: Difficulty,
        override_model: str | None = None,
    ) -> ModelInfo | None:
        """Select the best model for this request.

        Args:
            task_type: Classified task type.
            difficulty: Task difficulty.
            override_model: Optional model override.

        Returns:
            ModelInfo or None.
        """
        if override_model:
            model = self._registry.get(override_model)
            if model and self._health_checker.is_available(model.id):
                logger.info(f"[orchestrator] Using override model: {model.id}")
                return model
            logger.warning(
                f"[orchestrator] Override model '{override_model}' not available, "
                f"falling back to routing"
            )

        # Supply health data when available so the router can skip models
        # that are currently failing instead of retrying them blindly.
        healthy_models: list[str] | None = None
        try:
            all_ids = [item.id for item in self._registry.get_all()]
            healthy_models = self._health_checker.filter_healthy(all_ids)
            if not healthy_models:
                healthy_models = None
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"[orchestrator] Health lookup failed: {exc}")

        model = self._router.route(
            task_type, difficulty, healthy_models=healthy_models
        )
        if model:
            logger.info(
                f"[orchestrator] Routed {task_type.value}/{difficulty.value} → {model.id}"
            )
        return model


    # ──────────────────────────────────────────────────────────────────────────
    # Pipeline helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _inject_vault_memory(messages: list[dict[str, Any]]) -> None:
        """Append L1 vault memory to the system message without breaking the cache."""
        from tool.execute.execute_local_memory.vault_context import (
            build_context,
            build_global_context,
        )

        text = build_context() + build_global_context() + user_profile.prompt_block()
        if text:
            Orchestrator._append_system(messages, text)

    @staticmethod
    def _attach_images(messages: list[dict[str, Any]], images: list[str]) -> None:
        """Convert the last user message into multimodal parts, in place."""
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                parts: list[dict[str, Any]] = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                parts = list(content)
            else:
                return
            parts.extend(
                {"type": "image_url", "image_url": {"url": url}} for url in images
            )
            msg["content"] = parts
            return

    async def _build_harness(
        self,
        model: ModelInfo,
        task_type: TaskType,
        user_input: str,
        messages: list[dict[str, Any]],
        on_reset: Callable[[], None] | None,
        on_event: Callable[[dict[str, Any]], None] | None,
        limits: RunLimits | None = None,
    ) -> Harness:
        """Prepare supervision for one run and add its guidance to the prompt."""
        scaffold = limits.apply(scaffold_for(model.id)) if limits else scaffold_for(model.id)
        disabled = frozenset(
            name.strip()
            for name in os.getenv("AGENT_DISABLED_TOOLS", "").split(",")
            if name.strip()
        )
        schemas = self._tool_registry.get_all_schemas()
        tools: list[dict[str, Any]] = []
        tools_on = os.getenv("AGENT_TOOLS_ENABLED", "true").lower() != "false"
        if tools_on and (not limits or limits.max_tools > 0):
            tools = select_tools(schemas, task_type, user_input, scaffold, disabled)
        # Models without native tool calling use the same tools through their text.
        native = bool(model.supports_tools)
        text_protocol = text_tool_protocol(tools) if tools and not native else ""

        workspace = get_active_workspace() or os.getenv("SQUIDWINGS_WORKSPACE", "").strip()
        harness = Harness(
            scaffold=scaffold,
            tools=tools if native else [],
            text_tools=bool(text_protocol),
            parameters={
                s["function"]["name"]: s["function"].get("parameters") or {} for s in schemas
            },
            disabled_tools=disabled,
            user_input=user_input,
            workspace=workspace,
            max_output=model.max_output or 0,
            advisor_model=(
                pick_advisor(self._router, self._health_checker, model.id, task_type)
                if tools and not limits
                else None
            ),
            limits=limits,
            on_event=on_event,
            on_reset=on_reset,
        )

        if tools:
            protocol = protocol_text(scaffold)
            if protocol:
                self._append_system(messages, f"\n\n{protocol}")
            if text_protocol:
                self._append_system(messages, text_protocol)
            guides, harness.stats.skills_used = skill_guidance(
                user_input, task_type, scaffold.skill_chars
            )
            if guides:
                self._append_system(messages, guides)
            briefing, used = await build_briefing(
                user_input, task_type, workspace, scaffold.briefing_chars
            )
            harness.stats.playbooks_used = used
            if briefing:
                self._append_system(messages, briefing)
        return harness

    @staticmethod
    def _append_system(messages: list[dict[str, Any]], text: str) -> None:
        """Append to the system message, after the cached prefix."""
        for msg in messages:
            if msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                content.append({"type": "text", "text": text})
            else:
                msg["content"] = (content or "") + text
            return

    def _should_use_pipeline(self, user_input: str, task_type: Any, difficulty: str = "medium") -> bool:
        """Decide whether a request is worth the PLAN→SEARCH→COMPILE→EXECUTE pipeline.

        The pipeline spends two extra model calls before any work starts. It pays
        off for research and for non-trivial reasoning. Everything else goes to
        the tool loop, where the model can still call web_search if it needs to;
        "bagaimana kabarmu" does not need a research plan.
        """
        if task_type == TaskType.REASON and difficulty != "easy":
            return True
        return _RESEARCH_RE.search(user_input) is not None

    def _cool_down_provider(self, model: ModelInfo | None) -> bool:
        """After a billing failure, skip that provider's models for a while.

        Returns:
            True if another provider can take the request.
        """
        provider_ids = getattr(self._api_client, "provider_ids", None)
        if model is None or not isinstance(provider_ids, list) or len(provider_ids) < 2:
            return False
        provider = self._api_client.provider_of(model.id)
        for item in self._registry.get_all():
            if self._api_client.provider_of(item.id) == provider:
                self._health_checker.mark_unavailable(item.id, _BILLING_COOLDOWN_SECONDS)
        return True

    def _pipe_to_loop_result(self, pipe: PipelineResult) -> LoopResult:
        """Convert a PipelineResult into a LoopResult so the rest of process() stays unchanged."""
        lr = LoopResult()
        lr.content = pipe.content
        lr.thinking = pipe.thinking
        lr.finish_reason = "stop"
        lr.iterations = max(1, pipe.iterations)
        lr.tool_calls_history = list(pipe.tool_calls_history)
        lr.content_parts = [pipe.content] if pipe.content else []
        # Token counts
        lr.total_prompt_tokens = pipe.total_prompt_tokens
        lr.total_completion_tokens = pipe.total_completion_tokens
        return lr

    async def _auto_continue(
        self,
        model_id: str,
        base_messages: list[dict[str, Any]],
        partial_content: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
        on_content_chunk: Callable[[str], None] | None,
        loop_result: LoopResult,
        max_rounds: int = 5,
        context_window: int = 0,
    ) -> str:
        """Auto-continue when response is truncated (finish_reason='length').

        Args:
            model_id: Model to continue with.
            base_messages: Original messages.
            partial_content: Content so far.
            temperature: Temperature.
            max_tokens: Max tokens per continuation.
            stream: Whether to stream.
            on_content_chunk: Content chunk callback.
            loop_result: LoopResult to accumulate tokens.
            max_rounds: Maximum continuation rounds.

        Returns:
            Complete content string.
        """
        content_parts = [partial_content]
        continue_messages = list(base_messages)
        seen = {hash(partial_content.strip()[:500])}

        for round_num in range(max_rounds):
            if loop_result.finish_reason != "length":
                break

            logger.info(
                f"[orchestrator] Auto-continue round {round_num + 1}/{max_rounds}"
            )

            # The tail is enough to continue from. Resending everything so far
            # would make each round cost more than the last.
            continue_messages.append({"role": "assistant", "content": partial_content[-2000:]})
            continue_messages.append({
                "role": "user",
                "content": (
                    "Lanjutkan tepat dari bagian terakhir yang terputus. "
                    "Jangan ulangi dari awal, langsung sambungkan."
                ),
            })

            # Every continuation grows the prompt. Refit it before the next
            # request so previous answers cannot exceed the model's window.
            request_messages = continue_messages
            if context_window > 0:
                request_messages = self._context_window.fit_to_window(
                    continue_messages,
                    context_window=context_window,
                    max_output_tokens=max_tokens,
                    model=model_id,
                    buffer=0,
                )

            cont_loop = AgentLoop(
                api_client=self._api_client,
                tool_registry=self._tool_registry,
            )
            cont_result = await cont_loop.run(
                model_id=model_id,
                messages=request_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
                on_content_chunk=on_content_chunk,
            )

            loop_result.total_prompt_tokens += cont_result.total_prompt_tokens
            loop_result.total_completion_tokens += cont_result.total_completion_tokens
            loop_result.finish_reason = cont_result.finish_reason

            partial_content = cont_result.content
            if not partial_content:
                break
            signature = hash(partial_content.strip()[:500])
            if signature in seen:
                logger.warning("[orchestrator] Continuation repeated itself; stopping")
                break
            seen.add(signature)
            content_parts.append(partial_content)

        return "".join(content_parts)

    async def _try_fallback(
        self,
        user_input: str,
        session_id: str,
        task_type: TaskType,
        failed_model_id: str,
        on_content_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        workspace_context: str | None = None,
        stream: bool | None = None,
        _depth: int = 0,
        images: list[str] | None = None,
        on_reset: Callable[[], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        task_hint: str | None = None,
        persist: bool = True,
        limits: RunLimits | None = None,
    ) -> AgentResponse | None:
        """Walk the entire fallback chain until a usable answer is produced.

        The loop is iterative rather than recursive so a long chain cannot
        exhaust the stack, and every model is attempted exactly once. A blank
        or errored response advances to the next candidate instead of ending
        the request — bansos mode must keep going.
        """
        attempted: set[str] = set()
        current_failed = failed_model_id

        while True:
            fb_model = self._fallback.get_next(
                task_type, current_failed, session_id
            )
            if not fb_model:
                logger.warning(
                    "[orchestrator] Fallback chain exhausted for "
                    f"{task_type.value} (tried={sorted(attempted)})"
                )
                return None

            if fb_model.id in attempted:
                logger.warning(
                    f"[orchestrator] Fallback loop detected at {fb_model.id}"
                )
                return None

            # A provider known to be out of budget, or a refused model, costs
            # nothing to skip and does not count as an attempt.
            if not self._health_checker.is_available(fb_model.id):
                current_failed = fb_model.id
                continue

            if len(attempted) >= MAX_FALLBACK_ATTEMPTS:
                logger.warning(
                    f"[orchestrator] Giving up after {len(attempted)} fallback attempts"
                )
                return None

            attempted.add(fb_model.id)
            logger.info(
                f"[orchestrator] Fallback attempt {len(attempted)}: "
                f"{fb_model.id}"
            )

            try:
                response = await self.process(
                    user_input=user_input,
                    session_id=session_id,
                    override_model=fb_model.id,
                    stream=stream,
                    on_content_chunk=on_content_chunk,
                    on_thinking_chunk=on_thinking_chunk,
                    workspace_context=workspace_context,
                    _fallback_depth=_depth + 1,
                    images=images,
                    on_reset=on_reset,
                    on_event=on_event,
                    task_hint=task_hint,
                    persist=persist,
                    limits=limits,
                )
            except Exception as e:
                logger.error(
                    f"[orchestrator] Fallback {fb_model.id} raised: {e}"
                )
                current_failed = fb_model.id
                continue

            if (
                response.status
                in (ResponseStatus.SUCCESS, ResponseStatus.PARTIAL)
                and response.content.strip()
            ):
                response.fallback_used = True
                response.status = ResponseStatus.FALLBACK_USED
                return response

            logger.warning(
                f"[orchestrator] Fallback {fb_model.id} unusable "
                f"(status={response.status}); trying next"
            )
            current_failed = fb_model.id

    # --- Public accessors ---

    @property
    def registry(self) -> ModelRegistry:
        """The model registry."""
        return self._registry

    @property
    def session_manager(self) -> SessionManager:
        """The session manager."""
        return self._session_manager

    @property
    def conversation_store(self) -> ConversationStore:
        """The conversation store."""
        return self._conversation_store

    @property
    def classifier(self) -> IntentClassifier:
        """The intent classifier."""
        return self._classifier

    @property
    def api_client(self) -> APIClient:
        """The API client."""
        return self._api_client

    async def close(self) -> None:
        """Close the API client and clean up resources."""
        await self._api_client.close()
        logger.info("[orchestrator] Closed")

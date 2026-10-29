"""
utils.event_bus — Simple publish/subscribe event bus.

Provides decoupled communication between layers. Any component
can emit events, and any component can listen for them without
direct coupling.

Usage:
    from utils.event_bus import event_bus, EventType

    # Subscribe
    def on_model_selected(data):
        print(f"Model selected: {data['model_id']}")
    event_bus.subscribe(EventType.MODEL_SELECTED, on_model_selected)

    # Publish
    event_bus.publish(EventType.MODEL_SELECTED, {"model_id": "TH/gpt-6.1-sol"})
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# Type aliases for event handlers
SyncHandler = Callable[[dict[str, Any]], None]
AsyncHandler = Callable[[dict[str, Any]], Awaitable[None]]
EventHandler = SyncHandler | AsyncHandler


class EventBus:
    """Simple pub/sub event bus for decoupled inter-component communication.

    Supports both sync and async handlers. Async handlers are scheduled
    as tasks when published from a sync context.

    Thread-safe for basic usage (no locks, but append is atomic in CPython).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._event_history: list[dict[str, Any]] = []
        self._max_history: int = 1000

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type.

        Args:
            event_type: The event type to listen for (use EventType enum values).
            handler: Callable that receives the event data dict.
        """
        self._subscribers[event_type].append(handler)
        logger.debug(f"[event_bus] Subscribed {handler.__name__} to '{event_type}'")

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler from an event type.

        Args:
            event_type: The event type to unsubscribe from.
            handler: The handler to remove.
        """
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h != handler
            ]

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Publish an event to all subscribers.

        Sync handlers are called immediately. Async handlers are
        scheduled as tasks if an event loop is running.

        Args:
            event_type: The event type to publish.
            data: Optional data dict to pass to handlers.
        """
        event_data = data or {}
        event_data["_event_type"] = event_type

        # Record in history
        if len(self._event_history) >= self._max_history:
            self._event_history.pop(0)
        self._event_history.append(event_data)

        handlers = self._subscribers.get(event_type, [])
        if not handlers:
            return

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    # Schedule async handler
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(handler(event_data))
                    except RuntimeError:
                        # No running event loop — skip async handlers
                        logger.debug(
                            f"[event_bus] Skipping async handler {handler.__name__} "
                            f"(no running event loop)"
                        )
                else:
                    handler(event_data)
            except Exception as e:
                logger.error(
                    f"[event_bus] Error in handler {handler.__name__} "
                    f"for '{event_type}': {e}"
                )

    async def publish_async(
        self, event_type: str, data: dict[str, Any] | None = None
    ) -> None:
        """Publish an event, awaiting async handlers.

        Args:
            event_type: The event type to publish.
            data: Optional data dict to pass to handlers.
        """
        event_data = data or {}
        event_data["_event_type"] = event_type

        if len(self._event_history) >= self._max_history:
            self._event_history.pop(0)
        self._event_history.append(event_data)

        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event_data)
                else:
                    handler(event_data)
            except Exception as e:
                logger.error(
                    f"[event_bus] Error in handler {handler.__name__} "
                    f"for '{event_type}': {e}"
                )

    def get_history(
        self,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent event history, optionally filtered by type.

        Args:
            event_type: Optional filter for specific event type.
            limit: Maximum number of events to return.

        Returns:
            List of event data dicts (most recent last).
        """
        events = self._event_history
        if event_type:
            events = [e for e in events if e.get("_event_type") == event_type]
        return events[-limit:]

    def clear(self) -> None:
        """Remove all subscribers and history."""
        self._subscribers.clear()
        self._event_history.clear()

    @property
    def subscriber_count(self) -> dict[str, int]:
        """Get the number of subscribers per event type."""
        return {k: len(v) for k, v in self._subscribers.items() if v}


# Global singleton event bus
event_bus = EventBus()

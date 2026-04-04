"""Graphiti 客户端封装

提供单例 Graphiti 实例，并将异步 API 桥接为同步调用，
以兼容 Flask 的线程模型。

用法::

    from app.utils.graphiti_client import get_graphiti, run_async

    g = get_graphiti()
    results = run_async(g.search("hello", group_id="mygroup"))
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.graphiti_client')

T = TypeVar('T')

# ---------------------------------------------------------------------------
# Dedicated event-loop running in a daemon thread
# ---------------------------------------------------------------------------
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start the background event-loop exactly once (thread-safe)."""
    global _loop, _loop_thread
    if _loop is not None and _loop.is_running():
        return _loop

    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop

        _loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        _loop_thread = threading.Thread(target=_run, daemon=True, name="graphiti-loop")
        _loop_thread.start()
        return _loop


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Submit *coro* to the background loop and block until it completes."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()  # blocks the calling (Flask) thread


# ---------------------------------------------------------------------------
# Singleton Graphiti client
# ---------------------------------------------------------------------------
_graphiti_instance = None
_graphiti_lock = threading.Lock()


def get_graphiti():
    """Return the shared :class:`Graphiti` instance, creating it on first call.

    Also runs ``build_indices_and_constraints()`` once to ensure Neo4j is
    set up with the required indexes.
    """
    global _graphiti_instance
    if _graphiti_instance is not None:
        return _graphiti_instance

    with _graphiti_lock:
        if _graphiti_instance is not None:
            return _graphiti_instance

        from graphiti_core import Graphiti
        from graphiti_core.llm_client import LLMConfig
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from .llm_client import FallbackLLMClient

        logger.info(
            "Initializing Graphiti client: uri=%s user=%s model=%s",
            Config.NEO4J_URI,
            Config.NEO4J_USER,
            Config.LLM_MODEL_NAME,
        )

        llm_client = FallbackLLMClient.build(
            config=LLMConfig(
                api_key=Config.LLM_API_KEY,
                base_url=Config.LLM_BASE_URL,
                model=Config.LLM_MODEL_NAME,
                small_model=Config.LLM_MODEL_NAME,
            )
        )

        # Subclass OpenAIEmbedder to handle empty inputs (OpenAI rejects empty arrays)
        class SafeEmbedder(OpenAIEmbedder):
            async def create(self, input_data):
                if not input_data:
                    return [0.0] * self.config.embedding_dim
                return await super().create(input_data)
            async def create_batch(self, input_data_list):
                if not input_data_list:
                    return []
                return await super().create_batch(input_data_list)

        embedder = SafeEmbedder(
            config=OpenAIEmbedderConfig(
                api_key=Config.EMBEDDING_API_KEY,
                base_url=Config.EMBEDDING_BASE_URL,
                embedding_model=Config.EMBEDDING_MODEL,
            )
        )

        cross_encoder = OpenAIRerankerClient(
            config=LLMConfig(
                api_key=Config.LLM_API_KEY,
                base_url=Config.LLM_BASE_URL,
                model=Config.LLM_MODEL_NAME,
                small_model=Config.LLM_MODEL_NAME,
            )
        )

        _graphiti_instance = Graphiti(
            Config.NEO4J_URI,
            Config.NEO4J_USER,
            Config.NEO4J_PASSWORD,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=cross_encoder,
        )

        # Create indices / constraints in Neo4j (idempotent)
        run_async(_graphiti_instance.build_indices_and_constraints())
        logger.info("Graphiti client initialized and indices built")

        return _graphiti_instance


async def close_graphiti() -> None:
    """Gracefully close the Graphiti client (call on app shutdown)."""
    global _graphiti_instance
    if _graphiti_instance is not None:
        await _graphiti_instance.close()
        _graphiti_instance = None

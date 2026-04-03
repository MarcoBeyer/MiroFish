"""Graph 节点/边读取工具 (Graphiti / Neo4j)。

替代原先的 Zep Cloud 分页逻辑。Graphiti 使用 Neo4j 存储，
通过 group_id 隔离不同图谱的数据，直接查询即可获取完整列表。
"""

from __future__ import annotations

import time
from typing import Any, List

from .logger import get_logger

logger = get_logger('mirofish.graph_paging')

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY = 2.0  # seconds, doubles each retry
_MAX_NODES = 2000


def _with_retry(func, *args, max_retries=_DEFAULT_MAX_RETRIES,
                retry_delay=_DEFAULT_RETRY_DELAY, description="operation",
                **kwargs) -> Any:
    """Execute *func* with exponential-backoff retry on transient errors."""
    last_exc: Exception | None = None
    delay = retry_delay

    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            last_exc = e
            if attempt < max_retries - 1:
                logger.warning(
                    "%s attempt %d failed: %s, retrying in %.1fs...",
                    description, attempt + 1, str(e)[:100], delay,
                )
                time.sleep(delay)
                delay *= 2
            else:
                logger.error("%s failed after %d attempts: %s",
                             description, max_retries, str(e))

    assert last_exc is not None
    raise last_exc


def fetch_all_nodes(
    graphiti,
    group_id: str,
    max_items: int = _MAX_NODES,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
) -> List[Any]:
    """Retrieve all entity nodes for *group_id* from Neo4j via Graphiti.

    Returns a list of EntityNode objects (up to *max_items*).
    """
    from .graphiti_client import run_async

    async def _fetch():
        driver = graphiti.driver
        records, _, _ = await driver.execute_query(
            "MATCH (n:Entity) WHERE n.group_id = $gid "
            "RETURN n ORDER BY n.created_at DESC LIMIT $limit",
            gid=group_id,
            limit=max_items,
        )
        return [record["n"] for record in records]

    return _with_retry(
        lambda: run_async(_fetch()),
        max_retries=max_retries,
        retry_delay=retry_delay,
        description=f"fetch nodes (group={group_id})",
    )


def fetch_all_edges(
    graphiti,
    group_id: str,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
) -> List[Any]:
    """Retrieve all entity edges for *group_id* from Neo4j via Graphiti.

    Returns a list of relationship records.
    """
    from .graphiti_client import run_async

    async def _fetch():
        driver = graphiti.driver
        records, _, _ = await driver.execute_query(
            "MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) "
            "WHERE r.group_id = $gid "
            "RETURN r, s.uuid AS source_uuid, s.name AS source_name, "
            "       t.uuid AS target_uuid, t.name AS target_name "
            "ORDER BY r.created_at DESC",
            gid=group_id,
        )
        return records

    return _with_retry(
        lambda: run_async(_fetch()),
        max_retries=max_retries,
        retry_delay=retry_delay,
        description=f"fetch edges (group={group_id})",
    )

"""
图谱构建服务
使用 Graphiti (self-hosted) 构建 Knowledge Graph
"""

import uuid
import time
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.graphiti_client import get_graphiti, run_async
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges
from .text_processor import TextProcessor
from ..utils.locale import t


@dataclass
class GraphInfo:
    """图谱信息"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


def parse_ontology(ontology: Dict[str, Any]):
    """Parse an ontology dict into Graphiti-compatible Pydantic models.

    Returns ``(entity_types, edge_types, edge_type_map)`` — any of which may
    be ``None`` when the ontology section is empty.  Reusable by both
    :class:`GraphBuilderService` and :class:`ZepGraphMemoryUpdater`.
    """
    from pydantic import BaseModel, Field
    from typing import Optional as Opt

    entity_types: Dict[str, Any] = {}
    for entity_def in ontology.get("entity_types", []):
        name = entity_def["name"]
        description = entity_def.get("description", f"A {name} entity.")

        attrs: Dict[str, Any] = {}
        annotations: Dict[str, Any] = {}
        for attr_def in entity_def.get("attributes", []):
            attr_name = attr_def["name"]
            attr_desc = attr_def.get("description", attr_name)
            attrs[attr_name] = Field(description=attr_desc, default=None)
            annotations[attr_name] = Opt[str]

        attrs["__annotations__"] = annotations
        entity_cls = type(name, (BaseModel,), attrs)
        entity_cls.__doc__ = description
        entity_types[name] = entity_cls

    edge_types: Dict[str, Any] = {}
    edge_type_map: Dict[tuple, list] = {}
    for edge_def in ontology.get("edge_types", []):
        name = edge_def["name"]
        description = edge_def.get("description", f"A {name} relationship.")

        attrs: Dict[str, Any] = {}
        annotations: Dict[str, Any] = {}
        for attr_def in edge_def.get("attributes", []):
            attr_name = attr_def["name"]
            attr_desc = attr_def.get("description", attr_name)
            attrs[attr_name] = Field(description=attr_desc, default=None)
            annotations[attr_name] = Opt[str]

        attrs["__annotations__"] = annotations
        class_name = ''.join(word.capitalize() for word in name.split('_'))
        edge_cls = type(class_name, (BaseModel,), attrs)
        edge_cls.__doc__ = description
        edge_types[name] = edge_cls

        for st in edge_def.get("source_targets", []):
            key = (st.get("source", "Entity"), st.get("target", "Entity"))
            edge_type_map.setdefault(key, []).append(name)

    return (
        entity_types if entity_types else None,
        edge_types if edge_types else None,
        edge_type_map if edge_type_map else None,
    )


class GraphBuilderService:
    """
    图谱构建服务
    负责调用 Graphiti API 构建知识图谱
    """

    def __init__(self, api_key: Optional[str] = None):
        # api_key kept for interface compat; Graphiti uses Neo4j credentials
        self.graphiti = get_graphiti()
        self.task_manager = TaskManager()
        # Ontology definitions stored here, passed to each add_episode call
        self._entity_types: Dict[str, Any] | None = None
        self._edge_types: Dict[str, Any] | None = None
        self._edge_type_map: Dict[tuple, list] | None = None

    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3
    ) -> str:
        """异步构建图谱，返回任务ID"""
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )

        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size)
        )
        thread.daemon = True
        thread.start()

        return task_id

    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int
    ):
        """图谱构建工作线程"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message=t('progress.startBuildingGraph')
            )

            # 1. 创建图谱 (just generates a group_id)
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=t('progress.graphCreated', graphId=graph_id)
            )

            # 2. 解析本体为 Pydantic 模型
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message=t('progress.ontologySet')
            )

            # 3. 文本分块
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=t('progress.textSplit', count=total_chunks)
            )

            # 4. 逐块发送数据 (Graphiti processes inline — no polling needed)
            self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.7),  # 20-90%
                    message=msg
                )
            )

            # 5. 获取图谱信息
            self.task_manager.update_task(
                task_id,
                progress=90,
                message=t('progress.fetchingGraphInfo')
            )

            graph_info = self._get_graph_info(graph_id)

            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)

    def create_graph(self, name: str) -> str:
        """Create a logical graph namespace (group_id). No API call needed."""
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """Parse ontology definition into Pydantic models for Graphiti.

        These are stored on the service instance and passed to each
        ``add_episode()`` call.
        """
        self._entity_types, self._edge_types, self._edge_type_map = parse_ontology(ontology)

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """Add text chunks to the graph via Graphiti, return episode names."""
        episode_names = []
        total_chunks = len(chunks)

        for i, chunk in enumerate(chunks):
            chunk_num = i + 1

            if progress_callback:
                progress = chunk_num / total_chunks
                progress_callback(
                    f"处理第 {chunk_num}/{total_chunks} 个文本块...",
                    progress
                )

            ep_name = f"chunk_{chunk_num}"
            try:
                run_async(self.graphiti.add_episode(
                    name=ep_name,
                    episode_body=chunk,
                    source_description="MiroFish document chunk",
                    reference_time=datetime.now(timezone.utc),
                    group_id=graph_id,
                    entity_types=self._entity_types,
                    edge_types=self._edge_types,
                    edge_type_map=self._edge_type_map,
                    custom_extraction_instructions=(
                        "IMPORTANT: You MUST only use relation_type values from the provided FACT_TYPES list. "
                        "Do NOT invent new relation types. If a relationship does not fit any of the provided "
                        "types, skip that relationship entirely — do not extract it."
                    ) if self._edge_types else None,
                ))
                episode_names.append(ep_name)
            except Exception as e:
                if progress_callback:
                    progress_callback(f"块 {chunk_num} 处理失败: {str(e)}", 0)
                raise

        return episode_names


    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """获取图谱信息"""
        nodes = fetch_all_nodes(self.graphiti, graph_id)
        edges = fetch_all_edges(self.graphiti, graph_id)

        entity_types = set()
        for node in nodes:
            labels = node.get("labels", []) if isinstance(node, dict) else getattr(node, 'labels', [])
            if labels:
                for label in labels:
                    if label not in ("Entity", "Node"):
                        entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """获取完整图谱数据（包含详细信息）"""
        nodes = fetch_all_nodes(self.graphiti, graph_id)
        edges = fetch_all_edges(self.graphiti, graph_id)

        # Build node map for name lookups
        node_map: Dict[str, str] = {}
        nodes_data = []
        for node in nodes:
            n = dict(node) if not isinstance(node, dict) else node
            node_uuid = n.get("uuid", "")
            node_name = n.get("name", "")
            node_map[node_uuid] = node_name

            nodes_data.append({
                "uuid": node_uuid,
                "name": node_name,
                "labels": n.get("labels", []),
                "summary": n.get("summary", ""),
                "attributes": n.get("attributes", {}),
                "created_at": str(n.get("created_at", "")) or None,
            })

        edges_data = []
        for record in edges:
            if isinstance(record, dict):
                r = record
            else:
                # Neo4j Record object
                r_rel = record["r"]
                r = dict(r_rel)
                r["source_node_uuid"] = record.get("source_uuid", "")
                r["source_node_name"] = record.get("source_name", "")
                r["target_node_uuid"] = record.get("target_uuid", "")
                r["target_node_name"] = record.get("target_name", "")

            source_uuid = r.get("source_node_uuid", "")
            target_uuid = r.get("target_node_uuid", "")
            edges_data.append({
                "uuid": r.get("uuid", ""),
                "name": r.get("name", ""),
                "fact": r.get("fact", ""),
                "fact_type": r.get("fact_type", r.get("name", "")),
                "source_node_uuid": source_uuid,
                "target_node_uuid": target_uuid,
                "source_node_name": r.get("source_node_name", node_map.get(source_uuid, "")),
                "target_node_name": r.get("target_node_name", node_map.get(target_uuid, "")),
                "attributes": r.get("attributes", {}),
                "created_at": str(r.get("created_at", "")) or None,
                "valid_at": str(r.get("validity_start", "")) or None,
                "invalid_at": str(r.get("validity_end", "")) or None,
                "expired_at": None,
                "episodes": [],
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str):
        """Delete all nodes and edges belonging to this graph (group_id)."""
        async def _delete():
            driver = self.graphiti.driver
            await driver.execute_query(
                "MATCH (n) WHERE n.group_id = $gid DETACH DELETE n",
                gid=graph_id,
            )

        run_async(_delete())

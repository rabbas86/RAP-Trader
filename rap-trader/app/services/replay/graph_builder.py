"""Deterministic replay graph builder operating on resolved metadata only."""

from __future__ import annotations

from app.services.replay.errors import (
    ReplayCycleDetectedError,
    ReplayDepthExceededError,
    ReplayGraphTooLargeError,
    ReplayTemporalViolationError,
)
from app.services.replay.graph import ReplayGraph, ReplayGraphEdge, ReplayGraphNode


class ReplayGraphNodeMetadata:
    __slots__ = (
        "artifact_id",
        "artifact_type",
        "logical_as_of",
        "producer_version",
        "stage",
        "upstream_ids",
    )

    def __init__(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        logical_as_of: str,
        stage: str,
        producer_version: str,
        upstream_ids: tuple[str, ...],
    ) -> None:
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type
        self.logical_as_of = logical_as_of
        self.stage = stage
        self.producer_version = producer_version
        self.upstream_ids = upstream_ids


class ReplayGraphBuilder:
    """Pure graph algorithm for deterministic replay lineage construction."""

    def __init__(self, *, max_depth: int = 64, max_nodes: int = 512) -> None:
        if max_depth <= 0 or max_nodes <= 0:
            raise ValueError("traversal limits must be positive")
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def build(
        self,
        terminal: ReplayGraphNodeMetadata,
        lookup: dict[str, ReplayGraphNodeMetadata],
    ) -> ReplayGraph:
        if terminal.artifact_id not in lookup:
            raise ReplayCycleDetectedError(cycle=(terminal.artifact_id,))

        nodes: dict[str, ReplayGraphNode] = {}
        edges: list[ReplayGraphEdge] = []
        order: list[str] = []
        visited: set[str] = set()
        visiting: list[str] = []

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                cycle_start = visiting.index(node_id)
                raise ReplayCycleDetectedError(cycle=tuple(visiting[cycle_start:] + [node_id]))

            visiting.append(node_id)
            if len(nodes) >= self.max_nodes:
                raise ReplayGraphTooLargeError(max_nodes=self.max_nodes)
            if len(visiting) - 1 > self.max_depth:
                raise ReplayDepthExceededError(max_depth=self.max_depth)

            current = lookup[node_id]
            nodes[node_id] = ReplayGraphNode(
                artifact_id=current.artifact_id,
                artifact_type=current.artifact_type,
                logical_as_of=current.logical_as_of,
                stage=current.stage,
                producer_version=current.producer_version,
            )
            order.append(node_id)

            seen: set[str] = set()
            for upstream_id in current.upstream_ids:
                if upstream_id in seen:
                    continue
                seen.add(upstream_id)
                if upstream_id not in lookup:
                    raise ReplayCycleDetectedError(cycle=(node_id, upstream_id))
                upstream = lookup[upstream_id]
                if upstream.logical_as_of > current.logical_as_of:
                    raise ReplayTemporalViolationError(
                        upstream_id=upstream_id,
                        downstream_id=node_id,
                    )
                edges.append(
                    ReplayGraphEdge(
                        upstream_artifact_id=upstream_id,
                        downstream_artifact_id=node_id,
                    )
                )
                visit(upstream_id)

            visiting.remove(node_id)
            visited.add(node_id)

        visit(terminal.artifact_id)

        terminal_as_of = next(node.logical_as_of for node in nodes.values() if node.artifact_id == terminal.artifact_id)
        for node in nodes.values():
            if node.logical_as_of > terminal_as_of:
                raise ReplayTemporalViolationError(
                    upstream_id=node.artifact_id,
                    downstream_id=terminal.artifact_id,
                )

        sorted_nodes = sorted(nodes.values(), key=lambda item: item.artifact_id)
        sorted_edges = sorted(
            {edge: None for edge in edges}.keys(),
            key=lambda item: (item.upstream_artifact_id, item.downstream_artifact_id),
        )
        roots = sorted(
            [node.artifact_id for node in sorted_nodes if not any(edge.downstream_artifact_id == node.artifact_id for edge in sorted_edges)]
        )
        ordered = self._topological_order(nodes, sorted_edges)
        missing = [aid for aid in terminal.upstream_ids if aid not in nodes]
        if missing:
            raise ReplayCycleDetectedError(cycle=(terminal.artifact_id, missing[0]))

        return ReplayGraph(
            nodes=tuple(sorted_nodes),
            edges=tuple(sorted_edges),
            root_artifact_ids=tuple(roots),
            terminal_artifact_id=terminal.artifact_id,
            ordered_artifact_ids=tuple(ordered),
            node_count=len(sorted_nodes),
            edge_count=len(sorted_edges),
        )

    def _topological_order(self, nodes: dict[str, ReplayGraphNode], edges: list[ReplayGraphEdge]) -> list[str]:
        in_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge in edges:
            adjacency[edge.upstream_artifact_id].append(edge.downstream_artifact_id)
            in_degree[edge.downstream_artifact_id] += 1

        queue: list[str] = sorted([node_id for node_id, degree in in_degree.items() if degree == 0])
        ordered: list[str] = []
        while queue:
            node_id = queue.pop(0)
            ordered.append(node_id)
            for target in sorted(adjacency[node_id]):
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    queue.append(target)
            queue.sort()
        return ordered


__all__ = ["ReplayGraphBuilder", "ReplayGraphNodeMetadata"]

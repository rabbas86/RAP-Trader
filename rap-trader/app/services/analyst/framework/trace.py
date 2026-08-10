"""Shared deterministic analysis-trace DAG construction."""

from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import AnalysisTrace, AnalystRequest, EvidenceItem, EvidenceType, TraceEdge, TraceNode, validate_trace


def build_analysis_trace(
    analyst_id: str, opinion_id: str, evidence: list[EvidenceItem], request: AnalystRequest, source: str
) -> AnalysisTrace:
    request_node, source_node, opinion_node = f"request:{opinion_id}", f"source:{opinion_id}", f"opinion:{opinion_id}"
    source_type = "market_data" if analyst_id == "technical" else "input_data"
    nodes = [
        TraceNode(node_id=request_node, node_type="analyst_request", created_at=request.as_of, metadata={}),
        TraceNode(node_id=source_node, node_type=source_type, created_at=request.as_of, metadata={"source": source}),
    ]
    edges = [TraceEdge(source_node_id=request_node, target_node_id=source_node, edge_type="requests")]
    for item in evidence:
        kind = (
            "forecast"
            if item.evidence_type is EvidenceType.FORECAST
            else "backtest"
            if item.evidence_type is EvidenceType.BACKTEST
            else "evidence"
        )
        parent = source_node
        if kind != "evidence":
            parent = f"{kind}:{opinion_id}"
            nodes.append(TraceNode(node_id=parent, node_type=kind, created_at=request.as_of, metadata={}))
            edges.append(TraceEdge(source_node_id=request_node, target_node_id=parent, edge_type="requests"))
        nodes.append(
            TraceNode(
                node_id=item.evidence_id,
                node_type="evidence",
                created_at=request.as_of,
                metadata={"category": item.summary.split(":", 1)[0]},
            )
        )
        edges.append(TraceEdge(source_node_id=parent, target_node_id=item.evidence_id, edge_type="produces"))
    nodes.append(TraceNode(node_id=opinion_node, node_type="analyst_opinion", created_at=request.as_of, metadata={}))
    edges.extend(TraceEdge(source_node_id=item.evidence_id, target_node_id=opinion_node, edge_type="supports") for item in evidence)
    if not evidence:
        edges.append(TraceEdge(source_node_id=source_node, target_node_id=opinion_node, edge_type="insufficient_for"))
    return validate_trace(
        AnalysisTrace(
            trace_id=str(uuid5(NAMESPACE_URL, f"{analyst_id}-trace|{opinion_id}")), nodes=nodes, edges=edges, created_at=request.as_of
        )
    )

"""Risk-review provenance DAG."""

from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import AnalysisTrace, TraceEdge, TraceNode, validate_trace


def build_risk_trace(
    proposal_id: str, assessment_id: str, metric_names: tuple[str, ...], breach_ids: tuple[str, ...], as_of: datetime
) -> AnalysisTrace:
    proposal = f"proposal:{proposal_id}"
    metrics = f"metrics:{assessment_id}"
    breaches = f"breaches:{assessment_id}"
    stress = f"stress:{assessment_id}"
    assessment = f"assessment:{assessment_id}"
    decision = f"decision:{assessment_id}"
    nodes = [
        TraceNode(node_id=proposal, node_type="PortfolioProposal", created_at=as_of),
        TraceNode(node_id=metrics, node_type="RiskMetrics", created_at=as_of, metadata={"metrics": metric_names}),
        TraceNode(node_id=breaches, node_type="RiskBreaches", created_at=as_of, metadata={"breaches": breach_ids}),
        TraceNode(node_id=stress, node_type="StressResults", created_at=as_of),
        TraceNode(node_id=assessment, node_type="RiskAssessment", created_at=as_of),
        TraceNode(node_id=decision, node_type="RiskDecision", created_at=as_of),
    ]
    edges = [
        TraceEdge(source_node_id=proposal, target_node_id=metrics, edge_type="measured_by"),
        TraceEdge(source_node_id=metrics, target_node_id=breaches, edge_type="evaluated_against"),
        TraceEdge(source_node_id=proposal, target_node_id=stress, edge_type="stressed_by"),
        TraceEdge(source_node_id=breaches, target_node_id=assessment, edge_type="contributes_to"),
        TraceEdge(source_node_id=stress, target_node_id=assessment, edge_type="contributes_to"),
        TraceEdge(source_node_id=assessment, target_node_id=decision, edge_type="reviewed_as"),
    ]
    return validate_trace(
        AnalysisTrace(trace_id=str(uuid5(NAMESPACE_URL, f"risk-trace:{assessment_id}")), nodes=nodes, edges=edges, created_at=as_of)
    )

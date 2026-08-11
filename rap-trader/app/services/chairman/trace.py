"""Deterministic Chairman governance DAG with no execution nodes."""

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import AnalysisTrace, TraceEdge, TraceNode, validate_trace


def build_chairman_trace(
    committee_assessment_id: str,
    committee_recommendation_id: str,
    assessment_id: str,
    decision_id: str,
    as_of: datetime,
) -> AnalysisTrace:
    committee = f"committee-assessment:{committee_assessment_id}"
    recommendation = f"committee-recommendation:{committee_recommendation_id}"
    governance = f"governance-review:{assessment_id}"
    assessment = f"chairman-assessment:{assessment_id}"
    decision = f"chairman-decision:{decision_id}"
    nodes = [
        TraceNode(node_id=committee, node_type="CommitteeAssessment", created_at=as_of),
        TraceNode(node_id=recommendation, node_type="CommitteeRecommendation", created_at=as_of),
        TraceNode(node_id=governance, node_type="GovernanceReview", created_at=as_of),
        TraceNode(node_id=assessment, node_type="ChairmanAssessment", created_at=as_of),
        TraceNode(node_id=decision, node_type="ChairmanDecision", created_at=as_of),
    ]
    edges = [
        TraceEdge(source_node_id=committee, target_node_id=governance, edge_type="reviewed_by"),
        TraceEdge(source_node_id=recommendation, target_node_id=governance, edge_type="reviewed_by"),
        TraceEdge(source_node_id=governance, target_node_id=assessment, edge_type="produces"),
        TraceEdge(source_node_id=assessment, target_node_id=decision, edge_type="governs"),
    ]
    return validate_trace(
        AnalysisTrace(trace_id=str(uuid5(NAMESPACE_URL, f"chairman-trace:{assessment_id}")), nodes=nodes, edges=edges, created_at=as_of)
    )

"""Deterministic committee research-governance DAG."""

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import AnalysisTrace, TraceEdge, TraceNode, validate_trace


def build_committee_trace(
    opinion_ids: tuple[str, ...], proposal_id: str, risk_assessment_id: str, risk_decision_id: str, assessment_id: str, as_of: datetime
) -> AnalysisTrace:
    nodes = [TraceNode(node_id=f"opinion:{item}", node_type="AnalystOpinion", created_at=as_of) for item in opinion_ids]
    nodes.extend(
        [
            TraceNode(node_id=f"research-case:{assessment_id}", node_type="ResearchCase", created_at=as_of),
            TraceNode(node_id=f"alignment:{assessment_id}", node_type="CommitteeAlignment", created_at=as_of),
            TraceNode(node_id=f"conflicts:{assessment_id}", node_type="CommitteeConflicts", created_at=as_of),
            TraceNode(node_id=f"proposal:{proposal_id}", node_type="PortfolioProposal", created_at=as_of),
            TraceNode(node_id=f"risk-assessment:{risk_assessment_id}", node_type="RiskAssessment", created_at=as_of),
            TraceNode(node_id=f"risk-decision:{risk_decision_id}", node_type="RiskDecision", created_at=as_of),
            TraceNode(node_id=f"committee-assessment:{assessment_id}", node_type="CommitteeAssessment", created_at=as_of),
        ]
    )
    case = f"research-case:{assessment_id}"
    alignment = f"alignment:{assessment_id}"
    conflicts = f"conflicts:{assessment_id}"
    result = f"committee-assessment:{assessment_id}"
    edges = [TraceEdge(source_node_id=f"opinion:{item}", target_node_id=case, edge_type="contributes_to") for item in opinion_ids]
    edges.extend(
        [
            TraceEdge(source_node_id=case, target_node_id=alignment, edge_type="evaluated_as"),
            TraceEdge(source_node_id=case, target_node_id=conflicts, edge_type="evaluated_for"),
            TraceEdge(source_node_id=alignment, target_node_id=result, edge_type="informs"),
            TraceEdge(source_node_id=conflicts, target_node_id=result, edge_type="informs"),
            TraceEdge(source_node_id=f"proposal:{proposal_id}", target_node_id=result, edge_type="reviewed_by"),
            TraceEdge(
                source_node_id=f"proposal:{proposal_id}", target_node_id=f"risk-assessment:{risk_assessment_id}", edge_type="assessed_by"
            ),
            TraceEdge(
                source_node_id=f"risk-assessment:{risk_assessment_id}",
                target_node_id=f"risk-decision:{risk_decision_id}",
                edge_type="decided_by",
            ),
            TraceEdge(source_node_id=f"risk-decision:{risk_decision_id}", target_node_id=result, edge_type="governs"),
        ]
    )
    return validate_trace(
        AnalysisTrace(trace_id=str(uuid5(NAMESPACE_URL, f"committee-trace:{assessment_id}")), nodes=nodes, edges=edges, created_at=as_of)
    )

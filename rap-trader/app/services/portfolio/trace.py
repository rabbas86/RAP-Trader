"""Portfolio construction DAG trace."""

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import AnalysisTrace, TraceEdge, TraceNode, validate_trace
from app.domain.models.portfolio import AssetConviction


def build_portfolio_trace(proposal_id: str, convictions: list[AssetConviction], adjustments: list[str], as_of: datetime) -> AnalysisTrace:
    nodes: list[TraceNode] = []
    edges: list[TraceEdge] = []
    allocation_nodes: list[str] = []
    for conviction in convictions:
        contribution_nodes: list[str] = []
        for contribution in conviction.contributions:
            opinion_node = f"opinion:{contribution.opinion_id}"
            contribution_node = f"contribution:{contribution.opinion_id}"
            nodes.extend(
                [
                    TraceNode(node_id=opinion_node, node_type="AnalystOpinion", created_at=as_of),
                    TraceNode(node_id=contribution_node, node_type="AnalystContribution", created_at=as_of),
                ]
            )
            edges.append(TraceEdge(source_node_id=opinion_node, target_node_id=contribution_node, edge_type="maps_to"))
            contribution_nodes.append(contribution_node)
        conviction_node = f"conviction:{conviction.symbol}"
        allocation_node = f"allocation:{conviction.symbol}"
        nodes.extend(
            [
                TraceNode(node_id=conviction_node, node_type="AssetConviction", created_at=as_of),
                TraceNode(node_id=allocation_node, node_type="CandidateAllocation", created_at=as_of),
            ]
        )
        edges.extend(TraceEdge(source_node_id=node, target_node_id=conviction_node, edge_type="aggregates") for node in contribution_nodes)
        edges.append(TraceEdge(source_node_id=conviction_node, target_node_id=allocation_node, edge_type="weights"))
        allocation_nodes.append(allocation_node)
    adjustment_node = f"adjustments:{proposal_id}"
    proposal_node = f"proposal:{proposal_id}"
    nodes.extend(
        [
            TraceNode(node_id=adjustment_node, node_type="ConstraintAdjustments", created_at=as_of, metadata={"adjustments": adjustments}),
            TraceNode(node_id=proposal_node, node_type="PortfolioProposal", created_at=as_of),
        ]
    )
    edges.extend(TraceEdge(source_node_id=node, target_node_id=adjustment_node, edge_type="constrained_by") for node in allocation_nodes)
    edges.append(TraceEdge(source_node_id=adjustment_node, target_node_id=proposal_node, edge_type="produces"))
    return validate_trace(
        AnalysisTrace(trace_id=str(uuid5(NAMESPACE_URL, f"portfolio-trace:{proposal_id}")), nodes=nodes, edges=edges, created_at=as_of)
    )

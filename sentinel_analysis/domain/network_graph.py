"""Maritime Intelligence Relationship Network Graph Domain Models.

Represents a multi-relational knowledge graph connecting:
- Vessels (AIS contacts and SAR detections)
- Shared encounters (rendezvous, STS transfers, proximity events)
- Ports visited / destinations
- Flag states (registries and Flags of Convenience)
- Vessel operators, beneficial owners, and fleet managers
- Repeated routes and navigation corridors
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Optional


class NodeType(str, Enum):
    VESSEL = "VESSEL"
    ENCOUNTER = "ENCOUNTER"
    PORT = "PORT"
    FLAG = "FLAG"
    OPERATOR = "OPERATOR"
    ROUTE = "ROUTE"
    DARK_DETECTION = "DARK_DETECTION"


class EdgeType(str, Enum):
    ENCOUNTERED = "ENCOUNTERED"
    VISITED_PORT = "VISITED_PORT"
    FLAGGED_AS = "FLAGGED_AS"
    OPERATED_BY = "OPERATED_BY"
    TRAVELED_ROUTE = "TRAVELED_ROUTE"
    SHARED_ENCOUNTER = "SHARED_ENCOUNTER"
    CORRELATED_TO = "CORRELATED_TO"


@dataclass
class NetworkNode:
    """An individual entity in the maritime relationship network."""

    id: str
    label: str
    node_type: NodeType
    properties: dict[str, Any] = field(default_factory=dict)
    risk_score: float = 0.0
    degree: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type.value if isinstance(self.node_type, NodeType) else str(self.node_type),
            "properties": dict(self.properties),
            "risk_score": round(self.risk_score, 3),
            "degree": self.degree,
        }

    def to_cytoscape(self) -> dict[str, Any]:
        """Convert to Cytoscape.js node representation."""
        data = self.to_dict()
        return {
            "group": "nodes",
            "data": data,
            "classes": f"type-{data['node_type'].lower()} risk-{'high' if self.risk_score >= 0.7 else 'med' if self.risk_score >= 0.4 else 'low'}",
        }


@dataclass
class NetworkEdge:
    """A directed or undirected relationship between two entities."""

    source: str
    target: str
    edge_type: EdgeType
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.id is None:
            t_str = self.edge_type.value if isinstance(self.edge_type, EdgeType) else str(self.edge_type)
            self.id = f"{self.source}__{t_str}__{self.target}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type.value if isinstance(self.edge_type, EdgeType) else str(self.edge_type),
            "weight": round(self.weight, 3),
            "properties": dict(self.properties),
        }

    def to_cytoscape(self) -> dict[str, Any]:
        """Convert to Cytoscape.js edge representation."""
        data = self.to_dict()
        return {
            "group": "edges",
            "data": data,
            "classes": f"edge-{data['edge_type'].lower()}",
        }


@dataclass
class MaritimeNetworkGraph:
    """Complete maritime relationship graph connecting vessels and intelligence entities."""

    nodes: list[NetworkNode] = field(default_factory=list)
    edges: list[NetworkEdge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: NetworkNode) -> None:
        existing = self.get_node(node.id)
        if existing is None:
            self.nodes.append(node)
        else:
            # Merge properties
            existing.properties.update(node.properties)
            if node.risk_score > existing.risk_score:
                existing.risk_score = node.risk_score

    def add_edge(self, edge: NetworkEdge) -> None:
        for e in self.edges:
            if e.source == edge.source and e.target == edge.target and e.edge_type == edge.edge_type:
                e.weight += edge.weight
                e.properties.update(edge.properties)
                return
        self.edges.append(edge)

    def get_node(self, node_id: str) -> Optional[NetworkNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def compute_degrees(self) -> None:
        """Compute degree centrality for all nodes."""
        counts: dict[str, int] = {n.id: 0 for n in self.nodes}
        for e in self.edges:
            if e.source in counts:
                counts[e.source] += 1
            if e.target in counts:
                counts[e.target] += 1
        for n in self.nodes:
            n.degree = counts.get(n.id, 0)

    def propagate_risk(self, decay: float = 0.45, iterations: int = 2) -> None:
        """Propagate risk scores through shared encounters and operational links."""
        self.compute_degrees()
        for _ in range(iterations):
            delta: dict[str, float] = {n.id: 0.0 for n in self.nodes}
            for e in self.edges:
                src = self.get_node(e.source)
                tgt = self.get_node(e.target)
                if not src or not tgt:
                    continue
                # Weight multiplier based on relationship criticality
                edge_mult = 1.0
                if e.edge_type in (EdgeType.ENCOUNTERED, EdgeType.SHARED_ENCOUNTER):
                    edge_mult = 1.2
                elif e.edge_type == EdgeType.OPERATED_BY:
                    edge_mult = 0.9
                elif e.edge_type == EdgeType.FLAGGED_AS:
                    edge_mult = 0.7

                coupling = decay * edge_mult * (e.weight / max(1.0, float(src.degree or 1)))
                if src.risk_score > tgt.risk_score:
                    trans = (src.risk_score - tgt.risk_score) * coupling
                    delta[tgt.id] = max(delta[tgt.id], trans)
                if tgt.risk_score > src.risk_score:
                    trans = (tgt.risk_score - src.risk_score) * coupling
                    delta[src.id] = max(delta[src.id], trans)

            for n in self.nodes:
                n.risk_score = min(1.0, max(n.risk_score, n.risk_score + delta.get(n.id, 0.0)))

    def get_subgraph(self, focal_node_id: str, max_depth: int = 2) -> MaritimeNetworkGraph:
        """Extract an ego subgraph centered on focal_node_id up to max_depth hops."""
        visited: set[str] = {focal_node_id}
        current_layer: set[str] = {focal_node_id}

        for _ in range(max_depth):
            next_layer: set[str] = set()
            for edge in self.edges:
                if edge.source in current_layer and edge.target not in visited:
                    next_layer.add(edge.target)
                elif edge.target in current_layer and edge.source not in visited:
                    next_layer.add(edge.source)
            visited.update(next_layer)
            current_layer = next_layer
            if not current_layer:
                break

        sub_nodes = [n for n in self.nodes if n.id in visited]
        sub_edges = [e for e in self.edges if e.source in visited and e.target in visited]
        sub = MaritimeNetworkGraph(nodes=sub_nodes, edges=sub_edges, metadata=dict(self.metadata))
        sub.compute_degrees()
        return sub

    def calculate_metrics(self) -> dict[str, Any]:
        """Calculate graph-level summary intelligence metrics."""
        self.compute_degrees()
        total_nodes = len(self.nodes)
        total_edges = len(self.edges)
        vessel_count = sum(1 for n in self.nodes if n.node_type == NodeType.VESSEL)
        encounter_count = sum(1 for n in self.nodes if n.node_type == NodeType.ENCOUNTER)
        port_count = sum(1 for n in self.nodes if n.node_type == NodeType.PORT)
        flag_count = sum(1 for n in self.nodes if n.node_type == NodeType.FLAG)
        operator_count = sum(1 for n in self.nodes if n.node_type == NodeType.OPERATOR)
        route_count = sum(1 for n in self.nodes if n.node_type == NodeType.ROUTE)

        density = (2.0 * total_edges) / (total_nodes * (total_nodes - 1)) if total_nodes > 1 else 0.0
        high_risk_nodes = [n.to_dict() for n in self.nodes if n.risk_score >= 0.7]

        # Top 5 most central nodes by degree
        top_central = sorted(self.nodes, key=lambda n: n.degree, reverse=True)[:5]

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "vessel_count": vessel_count,
            "encounter_count": encounter_count,
            "port_count": port_count,
            "flag_count": flag_count,
            "operator_count": operator_count,
            "route_count": route_count,
            "graph_density": round(density, 4),
            "high_risk_count": len(high_risk_nodes),
            "high_risk_nodes": high_risk_nodes,
            "top_central_nodes": [n.to_dict() for n in top_central],
        }

    def to_dict(self) -> dict[str, Any]:
        self.compute_degrees()
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "metrics": self.calculate_metrics(),
            "metadata": dict(self.metadata),
        }

    def to_cytoscape(self) -> list[dict[str, Any]]:
        """Return full graph elements formatted for Cytoscape.js rendering."""
        elements = [n.to_cytoscape() for n in self.nodes]
        elements.extend([e.to_cytoscape() for e in self.edges])
        return elements

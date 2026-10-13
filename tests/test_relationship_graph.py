"""Unit tests for Maritime Entity Relationship Graph."""

import unittest
from datetime import datetime, timezone

from sentinel_analysis.application.use_cases.build_relationship_graph import (
    BuildRelationshipGraph,
    MID_TO_FLAG,
)
from sentinel_analysis.domain.entities import AISRecord, Acquisition, BoundingBox, Scan, Vessel, VesselPosition
from sentinel_analysis.domain.network_graph import (
    EdgeType,
    MaritimeNetworkGraph,
    NetworkEdge,
    NetworkNode,
    NodeType,
)


class TestMaritimeNetworkGraph(unittest.TestCase):
    def test_node_and_edge_creation(self):
        graph = MaritimeNetworkGraph()
        n1 = NetworkNode(id="vessel_123456789", label="VESSEL_123456789", node_type=NodeType.VESSEL, risk_score=0.2)
        n2 = NetworkNode(id="port_SGSIN", label="Port of Singapore", node_type=NodeType.PORT)
        graph.add_node(n1)
        graph.add_node(n2)

        edge = NetworkEdge(
            source="vessel_123456789",
            target="port_SGSIN",
            edge_type=EdgeType.VISITED_PORT,
            weight=1.0,
            properties={"calls": 3},
        )
        graph.add_edge(edge)

        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(len(graph.edges), 1)

        # Degree calculation
        graph.compute_degrees()
        self.assertEqual(graph.get_node("vessel_123456789").degree, 1)
        self.assertEqual(graph.get_node("port_SGSIN").degree, 1)

    def test_duplicate_edge_weight_accumulation(self):
        graph = MaritimeNetworkGraph()
        e1 = NetworkEdge(source="A", target="B", edge_type=EdgeType.ENCOUNTERED, weight=1.0)
        e2 = NetworkEdge(source="A", target="B", edge_type=EdgeType.ENCOUNTERED, weight=2.0)
        graph.add_edge(e1)
        graph.add_edge(e2)

        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].weight, 3.0)

    def test_cytoscape_export(self):
        graph = MaritimeNetworkGraph()
        graph.add_node(NetworkNode(id="v1", label="V1", node_type=NodeType.VESSEL, risk_score=0.85))
        graph.add_node(NetworkNode(id="f1", label="Togo", node_type=NodeType.FLAG, risk_score=0.85))
        graph.add_edge(NetworkEdge(source="v1", target="f1", edge_type=EdgeType.FLAGGED_AS))

        cy = graph.to_cytoscape()
        self.assertEqual(len(cy), 3)

        node_cy = [el for el in cy if el.get("group") == "nodes"]
        edge_cy = [el for el in cy if el.get("group") == "edges"]
        self.assertEqual(len(node_cy), 2)
        self.assertEqual(len(edge_cy), 1)
        self.assertIn("risk-high", node_cy[0]["classes"])
        self.assertEqual(edge_cy[0]["data"]["source"], "v1")
        self.assertEqual(edge_cy[0]["data"]["target"], "f1")

    def test_ego_subgraph_extraction(self):
        graph = MaritimeNetworkGraph()
        # Create chain: A - B - C - D
        for name in ["A", "B", "C", "D"]:
            graph.add_node(NetworkNode(id=name, label=name, node_type=NodeType.VESSEL))
        graph.add_edge(NetworkEdge(source="A", target="B", edge_type=EdgeType.ENCOUNTERED))
        graph.add_edge(NetworkEdge(source="B", target="C", edge_type=EdgeType.ENCOUNTERED))
        graph.add_edge(NetworkEdge(source="C", target="D", edge_type=EdgeType.ENCOUNTERED))

        # Depth 1 from A -> A and B
        sub1 = graph.get_subgraph(focal_node_id="A", max_depth=1)
        sub1_ids = {n.id for n in sub1.nodes}
        self.assertEqual(sub1_ids, {"A", "B"})
        self.assertEqual(len(sub1.edges), 1)

        # Depth 2 from A -> A, B, C
        sub2 = graph.get_subgraph(focal_node_id="A", max_depth=2)
        sub2_ids = {n.id for n in sub2.nodes}
        self.assertEqual(sub2_ids, {"A", "B", "C"})
        self.assertEqual(len(sub2.edges), 2)

    def test_threat_propagation(self):
        graph = MaritimeNetworkGraph()
        # High risk vessel V1 connected to innocent vessel V2 via encounter
        v1 = NetworkNode(id="v1", label="Shadow Tanker", node_type=NodeType.VESSEL, risk_score=0.9)
        v2 = NetworkNode(id="v2", label="Cargo Carrier", node_type=NodeType.VESSEL, risk_score=0.1)
        graph.add_node(v1)
        graph.add_node(v2)
        graph.add_edge(NetworkEdge(source="v1", target="v2", edge_type=EdgeType.SHARED_ENCOUNTER, weight=1.0))

        graph.propagate_risk(decay=0.5, iterations=2)
        # v2's risk score should have increased significantly
        updated_v2 = graph.get_node("v2")
        self.assertGreater(updated_v2.risk_score, 0.1)
        self.assertLessEqual(updated_v2.risk_score, 1.0)


class MockScanRepo:
    def __init__(self, detections=None):
        self._detections = detections or []

    def get(self, scan_id):
        if scan_id == "valid_scan":
            scan = Scan(
                folder_name="valid_scan",
                bbox=BoundingBox(103.0, 1.0, 104.0, 2.0),
                acquisition=Acquisition(datetime.now(timezone.utc), "Sentinel-1", "sentinel-1-grd"),
                image_path="test.png",
                metadata={"detections": self._detections},
            )
            return scan
        return None


class MockAISRepo:
    def __init__(self, positions=None):
        self._positions = positions or []

    def get_vessel_positions(self, bbox, time_range=None):
        return self._positions


class TestBuildRelationshipGraphUseCase(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        self.mock_ais_repo = MockAISRepo()
        self.mock_scan_repo = MockScanRepo()
        self.use_case = BuildRelationshipGraph(
            ais_repository=self.mock_ais_repo,
            scan_repository=self.mock_scan_repo,
        )

    def test_build_graph_from_records_with_flag_port_route_and_operator(self):
        records = [
            {
                "mmsi": "671123456",  # Togo MID 671
                "name": "NEPTUNE 1",
                "latitude": 1.25,
                "longitude": 103.80,
                "speed_over_ground": 0.5,
                "destination": "SINGAPORE",
                "operator": "Shadow Maritime Ltd",
                "timestamp": self.t0.isoformat(),
            },
            {
                "mmsi": "563987654",  # Singapore MID 563
                "name": "OCEAN STAR",
                "latitude": 1.252,  # Close to NEPTUNE 1 (~200m)
                "longitude": 103.801,
                "speed_over_ground": 0.6,
                "destination": "ROTTERDAM",
                "operator": "Blue Horizon Shipping",
                "route": "sgsin__nlrtm",
                "timestamp": self.t0.isoformat(),
            },

        ]

        result = self.use_case.execute(records=records, encounter_proximity_m=1000.0)
        self.assertTrue(result["success"])
        self.assertIn("cytoscape", result)
        self.assertIn("graph", result)
        graph = result["graph"]

        node_ids = {n["id"] for n in graph["nodes"]}
        # Vessels
        self.assertIn("vessel_671123456", node_ids)
        self.assertIn("vessel_563987654", node_ids)
        # Flags (Togo and Singapore)
        self.assertIn("flag_TG", node_ids)
        self.assertIn("flag_SG", node_ids)
        # Ports
        self.assertTrue("port_SGSIN" in node_ids or "port_SINGAPORE" in node_ids)
        self.assertTrue("port_NLRTM" in node_ids or "port_ROTTERDAM" in node_ids)

        # Operators
        self.assertIn("operator_shadow_maritime_ltd", node_ids)
        self.assertIn("operator_blue_horizon_shipping", node_ids)
        # Route
        self.assertIn("route_sgsin__nlrtm", node_ids)
        # Encounter
        encounter_nodes = [n for n in graph["nodes"] if n["node_type"] == NodeType.ENCOUNTER.value]
        self.assertEqual(len(encounter_nodes), 1)

        # High risk flag should give higher initial risk to Togo vessel
        v_togo = next(n for n in graph["nodes"] if n["id"] == "vessel_671123456")
        self.assertGreater(v_togo["risk_score"], 0.5)

    def test_correlate_dark_detections_from_scan(self):
        sar_detections = [
            {
                "latitude": 1.30,
                "longitude": 103.90,
                "confidence": 0.92,
                "classification": "dark_vessel",
                "is_dark": True,
            }
        ]
        mock_scan_repo = MockScanRepo(detections=sar_detections)
        use_case = BuildRelationshipGraph(
            ais_repository=self.mock_ais_repo,
            scan_repository=mock_scan_repo,
        )

        result = use_case.execute(scan_id="valid_scan", records=[])
        self.assertTrue(result["success"])
        nodes = result["graph"]["nodes"]
        dark_nodes = [n for n in nodes if n["node_type"] == NodeType.DARK_DETECTION.value]
        self.assertEqual(len(dark_nodes), 1)
        self.assertEqual(dark_nodes[0]["risk_score"], 0.85)

    def test_focal_mmsi_ego_graph(self):
        records = [
            {"mmsi": "111111111", "name": "V1", "latitude": 1.0, "longitude": 103.0},
            {"mmsi": "222222222", "name": "V2", "latitude": 10.0, "longitude": 110.0},
        ]
        result = self.use_case.execute(records=records, focal_mmsi="111111111", max_depth=1)
        self.assertTrue(result["success"])
        node_ids = {n["id"] for n in result["graph"]["nodes"]}
        self.assertIn("vessel_111111111", node_ids)
        self.assertNotIn("vessel_222222222", node_ids)



if __name__ == "__main__":
    unittest.main()

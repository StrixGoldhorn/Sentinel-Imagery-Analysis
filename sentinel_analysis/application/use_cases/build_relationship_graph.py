"""Application Use Case for Building Maritime Intelligence Relationship Graphs.

Constructs multi-relational network graphs connecting vessels, encounters,
ports, flags of registry, commercial operators, and repeated navigation corridors.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.ais_repository import AISRepository
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.network_graph import (
    EdgeType,
    MaritimeNetworkGraph,
    NetworkEdge,
    NetworkNode,
    NodeType,
)

logger = logging.getLogger(__name__)

# ITU Maritime Identification Digits (MID) prefix mapping
MID_TO_FLAG: dict[str, tuple[str, str, float]] = {
    # Prefix: (Country Name, ISO Code, Base Threat Weight)
    "563": ("Singapore", "SG", 0.1),
    "351": ("Panama", "PA", 0.35),
    "352": ("Panama", "PA", 0.35),
    "353": ("Panama", "PA", 0.35),
    "354": ("Panama", "PA", 0.35),
    "355": ("Panama", "PA", 0.35),
    "356": ("Panama", "PA", 0.35),
    "357": ("Panama", "PA", 0.35),
    "370": ("Panama", "PA", 0.35),
    "371": ("Panama", "PA", 0.35),
    "372": ("Panama", "PA", 0.35),
    "636": ("Liberia", "LR", 0.30),
    "538": ("Marshall Islands", "MH", 0.25),
    "215": ("Malta", "MT", 0.25),
    "229": ("Malta", "MT", 0.25),
    "248": ("Malta", "MT", 0.25),
    "249": ("Malta", "MT", 0.25),
    "311": ("Bahamas", "BS", 0.20),
    "209": ("Cyprus", "CY", 0.25),
    "210": ("Cyprus", "CY", 0.25),
    "212": ("Cyprus", "CY", 0.25),
    "232": ("United Kingdom", "GB", 0.05),
    "233": ("United Kingdom", "GB", 0.05),
    "234": ("United Kingdom", "GB", 0.05),
    "235": ("United Kingdom", "GB", 0.05),
    "257": ("Norway", "NO", 0.05),
    "258": ("Norway", "NO", 0.05),
    "259": ("Norway", "NO", 0.05),
    "211": ("Germany", "DE", 0.05),
    "228": ("France", "FR", 0.05),
    "247": ("Italy", "IT", 0.05),
    "224": ("Spain", "ES", 0.05),
    "338": ("United States", "US", 0.05),
    "366": ("United States", "US", 0.05),
    "367": ("United States", "US", 0.05),
    "368": ("United States", "US", 0.05),
    "369": ("United States", "US", 0.05),
    "412": ("China", "CN", 0.15),
    "413": ("China", "CN", 0.15),
    "414": ("China", "CN", 0.15),
    "431": ("Japan", "JP", 0.05),
    "432": ("Japan", "JP", 0.05),
    "440": ("Republic of Korea", "KR", 0.05),
    "441": ("Republic of Korea", "KR", 0.05),
    "525": ("Indonesia", "ID", 0.20),
    "533": ("Malaysia", "MY", 0.15),
    "574": ("Vietnam", "VN", 0.20),
    "567": ("Thailand", "TH", 0.20),
    # High-Risk Flags of Convenience / Dark Fleet Hubs
    "671": ("Togo", "TG", 0.85),
    "613": ("Cameroon", "CM", 0.90),
    "620": ("Comoros", "KM", 0.85),
    "626": ("Gabon", "GA", 0.80),
    "518": ("Cook Islands", "CK", 0.75),
    "511": ("Palau", "PW", 0.80),
    "667": ("Sierra Leone", "SL", 0.85),
    "677": ("Tanzania", "TZ", 0.80),
}

STANDARD_PORTS = {
    "SGSIN": ("Port of Singapore", 1.29, 103.85, "SG"),
    "SINGAPORE": ("Port of Singapore", 1.29, 103.85, "SG"),
    "NLRTM": ("Port of Rotterdam", 51.95, 4.14, "NL"),
    "ROTTERDAM": ("Port of Rotterdam", 51.95, 4.14, "NL"),
    "BEANR": ("Port of Antwerp", 51.28, 4.33, "BE"),
    "ANTWERP": ("Port of Antwerp", 51.28, 4.33, "BE"),
    "CNYTN": ("Yantian / Shenzhen", 22.58, 114.28, "CN"),
    "CNSHG": ("Port of Shanghai", 31.23, 121.50, "CN"),
    "SHANGHAI": ("Port of Shanghai", 31.23, 121.50, "CN"),
    "KRPUS": ("Port of Busan", 35.10, 129.04, "KR"),
    "BUSAN": ("Port of Busan", 35.10, 129.04, "KR"),
    "HKHKG": ("Port of Hong Kong", 22.30, 114.17, "HK"),
    "HONG KONG": ("Port of Hong Kong", 22.30, 114.17, "HK"),
    "PAPAN": ("Panama Canal / Balboa", 8.95, -79.57, "PA"),
    "PANAMA": ("Panama Canal / Balboa", 8.95, -79.57, "PA"),
    "EGSUE": ("Suez Canal", 29.97, 32.56, "EG"),
    "SUEZ": ("Suez Canal", 29.97, 32.56, "EG"),
    "AEJEA": ("Jebel Ali / Dubai", 25.00, 55.06, "AE"),
    "JEBEL ALI": ("Jebel Ali / Dubai", 25.00, 55.06, "AE"),
}


def _haversine_dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


class BuildRelationshipGraph:
    """Extracts, links, and scores maritime entity relationship networks."""

    def __init__(
        self,
        ais_repository: Optional[AISRepository] = None,
        scan_repository: Optional[ScanRepository] = None,
    ) -> None:
        self._ais_repo = ais_repository
        self._scan_repo = scan_repository

    def execute(
        self,
        focal_mmsi: Optional[str] = None,
        scan_id: Optional[str] = None,
        records: Optional[list[dict[str, Any]]] = None,
        encounter_proximity_m: float = 800.0,
        max_depth: int = 2,
        propagate_threats: bool = True,
        max_nodes: int = 250,
    ) -> dict[str, Any]:
        """Construct the complete multi-relational maritime intelligence graph."""
        graph = MaritimeNetworkGraph(metadata={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "focal_mmsi": focal_mmsi,
            "scan_id": scan_id,
        })

        # 1. Gather AIS records
        vessel_records = self._resolve_records(records, scan_id)

        # 2. Extract Vessel Nodes
        vessels_by_mmsi: dict[str, dict[str, Any]] = {}
        for rec in vessel_records:
            mmsi = str(rec.get("mmsi") or rec.get("vessel_id") or "").strip()
            if not mmsi or mmsi == "0":
                continue
            if mmsi not in vessels_by_mmsi:
                vessels_by_mmsi[mmsi] = rec
            else:
                # Merge newer positions
                vessels_by_mmsi[mmsi].update(rec)

        for mmsi, rec in vessels_by_mmsi.items():
            name = rec.get("name") or rec.get("vessel_name") or f"Vessel-{mmsi[-4:]}"
            ship_type = rec.get("vessel_type") or rec.get("ship_type") or "Unknown"
            length = rec.get("length") or rec.get("length_m") or 0.0
            beam = rec.get("width") or rec.get("width_m") or 0.0
            speed = float(rec.get("speed") or 0.0)
            heading = rec.get("heading") or rec.get("course")
            lat = rec.get("latitude") or rec.get("lat")
            lon = rec.get("longitude") or rec.get("lon")
            is_dark = bool(rec.get("is_dark", False))
            anomaly_flags = rec.get("anomaly_flags") or []

            base_risk = 0.85 if is_dark else (0.6 if anomaly_flags else 0.15)
            if rec.get("suspicious"):
                base_risk = max(base_risk, 0.75)

            v_node = NetworkNode(
                id=f"vessel_{mmsi}",
                label=f"{name} ({mmsi})",
                node_type=NodeType.VESSEL,
                risk_score=base_risk,
                properties={
                    "mmsi": mmsi,
                    "name": name,
                    "ship_type": ship_type,
                    "length": length,
                    "beam": beam,
                    "speed_knots": speed,
                    "heading": heading,
                    "latitude": lat,
                    "longitude": lon,
                    "is_dark": is_dark,
                    "destination": rec.get("destination"),
                    "operator": rec.get("operator") or rec.get("owner"),
                },
            )
            graph.add_node(v_node)

            # 3. Extract Flag Node
            self._link_flag(graph, v_node, mmsi, rec)

            # 4. Extract Port Node from Destination
            self._link_port(graph, v_node, rec)

            # 5. Extract Operator / Owner Node
            self._link_operator(graph, v_node, rec)

            # 6. Extract Route Node
            self._link_route(graph, v_node, rec)

        # 7. Extract Shared Encounters (Rendezvous / STS Transfers)
        self._extract_encounters(graph, vessels_by_mmsi, encounter_proximity_m)

        # 8. Integrate SAR Detections if scan_id provided
        if scan_id and self._scan_repo:
            self._link_scan_detections(graph, scan_id)

        # 9. Threat & Risk Propagation
        if propagate_threats:
            graph.propagate_risk(decay=0.40, iterations=2)

        # 10. Filter by Subgraph if focal MMSI requested
        result_graph = graph
        if focal_mmsi:
            focal_id = f"vessel_{focal_mmsi}" if not str(focal_mmsi).startswith("vessel_") else str(focal_mmsi)
            if graph.get_node(focal_id):
                result_graph = graph.get_subgraph(focal_id, max_depth=max_depth)

        # Limit total nodes if exceeded
        if len(result_graph.nodes) > max_nodes:
            # Keep top nodes by degree and risk
            sorted_nodes = sorted(result_graph.nodes, key=lambda n: (n.risk_score, n.degree), reverse=True)[:max_nodes]
            keep_ids = {n.id for n in sorted_nodes}
            result_graph.nodes = sorted_nodes
            result_graph.edges = [e for e in result_graph.edges if e.source in keep_ids and e.target in keep_ids]

        g_dict = result_graph.to_dict()
        return {
            "success": True,
            "graph": g_dict,
            "nodes": g_dict["nodes"],
            "edges": g_dict["edges"],
            "cytoscape": result_graph.to_cytoscape(),
            "metrics": result_graph.calculate_metrics(),
            "total_nodes": len(result_graph.nodes),
            "total_edges": len(result_graph.edges),
        }


    # -------------------------------------------------------------------------
    # Relationship Linking Helpers
    # -------------------------------------------------------------------------
    def _link_flag(self, graph: MaritimeNetworkGraph, v_node: NetworkNode, mmsi: str, rec: dict[str, Any]) -> None:
        flag_code = rec.get("flag") or rec.get("country")
        country_name = None
        base_threat = 0.10

        if not flag_code and len(mmsi) >= 3:
            mid = mmsi[:3]
            if mid in MID_TO_FLAG:
                country_name, flag_code, base_threat = MID_TO_FLAG[mid]

        if not country_name and flag_code:
            country_name = str(flag_code).upper()

        if country_name and flag_code:
            flag_id = f"flag_{flag_code.upper()}"
            flag_node = NetworkNode(
                id=flag_id,
                label=f"Flag: {country_name} ({flag_code.upper()})",
                node_type=NodeType.FLAG,
                risk_score=base_threat,
                properties={
                    "country": country_name,
                    "code": flag_code.upper(),
                    "is_foc": base_threat >= 0.70,
                },
            )
            graph.add_node(flag_node)
            if base_threat >= 0.70:
                v_node.risk_score = max(v_node.risk_score, base_threat * 0.75)
            graph.add_edge(NetworkEdge(
                source=v_node.id,
                target=flag_id,
                edge_type=EdgeType.FLAGGED_AS,
                weight=1.0,
                properties={"since": rec.get("flag_registered_date")},
            ))


    def _link_port(self, graph: MaritimeNetworkGraph, v_node: NetworkNode, rec: dict[str, Any]) -> None:
        dest = str(rec.get("destination") or "").strip().upper()
        if not dest or dest in ("UNKNOWN", "TBD", "OPEN SEA", "FOR ORDERS", "OFFSHORE"):
            return

        matched_code = None
        matched_info = None
        for code, info in STANDARD_PORTS.items():
            if code in dest or dest in code:
                matched_code = code
                matched_info = info
                break

        port_id = f"port_{matched_code or dest.replace(' ', '_')}"
        port_name = matched_info[0] if matched_info else f"Port: {dest}"
        port_node = NetworkNode(
            id=port_id,
            label=port_name,
            node_type=NodeType.PORT,
            risk_score=0.10,
            properties={
                "name": port_name,
                "unlocode": matched_code if matched_code and len(matched_code) == 5 else None,
                "latitude": matched_info[1] if matched_info else None,
                "longitude": matched_info[2] if matched_info else None,
            },
        )
        graph.add_node(port_node)
        graph.add_edge(NetworkEdge(
            source=v_node.id,
            target=port_id,
            edge_type=EdgeType.VISITED_PORT,
            weight=1.0,
            properties={"reported_destination": dest},
        ))

    def _link_operator(self, graph: MaritimeNetworkGraph, v_node: NetworkNode, rec: dict[str, Any]) -> None:
        op_name = rec.get("operator") or rec.get("owner") or rec.get("manager")
        if not op_name:
            # Check callsign prefix or group
            cs = rec.get("callsign")
            if cs and len(cs) >= 3 and rec.get("fleet_id"):
                op_name = f"Fleet {rec['fleet_id']}"
        if not op_name:
            return

        op_name_clean = str(op_name).strip()
        op_id = f"operator_{op_name_clean.lower().replace(' ', '_')}"
        op_node = NetworkNode(
            id=op_id,
            label=f"Operator: {op_name_clean}",
            node_type=NodeType.OPERATOR,
            risk_score=0.20 if not rec.get("operator_sanctioned") else 0.95,
            properties={
                "name": op_name_clean,
                "sanctioned": bool(rec.get("operator_sanctioned", False)),
            },
        )
        graph.add_node(op_node)
        graph.add_edge(NetworkEdge(
            source=v_node.id,
            target=op_id,
            edge_type=EdgeType.OPERATED_BY,
            weight=1.0,
            properties={"role": "Beneficial Owner / Operator"},
        ))

    def _link_route(self, graph: MaritimeNetworkGraph, v_node: NetworkNode, rec: dict[str, Any]) -> None:
        route_name = rec.get("route") or rec.get("corridor") or rec.get("traffic_lane")
        if not route_name:
            # Check if destination exists
            dest = rec.get("destination")
            if dest and len(str(dest)) >= 4:
                route_name = f"Corridor-to-{str(dest).strip().upper()[:8]}"
        if not route_name:
            return

        route_id = f"route_{str(route_name).lower().replace(' ', '_')}"
        route_node = NetworkNode(
            id=route_id,
            label=f"Route: {route_name}",
            node_type=NodeType.ROUTE,
            risk_score=0.15,
            properties={"name": route_name},
        )
        graph.add_node(route_node)
        graph.add_edge(NetworkEdge(
            source=v_node.id,
            target=route_id,
            edge_type=EdgeType.TRAVELED_ROUTE,
            weight=1.0,
            properties={"frequency": rec.get("route_frequency", 1)},
        ))

    def _extract_encounters(
        self,
        graph: MaritimeNetworkGraph,
        vessels: dict[str, dict[str, Any]],
        max_dist_m: float,
    ) -> None:
        mmsi_list = list(vessels.keys())
        n = len(mmsi_list)
        for i in range(n):
            m1 = mmsi_list[i]
            r1 = vessels[m1]
            lat1 = r1.get("latitude") or r1.get("lat")
            lon1 = r1.get("longitude") or r1.get("lon")
            if lat1 is None or lon1 is None:
                continue

            for j in range(i + 1, n):
                m2 = mmsi_list[j]
                r2 = vessels[m2]
                lat2 = r2.get("latitude") or r2.get("lat")
                lon2 = r2.get("longitude") or r2.get("lon")
                if lat2 is None or lon2 is None:
                    continue

                dist = _haversine_dist(float(lat1), float(lon1), float(lat2), float(lon2))
                if dist <= max_dist_m:
                    # Rendezvous or proximity encounter
                    s1 = float(r1.get("speed") or 0.0)
                    s2 = float(r2.get("speed") or 0.0)
                    is_sts = (s1 <= 4.0 and s2 <= 4.0 and dist <= 300.0)
                    enc_id = f"encounter_{min(m1, m2)}_{max(m1, m2)}"
                    enc_label = f"STS Transfer ({int(dist)}m)" if is_sts else f"Encounter ({int(dist)}m)"

                    enc_risk = 0.85 if is_sts else 0.45
                    enc_node = NetworkNode(
                        id=enc_id,
                        label=enc_label,
                        node_type=NodeType.ENCOUNTER,
                        risk_score=enc_risk,
                        properties={
                            "distance_m": round(dist, 1),
                            "is_sts": is_sts,
                            "speed_1_kts": s1,
                            "speed_2_kts": s2,
                            "latitude": (float(lat1) + float(lat2)) / 2.0,
                            "longitude": (float(lon1) + float(lon2)) / 2.0,
                        },
                    )
                    graph.add_node(enc_node)

                    # Connect both vessels to encounter
                    graph.add_edge(NetworkEdge(
                        source=f"vessel_{m1}",
                        target=enc_id,
                        edge_type=EdgeType.ENCOUNTERED,
                        weight=1.5 if is_sts else 1.0,
                    ))
                    graph.add_edge(NetworkEdge(
                        source=f"vessel_{m2}",
                        target=enc_id,
                        edge_type=EdgeType.ENCOUNTERED,
                        weight=1.5 if is_sts else 1.0,
                    ))

                    # Connect vessels directly with SHARED_ENCOUNTER
                    graph.add_edge(NetworkEdge(
                        source=f"vessel_{m1}",
                        target=f"vessel_{m2}",
                        edge_type=EdgeType.SHARED_ENCOUNTER,
                        weight=2.0 if is_sts else 1.0,
                        properties={"distance_m": round(dist, 1), "is_sts": is_sts},
                    ))

    def _link_scan_detections(self, graph: MaritimeNetworkGraph, scan_id: str) -> None:
        if not self._scan_repo:
            return
        scan = self._scan_repo.get(scan_id)
        if not scan:
            return

        dets = []
        if scan.metadata and isinstance(scan.metadata.get("detections"), list):
            dets = scan.metadata["detections"]

        if not dets and scan.image_path:
            scan_dir = Path(scan.image_path).parent
            det_file = scan_dir / "detection_results.json"
            if det_file.is_file():
                try:
                    data = json.loads(det_file.read_text(encoding="utf-8"))
                    dets = data.get("detections", [])
                except Exception as exc:
                    logger.debug("Error reading detection file: %s", exc)

        for idx, d in enumerate(dets):
            if not isinstance(d, dict):
                continue
            is_dark = bool(d.get("is_dark", True)) or d.get("classification") == "dark_vessel"
            lat = d.get("latitude")
            lon = d.get("longitude")
            det_id = f"det_{scan_id}_{idx}"

            if is_dark:
                d_node = NetworkNode(
                    id=det_id,
                    label=f"Dark Target #{idx} (SAR)",
                    node_type=NodeType.DARK_DETECTION,
                    risk_score=float(d.get("risk_score") or 0.85),
                    properties={
                        "detection_index": idx,
                        "scan_id": scan_id,
                        "length_m": d.get("length_m"),
                        "confidence": d.get("confidence"),
                        "latitude": lat,
                        "longitude": lon,
                    },
                )
                graph.add_node(d_node)
                # Connect to nearby vessels as potential candidates / correlation
                if lat is not None and lon is not None:
                    for n in list(graph.nodes):
                        if n.node_type == NodeType.VESSEL:
                            v_lat = n.properties.get("latitude")
                            v_lon = n.properties.get("longitude")
                            if v_lat is not None and v_lon is not None:
                                dist = _haversine_dist(float(lat), float(lon), float(v_lat), float(v_lon))
                                if dist <= 5000.0:  # Within 5km
                                    graph.add_edge(NetworkEdge(
                                        source=det_id,
                                        target=n.id,
                                        edge_type=EdgeType.CORRELATED_TO,
                                        weight=0.8,
                                        properties={"separation_m": round(dist, 1)},
                                    ))


    def _resolve_records(
        self,
        records: Optional[list[dict[str, Any]]],
        scan_id: Optional[str],
    ) -> list[dict[str, Any]]:
        if records is not None:
            return list(records)

        if self._ais_repo and hasattr(self._ais_repo, "get_vessel_positions"):
            try:
                fetched = self._ais_repo.get_vessel_positions(limit=300, latest_only=True)
                if fetched:
                    return fetched
            except Exception as exc:
                logger.debug("Failed querying ais repo for relationship graph: %s", exc)

        return []

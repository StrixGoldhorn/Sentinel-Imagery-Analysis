"""Use case for side-by-side detection comparison across algorithms (CFAR, Classical CV, and ONNX)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Sequence

from sentinel_analysis.application.ports.detection import DetectionResult, ShipDetector
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.algorithm_comparison import (
    AlgorithmMetrics,
    ConsensusContact,
    DetectionComparisonResult,
    PairwiseMatch,
    calculate_box_iou,
)
from sentinel_analysis.domain.entities import ShipDetection

logger = logging.getLogger(__name__)


def _to_detection_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if isinstance(item, ShipDetection) or hasattr(item, "x"):
        return {
            "x": int(getattr(item, "x", 0)),
            "y": int(getattr(item, "y", 0)),
            "width": int(getattr(item, "width", 0)),
            "height": int(getattr(item, "height", 0)),
            "confidence": getattr(item, "confidence", 0.8),
            "length": getattr(item, "length", None),
            "beam": getattr(item, "beam", None),
            "angle": getattr(item, "angle", None),
            "vessel_class": getattr(item, "vessel_class", None),
        }
    return dict(item)


class CompareDetectionAlgorithms:
    """Evaluates and compares ship detection algorithms side-by-side."""

    def __init__(
        self,
        scan_repository: Optional[ScanRepository] = None,
        cfar_detector: Optional[ShipDetector] = None,
        classical_detector: Optional[ShipDetector] = None,
        onnx_detector: Optional[ShipDetector] = None,
    ) -> None:
        self._scan_repository = scan_repository
        self._cfar_detector = cfar_detector
        self._classical_detector = classical_detector
        self._onnx_detector = onnx_detector

    def execute(
        self,
        scan_id: Optional[str] = None,
        image_path: Optional[Path | str] = None,
        dem_path: Optional[Path | str] = None,
        detections_by_algo: Optional[dict[str, Sequence[Any]]] = None,
        iou_threshold: float = 0.25,
        detection_threshold: int = 40,
    ) -> DetectionComparisonResult:
        """Run or compare detections across CFAR, Classical CV, and ONNX detectors."""
        resolved_image_path = Path(image_path) if image_path else None
        resolved_dem_path = Path(dem_path) if dem_path else None

        if resolved_image_path is None and scan_id and self._scan_repository is not None:
            scan_obj = self._scan_repository.get_scan(scan_id)
            if scan_obj is not None:
                if scan_obj.image_path:
                    resolved_image_path = Path(scan_obj.image_path)
                if resolved_dem_path is None and getattr(scan_obj, "metadata", None):
                    dem_str = scan_obj.metadata.get("dem_path")
                    if dem_str:
                        resolved_dem_path = Path(dem_str)

        # 1. Gather raw detections for each algorithm
        algo_detections: dict[str, list[dict[str, Any]]] = {
            "cfar": [],
            "classical_cv": [],
            "onnx": [],
        }

        if detections_by_algo is not None:
            for algo_key in ("cfar", "classical_cv", "onnx"):
                raw_list = detections_by_algo.get(algo_key, [])
                algo_detections[algo_key] = [_to_detection_dict(d) for d in raw_list]
        elif resolved_image_path is not None and resolved_image_path.exists():
            # Run CFAR detector
            if self._cfar_detector is not None:
                try:
                    res = self._cfar_detector.detect(
                        resolved_image_path,
                        dem_path=resolved_dem_path,
                        threshold=detection_threshold,
                    )
                    dets = res.detections if hasattr(res, "detections") else res[0]
                    algo_detections["cfar"] = [_to_detection_dict(d) for d in dets]
                except Exception as exc:
                    logger.warning("CFAR detection failed: %s", exc)

            # Run Classical CV detector
            if self._classical_detector is not None:
                try:
                    res = self._classical_detector.detect(
                        resolved_image_path,
                        dem_path=resolved_dem_path,
                        threshold=detection_threshold,
                    )
                    dets = res.detections if hasattr(res, "detections") else res[0]
                    algo_detections["classical_cv"] = [_to_detection_dict(d) for d in dets]
                except Exception as exc:
                    logger.warning("Classical CV detection failed: %s", exc)

            # Run ONNX detector
            if self._onnx_detector is not None:
                try:
                    res = self._onnx_detector.detect(
                        resolved_image_path,
                        dem_path=resolved_dem_path,
                        threshold=detection_threshold,
                    )
                    dets = res.detections if hasattr(res, "detections") else res[0]
                    algo_detections["onnx"] = [_to_detection_dict(d) for d in dets]
                except Exception as exc:
                    logger.warning("ONNX detection failed: %s", exc)

        # 2. Compute individual algorithm metrics
        algorithm_metrics: dict[str, AlgorithmMetrics] = {}
        for algo_key, dets in algo_detections.items():
            count = len(dets)
            confs = [float(d.get("confidence", 0.5)) for d in dets if d.get("confidence") is not None]
            lengths = [float(d.get("length")) for d in dets if d.get("length") is not None]
            beams = [float(d.get("beam")) for d in dets if d.get("beam") is not None]

            mean_conf = sum(confs) / max(1, len(confs)) if confs else 0.0
            mean_len = sum(lengths) / max(1, len(lengths)) if lengths else None
            mean_bm = sum(beams) / max(1, len(beams)) if beams else None

            algorithm_metrics[algo_key] = AlgorithmMetrics(
                algorithm=algo_key,
                detection_count=count,
                mean_confidence=mean_conf,
                mean_length_m=mean_len,
                mean_beam_m=mean_bm,
            )

        # 3. Compute pairwise matches & Jaccard index
        pairs = [("cfar", "classical_cv"), ("cfar", "onnx"), ("classical_cv", "onnx")]
        pairwise_metrics: dict[str, PairwiseMatch] = {}

        for a_key, b_key in pairs:
            list_a = algo_detections[a_key]
            list_b = algo_detections[b_key]
            matched_count, mean_iou = self._match_pairs(list_a, list_b, iou_threshold)
            unmatched_a = len(list_a) - matched_count
            unmatched_b = len(list_b) - matched_count
            union = len(list_a) + len(list_b) - matched_count
            jaccard = matched_count / max(1, union) if union > 0 else (1.0 if not list_a and not list_b else 0.0)

            pair_name = f"{a_key}_vs_{b_key}"
            pairwise_metrics[pair_name] = PairwiseMatch(
                algo_a=a_key,
                algo_b=b_key,
                matched_count=matched_count,
                unmatched_a_count=unmatched_a,
                unmatched_b_count=unmatched_b,
                jaccard_similarity=jaccard,
                mean_iou=mean_iou,
            )

        # 4. Multi-algorithm Consensus Clustering
        consensus_contacts = self._build_consensus_clusters(algo_detections, iou_threshold)

        three_way = sum(1 for c in consensus_contacts if len(c.detected_by) == 3)
        two_way = sum(1 for c in consensus_contacts if len(c.detected_by) == 2)
        unique_counts = {
            algo: sum(1 for c in consensus_contacts if c.detected_by == [algo])
            for algo in ("cfar", "classical_cv", "onnx")
        }

        # 5. Descriptive Summary
        summary = (
            f"Multi-Algorithm Evaluation: {len(consensus_contacts)} total unique target(s). "
            f"High Consensus (3/3): {three_way}, Moderate Consensus (2/3): {two_way}. "
            f"Unique detections: CFAR={unique_counts['cfar']}, Classical CV={unique_counts['classical_cv']}, ONNX={unique_counts['onnx']}."
        )

        return DetectionComparisonResult(
            scan_id=scan_id,
            algorithm_metrics=algorithm_metrics,
            pairwise_metrics=pairwise_metrics,
            three_way_consensus_count=three_way,
            two_way_consensus_count=two_way,
            unique_counts=unique_counts,
            total_unique_targets=len(consensus_contacts),
            consensus_contacts=consensus_contacts,
            summary=summary,
        )

    def _match_pairs(
        self,
        list_a: list[dict[str, Any]],
        list_b: list[dict[str, Any]],
        iou_thresh: float,
    ) -> tuple[int, float]:
        """Greedy IoU matching between two lists of detections."""
        if not list_a or not list_b:
            return 0, 0.0

        matched_b: set[int] = set()
        matched_ious: list[float] = []

        for det_a in list_a:
            best_iou = -1.0
            best_idx = -1
            for idx_b, det_b in enumerate(list_b):
                if idx_b in matched_b:
                    continue
                iou = calculate_box_iou(det_a, det_b)
                if iou >= iou_thresh and iou > best_iou:
                    best_iou = iou
                    best_idx = idx_b

            if best_idx >= 0:
                matched_b.add(best_idx)
                matched_ious.append(best_iou)

        mean_iou = sum(matched_ious) / max(1, len(matched_ious)) if matched_ious else 0.0
        return len(matched_ious), mean_iou

    def _build_consensus_clusters(
        self,
        algo_detections: dict[str, list[dict[str, Any]]],
        iou_thresh: float,
    ) -> list[ConsensusContact]:
        """Cluster detections across the 3 algorithms into unified consensus contacts."""
        cfar_dets = list(algo_detections.get("cfar", []))
        cv_dets = list(algo_detections.get("classical_cv", []))
        onnx_dets = list(algo_detections.get("onnx", []))

        used_cv: set[int] = set()
        used_onnx: set[int] = set()

        contacts: list[ConsensusContact] = []
        target_idx = 1

        # Match from CFAR outwards
        for c_det in cfar_dets:
            matched_algos = ["cfar"]
            det_map = {"cfar": c_det}

            # Match with CV
            best_cv_idx, best_cv_iou = -1, -1.0
            for i, cv_d in enumerate(cv_dets):
                if i in used_cv:
                    continue
                iou = calculate_box_iou(c_det, cv_d)
                if iou >= iou_thresh and iou > best_cv_iou:
                    best_cv_iou = iou
                    best_cv_idx = i

            if best_cv_idx >= 0:
                used_cv.add(best_cv_idx)
                matched_algos.append("classical_cv")
                det_map["classical_cv"] = cv_dets[best_cv_idx]

            # Match with ONNX
            best_onnx_idx, best_onnx_iou = -1, -1.0
            for j, onnx_d in enumerate(onnx_dets):
                if j in used_onnx:
                    continue
                iou = calculate_box_iou(c_det, onnx_d)
                if iou >= iou_thresh and iou > best_onnx_iou:
                    best_onnx_iou = iou
                    best_onnx_idx = j

            if best_onnx_idx >= 0:
                used_onnx.add(best_onnx_idx)
                matched_algos.append("onnx")
                det_map["onnx"] = onnx_dets[best_onnx_idx]

            contacts.append(self._make_contact(target_idx, matched_algos, det_map))
            target_idx += 1

        # Match remaining CV with remaining ONNX
        for i, cv_d in enumerate(cv_dets):
            if i in used_cv:
                continue
            matched_algos = ["classical_cv"]
            det_map = {"classical_cv": cv_d}

            best_onnx_idx, best_onnx_iou = -1, -1.0
            for j, onnx_d in enumerate(onnx_dets):
                if j in used_onnx:
                    continue
                iou = calculate_box_iou(cv_d, onnx_d)
                if iou >= iou_thresh and iou > best_onnx_iou:
                    best_onnx_iou = iou
                    best_onnx_idx = j

            if best_onnx_idx >= 0:
                used_onnx.add(best_onnx_idx)
                matched_algos.append("onnx")
                det_map["onnx"] = onnx_dets[best_onnx_idx]

            contacts.append(self._make_contact(target_idx, matched_algos, det_map))
            target_idx += 1

        # Remaining ONNX hits
        for j, onnx_d in enumerate(onnx_dets):
            if j in used_onnx:
                continue
            contacts.append(
                self._make_contact(target_idx, ["onnx"], {"onnx": onnx_d})
            )
            target_idx += 1

        return contacts

    def _make_contact(
        self,
        index: int,
        detected_by: list[str],
        det_map: dict[str, dict[str, Any]],
    ) -> ConsensusContact:
        contact_id = f"TGT-{index:03d}"
        if len(detected_by) == 3:
            agreement_level = "HIGH"
        elif len(detected_by) == 2:
            agreement_level = "MODERATE"
        else:
            agreement_level = "SINGLE_ALGORITHM"

        # Consensus box (average coordinates)
        boxes = [d for d in det_map.values()]
        avg_x = int(round(sum(b["x"] for b in boxes) / len(boxes)))
        avg_y = int(round(sum(b["y"] for b in boxes) / len(boxes)))
        avg_w = int(round(sum(b["width"] for b in boxes) / len(boxes)))
        avg_h = int(round(sum(b["height"] for b in boxes) / len(boxes)))

        confs = [float(b.get("confidence", 0.5)) for b in boxes if b.get("confidence") is not None]
        mean_conf = sum(confs) / max(1, len(confs)) if confs else 0.5

        return ConsensusContact(
            contact_id=contact_id,
            agreement_level=agreement_level,
            detected_by=detected_by,
            consensus_box={"x": avg_x, "y": avg_y, "width": avg_w, "height": avg_h},
            mean_confidence=mean_conf,
            algorithm_detections=det_map,
        )

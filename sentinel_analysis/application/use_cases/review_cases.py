"""Use cases for the analyst review workflow, review queue, and dataset generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sentinel_analysis.application.ports.review_repository import ReviewRepository
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.coordinates import project_detection_coordinates
from sentinel_analysis.domain.review import (
    ReviewAction,
    ReviewBox,
    ReviewDisposition,
    ReviewHistoryEntry,
    ReviewRecord,
    calculate_iou,
)


class SubmitAnalystReview:
    """Submits or updates an analyst review on a detected contact and writes an immutable audit record."""

    def __init__(self, review_repo: ReviewRepository, scan_repo: ScanRepository | None = None) -> None:
        self.review_repo = review_repo
        self.scan_repo = scan_repo

    def execute(
        self,
        scan_id: str,
        detection_idx: int,
        disposition: str,
        reviewer_id: str = "analyst",
        corrected_bbox: dict[str, Any] | None = None,
        comments: str | None = None,
        confidence: float | None = None,
        original_bbox: dict[str, Any] | None = None,
        vessel_class: str | None = None,
        reason_codes: list[str] | None = None,
    ) -> ReviewRecord:
        clean_disp = ReviewDisposition.from_str(disposition).value
        reviewer = str(reviewer_id or "analyst").strip()
        review_id = f"{scan_id}_{detection_idx}"

        existing = self.review_repo.get_by_scan_and_index(scan_id, detection_idx)
        now = datetime.now(timezone.utc)

        # Normalize bounding boxes
        norm_orig = dict(original_bbox) if original_bbox else (existing.original_bbox if existing else {})
        norm_corr = dict(corrected_bbox) if corrected_bbox else (existing.corrected_bbox if existing else None)

        # If original bbox not provided and no existing record, try to read from scan detections.json
        if not norm_orig and self.scan_repo is not None:
            scan = self.scan_repo.get(scan_id)
            if scan and hasattr(scan, "image_path"):
                det_path = Path(scan.image_path).parent / "detection_results.json"
                if det_path.is_file():
                    try:
                        data = json.loads(det_path.read_text(encoding="utf-8"))
                        dets = data.get("detections", [])
                        if 0 <= detection_idx < len(dets):
                            d = dets[detection_idx]
                            norm_orig = {
                                "x": float(d.get("x", 0)),
                                "y": float(d.get("y", 0)),
                                "width": float(d.get("width", 50)),
                                "height": float(d.get("height", 50)),
                            }
                            if confidence is None:
                                confidence = float(d.get("confidence", 0.0))
                            if vessel_class is None:
                                vessel_class = d.get("vessel_class")
                            if reason_codes is None:
                                reason_codes = d.get("reason_codes", [])
                    except Exception:
                        pass

        det_conf = float(confidence if confidence is not None else (existing.confidence if existing else 0.0))
        det_class = vessel_class if vessel_class is not None else (existing.vessel_class if existing else None)
        det_reasons = list(reason_codes) if reason_codes is not None else (list(existing.reason_codes) if existing else [])

        # Determine the action type
        if existing is None:
            action = ReviewAction.CREATED.value
        elif norm_corr != existing.corrected_bbox and norm_corr is not None:
            action = ReviewAction.BOX_CORRECTED.value
        elif clean_disp == ReviewDisposition.ACCEPTED.value:
            action = ReviewAction.ACCEPTED.value
        elif clean_disp == ReviewDisposition.REJECTED.value:
            action = ReviewAction.REJECTED.value
        elif clean_disp == ReviewDisposition.UNCERTAIN.value:
            action = ReviewAction.MARKED_UNCERTAIN.value
        else:
            action = ReviewAction.UPDATED.value

        record = ReviewRecord(
            review_id=review_id,
            scan_id=scan_id,
            detection_idx=detection_idx,
            disposition=clean_disp,
            reviewer_id=reviewer,
            original_bbox=norm_orig,
            corrected_bbox=norm_corr,
            confidence=det_conf,
            vessel_class=det_class,
            comments=comments if comments is not None else (existing.comments if existing else ""),
            reason_codes=det_reasons,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        self.review_repo.save(record)

        # Create immutable history entry
        history_entry = ReviewHistoryEntry(
            id=None,
            review_id=review_id,
            action=action,
            disposition=clean_disp,
            reviewer_id=reviewer,
            corrected_bbox=norm_corr,
            comments=comments or "",
            metadata={
                "confidence": det_conf,
                "vessel_class": det_class,
                "previous_disposition": existing.disposition if existing else None,
            },
            timestamp=now,
        )
        self.review_repo.add_history(history_entry)

        # Return refreshed record with complete history
        updated = self.review_repo.get(review_id)
        return updated or record


def enrich_review_geo(record: ReviewRecord, scan_repo: ScanRepository | None) -> ReviewRecord:
    """Enrich review record with latitude, longitude, and geo bounding box from scan data if missing."""
    if record.effective_lat is not None and record.effective_lng is not None:
        return record
    if scan_repo is None:
        return record
    try:
        scan = scan_repo.get(record.scan_id)
        if not scan:
            return record
        det_path = None
        if hasattr(scan, "image_path") and scan.image_path:
            img_p = Path(scan.image_path)
            for cp in [
                img_p.parent / "detection_results.json",
                img_p.parent.parent / "detection_results.json",
                img_p.parent / "images" / "detection_results.json",
            ]:
                if cp.is_file():
                    det_path = cp
                    break
        if det_path is None and hasattr(scan_repo, "root"):
            cp = Path(scan_repo.root) / record.scan_id / "detection_results.json"
            if cp.is_file():
                det_path = cp

        if det_path is not None and det_path.is_file():
            data = json.loads(det_path.read_text(encoding="utf-8"))
            dets = data.get("detections", [])
            if 0 <= record.detection_idx < len(dets):
                d = dets[record.detection_idx]
                record.lat = d.get("lat") if d.get("lat") is not None else d.get("latitude")
                record.lng = d.get("lng") if d.get("lng") is not None else d.get("longitude")
                record.geo_bbox = d.get("geo_bbox")
                if (record.lat is None or record.lng is None) and hasattr(scan, "bbox") and scan.bbox:
                    coords = project_detection_coordinates(d, bbox=scan.bbox)
                    record.lat = coords.get("lat")
                    record.lng = coords.get("lng")
                    record.geo_bbox = coords.get("geo_bbox")
                return record

        # Fallback to scan bbox interpolation if lat/lng not in detection json
        if record.lat is None and hasattr(scan, "bbox") and scan.bbox:
            b = scan.bbox
            record.lat = (b.min_latitude + b.max_latitude) / 2.0
            record.lng = (b.min_longitude + b.max_longitude) / 2.0
    except Exception:
        pass
    return record


class ListReviewQueue:
    """Lists contact reviews with filtering, pagination, summary stats, and auto-discovery of unreviewed scans."""

    def __init__(self, review_repo: ReviewRepository, scan_repo: ScanRepository | None = None) -> None:
        self.review_repo = review_repo
        self.scan_repo = scan_repo

    def sync_scans_to_queue(self, max_scans: int = 15) -> int:
        """Scan available scans on disk and populate pending review records for any unqueued detections."""
        if self.scan_repo is None:
            return 0
        added = 0
        scans = self.scan_repo.list()
        scans_to_check = scans[:max_scans]
        for scan in scans_to_check:
            scan_id = getattr(scan, "folder_name", "")
            if not scan_id:
                continue
            image_path = getattr(scan, "image_path", None)
            if not image_path:
                continue
            img_p = Path(image_path)
            candidate_paths = [
                img_p.parent / "detection_results.json",
                img_p.parent.parent / "detection_results.json",
                img_p.parent / "images" / "detection_results.json",
            ]
            if hasattr(self.scan_repo, "root"):
                candidate_paths.append(Path(self.scan_repo.root) / scan_id / "detection_results.json")
            det_path = None
            for p in candidate_paths:
                if p.is_file():
                    det_path = p
                    break
            if det_path is None:
                continue
            try:
                data = json.loads(det_path.read_text(encoding="utf-8"))
                detections = data.get("detections", [])
                for idx, det in enumerate(detections):
                    existing = self.review_repo.get_by_scan_and_index(scan_id, idx)
                    if existing is None:
                        lat = det.get("lat") if det.get("lat") is not None else det.get("latitude")
                        lng = det.get("lng") if det.get("lng") is not None else det.get("longitude")
                        geo_bbox = det.get("geo_bbox")
                        if (lat is None or lng is None) and hasattr(scan, "bbox") and scan.bbox:
                            coords = project_detection_coordinates(det, bbox=scan.bbox)
                            lat = coords.get("lat")
                            lng = coords.get("lng")
                            geo_bbox = coords.get("geo_bbox")
                        orig_box = {
                            "x": float(det.get("x", 0)),
                            "y": float(det.get("y", 0)),
                            "width": float(det.get("width", 50)),
                            "height": float(det.get("height", 50)),
                            "lat": lat,
                            "lng": lng,
                            "geo_bbox": geo_bbox,
                        }
                        record = ReviewRecord(
                            review_id=f"{scan_id}_{idx}",
                            scan_id=scan_id,
                            detection_idx=idx,
                            disposition=ReviewDisposition.PENDING.value,
                            reviewer_id=None,
                            original_bbox=orig_box,
                            corrected_bbox=None,
                            confidence=float(det.get("confidence", 0.0)),
                            vessel_class=det.get("vessel_class"),
                            comments=None,
                            reason_codes=list(det.get("reason_codes", [])),
                            lat=lat,
                            lng=lng,
                            geo_bbox=geo_bbox,
                        )
                        self.review_repo.save(record)
                        added += 1
            except Exception:
                continue
        return added

    def execute(
        self,
        disposition: str | None = None,
        scan_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sync_pending: bool = True,
    ) -> dict[str, Any]:
        if sync_pending:
            try:
                self.sync_scans_to_queue()
            except Exception:
                pass

        reviews = self.review_repo.list_reviews(
            disposition=disposition,
            scan_id=scan_id,
            limit=limit,
            offset=offset,
        )
        enriched = [enrich_review_geo(r, self.scan_repo) for r in reviews]
        stats = self.review_repo.count_by_disposition()

        return {
            "reviews": [r.to_dict() for r in enriched],
            "total": stats.get("total", 0),
            "stats": stats,
            "limit": limit,
            "offset": offset,
        }


class GetReviewDetails:
    """Fetches a review record along with its immutable audit history trail."""

    def __init__(self, review_repo: ReviewRepository, scan_repo: ScanRepository | None = None) -> None:
        self.review_repo = review_repo
        self.scan_repo = scan_repo

    def execute(self, review_id: str | None = None, scan_id: str | None = None, detection_idx: int | None = None) -> ReviewRecord | None:
        rec = None
        if review_id:
            rec = self.review_repo.get(review_id)
        elif scan_id is not None and detection_idx is not None:
            rec = self.review_repo.get_by_scan_and_index(scan_id, detection_idx)

        if rec is not None:
            enrich_review_geo(rec, self.scan_repo)
        return rec


class ExportReviewedDataset:
    """Exports analyst-reviewed cases into benchmark and retraining datasets."""

    def __init__(
        self,
        review_repo: ReviewRepository,
        scan_repo: ScanRepository | None = None,
        output_root: Path | str | None = None,
    ) -> None:
        self.review_repo = review_repo
        self.scan_repo = scan_repo
        self.output_root = Path(output_root).resolve() if output_root else Path("output").resolve()

    def build_benchmark_dataset(self, iou_threshold: float = 0.5) -> dict[str, Any]:
        """Generate a benchmark evaluation dataset comparing detector predictions with analyst ground truth."""
        all_reviews = self.review_repo.list_reviews(disposition="all", limit=5000)
        reviewed = [r for r in all_reviews if r.is_reviewed]

        true_positives = 0
        false_positives = 0
        false_negatives = 0
        uncertain_count = 0
        total_iou = 0.0
        iou_count = 0

        cases: list[dict[str, Any]] = []

        for r in reviewed:
            pred_box = r.original_bbox
            gt_box = r.effective_bbox
            iou = calculate_iou(pred_box, gt_box)

            if r.disposition == ReviewDisposition.ACCEPTED.value:
                if iou >= iou_threshold:
                    true_positives += 1
                    status = "TP"
                else:
                    # Ground truth accepted but prediction had low overlap -> localized poorly or false negative match
                    true_positives += 1
                    status = "TP_POOR_LOCALIZATION"
                total_iou += iou
                iou_count += 1
            elif r.disposition == ReviewDisposition.REJECTED.value:
                false_positives += 1
                status = "FP"
            elif r.disposition == ReviewDisposition.UNCERTAIN.value:
                uncertain_count += 1
                status = "UNCERTAIN"
            else:
                status = "PENDING"

            cases.append({
                "review_id": r.review_id,
                "scan_id": r.scan_id,
                "detection_idx": r.detection_idx,
                "disposition": r.disposition,
                "reviewer_id": r.reviewer_id,
                "original_bbox": pred_box,
                "ground_truth_bbox": gt_box,
                "iou": iou,
                "benchmark_status": status,
                "confidence": r.confidence,
                "vessel_class": r.vessel_class or "vessel",
                "reason_codes": r.reason_codes,
            })

        precision = (true_positives / (true_positives + false_positives)) if (true_positives + false_positives) > 0 else 0.0
        # When benchmark is built from reviewed detector detections, recall is estimated over accepted targets
        recall = 1.0 if (true_positives + false_negatives) == 0 else (true_positives / (true_positives + false_negatives))
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        mean_iou = (total_iou / iou_count) if iou_count > 0 else 0.0

        return {
            "metrics": {
                "total_reviewed": len(reviewed),
                "true_positives": true_positives,
                "false_positives": false_positives,
                "false_negatives": false_negatives,
                "uncertain": uncertain_count,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1_score": round(f1, 4),
                "mean_iou": round(mean_iou, 4),
                "iou_threshold": iou_threshold,
            },
            "cases": cases,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_retraining_dataset(self, format_type: str = "yolo") -> dict[str, Any]:
        """Compile reviewed contacts into an ML retraining dataset manifest with positive and negative samples."""
        all_reviews = self.review_repo.list_reviews(disposition="all", limit=5000)
        accepted = [r for r in all_reviews if r.disposition == ReviewDisposition.ACCEPTED.value]
        rejected = [r for r in all_reviews if r.disposition == ReviewDisposition.REJECTED.value]
        uncertain = [r for r in all_reviews if r.disposition == ReviewDisposition.UNCERTAIN.value]

        annotations: list[dict[str, Any]] = []

        # Standard class mapping
        classes = ["vessel", "false_alarm"]

        for r in accepted:
            eff_box = r.effective_bbox
            box_obj = ReviewBox.from_dict(eff_box)
            # Default normalizer assuming standard crop 256x256 if full size unknown
            yolo_box = box_obj.to_yolo(1000.0, 1000.0) if box_obj else (0.5, 0.5, 0.1, 0.1)

            annotations.append({
                "sample_id": r.review_id,
                "scan_id": r.scan_id,
                "detection_idx": r.detection_idx,
                "class_id": 0,
                "class_name": "vessel",
                "label_type": "positive",
                "box_pixels": eff_box,
                "yolo_normalized": list(yolo_box),
                "confidence": r.confidence,
                "is_corrected": bool(r.corrected_bbox is not None),
                "reviewer": r.reviewer_id,
            })

        for r in rejected:
            # Negative sample (hard negative for false alarm suppression)
            eff_box = r.effective_bbox
            box_obj = ReviewBox.from_dict(eff_box)
            yolo_box = box_obj.to_yolo(1000.0, 1000.0) if box_obj else (0.5, 0.5, 0.1, 0.1)

            annotations.append({
                "sample_id": r.review_id,
                "scan_id": r.scan_id,
                "detection_idx": r.detection_idx,
                "class_id": 1,
                "class_name": "false_alarm",
                "label_type": "hard_negative",
                "box_pixels": eff_box,
                "yolo_normalized": list(yolo_box),
                "confidence": r.confidence,
                "reviewer": r.reviewer_id,
            })

        # Save dataset manifest to output directory
        dataset_dir = self.output_root / "datasets" / "retraining"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = dataset_dir / "manifest.json"
        
        manifest_data = {
            "dataset_version": "1.0.0",
            "format": format_type,
            "classes": classes,
            "summary": {
                "total_samples": len(annotations),
                "positive_samples": len(accepted),
                "hard_negatives": len(rejected),
                "hard_negative_samples": len(rejected),
                "uncertain_holdout_samples": len(uncertain),
            },
            "annotations": annotations,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
        except Exception:
            pass

        return manifest_data

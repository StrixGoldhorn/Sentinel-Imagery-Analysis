import concurrent.futures
from datetime import datetime, timezone
from typing import Optional

from sentinel_analysis.application.ports.satellite import PassPrediction, PassPredictor
from sentinel_analysis.domain.entities import BoundingBox
from sentinel_analysis.infrastructure.satellite.s1_analyzer import Sentinel1MissionAnalyzer


class HybridPassPredictor:
    """Combines astronomical/NORAD tracking (N2YO) and historical Sentinel-1 mission repeat cycles."""

    def __init__(
        self,
        n2yo_predictor: Optional[PassPredictor] = None,
        mission_analyzer: Optional[Sentinel1MissionAnalyzer] = None,
    ) -> None:
        self._n2yo = n2yo_predictor
        self._mission_analyzer = mission_analyzer or Sentinel1MissionAnalyzer()

    def predict(self, bbox: BoundingBox, api_key: str) -> list[PassPrediction]:
        n2yo_passes: list[PassPrediction] = []
        historical_passes: list[PassPrediction] = []

        def _fetch_n2yo() -> list[PassPrediction]:
            if self._n2yo is not None and isinstance(api_key, str) and api_key.strip():
                try:
                    raw_n2yo = self._n2yo.predict(bbox, api_key.strip())
                    passes: list[PassPrediction] = []
                    for item in raw_n2yo:
                        p = dict(item)
                        if "source" not in p or not p["source"]:
                            p["source"] = "N2YO"
                        if "contribution" not in p or not p["contribution"]:
                            p["contribution"] = "n2yo"
                        if "contribution_label" not in p or not p["contribution_label"]:
                            p["contribution_label"] = "N2YO Tracking Only"
                        if "contribution_detail" not in p or not p["contribution_detail"]:
                            p["contribution_detail"] = "Astronomical pass tracking via N2YO NORAD orbit propagation"
                        if "satellite" not in p or not p["satellite"]:
                            p["satellite"] = "Sentinel-1A"
                        if "confidence_score" not in p or p["confidence_score"] is None:
                            p["confidence_score"] = 0.68  # Lower weight for astronomical tracking
                        passes.append(PassPrediction(**p))  # type: ignore[misc]
                    return passes
                except Exception:
                    return []
            return []

        def _fetch_hist() -> list[PassPrediction]:
            try:
                raw_hist = self._mission_analyzer.predict_from_history(bbox, days_ahead=10, limit=100)
                passes: list[PassPrediction] = []
                for item in raw_hist:
                    p = dict(item)
                    if "source" not in p or not p["source"]:
                        p["source"] = "HISTORICAL_MISSION"
                    if "contribution" not in p or not p["contribution"]:
                        p["contribution"] = "historical"
                    if "contribution_label" not in p or not p["contribution_label"]:
                        p["contribution_label"] = "Historical Repeat Cycle Only"
                    if "contribution_detail" not in p or not p["contribution_detail"]:
                        p["contribution_detail"] = p.get("historical_match") or "Extrapolated from Sentinel-1 12-day repeat cycle"
                    passes.append(PassPrediction(**p))  # type: ignore[misc]
                return passes
            except Exception:
                return []

        # Execute both external sources in parallel to dramatically cut latency
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_n2yo = executor.submit(_fetch_n2yo)
            fut_hist = executor.submit(_fetch_hist)
            n2yo_passes = fut_n2yo.result()
            historical_passes = fut_hist.result()

        # 3. Merge and cross-validate both sources
        return self._merge_predictions(n2yo_passes, historical_passes)

    @staticmethod
    def _merge_predictions(
        n2yo_passes: list[PassPrediction],
        hist_passes: list[PassPrediction],
    ) -> list[PassPrediction]:
        """Merge overlapping passes into cross-validated predictions with weighted confidence scores.

        Historical extrapolated data receives higher weight (75%) as it represents deterministic
        radar acquisition cycles, while astronomical N2YO tracking receives lower weight (25%).
        """
        merged: list[PassPrediction] = []
        matched_hist_indices: set[int] = set()

        for n_pass in n2yo_passes:
            n_time = datetime.fromisoformat(str(n_pass["time"]).replace("Z", "+00:00"))
            if n_time.utcoffset() is None:
                n_time = n_time.replace(tzinfo=timezone.utc)
            n_time = n_time.astimezone(timezone.utc)

            best_hist_idx: int | None = None
            min_diff_sec = 900.0  # 15 minute matching window

            for h_idx, h_pass in enumerate(hist_passes):
                if h_idx in matched_hist_indices:
                    continue
                h_time = datetime.fromisoformat(str(h_pass["time"]).replace("Z", "+00:00"))
                if h_time.utcoffset() is None:
                    h_time = h_time.replace(tzinfo=timezone.utc)
                h_time = h_time.astimezone(timezone.utc)

                diff = abs((n_time - h_time).total_seconds())
                if diff < min_diff_sec:
                    min_diff_sec = diff
                    best_hist_idx = h_idx

            if best_hist_idx is not None:
                matched_hist_indices.add(best_hist_idx)
                h_match = hist_passes[best_hist_idx]
                h_conf = float(h_match.get("confidence_score") or 0.94)
                n_conf = float(n_pass.get("confidence_score") or 0.68)
                # Weighted blend: 75% historical extrapolation + 25% N2YO tracking + synergy bonus
                blended_conf = round(min(0.99, (0.75 * h_conf + 0.25 * n_conf) + 0.105), 2)
                hist_note = h_match.get("historical_match", "")

                merged.append(
                    PassPrediction(
                        time=n_pass["time"],
                        max_elevation=n_pass.get("max_elevation") or h_match.get("max_elevation") or 70.0,
                        source="COMBINED",
                        contribution="both",
                        contribution_label="Both (N2YO + Historical)",
                        contribution_detail=f"Cross-validated: N2YO tracking confirmed by Sentinel-1 repeat cycle ({hist_note})",
                        satellite=h_match.get("satellite") or n_pass.get("satellite") or "Sentinel-1A",
                        orbit_direction=h_match.get("orbit_direction"),
                        relative_orbit=h_match.get("relative_orbit"),
                        confidence_score=blended_conf,
                        swath_mode=h_match.get("swath_mode") or "IW",
                        historical_match=(
                            f"Cross-validated (N2YO + Historical {hist_note})"
                        ),
                    )
                )
            else:
                # Standalone N2YO pass with lower weight
                merged.append(
                    PassPrediction(
                        time=n_pass["time"],
                        max_elevation=n_pass.get("max_elevation"),
                        source="N2YO",
                        contribution="n2yo",
                        contribution_label="N2YO Tracking Only",
                        contribution_detail="Astronomical pass tracking via N2YO NORAD orbit propagation",
                        satellite=n_pass.get("satellite") or "Sentinel-1A",
                        orbit_direction=n_pass.get("orbit_direction"),
                        relative_orbit=n_pass.get("relative_orbit"),
                        confidence_score=n_pass.get("confidence_score") or 0.68,
                        swath_mode=n_pass.get("swath_mode") or "IW",
                        historical_match="Astronomical pass tracking (N2YO)",
                    )
                )

        # Include unmatched historical repeat-cycle predictions with high confidence weight
        for h_idx, h_pass in enumerate(hist_passes):
            if h_idx not in matched_hist_indices:
                p = dict(h_pass)
                if "source" not in p or not p["source"]:
                    p["source"] = "HISTORICAL_MISSION"
                if "contribution" not in p or not p["contribution"]:
                    p["contribution"] = "historical"
                if "contribution_label" not in p or not p["contribution_label"]:
                    p["contribution_label"] = "Historical Repeat Cycle Only"
                if "contribution_detail" not in p or not p["contribution_detail"]:
                    p["contribution_detail"] = p.get("historical_match") or "Extrapolated from Sentinel-1 12-day repeat cycle"
                merged.append(PassPrediction(**p))  # type: ignore[misc]

        # Sort all predictions chronologically
        merged.sort(key=lambda p: datetime.fromisoformat(str(p["time"]).replace("Z", "+00:00")))
        return merged

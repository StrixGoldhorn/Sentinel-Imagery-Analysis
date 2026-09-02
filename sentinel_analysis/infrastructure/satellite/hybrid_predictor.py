"""Hybrid satellite flypast predictor merging N2YO tracking and historical Sentinel-1 mission modeling."""

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
        if self._n2yo is not None and isinstance(api_key, str) and api_key.strip():
            try:
                raw_n2yo = self._n2yo.predict(bbox, api_key.strip())
                for item in raw_n2yo:
                    p = dict(item)
                    if "source" not in p or not p["source"]:
                        p["source"] = "N2YO"
                    if "satellite" not in p or not p["satellite"]:
                        p["satellite"] = "Sentinel-1A"
                    if "confidence_score" not in p or p["confidence_score"] is None:
                        p["confidence_score"] = 0.68  # Lower weight for astronomical tracking
                    n2yo_passes.append(PassPrediction(**p))  # type: ignore[misc]
            except Exception:
                # If N2YO fails (e.g. rate-limit, offline, invalid key), we still proceed with historical predictions
                n2yo_passes = []

        # 2. Historical mission repeat cycle predictions (Higher weight)
        historical_passes: list[PassPrediction] = []
        try:
            historical_passes = self._mission_analyzer.predict_from_history(bbox, days_ahead=10)
        except Exception:
            historical_passes = []

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
                blended_conf = round(min(0.99, (0.75 * h_conf + 0.25 * n_conf) + 0.10), 2)

                merged.append(
                    PassPrediction(
                        time=n_pass["time"],
                        max_elevation=n_pass.get("max_elevation") or h_match.get("max_elevation") or 70.0,
                        source="COMBINED",
                        satellite=h_match.get("satellite") or n_pass.get("satellite") or "Sentinel-1A",
                        orbit_direction=h_match.get("orbit_direction"),
                        relative_orbit=h_match.get("relative_orbit"),
                        confidence_score=blended_conf,
                        swath_mode=h_match.get("swath_mode") or "IW",
                        historical_match=(
                            f"Cross-validated (N2YO + Historical {h_match.get('historical_match', '')})"
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
                merged.append(h_pass)


        # Sort all predictions chronologically
        merged.sort(key=lambda p: datetime.fromisoformat(str(p["time"]).replace("Z", "+00:00")))
        return merged

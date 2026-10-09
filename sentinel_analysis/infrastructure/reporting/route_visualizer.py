"""Kinematic route prediction and multi-sensor correlation visualizer.

Generates standalone publication-grade graphics and PDF report figures showing:
1. Traced historical AIS vessel track
2. Reported AIS vessel location at scan epoch
3. Kinematic forward predicted route (dead-reckoned along SOG/COG)
4. User-defined route buffer corridor
5. SAR radar detection oriented bounding boxes (OBB)
6. Multi-sensor correlation offset vector and telemetry card
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from PIL import Image

from sentinel_analysis.application.use_cases.correlate_ais_detections import (
    dead_reckon_position,
    haversine_distance_meters,
)
from sentinel_analysis.domain.entities import BoundingBox, Scan

logger = logging.getLogger(__name__)


def format_lat(lat: Any) -> str:
    """Format latitude with N/S hemisphere suffix."""
    if lat is None:
        return "N/A"
    try:
        val = float(lat)
        return f"{abs(val):.4f}°{'N' if val >= 0 else 'S'}"
    except (ValueError, TypeError):
        return "N/A"


def format_lon(lon: Any) -> str:
    """Format longitude with E/W hemisphere suffix."""
    if lon is None:
        return "N/A"
    try:
        val = float(lon)
        return f"{abs(val):.4f}°{'E' if val >= 0 else 'W'}"
    except (ValueError, TypeError):
        return "N/A"


def compute_corridor_polygon(
    route_points: list[tuple[float, float]],
    buffer_meters: float = 500.0,
) -> list[tuple[float, float]]:
    """Compute an envelope polygon representing a buffer corridor around a polyline route."""
    if len(route_points) < 2:
        return []

    left_points: list[tuple[float, float]] = []
    right_points: list[tuple[float, float]] = []

    for i in range(len(route_points)):
        p = route_points[i]
        # Calculate local segment bearing
        if i == 0:
            p_next = route_points[i + 1]
            dx = (p_next[1] - p[1]) * math.cos(math.radians(p[0]))
            dy = p_next[0] - p[0]
        elif i == len(route_points) - 1:
            p_prev = route_points[i - 1]
            dx = (p[1] - p_prev[1]) * math.cos(math.radians(p[0]))
            dy = p[0] - p_prev[0]
        else:
            p_prev = route_points[i - 1]
            p_next = route_points[i + 1]
            dx = (p_next[1] - p_prev[1]) * math.cos(math.radians(p[0]))
            dy = p_next[0] - p_prev[0]

        length = math.sqrt(dx * dx + dy * dy)
        if length < 1e-9:
            continue

        # Normal vector perpendicular to route
        nx = -dy / length
        ny = dx / length

        # Convert buffer meters to degree offsets
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * max(0.01, math.cos(math.radians(p[0])))

        off_lat = (ny * buffer_meters) / m_per_deg_lat
        off_lon = (nx * buffer_meters) / m_per_deg_lon

        left_points.append((p[0] + off_lat, p[1] + off_lon))
        right_points.append((p[0] - off_lat, p[1] - off_lon))

    if not left_points or not right_points:
        return []

    # Closed polygon: left path forward, right path backward
    right_points.reverse()
    return left_points + right_points


def extract_correlated_pairs(detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract and normalize AIS-correlated detection pairs, sorted by size and confidence."""
    correlated_pairs: list[dict[str, Any]] = []
    for d in detections:
        ais = d.get("correlated_ais") or d.get("raw_detection", {}).get("correlated_ais")
        if ais and isinstance(ais, dict):
            try:
                ais_lat = float(ais.get("latitude") if ais.get("latitude") is not None else ais.get("raw_latitude", 0.0))
            except (ValueError, TypeError):
                ais_lat = 0.0
            try:
                ais_lon = float(ais.get("longitude") if ais.get("longitude") is not None else ais.get("raw_longitude", 0.0))
            except (ValueError, TypeError):
                ais_lon = 0.0
            try:
                sar_lat = float(d.get("latitude") or d.get("lat") or 0.0)
            except (ValueError, TypeError):
                sar_lat = 0.0
            try:
                sar_lon = float(d.get("longitude") or d.get("lng") or 0.0)
            except (ValueError, TypeError):
                sar_lon = 0.0

            try:
                speed = float(ais.get("speed") or d.get("speed") or 0.0)
            except (ValueError, TypeError):
                speed = 0.0
            try:
                heading = float(ais.get("heading") or d.get("heading") or 0.0)
            except (ValueError, TypeError):
                heading = 0.0

            try:
                dist_m = float(ais.get("distance_to_center_meters") or ais.get("distance_to_box_meters") or 0.0)
            except (ValueError, TypeError):
                dist_m = 0.0

            try:
                length_m = float(d.get("length_m") or d.get("length") or 0.0)
            except (ValueError, TypeError):
                length_m = 0.0

            try:
                beam_m = float(d.get("width_m") or d.get("beam") or 0.0)
            except (ValueError, TypeError):
                beam_m = 0.0

            try:
                confidence = float(d.get("confidence") or 0.0)
            except (ValueError, TypeError):
                confidence = 0.0

            target_id = d.get("id", d.get("index", "?"))
            mmsi = ais.get("mmsi") or d.get("mmsi") or "N/A"
            vessel_name = ais.get("vessel_name") or d.get("vessel_name") or f"MMSI {mmsi}"
            vessel_type = ais.get("vessel_type") or d.get("vessel_type") or "Commercial Vessel"
            imo = ais.get("imo") or "N/A"

            correlated_pairs.append({
                "detection": d,
                "ais": ais,
                "mmsi": mmsi,
                "vessel_name": vessel_name,
                "vessel_type": vessel_type,
                "imo": imo,
                "ais_lat": ais_lat,
                "ais_lon": ais_lon,
                "sar_lat": sar_lat,
                "sar_lon": sar_lon,
                "speed": speed,
                "heading": heading,
                "dist_m": dist_m,
                "length_m": length_m,
                "beam_m": beam_m,
                "confidence": confidence,
                "target_id": target_id,
            })

    # Sort candidate vessels by length and confidence to pick primary subject
    correlated_pairs.sort(key=lambda p: (p["length_m"], p["confidence"]), reverse=True)
    return correlated_pairs


class RouteVisualizer:
    """Generates standalone visual images and PDF briefing figures for route correlation."""

    def __init__(self, default_buffer_meters: float = 500.0) -> None:
        self.default_buffer_meters = default_buffer_meters

    def generate_route_image(
        self,
        scan: Scan,
        detections: Optional[list[dict[str, Any]]] = None,
        output_path: Optional[Path] = None,
        buffer_meters: Optional[float] = None,
    ) -> Path:
        """Create and save a standalone PNG image showing route prediction and SAR correlation."""
        if detections is None:
            from sentinel_analysis.application.use_cases.generate_briefing import extract_scan_intelligence
            detections = extract_scan_intelligence(scan).get("detections", [])

        if buffer_meters is None:
            buffer_meters = self.default_buffer_meters

        if output_path is None:
            image_path = Path(scan.image_path)
            output_path = image_path.parent / f"{scan.folder_name}_route_correlation.png"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fig = plt.figure(figsize=(14.0, 9.0), dpi=200)
        fig.patch.set_facecolor("#0b132b")  # Dark maritime tactical theme

        self.render_figure_content(fig, scan, detections, buffer_meters=buffer_meters, dark_theme=True)

        fig.savefig(output_path, dpi=200, facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved route correlation visualization image to %s", output_path)
        return output_path

    def render_tactical_map_page(
        self,
        fig: plt.Figure,
        scan: Scan,
        detections: list[dict[str, Any]],
        buffer_meters: float = 500.0,
        dark_theme: bool = True,
    ) -> None:
        """Render Page 3: Full-canvas Tactical Route & Multi-Sensor Correlation Map."""
        panel_color = "#1c2541" if dark_theme else "#ffffff"
        border_color = "#3a506b" if dark_theme else "#cbd5e0"
        text_primary = "#ffffff" if dark_theme else "#1a202c"
        text_secondary = "#a0aec0" if dark_theme else "#718096"
        grid_color = "#203a43" if dark_theme else "#cbd5e0"

        # 1. Header Banner
        header_ax = fig.add_axes([0.05, 0.92, 0.90, 0.06])
        header_ax.axis("off")
        header_ax.text(
            0.0, 0.65,
            "KINEMATIC ROUTE PREDICTION & MULTI-SENSOR CORRELATION",
            fontsize=13.0,
            fontweight="bold",
            color="#00d2d3" if dark_theme else "#2b6cb0",
        )
        scan_id = scan.folder_name
        acq_str = scan.acquisition.acquired_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        header_ax.text(
            0.0, 0.12,
            f"Scan AOI: {scan_id}  |  Sensor: {scan.acquisition.satellite}  |  Pass Epoch: {acq_str}  |  Route Corridor Buffer: ±{buffer_meters:.0f}m",
            fontsize=8.5,
            color=text_secondary,
        )
        header_ax.text(
            1.0, 0.65,
            "TACTICAL FUSION // SPATIAL TRACK",
            fontsize=9.5,
            fontweight="bold",
            color="#f39c12" if dark_theme else "#c05621",
            ha="right",
        )

        correlated_pairs = extract_correlated_pairs(detections)
        primary = correlated_pairs[0] if correlated_pairs else None

        # 2. Main Tactical Map (Occupies primary canvas with spacious layout)
        map_ax = fig.add_axes([0.05, 0.12, 0.90, 0.78])
        map_ax.set_facecolor("#0f172a" if dark_theme else "#edf2f7")
        map_ax.grid(True, linestyle="--", alpha=0.35, color=grid_color, zorder=2)

        if primary and primary["ais_lat"] != 0.0 and primary["sar_lat"] != 0.0:
            spd_knots = max(8.0, primary["speed"]) if primary["speed"] > 0 else 12.0
            hdg_deg = primary["heading"] if primary["heading"] > 0 else 90.0

            if primary["heading"] == 0.0:
                d_lat = primary["sar_lat"] - primary["ais_lat"]
                d_lon = (primary["sar_lon"] - primary["ais_lon"]) * math.cos(math.radians(primary["ais_lat"]))
                hdg_deg = math.degrees(math.atan2(d_lon, d_lat)) % 360.0

            # Extrapolate historical points (-60m, -40m, -20m, 0m)
            hist_pts: list[tuple[float, float]] = []
            for t_sec in [-3600.0, -2400.0, -1200.0, 0.0]:
                h_lat, h_lon, _ = dead_reckon_position(primary["ais_lat"], primary["ais_lon"], spd_knots, hdg_deg, t_sec)
                hist_pts.append((h_lat, h_lon))

            # Extrapolate forward prediction points (0m, +20m, +40m, +60m)
            pred_pts: list[tuple[float, float]] = []
            for t_sec in [0.0, 1200.0, 2400.0, 3600.0]:
                f_lat, f_lon, _ = dead_reckon_position(primary["ais_lat"], primary["ais_lon"], spd_knots, hdg_deg, t_sec)
                pred_pts.append((f_lat, f_lon))

            full_route = hist_pts + pred_pts[1:]

            # Set bounds around trajectory and nearby vessels
            pts_lat = [p[0] for p in full_route] + [primary["sar_lat"]]
            pts_lon = [p[1] for p in full_route] + [primary["sar_lon"]]
            for cp in correlated_pairs[:8]:
                if cp["ais_lat"] != 0.0:
                    pts_lat.extend([cp["ais_lat"], cp["sar_lat"]])
                    pts_lon.extend([cp["ais_lon"], cp["sar_lon"]])

            min_c_lon, max_c_lon = min(pts_lon), max(pts_lon)
            min_c_lat, max_c_lat = min(pts_lat), max(pts_lat)
            lon_margin = max((max_c_lon - min_c_lon) * 0.20, 0.02)
            lat_margin = max((max_c_lat - min_c_lat) * 0.20, 0.015)
            map_ax.set_xlim(min_c_lon - lon_margin, max_c_lon + lon_margin)
            map_ax.set_ylim(min_c_lat - lat_margin, max_c_lat + lat_margin)

            # Corridor Buffer Envelope
            corridor_poly = compute_corridor_polygon(full_route, buffer_meters=buffer_meters)
            if corridor_poly:
                poly_lons = [p[1] for p in corridor_poly]
                poly_lats = [p[0] for p in corridor_poly]
                map_ax.fill(
                    poly_lons, poly_lats,
                    color="#00d2d3" if dark_theme else "#68d391",
                    alpha=0.18,
                    edgecolor="#00d2d3" if dark_theme else "#38a169",
                    linestyle="--",
                    linewidth=1.3,
                    label=f"Route Buffer Corridor (±{buffer_meters:.0f}m)",
                    zorder=3,
                )

            # Traced Historical Track
            h_lats = [p[0] for p in hist_pts]
            h_lons = [p[1] for p in hist_pts]
            map_ax.plot(
                h_lons, h_lats,
                color="#3498db",
                linestyle=":",
                linewidth=2.2,
                label="Traced Historical Track (-60m to 0m)",
                zorder=4,
            )
            map_ax.scatter(
                h_lons[:-1], h_lats[:-1],
                color="#3498db",
                edgecolors="white",
                linewidths=0.8,
                s=45,
                marker="o",
                zorder=4,
            )

            # Forward Predicted Route
            p_lats = [p[0] for p in pred_pts]
            p_lons = [p[1] for p in pred_pts]
            map_ax.plot(
                p_lons, p_lats,
                color="#2ecc71",
                linestyle="--",
                linewidth=2.5,
                label="Predicted Forward Route (0m to +60m)",
                zorder=5,
            )
            for i, (wp_lat, wp_lon) in enumerate(pred_pts[1:], start=1):
                map_ax.scatter(wp_lon, wp_lat, color="#2ecc71", edgecolors="white", s=55, marker="^", zorder=6)
                map_ax.annotate(
                    f"+{i * 20}m",
                    xy=(wp_lon, wp_lat),
                    xytext=(6, 4),
                    textcoords="offset points",
                    fontsize=7.5,
                    fontweight="bold",
                    color="#2ecc71",
                )

            # Reported AIS Fix Marker
            map_ax.scatter(
                [primary["ais_lon"]], [primary["ais_lat"]],
                color="#2980b9",
                edgecolors="white",
                linewidths=1.5,
                marker="s",
                s=120,
                label=f"Reported AIS Fix: {primary['vessel_name']}",
                zorder=7,
            )

            # SAR Radar Detection Box & Marker
            sar_d = primary["detection"]
            geo_poly = sar_d.get("geo_polygon") or sar_d.get("raw_detection", {}).get("geo_polygon")
            if geo_poly and len(geo_poly) >= 4:
                g_lats = [pt[0] for pt in geo_poly] + [geo_poly[0][0]]
                g_lons = [pt[1] for pt in geo_poly] + [geo_poly[0][1]]
                map_ax.plot(
                    g_lons, g_lats,
                    color="#e74c3c",
                    linewidth=2.0,
                    label=f"SAR Radar Detection (Target #{primary['target_id']})",
                    zorder=8,
                )
                map_ax.fill(g_lons, g_lats, color="#e74c3c", alpha=0.35, zorder=7)

            map_ax.scatter(
                [primary["sar_lon"]], [primary["sar_lat"]],
                color="#e74c3c",
                edgecolors="white",
                linewidths=1.5,
                marker="X",
                s=130,
                zorder=9,
            )

            # Multi-Sensor Correlation Vector
            offset_dist = primary["dist_m"] if primary["dist_m"] > 0 else haversine_distance_meters(
                primary["ais_lat"], primary["ais_lon"], primary["sar_lat"], primary["sar_lon"]
            )
            map_ax.plot(
                [primary["ais_lon"], primary["sar_lon"]],
                [primary["ais_lat"], primary["sar_lat"]],
                color="#f39c12",
                linestyle="-.",
                linewidth=2.0,
                label=f"Correlation Vector (Δ = {offset_dist:.1f}m)",
                zorder=7,
            )

            # Midpoint Δ label pill badge offset perpendicularly NW to avoid leader line collisions
            mid_v_lat = (primary["ais_lat"] + primary["sar_lat"]) / 2.0
            mid_v_lon = (primary["ais_lon"] + primary["sar_lon"]) / 2.0
            d_lon = primary["sar_lon"] - primary["ais_lon"]
            d_lat = primary["sar_lat"] - primary["ais_lat"]
            norm_len = math.hypot(d_lon, d_lat)
            perp_lon = -d_lat / norm_len if norm_len > 1e-9 else 0.0
            perp_lat = d_lon / norm_len if norm_len > 1e-9 else 0.0
            badge_offset = min(max(norm_len * 0.35, 0.0025), 0.006) if norm_len > 1e-9 else 0.0
            badge_lon = mid_v_lon + perp_lon * badge_offset
            badge_lat = mid_v_lat + perp_lat * badge_offset

            map_ax.text(
                badge_lon, badge_lat,
                f" Δ = {offset_dist:.0f}m ",
                fontsize=7.8,
                fontweight="bold",
                color="#fbbf24",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#0f172a", edgecolor="#fbbf24", alpha=0.95, lw=1.2),
                ha="center",
                va="center",
                zorder=11,
            )

            # Non-overlapping smart leader lines pointing away from each other:
            # AIS Fix Callout: offset up-left (-85, 48)
            map_ax.annotate(
                f"REPORTED AIS FIX\n{primary['vessel_name']}\nSOG: {spd_knots:.1f} kn | COG: {hdg_deg:.0f}°",
                xy=(primary["ais_lon"], primary["ais_lat"]),
                xytext=(-85, 48),
                textcoords="offset points",
                fontsize=8.0,
                fontweight="bold",
                color="#38bdf8",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="#1e293b", edgecolor="#38bdf8", alpha=0.95, lw=1.2),
                arrowprops=dict(arrowstyle="->", color="#38bdf8", lw=1.4, connectionstyle="arc3,rad=0.08"),
                zorder=12,
            )

            # SAR Contact Callout: offset down-right (85, -48)
            map_ax.annotate(
                f"SAR CONTACT #{primary['target_id']}\nL: {primary['length_m']:.0f}m, B: {primary['beam_m']:.0f}m\nConf: {primary['confidence']*100:.0f}%  |  Δ: {offset_dist:.0f}m",
                xy=(primary["sar_lon"], primary["sar_lat"]),
                xytext=(85, -48),
                textcoords="offset points",
                fontsize=8.0,
                fontweight="bold",
                color="#f87171",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="#1e293b", edgecolor="#f87171", alpha=0.95, lw=1.2),
                arrowprops=dict(arrowstyle="->", color="#f87171", lw=1.4, connectionstyle="arc3,rad=-0.08"),
                zorder=12,
            )

        else:
            bbox = scan.bbox
            map_ax.set_xlim(bbox.min_longitude, bbox.max_longitude)
            map_ax.set_ylim(bbox.min_latitude, bbox.max_latitude)
            map_ax.text(
                0.5, 0.5,
                "No AIS-correlated vessels identified in current pass.\nDisplaying geographic scan boundary.",
                transform=map_ax.transAxes,
                ha="center", va="center",
                fontsize=10, fontweight="bold",
                color=text_secondary,
                bbox=dict(boxstyle="round,pad=0.6", facecolor=panel_color, edgecolor=border_color, alpha=0.9),
            )

        # Plot secondary correlated vessels in the vicinity
        for cp in correlated_pairs[1:8]:
            map_ax.scatter([cp["sar_lon"]], [cp["sar_lat"]], color="#3498db", edgecolors="white", marker="o", s=45, alpha=0.75, zorder=4)
            map_ax.scatter([cp["ais_lon"]], [cp["ais_lat"]], color="#2980b9", edgecolors="white", marker="s", s=40, alpha=0.6, zorder=4)
            map_ax.plot([cp["ais_lon"], cp["sar_lon"]], [cp["ais_lat"], cp["sar_lat"]], color="#f39c12", linestyle=":", linewidth=1.0, alpha=0.5, zorder=3)

        map_ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{abs(x):.3f}°{'E' if x >= 0 else 'W'}"))
        map_ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{abs(y):.3f}°{'N' if y >= 0 else 'S'}"))
        map_ax.tick_params(labelsize=8, colors=text_secondary)
        map_ax.set_xlabel("Longitude (WGS84)", fontsize=8.5, color=text_primary, fontweight="bold")
        map_ax.set_ylabel("Latitude (WGS84)", fontsize=8.5, color=text_primary, fontweight="bold")

        handles, labels = map_ax.get_legend_handles_labels()
        if handles:
            map_ax.legend(
                loc="upper left",
                fontsize=8.0,
                framealpha=0.92,
                facecolor=panel_color,
                edgecolor=border_color,
                labelcolor=text_primary,
            )

        # 3. Bottom Tactical Status Strip
        status_ax = fig.add_axes([0.05, 0.035, 0.90, 0.065])
        status_ax.axis("off")
        status_rect = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.015",
            facecolor=panel_color,
            edgecolor=border_color,
            linewidth=1.2,
        )
        status_ax.add_patch(status_rect)

        if primary:
            offset_dist = primary["dist_m"] if primary["dist_m"] > 0 else haversine_distance_meters(
                primary["ais_lat"], primary["ais_lon"], primary["sar_lat"], primary["sar_lon"]
            )
            spd_knots = max(8.0, primary["speed"]) if primary["speed"] > 0 else 12.0
            hdg_deg = primary["heading"] if primary["heading"] > 0 else 90.0
            if primary["heading"] == 0.0:
                d_lat = primary["sar_lat"] - primary["ais_lat"]
                d_lon = (primary["sar_lon"] - primary["ais_lon"]) * math.cos(math.radians(primary["ais_lat"]))
                hdg_deg = math.degrees(math.atan2(d_lon, d_lat)) % 360.0

            status_ax.text(
                0.025, 0.65,
                f"PRIMARY TARGET: {primary['vessel_name']}  |  MMSI: {primary['mmsi']}  |  TYPE: {primary['vessel_type']}",
                fontsize=8.8,
                fontweight="bold",
                color=text_primary,
                va="center",
            )
            status_ax.text(
                0.025, 0.28,
                f"KINEMATICS: {spd_knots:.1f} kn @ {hdg_deg:.0f}°  |  RADAR: {primary['length_m']:.0f}m x {primary['beam_m']:.0f}m (Conf: {primary['confidence']*100:.0f}%)  |  OFFSET: Δ = {offset_dist:.1f}m",
                fontsize=8.0,
                color=text_secondary,
                va="center",
            )
            is_in_corridor = offset_dist <= buffer_meters
            st_color = "#2ecc71" if is_in_corridor else "#f39c12"
            st_bg = "#0f2e22" if is_in_corridor else "#332205"
            st_label = f"CORRIDOR STATUS: WITHIN BUFFER (±{buffer_meters:.0f}m)" if is_in_corridor else f"CORRIDOR STATUS: EXCEEDS BUFFER (Δ={offset_dist:.0f}m)"

            b_rect = patches.FancyBboxPatch(
                (0.66, 0.16), 0.32, 0.68,
                boxstyle="round,pad=0.02",
                facecolor=st_bg,
                edgecolor=st_color,
                linewidth=1.2,
            )
            status_ax.add_patch(b_rect)
            status_ax.text(
                0.82, 0.50,
                st_label,
                fontsize=7.8,
                fontweight="bold",
                color=st_color,
                ha="center",
                va="center",
            )
        else:
            status_ax.text(
                0.50, 0.50,
                "NO AIS-CORRELATED TARGETS DETECTED IN PASS // CORRIDOR EVALUATION PENDING",
                fontsize=9.0,
                fontweight="bold",
                color=text_secondary,
                ha="center",
                va="center",
            )

    def render_telemetry_dossier_page(
        self,
        fig: plt.Figure,
        scan: Scan,
        detections: list[dict[str, Any]],
        buffer_meters: float = 500.0,
        dark_theme: bool = True,
    ) -> None:
        """Render Page 4: Detailed Kinematic Telemetry Scorecard & Correlated Contacts Manifest."""
        panel_color = "#1c2541" if dark_theme else "#ffffff"
        border_color = "#3a506b" if dark_theme else "#cbd5e0"
        text_primary = "#ffffff" if dark_theme else "#1a202c"
        text_secondary = "#a0aec0" if dark_theme else "#718096"

        correlated_pairs = extract_correlated_pairs(detections)
        primary = correlated_pairs[0] if correlated_pairs else None

        # 1. Header Banner
        header_ax = fig.add_axes([0.05, 0.92, 0.90, 0.06])
        header_ax.axis("off")
        header_ax.text(
            0.0, 0.65,
            "KINEMATIC TELEMETRY & ROUTE CORRIDOR ANALYSIS",
            fontsize=13.0,
            fontweight="bold",
            color="#00d2d3" if dark_theme else "#2b6cb0",
        )
        scan_id = scan.folder_name
        p_name = primary["vessel_name"] if primary else "None"
        header_ax.text(
            0.0, 0.12,
            f"Scan AOI: {scan_id}  |  Primary Target: {p_name}  |  Corridor Buffer: ±{buffer_meters:.0f}m  |  Dead-Reckoned Waypoint Schedule",
            fontsize=8.5,
            color=text_secondary,
        )
        header_ax.text(
            1.0, 0.65,
            "TACTICAL FUSION // TELEMETRY DOSSIER",
            fontsize=9.5,
            fontweight="bold",
            color="#f39c12" if dark_theme else "#c05621",
            ha="right",
        )

        # 2. Top-Left Card: Primary Target Telemetry Scorecard [0.05, 0.50, 0.43, 0.39]
        card1_ax = fig.add_axes([0.05, 0.50, 0.43, 0.39])
        card1_ax.axis("off")
        card1_rect = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.02",
            facecolor=panel_color,
            edgecolor=border_color,
            linewidth=1.2,
        )
        card1_ax.add_patch(card1_rect)

        card1_ax.text(
            0.05, 0.92,
            "PRIMARY TARGET KINEMATIC SCORECARD",
            fontsize=10.0,
            fontweight="bold",
            color="#00d2d3" if dark_theme else "#2b6cb0",
        )

        if primary:
            spd_val = primary["speed"]
            hdg_val = primary["heading"]
            spd_knots = max(8.0, spd_val) if spd_val > 0 else 12.0
            hdg_deg = hdg_val if hdg_val > 0 else 90.0
            if hdg_val == 0.0:
                d_lat = primary["sar_lat"] - primary["ais_lat"]
                d_lon = (primary["sar_lon"] - primary["ais_lon"]) * math.cos(math.radians(primary["ais_lat"]))
                hdg_deg = math.degrees(math.atan2(d_lon, d_lat)) % 360.0

            offset_dist = primary["dist_m"] if primary["dist_m"] > 0 else haversine_distance_meters(
                primary["ais_lat"], primary["ais_lon"], primary["sar_lat"], primary["sar_lon"]
            )
            is_in_corridor = offset_dist <= buffer_meters
            st_color = "#2ecc71" if is_in_corridor else "#f39c12"
            st_bg = "#0f2e22" if is_in_corridor else "#332205"
            st_text = f"CORRIDOR STATUS: WITHIN BUFFER (±{buffer_meters:.0f}m)" if is_in_corridor else f"CORRIDOR STATUS: EXCEEDS BUFFER (Δ={offset_dist:.0f}m > ±{buffer_meters:.0f}m)"

            fields = [
                ("Vessel Name", primary["vessel_name"][:20]),
                ("MMSI // IMO", f"{primary['mmsi']} / {primary['imo'][:10]}"),
                ("Classification", primary["vessel_type"][:20]),
                ("Reported Velocity", f"{spd_knots:.1f} kn @ {hdg_deg:.0f}° COG"),
                ("Reported AIS Fix", f"{format_lat(primary['ais_lat'])}, {format_lon(primary['ais_lon'])}"),
                ("SAR Radar Position", f"{format_lat(primary['sar_lat'])}, {format_lon(primary['sar_lon'])}"),
                ("Radar Length x Beam", f"{primary['length_m']:.0f}m x {primary['beam_m']:.0f}m"),
                ("Detection Confidence", f"{primary['confidence']*100:.0f}%"),
                ("Cross-Sensor Offset", f"Δ = {offset_dist:.1f} m"),
            ]

            y_pos = 0.83
            for label, val in fields:
                card1_ax.text(0.06, y_pos, label, fontsize=7.8, color=text_secondary, fontweight="bold")
                card1_ax.text(0.94, y_pos, val, fontsize=7.8, color=text_primary, ha="right")
                y_pos -= 0.072

            # Bottom status badge in card 1
            badge1 = patches.FancyBboxPatch(
                (0.05, 0.06), 0.90, 0.085,
                boxstyle="round,pad=0.015",
                facecolor=st_bg,
                edgecolor=st_color,
                linewidth=1.2,
            )
            card1_ax.add_patch(badge1)
            card1_ax.text(
                0.50, 0.102,
                st_text,
                fontsize=7.5,
                fontweight="bold",
                color=st_color,
                ha="center",
                va="center",
            )
        else:
            card1_ax.text(
                0.50, 0.50,
                "No AIS-correlated vessels\nidentified in current pass.",
                fontsize=9.0,
                color=text_secondary,
                ha="center",
                va="center",
            )

        # 3. Top-Right Card: Dead-Reckoned Waypoint Schedule [0.52, 0.50, 0.43, 0.39]
        card2_ax = fig.add_axes([0.52, 0.50, 0.43, 0.39])
        card2_ax.axis("off")
        card2_rect = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.02",
            facecolor=panel_color,
            edgecolor=border_color,
            linewidth=1.2,
        )
        card2_ax.add_patch(card2_rect)

        card2_ax.text(
            0.05, 0.92,
            "DEAD-RECKONED TRAJECTORY SCHEDULE",
            fontsize=10.0,
            fontweight="bold",
            color="#00d2d3" if dark_theme else "#2b6cb0",
        )
        if primary:
            card2_ax.text(
                0.05, 0.85,
                f"Extrapolated route schedule ({spd_knots:.1f} kn @ {hdg_deg:.0f}° relative to pass epoch)",
                fontsize=7.5,
                color=text_secondary,
            )
        else:
            card2_ax.text(
                0.05, 0.85,
                "Trajectory schedule requires active kinematic fix",
                fontsize=7.5,
                color=text_secondary,
            )

        # Build Dead-Reckoning Table
        dr_headers = ["Offset", "Latitude", "Longitude", "Dist", "Phase"]
        dr_rows = []
        if primary:
            for t_min in [-60, -40, -20, 0, 20, 40, 60]:
                t_sec = t_min * 60.0
                p_lat, p_lon, d_meters = dead_reckon_position(
                    primary["ais_lat"], primary["ais_lon"], spd_knots, hdg_deg, t_sec
                )
                d_nm = (d_meters / 1852.0) if t_min >= 0 else -(d_meters / 1852.0)
                lbl = f"{t_min:+d}m" if t_min != 0 else "0m (Epoch)"
                ph = "Historical" if t_min < 0 else ("Pass Epoch" if t_min == 0 else "Projected")
                dr_rows.append([
                    lbl,
                    format_lat(p_lat),
                    format_lon(p_lon),
                    f"{d_nm:+.1f}nm",
                    ph,
                ])
        else:
            dr_rows = [["-", "-", "-", "-", "NO KINEMATICS"]]

        tbl2_ax = fig.add_axes([0.535, 0.52, 0.40, 0.30])
        tbl2_ax.axis("off")
        tbl2 = tbl2_ax.table(
            cellText=dr_rows,
            colLabels=dr_headers,
            colWidths=[0.20, 0.23, 0.23, 0.14, 0.20],
            loc="center",
            cellLoc="center",
        )
        tbl2.auto_set_font_size(False)
        tbl2.set_fontsize(7.2)
        tbl2.scale(1.0, 1.25)
        for (r, c), cell in tbl2.get_celld().items():
            cell.set_edgecolor(border_color)
            if r == 0:
                cell.set_facecolor("#2b6cb0")
                cell.get_text().set_color("white")
                cell.get_text().set_fontweight("bold")
            else:
                row_idx = r - 1
                is_epoch = primary and (row_idx == 3)
                if is_epoch:
                    cell.set_facecolor("#1e3a5f")
                    cell.get_text().set_color("#00d2d3")
                    cell.get_text().set_fontweight("bold")
                else:
                    cell.set_facecolor(panel_color)
                    cell.get_text().set_color(text_primary)
                    if c == 4:
                        if dr_rows[row_idx][4] == "Historical":
                            cell.get_text().set_color("#38bdf8")
                        elif dr_rows[row_idx][4] == "Projected":
                            cell.get_text().set_color("#2ecc71")

        # 4. Bottom Card: Correlated Contacts Manifest Table [0.05, 0.04, 0.90, 0.43]
        card3_ax = fig.add_axes([0.05, 0.04, 0.90, 0.43])
        card3_ax.axis("off")
        card3_rect = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.02",
            facecolor=panel_color,
            edgecolor=border_color,
            linewidth=1.2,
        )
        card3_ax.add_patch(card3_rect)

        card3_ax.text(
            0.03, 0.93,
            "CORRELATED CONTACTS MANIFEST & ROUTE CORRIDOR COMPLIANCE",
            fontsize=10.5,
            fontweight="bold",
            color=text_primary,
        )
        card3_ax.text(
            0.03, 0.86,
            f"Multi-sensor cross-match of all radar detections against reported AIS tracks (Corridor Buffer: ±{buffer_meters:.0f}m)",
            fontsize=8.0,
            color=text_secondary,
        )

        manifest_headers = ["Target ID", "Vessel Name", "MMSI", "Vessel Type", "Speed / Course", "AIS Coordinate", "SAR Coordinate", "Offset", "Corridor Status"]
        manifest_rows = []
        for cp in correlated_pairs[:8]:
            in_buf = cp["dist_m"] <= buffer_meters
            st = "INSIDE" if in_buf else "OFFSET"
            sog_cog = f"{cp['speed']:.1f}kn / {cp['heading']:.0f}°" if (cp["speed"] > 0 or cp["heading"] > 0) else "Nominal"
            manifest_rows.append([
                f"#{cp['target_id']}",
                cp["vessel_name"][:18],
                str(cp["mmsi"])[:10],
                cp["vessel_type"][:14],
                sog_cog,
                f"{format_lat(cp['ais_lat'])}, {format_lon(cp['ais_lon'])}",
                f"{format_lat(cp['sar_lat'])}, {format_lon(cp['sar_lon'])}",
                f"{cp['dist_m']:.0f}m",
                st,
            ])

        if not manifest_rows:
            manifest_rows = [["-", "NO CORRELATED CONTACTS IDENTIFIED", "-", "-", "-", "-", "-", "-", "-"]]

        tbl3_ax = fig.add_axes([0.06, 0.05, 0.88, 0.31])
        tbl3_ax.axis("off")
        tbl3 = tbl3_ax.table(
            cellText=manifest_rows,
            colLabels=manifest_headers,
            colWidths=[0.06, 0.15, 0.09, 0.10, 0.11, 0.15, 0.15, 0.08, 0.11],
            loc="center",
            cellLoc="center",
        )
        tbl3.auto_set_font_size(False)
        tbl3.set_fontsize(7.5)
        tbl3.scale(1.0, 1.35)

        for (r, c), cell in tbl3.get_celld().items():
            cell.set_edgecolor(border_color)
            if r == 0:
                cell.set_facecolor("#2b6cb0")
                cell.get_text().set_color("white")
                cell.get_text().set_fontweight("bold")
            else:
                cell.set_facecolor(panel_color)
                cell.get_text().set_color(text_primary)
                row_idx = r - 1
                if c == 7:  # Offset
                    cell.get_text().set_color("#f39c12")
                    cell.get_text().set_fontweight("bold")
                elif c == 8:  # Status
                    if row_idx < len(manifest_rows) and manifest_rows[row_idx][8] == "INSIDE":
                        cell.get_text().set_color("#2ecc71")
                    else:
                        cell.get_text().set_color("#f39c12")
                    cell.get_text().set_fontweight("bold")

    def render_figure_content(
        self,
        fig: plt.Figure,
        scan: Scan,
        detections: list[dict[str, Any]],
        buffer_meters: float = 500.0,
        dark_theme: bool = True,
    ) -> None:
        """Populate a Matplotlib Figure with the multi-sensor correlation tactical display (standalone view)."""
        panel_color = "#1c2541" if dark_theme else "#ffffff"
        border_color = "#3a506b" if dark_theme else "#cbd5e0"
        text_primary = "#ffffff" if dark_theme else "#1a202c"
        text_secondary = "#a0aec0" if dark_theme else "#718096"
        grid_color = "#203a43" if dark_theme else "#cbd5e0"

        # 1. Header Banner
        header_ax = fig.add_axes([0.04, 0.92, 0.92, 0.06])
        header_ax.axis("off")
        header_ax.text(
            0.0, 0.70,
            "KINEMATIC ROUTE PREDICTION & MULTI-SENSOR CORRELATION",
            fontsize=12.5,
            fontweight="bold",
            color="#00d2d3" if dark_theme else "#2b6cb0",
        )
        scan_id = scan.folder_name
        acq_str = scan.acquisition.acquired_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        header_ax.text(
            0.0, 0.15,
            f"Scan AOI: {scan_id}  |  Sensor: {scan.acquisition.satellite}  |  Pass Epoch: {acq_str}  |  Corridor Buffer: ±{buffer_meters:.0f}m",
            fontsize=9.0,
            color=text_secondary,
        )
        header_ax.text(
            1.0, 0.70,
            "TACTICAL FUSION",
            fontsize=9.5,
            fontweight="bold",
            color="#f39c12" if dark_theme else "#c05621",
            ha="right",
        )

        correlated_pairs = extract_correlated_pairs(detections)
        primary = correlated_pairs[0] if correlated_pairs else None

        # 2. Main Tactical Map (Left)
        map_ax = fig.add_axes([0.075, 0.08, 0.585, 0.81])
        map_ax.set_facecolor("#0f172a" if dark_theme else "#edf2f7")
        map_ax.grid(True, linestyle="--", alpha=0.35, color=grid_color, zorder=2)

        if primary and primary["ais_lat"] != 0.0 and primary["sar_lat"] != 0.0:
            spd_knots = max(8.0, primary["speed"]) if primary["speed"] > 0 else 12.0
            hdg_deg = primary["heading"] if primary["heading"] > 0 else 90.0

            if primary["heading"] == 0.0:
                d_lat = primary["sar_lat"] - primary["ais_lat"]
                d_lon = (primary["sar_lon"] - primary["ais_lon"]) * math.cos(math.radians(primary["ais_lat"]))
                hdg_deg = math.degrees(math.atan2(d_lon, d_lat)) % 360.0

            hist_pts: list[tuple[float, float]] = []
            for t_sec in [-3600.0, -2400.0, -1200.0, 0.0]:
                h_lat, h_lon, _ = dead_reckon_position(primary["ais_lat"], primary["ais_lon"], spd_knots, hdg_deg, t_sec)
                hist_pts.append((h_lat, h_lon))

            pred_pts: list[tuple[float, float]] = []
            for t_sec in [0.0, 1200.0, 2400.0, 3600.0]:
                f_lat, f_lon, _ = dead_reckon_position(primary["ais_lat"], primary["ais_lon"], spd_knots, hdg_deg, t_sec)
                pred_pts.append((f_lat, f_lon))

            full_route = hist_pts + pred_pts[1:]

            pts_lat = [p[0] for p in full_route] + [primary["sar_lat"]]
            pts_lon = [p[1] for p in full_route] + [primary["sar_lon"]]
            for cp in correlated_pairs[:8]:
                if cp["ais_lat"] != 0.0:
                    pts_lat.extend([cp["ais_lat"], cp["sar_lat"]])
                    pts_lon.extend([cp["ais_lon"], cp["sar_lon"]])

            min_c_lon, max_c_lon = min(pts_lon), max(pts_lon)
            min_c_lat, max_c_lat = min(pts_lat), max(pts_lat)
            lon_margin = max((max_c_lon - min_c_lon) * 0.25, 0.015)
            lat_margin = max((max_c_lat - min_c_lat) * 0.25, 0.012)
            map_ax.set_xlim(min_c_lon - lon_margin, max_c_lon + lon_margin)
            map_ax.set_ylim(min_c_lat - lat_margin, max_c_lat + lat_margin)

            corridor_poly = compute_corridor_polygon(full_route, buffer_meters=buffer_meters)
            if corridor_poly:
                poly_lons = [p[1] for p in corridor_poly]
                poly_lats = [p[0] for p in corridor_poly]
                map_ax.fill(
                    poly_lons, poly_lats,
                    color="#00d2d3" if dark_theme else "#68d391",
                    alpha=0.18,
                    edgecolor="#00d2d3" if dark_theme else "#38a169",
                    linestyle="--",
                    linewidth=1.2,
                    label=f"Corridor Buffer (±{buffer_meters:.0f}m)",
                    zorder=3,
                )

            # Historical Track
            h_lats = [p[0] for p in hist_pts]
            h_lons = [p[1] for p in hist_pts]
            map_ax.plot(h_lons, h_lats, color="#3498db", linestyle=":", linewidth=2.2, label="Traced Historical Track (-60m to 0m)", zorder=4)
            map_ax.scatter(h_lons[:-1], h_lats[:-1], color="#3498db", edgecolors="white", linewidths=0.8, s=40, marker="o", zorder=4)

            # Predicted Route
            p_lats = [p[0] for p in pred_pts]
            p_lons = [p[1] for p in pred_pts]
            map_ax.plot(p_lons, p_lats, color="#2ecc71", linestyle="--", linewidth=2.5, label="Predicted Forward Route (0m to +60m)", zorder=5)
            for i, (wp_lat, wp_lon) in enumerate(pred_pts[1:], start=1):
                map_ax.scatter(wp_lon, wp_lat, color="#2ecc71", edgecolors="white", s=50, marker="^", zorder=6)
                map_ax.annotate(f"+{i * 20}m", xy=(wp_lon, wp_lat), xytext=(6, 4), textcoords="offset points", fontsize=7, fontweight="bold", color="#2ecc71")

            # AIS Fix
            map_ax.scatter([primary["ais_lon"]], [primary["ais_lat"]], color="#2980b9", edgecolors="white", linewidths=1.5, marker="s", s=110, label=f"Reported AIS Fix: {primary['vessel_name']}", zorder=7)

            # SAR Contact
            sar_d = primary["detection"]
            geo_poly = sar_d.get("geo_polygon") or sar_d.get("raw_detection", {}).get("geo_polygon")
            if geo_poly and len(geo_poly) >= 4:
                g_lats = [pt[0] for pt in geo_poly] + [geo_poly[0][0]]
                g_lons = [pt[1] for pt in geo_poly] + [geo_poly[0][1]]
                map_ax.plot(g_lons, g_lats, color="#e74c3c", linewidth=2.0, label=f"SAR Radar Detection (Target #{primary['target_id']})", zorder=8)
                map_ax.fill(g_lons, g_lats, color="#e74c3c", alpha=0.35, zorder=7)

            map_ax.scatter([primary["sar_lon"]], [primary["sar_lat"]], color="#e74c3c", edgecolors="white", linewidths=1.5, marker="X", s=130, zorder=9)

            # Correlation Vector
            offset_dist = primary["dist_m"] if primary["dist_m"] > 0 else haversine_distance_meters(
                primary["ais_lat"], primary["ais_lon"], primary["sar_lat"], primary["sar_lon"]
            )
            map_ax.plot([primary["ais_lon"], primary["sar_lon"]], [primary["ais_lat"], primary["sar_lat"]], color="#f39c12", linestyle="-.", linewidth=2.0, label=f"Correlation Vector (Δ = {offset_dist:.1f}m)", zorder=7)

            mid_v_lat = (primary["ais_lat"] + primary["sar_lat"]) / 2.0
            mid_v_lon = (primary["ais_lon"] + primary["sar_lon"]) / 2.0
            map_ax.text(
                mid_v_lon, mid_v_lat,
                f" Δ = {offset_dist:.0f}m ",
                fontsize=7.5,
                fontweight="bold",
                color="#fbbf24",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#0f172a", edgecolor="#fbbf24", alpha=0.92, lw=1.2),
                ha="center",
                va="center",
                zorder=11,
            )

            # Opposing leader lines to guarantee zero collision
            map_ax.annotate(
                f"REPORTED AIS FIX\n{primary['vessel_name']}\nSOG: {spd_knots:.1f}kn | COG: {hdg_deg:.0f}°",
                xy=(primary["ais_lon"], primary["ais_lat"]),
                xytext=(-65, 45),
                textcoords="offset points",
                fontsize=7.5,
                fontweight="bold",
                color="#38bdf8",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#1e293b", edgecolor="#38bdf8", alpha=0.95, lw=1.2),
                arrowprops=dict(arrowstyle="->", color="#38bdf8", lw=1.2, connectionstyle="arc3,rad=0.08"),
                zorder=12,
            )

            map_ax.annotate(
                f"SAR CONTACT #{primary['target_id']}\nL: {primary['length_m']:.0f}m, B: {primary['beam_m']:.0f}m\nConf: {primary['confidence']*100:.0f}%",
                xy=(primary["sar_lon"], primary["sar_lat"]),
                xytext=(65, -45),
                textcoords="offset points",
                fontsize=7.5,
                fontweight="bold",
                color="#f87171",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#1e293b", edgecolor="#f87171", alpha=0.95, lw=1.2),
                arrowprops=dict(arrowstyle="->", color="#f87171", lw=1.2, connectionstyle="arc3,rad=-0.08"),
                zorder=12,
            )

        else:
            bbox = scan.bbox
            map_ax.set_xlim(bbox.min_longitude, bbox.max_longitude)
            map_ax.set_ylim(bbox.min_latitude, bbox.max_latitude)

        for cp in correlated_pairs[1:8]:
            map_ax.scatter([cp["sar_lon"]], [cp["sar_lat"]], color="#3498db", edgecolors="white", marker="o", s=45, alpha=0.75, zorder=4)
            map_ax.scatter([cp["ais_lon"]], [cp["ais_lat"]], color="#2980b9", edgecolors="white", marker="s", s=40, alpha=0.6, zorder=4)
            map_ax.plot([cp["ais_lon"], cp["sar_lon"]], [cp["ais_lat"], cp["sar_lat"]], color="#f39c12", linestyle=":", linewidth=1.0, alpha=0.5, zorder=3)

        map_ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{abs(x):.3f}°{'E' if x >= 0 else 'W'}"))
        map_ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{abs(y):.3f}°{'N' if y >= 0 else 'S'}"))
        map_ax.tick_params(labelsize=8, colors=text_secondary)
        map_ax.set_xlabel("Longitude (WGS84)", fontsize=8.5, color=text_primary, fontweight="bold")
        map_ax.set_ylabel("Latitude (WGS84)", fontsize=8.5, color=text_primary, fontweight="bold")

        handles, labels = map_ax.get_legend_handles_labels()
        if handles:
            map_ax.legend(
                loc="upper left",
                fontsize=7.5,
                framealpha=0.92,
                facecolor=panel_color,
                edgecolor=border_color,
                labelcolor=text_primary,
            )

        # 3. Telemetry Scorecard & Details Panel (Right side)
        side_ax = fig.add_axes([0.68, 0.08, 0.28, 0.81])
        side_ax.axis("off")

        card_rect = patches.FancyBboxPatch(
            (0.0, 0.49), 1.0, 0.51,
            boxstyle="round,pad=0.03",
            facecolor=panel_color,
            edgecolor=border_color,
            linewidth=1.2,
        )
        side_ax.add_patch(card_rect)

        side_ax.text(
            0.05, 0.96,
            "PRIMARY TARGET TELEMETRY",
            fontsize=10.0,
            fontweight="bold",
            color="#00d2d3" if dark_theme else "#2b6cb0",
        )

        if primary:
            offset_dist = primary["dist_m"] if primary["dist_m"] > 0 else haversine_distance_meters(
                primary["ais_lat"], primary["ais_lon"], primary["sar_lat"], primary["sar_lon"]
            )
            spd_knots = max(8.0, primary["speed"]) if primary["speed"] > 0 else 12.0
            hdg_deg = primary["heading"] if primary["heading"] > 0 else 90.0
            if primary["heading"] == 0.0:
                d_lat = primary["sar_lat"] - primary["ais_lat"]
                d_lon = (primary["sar_lon"] - primary["ais_lon"]) * math.cos(math.radians(primary["ais_lat"]))
                hdg_deg = math.degrees(math.atan2(d_lon, d_lat)) % 360.0

            status_text = "WITHIN ROUTE CORRIDOR" if offset_dist <= buffer_meters else "OUTSIDE BUFFER"
            status_color = "#2ecc71" if offset_dist <= buffer_meters else "#f39c12"

            fields = [
                ("Vessel Name", primary["vessel_name"][:20]),
                ("MMSI / IMO", f"{primary['mmsi']} / {primary['imo'][:8]}"),
                ("Classification", primary["vessel_type"][:20]),
                ("Reported Speed", f"{spd_knots:.1f} knots"),
                ("Heading / Course", f"{hdg_deg:.0f}°"),
                ("AIS Location", f"{primary['ais_lat']:.4f}°N, {abs(primary['ais_lon']):.4f}°W"),
                ("SAR Radar Loc", f"{primary['sar_lat']:.4f}°N, {abs(primary['sar_lon']):.4f}°W"),
                ("Radar Length x Beam", f"{primary['length_m']:.0f}m x {primary['beam_m']:.0f}m"),
                ("Radar Confidence", f"{primary['confidence']*100:.0f}%"),
                ("Correlation Offset", f"Δ = {offset_dist:.1f} m"),
            ]

            y_pos = 0.90
            for label, val in fields:
                side_ax.text(0.05, y_pos, label, fontsize=7.5, color=text_secondary, fontweight="bold")
                side_ax.text(0.95, y_pos, val, fontsize=7.5, color=text_primary, ha="right")
                y_pos -= 0.034

            # Buffer compliance badge (positioned clearly below fields)
            badge_rect = patches.FancyBboxPatch(
                (0.05, 0.515), 0.90, 0.040,
                boxstyle="round,pad=0.01",
                facecolor="#102a43" if dark_theme else "#edf2f7",
                edgecolor=status_color,
                linewidth=1.2,
            )
            side_ax.add_patch(badge_rect)
            side_ax.text(
                0.50, 0.535,
                f"CORRIDOR STATUS: {status_text}",
                fontsize=7.5,
                fontweight="bold",
                color=status_color,
                ha="center",
                va="center",
            )
        else:
            side_ax.text(
                0.50, 0.75,
                "No AIS-correlated vessels\nidentified in current pass.",
                fontsize=9,
                color=text_secondary,
                ha="center",
            )

        # Bottom Multi-Target Summary Table
        table_rect = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 0.46,
            boxstyle="round,pad=0.03",
            facecolor=panel_color,
            edgecolor=border_color,
            linewidth=1.2,
        )
        side_ax.add_patch(table_rect)

        side_ax.text(
            0.05, 0.42,
            "CORRELATED CONTACTS MANIFEST",
            fontsize=9.5,
            fontweight="bold",
            color=text_primary,
        )

        headers = ["Target", "Vessel Name", "Offset", "Status"]
        table_rows = []
        for cp in correlated_pairs[:7]:
            in_buf = cp["dist_m"] <= buffer_meters
            st = "INSIDE" if in_buf else "OFFSET"
            table_rows.append([
                f"#{cp['target_id']}",
                cp["vessel_name"][:14],
                f"{cp['dist_m']:.0f}m",
                st,
            ])

        if not table_rows:
            table_rows = [["-", "NO CORRELATED CONTACTS", "-", "-"]]

        t_ax = fig.add_axes([0.69, 0.06, 0.26, 0.33])
        t_ax.axis("off")
        tbl = t_ax.table(
            cellText=table_rows,
            colLabels=headers,
            colWidths=[0.18, 0.44, 0.20, 0.18],
            loc="center",
            cellLoc="center",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(7.5)
        tbl.scale(1.0, 1.25)

        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor(border_color)
            if r == 0:
                cell.set_facecolor("#2b6cb0")
                cell.get_text().set_color("white")
                cell.get_text().set_fontweight("bold")
            else:
                cell.set_facecolor(panel_color)
                cell.get_text().set_color(text_primary)
                if c == 3:
                    is_in = table_rows[r - 1][3] == "INSIDE"
                    cell.get_text().set_color("#2ecc71" if is_in else "#f39c12")
                    cell.get_text().set_fontweight("bold")

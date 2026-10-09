"""Matplotlib and Pillow implementation of Maritime Intelligence Brief PDF generator.

Produces publication-grade, multi-page intelligence briefing packets featuring:
- Page 1: Tactical Executive Summary, KPIs, SAR Map, Length Distribution, Metadata
- Page 2: Nautical Chart Tactical Overlay (OpenSeaMap seamarks & hydrographic basemap)
- Pages 3+: Individual Dedicated Full-Page Tactical Dossiers for Dark / Non-Cooperative Vessels
            (High-res radar chip, radar morphometry & physical signature, local tactical context map inset,
            security & SOLAS Chapter V Regulation 19 compliance assessment, tactical interdiction directives)
- Next Pages: Cooperative Vessel Intelligence Catalog (4 verified AIS-correlated ships per page in 2x2 grid)
- Final Pages: Paginated Detection Manifest & Compliance Log Table (strictly bounded, zero cutoff)
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from PIL import Image

from sentinel_analysis.application.use_cases.generate_briefing import extract_scan_intelligence
from sentinel_analysis.domain.entities import Scan
from sentinel_analysis.infrastructure.detection.classifier import get_vessel_hierarchy_label
from sentinel_analysis.infrastructure.reporting.nautical_chart import (
    build_nautical_basemap,
    render_nautical_chart_overlay,
)
from sentinel_analysis.infrastructure.reporting.route_visualizer import RouteVisualizer

logger = logging.getLogger(__name__)


def format_lat(lat: Optional[float]) -> str:
    """Format latitude with cardinal hemisphere suffix (e.g. 35.8457°N)."""
    if lat is None:
        return "N/A"
    direction = "N" if lat >= 0 else "S"
    return f"{abs(lat):.4f}°{direction}"


def format_lon(lon: Optional[float]) -> str:
    """Format longitude with cardinal hemisphere suffix (e.g. 6.0992°W)."""
    if lon is None:
        return "N/A"
    direction = "E" if lon >= 0 else "W"
    return f"{abs(lon):.4f}°{direction}"


def format_dms(deg_val: Optional[float], is_lat: bool) -> str:
    """Format decimal degrees into Degrees Minutes Seconds with cardinal direction."""
    if deg_val is None:
        return "N/A"
    abs_val = abs(deg_val)
    d = int(abs_val)
    m = int((abs_val - d) * 60)
    s = (abs_val - d - m / 60) * 3600
    if is_lat:
        hemi = "N" if deg_val >= 0 else "S"
        return f"{d:02d}° {m:02d}' {s:04.1f}\" {hemi}"
    else:
        hemi = "E" if deg_val >= 0 else "W"
        return f"{d:03d}° {m:02d}' {s:04.1f}\" {hemi}"


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great Circle distance in meters between two lat/lon pairs."""
    r = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


class MatplotlibIntelligenceBriefGenerator:
    """Generates multi-page, publication-grade maritime intelligence briefings as PDF files."""

    def __init__(self, route_visualizer: Optional[RouteVisualizer] = None) -> None:
        self.route_visualizer = route_visualizer or RouteVisualizer()

    def render_dark_vessel_dossier_page(
        self,
        fig: plt.Figure,
        target: dict[str, Any],
        scan: Scan,
        all_detections: list[dict[str, Any]],
        sar_full: Optional[np.ndarray],
        nautical_basemap: Optional[np.ndarray],
        basemap_extent: Optional[list[float]],
        page_num: int,
        total_dark_pages: int,
    ) -> None:
        """Render a publication-grade tactical intelligence dossier for an individual non-cooperative dark vessel."""
        t_id = target.get("id", target.get("index", "?"))
        is_solas = target.get("is_solas_suspect", False)
        t_lat = target.get("latitude")
        t_lon = target.get("longitude")
        px = int(target.get("pixel_x") or target.get("center_x") or 0)
        py = int(target.get("pixel_y") or target.get("center_y") or 0)
        length_m = float(target.get("length_m", 0) or 0)
        width_m = float(target.get("width_m", 0) or 0)
        aspect_ratio = length_m / max(width_m, 1.0)
        conf = float(target.get("confidence", 0) or 0)
        base_v_class = (
            target.get("vessel_class")
            or target.get("vessel_type")
            or target.get("raw_detection", {}).get("vessel_class")
            or target.get("raw_detection", {}).get("estimated_class")
        )
        vessel_class = get_vessel_hierarchy_label(base_v_class, length_m, width_m)
        reasons = target.get("dark_vessel_reasons") or []
        risk_level = target.get("dark_vessel_risk", "HIGH" if is_solas else "MEDIUM")
        acq = scan.acquisition

        # -------------------------------------------------------------------------
        # 1. Top Threat Banner
        # -------------------------------------------------------------------------
        banner_ax = fig.add_axes([0.05, 0.905, 0.90, 0.075])
        banner_ax.axis("off")

        border_col = "#c53030" if is_solas else "#dd6b20"
        bg_col = "#fff5f5" if is_solas else "#fffaf0"
        badge_patch = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.015",
            facecolor=bg_col,
            edgecolor=border_col,
            linewidth=1.5,
        )
        banner_ax.add_patch(badge_patch)

        banner_ax.text(
            0.02, 0.65,
            f"TACTICAL TARGET DOSSIER // NON-COOPERATIVE CONTACT #{t_id}",
            fontsize=13,
            fontweight="bold",
            color="#9b2c2c" if is_solas else "#c05621",
            va="center",
        )
        sub_text = (
            "SOLAS CH. V REG. 19 VIOLATION: MANDATORY AIS DISABLED // INTERDICTION CANDIDATE"
            if is_solas
            else "UNIDENTIFIED NON-REPORTING SURFACE CONTACT // SURVEILLANCE PRIORITY"
        )
        banner_ax.text(
            0.02, 0.25,
            sub_text,
            fontsize=8.5,
            fontweight="bold",
            color="#4a5568",
            va="center",
        )

        banner_ax.text(
            0.98, 0.65,
            "PRIORITY INTERDICTION" if is_solas else "TACTICAL REVIEW",
            fontsize=11,
            fontweight="bold",
            color="#c53030" if is_solas else "#dd6b20",
            ha="right",
            va="center",
        )
        banner_ax.text(
            0.98, 0.25,
            f"DARK TARGET {page_num} OF {total_dark_pages}",
            fontsize=8.5,
            fontweight="bold",
            color="#718096",
            ha="right",
            va="center",
        )

        # -------------------------------------------------------------------------
        # 2. Top-Left Panel: High-Resolution SAR Radar Chip
        # -------------------------------------------------------------------------
        chip_frame = fig.add_axes([0.05, 0.44, 0.43, 0.45])
        chip_frame.axis("off")
        chip_box = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.012",
            facecolor="white",
            edgecolor="#cbd5e0",
            linewidth=1.2,
        )
        chip_frame.add_patch(chip_box)

        hdr_patch = patches.FancyBboxPatch(
            (0.0, 0.90), 1.0, 0.10,
            boxstyle="round,pad=0.008",
            facecolor="#1a365d",
            edgecolor="#1a365d",
        )
        chip_frame.add_patch(hdr_patch)
        chip_frame.text(
            0.04, 0.95,
            "HIGH-RESOLUTION SAR RADAR SENSOR CHIP",
            fontsize=8.5,
            fontweight="bold",
            color="white",
            va="center",
        )

        chip_ax = fig.add_axes([0.065, 0.455, 0.40, 0.38])
        chip_drawn = False
        crop_max_dn = 0.0

        if sar_full is not None and sar_full.size > 0:
            h, w = sar_full.shape
            rad = max(40, min(int(length_m / 10.0 * 8), 120))
            x1, x2 = max(0, px - rad), min(w, px + rad)
            y1, y2 = max(0, py - rad), min(h, py + rad)
            if x2 > x1 and y2 > y1:
                crop = sar_full[y1:y2, x1:x2]
                crop_max_dn = float(crop.max())
                p2, p98 = np.percentile(crop, (2, 98))
                if p98 > p2:
                    stretched = np.clip((crop - p2) / (p98 - p2) * 255.0, 0, 255).astype(np.uint8)
                else:
                    stretched = crop
                chip_ax.imshow(stretched, cmap="gray", origin="upper")
                cx = px - x1
                cy = py - y1
                chip_ax.axhline(cy, color="#e53e3e", linestyle=":", alpha=0.8, linewidth=1.2)
                chip_ax.axvline(cx, color="#e53e3e", linestyle=":", alpha=0.8, linewidth=1.2)

                # 100m Scale bar: assume ~10m per pixel for Sentinel-1 GRD
                scale_len_px = 10  # 100 meters = 10 px
                chip_h, chip_w = stretched.shape
                sb_x = max(8, chip_w - scale_len_px - 25)
                sb_y = chip_h - 10
                chip_ax.plot([sb_x, sb_x + scale_len_px], [sb_y, sb_y], color="#00ffff", lw=2.5)
                chip_ax.text(sb_x + scale_len_px / 2, sb_y - 4, "100m", color="#00ffff", fontsize=6.5, ha="center", fontweight="bold")

                chip_drawn = True

        if not chip_drawn:
            synth = np.zeros((80, 80), dtype=np.uint8)
            synth[32:48, 30:50] = 220
            chip_ax.imshow(synth, cmap="gray")
            chip_ax.axhline(40, color="#e53e3e", linestyle=":", alpha=0.8)
            chip_ax.axvline(40, color="#e53e3e", linestyle=":", alpha=0.8)

        chip_ax.set_xticks([])
        chip_ax.set_yticks([])

        chip_ax.text(
            0.03, 0.94,
            f"Center: [X: {px}, Y: {py}]",
            transform=chip_ax.transAxes,
            fontsize=7,
            fontweight="bold",
            color="#f7fafc",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a202c", alpha=0.75, edgecolor="none"),
        )

        # -------------------------------------------------------------------------
        # 3. Bottom-Left Panel: Radar Morphometry & Signature Card
        # -------------------------------------------------------------------------
        card_frame = fig.add_axes([0.05, 0.12, 0.43, 0.30])
        card_frame.axis("off")
        card_box = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.012",
            facecolor="white",
            edgecolor="#cbd5e0",
            linewidth=1.2,
        )
        card_frame.add_patch(card_box)

        hdr2 = patches.FancyBboxPatch(
            (0.0, 0.86), 1.0, 0.14,
            boxstyle="round,pad=0.008",
            facecolor="#edf2f7",
            edgecolor="#cbd5e0",
        )
        card_frame.add_patch(hdr2)
        card_frame.text(
            0.04, 0.93,
            "RADAR MORPHOMETRY & PHYSICAL SIGNATURE",
            fontsize=8.5,
            fontweight="bold",
            color="#1a365d",
            va="center",
        )

        sig_items = [
            ("Estimated Length", f"{length_m:.1f} meters"),
            ("Estimated Beam (Width)", f"{width_m:.1f} meters"),
            ("Aspect Ratio (L / B)", f"{aspect_ratio:.1f} : 1"),
            ("Radar Detection Conf", f"{conf * 100:.1f}%"),
            ("Vessel Classification", f"{vessel_class[:24]}"),
            ("Peak Radar Return", f"{crop_max_dn:.0f} DN (8-bit)" if crop_max_dn > 0 else "High Backscatter"),
            ("Sensor & Polarization", f"{acq.satellite} ({', '.join(acq.polarizations) if acq.polarizations else 'SAR'})"),
            ("Orbit Geometry", f"{acq.orbit_direction or 'Ascending'} (Pass {acq.relative_orbit or 'N/A'})"),
        ]

        y_pos = 0.74
        for lbl, val in sig_items:
            card_frame.text(0.04, y_pos, lbl, fontsize=7.2, color="#718096", fontweight="bold", va="center")
            card_frame.text(0.96, y_pos, val, fontsize=7.2, color="#1a202c", ha="right", va="center")
            y_pos -= 0.095

        # -------------------------------------------------------------------------
        # 4. Top-Right Panel: Local Tactical Context Map (Inset)
        # -------------------------------------------------------------------------
        ctx_frame = fig.add_axes([0.52, 0.44, 0.43, 0.45])
        ctx_frame.axis("off")
        ctx_box = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.012",
            facecolor="white",
            edgecolor="#cbd5e0",
            linewidth=1.2,
        )
        ctx_frame.add_patch(ctx_box)

        hdr3 = patches.FancyBboxPatch(
            (0.0, 0.90), 1.0, 0.10,
            boxstyle="round,pad=0.008",
            facecolor="#1a365d",
            edgecolor="#1a365d",
        )
        ctx_frame.add_patch(hdr3)
        ctx_frame.text(
            0.04, 0.95,
            "LOCAL TACTICAL CONTEXT & TRAFFIC DENSITY (1.5 NM RANGE)",
            fontsize=8.5,
            fontweight="bold",
            color="white",
            va="center",
        )

        ctx_ax = fig.add_axes([0.535, 0.455, 0.40, 0.38])
        ctx_ax.set_facecolor("#0b192c")

        delta_lat = 0.025
        cos_lat = math.cos(math.radians(t_lat)) if t_lat is not None else 1.0
        delta_lon = delta_lat / max(cos_lat, 0.1)

        t_lat_val = t_lat if t_lat is not None else 0.0
        t_lon_val = t_lon if t_lon is not None else 0.0

        min_x, max_x = t_lon_val - delta_lon, t_lon_val + delta_lon
        min_y, max_y = t_lat_val - delta_lat, t_lat_val + delta_lat

        if nautical_basemap is not None and basemap_extent is not None:
            ctx_ax.imshow(
                nautical_basemap,
                extent=basemap_extent,
                origin="upper",
                alpha=0.9,
            )

        ctx_ax.set_xlim(min_x, max_x)
        ctx_ax.set_ylim(min_y, max_y)

        # Draw Range Rings around target: 0.5 NM and 1.0 NM
        for nm_radius, ring_color, lstyle in [(0.5, "#63b3ed", "--"), (1.0, "#4299e1", ":")]:
            r_lat = (nm_radius * 1852.0) / 111320.0
            r_lon = r_lat / max(cos_lat, 0.1)
            ring = patches.Ellipse(
                (t_lon_val, t_lat_val),
                width=r_lon * 2,
                height=r_lat * 2,
                edgecolor=ring_color,
                facecolor="none",
                linestyle=lstyle,
                linewidth=1.0,
                alpha=0.7,
                zorder=6,
            )
            ctx_ax.add_patch(ring)
            ctx_ax.text(
                t_lon_val, t_lat_val + r_lat,
                f" {nm_radius} NM",
                fontsize=6,
                color=ring_color,
                fontweight="bold",
                va="bottom",
                ha="center",
                zorder=7,
            )

        # Plot nearby cooperative AIS vessels
        closest_ais_name = "None within 5 NM"
        closest_ais_dist_nm = 999.0
        closest_sog = 0.0

        for other in all_detections:
            o_lat = other.get("latitude")
            o_lon = other.get("longitude")
            if o_lat is None or o_lon is None:
                continue
            if other.get("id") == target.get("id") or other.get("index") == target.get("index"):
                continue

            dist_m = haversine_distance_meters(t_lat_val, t_lon_val, o_lat, o_lon)
            dist_nm = dist_m / 1852.0

            ais_info = other.get("raw_detection", {}).get("correlated_ais") or other.get("correlated_ais") or {}
            o_name = other.get("vessel_name") or ais_info.get("vessel_name") or f"MMSI {other.get('mmsi') or 'Target'}"

            if not other.get("is_dark"):
                if dist_nm < closest_ais_dist_nm:
                    closest_ais_dist_nm = dist_nm
                    closest_ais_name = o_name
                    closest_sog = float(ais_info.get("speed") or 0.0)

            # Plot if within map viewport
            if min_x <= o_lon <= max_x and min_y <= o_lat <= max_y:
                o_dark = other.get("is_dark", False)
                dot_color = "#e53e3e" if o_dark else "#48bb78"
                ctx_ax.scatter(o_lon, o_lat, color=dot_color, s=50, edgecolors="white", linewidth=1.0, zorder=8)
                ctx_ax.text(
                    o_lon + 0.001, o_lat + 0.001,
                    f"{o_name[:12]} ({dist_nm:.1f} NM)",
                    fontsize=6,
                    color="#ffffff" if nautical_basemap is None else "#1a202c",
                    fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="#ffffff", alpha=0.8, edgecolor="none"),
                    zorder=9,
                )

        # Plot Dark Vessel target marker (centered)
        ctx_ax.scatter(
            t_lon_val, t_lat_val,
            color="#e53e3e",
            marker="D",
            s=110,
            edgecolors="white",
            linewidth=1.5,
            zorder=12,
        )
        target_ring = patches.Ellipse(
            (t_lon_val, t_lat_val),
            width=0.004 / max(cos_lat, 0.1),
            height=0.004,
            edgecolor="#e53e3e",
            facecolor="none",
            linestyle="-",
            linewidth=1.8,
            zorder=11,
        )
        ctx_ax.add_patch(target_ring)
        ctx_ax.text(
            t_lon_val, t_lat_val - 0.003,
            f"TARGET #{t_id} (DARK)",
            fontsize=7.5,
            fontweight="bold",
            color="white",
            ha="center",
            va="top",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#9b2c2c", edgecolor="white", linewidth=0.8),
            zorder=13,
        )

        ctx_ax.tick_params(labelsize=6.5)
        ctx_ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda val, pos: format_lon(val)))
        ctx_ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda val, pos: format_lat(val)))
        ctx_ax.grid(True, linestyle=":", alpha=0.5, color="#a0aec0")

        # -------------------------------------------------------------------------
        # 5. Bottom-Right Panel: Security & Regulatory Compliance Assessment
        # -------------------------------------------------------------------------
        sec_frame = fig.add_axes([0.52, 0.12, 0.43, 0.30])
        sec_frame.axis("off")
        sec_box = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.012",
            facecolor="white",
            edgecolor="#cbd5e0",
            linewidth=1.2,
        )
        sec_frame.add_patch(sec_box)

        hdr4 = patches.FancyBboxPatch(
            (0.0, 0.86), 1.0, 0.14,
            boxstyle="round,pad=0.008",
            facecolor="#edf2f7",
            edgecolor="#cbd5e0",
        )
        sec_frame.add_patch(hdr4)
        sec_frame.text(
            0.04, 0.93,
            "REGULATORY COMPLIANCE & RISK ASSESSMENT",
            fontsize=8.5,
            fontweight="bold",
            color="#1a365d",
            va="center",
        )

        pos_dms = f"{format_dms(t_lat, True)} / {format_dms(t_lon, False)}"
        pos_dec = f"{format_lat(t_lat)}, {format_lon(t_lon)}"

        if is_solas:
            solas_status_str = "NON-COMPLIANT // VIOLATION"
            solas_color = "#c53030"
        else:
            solas_status_str = "MONITORING // NON-REPORTING"
            solas_color = "#dd6b20"

        if closest_ais_dist_nm < 900:
            nearest_traffic_str = f"{closest_ais_name[:16]} ({closest_ais_dist_nm:.2f} NM, {closest_sog:.1f} kn)"
        else:
            nearest_traffic_str = "No active AIS traffic within 5 NM"

        compliance_items = [
            ("Position (WGS84)", pos_dec),
            ("Position (DMS)", pos_dms),
            ("SOLAS Reg 19 Carriage", solas_status_str),
            ("RF Transponder State", "SILENT / DISABLED (Zero Signal)"),
            ("Dark Risk Level", f"{risk_level} THREAT RATING"),
            ("Nearest AIS Traffic", nearest_traffic_str),
        ]

        y_pos = 0.74
        for lbl, val in compliance_items:
            sec_frame.text(0.04, y_pos, lbl, fontsize=7.2, color="#718096", fontweight="bold", va="center")
            val_col = solas_color if ("NON-COMPLIANT" in val or "HIGH" in val) else "#1a202c"
            sec_frame.text(0.96, y_pos, val, fontsize=7.2, color=val_col, ha="right", va="center", fontweight="bold" if val_col != "#1a202c" else "normal")
            y_pos -= 0.088

        reason_desc = reasons[0] if reasons else "No matching AIS position message received within spatial correlation tolerance."
        sec_frame.text(
            0.04, 0.20,
            f"Primary Finding:\n{reason_desc[:90]}",
            fontsize=6.8,
            color="#9b2c2c" if is_solas else "#c05621",
            fontstyle="italic",
        )

        # -------------------------------------------------------------------------
        # 6. Bottom Directive Bar: Tactical Interdiction / Action Directive
        # -------------------------------------------------------------------------
        act_frame = fig.add_axes([0.05, 0.03, 0.90, 0.07])
        act_frame.axis("off")
        act_box = patches.FancyBboxPatch(
            (0.0, 0.0), 1.0, 1.0,
            boxstyle="round,pad=0.012",
            facecolor="#fff5f5" if is_solas else "#fffaf0",
            edgecolor="#e53e3e" if is_solas else "#dd6b20",
            linewidth=1.2,
        )
        act_frame.add_patch(act_box)

        if is_solas:
            directive_header = "TACTICAL INTERDICTION DIRECTIVE // URGENT ACTION RECOMMENDED"
            directive_body = (
                "Forward target coordinates to regional Maritime Rescue Coordination Centre (MRCC) & Vessel Traffic Service (VTS).\n"
                "Target exhibits non-reporting behavior in regulated waters. Task MPA or coastal patrol vessel for physical interception & SOLAS compliance."
            )
        else:
            directive_header = "TACTICAL DIRECTIVE // SURVEILLANCE & PASSIVE TRACKING"
            directive_body = (
                "Maintain automated radar correlation track. Cross-reference coastal electro-optical/infrared (EO/IR) sensor logs\n"
                "and port authority departure manifests to resolve vessel identity."
            )

        act_frame.text(0.02, 0.78, directive_header, fontsize=7.8, fontweight="bold", color="#c53030" if is_solas else "#dd6b20", va="top")
        act_frame.text(0.02, 0.44, directive_body, fontsize=6.8, color="#4a5568", va="top", linespacing=1.3)

    def generate_brief(self, scan: Scan, output_path: Path) -> Path:
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        intel = extract_scan_intelligence(scan)
        detections = intel.get("detections", [])
        ghost_vessels = intel.get("ghost_vessels", [])
        bbox = scan.bbox
        acq = scan.acquisition
        metadata = scan.metadata or {}

        total_vessels = intel["total_vessels"]
        dark_count = intel["dark_vessels"]
        ais_count = intel["ais_correlated"]
        dark_ratio = intel["dark_percentage"]
        solas_suspects = [d for d in detections if d.get("is_solas_suspect")]
        dark_vessels = [d for d in detections if d.get("is_dark")]
        ais_vessels = [d for d in detections if not d.get("is_dark")]

        # Try loading scan imagery for backdrop & radar chips
        sar_full = None
        sar_thumb = None
        image_path = Path(scan.image_path) if scan.image_path else None
        if image_path and image_path.is_file():
            try:
                with Image.open(image_path) as im:
                    sar_full = np.array(im.convert("L"))
                    im_thumb = im.copy()
                    im_thumb.thumbnail((800, 800), Image.Resampling.BILINEAR)
                    sar_thumb = np.array(im_thumb.convert("L"))
            except Exception as exc:
                logger.warning("Could not open scan image for brief visuals: %s", exc)

        # Locate DEM path if available for nautical chart fallback
        dem_path = None
        if image_path:
            dem_candidates = list(image_path.parent.glob("*_stitched_dem.png")) or list(image_path.parent.glob("*_dem.png"))
            if dem_candidates:
                dem_path = dem_candidates[0]

        # Generate standalone route correlation image on disk as requested
        route_img_path = None
        if image_path:
            route_img_path = image_path.parent / f"{scan.folder_name}_route_correlation.png"
            try:
                self.route_visualizer.generate_route_image(
                    scan=scan,
                    detections=detections,
                    output_path=route_img_path,
                    buffer_meters=500.0,
                )
            except Exception as exc:
                logger.warning("Failed generating standalone route correlation image: %s", exc)

        # Build nautical chart basemap (OpenSeaMap seamarks + OSM / DEM fallback)
        nautical_basemap, basemap_extent = build_nautical_basemap(
            bbox,
            cache_dir=Path(".cache/nautical_tiles"),
            dem_path=dem_path,
        )

        with PdfPages(target_path) as pdf:
            # =========================================================================
            # PAGE 1: Tactical Executive Summary & Map
            # =========================================================================
            fig1 = plt.figure(figsize=(11.0, 8.5), dpi=150)
            fig1.patch.set_facecolor("#f8f9fa")

            # Header banner
            header_ax = fig1.add_axes([0.05, 0.90, 0.90, 0.08])
            header_ax.axis("off")
            header_ax.text(
                0.0, 0.70,
                "MARITIME DOMAIN AWARENESS // INTELLIGENCE BRIEFING",
                fontsize=16,
                fontweight="bold",
                color="#0f2b48",
            )
            acq_dt = acq.acquired_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            header_ax.text(
                0.0, 0.25,
                f"Scan: {scan.folder_name}  |  Sensor: {acq.satellite} ({acq.product_type})  |  Acquired: {acq_dt}",
                fontsize=10,
                color="#4a5568",
            )
            header_ax.text(
                1.0, 0.70,
                "UNCLASSIFIED // OFFICIAL",
                fontsize=11,
                fontweight="bold",
                color="#2b6cb0",
                ha="right",
            )

            # KPI Summary Blocks
            kpi_ax = fig1.add_axes([0.05, 0.77, 0.90, 0.10])
            kpi_ax.axis("off")

            kpis = [
                ("TOTAL TARGETS", str(total_vessels), "#2b6cb0"),
                ("DARK VESSELS", f"{dark_count} ({dark_ratio:.0f}%)", "#c53030" if dark_count > 0 else "#2b6cb0"),
                ("AIS CORRELATED", str(ais_count), "#2f855a"),
                ("SOLAS REG 19 VIOLATIONS", str(len(solas_suspects)), "#e53e3e" if solas_suspects else "#4a5568"),
            ]
            for i, (label, val, color) in enumerate(kpis):
                x = 0.02 + i * 0.245
                rect = patches.FancyBboxPatch(
                    (x, 0.05), 0.22, 0.85,
                    boxstyle="round,pad=0.03",
                    facecolor="white",
                    edgecolor=color,
                    linewidth=1.5,
                )
                kpi_ax.add_patch(rect)
                kpi_ax.text(x + 0.11, 0.58, val, fontsize=15, fontweight="bold", color=color, ha="center")
                kpi_ax.text(x + 0.11, 0.20, label, fontsize=8, fontweight="bold", color="#718096", ha="center")

            # Map Ax: Spatial Scatter & Bounding Area with SAR Backdrop
            map_ax = fig1.add_axes([0.06, 0.12, 0.55, 0.60])
            map_ax.set_facecolor("#edf2f7")
            map_ax.grid(True, linestyle="--", alpha=0.5, color="#cbd5e0", zorder=2)

            lon_pad = max((bbox.max_longitude - bbox.min_longitude) * 0.04, 0.01)
            lat_pad = max((bbox.max_latitude - bbox.min_latitude) * 0.04, 0.01)
            map_ax.set_xlim(bbox.min_longitude - lon_pad, bbox.max_longitude + lon_pad)
            map_ax.set_ylim(bbox.min_latitude - lat_pad, bbox.max_latitude + lat_pad)

            map_ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{abs(x):.2f}°{'E' if x >= 0 else 'W'}"))
            map_ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{abs(y):.2f}°{'N' if y >= 0 else 'S'}"))
            map_ax.set_xlabel("Longitude", fontsize=9, fontweight="bold")
            map_ax.set_ylabel("Latitude", fontsize=9, fontweight="bold")
            map_ax.set_title("Tactical Maritime Plot (WGS84)", fontsize=11, fontweight="bold", color="#1a202c")

            if sar_thumb is not None and sar_thumb.size > 0:
                map_ax.imshow(
                    sar_thumb,
                    cmap="gray",
                    extent=[bbox.min_longitude, bbox.max_longitude, bbox.min_latitude, bbox.max_latitude],
                    origin="upper",
                    aspect="auto",
                    alpha=0.70,
                    zorder=1,
                )

            aoi_rect = patches.Rectangle(
                (bbox.min_longitude, bbox.min_latitude),
                bbox.max_longitude - bbox.min_longitude,
                bbox.max_latitude - bbox.min_latitude,
                linewidth=1.2,
                edgecolor="#3182ce",
                facecolor="none",
                alpha=0.7,
                linestyle="--",
                zorder=2,
            )
            map_ax.add_patch(aoi_rect)

            ais_lons = [d["longitude"] for d in ais_vessels if d.get("longitude") is not None]
            ais_lats = [d["latitude"] for d in ais_vessels if d.get("latitude") is not None]
            if ais_lons:
                map_ax.scatter(
                    ais_lons, ais_lats,
                    c="#3182ce", edgecolors="white", linewidths=0.6,
                    marker="o", s=55,
                    label=f"AIS Correlated ({len(ais_lons)})",
                    zorder=4,
                )

            non_solas_dark = [d for d in dark_vessels if not d.get("is_solas_suspect")]
            dark_lons = [d["longitude"] for d in non_solas_dark if d.get("longitude") is not None]
            dark_lats = [d["latitude"] for d in non_solas_dark if d.get("latitude") is not None]
            if dark_lons:
                map_ax.scatter(
                    dark_lons, dark_lats,
                    c="#e53e3e", edgecolors="white", linewidths=0.7,
                    marker="^", s=70,
                    label=f"Dark Target ({len(dark_lons)})",
                    zorder=5,
                )

            solas_lons = [d["longitude"] for d in solas_suspects if d.get("longitude") is not None]
            solas_lats = [d["latitude"] for d in solas_suspects if d.get("latitude") is not None]
            if solas_lons:
                map_ax.scatter(
                    solas_lons, solas_lats,
                    c="#f6ad55", edgecolors="#9b2c2c", linewidths=1.2,
                    marker="*", s=140,
                    label=f"SOLAS Reg 19 ({len(solas_lons)})",
                    zorder=6,
                )

            ghost_lons = [
                float(g.get("longitude") or g.get("lon") or g.get("lng"))
                for g in ghost_vessels
                if (g.get("longitude") or g.get("lon") or g.get("lng")) is not None
            ]
            ghost_lats = [
                float(g.get("latitude") or g.get("lat"))
                for g in ghost_vessels
                if (g.get("latitude") or g.get("lat")) is not None
            ]
            if ghost_lons:
                map_ax.scatter(
                    ghost_lons, ghost_lats,
                    c="#805ad5", edgecolors="white", linewidths=0.6,
                    marker="D", s=50,
                    label=f"Ghost AIS ({len(ghost_lons)})",
                    zorder=5,
                )

            if total_vessels > 0 or ghost_vessels:
                map_ax.legend(loc="upper right", fontsize=8, framealpha=0.9, facecolor="white")
            else:
                map_ax.text(
                    0.5, 0.5,
                    "No vessel detections logged for this scan.\nExecute ship detection to map targets.",
                    transform=map_ax.transAxes,
                    ha="center", va="center",
                    fontsize=10, fontweight="bold",
                    color="#4a5568",
                    bbox=dict(boxstyle="round,pad=0.6", facecolor="white", edgecolor="#cbd5e0", alpha=0.9),
                    zorder=7,
                )

            # Size distribution chart
            chart_ax = fig1.add_axes([0.67, 0.45, 0.28, 0.27])
            lengths = [float(d.get("length_m", 0) or 0) for d in detections if float(d.get("length_m", 0) or 0) > 0]
            if lengths:
                bins = min(max(len(lengths), 4), 12)
                chart_ax.hist(lengths, bins=bins, color="#4299e1", edgecolor="#2b6cb0", alpha=0.85)
                chart_ax.axvline(45.0, color="#e53e3e", linestyle="--", linewidth=1.2, label="SOLAS (45m)")
                chart_ax.legend(loc="upper right", fontsize=7)
            else:
                chart_ax.text(
                    0.5, 0.5,
                    "No vessel dimensions recorded\n(Run ship detection)",
                    transform=chart_ax.transAxes,
                    ha="center", va="center",
                    fontsize=8, color="#718096",
                )
            chart_ax.set_title("Vessel Length Distribution", fontsize=10, fontweight="bold")
            chart_ax.set_xlabel("Length (meters)", fontsize=8)
            chart_ax.set_ylabel("Count", fontsize=8)
            chart_ax.tick_params(labelsize=8)
            chart_ax.grid(True, linestyle=":", alpha=0.5)

            # Metadata Table on Page 1
            table_ax = fig1.add_axes([0.67, 0.12, 0.28, 0.28])
            table_ax.axis("off")
            aoi_display = str(metadata.get("aoi_name") or metadata.get("custom_name") or "Area of Interest")
            meta_rows = [
                ("AOI Name", aoi_display[:20]),
                ("West Bound", format_lon(bbox.min_longitude)),
                ("East Bound", format_lon(bbox.max_longitude)),
                ("South Bound", format_lat(bbox.min_latitude)),
                ("North Bound", format_lat(bbox.max_latitude)),
                ("Orbit Direction", str(acq.orbit_direction or "N/A")),
                ("Relative Orbit", str(acq.relative_orbit or "N/A")),
                ("Polarizations", ", ".join(acq.polarizations) if acq.polarizations else "N/A"),
            ]
            t = table_ax.table(
                cellText=meta_rows,
                colWidths=[0.50, 0.50],
                loc="center",
                cellLoc="left",
            )
            t.auto_set_font_size(False)
            t.set_fontsize(8)
            t.scale(1.0, 1.25)
            for key, cell in t.get_celld().items():
                cell.set_edgecolor("#e2e8f0")
                if key[1] == 0:
                    cell.set_facecolor("#edf2f7")
                    cell.get_text().set_fontweight("bold")
                else:
                    cell.set_facecolor("white")

            pdf.savefig(fig1)
            plt.close(fig1)

            # =========================================================================
            # PAGE 2: Tactical Nautical Chart Overlay Plot
            # =========================================================================
            fig_naut = plt.figure(figsize=(11.0, 8.5), dpi=150)
            fig_naut.patch.set_facecolor("#f8f9fa")

            naut_header = fig_naut.add_axes([0.05, 0.91, 0.90, 0.07])
            naut_header.axis("off")
            naut_header.text(
                0.0, 0.60,
                "NAUTICAL CHART TACTICAL OVERLAY",
                fontsize=14,
                fontweight="bold",
                color="#0f2b48",
            )
            naut_header.text(
                0.0, 0.10,
                "OpenSeaMap seamarks, navigational aids, fairways, depth contours, and multi-sensor target tracking overlay.",
                fontsize=8.5,
                color="#718096",
            )
            naut_header.text(
                1.0, 0.60,
                "HYDROGRAPHIC FUSION",
                fontsize=9.5,
                fontweight="bold",
                color="#2b6cb0",
                ha="right",
            )

            # Nautical chart axes occupying primary canvas
            naut_ax = fig_naut.add_axes([0.06, 0.08, 0.88, 0.80])
            render_nautical_chart_overlay(
                ax=naut_ax,
                scan=scan,
                detections=detections,
                ghost_vessels=ghost_vessels,
                basemap_img=nautical_basemap,
                extent=basemap_extent,
            )

            pdf.savefig(fig_naut)
            plt.close(fig_naut)

            # Generate and persist the standalone route correlation PNG image
            standalone_route_img = target_path.parent / f"{scan.folder_name}_route_correlation.png"
            try:
                self.route_visualizer.generate_route_image(
                    scan=scan,
                    detections=detections,
                    output_path=standalone_route_img,
                    buffer_meters=500.0,
                )
            except Exception as r_err:
                logger.warning("Could not generate standalone route correlation image: %s", r_err)

            # =========================================================================
            # PAGES 3+: Individual Dedicated Full-Page Dossiers for Dark Vessels
            # Dedicated full-page tactical intelligence dossier for each non-cooperative target
            # =========================================================================
            sorted_dark_targets = sorted(
                dark_vessels,
                key=lambda d: (
                    1 if d.get("is_solas_suspect") else 0,
                    float(d.get("length_m", 0) or 0),
                    float(d.get("confidence", 0) or 0),
                ),
                reverse=True,
            )

            total_dark = len(sorted_dark_targets)
            for d_idx, dark_tgt in enumerate(sorted_dark_targets):
                fig_dark = plt.figure(figsize=(11.0, 8.5), dpi=150)
                fig_dark.patch.set_facecolor("#f8f9fa")
                self.render_dark_vessel_dossier_page(
                    fig=fig_dark,
                    target=dark_tgt,
                    scan=scan,
                    all_detections=detections,
                    sar_full=sar_full,
                    nautical_basemap=nautical_basemap,
                    basemap_extent=basemap_extent,
                    page_num=d_idx + 1,
                    total_dark_pages=total_dark,
                )
                pdf.savefig(fig_dark)
                plt.close(fig_dark)

            # =========================================================================
            # NEXT PAGES: Cooperative Vessel Intelligence Catalog
            # (4 verified AIS-correlated ships per page in 2x2 grid)
            # =========================================================================
            sorted_coop_targets = sorted(
                ais_vessels,
                key=lambda d: (
                    float(d.get("length_m", 0) or 0),
                    float(d.get("confidence", 0) or 0),
                ),
                reverse=True,
            )

            cards_per_page = 4
            num_coop_targets = len(sorted_coop_targets)
            num_coop_pages = math.ceil(num_coop_targets / cards_per_page) if num_coop_targets > 0 else 0

            # If total detections is 0 (neither dark nor cooperative targets)
            if total_vessels == 0:
                fig_empty = plt.figure(figsize=(11.0, 8.5), dpi=150)
                fig_empty.patch.set_facecolor("#f8f9fa")
                empty_ax = fig_empty.add_axes([0.05, 0.08, 0.90, 0.84])
                empty_ax.axis("off")
                empty_ax.text(
                    0.5, 0.55,
                    "NO VESSEL TARGETS RECORDED",
                    ha="center", va="center",
                    fontsize=14, fontweight="bold", color="#2d3748",
                )
                empty_ax.text(
                    0.5, 0.45,
                    "Automated ship detection has not yielded verified targets for this pass.\n"
                    "Execute ship detection or verify SAR imagery coverage.",
                    ha="center", va="center",
                    fontsize=10, color="#718096",
                )
                pdf.savefig(fig_empty)
                plt.close(fig_empty)
            else:
                for c_page_idx in range(num_coop_pages):
                    fig_dossier = plt.figure(figsize=(11.0, 8.5), dpi=150)
                    fig_dossier.patch.set_facecolor("#f8f9fa")

                    dossier_header = fig_dossier.add_axes([0.05, 0.91, 0.90, 0.07])
                    dossier_header.axis("off")
                    dossier_header.text(
                        0.0, 0.60,
                        f"TARGET INTELLIGENCE DOSSIER // COOPERATIVE SHIPPING (Page {c_page_idx + 1} of {num_coop_pages})",
                        fontsize=14,
                        fontweight="bold",
                        color="#0f2b48",
                    )
                    dossier_header.text(
                        0.0, 0.10,
                        "High-resolution SAR sub-crops, geographic positions, and AIS transponder correlation for verified contacts.",
                        fontsize=8.5,
                        color="#718096",
                    )
                    dossier_header.text(
                        1.0, 0.60,
                        f"TARGETS {c_page_idx * cards_per_page + 1}–{min((c_page_idx + 1) * cards_per_page, num_coop_targets)} OF {num_coop_targets}",
                        fontsize=9.5,
                        fontweight="bold",
                        color="#2b6cb0",
                        ha="right",
                    )

                    page_slice = sorted_coop_targets[c_page_idx * cards_per_page : (c_page_idx + 1) * cards_per_page]

                    # 2x2 grid layout coordinates: [row0_col0, row0_col1, row1_col0, row1_col1]
                    grid_positions = [
                        (0.05, 0.50, 0.43, 0.38),  # Top-left
                        (0.52, 0.50, 0.43, 0.38),  # Top-right
                        (0.05, 0.08, 0.43, 0.38),  # Bottom-left
                        (0.52, 0.08, 0.43, 0.38),  # Bottom-right
                    ]

                    for c_idx, target in enumerate(page_slice):
                        gx, gy, gw, gh = grid_positions[c_idx]

                        # Card background patch
                        card_ax = fig_dossier.add_axes([gx, gy, gw, gh])
                        card_ax.axis("off")

                        length_val = float(target.get("length_m", 0) or 0)
                        width_val = float(target.get("width_m", 0) or 0)
                        conf_val = float(target.get("confidence", 0) or 0)
                        t_id = target.get("id", target.get("index", "?"))

                        badge_title = f"TARGET #{t_id} // AIS CORRELATED (VERIFIED)"
                        badge_color = "#2f855a"
                        border_color = "#38a169"
                        card_bg = "#f0fff4"

                        # Draw outer card border
                        card_rect = patches.FancyBboxPatch(
                            (0.0, 0.0), 1.0, 1.0,
                            boxstyle="round,pad=0.02",
                            facecolor="white",
                            edgecolor=border_color,
                            linewidth=1.3,
                        )
                        card_ax.add_patch(card_rect)

                        # Top badge
                        header_rect = patches.FancyBboxPatch(
                            (0.0, 0.86), 1.0, 0.14,
                            boxstyle="round,pad=0.01",
                            facecolor=card_bg,
                            edgecolor=border_color,
                            linewidth=0.8,
                        )
                        card_ax.add_patch(header_rect)
                        card_ax.text(
                            0.04, 0.92,
                            badge_title,
                            fontsize=8.5,
                            fontweight="bold",
                            color=badge_color,
                            va="center",
                        )

                        # Left sub-axes: Radar Chip Crop
                        chip_ax = fig_dossier.add_axes([gx + gw * 0.04, gy + gh * 0.08, gw * 0.35, gh * 0.68])
                        chip_drawn = False

                        px = int(target.get("pixel_x") or target.get("center_x") or 0)
                        py = int(target.get("pixel_y") or target.get("center_y") or 0)

                        if sar_full is not None and sar_full.size > 0:
                            h, w = sar_full.shape
                            target_len = float(target.get("length_m", 0) or 40.0)
                            radius = max(24, min(int(target_len / 10.0 * 6), 64))
                            x1 = max(0, px - radius)
                            x2 = min(w, px + radius)
                            y1 = max(0, py - radius)
                            y2 = min(h, py + radius)
                            if x2 > x1 and y2 > y1:
                                crop = sar_full[y1:y2, x1:x2]
                                p1, p99 = np.percentile(crop, (1, 99))
                                if p99 > p1:
                                    stretched = np.clip((crop - p1) / (p99 - p1) * 255.0, 0, 255).astype(np.uint8)
                                else:
                                    stretched = crop
                                chip_ax.imshow(stretched, cmap="gray", origin="upper")

                                cx = px - x1
                                cy = py - y1
                                chip_ax.axhline(cy, color="#3182ce", linestyle=":", alpha=0.6, linewidth=1.0)
                                chip_ax.axvline(cx, color="#3182ce", linestyle=":", alpha=0.6, linewidth=1.0)
                                chip_drawn = True

                        if not chip_drawn:
                            synth = np.zeros((64, 64), dtype=np.uint8)
                            synth[26:38, 24:40] = 220
                            chip_ax.imshow(synth, cmap="gray")

                        chip_ax.set_xticks([])
                        chip_ax.set_yticks([])
                        chip_ax.set_title("SAR Radar Chip", fontsize=7.0, color="#718096", pad=2)

                        # Right sub-axes / text: Target Metadata
                        info_ax = fig_dossier.add_axes([gx + gw * 0.42, gy + gh * 0.04, gw * 0.55, gh * 0.80])
                        info_ax.axis("off")

                        lat_str = format_lat(target.get("latitude"))
                        lon_str = format_lon(target.get("longitude"))

                        ais = target.get("raw_detection", {}).get("correlated_ais") or target.get("correlated_ais") or {}

                        v_name = target.get("vessel_name") or ais.get("vessel_name") or "Unknown AIS Vessel"
                        mmsi = str(target.get("mmsi") or ais.get("mmsi") or "N/A")
                        imo = str(ais.get("imo") or "N/A")
                        base_v_type = target.get("vessel_class") or target.get("vessel_type") or ais.get("vessel_type")
                        v_type = get_vessel_hierarchy_label(base_v_type, length_val, width_val)
                        sog_val = float(ais.get("speed") or 0.0)
                        sog_str = f"{sog_val:.1f} kn" if sog_val > 0 else "Nominal / Anchor"
                        cog_val = float(ais.get("heading") or 0.0)
                        cog_str = f"{cog_val:.0f}°" if cog_val > 0 else "N/A"
                        offset_m = float(ais.get("distance_to_center_meters") or ais.get("distance_to_box_meters") or 0.0)

                        info_lines = [
                            ("Vessel Name", v_name[:18]),
                            ("MMSI // IMO", f"{mmsi} / {imo[:7]}"),
                            ("Vessel Type", v_type[:22]),
                            ("Position (SAR)", f"{lat_str}, {lon_str}"),
                            ("Position (AIS)", f"{format_lat(ais.get('latitude'))}, {format_lon(ais.get('longitude'))}"),
                            ("Dimensions", f"L: {length_val:.0f}m, B: {width_val:.0f}m"),
                            ("Speed / Course", f"{sog_str} / {cog_str}"),
                            ("Correlation Offset", f"Δ = {offset_m:.0f} m"),
                            ("Compliance", "SOLAS COMPLIANT // AIS ACTIVE"),
                        ]

                        y_text = 0.88
                        for label, val in info_lines:
                            info_ax.text(0.0, y_text, label, fontsize=6.8, color="#718096", fontweight="bold")
                            info_ax.text(1.0, y_text, val, fontsize=6.8, color="#1a202c", ha="right")
                            y_text -= 0.098

                    pdf.savefig(fig_dossier)
                    plt.close(fig_dossier)

            # =========================================================================
            # FINAL PAGES: Detection Manifest & Compliance Log Table
            # Strictly bounded, auto-paginated (up to 14 rows per page), guaranteed zero cutoff
            # =========================================================================
            rows_per_page = 14
            manifest_pages = max(1, math.ceil(len(detections) / rows_per_page))
            headers = ["ID", "Latitude", "Longitude", "Vessel Name / MMSI", "Est Dimensions", "Speed / COG", "AIS Status", "SOLAS Flag"]

            for m_page in range(manifest_pages):
                fig_tbl = plt.figure(figsize=(11.0, 8.5), dpi=150)
                fig_tbl.patch.set_facecolor("#f8f9fa")

                t_header = fig_tbl.add_axes([0.05, 0.91, 0.90, 0.07])
                t_header.axis("off")
                t_header.text(
                    0.0, 0.60,
                    f"DETECTION MANIFEST & COMPLIANCE LOG (Page {m_page + 1} of {manifest_pages})",
                    fontsize=14,
                    fontweight="bold",
                    color="#0f2b48",
                )
                t_header.text(
                    0.0, 0.10,
                    f"Complete tactical catalog of contacts {m_page * rows_per_page + 1}–{min((m_page + 1) * rows_per_page, len(detections))} of {len(detections)}.",
                    fontsize=8.5,
                    color="#718096",
                )
                t_header.text(
                    1.0, 0.60,
                    "OFFICIAL RECORD",
                    fontsize=10,
                    fontweight="bold",
                    color="#2b6cb0",
                    ha="right",
                )

                page_dets = detections[m_page * rows_per_page : (m_page + 1) * rows_per_page]
                table_data: list[list[str]] = []

                for i, d in enumerate(page_dets):
                    is_dark = d.get("is_dark", True)
                    is_solas = d.get("is_solas_suspect", False)
                    length_m = float(d.get("length_m", 0) or 0)
                    width_m = float(d.get("width_m", 0) or 0)

                    v_name = d.get("vessel_name")
                    mmsi_val = d.get("mmsi")
                    if v_name:
                        ident = v_name[:20]
                    elif mmsi_val:
                        ident = f"MMSI {mmsi_val}"
                    elif is_dark:
                        ident = "NON-REPORTING"
                    else:
                        ident = d.get("vessel_type") or "Commercial"

                    ais = d.get("raw_detection", {}).get("correlated_ais") or d.get("correlated_ais") or {}
                    sog = float(ais.get("speed") or 0.0)
                    cog = float(ais.get("heading") or 0.0)
                    sog_cog_str = f"{sog:.1f}kn / {cog:.0f}°" if (sog > 0 or cog > 0) else "N/A"

                    dim_str = f"{length_m:.0f}m x {width_m:.0f}m" if length_m > 0 else "-"

                    table_data.append([
                        f"#{d.get('id', m_page * rows_per_page + i + 1)}",
                        format_lat(d.get("latitude")),
                        format_lon(d.get("longitude")),
                        ident,
                        dim_str,
                        sog_cog_str,
                        "DARK TARGET" if is_dark else "CORRELATED",
                        "NON-COMPLIANT" if is_solas else "CLEAR",
                    ])

                if not table_data:
                    table_data = [["-", "-", "-", "NO TARGET CONTACTS LOGGED", "-", "-", "PENDING", "CLEAR"]]

                manifest_ax = fig_tbl.add_axes([0.05, 0.08, 0.90, 0.81])
                manifest_ax.axis("off")

                # Strictly bounded table layout that never clips off canvas
                tbl = manifest_ax.table(
                    cellText=table_data,
                    colLabels=headers,
                    bbox=[0.0, 0.04, 1.0, 0.90],
                    colWidths=[0.06, 0.13, 0.13, 0.22, 0.12, 0.11, 0.11, 0.12],
                    cellLoc="center",
                )
                tbl.auto_set_font_size(False)
                tbl.set_fontsize(8)

                for (r, c), cell in tbl.get_celld().items():
                    cell.set_edgecolor("#cbd5e0")
                    if r == 0:
                        cell.set_facecolor("#1a365d")
                        cell.get_text().set_color("white")
                        cell.get_text().set_fontweight("bold")
                    else:
                        status_col = table_data[r - 1][6]
                        solas_col = table_data[r - 1][7]
                        if solas_col == "NON-COMPLIANT":
                            cell.set_facecolor("#fff5f5" if c in (6, 7) else "white")
                            if c == 7:
                                cell.get_text().set_color("#c53030")
                                cell.get_text().set_fontweight("bold")
                        elif status_col == "DARK TARGET":
                            cell.set_facecolor("#fffaf0" if c == 6 else "white")
                            if c == 6:
                                cell.get_text().set_color("#dd6b20")
                        elif status_col == "CORRELATED":
                            cell.set_facecolor("#f0fff4" if c == 6 else "white")
                            if c == 6:
                                cell.get_text().set_color("#2f855a")

                manifest_ax.text(
                    0.0, 0.01,
                    "* IMO SOLAS Chapter V Regulation 19 mandates Automatic Identification System (AIS) carriage for all vessels >=300 gross tonnage (~45m length).",
                    fontsize=7,
                    fontstyle="italic",
                    color="#718096",
                )

                pdf.savefig(fig_tbl)
                plt.close(fig_tbl)

        logger.info("Generated comprehensive PDF intelligence brief for %s at %s", scan.folder_name, target_path)
        return target_path

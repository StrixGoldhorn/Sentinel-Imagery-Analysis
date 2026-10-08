"""Matplotlib and Pillow implementation of Maritime Intelligence Brief PDF generator."""

import logging
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


class MatplotlibIntelligenceBriefGenerator:
    """Generates multi-page, publication-grade maritime intelligence briefings as PDF files."""

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

        # Try loading scan imagery for backdrop & chips
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

        with PdfPages(target_path) as pdf:
            # ---------------- PAGE 1: Tactical Executive Summary & Map ----------------
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

            # Render SAR backdrop if available
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

            # Bounding Box Outline
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

            # Plot vessels
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

            # ---------------- PAGE 2: Radar Chips & Suspicious Targets ----------------
            fig2 = plt.figure(figsize=(11.0, 8.5), dpi=150)
            fig2.patch.set_facecolor("#f8f9fa")

            p2_header = fig2.add_axes([0.05, 0.91, 0.90, 0.07])
            p2_header.axis("off")
            p2_header.text(
                0.0, 0.60,
                "HIGH-RISK TARGET DOSSIER // SAR SENSOR CHIPS",
                fontsize=15,
                fontweight="bold",
                color="#0f2b48",
            )
            p2_header.text(
                0.0, 0.10,
                "Sub-crops of uncooperative / high-confidence radar contacts and SOLAS non-compliance review.",
                fontsize=9,
                color="#718096",
            )

            # Extract up to 4 top suspect targets
            # Priority: SOLAS suspect dark vessels first, then other dark vessels, then largest AIS ships
            top_suspects = sorted(
                detections,
                key=lambda d: (
                    2 if d.get("is_solas_suspect") else (1 if d.get("is_dark") else 0),
                    float(d.get("length_m", 0) or 0),
                    float(d.get("confidence", 0) or 0),
                ),
                reverse=True,
            )[:4]

            chip_w = 0.20
            chip_h = 0.28
            gap = 0.04
            start_x = 0.05

            if total_vessels == 0:
                empty_chip_ax = fig2.add_axes([0.05, 0.58, 0.90, 0.28])
                empty_chip_ax.axis("off")
                empty_chip_ax.text(
                    0.5, 0.5,
                    "NO RADAR TARGETS LOGGED FOR SENSOR CHIP DOSSIER\n\n"
                    "Vessel detection has not been executed on this scan imagery.\n"
                    "Run automated ship detection from the scan dashboard to extract high-resolution radar chips.",
                    ha="center", va="center",
                    fontsize=10, color="#718096", fontweight="bold",
                    bbox=dict(boxstyle="round,pad=1.0", facecolor="white", edgecolor="#cbd5e0", linewidth=1.2),
                )
            else:
                for idx, target in enumerate(top_suspects):
                    ax_x = start_x + idx * (chip_w + gap)
                    chip_ax = fig2.add_axes([ax_x, 0.58, chip_w, chip_h])

                    px = int(target.get("pixel_x") or target.get("center_x") or 0)
                    py = int(target.get("pixel_y") or target.get("center_y") or 0)

                    chip_drawn = False
                    if sar_full is not None and sar_full.size > 0:
                        h, w = sar_full.shape
                        target_len = float(target.get("length_m", 0) or 40.0)
                        radius = max(28, min(int(target_len / 10.0 * 6), 64))
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
                            cross_color = "#e53e3e" if target.get("is_dark") else "#3182ce"
                            chip_ax.axhline(cy, color=cross_color, linestyle=":", alpha=0.5, linewidth=0.8)
                            chip_ax.axvline(cx, color=cross_color, linestyle=":", alpha=0.5, linewidth=0.8)
                            chip_drawn = True

                    if not chip_drawn:
                        synth = np.zeros((64, 64), dtype=np.uint8)
                        synth[28:36, 26:38] = 220
                        chip_ax.imshow(synth, cmap="gray")

                    is_dark = target.get("is_dark", True)
                    length_val = float(target.get("length_m", 0) or 0)
                    width_val = float(target.get("width_m", 0) or 0)
                    is_solas = target.get("is_solas_suspect", False)

                    if is_solas:
                        status_label = "SOLAS NON-COMPLIANT"
                        badge_color = "#c53030"
                    elif is_dark:
                        status_label = "DARK VESSEL"
                        badge_color = "#dd6b20"
                    else:
                        status_label = "AIS CORRELATED"
                        badge_color = "#2b6cb0"

                    chip_ax.set_xticks([])
                    chip_ax.set_yticks([])
                    chip_ax.set_title(
                        f"Target #{target.get('id', idx + 1)}\n{status_label}",
                        fontsize=8,
                        fontweight="bold",
                        color=badge_color,
                    )

                    # Info banner below chip
                    v_ident = target.get("vessel_name") or (
                        f"MMSI {target['mmsi']}" if target.get("mmsi") else (
                            target.get("vessel_type") or "Contact"
                        )
                    )
                    if len(v_ident) > 20:
                        v_ident = v_ident[:18] + ".."

                    lat_str = format_lat(target.get("latitude"))
                    lon_str = format_lon(target.get("longitude"))
                    conf_str = f"{float(target.get('confidence', 0) or 0)*100:.0f}%"

                    dim_str = f"L: {length_val:.0f}m, B: {width_val:.0f}m" if length_val > 0 else "Dim: Unk"

                    chip_ax.text(
                        0.5, -0.12,
                        f"{v_ident}\n{dim_str} | Conf: {conf_str}\n{lat_str}, {lon_str}",
                        transform=chip_ax.transAxes,
                        fontsize=7,
                        ha="center",
                        va="top",
                        color="#2d3748",
                    )

            # Detection Manifest Table on bottom half of Page 2
            manifest_ax = fig2.add_axes([0.05, 0.08, 0.90, 0.42])
            manifest_ax.axis("off")
            manifest_ax.text(
                0.0, 0.98,
                "DETECTION MANIFEST & COMPLIANCE LOG",
                fontsize=11,
                fontweight="bold",
                color="#1a202c",
            )

            headers = ["ID", "Latitude", "Longitude", "Vessel Name / MMSI", "Est Length", "Est Width", "AIS Status", "SOLAS Flag"]
            table_data: list[list[str]] = []

            for i, d in enumerate(detections[:12]):
                is_dark = d.get("is_dark", True)
                is_solas = d.get("is_solas_suspect", False)
                length_m = float(d.get("length_m", 0) or 0)
                width_m = float(d.get("width_m", 0) or 0)

                v_name = d.get("vessel_name")
                mmsi_val = d.get("mmsi")
                if v_name:
                    ident = v_name[:18]
                elif mmsi_val:
                    ident = f"MMSI {mmsi_val}"
                elif is_dark:
                    ident = "NON-REPORTING"
                else:
                    ident = d.get("vessel_type") or "Commercial"

                table_data.append([
                    f"#{d.get('id', i + 1)}",
                    format_lat(d.get("latitude")),
                    format_lon(d.get("longitude")),
                    ident,
                    f"{length_m:.1f} m" if length_m > 0 else "-",
                    f"{width_m:.1f} m" if width_m > 0 else "-",
                    "DARK" if is_dark else "AIS LINKED",
                    "NON-COMPLIANT" if is_solas else "CLEAR",
                ])

            if not table_data:
                table_data = [["-", "-", "-", "NO TARGET CONTACTS LOGGED", "-", "-", "DETECTION PENDING", "CLEAR"]]

            tbl = manifest_ax.table(
                cellText=table_data,
                colLabels=headers,
                loc="bottom",
                cellLoc="center",
            )
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1.0, 1.25)

            # Color headers and rows
            for (r, c), cell in tbl.get_celld().items():
                cell.set_edgecolor("#cbd5e0")
                if r == 0:
                    cell.set_facecolor("#2b6cb0")
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
                    elif status_col == "DARK":
                        cell.set_facecolor("#fffaf0" if c == 6 else "white")
                        if c == 6:
                            cell.get_text().set_color("#dd6b20")
                    elif status_col == "AIS LINKED":
                        cell.set_facecolor("#f0fff4" if c == 6 else "white")
                        if c == 6:
                            cell.get_text().set_color("#2f855a")

            if total_vessels > 12:
                manifest_ax.text(
                    0.0, -0.04,
                    f"* Displaying 12 of {total_vessels} total contacts. Complete tactical dataset available in GeoJSON and CSV exports.",
                    fontsize=7.5,
                    fontstyle="italic",
                    color="#718096",
                )

            pdf.savefig(fig2)
            plt.close(fig2)

        logger.info("Generated PDF intelligence brief for %s at %s", scan.folder_name, target_path)
        return target_path


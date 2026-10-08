"""Matplotlib and Pillow implementation of Maritime Intelligence Brief PDF generator."""

from datetime import datetime, timezone
import io
import logging
from pathlib import Path
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from sentinel_analysis.domain.entities import Scan

logger = logging.getLogger(__name__)


class MatplotlibIntelligenceBriefGenerator:
    """Generates multi-page, publication-grade maritime intelligence briefings as PDF files."""

    def generate_brief(self, scan: Scan, output_path: Path) -> Path:
        target_path = Path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        metadata = scan.metadata or {}
        detections = [d for d in metadata.get("detections", []) if isinstance(d, dict)]
        bbox = scan.bbox
        acq = scan.acquisition

        total_vessels = len(detections)
        dark_vessels = [d for d in detections if d.get("is_dark", True)]
        ais_vessels = [d for d in detections if not d.get("is_dark", True)]
        dark_count = len(dark_vessels)
        ais_count = len(ais_vessels)
        dark_ratio = (dark_count / total_vessels * 100.0) if total_vessels > 0 else 0.0

        # IMO SOLAS Chapter V Regulation 19 violations: length >= 45m without AIS
        solas_suspects = [
            d for d in dark_vessels
            if float(d.get("length_m", 0) or 0) >= 45.0
        ]

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

            # Map Ax: Spatial Scatter & Bounding Area
            map_ax = fig1.add_axes([0.06, 0.12, 0.55, 0.60])
            map_ax.set_facecolor("#edf2f7")
            map_ax.grid(True, linestyle="--", alpha=0.5, color="#cbd5e0")
            map_ax.set_xlim(bbox.min_longitude - 0.02, bbox.max_longitude + 0.02)
            map_ax.set_ylim(bbox.min_latitude - 0.02, bbox.max_latitude + 0.02)
            map_ax.set_xlabel("Longitude (°E)", fontsize=9, fontweight="bold")
            map_ax.set_ylabel("Latitude (°N)", fontsize=9, fontweight="bold")
            map_ax.set_title("Tactical Maritime Plot (EPSG:4326)", fontsize=11, fontweight="bold", color="#1a202c")

            # Bounding Box Outline
            aoi_rect = patches.Rectangle(
                (bbox.min_longitude, bbox.min_latitude),
                bbox.max_longitude - bbox.min_longitude,
                bbox.max_latitude - bbox.min_latitude,
                linewidth=1.2,
                edgecolor="#3182ce",
                facecolor="#ebf8ff",
                alpha=0.3,
                linestyle="--",
            )
            map_ax.add_patch(aoi_rect)

            # Plot vessels
            dark_lons = [d["longitude"] for d in dark_vessels if "longitude" in d]
            dark_lats = [d["latitude"] for d in dark_vessels if "latitude" in d]
            if dark_lons:
                map_ax.scatter(dark_lons, dark_lats, c="#e53e3e", marker="^", s=65, label=f"Dark Vessel ({len(dark_lons)})", zorder=4)

            ais_lons = [d["longitude"] for d in ais_vessels if "longitude" in d]
            ais_lats = [d["latitude"] for d in ais_vessels if "latitude" in d]
            if ais_lons:
                map_ax.scatter(ais_lons, ais_lats, c="#3182ce", marker="o", s=45, label=f"AIS Correlated ({len(ais_lons)})", zorder=3)

            map_ax.legend(loc="upper right", fontsize=8, framealpha=0.9)

            # Size distribution chart
            chart_ax = fig1.add_axes([0.67, 0.45, 0.28, 0.27])
            lengths = [float(d.get("length_m", 0) or 0) for d in detections if float(d.get("length_m", 0) or 0) > 0]
            if lengths:
                chart_ax.hist(lengths, bins=min(len(lengths), 8), color="#4299e1", edgecolor="#2b6cb0", alpha=0.85)
            chart_ax.set_title("Vessel Length Distribution", fontsize=10, fontweight="bold")
            chart_ax.set_xlabel("Length (meters)", fontsize=8)
            chart_ax.set_ylabel("Count", fontsize=8)
            chart_ax.tick_params(labelsize=8)
            chart_ax.grid(True, linestyle=":", alpha=0.5)

            # Metadata Table on Page 1
            table_ax = fig1.add_axes([0.67, 0.12, 0.28, 0.28])
            table_ax.axis("off")
            aoi_display = str(metadata.get("aoi_name") or "Area of Interest")
            meta_rows = [
                ("AOI Name", aoi_display[:20]),
                ("West Longitude", f"{bbox.min_longitude:.4f}°"),
                ("East Longitude", f"{bbox.max_longitude:.4f}°"),
                ("South Latitude", f"{bbox.min_latitude:.4f}°"),
                ("North Latitude", f"{bbox.max_latitude:.4f}°"),
                ("Orbit Direction", str(acq.orbit_direction or "N/A")),
                ("Relative Orbit", str(acq.relative_orbit or "N/A")),
            ]
            t = table_ax.table(
                cellText=meta_rows,
                colWidths=[0.55, 0.45],
                loc="center",
                cellLoc="left",
            )
            t.auto_set_font_size(False)
            t.set_fontsize(8)
            t.scale(1.0, 1.4)
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

            # Load full image for chip extraction if available
            sar_img = None
            image_path = Path(scan.image_path)
            if image_path.is_file():
                try:
                    with Image.open(image_path) as im:
                        sar_img = np.array(im.convert("L"))
                except Exception as exc:
                    logger.warning("Could not open scan image for chips: %s", exc)

            # Extract up to 4 top suspect targets
            top_suspects = sorted(
                detections,
                key=lambda d: (
                    1 if d.get("is_dark", True) else 0,
                    float(d.get("length_m", 0) or 0),
                    float(d.get("confidence", 0) or 0),
                ),
                reverse=True,
            )[:4]

            chip_w = 0.20
            chip_h = 0.28
            gap = 0.04
            start_x = 0.05

            for idx, target in enumerate(top_suspects):
                ax_x = start_x + idx * (chip_w + gap)
                chip_ax = fig2.add_axes([ax_x, 0.58, chip_w, chip_h])

                px = int(target.get("pixel_x") or target.get("center_x") or 0)
                py = int(target.get("pixel_y") or target.get("center_y") or 0)

                chip_drawn = False
                if sar_img is not None and sar_img.size > 0:
                    h, w = sar_img.shape
                    radius = 32
                    x1 = max(0, px - radius)
                    x2 = min(w, px + radius)
                    y1 = max(0, py - radius)
                    y2 = min(h, py + radius)
                    if x2 > x1 and y2 > y1:
                        crop = sar_img[y1:y2, x1:x2]
                        chip_ax.imshow(crop, cmap="gray", origin="upper")
                        chip_drawn = True

                if not chip_drawn:
                    # Synthetic radar chip fallback
                    synth = np.zeros((64, 64), dtype=np.uint8)
                    synth[28:36, 26:38] = 220
                    chip_ax.imshow(synth, cmap="gray")

                is_dark = target.get("is_dark", True)
                length_val = float(target.get("length_m", 0) or 0)
                is_solas = is_dark and (length_val >= 45.0)

                status_label = "SOLAS NON-COMPLIANT" if is_solas else ("DARK VESSEL" if is_dark else "AIS LINKED")
                badge_color = "#c53030" if (is_solas or is_dark) else "#2b6cb0"

                chip_ax.set_xticks([])
                chip_ax.set_yticks([])
                chip_ax.set_title(
                    f"Target #{idx + 1}\n{status_label}",
                    fontsize=8,
                    fontweight="bold",
                    color=badge_color,
                )

                # Info banner below chip
                lat_str = f"{target.get('latitude', 0.0):.4f}°N" if target.get("latitude") else "N/A"
                lon_str = f"{target.get('longitude', 0.0):.4f}°E" if target.get("longitude") else "N/A"
                conf_str = f"{float(target.get('confidence', 0) or 0)*100:.0f}%"

                chip_ax.text(
                    0.5, -0.15,
                    f"L: {length_val:.0f}m | Conf: {conf_str}\n{lat_str}, {lon_str}",
                    transform=chip_ax.transAxes,
                    fontsize=7,
                    ha="center",
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

            headers = ["ID", "Latitude", "Longitude", "Est Length", "Est Width", "Confidence", "AIS Status", "SOLAS Flag"]
            table_data: list[list[str]] = []

            for i, d in enumerate(detections[:12]):  # Display up to first 12 detections
                is_dark = d.get("is_dark", True)
                length_m = float(d.get("length_m", 0) or 0)
                width_m = float(d.get("width_m", 0) or 0)
                conf = float(d.get("confidence", 0) or 0)
                is_solas = is_dark and (length_m >= 45.0)

                table_data.append([
                    f"#{i + 1}",
                    f"{d.get('latitude', 0.0):.4f}°",
                    f"{d.get('longitude', 0.0):.4f}°",
                    f"{length_m:.1f} m" if length_m > 0 else "-",
                    f"{width_m:.1f} m" if width_m > 0 else "-",
                    f"{conf * 100:.0f}%",
                    "DARK" if is_dark else "AIS LINKED",
                    "NON-COMPLIANT" if is_solas else "CLEAR",
                ])

            if not table_data:
                table_data = [["-", "-", "-", "-", "-", "-", "NO TARGETS", "CLEAR"]]

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
                    if status_col == "DARK":
                        cell.set_facecolor("#fff5f5" if c in (6, 7) else "white")
                    else:
                        cell.set_facecolor("#f0fff4" if c == 6 else "white")

            pdf.savefig(fig2)
            plt.close(fig2)

        logger.info("Generated PDF intelligence brief for %s at %s", scan.folder_name, target_path)
        return target_path

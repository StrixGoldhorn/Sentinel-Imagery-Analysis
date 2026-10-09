"""Use cases for exporting scan data to standard geospatial formats (GeoTIFF, STAC, GeoJSON)."""

from datetime import timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional

from sentinel_analysis.application.ports.imagery import GeoTIFFWriter
from sentinel_analysis.application.ports.scan_repository import ScanRepository
from sentinel_analysis.domain.entities import Scan

logger = logging.getLogger(__name__)


class ExportGeospatial:
    """Orchestrates standard geospatial exports for radar scans."""

    def __init__(
        self,
        scan_repository: ScanRepository,
        geotiff_writer: Optional[GeoTIFFWriter] = None,
    ) -> None:
        self._scan_repository = scan_repository
        self._geotiff_writer = geotiff_writer

    def export_geotiff(self, folder_name: str, target_path: Optional[Path] = None) -> Path:
        """Export a SAR scan as a georeferenced GeoTIFF with EPSG:4326 geotransform tags."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")
        image_path = Path(scan.image_path)
        if not image_path.is_file():
            raise FileNotFoundError(f"Scan image not found: {image_path}")

        if target_path is None:
            target_path = image_path.parent / f"{scan.folder_name}.tif"

        if self._geotiff_writer is None:
            raise RuntimeError("No GeoTIFFWriter configured in ExportGeospatial")

        return self._geotiff_writer.write_geotiff(image_path, target_path, scan.bbox)


    def export_stac_item(self, folder_name: str, base_url: str = "") -> dict[str, Any]:
        """Generate a STAC Item v1.0.0 specification compliant metadata dictionary."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")
        bbox = scan.bbox
        acq = scan.acquisition
        metadata = scan.metadata or {}

        acq_time = getattr(acq, "acquired_at", None) or getattr(acq, "begin_position", None)
        if acq_time:
            if acq_time.tzinfo is None:
                acq_time = acq_time.replace(tzinfo=timezone.utc)
            dt_str = acq_time.isoformat()
        else:
            from datetime import datetime
            dt_str = datetime.now(timezone.utc).isoformat()

        end_time = getattr(acq, "end_position", None)
        end_str = end_time.isoformat() if end_time else dt_str

        platform_raw = (getattr(acq, "satellite", None) or getattr(acq, "platform", "sentinel-1")).lower()
        constellation = "sentinel-1" if "sentinel" in platform_raw else ("umbra" if "umbra" in platform_raw else "sar")

        coordinates = [[
            [bbox.min_longitude, bbox.min_latitude],
            [bbox.max_longitude, bbox.min_latitude],
            [bbox.max_longitude, bbox.max_latitude],
            [bbox.min_longitude, bbox.max_latitude],
            [bbox.min_longitude, bbox.min_latitude],
        ]]

        clean_base = base_url.rstrip("/")

        stac_item: dict[str, Any] = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/sar/v1.0.0/schema.json",
                "https://stac-extensions.github.io/sat/v1.0.0/schema.json",
            ],
            "id": scan.folder_name,
            "geometry": {
                "type": "Polygon",
                "coordinates": coordinates,
            },
            "bbox": [
                bbox.min_longitude,
                bbox.min_latitude,
                bbox.max_longitude,
                bbox.max_latitude,
            ],
            "properties": {
                "datetime": dt_str,
                "start_datetime": dt_str,
                "end_datetime": end_str,
                "platform": platform_raw,
                "constellation": constellation,
                "sar:instrument_mode": getattr(acq, "sensor_mode", None) or "IW",
                "sar:frequency_band": "C" if "sentinel" in platform_raw else "X",
                "sar:polarizations": list(acq.polarizations) if acq.polarizations else ["VV"],
                "sar:product_type": "GRD",
                "sat:orbit_state": (acq.orbit_direction or "descending").lower(),
                "sat:relative_orbit": acq.relative_orbit or 0,
                "title": f"SAR Scan {scan.folder_name}",
                "description": f"Synthetic Aperture Radar capture over {metadata.get('aoi_name', 'AOI')}",
                "created": dt_str,
                "updated": dt_str,
            },
            "assets": {
                "thumbnail": {
                    "href": f"{clean_base}/static/output/{scan.folder_name}/images/{Path(scan.image_path).name}",
                    "type": "image/png",
                    "title": "SAR Overview Image",
                    "roles": ["thumbnail", "overview"],
                },
                "geotiff": {
                    "href": f"{clean_base}/api/scan/{scan.folder_name}/export/geotiff",
                    "type": "image/tiff; application=geotiff",
                    "title": "Georeferenced Cloud-Optimized GeoTIFF",
                    "roles": ["data"],
                },
                "detections": {
                    "href": f"{clean_base}/api/scan/{scan.folder_name}/export/geojson",
                    "type": "application/geo+json",
                    "title": "Vessel Detections GeoJSON",
                    "roles": ["metadata", "annotations"],
                },
            },
            "links": [
                {
                    "rel": "self",
                    "href": f"{clean_base}/api/scan/{scan.folder_name}/export/stac",
                    "type": "application/json",
                },
                {
                    "rel": "root",
                    "href": f"{clean_base}/api/scans",
                    "type": "application/json",
                },
            ],
        }

        return stac_item

    def export_geojson(self, folder_name: str) -> dict[str, Any]:
        """Generate GeoJSON FeatureCollection of all detected vessels and OBBs."""
        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")
        image_path = Path(scan.image_path)
        folder_dir = image_path.parent

        # Check existing geojson files first
        candidates = [
            folder_dir / f"{image_path.stem}_detections.geojson",
            folder_dir / "detections.geojson",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    with open(c, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                            return data
                except Exception:
                    pass

        # Synthesize from scan metadata detections
        metadata = scan.metadata or {}
        raw_detections = metadata.get("detections", [])
        features: list[dict[str, Any]] = []

        bbox = scan.bbox
        for idx, det in enumerate(raw_detections):
            if not isinstance(det, dict):
                continue

            lat = det.get("latitude")
            lon = det.get("longitude")
            if lat is None or lon is None:
                # Estimate from pixel coordinates if lat/lon not present
                px = det.get("pixel_x") or det.get("center_x") or 0.0
                py = det.get("pixel_y") or det.get("center_y") or 0.0
                # Fallback to bbox center if missing
                lon = bbox.min_longitude + (bbox.max_longitude - bbox.min_longitude) * 0.5
                lat = bbox.min_latitude + (bbox.max_latitude - bbox.min_latitude) * 0.5

            props = dict(det)
            props["feature_id"] = idx + 1
            props["scan_folder"] = scan.folder_name

            # Check if polygon coordinates exist for OBB
            polygon_coords = det.get("polygon_geo") or det.get("obb_coordinates")
            if polygon_coords and isinstance(polygon_coords, list) and len(polygon_coords) >= 4:
                geometry = {
                    "type": "Polygon",
                    "coordinates": [polygon_coords],
                }
            else:
                geometry = {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)],
                }

            features.append({
                "type": "Feature",
                "id": idx + 1,
                "geometry": geometry,
                "properties": props,
            })

        return {
            "type": "FeatureCollection",
            "crs": {
                "type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
            },
            "features": features,
        }

    def export_kmz(self, folder_name: str, target_path: Optional[Path] = None) -> Path:
        """Export vessel detections and scan footprint as a compressed Keyhole Markup Language (.kmz) archive."""
        import html
        import zipfile
        from xml.sax.saxutils import escape as xml_escape

        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")
        image_path = Path(scan.image_path)
        if target_path is None:
            target_path = image_path.parent / f"{scan.folder_name}.kmz"

        fc = self.export_geojson(folder_name)
        features = fc.get("features", [])
        bbox = scan.bbox

        kml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<kml xmlns="http://www.opengis.net/kml/2.2">',
            '  <Document>',
            f'    <name>{xml_escape(scan.folder_name)} SAR Vessel Detections</name>',
            '    <description>Satellite SAR Vessel Telemetry, SOLAS Compliance, and Kinematic Correlations</description>',
            '    <Style id="darkVesselStyle">',
            '      <IconStyle>',
            '        <color>ff0000ff</color>',
            '        <scale>1.2</scale>',
            '        <Icon><href>http://maps.google.com/mapfiles/kml/shapes/target.png</href></Icon>',
            '      </IconStyle>',
            '    </Style>',
            '    <Style id="suspectVesselStyle">',
            '      <IconStyle>',
            '        <color>ff00a5ff</color>',
            '        <scale>1.2</scale>',
            '        <Icon><href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href></Icon>',
            '      </IconStyle>',
            '    </Style>',
            '    <Style id="compliantVesselStyle">',
            '      <IconStyle>',
            '        <color>ff00cc00</color>',
            '        <scale>1.1</scale>',
            '        <Icon><href>http://maps.google.com/mapfiles/kml/shapes/marina.png</href></Icon>',
            '      </IconStyle>',
            '    </Style>',
            '    <Style id="persistentStructureStyle">',
            '      <IconStyle>',
            '        <color>ffff0088</color>',
            '        <scale>1.1</scale>',
            '        <Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon>',
            '      </IconStyle>',
            '    </Style>',
            '    <Style id="scanBboxStyle">',
            '      <LineStyle><color>ff38bdf8</color><width>2</width></LineStyle>',
            '      <PolyStyle><color>2238bdf8</color></PolyStyle>',
            '    </Style>',
        ]

        # Add SAR swath footprint polygon
        if bbox:
            kml_lines.extend([
                '    <Placemark>',
                f'      <name>SAR Swath Footprint: {xml_escape(scan.folder_name)}</name>',
                '      <styleUrl>#scanBboxStyle</styleUrl>',
                '      <Polygon>',
                '        <outerBoundaryIs>',
                '          <LinearRing>',
                '            <coordinates>',
                f'              {bbox.min_longitude},{bbox.min_latitude},0 ',
                f'              {bbox.max_longitude},{bbox.min_latitude},0 ',
                f'              {bbox.max_longitude},{bbox.max_latitude},0 ',
                f'              {bbox.min_longitude},{bbox.max_latitude},0 ',
                f'              {bbox.min_longitude},{bbox.min_latitude},0',
                '            </coordinates>',
                '          </LinearRing>',
                '        </outerBoundaryIs>',
                '      </Polygon>',
                '    </Placemark>',
            ])

        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [])

            # Get lon/lat
            lon, lat = 0.0, 0.0
            if geom.get("type") == "Point" and len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
            elif geom.get("type") == "Polygon" and coords and len(coords[0]) > 0:
                pts = coords[0]
                lon = sum(p[0] for p in pts) / len(pts)
                lat = sum(p[1] for p in pts) / len(pts)
            else:
                lat = float(props.get("latitude") or 0.0)
                lon = float(props.get("longitude") or 0.0)

            fid = props.get("feature_id") or feat.get("id") or 1
            is_dark = bool(props.get("is_dark"))
            is_solas = bool(props.get("is_solas_suspect"))
            is_spoofed = bool(props.get("is_speed_spoofed") or props.get("is_course_spoofed"))
            in_mpa = bool(props.get("in_protected_area") or props.get("geofence_breaches"))
            is_sts = bool(props.get("transshipment_events"))
            temp_change = str(props.get("temporal_change_type") or "NEW_VESSEL")
            opt_status = str(props.get("optical_status") or "NO_CORRELATED_OPTICAL")

            # Determine Placemark style
            if temp_change == "PERSISTENT_STRUCTURE":
                style_url = "#persistentStructureStyle"
                status_desc = "Persistent Radar Scatterer (Structure/Reef)"
            elif is_solas or is_spoofed or in_mpa or is_sts:
                style_url = "#suspectVesselStyle"
                status_desc = "Suspect Target / Security Anomaly"
            elif is_dark:
                style_url = "#darkVesselStyle"
                status_desc = "Dark Vessel (Non-cooperative SAR contact)"
            else:
                style_url = "#compliantVesselStyle"
                status_desc = "Cooperative AIS Vessel"

            name_val = props.get("vessel_name") or props.get("name") or (f"Dark Vessel #{fid}" if is_dark else f"Target #{fid}")
            mmsi_val = str(props.get("mmsi") or "N/A")
            length_val = props.get("length_m") or props.get("length") or "N/A"
            beam_val = props.get("width_m") or props.get("beam") or "N/A"
            conf_val = f"{float(props.get('confidence', 0.85)) * 100:.0f}%" if props.get("confidence") is not None else "N/A"

            balloon_html = (
                f'<table border="1" cellpadding="4" cellspacing="0" style="font-family:sans-serif;font-size:12px;border-collapse:collapse;">'
                f'<tr bgcolor="#f1f5f9"><th colspan="2">{html.escape(str(name_val))}</th></tr>'
                f'<tr><td><b>Status</b></td><td>{html.escape(status_desc)}</td></tr>'
                f'<tr><td><b>MMSI</b></td><td>{html.escape(mmsi_val)}</td></tr>'
                f'<tr><td><b>Dimensions</b></td><td>{length_val}m × {beam_val}m</td></tr>'
                f'<tr><td><b>Confidence</b></td><td>{conf_val}</td></tr>'
                f'<tr><td><b>SOLAS Compliance</b></td><td>{"NON-COMPLIANT" if is_solas else "COMPLIANT"}</td></tr>'
                f'<tr><td><b>Optical Status</b></td><td>{html.escape(opt_status)}</td></tr>'
                f'<tr><td><b>Temporal Change</b></td><td>{html.escape(temp_change)}</td></tr>'
                f'<tr><td><b>Coordinates</b></td><td>{lat:.5f}° N, {lon:.5f}° E</td></tr>'
                f'</table>'
            )

            kml_lines.extend([
                '    <Placemark>',
                f'      <name>{xml_escape(str(name_val))}</name>',
                f'      <styleUrl>{style_url}</styleUrl>',
                f'      <description><![CDATA[{balloon_html}]]></description>',
                '      <Point>',
                f'        <coordinates>{lon},{lat},0</coordinates>',
                '      </Point>',
                '    </Placemark>',
            ])

        kml_lines.extend([
            '  </Document>',
            '</kml>',
        ])

        kml_content = "\n".join(kml_lines)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as kmz:
            kmz.writestr("doc.kml", kml_content)

        return target_path

    def export_cursor_on_target(self, folder_name: str) -> str:
        """Generate MIL-STD-2525 compatible Cursor-on-Target (CoT v2.0) XML event stream."""
        from xml.sax.saxutils import escape as xml_escape

        scan = self._scan_repository.get(folder_name)
        if scan is None:
            raise FileNotFoundError(f"Scan '{folder_name}' not found")

        acq = scan.acquisition
        acq_time = getattr(acq, "acquired_at", None) or getattr(acq, "begin_position", None)
        if acq_time:
            if acq_time.tzinfo is None:
                acq_time = acq_time.replace(tzinfo=timezone.utc)
            t_iso = acq_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            from datetime import timedelta
            stale_iso = (acq_time + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            from datetime import datetime, timedelta
            now = datetime.now(timezone.utc)
            t_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            stale_iso = (now + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")

        fc = self.export_geojson(folder_name)
        features = fc.get("features", [])

        cot_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<events version="2.0">',
        ]

        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [])

            lon, lat = 0.0, 0.0
            if geom.get("type") == "Point" and len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
            elif geom.get("type") == "Polygon" and coords and len(coords[0]) > 0:
                pts = coords[0]
                lon = sum(p[0] for p in pts) / len(pts)
                lat = sum(p[1] for p in pts) / len(pts)
            else:
                lat = float(props.get("latitude") or 0.0)
                lon = float(props.get("longitude") or 0.0)

            fid = props.get("feature_id") or feat.get("id") or 1
            is_dark = bool(props.get("is_dark"))
            is_solas = bool(props.get("is_solas_suspect"))
            is_spoofed = bool(props.get("is_speed_spoofed") or props.get("is_course_spoofed"))
            in_mpa = bool(props.get("in_protected_area") or props.get("geofence_breaches"))
            is_sts = bool(props.get("transshipment_events"))
            temp_change = str(props.get("temporal_change_type") or "NEW_VESSEL")
            opt_status = str(props.get("optical_status") or "NO_CORRELATED_OPTICAL")

            # CoT Type Mapping (MIL-STD-2525 atom syntax)
            # a = atom, -h-S = hostile/suspect surface, -u-S = unknown surface, -f-S = friendly surface
            if is_solas or is_spoofed or in_mpa or is_sts:
                cot_type = "a-h-S"
            elif is_dark:
                cot_type = "a-u-S"
            elif props.get("is_correlated"):
                cot_type = "a-f-S"
            else:
                cot_type = "a-u-S"

            mmsi = props.get("mmsi")
            uid = f"SAR-{scan.folder_name}-{mmsi}" if mmsi else f"SAR-{scan.folder_name}-TGT-{fid:03d}"
            callsign = str(props.get("callsign") or props.get("vessel_name") or props.get("name") or uid)
            speed = float(props.get("speed") or 0.0)
            course = float(props.get("heading") or props.get("angle") or 0.0)
            length_m = props.get("length_m") or props.get("length") or 0.0
            beam_m = props.get("width_m") or props.get("beam") or 0.0

            remarks_parts = [
                f"Type: {cot_type}",
                f"Len: {length_m}m",
                f"Beam: {beam_m}m",
                f"Optical: {opt_status}",
                f"Temporal: {temp_change}",
            ]
            if is_solas:
                remarks_parts.append("SOLAS VIOLATION")
            if is_spoofed:
                remarks_parts.append("SPOOFING SUSPECT")
            if in_mpa:
                remarks_parts.append("MPA BREACH")
            if is_sts:
                remarks_parts.append("STS RENDEZVOUS")

            remarks_str = xml_escape("; ".join(remarks_parts))

            cot_lines.extend([
                f'  <event version="2.0" uid="{xml_escape(uid)}" type="{cot_type}" time="{t_iso}" start="{t_iso}" stale="{stale_iso}" how="m-g">',
                f'    <point lat="{lat:.6f}" lon="{lon:.6f}" hae="0.0" ce="15.0" le="10.0"/>',
                '    <detail>',
                f'      <contact callsign="{xml_escape(callsign)}"/>',
                f'      <track speed="{speed:.1f}" course="{course:.1f}"/>',
                f'      <remarks>{remarks_str}</remarks>',
                '    </detail>',
                '  </event>',
            ])

        cot_lines.append('</events>')
        return "\n".join(cot_lines)

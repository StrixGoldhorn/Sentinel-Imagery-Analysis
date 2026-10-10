/**
 * Interactive Radar Chip Inspector with Multi-Palette Colormaps,
 * OBB/Wake/AIS Layer Overlays, 1D Cross-Sectional Transect Profiles,
 * and Comprehensive RF Backscatter Analytics.
 */

const inspectorState = {
    folderName: null,
    detectionData: null,
    cropData: null,
    image: null,
    zoom: 2.0,
    colorMap: 'grayscale', // 'grayscale', 'amber', 'green', 'inferno'
    showObb: true,
    showWake: true,
    showPeak: true,
    showAis: true,
};

/**
 * Open Inspector modal and fetch high-res radar chip with full analytics.
 */
async function inspectDetection(folderName, detectionData) {
    const modal = document.getElementById('cropInspectorModal');
    if (!modal) return;

    inspectorState.folderName = folderName;
    inspectorState.detectionData = detectionData;
    inspectorState.cropData = null;
    inspectorState.zoom = 2.0;

    // Reset UI state
    const loadingEl = document.getElementById('inspectorLoading');
    const chipCanvas = document.getElementById('inspectorChipCanvas');
    const hoverInfo = document.getElementById('inspectorHoverInfo');

    if (loadingEl) {
        loadingEl.style.display = 'flex';
        loadingEl.innerText = 'Acquiring high-resolution SAR radar chip...';
    }
    if (chipCanvas) chipCanvas.style.display = 'none';
    if (hoverInfo) hoverInfo.innerText = 'Hover over radar chip for pixel backscatter';

    modal.classList.add('active');

    // Populate initial detection fields
    setInspectorText('inspectorLength', detectionData.length ? `${detectionData.length} m` : 'N/A');
    setInspectorText('inspectorBeam', detectionData.beam ? `${detectionData.beam} m` : 'N/A');
    setInspectorText('inspectorHeading', detectionData.angle !== undefined ? `${detectionData.angle}°` : 'N/A');
    setInspectorText('inspectorConfidence', detectionData.confidence !== undefined ? `${(detectionData.confidence * 100).toFixed(1)}%` : 'N/A');
    setInspectorText('inspectorCoords', `X: ${Math.round(detectionData.x)}, Y: ${Math.round(detectionData.y)} (${Math.round(detectionData.width)}×${Math.round(detectionData.height)} px)`);

    // Calibrated Uncertainty & Reason Codes
    const spUnc = detectionData.spatial_uncertainty || (detectionData.raw_detection && detectionData.raw_detection.spatial_uncertainty) || {};
    const cepVal = spUnc.cep_meters !== undefined ? spUnc.cep_meters : detectionData.cep_meters;
    const semiMaj = spUnc.semi_major_axis_meters;
    const semiMin = spUnc.semi_minor_axis_meters;
    const orient = spUnc.orientation_deg;

    if (cepVal !== undefined && cepVal !== null) {
        setInspectorText('inspectorSpatialCep', `±${Number(cepVal).toFixed(1)} m`);
    } else {
        setInspectorText('inspectorSpatialCep', 'Uncalibrated');
    }

    if (semiMaj !== undefined && semiMin !== undefined) {
        const orientStr = orient !== undefined ? ` @ ${Number(orient).toFixed(0)}°` : '';
        setInspectorText('inspectorSpatialEllipse', `±${Number(semiMaj).toFixed(0)}m × ±${Number(semiMin).toFixed(0)}m${orientStr}`);
    } else {
        setInspectorText('inspectorSpatialEllipse', 'Circular / Uncalibrated');
    }

    const dimUnc = detectionData.dimension_uncertainty || (detectionData.raw_detection && detectionData.raw_detection.dimension_uncertainty) || {};
    const lenUnc = dimUnc.length_uncertainty_m;
    const beamUnc = dimUnc.beam_uncertainty_m;
    const hdgUnc = dimUnc.heading_uncertainty_deg;

    if (lenUnc !== undefined && beamUnc !== undefined) {
        setInspectorText('inspectorDimBounds', `L: ±${Number(lenUnc).toFixed(1)}m, B: ±${Number(beamUnc).toFixed(1)}m`);
    } else {
        setInspectorText('inspectorDimBounds', 'Default Bounds');
    }

    if (hdgUnc !== undefined && hdgUnc !== null) {
        setInspectorText('inspectorHeadingBounds', `±${Number(hdgUnc).toFixed(1)}°`);
    } else {
        setInspectorText('inspectorHeadingBounds', 'N/A');
    }

    // Association Likelihood Badge
    const assocEl = document.getElementById('inspectorAssocLikelihoodBadge');
    const assocLh = detectionData.association_likelihood !== undefined ? detectionData.association_likelihood :
                   (detectionData.raw_detection && detectionData.raw_detection.association_likelihood);
    if (assocEl) {
        if (assocLh !== undefined && assocLh !== null) {
            const pct = (Number(assocLh) * 100).toFixed(1);
            let bg = '#10b981';
            if (assocLh < 0.4) bg = '#ef4444';
            else if (assocLh < 0.7) bg = '#f59e0b';
            assocEl.style.background = bg;
            assocEl.style.color = '#ffffff';
            assocEl.innerText = `Assoc Likelihood: ${pct}%`;
        } else {
            assocEl.style.background = '#e2e8f0';
            assocEl.style.color = '#475569';
            assocEl.innerText = 'Assoc Likelihood: N/A';
        }
    }

    // Reason Codes
    const rcContainer = document.getElementById('inspectorReasonCodes');
    const rCodes = detectionData.reason_codes || (detectionData.raw_detection && detectionData.raw_detection.reason_codes) || [];
    if (rcContainer) {
        if (Array.isArray(rCodes) && rCodes.length > 0) {
            rcContainer.innerHTML = rCodes.map(code => {
                let bg = '#64748b';
                if (code.includes('AIS_') || code.includes('CONFIRMED')) bg = '#10b981';
                else if (code.includes('DARK_') || code.includes('SOLAS_') || code.includes('SPOOF')) bg = '#ef4444';
                else if (code.includes('RADAR_STRONG')) bg = '#0ea5e9';
                return `<span class="badge" style="background: ${bg}; color: #ffffff; font-size: 0.70rem; padding: 2px 6px; border-radius: 4px; font-weight: 500;">${escapeHtml(code)}</span>`;
            }).join('');
        } else {
            rcContainer.innerHTML = `<span style="color: #94a3b8; font-size: 0.74rem; font-style: italic;">No specific reason codes</span>`;
        }
    }

    try {
        const detIdx = detectionData.origIdx !== undefined ? detectionData.origIdx : detectionData.index;
        let url = `/api/scan/${encodeURIComponent(folderName)}/crop?`;

        if (detIdx !== undefined && detIdx !== null) {
            url += `detection_idx=${detIdx}&padding=35`;
        } else {
            const params = new URLSearchParams({
                x: Math.round(detectionData.x),
                y: Math.round(detectionData.y),
                width: Math.round(detectionData.width),
                height: Math.round(detectionData.height),
                padding: 35,
            });
            url += params.toString();
        }

        const res = await fetch(url);
        if (!res.ok) {
            throw new Error(`HTTP error ${res.status}`);
        }
        const data = await res.json();
        inspectorState.cropData = data;

        // Load image into memory
        const img = new Image();
        img.onload = () => {
            inspectorState.image = img;
            if (loadingEl) loadingEl.style.display = 'none';
            if (chipCanvas) chipCanvas.style.display = 'block';

            renderRadarChip();
            renderAnalyticsBadges(data);
            renderTransectProfiles(data.stats);
            renderHistogram(data.stats.histogram);
        };
        img.onerror = () => {
            if (loadingEl) loadingEl.innerText = 'Failed to decode radar chip image.';
        };
        img.src = data.data_uri;

    } catch (err) {
        console.error('Inspector crop error:', err);
        if (loadingEl) loadingEl.innerText = 'Error loading radar crop data.';
    }
}

/**
 * Render the SAR radar chip on canvas with zoom, colormap, and vector overlays.
 */
function renderRadarChip() {
    const canvas = document.getElementById('inspectorChipCanvas');
    if (!canvas || !inspectorState.image || !inspectorState.cropData) return;

    const img = inspectorState.image;
    const crop = inspectorState.cropData;
    const zoom = inspectorState.zoom;

    const rawW = img.width;
    const rawH = img.height;
    const displayW = Math.round(rawW * zoom);
    const displayH = Math.round(rawH * zoom);

    canvas.width = displayW;
    canvas.height = displayH;

    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false; // crisp radar pixels

    // Create offscreen canvas to apply colormap
    const offCanvas = document.createElement('canvas');
    offCanvas.width = rawW;
    offCanvas.height = rawH;
    const offCtx = offCanvas.getContext('2d');
    offCtx.drawImage(img, 0, 0);

    const imgData = offCtx.getImageData(0, 0, rawW, rawH);
    applyColorMapToImageData(imgData, inspectorState.colorMap);
    offCtx.putImageData(imgData, 0, 0);

    // Draw colormapped image onto main canvas scaled by zoom
    ctx.drawImage(offCanvas, 0, 0, displayW, displayH);

    // Draw Vector Overlays
    const scale = zoom;

    // 1. Detection Bounding Box
    if (crop.local_box) {
        const box = crop.local_box;
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.45)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.strokeRect(box.x * scale, box.y * scale, box.width * scale, box.height * scale);
        ctx.setLineDash([]);
    }

    // 2. Oriented Bounding Box (OBB)
    if (inspectorState.showObb && crop.local_obb && crop.local_obb.corners) {
        ctx.strokeStyle = '#38bdf8'; // Sky blue
        ctx.lineWidth = 2;
        ctx.beginPath();
        const corners = crop.local_obb.corners;
        ctx.moveTo(corners[0][0] * scale, corners[0][1] * scale);
        for (let i = 1; i < corners.length; i++) {
            ctx.lineTo(corners[i][0] * scale, corners[i][1] * scale);
        }
        ctx.closePath();
        ctx.stroke();

        // Longitudinal axis / heading pointer
        if (crop.local_center) {
            const cx = crop.local_center.x * scale;
            const cy = crop.local_center.y * scale;
            const angleRad = (crop.local_obb.angle || 0) * Math.PI / 180.0;
            const len = (crop.local_box.height || 30) * scale * 0.7;

            ctx.strokeStyle = '#0284c7';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(cx + Math.sin(angleRad) * len, cy - Math.cos(angleRad) * len);
            ctx.stroke();
        }
    }

    // 3. Wake Vector & Kelvin Divergence Arms
    if (inspectorState.showWake && crop.local_wake) {
        const wake = crop.local_wake;
        const sx = wake.start[0] * scale;
        const sy = wake.start[1] * scale;
        const ex = wake.end[0] * scale;
        const ey = wake.end[1] * scale;

        // Wake track line
        ctx.strokeStyle = '#f59e0b'; // Amber
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(ex, ey);
        ctx.stroke();
        ctx.setLineDash([]);

        // Kelvin wake envelope arms (approx 19.47 deg divergence)
        const wakeAngleRad = (wake.wake_angle || 0) * Math.PI / 180.0;
        const kelvinRad = 19.47 * Math.PI / 180.0;
        const wakeLen = Math.hypot(ex - sx, ey - sy);

        ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(sx + Math.sin(wakeAngleRad + kelvinRad) * wakeLen, sy - Math.cos(wakeAngleRad + kelvinRad) * wakeLen);
        ctx.moveTo(sx, sy);
        ctx.lineTo(sx + Math.sin(wakeAngleRad - kelvinRad) * wakeLen, sy - Math.cos(wakeAngleRad - kelvinRad) * wakeLen);
        ctx.stroke();
    }

    // 4. Peak Radar Scatterer Crosshair
    if (inspectorState.showPeak && crop.local_peak) {
        const px = crop.local_peak.x * scale;
        const py = crop.local_peak.y * scale;

        ctx.strokeStyle = '#ef4444'; // Red crosshair
        ctx.lineWidth = 1.5;
        const chSize = 7;

        ctx.beginPath();
        ctx.moveTo(px - chSize, py);
        ctx.lineTo(px + chSize, py);
        ctx.moveTo(px, py - chSize);
        ctx.lineTo(px, py + chSize);
        ctx.stroke();

        ctx.strokeStyle = 'rgba(239, 68, 68, 0.7)';
        ctx.beginPath();
        ctx.arc(px, py, 4, 0, 2 * Math.PI);
        ctx.stroke();
    }

    // 5. Correlated AIS Displacement Vector
    if (inspectorState.showAis && crop.correlation && crop.local_center) {
        const ais = crop.correlation;
        if (ais.distance_meters !== undefined) {
            // Draw AIS centroid marker
            ctx.fillStyle = '#10b981';
            ctx.beginPath();
            ctx.arc(crop.local_center.x * scale, crop.local_center.y * scale, 3, 0, 2 * Math.PI);
            ctx.fill();
        }
    }
}

/**
 * Apply color palette transformation to raw grayscale pixel array.
 */
function applyColorMapToImageData(imgData, mode) {
    if (mode === 'grayscale') return;

    const data = imgData.data;
    const len = data.length;

    for (let i = 0; i < len; i += 4) {
        const v = data[i]; // raw backscatter (0-255)
        const norm = v / 255.0;

        if (mode === 'amber') {
            // Radar PPI phosphor amber
            data[i] = Math.min(255, Math.round(255 * Math.sqrt(norm)));
            data[i + 1] = Math.min(255, Math.round(180 * norm));
            data[i + 2] = Math.min(255, Math.round(30 * Math.pow(norm, 1.5)));
        } else if (mode === 'green') {
            // Tactical phosphor green
            data[i] = Math.min(255, Math.round(30 * norm));
            data[i + 1] = Math.min(255, Math.round(255 * Math.pow(norm, 0.85)));
            data[i + 2] = Math.min(255, Math.round(50 * norm));
        } else if (mode === 'inferno') {
            // High-contrast thermal heatmap
            let r = 0, g = 0, b = 0;
            if (norm < 0.25) {
                const t = norm / 0.25;
                r = Math.round(15 + 60 * t);
                g = Math.round(5 + 15 * t);
                b = Math.round(45 + 100 * t);
            } else if (norm < 0.5) {
                const t = (norm - 0.25) / 0.25;
                r = Math.round(75 + 120 * t);
                g = Math.round(20 + 30 * t);
                b = Math.round(145 - 80 * t);
            } else if (norm < 0.75) {
                const t = (norm - 0.5) / 0.25;
                r = Math.round(195 + 50 * t);
                g = Math.round(50 + 120 * t);
                b = Math.round(65 - 40 * t);
            } else {
                const t = (norm - 0.75) / 0.25;
                r = 255;
                g = Math.round(170 + 85 * t);
                b = Math.round(25 + 230 * t);
            }
            data[i] = r;
            data[i + 1] = g;
            data[i + 2] = b;
        }
    }
}

/**
 * Update analytical badges (SNR, RCS, Dynamic Range, CNN Classification, Wake dynamics).
 */
function renderAnalyticsBadges(cropData) {
    const stats = cropData.stats || {};

    // SNR Badge
    const snr = stats.snr_db !== undefined ? stats.snr_db : 0;
    const snrEl = document.getElementById('inspectorSnrBadge');
    if (snrEl) {
        let snrRating = 'Nominal';
        let snrColor = '#10b981'; // Green
        if (snr >= 14) {
            snrRating = 'High SNR';
            snrColor = '#10b981';
        } else if (snr >= 8) {
            snrRating = 'Moderate';
            snrColor = '#f59e0b';
        } else {
            snrRating = 'Low / Cluttered';
            snrColor = '#ef4444';
        }
        snrEl.innerHTML = `<span style="color: ${snrColor}; font-weight: 700;">${snr} dB</span> (${snrRating})`;
    }

    // RCS & Clutter
    setInspectorText('inspectorRcsBadge', stats.estimated_rcs_dbsm !== undefined ? `${stats.estimated_rcs_dbsm} dBm²` : 'N/A');
    setInspectorText('inspectorClutterFloor', stats.clutter_mean !== undefined ? `${stats.clutter_mean} DN (±${stats.clutter_std})` : 'N/A');
    setInspectorText('inspectorDynamicRange', stats.dynamic_range_db !== undefined ? `${stats.dynamic_range_db} dB` : 'N/A');

    // Classification Badge
    const classEl = document.getElementById('inspectorClassBadge');
    if (classEl) {
        if (cropData.classification && cropData.classification.predicted_class) {
            const pred = cropData.classification.predicted_class;
            const conf = Math.round((cropData.classification.confidence || 0) * 100);
            classEl.innerHTML = `<span class="badge" style="background: #2563eb; color: #fff; padding: 3px 8px; border-radius: 4px;">${escapeHtml(pred)} (${conf}%)</span>`;
        } else {
            classEl.innerHTML = `<span style="color: #64748b;">Classical CFAR Detection</span>`;
        }
    }

    // Wake & Kinematics Badge
    const wakeEl = document.getElementById('inspectorWakeBadge');
    if (wakeEl) {
        if (cropData.local_wake) {
            const wake = cropData.local_wake;
            let wakeText = `Wake Angle: ${Math.round(wake.wake_angle)}°`;
            if (wake.estimated_speed_knots) {
                wakeText += ` • Est. Speed: ${wake.estimated_speed_knots} kts`;
            }
            if (wake.ais_speed_discrepancy) {
                wakeText += ` <span style="color: #ef4444; font-weight: 700;">[⚠️ AIS Speed Spoofing Suspected]</span>`;
            } else {
                wakeText += ` <span style="color: #10b981;">[Speed Verified]</span>`;
            }
            wakeEl.innerHTML = wakeText;
        } else {
            wakeEl.innerHTML = `<span style="color: #64748b;">No prominent wake envelope detected</span>`;
        }
    }
}

/**
 * Draw 1D cross-sectional Transect Profiles (Range and Azimuth cuts through peak pixel).
 */
function renderTransectProfiles(stats) {
    const canvas = document.getElementById('inspectorTransectCanvas');
    if (!canvas || !stats) return;

    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const rangeProf = stats.range_profile || [];
    const azProf = stats.azimuth_profile || [];
    const clutter = stats.clutter_mean || 0;
    const maxVal = Math.max(stats.max_intensity || 255, 1);

    // Background grid
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    for (let y = 0; y < h; y += 20) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
    }

    // Draw Clutter floor reference line
    const clutterY = h - (clutter / maxVal) * (h - 15);
    ctx.strokeStyle = 'rgba(239, 68, 68, 0.6)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(0, clutterY);
    ctx.lineTo(w, clutterY);
    ctx.stroke();
    ctx.setLineDash([]);

    // 1. Range Profile (Cyan curve)
    if (rangeProf.length > 1) {
        ctx.strokeStyle = '#06b6d4'; // Cyan
        ctx.lineWidth = 2;
        ctx.beginPath();
        const step = w / (rangeProf.length - 1);
        rangeProf.forEach((v, i) => {
            const px = i * step;
            const py = h - (v / maxVal) * (h - 15);
            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        });
        ctx.stroke();
    }

    // 2. Azimuth Profile (Magenta curve)
    if (azProf.length > 1) {
        ctx.strokeStyle = '#ec4899'; // Magenta
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        const step = w / (azProf.length - 1);
        azProf.forEach((v, i) => {
            const px = i * step;
            const py = h - (v / maxVal) * (h - 15);
            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        });
        ctx.stroke();
    }

    // Transect Legend
    ctx.font = '10px monospace';
    ctx.fillStyle = '#06b6d4';
    ctx.fillText('— Range (X)', 10, 14);
    ctx.fillStyle = '#ec4899';
    ctx.fillText('— Azimuth (Y)', 95, 14);
    ctx.fillStyle = '#ef4444';
    ctx.fillText('-- Clutter Floor', 190, 14);
}

/**
 * Draw 32-bin intensity histogram on canvas.
 */
function renderHistogram(bins) {
    const canvas = document.getElementById('inspectorHistCanvas');
    if (!canvas || !bins || bins.length === 0) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, width, height);

    const maxVal = Math.max(...bins, 1);
    const barWidth = width / bins.length;

    bins.forEach((val, i) => {
        const barHeight = (val / maxVal) * (height - 8);
        const norm = i / bins.length;
        // Gradient color from blue to cyan to yellow
        const r = Math.round(30 + 225 * norm);
        const g = Math.round(100 + 120 * norm);
        const b = Math.round(240 - 180 * norm);

        ctx.fillStyle = `rgb(${r}, ${g}, ${b})`;
        ctx.fillRect(i * barWidth, height - barHeight, barWidth - 1, barHeight);
    });
}

/**
 * Handle canvas mouse movement to display pixel backscatter DN and coordinates.
 */
function onInspectorCanvasMouseMove(evt) {
    const canvas = document.getElementById('inspectorChipCanvas');
    const hoverInfo = document.getElementById('inspectorHoverInfo');
    if (!canvas || !hoverInfo || !inspectorState.image || !inspectorState.cropData) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = evt.clientX - rect.left;
    const mouseY = evt.clientY - rect.top;

    const scale = inspectorState.zoom;
    const cropX = Math.floor(mouseX / scale);
    const cropY = Math.floor(mouseY / scale);

    const crop = inspectorState.cropData;
    const globalX = (crop.origin ? crop.origin.x : 0) + cropX;
    const globalY = (crop.origin ? crop.origin.y : 0) + cropY;

    // Sample pixel from offscreen canvas
    const offCanvas = document.createElement('canvas');
    offCanvas.width = inspectorState.image.width;
    offCanvas.height = inspectorState.image.height;
    const offCtx = offCanvas.getContext('2d');
    offCtx.drawImage(inspectorState.image, 0, 0);

    let val = 0;
    try {
        const p = offCtx.getImageData(cropX, cropY, 1, 1).data;
        val = p[0];
    } catch (_) {}

    hoverInfo.innerText = `Chip: (${cropX}, ${cropY}) | Scene: (${globalX}, ${globalY}) | DN: ${val}`;
}

/**
 * Canvas Zoom Controls.
 */
function setInspectorZoom(zoomLevel) {
    if (zoomLevel === 'fit') {
        const container = document.querySelector('.crop-preview-container');
        if (container && inspectorState.image) {
            const availW = container.clientWidth - 20;
            const availH = 260;
            const fitZoom = Math.min(availW / inspectorState.image.width, availH / inspectorState.image.height);
            inspectorState.zoom = Math.max(0.5, Math.min(6.0, fitZoom));
        }
    } else {
        inspectorState.zoom = Math.max(0.5, Math.min(8.0, parseFloat(zoomLevel)));
    }

    const zoomLabel = document.getElementById('inspectorZoomVal');
    if (zoomLabel) zoomLabel.innerText = `${Math.round(inspectorState.zoom * 100)}%`;

    renderRadarChip();
}

function adjustInspectorZoom(factor) {
    setInspectorZoom(inspectorState.zoom * factor);
}

/**
 * Colormap & Overlay Toggles.
 */
function setInspectorColorMap(mapName) {
    inspectorState.colorMap = mapName;
    renderRadarChip();
}

function toggleInspectorOverlay(layerName, checked) {
    if (layerName === 'obb') inspectorState.showObb = checked;
    else if (layerName === 'wake') inspectorState.showWake = checked;
    else if (layerName === 'peak') inspectorState.showPeak = checked;
    else if (layerName === 'ais') inspectorState.showAis = checked;
    renderRadarChip();
}

/**
 * Launch Temporal Scrubber directly from Inspector modal.
 */
function openScrubberFromInspector() {
    if (inspectorState.folderName && typeof openTemporalAisScrubber === 'function') {
        closeInspectorModal();
        openTemporalAisScrubber(inspectorState.folderName);
    }
}

function setInspectorText(elId, text) {
    const el = document.getElementById(elId);
    if (el) el.innerText = text;
}

function closeInspectorModal() {
    const modal = document.getElementById('cropInspectorModal');
    if (modal) {
        modal.classList.remove('active');
    }
}

/**
 * Areas of Interest (AOI) management, automated capture scheduling, and pass predictions.
 */

let aoiMapLayers = [];

function toggleAoiLayer(uiId) {
    const layerObj = aoiMapLayers.find(l => l.uiId === uiId);
    if (!layerObj) return;

    const cb = document.querySelector(`#${uiId} > .layer-info-header input[type="checkbox"]`);
    if (cb && cb.checked) {
        map.addLayer(layerObj.leafletLayer);
    } else {
        map.removeLayer(layerObj.leafletLayer);
    }
    if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
}

function teleportToAoi(aoiId) {
    const item = aoiMapLayers.find(l => l.id === aoiId);
    if (item && item.bounds) {
        if (!map.hasLayer(item.leafletLayer)) {
            map.addLayer(item.leafletLayer);
            const cb = document.querySelector(`#${item.uiId} > .layer-info-header input[type="checkbox"]`);
            if (cb) cb.checked = true;
        }
        map.fitBounds(item.bounds, { padding: [60, 60], maxZoom: 14 });
        if (typeof showNotification === 'function') {
            showNotification(`Teleported to AOI: ${item.name || 'Selected Area'}`, 'info');
        }
    }
}

async function loadAOIs() {
    try {
        const response = await fetch(CONFIG.API_AOI);
        const aois = await response.json();
        const aoiList = document.getElementById('aoiList');
        
        // Clear UI list
        if (aoiList) {
            aoiList.innerHTML = '';
        }
        
        // Remove existing AOI layers and controls
        aoiMapLayers.forEach(l => {
            if (map.hasLayer(l.leafletLayer)) map.removeLayer(l.leafletLayer);
            const el = document.getElementById(l.uiId);
            if (el) el.remove();
        });
        aoiMapLayers = [];

        if (aois.length === 0) {
            if (aoiList) {
                aoiList.innerHTML = '<p style="color: #666; font-size: 0.9em;">No Areas of Interest saved yet.</p>';
            }
            return;
        }

        aois.forEach(aoi => {
            const safeAoiName = escapeHtml(aoi.name || `AOI #${aoi.id}`);
            if (aoiList) {
                const div = document.createElement('div');
                div.style.cssText = 'border: 1px solid #ddd; padding: 10px; border-radius: 5px; background: #fafafa;';
                
                let nextScanText = '<span style="color: #666; font-size: 0.8em;">Not predicted yet</span>';
                if (aoi.next_scan) {
                    const scanDate = new Date(aoi.next_scan);
                    nextScanText = `<span style="background: #28a745; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em;" title="Next Scan: ${scanDate.toLocaleString()}">${scanDate.toLocaleString()}</span>`;
                }

                const isAuto = aoi.auto_capture_enabled ? 'checked' : '';

                div.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
                        <strong>${safeAoiName}</strong>
                        ${nextScanText}
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; gap: 4px; flex-wrap: wrap;">
                        <small style="color: #666; font-size: 0.7em;">BBox: [${aoi.bbox.map(n => n.toFixed(2)).join(', ')}]</small>
                        <div style="display: flex; gap: 4px;">
                            <button class="btn btn-sm btn-outline-success" style="padding: 2px 6px; font-size: 0.75em;" onclick="triggerAoiScan(${aoi.id})" title="Initiate SAR imagery scan for ${safeAoiName}">🛰️ Scan</button>
                            <button class="btn btn-sm btn-outline-primary" style="padding: 2px 6px; font-size: 0.75em;" onclick="teleportToAoi(${aoi.id})" title="Center map on AOI">⌖ Teleport</button>
                            <button class="btn" style="padding: 3px 6px; font-size: 0.75em;" onclick="predictAOI(${aoi.id})">Predict</button>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 6px; font-size: 0.8em; color: #444;">
                        <input type="checkbox" id="auto-cap-${aoi.id}" ${isAuto} onchange="toggleAutoCapture(${aoi.id}, this.checked)">
                        <label for="auto-cap-${aoi.id}" style="cursor: pointer;">Auto-Capture on Pass</label>
                    </div>
                `;
                aoiList.appendChild(div);
            }

            const lBounds = L.latLngBounds([[aoi.bbox[1], aoi.bbox[0]], [aoi.bbox[3], aoi.bbox[2]]]);
            const aoiRect = L.rectangle(lBounds, { color: CONFIG.COLOR_AOI_OUTLINE, weight: 2, fill: false, interactive: true })
                .bindPopup(() => getAoiTabPopupContent(aoi.id), { className: 'sar-tab-popup aoi-tab-popup', minWidth: 260 });
            const tabMarker = L.circleMarker(lBounds.getNorthWest(), { radius: 6, opacity: 0, fillOpacity: 0, interactive: true })
                .bindTooltip(`<strong>${safeAoiName}</strong><br><span style="font-size:0.75rem;color:#86efac;font-weight:600;">Type: Area of Interest</span>`, { permanent: true, className: 'folder-tab-tooltip folder-tab-aoi', direction: 'right', offset: CONFIG.TOOLTIP_OFFSET })
                .bindPopup(() => getAoiTabPopupContent(aoi.id), { className: 'sar-tab-popup aoi-tab-popup', offset: [15, -5], minWidth: 260 });

            tabMarker.on('click', function() {
                this.openPopup();
            });

            const attachTooltipPointer = () => {
                const tt = tabMarker.getTooltip();
                if (tt && tt._container) {
                    tt._container.style.cursor = 'pointer';
                    tt._container.onclick = (ev) => {
                        ev.stopPropagation();
                        tabMarker.openPopup();
                    };
                }
            };
            tabMarker.on('tooltipopen', attachTooltipPointer);
            tabMarker.on('add', () => setTimeout(attachTooltipPointer, 0));
            
            const aoiGroup = L.featureGroup([aoiRect]).addTo(map);
            const layerId = `aoi-layer-${aoi.id}`;
            const zuluTime = aoi.next_scan ? new Date(aoi.next_scan).toISOString().replace('T', ' ').replace(/\..+/, '') + ' Z' : 'Not predicted yet';
            
            const controlHtml = `
                <div id="${layerId}" class="layer-info" style="border-left: 4px solid ${CONFIG.COLOR_AOI_OUTLINE};">
                    <div class="layer-info-header" style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                        <input type="checkbox" checked onchange="toggleAoiLayer('${layerId}')" title="Toggle Visibility">
                        <span style="background: rgba(40,167,69,0.15); color: #28a745; font-size: 0.72rem; font-weight: 700; padding: 2px 6px; border-radius: 3px; letter-spacing: 0.5px; text-transform: uppercase;">AOI</span>
                        <strong style="flex: 1; font-size: 0.95em; color: inherit; word-break: break-word; line-height: 1.3;" title="${safeAoiName}">${safeAoiName}</strong>
                    </div>
                    <div style="display: flex; gap: 6px; margin-bottom: 8px;">
                        <button type="button" class="btn btn-sm btn-outline-success btn-scan-aoi" onclick="triggerAoiScan(${aoi.id})" title="Initiate SAR imagery scan for ${safeAoiName}" style="flex: 1; padding: 4px 8px; font-size: 0.78rem; font-weight: 600; display: inline-flex; align-items: center; justify-content: center; gap: 4px; border-radius: 4px; background: rgba(40,167,69,0.08); border: 1px solid #28a745; color: #28a745; cursor: pointer;">
                            🛰️ Scan SAR
                        </button>
                        <button type="button" class="btn-teleport-aoi" onclick="teleportToAoi(${aoi.id})" title="Teleport to ${safeAoiName} on map" style="padding: 4px 8px; font-size: 0.78rem;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <circle cx="12" cy="12" r="10"></circle>
                                <line x1="22" y1="12" x2="18" y2="12"></line>
                                <line x1="6" y1="12" x2="2" y2="12"></line>
                                <line x1="12" y1="6" x2="12" y2="2"></line>
                                <line x1="12" y1="22" x2="12" y2="18"></line>
                            </svg>
                            Teleport
                        </button>
                    </div>
                    <details style="border: 1px solid #dee2e6; border-radius: 5px; padding: 8px; background: #f8f9fa;">
                        <summary style="cursor: pointer; font-size: 0.85em; color: #007bff; outline: none; font-weight: 500;">Layer Controls & Info</summary>
                        <div style="margin-top: 8px; font-size: 0.85em; color: #555; display: flex; flex-direction: column; gap: 6px;">
                            <div><strong>AOI Name:</strong> ${safeAoiName}</div>
                            <div><strong>Next Scan:</strong> ${zuluTime}</div>
                            <button type="button" class="btn btn-sm btn-success" onclick="triggerAoiScan(${aoi.id})" style="width: 100%; display: flex; align-items: center; justify-content: center; gap: 6px; padding: 6px 10px; font-weight: 600; cursor: pointer; margin-top: 4px;">
                                🛰️ Initiate SAR Scan (Latest Imagery)
                            </button>
                        </div>
                    </details>
                </div>
            `;
            document.getElementById('layersList').insertAdjacentHTML('beforeend', controlHtml);
            aoiMapLayers.push({ leafletLayer: aoiGroup, tabMarker: tabMarker, bounds: lBounds, uiId: layerId, id: aoi.id, name: safeAoiName, aoi: aoi });
        });
        
        if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
    } catch (error) {
        console.error("Error loading AOIs:", error);
        showNotification("Failed to load Areas of Interest from server.", "error");
    }
}

async function toggleAutoCapture(aoiId, enabled) {
    try {
        const res = await fetch(`/api/aoi/${aoiId}/auto_capture`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled })
        });
        if (res.ok) {
            showNotification(`Auto-capture ${enabled ? 'enabled' : 'disabled'} for AOI`, 'success');
        } else {
            showNotification('Failed to update auto-capture setting', 'error');
        }
    } catch (err) {
        showNotification('Connection error while updating auto-capture', 'error');
    }
}

async function predictAOI(aoiId) {
    try {
        const response = await fetch(`${CONFIG.API_AOI}/${aoiId}/predict`, {
            method: 'POST'
        });
        const result = await response.json();
        
        if (response.ok && result.status === 'success') {
            loadAOIs();
            let msg = "Scan predicted successfully!";
            const firstPred = (result.predictions && result.predictions.length > 0) ? result.predictions[0] : null;
            if (firstPred) {
                const sat = firstPred.satellite || "Sentinel-1";
                const dir = firstPred.orbit_direction ? ` (${firstPred.orbit_direction})` : '';
                const conf = firstPred.confidence_score ? ` [${Math.round(firstPred.confidence_score * 100)}% Conf]` : '';
                const src = firstPred.source ? ` [Source: ${firstPred.source}]` : '';
                msg = `Next Pass: ${sat}${dir}${conf}${src} at ${new Date(firstPred.time).toLocaleString()}`;
            }
            if (result.mission_analysis && result.mission_analysis.total_acquisitions > 0) {
                msg += ` | Hist: ${result.mission_analysis.total_acquisitions} passes (Avg ~${result.mission_analysis.average_revisit_days}d)`;
            }
            showNotification(msg, "success");
        } else {
            showNotification('Error predicting scan: ' + (result.error || 'No upcoming scans found'), "error");
        }
    } catch (error) {
        console.error("Error predicting AOI:", error);
        showNotification("Failed to trigger scan prediction.", "error");
    }
}

function initAoiHandlers() {
    const saveAoiBtn = document.getElementById('saveAoiBtn');
    if (!saveAoiBtn) return;

    saveAoiBtn.addEventListener('click', async () => {
        const nameInput = document.getElementById('aoiName');
        const name = nameInput.value.trim();
        
        if (!name) {
            showNotification("Please enter a name for the Area of Interest.", "warning");
            return;
        }

        if (!currentBbox) {
            showNotification("Please draw a rectangle on the map first.", "warning");
            return;
        }

        const bbox = currentBbox;

        try {
            const response = await fetch(CONFIG.API_AOI, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, bbox: bbox })
            });
            
            const result = await response.json();
            if (result.status === 'success') {
                nameInput.value = '';
                drawnItems.clearLayers();
                currentBbox = null;
                document.getElementById('saveAoiBtn').disabled = true;
                document.getElementById('scanBtn').disabled = true;
                document.getElementById('aoiStatus').innerText = "Draw a rectangle to begin.";
                document.getElementById('status').innerText = "Draw a rectangle to begin.";
                loadAOIs();
                showNotification("Area of Interest saved successfully!", "success");
            } else {
                showNotification('Error saving AOI: ' + (result.error || 'Unknown error'), "error");
            }
        } catch (error) {
            console.error("Error saving AOI:", error);
            showNotification("Failed to save Area of Interest.", "error");
        }
    });
}

async function forceScanAOI(aoiId) {
    let startPopup = null;
    try {
        startPopup = showNotification(`Initiating immediate AIS vessel scan for AOI #${aoiId}...`, "info", {
            autoClose: false,
            closable: true,
            title: "⚡ Force AIS Scan Started"
        });

        const res = await fetch(`/api/aoi/${aoiId}/force_ais_scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true })
        });
        const data = await res.json();

        if (startPopup && typeof startPopup.close === 'function') {
            startPopup.close();
        }

        if (res.ok && data.status === 'success') {
            const count = (data.results && data.results.total_inserted) || 0;
            showNotification(`Force AIS scan complete: ${count} vessel records ingested into database.`, "success", {
                autoClose: false,
                closable: true,
                showAckButton: true,
                ackText: "Dismiss",
                title: "✅ Force Scan Results"
            });
            if (typeof refreshAISVessels === 'function' && typeof map !== 'undefined' && map) {
                refreshAISVessels(map);
            }
        } else {
            showNotification(`Force AIS scan failed: ${data.error || 'Unknown error'}`, "error", {
                autoClose: false,
                closable: true,
                showAckButton: true,
                ackText: "Dismiss",
                title: "❌ Force Scan Failed"
            });
        }
    } catch (err) {
        console.error("Error running force AIS scan:", err);
        if (startPopup && typeof startPopup.close === 'function') {
            startPopup.close();
        }
        showNotification(`Failed to trigger force AIS scan: ${err.message || 'Connection error'}`, "error", {
            autoClose: false,
            closable: true,
            showAckButton: true,
            ackText: "Dismiss",
            title: "❌ Force Scan Error"
        });
    }
}

function getAoiTabPopupContent(aoiId) {
    const layerObj = aoiMapLayers.find(l => l.id === aoiId);
    if (!layerObj) return '<div style="padding:10px;">AOI not found</div>';
    const aoi = layerObj.aoi || {};
    const safeAoiName = escapeHtml(aoi.name || layerObj.name);
    const zuluTime = aoi.next_scan ? new Date(aoi.next_scan).toISOString().replace('T', ' ').replace(/\..+/, '') + ' Z' : 'Not predicted yet';
    const bboxStr = aoi.bbox ? aoi.bbox.map(n => Number(n).toFixed(3)).join(', ') : '';

    return `
        <div class="sar-tab-popup-content aoi-tab-popup-content">
            <div class="sar-tab-popup-header" style="border-left: 4px solid #16a34a; padding-left: 8px; margin-bottom: 8px;">
                <div style="font-size: 0.72rem; font-weight: 700; color: #16a34a; text-transform: uppercase; letter-spacing: 0.5px;">Area of Interest</div>
                <h4 style="margin: 2px 0 0 0; font-size: 0.95rem; font-weight: 700; color: #0f172a;">${safeAoiName}</h4>
            </div>
            <div class="sar-tab-popup-meta" style="margin-bottom: 10px;">
                <div class="meta-row"><span class="meta-lbl">BBox:</span><span class="meta-val">[${bboxStr}]</span></div>
                <div class="meta-row"><span class="meta-lbl">Next Scan:</span><span class="meta-val">${zuluTime}</span></div>
            </div>
            <div class="sar-tab-popup-actions">
                <button type="button" class="sar-tab-popup-btn-primary" style="background: #16a34a; color: white;" onclick="triggerAoiScan(${aoiId})">
                    🛰️ Scan Latest SAR Imagery
                </button>
                <button type="button" class="sar-tab-popup-btn-secondary" style="margin-top: 6px;" onclick="predictAOI(${aoiId})">
                    ⚡ Predict Next Pass
                </button>
            </div>
        </div>
    `;
}

async function triggerAoiScan(aoiId) {
    if (typeof isScanning !== 'undefined' && isScanning) {
        showNotification("A scan is already in progress.", "warning");
        return;
    }
    const layerObj = aoiMapLayers.find(l => l.id === aoiId);
    const aoi = layerObj ? layerObj.aoi : null;
    const aoiName = aoi ? aoi.name : `AOI #${aoiId}`;
    const bbox = aoi ? aoi.bbox : null;

    if (map) map.closePopup();

    showNotification(`Initiating SAR scan for ${aoiName}...`, "info");

    const statusText = document.getElementById('status');
    const scanBtn = document.getElementById('scanBtn');
    if (statusText) statusText.innerText = `Dispatching SAR acquisition for ${aoiName}...`;
    if (scanBtn) scanBtn.disabled = true;
    if (typeof isScanning !== 'undefined') isScanning = true;

    try {
        const asyncRes = await fetch(`/api/aoi/${aoiId}/scan?async=true`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ async: true })
        });

        if (asyncRes.ok) {
            const taskData = await asyncRes.json();
            const taskId = taskData.task_id;
            if (statusText) statusText.innerText = `Processing SAR imagery for ${aoiName} in background...`;
            if (typeof pollScanTask === 'function') {
                pollScanTask(taskId, bbox);
            }
            return;
        }

        const errorData = await asyncRes.json().catch(() => null);
        if (errorData && errorData.error) {
            if (statusText) statusText.innerText = "Draw a rectangle to begin.";
            if (scanBtn) scanBtn.disabled = false;
            if (typeof isScanning !== 'undefined') isScanning = false;
            showNotification(`Scan Error: ${errorData.error}`, "error");
            return;
        }

        // Fallback to synchronous endpoint
        const syncRes = await fetch(`/api/aoi/${aoiId}/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const result = await syncRes.json().catch(() => ({}));
        if (typeof handleScanCompletion === 'function') {
            handleScanCompletion(result);
        }
    } catch (err) {
        console.error("Error triggering AOI scan:", err);
        if (statusText) statusText.innerText = "Draw a rectangle to begin.";
        if (scanBtn) scanBtn.disabled = false;
        if (typeof isScanning !== 'undefined') isScanning = false;
        showNotification(`Connection failed while fetching SAR imagery for ${aoiName}.`, "error");
    }
}



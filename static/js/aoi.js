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

async function loadAOIs() {
    try {
        const response = await fetch(CONFIG.API_AOI);
        const aois = await response.json();
        const aoiList = document.getElementById('aoiList');
        
        if (aoiList) {
            aoiList.innerHTML = '';
            if (aois.length === 0) {
                aoiList.innerHTML = '<div style="color: #666; font-size: 0.85em;">No AOIs saved yet.</div>';
            }
        }
        
        // Clear existing AOI map layers
        aoiMapLayers.forEach(item => {
            map.removeLayer(item.leafletLayer);
            const uiEl = document.getElementById(item.uiId);
            if (uiEl) uiEl.remove();
        });
        aoiMapLayers = [];

        aois.forEach(aoi => {
            const safeAoiName = escapeHtml(aoi.name);
            if (aoiList) {
                const div = document.createElement('div');
                div.style.border = '1px solid #eee';
                div.style.padding = '10px';
                div.style.borderRadius = '5px';
                div.style.backgroundColor = '#fdfdfd';
                
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
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
                        <small style="color: #666; font-size: 0.7em;">BBox: [${aoi.bbox.map(n => n.toFixed(2)).join(', ')}]</small>
                        <button class="btn" style="padding: 4px 8px; font-size: 0.8em;" onclick="predictAOI(${aoi.id})">Predict Scan</button>
                    </div>
                    <div style="display: flex; align-items: center; gap: 6px; font-size: 0.8em; color: #444;">
                        <input type="checkbox" id="auto-cap-${aoi.id}" ${isAuto} onchange="toggleAutoCapture(${aoi.id}, this.checked)">
                        <label for="auto-cap-${aoi.id}" style="cursor: pointer;">Auto-Capture on Pass</label>
                    </div>
                `;
                aoiList.appendChild(div);
            }

            const lBounds = L.latLngBounds([[aoi.bbox[1], aoi.bbox[0]], [aoi.bbox[3], aoi.bbox[2]]]);
            const aoiRect = L.rectangle(lBounds, { color: CONFIG.COLOR_AOI_OUTLINE, weight: 2, fill: false, interactive: true });
            const tabMarker = L.circleMarker(lBounds.getNorthWest(), { radius: 0, opacity: 0, fillOpacity: 0, interactive: false })
                .bindTooltip(`<strong>${safeAoiName}</strong><br>Type: Area of Interest`, { permanent: true, className: 'folder-tab-tooltip folder-tab-aoi', direction: 'right', offset: CONFIG.TOOLTIP_OFFSET });
            
            const aoiGroup = L.featureGroup([aoiRect]).addTo(map);
            const layerId = `aoi-layer-${aoi.id}`;
            const zuluTime = aoi.next_scan ? new Date(aoi.next_scan).toISOString().replace('T', ' ').replace(/\..+/, '') + ' Z' : 'Not predicted yet';
            
            const controlHtml = `
                <div id="${layerId}" class="layer-info" style="border-left: 4px solid ${CONFIG.COLOR_AOI_OUTLINE};">
                    <div class="layer-info-header" style="display: flex; align-items: center; gap: 8px;">
                        <input type="checkbox" checked onchange="toggleAoiLayer('${layerId}')" title="Toggle Visibility">
                        <strong style="flex: 1; font-size: 0.9em; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="AOI: ${safeAoiName}">AOI: ${safeAoiName}</strong>
                    </div>
                    <details style="margin-top: 8px; border: 1px solid #dee2e6; border-radius: 5px; padding: 8px; background: #f8f9fa;">
                        <summary style="cursor: pointer; font-size: 0.85em; color: #007bff; outline: none; font-weight: 500;">Layer Controls & Info</summary>
                        <div style="margin-top: 8px; font-size: 0.85em; color: #555;">
                            <strong>Next Scan:</strong> ${zuluTime}
                        </div>
                    </details>
                </div>
            `;
            document.getElementById('layersList').insertAdjacentHTML('beforeend', controlHtml);
            aoiMapLayers.push({ leafletLayer: aoiGroup, tabMarker: tabMarker, bounds: lBounds, uiId: layerId, id: aoi.id });
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

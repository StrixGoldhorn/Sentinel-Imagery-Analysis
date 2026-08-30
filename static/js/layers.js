/**
 * SAR and CV/OBB layer management on the map.
 */

let activeLayers = [];

const updateServerMetadata = debounce(async (folderName, customName) => {
    try {
        await fetch(`${CONFIG.API_UPDATE_METADATA}/${folderName}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ custom_name: customName })
        });
    } catch (e) {
        console.error("Failed to update metadata on server", e);
        showNotification("Failed to update metadata on server.", "error");
    }
}, CONFIG.METADATA_UPDATE_DEBOUNCE_MS || 1000);

function addImageryLayer(imageUrl, bounds, datetime, folderName, serverCustomName) {
    const dateObj = new Date(datetime);
    const zuluTime = dateObj.toISOString().replace('T', ' ').replace(/\..+/, '') + ' Z';
    const localTime = dateObj.toLocaleString();

    const savedNames = JSON.parse(localStorage.getItem('layer_custom_names') || '{}');
    let captureName = serverCustomName || savedNames[folderName] || folderName;

    const getPopupContent = (name) => `
        <div style="min-width: 150px;">
            <strong>${escapeHtml(name)}</strong><hr style="margin: 5px 0;">
            <small><strong>Zulu:</strong> ${zuluTime}</small><br>
            <small><strong>Local:</strong> ${localTime}</small><br>
            <a href="${imageUrl}" target="_blank" rel="noopener noreferrer">Download PNG</a>
        </div>
    `;

    const sarLayer = L.imageOverlay(imageUrl, bounds, {
        opacity: CONFIG.SAR_DEFAULT_OPACITY,
        interactive: false
    });

    const lBounds = L.latLngBounds(bounds);
    const outlineLayer = L.rectangle(lBounds, {
        color: CONFIG.COLOR_SAR_OUTLINE,
        weight: 2,
        fill: false,
        interactive: true
    }).bindPopup(getPopupContent(captureName));

    const tabMarker = L.circleMarker(lBounds.getNorthWest(), { radius: 0, opacity: 0, fillOpacity: 0, interactive: false })
      .bindTooltip(`<strong>${escapeHtml(captureName)}</strong><br>Type: SAR Imagery`, { permanent: true, className: 'folder-tab-tooltip folder-tab-sar', direction: 'right', offset: CONFIG.TOOLTIP_OFFSET });

    const layerGroup = L.featureGroup([sarLayer, outlineLayer]).addTo(map);
    
    const layerId = `layer-${Date.now()}-${Math.floor(Math.random()*1000)}`;
    const controlHtml = `
        <div id="${layerId}" class="layer-info">
            <div class="layer-info-header" style="display: flex; align-items: center; gap: 8px;">
                <input type="checkbox" checked onchange="toggleMapLayerVisibility('${layerId}')" title="Toggle Visibility">
                <input type="text" class="name-input" value="${escapeHtml(captureName)}" placeholder="Layer Name" style="flex: 1; min-width: 0;">
                <button class="remove-layer-btn" onclick="removeSpecificLayer('${folderName}', '${layerId}')" title="Remove Layer">&times;</button>
            </div>
            <details style="margin-top: 8px; border: 1px solid #dee2e6; border-radius: 5px; padding: 8px; background: #f8f9fa;">
                <summary style="cursor: pointer; font-size: 0.85em; color: #007bff; outline: none; font-weight: 500;">Layer Controls & Info</summary>
                <div style="margin-top: 8px; font-size: 0.85em;">
                    <div style="margin-bottom: 5px;">
                        <strong>Zulu:</strong> ${zuluTime}<br>
                        <strong>Local:</strong> ${localTime}
                    </div>
                    <div class="layer-controls">
                        <label>Opacity</label>
                        <input type="range" class="opacity-slider" min="0" max="1" step="0.01" value="1">
                        
                        <label style="margin-top: 10px; display: flex; justify-content: space-between;">
                            <span>CV Threshold</span>
                            <span class="threshold-val" style="font-weight: normal; color: #007bff;">${CONFIG.CV_DEFAULT_THRESHOLD}</span>
                        </label>
                        <input type="range" class="cv-threshold-slider" min="10" max="200" step="1" value="${CONFIG.CV_DEFAULT_THRESHOLD}" oninput="this.previousElementSibling.querySelector('.threshold-val').innerText = this.value" onchange="runCVDetection('${folderName}', '${layerId}')">
                        
                        <button class="btn run-cv-btn" style="margin-top: 10px; font-size: 0.8rem; padding: 6px; width: 100%;" onclick="runCVDetection('${folderName}', '${layerId}')">Run CV Detection</button>
                        
                        <div class="cv-toggle-container" style="display: none; margin-top: 10px; align-items: center; background: #f8f9fa; padding: 6px; border-radius: 4px; border: 1px solid #ddd;">
                            <label style="display: flex; align-items: center; gap: 8px; margin: 0; cursor: pointer; width: 100%;">
                                <input type="checkbox" class="cv-visibility-toggle" checked onchange="toggleCVLayer('${layerId}')">
                                <span class="cv-results-text" style="font-size: 0.85em; font-weight: 500;">0 Ships</span>
                            </label>
                        </div>
                    </div>
                    <div style="margin-top: 5px; text-align: right;"><small><a href="${imageUrl}" target="_blank" rel="noopener noreferrer">View Original Image</a></small></div>
                </div>
            </details>
        </div>
    `;
    document.getElementById('layersList').insertAdjacentHTML('afterbegin', controlHtml);
    
    const container = document.getElementById(layerId);
    container.querySelector('.opacity-slider').oninput = (e) => sarLayer.setOpacity(e.target.value);
    container.querySelector('.name-input').oninput = (e) => {
        const newName = e.target.value;
        outlineLayer.setPopupContent(getPopupContent(newName || folderName));
        tabMarker.setTooltipContent(`<strong>${escapeHtml(newName || folderName)}</strong><br>Type: SAR Imagery`);
        
        const names = JSON.parse(localStorage.getItem('layer_custom_names') || '{}');
        names[folderName] = e.target.value;
        localStorage.setItem('layer_custom_names', JSON.stringify(names));

        updateServerMetadata(folderName, newName);
    };

    activeLayers.push({ leafletLayer: layerGroup, sarLayer: sarLayer, tabMarker: tabMarker, bounds: lBounds, uiId: layerId, folder: folderName });
    if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
}

function removeSpecificLayer(folderName, uiId) {
    if (!confirm("Are you sure you want to remove this layer from the map?")) return;
    
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (layerObj) {
        map.removeLayer(layerObj.leafletLayer);
        if (layerObj.detectLayer) map.removeLayer(layerObj.detectLayer);
        const el = document.getElementById(uiId);
        if (el) el.remove();
        activeLayers = activeLayers.filter(l => l.uiId !== uiId);
        
        let selected = JSON.parse(localStorage.getItem('selected_scans') || '[]');
        selected = selected.filter(f => f !== folderName);
        localStorage.setItem('selected_scans', JSON.stringify(selected));
    }
}

function toggleMapLayerVisibility(uiId) {
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (!layerObj) return;

    const cb = document.querySelector(`#${uiId} > .layer-info-header input[type="checkbox"]`);
    if (cb && cb.checked) {
        map.addLayer(layerObj.leafletLayer);
    } else {
        map.removeLayer(layerObj.leafletLayer);
    }
    if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
}

function toggleCVLayer(uiId) {
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (!layerObj || !layerObj.detectLayer) return;

    const cb = document.querySelector(`#${uiId} .cv-visibility-toggle`);
    if (cb && cb.checked) {
        map.addLayer(layerObj.detectLayer);
    } else {
        map.removeLayer(layerObj.detectLayer);
    }
    if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
}

async function runCVDetection(folderName, uiId) {
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (!layerObj) return;

    const btn = document.querySelector(`#${uiId} .run-cv-btn`);
    const thresholdInput = document.querySelector(`#${uiId} .cv-threshold-slider`);
    const thresholdVal = thresholdInput ? thresholdInput.value : CONFIG.CV_DEFAULT_THRESHOLD;
    const toggleContainer = document.querySelector(`#${uiId} .cv-toggle-container`);
    const toggleCb = document.querySelector(`#${uiId} .cv-visibility-toggle`);
    const resultsText = document.querySelector(`#${uiId} .cv-results-text`);

    btn.disabled = true;
    btn.innerText = "Running Detection...";

    try {
        const res = await fetch(`${CONFIG.API_RUN_CV}/${folderName}`, { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ threshold: thresholdVal })
        });
        const data = await res.json();

        if (data.status === 'success') {
            if (layerObj.detectLayer) map.removeLayer(layerObj.detectLayer);
            
            const detectLayer = L.featureGroup();
            layerObj.detectLayer = detectLayer;
            layerObj.cvTabs = [];

            const bounds = layerObj.bounds;
            const minLat = bounds.getSouth();
            const maxLat = bounds.getNorth();
            const minLon = bounds.getWest();
            const maxLon = bounds.getEast();

            const latScale = (maxLat - minLat) / data.height;
            const lonScale = (maxLon - minLon) / data.width;

            const detectionsList = data.detections || (data.boxes ? data.boxes.map(b => ({ x: b[0], y: b[1], width: b[2], height: b[3] })) : []);

            detectionsList.forEach(item => {
                const b_minLon = minLon + item.x * lonScale;
                const b_maxLon = minLon + (item.x + item.width) * lonScale;
                const b_maxLat = maxLat - item.y * latScale;
                const b_minLat = maxLat - (item.y + item.height) * latScale;

                const cvBounds = L.latLngBounds([[b_minLat, b_minLon], [b_maxLat, b_maxLon]]);

                // If Oriented Bounding Box polygon vertices exist, draw polygon, else rectangle
                let shapeLayer;
                if (item.polygon_points && item.polygon_points.length === 4) {
                    const geoPoints = item.polygon_points.map(pt => [
                        maxLat - pt[1] * latScale,
                        minLon + pt[0] * lonScale
                    ]);
                    shapeLayer = L.polygon(geoPoints, {
                        color: CONFIG.COLOR_OBB_DETECTION || '#e67e22',
                        weight: 2,
                        fillColor: '#e67e22',
                        fillOpacity: 0.15,
                        interactive: true
                    });
                } else {
                    shapeLayer = L.rectangle(cvBounds, {
                        color: CONFIG.COLOR_CV_DETECTION,
                        weight: 2,
                        fill: false,
                        interactive: true
                    });
                }

                // Popup with vessel metrology + button to open crop inspector
                const lengthInfo = item.length ? `<br><strong>Est. Length:</strong> ${item.length} m<br><strong>Est. Beam:</strong> ${item.beam} m<br><strong>Heading:</strong> ${item.angle}°` : '';
                const confInfo = item.confidence ? `<br><strong>Confidence:</strong> ${(item.confidence * 100).toFixed(1)}%` : '';
                shapeLayer.bindPopup(`
                    <div style="min-width: 140px;">
                        <strong>Vessel Detection</strong>
                        <hr style="margin: 4px 0;">
                        ${confInfo}
                        ${lengthInfo}
                        <hr style="margin: 6px 0;">
                        <button class="btn" style="width: 100%; padding: 4px; font-size: 0.8em;" onclick='inspectDetection("${folderName}", ${JSON.stringify(item)})'>Inspect Crop & Radar</button>
                    </div>
                `);

                shapeLayer.addTo(detectLayer);

                const tabMarker = L.circleMarker(cvBounds.getNorthWest(), { radius: 0, opacity: 0, fillOpacity: 0, interactive: false })
                    .bindTooltip(`<strong>Ship Detected</strong><br>Type: OBB Detection`, { permanent: true, className: 'folder-tab-tooltip folder-tab-cv', direction: 'right', offset: CONFIG.TOOLTIP_OFFSET });
                
                layerObj.cvTabs.push({ marker: tabMarker, bounds: cvBounds });
            });

            if (!toggleCb || toggleCb.checked) {
                detectLayer.addTo(map);
            }
            if (toggleContainer) {
                toggleContainer.style.display = 'flex';
                resultsText.innerText = `${detectionsList.length} Ships Detected`;
            }

            if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
            btn.disabled = false;
            btn.innerText = "Update Detection";
            showNotification(`CV Detection completed: ${detectionsList.length} vessels found.`, "success");
        } else {
            btn.innerText = "Update Detection";
            btn.disabled = false;
            showNotification("CV Detection Error: " + data.error, "error");
        }
    } catch (err) {
        btn.innerText = "Update Detection";
        btn.disabled = false;
        showNotification("Connection Error during CV Detection.", "error");
    }
}

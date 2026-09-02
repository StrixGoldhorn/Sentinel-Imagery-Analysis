/**
 * SAR and CV/OBB layer management on the map.
 */

let activeLayers = [];
let currentModalLayerId = null;

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

function getSarTabTooltipContent(name, detectionsCount, cvRun) {
    const countText = cvRun ? `${detectionsCount} ship${detectionsCount === 1 ? '' : 's'}` : 'Not run';
    const badgeClass = (cvRun && detectionsCount > 0) ? 'tab-cv-badge has-detections' : 'tab-cv-badge';
    return `<strong>${escapeHtml(name)}</strong><span class="${badgeClass}">🚢 ${countText}</span>`;
}

function getSarTabPopupContent(uiId) {
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (!layerObj) return '';
    const name = layerObj.name || layerObj.folder;
    const count = layerObj.detections ? layerObj.detections.length : 0;
    const btnText = layerObj.cvRun ? `📋 View Detected Ships (${count})` : '🚢 View Detected Ships (Run CV)';

    let countBadgeClass = 'not-run';
    let countBadgeText = 'Not Run';
    if (layerObj.cvRun) {
        if (count > 0) {
            countBadgeClass = 'has-ships';
            countBadgeText = `${count} Ship${count === 1 ? '' : 's'}`;
        } else {
            countBadgeClass = 'zero-ships';
            countBadgeText = '0 Ships';
        }
    }

    return `
        <div class="sar-tab-popup-content">
            <div class="sar-tab-popup-header">
                <h4 title="${escapeHtml(name)}">${escapeHtml(name)}</h4>
            </div>
            <div class="sar-tab-popup-meta">
                <div class="meta-row">
                    <span class="meta-lbl">Zulu:</span>
                    <span class="meta-val">${layerObj.zuluTime}</span>
                </div>
                <div class="meta-row">
                    <span class="meta-lbl">Local:</span>
                    <span class="meta-val">${layerObj.localTime}</span>
                </div>
            </div>
            <div class="sar-tab-popup-count-box">
                <span class="count-lbl">Ships Detected (CV):</span>
                <span class="count-badge ${countBadgeClass}">${countBadgeText}</span>
            </div>
            <div class="sar-tab-popup-actions">
                <button type="button" class="sar-tab-popup-btn-primary" onclick="openSarShipDetectionsModal('${uiId}')">
                    ${btnText}
                </button>
                <button type="button" class="sar-tab-popup-btn-secondary" onclick="runCVDetection('${layerObj.folder}', '${uiId}')">
                    ⚡ ${layerObj.cvRun ? 'Re-run CV Detection' : 'Run CV Detection'}
                </button>
                <button type="button" class="sar-tab-popup-btn-secondary" onclick="zoomToLayerBounds('${uiId}')">
                    🔍 Zoom to Bounds
                </button>
            </div>
            <div class="sar-tab-popup-footer">
                <a href="${layerObj.imageUrl}" target="_blank" rel="noopener noreferrer">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -1px;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                    Download Original PNG
                </a>
            </div>
        </div>
    `;
}

function addImageryLayer(imageUrl, bounds, datetime, folderName, serverCustomName) {
    const dateObj = new Date(datetime);
    const zuluTime = dateObj.toISOString().replace('T', ' ').replace(/\..+/, '') + ' Z';
    const localTime = dateObj.toLocaleString();

    const savedNames = JSON.parse(localStorage.getItem('layer_custom_names') || '{}');
    let captureName = serverCustomName || savedNames[folderName] || folderName;

    const sarLayer = L.imageOverlay(imageUrl, bounds, {
        opacity: CONFIG.SAR_DEFAULT_OPACITY,
        interactive: false
    });

    const lBounds = L.latLngBounds(bounds);
    const layerId = `layer-${Date.now()}-${Math.floor(Math.random()*1000)}`;

    const outlineLayer = L.rectangle(lBounds, {
        color: CONFIG.COLOR_SAR_OUTLINE,
        weight: 2,
        fill: false,
        interactive: true
    }).bindPopup(() => getSarTabPopupContent(layerId), { className: 'sar-tab-popup', minWidth: 260 });

    const tabMarker = L.circleMarker(lBounds.getNorthWest(), { radius: 6, opacity: 0, fillOpacity: 0, interactive: true })
        .bindTooltip(getSarTabTooltipContent(captureName, 0, false), { permanent: true, className: 'folder-tab-tooltip folder-tab-sar', direction: 'right', offset: CONFIG.TOOLTIP_OFFSET })
        .bindPopup(() => getSarTabPopupContent(layerId), { className: 'sar-tab-popup', offset: [15, -5], minWidth: 260 });

    tabMarker.on('click', function() {
        this.openPopup();
    });

    tabMarker.on('tooltipopen', function(e) {
        if (e.tooltip && e.tooltip._container) {
            e.tooltip._container.onclick = function(ev) {
                ev.stopPropagation();
                tabMarker.openPopup();
            };
        }
    });

    const layerGroup = L.featureGroup([sarLayer, outlineLayer]).addTo(map);

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
                        
                        <div style="display: flex; gap: 6px; margin-top: 10px;">
                            <button class="btn run-cv-btn" style="flex: 1; font-size: 0.8rem; padding: 6px;" onclick="runCVDetection('${folderName}', '${layerId}')">Run CV Detection</button>
                            <button class="btn view-ships-btn" style="flex: 1; font-size: 0.8rem; padding: 6px; background: #17a2b8; color: white; border: none; border-radius: 4px; cursor: pointer;" onclick="openSarShipDetectionsModal('${layerId}')">📋 View Ships (<span class="ships-count-label">0</span>)</button>
                        </div>
                        
                        <div class="cv-toggle-container" style="display: none; margin-top: 10px; align-items: center; justify-content: space-between; background: #f8f9fa; padding: 6px 10px; border-radius: 4px; border: 1px solid #ddd;">
                            <label style="display: flex; align-items: center; gap: 8px; margin: 0; cursor: pointer;">
                                <input type="checkbox" class="cv-visibility-toggle" checked onchange="toggleCVLayer('${layerId}')">
                                <span class="cv-results-text" style="font-size: 0.85em; font-weight: 500;">0 Ships</span>
                            </label>
                            <button type="button" style="padding: 2px 8px; font-size: 0.75rem; background: #007bff; color: white; border: none; border-radius: 3px; cursor: pointer;" onclick="openSarShipDetectionsModal('${layerId}')">View List</button>
                        </div>
                    </div>
                    <div style="margin-top: 5px; text-align: right;"><small><a href="${imageUrl}" target="_blank" rel="noopener noreferrer">View Original Image</a></small></div>
                </div>
            </details>
        </div>
    `;
    document.getElementById('layersList').insertAdjacentHTML('afterbegin', controlHtml);
    
    const layerObj = {
        leafletLayer: layerGroup,
        sarLayer: sarLayer,
        outlineLayer: outlineLayer,
        tabMarker: tabMarker,
        bounds: lBounds,
        uiId: layerId,
        folder: folderName,
        name: captureName,
        zuluTime: zuluTime,
        localTime: localTime,
        imageUrl: imageUrl,
        detections: [],
        cvRun: false,
        cvThreshold: CONFIG.CV_DEFAULT_THRESHOLD,
        detectLayer: null,
        cvTabs: []
    };

    const container = document.getElementById(layerId);
    container.querySelector('.opacity-slider').oninput = (e) => sarLayer.setOpacity(e.target.value);
    container.querySelector('.name-input').oninput = (e) => {
        const newName = e.target.value;
        layerObj.name = newName;
        tabMarker.setTooltipContent(getSarTabTooltipContent(newName || folderName, layerObj.detections.length, layerObj.cvRun));
        
        const names = JSON.parse(localStorage.getItem('layer_custom_names') || '{}');
        names[folderName] = e.target.value;
        localStorage.setItem('layer_custom_names', JSON.stringify(names));

        updateServerMetadata(folderName, newName);

        if (currentModalLayerId === layerId) {
            const layerSub = document.getElementById('sarDetectionsLayerSub');
            if (layerSub) layerSub.innerText = `Layer: ${newName || folderName} • Acquired: ${zuluTime}`;
        }
    };

    activeLayers.push(layerObj);
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

        if (currentModalLayerId === uiId) {
            closeSarShipDetectionsModal();
        }
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
    const thresholdVal = thresholdInput ? parseInt(thresholdInput.value, 10) : CONFIG.CV_DEFAULT_THRESHOLD;
    const toggleContainer = document.querySelector(`#${uiId} .cv-toggle-container`);
    const toggleCb = document.querySelector(`#${uiId} .cv-visibility-toggle`);
    const resultsText = document.querySelector(`#${uiId} .cv-results-text`);
    const shipsCountLabel = document.querySelector(`#${uiId} .ships-count-label`);

    if (btn) {
        btn.disabled = true;
        btn.innerText = "Running Detection...";
    }

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

            layerObj.imgWidth = data.width || 1;
            layerObj.imgHeight = data.height || 1;
            const latScale = (maxLat - minLat) / layerObj.imgHeight;
            const lonScale = (maxLon - minLon) / layerObj.imgWidth;

            const detectionsList = data.detections || (data.boxes ? data.boxes.map(b => ({ x: b[0], y: b[1], width: b[2], height: b[3] })) : []);

            layerObj.detections = detectionsList;
            layerObj.cvRun = true;
            layerObj.cvThreshold = thresholdVal;

            detectionsList.forEach((item, idx) => {
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
                const confVal = item.confidence !== undefined ? (item.confidence * 100).toFixed(1) : null;
                const confNum = item.confidence !== undefined ? item.confidence : 1;
                let confClass = 'high';
                if (confNum < 0.6) confClass = 'low';
                else if (confNum < 0.8) confClass = 'medium';

                const popupContent = `
                    <div class="cv-detection-popup-card">
                        <div class="cv-popup-header">
                            <div class="cv-popup-title-group">
                                <span class="cv-popup-icon">🚢</span>
                                <div>
                                    <div class="cv-popup-title">Vessel Detection #${idx + 1}</div>
                                    <div class="cv-popup-sub">SAR Computer Vision</div>
                                </div>
                            </div>
                            ${confVal !== null ? `<span class="cv-conf-badge ${confClass}">${confVal}% Conf</span>` : ''}
                        </div>
                        <div class="cv-popup-grid">
                            <div class="cv-popup-cell">
                                <span class="cv-lbl">Est. Length</span>
                                <span class="cv-val">${item.length ? `${item.length} m` : 'N/A'}</span>
                            </div>
                            <div class="cv-popup-cell">
                                <span class="cv-lbl">Est. Beam</span>
                                <span class="cv-val">${item.beam ? `${item.beam} m` : 'N/A'}</span>
                            </div>
                            <div class="cv-popup-cell">
                                <span class="cv-lbl">Heading</span>
                                <span class="cv-val">${item.angle !== undefined ? `${item.angle}°` : 'N/A'}</span>
                            </div>
                            <div class="cv-popup-cell">
                                <span class="cv-lbl">Pixel Size</span>
                                <span class="cv-val">${item.width}×${item.height} px</span>
                            </div>
                        </div>
                        <div class="cv-popup-actions">
                            <button type="button" class="cv-popup-btn-primary" onclick='inspectDetection("${folderName}", ${JSON.stringify(item)})'>
                                🔍 Inspect Crop & Radar
                            </button>
                            <button type="button" class="cv-popup-btn-secondary" onclick='openSarShipDetectionsModal("${uiId}")'>
                                📋 View All Ships List
                            </button>
                        </div>
                    </div>
                `;

                shapeLayer.bindPopup(popupContent, { className: 'cv-detection-popup', minWidth: 240 });

                shapeLayer.addTo(detectLayer);

                const tabMarker = L.circleMarker(cvBounds.getNorthWest(), { radius: 0, opacity: 0, fillOpacity: 0, interactive: false })
                    .bindTooltip(`<strong>Ship Detected #${idx + 1}</strong><br>Type: OBB Detection`, { permanent: true, className: 'folder-tab-tooltip folder-tab-cv', direction: 'right', offset: CONFIG.TOOLTIP_OFFSET });
                
                layerObj.cvTabs.push({ marker: tabMarker, bounds: cvBounds });
            });

            if (!toggleCb || toggleCb.checked) {
                detectLayer.addTo(map);
            }
            if (toggleContainer) {
                toggleContainer.style.display = 'flex';
                resultsText.innerText = `${detectionsList.length} Ships Detected`;
            }
            if (shipsCountLabel) {
                shipsCountLabel.innerText = detectionsList.length;
            }

            // Update Tab Tooltip badge and Tab Popup with new detection count
            layerObj.tabMarker.setTooltipContent(getSarTabTooltipContent(layerObj.name || folderName, detectionsList.length, true));
            layerObj.tabMarker.setPopupContent(getSarTabPopupContent(uiId));
            layerObj.outlineLayer.setPopupContent(getSarTabPopupContent(uiId));

            if (typeof updateTabsVisibility === 'function') updateTabsVisibility();
            if (btn) {
                btn.disabled = false;
                btn.innerText = "Update Detection";
            }
            showNotification(`CV Detection completed: ${detectionsList.length} vessels found.`, "success");

            // If modal is currently open for this layer, re-render its list
            if (currentModalLayerId === uiId) {
                renderSarDetectionsList();
            }
        } else {
            if (btn) {
                btn.innerText = "Update Detection";
                btn.disabled = false;
            }
            showNotification("CV Detection Error: " + data.error, "error");
        }
    } catch (err) {
        if (btn) {
            btn.innerText = "Update Detection";
            btn.disabled = false;
        }
        showNotification("Connection Error during CV Detection.", "error");
    }
}

function openSarShipDetectionsModal(uiId) {
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (!layerObj) return;

    currentModalLayerId = uiId;
    const modal = document.getElementById('sarShipDetectionsModal');
    if (!modal) return;

    const layerSub = document.getElementById('sarDetectionsLayerSub');
    if (layerSub) {
        layerSub.innerText = `Layer: ${layerObj.name || layerObj.folder} • Acquired: ${layerObj.zuluTime}`;
    }

    const slider = document.getElementById('sarModalThresholdSlider');
    if (slider) {
        slider.value = layerObj.cvThreshold || CONFIG.CV_DEFAULT_THRESHOLD;
        const valSpan = document.getElementById('sarModalThresholdVal');
        if (valSpan) valSpan.innerText = slider.value;
    }

    renderSarDetectionsList();
    modal.classList.add('active');
}

function closeSarShipDetectionsModal() {
    const modal = document.getElementById('sarShipDetectionsModal');
    if (modal) {
        modal.classList.remove('active');
    }
    currentModalLayerId = null;
}

function rerunCvFromModal() {
    if (!currentModalLayerId) return;
    const layerObj = activeLayers.find(l => l.uiId === currentModalLayerId);
    if (!layerObj) return;

    const slider = document.getElementById('sarModalThresholdSlider');
    const threshold = slider ? parseInt(slider.value, 10) : CONFIG.CV_DEFAULT_THRESHOLD;

    // Sync with sidebar threshold slider
    const sidebarSlider = document.querySelector(`#${currentModalLayerId} .cv-threshold-slider`);
    if (sidebarSlider) {
        sidebarSlider.value = threshold;
        const thresholdValSpan = document.querySelector(`#${currentModalLayerId} .threshold-val`);
        if (thresholdValSpan) thresholdValSpan.innerText = threshold;
    }

    const rerunBtn = document.getElementById('sarModalRerunBtn');
    if (rerunBtn) {
        rerunBtn.disabled = true;
        rerunBtn.innerText = "Running...";
    }

    runCVDetection(layerObj.folder, currentModalLayerId).finally(() => {
        if (rerunBtn) {
            rerunBtn.disabled = false;
            rerunBtn.innerText = "⚡ Re-run CV";
        }
    });
}

function renderSarDetectionsList() {
    if (!currentModalLayerId) return;
    const layerObj = activeLayers.find(l => l.uiId === currentModalLayerId);
    if (!layerObj) return;

    const container = document.getElementById('sarDetectionsContainer');
    const countBadge = document.getElementById('sarDetectionsCountBadge');
    const summaryEl = document.getElementById('sarDetectionsStatsSummary');
    if (!container) return;

    const detections = layerObj.detections || [];
    const count = detections.length;

    if (countBadge) {
        countBadge.innerText = layerObj.cvRun ? `${count} Ship${count === 1 ? '' : 's'} Detected` : 'CV Not Run Yet';
        countBadge.style.backgroundColor = (layerObj.cvRun && count > 0) ? '#e67e22' : (layerObj.cvRun ? '#28a745' : '#007bff');
    }

    if (!layerObj.cvRun) {
        container.innerHTML = `
            <div class="sar-detections-empty">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">🛰️</div>
                <h4 style="margin: 0 0 6px 0; color: #1e293b;">Computer Vision Detection Not Run</h4>
                <p style="font-size: 0.85rem; margin: 0 0 14px 0;">Ship detection has not been executed on this SAR satellite imagery layer yet.</p>
                <button type="button" class="btn" style="padding: 8px 18px; font-size: 0.88rem; background: #007bff; color: white; border: none; border-radius: 6px; cursor: pointer;" onclick="rerunCvFromModal()">
                    ▶ Run CV Ship Detection Now
                </button>
            </div>
        `;
        if (summaryEl) summaryEl.innerText = '';
        return;
    }

    if (count === 0) {
        container.innerHTML = `
            <div class="sar-detections-empty">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">🔍</div>
                <h4 style="margin: 0 0 6px 0; color: #1e293b;">No Ships Detected</h4>
                <p style="font-size: 0.85rem; margin: 0 0 14px 0;">No vessels were identified with threshold <strong>${layerObj.cvThreshold || 40}</strong>. You can try adjusting the threshold slider above and re-running.</p>
            </div>
        `;
        if (summaryEl) summaryEl.innerText = `0 vessels found (Threshold: ${layerObj.cvThreshold || 40})`;
        return;
    }

    // Sort detections
    const sortSelect = document.getElementById('sarDetectionsSortSelect');
    const sortVal = sortSelect ? sortSelect.value : 'conf_desc';

    const indexed = detections.map((d, origIdx) => ({ ...d, origIdx }));
    indexed.sort((a, b) => {
        if (sortVal === 'conf_desc') return (b.confidence || 0) - (a.confidence || 0);
        if (sortVal === 'length_desc') return (b.length || 0) - (a.length || 0);
        if (sortVal === 'length_asc') return (a.length || 0) - (b.length || 0);
        return a.origIdx - b.origIdx;
    });

    const bounds = layerObj.bounds;
    const minLat = bounds.getSouth();
    const maxLat = bounds.getNorth();
    const minLon = bounds.getWest();
    const maxLon = bounds.getEast();
    const imgHeight = layerObj.imgHeight || 1;
    const imgWidth = layerObj.imgWidth || 1;
    const latScale = (maxLat - minLat) / imgHeight;
    const lonScale = (maxLon - minLon) / imgWidth;

    let totalLength = 0;
    let validLengths = 0;

    const itemsHtml = indexed.map((item) => {
        if (item.length) {
            totalLength += item.length;
            validLengths++;
        }
        const confVal = item.confidence !== undefined ? (item.confidence * 100).toFixed(1) : 'N/A';
        const confNum = item.confidence || 0;
        let confBadgeClass = 'detection-confidence-badge';
        if (confNum < 0.6) confBadgeClass += ' low';
        else if (confNum < 0.8) confBadgeClass += ' medium';

        const shipLat = (maxLat - (item.center_y || (item.y + item.height/2)) * latScale).toFixed(5);
        const shipLon = (minLon + (item.center_x || (item.x + item.width/2)) * lonScale).toFixed(5);

        return `
            <div class="detection-item-card">
                <div class="detection-item-header">
                    <span class="detection-item-title">
                        <span>🚢 Vessel #${item.origIdx + 1}</span>
                    </span>
                    <span class="${confBadgeClass}">
                        Confidence: ${confVal}%
                    </span>
                </div>
                <div class="detection-item-grid">
                    <div class="detection-item-field">
                        <span class="lbl">Est. Length</span>
                        <span class="val">${item.length ? `${item.length} m` : 'N/A'}</span>
                    </div>
                    <div class="detection-item-field">
                        <span class="lbl">Est. Beam</span>
                        <span class="val">${item.beam ? `${item.beam} m` : 'N/A'}</span>
                    </div>
                    <div class="detection-item-field">
                        <span class="lbl">Heading / Angle</span>
                        <span class="val">${item.angle !== undefined ? `${item.angle}°` : 'N/A'}</span>
                    </div>
                    <div class="detection-item-field">
                        <span class="lbl">Position (Lat, Lon)</span>
                        <span class="val">${shipLat}, ${shipLon}</span>
                    </div>
                    <div class="detection-item-field">
                        <span class="lbl">Pixel Extents</span>
                        <span class="val">${item.width}×${item.height} px</span>
                    </div>
                </div>
                <div class="detection-item-actions">
                    <button type="button" class="sar-btn-zoom" onclick='zoomToShipDetection("${layerObj.uiId}", ${item.origIdx})'>
                        🎯 Zoom on Map
                    </button>
                    <button type="button" class="sar-btn-inspect" onclick='inspectDetection("${layerObj.folder}", ${JSON.stringify(item)})'>
                        🔍 Inspect Radar Crop
                    </button>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = itemsHtml;

    if (summaryEl) {
        const avgLen = validLengths > 0 ? (totalLength / validLengths).toFixed(1) : 'N/A';
        summaryEl.innerHTML = `Total Detected: <strong>${count}</strong> | Avg Length: <strong>${avgLen} m</strong> | Threshold: <strong>${layerObj.cvThreshold || 40}</strong>`;
    }
}

function zoomToShipDetection(uiId, detectionIndex) {
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (!layerObj || !layerObj.detections || !layerObj.detections[detectionIndex]) return;

    const item = layerObj.detections[detectionIndex];
    const bounds = layerObj.bounds;
    const minLat = bounds.getSouth();
    const maxLat = bounds.getNorth();
    const minLon = bounds.getWest();
    const maxLon = bounds.getEast();
    const imgHeight = layerObj.imgHeight || 1;
    const imgWidth = layerObj.imgWidth || 1;
    const latScale = (maxLat - minLat) / imgHeight;
    const lonScale = (maxLon - minLon) / imgWidth;

    const shipLat = maxLat - (item.center_y || (item.y + item.height/2)) * latScale;
    const shipLon = minLon + (item.center_x || (item.x + item.width/2)) * lonScale;

    closeSarShipDetectionsModal();
    map.setView([shipLat, shipLon], Math.max(map.getZoom(), 14), { animate: true });

    // Open popup for this detection if layer is available
    if (layerObj.detectLayer) {
        if (!map.hasLayer(layerObj.detectLayer)) {
            map.addLayer(layerObj.detectLayer);
            const toggleCb = document.querySelector(`#${uiId} .cv-visibility-toggle`);
            if (toggleCb) toggleCb.checked = true;
        }
        const layers = layerObj.detectLayer.getLayers();
        if (layers[detectionIndex]) {
            setTimeout(() => {
                layers[detectionIndex].openPopup();
            }, 300);
        }
    }
}

function zoomToLayerBounds(uiId) {
    const layerObj = activeLayers.find(l => l.uiId === uiId);
    if (!layerObj) return;
    map.fitBounds(layerObj.bounds, { padding: [40, 40] });
}

// Global Escape key handler for modal
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeSarShipDetectionsModal();
    }
});


/**
 * SAR Scanning and Asynchronous Task Runner.
 */

let isScanning = false;

async function triggerScan(bbox) {
    if (!bbox || isScanning) return;
    
    const scanBtn = document.getElementById('scanBtn');
    const statusText = document.getElementById('status');
    
    isScanning = true;
    scanBtn.disabled = true;
    statusText.innerText = "Dispatching SAR acquisition task...";

    const provider = document.getElementById('sarProviderSelect')?.value || 'copernicus';

    try {
        // Try asynchronous task submission first
        const asyncRes = await fetch(CONFIG.API_ASYNC_SCAN, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bbox: bbox, provider: provider })
        });

        if (asyncRes.ok) {
            const taskData = await asyncRes.json();
            const taskId = taskData.task_id;
            statusText.innerText = `Processing ${provider.toUpperCase()} SAR imagery in background...`;
            pollScanTask(taskId, bbox);
            return;
        }

        // If server explicitly returned an error message, show it
        const errorData = await asyncRes.json().catch(() => null);
        if (errorData && errorData.error) {
            statusText.innerText = "Draw a rectangle to begin.";
            scanBtn.disabled = false;
            isScanning = false;
            showNotification("Scan Error: " + errorData.error, "error");
            return;
        }

        // Fallback to synchronous scan if async endpoint is not available
        const response = await fetch(CONFIG.API_SCAN, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bbox: bbox, provider: provider })
        });
        
        const result = await response.json().catch(() => ({}));
        handleScanCompletion(result);
    } catch (e) {
        statusText.innerText = "Draw a rectangle to begin.";
        scanBtn.disabled = false;
        isScanning = false;
        showNotification("Connection failed while fetching SAR imagery.", "error");
    }

}

async function pollScanTask(taskId, bbox, retryCount = 0) {
    const statusText = document.getElementById('status');
    const scanBtn = document.getElementById('scanBtn');
    localStorage.setItem('activeScanTaskId', taskId);
    try {
        const res = await fetch(`${CONFIG.API_TASK_STATUS}/${taskId}`);
        if (!res.ok) throw new Error(`Task status request failed (${res.status})`);

        const data = await res.json();
        const taskStatus = String(data.status || '').toUpperCase();
        if (taskStatus === 'COMPLETED') {
            localStorage.removeItem('activeScanTaskId');
            const scanResult = data.result || {};
            handleScanCompletion({
                status: 'success',
                ...scanResult,
                customName: scanResult.customName || scanResult.folderName
            });
        } else if (taskStatus === 'FAILED' || taskStatus === 'CANCELLED') {
            localStorage.removeItem('activeScanTaskId');
            statusText.innerText = "Draw a rectangle to begin.";
            scanBtn.disabled = false;
            isScanning = false;
            showNotification("Scan failed: " + (data.error || data.message || "Unknown error"), "error");
        } else {
            statusText.innerText = `Acquiring SAR imagery... (${data.message || taskStatus || 'Processing'})`;
            setTimeout(() => pollScanTask(taskId, bbox, 0), 1500);
        }
    } catch (err) {
        if (retryCount < 5) {
            statusText.innerText = "Temporarily unable to read scan status; retrying...";
            const delay = Math.min(15000, 1000 * (2 ** retryCount));
            setTimeout(() => pollScanTask(taskId, bbox, retryCount + 1), delay);
        } else {
            statusText.innerText = "Scan status is unknown; reconnecting to status tracking...";
            setTimeout(() => pollScanTask(taskId, bbox, 0), 30000);
        }
    }
}

function handleScanCompletion(result) {
    const statusText = document.getElementById('status');
    const scanBtn = document.getElementById('scanBtn');

    if (result.status === 'success') {
        const layerId = addImageryLayer(result.imageUrl, result.bounds, result.datetime, result.folderName, result.customName || result.folderName);
        
        // Auto-render vessel detections if present from post-acquisition pipeline
        if (layerId && (result.detections || result.ship_count !== undefined)) {
            if (typeof applyDetectionsToLayer === 'function') {
                applyDetectionsToLayer(layerId, result);
            }
        }

        statusText.innerText = "Scan complete!";
        
        drawnItems.clearLayers();
        currentBbox = null;
        const saveAoiBtn = document.getElementById('saveAoiBtn');
        if (saveAoiBtn) saveAoiBtn.disabled = true;
        const aoiStatus = document.getElementById('aoiStatus');
        if (aoiStatus) aoiStatus.innerText = "Draw a rectangle to begin.";

        const shipCount = (result.detections && result.detections.length !== undefined)
            ? result.detections.length
            : (result.ship_count !== undefined ? result.ship_count : null);

        if (shipCount !== null) {
            showNotification(`SAR acquisition ready: ${shipCount} vessel${shipCount === 1 ? '' : 's'} detected. Intelligence briefing generated!`, "success");
        } else {
            showNotification("SAR acquisition successfully loaded!", "success");
        }
        scanBtn.disabled = false;
    } else {
        statusText.innerText = "Draw a rectangle to begin.";
        scanBtn.disabled = false;
        showNotification("Scan Error: " + (result.error || "Failed to acquire scan"), "error");
    }
    isScanning = false;
}

function initScannerHandlers() {
    const scanBtn = document.getElementById('scanBtn');
    if (scanBtn) {
        scanBtn.onclick = () => {
            if (currentBbox) {
                triggerScan(currentBbox);
            }
        };
    }

    const activeTaskId = localStorage.getItem('activeScanTaskId');
    if (activeTaskId) {
        isScanning = true;
        if (scanBtn) scanBtn.disabled = true;
        pollScanTask(activeTaskId, null);
    }

    const clearBtn = document.getElementById('clearScansBtn');
    if (clearBtn) {
        clearBtn.onclick = () => {
            if (!confirm("Are you sure you want to clear all scans?")) return;
            
            activeLayers.forEach(item => {
                map.removeLayer(item.leafletLayer);
                if (item.detectLayer) map.removeLayer(item.detectLayer);
                const uiEl = document.getElementById(item.uiId);
                if (uiEl) uiEl.remove();
            });
            activeLayers = [];
            localStorage.removeItem('selected_scans');
            const statusText = document.getElementById('status');
            if (statusText) statusText.innerText = "All layers cleared.";
        };
    }
}

const UMBRA_SITE_BOUNDS = {
    'singapore_strait': [1.234, 103.808, 1.294, 103.868],
    'panama_canal': [8.950, -79.605, 8.997, -79.558],
    'port_of_rotterdam': [51.861, 4.248, 51.909, 4.325],
    'port_of_antwerp': [51.236, 4.312, 51.286, 4.391],
    'port_of_hong_kong': [22.312, 114.095, 22.360, 114.147],
    'port_of_busan': [35.077, 129.048, 35.133, 129.117],
    'port_of_jebel_ali': [24.971, 55.021, 25.034, 55.090],
    'port_of_long_beach': [33.714, -118.274, 33.778, -118.198],
    'port_of_hamburg': [53.482, 9.895, 53.535, 9.983],
    'suez_canal': [29.93, 32.53, 30.01, 32.61],
    'strait_of_gibraltar': [36.12, -5.38, 36.18, -5.32],
    'strait_of_malacca': [2.18, 102.23, 2.24, 102.29]
};

function onSarProviderChange(provider) {
    const umbraControls = document.getElementById('umbraControls');
    if (umbraControls) {
        umbraControls.style.display = (provider === 'umbra') ? 'block' : 'none';
    }
}

function onUmbraSiteChange(siteKey) {
    if (!siteKey || !UMBRA_SITE_BOUNDS[siteKey]) return;
    const b = UMBRA_SITE_BOUNDS[siteKey]; // [min_lat, min_lon, max_lat, max_lon]
    const southWest = L.latLng(b[0], b[1]);
    const northEast = L.latLng(b[2], b[3]);
    const bounds = L.latLngBounds(southWest, northEast);

    if (window.map) {
        window.map.fitBounds(bounds);
    }
    if (window.drawnItems) {
        window.drawnItems.clearLayers();
        const rect = L.rectangle(bounds, { color: '#0ea5e9', weight: 2 });
        window.drawnItems.addLayer(rect);
    }

    currentBbox = [b[1], b[0], b[3], b[2]]; // [min_lon, min_lat, max_lon, max_lat]
    const bboxReadout = document.getElementById('selectedBboxCoords');
    const bboxDisplay = document.getElementById('selectedBboxDisplay');
    const statusText = document.getElementById('status');
    const scanBtn = document.getElementById('scanBtn');

    if (bboxReadout) bboxReadout.innerText = currentBbox.map(c => Number(c).toFixed(4)).join(', ');
    if (bboxDisplay) bboxDisplay.style.display = 'block';
    if (statusText) statusText.innerText = `Umbra site selected: ${siteKey.replace('_', ' ').toUpperCase()}`;
    if (scanBtn) scanBtn.disabled = false;
}

async function loadUmbraScenes() {
    const listEl = document.getElementById('umbraScenesList');
    const siteSelect = document.getElementById('umbraSiteSelect');
    const siteKey = siteSelect ? siteSelect.value : '';
    if (!listEl) return;

    listEl.style.display = 'block';
    listEl.innerHTML = '<span style="color: #64748b;">Loading Umbra Open Data scenes...</span>';

    try {
        const url = siteKey ? `/api/umbra/scenes?site=${encodeURIComponent(siteKey)}` : '/api/umbra/scenes';
        const res = await fetch(url);
        const data = await res.json();
        if (data.status === 'success' && data.scenes && data.scenes.length > 0) {
            listEl.innerHTML = data.scenes.map(s => `
                <div style="padding: 4px; border-bottom: 1px solid #e2e8f0;">
                    <strong>${escapeHtml(s.target_name || s.scene_id)}</strong><br>
                    <span>Res: ${s.resolution_meters}m | Pol: ${s.polarization}</span><br>
                    <small style="color: #64748b;">${s.timestamp.replace('T', ' ').slice(0, 19)} UTC</small>
                </div>
            `).join('');
        } else {
            listEl.innerHTML = '<span style="color: #94a3b8;">No open scenes indexed for this location.</span>';
        }
    } catch (err) {
        listEl.innerHTML = `<span style="color: #ef4444;">Failed to query Umbra catalog: ${escapeHtml(err.message)}</span>`;
    }
}


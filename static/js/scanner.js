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

    try {
        // Try asynchronous task submission first
        const asyncRes = await fetch(CONFIG.API_ASYNC_SCAN, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bbox: bbox })
        });

        if (asyncRes.ok) {
            const taskData = await asyncRes.json();
            const taskId = taskData.task_id;
            statusText.innerText = "Processing SAR imagery in background...";
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
            body: JSON.stringify({ bbox: bbox })
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
                imageUrl: scanResult.imageUrl,
                bounds: scanResult.bounds,
                datetime: scanResult.datetime,
                folderName: scanResult.folderName,
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
        addImageryLayer(result.imageUrl, result.bounds, result.datetime, result.folderName, result.customName || result.folderName);
        statusText.innerText = "Scan complete!";
        
        drawnItems.clearLayers();
        currentBbox = null;
        const saveAoiBtn = document.getElementById('saveAoiBtn');
        if (saveAoiBtn) saveAoiBtn.disabled = true;
        const aoiStatus = document.getElementById('aoiStatus');
        if (aoiStatus) aoiStatus.innerText = "Draw a rectangle to begin.";
        showNotification("SAR acquisition successfully loaded!", "success");
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

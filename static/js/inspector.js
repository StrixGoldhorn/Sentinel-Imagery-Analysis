/**
 * Vessel Crop and Detection Inspector Modal.
 */

async function inspectDetection(folderName, detectionData) {
    const modal = document.getElementById('cropInspectorModal');
    if (!modal) return;

    // Populate detection metadata fields
    document.getElementById('inspectorLength').innerText = detectionData.length ? `${detectionData.length} m` : 'N/A';
    document.getElementById('inspectorBeam').innerText = detectionData.beam ? `${detectionData.beam} m` : 'N/A';
    document.getElementById('inspectorHeading').innerText = detectionData.angle !== undefined ? `${detectionData.angle}°` : 'N/A';
    document.getElementById('inspectorConfidence').innerText = detectionData.confidence !== undefined ? `${(detectionData.confidence * 100).toFixed(1)}%` : 'N/A';
    document.getElementById('inspectorCoords').innerText = `X: ${detectionData.x}, Y: ${detectionData.y} (${detectionData.width}x${detectionData.height} px)`;

    const imgEl = document.getElementById('inspectorCropImg');
    const loadingEl = document.getElementById('inspectorLoading');
    const statsEl = document.getElementById('inspectorStats');
    const histCanvas = document.getElementById('inspectorHistCanvas');

    imgEl.style.display = 'none';
    loadingEl.style.display = 'block';
    statsEl.innerHTML = '';
    modal.classList.add('active');

    try {
        const query = new URLSearchParams({
            x: Math.round(detectionData.x),
            y: Math.round(detectionData.y),
            width: Math.round(detectionData.width),
            height: Math.round(detectionData.height),
            padding: 25
        });
        const res = await fetch(`/api/scan/${folderName}/crop?${query.toString()}`);
        const data = await res.json();

        if (res.ok && data.data_uri) {
            imgEl.src = data.data_uri;
            imgEl.style.display = 'block';
            loadingEl.style.display = 'none';

            statsEl.innerHTML = `
                <div><strong>Mean Intensity:</strong> ${data.stats.mean_intensity}</div>
                <div><strong>Peak Intensity:</strong> ${data.stats.max_intensity}</div>
                <div><strong>Min Intensity:</strong> ${data.stats.min_intensity}</div>
            `;

            drawHistogram(histCanvas, data.stats.histogram);
        } else {
            loadingEl.innerText = 'Failed to load radar crop.';
        }
    } catch (err) {
        console.error('Inspector crop error:', err);
        loadingEl.innerText = 'Error loading radar crop.';
    }
}

function drawHistogram(canvas, bins) {
    if (!canvas || !bins || bins.length === 0) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const maxVal = Math.max(...bins, 1);
    const barWidth = width / bins.length;

    bins.forEach((val, i) => {
        const barHeight = (val / maxVal) * (height - 10);
        ctx.fillStyle = '#007bff';
        ctx.fillRect(i * barWidth, height - barHeight, barWidth - 1, barHeight);
    });
}

function closeInspectorModal() {
    const modal = document.getElementById('cropInspectorModal');
    if (modal) {
        modal.classList.remove('active');
    }
}

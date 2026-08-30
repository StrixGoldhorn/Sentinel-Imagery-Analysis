/**
 * OpenSeaMap nautical chart overlay layer management.
 */

let nauticalLayer = null;
let nauticalLayerEnabled = false;

function initNauticalChart(mapInstance) {
    nauticalLayer = L.tileLayer(CONFIG.OPENSEAMAP_TILE_URL, {
        attribution: 'Map data &copy; <a href="http://www.openseamap.org">OpenSeaMap</a> contributors',
        maxZoom: 18
    });

    const savedState = localStorage.getItem('nautical_chart_enabled');
    if (savedState === 'true') {
        nauticalLayer.addTo(mapInstance);
        nauticalLayerEnabled = true;
    }

    const toggleCheckbox = document.getElementById('nauticalChartToggle');
    if (toggleCheckbox) {
        toggleCheckbox.checked = nauticalLayerEnabled;
        toggleCheckbox.addEventListener('change', (e) => {
            toggleNauticalChart(mapInstance, e.target.checked);
        });
    }
}

function toggleNauticalChart(mapInstance, enable) {
    if (!nauticalLayer) return;
    if (enable) {
        if (!mapInstance.hasLayer(nauticalLayer)) {
            nauticalLayer.addTo(mapInstance);
        }
        nauticalLayerEnabled = true;
        localStorage.setItem('nautical_chart_enabled', 'true');
        showNotification("Nautical chart overlay enabled", "success");
    } else {
        if (mapInstance.hasLayer(nauticalLayer)) {
            mapInstance.removeLayer(nauticalLayer);
        }
        nauticalLayerEnabled = false;
        localStorage.setItem('nautical_chart_enabled', 'false');
    }
}

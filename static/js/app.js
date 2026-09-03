/**
 * Main application initialization and event dispatching.
 */

let abortController = null;

function initSearch() {
    const searchInput = document.getElementById('locationSearch');
    const resultsBox = document.getElementById('searchResults');
    if (!searchInput || !resultsBox) return;

    const fetchSuggestions = debounce(async (query) => {
        if (query.length < CONFIG.SEARCH_MIN_CHARS) {
            resultsBox.style.display = 'none';
            return;
        }

        if (abortController) abortController.abort();
        abortController = new AbortController();

        try {
            const response = await fetch(`${CONFIG.NOMINATIM_SEARCH_URL}?format=json&q=${encodeURIComponent(query)}&limit=${CONFIG.SEARCH_RESULT_LIMIT}&accept-language=en`, { signal: abortController.signal });
            const data = await response.json();
            
            resultsBox.innerHTML = '';
            if (data.length > 0) {
                data.forEach(item => {
                    const div = document.createElement('div');
                    div.textContent = item.display_name;
                    div.onclick = () => {
                        map.setView([parseFloat(item.lat), parseFloat(item.lon)], CONFIG.SEARCH_ZOOM_LEVEL);
                        searchInput.value = item.display_name;
                        resultsBox.style.display = 'none';
                    };
                    resultsBox.appendChild(div);
                });
                resultsBox.style.display = 'block';
            } else {
                resultsBox.style.display = 'none';
            }
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('Suggestion error:', error);
                showNotification("Failed to fetch location suggestions.", "error");
            }
        }
    });

    searchInput.addEventListener('input', (e) => fetchSuggestions(e.target.value));

    document.addEventListener('click', (e) => {
        if (!document.querySelector('.search-group').contains(e.target)) {
            resultsBox.style.display = 'none';
        }
    });
}

function searchLocation() {
    const searchInput = document.getElementById('locationSearch');
    const resultsBox = document.getElementById('searchResults');
    if (!searchInput) return;
    const query = searchInput.value;
    if (!query) return;
    if (resultsBox) resultsBox.style.display = 'none';
}

function toggleAccordion(header) {
    const content = header.nextElementSibling;

    document.querySelectorAll('.accordion-header').forEach(h => { if(h !== header) h.classList.remove('active'); });
    document.querySelectorAll('.accordion-content').forEach(c => { if(c !== content) c.classList.remove('active'); });

    header.classList.toggle('active');
    content.classList.toggle('active');

    const isScanActive = document.getElementById('scan-page').classList.contains('active');
    const isAoiActive = document.getElementById('aoi-page').classList.contains('active');
    if ((isScanActive || isAoiActive) && !drawControlVisible) {
        map.addControl(drawControl);
        drawControlVisible = true;
    } else if (!isScanActive && !isAoiActive && drawControlVisible) {
        map.removeControl(drawControl);
        drawControlVisible = false;
    }
}

// Global initialization on page load
document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    initNauticalChart(map);
    initAISVessels(map);
    initSearch();
    initScannerHandlers();
    initAoiHandlers();
    loadAOIs();

    // Check for scans selected from gallery
    const params = new URLSearchParams(window.location.search);
    const loadImmediate = params.get('load');
    let selected = JSON.parse(localStorage.getItem('selected_scans') || '[]');

    if (loadImmediate && !selected.includes(loadImmediate)) {
        selected.push(loadImmediate);
        localStorage.setItem('selected_scans', JSON.stringify(selected));
    }

    // Check for AOI focus from AOIs page
    const aoiBboxParam = params.get('bbox') || params.get('aoi_bbox');
    if (aoiBboxParam) {
        const parts = aoiBboxParam.split(',').map(Number);
        if (parts.length === 4 && parts.every(n => !isNaN(n))) {
            const lBounds = L.latLngBounds([[parts[1], parts[0]], [parts[3], parts[2]]]);
            map.fitBounds(lBounds, { padding: [50, 50] });
            showNotification("Focused on Area of Interest", "info");
        }
    }

    // Check for new scan action from navbar
    if (params.get('action') === 'scan') {
        const scanHeader = document.querySelector('.accordion-header');
        if (scanHeader && !scanHeader.classList.contains('active')) {
            toggleAccordion(scanHeader);
        }
    }

    for (const folder of selected) {
        try {
            const res = await fetch(`${CONFIG.API_GET_SCAN}/${folder}`);
            const data = await res.json();
            if (!data.error) {
                addImageryLayer(data.imageUrl, data.bounds, data.datetime, folder, data.custom_name);
                if (folder === loadImmediate) {
                    map.fitBounds(data.bounds);
                }
            } else {
                showNotification("Failed to load scan data for: " + folder, "error");
            }
        } catch (err) {
            console.error("Failed to load persistent scan", folder);
            showNotification("Failed to load persistent scan: " + folder, "error");
        }
    }
});

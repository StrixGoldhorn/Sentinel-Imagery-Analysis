/**
 * Core Leaflet map initialization, drawing controls, and coordinate converters.
 */

let map = null;
let drawnItems = null;
let drawControl = null;
let drawControlVisible = true;
let currentBbox = null;

function initMap() {
    const savedView = JSON.parse(localStorage.getItem('map_view_state')) || {
        lat: CONFIG.MAP_DEFAULT_LAT,
        lng: CONFIG.MAP_DEFAULT_LNG,
        zoom: CONFIG.MAP_DEFAULT_ZOOM
    };

    map = L.map('map').setView([savedView.lat, savedView.lng], savedView.zoom);

    L.tileLayer(CONFIG.OSM_TILE_URL, {
        attribution: '© OpenStreetMap contributors'
    }).addTo(map);

    L.control.scale({ imperial: false }).addTo(map);

    drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    drawControl = new L.Control.Draw({
        draw: {
            polygon: false, marker: false, circle: false, polyline: false, circlemarker: false,
            rectangle: { shapeOptions: { color: CONFIG.COLOR_DRAW_RECTANGLE } }
        },
        edit: { featureGroup: drawnItems }
    });
    map.addControl(drawControl);

    // Save map view whenever it changes
    map.on('moveend', () => {
        localStorage.setItem('map_view_state', JSON.stringify({
            lat: map.getCenter().lat,
            lng: map.getCenter().lng,
            zoom: map.getZoom()
        }));
    });

    map.on('zoomend resize moveend', updateTabsVisibility);

    setupDrawingEvents();
    return map;
}

function getDimensions(layer) {
    const bounds = layer.getBounds();
    const nw = bounds.getNorthWest();
    const ne = bounds.getNorthEast();
    const sw = bounds.getSouthWest();
    
    const width = map.distance(nw, ne) / 1000;
    const height = map.distance(nw, sw) / 1000;
    
    return { width, height };
}

function setupDrawingEvents() {
    const statusText = document.getElementById('status');
    const aoiStatus = document.getElementById('aoiStatus');
    const scanBtn = document.getElementById('scanBtn');
    const saveAoiBtn = document.getElementById('saveAoiBtn');

    map.on(L.Draw.Event.DRAWSTART, function(e) {
        if (e.drawLayerType === 'rectangle') {
            const moveHandler = function() {
                const drawHandler = drawControl._toolbars.draw._modes.rectangle.handler;
                if (drawHandler && drawHandler._shape) {
                    const dim = getDimensions(drawHandler._shape);
                    const sizeText = `${dim.width.toFixed(2)} km x ${dim.height.toFixed(2)} km`;
                    if (statusText) statusText.innerText = `Selecting: ${sizeText}`;
                    if (aoiStatus) aoiStatus.innerText = `Selecting: ${sizeText}`;
                }
            };
            
            map.on('mousemove', moveHandler);
            map.once(L.Draw.Event.DRAWSTOP, () => map.off('mousemove', moveHandler));
        }
    });

    map.on(L.Draw.Event.CREATED, function (event) {
        drawnItems.clearLayers();
        const layer = event.layer;
        drawnItems.addLayer(layer);
        
        if (layer.editing) layer.editing.enable();

        const updateSelectionData = () => {
            const bounds = layer.getBounds();
            const dim = getDimensions(layer);

            currentBbox = [
                bounds.getWest(), bounds.getSouth(), 
                bounds.getEast(), bounds.getNorth()
            ];
            if (scanBtn) scanBtn.disabled = false;
            if (saveAoiBtn) saveAoiBtn.disabled = false;
            const sizeText = `${dim.width.toFixed(2)} km x ${dim.height.toFixed(2)} km`;
            if (statusText) statusText.innerText = `Area selected: ${sizeText}`;
            if (aoiStatus) aoiStatus.innerText = `Area selected: ${sizeText}`;
        };

        updateSelectionData();
        layer.on('edit', updateSelectionData);
    });

    map.on(L.Draw.Event.DELETED, function () {
        if (drawnItems.getLayers().length === 0) {
            currentBbox = null;
            if (scanBtn) scanBtn.disabled = true;
            if (saveAoiBtn) saveAoiBtn.disabled = true;
            if (statusText) statusText.innerText = "Draw a rectangle to begin.";
            if (aoiStatus) aoiStatus.innerText = "Draw a rectangle to begin.";
        }
    });
}

function updateTabsVisibility() {
    if (!map) return;
    const mapSize = map.getSize();
    const mapArea = mapSize.x * mapSize.y;

    const toggleTab = (marker, bounds, isParentVisible, group) => {
        if (!marker) return;
        let shouldShow = false;
        if (isParentVisible) {
            const nw = map.latLngToContainerPoint(bounds.getNorthWest());
            const se = map.latLngToContainerPoint(bounds.getSouthEast());
            const area = Math.abs(se.x - nw.x) * Math.abs(se.y - nw.y);
            shouldShow = area >= mapArea * CONFIG.TAB_VISIBILITY_AREA_THRESHOLD;
        }
        
        if (shouldShow) {
            if (!group.hasLayer(marker)) group.addLayer(marker);
        } else {
            if (group.hasLayer(marker)) group.removeLayer(marker);
        }
    };

    if (typeof activeLayers !== 'undefined') {
        activeLayers.forEach(layerObj => {
            const isSarVisible = map.hasLayer(layerObj.leafletLayer);
            toggleTab(layerObj.tabMarker, layerObj.bounds, isSarVisible, layerObj.leafletLayer);

            if (layerObj.detectLayer && layerObj.cvTabs) {
                const isCvVisible = map.hasLayer(layerObj.detectLayer);
                layerObj.cvTabs.forEach(cvTab => {
                    toggleTab(cvTab.marker, cvTab.bounds, isCvVisible, layerObj.detectLayer);
                });
            }
        });
    }

    if (typeof aoiMapLayers !== 'undefined') {
        aoiMapLayers.forEach(layerObj => {
            const isAoiVisible = map.hasLayer(layerObj.leafletLayer);
            toggleTab(layerObj.tabMarker, layerObj.bounds, isAoiVisible, layerObj.leafletLayer);
        });
    }
}

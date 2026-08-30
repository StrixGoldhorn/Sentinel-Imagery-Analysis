const CONFIG = {
    MAP_DEFAULT_LAT: 1.290270,
    MAP_DEFAULT_LNG: 103.851959,
    MAP_DEFAULT_ZOOM: 10,

    SEARCH_ZOOM_LEVEL: 13,
    SEARCH_MIN_CHARS: 3,
    SEARCH_DEBOUNCE_MS: 400,
    SEARCH_RESULT_LIMIT: 5,

    METADATA_UPDATE_DEBOUNCE_MS: 1000,

    TAB_VISIBILITY_AREA_THRESHOLD: 0.005,
    SAR_DEFAULT_OPACITY: 1,
    CV_DEFAULT_THRESHOLD: 40,
    TOOLTIP_OFFSET: [0, 0],

    OSM_TILE_URL: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    OPENSEAMAP_TILE_URL: 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png',
    NOMINATIM_SEARCH_URL: 'https://nominatim.openstreetmap.org/search',

    COLOR_SAR_OUTLINE: '#3498db',
    COLOR_CV_DETECTION: '#ff3333',
    COLOR_OBB_DETECTION: '#e67e22',
    COLOR_AOI_OUTLINE: '#28a745',
    COLOR_DRAW_RECTANGLE: '#007bff',

    API_UPDATE_METADATA: '/api/update_metadata',
    API_RUN_CV: '/api/run_cv',
    API_SCAN: '/scan',
    API_ASYNC_SCAN: '/api/tasks/scan',
    API_TASK_STATUS: '/api/tasks',
    API_GET_SCAN: '/api/scan',
    API_AOI: '/api/aoi',
    API_AIS_VESSELS: '/api/ais/vessels',
    NOTIFICATION_DURATION_MS: 3000
};
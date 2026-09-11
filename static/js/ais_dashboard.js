(() => {
    const state = { page: 1, pageSize: 50, vessels: [], totalCount: 0, aoiBboxes: new Map(), selectedId: null, sortKey: 'timestamp', sortDirection: -1 };
    const map = L.map('aisExplorerMap', { zoomControl: true }).setView([1.3, 103.8], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    const markerLayer = L.layerGroup().addTo(map);

    const $ = (id) => document.getElementById(id);
    const esc = (value) => (typeof escapeHtml === 'function' ? escapeHtml(String(value ?? '')) : String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
    const timeText = (value) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(String(value).replace(' ', 'T'))) : '—';
    const numberText = (value, digits = 1) => value === null || value === undefined || Number.isNaN(Number(value)) ? '—' : Number(value).toFixed(digits);

    function setCustomVisibility() {
        const visible = $('aisWindow').value === 'custom';
        $('aisCustomStartWrap').hidden = !visible;
        $('aisCustomEndWrap').hidden = !visible;
    }

    function buildParams(includePaging = true) {
        const params = new URLSearchParams();
        const search = $('aisSearch').value.trim();
        const type = $('aisType').value;
        const source = $('aisSource').value.trim();
        const aoiId = $('aisAoi').value;
        const window = $('aisWindow').value;
        if (search) params.set('q', search);
        if (type) params.set('vessel_type', type);
        if (source) params.set('source_plugin', source);
        if (aoiId) params.set('aoi_id', aoiId);
        if (aoiId && state.aoiBboxes.has(aoiId)) params.set('bbox', state.aoiBboxes.get(aoiId).join(','));
        params.set('latest_only', $('aisMode').value);
        if (window === 'all') {
            params.set('all_time', 'true');
        } else if (window === 'custom') {
            if ($('aisCustomStart').value) params.set('start', new Date($('aisCustomStart').value).toISOString());
            if ($('aisCustomEnd').value) params.set('end', new Date($('aisCustomEnd').value).toISOString());
        } else {
            params.set('within_hours', window === '12h' ? '12' : window === '24h' ? '24' : window === '3d' ? '72' : '168');
        }
        if (includePaging) {
            params.set('page', String(state.page));
            params.set('page_size', String(state.pageSize));
        }
        return params;
    }

    function syncUrl() {
        const url = new URL(location.href);
        url.search = buildParams(true).toString();
        history.replaceState({}, '', url);
    }

    function restoreFromUrl() {
        const p = new URLSearchParams(location.search);
        $('aisSearch').value = p.get('q') || '';
        $('aisType').value = p.get('vessel_type') || '';
        $('aisSource').value = p.get('source_plugin') || '';
        $('aisMode').value = p.get('latest_only') || 'true';
        if (p.get('all_time') === 'true') $('aisWindow').value = 'all';
        else if (p.get('start') || p.get('end')) {
            $('aisWindow').value = 'custom';
            if (p.get('start')) $('aisCustomStart').value = new Date(p.get('start')).toISOString().slice(0, 16);
            if (p.get('end')) $('aisCustomEnd').value = new Date(p.get('end')).toISOString().slice(0, 16);
        } else {
            const hours = Number(p.get('within_hours') || 12);
            $('aisWindow').value = hours === 24 ? '24h' : hours === 72 ? '3d' : hours === 168 ? '7d' : '12h';
        }
        state.page = Math.max(1, Number(p.get('page') || 1));
        setCustomVisibility();
    }

    async function loadAOIs() {
        try {
            const response = await fetch('/api/aoi');
            if (!response.ok) return;
            const aois = await response.json();
            for (const aoi of aois) {
                if (!aoi || !aoi.id || !Array.isArray(aoi.bbox)) continue;
                state.aoiBboxes.set(String(aoi.id), aoi.bbox);
                const option = document.createElement('option');
                option.value = aoi.id;
                option.textContent = aoi.name || `AOI #${aoi.id}`;
                $('aisAoi').appendChild(option);
            }
            const current = new URLSearchParams(location.search).get('aoi_id');
            if (current) $('aisAoi').value = current;
        } catch (error) { console.warn('Unable to load AOIs', error); }
    }

    function freshnessLabel(seconds, band) {
        if (band === 'NO_DATA' || seconds === null || seconds === undefined) return 'No data';
        const minutes = Math.floor(Number(seconds) / 60);
        return minutes < 60 ? `${minutes}m ago` : `${Math.floor(minutes / 60)}h ${minutes % 60}m ago`;
    }

    function renderSummary(summary) {
        $('aisMetricVessels').textContent = Number(summary.vessel_count || 0).toLocaleString();
        $('aisMetricReports').textContent = Number(summary.record_count || 0).toLocaleString();
        $('aisMetricLatest').textContent = timeText(summary.latest_report_at);
        $('aisMetricFreshness').textContent = freshnessLabel(summary.freshness_seconds, summary.freshness_band);
        $('aisMetricFreshness').className = `ais-status-badge ${(summary.freshness_band || 'NO_DATA').toLowerCase().replace('_', '-')}`;
    }

    function renderTable() {
        const body = $('aisVesselTableBody');
        if (!state.vessels.length) {
            body.innerHTML = '<tr><td colspan="8" class="ais-table-state">No AIS reports match the selected filters.</td></tr>';
        } else {
            const sortedVessels = [...state.vessels].sort((a, b) => {
                const left = a[state.sortKey] ?? '';
                const right = b[state.sortKey] ?? '';
                const leftValue = state.sortKey === 'timestamp' ? new Date(left || 0).getTime() : (typeof left === 'number' ? left : String(left).toLowerCase());
                const rightValue = state.sortKey === 'timestamp' ? new Date(right || 0).getTime() : (typeof right === 'number' ? right : String(right).toLowerCase());
                return (leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0) * state.sortDirection;
            });
            const detailQuery = buildParams(false).toString();
            body.innerHTML = sortedVessels.map(v => `<tr data-vessel-id="${v.vessel_id}" class="${v.vessel_id === state.selectedId ? 'ais-selected-row' : ''}">
                <td><span class="ais-vessel-name" title="${esc(v.name)}">${esc(v.name || `MMSI: ${v.mmsi}`)}</span><small class="ais-muted">${esc(v.callsign || 'No callsign')}</small></td>
                <td>${esc(v.mmsi || '—')}<br><small class="ais-muted">IMO ${esc(v.imo || '—')}</small></td>
                <td><span class="ais-type-badge" style="background-color: ${getVesselColor(v.type)}; color: ${getReadableTextColor(getVesselColor(v.type))};">${esc(v.type || 'Unspecified')}</span></td>
                <td>${timeText(v.timestamp)}</td>
                <td>${numberText(v.latitude, 4)}, ${numberText(v.longitude, 4)}</td>
                <td>${numberText(v.speed)} kn<br><small class="ais-muted">${numberText(v.heading, 0)}°</small></td>
                <td>${esc(v.source_plugin || 'Unknown')}</td>
                <td><a class="btn btn-sm btn-light" href="/ais/vessels/${v.vessel_id}${detailQuery ? `?${detailQuery}` : ''}" onclick="event.stopPropagation()">View</a></td>
            </tr>`).join('');
            body.querySelectorAll('tr[data-vessel-id]').forEach(row => row.addEventListener('click', () => selectVessel(Number(row.dataset.vesselId))));
        }
        const totalPages = Math.max(1, Math.ceil(state.totalCount / state.pageSize));
        $('aisPageLabel').textContent = `Page ${state.page} of ${totalPages}`;
        $('aisPrevPage').disabled = state.page <= 1;
        $('aisNextPage').disabled = state.page >= totalPages;
        $('aisTableSemantics').textContent = $('aisMode').value === 'true' ? 'Latest stored position' : 'Historical reports';
    }

    function renderMap() {
        markerLayer.clearLayers();
        const latest = new Map();
        for (const vessel of state.vessels) {
            if (vessel.latitude === null || vessel.longitude === null) continue;
            const current = latest.get(vessel.mmsi);
            if (!current || new Date(vessel.timestamp || 0) > new Date(current.timestamp || 0)) latest.set(vessel.mmsi, vessel);
        }
        latest.forEach(v => {
            const color = typeof getVesselColor === 'function' ? getVesselColor(v.type) : '#0284c7';
            const marker = L.circleMarker([v.latitude, v.longitude], { radius: 7, color: '#fff', weight: 2, fillColor: color, fillOpacity: .9 });
            marker.bindTooltip(`${esc(v.name || `MMSI: ${v.mmsi}`)} · ${numberText(v.speed)} kn`);
            marker.on('click', () => selectVessel(v.vessel_id));
            markerLayer.addLayer(marker);
        });
        const coordinates = Array.from(latest.values())
            .filter(v => v.latitude !== null && v.longitude !== null)
            .map(v => [v.latitude, v.longitude]);
        if (coordinates.length > 1) map.fitBounds(L.latLngBounds(coordinates), { padding: [24, 24], maxZoom: 10 });
        else if (coordinates.length === 1) map.setView(coordinates[0], Math.max(map.getZoom(), 9));
        $('aisMapCount').textContent = `${latest.size}`;
    }

    function selectVessel(id) {
        state.selectedId = id;
        const vessel = state.vessels.find(v => Number(v.vessel_id) === Number(id));
        if (vessel && vessel.latitude !== null && vessel.longitude !== null) map.setView([vessel.latitude, vessel.longitude], Math.max(map.getZoom(), 10));
        renderTable();
    }

    async function loadData() {
        const status = $('aisFilterStatus');
        status.textContent = 'Loading stored AIS data…';
        syncUrl();
        const params = buildParams(true);
        try {
            const [vesselResponse, summaryResponse] = await Promise.all([
                fetch(`/api/ais/vessels?${params}`),
                fetch(`/api/ais/summary?${buildParams(false)}`),
            ]);
            const vesselData = await vesselResponse.json();
            const summaryData = await summaryResponse.json();
            if (!vesselResponse.ok || vesselData.status !== 'success') throw new Error(vesselData.error || 'Unable to load AIS vessels');
            state.vessels = vesselData.vessels || [];
            state.totalCount = Number(vesselData.total_count ?? vesselData.count ?? state.vessels.length);
            renderTable(); renderMap();
            if (summaryResponse.ok && summaryData.status === 'success') renderSummary(summaryData);
            $('aisGeneratedAt').textContent = `Generated ${timeText(vesselData.generated_at)}`;
            status.textContent = `${state.totalCount.toLocaleString()} matching report${state.totalCount === 1 ? '' : 's'}`;
        } catch (error) {
            state.vessels = []; state.totalCount = 0; renderTable(); renderMap();
            status.textContent = error.message || 'Unable to load AIS data';
        }
    }

    function exportData(format) {
        const params = buildParams(false);
        params.set('format', format);
        window.location.href = `/api/ais/export?${params}`;
    }

    $('aisExplorerFilters').addEventListener('submit', event => { event.preventDefault(); state.page = 1; loadData(); });
    $('aisWindow').addEventListener('change', setCustomVisibility);
    $('aisClearFilters').addEventListener('click', () => { $('aisExplorerFilters').reset(); $('aisMode').value = 'true'; state.page = 1; setCustomVisibility(); loadData(); });
    $('aisRefreshButton').addEventListener('click', loadData);
    $('aisCsvExport').addEventListener('click', () => exportData('csv'));
    $('aisGeoJsonExport').addEventListener('click', () => exportData('geojson'));
    $('aisPrevPage').addEventListener('click', () => { if (state.page > 1) { state.page--; loadData(); } });
    $('aisNextPage').addEventListener('click', () => { if (state.page < Math.ceil(state.totalCount / state.pageSize)) { state.page++; loadData(); } });
    document.querySelectorAll('.ais-data-table thead th[data-sort]').forEach(header => header.addEventListener('click', () => {
        const key = header.dataset.sort;
        state.sortDirection = state.sortKey === key ? state.sortDirection * -1 : (key === 'timestamp' ? -1 : 1);
        state.sortKey = key;
        renderTable();
    }));
    document.addEventListener('ais:vessel-colors-changed', () => {
        renderTable();
        renderMap();
    });

    restoreFromUrl();
    loadAOIs().finally(loadData);
})();

(() => {
    const shell = document.querySelector('[data-vessel-id]');
    const vesselId = shell.dataset.vesselId;
    const $ = id => document.getElementById(id);
    const esc = value => typeof escapeHtml === 'function' ? escapeHtml(String(value ?? '')) : String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const timeText = value => value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(String(value).replace(' ', 'T'))) : '—';
    const numberText = (value, digits = 1) => value === null || value === undefined || Number.isNaN(Number(value)) ? '—' : Number(value).toFixed(digits);
    const map = L.map('vesselTrackMap').setView([1.3, 103.8], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    const layer = L.layerGroup().addTo(map);
    let currentVessel = null;

    const explorerUrl = new URL('/ais', location.origin);
    explorerUrl.search = location.search;
    $('vesselBackLink').href = explorerUrl.pathname + explorerUrl.search;

    const inheritedParams = new URLSearchParams(location.search);
    if (inheritedParams.get('all_time') === 'true') $('vesselHistoryWindow').value = 'all';
    else if (inheritedParams.get('within_hours') === '24') $('vesselHistoryWindow').value = '24h';
    else if (inheritedParams.get('within_hours') === '72') $('vesselHistoryWindow').value = '3d';
    else if (inheritedParams.get('within_hours') === '168') $('vesselHistoryWindow').value = '7d';

    function historyParams() {
        const window = $('vesselHistoryWindow').value;
        const params = new URLSearchParams();
        if (window === 'all') params.set('all_time', 'true');
        else params.set('within_hours', window === '12h' ? '12' : window === '24h' ? '24' : window === '3d' ? '72' : '168');
        params.set('limit', '10000');
        return params;
    }

    function renderIdentity(vessel) {
        currentVessel = vessel;
        $('vesselTitle').textContent = vessel.name || `MMSI: ${vessel.mmsi}`;
        $('vesselSubtitle').textContent = `${vessel.type || 'Unspecified'} · Last report ${timeText(vessel.timestamp)}`;
        const fields = [['MMSI', vessel.mmsi], ['IMO', vessel.imo], ['Callsign', vessel.callsign], ['Type', vessel.type], ['Created', timeText(vessel.created_at)]];
        $('vesselIdentity').innerHTML = fields.map(([label, value]) => `<div class="ais-identity-item"><span>${label}</span><strong>${esc(value || '—')}</strong></div>`).join('');
        $('editVesselName').value = vessel.vessel_name || (vessel.name && !String(vessel.name).startsWith('MMSI:') ? vessel.name : '') || '';
        $('editVesselType').value = vessel.vessel_type || vessel.type || '';
        $('editVesselCallsign').value = vessel.callsign || '';
        $('editVesselImo').value = vessel.imo && !String(vessel.imo).startsWith('UNKNOWN-') ? vessel.imo : '';
        const latest = [['Position', vessel.latitude === null ? '—' : `${numberText(vessel.latitude, 5)}, ${numberText(vessel.longitude, 5)}`], ['Speed', `${numberText(vessel.speed)} kn`], ['Heading', `${numberText(vessel.heading, 0)}°`], ['Reported', timeText(vessel.timestamp)], ['Source', vessel.source_plugin || '—']];
        $('vesselLatestTelemetry').innerHTML = latest.map(([label, value]) => `<div class="ais-telemetry-item"><span>${label}</span><strong>${esc(value)}</strong></div>`).join('');
    }

    function renderTrend(id, values, color = '#0284c7') {
        const svg = $(id);
        if (!values.length) { svg.innerHTML = '<text x="12" y="54" class="ais-trend-empty">No trend data</text>'; return; }
        const nums = values.map(Number).filter(Number.isFinite);
        if (!nums.length) { svg.innerHTML = '<text x="12" y="54" class="ais-trend-empty">No numeric data</text>'; return; }
        const min = Math.min(...nums), max = Math.max(...nums), range = max - min || 1;
        const points = values.map((value, index) => {
            const numeric = Number(value);
            if (!Number.isFinite(numeric)) return null;
            const x = values.length === 1 ? 160 : 8 + index * (304 / (values.length - 1));
            const y = 88 - ((numeric - min) / range) * 76;
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).filter(Boolean).join(' ');
        svg.innerHTML = `<line x1="8" y1="88" x2="312" y2="88" stroke="#dbe3ec"/><polyline points="${points}" style="stroke:${color}"/><text x="8" y="98">${numberText(min)}</text><text x="275" y="98">${numberText(max)}</text>`;
    }

    function renderHistory(locations) {
        const newestFirst = locations || [];
        const oldestFirst = [...newestFirst].reverse();
        $('vesselReportCount').textContent = `${newestFirst.length.toLocaleString()} report${newestFirst.length === 1 ? '' : 's'}`;
        $('vesselHistoryBody').innerHTML = newestFirst.length ? newestFirst.map(location => `<tr><td>${timeText(location.timestamp)}</td><td>${numberText(location.latitude, 5)}, ${numberText(location.longitude, 5)}</td><td>${numberText(location.speed)} kn</td><td>${numberText(location.heading, 0)}°</td><td>${esc(location.source_plugin || 'Unknown')}</td></tr>`).join('') : '<tr><td colspan="5" class="ais-table-state">No reports in this time window.</td></tr>';
        layer.clearLayers();
        const valid = oldestFirst.filter(l => Number.isFinite(Number(l.latitude)) && Number.isFinite(Number(l.longitude)));
        if (valid.length) {
            const latLngs = valid.map(l => [l.latitude, l.longitude]);
            L.polyline(latLngs, { color: '#0284c7', weight: 3, opacity: .8 }).addTo(layer);
            const latest = valid[valid.length - 1];
            L.circleMarker([latest.latitude, latest.longitude], { radius: 8, color: '#fff', weight: 2, fillColor: '#0284c7', fillOpacity: 1 }).bindTooltip('Latest stored position').addTo(layer);
            map.fitBounds(L.latLngBounds(latLngs), { padding: [28, 28], maxZoom: 12 });
        }
        const speeds = oldestFirst.map(l => l.speed).filter(v => v !== null && v !== undefined);
        const headings = oldestFirst.map(l => l.heading).filter(v => v !== null && v !== undefined);
        $('vesselSpeedSummary').textContent = speeds.length ? `${numberText(Math.min(...speeds))}–${numberText(Math.max(...speeds))} kn` : 'No data';
        $('vesselHeadingSummary').textContent = headings.length ? `${numberText(headings[0], 0)}° → ${numberText(headings[headings.length - 1], 0)}°` : 'No data';
        renderTrend('vesselSpeedTrend', speeds);
        renderTrend('vesselHeadingTrend', headings, '#7c3aed');
    }

    async function loadVessel() {
        $('vesselTrackWindow').textContent = $('vesselHistoryWindow').selectedOptions[0].textContent;
        try {
            const [vesselResponse, historyResponse] = await Promise.all([
                fetch(`/api/ais/vessels/${vesselId}`),
                fetch(`/api/ais/vessels/${vesselId}/history?${historyParams()}`),
            ]);
            const vesselData = await vesselResponse.json();
            const historyData = await historyResponse.json();
            if (!vesselResponse.ok || vesselData.status !== 'success') throw new Error(vesselData.error || 'Vessel not found');
            renderIdentity(vesselData.vessel);
            renderHistory(historyResponse.ok && historyData.status === 'success' ? historyData.locations : []);
        } catch (error) {
            $('vesselTitle').textContent = 'Unable to load vessel';
            $('vesselSubtitle').textContent = error.message;
            $('vesselHistoryBody').innerHTML = `<tr><td colspan="5" class="ais-table-state">${esc(error.message)}</td></tr>`;
        }
    }

    $('vesselHistoryWindow').addEventListener('change', loadVessel);
    $('vesselRefreshButton').addEventListener('click', loadVessel);
    $('vesselEditForm').addEventListener('submit', async event => {
        event.preventDefault();
        const button = event.target.querySelector('button[type="submit"]');
        button.disabled = true; button.textContent = 'Saving…';
        try {
            const response = await fetch(`/api/ais/vessels/${vesselId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: $('editVesselName').value.trim() || null, vessel_type: $('editVesselType').value.trim() || null, callsign: $('editVesselCallsign').value.trim() || null, imo: $('editVesselImo').value.trim() || null }) });
            const data = await response.json();
            if (!response.ok || data.status !== 'success') throw new Error(data.error || 'Unable to update vessel');
            renderIdentity(data.vessel);
        } catch (error) { alert(error.message); }
        finally { button.disabled = false; button.textContent = 'Save metadata'; }
    });
    loadVessel();
})();

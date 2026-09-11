(() => {
    const $ = id => document.getElementById(id);
    const timeText = value => value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(String(value).replace(' ', 'T'))) : '—';
    const esc = value => typeof escapeHtml === 'function' ? escapeHtml(String(value ?? '')) : String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const freshnessLabel = (seconds, band) => band === 'NO_DATA' || seconds === null || seconds === undefined ? 'No data' : Number(seconds) < 3600 ? `${Math.floor(Number(seconds) / 60)}m ago` : `${Math.floor(Number(seconds) / 3600)}h ago`;
    const statusClass = value => String(value || 'NO_DATA').toLowerCase().replace('_', '-');

    function renderCoverage(coverage) {
        const band = coverage.freshness_band || 'NO_DATA';
        $('activityFreshness').textContent = freshnessLabel(coverage.freshness_seconds, band);
        $('activityFreshness').className = `ais-status-badge ${statusClass(band)}`;
        $('activityReports').textContent = Number(coverage.record_count || 0).toLocaleString();
        const bySource = coverage.by_source || {};
        const entries = Object.entries(bySource).sort((a, b) => b[1] - a[1]);
        const max = Math.max(1, ...entries.map(entry => Number(entry[1])));
        $('activityCoverage').innerHTML = entries.length ? entries.map(([source, count]) => `<div class="ais-coverage-row"><div><strong>${esc(source)}</strong><small>${Number(count).toLocaleString()} reports</small></div><div class="ais-coverage-bar"><i style="width:${Math.round(Number(count) / max * 100)}%"></i></div></div>`).join('') : '<div class="ais-table-state">No reports in the last 12 hours.</div>';
        renderChart(coverage.time_buckets || []);
    }

    function renderChart(buckets) {
        const svg = $('activityCoverageChart');
        if (!buckets.length) { svg.innerHTML = '<text x="16" y="90">No coverage data in this window</text>'; return; }
        const max = Math.max(1, ...buckets.map(bucket => Number(bucket.count || 0)));
        const points = buckets.map((bucket, index) => `${8 + index * (624 / Math.max(1, buckets.length - 1))},${156 - Number(bucket.count || 0) / max * 132}`).join(' ');
        const first = timeText(buckets[0].timestamp), last = timeText(buckets[buckets.length - 1].timestamp);
        svg.innerHTML = `<line x1="8" y1="156" x2="632" y2="156"/><polyline points="${points}"/><text x="8" y="174">${esc(first)}</text><text x="520" y="174">${esc(last)}</text><text x="8" y="16">${max.toLocaleString()} reports/hour</text>`;
    }

    function renderScrapers(scrapers) {
        $('activitySources').textContent = scrapers.length.toLocaleString();
        $('activityScrapers').innerHTML = scrapers.length ? scrapers.map(scraper => {
            const state = scraper.operational_state || (scraper.enabled ? 'READY' : 'DISABLED');
            return `<div class="ais-source-row"><div><strong>${esc(scraper.display_name || scraper.name)}</strong><small>${esc(state)} · Last run ${timeText(scraper.last_run_at)}</small></div><div><span class="ais-status-badge ${statusClass(state)}">${esc(state)}</span><small>${scraper.success_rate === null || scraper.success_rate === undefined ? 'No runs' : `${Number(scraper.success_rate).toFixed(1)}% success`}</small></div></div>`;
        }).join('') : '<div class="ais-table-state">No configured scraper sources.</div>';
    }

    async function loadActivity() {
        try {
            const response = await fetch('/api/ais/activity');
            const data = await response.json();
            if (!response.ok || data.status !== 'success') throw new Error(data.error || 'Unable to load AIS activity');
            renderCoverage(data.coverage || {});
            renderScrapers(data.scrapers || []);
            const rate = data.metrics && data.metrics.overall_success_rate;
            $('activitySuccessRate').textContent = rate === null || rate === undefined ? '—' : `${Number(rate).toFixed(1)}%`;
        } catch (error) {
            $('activityScrapers').innerHTML = `<div class="ais-table-state">${esc(error.message)}</div>`;
            $('activityCoverage').innerHTML = `<div class="ais-table-state">${esc(error.message)}</div>`;
        }
    }
    $('aisActivityRefresh').addEventListener('click', loadActivity);
    loadActivity();
})();

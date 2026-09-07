/**
 * JavaScript controller for Global Platform Settings.
 */

let currentSection = 'cv';
let currentSettings = {};

function initNavTabs() {
    document.querySelectorAll('.nav-tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const sec = btn.dataset.section || btn.getAttribute('data-section');
            if (sec) {
                switchSection(sec);
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initNavTabs();
    loadSettings();
});

function switchSection(sectionId) {
    if (!sectionId) return;
    currentSection = sectionId;

    // Update nav tab buttons
    document.querySelectorAll('.nav-tab-btn').forEach(btn => {
        const isMatch = (btn.dataset.section === sectionId || btn.getAttribute('data-section') === sectionId);
        btn.classList.toggle('active', isMatch);
    });

    // Update sections
    document.querySelectorAll('.settings-section').forEach(sec => {
        sec.classList.toggle('active', sec.id === `section-${sectionId}`);
    });
}

async function loadSettings() {
    const loading = document.getElementById('settingsLoadingIndicator');
    const form = document.getElementById('settingsForm');

    if (loading) loading.style.display = 'flex';
    if (form) form.style.opacity = '0.4';

    try {
        const res = await fetch('/api/settings?definitions=true');
        const data = await res.json();

        if (data.status === 'success' && data.settings) {
            currentSettings = data.settings;
            populateForm(data.settings);
        } else {
            showToast(data.error || 'Failed to load settings', false);
        }
    } catch (err) {
        console.error('Failed to load settings:', err);
        showToast('Error connecting to settings API', false);
    } finally {
        if (loading) loading.style.display = 'none';
        if (form) form.style.opacity = '1';
    }
}

function populateForm(sections) {
    for (const [sectionKey, fields] of Object.entries(sections)) {
        for (const [fieldKey, fieldDef] of Object.entries(fields)) {
            if (sectionKey === 'scheduler' && fieldKey === 'enabled_satellites') {
                const rawVal = fieldDef.value;
                const checkedSats = Array.isArray(rawVal)
                    ? rawVal
                    : (typeof rawVal === 'string' ? rawVal.split(',').map(s => s.trim()) : ['Sentinel-1A', 'Sentinel-1C']);
                ['Sentinel-1A', 'Sentinel-1B', 'Sentinel-1C', 'Sentinel-1D'].forEach(sat => {
                    const chk = document.getElementById(`sat_${sat}`);
                    if (chk) chk.checked = checkedSats.includes(sat);
                });
                continue;
            }

            const inputId = `input_${sectionKey}_${fieldKey}`;
            const elem = document.getElementById(inputId);
            if (!elem) continue;

            const val = fieldDef.value;

            if (elem.type === 'checkbox') {
                elem.checked = Boolean(val);
            } else if (elem.type === 'range') {
                elem.value = val;
                const syncElem = document.getElementById(`sync_${sectionKey}_${fieldKey}`);
                if (syncElem) syncElem.value = val;
                const unit = fieldKey.includes('pixel') ? ' px' : '';
                updateRangeDisplay(`${sectionKey}_${fieldKey}`, val, unit);
            } else if (elem.type === 'color') {
                elem.value = val;
                const hexElem = document.getElementById(`hex_${sectionKey}_${fieldKey}`);
                if (hexElem) hexElem.value = val;
            } else {
                elem.value = (val !== null && val !== undefined) ? val : '';
            }
        }
    }
}

function updateRangeDisplay(fieldKey, value, unit) {
    const badge = document.getElementById(`badge_${fieldKey}`);
    if (badge) {
        badge.innerText = `${value}${unit || ''}`;
    }
    const sync = document.getElementById(`sync_${fieldKey}`);
    if (sync && sync.value != value) {
        sync.value = value;
    }
}

function syncSlider(fieldKey, value) {
    const slider = document.getElementById(`input_${fieldKey}`);
    if (slider) {
        slider.value = value;
    }
    const unit = fieldKey.includes('pixel') ? ' px' : '';
    const badge = document.getElementById(`badge_${fieldKey}`);
    if (badge) {
        badge.innerText = `${value}${unit}`;
    }
}

function togglePasswordVisibility(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
}

function getInputValue(id, fallback = '') {
    const el = document.getElementById(id);
    if (!el) return fallback;
    return el.value !== undefined ? el.value : fallback;
}

function getInt(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    const v = parseInt(el.value, 10);
    return isNaN(v) ? fallback : v;
}

function getFloat(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    const v = parseFloat(el.value);
    return isNaN(v) ? fallback : v;
}

function getString(id, fallback = '') {
    const el = document.getElementById(id);
    if (!el) return fallback;
    return typeof el.value === 'string' ? el.value.trim() : fallback;
}

function getBool(id, fallback = false) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    return Boolean(el.checked);
}

function collectFormData() {
    const payload = {
        cv: {
            coastal_buffer_pixels: getInt('input_cv_coastal_buffer_pixels', 81),
            morph_close_kernel: getInt('input_cv_morph_close_kernel', 27),
            dem_land_mask_enabled: getBool('input_cv_dem_land_mask_enabled', true),
            threshold: getInt('input_cv_threshold', 40),
            filter_type: getString('input_cv_filter_type', 'none'),
            minimum_area: getFloat('input_cv_minimum_area', 50.0),
            maximum_area: getFloat('input_cv_maximum_area', 5000.0),
            dilation_iterations: getInt('input_cv_dilation_iterations', 2),
            pixel_spacing_meters: getFloat('input_cv_pixel_spacing_meters', 10.0),
        },
        imagery: {
            default_evalscript: getString('input_imagery_default_evalscript', 'SAR'),
            search_window_days: getInt('input_imagery_search_window_days', 30),
            resolution_meters: getFloat('input_imagery_resolution_meters', 10.0),
            max_image_size: getInt('input_imagery_max_image_size', 2500),
        },
        scheduler: {
            n2yo_api_key: getString('input_scheduler_n2yo_api_key', ''),
            auto_capture_default: getBool('input_scheduler_auto_capture_default', false),
            poll_interval_seconds: getFloat('input_scheduler_poll_interval_seconds', 3600.0),
            satellite_norad_ids: getString('input_scheduler_satellite_norad_ids', '39634, 41456, 62232'),
            enabled_satellites: Array.from(document.querySelectorAll('input[name="satellite_selection"]:checked')).map(el => el.value),
        },
        map_ui: {
            default_lat: getFloat('input_map_ui_default_lat', 1.290270),
            default_lng: getFloat('input_map_ui_default_lng', 103.851959),
            default_zoom: getInt('input_map_ui_default_zoom', 10),
            sar_opacity: getFloat('input_map_ui_sar_opacity', 1.0),
            color_cv_detection: getString('input_map_ui_color_cv_detection', '#ff3333'),
            color_obb_detection: getString('input_map_ui_color_obb_detection', '#e67e22'),
        },
        system: {
            port: getInt('input_system_port', 5000),
            debug: getBool('input_system_debug', false),
            database_path: getString('input_system_database_path', 'instance/sentinel_analysis.db'),
            output_root: getString('input_system_output_root', 'scans'),
            cache_root: getString('input_system_cache_root', '.cache'),
        }
    };
    return payload;
}

async function saveAllSettings() {
    const saveBtn = document.getElementById('btnSaveSettings');
    const originalText = saveBtn ? saveBtn.innerHTML : '';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = 'Saving...';
    }

    try {
        const payload = collectFormData();
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (data.status === 'success') {
            showToast('Settings updated successfully', true);
            await loadSettings();
        } else {
            showToast(data.error || 'Failed to save settings', false);
        }
    } catch (err) {
        console.error('Save error:', err);
        showToast('Error communicating with settings server', false);
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalText;
        }
    }
}

async function resetCurrentSection() {
    if (!confirm(`Are you sure you want to reset all settings in '${currentSection.toUpperCase()}' to defaults?`)) {
        return;
    }

    const resetBtn = document.getElementById('btnResetSection');
    if (resetBtn) resetBtn.disabled = true;

    try {
        const res = await fetch('/api/settings/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ section: currentSection })
        });

        const data = await res.json();
        if (data.status === 'success') {
            showToast(data.message || `Reset ${currentSection} to defaults`, true);
            await loadSettings();
        } else {
            showToast(data.error || 'Failed to reset section', false);
        }
    } catch (err) {
        console.error('Reset error:', err);
        showToast('Error resetting section settings', false);
    } finally {
        if (resetBtn) resetBtn.disabled = false;
    }
}

function showToast(message, isSuccess = true) {
    const toast = document.getElementById('settingsToast');
    const msg = document.getElementById('toastMessage');
    if (!toast || !msg) return;

    msg.innerText = message;
    const icon = toast.querySelector('.toast-icon');
    if (icon) {
        icon.innerText = isSuccess ? '✓' : '✕';
        icon.style.background = isSuccess ? '#10b981' : '#ef4444';
    }

    toast.style.display = 'flex';
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => {
        toast.style.display = 'none';
    }, 3500);
}

// Expose handlers globally for inline event attributes
window.switchSection = switchSection;
window.saveAllSettings = saveAllSettings;
window.resetCurrentSection = resetCurrentSection;
window.updateRangeDisplay = updateRangeDisplay;
window.syncSlider = syncSlider;
window.togglePasswordVisibility = togglePasswordVisibility;

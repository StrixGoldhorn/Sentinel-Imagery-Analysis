/**
 * Translate backend ingestion outcomes without confusing transport success with
 * provider-level execution success.
 */
function getIngestionOutcomePresentation(rawOutcome, actionLabel = 'AIS ingestion') {
    const outcome = rawOutcome ? String(rawOutcome).trim().toUpperCase() : 'UNKNOWN';
    const presentations = {
        SUCCESS: { type: 'success', icon: '✅', label: 'succeeded' },
        PARTIAL: { type: 'warning', icon: '⚠️', label: 'partially succeeded' },
        SKIPPED: { type: 'info', icon: 'ℹ️', label: 'was skipped' },
        FAILED: { type: 'error', icon: '❌', label: 'failed' },
        CANCELLED: { type: 'error', icon: '⛔', label: 'was cancelled' },
        UNKNOWN: { type: 'warning', icon: '❔', label: 'completed with an unknown outcome' },
    };
    const presentation = presentations[outcome] || presentations.UNKNOWN;
    return {
        outcome,
        type: presentation.type,
        label: presentation.label,
        title: `${presentation.icon} ${actionLabel} ${presentation.label}`,
    };
}

window.getIngestionOutcomePresentation = getIngestionOutcomePresentation;

/**
 * Shared frontend time conventions.
 *
 * API timestamps are canonical UTC. User-facing timestamps are local by
 * default; callers may request a Zulu string when the operational context
 * makes that reference useful.
 */
(function () {
    function parseUtc(value) {
        if (!value) return null;
        if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
        let text = String(value).trim();
        if (!text) return null;
        if (!text.endsWith('Z') && !text.includes('+') && !text.includes('-', 10)) {
            text = text.replace(' ', 'T') + 'Z';
        }
        const date = new Date(text);
        return isNaN(date.getTime()) ? null : date;
    }

    function formatLocal(value, options = {}) {
        const date = parseUtc(value);
        return date ? date.toLocaleString(undefined, options) : null;
    }

    function formatZulu(value, options = {}) {
        const date = parseUtc(value);
        if (!date) return null;
        const year = date.getUTCFullYear();
        const month = String(date.getUTCMonth() + 1).padStart(2, '0');
        const day = String(date.getUTCDate()).padStart(2, '0');
        const hours = String(date.getUTCHours()).padStart(2, '0');
        const minutes = String(date.getUTCMinutes()).padStart(2, '0');
        const seconds = options.includeSeconds === false
            ? ''
            : `:${String(date.getUTCSeconds()).padStart(2, '0')}`;
        return `${year}-${month}-${day} ${hours}:${minutes}${seconds} UTC`;
    }

    function toLocalInputValue(value) {
        const date = parseUtc(value);
        if (!date) return '';
        const pad = number => String(number).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
            + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    function localInputToUtc(value) {
        if (!value) return null;
        const date = new Date(value);
        return isNaN(date.getTime()) ? null : date.toISOString();
    }

    window.SentinelTime = Object.freeze({
        parseUtc,
        formatLocal,
        formatZulu,
        toLocalInputValue,
        localInputToUtc,
    });
})();

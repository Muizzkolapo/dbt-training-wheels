// ============================================
// CSRF token handling
// ============================================
//
// CSRFProtect guards every blueprint, including the API, so any request that
// changes state has to carry the token Flask issued for this session. Rather
// than thread that through ~16 call sites (and rely on remembering it in new
// ones), wrap fetch once here and let every caller stay unchanged.
//
// Loaded before the other local scripts so the wrapper is in place before any
// of them can fire a request.

(function () {
    'use strict';

    const meta = document.querySelector('meta[name="csrf-token"]');
    const token = meta && meta.getAttribute('content');

    if (!token) {
        console.warn('[csrf] No csrf-token meta tag found; state-changing requests will be rejected.');
        return;
    }

    // Methods the server treats as read-only, so they need no token.
    const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);

    const originalFetch = window.fetch.bind(window);

    window.fetch = function (resource, options) {
        const opts = options || {};
        const method = (opts.method || 'GET').toUpperCase();

        if (SAFE_METHODS.has(method)) {
            return originalFetch(resource, opts);
        }

        // Only same-origin requests get the token -- never hand it to a third party.
        // Every call in this app uses a relative path, so absolute URLs are checked
        // against our own origin before the header is attached.
        const url = String(resource);
        const isAbsolute = /^[a-z][a-z0-9+.-]*:\/\//i.test(url);
        if (isAbsolute && !url.startsWith(window.location.origin)) {
            return originalFetch(resource, opts);
        }

        // Headers may arrive as a plain object, an array of pairs, or a Headers
        // instance; normalising means the caller's own headers survive.
        const headers = new Headers(opts.headers || {});
        if (!headers.has('X-CSRFToken')) {
            headers.set('X-CSRFToken', token);
        }

        return originalFetch(resource, Object.assign({}, opts, { headers: headers }));
    };
})();

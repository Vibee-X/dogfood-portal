/* Dogfood Portal — Theme Engine
   Lightweight, no dependencies. */

(function () {
    'use strict';

    var STORAGE_KEY = 'dogfood-theme';
    var DARK = 'dark';
    var LIGHT = 'light';

    /**
     * Apply theme to <html> without transition flash.
     */
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
    }

    /**
     * Get stored preference, or fall back to system preference.
     */
    function getPreferred() {
        try {
            var stored = localStorage.getItem(STORAGE_KEY);
            if (stored === LIGHT || stored === DARK) return stored;
        } catch (_) { /* storage unavailable */ }

        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
            return LIGHT;
        }
        return DARK;
    }

    // Apply immediately (runs in <head> via inline script or early load)
    var current = getPreferred();
    applyTheme(current);

    // Once DOM is ready, wire up toggle button
    document.addEventListener('DOMContentLoaded', function () {
        // Remove the no-transitions class after a brief delay to allow
        // the initial render to complete without flash
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                document.documentElement.classList.remove('no-transitions');
            });
        });

        var btn = document.getElementById('theme-toggle');
        if (!btn) return;

        btn.addEventListener('click', function () {
            current = current === DARK ? LIGHT : DARK;
            applyTheme(current);
            try { localStorage.setItem(STORAGE_KEY, current); } catch (_) {}
        });
    });
})();

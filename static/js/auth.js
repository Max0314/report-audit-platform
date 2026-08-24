(function () {
    'use strict';

    const productionPrefix = '/neo_hardware/report-audit';

    function basePath() {
        return window.location.pathname.startsWith(productionPrefix)
            ? productionPrefix
            : '';
    }

    function url(path) {
        const normalized = path.startsWith('/') ? path : '/' + path;
        return basePath() + normalized;
    }

    function cookie(name) {
        const prefix = encodeURIComponent(name) + '=';
        for (const part of document.cookie.split(';')) {
            const value = part.trim();
            if (value.startsWith(prefix)) {
                return decodeURIComponent(value.slice(prefix.length));
            }
        }
        return '';
    }

    window.ReportAudit = {
        url,
        csrfToken: function () {
            return cookie('csrf_token');
        }
    };
})();

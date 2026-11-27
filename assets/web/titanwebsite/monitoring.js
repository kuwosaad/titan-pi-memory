(function () {
    'use strict';

    // Keep local development and preview visits out of production reports.
    if (!['www.titanmemz.in', 'titanmemz.in'].includes(window.location.hostname)) return;

    function pageOnly(event) {
        var url = new URL(event.url, window.location.origin);
        url.search = '';
        url.hash = '';
        return Object.assign({}, event, { url: url.href });
    }

    window.va = window.va || function () {
        (window.vaq = window.vaq || []).push(arguments);
    };
    window.si = window.si || function () {
        (window.siq = window.siq || []).push(arguments);
    };
    window.va('beforeSend', pageOnly);
    window.si('beforeSend', pageOnly);

    ['/_vercel/insights/script.js', '/_vercel/speed-insights/script.js'].forEach(function (src) {
        var script = document.createElement('script');
        script.src = src;
        script.defer = true;
        document.head.appendChild(script);
    });
})();

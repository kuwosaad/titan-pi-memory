(() => {
    const demo = document.querySelector('#memory-demo');
    if (!demo) return;
    const terminal = document.querySelector('#agent-terminal');
    const picker = demo.querySelector('.agent-picker');
    const bar = demo.querySelector('.playback-bar');
    const play = document.querySelector('#demo-play');
    const stepButton = document.querySelector('#demo-step');
    const state = document.querySelector('#playback-state');
    const status = demo.querySelector('.demo-status');
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const buttons = [...demo.querySelectorAll('[data-agent]')];
    const memory = 'Keep SQLite so the app remains usable offline.';
    const answer = 'The current store is SQLite. I’ll add full-text search here so notes remain searchable offline.';
    const request = 'Add search to the notes screen.';
    const renderers = {
        pi: () => `
            <div class="agent-header pi-header"><strong>pi</strong><span>escape interrupt · / commands · ! bash · ctrl+o more</span></div>
            <div class="agent-transcript">
                <div class="agent-user" data-beat="1">Add search to the notes screen.</div>
                <div class="agent-thought" data-beat="2">Checking earlier context and the current code.</div>
                <div class="agent-tool" data-beat="2"><strong>titan</strong> action="query_memories" query="notes storage offline"<div class="agent-result" data-beat="3">1. ${memory} <span>[source: pi · scene: earlier session]</span></div></div>
                <div class="agent-tool" data-beat="4"><strong>read</strong> src/storage.ts<div class="agent-result">const db = new Database("notes.sqlite");</div></div>
                <div class="agent-answer" data-beat="5">${answer}</div>
            </div>
            <div class="agent-composer"><span class="agent-composer-prompt"></span><span class="composer-draft"></span><span class="agent-cursor" aria-hidden="true"></span></div>
            <div class="agent-footer"><span>~/projects/notes</span><span>TITAN READY · pi</span></div>`,
        claude: () => `
            <div class="agent-header claude-header"><span class="claude-mark" aria-hidden="true">▐▛███▜▌<br>▝▜█████▛▘<br> &nbsp;▘▘ ▝▝</span><span><strong>Claude Code</strong><br><span>~/projects/notes</span></span></div>
            <div class="agent-transcript">
                <div class="agent-user" data-beat="1">&gt; Add search to the notes screen.</div>
                <div class="agent-thought" data-beat="2">● I’ll check the earlier context first.</div>
                <div class="agent-tool" data-beat="2"><strong>● titan-memory_query_memories</strong> (query: "notes storage offline")<div class="agent-result" data-beat="3">└ ${memory} <span>· source: pi, earlier session</span></div></div>
                <div class="agent-tool" data-beat="4"><strong>● Read</strong> (src/storage.ts)<div class="agent-result">└ const db = new Database("notes.sqlite");</div></div>
                <div class="agent-answer" data-beat="5"><span class="agent-bullet">●</span> ${answer}</div>
            </div>
            <div class="agent-composer"><span class="agent-composer-prompt">&gt;</span><span class="composer-draft"></span><span class="agent-cursor" aria-hidden="true"></span></div>
            <div class="agent-footer"><span>~/projects/notes</span><span>Claude Code</span></div>`,
        codex: () => `
            <div class="agent-header codex-header"><strong>&gt;_ OpenAI Codex</strong><span>directory: ~/projects/notes</span></div>
            <div class="agent-transcript">
                <div class="agent-user" data-beat="1">› Add search to the notes screen.</div>
                <div class="agent-thought" data-beat="2">• I’ll check the earlier context first.</div>
                <div class="agent-tool" data-beat="2"><strong class="codex-call">• Calling titan-memory.query_memories</strong> ({"query":"notes storage offline"})<div class="agent-result" data-beat="3">└ ${memory} <span>· source: pi, earlier session</span></div></div>
                <div class="agent-tool" data-beat="4"><strong>• Ran</strong> sed -n '1,80p' src/storage.ts<div class="agent-result">└ const db = new Database("notes.sqlite");</div></div>
                <div class="agent-answer" data-beat="5"><span class="agent-bullet">•</span> ${answer}</div>
            </div>
            <div class="agent-composer"><span class="agent-composer-prompt">›</span><span class="composer-draft"></span><span class="agent-cursor" aria-hidden="true"></span></div>
            <div class="agent-footer"><span>? for shortcuts</span><span>~/projects/notes</span></div>`
    };
    const labels = ['composing request', 'request submitted', 'finding context', 'memory recalled', 'checking current code', 'context in use'];
    let agent = 'pi', beat = reduced.matches ? 5 : 0, playing = !reduced.matches;
    let explicitlyPaused = false, typed = 0;
    let visible = !('IntersectionObserver' in window), timer, started, remaining = 75, animations = [];
    picker.hidden = false; bar.hidden = false;
    function stopTimer() {
        if (timer !== undefined) { clearTimeout(timer); remaining = Math.max(0, remaining - (performance.now() - started)); timer = undefined; }
    }
    function cancelAnimations() { animations.forEach((animation) => animation.cancel()); animations = []; }
    function render(fresh = false, animate = false) {
        cancelAnimations();
        demo.dataset.beat = beat;
        if (fresh) {
            terminal.className = 'agent-terminal agent-' + agent;
            terminal.setAttribute('aria-label', 'Illustrative ' + ({pi:'Pi', claude:'Claude Code', codex:'Codex'}[agent]) + ' session');
            terminal.innerHTML = renderers[agent]();
        }
        terminal.querySelectorAll('[data-beat]').forEach((row) => {
            const shown = Number(row.dataset.beat) <= beat;
            row.classList.toggle('beat-pending', !shown);
            row.setAttribute('aria-hidden', String(!shown));
        });
        const codexCall = terminal.querySelector('.codex-call');
        if (codexCall) codexCall.textContent = '• ' + (beat >= 3 ? 'Called' : 'Calling') + ' titan-memory.query_memories';
        terminal.querySelector('.composer-draft').textContent = beat === 0 ? request.slice(0, typed) : (agent === 'codex' ? 'Ask Codex to do anything' : '');
        buttons.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.agent === agent)));
        state.textContent = labels[beat];
        play.hidden = reduced.matches;
        play.innerHTML = playing ? 'Ⅱ <span>pause</span>' : (beat === 5 ? '↺ <span>replay</span>' : '▶ <span>play</span>');
        play.setAttribute('aria-label', playing ? 'Pause playback' : (beat === 5 ? 'Replay playback' : 'Play playback'));
        stepButton.setAttribute('aria-disabled', String(beat === 5));
        if (animate && !reduced.matches && terminal.animate) {
            const easing = getComputedStyle(demo).getPropertyValue('--ease').trim();
            animations = [...terminal.querySelectorAll('[data-beat="' + beat + '"]')].map((row) => row.animate([{opacity:.2, transform:'translateY(4px)'}, {opacity:1, transform:'translateY(0)'}], {duration:300, easing}));
        }
    }
    function schedule() {
        stopTimer();
        if (!playing || !visible || document.hidden || reduced.matches || beat >= 5) return;
        started = performance.now();
        timer = setTimeout(() => {
            timer = undefined;
            if (beat === 0 && typed < request.length) {
                typed = Math.min(request.length, typed + 2);
                remaining = typed === request.length ? 450 : 75;
                render(); schedule(); return;
            }
            remaining = 1800; beat++; if (beat === 5) playing = false; render(false, true); schedule();
        }, remaining);
    }
    buttons.forEach((button) => button.addEventListener('click', () => {
        stopTimer(); agent = button.dataset.agent; beat = reduced.matches ? 5 : 0; typed = 0; playing = !reduced.matches && !explicitlyPaused; remaining = 75;
        render(true); schedule(); status.textContent = button.textContent.trim() + ' example selected.';
    }));
    stepButton.addEventListener('click', () => {
        if (beat === 5) return;
        stopTimer(); playing = false; explicitlyPaused = true; beat = Math.min(5, beat + 1); remaining = 1800; render(); status.textContent = labels[beat] + '.';
    });
    play.addEventListener('click', () => {
        stopTimer(); if (beat === 5) { beat = 0; typed = 0; remaining = 75; } playing = !playing; explicitlyPaused = !playing; render(); schedule();
    });
    if ('IntersectionObserver' in window) new IntersectionObserver((entries) => { visible = entries[0].isIntersecting && entries[0].intersectionRatio >= .25; if (!visible) cancelAnimations(); schedule(); }, {threshold:.25}).observe(terminal);
    document.addEventListener('visibilitychange', () => { if (document.hidden) cancelAnimations(); schedule(); });
    demo.querySelector('.evidence-strip').addEventListener('toggle', (event) => {
        if (event.currentTarget.open) { stopTimer(); playing = false; explicitlyPaused = true; render(); }
    });
    reduced.addEventListener('change', () => { stopTimer(); if (reduced.matches) { beat = 5; playing = false; } render(); schedule(); });
    render(true); schedule();

    const navLinks = [...document.querySelectorAll('.nav-links a[href^="#"]')];
    if ('IntersectionObserver' in window) {
        const navObserver = new IntersectionObserver((entries) => entries.forEach((entry) => {
            if (entry.isIntersecting) navLinks.forEach((link) => {
                if (link.getAttribute('href') === '#' + entry.target.id) link.setAttribute('aria-current', 'location'); else link.removeAttribute('aria-current');
            });
        }), {rootMargin:'-25% 0px -60% 0px'});
        navLinks.forEach((link) => { const target = document.querySelector(link.getAttribute('href')); if (target) navObserver.observe(target); });
        new IntersectionObserver((entries) => { if (entries[0].isIntersecting) navLinks.forEach((link) => link.removeAttribute('aria-current')); }, {threshold:.5}).observe(document.querySelector('.hero'));
    }
    const copy = document.querySelector('#copy-install');
    if (copy && navigator.clipboard && window.isSecureContext) {
        copy.hidden = false;
        copy.addEventListener('click', async () => {
            try { await navigator.clipboard.writeText('pi install npm:titan-pi-memory'); status.textContent = 'Pi install command copied.'; }
            catch (_) { status.textContent = 'Copy unavailable. Select the Pi install command in the terminal.'; }
        });
    }
})();

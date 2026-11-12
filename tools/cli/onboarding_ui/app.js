(() => {
  'use strict';
  const token = decodeURIComponent(location.hash.slice(1));
  const content = document.querySelector('#content');
  const actions = document.querySelector('#actions');
  const notice = document.querySelector('#notice');
  const steps = document.querySelector('#steps');
  const labels = ['Your agent', 'Extraction', 'Embeddings', 'Review'];
  let catalog, selection, page = 0, polling;
  const keys = {};
  const keyLinks = {openai: 'https://platform.openai.com/api-keys', gemini: 'https://aistudio.google.com/app/apikey', openrouter: 'https://openrouter.ai/settings/keys', opencode_go: 'https://opencode.ai/console/'};

  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path, body) {
    const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-Titan-Token': token, ...(body === undefined ? {} : {'Content-Type':'application/json'})}, ...(body === undefined ? {} : {body:JSON.stringify(body)}), cache:'no-store'});
    let result;
    try { result = await response.json(); } catch { throw new Error('The setup server returned an unexpected response.'); }
    if (!response.ok) throw new Error(result.error || (result.errors || []).join(' ') || result.message || `Setup request failed (${response.status}).`);
    return result;
  }
  function error(message) { notice.textContent = message; notice.hidden = !message; }
  function buttons(back, next, nextText = 'Continue') {
    actions.innerHTML = `${back ? '<button class="button" type="button" id="back">Back</button>' : ''}${next ? `<button class="button primary" type="button" id="next">${esc(nextText)} <span aria-hidden="true">→</span></button>` : ''}`;
    if (back) document.querySelector('#back').onclick = back;
    if (next) document.querySelector('#next').onclick = next;
  }
  function progress() {
    steps.innerHTML = labels.map((label, i) => `<li class="${i === page ? 'active' : i < page ? 'done' : ''}" ${i === page ? 'aria-current="step"' : ''}><span class="step-num">${i < page ? '✓' : String(i + 1).padStart(2,'0')}</span>${label}</li>`).join('');
  }
  function heading(kicker, title, description) {
    return `<p class="eyebrow">${esc(kicker)}</p><h2 id="panel-title" tabindex="-1">${esc(title)}</h2><p class="description">${esc(description)}</p>`;
  }
  function focusTitle() { document.querySelector('#panel-title')?.focus({preventScroll:true}); }
  function render() {
    error(''); progress();
    if (page === 0) {
      content.innerHTML = heading('01 / your agent', 'Who should remember?', 'Choose the coding agent you want to connect to Titan.') + `<div class="choices" role="radiogroup" aria-label="Coding agent">${catalog.agents.map(a => `<label class="choice"><input type="radio" name="agent" value="${esc(a.id)}" ${selection.agent === a.id ? 'checked' : ''} ${catalog.locked_agent && selection.agent !== a.id ? 'disabled' : ''}><span>${esc(a.label)}</span><span class="arrow" aria-hidden="true">↗</span></label>`).join('')}</div>`;
      document.querySelectorAll('[name=agent]').forEach(input => input.onchange = async () => {
        document.querySelectorAll('[name=agent]').forEach(item => { item.disabled = true; });
        document.querySelector('#next').disabled = true;
        try {
          const refreshed = await api('/api/state?agent=' + encodeURIComponent(input.value));
          catalog = refreshed; selection = structuredClone(refreshed.selected);
          Object.keys(keys).forEach(key => delete keys[key]);
          render();
        } catch (err) { error(err.message); document.querySelectorAll('[name=agent]').forEach(item => { item.disabled = false; }); document.querySelector('#next').disabled = false; }
      });
      buttons(null, () => { if (!selection.agent) return error('Choose an agent to continue.'); page++; render(); });
    } else if (page === 1 || page === 2) {
      const kind = page === 1 ? 'extraction' : 'embedding';
      const chosen = selection[kind];
      const choices = catalog[kind];
      const provider = choices.find(item => item.id === chosen.backend) || choices[0];
      if (!provider) { content.innerHTML = heading('setup', 'No options available', 'The setup server did not provide any options for this step.'); buttons(() => {page--;render();}, null); return; }
      chosen.backend = provider.id;
      if (!provider.models.includes(chosen.model)) chosen.model = provider.models[0] || '';
      const saved = provider.key_env && catalog.saved_keys.includes(provider.key_env);
      content.innerHTML = heading(page === 1 ? '02 / extraction' : '03 / embeddings', page === 1 ? 'Choose your brain.' : 'Find the connections.', page === 1 ? 'Choose the provider and model Titan will use to extract memories from sessions.' : 'Choose the provider and model Titan will use to make memories searchable.') +
        `<label class="field"><span>Provider</span><select id="provider">${choices.map(p => `<option value="${esc(p.id)}" ${p.id === provider.id ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></label><label class="field"><span>Model</span><select id="model">${provider.models.map(m => `<option value="${esc(m)}" ${m === chosen.model ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select></label>` +
        (provider.key_env ? `<label class="field"><span>API key</span><input id="key" type="password" autocomplete="off" spellcheck="false" placeholder="${saved ? 'Key already configured' : 'Enter your API key'}"><small>${saved ? 'A key is already configured. Leave blank to keep it, or enter a replacement.' : `Required for ${esc(provider.label)} (${esc(provider.key_env)}).`} ${keyLinks[provider.id] ? `<a href="${keyLinks[provider.id]}" target="_blank" rel="noreferrer">Get an API key ↗</a>` : ''}</small></label>` : '') +
        (provider.id === 'ollama' ? `<p class="prerequisite">Install and start <a href="https://ollama.com/download" target="_blank" rel="noreferrer">Ollama ↗</a>, then make sure the selected model is available locally before using Titan. You can download the selected model here, or use <code>ollama pull ${esc(chosen.model)}</code>.</p><button class="button pull" id="pull" type="button">Download model</button><p id="pull-status" class="pull-status" role="status" aria-live="polite"></p>` : '');
      document.querySelector('#provider').onchange = event => { chosen.backend = event.target.value; chosen.model = ''; render(); document.querySelector('#provider').focus(); };
      document.querySelector('#model').onchange = event => { chosen.model = event.target.value; if (provider.id === 'ollama') { render(); document.querySelector('#model').focus(); } };
      const pull = document.querySelector('#pull');
      if (pull) pull.onclick = () => pullModel(chosen.model);
      const key = document.querySelector('#key');
      if (key) { key.value = keys[provider.key_env] || ''; key.oninput = () => { keys[provider.key_env] = key.value; }; }
      buttons(() => {page--;render();}, () => { if (!chosen.model) return error('Choose a model to continue.'); if (provider.key_env && !saved && !keys[provider.key_env]?.trim()) return error('Enter an API key to continue.'); page++; render(); });
    } else {
      const agent = catalog.agents.find(a => a.id === selection.agent);
      const ext = catalog.extraction.find(p => p.id === selection.extraction.backend);
      const emb = catalog.embedding.find(p => p.id === selection.embedding.backend);
      content.innerHTML = heading('04 / review', 'Ready to remember.', 'Take one last look before installing Titan.') + `<dl class="review"><div><dt>Agent</dt><dd>${esc(agent?.label || selection.agent)}</dd></div><div><dt>Extraction</dt><dd>${esc(ext?.label || '')} · ${esc(selection.extraction.model)}</dd></div><div><dt>Embeddings</dt><dd>${esc(emb?.label || '')} · ${esc(selection.embedding.model)}</dd></div></dl><p class="review-note">Installing checks your selected providers with small test requests and verifies local Ollama models. Cloud requests may incur normal API charges.</p>`;
      buttons(() => {page--;render();}, install, 'Install Titan');
    }
    focusTitle();
  }
  async function pullModel(model) {
    const button = document.querySelector('#pull');
    const status = document.querySelector('#pull-status');
    button.disabled = true;
    const controls = [...document.querySelectorAll('#provider, #model, #back, #next, #cancel')];
    controls.forEach(control => { control.disabled = true; });
    const release = () => { button.disabled = false; controls.forEach(control => { control.disabled = false; }); };
    error('');
    try {
      await api('/api/pull', {model});
      async function check() {
        try {
          const result = await api('/api/status');
          status.textContent = result.message || 'Downloading model…';
          if (result.state === 'complete' || result.state === 'error') { release(); return; }
          polling = setTimeout(check, 1000);
        } catch (err) { release(); error(err.message); }
      }
      check();
    } catch (err) { release(); error(err.message); }
  }
  async function install() {
    const button = document.querySelector('#next');
    const back = document.querySelector('#back');
    const cancel = document.querySelector('#cancel');
    button.disabled = true; back.disabled = true; cancel.disabled = true;
    button.textContent = 'Checking providers…'; error('');
    const restore = () => { button.disabled = false; back.disabled = false; cancel.disabled = false; button.textContent = 'Install Titan →'; };
    const payload = {...selection, keys: Object.fromEntries(Object.entries(keys).filter(([,value]) => value.trim()))};
    try {
      const validation = await api('/api/validate', payload);
      if (!validation.ok) { error((validation.errors || ['Please check your configuration.']).join(' ')); restore(); return; }
      const result = await api('/api/install', payload);
      if (!result.ok) throw new Error(result.error || 'Could not start installation.');
      Object.keys(keys).forEach(key => delete keys[key]);
      document.querySelector('#cancel').hidden = true;
      showProgress();
    } catch (err) { error(err.message); restore(); }
  }
  function showProgress() {
    content.innerHTML = `<div class="status-mark" aria-hidden="true"><img src="titan-logo.svg" alt=""></div>${heading('installing titan', 'Making it yours.', 'The installer is running. Keep this page open until it finishes.')}<div class="progress-line" aria-hidden="true"></div><p id="status" role="status" aria-live="polite">Starting installation…</p>`;
    actions.innerHTML = ''; focusTitle(); poll();
  }
  async function poll() {
    try {
      const status = await api('/api/status');
      document.querySelector('#status').textContent = status.message || (status.state === 'running' ? 'Installing…' : status.state);
      if (status.state === 'complete') return finish(true, status.message);
      if (status.state === 'error') return finish(false, status.message);
      polling = setTimeout(poll, 1000);
    } catch (err) { finish(false, err.message); }
  }
  function finish(success, message) {
    clearTimeout(polling);
    content.innerHTML = `<div class="status-mark" aria-hidden="true">${success ? '✓' : '!'}</div>${heading(success ? 'setup complete' : 'installation stopped', success ? 'Ready to remember.' : 'Something went wrong.', message || (success ? 'Titan is installed and ready.' : 'The installation did not complete.'))}`;
    if (success) buttons(null, async () => { try { await api('/api/close', {}); } catch {} content.innerHTML = heading('all done', 'You can close this tab.', 'Return to your terminal to continue.'); actions.innerHTML = ''; focusTitle(); }, 'Done');
    else buttons(() => {page = 3; render();}, null);
    focusTitle();
  }
  document.querySelector('#cancel').onclick = async () => {
    clearTimeout(polling);
    try { await api('/api/close', {}); } catch (err) { error(err.message); return; }
    content.innerHTML = heading('setup closed', 'You can close this tab.', 'Return to your terminal to start setup again.');
    actions.innerHTML = ''; steps.innerHTML = ''; error('');
    document.querySelector('#cancel').hidden = true; focusTitle();
  };
  async function init() {
    if (!token) throw new Error('This setup link is missing its session token. Return to your terminal and open the provided link.');
    catalog = await api('/api/state'); selection = structuredClone(catalog.selected); render();
  }
  init().catch(err => { content.innerHTML = heading('setup unavailable', 'Could not open setup.', 'Return to your terminal and try again.'); error(err.message); });
})();

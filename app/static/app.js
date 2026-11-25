/* LeadDeck workspace UI. All lead data is inserted with textContent (never innerHTML). */
const LD = (() => {
  let csrf = '', me = null;
  const $ = (s, r = document) => r.querySelector(s);
  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) if (k != null) n.append(k.nodeType ? k : document.createTextNode(String(k)));
    return n;
  }
  async function api(path, opts = {}) {
    const o = { method: opts.method || 'GET', headers: { 'X-CSRF-Token': csrf }, ...opts };
    if (opts.json !== undefined) { o.body = JSON.stringify(opts.json); o.headers['Content-Type'] = 'application/json'; }
    const r = await fetch(path, o);
    if (r.status === 401 && !path.includes('/auth/')) { location.href = '/login'; throw new Error('login required'); }
    let data = null; try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
    return data;
  }
  function msg(box, text, ok) { box.replaceChildren(text ? el('div', { class: 'msg ' + (ok ? 'ok' : 'err') }, text) : ''); }
  const typeTag = t => el('span', { class: 'tag ' + ({ candidate: 'cand', employer_contact: 'emp' }[t] || 'unk') }, (t || 'unknown').replace('_', ' '));
  const fmtBytes = n => n == null ? 'n/a' : n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB';
  const when = s => s ? s.replace('T', ' ').slice(0, 16) + ' UTC' : '—';

  // ---- visual helpers (inline SVG built with createElementNS; lead data still goes in as text only)
  const NS = 'http://www.w3.org/2000/svg';
  function svg(attrs, ...kids) { const n = document.createElementNS(NS, 'svg'); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); kids.flat().forEach(k => n.append(k)); return n; }
  function sv(tag, attrs) { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; }
  const ICONS = {
    tools: [['rect', { x: 3, y: 3, width: 7, height: 7, rx: 2 }], ['rect', { x: 14, y: 3, width: 7, height: 7, rx: 2 }], ['rect', { x: 3, y: 14, width: 7, height: 7, rx: 2 }], ['rect', { x: 14, y: 14, width: 7, height: 7, rx: 2 }]],
    leads: [['circle', { cx: 9, cy: 8, r: 3.5 }], ['path', { d: 'M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6' }], ['circle', { cx: 17.5, cy: 9, r: 2.5 }], ['path', { d: 'M17 14c2.7 0 4.5 1.8 4.5 4.5' }]],
    campaigns: [['path', { d: 'M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z' }]],
    pipeline: [['rect', { x: 3, y: 4, width: 5, height: 16, rx: 1.6 }], ['rect', { x: 9.5, y: 4, width: 5, height: 10, rx: 1.6 }], ['rect', { x: 16, y: 4, width: 5, height: 13, rx: 1.6 }]],
    tasks: [['rect', { x: 4, y: 4, width: 16, height: 16, rx: 4 }], ['path', { d: 'M8 12l3 3 5-6' }]],
    analytics: [['path', { d: 'M4 20V10M10 20V4M16 20v-7M22 20H2' }]],
    sync: [['path', { d: 'M20 11a8 8 0 0 0-14.5-4M4 4v4h4M4 13a8 8 0 0 0 14.5 4M20 20v-4h-4' }]],
    search: [['circle', { cx: 11, cy: 11, r: 7 }], ['path', { d: 'm20 20-3.5-3.5' }]],
    inbox: [['path', { d: 'M3 13l3-8h12l3 8v6H3z' }], ['path', { d: 'M3 13h5l1 3h6l1-3h5' }]],
  };
  const icon = name => svg({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.9, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' }, (ICONS[name] || []).map(([t, a]) => sv(t, a)));
  const logo = () => el('div', { class: 'logo', 'aria-hidden': 'true' }, svg({ viewBox: '0 0 36 36' },
    sv('rect', { class: 'c1', x: 5, y: 5, width: 20, height: 26, rx: 5, fill: '#4a3bd6', opacity: '.65' }), sv('rect', { class: 'c2', x: 8, y: 5, width: 20, height: 26, rx: 5, fill: '#7a5cff', opacity: '.9' }),
    sv('rect', { x: 11, y: 5, width: 20, height: 26, rx: 5, fill: '#19c6d6' }), sv('path', { d: 'M17.5 22.5 25 14M18.5 14H25v6.5', fill: 'none', stroke: '#0d1230', 'stroke-width': 2.4, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })));
  const hueOf = s => { let h = 0; for (const ch of String(s || '?')) h = (h * 31 + ch.charCodeAt(0)) % 360; return h; };
  const avatarFor = (name, size) => el('span', { class: 'av' + (size ? ' ' + size : ''), style: '--h:' + hueOf(name), 'aria-hidden': 'true' }, String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?');
  const bandOf = score => score >= 50 ? 'high' : score >= 20 ? 'medium' : 'low';
  // score 0 = no email, no phone, found by one tool: say so instead of a "0" ring that looks broken
  const notEnriched = lead => lead && lead.looked_up
    ? el('span', { class: 'tag noinfo', title: 'Enrichment already looked this lead up and found no email or phone. Re-checking would spend credits again.' }, 'No contact found')
    : el('span', { class: 'tag noinfo', title: 'No email or phone yet. Enrich this lead (Campaigns → Enrich) to find contact details and a quality score.' }, 'Not enriched');
  const ringFor = (score, size) => { const band = bandOf(score || 0);
    return el('span', { class: 'ring ' + band + (size ? ' ' + size : '') + (score ? '' : ' zero'), style: '--v:' + (score || 0), title: 'Contact score ' + (score || 0) + '/100' },
      svg({ viewBox: '0 0 36 36' }, sv('circle', { class: 'bg', cx: 18, cy: 18, r: 15.9155, pathLength: 100 }), sv('circle', { class: 'fg', cx: 18, cy: 18, r: 15.9155, pathLength: 100 })), el('b', {}, String(score || 0))); };
  const stagePill = k => el('span', { class: 'stage s-' + k }, STAGE_LABEL[k] || k);
  function countUp(node, n) {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches || !Number.isFinite(n)) { node.textContent = String(n); return; }
    const t0 = performance.now(), dur = 700; node.textContent = '0';
    const tick = t => { const p = Math.min(1, (t - t0) / dur); node.textContent = Math.round(n * (1 - Math.pow(1 - p, 3))).toLocaleString(); if (p < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }
  // Shows a spinner on the button and blocks double clicks while fn runs.
  async function busy(btn, fn) {
    if (!btn || btn.classList.contains('busy')) return btn ? undefined : fn();
    btn.classList.add('busy'); btn.disabled = true;
    try { return await fn(); } finally { btn.classList.remove('busy'); btn.disabled = false; }
  }
  const tscroll = t => el('div', { class: 'tscroll' }, t);
  // Modal with a few labelled choices. Resolves with the chosen value, or null on Cancel / Escape / backdrop.
  function choose(title, text, options) {
    return new Promise(res => {
      const onKey = e => { if (e.key === 'Escape') done(null); };
      const done = v => { document.removeEventListener('keydown', onKey); back.remove(); res(v); };
      document.addEventListener('keydown', onKey);
      const btns = options.map(([v, label, cls]) => el('button', { class: cls || '', onclick: () => done(v) }, label));
      const back = el('div', { class: 'modal-back', onclick: e => e.target === back && done(null) },
        el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' }, el('h3', { style: 'margin:0 0 8px' }, title), el('div', { class: 'mut' }, text),
          el('div', { class: 'row', style: 'margin-top:16px;justify-content:flex-end' }, el('button', { class: 'ghost', onclick: () => done(null) }, 'Cancel'), ...btns)));
      document.body.append(back); btns[0].focus();
    });
  }
  const emptyState = (ico, title, hint, ...actions) => el('div', { class: 'empty big' }, el('span', { class: 'eico' }, icon(ico)), el('div', { style: 'font-weight:700;color:var(--text);font-size:17px' }, title), hint ? el('div', {}, hint) : null, actions.length ? el('div', { style: 'margin-top:12px' }, ...actions) : null);

  async function shell(active) {
    me = await api('/api/auth/me'); csrf = me.csrf;
    const links = [['/', 'Tools', 'tools'], ['/leads', 'Leads', 'leads'], ['/campaigns', 'Campaigns', 'campaigns'], ['/pipeline', 'CRM', 'pipeline'], ['/tasks', 'Tasks', 'tasks'], ['/analytics', 'Analytics', 'analytics'], ['/sync', 'Sync', 'sync']];
    const side = el('aside', {},
      el('div', { class: 'brand' }, logo(), 'LeadDeck'),
      ...links.map(([h, l, ic]) => el('a', { href: h, class: h === active ? 'active' : '', 'aria-current': h === active ? 'page' : null, 'aria-label': l }, icon(ic), el('span', {}, l))),
      el('div', { class: 'sp' }),
      el('div', { class: 'who' }, me.email + ' (' + me.role + ')'),
      el('a', { href: '#', onclick: async e => { e.preventDefault(); await api('/api/auth/logout', { method: 'POST' }); location.href = '/login'; } }, 'Sign out'));
    const main = el('main', { id: 'main' });
    document.body.replaceChildren(side, main);
    return main;
  }

  // ------------------------------------------------------------- Leads
  // Autocomplete dropdown for a text input. fetcher(q) resolves [{value,count,label}]; onPick(item); onEnter() when Enter is pressed with nothing highlighted.
  function withSuggest(input, fetcher, onPick, onEnter) {
    const list = el('div', { class: 'suggest', role: 'listbox', hidden: true });
    const wrap = el('div', { class: 'suggest-wrap' }, input, list);
    let items = [], active = -1, seq = 0, timer = null;
    input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list'); input.setAttribute('aria-expanded', 'false'); input.setAttribute('autocomplete', 'off');
    const hide = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
    const mark = () => [...list.children].forEach((c, i) => { c.classList.toggle('on', i === active); c.setAttribute('aria-selected', String(i === active)); });
    const pick = it => { hide(); onPick(it); };
    const show = () => {
      if (!items.length) return hide();
      list.replaceChildren(...items.map(it => {
        const q = input.value.trim(), at = q ? it.value.toLowerCase().indexOf(q.toLowerCase()) : -1;
        const row = el('div', { class: 'opt', role: 'option', 'aria-selected': 'false' }, it.label ? el('span', { class: 'tag' }, it.label) : null,
          el('span', { class: 'ov' }, ...(at >= 0 ? [it.value.slice(0, at), el('b', {}, it.value.slice(at, at + q.length)), it.value.slice(at + q.length)] : [it.value])), it.count != null ? el('span', { class: 'mut' }, String(it.count)) : null);
        row.addEventListener('mousedown', e => { e.preventDefault(); pick(it); });
        return row;
      }));
      list.hidden = false; input.setAttribute('aria-expanded', 'true'); active = -1;
    };
    const fetchNow = async () => {
      const my = ++seq;
      try { const r = await fetcher(input.value.trim()); if (my === seq && document.activeElement === input) { items = r; show(); } } catch (e) { hide(); }
    };
    input.addEventListener('focus', fetchNow);
    input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(fetchNow, 150); });
    input.addEventListener('blur', () => { seq++; clearTimeout(timer); setTimeout(hide, 120); });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (list.hidden) { fetchNow(); return; }
        e.preventDefault(); const n = items.length;
        active = e.key === 'ArrowDown' ? (active + 1) % n : (active <= 0 ? n - 1 : active - 1); mark();
      } else if (e.key === 'Enter') {
        if (!list.hidden && active >= 0) { e.preventDefault(); pick(items[active]); } else { hide(); onEnter && onEnter(); }
      } else if (e.key === 'Escape' && !list.hidden) { e.stopPropagation(); hide(); }
    });
    return wrap;
  }
  // Modal that asks for a view name (and whether to share it with the team). Resolves {name, shared} or null.
  function askName(title) {
    return new Promise(res => {
      const inp = el('input', { type: 'text', maxlength: 60, placeholder: 'View name', style: 'width:100%', 'aria-label': 'View name' }), share = el('input', { type: 'checkbox' });
      const onKey = e => { if (e.key === 'Escape') done(null); else if (e.key === 'Enter') submit(); };
      const done = v => { document.removeEventListener('keydown', onKey); back.remove(); res(v); };
      const submit = () => inp.value.trim() ? done({ name: inp.value.trim(), shared: share.checked }) : inp.focus();
      document.addEventListener('keydown', onKey);
      const back = el('div', { class: 'modal-back', onclick: e => e.target === back && done(null) },
        el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' }, el('h3', { style: 'margin:0 0 10px' }, title), inp,
          el('label', { class: 'mut', style: 'display:block;margin-top:10px' }, share, ' Share with the whole team'),
          el('div', { class: 'row', style: 'margin-top:12px;justify-content:flex-end' }, el('button', { class: 'ghost', onclick: () => done(null) }, 'Cancel'), el('button', { onclick: submit }, 'Save view'))));
      document.body.append(back); inp.focus();
    });
  }
  const QUALITY_LABEL = { high: 'High', medium: 'Medium', low: 'Low' };
  const TOOL_LABEL = { talentpulse: 'TalentPulse', hiringbadge: 'Hiring Badge Finder', commentminer: 'CommentMiner', jobharvest: 'JobHarvest' };

  async function leadsPage() {
    const main = await shell('/leads');
    const st = { page: 1, pageSize: 50, selected: new Set(), sort: 'last_seen', dir: 'desc', seq: 0, view: null, views: [] };
    const sel = (label, opts) => el('select', { 'aria-label': label }, ...opts.map(([v, l]) => el('option', { value: v }, l)));
    const f = {
      q: el('input', { type: 'text', placeholder: 'Search name, title, company…', 'aria-label': 'Search' }),
      title: el('input', { type: 'text', placeholder: 'Job title', 'aria-label': 'Job title' }),
      company: el('input', { type: 'text', placeholder: 'Company', 'aria-label': 'Company' }),
      location: el('input', { type: 'text', placeholder: 'Location', 'aria-label': 'Location' }),
      tool: sel('Found by', [['', 'All tools'], ...Object.entries(TOOL_LABEL)]),
      lead_type: sel('Lead type', [['', 'Any type'], ['candidate', 'candidate'], ['employer_contact', 'employer contact'], ['unknown', 'unknown']]),
      stage: sel('CRM stage', [['', 'Any stage'], ...Object.entries(STAGE_LABEL)]),
      quality: sel('Contact quality', [['', 'Any contact quality'], ['high', 'High (score 50+)'], ['medium', 'Medium (20–49)'], ['low', 'Low (under 20)']]),
      has_email: el('input', { type: 'checkbox' }), has_phone: el('input', { type: 'checkbox' }), dnc: el('input', { type: 'checkbox' }),
    };
    const TEXT = ['q', 'title', 'company', 'location'], SELECTS = ['tool', 'lead_type', 'stage', 'quality'], CHECKS = { has_email: 'true', has_phone: 'true', dnc: 'exclude' };
    const box = el('div'), tbody = el('tbody'), info = el('span', { class: 'mut' }), pager = el('div', { class: 'row' }), chips = el('div', { class: 'chips' });
    const campSel = el('select', { 'aria-label': 'Campaign' }), ownerSel = el('select', { 'aria-label': 'Assign owner' }), viewSel = el('select', { 'aria-label': 'Saved views' });
    const selCount = el('span', { class: 'mut' }), allBox = el('input', { type: 'checkbox', 'aria-label': 'Select all rows on this page' });

    const read = () => {
      const o = {};
      for (const k of [...TEXT, ...SELECTS]) if (f[k].value.trim()) o[k] = f[k].value.trim();
      for (const [k, v] of Object.entries(CHECKS)) if (f[k].checked) o[k] = v;
      return o;
    };
    const apply = o => {
      for (const k of [...TEXT, ...SELECTS]) f[k].value = o[k] || '';
      for (const [k, v] of Object.entries(CHECKS)) f[k].checked = o[k] === v || (o[k] && k !== 'dnc');
      st.sort = o.sort || 'last_seen'; st.dir = o.dir || 'desc';
    };
    const query = () => { const p = new URLSearchParams(read()); if (st.sort !== 'last_seen' || st.dir !== 'desc') { p.set('sort', st.sort); p.set('dir', st.dir); } return p; };
    const chipText = { q: v => 'Search: ' + v, title: v => 'Title: ' + v, company: v => 'Company: ' + v, location: v => 'Location: ' + v, tool: v => 'Found by ' + (TOOL_LABEL[v] || v), lead_type: v => 'Type: ' + v.replace('_', ' '),
      stage: v => 'Stage: ' + (STAGE_LABEL[v] || v), quality: v => 'Contact quality: ' + (QUALITY_LABEL[v] || v), has_email: () => 'Has email', has_phone: () => 'Has phone', dnc: () => 'Excluding do-not-contact' };
    function renderChips() {
      const o = read(), keys = Object.keys(o);
      chips.replaceChildren(...[...keys.map(k => el('button', { class: 'chip', 'aria-label': 'Remove filter ' + chipText[k](o[k]), onclick: () => { if (k in CHECKS) f[k].checked = false; else f[k].value = ''; refresh(); } }, chipText[k](o[k]), el('span', { 'aria-hidden': 'true' }, ' ×'))),
        keys.length ? el('button', { class: 'ghost small', onclick: () => { apply({}); refresh(); } }, 'Clear all') : null].filter(Boolean));
    }
    const th = (label, key) => el('th', { 'aria-sort': st.sort === key ? (st.dir === 'asc' ? 'ascending' : 'descending') : 'none' },
      key ? el('button', { class: 'thbtn', onclick: () => { if (st.sort === key) st.dir = st.dir === 'asc' ? 'desc' : 'asc'; else { st.sort = key; st.dir = key === 'score' || key === 'last_seen' ? 'desc' : 'asc'; } refresh(); } }, label, st.sort === key ? (st.dir === 'asc' ? ' ▲' : ' ▼') : '') : label);
    const headRow = el('tr');
    const renderHead = () => headRow.replaceChildren(el('th', {}, allBox), th('Name', 'name'), th('Title', 'title'), th('Company', 'company'), th('Location', 'location'), th('Email'), th('Phone'), th('Quality', 'score'), th('Stage', 'stage'), th('Last seen', 'last_seen'), th('Found by'));
    // Floating bar for actions on ticked rows; slides up when something is selected.
    const barCount = el('b'), bulkCamp = el('select', { 'aria-label': 'Campaign for selected leads' }), bulkOwner = el('select', { 'aria-label': 'Owner for selected leads' });
    const clearSel = () => { st.selected.clear(); tbody.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = false; }); allBox.checked = false; updSel(); };
    const bulkBar = el('div', { class: 'bulkbar', role: 'region', 'aria-label': 'Actions for selected leads', hidden: true },
      el('span', {}, barCount, ' selected'),
      el('span', { class: 'bsep' }), bulkCamp, el('button', { onclick: e => busy(e.currentTarget, () => addToCamp({ person_ids: [...st.selected] }, bulkCamp.value)) }, 'Add to campaign'),
      el('span', { class: 'bsep' }), bulkOwner, el('button', { class: 'ghost', onclick: e => busy(e.currentTarget, () => assign(bulkOwner.value)) }, 'Assign owner'),
      el('button', { class: 'ghost', onclick: clearSel, 'aria-label': 'Clear selection' }, 'Clear'));
    const updSel = () => { const n = st.selected.size; selCount.textContent = n ? n + ' selected' : ''; barCount.textContent = String(n); bulkBar.hidden = !n; };
    const phoneCell = r => r.phone ? r.phone : (r.company_phone ? el('span', { class: 'mut', title: 'Company switchboard, not a direct line' }, 'company: ' + r.company_phone) : '');
    const qualityTag = r => !r.score ? notEnriched(r) : el('span', { class: 'qcell', title: 'Contact score ' + r.score + '/100 — verified email 50, other email 20, direct phone 35, company phone 10, found by 2+ tools 15' }, ringFor(r.score), QUALITY_LABEL[r.quality]);

    async function load() {
      const my = ++st.seq;
      try {
        const p = query(); p.set('page', st.page); p.set('page_size', st.pageSize);
        const d = await api('/api/leads?' + p);
        if (my !== st.seq) return;                                   // a newer search already started
        if (!d.items.length && st.page > 1 && d.total) { st.page = 1; return load(); }
        history.replaceState(null, '', location.pathname + (query().toString() ? '?' + query() : ''));
        renderHead(); renderChips();
        tbody.replaceChildren(...(d.items.length ? d.items.map((r, i) => el('tr', { class: r.do_not_contact ? 'dnc-row' : '', style: '--i:' + Math.min(i, 16) },
          el('td', {}, el('input', { type: 'checkbox', 'aria-label': 'Select ' + (r.full_name || 'lead'), checked: st.selected.has(r.id), onchange: e => { e.target.checked ? st.selected.add(r.id) : st.selected.delete(r.id); updSel(); } })),
          el('td', { class: 'nw' }, el('div', { class: 'who-cell' }, avatarFor(r.full_name), el('div', {}, el('button', { class: 'lnkbtn', onclick: () => openDrawer(r.id, load) }, r.full_name || '(no name)'),
            r.linkedin_url ? el('a', { class: 'lnk ext', href: r.linkedin_url, target: '_blank', rel: 'noopener noreferrer', title: 'Open LinkedIn profile', 'aria-label': 'LinkedIn profile of ' + (r.full_name || 'lead') }, ' ↗') : null,
            r.match_confidence === 'low' ? el('span', { class: 'tag bad', title: 'No profile URL: matched by name and company' }, 'low match') : null,
            r.do_not_contact ? el('span', { class: 'tag bad' }, 'do not contact') : null))),
          el('td', {}, r.title || r.headline || ''), el('td', {}, r.company_name || ''), el('td', {}, r.location || ''),
          el('td', { class: 'nw' }, r.email || '', r.email_status === 'valid' ? el('span', { class: 'tag cand', title: 'Verified by the enrichment provider' }, 'verified') : null), el('td', { class: 'nw' }, phoneCell(r)),
          el('td', {}, qualityTag(r)), el('td', { class: 'nw' }, stagePill(r.stage), r.owner ? initials(r.owner) : null),
          el('td', { class: 'mut nw' }, (r.last_seen || '').slice(0, 10)), el('td', {}, r.tools.map(t => el('span', { class: 'tag' }, TOOL_LABEL[t] || t))))) :
          [el('tr', {}, el('td', { colspan: 11 }, Object.keys(read()).length ?
          emptyState('search', 'No leads match these filters', 'Try removing a filter or searching for a shorter word.', el('button', { class: 'ghost', onclick: () => { apply({}); refresh(); } }, 'Clear all filters')) :
          emptyState('inbox', 'No leads yet', 'Pull leads from your four tools on the Sync page.', el('a', { class: 'btn', href: '/sync' }, 'Go to Sync'))))]));
        tbody.classList.remove('fresh'); void tbody.offsetWidth; tbody.classList.add('fresh');
        allBox.checked = d.items.length > 0 && d.items.every(r => st.selected.has(r.id));
        allBox.onchange = () => { d.items.forEach(r => allBox.checked ? st.selected.add(r.id) : st.selected.delete(r.id)); tbody.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = allBox.checked; }); updSel(); };
        st.total = d.total; info.textContent = d.total + ' lead' + (d.total === 1 ? '' : 's') + (Object.keys(read()).length ? ' match' : ''); updSel();
        const pages = Math.max(1, Math.ceil(d.total / d.page_size));
        pager.replaceChildren(el('button', { class: 'ghost', disabled: st.page <= 1, onclick: () => { st.page--; load(); } }, 'Prev'),
          el('span', { class: 'mut' }, 'Page ' + st.page + ' of ' + pages),
          el('button', { class: 'ghost', disabled: st.page >= pages, onclick: () => { st.page++; load(); } }, 'Next'));
        msg(box, '');
      } catch (e) { msg(box, e.message); }
    }
    const refresh = () => { st.page = 1; st.view = null; viewSel.value = ''; delBtn.hidden = true; load(); };
    let timer = null; const later = () => { clearTimeout(timer); timer = setTimeout(refresh, 350); };
    for (const k of TEXT) f[k].addEventListener('input', later);
    for (const k of SELECTS) f[k].addEventListener('change', refresh);
    for (const k of Object.keys(CHECKS)) f[k].addEventListener('change', refresh);
    const exp = fmt => { location.href = '/api/leads/export.' + fmt + '?' + query(); };

    // saved views
    const delBtn = el('button', { class: 'ghost', hidden: true, onclick: async () => {
      const v = st.views.find(x => String(x.id) === viewSel.value); if (!v || !confirm('Delete the view "' + v.name + '"?')) return;
      try { await api('/api/views/' + v.id, { method: 'DELETE' }); toast('View deleted'); await loadViews(); } catch (e) { toast(e.message, false); }
    } }, 'Delete view');
    async function loadViews() {
      st.views = (await api('/api/views?page=leads')).items;
      viewSel.replaceChildren(el('option', { value: '' }, st.views.length ? 'Saved views…' : 'No saved views yet'), ...st.views.map(v => el('option', { value: v.id }, v.name + (v.shared ? ' (team)' : ''))));
      viewSel.value = st.view ? String(st.view) : ''; delBtn.hidden = !st.view;
    }
    viewSel.addEventListener('change', () => {
      const v = st.views.find(x => String(x.id) === viewSel.value); if (!v) { st.view = null; delBtn.hidden = true; return; }
      apply(v.filters); st.page = 1; st.view = v.id; delBtn.hidden = !(v.owner === me.email || me.role === 'admin'); load();
    });
    const saveView = async () => {
      const r = await askName('Save the current filters as a view'); if (!r) return;
      try { const o = read(); if (st.sort !== 'last_seen' || st.dir !== 'desc') { o.sort = st.sort; o.dir = st.dir; }
        const d = await api('/api/views', { method: 'POST', json: { name: r.name, shared: r.shared, filters: o } }); st.view = d.id; await loadViews(); delBtn.hidden = false; toast('View saved'); }
      catch (e) { toast(e.message, false); }
    };

    async function loadCamps() {
      const d = await api('/api/campaigns');
      const opts = () => d.items.length ? d.items.map(c => el('option', { value: c.id }, c.name)) : [el('option', { value: '' }, 'No campaigns yet')];
      campSel.replaceChildren(...opts()); bulkCamp.replaceChildren(...opts());
    }
    async function addToCamp(body, cid = campSel.value) {
      if (!cid) return toast('Create a campaign first (Campaigns page).', false);
      const name = (d => d && d.textContent)(campSel.querySelector('option[value="' + cid + '"]')) || 'the campaign';
      try { const r = await api('/api/campaigns/' + cid + '/leads', { method: 'POST', json: body }); const skip = r.requested - r.added;
        toast(r.added + ' added to ' + name + (skip ? ' · ' + skip + ' already there or not found' : '')); }
      catch (e) { toast(e.message, false); }
    }
    const assign = async (owner = ownerSel.value) => {
      if (!st.selected.size) return toast('Tick some rows first.', false);
      try { const r = await api('/api/crm/bulk/owner', { method: 'POST', json: { person_ids: [...st.selected], owner } }); toast(r.updated + ' lead' + (r.updated === 1 ? '' : 's') + (owner ? ' assigned to ' + owner : ' unassigned')); load(); }
      catch (e) { toast(e.message, false); }
    };
    const sug = field => async q => (await api('/api/leads/suggest?field=' + field + '&q=' + encodeURIComponent(q))).items;
    const pickAll = it => { if (it.field === 'name') f.q.value = it.value; else { f[it.field].value = it.value; f.q.value = ''; } refresh(); };
    const direct = k => it => { f[k].value = it.value; refresh(); };
    const users = (await api('/api/crm/users')).items.map(u => u.email);
    ownerSel.replaceChildren(el('option', { value: '' }, 'No owner'), ...users.map(u => el('option', { value: u }, u)));
    bulkOwner.replaceChildren(el('option', { value: '' }, 'No owner'), ...users.map(u => el('option', { value: u }, u)));

    main.append(el('h1', {}, 'Leads'), el('div', { class: 'sub' }, 'Every lead from your four tools, in one list. Start typing in any box for suggestions, and click a name to open the full record.'),
      el('div', { class: 'card toolbar' },
        el('div', { class: 'row' }, withSuggest(f.q, sug('all'), pickAll, refresh), withSuggest(f.title, sug('title'), direct('title'), refresh), withSuggest(f.company, sug('company'), direct('company'), refresh), withSuggest(f.location, sug('location'), direct('location'), refresh)),
        el('div', { class: 'row', style: 'margin-top:10px' }, f.tool, f.lead_type, f.stage, f.quality,
          el('label', { class: 'mut' }, f.has_email, ' has email'), el('label', { class: 'mut' }, f.has_phone, ' has phone'), el('label', { class: 'mut' }, f.dnc, ' hide do-not-contact'),
          el('span', { style: 'flex:1' }), viewSel, delBtn, el('button', { class: 'ghost', onclick: saveView }, 'Save view')),
        chips),
      el('div', { class: 'card' }, el('div', { class: 'row' }, info, selCount, el('span', { style: 'flex:1' }),
        ownerSel, el('button', { class: 'ghost', onclick: e => busy(e.currentTarget, () => assign()) }, 'Assign owner'),
        campSel,
        el('button', { class: 'ghost', onclick: e => st.selected.size ? busy(e.currentTarget, () => addToCamp({ person_ids: [...st.selected] })) : toast('Tick some rows first.', false) }, 'Add selected'),
        el('button', { class: 'ghost', title: 'Adds every lead that matches the current filters', onclick: async e => { if (!st.total || !confirm('Add all ' + st.total + ' matching leads to the selected campaign?')) return; await busy(e.currentTarget, () => addToCamp({ filter: Object.fromEntries(query()) })); } }, 'Add all matching'),
        el('button', { class: 'ghost', onclick: () => exp('csv') }, 'Export CSV'), el('button', { class: 'ghost', onclick: () => exp('xlsx') }, 'Export Excel')),
        box,
        el('div', { class: 'tscroll', style: 'margin-top:8px' }, el('table', { class: 'wide' }, el('thead', {}, headRow), tbody)),
        el('div', { style: 'margin-top:10px' }, pager)));
    document.body.append(bulkBar);   // outside <main>: its entrance transform would otherwise pin the fixed bar to main
    const url = Object.fromEntries(new URLSearchParams(location.search)); apply(url);
    renderHead(); tbody.replaceChildren(...Array.from({ length: 8 }, () => el('tr', {}, ...Array.from({ length: 11 }, (_, i) => el('td', {}, el('div', { class: 'skel', style: 'width:' + (i === 1 ? 150 : 50 + (i * 13) % 50) + 'px' }))))));
    await Promise.all([loadCamps(), loadViews()]); await load();
  }

  // --------------------------------------------------------- Campaigns
  async function campaignsPage() {
    const main = await shell('/campaigns');
    const box = el('div'), list = el('div'), detail = el('div');
    const name = el('input', { type: 'text', placeholder: 'Campaign name', 'aria-label': 'Campaign name' }), desc = el('input', { type: 'text', placeholder: 'Description (optional)', 'aria-label': 'Description', class: 'grow' });
    let openId = null;
    async function loadList() {
      const d = await api('/api/campaigns');
      list.replaceChildren(d.items.length ? tscroll(el('table', {}, el('thead', {}, el('tr', {}, ...['Name', 'Leads', 'Created', ''].map(h => el('th', {}, h)))),
        el('tbody', {}, ...d.items.map((c, i) => el('tr', { class: c.id === openId ? 'is-open' : '', style: '--i:' + Math.min(i, 16) }, el('td', {}, el('b', {}, c.name), c.description ? el('div', { class: 'mut' }, c.description) : null), el('td', {}, c.lead_count), el('td', { class: 'nw' }, when(c.created_at)),
          el('td', { style: 'text-align:right' }, el('button', { class: c.id === openId ? '' : 'ghost', onclick: e => busy(e.currentTarget, () => open(c.id, true)) }, c.id === openId ? 'Opened' : 'Open'))))))) :
        emptyState('campaigns', 'No campaigns yet', 'Name one above, then add leads to it from the Leads page.'));
    }
    const safe = fn => async e => { try { await fn(e); } catch (er) { toast(er.message, false); } };
    const statusTag = r => !r.email ? el('span', { class: 'mut' }, '—') : el('span', { class: 'tag ' + (r.email_status === 'valid' ? 'cand' : r.email_status === 'invalid' ? 'bad' : '') }, r.email_status || 'unverified');
    async function open(id, scroll) {
      const first = openId !== id; openId = id; if (first) loadList();
      const c = await api('/api/campaigns/' + id);
      const cfg = await api('/api/enrich/config');
      const out = el('div'), runs = el('div');
      const creditCap = el('input', { type: 'number', min: 1, value: cfg.default_credit_cap, style: 'width:90px' });
      const leadCap = el('input', { type: 'number', min: 1, value: cfg.default_lead_cap, style: 'width:90px' });
      let timer = null;
      async function loadRuns() {
        const d = await api('/api/enrich/runs?campaign_id=' + id);
        runs.replaceChildren(d.items.length ? tscroll(el('table', {}, el('thead', {}, el('tr', {}, ...['Started', 'Status', 'Found', 'Not found', 'Errors', 'Credits', 'Note'].map(h => el('th', {}, h)))),
          el('tbody', {}, ...d.items.slice(0, 5).map(r => el('tr', {}, el('td', { class: 'nw' }, when(r.started)), el('td', {}, el('span', { class: 'tag ' + (r.status === 'ok' ? 'cand' : r.status === 'failed' ? 'bad' : r.status === 'running' ? 'live' : '') }, r.status)),
            el('td', {}, r.matched), el('td', {}, r.not_found), el('td', {}, r.errors), el('td', { class: 'nw' }, r.credits_used + ' / cap ' + r.credit_cap), el('td', {}, r.note || '')))))) : el('div', { class: 'mut' }, 'No enrichment runs yet.'));
        return d.items[0];
      }
      const estBtn = el('button', { class: 'ghost', onclick: e => busy(e.currentTarget, async () => {
        try {
          const e = await api('/api/campaigns/' + id + '/enrich/estimate', { method: 'POST', json: { lead_cap: +leadCap.value } });
          const sk = Object.entries(e.skipped).filter(([, n]) => n).map(([k, n]) => n + ' ' + k.replace(/_/g, ' ')).join(', ') || 'none';
          const lookups = Math.min(e.would_look_up, +leadCap.value || e.would_look_up);
          msg(out, `Would look up ${e.would_look_up} of ${e.campaign_leads} leads. Estimated cost: ${e.estimated_credits} credits (hard cap ${creditCap.value}). Skipped: ${sk}.` + (e.configured ? '' : ' API key is not set on the server, so a run is not possible yet.'), e.configured);
          runBtn.disabled = !e.configured || !lookups;
        } catch (er) { msg(out, er.message); }
      }) }, 'Estimate cost');
      const runBtn = el('button', { disabled: true, onclick: async () => {
        if (!confirm('Spend up to ' + creditCap.value + ' credits on up to ' + leadCap.value + ' leads? This uses your RocketReach credits.')) return;
        try {
          await api('/api/campaigns/' + id + '/enrich', { method: 'POST', json: { confirm: true, credit_cap: +creditCap.value, lead_cap: +leadCap.value } });
          msg(out, 'Enrichment started. Results appear below; you can leave this page.', true);
          runBtn.disabled = true; clearInterval(timer);
          timer = setInterval(async () => { const r = await loadRuns(); if (r && r.status !== 'running') { clearInterval(timer); open(id); } }, 4000);
        } catch (er) { msg(out, er.message); }
      } }, 'Run enrichment');
      const enrichCard = el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, 'Enrich contacts (' + cfg.provider + ')'),
        el('div', { class: 'mut' }, cfg.configured ? 'API key found. Always run Estimate first; the run stops before it passes the credit cap.' : 'API key not set: add ROCKETREACH_API_KEY to .env and restart. You can still estimate.'),
        el('div', { class: 'row', style: 'margin-top:8px' }, el('label', {}, 'Credit cap ', creditCap), el('label', {}, 'Lead cap ', leadCap), estBtn, runBtn), out, el('div', { style: 'margin-top:10px' }, runs));
      detail.replaceChildren(enrichCard, el('div', { class: 'card' }, el('div', { class: 'row' }, el('h2', { style: 'margin:0' }, c.name), el('span', { class: 'mut' }, c.lead_count + ' leads'), el('span', { style: 'flex:1' }),
        el('a', { class: 'btn ghost', href: '/api/campaigns/' + id + '/export.csv' }, 'Export CSV'), el('a', { class: 'btn ghost', href: '/api/campaigns/' + id + '/export.xlsx' }, 'Export Excel')),
        el('div', { class: 'tscroll', style: 'margin-top:10px' }, el('table', {}, el('thead', {}, el('tr', {}, ...['Name', 'Title', 'Company', 'Email', 'Email status', 'Phone', 'Type', ''].map(h => el('th', {}, h)))),
          el('tbody', {}, ...c.leads.map(r => el('tr', {}, el('td', {}, r.full_name || '', r.do_not_contact ? el('span', { class: 'tag bad', style: 'margin-left:6px' }, 'do not contact') : null), el('td', {}, r.title || r.headline || ''), el('td', {}, r.company_name || ''), el('td', {}, r.email || ''), el('td', {}, statusTag(r)), el('td', {}, r.phone || '', r.company_phone && !r.phone ? el('div', { class: 'mut' }, 'company: ' + r.company_phone) : null), el('td', {}, typeTag(r.lead_type)),
            el('td', {}, el('div', { class: 'row' },
              el('button', { class: 'ghost', onclick: safe(async () => { if (!confirm('Remove ' + (r.full_name || 'this lead') + ' from ' + c.name + '? The lead itself is kept.')) return; await api('/api/campaigns/' + id + '/leads/' + r.id, { method: 'DELETE' }); toast('Removed from ' + c.name); open(id); loadList(); }) }, 'Remove'),
              el('button', { class: 'ghost', title: 'Look this person up again (uses up to 1 credit)', onclick: async () => { if (!cfg.configured || !confirm('Look up ' + (r.full_name || 'this person') + ' again? This can use 1 credit.')) return; try { await api('/api/campaigns/' + id + '/enrich', { method: 'POST', json: { confirm: true, credit_cap: 1, lead_cap: 1, person_ids: [r.id], include_done: true } }); msg(out, 'Re-check started; refresh in a moment.', true); setTimeout(() => open(id), 6000); } catch (er) { msg(out, er.message); } } }, 'Re-check'),
              el('button', { class: 'ghost', onclick: safe(async () => { await api('/api/leads/' + r.id + '/do-not-contact', { method: 'POST', json: { value: !r.do_not_contact } }); toast(r.do_not_contact ? 'Contact allowed again' : 'Marked do not contact'); open(id); }) }, r.do_not_contact ? 'Allow contact' : 'Do not contact'),
              el('button', { class: 'ghost danger-text', onclick: async () => { if (!confirm('Permanently erase this person and all their data? This cannot be undone.')) return; try { await api('/api/leads/' + r.id, { method: 'DELETE' }); toast('Person erased'); open(id); loadList(); } catch (er) { msg(out, er.message); } } }, 'Erase'))))))))));
      if (scroll) detail.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
      const stats = await api('/api/campaigns/' + id + '/enrich/stats');
      const pct = v => v == null ? 'n/a' : Math.round(v * 100) + '%';
      if (stats.looked_up) enrichCard.append(el('div', { class: 'grid', style: 'margin-top:12px' }, ...[
        ['Looked up', stats.looked_up], ['Found anything', pct(stats.match_rate)], ['Valid email', stats.with_valid_email + ' (' + pct(stats.valid_email_rate) + ')'],
        ['Direct phone', stats.with_direct_phone + ' (' + pct(stats.direct_phone_rate) + ')'], ['Company phone only', stats.company_phone_only], ['No phone at all', stats.no_phone_at_all],
        ['Wrong person (not saved)', stats.wrong_person], ['Needs review', stats.needs_review], ['Credits used', stats.credits_used], ['Credits per valid email', stats.credits_per_valid_email == null ? 'n/a' : stats.credits_per_valid_email]]
        .map(([l, v]) => el('div', { class: 'card' }, el('div', { class: 'stat' }, v), el('div', { class: 'mut' }, l)))),
        el('div', { class: 'mut' }, '"Valid" is the provider\'s own check, not an independent test. Credits are counted by LeadDeck; compare with your provider dashboard.'));
      await loadRuns();
    }
    const create = () => busy(createBtn, async () => {
      if (!name.value.trim()) { msg(box, 'Give the campaign a name first.'); name.focus(); return; }
      try { await api('/api/campaigns', { method: 'POST', json: { name: name.value, description: desc.value } }); toast('Campaign "' + name.value.trim() + '" created'); name.value = ''; desc.value = ''; msg(box, ''); loadList(); } catch (e) { msg(box, e.message); }
    });
    const createBtn = el('button', { onclick: create }, 'Create');
    for (const i of [name, desc]) i.addEventListener('keydown', e => e.key === 'Enter' && create());
    main.append(el('h1', {}, 'Campaigns'), el('div', { class: 'sub' }, 'Group leads into a campaign, then export or (in later phases) enrich and contact them.'),
      el('div', { class: 'card' }, el('div', { class: 'row' }, name, desc, createBtn), box),
      el('div', { class: 'card' }, list), detail);
    await loadList();
  }

  // -------------------------------------------------------------- Sync
  async function syncPage() {
    const main = await shell('/sync');
    const box = el('div'), cards = el('div', { class: 'grid' }), counts = el('div', { class: 'grid' }), hist = el('tbody');
    const runTxt = r => r ? `${r.status}${r.dry_run ? ' (dry run)' : ''} · fetched ${r.fetched} · new ${r.created} · updated ${r.updated} · unchanged ${r.skipped}` : 'never run';
    async function load() {
      const [s, h] = await Promise.all([api('/api/sync/status'), api('/api/sync/runs')]);
      counts.replaceChildren(...[['People', s.counts.people], ['Companies', s.counts.companies], ['Job postings', s.counts.job_postings], ['Database size', fmtBytes(s.db_size_bytes)]]
        .map(([l, v]) => { const n = el('div', { class: 'stat' }, String(v)); if (typeof v === 'number') countUp(n, v); return el('div', { class: 'card' }, n, el('div', { class: 'mut' }, l)); }));
      cards.replaceChildren(...Object.entries(s.tools).map(([tool, t]) => {
        const file = el('input', { type: 'file', accept: '.csv,.xlsx', style: 'display:none' });
        const card = el('div', { class: 'card tool-card' });
        const act = (path, label, btn) => busy(btn, async () => {
          card.classList.add('working');
          msg(box, t.label + ': ' + label + '… free-tier tools can take up to a minute to wake.', true);
          try { const r = await api(path, { method: 'POST' }); msg(box, t.label + ': ' + runTxt(r) + (r.status === 'failed' ? ' — ' + (JSON.parse(r.errors || '[]')[0] || 'failed') : ''), r.status === 'ok'); if (r.status === 'ok') toast(t.label + ': ' + (r.dry_run ? 'preview done' : 'synced')); }
          catch (e) { msg(box, t.label + ': ' + e.message); }
          card.classList.remove('working'); load();
        });
        file.addEventListener('change', async () => {
          const f0 = file.files[0]; if (!f0) return;
          const mode = await choose('Import ' + f0.name, 'Preview first to check the counts. Nothing is saved in a preview.', [['preview', 'Preview only'], ['real', 'Import for real', 'ghost']]);
          if (!mode) { file.value = ''; return; }
          const fd = new FormData(); fd.append('file', f0);
          await busy(impBtn, async () => {
            try { const r = await api('/api/sync/' + tool + '/import?dry_run=' + (mode === 'preview'), { method: 'POST', body: fd }); msg(box, t.label + ' import' + (mode === 'preview' ? ' preview' : '') + ': ' + runTxt(r), true); } catch (e) { msg(box, e.message); }
          });
          file.value = ''; load();
        });
        const last = t.last_run, ok = last && last.status === 'ok', failed = last && last.status === 'failed';
        const impBtn = el('button', { class: 'ghost', onclick: () => file.click() }, 'Import file');
        card.append(el('div', { class: 'tc-head' }, el('h3', {}, t.label), el('span', { class: 'tag ' + (ok ? 'cand' : failed ? 'bad' : 'unk') }, last ? (ok ? 'healthy' : last.status) : 'never run')),
          ...(last ? [el('div', { class: 'tc-nums' }, ...[['fetched', last.fetched], ['new', last.created], ['updated', last.updated], ['unchanged', last.skipped]].map(([l, n]) => el('div', {}, el('b', {}, String(n)), el('span', {}, l))))] : []),
          el('div', { class: 'mut' }, last && last.dry_run ? 'Last run was a preview (dry run).' : 'Last live sync: ' + (t.last_live_ok ? when(t.last_live_ok.finished) : 'never')),
          el('div', { class: 'row', style: 'margin-top:12px' },
            el('button', { class: 'ghost', title: 'Fetch and count, but save nothing', onclick: e => act('/api/sync/' + tool + '?dry_run=true', 'previewing', e.currentTarget) }, 'Dry run'),
            el('button', { onclick: e => act('/api/sync/' + tool + '?dry_run=false', 'syncing', e.currentTarget) }, 'Sync now'),
            impBtn, file));
        return card;
      }));
      hist.replaceChildren(...h.items.map(r => el('tr', {}, el('td', { class: 'nw' }, when(r.started)), el('td', { class: 'nw' }, (TOOL_LABEL[r.tool] || r.tool) + (r.source === 'import' ? ' (file)' : '')),
        el('td', {}, el('span', { class: 'tag ' + (r.status === 'ok' ? 'cand' : r.status === 'failed' ? 'bad' : r.status === 'running' ? 'live' : '') }, r.status + (r.dry_run ? ' · preview' : ''))),
        el('td', { class: 'nw mut', title: 'fetched / new / updated / unchanged' }, `${r.fetched} fetched · ${r.created} new · ${r.updated} upd.`), el('td', {}, (JSON.parse(r.errors || '[]')[0]) || ''))));
    }
    const allBtn = el('button', { onclick: e => busy(e.currentTarget, async () => { msg(box, 'Syncing all tools… this can take a few minutes while free-tier tools wake up.', true); try { const r = await api('/api/sync/all', { method: 'POST' }); msg(box, r.runs.map(x => (TOOL_LABEL[x.tool] || x.tool) + ': ' + x.status + (x.dry_run ? ' (preview)' : '')).join(' · '), r.runs.every(x => x.status === 'ok')); } catch (e) { msg(box, e.message); } load(); }) }, 'Sync all tools');
    main.append(el('h1', {}, 'Sync'), el('div', { class: 'sub' }, 'Pull leads from each tool. A tool’s first sync is always a preview (dry run) so you can check the counts before anything is saved.'),
      counts, el('div', { class: 'row', style: 'margin:6px 0 14px' }, allBtn), box, cards,
      el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, 'Recent runs'),
        el('div', { class: 'tscroll' }, el('table', {}, el('thead', {}, el('tr', {}, ...['Started', 'Tool', 'Status', 'Counts', 'First error'].map(h => el('th', {}, h)))), hist))));
    if (me.role === 'admin') {
      const e = el('input', { type: 'email', placeholder: 'new user email' }), p = el('input', { type: 'password', placeholder: 'password (8+ chars)' }), ub = el('div');
      main.append(el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, 'Add a user'), el('div', { class: 'mut', style: 'margin:-4px 0 10px' }, 'They sign in with this email and password. To reset a password later, use scripts/manage_user.py.'), el('div', { class: 'row' }, e, p, el('button', { class: 'ghost', onclick: ev => busy(ev.currentTarget, async () => {
        try { await api('/api/users', { method: 'POST', json: { email: e.value, password: p.value } }); msg(ub, 'User added.', true); e.value = ''; p.value = ''; } catch (x) { msg(ub, x.message); } }) }, 'Add')), ub));
    }
    await load();
  }


  // ----------------------------------------------------------- CRM shared
  const STAGE_LABEL = { new: 'New', enriched: 'Enriched', ready: 'Ready to contact', contacted: 'Contacted', replied: 'Replied', interested: 'Interested', meeting: 'Meeting booked', won: 'Won', lost: 'Lost', not_a_fit: 'Not a fit' };
  const NEEDS_REASON = ['lost', 'not_a_fit'];
  const localToday = () => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
  const fmtDue = (d, today) => !d ? '' : d.slice(0, 10) === today ? 'Today' : d.slice(0, 10) < today ? 'Overdue · ' + d.slice(0, 10) : d.slice(0, 10);
  const initials = e => e ? el('span', { class: 'avatar', title: e }, e[0].toUpperCase()) : null;
  const EVENT_TEXT = {
    stage_change: d => (d.from ? (STAGE_LABEL[d.from] || d.from) + ' → ' : 'Created as ') + (STAGE_LABEL[d.to] || d.to) + (d.reason ? ' (' + d.reason + ')' : '') + (d.auto ? ' · automatic' : ''),
    note: d => 'Note: ' + (d.preview || ''), owner_change: d => 'Owner: ' + (d.to || 'unassigned'), task_created: d => 'Task added: ' + d.title + (d.due_at ? ' (due ' + d.due_at.slice(0, 10) + ')' : ''),
    task_done: d => 'Task done: ' + d.title, email_sent: () => 'Email sent', reply: () => 'Replied', bounce: () => 'Email bounced', unsubscribe: () => 'Unsubscribed',
  };

  function toast(text, ok = true) {
    let box = $('#toasts'); if (!box) { box = el('div', { id: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(box); }
    const t = el('div', { class: 'toast ' + (ok ? 'ok' : 'err') }, text); box.append(t); setTimeout(() => t.remove(), ok ? 3500 : 7000);
  }
  // Small modal that asks for a reason (used for Lost / Not a fit). Resolves with text or null.
  function askReason(title) {
    return new Promise(res => {
      const inp = el('textarea', { rows: 3, placeholder: 'Reason (required)', style: 'width:100%' });
      const onKey = e => { if (e.key === 'Escape') done(null); };
      const done = v => { document.removeEventListener('keydown', onKey); back.remove(); res(v); };
      document.addEventListener('keydown', onKey);
      const ok = el('button', { onclick: () => inp.value.trim() ? done(inp.value.trim()) : inp.focus() }, 'Save');
      const back = el('div', { class: 'modal-back', onclick: e => e.target === back && done(null) },
        el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' }, el('h3', { style: 'margin:0 0 10px' }, title), inp, el('div', { class: 'row', style: 'margin-top:12px;justify-content:flex-end' }, el('button', { class: 'ghost', onclick: () => done(null) }, 'Cancel'), ok)));
      document.body.append(back); inp.focus();
    });
  }
  async function changeStage(pid, stage) {
    let reason = null;
    if (NEEDS_REASON.includes(stage)) { reason = await askReason('Why is this lead ' + STAGE_LABEL[stage].toLowerCase() + '?'); if (!reason) return false; }
    try { await api('/api/crm/leads/' + pid + '/stage', { method: 'POST', json: { stage, reason } }); return true; }
    catch (e) { toast(e.message, false); return false; }
  }

  // Lead drawer: contacts, stage, owner, notes, tasks, timeline. onChange refreshes the page behind it.
  async function openDrawer(pid, onChange) {
    $('#drawer-back')?.remove();
    const body = el('div', { class: 'drawer-body' }, el('div', { class: 'mut' }, 'Loading…'));
    const onKey = e => { if (e.key === 'Escape') close(); };
    const close = () => { document.removeEventListener('keydown', onKey); back.remove(); onChange && onChange(); };
    document.addEventListener('keydown', onKey);
    const back = el('div', { id: 'drawer-back', class: 'drawer-back', onclick: e => e.target === back && close() },
      el('div', { class: 'drawer', role: 'dialog', 'aria-modal': 'true' }, body));
    document.body.append(back);
    const users = (await api('/api/crm/users')).items.map(u => u.email);
    async function render() {
      let d; try { d = await api('/api/crm/leads/' + pid); } catch (e) { body.replaceChildren(el('div', { class: 'msg err' }, e.message)); return; }
      const today = localToday();
      const stageSel = el('select', { 'aria-label': 'Stage' }, ...Object.entries(STAGE_LABEL).map(([k, l]) => el('option', { value: k, selected: k === d.crm.stage }, l)));
      stageSel.addEventListener('change', async () => { const ok = await changeStage(pid, stageSel.value); if (ok) toast('Stage updated'); render(); });
      const ownerSel = el('select', { 'aria-label': 'Owner' }, el('option', { value: '' }, 'Unassigned'), ...users.map(u => el('option', { value: u, selected: u === d.crm.owner_email }, u)));
      ownerSel.addEventListener('change', async () => { try { await api('/api/crm/leads/' + pid + '/owner', { method: 'POST', json: { owner: ownerSel.value } }); toast('Owner updated'); } catch (e) { toast(e.message, false); } render(); });
      const note = el('textarea', { rows: 2, placeholder: 'Add a note…', style: 'width:100%' });
      const addNote = el('button', { onclick: async () => { if (!note.value.trim()) return; try { await api('/api/crm/leads/' + pid + '/notes', { method: 'POST', json: { body: note.value } }); toast('Note added'); render(); } catch (e) { toast(e.message, false); } } }, 'Add note');
      const tTitle = el('input', { type: 'text', placeholder: 'New task (e.g. Follow up by email)', style: 'flex:1;min-width:160px' }), tDue = el('input', { type: 'date' });
      const addTask = el('button', { onclick: async () => { if (!tTitle.value.trim()) return; try { await api('/api/crm/tasks', { method: 'POST', json: { person_id: pid, title: tTitle.value, due_at: tDue.value || null } }); toast('Task added'); render(); } catch (e) { toast(e.message, false); } } }, 'Add task');
      // Usable contacts first as copyable rows; invalid ones folded away so they don't drown the good ones.
      const KIND_ICON = { email: '✉', phone: '☎', company_phone: '🏢' };
      const cpRow = c => el('div', { class: 'cp' + (c.verification === 'invalid' ? ' bad' : '') },
        el('span', { class: 'cp-k', title: c.kind.replace('_', ' ') }, KIND_ICON[c.kind] || '•'),
        el('div', { class: 'cp-v' }, el('div', {}, c.value, c.is_primary ? el('span', { class: 'star', title: 'Primary' }, ' ★') : null), el('div', { class: 'mut' }, c.kind.replace('_', ' ') + (c.line_type ? ' (' + c.line_type + ')' : '') + (c.provider_grade ? ' · grade ' + c.provider_grade : '') + ' · ' + c.provider)),
        el('span', { class: 'tag ' + (c.verification === 'valid' ? 'cand' : c.verification === 'invalid' ? 'bad' : '') }, c.verification),
        c.verification !== 'invalid' ? el('button', { class: 'ghost small', 'aria-label': 'Copy ' + c.value, onclick: async e => { try { await navigator.clipboard.writeText(c.value); e.currentTarget.textContent = 'Copied'; } catch (er) { toast('Could not copy', false); } } }, 'Copy') : null);
      const goodCp = d.contact_points.filter(c => c.verification !== 'invalid'), badCp = d.contact_points.filter(c => c.verification === 'invalid');
      const contacts = d.contact_points.length ? el('div', { class: 'cps' }, ...goodCp.map(cpRow),
          !goodCp.length ? el('div', { class: 'mut' }, 'No usable contact found yet.') : null,
          badCp.length ? el('details', { class: 'cp-more' }, el('summary', {}, badCp.length + ' invalid ' + (badCp.length === 1 ? 'address' : 'addresses') + ' (not usable)'), ...badCp.map(cpRow)) : null) :
        el('div', { class: 'mut' }, (d.email ? 'Email from tools: ' + d.email + '. ' : '') + 'No enriched contacts yet. Run enrichment from the campaign page.');
      body.replaceChildren(...[
        el('div', { class: 'head' }, avatarFor(d.full_name, 'lg'), el('div', {}, el('h2', { style: 'margin:0;font-size:24px;letter-spacing:-.02em' }, d.full_name || 'Unnamed'), el('div', { class: 'mut' }, [d.title, d.company_name].filter(Boolean).join(' · ') || '—'),
          (d.company_industry || d.company_size) ? el('div', { class: 'mut', style: 'font-size:13px' }, [d.company_industry, d.company_size ? (/employee/i.test(d.company_size) ? d.company_size : d.company_size + ' employees') : null].filter(Boolean).join(' · ')) : null,
          d.linkedin_url ? el('a', { class: 'lnk', href: d.linkedin_url, target: '_blank', rel: 'noopener noreferrer' }, 'LinkedIn profile ↗') : null),
          d.score ? ringFor(d.score, 'lg') : notEnriched(d), el('button', { class: 'ghost', onclick: close, 'aria-label': 'Close' }, '✕')),
        d.do_not_contact ? el('div', { class: 'msg err' }, 'Do not contact' + (d.dnc_reason ? ' — ' + d.dnc_reason : '')) : null,
        el('div', { class: 'row', style: 'margin:12px 0' }, el('label', {}, 'Stage ', stageSel), el('label', {}, 'Owner ', ownerSel)),
        d.crm.stage === 'lost' || d.crm.stage === 'not_a_fit' ? el('div', { class: 'mut' }, 'Reason: ' + (d.crm.lost_reason || '—')) : null,
        el('h3', {}, 'Contact details'), contacts,
        el('h3', {}, 'Tasks'), el('div', { class: 'row' }, tTitle, tDue, addTask),
        d.tasks.length ? el('ul', { class: 'plain' }, ...d.tasks.map(t => el('li', {}, el('label', { class: 'row', style: 'gap:8px' }, el('input', { type: 'checkbox', checked: !!t.done_at, onchange: async e => { try { await api('/api/crm/tasks/' + t.id, { method: 'PATCH', json: { done: e.target.checked } }); render(); } catch (er) { toast(er.message, false); } } }),
          el('span', { style: t.done_at ? 'text-decoration:line-through;color:#94a3b8' : '' }, t.title), t.due_at ? el('span', { class: 'tag ' + (t.overdue ? 'bad' : '') }, fmtDue(t.due_at, today)) : null)))) : el('div', { class: 'mut', style: 'margin-top:6px' }, 'No tasks yet.'),
        el('h3', {}, 'Notes'), note, el('div', { style: 'margin:6px 0 10px' }, addNote),
        d.notes.length ? el('ul', { class: 'plain' }, ...d.notes.map(n => el('li', {}, el('div', {}, n.body), el('div', { class: 'mut' }, n.author + ' · ' + when(n.created_at))))) : null,
        el('h3', {}, 'Timeline'),
        el('ul', { class: 'timeline' }, ...d.timeline.map(e => el('li', {}, el('div', {}, (EVENT_TEXT[e.type] || (() => e.type))(e.detail)), el('div', { class: 'mut' }, (e.actor || '') + ' · ' + when(e.created_at)))))].filter(x => x != null));
    }
    await render();
  }

  // ---------------------------------------------------------- Pipeline
  async function pipelinePage() {
    const main = await shell('/pipeline');
    const camp = el('select', { 'aria-label': 'Campaign' }, el('option', { value: '' }, 'All campaigns')), owner = el('select', { 'aria-label': 'Owner' }, el('option', { value: '' }, 'Any owner'), el('option', { value: 'none' }, 'Unassigned')),
      q = el('input', { type: 'text', placeholder: 'Search name or company', 'aria-label': 'Search' }), boardBox = el('div', { class: 'board' }), info = el('span', { class: 'mut' }),
      staleOnly = el('input', { type: 'checkbox' });
    const [cs, us] = await Promise.all([api('/api/campaigns'), api('/api/crm/users')]);
    cs.items.forEach(c => camp.append(el('option', { value: c.id }, c.name))); us.items.forEach(u => owner.append(el('option', { value: u.email }, u.email)));
    const today = localToday(); let landed = null, first = true;
    const funnel = el('div', { class: 'funnel', 'aria-label': 'Pipeline summary' });
    async function load() {
      const p = new URLSearchParams(); if (camp.value) p.set('campaign_id', camp.value); if (owner.value) p.set('owner', owner.value); if (q.value.trim()) p.set('q', q.value.trim());
      let d; try { d = await api('/api/crm/board?' + p); } catch (e) { toast(e.message, false); return; }
      let total = 0;
      boardBox.replaceChildren(...d.stages.map(st => {
        const col = d.columns[st]; total += col.count;
        const shown = staleOnly.checked ? col.items.filter(it => it.stale) : col.items;
        const body = el('div', { class: 'col-body' }, ...shown.map(it => {
          const card = el('div', { class: 'lcard' + (it.do_not_contact ? ' dnc' : ''), draggable: 'true', tabindex: 0, role: 'button', 'data-pid': it.person_id, 'aria-label': (it.full_name || 'Lead') + ', open details' },
            el('div', { class: 'top' }, avatarFor(it.full_name), el('div', {}, el('div', { class: 'lname' }, it.full_name || 'Unnamed'), el('div', { class: 'mut', style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, [it.title, it.company_name].filter(Boolean).join(' · ') || '—'))),
            el('div', { class: 'lmeta' }, it.email_status ? el('span', { class: 'tag ' + (it.email_status === 'valid' ? 'cand' : it.email_status === 'invalid' ? 'bad' : '') }, 'email ' + it.email_status) : null,
              it.stale ? el('span', { class: 'tag bad', title: 'No activity for ' + it.idle_days + ' days (flagged after ' + col.stale_after_days + ')' }, '⏱ ' + it.idle_days + 'd idle') : null,
              it.do_not_contact ? el('span', { class: 'tag bad' }, 'do not contact') : null, it.open_tasks ? el('span', { class: 'tag' }, it.open_tasks + ' task' + (it.open_tasks > 1 ? 's' : '')) : null,
              it.next_action_at ? el('span', { class: 'tag ' + (it.next_action_at.slice(0, 10) < today ? 'bad' : '') }, fmtDue(it.next_action_at, today)) : null, initials(it.owner_email)));
          card.addEventListener('click', () => openDrawer(it.person_id, load));
          card.addEventListener('keydown', e => { if (e.key === 'Enter') openDrawer(it.person_id, load); });
          card.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', String(it.person_id)); card.classList.add('drag'); });
          card.addEventListener('dragend', () => card.classList.remove('drag'));
          return card;
        }), col.count > col.items.length && !staleOnly.checked ? el('div', { class: 'mut', style: 'padding:6px' }, '+' + (col.count - col.items.length) + ' more (use search or filters)') : null,
          !shown.length ? el('div', { class: 'empty' }, staleOnly.checked && col.count ? 'Nothing stale here' : 'Drop a lead here') : null);
        const colEl = el('section', { class: 'col stage-' + st, 'aria-label': col.label }, el('header', {}, el('span', {}, col.label), el('span', { class: 'count' }, col.count)), body);
        colEl.addEventListener('dragover', e => { e.preventDefault(); colEl.classList.add('over'); });
        colEl.addEventListener('dragleave', () => colEl.classList.remove('over'));
        colEl.addEventListener('drop', async e => { e.preventDefault(); colEl.classList.remove('over'); const pid = +e.dataTransfer.getData('text/plain'); if (pid && await changeStage(pid, st)) { toast('Moved to ' + col.label); landed = pid; } load(); });
        return colEl;
      }));
      const nStale = Object.values(d.columns).reduce((n, c) => n + c.items.filter(i => i.stale).length, 0);
      // pipeline summary strip: one segment per stage, sized by how many leads are in it
      const segs = d.stages.filter(k => d.columns[k].count > 0);
      funnel.replaceChildren(el('div', { class: 'bar', role: 'img', 'aria-label': d.stages.map(k => d.columns[k].label + ' ' + d.columns[k].count).join(', ') }, ...segs.map(k => el('div', { class: 'seg stage-' + k, style: '--n:' + d.columns[k].count, title: d.columns[k].label + ': ' + d.columns[k].count,
          onclick: () => boardBox.querySelector('.stage-' + k)?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' }) }))),
        total ? el('div', { class: 'legend' }, ...d.stages.filter(k => d.columns[k].count > 0).map(k => el('button', { class: 'stage-' + k, onclick: () => boardBox.querySelector('.stage-' + k)?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' }) }, el('i', {}), d.columns[k].label + ' ', el('b', {}, String(d.columns[k].count))))) : null);
      funnel.hidden = !total;
      if (first) { first = false; boardBox.classList.add('deal'); [...boardBox.querySelectorAll('.lcard')].forEach((c, i) => c.style.setProperty('--i', Math.min(i, 14))); setTimeout(() => boardBox.classList.remove('deal'), 1800); }
      if (landed) { boardBox.querySelector('[data-pid="' + landed + '"]')?.classList.add('landed'); landed = null; }
      info.textContent = total + ' lead' + (total === 1 ? '' : 's') + (total ? (nStale ? ' · ' + nStale + ' stale' : '') : ' — add leads to a campaign to see them here');
    }
    for (const c of [camp, owner, staleOnly]) c.addEventListener('change', load);
    let qTimer = null; q.addEventListener('input', () => { clearTimeout(qTimer); qTimer = setTimeout(load, 350); });
    const qBox = withSuggest(q, async t => (await api('/api/leads/suggest?field=all&q=' + encodeURIComponent(t))).items.filter(i => i.field === 'name' || i.field === 'company'), it => { q.value = it.value; load(); }, load);
    main.append(el('h1', {}, 'CRM pipeline'), el('div', { class: 'sub' }, 'Every lead you are working, by stage. Drag a card to move it, or click it for details. A red clock means no recent activity.'),
      el('div', { class: 'card toolbar' }, el('div', { class: 'row' }, camp, owner, qBox, el('label', { class: 'mut', title: 'Leads with no activity for several days in a stage where someone should be acting' }, staleOnly, ' stale only'), el('button', { class: 'ghost', onclick: load }, 'Refresh'), info)), funnel, boardBox);
    await load();
  }

  // ------------------------------------------------------------- Tasks
  async function tasksPage() {
    const main = await shell('/tasks');
    const today = localToday(), st = { due: 'overdue', mine: true };
    const list = el('div'), tabs = el('div', { class: 'row', role: 'tablist' });
    const defs = [['overdue', 'Overdue'], ['today', 'Today'], ['upcoming', 'Upcoming'], ['all', 'All open'], ['done', 'Done']];
    async function load() {
      tabs.replaceChildren(...defs.map(([k, l]) => el('button', { class: k === st.due ? '' : 'ghost', role: 'tab', 'aria-selected': String(k === st.due), onclick: () => { st.due = k; load(); } }, l)),
        el('label', { style: 'margin-left:12px' }, el('input', { type: 'checkbox', checked: st.mine, onchange: e => { st.mine = e.target.checked; load(); } }), ' Only my tasks'));
      const p = new URLSearchParams({ today }); if (st.mine) p.set('mine', '1');
      if (st.due === 'done') p.set('status', 'done'); else if (st.due !== 'all') p.set('due', st.due);
      let d; try { d = await api('/api/crm/tasks?' + p); } catch (e) { toast(e.message, false); return; }
      list.replaceChildren(d.items.length ? el('table', {}, el('thead', {}, el('tr', {}, ...['', 'Task', 'Lead', 'Due', 'Owner'].map(h => el('th', {}, h)))),
        el('tbody', {}, ...d.items.map(t => el('tr', {}, el('td', {}, el('input', { type: 'checkbox', checked: !!t.done_at, 'aria-label': 'Mark done', onchange: async e => { try { await api('/api/crm/tasks/' + t.id, { method: 'PATCH', json: { done: e.target.checked } }); toast(e.target.checked ? 'Task done' : 'Task reopened'); load(); } catch (er) { toast(er.message, false); } } })),
          el('td', {}, t.title), el('td', {}, el('a', { class: 'lnk', href: '#', onclick: e => { e.preventDefault(); openDrawer(t.person_id, load); } }, t.person_name || 'Lead'), el('div', { class: 'mut' }, t.company_name || '')),
          el('td', {}, t.due_at ? el('span', { class: 'tag ' + (t.overdue ? 'bad' : '') }, fmtDue(t.due_at, today)) : el('span', { class: 'mut' }, 'No date')), el('td', {}, t.owner_email || ''))))) :
        ({ overdue: () => emptyState('tasks', 'Nothing overdue', 'You are all caught up.'), today: () => emptyState('tasks', 'Nothing due today', 'Tasks with today’s date show up here.'),
          upcoming: () => emptyState('tasks', 'Nothing coming up', 'Add a follow-up from a lead’s panel on the CRM board.', el('a', { class: 'btn ghost', href: '/pipeline' }, 'Open CRM')),
          done: () => emptyState('tasks', 'No finished tasks yet', 'Tick a task to mark it done.') }[st.due] || (() => emptyState('tasks', 'No open tasks', st.mine ? 'Untick “Only my tasks” to see the whole team’s.' : 'Add a follow-up from a lead’s panel on the CRM board.')))());
    }
    main.append(el('h1', {}, 'Tasks'), el('div', { class: 'sub' }, 'Follow-ups for your leads. Add tasks from a lead’s detail panel on the Pipeline page.'), el('div', { class: 'card' }, tabs), el('div', { class: 'card' }, list));
    await load();
  }


  // ------------------------------------------------------------- Analytics
  const nf = n => (n == null ? '—' : Number(n).toLocaleString());
  const pc = n => (n == null ? '—' : n + '%');
  let tipEl = null;
  function tip(node, text) {   // one shared tooltip; hover or keyboard focus on a mark
    node.addEventListener('mouseenter', () => { if (!tipEl) { tipEl = el('div', { class: 'tip', role: 'tooltip' }); document.body.append(tipEl); } tipEl.textContent = text; tipEl.style.opacity = 1; });
    node.addEventListener('mousemove', e => { if (tipEl) { tipEl.style.left = Math.min(e.clientX + 14, innerWidth - 220) + 'px'; tipEl.style.top = (e.clientY + 14) + 'px'; } });
    const off = () => { if (tipEl) tipEl.style.opacity = 0; };
    node.addEventListener('mouseleave', off); node.addEventListener('blur', off);
    node.addEventListener('focus', () => { if (!tipEl) { tipEl = el('div', { class: 'tip', role: 'tooltip' }); document.body.append(tipEl); } const r = node.getBoundingClientRect(); tipEl.textContent = text; tipEl.style.left = r.left + 'px'; tipEl.style.top = (r.top - 36) + 'px'; tipEl.style.opacity = 1; });
  }
  const niceMax = v => { if (v <= 4) return 4; const p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p; return (f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; };
  const shortDay = d => new Date(d + 'T00:00:00Z').toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' });
  function columnChart(rows, key, unit, title, wide) {
    const W = wide ? 1100 : 640, H = wide ? 200 : 190, L = 34, B = 24, T = 10, R = 6, vals = rows.map(r => r[key]), total = vals.reduce((a, b) => a + b, 0), max = niceMax(Math.max(...vals, 0));
    const bw = (W - L - R) / rows.length, ih = H - T - B;
    const g = [0, .5, 1].map(f => { const y = T + ih * (1 - f); return [sv('line', { class: 'gl', x1: L, x2: W - R, y1: y, y2: y }), sv('text', { class: 'ax', x: L - 6, y: y + 4, 'text-anchor': 'end' }, nf(Math.round(max * f)))]; }).flat();
    const bars = rows.map((r, i) => { const h = Math.max(r[key] ? 2 : 0, ih * r[key] / max), x = L + i * bw + Math.min(1, bw * .12);
      const b = sv('rect', { class: 'col' + (r[key] ? '' : ' z'), x, y: T + ih - h, width: Math.max(1, bw - Math.min(2, bw * .24)), height: h, rx: Math.min(4, bw / 2.4), tabindex: r[key] ? '0' : null, 'aria-label': shortDay(r.date) + ': ' + r[key] + ' ' + unit });
      tip(b, shortDay(r.date) + ' — ' + nf(r[key]) + ' ' + unit); return b; });
    const lab = [0, Math.floor((rows.length - 1) / 2), rows.length - 1].map((i, k) => sv('text', { class: 'ax', x: L + i * bw + bw / 2, y: H - 6, 'text-anchor': k === 0 ? 'start' : k === 2 ? 'end' : 'middle' }, shortDay(rows[i].date)));
    const chart = svg({ viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img', 'aria-label': `${title}: ${nf(total)} ${unit} in the last ${rows.length} days` }, ...g, ...bars, ...lab);
    return el('div', {}, el('div', { class: 'chart-h' }, el('b', {}, title), el('span', { class: 'mut' }, nf(total) + ' ' + unit + ' in ' + rows.length + ' days')),
      total ? chart : el('div', { class: 'empty' }, 'Nothing recorded in this period.'));
  }
  const kpi = (label, value, sub, tone) => { const v = el('div', { class: 'kv' }, '0');
    if (typeof value === 'number') countUp(v, value); else v.textContent = value;
    return el('div', { class: 'kpi' + (tone ? ' ' + tone : '') }, el('div', { class: 'kl' }, label), v, el('div', { class: 'ks' }, sub || '')); };

  // ---- comparison helpers: every in-period number is shown against the previous period of the same length
  function delta(cur, prev, opts = {}) {   // opts.good: 'up' | 'down' | 'none'
    const good = opts.good || 'up', span = 'vs previous ' + opts.days + ' days';
    if (cur == null || prev == null) return el('span', { class: 'dl flat' }, 'No earlier data to compare');
    if (cur === prev) return el('span', { class: 'dl flat', title: span }, '— no change ', el('small', {}, span));
    if (!prev) return el('span', { class: 'dl ' + (good === 'none' ? 'flat' : good === 'up' ? 'good' : 'bad'), title: span }, '▲ new ', el('small', {}, 'none the period before'));
    const ch = Math.round(100 * (cur - prev) / Math.abs(prev)), up = cur > prev;
    const tone = good === 'none' ? 'flat' : (up === (good === 'up')) ? 'good' : 'bad';
    return el('span', { class: 'dl ' + tone, title: `${nf(cur)} now, ${nf(prev)} the ${opts.days} days before` }, (up ? '▲ ' : '▼ ') + Math.abs(ch) + '% ', el('small', {}, span));
  }
  function spark(vals) {   // tiny trend line for a KPI tile; decorative (the number and delta carry the meaning)
    if (!vals || !vals.some(v => v)) return null;
    const W = 120, H = 34, max = Math.max(...vals), n = vals.length, x = i => (W * i) / Math.max(1, n - 1), y = v => H - 3 - (H - 6) * v / max;
    const pts = vals.map((v, i) => x(i).toFixed(1) + ',' + y(v).toFixed(1)).join(' ');
    return svg({ viewBox: `0 0 ${W} ${H}`, class: 'spark', 'aria-hidden': 'true', preserveAspectRatio: 'none' },
      sv('polygon', { points: `0,${H} ${pts} ${W},${H}`, class: 'sa' }), sv('polyline', { points: pts, class: 'sl' }));
  }
  const meter = pct => el('div', { class: 'meter', role: 'img', 'aria-label': pc(pct) }, el('i', { style: `width:${Math.min(100, pct || 0)}%` }));
  function kpi2({ label, value, sub, dl, sp, bar, tone, hint }) {
    const v = el('div', { class: 'kv' }, '0');
    if (typeof value === 'number') countUp(v, value); else v.textContent = value;
    return el('div', { class: 'kpi' + (tone ? ' ' + tone : ''), title: hint || null }, el('div', { class: 'kl' }, label),
      el('div', { class: 'krow' }, v, sp || null), bar != null ? meter(bar) : null, el('div', { class: 'ks' }, sub || ''), dl || null);
  }
  function insights(d) {   // plain-language highlights, like the "insights" cards in HubSpot / Apollo reports
    const out = [], L = d.leads, cur = d.compare.current, prev = d.compare.previous;
    if (d.sources.length > 1) {
      const b = [...d.sources].sort((a, c) => (c.valid_email_rate || 0) - (a.valid_email_rate || 0) || c.avg_score - a.avg_score)[0];
      out.push(['good', 'Best source', `${b.label} gives the most reachable leads: ${pc(b.valid_email_rate ?? 0)} have a valid email (average score ${b.avg_score}).`]);
    }
    const all = d.funnel.filter(f => !f.terminal); let worst = null;
    for (let i = 1; i < all.length; i++) if (all[i - 1].reached) { const drop = all[i - 1].reached - all[i].reached; if (!worst || drop > worst.drop) worst = { from: all[i - 1], to: all[i], drop }; }
    if (worst && worst.drop > 0) out.push(['warn', 'Biggest drop-off', `${nf(worst.drop)} leads stop between ${worst.from.label} and ${worst.to.label} (${pc(worst.to.of_previous ?? 0)} move on).`]);
    if (L.total && L.valid_email_rate != null && L.valid_email_rate < 25) out.push(['info', 'Next step', `Only ${pc(L.valid_email_rate)} of leads have a valid email. Enriching a campaign raises this${d.enrichment.credits_per_valid_email ? ', at about ' + d.enrichment.credits_per_valid_email + ' credits per valid email' : ''}.`]);
    if (cur.leads_added !== prev.leads_added) out.push(['info', 'Lead flow', `${nf(cur.leads_added)} new leads in the last ${d.days} days, compared with ${nf(prev.leads_added)} in the ${d.days} days before.`]);
    if (d.tasks.overdue) out.push(['bad', 'Overdue', `${nf(d.tasks.overdue)} follow-up task${d.tasks.overdue === 1 ? ' is' : 's are'} overdue.`]);
    if (!out.length) return null;
    const ic = { good: '✓', warn: '!', bad: '!', info: 'i' };
    return el('div', { class: 'ins' }, ...out.slice(0, 4).map(([t, h, s]) => el('div', { class: 'insc ' + t }, el('span', { class: 'ii', 'aria-hidden': 'true' }, ic[t]), el('div', {}, el('b', {}, h), el('div', {}, s)))));
  }
  function sourcesTable(src) {
    if (!src.length) return el('div', { class: 'empty' }, 'No leads imported yet. Run a sync first.');
    src.forEach(s => { s.phone_rate = s.leads ? Math.round(1000 * s.with_phone / s.leads) / 10 : 0; });
    const st = { key: 'leads' }, wrap = el('div'), total = src.reduce((a, s) => a + s.leads, 0);
    const cols = [['leads', 'Leads', s => s.leads], ['valid_email_rate', 'Valid email', s => s.valid_email_rate || 0], ['phone_rate', 'Direct phone', s => s.phone_rate], ['avg_score', 'Avg. score', s => s.avg_score]];
    const best = Object.fromEntries(cols.map(([k, , f]) => [k, Math.max(...src.map(f))]));
    const top = (k, v) => v === best[k] && src.length > 1 && v > 0 ? el('span', { class: 'best' }, 'Best') : null;
    function draw() {
      const f = cols.find(c => c[0] === st.key)[2], rows = [...src].sort((a, b) => f(b) - f(a));
      const th = (k, l) => el('th', { class: 'sortable' + (st.key === k ? ' on' : ''), 'aria-sort': st.key === k ? 'descending' : 'none' }, el('button', { class: 'thb', onclick: () => { st.key = k; draw(); } }, l, st.key === k ? ' ↓' : ''));
      wrap.replaceChildren(tscroll(el('table', { class: 'cmp' }, el('thead', {}, el('tr', {}, el('th', {}, 'Tool'), ...cols.map(([k, l]) => th(k, l)))),
        el('tbody', {}, ...rows.map(s => el('tr', {}, el('td', { class: 'nw' }, el('b', {}, s.label)),
          el('td', {}, el('div', { class: 'cv' }, el('b', {}, nf(s.leads)), el('span', { class: 'mut' }, pc(Math.round(1000 * s.leads / total) / 10) + ' of all'), top('leads', s.leads)), meter(100 * s.leads / best.leads)),
          el('td', {}, el('div', { class: 'cv' }, el('b', {}, pc(s.valid_email_rate ?? 0)), el('span', { class: 'mut' }, nf(s.valid_email) + ' leads'), top('valid_email_rate', s.valid_email_rate || 0)), meter(s.valid_email_rate)),
          el('td', {}, el('div', { class: 'cv' }, el('b', {}, pc(s.phone_rate)), el('span', { class: 'mut' }, nf(s.with_phone) + ' leads'), top('phone_rate', s.phone_rate)), meter(s.phone_rate)),
          el('td', {}, el('div', { class: 'cv' }, ringFor(Math.round(s.avg_score), 'sm'), top('avg_score', s.avg_score)))))))),
        el('div', { class: 'mut', style: 'margin-top:10px;font-size:13px' }, 'Click a column heading to rank the tools by it. A lead found by two tools counts for both.'));
    }
    draw(); return wrap;
  }
  function outcomes(E) {
    const parts = [['matched', 'Found', E.matched], ['nf', 'Not found', E.not_found], ['wp', 'Wrong person', E.wrong_person], ['er', 'Error', E.errors]], t = parts.reduce((a, p) => a + p[2], 0);
    if (!t) return el('div', { class: 'empty' }, 'No lookups in this period.');
    return el('div', {}, el('div', { class: 'seg thin', role: 'img', 'aria-label': parts.map(p => p[1] + ' ' + p[2]).join(', ') },
      ...parts.filter(p => p[2]).map(([k, l, n]) => { const b = el('div', { class: 'sg o-' + k, style: `flex:${n}`, tabindex: '0' }); tip(b, `${l}: ${nf(n)} (${pc(Math.round(1000 * n / t) / 10)})`); return b; })),
      el('div', { class: 'leg row4' }, ...parts.map(([k, l, n]) => el('div', {}, el('i', { class: 'sw o-' + k }), el('span', {}, l), el('b', {}, nf(n)), el('span', { class: 'mut' }, pc(Math.round(1000 * n / t) / 10))))));
  }

  async function analyticsPage() {
    const main = await shell('/analytics'), st = { days: 30 }, body = el('div');
    const range = el('div', { class: 'seg-ctl', role: 'tablist', 'aria-label': 'Time range' });
    async function load() {
      range.replaceChildren(...[7, 30, 90].map(d => el('button', { class: d === st.days ? 'on' : '', role: 'tab', 'aria-selected': String(d === st.days), onclick: () => { st.days = d; load(); } }, d + ' days')));
      let d; try { d = await api('/api/analytics?days=' + st.days + '&today=' + localToday()); } catch (e) { body.replaceChildren(el('div', { class: 'msg err' }, e.message)); return; }
      const L = d.leads, E = d.enrichment, C = d.crm, T = d.tasks, O = d.outreach, Q = d.quality, cur = d.compare.current, prev = d.compare.previous, days = d.days;
      // funnel: reached, conversion from the stage before, and the biggest drop highlighted
      const stages = d.funnel.filter(f => !f.terminal), funMax = Math.max(1, ...stages.map(f => f.reached));
      let worstI = -1, worstDrop = 0; stages.forEach((f, i) => { if (i && stages[i - 1].reached - f.reached > worstDrop) { worstDrop = stages[i - 1].reached - f.reached; worstI = i; } });
      const funnel = el('div', { class: 'fun' }, ...stages.map((f, i) => el('div', { class: 'fr' + (i === worstI ? ' drop' : '') }, el('div', { class: 'fl' }, f.label),
        el('div', { class: 'ft' }, (() => { const b = el('div', { class: 'fb s-' + f.stage, style: `width:${Math.max(f.reached ? 1.5 : 0, 100 * f.reached / funMax)}%`, tabindex: '0', 'aria-label': `${f.label}: ${f.reached} reached, ${f.current} there now` });
          tip(b, `${f.label}: ${f.reached} reached · ${f.current} there now${f.of_previous != null ? ' · ' + pc(f.of_previous) + ' of the stage before' : ''}`); return b; })()),
        el('div', { class: 'fn' }, el('b', {}, nf(f.reached)), el('span', { class: 'conv' + (i === worstI ? ' bad' : '') }, f.of_previous != null ? pc(f.of_previous) : (i === 0 ? '100%' : '—'))))));
      const ended = el('div', { class: 'row', style: 'margin-top:12px;flex-wrap:wrap' }, el('span', { class: 'mut' }, 'Ended:'), ...d.funnel.filter(f => f.terminal).map(f => el('span', { class: 'chipc' }, stagePill(f.stage), ' ', el('b', {}, nf(f.current)))));
      const qt = Q.high + Q.medium + Q.low, qs = [['high', 'High', Q.high], ['medium', 'Medium', Q.medium], ['low', 'Low', Q.low]];
      const seg = el('div', { class: 'seg', role: 'img', 'aria-label': `Contact quality: ${Q.high} high, ${Q.medium} medium, ${Q.low} low` }, ...qs.filter(x => x[2]).map(([k, l, n]) => { const b = el('div', { class: 'sg q-' + k, style: `flex:${n}`, tabindex: '0' }, n / qt > .09 ? nf(n) : ''); tip(b, `${l}: ${nf(n)} leads (${pc(Math.round(1000 * n / qt) / 10)})`); return b; }));
      const outreach = O.connected ? el('div', {}, el('div', { class: 'kpis small' }, kpi('Emails sent', O.sent, 'last ' + days + ' days'), kpi('Reply rate', pc(O.reply_rate), nf(O.replies) + ' replies'), kpi('Bounce rate', pc(O.bounce_rate), nf(O.bounces) + ' bounced', O.bounce_rate > 5 ? 'warn' : ''), kpi('Unsubscribed', pc(O.unsubscribe_rate), nf(O.unsubscribes) + ' people')), columnChart(O.by_day, 'sent', 'emails', 'Emails sent per day', true))
        : el('div', { class: 'oe' }, el('span', { class: 'eico' }, icon('campaigns')), el('div', {}, el('b', {}, 'No emails recorded yet'), el('div', { class: 'mut' }, 'Sent, reply, bounce and unsubscribe rates appear here once an outreach tool sends events to LeadDeck. Nothing is estimated in the meantime.')));
      const owners = C.owners.length ? tscroll(el('table', {}, el('thead', {}, el('tr', {}, ...['Owner', 'Leads', 'Open tasks'].map(h => el('th', {}, h)))),
        el('tbody', {}, ...C.owners.map(o => el('tr', {}, el('td', {}, o.owner || el('span', { class: 'mut' }, 'Unassigned')), el('td', {}, nf(o.leads)), el('td', {}, nf(o.open_tasks))))))) : el('div', { class: 'empty' }, 'No leads in the pipeline yet.');
      const sec = (t, s) => el('div', { class: 'sec' }, el('h3', {}, t), s ? el('span', { class: 'mut' }, s) : null);
      body.replaceChildren(
        insights(d) || '',
        sec('Headline numbers', 'Arrows compare the last ' + days + ' days with the ' + days + ' days before'),
        el('div', { class: 'kpis k4' },
          kpi2({ label: 'New leads', value: cur.leads_added, sub: nf(L.total) + ' leads in total', dl: delta(cur.leads_added, prev.leads_added, { days }), sp: spark(L.by_day.map(r => r.added)) }),
          kpi2({ label: 'Valid email', value: pc(L.valid_email_rate), sub: nf(L.valid_email) + ' of ' + nf(L.total) + ' leads', bar: L.valid_email_rate, hint: 'Share of all leads with an email the provider checked as valid' }),
          kpi2({ label: 'Direct phone', value: pc(L.phone_rate), sub: nf(L.with_phone) + ' of ' + nf(L.total) + ' leads', bar: L.phone_rate }),
          kpi2({ label: 'Meetings booked', value: cur.meetings_booked, sub: nf(C.events_in_period) + ' CRM updates', dl: delta(cur.meetings_booked, prev.meetings_booked, { days }) }),
          kpi2({ label: 'Credits used', value: cur.credits, sub: nf(cur.lookups) + ' lookups', dl: delta(cur.credits, prev.credits, { days, good: 'none' }), sp: spark(E.by_day.map(r => r.credits)) }),
          kpi2({ label: 'Match rate', value: pc(cur.match_rate), sub: 'lookups that found the person', dl: delta(cur.match_rate, prev.match_rate, { days }), bar: cur.match_rate }),
          kpi2({ label: 'Credits per valid email', value: cur.credits_per_valid_email == null ? '—' : String(cur.credits_per_valid_email), sub: nf(cur.valid_emails_found) + ' valid emails found · lower is better', dl: delta(cur.credits_per_valid_email, prev.credits_per_valid_email, { days, good: 'down' }) }),
          kpi2({ label: 'Open tasks', value: T.open, sub: T.overdue ? nf(T.overdue) + ' overdue' : 'none overdue', tone: T.overdue ? 'warn' : '', dl: el('span', { class: 'dl flat' }, nf(cur.tasks_done) + ' done in ' + days + ' days') })),
        el('div', { class: 'card' }, columnChart(L.by_day, 'added', 'new leads', 'Lead flow: new leads per day', true),
          el('div', { class: 'mut', style: 'margin-top:8px;font-size:13px' }, nf(prev.leads_added) + ' new leads in the ' + days + ' days before.')),
        sec('Pipeline and reachability'),
        el('div', { class: 'grid2' },
          el('div', { class: 'card' }, el('h2', {}, 'Pipeline funnel'), el('div', { class: 'mut', style: 'margin:-6px 0 12px' }, 'Leads that ever reached each stage. The % is the share that moved on from the stage before; the biggest drop is marked in red.'), funnel, ended,
            C.stale ? el('div', { class: 'msg warn', style: 'margin-top:14px' }, nf(C.stale) + (C.stale === 1 ? ' lead has' : ' leads have') + ' had no activity for too long. ', el('a', { class: 'lnk', href: '/pipeline' }, 'Open the CRM board')) : null),
          el('div', { class: 'card' }, el('h2', {}, 'Contact quality'), el('div', { class: 'mut', style: 'margin:-6px 0 14px' }, 'How reachable your leads are. It does not measure fit.'), qt ? seg : el('div', { class: 'empty' }, 'No leads yet.'),
            el('div', { class: 'leg' }, ...qs.map(([k, l, n]) => el('div', {}, el('i', { class: 'sw q-' + k }), el('span', {}, l), el('b', {}, nf(n)), el('span', { class: 'mut' }, qt ? pc(Math.round(1000 * n / qt) / 10) : '')))),
            el('div', { class: 'mut', style: 'margin-top:12px;font-size:13px' }, 'High: score 50+ (a verified email). Medium: 20–49. Low: under 20.'))),
        sec('Sources'),
        el('div', { class: 'card' }, el('h2', {}, 'Compare the four tools'), sourcesTable(d.sources)),
        sec('Enrichment and activity'),
        el('div', { class: 'grid2' },
          el('div', { class: 'card' }, el('h2', {}, 'Lookup results'), el('div', { class: 'mut', style: 'margin:-6px 0 14px' }, nf(cur.lookups) + ' lookups in the last ' + days + ' days'), outcomes(E), el('div', { style: 'margin-top:18px' }, columnChart(E.by_day, 'credits', 'credits', 'Credits used per day'))),
          el('div', { class: 'card' }, columnChart(C.activity_by_day, 'events', 'events', 'CRM activity per day'), el('h2', { style: 'margin-top:18px' }, 'Team'), owners)),
        sec('Outreach'),
        el('div', { class: 'card' }, outreach),
        el('div', { class: 'mut foot' }, 'Credits are counted by LeadDeck (one per lookup that returned data); compare them with your RocketReach account. Figures are computed from LeadDeck’s own data when you open this page. Updated ' + when(d.generated_at) + '.'));
    }
    main.append(el('div', { class: 'row between', style: 'flex-wrap:wrap;gap:12px' }, el('div', {}, el('h1', {}, 'Analytics'), el('div', { class: 'sub' }, 'How your leads, enrichment and pipeline are doing, and how that compares with the period before.')), range), body);
    await load();
  }

  // ------------------------------------------------------------- Login
  function loginPage() {
    const em = el('input', { type: 'email', id: 'em', placeholder: 'you@company.com', autocomplete: 'username' }), pw = el('input', { type: 'password', id: 'pw', placeholder: 'Your password', autocomplete: 'current-password' }), box = el('div', { role: 'alert' });
    const goBtn = el('button', { class: 'go' }, 'Sign in');
    const go = async () => {
      goBtn.classList.add('busy'); msg(box, '');
      try { await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: em.value, password: pw.value }) })
        .then(async r => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || 'Sign-in failed. Check your email and password.'); }); location.href = '/'; }
      catch (e) { msg(box, e.message); goBtn.classList.remove('busy'); }
    };
    goBtn.addEventListener('click', go);
    for (const i of [em, pw]) i.addEventListener('keydown', e => e.key === 'Enter' && go());
    const show = el('button', { type: 'button', class: 'ghost', 'aria-label': 'Show password', onclick: () => { const t = pw.type === 'password'; pw.type = t ? 'text' : 'password'; show.textContent = t ? 'Hide' : 'Show'; show.setAttribute('aria-label', t ? 'Hide password' : 'Show password'); } }, 'Show');
    const sample = (name, role, stage, score) => el('div', { class: 'dc' }, el('div', { class: 'r1' }, avatarFor(name), el('div', {}, el('b', {}, name), el('small', {}, role)), el('span', { style: 'margin-left:auto' }, ringFor(score))), el('div', { class: 'r2' }, stagePill(stage), el('span', { class: 'tag cand' }, 'email verified')));
    document.body.replaceChildren(el('div', { class: 'auth' },
      el('section', { class: 'auth-art' }, el('div', { class: 'brand' }, logo(), 'LeadDeck'),
        el('div', { class: 'deck', 'aria-hidden': 'true' }, sample('Sample Lead A', 'Talent partner', 'new', 20), sample('Sample Lead B', 'HR manager', 'contacted', 50), sample('Sample Lead C', 'Head of hiring', 'ready', 85)),
        el('div', {}, el('h2', {}, 'Every lead, one deck.'), el('p', {}, 'Find people, check their contact details, and follow each conversation until it turns into a meeting.'))),
      el('section', { class: 'auth-form' }, el('div', { class: 'login' }, el('h1', {}, 'Sign in'), el('div', { class: 'mut' }, 'Use the account your admin set up for you.'),
        el('label', { for: 'em' }, 'Email'), em, el('label', { for: 'pw' }, 'Password'), el('div', { class: 'pwwrap' }, pw, show), goBtn, box))));
    em.focus();
  }

  return { boot: p => ({ leads: leadsPage, campaigns: campaignsPage, pipeline: pipelinePage, tasks: tasksPage, analytics: analyticsPage, sync: syncPage, login: loginPage })[p]() };
})();

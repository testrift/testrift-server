(() => {
    const state = { targets: [], collections: [], selectedKey: null, filterOptions: { purposes: [], source_pairs: [] }, profileSelectors: [] };
    const api = async (url, options) => {
        const response = await fetch(url, options);
        const body = await response.json();
        if (!response.ok || !body.success) throw new Error(body.error || `Request failed (${response.status})`);
        return body.data;
    };
    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
    const status = (message, ok = false) => {
        const element = document.getElementById('target-collections-status');
        element.textContent = message;
        element.className = `status${ok ? ' ok' : ''}`;
    };
    const selectedIds = id => [...document.querySelectorAll(`${id} input:checked`)].map(input => Number(input.value));
    const memberRows = selected => state.targets.map(target => `<label class="member-row"><input type="checkbox" value="${target.id}" ${selected.includes(target.id) ? 'checked' : ''}><span><b>${escapeHtml(target.display_name)}</b><small><code>${escapeHtml(target.key)}</code></small></span></label>`).join('') || '<div class="empty-state" style="display:block">No Targets have been reported.</div>';
    const renderTargets = () => {
        const memberships = new Map(state.targets.map(target => [target.id, []]));
        state.collections.forEach(collection => (collection.targets || []).forEach(target => memberships.get(target.id)?.push(collection.display_name)));
        document.getElementById('targets-body').innerHTML = state.targets.map(target => {
            const pending = target.setup_state === 'needs_setup';
            return `<tr data-search="${escapeHtml(`${target.key} ${target.display_name}`.toLowerCase())}"><td><a class="target-name" href="/targets/${encodeURIComponent(target.key)}">${escapeHtml(target.display_name)}</a></td><td><code class="key">${escapeHtml(target.key)}</code></td><td>${escapeHtml((memberships.get(target.id) || []).join(', ') || 'None')}</td><td><span class="target-status ${pending ? 'pending' : 'ready'}"><i class="fas ${pending ? 'fa-triangle-exclamation' : 'fa-circle-check'}"></i>${pending ? 'Setup' : 'Ready'}</span></td></tr>`;
        }).join('');
        document.getElementById('target-count').textContent = state.targets.length;
        document.getElementById('pending-target-count').textContent = state.targets.filter(target => target.setup_state === 'needs_setup').length;
    };
    const renderOnboarding = () => {
        const pending = state.targets.filter(target => target.setup_state === 'needs_setup');
        const panel = document.getElementById('onboarding-panel');
        panel.hidden = pending.length === 0;
        document.getElementById('setup-target').innerHTML = pending.map(target => `<option value="${escapeHtml(target.key)}" data-name="${escapeHtml(target.display_name)}">${escapeHtml(target.key)}</option>`).join('');
        document.getElementById('setup-name').value = pending[0]?.display_name || '';
        document.getElementById('setup-collections').innerHTML = state.collections.map(collection => `<label class="member-row"><input type="checkbox" value="${collection.id}"><span><b>${escapeHtml(collection.display_name)}</b><small><code>${escapeHtml(collection.key)}</code></small></span></label>`).join('') || '<div class="empty-state" style="display:block">Create a Collection before assigning this Target.</div>';
    };
    const renderCollections = () => {
        document.getElementById('collection-count').textContent = state.collections.length;
        document.getElementById('collections-list').innerHTML = state.collections.map(collection => `<div class="collection-row ${collection.key === state.selectedKey ? 'selected' : ''}" data-key="${escapeHtml(collection.key)}"><div><strong>${escapeHtml(collection.display_name)}</strong><small><code>${escapeHtml(collection.key)}</code>${collection.description ? ` · ${escapeHtml(collection.description)}` : ''}</small></div><span class="count-badge">${collection.targets?.length || 0}</span></div>`).join('') || '<div class="empty-state" style="display:block">No Collections yet.</div>';
    };
    const renderEditor = () => {
        const selected = state.collections.find(collection => collection.key === state.selectedKey);
        document.getElementById('collection-name').value = selected?.display_name || '';
        document.getElementById('collection-key').value = selected?.key || '';
        document.getElementById('collection-key').readOnly = Boolean(selected);
        document.getElementById('collection-description').value = selected?.description || '';
        document.getElementById('collection-members').innerHTML = memberRows((selected?.targets || []).map(target => target.id));
        document.getElementById('member-count').textContent = selected ? `(${selected.targets.length} selected)` : '';
        document.getElementById('delete-collection').hidden = !selected;
        document.getElementById('profiles-panel').hidden = !selected;
        document.getElementById('open-collection-summary').href = selected ? `/collections/${encodeURIComponent(selected.key)}` : '#';
        document.getElementById('profile-list').innerHTML = (selected?.profiles || []).map(profile => `<div class="profile-row"><div><strong>${escapeHtml(profile.name)}</strong><small>${escapeHtml(profile.purpose)} · ${profile.window_hours}h · ${escapeHtml(profile.selection_policy)}</small></div>${profile.is_primary ? '<span class="profile-primary-badge">Primary</span>' : ''}</div>`).join('') || '<div class="empty-state" style="display:block">No Summary Profiles yet.</div>';
        renderProfileFilters();
    };
    const renderProfileFilters = () => {
        const purposes = state.filterOptions.purposes;
        const purpose = document.getElementById('profile-purpose');
        purpose.innerHTML = purposes.map(item => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.value)} (${item.run_count})</option>`).join('') || '<option value="nightly">nightly</option>';
        document.getElementById('purpose-help').textContent = purposes.length ? 'Values observed in completed runs for this Collection\'s Targets.' : 'No completed runs yet; choose the expected run purpose.';
        const roles = [...new Set(state.filterOptions.source_pairs.map(item => item.source_role))];
        document.getElementById('profile-role').innerHTML = '<option value="">Source role</option>' + roles.map(role => `<option value="${escapeHtml(role)}">${escapeHtml(role)}</option>`).join('');
        document.getElementById('profile-branch').innerHTML = '<option value="">Branch</option>';
        document.getElementById('profile-branch').disabled = true;
        document.getElementById('add-profile-selector').disabled = true;
        document.getElementById('profile-selector-tokens').innerHTML = state.profileSelectors.map((item, index) => `<span class="filter-token"><code>${escapeHtml(item.source_role)}:${escapeHtml(item.branch)}</code><button type="button" data-remove-selector="${index}" title="Remove filter">×</button></span>`).join('');
        document.getElementById('selector-suggestions').innerHTML = state.filterOptions.source_pairs.map((item, index) => `<button class="filter-suggestion" type="button" data-suggestion="${index}"><code>${escapeHtml(item.source_role)}:${escapeHtml(item.branch)}</code><small>${item.run_count} run${item.run_count === 1 ? '' : 's'}</small></button>`).join('') || '<span class="help">No source metadata has been observed for this Collection yet.</span>';
    };
    const addProfileSelector = selector => {
        if (!selector.source_role || !selector.branch || state.profileSelectors.some(item => item.source_role === selector.source_role && item.branch === selector.branch)) return;
        state.profileSelectors.push(selector);
        renderProfileFilters();
    };
    const filterTargets = () => {
        const query = document.getElementById('target-search').value.trim().toLowerCase();
        let visible = 0;
        document.querySelectorAll('#targets-body tr').forEach(row => {
            const show = row.dataset.search.includes(query);
            row.hidden = !show;
            visible += show;
        });
        document.getElementById('targets-empty').style.display = visible ? 'none' : 'block';
    };
    async function refresh(selectedKey = state.selectedKey) {
        try {
            const [targets, collectionSummaries] = await Promise.all([api('/api/targets'), api('/api/collections')]);
            state.targets = targets;
            state.collections = await Promise.all(collectionSummaries.map(collection => api(`/api/collections/${encodeURIComponent(collection.key)}`)));
            state.selectedKey = state.collections.some(collection => collection.key === selectedKey) ? selectedKey : state.collections[0]?.key || null;
            state.filterOptions = state.selectedKey ? await api(`/api/collections/${encodeURIComponent(state.selectedKey)}/profile-filter-options`) : { purposes: [], source_pairs: [] };
            renderTargets(); renderOnboarding(); renderCollections(); renderEditor(); filterTargets(); status('');
        } catch (error) { status(error.message); }
    }
    document.getElementById('target-search').addEventListener('input', filterTargets);
    document.getElementById('collections-list').addEventListener('click', async event => {
        const row = event.target.closest('[data-key]');
        if (!row) return;
        state.selectedKey = row.dataset.key;
        state.profileSelectors = [];
        state.filterOptions = await api(`/api/collections/${encodeURIComponent(state.selectedKey)}/profile-filter-options`);
        renderCollections(); renderEditor();
    });
    document.getElementById('new-collection').addEventListener('click', () => {
        state.selectedKey = null;
        state.profileSelectors = [];
        state.filterOptions = { purposes: [], source_pairs: [] };
        renderCollections(); renderEditor();
        document.getElementById('collection-name').focus();
    });
    document.getElementById('collection-editor').addEventListener('submit', async event => {
        event.preventDefault();
        const key = document.getElementById('collection-key').value.trim();
        const body = { display_name: document.getElementById('collection-name').value.trim(), description: document.getElementById('collection-description').value.trim() };
        try {
            if (state.selectedKey) await api(`/api/collections/${encodeURIComponent(key)}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
            else await api('/api/collections', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ key, ...body }) });
            const targetIds = selectedIds('#collection-members');
            await api(`/api/collections/${encodeURIComponent(key)}/members`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ target_ids: targetIds }) });
            document.getElementById('saved-indicator').textContent = 'Saved';
            await refresh(key);
        } catch (error) { status(error.message); }
    });
    document.getElementById('profile-editor').addEventListener('submit', async event => {
        event.preventDefault();
        if (!state.selectedKey) return;
        const windowHours = Number(document.getElementById('profile-window').value);
        const body = { name: document.getElementById('profile-name').value.trim(), purpose: document.getElementById('profile-purpose').value, window_hours: windowHours, selectors: state.profileSelectors, is_primary: document.getElementById('profile-primary').checked };
        try {
            await api(`/api/collections/${encodeURIComponent(state.selectedKey)}/profiles`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
            event.target.reset();
            document.getElementById('profile-window').value = '36';
            state.profileSelectors = [];
            document.getElementById('profile-primary').checked = true;
            await refresh(state.selectedKey);
            status('Summary Profile created.', true);
        } catch (error) { status(error.message); }
    });
    document.getElementById('profile-role').addEventListener('change', event => {
        const branches = state.filterOptions.source_pairs.filter(item => item.source_role === event.target.value).map(item => item.branch);
        const branch = document.getElementById('profile-branch');
        branch.innerHTML = '<option value="">Branch</option>' + branches.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
        branch.disabled = branches.length === 0;
        document.getElementById('add-profile-selector').disabled = true;
    });
    document.getElementById('profile-branch').addEventListener('change', () => { document.getElementById('add-profile-selector').disabled = !document.getElementById('profile-branch').value; });
    document.getElementById('add-profile-selector').addEventListener('click', () => addProfileSelector({ source_role: document.getElementById('profile-role').value, branch: document.getElementById('profile-branch').value }));
    document.getElementById('profile-selector-tokens').addEventListener('click', event => {
        const index = event.target.dataset.removeSelector;
        if (index === undefined) return;
        state.profileSelectors.splice(Number(index), 1);
        renderProfileFilters();
    });
    document.getElementById('selector-suggestions').addEventListener('click', event => {
        const index = event.target.closest('[data-suggestion]')?.dataset.suggestion;
        if (index === undefined) return;
        const item = state.filterOptions.source_pairs[Number(index)];
        addProfileSelector({ source_role: item.source_role, branch: item.branch });
    });
    document.getElementById('delete-collection').addEventListener('click', async () => {
        if (!state.selectedKey || !confirm(`Delete ${state.selectedKey}?`)) return;
        try { await api(`/api/collections/${encodeURIComponent(state.selectedKey)}?cascade=true`, { method: 'DELETE' }); await refresh(null); } catch (error) { status(error.message); }
    });
    document.getElementById('setup-target').addEventListener('change', event => { document.getElementById('setup-name').value = event.target.selectedOptions[0]?.dataset.name || ''; });
    document.getElementById('complete-setup').addEventListener('click', async () => {
        const key = document.getElementById('setup-target').value;
        if (!key) return;
        try {
            await api(`/api/targets/${encodeURIComponent(key)}/complete-setup`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ display_name: document.getElementById('setup-name').value.trim(), collection_ids: selectedIds('#setup-collections') }) });
            await refresh(state.selectedKey);
            status('Target setup complete.', true);
        } catch (error) { status(error.message); }
    });
    refresh();
})();

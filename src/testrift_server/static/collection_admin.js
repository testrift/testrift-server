(() => {
    const collectionKey = window.__COLLECTION_KEY__;
    if (!collectionKey) return;

    const state = {
        collection: null,
        targets: [],
        filterOptions: { purposes: [], source_pairs: [] },
        profileSelectors: [],
    };

    const api = async (url, options) => {
        const response = await fetch(url, options);
        const body = await response.json();
        if (!response.ok || !body.success) throw new Error(body.error || `Request failed (${response.status})`);
        return body.data;
    };

    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, character =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]
    );

    const memberLabelHtml = (displayName, key) => {
        const name = escapeHtml(displayName || key);
        const keyHtml = escapeHtml(key);
        if (!key || displayName === key) return `<b>${name}</b>`;
        return `<b>${name}</b><small><code>${keyHtml}</code></small>`;
    };

    const status = (message, ok = false) => {
        const element = document.getElementById('collection-admin-status');
        if (!element) return;
        element.textContent = message || '';
        element.className = `status${ok ? ' ok' : ''}`;
    };

    const selectedIds = selector =>
        [...document.querySelectorAll(`${selector} input:checked`)].map(input => Number(input.value));

    const updateMemberCount = () => {
        const count = document.querySelectorAll('#collection-members input:checked').length;
        document.getElementById('member-count').textContent = `(${count} selected)`;
    };

    const renderMembers = () => {
        const selected = new Set((state.collection?.targets || []).map(target => target.id));
        document.getElementById('collection-members').innerHTML = state.targets.map(target =>
            `<label class="member-row"><input type="checkbox" value="${target.id}" ${selected.has(target.id) ? 'checked' : ''}><span>${memberLabelHtml(target.display_name, target.key)}</span></label>`
        ).join('') || '<div class="help">No Targets have been reported yet.</div>';
        updateMemberCount();
    };

    const renderProfiles = () => {
        document.getElementById('profile-list').innerHTML = (state.collection?.profiles || []).map(profile =>
            `<div class="profile-row"><div><strong>${escapeHtml(profile.name)}</strong><small>${escapeHtml(profile.purpose)} · ${profile.window_hours}h · ${escapeHtml(profile.selection_policy)}</small></div>${profile.is_primary ? '<span class="profile-primary-badge">Primary</span>' : ''}</div>`
        ).join('') || '<div class="help" style="padding:12px 15px">No Summary Profiles yet.</div>';
        renderProfileFilters();
    };

    const renderProfileFilters = () => {
        const purposes = state.filterOptions.purposes;
        const purpose = document.getElementById('profile-purpose');
        purpose.innerHTML = purposes.map(item =>
            `<option value="${escapeHtml(item.value)}">${escapeHtml(item.value)} (${item.run_count})</option>`
        ).join('') || '<option value="nightly">nightly</option>';
        document.getElementById('purpose-help').textContent = purposes.length
            ? "Values observed in completed runs for this Collection's Targets."
            : 'No completed runs yet; choose the expected run purpose.';

        const roles = [...new Set(state.filterOptions.source_pairs.map(item => item.source_role))];
        document.getElementById('profile-role').innerHTML =
            '<option value="">Source role</option>' + roles.map(role =>
                `<option value="${escapeHtml(role)}">${escapeHtml(role)}</option>`
            ).join('');
        document.getElementById('profile-branch').innerHTML = '<option value="">Branch</option>';
        document.getElementById('profile-branch').disabled = true;
        document.getElementById('add-profile-selector').disabled = true;
        document.getElementById('profile-selector-tokens').innerHTML = state.profileSelectors.map((item, index) =>
            `<span class="filter-token"><code>${escapeHtml(item.source_role)}:${escapeHtml(item.branch)}</code><button type="button" data-remove-selector="${index}" title="Remove filter">×</button></span>`
        ).join('');
        document.getElementById('selector-suggestions').innerHTML = state.filterOptions.source_pairs.map((item, index) =>
            `<button class="filter-suggestion" type="button" data-suggestion="${index}"><code>${escapeHtml(item.source_role)}:${escapeHtml(item.branch)}</code><small>${item.run_count} run${item.run_count === 1 ? '' : 's'}</small></button>`
        ).join('') || '<span class="help">No source metadata has been observed for this Collection yet.</span>';
    };

    const addProfileSelector = selector => {
        if (!selector.source_role || !selector.branch) return;
        if (state.profileSelectors.some(item =>
            item.source_role === selector.source_role && item.branch === selector.branch)) return;
        state.profileSelectors.push(selector);
        renderProfileFilters();
    };

    const fillEditor = () => {
        const collection = state.collection;
        document.getElementById('collection-name').value = collection.display_name || '';
        document.getElementById('collection-description').value = collection.description || '';
        document.getElementById('collection-key-display').textContent = collection.key;
        renderMembers();
        renderProfiles();
    };

    async function refresh() {
        try {
            const [collection, targets, filterOptions] = await Promise.all([
                api(`/api/collections/${encodeURIComponent(collectionKey)}`),
                api('/api/targets'),
                api(`/api/collections/${encodeURIComponent(collectionKey)}/profile-filter-options`),
            ]);
            state.collection = collection;
            state.targets = targets;
            state.filterOptions = filterOptions;
            fillEditor();
            status('');
            return collection;
        } catch (error) {
            status(error.message);
            throw error;
        }
    }

    document.getElementById('collection-editor').addEventListener('submit', async event => {
        event.preventDefault();
        const body = {
            display_name: document.getElementById('collection-name').value.trim(),
            description: document.getElementById('collection-description').value.trim(),
        };
        try {
            await api(`/api/collections/${encodeURIComponent(collectionKey)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            await api(`/api/collections/${encodeURIComponent(collectionKey)}/members`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_ids: selectedIds('#collection-members') }),
            });
            document.getElementById('saved-indicator').textContent = 'Saved';
            await refresh();
            if (typeof window.__onCollectionUpdated === 'function') {
                window.__onCollectionUpdated(state.collection);
            }
            status('Collection saved.', true);
            window.__editCollectionModal?.hide();
        } catch (error) {
            status(error.message);
        }
    });

    document.getElementById('profile-editor').addEventListener('submit', async event => {
        event.preventDefault();
        const windowHours = Number(document.getElementById('profile-window').value);
        const body = {
            name: document.getElementById('profile-name').value.trim(),
            purpose: document.getElementById('profile-purpose').value,
            window_hours: windowHours,
            selectors: state.profileSelectors,
            is_primary: document.getElementById('profile-primary').checked,
        };
        try {
            await api(`/api/collections/${encodeURIComponent(collectionKey)}/profiles`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            event.target.reset();
            document.getElementById('profile-window').value = '36';
            state.profileSelectors = [];
            document.getElementById('profile-primary').checked = true;
            await refresh();
            if (typeof window.__onCollectionUpdated === 'function') {
                window.__onCollectionUpdated(state.collection);
            }
            status('Summary Profile created.', true);
        } catch (error) {
            status(error.message);
        }
    });

    document.getElementById('profile-role').addEventListener('change', event => {
        const branches = state.filterOptions.source_pairs
            .filter(item => item.source_role === event.target.value)
            .map(item => item.branch);
        const branch = document.getElementById('profile-branch');
        branch.innerHTML = '<option value="">Branch</option>' + branches.map(value =>
            `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`
        ).join('');
        branch.disabled = branches.length === 0;
        document.getElementById('add-profile-selector').disabled = true;
    });

    document.getElementById('profile-branch').addEventListener('change', () => {
        document.getElementById('add-profile-selector').disabled =
            !document.getElementById('profile-branch').value;
    });

    document.getElementById('add-profile-selector').addEventListener('click', () =>
        addProfileSelector({
            source_role: document.getElementById('profile-role').value,
            branch: document.getElementById('profile-branch').value,
        })
    );

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

    document.getElementById('collection-members').addEventListener('change', updateMemberCount);

    document.getElementById('delete-collection').addEventListener('click', async () => {
        const name = state.collection?.display_name || collectionKey;
        const confirmed = confirm(
            `Delete collection "${name}"?\n\n` +
            'This permanently removes the collection, its membership, and summary profiles. ' +
            'Targets and test runs are kept.\n\nThis cannot be undone.'
        );
        if (!confirmed) return;
        try {
            await api(`/api/collections/${encodeURIComponent(collectionKey)}?cascade=true`, { method: 'DELETE' });
            window.location.href = '/collections';
        } catch (error) {
            status(error.message);
        }
    });

    document.getElementById('open-edit')?.addEventListener('click', () => {
        fillEditor();
        status('');
        window.__editCollectionModal?.show();
    });

    refresh().catch(() => {});
})();

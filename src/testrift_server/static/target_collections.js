(() => {
    const api = async (url, options) => {
        const response = await fetch(url, options);
        const body = await response.json();
        if (!response.ok || !body.success) throw new Error(body.error || `Request failed (${response.status})`);
        return body.data;
    };
    const status = (message, ok = false) => {
        const element = document.getElementById('target-collections-status');
        element.textContent = message;
        element.className = `status${ok ? ' ok' : ''}`;
    };
    const renderTargets = targets => {
        document.getElementById('targets-body').innerHTML = targets.map(target => `<tr><td>${target.key}</td><td>${target.display_name}</td><td>${target.setup_state}</td></tr>`).join('');
        document.getElementById('collection-members').innerHTML = targets.map(target => `<label><input type="checkbox" value="${target.id}"> ${target.key}</label>`).join('');
    };
    const renderCollections = collections => {
        document.getElementById('collections-body').innerHTML = collections.map(collection => `<tr><td>${collection.key}</td><td>${collection.display_name}</td><td>${collection.description || ''}</td></tr>`).join('');
    };
    async function refresh() {
        try {
            const [targets, collections] = await Promise.all([api('/api/targets'), api('/api/collections')]);
            renderTargets(targets); renderCollections(collections); status('');
        } catch (error) { status(error.message); }
    }
    document.getElementById('create-collection').addEventListener('click', async () => {
        const key = document.getElementById('collection-key').value.trim();
        const displayName = document.getElementById('collection-name').value.trim();
        const description = document.getElementById('collection-description').value.trim();
        try {
            await api('/api/collections', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, display_name: displayName, description}) });
            const collection = await api(`/api/collections/${key}`);
            const targetIds = [...document.querySelectorAll('#collection-members input:checked')].map(input => Number(input.value));
            await api(`/api/collections/${key}/members`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({target_ids: targetIds}) });
            status('Collection saved.', true); refresh();
        } catch (error) { status(error.message); }
    });
    refresh();
})();

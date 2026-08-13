(function () {
  const statusEl = document.getElementById('users-status');
  const createStatus = document.getElementById('create-status');
  const createPanel = document.getElementById('create-panel');
  const tableBody = document.querySelector('#users-table tbody');

  function setStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.classList.toggle('text-danger', Boolean(isError));
  }

  async function api(url, options) {
    const response = await fetch(url, Object.assign({
      headers: {'Content-Type': 'application/json'},
    }, options || {}));
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) {
      throw new Error(data.error || ('Request failed (' + response.status + ')'));
    }
    return data;
  }

  function sourceLabel(user) {
    return user.auth_source === 'oidc' ? 'SSO' : 'Local';
  }

  function formatTime(value) {
    if (!value) return '—';
    return value.replace('T', ' ').replace('Z', ' UTC');
  }

  async function loadUsers() {
    setStatus('Loading…', false);
    const data = await api('/api/users');
    tableBody.innerHTML = '';
    data.users.forEach((user) => {
      const row = document.createElement('tr');
      const lockout = user.lockout && user.lockout.locked
        ? '<span class="lockout-badge">Locked until ' + (user.lockout.until || 'window end') + '</span>'
        : '—';
      const local = user.auth_source === 'local';
      row.innerHTML =
        '<td>' + escapeHtml(user.display_name || '') + '</td>' +
        '<td>' + escapeHtml(user.username || user.email || '') + '</td>' +
        '<td>' + sourceLabel(user) + '</td>' +
        '<td>' +
          '<select class="form-select form-select-sm role-select">' +
            '<option value="member"' + (user.role === 'member' ? ' selected' : '') + '>Member</option>' +
            '<option value="admin"' + (user.role === 'admin' ? ' selected' : '') + '>Admin</option>' +
          '</select>' +
        '</td>' +
        '<td>' + (user.enabled ? 'Yes' : 'No') + '</td>' +
        '<td>' + formatTime(user.last_login_at) + '</td>' +
        '<td>' + lockout + '</td>' +
        '<td class="user-actions">' +
          '<button class="btn btn-sm btn-outline-secondary toggle-enabled" type="button">' +
            (user.enabled ? 'Disable' : 'Enable') +
          '</button>' +
          (local ? '<button class="btn btn-sm btn-outline-secondary reset-password" type="button">Reset password</button>' : '') +
          (local && user.lockout && user.lockout.locked
            ? '<button class="btn btn-sm btn-outline-secondary unlock-user" type="button">Clear lockout</button>'
            : '') +
        '</td>';
      row.querySelector('.role-select').addEventListener('change', async (event) => {
        try {
          await api('/api/users/' + user.id, {
            method: 'PUT',
            body: JSON.stringify({role: event.target.value}),
          });
          await loadUsers();
        } catch (error) {
          event.target.value = user.role;
          setStatus(error.message, true);
        }
      });
      row.querySelector('.toggle-enabled').addEventListener('click', async () => {
        try {
          await api('/api/users/' + user.id, {
            method: 'PUT',
            body: JSON.stringify({enabled: !user.enabled}),
          });
          await loadUsers();
        } catch (error) {
          setStatus(error.message, true);
        }
      });
      const resetBtn = row.querySelector('.reset-password');
      if (resetBtn) {
        resetBtn.addEventListener('click', async () => {
          const password = window.prompt('New password for ' + (user.username || user.display_name));
          if (!password) return;
          try {
            await api('/api/users/' + user.id + '/reset-password', {
              method: 'POST',
              body: JSON.stringify({password}),
            });
            setStatus('Password updated for ' + user.username, false);
          } catch (error) {
            setStatus(error.message, true);
          }
        });
      }
      const unlockBtn = row.querySelector('.unlock-user');
      if (unlockBtn) {
        unlockBtn.addEventListener('click', async () => {
          try {
            await api('/api/users/' + user.id + '/unlock', {method: 'POST', body: '{}'});
            await loadUsers();
          } catch (error) {
            setStatus(error.message, true);
          }
        });
      }
      tableBody.appendChild(row);
    });
    setStatus(data.users.length + ' user' + (data.users.length === 1 ? '' : 's'), false);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  document.getElementById('toggle-create').addEventListener('click', () => {
    createPanel.hidden = !createPanel.hidden;
  });
  document.getElementById('cancel-create').addEventListener('click', () => {
    createPanel.hidden = true;
    createStatus.textContent = '';
  });
  document.getElementById('create-user').addEventListener('submit', async (event) => {
    event.preventDefault();
    createStatus.textContent = '';
    try {
      await api('/api/users', {
        method: 'POST',
        body: JSON.stringify({
          username: document.getElementById('new-username').value,
          password: document.getElementById('new-password').value,
          display_name: document.getElementById('new-display-name').value,
          email: document.getElementById('new-email').value,
          role: document.getElementById('new-role').value,
        }),
      });
      event.target.reset();
      createPanel.hidden = true;
      await loadUsers();
    } catch (error) {
      createStatus.textContent = error.message;
    }
  });

  loadUsers().catch((error) => setStatus(error.message, true));
})();

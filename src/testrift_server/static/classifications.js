/**
 * Test Case Classification System
 *
 * Handles:
 * - Classification icons (fixed, regression, flaky)
 * - New test case indicators (yellow star)
 * - Hover tooltips showing history
 */

// Tooltip singleton - only one visible at a time
let activeTooltip = null;

/**
 * Create a classification icon element
 * @param {string} classification - 'fixed', 'regression', or 'flaky'
 * @returns {HTMLElement}
 */
function createClassificationIcon(classification) {
    const icon = document.createElement('span');
    icon.className = `classification-icon classification-${classification}`;

    // Set tooltip text
    const tooltips = {
        'fixed': 'Fixed: Previously failing, now passing (5+ consecutive failures)',
        'regression': 'Regression: Previously passing, now failing (5+ consecutive passes)',
        'flaky': 'Flaky: Intermittent results (>4 transitions in last 10 runs)'
    };

    icon.setAttribute('data-classification-tooltip', tooltips[classification] || classification);

    // Add hover handlers
    icon.addEventListener('mouseenter', showClassificationTooltip);
    icon.addEventListener('mouseleave', hideTooltip);

    return icon;
}

/**
 * Create a new test case indicator (yellow star)
 * @returns {HTMLElement}
 */
function createNewTcIndicator() {
    const indicator = document.createElement('span');
    indicator.className = 'new-tc-indicator';
    indicator.setAttribute('data-classification-tooltip', 'New: This test case was not in the previous run');

    indicator.addEventListener('mouseenter', showClassificationTooltip);
    indicator.addEventListener('mouseleave', hideTooltip);

    return indicator;
}

/**
 * Show classification tooltip
 * @param {MouseEvent} event
 */
function showClassificationTooltip(event) {
    hideTooltip();

    const target = event.currentTarget;
    const text = target.getAttribute('data-classification-tooltip');
    if (!text) return;

    const tooltip = document.createElement('div');
    tooltip.className = 'classification-tooltip';
    tooltip.textContent = text;
    document.body.appendChild(tooltip);

    // Position tooltip
    const rect = target.getBoundingClientRect();
    tooltip.style.left = `${rect.left + window.scrollX}px`;
    tooltip.style.top = `${rect.bottom + window.scrollY + 8}px`;

    // Make visible after positioning
    requestAnimationFrame(() => {
        tooltip.classList.add('visible');
    });

    activeTooltip = tooltip;
}

/**
 * Create a status badge element
 * @param {string} status - Status string (passed, failed, etc.)
 * @returns {HTMLElement} Status badge element
 */
function createStatusBadge(status) {
    const statusBadge = document.createElement('span');
    const statusLower = (status || '').toLowerCase();
    let badgeClass = 'status-badge ';
    let badgeText = '';

    const statusMap = {
        'passed': { class: 'status-passed', text: 'PASSED' },
        'failed': { class: 'status-failed', text: 'FAILED' },
        'error': { class: 'status-error', text: 'ERROR' },
        'skipped': { class: 'status-skipped', text: 'SKIPPED' },
        'aborted': { class: 'status-aborted', text: 'ABORTED' },
        'running': { class: 'status-running', text: 'RUNNING' }
    };

    const statusInfo = statusMap[statusLower];
    if (statusInfo) {
        badgeClass += statusInfo.class;
        badgeText = statusInfo.text;
    } else {
        badgeClass += 'status-unknown';
        badgeText = 'UNKNOWN';
    }

    statusBadge.className = badgeClass;
    statusBadge.textContent = badgeText;
    return statusBadge;
}

/**
 * Create empty state element
 * @returns {HTMLElement} Empty state element
 */
function createEmptyState() {
    const empty = document.createElement('div');
    empty.className = 'history-tooltip-empty';
    empty.textContent = 'No history available';
    return empty;
}

/**
 * Normalize history data (handle both array and object formats)
 * @param {Array|Object} history - History data
 * @returns {Object} Normalized history with previous and latest arrays
 */
function normalizeHistory(history) {
    if (Array.isArray(history)) {
        return { previous: history, latest: history };
    } else if (history && typeof history === 'object') {
        return {
            previous: history.previous || [],
            latest: history.latest || []
        };
    }
    return { previous: [], latest: [] };
}

/**
 * Create tabbed interface for history tooltip
 * @param {HTMLElement} tooltip - Tooltip container
 * @param {string} previousLabel - Label for previous tab
 * @param {string} latestLabel - Label for latest tab
 * @returns {Object} Object with {previousTab, latestTab, previousContent, latestContent}
 */
function createHistoryTabs(tooltip, previousLabel, latestLabel) {
    const tabsContainer = document.createElement('div');
    tabsContainer.className = 'history-tabs';

    const previousTab = document.createElement('button');
    previousTab.className = 'history-tab active';
    previousTab.textContent = previousLabel;
    previousTab.setAttribute('data-tab', 'previous');

    const latestTab = document.createElement('button');
    latestTab.className = 'history-tab';
    latestTab.textContent = latestLabel;
    latestTab.setAttribute('data-tab', 'latest');

    tabsContainer.appendChild(previousTab);
    tabsContainer.appendChild(latestTab);
    tooltip.appendChild(tabsContainer);

    const previousContent = document.createElement('div');
    previousContent.className = 'history-tab-content active';
    previousContent.setAttribute('data-content', 'previous');

    const latestContent = document.createElement('div');
    latestContent.className = 'history-tab-content';
    latestContent.setAttribute('data-content', 'latest');

    const contentWrapper = document.createElement('div');
    contentWrapper.className = 'history-content-wrapper';
    contentWrapper.appendChild(previousContent);
    contentWrapper.appendChild(latestContent);
    tooltip.appendChild(contentWrapper);

    // Tab switching
    previousTab.addEventListener('click', () => {
        previousTab.classList.add('active');
        latestTab.classList.remove('active');
        previousContent.classList.add('active');
        latestContent.classList.remove('active');
    });

    latestTab.addEventListener('click', () => {
        latestTab.classList.add('active');
        previousTab.classList.remove('active');
        latestContent.classList.add('active');
        previousContent.classList.remove('active');
    });

    return { previousTab, latestTab, previousContent, latestContent };
}

/**
 * Render history items into a container
 * @param {HTMLElement} container - Container element
 * @param {Array} history - Array of {status, run_id, run_name, has_log, tc_id} objects
 */
function renderHistoryItems(container, history) {
    container.innerHTML = '';

    if (!history || history.length === 0) {
        container.appendChild(createEmptyState());
        return;
    }

    history.forEach(item => {
        const historyItem = document.createElement('div');
        historyItem.className = 'tc-history-item';

        // Create run name (link if has_log, otherwise plain text)
        const nameContainer = document.createElement('span');
        nameContainer.className = 'tc-history-name';

        // Use tc_id from the history item itself - each run has its own tc_id
        const tcId = item.tc_id;

        if (item.has_log && item.run_id && tcId) {
            const link = document.createElement('a');
            link.href = `/testRun/${item.run_id}/log/${encodeURIComponent(tcId)}.html`;
            link.textContent = item.run_name || item.run_id.substring(0, 8);
            link.className = 'tc-history-link';
            nameContainer.appendChild(link);
        } else {
            const name = document.createElement('span');
            name.textContent = item.run_name || item.run_id.substring(0, 8);
            name.className = 'tc-history-name-plain';
            if (!item.has_log) {
                name.title = 'Log file no longer exists';
            }
            nameContainer.appendChild(name);
        }

        historyItem.appendChild(nameContainer);
        historyItem.appendChild(createStatusBadge(item.status));
        container.appendChild(historyItem);
    });
}

/**
 * Show history tooltip for a status badge
 * @param {HTMLElement} badge - The status badge element
 * @param {Array|Object} history - Array of {status, run_id, run_name, has_log, tc_id} objects, or {previous: [], latest: []}
 * @param {string} title - Tooltip title
 */
function showHistoryTooltip(badge, history, title) {
    hideTooltip();

    const { previous: previousHistory, latest: latestHistory } = normalizeHistory(history);

    const tooltip = document.createElement('div');
    tooltip.className = 'history-tooltip tc-history-tooltip vertical-history-tooltip';

    const titleEl = document.createElement('div');
    titleEl.className = 'history-tooltip-title';
    titleEl.textContent = title || 'Test Case History';
    tooltip.appendChild(titleEl);

    const { previousContent, latestContent } = createHistoryTabs(tooltip, 'Previous Results', 'Latest Results');

    // Render initial content
    renderHistoryItems(previousContent, previousHistory);
    renderHistoryItems(latestContent, latestHistory);

    document.body.appendChild(tooltip);
    positionTooltip(tooltip, badge);
    activeTooltip = tooltip;
}

/**
 * Position tooltip relative to badge element
 */
function positionTooltip(tooltip, badge) {
    // Position tooltip
    const rect = badge.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();

    let left = rect.left + window.scrollX;
    let top = rect.bottom + window.scrollY + 4; // Smaller gap to allow easier mouse movement

    // Adjust if tooltip would go off screen
    if (left + tooltipRect.width > window.innerWidth) {
        left = window.innerWidth - tooltipRect.width - 10;
    }

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;

    // Make visible after positioning
    requestAnimationFrame(() => {
        tooltip.classList.add('visible');
    });

    // Allow tooltip to stay visible when mouse moves to it
    // Use named function so we can remove it if needed
    const tooltipMouseEnter = () => {
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
    };

    const tooltipMouseLeave = () => {
        hideTooltip();
    };

    tooltip.addEventListener('mouseenter', tooltipMouseEnter);
    tooltip.addEventListener('mouseleave', tooltipMouseLeave);

    // Store references for cleanup if needed
    tooltip._tooltipMouseEnter = tooltipMouseEnter;
    tooltip._tooltipMouseLeave = tooltipMouseLeave;
}

/**
 * Create run status badge based on run status and counts
 * @param {Object} run - Run data object
 * @returns {HTMLElement} Status badge element
 */
function createRunStatusBadge(run) {
    const status = (run.status || '').toLowerCase();

    if (status === 'running') {
        return createStatusBadge('running');
    } else if (status === 'aborted') {
        return createStatusBadge('aborted');
    } else if (status === 'finished') {
        const errorCount = run.error_count || 0;
        const failedCount = run.failed_count || 0;
        const passedCount = run.passed_count || 0;
        const skippedCount = run.skipped_count || 0;

        if (errorCount > 0) {
            return createStatusBadge('error');
        } else if (failedCount > 0) {
            return createStatusBadge('failed');
        } else if (skippedCount > 0 && passedCount === 0 && failedCount === 0 && errorCount === 0) {
            return createStatusBadge('skipped');
        } else {
            return createStatusBadge('passed');
        }
    }

    return createStatusBadge('unknown');
}

/**
 * Render run history items into a container
 * @param {HTMLElement} container - Container element
 * @param {Array} runs - Array of run data objects
 */
function renderRunHistoryItems(container, runs) {
    container.innerHTML = '';

    if (!runs || runs.length === 0) {
        container.appendChild(createEmptyState());
        return;
    }

    runs.forEach(run => {
        const item = document.createElement('div');
        item.className = 'run-history-item';

        const nameContainer = document.createElement('span');
        nameContainer.className = 'run-history-name';
        const link = document.createElement('a');
        link.href = `/testRun/${run.run_id}/index.html`;
        link.textContent = run.run_name || run.run_id.substring(0, 8);
        link.className = 'run-history-link';
        nameContainer.appendChild(link);
        item.appendChild(nameContainer);

        item.appendChild(createRunStatusBadge(run));
        container.appendChild(item);
    });
}

/**
 * Show run history tooltip with tabs (previous vs latest)
 * @param {HTMLElement} badge - The status badge element
 * @param {Array|Object} history - Array of run data or {previous: [], latest: []}
 */
function showRunHistoryTooltip(badge, history) {
    hideTooltip();

    const { previous: previousHistory, latest: latestHistory } = normalizeHistory(history);

    const tooltip = document.createElement('div');
    tooltip.className = 'history-tooltip run-history-tooltip';

    const titleEl = document.createElement('div');
    titleEl.className = 'history-tooltip-title';
    titleEl.textContent = 'Test Run History';
    tooltip.appendChild(titleEl);

    const { previousContent, latestContent } = createHistoryTabs(tooltip, 'Previous Test Runs', 'Latest Test Runs');

    renderRunHistoryItems(previousContent, previousHistory);
    renderRunHistoryItems(latestContent, latestHistory);

    document.body.appendChild(tooltip);
    positionTooltip(tooltip, badge);
    activeTooltip = tooltip;
}

/**
 * Hide any active tooltip
 */
function hideTooltip() {
    if (activeTooltip) {
        activeTooltip.remove();
        activeTooltip = null;
    }
    if (hideTimeout) {
        clearTimeout(hideTimeout);
        hideTimeout = null;
    }
}

/**
 * Fetch TC history for hover tooltip
 * @param {string} testCaseId
 * @param {string} groupHash
 * @param {string} currentRunId
 * @returns {Promise<Array>}
 */
async function fetchTcHistory(testCaseId, groupHash, currentRunId, testCaseFullName) {
    try {
        // Use testCaseFullName if provided, otherwise fall back to testCaseId
        const tcName = testCaseFullName || testCaseId;
        let url = `/api/tc-hover-history?tc_full_name=${encodeURIComponent(tcName)}`;
        if (groupHash) {
            url += `&group=${encodeURIComponent(groupHash)}`;
        }
        if (currentRunId) {
            url += `&current_run_id=${encodeURIComponent(currentRunId)}`;
        }

        const response = await fetch(url);
        const data = await response.json();

        if (data.success) {
            // New API returns {previous: [], latest: []}
            if (data.data && typeof data.data === 'object' && 'previous' in data.data) {
                return data.data;
            }
            // Fallback for old API format (array)
            return { previous: data.data || [], latest: data.data || [] };
        } else {
            console.warn('TC history API returned success=false:', data.error);
            return { previous: [], latest: [] };
        }
    } catch (error) {
        console.error('Error fetching TC history:', error);
        return { previous: [], latest: [] };
    }
}

/**
 * Fetch run history for hover tooltip
 * @param {string} groupHash
 * @param {string} currentRunId
 * @returns {Promise<Array>}
 */
async function fetchRunHistory(groupHash, currentRunId) {
    try {
        let url = `/api/run-hover-history/${encodeURIComponent(groupHash)}`;
        if (currentRunId) {
            url += `?current_run_id=${encodeURIComponent(currentRunId)}`;
        }

        const response = await fetch(url);
        const data = await response.json();

        if (data.success) {
            // New API returns {previous: [], latest: []}
            if (data.data && typeof data.data === 'object' && 'previous' in data.data) {
                return data.data;
            }
            // Fallback to array
            return { previous: data.data || [], latest: data.data || [] };
        }
        return { previous: [], latest: [] };
    } catch (error) {
        console.error('Error fetching run history:', error);
        return { previous: [], latest: [] };
    }
}

/**
 * Fetch classifications for all TCs in a run
 * @param {string} runId
 * @returns {Promise<Object>} Map of test_case_id to classification data
 */
async function fetchClassifications(runId) {
    try {
        const response = await fetch(`/api/classifications/${encodeURIComponent(runId)}`);
        const data = await response.json();

        if (data.success) {
            return data.data;
        }
        return {};
    } catch (error) {
        console.error('Error fetching classifications:', error);
        return {};
    }
}

/**
 * Apply classifications to test case elements on a page
 * @param {Object} classifications - Map of test_case_id to classification data
 * @param {Function} getTcElement - Function that returns the element for a TC id
 * @param {Function} getBadgeElement - Function that returns the badge element for a TC id
 */
function applyClassifications(classifications, getTcElement, getBadgeElement) {
    for (const [tcId, data] of Object.entries(classifications)) {
        const tcElement = getTcElement(tcId);
        if (!tcElement) continue;

        // Add classification icon
        if (data.classification) {
            const icon = createClassificationIcon(data.classification);

            // Find the badge and insert after it
            const badge = getBadgeElement(tcId);
            if (badge) {
                badge.parentNode.insertBefore(icon, badge.nextSibling);
            }
        }

        // Add new TC indicator
        if (data.is_new) {
            const indicator = createNewTcIndicator();

            // Find the TC name element and append after it
            const nameElement = tcElement.querySelector('a, span');
            if (nameElement) {
                nameElement.parentNode.insertBefore(indicator, nameElement.nextSibling);
            }
        }

        // Store history for hover
        const badge = getBadgeElement(tcId);
        if (badge && data.history) {
            // Convert array to object format for tabs
            const historyData = Array.isArray(data.history)
                ? { previous: data.history, latest: data.history }
                : data.history;
            setupBadgeHistoryHover(badge, historyData, 'Test Case History', tcId);
        }
    }
}

// Global hide timeout for tooltips
let hideTimeout = null;

/**
 * Setup history hover for a status badge (for use with already-known history data)
 * @param {HTMLElement} badge - The badge element
 * @param {Array} history - The history array
 * @param {string} title - Optional title
 */
function setupBadgeHistoryHover(badge, history, title) {
    badge.classList.add('status-badge-with-history');

    badge.addEventListener('mouseenter', () => {
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
        showHistoryTooltip(badge, history, title);
    });

    badge.addEventListener('mouseleave', () => {
        // Delay hiding to allow moving to tooltip
        hideTimeout = setTimeout(() => {
            hideTooltip();
        }, 150);
    });
}

/**
 * Setup async history hover for a status badge (fetches on hover)
 * @param {HTMLElement} badge - The badge element
 * @param {string} testCaseId - The test case ID
 * @param {string} groupHash - The group hash (optional)
 * @param {string} currentRunId - Current run ID to exclude (optional)
 */
function setupBadgeHistoryHoverAsync(badge, testCaseId, groupHash, currentRunId, testCaseFullName) {
    // Remove any existing event listeners by cloning the element
    // This prevents duplicate listeners that cause tooltip to not close
    if (badge._historyHandlersAttached) {
        // Already has handlers, remove old ones first
        const oldMouseEnter = badge._historyMouseEnter;
        const oldMouseLeave = badge._historyMouseLeave;
        if (oldMouseEnter) {
            badge.removeEventListener('mouseenter', oldMouseEnter);
        }
        if (oldMouseLeave) {
            badge.removeEventListener('mouseleave', oldMouseLeave);
        }
    }

    badge.classList.add('status-badge-with-history');
    let historyCache = null;

    const mouseEnterHandler = async () => {
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
        if (!historyCache) {
            try {
                historyCache = await fetchTcHistory(testCaseId, groupHash, currentRunId, testCaseFullName);
            } catch (e) {
                console.error('Error fetching history for', testCaseId, e);
                historyCache = []; // Set to empty array so we don't retry
            }
        }
        if (historyCache) {
            // Support both array and object formats
            const historyData = Array.isArray(historyCache)
                ? { previous: historyCache, latest: historyCache }
                : historyCache;
            showHistoryTooltip(badge, historyData, 'Test Case History');
        }
    };

    const mouseLeaveHandler = (e) => {
        // Don't hide immediately - check if mouse is moving to tooltip
        // Clear any existing timeout
        if (hideTimeout) {
            clearTimeout(hideTimeout);
        }

        // Check if we're moving to the tooltip
        const relatedTarget = e.relatedTarget;
        if (relatedTarget && activeTooltip) {
            // Check if we're moving to the tooltip or any element within it
            let current = relatedTarget;
            while (current && current !== document.body && current !== document.documentElement) {
                if (current === activeTooltip || activeTooltip.contains(current)) {
                    // Moving to tooltip, don't hide
                    return;
                }
                current = current.parentElement;
            }
        }

        // Schedule hide with delay to allow moving to tooltip
        hideTimeout = setTimeout(() => {
            hideTooltip();
        }, 200);
    };

    badge.addEventListener('mouseenter', mouseEnterHandler);
    badge.addEventListener('mouseleave', mouseLeaveHandler);

    // Store references so we can remove them later if needed
    badge._historyHandlersAttached = true;
    badge._historyMouseEnter = mouseEnterHandler;
    badge._historyMouseLeave = mouseLeaveHandler;
}

/**
 * Setup run history hover for a run status badge
 * @param {HTMLElement} badge - The badge element
 * @param {string} groupHash - The group hash
 * @param {string} currentRunId - Current run ID to exclude (optional)
 */
function setupRunBadgeHistoryHover(badge, groupHash, currentRunId) {
    if (!groupHash) return;

    badge.classList.add('status-badge-with-history');
    let historyCache = null;

    badge.addEventListener('mouseenter', async () => {
        if (hideTimeout) {
            clearTimeout(hideTimeout);
            hideTimeout = null;
        }
        if (!historyCache) {
            historyCache = await fetchRunHistory(groupHash, currentRunId);
        }
        showRunHistoryTooltip(badge, historyCache);
    });

    badge.addEventListener('mouseleave', () => {
        // Delay hiding to allow moving to tooltip
        hideTimeout = setTimeout(() => {
            hideTooltip();
        }, 150);
    });
}

// Export for use in other modules
if (typeof window !== 'undefined') {
    window.Classifications = {
        createClassificationIcon,
        createNewTcIndicator,
        showHistoryTooltip,
        showRunHistoryTooltip,
        hideTooltip,
        fetchTcHistory,
        fetchRunHistory,
        fetchClassifications,
        applyClassifications,
        setupBadgeHistoryHover,
        setupBadgeHistoryHoverAsync,
        setupRunBadgeHistoryHover
    };
}


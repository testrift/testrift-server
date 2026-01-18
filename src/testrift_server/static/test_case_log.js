
// ============================================================================
// CONFIGURATION AND CONSTANTS
// ============================================================================

// Color and styling constants
const GOLDEN_ANGLE = 137.508;

// Device and message processing constants
const DevMode = { Normal: 0, Setup: 1, Teardown: 2 };
const MSG_JOIN_TMO_MS = 10;

// Counter for generating unique IDs
let lblId = 0;

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Generate a unique color for each index using the golden angle.
 * This ensures colors are well-distributed and readable with white text.
 */
function selectColor(i) {
    // Use golden angle to spread hues evenly
    const hue = (i * GOLDEN_ANGLE) % 360;
    // Use 45% saturation and 40% lightness for good contrast with white text
    return `hsl(${hue}, 65%, 40%)`;
}

function generateLabelId() {
    return 'id' + lblId++;
}

function generateBadge(entry, labelName) {
    return `<span class="badge badge-custom" style="background-color: ${entry.color}; font-size: 80%;">${labelName}</span>`;
}

function updateStatusBadge() {
    const statusBadge = document.getElementById('tc-status-badge');
    if (!statusBadge) return;

    const status = templateConfig.testCaseStatus || 'unknown';
    const result = templateConfig.testCaseResult || '';

    let displayStatus, statusClass, showSpinner = false;
    const s = status.toLowerCase();

    if (s === 'passed') {
        displayStatus = 'PASSED';
        statusClass = 'status-passed';
    } else if (s === 'error') {
        displayStatus = 'ERROR';
        statusClass = 'status-error';
    } else if (s === 'failed') {
        displayStatus = 'FAILED';
        statusClass = 'status-failed';
    } else if (s === 'skipped') {
        displayStatus = 'SKIPPED';
        statusClass = 'status-skipped';
    } else if (s === 'aborted') {
        displayStatus = 'ABORTED';
        statusClass = 'status-failed';
    } else if (s === 'running') {
        displayStatus = 'Running';
        statusClass = 'status-running';
        showSpinner = true;
    } else {
        displayStatus = 'UNKNOWN';
        statusClass = 'status-unknown';
    }

    statusBadge.className = `status-badge ${statusClass}`;
    statusBadge.innerHTML = displayStatus + (showSpinner ? '<span class="spinner"></span>' : '');
}

// ============================================================================
// LIVE INDICATOR FUNCTIONS
// ============================================================================

// Define as variables to ensure they're available immediately
var removeLiveIndicator = function() {
    const liveIndicator = document.querySelector('.live-indicator');
    if (liveIndicator) {
        liveIndicator.remove();
    }
};

var addLiveIndicator = function() {
    const tableBody = document.querySelector('#msg_table tbody');
    if (tableBody) {
        // Remove existing indicator first
        removeLiveIndicator();

        const liveRow = document.createElement('tr');
        liveRow.className = 'live-indicator';
        liveRow.innerHTML = '<td colspan="3" style="text-align: left; color: #666; font-style: italic;"><span class="dots"><span class="dot">.</span><span class="dot">.</span><span class="dot">.</span></span></td>';
        tableBody.appendChild(liveRow);
    }
};

// ============================================================================
// DEVICE AND SOURCE LABEL MANAGEMENT
// ============================================================================

function addComponentLabel(compMap, row, labelName) {
    let component;
    if (!compMap.has(labelName)) {
        component = {
            color: selectColor(colorIndex++),
            channels: new Map(),
            mode: DevMode.Normal,
            labelUid: generateLabelId()
        };
        compMap.set(labelName, component);
    } else {
        component = compMap.get(labelName);
    }

    if (row) row.addClass(component.labelUid);
    return [component, generateBadge(component, labelName)];
}

function addChannelLabel(row, parentComp, labelName) {
    let channel;
    if (!parentComp.channels.has(labelName)) {
        channel = {
            color: selectColor(colorIndex++),
            labelUid: generateLabelId()
        };
        parentComp.channels.set(labelName, channel);
    } else {
        channel = parentComp.channels.get(labelName);
    }

    row.addClass(channel.labelUid);
    return generateBadge(channel, labelName);
}

// ============================================================================
// AT COMMAND PROCESSING
// ============================================================================

function generateAtLine(atLookupTable, isRx, atLine) {
    const directionText = isRx ? "Host ◄―― DUT" : "Host ――► DUT";
    const bgClass = isRx ? "bg-rx" : "bg-tx";
    let html = `<span class="badge ${bgClass}">${directionText}</span> <span class="at_cmd">${atLine}</span>`
    atLine = atLine.trim()
    if (atLookupTable !== null) {
        const parsed_cmd = parseAtCommand(atLine);
        const info = atLookupTable[parsed_cmd.commandWithOp];
        if (info) {
            html += `<span class="at_help">${info.brief}`;
            if (parsed_cmd.params.length > 0) {
                const params = [];

                parsed_cmd.params.forEach((paramVal, index) => {
                    const param = info.params[index];
                    if (param) {
                        if (paramVal in param.values) {
                            params.push(`${param.name}: ${param.values[paramVal]} (${paramVal})`);
                        } else {
                            params.push(`${param.name}: ${paramVal}`);
                        }
                    }
                });

                if (params.length > 0) {
                    html += ` [${params.join(", ")}]`;
                }

            }
            html += "</span>";
        }
    }
    return html;
}

// ============================================================================
// ANSI COLOR PARSING
// ============================================================================

/**
 * Parse ANSI escape codes and convert to HTML with inline styles.
 * When ANSI codes are detected, wraps the entire message in a near-black background box.
 */
function parseAnsiColors(text) {
    // Check if text contains ANSI codes
    if (!text.includes('\x1b[') && !text.includes('\u001b[')) {
        return text;
    }

    const ansiRegex = /\x1b\[(\d+(?:;\d+)*)m/g;
    let result = '';
    let lastIndex = 0;
    let currentStyles = [];

    // ANSI color mappings
    const colorMap = {
        30: '#000000', 31: '#cd3131', 32: '#0dbc79', 33: '#e5e510',
        34: '#2472c8', 35: '#bc3fbc', 36: '#11a8cd', 37: '#e5e5e5',
        90: '#666666', 91: '#f14c4c', 92: '#23d18b', 93: '#f5f543',
        94: '#3b8eea', 95: '#d670d6', 96: '#29b8db', 97: '#e5e5e5'
    };

    const bgColorMap = {
        40: '#000000', 41: '#cd3131', 42: '#0dbc79', 43: '#e5e510',
        44: '#2472c8', 45: '#bc3fbc', 46: '#11a8cd', 47: '#e5e5e5',
        100: '#666666', 101: '#f14c4c', 102: '#23d18b', 103: '#f5f543',
        104: '#3b8eea', 105: '#d670d6', 106: '#29b8db', 107: '#e5e5e5'
    };

    text.replace(ansiRegex, (match, codes, offset) => {
        // Add text before this escape code
        if (offset > lastIndex) {
            const textBefore = text.substring(lastIndex, offset);
            if (currentStyles.length > 0) {
                result += `<span style="${currentStyles.join('; ')}">${textBefore}</span>`;
            } else {
                result += textBefore;
            }
        }

        // Process the escape codes
        const codeList = codes.split(';').map(c => parseInt(c));
        for (const code of codeList) {
            if (code === 0) {
                // Reset all styles
                currentStyles = [];
            } else if (code === 1) {
                // Bold
                currentStyles.push('font-weight: bold');
            } else if (code >= 30 && code <= 37 || code >= 90 && code <= 97) {
                // Foreground color
                currentStyles = currentStyles.filter(s => !s.startsWith('color:'));
                currentStyles.push(`color: ${colorMap[code]}`);
            } else if (code >= 40 && code <= 47 || code >= 100 && code <= 107) {
                // Background color
                currentStyles = currentStyles.filter(s => !s.startsWith('background-color:'));
                currentStyles.push(`background-color: ${bgColorMap[code]}`);
            }
        }

        lastIndex = offset + match.length;
        return '';
    });

    // Add remaining text
    if (lastIndex < text.length) {
        const textAfter = text.substring(lastIndex);
        if (currentStyles.length > 0) {
            result += `<span style="${currentStyles.join('; ')}">${textAfter}</span>`;
        } else {
            result += textAfter;
        }
    }

    // Wrap entire result in near-black background box since ANSI was detected
    return `<span style="background-color: #1a1a1a; padding: 2px 4px; border-radius: 3px; display: inline-block;">${result}</span>`;
}

// ============================================================================
// LOG MESSAGE PROCESSING
// ============================================================================

function processLogMessage(d, compMap, chanList, atLookupTable) {
    const msgTableBody = $("#msg_table tbody");

    // --- Teardown collapsible group (based on d.phase === "teardown") ---
    // Only appears if at least one teardown-phase log is actually rendered.
    if (!window.__teardownGroupState) {
        window.__teardownGroupState = {
            headerRow: null,
            collapsed: true
        };
    }
    const teardownGroup = window.__teardownGroupState;

    let msgIsEcho = false;
    const originalTime = d.timestamp || '';
    const time = convertToLocalTime(originalTime);
    let chanName = d.channel || '';
    let compName = d.component || '';
    let messageText = d.message || '';
    const dir = d.dir || null; // Optional explicit direction: 'tx' / 'rx'
    const phase = d.phase || null; // Optional phase marker ("teardown")
    const kind = d.kind || null; // Optional kind marker (e.g. "exception")

    if (compName == "") {
        compName = chanName;
        chanName = "";
    }
    if (compName == "") {
        compName = "Unknown";
    }

    // Direction is determined solely from the explicit dir field when present.
    const isReceived = !!dir && dir.toLowerCase() === 'rx';
    const isTransmitted = !!dir && dir.toLowerCase() === 'tx';

    // Echo detection uses the normalized message text and explicit direction only.
    if (isReceived) {
        if (atMap.has(chanName) && (messageText == atMap.get(chanName))) {
            msgIsEcho = true;
        }
    } else if (isTransmitted) {
        atMap.set(chanName, messageText);
    }
    if (messageText.trim() == "") {
        // Skip empty lines
        return
    }

    // Parse ANSI color codes before filter check
    messageText = parseAnsiColors(messageText);

    // Apply current filter to the clean message text before any HTML processing
    if (currentFilter) {
        const matches = currentFilter.regex.test(messageText);
        const shouldShow = currentFilter.mode === 'include' ? matches : !matches;

        if (!shouldShow) {
            return; // Skip adding this row entirely
        }
    }

    if (msgIsEcho) {
        messageText = `<span style=\"color: #777;\">${messageText}</span>`
    }

    const row = $("<tr>").addClass(chanName);
    if (phase === "teardown") {
        row.addClass("teardown-log-row");
    }
    if (kind === "exception") {
        row.addClass("stacktrace-log-row");
    }

    // Create time cell with original timestamp stored as data attribute
    const timeCell = $(`<td class="fit">`).css("padding", "3px");
    timeCell.attr('data-original-time', originalTime);

    // Display time based on current mode
    if (showDeltaTime) {
        const deltaTime = calculateDeltaTime(originalTime);
        timeCell.text(deltaTime).css({
            'font-family': 'monospace'
        });
    } else {
        timeCell.text(time);
    }

    timeCell.appendTo(row);

    let badges = "";
    const [component, compBadge] = addComponentLabel(compMap, row, compName);
    badges += compBadge;

    if (chanName == "Teardown") {
        component.mode = DevMode.Teardown;
    } else if (component.mode == DevMode.Teardown) {
        // We're currently in the component teardown stage, so add a "Teardown" label
        const teardownBadge = addChannelLabel(row, component, "Teardown");
        badges += teardownBadge
    }

    if (chanName == "Setup") {
        if (messageText.includes("SETUP") && messageText.includes("DONE")) {
            component.mode = DevMode.Normal;
        } else {
            component.mode = DevMode.Setup;
        }
    } else if (component.mode == DevMode.Setup) {
        // We're currently in the component setup stage, so add a "Setup" label
        const setupBadge = addChannelLabel(row, component, "Setup");
        badges += setupBadge
    }

    if (chanName != "") {
        const channelBadge = addChannelLabel(row, component, chanName);
        badges += channelBadge
    }

    if (messageText.includes("ASSERT FAILURE")) {
        badges += addChannelLabel(row, component, "Assert");
    }

    if (badges.includes(">Assert<")) {
        let msgEl = $("<pre>").addClass("bg-danger text-white").append("<code>").html(messageText);
        msgEl.find('a').each(function () {
            $(this).addClass("link-dark")
        });
        messageText = msgEl.prop('outerHTML')
        let li = $(`<li class="list-group-item">`)
        .html(`<strong>Assert failure:</strong> ${messageText}`)
        .css('cursor', 'pointer')
        .on("mousedown", function () {
            const w = $(window);
            $('html,body').animate({ scrollTop: row.offset().top - (w.height() / 2) }, 200);
        });
        $("#test_info_list").append(li);
    }

    $(`<td class="fit">`)
    .html(badges)
    .appendTo(row);

    if (isReceived || isTransmitted) {
        let html = generateAtLine(atLookupTable, isReceived, messageText);
        $("<td>").html(html).appendTo(row);
    } else {
        // Join messages if they come from same source and less than MSG_JOIN_TMO_MS from the first message in the group
        const currentMsgTime = new Date(originalTime);

        // Check if we should join with the previous message
        if (kind !== "exception" && lastMsgGroupStartTime && badges == lastBadges) {
            const diffMs = currentMsgTime - lastMsgGroupStartTime;
            if (!isNaN(diffMs) && diffMs <= MSG_JOIN_TMO_MS) {
                // Join with previous message
                lastTd.append(`<br>${messageText}`);
                return;
            }
        }

        // Start a new message group
        lastMsgGroupStartTime = currentMsgTime;
        lastTd = $(`<td>`).html(messageText).appendTo(row);
    }
    lastBadges = badges

    if (phase === "teardown") {
        if (!teardownGroup.headerRow) {
            const header = $(`
                <tr class="teardown-header-row collapsed">
                    <td colspan="3">
                        <span class="teardown-header-content">
                            <span class="teardown-toggle-icon" role="button" tabindex="0" aria-label="Toggle teardown logs">▸</span>
                            <span class="teardown-header-label">Teardown</span>
                        </span>
                    </td>
                </tr>
            `);

            const applyVisibility = () => {
                const isCollapsed = teardownGroup.collapsed;
                header.toggleClass("collapsed", isCollapsed);
                $("#msg_table tbody tr.teardown-log-row").toggle(!isCollapsed);
                // Update the glyph so it never "disappears" due to pseudo-element/CSS quirks
                header.find(".teardown-toggle-icon").text(isCollapsed ? "▸" : "▾");
            };

            const toggle = () => {
                teardownGroup.collapsed = !teardownGroup.collapsed;
                applyVisibility();
            };

            // Revert to simple behavior: clicking anywhere on the header toggles.
            header.on("click", toggle);
            header.on("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggle();
                }
            });

            teardownGroup.headerRow = header;
            teardownGroup.collapsed = true;
            msgTableBody.append(header);
            applyVisibility();
        }

        row.appendTo(msgTableBody);
        if (teardownGroup.collapsed) {
            row.hide();
        }
    } else {
        row.appendTo(msgTableBody);
    }

    // Always ensure live indicator is at the bottom if test case is still running
    if (testCaseStatus === 'running' || testCaseStatus === 'unknown') {
        // Remove existing indicator first
        removeLiveIndicator();
        // Add it back at the bottom
        addLiveIndicator();
    }
}

// ============================================================================
// UI EVENT HANDLERS
// ============================================================================

function handleCBChange(ev) {
    const checkbox = $(ev.target);
    const labelUid = checkbox.attr("labelUid");
    const isChecked = checkbox.prop("checked");

    // Show/hide rows with this label
    if (isChecked) {
        $(`.${labelUid}`).show();
    } else {
        $(`.${labelUid}`).hide();
    }

    // Update child checkboxes
    checkbox.closest('li').find('ul .tree-checkbox').each(function () {
        $(this).prop('checked', isChecked);
    });
}

function updateChannelList(compMap, chanList) {
    for (let [compName, component] of compMap) {
        const listItem = $("<li>").appendTo(chanList);
        const checkbox = $("<input>")
        .attr("type", "checkbox")
        .prop("checked", true)
        .attr("labelUid", component.labelUid)
        .addClass("tree-checkbox")
        .on("change", handleCBChange);
        const label = `<span class="badge badge-custom" style="background-color: ${component.color}; font-size: 80%;">${compName}</span>`;
        listItem.append(checkbox).append(" ").append(label);
        for (let [chanName, channel] of component.channels) {
            const chanUl = $("<ul>").appendTo(listItem);
            const chanLi = $("<li>").appendTo(chanUl);
            const chanCheckbox = $("<input>")
            .attr("type", "checkbox")
            .prop("checked", true)
            .attr("labelUid", channel.labelUid)
            .addClass("tree-checkbox")
            .on("change", handleCBChange);
            const chanLabel = `<span class="badge badge-custom" style="background-color: ${channel.color}; font-size: 80%;">${chanName}</span>`;
            chanLi.append(chanCheckbox).append(" ").append(chanLabel);
        }
    }
}

// ============================================================================
// TIME UTILITIES
// ============================================================================

function convertToLocalTime(utcTimeString) {
    if (!utcTimeString) return utcTimeString;
    try {
        // Fix malformed timestamp format that has both +00:00 and Z
        let fixedTimeString = utcTimeString;
        if (fixedTimeString.includes('+00:00Z')) {
            // Remove the +00:00 part, keep only Z
            fixedTimeString = fixedTimeString.replace('+00:00Z', 'Z');
        } else if (fixedTimeString.includes('+00:00') && !fixedTimeString.endsWith('Z')) {
            // Replace +00:00 with Z
            fixedTimeString = fixedTimeString.replace('+00:00', 'Z');
        }

        const utcDate = new Date(fixedTimeString);
        if (isNaN(utcDate.getTime())) {
            console.warn('Failed to parse time after fixing format:', fixedTimeString);
            return utcTimeString;
        }
        // Always include milliseconds as ".xxx"
        const localTime = utcDate.toLocaleString();
        const ms = String(utcDate.getMilliseconds()).padStart(3, '0');
        return `${localTime}.${ms}`;
    } catch (e) {
        console.warn('Failed to parse time:', utcTimeString, e);
        return utcTimeString;
    }
}

function calculateDeltaTime(currentTimeString) {
    if (!lastVisibleMessageTime) {
        // Fix malformed timestamp format
        let fixedTimeString = currentTimeString;
        if (fixedTimeString.includes('+00:00Z')) {
            fixedTimeString = fixedTimeString.replace('+00:00Z', 'Z');
        } else if (fixedTimeString.includes('+00:00') && !fixedTimeString.endsWith('Z')) {
            fixedTimeString = fixedTimeString.replace('+00:00', 'Z');
        }
        lastVisibleMessageTime = new Date(fixedTimeString);
        return "0ms";
    }

    // Fix malformed timestamp format
    let fixedTimeString = currentTimeString;
    if (fixedTimeString.includes('+00:00Z')) {
        fixedTimeString = fixedTimeString.replace('+00:00Z', 'Z');
    } else if (fixedTimeString.includes('+00:00') && !fixedTimeString.endsWith('Z')) {
        fixedTimeString = fixedTimeString.replace('+00:00', 'Z');
    }

    const currentTime = new Date(fixedTimeString);
    const delta = currentTime - lastVisibleMessageTime;
    lastVisibleMessageTime = currentTime;

    if (delta < 0) {
        // Handle case where time goes backwards (shouldn't happen but just in case)
        return "0ms";
    } else if (delta < 1000) {
        return `+${delta}ms`;
    } else if (delta < 60000) {
        return `+${(delta / 1000).toFixed(2)}s`;
    } else {
        const minutes = Math.floor(delta / 60000);
        const seconds = ((delta % 60000) / 1000).toFixed(1);
        return `+${minutes}m ${seconds}s`;
    }
}

function toggleTimeDisplay() {
    showDeltaTime = !showDeltaTime;
    const button = document.getElementById('time-display-toggle');
    const messageRows = document.querySelectorAll('#msg_table tbody tr:not(.live-indicator):not(.teardown-header-row)');

    if (showDeltaTime) {
        button.textContent = 'Show Absolute Time';
        lastVisibleMessageTime = null; // Reset for delta calculation

        messageRows.forEach((row, index) => {
            const timeCell = row.cells[0];
            const originalTime = timeCell.getAttribute('data-original-time');

            if (originalTime) {
                const deltaTime = calculateDeltaTime(originalTime);
                timeCell.textContent = deltaTime;
                timeCell.style.fontFamily = 'monospace';
            }
        });
    } else {
        button.textContent = 'Show Delta Time';

        messageRows.forEach((row, index) => {
            const timeCell = row.cells[0];
            const originalTime = timeCell.getAttribute('data-original-time');

            if (originalTime) {
                timeCell.textContent = convertToLocalTime(originalTime);
                timeCell.style.fontFamily = '';
                timeCell.style.color = '';
            }
        });
    }

    // Save user preference
    saveTimeDisplayPreference();
}

function convertTimesToLocal() {
    // Convert start time and end time in info items
    document.querySelectorAll('.info-item').forEach(item => {
        const strong = item.querySelector('strong');
        if (strong && (strong.textContent === 'Start Time' || strong.textContent === 'End Time')) {
            const valueElement = strong.nextSibling;
            if (valueElement && valueElement.textContent) {
                const text = valueElement.textContent.trim();
                if (text && (text.includes('T') || text.includes('Z'))) {
                    valueElement.textContent = ' ' + convertToLocalTime(text);
                }
            }
        }
    });

    // Convert timestamps in the message log table
    // Skip non-log rows like the live indicator and teardown header.
    const isoLike = /^\s*\d{4}-\d{2}-\d{2}T/;
    document.querySelectorAll('#msg_table tbody tr:not(.live-indicator):not(.teardown-header-row) td:first-child').forEach(cell => {
        const originalTime = cell.getAttribute('data-original-time');
        if (originalTime) {
            if (showDeltaTime) {
                const deltaTime = calculateDeltaTime(originalTime);
                cell.textContent = deltaTime;
                cell.style.fontFamily = 'monospace';
            } else {
                cell.textContent = convertToLocalTime(originalTime);
                cell.style.fontFamily = '';
            }
        } else {
            // Fallback for cells without data-original-time attribute
            const text = cell.textContent.trim();
            // Only convert if it actually looks like an ISO timestamp (avoid matching words like "Teardown")
            if (text && (isoLike.test(text) || text.endsWith('Z'))) {
                cell.textContent = convertToLocalTime(text);
            }
        }
    });
}

// Load time display preference from localStorage
function loadTimeDisplayPreference() {
    const savedTimeMode = localStorage.getItem('tcLogTimeDisplayMode');
    if (savedTimeMode === 'delta') {
        showDeltaTime = true;
    }
}

// Save time display preference to localStorage
function saveTimeDisplayPreference() {
    localStorage.setItem('tcLogTimeDisplayMode', showDeltaTime ? 'delta' : 'absolute');
}

// Load sidebar state preference from localStorage
function loadSidebarPreference() {
    const savedSidebarState = localStorage.getItem('tcLogSidebarState');
    if (savedSidebarState === 'collapsed') {
        sidebarCollapsed = true;
    }
}

// Save sidebar state preference to localStorage
function saveSidebarPreference() {
    localStorage.setItem('tcLogSidebarState', sidebarCollapsed ? 'collapsed' : 'expanded');
}

// Apply loaded sidebar preference to UI
function applySidebarPreference() {
    if (sidebarCollapsed) {
        devicesSidebar.classList.add('collapsed');
        mainContent.classList.add('sidebar-collapsed');
    } else {
        devicesSidebar.classList.remove('collapsed');
        mainContent.classList.remove('sidebar-collapsed');
    }
}

// Apply loaded time display preference to UI
function applyTimeDisplayPreference() {
    const button = document.getElementById('time-display-toggle');
    if (showDeltaTime) {
        button.textContent = 'Show Absolute Time';
        // Apply delta time display to existing messages
        const messageRows = document.querySelectorAll('#msg_table tbody tr:not(.live-indicator):not(.teardown-header-row)');
        lastVisibleMessageTime = null; // Reset for delta calculation

        // Sort messages by timestamp to ensure correct delta calculation
        const sortedRows = Array.from(messageRows).sort((a, b) => {
            const timeA = a.cells[0].getAttribute('data-original-time');
            const timeB = b.cells[0].getAttribute('data-original-time');
            if (!timeA || !timeB) return 0;
            return new Date(timeA) - new Date(timeB);
        });

        sortedRows.forEach((row, index) => {
            const timeCell = row.cells[0];
            const originalTime = timeCell.getAttribute('data-original-time');

            if (originalTime) {
                const deltaTime = calculateDeltaTime(originalTime);
                timeCell.textContent = deltaTime;
                timeCell.style.fontFamily = 'monospace';
            }
        });
    } else {
        button.textContent = 'Show Delta Time';
    }
}


// ============================================================================
// EXECUTION TIME TRACKING
// ============================================================================

function startTcExecutionTimer() {
  if (tcExecutionTimer) return; // Already running

  tcExecutionTimer = setInterval(() => {
    updateTcExecutionTime();
  }, 1000); // Update every second
}

function stopTcExecutionTimer() {
  if (tcExecutionTimer) {
    clearInterval(tcExecutionTimer);
    tcExecutionTimer = null;
  }
}

function updateTcExecutionTime() {
  if (tcStartTime) {
    const now = Date.now();
    const elapsed = now - tcStartTime;
    const formattedTime = formatTcExecutionTime(elapsed);

    const executionTimeElement = document.getElementById('tc-execution-time');
    if (executionTimeElement) {
      executionTimeElement.textContent = `⏱️ ${formattedTime}`;
      executionTimeElement.style.display = 'inline';
    }
  }
}

function showTcExecutionTime() {
  const executionTimeElement = document.getElementById('tc-execution-time');
  if (executionTimeElement) {
    executionTimeElement.style.display = 'inline';
  }
}

function hideTcExecutionTime() {
  const executionTimeElement = document.getElementById('tc-execution-time');
  if (executionTimeElement) {
    executionTimeElement.style.display = 'none';
  }
}

function showFinalTcExecutionTime(tc_meta) {
  if (tc_meta && tc_meta.start_time && tc_meta.end_time) {
    // Test case start/end times are local time strings - use them as-is
    const startTimeStr = tc_meta.start_time;
    const endTimeStr = tc_meta.end_time;

    const startTime = new Date(startTimeStr);
    const endTime = new Date(endTimeStr);
    const duration = endTime - startTime;

    if (duration > 0) {
      const formattedTime = formatTcExecutionTime(duration);
      const executionTimeElement = document.getElementById('tc-execution-time');
      if (executionTimeElement) {
        executionTimeElement.textContent = `⏱️ ${formattedTime}`;
        executionTimeElement.style.display = 'inline';
      }
    }
  }
}

function formatTcExecutionTime(milliseconds) {
  const seconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
  } else if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  } else {
    return `${seconds}s`;
  }
}

// ============================================================================
// GLOBAL VARIABLES
// ============================================================================
// These will be initialized when templateConfig is available

let runId, testCaseId, testCaseStatus, compMap, chanList, atLookupTable;
let showDeltaTime, lastVisibleMessageTime, tcStartTime, tcExecutionTimer;
let currentFilter, sidebarCollapsed;
let atMap, lastTd, lastMsgTime, lastBadges, colorIndex, lastMsgGroupStartTime;

// DOM element references
let sidebarToggle, devicesSidebar, mainContent;

// Initialize the test case log when templateConfig is available
function initializeTestCaseLog() {
    // Initialize global variables
    runId = templateConfig.runId;
    testCaseId = templateConfig.testCaseId;
    testCaseStatus = templateConfig.testCaseStatus;
    compMap = new Map();
    chanList = $("#src_list");
    atLookupTable = typeof buildAtSyntaxLookupTable !== 'undefined' ? buildAtSyntaxLookupTable() : null;

    // Time display mode
    showDeltaTime = false;
    lastVisibleMessageTime = null;
    tcStartTime = null;
    tcExecutionTimer = null;

    // Initialize other global variables
    colorIndex = 0;
    atMap = new Map();
    lastTd = null;
    lastMsgTime = NaN;
    lastBadges = "";
    lastMsgGroupStartTime = null;



    // Filter functionality - make currentFilter accessible to processLogMessage
    currentFilter = null;

    // Sidebar toggle functionality
    sidebarCollapsed = false;
    sidebarToggle = document.getElementById('sidebarToggle');
    devicesSidebar = document.getElementById('devicesSidebar');
    mainContent = document.querySelector('.main-content');

    sidebarToggle.addEventListener('click', function() {
        sidebarCollapsed = !sidebarCollapsed;

        if (sidebarCollapsed) {
            devicesSidebar.classList.add('collapsed');
            mainContent.classList.add('sidebar-collapsed');
        } else {
            devicesSidebar.classList.remove('collapsed');
            mainContent.classList.remove('sidebar-collapsed');
        }

        // Save user preference
        saveSidebarPreference();
    });

    // Tab switching functionality
    const devicesTab = document.getElementById('devicesTab');
    const filterTab = document.getElementById('filterTab');
    const devicesContent = document.getElementById('devicesContent');
    const filterContent = document.getElementById('filterContent');

    devicesTab.addEventListener('click', function() {
        devicesTab.classList.add('active');
        filterTab.classList.remove('active');
        devicesContent.classList.add('active');
        filterContent.classList.remove('active');
    });

    filterTab.addEventListener('click', function() {
        filterTab.classList.add('active');
        devicesTab.classList.remove('active');
        filterContent.classList.add('active');
        devicesContent.classList.remove('active');
    });

    // Filter functionality
    const filterInput = document.getElementById('filterInput');
    const filterClearBtn = document.getElementById('filterClearBtn');
    const filterStatus = document.getElementById('filterStatus');
    const caseSensitive = document.getElementById('caseSensitive');
    const fullWord = document.getElementById('fullWord');
    const useWildcards = document.getElementById('useWildcards');
    const filterModeRadios = document.querySelectorAll('input[name="filterMode"]');

    function createFilterRegex(text, caseSensitive, fullWord, useWildcards) {
        if (!text.trim()) return null;

        let pattern = text;

        if (useWildcards) {
            // Convert wildcards to regex
            pattern = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); // Escape special regex chars
            pattern = pattern.replace(/\\\*/g, '.*'); // * becomes .*
            pattern = pattern.replace(/\\\?/g, '.');  // ? becomes .
        } else {
            // Escape special regex characters
            pattern = pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }

        if (fullWord) {
            pattern = '\\b' + pattern + '\\b';
        }

        const flags = caseSensitive ? 'g' : 'gi';
        return new RegExp(pattern, flags);
    }

    function applyFilter() {
        const filterText = filterInput.value.trim();
        const isCaseSensitive = caseSensitive.checked;
        const isFullWord = fullWord.checked;
        const isWildcards = useWildcards.checked;
        const mode = document.querySelector('input[name="filterMode"]:checked').value;

        if (!filterText) {
            currentFilter = null;
            filterStatus.textContent = 'No filter applied';
            filterStatus.className = 'filter-status';
            showAllMessages();
            return;
        }

        currentFilter = {
            regex: createFilterRegex(filterText, isCaseSensitive, isFullWord, isWildcards),
            mode: mode
        };

        if (currentFilter.regex) {
            reapplyFilterToAllMessages();
        }
    }

    function reapplyFilterToAllMessages() {
        if (!currentFilter) {
            showAllMessages();
            return;
        }

        const messageRows = document.querySelectorAll('#msg_table tbody tr');
        let visibleCount = 0;
        let totalCount = messageRows.length;

        messageRows.forEach((row, index) => {
            // Get the original message text from the data attribute or reconstruct it
            let messageText = '';
            const cells = row.cells;
            if (cells.length >= 3) {
                // Try to get clean text from the message cell
                const messageCell = cells[2];
                messageText = messageCell.textContent || messageCell.innerText;
            }

            // Reset regex lastIndex to fix the regex reuse bug
            currentFilter.regex.lastIndex = 0;

            const matches = currentFilter.regex.test(messageText);
            const shouldShow = currentFilter.mode === 'include' ? matches : !matches;

            if (shouldShow) {
                row.style.display = '';
                visibleCount++;
            } else {
                row.style.display = 'none';
            }
        });

        const modeText = currentFilter.mode === 'include' ? 'Including' : 'Excluding';
        const filterText = filterInput.value;
        filterStatus.textContent = `${modeText}: "${filterText}" (${visibleCount}/${totalCount} messages)`;
        filterStatus.className = `filter-status ${currentFilter.mode === 'exclude' ? 'exclude' : 'active'}`;
    }

    function filterMessages() {
        if (!currentFilter) return;

        const messageRows = document.querySelectorAll('#msg_table tbody tr');
        let visibleCount = 0;
        let totalCount = messageRows.length;

        messageRows.forEach(row => {
            const messageText = row.cells[2].textContent; // Message column
            const matches = currentFilter.regex.test(messageText);
            const shouldShow = currentFilter.mode === 'include' ? matches : !matches;

            if (shouldShow) {
                row.style.display = '';
                visibleCount++;
            } else {
                row.style.display = 'none';
            }
        });

        return { visible: visibleCount, total: totalCount };
    }

    function showAllMessages() {
        const messageRows = document.querySelectorAll('#msg_table tbody tr');
        messageRows.forEach(row => {
            row.style.display = '';
        });
    }

    function updateFilterStatus() {
        if (!currentFilter) return;

        const stats = filterMessages();
        const modeText = currentFilter.mode === 'include' ? 'Including' : 'Excluding';
        const filterText = filterInput.value;

        filterStatus.textContent = `${modeText}: "${filterText}" (${stats.visible}/${stats.total} messages)`;
        filterStatus.className = `filter-status ${currentFilter.mode === 'exclude' ? 'exclude' : 'active'}`;
    }

    // Event listeners for filter controls
    filterInput.addEventListener('input', applyFilter);
    filterClearBtn.addEventListener('click', function() {
        filterInput.value = '';
        applyFilter();
    });


    caseSensitive.addEventListener('change', applyFilter);
    fullWord.addEventListener('change', applyFilter);
    useWildcards.addEventListener('change', applyFilter);

    filterModeRadios.forEach(radio => {
        radio.addEventListener('change', applyFilter);
    });



    // Helper functions
    function selectColor(index) {
        const colors = [
            "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
            "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9"
        ];
        return colors[index % colors.length];
    }


    // Status updates
    const ws = templateConfig.liveRun ? new WebSocket(`ws://${location.host}/ws/ui`) : null;

    // Add live indicator when WebSocket connects


    if (templateConfig.liveRun && ws) {
        ws.onopen = () => {
            // Add live indicator if test case is still running
            if (testCaseStatus === 'running' || testCaseStatus === 'unknown') {
                addLiveIndicator();
            }
        };

        ws.onclose = () => {
            removeLiveIndicator();
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                // Stack traces are delivered via the per-test-case /ws/logs socket (not /ws/ui).
                if ((msg.type === 'test_case_started' || msg.type === 'test_case_finished' || msg.type === 'test_case_updated') && msg.run_id === runId && msg.test_case_id === testCaseId) {
                    if (msg.tc_meta) {
                        if (msg.tc_meta.status) {
                            // Update status badge
                            const statusBadge = document.getElementById('tc-status-badge');
                            if (statusBadge) {
                                const status = msg.tc_meta.status.toLowerCase();
                                let statusClass = 'status-unknown';
                                let displayText = status.toUpperCase();

                                if (status === 'running') {
                                    statusClass = 'status-running';
                                    displayText = 'Running';
                                } else if (status === 'passed') {
                                    statusClass = 'status-passed';
                                    displayText = 'PASSED';
                                } else if (status === 'failed') {
                                    statusClass = 'status-failed';
                                    displayText = 'FAILED';
                                } else if (status === 'skipped') {
                                    statusClass = 'status-skipped';
                                    displayText = 'SKIPPED';
                                } else if (status === 'aborted') {
                                    statusClass = 'status-failed';
                                    displayText = 'ABORTED';
                                } else {
                                    statusClass = 'status-unknown';
                                    displayText = 'UNKNOWN';
                                }

                                statusBadge.className = `status-badge ${statusClass}`;
                                statusBadge.textContent = displayText;

                                // Add or remove spinner for running status
                                const existingSpinner = statusBadge.querySelector('.spinner');
                                if (status === 'running') {
                                    if (!existingSpinner) {
                                        const spinner = document.createElement('span');
                                        spinner.className = 'spinner';
                                        statusBadge.appendChild(spinner);
                                    }
                                    // Start real-time execution time tracking
                                    tcStartTime = Date.now();
                                    showTcExecutionTime();
                                    startTcExecutionTimer();
                                    // Add live indicator when test case is running
                                    addLiveIndicator();
                                } else {
                                    if (existingSpinner) {
                                        existingSpinner.remove();
                                    }
                                    // Stop real-time execution time tracking and show final time
                                    stopTcExecutionTimer();
                                    showFinalTcExecutionTime(msg.tc_meta);
                                    // Remove live indicator when test case finishes
                                    removeLiveIndicator();
                                }
                            }

                            // Calculate and display execution time if test case has finished
                            if (status !== 'running' && msg.tc_meta.start_time && msg.tc_meta.end_time) {
                                // Ensure both times are in proper ISO format
                                let startTimeStr = msg.tc_meta.start_time;
                                let endTimeStr = msg.tc_meta.end_time;

                                // Add 'Z' to endTime if it doesn't have timezone info
                                if (!endTimeStr.includes('Z') && !endTimeStr.includes('+') && !endTimeStr.includes('-', 10)) {
                                    endTimeStr += 'Z';
                                }

                                const startTime = new Date(startTimeStr);
                                const endTime = new Date(endTimeStr);
                                const duration = endTime - startTime;

                                if (duration > 0) {
                                    let executionTimeText = '';
                                    if (duration < 1000) {
                                        executionTimeText = `⏱️ ${duration}ms`;
                                    } else if (duration < 60000) {
                                        executionTimeText = `⏱️ ${(duration / 1000).toFixed(2)}s`;
                                    } else {
                                        const minutes = Math.floor(duration / 60000);
                                        const seconds = ((duration % 60000) / 1000).toFixed(1);
                                        executionTimeText = `⏱️ ${minutes}m ${seconds}s`;
                                    }

                                    const executionTimeElement = document.getElementById('tc-execution-time');
                                    if (executionTimeElement) {
                                        executionTimeElement.textContent = executionTimeText;
                                        executionTimeElement.style.display = 'inline';
                                    }
                                }
                            }
                        }

                        // Update end time if available
                        if (msg.tc_meta && msg.tc_meta.end_time) {
                            const infoItems = document.querySelectorAll('.info-item');
                            infoItems.forEach(item => {
                                const strong = item.querySelector('strong');
                                if (strong && strong.textContent === 'End Time') {
                                    const valueElement = strong.nextSibling;
                                    if (valueElement) {
                                        valueElement.textContent = ' ' + convertToLocalTime(msg.tc_meta.end_time);
                                    }
                                }
                            });
                        }
                    }
                }
            } catch(e) { console.error('WS message parse error', e); }
        };
    }


    // For live runs, all logs come via WebSocket (including existing ones)
    // For non-live runs, logs are embedded in HTML
    console.log('Template config for live run detection:', {
        liveRun: templateConfig.liveRun,
        testCaseStatus: templateConfig.testCaseStatus,
        runId: templateConfig.runId,
        testCaseId: templateConfig.testCaseId
    });

    if (templateConfig.liveRun) {
        console.log('Establishing live log WebSocket connection...');
        const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${scheme}//${location.host}/ws/logs/${runId}/${testCaseId}`;
        console.log('WebSocket URL:', wsUrl);
        const wsLogs = new WebSocket(wsUrl);

        wsLogs.onopen = () => {
            console.log('Live log WebSocket connected successfully');
        };

        wsLogs.onerror = (error) => {
            console.error('Live log WebSocket error:', error);
        };

        wsLogs.onclose = (event) => {
            console.log('Live log WebSocket closed:', event.code, event.reason);
        };

        wsLogs.onmessage = (e) => {
            try {
                const d = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
                if (d.type === 'error') { console.warn('Log WS error:', d.message); return; }

                if (d.type === 'exception') {
                    handleIncomingStackTrace({
                        timestamp: d.timestamp,
                        message: d.message,
                        exception_type: d.exception_type,
                        stack_trace: d.stack_trace
                    });
                    return;
                }

                // Process individual log entries (not batches)
                if (d.timestamp && d.message) {
                    const processedLog = {
                        timestamp: d.timestamp,
                        message: d.message,
                        component: d.component || '',
                        channel: d.channel || '',
                        dir: d.dir || null, // Include direction field if present
                        phase: d.phase || null // Optional phase marker from server
                    };
                    processLogMessage(processedLog, compMap, chanList, atLookupTable);

                    // Update channel list after processing new messages
                    chanList.empty();
                    updateChannelList(compMap, chanList);
                }

            } catch(err) { console.error('Error processing log entry:', err); }
        };
    }

    // Load initial logs only for non-live runs (live runs get all logs via WebSocket)
    if (!templateConfig.liveRun) {
        const initialLogs = templateConfig.initialLogs;
        const initialTraces = templateConfig.stackTraces || [];

        // Clear the table first to avoid duplication
        $("#msg_table tbody").empty();

        const merged = [];

        // Normal logs
        (Array.isArray(initialLogs) ? initialLogs : []).forEach(logEntry => {
            merged.push({
                timestamp: logEntry.timestamp, // Keep original timestamp for delta calculation
                message: logEntry.message,
                component: logEntry.component || '',
                channel: logEntry.channel || '',
                dir: logEntry.dir || null,
                phase: logEntry.phase || null,
                kind: null
            });
        });

        // Stack traces as message-log entries (same layout as the stack trace cards, minus timestamp)
        (Array.isArray(initialTraces) ? initialTraces : []).forEach(trace => {
            merged.push({
                timestamp: trace.timestamp || '',
                message: createInlineStackTraceHtml(trace),
                component: 'NUnit',
                channel: 'Exception',
                dir: null,
                phase: null,
                kind: 'exception'
            });
        });

        // Sort by timestamp so exceptions appear in the right place in the log table
        merged.sort((a, b) => {
            try { return new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(); } catch { return 0; }
        });

        merged.forEach(entry => processLogMessage(entry, compMap, chanList, atLookupTable));
    } else {
        // For live runs, clear the table since WebSocket will populate it
        $("#msg_table tbody").empty();
    }




    // Load preferences and apply them
    loadTimeDisplayPreference();
    loadSidebarPreference();
    applyTimeDisplayPreference();
    applySidebarPreference();

    // Convert times to local time on page load
    convertTimesToLocal();

    // Calculate and display execution time on page load if test case has finished
    function calculateInitialExecutionTime() {
        const tcStatus = templateConfig.testCaseStatus;
        const tcStartTime = templateConfig.testCaseStartTime;
        const tcEndTime = templateConfig.testCaseEndTime;

        if (tcStatus && tcStatus.toLowerCase() !== 'running' && tcStartTime && tcEndTime) {
            // Test case start/end times are local time strings - use them as-is
            const startTimeStr = tcStartTime;
            const endTimeStr = tcEndTime;

            const startTime = new Date(startTimeStr);
            const endTime = new Date(endTimeStr);
            const duration = endTime - startTime;

            if (duration > 0) {
                let executionTimeText = '';
                if (duration < 1000) {
                    executionTimeText = `⏱️ ${duration}ms`;
                } else if (duration < 60000) {
                    executionTimeText = `⏱️ ${(duration / 1000).toFixed(2)}s`;
                } else {
                    const minutes = Math.floor(duration / 60000);
                    const seconds = ((duration % 60000) / 1000).toFixed(1);
                    executionTimeText = `⏱️ ${minutes}m ${seconds}s`;
                }

                const executionTimeElement = document.getElementById('tc-execution-time');
                if (executionTimeElement) {
                    executionTimeElement.textContent = executionTimeText;
                    executionTimeElement.style.display = 'inline';
                }
            }
        }
    }

    function convertTcTimestampsToLocal() {
        // Convert start time display
        const startTimeDisplay = document.getElementById('tc-start-time-display');
        if (startTimeDisplay && startTimeDisplay.textContent) {
            const text = startTimeDisplay.textContent.trim();
            if (text && text !== 'N/A' && (text.includes('T') || text.includes('Z') || text.includes('-'))) {
                startTimeDisplay.textContent = convertToLocalTime(text);
            }
        }

        // Convert end time display
        const endTimeDisplay = document.getElementById('tc-end-time-display');
        if (endTimeDisplay && endTimeDisplay.textContent) {
            const text = endTimeDisplay.textContent.trim();
            if (text && text !== 'N/A' && (text.includes('T') || text.includes('Z') || text.includes('-'))) {
                endTimeDisplay.textContent = convertToLocalTime(text);
            }
        }
    }


    calculateInitialExecutionTime();

    // Convert timestamps to local time
    convertTcTimestampsToLocal();

    // Add live indicator if test case is running
    if (testCaseStatus === 'running' || testCaseStatus === 'unknown') {
        addLiveIndicator();

        // Start real-time execution time tracking if test is running
        if (testCaseStatus === 'running') {
            showTcExecutionTime();
            const tcStartTimeStr = templateConfig.testCaseStartTime;
            if (tcStartTimeStr) {
                tcStartTime = new Date(tcStartTimeStr).getTime();
                startTcExecutionTimer();
            } else {
                // If no start time available, use current time
                tcStartTime = Date.now();
                startTcExecutionTimer();
            }
        }
    }

    // Add event listener for time display toggle
    document.getElementById('time-display-toggle').addEventListener('click', toggleTimeDisplay);

    // Update source list after loading initial logs
    chanList.empty();
    updateChannelList(compMap, chanList);

    // Update status badge
    updateStatusBadge();
}

// ============================================================================
// ATTACHMENT MANAGEMENT
// ============================================================================

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatFileIcon(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const iconMap = {
        'pdf': '📄',
        'doc': '📄', 'docx': '📄',
        'txt': '📝', 'log': '📝',
        'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'bmp': '🖼️',
        'mp4': '🎥', 'avi': '🎥', 'mov': '🎥',
        'mp3': '🎵', 'wav': '🎵',
        'zip': '📦', 'rar': '📦', '7z': '📦',
        'xls': '📊', 'xlsx': '📊', 'csv': '📊',
        'xml': '📋', 'json': '📋',
        'html': '🌐', 'htm': '🌐',
        'exe': '⚙️', 'msi': '⚙️',
        'dll': '🔧', 'so': '🔧'
    };
    return iconMap[ext] || '📎';
}

function renderAttachmentItem(attachment) {
    const icon = formatFileIcon(attachment.filename);
    const size = formatFileSize(attachment.size);
    const runId = templateConfig.runId;
    const testCaseId = templateConfig.testCaseId;
    const downloadUrl = `/api/attachments/${runId}/${testCaseId}/download/${encodeURIComponent(attachment.filename)}`;

    return `
        <a href="${downloadUrl}" class="attachment-item" data-filename="${attachment.filename}" title="Click to download ${attachment.filename}">
            <div class="attachment-icon">${icon}</div>
            <div class="attachment-name">${attachment.filename}</div>
            <div class="attachment-size">(${size})</div>
        </a>
    `;
}

function loadAttachments() {
    if (!templateConfig) {
        console.error('templateConfig not available');
        return;
    }

    const attachmentsList = document.getElementById('attachmentsList');

    // In offline mode (zip file), use embedded attachment data
    if (!templateConfig.serverMode) {
        loadOfflineAttachments();
        return;
    }

    const runId = templateConfig.runId;
    const testCaseId = templateConfig.testCaseId;

    fetch(`/api/attachments/${runId}/${testCaseId}/list`)
        .then(response => response.json())
        .then(data => {
            if (data.attachments && data.attachments.length > 0) {
                const html = data.attachments.map(renderAttachmentItem).join('');
                attachmentsList.innerHTML = html;
            } else {
                attachmentsList.innerHTML = '<div class="no-attachments">No attachments found for this test case.</div>';
            }
        })
        .catch(error => {
            console.error('Error loading attachments:', error);
            attachmentsList.innerHTML = '<div class="no-attachments">Error loading attachments.</div>';
        });
}

function loadOfflineAttachments() {
    const attachmentsList = document.getElementById('attachmentsList');

    if (templateConfig.attachments && templateConfig.attachments.length > 0) {
        const html = templateConfig.attachments.map(renderOfflineAttachmentItem).join('');
        attachmentsList.innerHTML = html;
    } else {
        attachmentsList.innerHTML = '<div class="no-attachments">No attachments found for this test case.</div>';
    }
}

function renderOfflineAttachmentItem(attachment) {
    const icon = formatFileIcon(attachment.filename);
    const size = formatFileSize(attachment.size);
    const testCaseId = templateConfig.testCaseId;
    // Create relative path to attachment in zip file
    const downloadUrl = `../attachments/${testCaseId}/${encodeURIComponent(attachment.filename)}`;

    return `
        <a href="${downloadUrl}" class="attachment-item" data-filename="${attachment.filename}" title="Click to download ${attachment.filename}">
            <div class="attachment-icon">${icon}</div>
            <div class="attachment-name">${attachment.filename}</div>
            <div class="attachment-size">(${size})</div>
        </a>
    `;
}

// downloadAttachment function removed - now using direct links

// Removed toggleAttachments function - attachments are now always visible in test details

function initializeAttachments() {
    // Automatically load attachments when page loads
    loadAttachments();
}

// ============================================================================
// STACK TRACE MANAGEMENT
// ============================================================================

const stackTraceList = document.getElementById('stackTraceList');
const stackTraceEmptyState = document.getElementById('stackTraceEmptyState');

function refreshStackTraceEmptyState() {
    if (!stackTraceEmptyState) return;
    const hasItems = stackTraceList && stackTraceList.children.length > 0;
    stackTraceEmptyState.style.display = hasItems ? 'none' : 'block';
}

function createStackTraceCard(trace, includeTimestamp = true) {
    const card = document.createElement('div');
    card.className = 'stack-trace-card';

    const header = document.createElement('div');
    header.className = 'stack-trace-card-header';

    const title = document.createElement('div');
    title.className = 'stack-trace-title';
    const isError = !!trace.is_error;
    if (isError) {
        title.textContent = trace.exception_type || 'Error';
    } else {
        title.textContent = trace.exception_type || 'Failure';
    }

    header.appendChild(title);
    if (includeTimestamp) {
        const timestamp = document.createElement('div');
        timestamp.className = 'stack-trace-timestamp';
        timestamp.textContent = convertToLocalTime(trace.timestamp || '');
        header.appendChild(timestamp);
    }
    card.appendChild(header);

    if (trace.message) {
        const messageEl = document.createElement('div');
        messageEl.className = 'stack-trace-message';
        messageEl.textContent = trace.message;
        card.appendChild(messageEl);
    }

    const pre = document.createElement('pre');
    pre.className = 'stack-trace-body';
    const code = document.createElement('code');

    // stack_trace is expected to be a list of strings (one per stack frame/line)
    let stackText = '';
    if (Array.isArray(trace.stack_trace)) {
        stackText = trace.stack_trace.join('\n');
    } else if (typeof trace.stack_trace === 'string' && trace.stack_trace.trim().length > 0) {
        stackText = trace.stack_trace;
    }

    code.textContent = stackText || 'No exception details provided.';
    pre.appendChild(code);
    card.appendChild(pre);

    return card;
}

function createInlineStackTraceHtml(trace) {
    // Same layout as the stack trace cards, except WITHOUT the timestamp (table already has a time column).
    const card = createStackTraceCard(trace, false);
    card.classList.add('inline-in-log');
    return card.outerHTML;
}

function appendStackTrace(trace, suppressRefresh = false) {
    if (!stackTraceList || !trace) return;
    const card = createStackTraceCard(trace, true);
    stackTraceList.appendChild(card);
    if (!suppressRefresh) {
        refreshStackTraceEmptyState();
    }
}

function initializeStackTraces(initialTraces) {
    if (!stackTraceList) return;
    stackTraceList.innerHTML = '';

    if (Array.isArray(initialTraces)) {
        initialTraces.forEach(trace => appendStackTrace(trace, true));
    }

    refreshStackTraceEmptyState();
}

function handleIncomingStackTrace(trace) {
    appendStackTrace(trace);

    // Also render stack traces into the main message log table.
    // Important: do NOT include timestamps inside the message body (time column already exists).
    try {
        const processedLog = {
            timestamp: trace.timestamp || '',
            message: createInlineStackTraceHtml(trace),
            component: 'NUnit',
            channel: 'Exception',
            dir: null,
            phase: null,
            kind: 'exception'
        };

        processLogMessage(processedLog, compMap, chanList, atLookupTable);
        chanList.empty();
        updateChannelList(compMap, chanList);
    } catch (e) {
        console.warn('Failed to add stack trace to message log:', e);
    }
}

function initializeStackTraceSection() {
    const traces = templateConfig.stackTraces || [];
    initializeStackTraces(traces);
}

// Initialize when DOM is ready and templateConfig is available
if (typeof templateConfig !== 'undefined') {
    initializeTestCaseLog();
    initializeAttachments();
    initializeStackTraceSection();
} else {
    // Wait for templateConfig to be defined
    document.addEventListener('DOMContentLoaded', function() {
        if (typeof templateConfig !== 'undefined') {
            initializeTestCaseLog();
            initializeAttachments();
            initializeStackTraceSection();
        }
    });
}

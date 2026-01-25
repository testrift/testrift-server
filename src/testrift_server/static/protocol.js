/**
 * TestRift Compact Protocol Decoder for JavaScript
 *
 * This module decodes the compact binary protocol used for log entries.
 * It mirrors the Python protocol.py constants and decoding logic.
 */

// =============================================================================
// MESSAGE TYPES (t field)
// =============================================================================
const MSG_TYPE = {
    RUN_STARTED: 1,
    RUN_STARTED_RESPONSE: 2,
    TEST_CASE_STARTED: 3,
    LOG_BATCH: 4,
    EXCEPTION: 5,
    TEST_CASE_FINISHED: 6,
    RUN_FINISHED: 7,
    BATCH: 8,
    HEARTBEAT: 9,
    STRING_TABLE: 10,
};

const MSG_TYPE_NAMES = {
    [MSG_TYPE.RUN_STARTED]: 'run_started',
    [MSG_TYPE.RUN_STARTED_RESPONSE]: 'run_started_response',
    [MSG_TYPE.TEST_CASE_STARTED]: 'test_case_started',
    [MSG_TYPE.LOG_BATCH]: 'log_batch',
    [MSG_TYPE.EXCEPTION]: 'exception',
    [MSG_TYPE.TEST_CASE_FINISHED]: 'test_case_finished',
    [MSG_TYPE.RUN_FINISHED]: 'run_finished',
    [MSG_TYPE.BATCH]: 'batch',
    [MSG_TYPE.HEARTBEAT]: 'heartbeat',
    [MSG_TYPE.STRING_TABLE]: 'string_table',
};

// =============================================================================
// STATUS CODES (s field)
// =============================================================================
const STATUS = {
    RUNNING: 1,
    PASSED: 2,
    FAILED: 3,
    SKIPPED: 4,
    ABORTED: 5,
    FINISHED: 6,
};

const STATUS_NAMES = {
    [STATUS.RUNNING]: 'running',
    [STATUS.PASSED]: 'passed',
    [STATUS.FAILED]: 'failed',
    [STATUS.SKIPPED]: 'skipped',
    [STATUS.ABORTED]: 'aborted',
    [STATUS.FINISHED]: 'finished',
};

// =============================================================================
// DIRECTION CODES (d field)
// =============================================================================
const DIR = {
    TX: 1,
    RX: 2,
};

const DIR_NAMES = {
    [DIR.TX]: 'tx',
    [DIR.RX]: 'rx',
};

// =============================================================================
// PHASE CODES (p field)
// =============================================================================
const PHASE = {
    TEARDOWN: 1,
};

const PHASE_NAMES = {
    [PHASE.TEARDOWN]: 'teardown',
};

// =============================================================================
// FIELD KEYS
// =============================================================================
const F = {
    TYPE: 't',
    RUN_ID: 'r',
    RUN_NAME: 'n',
    STATUS: 's',
    TIMESTAMP: 'ts',
    ERROR: 'err',
    TC_FULL_NAME: 'f',
    TC_ID: 'i',
    MESSAGE: 'm',
    COMPONENT: 'c',
    CHANNEL: 'ch',
    DIR: 'd',
    PHASE: 'p',
    ENTRIES: 'e',
    EVENTS: 'ev',
    EVENT_TYPE: 'et',
    EXCEPTION_TYPE: 'xt',
    STACK_TRACE: 'st',
    IS_ERROR: 'ie',
};

// =============================================================================
// UTILITY FUNCTIONS
// =============================================================================

/**
 * Convert milliseconds since epoch to ISO 8601 timestamp string.
 * @param {number} ms - Milliseconds since Unix epoch
 * @returns {string} ISO 8601 formatted timestamp
 */
function msToTimestamp(ms) {
    if (typeof ms !== 'number') return ms;
    return new Date(ms).toISOString();
}

/**
 * Decode an interned string value.
 * - If value is [id, string]: Register string in table and return it
 * - If value is an integer: Look up in table
 * - Otherwise return as-is
 *
 * @param {number|Array|string} value - The interned value
 * @param {Object} stringTable - Map from string ID to string value
 * @returns {string} The decoded string
 */
function decodeInternedString(value, stringTable) {
    if (value == null) return '';

    // First occurrence: [id, string]
    if (Array.isArray(value) && value.length === 2) {
        const [id, str] = value;
        stringTable[id] = str;
        return str;
    }

    // Subsequent occurrence: just the ID
    if (typeof value === 'number') {
        return stringTable[value] || `<unknown:${value}>`;
    }

    // Already a string (shouldn't happen in compact protocol)
    return String(value);
}

// =============================================================================
// DECODERS
// =============================================================================

/**
 * Protocol decoder with per-instance string table.
 */
class ProtocolDecoder {
    constructor() {
        this.stringTable = {};
    }

    /**
     * Load a string table received from server.
     * @param {Object} strings - Map from string ID to string value
     */
    loadStringTable(strings) {
        if (strings && typeof strings === 'object') {
            for (const [id, str] of Object.entries(strings)) {
                this.stringTable[parseInt(id, 10)] = str;
            }
        }
    }

    /**
     * Decode a compact log entry to UI format.
     * @param {Object} entry - Raw compact entry
     * @returns {Object} Decoded entry with long keys
     */
    decodeLogEntry(entry) {
        if (!entry || typeof entry !== 'object') {
            return null;
        }

        const result = {};

        // Timestamp: convert ms to ISO string
        const ts = entry[F.TIMESTAMP];
        if (ts != null) {
            result.timestamp = msToTimestamp(ts);
        }

        // Message
        if (F.MESSAGE in entry) {
            result.message = entry[F.MESSAGE] || '';
        }

        // Component (interned)
        const comp = entry[F.COMPONENT];
        if (comp != null) {
            result.component = decodeInternedString(comp, this.stringTable);
        } else {
            result.component = '';
        }

        // Channel (interned)
        const ch = entry[F.CHANNEL];
        if (ch != null) {
            result.channel = decodeInternedString(ch, this.stringTable);
        } else {
            result.channel = '';
        }

        // Direction
        const dir = entry[F.DIR];
        if (dir != null) {
            result.dir = DIR_NAMES[dir] || null;
        } else {
            result.dir = null;
        }

        // Phase
        const phase = entry[F.PHASE];
        if (phase != null) {
            result.phase = PHASE_NAMES[phase] || null;
        } else {
            result.phase = null;
        }

        return result;
    }

    /**
     * Decode a WebSocket message (could be log entry, exception, or control message).
     * @param {Object} data - Raw decoded MessagePack data
     * @returns {Object} Decoded message with type and normalized fields
     */
    decodeMessage(data) {
        if (!data || typeof data !== 'object') {
            return { type: 'error', message: 'Invalid message' };
        }

        const msgType = data[F.TYPE];

        // Already has 'type' field (legacy or server-decoded format)
        if (data.type && typeof data.type === 'string') {
            // Handle string_table message to pre-populate decoder
            if (data.type === 'string_table' && data.strings) {
                this.loadStringTable(data.strings);
            }
            return data;
        }

        // Handle different message types
        const typeName = MSG_TYPE_NAMES[msgType];

        switch (msgType) {
            case MSG_TYPE.EXCEPTION:
                return this._decodeException(data);

            case MSG_TYPE.LOG_BATCH:
                return this._decodeLogBatch(data);

            default:
                // For other types, just decode with long keys
                return this._decodeGenericMessage(data, typeName);
        }
    }

    _decodeException(data) {
        const result = {
            type: 'exception',
            timestamp: data[F.TIMESTAMP] != null ? msToTimestamp(data[F.TIMESTAMP]) : '',
            message: data[F.MESSAGE] || '',
            exception_type: data[F.EXCEPTION_TYPE] || '',
            stack_trace: data[F.STACK_TRACE] || [],
            is_error: data[F.IS_ERROR] || false,
        };
        return result;
    }

    _decodeLogBatch(data) {
        const entries = data[F.ENTRIES] || [];
        const decoded = entries.map(e => this.decodeLogEntry(e));
        return {
            type: 'log_batch',
            entries: decoded.filter(e => e != null),
        };
    }

    _decodeGenericMessage(data, typeName) {
        const result = { type: typeName || 'unknown' };

        // Map common fields
        if (data[F.TIMESTAMP] != null) {
            result.timestamp = msToTimestamp(data[F.TIMESTAMP]);
        }
        if (data[F.MESSAGE] != null) {
            result.message = data[F.MESSAGE];
        }
        if (data[F.STATUS] != null) {
            result.status = STATUS_NAMES[data[F.STATUS]] || data[F.STATUS];
        }
        if (data[F.TC_ID] != null) {
            result.tc_id = data[F.TC_ID];
        }
        if (data[F.TC_FULL_NAME] != null) {
            result.tc_full_name = data[F.TC_FULL_NAME];
        }

        return result;
    }

    /**
     * Clear the string table (call when switching test cases).
     */
    reset() {
        this.stringTable = {};
    }
}

// Export for use in other modules
if (typeof window !== 'undefined') {
    window.ProtocolDecoder = ProtocolDecoder;
    window.TRProtocol = {
        MSG_TYPE,
        MSG_TYPE_NAMES,
        STATUS,
        STATUS_NAMES,
        DIR,
        DIR_NAMES,
        PHASE,
        PHASE_NAMES,
        F,
        msToTimestamp,
        decodeInternedString,
    };
}

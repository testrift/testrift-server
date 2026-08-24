(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    root.LogGrouping = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
    "use strict";

    function key(sourceBadges, direction) {
        return `${sourceBadges}\u0000${(direction || "").toLowerCase()}`;
    }

    function shouldJoin(previous, current, timeoutMs) {
        if (!previous || current.kind === "exception") return false;
        if (previous.key !== current.key) return false;

        const diffMs = current.time - previous.startTime;
        return Number.isFinite(diffMs) && diffMs >= 0 && diffMs <= timeoutMs;
    }

    return { key, shouldJoin };
});

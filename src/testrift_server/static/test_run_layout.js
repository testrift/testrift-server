/**
 * Test-run tree/list layout helpers.
 * Aligns PASSED/FAILED badges after the longest TC name and builds hover titles.
 */
(function (global) {
    "use strict";

    function measureContentWidth(el) {
        if (!el) return 0;
        const nodes = [el].concat(Array.prototype.slice.call(el.querySelectorAll("*")));
        const prev = nodes.map(function (node) {
            return {
                node: node,
                maxWidth: node.style.maxWidth,
                overflow: node.style.overflow,
                overflowX: node.style.overflowX,
                whiteSpace: node.style.whiteSpace,
                width: node.style.width,
                flexShrink: node.style.flexShrink
            };
        });
        nodes.forEach(function (node) {
            node.style.maxWidth = "none";
            node.style.overflow = "visible";
            node.style.overflowX = "visible";
            node.style.whiteSpace = "nowrap";
            node.style.width = "auto";
            node.style.flexShrink = "0";
        });
        const width = Math.ceil(el.scrollWidth || el.getBoundingClientRect().width || 0);
        prev.forEach(function (p) {
            p.node.style.maxWidth = p.maxWidth;
            p.node.style.overflow = p.overflow;
            p.node.style.overflowX = p.overflowX;
            p.node.style.whiteSpace = p.whiteSpace;
            p.node.style.width = p.width;
            p.node.style.flexShrink = p.flexShrink;
        });
        return width;
    }

    function fullNameTooltip(fullName, existingTitle) {
        const name = String(fullName || "").replace(/&quot;/g, '"');
        const existing = existingTitle || "";
        if (existing.indexOf("cleaned up") !== -1) {
            return name ? (name + "\n" + existing) : existing;
        }
        return name || existing;
    }

    function isLayoutVisible(el, container) {
        let node = el;
        while (node && node !== container && node !== document && node !== document.body) {
            if (node.nodeType === 1) {
                if (node.hidden) return false;
                const style = node.style;
                if (style && (style.display === "none" || style.visibility === "hidden")) {
                    return false;
                }
            }
            node = node.parentNode;
        }
        return true;
    }

    // Place the badge column just after the longest TC name. Only clamp
    // to the container edge when that would overflow.
    function alignStatusBadges(container) {
        if (!container) {
            container = global.document && global.document.getElementById("test-cases-list");
        }
        if (!container) return null;

        const containerRect = container.getBoundingClientRect();
        const containerLeft = containerRect.left;
        const containerWidth = containerRect.width;
        if (containerWidth <= 0) return null;

        let maxRightEdge = 0;
        Array.prototype.forEach.call(container.querySelectorAll(".tc-main"), function (el) {
            if (!isLayoutVisible(el, container)) return;
            const indent = Math.max(0, el.getBoundingClientRect().left - containerLeft);
            const row = el.closest ? el.closest(".tc-row") : null;
            el.style.setProperty("--tc-indent", indent + "px");
            if (row) {
                row.style.setProperty("--tc-indent", indent + "px");
            }
            const rightEdge = indent + measureContentWidth(el);
            if (rightEdge > maxRightEdge) {
                maxRightEdge = rightEdge;
            }
        });

        Array.prototype.forEach.call(container.querySelectorAll(".list-view .list-left-text"), function (el) {
            if (!isLayoutVisible(el, container)) return;
            const indent = Math.max(0, el.getBoundingClientRect().left - containerLeft);
            const rightEdge = indent + measureContentWidth(el);
            if (rightEdge > maxRightEdge) {
                maxRightEdge = rightEdge;
            }
        });

        let maxRightWidth = 0;
        Array.prototype.forEach.call(container.querySelectorAll(".tc-right, .list-view-right"), function (el) {
            if (!isLayoutVisible(el, container)) return;
            const width = el.scrollWidth || el.getBoundingClientRect().width;
            if (width > maxRightWidth) {
                maxRightWidth = width;
            }
        });

        const rightMinWidth = Math.max(Math.ceil(maxRightWidth), 1);
        container.style.setProperty("--tc-right-width", rightMinWidth + "px");

        const padding = container.classList.contains("list-view") ? 12 : 16;
        let statusLeft = Math.max(maxRightEdge + padding, 100);

        const maxLeft = Math.max(containerWidth - rightMinWidth - 8, 0);
        statusLeft = Math.min(statusLeft, maxLeft);

        container.style.setProperty("--tc-status-left", statusLeft + "px");
        return { statusLeft: statusLeft, rightMinWidth: rightMinWidth, maxRightEdge: maxRightEdge };
    }

    global.TestRunLayout = {
        measureContentWidth: measureContentWidth,
        isLayoutVisible: isLayoutVisible,
        alignStatusBadges: alignStatusBadges,
        fullNameTooltip: fullNameTooltip
    };
})(typeof window !== "undefined" ? window : global);

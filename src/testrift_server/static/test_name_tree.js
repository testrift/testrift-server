/**
 * Compact test-name tree shared by the test-run page and the matrix page.
 *
 * Names are split on '.' and '+' except inside "", '', [], and ().
 * Sibling groups keep the longest shared parent label (radix compression).
 */
(function (global) {
    "use strict";

    function splitName(name) {
        const segments = [];
        const separators = [];
        if (name == null || name === "") {
            return { segments: segments, separators: separators };
        }
        const text = String(name);
        let current = "";
        let paren = 0;
        let bracket = 0;
        let inQuotes = false;
        let quoteChar = null;

        for (let i = 0; i < text.length; i++) {
            const ch = text[i];
            if (inQuotes) {
                current += ch;
                if (ch === quoteChar) {
                    inQuotes = false;
                    quoteChar = null;
                }
                continue;
            }
            if (ch === '"' || ch === "'") {
                inQuotes = true;
                quoteChar = ch;
                current += ch;
                continue;
            }
            if (ch === "(") {
                paren += 1;
                current += ch;
                continue;
            }
            if (ch === ")") {
                if (paren > 0) {
                    paren -= 1;
                }
                current += ch;
                continue;
            }
            if (ch === "[") {
                bracket += 1;
                current += ch;
                continue;
            }
            if (ch === "]") {
                if (bracket > 0) {
                    bracket -= 1;
                }
                current += ch;
                continue;
            }
            if ((ch === "." || ch === "+") && paren === 0 && bracket === 0) {
                if (current) {
                    segments.push(current);
                    separators.push(ch);
                    current = "";
                }
                continue;
            }
            current += ch;
        }
        if (current) {
            segments.push(current);
        }
        while (separators.length >= segments.length) {
            separators.pop();
        }
        return { segments: segments, separators: separators };
    }

    function joinSegments(segments, separators) {
        if (!segments || !segments.length) {
            return "";
        }
        let out = segments[0];
        for (let i = 1; i < segments.length; i++) {
            out += (separators && separators[i - 1] ? separators[i - 1] : ".") + segments[i];
        }
        return out;
    }

    function parentName(fullName) {
        const parts = splitName(fullName);
        if (parts.segments.length <= 1) {
            return "";
        }
        return joinSegments(parts.segments.slice(0, -1), parts.separators);
    }

    function createNode(label, incomingSep) {
        return {
            label: label,
            incomingSep: incomingSep || "",
            children: new Map(),
            fullName: null
        };
    }

    function insertName(root, name) {
        const parts = splitName(name);
        if (!parts.segments.length) {
            return;
        }
        let node = root;
        for (let i = 0; i < parts.segments.length; i++) {
            const seg = parts.segments[i];
            let child = node.children.get(seg);
            if (!child) {
                child = createNode(seg, i === 0 ? "" : parts.separators[i - 1]);
                node.children.set(seg, child);
            }
            node = child;
        }
        node.fullName = name;
    }

    function compressChildren(node) {
        const compressed = new Map();
        node.children.forEach(function (child) {
            compressChildren(child);
            let cur = child;
            while (cur.children.size === 1 && !cur.fullName) {
                const next = cur.children.values().next().value;
                cur = {
                    label: cur.label + (next.incomingSep || ".") + next.label,
                    incomingSep: cur.incomingSep,
                    children: next.children,
                    fullName: next.fullName
                };
            }
            compressed.set(cur.label, cur);
        });
        node.children = compressed;
    }

    function childrenToForest(node, parentPath) {
        const forest = [];
        node.children.forEach(function (child) {
            const path = parentPath
                ? parentPath + (child.incomingSep || ".") + child.label
                : child.label;
            forest.push({
                label: child.label,
                path: path,
                fullName: child.fullName,
                children: childrenToForest(child, path)
            });
        });
        return forest;
    }

    function build(names) {
        const root = createNode("", "");
        (names || []).forEach(function (name) {
            if (name != null && name !== "") {
                insertName(root, name);
            }
        });
        compressChildren(root);
        return childrenToForest(root, "");
    }

    function toMatrixTree(forest) {
        const obj = {};
        (forest || []).forEach(function (node) {
            obj[node.label] = {
                isLeaf: !node.children.length,
                testCaseId: node.fullName || null,
                path: node.path,
                children: toMatrixTree(node.children)
            };
        });
        return obj;
    }

    function dump(forest, indent) {
        indent = indent || "";
        let out = "";
        (forest || []).forEach(function (node) {
            out += indent + node.label + "\n";
            out += dump(node.children, indent + "  ");
        });
        return out;
    }

    global.TestNameTree = {
        splitName: splitName,
        joinSegments: joinSegments,
        parentName: parentName,
        build: build,
        toMatrixTree: toMatrixTree,
        dump: dump
    };
})(typeof window !== "undefined" ? window : global);

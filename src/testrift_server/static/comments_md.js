/* Shared markdown + GitLab-style :shortcode: rendering for comments. */
(function (global) {
    const ALLOWED_TAGS = [
        "p", "br", "strong", "em", "b", "i", "code", "pre", "a",
        "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "h4"
    ];
    const ALLOWED_ATTR = ["href", "title", "rel", "target"];

    let shortcodes = {};
    let shortcodeReady = null;

    function staticBase() {
        if (global.COMMENTS_STATIC_BASE) {
            return global.COMMENTS_STATIC_BASE.replace(/\/?$/, "/");
        }
        return "/static/";
    }

    function loadShortcodes() {
        if (shortcodeReady) {
            return shortcodeReady;
        }
        shortcodeReady = fetch(staticBase() + "emoji_shortcodes.json")
            .then(function (response) {
                if (!response.ok) {
                    return {};
                }
                return response.json();
            })
            .then(function (data) {
                shortcodes = data || {};
                return shortcodes;
            })
            .catch(function () {
                shortcodes = {};
                return shortcodes;
            });
        return shortcodeReady;
    }

    function expandShortcodesInText(text) {
        return text.replace(/(^|[^A-Za-z0-9_]):([A-Za-z0-9_+-]+):/g, function (match, prefix, name) {
            const glyph = shortcodes[name];
            if (!glyph) {
                return match;
            }
            return prefix + glyph;
        });
    }

    function expandShortcodes(source) {
        if (!source) {
            return "";
        }
        const parts = [];
        const fenceRe = /(```[\s\S]*?```|`[^`]*`)/g;
        let last = 0;
        let match;
        while ((match = fenceRe.exec(source)) !== null) {
            parts.push(expandShortcodesInText(source.slice(last, match.index)));
            parts.push(match[0]);
            last = match.index + match[0].length;
        }
        parts.push(expandShortcodesInText(source.slice(last)));
        return parts.join("");
    }

    function sanitizeHref(href) {
        if (!href) {
            return "";
        }
        const trimmed = String(href).trim();
        const lower = trimmed.toLowerCase();
        if (lower.startsWith("http://") || lower.startsWith("https://") || lower.startsWith("mailto:")) {
            return trimmed;
        }
        return "";
    }

    function render(source) {
        const expanded = expandShortcodes(source || "");
        let html = expanded;
        if (typeof marked !== "undefined") {
            if (typeof marked.parse === "function") {
                marked.setOptions({ breaks: true, gfm: true });
                html = marked.parse(expanded);
            } else if (typeof marked === "function") {
                html = marked(expanded, { breaks: true, gfm: true });
            }
        } else {
            const escaped = expanded
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");
            html = escaped.replace(/\n/g, "<br>");
        }
        if (typeof DOMPurify !== "undefined") {
            html = DOMPurify.sanitize(html, {
                ALLOWED_TAGS: ALLOWED_TAGS,
                ALLOWED_ATTR: ALLOWED_ATTR,
                FORBID_TAGS: ["img", "script", "iframe", "object", "embed"],
                ADD_ATTR: ["target", "rel"]
            });
        }
        const wrap = document.createElement("div");
        wrap.innerHTML = html;
        wrap.querySelectorAll("a").forEach(function (anchor) {
            const href = sanitizeHref(anchor.getAttribute("href"));
            if (!href) {
                anchor.removeAttribute("href");
                return;
            }
            anchor.setAttribute("href", href);
            anchor.setAttribute("rel", "noopener noreferrer");
            if (href.startsWith("http://") || href.startsWith("https://")) {
                anchor.setAttribute("target", "_blank");
            }
        });
        wrap.querySelectorAll("img").forEach(function (img) {
            img.remove();
        });
        return wrap.innerHTML;
    }

    function matchingShortcodes(prefix, limit) {
        const needle = (prefix || "").toLowerCase();
        if (!needle) {
            return [];
        }
        const names = Object.keys(shortcodes);
        const starts = [];
        const contains = [];
        for (let i = 0; i < names.length; i++) {
            const name = names[i];
            const lower = name.toLowerCase();
            if (lower.startsWith(needle)) {
                starts.push(name);
            } else if (lower.indexOf(needle) !== -1) {
                contains.push(name);
            }
            if (starts.length >= limit) {
                break;
            }
        }
        return starts.concat(contains).slice(0, limit).map(function (name) {
            return { name: name, glyph: shortcodes[name] };
        });
    }

    global.CommentsMarkdown = {
        loadShortcodes: loadShortcodes,
        render: render,
        matchingShortcodes: matchingShortcodes,
        getShortcodes: function () { return shortcodes; }
    };
})(window);

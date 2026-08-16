## Comments

People can leave comments on a test run and on log lines in a test case. The log interaction follows the gutter + range mockup. Discovery uses a comment icon, not a count, so it stays obvious in the tree and on pages that only link to a run.

### Goals

- Comment on one log line or a contiguous range of lines on the test case log page.
- See that a test case has comments from the test run tree, without reading a number.
- Jump from that icon to the test case page at the first comment.
- Move between comments on a large log without hunting.
- See that a run has comments from every page that lists or links to runs.
- Write comments with Unicode emoji, GitLab-style `:shortcode:` aliases, and a small markdown subset. The composer uses Write and Preview tabs (preview on click, not as you type). Authors can edit their own comments.

### Non-goals (first version)

- Mentions, email, or other notifications.
- Images, raw HTML, or file attachments inside a comment.
- Custom / instance-uploaded emoji (GitLab “custom emoji”). Stick to the standard Unicode shortcode set.
- Threading beyond a flat list under one line range (replies are just more comments on the same range).
- Comments on stack-trace cards, attachments, or AI analysis as separate objects (those can be commented if they also appear as log rows).
- Per-target ACLs. If authentication is on, any signed-in Member or Admin who can see the run can comment.

### Test case log: gutter + range

The message log stays a table (Time, Channel, Message).

**Select**

- Click a row to select that line.
- Shift-click (or click-drag) to select a contiguous range.
- A selected range is highlighted; the gutter on those rows is active.

**Add**

- With a selection, a composer opens **under the last selected row**.
- Saving attaches the comment to that range. The log text is not copied into the comment except as a short snapshot for display if the range is later filtered out of view.

**Show**

- A gutter mark (comment icon) on any row that is part of at least one comment range.
- Every thread is shown under the last line of its range (not collapsed). Opening a mark, using the navigator, or following a deep link focuses that comment and scrolls to it.
- Rows in a comment range keep a left gutter bar so the span is visible without extra clicks. The focused comment’s bar is a little stronger; the log row color stays as it is.
- Each thread shows a line label (`Line 12` or `Lines 12–18`, 1-based).
- Each thread has **Reply**, which adds another comment on that same line range.
- When a thread has more than one comment, **Prev** / **Next** on the thread move only inside that thread.

**Navigate comments on a huge log**

Logs can be tens of thousands of lines. Scattered comments must be reachable without scrolling the whole table.

- A compact **comment navigator** sits in the log header so it stays available while reading the log.
- It shows `3 / 12` (current comment in document order, total on this test case). **This is the only place a comment count appears.** Tree nodes, run lists, gutters, and tooltips use the comment icon with no number.
- **Previous** / **Next** jump to the next comment, focus it, and scroll it into view.
- Keyboard: `n` / `p` (or `j` / `k`) while focus is not in a text field.
- Opening the page from a tree icon lands on comment 1, focuses it, and scrolls it into view.
- Deep link: `/testRun/{run_id}/log/{tc_id}.html#comment={comment_id}` selects that comment in the navigator. Run-level comments use `/testRun/{run_id}/index.html#comment={comment_id}`.

If the current filter hides some commented lines, the navigator still lists those comments. Jumping to one temporarily reveals the range (or shows a one-line hint “comment on hidden lines” with a control to clear the filter for that jump).

**Live runs**

New log lines only append. Existing line indices stay valid. Comments may be added while the test is still running.

### Optional: commented-lines filter, copy link, metrics highlight

These are part of the design. They can ship after the core gutter, navigator, and icons.

**Filter: commented lines only**

A log control (checkbox or toggle next to the existing filters) keeps only rows that sit in a comment range, plus a few lines of context above and below each range.

- Helps a log with tens of thousands of lines and a handful of comments.
- Combines with channel / source / text filters (AND): the toggle first restricts to commented ranges, then the other filters apply.
- Turning it off restores the previous unfiltered window (scroll position as close as practical).
- The navigator still lists every comment on the test case. If a jump would be hidden by *other* filters, use the same “comment on hidden lines” hint as above.
- Hidden when the test case has no comments.

**Copy link**

Each comment has a **Copy link** control (icon or menu item) that copies the `#comment=` URL for that comment (absolute URL of the current page plus the hash).

- On the test case log page: `/testRun/{run_id}/log/{tc_id}.html#comment={id}`
- On the run page (run-level comments): `/testRun/{run_id}/index.html#comment={id}`
- Opening that URL focuses the comment, selects it in the navigator (log page), and scrolls it into view.
- After copy, a short confirmation (tooltip or toast) is enough; no modal.

**Highlight on the metrics chart**

The test case log already jumps from a click on the metrics chart to the closest log line. Going the other way: focusing a comment also marks that time on the chart.

- Use the timestamp of the first line in the comment range (`data-original-time` on that row). For a multi-line range, optionally span from first-line time to last-line time.
- Draw a brief pulse or vertical marker on `#tc-metrics-chart` at that time (same x-mapping the chart already uses for click-to-log).
- Fires when the navigator lands on a comment, when a gutter thread is opened, and when a `#comment=` deep link is applied.
- No-op when the metrics section is hidden or the test has no metrics.
- Chart click behavior stays as it is (scroll to closest log line). Do not auto-open a comment from the chart in this version.

### Test run page: comment icon on the tree

Do **not** show a numeric count on the tree (or on run lists, or on the gutter). A number is easy to miss and easy to confuse with duration or retry counts. The count lives only in the test case log navigator (`3 / 12`).

- Each **leaf** test case that has at least one comment shows a comment icon in the right-hand cluster (next to status / classification).
- Clicking the icon goes to that test case log page and opens the **first** comment (document order: earliest last-line of the range, then created time).
- Clicking the test name still opens the log as today (no forced jump to a comment).
- Parent suite nodes do not show a comment icon; only leaves do.

Tooltip on the icon: “Comments on this test”. No count in the tooltip.

List view on the run page uses the same icon in the row’s right cluster.

### Pages that point at runs

Any UI that links to a test run should show the same comment icon when **that run has at least one comment** (run-level or any test case). Clicking the icon opens the test run page and focuses the first commented test (scroll the tree/list so that row is visible; do not skip the run page).

Apply this on:

| Page | Where the icon sits |
|------|---------------------|
| Home (`/`) | Run list row, next to the run name/status |
| Target (`/targets/{key}`) | Same, in that target’s run list |
| Analyzer | Run row / run picker that links to `/testRun/{id}/index.html` |
| Collection summary | Tile or selected-run link |
| Failures | Row that links to a specific run or test case log. If the link is a test case log, the icon means comments on **that test**, and click goes to that log at the first comment (same as the tree). |

Matrix cells that already link to a run should get the icon if it still fits; if the cell is too tight, a dot on the cell plus tooltip is enough.

The home and target run lists are the important ones. Analyzer, collections, failures, and matrix should not be forgotten: they are how people re-enter an old run.

### Run-level comments (included)

Not every note is about one log line (“shield box was open”, “wrong image flashed”). The run page has a **Run comments** block in the header/info card, above the tree.

- Same comment icon in the header if the run has run-level comments.
- Tree icons remain for test-case comments only.
- List-page icons (home, target, …) light up if **either** run-level or test-case comments exist.

Without this, people will misuse a random test case as a notepad.

### Who can comment

| Auth | Read | Write |
|------|------|--------|
| `auth.enabled: false` | Anyone who can open the UI | Anyone. Prompt for a display name (remembered in the browser). Stored as that name; no user id. |
| `auth.enabled: true` | Signed-in Member or Admin (same as seeing runs) | Signed-in Member or Admin. Author is the account display name. |

- Authors can **edit** and **delete** their own comments (see below).
- Admins can delete any comment (not silently rewrite someone else’s text).
- No anonymous write when authentication is on.

**Edit own comments**

Same pattern as GitLab: a rendered comment shows **Edit** (and Delete) only for the owner.

- **Auth on:** Edit is shown when `author_user_id` is the signed-in user. Saving uses `PATCH /api/comments/{id}`. The API rejects edits by anyone else (403).
- **Auth off:** there is no account. Treat the remembered display name as the owner: Edit is shown when `author_name` matches that name. The API still accepts PATCH (auth is off); the UI is what keeps people from editing others’ comments.
- Edit opens the same composer on that comment: markdown source in Write, Preview tab to check rendering, **Save changes** / **Cancel**. Cancel restores the rendered comment without saving.
- After a successful save, `updated_at` changes and the thread shows **edited**.
- Changing the log line range is not part of edit (body only). To comment on different lines, add a new comment.

### Data model (proposed)

Store comments in SQLite so “which runs have comments?” is cheap for list pages. Do not scan JSONL logs to draw icons.

```
comments
  id              INTEGER PRIMARY KEY
  run_id          TEXT NOT NULL  -- FK test_runs ON DELETE CASCADE
  scope           TEXT NOT NULL  -- 'run' | 'log'
  tc_id           TEXT           -- required when scope = log
  line_start      INTEGER        -- 0-based index in the test case log, inclusive
  line_end        INTEGER        -- inclusive; = line_start for a single line
  body            TEXT NOT NULL  -- markdown source; may contain Unicode emoji and :shortcode:; never HTML
  author_user_id  INTEGER        -- null when auth is off
  author_name     TEXT NOT NULL
  created_at      TEXT NOT NULL
  updated_at      TEXT NOT NULL
```

Log lines have no public id today. JSONL is append-only, so a **0-based line index** is stable for a given test case. `line_start` / `line_end` refer to that order (the same order as the table, including stack-trace rows that are merged into the log).

Also keep a small denormalized flag or count on `test_runs` / `test_cases` (or a pair of queries with indexes on `run_id` and `(run_id, tc_id)`) so list pages do not load comment bodies.

Retention: comments are deleted with the run (cascade), same as other run data.

ZIP export: include comments in the exported run/test-case HTML so a downloaded ZIP still shows them. Vendor the markdown renderer next to other `/static/` scripts so offline pages can still render `body`. List-page icons are server-only (they need the database).

### HTTP (proposed)

All under the existing UI auth rules (session when auth is on). Not ingest; NUnit does not send comments.

| Method | Path | Role |
|--------|------|------|
| GET | `/api/runs/{run_id}/comments` | Summary: run-level comments + per-`tc_id` “has comments” (and first comment id). Used by the run page and by list pages in bulk. |
| GET | `/api/runs/{run_id}/comments/log/{tc_id}` | Full threads for one test case |
| POST | `/api/runs/{run_id}/comments` | Create (`scope`, optional `tc_id`, `line_start`, `line_end`, `body`, optional `author_name` when auth is off) |
| PATCH | `/api/comments/{id}` | Edit body (author or Admin) |
| DELETE | `/api/comments/{id}` | Author or Admin |

Bulk for home/target: extend `GET /api/test-runs` with `has_comments` (boolean) per run so the list does not N+1.

WebSocket: optional later (`comments_changed` on `/ws/ui`). V1 can refresh on save and on page load. Live log already streams; new comments from another browser can wait for refresh in v1.

### Markdown and emoji

Store **markdown source** in `body`. Do not store HTML. Do not rewrite `:shortcode:` to Unicode on save — the source stays as typed so an edit still shows `:thumbsup:`. Render to characters only when displaying a saved comment or when the user opens the Preview tab.

**Emoji**

- Unicode in the source works as-is (OS picker `Win + .` is fine).
- **GitLab-style shortcodes:** `:name:` (colons around the name), including GitLab aliases. Examples that must work: `:thumbsup:`, `:thumbs_up:`, `:+1:`, `:tada:`, `:white_check_mark:`, `:x:`, `:bug:`, `:rocket:`. Names and aliases follow [TanukiEmoji](https://gitlab-org.gitlab.io/ruby/gems/tanuki_emoji) / GitLab Flavored Markdown (the same family as the [Emoji Cheat Sheet](https://www.webfx.com/tools/emoji-cheat-sheet/) GitLab documents).
- Render as **Unicode characters**, not PNG sprites or `<img>`. Vendor a compact JSON map `shortcode → Unicode` under `/static/` (no extra Python package).
- Do **not** expand shortcodes inside inline code or fenced code blocks (`` `:smile:` `` stays literal).
- Skin-tone suffixes GitLab uses (`:thumbsup_tone1:` … `_tone5:`) can be in the map if the dataset includes them; they are not required in the composer UI.
- A modest **autocomplete** when the user types `:` in the textarea (filter names/aliases, insert `:thumbsup:`) matches GitLab’s comment box. A full graphical emoji picker can wait.

**Allowed markdown** (GitHub-flavored, small subset):

- `**bold**`, `*italic*`, `` `inline code` ``
- Links: `[text](https://…)`
- Unordered lists and line breaks (`marked` `breaks: true` so Enter matches what people type)
- Fenced or indented code blocks are fine if the parser produces them; keep styling compact in the thread

**Not allowed**

- Images (`![](url)`): no hotlinking or size surprises in a log thread
- Raw HTML
- Tables
- `javascript:` (and similar) URLs

**How to render**

The UI is vanilla JS (Bootstrap / Font Awesome from CDN, a few files under `/static/`). One shared function for saved comments, ZIP HTML, and the Preview tab:

1. Expand `:shortcode:` (and aliases) to Unicode, skipping code spans/blocks
2. [marked](https://github.com/markedjs/marked) to parse markdown
3. [DOMPurify](https://github.com/cure53/DOMPurify) with a tight tag/attr allow-list before inserting into the DOM

Vendor marked, DOMPurify, and the shortcode map under `/static/` (same pattern as `msgpack.min.js`). Do not rely on a CDN for this.

The API still sends and receives `body` as source. No `body_html` field; the client (and exported pages) render.

**Composer**

GitLab-style **Write** / **Preview** tabs, not a live side-by-side preview.

- **Write:** a `<textarea>` with markdown source (including `:shortcode:`). Modest length limit (e.g. 8k characters). Optional small toolbar (bold / italic / code) that wraps the current selection; Font Awesome is already on the page.
- **Preview:** click the Preview tab to render the current textarea with the same pipeline as a saved comment. The preview does **not** update while typing; click Preview again (or leave Write and come back to Preview) to refresh. Switching back to Write keeps the source.
- New comments: composer under the last selected log row (or in the run-level comments block). **Comment** / **Cancel**.
- Editing: same tabs on the existing comment (see **Edit own comments**).

### UI details

- Icon: a speech-bubble / comment glyph, same asset everywhere (tree, run lists, log gutter, navigator). Not a number — the `3 / 12` counter is only on the log navigator.
- Empty gutter on hover: faint icon; click starts a selection if none exists.
- Each saved comment shows author, time, rendered body, and owner actions (**Edit**, Delete). Copy link sits with those actions when that optional control is present.
- Edited comments show “edited” and `updated_at` in the thread.
- Filters, source sidebar, and delta-time on the log page stay as they are; comments are an extra layer.

### Slim / local live-log server

A later server mode will show only the **current live run** and its **test case log pages** on a developer machine, without multi-run pages and without extra Python packages. Comments should fit that split:

- **In the slim surface:** run-level comments, gutter + range on the TC log, navigator, markdown/emoji/shortcodes and Write/Preview composer (vendored JS, no Python markdown library), edit own comments, copy link, commented-lines filter, metrics-chart highlight.
- **Full server only:** comment icons on home, target, analyzer, collections, failures, and matrix. Those pages will not exist in the slim mode.
- Do not add Python dependencies for comments. `marked`, DOMPurify, and the shortcode map stay under `/static/`.
- Comment APIs and SQLite tables can exist in both modes; list-page `has_comments` aggregation is unused if there is no run list.

### Further suggestions

These are optional; they are not required to ship the first version.

1. **Don’t export secrets** — comments are user text; ZIP/HTML export should keep them, but treat them like run metadata in backups.
2. **Offline / static HTML** — server-mode only for creating comments. Static ZIP pages are read-only.
3. **No comment on empty logs** — if a test has no log rows, only run-level comments apply; the tree icon does not appear for that test.
4. **Graphical emoji picker** — shortcode autocomplete plus the OS picker is enough; a sprite grid can wait.

### Implementation order

1. Schema + APIs + `has_comments` on the run list.
2. Test case log: selection, composer (Write / Preview tabs + `:shortcode:` autocomplete), edit own comments, gutter, navigator, deep link, markdown/emoji/shortcode rendering.
3. Test run tree/list icons and jump-to-first-comment.
4. Home and Target run-list icons.
5. Analyzer, Failures, Collection summary, Matrix.
6. Run-level comments in the run header.
7. ZIP/static HTML read-only rendering.
8. Optional: commented-lines-only filter, copy link, metrics-chart highlight.

### Out of scope until later

- Reactions, assignments, or “resolve” like a code-review thread.
- Commenting from the NUnit plugin or collector.
- Search across all comments on the server.
- Real-time sync of comments between two open browsers.
- WYSIWYG / contenteditable composers (EasyMDE, Quill, and similar).
- Server-side HTML rendering of `body` (Python markdown) unless a second consumer needs it.

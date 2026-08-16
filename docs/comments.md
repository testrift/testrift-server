# Comments

People can leave comments on a test run and on log lines in a test case. Comments are stored with the run in SQLite and deleted when the run is deleted. Test clients do not send comments; they are created in the web UI. See [authentication.md](authentication.md) for who can use the UI.

## Test case log

The message log stays a table (Time, Channel, Message). A gutter on the left is used to select lines and to show which lines already have comments.

**Select and add**

- Click a row to select that line. Shift-click or click-drag to select a contiguous range.
- A composer opens under the last selected row. Saving attaches the comment to that range.
- The composer uses **Write** and **Preview** tabs. Preview renders on click, not while typing.

**Show**

- A comment icon in the gutter marks any row that is part of at least one comment range. A left bar on those rows shows the span; the focused comment’s bar is a little stronger.
- Every thread is shown under the last line of its range. Each thread has a line label (`Line 12` or `Lines 12–18`, 1-based).
- **Reply** adds another comment on the same line range. When a thread has more than one comment, **Prev** / **Next** on the thread move only inside that thread.

**Navigate**

Logs can be large. A comment navigator in the log header shows `3 / 12` (current comment in document order, total on this test case). That is the only place a comment count appears. Tree nodes, run lists, gutters, and tooltips use the comment icon with no number.

- **Previous** / **Next** jump to the next comment, focus it, and scroll it into view.
- Keyboard: `n` / `p` or `j` / `k` while focus is not in a text field.
- Opening the page from a tree icon with `?openFirst=1` lands on the first comment.
- Deep link: `/testRun/{run_id}/log/{tc_id}.html#comment={comment_id}`

If the current filter hides some commented lines, the navigator still lists those comments. Jumping to one shows a hint with a control to clear the filter.

**Commented lines only**

A checkbox next to the log filters keeps only rows that sit in a comment range, plus a few lines of context. It combines with channel, source, and text filters (AND). It is hidden when the test case has no comments.

**Copy link**

Each comment has **Copy link**, which copies the `#comment=` URL for that comment.

**Metrics chart**

Focusing a comment marks that time on the metrics chart when the chart is visible. Chart click still scrolls to the closest log line and does not open a comment.

Line indices in the API are **0-based** and inclusive (`line_end` equals `line_start` for a single line). They follow the same order as the table, including stack-trace rows that are merged into the log. New log lines only append, so existing indices stay valid while a test is still running.

## Test run page

The run page has a **Run comments** block in the header for notes that are not about one log line.

Each **leaf** test case that has at least one comment shows a comment icon next to status / classification. Clicking the icon opens that test case log at the first comment. Clicking the test name opens the log without jumping to a comment. Parent suite nodes do not show a comment icon.

Tooltip on the icon: “Comments on this test”. List view uses the same icon in the row’s right cluster.

## Pages that list runs

Any UI that links to a test run shows the same comment icon when that run has at least one comment (run-level or any test case). Clicking the icon opens the test run page and scrolls to the first commented test.

| Page | Where the icon sits |
|------|---------------------|
| Home (`/`) | Run list row, next to the run name/status |
| Target (`/targets/{key}`) | Same, in that target’s run list |
| Analyzer | Run row / run picker that links to the run page |
| Collection summary | Tile or selected-run link |
| Failures | Row that links to a run or test case log. If the link is a test case log, the icon means comments on **that test**, and click goes to that log at the first comment. |

## Who can comment

| Auth | Read | Write |
|------|------|--------|
| `auth.enabled: false` | Anyone who can open the UI | Anyone. The UI prompts for a display name (remembered in the browser). |
| `auth.enabled: true` | Signed-in Member or Admin | Signed-in Member or Admin. Author is the account display name. |

- Authors can **edit** and **delete** their own comments.
- Admins can delete any comment. They cannot rewrite someone else’s text.
- **Auth on:** **Edit** is shown when `author_user_id` is the signed-in user. The API rejects edits by anyone else (403).
- **Auth off:** **Edit** is shown when `author_name` matches the remembered display name. The API still accepts PATCH because authentication is off; the UI is what keeps people from editing others’ comments.
- Edit changes the body only. To comment on different lines, add a new comment.
- After a save, the thread shows **edited**.

## Markdown and emoji

The API stores markdown **source** in `body` (not HTML). Shortcodes such as `:thumbsup:` are kept as typed and rendered to Unicode when displaying a comment or when opening Preview.

**Allowed**

- Unicode emoji, and GitLab-style `:shortcode:` aliases (`:thumbsup:`, `:+1:`, `:tada:`, `:rocket:`, and similar). Shortcodes inside inline code or fenced code blocks stay literal.
- `**bold**`, `*italic*`, `` `inline code` ``, links, lists, line breaks, and fenced code blocks.

**Not allowed**

- Images, raw HTML, tables, and `javascript:` (and similar) URLs.

Rendering uses vendored `marked` and DOMPurify under `/static/`. Body length is at most 8000 characters.

## ZIP export

Exported run and test-case HTML includes comments and renders them offline (read-only). Creating comments is server-mode only. List-page icons need the database and are not part of the ZIP.

## HTTP APIs

Same UI auth rules as other JSON APIs (session when authentication is on). These are not ingest endpoints.

| Method | Path | Role |
|--------|------|------|
| GET | `/api/runs/{run_id}/comments` | Run-level comments plus per-`tc_id` presence (`has_comments`, `first_comment_id`) |
| GET | `/api/runs/{run_id}/comments/log/{tc_id}` | Full comments for one test case |
| POST | `/api/runs/{run_id}/comments` | Create (`scope` `run` or `log`, optional `tc_id`, `line_start`, `line_end`, `body`, optional `author_name` when auth is off) |
| PATCH | `/api/comments/{id}` | Edit body (author; when auth is off, any caller) |
| DELETE | `/api/comments/{id}` | Author or Admin (when auth is off, any caller) |
| GET | `/api/comments/presence?run_ids=` | Bulk `has_comments` for run list pages |

`GET /api/test-runs` also includes `has_comments` per run. Path list: [http_contracts.md](http_contracts.md).

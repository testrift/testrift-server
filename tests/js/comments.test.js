/**
 * Regression tests for comment-thread visibility on the TC log page.
 *
 * A comment thread references a range of log lines (line_start..line_end).
 * It must stay visible as long as at least one of those lines is visible,
 * and hide only once every referenced line is hidden -- whether by the
 * text filter or by unchecking a Source in the sidebar.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SOURCE_PATH = path.join(
  __dirname,
  "..",
  "..",
  "src",
  "testrift_server",
  "static",
  "comments.js"
);
const SOURCE = fs.readFileSync(SOURCE_PATH, "utf8");

function rowByIndex(index) {
  return document.querySelector(
    '#msg_table tbody tr.log-entry-row[data-log-index="' + index + '"]'
  );
}

function buildLogTable(rowCount) {
  let rows = "";
  for (let i = 0; i < rowCount; i++) {
    rows +=
      '<tr class="log-entry-row" data-log-index="' +
      i +
      '"><td class="log-gutter"></td><td class="log-message">msg ' +
      i +
      "</td></tr>";
  }
  document.body.innerHTML =
    '<table id="msg_table"><tbody>' + rows + "</tbody></table>";
}

function addThreadRow(start, end) {
  const tr = document.createElement("tr");
  tr.className = "comment-thread-row";
  tr.setAttribute("data-thread-key", start + "-" + end);
  tr.setAttribute("data-thread-start", String(start));
  tr.setAttribute("data-thread-end", String(end));
  rowByIndex(end).after(tr);
  return tr;
}

describe("CommentsUI.Log thread visibility", () => {
  beforeEach(() => {
    jest.resetModules();
    buildLogTable(6);
    // comments.js is a plain browser script (not a module); eval it into
    // this jsdom window so it wires up window.CommentsUI like a <script> tag.
    window.eval(SOURCE);
    window.CommentsUI.Log.comments = [{ id: 1, line_start: 2, line_end: 4 }];
  });

  test("hides the thread once every referenced line is hidden", () => {
    const thread = addThreadRow(2, 4);
    [2, 3, 4].forEach((i) => {
      rowByIndex(i).style.display = "none";
    });

    window.CommentsUI.Log.applyCommentedFilter();

    expect(thread.style.display).toBe("none");
  });

  test("keeps the thread visible when only the end line is hidden", () => {
    const thread = addThreadRow(2, 4);
    rowByIndex(4).style.display = "none";

    window.CommentsUI.Log.applyCommentedFilter();

    expect(thread.style.display).not.toBe("none");
  });

  test("shows the thread again once a hidden line becomes visible", () => {
    const thread = addThreadRow(2, 4);
    [2, 3, 4].forEach((i) => {
      rowByIndex(i).style.display = "none";
    });
    window.CommentsUI.Log.applyCommentedFilter();
    expect(thread.style.display).toBe("none");

    rowByIndex(3).style.display = "";
    window.CommentsUI.Log.applyCommentedFilter();

    expect(thread.style.display).not.toBe("none");
  });
});

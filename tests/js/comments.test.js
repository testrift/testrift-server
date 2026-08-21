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

function buildPage(rowCount) {
  buildLogTable(rowCount);
  document.body.insertAdjacentHTML(
    "beforeend",
    '<label id="commentedLinesFilterWrap">' +
      '<input type="checkbox" id="commentedLinesOnly">' +
      "</label>" +
      '<span id="commentNavigator" class="comment-navigator">' +
      '<button type="button" id="commentNavFirst">First thread</button>' +
      '<span id="commentNavLabel"></span>' +
      "</span>"
  );
}

function focusedCommentId() {
  const item = document.querySelector(".tr-comment-item.is-focused");
  return item && Number(item.getAttribute("data-comment-id"));
}

describe("CommentsUI.Log thread-to-thread navigation", () => {
  beforeEach(() => {
    jest.resetModules();
    buildPage(10);
    window.eval(SOURCE);
  });

  test("gives every thread a Prev/Next thread control when there are multiple threads", async () => {
    await window.CommentsUI.Log.init({
      runId: "r1",
      tcId: "tc1",
      readOnly: true,
      embeddedComments: [
        { id: 1, line_start: 1, line_end: 1, created_at: "t1" },
        { id: 2, line_start: 5, line_end: 5, created_at: "t2" },
        { id: 3, line_start: 8, line_end: 8, created_at: "t3" },
      ],
    });

    const jumps = document.querySelectorAll(".tr-thread-jump");
    expect(jumps.length).toBe(3);
    jumps.forEach((jump) => expect(jump.hidden).toBe(false));
  });

  test("Next thread / Prev thread move focus to the adjacent thread, not within-thread replies", async () => {
    await window.CommentsUI.Log.init({
      runId: "r1",
      tcId: "tc1",
      readOnly: true,
      embeddedComments: [
        { id: 1, line_start: 1, line_end: 1, created_at: "t1" },
        { id: 2, line_start: 5, line_end: 5, created_at: "t2" },
      ],
    });

    const firstThreadRow = document.querySelector(
      '.comment-thread-row[data-thread-key="1-1"]'
    );
    firstThreadRow.querySelector('[data-thread-jump-dir="1"]').click();
    expect(focusedCommentId()).toBe(2);

    const secondThreadRow = document.querySelector(
      '.comment-thread-row[data-thread-key="5-5"]'
    );
    secondThreadRow.querySelector('[data-thread-jump-dir="-1"]').click();
    expect(focusedCommentId()).toBe(1);
  });

  test("disables Prev on the first thread and Next on the last thread", async () => {
    await window.CommentsUI.Log.init({
      runId: "r1",
      tcId: "tc1",
      readOnly: true,
      embeddedComments: [
        { id: 1, line_start: 1, line_end: 1, created_at: "t1" },
        { id: 2, line_start: 5, line_end: 5, created_at: "t2" },
      ],
    });

    const firstThreadRow = document.querySelector(
      '.comment-thread-row[data-thread-key="1-1"]'
    );
    const secondThreadRow = document.querySelector(
      '.comment-thread-row[data-thread-key="5-5"]'
    );
    expect(
      firstThreadRow.querySelector('[data-thread-jump-dir="-1"]').disabled
    ).toBe(true);
    expect(
      secondThreadRow.querySelector('[data-thread-jump-dir="1"]').disabled
    ).toBe(true);
  });

  test("hides the thread-jump control when there is only one thread", async () => {
    await window.CommentsUI.Log.init({
      runId: "r1",
      tcId: "tc1",
      readOnly: true,
      embeddedComments: [{ id: 1, line_start: 1, line_end: 1, created_at: "t1" }],
    });

    const jump = document.querySelector(".tr-thread-jump");
    expect(jump.hidden).toBe(true);
  });

  test("never renders within-thread reply navigation, even with multiple replies", async () => {
    await window.CommentsUI.Log.init({
      runId: "r1",
      tcId: "tc1",
      readOnly: true,
      embeddedComments: [
        { id: 1, line_start: 1, line_end: 1, created_at: "t1" },
        { id: 2, line_start: 1, line_end: 1, created_at: "t2" },
      ],
    });

    expect(document.querySelectorAll(".tr-thread-nav").length).toBe(0);
    expect(document.querySelectorAll(".tr-comment-item").length).toBe(2);
  });

  test("the single top navigator button jumps to the first thread", async () => {
    await window.CommentsUI.Log.init({
      runId: "r1",
      tcId: "tc1",
      readOnly: true,
      embeddedComments: [
        { id: 1, line_start: 1, line_end: 1, created_at: "t1" },
        { id: 2, line_start: 5, line_end: 5, created_at: "t2" },
        { id: 3, line_start: 8, line_end: 8, created_at: "t3" },
      ],
    });

    expect(document.getElementById("commentNavPrev")).toBeNull();
    expect(document.getElementById("commentNavNext")).toBeNull();

    window.CommentsUI.Log.goto(2);
    expect(focusedCommentId()).toBe(3);

    document.getElementById("commentNavFirst").click();

    expect(focusedCommentId()).toBe(1);
    expect(window.CommentsUI.Log.currentNav).toBe(0);
  });
});

describe("CommentsUI.Run.decorateTree", () => {
  beforeEach(() => {
    jest.resetModules();
    document.body.innerHTML =
      '<ul id="test-cases-list">' +
      '<li class="test-case-node" data-storage-id="1-3515">' +
      '<div class="tc-right"><span class="comment-container"></span></div>' +
      "</li>" +
      "</ul>";
    window.eval(SOURCE);
  });

  test("does not throw when testCases is unset", () => {
    window.CommentsUI.Run.config = { runId: "c92ad0e76100" };
    window.CommentsUI.Run.testCases = undefined;
    expect(() => window.CommentsUI.Run.decorateTree()).not.toThrow();
    expect(document.querySelector(".tr-comment-icon")).toBeNull();
  });

  test("adds a comment icon for commented test cases", () => {
    window.CommentsUI.Run.config = { runId: "c92ad0e76100" };
    window.CommentsUI.Run.testCases = {
      "1-3515": { first_comment_id: 9 },
    };
    window.CommentsUI.Run.decorateTree();
    const icon = document.querySelector(".tr-comment-icon");
    expect(icon).not.toBeNull();
    expect(icon.getAttribute("href")).toContain("/log/1-3515.html#comment=9");
  });
});

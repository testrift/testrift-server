/**
 * Tests for test-run tree/list badge alignment and hover titles.
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
  "test_run_layout.js"
);
const SOURCE = fs.readFileSync(SOURCE_PATH, "utf8");

function mockBox(el, { left = 0, width = 0 } = {}) {
  el.getBoundingClientRect = () => ({
    left,
    width,
    right: left + width,
    top: 0,
    bottom: 0,
    height: 0,
    x: left,
    y: 0,
    toJSON() {
      return {};
    },
  });
  Object.defineProperty(el, "scrollWidth", {
    configurable: true,
    get: () => width,
  });
}

describe("TestRunLayout", () => {
  beforeEach(() => {
    jest.resetModules();
    document.body.innerHTML = "";
    window.eval(SOURCE);
  });

  describe("fullNameTooltip", () => {
    test("returns the full name for hover", () => {
      expect(
        window.TestRunLayout.fullNameTooltip(
          "NUnitTest.uConnect2.Scenarios.TransparentConnectionIndication.TransparentPeerConnectionGpio.DeassertedOnBoot"
        )
      ).toBe(
        "NUnitTest.uConnect2.Scenarios.TransparentConnectionIndication.TransparentPeerConnectionGpio.DeassertedOnBoot"
      );
    });

    test("decodes HTML quote entities", () => {
      expect(window.TestRunLayout.fullNameTooltip("Foo(&quot;bar&quot;)")).toBe(
        'Foo("bar")'
      );
    });

    test("keeps cleanup title and prepends the full name", () => {
      expect(
        window.TestRunLayout.fullNameTooltip(
          "Foo.Bar",
          "Log files have been cleaned up due to retention policy"
        )
      ).toBe(
        "Foo.Bar\nLog files have been cleaned up due to retention policy"
      );
    });
  });

  describe("alignStatusBadges", () => {
    test("places the badge column after the longest tree-view name", () => {
      document.body.innerHTML =
        '<ul id="test-cases-list" class="tree">' +
        '<li><div class="tc-row"><div class="tc-main">short</div><div class="tc-right">PASSED</div></div></li>' +
        '<li><div class="tc-row"><div class="tc-main">a-very-long-test-case-name</div><div class="tc-right">FAILED</div></div></li>' +
        "</ul>";

      const container = document.getElementById("test-cases-list");
      mockBox(container, { left: 0, width: 1000 });
      const mains = container.querySelectorAll(".tc-main");
      mockBox(mains[0], { left: 40, width: 80 });
      mockBox(mains[1], { left: 40, width: 400 });
      container.querySelectorAll(".tc-right").forEach((el) => {
        mockBox(el, { left: 0, width: 120 });
      });

      const result = window.TestRunLayout.alignStatusBadges(container);

      expect(result.maxRightEdge).toBe(440);
      expect(result.statusLeft).toBe(456);
      expect(container.style.getPropertyValue("--tc-status-left")).toBe("456px");
      expect(mains[1].style.getPropertyValue("--tc-indent")).toBe("40px");
      expect(result.statusLeft).toBeLessThan(800);
    });

    test("clamps the badge column when the longest name would overflow", () => {
      document.body.innerHTML =
        '<ul id="test-cases-list" class="tree">' +
        '<li><div class="tc-row"><div class="tc-main">long-name</div><div class="tc-right">FAILED</div></div></li>' +
        "</ul>";

      const container = document.getElementById("test-cases-list");
      mockBox(container, { left: 0, width: 300 });
      mockBox(container.querySelector(".tc-main"), { left: 40, width: 400 });
      mockBox(container.querySelector(".tc-right"), { left: 0, width: 100 });

      const result = window.TestRunLayout.alignStatusBadges(container);

      expect(result.statusLeft).toBe(192);
    });

    test("ignores collapsed/hidden tree rows when placing the badge column", () => {
      document.body.innerHTML =
        '<ul id="test-cases-list" class="tree">' +
        '<li><div class="tc-row"><div class="tc-main">short</div><div class="tc-right">PASSED</div></div>' +
        '<ul style="display: none;"><li><div class="tc-row"><div class="tc-main">a-very-long-hidden-name</div><div class="tc-right">FAILED</div></div></li></ul>' +
        "</li></ul>";

      const container = document.getElementById("test-cases-list");
      mockBox(container, { left: 0, width: 1000 });
      const mains = container.querySelectorAll(".tc-main");
      mockBox(mains[0], { left: 8, width: 80 });
      mockBox(mains[1], { left: 40, width: 400 });
      container.querySelectorAll(".tc-right").forEach((el) => {
        mockBox(el, { left: 0, width: 120 });
      });

      const result = window.TestRunLayout.alignStatusBadges(container);

      expect(result.maxRightEdge).toBe(88);
      expect(result.statusLeft).toBe(104);
    });

    test("places list-view badges after the longest list-left-text", () => {
      document.body.innerHTML =
        '<ul id="test-cases-list" class="list-view">' +
        '<li><div class="list-left"><span class="list-left-text">NUnitTest.uConnect2.Short</span></div><div class="list-view-right">PASSED</div></li>' +
        '<li><div class="list-left"><span class="list-left-text">NUnitTest.uConnect2.A.Very.Long.Test.Name</span></div><div class="list-view-right">FAILED</div></li>' +
        "</ul>";

      const container = document.getElementById("test-cases-list");
      mockBox(container, { left: 0, width: 1000 });
      const texts = container.querySelectorAll(".list-left-text");
      mockBox(texts[0], { left: 8, width: 200 });
      mockBox(texts[1], { left: 8, width: 500 });
      container.querySelectorAll(".list-view-right").forEach((el) => {
        mockBox(el, { left: 0, width: 110 });
      });

      const result = window.TestRunLayout.alignStatusBadges(container);

      expect(result.maxRightEdge).toBe(508);
      expect(result.statusLeft).toBe(520);
      expect(container.style.getPropertyValue("--tc-status-left")).toBe("520px");
    });
  });
});

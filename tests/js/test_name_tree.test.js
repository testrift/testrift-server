/**
 * Compact test-name tree: grouping, separators, and live/random insertion order.
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
  "test_name_tree.js"
);
const SOURCE = fs.readFileSync(SOURCE_PATH, "utf8");

function shuffle(items) {
  const copy = items.slice();
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = copy[i];
    copy[i] = copy[j];
    copy[j] = tmp;
  }
  return copy;
}

function labels(forest) {
  return (forest || []).map((n) => n.label);
}

function findNode(forest, label) {
  for (const node of forest || []) {
    if (node.label === label) return node;
    const hit = findNode(node.children, label);
    if (hit) return hit;
  }
  return null;
}

function leafNames(forest, acc = []) {
  for (const node of forest || []) {
    if (node.fullName && !node.children.length) {
      acc.push(node.fullName);
    }
    leafNames(node.children, acc);
  }
  return acc;
}

describe("TestNameTree", () => {
  beforeEach(() => {
    jest.resetModules();
    window.eval(SOURCE);
  });

  describe("splitName", () => {
    test("splits on dots", () => {
      expect(window.TestNameTree.splitName("a.b.c").segments).toEqual([
        "a",
        "b",
        "c",
      ]);
    });

    test("splits on plus for nested classes", () => {
      const parts = window.TestNameTree.splitName("Parent+Child.Method");
      expect(parts.segments).toEqual(["Parent", "Child", "Method"]);
      expect(parts.separators).toEqual(["+", "."]);
    });

    test("does not split dots inside parentheses", () => {
      expect(window.TestNameTree.splitName("Foo.MyTest(6.0)").segments).toEqual([
        "Foo",
        "MyTest(6.0)",
      ]);
    });

    test("does not split dots inside square brackets", () => {
      expect(window.TestNameTree.splitName("Foo.Bar[a.b.c]").segments).toEqual([
        "Foo",
        "Bar[a.b.c]",
      ]);
    });

    test("does not split dots inside double quotes", () => {
      expect(
        window.TestNameTree.splitName('Foo.Bar("x.y.z")').segments
      ).toEqual(["Foo", 'Bar("x.y.z")']);
    });

    test("does not split dots inside single quotes", () => {
      expect(
        window.TestNameTree.splitName("Foo.Bar('x.y')").segments
      ).toEqual(["Foo", "Bar('x.y')"]);
    });

    test("ignores plus and dots inside parentheses", () => {
      expect(
        window.TestNameTree.splitName("Foo.Bar(a+b.0)").segments
      ).toEqual(["Foo", "Bar(a+b.0)"]);
    });

    test("handles nested parentheses and brackets", () => {
      expect(
        window.TestNameTree.splitName("Foo.Bar(a[1.2].x)").segments
      ).toEqual(["Foo", "Bar(a[1.2].x)"]);
    });

    test("handles quotes inside parentheses", () => {
      expect(
        window.TestNameTree.splitName('NUnitTest.Range.OneMbitOutputPower(4.0d)').segments
      ).toEqual(["NUnitTest", "Range", "OneMbitOutputPower(4.0d)"]);
    });

    test("handles empty and separator-free names", () => {
      expect(window.TestNameTree.splitName("").segments).toEqual([]);
      expect(window.TestNameTree.splitName("LeafOnly").segments).toEqual([
        "LeafOnly",
      ]);
    });
  });

  describe("build", () => {
    test("keeps the longest shared parent for two siblings", () => {
      const forest = window.TestNameTree.build([
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect",
        "NUnitTest.uConnect2.Func.IP.TcpClient.Ipv6Connectivity",
      ]);
      expect(window.TestNameTree.dump(forest).trim()).toBe(
        [
          "NUnitTest.uConnect2.Func.IP.TcpClient",
          "  GracefulDisconnect",
          "  Ipv6Connectivity",
        ].join("\n")
      );
    });

    test("splits groups at the first divergence and keeps long labels", () => {
      const forest = window.TestNameTree.build([
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect",
        "NUnitTest.uConnect2.Func.IP.TcpClient.Ipv6Connectivity",
        "NUnitTest.uConnect2.Func.Bluetooth.SPS.SpsConnectInterop.SpsConnectFromNinaW15Central",
        "NUnitTest.uConnect2.Func.Bluetooth.SPS.SpsConnectInterop.SpsConnectToNinaB3Peripheral",
      ]);
      expect(window.TestNameTree.dump(forest).trim()).toBe(
        [
          "NUnitTest.uConnect2.Func",
          "  IP.TcpClient",
          "    GracefulDisconnect",
          "    Ipv6Connectivity",
          "  Bluetooth.SPS.SpsConnectInterop",
          "    SpsConnectFromNinaW15Central",
          "    SpsConnectToNinaB3Peripheral",
        ].join("\n")
      );
    });

    test("is independent of insertion order", () => {
      const names = [
        "NUnitTest.uConnect2.Func.Bluetooth.SPS.SpsConnectInterop.SpsConnectToNinaB3Peripheral",
        "NUnitTest.uConnect2.Func.IP.TcpClient.Ipv6Connectivity",
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect",
        "NUnitTest.uConnect2.Func.Bluetooth.SPS.SpsConnectInterop.SpsConnectFromNinaW15Central",
      ];
      for (let i = 0; i < 8; i++) {
        const forest = window.TestNameTree.build(shuffle(names));
        const root = forest[0];
        expect(root.label).toBe("NUnitTest.uConnect2.Func");
        expect(labels(root.children).sort()).toEqual([
          "Bluetooth.SPS.SpsConnectInterop",
          "IP.TcpClient",
        ]);
        expect(
          findNode(forest, "IP.TcpClient").children.map((c) => c.label).sort()
        ).toEqual(["GracefulDisconnect", "Ipv6Connectivity"]);
      }
    });

    test("shows a single test as one compressed leaf", () => {
      const forest = window.TestNameTree.build([
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect",
      ]);
      expect(forest).toHaveLength(1);
      expect(forest[0].label).toBe(
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect"
      );
      expect(forest[0].children).toHaveLength(0);
      expect(forest[0].fullName).toBe(
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect"
      );
    });

    test("keeps unrelated roots separate", () => {
      const forest = window.TestNameTree.build([
        "SuiteA.TestOne",
        "SuiteB.TestTwo",
      ]);
      expect(labels(forest)).toEqual(["SuiteA.TestOne", "SuiteB.TestTwo"]);
      expect(forest.every((n) => n.children.length === 0)).toBe(true);
    });

    test("keeps plus separators when compressing nested classes", () => {
      const forest = window.TestNameTree.build([
        "Outer+Inner.MethodA",
        "Outer+Inner.MethodB",
      ]);
      expect(forest[0].label).toBe("Outer+Inner");
      expect(labels(forest[0].children)).toEqual(["MethodA", "MethodB"]);
      expect(forest[0].path).toBe("Outer+Inner");
    });

    test("does not split parameterized siblings on the argument dot", () => {
      const forest = window.TestNameTree.build([
        "Foo.MyTest(6.0)",
        "Foo.MyTest(7.0)",
      ]);
      expect(window.TestNameTree.dump(forest).trim()).toBe(
        ["Foo", "  MyTest(6.0)", "  MyTest(7.0)"].join("\n")
      );
    });

    test("does not split matrix-style names with dots in parentheses", () => {
      const forest = window.TestNameTree.build([
        "NUnitTest.uConnect2.Range.RangeBle.OneMbitOutputPower(4.0d)",
        "NUnitTest.uConnect2.Range.RangeBle.OneMbitOutputPower(8.0d)",
      ]);
      expect(forest[0].label).toBe(
        "NUnitTest.uConnect2.Range.RangeBle"
      );
      expect(labels(forest[0].children)).toEqual([
        "OneMbitOutputPower(4.0d)",
        "OneMbitOutputPower(8.0d)",
      ]);
    });

    test("allows a test name that is a prefix of another test name", () => {
      const forest = window.TestNameTree.build(["Foo.Bar", "Foo.Bar.Baz"]);
      expect(forest).toHaveLength(1);
      expect(forest[0].label).toBe("Foo.Bar");
      expect(forest[0].fullName).toBe("Foo.Bar");
      expect(labels(forest[0].children)).toEqual(["Baz"]);
      expect(forest[0].children[0].fullName).toBe("Foo.Bar.Baz");
    });

    test("rebuilds compactly as names arrive one by one", () => {
      const arriving = [
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect",
        "NUnitTest.uConnect2.Func.Bluetooth.SPS.SpsConnectInterop.SpsConnectFromNinaW15Central",
        "NUnitTest.uConnect2.Func.IP.TcpClient.Ipv6Connectivity",
        "NUnitTest.uConnect2.Func.Bluetooth.SPS.SpsConnectInterop.SpsConnectToNinaB3Peripheral",
      ];
      const seen = [];
      const dumps = arriving.map((name) => {
        seen.push(name);
        return window.TestNameTree.dump(window.TestNameTree.build(seen)).trim();
      });
      expect(dumps[0]).toBe(
        "NUnitTest.uConnect2.Func.IP.TcpClient.GracefulDisconnect"
      );
      expect(dumps[1]).toBe(
        [
          "NUnitTest.uConnect2.Func",
          "  IP.TcpClient.GracefulDisconnect",
          "  Bluetooth.SPS.SpsConnectInterop.SpsConnectFromNinaW15Central",
        ].join("\n")
      );
      expect(dumps[3]).toBe(
        [
          "NUnitTest.uConnect2.Func",
          "  IP.TcpClient",
          "    GracefulDisconnect",
          "    Ipv6Connectivity",
          "  Bluetooth.SPS.SpsConnectInterop",
          "    SpsConnectFromNinaW15Central",
          "    SpsConnectToNinaB3Peripheral",
        ].join("\n")
      );
    });

    test("preserves first-seen sibling order", () => {
      const forest = window.TestNameTree.build([
        "Root.GroupB.Two",
        "Root.GroupA.One",
        "Root.GroupB.One",
      ]);
      expect(labels(forest[0].children)).toEqual(["GroupB", "GroupA.One"]);
      expect(labels(findNode(forest, "GroupB").children)).toEqual([
        "Two",
        "One",
      ]);
    });

    test("toMatrixTree matches compact labels and leaf ids", () => {
      const names = [
        "Foo.MyTest(6.0)",
        "Foo.MyTest(7.0)",
      ];
      const matrix = window.TestNameTree.toMatrixTree(
        window.TestNameTree.build(names)
      );
      expect(Object.keys(matrix)).toEqual(["Foo"]);
      expect(matrix.Foo.isLeaf).toBe(false);
      expect(Object.keys(matrix.Foo.children)).toEqual([
        "MyTest(6.0)",
        "MyTest(7.0)",
      ]);
      expect(matrix.Foo.children["MyTest(6.0)"].isLeaf).toBe(true);
      expect(matrix.Foo.children["MyTest(6.0)"].testCaseId).toBe(
        "Foo.MyTest(6.0)"
      );
      expect(matrix.Foo.path).toBe("Foo");
      expect(matrix.Foo.children["MyTest(6.0)"].path).toBe("Foo.MyTest(6.0)");
    });

    test("handles a large shuffled set without dropping leaves", () => {
      const names = [];
      for (let g = 0; g < 20; g++) {
        for (let i = 0; i < 15; i++) {
          names.push(`NUnitTest.Suite.Group${g}.Case${i}`);
        }
      }
      const forest = window.TestNameTree.build(shuffle(names));
      expect(leafNames(forest).sort()).toEqual(names.slice().sort());
      expect(forest[0].label).toBe("NUnitTest.Suite");
      expect(forest[0].children).toHaveLength(20);
      forest[0].children.forEach((group) => {
        expect(group.children).toHaveLength(15);
      });
    });
  });
});

"use strict";

const LogGrouping = require("../../src/testrift_server/static/log_grouping.js");

describe("LogGrouping", () => {
  const startTime = new Date("2026-08-21T12:02:53.560Z");
  const rxKey = LogGrouping.key("NoraW36 [5] CMD", "rx");

  test("joins the same source and direction inside the timeout", () => {
    expect(
      LogGrouping.shouldJoin(
        { key: rxKey, startTime },
        {
          key: LogGrouping.key("NoraW36 [5] CMD", "RX"),
          time: new Date("2026-08-21T12:02:53.561Z"),
          kind: null,
        },
        10
      )
    ).toBe(true);
  });

  test("does not join across direction changes", () => {
    expect(
      LogGrouping.shouldJoin(
        { key: rxKey, startTime },
        {
          key: LogGrouping.key("NoraW36 [5] CMD", "tx"),
          time: startTime,
          kind: null,
        },
        10
      )
    ).toBe(false);
  });

  test("does not join beyond the timeout from the group start", () => {
    expect(
      LogGrouping.shouldJoin(
        { key: rxKey, startTime },
        {
          key: rxKey,
          time: new Date("2026-08-21T12:02:53.571Z"),
          kind: null,
        },
        10
      )
    ).toBe(false);
  });
});

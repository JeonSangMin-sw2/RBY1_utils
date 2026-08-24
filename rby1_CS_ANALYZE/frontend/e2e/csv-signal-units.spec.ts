import { expect, test } from "@playwright/test";
import { csvSignalDisplayValue, csvSignalUnit } from "../src/csvSignalUnits";

test("CSV 물리 신호의 표시 단위와 변환 배율을 유지한다", () => {
  expect(csvSignalUnit("position")).toMatchObject({ axisLabel: "위치 (deg)", symbol: "deg" });
  expect(csvSignalDisplayValue("position", Math.PI)).toBeCloseTo(180, 12);

  expect(csvSignalUnit("velocity")).toMatchObject({ axisLabel: "속도 (deg/s)", symbol: "deg/s" });
  expect(csvSignalDisplayValue("velocity", Math.PI / 2)).toBeCloseTo(90, 12);

  expect(csvSignalUnit("current")).toMatchObject({ axisLabel: "전류 (A)", symbol: "A" });
  expect(csvSignalDisplayValue("current", 3.5)).toBe(3.5);

  expect(csvSignalUnit("torque")).toMatchObject({ axisLabel: "토크 (Nm)", symbol: "Nm" });
  expect(csvSignalDisplayValue("torque", 12.25)).toBe(12.25);
});

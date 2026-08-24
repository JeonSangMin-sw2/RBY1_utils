import { expect, test } from "@playwright/test";
import { confirmedLogMessage } from "../src/logText";

test("확인된 내용에는 RPC 로그 메시지만 표시한다", () => {
  const excerpt = "[06/01/26 13:48:20.693555] [info] [05:22:39.823348] [ControlInterface] Control was preempted or canceled by another request.";

  expect(confirmedLogMessage(excerpt, { component: "ControlInterface" })).toBe(
    "Control was preempted or canceled by another request.",
  );
});

test("메시지 본문에 포함된 대괄호는 보존한다", () => {
  const excerpt = "[07/01/26 10:23:14.462607] [error] [00:09:55.603007] [ControlManager::CheckRobotStateTimeout] Joint(s) ([14]) are in invalid state.";

  expect(confirmedLogMessage(excerpt, { component: "ControlManager::CheckRobotStateTimeout" })).toBe(
    "Joint(s) ([14]) are in invalid state.",
  );
});

test("ISO 시각 접두부도 제거하고 알 수 없는 본문 접두부는 유지한다", () => {
  const excerpt = "2026-07-13T01:02:03Z [warning] [motion] [retry 2] command delayed";

  expect(confirmedLogMessage(excerpt, { component: "motion" })).toBe("[retry 2] command delayed");
});

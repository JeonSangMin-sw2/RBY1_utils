import { expect, test } from "./fixtures";

test("V4 offline incident triage, CSV analysis, robot visualization, and reopen @network-off", async ({ page, baseURL }) => {
  test.setTimeout(120_000);
  expect(baseURL, "network-off project requires a loopback baseURL").toBeTruthy();
  const remote: string[] = [];
  const chartRequests: string[] = [];
  let failViewerMesh = false;
  await page.route("**/models/**/LINK_1.glb", async (route) => {
    if (failViewerMesh) await route.abort("failed");
    else await route.continue();
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    const host = url.hostname;
    if (!["127.0.0.1", "localhost", "[::1]"].includes(host)) remote.push(request.url());
    if (/\/api\/v3\/cases\/[^/]+\/csvs\/\d+\/chart$/.test(url.pathname)) chartRequests.push(request.url());
  });

  await page.goto(`${baseURL}/#bootstrap=e2e-proof`);
  await expect.poll(() => page.evaluate(() => location.hash)).toBe("");
  const persisted = await page.evaluate(() => ({
    local: Object.values(localStorage),
    session: Object.values(sessionStorage),
    cookie: document.cookie,
    query: location.search,
  }));
  expect(JSON.stringify(persisted)).not.toContain("e2e-proof");
  await page.reload();
  await expect(page.getByRole("heading", { name: "분석할 파일을 가져오십시오" })).toBeVisible();

  const intermediateProgress = page.evaluate(() => new Promise<number>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      observer.disconnect();
      reject(new Error("No intermediate analysis progress was rendered"));
    }, 20_000);
    const observer = new MutationObserver(() => {
      const progress = document.querySelector<HTMLElement>('[role="progressbar"]');
      const value = Number(progress?.getAttribute("aria-valuenow"));
      if (value > 0 && value < 100) {
        window.clearTimeout(timeout);
        observer.disconnect();
        resolve(value);
      }
    });
    observer.observe(document.body, { attributes: true, childList: true, subtree: true });
  }));

  const incidentChain = Buffer.from(
    "[06/30/26 08:00:00.000000] [info] [00:00:00.000000] [App] RBY1 Model: rby1m, Model Version: v1.3\n"
    + "[06/30/26 09:09:30.000000] [debug] [00:31:10.000000] "
    + "[Service::PowerService::JointCommand] Requested: name: \".*\" command: COMMAND_SERVO_ON\n"
    + "[06/30/26 09:09:30.100000] [info] [00:31:10.100000] "
    + "[Hardware::ExecutePowerCommand] Engaging brakes...\n"
    + "[06/30/26 09:09:30.200000] [info] [00:31:10.200000] "
    + "[Hardware::ExecutePowerCommand] Brakes engaged\n"
    + "[06/30/26 09:09:30.300000] [info] [00:31:10.300000] "
    + "[Hardware::ExecutePowerCommand] Switched to gravity compensation mode\n"
    + "[06/30/26 09:09:31.000000] [info] [00:31:11.000000] "
    + "[Hardware::ExecutePowerCommand] Power command succeeded\n"
    + "[06/30/26 09:10:58.638238] [error] [00:32:38.768002] "
    + "[ControlManager::CheckRobotStateTimeout] Timeout: Joint 'right_arm_3' state update exceeded timeout. "
    + "Robot duration: 2.996 ms, Joint duration: 5284140.001 ms, Timeout threshold: 200.000 ms\n"
    + "[06/30/26 09:10:58.639238] [info] [00:32:38.769002] "
    + "[ControlManager] Control Manager state changed to 'MajorFault'.\n"
    + "[06/30/26 09:10:58.640238] [error] [00:32:38.770002] "
    + "[ControlManager] Major Fault Reaction Started...\n"
    + "[06/30/26 09:10:58.641238] [info] [00:32:38.771002] "
    + "[Hardware::ExecutePowerCommand] Started: device name = 48v|.*, command = PowerOff\n",
  );
  const targetLimit = Buffer.from(
    "[06/30/26 09:11:05.000000] [error] [00:32:45.000000] [ControlManager] Invalid request: "
    + "Target position at index 5 is 0.87, which exceeds the maximum allowed bound 0.79.\n",
  );
  const nonFailures = Buffer.from(
    "[06/30/26 09:11:10.000000] [info] [00:32:50.000000] [ControlInterface] "
    + "Control was preempted or canceled by another request.\n"
    + "[06/30/26 09:11:11.000000] [debug] [00:32:51.000000] "
    + "[Service::ParameterService::GetParameter] Requested: name: \"servo_on_command.timeout\"\n",
  );
  const standaloneCritical = Buffer.from(
    "[06/30/26 09:12:00.000000] [info] [00:33:40.000000] "
    + "[ControlManager] Control Manager state changed to 'MajorFault'.\n",
  );
  const largeLog = Buffer.concat([
    incidentChain,
    targetLimit,
    nonFailures,
    standaloneCritical,
    Buffer.alloc(24 * 1024 * 1024, "ordinary diagnostic context line\n"),
  ]);
  const additionalArmPositionColumns = [
    ...Array.from({ length: 7 }, (_, index) => `right_arm_${index}_pos`).filter((name) => name !== "right_arm_3_pos"),
    ...Array.from({ length: 7 }, (_, index) => `left_arm_${index}_pos`).filter((name) => name !== "left_arm_2_pos"),
  ];
  const additionalArmPositionValues = (base: number) => additionalArmPositionColumns
    .map((_, index) => (base + index * 0.01).toFixed(2))
    .join(",");
  const faultCsv = Buffer.from(
    "# Fault Occurred At: 2026-06-30T09:10:58.700+00:00\n"
    + `timestamp,power_5v,power_12v,power_24v,power_48v,control_manager_state,control_state,right_arm_3_state,right_arm_3_pos,right_arm_3_target_pos,right_arm_3_vel,right_arm_3_target_vel,right_arm_3_cur,right_arm_3_tq,left_arm_2_state,left_arm_2_pos,left_arm_2_target_pos,left_arm_2_vel,left_arm_2_target_vel,left_arm_2_cur,left_arm_2_tq,head_0_pos,head_0_cur,head_0_tq,torso_0_pos,torso_0_cur,torso_0_tq,left_wheel_pos,left_wheel_cur,left_wheel_tq,right_wheel_pos,right_wheel_cur,right_wheel_tq,${additionalArmPositionColumns.join(",")}\n`
    + `0,1,1,1,1,1,1,7,0.10,0.10,0.01,0.01,0.2,0.3,7,-0.10,-0.10,-0.01,-0.01,0.2,0.3,0.01,0.1,0.2,0.02,0.2,0.3,0.03,0.3,0.4,-0.03,0.3,0.4,${additionalArmPositionValues(0.10)}\n`
    + `1,1,1,1,1,1,1,7,0.55,0.56,0.02,0.03,0.3,0.4,7,-0.55,-0.56,-0.02,-0.03,0.3,0.4,0.02,0.2,0.3,0.03,0.3,0.4,0.04,0.4,0.5,-0.04,0.4,0.5,${additionalArmPositionValues(0.20)}\n`
    + `2,1,1,1,0,3,2,1031,1.00,1.01,0.03,0.04,0.4,0.5,263,-1.00,-1.01,-0.03,-0.04,0.4,0.5,0.03,0.3,0.4,0.04,0.4,0.5,0.05,0.5,0.6,-0.05,0.5,0.6,${additionalArmPositionValues(0.30)}\n`,
  );
  const secondFaultCsv = Buffer.from(
    "# Fault Occurred At: 2026-06-30T09:20:58.700+00:00\n"
    + "timestamp,power_5v,power_12v,power_24v,power_48v,control_manager_state,control_state,right_arm_3_state,right_arm_3_pos,right_arm_3_target_pos,right_arm_3_vel,right_arm_3_target_vel,right_arm_3_cur,right_arm_3_tq,left_arm_2_state,left_arm_2_pos,left_arm_2_target_pos,left_arm_2_vel,left_arm_2_target_vel,left_arm_2_cur,left_arm_2_tq\n"
    + "0,1,1,1,1,1,1,7,0.20,0.20,0.01,0.01,0.5,0.6,7,-0.20,-0.20,-0.01,-0.01,0.5,0.6\n"
    + "1,1,1,1,1,1,1,7,0.21,0.22,0.02,0.03,0.6,0.7,7,-0.21,-0.22,-0.02,-0.03,0.6,0.7\n"
    + "2,1,1,1,0,3,2,1031,0.22,0.23,0.03,0.04,0.7,0.8,263,-0.22,-0.23,-0.03,-0.04,0.7,0.8\n",
  );
  const disjointFaultCsv = Buffer.from(
    "# Fault Occurred At: 2026-06-30T09:30:58.700+00:00\n"
    + "timestamp,power_5v,power_12v,power_24v,power_48v,control_manager_state,control_state,head_1_pos,head_1_cur\n"
    + "0,1,1,1,1,1,1,0.30,0.8\n"
    + "1,1,1,1,1,1,1,0.31,0.9\n"
    + "2,1,1,1,0,3,2,0.32,1.0\n",
  );

  const fileInput = page.getByLabel("분석할 로그 파일 선택");
  await expect(page.getByLabel("분석할 로그 폴더 선택")).toHaveAttribute("webkitdirectory", "");
  await fileInput.setInputFiles([
    { name: "robot.log", mimeType: "text/plain", buffer: largeLog },
    { name: "fault-2026-06-30_09-10-58-700.csv", mimeType: "text/csv", buffer: faultCsv },
    { name: "fault-2026-06-30_09-20-58-700.csv", mimeType: "text/csv", buffer: secondFaultCsv },
    { name: "fault-2026-06-30_09-30-58-700.csv", mimeType: "text/csv", buffer: disjointFaultCsv },
  ]);
  await expect(fileInput).toHaveValue("");
  expect(await intermediateProgress).toBeGreaterThan(0);

  await expect(page.getByRole("heading", { name: "장애 사건", exact: true }).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".incidentRow")).toHaveCount(3, { timeout: 30_000 });
  await expect(page.locator(".criticalMetric strong")).toHaveText("2");
  await expect(page.locator(".criticalMetric")).toContainText("Major / Minor Fault");
  await expect(page.locator(".rangeMetric")).toContainText("06/30 09:10:58 ~ 09:12:00");
  await expect(page.locator(".triageBand")).not.toContainText("영향 대상");
  await expect(page.locator(".triageBand")).not.toContainText("Fault CSV");
  await expect(page.locator(".triageBand")).not.toContainText("미분류");
  await expect(page.locator(".layerSummary")).toContainText("모터 / 조인트");
  await expect(page.locator(".navButtons")).toHaveCount(0);
  await expect(page.locator(".incidentList")).not.toContainText("servo_on_command.timeout");
  await expect(page.locator(".incidentList")).not.toContainText("preempted");

  const firstIncident = page.locator(".incidentRow").first();
  await expect(firstIncident).toContainText("관절 상태 갱신 시간 초과");
  await expect(firstIncident.locator("time span")).toHaveText("06/30");
  await expect(firstIncident.locator("time strong")).toHaveText("09:10:58");
  const cardColumns = await firstIncident.evaluate((element) => {
    const time = element.querySelector("time")?.getBoundingClientRect();
    const main = element.querySelector(".incidentMain")?.getBoundingClientRect();
    return { timeRight: time?.right ?? 0, mainLeft: main?.left ?? 0 };
  });
  expect(cardColumns.timeRight).toBeLessThanOrEqual(cardColumns.mainLeft);
  await expect(page.getByRole("heading", { name: "발생 순서" })).toBeVisible();
  await expect(page.locator(".evidenceRow")).toHaveCount(9);
  await expect(page.locator(".evidenceRow").first()).toContainText("COMMAND_SERVO_ON");
  await expect(page.locator(".evidenceRow").first().locator(".roleLabel")).toHaveText("선행 명령");
  await expect(page.locator(".evidenceRow").nth(4).locator(".roleLabel")).toHaveText("명령 성공");
  await expect(page.locator(".evidenceSequence")).toContainText("right_arm_3");
  const primaryEvidence = page.locator(".evidenceRow.primaryEvidence");
  await expect(primaryEvidence).toHaveCount(1);
  await expect(primaryEvidence).toContainText("대표 장애 로그");
  await expect(primaryEvidence).toContainText("state update exceeded timeout");
  const primaryEvidenceStyle = await primaryEvidence.evaluate((element) => {
    const style = getComputedStyle(element);
    return { backgroundColor: style.backgroundColor, borderLeftWidth: style.borderLeftWidth };
  });
  expect(primaryEvidenceStyle.backgroundColor).toBe("rgb(41, 20, 22)");
  expect(Number.parseFloat(primaryEvidenceStyle.borderLeftWidth)).toBeGreaterThanOrEqual(4);
  await primaryEvidence.screenshot({ path: "test-results/v3-primary-evidence.png" });
  const evidenceColumns = await page.locator(".evidenceRow").first().locator("summary").evaluate((element) => {
    const time = element.querySelector("time")?.getBoundingClientRect();
    const role = element.querySelector(".roleLabel")?.getBoundingClientRect();
    const message = element.querySelector("div")?.getBoundingClientRect();
    return {
      timeRight: time?.right ?? 0,
      roleLeft: role?.left ?? 0,
      roleRight: role?.right ?? 0,
      messageLeft: message?.left ?? 0,
    };
  });
  expect(evidenceColumns.timeRight).toBeLessThanOrEqual(evidenceColumns.roleLeft);
  expect(evidenceColumns.roleRight).toBeLessThanOrEqual(evidenceColumns.messageLeft);
  const detailBadgeStyle = await page.locator(".severityPillar span").evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return { writingMode: style.writingMode, width: rect.width, height: rect.height };
  });
  expect(detailBadgeStyle.writingMode).toBe("horizontal-tb");
  expect(detailBadgeStyle.width).toBeGreaterThan(detailBadgeStyle.height);
  await expect(page.getByRole("heading", { name: "가능한 원인" })).toBeVisible();
  await expect(page.locator(".causeSection")).toContainText("CAN 통신");
  await expect(page.locator(".checkSection")).toContainText("엔코더 값이 변하는지");
  await expect(page.locator(".csvMatchLine")).toContainText("CSV 시각 일치");
  const chartCanvas = page.locator(".chart canvas");
  await expect(chartCanvas).toBeVisible();
  const paintedChartPixels = await chartCanvas.evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext("2d");
    if (!context) return 0;
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let painted = 0;
    for (let index = 3; index < pixels.length; index += 4) {
      if (pixels[index] > 0) painted += 1;
    }
    return painted;
  });
  expect(paintedChartPixels).toBeGreaterThan(100);
  const criticalIncident = page.locator(".incidentRow.fault-major");
  await expect(criticalIncident).toHaveCount(2);
  await expect(criticalIncident.first()).toContainText("Major Fault");
  await expect(criticalIncident.last()).toContainText("MajorFault");
  const criticalStyle = await criticalIncident.first().evaluate((element) => {
    const row = getComputedStyle(element);
    const badge = getComputedStyle(element.querySelector(".incidentTitle > span") as Element);
    return { background: row.backgroundColor, borderWidth: row.borderLeftWidth, badgeBackground: badge.backgroundColor };
  });
  expect(criticalStyle.background).not.toBe("rgb(17, 20, 23)");
  expect(Number.parseFloat(criticalStyle.borderWidth)).toBeGreaterThanOrEqual(5);
  expect(criticalStyle.badgeBackground).not.toBe("rgba(0, 0, 0, 0)");
  await page.locator(".criticalMetric").click();
  await expect(page.locator(".incidentRow")).toHaveCount(2);
  await expect(page.locator(".criticalMetric")).toHaveAttribute("aria-pressed", "true");
  await page.locator(".triageMetric").first().click();
  await expect(page.locator(".incidentRow")).toHaveCount(3);
  await expect(page.locator(".layerMetric.layer-all")).toHaveCount(0);
  await page.locator(".layerMetric.layer-motor").click();
  await expect(page.locator(".incidentRow")).toHaveCount(1);
  await expect(page.locator(".layerMetric.layer-motor")).toHaveAttribute("aria-pressed", "true");
  await page.locator(".triageMetric").first().click();
  await expect(page.locator(".incidentRow")).toHaveCount(3);
  await page.screenshot({ path: "test-results/v3-desktop.png", fullPage: true });

  await page.getByRole("button", { name: "CSV", exact: true }).click();
  await expect(page.getByRole("heading", { name: "CSV 전체 신호 분석" })).toBeVisible();
  await expect(page.locator(".csvStats")).toContainText("3");
  await expect(page.locator(".csvStats > div")).toHaveCount(3);
  await expect(page.locator(".csvStats")).not.toContainText("조인트");
  await expect(page.locator(".csvStats")).not.toContainText("신호");
  const csvSubtitleSize = await page.locator(".csvWorkspaceHeader p").evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
  expect(csvSubtitleSize).toBeGreaterThanOrEqual(13);
  await expect(page.locator(".jointGroupBlock")).toHaveCount(5);
  await expect(page.locator(".jointGroup-wheel")).toContainText("left_wheel");
  await expect(page.locator(".jointGroup-wheel")).toContainText("right_wheel");
  const headJoint = page.getByRole("checkbox", { name: "head_0" });
  const leftJoint = page.getByRole("checkbox", { name: "left_arm_2" });
  const rightJoint = page.getByRole("checkbox", { name: "right_arm_3" });
  const headGroup = page.getByRole("button", { name: "CSV Head 그룹 선택 전환" });
  const leftArmGroup = page.getByRole("button", { name: "CSV Left Arm 그룹 선택 전환" });
  const rightArmGroup = page.getByRole("button", { name: "CSV Right Arm 그룹 선택 전환" });
  const selectedJointCount = page.locator(".jointSelectorHead span");
  await expect(headJoint).toBeChecked();
  await expect(headGroup).toHaveAttribute("aria-pressed", "true");
  await headGroup.click();
  await expect(headJoint).not.toBeChecked();
  await expect(selectedJointCount).toHaveText("0 / 18개 선택");
  await leftArmGroup.click();
  await expect(page.locator(".jointGroup-left_arm input:checked")).toHaveCount(7);
  await expect(leftJoint).toBeChecked();
  await expect(rightJoint).not.toBeChecked();
  await expect(leftArmGroup).toHaveAttribute("aria-pressed", "true");
  await expect(rightArmGroup).toHaveAttribute("aria-pressed", "false");
  await expect(selectedJointCount).toHaveText("7 / 18개 선택");
  await expect.poll(() => chartRequests.length).toBeGreaterThan(0);
  const leftOnlySeries = new URL(chartRequests.at(-1)!).searchParams.getAll("series");
  expect(leftOnlySeries).toContain("left_arm_2_pos");
  expect(leftOnlySeries).not.toContain("right_arm_3_pos");
  const leftOnlyRequestCount = chartRequests.length;
  await rightArmGroup.click();
  await expect(page.locator(".jointGroup-left_arm input:checked")).toHaveCount(7);
  await expect(page.locator(".jointGroup-right_arm input:checked")).toHaveCount(7);
  await expect(leftJoint).toBeChecked();
  await expect(rightJoint).toBeChecked();
  await expect(leftArmGroup).toHaveAttribute("aria-pressed", "true");
  await expect(rightArmGroup).toHaveAttribute("aria-pressed", "true");
  await expect(selectedJointCount).toHaveText("14 / 18개 선택");
  await expect(page.locator(".selectedSeriesText")).toContainText("left_arm_2_pos");
  await expect(page.locator(".selectedSeriesText")).toContainText("right_arm_3_pos");
  await expect.poll(() => chartRequests.length).toBeGreaterThan(leftOnlyRequestCount);
  const initialChartSeries = new URL(chartRequests.at(-1)!).searchParams.getAll("series");
  expect(initialChartSeries).toContain("left_arm_2_pos");
  expect(initialChartSeries).toContain("right_arm_3_pos");
  expect(initialChartSeries).not.toContain("left_arm_2_state");
  await leftArmGroup.click();
  await expect(page.locator(".jointGroup-left_arm input:checked")).toHaveCount(0);
  await expect(page.locator(".jointGroup-right_arm input:checked")).toHaveCount(7);
  await expect(leftArmGroup).toHaveAttribute("aria-pressed", "false");
  await expect(rightArmGroup).toHaveAttribute("aria-pressed", "true");
  await expect(selectedJointCount).toHaveText("7 / 18개 선택");
  await leftArmGroup.click();
  await expect(selectedJointCount).toHaveText("14 / 18개 선택");
  const initialChartRequestCount = chartRequests.length;
  await page.getByRole("button", { name: "선택 해제" }).click();
  await expect(leftJoint).not.toBeChecked();
  await expect(rightJoint).not.toBeChecked();
  await expect(page.locator(".csvPlot-primary .chartLoading")).toHaveText("선택한 항목에 표시할 샘플이 없습니다.");
  await page.getByRole("button", { name: "전체 선택" }).click();
  await expect(leftJoint).toBeChecked();
  await expect(rightJoint).toBeChecked();
  await expect.poll(() => chartRequests.length).toBeGreaterThan(initialChartRequestCount);
  const csvSelect = page.getByLabel("분석할 CSV 파일");
  await expect(csvSelect.locator("option")).toHaveCount(3);
  const csvValues = await csvSelect.locator("option").evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
  await csvSelect.selectOption(csvValues[1]);
  await expect(rightJoint).toBeChecked();
  await expect(leftJoint).not.toBeChecked();
  await expect(page.locator(".csvPlot-primary .csvChart canvas")).toBeVisible();
  await page.getByRole("button", { name: "CSV Left Arm 그룹 선택 전환" }).click();
  await expect(leftJoint).toBeChecked();
  await expect(rightJoint).toBeChecked();
  await csvSelect.selectOption(csvValues[0]);
  await expect(leftJoint).toBeChecked();
  await expect(rightJoint).toBeChecked();
  await csvSelect.selectOption(csvValues[2]);
  const disjointHead = page.getByRole("checkbox", { name: "head_1" });
  await expect(disjointHead).toBeChecked();
  await expect(page.locator(".csvPlot-primary .selectedSeriesText")).toContainText("head_1_pos");
  await expect(page.locator(".csvPlot-primary .csvChart canvas")).toBeVisible();
  await csvSelect.selectOption(csvValues[0]);
  await expect(leftJoint).toBeChecked();
  await expect(rightJoint).toBeChecked();

  const comparisonPlotSelect = page.getByLabel("비교 Plot에 추가할 신호");
  const addComparisonPlot = page.getByRole("button", { name: "Plot 추가" });
  for (const comparison of ["current", "torque", "state"]) {
    await comparisonPlotSelect.selectOption(comparison);
    await addComparisonPlot.click();
  }
  await expect(page.locator(".csvPlot")).toHaveCount(4);
  await expect(page.locator(".csvPlot-comparison")).toHaveCount(3);
  await expect(page.locator(".csvPlot-comparison").first().locator(".selectedSeriesText")).toContainText("left_arm_2_cur");
  await expect(page.locator(".csvPlot-comparison").first().locator(".selectedSeriesText")).toContainText("right_arm_3_cur");
  const primaryPlotBox = await page.locator(".csvPlot-primary").boundingBox();
  const secondaryPlotBox = await page.locator(".csvPlot-comparison").first().boundingBox();
  expect(primaryPlotBox).not.toBeNull();
  expect(secondaryPlotBox).not.toBeNull();
  expect(primaryPlotBox!.y).toBeLessThan(secondaryPlotBox!.y);
  await expect(page.getByRole("img", { name: /비교 CSV 전류 그래프/ })).toBeVisible();
  const primaryTimeline = page.locator(".csvPlot-primary .csvTimeline");
  const secondaryTimeline = page.locator(".csvPlot-comparison .csvTimeline").first();
  await expect(primaryTimeline).toHaveAttribute("data-y-unit", "deg");
  expect(Number(await primaryTimeline.getAttribute("data-y-scale"))).toBeCloseTo(180 / Math.PI, 12);
  await expect(page.getByRole("img", { name: /CSV 위치 그래프/ })).toHaveAccessibleName(/Y축 위치 \(deg\)/);
  await expect(secondaryTimeline).toHaveAttribute("data-y-unit", "A");
  await expect(secondaryTimeline).toHaveAttribute("data-y-scale", "1");
  await expect(page.getByRole("img", { name: /비교 CSV 전류 그래프/ })).toHaveAccessibleName(/Y축 전류 \(A\)/);
  const primaryCanvas = page.locator(".csvPlot-primary .csvChart canvas");
  await primaryCanvas.scrollIntoViewIfNeeded();
  const primaryCanvasBox = await primaryCanvas.boundingBox();
  if (!primaryCanvasBox) throw new Error("Primary CSV canvas has no bounding box");
  await page.mouse.move(
    primaryCanvasBox.x + primaryCanvasBox.width - 80,
    primaryCanvasBox.y + primaryCanvasBox.height / 2,
  );
  await page.mouse.wheel(0, -500);
  await expect.poll(async () => {
    const start = Number(await primaryTimeline.getAttribute("data-zoom-start"));
    const end = Number(await primaryTimeline.getAttribute("data-zoom-end"));
    return end - start;
  }).toBeLessThan(100);
  await expect.poll(async () => {
    const primaryStart = await primaryTimeline.getAttribute("data-zoom-start");
    const primaryEnd = await primaryTimeline.getAttribute("data-zoom-end");
    const ranges = await page.locator(".csvPlot-comparison .csvTimeline").evaluateAll((elements) => elements.map((element) => ({
      start: element.getAttribute("data-zoom-start"),
      end: element.getAttribute("data-zoom-end"),
    })));
    return ranges.every((range) => primaryStart === range.start && primaryEnd === range.end);
  }).toBe(true);
  await page.screenshot({ path: "test-results/v4-csv-multi-plot.png", fullPage: true });
  await page.getByRole("button", { name: "비교 Plot 삭제: 상태 비트" }).click();
  await page.getByRole("button", { name: "비교 Plot 삭제: 토크" }).click();
  await page.getByRole("button", { name: "비교 Plot 삭제: 전류" }).click();
  await expect(page.locator(".csvPlot")).toHaveCount(1);
  await page.getByRole("button", { name: /속도/ }).click();
  await expect(primaryTimeline).toHaveAttribute("data-y-unit", "deg/s");
  expect(Number(await primaryTimeline.getAttribute("data-y-scale"))).toBeCloseTo(180 / Math.PI, 12);
  await expect(page.getByRole("img", { name: /CSV 속도 그래프/ })).toHaveAccessibleName(/Y축 속도 \(deg\/s\)/);
  await page.screenshot({ path: "test-results/v3-csv-velocity.png", fullPage: true });
  await page.getByRole("button", { name: /토크/ }).click();
  await expect(primaryTimeline).toHaveAttribute("data-y-unit", "Nm");
  await expect(primaryTimeline).toHaveAttribute("data-y-scale", "1");
  await expect(page.getByRole("img", { name: /CSV 토크 그래프/ })).toHaveAccessibleName(/Y축 토크 \(Nm\)/);
  await page.screenshot({ path: "test-results/v3-csv-torque.png", fullPage: true });
  await page.getByRole("button", { name: /위치/ }).click();
  const selectedSeriesSize = await page.locator(".selectedSeriesText").evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
  expect(selectedSeriesSize).toBeGreaterThanOrEqual(12);
  const stateCategory = page.getByRole("button", { name: /상태 비트/ });
  const categoryDescriptionFontSize = await stateCategory.locator("span").evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
  expect(categoryDescriptionFontSize).toBeGreaterThanOrEqual(11);
  await stateCategory.click();
  await expect(page.locator(".stateJointGroup")).toHaveCount(2);
  await expect(page.getByRole("img", { name: /CSV 상태 비트 그래프/ })).toHaveAccessibleName(/left_arm_2 · JAM/);
  await expect(page.getByRole("img", { name: /CSV 상태 비트 그래프/ })).toHaveAccessibleName(/right_arm_3 · BIG/);
  await expect(page.locator(".stateDecoder")).toContainText("BIG");
  await expect(page.locator(".stateDecoder")).toContainText("Big Position Error");
  await expect(page.locator(".stateDecoder")).toContainText("1031");
  await expect(page.locator(".stateDecoder")).toContainText("1 + 2 + 4 + 1,024 = FET + RUN + INIT + BIG");
  await expect(page.locator(".motorStateGuide")).toContainText("JAM · CUR · BIG · INP");
  await expect(page.locator(".motorStateGuide")).toContainText("joint_state.temperature");
  await expect(page.locator(".motorStateGuide")).toContainText("Dynamixel로 구성된 Head");
  await expect(page.locator(".motorStateGuide")).toContainText("19~31");
  await page.locator(".motorBitReference summary").click();
  await expect(page.locator(".motorBitReference tbody tr")).toHaveCount(20);
  await expect(page.locator(".motorStateGuide")).toContainText("Core Motor Fault 판정 대상");
  await expect(page.locator(".motorBitReference")).toContainText("Motor Fault 판정 대상");
  await expect(page.locator(".stateDecoder")).not.toContainText("직접 Motor Fault");
  await page.screenshot({ path: "test-results/v3-csv-state.png", fullPage: true });
  const csvCanvas = page.locator(".csvChart canvas");
  await expect(csvCanvas).toBeVisible();
  const csvPaintedPixels = await csvCanvas.evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext("2d");
    if (!context) return 0;
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let painted = 0;
    for (let index = 3; index < pixels.length; index += 4) if (pixels[index] > 0) painted += 1;
    return painted;
  });
  expect(csvPaintedPixels).toBeGreaterThan(100);

  await page.getByRole("button", { name: /전원·제어/ }).click();
  await expect(page.locator(".systemStateGrid")).toContainText("1 = ON (켜짐)");
  await expect(page.locator(".systemStateGrid")).toContainText("0 = Unknown (알 수 없음)");
  await expect(page.locator(".systemStateGrid")).toContainText("3 = MajorFault (중대 Fault)");
  await expect(page.locator(".systemStateGrid")).toContainText("2 = Switching (전환 중)");
  const systemGraph = page.getByRole("img", { name: /CSV 전원·제어 그래프/ });
  await expect(systemGraph).toHaveAccessibleName(/Control Manager · Enabled/);
  await expect(systemGraph).toHaveAccessibleName(/Control Manager · MajorFault/);
  await expect(systemGraph).toHaveAccessibleName(/48V · Unknown/);
  await expect(systemGraph).toHaveAccessibleName(/Control · Switching/);
  await expect(systemGraph).not.toHaveAccessibleName(/power_48v/);
  const systemCanvas = page.locator(".csvChart canvas");
  await expect(systemCanvas).toBeVisible();
  const systemCanvasBox = await systemCanvas.boundingBox();
  if (!systemCanvasBox) throw new Error("System CSV canvas has no bounding box");
  await page.mouse.move(
    systemCanvasBox.x + systemCanvasBox.width - 40,
    systemCanvasBox.y + systemCanvasBox.height / 2,
  );
  await expect(page.locator(".csvChart")).toContainText("MajorFault (중대 Fault, 원본 3)");
  await expect(page.locator(".csvChart")).toContainText("Switching (전환 중, 원본 2)");
  await expect(page.locator(".csvChart")).toContainText(/\d{2}:\d{2}:\d{2}\.\d{3}/);
  await page.screenshot({ path: "test-results/v3-csv-system.png", fullPage: true });

  await page.getByRole("button", { name: "시각화", exact: true }).click();
  await expect(page.getByRole("heading", { name: "로봇 자세 시각화" })).toBeVisible();
  await expect(page.locator(".robotViewerToolbar")).toContainText("M Type · V1.3");
  await expect(page.locator(".robotViewerToolbar")).toContainText("로그 확인");
  await expect(page.locator(".robotCanvas")).toHaveAttribute("data-viewer-state", "ready", { timeout: 20_000 });
  await expect(page.getByText("로봇 모델 준비됨")).toBeVisible();
  await expect(page.locator(".visualizationJointSelector .jointGroupBlock")).toHaveCount(5);
  await expect(page.locator(".visualizationJointSelector .jointGroup-wheel")).toContainText("left_wheel");
  await expect(page.locator(".visualizationJointSelector .jointGroup-wheel")).toContainText("right_wheel");
  await page.getByRole("button", { name: "시각화 Wheel 그룹만 선택" }).click();
  await expect(page.getByRole("checkbox", { name: "left_wheel" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "right_wheel" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "left_arm_2" })).not.toBeChecked();
  await page.getByRole("button", { name: "전체 선택" }).click();
  await expect(page.getByRole("checkbox", { name: "left_arm_2" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "right_arm_3" })).toBeChecked();
  await expect(page.getByRole("button", { name: "재생", exact: true })).toBeEnabled();
  await expect(page.locator(".visualizationPlotBody").first()).toHaveAttribute("data-unit", "deg");
  await expect(page.locator(".visualizationPlotBody").nth(1)).toHaveAttribute("data-unit", "A");

  const robotCanvas = page.locator(".robotCanvas canvas");
  await expect(robotCanvas).toBeVisible();
  const robotPixels = async () => robotCanvas.evaluate((canvas: HTMLCanvasElement) => {
    const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    if (!gl) return { painted: 0, checksum: 0 };
    const pixels = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    let painted = 0;
    let checksum = 2166136261;
    for (let index = 0; index < pixels.length; index += 4) {
      if (Math.abs(pixels[index] - 11) + Math.abs(pixels[index + 1] - 14) + Math.abs(pixels[index + 2] - 16) > 12) painted += 1;
      const rgb = pixels[index] | (pixels[index + 1] << 8) | (pixels[index + 2] << 16);
      checksum = Math.imul(checksum ^ rgb, 16777619) >>> 0;
    }
    return { painted, checksum };
  });
  const initialRobotPixels = await robotPixels();
  expect(initialRobotPixels.painted).toBeGreaterThan(5_000);
  const initialJointSignature = await page.locator(".robotCanvas").getAttribute("data-joint-signature");
  expect(initialJointSignature).toBeTruthy();

  const positionCanvas = page.locator(".visualizationPlot").first().locator("canvas");
  const positionBox = await positionCanvas.boundingBox();
  if (!positionBox) throw new Error("Visualization position canvas has no bounding box");
  await page.mouse.click(positionBox.x + positionBox.width * 0.76, positionBox.y + positionBox.height * 0.46);
  await expect(page.locator(".playbackTime strong")).not.toHaveText(/^00:00\.000/);
  await expect.poll(() => page.locator(".robotCanvas").getAttribute("data-joint-signature")).not.toBe(initialJointSignature);

  const robotBox = await robotCanvas.boundingBox();
  if (!robotBox) throw new Error("Robot canvas has no bounding box");
  const cameraBeforeOrbit = await page.locator(".robotCanvas").getAttribute("data-camera-position");
  await page.mouse.move(robotBox.x + robotBox.width * 0.55, robotBox.y + robotBox.height * 0.52);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(robotBox.x + robotBox.width * 0.72, robotBox.y + robotBox.height * 0.44, { steps: 8 });
  await page.mouse.up({ button: "left" });
  await expect.poll(() => page.locator(".robotCanvas").getAttribute("data-camera-position")).not.toBe(cameraBeforeOrbit);
  const targetBeforePan = await page.locator(".robotCanvas").getAttribute("data-camera-target");
  await page.mouse.down({ button: "right" });
  await page.mouse.move(robotBox.x + robotBox.width * 0.65, robotBox.y + robotBox.height * 0.58, { steps: 6 });
  await page.mouse.up({ button: "right" });
  await expect.poll(() => page.locator(".robotCanvas").getAttribute("data-camera-target")).not.toBe(targetBeforePan);

  await page.getByRole("button", { name: "선택 해제" }).click();
  await expect(page.getByRole("button", { name: "재생", exact: true })).toBeDisabled();
  await expect(page.getByText(/제로 포지션 자세/)).toBeVisible();
  await page.getByRole("button", { name: "전체 선택" }).click();
  await page.getByRole("button", { name: "처음", exact: true }).click();
  const timeAtStart = await page.locator(".playbackTime strong").textContent();
  const visualizationPlots = page.locator(".visualizationPlotBody");
  await expect(visualizationPlots).toHaveCount(2);
  await expect(visualizationPlots.first()).toHaveAttribute("data-cursor-visible", "true");
  await expect(visualizationPlots.nth(1)).toHaveAttribute("data-cursor-visible", "true");
  const cursorAtStart = await visualizationPlots.first().getAttribute("data-cursor-time");
  expect(cursorAtStart).not.toBeNull();
  await expect(visualizationPlots.nth(1)).toHaveAttribute("data-cursor-time", cursorAtStart ?? "");
  await page.getByLabel("재생 속도").selectOption("2");
  await page.getByRole("button", { name: "재생", exact: true }).click();
  await page.waitForTimeout(450);
  await expect(page.locator(".playbackTime strong")).not.toHaveText(timeAtStart ?? "");
  await page.getByRole("button", { name: "정지", exact: true }).click();
  await expect.poll(async () => {
    const first = await visualizationPlots.first().getAttribute("data-cursor-time");
    const second = await visualizationPlots.nth(1).getAttribute("data-cursor-time");
    return first !== cursorAtStart && first === second;
  }).toBe(true);
  const playbackCursorPixels = async (index: number) => visualizationPlots.nth(index).locator("canvas").evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext("2d");
    if (!context) return { height: canvas.height, longestVerticalRun: 0 };
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let longestVerticalRun = 0;
    for (let x = 0; x < canvas.width; x += 1) {
      let currentRun = 0;
      for (let y = 0; y < canvas.height; y += 1) {
        const offset = (y * canvas.width + x) * 4;
        const isCursorColor = pixels[offset] > 190 && pixels[offset + 1] > 135 && pixels[offset + 2] < 135;
        currentRun = isCursorColor ? currentRun + 1 : 0;
        longestVerticalRun = Math.max(longestVerticalRun, currentRun);
      }
    }
    return { height: canvas.height, longestVerticalRun };
  });
  for (const index of [0, 1]) {
    const cursorPixels = await playbackCursorPixels(index);
    expect(cursorPixels.longestVerticalRun).toBeGreaterThan(cursorPixels.height * 0.35);
  }
  await page.getByRole("button", { name: "토크", exact: true }).click();
  await expect(page.locator(".visualizationPlotBody").nth(1)).toHaveAttribute("data-unit", "Nm");

  const visualizationPlot = page.locator(".visualizationPlotBody").first();
  const visualizationPlotBox = await visualizationPlot.locator("canvas").boundingBox();
  if (!visualizationPlotBox) throw new Error("Visualization plot canvas has no bounding box");
  await page.mouse.move(visualizationPlotBox.x + visualizationPlotBox.width - 70, visualizationPlotBox.y + visualizationPlotBox.height / 2);
  await page.mouse.wheel(0, -500);
  await expect.poll(async () => {
    const startValue = Number(await visualizationPlot.getAttribute("data-zoom-start"));
    const endValue = Number(await visualizationPlot.getAttribute("data-zoom-end"));
    return endValue - startValue;
  }).toBeLessThan(100);
  await expect.poll(async () => {
    const first = page.locator(".visualizationPlotBody").first();
    const second = page.locator(".visualizationPlotBody").nth(1);
    return await first.getAttribute("data-zoom-start") === await second.getAttribute("data-zoom-start")
      && await first.getAttribute("data-zoom-end") === await second.getAttribute("data-zoom-end");
  }).toBe(true);
  await page.screenshot({ path: "test-results/v4-visualization-desktop.png", fullPage: true });

  failViewerMesh = true;
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  await page.getByRole("button", { name: "시각화", exact: true }).click();
  await expect(page.locator(".robotCanvas")).toHaveAttribute("data-viewer-state", "error", { timeout: 20_000 });
  await expect(page.getByText("모델 로드 실패")).toBeVisible();
  await expect(page.getByText(/필수 메시 1개를 불러오지 못했습니다/)).toBeVisible();
  failViewerMesh = false;

  await page.getByRole("button", { name: "로그", exact: true }).click();
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "left_arm_2" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "right_arm_3" })).toBeChecked();
  await page.getByRole("button", { name: "로그", exact: true }).click();

  await page.locator(".incidentRow").nth(1).click();
  await expect(page.locator(".selectedHeader")).toContainText("Target position 허용 범위 초과");
  await expect(page.locator(".causeSection")).toContainText("target position이 허용 범위 초과");
  await expect(page.locator(".checkSection")).toContainText("해당 축 목표 값 제한하여 다시 제어나 명령");
  await expect(page.locator(".remedySection")).toContainText("허용 범위 안으로 목표값을 수정한 후 명령을 다시 전송하십시오");

  await page.locator(".incidentList").focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator(".incidentRow").nth(2)).toHaveAttribute("aria-current", "true");
  await expect(page.locator(".selectedHeader")).toContainText("MajorFault");
  await page.locator(".incidentRow").nth(1).click();
  await expect(page.locator(".checkSection")).toContainText("해당 축 목표 값 제한하여 다시 제어나 명령");

  const selectedCase = await page.getByLabel("저장된 분석 열기").inputValue();
  await page.getByLabel("저장된 분석 열기").selectOption("");
  await page.getByLabel("저장된 분석 열기").selectOption(selectedCase);
  await expect(page.locator(".incidentRow")).toHaveCount(3);
  await expect(page.locator(".selectedHeader")).toContainText("관절 상태 갱신 시간 초과");

  await page.locator(".incidentRow").nth(1).click();
  await expect(page.locator(".selectedHeader")).toContainText("Target position 허용 범위 초과");
  await expect(page.locator(".checkSection")).toContainText("해당 축 목표 값 제한하여 다시 제어나 명령");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".incidentPanel")).toBeVisible();
  await expect(page.locator(".responsePanel")).toBeVisible();
  await expect(page.locator(".checkSection")).toContainText("해당 축 목표 값 제한하여 다시 제어나 명령");
  await expect(page.locator(".chartEmpty")).toContainText("Fault CSV");
  const horizontalOverflow = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll<HTMLElement>("body *")]
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          className: element.className,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      })
      .filter(({ left, right }) => left < 0 || right > viewportWidth)
      .slice(0, 20);
    return {
      clientWidth: viewportWidth,
      scrollWidth: document.documentElement.scrollWidth,
      containers: [document.body, document.querySelector<HTMLElement>("#root"), document.querySelector<HTMLElement>(".appShell"), document.querySelector<HTMLElement>(".layerSummary")]
        .filter((element): element is HTMLElement => Boolean(element))
        .map((element) => ({
          className: element.className,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          overflowX: getComputedStyle(element).overflowX,
          rect: Math.round(element.getBoundingClientRect().width),
        })),
      offenders,
    };
  });
  expect(
    horizontalOverflow.scrollWidth,
    `mobile layout must not overflow horizontally: ${JSON.stringify(horizontalOverflow)}`,
  ).toBe(horizontalOverflow.clientWidth);
  await page.screenshot({ path: "test-results/v3-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "시각화", exact: true }).click();
  await expect(page.locator(".robotCanvas")).toHaveAttribute("data-viewer-state", "ready", { timeout: 20_000 });
  expect((await robotPixels()).painted).toBeGreaterThan(3_000);
  const visualizationMobileOverflow = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(visualizationMobileOverflow.scrollWidth).toBe(visualizationMobileOverflow.clientWidth);
  await page.screenshot({ path: "test-results/v4-visualization-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "로그", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  const transfer = await page.evaluateHandle(() => new DataTransfer());
  await page.locator("main").dispatchEvent("dragenter", { dataTransfer: transfer });
  await expect(page.getByText("여기에 파일 또는 폴더를 놓아 분석")).toBeVisible();
  await page.locator("main").evaluate((element) => {
    const folderContentA = [
      "# Fault Occurred At: 2026-07-14T09:00:00.000+09:00\n"
      + "timestamp,right_arm_0_state,right_arm_0_pos,right_arm_0_target_pos\n"
      + "0,7,0.1,0.1\n1,263,0.2,0.25\n",
    ];
    const folderContentB = [
      "# Fault Occurred At: 2026-07-14T09:00:00.000+09:00\n"
      + "timestamp,right_arm_0_state,right_arm_0_pos,right_arm_0_target_pos\n"
      + "0,7,0.1,0.1\n1,263,0.2,0.26\n",
    ];
    const fileA = new File(folderContentA, "folder-fault.csv", { type: "text/csv", lastModified: 1 });
    const fileB = new File(folderContentB, "folder-fault.csv", { type: "text/csv", lastModified: 1 });
    const fallbackFile = new File([
      "# Fault Occurred At: 2026-07-14T09:01:00.000+09:00\n"
      + "timestamp,left_arm_0_state,left_arm_0_pos\n"
      + "0,7,0.1\n1,7,0.2\n",
    ], "fallback-fault.csv", { type: "text/csv" });
    const ignored = new File(["not an analyzer input"], "notes.txt", { type: "text/plain" });
    const fileEntry = (value: File) => ({
      isFile: true,
      isDirectory: false,
      name: value.name,
      file: (resolve: (selected: File) => void) => resolve(value),
    });
    const failedEntry = {
      isFile: true,
      isDirectory: false,
      name: "unreadable.log",
      file: (_resolve: (selected: File) => void, reject?: (error: DOMException) => void) => {
        reject?.(new DOMException("read failed"));
      },
    };
    const nestedDirectory = (name: string, value: File) => ({
      isFile: false,
      isDirectory: true,
      name,
      createReader: () => {
        let complete = false;
        return {
          readEntries: (resolve: (entries: object[]) => void) => {
            resolve(complete ? [] : [fileEntry(value), fileEntry(ignored)]);
            complete = true;
          },
        };
      },
    });
    const rootDirectory = {
      isFile: false,
      isDirectory: true,
      name: "logs",
      createReader: () => {
        let complete = false;
        return {
          readEntries: (resolve: (entries: object[]) => void) => {
            resolve(complete ? [] : [
              nestedDirectory("robot-a", fileA),
              nestedDirectory("robot-b", fileB),
              failedEntry,
            ]);
            complete = true;
          },
        };
      },
    };
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", {
      value: {
        files: [fallbackFile, ignored],
        items: [
          { kind: "file", webkitGetAsEntry: () => rootDirectory, getAsFile: () => null },
          { kind: "file", webkitGetAsEntry: () => null, getAsFileSystemHandle: async () => null, getAsFile: () => null },
        ],
      },
    });
    element.dispatchEvent(drop);
  });
  await expect(page.getByRole("heading", { name: "CSV 전체 신호 분석" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("분석할 CSV 파일").locator("option")).toHaveCount(3);
  await expect(page.getByRole("status")).toContainText("읽지 못한 항목 1건");
  const importedOptions = await page.getByLabel("분석할 CSV 파일").locator("option").evaluateAll((options) => (
    options.map((option) => (option as HTMLOptionElement).value)
  ));
  await page.getByLabel("분석할 CSV 파일").selectOption(importedOptions[1]);
  await page.getByRole("checkbox", { name: "right_arm_0" }).check();
  await page.getByRole("button", { name: /상태 비트/ }).click();
  await expect(page.locator(".stateDecoder")).toContainText("JAM");
  expect(remote).toEqual([]);
});

test("bootstrap secret is removed from the URL even when exchange fails", async ({ page, baseURL }) => {
  await page.route("**/api/session", (route) => route.abort());
  await page.goto(`${baseURL}/#bootstrap=must-not-remain`).catch(() => undefined);
  await expect.poll(() => page.evaluate(() => location.hash)).toBe("");
  await expect(page.getByText(/로컬 보안 세션 연결에 실패|Failed to fetch/)).toBeVisible();
});

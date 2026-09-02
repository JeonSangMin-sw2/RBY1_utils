import { useEffect, useMemo, useRef, useState } from "react";
import { LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { init, use as registerECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { ApiClient } from "./api";
import { csvSignalDisplayValue, csvSignalUnit } from "./csvSignalUnits";
import { groupJoints, sortJoints } from "./jointGroups";
import { RobotViewer, type RobotModelDescriptor } from "./RobotViewer";

type CsvSeriesMeta = { name: string; kind: "continuous" | "discrete" };
type LinkedIncident = {
  id: string;
  title: string;
  severity: string;
  fault_level?: "major" | "minor" | null;
  summary?: string;
  start_time?: number;
  start_raw?: string;
  log_time_display?: string;
  delta_seconds?: number;
  csv_sample_time?: number;
  csv_time_display?: string;
};
type CsvArtifact = {
  id: number;
  name: string;
  member?: string;
  min_sample_time?: number;
  max_sample_time?: number;
  sample_count: number;
  available_series: CsvSeriesMeta[];
  detected_joints: string[];
  robot_model?: RobotModelDescriptor;
  linked_incidents?: LinkedIncident[];
};
type CsvListPayload = { csvs: CsvArtifact[] };
type CsvSeries = { name: string; kind: "continuous" | "discrete"; nan_count: number; points: [number, number][] };
type MotorBitKind = "status" | "diagnostic" | "core_fault" | "reserved";
type MotorBit = {
  bit: number;
  value: number;
  name: string;
  label: string;
  kind: MotorBitKind;
  core_fault: boolean;
  reserved: boolean;
};
type MotorStateContract = {
  width_bits: number;
  core_fault_bits: number[];
  core_fault_names: string[];
  reserved_range: string;
  temperature_note: string;
  dynamixel_head_note: string;
};
type SystemStateType = "power" | "control_manager" | "control_state";
type SystemStateDefinition = { value: number; name: string; label: string };
type SystemStateContract = {
  series_types: Record<string, SystemStateType>;
  definitions: Record<SystemStateType, SystemStateDefinition[]>;
};
type CsvChartPayload = {
  start: number;
  end: number;
  available_series: string[];
  motor_state_bits: MotorBit[];
  motor_state_contract: MotorStateContract;
  system_state_contract: SystemStateContract;
  dense_series?: { name: string; required_points: number; suggested_window_seconds: number }[];
  series: CsvSeries[];
  linked_incidents?: LinkedIncident[];
};
type SignalCategory = "position" | "velocity" | "current" | "torque" | "temperature" | "state" | "gain" | "system";
type SemanticPoint = { rawValue: number; name: string; label: string };
type DisplaySeries = CsvSeries & { lane?: number; semanticPoints?: SemanticPoint[] };
type LaneChart = { series: DisplaySeries[]; labels: Map<number, string> };
export type ZoomRange = { start: number; end: number };

registerECharts([
  LineChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const CATEGORY_META: { key: SignalCategory; label: string; description: string }[] = [
  { key: "position", label: "위치", description: "현재 위치와 목표 위치" },
  { key: "velocity", label: "속도", description: "현재 속도와 목표 속도" },
  { key: "current", label: "전류", description: "모터 측정 전류" },
  { key: "torque", label: "토크", description: "측정 토크와 피드포워드 토크" },
  { key: "temperature", label: "온도", description: "모터 및 드라이브 측정 온도" },
  { key: "state", label: "상태 비트", description: "모터 상태 비트 해석" },
  { key: "gain", label: "피드백 게인", description: "목표 피드백 게인" },
  { key: "system", label: "전원·제어", description: "전원 및 Control Manager 상태" },
];

const SYSTEM_SERIES = [
  "power_5v",
  "power_12v",
  "power_24v",
  "power_48v",
  "control_manager_state",
  "control_state",
];

const SERIES_COLORS = ["#65c8b3", "#f0b85b", "#e66e73", "#6ea8df", "#d7dc82", "#b9a0d8"];
const UINT32_RANGE = 2 ** 32;

function normalizeModel(value?: RobotModelDescriptor): RobotModelDescriptor {
  const supportedVersion = value?.version === "v1.0" || value?.version === "v1.1" || value?.version === "v1.2" || value?.version === "v1.3"
    ? value.version
    : "v1.2";
  const confidence = value?.confidence === "conflict" ? "conflict" : (value?.confidence ?? "assumed");
  return {
    model: value?.model === "m" ? "m" : "a",
    version: supportedVersion,
    confidence,
    reason: value?.reason ?? "모델 정보가 없어 A Type V1.2로 가정",
  };
}

function interpolate(points: [number, number][], time: number): number {
  if (!points.length) return 0;
  if (time <= points[0][0]) return points[0][1];
  if (time >= points[points.length - 1][0]) return points[points.length - 1][1];
  let low = 0;
  let high = points.length - 1;
  while (low + 1 < high) {
    const middle = Math.floor((low + high) / 2);
    if (points[middle][0] <= time) low = middle;
    else high = middle;
  }
  const [leftTime, leftValue] = points[low];
  const [rightTime, rightValue] = points[high];
  if (rightTime <= leftTime) return leftValue;
  const ratio = (time - leftTime) / (rightTime - leftTime);
  return leftValue + (rightValue - leftValue) * ratio;
}

function motorStateMask(value: number): number {
  const integer = Math.trunc(value);
  return ((integer % UINT32_RANGE) + UINT32_RANGE) % UINT32_RANGE;
}

function hasBit(value: number, bit: number): boolean {
  return Math.floor(motorStateMask(value) / (2 ** bit)) % 2 === 1;
}

function motorBits(value: number, definitions: MotorBit[]): MotorBit[] {
  return definitions.filter((item) => hasBit(value, item.bit));
}

function systemSeriesLabel(name: string): string {
  const power = /^power_(5v|12v|24v|48v)$/.exec(name);
  if (power) return power[1].toUpperCase();
  if (name === "control_manager_state") return "Control Manager";
  if (name === "control_state") return "Control";
  return name;
}

function systemStateDefinition(
  seriesName: string,
  value: number,
  contract: SystemStateContract,
): SystemStateDefinition {
  const state = Math.trunc(value);
  const stateType = contract.series_types[seriesName];
  const definitions = stateType ? contract.definitions[stateType] : [];
  return definitions.find((item) => item.value === state) ?? {
    value: state,
    name: `Unknown(${state})`,
    label: `정의되지 않은 상태 ${state}`,
  };
}

const SERIES_SUFFIX_RE = /_(target_fb_gain|target_ff_tq|target_pos|target_vel|state|pos|vel|cur|tq|temperature|temp|motor_temp|drive_temp)$/;

function jointFromSeries(name: string): string | null {
  const match = name.match(SERIES_SUFFIX_RE);
  return match && match.index !== undefined ? name.slice(0, match.index) : null;
}

function categoryAvailable(category: SignalCategory, joints: string[], available: Set<string>): boolean {
  if (category === "system") {
    return SYSTEM_SERIES.some((name) => available.has(name));
  }
  return joints.some((joint) => namesFor(category, joint, available).length > 0);
}

function namesFor(category: SignalCategory, joint: string, available: Set<string>): string[] {
  switch (category) {
    case "position":
      return [`${joint}_pos`, `${joint}_target_pos`].filter((name) => available.has(name));
    case "velocity":
      return [`${joint}_vel`, `${joint}_target_vel`].filter((name) => available.has(name));
    case "current":
      return [`${joint}_cur`].filter((name) => available.has(name));
    case "torque":
      return [`${joint}_tq`, `${joint}_target_ff_tq`].filter((name) => available.has(name));
    case "temperature":
      return [
        `${joint}_temperature`,
        `${joint}_temp`,
        `${joint}_motor_temp`,
        `${joint}_drive_temp`,
      ].filter((name) => available.has(name));
    case "state":
      return [`${joint}_state`].filter((name) => available.has(name));
    case "gain":
      return [`${joint}_target_fb_gain`].filter((name) => available.has(name));
    case "system":
      return SYSTEM_SERIES.filter((name) => available.has(name));
  }
}

function eligibleJointsFor(category: SignalCategory, joints: string[], available: Set<string>): string[] {
  if (category === "system") return [];
  return joints.filter((joint) => namesFor(category, joint, available).length > 0);
}

function formatDuration(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return `${minutes}m ${remainder.toFixed(1)}s`;
}

function formatAxisTime(value: number, start: number): string {
  return `${(value - start).toFixed(1)}s`;
}

type SystemTooltipParam = { seriesName: string; value: [number, number]; marker: string };

function systemTooltip(
  params: SystemTooltipParam | SystemTooltipParam[],
  series: DisplaySeries[],
  start: number,
): string {
  const items = Array.isArray(params) ? params : [params];
  if (!items.length) return "";
  const time = items[0].value[0];
  const lines = [`시간: +${(time - start).toFixed(3)}s (${time.toFixed(3)}s)`];
  items.forEach((item) => {
    const target = series.find((candidate) => candidate.name === item.seriesName);
    const point = target?.semanticPoints?.find((_, index) => target.points[index]?.[0] === time)
      ?? target?.semanticPoints?.[target.points.findIndex(([candidateTime]) => candidateTime === time)];
    const detail = point ? `${point.rawValue} · ${point.name} (${point.label})` : String(item.value[1]);
    lines.push(`${item.marker}${item.seriesName}: <b>${detail}</b>`);
  });
  return lines.join("<br/>");
}

function signalTooltip(
  params: SystemTooltipParam | SystemTooltipParam[],
  start: number,
  unit?: string,
  incidents?: LinkedIncident[],
): string {
  const items = Array.isArray(params) ? params : [params];
  if (!items.length) return "";
  const time = items[0].value[0];
  const lines = [`시간: +${(time - start).toFixed(3)}s (${time.toFixed(3)}s)`];

  if (incidents && incidents.length > 0) {
    const matched = incidents.filter((inc) => {
      const incTime = inc.csv_sample_time ?? inc.start_time;
      return typeof incTime === "number" && Math.abs(incTime - time) <= 0.04;
    });
    matched.forEach((inc) => {
      lines.push(`<div style="margin:4px 0 2px;padding:3px 6px;background:rgba(220,38,38,0.35);border-left:3px solid #ef4444;border-radius:2px;color:#fca5a5;font-size:11px;">⚠️ <b>[장애 사건] ${inc.title}</b> (${inc.csv_time_display || inc.log_time_display})</div>`);
    });
  }

  items.forEach((item) => {
    const rawValue = item.value[1];
    const value = typeof rawValue === "number" ? rawValue.toFixed(3) : String(rawValue);
    lines.push(`${item.marker}${item.seriesName}: <b>${value}${unit ? ` ${unit}` : ""}</b>`);
  });
  return lines.join("<br/>");
}

function shortJointLabel(joint: string): string {
  return joint
    .replace(/^right_arm_(\d+)$/, "r_arm_$1")
    .replace(/^left_arm_(\d+)$/, "l_arm_$1")
    .replace(/^torso_(\d+)$/, "torso_$1")
    .replace(/^head_(\d+)$/, "head_$1")
    .replace(/^right_wheel$/, "r_wheel")
    .replace(/^left_wheel$/, "l_wheel");
}

function shortBitName(name: string): string {
  return name
    .replace(/^Motor Fault$/, "Fault")
    .replace(/^Diagnostic$/, "Diag")
    .replace(/^Core Fault$/, "CoreFlt")
    .replace(/^Over Temperature$/, "OverTemp")
    .replace(/^Over Current$/, "OverCur");
}

function stateLanes(series: CsvSeries[], allDefinitions: MotorBit[]): LaneChart {
  const entries = series.flatMap((item) => {
    const observed = new Set<number>();
    item.points.forEach(([, value]) => motorBits(value, allDefinitions).forEach((bit) => observed.add(bit.bit)));
    [0, 1, 2].forEach((bit) => observed.add(bit));
    return [...observed]
      .sort((a, b) => a - b)
      .map((bit) => allDefinitions.find((definition) => definition.bit === bit))
      .filter((definition): definition is MotorBit => Boolean(definition))
      .map((definition) => ({ source: item, definition }));
  });
  const labels = new Map<number, string>();
  return {
    labels,
    series: entries.map(({ source, definition }, index) => {
      const lane = entries.length - index;
      const joint = jointFromSeries(source.name) ?? source.name;
      const compactLabel = `${shortJointLabel(joint)}:${shortBitName(definition.name)}`;
      labels.set(lane, compactLabel);
      return {
        name: `${joint} · ${definition.name} · ${definition.label}`,
        kind: "discrete",
        nan_count: 0,
        lane,
        points: source.points.map(([time, value]) => [time, lane + (hasBit(value, definition.bit) ? 0.32 : -0.32)]),
      };
    }),
  };
}

function systemLanes(series: CsvSeries[], contract: SystemStateContract): LaneChart {
  const entries: { seriesName: string; value: number; definition: SystemStateDefinition }[] = [];
  series.forEach((item) => {
    const values = [...new Set(item.points.map(([, value]) => Math.trunc(value)))].sort((a, b) => a - b);
    values.forEach((value) => entries.push({
      seriesName: item.name,
      value,
      definition: systemStateDefinition(item.name, value, contract),
    }));
  });

  const labels = new Map<number, string>();
  const laneByState = new Map<string, number>();
  entries.forEach((entry, index) => {
    const lane = entries.length - index;
    laneByState.set(`${entry.seriesName}:${entry.value}`, lane);
    const compactSys = systemSeriesLabel(entry.seriesName);
    labels.set(lane, `${compactSys}:${entry.definition.name}`);
  });

  return {
    labels,
    series: series.map((item) => {
      const semanticPoints = item.points.map(([, value]) => {
        const definition = systemStateDefinition(item.name, value, contract);
        return { rawValue: Math.trunc(value), name: definition.name, label: definition.label };
      });
      return {
        ...item,
        name: systemSeriesLabel(item.name),
        semanticPoints,
        points: item.points.map(([time, value]) => [
          time,
          laneByState.get(`${item.name}:${Math.trunc(value)}`) ?? 0,
        ]),
      };
    }),
  };
}

function bitClass(bit: MotorBit): string {
  if (bit.core_fault) return "coreFaultBit";
  if (bit.kind === "diagnostic") return "diagnosticBit";
  if (bit.reserved) return "reservedBit";
  return "statusBit";
}

function stateEquation(activeBits: MotorBit[]): string {
  const integer = activeBits.reduce((total, item) => total + item.value, 0);
  const terms = activeBits.map((item) => `2^${item.bit}`).join(" + ");
  return `${terms} = ${integer} (0x${integer.toString(16).toUpperCase()})`;
}

function MotorBitReference({ definitions }: { definitions: MotorBit[] }) {
  return <details className="motorBitReference">
    <summary><span>전체 모터 상태 비트 정의표 (클릭하여 접기/펼치기)</span></summary>
    <div className="motorBitTableWrap">
      <table className="motorBitTable">
        <thead>
          <tr>
            <th>비트</th>
            <th>10진수</th>
            <th>16진수</th>
            <th>이름</th>
            <th>설명</th>
            <th>구분</th>
          </tr>
        </thead>
        <tbody>
          {definitions.map((item) => (
            <tr key={item.bit}>
              <td>{item.bit}</td>
              <td>{item.value}</td>
              <td>0x{item.value.toString(16).toUpperCase()}</td>
              <td><code>{item.name}</code></td>
              <td>{item.label}</td>
              <td><span className={bitClass(item)}>{item.core_fault ? "Core Fault" : item.kind}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </details>;
}

function StateSummary({
  category,
  series,
  definitions,
  contract,
  systemContract,
  activeGroupKey,
  onSelectGroupKey,
}: {
  category: SignalCategory;
  series: CsvSeries[];
  definitions: MotorBit[];
  contract?: MotorStateContract;
  systemContract?: SystemStateContract;
  activeGroupKey?: string;
  onSelectGroupKey?: (key: string) => void;
}) {
  const [isGuideOpen, setIsGuideOpen] = useState(false);
  const [internalGroupKey, setInternalGroupKey] = useState<string>("all");
  const selectedGroupKey = activeGroupKey ?? internalGroupKey;
  const setSelectedGroupKey = onSelectGroupKey ?? setInternalGroupKey;

  if (category !== "state" && category !== "system") return null;

  if (category === "system") {
    if (!systemContract) return null;
    const resolvedSystemContract = systemContract;
    return (
      <section className="stateDecoder" aria-label="전원 및 제어 상태 해석">
        <div className="stateDecoderHead">
          <h3>⚡ 전원 및 제어 상태 값 해석</h3>
          <p>그래프와 원본 값에 동일한 상태 정의를 적용합니다.</p>
        </div>
        <div className="systemStateGrid">
          {series.map((item) => {
            const values = [...new Set(item.points.map(([, value]) => Math.trunc(value)))];
            return (
              <div key={item.name}>
                <strong>{systemSeriesLabel(item.name)}</strong>
                <p>{values.map((value) => {
                  const state = systemStateDefinition(item.name, value, resolvedSystemContract);
                  return `${value} = ${state.name} (${state.label})`;
                }).join(" · ")}</p>
              </div>
            );
          })}
        </div>
      </section>
    );
  }

  if (!series.length) return null;

  const detectedJointNames = useMemo(() => {
    return series
      .map((raw) => jointFromSeries(raw.name) ?? raw.name)
      .filter((name): name is string => Boolean(name));
  }, [series]);

  const jointGroupList = useMemo(() => {
    return groupJoints(sortJoints([...new Set(detectedJointNames)]));
  }, [detectedJointNames]);

  const filteredSeries = useMemo(() => {
    if (selectedGroupKey === "all") return series;
    const targetGroup = jointGroupList.find((g) => g.key === selectedGroupKey);
    if (!targetGroup) return series;
    return series.filter((raw) => {
      const joint = jointFromSeries(raw.name) ?? raw.name;
      return targetGroup.joints.includes(joint);
    });
  }, [selectedGroupKey, series, jointGroupList]);

  const { totalFaultCount, totalDiagnosticCount } = useMemo(() => {
    let faults = 0;
    let diags = 0;
    series.forEach((raw) => {
      const values = [...new Set(raw.points.map(([, value]) => Math.trunc(value)))];
      values.forEach((val) => {
        const bits = motorBits(val, definitions);
        if (bits.some((b) => b.kind === "core_fault")) faults++;
        if (bits.some((b) => b.kind === "diagnostic")) diags++;
      });
    });
    return { totalFaultCount: faults, totalDiagnosticCount: diags };
  }, [series, definitions]);

  return (
    <section className="stateDecoder" aria-label="모터 상태 비트 해석">
      <div
        className={`stateDecoderAccordionHeader ${isGuideOpen ? "expanded" : "collapsed"}`}
        onClick={() => setIsGuideOpen((prev) => !prev)}
        role="button"
        tabIndex={0}
        aria-expanded={isGuideOpen}
      >
        <div className="stateDecoderTitleGroup">
          <span className="stateAccordionIcon">{isGuideOpen ? "▼" : "▶"}</span>
          <div className="stateDecoderTitles">
            <h3>모터 상태 비트 해석 (접기/펼치기)</h3>
            <p>조인트별 상태 정수값을 RBMotor 비트 정의와 매칭하여 실시간 분석합니다.</p>
          </div>
        </div>
        <div className="stateDecoderBadgeGroup">
          {totalFaultCount > 0 ? (
            <span className="stateBadge error">⚠️ Fault 비트 감지 ({totalFaultCount}건)</span>
          ) : totalDiagnosticCount > 0 ? (
            <span className="stateBadge warning">⚡ 주의 비트 감지 ({totalDiagnosticCount}건)</span>
          ) : (
            <span className="stateBadge ok">✓ 정상 상태</span>
          )}
          <span className="stateToggleBtnLabel">{isGuideOpen ? "접기 ▲" : "펼치기 ▼"}</span>
        </div>
      </div>

      {isGuideOpen && (
        <div className="stateDecoderContent">
          <div className="stateComponentTabs" role="tablist" aria-label="조인트 부위별 필터">
            <span className="stateTabLabel">부위별 보기:</span>
            <button
              type="button"
              className={`stateTabBtn ${selectedGroupKey === "all" ? "active" : ""}`}
              onClick={() => setSelectedGroupKey("all")}
            >
              전체 ({series.length})
            </button>
            {jointGroupList.map((group) => {
              const count = group.joints.filter((j) => detectedJointNames.includes(j)).length;
              if (count === 0) return null;
              return (
                <button
                  type="button"
                  key={group.key}
                  className={`stateTabBtn ${selectedGroupKey === group.key ? "active" : ""}`}
                  onClick={() => setSelectedGroupKey(group.key)}
                >
                  {group.label} ({count})
                </button>
              );
            })}
          </div>

          <div className="stateJointsGrid">
            {filteredSeries.map((raw) => {
              const jointName = jointFromSeries(raw.name) ?? raw.name;
              const values = [...new Set(raw.points.map(([, value]) => Math.trunc(value)))];
              return (
                <section className="stateJointGroupCard" key={raw.name}>
                  <div className="stateJointCardHead">
                    <h4>{jointName}</h4>
                    <span className="stateRawCount">{values.length}개 상태값 발생</span>
                  </div>
                  <div className="stateValueList">
                    {values.map((value) => {
                      const bits = motorBits(value, definitions);
                      const hasCoreFault = bits.some((item) => item.kind === "core_fault");
                      const hasDiagnostic = bits.some((item) => item.kind === "diagnostic");
                      const valueClass = hasCoreFault ? "hasCoreFault" : hasDiagnostic ? "hasDiagnostic" : "";
                      return (
                        <div className={`stateValueItem ${valueClass}`} key={`${raw.name}:${value}`}>
                          <div className="stateValueHeader">
                            <code>{value} · 0x{motorStateMask(value).toString(16).toUpperCase()}</code>
                            {hasCoreFault && <span className="tagCoreFault">Core Fault</span>}
                          </div>
                          <div className="stateBitsList">
                            {bits.length ? (
                              bits.map((item) => (
                                <span className={bitClass(item)} key={item.bit} title={`bit ${item.bit} · 값 ${item.value}`}>
                                  {item.name}<small>{item.label}</small>
                                </span>
                              ))
                            ) : (
                              <span className="normalBit">활성 비트 없음 (정상)</span>
                            )}
                          </div>
                          {bits.length > 0 && <p className="stateEquation">{stateEquation(bits)}</p>}
                        </div>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>

          {contract && (
            <div className="motorStateGuide">
              <div>
                <strong>Core Motor Fault 판정 대상</strong>
                <span>{contract.core_fault_names.join(" · ")} (비트 {contract.core_fault_bits.join(", ")})</span>
              </div>
              <div>
                <strong>기타 상태 비트</strong>
                <span>CSV에는 기록되지만 모두 Core의 Motor Fault 판정 조건에 포함되는 것은 아닙니다.</span>
              </div>
              <ul>
                <li>{contract.temperature_note}</li>
                <li>{contract.dynamixel_head_note}</li>
                <li>비트 {contract.reserved_range}은 예약 영역입니다.</li>
              </ul>
            </div>
          )}
          <MotorBitReference definitions={definitions} />
        </div>
      )}
    </section>
  );
}

function CsvPlot({
  category,
  onCategoryChange,
  availableCategories,
  selectedNames,
  payload,
  loading,
  error,
  kind,
  comparisonIndex,
  onRemove,
  zoomRange,
  onZoomRangeChange,
  incidentMarks = [],
  selectedIncidentId,
  cursorTime,
  onCursorChange,
}: {
  category: SignalCategory;
  onCategoryChange: (newCategory: SignalCategory) => void;
  availableCategories: { key: SignalCategory; label: string; description: string }[];
  selectedNames: string[];
  payload: CsvChartPayload | null;
  loading: boolean;
  error: string;
  kind: "primary" | "comparison";
  comparisonIndex?: number;
  onRemove?: () => void;
  zoomRange: ZoomRange;
  onZoomRangeChange: (range: ZoomRange) => void;
  incidentMarks?: LinkedIncident[];
  selectedIncidentId?: string | null;
  cursorTime?: number;
  onCursorChange?: (time: number) => void;
}) {
  const chartNode = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof init> | null>(null);
  const zoomCallbackRef = useRef(onZoomRangeChange);
  const cursorCallbackRef = useRef(onCursorChange);

  const series = useMemo(() => {
    if (!payload) return [];
    const selected = new Set(selectedNames);
    return payload.series.filter((item) => selected.has(item.name));
  }, [payload, selectedNames]);

  const detectedJointNames = useMemo(() => {
    return series
      .map((raw) => jointFromSeries(raw.name) ?? raw.name)
      .filter((name): name is string => Boolean(name));
  }, [series]);

  const jointGroupList = useMemo(() => {
    return groupJoints(sortJoints([...new Set(detectedJointNames)]));
  }, [detectedJointNames]);

  // State groups: Remove 'all', only keep concrete joint groups
  const stateGroups = useMemo(() => {
    if (category !== "state" || jointGroupList.length === 0) return [];
    return jointGroupList.map((g) => ({
      key: g.key,
      label: `${g.label} (${g.joints.filter((j) => detectedJointNames.includes(j)).length})`,
    }));
  }, [category, jointGroupList, detectedJointNames]);

  const [activeStateGroup, setActiveStateGroup] = useState<string>(() => jointGroupList[0]?.key ?? "head");

  useEffect(() => {
    if (stateGroups.length > 0 && !stateGroups.some((g) => g.key === activeStateGroup)) {
      setActiveStateGroup(stateGroups[0].key);
    }
  }, [stateGroups, activeStateGroup]);

  const stateFilteredSeries = useMemo(() => {
    if (category !== "state") return series;
    const targetGroup = jointGroupList.find((g) => g.key === activeStateGroup) ?? jointGroupList[0];
    if (!targetGroup) return series;
    return series.filter((raw) => {
      const joint = jointFromSeries(raw.name) ?? raw.name;
      return targetGroup.joints.includes(joint);
    });
  }, [category, activeStateGroup, series, jointGroupList]);

  const lanes = useMemo(() => {
    if (!payload) return null;
    if (category === "state" && stateFilteredSeries.length) {
      return stateLanes(stateFilteredSeries, payload.motor_state_bits);
    }
    if (category === "system" && series.length) {
      return systemLanes(series, payload.system_state_contract);
    }
    return null;
  }, [category, payload, stateFilteredSeries, series]);
  const signalUnit = csvSignalUnit(category);
  const displayed = useMemo(() => {
    if (lanes) return lanes.series;
    return series.map((item) => ({
      ...item,
      points: item.points.map(([time, value]): [number, number] => [
        time,
        csvSignalDisplayValue(category, value),
      ]),
    }));
  }, [category, lanes, series]);
  const categoryLabel = CATEGORY_META.find((item) => item.key === category)?.label ?? "신호";

  const isDispatchingZoomRef = useRef(false);
  const isInteractingRef = useRef(false);

  useEffect(() => {
    zoomCallbackRef.current = onZoomRangeChange;
  }, [onZoomRangeChange]);

  useEffect(() => {
    cursorCallbackRef.current = onCursorChange;
  }, [onCursorChange]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (isInteractingRef.current) return;
    isDispatchingZoomRef.current = true;
    chart.dispatchAction({
      type: "dataZoom",
      dataZoomIndex: 0,
      start: zoomRange.start,
      end: zoomRange.end,
    });
    isDispatchingZoomRef.current = false;
  }, [zoomRange.start, zoomRange.end]);

  useEffect(() => {
    if (!chartNode.current) return;
    const chart = init(chartNode.current);
    chartRef.current = chart;
    const handleZoom = (event: unknown) => {
      if (isDispatchingZoomRef.current) return;
      const value = event as { start?: number; end?: number; batch?: { start?: number; end?: number }[] };
      const range = value.batch?.[0] ?? value;
      if (typeof range.start !== "number" || typeof range.end !== "number") return;
      zoomCallbackRef.current({ start: range.start, end: range.end });
    };
    chart.on("datazoom", handleZoom);

    let isDragging = false;
    let downPos = { x: 0, y: 0 };
    const handleMouseDown = (e: { offsetX: number; offsetY: number }) => {
      isInteractingRef.current = true;
      isDragging = false;
      downPos = { x: e.offsetX, y: e.offsetY };
    };
    const handleMouseMove = (e: { offsetX: number; offsetY: number }) => {
      if (Math.abs(e.offsetX - downPos.x) > 4 || Math.abs(e.offsetY - downPos.y) > 4) {
        isDragging = true;
      }
    };
    const handleMouseUp = (event: { offsetX: number; offsetY: number }) => {
      setTimeout(() => {
        isInteractingRef.current = false;
      }, 50);
      if (isDragging) return;
      const pointInPixel = [event.offsetX, event.offsetY];
      if (chart.containPixel("grid", pointInPixel)) {
        const pointInGrid = chart.convertFromPixel({ seriesIndex: 0 }, pointInPixel);
        if (pointInGrid && typeof pointInGrid[0] === "number") {
          cursorCallbackRef.current?.(pointInGrid[0]);
        }
      }
    };
    const handleGlobalMouseUp = () => {
      setTimeout(() => {
        isInteractingRef.current = false;
      }, 50);
    };

    chart.getZr().on("mousedown", handleMouseDown);
    chart.getZr().on("mousemove", handleMouseMove);
    chart.getZr().on("mouseup", handleMouseUp);
    window.addEventListener("mouseup", handleGlobalMouseUp);

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(chartNode.current);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", resize);
      window.removeEventListener("mouseup", handleGlobalMouseUp);
      chart.getZr().off("mousedown", handleMouseDown);
      chart.getZr().off("mousemove", handleMouseMove);
      chart.getZr().off("mouseup", handleMouseUp);
      chart.off("datazoom", handleZoom);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // Calculate visible window and offscreen incidents
  const totalDuration = payload ? Math.max(0.001, payload.end - payload.start) : 1;
  const visibleStart = payload ? payload.start + (totalDuration * zoomRange.start) / 100 : 0;
  const visibleEnd = payload ? payload.start + (totalDuration * zoomRange.end) / 100 : 1;

  const validIncidents = useMemo(() => {
    if (!payload) return [];
    return incidentMarks
      .map((inc) => {
        const t = inc.csv_sample_time ?? (typeof inc.start_time === "number" ? inc.start_time : undefined);
        return {
          ...inc,
          validTime: t,
        };
      })
      .filter((inc): inc is typeof inc & { validTime: number } => typeof inc.validTime === "number" && inc.validTime >= payload.start && inc.validTime <= payload.end);
  }, [incidentMarks, payload]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (!payload || !displayed.length) {
      chart.clear();
      return;
    }
    const laneMode = Boolean(lanes);
    const laneCount = lanes?.labels.size ?? 0;

    const allIncidentMarks = validIncidents.flatMap((inc) => {
      const isSelected = inc.id === selectedIncidentId;
      return [{
        name: inc.title,
        xAxis: inc.validTime,
        lineStyle: {
          color: isSelected ? "#ff3366" : "rgba(255, 153, 0, 0.85)",
          width: isSelected ? 2.5 : 1.5,
          type: isSelected ? ("solid" as const) : ("dashed" as const),
        },
        label: {
          show: isSelected,
          position: "insideEndTop" as const,
          formatter: `⚠️ ${inc.title}`,
          color: "#ffffff",
          fontSize: 11,
          fontWeight: isSelected ? ("bold" as const) : ("normal" as const),
          backgroundColor: isSelected ? "rgba(220, 20, 60, 0.95)" : "rgba(180, 83, 9, 0.88)",
          padding: [3, 7],
          borderRadius: 3,
          borderColor: isSelected ? "#ff3366" : "#ff9900",
          borderWidth: 1,
        },
      }];
    });

    const playbackMark = typeof cursorTime === "number" && cursorTime >= payload.start && cursorTime <= payload.end ? [{
      name: "재생 위치",
      xAxis: cursorTime,
      lineStyle: { color: "#f4c15d", width: 2, type: "solid" as const },
      label: { show: false },
    }] : [];

    const markLineData = [
      ...playbackMark,
      ...allIncidentMarks,
    ];

    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      color: SERIES_COLORS,
      legend: {
        type: "scroll",
        textStyle: { color: "#c8d0d5", fontSize: 11 },
        top: 2,
        left: 0,
        right: 0,
        height: 20,
        show: category !== "state" || laneCount <= 8,
      },
      grid: {
        left: 68,
        right: 18,
        top: 28,
        bottom: 24,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line" },
        formatter: category === "system"
          ? (params: SystemTooltipParam | SystemTooltipParam[]) => systemTooltip(params, displayed, payload.start)
          : (params: SystemTooltipParam | SystemTooltipParam[]) => signalTooltip(params, payload.start, signalUnit?.symbol, validIncidents),
      },
      dataZoom: [
        {
          type: "inside",
          filterMode: "none",
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
          start: zoomRange.start,
          end: zoomRange.end,
        },
      ],
      xAxis: {
        type: "value",
        min: payload.start,
        max: payload.end,
        axisLabel: { color: "#aab4bc", fontSize: 11, formatter: (value: number) => formatAxisTime(value, payload.start) },
        splitLine: { lineStyle: { color: "#252b31" } },
      },
      yAxis: laneMode && lanes ? {
        type: "value",
        min: 0.5,
        max: laneCount + 0.5,
        interval: 1,
        axisLabel: {
          color: "#c1c9ce",
          fontSize: 10,
          width: 60,
          overflow: "truncate",
          ellipsis: "...",
          formatter: (value: number) => lanes.labels.get(Math.round(value)) ?? "",
        },
        splitLine: { lineStyle: { color: "#252b31" } },
      } : {
        type: "value",
        scale: true,
        name: signalUnit?.axisLabel,
        nameLocation: "middle",
        nameGap: 46,
        nameTextStyle: { color: "#c9d1d6", fontSize: 11, fontWeight: 700 },
        axisLabel: { color: "#aab4bc", fontSize: 11 },
        splitLine: { lineStyle: { color: "#252b31" } },
      },
      series: displayed.map((item, index) => ({
        name: item.name,
        type: "line",
        step: item.kind === "discrete" ? "end" : false,
        showSymbol: false,
        sampling: false,
        lineStyle: laneMode ? { width: 2 } : undefined,
        data: item.points,
        markLine: index === 0 && markLineData.length > 0 ? {
          silent: false,
          symbol: ["none", "none"],
          lineStyle: {
            color: "#ff7a00",
            width: 2,
            type: "solid",
          },
          data: markLineData,
        } : index === 0 ? {
          silent: true,
          symbol: ["none", "none"],
          label: { formatter: `CSV 종료 (${(payload.end - payload.start).toFixed(3)}s)`, color: "#f0b85b", fontSize: 11 },
          lineStyle: { color: "#f0b85b", width: 1.2, type: "dashed" },
          data: [{ xAxis: payload.end }],
        } : undefined,
      })),
    }, { notMerge: true, lazyUpdate: false });
  }, [category, cursorTime, displayed, incidentMarks, lanes, payload, selectedIncidentId, signalUnit, validIncidents]);

  const handlePrevStateGroup = () => {
    if (!stateGroups.length) return;
    const currentIndex = stateGroups.findIndex((g) => g.key === activeStateGroup);
    const nextIndex = (currentIndex - 1 + stateGroups.length) % stateGroups.length;
    setActiveStateGroup(stateGroups[nextIndex].key);
  };

  const handleNextStateGroup = () => {
    if (!stateGroups.length) return;
    const currentIndex = stateGroups.findIndex((g) => g.key === activeStateGroup);
    const nextIndex = (currentIndex + 1) % stateGroups.length;
    setActiveStateGroup(stateGroups[nextIndex].key);
  };

  return <section className={`csvPlotCard csvPlot-${kind === "primary" ? "primary" : "secondary"}`} aria-label={categoryLabel}>
    {/* 1. Left: Embedded Signal Category Selection Nav */}
    <aside className="csvPlotMiniCategoryNav" aria-label="신호 분류 선택">
      <div className="miniNavHeader">신호 분류</div>
      <div className="miniNavBtnList">
        {availableCategories.map((item) => {
          const isActive = item.key === category;
          return (
            <button
              type="button"
              key={item.key}
              className={`miniNavBtn ${isActive ? "active" : ""}`}
              onClick={() => onCategoryChange(item.key)}
              title={item.description}
            >
              <strong>{item.label}</strong>
            </button>
          );
        })}
      </div>
    </aside>

    {/* 2. Right: Plot Header & ECharts Timeline Canvas */}
    <div className="csvPlotMain">
      <div className="csvChartHeader">
        <div className="csvChartHeaderTitleWrap">
          <h3>
            {categoryLabel}
            <span>{category === "state" ? `${stateFilteredSeries.length}개 신호` : `${selectedNames.length}개 신호`}</span>
            {kind === "comparison" && <b className="plotPriority">비교 {comparisonIndex ?? 1}</b>}
          </h3>
          {category === "state" && stateGroups.length > 1 && (
            <div className="statePlotGroupSwitcher" role="toolbar" aria-label="상태비트 컴포넌트 부위 전환">
              <button
                type="button"
                className="stateGroupNavBtn"
                onClick={handlePrevStateGroup}
                title="이전 부위로 넘기기 (◀)"
                aria-label="이전 부위"
              >
                ◀
              </button>
              <div className="stateGroupTabList">
                {stateGroups.map((g) => (
                  <button
                    type="button"
                    key={g.key}
                    className={`stateGroupTabBtn ${activeStateGroup === g.key ? "active" : ""}`}
                    onClick={() => setActiveStateGroup(g.key)}
                  >
                    {g.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="stateGroupNavBtn"
                onClick={handleNextStateGroup}
                title="다음 부위로 넘기기 (▶)"
                aria-label="다음 부위"
              >
                ▶
              </button>
            </div>
          )}
        </div>
        {onRemove && <button type="button" className="textButton danger smallBtn" aria-label={`비교 Plot 삭제: ${categoryLabel}`} onClick={onRemove}>✕ Plot 삭제</button>}
      </div>

      <div className="csvTimelineContainer">
        <div
          className="csvTimeline"
          data-zoom-start={zoomRange.start.toFixed(3)}
          data-zoom-end={zoomRange.end.toFixed(3)}
          data-y-unit={signalUnit?.symbol ?? ""}
          data-y-scale={signalUnit?.scale ?? 1}
          role="img"
          aria-label={`${kind === "comparison" ? "비교 " : ""}CSV ${categoryLabel} 그래프${signalUnit ? `, Y축 ${signalUnit.axisLabel}` : ""}: ${
            lanes ? [...lanes.labels.values()].join(", ") : (category === "state" ? stateFilteredSeries.map((s) => s.name) : selectedNames).join(", ")
          }`}
        >
          {loading && <div className="chartLoading">CSV 신호를 불러오는 중입니다.</div>}
          {error && <div className="chartLoading errorText">{error}</div>}
          {!loading && !error && !displayed.length && <div className="chartLoading">선택한 항목에 표시할 샘플이 없습니다.</div>}
          <div className={!loading && !error && displayed.length ? "csvChart" : "csvChart isHidden"} ref={chartNode} />
        </div>
      </div>

      {/* Embedded State & System Interpretation inside this plot */}
      {(category === "state" || category === "system") && (
        <div className="csvPlotEmbeddedStateSection">
          <StateSummary
            category={category}
            series={category === "state"
              ? (payload?.series.filter((s) => s.name.endsWith("_state")) ?? [])
              : (payload?.series.filter((s) => s.name === "control_manager_state" || s.name === "control_state" || s.name.startsWith("power_")) ?? [])}
            definitions={payload?.motor_state_bits ?? []}
            contract={payload?.motor_state_contract}
            systemContract={payload?.system_state_contract}
            activeGroupKey={category === "state" ? activeStateGroup : undefined}
            onSelectGroupKey={category === "state" ? setActiveStateGroup : undefined}
          />
        </div>
      )}
    </div>
  </section>;
}

export type IncidentSummary = {
  id: string;
  title: string;
  severity: string;
  start_time?: number;
  end_time?: number;
  start_raw?: string;
  end_raw?: string;
  meaning?: string;
  affected_joints?: string[];
  fault_level?: "major" | "minor" | null;
};

export function UnifiedTimelineBrushBar({
  durationSec,
  zoomRange,
  onZoomChange,
}: {
  durationSec: number;
  zoomRange: ZoomRange;
  onZoomChange: (range: ZoomRange) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef<"center" | "left" | "right" | null>(null);
  const dragStartRef = useRef<{ startX: number; initStart: number; initEnd: number }>({
    startX: 0,
    initStart: 0,
    initEnd: 100,
  });

  const currentStartSec = (durationSec * zoomRange.start) / 100;
  const currentEndSec = (durationSec * zoomRange.end) / 100;

  const [startInput, setStartInput] = useState(() => currentStartSec.toFixed(3));
  const [endInput, setEndInput] = useState(() => currentEndSec.toFixed(3));

  useEffect(() => {
    setStartInput(((durationSec * zoomRange.start) / 100).toFixed(3));
    setEndInput(((durationSec * zoomRange.end) / 100).toFixed(3));
  }, [durationSec, zoomRange.start, zoomRange.end]);

  function handleStartInputCommit() {
    let val = parseFloat(startInput);
    if (isNaN(val)) {
      setStartInput(((durationSec * zoomRange.start) / 100).toFixed(3));
      return;
    }
    const curEndSec = (durationSec * zoomRange.end) / 100;
    val = Math.max(0, Math.min(val, curEndSec - 0.01));
    const newStartPct = (val / durationSec) * 100;
    onZoomChange({ start: Math.max(0, newStartPct), end: zoomRange.end });
  }

  function handleEndInputCommit() {
    let val = parseFloat(endInput);
    if (isNaN(val)) {
      setEndInput(((durationSec * zoomRange.end) / 100).toFixed(3));
      return;
    }
    const curStartSec = (durationSec * zoomRange.start) / 100;
    val = Math.min(durationSec, Math.max(val, curStartSec + 0.01));
    const newEndPct = (val / durationSec) * 100;
    onZoomChange({ start: zoomRange.start, end: Math.min(100, newEndPct) });
  }

  function handleStepStart(delta: number) {
    const curStart = (durationSec * zoomRange.start) / 100;
    const curEnd = (durationSec * zoomRange.end) / 100;
    const nextVal = Math.max(0, Math.min(parseFloat((curStart + delta).toFixed(2)), curEnd - 0.05));
    const newStartPct = (nextVal / durationSec) * 100;
    onZoomChange({ start: Math.max(0, newStartPct), end: zoomRange.end });
  }

  function handleStepEnd(delta: number) {
    const curStart = (durationSec * zoomRange.start) / 100;
    const curEnd = (durationSec * zoomRange.end) / 100;
    const nextVal = Math.min(durationSec, Math.max(parseFloat((curEnd + delta).toFixed(2)), curStart + 0.05));
    const newEndPct = (nextVal / durationSec) * 100;
    onZoomChange({ start: zoomRange.start, end: Math.min(100, newEndPct) });
  }

  const hasMovedRef = useRef<boolean>(false);

  function startDrag(type: "center" | "left" | "right", e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    isDraggingRef.current = type;
    hasMovedRef.current = false;
    const startX = e.clientX;
    const initStart = zoomRange.start;
    const initEnd = zoomRange.end;

    function onMouseMove(moveEvent: MouseEvent) {
      if (!isDraggingRef.current || !trackRef.current) return;
      const rect = trackRef.current.getBoundingClientRect();
      const trackWidth = rect.width || 1;
      const deltaX = moveEvent.clientX - startX;
      if (Math.abs(deltaX) > 2) {
        hasMovedRef.current = true;
      }
      const deltaPct = (deltaX / trackWidth) * 100;

      if (isDraggingRef.current === "center") {
        const span = initEnd - initStart;
        let newStart = initStart + deltaPct;
        if (newStart < 0) newStart = 0;
        if (newStart + span > 100) newStart = 100 - span;
        onZoomChange({ start: Math.max(0, newStart), end: Math.min(100, newStart + span) });
      } else if (isDraggingRef.current === "left") {
        let newStart = initStart + deltaPct;
        if (newStart < 0) newStart = 0;
        if (newStart > initEnd - 0.2) newStart = initEnd - 0.2;
        onZoomChange({ start: newStart, end: initEnd });
      } else if (isDraggingRef.current === "right") {
        let newEnd = initEnd + deltaPct;
        if (newEnd > 100) newEnd = 100;
        if (newEnd < initStart + 0.2) newEnd = initStart + 0.2;
        onZoomChange({ start: initStart, end: newEnd });
      }
    }

    function onMouseUp() {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      setTimeout(() => {
        isDraggingRef.current = null;
        hasMovedRef.current = false;
      }, 60);
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  }

  function handleTrackClick(e: React.MouseEvent<HTMLDivElement>) {
    if (isDraggingRef.current || hasMovedRef.current) return;
    if (!trackRef.current) return;
    const rect = trackRef.current.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickPct = (clickX / rect.width) * 100;
    if (clickPct < zoomRange.start) {
      onZoomChange({ start: Math.max(0, clickPct), end: zoomRange.end });
    } else if (clickPct > zoomRange.end) {
      onZoomChange({ start: zoomRange.start, end: Math.min(100, clickPct) });
    }
  }

  const windowLeft = Math.max(0, Math.min(100, zoomRange.start));
  const windowWidth = Math.max(0.5, Math.min(100 - windowLeft, zoomRange.end - zoomRange.start));

  return (
    <div className="csvUnifiedZoomBar" role="toolbar" aria-label="통합 타임라인 줌 제어">
      <div className="timelineBrushWrap">
        <div
          className="timelineBrushTrack"
          ref={trackRef}
          onClick={handleTrackClick}
          title="클릭하여 해당 위치로 줌 윈도우 이동 / 드래그하여 구간 및 핸들 조절"
        >
          <div className="timelineBrushGridLines">
            <span style={{ left: "25%" }} />
            <span style={{ left: "50%" }} />
            <span style={{ left: "75%" }} />
          </div>

          <div
            className="timelineBrushWindow"
            style={{
              left: `${windowLeft}%`,
              width: `${windowWidth}%`,
            }}
            onMouseDown={(e) => startDrag("center", e)}
            onClick={(e) => e.stopPropagation()}
            title="드래그하여 선택된 시간 구간 이동 (Pan)"
          >
            <div
              className="timelineBrushHandle handleLeft"
              onMouseDown={(e) => startDrag("left", e)}
              onClick={(e) => e.stopPropagation()}
              title="드래그하여 시작 시점 조절"
            >
              <div className="handleGrip" />
            </div>

            <div className="brushWindowLabel">
              {currentStartSec.toFixed(2)}s ~ {currentEndSec.toFixed(2)}s
            </div>

            <div
              className="timelineBrushHandle handleRight"
              onMouseDown={(e) => startDrag("right", e)}
              onClick={(e) => e.stopPropagation()}
              title="드래그하여 종료 시점 조절"
            >
              <div className="handleGrip" />
            </div>
          </div>
        </div>
      </div>

      <div className="zoomDirectTimeInputs">
        <label className="timeInputItem">
          <span>시작</span>
          <div className="inputWithUnit">
            <input
              type="number"
              step="0.1"
              min={0}
              max={durationSec}
              value={startInput}
              onChange={(e) => setStartInput(e.target.value)}
              onBlur={handleStartInputCommit}
              onKeyDown={(e) => { if (e.key === "Enter") handleStartInputCommit(); }}
              title="시작 시간 (초 단위 입력 / 0.1s 증감 버튼 지원)"
            />
            <div className="timeStepperBtns">
              <button
                type="button"
                className="btnStepper"
                onClick={() => handleStepStart(+0.1)}
                title="+0.1초 증가"
              >
                ▲
              </button>
              <button
                type="button"
                className="btnStepper"
                onClick={() => handleStepStart(-0.1)}
                title="-0.1초 감소"
              >
                ▼
              </button>
            </div>
            <span className="unitTag">s</span>
          </div>
        </label>
        <span className="timeInputSep">~</span>
        <label className="timeInputItem">
          <span>종료</span>
          <div className="inputWithUnit">
            <input
              type="number"
              step="0.1"
              min={0}
              max={durationSec}
              value={endInput}
              onChange={(e) => setEndInput(e.target.value)}
              onBlur={handleEndInputCommit}
              onKeyDown={(e) => { if (e.key === "Enter") handleEndInputCommit(); }}
              title="종료 시간 (초 단위 입력 / 0.1s 증감 버튼 지원)"
            />
            <div className="timeStepperBtns">
              <button
                type="button"
                className="btnStepper"
                onClick={() => handleStepEnd(+0.1)}
                title="+0.1초 증가"
              >
                ▲
              </button>
              <button
                type="button"
                className="btnStepper"
                onClick={() => handleStepEnd(-0.1)}
                title="-0.1초 감소"
              >
                ▼
              </button>
            </div>
            <span className="unitTag">s</span>
          </div>
        </label>
      </div>

      <div className="zoomPresetGroup">
        <button
          type="button"
          className={`btnZoomPreset ${Math.abs(zoomRange.end - zoomRange.start - 100) < 1 ? "active" : ""}`}
          onClick={() => onZoomChange({ start: 0, end: 100 })}
        >
          전체 (100%)
        </button>
        <button
          type="button"
          className={`btnZoomPreset ${Math.abs(zoomRange.end - zoomRange.start - 50) < 1 ? "active" : ""}`}
          onClick={() => {
            const center = (zoomRange.start + zoomRange.end) / 2;
            const newStart = Math.max(0, Math.min(50, center - 25));
            onZoomChange({ start: newStart, end: newStart + 50 });
          }}
        >
          50%
        </button>
        <button
          type="button"
          className={`btnZoomPreset ${Math.abs(zoomRange.end - zoomRange.start - 25) < 1 ? "active" : ""}`}
          onClick={() => {
            const center = (zoomRange.start + zoomRange.end) / 2;
            const newStart = Math.max(0, Math.min(75, center - 12.5));
            onZoomChange({ start: newStart, end: newStart + 25 });
          }}
        >
          25%
        </button>
        <button
          type="button"
          className={`btnZoomPreset ${Math.abs(zoomRange.end - zoomRange.start - 10) < 1 ? "active" : ""}`}
          onClick={() => {
            const center = (zoomRange.start + zoomRange.end) / 2;
            const newStart = Math.max(0, Math.min(90, center - 5));
            onZoomChange({ start: newStart, end: newStart + 10 });
          }}
        >
          10%
        </button>
        {Math.abs(zoomRange.end - zoomRange.start - 100) >= 1 && (
          <button
            type="button"
            className="btnZoomReset"
            onClick={() => onZoomChange({ start: 0, end: 100 })}
            title="기본 100% 배율로 초기화"
          >
            🔄 리셋
          </button>
        )}
      </div>
    </div>
  );
}

export function CsvAnalysis({
  client,
  caseId,
  incidents,
  selectedArtifactId,
  onSelectArtifactId,
}: {
  client: ApiClient;
  caseId: string;
  incidents?: IncidentSummary[];
  selectedArtifactId?: number | null;
  onSelectArtifactId?: (id: number) => void;
}) {
  const [listResult, setListResult] = useState<{ caseId: string; csvs: CsvArtifact[]; error?: string } | null>(null);
  const [artifactId, setArtifactId] = useState(selectedArtifactId ?? 0);
  const [plotCategories, setPlotCategories] = useState<SignalCategory[]>(["position"]);
  const [selectedJointNames, setSelectedJointNames] = useState<string[] | null>(() => {
    try {
      const raw = window.sessionStorage.getItem("rby1_selected_joints_global");
      if (raw) return JSON.parse(raw);
    } catch {}
    return null;
  });
  const [zoomRange, setZoomRange] = useState<ZoomRange>({ start: 0, end: 100 });
  const [artifactPayloads, setArtifactPayloads] = useState<Record<string, CsvChartPayload>>({});
  const [fetchingKey, setFetchingKey] = useState<string | null>(null);
  const [artifactFetchError, setArtifactFetchError] = useState<string>("");

  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);

  const [show3DView, setShow3DView] = useState(true);
  const [cursorTime, setCursorTime] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const cursorRef = useRef(cursorTime);

  const [simWidth, setSimWidth] = useState<number>(() => {
    try {
      const saved = window.sessionStorage.getItem("rby1_sim_width");
      if (saved) return Math.max(320, Math.min(800, Number(saved)));
    } catch {}
    return 460;
  });
  const [isDraggingResizer, setIsDraggingResizer] = useState(false);
  const dragStartXRef = useRef(0);
  const dragStartWidthRef = useRef(simWidth);

  useEffect(() => {
    if (!isDraggingResizer) return;
    const handleMouseMove = (e: MouseEvent) => {
      const delta = dragStartXRef.current - e.clientX;
      const minW = 300;
      const maxW = Math.max(360, window.innerWidth - 420);
      const newWidth = Math.max(minW, Math.min(maxW, dragStartWidthRef.current + delta));
      setSimWidth(newWidth);
      try {
        window.sessionStorage.setItem("rby1_sim_width", String(newWidth));
      } catch {}
    };
    const handleMouseUp = () => {
      setIsDraggingResizer(false);
    };
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDraggingResizer]);

  const startResizerDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    dragStartXRef.current = e.clientX;
    dragStartWidthRef.current = simWidth;
    setIsDraggingResizer(true);
  };

  useEffect(() => {
    cursorRef.current = cursorTime;
  }, [cursorTime]);

  useEffect(() => {
    setArtifactPayloads({});
    setFetchingKey(null);
    setArtifactFetchError("");
  }, [caseId]);

  useEffect(() => {
    let active = true;
    client.json<CsvListPayload>(`/api/v3/cases/${caseId}/csvs`)
      .then((result) => {
        if (!active) return;
        setListResult({ caseId, csvs: result.csvs });
        const targetId = (selectedArtifactId && result.csvs.some((item) => item.id === selectedArtifactId))
          ? selectedArtifactId
          : (result.csvs[0]?.id ?? 0);
        setArtifactId(targetId);
        onSelectArtifactId?.(targetId);
        setZoomRange({ start: 0, end: 100 });
      })
      .catch((reason: unknown) => {
        if (active) setListResult({ caseId, csvs: [], error: reason instanceof Error ? reason.message : String(reason) });
      });
    return () => { active = false; };
  }, [caseId, client]);

  useEffect(() => {
    if (selectedArtifactId && selectedArtifactId !== artifactId && listResult?.csvs.some((item) => item.id === selectedArtifactId)) {
      setArtifactId(selectedArtifactId);
      setZoomRange({ start: 0, end: 100 });
    }
  }, [artifactId, listResult?.csvs, selectedArtifactId]);

  useEffect(() => {
    try {
      window.sessionStorage.setItem("rby1_selected_joints_global", JSON.stringify(selectedJointNames));
    } catch {
      void 0;
    }
  }, [selectedJointNames]);

  const csvs = listResult?.caseId === caseId ? listResult.csvs : [];
  const listLoading = listResult?.caseId !== caseId;
  const listError = listResult?.caseId === caseId ? listResult.error ?? "" : "";
  const resolvedArtifactId = csvs.some((item) => item.id === artifactId) ? artifactId : csvs[0]?.id ?? 0;
  const csv = csvs.find((item) => item.id === resolvedArtifactId) ?? null;
  const available = useMemo(() => new Set(csv?.available_series.map((item) => item.name) ?? []), [csv]);
  const joints = useMemo(() => {
    if (!csv) return [];
    const detected = csv.detected_joints?.length ? csv.detected_joints : csv.available_series.map((item) => jointFromSeries(item.name)).filter((item): item is string => Boolean(item));
    return sortJoints(detected);
  }, [csv]);
  const categories = useMemo(() => CATEGORY_META.filter((item) => categoryAvailable(item.key, joints, available)), [available, joints]);

  const resolvedPlotCategories = useMemo(() => {
    const valid = new Set(categories.map((item) => item.key));
    const next = plotCategories.filter((cat) => valid.has(cat));
    return next.length > 0 ? next : [categories[0]?.key ?? "position"];
  }, [categories, plotCategories]);

  const plotCategoryEntries = useMemo(() => resolvedPlotCategories.map((item) => ({
    category: item,
    eligibleJoints: eligibleJointsFor(item, joints, available),
  })), [available, joints, resolvedPlotCategories]);

  const selectorEligibleJoints = useMemo(
    () => sortJoints([...new Set(plotCategoryEntries.flatMap((item) => item.eligibleJoints))]),
    [plotCategoryEntries],
  );
  const resolvedSelectorJoints = useMemo(() => {
    if (!selectorEligibleJoints.length) return [];
    if (selectedJointNames === null) return selectorEligibleJoints;
    return selectorEligibleJoints.filter((j) => selectedJointNames.includes(j));
  }, [selectedJointNames, selectorEligibleJoints]);

  const plots = useMemo(() => plotCategoryEntries.map((entry) => {
    const selected = entry.eligibleJoints.filter((joint) => resolvedSelectorJoints.includes(joint));
    const names = entry.category === "system"
      ? namesFor(entry.category, "", available)
      : selected.flatMap((joint) => namesFor(entry.category, joint, available));
    return { category: entry.category, selectedNames: [...new Set(names)] };
  }), [available, plotCategoryEntries, resolvedSelectorJoints]);

  const jointGroups = useMemo(() => groupJoints(selectorEligibleJoints), [selectorEligibleJoints]);

  function handleAddComparisonPlot() {
    if (resolvedPlotCategories.length >= 3) return;
    const used = new Set(resolvedPlotCategories);
    const candidate = categories.find((c) => !used.has(c.key))?.key ?? categories[0]?.key ?? "velocity";
    setPlotCategories((prev) => [...prev, candidate]);
  }

  function handleRemoveComparisonPlot(index: number) {
    setPlotCategories((prev) => prev.filter((_, i) => i !== index));
  }

  function handleChangePlotCategory(index: number, newCategory: SignalCategory) {
    setPlotCategories((prev) => prev.map((cat, i) => (i === index ? newCategory : cat)));
  }

  function toggleJoint(name: string, checked: boolean) {
    const current = resolvedSelectorJoints;
    const next = checked
      ? selectorEligibleJoints.filter((joint) => joint === name || current.includes(joint))
      : current.filter((joint) => joint !== name);
    setSelectedJointNames(next);
  }

  function setSelectedJoints(next: string[]) {
    setSelectedJointNames(next);
  }

  function handleSelectAllJoints() {
    setSelectedJointNames(selectorEligibleJoints);
  }

  function handleDeselectAllJoints() {
    setSelectedJointNames([]);
  }

  function handleIncidentClick(inc: LinkedIncident) {
    setSelectedIncidentId((prev) => (prev === inc.id ? null : inc.id));
    const targetTime = inc.csv_sample_time ?? inc.start_time;
    if (typeof targetTime === "number") {
      setCursorTime(targetTime);
    }
  }

  useEffect(() => {
    if (!caseId || !resolvedArtifactId) return;
    const cacheKey = `${caseId}:${resolvedArtifactId}`;
    if (artifactPayloads[cacheKey]) return;
    let active = true;
    setFetchingKey(cacheKey);
    setArtifactFetchError("");
    client.json<CsvChartPayload>(`/api/v3/cases/${caseId}/csvs/${resolvedArtifactId}/chart?max_points=2000&skip_dense=true`)
      .then((result) => {
        if (!active) return;
        setArtifactPayloads((prev) => ({ ...prev, [cacheKey]: result }));
        setCursorTime(result.start);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setArtifactFetchError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (active) {
          setFetchingKey((curr) => (curr === cacheKey ? null : curr));
        }
      });
    return () => {
      active = false;
      setFetchingKey((curr) => (curr === cacheKey ? null : curr));
    };
  }, [caseId, client, resolvedArtifactId, artifactPayloads]);

  const currentCacheKey = `${caseId}:${resolvedArtifactId}`;
  const payload = resolvedArtifactId ? artifactPayloads[currentCacheKey] ?? null : null;
  const chartLoading = Boolean(resolvedArtifactId && fetchingKey === currentCacheKey && !payload);
  const chartError = artifactFetchError;

  const allStateSeries = useMemo(() => {
    if (!payload) return [];
    return payload.series.filter((s) => s.name.endsWith("_state") || s.name === "control_manager_state" || s.name === "control_state");
  }, [payload]);

  const activeIncidents: LinkedIncident[] = useMemo(() => {
    const list = payload?.linked_incidents && payload.linked_incidents.length > 0
      ? payload.linked_incidents
      : (csv?.linked_incidents && csv.linked_incidents.length > 0 ? csv.linked_incidents : []);
    if (!payload) return list;
    return list.filter((inc) => {
      const t = inc.csv_sample_time ?? inc.start_time;
      return typeof t === "number" && t >= payload.start && t <= payload.end;
    });
  }, [csv?.linked_incidents, payload]);

  const baseModel = normalizeModel(csv?.robot_model);
  const activeModel: RobotModelDescriptor = baseModel;

  const allPositionSeries = useMemo(() => {
    if (!payload) return [];
    return joints
      .map((joint) => payload.series.find((s) => s.name === `${joint}_pos`))
      .filter((item): item is CsvSeries => Boolean(item));
  }, [payload, joints]);

  // 사전 보간 궤적 버퍼 (Precomputed Trajectory Buffer - 30 FPS 초경량화, 60초 기준 ~140KB)
  const precomputedTrajectory = useMemo(() => {
    if (!payload || !allPositionSeries.length) return null;
    const start = payload.start;
    const end = payload.end;
    const duration = Math.max(0, end - start);
    if (duration <= 0) return null;

    const FPS = 30; // 30 FPS (33.3ms) 샘플링으로 메모리를 0.1~0.2MB 수준으로 최소화
    const totalFrames = Math.ceil(duration * FPS) + 1;
    const step = 1 / FPS;
    const frames: Record<string, number>[] = new Array(totalFrames);

    for (let f = 0; f < totalFrames; f++) {
      const t = start + f * step;
      const poseMap: Record<string, number> = {};
      for (const item of allPositionSeries) {
        const jointName = item.name.slice(0, -"_pos".length);
        poseMap[jointName] = interpolate(item.points, t);
      }
      frames[f] = poseMap;
    }

    return {
      start,
      end,
      step,
      FPS,
      frames,
    };
  }, [payload, allPositionSeries]);

  const pose = useMemo(() => {
    if (!precomputedTrajectory) {
      return Object.fromEntries(allPositionSeries.map((item) => [
        item.name.slice(0, -"_pos".length),
        interpolate(item.points, cursorTime),
      ]));
    }
    const idx = Math.min(
      Math.max(0, Math.round((cursorTime - precomputedTrajectory.start) / precomputedTrajectory.step)),
      precomputedTrajectory.frames.length - 1
    );
    return precomputedTrajectory.frames[idx] ?? {};
  }, [allPositionSeries, cursorTime, precomputedTrajectory]);

  const playbackAvailable = Boolean(payload && allPositionSeries.length > 0);
  const start = payload?.start ?? csv?.min_sample_time ?? 0;
  const end = payload?.end ?? csv?.max_sample_time ?? start;
  const cursorLabel = `${formatDuration(cursorTime - start)} / ${formatDuration(end - start)}`;

  useEffect(() => {
    if (!playing || !payload || !playbackAvailable) return;
    let frame = 0;
    let previous = performance.now();
    const tick = (now: number) => {
      const dt = (now - previous) / 1000;
      previous = now;
      const elapsed = dt * speed;
      const next = cursorRef.current + elapsed;
      if (next >= payload.end) {
        setCursorTime(payload.end);
        setPlaying(false);
        return;
      }
      setCursorTime(next);
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [payload, playbackAvailable, playing, speed]);

  function startPlayback() {
    if (!payload || !playbackAvailable) return;
    if (cursorRef.current >= payload.end) setCursorTime(payload.start);
    setPlaying(true);
  }

  if (listLoading) return <section className="csvWorkspace"><div className="csvEmpty">CSV 목록을 불러오는 중입니다.</div></section>;
  if (listError) return <section className="csvWorkspace"><div className="csvEmpty errorText">{listError}</div></section>;
  if (!csvs.length) return <section className="csvWorkspace"><div className="csvEmpty"><strong>분석할 Fault CSV가 없습니다.</strong><span>상단의 파일 가져오기 또는 드래그앤드롭으로 CSV를 추가하십시오.</span></div></section>;

  return <section className="csvWorkspace" aria-label="Fault CSV 신호 분석 & 3D 시각화">
    <header className="csvHeader">
      <div className="csvHeaderLeft">
        <h2>CSV 신호 분석 & 3D 시각화</h2>
        <p>사건 발생 여부와 관계없이 Fault CSV의 전체 시간 구간 및 3D 동작을 검토합니다.</p>
      </div>
      <div className="csvHeaderControls">
        <label className="csvFileSelectLabel">
          <span>CSV 파일</span>
          <select aria-label="분석할 CSV 파일" value={resolvedArtifactId} onChange={(event) => {
            const nextId = Number(event.target.value);
            setArtifactId(nextId);
            onSelectArtifactId?.(nextId);
            setZoomRange({ start: 0, end: 100 });
            setPlaying(false);
          }}>
            {csvs.map((item) => <option value={item.id} key={item.id}>{item.member || item.name}</option>)}
          </select>
        </label>
        <div className="headerStatBadgeGroup">
          <div className="headerStatBadge"><span>샘플</span><strong>{(csv?.sample_count ?? 0).toLocaleString()}</strong></div>
          <div className="headerStatBadge"><span>기록 구간</span><strong>{formatDuration((csv?.max_sample_time ?? 0) - (csv?.min_sample_time ?? 0))}</strong></div>
        </div>
      </div>
    </header>

    {selectorEligibleJoints.length > 0 && (
      <section className="jointSelector" aria-labelledby="joint-selector-title">
        <div className="jointSelectorHead">
          <div className="jointSelectorTitleRow">
            <h3 id="joint-selector-title">조인트 선택</h3>
            <span className="jointCountBadge">{resolvedSelectorJoints.length} / {selectorEligibleJoints.length}개 선택</span>
          </div>
          <div className="jointQuickActions">
            <button
              type="button"
              className="btnMiniAction"
              onClick={handleSelectAllJoints}
              title="모든 관절 선택"
            >
              전체 선택
            </button>
            <button
              type="button"
              className="btnMiniAction"
              onClick={handleDeselectAllJoints}
              title="모든 관절 선택 해제"
            >
              전체 해제
            </button>
          </div>
        </div>
        <div className="jointGroupActions" role="group" aria-label="CSV 조인트 그룹 선택">
          {jointGroups.map((group) => {
            const groupJointsList = group.joints;
            const isAllGroupSelected = groupJointsList.length > 0 && groupJointsList.every((j) => resolvedSelectorJoints.includes(j));
            const isSomeGroupSelected = !isAllGroupSelected && groupJointsList.some((j) => resolvedSelectorJoints.includes(j));
            return (
              <button
                type="button"
                className={`textButton jointGroupBtn${isAllGroupSelected ? " active" : isSomeGroupSelected ? " partial" : ""}`}
                aria-pressed={isAllGroupSelected}
                aria-label={`CSV ${group.label} 그룹 선택 전환`}
                onClick={() => {
                  const groupSet = new Set(groupJointsList);
                  if (isAllGroupSelected) {
                    setSelectedJointNames(resolvedSelectorJoints.filter((j) => !groupSet.has(j)));
                  } else {
                    setSelectedJointNames(selectorEligibleJoints.filter((j) => groupSet.has(j) || resolvedSelectorJoints.includes(j)));
                  }
                }}
                key={group.key}
              >
                {group.label}
                <span className="groupCount">
                  ({groupJointsList.filter((j) => resolvedSelectorJoints.includes(j)).length}/{groupJointsList.length})
                </span>
              </button>
            );
          })}
        </div>
        <details className="jointDetailAccordion">
          <summary><span>상세 조인트 개별 선택 (접기 / 펼치기)</span></summary>
          <div className="jointGroupList">
            {jointGroups.map((group) => (
              <section className={`jointGroupBlock jointGroup-${group.key}`} aria-label={`${group.label} 조인트`} key={group.key}>
                <h4>{group.label}<span>{group.joints.filter((j) => resolvedSelectorJoints.includes(j)).length} / {group.joints.length}</span></h4>
                <div className="jointChecklist">
                  {group.joints.map((item) => (
                    <label key={item}>
                      <input
                        type="checkbox"
                        checked={resolvedSelectorJoints.includes(item)}
                        onChange={(event) => toggleJoint(item, event.currentTarget.checked)}
                      />
                      <span>{item}</span>
                    </label>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </details>
      </section>
    )}

    {activeIncidents.length > 0 ? (
      <div className="csvIncidentListBanner">
        <span className="csvIncidentBadge">⚠️ 이 CSV 구간 감지 에러 ({activeIncidents.length}건 · 클릭하여 위치 표시)</span>
        <div className="csvIncidentTags">
          {activeIncidents.map((inc) => (
            <button
              type="button"
              key={inc.id}
              className={`incidentTag ${selectedIncidentId === inc.id ? "selected" : ""}`}
              title={`${inc.summary || inc.title} (클릭하여 오류 지점 표시)`}
              onClick={() => handleIncidentClick(inc)}
            >
              <span className="timeIndexBadge">
                <span className="csvTimeTag">CSV {inc.csv_time_display ?? `${(inc.csv_sample_time ?? 0).toFixed(3)}s`}</span>
                <span className="logTimeTag">Log {inc.log_time_display || inc.start_raw}</span>
              </span>
              <span className="incidentDesc">
                {inc.fault_level === "major" ? "[Major]" : inc.fault_level === "minor" ? "[Minor]" : `[${inc.severity}]`} {inc.title}
              </span>
            </button>
          ))}
        </div>
      </div>
    ) : (
      <div className="csvIncidentListBanner cleanBanner">
        <span>✓ 이 CSV 구간에서는 기록된 에러 사건이 없습니다. (정상 동작 구간)</span>
      </div>
    )}

    {/* Top Action Bar: 3D Toggle & Comparison Plot Addition */}
    <div className="csvWorkspaceTopBar">
      <div className="topBarLeft">
        <button
          type="button"
          className={`btnTopBarAction btnToggle3D ${show3DView ? "active" : ""}`}
          onClick={() => setShow3DView((prev) => !prev)}
          title="3D 로봇 자세 시뮬레이터 및 재생 컨트롤을 열고 닫습니다"
        >
          <span className="btn3DIcon">🤖</span>
          <span className="btn3DText">3D 시각화 {show3DView ? "ON" : "OFF"}</span>
        </button>

        <button
          type="button"
          className="btnTopBarAction btnAddComparison"
          disabled={resolvedPlotCategories.length >= 3}
          onClick={handleAddComparisonPlot}
          title={resolvedPlotCategories.length >= 3 ? "비교 Plot은 최대 2개(총 3개)까지만 추가 가능합니다" : "비교 Plot을 추가합니다 (최대 2개)"}
        >
          <span className="btnAddIcon">➕</span>
          <span>비교 Plot 추가</span>
          <span className="plotCountBadge">({resolvedPlotCategories.length - 1}/2)</span>
        </button>
      </div>
      <div className="topBarRight">
        <span className="topBarHint">💡 각 Plot 좌측에서 신호를 클릭하여 원하는 그래프로 개별 변경할 수 있습니다.</span>
      </div>
    </div>

    {/* Unified Timeline Zoom Control Toolbar with Dual-Handle Brush & Direct Numeric Time Inputs */}
    <UnifiedTimelineBrushBar
      durationSec={csv ? Math.max(0.001, (csv.max_sample_time ?? 5) - (csv.min_sample_time ?? 0)) : (payload ? Math.max(0.001, payload.end - payload.start) : 5.0)}
      zoomRange={zoomRange}
      onZoomChange={setZoomRange}
    />

    {/* Workspace 2-Column Area: [Plots List (scrollable)] | [Resizer] | [3D Simulation] */}
    <div
      className={`csvWorkspace2Col ${show3DView ? "hasSim3D" : "noSim3D"}`}
      style={show3DView ? { gridTemplateColumns: `minmax(0, 1fr) 8px ${simWidth}px` } : undefined}
    >
      <main className="csvColPlots" aria-label="CSV 신호 그래프 및 분석">
        <div className={`csvPlotsGrid ${plots.length > 1 ? "splitRows" : "singleRow"}`}>
          {plots.map((plot, index) => (
            <CsvPlot
              key={`${index}:${plot.category}`}
              category={plot.category}
              onCategoryChange={(newCat) => handleChangePlotCategory(index, newCat)}
              availableCategories={categories}
              selectedNames={plot.selectedNames}
              payload={payload}
              loading={chartLoading}
              error={chartError}
              kind={index === 0 ? "primary" : "comparison"}
              comparisonIndex={index}
              onRemove={index > 0 ? () => handleRemoveComparisonPlot(index) : undefined}
              zoomRange={zoomRange}
              onZoomRangeChange={setZoomRange}
              incidentMarks={activeIncidents}
              selectedIncidentId={selectedIncidentId}
              cursorTime={show3DView ? cursorTime : undefined}
              onCursorChange={setCursorTime}
            />
          ))}
        </div>
      </main>

      {show3DView && (
        <div
          className={`splitResizer ${isDraggingResizer ? "dragging" : ""}`}
          onMouseDown={startResizerDrag}
          title="드래그하여 그래프와 3D 시뮬레이션 영역 너비를 조절합니다"
          role="separator"
          aria-orientation="vertical"
        >
          <div className="resizerGrip" />
        </div>
      )}

      {show3DView && (
        <aside className="csvColSimulation" aria-label="3D 로봇 자세 시뮬레이션">
          <div className="simulationDockSticky">
            <section className="simulationDock">
              <div className="simControlBar">
                <div className="playbackControls" aria-label="자세 재생 제어">
                  <button type="button" className="textButton" disabled={!playbackAvailable} onClick={() => { setPlaying(false); setCursorTime(start); }}>처음</button>
                  <button type="button" className={`textButton playbackPrimary ${playing ? "playing" : ""}`} disabled={!playbackAvailable} onClick={() => {
                    if (playing) setPlaying(false);
                    else startPlayback();
                  }}>{playing ? "일시정지" : "▶ 재생"}</button>
                  <button type="button" className="textButton" disabled={!playing} onClick={() => setPlaying(false)}>정지</button>
                  <label className="speedSelectLabel">
                    <span>속도</span>
                    <select aria-label="재생 속도" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
                      <option value={0.2}>0.2x</option>
                      <option value={0.5}>0.5x</option>
                      <option value={0.7}>0.7x</option>
                      <option value={1.0}>1.0x</option>
                      <option value={1.2}>1.2x</option>
                      <option value={1.5}>1.5x</option>
                      <option value={2.0}>2.0x</option>
                    </select>
                  </label>
                </div>
                <div className="playbackTime">
                  <span>재생 시간</span>
                  <strong>{cursorLabel}</strong>
                </div>
              </div>

              <div className="simViewerContainer">
                <RobotViewer
                  model={activeModel}
                  jointValues={pose}
                  cursorLabel={cursorLabel}
                />
              </div>
            </section>
          </div>
        </aside>
      )}
    </div>
  </section>;
}
